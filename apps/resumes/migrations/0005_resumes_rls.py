from django.db import migrations


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            """
            ALTER TABLE resumes_resume ENABLE ROW LEVEL SECURITY;
            ALTER TABLE resumes_matchresult ENABLE ROW LEVEL SECURITY;
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth') THEN
                    -- 1. Resumes: Recruiters full access
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'resumes_resume' AND policyname = 'Allow recruiters full access to resumes'
                    ) THEN
                        CREATE POLICY "Allow recruiters full access to resumes"
                        ON resumes_resume
                        FOR ALL
                        TO authenticated
                        USING (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        )
                        WITH CHECK (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        );
                    END IF;

                    -- 2. Resumes: Applicants can view own resume
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'resumes_resume' AND policyname = 'Allow applicants to view their own resume'
                    ) THEN
                        CREATE POLICY "Allow applicants to view their own resume"
                        ON resumes_resume
                        FOR SELECT
                        TO authenticated
                        USING (
                            application_id IN (
                                SELECT id FROM applications_application WHERE user_id = auth.uid()
                            )
                        );
                    END IF;

                    -- 3. Resumes: Applicants can insert own resume
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'resumes_resume' AND policyname = 'Allow applicants to insert their own resume'
                    ) THEN
                        CREATE POLICY "Allow applicants to insert their own resume"
                        ON resumes_resume
                        FOR INSERT
                        TO authenticated
                        WITH CHECK (
                            application_id IN (
                                SELECT id FROM applications_application WHERE user_id = auth.uid()
                            )
                        );
                    END IF;

                    -- 4. Match Results: Recruiters full access
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'resumes_matchresult' AND policyname = 'Allow recruiters full access to match results'
                    ) THEN
                        CREATE POLICY "Allow recruiters full access to match results"
                        ON resumes_matchresult
                        FOR ALL
                        TO authenticated
                        USING (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        )
                        WITH CHECK (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        );
                    END IF;

                    -- 5. Match Results: Applicants can view own match results
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'resumes_matchresult' AND policyname = 'Allow applicants to view their own match results'
                    ) THEN
                        CREATE POLICY "Allow applicants to view their own match results"
                        ON resumes_matchresult
                        FOR SELECT
                        TO authenticated
                        USING (
                            application_id IN (
                                SELECT id FROM applications_application WHERE user_id = auth.uid()
                            )
                        );
                    END IF;
                END IF;
            END
            $$;
            """
        )


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            """
            DROP POLICY IF EXISTS "Allow recruiters full access to resumes" ON resumes_resume;
            DROP POLICY IF EXISTS "Allow applicants to view their own resume" ON resumes_resume;
            DROP POLICY IF EXISTS "Allow applicants to insert their own resume" ON resumes_resume;
            DROP POLICY IF EXISTS "Allow recruiters full access to match results" ON resumes_matchresult;
            DROP POLICY IF EXISTS "Allow applicants to view their own match results" ON resumes_matchresult;
            ALTER TABLE resumes_resume DISABLE ROW LEVEL SECURITY;
            ALTER TABLE resumes_matchresult DISABLE ROW LEVEL SECURITY;
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ('resumes', '0004_alter_resume_file_alter_resume_processing_status'),
    ]

    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
    ]
