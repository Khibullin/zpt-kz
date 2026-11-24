from datetime import datetime, timezone as utc

from django.db import migrations, models


# Reviewed against the supplied 2026-11-24 Apify dataset and ACTIVE batch 14.
# These are product-specific outcomes, not guessed prices or search hits.
CONFIRMED = [[7,"343875218",3500,"CAR KIT AUTO","https://kaspi.kz/shop/p/vozdushnyi-fil-tr-m111109111-161941852/"],[11,"643307545",2239,"ИП БЕРЕКЕ","https://kaspi.kz/shop/p/changan-vozdushnyi-fil-tr-s3010140903-119138919/"],[17,"507623213",3500,"StarParts","https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109110xp64xa-142534066/"],[18,"820943016",2499,"ИП ХАЛИБАЕВА","https://kaspi.kz/shop/p/haval-salonnyi-fil-tr-8100103xkv08a-119324299/"],[25,"582544350",4000,"ИП ХАЛИБАЕВА","https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109110xkv08a-123401227/"],[30,"8890649934",6998,"SERAUTO","https://kaspi.kz/shop/p/zeekr-salonnyi-fil-tr-8890649934-131536322/"],[33,"136510928_154746012",3000,"ИП АВТОСИТИ","https://kaspi.kz/shop/p/salonnyi-fil-tr-s111f2801031700-178859130/"],[34,"801748033",2500,"Быстро доставим GARLA","https://kaspi.kz/shop/p/chery-exeed-salonnyi-fil-tr-301000265aa-135424411/"],[47,"346931123",3899,"ИП ПРОКОПЧУК","https://kaspi.kz/shop/p/haval-salonnyi-fil-tr-8104400asz08a-130150267/"],[48,"617689253",8308,"ТОО \"MOTUL SERVISE\"","https://kaspi.kz/shop/p/jac-vozdushnyi-fil-tr-1109140w5000-134238795/"],[55,"237056643",3000,"ТОО \"DCG ERTIS\"","https://kaspi.kz/shop/p/jac-vozdushnyi-fil-tr-1109120u8710-120850259/"],[64,"124260969_572514463",6499,"RIMS","https://kaspi.kz/shop/p/tmg-shrus-naruzhnyi-4890115020071-124260969/"],[79,"756150183",3000,"MIR-AVTO.KZ","https://kaspi.kz/shop/p/vozdushnyi-fil-tr-changan-1109190cr01-181125124/"],[81,"806873204",1600,"Oil City Taldykorgan","https://kaspi.kz/shop/p/chery-exeed-masljanyi-fil-tr-4801012010-135661288/"],[83,"720926049",1604,"Алтын Авто","https://kaspi.kz/shop/p/jac-vozdushnyi-fil-tr-1109130u1510-115912467/"],[86,"F081109111HD",3250,"ИП НАЗАРЕНКО А.В.","https://kaspi.kz/shop/p/ag-parts-vozdushnyi-fil-tr-f081109111hd-145778680/"],[87,"323939926",3250,"ИП НАЗАРЕНКО А.В.","https://kaspi.kz/shop/p/ag-parts-vozdushnyi-fil-tr-f081109111hd-145778680/"]]
REVIEW = [[5,"180634092_999823467",5000,"Свеча зажигания TORCH D20T0120700","https://kaspi.kz/shop/p/svecha-zazhiganija-torch-d20t0120700-167557785/"],[15,"038877771",3878,"MANN-FILTER масляный фильтр HU71151X","https://kaspi.kz/shop/p/mann-filter-masljanyi-fil-tr-hu71151x-165664445/"],[16,"835932711",2600,"Свеча зажигания Chery/Exeed F4J163707010","https://kaspi.kz/shop/p/svecha-zazhiganija-chery-exeed-f4j163707010-144433640/"],[27,"126807700",2300,"JETOUR салонный фильтр 272774M400","https://kaspi.kz/shop/p/jetour-salonnyi-fil-tr-272774m400-115849663/"],[35,"784189420",2995,"Geely масляный фильтр 1056022300","https://kaspi.kz/shop/p/geely-masljanyi-fil-tr-1056022300-128670723/"],[45,"140897673_153940377",4950,"CHAMP воздушный фильтр S1010140400","https://kaspi.kz/shop/p/champ-vozdushnyi-fil-tr-s1010140400-139296491/"],[54,"610354629",2500,"LYKMC салонный фильтр A138107915","https://kaspi.kz/shop/p/lykmc-salonnyi-fil-tr-a138107915-141076209/"],[80,"EM2E8121211E",3500,"AibatAuto салонный фильтр EM2E-8121211E","https://kaspi.kz/shop/p/aibatauto-salonnyi-fil-tr-em2e-8121211e-145496717/"],[88,"482719437",3500,"AibatAuto салонный фильтр EM2E-8121211E","https://kaspi.kz/shop/p/aibatauto-salonnyi-fil-tr-em2e-8121211e-145496717/"]]


def publish_reviewed_apify_dataset(apps, schema_editor):
    Row = apps.get_model("repricer", "KaspiActiveExportRow")
    Offer = apps.get_model("repricer", "KaspiActiveCompetitorOffer")
    Batch = apps.get_model("catalog", "CatalogImportBatch")
    batch = Batch.objects.filter(pk=14, filename="ACTIVE.xlsx").first()
    if batch is None or Row.objects.filter(batch_id=batch.pk).count() != 88:
        return
    captured_at = datetime(2026, 11, 24, 4, 20, 24, tzinfo=utc.utc)
    for number, sku, price, seller, url in CONFIRMED:
        row = Row.objects.filter(batch_id=14, row_number=number, master_sku=sku).first()
        if row is None:
            continue
        # Retain an earlier lower verified price; don't touch own Kaspi prices.
        if row.competitor_min_price is not None and row.competitor_min_price <= price:
            continue
        Offer.objects.get_or_create(
            active_row_id=row.pk, city="Almaty", seller_id="apify-20261124-" + str(number),
            product_url=url,
            defaults={"seller_name": seller, "price": price, "checked_at": captured_at},
        )
        row.competitor_min_price = price
        row.competitor_seller = seller
        row.competitor_seller_id = "apify-20261124-" + str(number)
        row.competitor_city = "Almaty"
        row.competitor_url = url
        row.competitor_checked_at = captured_at
        row.competitor_scan_status = "MATCHED"
        row.competitor_scan_note = ""
        row.save(update_fields=[
            "competitor_min_price", "competitor_seller", "competitor_seller_id",
            "competitor_city", "competitor_url", "competitor_checked_at",
            "competitor_scan_status", "competitor_scan_note",
        ])
    for number, sku, price, title, url in REVIEW:
        row = Row.objects.filter(batch_id=14, row_number=number, master_sku=sku).first()
        if row is None:
            continue
        row.candidate_price = price
        row.candidate_title = title
        row.candidate_url = url
        row.candidate_seller = ""
        if row.competitor_min_price is None:
            row.competitor_scan_note = "Найдена карточка с таким артикулом: требуется проверка бренда"
        row.save(update_fields=[
            "candidate_price", "candidate_title", "candidate_url",
            "candidate_seller", "competitor_scan_note",
        ])


class Migration(migrations.Migration):
    dependencies = [("repricer", "0010_repricing_default_three_cards")]
    operations = [
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="candidate_price",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="candidate_title",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="candidate_url",
            field=models.URLField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="kaspiactiveexportrow", name="candidate_seller",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(publish_reviewed_apify_dataset, migrations.RunPython.noop),
    ]
