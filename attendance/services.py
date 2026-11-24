from datetime import date, datetime, timedelta, time
import calendar
import math
from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum
from .models import AttendanceRecord, LeaveRecord


def format_minutes_to_hm(minutes, show_sign=False):
    """Converts minutes to formatted string like '9h 15m' or '+1h 20m' or '35m'."""
    if minutes is None:
        return "-"
    
    is_negative = minutes < 0
    abs_mins = int(round(abs(minutes)))
    h = abs_mins // 60
    m = abs_mins % 60

    if h == 0 and abs_mins > 0:
        time_str = f"{m}m"
    elif h == 0 and abs_mins == 0:
        time_str = "0m"
    else:
        time_str = f"{h}h {m:02d}m"

    if show_sign:
        if is_negative:
            return f"-{time_str}"
        elif abs_mins == 0:
            return "0m"
        else:
            return f"+{time_str}"
    else:
        return time_str


def get_day_schedule_status(user, target_date, policy=None):
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
        if policy is None:
            profile = getattr(user, 'profile', None)
            policy = getattr(profile, 'saturday_off_policy', 'SECOND_FOURTH') if profile else 'SECOND_FOURTH'

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


def get_month_schedule_summary(user, year, month, policy=None):
    """
    Calculates expected working days, total Sundays, and the 2 off-Saturdays for the month.
    """
    total_days = calendar.monthrange(year, month)[1]
    sundays = []
    off_saturdays = []
    working_saturdays = []
    weekdays_count = 0

    if policy is None:
        profile = getattr(user, 'profile', None)
        policy = getattr(profile, 'saturday_off_policy', 'SECOND_FOURTH') if profile else 'SECOND_FOURTH'

    # Pre-fetch flexible off dates if needed so no query inside the loop
    flexible_offs = set()
    if policy == 'FLEXIBLE_TWO':
        flexible_offs = set(LeaveRecord.objects.filter(
            user=user,
            date__year=year,
            date__month=month,
            leave_type='SATURDAY_OFF'
        ).values_list('date', flat=True))

    for day in range(1, total_days + 1):
        d = date(year, month, day)
        if policy == 'FLEXIBLE_TWO' and d.weekday() == 5:
            is_off = d in flexible_offs
            status = {
                'is_sunday': False,
                'is_saturday': True,
                'is_off_day': is_off,
            }
        else:
            status = get_day_schedule_status(user, d, policy=policy)

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
    ).select_related('user__profile').order_by('date')

    target_per_day = user.profile.daily_target_minutes

    completed_records = [r for r in records if r.logout_time is not None]
    working_records = [
        r for r in completed_records
        if not (r.date.weekday() == 6 and (r.get_worked_minutes() or 0) == 0)
    ]
    days_worked = len(working_records)
    total_minutes = sum(r.get_worked_minutes() for r in completed_records)

    leaves = LeaveRecord.objects.filter(
        user=user,
        date__range=[monday, sunday]
    )
    total_leaves = float(sum(l.duration for l in leaves))

    if days_worked > 0:
        avg_minutes = total_minutes / days_worked
        total_target_minutes = sum(r.target_minutes for r in working_records)
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
    ).select_related('user__profile').order_by('date')

    for r in records:
        r.schedule_status = get_day_schedule_status(user, r.date)

    completed_records = [r for r in records if r.logout_time is not None]
    working_records = [
        r for r in completed_records
        if not (r.date.weekday() == 6 and (r.get_worked_minutes() or 0) == 0)
    ]
    days_worked = len(working_records)
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
        total_target_minutes = sum(r.target_minutes for r in working_records)
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

    missed_minutes = max(0, -diff_minutes)
    missed_hours_formatted = format_minutes_to_hm(missed_minutes)
    missed_hours_decimal = round(missed_minutes / 60.0, 2)
    per_minute_salary = salary_info.get('per_minute_salary', Decimal('0.00'))

    if user.profile.monthly_salary > Decimal('0.00'):
        estimated_payable_salary = max(Decimal('0.00'), user.profile.monthly_salary - total_salary_deduction)
    else:
        estimated_payable_salary = Decimal('0.00')

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
        'missed_minutes': missed_minutes,
        'missed_hours_formatted': missed_hours_formatted,
        'missed_hours_decimal': missed_hours_decimal,
        'total_leaves_month': total_leaves_month,
        'total_leaves_year': total_leaves_year,
        'schedule_summary': schedule_summary,
        'salary_info': salary_info,
        'per_day_salary': per_day_salary,
        'per_hour_salary': per_hour_salary,
        'per_minute_salary': per_minute_salary,
        'unpaid_leave_days': unpaid_leave_days,
        'salary_deduction_deficit': salary_deduction_deficit,
        'unpaid_salary_deduction': unpaid_salary_deduction,
        'total_salary_deduction': total_salary_deduction,
        'estimated_payable_salary': estimated_payable_salary,
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


