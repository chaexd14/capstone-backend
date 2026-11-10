import os
import uuid
import logging
from datetime import timedelta

import jwt
from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection, transaction, DatabaseError, IntegrityError
from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.accounts.models import UserRole, RoleChoices

logger = logging.getLogger(__name__)

# Cache of PyJWKClient instances keyed by normalized Supabase URL
_jwks_clients: dict = {}
SUPERADMIN_SESSION_MAX_AGE = timedelta(hours=8)


def staff_session_is_current(user_id, session_id, valid_after):
    try:
        identity = uuid.UUID(str(session_id))
    except (TypeError, ValueError):
        return False
    if connection.vendor != 'postgresql':
        return False
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("""
                SELECT public.talentmatch_session_is_current(%s, %s, %s)
            """, [user_id, identity, valid_after])
            return cursor.fetchone()[0]
    except DatabaseError:
        logger.warning('Unable to verify a staff session against Supabase Auth.')
        return False


def account_email_is_confirmed(user_id, email):
    if connection.vendor != 'postgresql':
        return settings.USE_LOCAL_DB
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('SELECT public.talentmatch_email_is_confirmed(%s, %s)', [user_id, email])
            return cursor.fetchone()[0]
    except DatabaseError:
        logger.warning('Unable to verify application email against Supabase Auth.')
        return False


def get_jwks_client(supabase_url: str):
    normalized_url = supabase_url.rstrip('/')
    if normalized_url not in _jwks_clients:
        jwks_url = f"{normalized_url}/auth/v1/.well-known/jwks.json"
        _jwks_clients[normalized_url] = jwt.PyJWKClient(
            jwks_url, cache_jwk_set=True, lifespan=3600
        )
    return _jwks_clients[normalized_url]


