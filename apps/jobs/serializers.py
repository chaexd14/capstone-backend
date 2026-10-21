from rest_framework import serializers
from apps.jobs.models import Job, ArchivedJob, JobStatus
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

    def validate_title(self, value):
        cleaned = (value or '').strip()
        if len(cleaned) < 3:
            raise serializers.ValidationError("Job title must be at least 3 characters long.")
        return cleaned

    def validate_department(self, value):
        cleaned = (value or '').strip()
        if len(cleaned) < 2:
            raise serializers.ValidationError("Department must be at least 2 characters long.")
        return cleaned

    def validate_location(self, value):
        cleaned = (value or '').strip()
        if len(cleaned) < 2:
            raise serializers.ValidationError("Location must be at least 2 characters long.")
        return cleaned

    def validate_description(self, value):
        cleaned = (value or '').strip()
        if len(cleaned) < 20:
            raise serializers.ValidationError(
                "Job description must be at least 20 characters long to support AI qualification matching."
            )
        return cleaned

    def _clean_qualification_list(self, value, field_label):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError(f"{field_label} must be a list of qualification strings.")
        cleaned = []
        seen = set()
        for item in value:
            text = str(item).strip()
            # Strip leading bullet characters if pasted
            while text and text[0] in "•●▪▸-*":
                text = text[1:].strip()
            if not text:
                continue
            if len(text) > 300:
                raise serializers.ValidationError(
                    f"Each qualification item in {field_label} cannot exceed 300 characters."
                )
            lower = text.lower()
            if lower not in seen:
                seen.add(lower)
                cleaned.append(text)
        return cleaned

    def validate_required_skills(self, value):
        return self._clean_qualification_list(value, "required_skills")

    def validate_preferred_skills(self, value):
        return self._clean_qualification_list(value, "preferred_skills")

    def validate(self, attrs):
        status_val = attrs.get('status')
        if self.instance and status_val is None:
            status_val = self.instance.status
        elif status_val is None:
            status_val = JobStatus.PUBLISHED

        req_skills = attrs.get('required_skills')
        if req_skills is None and self.instance:
            req_skills = self.instance.required_skills

        if status_val == JobStatus.PUBLISHED and (not req_skills or len(req_skills) == 0):
            raise serializers.ValidationError({
                "required_skills": "Add at least one required skill before publishing a job requisition."
            })

        return attrs

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
