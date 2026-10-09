from unittest.mock import Mock, patch

from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory, TestCase, override_settings

from core.admin import EditorialPageAdmin
from core.models import EditorialPage


class EditorialAIAdminActionTests(TestCase):
    def setUp(self):
        self.admin = EditorialPageAdmin(EditorialPage, AdminSite())
        self.admin.message_user = Mock()
        self.request = RequestFactory().post('/admin/core/editorialpage/')
        self.page = EditorialPage.objects.create(
            title='Draft example', slug='draft-example',
            meta_description='Draft requiring review', body='Unverified text',
            status='draft',
        )

    @override_settings(EDITORIAL_AI_ENABLED=False)
    @patch('core.editorial_ai.improve_draft_with_ai')
    def test_flag_prevents_ai_execution(self, improve):
        self.admin.improve_selected_drafts_with_ai(
            self.request, EditorialPage.objects.filter(pk=self.page.pk),
        )
        improve.assert_not_called()
        self.assertEqual(EditorialPage.objects.get(pk=self.page.pk).status, 'draft')

    @override_settings(EDITORIAL_AI_ENABLED=True, OPENAI_API_KEY='example')
    @patch('core.editorial_ai.improve_draft_with_ai')
    def test_no_candidate_does_not_run_ai(self, improve):
        self.admin.improve_selected_drafts_with_ai(
            self.request, EditorialPage.objects.filter(pk=self.page.pk),
        )
        improve.assert_not_called()
        self.assertEqual(EditorialPage.objects.get(pk=self.page.pk).status, 'draft')
