import threading
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from downloader.models import Job
from downloader.services import run_job


# ponytail: download flow (index/submit/status/download_file) is public by
# design; only destructive ops (delete) and /admin/ require login
def index(request):
    jobs = Job.objects.order_by("-created_at")[:20]
    return render(request, "downloader/index.html", {"jobs": jobs})


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


def status(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    return JsonResponse(
        {
            "status": job.status,
            "progress": job.progress,
            "title": job.title,
            "error": job.error,
            "done": job.status == Job.Status.DONE,
            "failed": job.status == Job.Status.FAILED,
        }
    )


@login_required
@require_POST
def delete(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    if job.file_path:
        (settings.MEDIA_ROOT / job.file_path).unlink(missing_ok=True)
    job.delete()
    return redirect("index")


def download_file(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    path = settings.MEDIA_ROOT / job.file_path if job.file_path else None
    if not path or not path.exists():
        raise Http404
    return FileResponse(
        path.open("rb"), as_attachment=True, filename=Path(job.file_path).name
    )