class SupabaseJWTAuthentication(authentication.BaseAuthentication):
    """
    Validates Supabase JWTs sent in the Authorization: Bearer <token> header.

    Security guarantees:
    1. Algorithm Confusion Protection: Strictly validates header 'alg' and prevents
       mixing symmetric secrets with asymmetric verification keys.
    2. Strict Claim & Issuer Verification: Enforces presence of exp, iss, sub, and aud.
       Validates issuer strictly against the configured Supabase project URL.
    3. Generic Client Errors: Emits generic error messages to clients while logging
       detailed diagnostic messages server-side.
    4. Authoritative Role Resolution: Role is determined EXCLUSIVELY by the server-side
       UserRole database table. User-editable metadata (user_metadata) is NEVER used
       to grant permissions or elevate Django is_staff/is_superuser flags.
    5. Fail-Closed Default: Unprovisioned or unrecognized users are strictly mapped to
       RoleChoices.APPLICANT with is_staff=False and is_superuser=False.
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

        supabase_url = (getattr(settings, 'SUPABASE_URL', '') or '').strip()
        if not supabase_url:
            logger.error("SUPABASE_URL is not configured on the server.")
            raise exceptions.AuthenticationFailed("Authentication service configuration error")

        jwt_secret = getattr(
            settings,
            'SUPABASE_JWT_SECRET',
            os.getenv('SUPABASE_JWT_SECRET', '')
        )

        try:
            unverified_header = jwt.get_unverified_header(token)
        except Exception as e:
            logger.warning("Supabase JWT malformed header: %s", str(e))
            raise exceptions.AuthenticationFailed("Invalid or expired authentication credentials")

        alg = unverified_header.get('alg')
        if not alg:
            logger.warning("Supabase JWT missing 'alg' header")
            raise exceptions.AuthenticationFailed("Invalid or expired authentication credentials")

        payload = None
        decode_error = None

        expected_issuer = f"{supabase_url.rstrip('/')}/auth/v1"
        decode_options = {
            "verify_signature": True,
            "verify_exp": True,
            "verify_aud": True,
            "verify_iss": True,
            "require": ["exp", "iss", "sub", "aud"],
        }

        # 1. Asymmetric verification (RS256, ES256) via Supabase JWKS
        if alg in ('RS256', 'ES256'):
            try:
                jwks_client = get_jwks_client(supabase_url)
                signing_key = jwks_client.get_signing_key_from_jwt(token)
                payload = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=[alg],
                    audience="authenticated",
                    issuer=expected_issuer,
                    options=decode_options,
                )
            except Exception as e:
                decode_error = e
                logger.warning("JWKS asymmetric decode failed: %s", str(e))

        # 2. Symmetric verification (HS256) via configured SUPABASE_JWT_SECRET
        elif alg == 'HS256':
            if not jwt_secret:
                logger.error("HS256 token received but SUPABASE_JWT_SECRET is not configured on the server")
                raise exceptions.AuthenticationFailed("Authentication service configuration error")
            try:
                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=['HS256'],
                    audience="authenticated",
                    issuer=expected_issuer,
                    options=decode_options,
                )
            except Exception as e:
                decode_error = e
                logger.warning("HS256 symmetric decode failed: %s", str(e))

        else:
            logger.warning("Unsupported token signing algorithm: %s", alg)
            raise exceptions.AuthenticationFailed("Invalid or expired authentication credentials")

        if payload is None:
            logger.warning("Supabase JWT decode failed: %s", str(decode_error))
            raise exceptions.AuthenticationFailed("Invalid or expired authentication credentials")

        user_id_str = payload.get('sub')
        try:
            user_uuid = uuid.UUID(str(user_id_str))
        except (ValueError, TypeError) as e:
            logger.warning("Invalid token subject UUID '%s': %s", user_id_str, str(e))
            raise exceptions.AuthenticationFailed("Invalid or expired authentication credentials")

        email = (payload.get('email') or '').strip().lower()

        # =====================================================================
        # AUTHORITATIVE SERVER-CONTROLLED ROLE RESOLUTION & SAFE PROVISIONING
        # Look up authoritative role strictly by verified Supabase auth UUID.
        # Client-editable metadata (payload['user_metadata']) and JWT claims
        # are strictly IGNORED.
        #
        # Security: Automatic claiming of elevated roles (recruiter/superadmin)
        # by matching JWT email is explicitly PROHIBITED. Elevated roles
        # must be provisioned against the exact Supabase UUID via backend CLI.
        #
        # Concurrency & Association Safety:
        # 1. Do not silently reassign an existing UserRole to a different User.
        # 2. If existing associations conflict with the verified UUID, fail closed
        #    and log a structured security event.
        # 3. Narrow IntegrityError recovery strictly to concurrent creation races.
        # =====================================================================
        user_role_obj = UserRole.objects.filter(supabase_user_id=user_uuid).first()

        # If existing UserRole has a linked Django User, verify identity consistency
        if user_role_obj and user_role_obj.user_id:
            user = User.objects.filter(id=user_role_obj.user_id).first()
            if not user or user.username != str(user_uuid):
                logger.error(
                    "[SECURITY:IDENTITY_CONFLICT] Supabase UUID %s associated with conflicting User (id=%s, username=%s)",
                    user_uuid, user_role_obj.user_id, getattr(user, 'username', 'MISSING')
                )
                raise exceptions.AuthenticationFailed("Authentication identity association conflict")
        else:
            # Check if a Django User with this username already exists
            user = User.objects.filter(username=str(user_uuid)).first()
            # If this User already has a linked UserRole with a DIFFERENT supabase_user_id, conflict!
            if user and hasattr(user, 'role_profile') and user.role_profile:
                if user.role_profile.supabase_user_id != user_uuid:
                    logger.error(
                        "[SECURITY:IDENTITY_CONFLICT] User %s is already bound to different Supabase UUID %s (attempted: %s)",
                        user.id, user.role_profile.supabase_user_id, user_uuid
                    )
                    raise exceptions.AuthenticationFailed("Authentication identity association conflict")

        if user and not user.is_active:
            raise exceptions.AuthenticationFailed("This account has been disabled.")

        if not user or not user_role_obj or user_role_obj.user_id is None:
            try:
                with transaction.atomic():
                    if not user:
                        user = User.objects.create(
                            username=str(user_uuid),
                            email=email,
                            is_staff=False,
                            is_superuser=False,
                        )
                    if not user_role_obj:
                        user_role_obj = UserRole.objects.create(
                            user=user,
                            supabase_user_id=user_uuid,
                            email=email,
                            role=RoleChoices.APPLICANT,
                            assigned_by='system_registration',
                            notes='Baseline applicant profile auto-created on first authenticated request'
                        )
                    else:
                        if user_role_obj.user_id is None:
                            user_role_obj.user = user
                            if email and not user_role_obj.email:
                                user_role_obj.email = email
                            user_role_obj.save(update_fields=['user', 'email'])
            except IntegrityError as exc:
                # Narrow recovery: Only recover if this was an expected concurrent creation race
                user = User.objects.filter(username=str(user_uuid)).first()
                user_role_obj = UserRole.objects.filter(supabase_user_id=user_uuid).first()
                if not user or not user_role_obj:
                    logger.error(
                        "[DATABASE:INTEGRITY_ERROR] Unexpected database integrity error during user provisioning for UUID %s: %s",
                        user_uuid, exc
                    )
                    raise exc

                if user_role_obj.user_id and user_role_obj.user_id != user.id:
                    logger.error(
                        "[SECURITY:IDENTITY_CONFLICT] Concurrently provisioned UserRole (id=%s) points to different User (id=%s, expected=%s)",
                        user_role_obj.id, user_role_obj.user_id, user.id
                    )
                    raise exceptions.AuthenticationFailed("Authentication identity association conflict")

                if user_role_obj.user_id is None:
                    user_role_obj.user = user
                    user_role_obj.save(update_fields=['user'])

        if not user.is_active:
            raise exceptions.AuthenticationFailed("This account has been disabled.")
        trusted_role = str(user_role_obj.role).lower().strip()
        if trusted_role not in (RoleChoices.APPLICANT, RoleChoices.RECRUITER, RoleChoices.SUPERADMIN):
            trusted_role = RoleChoices.APPLICANT

        valid_after = user_role_obj.sessions_valid_after
        if trusted_role == RoleChoices.SUPERADMIN:
            # Token refresh cannot extend the lifetime of the original session.
            session_cutoff = timezone.now() - SUPERADMIN_SESSION_MAX_AGE
            valid_after = max(valid_after, session_cutoff) if valid_after else session_cutoff
        if valid_after and not staff_session_is_current(user_uuid, payload.get('session_id'), valid_after):
            raise exceptions.AuthenticationFailed("Sign out and sign in again to use this account.")

        # Application-specific authorization:
        # TalentMatch permissions rely on application-level role checks (user.role).
        # Neither is_staff nor is_superuser is granted to Supabase Auth users,
        # preventing unauthorized access to the Django administrative site (/admin/)
        # and preventing Django model permission bypasses.
        is_staff_role = False
        is_superuser_role = False

        user.role = trusted_role
        user.role_profile = user_role_obj
        user.supabase_id = str(user_uuid)
        user.supabase_email = email

        update_fields = []
        if email and user.email != email:
            user.email = email
            update_fields.append('email')

        # Synchronize Django User flags strictly to match authoritative server role.
        # Immediately revokes is_staff/is_superuser if role was demoted or previously set.
        if user.is_staff != is_staff_role:
            user.is_staff = is_staff_role
            update_fields.append('is_staff')
        if user.is_superuser != is_superuser_role:
            user.is_superuser = is_superuser_role
            update_fields.append('is_superuser')

        if update_fields:
            user.save(update_fields=update_fields)

        return (user, token)
