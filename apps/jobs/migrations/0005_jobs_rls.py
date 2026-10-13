from django.db import migrations


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            """
            ALTER TABLE jobs_job ENABLE ROW LEVEL SECURITY;
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth') THEN
                    -- 1. Anyone can view published jobs
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'jobs_job' AND policyname = 'Allow public read of published jobs'
                    ) THEN
                        CREATE POLICY "Allow public read of published jobs"
                        ON jobs_job
                        FOR SELECT
                        USING (status = 'PUBLISHED');
                    END IF;

                    -- 2. Recruiters and superadmins can manage all jobs
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'jobs_job' AND policyname = 'Allow recruiters to manage jobs'
                    ) THEN
                        CREATE POLICY "Allow recruiters to manage jobs"
                        ON jobs_job
                        FOR ALL
                        TO authenticated
                        USING (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        )
                        WITH CHECK (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
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
            DROP POLICY IF EXISTS "Allow public read of published jobs" ON jobs_job;
            DROP POLICY IF EXISTS "Allow recruiters to manage jobs" ON jobs_job;
            ALTER TABLE jobs_job DISABLE ROW LEVEL SECURITY;
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0004_archivedjob_rls'),
    ]

    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
    ]
