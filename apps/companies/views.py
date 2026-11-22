from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import RoleChoices, UserRole
from config.permissions import IsCompanyRecruiter, recruiter_company_id


class CompanyTeamView(APIView):
    """
    Company-scoped list of active recruiter teammates for task assignment and collaboration.
    """
    permission_classes = [IsCompanyRecruiter]

    def get(self, request):
        company_id = recruiter_company_id(request.user)
        if company_id is None:
            return Response([])

        members = (
            UserRole.objects.filter(
                company_id=company_id,
                role=RoleChoices.RECRUITER,
            )
            .select_related('user')
            .order_by('user__first_name', 'user__last_name', 'email')
        )
        payload = []
        for member in members:
            if member.user and not member.user.is_active:
                continue
            first = (member.user.first_name if member.user else '').strip()
            last = (member.user.last_name if member.user else '').strip()
            full_name = f"{first} {last}".strip() or member.email or 'Recruiter'
            payload.append({
                'id': str(member.supabase_user_id or (member.user.username if member.user else member.pk)),
                'name': full_name,
                'email': member.email or (member.user.email if member.user else ''),
            })
        return Response(payload)
