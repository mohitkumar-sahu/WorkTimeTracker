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

# If an external database (e.g. Neon PostgreSQL) is connected and completely fresh, auto-initialize once
if "DATABASE_URL" in os.environ:
    try:
        from django.db import connection
        tables = connection.introspection.table_names()
        if "django_migrations" not in tables:
            from django.core.management import call_command
            call_command("migrate", interactive=False)
            from django.contrib.auth.models import User
            if not User.objects.filter(username="demo").exists():
                call_command("seed_demo_data")
    except Exception as e:
        print(f"External DB init notice: {e}")

