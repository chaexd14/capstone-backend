from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q

from apps.notifications.models import Notification, RecipientType
from apps.notifications.serializers import NotificationSerializer
from config.permissions import IsRecruiterOrSuperAdmin

class NotificationViewSet(viewsets.ModelViewSet):
    queryset = Notification.objects.select_related('application', 'job').all()
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        queryset = Notification.objects.select_related('application', 'job').all()
        if not user or not user.is_authenticated:
            return queryset.none()

        role = getattr(user, 'role', '').lower()
        is_recruiter = role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser
        user_uuid = getattr(user, 'supabase_id', str(user.username))

        unread_only = self.request.query_params.get('unread_only')

        if is_recruiter:
            recipient_type = self.request.query_params.get('recipient_type')
            if recipient_type:
                rt_upper = recipient_type.upper()
                if rt_upper == 'RECRUITER':
                    queryset = queryset.filter(recipient_type=RecipientType.RECRUITER)
                elif rt_upper == 'APPLICANT':
                    queryset = queryset.filter(recipient_type=RecipientType.APPLICANT)
            else:
                queryset = queryset.filter(
                    Q(recipient_type=RecipientType.RECRUITER) | Q(user_id=user_uuid)
                )
        else:
            # Applicants can only view their own notifications
            queryset = queryset.filter(recipient_type=RecipientType.APPLICANT, user_id=user_uuid)

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

    @action(detail=True, methods=['patch'], url_path='read')
    def mark_read(self, request, pk=None):
        instance = self.get_object()
        user = request.user
        role = getattr(user, 'role', '').lower()
        is_recruiter = role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser
        user_uuid = getattr(user, 'supabase_id', str(user.username))

        if not is_recruiter and str(instance.user_id) != str(user_uuid):
            return Response({"detail": "Permission denied."}, status=status.HTTP_403_FORBIDDEN)

        instance.is_read = True
        instance.save(update_fields=['is_read'])
        serializer = self.get_serializer(instance)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        user = request.user
        role = getattr(user, 'role', '').lower()
        is_recruiter = role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser
        user_uuid = getattr(user, 'supabase_id', str(user.username))

        queryset = Notification.objects.filter(is_read=False)
        if is_recruiter:
            queryset = queryset.filter(
                Q(recipient_type=RecipientType.RECRUITER) | Q(user_id=user_uuid)
            )
        else:
            queryset = queryset.filter(recipient_type=RecipientType.APPLICANT, user_id=user_uuid)

        count = queryset.update(is_read=True)
        return Response({'success': True, 'marked_read_count': count}, status=status.HTTP_200_OK)
