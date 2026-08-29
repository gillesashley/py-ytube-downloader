# AGENTS.md

YouTube downloader webapp: Django (`app/`) containerized with Docker for VPS deployment (branch `webapp`). Windows dev machine; everything runs through the repo venv.

## Commands (PowerShell, from repo root)

```powershell
# Tests — MUST pass `downloader` as arg: plain `manage.py test` from repo root
# discovers 0 tests (app/ is not a Python package). 
& ".\venv\Scripts\python.exe" app\manage.py test downloader

# Lint + format (extended ruleset is the gate; `--select` list is load-bearing)
& ".\venv\Scripts\ruff.exe" check . --select E,F,W,UP,B,I,SIM,PERF,ASYNC,RUF
& ".\venv\Scripts\ruff.exe" format .

# Type check — `pyright` alone (pyrightconfig.json pins app/ + venv). Requires
# django-stubs in the venv (listed commented in requirements.txt; install manually).
& ".\venv\Scripts\pyright.exe"

# Migrations + dev server
& ".\venv\Scripts\python.exe" app\manage.py makemigrations downloader
& ".\venv\Scripts\python.exe" app\manage.py migrate
& ".\venv\Scripts\python.exe" app\manage.py runserver
```

Order matters: fix `ruff` before `pyright`; run tests after model changes (`makemigrations` + `migrate` first).

## Architecture

- `app/` = Django 6.1 project: package `config` (settings), app `downloader`. The app is intentionally NOT a package dir (`app/` has no `__init__.py`) — `manage.py test downloader` not `manage.py test`.
- `services.run_job(job_id, resolution=None)` = the whole download pipeline (yt-dlp extract → pick 1080p/720p or the requested height → merge with best audio → progress to DB). Runs in a daemon thread per job spawned by the `submit` view.
- Threading model: **one gunicorn worker with threads** — the status poll must reach the worker holding the thread. Never scale workers without adding a DB-claim worker process first (see design doc `docs/plans/2026-08-29-webapp-design.md`).
- Frontend: vanilla JS polling every 2s; CSS lives in `app/static/css/base.css` (project static dir via `STATICFILES_DIRS`; container runs `collectstatic`, whitenoise serves it).
- **Cancellation**: the public `cancel` view sets `job.cancelled`; the yt-dlp progress hook checks it and raises `DownloadCancelled`, which `run_job` catches BEFORE the generic `except Exception` (marks status `cancelled`, cleans partial files). Don't reorder those handlers.
- Auth: the download flow (index/submit/status/cancel/download_file) is PUBLIC by design; only
  `delete` and `/admin/` require login (login_required decorator + Django admin's default).
- Views are class-based (ListView for index, View subclasses with `http_method_names = ["post"]`
  carrying `# noqa: RUF012` — django-stubs types that attr as an instance var, don't "fix" it with ClassVar).
- Download files go to `MEDIA_ROOT` (`app/media`); job rows track relative paths.

## Gotchas (all learned the hard way)

- **yt-dlp must stay unpinned** in requirements.txt — it breaks with YouTube changes; the pinned 2024.10.7 caused "Requested format is not available". Every container build gets latest.
- **ffmpeg**: vendored `ffmpeg/bin` used on Windows dev via `ffmpeg_location`; container uses apt ffmpeg on PATH (`FFMPEG_DIR.exists()` check in services.py). 1080p/720p streams are video-only; merging without ffmpeg fails.
- **yt-dlp `_Params` TypedDict**: Pylance's bundled stubs type-check `YoutubeDL(params=...)` strictly. Passing a plain dict errors — `opts: Any` in services.py. Use the same pattern for new yt-dlp calls.
- **Never save the stale Job instance wholesale after progress hooks**: the instance predates raw `update()` progress writes; plain `save()` resets progress to 0. Terminal saves use `save(update_fields=[...])`.
- `run_job` catches `Exception` (not just `DownloadError`) deliberately — a job stuck in RUNNING is worse than a visible failure. Don't narrow it.
- `DownloaderConfig.ready()` touches the DB (orphan-job recovery) and must suppress `OperationalError` (runs before first migrate). Django emits a known "DB access in ready()" RuntimeWarning — harmless.
- Use `extract_info(url, download=True)` on the SAME YoutubeDL instance to get `requested_downloads` populated (separate `ydl.download()` re-extracts into a fresh dict).
- URLs use plain names (no `app_name` namespace in `downloader/urls.py`) — `reverse("index")`, templates `{% url 'submit' %}` depend on it.
- SQLite; no OPTIONS timeout tuning (5s default). If "database is locked" ever bites, add `'OPTIONS': {'timeout': 30}`.

## Django-specific conventions

- Settings are env-driven: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `ADMIN_USER`, `ADMIN_PASSWORD` (the last two feed `ensure_admin`, which creates the superuser at container startup).
- Django 6.1 stock template quirk: `settings.py` has a `MAILERS` block and an AUTH_PASSWORD_VALIDATORS line that violates 88-char (marked `# noqa: E501`) — both stock, don't "fix" them.
- Generated migrations are checked in; `dependencies`/`operations` carry `# noqa: RUF012`.
- Tests: Django `TestCase` in `app/downloader/tests.py`, `MockYDL`/`BoomYDL`/`patch_ydl` fakes for yt-dlp (mirror `requested_downloads`-on-`download=True`). Type-check note: pyright doesn't narrow on `assertIsNotNone` — use `assert x is not None` before subscripting.
- Design/plan docs live in `docs/plans/` (design + 12-task implementation plan for the webapp).

## Verification baseline (all currently green)

`ruff check` (extended select) clean · `pyright` 0 errors · `manage.py test downloader` green · `docker compose up -d --build` + browser check for UI changes.
