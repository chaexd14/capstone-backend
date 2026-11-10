from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import RoleChoices, StaffInvitation, UserRole, record_security_event
from apps.accounts.staff_identity import send_staff_invitation, set_invited_staff_password, staff_invite_redirect
from apps.companies.models import Company


class StaffInvitationSerializer(serializers.ModelSerializer):
    state = serializers.SerializerMethodField()

    def get_state(self, invitation):
        if invitation.state == StaffInvitation.State.PENDING and invitation.expires_at <= timezone.now():
            return 'expired'
        return invitation.state

    class Meta:
        model = StaffInvitation
        fields = ['id', 'email', 'company', 'state', 'expires_at']


class SendStaffInvitationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    company = serializers.PrimaryKeyRelatedField(queryset=Company.objects.all())

    def to_internal_value(self, data):
        if isinstance(data, dict) and ('password' in data or 'email_verified' in data):
            raise ValidationError('Staff accounts must be activated through a verified email invitation.')
        return super().to_internal_value(data)

    def validate_email(self, value):
        return value.lower()


def invite_staff(user, email, company):
    with transaction.atomic():
        invitation = StaffInvitation.objects.select_for_update().filter(email=email).first()
        if invitation:
            if invitation.company_id != company.pk:
                raise ValidationError({'email': 'This email already has an invitation for another company.'})
            if invitation.state in (StaffInvitation.State.SENDING, StaffInvitation.State.ACCEPTED):
                return invitation, False
            if invitation.state == StaffInvitation.State.PENDING:
                if invitation.expires_at <= timezone.now():
                    raise ValidationError({'email': 'This invitation expired. Reconcile it with the Supabase Auth account before issuing another.'})
                return invitation, False
        if UserRole.objects.filter(email__iexact=email).exclude(role=RoleChoices.APPLICANT).exists():
            raise ValidationError({'email': 'A staff account already uses this email address.'})
        redirect_url = staff_invite_redirect()
        if invitation is None:
            try:
                with transaction.atomic():
                    invitation = StaffInvitation.objects.create(
                        email=email, company=company,
                        invited_by=str(getattr(user, 'supabase_id', user.pk)),
                        expires_at=timezone.now() + timedelta(hours=24),
                    )
            except IntegrityError:
                raise ValidationError({'email': 'An invitation is already being processed for this email.'}) from None
        else:
            invitation.state = StaffInvitation.State.SENDING
            invitation.expires_at = timezone.now() + timedelta(hours=24)
            invitation.save(update_fields=['state', 'expires_at'])

    try:
        identity = send_staff_invitation(email, redirect_url)
    except Exception:
        StaffInvitation.objects.filter(pk=invitation.pk).update(state=StaffInvitation.State.FAILED)
        raise

    with transaction.atomic():
        invitation.supabase_user_id = identity
        invitation.state = StaffInvitation.State.PENDING
        invitation.save(update_fields=['supabase_user_id', 'state'])
        record_security_event(user, 'staff.invite', invitation.pk, company=company.pk)
    return invitation, True


class InvitationPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(min_length=15, max_length=72, write_only=True, trim_whitespace=False)

    def validate_password(self, value):
        if len(value.encode('utf-8')) > 72:
            raise ValidationError('Use at most 72 bytes for your password.')
        try:
            validate_password(value, self.context['user'])
        except DjangoValidationError as exc:
            raise ValidationError(exc.messages) from exc
        return value


class AcceptStaffInvitationView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = 'staff_creation'

    @sensitive_variables()
    @transaction.atomic
    def post(self, request):
        user = request.user
        identity = getattr(user, 'supabase_id', None)
        if not identity or not user.email:
            raise PermissionDenied('Sign in with the invited email address.')
        invitation = StaffInvitation.objects.select_for_update().filter(
            supabase_user_id=identity, email__iexact=user.email,
        ).first()
        if invitation is None:
            raise NotFound('No staff invitation matches this account.')
        if invitation.state == StaffInvitation.State.ACCEPTED:
            return Response({'activated': True}, headers={'Cache-Control': 'private, no-store'})
        if invitation.state != StaffInvitation.State.PENDING or invitation.expires_at <= timezone.now():
            raise PermissionDenied('This invitation is unavailable or expired.')
        profile = UserRole.objects.select_for_update().get(user=user, supabase_user_id=identity)
        if profile.role != RoleChoices.APPLICANT:
            raise PermissionDenied('This account already has a privileged role.')

        password = InvitationPasswordSerializer(data=request.data, context={'user': user})
        password.is_valid(raise_exception=True)
        set_invited_staff_password(invitation.supabase_user_id, invitation.email, password.validated_data['password'])
        profile.role = RoleChoices.RECRUITER
        profile.company = invitation.company
        profile.assigned_by = invitation.invited_by
        profile.sessions_valid_after = timezone.now()
        profile.save(update_fields=['role', 'company', 'assigned_by', 'sessions_valid_after', 'updated_at'])
        invitation.state = StaffInvitation.State.ACCEPTED
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=['state', 'accepted_at'])
        record_security_event(user, 'staff.invite_accept', invitation.pk, company=invitation.company_id)
        return Response({'activated': True}, headers={'Cache-Control': 'private, no-store'})
