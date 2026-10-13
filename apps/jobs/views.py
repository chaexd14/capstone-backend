from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from apps.jobs.models import Job, JobStatus, ArchivedJob
from apps.jobs.serializers import JobSerializer, ArchivedJobSerializer
from apps.applications.models import Application
from apps.applications.serializers import ApplicationSerializer, ApplicationCreateSerializer
from apps.resumes.tasks import enqueue_resume_processing
from apps.jobs.services import generate_job_requisition_with_gemini, lint_job_requisition
from apps.notifications.services import create_new_application_notification
from config.permissions import IsRecruiterOrSuperAdmin

from django.db import models, transaction
from django.db.models import Count
from django.core.cache import cache

class JobViewSet(viewsets.ModelViewSet):
    queryset = Job.objects.select_related('company').annotate(applicant_count=Count('applications')).all()
    serializer_class = JobSerializer
    permission_classes = [IsRecruiterOrSuperAdmin]

    def get_permissions(self):
        if self.action in ['list', 'retrieve', 'public_stats']:
            return [AllowAny()]
        if self.action == 'apply':
            return [IsAuthenticated()]
        return [IsRecruiterOrSuperAdmin()]

    def get_queryset(self):
        queryset = Job.objects.select_related('company').annotate(applicant_count=Count('applications')).all()
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

    def list(self, request, *args, **kwargs):
        status_param = request.query_params.get('status', 'all')
        cache_key = f"tm_jobs_list_{status_param}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)
        response = super().list(request, *args, **kwargs)
        cache.set(cache_key, response.data, timeout=300)
        return response

    def perform_create(self, serializer):
        super().perform_create(serializer)
        cache.clear()

    def perform_update(self, serializer):
        super().perform_update(serializer)
        cache.clear()

    def perform_destroy(self, instance):
        with transaction.atomic():
            for app in instance.applications.all():
                if hasattr(app, 'resume') and app.resume and app.resume.file:
                    try:
                        app.resume.file.delete(save=False)
                    except Exception:
                        pass
                app.delete()
            instance.delete()
        cache.clear()

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

    @action(detail=True, methods=['post'], url_path='close', permission_classes=[IsRecruiterOrSuperAdmin])
    def close(self, request, pk=None):
        job = self.get_object()
        job.status = JobStatus.CLOSED
        job.save(update_fields=['status', 'updated_at'])
        cache.clear()
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='reopen', permission_classes=[IsRecruiterOrSuperAdmin])
    def reopen(self, request, pk=None):
        job = self.get_object()
        job.status = JobStatus.PUBLISHED
        job.save(update_fields=['status', 'updated_at'])
        cache.clear()
        serializer = self.get_serializer(job)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='archive', permission_classes=[IsRecruiterOrSuperAdmin])
    def archive(self, request, pk=None):
        job = self.get_object()
        with transaction.atomic():
            # 1. Permanently delete all applications and their physical resume files
            for app in job.applications.all():
                if hasattr(app, 'resume') and app.resume and app.resume.file:
                    try:
                        app.resume.file.delete(save=False)
                    except Exception:
                        pass
                app.delete()

            # 2. Store to dedicated ArchivedJob table
            archived_job = ArchivedJob.objects.create(
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

            # 3. Permanently remove active Job record
            job.delete()

        cache.clear()
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

        data = request.data.copy()
        data['job'] = job.id

        # Bind application strictly to the authenticated user's ID
        user_uuid = getattr(request.user, 'supabase_id', str(request.user.username))
        data['user_id'] = user_uuid

        serializer = ApplicationCreateSerializer(data=data)
        if serializer.is_valid():
            with transaction.atomic():
                application = serializer.save()
                # On successful commit, safely enqueue background AI processing and notifications
                app_id = application.id
                transaction.on_commit(lambda: enqueue_resume_processing(app_id))
                transaction.on_commit(lambda: create_new_application_notification(application))

            cache.clear()
            # Return the application details immediately with PENDING/PROCESSING status & tracking_token
            full_serializer = ApplicationSerializer(application, context={'request': request})
            return Response(full_serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='applicants', permission_classes=[IsRecruiterOrSuperAdmin])
    def applicants(self, request, pk=None):
        job = self.get_object()
        applications = job.applications.select_related('job', 'resume', 'match_result').all()
        serializer = ApplicationSerializer(applications, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='ai-draft', permission_classes=[IsRecruiterOrSuperAdmin])
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

    @action(detail=False, methods=['post'], url_path='lint-jd', permission_classes=[IsRecruiterOrSuperAdmin])
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
    permission_classes = [IsRecruiterOrSuperAdmin]
    http_method_names = ['get', 'post', 'delete', 'head', 'options']

    def perform_destroy(self, instance):
        instance.delete()
        cache.clear()

    @action(detail=True, methods=['post'], url_path='restore', permission_classes=[IsRecruiterOrSuperAdmin])
    def restore(self, request, pk=None):
        archived_job = self.get_object()
        with transaction.atomic():
            # Recreate active job with PUBLISHED status
            new_job = Job.objects.create(
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
            archived_job.delete()

        cache.clear()
        job_serializer = JobSerializer(new_job)
        return Response(job_serializer.data, status=status.HTTP_201_CREATED)
