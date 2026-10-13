from django.db import migrations


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(
            """
            ALTER TABLE notifications_notification ENABLE ROW LEVEL SECURITY;
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'auth') THEN
                    -- 1. Recruiters and superadmins can view and manage notifications
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'notifications_notification' AND policyname = 'Allow recruiters full access to notifications'
                    ) THEN
                        CREATE POLICY "Allow recruiters full access to notifications"
                        ON notifications_notification
                        FOR ALL
                        TO authenticated
                        USING (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        )
                        WITH CHECK (
                            ((auth.jwt() -> 'user_metadata'::text) ->> 'role'::text) = ANY (ARRAY['recruiter'::text, 'superadmin'::text])
                        );
                    END IF;

                    -- 2. Applicants can view their own notifications
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'notifications_notification' AND policyname = 'Allow applicants to view their own notifications'
                    ) THEN
                        CREATE POLICY "Allow applicants to view their own notifications"
                        ON notifications_notification
                        FOR SELECT
                        TO authenticated
                        USING (
                            user_id = auth.uid()
                        );
                    END IF;

                    -- 3. Applicants can update their own notifications (e.g. mark as read)
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_policies WHERE tablename = 'notifications_notification' AND policyname = 'Allow applicants to update their own notifications'
                    ) THEN
                        CREATE POLICY "Allow applicants to update their own notifications"
                        ON notifications_notification
                        FOR UPDATE
                        TO authenticated
                        USING (
                            user_id = auth.uid()
                        )
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
            DROP POLICY IF EXISTS "Allow recruiters full access to notifications" ON notifications_notification;
            DROP POLICY IF EXISTS "Allow applicants to view their own notifications" ON notifications_notification;
            DROP POLICY IF EXISTS "Allow applicants to update their own notifications" ON notifications_notification;
            ALTER TABLE notifications_notification DISABLE ROW LEVEL SECURITY;
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
    ]
