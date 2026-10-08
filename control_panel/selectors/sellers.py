from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db.models import (
    BooleanField,
    Case,
    Count,
    DateTimeField,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    Max,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.http import QueryDict
from django.shortcuts import get_object_or_404

from catalog.models import Product, SellerProfile
from core.models import (
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    CONTACT_CONSENT_STATUS_GRANTED,
    CONTACT_CONSENT_STATUS_REVOKED,
    SELLER_LEAD_BUSINESS_TYPE_CHOICES,
    SELLER_LEAD_LIFECYCLE_STATUS_CHOICES,
    SELLER_REQUEST_PAGE_EVENT_CALL_CLICK,
    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
    Match,
    PartCategory,
    Seller,
    SellerContactConsent,
    SellerLead,
    SellerRequestPageEvent,
)
from core.phone_utils import normalize_kz_phone
from core.services.seller_whatsapp_consent import get_seller_whatsapp_marketing_consent
from control_panel.display import (
    SELLER_SORT_CHOICES,
    categories_text,
    consent_source_label,
    consent_tone,
    dash,
    event_label,
    marketing_consent_label,
    match_status_label,
    normalize_seller_sort,
    request_result_label,
    vehicle_label,
)
from control_panel.selectors.common import (
    SELLER_EVENT_HISTORY_LIMIT,
    SELLER_MATCH_HISTORY_LIMIT,
    fetch_latest,
    first_value,
    paginate,
)


SOURCE_CHOICES = (
    ('base', 'База заявок'),
    ('instagram', 'Instagram'),
    ('web', 'Веб-поиск / Google'),
    ('two_gis', '2GIS'),
    ('website', 'Сайт'),
    ('manual', 'Вручную'),
)
SOURCE_LABELS = dict(SOURCE_CHOICES)
BUSINESS_TYPE_CHOICES = tuple(SELLER_LEAD_BUSINESS_TYPE_CHOICES)
BUSINESS_TYPE_LABELS = dict(BUSINESS_TYPE_CHOICES)
LIFECYCLE_CHOICES = tuple(SELLER_LEAD_LIFECYCLE_STATUS_CHOICES)
LIFECYCLE_LABELS = dict(LIFECYCLE_CHOICES)
SELLER_STAGE_CHOICES = (
    ('base', 'База заявок'),
    ('registered', 'Зарегистрирован'),
    *LIFECYCLE_CHOICES,
)
CANDIDATE_STAGE_CHOICES = (
    ('registered', 'Зарегистрирован'),
    *LIFECYCLE_CHOICES,
)
CANDIDATE_SORT_CHOICES = (
    ('recent', 'Сначала новые'),
    ('name', 'По имени'),
    ('stage', 'По этапу'),
)


@dataclass(frozen=True)
class SellerRow:
    pk: int
    name: str
    city: str
    categories: str
    is_active: bool
    receive_requests: bool
    is_paused: bool
    consent_status: str
    consent_label: str
    sent: int
    opened: int
    whatsapp: int
    called: int
    out_of_stock: int
    cannot_fulfill: int
    last_activity: datetime | None
    consent_tone: str
    source_label: str
    business_type_label: str
    stage_label: str
    has_whatsapp: bool
    products_count: int
    registered: bool


@dataclass(frozen=True)
class SellerLeadRow:
    pk: int
    name: str
    city: str
    source_label: str
    business_type_label: str
    stage_label: str
    has_whatsapp: bool
    instagram_username: str
    website_url: str
    linked_seller_id: int | None
    can_invite: bool
    can_mark_invited: bool
    admin_url: str


def _category_text(seller: Seller) -> str:
    if seller.all_categories:
        return 'Все категории'
    names = [item.name for item in seller.selected_categories.all()]
    if names:
        return ', '.join(names)
    return categories_text(seller.category)


def _consent_for_seller(seller: Seller) -> SellerContactConsent | None:
    consents = getattr(seller, 'marketing_consents', None)
    if consents is None:
        return get_seller_whatsapp_marketing_consent(seller)
    phone = normalize_kz_phone(seller.whatsapp)
    for consent in consents:
        if phone and consent.phone_normalized == phone:
            return consent
    return consents[0] if consents else None


def _consent_prefetch():
    return Prefetch(
        'contact_consents',
        queryset=SellerContactConsent.objects.filter(
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        ).order_by('-updated_at', '-id'),
        to_attr='marketing_consents',
    )


def _lead_prefetch():
    return Prefetch(
        'seller_leads',
        queryset=(
            SellerLead.objects
            .filter(duplicate_of__isnull=True)
            .prefetch_related('sources')
            .order_by('-updated_at', '-id')
        ),
        to_attr='control_leads',
    )


def _count_for_seller_subquery(model, *, filter_q=None):
    queryset = model.objects.filter(seller_id=OuterRef('pk'))
    if filter_q is not None:
        queryset = queryset.filter(filter_q)
    return (
        queryset
        .order_by()
        .values('seller_id')
        .annotate(total=Count('pk'))
        .values('total')[:1]
    )


def _last_activity_subquery():
    return (
        SellerRequestPageEvent.objects
        .filter(seller_id=OuterRef('pk'))
        .order_by()
        .values('seller_id')
        .annotate(last=Max('created_at'))
        .values('last')[:1]
    )


def _annotated_sellers():
    discovery_lead = SellerLead.objects.filter(
        request_seller_id=OuterRef('pk'),
        duplicate_of__isnull=True,
    )
    registered_profile = SellerProfile.objects.filter(
        user_id=OuterRef('user_id'),
    )
    canonical_product = Product.objects.filter(
        status='active',
        seller_profile__user_id=OuterRef('user_id'),
    )
    legacy_product = Product.objects.filter(
        status='active',
        seller_profile__isnull=True,
        seller_name=OuterRef('name'),
    )
    has_active_product = ExpressionWrapper(
        Exists(canonical_product) | Exists(legacy_product),
        output_field=BooleanField(),
    )

    return Seller.objects.annotate(
        sent_count=Coalesce(
            Subquery(
                _count_for_seller_subquery(
                    Match,
                    filter_q=Q(sent_at__isnull=False) | Q(status='sent'),
                ),
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        opened_count=Coalesce(
            Subquery(
                _count_for_seller_subquery(
                    SellerRequestPageEvent,
                    filter_q=Q(event_type='page_open'),
                ),
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        whatsapp_count=Coalesce(
            Subquery(
                _count_for_seller_subquery(
                    SellerRequestPageEvent,
                    filter_q=Q(event_type='whatsapp_click'),
                ),
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        call_count=Coalesce(
            Subquery(
                _count_for_seller_subquery(
                    SellerRequestPageEvent,
                    filter_q=Q(event_type='call_click'),
                ),
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        out_of_stock_count=Coalesce(
            Subquery(
                _count_for_seller_subquery(
                    SellerRequestPageEvent,
                    filter_q=Q(event_type='out_of_stock'),
                ),
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        cannot_fulfill_count=Coalesce(
            Subquery(
                _count_for_seller_subquery(
                    SellerRequestPageEvent,
                    filter_q=Q(event_type='cannot_fulfill'),
                ),
                output_field=IntegerField(),
            ),
            Value(0),
        ),
        last_activity=Subquery(
            _last_activity_subquery(),
            output_field=DateTimeField(),
        ),
        has_discovery_lead=Exists(discovery_lead),
        registered_profile=Exists(registered_profile),
        has_active_product=has_active_product,
    )


def _product_counts_for_sellers(sellers: list[Seller]) -> dict[int, int]:
    if not sellers:
        return {}

    counts = {seller.pk: 0 for seller in sellers}
    sellers_by_user = {
        seller.user_id: seller.pk
        for seller in sellers
        if seller.user_id
    }
    if sellers_by_user:
        for user_id, total in (
            Product.objects
            .filter(
                status='active',
                seller_profile__user_id__in=sellers_by_user,
            )
            .values_list('seller_profile__user_id')
            .annotate(total=Count('pk'))
            .values_list('seller_profile__user_id', 'total')
        ):
            seller_pk = sellers_by_user.get(user_id)
            if seller_pk:
                counts[seller_pk] += int(total or 0)

    sellers_by_name = {seller.name: seller.pk for seller in sellers if seller.name}
    if sellers_by_name:
        for seller_name, total in (
            Product.objects
            .filter(
                status='active',
                seller_profile__isnull=True,
                seller_name__in=sellers_by_name,
            )
            .values_list('seller_name')
            .annotate(total=Count('pk'))
            .values_list('seller_name', 'total')
        ):
            seller_pk = sellers_by_name.get(seller_name)
            if seller_pk:
                counts[seller_pk] += int(total or 0)

    return counts


def _source_keys_for_lead(lead: SellerLead | None) -> list[str]:
    if lead is None:
        return ['base']

    keys: list[str] = []

    def add(value: str):
        if value and value not in keys:
            keys.append(value)

    if lead.instagram_username or str(lead.source_type or '').startswith('instagram'):
        add('instagram')
    if lead.source_type == 'web_search':
        add('web')
    if lead.source_type == 'manual':
        add('manual')
    if lead.website_url:
        add('website')

    related_sources = getattr(lead, 'sources', None)
    source_rows = related_sources.all() if related_sources is not None else []
    for source in source_rows:
        source_type = str(source.source_type or '')
        if source_type == 'instagram':
            add('instagram')
        elif source_type in {'brave_search', 'web_search', 'google_places'}:
            add('web')
        elif source_type == 'two_gis':
            add('two_gis')
        elif source_type == 'website':
            add('website')
        elif source_type == 'manual':
            add('manual')

    return keys or ['manual']


def _source_label_for_lead(lead: SellerLead | None) -> str:
    keys = _source_keys_for_lead(lead)
    return ' + '.join(SOURCE_LABELS.get(key, key) for key in keys[:3])


def _seller_lead_for_row(seller: Seller) -> SellerLead | None:
    leads = getattr(seller, 'control_leads', None) or []
    return leads[0] if leads else None


def _seller_stage(seller: Seller, lead: SellerLead | None) -> tuple[str, str]:
    if bool(getattr(seller, 'registered_profile', False)):
        return 'registered', 'Зарегистрирован'
    if lead is not None:
        return (
            lead.lifecycle_status,
            LIFECYCLE_LABELS.get(lead.lifecycle_status, lead.lifecycle_status),
        )
    return 'base', 'База заявок'


def _seller_type_label(lead: SellerLead | None) -> str:
    if lead is None:
        return '—'
    return BUSINESS_TYPE_LABELS.get(lead.business_type, lead.business_type or '—')


def _seller_source_filter(queryset, source: str):
    if not source:
        return queryset
    if source == 'base':
        return queryset.filter(has_discovery_lead=False)

    base = Q(seller_leads__duplicate_of__isnull=True)
    if source == 'instagram':
        return queryset.filter(base).filter(
            Q(seller_leads__instagram_username__gt='')
            | Q(seller_leads__source_type__startswith='instagram')
            | Q(seller_leads__sources__source_type='instagram')
        ).distinct()
    if source == 'web':
        return queryset.filter(base).filter(
            Q(seller_leads__source_type='web_search')
            | Q(
                seller_leads__sources__source_type__in=(
                    'brave_search',
                    'web_search',
                    'google_places',
                )
            )
        ).distinct()
    if source == 'two_gis':
        return queryset.filter(
            base,
            seller_leads__sources__source_type='two_gis',
        ).distinct()
    if source == 'website':
        return queryset.filter(base).filter(
            Q(seller_leads__website_url__gt='')
            | Q(seller_leads__sources__source_type='website')
        ).distinct()
    if source == 'manual':
        return queryset.filter(base).filter(
            Q(seller_leads__source_type='manual')
            | Q(seller_leads__sources__source_type='manual')
        ).distinct()
    return queryset


def _seller_stage_filter(queryset, stage: str):
    if not stage:
        return queryset
    if stage == 'base':
        return queryset.filter(has_discovery_lead=False)
    if stage == 'registered':
        return queryset.filter(registered_profile=True)
    return queryset.filter(
        seller_leads__duplicate_of__isnull=True,
        seller_leads__lifecycle_status=stage,
    ).distinct()


def _seller_quick_filter(queryset, quick: str):
    if quick == 'ready':
        return _seller_stage_filter(queryset, SellerLead.LIFECYCLE_READY_TO_INVITE)
    if quick == 'invited':
        return _seller_stage_filter(queryset, SellerLead.LIFECYCLE_INVITED)
    if quick == 'no_whatsapp':
        return queryset.filter(Q(whatsapp='') | Q(whatsapp__isnull=True))
    if quick == 'dismantler':
        return queryset.filter(
            seller_leads__duplicate_of__isnull=True,
            seller_leads__business_type=SellerLead.BUSINESS_TYPE_DISMANTLER,
        ).distinct()
    if quick == 'wholesaler':
        return queryset.filter(
            seller_leads__duplicate_of__isnull=True,
            seller_leads__business_type=SellerLead.BUSINESS_TYPE_WHOLESALER,
        ).distinct()
    if quick == 'receives':
        return queryset.filter(receive_requests=True)
    if quick == 'not_receives':
        return queryset.filter(receive_requests=False)
    if quick == 'no_products':
        return queryset.filter(has_active_product=False)
    return queryset


def _seller_summary() -> dict[str, int]:
    base = _annotated_sellers()
    return {
        'total': Seller.objects.count(),
        'receives': Seller.objects.filter(receive_requests=True).count(),
        'registered': base.filter(registered_profile=True).count(),
        'no_products': base.filter(has_active_product=False).count(),
    }


def list_sellers(params: QueryDict) -> dict:
    queryset = _annotated_sellers().prefetch_related(
        'selected_categories',
        _consent_prefetch(),
        _lead_prefetch(),
    )
    city = first_value(params, 'city')
    if city:
        queryset = queryset.filter(city__icontains=city)

    active = first_value(params, 'active')
    if active == 'yes':
        queryset = queryset.filter(is_active=True)
    elif active == 'no':
        queryset = queryset.filter(is_active=False)

    receive = first_value(params, 'receive')
    if receive == 'yes':
        queryset = queryset.filter(receive_requests=True)
    elif receive == 'no':
        queryset = queryset.filter(receive_requests=False)

    consent = first_value(params, 'consent')
    granted = SellerContactConsent.objects.filter(
        seller_id=OuterRef('pk'),
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        status=CONTACT_CONSENT_STATUS_GRANTED,
    )
    revoked = SellerContactConsent.objects.filter(
        seller_id=OuterRef('pk'),
        channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
        purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        status=CONTACT_CONSENT_STATUS_REVOKED,
    )
    if consent == 'granted':
        queryset = queryset.filter(Exists(granted))
    elif consent == 'revoked':
        queryset = queryset.filter(Exists(revoked))
    elif consent == 'unknown':
        queryset = queryset.exclude(Exists(granted)).exclude(Exists(revoked))

    opened = first_value(params, 'opened')
    opened_exists = SellerRequestPageEvent.objects.filter(
        seller_id=OuterRef('pk'),
        event_type='page_open',
    )
    if opened == 'yes':
        queryset = queryset.filter(Exists(opened_exists))
    elif opened == 'no':
        queryset = queryset.exclude(Exists(opened_exists))

    activity = first_value(params, 'activity')
    activity_exists = SellerRequestPageEvent.objects.filter(seller_id=OuterRef('pk'))
    if activity == 'yes':
        queryset = queryset.filter(Exists(activity_exists))
    elif activity == 'no':
        queryset = queryset.exclude(Exists(activity_exists))

    category = first_value(params, 'category')
    if category:
        queryset = queryset.filter(
            Q(all_categories=True)
            | Q(selected_categories__name__icontains=category)
            | Q(category__icontains=category)
        ).distinct()

    search = first_value(params, 'q')
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(city__icontains=search))

    source = first_value(params, 'source')
    queryset = _seller_source_filter(queryset, source)

    business_type = first_value(params, 'business_type')
    if business_type:
        queryset = queryset.filter(
            seller_leads__duplicate_of__isnull=True,
            seller_leads__business_type=business_type,
        ).distinct()

    stage = first_value(params, 'stage')
    queryset = _seller_stage_filter(queryset, stage)

    whatsapp_state = first_value(params, 'whatsapp_state')
    if whatsapp_state == 'yes':
        queryset = queryset.exclude(whatsapp='')
    elif whatsapp_state == 'no':
        queryset = queryset.filter(whatsapp='')

    products = first_value(params, 'products')
    if products == 'yes':
        queryset = queryset.filter(has_active_product=True)
    elif products == 'no':
        queryset = queryset.filter(has_active_product=False)

    quick = first_value(params, 'quick')
    queryset = _seller_quick_filter(queryset, quick)

    sort = normalize_seller_sort(first_value(params, 'sort'))
    if sort == 'activity':
        queryset = queryset.order_by(
            F('last_activity').desc(nulls_last=True), 'name', 'id'
        )
    elif sort == 'sent':
        queryset = queryset.order_by('-sent_count', 'name', 'id')
    elif sort == 'opened':
        queryset = queryset.order_by('-opened_count', 'name', 'id')
    elif sort == 'whatsapp':
        queryset = queryset.order_by('-whatsapp_count', 'name', 'id')
    else:
        queryset = queryset.order_by('name', 'id')

    page = paginate(queryset, params)
    product_counts = _product_counts_for_sellers(page.object_list)
    rows = []
    for seller in page.object_list:
        record = _consent_for_seller(seller)
        status = record.status if record else ''
        lead = _seller_lead_for_row(seller)
        _stage_key, stage_label = _seller_stage(seller, lead)
        rows.append(
            SellerRow(
                pk=seller.pk,
                name=seller.name,
                city=dash(seller.city),
                categories=_category_text(seller),
                is_active=seller.is_active,
                receive_requests=seller.receive_requests,
                is_paused=seller.is_paused,
                consent_status=status or 'absent',
                consent_label=marketing_consent_label(status),
                sent=seller.sent_count,
                opened=seller.opened_count,
                whatsapp=seller.whatsapp_count,
                called=seller.call_count,
                out_of_stock=seller.out_of_stock_count,
                cannot_fulfill=seller.cannot_fulfill_count,
                last_activity=seller.last_activity,
                consent_tone=consent_tone(status),
                source_label=_source_label_for_lead(lead),
                business_type_label=_seller_type_label(lead),
                stage_label=stage_label,
                has_whatsapp=bool(normalize_kz_phone(seller.whatsapp)),
                products_count=product_counts.get(seller.pk, 0),
                registered=bool(seller.registered_profile),
            )
        )

    cities = list(
        Seller.objects.exclude(city='')
        .order_by('city')
        .values_list('city', flat=True)
        .distinct()
    )
    category_names = set(
        PartCategory.objects.exclude(name='')
        .order_by('name')
        .values_list('name', flat=True)
    )
    category_names.update(
        Seller.objects.exclude(category='')
        .order_by('category')
        .values_list('category', flat=True)
        .distinct()
    )
    return {
        'seller_view': 'sellers',
        'rows': rows,
        'page': page.page,
        'querystring': page.querystring,
        'total': page.total,
        'summary': _seller_summary(),
        'seller_total': Seller.objects.count(),
        'candidate_total': SellerLead.objects.filter(duplicate_of__isnull=True).count(),
        'cities': cities,
        'categories': sorted(category_names),
        'sort_choices': SELLER_SORT_CHOICES,
        'source_choices': SOURCE_CHOICES,
        'business_type_choices': BUSINESS_TYPE_CHOICES,
        'stage_choices': SELLER_STAGE_CHOICES,
        'filters': {
            'city': city,
            'active': active,
            'receive': receive,
            'consent': consent,
            'opened': opened,
            'activity': activity,
            'category': category,
            'q': search,
            'source': source,
            'business_type': business_type,
            'stage': stage,
            'whatsapp_state': whatsapp_state,
            'products': products,
            'quick': quick,
            'sort': sort,
        },
    }


def _candidate_source_filter(queryset, source: str):
    if not source:
        return queryset
    if source == 'instagram':
        return queryset.filter(
            Q(instagram_username__gt='')
            | Q(source_type__startswith='instagram')
            | Q(sources__source_type='instagram')
        ).distinct()
    if source == 'web':
        return queryset.filter(
            Q(source_type='web_search')
            | Q(
                sources__source_type__in=(
                    'brave_search',
                    'web_search',
                    'google_places',
                )
            )
        ).distinct()
    if source == 'two_gis':
        return queryset.filter(sources__source_type='two_gis').distinct()
    if source == 'website':
        return queryset.filter(
            Q(website_url__gt='') | Q(sources__source_type='website')
        ).distinct()
    if source == 'manual':
        return queryset.filter(
            Q(source_type='manual') | Q(sources__source_type='manual')
        ).distinct()
    return queryset


def _candidate_quick_filter(queryset, quick: str):
    if quick == 'ready':
        return queryset.filter(lifecycle_status=SellerLead.LIFECYCLE_READY_TO_INVITE)
    if quick == 'invited':
        return queryset.filter(lifecycle_status=SellerLead.LIFECYCLE_INVITED)
    if quick == 'no_whatsapp':
        return queryset.filter(whatsapp='')
    if quick == 'dismantler':
        return queryset.filter(business_type=SellerLead.BUSINESS_TYPE_DISMANTLER)
    if quick == 'wholesaler':
        return queryset.filter(business_type=SellerLead.BUSINESS_TYPE_WHOLESALER)
    if quick == 'unlinked':
        return queryset.filter(request_seller__isnull=True)
    return queryset


def _candidate_summary() -> dict[str, int]:
    base = SellerLead.objects.filter(duplicate_of__isnull=True)
    return {
        'total': base.count(),
        'with_whatsapp': base.exclude(whatsapp='').count(),
        'ready': base.filter(
            lifecycle_status=SellerLead.LIFECYCLE_READY_TO_INVITE
        ).count(),
        'invited': base.filter(
            lifecycle_status=SellerLead.LIFECYCLE_INVITED
        ).count(),
        'registered': base.filter(
            Q(status=SellerLead.STATUS_REGISTERED)
            | Q(
                lifecycle_status__in=(
                    SellerLead.LIFECYCLE_CLAIMED,
                    SellerLead.LIFECYCLE_VERIFIED,
                    SellerLead.LIFECYCLE_ACTIVE,
                )
            )
        ).distinct().count(),
    }


def list_seller_candidates(params: QueryDict) -> dict:
    queryset = (
        SellerLead.objects
        .filter(duplicate_of__isnull=True)
        .select_related('request_seller')
        .prefetch_related('sources')
    )

    search = first_value(params, 'q')
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(city__icontains=search)
            | Q(instagram_username__icontains=search)
            | Q(website_url__icontains=search)
        )

    city = first_value(params, 'city')
    if city:
        queryset = queryset.filter(city__iexact=city)

    source = first_value(params, 'source')
    queryset = _candidate_source_filter(queryset, source)

    business_type = first_value(params, 'business_type')
    if business_type:
        queryset = queryset.filter(business_type=business_type)

    stage = first_value(params, 'stage')
    if stage == 'registered':
        queryset = queryset.filter(
            Q(status=SellerLead.STATUS_REGISTERED)
            | Q(
                lifecycle_status__in=(
                    SellerLead.LIFECYCLE_CLAIMED,
                    SellerLead.LIFECYCLE_VERIFIED,
                    SellerLead.LIFECYCLE_ACTIVE,
                )
            )
        )
    elif stage:
        queryset = queryset.filter(lifecycle_status=stage)

    whatsapp_state = first_value(params, 'whatsapp_state')
    if whatsapp_state == 'yes':
        queryset = queryset.exclude(whatsapp='')
    elif whatsapp_state == 'no':
        queryset = queryset.filter(whatsapp='')

    linked = first_value(params, 'linked')
    if linked == 'yes':
        queryset = queryset.filter(request_seller__isnull=False)
    elif linked == 'no':
        queryset = queryset.filter(request_seller__isnull=True)

    quick = first_value(params, 'quick')
    queryset = _candidate_quick_filter(queryset, quick)

    sort = first_value(params, 'sort')
    if sort not in dict(CANDIDATE_SORT_CHOICES):
        sort = 'recent'
    if sort == 'name':
        queryset = queryset.order_by('name', 'id')
    elif sort == 'stage':
        queryset = queryset.annotate(
            control_stage_order=Case(
                When(lifecycle_status=SellerLead.LIFECYCLE_READY_TO_INVITE, then=Value(1)),
                When(lifecycle_status=SellerLead.LIFECYCLE_INVITED, then=Value(2)),
                When(lifecycle_status=SellerLead.LIFECYCLE_ENRICHED, then=Value(3)),
                When(lifecycle_status=SellerLead.LIFECYCLE_CLASSIFIED, then=Value(4)),
                When(lifecycle_status=SellerLead.LIFECYCLE_FOUND, then=Value(5)),
                default=Value(9),
                output_field=IntegerField(),
            )
        ).order_by('control_stage_order', '-updated_at', 'id')
    else:
        queryset = queryset.order_by('-updated_at', '-id')

    page = paginate(queryset, params)
    rows: list[SellerLeadRow] = []
    for lead in page.object_list:
        registered = (
            lead.status == SellerLead.STATUS_REGISTERED
            or lead.lifecycle_status
            in {
                SellerLead.LIFECYCLE_CLAIMED,
                SellerLead.LIFECYCLE_VERIFIED,
                SellerLead.LIFECYCLE_ACTIVE,
            }
        )
        if registered:
            stage_label = 'Зарегистрирован'
        else:
            stage_label = LIFECYCLE_LABELS.get(
                lead.lifecycle_status,
                lead.lifecycle_status,
            )
        can_invite = bool(
            lead.lifecycle_status == SellerLead.LIFECYCLE_READY_TO_INVITE
            and normalize_kz_phone(lead.whatsapp)
        )
        can_mark_invited = can_invite

        rows.append(
            SellerLeadRow(
                pk=lead.pk,
                name=lead.name,
                city=dash(lead.city),
                source_label=_source_label_for_lead(lead),
                business_type_label=BUSINESS_TYPE_LABELS.get(
                    lead.business_type,
                    lead.business_type or '—',
                ),
                stage_label=stage_label,
                has_whatsapp=bool(normalize_kz_phone(lead.whatsapp)),
                instagram_username=lead.instagram_username,
                website_url=lead.website_url,
                linked_seller_id=lead.request_seller_id,
                can_invite=can_invite,
                can_mark_invited=can_mark_invited,
                admin_url=f'/admin/core/sellerlead/{lead.pk}/change/',
            )
        )

    cities = list(
        SellerLead.objects
        .filter(duplicate_of__isnull=True)
        .exclude(city='')
        .order_by('city')
        .values_list('city', flat=True)
        .distinct()
    )
    return {
        'seller_view': 'candidates',
        'rows': rows,
        'page': page.page,
        'querystring': page.querystring,
        'total': page.total,
        'summary': _candidate_summary(),
        'seller_total': Seller.objects.count(),
        'candidate_total': SellerLead.objects.filter(duplicate_of__isnull=True).count(),
        'cities': cities,
        'sort_choices': CANDIDATE_SORT_CHOICES,
        'source_choices': tuple(choice for choice in SOURCE_CHOICES if choice[0] != 'base'),
        'business_type_choices': BUSINESS_TYPE_CHOICES,
        'stage_choices': CANDIDATE_STAGE_CHOICES,
        'filters': {
            'q': search,
            'city': city,
            'source': source,
            'business_type': business_type,
            'stage': stage,
            'whatsapp_state': whatsapp_state,
            'linked': linked,
            'quick': quick,
            'sort': sort,
        },
    }


def get_seller_detail(pk: int) -> dict:
    seller = get_object_or_404(
        _annotated_sellers().prefetch_related(
            'selected_categories',
            'selected_brands',
            _consent_prefetch(),
            _lead_prefetch(),
        ),
        pk=pk,
    )
    consent = _consent_for_seller(seller)
    lead = _seller_lead_for_row(seller)
    _stage_key, stage_label = _seller_stage(seller, lead)

    matches = fetch_latest(
        Match.objects.filter(seller_id=seller.pk)
        .select_related('request')
        .order_by('-created_at', '-id'),
        limit=SELLER_MATCH_HISTORY_LIMIT,
    )
    request_ids = [match.request_id for match in matches if match.request_id]
    types_by_request: dict[int, set[str]] = {}
    if request_ids:
        for request_id, event_type in SellerRequestPageEvent.objects.filter(
            seller_id=seller.pk,
            request_id__in=request_ids,
        ).values_list('request_id', 'event_type'):
            types_by_request.setdefault(request_id, set()).add(event_type)
    requests = []
    for match in matches:
        types = types_by_request.get(match.request_id, set())
        requests.append(
            {
                'request_id': match.request_id,
                'sent_at': match.sent_at,
                'status': match_status_label(match.status),
                'vehicle': vehicle_label(match.request.brand, match.request.model)
                if match.request_id
                else '—',
                'opened': SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN in types,
                'whatsapp': SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK in types,
                'called': SELLER_REQUEST_PAGE_EVENT_CALL_CLICK in types,
                'result': request_result_label(types),
            }
        )
    events = [
        {
            'created_at': event.created_at,
            'event_label': event_label(event.event_type),
            'request_id': event.request_id,
        }
        for event in fetch_latest(
            SellerRequestPageEvent.objects.filter(seller_id=seller.pk)
            .select_related('request')
            .order_by('-created_at', '-id'),
            limit=SELLER_EVENT_HISTORY_LIMIT,
        )
    ]
    return {
        'seller': seller,
        'categories': _category_text(seller),
        'brands': ', '.join(item.name for item in seller.selected_brands.all()) or '—',
        'consent_label': marketing_consent_label(consent.status if consent else ''),
        'consent_tone': consent_tone(consent.status if consent else ''),
        'consent_source': consent_source_label(consent.source if consent else ''),
        'consented_at': consent.consented_at if consent else None,
        'revoked_at': consent.revoked_at if consent else None,
        'sent': seller.sent_count,
        'opened': seller.opened_count,
        'whatsapp': seller.whatsapp_count,
        'called': seller.call_count,
        'out_of_stock': seller.out_of_stock_count,
        'cannot_fulfill': seller.cannot_fulfill_count,
        'last_activity': seller.last_activity,
        'requests': requests,
        'events': events,
        'discovery': {
            'lead': lead,
            'source_label': _source_label_for_lead(lead),
            'business_type_label': _seller_type_label(lead),
            'stage_label': stage_label,
            'products_count': _product_counts_for_sellers([seller]).get(seller.pk, 0),
            'registered': bool(seller.registered_profile),
        },
        'admin_url': f'/admin/core/seller/{seller.pk}/change/',
    }
