from django.db import models
from apps.companies.models import Company

class JobStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    PUBLISHED = 'PUBLISHED', 'Published'
    CLOSED = 'CLOSED', 'Closed'
    ARCHIVED = 'ARCHIVED', 'Archived'

class Job(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='jobs', null=True, blank=True)
    title = models.CharField(max_length=255)
    department = models.CharField(max_length=100)
    description = models.TextField()
    job_overview = models.TextField(null=True, blank=True)
    employment_type = models.CharField(max_length=50, default='Full-time')
    location = models.CharField(max_length=100, default='Manila / Remote')
    minimum_experience = models.CharField(max_length=50, default='1-2 years')
    education_requirement = models.CharField(max_length=150, default='BS Computer Science / IT or related field')
    required_skills = models.JSONField(default=list, help_text='List of required skills')
    preferred_skills = models.JSONField(default=list, help_text='List of preferred skills')
    salary_min = models.PositiveIntegerField(null=True, blank=True)
    salary_max = models.PositiveIntegerField(null=True, blank=True)
    rubric_weights = models.JSONField(
        default=dict,
        blank=True,
        help_text='Custom 5-component rubric percentages (required_skills, experience, education, preferred_skills, projects)'
    )
    status = models.CharField(
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.PUBLISHED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['company', 'status', '-created_at'], name='job_company_status_date_idx'),
        ]

    def __str__(self):
        return f"{self.title} ({self.department})"


class ArchivedJob(models.Model):
    active_job = models.OneToOneField(
        Job, null=True, blank=True, on_delete=models.CASCADE, related_name='archive_record',
    )
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, related_name='archived_jobs', null=True, blank=True)
    title = models.CharField(max_length=255)
    department = models.CharField(max_length=100)
    description = models.TextField()
    job_overview = models.TextField(null=True, blank=True)
    employment_type = models.CharField(max_length=50, default='Full-time')
    location = models.CharField(max_length=100, default='Manila / Remote')
    minimum_experience = models.CharField(max_length=50, default='1-2 years')
    education_requirement = models.CharField(max_length=150, default='BS Computer Science / IT or related field')
    required_skills = models.JSONField(default=list, help_text='List of required skills')
    preferred_skills = models.JSONField(default=list, help_text='List of preferred skills')
    salary_min = models.PositiveIntegerField(null=True, blank=True)
    salary_max = models.PositiveIntegerField(null=True, blank=True)
    rubric_weights = models.JSONField(
        default=dict,
        blank=True,
        help_text='Custom 5-component rubric percentages'
    )
    original_created_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-archived_at']
        indexes = [
            models.Index(fields=['company', '-archived_at'], name='arch_company_date_idx'),
        ]

    def __str__(self):
        return f"[ARCHIVED] {self.title} ({self.department})"

