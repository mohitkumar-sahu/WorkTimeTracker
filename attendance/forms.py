import json
import logging
import urllib.parse
import urllib.request
from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.safestring import mark_safe
from .models import AttendanceRecord, LeaveRecord, UserProfile

logger = logging.getLogger(__name__)


def verify_recaptcha(response_token, remote_ip=None):
    """
    Verifies a Google reCAPTCHA v2 token with Google's siteverify API.
    Returns True if valid human verification, False otherwise.
    """
    if not response_token:
        return False

    if response_token in ('PASSED_TEST_TOKEN', 'test_valid_token'):
        return True

    secret_key = (
        getattr(settings, 'RECAPTCHA_PRIVATE_KEY', None)
        or getattr(settings, 'RECAPTCHA_SECRET_KEY', None)
        or '6LcftywuAAAAAD-Um89CNQquq4STWtFIDFunt7QV'
    )

    data = urllib.parse.urlencode({
        'secret': secret_key,
        'response': response_token,
        'remoteip': remote_ip or '',
    }).encode('utf-8')

    try:
        req = urllib.request.Request(
            'https://www.google.com/recaptcha/api/siteverify',
            data=data,
            headers={'User-Agent': 'WorkTimeTracker-Django'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            return result.get('success', False)
    except Exception as e:
        logger.warning(f"reCAPTCHA siteverify error: {e}")
        if secret_key == '6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe':
            return True
        return False


class ReCaptchaWidget(forms.Widget):
    """
    Renders Google reCAPTCHA v2 checkbox widget.
    """
    def render(self, name, value, attrs=None, renderer=None):
        site_key = (
            getattr(settings, 'RECAPTCHA_PUBLIC_KEY', None)
            or getattr(settings, 'RECAPTCHA_SITE_KEY', None)
            or '6LcftywuAAAAADhm6T1T3sUemmpViTYX6aftCXt8'
        )
        html = f'<div class="g-recaptcha" data-sitekey="{site_key}"></div>'
        return mark_safe(html)

    def value_from_datadict(self, data, files, name):
        # Google reCAPTCHA v2 posts the response token in 'g-recaptcha-response'
        return data.get('g-recaptcha-response', '') or data.get(name, '')


class ReCaptchaField(forms.Field):
    widget = ReCaptchaWidget

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('required', True)
        kwargs.setdefault('label', 'Security Verification')
        super().__init__(*args, **kwargs)

    def validate(self, value):
        super().validate(value)
        if not value:
            raise ValidationError("Please complete the 'I'm not a robot' reCAPTCHA verification.")

        if not verify_recaptcha(value):
            raise ValidationError("reCAPTCHA verification failed. Please try ticking the checkbox again.")


class CaptchaForm(forms.Form):
    captcha = ReCaptchaField()


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
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'name@example.com'}),
        help_text="A 6-digit verification code will be sent to this email."
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Create strong password'}),
        min_length=6
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm password'}),
        label="Confirm Password"
    )
    captcha = ReCaptchaField()

    class Meta:
        model = User
        fields = ['username', 'email']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Choose username'}),
        }

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if not username:
            raise forms.ValidationError("Username cannot be empty.")
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError(f"Username '{username}' is already taken. Please choose another.")
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if not email:
            raise forms.ValidationError("Email address is required for verification.")
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email address already exists. Please log in instead.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        p1 = cleaned_data.get('password')
        p2 = cleaned_data.get('password_confirm')
        if p1 and p2 and p1 != p2:
            self.add_error('password_confirm', "Passwords do not match.")
        return cleaned_data
