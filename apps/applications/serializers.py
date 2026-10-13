from rest_framework import serializers
from apps.applications.models import Application
from apps.resumes.models import Resume, MatchResult
from apps.resumes.serializers import ResumeSerializer

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

class ApplicationSerializer(serializers.ModelSerializer):
    job_title = serializers.CharField(source='job.title', read_only=True)
    job_department = serializers.CharField(source='job.department', read_only=True)
    job_location = serializers.CharField(source='job.location', read_only=True)
    applicant_name = serializers.CharField(read_only=True)
    candidate_code = serializers.CharField(read_only=True)
    resume = ResumeSerializer(read_only=True)
    match_result = MatchResultSerializer(read_only=True)

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
            'applied_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'tracking_token', 'applied_at', 'updated_at', 'candidate_code']

class ApplicationCreateSerializer(serializers.ModelSerializer):
    resume = serializers.FileField(write_only=True, required=True)
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

    def validate(self, attrs):
        job = attrs.get('job')
        email = (attrs.get('email') or '').strip().lower()
        user_id = attrs.get('user_id')

        if job and email:
            if Application.objects.filter(job=job, email__iexact=email).exists():
                raise serializers.ValidationError({
                    "detail": "You have already applied for this position. Applicants can only submit one application per job."
                })

        if job and user_id:
            if Application.objects.filter(job=job, user_id=user_id).exists():
                raise serializers.ValidationError({
                    "detail": "You have already applied for this position with your account. Applicants can only submit one application per job."
                })

        return attrs

    def validate_resume(self, file_obj):
        max_size = 10 * 1024 * 1024
        if file_obj.size > max_size:
            raise serializers.ValidationError("File size exceeds maximum limit of 10MB.")

        name = file_obj.name.lower()
        if not (name.endswith('.pdf') or name.endswith('.docx') or name.endswith('.doc') or name.endswith('.txt')):
            raise serializers.ValidationError("Only .pdf, .docx, and .txt files are accepted.")
        return file_obj

    def create(self, validated_data):
        resume_file = validated_data.pop('resume')
        application = Application.objects.create(**validated_data)

        Resume.objects.create(
            application=application,
            file=resume_file,
            original_filename=resume_file.name,
            file_type=resume_file.content_type or 'application/octet-stream',
            file_size=resume_file.size,
            processing_status='PENDING'
        )

        return application
