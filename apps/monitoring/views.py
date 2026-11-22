import secrets
import time
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import SecurityEvent
from apps.companies.management_views import PlatformAdminOnly
from apps.monitoring.models import SystemSnapshot
from apps.resumes.models import ResumeWork, WorkerHeartbeat


class AgentReportSerializer(serializers.Serializer):
    agent_id = serializers.RegexField(r'^[a-zA-Z0-9._-]+$', max_length=64, default='primary-vm')
    cpu_percent = serializers.FloatField(min_value=0, max_value=100)
    memory_percent = serializers.FloatField(min_value=0, max_value=100)
    disk_percent = serializers.FloatField(min_value=0, max_value=100)
    uptime_seconds = serializers.IntegerField(min_value=0)
    api_service_up = serializers.BooleanField()
    worker_service_up = serializers.BooleanField()


def _database_latency():
    started = time.monotonic()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        return round((time.monotonic() - started) * 1000, 1), True
    except Exception:
        return None, False


def _queue_state(now):
    ready = ResumeWork.objects.filter(failed=False, run_after__lte=now)
    oldest = ready.order_by('run_after').values_list('run_after', flat=True).first()
    return {
        'depth': ready.count(),
        'failed': ResumeWork.objects.filter(failed=True).count(),
        'oldest_seconds': max(0, int((now - oldest).total_seconds())) if oldest else 0,
    }


def _health_status(*, available=True, down=False, degraded=False):
    if not available:
        return 'unavailable'
    if down:
        return 'down'
    if degraded:
        return 'degraded'
    return 'healthy'


class MonitoringAgentReportView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        supplied = request.headers.get('X-Monitoring-Agent-Key', '')
        if not settings.MONITORING_AGENT_SECRET or not secrets.compare_digest(
            supplied, settings.MONITORING_AGENT_SECRET
        ):
            return Response({'detail': 'Monitoring agent credentials are invalid.'}, status=status.HTTP_403_FORBIDDEN)
        serializer = AgentReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        now = timezone.now()
        queue = _queue_state(now)
        latency, _ = _database_latency()
        snapshot = SystemSnapshot.objects.create(
            **serializer.validated_data,
            queue_depth=queue['depth'], failed_work=queue['failed'], database_latency_ms=latency,
        )
        SystemSnapshot.objects.filter(recorded_at__lt=now - timedelta(days=7)).delete()
        return Response({'accepted': True, 'recorded_at': snapshot.recorded_at}, status=status.HTTP_202_ACCEPTED)


class PrivateMonitoringView(APIView):
    permission_classes = [PlatformAdminOnly]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class MonitoringOverviewView(PrivateMonitoringView):
    def get(self, request):
        now = timezone.now()
        snapshot = SystemSnapshot.objects.first()
        fresh = bool(snapshot and snapshot.recorded_at >= now - timedelta(seconds=120))
        queue = _queue_state(now)
        db_latency, database_up = _database_latency()
        heartbeat = WorkerHeartbeat.objects.filter(name='resume-worker').first()
        heartbeat_age = max(0, int((now - heartbeat.updated_at).total_seconds())) if heartbeat else None

        vm_status = _health_status(
            available=fresh,
            degraded=bool(fresh and (max(snapshot.cpu_percent, snapshot.memory_percent) >= 80 or snapshot.disk_percent >= 85)),
            down=bool(fresh and not snapshot.api_service_up and not snapshot.worker_service_up),
        )
        api_status = _health_status(
            available=fresh, down=bool(fresh and not snapshot.api_service_up),
            degraded=bool(database_up and db_latency is not None and db_latency >= 750),
        )
        worker_up = fresh and snapshot.worker_service_up and heartbeat_age is not None and heartbeat_age <= 60
        worker_status = _health_status(
            available=fresh or heartbeat is not None,
            down=bool((fresh and not snapshot.worker_service_up) or (heartbeat_age is not None and heartbeat_age > 300)),
            degraded=bool(not worker_up or queue['oldest_seconds'] > 300 or queue['failed'] > 0),
        )
        database_status = _health_status(
            down=not database_up, degraded=bool(db_latency is not None and db_latency >= 750),
        )
        components = [
            {'id': 'frontend', 'name': 'Vercel frontend', 'status': 'unavailable', 'detail': 'Provider telemetry is not connected.' if not settings.VERCEL_MONITORING_TOKEN else 'Provider polling is awaiting its first sample.'},
            {'id': 'api', 'name': 'Django API', 'status': api_status, 'detail': 'VM service is responding.' if api_status == 'healthy' else 'Check the VM agent and API service.'},
            {'id': 'worker', 'name': 'Resume worker', 'status': worker_status, 'detail': f'Heartbeat {heartbeat_age}s ago.' if heartbeat_age is not None else 'No worker heartbeat received.'},
            {'id': 'database', 'name': 'Supabase database', 'status': database_status, 'detail': f'{db_latency:.1f} ms query latency.' if db_latency is not None else 'Database query failed.'},
            {'id': 'storage', 'name': 'Cloudflare R2', 'status': 'unavailable', 'detail': 'Application-observed storage telemetry is not available yet.' if settings.USE_R2_STORAGE else 'R2 storage is not enabled.'},
            {'id': 'ai', 'name': 'Gemini AI', 'status': 'unavailable', 'detail': 'Application-observed AI telemetry is not available yet.' if settings.ENABLE_GEMINI_PROCESSING else 'Gemini processing is disabled.'},
        ]
        actionable = [item for item in components if item['status'] != 'healthy']
        overall = 'down' if any(item['status'] == 'down' for item in components) else 'degraded' if actionable else 'healthy'
        security = list(
            SecurityEvent.objects.filter(created_at__gte=now - timedelta(hours=24))
            .values('action').annotate(count=Count('id')).order_by('-count', 'action')[:8]
        )
        return Response({
            'generated_at': now, 'overall_status': overall, 'active_incidents': len(actionable),
            'components': components,
            'host': {
                'status': vm_status, 'last_reported_at': snapshot.recorded_at if snapshot else None,
                'cpu_percent': snapshot.cpu_percent if fresh else None,
                'memory_percent': snapshot.memory_percent if fresh else None,
                'disk_percent': snapshot.disk_percent if fresh else None,
                'uptime_seconds': snapshot.uptime_seconds if fresh else None,
            },
            'queue': {**queue, 'heartbeat_age_seconds': heartbeat_age},
            'security_events_24h': security,
            'incidents': [
                {'component': item['name'], 'severity': item['status'], 'summary': item['detail']}
                for item in actionable
            ],
        })


