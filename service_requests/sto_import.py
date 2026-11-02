"""Bounded MapsGrabs archive reader and additive, non-dispatching directory import."""
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from urllib.parse import urlsplit

from openpyxl import load_workbook
from core.kazakhstan_locations import canonical_kazakhstan_city

MARKER = 'STO_ARCHIVE_20261102'
REPAIR = re.compile(r'автосервис|кузовной ремонт|ремонт .*?(двигател|авто|акпп|мкпп|инжектор|турбин|кардан|карбюратор|рулев|дизель|выхлоп|стартер|кузов|тормоз|ходов|электромобил|гибрид|мототех|спецтех|автобус)|шиномонтаж|развал|замена масла|диагностика автомобилей|сварочные работы|обслуживание автомобильных', re.I)


def norm(value):
    return re.sub(r'[^\w]', '', str(value or '').casefold())


def phones(value):
    result = []
    for part in str(value or '').split(','):
        p = re.sub(r'\D', '', part)
        if len(p) == 11 and p[0] in '78':
            p = '7' + p[1:]
            if p not in result:
                result.append(p)
    return result


def links(value):
    return [v.strip() for v in str(value or '').split(',') if urlsplit(v.strip()).scheme in ('http', 'https') and urlsplit(v.strip()).netloc]


def read_archive(data):
    if len(data) > 10_000_000:
        raise ValueError('Архив превышает 10 МБ.')
    ready, issues, seen = [], [], set()
    counts = Counter()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 100 or sum(x.file_size for x in members) > 30_000_000:
            raise ValueError('Слишком большой распакованный архив.')
        for member in members:
            if not member.filename.lower().endswith('.xlsx'):
                continue
            content = archive.read(member)
            with zipfile.ZipFile(io.BytesIO(content)) as inner:
                if sum(x.file_size for x in inner.infolist()) > 30_000_000:
                    raise ValueError('Слишком большая книга Excel.')
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            try:
                if 'Организации' not in book.sheetnames:
                    counts['unrelated_files'] += 1
                    continue
                values = iter(book['Организации'].values)
                header = next(values)
                for number, values_row in enumerate(values, 2):
                    if not any(v is not None for v in values_row):
                        continue
                    counts['source_rows'] += 1
                    if counts['source_rows'] > 10000:
                        raise ValueError('Более 10000 строк в архиве.')
                    row = dict(zip(header, values_row))
                    external_id = str(row.get('ID организации') or '').strip()
                    reason = ''
                    if not re.fullmatch(r'\d{8,17}', external_id):
                        reason = 'invalid_id'
                    elif external_id in seen:
                        reason = 'duplicate_id'
                    else:
                        seen.add(external_id)
                    categories = str(row.get('Категории') or '')
                    city_raw = str(row.get('Город') or '').strip()
                    city = canonical_kazakhstan_city({'Арысь': 'Арыс', 'Кульсары': 'Кулсары'}.get(city_raw, city_raw)) if city_raw else ''
                    name = str(row.get('Название') or '').strip()
                    address = str(row.get('Адрес') or '').strip()
                    contact = phones(row.get('Контакты'))
                    socials = links(row.get('Соцсети'))
                    wa = []
                    for link in socials:
                        parsed = urlsplit(link)
                        if parsed.netloc.lower() in ('wa.me', 'www.wa.me'):
                            wa += phones(parsed.path.strip('/'))
                    contact = list(dict.fromkeys(contact + wa))
                    if not reason:
                        if not REPAIR.search(categories): reason = 'non_repair'
                        elif not city: reason = 'city_missing_or_unknown'
                        elif not address: reason = 'address_missing'
                        elif not contact: reason = 'phone_missing'
                        elif not name or len(name) > 255 or len(address) > 255: reason = 'invalid_fields'
                    if reason:
                        counts[reason] += 1
                        issues.append({'id': external_id, 'name': name, 'reason': reason, 'file': member.filename, 'row': number})
                        continue
                    source = 'https://2gis.ru/firm/' + external_id
                    services = [s.strip() for s in categories.split('|') if REPAIR.search(s)]
                    notes = {'source': source, 'file': member.filename, 'row': number,
                             'phones': contact, 'socials': socials, 'websites': links(row.get('Сайты')),
                             'email': row.get('Email'), 'coordinates': row.get('Координаты'),
                             'rating_2gis': row.get('Рейтинг'), 'ratings_count_2gis': row.get('Оценок'),
                             'categories': categories}
                    ready.append({'id': external_id, 'name': name, 'city': city, 'address': address,
                                  'phones': contact, 'wa': list(dict.fromkeys(wa)), 'services': services,
                                  'map_link': source, 'description': 'Услуги: ' + '; '.join(services) + '.',
                                  'website': next((u for u in notes['websites'] if len(u) <= 200 and not any(t in urlsplit(u).netloc for t in ('tiktok.', 'instagram.', 'wa.me'))), ''),
                                  'instagram': next((u for u in socials if urlsplit(u).netloc.lower() in ('instagram.com', 'www.instagram.com') and len(u) <= 200), ''),
                                  'notes': MARKER + ' ' + external_id + '\n' + json.dumps(notes, ensure_ascii=False)})
            finally:
                book.close()
    counts['ready'] = len(ready)
    return ready, dict(counts), issues


