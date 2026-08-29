import threading
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView

from downloader.models import Job
from downloader.services import run_job


# ponytail: download flow (index/submit/status/cancel/download_file) is public by
# design; only destructive ops (delete) and /admin/ require login
class IndexView(ListView):
    model = Job
    template_name = "downloader/index.html"
    context_object_name = "jobs"

    def get_queryset(self):
        return Job.objects.order_by("-created_at")[:20]


class SubmitView(View):
    http_method_names = ["post"]  # noqa: RUF012  # Django View contract

    def post(self, request):
        url = request.POST.get("url", "").strip()
        resolution = request.POST.get("resolution")
        if resolution not in ("1080", "720"):
            resolution = None
        if not url.startswith(("http://", "https://")):
            messages.error(request, "Enter a valid URL starting with http(s)://.")
        else:
            job = Job.objects.create(url=url)
            # ponytail: thread inside single gunicorn worker; swap for a DB-claim
            # worker process when multi-user/concurrency demands it
            threading.Thread(
                target=run_job, args=(job.pk, resolution), daemon=True
            ).start()
            messages.success(request, "Download started.")
        return redirect("index")


class StatusView(View):
    def get(self, request, job_id):
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


class CancelView(View):
    """Public: flag a running/queued job for cancellation; the worker picks it up."""

    http_method_names = ["post"]  # noqa: RUF012  # Django View contract

    def post(self, request, job_id):
        job = get_object_or_404(Job, pk=job_id)
        if job.status in (Job.Status.QUEUED, Job.Status.RUNNING):
            job.cancelled = True
            job.save(update_fields=["cancelled"])
        return redirect("index")


class DeleteView(LoginRequiredMixin, View):
    http_method_names = ["post"]  # noqa: RUF012  # Django View contract

    def post(self, request, job_id):
        job = get_object_or_404(Job, pk=job_id)
        if job.file_path:
            (settings.MEDIA_ROOT / job.file_path).unlink(missing_ok=True)
        job.delete()
        return redirect("index")


class DownloadFileView(View):
    def get(self, request, job_id):
        job = get_object_or_404(Job, pk=job_id)
        path = settings.MEDIA_ROOT / job.file_path if job.file_path else None
        if not path or not path.exists():
            raise Http404
        return FileResponse(
            path.open("rb"), as_attachment=True, filename=Path(job.file_path).name
        )
