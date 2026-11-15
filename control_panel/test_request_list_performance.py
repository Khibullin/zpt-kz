from django.db import connection
from django.http import QueryDict
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from core.models import Match, Request, SellerRequestPageEvent
from control_panel.selectors.parts_requests import list_parts_requests
from control_panel.tests import _request, _seller
from core.services.seller_request_access import create_seller_request_access


class RequestListPerformanceTests(TestCase):
    def test_independent_history_totals_and_filters(self):
        request = _request()
        first = _seller()
        second = _seller(whatsapp='77015550002')
        Match.objects.create(request=request,seller=first,status='sent')
        Match.objects.create(request=request,seller=second,status='failed',sent_at=timezone.now())
        # Repeated page opens count distinct sellers, while outcome events count
        # independently of the number of matches on the same request.
        for kind,seller in [('page_open',first),('page_open',first),('page_open',second),
                            ('whatsapp_click',first),('call_click',second),
                            ('out_of_stock',first),('cannot_fulfill',second)]:
            access=create_seller_request_access(request=request,seller=seller)
            SellerRequestPageEvent.objects.create(access=access,request=request,seller=seller,event_type=kind)
        result=list_parts_requests(QueryDict('opened=yes&contact=yes&stage=active'))
        self.assertEqual(result['total'],1)
        row=result['rows'][0]
        self.assertEqual((row.sellers_received,row.opened,row.whatsapp,row.called),(2,2,1,1))
        self.assertEqual(row.result,'Нет в наличии, Не могу выполнить')
        self.assertEqual(list_parts_requests(QueryDict('opened=no'))['total'],0)

    def test_history_is_aggregated_only_after_pagination(self):
        oldest=_request()
        Request.objects.bulk_create([Request(transport_type='car',brand='Toyota',model='Camry',
            city='Алматы',phone='77019990011',description='Part') for _ in range(55)])
        with CaptureQueriesContext(connection) as captured:
            result=list_parts_requests(QueryDict('page=1'))
        self.assertEqual(result['total'],56)
        self.assertEqual(len(result['rows']),50)
        self.assertNotIn(oldest.pk,[row.pk for row in result['rows']])
        # A request with many matches and events must not generate their Cartesian
        # product in a list or paginator count query.
        for query in captured:
            sql=query['sql']
            self.assertFalse('JOIN "core_match"' in sql and 'JOIN "core_sellerrequestpageevent"' in sql)
        self.assertLessEqual(len(captured),11)

    def test_bulk_stages_match_latest_operator_decisions_and_completed_priority(self):
        from django.contrib.admin.models import LogEntry,CHANGE
        from django.contrib.contenttypes.models import ContentType
        from django.contrib.auth import get_user_model
        from control_panel.request_workspace import with_workspace_stage,STAGE_PREFIX
        user=get_user_model().objects.create_user('stage-auditor')
        seller=_seller()
        waiting=_request();done=_request(purchase_result='purchased');new=_request();active=_request()
        Match.objects.create(request=waiting,seller=seller,status='sent')
        for request,message in [(waiting,'active'),(waiting,'waiting'),(done,'waiting'),(active,'active')]:
            LogEntry.objects.create(user=user,content_type=ContentType.objects.get_for_model(Request),
                object_id=str(request.pk),object_repr='Request',action_flag=CHANGE,
                change_message=STAGE_PREFIX+message)
        expected=dict(with_workspace_stage(Request.objects.all()).values_list('pk','workspace_stage'))
        result=list_parts_requests(QueryDict())
        self.assertEqual({row.pk:row.stage for row in result['rows']},expected)
        self.assertEqual(expected[waiting.pk],'waiting')
        self.assertEqual(expected[done.pk],'done')
