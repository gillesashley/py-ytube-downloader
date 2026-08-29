from django.urls import path

from downloader import views

urlpatterns = [
    path("", views.index, name="index"),
    path("submit/", views.submit, name="submit"),
    path("status/<int:job_id>/", views.status, name="status"),
    path("delete/<int:job_id>/", views.delete, name="delete"),
    path("download/<int:job_id>/", views.download_file, name="download_file"),
]
