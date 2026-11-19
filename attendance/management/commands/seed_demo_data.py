from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import date, time, timedelta
from decimal import Decimal
import random

from attendance.models import AttendanceRecord, LeaveRecord


class Command(BaseCommand):
    help = "Seeds demo attendance and leave records for testing the dashboard"

    def handle(self, *args, **options):
        # Create or fetch demo user
        user, created = User.objects.get_or_create(username="demo", defaults={"email": "demo@example.com"})
        if created:
            user.set_password("demo123")
            user.save()
            self.stdout.write(self.style.SUCCESS("Created demo user: username='demo', password='demo123'"))
        else:
            self.stdout.write(self.style.NOTICE("Using existing user 'demo'"))

        # Configure profile
        profile = user.profile
        profile.target_hours = 9
        profile.target_minutes = 15
        profile.hourly_salary_rate = Decimal("450.00")
        profile.save()

        today = timezone.localdate()

        # Clean existing demo records for the current month
        AttendanceRecord.objects.filter(user=user, date__month=today.month, date__year=today.year).delete()
        LeaveRecord.objects.filter(user=user, date__month=today.month, date__year=today.year).delete()

        # Seed past 12 working days
        shift_samples = [
            (time(9, 25), time(19, 0), 20),   # 9h 15m (Exact)
            (time(9, 10), time(19, 0), 20),   # 9h 30m (+15m surplus)
            (time(9, 30), time(19, 15), 15),  # 9h 30m (+15m surplus)
            (time(9, 45), time(18, 45), 20),  # 8h 40m (-35m deficit)
            (time(9, 15), time(19, 0), 30),   # 9h 15m (Exact)
            (time(9, 0), time(19, 0), 30),    # 9h 30m (+15m surplus)
            (time(10, 0), time(18, 45), 15),  # 8h 30m (-45m deficit)
            (time(9, 20), time(19, 0), 25),   # 9h 15m (Exact)
            (time(9, 30), time(19, 30), 20),  # 9h 40m (+25m surplus)
        ]

        days_seeded = 0
        current_day = today - timedelta(days=1)
        sample_idx = 0

        while days_seeded < 10 and current_day.month == today.month:
            # Skip weekends (Saturday=5, Sunday=6)
            if current_day.weekday() < 5:
                # Every 6th business day mark as leave
                if days_seeded == 4:
                    LeaveRecord.objects.create(
                        user=user,
                        date=current_day,
                        leave_type="CASUAL",
                        duration=Decimal("1.0"),
                        reason="Family medical appointment"
                    )
                elif days_seeded == 7:
                    LeaveRecord.objects.create(
                        user=user,
                        date=current_day,
                        leave_type="HALF_DAY",
                        duration=Decimal("0.5"),
                        reason="Personal work in first half"
                    )
                else:
                    login_t, logout_t, brk = shift_samples[sample_idx % len(shift_samples)]
                    AttendanceRecord.objects.create(
                        user=user,
                        date=current_day,
                        login_time=login_t,
                        logout_time=logout_t,
                        break_minutes=brk,
                        notes="Regular office shift"
                    )
                    sample_idx += 1
                days_seeded += 1
            current_day -= timedelta(days=1)

        # For today: create an active clocked-in record so the user can test Punch Out
        now_time = timezone.localtime(timezone.now()).time().replace(microsecond=0)
        # Login 3 hours ago or at 9:30 AM
        today_login = time(9, 30)
        AttendanceRecord.objects.create(
            user=user,
            date=today,
            login_time=today_login,
            logout_time=None,  # In progress!
            break_minutes=15,
            notes="Today's shift"
        )

        self.stdout.write(self.style.SUCCESS("Successfully seeded demo attendance & leave data for user 'demo'!"))
