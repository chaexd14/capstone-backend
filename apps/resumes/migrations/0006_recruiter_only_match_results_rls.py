from django.db import migrations


def enforce_recruiter_only_match_results(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    schema_editor.execute(
        """
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            -- Apply RLS hardening if auth schema exists (Supabase PostgreSQL environment)
            IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth') THEN

                -- 1. Ensure RLS is active on resumes_matchresult
                ALTER TABLE public.resumes_matchresult ENABLE ROW LEVEL SECURITY;

                -- 2. Drop all existing policies on resumes_matchresult to prevent OR-composition
                FOR r IN (
                    SELECT schemaname, tablename, policyname
                    FROM pg_policies
                    WHERE schemaname = 'public'
                      AND tablename = 'resumes_matchresult'
                ) LOOP
                    EXECUTE format('DROP POLICY IF EXISTS %%I ON %%I.%%I', r.policyname, r.schemaname, r.tablename);
                END LOOP;

                DROP POLICY IF EXISTS "Allow applicants to view their own match results" ON public.resumes_matchresult;
                DROP POLICY IF EXISTS "Applicants can view own match results" ON public.resumes_matchresult;

                -- 3. Drop legacy recruiter policies referencing user_metadata
                DROP POLICY IF EXISTS "Recruiters can view all match results" ON public.resumes_matchresult;
                DROP POLICY IF EXISTS "Allow recruiters to view match results" ON public.resumes_matchresult;

                -- 4. Ensure authoritative recruiter policy exists using is_recruiter_or_superadmin()
                CREATE POLICY "Allow recruiters full access to match results"
                ON public.resumes_matchresult
                FOR ALL
                TO authenticated
                USING (public.is_recruiter_or_superadmin())
                WITH CHECK (public.is_recruiter_or_superadmin());

                -- 5. Revoke all table privileges from anon role on resumes_matchresult
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    REVOKE ALL ON TABLE public.resumes_matchresult FROM anon;
                END IF;

                -- 6. Restrict authenticated role table privileges to SELECT only
                --    (Direct INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER via PostgREST is disallowed)
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    REVOKE ALL ON TABLE public.resumes_matchresult FROM authenticated;
                    GRANT SELECT ON TABLE public.resumes_matchresult TO authenticated;
                END IF;

            ELSE
                RAISE NOTICE 'Skipping Supabase RLS configuration: "auth" schema does not exist in this database.';
            END IF;
        END
        $$;
        """
    )


def rollback_recruiter_only_match_results(apps, schema_editor):
    """
    Security Safeguard: Rolling back recruiter-only match result restrictions is prohibited.
    Reverting would grant table privileges to untrusted roles or re-expose match results.
    Apply a forward migration if policy adjustments are required.
    """
    raise RuntimeError(
        "IrreversibleMigration: Rolling back 0006_recruiter_only_match_results_rls is prohibited. "
        "Rolling back would compromise candidate evaluation privacy and grant untrusted table privileges. "
        "Apply a forward migration if adjustments are needed."
    )


class Migration(migrations.Migration):

    dependencies = [
        ('resumes', '0005_resumes_rls'),
        ('accounts', '0002_harden_rls_trusted_roles'),
    ]

    operations = [
        migrations.RunPython(
            enforce_recruiter_only_match_results,
            reverse_code=rollback_recruiter_only_match_results
        ),
    ]
