import csv
import json
import random
import calendar
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta
from decimal import Decimal
from django.conf import settings
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
from .forms import (
    AttendanceForm, LeaveForm, UserProfileForm,
    UserRegistrationForm, CaptchaForm, verify_recaptcha
)
from .services import (
    calculate_weekly_summary,
    calculate_monthly_summary,
    get_leave_statistics,
    get_chart_data_for_month,
    get_day_schedule_status,
    format_minutes_to_hm,
    calculate_flexi_recovery_plan,
)


def get_month_saturdays_status(user, year, month):
    """Returns list of all Saturdays in the month and whether each is marked off or worked."""
    total_days = calendar.monthrange(year, month)[1]
    saturdays = []
    today = timezone.localdate()
    ordinal_names = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}

    # Bulk fetch all month leaves & attendances in 2 queries instead of querying inside loop
    start_d = date(year, month, 1)
    end_d = date(year, month, total_days)
    leaves_by_date = {l.date: l for l in LeaveRecord.objects.filter(user=user, date__range=[start_d, end_d])}
    att_by_date = {a.date: a for a in AttendanceRecord.objects.filter(user=user, date__range=[start_d, end_d])}

    for day in range(1, total_days + 1):
        d = date(year, month, day)
        if d.weekday() == 5:  # Saturday
            ordinal = (day - 1) // 7 + 1
            ordinal_str = ordinal_names.get(ordinal, f"{ordinal}th")

            leave = leaves_by_date.get(d)
            att = att_by_date.get(d)

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
    chart_data = get_chart_data_for_month(request.user, today.year, today.month, records=monthly_stats.get('records'))

    # All Saturdays in current month with their status
    month_saturdays = get_month_saturdays_status(request.user, today.year, today.month)
    saturdays_off_count = sum(1 for s in month_saturdays if s['is_off'])

    # Recent 7 attendance records
    recent_records = AttendanceRecord.objects.filter(user=request.user).select_related('user__profile').order_by('-date')[:7]

    # Target info
    target_formatted = request.user.profile.daily_target_formatted

    # Flexi-hours recovery plan
    flexi_plan = calculate_flexi_recovery_plan(request.user, today, today_record=today_record, monthly_summary=monthly_stats)

    context = {
        'today': today,
        'now_dt': now_dt,
        'is_today_sunday': (today.weekday() == 6),
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
        'flexi_plan': flexi_plan,
    }
    return render(request, 'attendance/dashboard.html', context)


@login_required
@require_POST
def quick_mark_leave(request):
    """Allows 1-click marking of today or any selected date as company off / leave / festival from the home page or history page."""
    date_str = request.POST.get('date')
    if date_str:
        try:
            leave_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            leave_date = timezone.localdate()
    else:
        leave_date = timezone.localdate()

    if leave_date.weekday() == 6:
        messages.info(request, "🏖️ Sunday is already an official off day for all employees. No leave needs to be marked.")
        return redirect('dashboard')

    leave_type = request.POST.get('leave_type', 'SATURDAY_OFF')
    duration = Decimal(request.POST.get('duration', '1.0'))
    reason = request.POST.get('reason', '').strip()

    if not reason:
        if leave_type == 'SATURDAY_OFF':
            reason = "Company declared Saturday Off"
        elif leave_type == 'HOLIDAY':
            reason = "Company declared Holiday"
        elif leave_type == 'PAID':
            reason = "Festival / Manager Announced Paid Leave"
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

    if leave_type == 'PAID':
        messages.success(
            request,
            f"🎉 {leave_date.strftime('%A, %b %d')} marked as Paid Festival Leave ({reason})! "
            f"This day is fully paid and will not cause any salary deduction."
        )
    else:
        messages.success(
            request,
            f"🌴 {leave_date.strftime('%A, %b %d')} marked as {leave.get_leave_type_display()}! "
            f"This day is protected and will not reduce your daily working average."
        )

    next_url = request.POST.get('next')
    if next_url == 'history':
        return redirect(f"/history/?year={leave_date.year}&month={leave_date.month}")
    return redirect('dashboard')


