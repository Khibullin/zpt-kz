from django.test import TestCase

from core.models import MARKET_SCOPE_FOREIGN, SellerLead
from core.services.seller_lead_market import qualify_market


class SellerLeadMarketSafetyTests(TestCase):
    def test_russian_identity_overrides_kz_search_city(self):
        lead = SellerLead.objects.create(
            name='Автозапчасти УРАЛ, отправим по РФ',
            city='Уральск',
            whatsapp='79512325963',
            website_url='https://example.xn--p1ai/',
        )

        scope, evidence = qualify_market(lead)

        self.assertEqual(scope, MARKET_SCOPE_FOREIGN)
        self.assertIn('identity=', evidence)
        self.assertIn('phone=+7-9xx', evidence)
        self.assertIn('domain=.xn--p1ai', evidence)
