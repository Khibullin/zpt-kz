"""Import exact, sourced manufacturer applications without changing trade data."""
import json
from urllib.parse import urlsplit
from django.db import transaction
from django.core.validators import URLValidator
from django.core.exceptions import ValidationError
from .models import Supplier, Offer, VehicleFitment, Activity

ALLOWED = {'ASVA': {'asva-trading.com'}, 'MOTORAD': {'eu.motorad.com'},
           'AKIKA': {'akika.jp', 'www.akika.jp'}, 'AYWIPARTS': {'aywiparts.ru'},
           '555': {'www.sankei-555.com'}, 'PATRON': {'patron.ru', 'www.patron.ru'},
           'BILSTEIN': {'cart.bilsteinus.com'}, 'BLUE PRINT': {'partsfinder.bilsteingroup.com'},
           'AKEBONO': {'www.akebono-brake.com'}, 'NGK': {'www.ngkntk.com'}}


@transaction.atomic
def import_catalogue_fitments(file, supplier_name, date, user):
    try:
        payload = json.load(file)
    except (ValueError, UnicodeError):
        raise ValueError('Нужен JSON-файл каталожной применимости.')
    if not isinstance(payload, dict) or payload.get('checked_on') != date.isoformat():
        raise ValueError('Дата файла должна совпадать с датой проверки.')
    rows = payload.get('offers')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20000:
        raise ValueError('Нужен непустой список offers, до 20 000 товаров.')
    supplier = Supplier.objects.get(name=supplier_name.strip())
    updated = pairs = 0
    seen = set()
    for row in rows:
        brand, article = row.get('brand'), row.get('article')
        if not isinstance(brand, str) or not isinstance(article, str) or not article.strip():
            raise ValueError('Укажите бренд и полный артикул.')
        key = (brand, article)
        if key in seen:
            raise ValueError('Бренд и артикул дублируются.')
        seen.add(key)
        if row.get('source_article') != article:
            raise ValueError('Артикул не совпадает с прочитанным источником.')
        source = row.get('source_url', '')
        try:
            URLValidator(schemes=['https'])(source)
        except ValidationError:
            raise ValueError('Источник должен быть HTTPS-ссылкой каталога.')
        if urlsplit(source).hostname not in ALLOWED.get(brand.upper(), set()):
            raise ValueError('Источник не входит в список каталогов данного производителя.')
        if row.get('status') != 'sourced' or not isinstance(row.get('rows'), list) or not row['rows']:
            raise ValueError('Неподтверждённую применимость импортировать нельзя.')
        offer = Offer.objects.select_for_update().filter(supplier=supplier, brand=brand, article=article).first()
        if not offer:
            raise ValueError('Точный бренд и артикул не найдены у выбранного поставщика.')
        grouped = {}
        for fit in row['rows']:
            make, model = fit.get('make'), fit.get('model')
            if not isinstance(make, str) or not make.strip() or len(make) > 100 or not isinstance(model, str) or not model.strip() or len(model) > 150:
                raise ValueError('Укажите марку и модель автомобиля.')
            config = {k: v for k, v in fit.items() if k not in ('make', 'model')}
            if any(not isinstance(v, str) or len(v) > 2000 for v in config.values()):
                raise ValueError('Некорректные ограничения применимости.')
            config['source_url'] = source
            group = grouped.setdefault((make.strip(), model.strip()), [])
            if config not in group:
                group.append(config)
        # Existing staff corrections or partial reviews take precedence over imports.
        if offer.fitments.exists():
            continue
        VehicleFitment.objects.bulk_create([VehicleFitment(offer=offer, make=make,
            model=model, configurations=configs, source_url=source, verified=True)
            for (make, model), configs in grouped.items()], batch_size=200)
        metadata = dict(offer.characteristics or {})
        metadata['manufacturer_applicability'] = {'manufacturer': brand,
            'checked_on': date.isoformat(), 'source_url': source, 'status': 'sourced',
            'note': row.get('note', '')}
        offer.characteristics = metadata
        offer.save(update_fields=['characteristics'])
        updated += 1
        pairs += len(grouped)
    Activity.objects.create(user=user, action='catalogue_fitment_import', details={
        'supplier': supplier.name, 'offers': updated, 'make_model_pairs': pairs,
        'date': date.isoformat()})
    return updated
