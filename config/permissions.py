from rest_framework import permissions


def is_platform_admin(user):
    return bool(user and user.is_authenticated) and getattr(user, 'role', '') == 'superadmin'


def is_recruiter(user):
    return bool(user and user.is_authenticated) and getattr(user, 'role', '') == 'recruiter'


def recruiter_company_id(user):
    return getattr(getattr(user, 'role_profile', None), 'company_id', None)


def scope_recruiter_records(queryset, user, company_field):
    company_id = recruiter_company_id(user)
    if is_recruiter(user) and company_id is not None:
        return queryset.filter(**{company_field: company_id})
    return queryset.none()


def can_manage_company(user, company_id):
    return is_recruiter(user) and company_id is not None and recruiter_company_id(user) == company_id


class IsCompanyRecruiter(permissions.BasePermission):
    def has_permission(self, request, view):
        return is_recruiter(request.user) and recruiter_company_id(request.user) is not None


class IsApplicationOwnerOrRecruiter(permissions.BasePermission):
    """
    Allows recruiters to inspect or modify applications in their company.
    Allows authenticated applicants to inspect ONLY their own application.
    Tracking tokens do not grant API access.
    """
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if is_recruiter(user):
            return can_manage_company(user, obj.job.company_id)

        # Authenticated applicant can only inspect their own application
        if obj.user_id:
            user_uuid = getattr(user, 'supabase_id', str(user.username))
            if str(obj.user_id) == str(user_uuid):
                # Applicants cannot perform administrative mutation (PATCH status, destroy, reprocess)
                return request.method in permissions.SAFE_METHODS

        return False


class IsResumeOwnerOrRecruiter(permissions.BasePermission):
    """
    Allows recruiters to download or inspect resumes in their company.
    Allows authenticated applicants to download or inspect only their own attached resume.
    Tracking tokens do not grant API access.
    """
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if is_recruiter(user):
            return can_manage_company(user, obj.application.job.company_id)

        # Check resume's linked application ownership
        application = getattr(obj, 'application', None)
        if application and application.user_id:
            user_uuid = getattr(user, 'supabase_id', str(user.username))
            if str(application.user_id) == str(user_uuid):
                return request.method in permissions.SAFE_METHODS

        return False