class MonitoringTimeseriesView(PrivateMonitoringView):
    def get(self, request):
        range_name = request.query_params.get('range', '24h')
        if range_name not in ('24h', '7d'):
            return Response({'range': 'Choose 24h or 7d.'}, status=status.HTTP_400_BAD_REQUEST)
        since = timezone.now() - (timedelta(hours=24) if range_name == '24h' else timedelta(days=7))
        rows = SystemSnapshot.objects.filter(recorded_at__gte=since).order_by('recorded_at')
        limit = 288 if range_name == '24h' else 336
        step = max(1, (rows.count() + limit - 1) // limit)
        points = [
            {
                'recorded_at': row.recorded_at, 'cpu_percent': row.cpu_percent,
                'memory_percent': row.memory_percent, 'disk_percent': row.disk_percent,
                'queue_depth': row.queue_depth, 'failed_work': row.failed_work,
                'database_latency_ms': row.database_latency_ms,
            }
            for index, row in enumerate(rows.iterator()) if index % step == 0
        ]
        return Response({'range': range_name, 'points': points})


class MonitoringFailedWorkView(PrivateMonitoringView):
    """Lists failed ResumeWork items so Super Admins can inspect and retry stuck resume jobs."""

    def get(self, request):
        items = ResumeWork.objects.filter(failed=True).order_by('run_after')[:100]
        return Response({
            'count': ResumeWork.objects.filter(failed=True).count(),
            'items': [
                {
                    'id': item.pk,
                    'kind': item.kind,
                    'target': item.target,
                    'attempts': item.attempts,
                    'run_after': item.run_after,
                }
                for item in items
            ],
        })


class MonitoringFailedWorkRetryView(PrivateMonitoringView):
    """Re-queues a single failed ResumeWork item for immediate worker pickup."""

    def post(self, request, pk):
        from apps.accounts.models import record_security_event
        from django.shortcuts import get_object_or_404

        work = get_object_or_404(ResumeWork, pk=pk, failed=True)
        work.failed = False
        work.attempts = 0
        work.run_after = timezone.now()
        work.save(update_fields=['failed', 'attempts', 'run_after'])
        record_security_event(request.user, 'monitoring.work.retry', work.pk, kind=work.kind)
        return Response({'retried': True, 'id': work.pk})


class MonitoringSecurityEventsView(PrivateMonitoringView):
    """Redacted, searchable platform security event log (excludes actor PII and candidate details)."""

    def get(self, request):
        action_filter = (request.query_params.get('action') or '').strip()
        hours_raw = request.query_params.get('hours', '24')
        try:
            hours = max(1, min(168, int(hours_raw)))
        except ValueError:
            hours = 24

        since = timezone.now() - timedelta(hours=hours)
        qs = SecurityEvent.objects.filter(created_at__gte=since).order_by('-created_at')
        if action_filter:
            qs = qs.filter(action__icontains=action_filter)

        actions = list(
            SecurityEvent.objects.filter(created_at__gte=since)
            .values_list('action', flat=True)
            .distinct()
            .order_by('action')
        )
        rows = qs[:100]
        return Response({
            'hours': hours,
            'total': qs.count(),
            'available_actions': actions,
            'events': [
                {
                    'id': row.pk,
                    'action': row.action,
                    'object_id': row.object_id,
                    'created_at': row.created_at,
                }
                for row in rows
            ],
        })
