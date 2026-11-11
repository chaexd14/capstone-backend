import re
import logging
from django.conf import settings
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.resumes.upload_validation import validate_resume_format
from rest_framework import serializers

logger = logging.getLogger(__name__)
from apps.applications.models import Application, ApplicationActivity, CandidateNote, RecruiterTask
from apps.resumes.models import Resume, MatchResult
from apps.resumes.serializers import (
    ResumeSerializer,
    ApplicantResumeSerializer,
    RecruiterResumeSerializer,
)

NAME_PATTERN = re.compile(r"^[^\W\d_][\w\s'.-]*$", re.UNICODE)
PHONE_CHARS_PATTERN = re.compile(r"^[+\d\s().-]+$")


class MatchResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchResult
        fields = [
            'id',
            'match_score',
            'skill_match_score',
            'experience_match_score',
            'education_match_score',
            'semantic_match_score',
            'preferred_skill_match_score',
            'project_match_score',
            'matched_skills',
            'missing_skills',
            'ai_insights',
            'explanation',
            'created_at',
            'updated_at',
        ]


class ApplicantApplicationSerializer(serializers.ModelSerializer):
    """
    Applicant-safe serializer strictly excluding all AI match evaluations,
    scoring weights, internal recruiter notes, and presigned file URLs.
    """
    job_title = serializers.CharField(source='job.title', read_only=True)
    job_department = serializers.CharField(source='job.department', read_only=True)
    job_location = serializers.CharField(source='job.location', read_only=True)
    applicant_name = serializers.CharField(read_only=True)
    candidate_code = serializers.CharField(read_only=True)
    resume = ApplicantResumeSerializer(read_only=True)

    class Meta:
        model = Application
        fields = [
            'id',
            'job',
            'job_title',
            'job_department',
            'job_location',
            'candidate_code',
            'first_name',
            'last_name',
            'applicant_name',
            'email',
            'phone',
            'user_id',
            'status',
            'resume',
            'applied_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'job',
            'job_title',
            'job_department',
            'job_location',
            'candidate_code',
            'applicant_name',
            'email',
            'user_id',
            'status',
            'resume',
            'applied_at',
            'updated_at',
        ]


class RecruiterApplicationSerializer(serializers.ModelSerializer):
    """
    Full recruiter serializer with candidate dossier, AI scoring metrics,
    component sub-scores, insights, and administrative notes.
    """
    job_title = serializers.CharField(source='job.title', read_only=True)
    job_department = serializers.CharField(source='job.department', read_only=True)
    job_location = serializers.CharField(source='job.location', read_only=True)
    applicant_name = serializers.CharField(read_only=True)
    candidate_code = serializers.CharField(read_only=True)
    resume = RecruiterResumeSerializer(read_only=True)
    match_result = MatchResultSerializer(read_only=True)
    open_task_count = serializers.SerializerMethodField()
    latest_activity_at = serializers.SerializerMethodField()

    def get_open_task_count(self, obj):
        annotated = getattr(obj, '_open_task_count', None)
        if annotated is not None:
            return annotated
        return obj.recruiter_tasks.filter(completed_at__isnull=True).count()

    def get_latest_activity_at(self, obj):
        annotated = getattr(obj, '_latest_activity_at', None)
        if annotated is not None:
            return annotated
        return obj.activity.order_by('-created_at').values_list('created_at', flat=True).first()

    class Meta:
        model = Application
        fields = [
            'id',
            'tracking_token',
            'job',
            'job_title',
            'job_department',
            'job_location',
            'candidate_code',
            'first_name',
            'last_name',
            'applicant_name',
            'email',
            'phone',
            'user_id',
            'notes',
            'status',
            'resume',
            'match_result',
            'open_task_count',
            'latest_activity_at',
            'applied_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'job', 'user_id', 'tracking_token', 'applied_at', 'updated_at', 'candidate_code',
            'first_name', 'last_name', 'email', 'phone',
        ]


# Backward-compatible alias for existing imports
ApplicationSerializer = RecruiterApplicationSerializer


class CandidateNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CandidateNote
        fields = ['id', 'application', 'author_id', 'author_name', 'body', 'created_at', 'updated_at']
        read_only_fields = ['id', 'application', 'author_id', 'author_name', 'created_at', 'updated_at']


class RecruiterTaskSerializer(serializers.ModelSerializer):
    is_complete = serializers.SerializerMethodField()

    def get_is_complete(self, obj):
        return obj.completed_at is not None

    class Meta:
        model = RecruiterTask
        fields = [
            'id', 'application', 'title', 'priority', 'due_at', 'assigned_to',
            'assigned_to_name', 'created_by', 'completed_at', 'is_complete',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'application', 'created_by', 'completed_at', 'created_at', 'updated_at']


class ApplicationActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApplicationActivity
        fields = ['id', 'application', 'kind', 'actor_id', 'actor_name', 'summary', 'details', 'created_at']
        read_only_fields = fields


class ApplicationCreateSerializer(serializers.ModelSerializer):
    resume = serializers.FileField(write_only=True, required=True)
    phone = serializers.CharField(max_length=50, required=True, allow_blank=False)
    user_id = serializers.UUIDField(required=False, allow_null=True)
    tracking_token = serializers.UUIDField(read_only=True)

    class Meta:
        model = Application
        fields = [
            'id',
            'tracking_token',
            'job',
            'user_id',
            'first_name',
            'last_name',
            'email',
            'phone',
            'resume',
            'status',
            'applied_at',
        ]
        read_only_fields = ['id', 'tracking_token', 'status', 'applied_at']
        validators = []

    def _validate_person_name(self, value: str, label: str) -> str:
        cleaned = (value or '').strip()
        if len(cleaned) < 2:
            raise serializers.ValidationError(f"{label} must be at least 2 characters long.")
        if len(cleaned) > 100:
            raise serializers.ValidationError(f"{label} cannot exceed 100 characters.")
        if any(ch.isdigit() for ch in cleaned) or not NAME_PATTERN.match(cleaned):
            raise serializers.ValidationError(
                f"{label} can only contain letters, spaces, hyphens, and apostrophes."
            )
        return cleaned

    def validate_first_name(self, value):
        return self._validate_person_name(value, "First name")

    def validate_last_name(self, value):
        return self._validate_person_name(value, "Last name")

    def validate_email(self, value):
        cleaned = (value or '').strip().lower()
        if not cleaned:
            raise serializers.ValidationError("Email address is required.")
        return cleaned

    def validate_phone(self, value):
        cleaned = (value or '').strip()
        if not cleaned:
            raise serializers.ValidationError(
                "Phone number is required (for example, +63 917 123 4567)."
            )
        if not PHONE_CHARS_PATTERN.match(cleaned):
            raise serializers.ValidationError(
                "Phone number can only contain digits, spaces, '+', '-', and parentheses."
            )
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) < 7 or len(digits) > 15:
            raise serializers.ValidationError(
                "Enter a valid phone number containing 7 to 15 digits."
            )
        return cleaned

    def validate(self, attrs):
        job = attrs.get('job')
        user_id = attrs.get('user_id')

        if job and user_id:
            if Application.objects.filter(job=job, user_id=user_id).exists():
                raise serializers.ValidationError({
                    "detail": "You have already applied for this position with your account. Applicants can only submit one application per job."
                })

        return attrs

    def validate_resume(self, file_obj):
        if file_obj.size == 0:
            raise serializers.ValidationError(
                "The uploaded resume file is empty (0 bytes). Please upload a valid document."
            )

        max_size = getattr(settings, 'MAX_RESUME_UPLOAD_SIZE', 10 * 1024 * 1024)
        if file_obj.size > max_size:
            max_mb = max_size // (1024 * 1024)
            raise serializers.ValidationError(f"File size exceeds maximum limit of {max_mb}MB.")

        name = (file_obj.name or '').lower()
        if name.endswith('.doc'):
            raise serializers.ValidationError(
                "Legacy binary .doc files are not supported. Please save your resume as .docx or .pdf and upload again."
            )
        try:
            file_obj.content_type = validate_resume_format(file_obj, name)
        except ValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return file_obj

    @transaction.atomic
    def create(self, validated_data):
        from apps.resumes.tasks import cache_resume_payload

        resume_file = validated_data.pop('resume')
        application = Application.objects.create(**validated_data)

        uploaded_file_name = None
        storage_backend = None
        raw_bytes = None
        try:
            try:
                resume_file.seek(0)
                raw_bytes = resume_file.read()
                resume_file.seek(0)
            except Exception:
                raw_bytes = None

            resume = Resume(
                application=application,
                original_filename=resume_file.name,
                file_type=getattr(resume_file, 'content_type', 'application/octet-stream') or 'application/octet-stream',
                file_size=resume_file.size,
                processing_status='PENDING'
            )
            # Write physical file to storage independently before database persistence
            resume.file.save(resume_file.name, resume_file, save=False)
            uploaded_file_name = resume.file.name
            storage_backend = resume.file.storage

            # Persist record to database
            resume.save()
            if raw_bytes:
                cache_resume_payload(resume.pk, raw_bytes)
        except Exception as exc:
            # Compensation cleanup: If database persistence fails after physical storage write,
            # reliably delete the uploaded object from cloud storage to prevent orphaned files
            if uploaded_file_name and storage_backend:
                try:
                    storage_backend.delete(uploaded_file_name)
                except Exception as cleanup_err:
                    logger.error(
                        "Failed to clean up orphaned storage object %s: %s",
                        uploaded_file_name,
                        type(cleanup_err).__name__
                    )
            raise exc

        return application
