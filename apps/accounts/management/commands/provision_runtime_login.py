import os
from importlib import import_module

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.views.decorators.debug import sensitive_variables
from psycopg import sql


ROLE = import_module('apps.accounts.migrations.0008_restricted_database_runtime').ROLE


class Command(BaseCommand):
    help = 'Enable the restricted database login using a password from the release environment.'

    @sensitive_variables()
    def handle(self, *args, **options):
        password = os.environ.get('TALENTMATCH_RUNTIME_DB_PASSWORD', '')
        if len(password) < 32:
            raise CommandError('Set TALENTMATCH_RUNTIME_DB_PASSWORD to a new random password of at least 32 characters.')
        if connection.vendor != 'postgresql':
            raise CommandError('Run this command with the PostgreSQL migration credential.')

        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('''
                SELECT rolcanlogin, rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication
                FROM pg_roles WHERE rolname = %s
            ''', [ROLE])
            if cursor.fetchone() != (False, False):
                raise CommandError('Expected a restricted role without an existing login. Run migrations first.')
            verifier = connection.connection.pgconn.encrypt_password(
                password.encode(), ROLE.encode(), b'scram-sha-256',
            ).decode()
            cursor.execute(sql.SQL('ALTER ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(ROLE), sql.Literal(verifier)))

        self.stdout.write(self.style.SUCCESS('Restricted database login enabled.'))
