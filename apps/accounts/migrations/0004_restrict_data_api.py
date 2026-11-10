from django.db import migrations


TABLES = (
    'accounts_securityevent', 'accounts_userrole', 'applications_application',
    'companies_company', 'jobs_archivedjob', 'jobs_job', 'notifications_notification',
    'resumes_matchresult', 'resumes_resume', 'resumes_resumework',
    'auth_group', 'auth_group_permissions', 'auth_permission', 'auth_user',
    'auth_user_groups', 'auth_user_user_permissions', 'django_admin_log',
    'django_content_type', 'django_migrations', 'django_session',
)


def protect_django_tables(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
        recipients = ', '.join(['PUBLIC', *(quote(role) for (role,) in cursor.fetchall())])
        cursor.execute("SELECT to_regprocedure('public.is_recruiter_or_superadmin()')")
        if cursor.fetchone()[0]:
            cursor.execute(
                f'REVOKE EXECUTE ON FUNCTION public.is_recruiter_or_superadmin() FROM {recipients}'
            )
        for table in TABLES:
            cursor.execute(
                "SELECT attname FROM pg_attribute "
                "WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped",
                [f'public.{table}'],
            )
            columns = ', '.join(quote(column) for (column,) in cursor.fetchall())
            if columns:
                relation = f'public.{quote(table)}'
                cursor.execute(f'ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY')
                cursor.execute(f'REVOKE ALL PRIVILEGES ON TABLE {relation} FROM {recipients}')
                cursor.execute(f'REVOKE ALL PRIVILEGES ({columns}) ON TABLE {relation} FROM {recipients}')
                cursor.execute(
                    "SELECT pg_get_serial_sequence(%s, attname) FROM pg_attribute "
                    "WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped",
                    [relation, relation],
                )
                for (sequence,) in cursor.fetchall():
                    if sequence:
                        cursor.execute(f'REVOKE ALL PRIVILEGES ON SEQUENCE {sequence} FROM {recipients}')


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0003_securityevent_userrole_company'),
        ('jobs', '0006_archivedjob_active_job'),
        ('resumes', '0007_resume_scan_passed_resumework'),
        ('sessions', '0001_initial'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]
    operations = [migrations.RunPython(protect_django_tables)]
