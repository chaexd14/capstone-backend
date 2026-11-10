import uuid
import json
import logging
from django.db import models
from django.db import transaction
from django.contrib.auth.models import User


class RoleChoices(models.TextChoices):
    APPLICANT = 'applicant', 'Applicant'
    RECRUITER = 'recruiter', 'Recruiter'
    SUPERADMIN = 'superadmin', 'Superadmin'


class SecurityEvent(models.Model):
    actor_id = models.CharField(max_length=150)
    action = models.CharField(max_length=50)
    object_id = models.CharField(max_length=64)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


def record_security_event(user, action, object_id, **details):
    actor_id = user if isinstance(user, str) else getattr(user, 'supabase_id', user.pk)
    event = SecurityEvent.objects.create(
        actor_id=str(actor_id),
        action=action, object_id=str(object_id), details=details,
    )
    payload = json.dumps({
        'event_id': event.pk, 'actor_id': event.actor_id, 'action': event.action,
        'object_id': event.object_id, 'occurred_at': event.created_at.isoformat(), 'details': event.details,
    })
    transaction.on_commit(lambda: logging.getLogger('security.events').info(payload), robust=True)


class UserRole(models.Model):
    """
    Authoritative server-controlled role mapping for TalentMatch users.
    Maps an immutable Supabase auth UUID to a verified application role.
    Client-controlled metadata (user_metadata) can NEVER override this table.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='role_profile',
        null=True,
        blank=True,
        help_text="Internal Django user instance"
    )
    supabase_user_id = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Supabase auth.users UUID"
    )
    email = models.EmailField(
        db_index=True,
        blank=True,
        default='',
        help_text="Registered email address"
    )
    role = models.CharField(
        max_length=20,
        choices=RoleChoices.choices,
        default=RoleChoices.APPLICANT,
        db_index=True,
        help_text="Authoritative server-verified role"
    )
    company = models.ForeignKey(
        'companies.Company', on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Required for recruiter access to company records.",
    )
    sessions_valid_after = models.DateTimeField(null=True, blank=True, editable=False)
    assigned_by = models.CharField(
        max_length=150,
        blank=True,
        default='system',
        help_text="Administrator or system process that assigned this role"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Audit notes regarding role provisioning"
    )

    class Meta:
        verbose_name = 'User Role'
        verbose_name_plural = 'User Roles'
        ordering = ['-created_at']

    def __str__(self):
        ident = self.email or str(self.supabase_user_id or self.user_id)
        return f"{ident} ({self.role})"


class StaffInvitation(models.Model):
    class State(models.TextChoices):
        SENDING = 'sending'
        PENDING = 'pending'
        FAILED = 'failed'
        ACCEPTED = 'accepted'

    email = models.EmailField(unique=True)
    company = models.ForeignKey('companies.Company', on_delete=models.PROTECT)
    supabase_user_id = models.UUIDField(null=True, unique=True)
    invited_by = models.CharField(max_length=150)
    state = models.CharField(max_length=10, choices=State.choices, default=State.SENDING)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True)
