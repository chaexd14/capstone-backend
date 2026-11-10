import uuid
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.db import transaction, IntegrityError
from django.utils import timezone
from apps.accounts.models import UserRole, RoleChoices, record_security_event
from apps.companies.models import Company


class Command(BaseCommand):
    help = "Securely provision or update server-controlled roles for TalentMatch users."

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            help="Target user by registered email address"
        )
        parser.add_argument(
            '--supabase-id',
            type=str,
            help="Target user by Supabase auth UUID"
        )
        parser.add_argument(
            '--role',
            type=str,
            choices=[r.value for r in RoleChoices],
            default=None,
            help="Role to assign (applicant, recruiter, superadmin). Explicit intent required for elevated roles."
        )
        parser.add_argument(
            '--company-id', type=int, help="Company assignment for a recruiter."
        )
        parser.add_argument(
            '--notes',
            type=str,
            default='Provisioned via management command',
            help="Audit notes for this role assignment"
        )
        parser.add_argument(
            '--assigned-by',
            type=str,
            default='admin_cli',
            help="Identity of administrator executing the provisioning"
        )
        parser.add_argument(
            '--list',
            action='store_true',
            help="List all users with assigned roles"
        )
        parser.add_argument(
            '--revoke',
            action='store_true',
            help="Revoke privileged role (reset to applicant)"
        )

    def handle(self, *args, **options):
        if options['list']:
            roles = UserRole.objects.all().order_by('role', '-updated_at')
            if not roles.exists():
                self.stdout.write(self.style.WARNING("No user roles registered in the database."))
                return

            self.stdout.write(self.style.MIGRATE_HEADING("=== TalentMatch Authoritative User Roles ==="))
            for ur in roles:
                ident = ur.email or str(ur.supabase_user_id)
                self.stdout.write(
                    f" - [{ur.role.upper()}] {ident} | UUID: {ur.supabase_user_id} | Assigned by: {ur.assigned_by} ({ur.updated_at.strftime('%Y-%m-%d %H:%M')})"
                )
            return

        email = (options.get('email') or '').strip().lower()
        supabase_id_str = (options.get('supabase_id') or '').strip()
        is_revoke = options['revoke']
        raw_role = options.get('role')
        notes = options['notes']
        assigned_by = options['assigned_by']
        company_id = options['company_id']

        if is_revoke:
            target_role = RoleChoices.APPLICANT
        elif raw_role:
            target_role = raw_role
        else:
            target_role = None  # Will default to applicant for new records or preserve existing

        if not email and not supabase_id_str:
            raise CommandError("Please provide either --email or --supabase-id (or use --list).")

        supabase_uuid = None
        if supabase_id_str:
            try:
                supabase_uuid = uuid.UUID(supabase_id_str)
            except ValueError:
                raise CommandError(f"Invalid Supabase UUID provided: '{supabase_id_str}'")

        # Security Requirement: Elevated roles (recruiter/superadmin) CANNOT be created as unbound email records.
        # Elevated roles must require explicit, validated operator intent.
        if target_role in (RoleChoices.RECRUITER, RoleChoices.SUPERADMIN):
            if not supabase_uuid:
                raise CommandError(
                    f"Elevated role '{target_role}' requires an explicit '--supabase-id'. "
                    "Unbound email-only assignment of recruiter or superadmin roles is prohibited."
                )
        if company_id is not None:
            if not supabase_uuid or not Company.objects.filter(pk=company_id).exists():
                raise CommandError("A company assignment requires a valid company and --supabase-id.")

        def _execute_provisioning(attempt_role):
            with transaction.atomic():
                existing_by_uuid = UserRole.objects.filter(supabase_user_id=supabase_uuid).first() if supabase_uuid else None
                existing_by_email = UserRole.objects.filter(email__iexact=email).first() if email else None

                # Cross-validation: prevent silent reassignment or binding conflicts
                if existing_by_uuid and existing_by_email and existing_by_uuid.id != existing_by_email.id:
                    raise CommandError(
                        f"Conflicting records: Supabase UUID '{supabase_uuid}' is assigned to email '{existing_by_uuid.email}', "
                        f"while email '{email}' is bound to Supabase UUID '{existing_by_email.supabase_user_id}'. "
                        "Silent reassignment is prohibited."
                    )

                if existing_by_uuid and email:
                    if existing_by_uuid.email and existing_by_uuid.email.lower() != email:
                        raise CommandError(
                            f"Conflicting email: Supabase UUID '{supabase_uuid}' is already bound to '{existing_by_uuid.email}', "
                            f"which does not match provided email '{email}'. Reassignment is prohibited."
                        )

                if existing_by_email and supabase_uuid:
                    if existing_by_email.supabase_user_id and existing_by_email.supabase_user_id != supabase_uuid:
                        raise CommandError(
                            f"Conflicting UUID: Email '{email}' is already bound to Supabase UUID '{existing_by_email.supabase_user_id}', "
                            f"which does not match provided UUID '{supabase_uuid}'. Silent reassignment is prohibited."
                        )

                user_role = existing_by_uuid or existing_by_email
                effective_role = attempt_role if attempt_role else (user_role.role if user_role else RoleChoices.APPLICANT)
                old_role = user_role.role if user_role else None
                old_company = user_role.company_id if user_role else None
                if company_id is not None and effective_role != RoleChoices.RECRUITER:
                    raise CommandError("Company assignment is only supported for recruiter accounts.")

                # Ensure elevated role is not assigned without explicit operator intent
                if not user_role and effective_role in (RoleChoices.RECRUITER, RoleChoices.SUPERADMIN) and not raw_role:
                    effective_role = RoleChoices.APPLICANT

                if not user_role:
                    user_role = UserRole(
                        supabase_user_id=supabase_uuid,
                        email=email,
                        role=effective_role,
                        assigned_by=assigned_by,
                        notes=notes
                    )
                else:
                    if attempt_role or is_revoke:
                        user_role.role = effective_role
                    user_role.assigned_by = assigned_by
                    user_role.notes = notes
                    if email and not user_role.email:
                        user_role.email = email
                    if supabase_uuid and not user_role.supabase_user_id:
                        user_role.supabase_user_id = supabase_uuid

                # Locate linked Django User
                linked_user = user_role.user
                if not linked_user and user_role.supabase_user_id:
                    linked_user = User.objects.filter(username=str(user_role.supabase_user_id)).first()
                if not linked_user and email:
                    linked_user = User.objects.filter(email__iexact=email).first()

                # Safety check: Never reassign an existing UserRole or Supabase UUID to a different Django user
                if linked_user:
                    existing_role_profile = getattr(linked_user, 'role_profile', None)
                    if existing_role_profile and existing_role_profile.id != user_role.id:
                        raise CommandError(
                            f"Security conflict: User '{linked_user.username}' is already linked to a different UserRole (id={existing_role_profile.id}). "
                            "Reassigning existing user profiles is strictly prohibited."
                        )

                    # Supabase users are not granted Django is_staff or is_superuser
                    # Native Django superusers without Supabase bindings remain separate administrative accounts
                    if not linked_user.is_superuser:
                        linked_user.is_staff = False
                        linked_user.is_superuser = False
                        linked_user.save(update_fields=['is_staff', 'is_superuser'])

                    user_role.user = linked_user

                if effective_role != RoleChoices.RECRUITER:
                    user_role.company_id = None
                elif company_id is not None:
                    user_role.company_id = company_id
                changed = (old_role, old_company) != (user_role.role, user_role.company_id)
                if changed and old_role is not None:
                    user_role.sessions_valid_after = timezone.now()
                user_role.save()
                if changed:
                    record_security_event(
                        assigned_by, 'role.change', user_role.supabase_user_id or user_role.pk,
                        previous=old_role, current=user_role.role,
                        previous_company=old_company, company=user_role.company_id,
                    )
                return user_role, effective_role

        try:
            user_role, resolved_role = _execute_provisioning(target_role)
        except IntegrityError as exc:
            # Concurrency recovery: re-fetch and verify identity mapping within a fresh transaction
            try:
                user_role, resolved_role = _execute_provisioning(target_role)
            except Exception as retry_exc:
                raise CommandError(f"Database integrity error during user provisioning: {retry_exc}") from exc

        action_label = "revoked to 'applicant'" if is_revoke else f"provisioned role '{resolved_role}'"
        target_label = f"UUID {supabase_uuid}" if supabase_uuid else f"email {email}"
        if supabase_uuid and email:
            target_label = f"{email} (UUID {supabase_uuid})"

        self.stdout.write(
            self.style.SUCCESS(
                f"[SUCCESS] Successfully {action_label} for {target_label}."
            )
        )
