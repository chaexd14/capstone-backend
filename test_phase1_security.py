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
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')
from django.contrib.auth.models import User
from django.db import IntegrityError
from rest_framework.test import APIClient
from rest_framework import status

from apps.companies.models import Company
from apps.jobs.models import Job, JobStatus, ArchivedJob
from apps.applications.models import Application, ApplicationStatus
from apps.resumes.models import Resume, MatchResult, resume_upload_path
from apps.notifications.models import Notification, RecipientType
from config.storage_backends import CloudflareR2ResumeStorage


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

    # 13. Tracking Token Disablement: Anonymous application retrieval with tracking token rejected (401)
    client.force_authenticate(user=None)
    res = client.get(f"/api/applications/{app_a.id}/?tracking_token={token_a}", **sec_headers)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    test_results.append(("T-SEC-10: Anonymous application retrieval with tracking token rejected (401)", "PASSED"))

    # 14. Cross-Applicant Token Abuse: Authenticated Applicant A using Applicant B's token rejected
    client.force_authenticate(user=user_a)
    res = client.get(f"/api/applications/{app_b.id}/?tracking_token={token_b}", **sec_headers)
    assert res.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
    test_results.append(("T-SEC-11: Authenticated applicant cannot use another applicant's token (IDOR blocked)", "PASSED"))

    # 15. Tracking Token Disablement: Anonymous resume download with tracking token rejected (401)
    client.force_authenticate(user=None)
    res = client.get(f"/api/resumes/{resume_a.id}/download/?tracking_token={token_a}", **sec_headers)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    test_results.append(("T-SEC-12: Anonymous resume download with tracking token rejected (401)", "PASSED"))

    # 15b. Tracking Token Disablement: Anonymous resume metadata retrieval rejected (401)
    res = client.get(f"/api/resumes/{resume_a.id}/?tracking_token={token_a}", **sec_headers)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    test_results.append(("T-SEC-12b: Anonymous resume retrieve with tracking token rejected (401)", "PASSED"))

    # 15c. Cross-Applicant Token Abuse on Resume Download: Applicant A cannot download Applicant B's resume with B's token
    client.force_authenticate(user=user_a)
    res = client.get(f"/api/resumes/{resume_b.id}/download/?tracking_token={token_b}", **sec_headers)
    assert res.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
    test_results.append(("T-SEC-12c: Applicant cannot download other resume using other's token", "PASSED"))

    # 15d. Authorized Resume Download: Applicant A can download own resume
    res = client.get(f"/api/resumes/{resume_a.id}/download/", **sec_headers)
    assert res.status_code in (status.HTTP_302_FOUND, status.HTTP_200_OK)
    test_results.append(("T-SEC-12d: Authenticated applicant can download own resume", "PASSED"))

    # 15e. Tracking Token Disablement: Anonymous match details with tracking token rejected (401)
    client.force_authenticate(user=None)
    res = client.get(f"/api/applications/{app_a.id}/match/?tracking_token={token_a}", **sec_headers)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    test_results.append(("T-SEC-12e: Anonymous match details with tracking token rejected (401)", "PASSED"))

    # 15f. Header X-Tracking-Token cannot substitute for authentication
    res = client.get(f"/api/applications/{app_a.id}/", HTTP_X_TRACKING_TOKEN=str(token_a), **sec_headers)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    test_results.append(("T-SEC-12f: X-Tracking-Token header cannot substitute for authentication", "PASSED"))

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
    expected_iss = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1"
    valid_payload = {
        "sub": jwt_user_id,
        "email": "jwt_candidate@example.com",
        "aud": "authenticated",
        "iss": expected_iss,
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
        "iss": expected_iss,
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

    # ==========================================
    # PHASE 2: SERIALIZER & AI MATCH ISOLATION TESTS
    # ==========================================
    match_a = MatchResult.objects.create(
        application=app_a,
        match_score=88.5,
        skill_match_score=90.0,
        experience_match_score=85.0,
        education_match_score=95.0,
        semantic_match_score=87.0,
        preferred_skill_match_score=80.0,
        project_match_score=85.0,
        matched_skills=["Python", "Django", "PostgreSQL"],
        missing_skills=["Kubernetes"],
        ai_insights={"recommendation": "Strong candidate for backend audit role."},
        explanation="Candidate demonstrates extensive knowledge in DRF authorization architecture."
    )

    # 30. T-SER-01: Applicant retrieving own application does NOT receive match_result, notes, or presigned resume URL
    client.force_authenticate(user=user_a)
    res = client.get(f"/api/applications/{app_a.id}/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert "match_result" not in res.data, "Applicant response must strictly exclude match_result"
    assert "notes" not in res.data, "Applicant response must strictly exclude recruiter notes"
    assert "tracking_token" not in res.data, "Applicant response must strictly exclude tracking_token"
    resume_data = res.data.get("resume")
    assert resume_data is not None, "Applicant should receive basic resume metadata"
    assert "file_url" not in resume_data, "Applicant response must strictly exclude presigned file_url"
    assert "file" not in resume_data, "Applicant response must strictly exclude raw storage path"
    assert "extracted_text" not in resume_data, "Applicant response must strictly exclude extracted_text"
    assert "redacted_text" not in resume_data, "Applicant response must strictly exclude redacted_text"
    assert "original_filename" in resume_data
    assert "processing_status" in resume_data
    test_results.append(("T-SER-01: Applicant retrieving own application excludes match_result, notes, & file_url", "PASSED"))

    # 31. T-SER-02: Applicant accessing /match/ endpoint receives 403 Forbidden
    res = client.get(f"/api/applications/{app_a.id}/match/", **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN, f"Applicant should get 403 on match endpoint, got {res.status_code}"
    test_results.append(("T-SER-02: Applicant accessing /match/ action forbidden (403)", "PASSED"))

    # 32. T-SER-03: Applicant retrieving own resume does NOT receive file_url or extracted text
    res = client.get(f"/api/resumes/{resume_a.id}/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert "file_url" not in res.data, "Applicant resume response must exclude file_url"
    assert "file" not in res.data, "Applicant resume response must exclude file path"
    assert "extracted_text" not in res.data, "Applicant resume response must exclude extracted_text"
    assert "redacted_text" not in res.data, "Applicant resume response must exclude redacted_text"
    assert "original_filename" in res.data
    test_results.append(("T-SER-03: Applicant retrieving own resume excludes file_url & extracted text", "PASSED"))

    # 33. T-SER-04: Recruiter retrieving application receives match_result, notes, & recruiter resume
    client.force_authenticate(user=user_recruiter)
    res = client.get(f"/api/applications/{app_a.id}/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert "match_result" in res.data and res.data["match_result"] is not None
    assert res.data["match_result"]["match_score"] == 88.5
    assert "notes" in res.data
    assert "tracking_token" in res.data
    recruiter_resume = res.data.get("resume")
    assert recruiter_resume is not None
    assert "file_url" not in recruiter_resume, "Ordinary recruiter serializer must not generate signed URLs"
    assert "file" not in recruiter_resume, "Ordinary recruiter serializer must not expose storage paths"
    assert "extracted_text" in recruiter_resume
    test_results.append(("T-SER-04: Recruiter retrieving application receives match_result, notes, & extracted text (no file_url)", "PASSED"))

    # 34. T-SER-05: Recruiter accessing /match/ receives 200 OK with match score and details
    res = client.get(f"/api/applications/{app_a.id}/match/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert res.data["match_score"] == 88.5
    assert "matched_skills" in res.data
    assert "ai_insights" in res.data
    test_results.append(("T-SER-05: Recruiter accessing /match/ action receives 200 OK with full match details", "PASSED"))

    # 35. T-SER-06: Anonymous access to /match/ returns 401 Unauthorized
    client.force_authenticate(user=None)
    res = client.get(f"/api/applications/{app_a.id}/match/", **sec_headers)
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    test_results.append(("T-SER-06: Anonymous user accessing /match/ action rejected (401)", "PASSED"))

    # 36. T-SER-07: Recruiter inspecting job applicants receives full recruiter serializer
    client.force_authenticate(user=user_recruiter)
    res = client.get(f"/api/jobs/{job.id}/applicants/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    found_recruiter_app = False
    for item in res.data:
        if item["id"] == app_a.id:
            assert "match_result" in item
            assert "notes" in item
            found_recruiter_app = True
    assert found_recruiter_app, "app_a must be in job applicants response"
    test_results.append(("T-SER-07: Recruiter inspecting job applicants receives full recruiter serializer", "PASSED"))

    # ==========================================
    # PHASE 3: CLOUDFLARE R2 RESUME ACCESS TESTS
    # ==========================================
    # 37. T-R2-01: Storage expiration configured to 300 seconds
    r2_storage = CloudflareR2ResumeStorage()
    assert r2_storage.querystring_expire == 300, f"Expected 300s TTL, got {r2_storage.querystring_expire}"
    test_results.append(("T-R2-01: Cloudflare R2 storage querystring_expire set to 300 seconds", "PASSED"))

    # 38. T-R2-02: Opaque object keys generated (no tracking token, email, or candidate name)
    generated_path = resume_upload_path(resume_a, "sensitive_candidate_resume_v1.pdf")
    assert generated_path.startswith("resumes/"), f"Expected resumes/ prefix, got {generated_path}"
    assert generated_path.endswith(".pdf"), f"Expected .pdf extension, got {generated_path}"
    assert "sensitive" not in generated_path
    assert "candidate" not in generated_path
    assert str(token_a) not in generated_path
    assert len(generated_path.replace("resumes/", "").replace(".pdf", "")) == 32  # uuid4 hex length
    test_results.append(("T-R2-02: Resume upload path generates opaque UUID key with no PII or token", "PASSED"))

    # 39. T-R2-03: Recruiter JSON download request (?redirect=false) returns 200 OK with download_url and 300s TTL
    client.force_authenticate(user=user_recruiter)
    res = client.get(f"/api/resumes/{resume_a.id}/download/?redirect=false", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert "download_url" in res.data and res.data["download_url"] is not None
    assert res.data["expires_in"] == 300
    test_results.append(("T-R2-03: Recruiter JSON download request returns 200 OK with download_url (300s TTL)", "PASSED"))

    # 40. T-R2-04: Applicant JSON download request (?redirect=false) for own resume returns 200 OK
    client.force_authenticate(user=user_a)
    res = client.get(f"/api/resumes/{resume_a.id}/download/?redirect=false", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert "download_url" in res.data and res.data["download_url"] is not None
    assert res.data["expires_in"] == 300
    test_results.append(("T-R2-04: Applicant JSON download request for own resume returns 200 OK (300s TTL)", "PASSED"))

    # 41. T-R2-05: Applicant JSON download request for another applicant's resume is blocked (404/403)
    res = client.get(f"/api/resumes/{resume_b.id}/download/?redirect=false", **sec_headers)
    assert res.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN)
    test_results.append(("T-R2-05: Applicant cross-download attempt blocked (404/403)", "PASSED"))

    # 42. T-R2-06: Purged resume download returns HTTP 410 Gone
    resume_a.file = None
    resume_a.processing_status = "PURGED"
    resume_a.save(update_fields=['file', 'processing_status'])
    client.force_authenticate(user=user_recruiter)
    res = client.get(f"/api/resumes/{resume_a.id}/download/", **sec_headers)
    assert res.status_code == status.HTTP_410_GONE
    test_results.append(("T-R2-06: Purged resume download returns 410 Gone", "PASSED"))

    # ==========================================
    # PHASE 4: DATA INTEGRITY & LIFECYCLE TESTS
    # ==========================================
    # 43. T-INT-01: Database UniqueConstraint blocks duplicate (job, user_id)
    try:
        Application.objects.create(
            job=job,
            first_name="Bob Duplicate",
            last_name="Applicant",
            email=f"bob_diff_{uuid.uuid4().hex[:6]}@test.com",
            phone="+639123456789",
            user_id=applicant_b_uuid,  # same user_id as app_b
            status=ApplicationStatus.SUBMITTED
        )
        assert False, "Expected IntegrityError on duplicate (job, user_id)"
    except IntegrityError:
        test_results.append(("T-INT-01: Database UniqueConstraint blocks duplicate (job, user_id)", "PASSED"))

    # 44. T-INT-02: Partial UniqueConstraint allows nullable user_id without collision
    anon_app_1 = Application.objects.create(
        job=job,
        first_name="Anonymous 1",
        last_name="Applicant",
        email=f"anon1_{uuid.uuid4().hex[:6]}@test.com",
        user_id=None,
        status=ApplicationStatus.SUBMITTED
    )
    anon_app_2 = Application.objects.create(
        job=job,
        first_name="Anonymous 2",
        last_name="Applicant",
        email=f"anon2_{uuid.uuid4().hex[:6]}@test.com",
        user_id=None,
        status=ApplicationStatus.SUBMITTED
    )
    assert anon_app_1.id != anon_app_2.id
    test_results.append(("T-INT-02: Partial UniqueConstraint permits nullable user_id records", "PASSED"))
    anon_app_1.delete()
    anon_app_2.delete()

    # 45. T-INT-03: Job closure purges physical resume files while preserving candidate dossier in DB
    job_lifecycle = Job.objects.create(
        title="Lifecycle Verification Job",
        company=company,
        status=JobStatus.PUBLISHED
    )
    app_life = Application.objects.create(
        job=job_lifecycle,
        first_name="Life",
        last_name="Cycle",
        email=f"life_{uuid.uuid4().hex[:6]}@test.com",
        user_id=str(uuid.uuid4()),
        status=ApplicationStatus.SUBMITTED
    )
    resume_life = Resume.objects.create(
        application=app_life,
        file="resumes/life_test.pdf",
        processing_status="COMPLETED"
    )
    match_life = MatchResult.objects.create(
        application=app_life,
        match_score=92.0
    )

    client.force_authenticate(user=user_recruiter)
    res = client.post(f"/api/jobs/{job_lifecycle.id}/close/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    job_lifecycle.refresh_from_db()
    assert job_lifecycle.status == JobStatus.CLOSED
    resume_life.refresh_from_db()
    assert resume_life.processing_status == "PURGED"
    assert not bool(resume_life.file)
    # Verify Application and MatchResult still exist in DB
    assert Application.objects.filter(id=app_life.id).exists()
    assert MatchResult.objects.filter(id=match_life.id).exists()
    test_results.append(("T-INT-03: Job closure purges R2 resumes while preserving candidate records", "PASSED"))

    # 46. T-INT-04: Job archival deletes active job & applications, creates ArchivedJob
    res = client.post(f"/api/jobs/{job_lifecycle.id}/archive/", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert not Job.objects.filter(id=job_lifecycle.id).exists()
    assert not Application.objects.filter(id=app_life.id).exists()
    archived_record = ArchivedJob.objects.filter(title="Lifecycle Verification Job").first()
    assert archived_record is not None
    test_results.append(("T-INT-04: Job archival creates ArchivedJob and permanently deletes applications", "PASSED"))
    archived_record.delete()

    # ==========================================
    # PHASE 5: RECRUITER AUTHORIZATION INTEGRITY
    # ==========================================
    # 47. T-ROLE-11: Tampered JWT with user_metadata={'role': 'recruiter'} cannot elevate privileges
    jwt_secret = "test-supabase-jwt-secret-for-verification-only-2026"
    old_secret = getattr(settings, 'SUPABASE_JWT_SECRET', '')
    settings.SUPABASE_JWT_SECRET = jwt_secret

    client.force_authenticate(user=None)
    malicious_user_id = str(uuid.uuid4())
    expected_iss = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1"
    tampered_role_payload = {
        "sub": malicious_user_id,
        "email": "attacker@example.com",
        "aud": "authenticated",
        "iss": expected_iss,
        "exp": int(time.time()) + 3600,
        "user_metadata": {
            "role": "recruiter",
            "is_staff": True,
            "is_superuser": True
        },
        "app_metadata": {
            "role": "recruiter"
        }
    }
    tampered_jwt = jwt.encode(tampered_role_payload, jwt_secret, algorithm="HS256")

    # Attacker tries to list all applications
    res = client.get("/api/applications/", HTTP_AUTHORIZATION=f"Bearer {tampered_jwt}", **sec_headers)
    assert res.status_code == status.HTTP_200_OK
    assert len(res.data) == 0, "Attacker must only see own applications (0), not database-wide records"

    # Attacker tries to invoke recruiter-only endpoint
    res = client.post("/api/jobs/ai-draft/", {"title": "Privilege Escalation"}, HTTP_AUTHORIZATION=f"Bearer {tampered_jwt}", **sec_headers)
    assert res.status_code == status.HTTP_403_FORBIDDEN, "Attacker with spoofed metadata must be denied recruiter endpoints (403)"

    # Verify user was provisioned as applicant in DB, not recruiter
    attacker_user = User.objects.get(username=malicious_user_id)
    assert attacker_user.is_staff is False
    assert attacker_user.is_superuser is False
    assert attacker_user.role_profile.role == "applicant"

    settings.SUPABASE_JWT_SECRET = old_secret
    test_results.append(("T-ROLE-11: Spoofed user_metadata in JWT cannot elevate privileges (stays applicant)", "PASSED"))

    # Cleanup attacker user
    attacker_user.delete()

    # Cleanup test entities
    match_a.delete()
    resume_a.delete()
    resume_b.delete()
    app_a.delete()
    app_b.delete()

    print("\nSUMMARY OF RESULTS:")
    print("-" * 70)
    for test_name, result in test_results:
        print(f"[{result}] {test_name}")
    print("-" * 70)
    print(f"ALL {len(test_results)} SECURITY & AUTHORIZATION TESTS PASSED!")


if __name__ == "__main__":
    run_phase1_security_tests()
