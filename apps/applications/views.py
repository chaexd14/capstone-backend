import uuid
from django.db import transaction
from django.shortcuts import get_object_or_404
from apps.accounts.models import record_security_event
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import SimpleRateThrottle
from apps.resumes.views import get_trusted_client_ip
from config.throttling import SharedRateLimit

class TrackingTokenRateThrottle(SharedRateLimit, SimpleRateThrottle):
    scope = 'tracking_token'

    def get_ident(self, request):
        return get_trusted_client_ip(request)

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}

    def get_rate(self):
        from django.conf import settings
        rates = getattr(settings, 'REST_FRAMEWORK', {}).get('DEFAULT_THROTTLE_RATES', {})
        return rates.get(self.scope, '20/minute')
from apps.applications.models import Application, ApplicationStatus
from apps.jobs.models import JobStatus
from apps.applications.serializers import (
    ApplicationSerializer,
    ApplicantApplicationSerializer,
    RecruiterApplicationSerializer,
    ApplicationCreateSerializer,
    MatchResultSerializer,
)
from apps.resumes.tasks import enqueue_resume_processing
from apps.notifications.services import create_status_change_notification
from config.permissions import (
    IsCompanyRecruiter, IsApplicationOwnerOrRecruiter,
    is_recruiter, scope_recruiter_records,
)


class ApplicationViewSet(viewsets.ModelViewSet):
    queryset = Application.objects.select_related('job', 'resume', 'match_result').all()
    serializer_class = ApplicationSerializer
    permission_classes = [IsApplicationOwnerOrRecruiter]
    http_method_names = ['get', 'post', 'put', 'patch', 'head', 'options']

    def get_permissions(self):
        if self.action in ('update_status', 'reprocess', 'match_details'):
            return [IsCompanyRecruiter()]
        if self.action == 'track_status' or (hasattr(self, 'request') and self.request and 'track-status' in getattr(self.request, 'path', '')):
            return [AllowAny()]
        return [IsAuthenticated(), IsApplicationOwnerOrRecruiter()]

    def get_serializer_class(self):
        user = getattr(self.request, 'user', None)
        if not user or not user.is_authenticated:
            return ApplicantApplicationSerializer
        if is_recruiter(user):
            return RecruiterApplicationSerializer
        return ApplicantApplicationSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = Application.objects.select_related('job', 'resume', 'match_result').all()

        if not user or not user.is_authenticated:
            return queryset.none()

        if is_recruiter(user):
            queryset = scope_recruiter_records(queryset, user, 'job__company_id')
            if self.action == 'list':
                queryset = queryset.exclude(job__status=JobStatus.ARCHIVED)
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

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "Method \"POST\" not allowed. Applications must be submitted via /api/jobs/{id}/apply/."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

    def _lock_application(self):
        visible = self.get_object()
        return get_object_or_404(Application.objects.select_for_update(), pk=visible.pk)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        application = self._lock_application()
        previous = application.status
        serializer = self.get_serializer(application, data=request.data, partial=kwargs.get('partial', False))
        serializer.is_valid(raise_exception=True)
        serializer.save()
        self._record_status_change(application, previous)
        return Response(serializer.data)

    def _record_status_change(self, application, previous):
        if previous != application.status:
            record_security_event(
                self.request.user, 'application.status', application.pk,
                previous=previous, current=application.status,
            )
            create_status_change_notification(application, previous, application.status)

    @action(detail=True, methods=['patch'], url_path='status', permission_classes=[IsCompanyRecruiter])
    @transaction.atomic
    def update_status(self, request, pk=None):
        application = self._lock_application()
        previous = application.status
        new_status = request.data.get('status')
        if new_status not in ApplicationStatus.values:
            return Response(
                {"error": f"Invalid status. Must be one of: {list(ApplicationStatus.values)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_status != previous:
            application.status = new_status
            application.save(update_fields=['status', 'updated_at'])
            self._record_status_change(application, previous)
        return Response(self.get_serializer(application).data)

    @action(detail=True, methods=['post'], url_path='reprocess', permission_classes=[IsCompanyRecruiter])
    def reprocess(self, request, pk=None):
        application = self.get_object()
        if not hasattr(application, 'resume') or not application.resume.file:
            return Response({"error": "No resume attached"}, status=status.HTTP_400_BAD_REQUEST)
        enqueue_resume_processing(application.pk, retry_failed=True)
        return Response(self.get_serializer(application).data, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'], url_path='match', permission_classes=[IsCompanyRecruiter])
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

        token = request.data.get('token')
        token_str = token.strip() if isinstance(token, str) else ''
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
