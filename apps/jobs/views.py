import uuid
from collections.abc import Mapping
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.accounts.models import record_security_event
from apps.jobs.models import Job, JobStatus, ArchivedJob
from apps.jobs.serializers import JobSerializer, ArchivedJobSerializer
from apps.applications.models import Application
from apps.applications.serializers import (
    ApplicantApplicationSerializer,
    RecruiterApplicationSerializer,
    ApplicationCreateSerializer,
)
from apps.resumes.models import ResumeWork
from apps.resumes.tasks import enqueue_resume_processing
from apps.resumes.upload_limits import ResumePayloadTooLarge, ResumeUploadLimit
from apps.jobs.services import generate_job_requisition_with_gemini, lint_job_requisition
from apps.notifications.services import create_new_application_notification
from config.authentication import account_email_is_confirmed
from config.permissions import (
    IsCompanyRecruiter, is_recruiter,
    recruiter_company_id, scope_recruiter_records,
)

from django.db import transaction, IntegrityError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.db.models import Count

class JobViewSet(viewsets.ModelViewSet):
    queryset = Job.objects.select_related('company').annotate(applicant_count=Count('applications')).all()
    serializer_class = JobSerializer
    permission_classes = [IsCompanyRecruiter]
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'public_stats']:
            return [AllowAny()]
        if self.action == 'apply':
            return [IsAuthenticated()]
        return [IsCompanyRecruiter()]

    def get_queryset(self):
        user = getattr(self.request, 'user', None)
        privileged = is_recruiter(user)

        queryset = Job.objects.select_related('company').annotate(applicant_count=Count('applications')).all()
        if not privileged or self.action == 'apply':
            # Anonymous users and applicants can ONLY view published jobs;
            # the apply action also queries closed jobs to return a specific 400 error.
            if self.action == 'apply':
                return queryset.filter(status__in=[JobStatus.PUBLISHED, JobStatus.CLOSED])
            return queryset.filter(status=JobStatus.PUBLISHED)

        queryset = scope_recruiter_records(queryset, user, 'company_id')
        if self.action != 'archive':
            queryset = queryset.exclude(status=JobStatus.ARCHIVED)
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

    def perform_create(self, serializer):
        serializer.save(company_id=recruiter_company_id(self.request.user))

    def _lock_active_job(self):
        visible = self.get_object()
        job = get_object_or_404(Job.objects.select_for_update(), pk=visible.pk)
        if job.status == JobStatus.ARCHIVED:
            raise NotFound()
        return job

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        job = self._lock_active_job()
        serializer = self.get_serializer(job, data=request.data, partial=kwargs.get('partial', False))
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    def perform_update(self, serializer):
        if 'company' in serializer.validated_data:
            company = serializer.validated_data['company']
            if (company.pk if company else None) != serializer.instance.company_id:
                raise PermissionDenied("Job ownership cannot be changed through this endpoint.")
        serializer.save()

    @action(detail=False, methods=['get'], url_path='public-stats', permission_classes=[AllowAny])
    def public_stats(self, request):
        """
        Public aggregate metric endpoint providing anonymous candidate and job counts
        with zero PII or application details exposed.
        """
        open_jobs = Job.objects.filter(status=JobStatus.PUBLISHED).count()
        screened_candidates = Application.objects.count()
        return Response({
            "open_benchmarks": open_jobs,
            "screened_candidates": screened_candidates
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='close', permission_classes=[IsCompanyRecruiter])
    @transaction.atomic
    def close(self, request, pk=None):
        job = self._lock_active_job()
        job.status = JobStatus.CLOSED
        job.save(update_fields=['status', 'updated_at'])
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reopen', permission_classes=[IsCompanyRecruiter])
    @transaction.atomic
    def reopen(self, request, pk=None):
        job = self._lock_active_job()
        job.status = JobStatus.PUBLISHED
        job.save(update_fields=['status', 'updated_at'])
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='archive', permission_classes=[IsCompanyRecruiter])
    def archive(self, request, pk=None):
        visible_job = self.get_object()
        with transaction.atomic():
            job = get_object_or_404(Job.objects.select_for_update(), pk=visible_job.pk)
            archived_job = ArchivedJob.objects.filter(active_job=job).first()
            if archived_job is None:
                archived_job = ArchivedJob.objects.create(
                    active_job=job,
                    company=job.company,
                    title=job.title,
                    department=job.department,
                    description=job.description,
                    employment_type=job.employment_type,
                    location=job.location,
                    minimum_experience=job.minimum_experience,
                    education_requirement=job.education_requirement,
                    required_skills=job.required_skills,
                    preferred_skills=job.preferred_skills,
                    rubric_weights=job.rubric_weights,
                    original_created_at=job.created_at,
                )
                job.status = JobStatus.ARCHIVED
                job.save(update_fields=['status', 'updated_at'])
                record_security_event(request.user, 'job.archive', job.pk)

        serializer = ArchivedJobSerializer(archived_job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='apply', permission_classes=[IsAuthenticated])
    def apply(self, request, pk=None):
        job = self.get_object()
        if job.status == JobStatus.CLOSED:
            return Response(
                {'error': 'This job is closed and is no longer accepting applications.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if job.status != JobStatus.PUBLISHED:
            return Response(
                {'error': 'This job is not currently open for applications.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Resolve the authenticated user's Supabase UUID strictly from validated identity or UserRole.
        # Never generate a synthetic fallback UUID; fail closed if identity is missing or malformed.
        user_uuid = getattr(request.user, 'supabase_id', None)
        if not user_uuid and hasattr(request.user, 'role_profile') and request.user.role_profile:
            if request.user.role_profile.supabase_user_id:
                user_uuid = str(request.user.role_profile.supabase_user_id)
        if not user_uuid and getattr(request.user, 'username', None):
            user_uuid = str(request.user.username)

        if not user_uuid:
            return Response(
                {'detail': 'Authenticated user is missing a valid Supabase UUID identity.'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            valid_user_uuid = str(uuid.UUID(str(user_uuid)))
        except (ValueError, TypeError, AttributeError):
            return Response(
                {'detail': 'Authenticated user does not possess a valid Supabase UUID identity.'},
                status=status.HTTP_403_FORBIDDEN
            )

        account_email = getattr(request.user, 'supabase_email', request.user.email).strip().lower()
        if not account_email or not account_email_is_confirmed(valid_user_uuid, account_email):
            raise PermissionDenied('Sign in with a verified email address to apply.')
        try:
            content_length = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            raise ValidationError('Invalid application request length.') from None
        if content_length > settings.MAX_RESUME_UPLOAD_SIZE + 256 * 1024:
            raise ResumePayloadTooLarge()
        upload_limit = ResumeUploadLimit(request)
        request.upload_handlers.insert(0, upload_limit)
        submitted = request.data
        if upload_limit.exceeded:
            raise ResumePayloadTooLarge()
        if not isinstance(submitted, Mapping):
            raise ValidationError('Submit application details as form data.')
        submitted_email = submitted.get('email', account_email)
        if not isinstance(submitted_email, str) or submitted_email.strip().lower() != account_email:
            return Response({'email': ['Use the email address on your signed-in account.']}, status=400)

        # Protect against mass assignment of ownership, status, recruiter notes, tracking tokens, or internal fields
        protected_fields = {
            'user_id', 'supabase_user_id', 'status', 'processing_status',
            'notes', 'tracking_token', 'applied_at', 'updated_at', 'candidate_code'
        }
        data = {k: v for k, v in submitted.items() if k not in protected_fields}
        data['job'] = job.id
        data['user_id'] = valid_user_uuid
        data['email'] = account_email

        serializer = ApplicationCreateSerializer(data=data)
        if serializer.is_valid():
            application = None
            try:
                with transaction.atomic():
                    locked_job = Job.objects.select_for_update().filter(pk=job.pk).first()
                    if locked_job is None or locked_job.status != JobStatus.PUBLISHED:
                        return Response({'detail': 'This job is no longer accepting applications.'},
                                        status=status.HTTP_409_CONFLICT)
                    application = serializer.save()
                    enqueue_resume_processing(application.pk)
                    create_new_application_notification(application)
            except Exception as exc:
                if application is not None and application.resume.file:
                    ResumeWork.objects.get_or_create(
                        kind=ResumeWork.Kind.DELETE, target=application.resume.file.name,
                    )
                if isinstance(exc, IntegrityError) and Application.objects.filter(job=job, user_id=valid_user_uuid).exists():
                    return Response(
                        {'detail': 'You have already applied for this position.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                raise

            # Return applicant-safe application details (excluding AI scores and presigned URLs)
            safe_serializer = ApplicantApplicationSerializer(application, context={'request': request})
            return Response(safe_serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='applicants', permission_classes=[IsCompanyRecruiter])
    def applicants(self, request, pk=None):
        job = self.get_object()
        applications = job.applications.select_related('job', 'resume', 'match_result').all()
        serializer = RecruiterApplicationSerializer(applications, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='report')
    def export_report(self, request, pk=None):
        from apps.jobs.report_data import ReportRequestSerializer, build_pipeline_report
        from apps.jobs.report_exports import (
            EXCEL_MIME, PDF_MIME, render_pipeline_excel, render_pipeline_pdf, report_filename,
        )

        job = self.get_object()
        options = ReportRequestSerializer(data=request.data)
        options.is_valid(raise_exception=True)
        file_type = options.validated_data['file_type']
        report = build_pipeline_report(
            job, options.validated_data.get('date_from'), options.validated_data.get('date_to'),
        )
        if file_type == 'pdf':
            payload, mime_type = render_pipeline_pdf(report), PDF_MIME
        else:
            payload, mime_type = render_pipeline_excel(report), EXCEL_MIME

        filename = report_filename(report, file_type)
        record_security_event(request.user, 'job.report_export', job.pk, file_type=file_type,
                              application_count=len(report.applications))
        return HttpResponse(payload, content_type=mime_type, headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'private, no-store',
        })

    @action(detail=False, methods=['post'], url_path='ai-draft', permission_classes=[IsCompanyRecruiter])
    def ai_draft(self, request):
        """
        Auto-drafts a structured, bias-free job requisition from a role title & department.
        Returns: title, department, description, minimum_experience, education_requirement,
                 required_skills, preferred_skills, suggested_skills, job_family, weights, is_regulated.
        """
        title = request.data.get('title', '').strip()
        department = request.data.get('department', '').strip()
        if not title:
            return Response({'error': 'Job Title is required to auto-draft requisition.'}, status=status.HTTP_400_BAD_REQUEST)

        draft = generate_job_requisition_with_gemini(title, department)
        return Response(draft, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='lint-jd', permission_classes=[IsCompanyRecruiter])
    def lint_jd(self, request):
        """
        Performs Section 13.11 real-time pre-flight bias and regulated licensure audit.
        """
        title = request.data.get('title', '')
        description = request.data.get('description', '')
        required_skills = request.data.get('required_skills', [])
        education = request.data.get('education_requirement', '')

        lint_result = lint_job_requisition(
            title=title,
            description=description,
            required_skills=required_skills,
            education=education
        )
        return Response(lint_result, status=status.HTTP_200_OK)


class ArchivedJobViewSet(viewsets.ModelViewSet):
    queryset = ArchivedJob.objects.select_related('company').all()
    serializer_class = ArchivedJobSerializer
    permission_classes = [IsCompanyRecruiter]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        return scope_recruiter_records(super().get_queryset(), self.request.user, 'company_id')

    def create(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=['post'], url_path='restore', permission_classes=[IsCompanyRecruiter])
    @transaction.atomic
    def restore(self, request, pk=None):
        visible = self.get_object()
        job = None
        if visible.active_job_id:
            job = get_object_or_404(Job.objects.select_for_update(), pk=visible.active_job_id)
        archived_job = get_object_or_404(ArchivedJob.objects.select_for_update(), pk=visible.pk)
        if job is not None:
            job.status = JobStatus.PUBLISHED
            job.save(update_fields=['status', 'updated_at'])
        else:
            job = Job.objects.create(
                company=archived_job.company,
                title=archived_job.title,
                department=archived_job.department,
                description=archived_job.description,
                employment_type=archived_job.employment_type,
                location=archived_job.location,
                minimum_experience=archived_job.minimum_experience,
                education_requirement=archived_job.education_requirement,
                required_skills=archived_job.required_skills,
                preferred_skills=archived_job.preferred_skills,
                rubric_weights=archived_job.rubric_weights,
                status=JobStatus.PUBLISHED,
            )
        record_security_event(request.user, 'job.restore', job.pk)
        archived_job.delete()
        return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)
