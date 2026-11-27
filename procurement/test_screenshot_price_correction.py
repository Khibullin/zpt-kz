from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from .models import (Supplier, Offer, Membership, BuyingComparisonMaterial,
                     KaspiPriceAudit, PermanentBuyingItem, Activity)
from .comparison_screenshot_ai import parsed_screenshot_price


class ScreenshotPriceParsingTests(SimpleTestCase):
    def test_full_tenge_amount(self):
        for text in ('6 201 ₸', '6\u202f201 тенге', '6201', '6 201,00 KZT'):
            self.assertEqual(parsed_screenshot_price(text), Decimal('6201'))
        for text in ('', '0', '-6201', 'NaN', '6201 / 2067 x 3', '6201 и 28627', '6 20 1'):
            with self.assertRaises(ValueError):
                parsed_screenshot_price(text)


class ScreenshotPriceCorrectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('price_manager')
        Membership.objects.create(user=self.user, role='sales')
        self.client.force_login(self.user)
        self.offer = Offer.objects.create(supplier=Supplier.objects.create(name='Phaeton DC'),
            brand='ASVA', article='0121-087', name='Наконечник', cost=4238, stock='4')
        self.audit = KaspiPriceAudit.objects.create(offer=self.offer, source_filename='current.json',
            product_id='180093729', min_price=28627, cost_snapshot=4238,
            commission_snapshot=Decimal('12.5'), expenses_snapshot=250,
            profit=Decimal('20560.62'), roi=Decimal('485.15'),
            card_url='https://kaspi.kz/shop/p/asva-0121-087-180093729/')
        self.other = KaspiPriceAudit.objects.create(offer=self.offer, source_filename='old.json',
            product_id='180093729', min_price=28000, cost_snapshot=4238,
            commission_snapshot=Decimal('12.5'), expenses_snapshot=250, profit=20012, roi=472)
        self.material = BuyingComparisonMaterial.objects.create(offer=self.offer,
            source_filename='current.json', kaspi_product_id='180093729',
            kaspi_data={'manual_screenshots': ['data:image/jpeg;base64,evidence']})
        self.item = PermanentBuyingItem.objects.create(offer=self.offer, title='ASVA 0121-087',
            cost_snapshot=4238, kaspi_price_snapshot=28627, commission_snapshot=Decimal('12.5'),
            expenses_snapshot=250, profit_snapshot=Decimal('20560.62'), roi_snapshot=Decimal('485.15'))
        self.url = reverse('procurement:comparison_confirm_screenshots', args=[self.offer.pk])
        self.payload = {'source_file': 'current.json', 'product_id': '180093729',
            'brand': 'ASVA', 'article': '0121-087', 'screenshot_price': '6 201 ₸',
            'screenshot_product_id': '177988801', 'name': 'ASVA рулевой наконечник 0121-087'}

    def test_updates_scoped_calculation_and_card_preserves_history(self):
        response = self.client.post(self.url, self.payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.audit.refresh_from_db(); self.other.refresh_from_db()
        self.offer.refresh_from_db(); self.item.refresh_from_db(); self.material.refresh_from_db()
        self.assertEqual(self.audit.min_price, Decimal('6201'))
        self.assertEqual(self.audit.profit, Decimal('937.88'))
        self.assertLess(self.audit.roi, 50)
        self.assertEqual(self.audit.product_id, '177988801')
        self.assertTrue(self.audit.card_url.endswith('-177988801/'))
        self.assertEqual(self.material.kaspi_product_id, '177988801')
        self.assertEqual(self.other.min_price, Decimal('28000'))
        self.assertEqual(self.offer.cost, Decimal('4238'))
        self.assertEqual(self.item.kaspi_price_snapshot, Decimal('28627'))
        history = Activity.objects.get(action='kaspi_screenshot_price')
        self.assertEqual(history.details['previous']['min_price'], '28627.00')
        page = self.client.get(reverse('procurement:comparison', args=[self.offer.pk]),
                               {'apify_source': 'current.json'})
        self.assertContains(page, 'Kaspi — цена со скриншота')
        self.assertEqual(page.context['kaspi_price'], Decimal('6201'))
        listing = self.client.get(reverse('procurement:dashboard'),
            {'mode': 'best', 'apify_source': 'current.json', 'q': '0121-087'})
        self.assertNotContains(listing, '20560')
        self.assertNotContains(listing, 'comparison/' + str(self.offer.pk) + '/')

    def test_invalid_price_or_identity_does_not_write(self):
        for change in ({'screenshot_price': '2067 x 3'}, {'brand': 'OTHER'}, {'article': '0121-088'}):
            response = self.client.post(self.url, {**self.payload, **change})
            self.assertEqual(response.status_code, 400)
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.min_price, Decimal('28627'))
        self.assertFalse(Activity.objects.filter(action='kaspi_screenshot_price').exists())

    def test_characteristics_only_preserves_price(self):
        response = self.client.post(self.url, {**self.payload, 'screenshot_price': '', 'screenshot_product_id': ''})
        self.assertEqual(response.status_code, 200)
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.min_price, Decimal('28627'))


