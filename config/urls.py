from django.contrib import admin
from django.conf import settings
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from config.health import liveness, readiness

from apps.jobs.views import JobViewSet, ArchivedJobViewSet
from apps.applications.views import ApplicationViewSet
from apps.resumes.views import ResumeViewSet
from apps.notifications.views import NotificationViewSet
from apps.accounts.views import LoginView, MeView, LogoutView
from apps.accounts.staff_invitations import AcceptStaffInvitationView
from apps.companies.management_views import CompanyManagementViewSet, StaffManagementViewSet

router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'archived-jobs', ArchivedJobViewSet, basename='archived-job')
router.register(r'applications', ApplicationViewSet, basename='application')
router.register(r'resumes', ResumeViewSet, basename='resume')
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'platform/companies', CompanyManagementViewSet, basename='platform-company')
router.register(r'platform/staff', StaffManagementViewSet, basename='platform-staff')

urlpatterns = [
    path('api/health/', liveness, name='health_check'),
    path('api/live/', liveness, name='liveness'),
    path('api/ready/', readiness, name='readiness'),
    path('api/auth/login/', LoginView.as_view(), name='auth_login'),
    path('api/auth/me/', MeView.as_view(), name='auth_me'),
    path('api/auth/staff-invitation/accept/', AcceptStaffInvitationView.as_view(), name='accept-staff-invitation'),
    path('api/auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('api/', include(router.urls)),
]

if settings.ENABLE_DJANGO_ADMIN:
    urlpatterns.append(path('admin/', admin.site.urls))
