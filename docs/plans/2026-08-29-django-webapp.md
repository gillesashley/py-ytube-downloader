# Django Downloader Webapp Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the CLI yt-dlp downloader into a private single-user Django webapp (1080p/720p only, background downloads with progress, auth-protected), containerized with Docker for VPS deployment.

**Architecture:** Django project in `app/` (project pkg `config`, app `downloader`). One `Job` model per download; each job runs in a daemon thread inside a SINGLE gunicorn worker (multi-worker would break thread-reachable polling; documented `# ponytail:` upgrade path is a DB-claim worker process). Frontend polls a JSON status endpoint every 2s. Files stored in `MEDIA_ROOT` (Docker volume). SQLite. No Celery/Redis/DRF/static-files framework. yt-dlp intentionally unpinned (stale pins broke the CLI — 2024.10.7 failure).

**Tech Stack:** Django 6.x (latest), yt-dlp (latest, unpinned), gunicorn (Dockerfile only), ffmpeg (apt in image; vendored `ffmpeg/bin` on Windows dev), Docker + docker-compose.

**Design doc:** `docs/plans/2026-08-29-webapp-design.md` (approved).

**Repo conventions:** venv at repo root → prefix all commands `& ".\venv\Scripts\python.exe"`. Lint gate: ruff + pyright (already installed in venv). Tests: Django's built-in runner (`manage.py test`), no pytest.

---

## Task 1: Install Django, create project skeleton

**Files:**
- Modify: `requirements.txt`
- Create: `app/manage.py`, `app/config/*`, `app/downloader/*` (via django commands)

**Step 1: Install latest Django and pin it**

Run:
```powershell
& ".\venv\Scripts\python.exe" -m pip install --upgrade Django
& ".\venv\Scripts\python.exe" -m pip show Django | Select-String "^Version:"
```
Expected: a version line (e.g. `Version: 6.1.x`).

**Step 2: Create project + app**

Run:
```powershell
& ".\venv\Scripts\python.exe" -m django startproject config app
& ".\venv\Scripts\python.exe" app\manage.py startapp downloader app\downloader
```
Expected: `app/manage.py`, `app/config/settings.py`, `app/config/urls.py`, `app/downloader/models.py` etc. exist.

**Step 3: Update requirements.txt** (replace current yt-dlp-only content)

```txt
# yt-dlp intentionally unpinned: YouTube changes break it; every rebuild gets the latest.
yt-dlp
Django==<version from Step 1>
```

**Step 4: Verify**

Run: `& ".\venv\Scripts\python.exe" app\manage.py check`
Expected: `System check identified no issues (0 silenced).`

**Step 5: Commit**

```bash
git add requirements.txt app/
git commit -m "chore: scaffold Django project"
```

---

## Task 2: Configure settings (env-driven, media, app wiring)

**Files:**
- Modify: `app/config/settings.py`

**Step 1: Edit `app/config/settings.py`**

- `from pathlib import Path` already present; add `import os` at top.
- Replace `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`:
```python
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o]
```
- `INSTALLED_APPS`: append `"downloader",`.
- Add `MEDIA_ROOT = BASE_DIR / "media"` at the end.
- TEMPLATES `"DIRS": [BASE_DIR / "templates"],` (for the auth login template).
- Add at end:
```python
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
```

**Step 2: Verify**

Run: `& ".\venv\Scripts\python.exe" app\manage.py check`
Expected: no issues.

**Step 3: Commit**

```bash
git add app/config/settings.py
git commit -m "feat: env-driven settings, media root, auth redirects"
```

---

## Task 3: Job model (TDD)

**Files:**
- Modify: `app/downloader/models.py`
- Test: `app/downloader/tests.py`

**Step 1: Write failing test** — append to `app/downloader/tests.py`:

