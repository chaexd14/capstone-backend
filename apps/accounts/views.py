from django.contrib.auth import logout
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated


class LoginView(APIView):
    """
    Deprecated legacy login endpoint.
    All password authentication and credential verification is handled
    authoritatively through Supabase Auth.
    Hardcoded demo credentials and fake session tokens have been permanently removed.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        return Response(
            {
                "error": "Direct credential login is disabled. Please authenticate via Supabase Auth.",
                "code": "AUTH_DEPRECATED"
            },
            status=status.HTTP_400_BAD_REQUEST
        )


class MeView(APIView):
    """
    Returns the currently authenticated user's identity and authoritative server-verified role.
    Requires valid Supabase JWT authentication.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        role = getattr(user, 'role', 'applicant')
        return Response({
            "authenticated": True,
            "user": {
                "id": user.id,
                "supabase_id": getattr(user, 'supabase_id', str(user.username)),
                "username": user.username,
                "email": user.email,
                "name": f"{user.first_name} {user.last_name}".strip() or user.username,
                "role": role,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
            }
        }, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """
    Safe session teardown endpoint.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        logout(request)
        return Response({"message": "Logged out successfully."}, status=status.HTTP_200_OK)
