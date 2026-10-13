import uuid
from django.db import models
from apps.jobs.models import Job
from apps.applications.models import Application

class RecipientType(models.TextChoices):
    RECRUITER = 'RECRUITER', 'Recruiter'
    APPLICANT = 'APPLICANT', 'Applicant'

class NotificationType(models.TextChoices):
    NEW_APPLICATION = 'NEW_APPLICATION', 'New Application'
    STATUS_UPDATE = 'STATUS_UPDATE', 'Status Update'
    APPLICATION_SUBMITTED = 'APPLICATION_SUBMITTED', 'Application Submitted'

class Notification(models.Model):
    recipient_type = models.CharField(
        max_length=20,
        choices=RecipientType.choices,
        db_index=True
    )
    user_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Supabase auth.users UUID for specific applicant notifications"
    )
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications'
    )
    job = models.ForeignKey(
        Job,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications'
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
        default=NotificationType.NEW_APPLICATION
    )
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.recipient_type}] {self.title} - Read: {self.is_read}"
