from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "dashboard"
    verbose_name = "Pressure Monitoring Dashboard"

    def ready(self):
        import dashboard.signals  # noqa: F401
