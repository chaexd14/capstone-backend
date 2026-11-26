import logging
import threading
import time

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse


logger = logging.getLogger(__name__)
MIGRATION_CHECK_TTL_SECONDS = 60
_migration_check_lock = threading.Lock()
_migration_check_cache = {'checked_at': 0.0, 'pending': None}


def liveness(request):
    return JsonResponse({'status': 'alive', 'service': 'TalentMatch API'})


def _pending_migration_count():
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    return len(executor.migration_plan(targets))


def _cached_pending_migration_count():
    now = time.monotonic()
    if now - _migration_check_cache['checked_at'] < MIGRATION_CHECK_TTL_SECONDS:
        return _migration_check_cache['pending']

    with _migration_check_lock:
        now = time.monotonic()
        if now - _migration_check_cache['checked_at'] < MIGRATION_CHECK_TTL_SECONDS:
            return _migration_check_cache['pending']
        pending = _pending_migration_count()
        _migration_check_cache.update(checked_at=now, pending=pending)
        return pending


def _clear_migration_check_cache():
    """Reset cached migration state for tests and explicit operational refreshes."""
    _migration_check_cache.update(checked_at=0.0, pending=None)


def readiness(request):
    checks = {'database': 'ok', 'migrations': 'ok', 'configuration': 'ok'}
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception as exc:
        checks['database'] = 'unavailable'
        logger.error('Readiness database check failed. error=%s', type(exc).__name__)

    if checks['database'] == 'ok':
        try:
            pending = _cached_pending_migration_count()
            if pending:
                checks['migrations'] = f'{pending}_pending'
        except Exception as exc:
            checks['migrations'] = 'unknown'
            logger.error('Readiness migration check failed. error=%s', type(exc).__name__)

    if not settings.DEBUG:
        required = (
            settings.SECRET_KEY,
            settings.SUPABASE_URL,
            settings.ALLOWED_HOSTS,
            settings.CORS_ALLOWED_ORIGINS,
        )
        if not all(required):
            checks['configuration'] = 'incomplete'

    ready = all(value == 'ok' for value in checks.values())
    return JsonResponse(
        {'status': 'ready' if ready else 'unready', 'checks': checks},
        status=200 if ready else 503,
    )
