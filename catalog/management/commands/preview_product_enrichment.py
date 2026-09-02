import csv
import json
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from catalog.models import Brand, CarModel, Category, Product
from catalog.product_assistant import preview_enrichment_for_product

DEFAULT_PREVIEW_STEM = 'product_enrichment_preview'
STEM_RE = re.compile(r'^[A-Za-z0-9._-]+$')

CSV_COLUMNS = (
    'product_id',
    'current_article',
    'current_title',
    'current_brand',
    'current_brand_id',
    'current_category',
    'current_category_id',
    'current_compatibility',
    'current_engine_compatibility',
    'current_oem_cross_references',
    'current_description',
    'suggested_title',
    'suggested_brand',
    'suggested_brand_id',
    'suggested_category',
    'suggested_category_id',
    'suggested_compatibility',
    'suggested_engine_compatibility',
    'suggested_oem_cross_references',
    'suggested_description',
    'research_notes',
    'evidence_notes',
    'sources',
    'ai_used',
    'web_search_used',
    'source_count',
    'confidence',
    'approved_fields',
    'blocked_fields',
    'dictionary_additions',
    'unresolved_fields',
)


def parse_product_ids(raw: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for part in str(raw or '').replace(';', ',').split(','):
        token = part.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise CommandError(f'Некорректный product id: {token}') from exc
        if value in seen:
            continue
        seen.add(value)
        ids.append(value)
    return ids


def validate_preview_stem(stem: str) -> str:
    text = str(stem or '').strip()
    if not text or not STEM_RE.fullmatch(text) or text in {'.', '..'}:
        raise CommandError(f'Некорректный --stem: {stem}')
    return text


def _git_snapshot_meta() -> dict:
    meta: dict[str, str] = {}
    root = Path(settings.BASE_DIR)
    try:
        commit = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=root,
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return meta
    if commit:
        meta['git_commit'] = commit
    try:
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=root,
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        branch = ''
    if branch:
        meta['git_branch'] = branch
    return meta


def build_snapshot_metadata(stem: str) -> dict:
    meta = {
        'generated_at': timezone.now().isoformat(),
        'stem': stem,
    }
    meta.update(_git_snapshot_meta())
    return meta


def _join_notes(notes) -> str:
    parts = []
    for item in notes or []:
        if isinstance(item, dict):
            text = str(item.get('text') or '').strip()
            severity = str(item.get('severity') or '').strip()
            if text and severity:
                parts.append(f'{severity}: {text}')
            elif text:
                parts.append(text)
        elif str(item or '').strip():
            parts.append(str(item).strip())
    return ' | '.join(parts)


def _join_sources(sources) -> str:
    parts = []
    for item in sources or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get('title') or '').strip()
        url = str(item.get('url') or '').strip()
        if title and url and title != url:
            parts.append(f'{title} <{url}>')
        else:
            parts.append(title or url)
    return ' | '.join(parts)


def _join_unresolved(rows) -> str:
    parts = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get('field') or '').strip()
        reason = str(item.get('reason') or '').strip()
        if field_name and reason:
            parts.append(f'{field_name}: {reason}')
        elif reason:
            parts.append(reason)
    return ' | '.join(parts)


def _join_list(items) -> str:
    return ', '.join(str(item).strip() for item in items or [] if str(item).strip())


