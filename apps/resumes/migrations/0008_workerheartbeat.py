from django.db import migrations, models


TABLE = 'resumes_workerheartbeat'
ACCESS = 'SELECT, INSERT, UPDATE, DELETE'


def protect_worker_heartbeat(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    quote = schema_editor.quote_name
    table = f'public.{quote(TABLE)}'
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')")
        recipients = ', '.join(['PUBLIC', *(quote(role) for role, in cursor.fetchall())])
        cursor.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        cursor.execute(f'REVOKE ALL ON TABLE {table} FROM {recipients}')
        cursor.execute(f'GRANT {ACCESS} ON TABLE {table} TO talentmatch_runtime')
        cursor.execute(
            f'CREATE POLICY talentmatch_runtime ON {table} TO talentmatch_runtime '
            'USING (true) WITH CHECK (true)'
        )


class Migration(migrations.Migration):
    dependencies = [('resumes', '0007_resume_scan_passed_resumework')]

    operations = [
        migrations.CreateModel(
            name='WorkerHeartbeat',
            fields=[
                ('name', models.CharField(default='resume-worker', max_length=64, primary_key=True, serialize=False)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
            ],
        ),
        migrations.RunPython(protect_worker_heartbeat, migrations.RunPython.noop),
    ]
