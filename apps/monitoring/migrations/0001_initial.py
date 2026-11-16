from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name='SystemSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('agent_id', models.CharField(default='primary-vm', max_length=64)),
                ('cpu_percent', models.FloatField()),
                ('memory_percent', models.FloatField()),
                ('disk_percent', models.FloatField()),
                ('uptime_seconds', models.PositiveBigIntegerField(default=0)),
                ('api_service_up', models.BooleanField(default=True)),
                ('worker_service_up', models.BooleanField(default=True)),
                ('queue_depth', models.PositiveIntegerField(default=0)),
                ('failed_work', models.PositiveIntegerField(default=0)),
                ('database_latency_ms', models.FloatField(null=True)),
                ('recorded_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={'ordering': ['-recorded_at']},
        ),
    ]