def write_enrichment_preview_reports(
    rows: list[dict],
    *,
    report_dir: Path,
    stem: str = DEFAULT_PREVIEW_STEM,
    metadata: dict | None = None,
):
    report_dir.mkdir(parents=True, exist_ok=True)
    csv_path = report_dir / f'{stem}.csv'
    json_path = report_dir / f'{stem}.json'
    with csv_path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                'product_id': row.get('product_id') or '',
                'current_article': row.get('current_article') or '',
                'current_title': row.get('current_title') or '',
                'current_brand': row.get('current_brand') or '',
                'current_brand_id': '' if row.get('current_brand_id') in (None, '') else row.get('current_brand_id'),
                'current_category': row.get('current_category') or '',
                'current_category_id': '' if row.get('current_category_id') in (None, '') else row.get('current_category_id'),
                'current_compatibility': row.get('current_compatibility') or '',
                'current_engine_compatibility': row.get('current_engine_compatibility') or '',
                'current_oem_cross_references': row.get('current_oem_cross_references') or '',
                'current_description': row.get('current_description') or '',
                'suggested_title': row.get('suggested_title') or '',
                'suggested_brand': row.get('suggested_brand') or '',
                'suggested_brand_id': '' if row.get('suggested_brand_id') in (None, '') else row.get('suggested_brand_id'),
                'suggested_category': row.get('suggested_category') or '',
                'suggested_category_id': '' if row.get('suggested_category_id') in (None, '') else row.get('suggested_category_id'),
                'suggested_compatibility': row.get('suggested_compatibility') or '',
                'suggested_engine_compatibility': row.get('suggested_engine_compatibility') or '',
                'suggested_oem_cross_references': row.get('suggested_oem_cross_references') or '',
                'suggested_description': row.get('suggested_description') or '',
                'research_notes': _join_notes(row.get('research_notes')),
                'evidence_notes': _join_notes(row.get('evidence_notes')),
                'sources': _join_sources(row.get('sources')),
                'ai_used': row.get('ai_used'),
                'web_search_used': row.get('web_search_used'),
                'source_count': row.get('source_count') if row.get('source_count') is not None else '',
                'confidence': row.get('confidence') or '',
                'approved_fields': _join_list(row.get('approved_fields')),
                'blocked_fields': _join_list(row.get('blocked_fields')),
                'dictionary_additions': json.dumps(
                    row.get('dictionary_additions') or {'brands': [], 'categories': []},
                    ensure_ascii=False,
                ),
                'unresolved_fields': _join_unresolved(row.get('unresolved_fields')),
            })
    payload = dict(metadata or {})
    payload['summary'] = {
        'total': len(rows),
        'ok': sum(1 for item in rows if item.get('ok')),
        'missing': sum(1 for item in rows if item.get('error') == 'не найден'),
    }
    payload['products'] = rows
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return csv_path, json_path


