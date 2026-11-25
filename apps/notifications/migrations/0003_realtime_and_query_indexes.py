from django.db import migrations, models


def configure_notification_realtime(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'authenticated'")
        if cursor.fetchone() is None:
            return

        cursor.execute(
            '''
            DROP POLICY IF EXISTS "Allow recruiters full access to notifications"
                ON public.notifications_notification;
            CREATE POLICY "Allow recruiters company read of notifications"
                ON public.notifications_notification
                FOR SELECT
                TO authenticated
                USING (
                    public.talentmatch_can_access_company(
                        (SELECT job.company_id FROM public.jobs_job job WHERE job.id = job_id)
                    )
                );

            DROP POLICY IF EXISTS "Allow applicants to update their own notifications"
                ON public.notifications_notification;
            GRANT SELECT ON TABLE public.notifications_notification TO authenticated;
            '''
        )

        cursor.execute("SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'")
        if cursor.fetchone() is not None:
            cursor.execute(
                "SELECT 1 FROM pg_publication_tables "
                "WHERE pubname = 'supabase_realtime' AND schemaname = 'public' "
                "AND tablename = 'notifications_notification'"
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    'ALTER PUBLICATION supabase_realtime '
                    'ADD TABLE public.notifications_notification'
                )


def irreversible(_apps, _schema_editor):
    raise RuntimeError(
        'Realtime access is security-sensitive and must be changed with a forward migration.'
    )


class Migration(migrations.Migration):
    dependencies = [
        ('applications', '0013_realtime_read_scope'),
        ('notifications', '0002_notifications_rls'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['recipient_type', 'user_id', 'is_read', '-created_at'],
                name='notif_user_unread_created_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='notification',
            index=models.Index(
                fields=['recipient_type', 'job', 'is_read', '-created_at'],
                name='notif_job_unread_created_idx',
            ),
        ),
        migrations.RunPython(configure_notification_realtime, reverse_code=irreversible),
    ]
