import csv
import io
import itertools
import zipfile
from decimal import Decimal, InvalidOperation

from django.db import transaction
from .models import Supplier, Offer, Activity

ALIASES = {'Артикул': 'article', 'Бренд': 'brand', 'Наименование': 'name', 'Категория': 'category',
           'Оптовая цена, ₸': 'cost', 'Остаток на сайте': 'stock', 'Мин. заказ, шт.': 'min_order',
           'Карточка товара': 'supplier_url', 'Цена продажи на Kaspi, ₸': 'kaspi_price', 'Ссылка на Kaspi': 'kaspi_url'}
ALIASES.update({'Торговая марка': 'brand', 'Цена, за 1шт': 'cost', 'Цена, за 1шт со скидкой': 'cost',
                'Остаток': 'stock', 'Миним заказ, шт': 'min_order', 'В продаже': 'active'})
REQUIRED = {'article', 'brand', 'name', 'cost'}


def load_rows(file, required=None):
    required = required or REQUIRED
    if file.name.lower().endswith('.xlsx'):
        from openpyxl import load_workbook
        with zipfile.ZipFile(file) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError('Распакованный Excel превышает 100 МБ.')
        file.seek(0)
        book = load_workbook(file, read_only=True, data_only=True)
        try:
            sheet = book['Оптовой каталог'] if 'Оптовой каталог' in book.sheetnames else book.worksheets[0]
            rows = list(itertools.islice(sheet.iter_rows(values_only=True), 20031))
        finally:
            book.close()
    else:
        text = file.read().decode('utf-8-sig')
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        rows = list(itertools.islice(csv.reader(io.StringIO(text), dialect), 20031))
    if len(rows) > 20030:
        raise ValueError('Не более 20 000 строк данных и 30 строк заголовка.')
    for index, row in enumerate(rows[:30]):
        keys = [ALIASES.get(str(v).strip(), str(v).strip()) for v in row]
        if required.issubset(keys):
            result = []
            for number, row in enumerate(rows[index + 1:], index + 2):
                if not any(v is not None and str(v).strip() for v in row):
                    continue
                result.append((number, dict(zip(keys, row))))
                if len(result) > 20000:
                    raise ValueError('Не более 20 000 строк за загрузку.')
            if not result:
                raise ValueError('Прайс пуст.')
            return result
    raise ValueError('Не найдены обязательные колонки: ' + ', '.join(sorted(required)))


def money(value, optional=False):
    if optional and (value is None or str(value).strip() in ('', 'Не найдено')):
        return None
    try:
        result = Decimal(str(value).replace(' ', '').replace('\u00a0', '').replace(',', '.'))
    except InvalidOperation:
        raise ValueError('Цена должна быть числом.')
    if not result.is_finite() or result < 0 or result >= Decimal('1000000000000') or result.as_tuple().exponent < -2:
        raise ValueError('Недопустимая цена: нужны неотрицательные тенге, до двух знаков после запятой.')
    return result