class Command(BaseCommand):
    help = (
        'Пакетный preview AI-обогащения карточек. '
        'Ничего не записывает в Product, Brand, Category, CarModel. '
        'Пишет CSV/JSON отчёт.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--product-ids',
            required=True,
            help='Список id через запятую, например 2130,2131,2132',
        )
        parser.add_argument(
            '--report',
            default='',
            help='Каталог для CSV/JSON. По умолчанию var/reports/',
        )
        parser.add_argument(
            '--stem',
            default='',
            help=(
                'Имя immutable snapshot без расширения. '
                f'По умолчанию {DEFAULT_PREVIEW_STEM} (можно перезаписывать).'
            ),
        )
        parser.add_argument(
            '--overwrite',
            action='store_true',
            default=False,
            help='Разрешить перезапись существующего именованного snapshot.',
        )

    def handle(self, *args, **options):
        ids = parse_product_ids(options.get('product_ids') or '')
        if not ids:
            raise CommandError('Укажите --product-ids')

        explicit_stem = str(options.get('stem') or '').strip()
        stem = validate_preview_stem(explicit_stem or DEFAULT_PREVIEW_STEM)
        report_dir = Path(options['report'] or (Path(settings.BASE_DIR) / 'var' / 'reports'))
        csv_path = report_dir / f'{stem}.csv'
        json_path = report_dir / f'{stem}.json'
        if explicit_stem and not options.get('overwrite') and (csv_path.exists() or json_path.exists()):
            raise CommandError(
                f'Snapshot «{stem}» уже существует. Укажите --overwrite, чтобы заменить.'
            )

        products = list(
            Product.objects.filter(pk__in=ids).select_related(
                'brand',
                'brand__country',
                'car_model',
                'category',
                'seller_profile',
            )
        )
        by_id = {item.pk: item for item in products}
        product_count = Product.objects.count()
        snapshots = {
            item.pk: {
                'price': item.price,
                'status': item.status,
                'stock_qty': item.stock_qty,
                'seller_profile_id': item.seller_profile_id,
                'seller_name': item.seller_name,
                'title': item.title,
                'article': item.article,
                'description': item.description,
                'compatibility': item.compatibility,
                'brand_id': item.brand_id,
                'category_id': item.category_id,
                'main_image': str(item.main_image or ''),
            }
            for item in products
        }
        brand_count = Brand.objects.count()
        category_count = Category.objects.count()
        model_count = CarModel.objects.count()

        rows = []
        for product_id in ids:
            product = by_id.get(product_id)
            if product is None:
                rows.append({
                    'ok': False,
                    'error': 'не найден',
                    'product_id': product_id,
                    'current_article': '',
                    'current_title': '',
                    'current_brand': '',
                    'current_brand_id': None,
                    'current_category': '',
                    'current_category_id': None,
                    'current_compatibility': '',
                    'current_engine_compatibility': '',
                    'current_oem_cross_references': '',
                    'current_description': '',
                    'suggested_title': '',
                    'suggested_brand': '',
                    'suggested_brand_id': None,
                    'suggested_category': '',
                    'suggested_category_id': None,
                    'suggested_compatibility': '',
                    'suggested_engine_compatibility': '',
                    'suggested_oem_cross_references': '',
                    'suggested_description': '',
                    'research_notes': [],
                    'evidence_notes': [],
                    'sources': [],
                    'ai_used': False,
                    'web_search_used': False,
                    'source_count': 0,
                    'confidence': '',
                    'approved_fields': [],
                    'blocked_fields': [],
                    'field_decisions': {},
                    'dictionary_additions': {'brands': [], 'categories': []},
                    'unresolved_fields': [{'field': 'product', 'reason': 'Product не найден'}],
                    'unmatched': [],
                    'fields': {},
                })
                continue
            row = preview_enrichment_for_product(product)
            rows.append(row)
            self.stdout.write(
                f"{product_id} {row.get('current_article') or '-'} "
                f"confidence={row.get('confidence')} "
                f"web_search={row.get('web_search_used')} "
                f"sources={row.get('source_count')} "
                f"unresolved={len(row.get('unresolved_fields') or [])}"
            )

        metadata = build_snapshot_metadata(stem)
        csv_path, json_path = write_enrichment_preview_reports(
            rows,
            report_dir=report_dir,
            stem=stem,
            metadata=metadata,
        )

        for product in Product.objects.filter(pk__in=snapshots):
            before = snapshots[product.pk]
            if (
                product.price != before['price']
                or product.status != before['status']
                or product.stock_qty != before['stock_qty']
                or product.seller_profile_id != before['seller_profile_id']
                or product.seller_name != before['seller_name']
                or product.title != before['title']
                or product.article != before['article']
                or product.description != before['description']
                or product.compatibility != before['compatibility']
                or product.brand_id != before['brand_id']
                or product.category_id != before['category_id']
                or str(product.main_image or '') != before['main_image']
            ):
                raise CommandError('Preview изменил Product — это ошибка.')
        if Product.objects.count() != product_count:
            raise CommandError('Preview изменил число Product — это ошибка.')
        if (
            Brand.objects.count() != brand_count
            or Category.objects.count() != category_count
            or CarModel.objects.count() != model_count
        ):
            raise CommandError('Preview создал Brand/Category/CarModel — это ошибка.')

        self.stdout.write('SUMMARY:')
        self.stdout.write(f'total {len(rows)}')
        self.stdout.write(f"found {sum(1 for item in rows if item.get('ok'))}")
        self.stdout.write(f"missing {sum(1 for item in rows if item.get('error') == 'не найден')}")
        self.stdout.write(f'report_csv {csv_path}')
        self.stdout.write(f'report_json {json_path}')
        self.stdout.write('mode preview-only')
