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


class TemplateStage(models.TextChoices):
    SHORTLISTED = 'SHORTLISTED', 'Shortlisted'
    INTERVIEW = 'INTERVIEW', 'Interview'
    HIRED = 'HIRED', 'Hired'
    REJECTED = 'REJECTED', 'Rejected'
    SCREENING = 'SCREENING', 'Screening'
    GENERAL = 'GENERAL', 'General'


class CommunicationTemplate(models.Model):
    company = models.ForeignKey(
        'companies.Company',
        on_delete=models.CASCADE,
        related_name='communication_templates',
        help_text="Company owning this outreach template"
    )
    name = models.CharField(max_length=150)
    stage = models.CharField(
        max_length=30,
        choices=TemplateStage.choices,
        default=TemplateStage.GENERAL,
        db_index=True
    )
    subject = models.CharField(max_length=200)
    body = models.TextField(max_length=5000)
    is_default = models.BooleanField(default=False)
    created_by = models.CharField(max_length=150, blank=True, default='Recruiter')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['stage', 'name', '-created_at']

    def __str__(self):
        return f"{self.name} ({self.stage})"


DEFAULT_COMMUNICATION_TEMPLATES = [
    {
        'name': 'Candidate Shortlisted',
        'stage': TemplateStage.SHORTLISTED,
        'subject': 'Next steps regarding your application for {{job_title}}',
        'body': 'Hello {{candidate_name}},\n\nGreat news! We have reviewed your qualifications and are pleased to inform you that your application for {{job_title}} at {{company_name}} has been shortlisted.\n\nOur team is currently finalizing interview schedules and will reach out shortly with details on the next step.\n\nBest regards,\n{{recruiter_name}}\n{{company_name}}',
    },
    {
        'name': 'Interview Invitation',
        'stage': TemplateStage.INTERVIEW,
        'subject': 'Interview Invitation — {{job_title}} at {{company_name}}',
        'body': 'Hello {{candidate_name}},\n\nThank you for applying for {{job_title}} at {{company_name}}.\n\nWe were impressed by your background and would like to invite you for an interview. Please let us know your availability over the next few business days or reply with your preferred times.\n\nLooking forward to speaking with you.\n\nWarm regards,\n{{recruiter_name}}\n{{company_name}}',
    },
    {
        'name': 'Job Offer / Hired',
        'stage': TemplateStage.HIRED,
        'subject': 'Job Offer & Next Steps: {{job_title}} at {{company_name}}',
        'body': 'Dear {{candidate_name}},\n\nCongratulations! On behalf of {{company_name}}, we are thrilled to extend an offer for the position of {{job_title}}.\n\nThe team was exceptionally impressed with your skills, experience, and the conversations we shared throughout the selection process.\n\nPlease review the attached offer details and let us know if you have any questions.\n\nWelcome to the team!\n\nSincerely,\n{{recruiter_name}}\n{{company_name}}',
    },
    {
        'name': 'Application Update (Rejection)',
        'stage': TemplateStage.REJECTED,
        'subject': 'Update on your application for {{job_title}} at {{company_name}}',
        'body': 'Dear {{candidate_name}},\n\nThank you for taking the time to apply for {{job_title}} at {{company_name}} and for your interest in joining our team.\n\nAfter careful consideration and review of many qualified applicants, we have decided to move forward with other candidates whose experience more closely aligns with our current requirements.\n\nWe sincerely appreciate the time and effort you invested in your application and wish you the very best in your career pursuits.\n\nSincerely,\n{{recruiter_name}}\n{{company_name}}',
    },
]


def seed_default_templates(company, actor_name='System'):
    """
    Ensures the standard baseline templates exist for the given company.
    If a template with the same name already exists for the company, it is preserved.
    """
    created_list = []
    for item in DEFAULT_COMMUNICATION_TEMPLATES:
        template, created = CommunicationTemplate.objects.get_or_create(
            company=company,
            name=item['name'],
            defaults={
                'stage': item['stage'],
                'subject': item['subject'],
                'body': item['body'],
                'is_default': True,
                'created_by': actor_name,
            }
        )
        created_list.append(template)
    return created_list


