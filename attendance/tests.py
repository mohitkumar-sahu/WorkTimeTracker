from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date, time, timedelta
from decimal import Decimal

from attendance.models import AttendanceRecord, LeaveRecord, UserProfile
from attendance.services import (
    calculate_weekly_summary,
    calculate_monthly_summary,
    get_leave_statistics,
    get_day_schedule_status,
    get_month_schedule_summary,
    format_minutes_to_hm,
)


class AttendanceCalculationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        # User profile should have default 9h 15m target
        self.assertEqual(self.user.profile.target_hours, 9)
        self.assertEqual(self.user.profile.target_minutes, 15)
        self.assertEqual(self.user.profile.daily_target_minutes, 555)

    def test_daily_attendance_exact_target(self):
        # 09:30 to 18:45 = 9h 15m (555 mins, break is included in shift)
        rec = AttendanceRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 2),
            login_time=time(9, 30),
            logout_time=time(18, 45)
        )
        self.assertEqual(rec.get_worked_minutes(), 555)
        self.assertEqual(rec.worked_hours_formatted, "9h 15m")
        self.assertEqual(rec.diff_minutes, 0)
        self.assertEqual(rec.status, "EXACT")
        # Expected logout time: 09:30 + 555 min (9h 15m) = 18:45
        self.assertEqual(rec.expected_logout_time, time(18, 45))

    def test_daily_attendance_surplus(self):
        # 09:00 to 18:30 = 9h 30m (570 mins) -> +15 mins surplus
        rec = AttendanceRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 3),
            login_time=time(9, 0),
            logout_time=time(18, 30)
        )
        self.assertEqual(rec.get_worked_minutes(), 570)
        self.assertEqual(rec.worked_hours_formatted, "9h 30m")
        self.assertEqual(rec.diff_minutes, 15)
        self.assertEqual(rec.diff_formatted, "+15m")
        self.assertEqual(rec.status, "SURPLUS")

    def test_daily_attendance_deficit(self):
        # 09:30 to 17:45 = 8h 15m (495 mins) -> -60 mins deficit
        rec = AttendanceRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 4),
            login_time=time(9, 30),
            logout_time=time(17, 45)
        )
        self.assertEqual(rec.get_worked_minutes(), 495)
        self.assertEqual(rec.worked_hours_formatted, "8h 15m")
        self.assertEqual(rec.diff_minutes, -60)
        self.assertEqual(rec.diff_formatted, "-1h 00m")
        self.assertEqual(rec.status, "DEFICIT")

    def test_weekly_summary_calculation(self):
        # Mon: 9h 30m (570m, +15m)
        AttendanceRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 2), # Monday
            login_time=time(9, 0),
            logout_time=time(18, 30)
        )
        # Tue: 9h 00m (540m, -15m)
        AttendanceRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 3), # Tuesday
            login_time=time(9, 30),
            logout_time=time(18, 30)
        )

        weekly = calculate_weekly_summary(self.user, date(2026, 11, 4))
        self.assertEqual(weekly['days_worked'], 2)
        self.assertEqual(weekly['total_minutes'], 1110)
        self.assertEqual(weekly['avg_minutes'], 555)  # 1110 / 2 = 555 (9h 15m)
        self.assertEqual(weekly['avg_hours_formatted'], "9h 15m")
        self.assertEqual(weekly['diff_minutes'], 0)
        self.assertTrue(weekly['is_surplus'])

    def test_monthly_summary_and_leave_integration(self):
        # Work 2 days in November 2026
        AttendanceRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 2),
            login_time=time(9, 30),
            logout_time=time(18, 45)  # 555m (9h 15m)
        )
        AttendanceRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 3),
            login_time=time(9, 30),
            logout_time=time(19, 0)   # 570m (9h 30m, +15m)
        )
        # Record 1 Casual Leave and 1 Half Day Leave
        LeaveRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 5),
            leave_type='CASUAL',
            duration=Decimal('1.0'),
            reason='Family event'
        )
        LeaveRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 6),
            leave_type='HALF_DAY',
            duration=Decimal('0.5'),
            reason='Doctor appointment'
        )

        monthly = calculate_monthly_summary(self.user, 2026, 11)
        self.assertEqual(monthly['days_worked'], 2)
        self.assertEqual(monthly['total_leaves_month'], 1.5)
        self.assertEqual(monthly['total_leaves_year'], 1.5)
        self.assertEqual(monthly['diff_minutes'], 15)  # +15m surplus
        self.assertTrue(monthly['is_surplus'])

        leave_stats = get_leave_statistics(self.user, 2026)
        self.assertEqual(leave_stats['total_days'], 1.5)
        self.assertEqual(leave_stats['breakdown']['CASUAL']['count'], 1.0)
        self.assertEqual(leave_stats['breakdown']['HALF_DAY']['count'], 0.5)

    def test_role_and_work_hours_customization(self):
        # 1. Default should be Developer (9h 15m)
        self.assertEqual(self.user.profile.role, 'DEVELOPER')
        self.assertEqual(self.user.profile.daily_target_formatted, '9h 15m')
        self.assertEqual(self.user.profile.display_role, 'Developer')

        # 2. Update to Tester (9h 00m)
        profile = self.user.profile
        profile.role = 'TESTER'
        profile.target_hours = 9
        profile.target_minutes = 0
        profile.save()
        self.assertEqual(profile.display_role, 'Tester')
        self.assertEqual(profile.daily_target_minutes, 540)
        self.assertEqual(profile.daily_target_formatted, '9h 0m')

        # 3. Update to HR (8h 30m)
        profile.role = 'HR'
        profile.target_hours = 8
        profile.target_minutes = 30
        profile.save()
        self.assertEqual(profile.display_role, 'HR')
        self.assertEqual(profile.daily_target_minutes, 510)
        self.assertEqual(profile.daily_target_formatted, '8h 30m')

        # 4. Update to Custom Role (e.g. DevOps Engineer - 8h 45m)
        profile.role = 'CUSTOM'
        profile.custom_role_title = 'DevOps Engineer'
        profile.target_hours = 8
        profile.target_minutes = 45
        profile.save()
        self.assertEqual(profile.display_role, 'DevOps Engineer')
        self.assertEqual(profile.daily_target_minutes, 525)
        self.assertEqual(profile.daily_target_formatted, '8h 45m')

    def test_startup_weekend_and_saturday_schedule(self):
        # In November 2026:
        # Sunday: Nov 1
        sun_status = get_day_schedule_status(self.user, date(2026, 11, 1))
        self.assertTrue(sun_status['is_sunday'])
        self.assertTrue(sun_status['is_off_day'])
        self.assertEqual(sun_status['label'], 'Sunday Off')

        # 1st Saturday: Nov 7 -> Working Saturday
        sat1_status = get_day_schedule_status(self.user, date(2026, 11, 7))
        self.assertTrue(sat1_status['is_saturday'])
        self.assertFalse(sat1_status['is_off_day'])
        self.assertEqual(sat1_status['label'], '1st Saturday (Working)')

        # 2nd Saturday: Nov 14 -> 2nd Sat Off (Company Off)
        sat2_status = get_day_schedule_status(self.user, date(2026, 11, 14))
        self.assertTrue(sat2_status['is_saturday'])
        self.assertTrue(sat2_status['is_off_day'])
        self.assertEqual(sat2_status['label'], '2nd Saturday Off')

        # 3rd Saturday: Nov 21 -> Working Saturday
        sat3_status = get_day_schedule_status(self.user, date(2026, 11, 21))
        self.assertTrue(sat3_status['is_saturday'])
        self.assertFalse(sat3_status['is_off_day'])

        # 4th Saturday: Nov 28 -> 4th Sat Off (Company Off)
        sat4_status = get_day_schedule_status(self.user, date(2026, 11, 28))
        self.assertTrue(sat4_status['is_saturday'])
        self.assertTrue(sat4_status['is_off_day'])
        self.assertEqual(sat4_status['label'], '4th Saturday Off')

        # Month schedule summary for Nov 2026: 30 days, 5 Sundays, 2 Off Sats, 2 Working Sats, 23 Expected Work Days
        month_summary = get_month_schedule_summary(self.user, 2026, 11)
        self.assertEqual(month_summary['total_days_in_month'], 30)
        self.assertEqual(month_summary['sundays_count'], 5)
        self.assertEqual(month_summary['off_saturdays_count'], 2)
        self.assertEqual(month_summary['working_saturdays_count'], 2)
        self.assertEqual(month_summary['expected_working_days'], 23)

    def test_web_views_render_successfully(self):
        self.client.login(username='testuser', password='password123')

        # 1. Dashboard
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Attendance & Hours Dashboard")
        self.assertContains(res, "9h 15m")

        # 2. History
        res = self.client.get('/history/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Attendance History & Logs")

        # 3. CSV Export
        res = self.client.get('/history/export-csv/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv')

        # 4. Leaves
        res = self.client.get('/leaves/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Leave Management & Balance")

        # 5. Profile Settings
        res = self.client.get('/settings/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Role & Work Time Settings")

        # 6. Quick Mark Leave from Home Page
        res = self.client.post('/quick-mark-leave/', {
            'date': '2026-11-21',
            'leave_type': 'SATURDAY_OFF',
            'duration': '1.0',
            'reason': 'Company announced Saturday Off'
        })
        self.assertEqual(res.status_code, 302)
        leave = LeaveRecord.objects.filter(user=self.user, date=date(2026, 11, 21)).first()
        self.assertIsNotNone(leave)
        self.assertEqual(leave.leave_type, 'SATURDAY_OFF')

        # 7. Quick Cancel Leave from Home Page
        res = self.client.get(f'/quick-cancel-leave/{leave.pk}/')
        self.assertEqual(res.status_code, 302)
        self.assertFalse(LeaveRecord.objects.filter(pk=leave.pk).exists())

    def test_calendar_search_filter_bounded_by_signup_and_current_month(self):
        # Create a user who joined in November 2026
        u = User.objects.create_user(username='newjoiner', password='password123')
        self.client.login(username='newjoiner', password='password123')

        res = self.client.get('/history/')
        self.assertEqual(res.status_code, 200)

        # Context years should only contain 2026
        years = res.context['years']
        self.assertEqual(years, [2026])

        # Context months should only contain November (since user joined in Nov 2026 and current month is Nov 2026)
        months = res.context['months']
        month_numbers = [m[0] for m in months]
        self.assertEqual(month_numbers, [11])  # Only November!
        self.assertNotIn(12, month_numbers)    # No December
        self.assertNotIn(10, month_numbers)    # No October

    def test_profile_settings_username_and_password_reset(self):
        self.client.login(username='testuser', password='password123')
        
        # 1. Check User ID is removed and Username is rendered
        res = self.client.get('/settings/')
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, f"User ID: #{self.user.id}")
        self.assertContains(res, "Reset Password")
        self.assertContains(res, self.user.username)

        # 2. Update Username
        res = self.client.post('/settings/', {
            'action': 'update_username',
            'username': 'updated_worker',
        })
        self.assertEqual(res.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'updated_worker')

        # 3. Change / Reset Password
        res = self.client.post('/settings/', {
            'action': 'change_password',
            'old_password': 'password123',
            'new_password1': 'NewSecretPass123!',
            'new_password2': 'NewSecretPass123!',
        })
        self.assertEqual(res.status_code, 302)
        # Verify new password authenticates
        self.client.logout()
        login_success = self.client.login(username='updated_worker', password='NewSecretPass123!')
        self.assertTrue(login_success)

    def test_mark_festival_paid_leave_from_history(self):
        self.client.login(username='testuser', password='password123')

        # 1. Open History page, check Mark Festival / Paid Leave button exists
        res = self.client.get('/history/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Mark Festival / Paid Leave")
        self.assertContains(res, "markPaidLeaveModal")

        # 2. Mark a Manager-Announced Paid Leave (e.g. Diwali Festival)
        res = self.client.post('/quick-mark-leave/', {
            'date': '2026-11-12',
            'leave_type': 'PAID',
            'duration': '1.0',
            'reason': 'Diwali Festival (Manager Announced)',
            'next': 'history'
        })
        self.assertEqual(res.status_code, 302)
        self.assertIn('/history/?year=2026&month=11', res.url)

        # 3. Verify LeaveRecord exists in database
        leave = LeaveRecord.objects.filter(user=self.user, date=date(2026, 11, 12)).first()
        self.assertIsNotNone(leave)
        self.assertEqual(leave.leave_type, 'PAID')
        self.assertEqual(leave.reason, 'Diwali Festival (Manager Announced)')

        # 4. Verify History page renders the Paid Festival Leave in banner and table
        res = self.client.get('/history/?year=2026&month=11')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Diwali Festival (Manager Announced)")
        self.assertContains(res, "Paid Festival Leaves & Holidays")

    def test_monthly_salary_setting_and_daily_deduction_calculation(self):
        self.client.login(username='testuser', password='password123')

        # 1. Update Monthly Salary to 46,000 in Settings
        res = self.client.post('/settings/', {
            'action': 'update_profile',
            'role': 'DEVELOPER',
            'target_hours': 9,
            'target_minutes': 15,
            'monthly_salary': '46000.00',
        })
        self.assertEqual(res.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.monthly_salary, Decimal('46000.00'))

        # 2. In November 2026: 30 days - 5 Sundays - 2 off Saturdays = 23 working days
        # 1 day salary = 46000 / 23 = 2000.00 / day
        # Daily target is 9h 15m = 9.25 hours
        # Hourly rate = 2000 / 9.25 = ~216.22 / hr
        salary_info = self.user.profile.get_salary_deduction_info(2026, 11)
        self.assertEqual(salary_info['working_days'], 23)
        self.assertEqual(salary_info['per_day_salary'], Decimal('2000.00'))
        self.assertEqual(salary_info['per_hour_salary'], Decimal('216.22'))

        # 3. Monthly summary reflects 1-day rate and hourly rate on settings page
        res = self.client.get('/settings/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "1-Day Salary Rate")
        self.assertContains(res, "2000.00")
        self.assertContains(res, "Hourly Deduction Rate")
        self.assertContains(res, "216.22")

        # 4. History page shows salary rate and hourly rate insight
        res = self.client.get('/history/?year=2026&month=11')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Day Rate: ₹2000.00/d")
        self.assertContains(res, "Hourly Rate: ₹216.22/hr")

    def test_exact_1_hour_missed_salary_deduction(self):
        self.client.login(username='testuser', password='password123')

        # Set user's monthly salary to 46,000 (hourly rate: 216.22)
        self.user.profile.monthly_salary = Decimal('46000.00')
        self.user.profile.save()

        # In November 2026: Working day Nov 2
        # Target is 9h 15m (09:30 to 18:45 = 555 mins)
        # If user logs out 1 hour early at 17:45 (worked 8h 15m = 495 mins -> missed 60 mins / 1 hour)
        AttendanceRecord.objects.create(
            user=self.user,
            date=date(2026, 11, 2),
            login_time=time(9, 30),
            logout_time=time(17, 45)
        )

        # Calculate monthly summary
        summary = calculate_monthly_summary(self.user, 2026, 11)
        self.assertEqual(summary['diff_minutes'], -60)  # Exactly 1 hour missed!
        self.assertEqual(summary['diff_formatted'], "-1h 00m")
        self.assertEqual(summary['per_hour_salary'], Decimal('216.22'))
        # 1 hour * 216.22 = 216.22 deduction
        self.assertEqual(summary['salary_deduction_deficit'], Decimal('216.22'))
        self.assertEqual(summary['estimated_payable_salary'], Decimal('45783.78'))

        # Check that dashboard renders the hourly cut
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Hourly Deduction Risk")
        self.assertContains(res, "216.22")
        self.assertContains(res, "Est. Cut:")

        # Check that history logs render the hourly cut in monthly summary and time only in table
        res = self.client.get('/history/?year=2026&month=11')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Hourly Cut: -₹216.22")
        self.assertContains(res, "-1h 00m")
        self.assertNotContains(res, "-₹216.22 cut")
