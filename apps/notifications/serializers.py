from rest_framework import serializers
from apps.notifications.models import Notification

class NotificationSerializer(serializers.ModelSerializer):
    job_title = serializers.SerializerMethodField()
    candidate_code = serializers.SerializerMethodField()
    applicant_name = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            'id',
            'recipient_type',
            'user_id',
            'application',
            'candidate_code',
            'applicant_name',
            'job',
            'job_title',
            'title',
            'message',
            'notification_type',
            'is_read',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_job_title(self, obj):
        if obj.job:
            return obj.job.title
        if obj.application and obj.application.job:
            return obj.application.job.title
        return None

    def get_candidate_code(self, obj):
        if obj.application:
            return obj.application.candidate_code
        return None

    def get_applicant_name(self, obj):
        if obj.application:
            return obj.application.applicant_name
        return None
