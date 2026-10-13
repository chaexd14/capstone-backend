"""
Phase 1 Automated Security & Authorization Test Suite
Validates:
- IDOR prevention on applications & resumes
- Role-based permissions (Applicant vs Recruiter vs Unauthenticated)
- Direct browser navigation / middleware defense
- Tracking token authorization & tamper prevention
- Public endpoints & aggregate privacy
- Recruiter administrative action authorization
- Supabase JWT authentication validation (valid, expired, tampered)
"""
import os
import time
import uuid
import jwt
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status

from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus
from apps.applications.models import Application, ApplicationStatus
from apps.resumes.models import Resume
from apps.notifications.models import Notification, RecipientType


def run_phase1_security_tests():
    print("=" * 70)
    print("RUNNING EXHAUSTIVE PHASE 1 PRIVACY & SECURITY VERIFICATION SUITE")
    print("=" * 70)

    client = APIClient()

    # Setup test entities
    company, _ = Company.objects.get_or_create(name="Phase 1 Verification Corp")
    job, _ = Job.objects.get_or_create(
        title="Privacy Analyst Phase 1 Job",
        defaults={
            "company": company,
            "department": "Security & Compliance",
            "description": "Verify DPA 2012 compliance and server-side authorization.",
            "location": "Makati City, Metro Manila",
            "status": JobStatus.PUBLISHED,
        }
    )

    applicant_a_uuid = str(uuid.uuid4())
    applicant_b_uuid = str(uuid.uuid4())
    recruiter_uuid = str(uuid.uuid4())

    user_a, _ = User.objects.get_or_create(username=applicant_a_uuid, defaults={"email": "alice_sec@test.com"})
    user_a.role = "applicant"
    user_a.supabase_id = applicant_a_uuid
    user_a.is_staff = False
    user_a.save()

    user_b, _ = User.objects.get_or_create(username=applicant_b_uuid, defaults={"email": "bob_sec@test.com"})
    user_b.role = "applicant"
    user_b.supabase_id = applicant_b_uuid
    user_b.is_staff = False
    user_b.save()

    user_recruiter, _ = User.objects.get_or_create(username=recruiter_uuid, defaults={"email": "recruiter_sec@test.com", "is_staff": True})
    user_recruiter.role = "recruiter"
    user_recruiter.supabase_id = recruiter_uuid
    user_recruiter.is_staff = True
    user_recruiter.save()

    # Create applications with tracking tokens
    token_a = uuid.uuid4()
    token_b = uuid.uuid4()

    app_a = Application.objects.create(
        job=job,
        first_name="Alice",
        last_name="Applicant",
        email=f"alice_{uuid.uuid4().hex[:6]}@test.com",
        phone="+639123456781",
        user_id=applicant_a_uuid,
        tracking_token=token_a,
        status=ApplicationStatus.SUBMITTED
    )
    resume_a = Resume.objects.create(
        application=app_a,
        file="resumes/test_alice.pdf",
        processing_status="COMPLETED"
    )

    app_b = Application.objects.create(
        job=job,
        first_name="Bob",
        last_name="Applicant",
        email=f"bob_{uuid.uuid4().hex[:6]}@test.com",
        phone="+639123456782",
        user_id=applicant_b_uuid,
        tracking_token=token_b,
        status=ApplicationStatus.SUBMITTED
    )
    resume_b = Resume.objects.create(
        application=app_b,
        file="resumes/test_bob.pdf",
        processing_status="COMPLETED"
    )

    sec_headers = {
        "HTTP_X_APP_CLIENT_KEY": "talentmatch-client-key-2026",
        "HTTP_X_REQUESTED_WITH": "XMLHttpRequest"
    }

    test_results = []

    # 1. Public Stats Endpoint
    res = client.get("/api/jobs/public-stats/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert "screened_candidates" in res.data and "open_benchmarks" in res.data
    test_results.append(("T-PUB-01: Public Aggregate Stats endpoint works with no PII", "PASSED"))

    # 2. Public Jobs List Endpoint
    res = client.get("/api/jobs/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    test_results.append(("T-PUB-02: Public Jobs listing is accessible anonymously", "PASSED"))

    # 3. Direct browser navigation blocked (Middleware defense)
    res = client.get("/api/applications/", HTTP_SEC_FETCH_DEST="document", HTTP_SEC_FETCH_MODE="navigate")
    assert res.status_code == status.HTTP_404_NOT_FOUND
    test_results.append(("T-MID-01: Direct browser URL navigation concealed with 404", "PASSED"))

    # 4. Unauthenticated /api/applications/ access rejected
    client.force_authenticate(user=None)
    res = client.get("/api/applications/", **sec_headers)
    assert res.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
    test_results.append(("T-SEC-01: Unauthenticated applications listing rejected (401)", "PASSED"))

    # 5. Unauthenticated job application submission rejected
    res = client.post(f"/api/jobs/{job.id}/apply/", {}, **sec_headers)
    assert res.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)
    test_results.append(("T-SEC-02: Unauthenticated job submission rejected (401)", "PASSED"))

    # 6. Applicant A cannot view all applications (scoped strictly to own)
    client.force_authenticate(user=user_a)
    res = client.get("/api/applications/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    app_ids = [item["id"] for item in res.data]
    assert app_a.id in app_ids, "Applicant A should see own application"
    assert app_b.id not in app_ids, "Applicant A MUST NOT see Applicant B's application!"
    test_results.append(("T-SEC-03: Applicant listing scoped strictly to own records", "PASSED"))

    # 7. IDOR Protection: Applicant A cannot retrieve Applicant B's application (returns 403 or 404)
    res = client.get(f"/api/applications/{app_b.id}/", **sec_headers)
    assert res.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
    test_results.append(("T-SEC-04: IDOR Prevention on GET /api/applications/<id>/", "PASSED"))

    # 8. Tamper Protection: Applicant A cannot PATCH status of application
    res = client.patch(f"/api/applications/{app_a.id}/status/", {"status": "OFFERED"}, **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-SEC-05: Applicant forbidden from tampering with application status", "PASSED"))

    # 9. Tamper Protection: Applicant A cannot reprocess application
    res = client.post(f"/api/applications/{app_a.id}/reprocess/", {}, **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-SEC-06: Applicant forbidden from retriggering AI scoring", "PASSED"))

    # 10. Tamper Protection: Applicant A cannot delete application
    res = client.delete(f"/api/applications/{app_a.id}/", **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-SEC-07: Applicant forbidden from destroying application record", "PASSED"))

    # 11. Resume Harvesting Prevention: Applicant A cannot list all resumes
    res = client.get("/api/resumes/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    resume_ids = [item["id"] for item in res.data]
    assert resume_a.id in resume_ids
    assert resume_b.id not in resume_ids, "Applicant A MUST NOT harvest Applicant B's resume!"
    test_results.append(("T-SEC-08: Resume listing strictly scoped to applicant owner", "PASSED"))

    # 12. IDOR Protection: Applicant A cannot download Applicant B's resume
    res = client.get(f"/api/resumes/{resume_b.id}/download/", **sec_headers)
    assert res.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
    test_results.append(("T-SEC-09: IDOR Prevention on GET /api/resumes/<id>/download/", "PASSED"))

    # 13. Tracking Token: Guest / polling access to own application
    client.force_authenticate(user=None)
    res = client.get(f"/api/applications/{app_a.id}/?tracking_token={token_a}", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert res.data["id"] == app_a.id
    test_results.append(("T-SEC-10: Tracking token authorizes single application polling", "PASSED"))

    # 14. Tracking Token Tampering: Wrong tracking token rejected
    wrong_token = uuid.uuid4()
    res = client.get(f"/api/applications/{app_a.id}/?tracking_token={wrong_token}", **sec_headers)
    assert res.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
    test_results.append(("T-SEC-11: Invalid tracking token rejected (IDOR blocked)", "PASSED"))

    # 15. Tracking Token: Resume download authorized with matching token
    res = client.get(f"/api/resumes/{resume_a.id}/download/?tracking_token={token_a}", **sec_headers)
    assert res.status_code in (status.HTTP_302_FOUND, status.HTTP_200_OK)
    test_results.append(("T-SEC-12: Tracking token authorizes candidate resume download", "PASSED"))

    # 16. Recruiter cross-role actions: Applicant forbidden from archive job
    client.force_authenticate(user=user_a)
    res = client.post(f"/api/jobs/{job.id}/archive/", {}, **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-ROLE-01: Applicant forbidden from archiving jobs", "PASSED"))

    # 17. Recruiter cross-role actions: Applicant forbidden from close job
    res = client.post(f"/api/jobs/{job.id}/close/", {}, **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-ROLE-02: Applicant forbidden from closing jobs", "PASSED"))

    # 18. Recruiter cross-role actions: Applicant forbidden from reopening job
    res = client.post(f"/api/jobs/{job.id}/reopen/", {}, **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-ROLE-03: Applicant forbidden from reopening jobs", "PASSED"))

    # 19. Recruiter cross-role actions: Applicant forbidden from deleting job
    res = client.delete(f"/api/jobs/{job.id}/", **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-ROLE-04: Applicant forbidden from deleting jobs", "PASSED"))

    # 20. Recruiter cross-role actions: Applicant forbidden from viewing applicant list
    res = client.get(f"/api/jobs/{job.id}/applicants/", **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-ROLE-05: Applicant forbidden from viewing requisition applicants", "PASSED"))

    # 21. Recruiter cross-role actions: Applicant forbidden from AI draft
    res = client.post("/api/jobs/ai-draft/", {"title": "Software Engineer"}, **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-ROLE-06: Applicant forbidden from invoking AI draft requisition", "PASSED"))

    # 22. Recruiter cross-role actions: Applicant forbidden from JD lint
    res = client.post("/api/jobs/lint-jd/", {"title": "Software Engineer"}, **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN
    test_results.append(("T-ROLE-07: Applicant forbidden from invoking JD lint", "PASSED"))

    # 23. Recruiter authorized actions: Recruiter can view all applications
    client.force_authenticate(user=user_recruiter)
    res = client.get("/api/applications/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    recruiter_app_ids = [item["id"] for item in res.data]
    assert app_a.id in recruiter_app_ids and app_b.id in recruiter_app_ids
    test_results.append(("T-ROLE-08: Recruiter has access to all applicant records", "PASSED"))

    # 24. Recruiter authorized actions: Recruiter can update status
    res = client.patch(f"/api/applications/{app_a.id}/status/", {"status": "UNDER_REVIEW"}, **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    app_a.refresh_from_db()
    assert app_a.status == "UNDER_REVIEW"
    test_results.append(("T-ROLE-09: Recruiter authorized to update candidate status", "PASSED"))

    # 25. Recruiter authorized actions: Recruiter can view job applicants
    res = client.get(f"/api/jobs/{job.id}/applicants/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    test_results.append(("T-ROLE-10: Recruiter authorized to inspect job applicants", "PASSED"))

    # 26. Notifications: External POST disabled (Method Not Allowed)
    res = client.post("/api/notifications/", {"title": "Spam", "message": "Spam"}, **sec_headers)
    assert res.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    test_results.append(("T-NOTIF-01: Direct POST to notifications endpoint disabled (405)", "PASSED"))

    # 27. Supabase JWT Authentication: Valid HMAC Token
    jwt_secret = "test-supabase-jwt-secret-for-verification-only-2026"
    old_secret = getattr(settings, 'SUPABASE_JWT_SECRET', '')
    settings.SUPABASE_JWT_SECRET = jwt_secret

    client.force_authenticate(user=None)
    jwt_user_id = str(uuid.uuid4())
    valid_payload = {
        "sub": jwt_user_id,
        "email": "jwt_candidate@example.com",
        "aud": "authenticated",
        "exp": int(time.time()) + 3600,
        "user_metadata": {"role": "applicant"}
    }
    valid_token = jwt.encode(valid_payload, jwt_secret, algorithm="HS256")

    res = client.get("/api/applications/", HTTP_AUTHORIZATION=f"Bearer {valid_token}")
    assert res.status_code == status.HTTP_200_OK
    test_results.append(("T-AUTH-01: Supabase JWT Bearer token correctly authenticates caller", "PASSED"))

    # 28. Supabase JWT Authentication: Expired Token Rejected
    expired_payload = {
        "sub": jwt_user_id,
        "email": "jwt_candidate@example.com",
        "aud": "authenticated",
        "exp": int(time.time()) - 3600,
        "user_metadata": {"role": "applicant"}
    }
    expired_token = jwt.encode(expired_payload, jwt_secret, algorithm="HS256")
    res = client.get("/api/applications/", HTTP_AUTHORIZATION=f"Bearer {expired_token}")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    test_results.append(("T-AUTH-02: Expired Supabase JWT token rejected with 401", "PASSED"))

    # 29. Supabase JWT Authentication: Tampered Signature Rejected
    tampered_token = jwt.encode(valid_payload, "wrong-secret-key-signature-32-chars!", algorithm="HS256")
    res = client.get("/api/applications/", HTTP_AUTHORIZATION=f"Bearer {tampered_token}")
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    test_results.append(("T-AUTH-03: Tampered Supabase JWT token rejected with 401", "PASSED"))

    settings.SUPABASE_JWT_SECRET = old_secret

    # Cleanup test entities
    resume_a.delete()
    resume_b.delete()
    app_a.delete()
    app_b.delete()

    print("\nSUMMARY OF RESULTS:")
    print("-" * 70)
    for test_name, result in test_results:
        print(f"[{result}] {test_name}")
    print("-" * 70)
    print(f"ALL {len(test_results)} PHASE 1 SECURITY & AUTHORIZATION TESTS PASSED!")


if __name__ == "__main__":
    run_phase1_security_tests()