@login_required
def quick_cancel_leave(request, pk):
    """Cancels/unmarks a leave directly from the home page or history page."""
    leave = get_object_or_404(LeaveRecord, pk=pk, user=request.user)
    leave_date = leave.date
    date_str = leave.date.strftime('%A, %b %d')
    leave.delete()
    messages.info(request, f"Removed leave for {date_str}.")

    next_url = request.GET.get('next')
    if next_url == 'history':
        return redirect(f"/history/?year={leave_date.year}&month={leave_date.month}")
    elif next_url == 'leaves':
        return redirect('leave_list')
    return redirect('dashboard')


@login_required
@require_POST
def quick_mark_leave_range(request):
    """
    Allows marking leaves across a date range (e.g. 8 days from date to date)
    in a single action instead of entering day by day.
    """
    start_str = request.POST.get('start_date')
    end_str = request.POST.get('end_date')

    if not start_str or not end_str:
        messages.error(request, "Please specify both From Date and To Date.")
        return redirect('history')

    try:
        start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_str, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, "Invalid date format provided.")
        return redirect('history')

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    leave_type = request.POST.get('leave_type', 'LEAVE')
    duration = Decimal(request.POST.get('duration', '1.0'))
    reason = request.POST.get('reason', '').strip()
    skip_off_days = request.POST.get('skip_off_days') in ('on', '1', 'true', 'True')

    if not reason:
        type_labels = dict(LeaveRecord.LEAVE_CHOICES)
        reason = type_labels.get(leave_type, "Leave / Day Off")

    current_date = start_date
    marked_dates = []
    skipped_count = 0

    while current_date <= end_date:
        if skip_off_days:
            schedule = get_day_schedule_status(request.user, current_date)
            if schedule['is_off_day']:
                skipped_count += 1
                current_date += timedelta(days=1)
                continue

        LeaveRecord.objects.update_or_create(
            user=request.user,
            date=current_date,
            defaults={
                'leave_type': leave_type,
                'duration': duration,
                'reason': reason,
            }
        )
        marked_dates.append(current_date)
        current_date += timedelta(days=1)

    count = len(marked_dates)
    if count > 0:
        skip_msg = f" (skipped {skipped_count} weekly off{'s' if skipped_count != 1 else ''})" if skipped_count > 0 else ""
        messages.success(
            request,
            f"🌴 Successfully marked {count} day{'s' if count != 1 else ''} of leave from "
            f"{start_date.strftime('%b %d, %Y')} to {end_date.strftime('%b %d, %Y')}{skip_msg} ({reason})!"
        )
    else:
        messages.warning(request, "No leave dates were marked because all dates in the range were already scheduled company offs.")

    next_url = request.POST.get('next')
    if next_url == 'leaves':
        return redirect('leave_list')
    return redirect(f"/history/?year={start_date.year}&month={start_date.month}")


