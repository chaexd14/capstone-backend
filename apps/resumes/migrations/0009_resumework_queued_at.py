from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('resumes', '0008_workerheartbeat')]

    operations = [
        migrations.AddField(
            model_name='resumework',
            name='queued_at',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
            preserve_default=False,
        ),
    ]
