from django.db import migrations

SELLERS = [[7,"5050005"],[11,"30150735"],[17,"30430559"],[18,"30308762"],[25,"30308762"],[30,"30296490"],[33,"30342257"],[34,"18217623"],[47,"30276559"],[48,"30450675"],[55,"30277714"],[64,"15544171"],[79,"30446021"],[81,"17324171"],[83,"14548019"],[86,"30119745"],[87,"30119745"]]

def repair_real_merchant_ids(apps, schema_editor):
    Row = apps.get_model("repricer", "KaspiActiveExportRow")
    Offer = apps.get_model("repricer", "KaspiActiveCompetitorOffer")
    for number, merchant_id in SELLERS:
        row = Row.objects.filter(batch_id=14, row_number=number).first()
        if row is None:
            continue
        old_id = "apify-20261124-" + str(number)
        Offer.objects.filter(active_row_id=row.pk, seller_id=old_id).update(seller_id=merchant_id)
        if row.competitor_seller_id == old_id:
            row.competitor_seller_id = merchant_id
            row.save(update_fields=["competitor_seller_id"])

class Migration(migrations.Migration):
    dependencies = [("repricer", "0011_publish_reviewed_apify_20261124")]
    operations = [migrations.RunPython(repair_real_merchant_ids, migrations.RunPython.noop)]