def get_chart_data_for_month(user, year, month, records=None):
    """Provides structured data for Chart.js to render daily hours against target."""
    if records is None:
        records = AttendanceRecord.objects.filter(
            user=user,
            date__year=year,
            date__month=month,
            logout_time__isnull=False
        ).select_related('user__profile').order_by('date')
    
    # Exclude 0-minute accidental Sunday records
    records = [
        r for r in records
        if r.logout_time is not None and not (r.date.weekday() == 6 and (r.get_worked_minutes() or 0) == 0)
    ]

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


def get_next_working_day(user, from_date=None, policy=None):
    """Finds the next official working day for the user, skipping Sundays, off-Saturdays, and recorded leaves."""
    if from_date is None:
        from_date = timezone.localdate()
    if policy is None:
        profile = getattr(user, 'profile', None)
        policy = getattr(profile, 'saturday_off_policy', 'SECOND_FOURTH') if profile else 'SECOND_FOURTH'

    end_date = from_date + timedelta(days=15)
    leave_dates = set(LeaveRecord.objects.filter(
        user=user,
        date__gt=from_date,
        date__lte=end_date,
        leave_type__in=('SATURDAY_OFF', 'HOLIDAY', 'LEAVE', 'PAID', 'SICK')
    ).values_list('date', flat=True))

    curr = from_date + timedelta(days=1)
    for _ in range(14):  # Look up to 2 weeks ahead
        status = get_day_schedule_status(user, curr, policy=policy)
        if not status['is_off_day'] and curr not in leave_dates:
            return curr
        curr += timedelta(days=1)
    return from_date + timedelta(days=1)


def get_remaining_working_days_in_month(user, from_date=None, policy=None):
    """Calculates remaining official working days in the month strictly after from_date."""
    if from_date is None:
        from_date = timezone.localdate()
    if policy is None:
        profile = getattr(user, 'profile', None)
        policy = getattr(profile, 'saturday_off_policy', 'SECOND_FOURTH') if profile else 'SECOND_FOURTH'
    year = from_date.year
    month = from_date.month
    total_days = calendar.monthrange(year, month)[1]

    last_date = date(year, month, total_days)
    if from_date >= last_date:
        return 0

    leave_dates = set(LeaveRecord.objects.filter(
        user=user,
        date__gt=from_date,
        date__lte=last_date,
        leave_type__in=('SATURDAY_OFF', 'HOLIDAY', 'LEAVE', 'PAID', 'SICK')
    ).values_list('date', flat=True))

    count = 0
    for day in range(from_date.day + 1, total_days + 1):
        d = date(year, month, day)
        status = get_day_schedule_status(user, d, policy=policy)
        if not status['is_off_day'] and d not in leave_dates:
            count += 1
    return count


