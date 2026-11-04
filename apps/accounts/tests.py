import uuid
import time
import jwt
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed

from apps.accounts.models import UserRole, RoleChoices
from apps.jobs.models import Job, JobStatus
from apps.applications.models import Application, ApplicationStatus
from apps.resumes.models import Resume
from config.authentication import SupabaseJWTAuthentication


TEST_JWT_SECRET = "test-secret-for-phase2-hardening-suite-2026-min-32-chars"


class Phase2TrustedRoleSecurityTests(TestCase):
    def setUp(self):
        self.auth = SupabaseJWTAuthentication()
        self.client = APIClient()

    def _generate_token(self, user_id=None, email=None, user_metadata=None, app_metadata=None, expired=False, alg="HS256", secret=TEST_JWT_SECRET, iss=None, omit_claims=None):
        now = int(time.time())
        supabase_url = getattr(settings, 'SUPABASE_URL', '').rstrip('/')
        payload = {
            "sub": str(user_id or uuid.uuid4()),
            "email": email or "user@test.com",
            "aud": "authenticated",
            "iss": iss or f"{supabase_url}/auth/v1",
            "iat": now - 60,
            "exp": now - 3600 if expired else now + 3600,
            "user_metadata": user_metadata or {},
            "app_metadata": app_metadata or {},
        }
        if omit_claims:
            for c in omit_claims:
                payload.pop(c, None)
        return jwt.encode(payload, secret, algorithm=alg)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_user_metadata_recruiter_escalation_blocked(self):
        """
        Regression Test: An attacker adds {"role": "recruiter"} to user_metadata in their JWT.
        The backend must IGNORE user_metadata and classify the user strictly as 'applicant',
        with is_staff=False and is_superuser=False.
        """
        attacker_uuid = uuid.uuid4()
        token = self._generate_token(
            user_id=attacker_uuid,
            email="attacker_recruiter@test.com",
            user_metadata={"role": "recruiter"}
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        user, returned_token = self.auth.authenticate(DummyRequest())

        self.assertEqual(user.role, RoleChoices.APPLICANT)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        # Verify database UserRole record was created strictly as applicant
        user_role = UserRole.objects.get(supabase_user_id=attacker_uuid)
        self.assertEqual(user_role.role, RoleChoices.APPLICANT)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_user_metadata_superadmin_escalation_blocked(self):
        """
        Regression Test: An attacker adds {"role": "superadmin"} to user_metadata in their JWT.
        The backend must IGNORE user_metadata and prevent superuser/staff privileges.
        """
        attacker_uuid = uuid.uuid4()
        token = self._generate_token(
            user_id=attacker_uuid,
            email="attacker_admin@test.com",
            user_metadata={"role": "superadmin"}
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        user, _ = self.auth.authenticate(DummyRequest())

        self.assertEqual(user.role, RoleChoices.APPLICANT)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_app_metadata_recruiter_escalation_blocked_without_db_provisioning(self):
        """
        Regression Test: An applicant sends a JWT containing {"role": "recruiter"} or {"role": "superadmin"}
        inside app_metadata. The backend must NOT grant elevated privileges unless explicitly
        provisioned in the authoritative accounts_userrole table.
        """
        attacker_uuid = uuid.uuid4()
        token = self._generate_token(
            user_id=attacker_uuid,
            email="applicant_with_app_metadata@test.com",
            app_metadata={"role": "recruiter"}
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        user, _ = self.auth.authenticate(DummyRequest())

        self.assertEqual(user.role, RoleChoices.APPLICANT)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_unbound_recruiter_role_cannot_be_claimed_by_matching_jwt_email(self):
        """
        Regression Test: An unbound recruiter record exists in UserRole (e.g. pre-created
        with an email and no Supabase UUID). An incoming JWT with a matching email MUST NOT
        claim the elevated recruiter role. The caller must be assigned applicant permissions
        and the unbound record must not be bound.
        """
        target_email = "pre_provisioned_target@company.com"
        unbound_recruiter = UserRole.objects.create(
            supabase_user_id=None,
            email=target_email,
            role=RoleChoices.RECRUITER,
            assigned_by="old_admin",
            notes="Unbound legacy record"
        )

        attacker_uuid = uuid.uuid4()
        token = self._generate_token(
            user_id=attacker_uuid,
            email=target_email
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        user, _ = self.auth.authenticate(DummyRequest())

        # Caller must receive APPLICANT role, never RECRUITER
        self.assertEqual(user.role, RoleChoices.APPLICANT)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

        # Unbound recruiter record must remain untouched and un-claimed
        unbound_recruiter.refresh_from_db()
        self.assertIsNone(unbound_recruiter.supabase_user_id)
        self.assertEqual(unbound_recruiter.role, RoleChoices.RECRUITER)

        # The new user has their own separate APPLICANT record
        caller_role = UserRole.objects.get(supabase_user_id=attacker_uuid)
        self.assertEqual(caller_role.role, RoleChoices.APPLICANT)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_authoritative_recruiter_provisioning(self):
        """
        A user officially provisioned in UserRole by exact UUID with 'recruiter'
        must receive recruiter role and is_staff=True.
        """
        recruiter_uuid = uuid.uuid4()
        UserRole.objects.create(
            supabase_user_id=recruiter_uuid,
            email="authorized_recruiter@test.com",
            role=RoleChoices.RECRUITER,
            assigned_by="security_admin",
            notes="Authorized HR Staff"
        )

        token = self._generate_token(
            user_id=recruiter_uuid,
            email="authorized_recruiter@test.com",
            user_metadata={}
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        user, _ = self.auth.authenticate(DummyRequest())

        self.assertEqual(user.role, RoleChoices.RECRUITER)
        self.assertFalse(user.is_staff)  # Application-level role authorization; Django staff locked down
        self.assertFalse(user.is_superuser)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_authoritative_superadmin_provisioning(self):
        """
        A user provisioned by exact UUID as 'superadmin' receives superuser and staff flags.
        """
        admin_uuid = uuid.uuid4()
        UserRole.objects.create(
            supabase_user_id=admin_uuid,
            email="chief_admin@test.com",
            role=RoleChoices.SUPERADMIN,
            assigned_by="system_root"
        )

        token = self._generate_token(
            user_id=admin_uuid,
            email="chief_admin@test.com"
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        user, _ = self.auth.authenticate(DummyRequest())

        self.assertEqual(user.role, RoleChoices.SUPERADMIN)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)  # Application-specific authorization preferred over Django superuser bypass

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_expired_token_rejected(self):
        """
        Expired tokens must be denied authentication.
        """
        some_uuid = uuid.uuid4()
        token = self._generate_token(
            user_id=some_uuid,
            email="expired_user@test.com",
            expired=True
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(DummyRequest())
        self.assertIn("expired", str(ctx.exception).lower())

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_tampered_signature_rejected(self):
        """
        Tokens signed with an incorrect key must be denied.
        """
        some_uuid = uuid.uuid4()
        token = self._generate_token(
            user_id=some_uuid,
            email="tampered@test.com",
            secret="completely-wrong-secret-key-that-should-fail-00000"
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with self.assertRaises(AuthenticationFailed):
            self.auth.authenticate(DummyRequest())

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_missing_issuer_claim_rejected(self):
        """
        Tokens missing the 'iss' claim must be denied authentication.
        Generic error message must be returned to client.
        """
        token = self._generate_token(omit_claims=["iss"])
        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(DummyRequest())
        self.assertEqual(str(ctx.exception), "Invalid or expired authentication credentials")

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_foreign_issuer_rejected(self):
        """
        Tokens with foreign 'iss' claim must be rejected.
        Generic error message must be returned to client without leaking internal decoding details.
        """
        token_foreign = self._generate_token(
            iss="https://foreign-attacker-project.supabase.co/auth/v1"
        )
        class DummyRequest:
            headers = {"Authorization": f"Bearer {token_foreign}"}

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(DummyRequest())
        self.assertEqual(str(ctx.exception), "Invalid or expired authentication credentials")

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_missing_expiration_claim_rejected(self):
        """
        Tokens missing the 'exp' claim must be denied authentication.
        """
        token = self._generate_token(omit_claims=["exp"])
        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(DummyRequest())
        self.assertEqual(str(ctx.exception), "Invalid or expired authentication credentials")

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_missing_subject_claim_rejected(self):
        """
        Tokens missing the 'sub' claim must be denied authentication.
        """
        token = self._generate_token(omit_claims=["sub"])
        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(DummyRequest())
        self.assertEqual(str(ctx.exception), "Invalid or expired authentication credentials")

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_missing_audience_claim_rejected(self):
        """
        Tokens missing the 'aud' claim or wrong audience must be denied authentication.
        """
        token_no_aud = self._generate_token(omit_claims=["aud"])
        class DummyRequest1:
            headers = {"Authorization": f"Bearer {token_no_aud}"}

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(DummyRequest1())
        self.assertEqual(str(ctx.exception), "Invalid or expired authentication credentials")

        # Wrong audience
        now = int(time.time())
        token_wrong_aud = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "email": "user@test.com",
                "aud": "wrong-audience",
                "iss": f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1",
                "exp": now + 3600,
            },
            TEST_JWT_SECRET,
            algorithm="HS256"
        )
        class DummyRequest2:
            headers = {"Authorization": f"Bearer {token_wrong_aud}"}

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(DummyRequest2())
        self.assertEqual(str(ctx.exception), "Invalid or expired authentication credentials")

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_valid_token_with_all_required_claims_succeeds(self):
        """
        A token with sub, email, aud='authenticated', iss=expected_issuer, and exp succeeds.
        """
        some_uuid = uuid.uuid4()
        token = self._generate_token(user_id=some_uuid, email="legit@test.com")
        class LegitRequest:
            headers = {"Authorization": f"Bearer {token}"}

        user, _ = self.auth.authenticate(LegitRequest())
        self.assertEqual(user.supabase_id, str(some_uuid))

    @override_settings(SUPABASE_URL="")
    def test_missing_supabase_url_fails_closed(self):
        """
        If SUPABASE_URL is not configured, authentication fails closed with a configuration error.
        """
        token = self._generate_token()
        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with self.assertRaises(AuthenticationFailed) as ctx:
            self.auth.authenticate(DummyRequest())
        self.assertIn("configuration error", str(ctx.exception).lower())

    def test_jwks_cache_keyed_by_supabase_url(self):
        """
        Verify the JWKS client cache is keyed by Supabase URL, preventing cross-project key reuse.
        """
        from config.authentication import get_jwks_client, _jwks_clients
        url_1 = "https://project-a.supabase.co"
        url_2 = "https://project-b.supabase.co"
        client_1 = get_jwks_client(url_1)
        client_2 = get_jwks_client(url_2)
        self.assertIn(url_1, _jwks_clients)
        self.assertIn(url_2, _jwks_clients)
        self.assertIsNot(client_1, client_2)
        self.assertEqual(client_1.uri, f"{url_1}/auth/v1/.well-known/jwks.json")
        self.assertEqual(client_2.uri, f"{url_2}/auth/v1/.well-known/jwks.json")

    def test_production_startup_validation(self):
        """
        Verify production startup fails safely if SECRET_KEY or SUPABASE_URL is missing/insecure.
        """
        # Insecure secret key in production
        def check_startup(debug, secret_key, supabase_url):
            if not debug:
                if secret_key.startswith('django-insecure-'):
                    raise RuntimeError("Set a secure SECRET_KEY for production.")
                if not supabase_url:
                    raise RuntimeError("SUPABASE_URL must be configured in production.")

        with self.assertRaises(RuntimeError) as ctx:
            check_startup(debug=False, secret_key="django-insecure-test", supabase_url="https://test.supabase.co")
        self.assertIn("SECRET_KEY", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            check_startup(debug=False, secret_key="secure-prod-key-12345", supabase_url="")
        self.assertIn("SUPABASE_URL", str(ctx.exception))

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_role_revocation_downgrades_privileges(self):
        """
        When a user's role in UserRole is demoted to applicant, subsequent
        authentication must immediately strip is_staff and is_superuser.
        """
        user_uuid = uuid.uuid4()
        ur = UserRole.objects.create(
            supabase_user_id=user_uuid,
            email="former_recruiter@test.com",
            role=RoleChoices.RECRUITER
        )

        token = self._generate_token(user_id=user_uuid, email="former_recruiter@test.com")

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        user, _ = self.auth.authenticate(DummyRequest())
        self.assertEqual(user.role, RoleChoices.RECRUITER)
        self.assertFalse(user.is_staff)

        # Admin demotes user
        ur.role = RoleChoices.APPLICANT
        ur.save(update_fields=['role'])

        # Next request
        user2, _ = self.auth.authenticate(DummyRequest())
        self.assertEqual(user2.role, RoleChoices.APPLICANT)
        self.assertFalse(user2.is_staff)
        self.assertFalse(user2.is_superuser)

    def test_provision_user_command_requires_supabase_id_for_elevated_roles(self):
        """
        Regression Test: provision_user CLI command must reject email-only assignment
        of 'recruiter' or 'superadmin' roles without an explicit --supabase-id.
        """
        # Attempting recruiter without --supabase-id must fail
        with self.assertRaises(CommandError) as ctx:
            call_command("provision_user", email="test_recruiter@talentmatch.ai", role="recruiter")
        self.assertIn("requires an explicit '--supabase-id'", str(ctx.exception))

        # Attempting superadmin without --supabase-id must fail
        with self.assertRaises(CommandError) as ctx:
            call_command("provision_user", email="test_admin@talentmatch.ai", role="superadmin")
        self.assertIn("requires an explicit '--supabase-id'", str(ctx.exception))

        # Providing --supabase-id succeeds
        recruiter_uuid = uuid.uuid4()
        call_command(
            "provision_user",
            supabase_id=str(recruiter_uuid),
            email="valid_recruiter@talentmatch.ai",
            role="recruiter"
        )
        ur = UserRole.objects.get(supabase_user_id=recruiter_uuid)
        self.assertEqual(ur.role, RoleChoices.RECRUITER)
        self.assertEqual(ur.email, "valid_recruiter@talentmatch.ai")

    def test_provision_user_command_prevents_silent_reassignment_and_conflicts(self):
        """
        Regression Test: provision_user command must reject attempts to silently
        reassign existing UUIDs to different emails or vice-versa.
        """
        uuid_a = uuid.uuid4()
        UserRole.objects.create(
            supabase_user_id=uuid_a,
            email="account_a@talentmatch.ai",
            role=RoleChoices.RECRUITER
        )

        uuid_b = uuid.uuid4()
        UserRole.objects.create(
            supabase_user_id=uuid_b,
            email="account_b@talentmatch.ai",
            role=RoleChoices.RECRUITER
        )

        # Attempting to assign uuid_a with account_b email must raise CommandError
        with self.assertRaises(CommandError) as ctx:
            call_command("provision_user", supabase_id=str(uuid_a), email="account_b@talentmatch.ai", role="recruiter")
        self.assertIn("Conflicting", str(ctx.exception))

        # Attempting to assign uuid_b with a completely different email must raise CommandError
        with self.assertRaises(CommandError) as ctx:
            call_command("provision_user", supabase_id=str(uuid_b), email="different@talentmatch.ai", role="recruiter")
        self.assertIn("Conflicting email", str(ctx.exception))

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_recruiter_can_access_authorized_resources_when_provisioned_by_exact_uuid(self):
        """
        Regression Test: A recruiter provisioned by exact Supabase UUID can access
        authorized recruiter endpoints (e.g. GET /api/applications/ and GET /api/jobs/).
        """
        recruiter_uuid = uuid.uuid4()
        UserRole.objects.create(
            supabase_user_id=recruiter_uuid,
            email="lead_recruiter@talentmatch.ai",
            role=RoleChoices.RECRUITER
        )

        token = self._generate_token(
            user_id=recruiter_uuid,
            email="lead_recruiter@talentmatch.ai"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        # Recruiter can access applications listing
        res_apps = self.client.get("/api/applications/")
        self.assertEqual(res_apps.status_code, status.HTTP_200_OK)

        # Recruiter can access jobs listing
        res_jobs = self.client.get("/api/jobs/")
        self.assertEqual(res_jobs.status_code, status.HTTP_200_OK)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_anonymous_and_applicant_cannot_read_unpublished_jobs(self):
        """
        Security Test:
        - Anonymous callers and authenticated applicants can ONLY see PUBLISHED jobs.
        - DRAFT, CLOSED, and ARCHIVED jobs return 404 for non-recruiters.
        - Only authenticated recruiters can read unpublished jobs.
        """
        published_job = Job.objects.create(
            title="Published Python Engineer",
            department="Engineering",
            location="Remote",
            status=JobStatus.PUBLISHED,
            description="Open for everyone."
        )
        draft_job = Job.objects.create(
            title="Secret Draft Architect",
            department="R&D",
            location="Remote",
            status=JobStatus.DRAFT,
            description="Internal draft."
        )
        closed_job = Job.objects.create(
            title="Closed Job Position",
            department="Operations",
            location="Remote",
            status=JobStatus.CLOSED,
            description="No longer hiring."
        )

        # 1. Anonymous caller
        self.client.credentials()
        res_list = self.client.get("/api/jobs/")
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        ids = [j['id'] for j in res_list.data if isinstance(j, dict) and 'id' in j]
        self.assertIn(published_job.id, ids)
        self.assertNotIn(draft_job.id, ids)
        self.assertNotIn(closed_job.id, ids)

        # Direct detail access by ID returns 404
        self.assertEqual(self.client.get(f"/api/jobs/{draft_job.id}/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(f"/api/jobs/{closed_job.id}/").status_code, status.HTTP_404_NOT_FOUND)

        # 2. Authenticated Applicant
        applicant_uuid = uuid.uuid4()
        token_applicant = self._generate_token(user_id=applicant_uuid, email="applicant_view@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_applicant}")

        res_app_list = self.client.get("/api/jobs/")
        self.assertEqual(res_app_list.status_code, status.HTTP_200_OK)
        app_ids = [j['id'] for j in res_app_list.data if isinstance(j, dict) and 'id' in j]
        self.assertIn(published_job.id, app_ids)
        self.assertNotIn(draft_job.id, app_ids)
        self.assertNotIn(closed_job.id, app_ids)

        self.assertEqual(self.client.get(f"/api/jobs/{draft_job.id}/").status_code, status.HTTP_404_NOT_FOUND)

        # 3. Authenticated Recruiter
        recruiter_uuid = uuid.uuid4()
        UserRole.objects.create(
            supabase_user_id=recruiter_uuid,
            email="recruiter_draft@test.com",
            role=RoleChoices.RECRUITER
        )
        token_recruiter = self._generate_token(user_id=recruiter_uuid, email="recruiter_draft@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_recruiter}")

        self.assertEqual(self.client.get(f"/api/jobs/{draft_job.id}/").status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(f"/api/jobs/{closed_job.id}/").status_code, status.HTTP_200_OK)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_applicant_cannot_read_another_applicant_resume(self):
        """
        Security Test: An applicant is forbidden from reading or downloading
        another applicant's resume.
        """
        job = Job.objects.create(
            title="Security Analyst",
            department="InfoSec",
            location="Remote",
            status=JobStatus.PUBLISHED
        )
        applicant_a = uuid.uuid4()
        applicant_b = uuid.uuid4()

        app_b = Application.objects.create(
            job=job,
            first_name="Bob",
            last_name="Applicant",
            email="bob@test.com",
            status=ApplicationStatus.SUBMITTED,
            user_id=applicant_b
        )

        test_file = SimpleUploadedFile("resume.pdf", b"%PDF-1.4 test resume content", content_type="application/pdf")
        resume_b = Resume.objects.create(
            application=app_b,
            file=test_file,
            original_filename="resume_bob.pdf",
            extracted_text="Bob's sensitive private resume text"
        )

        # Authenticate as Applicant A
        token_a = self._generate_token(user_id=applicant_a, email="alice_spy@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}")

        # Applicant A tries to retrieve Bob's resume: blocked
        res_get = self.client.get(f"/api/resumes/{resume_b.id}/")
        self.assertIn(res_get.status_code, (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN))

        # Applicant A tries to download Bob's resume: blocked
        res_dl = self.client.get(f"/api/resumes/{resume_b.id}/download/")
        self.assertIn(res_dl.status_code, (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN))

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_applicant_application_submission_binds_to_authenticated_user_id(self):
        """
        Security Test: When an applicant submits an application, the backend
        strictly binds it to the caller's authenticated Supabase UUID.
        Attempts to pass a NULL user_id or spoof another UUID are rejected or overwritten.
        """
        job = Job.objects.create(
            title="Backend Security Engineer",
            department="Engineering",
            location="Remote",
            status=JobStatus.PUBLISHED
        )

        applicant_uuid = uuid.uuid4()
        spoofed_uuid = str(uuid.uuid4())
        token = self._generate_token(user_id=applicant_uuid, email="applicant_real@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

        test_file = SimpleUploadedFile("resume.pdf", b"%PDF-1.4 sample content", content_type="application/pdf")

        # Submit application attempting to spoof another user_id
        res = self.client.post(
            f"/api/jobs/{job.id}/apply/",
            {
                "first_name": "Real",
                "last_name": "Applicant",
                "email": "applicant_real@test.com",
                "phone": "+63 917 123 4567",
                "resume": test_file,
                "user_id": spoofed_uuid,
            },
            format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        created_app = Application.objects.get(id=res.data['id'])
        # Must be bound to authenticated caller's UUID, never the spoofed UUID
        self.assertIsNotNone(created_app.user_id)
        self.assertEqual(str(created_app.user_id), str(applicant_uuid))
        self.assertNotEqual(str(created_app.user_id), spoofed_uuid)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_applicant_ownership_and_public_job_access_remain_functional(self):
        """
        Regression Test:
        1. Anonymous callers can view published public jobs and aggregate stats.
        2. Authenticated applicants can view own applications.
        3. Applicants cannot view another user's application (IDOR blocked).
        """
        self.client.credentials()  # clear credentials
        res_stats = self.client.get("/api/jobs/public-stats/")
        self.assertEqual(res_stats.status_code, status.HTTP_200_OK)

        job = Job.objects.create(
            title="Senior Platform Engineer",
            department="Engineering",
            location="Remote",
            status=JobStatus.PUBLISHED,
            description="Build secure platforms."
        )

        applicant_a_uuid = uuid.uuid4()
        applicant_b_uuid = uuid.uuid4()

        app_a = Application.objects.create(
            job=job,
            first_name="Alice",
            last_name="Smith",
            email="alice@test.com",
            status=ApplicationStatus.SUBMITTED,
            user_id=applicant_a_uuid
        )

        app_b = Application.objects.create(
            job=job,
            first_name="Bob",
            last_name="Jones",
            email="bob@test.com",
            status=ApplicationStatus.SUBMITTED,
            user_id=applicant_b_uuid
        )

        # Authenticate as Applicant A
        token_a = self._generate_token(user_id=applicant_a_uuid, email="alice@test.com")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token_a}")

        # Applicant A can retrieve own application
        res_own = self.client.get(f"/api/applications/{app_a.id}/")
        self.assertEqual(res_own.status_code, status.HTTP_200_OK)

        # Applicant A CANNOT retrieve Applicant B's application (IDOR blocked)
        res_other = self.client.get(f"/api/applications/{app_b.id}/")
        self.assertIn(res_other.status_code, (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN))

        # Applicant A cannot close a job
        res_close = self.client.post(f"/api/jobs/{job.id}/close/")
        self.assertEqual(res_close.status_code, status.HTTP_403_FORBIDDEN)


class CorsCsrfAndSessionHardeningTests(TestCase):
    """
    Regression test suite for Authentication, CORS, and CSRF security hardening:
    1. Removal of DRF SessionAuthentication (API exclusively uses Supabase Bearer JWTs).
    2. Strict CORS policy with disabled wildcard origins and disabled credentials.
    3. Removal of wildcard hosting-provider domains from CSRF_TRUSTED_ORIGINS.
    """

    def setUp(self):
        self.client = APIClient()

    def _generate_valid_jwt(self, user_uuid, email="verified_user@talentmatch.ai"):
        now = int(time.time())
        supabase_url = getattr(settings, 'SUPABASE_URL', '').rstrip('/')
        payload = {
            "sub": str(user_uuid),
            "email": email,
            "aud": "authenticated",
            "iss": f"{supabase_url}/auth/v1",
            "iat": now - 60,
            "exp": now + 3600,
        }
        return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")

    # =========================================================================
    # 1. CORS Security Verification
    # =========================================================================
    def test_cors_configuration_defaults(self):
        """
        Verify that CORS wildcard origins and cross-origin credentials are disabled.
        """
        self.assertFalse(settings.CORS_ALLOW_ALL_ORIGINS)
        self.assertFalse(settings.CORS_ALLOW_CREDENTIALS)

    def test_cors_custom_headers_supported(self):
        """
        Custom headers required by frontend (x-app-client-key, x-requested-with)
        must remain supported in CORS headers.
        """
        self.assertIn('x-app-client-key', settings.CORS_ALLOW_HEADERS)
        self.assertIn('x-requested-with', settings.CORS_ALLOW_HEADERS)

    def test_cors_trusted_origin_preflight_succeeds(self):
        """
        Preflight OPTIONS request from an explicitly configured origin succeeds.
        Access-Control-Allow-Credentials must NOT be true.
        """
        trusted_origin = "http://localhost:3000"
        with self.settings(CORS_ALLOWED_ORIGINS=[trusted_origin], CORS_ALLOW_CREDENTIALS=False):
            response = self.client.options(
                "/api/jobs/public-stats/",
                HTTP_ORIGIN=trusted_origin,
                HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
                HTTP_ACCESS_CONTROL_REQUEST_HEADERS="x-app-client-key"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), trusted_origin)
            self.assertNotEqual(response.headers.get("Access-Control-Allow-Credentials"), "true")

    def test_cors_untrusted_third_party_origin_rejected(self):
        """
        Preflight OPTIONS request from untrusted origin (e.g. arbitrary Vercel app)
        must NOT return Access-Control-Allow-Origin header.
        """
        untrusted_origin = "https://unauthorized-phishing-app.vercel.app"
        response = self.client.options(
            "/api/jobs/public-stats/",
            HTTP_ORIGIN=untrusted_origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET"
        )
        self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))

    # =========================================================================
    # 2. CSRF Security Verification
    # =========================================================================
    def test_csrf_trusted_origins_contain_no_wildcards(self):
        """
        CSRF_TRUSTED_ORIGINS must not contain wildcard hosting-provider domains
        such as https://*.vercel.app, https://*.onrender.com, or https://*.railway.app.
        """
        wildcard_domains = [
            "https://*.vercel.app",
            "https://*.onrender.com",
            "https://*.railway.app",
        ]
        for origin in settings.CSRF_TRUSTED_ORIGINS:
            self.assertNotIn("*", origin, f"Wildcard detected in CSRF_TRUSTED_ORIGINS: {origin}")
            for wildcard in wildcard_domains:
                self.assertNotEqual(origin, wildcard)

    def test_csrf_untrusted_origins_not_in_whitelist(self):
        """
        Unconfigured third-party origins must not be trusted.
        """
        untrusted_origin = "https://attacker.vercel.app"
        self.assertNotIn(untrusted_origin, settings.CSRF_TRUSTED_ORIGINS)

    # =========================================================================
    # 3. Authentication & Session Removal Verification
    # =========================================================================
    def test_session_authentication_removed_from_drf(self):
        """
        DRF DEFAULT_AUTHENTICATION_CLASSES must only contain SupabaseJWTAuthentication.
        SessionAuthentication must be absent.
        """
        auth_classes = settings.REST_FRAMEWORK.get('DEFAULT_AUTHENTICATION_CLASSES', [])
        self.assertNotIn('rest_framework.authentication.SessionAuthentication', auth_classes)
        self.assertIn('config.authentication.SupabaseJWTAuthentication', auth_classes)

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_valid_supabase_bearer_jwt_authenticates_protected_endpoint(self):
        """
        Valid Supabase Bearer JWT successfully authenticates against /api/auth/me/.
        """
        user_uuid = uuid.uuid4()
        token = self._generate_valid_jwt(user_uuid, "alice@talentmatch.ai")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get("authenticated"))
        self.assertEqual(response.data.get("user", {}).get("email"), "alice@talentmatch.ai")

    @override_settings(SUPABASE_JWT_SECRET=TEST_JWT_SECRET)
    def test_expired_supabase_bearer_jwt_rejected(self):
        """
        Expired Supabase Bearer JWT is rejected with 401 Unauthorized.
        """
        now = int(time.time())
        supabase_url = getattr(settings, 'SUPABASE_URL', '').rstrip('/')
        payload = {
            "sub": str(uuid.uuid4()),
            "email": "expired@talentmatch.ai",
            "aud": "authenticated",
            "iss": f"{supabase_url}/auth/v1",
            "exp": now - 3600,
        }
        token = jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_anonymous_request_rejected_on_protected_endpoint(self):
        """
        Anonymous request to /api/auth/me/ is rejected with 401 Unauthorized.
        """
        self.client.credentials()  # clear credentials
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_django_session_cookie_cannot_authenticate_api(self):
        """
        Verify that an ambient Django session cookie CANNOT authenticate protected API endpoints.
        Because SessionAuthentication was removed from DRF, requests relying on session cookies
        are rejected with 401 Unauthorized.
        """
        user = User.objects.create_user(
            username="admin_session_user",
            password="test-password-12345",
            email="admin_session@talentmatch.ai"
        )
        self.client.login(username="admin_session_user", password="test-password-12345")
        # No Authorization Bearer header provided
        self.client.credentials()
        response = self.client.get("/api/auth/me/")
        # Must be rejected because DRF does not accept session authentication
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
