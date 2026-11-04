import uuid
from django.db import models
from django.contrib.auth.models import User


class RoleChoices(models.TextChoices):
    APPLICANT = 'applicant', 'Applicant'
    RECRUITER = 'recruiter', 'Recruiter'
    SUPERADMIN = 'superadmin', 'Superadmin'


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
