from django.db import migrations


def revoke_client_defaults(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
        roles = ', '.join(schema_editor.quote_name(name) for name, in cursor.fetchall())
        if not roles:
            return
        for object_type in ('TABLES', 'SEQUENCES', 'FUNCTIONS'):
            cursor.execute(
                f'ALTER DEFAULT PRIVILEGES IN SCHEMA public '
                f'REVOKE ALL PRIVILEGES ON {object_type} FROM {roles}'
            )
        # PostgreSQL grants EXECUTE to PUBLIC globally for new functions.
        # Per-schema defaults cannot subtract that implicit global grant.
        cursor.execute('ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC')


class Migration(migrations.Migration):
    dependencies = [('accounts', '0004_restrict_data_api')]
    operations = [migrations.RunPython(revoke_client_defaults)]
