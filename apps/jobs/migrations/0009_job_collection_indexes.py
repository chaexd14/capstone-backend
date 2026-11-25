from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('jobs', '0008_job_overview')]

    operations = [
        migrations.AddIndex(
            model_name='job',
            index=models.Index(fields=['company', 'status', '-created_at'], name='job_company_status_date_idx'),
        ),
        migrations.AddIndex(
            model_name='archivedjob',
            index=models.Index(fields=['company', '-archived_at'], name='arch_company_date_idx'),
        ),
    ]
