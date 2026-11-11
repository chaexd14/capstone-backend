from django.db import migrations


ROLE = 'talentmatch_runtime'
TABLE = 'public.jobs_job'


def grant_delete(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', [ROLE])
        if cursor.fetchone() is not None:
            cursor.execute(f'GRANT DELETE ON TABLE {TABLE} TO {ROLE}')


def revoke_delete(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', [ROLE])
        if cursor.fetchone() is not None:
            cursor.execute(f'REVOKE DELETE ON TABLE {TABLE} FROM {ROLE}')


class Migration(migrations.Migration):
    dependencies = [('accounts', '0009_throttle_window')]
    operations = [migrations.RunPython(grant_delete, reverse_code=revoke_delete)]
