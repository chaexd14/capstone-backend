from rest_framework import permissions


class IsRecruiterOrSuperAdmin(permissions.BasePermission):
    """
    Allows access only to authenticated users who possess a recruiter or superadmin role.
    """
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        role = getattr(user, 'role', '').lower()
        return role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser


class IsApplicationOwnerOrRecruiter(permissions.BasePermission):
    """
    Allows recruiters to inspect or modify all applications.
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

        # Recruiters and staff can access any application
        role = getattr(user, 'role', '').lower()
        if role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser:
            return True

        # Authenticated applicant can only inspect their own application
        if obj.user_id:
            user_uuid = getattr(user, 'supabase_id', str(user.username))
            if str(obj.user_id) == str(user_uuid):
                # Applicants cannot perform administrative mutation (PATCH status, destroy, reprocess)
                return request.method in permissions.SAFE_METHODS

        return False


class IsResumeOwnerOrRecruiter(permissions.BasePermission):
    """
    Allows recruiters to download or inspect any candidate resume.
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

        # Recruiters and staff can access any resume
        role = getattr(user, 'role', '').lower()
        if role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser:
            return True

        # Check resume's linked application ownership
        application = getattr(obj, 'application', None)
        if application and application.user_id:
            user_uuid = getattr(user, 'supabase_id', str(user.username))
            if str(application.user_id) == str(user_uuid):
                return request.method in permissions.SAFE_METHODS

        return False
