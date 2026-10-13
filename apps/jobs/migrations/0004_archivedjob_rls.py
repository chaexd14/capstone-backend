from django.db import migrations


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            """
            ALTER TABLE jobs_archivedjob ENABLE ROW LEVEL SECURITY;
            DO $$
            BEGIN
                -- Only create Supabase-specific policy if auth schema exists
                IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth') THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'jobs_archivedjob' AND policyname = 'Allow recruiters to manage archived jobs'
                    ) THEN
                        CREATE POLICY "Allow recruiters to manage archived jobs"
                        ON jobs_archivedjob
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
            DROP POLICY IF EXISTS "Allow recruiters to manage archived jobs" ON jobs_archivedjob;
            ALTER TABLE jobs_archivedjob DISABLE ROW LEVEL SECURITY;
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ('jobs', '0003_archivedjob'),
    ]

    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
    ]
