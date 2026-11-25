"""Measure private page work without recording SQL, user data or query strings."""
import logging
from time import perf_counter
from django.db import connection

logger = logging.getLogger('gunicorn.error')


class OfficeTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith('/office/') or not request.user.is_authenticated:
            return self.get_response(request)
        started = perf_counter()
        sql_ms = 0.0
        queries = 0

        def measured(execute, sql, params, many, context):
            nonlocal sql_ms, queries
            tick = perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                queries += 1
                sql_ms += (perf_counter() - tick) * 1000

        with connection.execute_wrapper(measured):
            response = self.get_response(request)
        elapsed = (perf_counter() - started) * 1000
        timing = f'app;dur={elapsed:.1f}, db;dur={sql_ms:.1f}, queries;desc="{queries}"'
        response['Server-Timing'] = ', '.join(filter(None, (response.get('Server-Timing'), timing)))
        logger.info('Office performance: view=%s status=%s app_ms=%.1f db_ms=%.1f queries=%s',
                    getattr(getattr(request, 'resolver_match', None), 'view_name', 'unknown'),
                    response.status_code, elapsed, sql_ms, queries)
        return response
