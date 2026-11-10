from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Tags, Error, register

from config.settings import is_distributed_throttling_ready


def is_https_origin(value):
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password
                and url.path in ('', '/') and not url.query and not url.fragment)
    except ValueError:
        return False


@register(Tags.security, deploy=True)
def check_resume_security(app_configs, **kwargs):
    errors = []
    if not is_distributed_throttling_ready(
        settings.CACHES, settings.EDGE_RATE_LIMIT_ENFORCED, settings.POSTGRES_RATE_LIMITING,
    ):
        errors.append(Error(
            'Use shared PostgreSQL or Redis rate limiting, or enforce limits at the trusted edge.',
            id='talentmatch.E001',
        ))
    if not settings.REQUIRE_RESUME_SCAN:
        errors.append(Error('Enable scanning and provision the resume worker.', id='talentmatch.E003'))
    if not settings.USE_R2_STORAGE:
        errors.append(Error('Configure private R2 storage for production resumes.', id='talentmatch.E004'))
    else:
        from config.storage_backends import CloudflareR2ResumeStorage

        storage = CloudflareR2ResumeStorage
        if not all((storage.bucket_name, storage.endpoint_url, storage.access_key, storage.secret_key)):
            errors.append(Error('Configure the private R2 bucket endpoint and credentials.', id='talentmatch.E005'))
    if settings.DEBUG or not settings.SECURE_SSL_REDIRECT or settings.ENABLE_DJANGO_ADMIN:
        errors.append(Error('Disable debug and Django admin and require HTTPS for the API.', id='talentmatch.E006'))
    if not settings.ALLOWED_HOSTS or any('*' in host or host.startswith('.') for host in settings.ALLOWED_HOSTS):
        errors.append(Error('Set an explicit API host allowlist.', id='talentmatch.E007'))
    if len(settings.SECRET_KEY) < 50 or len(set(settings.SECRET_KEY)) < 5:
        errors.append(Error('Use a randomly generated Django signing key.', id='talentmatch.E008'))
    if (not is_https_origin(settings.SUPABASE_URL) or not settings.CORS_ALLOWED_ORIGINS
            or not all(map(is_https_origin, settings.CORS_ALLOWED_ORIGINS))):
        errors.append(Error('Configure HTTPS Supabase and frontend origins.', id='talentmatch.E009'))
    if settings.ENABLE_GEMINI_PROCESSING and not settings.GEMINI_API_KEY:
        errors.append(Error('Configure the server-side Gemini key before enabling AI processing.', id='talentmatch.E010'))
    release_evidence = (
        ('BACKUP_RESTORE_VERIFIED', settings.BACKUP_RESTORE_VERIFIED, 'Complete and approve a backup restoration drill.', 'talentmatch.E011'),
        ('SECURITY_LOG_FORWARDING_CONFIRMED', settings.SECURITY_LOG_FORWARDING_CONFIRMED, 'Confirm protected centralized security-log forwarding.', 'talentmatch.E012'),
        ('PRIVACY_NOTICE_APPROVED', settings.PRIVACY_NOTICE_APPROVED, 'Approve and publish the deployment privacy notice.', 'talentmatch.E013'),
        ('STAGING_ACCEPTANCE_APPROVED', settings.STAGING_ACCEPTANCE_APPROVED, 'Complete and approve production-equivalent staging acceptance.', 'talentmatch.E014'),
        ('CAPACITY_TEST_VERIFIED', settings.CAPACITY_TEST_VERIFIED, 'Complete and approve the production-equivalent capacity test.', 'talentmatch.E015'),
    )
    for _, confirmed, message, check_id in release_evidence:
        if not confirmed:
            errors.append(Error(message, id=check_id))
    return errors
