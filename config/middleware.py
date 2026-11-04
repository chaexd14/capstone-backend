import os
from django.conf import settings
from django.http import HttpResponseNotFound

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

        # Allow resume download endpoint to reach view where authentication, permissions,
        # and browser redirect to signed storage URLs are handled.
        if path.startswith('/api/resumes/') and path.endswith('/download/'):
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
        if path.startswith('/api/applications/track-status'):
            response['Cache-Control'] = 'private, no-store, no-cache, must-revalidate'
            response['Pragma'] = 'no-cache'
        return response
