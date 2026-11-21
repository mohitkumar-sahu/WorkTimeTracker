from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import AttendanceRecord, LeaveRecord, UserProfile


class AttendanceForm(forms.ModelForm):
    class Meta:
        model = AttendanceRecord
        fields = ['date', 'login_time', 'logout_time', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'login_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'logout_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'notes': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Worked late for project release'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if not self.instance.pk and not self.initial.get('date'):
            self.initial['date'] = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        date = cleaned_data.get('date')
        user = self.user

        if user and date:
            qs = AttendanceRecord.objects.filter(user=user, date=date)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(f"An attendance entry already exists for {date}. Please edit the existing entry instead.")

        return cleaned_data


class LeaveForm(forms.ModelForm):
    duration = forms.ChoiceField(
        choices=[(1.0, 'Full Day (1.0)'), (0.5, 'Half Day (0.5)')],
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = LeaveRecord
        fields = ['date', 'leave_type', 'duration', 'reason']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'leave_type': forms.Select(attrs={'class': 'form-select'}),
            'reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Reason for leave (optional)'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        if not self.instance.pk and not self.initial.get('date'):
            self.initial['date'] = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        date = cleaned_data.get('date')
        user = self.user

        if user and date:
            qs = LeaveRecord.objects.filter(user=user, date=date)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError(f"A leave record already exists for {date}.")

        return cleaned_data


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['role', 'custom_role_title', 'target_hours', 'target_minutes', 'monthly_salary']
        widgets = {
            'role': forms.Select(attrs={'class': 'form-select', 'id': 'id_role'}),
            'custom_role_title': forms.TextInput(attrs={'class': 'form-control', 'id': 'id_custom_role_title', 'placeholder': 'e.g. DevOps Engineer'}),
            'target_hours': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_target_hours', 'min': 1, 'max': 24}),
            'target_minutes': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_target_minutes', 'min': 0, 'max': 59}),
            'monthly_salary': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_monthly_salary', 'step': '0.01', 'min': '0', 'placeholder': 'e.g. 50000'}),
        }
        labels = {
            'role': 'Job Role',
            'custom_role_title': 'Custom Role Title',
            'target_hours': 'Daily Target Hours',
            'target_minutes': 'Daily Target Minutes',
            'monthly_salary': 'Monthly Fixed Salary',
        }
        help_texts = {
            'role': 'Select your role (e.g. Developer, Tester, HR).',
            'target_hours': 'Working hours required per day.',
            'target_minutes': 'Working minutes required per day.',
            'monthly_salary': 'Used to calculate daily salary deduction (Monthly Salary ÷ Total Working Days in Month).',
        }


class UserRegistrationForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        label="Confirm Password"
    )

    class Meta:
        model = User
        fields = ['username', 'email']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password')
        p2 = cleaned_data.get('password_confirm')
        if p1 and p2 and p1 != p2:
            self.add_error('password_confirm', "Passwords do not match.")
        return cleaned_data
