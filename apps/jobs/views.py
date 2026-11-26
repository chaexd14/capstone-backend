import uuid
import hashlib
import json
from collections.abc import Mapping
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.accounts.models import record_security_event
from apps.jobs.models import Job, JobStatus, ArchivedJob
from apps.jobs.serializers import (
    JobSerializer, JobListSerializer, ArchivedJobSerializer, ArchivedJobListSerializer,
)
from apps.applications.models import Application
from apps.applications.serializers import (
    ApplicantApplicationSerializer,
    RecruiterApplicationListSerializer,
    ApplicationCreateSerializer,
)
from apps.applications.views import (
    annotate_recruiter_applications,
    defer_heavy_resume_fields,
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
from django.utils.cache import patch_vary_headers
from django.shortcuts import get_object_or_404
from django.db.models import Avg, Case, Count, IntegerField, Max, Q, Value, When, BooleanField
from config.pagination import StandardResultsSetPagination

class JobViewSet(viewsets.ModelViewSet):
    queryset = Job.objects.select_related('company').annotate(applicant_count=Count('applications')).all()
    serializer_class = JobSerializer
    permission_classes = [IsCompanyRecruiter]
    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']
    pagination_class = StandardResultsSetPagination

    def _apply_public_cache_headers(self, request, response):
        if getattr(request.user, 'is_authenticated', False) or response.status_code != 200:
            return response
        serialized = json.dumps(response.data, sort_keys=True, separators=(',', ':')).encode()
        etag = f'"{hashlib.sha256(serialized).hexdigest()}"'
        if request.headers.get('If-None-Match') == etag:
            response = Response(status=status.HTTP_304_NOT_MODIFIED)
        response['ETag'] = etag
        response['Cache-Control'] = 'public, max-age=30, s-maxage=30, stale-while-revalidate=60'
        response['X-Cache'] = 'revalidated' if response.status_code == 304 else 'miss'
        patch_vary_headers(response, ('Authorization', 'Accept-Encoding'))
        return response

    def get_serializer_class(self):
        return JobListSerializer if self.action == 'list' else JobSerializer

    def _apply_collection_filters(self, queryset, *, include_status=True):
        status_param = self.request.query_params.get('status')
        if include_status and status_param:
            queryset = queryset.filter(status=status_param)
        department = (self.request.query_params.get('department') or '').strip()
        if department:
            queryset = queryset.filter(department__iexact=department)
        query = (self.request.query_params.get('q') or '').strip()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) | Q(department__icontains=query) |
                Q(location__icontains=query) | Q(employment_type__icontains=query)
            )
        return queryset

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'public_stats']:
            return [AllowAny()]
        if self.action == 'apply':
            return [IsAuthenticated()]
        return [IsCompanyRecruiter()]

    def get_queryset(self):
        user = getattr(self.request, 'user', None)
        privileged = is_recruiter(user)

        queryset = Job.objects.select_related('company').all()
        if privileged and self.action != 'apply':
            queryset = queryset.annotate(
                applicant_count=Count('applications', distinct=True),
                top_match_score=Max('applications__match_result__match_score'),
                average_match_score=Avg('applications__match_result__match_score'),
            )
        if not privileged or self.action == 'apply':
            queryset = queryset.annotate(applicant_count=Value(0, output_field=IntegerField()))
            # Anonymous users and applicants can ONLY view published jobs;
            # the apply action also queries closed jobs to return a specific 400 error.
            if self.action == 'apply':
                return queryset.filter(status__in=[JobStatus.PUBLISHED, JobStatus.CLOSED])
            return queryset.filter(status=JobStatus.PUBLISHED)

        queryset = scope_recruiter_records(queryset, user, 'company_id')
        if self.action != 'archive':
            queryset = queryset.exclude(status=JobStatus.ARCHIVED)
        queryset = self._apply_collection_filters(queryset)
        ordering = self.request.query_params.get('ordering') or '-created_at'
        allowed_ordering = {'created_at', '-created_at', 'updated_at', '-updated_at', 'title', '-title'}
        return queryset.order_by(ordering if ordering in allowed_ordering else '-created_at', '-id')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        user = getattr(request, 'user', None)
        if is_recruiter(user):
            facet_queryset = scope_recruiter_records(
                Job.objects.exclude(status=JobStatus.ARCHIVED), user, 'company_id'
            )
            facet_queryset = self._apply_collection_filters(facet_queryset, include_status=False)
        else:
            facet_queryset = queryset
        status_counts = {
            row['status']: row['count']
            for row in facet_queryset.order_by().values('status').annotate(count=Count('id', distinct=True))
        }
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        response = self.paginator.get_paginated_response(
            serializer.data, facets={'status_counts': status_counts}
        )
        return self._apply_public_cache_headers(request, response)

    def retrieve(self, request, *args, **kwargs):
        return self._apply_public_cache_headers(request, super().retrieve(request, *args, **kwargs))

    def perform_create(self, serializer):
        job = serializer.save(company_id=recruiter_company_id(self.request.user))
        source_job_id = self.request.data.get('source_job_id')
        if source_job_id:
            record_security_event(self.request.user, 'job.duplicate', job.pk, source_job_id=source_job_id)
        else:
            record_security_event(self.request.user, 'job.create', job.pk)

    def _lock_active_job(self):
        lookup_value = self.kwargs.get(self.lookup_field or 'pk')
        base = scope_recruiter_records(
            Job.objects.select_related('company'),
            self.request.user,
            'company_id',
        )
        job = get_object_or_404(base.select_for_update(of=('self',)), pk=lookup_value)
        if job.status == JobStatus.ARCHIVED:
            raise NotFound()
        self.check_object_permissions(self.request, job)
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

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        job = self._lock_active_job()
        if job.status != JobStatus.DRAFT:
            raise ValidationError('Only draft jobs can be permanently deleted.')
        if job.applications.exists():
            raise ValidationError('A draft with applications cannot be permanently deleted.')
        job_id = job.pk
        job_title = job.title
        job.delete()
        record_security_event(request.user, 'job.draft.delete', job_id, title=job_title)
        return Response(status=status.HTTP_204_NO_CONTENT)

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
        publish_errors = {}
        if job.salary_min is None or job.salary_max is None:
            publish_errors.update({
                'salary_min': 'Add a monthly salary before reopening this job.',
                'salary_max': 'Add a monthly salary before reopening this job.',
            })
        if not job.job_overview or len(job.job_overview.strip()) < 40:
            publish_errors['job_overview'] = 'Add a job overview of at least 40 characters before reopening this job.'
        if publish_errors:
            raise ValidationError(publish_errors)
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
                    job_overview=job.job_overview,
                    employment_type=job.employment_type,
                    location=job.location,
                    minimum_experience=job.minimum_experience,
                    education_requirement=job.education_requirement,
                    required_skills=job.required_skills,
                    preferred_skills=job.preferred_skills,
                    salary_min=job.salary_min,
                    salary_max=job.salary_max,
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
        applications = defer_heavy_resume_fields(
            annotate_recruiter_applications(
                job.applications.select_related('job', 'resume', 'match_result').order_by('-applied_at', '-id')
            )
        )
        page = self.paginate_queryset(applications)
        serializer = RecruiterApplicationListSerializer(page, many=True, context={'request': request})
        return self.paginator.get_paginated_response(serializer.data)

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
            job,
            options.validated_data.get('date_from'),
            options.validated_data.get('date_to'),
            options.validated_data.get('status') if options.validated_data['scope'] == 'status' else None,
            options.validated_data.get('application_ids') if options.validated_data['scope'] == 'selected' else None,
        )
        if file_type == 'pdf':
            payload, mime_type = render_pipeline_pdf(report), PDF_MIME
        else:
            payload, mime_type = render_pipeline_excel(report), EXCEL_MIME

        filename = report_filename(report, file_type)
        record_security_event(
            request.user,
            'job.report_export',
            job.pk,
            file_type=file_type,
            scope=options.validated_data['scope'],
            status=options.validated_data.get('status'),
            application_count=len(report.applications),
        )
        return HttpResponse(payload, content_type=mime_type, headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'private, no-store',
        })

    @action(detail=True, methods=['post'], url_path='candidates-report')
    def export_candidates_report(self, request, pk=None):
        from apps.jobs.report_data import (
            JobCandidatesReportRequestSerializer, build_job_candidates_report,
        )
        from apps.jobs.report_exports import (
            EXCEL_MIME, PDF_MIME, job_candidates_report_filename,
            render_job_candidates_excel, render_job_candidates_pdf,
        )

        job = self.get_object()
        options = JobCandidatesReportRequestSerializer(data=request.data)
        options.is_valid(raise_exception=True)
        file_type = options.validated_data['file_type']
        report_view = options.validated_data.get('report_view', 'ranked')
        report = build_job_candidates_report(
            job,
            report_view=report_view,
            date_from=options.validated_data.get('date_from'),
            date_to=options.validated_data.get('date_to'),
            status=options.validated_data.get('status'),
            match_band=options.validated_data.get('match_band', 'ALL'),
        )
        if file_type == 'pdf':
            payload, mime_type = render_job_candidates_pdf(report), PDF_MIME
        else:
            payload, mime_type = render_job_candidates_excel(report), EXCEL_MIME

        filename = job_candidates_report_filename(report, file_type)
        record_security_event(
            request.user,
            'job.candidates_report_export',
            job.pk,
            file_type=file_type,
            report_view=report_view,
            status=options.validated_data.get('status'),
            match_band=options.validated_data.get('match_band', 'ALL'),
            application_count=len(report.rows),
        )
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
        job_overview = request.data.get('job_overview', '')
        required_skills = request.data.get('required_skills', [])
        education = request.data.get('education_requirement', '')

        lint_result = lint_job_requisition(
            title=title,
            description=description,
            required_skills=required_skills,
            education=education,
            job_overview=job_overview,
        )
        return Response(lint_result, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reprocess-all', permission_classes=[IsCompanyRecruiter])
    def reprocess_all(self, request, pk=None):
        """Re-enqueues AI scoring for all non-purged applications on this job after rubric or skills updates."""
        job = self.get_object()
        apps = job.applications.select_related('resume').all()
        queued = 0
        for app in apps:
            resume = getattr(app, 'resume', None)
            if resume and resume.file:
                enqueue_resume_processing(app.pk, retry_failed=True)
                queued += 1
        record_security_event(request.user, 'job.reprocess_all', job.pk, queued=queued)
        return Response({'queued': queued}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'], url_path='fairness-audit', permission_classes=[IsCompanyRecruiter])
    def fairness_audit(self, request, pk=None):
        """
        Aggregates explainable fairness and screening compliance metrics for a job requisition:
        - JD bias lint status
        - Score-band vs shortlist alignment
        - Four-Fifths adverse impact check across experience-tenure cohorts (proxy fairness check)
        - Redaction coverage across evaluated resumes
        """
        from apps.resumes.audit_service import adverse_impact_ratio
        from apps.applications.models import ApplicationStatus

        job = self.get_object()
        apps = list(
            job.applications.select_related('resume', 'match_result')
            .only(
                'id',
                'status',
                'resume__extracted_experience_years',
                'match_result__match_score',
            )
            .annotate(
                _has_redaction=Case(
                    When(
                        Q(resume__redacted_text__gt='') | ~Q(resume__redacted_data={}),
                        then=Value(True),
                    ),
                    default=Value(False),
                    output_field=BooleanField(),
                )
            )
        )
        lint_result = lint_job_requisition(
            title=job.title,
            description=job.description,
            required_skills=job.required_skills or [],
            education=job.education_requirement or '',
            job_overview=job.job_overview or '',
        )

        selected_statuses = {ApplicationStatus.SHORTLISTED, ApplicationStatus.INTERVIEW, ApplicationStatus.HIRED}
        band_alignment = {
            band: {'total': 0, 'shortlisted': 0}
            for band in ('Strong', 'Good', 'Partial', 'Weak')
        }
        selected_by_cohort = {}
        total_by_cohort = {}
        redacted_count = 0
        evaluated_count = 0

        for app in apps:
            resume = getattr(app, 'resume', None)
            match = getattr(app, 'match_result', None)
            if getattr(app, '_has_redaction', False):
                redacted_count += 1
            if match is not None:
                evaluated_count += 1
                score = match.match_score or 0
                band = 'Strong' if score >= 85 else 'Good' if score >= 70 else 'Partial' if score >= 50 else 'Weak'
                band_alignment[band]['total'] += 1
                if app.status in selected_statuses:
                    band_alignment[band]['shortlisted'] += 1

            exp_years = resume.extracted_experience_years if resume else None
            cohort = (
                'Early Career (0-2y)' if exp_years is not None and exp_years < 3
                else 'Mid-Level (3-6y)' if exp_years is not None and exp_years < 7
                else 'Senior (7y+)' if exp_years is not None
                else 'Unspecified Tenure'
            )
            total_by_cohort[cohort] = total_by_cohort.get(cohort, 0) + 1
            if app.status in selected_statuses:
                selected_by_cohort[cohort] = selected_by_cohort.get(cohort, 0) + 1

        air = adverse_impact_ratio(selected_by_cohort, total_by_cohort)
        return Response({
            'job_id': job.id,
            'job_title': job.title,
            'total_applicants': len(apps),
            'evaluated_count': evaluated_count,
            'redaction_coverage_percent': round((redacted_count / len(apps)) * 100, 1) if apps else 100.0,
            'jd_lint': {
                'is_clean': lint_result.get('is_clean', True),
                'is_regulated': lint_result.get('is_regulated', False),
                'flag_count': len(lint_result.get('flags', [])),
                'flags': lint_result.get('flags', []),
            },
            'band_alignment': [
                {
                    'band': band,
                    'total': data['total'],
                    'shortlisted': data['shortlisted'],
                    'shortlist_rate': round((data['shortlisted'] / data['total']) * 100, 1) if data['total'] else 0.0,
                }
                for band, data in band_alignment.items()
            ],
            'adverse_impact': air,
        })

    @action(detail=True, methods=['post'], url_path='fairness-audit/counterfactual', permission_classes=[IsCompanyRecruiter])
    def fairness_counterfactual(self, request, pk=None):
        """
        Runs a deterministic counterfactual invariance check on an application's resume text
        (or a representative sample) by perturbing name, pronouns, university prestige, and address,
        passing each variant through TalentMatch's redaction & token-overlap scorer.
        """
        from apps.resumes.audit_service import run_counterfactual_audit_on_text
        from apps.resumes.redaction_service import redact_raw_resume_text

        job = self.get_object()
        app_id = request.data.get('application_id')
        target_app = None
        if app_id:
            target_app = job.applications.select_related('resume').filter(pk=app_id).first()
        else:
            target_app = job.applications.select_related('resume').filter(
                resume__extracted_text__gt=''
            ).first()

        base_text = ''
        applicant_info = {'first_name': 'Alex', 'last_name': 'Santos', 'email': 'alex@example.com', 'phone': '+63 917 123 4567'}
        sample_source = 'Synthetic benchmark resume'
        if target_app and hasattr(target_app, 'resume') and target_app.resume.extracted_text:
            base_text = target_app.resume.extracted_text
            applicant_info = {
                'first_name': target_app.first_name,
                'last_name': target_app.last_name,
                'email': target_app.email,
                'phone': target_app.phone,
            }
            sample_source = f'Application {target_app.candidate_code}'
        else:
            req_skills = ', '.join((job.required_skills or ['communication', 'problem solving'])[:6])
            base_text = (
                f"Name: Alex Santos\nGender: Male (he/him)\nAddress: Makati City, Metro Manila\n"
                f"Education: BS Degree, University of the Philippines\n"
                f"Experience: 4 years hands-on work with {req_skills}."
            )

        required_tokens = [s.lower().strip() for s in (job.required_skills or []) if str(s).strip()]

        def _offline_redacted_eval(text, info):
            redacted = redact_raw_resume_text(text, applicant_info=info)
            lowered = redacted.lower()
            if not required_tokens:
                return {'match_score': 80.0}
            hits = sum(1 for tok in required_tokens if tok in lowered)
            return {'match_score': round((hits / len(required_tokens)) * 100.0, 2)}

        variants = [
            {
                'label': 'Female pronoun & name swap',
                'applicant_info': {'first_name': 'Maria', 'last_name': 'Reyes', 'email': 'maria.reyes@example.com'},
                'text': f"Name: Maria Reyes\nPronouns: she/her\n{base_text}",
            },
            {
                'label': 'Regional university & provincial address swap',
                'applicant_info': {'first_name': 'Jose', 'last_name': 'Cruz'},
                'text': f"Name: Jose Cruz\nAddress: Cebu City, Philippines\nEducation: Polytechnic University of the Philippines\n{base_text}",
            },
            {
                'label': 'Elite university prestige swap',
                'applicant_info': {'first_name': 'Gabriel', 'last_name': 'Tan'},
                'text': f"Name: Gabriel Tan\nAddress: BGC, Taguig City\nEducation: Ateneo de Manila University\n{base_text}",
            },
        ]

        audit_result = run_counterfactual_audit_on_text(
            base_text=base_text,
            applicant_info=applicant_info,
            variants=variants,
            eval_fn=_offline_redacted_eval,
        )
        audit_result['sample_source'] = sample_source
        return Response(audit_result, status=status.HTTP_200_OK)


class ArchivedJobViewSet(viewsets.ModelViewSet):
    queryset = (
        ArchivedJob.objects
        .select_related('company', 'active_job')
        .annotate(applicant_count=Count('active_job__applications'))
        .all()
    )
    serializer_class = ArchivedJobSerializer
    permission_classes = [IsCompanyRecruiter]
    http_method_names = ['get', 'post', 'head', 'options']
    pagination_class = StandardResultsSetPagination

    def get_serializer_class(self):
        return ArchivedJobListSerializer if self.action == 'list' else ArchivedJobSerializer

    def get_queryset(self):
        queryset = scope_recruiter_records(super().get_queryset(), self.request.user, 'company_id')
        department = (self.request.query_params.get('department') or '').strip()
        if department:
            queryset = queryset.filter(department__iexact=department)
        query = (self.request.query_params.get('q') or '').strip()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) | Q(department__icontains=query) | Q(location__icontains=query)
            )
        ordering = self.request.query_params.get('ordering') or '-archived_at'
        allowed = {'archived_at', '-archived_at', 'title', '-title'}
        return queryset.order_by(ordering if ordering in allowed else '-archived_at', '-id')

    def create(self, request, *args, **kwargs):
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=['post'], url_path='restore', permission_classes=[IsCompanyRecruiter])
    @transaction.atomic
    def restore(self, request, pk=None):
        archived_job = self.get_object()
        job = None
        if archived_job.active_job_id:
            job = get_object_or_404(Job.objects.select_for_update(), pk=archived_job.active_job_id)
        if job is not None:
            job.status = JobStatus.PUBLISHED if (
                job.salary_min is not None
                and job.salary_max is not None
                and job.job_overview
                and len(job.job_overview.strip()) >= 40
            ) else JobStatus.DRAFT
            job.save(update_fields=['status', 'updated_at'])
        else:
            job = Job.objects.create(
                company=archived_job.company,
                title=archived_job.title,
                department=archived_job.department,
                description=archived_job.description,
                job_overview=archived_job.job_overview,
                employment_type=archived_job.employment_type,
                location=archived_job.location,
                minimum_experience=archived_job.minimum_experience,
                education_requirement=archived_job.education_requirement,
                required_skills=archived_job.required_skills,
                preferred_skills=archived_job.preferred_skills,
                salary_min=archived_job.salary_min,
                salary_max=archived_job.salary_max,
                rubric_weights=archived_job.rubric_weights,
                status=(JobStatus.PUBLISHED if (
                    archived_job.salary_min is not None
                    and archived_job.salary_max is not None
                    and archived_job.job_overview
                    and len(archived_job.job_overview.strip()) >= 40
                ) else JobStatus.DRAFT),
            )
        record_security_event(request.user, 'job.restore', job.pk)
        archived_job.delete()
        return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)
