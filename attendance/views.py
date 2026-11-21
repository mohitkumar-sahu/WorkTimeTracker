import csv
import json
import calendar
from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import AttendanceRecord, LeaveRecord, UserProfile
from .forms import AttendanceForm, LeaveForm, UserProfileForm, UserRegistrationForm
from .services import (
    calculate_weekly_summary,
    calculate_monthly_summary,
    get_leave_statistics,
    get_chart_data_for_month,
    get_day_schedule_status,
    format_minutes_to_hm,
)


@login_required
def dashboard(request):
    today = timezone.localdate()
    now_dt = timezone.localtime(timezone.now())

    # Get or check today's attendance record
    today_record = AttendanceRecord.objects.filter(user=request.user, date=today).first()
    today_leave = LeaveRecord.objects.filter(user=request.user, date=today).first()
    today_schedule = get_day_schedule_status(request.user, today)

    # Calculate summaries
    weekly_stats = calculate_weekly_summary(request.user, today)
    monthly_stats = calculate_monthly_summary(request.user, today.year, today.month)
    leave_stats = get_leave_statistics(request.user, today.year)
    chart_data = get_chart_data_for_month(request.user, today.year, today.month)

    # Recent 7 attendance records
    recent_records = AttendanceRecord.objects.filter(user=request.user).order_by('-date')[:7]

    # Target info
    target_formatted = request.user.profile.daily_target_formatted

    context = {
        'today': today,
        'now_dt': now_dt,
        'today_record': today_record,
        'today_leave': today_leave,
        'today_schedule': today_schedule,
        'weekly_stats': weekly_stats,
        'monthly_stats': monthly_stats,
        'leave_stats': leave_stats,
        'chart_data_json': json.dumps(chart_data),
        'recent_records': recent_records,
        'target_formatted': target_formatted,
    }
    return render(request, 'attendance/dashboard.html', context)


@login_required
@require_POST
def quick_punch_in(request):
    today = timezone.localdate()
    now_time = timezone.localtime(timezone.now()).time().replace(microsecond=0)

    record, created = AttendanceRecord.objects.get_or_create(
        user=request.user,
        date=today,
        defaults={'login_time': now_time}
    )

    if not created:
        if record.logout_time:
            messages.warning(request, f"You already completed punch out today at {record.logout_time.strftime('%I:%M %p')}.")
        else:
            messages.info(request, f"You were already punched in today at {record.login_time.strftime('%I:%M %p')}.")
    else:
        req_logout = record.expected_logout_time.strftime('%I:%M %p')
        messages.success(
            request,
            f"✅ Punched in successfully at {now_time.strftime('%I:%M %p')}! "
            f"To achieve your daily target ({request.user.profile.daily_target_formatted}), "
            f"your minimum logout time is {req_logout}."
        )

    return redirect('dashboard')


@login_required
@require_POST
def quick_punch_out(request):
    today = timezone.localdate()
    now_time = timezone.localtime(timezone.now()).time().replace(microsecond=0)

    record = AttendanceRecord.objects.filter(user=request.user, date=today).first()
    if not record:
        messages.error(request, "You haven't punched in today yet! Please punch in first.")
        return redirect('dashboard')

    record.logout_time = now_time
    record.save()

    worked_str = record.worked_hours_formatted
    diff_str = record.diff_formatted
    if record.diff_minutes >= 0:
        messages.success(
            request,
            f"🎉 Great job! Punched out at {now_time.strftime('%I:%M %p')}. "
            f"Total Worked: {worked_str} ({diff_str} surplus)!"
        )
    else:
        messages.warning(
            request,
            f"⚠️ Punched out at {now_time.strftime('%I:%M %p')}. "
            f"Total Worked: {worked_str} ({diff_str} deficit). Try to cover the remaining time later this week!"
        )

    return redirect('dashboard')


@login_required
def attendance_history(request):
    today = timezone.localdate()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    monthly_stats = calculate_monthly_summary(request.user, year, month)
    records = AttendanceRecord.objects.filter(
        user=request.user,
        date__year=year,
        date__month=month
    ).order_by('-date')

    # Year choices (current year - 2 to current year + 1)
    years = list(range(today.year - 2, today.year + 2))
    months = [(i, calendar.month_name[i]) for i in range(1, 13)]

    context = {
        'selected_year': year,
        'selected_month': month,
        'month_name': calendar.month_name[month],
        'monthly_stats': monthly_stats,
        'records': records,
        'years': years,
        'months': months,
    }
    return render(request, 'attendance/history.html', context)


