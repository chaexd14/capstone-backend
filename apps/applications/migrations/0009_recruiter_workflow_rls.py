from django.db import migrations


WORKFLOW_TABLES = (
    'applications_applicationactivity',
    'applications_candidatenote',
    'applications_recruitertask',
)


def protect_workflow_tables(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'talentmatch_runtime'")
        runtime_exists = cursor.fetchone() is not None
        for table in WORKFLOW_TABLES:
            relation = f'public.{quote(table)}'
            cursor.execute(f'ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE {relation} FORCE ROW LEVEL SECURITY')
            cursor.execute(f'REVOKE ALL ON {relation} FROM PUBLIC')
            if runtime_exists:
                cursor.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON {relation} TO talentmatch_runtime')
                cursor.execute(
                    f'CREATE POLICY talentmatch_runtime ON {relation} '
                    'TO talentmatch_runtime USING (true) WITH CHECK (true)'
                )
                cursor.execute('SELECT pg_get_serial_sequence(%s, %s)', [relation, 'id'])
                sequence = cursor.fetchone()[0]
                if sequence:
                    cursor.execute(f'GRANT USAGE ON SEQUENCE {sequence} TO talentmatch_runtime')


class Migration(migrations.Migration):
    dependencies = [
        ('applications', '0008_applicationactivity_candidatenote_recruitertask'),
        ('accounts', '0009_throttle_window'),
    ]

    operations = [migrations.RunPython(protect_workflow_tables, migrations.RunPython.noop)]
