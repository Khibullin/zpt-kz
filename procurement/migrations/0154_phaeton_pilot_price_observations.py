"""Separate live observations; never rewrite imported or permanent snapshots.

Prices were read from Kaspi seller tables for Almaty, 27 November 2026.
Search coverage and package identity remain unconfirmed, so these do not
admit any Phaeton item to recommendations.
"""
from decimal import Decimal
from django.db import migrations

SOURCE = 'manual_phaeton_pilot_20261127.json'
OBSERVATIONS = [
    ('PARTS-MALL', 'PSC-B003', '153306919', '4776', 'RIMS', 'pmc-parts-mall-rolik-obvodnoi-psc-b003', 'PMC PARTS MALL ролик обводной PSC-B003'),
    ('CTR', 'CQ0735R', '189199102', '11356', 'STETS PARTS', 'ctr-rychag-podveski-cq0735r-552502b000', 'CTR рычаг подвески CQ0735R 552502B000'),
    ('CTR', 'GV1149', '188938866', '3898', 'STETS PARTS', 'ctr-sailentblok-balki-gv1149-4872502290', 'CTR сайлентблок балки GV1149 4872502290'),
    ('CTR', 'CQ0889R', '188941612', '18842', 'STETS PARTS', 'ctr-rychag-podveski-cq0889r-31106878074', 'CTR рычаг подвески CQ0889R 31106878074'),
    ('BLUE PRINT', 'ADN12510', '101500055', '4817', 'RIMS', 'blue-print-salonnyi-fil-tr-adn12510', 'Blue Print салонный фильтр ADN12510'),
    ('FEBEST', '1415-ACT', '128923477', '9054', 'RIMS', 'febest-komplekt-pyl-nika-privodnoi-val-1415-act', 'FEBEST Комплект Пыльника, Приводной Вал 1415-ACT'),
    ('FEBI BILSTEIN', '30844', '124284019', '4999', 'RIMS', 'febi-bilstein-tjaga-stoika-stabilizator-30844', 'FEBI BILSTEIN Тяга / Стойка, Стабилизатор 30844'),
    ('MASUMA', 'MB-9552', '174069835', '9338', 'GS-Market', 'masuma-opora-sharovaja-mb-9552', 'MASUMA опора шаровая MB-9552'),
    ('555', 'SLK-8150R', '179855000', '9669', 'RIMS', '555-stoika-stabilizatora-slk-8150r', '555 стойка стабилизатора SLK-8150R'),
    ('TATSUMI', 'TFB1021', '137780948', '7994', 'RIMS', 'tatsumi-remen-poliklinovyi-tfb1021', 'TATSUMI ремень поликлиновый TFB1021'),
]


def save_observations(apps, schema_editor):
    Audit = apps.get_model('procurement', 'KaspiPriceAudit')
    Offer = apps.get_model('procurement', 'Offer')
    for brand, article, pid, value, seller, slug, title in OBSERVATIONS:
        for offer in Offer.objects.filter(supplier__name='Phaeton DC', brand__iexact=brand, article__iexact=article):
            prior = Audit.objects.filter(offer=offer).exclude(source_filename=SOURCE).order_by('-observed_at', '-pk').first()
            if not prior or prior.cost_snapshot <= 0:
                continue  # Never invent the user's commercial configuration.
            price = Decimal(value)
            profit = price * (1 - prior.commission_snapshot / Decimal(100)) - prior.cost_snapshot - prior.expenses_snapshot
            Audit.objects.get_or_create(offer=offer, source_filename=SOURCE, defaults={
                'min_price': price, 'product_id': pid,
                'card_url': f'https://kaspi.kz/shop/p/{slug}-{pid}/?c=750000000',
                'card_title': title, 'seller_name': seller,
                'cost_snapshot': prior.cost_snapshot,
                'commission_snapshot': prior.commission_snapshot,
                'expenses_snapshot': prior.expenses_snapshot,
                'profit': profit.quantize(Decimal('.01')),
                'roi': (profit * 100 / prior.cost_snapshot).quantize(Decimal('.0001')),
                'card_ids': [pid], 'identity_status': 'needs_review',
                'minimum_review_status': 'needs_review',
                'minimum_review_evidence': {
                    'source': 'kaspi_browser', 'observed_on': '2026-11-27',
                    'scope': 'almaty', 'city_id': '750000000',
                    'search_query': article, 'search_complete': False,
                    'sellers_complete': False, 'previous_audit_id': prior.pk,
                    'previous_price': str(prior.min_price),
                    'reason': 'Найдена более низкая цена; проверка Казахстана и комплектности не завершена',
                },
            })


class Migration(migrations.Migration):
    dependencies = [('procurement', '0153_kaspi_minimum_review')]
    operations = [migrations.RunPython(save_observations, migrations.RunPython.noop)]
