import uuid
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import AnonRateThrottle
from apps.resumes.views import get_trusted_client_ip

class TrackingTokenRateThrottle(AnonRateThrottle):
    scope = 'tracking_token'

    def get_ident(self, request):
        return get_trusted_client_ip(request)

    def get_rate(self):
        from django.conf import settings
        rates = getattr(settings, 'REST_FRAMEWORK', {}).get('DEFAULT_THROTTLE_RATES', {})
        return rates.get(self.scope, '20/minute')
from apps.applications.models import Application, ApplicationStatus
from apps.applications.serializers import (
    ApplicationSerializer,
    ApplicantApplicationSerializer,
    RecruiterApplicationSerializer,
    ApplicationCreateSerializer,
    MatchResultSerializer,
)
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
        if self.action in ('update_status', 'reprocess', 'match_details'):
            return [IsRecruiterOrSuperAdmin()]
        if self.action == 'track_status' or (hasattr(self, 'request') and self.request and 'track-status' in getattr(self.request, 'path', '')):
            return [AllowAny()]
        return [IsAuthenticated(), IsApplicationOwnerOrRecruiter()]

    def get_serializer_class(self):
        user = getattr(self.request, 'user', None)
        if not user or not user.is_authenticated:
            return ApplicantApplicationSerializer
        role = getattr(user, 'role', '').lower()
        if role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser:
            return RecruiterApplicationSerializer
        return ApplicantApplicationSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = Application.objects.select_related('job', 'resume', 'match_result').all()

        if not user or not user.is_authenticated:
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

        # Applicants are strictly restricted to their own applications; fail closed if identity is invalid
        user_uuid = getattr(user, 'supabase_id', None)
        if not user_uuid and hasattr(user, 'role_profile') and user.role_profile and user.role_profile.supabase_user_id:
            user_uuid = str(user.role_profile.supabase_user_id)
        if not user_uuid:
            user_uuid = str(getattr(user, 'username', ''))
        try:
            valid_uuid = uuid.UUID(str(user_uuid))
        except (ValueError, TypeError, AttributeError):
            return queryset.none()
        return queryset.filter(user_id=valid_uuid)

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

    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "Method \"POST\" not allowed. Applications must be submitted via /api/jobs/{id}/apply/."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

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

    @action(detail=True, methods=['get'], url_path='match', permission_classes=[IsRecruiterOrSuperAdmin])
    def match_details(self, request, pk=None):
        application = self.get_object()
        if hasattr(application, 'match_result'):
            serializer = MatchResultSerializer(application.match_result)
            return Response(serializer.data)
        return Response({"detail": "Match result not yet calculated"}, status=status.HTTP_404_NOT_FOUND)

    @action(
        detail=False,
        methods=['post'],
        url_path='track-status',
        permission_classes=[AllowAny],
        throttle_classes=[TrackingTokenRateThrottle]
    )
    def track_status(self, request):
        """
        Narrowly-scoped public status tracking endpoint.
        Allows applicants to check high-level submission and evaluation status
        using their unique tracking_token without granting access to sensitive PII,
        resume downloads, or recruiter evaluation data.

        Accepts token strictly via POST JSON body: {"token": "<uuid>"}.
        GET requests are rejected with HTTP 405 Method Not Allowed to eliminate
        token exposure in browser history, proxy access logs, and HTTP Referer headers.
        Enforces Cache-Control: private, no-store, no-cache, must-revalidate and rate limiting.
        Never logs tracking tokens in server logs.
        """
        def _build_response(data, code):
            resp = Response(data, status=code)
            resp['Cache-Control'] = 'private, no-store, no-cache, must-revalidate'
            resp['Pragma'] = 'no-cache'
            return resp

        if request.method != 'POST':
            return _build_response(
                {
                    "error": "Method Not Allowed. Public tracking status requires a POST request "
                             "with {'token': '<uuid>'} in the request body to prevent token exposure "
                             "in access logs and browser history."
                },
                status.HTTP_405_METHOD_NOT_ALLOWED
            )

        token_str = (request.data.get('token') or '').strip()
        if not token_str:
            return _build_response({"error": "Tracking token is required in request body."}, status.HTTP_400_BAD_REQUEST)

        try:
            token_uuid = uuid.UUID(token_str)
        except (ValueError, TypeError):
            return _build_response({"error": "Invalid tracking token format."}, status.HTTP_400_BAD_REQUEST)

        app = Application.objects.select_related('job', 'resume').filter(tracking_token=token_uuid).first()
        if not app or app.tracking_token is None:
            return _build_response({"error": "Application not found or tracking token is invalid/revoked."}, status.HTTP_404_NOT_FOUND)

        processing_status = app.resume.processing_status if hasattr(app, 'resume') and app.resume else 'PENDING'
        return _build_response({
            "candidate_code": app.candidate_code,
            "job_title": app.job.title if app.job else None,
            "job_department": app.job.department if app.job else None,
            "status": app.status,
            "processing_status": processing_status,
            "applied_at": app.applied_at,
            "updated_at": app.updated_at,
        }, status.HTTP_200_OK)
