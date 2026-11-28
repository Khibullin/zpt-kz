"""Trial-import a complete retained file, rolling back every database change."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from procurement.apify_batch_intake import BATCH_SIZE, source_rows
from procurement.apify_price_recheck import import_apify_recheck_results
from procurement.models import Activity, KaspiPriceAudit


class Command(BaseCommand):
    help = 'Проверить весь сохранённый JSON Apify без сохранения пересчёта и без Apify.'

    def add_arguments(self, parser):
        parser.add_argument('session_id', type=int)
        parser.add_argument('--supplier', default='Phaeton DC')

    def handle(self, *args, **options):
        try:
            session = Activity.objects.select_related('user').get(
                pk=options['session_id'], action='apify_upload_session')
        except Activity.DoesNotExist as exc:
            raise CommandError('Сессия загрузки не найдена.') from exc
        details = session.details or {}
        if not session.user or details.get('next_upload_batch', details['total_batches']) != details['total_batches']:
            raise CommandError('Нет пользователя расчёта либо загрузка не завершена.')
        batches = Activity.objects.filter(user=session.user, action='apify_upload_source_batch',
            details__session_id=session.pk).order_by('pk')
        if batches.count() != details['total_batches']:
            raise CommandError('Исходные строки не полностью сохранены. Требуется исходный JSON.')
        source = details['filename']
        supplier = options['supplier']
        def snapshot():
            return {a.offer_id: a for a in KaspiPriceAudit.objects.filter(
                source_filename=source, offer__supplier__name=supplier).select_related('offer')}
        before = snapshot()
        totals = {}
        processed = 0
        with transaction.atomic():
            for index, batch in enumerate(batches.iterator(chunk_size=1)):
                rows = source_rows(batch)
                expected = min(BATCH_SIZE, details['total'] - index * BATCH_SIZE)
                if batch.details['batch'] != index or len(rows) != expected:
                    raise CommandError('Нарушена полнота или последовательность исходных строк.')
                result = import_apify_recheck_results(rows, source, session.user)
                for key, value in result.items():
                    totals[key] = totals.get(key, 0) + value
                processed += len(rows)
                self.stderr.write(f'Проверено {processed} / {details["total"]}')
            if processed != details['total']:
                raise CommandError('Не все исходные строки проверены.')
            after = snapshot()
            lowered = [a for key, a in after.items() if key in before and a.min_price < before[key].min_price]
            report = {
                'session_id': session.pk, 'source': source, 'supplier': supplier,
                'source_rows': processed, 'source_sha256': details.get('digest', ''), 'total_batches': details['total_batches'],
                'audits_before': len(before), 'audits_after': len(after),
                'lowered': len(lowered), 'new_matches': len(set(after) - set(before)),
                'eligible_before': sum(a.cost_snapshot >= 2000 and a.roi >= 50 for a in before.values()),
                'eligible_after': sum(a.cost_snapshot >= 2000 and a.roi >= 50 for a in after.values()),
                'lower_prices': [dict(offer_id=a.offer_id, brand=a.offer.brand, article=a.offer.article,
                    before=str(before[a.offer_id].min_price), after=str(a.min_price),
                    product_id=a.product_id, city=a.price_evidence.get('city', '')) for a in lowered],
                'stats': totals, 'saved': False,
            }
            transaction.set_rollback(True)
        self.stdout.write(json.dumps(report, ensure_ascii=False))
