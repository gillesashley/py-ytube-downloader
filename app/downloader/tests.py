import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from downloader.apps import DownloaderConfig
from downloader.models import Job
from downloader.services import _progress, pick_audio_format, pick_video_format, run_job


def F(**kw):
    return {
        "height": None,
        "vcodec": "avc1",
        "acodec": "none",
        "fps": None,
        "abr": None,
        "format_id": "0",
        **kw,
    }


class MockYDL:
    """Fake yt_dlp.YoutubeDL: returns info on extract_info, writes a fake file."""

    last_opts: dict | None = None

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
            self._info["requested_downloads"] = [
                {"filepath": str(settings.MEDIA_ROOT / "T.mp4")}
            ]
        return self._info

    def download(self, urls):
        return 0

    def prepare_filename(self, info):
        return str(settings.MEDIA_ROOT / "T.mp4")


class CancelYDL(MockYDL):
    """Fires the real progress hook once during download, like yt-dlp does."""

    def extract_info(self, url, download=True):
        opts = type(self).last_opts or {}
        for hook in opts.get("progress_hooks", []):
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
        return super().extract_info(url, download)


class BoomYDL(MockYDL):
    def __init__(self, info, exc):
        super().__init__(info)
        self._exc = exc

    def extract_info(self, url, download=False):
        raise self._exc


