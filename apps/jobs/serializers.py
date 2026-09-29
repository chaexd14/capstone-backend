from rest_framework import serializers
from apps.jobs.models import Job
from apps.companies.models import Company

class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ['id', 'name', 'description', 'logo', 'website', 'created_at']

class JobSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True, default='TalentMatch')
    applicant_count = serializers.IntegerField(source='applications.count', read_only=True)

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
            'status',
            'applicant_count',
            'created_at',
            'updated_at',
        ]
