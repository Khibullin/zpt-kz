"""Approved profitable-buying rules must be identical for admin and sales manager."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import KaspiBuyingCandidate, Membership, Offer, Preferences, Supplier


class ApprovedBuyingRulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user('buying-rule-admin')
        cls.manager = get_user_model().objects.create_user('buying-rule-manager')
        for user, role in ((cls.admin, 'admin'), (cls.manager, 'sales')):
            Membership.objects.create(user=user, role=role)
            Preferences.objects.create(user=user, commission=Decimal('12.5'), expenses=Decimal('250'))
        cls.supplier = Supplier.objects.create(name='Omega')
        items = [
            ('EXACT', 'Деталь ровно 5000', 'Детали', '5000', '15000'),
            ('LOW', 'Деталь дешевле 5000', 'Детали', '4999', '15000'),
            ('VALID', 'Деталь дороже 5000', 'Детали', '6000', '9000'),
            ('SPRING', 'Пружина подвески', 'Подвеска', '6000', '15000'),
            ('ENGSPRING', 'Coil spring', 'Suspension', '6000', '15000'),
            ('LOWROI', 'Мало доходности', 'Детали', '6000', '8999'),
        ]
        for article, name, category, cost, price in items:
            offer = Offer.objects.create(
                supplier=cls.supplier, article=article, brand='BR',
                name=name, category=category, cost=Decimal(cost), stock='20',
            )
            KaspiBuyingCandidate.objects.create(
                product_id='buying-rules-' + article,
                title=name, brand='BR', offer=offer, match_status='candidate',
                minimum_price=Decimal(price),
                url='https://kaspi.kz/shop/p/buying-rules-' + article.lower() + '/',
            )

    def test_same_threshold_for_admin_and_manager(self):
        url = reverse('procurement:dashboard')
        for user in (self.admin, self.manager):
            self.client.force_login(user)
            response = self.client.get(url, {'mode': 'best', 'supplier': ''})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['total'], 1)
            self.assertEqual(
                [row['offer'].article for row in response.context['page'].object_list],
                ['VALID'],
            )
            self.assertContains(response, 'Правила 19.11.2026')
            self.assertIn('no-store', response.get('Cache-Control', ''))

    def test_all_items_remain_in_unfiltered_catalogue(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('procurement:dashboard'), {'supplier': self.supplier.pk})
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.context['total'], 6)