@transaction.atomic
def import_offers(file, supplier_name, date, user):
    rows = load_rows(file)
    supplier, _ = Supplier.objects.get_or_create(name=supplier_name.strip())
    seen = set()
    for number, row in rows:
        article, brand, name = [str(row.get(k) or '').strip() for k in ('article', 'brand', 'name')]
        if not article or not brand or not name:
            raise ValueError(f'Строка {number}: не заполнены артикул, бренд или название.')
        key = (brand, article)
        if key in seen:
            raise ValueError(f'Строка {number}: дублируется бренд и артикул.')
        seen.add(key)
        try:
            minimum = int(row.get('min_order') or 1)
            if minimum < 1:
                raise ValueError('Минимальный заказ должен быть больше нуля.')
            defaults = dict(name=name, category=str(row.get('category') or ''), cost=money(row['cost']),
                            stock=str(row.get('stock') or ''), min_order=minimum, checked_on=date)
            from django.core.validators import URLValidator
            from django.core.exceptions import ValidationError
            for field in ('supplier_url', 'kaspi_url', 'photo_url'):
                if row.get(field):
                    value = str(row[field]).strip()
                    try:
                        URLValidator(schemes=['http', 'https'])(value)
                    except ValidationError:
                        raise ValueError('Некорректная ссылка.')
                    defaults[field] = value
            for field in ('description', 'applicability'):
                if field in row:
                    defaults[field] = str(row[field] or '')
            if 'kaspi_price' in row:
                defaults['kaspi_price'] = money(row['kaspi_price'], optional=True)
            offer, created = Offer.objects.get_or_create(supplier=supplier, brand=brand, article=article, defaults=defaults)
            if not created:
                changed_match = defaults.get('kaspi_url', offer.kaspi_url) != offer.kaspi_url
                for field, value in defaults.items():
                    setattr(offer, field, value)
                if changed_match:
                    from .models import KaspiSellerSnapshot
                    KaspiSellerSnapshot.objects.filter(offer=offer).delete()
                    offer.match_verified = False
                    for field in ('review_count', 'recent_reviews', 'sales_30', 'last_review', 'evidence_date'):
                        setattr(offer, field, None)
                offer.full_clean()
                offer.save()
            else:
                offer.full_clean()
        except (ValueError, TypeError, OverflowError, ValidationError) as error:
            raise ValueError(f'Строка {number}: {error}')
    Activity.objects.create(user=user, action='import', details={'supplier': supplier.name, 'rows': len(rows)})
    return len(rows)


@transaction.atomic
def import_demand(file, supplier_name, date, user):
    from datetime import datetime
    supplier = Supplier.objects.filter(name=supplier_name.strip()).first()
    if supplier is None:
        raise ValueError('Сначала загрузите прайс выбранного поставщика.')
    rows = load_rows(file, required={'article', 'brand'})
    seen = set()
    for number, row in rows:
        key = (str(row.get('brand') or '').strip(), str(row.get('article') or '').strip())
        if key in seen:
            raise ValueError(f'Строка {number}: дублируется бренд и артикул.')
        seen.add(key)
        offer = Offer.objects.filter(supplier=supplier, brand=key[0], article=key[1]).first()
        if offer is None:
            raise ValueError(f'Строка {number}: товар не найден у поставщика.')
        changes = {}
        for field in ('sales_30', 'review_count', 'recent_reviews'):
            value = row.get(field)
            if value is None or str(value).strip() == '':
                continue
            try:
                decimal = Decimal(str(value))
                if not decimal.is_finite() or decimal < 0 or decimal != decimal.to_integral_value() or decimal > 2147483647:
                    raise ValueError()
                changes[field] = int(decimal)
            except (InvalidOperation, ValueError):
                raise ValueError(f'Строка {number}: {field} должно быть неотрицательным целым числом.')
        if not changes:
            raise ValueError(f'Строка {number}: нет показателей спроса.')
        if 'recent_reviews' in changes and 'review_count' in changes and changes['recent_reviews'] > changes['review_count']:
            raise ValueError(f'Строка {number}: свежих отзывов больше общего количества.')
        if 'sales_30' in changes:
            # Make the reporting window explicit instead of guessing it from a file name.
            from datetime import timedelta
            expected_start = date - timedelta(days=29)
            if str(row.get('period_start') or '') != expected_start.isoformat() or str(row.get('period_end') or '') != date.isoformat():
                raise ValueError(f'Строка {number}: для sales_30 нужны period_start={expected_start.isoformat()} и period_end={date.isoformat()}.')
        if row.get('last_review'):
            try:
                last_review = datetime.strptime(str(row['last_review']), '%Y-%m-%d').date()
            except ValueError:
                raise ValueError(f'Строка {number}: дата отзыва должна быть YYYY-MM-DD.')
            if last_review > date:
                raise ValueError(f'Строка {number}: отзыв позднее даты проверки.')
            changes['last_review'] = last_review
        # Each report is a fresh evidence snapshot. Missing counters stay unknown,
        # rather than silently renewing an older report's numbers.
        for field in ('sales_30', 'review_count', 'recent_reviews', 'last_review'):
            setattr(offer, field, changes.get(field))
        offer.evidence_date = date
        offer.evidence_source = f'Загруженный отчёт: {file.name}'[:250]
        offer.full_clean()
        offer.save()
    Activity.objects.create(user=user, action='demand_import', details={'supplier': supplier.name, 'rows': len(rows), 'date': date.isoformat()})
    return len(rows)


