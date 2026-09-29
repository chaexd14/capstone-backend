from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from rest_framework.routers import DefaultRouter

from apps.jobs.views import JobViewSet
from apps.applications.views import ApplicationViewSet
from apps.resumes.views import ResumeViewSet
from apps.accounts.views import LoginView, MeView, LogoutView

router = DefaultRouter()
router.register(r'jobs', JobViewSet, basename='job')
router.register(r'applications', ApplicationViewSet, basename='application')
router.register(r'resumes', ResumeViewSet, basename='resume')

def health_check(request):
    return JsonResponse({
        "status": "healthy",
        "service": "TalentMatch API",
        "version": "1.0.0"
    })

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health/', health_check, name='health_check'),
    path('api/auth/login/', LoginView.as_view(), name='auth_login'),
    path('api/auth/me/', MeView.as_view(), name='auth_me'),
    path('api/auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('api/', include(router.urls)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
