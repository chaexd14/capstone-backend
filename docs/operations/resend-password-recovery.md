# Resend password-recovery email

TalentMatch delegates password-reset token creation, expiry, one-time verification, and password updates to Supabase Auth. Resend is configured only as Supabase Auth's custom SMTP delivery provider. Django must not receive the Resend API key and must not issue separate password-reset tokens.

## Free-tier setup

1. Create a Resend account and add a sending domain or subdomain such as `auth.example.com`.
2. Add the SPF and DKIM records shown by Resend to the domain's DNS and wait for verification.
3. Create a Resend API key restricted to sending from the verified domain.
4. In Supabase Dashboard, open the target project and configure **Authentication → Email/SMTP**:

   | Setting | Value |
   |---|---|
   | Sender email | `no-reply@auth.example.com` |
   | Sender name | `TalentMatch` |
   | Host | `smtp.resend.com` |
   | Port | `465` |
   | Username | `resend` |
   | Password | Resend API key |

5. In Supabase URL configuration, set the production frontend as the Site URL and allow both the exact `/reset-password` recovery callback and `/auth/callback` signup-confirmation callback. Add localhost only for development.
6. In the Supabase reset-password template, keep the provider-generated recovery link and avoid promotional content or tracking links.

Never commit the Resend API key, place it in frontend variables, add it to Django logs, or store it in TalentMatch tables. Supabase stores and uses the SMTP credential.

## Release verification

Use a scoped, temporary Supabase Management API token in the release shell:

```powershell
$env:SUPABASE_ACCESS_TOKEN="<release-only-token>"
python manage.py check_signup_security `
  --max-auth-emails-per-hour 30 `
  --require-smtp-host smtp.resend.com
```

The command checks that email confirmation remains enabled, anonymous sign-in remains disabled, the Auth email ceiling is bounded, external email is enabled, and Resend's expected SMTP host, port, user, and sender address are configured. It never reads or prints the SMTP password.

## Acceptance test

Use a synthetic account addressed to a mailbox controlled by the team.

1. Create a synthetic applicant account and confirm that the Resend-delivered signup link opens `/auth/callback`, exchanges the one-time code, and returns to TalentMatch.
2. Request a reset from both applicant and recruiter login surfaces.
3. Confirm the UI always returns the same generic response, including for a nonexistent address.
4. Confirm Resend records delivered messages and that they use the verified From domain.
5. Open the newest reset link in the browser that requested it and set a password of at least 15 characters.
6. Confirm the recovery session is signed out and the new password works.
7. Confirm expired or previously consumed confirmation and recovery links fail safely.
8. Confirm repeated requests are rate-limited and do not reveal account existence.

Do not enable recruiter-to-candidate pipeline email from this setup. That feature requires a separate queued delivery model, explicit recruiter confirmation, bounce handling, and audit records.
