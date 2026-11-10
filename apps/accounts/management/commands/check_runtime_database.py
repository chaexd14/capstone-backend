from importlib import import_module

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


runtime = import_module('apps.accounts.migrations.0008_restricted_database_runtime')


class Command(BaseCommand):
    help = 'Check RLS and effective privileges using the Django runtime connection.'

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('Run this check with the PostgreSQL runtime credential.')

        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute('''
                SELECT rolname, (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
                FROM pg_roles WHERE rolname = current_user
            ''')
            role, elevated = cursor.fetchone()
            if role != runtime.ROLE or elevated:
                raise CommandError('The database connection must use the restricted TalentMatch runtime role.')
            cursor.execute('''
                SELECT has_schema_privilege(current_user, 'public', 'CREATE')
                    OR has_schema_privilege(current_user, 'auth', 'CREATE')
                    OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = (SELECT oid FROM pg_roles WHERE rolname=current_user))
            ''')
            if cursor.fetchone()[0]:
                raise CommandError('The runtime role can create schema objects or has another role membership.')

            for table, access in runtime.TABLE_ACCESS.items():
                cursor.execute('''
                    SELECT row_security_active(c.oid),
                        ARRAY(SELECT privilege FROM unnest(%s::text[]) AS privilege
                              WHERE has_table_privilege(current_user, c.oid, privilege))
                    FROM pg_class c WHERE c.oid = to_regclass(%s)
                ''', [['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'], f'public.{table}'])
                result = cursor.fetchone()
                if not result or not result[0] or set(result[1]) != set(access.split(', ')):
                    raise CommandError(f'Unexpected grants or inactive RLS on {table}.')

            cursor.execute('''
                SELECT public.talentmatch_session_is_current(NULL, NULL, now()),
                       public.talentmatch_email_is_confirmed(NULL, '')
            ''')
            if cursor.fetchone() != (False, False):
                raise CommandError('The Auth verification functions returned an unexpected result.')
            cursor.execute('''
                SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                               WHERE n.nspname = 'auth' AND c.relkind IN ('r', 'p')
                               AND (has_table_privilege(current_user, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')
                                    OR has_any_column_privilege(current_user, c.oid, 'SELECT,INSERT,UPDATE')))
            ''')
            if cursor.fetchone()[0]:
                raise CommandError('The runtime role has direct access to Supabase Auth data.')

        self.stdout.write(self.style.SUCCESS('Runtime grants and row-level security verified.'))
