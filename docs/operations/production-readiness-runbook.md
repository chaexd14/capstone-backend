# Production readiness execution runbook

This runbook records the controls that require production-equivalent infrastructure. Use only synthetic accounts and resumes until every P0 row has evidence and approval.

## Automated release gates

- Frontend CI: locked install, lint, production-environment contract, production build, and desktop/mobile Playwright smoke tests.
- Backend CI: locked install, dependency consistency, migration drift, complete Django tests, and `check --deploy`.
- Protect `main` in both repositories and require every CI job before merge or deployment.

## Monitoring and alert checks

Run from a service with the same restricted database connection as the worker:

```bash
python manage.py check_resume_worker_health --max-heartbeat-age 60 --max-queue-age 300 --max-failed 0
```

Alert when the command exits non-zero. Also alert on:

| Signal | Initial threshold | Required response |
|---|---:|---|
| API readiness | 2 consecutive failures | Page operator and stop new traffic |
| HTTP 5xx rate | >1% for 5 minutes | Page operator; inspect request IDs |
| API p95 | >750 ms for 10 minutes | Investigate DB/provider saturation |
| Worker heartbeat | >60 seconds old | Restart/replace worker |
| Oldest ready work | >5 minutes | Scale or repair worker |
| Failed work | Any | Investigate and explicitly retry |
| ClamAV update/process failure | Any | Keep affected resumes unavailable |
| R2/DB authentication failure | Any | Rotate or repair credentials |

Send JSON application logs to an access-controlled destination. Redact authorization headers and never index resume text, names, email addresses, phone numbers, or provider prompts. Retain security-event logs according to the approved policy.

## Recovery drill

1. Record the source PostgreSQL backup timestamp and R2 backup/version timestamp.
2. Restore both into an isolated environment with separate credentials and no outbound email.
3. Point the API and worker at the restored database and bucket.
4. Run migrations only if the documented recovery procedure requires them.
5. Run `python manage.py verify_recovery_snapshot --application-id <synthetic-id>`.
6. Sign in as the owning recruiter and verify the restored resume download.
7. Sign in as a recruiter from another company and verify HTTP 404/403 denial.
8. Archive and restore the synthetic job, then run a retention preview.
9. Record recovery point, start/end time, achieved RPO/RTO, missing objects, and approver.

Do not mark recovery resolved until both database rows and private objects have been restored from the configured backup mechanism—not copied from the live runtime.

## Production-equivalent acceptance matrix

| Test | Expected result | Evidence required |
|---|---|---|
| Applicant signup and confirmation | Confirmed applicant can sign in | Build ID and screenshots/log IDs |
| Password recovery | Expired link fails; current link succeeds | Mail event and browser evidence |
| Recruiter invitation | Correct company assigned after acceptance | Security-event ID |
| Duplicate application | Second submission rejected | API response/request ID |
| Clean resume | Scan and evaluation complete | Worker logs and final status |
| EICAR/malicious resume | File unavailable and rejected | Scanner/worker event |
| Scanner unavailable | Resume remains unavailable and retryable | Queue evidence |
| Cross-company access | Recruiter cannot list/download another tenant's data | HTTP response/request ID |
| Public tracking throttle | Excess requests receive 429 | Response evidence |
| Archive and restore | Lifecycle transitions are authorized and audited | Security-event IDs |
| Retention preview/apply | Only approved records and objects are removed | Preview, approval, deletion evidence |
| AI provider unavailable | Deterministic path completes without personal-data leakage | Evaluation and sanitized logs |
| Backup restore | Application and object recover within RPO/RTO | Recovery report |

## Capacity smoke test

Use only a read-only staging endpoint:

```bash
python scripts/load_smoke.py https://staging-api.example/api/ready/ --requests 1000 --concurrency 25 --max-p95-ms 750 --max-error-rate 0.01
```

Run a separate supervised resume-processing test with synthetic files. Record API p50/p95/p99, error rate, database connections, CPU/memory, queue growth, queue recovery time, and provider quotas. Establish production limits from measured results rather than the starter-plan worker count.

## Privacy release gate

The production frontend build must define:

- `NEXT_PUBLIC_ORGANIZATION_NAME`
- `NEXT_PUBLIC_PRIVACY_CONTACT_EMAIL` (monitored mailbox)
- `NEXT_PUBLIC_PRIVACY_POLICY_VERSION`
- `NEXT_PUBLIC_DATA_RETENTION_DAYS`

Privacy/legal approval must identify the data controller, processors and regions, purposes, retention periods, data-subject verification procedure, AI provider behavior, complaint route, and response owner. Keep `ENABLE_GEMINI_PROCESSING=False` until approval is recorded.

## Evidence record

| Control | Date | Build/tag | Evidence link or artifact | Owner | Approver | Result |
|---|---|---|---|---|---|---|
| CI gates |  |  |  |  |  | Pending |
| Monitoring alerts |  |  |  |  |  | Pending |
| Recovery drill |  |  |  |  |  | Pending |
| Acceptance matrix |  |  |  |  |  | Pending |
| Capacity test |  |  |  |  |  | Pending |
| Privacy approval |  |  |  |  |  | Pending |
