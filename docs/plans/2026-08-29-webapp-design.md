# Design: yt-dlp Downloader Webapp (Django + Docker)

Date: 2026-08-29
Status: Approved by user

## Goal

Turn the existing CLI downloader (`main.py`) into a private webapp deployable on the
user's VPS, containerized with Docker. Single user for now; multi-user must remain a
cheap future step (Django's auth already provides the hook).

## Constraints

- Only 1080p and 720p downloads (prefer 1080, fall back to 720). Videos with neither
  are marked failed with a clear error, never crash.
- yt-dlp is NOT pinned in requirements.txt — stale pins broke the CLI earlier (2024.10.7
  failure). Every image build pulls the latest yt-dlp.
- ffmpeg required for merging (video-only 1080p/720p streams) — installed in image via apt.
- Private: every page behind Django auth; single superuser created from env vars.

## Architecture

- Django project in `app/`: `config/` (settings, urls, wsgi) + `downloader/` app.
- Existing `main.py` stays as the CLI (untouched); `downloader/services.py` holds the
  adapted download logic (pick 1080/720 + audio, progress hook). Small duplication accepted.
- **Job model**: url, title, status (queued/running/done/failed — no-quality videos are
  marked failed with the error message), error, file
  path, progress percent, created_at. Doubles as download history.
- **Background execution**: one thread per job inside a SINGLE gunicorn worker (gthread,
  threads=4). Polling `status/<id>/` reaches the owning worker. Multiple workers would
  break this; documented upgrade path is a separate DB-claim worker process (Job model
  unchanged). Marked `# ponytail:` in code.
- **Flow**: homepage form (URL only — no resolution choice, matches CLI behavior) → Job →
  thread runs yt-dlp with progress hook writing percent to DB → frontend polls every 2s →
  done: FileResponse streams the file. Delete button per job (disk hygiene).
- **Storage**: SQLite (zero-config); downloads in Docker volume `/app/media`. Postgres
  only when multi-user demands it.
- **Startup**: AppConfig.ready marks orphaned `running` jobs as failed (crash/deploy
  recovery).

## Container

- `python:3.12-slim`; apt install ffmpeg; pip install requirements (Django, gunicorn,
  yt-dlp); single gunicorn worker with threads; superuser created from env (SECRET_KEY,
  ADMIN_USER/ADMIN_PASSWORD, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS).
- docker-compose.yml: web service + media volume. Domain/TLS added later via user's
  existing reverse proxy.

## Error handling

- yt-dlp DownloadError → job failed + error text shown in UI, no 500s.
- No 720/1080 format → job failed with "no 720p or 1080p format" message (same wording as CLI).

## Verification

- ruff + pyright clean (as today's CLI standard).
- Manual end-to-end: run locally, download the test video, check progress/status/file.
