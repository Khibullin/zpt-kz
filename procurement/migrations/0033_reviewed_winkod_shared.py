"""Reuse four reviewed exact WINKOD identities, preserving all trade data."""
from django.db import migrations, transaction

TARGETS = {
    55056: ('W333516SA', 25270),
    55057: ('W333517SA', 25218),
    55059: ('W333519SA', 25195),
    55289: ('W335624SA', 25273),
}


def apply_review(apps, schema_editor):
    if schema_editor.connection.alias != 'default':
        return
    from django.contrib.auth import get_user_model
    from procurement.models import Supplier, Offer
    from procurement.shared_fitments import fill_from_shared_base
    supplier = Supplier.objects.filter(pk=3, name='Фаетон').first()
    user = get_user_model().objects.filter(pk=1, username='admin', is_active=True,
        is_staff=True, is_superuser=True).first()
    if not supplier or not user:
        return
    with transaction.atomic():
        selected = []
        for target_id, (article, source_id) in TARGETS.items():
            target = Offer.objects.select_for_update().filter(pk=target_id,
                supplier=supplier, brand='WINKOD', article=article).first()
            source = Offer.objects.filter(pk=source_id, supplier_id=5,
                brand='WINKOD', article=article).first()
            expected_url = 'https://shop.globalparts.kz/product/winkod/' + article.lower()
            if not target or target.fitments.exists() or not source:
                continue
            fits = source.fitments.all()
            if not fits.exists() or fits.exclude(verified=True, source_url=expected_url).exists():
                continue
            selected.append(target_id)
        if selected:
            fill_from_shared_base(supplier, user, offers=Offer.objects.filter(pk__in=selected))


class Migration(migrations.Migration):
    dependencies = [('procurement', '0032_reviewed_remaining2')]
    operations = [migrations.RunPython(apply_review, migrations.RunPython.noop)]
