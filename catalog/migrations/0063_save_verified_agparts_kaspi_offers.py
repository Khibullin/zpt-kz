"""Save the 46 Kaspi cards checked against active AG Parts offers.

The public AG Parts seller catalog showed each exact Kaspi merchant SKU on the
matching card. Keep the seller context in each public URL so buyers reach the
AG Parts offer.
"""
from django.db import migrations
from django.utils import timezone


CHECKED_LISTINGS = (
    ("038877771", "HU71151X", "https://kaspi.kz/shop/p/ag-parts-masljanyi-fil-tr-hu71151x-151589696/?c=750000000&m=30363568&ms=true"),
    ("1017110XEN01", "1017110XEN01", "https://kaspi.kz/shop/p/masljanyi-fil-tr-1017110xen01-141407587/?c=750000000&m=30363568&ms=true"),
    ("107661304_515856655", "F4J161012030", "https://kaspi.kz/shop/p/masljanyi-f4j161012030-107661304/?c=750000000&m=30363568&ms=true"),
    ("115801437_271928151", "X0390000206", "https://kaspi.kz/shop/p/lixiang-salonnyi-fil-tr-x0390000206-115801437/?c=750000000&m=30363568&ms=true"),
    ("116207063_792647100", "1109101XGW01A", "https://kaspi.kz/shop/p/pac-vozdushnyi-fil-tr-1109101xgw01a-116207063/?c=750000000&m=30363568&ms=true"),
    ("119186242_360313404", "151000187AA", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-151000187aa-119186242/?c=750000000&m=30363568&ms=true"),
    ("119186248_871011130", "151000079AA", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-151000079aa-119186248/?c=750000000&m=30363568&ms=true"),
    ("119186252_545908053", "151000025AA", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-151000025aa-119186252/?c=750000000&m=30363568&ms=true"),
    ("119192295_547130972", "8114010U8520", "https://kaspi.kz/shop/p/salonnyi-fil-tr-8114010u8520-119192295/?c=750000000&m=30363568&ms=true"),
    ("119714061_988697284", "301001199AA", "https://kaspi.kz/shop/p/salonnyi-fil-tr-301001199aa-119714061/?c=750000000&m=30363568&ms=true"),
    ("120214535_560663169", "T151109111", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-t151109111-120214535/?c=750000000&m=30363568&ms=true"),
    ("124666213", "8104102P3010", "https://kaspi.kz/shop/p/salonnyi-fil-tr-8104102p3010-138022439/?c=750000000&m=30363568&ms=true"),
    ("129914457_677517150", "8100422XNZ01A", "https://kaspi.kz/shop/p/haval-gwm-salonnyi-fil-tr-8100422xnz01a-129914457/?c=750000000&m=30363568&ms=true"),
    ("131096019_815347049", "T218107011", "https://kaspi.kz/shop/p/ag-parts-salonnyi-fil-tr-t218107011-121463463/?c=750000000&m=30363568&ms=true"),
    ("135421060_649647091", "1109130U2400", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109130u2400-135421060/?c=750000000&m=30363568&ms=true"),
    ("135426201_722693725", "2032047000", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-2032047000-135426201/?c=750000000&m=30363568&ms=true"),
    ("136510928_154746012", "S111F2801031700", "https://kaspi.kz/shop/p/sangwell-salonnyi-fil-tr-s111f2801031700-136510928/?c=750000000&m=30363568&ms=true"),
    ("137027508_342359488", "J691109111", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-j691109111-137027508/?c=750000000&m=30363568&ms=true"),
    ("148066941", "M118107915", "https://kaspi.kz/shop/p/ag-parts-salonnyi-fil-tr-m118107915-151589796/?c=750000000&m=30363568&ms=true"),
    ("180634092_999823467", "D20T0120700", "https://kaspi.kz/shop/p/svecha-zazhiganija-ag-parts-d20t0120700-153482464/?c=750000000&m=30363568&ms=true"),
    ("237056643", "1109120U8710", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109120u8710-138015548/?c=750000000&m=30363568&ms=true"),
    ("343875218", "M111109111", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-m111109111-138011904/?c=750000000&m=30363568&ms=true"),
    ("346931123", "8104400ASZ08A", "https://kaspi.kz/shop/p/salonnyi-fil-tr-8104400asz08a-138019707/?c=750000000&m=30363568&ms=true"),
    ("394160893", "8104400XP24BA", "https://kaspi.kz/shop/p/salonnyi-fil-tr-8104400xp24ba-138028027/?c=750000000&m=30363568&ms=true"),
    ("482719437", "EM2E-8121211E", "https://kaspi.kz/shop/p/salonnyi-fil-tr-em2e-8121211e-138031236/?c=750000000&m=30363568&ms=true"),
    ("507623213", "1109110XP64XA", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109110xp64xa-138014702/?c=750000000&m=30363568&ms=true"),
    ("516263778", "1017110XED95", "https://kaspi.kz/shop/p/masljanyi-fil-tr-1017110xed95-138030411/?c=750000000&m=30363568&ms=true"),
    ("538852818", "1109104XGW02A", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109104xgw02a-138012174/?c=750000000&m=30363568&ms=true"),
    ("610354629", "A138107915", "https://kaspi.kz/shop/p/ag-parts-salonnyi-fil-tr-a138107915-151589558/?c=750000000&m=30363568&ms=true"),
    ("617689253", "1109140W5000", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109140w5000-138015392/?c=750000000&m=30363568&ms=true"),
    ("638978124", "1064000180", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1064000180-ag-302-eco-sa-8147-a1003-a-1180-sb-3250-71-01286-sx-150711435/?c=750000000&m=30363568&ms=true"),
    ("643307545", "S3010140903", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-s3010140903-138013617/?c=750000000&m=30363568&ms=true"),
    ("711699731", "1109110XP6EXACHS", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109110xp6exachs-138113537/?c=750000000&m=30363568&ms=true"),
    ("720926049", "1109130U1510", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-1109130u1510-150712095/?c=750000000&m=30363568&ms=true"),
    ("738917542", "C281F2801032601", "https://kaspi.kz/shop/p/salonnyi-fil-tr-c281f2801032601-138020446/?c=750000000&m=30363568&ms=true"),
    ("756150183", "1109190CR01", "https://kaspi.kz/shop/p/ag-parts-vozdushnyi-fil-tr-1109190cr01-151649550/?c=750000000&m=30363568&ms=true"),
    ("771931649", "FAE1109160", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-fae1109160-138016320/?c=750000000&m=30363568&ms=true"),
    ("820943016", "8100103XKV08A", "https://kaspi.kz/shop/p/salonnyi-fil-tr-8100103xkv08a-138020001/?c=750000000&m=30363568&ms=true"),
    ("835932711", "F4J163707010", "https://kaspi.kz/shop/p/svecha-zazhiganija-ag-parts-f4j163707010-133904131/?c=750000000&m=30363568&ms=true"),
    ("851942073", "6600131687", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-6600131687-138014607/?c=750000000&m=30363568&ms=true"),
    ("865592695", "151000151AA", "https://kaspi.kz/shop/p/vozdushnyi-fil-tr-151000151aa-138014194/?c=750000000&m=30363568&ms=true"),
    ("8890649934", "8890649934", "https://kaspi.kz/shop/p/salonnyi-fil-tr-8890649934-141509283/?c=750000000&m=30363568&ms=true"),
    ("948388624", "13033898-00", "https://kaspi.kz/shop/p/salonnyi-fil-tr-13033898-00-138031071/?c=750000000&m=30363568&ms=true"),
    ("976276599", "RF059ZKR", "https://kaspi.kz/shop/p/salonnyi-fil-tr-rf059zkr-138011295/?c=750000000&m=30363568&ms=true"),
    ("EM2E8121211E", "EM2E-8121211E", "https://kaspi.kz/shop/p/ag-parts-salonnyi-fil-tr-em2e8121211e-155609045/?c=750000000&m=30363568&ms=true"),
    ("X01-90000014", "X01-90000014", "https://kaspi.kz/shop/p/lixiang-vozdushnyi-fil-tr-x01-90000014-119314397/?c=750000000&m=30363568&ms=true"),
)


def save_verified_public_offers(apps, schema_editor):
    Listing = apps.get_model("catalog", "ProductKaspiListing")
    db = schema_editor.connection.alias

    for master_sku, article, expected_url in CHECKED_LISTINGS:
        candidates = list(
            Listing.objects.using(db).filter(
                master_sku=master_sku,
                merchant_sku=article,
                product__article=article,
                product__status="active",
                product__seller_profile__slug="ag-parts",
                is_active=True,
            )
        )
        if not candidates:
            # Fresh/test databases may not contain imported production listings.
            continue
        if len(candidates) != 1:
            raise RuntimeError(
                f"Cannot uniquely identify AG Parts Kaspi listing for {article}"
            )

        listing = candidates[0]
        if listing.public_url not in ("", expected_url):
            raise RuntimeError(
                f"Existing Kaspi URL differs from the checked seller card for {article}"
            )

        Listing.objects.using(db).filter(
            pk=listing.pk,
            public_url__in=["", expected_url],
        ).update(
            public_url=expected_url,
            public_url_source="public_seller_checked",
            public_url_verified_at=timezone.now(),
        )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0062_record_public_agparts_kaspi_checks")]

    operations = [
        migrations.RunPython(save_verified_public_offers, migrations.RunPython.noop)
    ]
}
