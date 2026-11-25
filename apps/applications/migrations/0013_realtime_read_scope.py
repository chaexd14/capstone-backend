from django.db import migrations


def configure_application_realtime(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'authenticated'")
        if cursor.fetchone() is None:
            return

        cursor.execute(
            '''
            CREATE OR REPLACE FUNCTION public.talentmatch_can_access_company(target_company_id bigint)
            RETURNS boolean
            LANGUAGE sql
            STABLE
            SECURITY DEFINER
            SET search_path = ''
            AS $$
                SELECT EXISTS (
                    SELECT 1
                    FROM public.accounts_userrole role_profile
                    WHERE role_profile.supabase_user_id = auth.uid()
                      AND (
                          role_profile.role = 'superadmin'
                          OR (
                              role_profile.role = 'recruiter'
                              AND role_profile.company_id = target_company_id
                          )
                      )
                )
            $$;
            REVOKE ALL ON FUNCTION public.talentmatch_can_access_company(bigint) FROM PUBLIC;
            GRANT EXECUTE ON FUNCTION public.talentmatch_can_access_company(bigint) TO authenticated;

            DROP POLICY IF EXISTS "Allow recruiters full access to applications"
                ON public.applications_application;
            CREATE POLICY "Allow recruiters company read of applications"
                ON public.applications_application
                FOR SELECT
                TO authenticated
                USING (
                    public.talentmatch_can_access_company(
                        (SELECT job.company_id FROM public.jobs_job job WHERE job.id = job_id)
                    )
                );

            GRANT SELECT ON TABLE public.applications_application TO authenticated;
            '''
        )

        cursor.execute("SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'")
        if cursor.fetchone() is not None:
            cursor.execute(
                "SELECT 1 FROM pg_publication_tables "
                "WHERE pubname = 'supabase_realtime' AND schemaname = 'public' "
                "AND tablename = 'applications_application'"
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    'ALTER PUBLICATION supabase_realtime '
                    'ADD TABLE public.applications_application'
                )


def irreversible(_apps, _schema_editor):
    raise RuntimeError(
        'Realtime access is security-sensitive and must be changed with a forward migration.'
    )


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0010_allow_runtime_draft_job_deletion'),
        ('applications', '0012_application_performance_indexes'),
        ('jobs', '0009_job_collection_indexes'),
    ]

    operations = [
        migrations.RunPython(configure_application_realtime, reverse_code=irreversible),
    ]
