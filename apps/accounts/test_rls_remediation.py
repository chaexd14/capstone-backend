import uuid
import re
from pathlib import Path
from django.test import TestCase, override_settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status

from apps.accounts.models import UserRole, RoleChoices
from apps.jobs.models import Job, JobStatus
from apps.applications.models import Application, ApplicationStatus
from apps.resumes.models import Resume, MatchResult
from apps.notifications.models import Notification, RecipientType
from apps.applications.serializers import ApplicantApplicationSerializer, RecruiterApplicationSerializer
from apps.resumes.serializers import ApplicantResumeSerializer, RecruiterResumeSerializer


class RLSMigrationStaticVerificationTests(TestCase):
    """
    Automated Static Verification of PostgreSQL/Supabase Row Level Security
    migrations, dependency graphs, policy definitions, and safety guards.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.migrations_dir = Path(__file__).resolve().parent.parent

    def test_migration_dependency_ordering_guarantees_column_existence(self):
        """
        Verification Finding 2:
        resumes.0005_resumes_rls references applications_application(user_id).
        Ensure that on an empty/unapplied database, applications.0002_application_user_id
        is planned and executed strictly BEFORE resumes.0005_resumes_rls.
        """
        executor = MigrationExecutor(connections['default'])
        executor.loader.applied_migrations = set()
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())

        plan_names = [f"{m[0].app_label}.{m[0].name}" for m in plan]

        self.assertIn("applications.0002_application_user_id", plan_names)
        self.assertIn("resumes.0005_resumes_rls", plan_names)

        app_0002_idx = plan_names.index("applications.0002_application_user_id")
        res_0005_idx = plan_names.index("resumes.0005_resumes_rls")

        self.assertLess(
            app_0002_idx,
            res_0005_idx,
            "CRITICAL: applications.0002_application_user_id must execute BEFORE resumes.0005_resumes_rls "
            "to prevent 'column user_id does not exist' failures on fresh databases."
        )

    def test_accounts_0002_prerequisites_all_precede_it(self):
        """
        Verification Finding 2:
        accounts.0002_harden_rls_trusted_roles references tables from accounts, jobs,
        applications, notifications, and resumes. Ensure all prerequisite migrations precede it.
        """
        executor = MigrationExecutor(connections['default'])
        executor.loader.applied_migrations = set()
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        plan_names = [f"{m[0].app_label}.{m[0].name}" for m in plan]

        accounts_0002_idx = plan_names.index("accounts.0002_harden_rls_trusted_roles")

        prereqs = [
            "accounts.0001_initial",
            "jobs.0005_jobs_rls",
            "applications.0005_applications_rls",
            "notifications.0002_notifications_rls",
            "resumes.0005_resumes_rls",
        ]

        for prereq in prereqs:
            self.assertIn(prereq, plan_names)
            prereq_idx = plan_names.index(prereq)
            self.assertLess(
                prereq_idx,
                accounts_0002_idx,
                f"Prerequisite {prereq} must execute before accounts.0002_harden_rls_trusted_roles"
            )

    def test_authoritative_policies_do_not_trust_user_metadata(self):
        """
        Verification Finding 1:
        Verify that active policies created in accounts.0002 and resumes.0006
        do NOT use user_metadata or app_metadata for authorization.
        """
        accounts_0002_path = self.migrations_dir / 'accounts' / 'migrations' / '0002_harden_rls_trusted_roles.py'
        resumes_0006_path = self.migrations_dir / 'resumes' / 'migrations' / '0006_recruiter_only_match_results_rls.py'

        for file_path in [accounts_0002_path, resumes_0006_path]:
            content = file_path.read_text(encoding='utf-8')

            # Extract CREATE POLICY statements
            policies = re.findall(r'CREATE\s+POLICY\s+.*?;', content, re.DOTALL | re.IGNORECASE)
            self.assertGreater(len(policies), 0, f"Expected policies in {file_path.name}")

            for policy in policies:
                self.assertNotIn(
                    'user_metadata',
                    policy,
                    f"Policy in {file_path.name} must NOT reference user_metadata: {policy}"
                )
                self.assertNotIn(
                    'app_metadata',
                    policy,
                    f"Policy in {file_path.name} must NOT reference app_metadata: {policy}"
                )

    def test_no_applicant_facing_select_policy_on_match_results(self):
        """
        Verification Finding 7:
        Verify that neither accounts.0002 nor resumes.0006 creates an applicant SELECT
        policy on resumes_matchresult.
        """
        accounts_0002_path = self.migrations_dir / 'accounts' / 'migrations' / '0002_harden_rls_trusted_roles.py'
        resumes_0006_path = self.migrations_dir / 'resumes' / 'migrations' / '0006_recruiter_only_match_results_rls.py'

        for file_path in [accounts_0002_path, resumes_0006_path]:
            content = file_path.read_text(encoding='utf-8')

            # Ensure no policy grants applicants SELECT access on match results
            match_policies = re.findall(
                r'CREATE\s+POLICY\s+"([^"]+)"\s+ON\s+public\.resumes_matchresult',
                content,
                re.IGNORECASE
            )
            for pol_name in match_policies:
                self.assertNotIn(
                    'applicant',
                    pol_name.lower(),
                    f"Forbidden applicant policy '{pol_name}' on resumes_matchresult found in {file_path.name}"
                )

    def test_reverse_migrations_do_not_disable_rls_or_grant_anon(self):
        """
        Verification Finding 6:
        Reverse operations must NOT execute 'DISABLE ROW LEVEL SECURITY'
        or 'GRANT ALL ... TO anon'.
        """
        rls_migration_paths = [
            self.migrations_dir / 'jobs' / 'migrations' / '0004_archivedjob_rls.py',
            self.migrations_dir / 'jobs' / 'migrations' / '0005_jobs_rls.py',
            self.migrations_dir / 'applications' / 'migrations' / '0005_applications_rls.py',
            self.migrations_dir / 'notifications' / 'migrations' / '0002_notifications_rls.py',
            self.migrations_dir / 'resumes' / 'migrations' / '0005_resumes_rls.py',
            self.migrations_dir / 'accounts' / 'migrations' / '0002_harden_rls_trusted_roles.py',
            self.migrations_dir / 'resumes' / 'migrations' / '0006_recruiter_only_match_results_rls.py',
        ]

        for file_path in rls_migration_paths:
            content = file_path.read_text(encoding='utf-8')

            # Look for reverse_code functions or operations
            self.assertNotIn(
                'DISABLE ROW LEVEL SECURITY',
                content,
                f"Destructive 'DISABLE ROW LEVEL SECURITY' found in {file_path.name}"
            )
            self.assertNotIn(
                'GRANT ALL ON TABLE public.resumes_matchresult TO anon',
                content,
                f"Destructive grant to anon found in {file_path.name}"
            )

    def test_helper_function_safety_and_explicit_search_path(self):
        """
        Verification Finding 3:
        Verify public.is_recruiter_or_superadmin():
        - Has explicit search_path = public, auth, pg_temp
        - References public.accounts_userrole and auth.uid()
        - Explicitly revokes EXECUTE from PUBLIC and anon
        """
        accounts_0002_path = self.migrations_dir / 'accounts' / 'migrations' / '0002_harden_rls_trusted_roles.py'
        content = accounts_0002_path.read_text(encoding='utf-8')

        self.assertIn("SET search_path = public, auth, pg_temp", content)
        self.assertIn("public.accounts_userrole", content)
        self.assertIn("auth.uid()", content)
        self.assertIn("REVOKE ALL ON FUNCTION public.is_recruiter_or_superadmin() FROM PUBLIC;", content)
        self.assertIn("REVOKE ALL ON FUNCTION public.is_recruiter_or_superadmin() FROM anon;", content)


class RLSBackendAPIAuthorizationTests(TestCase):
    """
    Independent API-level verification that applicant requests cannot access
    match results, AI evaluation details, recruiter notes, or other applicants' data.
    """

    def setUp(self):
        self.client = APIClient()
        self.job = Job.objects.create(
            title="Privacy Auditor",
            department="Compliance",
            location="Remote",
            status=JobStatus.PUBLISHED
        )

        self.applicant_a_uuid = uuid.uuid4()
        self.applicant_b_uuid = uuid.uuid4()
        self.recruiter_uuid = uuid.uuid4()

        self.app_a = Application.objects.create(
            job=self.job,
            first_name="Alice",
            last_name="Applicant",
            email="alice@privacy.test",
            status=ApplicationStatus.SUBMITTED,
            user_id=self.applicant_a_uuid,
            notes="Confidential recruiter interview notes."
        )
        self.resume_a = Resume.objects.create(
            application=self.app_a,
            file="resumes/alice.pdf",
            extracted_text="Alice's private resume raw text",
            redacted_text="Redacted text",
            processing_status="COMPLETED"
        )
        self.match_a = MatchResult.objects.create(
            application=self.app_a,
            match_score=94.5,
            skill_match_score=90.0,
            experience_match_score=95.0,
            education_match_score=100.0,
            semantic_match_score=92.0,
            ai_insights={"strengths": ["RLS verification", "Security testing"]},
            explanation="Strong candidate with proven compliance background."
        )

        # Provision authoritative roles in UserRole table
        UserRole.objects.create(
            supabase_user_id=self.recruiter_uuid,
            email="recruiter@privacy.test",
            role=RoleChoices.RECRUITER
        )
        UserRole.objects.create(
            supabase_user_id=self.applicant_a_uuid,
            email="alice@privacy.test",
            role=RoleChoices.APPLICANT
        )

    def test_applicant_serializer_excludes_all_match_and_evaluation_data(self):
        """
        Security Test: ApplicantApplicationSerializer strictly excludes
        match_result, internal recruiter notes, and tracking tokens.
        """
        serializer = ApplicantApplicationSerializer(self.app_a)
        data = serializer.data

        self.assertNotIn('match_result', data)
        self.assertNotIn('notes', data)
        self.assertNotIn('tracking_token', data)

    def test_applicant_resume_serializer_excludes_extracted_and_redacted_text(self):
        """
        Security Test: ApplicantResumeSerializer strictly excludes
        extracted_text, redacted_text, and redacted_data.
        """
        serializer = ApplicantResumeSerializer(self.resume_a)
        data = serializer.data

        self.assertNotIn('extracted_text', data)
        self.assertNotIn('redacted_text', data)
        self.assertNotIn('redacted_data', data)

    def test_recruiter_serializer_includes_evaluation_dossier(self):
        """
        Security Test: RecruiterApplicationSerializer provides full dossier
        for authorized recruiters.
        """
        serializer = RecruiterApplicationSerializer(self.app_a)
        data = serializer.data

        self.assertIn('match_result', data)
        self.assertIn('notes', data)
        self.assertEqual(data['match_result']['match_score'], 94.5)

    def test_applicant_cannot_access_match_details_endpoint(self):
        """
        Security Test: GET /api/applications/{id}/match/ requires IsRecruiterOrSuperAdmin.
        An applicant caller must be rejected with 403 Forbidden.
        """
        # Create user instance for Applicant A
        user_applicant, _ = User.objects.get_or_create(
            username=str(self.applicant_a_uuid),
            defaults={"email": "alice@privacy.test"}
        )
        user_applicant.role = "applicant"
        user_applicant.supabase_id = str(self.applicant_a_uuid)
        user_applicant.is_staff = False
        user_applicant.save()

        self.client.force_authenticate(user=user_applicant)

        res = self.client.get(f"/api/applications/{self.app_a.id}/match/")
        self.assertEqual(
            res.status_code,
            status.HTTP_403_FORBIDDEN,
            "Applicant must NEVER be able to retrieve candidate match result evaluations."
        )

    def test_recruiter_can_access_match_details_endpoint(self):
        """
        Security Test: An authorized recruiter can retrieve match details.
        """
        user_recruiter, _ = User.objects.get_or_create(
            username=str(self.recruiter_uuid),
            defaults={"email": "recruiter@privacy.test"}
        )
        user_recruiter.role = "recruiter"
        user_recruiter.supabase_id = str(self.recruiter_uuid)
        user_recruiter.is_staff = True
        user_recruiter.save()

        self.client.force_authenticate(user=user_recruiter)

        res = self.client.get(f"/api/applications/{self.app_a.id}/match/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['match_score'], 94.5)


class UserRoleProvisioningAndPrivilegeTests(TestCase):
    """
    Validation of concurrency safety, database constraints, superuser privileges,
    and role demotion synchronization.
    """

    def setUp(self):
        from config.authentication import SupabaseJWTAuthentication
        self.auth = SupabaseJWTAuthentication()

    def test_database_uniqueness_constraint_on_supabase_user_id(self):
        """
        Finding 1: Ensure database uniqueness constraint exists on UserRole.supabase_user_id.
        """
        user_uuid = uuid.uuid4()
        UserRole.objects.create(
            supabase_user_id=user_uuid,
            email="unique_test_1@talentmatch.ai",
            role=RoleChoices.APPLICANT
        )

        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            UserRole.objects.create(
                supabase_user_id=user_uuid,
                email="unique_test_2@talentmatch.ai",
                role=RoleChoices.APPLICANT
            )

    def test_concurrency_safe_applicant_provisioning_handles_duplicate(self):
        """
        Finding 1: Concurrency safety simulation.
        If UserRole was already created by a competing thread/worker, authenticate()
        handles the situation cleanly without crashing or leaving orphaned records.
        """
        user_uuid = uuid.uuid4()
        user_a = User.objects.create(username=str(user_uuid), email="race@test.com")
        UserRole.objects.create(
            user=user_a,
            supabase_user_id=user_uuid,
            email="race@test.com",
            role=RoleChoices.APPLICANT
        )

        import jwt
        token = jwt.encode(
            {
                "sub": str(user_uuid),
                "email": "race@test.com",
                "aud": "authenticated",
                "iss": "https://test.supabase.co/auth/v1",
                "exp": 9999999999,
            },
            "test-secret-min-32-chars-length-valid-key",
            algorithm="HS256"
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with override_settings(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_JWT_SECRET="test-secret-min-32-chars-length-valid-key"
        ):
            resolved_user, _ = self.auth.authenticate(DummyRequest())
            self.assertEqual(resolved_user.username, str(user_uuid))
            self.assertEqual(resolved_user.role, RoleChoices.APPLICANT)
            self.assertFalse(resolved_user.is_staff)
            self.assertFalse(resolved_user.is_superuser)

    def test_superadmin_role_does_not_grant_django_is_superuser(self):
        """
        Finding 2: TalentMatch superadmin receives is_staff=True for staff access,
        but is_superuser=False to avoid unrestricted Django model permission bypass.
        """
        admin_uuid = uuid.uuid4()
        UserRole.objects.create(
            supabase_user_id=admin_uuid,
            email="superadmin@talentmatch.ai",
            role=RoleChoices.SUPERADMIN
        )

        import jwt
        token = jwt.encode(
            {
                "sub": str(admin_uuid),
                "email": "superadmin@talentmatch.ai",
                "aud": "authenticated",
                "iss": "https://test.supabase.co/auth/v1",
                "exp": 9999999999,
            },
            "test-secret-min-32-chars-length-valid-key",
            algorithm="HS256"
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with override_settings(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_JWT_SECRET="test-secret-min-32-chars-length-valid-key"
        ):
            resolved_user, _ = self.auth.authenticate(DummyRequest())
            self.assertEqual(resolved_user.role, RoleChoices.SUPERADMIN)
            self.assertFalse(resolved_user.is_staff)  # Application-level role authorization: is_staff remains False
            self.assertFalse(resolved_user.is_superuser)

    def test_role_demotion_immediately_revokes_privileges(self):
        """
        Finding 2: When an administrator demotes a user from superadmin/recruiter
        to applicant, subsequent requests immediately strip is_staff and is_superuser.
        """
        user_uuid = uuid.uuid4()
        ur = UserRole.objects.create(
            supabase_user_id=user_uuid,
            email="demotee@talentmatch.ai",
            role=RoleChoices.SUPERADMIN
        )

        import jwt
        token = jwt.encode(
            {
                "sub": str(user_uuid),
                "email": "demotee@talentmatch.ai",
                "aud": "authenticated",
                "iss": "https://test.supabase.co/auth/v1",
                "exp": 9999999999,
            },
            "test-secret-min-32-chars-length-valid-key",
            algorithm="HS256"
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with override_settings(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_JWT_SECRET="test-secret-min-32-chars-length-valid-key"
        ):
            # First request: recognized as superadmin (role='superadmin', is_staff=False)
            u1, _ = self.auth.authenticate(DummyRequest())
            self.assertEqual(u1.role, RoleChoices.SUPERADMIN)
            self.assertFalse(u1.is_staff)

            # Demote in database to applicant
            ur.role = RoleChoices.APPLICANT
            ur.save(update_fields=['role'])

            # Second request: immediately synchronized
            u2, _ = self.auth.authenticate(DummyRequest())
            self.assertEqual(u2.role, RoleChoices.APPLICANT)
            self.assertFalse(u2.is_staff)
            self.assertFalse(u2.is_superuser)

    def test_conflicting_userrole_association_fails_closed_and_logs_security_event(self):
        """
        Finding 1: If an existing UserRole or User association conflicts with the
        authenticated Supabase UUID, authentication must fail closed with an error.
        """
        user_uuid_1 = uuid.uuid4()
        user_uuid_2 = uuid.uuid4()

        # Create user 1 with UserRole pointing to user_uuid_1
        user_1 = User.objects.create(username=str(user_uuid_1), email="user1@talentmatch.ai")
        ur = UserRole.objects.create(
            user=user_1,
            supabase_user_id=user_uuid_1,
            email="user1@talentmatch.ai",
            role=RoleChoices.APPLICANT
        )

        # Attacker crafts a token with user_uuid_1, but UserRole was somehow linked to another user
        user_2 = User.objects.create(username="different_user_login", email="user2@talentmatch.ai")
        ur.user = user_2
        ur.save(update_fields=['user'])

        import jwt
        token = jwt.encode(
            {
                "sub": str(user_uuid_1),
                "email": "user1@talentmatch.ai",
                "aud": "authenticated",
                "iss": "https://test.supabase.co/auth/v1",
                "exp": 9999999999,
            },
            "test-secret-min-32-chars-length-valid-key",
            algorithm="HS256"
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        from rest_framework.exceptions import AuthenticationFailed
        with override_settings(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_JWT_SECRET="test-secret-min-32-chars-length-valid-key"
        ):
            with self.assertRaises(AuthenticationFailed) as ctx:
                self.auth.authenticate(DummyRequest())
            self.assertIn("conflict", str(ctx.exception).lower())

    def test_unexpected_integrity_error_is_not_concealed(self):
        """
        Finding 1: An unexpected database integrity error during user provisioning
        is re-raised and never swallowed.
        """
        from unittest.mock import patch
        from django.db import IntegrityError

        user_uuid = uuid.uuid4()
        import jwt
        token = jwt.encode(
            {
                "sub": str(user_uuid),
                "email": "unexpected_error@talentmatch.ai",
                "aud": "authenticated",
                "iss": "https://test.supabase.co/auth/v1",
                "exp": 9999999999,
            },
            "test-secret-min-32-chars-length-valid-key",
            algorithm="HS256"
        )

        class DummyRequest:
            headers = {"Authorization": f"Bearer {token}"}

        with override_settings(
            SUPABASE_URL="https://test.supabase.co",
            SUPABASE_JWT_SECRET="test-secret-min-32-chars-length-valid-key"
        ):
            with patch('django.contrib.auth.models.User.objects.create', side_effect=IntegrityError("Foreign key violation")):
                with self.assertRaises(IntegrityError):
                    self.auth.authenticate(DummyRequest())


class ResumeDownloadSecurityAndContentNegotiationTests(TestCase):
    """
    Verification of resume download content negotiation, audit logging safety,
    trusted proxy IP handling, and pre-signed URL security.
    """

    def setUp(self):
        self.client = APIClient()
        self.job = Job.objects.create(
            title="Download Tester Job",
            department="QA",
            location="Remote",
            status=JobStatus.PUBLISHED
        )
        self.applicant_uuid = uuid.uuid4()
        self.app = Application.objects.create(
            job=self.job,
            first_name="Dana",
            last_name="Download",
            email="dana@test.com",
            status=ApplicationStatus.SUBMITTED,
            user_id=self.applicant_uuid
        )

        from django.core.files.uploadedfile import SimpleUploadedFile
        test_file = SimpleUploadedFile("sample.pdf", b"%PDF-1.4 test resume", content_type="application/pdf")
        self.resume = Resume.objects.create(
            application=self.app,
            file=test_file,
            original_filename="sample.pdf"
        )

        self.user = User.objects.create(username=str(self.applicant_uuid), email="dana@test.com")
        self.user.role = "applicant"
        self.user.supabase_id = str(self.applicant_uuid)
        self.user.is_staff = False
        self.user.save()

    def test_download_content_negotiation_json_via_accept_header(self):
        """
        Finding 3: Content negotiation via complex Accept header returns JSON payload.
        """
        self.client.force_authenticate(user=self.user)
        res = self.client.get(
            f"/api/resumes/{self.resume.id}/download/",
            HTTP_ACCEPT="application/json, text/plain, */*"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res.data)
        self.assertIn('expires_in', res.data)

    def test_download_content_negotiation_json_via_redirect_false(self):
        """
        Finding 3: Query parameter ?redirect=false returns JSON payload.
        """
        self.client.force_authenticate(user=self.user)
        res = self.client.get(f"/api/resumes/{self.resume.id}/download/?redirect=false")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res.data)
        self.assertIn('expires_in', res.data)

    def test_audit_logging_failure_does_not_break_download(self):
        """
        Finding 3: If audit_logger throws an exception, the download must still succeed.
        """
        from unittest.mock import patch
        self.client.force_authenticate(user=self.user)

        with patch('apps.resumes.views.audit_logger.info', side_effect=IOError("Disk log full")):
            res = self.client.get(f"/api/resumes/{self.resume.id}/download/?redirect=false")
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            self.assertIn('download_url', res.data)

    def test_audit_event_outcome_is_signed_url_issued(self):
        """
        Finding 3: Audit log must record SIGNED_URL_ISSUED rather than SUCCESS,
        accurately indicating that a signed storage URL was generated.
        """
        from unittest.mock import patch
        self.client.force_authenticate(user=self.user)

        with patch('apps.resumes.views.audit_logger.info') as mock_audit:
            res = self.client.get(f"/api/resumes/{self.resume.id}/download/?redirect=false")
            self.assertEqual(res.status_code, status.HTTP_200_OK)
            mock_audit.assert_called_once()
            log_msg = mock_audit.call_args[0][0]
            self.assertIn("outcome=SIGNED_URL_ISSUED", log_msg)
            self.assertNotIn("outcome=SUCCESS", log_msg)

    def test_content_negotiation_competing_media_types_and_quality_values(self):
        """
        Finding 4: Proper content negotiation with quality factor parsing.
        """
        self.client.force_authenticate(user=self.user)

        # 1. Browser navigation Accept header (text/html without application/json): Redirects
        res_browser = self.client.get(
            f"/api/resumes/{self.resume.id}/download/",
            HTTP_ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )
        self.assertEqual(res_browser.status_code, status.HTTP_302_FOUND)

        # 2. Browser with text/html higher quality than application/json: Redirects
        res_html_pref = self.client.get(
            f"/api/resumes/{self.resume.id}/download/",
            HTTP_ACCEPT="text/html;q=1.0, application/json;q=0.5"
        )
        self.assertEqual(res_html_pref.status_code, status.HTTP_302_FOUND)

        # 3. Client with application/json higher quality than text/html: Returns JSON
        res_json_pref = self.client.get(
            f"/api/resumes/{self.resume.id}/download/",
            HTTP_ACCEPT="application/json;q=0.9, text/html;q=0.5"
        )
        self.assertEqual(res_json_pref.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res_json_pref.data)

        # 4. Explicit ?format=json overrides browser HTML Accept header
        res_fmt = self.client.get(
            f"/api/resumes/{self.resume.id}/download/?format=json",
            HTTP_ACCEPT="text/html,application/xhtml+xml"
        )
        self.assertEqual(res_fmt.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res_fmt.data)

        # 5. Explicit ?redirect=false overrides browser HTML Accept header
        res_red = self.client.get(
            f"/api/resumes/{self.resume.id}/download/?redirect=false",
            HTTP_ACCEPT="text/html,application/xhtml+xml"
        )
        self.assertEqual(res_red.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res_red.data)

    def test_trusted_proxy_ip_handling_and_spoofing_prevention(self):
        """
        Finding 3 & Follow-up Finding 2:
        Proper IP parsing with ipaddress module, CIDR subnets, and right-to-left
        X-Forwarded-For traversal preventing spoofing attacks.
        """
        from apps.resumes.views import get_trusted_client_ip

        class MockRequest:
            def __init__(self, remote_addr, x_forwarded_for=None):
                self.META = {'REMOTE_ADDR': remote_addr}
                if x_forwarded_for:
                    self.META['HTTP_X_FORWARDED_FOR'] = x_forwarded_for

        with override_settings(TRUSTED_PROXY_IPS=['127.0.0.1', '10.0.0.0/8', '172.16.0.0/12', '::1']):
            # 1. Untrusted remote peer: ignore X-Forwarded-For completely
            req_untrusted = MockRequest(remote_addr='198.51.100.5', x_forwarded_for='203.0.113.195')
            self.assertEqual(get_trusted_client_ip(req_untrusted), '198.51.100.5')

            # 2. Spoofing attempt: Malicious client prepends fake IP to X-Forwarded-For
            # Malicious client sends "X-Forwarded-For: 1.1.1.1"
            # Reverse proxy (10.0.0.1) appends client IP: "1.1.1.1, 203.0.113.195"
            req_spoofed = MockRequest(remote_addr='10.0.0.1', x_forwarded_for='1.1.1.1, 203.0.113.195')
            # Right-to-left traversal must pick 203.0.113.195 and ignore the spoofed 1.1.1.1
            self.assertEqual(get_trusted_client_ip(req_spoofed), '203.0.113.195')

            # 3. Multi-proxy trusted chain:
            # Client (198.51.100.10) -> Cloudflare proxy (172.16.0.2) -> Ingress (10.0.0.5) -> Backend (127.0.0.1)
            req_chain = MockRequest(
                remote_addr='127.0.0.1',
                x_forwarded_for='198.51.100.10, 172.16.0.2, 10.0.0.5'
            )
            self.assertEqual(get_trusted_client_ip(req_chain), '198.51.100.10')

            # 4. Port stripping in IPv4
            req_port = MockRequest(remote_addr='10.0.0.1', x_forwarded_for='203.0.113.195:49152')
            self.assertEqual(get_trusted_client_ip(req_port), '203.0.113.195')

    def test_unauthorized_user_cannot_access_download(self):
        """
        Finding 3: An unauthenticated caller or unauthorized applicant cannot generate a signed URL.
        """
        # Anonymous user
        res_anon = self.client.get(f"/api/resumes/{self.resume.id}/download/")
        self.assertEqual(res_anon.status_code, status.HTTP_401_UNAUTHORIZED)

        # Another applicant
        other_user = User.objects.create(username=str(uuid.uuid4()), email="other@test.com")
        other_user.role = "applicant"
        other_user.supabase_id = str(uuid.uuid4())
        other_user.is_staff = False
        other_user.save()

        self.client.force_authenticate(user=other_user)
        res_other = self.client.get(f"/api/resumes/{self.resume.id}/download/")
        self.assertIn(res_other.status_code, (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN))


class UploadLimitsAndExpirationTests(TestCase):
    """
    Verification of maximum upload size validation and pre-signed URL expiration.
    """

    def test_resume_upload_exceeding_max_size_rejected(self):
        """
        Finding 5: Upload exceeding MAX_RESUME_UPLOAD_SIZE is rejected with 400.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.applications.serializers import ApplicationCreateSerializer

        # Create a file that exceeds the 10MB limit (e.g. 11MB)
        large_file = SimpleUploadedFile(
            "large_resume.pdf",
            b"0" * (11 * 1024 * 1024),
            content_type="application/pdf"
        )

        serializer = ApplicationCreateSerializer()
        from rest_framework.exceptions import ValidationError
        with override_settings(MAX_RESUME_UPLOAD_SIZE=10 * 1024 * 1024):
            with self.assertRaises(ValidationError) as ctx:
                serializer.validate_resume(large_file)
            self.assertIn("exceeds maximum limit", str(ctx.exception))

    def test_signed_url_expiration_reflects_storage_configuration(self):
        """
        Finding 6: get_signed_url_expiration reflects R2_QUERYSTRING_EXPIRE setting.
        """
        from apps.resumes.views import get_signed_url_expiration

        with override_settings(R2_QUERYSTRING_EXPIRE=600):
            self.assertEqual(get_signed_url_expiration(), 600)

        with override_settings(R2_QUERYSTRING_EXPIRE=900):
            self.assertEqual(get_signed_url_expiration(), 900)

    def test_failed_upload_cleans_up_orphaned_storage_file(self):
        """
        Follow-up Finding 1: If database persistence fails after physical storage write,
        the uploaded file must be deleted from storage to prevent orphaned files.
        """
        from unittest.mock import patch
        from django.db import DatabaseError
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.core.files.storage import default_storage
        from apps.applications.serializers import ApplicationCreateSerializer

        job = Job.objects.create(
            title="Cleanup Test Job",
            department="DevOps",
            location="Remote",
            status=JobStatus.PUBLISHED
        )
        test_file = SimpleUploadedFile("candidate_resume.pdf", b"%PDF-1.4 test data", content_type="application/pdf")

        serializer = ApplicationCreateSerializer(data={
            "job": job.id,
            "first_name": "Test",
            "last_name": "Applicant",
            "email": "cleanup_test@example.com",
            "phone": "+63 917 123 4567",
            "resume": test_file,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)

        deleted_files = []
        original_delete = default_storage.delete

        def tracking_delete(name):
            deleted_files.append(name)
            return original_delete(name)

        with patch.object(default_storage, 'delete', side_effect=tracking_delete):
            with patch.object(Resume, 'save', side_effect=DatabaseError("Simulated DB connection crash")):
                with self.assertRaises(DatabaseError):
                    serializer.save()

        # Storage delete MUST have been called with the uploaded file path
        self.assertGreater(len(deleted_files), 0)
        self.assertFalse(default_storage.exists(deleted_files[0]))


class TrackingTokenPublicStatusTests(TestCase):
    """
    Verification of the separate, narrowly scoped public tracking-status endpoint.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = APIClient()
        self.job = Job.objects.create(
            title="Tracking Test Role",
            department="Operations",
            location="Remote",
            status=JobStatus.PUBLISHED
        )
        self.tracking_uuid = uuid.uuid4()
        self.app = Application.objects.create(
            job=self.job,
            first_name="Eve",
            last_name="Secret",
            email="eve_private@test.com",
            phone="+63 917 999 8888",
            status=ApplicationStatus.UNDER_REVIEW,
            tracking_token=self.tracking_uuid
        )
        self.resume = Resume.objects.create(
            application=self.app,
            file="resumes/eve.pdf",
            processing_status="COMPLETED"
        )
        self.match = MatchResult.objects.create(
            application=self.app,
            match_score=88.0,
            ai_insights={"notes": "Confidential internal evaluation"}
        )

    def test_public_tracking_status_returns_narrow_status_without_pii(self):
        """
        Finding 7 & Follow-up Finding 3: POST /api/applications/track-status/ returns status and job title,
        with strictly NO applicant PII, NO resume URL, and NO match results.
        """
        res = self.client.post(
            "/api/applications/track-status/",
            {"token": str(self.tracking_uuid)},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        data = res.data
        self.assertEqual(data['status'], ApplicationStatus.UNDER_REVIEW)
        self.assertEqual(data['job_title'], "Tracking Test Role")
        self.assertEqual(data['job_department'], "Operations")
        self.assertEqual(data['processing_status'], "COMPLETED")

        # PII, resume files, and evaluations must NEVER be present
        self.assertNotIn('email', data)
        self.assertNotIn('phone', data)
        self.assertNotIn('first_name', data)
        self.assertNotIn('last_name', data)
        self.assertNotIn('applicant_name', data)
        self.assertNotIn('resume', data)
        self.assertNotIn('file', data)
        self.assertNotIn('match_score', data)
        self.assertNotIn('match_result', data)
        self.assertNotIn('notes', data)

    def test_tracking_status_get_method_rejected_with_405(self):
        """
        Follow-up Finding 3: GET /api/applications/track-status/ is rejected with HTTP 405
        to prevent tracking token exposure in browser history and proxy access logs.
        """
        res = self.client.get(f"/api/applications/track-status/?token={self.tracking_uuid}")
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_tracking_status_invalid_or_missing_token_rejected(self):
        """
        Finding 7 & Follow-up Finding 3: Missing or invalid UUID token returns 400; non-existent returns 404.
        """
        # Missing token in body
        res_missing = self.client.post("/api/applications/track-status/", {}, format="json")
        self.assertEqual(res_missing.status_code, status.HTTP_400_BAD_REQUEST)

        # Invalid token format
        res_invalid = self.client.post("/api/applications/track-status/", {"token": "not-a-uuid"}, format="json")
        self.assertEqual(res_invalid.status_code, status.HTTP_400_BAD_REQUEST)

        # Non-existent token
        res_not_found = self.client.post("/api/applications/track-status/", {"token": str(uuid.uuid4())}, format="json")
        self.assertEqual(res_not_found.status_code, status.HTTP_404_NOT_FOUND)

    def test_tracking_token_does_not_grant_access_to_private_application_endpoints(self):
        """
        Finding 7: A tracking token does NOT authenticate access to GET /api/applications/{id}/.
        """
        res = self.client.get(f"/api/applications/{self.app.id}/?tracking_token={self.tracking_uuid}")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_public_tracking_status_via_post_method(self):
        """
        Follow-up Finding 2: POST /api/applications/track-status/ accepts token in request body,
        preventing URL logging, referrer leakage, and browser history exposure.
        """
        res = self.client.post(
            "/api/applications/track-status/",
            {"token": str(self.tracking_uuid)},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['status'], ApplicationStatus.UNDER_REVIEW)
        self.assertEqual(res.data['job_title'], "Tracking Test Role")

    def test_tracking_status_cache_control_no_store(self):
        """
        Follow-up Finding 2 & 3: Response includes Cache-Control: no-store to prevent caching.
        """
        res_post = self.client.post(
            "/api/applications/track-status/",
            {"token": str(self.tracking_uuid)},
            format="json"
        )
        self.assertEqual(res_post.status_code, status.HTTP_200_OK)
        self.assertIn("no-store", res_post.headers.get("Cache-Control", ""))

    def test_tracking_status_revoked_and_rotated_tokens(self):
        """
        Follow-up Finding 2: Revoking or rotating tracking tokens immediately blocks status access.
        """
        old_token = self.tracking_uuid

        # 1. Rotate token
        new_token = uuid.uuid4()
        self.app.tracking_token = new_token
        self.app.save(update_fields=['tracking_token'])

        # Old token fails
        res_old = self.client.post(
            "/api/applications/track-status/",
            {"token": str(old_token)},
            format="json"
        )
        self.assertEqual(res_old.status_code, status.HTTP_404_NOT_FOUND)

        # New token succeeds
        res_new = self.client.post(
            "/api/applications/track-status/",
            {"token": str(new_token)},
            format="json"
        )
        self.assertEqual(res_new.status_code, status.HTTP_200_OK)

        # 2. Revoke token completely (set to None)
        self.app.tracking_token = None
        self.app.save(update_fields=['tracking_token'])

        res_revoked = self.client.post(
            "/api/applications/track-status/",
            {"token": str(new_token)},
            format="json"
        )
        self.assertEqual(res_revoked.status_code, status.HTTP_404_NOT_FOUND)

    def test_tracking_status_rate_limiting(self):
        """
        Follow-up Finding 2: Rate limit of 20 requests/minute enforces HTTP 429 on excessive requests.
        """
        from django.core.cache import cache
        cache.clear()

        # Send 20 requests (all should succeed)
        for _ in range(20):
            res = self.client.post(
                "/api/applications/track-status/",
                {"token": str(self.tracking_uuid)},
                format="json"
            )
            self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 21st request must trigger HTTP 429 Too Many Requests
        res_throttled = self.client.post(
            "/api/applications/track-status/",
            {"token": str(self.tracking_uuid)},
            format="json"
        )
        self.assertEqual(res_throttled.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_oversized_resume_upload_via_job_apply_endpoint_rejected(self):
        """
        Follow-up Finding 6: Uploading a resume exceeding MAX_RESUME_UPLOAD_SIZE
        via POST /api/jobs/{id}/apply/ is rejected with HTTP 400.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload_uuid = str(uuid.uuid4())
        applicant_user = User.objects.create_user(username=upload_uuid, email="upload@applicant.com")
        applicant_user.role = RoleChoices.APPLICANT
        applicant_user.supabase_id = upload_uuid
        self.client.force_authenticate(user=applicant_user)

        oversized_pdf = SimpleUploadedFile(
            "large_resume.pdf",
            b"%PDF-1.4 " + b"0" * (11 * 1024 * 1024),
            content_type="application/pdf"
        )

        with override_settings(MAX_RESUME_UPLOAD_SIZE=10 * 1024 * 1024):
            res = self.client.post(
                f"/api/jobs/{self.job.id}/apply/",
                {
                    "first_name": "Large",
                    "last_name": "Applicant",
                    "email": "upload_large@test.com",
                    "phone": "+63 917 123 4567",
                    "resume": oversized_pdf,
                },
                format="multipart"
            )
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("resume", res.data)
            self.assertIn("exceeds maximum limit", str(res.data["resume"]))


class SecurityRemediationFollowupRegressionTests(TestCase):
    """
    Exhaustive regression test suite verifying all 6 findings from
    TalentMatch_Security_Remediation_Followup.md:
    Finding A: Application creation & mass assignment defense.
    Finding B: Tracking token rate limiting & proxy trust.
    Finding C: Notification update column-level restrictions.
    Finding D: Private resume storage cache headers.
    Finding E: Provision user transaction & concurrency safety.
    Finding F: Irreversible RLS migrations.
    Section 8: UUID ownership mapping consistency.
    """

    def setUp(self):
        self.client = APIClient()
        self.job = Job.objects.create(
            title="Senior Security Engineer",
            department="Engineering",
            location="Remote",
            status=JobStatus.PUBLISHED
        )
        self.closed_job = Job.objects.create(
            title="Closed Role",
            department="Engineering",
            location="Remote",
            status=JobStatus.CLOSED
        )

        self.applicant_a_uuid = uuid.uuid4()
        self.applicant_b_uuid = uuid.uuid4()
        self.recruiter_uuid = uuid.uuid4()

        # Provision applicant A
        self.user_a, _ = User.objects.get_or_create(
            username=str(self.applicant_a_uuid),
            defaults={"email": "alice_followup@test.com"}
        )
        self.user_a.role = RoleChoices.APPLICANT
        self.user_a.supabase_id = str(self.applicant_a_uuid)
        self.user_a.is_staff = False
        self.user_a.save()
        self.role_a = UserRole.objects.create(
            user=self.user_a,
            supabase_user_id=self.applicant_a_uuid,
            email="alice_followup@test.com",
            role=RoleChoices.APPLICANT
        )

        # Provision applicant B
        self.user_b, _ = User.objects.get_or_create(
            username=str(self.applicant_b_uuid),
            defaults={"email": "bob_followup@test.com"}
        )
        self.user_b.role = RoleChoices.APPLICANT
        self.user_b.supabase_id = str(self.applicant_b_uuid)
        self.user_b.is_staff = False
        self.user_b.save()
        self.role_b = UserRole.objects.create(
            user=self.user_b,
            supabase_user_id=self.applicant_b_uuid,
            email="bob_followup@test.com",
            role=RoleChoices.APPLICANT
        )

        # Provision recruiter
        self.user_recruiter, _ = User.objects.get_or_create(
            username=str(self.recruiter_uuid),
            defaults={"email": "recruiter_followup@test.com"}
        )
        self.user_recruiter.role = RoleChoices.RECRUITER
        self.user_recruiter.supabase_id = str(self.recruiter_uuid)
        self.user_recruiter.is_staff = False
        self.user_recruiter.save()
        self.role_recruiter = UserRole.objects.create(
            user=self.user_recruiter,
            supabase_user_id=self.recruiter_uuid,
            email="recruiter_followup@test.com",
            role=RoleChoices.RECRUITER
        )

    # -------------------------------------------------------------------------
    # FINDING A: Application Creation and Mass Assignment
    # -------------------------------------------------------------------------

    def test_finding_a_generic_application_create_returns_405(self):
        """
        Finding A: Generic ApplicationViewSet create action (POST /api/applications/)
        is explicitly disabled and returns HTTP 405 Method Not Allowed.
        """
        self.client.force_authenticate(user=self.user_a)
        res = self.client.post("/api/applications/", {
            "first_name": "Alice",
            "last_name": "Test",
            "email": "alice_followup@test.com",
            "phone": "+63 917 123 4567",
        })
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertIn("not allowed", str(res.data.get("detail", "")).lower())

    def test_finding_a_applicant_cannot_mass_assign_ownership(self):
        """
        Finding A: Applicant A cannot set user_id to Applicant B. Ownership
        is strictly derived from the authenticated session identity.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_authenticate(user=self.user_a)

        resume_file = SimpleUploadedFile("alice_resume.pdf", b"%PDF-1.4 sample resume", content_type="application/pdf")
        res = self.client.post(
            f"/api/jobs/{self.job.id}/apply/",
            {
                "first_name": "Alice",
                "last_name": "Applicant",
                "email": "alice_apply@test.com",
                "phone": "+63 917 123 4567",
                "resume": resume_file,
                "user_id": str(self.applicant_b_uuid),  # Attempted spoof
            },
            format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        app_id = res.data["id"]
        persisted_app = Application.objects.get(id=app_id)
        self.assertEqual(persisted_app.user_id, self.applicant_a_uuid)
        self.assertNotEqual(persisted_app.user_id, self.applicant_b_uuid)

    def test_finding_a_applicant_cannot_mass_assign_status_or_protected_fields(self):
        """
        Finding A: Submitting status='HIRED', processing_status='COMPLETED', or notes
        during application submission is ignored or stripped.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_authenticate(user=self.user_a)

        resume_file = SimpleUploadedFile("alice_resume_2.pdf", b"%PDF-1.4 sample resume", content_type="application/pdf")
        res = self.client.post(
            f"/api/jobs/{self.job.id}/apply/",
            {
                "first_name": "Alice",
                "last_name": "Applicant",
                "email": "alice_status_tamper@test.com",
                "phone": "+63 917 123 4567",
                "resume": resume_file,
                "status": "HIRED",
                "processing_status": "COMPLETED",
                "notes": "Pre-approved candidate",
            },
            format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        persisted_app = Application.objects.get(id=res.data["id"])
        self.assertEqual(persisted_app.status, ApplicationStatus.SUBMITTED)
        self.assertEqual(persisted_app.notes, "")

    def test_finding_a_applicant_cannot_patch_protected_application_fields(self):
        """
        Finding A: An applicant cannot modify status, user_id, or job via PATCH /api/applications/{id}/.
        """
        app = Application.objects.create(
            job=self.job,
            first_name="Alice",
            last_name="Applicant",
            email="alice_patch@test.com",
            phone="+63 917 123 4567",
            status=ApplicationStatus.SUBMITTED,
            user_id=self.applicant_a_uuid,
        )
        self.client.force_authenticate(user=self.user_a)

        res = self.client.patch(f"/api/applications/{app.id}/", {
            "status": "HIRED",
            "user_id": str(self.applicant_b_uuid),
            "job": self.closed_job.id,
        })
        # Applicants cannot perform non-safe mutation on applications
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        app.refresh_from_db()
        self.assertEqual(app.status, ApplicationStatus.SUBMITTED)
        self.assertEqual(app.user_id, self.applicant_a_uuid)
        self.assertEqual(app.job_id, self.job.id)

    def test_finding_a_cannot_apply_to_closed_job(self):
        """
        Finding A: Applying to a closed job is rejected with HTTP 400.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_authenticate(user=self.user_a)

        resume_file = SimpleUploadedFile("closed_apply.pdf", b"%PDF-1.4 sample", content_type="application/pdf")
        res = self.client.post(
            f"/api/jobs/{self.closed_job.id}/apply/",
            {
                "first_name": "Alice",
                "last_name": "Applicant",
                "email": "alice_closed@test.com",
                "phone": "+63 917 123 4567",
                "resume": resume_file,
            },
            format="multipart"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("closed", str(res.data).lower())

    def test_finding_a_applicant_cannot_attach_scalar_resume_id(self):
        """
        Finding A: Submitting an integer or UUID string as resume fails validation.
        Applicant cannot link an existing resume.
        """
        self.client.force_authenticate(user=self.user_a)
        res = self.client.post(
            f"/api/jobs/{self.job.id}/apply/",
            {
                "first_name": "Alice",
                "last_name": "Applicant",
                "email": "alice_fake_resume@test.com",
                "phone": "+63 917 123 4567",
                "resume": "existing-resume-uuid-or-id",
            }
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("resume", res.data)

    # -------------------------------------------------------------------------
    # FINDING B: Tracking-Token Rate Limiting and Proxy Trust
    # -------------------------------------------------------------------------

    def test_finding_b_track_status_accepts_post_rejects_get_with_405(self):
        """
        Finding B: GET /api/applications/track-status/ returns 405.
        POST with valid token returns 200.
        """
        app = Application.objects.create(
            job=self.job,
            first_name="Alice",
            last_name="Applicant",
            email="alice_track@test.com",
            status=ApplicationStatus.SUBMITTED,
            user_id=self.applicant_a_uuid,
        )

        res_get = self.client.get("/api/applications/track-status/")
        self.assertEqual(res_get.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertIn("no-store", res_get.headers.get("Cache-Control", ""))

        res_post = self.client.post(
            "/api/applications/track-status/",
            {"token": str(app.tracking_token)},
            format="json"
        )
        self.assertEqual(res_post.status_code, status.HTTP_200_OK)
        self.assertEqual(res_post.data["candidate_code"], app.candidate_code)
        self.assertEqual(res_post.data["status"], ApplicationStatus.SUBMITTED)
        self.assertNotIn("email", res_post.data)
        self.assertNotIn("phone", res_post.data)
        self.assertNotIn("notes", res_post.data)
        self.assertIn("no-store", res_post.headers.get("Cache-Control", ""))

    def test_finding_b_spoofed_x_forwarded_for_cannot_bypass_throttling(self):
        """
        Finding B: An attacker connecting from an untrusted REMOTE_ADDR cannot
        fragment or bypass rate limits by supplying arbitrary X-Forwarded-For headers.
        """
        app = Application.objects.create(
            job=self.job,
            first_name="Alice",
            last_name="Applicant",
            email="alice_throttle_test@test.com",
            status=ApplicationStatus.SUBMITTED,
            user_id=self.applicant_a_uuid,
        )

        # Configure custom rate of 3/minute for predictable test
        with override_settings(
            TRUSTED_PROXY_IPS=['127.0.0.1'],
            REST_FRAMEWORK={
                'DEFAULT_THROTTLE_CLASSES': ['apps.applications.views.TrackingTokenRateThrottle'],
                'DEFAULT_THROTTLE_RATES': {'tracking_token': '3/minute'},
            }
        ):
            from django.core.cache import cache
            cache.clear()

            # Requests from untrusted external IP 198.51.100.5 with varying spoofed headers
            statuses = []
            for i in range(5):
                res = self.client.post(
                    "/api/applications/track-status/",
                    {"token": str(app.tracking_token)},
                    format="json",
                    REMOTE_ADDR="198.51.100.5",
                    HTTP_X_FORWARDED_FOR=f"10.0.0.{i}, 192.168.1.{i}",
                )
                statuses.append(res.status_code)

            self.assertIn(status.HTTP_429_TOO_MANY_REQUESTS, statuses)

    def test_finding_b_trusted_proxy_resolves_correct_client_ip(self):
        """
        Finding B: Secure right-to-left traversal extracts the real client IP
        when connecting through configured trusted proxies.
        """
        from apps.resumes.views import get_trusted_client_ip
        from django.test import RequestFactory

        rf = RequestFactory()

        with override_settings(TRUSTED_PROXY_IPS=['127.0.0.1', '10.0.0.1']):
            # Case 1: Untrusted remote address ignores X-Forwarded-For
            req1 = rf.get('/api/test/', REMOTE_ADDR='198.51.100.99', HTTP_X_FORWARDED_FOR='1.2.3.4')
            self.assertEqual(get_trusted_client_ip(req1), '198.51.100.99')

            # Case 2: Trusted proxy traverses right-to-left stopping at first untrusted
            req2 = rf.get('/api/test/', REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_FOR='203.0.113.195, 10.0.0.1')
            self.assertEqual(get_trusted_client_ip(req2), '203.0.113.195')

    # -------------------------------------------------------------------------
    # FINDING C: Notification RLS and Column-Level Restrictions
    # -------------------------------------------------------------------------

    def test_finding_c_applicant_can_mark_own_notification_read(self):
        """
        Finding C: An applicant can mark their own notification as read via /read/.
        """
        notif = Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.applicant_a_uuid,
            title="Interview Invitation",
            message="Please attend your interview tomorrow.",
            is_read=False
        )
        self.client.force_authenticate(user=self.user_a)

        res = self.client.patch(f"/api/notifications/{notif.id}/read/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_finding_c_applicant_cannot_modify_unauthorized_notification_fields(self):
        """
        Finding C: Attempting to update title, message, or other columns via PATCH
        is rejected with HTTP 400 Bad Request.
        """
        notif = Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.applicant_a_uuid,
            title="Legitimate Title",
            message="Legitimate Message",
            is_read=False
        )
        self.client.force_authenticate(user=self.user_a)

        res = self.client.patch(f"/api/notifications/{notif.id}/", {
            "title": "Hacked Title",
            "message": "Tampered Message",
            "is_read": True
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("is_read", str(res.data.get("detail", "")))
        notif.refresh_from_db()
        self.assertEqual(notif.title, "Legitimate Title")
        self.assertEqual(notif.message, "Legitimate Message")

    def test_finding_c_applicant_cannot_update_another_users_notification(self):
        """
        Finding C: Applicant A cannot mark Applicant B's notification as read.
        """
        notif_b = Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.applicant_b_uuid,
            title="Bob's Notification",
            message="Confidential to Bob",
            is_read=False
        )
        self.client.force_authenticate(user=self.user_a)

        res = self.client.patch(f"/api/notifications/{notif_b.id}/read/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        notif_b.refresh_from_db()
        self.assertFalse(notif_b.is_read)

    def test_finding_c_applicant_cannot_delete_or_create_notifications(self):
        """
        Finding C: Applicants cannot delete notifications (403) or create notifications (405).
        """
        notif = Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.applicant_a_uuid,
            title="Notice",
            message="Content",
            is_read=False
        )
        self.client.force_authenticate(user=self.user_a)

        del_res = self.client.delete(f"/api/notifications/{notif.id}/")
        self.assertEqual(del_res.status_code, status.HTTP_403_FORBIDDEN)

        create_res = self.client.post("/api/notifications/", {"title": "New Notice"})
        self.assertEqual(create_res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_finding_c_notification_put_returns_405(self):
        """
        Finding C: PUT /api/notifications/{id}/ returns HTTP 405 Method Not Allowed.
        """
        notif = Notification.objects.create(
            recipient_type=RecipientType.APPLICANT,
            user_id=self.applicant_a_uuid,
            title="Notice",
            message="Content",
            is_read=False
        )
        self.client.force_authenticate(user=self.user_a)
        res = self.client.put(f"/api/notifications/{notif.id}/", {"is_read": True})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    # -------------------------------------------------------------------------
    # FINDING D: Private Resume Object Cache Headers
    # -------------------------------------------------------------------------

    def test_finding_d_resume_storage_cache_control_is_private_no_store(self):
        """
        Finding D: CloudflareR2ResumeStorage object parameters configure
        'private, no-store, no-cache, must-revalidate'.
        """
        from config.storage_backends import CloudflareR2ResumeStorage
        cache_control = CloudflareR2ResumeStorage.object_parameters.get('CacheControl', '')
        self.assertIn('private', cache_control)
        self.assertIn('no-store', cache_control)
        self.assertNotIn('max-age=86400', cache_control)

    def test_finding_d_signed_url_expiration_defaults_to_300(self):
        """
        Finding D: Resume signed URL expiration defaults to 300 seconds.
        """
        from apps.resumes.views import get_signed_url_expiration
        self.assertEqual(get_signed_url_expiration(), 300)

    def test_finding_d_resume_download_responses_include_no_store_headers(self):
        """
        Finding D: Resume download endpoint (both JSON and redirect responses)
        sets Cache-Control: private, no-store.
        """
        app = Application.objects.create(
            job=self.job,
            first_name="Alice",
            last_name="Applicant",
            email="alice_dl@test.com",
            status=ApplicationStatus.SUBMITTED,
            user_id=self.applicant_a_uuid,
        )
        resume = Resume.objects.create(
            application=app,
            file="resumes/alice_dl.pdf",
            processing_status="COMPLETED"
        )
        self.client.force_authenticate(user=self.user_a)

        # JSON response
        res_json = self.client.get(f"/api/resumes/{resume.id}/download/?format=json")
        self.assertEqual(res_json.status_code, status.HTTP_200_OK)
        cc_json = res_json.headers.get("Cache-Control", "")
        self.assertIn("private", cc_json)
        self.assertIn("no-store", cc_json)

        # Redirect response
        res_redir = self.client.get(f"/api/resumes/{resume.id}/download/")
        self.assertIn(res_redir.status_code, (status.HTTP_302_FOUND, status.HTTP_301_MOVED_PERMANENTLY))
        cc_redir = res_redir.headers.get("Cache-Control", "")
        self.assertIn("private", cc_redir)
        self.assertIn("no-store", cc_redir)

    def test_finding_d_purged_resume_download_includes_no_store_headers(self):
        """
        Finding D: 410 Gone response on purged resume includes no-store headers.
        """
        app = Application.objects.create(
            job=self.job,
            first_name="Alice",
            last_name="Applicant",
            email="alice_purged@test.com",
            status=ApplicationStatus.SUBMITTED,
            user_id=self.applicant_a_uuid,
        )
        resume_purged = Resume.objects.create(
            application=app,
            file=None,
            processing_status="PURGED"
        )
        self.client.force_authenticate(user=self.user_a)

        res = self.client.get(f"/api/resumes/{resume_purged.id}/download/?format=json")
        self.assertEqual(res.status_code, status.HTTP_410_GONE)
        cc = res.headers.get("Cache-Control", "")
        self.assertIn("private", cc)
        self.assertIn("no-store", cc)

    # -------------------------------------------------------------------------
    # FINDING E: Provisioning Command Transaction and Concurrency Safety
    # -------------------------------------------------------------------------

    def test_finding_e_provision_applicant_succeeds(self):
        """
        Finding E: Normal provisioning with email and UUID succeeds atomically.
        """
        from django.core.management import call_command
        target_uuid = uuid.uuid4()
        call_command(
            'provision_user',
            '--supabase-id', str(target_uuid),
            '--email', 'prov_test@talentmatch.ai'
        )
        role = UserRole.objects.get(supabase_user_id=target_uuid)
        self.assertEqual(role.role, RoleChoices.APPLICANT)
        self.assertEqual(role.email, 'prov_test@talentmatch.ai')

    def test_finding_e_elevated_role_requires_explicit_intent_and_uuid(self):
        """
        Finding E: Elevated roles require explicit intent and cannot be assigned without --supabase-id.
        """
        from django.core.management import call_command
        from django.core.management.base import CommandError

        # Elevated role without UUID must fail
        with self.assertRaises(CommandError):
            call_command('provision_user', '--email', 'unbound_recruiter@test.com', '--role', 'recruiter')

        # Elevated role with explicit UUID and explicit --role succeeds
        target_uuid = uuid.uuid4()
        call_command(
            'provision_user',
            '--supabase-id', str(target_uuid),
            '--email', 'explicit_recruiter@test.com',
            '--role', 'recruiter'
        )
        role = UserRole.objects.get(supabase_user_id=target_uuid)
        self.assertEqual(role.role, RoleChoices.RECRUITER)

    def test_finding_e_provision_is_idempotent(self):
        """
        Finding E: Re-running provisioning with the same parameters is idempotent.
        """
        from django.core.management import call_command
        target_uuid = uuid.uuid4()
        call_command('provision_user', '--supabase-id', str(target_uuid), '--email', 'idempotent@test.com')
        call_command('provision_user', '--supabase-id', str(target_uuid), '--email', 'idempotent@test.com')
        count = UserRole.objects.filter(supabase_user_id=target_uuid).count()
        self.assertEqual(count, 1)

    def test_finding_e_conflicting_uuid_cannot_be_reassigned(self):
        """
        Finding E: A conflicting Supabase UUID cannot be reassigned to a different email.
        """
        from django.core.management import call_command
        from django.core.management.base import CommandError
        target_uuid = uuid.uuid4()
        call_command('provision_user', '--supabase-id', str(target_uuid), '--email', 'original@test.com')

        with self.assertRaises(CommandError):
            call_command('provision_user', '--supabase-id', str(target_uuid), '--email', 'imposter@test.com')

    def test_finding_e_supabase_user_never_granted_staff_or_superuser(self):
        """
        Finding E: Provisioned Supabase users are never granted Django is_staff or is_superuser.
        """
        from django.core.management import call_command
        target_uuid = uuid.uuid4()
        user = User.objects.create_user(username=str(target_uuid), email='nosudo@test.com', is_staff=True)

        call_command(
            'provision_user',
            '--supabase-id', str(target_uuid),
            '--email', 'nosudo@test.com',
            '--role', 'recruiter'
        )
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    # -------------------------------------------------------------------------
    # FINDING F: RLS Migrations Must Not Silently Reverse
    # -------------------------------------------------------------------------

    def test_finding_f_all_rls_migrations_raise_runtime_error_on_reverse(self):
        """
        Finding F: Reverse handlers for intentionally irreversible RLS migrations
        raise explicit RuntimeError explaining the security rationale.
        """
        import importlib

        migration_modules = [
            'apps.applications.migrations.0005_applications_rls',
            'apps.jobs.migrations.0004_archivedjob_rls',
            'apps.jobs.migrations.0005_jobs_rls',
            'apps.notifications.migrations.0002_notifications_rls',
            'apps.resumes.migrations.0005_resumes_rls',
            'apps.accounts.migrations.0002_harden_rls_trusted_roles',
            'apps.resumes.migrations.0006_recruiter_only_match_results_rls',
        ]

        for mod_name in migration_modules:
            mod = importlib.import_module(mod_name)
            # Find the reverse function used in operations
            reverse_func = None
            for op in mod.Migration.operations:
                if hasattr(op, 'reverse_code') and callable(op.reverse_code):
                    reverse_func = op.reverse_code
                    break

            self.assertIsNotNone(reverse_func, f"Expected callable reverse_code in {mod_name}")
            with self.assertRaises(RuntimeError, msg=f"Expected RuntimeError from {mod_name}.reverse_code"):
                reverse_func(None, None)

    def test_finding_f_no_rls_migration_uses_silent_pass(self):
        """
        Finding F: Static audit confirms no RLS migration uses 'pass' as reverse handler.
        """
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parent.parent.parent

        migration_paths = [
            backend_dir / 'apps' / 'applications' / 'migrations' / '0005_applications_rls.py',
            backend_dir / 'apps' / 'jobs' / 'migrations' / '0004_archivedjob_rls.py',
            backend_dir / 'apps' / 'jobs' / 'migrations' / '0005_jobs_rls.py',
            backend_dir / 'apps' / 'notifications' / 'migrations' / '0002_notifications_rls.py',
            backend_dir / 'apps' / 'resumes' / 'migrations' / '0005_resumes_rls.py',
        ]

        for path in migration_paths:
            content = path.read_text(encoding='utf-8')
            # Look for def disable_rls or reverse handler that contains only 'pass'
            func_match = re.search(r'def\s+(?:disable_rls|rollback_[a-zA-Z0-9_]+)\([^)]*\):(?:\s*#[^\n]*)*\s*pass\b', content)
            self.assertIsNone(func_match, f"Silent 'pass' reverse handler found in {path.name}")
            self.assertIn("raise RuntimeError", content, f"Missing explicit RuntimeError in {path.name}")

    # -------------------------------------------------------------------------
    # SECTION 8: UUID Ownership Mapping Verification
    # -------------------------------------------------------------------------

    def test_section_8_uuid_mapping_consistency(self):
        """
        Section 8: Verify that Application.user_id, Notification.user_id, and
        UserRole.supabase_user_id are all typed as UUIDField, matching Supabase auth.uid().
        """
        from django.db.models import UUIDField
        app_user_field = Application._meta.get_field('user_id')
        notif_user_field = Notification._meta.get_field('user_id')
        role_user_field = UserRole._meta.get_field('supabase_user_id')

        self.assertIsInstance(app_user_field, UUIDField)
        self.assertIsInstance(notif_user_field, UUIDField)
        self.assertIsInstance(role_user_field, UUIDField)
        self.assertTrue(role_user_field.unique)
        self.assertTrue(app_user_field.db_index)
        self.assertTrue(notif_user_field.db_index)


class FinalSecurityRemediationAndStagingReadinessTests(TestCase):
    """
    Regression test suite verifying all requirements from
    TalentMatch_Final_Security_Remediation_and_Staging_Readiness.md:
    - Finding A: Removal of excessive PostgreSQL privileges (no GRANT ALL, explicit REVOKE ALL).
    - Finding B: Removal of synthetic uuid5 Supabase ownership UUID fallback (fail-closed 403).
    - Finding C: Unconditional policy drop across all 7 protected tables before recreation.
    - Read-only SQL verification script (docs/security/verify_staging_rls.sql).
    - Shared rate-limiting readiness verification (REDIS_URL / EDGE_RATE_LIMIT_ENFORCED).
    """

    PROTECTED_TABLES = (
        'accounts_userrole',
        'resumes_resume',
        'resumes_matchresult',
        'jobs_job',
        'jobs_archivedjob',
        'applications_application',
        'notifications_notification',
    )

    def setUp(self):
        self.client = APIClient()
        self.job = Job.objects.create(
            title="Staff Security Architect",
            department="Security",
            location="Remote",
            status=JobStatus.PUBLISHED,
        )

    # -------------------------------------------------------------------------
    # FINDING A: Least-Privilege PostgreSQL Grants & Explicit REVOKE ALL
    # -------------------------------------------------------------------------

    def test_finding_a_no_migration_grants_all_to_anon_or_authenticated(self):
        """
        Finding A: Static scan across all migrations confirms zero occurrences
        of GRANT ALL to anon or authenticated.
        """
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parent.parent.parent
        migration_files = list((backend_dir / 'apps').glob('*/migrations/*.py'))
        self.assertGreater(len(migration_files), 0)

        for mig_file in migration_files:
            content = mig_file.read_text(encoding='utf-8')
            grant_all_match = re.search(
                r'GRANT\s+ALL(?:\s+PRIVILEGES)?\s+ON\s+(?:TABLE\s+)?[^\n;]+\s+TO\s+(?:anon|authenticated)\b',
                content,
                re.IGNORECASE,
            )
            self.assertIsNone(
                grant_all_match,
                f"Excessive 'GRANT ALL' to anon/authenticated found in {mig_file.name}: "
                f"{grant_all_match.group(0) if grant_all_match else ''}"
            )

    def test_finding_a_explicit_revoke_all_and_least_privilege_matrix(self):
        """
        Finding A: Verify accounts.0002 and resumes.0006 explicitly REVOKE ALL
        from anon and authenticated on all 7 protected tables, restrict
        authenticated to SELECT-only across all 7 protected tables (including
        jobs, archived jobs, and notifications), remove column-level update
        grants, and scope sequence revocations to the 7 TalentMatch sequences
        without revoking Django/postgres privileges.
        """
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parent.parent.parent
        mig_0002_raw = (
            backend_dir / 'apps' / 'accounts' / 'migrations' / '0002_harden_rls_trusted_roles.py'
        ).read_text(encoding='utf-8')
        mig_0006_raw = (
            backend_dir / 'apps' / 'resumes' / 'migrations' / '0006_recruiter_only_match_results_rls.py'
        ).read_text(encoding='utf-8')
        mig_notif_raw = (
            backend_dir / 'apps' / 'notifications' / 'migrations' / '0002_notifications_rls.py'
        ).read_text(encoding='utf-8')

        # Strip SQL line comments (-- ...) to avoid matching comments in static assertions
        mig_0002 = re.sub(r'--[^\n]*', '', mig_0002_raw)
        mig_0006 = re.sub(r'--[^\n]*', '', mig_0006_raw)
        mig_notif = re.sub(r'--[^\n]*', '', mig_notif_raw)

        combined_sql = mig_0002 + "\n" + mig_0006

        # 1. Every protected table must have REVOKE ALL FROM anon and REVOKE ALL FROM authenticated in accounts.0002
        for table in self.PROTECTED_TABLES:
            self.assertRegex(
                mig_0002,
                rf'REVOKE\s+ALL\s+ON\s+TABLE\s+public\.{table}\s+FROM\s+anon\s*;',
                f"Missing REVOKE ALL FROM anon for public.{table}",
            )
            self.assertRegex(
                mig_0002,
                rf'REVOKE\s+ALL\s+ON\s+TABLE\s+public\.{table}\s+FROM\s+authenticated\s*;',
                f"Missing REVOKE ALL FROM authenticated for public.{table}",
            )

        # 2. Sequence privileges must be revoked from both anon and authenticated,
        #    scoped to the 7 protected TalentMatch sequences (not ALL SEQUENCES IN SCHEMA public),
        #    and must never revoke from postgres or service_role.
        self.assertIsNone(
            re.search(
                r'REVOKE\s+ALL\s+ON\s+ALL\s+SEQUENCES\s+IN\s+SCHEMA\s+public',
                mig_0002,
                re.IGNORECASE,
            ),
            "Sequence revocation must be scoped to TalentMatch sequences rather than ALL SEQUENCES IN SCHEMA public",
        )
        self.assertRegex(
            mig_0002,
            r'REVOKE\s+ALL\s+ON\s+SEQUENCE\s+%%I\.%%I\s+FROM\s+anon',
        )
        self.assertRegex(
            mig_0002,
            r'REVOKE\s+ALL\s+ON\s+SEQUENCE\s+%%I\.%%I\s+FROM\s+authenticated',
        )
        for seq_name in (
            'accounts_userrole_id_seq',
            'applications_application_id_seq',
            'jobs_job_id_seq',
            'jobs_archivedjob_id_seq',
            'notifications_notification_id_seq',
            'resumes_resume_id_seq',
            'resumes_matchresult_id_seq',
        ):
            self.assertIn(
                f"'{seq_name}'",
                mig_0002,
                f"Expected scoped sequence '{seq_name}' in accounts.0002",
            )
        self.assertIsNone(
            re.search(
                r'REVOKE\s+[^\n;]+\s+FROM\s+(?:postgres|service_role)\b',
                combined_sql,
                re.IGNORECASE,
            ),
            "Must never revoke table or sequence privileges from postgres or service_role",
        )

        # 3. Neither anon nor authenticated may ever be granted TRUNCATE, REFERENCES, or TRIGGER
        for forbidden_priv in ('TRUNCATE', 'REFERENCES', 'TRIGGER'):
            forbidden_grant = re.search(
                rf'GRANT\s+[^\n;]*\b{forbidden_priv}\b[^\n;]*\bTO\s+(?:anon|authenticated)\b',
                combined_sql + "\n" + mig_notif,
                re.IGNORECASE,
            )
            self.assertIsNone(
                forbidden_grant,
                f"Forbidden privilege {forbidden_priv} granted in migrations",
            )

        # 4. anon must only receive SELECT on public.jobs_job and no other table grants
        anon_grants = re.findall(
            r'GRANT\s+([^\n;]+?)\s+ON\s+TABLE\s+public\.([a-z0-9_]+)\s+TO\s+anon\b',
            combined_sql,
            re.IGNORECASE,
        )
        self.assertEqual(
            anon_grants,
            [('SELECT', 'jobs_job')],
            f"Unexpected table grants to anon: {anon_grants}",
        )

        # 5. All 7 protected tables in accounts.0002 (and resumes.0006) must grant ONLY SELECT to authenticated
        for table in self.PROTECTED_TABLES:
            auth_grants = re.findall(
                rf'GRANT\s+([^\n;]+?)\s+ON\s+(?:TABLE\s+)?(?:public\.)?{table}\s+TO\s+authenticated\b',
                mig_0002,
                re.IGNORECASE,
            )
            self.assertEqual(
                [g.strip().upper() for g in auth_grants],
                ['SELECT'],
                f"Expected SELECT-only grant for authenticated on public.{table} in accounts.0002, got: {auth_grants}",
            )

        # Verify zero write or column-level update grants to authenticated exist anywhere in accounts.0002 or resumes.0006
        for write_priv in ('INSERT', 'UPDATE', 'DELETE'):
            write_grant_match = re.search(
                rf'GRANT\s+[^\n;]*\b{write_priv}\b[^\n;]*\bTO\s+(?:anon|authenticated)\b',
                combined_sql,
                re.IGNORECASE,
            )
            self.assertIsNone(
                write_grant_match,
                f"Unexpected {write_priv} grant to anon/authenticated in authoritative migrations: "
                f"{write_grant_match.group(0) if write_grant_match else ''}",
            )

        # 6. Applicant INSERT policies on applications_application and resumes_resume must NOT be created in accounts.0002
        self.assertIsNone(
            re.search(
                r'CREATE\s+POLICY\s+"Allow applicants to insert their own applications"',
                mig_0002,
                re.IGNORECASE,
            ),
            "Applicant INSERT policy on applications_application must not be created in accounts.0002",
        )
        self.assertIsNone(
            re.search(
                r'CREATE\s+POLICY\s+"Allow applicants to insert their own resume"',
                mig_0002,
                re.IGNORECASE,
            ),
            "Applicant INSERT policy on resumes_resume must not be created in accounts.0002",
        )

        # 7. Preserve public published-job read policy, applicant SELECT policies, and recruiter RLS policies
        for preserved_policy in (
            'Allow users to view own role profile',
            'Allow recruiters full access to applications',
            'Allow applicants to view their own applications',
            'Allow public read of published jobs',
            'Allow recruiters to manage jobs',
            'Allow recruiters to manage archived jobs',
            'Allow recruiters full access to notifications',
            'Allow applicants to view their own notifications',
            'Allow recruiters full access to resumes',
            'Allow applicants to view their own resume',
            'Allow recruiters full access to match results',
        ):
            self.assertRegex(
                mig_0002,
                rf'CREATE\s+POLICY\s+"{re.escape(preserved_policy)}"',
                f"Expected preserved policy '{preserved_policy}' in accounts.0002",
            )

    # -------------------------------------------------------------------------
    # FINDING B: Fail-Closed Supabase Ownership UUID Validation (No uuid5)
    # -------------------------------------------------------------------------

    def test_finding_b_no_synthetic_uuid5_fallback_in_codebase(self):
        """
        Finding B: Static audit confirms uuid5 and NAMESPACE_DNS are completely
        absent from apps/jobs/views.py and config/authentication.py.
        """
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parent.parent.parent
        jobs_views = (backend_dir / 'apps' / 'jobs' / 'views.py').read_text(encoding='utf-8')
        auth_file = (backend_dir / 'config' / 'authentication.py').read_text(encoding='utf-8')

        self.assertNotIn('uuid5', jobs_views)
        self.assertNotIn('NAMESPACE_DNS', jobs_views)
        self.assertNotIn('uuid5', auth_file)
        self.assertNotIn('NAMESPACE_DNS', auth_file)

    def test_finding_b_apply_with_missing_supabase_uuid_fails_closed_403(self):
        """
        Finding B: An authenticated user without a valid Supabase UUID (missing
        supabase_id and non-UUID username, no UserRole record) is rejected with
        HTTP 403 Forbidden before any Application or Resume is created.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        unbound_user = User.objects.create_user(
            username="legacy_local_applicant",
            email="unbound_applicant@test.com",
        )
        unbound_user.role = RoleChoices.APPLICANT
        unbound_user.supabase_id = None
        self.client.force_authenticate(user=unbound_user)

        app_count_before = Application.objects.count()
        resume_count_before = Resume.objects.count()

        resume_file = SimpleUploadedFile(
            "unbound_resume.pdf",
            b"%PDF-1.4 sample resume content",
            content_type="application/pdf",
        )
        res = self.client.post(
            f"/api/jobs/{self.job.id}/apply/",
            {
                "first_name": "Unbound",
                "last_name": "Applicant",
                "email": "unbound_applicant@test.com",
                "phone": "+63 917 000 1111",
                "resume": resume_file,
            },
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Application.objects.count(), app_count_before)
        self.assertEqual(Resume.objects.count(), resume_count_before)

    def test_finding_b_apply_with_malformed_supabase_uuid_fails_closed_403(self):
        """
        Finding B: An authenticated user with a malformed supabase_id string is
        rejected with HTTP 403 Forbidden before any Application or Resume is saved.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        malformed_user = User.objects.create_user(
            username="malformed-uuid-user",
            email="malformed_uuid@test.com",
        )
        malformed_user.role = RoleChoices.APPLICANT
        malformed_user.supabase_id = "not-a-valid-uuid-value"
        self.client.force_authenticate(user=malformed_user)

        app_count_before = Application.objects.count()
        resume_count_before = Resume.objects.count()

        resume_file = SimpleUploadedFile(
            "malformed_resume.pdf",
            b"%PDF-1.4 sample resume content",
            content_type="application/pdf",
        )
        res = self.client.post(
            f"/api/jobs/{self.job.id}/apply/",
            {
                "first_name": "Malformed",
                "last_name": "Identity",
                "email": "malformed_uuid@test.com",
                "phone": "+63 917 000 2222",
                "resume": resume_file,
            },
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Application.objects.count(), app_count_before)
        self.assertEqual(Resume.objects.count(), resume_count_before)

    def test_finding_b_apply_resolves_canonical_uuid_from_user_role_mapping(self):
        """
        Finding B: When UserRole.supabase_user_id is present, JobViewSet.apply
        resolves the canonical UUID from UserRole and binds Application.user_id.
        """
        from django.core.files.uploadedfile import SimpleUploadedFile

        canonical_uuid = uuid.uuid4()
        mapped_user = User.objects.create_user(
            username="mapped_applicant_handle",
            email="mapped_applicant@test.com",
        )
        mapped_user.role = RoleChoices.APPLICANT
        mapped_user.supabase_id = None  # Force lookup via UserRole.supabase_user_id
        UserRole.objects.create(
            user=mapped_user,
            supabase_user_id=canonical_uuid,
            email="mapped_applicant@test.com",
            role=RoleChoices.APPLICANT,
        )
        self.client.force_authenticate(user=mapped_user)

        resume_file = SimpleUploadedFile(
            "mapped_resume.pdf",
            b"%PDF-1.4 valid resume",
            content_type="application/pdf",
        )
        res = self.client.post(
            f"/api/jobs/{self.job.id}/apply/",
            {
                "first_name": "Mapped",
                "last_name": "Applicant",
                "email": "mapped_applicant@test.com",
                "phone": "+63 917 000 3333",
                "resume": resume_file,
            },
            format="multipart",
        )

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        created_app = Application.objects.get(id=res.data["id"])
        self.assertEqual(created_app.user_id, canonical_uuid)

    def test_finding_b_application_list_with_malformed_uuid_returns_empty_queryset(self):
        """
        Finding B: GET /api/applications/ for an applicant with a malformed
        supabase_id safely returns an empty list rather(than raising ValueError).
        """
        malformed_user = User.objects.create_user(
            username="non_uuid_applicant",
            email="non_uuid_list@test.com",
        )
        malformed_user.role = RoleChoices.APPLICANT
        malformed_user.supabase_id = "invalid-uuid-format"
        self.client.force_authenticate(user=malformed_user)

        res = self.client.get("/api/applications/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", res.data) if isinstance(res.data, dict) else res.data
        self.assertEqual(len(results), 0)

    # -------------------------------------------------------------------------
    # FINDING C: Complete Policy Inventory & Unconditional Policy Drop
    # -------------------------------------------------------------------------

    def test_finding_c_migrations_drop_all_existing_policies_on_all_protected_tables(self):
        """
        Finding C: accounts.0002 drops ALL existing policies on all 7 protected
        tables without filtering only on 'user_metadata', and resumes.0006 drops
        all existing policies on resumes_matchresult before recreating its policy.
        """
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parent.parent.parent
        mig_0002 = (
            backend_dir / 'apps' / 'accounts' / 'migrations' / '0002_harden_rls_trusted_roles.py'
        ).read_text(encoding='utf-8')
        mig_0006 = (
            backend_dir / 'apps' / 'resumes' / 'migrations' / '0006_recruiter_only_match_results_rls.py'
        ).read_text(encoding='utf-8')

        # Ensure accounts.0002 pg_policies query does NOT filter by user_metadata
        pg_policies_block_match = re.search(
            r'FROM\s+pg_policies\s+WHERE\s+(.*?)\s+LOOP',
            mig_0002,
            re.DOTALL | re.IGNORECASE,
        )
        self.assertIsNotNone(pg_policies_block_match, "Missing pg_policies loop in accounts.0002")
        where_clause = pg_policies_block_match.group(1)
        self.assertNotIn('user_metadata', where_clause)
        self.assertNotIn('raw_user_meta_data', where_clause)

        for table in self.PROTECTED_TABLES:
            self.assertIn(f"'{table}'", where_clause, f"Table {table} missing from pg_policies drop loop")

        self.assertIn(
            "EXECUTE format('DROP POLICY IF EXISTS %%I ON %%I.%%I', r.policyname, r.schemaname, r.tablename);",
            mig_0002,
        )
        self.assertIsNone(
            re.search(r'(?<!%)%I', mig_0002),
            "Unescaped single %I found in accounts.0002; must use %%I for Django schema_editor.execute compatibility",
        )

        # Ensure resumes.0006 also drops all policies on resumes_matchresult via pg_policies
        self.assertIn('FROM pg_policies', mig_0006)
        self.assertIn("tablename = 'resumes_matchresult'", mig_0006)
        self.assertIn(
            "EXECUTE format('DROP POLICY IF EXISTS %%I ON %%I.%%I', r.policyname, r.schemaname, r.tablename);",
            mig_0006,
        )
        self.assertIsNone(
            re.search(r'(?<!%)%I', mig_0006),
            "Unescaped single %I found in resumes.0006; must use %%I for Django schema_editor.execute compatibility",
        )

    # -------------------------------------------------------------------------
    # SQL VERIFICATION ARTIFACT: docs/security/verify_staging_rls.sql
    # -------------------------------------------------------------------------

    def test_verify_staging_rls_sql_is_strictly_read_only_and_complete(self):
        """
        Verify docs/security/verify_staging_rls.sql exists, checks all 7
        protected tables, helper function, privileges, and duplicate detection,
        and contains zero mutating SQL keywords.
        """
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parent.parent.parent
        sql_path = backend_dir / 'docs' / 'security' / 'verify_staging_rls.sql'
        self.assertTrue(sql_path.exists(), f"Missing verification script at {sql_path}")

        sql_content = sql_path.read_text(encoding='utf-8')
        self.assertGreater(len(sql_content.strip()), 100)

        # Must cover all 7 protected tables
        for table in self.PROTECTED_TABLES:
            self.assertIn(table, sql_content, f"Protected table {table} missing from verify_staging_rls.sql")

        # Must inspect core catalogs, effective table/column/sequence privileges across all 7 tables,
        # RLS policy checks, and duplicate application checks
        for required_token in (
            'relrowsecurity',
            'relforcerowsecurity',
            'pg_policies',
            'public_read_published_jobs_present',
            'role_table_grants',
            'has_table_privilege',
            'has_column_privilege',
            'authenticated_can_insert_applications',
            'authenticated_can_update_applications',
            'authenticated_can_delete_applications',
            'authenticated_can_insert_resumes',
            'authenticated_can_update_resumes',
            'authenticated_can_delete_resumes',
            'authenticated_can_insert_jobs',
            'authenticated_can_update_jobs',
            'authenticated_can_delete_jobs',
            'authenticated_can_insert_archivedjobs',
            'authenticated_can_update_archivedjobs',
            'authenticated_can_delete_archivedjobs',
            'authenticated_can_insert_notifications',
            'authenticated_can_update_notifications',
            'authenticated_can_delete_notifications',
            'authenticated_can_update_notification_is_read',
            'authenticated_can_insert_userrole',
            'authenticated_can_update_userrole',
            'authenticated_can_delete_userrole',
            'authenticated_can_insert_matchresult',
            'authenticated_can_update_matchresult',
            'authenticated_can_delete_matchresult',
            'postgres_bypassrls',
            'column_privileges',
            'has_sequence_privilege',
            'is_recruiter_or_superadmin',
            'HAVING COUNT(*) > 1',
        ):
            self.assertIn(required_token, sql_content, f"Required check '{required_token}' missing from SQL script")

        # Strip SQL comments and single-quoted string literals (e.g. privilege names in
        # has_table_privilege(..., 'INSERT')) before scanning for mutating SQL statements
        sql_without_comments = re.sub(r'--[^\n]*', '', sql_content)
        sql_without_comments = re.sub(r'/\*.*?\*/', '', sql_without_comments, flags=re.DOTALL)
        sql_code_only = re.sub(r"'[^']*'", "''", sql_without_comments)

        # Must NOT contain any mutating SQL keywords anywhere in executable SQL statements
        forbidden_keywords = (
            'GRANT',
            'REVOKE',
            'CREATE',
            'ALTER',
            'DROP',
            'UPDATE',
            'INSERT',
            'DELETE',
            'TRUNCATE',
        )
        for kw in forbidden_keywords:
            match = re.search(rf'\b{kw}\b', sql_code_only, re.IGNORECASE)
            self.assertIsNone(
                match,
                f"Mutating keyword '{kw}' found in read-only SQL script verify_staging_rls.sql",
            )

    # -------------------------------------------------------------------------
    # SECTION 5: Shared Rate Limiting Readiness Helper
    # -------------------------------------------------------------------------

    def test_section_5_distributed_throttling_readiness_helper(self):
        """
        Section 5: Verify is_distributed_throttling_ready accurately distinguishes
        worker-local LocMemCache from shared Redis cache or edge rate limiting.
        """
        from config.settings import is_distributed_throttling_ready

        locmem_caches = {
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'unique-snowflake',
            }
        }
        redis_caches = {
            'default': {
                'BACKEND': 'django.core.cache.backends.redis.RedisCache',
                'LOCATION': 'redis://127.0.0.1:6379/1',
            }
        }

        # LocMemCache without edge enforcement is NOT ready for multi-worker staging/prod
        self.assertFalse(
            is_distributed_throttling_ready(caches_config=locmem_caches, edge_enforced=False)
        )

        # LocMemCache with explicit Nginx/Cloudflare edge enforcement IS ready
        self.assertTrue(
            is_distributed_throttling_ready(caches_config=locmem_caches, edge_enforced=True)
        )

        # Shared Redis cache backend IS ready even without edge flag
        self.assertTrue(
            is_distributed_throttling_ready(caches_config=redis_caches, edge_enforced=False)
        )
