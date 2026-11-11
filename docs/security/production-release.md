# TalentMatch production release

Use synthetic accounts and resumes for acceptance testing. Release the matching frontend and API together.

## Database and service configuration

1. From a protected release environment, use the migration owner to run `python manage.py migrate`, then `python manage.py check_data_api_isolation`. Apply future migrations in this environment before releasing the service images.
2. Supply a random `TALENTMATCH_RUNTIME_DB_PASSWORD` to `python manage.py provision_runtime_login` once per database. Set `DATABASE_URL` on both services to the restricted `talentmatch_runtime` connection. Keep the migration owner credential in the release environment.
3. Set `SUPABASE_URL`, the exact API host allowlist, the frontend HTTPS CORS origin, and private R2 credentials in the service environment. With `REDIS_URL` unset, API workers share rate counters in PostgreSQL. Put the staff provisioning key and invitation callback on the web service. Use the Supabase JWT signing secret on the web service if the project still issues legacy HS256 tokens.
4. Configure the trusted edge to reject oversized application requests before forwarding the body. Allow at most the resume size limit plus multipart overhead. Verify TLS redirects and the proxy chain used for IP-based rate limits.
5. Deploy the web and resume worker services defined in `render.yaml`. The startup script checks deployment settings, pending migrations, and restricted database privileges. The worker refreshes ClamAV signatures and clears expired PostgreSQL rate counters. Test a clean upload and a scanner rejection, then confirm queue age and failed work are monitored.

## Supabase Auth and frontend

- Add the frontend’s exact `/reset-password` and `/auth/callback` URLs to the Supabase redirect allowlist. Signup confirmations exchange their PKCE code at `/auth/callback`; password recovery remains isolated at `/reset-password`. Open recovery mail in the browser that requested it. Test expired links, signup confirmation, and a successful reset with synthetic users.
- Configure Resend SMTP, email confirmation, and the project’s signup quotas using `docs/operations/resend-password-recovery.md`. Run `python manage.py check_signup_security --max-auth-emails-per-hour <limit> --require-smtp-host smtp.resend.com` with a release-only management token. Add `--apply` only for the reviewed quota. Verify the per-IP signup limit in Supabase Auth.
- Configure the frontend build with the intended HTTPS API URL, Supabase URL, and publishable key. Verify login, staff invitations, reports, CSP, and cross-company denials on the deployed frontend.

## Candidate data cutover

- Keep the selected R2 bucket private. Before real use, inventory and remove the agreed test records and their corresponding objects, then verify the storage deletion queue and the bucket's public-access settings.
- Gemini processing is off until `ENABLE_GEMINI_PROCESSING=True` is configured. Review the provider’s data-use and retention terms and publish the deployment privacy notice before enabling it. The local evaluation path remains available.
- Test a backup restore and direct structured security events to an access-controlled log destination before admitting real applicants.
