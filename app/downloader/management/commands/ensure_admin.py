import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the admin superuser from ADMIN_USER/ADMIN_PASSWORD env vars."

    def handle(self, *args, **options):
        User = get_user_model()
        username = os.environ.get("ADMIN_USER", "admin")
        password = os.environ.get("ADMIN_PASSWORD", "admin")
        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(username, "", password)
            self.stdout.write(f"Created superuser '{username}'")
        else:
            self.stdout.write(f"Superuser '{username}' already exists")
