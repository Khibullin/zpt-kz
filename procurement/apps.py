from django.apps import AppConfig


class ProcurementConfig(AppConfig):
    name = 'procurement'
    def ready(self):
        from . import retail_signals  # noqa: F401