@transaction.atomic
def import_enrichment(file, supplier_name, date, user):
    """Apply sourced fitment only; never renew prices, sales or Kaspi verification."""
    import json
    from urllib.parse import urlsplit
    from django.core.validators import URLValidator
    from django.core.exceptions import ValidationError
    from .models import VehicleFitment
    from .categories import OMEGA_CATEGORIES

    supplier = Supplier.objects.filter(name=supplier_name.strip()).first()
    if supplier is None:
        raise ValueError('Сначала загрузите прайс поставщика.')
    try:
        payload = json.load(file)
    except (ValueError, UnicodeError):
        raise ValueError('Нужен JSON-файл применимости Omega.')
    if not isinstance(payload, dict) or payload.get('checked_on') != date.isoformat():
        raise ValueError('Дата файла должна совпадать с датой проверки.')
    rows = payload.get('offers')
    if not isinstance(rows, list) or not rows or len(rows) > 20000:
        raise ValueError('Нужен список offers: от 1 до 20 000 товаров.')
    seen = set()
    total_fitments = 0
    for row in rows:
        if not isinstance(row, dict) or row.get('error'):
            raise ValueError('Файл содержит непроверенную карточку или ошибку чтения.')
        key = (str(row.get('brand', '')).strip(), str(row.get('article', '')).strip())
        if key in seen:
            raise ValueError('Бренд и артикул дублируются.')
        seen.add(key)
        offer = Offer.objects.filter(supplier=supplier, brand=key[0], article=key[1]).first()
        if offer is None:
            raise ValueError('Товар из файла не найден у выбранного поставщика.')
        source = row.get('source_url', '')
        if not isinstance(source, str):
            raise ValueError('Не указана ссылка на источник.')
        try:
            URLValidator(schemes=['https'])(source)
        except ValidationError:
            raise ValueError('Источник должен быть HTTPS-ссылкой Omega.')
        if urlsplit(source).hostname != 'omega-auto-parts.kz' or source.rstrip('/') != offer.supplier_url.rstrip('/'):
            raise ValueError('Источник не совпадает с карточкой товара Omega в прайсе.')
        category = row.get('category', '')
        if category == 'Техобслуживание и сервис':
            category = 'Тех обслуживание и сервис'
        if category not in OMEGA_CATEGORIES:
            raise ValueError('Категория отсутствует в каталоге Omega.')
        fitments = row.get('fitments', [])
        if not isinstance(fitments, list) or len(fitments) > 20000:
            raise ValueError('Некорректный список модификаций.')
        grouped = {}
        names = {'Поколение (Omega)': 'generation', 'Шасси': 'chassis', 'Годы': 'years',
                 'Двигатель': 'engine', 'Объём': 'capacity', 'Топливо': 'fuel', 'КПП': 'transmission',
                 'Кузов': 'body', 'Привод': 'drive'}
        for fit in fitments:
            if not isinstance(fit, dict):
                raise ValueError('Некорректная применимость.')
            make, model = fit.get('make'), fit.get('model')
            if not isinstance(make, str) or not make.strip() or len(make) > 100 or not isinstance(model, str) or not model.strip() or len(model) > 150:
                raise ValueError('Для применимости обязательны марка и модель.')
            specs = fit.get('specifications', {})
            if not isinstance(specs, dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 500 for k, v in specs.items()):
                raise ValueError('Некорректные параметры автомобиля.')
            configuration = {names[k]: v for k, v in specs.items() if k in names and v}
            group = grouped.setdefault((make.strip(), model.strip()), [])
            if configuration not in group:
                group.append(configuration)
        existing_items = {(item.make, item.model): item for item in
                          VehicleFitment.objects.filter(offer=offer, source_url=source, details='')}
        creates, updates = [], []
        for (make, model), configurations in grouped.items():
            item = existing_items.get((make, model)) or VehicleFitment(offer=offer, make=make, model=model, source_url=source)
            item.configurations = configurations
            item.verified = True
            # FK and unique identity are already resolved above; the database keeps
            # the unique constraint. Avoid thousands of redundant validation queries.
            item.full_clean(exclude=['offer'], validate_unique=False, validate_constraints=False)
            (updates if item.pk else creates).append(item)
        VehicleFitment.objects.bulk_create(creates, batch_size=200)
        if updates:
            VehicleFitment.objects.bulk_update(updates, ['configurations', 'verified'], batch_size=200)
        total_fitments += len(grouped)
        characteristics = dict(offer.characteristics or {})
        characteristics['omega_applicability_source'] = source
        characteristics['omega_applicability_checked_on'] = date.isoformat()
        characteristics['omega_applicability_status'] = 'sourced' if grouped else 'not_provided'
        offer.category = category
        description = row.get('description')
        if isinstance(description, str) and description:
            offer.description = description
        offer.characteristics = characteristics
        offer.save(update_fields=['category', 'description', 'characteristics'])
    Activity.objects.create(user=user, action='fitment_import', details={'supplier': supplier.name, 'offers': len(rows), 'make_model_pairs': total_fitments, 'date': date.isoformat()})
    return len(rows)


