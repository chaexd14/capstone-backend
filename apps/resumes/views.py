from django.http import HttpResponseRedirect
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from apps.resumes.models import Resume
from apps.resumes.serializers import ResumeSerializer
from config.permissions import IsResumeOwnerOrRecruiter

from django.db.models import Q

class ResumeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ResumeSerializer
    permission_classes = [IsResumeOwnerOrRecruiter]

    def get_permissions(self):
        token = self.request.query_params.get('tracking_token') or self.request.headers.get('X-Tracking-Token')
        if token and self.action in ('download', 'retrieve'):
            return [IsResumeOwnerOrRecruiter()]
        return [IsAuthenticated(), IsResumeOwnerOrRecruiter()]

    def get_queryset(self):
        user = self.request.user
        queryset = Resume.objects.select_related('application').all()
        token = self.request.query_params.get('tracking_token') or self.request.headers.get('X-Tracking-Token')

        if not user or not user.is_authenticated:
            if token and self.action in ('download', 'retrieve'):
                return queryset.filter(application__tracking_token=token)
            return queryset.none()

        role = getattr(user, 'role', '').lower()
        is_recruiter = role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser

        if is_recruiter:
            return queryset

        # Applicants are strictly restricted to their own resumes
        user_uuid = getattr(user, 'supabase_id', str(user.username))
        if token:
            return queryset.filter(Q(application__user_id=user_uuid) | Q(application__tracking_token=token))
        return queryset.filter(application__user_id=user_uuid)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """
        Download endpoint strictly protected for applicant owner and authenticated recruiters.
        Generates a fresh pre-signed redirect URL to Cloudflare R2 / media storage.
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
