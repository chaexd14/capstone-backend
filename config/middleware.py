import uuid
import logging
import time
from django.db import connection
from django.http import HttpResponseNotFound

from config.observability import request_id_context
from config.performance_metrics import record_api_request

logger = logging.getLogger('talentmatch.performance')

NOT_FOUND_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>404 Not Found</title>
</head>
<body>
  <h1>Not Found</h1>
  <p>The requested URL was not found on this server.</p>
</body>
</html>"""


def _not_found_response():
    return HttpResponseNotFound(NOT_FOUND_HTML, content_type="text/html")


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = uuid.uuid4().hex
        request.request_id = request_id
        token = request_id_context.set(request_id)
        try:
            response = self.get_response(request)
            response['X-Request-ID'] = request_id
            return response
        finally:
            request_id_context.reset(token)


class ApiPerformanceMiddleware:
    """Log collection latency and response size without exposing response content."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.perf_counter()
        query_stats = {'count': 0, 'duration_ms': 0.0}

        def track_query(execute, sql, params, many, context):
            query_started = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                query_stats['count'] += 1
                query_stats['duration_ms'] += (time.perf_counter() - query_started) * 1000

        with connection.execute_wrapper(track_query):
            response = self.get_response(request)
        if request.path.startswith('/api/'):
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            content_length = response.get('Content-Length')
            if content_length is None and not getattr(response, 'streaming', False):
                content_length = len(getattr(response, 'content', b''))
            response['Server-Timing'] = f'app;dur={duration_ms}'
            response['X-Request-ID'] = getattr(request, 'request_id', '')
            logger.info(
                'api_request',
                extra={'event': {
                    'method': request.method,
                    'path': request.path,
                    'status': response.status_code,
                    'duration_ms': duration_ms,
                    'db_query_count': query_stats['count'],
                    'db_duration_ms': round(query_stats['duration_ms'], 1),
                    'cache_status': response.get('X-Cache', 'unreported'),
                    'response_bytes': int(content_length or 0),
                }},
            )
            record_api_request(
                path=request.path, status=response.status_code, duration_ms=duration_ms,
                db_query_count=query_stats['count'],
                db_duration_ms=round(query_stats['duration_ms'], 1),
            )
        return response


class ApiAccessSecurityMiddleware:
    """
    Middleware to restrict direct browser URL navigation to backend /api/ endpoints.

    Returns a generic 404 Not Found HTML page if a client attempts to navigate to /api/
    directly via a browser address bar (document navigation), concealing endpoint existence.
    API callers (JSON/REST requests) pass through to Django REST Framework, where
    SupabaseJWTAuthentication and DRF permission classes authoritatively enforce security.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Only apply browser concealment to /api/ endpoints
        if not path.startswith('/api/'):
            return self.get_response(request)

        # Allow CORS preflight requests
        if request.method == 'OPTIONS':
            return self.get_response(request)

        # 1. Check for direct browser navigation (user pasting/typing URL into browser address bar)
        sec_fetch_dest = request.headers.get('Sec-Fetch-Dest', '').lower()
        sec_fetch_mode = request.headers.get('Sec-Fetch-Mode', '').lower()
        accept_header = request.headers.get('Accept', '').lower()

        if sec_fetch_dest == 'document' or sec_fetch_mode == 'navigate':
            return _not_found_response()

        # If a browser requests HTML content directly (not API JSON client)
        if 'text/html' in accept_header and 'application/json' not in accept_header:
            return _not_found_response()

        response = self.get_response(request)
        if path.startswith(('/api/applications/', '/api/resumes/', '/api/notifications/', '/api/auth/')):
            response['Cache-Control'] = 'private, no-store, no-cache, must-revalidate'
            response['Pragma'] = 'no-cache'
        return response