@transaction.atomic
def import_competitors(file, supplier_name, date, user):
    import json
    import re
    from urllib.parse import urlsplit
    from django.core.validators import URLValidator
    from django.core.exceptions import ValidationError
    from .models import KaspiSellerSnapshot
    supplier = Supplier.objects.filter(name=supplier_name.strip()).first()
    if supplier is None:
        raise ValueError('Поставщик не найден.')
    try:
        payload = json.load(file)
    except (ValueError, UnicodeError):
        raise ValueError('Нужен JSON-файл продавцов Kaspi.')
    if not isinstance(payload, dict) or payload.get('checked_on') != date.isoformat():
        raise ValueError('Дата файла не совпадает с датой проверки.')
    rows = payload.get('offers')
    if not isinstance(rows, list) or not rows or len(rows) > 20000:
        raise ValueError('Нужен список offers от 1 до 20 000 товаров.')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('error'):
            raise ValueError('Карточка содержит ошибку проверки.')
        key = (str(row.get('brand', '')).strip(), str(row.get('article', '')).strip())
        if key in seen:
            raise ValueError('Дублируются бренд и артикул.')
        seen.add(key)
        offer = Offer.objects.filter(supplier=supplier, brand=key[0], article=key[1]).first()
        source = row.get('source_url', '')
        if offer is None or not offer.kaspi_url or source != offer.kaspi_url:
            raise ValueError('Источник не совпадает с карточкой Kaspi выбранного товара.')
        if urlsplit(source).hostname != 'kaspi.kz' or urlsplit(source).scheme != 'https' or not re.search(r'/shop/p/.+-[0-9]+/', source):
            raise ValueError('Некорректная карточка Kaspi.')
        if row.get('city') != '750000000' or not isinstance(row.get('complete'), bool):
            raise ValueError('Нужны данные Алматы и признак полноты проверки.')
        sellers = row.get('sellers')
        if not isinstance(sellers, list) or len(sellers) > 1000:
            raise ValueError('Некорректный список продавцов.')
        normalized = {}
        for seller in sellers:
            if not isinstance(seller, dict):
                raise ValueError('Некорректный продавец.')
            merchant = str(seller.get('merchant_id', ''))
            name = str(seller.get('name') or '').strip()
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', merchant) or not name or len(name) > 250:
                raise ValueError('Не указаны магазин и идентификатор продавца.')
            price = money(seller.get('price'))
            if price <= 0:
                raise ValueError('Цена продавца должна быть больше нуля.')
            rating = seller.get('rating')
            if rating is not None:
                try:
                    rating = Decimal(str(rating))
                    if not rating.is_finite() or not 0 <= rating <= 5:
                        raise ValueError()
                except (ValueError, InvalidOperation):
                    raise ValueError('Рейтинг должен быть от 0 до 5 или пустым.')
            reviews = seller.get('reviews')
            if reviews is not None and (isinstance(reviews, bool) or not isinstance(reviews, int) or not 0 <= reviews <= 2147483647):
                raise ValueError('Количество отзывов должно быть неотрицательным целым или пустым.')
            link = seller.get('url', '')
            try:
                URLValidator(schemes=['https'])(link)
            except ValidationError:
                raise ValueError('Некорректная ссылка магазина.')
            if urlsplit(link).hostname != 'kaspi.kz' or not urlsplit(link).path.startswith(f'/shop/m/{merchant}/'):
                raise ValueError('Ссылка не соответствует продавцу Kaspi.')
            item = {'merchant_id': merchant, 'name': name, 'url': link, 'price': str(price),
                    'rating': str(rating) if rating is not None else None, 'reviews': reviews}
            if merchant not in normalized or price < Decimal(normalized[merchant]['price']):
                normalized[merchant] = item
        KaspiSellerSnapshot.objects.update_or_create(offer=offer, defaults={'source_url': source, 'city': row['city'],
            'checked_on': date, 'complete': row['complete'], 'sellers': list(normalized.values())})
    Activity.objects.create(user=user, action='competitors_import', details={'supplier': supplier.name, 'offers': len(rows), 'date': date.isoformat()})
    return len(rows)


