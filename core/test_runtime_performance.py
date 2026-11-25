from pathlib import Path
import runpy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import SimpleTestCase, RequestFactory, override_settings
from core import process_memory
from core.office_timing import OfficeTimingMiddleware
from orders.context_processors import cart_count
from orders.constants import SESSION_CART_KEY


class RuntimePerformanceTests(SimpleTestCase):
    def test_allocator_release_is_bounded_and_throttled(self):
        with patch.object(process_memory, '_last_release', 0), \
             patch.object(process_memory, 'resident_bytes', side_effect=[180*process_memory.MIB, 130*process_memory.MIB, 130*process_memory.MIB]), \
             patch.object(process_memory.time, 'monotonic', return_value=100), \
             patch.object(process_memory.gc, 'collect') as collect, \
             patch.object(process_memory.ctypes, 'CDLL') as libc:
            self.assertEqual(process_memory.release_idle_memory(), 130*process_memory.MIB)
            self.assertEqual(process_memory.release_idle_memory(), 130*process_memory.MIB)
            collect.assert_called_once()
            libc.return_value.malloc_trim.assert_called_once_with(0)

    def test_low_memory_requests_skip_collection(self):
        with patch.object(process_memory, 'resident_bytes', return_value=50*process_memory.MIB), \
             patch.object(process_memory.gc, 'collect') as collect:
            process_memory.release_idle_memory()
            collect.assert_not_called()

    def test_platform_without_native_trim_still_serves_requests(self):
        with patch.object(process_memory, '_last_release', 0), \
             patch.object(process_memory, 'resident_bytes', return_value=150*process_memory.MIB), \
             patch.object(process_memory.time, 'monotonic', return_value=100), \
             patch.object(process_memory.gc, 'collect'), \
             patch.object(process_memory.ctypes, 'CDLL', side_effect=OSError):
            self.assertEqual(process_memory.release_idle_memory(), 150*process_memory.MIB)

    def test_oversized_http_worker_uses_graceful_exit_flag(self):
        config=runpy.run_path(str(Path(__file__).resolve().parents[1]/'gunicorn.conf.py'))
        worker=SimpleNamespace(alive=True,log=MagicMock())
        with patch.object(process_memory,'release_idle_memory',return_value=193*process_memory.MIB):
            config['post_request'](worker,None,None,None)
        self.assertFalse(worker.alive)

    @override_settings(DOMAIN_BOUNDARY_ENABLED=False)
    def test_office_render_does_not_read_or_merge_retail_cart(self):
        request=RequestFactory().get('/office/accounting/')
        request.user=SimpleNamespace(is_authenticated=True)
        request.session={SESSION_CART_KEY:{'12':3}}
        with patch('orders.context_processors.CartManager') as manager:
            self.assertEqual(cart_count(request),{'cart_count':0})
            manager.assert_not_called()
        self.assertEqual(request.session[SESSION_CART_KEY],{'12':3})

    @override_settings(DOMAIN_BOUNDARY_ENABLED=False)
    def test_public_guest_read_preserves_session_and_quantity(self):
        request=RequestFactory().get('/')
        request.user=AnonymousUser()
        request.session={}
        self.assertEqual(cart_count(request),{'cart_count':0})
        self.assertEqual(request.session,{})
        request.session={SESSION_CART_KEY:{'1':2,'2':3}}
        self.assertEqual(cart_count(request),{'cart_count':5})

    def test_timing_is_private_and_does_not_log_parameters(self):
        request=RequestFactory().get('/office/accounting/?q=private-content')
        request.user=SimpleNamespace(is_authenticated=True)
        response=OfficeTimingMiddleware(lambda r:HttpResponse('ok'))(request)
        self.assertIn('app;dur=',response['Server-Timing'])
        request=RequestFactory().get('/')
        request.user=AnonymousUser()
        self.assertNotIn('Server-Timing',OfficeTimingMiddleware(lambda r:HttpResponse('ok'))(request))
