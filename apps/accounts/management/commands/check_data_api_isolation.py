from importlib import import_module

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

PROTECTED = (*import_module('apps.accounts.migrations.0004_restrict_data_api').TABLES, 'accounts_staffinvitation')


class Command(BaseCommand):
    help = "Verify that public Data API roles cannot access protected Django tables."

    def handle(self, *args, **options):
        if connection.vendor != 'postgresql':
            raise CommandError('Run this check against the intended PostgreSQL database.')
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute("""
                SELECT t.name FROM unnest(%s::text[]) AS t(name)
                LEFT JOIN pg_class c ON c.oid = to_regclass('public.' || t.name)
                WHERE c.oid IS NULL OR NOT c.relrowsecurity OR EXISTS (
                    SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon','authenticated')
                    AND (
                        has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                        OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
                    )
                )
            """, [list(PROTECTED)])
            tables = [name for name, in cursor.fetchall()]
            cursor.execute("""
                SELECT c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
                  AND EXISTS (
                    SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon','authenticated')
                    AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                         OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))
                  )
            """)
            public_relations = [name for name, in cursor.fetchall()]
            cursor.execute("""
                SELECT DISTINCT r.rolname FROM pg_default_acl a
                CROSS JOIN LATERAL aclexplode(a.defaclacl) x
                JOIN pg_roles r ON r.oid=x.grantee
                WHERE a.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=current_user)
                  AND (a.defaclnamespace=0 OR a.defaclnamespace=to_regnamespace('public'))
                  AND r.rolname IN ('anon','authenticated')
            """)
            default_roles = [name for name, in cursor.fetchall()]
            cursor.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_roles owner
                    LEFT JOIN pg_default_acl d ON d.defaclrole=owner.oid
                        AND d.defaclnamespace=0 AND d.defaclobjtype='f'
                    CROSS JOIN LATERAL aclexplode(coalesce(d.defaclacl,acldefault('f',owner.oid))) x
                    WHERE owner.rolname=current_user AND x.grantee=0 AND x.privilege_type='EXECUTE'
                ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('anon','authenticated'))
            """)
            public_function_execute = cursor.fetchone()[0]
            cursor.execute('''
                SELECT p.proname FROM pg_proc p
                WHERE p.pronamespace = to_regnamespace('public') AND p.prosecdef
                    AND p.proname IN ('talentmatch_session_is_current', 'talentmatch_email_is_confirmed')
                    AND EXISTS (SELECT 1 FROM pg_roles r
                                WHERE r.rolname IN ('anon', 'authenticated', 'service_role')
                                AND has_function_privilege(r.oid, p.oid, 'EXECUTE'))
            ''')
            exposed_helpers = [name for name, in cursor.fetchall()]
        if tables or default_roles or public_relations or public_function_execute or exposed_helpers:
            raise CommandError(
                f'Client isolation failed. Protected tables: {tables}. '
                f'Public relations with client grants: {public_relations}. Default client grants: {default_roles}. '
                f'Default PUBLIC function execute: {public_function_execute}. Exposed Auth helpers: {exposed_helpers}.'
            )
        self.stdout.write(self.style.SUCCESS(f'No client grants on {len(PROTECTED)} protected tables or in this role’s public object defaults.'))
