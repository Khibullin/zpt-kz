"""Exact-article seed plan for the first maintenance kits.

Binds a Product only when Product.objects.filter(article=article).count() == 1.
Never uses icontains or first()-style fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction

from catalog.models import Brand, CarModel, MaintenanceKit, MaintenanceKitItem, Product


UNI_K_INCOMPLETE_WARNING = (
    'Неполный комплект: отсутствует подтверждённый масляный фильтр. '
    'Не публиковать, пока SKU масляного фильтра не подтверждён.'
)

STATUS_OK = 'OK'
STATUS_MISSING = 'MISSING'
STATUS_AMBIGUOUS = 'AMBIGUOUS'


KIT_SPECS = (
    {
        'slug': 'komplekt-to-chery-tiggo-7-pro-15t',
        'name': 'Комплект ТО Chery Tiggo 7 Pro 1.5T',
        'brand_name': 'Chery',
        'model_name': 'Tiggo 7 Pro',
        'engine': '1.5T',
        'description': (
            'Комплект расходников для ТО Chery Tiggo 7 Pro 1.5T: '
            'воздушный фильтр, салонный фильтр, масляный фильтр и свечи зажигания.'
        ),
        'publish_if_complete': True,
        'items': (
            ('T151109111', 1),
            ('T218107011', 1),
            ('4801012010', 1),
            ('F4J163707010', 4),
        ),
    },
    {
        'slug': 'komplekt-to-exeed-txl-16t',
        'name': 'Комплект ТО EXEED TXL 1.6T',
        'brand_name': 'Exeed',
        'model_name': 'TXL',
        'engine': '1.6T',
        'description': (
            'Комплект расходников для ТО EXEED TXL 1.6T: '
            'воздушный фильтр, салонный фильтр, масляный фильтр и свечи зажигания.'
        ),
        'publish_if_complete': True,
        'items': (
            ('151000025AA', 1),
            ('301001199AA', 1),
            ('F4J161012030', 1),
            ('F4J163707010', 4),
        ),
    },
    {
        'slug': 'komplekt-to-changan-uni-k-20t',
        'name': 'Комплект ТО Changan UNI-K 2.0T',
        'brand_name': 'Changan',
        'model_name': 'UNI-K',
        'engine': '2.0T',
        'description': UNI_K_INCOMPLETE_WARNING,
        'publish_if_complete': False,
        'items': (
            ('1109190CR01', 1),
            ('CD569F2801032700', 1),
            ('D20T0120700', 4),
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
        plan = SeedKitPlan(
            spec=spec,
            brand=brand,
            car_model=car_model,
            items=item_refs,
            existing_kit_id=existing,
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
            'description': spec['description'],
            'is_active': bool(spec['publish_if_complete']),
        },
    )
    for (_article, quantity), ref in zip(spec['items'], plan.items):
        MaintenanceKitItem.objects.update_or_create(
            kit=kit,
            product_id=ref.object_id,
            defaults={'quantity': quantity},
        )
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
    return plans


def format_plan_report(plans: list[SeedKitPlan], *, apply=False) -> str:
    mode = 'apply' if apply else 'dry-run'
    lines = [f'mode: {mode}']
    for plan in plans:
        spec = plan.spec
        lines.append(f'--- {spec["name"]} ---')
        lines.append(f'slug: {spec["slug"]}')
        lines.append(
            f'brand: {plan.brand.status} {plan.brand.label}'
            + (f' id={plan.brand.object_id}' if plan.brand.object_id else '')
        )
        lines.append(
            f'model: {plan.car_model.status} {plan.car_model.label}'
            + (f' id={plan.car_model.object_id}' if plan.car_model.object_id else '')
        )
        for (article, qty), ref in zip(spec['items'], plan.items):
            extra = f' id={ref.object_id}' if ref.object_id else f' {ref.detail}'
            lines.append(f'item {article} x{qty}: {ref.status}{extra}')
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
