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
    Allows authenticated applicants or callers with a valid tracking token to inspect ONLY their own application.
    """
    def has_permission(self, request, view):
        user = request.user
        token = request.query_params.get('tracking_token') or request.headers.get('X-Tracking-Token')
        if user and user.is_authenticated:
            return True
        if token and getattr(view, 'action', '') in ('retrieve', 'match_details'):
            return True
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user
        token = request.query_params.get('tracking_token') or request.headers.get('X-Tracking-Token')

        # Recruiters and staff can access any application
        if user and user.is_authenticated:
            role = getattr(user, 'role', '').lower()
            if role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser:
                return True

        # Check tracking token from query or header for applicant guest/polling sessions (read-only)
        if token and obj.tracking_token and str(obj.tracking_token) == str(token):
            return request.method in permissions.SAFE_METHODS

        # Authenticated applicant can only inspect their own application
        if user and user.is_authenticated and obj.user_id:
            user_uuid = getattr(user, 'supabase_id', str(user.username))
            if str(obj.user_id) == str(user_uuid):
                # Applicants cannot perform administrative mutation (PATCH status, destroy, reprocess)
                return request.method in permissions.SAFE_METHODS

        return False


class IsResumeOwnerOrRecruiter(permissions.BasePermission):
    """
    Allows recruiters to download or inspect any candidate resume.
    Allows applicants to download or inspect only their own attached resume.
    """
    def has_permission(self, request, view):
        user = request.user
        token = request.query_params.get('tracking_token') or request.headers.get('X-Tracking-Token')
        if user and user.is_authenticated:
            return True
        if token and getattr(view, 'action', '') in ('download', 'retrieve'):
            return True
        return False

    def has_object_permission(self, request, view, obj):
        user = request.user
        token = request.query_params.get('tracking_token') or request.headers.get('X-Tracking-Token')

        # Recruiters and staff can access any resume
        if user and user.is_authenticated:
            role = getattr(user, 'role', '').lower()
            if role in ('recruiter', 'superadmin') or user.is_staff or user.is_superuser:
                return True

        # Check resume's linked application ownership
        application = getattr(obj, 'application', None)
        if application:
            if token and application.tracking_token and str(application.tracking_token) == str(token):
                return request.method in permissions.SAFE_METHODS

            if user and user.is_authenticated and application.user_id:
                user_uuid = getattr(user, 'supabase_id', str(user.username))
                if str(application.user_id) == str(user_uuid):
                    return request.method in permissions.SAFE_METHODS

        return False
