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
    employment_type = models.CharField(max_length=50, default='Full-time')
    location = models.CharField(max_length=100, default='Manila / Remote')
    minimum_experience = models.CharField(max_length=50, default='1-2 years')
    education_requirement = models.CharField(max_length=150, default='BS Computer Science / IT or related field')
    required_skills = models.JSONField(default=list, help_text='List of required skills')
    preferred_skills = models.JSONField(default=list, help_text='List of preferred skills')
    status = models.CharField(
        max_length=20,
        choices=JobStatus.choices,
        default=JobStatus.PUBLISHED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.department})"
