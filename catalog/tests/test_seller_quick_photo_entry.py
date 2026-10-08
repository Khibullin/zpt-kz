from django.test import TestCase
from django.urls import reverse

from core.services.seller_identity import create_unified_seller_account


PASSWORD = 'QuickPhotoPass123!'


class SellerQuickPhotoEntryTests(TestCase):
    def setUp(self):
        self.user, self.request_seller, self.profile = create_unified_seller_account(
            name='Quick Photo Shop',
            whatsapp='77015550991',
            password=PASSWORD,
            city='Алматы',
        )
        self.client.force_login(self.user)

    def test_dashboard_has_quick_photo_entry(self):
        response = self.client.get(reverse('seller_dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'{reverse("add_product")}?mode=photo',
        )
        self.assertContains(response, 'Добавить по фото')

    def test_photo_mode_marks_product_form_for_autostart(self):
        response = self.client.get(reverse('add_product'), {'mode': 'photo'})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['photo_mode'])
        self.assertContains(
            response,
            'data-autostart-article-recognition="1"',
        )

    def test_regular_add_product_does_not_autostart_photo_picker(self):
        response = self.client.get(reverse('add_product'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['photo_mode'])
        self.assertContains(
            response,
            'data-autostart-article-recognition="0"',
        )
