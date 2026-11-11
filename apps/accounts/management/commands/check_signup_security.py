import os
import re

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.views.decorators.debug import sensitive_variables


REQUIRED = {
    'mailer_autoconfirm': False,
    'mailer_allow_unverified_email_sign_ins': False,
    'external_anonymous_users_enabled': False,
}


class Command(BaseCommand):
    help = 'Verify or enforce the hosted Supabase signup safeguards with a release-only management token.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--max-auth-emails-per-hour', type=int, required=True)
        parser.add_argument(
            '--require-smtp-host',
            default='',
            help='Require Supabase Auth to use this custom SMTP host (for example smtp.resend.com).',
        )

    @sensitive_variables()
    def handle(self, *args, **options):
        limit = options['max_auth_emails_per_hour']
        if limit < 1:
            raise CommandError('Choose a positive hourly Auth email ceiling.')
        match = re.fullmatch(r'https://([a-z0-9]+)\.supabase\.co/?', settings.SUPABASE_URL)
        if not match:
            raise CommandError('Use the target project’s canonical Supabase URL.')
        token = os.getenv('SUPABASE_ACCESS_TOKEN', '').strip()
        if not token:
            raise CommandError('Set a scoped SUPABASE_ACCESS_TOKEN in the release shell.')
        url = f'https://api.supabase.com/v1/projects/{match[1]}/config/auth'
        headers = {'Authorization': f'Bearer {token}'}
        config = self.request('GET', url, headers)
        changes = {key: value for key, value in REQUIRED.items() if config.get(key) is not value}
        email_limit = config.get('rate_limit_email_sent')
        if type(email_limit) is not int or not 1 <= email_limit <= limit:
            changes['rate_limit_email_sent'] = limit
        if changes and not options['apply']:
            raise CommandError(f'Auth settings require changes: {", ".join(changes)}. Review and rerun with --apply.')
        if changes:
            self.request('PATCH', url, headers, changes)
            config = self.request('GET', url, headers)
            if any(config.get(key) != value for key, value in changes.items()):
                raise CommandError('Supabase did not return the expected Auth settings after the update.')
        expected_smtp_host = options['require_smtp_host'].strip().lower()
        if expected_smtp_host:
            smtp_failures = []
            if str(config.get('smtp_host') or '').strip().lower() != expected_smtp_host:
                smtp_failures.append('smtp_host')
            if expected_smtp_host == 'smtp.resend.com' and str(config.get('smtp_user') or '').strip() != 'resend':
                smtp_failures.append('smtp_user')
            if config.get('smtp_port') not in (465, 587, '465', '587'):
                smtp_failures.append('smtp_port')
            if not str(config.get('smtp_admin_email') or '').strip():
                smtp_failures.append('smtp_admin_email')
            if config.get('external_email_enabled') is not True:
                smtp_failures.append('external_email_enabled')
            if smtp_failures:
                raise CommandError(
                    'Custom Auth SMTP is not ready: ' + ', '.join(smtp_failures) +
                    '. Configure credentials in Supabase Auth SMTP settings.'
                )
        self.stdout.write(self.style.SUCCESS(
            'Email confirmation, anonymous signup denial, Auth email ceiling, and custom SMTP verified.'
            if expected_smtp_host else
            'Email confirmation, anonymous signup denial, and Auth email ceiling verified.'
        ))

    @staticmethod
    @sensitive_variables()
    def request(method, url, headers, changes=None):
        try:
            response = requests.request(method, url, headers=headers, json=changes, timeout=10, allow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise CommandError(f'Supabase Management API returned HTTP {response.status_code}.')
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (requests.RequestException, ValueError):
            raise CommandError('Supabase Management API request failed.') from None
