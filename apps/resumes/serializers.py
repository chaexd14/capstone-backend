from rest_framework import serializers
from apps.resumes.models import Resume, MatchResult

class ApplicantResumeSerializer(serializers.ModelSerializer):
    is_purged = serializers.SerializerMethodField()

    class Meta:
        model = Resume
        fields = [
            'id',
            'original_filename',
            'file_type',
            'file_size',
            'processing_status',
            'is_purged',
            'uploaded_at',
        ]
        read_only_fields = ['id', 'uploaded_at']

    def get_is_purged(self, obj):
        return obj.processing_status == 'PURGED' or not bool(obj.file)


class RecruiterResumeSerializer(serializers.ModelSerializer):
    is_purged = serializers.SerializerMethodField()

    class Meta:
        model = Resume
        fields = [
            'id',
            'application',
            'is_purged',
            'original_filename',
            'file_type',
            'file_size',
            'processing_status',
            'extracted_text',
            'extracted_skills',
            'extracted_education',
            'extracted_experience_years',
            'redacted_text',
            'redacted_data',
            'uploaded_at',
            'processed_at',
        ]
        read_only_fields = ['id', 'uploaded_at', 'processed_at']

    def get_is_purged(self, obj):
        return obj.processing_status == 'PURGED' or not bool(obj.file)


# Backward-compatible alias for existing imports
ResumeSerializer = RecruiterResumeSerializer

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
