from django.apps import AppConfig


class MarketingConfig(AppConfig):
    name = 'marketing'

    def ready(self):
        from marketing.meta_template_payload_patch import install_meta_template_payload_patch

        install_meta_template_payload_patch()
