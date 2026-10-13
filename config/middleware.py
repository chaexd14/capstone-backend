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
    Middleware to restrict direct browser URL navigation and unauthorized access
    to backend /api/ endpoints.

    Returns a generic 404 Not Found response for any direct browser navigation
    or unauthorized requests, completely concealing endpoint existence.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.expected_client_key = getattr(
            settings, 'APP_CLIENT_KEY', os.getenv('APP_CLIENT_KEY', 'talentmatch-client-key-2026')
        )

    def __call__(self, request):
        path = request.path

        # Only apply restrictions to /api/ endpoints
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

        # If a browser requests HTML content directly
        if 'text/html' in accept_header and 'application/json' not in accept_header:
            return _not_found_response()

        # 2. Check for application verification header
        client_key = request.headers.get('X-App-Client-Key')
        requested_with = request.headers.get('X-Requested-With')

        # Both the client key and requested_with must be valid
        if not client_key or client_key != self.expected_client_key:
            return _not_found_response()

        if not requested_with or requested_with.lower() != 'xmlhttprequest':
            return _not_found_response()

        return self.get_response(request)
