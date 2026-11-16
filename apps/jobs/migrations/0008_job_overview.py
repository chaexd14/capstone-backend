from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('jobs', '0007_job_salary'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='job_overview',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='archivedjob',
            name='job_overview',
            field=models.TextField(blank=True, null=True),
        ),
    ]
