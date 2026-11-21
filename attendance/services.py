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


def get_day_schedule_status(user, target_date):
    """
    Returns day schedule details based on startup weekend policy:
    - Sundays are always fixed weekly off.
    - 2 Saturdays per month are off (2nd & 4th Saturday by default).
    - Remaining Saturdays and Monday-Friday are working days.
    """
    weekday = target_date.weekday()
    ordinal_names = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th"}

    if weekday == 6:  # Sunday
        return {
            'is_sunday': True,
            'is_saturday': False,
            'is_off_day': True,
            'label': 'Sunday Off',
            'short_badge': 'Sunday Off',
            'badge_class': 'badge bg-secondary text-white',
            'is_regular_working_day': False,
        }
    elif weekday == 5:  # Saturday
        ordinal = (target_date.day - 1) // 7 + 1
        ordinal_str = ordinal_names.get(ordinal, f"{ordinal}th")
        policy = getattr(user.profile, 'saturday_off_policy', 'SECOND_FOURTH')

        is_off = False
        if policy == 'SECOND_FOURTH' and ordinal in (2, 4):
            is_off = True
        elif policy == 'FIRST_THIRD' and ordinal in (1, 3):
            is_off = True
        elif policy == 'ALL_SATURDAYS':
            is_off = True
        elif policy == 'NO_SATURDAYS':
            is_off = False
        elif policy == 'FLEXIBLE_TWO':
            # Check if recorded as SATURDAY_OFF in leaves
            is_off = LeaveRecord.objects.filter(user=user, date=target_date, leave_type='SATURDAY_OFF').exists()

        if is_off:
            return {
                'is_sunday': False,
                'is_saturday': True,
                'saturday_ordinal': ordinal,
                'is_off_day': True,
                'label': f'{ordinal_str} Saturday Off',
                'short_badge': f'{ordinal_str} Sat Off',
                'badge_class': 'badge bg-info bg-opacity-25 text-info-emphasis border border-info',
                'is_regular_working_day': False,
            }
        else:
            return {
                'is_sunday': False,
                'is_saturday': True,
                'saturday_ordinal': ordinal,
                'is_off_day': False,
                'label': f'{ordinal_str} Saturday (Working)',
                'short_badge': f'{ordinal_str} Sat (Work)',
                'badge_class': 'badge bg-warning bg-opacity-25 text-dark border border-warning',
                'is_regular_working_day': True,
            }
    else:
        return {
            'is_sunday': False,
            'is_saturday': False,
            'is_off_day': False,
            'label': 'Working Day',
            'short_badge': 'Working Day',
            'badge_class': 'badge bg-light text-dark border',
            'is_regular_working_day': True,
        }


def get_month_schedule_summary(user, year, month):
    """
    Calculates expected working days, total Sundays, and the 2 off-Saturdays for the month.
    """
    total_days = calendar.monthrange(year, month)[1]
    sundays = []
    off_saturdays = []
    working_saturdays = []
    weekdays_count = 0

    for day in range(1, total_days + 1):
        d = date(year, month, day)
        status = get_day_schedule_status(user, d)
        if status['is_sunday']:
            sundays.append(d)
        elif status['is_saturday']:
            if status['is_off_day']:
                off_saturdays.append(d)
            else:
                working_saturdays.append(d)
        else:
            weekdays_count += 1

    expected_working_days = weekdays_count + len(working_saturdays)

    return {
        'total_days_in_month': total_days,
        'expected_working_days': expected_working_days,
        'sundays_count': len(sundays),
        'off_saturdays_count': len(off_saturdays),
        'working_saturdays_count': len(working_saturdays),
        'off_saturdays': off_saturdays,
        'working_saturdays': working_saturdays,
    }


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

    # Week Saturday schedule status
    saturday_date = monday + timedelta(days=5)
    saturday_status = get_day_schedule_status(user, saturday_date)

    return {
        'monday': monday,
        'sunday': sunday,
        'saturday_date': saturday_date,
        'saturday_status': saturday_status,
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

    for r in records:
        r.schedule_status = get_day_schedule_status(user, r.date)

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

    deficit_hours = max(0, -diff_minutes) / 60.0
    month_name = calendar.month_name[month]

    # Get startup schedule breakdown for this month
    schedule_summary = get_month_schedule_summary(user, year, month)

    # Salary and Deduction calculations:
    # 1 day salary = Monthly Salary / Total Working Days in Month
    salary_info = user.profile.get_salary_deduction_info(year, month)
    per_day_salary = salary_info['per_day_salary']
    per_hour_salary = salary_info['per_hour_salary']

    # Unpaid leaves in this month
    unpaid_leaves = leaves.filter(leave_type='UNPAID')
    unpaid_leave_days = float(sum(l.duration for l in unpaid_leaves)) if unpaid_leaves.exists() else 0.0

    # Deficit salary deduction (if negative diff):
    if diff_minutes < 0 and per_hour_salary > 0:
        deficit_mins = -diff_minutes
        salary_deduction_deficit = round((Decimal(deficit_mins) / Decimal(60)) * per_hour_salary, 2)
    else:
        salary_deduction_deficit = Decimal('0.00')

    unpaid_salary_deduction = round(Decimal(str(unpaid_leave_days)) * per_day_salary, 2)
    total_salary_deduction = salary_deduction_deficit + unpaid_salary_deduction

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
        'total_leaves_month': total_leaves_month,
        'total_leaves_year': total_leaves_year,
        'schedule_summary': schedule_summary,
        'salary_info': salary_info,
        'per_day_salary': per_day_salary,
        'per_hour_salary': per_hour_salary,
        'unpaid_leave_days': unpaid_leave_days,
        'salary_deduction_deficit': salary_deduction_deficit,
        'unpaid_salary_deduction': unpaid_salary_deduction,
        'total_salary_deduction': total_salary_deduction,
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
