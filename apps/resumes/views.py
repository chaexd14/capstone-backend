import logging
import ipaddress
from django.conf import settings
from django.http import HttpResponseRedirect
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.resumes.models import Resume
from apps.resumes.serializers import (
    ResumeSerializer,
    ApplicantResumeSerializer,
    RecruiterResumeSerializer,
)
from config.permissions import IsResumeOwnerOrRecruiter

from django.db.models import Q

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger('security.audit')


def _parse_ip(ip_str):
    if not ip_str:
        return None
    cleaned = str(ip_str).strip()
    if not cleaned:
        return None
    if ':' in cleaned and not cleaned.startswith('['):
        if cleaned.count(':') == 1:
            cleaned = cleaned.split(':')[0]
    elif cleaned.startswith('[') and ']' in cleaned:
        cleaned = cleaned[1:cleaned.index(']')]
    try:
        return ipaddress.ip_address(cleaned)
    except (ValueError, TypeError):
        return None


def _is_ip_in_trusted_list(ip_obj, trusted_list):
    if not ip_obj or not trusted_list:
        return False
    for entry in trusted_list:
        try:
            network = ipaddress.ip_network(str(entry).strip(), strict=False)
            if ip_obj in network:
                return True
        except (ValueError, TypeError):
            continue
    return False


def get_trusted_client_ip(request):
    """
    Safely resolves the true client IP using secure right-to-left traversal of X-Forwarded-For.
    Only trusts X-Forwarded-For if REMOTE_ADDR matches an explicitly configured trusted proxy
    IP address or CIDR range. Prevents IP spoofing where untrusted clients prepend fake IPs.
    """
    remote_addr_str = request.META.get('REMOTE_ADDR', '').strip()
    remote_ip = _parse_ip(remote_addr_str)
    trusted_proxies = getattr(settings, 'TRUSTED_PROXY_IPS', ['127.0.0.1', '::1'])

    if not remote_ip or not _is_ip_in_trusted_list(remote_ip, trusted_proxies):
        return remote_addr_str

    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if not x_forwarded_for:
        return remote_addr_str

    parts = [p.strip() for p in x_forwarded_for.split(',') if p.strip()]
    if not parts:
        return remote_addr_str

    # Walk from rightmost upstream proxy backwards to find the first untrusted IP
    resolved_ip = parts[-1]
    for part in reversed(parts):
        ip_obj = _parse_ip(part)
        if not ip_obj:
            resolved_ip = part
            break
        if not _is_ip_in_trusted_list(ip_obj, trusted_proxies):
            resolved_ip = str(ip_obj)
            break
        resolved_ip = str(ip_obj)

    return resolved_ip


def get_signed_url_expiration(storage=None):
    """
    Unified source of truth for pre-signed URL expiration (in seconds).
    """
    if storage and hasattr(storage, 'querystring_expire'):
        try:
            return int(storage.querystring_expire)
        except (ValueError, TypeError):
            pass
    return int(getattr(settings, 'R2_QUERYSTRING_EXPIRE', 300))


def client_prefers_json(request):
    """
    Evaluates whether the client prefers a JSON response over an HTTP redirect.
    Preserves explicit ?format=json and ?redirect=false parameters.
    Evaluates HTTP Accept header quality values (q-factor) properly rather
    than performing an insecure simple substring match.
    """
    if request.query_params.get('redirect', '').lower() in ('false', '0'):
        return True
    if request.query_params.get('format') == 'json':
        return True

    accept_header = request.headers.get('Accept', '')
    if not accept_header:
        return False

    json_q = -1.0
    html_q = -1.0

    for part in accept_header.split(','):
        part = part.strip()
        if not part:
            continue
        params = part.split(';')
        media_type = params[0].strip().lower()
        q = 1.0
        for p in params[1:]:
            p = p.strip()
            if p.startswith('q='):
                try:
                    q = float(p[2:].strip())
                except ValueError:
                    q = 0.0

        if media_type == 'application/json':
            json_q = max(json_q, q)
        elif media_type in ('text/html', 'application/xhtml+xml'):
            html_q = max(html_q, q)

    # If application/json was requested with higher preference than HTML
    if json_q > 0.0 and json_q >= html_q:
        return True

    return False


from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.exceptions import NotAcceptable


