import uuid
from django.db import transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
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
from apps.applications.models import (
    Application,
    ApplicationActivity,
    ApplicationStatus,
    CandidateNote,
    RecruiterTask,
)
from apps.jobs.models import JobStatus
from apps.applications.serializers import (
    ApplicationSerializer,
    ApplicantApplicationSerializer,
    RecruiterApplicationSerializer,
    ApplicationCreateSerializer,
    ApplicationActivitySerializer,
    CandidateNoteSerializer,
    MatchResultSerializer,
    RecruiterTaskSerializer,
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
        if self.action in (
            'update_status', 'reprocess', 'match_details', 'notes', 'delete_note',
            'activity', 'tasks', 'update_task', 'bulk_update', 'communications',
            'communication_templates',
        ):
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
            queryset = scope_recruiter_records(queryset, user, 'job__company_id').annotate(
                _open_task_count=Count(
                    'recruiter_tasks',
                    filter=Q(recruiter_tasks__completed_at__isnull=True),
                    distinct=True,
                ),
                _latest_activity_at=Max('activity__created_at'),
            )
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
            actor_id, actor_name = self._actor()
            record_security_event(
                self.request.user, 'application.status', application.pk,
                previous=previous, current=application.status,
            )
            ApplicationActivity.objects.create(
                application=application,
                kind=ApplicationActivity.Kind.STATUS,
                actor_id=actor_id,
                actor_name=actor_name,
                summary=f'Moved from {previous.replace("_", " ").title()} to {application.get_status_display()}',
                details={'previous': previous, 'current': application.status},
            )
            create_status_change_notification(application, previous, application.status)

    def _actor(self):
        user = self.request.user
        actor_id = str(getattr(user, 'supabase_id', getattr(user, 'pk', 'recruiter')))
        actor_name = (
            getattr(user, 'email', '')
            or getattr(user, 'first_name', '')
            or 'Recruiter'
        )
        return actor_id, actor_name

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

    @action(detail=True, methods=['get', 'post'], url_path='notes', permission_classes=[IsCompanyRecruiter])
    def notes(self, request, pk=None):
        application = self.get_object()
        if request.method == 'GET':
            return Response(CandidateNoteSerializer(application.recruiter_notes.all(), many=True).data)

        serializer = CandidateNoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor_id, actor_name = self._actor()
        note = serializer.save(application=application, author_id=actor_id, author_name=actor_name)
        ApplicationActivity.objects.create(
            application=application,
            kind=ApplicationActivity.Kind.NOTE,
            actor_id=actor_id,
            actor_name=actor_name,
            summary='Added a private recruiter note',
            details={'note_id': note.pk},
        )
        record_security_event(request.user, 'application.note.create', application.pk, note_id=note.pk)
        return Response(CandidateNoteSerializer(note).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=['delete'],
        url_path=r'notes/(?P<note_id>[^/.]+)',
        permission_classes=[IsCompanyRecruiter],
    )
    def delete_note(self, request, pk=None, note_id=None):
        application = self.get_object()
        note = get_object_or_404(application.recruiter_notes, pk=note_id)
        note.delete()
        record_security_event(request.user, 'application.note.delete', application.pk, note_id=note_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get'], url_path='activity', permission_classes=[IsCompanyRecruiter])
    def activity(self, request, pk=None):
        application = self.get_object()
        return Response(ApplicationActivitySerializer(application.activity.all()[:100], many=True).data)

    @action(detail=True, methods=['get', 'post'], url_path='tasks', permission_classes=[IsCompanyRecruiter])
    def tasks(self, request, pk=None):
        application = self.get_object()
        if request.method == 'GET':
            return Response(RecruiterTaskSerializer(application.recruiter_tasks.all(), many=True).data)

        serializer = RecruiterTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor_id, actor_name = self._actor()
        task = serializer.save(
            application=application,
            created_by=actor_id,
            assigned_to=serializer.validated_data.get('assigned_to') or actor_id,
            assigned_to_name=serializer.validated_data.get('assigned_to_name') or actor_name,
        )
        ApplicationActivity.objects.create(
            application=application,
            kind=ApplicationActivity.Kind.TASK,
            actor_id=actor_id,
            actor_name=actor_name,
            summary=f'Created task: {task.title}',
            details={'task_id': task.pk, 'due_at': task.due_at.isoformat() if task.due_at else None},
        )
        return Response(RecruiterTaskSerializer(task).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=['patch'],
        url_path=r'tasks/(?P<task_id>[^/.]+)',
        permission_classes=[IsCompanyRecruiter],
    )
    def update_task(self, request, pk=None, task_id=None):
        application = self.get_object()
        task = get_object_or_404(application.recruiter_tasks, pk=task_id)
        allowed = {'title', 'priority', 'due_at', 'assigned_to', 'assigned_to_name', 'is_complete'}
        if set(request.data) - allowed:
            return Response({'detail': 'Unsupported task field.'}, status=status.HTTP_400_BAD_REQUEST)
        complete = request.data.get('is_complete')
        if complete is not None and not isinstance(complete, bool):
            return Response({'detail': 'is_complete must be true or false.'}, status=status.HTTP_400_BAD_REQUEST)
        payload = {key: value for key, value in request.data.items() if key != 'is_complete'}
        serializer = RecruiterTaskSerializer(task, data=payload, partial=True)
        serializer.is_valid(raise_exception=True)
        task = serializer.save()
        if complete is not None:
            task.completed_at = timezone.now() if bool(complete) else None
            task.save(update_fields=['completed_at', 'updated_at'])
        actor_id, actor_name = self._actor()
        ApplicationActivity.objects.create(
            application=application,
            kind=ApplicationActivity.Kind.TASK,
            actor_id=actor_id,
            actor_name=actor_name,
            summary=('Completed task: ' if task.completed_at else 'Updated task: ') + task.title,
            details={'task_id': task.pk},
        )
        return Response(RecruiterTaskSerializer(task).data)

    @action(detail=False, methods=['post'], url_path='bulk', permission_classes=[IsCompanyRecruiter])
    @transaction.atomic
    def bulk_update(self, request):
        application_ids = request.data.get('application_ids')
        new_status = request.data.get('status')
        if not isinstance(application_ids, list) or not application_ids or len(application_ids) > 100:
            return Response({'detail': 'Select between 1 and 100 applications.'}, status=status.HTTP_400_BAD_REQUEST)
        if any(not isinstance(application_id, int) or isinstance(application_id, bool) for application_id in application_ids):
            return Response({'detail': 'Application IDs must be integers.'}, status=status.HTTP_400_BAD_REQUEST)
        if new_status not in ApplicationStatus.values:
            return Response({'detail': 'A valid target status is required.'}, status=status.HTTP_400_BAD_REQUEST)
        # Lock a plain, company-scoped queryset. The recruiter list queryset contains
        # nullable joins and aggregate annotations; PostgreSQL rejects FOR UPDATE on
        # the nullable side of an outer join.
        visible = scope_recruiter_records(
            Application.objects.all(), request.user, 'job__company_id'
        ).filter(pk__in=application_ids)
        applications = list(visible.select_for_update())
        if len(applications) != len(set(application_ids)):
            return Response({'detail': 'One or more applications are unavailable.'}, status=status.HTTP_403_FORBIDDEN)
        for application in applications:
            previous = application.status
            if previous == new_status:
                continue
            application.status = new_status
            application.save(update_fields=['status', 'updated_at'])
            self._record_status_change(application, previous)
        enriched = self.get_queryset().filter(pk__in=application_ids)
        return Response(RecruiterApplicationSerializer(enriched, many=True).data)

    @action(detail=False, methods=['get'], url_path='communication-templates', permission_classes=[IsCompanyRecruiter])
    def communication_templates(self, request):
        templates = [
            {'id': 'screening', 'name': 'Screening invitation', 'subject': 'Next step for {{job_title}}', 'body': 'Hello {{candidate_name}},\n\nWe would like to invite you to the next screening step for {{job_title}}. Please reply with your availability.\n\nThank you,\n{{recruiter_name}}'},
            {'id': 'interview', 'name': 'Interview invitation', 'subject': 'Interview invitation — {{job_title}}', 'body': 'Hello {{candidate_name}},\n\nYour application for {{job_title}} has progressed to interview. We will confirm the schedule with you shortly.\n\nRegards,\n{{recruiter_name}}'},
            {'id': 'rejection', 'name': 'Application update', 'subject': 'Update on your {{job_title}} application', 'body': 'Hello {{candidate_name}},\n\nThank you for your interest in {{job_title}}. After careful review, we will not be progressing your application at this time.\n\nWe appreciate the time you invested.\n{{recruiter_name}}'},
        ]
        return Response(templates)

    @action(detail=True, methods=['post'], url_path='communications', permission_classes=[IsCompanyRecruiter])
    def communications(self, request, pk=None):
        application = self.get_object()
        subject = str(request.data.get('subject') or '').strip()
        body = str(request.data.get('body') or '').strip()
        draft_action = str(request.data.get('draft_action') or '').strip().upper()
        if not subject or not body or len(subject) > 200 or len(body) > 5000:
            return Response({'detail': 'Subject and message are required.'}, status=status.HTTP_400_BAD_REQUEST)
        action_labels = {
            'OPENED_GMAIL': ('Opened Gmail draft', 'GMAIL'),
            'COPIED': ('Copied email draft', 'COPY'),
        }
        if draft_action not in action_labels:
            return Response(
                {'detail': 'Choose a supported email draft action.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        action_label, channel = action_labels[draft_action]
        actor_id, actor_name = self._actor()
        activity = ApplicationActivity.objects.create(
            application=application,
            kind=ApplicationActivity.Kind.COMMUNICATION,
            actor_id=actor_id,
            actor_name=actor_name,
            summary=f'{action_label}: {subject}',
            details={
                'subject': subject,
                'body': body,
                'channel': channel,
                'delivery_status': 'DRAFT',
            },
        )
        record_security_event(
            request.user,
            'application.communication.draft',
            application.pk,
            activity_id=activity.pk,
            draft_action=draft_action,
        )
        return Response(ApplicationActivitySerializer(activity).data, status=status.HTTP_201_CREATED)

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
