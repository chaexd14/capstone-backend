from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from apps.applications.models import Application, ApplicationStatus
from apps.applications.serializers import ApplicationSerializer, ApplicationCreateSerializer, MatchResultSerializer
from apps.resumes.services import process_resume_and_calculate_match

class ApplicationViewSet(viewsets.ModelViewSet):
    queryset = Application.objects.all()
    serializer_class = ApplicationSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def get_queryset(self):
        queryset = Application.objects.all()
        job_id = self.request.query_params.get('job_id')
        status_param = self.request.query_params.get('status')
        email = self.request.query_params.get('email')

        if job_id:
            queryset = queryset.filter(job_id=job_id)
        if status_param:
            queryset = queryset.filter(status=status_param)
        if email:
            queryset = queryset.filter(email__iexact=email)
        return queryset

    @action(detail=True, methods=['patch'], url_path='status', permission_classes=[AllowAny], authentication_classes=[])
    def update_status(self, request, pk=None):
        application = self.get_object()
        new_status = request.data.get('status')

        if not new_status or new_status not in ApplicationStatus.values:
            return Response(
                {"error": f"Invalid status. Must be one of: {list(ApplicationStatus.values)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        application.status = new_status
        application.save(update_fields=['status'])
        serializer = self.get_serializer(application)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='reprocess', permission_classes=[AllowAny], authentication_classes=[])
    def reprocess(self, request, pk=None):
        application = self.get_object()
        match_result = process_resume_and_calculate_match(application)
        if not match_result:
            return Response({"error": "No resume attached or processing failed"}, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = self.get_serializer(application)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='match', permission_classes=[AllowAny], authentication_classes=[])
    def match_details(self, request, pk=None):
        application = self.get_object()
        if hasattr(application, 'match_result'):
            serializer = MatchResultSerializer(application.match_result)
            return Response(serializer.data)
        return Response({"detail": "Match result not yet calculated"}, status=status.HTTP_404_NOT_FOUND)
