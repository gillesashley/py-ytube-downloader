from django.urls import path

from downloader import views

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("submit/", views.SubmitView.as_view(), name="submit"),
    path("status/<int:job_id>/", views.StatusView.as_view(), name="status"),
    path("cancel/<int:job_id>/", views.CancelView.as_view(), name="cancel"),
    path("delete/<int:job_id>/", views.DeleteView.as_view(), name="delete"),
    path(
        "download/<int:job_id>/", views.DownloadFileView.as_view(), name="download_file"
    ),
]
