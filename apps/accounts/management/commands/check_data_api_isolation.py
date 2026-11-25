from importlib import import_module

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

PROTECTED = (
    *import_module('apps.accounts.migrations.0004_restrict_data_api').TABLES,
    'accounts_staffinvitation',
    import_module('apps.accounts.migrations.0009_throttle_window').TABLE,
    *import_module('apps.applications.migrations.0011_recruiter_enhancements').ENHANCEMENT_TABLES,
)

REALTIME_READ_POLICIES = {
    'applications_application': {
        'Allow applicants to view their own applications',
        'Allow recruiters company read of applications',
    },
    'notifications_notification': {
        'Allow applicants to view their own notifications',
        'Allow recruiters company read of notifications',
    },
}


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
                WHERE c.oid IS NULL OR NOT c.relrowsecurity OR (
                    NOT (t.name = ANY(%s::text[])) AND EXISTS (
                        SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon','authenticated')
                        AND (
                            has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                            OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
                        )
                    )
                ) OR (
                    t.name = ANY(%s::text[]) AND EXISTS (
                    SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon','authenticated')
                    AND (
                        (r.rolname = 'anon' AND (
                            has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                            OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
                        ))
                        OR has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                        OR has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE,REFERENCES')
                    )
                ))
            """, [list(PROTECTED), list(REALTIME_READ_POLICIES), list(REALTIME_READ_POLICIES)])
            tables = [name for name, in cursor.fetchall()]
            cursor.execute("""
                SELECT c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
                  AND NOT (c.relname = ANY(%s::text[]))
                  AND EXISTS (
                    SELECT 1 FROM pg_roles r WHERE r.rolname IN ('anon','authenticated')
                    AND (has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                         OR has_any_column_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,REFERENCES'))
                  )
            """, [list(REALTIME_READ_POLICIES)])
            public_relations = [name for name, in cursor.fetchall()]
            realtime_issues = []
            for table, expected_policies in REALTIME_READ_POLICIES.items():
                cursor.execute(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname='public' AND tablename=%s AND cmd='SELECT'",
                    [table],
                )
                present_policies = {name for name, in cursor.fetchall()}
                missing_policies = expected_policies - present_policies
                if missing_policies:
                    realtime_issues.append(f'{table}: missing policies {sorted(missing_policies)}')
                cursor.execute(
                    "SELECT 1 FROM pg_publication_tables "
                    "WHERE pubname='supabase_realtime' AND schemaname='public' AND tablename=%s",
                    [table],
                )
                if cursor.fetchone() is None:
                    realtime_issues.append(f'{table}: missing from supabase_realtime publication')
                cursor.execute(
                    "SELECT has_table_privilege('authenticated', %s, 'SELECT')",
                    [f'public.{table}'],
                )
                if not cursor.fetchone()[0]:
                    realtime_issues.append(f'{table}: authenticated SELECT is missing')
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
        if tables or realtime_issues or default_roles or public_relations or public_function_execute or exposed_helpers:
            raise CommandError(
                f'Client isolation failed. Protected tables: {tables}. '
                f'Realtime read issues: {realtime_issues}. '
                f'Public relations with client grants: {public_relations}. Default client grants: {default_roles}. '
                f'Default PUBLIC function execute: {public_function_execute}. Exposed Auth helpers: {exposed_helpers}.'
            )
        self.stdout.write(self.style.SUCCESS(
            f'Client isolation verified for {len(PROTECTED)} protected tables; '
            f'{len(REALTIME_READ_POLICIES)} realtime tables expose scoped SELECT only.'
        ))
