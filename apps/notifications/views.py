from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.db.models import Q

from apps.notifications.models import Notification, RecipientType
from apps.notifications.serializers import NotificationSerializer

class NotificationViewSet(viewsets.ModelViewSet):
    queryset = Notification.objects.select_related('application', 'job').all()
    serializer_class = NotificationSerializer
    permission_classes = [AllowAny]
    authentication_classes = []

    def get_queryset(self):
        queryset = Notification.objects.select_related('application', 'job').all()
        recipient_type = self.request.query_params.get('recipient_type')
        user_id = self.request.query_params.get('user_id')
        unread_only = self.request.query_params.get('unread_only')

        if recipient_type:
            rt_upper = recipient_type.upper()
            if rt_upper == 'RECRUITER':
                queryset = queryset.filter(recipient_type=RecipientType.RECRUITER)
            elif rt_upper == 'APPLICANT':
                queryset = queryset.filter(recipient_type=RecipientType.APPLICANT)
                if user_id:
                    queryset = queryset.filter(user_id=user_id)

        elif user_id:
            queryset = queryset.filter(
                Q(user_id=user_id) | Q(recipient_type=RecipientType.RECRUITER)
            )

        if unread_only and unread_only.lower() in ('true', '1'):
            queryset = queryset.filter(is_read=False)

        return queryset.order_by('-created_at')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        unread_count = queryset.filter(is_read=False).count()

        # Limit payload to most recent 50 notifications for quick network transfer
        limited_queryset = queryset[:50]
        serializer = self.get_serializer(limited_queryset, many=True)

        return Response({
            'unread_count': unread_count,
            'notifications': serializer.data
        })

    @action(detail=True, methods=['patch'], url_path='read')
    def mark_read(self, request, pk=None):
        instance = self.get_object()
        instance.is_read = True
        instance.save(update_fields=['is_read'])
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        recipient_type = request.data.get('recipient_type') or request.query_params.get('recipient_type')
        user_id = request.data.get('user_id') or request.query_params.get('user_id')

        queryset = Notification.objects.filter(is_read=False)
        if recipient_type:
            rt_upper = recipient_type.upper()
            if rt_upper == 'RECRUITER':
                queryset = queryset.filter(recipient_type=RecipientType.RECRUITER)
            elif rt_upper == 'APPLICANT':
                queryset = queryset.filter(recipient_type=RecipientType.APPLICANT)
                if user_id:
                    queryset = queryset.filter(user_id=user_id)
        elif user_id:
            queryset = queryset.filter(user_id=user_id)

        count = queryset.update(is_read=True)
        return Response({'success': True, 'marked_read_count': count}, status=status.HTTP_200_OK)
