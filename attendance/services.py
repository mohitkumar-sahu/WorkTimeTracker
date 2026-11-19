from datetime import date, datetime, timedelta
import calendar
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum
from .models import AttendanceRecord, LeaveRecord


def format_minutes_to_hm(minutes, show_sign=False):
    """Converts minutes to formatted string like '9h 15m' or '+1h 20m'."""
    if minutes is None:
        return "-"
    
    is_negative = minutes < 0
    abs_mins = int(round(abs(minutes)))
    h = abs_mins // 60
    m = abs_mins % 60

    if show_sign:
        if is_negative:
            return f"-{h}h {m:02d}m"
        elif abs_mins == 0:
            return "0m"
        else:
            return f"+{h}h {m:02d}m"
    else:
        return f"{h}h {m:02d}m"


def get_week_bounds(target_date=None):
    """Returns (monday, sunday) dates for the week containing target_date."""
    if target_date is None:
        target_date = timezone.localdate()
    monday = target_date - timedelta(days=target_date.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def calculate_weekly_summary(user, target_date=None):
    """Calculates weekly stats for user for the week of target_date."""
    if target_date is None:
        target_date = timezone.localdate()
    
    monday, sunday = get_week_bounds(target_date)
    records = AttendanceRecord.objects.filter(
        user=user,
        date__range=[monday, sunday]
    ).order_by('date')

    target_per_day = user.profile.daily_target_minutes

    completed_records = [r for r in records if r.logout_time is not None]
    days_worked = len(completed_records)
    total_minutes = sum(r.get_worked_minutes() for r in completed_records)

    leaves = LeaveRecord.objects.filter(
        user=user,
        date__range=[monday, sunday]
    )
    total_leaves = float(sum(l.duration for l in leaves))

    if days_worked > 0:
        avg_minutes = total_minutes / days_worked
        total_target_minutes = days_worked * target_per_day
        diff_minutes = total_minutes - total_target_minutes
    else:
        avg_minutes = 0
        total_target_minutes = 0
        diff_minutes = 0

    return {
        'monday': monday,
        'sunday': sunday,
        'days_worked': days_worked,
        'total_minutes': total_minutes,
        'total_hours_formatted': format_minutes_to_hm(total_minutes),
        'avg_minutes': avg_minutes,
        'avg_hours_formatted': format_minutes_to_hm(avg_minutes),
        'avg_hours_decimal': round(avg_minutes / 60.0, 2),
        'diff_minutes': diff_minutes,
        'diff_formatted': format_minutes_to_hm(diff_minutes, show_sign=True),
        'is_surplus': diff_minutes >= 0,
        'total_leaves': total_leaves,
        'records': records,
    }


def calculate_monthly_summary(user, year=None, month=None):
    """Calculates monthly statistics for the specified year and month."""
    today = timezone.localdate()
    if year is None:
        year = today.year
    if month is None:
        month = today.month

    target_per_day = user.profile.daily_target_minutes

    # Get records for this month
    records = AttendanceRecord.objects.filter(
        user=user,
        date__year=year,
        date__month=month
    ).order_by('date')

    completed_records = [r for r in records if r.logout_time is not None]
    days_worked = len(completed_records)
    total_minutes = sum(r.get_worked_minutes() for r in completed_records)

    # Leaves for this month
    leaves = LeaveRecord.objects.filter(
        user=user,
        date__year=year,
        date__month=month
    )
    total_leaves_month = float(sum(l.duration for l in leaves))

    # All-time / Current Year leaves
    leaves_year = LeaveRecord.objects.filter(
        user=user,
        date__year=year
    )
    total_leaves_year = float(sum(l.duration for l in leaves_year))

    if days_worked > 0:
        avg_minutes = total_minutes / days_worked
        total_target_minutes = days_worked * target_per_day
        diff_minutes = total_minutes - total_target_minutes
    else:
        avg_minutes = 0
        total_target_minutes = 0
        diff_minutes = 0

    # Calculate estimated deduction if deficit and hourly rate provided
    hourly_rate = float(user.profile.hourly_salary_rate or 0)
    deficit_hours = max(0, -diff_minutes) / 60.0
    estimated_deduction = round(deficit_hours * hourly_rate, 2)

    # Month name
    month_name = calendar.month_name[month]

    return {
        'year': year,
        'month': month,
        'month_name': month_name,
        'days_worked': days_worked,
        'total_minutes': total_minutes,
        'total_hours_formatted': format_minutes_to_hm(total_minutes),
        'avg_minutes': avg_minutes,
        'avg_hours_formatted': format_minutes_to_hm(avg_minutes),
        'avg_hours_decimal': round(avg_minutes / 60.0, 2),
        'diff_minutes': diff_minutes,
        'diff_formatted': format_minutes_to_hm(diff_minutes, show_sign=True),
        'is_surplus': diff_minutes >= 0,
        'deficit_hours': round(deficit_hours, 2),
        'hourly_rate': hourly_rate,
        'estimated_deduction': estimated_deduction,
        'total_leaves_month': total_leaves_month,
        'total_leaves_year': total_leaves_year,
        'records': records,
        'leaves': leaves,
    }


def get_leave_statistics(user, year=None):
    """Detailed summary of leaves taken by category."""
    if year is None:
        year = timezone.localdate().year

    leaves = LeaveRecord.objects.filter(user=user, date__year=year).order_by('-date')
    
    breakdown = {}
    for code, label in LeaveRecord.LEAVE_CHOICES:
        breakdown[code] = {
            'label': label,
            'count': 0.0
        }

    total_days = 0.0
    for l in leaves:
        val = float(l.duration)
        total_days += val
        if l.leave_type in breakdown:
            breakdown[l.leave_type]['count'] += val

    # Filter breakdown to those with > 0 or all
    active_breakdown = {k: v for k, v in breakdown.items() if v['count'] > 0}

    return {
        'year': year,
        'total_days': total_days,
        'breakdown': breakdown,
        'active_breakdown': active_breakdown,
        'recent_leaves': leaves[:10],
    }


def get_chart_data_for_month(user, year, month):
    """Provides structured data for Chart.js to render daily hours against target."""
    records = AttendanceRecord.objects.filter(
        user=user,
        date__year=year,
        date__month=month,
        logout_time__isnull=False
    ).order_by('date')

    target_decimal = round(user.profile.daily_target_minutes / 60.0, 2)

    labels = []
    worked_hours = []
    background_colors = []
    border_colors = []

    for r in records:
        day_str = r.date.strftime("%d %b (%a)")
        labels.append(day_str)
        hours = r.worked_hours_decimal
        worked_hours.append(hours)
        if hours >= target_decimal:
            background_colors.append('rgba(40, 167, 69, 0.7)')  # Green
            border_colors.append('#28a745')
        else:
            background_colors.append('rgba(220, 53, 69, 0.7)')  # Red
            border_colors.append('#dc3545')

    return {
        'labels': labels,
        'worked_hours': worked_hours,
        'target_hours': [target_decimal] * len(labels),
        'background_colors': background_colors,
        'border_colors': border_colors,
        'target_decimal': target_decimal,
    }
