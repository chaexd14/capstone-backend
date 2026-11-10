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
    if not is_distributed_throttling_ready(settings.CACHES, settings.EDGE_RATE_LIMIT_ENFORCED):
        errors.append(Error(
            'Configure shared Redis rate limiting or enforce limits at the trusted edge.',
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
    return errors
