from django.test import TestCase

from downloader.models import Job
from downloader.services import pick_audio_format, pick_video_format


def F(**kw):
    return {"height": None, "vcodec": "avc1", "acodec": "none",
            "fps": None, "abr": None, "format_id": "0", **kw}


class JobModelTests(TestCase):
    def test_job_defaults(self):
        job = Job.objects.create(url="https://example.com/v")
        self.assertEqual(job.status, Job.Status.QUEUED)
        self.assertEqual(job.progress, 0.0)
        self.assertEqual(job.title, "")
        self.assertEqual(job.file_path, "")


class FormatPickerTests(TestCase):
    def test_prefers_1080_over_720(self):
        formats = [F(height=720, format_id="a"), F(height=1080, format_id="b")]
        self.assertEqual(pick_video_format(formats)["format_id"], "b")

    def test_returns_none_without_720_or_1080(self):
        self.assertIsNone(pick_video_format([F(height=360)]))

    def test_picks_highest_fps_at_same_height(self):
        formats = [F(height=720, fps=30), F(height=720, fps=60)]
        self.assertEqual(pick_video_format(formats)["fps"], 60)

    def test_picks_highest_abr_audio(self):
        formats = [F(abr=128, vcodec="none", acodec="mp4a", format_id="a"),
                   F(abr=256, vcodec="none", acodec="mp4a", format_id="b")]
        self.assertEqual(pick_audio_format(formats)["format_id"], "b")