```python
from django.test import TestCase
from downloader.models import Job


class JobModelTests(TestCase):
    def test_job_defaults(self):
        job = Job.objects.create(url="https://example.com/v")
        self.assertEqual(job.status, Job.Status.QUEUED)
        self.assertEqual(job.progress, 0.0)
        self.assertEqual(job.title, "")
        self.assertEqual(job.file_path, "")
```

**Step 2: Run to verify it fails**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.JobModelTests -v 2`
Expected: FAIL — `django.core.exceptions.ImproperlyConfigured` or `ModuleNotFoundError` (app not migrated / model missing).

**Step 3: Implement model** — replace `app/downloader/models.py`:

```python
class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    url = models.URLField()
    title = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)
    file_path = models.CharField(max_length=1000, blank=True)
    progress = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
```

**Step 4: Make + run migrations**

Run:
```powershell
& ".\venv\Scripts\python.exe" app\manage.py makemigrations downloader
& ".\venv\Scripts\python.exe" app\manage.py migrate
& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.JobModelTests -v 2
```
Expected: 1 test PASS.

**Step 5: Commit**

```bash
git add app/downloader/models.py app/downloader/migrations/ app/downloader/tests.py
git commit -m "feat: Job model with status/progress/error"
```

---

## Task 4: Format pickers (TDD) — copied from CLI

**Files:**
- Create: `app/downloader/services.py`
- Test: `app/downloader/tests.py`

**Step 1: Write failing test** — append:

```python
from downloader.services import pick_audio_format, pick_video_format

def F(**kw):
    return {"height": None, "vcodec": "avc1", "acodec": "none",
            "fps": None, "abr": None, "format_id": "0", **kw}
```

Note: fixture default `vcodec` must be `"avc1"` (not `"none"`) or `pick_video_format` filters everything out; audio tests must pass `vcodec="none", acodec="mp4a"` explicitly.


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
        formats = [F(abr=128, format_id="a"), F(abr=256, format_id="b")]
        self.assertEqual(pick_audio_format(formats)["format_id"], "b")
```

**Step 2: Run to verify fail**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.FormatPickerTests -v 2`
Expected: FAIL — `ModuleNotFoundError: No module named 'downloader.services'`.

**Step 3: Implement** — create `app/downloader/services.py` (copied from `main.py`):

```python
RESOLUTIONS = (1080, 720)


def pick_video_format(formats):
    """Best video format, preferring 1080p then 720p."""
    for height in RESOLUTIONS:
        candidates = [
            f for f in formats if f.get("height") == height and f.get("vcodec") != "none"
        ]
        if candidates:
            return max(candidates, key=lambda f: f.get("fps") or 0)
    return None


def pick_audio_format(formats):
    audio = [
        f for f in formats if f.get("vcodec") == "none" and f.get("acodec") != "none"
    ]
    return max(audio, key=lambda f: f.get("abr") or 0) if audio else None
```

**Step 4: Run tests**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.FormatPickerTests -v 2`
Expected: 4 tests PASS.

**Step 5: Commit**

```bash
git add app/downloader/services.py app/downloader/tests.py
git commit -m "feat: 1080/720 format pickers (from CLI logic)"
```

---

## Task 5: Background executor `run_job` (TDD)

**Files:**
- Modify: `app/downloader/services.py`
- Test: `app/downloader/tests.py`

**Step 1: Write failing tests** — append:

```python
from unittest.mock import patch
from downloader.models import Job


class RunJobTests(TestCase):
    def setUp(self):
        self.job = Job.objects.create(url="https://example.com/v")

    def test_no_720_or_1080_marks_failed(self):
        info = {"title": "Low res", "formats": [{"height": 360, "vcodec": "avc1"}]}
        mock_ydl = MockYDL(info)
        with patch("downloader.services.yt_dlp.YoutubeDL", return_value=mock_ydl):
            run_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.FAILED)
        self.assertIn("no 720p or 1080p", self.job.error)

    def test_success_sets_done_and_file(self):
        info = {"title": "T", "formats": [
            {"height": 1080, "vcodec": "avc1", "fps": 25, "format_id": "137", "acodec": "none"},
            {"height": 0, "vcodec": "none", "acodec": "mp4a", "abr": 128, "format_id": "140"},
        ]}
        mock_ydl = MockYDL(info)
        with patch("downloader.services.yt_dlp.YoutubeDL", return_value=mock_ydl):
            run_job(self.job.pk)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, Job.Status.DONE)
        self.assertEqual(self.job.title, "T")
        self.assertEqual(self.job.file_path, "T.mp4")
```

