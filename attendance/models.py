from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import datetime, date, timedelta
from decimal import Decimal


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('DEVELOPER', 'Developer'),
        ('TESTER', 'Tester'),
        ('HR', 'HR'),
        ('DESIGNER', 'UI/UX Designer'),
        ('MANAGER', 'Project Manager'),
        ('CUSTOM', 'Custom Role'),
    ]

    SATURDAY_POLICY_CHOICES = [
        ('SECOND_FOURTH', '2nd & 4th Saturday Off (2 Saturdays/month)'),
        ('FIRST_THIRD', '1st & 3rd Saturday Off (2 Saturdays/month)'),
        ('FLEXIBLE_TWO', 'Flexible 2 Saturdays Off (mark in Leaves)'),
        ('ALL_SATURDAYS', 'All Saturdays Off (5-day work week)'),
        ('NO_SATURDAYS', 'All Saturdays Working (6-day work week)'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default='DEVELOPER', help_text="Select your role")
    custom_role_title = models.CharField(max_length=50, blank=True, help_text="Custom role name")
    target_hours = models.PositiveIntegerField(default=9, help_text="Daily required working hours")
    target_minutes = models.PositiveIntegerField(default=15, help_text="Daily required working minutes")
    saturday_off_policy = models.CharField(
        max_length=20, choices=SATURDAY_POLICY_CHOICES, default='SECOND_FOURTH',
        help_text="Company policy for Saturday off (All Sundays are fixed off)"
    )
    hourly_salary_rate = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('0.00'), blank=True,
        help_text="Optional hourly rate"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} ({self.display_role} - {self.daily_target_formatted})"

    @property
    def display_role(self):
        if self.role == 'CUSTOM' and self.custom_role_title:
            return self.custom_role_title
        return self.get_role_display()

    @property
    def daily_target_minutes(self):
        return (self.target_hours * 60) + self.target_minutes

    @property
    def daily_target_formatted(self):
        return f"{self.target_hours}h {self.target_minutes}m"


@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)
    else:
        if hasattr(instance, 'profile'):
            instance.profile.save()
        else:
            UserProfile.objects.create(user=instance)


class AttendanceRecord(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendances')
    date = models.DateField(default=timezone.localdate)
    login_time = models.TimeField(help_text="Punch in / Login time")
    logout_time = models.TimeField(null=True, blank=True, help_text="Punch out / Logout time (leave empty if ongoing)")
    break_minutes = models.PositiveIntegerField(default=0, help_text="Break time in minutes (e.g. lunch/tea)")
    notes = models.CharField(max_length=255, blank=True, help_text="Optional note")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        unique_together = ('user', 'date')

    def __str__(self):
        return f"{self.user.username} - {self.date} ({self.login_time} - {self.logout_time or 'In Progress'})"

    @property
    def target_minutes(self):
        if hasattr(self.user, 'profile'):
            return self.user.profile.daily_target_minutes
        return 9 * 60 + 15

    def get_worked_minutes(self):
        """Returns total worked minutes (login to logout duration), or None if logout_time is not set."""
        if not self.logout_time:
            return None
        
        login_dt = datetime.combine(self.date, self.login_time)
        logout_dt = datetime.combine(self.date, self.logout_time)

        # Handle overnight shifts where logout is next day
        if self.logout_time < self.login_time:
            logout_dt += timedelta(days=1)

        diff = logout_dt - login_dt
        return max(0, int(diff.total_seconds() // 60))

    def get_current_elapsed_minutes(self):
        """For ongoing shift today, calculate minutes elapsed until right now."""
        if self.logout_time:
            return self.get_worked_minutes()
        
        now = timezone.localtime(timezone.now())
        if self.date == now.date():
            login_dt = timezone.make_aware(datetime.combine(self.date, self.login_time))
            if now > login_dt:
                diff = now - login_dt
                return max(0, int(diff.total_seconds() // 60))
        return 0

    @property
    def worked_hours_formatted(self):
        mins = self.get_worked_minutes()
        if mins is None:
            return "In Progress"
        h = mins // 60
        m = mins % 60
        return f"{h}h {m:02d}m"

    @property
    def worked_hours_decimal(self):
        mins = self.get_worked_minutes()
        if mins is None:
            return 0.0
        return round(mins / 60.0, 2)

    @property
    def diff_minutes(self):
        mins = self.get_worked_minutes()
        if mins is None:
            return None
        return mins - self.target_minutes

    @property
    def diff_formatted(self):
        diff = self.diff_minutes
        if diff is None:
            return "In Progress"
        if diff == 0:
            return "Exact (0m)"
        sign = "+" if diff > 0 else "-"
        abs_diff = abs(diff)
        h = abs_diff // 60
        m = abs_diff % 60
        if h > 0:
            return f"{sign}{h}h {m:02d}m"
        return f"{sign}{m}m"

    @property
    def status(self):
        if not self.logout_time:
            return "IN_PROGRESS"
        diff = self.diff_minutes
        if diff > 0:
            return "SURPLUS"
        elif diff < 0:
            return "DEFICIT"
        return "EXACT"

    @property
    def expected_logout_time(self):
        """Calculates what time the user must log out to hit their required target (e.g. 9h 15m)."""
        target_mins = self.target_minutes
        login_dt = datetime.combine(self.date, self.login_time)
        required_logout_dt = login_dt + timedelta(minutes=target_mins)
        return required_logout_dt.time()


class LeaveRecord(models.Model):
    LEAVE_CHOICES = [
        ('LEAVE', 'Personal Leave / Day Off'),
        ('SICK', 'Sick Leave'),
        ('HALF_DAY', 'Half Day Leave'),
        ('SATURDAY_OFF', 'Company Saturday Off (2/month)'),
        ('HOLIDAY', 'Company / Public Holiday'),
        ('UNPAID', 'Unpaid Leave / LWP'),
        ('CASUAL', 'Casual Leave'),
        ('OTHER', 'Other'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='leaves')
    date = models.DateField(default=timezone.localdate)
    leave_type = models.CharField(max_length=20, choices=LEAVE_CHOICES, default='LEAVE')
    duration = models.DecimalField(
        max_digits=3, decimal_places=1, default=Decimal('1.0'),
        help_text="1.0 for Full Day, 0.5 for Half Day"
    )
    reason = models.TextField(blank=True, help_text="Reason for leave")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        unique_together = ('user', 'date')

    def __str__(self):
        return f"{self.user.username} - {self.get_leave_type_display()} on {self.date} ({self.duration} day)"