@login_required
def export_attendance_csv(request):
    today = timezone.localdate()
    year = int(request.GET.get('year', today.year))
    month = int(request.GET.get('month', today.month))

    records = AttendanceRecord.objects.filter(
        user=request.user,
        date__year=year,
        date__month=month
    ).order_by('date')

    month_name = calendar.month_name[month]
    filename = f"attendance_{request.user.username}_{month_name}_{year}.csv"

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Day', 'Login Time', 'Logout Time', 'Break (Mins)',
        'Worked Hours', 'Target Hours', 'Surplus / Deficit', 'Status', 'Notes'
    ])

    for r in records:
        login_s = r.login_time.strftime('%I:%M %p') if r.login_time else '-'
        logout_s = r.logout_time.strftime('%I:%M %p') if r.logout_time else 'In Progress'
        writer.writerow([
            r.date.strftime('%Y-%m-%d'),
            r.date.strftime('%A'),
            login_s,
            logout_s,
            r.break_minutes,
            r.worked_hours_formatted,
            request.user.profile.daily_target_formatted,
            r.diff_formatted,
            r.status,
            r.notes or ''
        ])

    return response


@login_required
def attendance_create(request):
    if request.method == 'POST':
        form = AttendanceForm(request.POST, user=request.user)
        if form.is_valid():
            record = form.save(commit=False)
            record.user = request.user
            record.save()
            messages.success(request, f"Attendance for {record.date} saved successfully.")
            return redirect('history')
    else:
        form = AttendanceForm(user=request.user)

    return render(request, 'attendance/attendance_form.html', {'form': form, 'title': 'Log Working Hours'})


@login_required
def attendance_update(request, pk):
    record = get_object_or_404(AttendanceRecord, pk=pk, user=request.user)
    if request.method == 'POST':
        form = AttendanceForm(request.POST, instance=record, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, f"Attendance record for {record.date} updated successfully.")
            return redirect('history')
    else:
        form = AttendanceForm(instance=record, user=request.user)

    return render(request, 'attendance/attendance_form.html', {
        'form': form,
        'title': f'Edit Attendance for {record.date}',
        'is_edit': True,
        'record': record
    })


@login_required
def attendance_delete(request, pk):
    record = get_object_or_404(AttendanceRecord, pk=pk, user=request.user)
    if request.method == 'POST':
        date_str = str(record.date)
        record.delete()
        messages.success(request, f"Attendance record for {date_str} deleted.")
        return redirect('history')
    return render(request, 'attendance/confirm_delete.html', {'object': record, 'type': 'Attendance'})


@login_required
def leave_list(request):
    today = timezone.localdate()
    year = int(request.GET.get('year', today.year))

    leave_stats = get_leave_statistics(request.user, year)
    leaves = LeaveRecord.objects.filter(user=request.user, date__year=year).order_by('-date')
    years = list(range(today.year - 2, today.year + 2))

    context = {
        'selected_year': year,
        'leave_stats': leave_stats,
        'leaves': leaves,
        'years': years,
    }
    return render(request, 'attendance/leaves.html', context)


@login_required
def leave_create(request):
    if request.method == 'POST':
        form = LeaveForm(request.POST, user=request.user)
        if form.is_valid():
            leave = form.save(commit=False)
            leave.user = request.user
            leave.save()
            messages.success(request, f"Leave on {leave.date} ({leave.get_leave_type_display()}) recorded.")
            return redirect('leave_list')
    else:
        form = LeaveForm(user=request.user)

    return render(request, 'attendance/leave_form.html', {'form': form, 'title': 'Apply / Record Leave'})


@login_required
def leave_delete(request, pk):
    leave = get_object_or_404(LeaveRecord, pk=pk, user=request.user)
    if request.method == 'POST':
        date_str = str(leave.date)
        leave.delete()
        messages.success(request, f"Leave on {date_str} cancelled.")
        return redirect('leave_list')
    return render(request, 'attendance/confirm_delete.html', {'object': leave, 'type': 'Leave'})


@login_required
def profile_settings(request):
    profile = request.user.profile
    if request.method == 'POST':
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Target work hours and settings updated successfully!")
            return redirect('profile_settings')
    else:
        form = UserProfileForm(instance=profile)

    return render(request, 'attendance/profile.html', {'form': form, 'profile': profile})


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = User.objects.create_user(
                username=form.cleaned_data['username'],
                email=form.cleaned_data['email'],
                password=form.cleaned_data['password']
            )
            login(request, user)
            messages.success(request, f"Welcome {user.username}! Your worktime tracker is ready.")
            return redirect('dashboard')
    else:
        form = UserRegistrationForm()

    return render(request, 'attendance/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Welcome back, {user.username}!")
            next_url = request.GET.get('next', 'dashboard')
            return redirect(next_url)
    else:
        form = AuthenticationForm()

    return render(request, 'attendance/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect('login')