Helper at top of tests.py:

```python
class MockYDL:
    """Fake yt_dlp.YoutubeDL: returns info on extract_info, writes a fake file."""

    def __init__(self, info):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        return self._info

    def download(self, urls):
        return 0

    def prepare_filename(self, info):
        return str(settings.MEDIA_ROOT / "T.mp4")
```

(add `from django.conf import settings` and `from downloader.services import run_job` to imports)

**Step 2: Run to verify fail**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.RunJobTests -v 2`
Expected: FAIL — `ImportError: cannot import name 'run_job'`.

**Step 3: Implement** — append to `app/downloader/services.py`:

```python
from pathlib import Path

import yt_dlp
from django.conf import settings
from yt_dlp.utils import DownloadError

FFMPEG_DIR = Path(__file__).resolve().parents[2] / "ffmpeg" / "bin"  # Windows dev only; container uses PATH ffmpeg


def _progress(job_id, d):
    from downloader.models import Job  # local import: avoids app-config import at module load

    if d.get("status") != "downloading":
        return
    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
    downloaded = d.get("downloaded_bytes") or 0
    if total:
        # ponytail: direct update(), no ORM save() — single worker, no race concerns
        Job.objects.filter(pk=job_id).update(progress=round(downloaded / total * 100, 1))


def run_job(job_id):
    from downloader.models import Job

    job = Job.objects.get(pk=job_id)
    job.status = Job.Status.RUNNING
    job.save()
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(job.url, download=False)
        formats = info.get("formats", [])
        video = pick_video_format(formats)
        if not video:
            job.status = Job.Status.FAILED
            job.error = f"'{info.get('title', job.url)}' has no 720p or 1080p format."
            job.save()
            return
        audio = pick_audio_format(formats)
        if audio and video.get("acodec") == "none":
            format_sel = f"{video['format_id']}+{audio['format_id']}"
        else:
            format_sel = f"{video['format_id']}/best"

        job.title = info.get("title", "")
        job.save()

        opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": str(settings.MEDIA_ROOT / "%(title)s.%(ext)s"),
            "format": format_sel,
            "progress_hooks": [lambda d: _progress(job.pk, d)],
        }
        if FFMPEG_DIR.exists():
            opts["ffmpeg_location"] = str(FFMPEG_DIR)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([job.url])

        files = [fd.get("filepath") for fd in info.get("requested_downloads", []) if fd.get("filepath")]
        path = Path(files[0]) if files else Path(ydl.prepare_filename(info))
        try:
            job.file_path = path.relative_to(settings.MEDIA_ROOT).as_posix()
        except ValueError:
            job.file_path = path.name
        job.status = Job.Status.DONE
        job.save()
    except DownloadError as e:
        job.status = Job.Status.FAILED
        job.error = str(e)
        job.save()
```

Note: `requested_downloads` is populated by yt-dlp only when `download=True` runs on the same info dict — correct here because the first `extract_info(download=False)` dict is passed to `prepare_filename`/reused? **Important:** run `ydl.download` on the SAME `YoutubeDL` instance as `extract_info` in the second block, so `requested_downloads` is populated on `info`. The code above re-enters a new context for download — if tests show `file_path` empty, change the second block to reuse the first `ydl`:
```python
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=False)  # populates requested_downloads on same ydl
            ydl.download([job.url])
