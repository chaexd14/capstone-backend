from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from apps.applications.models import Application, ApplicationStatus
from apps.applications.serializers import ApplicationSerializer, ApplicationCreateSerializer, MatchResultSerializer
from apps.resumes.services import process_resume_and_calculate_match
from apps.notifications.services import create_status_change_notification

from django.core.cache import cache

class ApplicationViewSet(viewsets.ModelViewSet):
    queryset = Application.objects.select_related('job', 'resume', 'match_result').all()
    serializer_class = ApplicationSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def get_queryset(self):
        queryset = Application.objects.select_related('job', 'resume', 'match_result').all()
        job_id = self.request.query_params.get('job_id')
        status_param = self.request.query_params.get('status')
        email = self.request.query_params.get('email')
        user_id = self.request.query_params.get('user_id')
        tracking_token = self.request.query_params.get('tracking_token') or self.request.query_params.get('token')

        if tracking_token:
            queryset = queryset.filter(tracking_token=tracking_token)
        if job_id:
            queryset = queryset.filter(job_id=job_id)
        if status_param:
            queryset = queryset.filter(status=status_param)
        if email:
            queryset = queryset.filter(email__iexact=email)
        if user_id:
            queryset = queryset.filter(user_id=user_id)
        return queryset

    def list(self, request, *args, **kwargs):
        job_id = request.query_params.get('job_id', '')
        status_param = request.query_params.get('status', '')
        email = request.query_params.get('email', '')
        user_id = request.query_params.get('user_id', '')
        token = request.query_params.get('tracking_token', '') or request.query_params.get('token', '')
        cache_key = f"tm_apps_list_{job_id}_{status_param}_{email}_{user_id}_{token}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)
        response = super().list(request, *args, **kwargs)
        cache.set(cache_key, response.data, timeout=300)
        return response

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        token = request.query_params.get('token') or request.query_params.get('tracking_token') or request.headers.get('X-Tracking-Token')
        user_id = request.query_params.get('user_id')

        # If a tracking token is supplied, strictly verify it matches
        if token and str(instance.tracking_token) != str(token):
            return Response(
                {"detail": "Invalid tracking token. Access denied."},
                status=status.HTTP_403_FORBIDDEN
            )
        # If a user_id is supplied, verify it matches
        if user_id and instance.user_id and str(instance.user_id) != str(user_id):
            return Response(
                {"detail": "Application belongs to another user."},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        cache.clear()

    def perform_update(self, serializer):
        prev_status = serializer.instance.status if serializer.instance else None
        instance = serializer.save()
        cache.clear()
        if prev_status and instance.status != prev_status:
            create_status_change_notification(instance, prev_status, instance.status)

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        cache.clear()

    @action(detail=True, methods=['patch'], url_path='status', permission_classes=[AllowAny], authentication_classes=[])
    def update_status(self, request, pk=None):
        application = self.get_object()
        prev_status = application.status
        new_status = request.data.get('status')

        if not new_status or new_status not in ApplicationStatus.values:
            return Response(
                {"error": f"Invalid status. Must be one of: {list(ApplicationStatus.values)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        application.status = new_status
        application.save(update_fields=['status'])
        cache.clear()
        if prev_status != new_status:
            create_status_change_notification(application, prev_status, new_status)

        serializer = self.get_serializer(application)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='reprocess', permission_classes=[AllowAny], authentication_classes=[])
    def reprocess(self, request, pk=None):
        application = self.get_object()
        match_result = process_resume_and_calculate_match(application)
        if not match_result:
            return Response({"error": "No resume attached or processing failed"}, status=status.HTTP_400_BAD_REQUEST)
        
        cache.clear()
        serializer = self.get_serializer(application)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='match', permission_classes=[AllowAny], authentication_classes=[])
    def match_details(self, request, pk=None):
        application = self.get_object()
        token = request.query_params.get('token') or request.query_params.get('tracking_token') or request.headers.get('X-Tracking-Token')
        user_id = request.query_params.get('user_id')

        if token and str(application.tracking_token) != str(token):
            return Response({"detail": "Invalid tracking token. Access denied."}, status=status.HTTP_403_FORBIDDEN)
        if user_id and application.user_id and str(application.user_id) != str(user_id):
            return Response({"detail": "Application belongs to another user."}, status=status.HTTP_403_FORBIDDEN)

        if hasattr(application, 'match_result'):
            serializer = MatchResultSerializer(application.match_result)
            return Response(serializer.data)
        return Response({"detail": "Match result not yet calculated"}, status=status.HTTP_404_NOT_FOUND)