class ResumeContentNegotiation(DefaultContentNegotiation):
    """
    Custom content negotiation for resume endpoints.
    Ensures explicit ?format=json and ?redirect=false are honored even if
    the HTTP Accept header specifies HTML, and falls back gracefully to allow
    browser redirect downloads without throwing HTTP 406 NotAcceptable.
    """
    def select_renderer(self, request, renderers, format_suffix=None):
        if request.query_params.get('format') == 'json' or request.query_params.get('redirect', '').lower() in ('false', '0'):
            for r in renderers:
                if getattr(r, 'format', None) == 'json':
                    return (r, 'application/json')
        try:
            return super().select_renderer(request, renderers, format_suffix)
        except NotAcceptable:
            if renderers:
                return (renderers[0], renderers[0].media_type)
            raise


class ResumeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ResumeSerializer
    content_negotiation_class = ResumeContentNegotiation
    permission_classes = [IsResumeOwnerOrRecruiter]

    def get_serializer_class(self):
        user = getattr(self.request, 'user', None)
        if not user or not user.is_authenticated:
            return ApplicantResumeSerializer
        role = getattr(user, 'role', '').lower()
        if role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser:
            return RecruiterResumeSerializer
        return ApplicantResumeSerializer

    def get_permissions(self):
        return [IsAuthenticated(), IsResumeOwnerOrRecruiter()]

    def get_queryset(self):
        user = self.request.user
        queryset = Resume.objects.select_related('application').all()

        if not user or not user.is_authenticated:
            return queryset.none()

        role = getattr(user, 'role', '').lower()
        is_recruiter = role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser

        if is_recruiter:
            return queryset

        # Applicants are strictly restricted to their own resumes
        user_uuid = getattr(user, 'supabase_id', str(user.username))
        return queryset.filter(application__user_id=user_uuid)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        resume = self.get_object()
        user = request.user
        user_identifier = getattr(user, 'supabase_id', str(getattr(user, 'username', 'anonymous')))
        user_role = getattr(user, 'role', 'authenticated_user')
        app_id = getattr(resume, 'application_id', None)
        client_ip = get_trusted_client_ip(request)

        def _no_cache(response_obj):
            response_obj['Cache-Control'] = 'private, no-store, no-cache, must-revalidate'
            response_obj['Pragma'] = 'no-cache'
            return response_obj

        if not resume.file:
            try:
                audit_logger.warning(
                    f"[AUDIT:RESUME_DOWNLOAD] user_id={user_identifier} role={user_role} "
                    f"resume_id={resume.id} application_id={app_id} outcome=FILE_PURGED"
                )
            except Exception as log_err:
                logger.error("Audit logger failure on purged resume download: %s", type(log_err).__name__)

            return _no_cache(Response(
                {'error': 'Resume file has been deleted or purged in accordance with candidate data privacy policy.'},
                status=status.HTTP_410_GONE
            ))

        try:
            url = resume.file.url
        except Exception as e:
            logger.error(
                "[R2 STORAGE ERROR] Failed to generate signed URL for resume_id=%s: error_type=%s",
                resume.id,
                type(e).__name__
            )
            try:
                audit_logger.warning(
                    f"[AUDIT:RESUME_DOWNLOAD] user_id={user_identifier} role={user_role} "
                    f"resume_id={resume.id} application_id={app_id} outcome=STORAGE_ERROR"
                )
            except Exception as log_err:
                logger.error("Audit logger failure on storage error: %s", type(log_err).__name__)

            return _no_cache(Response(
                {'error': 'An internal storage error occurred while preparing your file download. Please try again later.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            ))

        # Audit successful authorization and signed URL generation (without leaking signature, secrets, or raw URL)
        # Outcome is accurately recorded as SIGNED_URL_ISSUED (not file download completed)
        try:
            audit_logger.info(
                f"[AUDIT:RESUME_DOWNLOAD] user_id={user_identifier} role={user_role} "
                f"resume_id={resume.id} application_id={app_id} outcome=SIGNED_URL_ISSUED "
                f"client_ip={client_ip}"
            )
        except Exception as log_err:
            logger.error("Audit logger failure on signed URL issuance: %s", type(log_err).__name__)

        expires_in = get_signed_url_expiration(getattr(resume.file, 'storage', None))

        # Robust content negotiation: Support application/json via query param, format param, or evaluated Accept header
        if client_prefers_json(request):
            return _no_cache(Response({'download_url': url, 'expires_in': expires_in}, status=status.HTTP_200_OK))

        return _no_cache(HttpResponseRedirect(url))
