from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponseNotFound

from control_panel.http import apply_control_headers

LOGIN_URL = '/admin/login/'

_NOT_FOUND = (
    '<!DOCTYPE html><html lang="ru"><head>'
    '<meta charset="utf-8">'
    '<meta name="robots" content="noindex, nofollow">'
    '<title>Не найдено — ZPT.KZ Control</title>'
    '</head><body>Не найдено</body></html>'
)


def control_staff_required(view_func):
    @staff_member_required(login_url=LOGIN_URL)
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        try:
            response = view_func(request, *args, **kwargs)
        except Http404:
            response = HttpResponseNotFound(_NOT_FOUND)
        return apply_control_headers(response)

    return wrapped
