from django.db import migrations


ROLE = 'talentmatch_runtime'
TABLE_ACCESS = {
    'accounts_securityevent': 'SELECT, INSERT',
    'accounts_staffinvitation': 'SELECT, INSERT, UPDATE',
    'accounts_userrole': 'SELECT, INSERT, UPDATE',
    'applications_application': 'SELECT, INSERT, UPDATE',
    'auth_user': 'SELECT, INSERT, UPDATE',
    'companies_company': 'SELECT, INSERT, UPDATE',
    'django_migrations': 'SELECT',
    'django_session': 'SELECT, INSERT, UPDATE, DELETE',
    'jobs_archivedjob': 'SELECT, INSERT, DELETE',
    'jobs_job': 'SELECT, INSERT, UPDATE',
    'notifications_notification': 'SELECT, INSERT, UPDATE',
    'resumes_matchresult': 'SELECT, INSERT, UPDATE',
    'resumes_resume': 'SELECT, INSERT, UPDATE',
    'resumes_resumework': 'SELECT, INSERT, UPDATE, DELETE',
}


def create_runtime_role(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    quote = schema_editor.quote_name
    role = quote(ROLE)
    with schema_editor.connection.cursor() as cursor:
        # A release operator provisions the login password outside source control.
        cursor.execute(f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS')
        cursor.execute(f'GRANT USAGE ON SCHEMA public TO {role}')
        for table, access in TABLE_ACCESS.items():
            relation = f'public.{quote(table)}'
            cursor.execute(f'GRANT {access} ON {relation} TO {role}')
            cursor.execute(f'CREATE POLICY talentmatch_runtime ON {relation} TO {role} USING (true) WITH CHECK (true)')
            if 'INSERT' in access and table != 'django_session':
                cursor.execute('SELECT pg_get_serial_sequence(%s, %s)', [relation, 'id'])
                sequence = cursor.fetchone()[0]
                if sequence:
                    cursor.execute(f'GRANT USAGE ON SEQUENCE {sequence} TO {role}')

        # Auth owns these RLS-protected tables. Expose booleans instead of granting table access.
        cursor.execute('''
            CREATE FUNCTION public.talentmatch_session_is_current(identity uuid, session uuid, cutoff timestamptz)
            RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
                SELECT EXISTS (SELECT 1 FROM auth.sessions
                               WHERE id = session AND user_id = identity AND created_at >= cutoff);
            $$
        ''')
        cursor.execute('''
            CREATE FUNCTION public.talentmatch_email_is_confirmed(identity uuid, account_email text)
            RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
                SELECT EXISTS (SELECT 1 FROM auth.users
                               WHERE id = identity AND lower(email) = account_email
                               AND email_confirmed_at IS NOT NULL AND deleted_at IS NULL);
            $$
        ''')
        cursor.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')")
        clients = ', '.join(['PUBLIC', *(quote(name) for name, in cursor.fetchall())])
        for signature in ('talentmatch_session_is_current(uuid, uuid, timestamptz)',
                          'talentmatch_email_is_confirmed(uuid, text)'):
            cursor.execute(f'REVOKE ALL ON FUNCTION public.{signature} FROM {clients}')
            cursor.execute(f'GRANT EXECUTE ON FUNCTION public.{signature} TO {role}')


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0007_staff_invitation'),
        ('applications', '0007_remove_application_job_email_constraint'),
    ]

    operations = [migrations.RunPython(create_runtime_role)]
