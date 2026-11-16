from django.db import models


class SystemSnapshot(models.Model):
    agent_id = models.CharField(max_length=64, default='primary-vm')
    cpu_percent = models.FloatField()
    memory_percent = models.FloatField()
    disk_percent = models.FloatField()
    uptime_seconds = models.PositiveBigIntegerField(default=0)
    api_service_up = models.BooleanField(default=True)
    worker_service_up = models.BooleanField(default=True)
    queue_depth = models.PositiveIntegerField(default=0)
    failed_work = models.PositiveIntegerField(default=0)
    database_latency_ms = models.FloatField(null=True)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-recorded_at']

