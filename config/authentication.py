import os
import logging
import jwt
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework import authentication, exceptions

logger = logging.getLogger(__name__)

# Global PyJWKClient cache
_jwks_client = None

def get_jwks_client(supabase_url: str):
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
    return _jwks_client


class SupabaseJWTAuthentication(authentication.BaseAuthentication):
    """
    Validates Supabase JWTs sent in the Authorization: Bearer <token> header.
    Supports asymmetric JWKS verification (ES256, RS256) and symmetric secret verification (HS256).
    """

    def authenticate_header(self, request):
        return 'Bearer realm="api"'

    def authenticate(self, request):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None

        token = auth_header.split(' ', 1)[1].strip()
        if not token:
            return None

        supabase_url = getattr(settings, 'SUPABASE_URL', os.getenv('SUPABASE_URL', 'https://vbjzwghlifamkwuznyly.supabase.co'))
        jwt_secret = getattr(settings, 'SUPABASE_JWT_SECRET', os.getenv('SUPABASE_JWT_SECRET', ''))

        payload = None
        decode_error = None

        # 1. Attempt JWKS verification first (ES256, RS256)
        try:
            jwks_client = get_jwks_client(supabase_url)
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["ES256", "RS256", "HS256"],
                audience="authenticated"
            )
        except Exception as e:
            decode_error = e
            logger.debug(f"JWKS decode failed or skipped: {e}")

        # 2. Fallback to SUPABASE_JWT_SECRET (HS256) if JWKS did not succeed
        if payload is None and jwt_secret:
            try:
                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=["HS256"],
                    audience="authenticated"
                )
                decode_error = None
            except Exception as e:
                decode_error = e

        if payload is None:
            if isinstance(decode_error, jwt.ExpiredSignatureError):
                raise exceptions.AuthenticationFailed('Token has expired')
            raise exceptions.AuthenticationFailed(f'Invalid Supabase authentication token: {str(decode_error)}')

        user_id = payload.get('sub')
        if not user_id:
            raise exceptions.AuthenticationFailed('Invalid token: missing subject (sub) claim')

        email = payload.get('email', '')
        user_metadata = payload.get('user_metadata') or {}
        app_metadata = payload.get('app_metadata') or {}
        raw_role = user_metadata.get('role') or app_metadata.get('role') or 'applicant'
        role = str(raw_role).lower().strip()

        # Retrieve or instantiate mapped Django user
        is_staff_role = role in ('recruiter', 'superadmin')
        is_superuser_role = role == 'superadmin'

        user, created = User.objects.get_or_create(
            username=str(user_id),
            defaults={
                'email': email,
                'is_staff': is_staff_role,
                'is_superuser': is_superuser_role
            }
        )

        user.role = role
        user.supabase_id = str(user_id)

        update_fields = []
        if email and user.email != email:
            user.email = email
            update_fields.append('email')
        if not created:
            if user.is_staff != is_staff_role:
                user.is_staff = is_staff_role
                update_fields.append('is_staff')
            if user.is_superuser != is_superuser_role:
                user.is_superuser = is_superuser_role
                update_fields.append('is_superuser')

        if update_fields:
            user.save(update_fields=update_fields)

        return (user, token)
