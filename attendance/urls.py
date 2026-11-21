from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('punch-in/', views.quick_punch_in, name='quick_punch_in'),
    path('punch-out/', views.quick_punch_out, name='quick_punch_out'),
    path('quick-mark-leave/', views.quick_mark_leave, name='quick_mark_leave'),
    path('quick-mark-leave-range/', views.quick_mark_leave_range, name='quick_mark_leave_range'),
    path('quick-cancel-leave/<int:pk>/', views.quick_cancel_leave, name='quick_cancel_leave'),
    path('attendance/log/', views.attendance_create, name='attendance_create'),
    path('attendance/<int:pk>/edit/', views.attendance_update, name='attendance_update'),
    path('attendance/<int:pk>/delete/', views.attendance_delete, name='attendance_delete'),
    path('history/', views.attendance_history, name='history'),
    path('history/export-csv/', views.export_attendance_csv, name='export_attendance_csv'),
    path('leaves/', views.leave_list, name='leave_list'),
    path('leaves/add/', views.leave_create, name='leave_create'),
    path('leaves/<int:pk>/delete/', views.leave_delete, name='leave_delete'),
    path('settings/', views.profile_settings, name='profile_settings'),
    path('setup-db/', views.setup_db, name='setup_db'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
]
