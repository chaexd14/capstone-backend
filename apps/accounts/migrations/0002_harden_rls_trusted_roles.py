from django.db import migrations


def harden_rls_policies(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    schema_editor.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            -- Only configure Supabase RLS if the auth schema exists (Supabase PostgreSQL environment)
            IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth') THEN

                -- =====================================================================
                -- 1. Helper function: authoritative accounts_userrole table lookup ONLY.
                --    Strictly NO app_metadata or user_metadata fallback.
                --    Safe search_path = public, auth, pg_temp prevents path injection.
                --    All referenced tables and functions are fully schema-qualified.
                -- =====================================================================
                CREATE OR REPLACE FUNCTION public.is_recruiter_or_superadmin()
                RETURNS boolean
                LANGUAGE sql
                SECURITY DEFINER
                SET search_path = public, auth, pg_temp
                STABLE
                AS $func$
                    SELECT EXISTS (
                        SELECT 1 FROM public.accounts_userrole
                        WHERE supabase_user_id = auth.uid()
                          AND role IN ('recruiter', 'superadmin')
                    );
                $func$;

                -- Ensure owner is a trusted database role (e.g. postgres)
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'postgres') THEN
                    ALTER FUNCTION public.is_recruiter_or_superadmin() OWNER TO postgres;
                END IF;

                -- Minimal execute permissions: revoke from PUBLIC and anon, grant to authenticated and service_role
                REVOKE ALL ON FUNCTION public.is_recruiter_or_superadmin() FROM PUBLIC;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    REVOKE ALL ON FUNCTION public.is_recruiter_or_superadmin() FROM anon;
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    GRANT EXECUTE ON FUNCTION public.is_recruiter_or_superadmin() TO authenticated;
                END IF;
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
                    GRANT EXECUTE ON FUNCTION public.is_recruiter_or_superadmin() TO service_role;
                END IF;

                -- =====================================================================
                -- 2. Audit and drop EVERY existing policy across all seven protected
                --    application tables in schema public before installing the
                --    authoritative policy inventory (prevents OR-composition with
                --    unknown or legacy permissive policies).
                -- =====================================================================
                FOR r IN (
                    SELECT schemaname, tablename, policyname
                    FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename IN (
                          'applications_application',
                          'jobs_job',
                          'jobs_archivedjob',
                          'notifications_notification',
                          'resumes_resume',
                          'resumes_matchresult',
                          'accounts_userrole'
                      )
                ) LOOP
                    EXECUTE format('DROP POLICY IF EXISTS %%I ON %%I.%%I', r.policyname, r.schemaname, r.tablename);
                END LOOP;

                -- Explicitly drop known legacy, duplicate, or overly permissive policy names
                DROP POLICY IF EXISTS "Allow recruiters full access to applications" ON public.applications_application;
                DROP POLICY IF EXISTS "Recruiters full access to applications" ON public.applications_application;
                DROP POLICY IF EXISTS "Applicants can insert own applications" ON public.applications_application;
                DROP POLICY IF EXISTS "Allow applicants to insert their own applications" ON public.applications_application;
                DROP POLICY IF EXISTS "Allow applicants to view their own applications" ON public.applications_application;
                DROP POLICY IF EXISTS "Applicants can view own applications" ON public.applications_application;

                DROP POLICY IF EXISTS "Allow public read access to jobs" ON public.jobs_job;
                DROP POLICY IF EXISTS "Allow public read of published jobs" ON public.jobs_job;
                DROP POLICY IF EXISTS "Allow recruiters to manage jobs" ON public.jobs_job;
                DROP POLICY IF EXISTS "Recruiters can manage jobs" ON public.jobs_job;

                DROP POLICY IF EXISTS "Allow recruiters to manage archived jobs" ON public.jobs_archivedjob;
                DROP POLICY IF EXISTS "Recruiters can manage archived jobs" ON public.jobs_archivedjob;

                DROP POLICY IF EXISTS "Allow recruiters full access to notifications" ON public.notifications_notification;
                DROP POLICY IF EXISTS "Recruiters full access to notifications" ON public.notifications_notification;
                DROP POLICY IF EXISTS "Allow applicants to view their own notifications" ON public.notifications_notification;
                DROP POLICY IF EXISTS "Applicants can view own notifications" ON public.notifications_notification;
                DROP POLICY IF EXISTS "Allow applicants to update their own notifications" ON public.notifications_notification;
                DROP POLICY IF EXISTS "Applicants can update own notifications" ON public.notifications_notification;

                DROP POLICY IF EXISTS "Allow recruiters full access to resumes" ON public.resumes_resume;
                DROP POLICY IF EXISTS "Recruiters full access to resumes" ON public.resumes_resume;
                DROP POLICY IF EXISTS "Allow applicants to view their own resume" ON public.resumes_resume;
                DROP POLICY IF EXISTS "Applicants can view own resumes" ON public.resumes_resume;
                DROP POLICY IF EXISTS "Applicants can view own resume" ON public.resumes_resume;
                DROP POLICY IF EXISTS "Allow applicants to insert their own resume" ON public.resumes_resume;
                DROP POLICY IF EXISTS "Applicants can insert own resumes" ON public.resumes_resume;
                DROP POLICY IF EXISTS "Applicants can insert own resume" ON public.resumes_resume;

                DROP POLICY IF EXISTS "Allow recruiters full access to match results" ON public.resumes_matchresult;
                DROP POLICY IF EXISTS "Recruiters full access to match results" ON public.resumes_matchresult;
                DROP POLICY IF EXISTS "Allow applicants to view their own match results" ON public.resumes_matchresult;
                DROP POLICY IF EXISTS "Applicants can view own match results" ON public.resumes_matchresult;

                DROP POLICY IF EXISTS "Allow users to view own role profile" ON public.accounts_userrole;
                DROP POLICY IF EXISTS "Users can view own role" ON public.accounts_userrole;

                -- =====================================================================
                -- 3. Protect accounts_userrole table itself
                --    Enable RLS and enforce SELECT-only for authenticated user's own row.
                --    Does NOT call is_recruiter_or_superadmin() to prevent recursion.
                -- =====================================================================
                ALTER TABLE public.accounts_userrole ENABLE ROW LEVEL SECURITY;

                CREATE POLICY "Allow users to view own role profile"
                ON public.accounts_userrole
                FOR SELECT
                TO authenticated
                USING (supabase_user_id = auth.uid());

                -- =====================================================================
                -- 4. Authoritative policies on applications_application
                --    - Recruiters: full access via is_recruiter_or_superadmin()
                --    - Applicants: SELECT own row (user_id = auth.uid())
                --    - Direct client writes are disabled; Django is the trusted write path
                -- =====================================================================
                ALTER TABLE public.applications_application ENABLE ROW LEVEL SECURITY;

                CREATE POLICY "Allow recruiters full access to applications"
                ON public.applications_application
                FOR ALL
                TO authenticated
                USING (public.is_recruiter_or_superadmin())
                WITH CHECK (public.is_recruiter_or_superadmin());

                CREATE POLICY "Allow applicants to view their own applications"
                ON public.applications_application
                FOR SELECT
                TO authenticated
                USING (user_id = auth.uid());

                -- =====================================================================
                -- 5. Authoritative policies on jobs_job
                --    - Public: SELECT strictly restricted to status = 'PUBLISHED'
                --    - Recruiters: ALL operations via is_recruiter_or_superadmin()
                --    - Direct client writes are disabled via SQL table grants; Django is the trusted write path
                -- =====================================================================
                ALTER TABLE public.jobs_job ENABLE ROW LEVEL SECURITY;

                CREATE POLICY "Allow public read of published jobs"
                ON public.jobs_job
                FOR SELECT
                USING (status = 'PUBLISHED');

                CREATE POLICY "Allow recruiters to manage jobs"
                ON public.jobs_job
                FOR ALL
                TO authenticated
                USING (public.is_recruiter_or_superadmin())
                WITH CHECK (public.is_recruiter_or_superadmin());

                -- =====================================================================
                -- 6. Authoritative policies on jobs_archivedjob
                --    - Recruiters: ALL operations via is_recruiter_or_superadmin()
                --    - Direct client writes are disabled via SQL table grants; Django is the trusted write path
                -- =====================================================================
                ALTER TABLE public.jobs_archivedjob ENABLE ROW LEVEL SECURITY;

                CREATE POLICY "Allow recruiters to manage archived jobs"
                ON public.jobs_archivedjob
                FOR ALL
                TO authenticated
                USING (public.is_recruiter_or_superadmin())
                WITH CHECK (public.is_recruiter_or_superadmin());

                -- =====================================================================
                -- 7. Authoritative policies on notifications_notification
                --    - Recruiters: ALL operations via is_recruiter_or_superadmin()
                --    - Applicants: SELECT own notifications (user_id = auth.uid())
                --    - Direct client writes are disabled via SQL table grants; Django is the trusted write path
                -- =====================================================================
                ALTER TABLE public.notifications_notification ENABLE ROW LEVEL SECURITY;

                CREATE POLICY "Allow recruiters full access to notifications"
                ON public.notifications_notification
                FOR ALL
                TO authenticated
                USING (public.is_recruiter_or_superadmin())
                WITH CHECK (public.is_recruiter_or_superadmin());

                CREATE POLICY "Allow applicants to view their own notifications"
                ON public.notifications_notification
                FOR SELECT
                TO authenticated
                USING (user_id = auth.uid());

                CREATE POLICY "Allow applicants to update their own notifications"
                ON public.notifications_notification
                FOR UPDATE
                TO authenticated
                USING (user_id = auth.uid())
                WITH CHECK (user_id = auth.uid());

                -- =====================================================================
                -- 8. Authoritative policies on resumes_resume
                --    - Recruiters: ALL operations via is_recruiter_or_superadmin()
                --    - Applicants: SELECT restricted to linked application ownership
                --    - Direct client writes are disabled; Django is the trusted write path
                -- =====================================================================
                ALTER TABLE public.resumes_resume ENABLE ROW LEVEL SECURITY;

                CREATE POLICY "Allow recruiters full access to resumes"
                ON public.resumes_resume
                FOR ALL
                TO authenticated
                USING (public.is_recruiter_or_superadmin())
                WITH CHECK (public.is_recruiter_or_superadmin());

                CREATE POLICY "Allow applicants to view their own resume"
                ON public.resumes_resume
                FOR SELECT
                TO authenticated
                USING (
                    application_id IN (
                        SELECT id FROM public.applications_application WHERE user_id = auth.uid()
                    )
                );

                -- =====================================================================
                -- 9. Authoritative policies on resumes_matchresult
                --    - Recruiters: ALL operations via is_recruiter_or_superadmin()
                --    - Applicants: NO DIRECT ACCESS (match results are strictly recruiter-only)
                -- =====================================================================
                ALTER TABLE public.resumes_matchresult ENABLE ROW LEVEL SECURITY;

                CREATE POLICY "Allow recruiters full access to match results"
                ON public.resumes_matchresult
                FOR ALL
                TO authenticated
                USING (public.is_recruiter_or_superadmin())
                WITH CHECK (public.is_recruiter_or_superadmin());

                -- =====================================================================
                -- 10. Table Privileges & Grants: Explicit least-privilege grants
                --     Revoke ALL first to guarantee anon and authenticated never hold
                --     INSERT, UPDATE, DELETE, column-level UPDATE, TRUNCATE, REFERENCES,
                --     or TRIGGER privileges on protected business tables.
                --     Django (postgres role) is the exclusive trusted write path.
                -- =====================================================================
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    REVOKE ALL ON TABLE public.accounts_userrole FROM anon;
                    REVOKE ALL ON TABLE public.applications_application FROM anon;
                    REVOKE ALL ON TABLE public.jobs_archivedjob FROM anon;
                    REVOKE ALL ON TABLE public.notifications_notification FROM anon;
                    REVOKE ALL ON TABLE public.resumes_resume FROM anon;
                    REVOKE ALL ON TABLE public.resumes_matchresult FROM anon;
                    REVOKE ALL ON TABLE public.jobs_job FROM anon;
                    GRANT SELECT ON TABLE public.jobs_job TO anon;
                END IF;

                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    REVOKE ALL ON TABLE public.accounts_userrole FROM authenticated;
                    GRANT SELECT ON TABLE public.accounts_userrole TO authenticated;

                    REVOKE ALL ON TABLE public.applications_application FROM authenticated;
                    GRANT SELECT ON TABLE public.applications_application TO authenticated;

                    REVOKE ALL ON TABLE public.jobs_job FROM authenticated;
                    GRANT SELECT ON TABLE public.jobs_job TO authenticated;

                    REVOKE ALL ON TABLE public.jobs_archivedjob FROM authenticated;
                    GRANT SELECT ON TABLE public.jobs_archivedjob TO authenticated;

                    REVOKE ALL ON TABLE public.notifications_notification FROM authenticated;
                    GRANT SELECT ON TABLE public.notifications_notification TO authenticated;

                    REVOKE ALL ON TABLE public.resumes_resume FROM authenticated;
                    GRANT SELECT ON TABLE public.resumes_resume TO authenticated;

                    REVOKE ALL ON TABLE public.resumes_matchresult FROM authenticated;
                    GRANT SELECT ON TABLE public.resumes_matchresult TO authenticated;
                END IF;

                -- =====================================================================
                -- 11. Scoped Sequence Privileges:
                --     Django (postgres role) performs all business-data writes and
                --     retains full sequence ownership/privileges. Revoke sequence
                --     USAGE, SELECT, and UPDATE from Supabase client roles (anon,
                --     authenticated) strictly for the seven protected TalentMatch
                --     table sequences in schema public, without assuming all sequences
                --     in public belong to TalentMatch or touching backend roles.
                -- =====================================================================
                FOR r IN (
                    SELECT n.nspname AS seq_schema, s.relname AS seq_name
                    FROM pg_catalog.pg_class s
                    JOIN pg_catalog.pg_namespace n
                        ON n.oid = s.relnamespace
                    WHERE n.nspname = 'public'
                      AND s.relkind = 'S'
                      AND (
                          s.relname IN (
                              'accounts_userrole_id_seq',
                              'applications_application_id_seq',
                              'jobs_job_id_seq',
                              'jobs_archivedjob_id_seq',
                              'notifications_notification_id_seq',
                              'resumes_resume_id_seq',
                              'resumes_matchresult_id_seq'
                          )
                          OR EXISTS (
                              SELECT 1
                              FROM pg_catalog.pg_depend d
                              JOIN pg_catalog.pg_class t
                                  ON t.oid = d.refobjid
                              JOIN pg_catalog.pg_namespace tn
                                  ON tn.oid = t.relnamespace
                              WHERE d.classid = 'pg_catalog.pg_class'::regclass
                                AND d.objid = s.oid
                                AND d.refclassid = 'pg_catalog.pg_class'::regclass
                                AND d.deptype IN ('a', 'i')
                                AND tn.nspname = 'public'
                                AND t.relname IN (
                                    'accounts_userrole',
                                    'applications_application',
                                    'jobs_job',
                                    'jobs_archivedjob',
                                    'notifications_notification',
                                    'resumes_resume',
                                    'resumes_matchresult'
                                )
                          )
                      )
                ) LOOP
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                        EXECUTE format('REVOKE ALL ON SEQUENCE %%I.%%I FROM anon', r.seq_schema, r.seq_name);
                    END IF;
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                        EXECUTE format('REVOKE ALL ON SEQUENCE %%I.%%I FROM authenticated', r.seq_schema, r.seq_name);
                    END IF;
                END LOOP;

            ELSE
                RAISE NOTICE 'Skipping Supabase RLS configuration: "auth" schema does not exist in this database.';
            END IF;
        END
        $$;
        """
    )


def rollback_rls_policies(apps, schema_editor):
    """
    Security Safeguard: Rolling back this hardening migration is explicitly
    prohibited because reverting would disable Row Level Security on accounts_userrole
    or restore vulnerable user_metadata policies on core application tables.

    Recovery Procedure:
    To update or modify RLS policies, write and apply a new forward migration.
    """
    raise RuntimeError(
        "IrreversibleMigration: Rolling back 0002_harden_rls_trusted_roles is prohibited. "
        "Rolling back would strip RLS protection from accounts_userrole and expose sensitive tables. "
        "If policy adjustments are required, apply a forward migration."
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('jobs', '0005_jobs_rls'),
        ('applications', '0005_applications_rls'),
        ('notifications', '0002_notifications_rls'),
        ('resumes', '0005_resumes_rls'),
    ]

    operations = [
        migrations.RunPython(harden_rls_policies, reverse_code=rollback_rls_policies),
    ]
