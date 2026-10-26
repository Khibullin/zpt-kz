from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_safe
from core.models import SellerRequestAccess
from core.services.manual_telegram_invitation import invitation_access, bot_url


@require_safe
def short_link(request, code, telegram=False):
    access = get_object_or_404(SellerRequestAccess, short_code=code)
    if access.is_expired():
        response = HttpResponse('Срок действия ссылки истёк.', status=410)
    elif not invitation_access(access.token):
        response = HttpResponse('Заявка недоступна.', status=403)
    else:
        # The destination page still applies every buyer, seller and lifecycle check.
        target = bot_url(access) if telegram else reverse('seller_request_link', args=[access.token])
        response = HttpResponseRedirect(target)
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response
