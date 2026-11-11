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
                fields=['job', 'user_id'],
                condition=models.Q(user_id__isnull=False),
                name='unique_application_job_user'
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


class CandidateNote(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='recruiter_notes')
    author_id = models.CharField(max_length=150)
    author_name = models.CharField(max_length=150, blank=True, default='Recruiter')
    body = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class RecruiterTask(models.Model):
    class Priority(models.TextChoices):
        LOW = 'LOW', 'Low'
        MEDIUM = 'MEDIUM', 'Medium'
        HIGH = 'HIGH', 'High'

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='recruiter_tasks')
    title = models.CharField(max_length=200)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    assigned_to = models.CharField(max_length=150, blank=True, default='')
    assigned_to_name = models.CharField(max_length=150, blank=True, default='')
    created_by = models.CharField(max_length=150)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['completed_at', 'due_at', '-created_at']


class ApplicationActivity(models.Model):
    class Kind(models.TextChoices):
        STATUS = 'STATUS', 'Status changed'
        NOTE = 'NOTE', 'Note added'
        TASK = 'TASK', 'Task updated'
        COMMUNICATION = 'COMMUNICATION', 'Communication recorded'

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='activity')
    kind = models.CharField(max_length=20, choices=Kind.choices)
    actor_id = models.CharField(max_length=150)
    actor_name = models.CharField(max_length=150, blank=True, default='Recruiter')
    summary = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
