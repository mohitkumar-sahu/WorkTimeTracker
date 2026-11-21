# WorkTime Tracker (Django Attendance & Hours Tracker)

A complete web application built with **Django** to track daily login/logout timings against a mandatory **9 hours 15 minutes (9.25 hrs)** daily requirement, compute weekly and monthly averages, monitor surplus/deficit hours to prevent salary deductions, and track leave balances.

---

## 🌟 Key Features

1. **Target Working Time (9h 15m Daily Target)**:
   - Configurable per user profile (defaults to 9 hours 15 minutes).
   - Real-time calculation of net worked hours after deducting break times (lunch, tea, etc.).
   - Visual surplus (+green) and deficit (-red) status for each day.

2. **One-Click Punch In & Punch Out**:
   - **Punch In**: Automatically captures current time and calculates the **Target Logout Time** (e.g. *"If you logged in at 09:30 AM with a 15m break, logout by 07:00 PM to hit 9h 15m"*).
   - **Punch Out**: Automatically records end time and informs you of your daily total and surplus/deficit.
   - **Manual Logging & Editing**: Ability to manually backfill or adjust past days.

3. **Weekly & Monthly Averages**:
   - **Weekly Average**: Average hours worked per working day for the current week, plus net surplus/deficit.
   - **Monthly Average**: Average hours worked per day across the current month.
   - **Salary Deduction Risk Warning**: If monthly average drops below 9h 15m, a warning banner highlights the exact deficit hours and estimated salary impact so you can recover the hours before month-end.

4. **Leave Management**:
   - Track **Casual Leave (CL)**, **Sick Leave (SL)**, **Earned/Paid Leave (PL)**, **Half Day**, and **Unpaid Leave**.
   - **Smart Average Protection**: Approved leaves are tracked separately and do **not** penalize your working hours average.
   - Leave summary counters for the month and year, plus breakdown by category.

5. **Visual Analytics & Exports**:
   - Interactive **Chart.js** bar chart displaying daily worked hours vs. the 9h 15m target threshold.
   - **Export to CSV**: Download monthly attendance sheets for HR or personal records.

---

## 🚀 Quick Start Guide

### 1. Activate the Virtual Environment
```bash
source venv/bin/activate
```

### 2. Run Database Migrations (already applied)
```bash
python manage.py migrate
```

### 3. (Optional) Seed Demo Data
To immediately see pre-populated shifts, surplus/deficit badges, leave records, and chart visuals:
```bash
python manage.py seed_demo_data
```
This sets up a user:
- **Username**: `demo`
- **Password**: `demo123`

### 4. Start the Development Server
```bash
python manage.py runserver 8000
```
Open your browser and visit: **http://127.0.0.1:8000**

---

## 📁 Project Architecture

```
WorkTimeTracker/
├── attendance/                 # Main attendance & leave tracking app
│   ├── models.py               # UserProfile, AttendanceRecord, LeaveRecord
│   ├── services.py             # Weekly/Monthly aggregations & salary deduction math
│   ├── forms.py                # AttendanceForm, LeaveForm, UserProfileForm, Register
│   ├── views.py                # Dashboard, Punch In/Out, History, CSV export, Leaves
│   ├── urls.py                 # Route definitions
│   ├── admin.py                # Django admin registration
│   ├── tests.py                # Automated unit and view tests
│   ├── templates/attendance/   # Bootstrap 5 responsive UI templates
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── history.html
│   │   ├── attendance_form.html
│   │   ├── leaves.html
│   │   ├── leave_form.html
│   │   ├── profile.html
│   │   ├── login.html
│   │   └── register.html
│   └── management/commands/
│       └── seed_demo_data.py   # Demo data generator
├── config/                     # Django project configuration
│   ├── settings.py             # Configured with Asia/Kolkata timezone & app settings
│   ├── urls.py
│   └── wsgi.py
├── db.sqlite3                  # SQLite database
├── manage.py
└── requirements.txt
```

---

## 🧪 Running Automated Tests
```bash
source venv/bin/activate
python manage.py test
```
All unit tests and view assertions run in ~1 second.
