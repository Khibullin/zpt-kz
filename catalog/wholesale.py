"""Public wholesale storefront helpers.

Anonymous-visible permanent wholesale prices come from active
ProductPriceTier rows. This module must not change guest retail catalog
pricing or resolve_commercial_price().
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db.models import Prefetch
from django.utils.text import slugify

from catalog.applicability import extra_compatibility_text, grouped_applicability
from catalog.commercial import check_stock_qty
from catalog.models import Brand, CarModel, Product, ProductPriceTier, SellerProfile


WHOLESALE_TYPE_CABIN = 'cabin_filter'
WHOLESALE_TYPE_OIL = 'oil_filter'
WHOLESALE_TYPE_SPARK = 'spark_plug'

WHOLESALE_TYPE_CHOICES = (
    (WHOLESALE_TYPE_CABIN, 'Салонные фильтры'),
    (WHOLESALE_TYPE_OIL, 'Масляные фильтры'),
    (WHOLESALE_TYPE_SPARK, 'Свечи зажигания'),
)

WHOLESALE_TYPE_LABELS = dict(WHOLESALE_TYPE_CHOICES)

_TYPE_RULES = (
    (WHOLESALE_TYPE_CABIN, ('салонн', 'cabin')),
    (WHOLESALE_TYPE_OIL, ('маслян', 'oil')),
    (WHOLESALE_TYPE_SPARK, ('свеч', 'spark')),
)


def normalize_wholesale_text(value):
    text = ' '.join(str(value or '').strip().lower().replace('\n', ' ').split())
    return text.replace('ё', 'е')


def wholesale_product_type(product):
    """Best-effort type from title/category. Does not change Product schema."""
    haystack = normalize_wholesale_text(
        ' '.join(
            part
            for part in (
                getattr(product, 'title', ''),
                getattr(getattr(product, 'category', None), 'name', ''),
            )
            if part
        )
    )
    if not haystack:
        return ''
    for type_key, tokens in _TYPE_RULES:
        if any(token in haystack for token in tokens):
            return type_key
    return ''


def wholesale_product_type_label(type_key):
    return WHOLESALE_TYPE_LABELS.get(type_key, '')


def public_wholesale_unit_price(product):
    """Permanent public wholesale unit price from an active ProductPriceTier.

    One constant price per product: the active tier with the lowest min_qty.
    Returns None if the product has no active tier.
    """
    cached = getattr(product, '_prefetched_objects_cache', {})
    if 'price_tiers' in cached:
        tiers = [
            tier for tier in product.price_tiers.all()
            if getattr(tier, 'is_active', False)
        ]
        tiers.sort(key=lambda tier: (tier.min_qty, tier.pk or 0))
        return int(tiers[0].price) if tiers else None
    tier = (
        product.price_tiers.filter(is_active=True)
        .order_by('min_qty', 'id')
        .first()
    )
    return int(tier.price) if tier is not None else None


def wholesale_products_qs(seller):
    if seller is None or not seller.wholesale_enabled:
        return Product.objects.none()
    return (
        Product.objects.owned_by_seller(seller)
        .filter(status='active', publish_to_sellers=True)
        .filter(price_tiers__is_active=True)
        .distinct()
        .select_related(
            'brand',
            'car_model',
            'car_model__brand',
            'category',
            'seller_profile',
        )
        .prefetch_related(
            'selected_brands',
            Prefetch(
                'selected_models',
                queryset=CarModel.objects.select_related('brand'),
            ),
            Prefetch(
                'price_tiers',
                queryset=ProductPriceTier.objects.filter(is_active=True).order_by(
                    'min_qty', 'id'
                ),
            ),
        )
    )


def seller_has_wholesale_storefront(seller):
    if seller is None or not seller.wholesale_enabled:
        return False
    return wholesale_products_qs(seller).exists()


def is_wholesale_eligible(product, seller=None):
    if product is None or product.status != 'active' or not product.publish_to_sellers:
        return False
    if public_wholesale_unit_price(product) is None:
        return False
    if seller is None:
        return True
    if not seller.wholesale_enabled:
        return False
    return Product.objects.owned_by_seller(seller).filter(pk=product.pk).exists()


def resolve_wholesale_owner(product):
    if product is None:
        return None
    if product.seller_profile_id:
        return product.seller_profile
    name = (product.seller_name or '').strip()
    if not name:
        return None
    return SellerProfile.objects.select_related('wholesale_terms').filter(
        name__iexact=name
    ).order_by('pk').first()


@dataclass
class PublicWholesaleQuote:
    quantity: int
    unit_price: int | None
    can_buy: bool
    reason: str = ''
    price_type: str = 'wholesale'
    label: str = 'Опт'

    @property
    def total_price(self):
        if not self.can_buy or self.unit_price is None:
            return None
        return int(self.unit_price) * int(self.quantity)


def quote_public_wholesale(product, quantity, seller=None):
    """Public wholesale quote for guests. Independent of SellerProfile login."""
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return PublicWholesaleQuote(0, None, False, 'Укажите количество больше 0.')
    if qty <= 0:
        return PublicWholesaleQuote(qty, None, False, 'Укажите количество больше 0.')

    owner = seller or resolve_wholesale_owner(product)
    if owner is None or not owner.wholesale_enabled:
        return PublicWholesaleQuote(
            qty, None, False, 'Оптовая витрина этого продавца недоступна.'
        )
    if not is_wholesale_eligible(product, owner):
        return PublicWholesaleQuote(
            qty, None, False, 'Этот товар нельзя купить оптом.'
        )

    stock_error = check_stock_qty(product, qty)
    if stock_error:
        return PublicWholesaleQuote(qty, None, False, stock_error)

    unit_price = public_wholesale_unit_price(product)
    if unit_price is None:
        return PublicWholesaleQuote(
            qty, None, False, 'Для товара не задана оптовая цена.'
        )
    return PublicWholesaleQuote(qty, unit_price, True)


def wholesale_fitment_text(product):
    groups = grouped_applicability(product)
    bits = []
    for group in groups:
        brand_name = (group['brand'].name or '').strip()
        models = [
            (model.name or '').strip()
            for model in group['models']
            if (model.name or '').strip()
        ]
        if models:
            bits.append(f'{brand_name} {", ".join(models)}'.strip())
        elif brand_name:
            bits.append(brand_name)
    extra = extra_compatibility_text(product, groups)
    if extra:
        bits.append(re.sub(r'\s+', ' ', extra).strip())
    return '; '.join(bit for bit in bits if bit)


def public_wholesale_prefetch(queryset):
    """Prefetch seller_profile, vehicle M2M, and active tiers for public cards."""
    return queryset.select_related(
        'seller_profile',
        'seller_profile__wholesale_terms',
    ).prefetch_related(
        'selected_brands',
        Prefetch(
            'selected_models',
            queryset=CarModel.objects.select_related('brand'),
        ),
        Prefetch(
            'price_tiers',
            queryset=ProductPriceTier.objects.filter(is_active=True).order_by(
                'min_qty', 'id'
            ),
        ),
    )


def public_wholesale_owner_from_product(product):
    if product is None:
        return None
    if getattr(product, 'seller_profile_id', None):
        return product.seller_profile
    return getattr(product, 'seller', None)


def has_public_wholesale_offer(product, seller=None):
    """Whether the product should show public wholesale UI.

    Uses prefetched seller_profile/seller and price_tiers when available.
    """
    if product is None or product.status != 'active' or not product.publish_to_sellers:
        return False
    owner = seller or public_wholesale_owner_from_product(product)
    if owner is None or not getattr(owner, 'wholesale_enabled', False):
        return False
    if public_wholesale_unit_price(product) is None:
        return False
    owner_id = getattr(owner, 'pk', None)
    if product.seller_profile_id and owner_id and product.seller_profile_id != owner_id:
        return False
    return True


def public_wholesale_min_order_qty(product, seller=None):
    """Seller-level wholesale minimum for public card labels. No extra queries."""
    owner = seller or public_wholesale_owner_from_product(product)
    if owner is None:
        return None
    try:
        qty = int(getattr(owner, 'wholesale_min_order_qty', 0) or 0)
    except (TypeError, ValueError):
        return None
    return qty if qty > 0 else None


def attach_public_wholesale_flags(products):
    """Attach public wholesale flags and unit price without extra queries.

    Uses prefetched price_tiers from public_wholesale_prefetch().
    Sets:
      product.has_public_wholesale
      product.wholesale_unit_price  (None when ineligible)
      product.wholesale_min_order_qty  (None when ineligible)
      product.public_stock  (only when wholesale-eligible or stock_qty is set)
    """
    for product in products:
        has_offer = has_public_wholesale_offer(product)
        product.has_public_wholesale = has_offer
        product.wholesale_unit_price = (
            public_wholesale_unit_price(product) if has_offer else None
        )
        product.wholesale_min_order_qty = (
            public_wholesale_min_order_qty(product) if has_offer else None
        )
        if has_offer or getattr(product, 'stock_qty', None) is not None:
            product.public_stock = public_stock_status(product)
        else:
            product.public_stock = None
    return products


def public_stock_status(product):
    """Public availability copy. NULL stock stays orderable (unknown remainder)."""
    qty = getattr(product, 'stock_qty', None)
    if qty is None:
        return {
            'code': 'unknown',
            'label': 'Наличие уточняется',
            'can_buy': True,
            'qty': None,
        }
    qty = int(qty)
    if qty <= 0:
        return {
            'code': 'out',
            'label': 'Нет в наличии',
            'can_buy': False,
            'qty': 0,
        }
    return {
        'code': 'in',
        'label': f'В наличии: {qty} шт.',
        'can_buy': True,
        'qty': qty,
    }


def public_stock_xlsx_value(product):
    qty = getattr(product, 'stock_qty', None)
    if qty is None:
        return 'Уточняется при заказе'
    return int(qty)


def remaining_wholesale_qty(total_qty, seller):
    minimum = int(getattr(seller, 'wholesale_min_order_qty', 0) or 0)
    if minimum <= 0:
        return 0
    return max(0, minimum - int(total_qty or 0))


def wholesale_car_brands(products_qs):
    """Car brands from primary brand, model brand, and selected_brands."""
    brand_ids = set()
    brand_ids.update(
        products_qs.exclude(brand_id=None).values_list('brand_id', flat=True)
    )
    brand_ids.update(
        products_qs.exclude(car_model__brand_id=None).values_list(
            'car_model__brand_id',
            flat=True,
        )
    )
    brand_ids.update(products_qs.values_list('selected_brands', flat=True))
    brand_ids.discard(None)
    return Brand.objects.filter(pk__in=brand_ids).order_by('name')


_FILENAME_UNSAFE = re.compile(r'[^A-Za-z0-9]+')

VAT_INCLUDED = 'included'
VAT_EXCLUDED = 'excluded'
VAT_UNSPECIFIED = 'unspecified'
DELIVERY_PAYER_BUYER = 'buyer'
DELIVERY_PAYER_SELLER = 'seller'


def get_seller_wholesale_terms(seller):
    if seller is None:
        return None
    return getattr(seller, 'wholesale_terms', None)


def build_wholesale_terms_snapshot(seller):
    """JSON-safe copy of current seller wholesale terms.

    Missing SellerWholesaleTerms must not invent VAT, DPD, or 100% prepayment.
    min_order_qty comes from SellerProfile so later seller edits do not rewrite
    already placed orders.
    """
    snapshot = {
        'vat_mode': VAT_UNSPECIFIED,
        'prepayment_percent': None,
        'confirm_stock_before_payment': False,
        'provides_invoice': False,
        'provides_waybill': False,
        'provides_esf': False,
        'pickup_enabled': False,
        'pickup_city': '',
        'delivery_kz_enabled': False,
        'delivery_payer': 'agreement',
        'primary_carrier': '',
        'primary_carrier_service': '',
        'primary_carrier_url': '',
        'other_carrier_allowed': False,
        'stock_note': '',
        'min_order_qty': int(getattr(seller, 'wholesale_min_order_qty', 0) or 0)
        if seller is not None
        else 0,
    }
    terms = get_seller_wholesale_terms(seller)
    if terms is None:
        return snapshot
    percent = terms.prepayment_percent
    snapshot.update({
        'vat_mode': terms.vat_mode or VAT_UNSPECIFIED,
        'prepayment_percent': int(percent) if percent is not None else None,
        'confirm_stock_before_payment': bool(terms.confirm_stock_before_payment),
        'provides_invoice': bool(terms.provides_invoice),
        'provides_waybill': bool(terms.provides_waybill),
        'provides_esf': bool(terms.provides_esf),
        'pickup_enabled': bool(terms.pickup_enabled),
        'pickup_city': (terms.pickup_city or '').strip(),
        'delivery_kz_enabled': bool(terms.delivery_kz_enabled),
        'delivery_payer': terms.delivery_payer or 'agreement',
        'primary_carrier': (terms.primary_carrier or '').strip(),
        'primary_carrier_service': (terms.primary_carrier_service or '').strip(),
        'primary_carrier_url': (terms.primary_carrier_url or '').strip(),
        'other_carrier_allowed': bool(terms.other_carrier_allowed),
        'stock_note': (terms.stock_note or '').strip(),
    })
    return snapshot


def _document_labels(snapshot):
    labels = []
    if snapshot.get('provides_invoice'):
        labels.append('счет')
    if snapshot.get('provides_waybill'):
        labels.append('накладная')
    if snapshot.get('provides_esf'):
        labels.append('ЭСФ')
    return labels


def _join_ru(parts):
    parts = [part for part in parts if part]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f'{parts[0]} и {parts[1]}'
    return f'{", ".join(parts[:-1])} и {parts[-1]}'


def wholesale_vat_price_suffix(snapshot):
    vat_mode = (snapshot or {}).get('vat_mode')
    if vat_mode == VAT_INCLUDED:
        return 'с НДС'
    if vat_mode == VAT_EXCLUDED:
        return 'без НДС'
    return ''


def wholesale_payment_oneliner(snapshot):
    snapshot = snapshot or {}
    percent = snapshot.get('prepayment_percent')
    confirm = bool(snapshot.get('confirm_stock_before_payment'))
    if percent is not None and confirm:
        return f'{int(percent)}% предоплата после подтверждения наличия.'
    if percent is not None:
        return f'{int(percent)}% предоплата.'
    if confirm:
        return 'Наличие подтверждается перед оплатой.'
    return ''


def wholesale_storefront_condition_lines(snapshot):
    """Public storefront bullets. Skip unspecified / empty rules."""
    snapshot = snapshot or {}
    lines = []
    vat_mode = snapshot.get('vat_mode')
    if vat_mode == VAT_INCLUDED:
        lines.append('Все оптовые цены указаны с НДС')
    elif vat_mode == VAT_EXCLUDED:
        lines.append('Оптовые цены указаны без НДС')

    min_qty = int(snapshot.get('min_order_qty') or 0)
    if min_qty > 0:
        lines.append(f'Минимальный заказ — {min_qty} единиц в ассортименте')

    percent = snapshot.get('prepayment_percent')
    confirm = bool(snapshot.get('confirm_stock_before_payment'))
    if percent is not None and confirm:
        lines.append(f'{int(percent)}% предоплата после подтверждения наличия')
    elif percent is not None:
        lines.append(f'{int(percent)}% предоплата')
    elif confirm:
        lines.append('Наличие подтверждается перед оплатой')

    docs = _document_labels(snapshot)
    if len(docs) == 3:
        lines.append('Счет, накладная и ЭСФ')
    elif docs:
        label = _join_ru(docs)
        lines.append(label[:1].upper() + label[1:] if label else label)

    if snapshot.get('pickup_enabled'):
        city = (snapshot.get('pickup_city') or '').strip()
        lines.append(f'Самовывоз — {city}' if city else 'Самовывоз')

    if snapshot.get('delivery_kz_enabled'):
        carrier = (snapshot.get('primary_carrier') or '').strip()
        if carrier:
            lines.append(f'Доставка по Казахстану — {carrier}')
        else:
            lines.append('Доставка по Казахстану')
        payer = snapshot.get('delivery_payer')
        if payer == DELIVERY_PAYER_BUYER:
            lines.append('Стоимость доставки оплачивает покупатель')
        elif payer == DELIVERY_PAYER_SELLER:
            lines.append('Стоимость доставки оплачивает продавец')
        if snapshot.get('other_carrier_allowed'):
            lines.append('Другая транспортная компания — по согласованию')

    note = (snapshot.get('stock_note') or '').strip()
    if note and note not in lines:
        lines.append(note)
    return lines


def wholesale_cart_condition_lines(snapshot):
    snapshot = snapshot or {}
    lines = []
    vat_mode = snapshot.get('vat_mode')
    if vat_mode == VAT_INCLUDED:
        lines.append('цены с НДС')
    elif vat_mode == VAT_EXCLUDED:
        lines.append('цены без НДС')
    percent = snapshot.get('prepayment_percent')
    if percent is not None:
        lines.append(f'{int(percent)}% предоплата')
    if snapshot.get('confirm_stock_before_payment'):
        lines.append('наличие подтверждается перед оплатой')
    if (
        snapshot.get('delivery_kz_enabled')
        and snapshot.get('delivery_payer') == DELIVERY_PAYER_BUYER
    ):
        lines.append('доставка оплачивается покупателем')
    return lines


def wholesale_checkout_presentation(snapshot):
    snapshot = snapshot or {}
    percent = snapshot.get('prepayment_percent')
    steps = ['Менеджер подтверждает наличие.']
    if percent is not None:
        steps.append(f'Вы получаете счет на {int(percent)}% предоплату.')
    else:
        steps.append('Вы получаете счет после подтверждения заказа.')
    steps.append('После оплаты оформляются отгрузочные документы.')
    pickup = bool(snapshot.get('pickup_enabled'))
    delivery = bool(snapshot.get('delivery_kz_enabled'))
    if pickup and delivery:
        steps.append('Самовывоз или отправка транспортной компанией.')
    elif pickup:
        steps.append('Самовывоз.')
    elif delivery:
        steps.append('Отправка транспортной компанией.')
    else:
        steps.append('Самовывоз или отправка транспортной компанией.')

    carrier = (snapshot.get('primary_carrier') or '').strip()
    carrier_line = ''
    if delivery and carrier:
        carrier_line = f'Основная транспортная компания: {carrier}'
    payer_line = ''
    if delivery and snapshot.get('delivery_payer') == DELIVERY_PAYER_BUYER:
        payer_line = 'Стоимость доставки оплачивает покупатель.'
    elif delivery and snapshot.get('delivery_payer') == DELIVERY_PAYER_SELLER:
        payer_line = 'Стоимость доставки оплачивает продавец.'
    other_line = ''
    if delivery and snapshot.get('other_carrier_allowed'):
        other_line = (
            'Другую транспортную компанию можно согласовать с продавцом.'
        )
    return {
        'steps': steps,
        'carrier_line': carrier_line,
        'delivery_payer_line': payer_line,
        'other_carrier_line': other_line,
    }


def wholesale_success_presentation(snapshot):
    snapshot = snapshot or {}
    payment_lines = []
    oneliner = wholesale_payment_oneliner(snapshot)
    if oneliner:
        payment_lines.append(oneliner.rstrip('.'))
        payment_lines.append('Менеджер свяжется с вами и выставит счет.')
    else:
        payment_lines.append('Менеджер свяжется с вами и выставит счет.')

    documents_lines = []
    docs = _document_labels(snapshot)
    if len(docs) == 3:
        documents_lines.append('Счет, накладная, ЭСФ.')
    elif docs:
        label = _join_ru(docs)
        documents_lines.append(label[:1].upper() + label[1:] + '.')

    delivery_lines = []
    if snapshot.get('pickup_enabled'):
        city = (snapshot.get('pickup_city') or '').strip()
        delivery_lines.append(f'Самовывоз — {city}' if city else 'Самовывоз')
    if snapshot.get('delivery_kz_enabled'):
        carrier = (snapshot.get('primary_carrier') or '').strip()
        if carrier:
            delivery_lines.append(f'{carrier} по Казахстану.')
        else:
            delivery_lines.append('Доставка транспортной компанией по Казахстану.')
        if snapshot.get('delivery_payer') == DELIVERY_PAYER_BUYER:
            delivery_lines.append('Стоимость доставки оплачивает покупатель.')
        elif snapshot.get('delivery_payer') == DELIVERY_PAYER_SELLER:
            delivery_lines.append('Стоимость доставки оплачивает продавец.')
        if snapshot.get('other_carrier_allowed'):
            delivery_lines.append('Другую ТК можно согласовать.')
    return {
        'payment_lines': payment_lines,
        'documents_lines': documents_lines,
        'delivery_lines': delivery_lines,
    }


def wholesale_email_term_lines(snapshot):
    snapshot = snapshot or {}
    lines = []
    vat_mode = snapshot.get('vat_mode')
    if vat_mode == VAT_INCLUDED:
        lines.append('НДС: включен в цену')
    elif vat_mode == VAT_EXCLUDED:
        lines.append('НДС: не включен в цену')
    oneliner = wholesale_payment_oneliner(snapshot).rstrip('.')
    if oneliner:
        lines.append(f'Оплата: {oneliner}')
    docs = _document_labels(snapshot)
    if docs:
        lines.append(f'Документы: {", ".join(docs)}')
    delivery_bits = []
    carrier = (snapshot.get('primary_carrier') or '').strip()
    if snapshot.get('delivery_kz_enabled') and carrier:
        delivery_bits.append(carrier)
    if snapshot.get('pickup_enabled'):
        city = (snapshot.get('pickup_city') or '').strip()
        delivery_bits.append(f'самовывоз {city}'.strip() if city else 'самовывоз')
    if delivery_bits:
        lines.append('Доставка: ' + ' / '.join(delivery_bits))
    if (
        snapshot.get('delivery_kz_enabled')
        and snapshot.get('delivery_payer') == DELIVERY_PAYER_BUYER
    ):
        lines.append('Доставку оплачивает покупатель')
    return lines


def format_wholesale_terms_admin_text(snapshot):
    snapshot = snapshot or {}
    if not snapshot:
        return 'Нет снимка оптовых условий.'
    lines = wholesale_email_term_lines(snapshot)
    extras = wholesale_storefront_condition_lines(snapshot)
    merged = []
    seen = set()
    for line in lines + extras:
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(line)
    return '\n'.join(merged) if merged else 'Нет заполненных оптовых условий.'


def safe_wholesale_filename_stem(name):
    ascii_stem = _FILENAME_UNSAFE.sub('_', str(name or '').strip()).strip('_')
    if ascii_stem:
        return ascii_stem[:80]
    slug = slugify(str(name or '').strip())
    if slug:
        return slug.replace('-', '_')[:80]
    return 'wholesale'


def wholesale_price_filename(seller, day=None):
    from django.utils import timezone

    day = day or timezone.localdate()
    stem = safe_wholesale_filename_stem(getattr(seller, 'name', '') if seller else '')
    return f'{stem}_wholesale_price_{day:%Y-%m-%d}.xlsx'