```

**Step 4: Run tests**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.RunJobTests -v 2`
Expected: 2 tests PASS. If the file-path test fails, apply the reuse-`ydl` fix above and re-run.

**Step 5: Commit**

```bash
git add app/downloader/services.py app/downloader/tests.py
git commit -m "feat: background job executor with progress hook"
```

---

## Task 6: Views (TDD)

**Files:**
- Modify: `app/downloader/views.py`
- Test: `app/downloader/tests.py`

**Step 1: Write failing tests** — append:

```python
from django.urls import reverse


class ViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("me", password="pw")
        self.client.force_login(self.user)

    def test_index_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse("index"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])

    def test_submit_creates_job_and_starts(self):
        with patch("downloader.views.run_job") as m:
            r = self.client.post(reverse("submit"), {"url": "https://example.com/v"})
        self.assertEqual(r.status_code, 302)
        job = Job.objects.get()
        self.assertEqual(job.url, "https://example.com/v")
        m.assert_called_once_with(job.pk)

    def test_submit_rejects_non_http_url(self):
        r = self.client.post(reverse("submit"), {"url": "not-a-url"})
        self.assertEqual(Job.objects.count(), 0)
        self.assertEqual(r.status_code, 302)

    def test_status_json(self):
        job = Job.objects.create(url="https://example.com/v", status=Job.Status.RUNNING, progress=42.5)
        r = self.client.get(reverse("status", args=[job.pk]))
        self.assertEqual(r.json()["status"], "running")
        self.assertEqual(r.json()["progress"], 42.5)

    def test_delete_removes_file_and_job(self):
        job = Job.objects.create(url="https://example.com/v", file_path="T.mp4")
        with patch("downloader.views.Path.unlink") as m:
            self.client.post(reverse("delete", args=[job.pk]))
        m.assert_called_once_with(missing_ok=True)
        self.assertFalse(Job.objects.filter(pk=job.pk).exists())

    def test_download_file_404_when_missing(self):
        job = Job.objects.create(url="https://example.com/v", file_path="ghost.mp4")
        r = self.client.get(reverse("download_file", args=[job.pk]))
        self.assertEqual(r.status_code, 404)
```

(add `from django.contrib.auth.models import User` to imports)

**Step 2: Run to verify fail**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.ViewTests -v 2`
Expected: FAIL — views missing.

**Step 3: Implement** — replace `app/downloader/views.py`:

```python
import threading
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from downloader.models import Job
from downloader.services import run_job


@login_required
def index(request):
    jobs = Job.objects.order_by("-created_at")[:20]
    return render(request, "downloader/index.html", {"jobs": jobs})


@login_required
@require_POST
def submit(request):
    url = request.POST.get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        messages.error(request, "Enter a valid URL starting with http(s)://.")
    else:
        job = Job.objects.create(url=url)
        # ponytail: thread inside single gunicorn worker; swap for a DB-claim
        # worker process when multi-user/concurrency demands it
        threading.Thread(target=run_job, args=(job.pk,), daemon=True).start()
        messages.success(request, "Download started.")
    return redirect("index")


