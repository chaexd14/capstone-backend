from rest_framework import serializers
from apps.jobs.models import Job, ArchivedJob
from apps.companies.models import Company

class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['id', 'name', 'description', 'logo', 'website', 'created_at']

class JobSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True, default='TalentMatch')
    applicant_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Job
        fields = [
            'id',
            'company',
            'company_name',
            'title',
            'department',
            'description',
            'employment_type',
            'location',
            'minimum_experience',
            'education_requirement',
            'required_skills',
            'preferred_skills',
            'rubric_weights',
            'status',
            'applicant_count',
            'created_at',
            'updated_at',
        ]

    def validate_rubric_weights(self, value):
        if not value:
            return value
        if not isinstance(value, dict):
            raise serializers.ValidationError("rubric_weights must be a dictionary.")

        keys = ["required_skills", "experience", "education", "preferred_skills", "projects"]
        total = 0
        cleaned = {}
        for k in keys:
            if k not in value:
                raise serializers.ValidationError(f"Missing required rubric component: '{k}'.")
            try:
                weight_val = float(value[k])
                if weight_val < 0:
                    raise ValueError
                total += weight_val
                cleaned[k] = weight_val
            except (TypeError, ValueError):
                raise serializers.ValidationError(f"Weight for '{k}' must be a non-negative number.")

        if round(total, 1) != 100.0 and round(total, 2) != 1.0:
            raise serializers.ValidationError(f"Rubric weights must sum to exactly 100% (current sum: {total}%).")

        # If recruiter sent decimals (e.g. 0.40, 0.25...), convert to 0-100 percentages
        if round(total, 2) == 1.0:
            cleaned = {k: round(v * 100, 1) for k, v in cleaned.items()}

        return cleaned


class ArchivedJobSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True, default='TalentMatch')

    class Meta:
        model = ArchivedJob
        fields = [
            'id',
            'company',
            'company_name',
            'title',
            'department',
            'description',
            'employment_type',
            'location',
            'minimum_experience',
            'education_requirement',
            'required_skills',
            'preferred_skills',
            'rubric_weights',
            'original_created_at',
            'archived_at',
        ]
