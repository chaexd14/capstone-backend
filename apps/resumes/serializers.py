from rest_framework import serializers
from apps.resumes.models import Resume, MatchResult

class ResumeSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    is_purged = serializers.SerializerMethodField()

    class Meta:
        model = Resume
        fields = [
            'id',
            'application',
            'file',
            'file_url',
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

    def get_file_url(self, obj):
        if not obj.file:
            return None
        try:
            url = obj.file.url
            if url:
                request = self.context.get('request')
                if request and not (url.startswith('http://') or url.startswith('https://')):
                    return request.build_absolute_uri(url)
                return url
        except Exception:
            return None
        return None

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
