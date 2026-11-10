import logging
from urllib.parse import urlsplit
from uuid import UUID

import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables
from rest_framework import exceptions

logger = logging.getLogger(__name__)


class AuthProviderUnavailable(exceptions.APIException):
    status_code = 503
    default_detail = 'Staff provisioning is temporarily unavailable. Contact the platform operator.'


@sensitive_variables()
def _auth_admin_request(method, path, **kwargs):
    key = settings.SUPABASE_SECRET_KEY or settings.SUPABASE_SERVICE_ROLE_KEY
    if not key or not settings.SUPABASE_URL:
        raise AuthProviderUnavailable('Configure the Django server’s Supabase URL and secret key to invite staff.')
    headers = {'apikey': key}
    if not key.startswith('sb_secret_'):
        headers['Authorization'] = f'Bearer {key}'
    try:
        return requests.request(
            method, f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1{path}",
            headers=headers, timeout=(4, 12), allow_redirects=False, **kwargs,
        )
    except requests.RequestException:
        raise AuthProviderUnavailable() from None


def staff_invite_redirect():
    value = settings.STAFF_INVITE_REDIRECT_URL
    redirect = urlsplit(value)
    secure = redirect.scheme == 'https' or (
        redirect.scheme == 'http' and redirect.hostname in ('localhost', '127.0.0.1', '::1')
    )
    if (not secure or not redirect.hostname or redirect.path != '/accept-staff-invite'
            or redirect.username or redirect.password or redirect.query or redirect.fragment):
        raise AuthProviderUnavailable('Set STAFF_INVITE_REDIRECT_URL to the approved staff invitation page.')
    return value


def send_staff_invitation(email, redirect_url):
    response = _auth_admin_request('POST', '/invite', params={'redirect_to': redirect_url}, json={'email': email})
    try:
        data = response.json()
        if 200 <= response.status_code < 300:
            return UUID(data['id'])
        if data.get('error_code') in ('email_exists', 'user_already_exists'):
            raise exceptions.ValidationError({'email': 'An existing Supabase account uses this email.'})
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    logger.warning('Supabase invitation failed. status=%s', response.status_code)
    raise AuthProviderUnavailable()


@sensitive_variables()
def set_invited_staff_password(identity, email, password):
    path = f'/admin/users/{identity}'
    response = _auth_admin_request('GET', path)
    if not 200 <= response.status_code < 300:
        raise AuthProviderUnavailable()
    try:
        data = response.json()
        confirmed = (
            UUID(data['id']) == identity
            and data.get('email', '').casefold() == email.casefold()
            and bool(data.get('email_confirmed_at'))
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AuthProviderUnavailable() from None
    if not confirmed:
        raise exceptions.PermissionDenied('Verify the invited work email before activating this account.')

    result = _auth_admin_request('PUT', path, json={'password': password})
    if 200 <= result.status_code < 300:
        return
    try:
        if result.json().get('error_code') == 'weak_password':
            raise exceptions.ValidationError({'password': 'Choose a stronger password that meets the project policy.'})
    except (ValueError, AttributeError):
        pass
    logger.warning('Supabase staff activation failed. status=%s', result.status_code)
    raise AuthProviderUnavailable()
