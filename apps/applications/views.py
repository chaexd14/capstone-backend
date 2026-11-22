from datetime import timedelta, timezone as dt_timezone
import statistics
import uuid
from django.db import transaction
from django.db.models import Avg, Count, Max, Q
from django.http import HttpResponse
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
    InterviewSchedule,
    InterviewScorecard,
    RecruiterTask,
    RejectionReason,
    CommunicationTemplate,
    TemplateStage,
    seed_default_templates,
)
from apps.jobs.models import JobStatus
from apps.applications.serializers import (
    ApplicationSerializer,
    ApplicantApplicationSerializer,
    RecruiterApplicationSerializer,
    ApplicationCreateSerializer,
    ApplicationActivitySerializer,
    CandidateNoteSerializer,
    GlobalRecruiterTaskSerializer,
    InterviewScheduleSerializer,
    InterviewScorecardSerializer,
    MatchResultSerializer,
    RecruiterTaskSerializer,
    CommunicationTemplateSerializer,
)
from apps.resumes.tasks import enqueue_resume_processing
from apps.notifications.services import create_status_change_notification
from apps.applications.report_service import (
    AllApplicationsReportRequestSerializer,
    EXCEL_MIME,
    PDF_MIME,
    applications_report_filename,
    build_applications_report,
    render_applications_excel,
    render_applications_pdf,
)
from config.permissions import (
    IsCompanyRecruiter, IsApplicationOwnerOrRecruiter,
    is_recruiter, scope_recruiter_records, recruiter_company_id,
)


