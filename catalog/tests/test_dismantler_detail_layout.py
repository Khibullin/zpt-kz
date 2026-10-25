from datetime import time
import re
from django.test import TestCase
from core.models import Seller, SellerWorkInterval
from core.services.seller_card_display import build_seller_card
from core.templatetags.dismantler_detail import dismantler_profile


class DismantlerDetailLayoutTests(TestCase):
    def setUp(self):
        self.seller = Seller.objects.create(name='060, центр авторазбора', city='Кокшетау',
            whatsapp='77001234567', transport_type='car', is_dismantler=True,
            address='улица Жумабека Ташенова, 193/2',
            market_location='улица Жумабека Ташенова, 193/2', category='Multi', brand='Multi')

    def test_no_duplicate_address_or_placeholder_about(self):
        page = self.client.get(f'/parts-seller/{self.seller.pk}/')
        # JSON-LD also contains the address; it is not duplicate visible content.
        visible_html = re.sub(r'<script\b[^>]*>.*?</script>', '', page.content.decode(), flags=re.S)
        self.assertEqual(visible_html.count(self.seller.address), 1)
        self.assertNotContains(page, 'Multi')
        self.assertContains(page, 'Легковые автомобили')
        self.assertNotContains(page, '<dt>Марки</dt>')
        self.assertContains(page, 'tel:+77001234567')
        self.assertContains(page, 'https://wa.me/77001234567')

    def test_schedule_groups_only_consecutive_known_days(self):
        for day in [0, 1, 3]:
            SellerWorkInterval.objects.create(seller=self.seller, weekday=day,
                opens=time(10), closes=time(18))
        card = build_seller_card(self.seller)
        groups = dismantler_profile(self.seller, card)['schedule']
        self.assertEqual([row['days'] for row in groups], ['Пн–Вт', 'Чт'])
        self.assertEqual(len(card['schedule']), 3)
