from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('applications', '0006_application_unique_application_job_user')]

    operations = [
        migrations.RemoveConstraint(model_name='application', name='unique_application_job_email'),
    ]
