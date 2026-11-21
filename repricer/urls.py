from django.urls import path

from .views import repricer_dashboard
from .manual_collection import manual_start, manual_step, manual_pause, manual_resume


app_name = "repricer"

urlpatterns = [
    path("", repricer_dashboard, name="dashboard"),
    path("manual/start/", manual_start, name="manual_start"),
    path("manual/<int:run_id>/step/", manual_step, name="manual_step"),
    path("manual/<int:run_id>/pause/", manual_pause, name="manual_pause"),
    path("manual/<int:run_id>/resume/", manual_resume, name="manual_resume"),
]
