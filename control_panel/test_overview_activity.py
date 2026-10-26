from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from control_panel.tests import _staff, _seller, _request
from core.models import RequestDispatch, SellerRequestPageEvent
from core.services.seller_request_access import create_seller_request_access

@override_settings(ALLOWED_HOSTS=['*'])
class OverviewActivityTests(TestCase):
    def test_access_periods_and_matching_records(self):
        sent_url = '/control/activity/sent/'
        self.assertEqual(self.client.get(sent_url).status_code, 302)
        staff, _ = _staff()
        self.client.force_login(staff)
        seller = _seller(name='Магазин отчёта')
        req = _request()
        RequestDispatch.objects.create(request=req, seller=seller, wave_number=1,
            position_number=1, status='sent', scheduled_at=timezone.now(), sent_at=timezone.now())
        access = create_seller_request_access(request=req, seller=seller)
        event = SellerRequestPageEvent.objects.create(request=req, seller=seller, access=access, event_type='page_open')
        SellerRequestPageEvent.objects.filter(pk=event.pk).update(created_at=timezone.now()-timedelta(days=10))
        response = self.client.get(sent_url+'?period=today')
        self.assertContains(response, 'Магазин отчёта')
        self.assertEqual(response.context['page'].paginator.count, 1)
        self.assertEqual(response.context['rows'][0]['request_id'], req.pk)
        self.assertEqual(self.client.get('/control/activity/opened/?period=7d').context['page'].paginator.count, 0)
        self.assertEqual(self.client.get('/control/activity/opened/?period=30d').context['page'].paginator.count, 1)
        self.assertEqual(self.client.get('/control/activity/invalid/').status_code, 404)
        self.assertContains(self.client.get('/control/?period=30d'), '/control/activity/opened/?period=30d')
        self.assertContains(self.client.get('/control/?period=30d'), '/control/activity/sent/?period=30d')