class AutomaticScreenshotFillTests(ScreenshotPriceCorrectionTests):
    def recognize(self, fields=None, side_effect=None):
        from unittest.mock import patch
        result = {**self.payload, 'product_photo_box': None}
        result.pop('source_file'); result.pop('product_id')
        if fields:
            result.update(fields)
        with patch('procurement.comparison_clipboard.recognize_screenshots',
                   return_value=result, side_effect=side_effect):
            return self.client.post(reverse('procurement:comparison_recognize_screenshots',
                args=[self.offer.pk]), {'source_file': 'current.json',
                'product_id': '180093729', 'auto_apply': 'yes'})

    def test_automatic_recognition_saves_price_and_preserves_prior_fields(self):
        self.material.kaspi_data.update(fitment='Toyota Land Cruiser', manual_confirmed_at='old')
        self.material.save()
        response = self.recognize({'fitment': ''})
        self.assertTrue(response.json()['auto_saved'], response.content)
        self.audit.refresh_from_db(); self.material.refresh_from_db()
        self.assertEqual(self.audit.min_price, Decimal('6201'))
        self.assertEqual(self.material.kaspi_status, 'screenshot_extracted')
        self.assertEqual(self.material.kaspi_data['fitment'], 'Toyota Land Cruiser')
        self.assertNotIn('manual_confirmed_at', self.material.kaspi_data)
        self.assertIn('auto_applied_at', self.material.kaspi_data)
        self.assertEqual(self.audit.identity_status, 'needs_review')
        self.assertTrue(Activity.objects.get(action='kaspi_screenshot_price').details['automatic'])

    def test_mismatch_or_ambiguous_price_keeps_existing_calculation(self):
        for change in ({'brand': 'OTHER'}, {'article': ''}, {'screenshot_price': '2067 x 3'}):
            response = self.recognize(change)
            self.assertFalse(response.json()['auto_saved'])
            self.assertTrue(response.json()['auto_error'])
            self.audit.refresh_from_db()
            self.assertEqual(self.audit.min_price, Decimal('28627'))
        self.assertFalse(Activity.objects.filter(action='kaspi_screenshot_price').exists())

    def test_changed_screenshots_during_recognition_are_not_applied(self):
        def changed(*args, **kwargs):
            BuyingComparisonMaterial.objects.filter(pk=self.material.pk).update(
                kaspi_data={'manual_screenshots': ['data:image/jpeg;base64,new']})
            return {**self.payload, 'product_photo_box': None}
        response = self.recognize(side_effect=changed)
        self.assertFalse(response.json()['auto_saved'])
        self.assertIn('изменились', response.json()['auto_error'])
        self.audit.refresh_from_db()
        self.assertEqual(self.audit.min_price, Decimal('28627'))

    def test_layout_starts_open_and_has_three_table_columns(self):
        response = self.client.get(reverse('procurement:comparison', args=[self.offer.pk]),
                                   {'apify_source': 'current.json'})
        self.assertContains(response, '<details class="eep-tools" open>')
        self.assertContains(response, '<th scope="col">Параметр</th>')
        self.assertContains(response, "auto_apply:'yes'")
        self.assertContains(response, 'await recognizeAndFill();')
