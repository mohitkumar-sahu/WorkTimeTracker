"""
WSGI config for config project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
app = application

# Auto-migrate and seed demo user on Vercel deployment if needed
if "VERCEL" in os.environ:
    from django.core.management import call_command
    try:
        call_command("migrate", interactive=False)
        from django.contrib.auth.models import User
        if not User.objects.filter(username="demo").exists():
            call_command("seed_demo_data")
    except Exception as e:
        print(f"Vercel auto-migrate note: {e}")
