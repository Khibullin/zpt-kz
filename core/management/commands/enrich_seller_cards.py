"""Fill existing seller cards from local 2GIS and Yandex Excel exports.

Does not create sellers, does not call external sites, and does not change
request delivery, activity, or publication.
"""

from django.core.management.base import BaseCommand, CommandError

from core.services.seller_card_enrichment import enrich_files


class Command(BaseCommand):
    help = (
        'Сверяет выгрузки 2ГИС и Яндекса с существующими продавцами. '
        'Без --apply данные не записываются.'
    )

    def add_arguments(self, parser):
        parser.add_argument('paths', nargs='+', help='Файлы .xlsx выгрузок MapsGrabs')
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Записать однозначно сопоставленные данные',
        )

    def handle(self, *args, **options):
        try:
            report = enrich_files(options['paths'], apply=options['apply'])
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Строк в файлах: {report["source_rows"]}')
        self.stdout.write(f'Организаций после объединения повторов: {report["organizations"]}')
        self.stdout.write(f'Однозначных совпадений: {len(report["matched"])}')
        self.stdout.write(f'Карточек к обогащению: {len(report["plans"])}')
        self.stdout.write(f'Без совпадения: {len(report["unmatched"])}')
        self.stdout.write(f'Неоднозначные: {len(report["ambiguous"])}')
        self.stdout.write(f'Конфликты: {len(report["conflicts"])}')
        self.stdout.write(f'Некорректные ссылки и поля: {len(report["invalid_links"])}')
        self.stdout.write(f'Подозрительные WhatsApp: {len(report["suspicious_whatsapp"])}')
        reasons = {}
        for row in report['unmatched']:
            reason = '; '.join(row['reasons'])
            reasons[reason] = reasons.get(reason, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
            self.stdout.write(f'ПРОПУСК {count}: {reason}')
        invalid_reasons = {}
        for row in report['invalid_links']:
            invalid_reasons[row['reason']] = invalid_reasons.get(row['reason'], 0) + 1
        for reason, count in sorted(invalid_reasons.items(), key=lambda item: -item[1]):
            self.stdout.write(f'НЕКОРРЕКТНО {count}: {reason}')
        for error in report['file_errors']:
            self.stdout.write(self.style.WARNING(error))
        for row in report['ambiguous'][:30]:
            self.stdout.write(f'НЕОДНОЗНАЧНО {row["name"]} / {row["external_id"]}: {"; ".join(row["reasons"])}')
        for row in report['conflicts'][:30]:
            label = row.get('seller_name') or row.get('name')
            self.stdout.write(f'КОНФЛИКТ {label} / {row.get("field")}: {row.get("current") or row.get("values")}')
        for row in report['suspicious_whatsapp'][:30]:
            self.stdout.write(f'WHATSAPP {row["name"]}: {row["reason"]} {row["url"]}')
        for row in report['invalid_links'][:30]:
            self.stdout.write(f'ССЫЛКА {row["name"]}: {row["reason"]} {row["url"]}')
        if report['applied'] is None:
            self.stdout.write(self.style.WARNING('Режим проверки: база не изменялась.'))
            return
        applied = report['applied']
        self.stdout.write(self.style.SUCCESS(
            f'Записано карточек: {applied["enriched_sellers"]}. Добавлено: {applied["added"]}'
        ))
        for example in applied['examples']:
            self.stdout.write(f'ПРИМЕР #{example["id"]} {example["name"]}: {", ".join(example["fills"])}')
