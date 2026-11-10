from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.accounts.models import RoleChoices, StaffInvitation, UserRole, record_security_event
from apps.accounts.staff_invitations import SendStaffInvitationSerializer, StaffInvitationSerializer, invite_staff
from apps.companies.models import Company
from config.permissions import is_platform_admin


class PlatformAdminOnly(BasePermission):
    def has_permission(self, request, view):
        return is_platform_admin(request.user)


class CompanyManagementSerializer(serializers.ModelSerializer):
    description = serializers.CharField(max_length=2000, allow_blank=True, required=False)

    class Meta:
        model = Company
        fields = ['id', 'name', 'description', 'website']


class StaffManagementSerializer(serializers.ModelSerializer):
    company_name = serializers.CharField(source='company.name', read_only=True, default=None)
    active = serializers.BooleanField(source='user.is_active', read_only=True, default=True)

    class Meta:
        model = UserRole
        fields = ['id', 'email', 'company', 'company_name', 'active', 'created_at']
        read_only_fields = fields


class UpdateStaffSerializer(serializers.Serializer):
    company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all(), required=False, allow_null=True)
    active = serializers.BooleanField(required=False)


class PrivatePlatformViewSet(viewsets.GenericViewSet):
    permission_classes = [PlatformAdminOnly]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store'
        return response


class CompanyManagementViewSet(PrivatePlatformViewSet, viewsets.ModelViewSet):
    queryset = Company.objects.order_by('name', 'pk')
    serializer_class = CompanyManagementSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    @transaction.atomic
    def perform_create(self, serializer):
        company = serializer.save()
        record_security_event(self.request.user, 'company.create', company.pk)

    @transaction.atomic
    def perform_update(self, serializer):
        company = serializer.save()
        record_security_event(self.request.user, 'company.update', company.pk)


class StaffManagementViewSet(PrivatePlatformViewSet, viewsets.ModelViewSet):
    queryset = UserRole.objects.filter(role=RoleChoices.RECRUITER).select_related('company', 'user')
    serializer_class = StaffManagementSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action != 'list':
            return queryset
        company = self.request.query_params.get('company')
        if company == 'unassigned':
            return queryset.filter(company__isnull=True)
        if company:
            if not company.isascii() or not company.isdecimal() or len(company) > 18:
                raise ValidationError({'company': 'Choose a valid company.'})
            return queryset.filter(company_id=int(company))
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = SendStaffInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation, sent = invite_staff(request.user, **serializer.validated_data)
        return Response(StaffInvitationSerializer(invitation).data, status=201 if sent else 200)

    @action(detail=False, methods=['get'])
    def invitations(self, request):
        company = request.query_params.get('company', '')
        if not company.isascii() or not company.isdecimal() or len(company) > 18:
            raise ValidationError({'company': 'Choose a valid company.'})
        invitations = StaffInvitation.objects.filter(company_id=int(company)).exclude(
            state=StaffInvitation.State.ACCEPTED
        ).order_by('email')
        return Response(StaffInvitationSerializer(invitations, many=True).data)

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        staff = get_object_or_404(self.get_queryset().select_for_update(of=('self',)), pk=kwargs['pk'])
        serializer = UpdateStaffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if not values:
            raise ValidationError('Choose a company or an account status to update.')
        company_id = values['company'].pk if values.get('company') else None
        if 'company' in values and staff.company_id != company_id:
            previous_company = staff.company_id
            staff.company = values['company']
            staff.sessions_valid_after = timezone.now()
            staff.save(update_fields=['company', 'sessions_valid_after', 'updated_at'])
            record_security_event(request.user, 'staff.company', staff.pk,
                                  previous_company=previous_company, company=staff.company_id)
        if 'active' in values:
            if staff.supabase_user_id is None:
                raise ValidationError('This staff record needs a verified Supabase identity.')
            if staff.user_id is None:
                staff.user, _ = User.objects.get_or_create(
                    username=str(staff.supabase_user_id), defaults={'email': staff.email},
                )
                existing_profile = getattr(staff.user, 'role_profile', None)
                if existing_profile and existing_profile.pk != staff.pk:
                    raise ValidationError('The staff identity is already linked to another account.')
                staff.save(update_fields=['user', 'updated_at'])
            if staff.user.username != str(staff.supabase_user_id):
                raise ValidationError('The staff account has an inconsistent identity mapping.')
            if staff.user.is_active != values['active']:
                staff.user.is_active = values['active']
                staff.user.save(update_fields=['is_active'])
                staff.sessions_valid_after = timezone.now()
                staff.save(update_fields=['sessions_valid_after', 'updated_at'])
                record_security_event(request.user, 'staff.access', staff.pk, active=staff.user.is_active)
        return Response(self.get_serializer(staff).data)
