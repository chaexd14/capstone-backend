from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('jobs', '0006_archivedjob_active_job'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='salary_min',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='job',
            name='salary_max',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='archivedjob',
            name='salary_min',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='archivedjob',
            name='salary_max',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