class ApplicationViewSet(viewsets.ModelViewSet):
    queryset = Application.objects.select_related('job', 'resume', 'match_result').all()
    serializer_class = ApplicationSerializer
    permission_classes = [IsApplicationOwnerOrRecruiter]
    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']

    def get_permissions(self):
        if self.action in (
            'update_status', 'reprocess', 'match_details', 'notes', 'delete_note',
            'activity', 'tasks', 'update_task', 'bulk_update', 'communications',
            'communication_templates', 'export_report', 'tags', 'global_tasks',
            'analytics', 'talent_pool', 'interviews', 'interview_detail',
            'interview_ics', 'scorecards', 'scorecard_detail',
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
                _interview_count=Count(
                    'interviews',
                    filter=~Q(interviews__status=InterviewSchedule.Status.CANCELLED),
                    distinct=True,
                ),
                _scorecard_count=Count('scorecards', distinct=True),
                _scorecard_avg_rating=Avg('scorecards__overall_rating'),
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

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"detail": 'Method "DELETE" not allowed.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
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

    def _record_status_change(self, application, previous, rejection_reason='', rejection_note=''):
        if previous != application.status:
            actor_id, actor_name = self._actor()
            record_security_event(
                self.request.user, 'application.status', application.pk,
                previous=previous, current=application.status,
                **({'rejection_reason': rejection_reason} if rejection_reason else {}),
            )
            summary = f'Moved from {previous.replace("_", " ").title()} to {application.get_status_display()}'
            if application.status == ApplicationStatus.REJECTED and rejection_reason:
                reason_label = dict(RejectionReason.choices).get(rejection_reason, rejection_reason)
                summary = f'{summary} ({reason_label})'
            ApplicationActivity.objects.create(
                application=application,
                kind=ApplicationActivity.Kind.STATUS,
                actor_id=actor_id,
                actor_name=actor_name,
                summary=summary[:255],
                details={
                    'previous': previous,
                    'current': application.status,
                    **({'rejection_reason': rejection_reason} if rejection_reason else {}),
                    **({'rejection_note': rejection_note} if rejection_note else {}),
                },
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

        rejection_reason = str(request.data.get('rejection_reason') or '').strip()
        rejection_note = str(request.data.get('rejection_note') or '').strip()[:500]
        if rejection_reason and rejection_reason not in RejectionReason.values:
            return Response(
                {"error": f"Invalid rejection_reason. Must be one of: {list(RejectionReason.values)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        update_fields = []
        if new_status != previous:
            application.status = new_status
            update_fields.append('status')
        if new_status == ApplicationStatus.REJECTED:
            if rejection_reason:
                application.rejection_reason = rejection_reason
                update_fields.append('rejection_reason')
            if rejection_note:
                application.rejection_note = rejection_note
                update_fields.append('rejection_note')
        elif previous == ApplicationStatus.REJECTED and new_status != ApplicationStatus.REJECTED:
            application.rejection_reason = ''
            application.rejection_note = ''
            update_fields.extend(['rejection_reason', 'rejection_note'])

        if update_fields:
            application.save(update_fields=list(dict.fromkeys(update_fields + ['updated_at'])))
            self._record_status_change(application, previous, rejection_reason, rejection_note)
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
        methods=['patch', 'delete'],
        url_path=r'notes/(?P<note_id>[^/.]+)',
        permission_classes=[IsCompanyRecruiter],
    )
    def delete_note(self, request, pk=None, note_id=None):
        application = self.get_object()
        note = get_object_or_404(application.recruiter_notes, pk=note_id)
        if request.method == 'PATCH':
            if set(request.data) - {'body'}:
                return Response({'detail': 'Unsupported note field.'}, status=status.HTTP_400_BAD_REQUEST)
            serializer = CandidateNoteSerializer(note, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            note = serializer.save()
            actor_id, actor_name = self._actor()
            ApplicationActivity.objects.create(
                application=application,
                kind=ApplicationActivity.Kind.NOTE,
                actor_id=actor_id,
                actor_name=actor_name,
                summary='Updated a private recruiter note',
                details={'note_id': note.pk},
            )
            record_security_event(request.user, 'application.note.update', application.pk, note_id=note.pk)
            return Response(CandidateNoteSerializer(note).data)
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
        methods=['patch', 'delete'],
        url_path=r'tasks/(?P<task_id>[^/.]+)',
        permission_classes=[IsCompanyRecruiter],
    )
    def update_task(self, request, pk=None, task_id=None):
        application = self.get_object()
        task = get_object_or_404(application.recruiter_tasks, pk=task_id)
        if request.method == 'DELETE':
            deleted_title = task.title
            task.delete()
            actor_id, actor_name = self._actor()
            ApplicationActivity.objects.create(
                application=application,
                kind=ApplicationActivity.Kind.TASK,
                actor_id=actor_id,
                actor_name=actor_name,
                summary=f'Deleted task: {deleted_title}',
                details={'task_id': task_id},
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
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
        rejection_reason = str(request.data.get('rejection_reason') or '').strip()
        rejection_note = str(request.data.get('rejection_note') or '').strip()[:500]
        if rejection_reason and rejection_reason not in RejectionReason.values:
            return Response({'detail': 'Choose a valid rejection reason.'}, status=status.HTTP_400_BAD_REQUEST)
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
            update_fields = ['status', 'updated_at']
            if new_status == ApplicationStatus.REJECTED:
                if rejection_reason:
                    application.rejection_reason = rejection_reason
                    update_fields.append('rejection_reason')
                if rejection_note:
                    application.rejection_note = rejection_note
                    update_fields.append('rejection_note')
            elif previous == ApplicationStatus.REJECTED:
                application.rejection_reason = ''
                application.rejection_note = ''
                update_fields.extend(['rejection_reason', 'rejection_note'])
            application.save(update_fields=list(dict.fromkeys(update_fields)))
            self._record_status_change(application, previous, rejection_reason, rejection_note)
        enriched = self.get_queryset().filter(pk__in=application_ids)
        return Response(RecruiterApplicationSerializer(enriched, many=True).data)

    @action(detail=True, methods=['patch'], url_path='tags', permission_classes=[IsCompanyRecruiter])
    @transaction.atomic
    def tags(self, request, pk=None):
        application = self._lock_application()
        raw_tags = request.data.get('tags')
        if not isinstance(raw_tags, list):
            return Response({'detail': 'Tags must be provided as a list.'}, status=status.HTTP_400_BAD_REQUEST)
        cleaned = []
        seen = set()
        for item in raw_tags:
            tag = str(item or '').strip()[:40]
            key = tag.lower()
            if tag and key not in seen:
                seen.add(key)
                cleaned.append(tag)
            if len(cleaned) >= 10:
                break
        application.tags = cleaned
        application.save(update_fields=['tags', 'updated_at'])
        actor_id, actor_name = self._actor()
        ApplicationActivity.objects.create(
            application=application,
            kind=ApplicationActivity.Kind.TAG,
            actor_id=actor_id,
            actor_name=actor_name,
            summary=f'Updated tags: {", ".join(cleaned) if cleaned else "cleared"}',
            details={'tags': cleaned},
        )
        return Response(self.get_serializer(application).data)

    @action(detail=False, methods=['get'], url_path='tasks', permission_classes=[IsCompanyRecruiter])
    def global_tasks(self, request):
        now = timezone.now()
        qs = scope_recruiter_records(
            RecruiterTask.objects.select_related('application', 'application__job').all(),
            request.user,
            'application__job__company_id',
        ).exclude(application__job__status=JobStatus.ARCHIVED)

        task_status = (request.query_params.get('status') or 'open').lower()
        if task_status == 'open':
            qs = qs.filter(completed_at__isnull=True)
        elif task_status == 'completed':
            qs = qs.filter(completed_at__isnull=False)
        elif task_status == 'overdue':
            qs = qs.filter(completed_at__isnull=True, due_at__lt=now)

        priority = (request.query_params.get('priority') or '').upper()
        if priority in RecruiterTask.Priority.values:
            qs = qs.filter(priority=priority)

        assigned = (request.query_params.get('assigned_to') or 'all').lower()
        if assigned == 'me':
            actor_id, _ = self._actor()
            qs = qs.filter(assigned_to=actor_id)

        job_id = request.query_params.get('job_id')
        if job_id and str(job_id).isdigit():
            qs = qs.filter(application__job_id=int(job_id))

        all_open = scope_recruiter_records(
            RecruiterTask.objects.filter(completed_at__isnull=True).exclude(
                application__job__status=JobStatus.ARCHIVED
            ),
            request.user,
            'application__job__company_id',
        )
        today_end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        return Response({
            'summary': {
                'open': all_open.count(),
                'overdue': all_open.filter(due_at__lt=now).count(),
                'due_today': all_open.filter(due_at__gte=now, due_at__lte=today_end).count(),
            },
            'tasks': GlobalRecruiterTaskSerializer(qs[:200], many=True).data,
        })

    @action(detail=False, methods=['get'], url_path='analytics', permission_classes=[IsCompanyRecruiter])
    def analytics(self, request):
        now = timezone.now()
        apps = list(
            scope_recruiter_records(
                Application.objects.select_related('job', 'resume', 'match_result').all(),
                request.user,
                'job__company_id',
            ).exclude(job__status=JobStatus.ARCHIVED)
        )
        total = len(apps)
        stage_counts = {stage: 0 for stage in ApplicationStatus.values}
        band_counts = {'Strong': 0, 'Good': 0, 'Partial': 0, 'Weak': 0, 'Pending': 0}
        rejection_breakdown = {}
        high_match_unreviewed = []
        stale_in_review = []
        failed_parses = []
        review_latencies_hours = []
        stale_cutoff = now - timedelta(days=5)

        first_activities = dict(
            ApplicationActivity.objects.filter(application_id__in=[a.id for a in apps])
            .values('application_id')
            .annotate(first_at=Max('created_at'))
            .values_list('application_id', 'first_at')
        )

        for app in apps:
            stage_counts[app.status] = stage_counts.get(app.status, 0) + 1
            match = getattr(app, 'match_result', None)
            score = match.match_score if match else None
            if score is None:
                band_counts['Pending'] += 1
            elif score >= 85:
                band_counts['Strong'] += 1
            elif score >= 70:
                band_counts['Good'] += 1
            elif score >= 50:
                band_counts['Partial'] += 1
            else:
                band_counts['Weak'] += 1

            if app.status == ApplicationStatus.REJECTED and app.rejection_reason:
                rejection_breakdown[app.rejection_reason] = rejection_breakdown.get(app.rejection_reason, 0) + 1

            if app.status in (ApplicationStatus.SUBMITTED, ApplicationStatus.PROCESSING) and score is not None and score >= 80:
                high_match_unreviewed.append({
                    'id': app.id,
                    'candidate_code': app.candidate_code,
                    'job_id': app.job_id,
                    'job_title': app.job.title,
                    'match_score': round(score),
                    'applied_at': app.applied_at,
                })

            if app.status == ApplicationStatus.UNDER_REVIEW and app.updated_at < stale_cutoff:
                stale_in_review.append({
                    'id': app.id,
                    'candidate_code': app.candidate_code,
                    'job_id': app.job_id,
                    'job_title': app.job.title,
                    'days_waiting': max(1, (now - app.updated_at).days),
                })

            resume = getattr(app, 'resume', None)
            if resume and resume.processing_status == 'FAILED':
                failed_parses.append({
                    'id': app.id,
                    'candidate_code': app.candidate_code,
                    'job_id': app.job_id,
                    'job_title': app.job.title,
                })

            first_act = first_activities.get(app.id)
            if first_act and first_act >= app.applied_at:
                review_latencies_hours.append((first_act - app.applied_at).total_seconds() / 3600.0)

        overdue_tasks = scope_recruiter_records(
            RecruiterTask.objects.filter(completed_at__isnull=True, due_at__lt=now)
            .exclude(application__job__status=JobStatus.ARCHIVED),
            request.user,
            'application__job__company_id',
        ).count()

        funnel = [
            {'stage': 'SUBMITTED', 'label': 'Applied', 'count': total},
            {'stage': 'UNDER_REVIEW', 'label': 'Reviewed', 'count': sum(stage_counts[s] for s in ('UNDER_REVIEW', 'SHORTLISTED', 'INTERVIEW', 'HIRED', 'REJECTED'))},
            {'stage': 'SHORTLISTED', 'label': 'Shortlisted', 'count': sum(stage_counts[s] for s in ('SHORTLISTED', 'INTERVIEW', 'HIRED'))},
            {'stage': 'INTERVIEW', 'label': 'Interview', 'count': sum(stage_counts[s] for s in ('INTERVIEW', 'HIRED'))},
            {'stage': 'HIRED', 'label': 'Hired', 'count': stage_counts['HIRED']},
        ]
        for step in funnel:
            step['conversion_percent'] = round((step['count'] / total) * 100, 1) if total > 0 else 0.0

        return Response({
            'total_applications': total,
            'stage_counts': stage_counts,
            'funnel': funnel,
            'score_distribution': band_counts,
            'median_review_hours': round(statistics.median(review_latencies_hours), 1) if review_latencies_hours else None,
            'rejection_reasons': [
                {'reason': code, 'label': dict(RejectionReason.choices).get(code, code), 'count': count}
                for code, count in sorted(rejection_breakdown.items(), key=lambda pair: -pair[1])
            ],
            'needs_attention': {
                'high_match_unreviewed': sorted(high_match_unreviewed, key=lambda x: -x['match_score'])[:8],
                'stale_in_review': sorted(stale_in_review, key=lambda x: -x['days_waiting'])[:8],
                'failed_parses': failed_parses[:8],
                'overdue_tasks_count': overdue_tasks,
            },
        })

    @action(detail=False, methods=['get'], url_path='talent-pool', permission_classes=[IsCompanyRecruiter])
    def talent_pool(self, request):
        query = (request.query_params.get('q') or '').strip().lower()
        min_score_raw = request.query_params.get('min_score')
        min_exp_raw = request.query_params.get('min_exp')
        tag_filter = (request.query_params.get('tag') or '').strip().lower()

        qs = scope_recruiter_records(
            Application.objects.select_related('job', 'resume', 'match_result').all(),
            request.user,
            'job__company_id',
        ).order_by('-match_result__match_score', '-applied_at')

        if min_score_raw:
            try:
                qs = qs.filter(match_result__match_score__gte=float(min_score_raw))
            except ValueError:
                pass
        if min_exp_raw:
            try:
                qs = qs.filter(resume__extracted_experience_years__gte=float(min_exp_raw))
            except ValueError:
                pass

        results = []
        for app in qs[:300]:
            resume = getattr(app, 'resume', None)
            skills = list(resume.extracted_skills or []) if resume else []
            education = list(resume.extracted_education or []) if resume else []
            tags = list(app.tags or [])
            if tag_filter and not any(t.lower() == tag_filter for t in tags):
                continue
            if query:
                haystack = ' '.join([
                    app.candidate_code,
                    app.job.title if app.job else '',
                    app.job.department if app.job else '',
                    ' '.join(str(s) for s in skills),
                    ' '.join(str(e) for e in education),
                    ' '.join(str(t) for t in tags),
                ]).lower()
                if query not in haystack:
                    continue
            results.append(app)
            if len(results) >= 100:
                break

        return Response(RecruiterApplicationSerializer(results, many=True).data)

    @action(detail=True, methods=['get', 'post'], url_path='interviews', permission_classes=[IsCompanyRecruiter])
    def interviews(self, request, pk=None):
        application = self.get_object()
        if request.method == 'GET':
            return Response(InterviewScheduleSerializer(application.interviews.all(), many=True).data)

        serializer = InterviewScheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor_id, actor_name = self._actor()
        interview = serializer.save(
            application=application,
            created_by=actor_id,
            created_by_name=actor_name,
        )
        ApplicationActivity.objects.create(
            application=application,
            kind=ApplicationActivity.Kind.INTERVIEW,
            actor_id=actor_id,
            actor_name=actor_name,
            summary=f'Scheduled interview: {interview.title}',
            details={'interview_id': interview.pk, 'scheduled_at': interview.scheduled_at.isoformat(), 'mode': interview.mode},
        )
        return Response(InterviewScheduleSerializer(interview).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=['patch', 'delete'],
        url_path=r'interviews/(?P<interview_id>[^/.]+)',
        permission_classes=[IsCompanyRecruiter],
    )
    def interview_detail(self, request, pk=None, interview_id=None):
        application = self.get_object()
        interview = get_object_or_404(application.interviews, pk=interview_id)
        if request.method == 'DELETE':
            title = interview.title
            interview.delete()
            actor_id, actor_name = self._actor()
            ApplicationActivity.objects.create(
                application=application,
                kind=ApplicationActivity.Kind.INTERVIEW,
                actor_id=actor_id,
                actor_name=actor_name,
                summary=f'Removed interview: {title}',
                details={'interview_id': interview_id},
            )
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = InterviewScheduleSerializer(interview, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        interview = serializer.save()
        actor_id, actor_name = self._actor()
        ApplicationActivity.objects.create(
            application=application,
            kind=ApplicationActivity.Kind.INTERVIEW,
            actor_id=actor_id,
            actor_name=actor_name,
            summary=f'Updated interview ({interview.get_status_display()}): {interview.title}',
            details={'interview_id': interview.pk, 'status': interview.status},
        )
        return Response(InterviewScheduleSerializer(interview).data)

    @action(
        detail=True,
        methods=['get'],
        url_path=r'interviews/(?P<interview_id>[^/.]+)/ics',
        permission_classes=[IsCompanyRecruiter],
    )
    def interview_ics(self, request, pk=None, interview_id=None):
        application = self.get_object()
        interview = get_object_or_404(application.interviews, pk=interview_id)
        start_utc = interview.scheduled_at.astimezone(dt_timezone.utc)
        end_utc = start_utc + timedelta(minutes=interview.duration_minutes)
        fmt = '%Y%m%dT%H%M%SZ'
        escape = lambda val: str(val or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')
        ics_lines = [
            'BEGIN:VCALENDAR',
            'VERSION:2.0',
            'PRODID:-//TalentMatch//Recruiter Scheduling//EN',
            'BEGIN:VEVENT',
            f'UID:tm-interview-{interview.pk}@talentmatch',
            f'DTSTAMP:{timezone.now().astimezone(dt_timezone.utc).strftime(fmt)}',
            f'DTSTART:{start_utc.strftime(fmt)}',
            f'DTEND:{end_utc.strftime(fmt)}',
            f'SUMMARY:{escape(f"{interview.title} - {application.candidate_code} ({application.job.title})")}',
            f'LOCATION:{escape(interview.meeting_link or interview.get_mode_display())}',
            f'DESCRIPTION:{escape(interview.notes or f"Candidate {application.candidate_code} interview for {application.job.title}.")}',
            'END:VEVENT',
            'END:VCALENDAR',
        ]
        response = HttpResponse('\r\n'.join(ics_lines) + '\r\n', content_type='text/calendar; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="interview-{application.candidate_code}-{interview.pk}.ics"'
        response['Cache-Control'] = 'private, no-store'
        return response

    @action(detail=True, methods=['get', 'post'], url_path='scorecards', permission_classes=[IsCompanyRecruiter])
    def scorecards(self, request, pk=None):
        application = self.get_object()
        if request.method == 'GET':
            return Response(InterviewScorecardSerializer(application.scorecards.all(), many=True).data)
        serializer = InterviewScorecardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        actor_id, actor_name = self._actor()
        scorecard = serializer.save(
            application=application,
            evaluator_id=actor_id,
            evaluator_name=actor_name,
        )
        ApplicationActivity.objects.create(
            application=application,
            kind=ApplicationActivity.Kind.SCORECARD,
            actor_id=actor_id,
            actor_name=actor_name,
            summary=f'Submitted scorecard ({scorecard.overall_rating}/5 · {scorecard.get_recommendation_display()})',
            details={'scorecard_id': scorecard.pk, 'overall_rating': scorecard.overall_rating, 'recommendation': scorecard.recommendation},
        )
        return Response(InterviewScorecardSerializer(scorecard).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=['patch', 'delete'],
        url_path=r'scorecards/(?P<scorecard_id>[^/.]+)',
        permission_classes=[IsCompanyRecruiter],
    )
    def scorecard_detail(self, request, pk=None, scorecard_id=None):
        application = self.get_object()
        scorecard = get_object_or_404(application.scorecards, pk=scorecard_id)
        if request.method == 'DELETE':
            scorecard.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = InterviewScorecardSerializer(scorecard, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        scorecard = serializer.save()
        return Response(InterviewScorecardSerializer(scorecard).data)

    @action(detail=False, methods=['post'], url_path='report', permission_classes=[IsCompanyRecruiter])
    def export_report(self, request):
        options = AllApplicationsReportRequestSerializer(data=request.data)
        options.is_valid(raise_exception=True)
        file_type = options.validated_data['file_type']
        base_queryset = scope_recruiter_records(
            Application.objects.all(), request.user, 'job__company_id'
        ).exclude(job__status=JobStatus.ARCHIVED)
        report = build_applications_report(
            base_queryset,
            date_from=options.validated_data.get('date_from'),
            date_to=options.validated_data.get('date_to'),
            status=options.validated_data.get('status'),
            match_band=options.validated_data.get('match_band') or 'ALL',
            job_id=options.validated_data.get('job_id'),
        )
        if file_type == 'pdf':
            payload, mime_type = render_applications_pdf(report), PDF_MIME
        else:
            payload, mime_type = render_applications_excel(report), EXCEL_MIME

        filename = applications_report_filename(report, file_type)
        record_security_event(
            request.user,
            'application.report_export',
            options.validated_data.get('job_id') or 'all',
            file_type=file_type,
            status=options.validated_data.get('status'),
            match_band=options.validated_data.get('match_band') or 'ALL',
            job_id=options.validated_data.get('job_id'),
            application_count=len(report.rows),
        )
        return HttpResponse(
            payload,
            content_type=mime_type,
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Cache-Control': 'private, no-store',
            },
        )

    @action(detail=False, methods=['get'], url_path='communication-templates', permission_classes=[IsCompanyRecruiter])
    def communication_templates(self, request):
        company_id = recruiter_company_id(request.user)
        if company_id is None:
            return Response([])
        from apps.companies.models import Company
        company = Company.objects.filter(pk=company_id).first()
        if not company:
            return Response([])
        templates = CommunicationTemplate.objects.filter(company=company)
        if not templates.exists():
            seed_default_templates(company, actor_name=getattr(request.user, 'name', 'System') or 'System')
            templates = CommunicationTemplate.objects.filter(company=company)
        return Response(CommunicationTemplateSerializer(templates.order_by('stage', 'name', '-created_at'), many=True).data)

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


class CommunicationTemplateViewSet(viewsets.ModelViewSet):
    """
    Dedicated management viewset for company outreach communication templates.
    Company-scoped: recruiters can only list, create, edit, or delete templates
    belonging to their authorized company profile.
    """
    serializer_class = CommunicationTemplateSerializer
    permission_classes = [IsCompanyRecruiter]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        company_id = recruiter_company_id(user)
        if not is_recruiter(user) or company_id is None:
            return CommunicationTemplate.objects.none()

        from apps.companies.models import Company
        company = Company.objects.filter(pk=company_id).first()
        if not company:
            return CommunicationTemplate.objects.none()

        qs = CommunicationTemplate.objects.filter(company=company)
        if not qs.exists():
            seed_default_templates(company, actor_name=getattr(user, 'name', 'System') or 'System')
            qs = CommunicationTemplate.objects.filter(company=company)

        stage_param = self.request.query_params.get('stage')
        if stage_param:
            qs = qs.filter(stage=stage_param)
        return qs.order_by('stage', 'name', '-created_at')

    def perform_create(self, serializer):
        user = self.request.user
        company_id = recruiter_company_id(user)
        actor_name = getattr(user, 'name', '') or getattr(user, 'email', '') or 'Recruiter'
        template = serializer.save(
            company_id=company_id,
            created_by=actor_name,
            is_default=False,
        )
        record_security_event(
            user,
            'application.communication_template.create',
            template.pk,
            name=template.name,
            stage=template.stage,
        )

    def perform_update(self, serializer):
        user = self.request.user
        template = serializer.save()
        record_security_event(
            user,
            'application.communication_template.update',
            template.pk,
            name=template.name,
            stage=template.stage,
        )

    def perform_destroy(self, instance):
        user = self.request.user
        template_id = instance.pk
        name = instance.name
        stage = instance.stage
        instance.delete()
        record_security_event(
            user,
            'application.communication_template.delete',
            template_id,
            name=name,
            stage=stage,
        )

    @action(detail=False, methods=['post'], url_path='reset-defaults')
    def reset_defaults(self, request):
        user = request.user
        company_id = recruiter_company_id(user)
        from apps.companies.models import Company
        company = get_object_or_404(Company, pk=company_id)
        actor_name = getattr(user, 'name', '') or getattr(user, 'email', '') or 'Recruiter'

        seed_default_templates(company, actor_name=actor_name)
        record_security_event(
            user,
            'application.communication_template.reset_defaults',
            company_id,
        )
        queryset = self.get_queryset()
        return Response(self.get_serializer(queryset, many=True).data)

