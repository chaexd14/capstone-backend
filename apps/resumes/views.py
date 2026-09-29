from rest_framework import viewsets
from rest_framework.permissions import AllowAny
from apps.resumes.models import Resume
from apps.resumes.serializers import ResumeSerializer

class ResumeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Resume.objects.all()
    serializer_class = ResumeSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
