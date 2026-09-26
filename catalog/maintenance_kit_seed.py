"""Exact-article seed plan for maintenance kits.

Binds a Product only when Product.objects.filter(article=article).count() == 1.
Never uses icontains or first()-style fallbacks.
Does not change Product.price, Product.stock_qty, or PP1/PP2.

Fitment decisions use independent catalogs (FitInPart / WIX), not zpt.kz cards.
Disputed articles stay in reference_lines and must not return as kit items.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from catalog.models import Brand, CarModel, MaintenanceKit, MaintenanceKitItem, Product


UNI_K_INCOMPLETE_WARNING = (
    'Неполный комплект: отсутствует подтверждённый масляный фильтр. '
    'Салонный CD569F2801032700: артикул уточняется. '
    'Свеча D20T0120700 для UNI-K не подтверждаем. '
    'Не публиковать, пока SKU масляного, салонного фильтра и свечи не подтверждены.'
)

COVER_SPARKS_EXCLUDED_CAPTION = (
    'Состав набора указан ниже; свечи в заказ не входят'
)

OEM_UNKNOWN = 'OEM неизвестен'

STATUS_OK = 'OK'
STATUS_MISSING = 'MISSING'
STATUS_AMBIGUOUS = 'AMBIGUOUS'


# Independent sources (not zpt.kz Product cards):
# T15-1109111 air — Tiggo 7 Pro SQRE4T15C from Mar 2020:
#   https://www.fitinpart.sg/v2/en/product/50357231/chery-t15-1109111
# T21-8107011 cabin — Tiggo 7 Pro SQRE4T15C from Mar 2020:
#   https://www.fitinpart.sg/v2/en/product/50357199/chery-t21-8107011
# 480-1012010 oil — Tiggo 7 Pro SQRE4T15C from Mar 2020:
#   https://www.fitinpart.sg/v2/en/product/136865/chery-480-1012010
# F4J16-3707010 spark — TXL M32T SQRF4J16A from Apr 2019; Tiggo 7 Pro SQRE4T15C
#   is not listed. Seller/our cards contradict. Keep disputed, not in 7 Pro kit:
#   https://www.fitinpart.sg/v2/en/product/70437342/chery-f4j16-3707010
# F4J16-1012030 oil — TXL M32T SQRF4J16A from Apr 2019, not SQRF4J16D / 2.0T:
#   https://www.fitinpart.sg/v2/en/product/50357242/chery-f4j16-1012030
# 480-1012010 also listed for TXL M32T SQRF4J16D from Jul 2023 — different engine.
# WIX WA11833 OE 151000025AA — EXEED TX/TXL 1.6T (SQRF4J16 / later 1.6 codes):
#   https://www.wixfilters.com/zh-cn/catalog/results/product.html/wa11833_wix.html
# S301014-0903 air — UNI-V 1.5 JL473ZQ7 from Mar 2022:
#   https://www.fitinpart.sg/v2/en/product/61763323/changan-s301014-0903
# C281F280103-2601 cabin — UNI-V 1.5 JL473ZQ7 from Mar 2022:
#   https://www.fitinpart.sg/v2/en/product/70433118/changan-c281f280103-2601
# 1109101XGW01A air — Dargo 2.0 GW4N20 from Apr 2022 (also 1.5 — kit scoped to 2.0):
#   https://www.fitinpart.sg/v2/en/product/79453172/haval-1109101xgw01a
# 1017110XEN01 oil — Dargo 2.0 GW4N20 from Apr 2022:
#   https://www.fitinpart.sg/v2/en/product/51490992/great-wall-1017110xen01
# Dargo cabin: FitInPart Dargo GW4N20 lists 8104400XKZ96A; AG SKU is 8104400XKY28B.
#   Disputed — reference only, OEM unknown.
# 151000079AA air — Tiggo 8 Pro 1.6 T1D SQRF4J16A Mar 2021~ and T1A SQRF4J16
#   Mar 2021~May 2024; Arrizo 8 SQRF4J16C Apr 2022~:
#   https://www.fitinpart.sg/v2/en/product/50357241/chery-151000079aa
# T21-8107011 cabin — Tiggo 8 Pro 1.6 SQRF4J16A Mar 2021~; Tiggo 7 T15
#   SQRE4T15 Apr 2016~Nov 2020 and T1E SQRE4T15C Sep 2020~; Arrizo 8
#   SQRF4J16C Apr 2022~; Tiggo 8 T18 SQRE4T15C Dec 2020~Mar 2022 and
#   SQRE4T15B Jan 2022~:
#   https://www.fitinpart.sg/v2/en/product/50357199/chery-t21-8107011
# F4J16-1012030 oil — Tiggo 8 Pro T1D SQRF4J16A Mar 2021~:
#   https://www.fitinpart.sg/v2/en/product/50357242/chery-f4j16-1012030
# 480-1012010 oil — same Tiggo 8 Pro T1D SQRF4J16A Mar 2021~ AND Tiggo 7
#   1.5 / Arrizo 8 SQRF4J16C / Tiggo 8 1.5:
#   https://www.fitinpart.sg/v2/en/product/136865/chery-480-1012010
# Tiggo 8 Pro 1.6 oil is disputed (cartridge vs spin-on) — not in kit.
# 301001199AA cabin: JS AC0359C lists Chery 301001199AA with TXL SQRF4J16A
#   (also 265AA and other 30100* as co-crosses). Keep published TXL 1.6 kit
#   line; do not substitute T21-8107011 or swap to 301000265AA without review.
# F4J16-3707010 spark — Tiggo 8 T18 SQRE4T15B Jan 2022~; TXL SQRF4J16A.
#   Not listed for Tiggo 7 T15/T1E or Tiggo 8 Pro:
#   https://www.fitinpart.sg/v2/en/product/70437342/chery-f4j16-3707010
# T15-1109111 air — Tiggo 7 1.5 T15/T1E; Tiggo 8 T18 1.5. Not Tiggo 8 Pro 1.6:
#   https://www.fitinpart.sg/v2/en/product/50357231/chery-t15-1109111
# D20T0120700 spark — CS75 Plus 2.0 JL486ZQ4 Apr 2019–Apr 2021 and CS95.
#   UNI-K is not listed. Keep as reference only, not a UNI-K kit item:
#   https://www.fitinpart.sg/v2/en/product/76262538/changan-d20t0120700
# CD569F2801032700 cabin — no dedicated packing page. UNI-K vehicle catalog
#   lists neighbor cabin CD569F280103-2701, not 2700. Brake CD569F260303-*
#   are different parts. Keep 2700 as reference only: артикул уточняется.

KIT_SPECS = (
    {
        'slug': 'komplekt-to-chery-tiggo-7-pro-15t',
        'name': 'Набор ТО — 3 позиции Chery Tiggo 7 Pro 1.5T SQRE4T15C',
        'brand_name': 'Chery',
        'model_name': 'Tiggo 7 Pro',
        'engine': '1.5T SQRE4T15C',
        'year_from': 2020,
        'year_to': None,
        'description': (
            'Набор ТО — 3 позиции для Chery Tiggo 7 Pro 1.5T SQRE4T15C '
            '(с марта 2020): воздушный, салонный и масляный фильтры. '
            'Свечи в набор не входят; артикул свечей для конкретного '
            'автомобиля требует уточнения. Не для Tiggo 7 (T15) и не для '
            'Tiggo 7 Pro Max 1.6.'
        ),
        'cover_note': COVER_SPARKS_EXCLUDED_CAPTION,
        'reference_lines': (
            {
                'type_label': 'Свеча зажигания',
                'article': '',
                'note': (
                    'Свечи в набор не входят. OEM для этой модификации '
                    'неизвестен: источники противоречат друг другу.'
                ),
            },
        ),
        'publish_if_complete': True,
        'items': (
            ('T151109111', 1),
            ('T218107011', 1),
            ('4801012010', 1),
        ),
    },
    {
        'slug': 'komplekt-to-exeed-txl-16t',
        'name': 'Набор ТО — 4 позиции EXEED TXL 1.6T SQRF4J16A',
        'brand_name': 'Exeed',
        'model_name': 'TXL',
        'engine': '1.6T SQRF4J16A',
        'year_from': 2019,
        'year_to': None,
        'description': (
            'Набор ТО — 4 позиции для EXEED TXL 1.6T SQRF4J16A (M32T, '
            'с апреля 2019): воздушный, салонный, масляный фильтры и свечи. '
            'Не для 2.0T и не для поздних 1.6T SQRF4J16D (с июля 2023) — '
            'у них другой масляный фильтр.'
        ),
        'cover_note': '',
        'reference_lines': (),
        'publish_if_complete': True,
        'items': (
            ('151000025AA', 1),
            ('301001199AA', 1),
            ('F4J161012030', 1),
            ('F4J163707010', 4),
        ),
    },
    {
        'slug': 'nabor-to-changan-uni-v-15',
        'name': 'Набор ТО — 2 позиции Changan UNI-V 1.5 JL473ZQ7',
        'brand_name': 'Changan',
        'model_name': 'UNI-V',
        'engine': '1.5 JL473ZQ7',
        'year_from': 2022,
        'year_to': None,
        'description': (
            'Набор ТО — 2 позиции для Changan UNI-V 1.5 JL473ZQ7 '
            '(с марта 2022): воздушный и салонный фильтры. Масляный фильтр '
            'и свечи в набор не входят.'
        ),
        'cover_note': '',
        'reference_lines': (
            {
                'type_label': 'Масляный фильтр',
                'article': '',
                'note': 'В набор не входит. OEM неизвестен.',
            },
            {
                'type_label': 'Свеча зажигания',
                'article': '',
                'note': 'В набор не входит. OEM неизвестен.',
            },
        ),
        'publish_if_complete': True,
        'items': (
            ('S3010140903', 1),
            ('C281F2801032601', 1),
        ),
    },
    {
        'slug': 'nabor-to-haval-dargo-20-gw4n20',
        'name': 'Набор ТО — 2 позиции Haval Dargo 2.0 GW4N20',
        'brand_name': 'Haval',
        'model_name': 'Dargo',
        'engine': '2.0 GW4N20',
        'year_from': 2022,
        'year_to': None,
        'description': (
            'Набор ТО — 2 позиции для Haval Dargo 2.0 GW4N20 '
            '(с апреля 2022): воздушный и масляный фильтры. Не для 1.5 '
            'GW4B15L. Салонный фильтр в набор не входит.'
        ),
        'cover_note': '',
        'reference_lines': (
            {
                'type_label': 'Салонный фильтр',
                'article': '',
                'note': (
                    'В набор не входит. OEM неизвестен: каталоги указывают '
                    'разные номера.'
                ),
            },
        ),
        'publish_if_complete': True,
        'items': (
            ('1109101XGW01A', 1),
            ('1017110XEN01', 1),
        ),
    },
    {
        'slug': 'komplekt-to-changan-uni-k-20t',
        'name': 'Комплект ТО Changan UNI-K 2.0T',
        'brand_name': 'Changan',
        'model_name': 'UNI-K',
        'engine': '2.0T',
        'year_from': None,
        'year_to': None,
        'description': UNI_K_INCOMPLETE_WARNING,
        'cover_note': '',
        'reference_lines': (
            {
                'type_label': 'Масляный фильтр',
                'article': '',
                'note': 'В набор не входит. OEM неизвестен.',
            },
            {
                'type_label': 'Салонный фильтр',
                'article': 'CD569F2801032700',
                'note': (
                    'В набор не входит. Артикул уточняется: независимой '
                    'packing-страницы для CD569F2801032700 нет, каталог UNI-K '
                    'указывает соседний CD569F280103-2701. Тормозные '
                    'CD569F260303 не копируем. Несовместимость не объявляем.'
                ),
            },
            {
                'type_label': 'Свеча зажигания',
                'article': '',
                'note': (
                    'В набор не входит. D20T0120700 для UNI-K не подтверждаем: '
                    'независимо указаны CS75 Plus 2.0 JL486ZQ4 (04.2019–04.2021) '
                    'и CS95, без UNI-K. Несовместимость не объявляем.'
                ),
            },
        ),
        'publish_if_complete': False,
        'items': (
            ('1109190CR01', 1),
        ),
    },
    {
        'slug': 'nabor-to-chery-tiggo-8-pro-16-sqrf4j16a',
        'name': 'Набор ТО — 2 позиции Chery Tiggo 8 Pro 1.6 SQRF4J16A',
        'brand_name': 'Chery',
        'model_name': 'Tiggo 8 Pro',
        'engine': '1.6 SQRF4J16A / SQRF4J16',
        'year_from': 2021,
        'year_to': None,
        'description': (
            'Набор ТО — 2 позиции для Chery Tiggo 8 Pro 1.6 SQRF4J16A '
            '(T1D, с марта 2021) и 1.6 SQRF4J16 (T1A, март 2021 – май 2024): '
            'воздушный и салонный фильтры. Масляный фильтр и свечи в набор '
            'не входят. Не для Tiggo 8 без Pro, не для 1.5T и не для 2.0T.'
        ),
        'cover_note': '',
        'reference_lines': (
            {
                'type_label': 'Масляный фильтр',
                'article': '',
                'note': (
                    'В набор не входит. OEM уточняется: каталоги указывают '
                    'разные номера для этой модификации.'
                ),
            },
            {
                'type_label': 'Свеча зажигания',
                'article': '',
                'note': 'В набор не входит. OEM неизвестен.',
            },
        ),
        'publish_if_complete': True,
        'items': (
            ('151000079AA', 1),
            ('T218107011', 1),
        ),
    },
    {
        'slug': 'nabor-to-chery-tiggo-7-15',
        'name': 'Набор ТО — 3 позиции Chery Tiggo 7 1.5 SQRE4T15',
        'brand_name': 'Chery',
        'model_name': 'Tiggo 7',
        'engine': '1.5 SQRE4T15 / SQRE4T15C',
        'year_from': 2016,
        'year_to': None,
        'description': (
            'Набор ТО — 3 позиции для Chery Tiggo 7 1.5 SQRE4T15 '
            '(T15, апрель 2016 – ноябрь 2020) и SQRE4T15C (T1E, с сентября '
            '2020): воздушный, салонный и масляный фильтры. Свечи в набор '
            'не входят. Не для Tiggo 7 Pro и не для 2.0.'
        ),
        'cover_note': '',
        'reference_lines': (
            {
                'type_label': 'Свеча зажигания',
                'article': '',
                'note': 'В набор не входит. OEM неизвестен.',
            },
        ),
        'publish_if_complete': True,
        'items': (
            ('T151109111', 1),
            ('T218107011', 1),
            ('4801012010', 1),
        ),
    },
    {
        'slug': 'nabor-to-chery-arrizo-8-16-sqrf4j16c',
        'name': 'Набор ТО — 3 позиции Chery Arrizo 8 1.6 SQRF4J16C',
        'brand_name': 'Chery',
        'model_name': 'Arrizo 8',
        'engine': '1.6 SQRF4J16C',
        'year_from': 2022,
        'year_to': None,
        'description': (
            'Набор ТО — 3 позиции для Chery Arrizo 8 1.6 SQRF4J16C '
            '(DC21B, с апреля 2022): воздушный, салонный и масляный фильтры. '
            'Свечи в набор не входят. Не для Tiggo 8 Pro и не для других '
            'моторов Arrizo 8.'
        ),
        'cover_note': '',
        'reference_lines': (
            {
                'type_label': 'Свеча зажигания',
                'article': '',
                'note': 'В набор не входит. OEM неизвестен.',
            },
        ),
        'publish_if_complete': True,
        'items': (
            ('151000079AA', 1),
            ('T218107011', 1),
            ('4801012010', 1),
        ),
    },
    {
        'slug': 'nabor-to-chery-tiggo-8-15t-sqre4t15c',
        'name': 'Набор ТО — 3 позиции Chery Tiggo 8 1.5T SQRE4T15C',
        'brand_name': 'Chery',
        'model_name': 'Tiggo 8',
        'engine': '1.5T SQRE4T15C',
        'year_from': 2020,
        'year_to': 2022,
        'description': (
            'Набор ТО — 3 позиции для Chery Tiggo 8 1.5T SQRE4T15C '
            '(T18, декабрь 2020 – март 2022): воздушный, салонный и масляный '
            'фильтры. Свечи в набор не входят. Не для SQRE4T15B, не для 2.0 '
            'и не для Tiggo 8 Pro.'
        ),
        'cover_note': '',
        'reference_lines': (
            {
                'type_label': 'Свеча зажигания',
                'article': '',
                'note': 'В набор не входит. OEM неизвестен.',
            },
        ),
        'publish_if_complete': True,
        'items': (
            ('T151109111', 1),
            ('T218107011', 1),
            ('4801012010', 1),
        ),
    },
    {
        'slug': 'nabor-to-chery-tiggo-8-15t-sqre4t15b',
        'name': 'Набор ТО — 4 позиции Chery Tiggo 8 1.5T SQRE4T15B',
        'brand_name': 'Chery',
        'model_name': 'Tiggo 8',
        'engine': '1.5T SQRE4T15B',
        'year_from': 2022,
        'year_to': None,
        'description': (
            'Набор ТО — 4 позиции для Chery Tiggo 8 1.5T SQRE4T15B '
            '(T18, с января 2022): воздушный, салонный, масляный фильтры '
            'и свечи. Не для SQRE4T15C, не для 2.0 и не для Tiggo 8 Pro.'
        ),
        'cover_note': '',
        'reference_lines': (),
        'publish_if_complete': True,
        'items': (
            ('T151109111', 1),
            ('T218107011', 1),
            ('4801012010', 1),
            ('F4J163707010', 4),
        ),
    },
)


@dataclass
class SeedRef:
    status: str
    kind: str
    label: str
    object_id: int | None = None
    detail: str = ''


@dataclass
class SeedKitPlan:
    spec: dict
    brand: SeedRef
    car_model: SeedRef
    items: list[SeedRef] = field(default_factory=list)
    would_publish: bool = False
    skip_reason: str = ''
    existing_kit_id: int | None = None
    current_items: list[tuple[str, int, int]] = field(default_factory=list)
    would_remove: list[tuple[str, int]] = field(default_factory=list)
    would_add: list[tuple[str, int]] = field(default_factory=list)

    @property
    def complete(self):
        if self.brand.status != STATUS_OK or self.car_model.status != STATUS_OK:
            return False
        return all(ref.status == STATUS_OK for ref in self.items)


def _unique_or_status(qs, kind, label):
    matches = list(qs[:2])
    count = qs.count()
    if count == 0:
        return SeedRef(STATUS_MISSING, kind, label, detail='count=0')
    if count > 1:
        ids = ', '.join(str(obj.pk) for obj in qs.order_by('pk')[:5])
        return SeedRef(
            STATUS_AMBIGUOUS,
            kind,
            label,
            detail=f'count={count}; ids={ids}',
        )
    obj = matches[0]
    return SeedRef(STATUS_OK, kind, label, object_id=obj.pk)


def resolve_article(article: str) -> SeedRef:
    return _unique_or_status(
        Product.objects.filter(article=article),
        'product',
        article,
    )


def resolve_brand(name: str) -> SeedRef:
    return _unique_or_status(Brand.objects.filter(name=name), 'brand', name)


def resolve_car_model(brand_ref: SeedRef, model_name: str) -> SeedRef:
    if brand_ref.status != STATUS_OK or brand_ref.object_id is None:
        return SeedRef(
            STATUS_MISSING,
            'car_model',
            model_name,
            detail='brand not unique',
        )
    return _unique_or_status(
        CarModel.objects.filter(brand_id=brand_ref.object_id, name=model_name),
        'car_model',
        model_name,
    )


def _current_kit_items(kit_id: int | None) -> list[tuple[str, int, int]]:
    if not kit_id:
        return []
    rows = (
        MaintenanceKitItem.objects.filter(kit_id=kit_id)
        .select_related('product')
        .order_by('id')
    )
    result = []
    for item in rows:
        article = str(getattr(item.product, 'article', '') or '').strip()
        result.append((article, int(item.quantity), item.product_id))
    return result


def _diff_items(current, planned_pairs):
    current_by_article = {article: qty for article, qty, _pid in current}
    planned_by_article = {article: qty for article, qty in planned_pairs}
    would_remove = [
        (article, qty)
        for article, qty in current_by_article.items()
        if article not in planned_by_article
    ]
    would_add = [
        (article, qty)
        for article, qty in planned_pairs
        if article not in current_by_article
    ]
    return would_remove, would_add


def plan_maintenance_kits(specs=KIT_SPECS) -> list[SeedKitPlan]:
    plans = []
    for spec in specs:
        brand = resolve_brand(spec['brand_name'])
        car_model = resolve_car_model(brand, spec['model_name'])
        item_refs = [
            resolve_article(article)
            for article, _qty in spec['items']
        ]
        existing = (
            MaintenanceKit.objects.filter(slug=spec['slug'])
            .values_list('pk', flat=True)
            .first()
        )
        current_items = _current_kit_items(existing)
        would_remove, would_add = _diff_items(current_items, spec['items'])
        plan = SeedKitPlan(
            spec=spec,
            brand=brand,
            car_model=car_model,
            items=item_refs,
            existing_kit_id=existing,
            current_items=current_items,
            would_remove=would_remove,
            would_add=would_add,
        )
        if not plan.complete:
            missing = []
            if brand.status != STATUS_OK:
                missing.append(f'brand:{brand.status}')
            if car_model.status != STATUS_OK:
                missing.append(f'model:{car_model.status}')
            for ref in item_refs:
                if ref.status != STATUS_OK:
                    missing.append(f'{ref.label}:{ref.status}')
            plan.skip_reason = ', '.join(missing)
            plan.would_publish = False
        else:
            plan.would_publish = bool(spec['publish_if_complete'])
        plans.append(plan)
    return plans


def _upsert_kit(plan: SeedKitPlan) -> MaintenanceKit:
    spec = plan.spec
    kit, _created = MaintenanceKit.objects.update_or_create(
        slug=spec['slug'],
        defaults={
            'name': spec['name'],
            'brand_id': plan.brand.object_id,
            'car_model_id': plan.car_model.object_id,
            'engine': spec['engine'],
            'year_from': spec.get('year_from'),
            'year_to': spec.get('year_to'),
            'description': spec['description'],
            'cover_note': spec.get('cover_note') or '',
            'reference_lines': list(spec.get('reference_lines') or ()),
            'is_active': bool(spec['publish_if_complete']),
        },
    )
    wanted_ids = []
    for (_article, quantity), ref in zip(spec['items'], plan.items):
        MaintenanceKitItem.objects.update_or_create(
            kit=kit,
            product_id=ref.object_id,
            defaults={'quantity': quantity},
        )
        wanted_ids.append(ref.object_id)
    MaintenanceKitItem.objects.filter(kit=kit).exclude(
        product_id__in=wanted_ids,
    ).delete()
    return kit


def apply_maintenance_kits(plans: list[SeedKitPlan] | None = None) -> list[SeedKitPlan]:
    if plans is None:
        plans = plan_maintenance_kits()
    with transaction.atomic():
        for plan in plans:
            if not plan.complete:
                continue
            kit = _upsert_kit(plan)
            plan.existing_kit_id = kit.pk
            plan.current_items = _current_kit_items(kit.pk)
            plan.would_remove = []
            plan.would_add = []
    return plans


def _fmt_pairs(pairs) -> str:
    if not pairs:
        return '(none)'
    return ', '.join(f'{article} x{qty}' for article, qty, *_rest in pairs)


def format_plan_report(plans: list[SeedKitPlan], *, apply=False) -> str:
    mode = 'apply' if apply else 'dry-run'
    lines = [f'mode: {mode}']
    for plan in plans:
        spec = plan.spec
        lines.append(f'--- {spec["name"]} ---')
        lines.append(f'slug: {spec["slug"]}')
        lines.append(f'kit_id: {plan.existing_kit_id or "(new)"}')
        lines.append(
            f'brand: {plan.brand.status} {plan.brand.label}'
            + (f' id={plan.brand.object_id}' if plan.brand.object_id else '')
        )
        lines.append(
            f'model: {plan.car_model.status} {plan.car_model.label}'
            + (f' id={plan.car_model.object_id}' if plan.car_model.object_id else '')
        )
        lines.append(f'engine: {spec["engine"]}')
        lines.append(f'current: {_fmt_pairs(plan.current_items)}')
        lines.append(f'planned: {_fmt_pairs(spec["items"])}')
        if plan.would_remove:
            lines.append(f'would_remove: {_fmt_pairs(plan.would_remove)}')
        if plan.would_add:
            lines.append(f'would_add: {_fmt_pairs(plan.would_add)}')
        for (article, qty), ref in zip(spec['items'], plan.items):
            extra = f' id={ref.object_id}' if ref.object_id else f' {ref.detail}'
            lines.append(f'item {article} x{qty}: {ref.status}{extra}')
        for raw in spec.get('reference_lines') or ():
            article = str(raw.get('article') or '').strip() or OEM_UNKNOWN
            lines.append(
                f'reference {raw.get("type_label", "")}: {article}'
            )
        if not plan.complete:
            lines.append(f'result: SKIP ({plan.skip_reason})')
        elif apply:
            lines.append(
                f'result: UPSERT kit_id={plan.existing_kit_id} '
                f'is_active={bool(spec["publish_if_complete"])}'
            )
        else:
            publish = 'publish' if plan.would_publish else 'draft'
            action = 'update' if plan.existing_kit_id else 'create'
            lines.append(f'result: WOULD {action} ({publish})')
    return '\n'.join(lines)
