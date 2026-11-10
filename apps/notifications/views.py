from uuid import UUID

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q

from apps.notifications.models import Notification, RecipientType
from apps.notifications.serializers import NotificationSerializer
from config.permissions import is_recruiter, recruiter_company_id

class NotificationViewSet(viewsets.ModelViewSet):
    queryset = Notification.objects.select_related('application', 'job').all()
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        queryset = Notification.objects.select_related('application', 'job').all()
        if not user or not user.is_authenticated:
            return queryset.none()

        try:
            user_uuid = UUID(str(getattr(user, 'supabase_id', user.username)))
        except (ValueError, TypeError):
            user_uuid = None

        own = Q(recipient_type=RecipientType.APPLICANT, user_id=user_uuid) if user_uuid else Q(pk__in=[])
        recruiter = Q(recipient_type=RecipientType.RECRUITER)
        if is_recruiter(user) and recruiter_company_id(user) is not None:
            allowed = (recruiter & Q(job__company_id=recruiter_company_id(user))) | own
        else:
            allowed = own
        queryset = queryset.filter(allowed)

        recipient_type = self.request.query_params.get('recipient_type')
        if recipient_type:
            queryset = queryset.filter(recipient_type=recipient_type.upper())
        unread_only = self.request.query_params.get('unread_only')
        if unread_only and unread_only.lower() in ('true', '1'):
            queryset = queryset.filter(is_read=False)

        return queryset.order_by('-created_at')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        unread_count = queryset.filter(is_read=False).count()

        limited_queryset = queryset[:50]
        serializer = self.get_serializer(limited_queryset, many=True)

        return Response({
            'unread_count': unread_count,
            'notifications': serializer.data
        })

    def create(self, request, *args, **kwargs):
        return Response(
            {"detail": "Direct creation of notifications via the public API is disabled."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

    def update(self, request, *args, **kwargs):
        return Response(
            {"detail": "Method \"PUT\" not allowed. Use the /read/ action or PATCH with {'is_read': true}."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

    def partial_update(self, request, *args, **kwargs):
        allowed_fields = {'is_read'}
        submitted_fields = set(request.data.keys())
        disallowed_fields = submitted_fields - allowed_fields
        if disallowed_fields:
            return Response(
                {"detail": f"Only the 'is_read' field can be updated on notifications. Disallowed fields: {list(disallowed_fields)}."},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=['patch', 'post'], url_path='read')
    def mark_read(self, request, pk=None):
        instance = self.get_object()
        instance.is_read = True
        instance.save(update_fields=['is_read'])
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post', 'patch'], url_path='mark-all-read')
    def mark_all_read(self, request):
        queryset = self.get_queryset().filter(is_read=False)
        recipient_type = request.data.get('recipient_type')
        if isinstance(recipient_type, str) and recipient_type:
            queryset = queryset.filter(recipient_type=recipient_type.upper())
        count = queryset.update(is_read=True)
        return Response({'success': True, 'marked_read_count': count}, status=status.HTTP_200_OK)
