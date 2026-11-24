from django.db import migrations


# Findings from review of the eight distinct manufacturer/OEM references in
# the user's 24 November 2026 Apify dataset. No price is promoted to MATCHED.
REVIEW_NOTES = [[5,"180634092_999823467","OEM Changan подтверждён; TORCH — другой бренд. Сверить производителя и характеристики нашей свечи.","Zhuparts"],[15,"038877771","HU71151X — номер фильтра MANN-FILTER. Наша карточка AG Parts: идентичность производителя не подтверждена.","RIMS"],[16,"835932711","OEM Chery/Exeed подтверждён. Цена отличается значительно; сверить оригинальность, исполнение и упаковку.","ИП ELEPHANT PREMIUM"],[27,"126807700","OEM встречается для Nissan и Jetour; совместимость не доказывает одинаковый бренд и исполнение.","ИП ХАЛИБАЕВА"],[35,"784189420","OEM Geely подтверждён; сравнение с нашей карточкой AG Parts требует подтверждения производителя.","ИП ПРОКОПЧУК"],[45,"140897673_153940377","CHAMP указан как фильтр по OEM Changan; это аналог другого бренда, не тот же изготовитель.","ИП РУСЛАН"],[54,"610354629","OEM Chery подтверждён; LYKMC — аналог другого бренда, не идентичная торговая позиция.","West Auto"],[80,"EM2E8121211E","OEM BYD соответствует Atto 3/Dolphin; наша карточка указывает Song Plus EV. Проверить модель и бренд.","ИП ХАЛИБАЕВА"],[88,"482719437","OEM BYD соответствует Atto 3/Dolphin; AibatAuto — другой бренд. Сверить фактический фильтр.","ИП ХАЛИБАЕВА"]]


def write_review_findings(apps, schema_editor):
    Row = apps.get_model("repricer", "KaspiActiveExportRow")
    for row_number, sku, message, seller in REVIEW_NOTES:
        row = Row.objects.filter(batch_id=14, row_number=row_number, master_sku=sku,
                                 competitor_min_price__isnull=True,
                                 candidate_price__isnull=False).first()
        if row is None:
            continue
        row.competitor_scan_note = message
        row.candidate_seller = seller
        row.save(update_fields=["competitor_scan_note", "candidate_seller"])


class Migration(migrations.Migration):
    dependencies = [("repricer", "0011_publish_reviewed_apify_20261124")]
    operations = [migrations.RunPython(write_review_findings, migrations.RunPython.noop)]
