from unittest.mock import Mock
from django.test import TestCase, override_settings
from core.editorial_ai import improve_draft_with_ai
from core.models import EditorialPage


class EditorialAIFailClosedTests(TestCase):
    def test_generation_disabled_without_external_call(self):
        with self.assertRaises(ValueError):
            improve_draft_with_ai(1, session=Mock())

    @override_settings(EDITORIAL_AI_ENABLED=True, OPENAI_API_KEY='')
    def test_generation_without_credentials(self):
        with self.assertRaises(ValueError):
            improve_draft_with_ai(1, session=Mock())

    @override_settings(EDITORIAL_AI_ENABLED=True, OPENAI_API_KEY='placeholder')
    def test_generation_requires_existing_article(self):
        with self.assertRaises(EditorialPage.DoesNotExist):
            improve_draft_with_ai(99999, session=Mock())
