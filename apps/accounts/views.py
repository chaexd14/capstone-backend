import unicodedata

from django.contrib.auth import logout
from django.db import transaction
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated

from apps.accounts.models import record_security_event
from apps.accounts.staff_identity import update_identity_name


def validate_profile_name(value):
    value = value.strip()
    if len(value) < 2:
        raise serializers.ValidationError('Use at least 2 characters.')
    allowed_punctuation = {" ", "'", ".", "-"}
    if any(not (unicodedata.category(char).startswith('L') or char in allowed_punctuation) for char in value):
        raise serializers.ValidationError(
            'Use letters, spaces, hyphens, apostrophes, or periods only.'
        )
    return value


class AccountProfileSerializer(serializers.Serializer):
    first_name = serializers.CharField(
        max_length=100, trim_whitespace=True, validators=[validate_profile_name],
    )
    last_name = serializers.CharField(
        max_length=100, trim_whitespace=True, validators=[validate_profile_name],
    )


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

    def _response(self, user):
        role = getattr(user, 'role', 'applicant')
        first_name = (user.first_name or "").strip()
        last_name = (user.last_name or "").strip()
        full_name = f"{first_name} {last_name}".strip()
        role_profile = getattr(user, 'role_profile', None)
        company = getattr(role_profile, 'company', None) if role_profile else None
        return Response({
            "authenticated": True,
            "user": {
                "id": user.id,
                "supabase_id": getattr(user, 'supabase_id', str(user.username)),
                "username": user.username,
                "first_name": first_name,
                "last_name": last_name,
                "email": user.email,
                "name": full_name,
                "role": role,
                "company_id": company.id if company else None,
                "company_name": company.name if company else None,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
            }
        }, status=status.HTTP_200_OK)

    def get(self, request):
        return self._response(request.user)

    def patch(self, request):
        allowed_fields = {'first_name', 'last_name'}
        unexpected_fields = set(request.data.keys()) - allowed_fields
        if unexpected_fields:
            return Response(
                {'detail': 'Only first_name and last_name can be changed here.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AccountProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        first_name = serializer.validated_data['first_name']
        last_name = serializer.validated_data['last_name']

        changed_fields = [
            field
            for field, previous, current in (
                ('first_name', user.first_name, first_name),
                ('last_name', user.last_name, last_name),
            )
            if previous != current
        ]
        if not changed_fields:
            return self._response(user)

        identity = getattr(user, 'supabase_id', user.username)
        update_identity_name(identity, first_name, last_name)

        with transaction.atomic():
            user.first_name = first_name
            user.last_name = last_name
            user.save(update_fields=changed_fields)
            record_security_event(
                user,
                'account.profile',
                user.pk,
                fields=changed_fields,
            )

        return self._response(user)


class LogoutView(APIView):
    """
    Safe session teardown endpoint.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        logout(request)
        return Response({"message": "Logged out successfully."}, status=status.HTTP_200_OK)
