from django.test import TestCase

from downloader.models import Job


class JobModelTests(TestCase):
    def test_job_defaults(self):
        job = Job.objects.create(url="https://example.com/v")
        self.assertEqual(job.status, Job.Status.QUEUED)
        self.assertEqual(job.progress, 0.0)
        self.assertEqual(job.title, "")
        self.assertEqual(job.file_path, "")
