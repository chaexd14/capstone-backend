from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle


class TrustedIPThrottle:
    def get_ident(self, request):
        from apps.resumes.views import get_trusted_client_ip

        return get_trusted_client_ip(request)


class TrustedAnonRateThrottle(TrustedIPThrottle, AnonRateThrottle):
    pass


class TrustedUserRateThrottle(TrustedIPThrottle, UserRateThrottle):
    pass


class SensitiveActionThrottle(TrustedIPThrottle, ScopedRateThrottle):
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
        return super().allow_request(request, view)
