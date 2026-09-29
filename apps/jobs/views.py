from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from apps.jobs.models import Job, JobStatus
from apps.jobs.serializers import JobSerializer
from apps.applications.models import Application
from apps.applications.serializers import ApplicationSerializer, ApplicationCreateSerializer
from apps.resumes.services import process_resume_and_calculate_match

class JobViewSet(viewsets.ModelViewSet):
    queryset = Job.objects.all()
    serializer_class = JobSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def get_queryset(self):
        queryset = Job.objects.all()
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        return queryset

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

            # Return the full application details including match score and resume
            full_serializer = ApplicationSerializer(application, context={'request': request})
            return Response(full_serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='applicants', permission_classes=[AllowAny], authentication_classes=[])
    def applicants(self, request, pk=None):
        job = self.get_object()
        applications = job.applications.all()
        serializer = ApplicationSerializer(applications, many=True, context={'request': request})
        return Response(serializer.data)
