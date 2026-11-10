from django.db import migrations, models


TABLE = 'accounts_throttlewindow'
ACCESS = 'SELECT, INSERT, UPDATE, DELETE'


def protect_throttle_windows(apps, schema_editor):
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
        cursor.execute(f'CREATE POLICY talentmatch_runtime ON {table} TO talentmatch_runtime USING (true) WITH CHECK (true)')


class Migration(migrations.Migration):
    dependencies = [('accounts', '0008_restricted_database_runtime')]

    operations = [
        migrations.CreateModel(
            name='ThrottleWindow',
            fields=[
                ('bucket_key', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('hits', models.PositiveIntegerField()),
                ('expires_at', models.DateTimeField(db_index=True)),
            ],
        ),
        migrations.RunPython(protect_throttle_windows, migrations.RunPython.noop),
    ]
