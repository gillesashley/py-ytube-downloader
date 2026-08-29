from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from downloader.models import Job
from downloader.services import _progress, pick_audio_format, pick_video_format, run_job


def F(**kw):
    return {"height": None, "vcodec": "avc1", "acodec": "none",
            "fps": None, "abr": None, "format_id": "0", **kw}


class MockYDL:
    """Fake yt_dlp.YoutubeDL: returns info on extract_info, writes a fake file."""

    last_opts = None

    def __init__(self, info):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if download:
            # mirrors real yt_dlp: requested_downloads is only set on the dict
            # returned by extract_info(download=True)
            self._info["requested_downloads"] = [{"filepath": str(settings.MEDIA_ROOT / "T.mp4")}]
        return self._info

    def download(self, urls):
        return 0

    def prepare_filename(self, info):
        return str(settings.MEDIA_ROOT / "T.mp4")


class BoomYDL(MockYDL):
    def __init__(self, info, exc):
        super().__init__(info)
        self._exc = exc

    def extract_info(self, url, download=False):
        raise self._exc


def patch_ydl(mock_ydl):
    """Patch downloader.services.yt_dlp.YoutubeDL, recording constructor opts on the mock."""

    def factory(*args, **kwargs):
        mock_ydl.last_opts = args[0] if args else dict(kwargs)
        return mock_ydl

    return patch("downloader.services.yt_dlp.YoutubeDL", side_effect=factory)


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

    def test_excludes_audio_only_formats_from_video(self):
        formats = [F(height=1080), F(height=1080, vcodec="none", acodec="mp4a")]
        self.assertEqual(pick_video_format(formats)["format_id"], "0")

    def test_returns_none_without_audio(self):
        self.assertIsNone(pick_audio_format([F()]))


class RunJobTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(url="https://example.com/v")

    def test_no_720_or_1080_marks_failed(self):
        info = {"title": "Low res", "formats": [{"height": 360, "vcodec": "avc1"}]}
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.FAILED)
        self.assertIn("no 720p or 1080p", self.job.error)

    def test_progress_hook_writes_percent(self):
        _progress(self.job.pk, {"status": "downloading",
                                "downloaded_bytes": 50000000, "total_bytes": 100000000})
        self.job.refresh_from_db()
        self.assertEqual(self.job.progress, 50.0)

    def test_success_sets_done_and_file(self):
        info = {"title": "T", "formats": [
            {"height": 1080, "vcodec": "avc1", "fps": 25, "format_id": "137", "acodec": "none"},
            {"height": 0, "vcodec": "none", "acodec": "mp4a", "abr": 128, "format_id": "140"},
        ]}
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        self.assertEqual(mock_ydl.last_opts["format"], "137+140")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.DONE)
        self.assertEqual(self.job.title, "T")
        self.assertEqual(self.job.file_path, "T.mp4")
        self.assertEqual(self.job.progress, 100.0)

    def test_muxed_video_skips_merge(self):
        info = {"title": "T", "formats": [
            {"height": 1080, "vcodec": "avc1", "fps": 30, "format_id": "22", "acodec": "mp4a"},
            {"height": 0, "vcodec": "none", "acodec": "mp4a", "abr": 128, "format_id": "140"},
        ]}
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        self.assertEqual(mock_ydl.last_opts["format"], "22/best")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.DONE)

    def test_download_error_marks_failed(self):
        from yt_dlp.utils import DownloadError

        mock_ydl = BoomYDL(None, DownloadError("connection reset"))
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.FAILED)
        self.assertIn("connection reset", self.job.error)

    def test_non_download_error_marks_failed(self):
        from yt_dlp.utils import PostProcessingError

        mock_ydl = BoomYDL(None, PostProcessingError("ffmpeg merge failed"))
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.FAILED)
        self.assertIn("ffmpeg merge failed", self.job.error)
