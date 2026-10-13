from django.db import migrations


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            """
            ALTER TABLE applications_application ENABLE ROW LEVEL SECURITY;
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth') THEN
                    -- 1. Recruiters and superadmins can view and manage all applications
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'applications_application' AND policyname = 'Allow recruiters full access to applications'
                    ) THEN
                        CREATE POLICY "Allow recruiters full access to applications"
                        ON applications_application
                        FOR ALL
                        TO authenticated
                        USING (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        )
                        WITH CHECK (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        );
                    END IF;

                    -- 2. Applicants can read their own applications
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'applications_application' AND policyname = 'Allow applicants to view their own applications'
                    ) THEN
                        CREATE POLICY "Allow applicants to view their own applications"
                        ON applications_application
                        FOR SELECT
                        TO authenticated
                        USING (
                            user_id = auth.uid()
                        );
                    END IF;

                    -- 3. Applicants can insert their own application
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'applications_application' AND policyname = 'Allow applicants to insert their own applications'
                    ) THEN
                        CREATE POLICY "Allow applicants to insert their own applications"
                        ON applications_application
                        FOR INSERT
                        TO authenticated
                        WITH CHECK (
                            user_id = auth.uid()
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
            DROP POLICY IF EXISTS "Allow recruiters full access to applications" ON applications_application;
            DROP POLICY IF EXISTS "Allow applicants to view their own applications" ON applications_application;
            DROP POLICY IF EXISTS "Allow applicants to insert their own applications" ON applications_application;
            ALTER TABLE applications_application DISABLE ROW LEVEL SECURITY;
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ('applications', '0004_application_unique_application_job_email'),
    ]

    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
    ]