@transaction.atomic
def import_matches(file, supplier_name, date, user):
    import json
    from datetime import date as Date
    from urllib.parse import urlsplit
    from .matching import compare_identity
    payload = json.load(file)
    if not isinstance(payload, dict) or payload.get('checked_on') != date.isoformat():
        raise ValueError('Дата файла не совпадает с датой проверки.')
    rows = payload.get('offers')
    if not isinstance(rows, list) or not rows or len(rows) > 20000:
        raise ValueError('Нужен список offers от 1 до 20 000 товаров.')
    supplier = Supplier.objects.get(name=supplier_name.strip())
    seen = set()
    for row in rows:
        offer = Offer.objects.select_for_update().get(supplier=supplier, brand=row['brand'], article=row['article'])
        if offer.pk in seen:
            raise ValueError('Повтор товара в файле.')
        seen.add(offer.pk)
        source = row.get('source_url', '')
        if urlsplit(source).hostname != 'kaspi.kz' or source != offer.kaspi_url:
            raise ValueError('Ссылка не совпадает с текущей карточкой Kaspi.')
        source_date = Date.fromisoformat(row['source_date'])
        if source_date > date:
            raise ValueError('Дата источника позже даты проверки.')
        title = row.get('title', '')
        if not isinstance(title, str) or len(title) > 1000:
            raise ValueError('Неверное название Kaspi.')
        confirmed, reason = compare_identity(offer, title, row.get('kaspi_article'), row.get('kaspi_brand'))
        # A staff review may resolve wording/applicability differences in the name,
        # but never override a missing/different article or brand.
        if not confirmed and row.get('name_reviewed') is True and reason.startswith('Название'):
            import re
            from .matching import normalize
            article_ok = normalize(row.get('kaspi_article')) == normalize(offer.article)
            brand_ok = normalize(row.get('kaspi_brand')) == normalize(offer.brand)
            observed = all(re.search(r'(?<!\w)' + re.escape(normalize(value)) + r'(?!\w)', normalize(title))
                           for value in (offer.article, offer.brand))
            if article_ok and brand_ok and observed:
                confirmed, reason = True, 'Название и комплектность проверены; артикул и бренд совпадают'
        evidence = {'source_url': source, 'source_date': source_date.isoformat(), 'checked_on': date.isoformat(),
                    'title': title, 'article': row.get('kaspi_article'), 'brand': row.get('kaspi_brand'),
                    'confirmed': confirmed, 'reason': reason, 'name_reviewed': row.get('name_reviewed') is True, 'method': 'article_name_brand_v1'}
        offer.characteristics = {**offer.characteristics, 'kaspi_identity': evidence}
        offer.match_verified = confirmed
        offer.save(update_fields=['characteristics', 'match_verified'])
        Activity.objects.create(user=user, action='match_checked', details={'offer': offer.pk, **evidence})
    return len(rows)
