from pathlib import Path
from typing import Any

import yt_dlp
from django.conf import settings
from yt_dlp.utils import DownloadCancelled

RESOLUTIONS = (1080, 720)


def pick_video_format(formats, height=None):
    """Best video format at `height` (1080/720), or 1080→720 fallback when None."""
    heights = (height,) if height else RESOLUTIONS
    for h in heights:
        candidates = [
            f for f in formats if f.get("height") == h and f.get("vcodec") != "none"
        ]
        if candidates:
            return max(candidates, key=lambda f: f.get("fps") or 0)
    return None


def pick_audio_format(formats):
    """Best audio-only format, highest bitrate."""
    audio = [
        f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"
    ]
    return max(audio, key=lambda f: f.get("abr") or 0) if audio else None


# Windows dev only; the container uses ffmpeg from PATH
FFMPEG_DIR = Path(__file__).resolve().parents[2] / "ffmpeg" / "bin"


def _is_cancelled(job_id):
    from downloader.models import Job

    return Job.objects.filter(pk=job_id, cancelled=True).exists()


def _progress(job_id, d):
    from downloader.models import Job  # local import avoids app-config at module load

    if _is_cancelled(job_id):
        raise DownloadCancelled
    if d.get("status") != "downloading":
        return
    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    downloaded = d.get("downloaded_bytes") or 0
    if total:
        # ponytail: direct update(), no ORM save() — single worker, no race concerns
        pct = min(round(downloaded / total * 100, 1), 100.0)
        Job.objects.filter(pk=job_id).update(progress=pct)


def _cleanup_partials(title):
    """Remove yt-dlp intermediate files (<title>.f* / <title>.*.part)."""
    for pattern in (f"{title}.f*", f"{title}.*.part"):
        for p in settings.MEDIA_ROOT.glob(pattern):
            p.unlink(missing_ok=True)


def run_job(job_id, resolution=None):
    """Run the download; `resolution` is an optional height (1080/720) to use."""
    from downloader.models import Job

    job = Job.objects.get(pk=job_id)
    job.status = Job.Status.RUNNING
    job.save()
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(job.url, download=False)
        if info.get("is_live"):
            # live streams download until the broadcast ends (unbounded) and their
            # formats change over the first minutes — reject with a clear message
            job.status = Job.Status.FAILED
            job.error = "This is a live stream; wait until it ends, then try again."
            job.save(update_fields=["status", "error"])
            return
        formats = info.get("formats", [])
        video = pick_video_format(formats, height=resolution)
        if not video:
            # transient YouTube hiccup: the player response sometimes omits formats;
            # one re-extraction fixes most of these cases
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(job.url, download=False)
            formats = info.get("formats", [])
            video = pick_video_format(formats, height=resolution)
        if not video:
            job.status = Job.Status.FAILED
            label = f"{resolution}p" if resolution else "720p or 1080p"
            job.error = f"'{info.get('title', job.url)}' has no {label} format."
            # update_fields: instance is stale on progress (hook writes to DB directly)
            job.save(update_fields=["status", "error"])
            return
        audio = pick_audio_format(formats)
        if audio and video.get("acodec") == "none":
            format_sel = f"{video['format_id']}+{audio['format_id']}"
        else:
            format_sel = f"{video['format_id']}/best"

        job.title = info.get("title", "")
        job.save()

        if _is_cancelled(job.pk):
            raise DownloadCancelled

        # ponytail: opts: Any — yt-dlp's _Params TypedDict comes from Pylance's
        # bundled stubs and rejects plain dicts; keys are verified by ruff/manual
        opts: Any = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": str(settings.MEDIA_ROOT / "%(title)s.%(ext)s"),
            "format": format_sel,
            "progress_hooks": [lambda d: _progress(job.pk, d)],
        }
        if FFMPEG_DIR.exists():
            opts["ffmpeg_location"] = str(FFMPEG_DIR)
        with yt_dlp.YoutubeDL(opts) as ydl:
            # ponytail: deviation from plan — download=True so requested_downloads is
            # populated on the returned dict; separate ydl.download() re-extracts into a
            # fresh dict, leaving info["requested_downloads"] absent in production
            info = ydl.extract_info(job.url, download=True)

        files = [
            fd["filepath"]
            for fd in info.get("requested_downloads", [])
            if fd.get("filepath")
        ]
        path = Path(files[0]) if files else Path(ydl.prepare_filename(info))
        try:
            job.file_path = path.relative_to(settings.MEDIA_ROOT).as_posix()
        except ValueError:
            job.file_path = path.name
        job.status = Job.Status.DONE
        job.progress = 100.0
        # update_fields: instance is stale on progress (hook writes to DB directly)
        job.save(update_fields=["status", "file_path", "progress"])
    except DownloadCancelled:
        # user pressed cancel: flag was seen by the progress hook (or pre-download
        # check); clean up partials and record the terminal state
        _cleanup_partials(job.title)
        job.status = Job.Status.CANCELLED
        job.save(update_fields=["status"])
    # any failure must mark FAILED, never leave the job stuck RUNNING;
    # KeyboardInterrupt/SystemExit are not Exception subclasses, so they still propagate
    except Exception as e:
        job.status = Job.Status.FAILED
        job.error = str(e)
        # update_fields: instance is stale on progress (hook writes to DB directly)
        job.save(update_fields=["status", "error"])