def calculate_flexi_recovery_plan(user, target_date=None, today_record=None, monthly_summary=None):
    """
    Computes actionable compensatory work time plan for startups with flexible hours:
    - If user leaves early today (diff_minutes < 0), calculates tomorrow's extended target shift.
    - If user has past accumulated deficit and is clocked in today, calculates recovery punch-out time.
    - If user has banked surplus buffer, calculates safe early departure punch-out time.
    - Provides single-day next shift recovery and gradual spread over remaining working days.
    """
    if target_date is None:
        target_date = timezone.localdate()

    daily_target_minutes = user.profile.daily_target_minutes
    daily_target_formatted = user.profile.daily_target_formatted

    if today_record is None:
        today_record = AttendanceRecord.objects.filter(user=user, date=target_date).first()

    if monthly_summary is None:
        monthly_summary = calculate_monthly_summary(user, target_date.year, target_date.month)

    next_working_day = get_next_working_day(user, target_date)
    remaining_working_days = get_remaining_working_days_in_month(user, target_date)

    today_shift_completed = False
    today_left_early = False
    today_worked_minutes = 0
    today_worked_formatted = "-"
    today_diff_minutes = 0
    today_deficit_minutes = 0
    today_deficit_formatted = "0m"
    today_surplus_minutes = 0
    today_surplus_formatted = "0m"

    is_shift_in_progress = False
    login_time = None
    standard_logout_time = None
    recovery_logout_time = None
    safe_early_logout_time = None

    if today_record:
        if today_record.logout_time:
            today_shift_completed = True
            today_worked_minutes = today_record.get_worked_minutes() or 0
            today_worked_formatted = today_record.worked_hours_formatted
            today_diff_minutes = today_record.diff_minutes or 0
            if today_diff_minutes < 0:
                today_left_early = True
                today_deficit_minutes = abs(today_diff_minutes)
                today_deficit_formatted = format_minutes_to_hm(today_deficit_minutes)
            elif today_diff_minutes > 0:
                today_surplus_minutes = today_diff_minutes
                today_surplus_formatted = format_minutes_to_hm(today_surplus_minutes)
        else:
            is_shift_in_progress = True
            login_time = today_record.login_time
            standard_logout_time = today_record.expected_logout_time

    # Prior deficit / surplus in current month before target_date
    if 'records' in monthly_summary:
        prior_records = [r for r in monthly_summary['records'] if r.date < target_date and r.logout_time is not None]
    else:
        prior_records = AttendanceRecord.objects.filter(
            user=user,
            date__year=target_date.year,
            date__month=target_date.month,
            date__lt=target_date,
            logout_time__isnull=False
        )
    prior_working_records = [
        r for r in prior_records
        if not (r.date.weekday() == 6 and (r.get_worked_minutes() or 0) == 0)
    ]
    prior_days_worked = len(prior_working_records)
    prior_worked_minutes = sum(r.get_worked_minutes() for r in prior_records)
    prior_target_minutes = sum(r.target_minutes for r in prior_working_records)
    prior_diff_minutes = prior_worked_minutes - prior_target_minutes if prior_days_worked > 0 else 0
    prior_deficit_minutes = abs(prior_diff_minutes) if prior_diff_minutes < 0 else 0
    prior_surplus_minutes = prior_diff_minutes if prior_diff_minutes > 0 else 0

    # Net month balance
    net_monthly_diff = monthly_summary['diff_minutes']
    has_monthly_deficit = net_monthly_diff < 0
    net_deficit_minutes = abs(net_monthly_diff) if has_monthly_deficit else 0
    net_deficit_formatted = format_minutes_to_hm(net_deficit_minutes)
    net_surplus_minutes = net_monthly_diff if net_monthly_diff > 0 else 0
    net_surplus_formatted = format_minutes_to_hm(net_surplus_minutes)

    # In-progress shift recovery / safe departure
    if is_shift_in_progress and login_time:
        login_dt = datetime.combine(target_date, login_time)
        if prior_deficit_minutes > 0:
            rec_dt = login_dt + timedelta(minutes=daily_target_minutes + prior_deficit_minutes)
            recovery_logout_time = rec_dt.time()
        elif prior_surplus_minutes > 0:
            safe_mins = max(0, daily_target_minutes - prior_surplus_minutes)
            safe_dt = login_dt + timedelta(minutes=safe_mins)
            safe_early_logout_time = safe_dt.time()

    # Recovery deficit targeting next working day:
    # If today user left early, prioritize recovering today's early leave deficit
    # Otherwise if month has net deficit, recover that
    if today_left_early:
        deficit_to_recover = today_deficit_minutes
    elif net_deficit_minutes > 0:
        deficit_to_recover = net_deficit_minutes
    else:
        deficit_to_recover = 0

    next_day_target_minutes = daily_target_minutes + deficit_to_recover
    next_day_target_formatted = format_minutes_to_hm(next_day_target_minutes)

    # Next working day sample punch in at 09:30 AM (or user's today's punch in if available)
    sample_login_time = login_time if login_time else time(9, 30)
    sample_login_dt = datetime.combine(next_working_day, sample_login_time)
    next_day_expected_logout_dt = sample_login_dt + timedelta(minutes=next_day_target_minutes)
    next_day_expected_logout_time = next_day_expected_logout_dt.time()

    # Gradual spread over remaining days of month
    spread_extra_per_day = 0
    spread_daily_target_minutes = daily_target_minutes
    spread_daily_target_formatted = daily_target_formatted
    if remaining_working_days > 0 and deficit_to_recover > 0:
        spread_extra_per_day = math.ceil(deficit_to_recover / remaining_working_days)
        spread_daily_target_minutes = daily_target_minutes + spread_extra_per_day
        spread_daily_target_formatted = format_minutes_to_hm(spread_daily_target_minutes)

    return {
        'target_date': target_date,
        'daily_target_minutes': daily_target_minutes,
        'daily_target_formatted': daily_target_formatted,
        'today_shift_completed': today_shift_completed,
        'today_left_early': today_left_early,
        'today_worked_minutes': today_worked_minutes,
        'today_worked_formatted': today_worked_formatted,
        'today_diff_minutes': today_diff_minutes,
        'today_deficit_minutes': today_deficit_minutes,
        'today_deficit_formatted': today_deficit_formatted,
        'today_surplus_minutes': today_surplus_minutes,
        'today_surplus_formatted': today_surplus_formatted,
        'is_shift_in_progress': is_shift_in_progress,
        'login_time': login_time,
        'standard_logout_time': standard_logout_time,
        'recovery_logout_time': recovery_logout_time,
        'safe_early_logout_time': safe_early_logout_time,
        'prior_deficit_minutes': prior_deficit_minutes,
        'prior_deficit_formatted': format_minutes_to_hm(prior_deficit_minutes),
        'prior_surplus_minutes': prior_surplus_minutes,
        'prior_surplus_formatted': format_minutes_to_hm(prior_surplus_minutes),
        'has_monthly_deficit': has_monthly_deficit,
        'net_deficit_minutes': net_deficit_minutes,
        'net_deficit_formatted': net_deficit_formatted,
        'net_surplus_minutes': net_surplus_minutes,
        'net_surplus_formatted': net_surplus_formatted,
        'deficit_to_recover': deficit_to_recover,
        'deficit_to_recover_formatted': format_minutes_to_hm(deficit_to_recover),
        'next_working_day': next_working_day,
        'next_working_day_name': next_working_day.strftime("%A, %b %d"),
        'next_day_target_minutes': next_day_target_minutes,
        'next_day_target_formatted': next_day_target_formatted,
        'sample_login_time': sample_login_time,
        'next_day_expected_logout_time': next_day_expected_logout_time,
        'remaining_working_days': remaining_working_days,
        'spread_extra_per_day': spread_extra_per_day,
        'spread_daily_target_minutes': spread_daily_target_minutes,
        'spread_daily_target_formatted': spread_daily_target_formatted,
    }
