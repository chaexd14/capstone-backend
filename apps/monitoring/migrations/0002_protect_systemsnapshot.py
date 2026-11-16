from django.db import migrations


TABLE = 'monitoring_systemsnapshot'


def protect_system_snapshots(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    quote = schema_editor.quote_name
    relation = f'public.{quote(TABLE)}'
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'talentmatch_runtime'")
        runtime_exists = cursor.fetchone() is not None
        cursor.execute(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')"
        )
        recipients = ', '.join(['PUBLIC', *(quote(role) for role, in cursor.fetchall())])
        cursor.execute(f'ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY')
        cursor.execute(f'ALTER TABLE {relation} FORCE ROW LEVEL SECURITY')
        cursor.execute(f'REVOKE ALL ON TABLE {relation} FROM {recipients}')
        if runtime_exists:
            cursor.execute(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {relation} TO talentmatch_runtime'
            )
            cursor.execute(
                f'CREATE POLICY talentmatch_runtime ON {relation} TO talentmatch_runtime '
                'USING (true) WITH CHECK (true)'
            )
            cursor.execute('SELECT pg_get_serial_sequence(%s, %s)', [relation, 'id'])
            sequence = cursor.fetchone()
            if sequence and sequence[0]:
                cursor.execute(f'GRANT USAGE, SELECT ON SEQUENCE {sequence[0]} TO talentmatch_runtime')


class Migration(migrations.Migration):
    dependencies = [('monitoring', '0001_initial')]
    operations = [
        migrations.RunPython(protect_system_snapshots, migrations.RunPython.noop),
    ]
