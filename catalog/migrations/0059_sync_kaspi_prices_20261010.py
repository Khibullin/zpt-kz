"""Synchronise unambiguous ZPT retail prices with Kaspi ACTIVE export (2026-10-10).

Do not touch stock_qty: PP2 is an external sellable snapshot, not physical ZPT stock.
Do not create public links, enable Kaspi listings or alter third-party products.
"""
from django.db import migrations

KASPI_PRICES = {
  "124666213": 1800,
  "126807700": 2250,
  "138276423": 1750,
  "148066941": 2200,
  "237056643": 2750,
  "253427189": 1590,
  "292746392": 3150,
  "323939926": 3300,
  "343875218": 1540,
  "346931123": 1870,
  "365030770": 3199,
  "394160893": 1760,
  "423766246": 1839,
  "482719437": 6600,
  "507623213": 2640,
  "516263778": 1670,
  "538852818": 1400,
  "582544350": 2310,
  "610354629": 2200,
  "617689253": 4180,
  "638978124": 870,
  "643307545": 890,
  "711699731": 2640,
  "720926049": 2099,
  "738917542": 1532,
  "756150183": 2750,
  "771931649": 1760,
  "784189420": 1999,
  "801748033": 1529,
  "806873204": 1309,
  "820943016": 1529,
  "826118177": 1590,
  "835932711": 12450,
  "851942073": 2395,
  "864407367": 2045,
  "865592695": 2950,
  "948388624": 2739,
  "976276599": 1760,
  "1056025900": 3890,
  "115801437_271928151": 2960,
  "ZJPCY5000055": 2200,
  "129914457_677517150": 3740,
  "038877771": 2200,
  "107661304_515856655": 1950,
  "026069270": 4400,
  "EM2E8121211E": 4290,
  "137027508_342359488": 1710,
  "140897673_153940377": 2790,
  "029087781": 3817,
  "123401227_789976249": 2310,
  "136510928_154746012": 1624,
  "8890649934": 3240,
  "170037397_480555557": 1870,
  "116207063_792647100": 1150,
  "180634092_999823467": 2200,
  "1017110XEN01": 2066,
  "P8104140": 2499,
  "X01-90000014": 840,
  "119714061_988697284": 1532,
  "119186242_360313404": 2200,
  "119186252_545908053": 928,
  "PBC1109610": 1980,
  "135426201_722693725": 2628,
  "135421060_649647091": 2180,
  "120214535_560663169": 1600,
  "138029392_578287675": 3770,
  "138028524_325397795": 2870,
  "128442767_035937055": 2570,
  "119192295_547130972": 1919,
  "123554287_737968474": 1999,
  "8025530500": 2189,
  "F081109111HD": 3249,
  "131096019_815347049": 1650,
  "119186248_871011130": 810
}
EXCLUDED_SKUS = {"835932711"}  # F4J163707010: price may represent a pack of spark plugs.


def sync_prices(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    Listing = apps.get_model("catalog", "ProductKaspiListing")
    alias = schema_editor.connection.alias

    candidate_prices = {}
    for product_id, sku in Listing.objects.using(alias).filter(
        master_sku__in=list(KASPI_PRICES)
    ).values_list("product_id", "master_sku"):
        if sku not in EXCLUDED_SKUS:
            candidate_prices.setdefault(product_id, set()).add(KASPI_PRICES[sku])

    for product_id, values in candidate_prices.items():
        if len(values) != 1:
            continue  # Multiple Kaspi offers with conflicting prices: manual reconciliation.
        product = Product.objects.using(alias).filter(pk=product_id, status="active").first()
        if product is None or product.price is None:
            continue  # Do not activate hidden/unpriced goods.
        new_price = next(iter(values))
        if product.price != new_price:
            Product.objects.using(alias).filter(pk=product_id, price=product.price).update(
                price=new_price
            )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0058_sync_t151109111_price_20261009")]
    operations = [migrations.RunPython(sync_prices, migrations.RunPython.noop)]
