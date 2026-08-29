from django.db import models


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    url = models.URLField()
    title = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.QUEUED
    )
    error = models.TextField(blank=True)
    file_path = models.CharField(max_length=1000, blank=True)
    progress = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
