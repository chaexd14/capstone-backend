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
        self.stdout.write(self.style.SUCCESS('Email confirmation, anonymous signup denial, and Auth email ceiling verified.'))

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
