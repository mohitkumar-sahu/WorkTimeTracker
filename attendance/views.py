import csv
import json
import calendar
from datetime import datetime, date
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
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


def get_month_saturdays_status(user, year, month):
    """Returns list of all Saturdays in the month and whether each is marked off or worked."""
    total_days = calendar.monthrange(year, month)[1]
    saturdays = []
    today = timezone.localdate()
    ordinal_names = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}

    for day in range(1, total_days + 1):
        d = date(year, month, day)
        if d.weekday() == 5:  # Saturday
            ordinal = (day - 1) // 7 + 1
            ordinal_str = ordinal_names.get(ordinal, f"{ordinal}th")

            leave = LeaveRecord.objects.filter(user=user, date=d).first()
            att = AttendanceRecord.objects.filter(user=user, date=d).first()

            is_off = (leave is not None and leave.leave_type in ('SATURDAY_OFF', 'LEAVE', 'HOLIDAY'))

            saturdays.append({
                'date': d,
                'day_num': day,
                'ordinal': ordinal,
                'ordinal_label': f"{ordinal_str} Saturday",
                'is_today': (d == today),
                'leave': leave,
                'attendance': att,
                'is_off': is_off,
            })
    return saturdays


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

    # All Saturdays in current month with their status
    month_saturdays = get_month_saturdays_status(request.user, today.year, today.month)
    saturdays_off_count = sum(1 for s in month_saturdays if s['is_off'])

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
        'month_saturdays': month_saturdays,
        'saturdays_off_count': saturdays_off_count,
        'chart_data_json': json.dumps(chart_data),
        'recent_records': recent_records,
        'target_formatted': target_formatted,
    }
    return render(request, 'attendance/dashboard.html', context)


@login_required
@require_POST
def quick_mark_leave(request):
    """Allows 1-click marking of today or any selected date as company off / leave from the home page."""
    date_str = request.POST.get('date')
    if date_str:
        try:
            leave_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            leave_date = timezone.localdate()
    else:
        leave_date = timezone.localdate()

    leave_type = request.POST.get('leave_type', 'SATURDAY_OFF')
    duration = Decimal(request.POST.get('duration', '1.0'))
    reason = request.POST.get('reason', '').strip()

    if not reason:
        if leave_type == 'SATURDAY_OFF':
            reason = "Company declared Saturday Off"
        elif leave_type == 'HOLIDAY':
            reason = "Company declared Holiday"
        else:
            reason = "Leave / Day Off"

    leave, created = LeaveRecord.objects.update_or_create(
        user=request.user,
        date=leave_date,
        defaults={
            'leave_type': leave_type,
            'duration': duration,
            'reason': reason,
        }
    )

    messages.success(
        request,
        f"🌴 {leave_date.strftime('%A, %b %d')} marked as {leave.get_leave_type_display()}! "
        f"This day is protected and will not reduce your daily working average."
    )
    return redirect('dashboard')


@login_required
def quick_cancel_leave(request, pk):
    """Cancels/unmarks a leave directly from the home page."""
    leave = get_object_or_404(LeaveRecord, pk=pk, user=request.user)
    date_str = leave.date.strftime('%A, %b %d')
    leave.delete()
    messages.info(request, f"Removed leave for {date_str}.")
    return redirect('dashboard')


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


def get_user_date_range(user):
    """
    Returns (start_date, end_date) representing when the user first joined/logged
    until the current local date.
    """
    today = timezone.localdate()
    start_date = user.date_joined.date() if user.date_joined else today

    # Also check if user has earlier attendance or leave records
    earliest_att = AttendanceRecord.objects.filter(user=user).order_by('date').first()
    if earliest_att and earliest_att.date < start_date:
        start_date = earliest_att.date

    earliest_leave = LeaveRecord.objects.filter(user=user).order_by('date').first()
    if earliest_leave and earliest_leave.date < start_date:
        start_date = earliest_leave.date

    # Ensure start_date does not exceed today
    if start_date > today:
        start_date = today

    return start_date, today