def patch_ydl(mock_ydl):
    """Patch YoutubeDL, recording constructor opts on the mock."""

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
        v = pick_video_format(formats)
        assert v is not None
        self.assertEqual(v["format_id"], "b")

    def test_returns_none_without_720_or_1080(self):
        self.assertIsNone(pick_video_format([F(height=360)]))

    def test_picks_highest_fps_at_same_height(self):
        formats = [F(height=720, fps=30), F(height=720, fps=60)]
        v = pick_video_format(formats)
        assert v is not None
        self.assertEqual(v["fps"], 60)

    def test_picks_highest_abr_audio(self):
        formats = [
            F(abr=128, vcodec="none", acodec="mp4a", format_id="a"),
            F(abr=256, vcodec="none", acodec="mp4a", format_id="b"),
        ]
        a = pick_audio_format(formats)
        assert a is not None
        self.assertEqual(a["format_id"], "b")

    def test_excludes_audio_only_formats_from_video(self):
        formats = [F(height=1080), F(height=1080, vcodec="none", acodec="mp4a")]
        v = pick_video_format(formats)
        assert v is not None
        self.assertEqual(v["format_id"], "0")

    def test_height_filter_returns_only_that_height(self):
        formats = [F(height=1080, format_id="a"), F(height=720, format_id="b")]
        v = pick_video_format(formats, height=720)
        assert v is not None
        self.assertEqual(v["format_id"], "b")

    def test_height_filter_none_when_missing(self):
        self.assertIsNone(pick_video_format([F(height=360)], height=1080))

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

    def test_live_stream_rejected(self):
        info = {"title": "Live now", "is_live": True, "formats": []}
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.FAILED)
        self.assertIn("live stream", self.job.error)

    def test_retries_extraction_when_formats_missing(self):
        """First extract returns a partial format list; second has 720p."""
        calls = []

        def flaky_ydl_factory(*args, **kwargs):
            class FlakyYDL(MockYDL):
                def extract_info(self, url, download=False):
                    fmt = {
                        "height": 720,
                        "vcodec": "avc1",
                        "fps": 25,
                        "format_id": "136",
                        "acodec": "none",
                    }
                    full = {"title": "T", "formats": [fmt]}
                    if download:
                        return full
                    calls.append(True)
                    if len(calls) == 1:
                        return {
                            "title": "T",
                            "formats": [{"height": 360, "vcodec": "avc1"}],
                        }
                    return full

            mock = FlakyYDL({})
            mock.last_opts = args[0] if args else dict(kwargs)
            return mock

        with patch(
            "downloader.services.yt_dlp.YoutubeDL", side_effect=flaky_ydl_factory
        ):
            run_job(self.job.pk, resolution=720)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.DONE)
        self.assertEqual(len(calls), 2)  # format extractions, not the download pass

    def test_requested_height_missing_marks_failed(self):
        info = {
            "title": "T",
            "formats": [
                {
                    "height": 720,
                    "vcodec": "avc1",
                    "fps": 25,
                    "format_id": "a",
                    "acodec": "none",
                }
            ],
        }
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk, resolution=1080)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.FAILED)
        self.assertIn("no 1080p format", self.job.error)

    def test_requested_height_used_when_present(self):
        info = {
            "title": "T",
            "formats": [
                {
                    "height": 1080,
                    "vcodec": "avc1",
                    "fps": 25,
                    "format_id": "137",
                    "acodec": "none",
                },
                {
                    "height": 720,
                    "vcodec": "avc1",
                    "fps": 25,
                    "format_id": "136",
                    "acodec": "none",
                },
            ],
        }
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk, resolution=720)
        opts = mock_ydl.last_opts
        assert opts is not None
        self.assertEqual(opts["format"], "136/best")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.DONE)

    def test_progress_hook_writes_percent(self):
        _progress(
            self.job.pk,
            {
                "status": "downloading",
                "downloaded_bytes": 50000000,
                "total_bytes": 100000000,
            },
        )
        self.job.refresh_from_db()
        self.assertEqual(self.job.progress, 50.0)

    def test_success_sets_done_and_file(self):
        info = {
            "title": "T",
            "formats": [
                {
                    "height": 1080,
                    "vcodec": "avc1",
                    "fps": 25,
                    "format_id": "137",
                    "acodec": "none",
                },
                {
                    "height": 0,
                    "vcodec": "none",
                    "acodec": "mp4a",
                    "abr": 128,
                    "format_id": "140",
                },
            ],
        }
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        opts = mock_ydl.last_opts
        assert opts is not None
        self.assertEqual(opts["format"], "137+140")
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.DONE)
        self.assertEqual(self.job.title, "T")
        self.assertEqual(self.job.file_path, "T.mp4")
        self.assertEqual(self.job.progress, 100.0)

    def test_muxed_video_skips_merge(self):
        info = {
            "title": "T",
            "formats": [
                {
                    "height": 1080,
                    "vcodec": "avc1",
                    "fps": 30,
                    "format_id": "22",
                    "acodec": "mp4a",
                },
                {
                    "height": 0,
                    "vcodec": "none",
                    "acodec": "mp4a",
                    "abr": 128,
                    "format_id": "140",
                },
            ],
        }
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        opts = mock_ydl.last_opts
        assert opts is not None
        self.assertEqual(opts["format"], "22/best")
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

    def test_cancelled_job_aborts_and_cleans_partials(self):
        info = {
            "title": "T",
            "formats": [
                {
                    "height": 1080,
                    "vcodec": "avc1",
                    "fps": 25,
                    "format_id": "137",
                    "acodec": "none",
                },
                {
                    "height": 0,
                    "vcodec": "none",
                    "acodec": "mp4a",
                    "abr": 128,
                    "format_id": "140",
                },
            ],
        }
        self.job.cancelled = True
        self.job.save(update_fields=["cancelled"])
        settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
        (settings.MEDIA_ROOT / "T.f137.mp4.part").write_bytes(b"partial")
        mock_ydl = CancelYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.CANCELLED)
        self.assertFalse((settings.MEDIA_ROOT / "T.f137.mp4.part").exists())

    def test_cancel_before_download_skips(self):
        info = {
            "title": "T",
            "formats": [
                {
                    "height": 1080,
                    "vcodec": "avc1",
                    "fps": 25,
                    "format_id": "137",
                    "acodec": "none",
                }
            ],
        }
        self.job.cancelled = True
        self.job.save(update_fields=["cancelled"])
        mock_ydl = MockYDL(info)
        with patch_ydl(mock_ydl):
            run_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.CANCELLED)


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("me", password="pw")

    def test_index_public(self):
        r = self.client.get(reverse("index"))
        self.assertEqual(r.status_code, 200)

    def test_submit_creates_job_and_starts(self):
        called = []
        done = threading.Event()

        def fake_run_job(pk, resolution=None):
            called.append((pk, resolution))
            done.set()

        with patch("downloader.views.run_job", side_effect=fake_run_job):
            r = self.client.post(reverse("submit"), {"url": "https://example.com/v"})
        self.assertEqual(r.status_code, 302)
        job = Job.objects.get()
        self.assertEqual(job.url, "https://example.com/v")
        self.assertTrue(done.wait(2))
        self.assertEqual(called, [(job.pk, None)])

    def test_submit_passes_resolution(self):
        called = []
        done = threading.Event()

        def fake_run_job(pk, resolution=None):
            called.append((pk, resolution))
            done.set()

        with patch("downloader.views.run_job", side_effect=fake_run_job):
            self.client.post(
                reverse("submit"),
                {"url": "https://example.com/v", "resolution": "720"},
            )
        job = Job.objects.get()
        self.assertTrue(done.wait(2))
        self.assertEqual(called, [(job.pk, "720")])

    def test_submit_ignores_bogus_resolution(self):
        called = []
        done = threading.Event()

        def fake_run_job(pk, resolution=None):
            called.append((pk, resolution))
            done.set()

        with patch("downloader.views.run_job", side_effect=fake_run_job):
            self.client.post(
                reverse("submit"),
                {"url": "https://example.com/v", "resolution": "9999"},
            )
        job = Job.objects.get()
        self.assertTrue(done.wait(2))
        self.assertEqual(called, [(job.pk, None)])

    def test_submit_rejects_non_http_url(self):
        r = self.client.post(reverse("submit"), {"url": "not-a-url"})
        self.assertEqual(Job.objects.count(), 0)
        self.assertEqual(r.status_code, 302)

    def test_status_json(self):
        job = Job.objects.create(
            url="https://example.com/v", status=Job.Status.RUNNING, progress=42.5
        )
        r = self.client.get(reverse("status", args=[job.pk]))
        self.assertEqual(r.json()["status"], "running")
        self.assertEqual(r.json()["progress"], 42.5)

    def test_cancel_flags_running_job(self):
        job = Job.objects.create(url="https://example.com/v", status=Job.Status.RUNNING)
        r = self.client.post(reverse("cancel", args=[job.pk]))
        self.assertEqual(r.status_code, 302)
        job.refresh_from_db()
        self.assertTrue(job.cancelled)
        self.assertEqual(job.status, Job.Status.RUNNING)  # worker flips it on abort

    def test_cancel_public(self):
        job = Job.objects.create(url="https://example.com/v", status=Job.Status.RUNNING)
        self.client.post(reverse("cancel", args=[job.pk]))
        job.refresh_from_db()
        self.assertTrue(job.cancelled)

    def test_cancel_noop_on_done_job(self):
        job = Job.objects.create(url="https://example.com/v", status=Job.Status.DONE)
        self.client.post(reverse("cancel", args=[job.pk]))
        job.refresh_from_db()
        self.assertFalse(job.cancelled)

    def test_delete_removes_file_and_job(self):
        self.client.force_login(self.user)
        job = Job.objects.create(url="https://example.com/v", file_path="T.mp4")
        with patch("pathlib.Path.unlink") as m:
            self.client.post(reverse("delete", args=[job.pk]))
        m.assert_called_once_with(missing_ok=True)
        self.assertFalse(Job.objects.filter(pk=job.pk).exists())

    def test_delete_requires_login(self):
        job = Job.objects.create(url="https://example.com/v")
        r = self.client.post(reverse("delete", args=[job.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])
        self.assertTrue(Job.objects.filter(pk=job.pk).exists())

    def test_download_file_404_when_missing(self):
        job = Job.objects.create(url="https://example.com/v", file_path="ghost.mp4")
        r = self.client.get(reverse("download_file", args=[job.pk]))
        self.assertEqual(r.status_code, 404)

    def test_download_file_serves_attachment(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            override_settings(MEDIA_ROOT=Path(tmp)),
        ):
            Path(tmp, "T.mp4").write_bytes(b"data")
            job = Job.objects.create(url="https://example.com/v", file_path="T.mp4")
            r = self.client.get(reverse("download_file", args=[job.pk]))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(
                r.headers["Content-Disposition"], 'attachment; filename="T.mp4"'
            )
            self.assertEqual(b"".join(r.streaming_content), b"data")  # type: ignore
            r.close()  # release the file handle so Windows allows temp-dir cleanup


class StartupTests(TestCase):
    def test_ready_marks_orphaned_running_jobs_failed(self):
        job = Job.objects.create(url="https://example.com/v", status=Job.Status.RUNNING)
        DownloaderConfig.create("downloader.apps.DownloaderConfig").ready()
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertIn("Interrupted", job.error)

    def test_ensure_admin_creates_superuser(self):
        from django.core.management import call_command

        with patch.dict("os.environ", {"ADMIN_USER": "boss", "ADMIN_PASSWORD": "pw"}):
            call_command("ensure_admin")
        self.assertTrue(
            User.objects.filter(username="boss", is_superuser=True).exists()
        )

    def test_ensure_admin_idempotent(self):
        from django.core.management import call_command

        with patch.dict("os.environ", {"ADMIN_USER": "boss", "ADMIN_PASSWORD": "pw"}):
            call_command("ensure_admin")
            call_command("ensure_admin")
        self.assertEqual(User.objects.filter(username="boss").count(), 1)
