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


def _provider_error(response, fallback='Staff provisioning is temporarily unavailable.'):
    try:
        data = response.json()
        message = data.get('msg') or data.get('message') or data.get('error_description')
        if response.status_code in (400, 409, 422) and message:
            raise exceptions.ValidationError({'email': message})
    except (ValueError, TypeError, AttributeError):
        pass
    raise AuthProviderUnavailable(fallback)


@sensitive_variables('password')
def create_staff_identity(email, password, first_name, last_name):
    response = _auth_admin_request('POST', '/admin/users', json={
        'email': email,
        'password': password,
        'email_confirm': True,
        'user_metadata': {
            'first_name': first_name,
            'last_name': last_name,
            'full_name': f'{first_name} {last_name}'.strip(),
        },
    })
    if 200 <= response.status_code < 300:
        try:
            return UUID(response.json()['id'])
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    _provider_error(response)


def delete_staff_identity(identity):
    response = _auth_admin_request('DELETE', f'/admin/users/{identity}')
    if not 200 <= response.status_code < 300 and response.status_code != 404:
        logger.error('Supabase staff cleanup failed. identity=%s status=%s', identity, response.status_code)


@sensitive_variables('password')
def set_staff_password(identity, password):
    response = _auth_admin_request('PUT', f'/admin/users/{identity}', json={'password': password})
    if 200 <= response.status_code < 300:
        return
    try:
        if response.json().get('error_code') == 'weak_password':
            raise exceptions.ValidationError({'password': 'Choose a stronger password that meets the project policy.'})
    except (ValueError, AttributeError):
        pass
    raise AuthProviderUnavailable()


def password_reset_redirect():
    value = settings.PASSWORD_RESET_REDIRECT_URL
    redirect = urlsplit(value)
    secure = redirect.scheme == 'https' or (
        redirect.scheme == 'http' and redirect.hostname in ('localhost', '127.0.0.1', '::1')
    )
    if (not secure or not redirect.hostname or redirect.path != '/reset-password'
            or redirect.username or redirect.password or redirect.query or redirect.fragment):
        raise AuthProviderUnavailable('Set PASSWORD_RESET_REDIRECT_URL to the approved password reset page.')
    return value


def send_password_reset(email):
    response = _auth_admin_request(
        'POST', '/recover', params={'redirect_to': password_reset_redirect()}, json={'email': email},
    )
    if not 200 <= response.status_code < 300:
        logger.warning('Supabase password recovery failed. status=%s', response.status_code)
        raise AuthProviderUnavailable('The password reset link could not be sent. Try again later.')


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
