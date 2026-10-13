import uuid
from django.db import models
from apps.jobs.models import Job

class ApplicationStatus(models.TextChoices):
    SUBMITTED = 'SUBMITTED', 'Submitted'
    PROCESSING = 'PROCESSING', 'Processing'
    UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
    SHORTLISTED = 'SHORTLISTED', 'Shortlisted'
    INTERVIEW = 'INTERVIEW', 'Interview'
    REJECTED = 'REJECTED', 'Rejected'
    HIRED = 'HIRED', 'Hired'

class Application(models.Model):
    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name='applications')
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=50, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    status = models.CharField(
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.SUBMITTED
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    user_id = models.UUIDField(null=True, blank=True, db_index=True, help_text="Supabase auth.users UUID")
    tracking_token = models.UUIDField(default=uuid.uuid4, null=True, db_index=True, editable=False, help_text="Secure token for applicant polling and verification")

    class Meta:
        ordering = ['-applied_at']
        constraints = [
            models.UniqueConstraint(
                fields=['job', 'email'],
                name='unique_application_job_email'
            ),
        ]

    @property
    def candidate_code(self):
        return f"TM-{self.id:04d}"

    @property
    def applicant_name(self):
        return f"{self.first_name} {self.last_name}"

    def __str__(self):
        return f"{self.candidate_code} ({self.applicant_name}) - {self.job.title}"
