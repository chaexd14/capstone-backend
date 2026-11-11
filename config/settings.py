"""
Django settings for TalentMatch project.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from django.core.management.utils import get_random_secret_key

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
ENV_FILE = os.getenv('DJANGO_ENV_FILE', '.env')
load_dotenv(BASE_DIR / '.env')
if ENV_FILE and ENV_FILE != '.env':
    load_dotenv(BASE_DIR / ENV_FILE, override=True)

DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't')
SECRET_KEY = os.getenv('SECRET_KEY', '').strip()
if DEBUG and not SECRET_KEY:
    SECRET_KEY = get_random_secret_key()
ENABLE_DJANGO_ADMIN = os.getenv('ENABLE_DJANGO_ADMIN', 'False').lower() in ('true', '1')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_context': {'()': 'config.observability.RequestContextFilter'},
    },
    'formatters': {
        'json': {'()': 'config.observability.JsonFormatter'},
    },
    'handlers': {
        'structured_console': {
            'class': 'logging.StreamHandler',
            'filters': ['request_context'],
            'formatter': 'json',
        },
    },
    'root': {'handlers': ['structured_console'], 'level': os.getenv('LOG_LEVEL', 'INFO')},
    'loggers': {
        'security.events': {
            'handlers': ['structured_console'], 'level': 'INFO', 'propagate': False,
        },
        'security.audit': {
            'handlers': ['structured_console'], 'level': 'INFO', 'propagate': False,
        },
    },
}

SUPABASE_URL = os.getenv('SUPABASE_URL', '').strip()
SUPABASE_JWT_SECRET = os.getenv('SUPABASE_JWT_SECRET', '')
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()
SUPABASE_SECRET_KEY = os.getenv('SUPABASE_SECRET_KEY', '').strip()
STAFF_INVITE_REDIRECT_URL = os.getenv('STAFF_INVITE_REDIRECT_URL', '').strip()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '').strip()
ENABLE_GEMINI_PROCESSING = os.getenv('ENABLE_GEMINI_PROCESSING', 'False').lower() in ('true', '1')
BACKUP_RESTORE_VERIFIED = os.getenv('BACKUP_RESTORE_VERIFIED', 'False').lower() in ('true', '1')
SECURITY_LOG_FORWARDING_CONFIRMED = os.getenv(
    'SECURITY_LOG_FORWARDING_CONFIRMED', 'False'
).lower() in ('true', '1')
PRIVACY_NOTICE_APPROVED = os.getenv('PRIVACY_NOTICE_APPROVED', 'False').lower() in ('true', '1')
STAGING_ACCEPTANCE_APPROVED = os.getenv('STAGING_ACCEPTANCE_APPROVED', 'False').lower() in ('true', '1')
CAPACITY_TEST_VERIFIED = os.getenv('CAPACITY_TEST_VERIFIED', 'False').lower() in ('true', '1')

if not DEBUG:
    if not SECRET_KEY or SECRET_KEY.startswith('django-insecure-'):
        raise RuntimeError("Set a secure SECRET_KEY for production.")
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL must be configured in production.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        'ALLOWED_HOSTS',
        'localhost,127.0.0.1'
    ).split(',')
    if host.strip()
]

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    'corsheaders',
    
    # Local apps
    'apps.accounts',
    'apps.companies',
    'apps.jobs',
    'apps.applications',
    'apps.resumes',
    'apps.notifications',
]

MIDDLEWARE = [
    'config.middleware.RequestIdMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'config.middleware.ApiAccessSecurityMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database (Supabase PostgreSQL / DATABASE_URL support with SQLite local fallback)
import dj_database_url

DATABASE_URL = os.getenv('DATABASE_URL') or os.getenv('SUPABASE_DB_URL')
DB_ENGINE = os.getenv('DB_ENGINE', 'django.db.backends.sqlite3')

USE_LOCAL_DB = os.getenv('USE_LOCAL_DB', 'False').lower() in ('true', '1', 't')

if USE_LOCAL_DB:
    if not DEBUG:
        raise RuntimeError('SQLite development mode requires DEBUG=True.')
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
elif DATABASE_URL:
    db_config = dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=600,
        conn_health_checks=True,
    )
    if 'postgres' in db_config.get('ENGINE', ''):
        db_config.setdefault('OPTIONS', {})
        db_config['OPTIONS'].update({
            'sslmode': os.getenv('DB_SSLMODE', 'require'),
            'connect_timeout': 10,
            'keepalives': 1,
            'keepalives_idle': 30,
            'keepalives_interval': 10,
            'keepalives_count': 5,
        })
        if str(db_config.get('PORT')) == '6543' and (db_config.get('HOST') or '').endswith('.pooler.supabase.com'):
            db_config['DISABLE_SERVER_SIDE_CURSORS'] = True
    DATABASES = {'default': db_config}
elif 'postgresql' in DB_ENGINE.lower():
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DB_NAME', 'postgres'),
            'USER': os.getenv('DB_USER', 'postgres'),
            'PASSWORD': os.getenv('DB_PASSWORD', 'postgres'),
            'HOST': os.getenv('DB_HOST', 'localhost'),
            'PORT': os.getenv('DB_PORT', '5432'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / os.getenv('DB_NAME', 'db.sqlite3'),
        }
    }

# PostgreSQL provides shared throttling by default. Redis remains an optional cache backend.
REDIS_URL = os.getenv('REDIS_URL', '').strip()
EDGE_RATE_LIMIT_ENFORCED = os.getenv('EDGE_RATE_LIMIT_ENFORCED', 'False').lower() in ('true', '1')
POSTGRES_RATE_LIMITING = not REDIS_URL and 'postgresql' in DATABASES['default']['ENGINE']

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'talentmatch-locmem-cache',
        }
    }


def is_distributed_throttling_ready(caches_config=None, edge_enforced=None, postgres_rate_limiting=None) -> bool:
    active_caches = caches_config if caches_config is not None else CACHES
    active_edge = edge_enforced if edge_enforced is not None else EDGE_RATE_LIMIT_ENFORCED
    active_postgres = postgres_rate_limiting if postgres_rate_limiting is not None else POSTGRES_RATE_LIMITING
    backend = active_caches.get('default', {}).get('BACKEND')
    return bool(active_edge or active_postgres or backend == 'django.core.cache.backends.redis.RedisCache')

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

USE_R2_STORAGE = os.getenv('USE_R2_STORAGE', 'False').lower() in ('true', '1')

STORAGES = {
    "default": {
        "BACKEND": "config.storage_backends.CloudflareR2ResumeStorage"
        if USE_R2_STORAGE
        else "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

# Media files (Resume uploads, logos)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

from corsheaders.defaults import default_headers

# CORS Settings
CORS_ALLOW_ALL_ORIGINS = False

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        'CORS_ALLOWED_ORIGINS',
        'http://localhost:3000,http://127.0.0.1:3000' if DEBUG else ''
    ).split(',')
    if origin.strip()
]

CORS_ALLOW_CREDENTIALS = False
CORS_EXPOSE_HEADERS = ['Content-Disposition']

CORS_ALLOW_HEADERS = list(default_headers) + [
    'x-app-client-key',
    'x-requested-with',
]

# CSRF Settings
default_csrf = 'http://localhost:3000,http://127.0.0.1:3000' if DEBUG else ''
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv('CSRF_TRUSTED_ORIGINS', default_csrf).split(',')
    if origin.strip()
]

# Deployment Security & SSL Settings
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'False' if DEBUG else 'True').lower() in ('true', '1')
SESSION_COOKIE_SECURE = not DEBUG or os.getenv('SESSION_COOKIE_SECURE', 'False').lower() in ('true', '1')
CSRF_COOKIE_SECURE = not DEBUG or os.getenv('CSRF_COOKIE_SECURE', 'False').lower() in ('true', '1')
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

# HTTP Strict Transport Security (HSTS)
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0' if DEBUG else '31536000'))
# Do not enable include_subdomains or preload by default until all affected subdomains are confirmed HTTPS-ready
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv('SECURE_HSTS_SUBDOMAINS', 'False').lower() in ('true', '1')
SECURE_HSTS_PRELOAD = os.getenv('SECURE_HSTS_PRELOAD', 'False').lower() in ('true', '1')

# Request and upload size limits (protect against memory exhaustion / DOS)
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('DATA_UPLOAD_MAX_MEMORY_SIZE', str(10 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv('FILE_UPLOAD_MAX_MEMORY_SIZE', str(1024 * 1024)))
MAX_RESUME_UPLOAD_SIZE = int(os.getenv('MAX_RESUME_UPLOAD_SIZE', str(10 * 1024 * 1024)))  # Application-level max resume size: 10MB
REQUIRE_RESUME_SCAN = os.getenv(
    'REQUIRE_RESUME_SCAN', 'False' if DEBUG else 'True'
).lower() in ('true', '1')
RESUME_SCANNER = os.getenv('RESUME_SCANNER', 'clamscan')

# Reverse proxy trusted IPs for secure X-Forwarded-For processing
TRUSTED_PROXY_IPS = [ip.strip() for ip in os.getenv('TRUSTED_PROXY_IPS', '127.0.0.1,::1').split(',') if ip.strip()]

# REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'config.authentication.SupabaseJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'config.throttling.TrustedAnonRateThrottle',
        'config.throttling.TrustedUserRateThrottle',
        'config.throttling.SensitiveActionThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.getenv('THROTTLE_ANON_RATE', '200/minute'),
        'user': os.getenv('THROTTLE_USER_RATE', '1000/minute'),
        'tracking_token': os.getenv('THROTTLE_TRACKING_RATE', '20/minute'),
        'resume_apply': os.getenv('THROTTLE_RESUME_APPLY_RATE', '10/hour'),
        'resume_download': '60/minute',
        'ai_action': '20/hour',
        'recruiter_report': '30/hour',
        'staff_creation': '20/hour',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
