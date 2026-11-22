"""Regression tests for the approved Omega-only permanent buying register.

A saved Kaspi search candidate is NOT itself a buying row; a permanent offer
is one row per supplier SKU. New purchase selection requires evidence.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (KaspiBuyingCandidate, KaspiPriceAudit, Membership, Offer, Preferences,
                     PermanentBuyingItem, Selection, Supplier)


class PermanentBuyingPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('buying-page-check')
        Membership.objects.create(user=cls.user, role='sales')
        Preferences.objects.create(
            user=cls.user, commission=Decimal('12.5'), expenses=Decimal('250'))
        cls.supplier = Supplier.objects.create(name='Omega')
        cls.offer = Offer.objects.create(
            supplier=cls.supplier, brand='BR', article='TEST-1',
            name='Радиатор', applicability='Toyota Camry',
            cost=Decimal('10000'), stock='20', min_order=2,
            photo_url='https://example.com/part.jpg')
        cls.candidate = KaspiBuyingCandidate.objects.create(
            product_id='123456789', title='BR TEST-1 радиатор',
            brand='BR', url='https://kaspi.kz/shop/p/test-123456789/',
            minimum_price=Decimal('40000'),
            city_offers=[{'city': 'Алматы', 'sellers': []}],
            offer=cls.offer, match_status='candidate')
        cls.saved = PermanentBuyingItem.objects.create(
            offer=cls.offer, source='apify', product_id='123456789',
            title='BR TEST-1 радиатор',
            kaspi_url='https://kaspi.kz/shop/p/test-123456789/',
            cost_snapshot=Decimal('10000'), kaspi_price_snapshot=Decimal('40000'),
            commission_snapshot=Decimal('12.5'), expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('24750'), roi_snapshot=Decimal('247.5000'),
            identity_status='candidate')

    def setUp(self):
        self.client.force_login(self.user)

    def dashboard(self, **query):
        # These legacy fixture tests explicitly exercise the preserved archive;
        # the user-facing default is the Apify-rechecked shortlist.
        return self.client.get(reverse('procurement:dashboard'),
                               {'mode': 'best', 'rechecked': '0', **query})

    def test_one_buying_row_per_supplier_sku(self):
        KaspiBuyingCandidate.objects.create(
            product_id='123456790', title='BR TEST-1 variant', brand='BR',
            offer=self.offer, match_status='candidate',
            minimum_price=Decimal('50000'))
        response = self.dashboard()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        self.assertEqual(response.context['total'], 1)
        self.assertContains(response, 'BR TEST-1 радиатор')
        self.assertContains(response, 'Цена Kaspi не подтверждена')
        self.assertContains(response, 'Требует проверки до заказа')

    def test_old_below_50_remains_visible_without_purchase_permission(self):
        old_offer = Offer.objects.create(
            supplier=self.supplier, brand='OLD', article='H-14',
            name='Исторический товар', cost=Decimal('10000'), stock='0')
        PermanentBuyingItem.objects.create(
            offer=old_offer, source='historical', title='Исторический товар',
            cost_snapshot=Decimal('10000'), kaspi_price_snapshot=Decimal('15000'),
            commission_snapshot=Decimal('12.5'), expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('2875'), roi_snapshot=Decimal('28.7500'),
            identity_status='historical')
        response = self.dashboard()
        self.assertEqual(response.context['total'], 2)
        self.assertContains(response, 'Доходность ниже 50%')
        self.assertContains(response, 'Исторический товар')
        self.assertEqual(response.context['page'].object_list[0]['offer'].pk,
                         self.offer.pk)

    def test_filters_use_permanent_records(self):
        self.assertEqual(self.dashboard(q='Camry').context['total'], 1)
        self.assertEqual(self.dashboard(q='no-such-part').context['total'], 0)
        self.assertEqual(self.dashboard(min_profit='30000').context['total'], 0)

    def test_search_brand_and_article_across_fields(self):
        # Regression: the site used to return zero for "RAMEDER 20A040152".
        self.offer.brand = 'RAMEDER'
        self.offer.article = '20A040152'
        self.offer.save(update_fields=['brand', 'article'])
        response = self.dashboard(q='RAMEDER 20A040152')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        self.assertEqual(response.context['page'].object_list[0]['offer'].pk,
                         self.offer.pk)

    def test_search_brand_and_article_on_default_audited_buying(self):
        self.offer.brand = 'RAMEDER'
        self.offer.article = '20A040152'
        self.offer.save(update_fields=['brand', 'article'])
        KaspiPriceAudit.objects.create(
            offer=self.offer, source_filename='kaspi.json',
            min_price=Decimal('20000'), product_id='123456789',
            card_url='https://kaspi.kz/shop/p/rameder-123456789/',
            card_title='Rameder 20A040152 1 шт', seller_name='Seller',
            cost_snapshot=Decimal('10000'), commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'), profit=Decimal('7250'),
            roi=Decimal('72.5000'), card_ids=['123456789'])
        response = self.client.get(reverse('procurement:dashboard'),
                                   {'mode': 'best', 'supplier': str(self.supplier.pk),
                                    'q': 'RAMEDER 20A040152'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)

    def test_rechecked_shortcut_shows_only_audited_items(self):
        other = Offer.objects.create(
            supplier=self.supplier, brand='OTHER', article='B2',
            name='Ещё товар', cost=Decimal('9000'), stock='7')
        PermanentBuyingItem.objects.create(
            offer=other, source='historical', title=other.name,
            cost_snapshot=Decimal('9000'), kaspi_price_snapshot=Decimal('15000'),
            commission_snapshot=Decimal('12.5'), expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('3875'), roi_snapshot=Decimal('43.0556'))
        KaspiPriceAudit.objects.create(
            offer=self.offer, source_filename='apify-recheck.json',
            min_price=Decimal('20000'), product_id='123456789',
            card_url='https://kaspi.kz/shop/p/test-123456789/',
            card_title='BR TEST-1 радиатор', seller_name='Seller',
            cost_snapshot=Decimal('10000'), commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'), profit=Decimal('7250'),
            roi=Decimal('72.5000'), card_ids=['123456789'])
        response = self.dashboard(rechecked='1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        self.assertContains(response, 'Было: 40')
        self.assertNotContains(response, 'Показаны только перепроверенные товары')
        self.assertNotContains(response, 'Ещё товар')

    def test_default_buying_shows_rechecked_without_toggle(self):
        other = Offer.objects.create(
            supplier=self.supplier, brand='OTHER', article='B2',
            name='Архивный товар', cost=Decimal('9000'), stock='5')
        PermanentBuyingItem.objects.create(
            offer=other, source='historical', title=other.name,
            cost_snapshot=Decimal('9000'), kaspi_price_snapshot=Decimal('16000'),
            commission_snapshot=Decimal('12.5'), expenses_snapshot=Decimal('250'),
            profit_snapshot=Decimal('4750'), roi_snapshot=Decimal('52.7778'))
        KaspiPriceAudit.objects.create(
            offer=self.offer, source_filename='apify.json',
            min_price=Decimal('20000'), product_id='123456789',
            card_url='https://kaspi.kz/shop/p/test-123456789/',
            card_title='BR TEST-1 радиатор', seller_name='Seller',
            cost_snapshot=Decimal('10000'), commission_snapshot=Decimal('12.5'),
            expenses_snapshot=Decimal('250'), profit=Decimal('7250'),
            roi=Decimal('72.5000'), card_ids=['123456789'])
        response = self.client.get(reverse('procurement:dashboard'), {'mode': 'best'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        self.assertContains(response, 'BR TEST-1 радиатор')
        self.assertNotContains(response, 'Архивный товар')
        self.assertNotContains(response, 'Перепроверенные — сравнить цены')
        self.assertNotContains(response, '>Все товары</a>')

    def test_unverified_can_be_chosen_into_draft_but_not_ordered(self):
        response = self.dashboard()
        self.assertContains(response, 'Отметить в список закупки:')
        response = self.client.post(
            reverse('procurement:select_many'), {'offer_ids': [str(self.offer.pk)]},
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Selection.objects.get(
            user=self.user, offer=self.offer).quantity, 2)
        basket = self.client.get(reverse('procurement:basket'))
        self.assertContains(basket, 'Предварительный отбор')
        self.assertContains(basket, 'Требует проверки перед заказом')
        confirm = self.client.get(reverse('procurement:purchase_confirm',
                                           args=[self.supplier.pk]))
        self.assertEqual(confirm.status_code, 200)
        self.assertContains(confirm, 'Заказ пока недоступен')
        self.assertNotContains(confirm, 'Создать заказ в ZPT.KZ')
        post = self.client.post(reverse('procurement:purchase_confirm',
                                        args=[self.supplier.pk]), {})
        self.assertEqual(post.status_code, 400)
        from .models import PurchaseOrder
        self.assertFalse(PurchaseOrder.objects.filter(user=self.user).exists())

    def test_manual_verified_candidate_with_seller_is_selectable(self):
        self.candidate.city_offers = [
            {'city': 'Алматы', 'sellers': [{'name': 'Seller', 'price': '40000'}]}]
        self.candidate.save()
        self.saved.identity_status = 'verified'
        self.saved.save()
        response = self.dashboard()
        self.assertContains(response, 'Отметить в список закупки:')
        response = self.client.post(
            reverse('procurement:select_many'), {'offer_ids': [str(self.offer.pk)]},
            HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Selection.objects.get(
            user=self.user, offer=self.offer).quantity, 2)
        # Preserved quantity may not be reset by repeated adds.
        self.client.post(reverse('procurement:select_many'),
                         {'offer_ids': [str(self.offer.pk)]},
                         HTTP_ACCEPT='application/json')
        self.assertEqual(Selection.objects.get(
            user=self.user, offer=self.offer).quantity, 2)
