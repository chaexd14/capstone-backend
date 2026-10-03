from django.db import models
from apps.applications.models import Application

class ProcessingStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    PROCESSING = 'PROCESSING', 'Processing'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'

class Resume(models.Model):
    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name='resume')
    file = models.FileField(upload_to='resumes/%Y/%m/')
    original_filename = models.CharField(max_length=255)
    file_type = models.CharField(max_length=50, blank=True, default='')
    file_size = models.PositiveIntegerField(default=0)
    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING
    )
    extracted_text = models.TextField(blank=True, default='')
    extracted_skills = models.JSONField(default=list, blank=True)
    extracted_education = models.JSONField(default=list, blank=True)
    extracted_experience_years = models.FloatField(null=True, blank=True)
    redacted_text = models.TextField(blank=True, default='')
    redacted_data = models.JSONField(default=dict, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"Resume: {self.original_filename} ({self.processing_status})"

class MatchResult(models.Model):
    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name='match_result')
    match_score = models.FloatField(default=0.0, help_text='Overall percentage (0-100)')
    skill_match_score = models.FloatField(default=0.0, help_text='Required/Must-have skills fit')
    experience_match_score = models.FloatField(default=0.0, help_text='Relevant work experience fit')
    education_match_score = models.FloatField(default=0.0, help_text='Education level and credentials fit')
    semantic_match_score = models.FloatField(default=0.0, help_text='Contextual duties & responsibilities fit')
    preferred_skill_match_score = models.FloatField(default=0.0, help_text='Preferred/Nice-to-have skills fit')
    project_match_score = models.FloatField(default=0.0, help_text='Projects and achievement relevance')
    matched_skills = models.JSONField(default=list)
    missing_skills = models.JSONField(default=list)
    ai_insights = models.JSONField(default=dict, blank=True, help_text='Grounded AI Recruiter Insights JSON')
    explanation = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-match_score']

    def __str__(self):
        return f"Match: {self.application.candidate_code} -> {self.match_score:.1f}%"