@login_required
def attendance_history(request):
    today = timezone.localdate()
    start_date, end_date = get_user_date_range(request.user)

    # Years available: only from user's first active year until current year
    years = list(range(start_date.year, end_date.year + 1))

    try:
        selected_year = int(request.GET.get('year', today.year))
    except (ValueError, TypeError):
        selected_year = today.year

    if selected_year not in years:
        selected_year = end_date.year

    # Determine valid month range for the selected year
    min_month = start_date.month if selected_year == start_date.year else 1
    max_month = end_date.month if selected_year == end_date.year else 12

    try:
        selected_month = int(request.GET.get('month', today.month))
    except (ValueError, TypeError):
        selected_month = today.month

    if selected_month < min_month or selected_month > max_month:
        selected_month = max_month  # default to latest valid month

    # Build valid months list for the selected year
    months = [
        (m, calendar.month_name[m])
        for m in range(min_month, max_month + 1)
    ]

    # Map of valid months per year for instant JS dropdown updates
    year_months_map = {}
    for y in years:
        y_min_m = start_date.month if y == start_date.year else 1
        y_max_m = end_date.month if y == end_date.year else 12
        year_months_map[y] = [
            {'num': m, 'name': calendar.month_name[m]}
            for m in range(y_min_m, y_max_m + 1)
        ]

    monthly_stats = calculate_monthly_summary(request.user, selected_year, selected_month)
    records = AttendanceRecord.objects.filter(
        user=request.user,
        date__year=selected_year,
        date__month=selected_month
    ).order_by('-date')

    context = {
        'selected_year': selected_year,
        'selected_month': selected_month,
        'month_name': calendar.month_name[selected_month],
        'monthly_stats': monthly_stats,
        'records': records,
        'years': years,
        'months': months,
        'year_months_json': json.dumps(year_months_map),
        'start_date': start_date,
        'end_date': end_date,
    }
    return render(request, 'attendance/history.html', context)


@login_required
def export_attendance_csv(request):
    today = timezone.localdate()
    start_date, end_date = get_user_date_range(request.user)

    try:
        year = int(request.GET.get('year', today.year))
    except (ValueError, TypeError):
        year = today.year

    try:
        month = int(request.GET.get('month', today.month))
    except (ValueError, TypeError):
        month = today.month

    # Clamp year and month
    if year < start_date.year:
        year = start_date.year
    elif year > end_date.year:
        year = end_date.year

    min_m = start_date.month if year == start_date.year else 1
    max_m = end_date.month if year == end_date.year else 12
    if month < min_m:
        month = min_m
    elif month > max_m:
        month = max_m

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
        'Date', 'Day', 'Login Time', 'Logout Time',
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
    start_date, end_date = get_user_date_range(request.user)
    years = list(range(start_date.year, end_date.year + 1))

    try:
        year = int(request.GET.get('year', today.year))
    except (ValueError, TypeError):
        year = today.year

    if year not in years:
        year = end_date.year

    leave_stats = get_leave_statistics(request.user, year)
    leaves = LeaveRecord.objects.filter(user=request.user, date__year=year).order_by('-date')

    context = {
        'selected_year': year,
        'leave_stats': leave_stats,
        'leaves': leaves,
        'years': years,
        'start_date': start_date,
        'end_date': end_date,
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
    password_form = PasswordChangeForm(user=request.user)
    form = UserProfileForm(instance=profile)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'change_password':
            password_form = PasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)  # Keep user logged in
                messages.success(request, "🔒 Password reset/changed successfully!")
                return redirect('profile_settings')
            else:
                messages.error(request, "Please fix the password errors below.")
        elif action == 'update_username':
            new_username = request.POST.get('username', '').strip()
            if not new_username:
                messages.error(request, "Username cannot be empty.")
            elif new_username != request.user.username:
                if User.objects.filter(username=new_username).exclude(pk=request.user.pk).exists():
                    messages.error(request, f"Username '{new_username}' is already taken.")
                else:
                    request.user.username = new_username
                    request.user.save()
                    messages.success(request, f"Username updated to '{new_username}'!")
                    return redirect('profile_settings')
            else:
                messages.info(request, "Username is unchanged.")
                return redirect('profile_settings')
        else:
            form = UserProfileForm(request.POST, instance=profile)
            if form.is_valid():
                form.save()
                messages.success(request, "✅ Role & target work hours updated successfully!")
                return redirect('profile_settings')

    context = {
        'form': form,
        'password_form': password_form,
        'profile': profile,
        'user_obj': request.user,
    }
    return render(request, 'attendance/profile.html', context)


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

            # Check if user opted to remember credentials/session
            if request.POST.get('remember_me'):
                # 30-day persistent session
                request.session.set_expiry(60 * 60 * 24 * 30)
            else:
                # Browser session (expires when browser is closed)
                request.session.set_expiry(0)

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
