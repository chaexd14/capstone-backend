import base64
import logging
import os
import ipaddress
from django.conf import settings
from django.http import FileResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.accounts.models import record_security_event
from apps.resumes.models import Resume
from apps.resumes.serializers import (
    ResumeSerializer,
    ApplicantResumeSerializer,
    RecruiterResumeSerializer,
)
from config.permissions import (
    IsResumeOwnerOrRecruiter, is_recruiter, scope_recruiter_records,
)

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


class ResumeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ResumeSerializer
    permission_classes = [IsResumeOwnerOrRecruiter]

    def get_serializer_class(self):
        user = getattr(self.request, 'user', None)
        if not user or not user.is_authenticated:
            return ApplicantResumeSerializer
        if is_recruiter(user):
            return RecruiterResumeSerializer
        return ApplicantResumeSerializer

    def get_permissions(self):
        return [IsAuthenticated(), IsResumeOwnerOrRecruiter()]

    def get_queryset(self):
        user = self.request.user
        queryset = Resume.objects.select_related('application').all()

        if not user or not user.is_authenticated:
            return queryset.none()

        if is_recruiter(user):
            return scope_recruiter_records(queryset, user, 'application__job__company_id')

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

        if not resume.file:
            try:
                audit_logger.warning(
                    f"[AUDIT:RESUME_DOWNLOAD] user_id={user_identifier} role={user_role} "
                    f"resume_id={resume.id} application_id={app_id} outcome=FILE_PURGED"
                )
            except Exception as log_err:
                logger.error("Audit logger failure on purged resume download: %s", type(log_err).__name__)

            return Response(
                {'error': 'Resume file has been deleted or purged in accordance with candidate data privacy policy.'},
                status=status.HTTP_410_GONE
            )

        if settings.REQUIRE_RESUME_SCAN and not resume.scan_passed:
            return Response(
                {'error': 'This resume has not passed upload security checks.'},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            file = resume.file.open('rb')
            try:
                response = FileResponse(
                    file, as_attachment=True, filename=resume.original_filename,
                    content_type='application/octet-stream',
                )
            except Exception:
                file.close()
                raise
        except FileNotFoundError:
            return Response({'error': 'Resume file is unavailable.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            logger.error("Unable to open resume_id=%s: error_type=%s", resume.id, type(exc).__name__)
            return Response(
                {'error': 'An internal storage error occurred while preparing your file download. Please try again later.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            record_security_event(user, 'resume.download', resume.pk, application_id=app_id)
        except Exception:
            response.close()
            raise

        try:
            audit_logger.info(
                f"[AUDIT:RESUME_DOWNLOAD] user_id={user_identifier} role={user_role} "
                f"resume_id={resume.id} application_id={app_id} outcome=FILE_PREPARED "
                f"client_ip={client_ip}"
            )
        except Exception as log_err:
            logger.error("Audit logger failure on resume download: %s", type(log_err).__name__)

        response['Referrer-Policy'] = 'no-referrer'
        response['X-Content-Type-Options'] = 'nosniff'
        return response

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        """
        Renders an authenticated, CSP-safe document preview for recruiter/owner inspection.
        For PDF resumes, renders up to 10 pages at 150 DPI into base64 PNG data URLs
        via PyMuPDF so the frontend can display crisp page sheets using standard <img>
        tags (avoiding browser <iframe> / <embed> CSP blocks).
        """
        resume = self.get_object()
        user = request.user
        user_identifier = getattr(user, 'supabase_id', str(getattr(user, 'username', 'anonymous')))
        user_role = getattr(user, 'role', 'authenticated_user')
        app_id = getattr(resume, 'application_id', None)
        client_ip = get_trusted_client_ip(request)

        if not resume.file:
            return Response(
                {'error': 'Resume file has been deleted or purged in accordance with candidate data privacy policy.'},
                status=status.HTTP_410_GONE,
            )

        if settings.REQUIRE_RESUME_SCAN and not resume.scan_passed:
            return Response(
                {'error': 'This resume has not passed upload security checks.'},
                status=status.HTTP_409_CONFLICT,
            )

        filename = (resume.original_filename or resume.file.name or '').lower()
        _, ext = os.path.splitext(filename)
        ext = ext.lstrip('.') or 'pdf'

        if ext != 'pdf':
            record_security_event(user, 'resume.preview', resume.pk, application_id=app_id)
            return Response({
                'format': ext,
                'page_count': 1,
                'rendered_pages': 0,
                'pages': [],
            })

        max_preview_bytes = 10 * 1024 * 1024
        max_preview_pages = 10

        try:
            with resume.file.open('rb') as file_obj:
                pdf_bytes = file_obj.read(max_preview_bytes + 1)
        except FileNotFoundError:
            return Response({'error': 'Resume file is unavailable.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:
            logger.error("Unable to open resume_id=%s for preview: error_type=%s", resume.id, type(exc).__name__)
            return Response(
                {'error': 'Unable to read resume file for preview.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if len(pdf_bytes) > max_preview_bytes:
            return Response(
                {'error': 'Resume file exceeds inline preview size limit.'},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

        try:
            import pymupdf

            pages_payload = []
            with pymupdf.open(stream=pdf_bytes, filetype='pdf') as doc:
                if doc.needs_pass:
                    return Response(
                        {'error': 'Encrypted PDF cannot be previewed inline.'},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )
                total_pages = doc.page_count
                render_count = min(total_pages, max_preview_pages)
                matrix = pymupdf.Matrix(150.0 / 72.0, 150.0 / 72.0)

                for idx in range(render_count):
                    page = doc.load_page(idx)
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    png_bytes = pix.tobytes('png')
                    b64_str = base64.b64encode(png_bytes).decode('ascii')
                    pages_payload.append({
                        'page_number': idx + 1,
                        'width': pix.width,
                        'height': pix.height,
                        'data_url': f'data:image/png;base64,{b64_str}',
                    })
        except Exception as exc:
            logger.error("PyMuPDF preview render failed for resume_id=%s: %s", resume.id, type(exc).__name__)
            return Response(
                {'error': 'Unable to render PDF pages for inline preview.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        record_security_event(user, 'resume.preview', resume.pk, application_id=app_id)
        try:
            audit_logger.info(
                f"[AUDIT:RESUME_PREVIEW] user_id={user_identifier} role={user_role} "
                f"resume_id={resume.id} application_id={app_id} pages={len(pages_payload)} "
                f"client_ip={client_ip}"
            )
        except Exception as log_err:
            logger.error("Audit logger failure on resume preview: %s", type(log_err).__name__)

        return Response({
            'format': 'pdf',
            'page_count': total_pages,
            'rendered_pages': len(pages_payload),
            'pages': pages_payload,
        })
