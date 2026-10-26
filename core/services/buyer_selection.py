"""Manual buyer contacts, isolated from automatic routing and seller consent."""
from copy import copy
from django.core.paginator import Paginator
from core.models import BuyerSellerSelection, SellerChannelConnection
from core.services import request_connections as service


def catalog_request(req, city):
    scoped = copy(req)
    # This view scope is the buyer's explicit directory choice, never saved to routing.
    scoped.search_scope = 'kazakhstan' if city == '*' else 'city'
    scoped.city = req.city if not city or city == '*' else city
    return scoped


def eligible(req, city='', seller_ids=None):
    scoped = catalog_request(req, city)
    direct = service.recommendations(scoped)
    direct = [item for item in direct if not item['seller'].is_test_seller
              and (seller_ids is None or item['seller'].pk in seller_ids)]
    items = direct + service.directory_suggestions(scoped,
        excluded_ids=[item['seller'].pk for item in direct], seller_ids=seller_ids)
    return [item for item in items if not item['seller'].is_test_seller and
            item['seller'].is_active and not item['seller'].is_paused]


def catalog_page(req, city, number):
    scoped = catalog_request(req, city)
    direct = [item for item in service.recommendations(scoped) if not item['seller'].is_test_seller]
    excluded = [item['seller'].pk for item in direct]
    from django.core.cache import cache
    from hashlib import sha256
    signature = repr((req.pk, str(req.access_token), req.brand, req.model, req.category,
                      req.transport_type, req.is_dismantler_request, req.city, city, excluded))
    key = 'buyer-shortlist-order:' + sha256(signature.encode()).hexdigest()
    ids = cache.get(key)
    if ids is None:
        ids = service._directory_order(scoped, excluded)
        cache.set(key, ids, 60)
    from core.models import Seller
    tests = set(Seller.objects.filter(pk__in=ids, is_test_seller=True).values_list('pk', flat=True))
    page = Paginator(direct + [{'directory_id': pk} for pk in ids if pk not in tests], 10).get_page(number)
    wanted = [item['directory_id'] for item in page if 'directory_id' in item]
    current = {item['seller'].pk: item for item in service.directory_suggestions(scoped, seller_ids=wanted)} if wanted else {}
    page.object_list = [current[item['directory_id']] if 'directory_id' in item else item
                       for item in page if 'directory_id' not in item or item['directory_id'] in current]
    service.decorate_buyer_seller_cards(page.object_list)
    return page


def default_message(req, selection):
    from core.services.manual_short_links import short_url
    vehicle = ' '.join(str(v) for v in (req.brand, req.model, req.year) if v)
    text = f'Здравствуйте! Ищу запчасть на {vehicle}.\n{req.description}\nГород: {req.city}.'
    if req.requested_quantity:
        text += f'\nКоличество: {req.requested_quantity} шт.'
    text += '\nПодскажите наличие, производителя, артикул, цену и условия гарантии.'
    text += '\nМоя заявка ZPT.KZ: ' + short_url(selection.access)
    connected = SellerChannelConnection.objects.filter(seller=selection.seller,
        telegram_id__isnull=False, telegram_consent=True).exists()
    if not connected:
        text += '\n\nПолучайте подходящие заявки покупателей через Telegram ZPT.KZ. Подключитесь и выберите категории, марки и города: ' + short_url(selection.access, telegram=True)
    return text
