from datetime import timedelta
from fractions import Fraction
from django.test import TestCase, override_settings
from django.utils import timezone
from core.models import Request, Seller, RequestInteraction, RequestSellerNotification
from core.services.request_connections import response_metrics, recommendations


@override_settings(TWO_WAY_REQUESTS_ENABLED=True)
class ResponseRankingTests(TestCase):
    def setUp(self):
        self.sellers = [Seller.objects.create(name=name, whatsapp='77001112233', city='Алматы',
            transport_type='car', sells_new_parts=True, all_brands=True, all_categories=True,
            direct_contacts_enabled=True) for name in ('Молчит', 'Отвечает', 'Новый')]
        self.req = Request.objects.create(brand='Toyota', model='Camry', city='Алматы',
            transport_type='car', category='Фильтр', public_summary='Масляный фильтр', seller_feed_enabled=True)

    def notice(self, seller, **kwargs):
        fields = dict(request=self.req, seller=seller, channel='telegram', status='sent', message_id=1)
        fields.update(kwargs)
        notice = RequestSellerNotification.objects.create(**fields)
        RequestSellerNotification.objects.filter(pk=notice.pk).update(updated_at=timezone.now()-timedelta(hours=73))
        return notice

    def test_response_ranks_above_neutral_and_silent_without_changing_access(self):
        for seller in self.sellers[:2]: self.notice(seller)
        RequestInteraction.objects.create(request=self.req, seller=self.sellers[1], actor='seller', kind='out_of_stock')
        metrics = response_metrics(self.sellers)
        self.assertEqual([metrics[s.pk]['score'] for s in self.sellers], [Fraction(1,3), Fraction(2,3), Fraction(1,2)])
        self.assertEqual([x['seller'].pk for x in recommendations(self.req)], [self.sellers[i].pk for i in (1,2,0)])
        self.assertFalse(RequestInteraction.objects.filter(kind='contact_confirmed').exists())

    def test_open_is_not_response_and_two_channels_count_once(self):
        seller = self.sellers[0]
        self.notice(seller)
        self.notice(seller, channel='whatsapp', status='manual', message_id=None)
        RequestInteraction.objects.create(request=self.req, seller=seller, actor='seller', kind='opened')
        self.assertEqual(response_metrics([seller])[seller.pk]['received'], 1)
        self.assertEqual(response_metrics([seller])[seller.pk]['responded'], 0)

    def test_unknown_fresh_irrelevant_and_revoked_are_not_penalties(self):
        seller = self.sellers[0]
        notice = self.notice(seller, status='unknown')
        self.assertEqual(response_metrics([seller])[seller.pk]['received'], 0)
        RequestSellerNotification.objects.filter(pk=notice.pk).update(status='sent', updated_at=timezone.now())
        self.assertEqual(response_metrics([seller])[seller.pk]['received'], 0)
        RequestSellerNotification.objects.filter(pk=notice.pk).update(updated_at=timezone.now()-timedelta(hours=73))
        self.req.search_scope='city'; self.req.city='Астана'; self.req.save()
        self.assertEqual(response_metrics([seller])[seller.pk]['received'], 0)
        self.req.city='Алматы'; self.req.access_revoked=True; self.req.save()
        self.assertEqual(response_metrics([seller])[seller.pk]['received'], 0)

    def test_late_response_restores_score_and_buyer_click_does_not(self):
        seller = self.sellers[0]; self.notice(seller)
        RequestInteraction.objects.create(request=self.req, seller=seller, actor='buyer', kind='whatsapp_click')
        self.assertEqual(response_metrics([seller])[seller.pk]['responded'], 0)
        RequestInteraction.objects.create(request=self.req, seller=seller, actor='seller', kind='interest')
        self.assertEqual(response_metrics([seller])[seller.pk]['responded'], 1)
