from django.conf import settings
from django.db import connection
from django.utils.crypto import salted_hmac
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle


class SharedRateLimit:
    def allow_request(self, request, view):
        if not settings.POSTGRES_RATE_LIMITING:
            return super().allow_request(request, view)
        if self.rate is None:
            return True
        key = self.get_cache_key(request, view)
        if key is None:
            return True
        if self.num_requests <= 0:
            self.retry_after = self.duration
            return False

        # The shared row is incremented only if the current window still has room.
        bucket = salted_hmac('talentmatch.throttle', key, algorithm='sha256').hexdigest()
        with connection.cursor() as cursor:
            cursor.execute('''
                INSERT INTO public.accounts_throttlewindow AS bucket_row (bucket_key, hits, expires_at)
                VALUES (%s, 1, statement_timestamp() + %s * interval '1 second')
                ON CONFLICT (bucket_key) DO UPDATE SET
                    hits = CASE WHEN bucket_row.expires_at <= statement_timestamp() THEN 1 ELSE bucket_row.hits + 1 END,
                    expires_at = CASE WHEN bucket_row.expires_at <= statement_timestamp()
                                      THEN EXCLUDED.expires_at ELSE bucket_row.expires_at END
                WHERE bucket_row.expires_at <= statement_timestamp() OR bucket_row.hits < %s
                RETURNING 1
            ''', [bucket, self.duration, self.num_requests])
            if cursor.fetchone():
                return True
            cursor.execute('''
                SELECT greatest(1, ceil(extract(epoch FROM expires_at - statement_timestamp())))::integer
                FROM public.accounts_throttlewindow WHERE bucket_key = %s
            ''', [bucket])
            remaining = cursor.fetchone()
        self.retry_after = remaining[0] if remaining else 1
        return False

    def wait(self):
        if settings.POSTGRES_RATE_LIMITING:
            return self.retry_after
        return super().wait()


def purge_expired_throttle_windows():
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute('''
            DELETE FROM public.accounts_throttlewindow WHERE bucket_key IN (
                SELECT bucket_key FROM public.accounts_throttlewindow
                WHERE expires_at <= statement_timestamp()
                ORDER BY expires_at LIMIT 5000 FOR UPDATE SKIP LOCKED
            )
        ''')


class TrustedIPThrottle:
    def get_ident(self, request):
        from apps.resumes.views import get_trusted_client_ip

        return get_trusted_client_ip(request)


class TrustedAnonRateThrottle(TrustedIPThrottle, SharedRateLimit, AnonRateThrottle):
    pass


class TrustedUserRateThrottle(TrustedIPThrottle, SharedRateLimit, UserRateThrottle):
    pass


class SensitiveActionThrottle(TrustedIPThrottle, SharedRateLimit, ScopedRateThrottle):
    scopes = {
        ('JobViewSet', 'apply'): 'resume_apply',
        ('ResumeViewSet', 'download'): 'resume_download',
        ('ApplicationViewSet', 'reprocess'): 'ai_action',
        ('JobViewSet', 'ai_draft'): 'ai_action',
        ('JobViewSet', 'export_report'): 'recruiter_report',
        ('StaffManagementViewSet', 'create'): 'staff_creation',
        ('AcceptStaffInvitationView', None): 'staff_creation',
    }

    def allow_request(self, request, view):
        view.throttle_scope = self.scopes.get((type(view).__name__, getattr(view, 'action', None)))
        if settings.POSTGRES_RATE_LIMITING:
            self.scope = view.throttle_scope
            if not self.scope:
                return True
            self.rate = self.get_rate()
            self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)
