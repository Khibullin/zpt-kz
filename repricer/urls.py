from django.urls import path

from .views import repricer_dashboard


app_name = "repricer"

urlpatterns = [
    path("", repricer_dashboard, name="dashboard"),
]