@login_required
def status(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    return JsonResponse({
        "status": job.status,
        "progress": job.progress,
        "title": job.title,
        "error": job.error,
        "done": job.status == Job.Status.DONE,
        "failed": job.status == Job.Status.FAILED,
    })


@login_required
@require_POST
def delete(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    if job.file_path:
        (settings.MEDIA_ROOT / job.file_path).unlink(missing_ok=True)
    job.delete()
    return redirect("index")


@login_required
def download_file(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    path = settings.MEDIA_ROOT / job.file_path if job.file_path else None
    if not path or not path.exists():
        raise Http404
    return FileResponse(path.open("rb"), as_attachment=True, filename=Path(job.file_path).name)
```

**Step 4: Run tests**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.ViewTests -v 2`
Expected: 6 tests PASS.

**Step 5: Commit**

```bash
git add app/downloader/views.py app/downloader/tests.py
git commit -m "feat: index/submit/status/delete/download views"
```

---

## Task 7: URL wiring + auth URLs

**Files:**
- Create: `app/downloader/urls.py`
- Modify: `app/config/urls.py`

**Step 1: Create `app/downloader/urls.py`**

```python
from django.urls import path

from downloader import views

app_name = "downloader"

urlpatterns = [
    path("", views.index, name="index"),
    path("submit/", views.submit, name="submit"),
    path("status/<int:job_id>/", views.status, name="status"),
    path("delete/<int:job_id>/", views.delete, name="delete"),
    path("download/<int:job_id>/", views.download_file, name="download_file"),
]
```

**Step 2: Replace `app/config/urls.py`**

```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("downloader.urls")),
]
```

**Step 3: Verify**

Run: `& ".\venv\Scripts\python.exe" app\manage.py check`
Expected: no issues.

**Step 4: Commit**

```bash
git add app/downloader/urls.py app/config/urls.py
git commit -m "feat: wire downloader + auth urls"
```

---

## Task 8: Templates (base, index with polling, login)

**Files:**
- Create: `app/templates/base.html`
- Create: `app/templates/registration/login.html`
- Create: `app/downloader/templates/downloader/index.html`

**Step 1: `app/templates/base.html`** — inline CSS, no static files (keeps container simple, no collectstatic):

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}yt-dlp downloader{% endblock %}</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; background: #111; color: #ddd; }
  h1 { font-size: 1.4rem; }
  form.inline { display: inline; }
  input[type=url], input[type=password], input[type=text] { width: 100%; padding: .5rem; margin: .25rem 0 .75rem; box-sizing: border-box; }
  button { padding: .5rem 1rem; cursor: pointer; }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  th, td { text-align: left; padding: .4rem; border-bottom: 1px solid #333; vertical-align: top; }
  .bar { background: #333; border-radius: 4px; height: 12px; width: 120px; }
  .bar > div { background: #2e8b57; height: 12px; border-radius: 4px; }
  .err { color: #e57373; }
  .msg { padding: .5rem .75rem; border-radius: 4px; margin-bottom: 1rem; }
  .msg.ok { background: #1e3a2a; }
  .msg.bad { background: #4a1e1e; }
  a { color: #7cb3ff; }
</style>
</head>
<body>
  <h1>yt-dlp downloader</h1>
  <p><small>{% if user.is_authenticated %}{{ user.username }} · <a href="{% url 'logout' %}">log out</a>{% else %}<a href="{% url 'login' %}">log in</a>{% endif %}</small></p>
  {% if messages %}
    {% for m in messages %}<div class="msg {{ m.tags }}">{{ m }}</div>{% endfor %}
  {% endif %}
  {% block content %}{% endblock %}
</body>
</html>
```

**Step 2: `app/templates/registration/login.html`**

```html
{% extends "base.html" %}
{% block content %}
  {% if form.errors %}<div class="msg bad">Wrong username or password.</div>{% endif %}
  <form method="post">
    {% csrf_token %}
    {{ form.as_p }}
    <button type="submit">Log in</button>
  </form>
{% endblock %}
```

**Step 3: `app/downloader/templates/downloader/index.html`**

```html
{% extends "base.html" %}
{% block content %}
  <form method="post" action="{% url 'submit' %}">
    {% csrf_token %}
    <label for="url">YouTube URL</label>
    <input type="url" name="url" id="url" required placeholder="https://www.youtube.com/watch?v=...">
    <button type="submit">Download (1080p/720p)</button>
  </form>

  <table>
    <tr><th>Video</th><th>Status</th><th>Progress</th><th></th></tr>
    {% for job in jobs %}
      <tr data-job-id="{{ job.pk }}" data-status="{{ job.status }}">
        <td>{{ job.title|default:job.url }}<br><small><a href="{{ job.url }}" target="_blank" rel="noopener">{{ job.url|truncatechars:50 }}</a></small></td>
        <td class="job-status">{{ job.status }}
          {% if job.error %}<br><span class="err">{{ job.error }}</span>{% endif %}
        </td>
        <td>
          {% if job.status == "running" or job.status == "queued" %}
            <div class="bar"><div class="job-progress" style="width: {{ job.progress }}%"></div></div>
          {% elif job.status == "done" %}
            <a href="{% url 'download_file' job.pk %}">Download file</a>
          {% endif %}
        </td>
        <td>
          <form class="inline" method="post" action="{% url 'delete' job.pk %}"
                onsubmit="return confirm('Delete job?');">
            {% csrf_token %}<button type="submit">Delete</button>
          </form>
        </td>
      </tr>
    {% empty %}
      <tr><td colspan="4">No downloads yet.</td></tr>
    {% endfor %}
  </table>

  <script>
    // ponytail: vanilla JS, poll active rows every 2s, reload page when done
    document.querySelectorAll("tr[data-status='running'], tr[data-status='queued']").forEach(row => {
      const poll = () => {
        fetch(`/status/${row.dataset.jobId}/`)
          .then(r => r.json())
          .then(j => {
            if (j.status === "running") {
              row.querySelector(".job-status").textContent = j.status;
              const bar = row.querySelector(".job-progress");
              if (bar) bar.style.width = j.progress + "%";
              setTimeout(poll, 2000);
            } else {
              location.reload();
            }
          })
          .catch(() => setTimeout(poll, 2000));
      };
      setTimeout(poll, 2000);
    });
  </script>
{% endblock %}
```

**Step 4: Verify page renders**

Run:
```powershell
& ".\venv\Scripts\python.exe" app\manage.py migrate
& ".\venv\Scripts\python.exe" app\manage.py createsuperuser --username admin --email "" --noinput
```
(createsuperuser will prompt for password interactively; enter one and remember it for local testing)

Then `& ".\venv\Scripts\python.exe" app\manage.py runserver` and check `http://127.0.0.1:8000/` redirects to login, login works, page renders with the form and empty table. Stop server after.

**Step 5: Commit**

```bash
git add app/templates/ app/downloader/templates/
git commit -m "feat: templates with progress polling and auth login page"
```

---

## Task 9: Startup safety — orphaned jobs + admin bootstrap (TDD)

**Files:**
- Modify: `app/downloader/apps.py`
- Create: `app/downloader/management/commands/ensure_admin.py`
- Test: `app/downloader/tests.py`

**Step 1: Write failing test** — append:

```python
from downloader.apps import DownloaderConfig


class StartupTests(TestCase):
    def test_ready_marks_orphaned_running_jobs_failed(self):
        job = Job.objects.create(url="https://example.com/v", status=Job.Status.RUNNING)
        DownloaderConfig("downloader", "downloader.apps.DownloaderConfig").ready()
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertIn("Interrupted", job.error)

    def test_ensure_admin_creates_superuser(self):
        from django.core.management import call_command
        with patch.dict("os.environ", {"ADMIN_USER": "boss", "ADMIN_PASSWORD": "pw"}):
            call_command("ensure_admin")
        self.assertTrue(User.objects.filter(username="boss", is_superuser=True).exists())
```

**Step 2: Run to verify fail**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.StartupTests -v 2`
Expected: FAIL — `ready()` missing / command missing.

**Step 3: Implement** — replace `app/downloader/apps.py`:

```python
from django.apps import AppConfig


class DownloaderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "downloader"

    def ready(self):
        from django.db import OperationalError
        from downloader.models import Job

        try:
            Job.objects.filter(status=Job.Status.RUNNING).update(
                status=Job.Status.FAILED, error="Interrupted (server restarted).")
        except OperationalError:
            pass  # table not created yet (during initial migrate)
```

Create `app/downloader/management/__init__.py`, `app/downloader/management/commands/__init__.py` (empty), and `app/downloader/management/commands/ensure_admin.py`:

```python
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
```

**Step 4: Run tests**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test downloader.tests.StartupTests -v 2`
Expected: 2 tests PASS. (Test DB gets created after ready() — the first call may race; if flaky, the ready() call in the test is after tables exist, so it's fine.)

**Step 5: Commit**

```bash
git add app/downloader/apps.py app/downloader/management/
git add app/downloader/tests.py
git commit -m "feat: orphan-job recovery at startup + ensure_admin command"
```

---

## Task 10: Container — Dockerfile + docker-compose

**Files:**
- Create: `Dockerfile` (repo root)
- Create: `docker-compose.yml` (repo root)

**Step 1: `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY app/ .

EXPOSE 8000

# Single worker: background download threads live in the worker process, and
# the status poll must always reach them. Add a DB-claim worker process before
# scaling workers.
CMD ["sh", "-c", "python manage.py migrate && python manage.py ensure_admin && exec gunicorn config.wsgi:application -b 0.0.0.0:8000 -w 1 --threads 4"]
```

**Step 2: `docker-compose.yml`**

```yaml
services:
  web:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - media:/app/media
    environment:
      - SECRET_KEY=${SECRET_KEY:-change-me-in-prod}
      - DEBUG=0
      - ALLOWED_HOSTS=${ALLOWED_HOSTS:-localhost}
      - CSRF_TRUSTED_ORIGINS=${CSRF_TRUSTED_ORIGINS:-}
      - ADMIN_USER=${ADMIN_USER:-admin}
      - ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin}

volumes:
  media:
```

**Step 3: Add `.dockerignore`**

```
venv/
media/
*.mp4
*.part
docs/
.git/
```

**Step 4: Build & smoke test (if Docker Desktop is installed)**

Run:
```powershell
docker compose up -d --build
docker compose logs web
```
Expected: gunicorn listening on `0.0.0.0:8000`. Open `http://localhost:8000`, log in with `admin/admin`, download a small video. If Docker isn't installed locally, note this and test on the VPS later.

**Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore
git commit -m "feat: Docker containerization with single-worker gunicorn"
```

---

## Task 11: Full verification gate

**Step 1: Lint + type check**

Run:
```powershell
& ".\venv\Scripts\ruff.exe" check . --select E,F,W,UP,B,I,SIM,PERF,ASYNC,RUF
& ".\venv\Scripts\ruff.exe" format --check .
& ".\venv\Scripts\pyright.exe" --level error app
```
Expected: all clean (Django is in typeshed-fallback stubs, so pyright resolves it; if Django module resolution fails, that's a pyright environment issue — ruff + tests still gate).

**Step 2: Full test suite**

Run: `& ".\venv\Scripts\python.exe" app\manage.py test -v 2`
Expected: all tests PASS (JobModel 1, FormatPicker 4, RunJob 2, View 6, Startup 2 = 15).

**Step 3: Manual end-to-end (real download)**

Run:
```powershell
& ".\venv\Scripts\python.exe" app\manage.py runserver
```
Open `http://127.0.0.1:8000`, log in, submit `https://www.youtube.com/watch?v=tS1PFlTmpuU`, watch progress poll, wait for done, download the file, verify it plays (should be 1080p like the CLI test). Also submit a garbage URL and confirm the failed state shows the error and no 500.

**Step 4: CLI still works**

Run: `& ".\venv\Scripts\python.exe" main.py`
Expected: usage message prints (main.py untouched).

---

## Task 12: Final review + commit

- `git status` clean; all above commits in place.
- Confirm `requirements.txt` has unpinned yt-dlp.
- Report to user: what to fill in for VPS deploy (SECRET_KEY, ADMIN_USER/ADMIN_PASSWORD, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, domain, reverse proxy).
