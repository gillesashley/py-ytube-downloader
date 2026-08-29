from django.apps import AppConfig


class DownloaderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "downloader"

    def ready(self):
        from contextlib import suppress

        from django.db import OperationalError

        from downloader.models import Job

        # table not created yet during initial migrate
        with suppress(OperationalError):
            Job.objects.filter(status=Job.Status.RUNNING).update(
                status=Job.Status.FAILED, error="Interrupted (server restarted)."
            )
