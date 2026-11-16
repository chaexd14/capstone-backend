from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from apps.accounts.models import RoleChoices, StaffInvitation, UserRole, record_security_event
from apps.accounts.staff_invitations import StaffInvitationSerializer
from apps.accounts.staff_identity import (
    create_staff_identity, delete_staff_identity, send_password_reset, set_staff_password,
)
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
    first_name = serializers.CharField(source='user.first_name', read_only=True, default='')
    last_name = serializers.CharField(source='user.last_name', read_only=True, default='')
    company_name = serializers.CharField(source='company.name', read_only=True, default=None)
    active = serializers.BooleanField(source='user.is_active', read_only=True, default=True)

    class Meta:
        model = UserRole
        fields = ['id', 'first_name', 'last_name', 'email', 'role', 'company', 'company_name', 'active', 'created_at']
        read_only_fields = fields


class UpdateStaffSerializer(serializers.Serializer):
    company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all(), required=False, allow_null=True)
    active = serializers.BooleanField(required=False)


class StaffPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(min_length=15, max_length=72, write_only=True, trim_whitespace=False)

    def validate_password(self, value):
        if len(value.encode('utf-8')) > 72:
            raise serializers.ValidationError('Use at most 72 bytes for the password.')
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value


class CreateStaffSerializer(StaffPasswordSerializer):
    first_name = serializers.CharField(max_length=150, trim_whitespace=True)
    last_name = serializers.CharField(max_length=150, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)
    role = serializers.ChoiceField(choices=[RoleChoices.RECRUITER])
    company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all())

    def validate_email(self, value):
        value = value.strip().lower()
        if UserRole.objects.filter(email__iexact=value).exists() or User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account already uses this email address.')
        return value


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
        serializer = CreateStaffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        identity = create_staff_identity(
            values['email'], values['password'], values['first_name'], values['last_name'],
        )
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    username=str(identity), email=values['email'], first_name=values['first_name'],
                    last_name=values['last_name'], password=None,
                )
                staff = UserRole.objects.create(
                    user=user, supabase_user_id=identity, email=values['email'], role=RoleChoices.RECRUITER,
                    company=values['company'], assigned_by=str(getattr(request.user, 'supabase_id', request.user.pk)),
                )
                record_security_event(request.user, 'staff.create', staff.pk, company=staff.company_id)
        except (IntegrityError, ValueError):
            delete_staff_identity(identity)
            raise ValidationError({'email': 'The account could not be created. Verify the email and try again.'}) from None
        except Exception:
            delete_staff_identity(identity)
            raise
        return Response(self.get_serializer(staff).data, status=201)

    @action(detail=True, methods=['post'], url_path='password-reset-link')
    def password_reset_link(self, request, pk=None):
        staff = get_object_or_404(self.get_queryset(), pk=pk)
        send_password_reset(staff.email)
        record_security_event(request.user, 'staff.password_reset_link', staff.pk, company=staff.company_id)
        return Response({'sent': True})

    @action(detail=True, methods=['post'], url_path='password')
    def password(self, request, pk=None):
        staff = get_object_or_404(self.get_queryset(), pk=pk)
        if staff.supabase_user_id is None:
            raise ValidationError('This staff record does not have a verified identity.')
        serializer = StaffPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        set_staff_password(staff.supabase_user_id, serializer.validated_data['password'])
        staff.sessions_valid_after = timezone.now()
        staff.save(update_fields=['sessions_valid_after', 'updated_at'])
        record_security_event(request.user, 'staff.password_reset', staff.pk, company=staff.company_id)
        return Response({'changed': True})

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
