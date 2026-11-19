from django.contrib import admin
from .models import UserProfile, AttendanceRecord, LeaveRecord


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'target_hours', 'target_minutes', 'daily_target_formatted', 'hourly_salary_rate')
    search_fields = ('user__username', 'user__email')


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'login_time', 'logout_time', 'break_minutes', 'worked_hours_formatted', 'diff_formatted', 'status')
    list_filter = ('date', 'user')
    search_fields = ('user__username', 'notes')
    ordering = ('-date',)


@admin.register(LeaveRecord)
class LeaveRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'leave_type', 'duration', 'reason')
    list_filter = ('leave_type', 'date', 'user')
    search_fields = ('user__username', 'reason')
    ordering = ('-date',)