def apply_rows(rows, user, dry_run=True):
    from django.contrib.auth.hashers import make_password
    from django.db import transaction, connection
    from service_requests.models import ServiceSeller, Service
    results = []
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_xact_lock(%s)', [2026110201])
        existing = list(ServiceSeller.objects.select_for_update().all())
        occupied = {p for s in existing for p in phones(s.whatsapp)}
        by_source, by_address = defaultdict(list), defaultdict(list)
        def index_seller(seller):
            source = re.search(r'/firm/(\d+)(?:[/?#]|$)', seller.map_link or '')
            if source:
                by_source[source[1]].append(seller)
            else:
                for source_id in re.findall(re.escape(MARKER) + r' (\d+)\n', seller.catalog_notes):
                    by_source[source_id].append(seller)
                if norm(seller.name) not in ('сто', 'cto'):
                    by_address[(norm(seller.name), norm(seller.city), norm(seller.address))].append(seller)
        for seller in existing:
            index_seller(seller)
        for row in rows:
            exact = by_source[row['id']]
            address = by_address[(norm(row['name']), norm(row['city']), norm(row['address']))]
            # Distinct source IDs are distinct records even for anonymous STOs
            # sharing a yard. Never use address similarity to override source ID.
            address = [s for s in address if not re.search(r'/firm/\d+', s.map_link or '') and norm(s.name) not in ('сто', 'cto')]
            matches = exact or address
            if len(matches) > 1:
                results.append({'id': row['id'], 'name': row['name'], 'status': 'ambiguous'})
                continue
            seller = matches[0] if matches else None
            if seller and (seller.user_id or seller.is_test_seller or seller.is_paused or not seller.is_active or seller.seller_type != 'sto' or not seller.catalog_only):
                results.append({'id': row['id'], 'name': row['name'], 'status': 'protected'})
                continue
            new = seller is None
            if new:
                phone = next((p for p in row['wa'] + row['phones'] if p not in occupied), '')
                if not phone:
                    results.append({'id': row['id'], 'name': row['name'], 'city': row['city'], 'status': 'shared_phone_review'})
                    continue
                seller = ServiceSeller(name=row['name'], city=row['city'], whatsapp=phone,
                                       password=make_password(None), seller_type='sto', catalog_only=True,
                                       receive_requests=False, is_active=True)
                occupied.update(phones(seller.whatsapp))
            changed = []
            for field in ('address', 'description', 'website', 'instagram', 'map_link'):
                if not getattr(seller, field) and row[field]:
                    setattr(seller, field, row[field]); changed.append(field)
            marker = MARKER + ' ' + row['id'] + '\n'
            if marker not in seller.catalog_notes:
                seller.catalog_notes = (seller.catalog_notes + '\n' + row['notes']).strip()
                changed.append('catalog_notes')
            if not dry_run:
                if new: seller.save()
                elif changed: seller.save(update_fields=changed)
                if new:
                    for name in row['services']:
                        service, _ = Service.objects.get_or_create(name=name[:100])
                        seller.services.add(service)
                if new or changed:
                    from django.contrib.admin.models import LogEntry, ADDITION, CHANGE
                    LogEntry.objects.log_actions(user_id=user.pk, queryset=ServiceSeller.objects.filter(pk=seller.pk), action_flag=ADDITION if new else CHANGE, change_message=MARKER)
            if new: index_seller(seller)
            results.append({'id': row['id'], 'name': row['name'], 'city': row['city'],
                            'status': 'created' if new else 'updated' if changed else 'unchanged',
                            'pk': seller.pk})
    return results


def repair_import_aliases(rows, user):
    """Undo only this import's accidental address aliases; preserve original rows."""
    from django.db import transaction
    from django.contrib.admin.models import LogEntry, CHANGE
    from service_requests.models import ServiceSeller
    by_id = {r['id']: r for r in rows}
    repaired = 0
    with transaction.atomic():
        for seller in ServiceSeller.objects.select_for_update().filter(catalog_only=True, user__isnull=True, receive_requests=False, is_active=True, is_paused=False, is_test_seller=False, catalog_notes__startswith=MARKER):
            ids = re.findall(re.escape(MARKER) + r' (\d+)\n', seller.catalog_notes)
            if len(ids) < 2:
                continue
            original = by_id.get(ids[0])
            if not original or seller.map_link != original['map_link'] or seller.whatsapp not in original['phones']:
                continue
            if (seller.name, seller.city, seller.address, seller.description) != (original['name'], original['city'], original['address'], original['description']):
                continue
            # Only empty original links could have been filled by an alias.
            fields = ['catalog_notes']
            seller.catalog_notes = original['notes']
            for field in ('website', 'instagram'):
                aliases = [by_id[i] for i in ids[1:] if i in by_id]
                if not original[field] and any(getattr(seller, field) == alias[field] for alias in aliases):
                    setattr(seller, field, ''); fields.append(field)
            seller.save(update_fields=fields)
            LogEntry.objects.log_actions(user_id=user.pk, queryset=ServiceSeller.objects.filter(pk=seller.pk), action_flag=CHANGE, change_message=MARKER + ' исправлено совпадение адресов разных ID')
            repaired += 1
    return repaired
