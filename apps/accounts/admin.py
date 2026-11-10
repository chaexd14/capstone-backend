from django.contrib import admin
from apps.accounts.models import UserRole


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    list_display = ('id', 'email', 'role', 'supabase_user_id', 'assigned_by', 'created_at', 'updated_at')
    list_filter = ('role', 'assigned_by', 'created_at')
    search_fields = ('email', 'supabase_user_id', 'notes')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('user', 'supabase_user_id', 'email', 'role', 'company')
        }),
        ('Audit Trail', {
            'fields': ('assigned_by', 'notes', 'created_at', 'updated_at')
        }),
    )
