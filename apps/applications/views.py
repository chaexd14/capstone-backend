from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.applications.models import Application, ApplicationStatus
from apps.applications.serializers import ApplicationSerializer, ApplicationCreateSerializer, MatchResultSerializer
from apps.resumes.services import process_resume_and_calculate_match
from apps.notifications.services import create_status_change_notification
from config.permissions import IsRecruiterOrSuperAdmin, IsApplicationOwnerOrRecruiter

from django.db.models import Q
from django.core.cache import cache

class ApplicationViewSet(viewsets.ModelViewSet):
    queryset = Application.objects.select_related('job', 'resume', 'match_result').all()
    serializer_class = ApplicationSerializer
    permission_classes = [IsApplicationOwnerOrRecruiter]

    def get_permissions(self):
        token = self.request.query_params.get('tracking_token') or self.request.headers.get('X-Tracking-Token')
        if token and self.action in ['retrieve', 'match_details']:
            return [IsApplicationOwnerOrRecruiter()]
        return [IsAuthenticated(), IsApplicationOwnerOrRecruiter()]

    def get_queryset(self):
        user = self.request.user
        queryset = Application.objects.select_related('job', 'resume', 'match_result').all()
        token = self.request.query_params.get('tracking_token') or self.request.headers.get('X-Tracking-Token')

        if not user or not user.is_authenticated:
            if token and getattr(self, 'action', '') in ('retrieve', 'match_details'):
                return queryset.filter(tracking_token=token)
            return queryset.none()

        role = getattr(user, 'role', '').lower()
        is_recruiter = role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser

        if is_recruiter:
            job_id = self.request.query_params.get('job_id')
            status_param = self.request.query_params.get('status')
            email = self.request.query_params.get('email')
            user_id = self.request.query_params.get('user_id')

            if job_id:
                queryset = queryset.filter(job_id=job_id)
            if status_param:
                queryset = queryset.filter(status=status_param)
            if email:
                queryset = queryset.filter(email__iexact=email)
            if user_id:
                queryset = queryset.filter(user_id=user_id)
            return queryset

        # Applicants are strictly restricted to their own applications (or matching tracking token)
        user_uuid = getattr(user, 'supabase_id', str(user.username))
        if token:
            return queryset.filter(Q(user_id=user_uuid) | Q(tracking_token=token))
        return queryset.filter(user_id=user_uuid)

    def list(self, request, *args, **kwargs):
        user = request.user
        if not user or not user.is_authenticated:
            return Response(
                {"detail": "Authentication credentials were not provided."},
                status=status.HTTP_401_UNAUTHORIZED
            )

        role = getattr(user, 'role', '').lower()
        is_recruiter = role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser

        job_id = request.query_params.get('job_id', '') if is_recruiter else ''
        status_param = request.query_params.get('status', '') if is_recruiter else ''
        email = request.query_params.get('email', '') if is_recruiter else ''
        target_user = request.query_params.get('user_id', '') if is_recruiter else getattr(user, 'supabase_id', str(user.username))

        cache_key = f"tm_apps_list_{role}_{user.id}_{job_id}_{status_param}_{email}_{target_user}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        response = super().list(request, *args, **kwargs)
        cache.set(cache_key, response.data, timeout=300)
        return response

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def perform_create(self, serializer):
        # Bind application ownership to current authenticated applicant
        user = self.request.user
        user_uuid = getattr(user, 'supabase_id', str(user.username))
        serializer.save(user_id=user_uuid)
        cache.clear()

    def perform_update(self, serializer):
        prev_status = serializer.instance.status if serializer.instance else None
        instance = serializer.save()
        cache.clear()
        if prev_status and instance.status != prev_status:
            create_status_change_notification(instance, prev_status, instance.status)

    def destroy(self, request, *args, **kwargs):
        # Destruction of application records is recruiter/admin only
        user = request.user
        role = getattr(user, 'role', '').lower()
        if not (role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser):
            return Response(
                {"detail": "Only recruiters or administrators can delete application records."},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        super().perform_destroy(instance)
        cache.clear()

    @action(detail=True, methods=['patch'], url_path='status', permission_classes=[IsRecruiterOrSuperAdmin])
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

    @action(detail=True, methods=['post'], url_path='reprocess', permission_classes=[IsRecruiterOrSuperAdmin])
    def reprocess(self, request, pk=None):
        application = self.get_object()
        match_result = process_resume_and_calculate_match(application)
        if not match_result:
            return Response({"error": "No resume attached or processing failed"}, status=status.HTTP_400_BAD_REQUEST)
        
        cache.clear()
        serializer = self.get_serializer(application)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='match', permission_classes=[IsAuthenticated, IsApplicationOwnerOrRecruiter])
    def match_details(self, request, pk=None):
        application = self.get_object()
        if hasattr(application, 'match_result'):
            serializer = MatchResultSerializer(application.match_result)
            return Response(serializer.data)
        return Response({"detail": "Match result not yet calculated"}, status=status.HTTP_404_NOT_FOUND)