@login_required
@require_POST
def quick_punch_in(request):
    today = timezone.localdate()
    if today.weekday() == 6:  # Sunday
        messages.warning(request, "🏖️ Today is Sunday! Sunday is completely off for all employees. Attendance tracking is disabled on Sundays.")
        return redirect('dashboard')

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
    if today.weekday() == 6:  # Sunday
        messages.warning(request, "🏖️ Today is Sunday! Sunday is completely off for all employees.")
        return redirect('dashboard')

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
    ).select_related('user__profile').order_by('-date')

    monthly_leaves = LeaveRecord.objects.filter(
        user=request.user,
        date__year=selected_year,
        date__month=selected_month
    ).order_by('-date')

    leaves_by_date = {l.date: l for l in monthly_leaves}
    records_by_date = {r.date: r for r in records}

    all_dates = sorted(set(list(records_by_date.keys()) + list(leaves_by_date.keys())), reverse=True)
    table_rows = []
    for d in all_dates:
        table_rows.append({
            'date': d,
            'record': records_by_date.get(d),
            'leave': leaves_by_date.get(d),
            'schedule_status': get_day_schedule_status(request.user, d),
        })

    context = {
        'selected_year': selected_year,
        'selected_month': selected_month,
        'month_name': calendar.month_name[selected_month],
        'monthly_stats': monthly_stats,
        'records': records,
        'monthly_leaves': monthly_leaves,
        'leaves_by_date': leaves_by_date,
        'table_rows': table_rows,
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
            if record.date.weekday() == 6:
                messages.error(request, "Sunday is completely off for all employees. Attendance cannot be recorded on Sundays.")
                return render(request, 'attendance/attendance_form.html', {'form': form, 'title': 'Log Working Hours'})
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
    profile, _ = UserProfile.objects.get_or_create(user=request.user)
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
                messages.success(request, "✅ Role, target hours, and salary settings updated successfully!")
                return redirect('profile_settings')

    salary_info = profile.get_salary_deduction_info()

    context = {
        'form': form,
        'password_form': password_form,
        'profile': profile,
        'salary_info': salary_info,
        'user_obj': request.user,
    }
    return render(request, 'attendance/profile.html', context)


def send_registration_otp(email, username, otp):
    """
    Sends the 6-digit OTP code to the user's email using Django's email framework.
    Returns (success: bool, dev_code: str or None, error: str or None)
    """
    subject = f"Your WorkTime Tracker Verification Code: {otp}"
    plain_text = f"""Hi {username},

Thank you for registering on WorkTime Tracker!

Your 6-digit email verification code is:

    {otp}

This code will expire in 10 minutes.
If you did not request this verification, you can safely ignore this email.

Best regards,
WorkTime Tracker Team
"""
    html_text = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 28px; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0;">
        <div style="text-align: center; margin-bottom: 24px;">
            <h2 style="color: #4f46e5; margin: 0; font-size: 22px; font-weight: 800;">⏱️ WorkTime Tracker</h2>
            <p style="color: #64748b; font-size: 13px; margin-top: 4px;">Email Verification Code</p>
        </div>
        <div style="background: #f8fafc; border-radius: 8px; padding: 24px; text-align: center; margin-bottom: 20px;">
            <p style="color: #334155; font-size: 14px; margin: 0 0 12px;">Hello <strong>{username}</strong>, enter this code on the registration page to verify your email:</p>
            <div style="font-size: 34px; font-weight: 800; letter-spacing: 8px; color: #4f46e5; padding: 8px 0; font-family: monospace;">
                {otp}
            </div>
            <p style="color: #94a3b8; font-size: 12px; margin: 8px 0 0;">⏱️ Code is valid for <strong>10 minutes</strong>.</p>
        </div>
        <p style="color: #64748b; font-size: 12px; line-height: 1.5; margin: 0;">
            If you did not request this verification, no action is needed and you can safely ignore this email.
        </p>
    </div>
    """

    # If SMTP is not yet configured, log to console and provide fallback
    if not getattr(settings, 'EMAIL_HOST_USER', None):
        print(f"[OTP DEV NOTIFICATION] OTP for {email} ({username}): {otp}")
        return True, otp, None

    try:
        from django.core.mail import send_mail
        send_mail(
            subject=subject,
            message=plain_text,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'WorkTime Tracker <noreply@worktimetracker.app>'),
            recipient_list=[email],
            html_message=html_text,
            fail_silently=False,
        )
        return True, None, None
    except Exception as e:
        print(f"SMTP delivery error to {email}: {e}")
        return False, otp, str(e)


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    recaptcha_site_key = (
        getattr(settings, 'RECAPTCHA_PUBLIC_KEY', None)
        or getattr(settings, 'RECAPTCHA_SITE_KEY', None)
        or '6LcftywuAAAAADhm6T1T3sUemmpViTYX6aftCXt8'
    )
    recaptcha_error = None
    dev_otp_display = None
    step = 'form'

    pending = request.session.get('reg_pending')
    if pending:
        step = 'verify_otp'

    if request.method == 'POST':
        action = request.POST.get('action', 'submit_form')

        # 1. User wants to change email or cancel pending verification
        if action == 'cancel_verification':
            if 'reg_pending' in request.session:
                del request.session['reg_pending']
                request.session.modified = True
            messages.info(request, "Registration reset. You can edit your details below.")
            return redirect('register')

        # 2. User requests a new OTP code
        elif action == 'resend_otp':
            if not pending:
                messages.error(request, "Session expired. Please fill the registration form again.")
                return redirect('register')

            now_ts = timezone.now().timestamp()
            last_sent = pending.get('otp_sent_at', 0)
            if now_ts - last_sent < 30:
                wait_sec = int(30 - (now_ts - last_sent))
                messages.warning(request, f"Please wait {wait_sec}s before requesting another verification code.")
            else:
                new_otp = f"{random.randint(100000, 999999)}"
                pending['otp'] = new_otp
                pending['otp_sent_at'] = now_ts
                pending['attempts'] = 0
                request.session['reg_pending'] = pending
                request.session.modified = True

                success, dev_code, err = send_registration_otp(pending['email'], pending['username'], new_otp)
                if success:
                    messages.success(request, f"A new verification code has been sent to {pending['email']}!")
                else:
                    messages.warning(request, f"Notice: {err or 'Could not deliver email'}")
                if dev_code:
                    dev_otp_display = dev_code

            return render(request, 'attendance/register.html', {
                'step': 'verify_otp',
                'pending_email': pending['email'],
                'pending_username': pending['username'],
                'dev_otp_display': dev_otp_display,
            })

        # 3. User submits the 6-digit OTP code to complete registration
        elif action == 'verify_otp':
            if not pending:
                messages.error(request, "Registration session expired. Please register again.")
                return redirect('register')

            user_otp = request.POST.get('otp', '').strip()
            now_ts = timezone.now().timestamp()
            otp_sent_at = pending.get('otp_sent_at', 0)

            # Check 10-minute expiry (600s)
            if now_ts - otp_sent_at > 600:
                del request.session['reg_pending']
                request.session.modified = True
                messages.error(request, "Verification code has expired (valid for 10 minutes). Please register again.")
                return redirect('register')

            # Check maximum attempt limit (5 attempts)
            attempts = pending.get('attempts', 0) + 1
            pending['attempts'] = attempts
            request.session['reg_pending'] = pending
            request.session.modified = True

            if attempts > 5:
                del request.session['reg_pending']
                request.session.modified = True
                messages.error(request, "Too many incorrect attempts. For security, please register again.")
                return redirect('register')

            # Verify OTP
            if user_otp == str(pending.get('otp')):
                try:
                    user = User.objects.create_user(
                        username=pending['username'],
                        email=pending['email'],
                        password=pending['password']
                    )
                    del request.session['reg_pending']
                    request.session.modified = True

                    login(request, user)
                    request.session.set_expiry(600)
                    request.session['login_timestamp'] = timezone.now().timestamp()

                    messages.success(request, f"🎉 Email verified! Welcome to WorkTime Tracker, {user.username}!")
                    return redirect('dashboard')
                except Exception as e:
                    messages.error(request, f"Could not create account: {e}")
                    return redirect('register')
            else:
                remaining = 5 - attempts
                messages.error(request, f"❌ Invalid verification code. {remaining} attempt(s) remaining.")
                return render(request, 'attendance/register.html', {
                    'step': 'verify_otp',
                    'pending_email': pending['email'],
                    'pending_username': pending['username'],
                    'dev_otp_display': dev_otp_display,
                })

        # 4. Initial registration form submission (Step 1)
        else:
            form = UserRegistrationForm(request.POST)
            recaptcha_token = request.POST.get('g-recaptcha-response', '').strip()
            client_ip = request.META.get('REMOTE_ADDR')
            is_human = verify_recaptcha(recaptcha_token, client_ip)

            if not is_human:
                if not recaptcha_token:
                    recaptcha_error = "Please complete the 'I'm not a robot' reCAPTCHA verification."
                else:
                    recaptcha_error = "Security verification failed. Please try submitting again."
                messages.error(request, recaptcha_error)
            elif form.is_valid():
                username = form.cleaned_data['username']
                email = form.cleaned_data['email']
                password = form.cleaned_data['password']

                otp = f"{random.randint(100000, 999999)}"
                now_ts = timezone.now().timestamp()

                request.session['reg_pending'] = {
                    'username': username,
                    'email': email,
                    'password': password,
                    'otp': otp,
                    'otp_sent_at': now_ts,
                    'attempts': 0,
                }
                request.session.modified = True

                success, dev_code, err = send_registration_otp(email, username, otp)
                if dev_code:
                    dev_otp_display = dev_code

                messages.success(request, f"Verification code sent to {email}!")
                return render(request, 'attendance/register.html', {
                    'step': 'verify_otp',
                    'pending_email': email,
                    'pending_username': username,
                    'dev_otp_display': dev_otp_display,
                })

    else:
        # GET request
        form = UserRegistrationForm()

    context = {
        'form': form,
        'form2': form,  # Enables rendering {{ form2.captcha }} as well as {{ form.captcha }}
        'step': step,
        'pending_email': pending.get('email') if pending else None,
        'pending_username': pending.get('username') if pending else None,
        'recaptcha_site_key': recaptcha_site_key,
        'recaptcha_error': recaptcha_error,
        'dev_otp_display': dev_otp_display,
    }
    return render(request, 'attendance/register.html', context)


def setup_db(request):
    """
    Initializes/migrates database tables and seeds demo user.
    Useful for one-time initialization of fresh cloud databases (like Neon PostgreSQL).
    """
    from django.core.management import call_command
    from django.http import HttpResponse
    try:
        call_command("migrate", interactive=False)
        from django.contrib.auth.models import User
        if not User.objects.filter(username="demo").exists():
            call_command("seed_demo_data")
        return HttpResponse(
            "<div style='font-family:sans-serif;padding:40px;text-align:center;'>"
            "<h2 style='color:#16a34a;'>✅ Database Migrated & Initialized Successfully!</h2>"
            "<p>All database tables and the demo user (username: <code>demo</code>, password: <code>demo123</code>) are ready.</p>"
            "<p><a href='/login/' style='display:inline-block;padding:10px 20px;background:#4f46e5;color:white;text-decoration:none;border-radius:6px;font-weight:bold;'>Go to Login Page →</a></p>"
            "</div>"
        )
    except Exception as e:
        return HttpResponse(f"Error during migration: {e}", status=500)


def login_view(request):
    from django.core.management import call_command
    from django.db.utils import ProgrammingError, OperationalError

    if request.user.is_authenticated:
        return redirect('dashboard')

    timeout_notice = request.GET.get('timeout') == '1'

    if request.method == 'POST':
        try:
            form = AuthenticationForm(request, data=request.POST)
            is_valid = form.is_valid()
        except (ProgrammingError, OperationalError):
            # Fresh database without tables - auto migrate once
            try:
                call_command("migrate", interactive=False)
                from django.contrib.auth.models import User
                if not User.objects.filter(username="demo").exists():
                    call_command("seed_demo_data")
                form = AuthenticationForm(request, data=request.POST)
                is_valid = form.is_valid()
            except Exception:
                is_valid = False

        if is_valid:
            user = form.get_user()
            login(request, user)

            # Strict 10-minute session auto logout requirement
            # Session expires exactly 10 minutes (600 seconds) after login
            request.session.set_expiry(600)
            request.session['login_timestamp'] = timezone.now().timestamp()

            messages.success(request, f"Welcome back, {user.username}!")
            next_url = request.GET.get('next', 'dashboard')
            return redirect(next_url)
    else:
        form = AuthenticationForm()

    return render(request, 'attendance/login.html', {'form': form, 'timeout_notice': timeout_notice})



def logout_view(request):
    is_timeout = request.GET.get('timeout') == '1'
    logout(request)
    if is_timeout:
        messages.warning(request, "⏰ You have been automatically logged out after 10 minutes of session duration.")
        return redirect('/login/?timeout=1')
    else:
        messages.info(request, "You have been logged out.")
        return redirect('login')
