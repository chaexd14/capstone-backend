from django.http import HttpResponseRedirect
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from apps.resumes.models import Resume
from apps.resumes.serializers import ResumeSerializer

class ResumeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Resume.objects.all()
    serializer_class = ResumeSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """
        Download endpoint for applicants and recruiters.
        Generates a fresh pre-signed redirect URL to Cloudflare R2.
        If the job was closed and the file purged, returns HTTP 410 Gone.
        """
        resume = self.get_object()
        if not resume.file:
            return Response(
                {'error': 'Resume file has been deleted or purged in accordance with candidate data privacy policy.'},
                status=status.HTTP_410_GONE
            )
        try:
            return HttpResponseRedirect(resume.file.url)
        except Exception as e:
            return Response({'error': f'Failed to retrieve file download URL: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
