from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from apps.jobs.models import Job, JobStatus
from apps.jobs.serializers import JobSerializer
from apps.applications.models import Application
from apps.applications.serializers import ApplicationSerializer, ApplicationCreateSerializer
from apps.resumes.services import process_resume_and_calculate_match
from apps.jobs.services import generate_job_requisition_with_gemini, lint_job_requisition

from django.db.models import Count
from django.core.cache import cache

class JobViewSet(viewsets.ModelViewSet):
    queryset = Job.objects.select_related('company').annotate(applicant_count=Count('applications')).all()
    serializer_class = JobSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

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
        super().perform_destroy(instance)
        cache.clear()

    @action(detail=True, methods=['post'], url_path='apply', permission_classes=[AllowAny], authentication_classes=[])
    def apply(self, request, pk=None):
        job = self.get_object()
        data = request.data.copy()
        data['job'] = job.id

        serializer = ApplicationCreateSerializer(data=data)
        if serializer.is_valid():
            application = serializer.save()
            
            # Immediately run the AI extraction and matching pipeline
            try:
                process_resume_and_calculate_match(application)
            except Exception as e:
                print(f"Error during AI resume processing: {e}")

            cache.clear()
            # Return the full application details including match score and resume
            full_serializer = ApplicationSerializer(application, context={'request': request})
            return Response(full_serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='applicants', permission_classes=[AllowAny], authentication_classes=[])
    def applicants(self, request, pk=None):
        job = self.get_object()
        applications = job.applications.select_related('job', 'resume', 'match_result').all()
        serializer = ApplicationSerializer(applications, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='ai-draft', permission_classes=[AllowAny], authentication_classes=[])
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

    @action(detail=False, methods=['post'], url_path='lint-jd', permission_classes=[AllowAny], authentication_classes=[])
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

