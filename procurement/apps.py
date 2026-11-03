from django.apps import AppConfig


class ProcurementConfig(AppConfig):
    name = 'procurement'
    def ready(self):
        from . import filter_options  # noqa: F401
        from . import retail_signals  # noqa: F401
