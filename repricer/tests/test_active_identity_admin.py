from django.contrib.admin.models import LogEntry, CHANGE
from django.urls import reverse
from django.test import TestCase
from repricer.tests import test_apify_output as fixtures


class ActiveIdentityAdminTests(TestCase):
    setUp = fixtures.ApifyOutputTests.setUp

    def test_article_correction_is_audited_and_cannot_change_price_or_stock(self):
        self.staff.is_superuser = True
        self.staff.save()
        url = reverse('admin:repricer_kaspiactiveexportrow_change', args=[self.unknown.pk])
        response = self.client.post(url, {
            'merchant_sku': 'F081109111HD', 'price': '1', 'quantity': '999',
            'master_sku': 'forged', '_save': 'Save',
        })
        self.assertEqual(response.status_code, 302)
        self.unknown.refresh_from_db()
        self.assertEqual(self.unknown.merchant_sku, 'F081109111HD')
        self.assertEqual(self.unknown.master_sku, 'UNMAPPED')
        self.assertEqual(self.unknown.price, 5000)
        self.assertEqual(self.unknown.quantity, 2)
        self.assertIsNone(self.unknown.listing_id)
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(self.unknown.pk), user=self.staff, action_flag=CHANGE).exists())
