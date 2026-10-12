"""Owner-authorized audit: sourced applicability only; preserve all commerce data.

The JSON is a frozen migration input. Compare the exact audited fields before
any mutation, so a newer seller edit stops this migration instead of being lost.
"""
import json
from datetime import date
from pathlib import Path

from django.db import migrations, transaction


DATA_FILE = Path(__file__).resolve().parent.parent / 'data/ag_parts_fitment_audit/confirmed_20261012.json'
NEW_BRAND_COUNTRIES = {'Kaiyi': 'Китай', 'Tenet': 'Россия', 'Belgee': 'Европа', 'Sollers': 'Россия'}


def forwards(apps, schema_editor):
    Product = apps.get_model('catalog', 'Product')
    Fitment = apps.get_model('catalog', 'ProductFitment')
    Brand = apps.get_model('catalog', 'Brand')
    Country = apps.get_model('catalog', 'Country')
    CarModel = apps.get_model('catalog', 'CarModel')
    alias = schema_editor.connection.alias
    data = json.loads(DATA_FILE.read_text(encoding='utf-8'))
    reviewed_on = date.fromisoformat(data['audit_date'])
    with transaction.atomic(using=alias):
        pending = []
        for entry in data['products']:
            product = Product.objects.using(alias).select_for_update().filter(
                pk=entry['id'], article=entry['article'], seller_profile__slug=data['seller_slug'],
            ).first()
            if product is None:
                continue  # A fresh installation has no production seller stock.
            if product.fitment_reviewed_on == reviewed_on:
                continue  # A repeated invocation must not overwrite later edits.
            expected = entry['expected']
            changed = [key for key in ('brand_id', 'car_model_id', 'compatibility', 'engine_compatibility')
                       if getattr(product, key) != expected[key]]
            model_ids = sorted(product.selected_models.using(alias).values_list('pk', flat=True))
            if model_ids != sorted(expected['model_ids'] or []):
                changed.append('selected_models')
            if 'description' in entry and product.description != entry['original_description']:
                changed.append('description')
            if Fitment.objects.using(alias).filter(product_id=product.pk).exists():
                changed.append('fitments')
            if changed:
                raise RuntimeError(f"Fitment audit conflict: {entry['article']}: {', '.join(changed)}")
            pending.append((product, entry))

        model_cache = {}
        for product, entry in pending:
            confirmed_models = {}
            fitments = []
            for values in entry['rows']:
                row = dict(values)
                brand_name, model_name = row.pop('brand'), row.pop('model')
                key = (brand_name, model_name)
                if key not in model_cache:
                    brand = Brand.objects.using(alias).filter(name__iexact=brand_name).first()
                    if brand is None:
                        country_name = NEW_BRAND_COUNTRIES[brand_name]
                        country, _ = Country.objects.using(alias).get_or_create(name=country_name)
                        brand = Brand.objects.using(alias).create(name=brand_name, country_id=country.pk)
                    model = CarModel.objects.using(alias).filter(brand_id=brand.pk, name__iexact=model_name).first()
                    if model is None:
                        model = CarModel.objects.using(alias).create(brand_id=brand.pk, name=model_name)
                    model_cache[key] = model
                model = model_cache[key]
                row['verified_on'] = date.fromisoformat(row['verified_on']) if row['verified_on'] else None
                fitments.append(Fitment(product_id=product.pk, car_model_id=model.pk, **row))
                if row['status'] == 'confirmed':
                    confirmed_models[model.pk] = model
            Fitment.objects.using(alias).bulk_create(fitments)
            product.selected_models.set(confirmed_models)
            # Keep the original primary brand; add the exact confirmed owners.
            brand_ids = {model.brand_id for model in confirmed_models.values()}
            if product.brand_id:
                brand_ids.add(product.brand_id)
            product.selected_brands.set(brand_ids)
            if product.car_model_id not in confirmed_models:
                primary = next((m for m in confirmed_models.values() if m.brand_id == product.brand_id), None)
                product.car_model_id = primary.pk if primary else None
            product.compatibility = entry['compatibility']
            product.engine_compatibility = entry['engine_compatibility']
            product.fitment_reviewed_on = reviewed_on
            fields = ['car_model', 'compatibility', 'engine_compatibility', 'fitment_reviewed_on']
            if 'description' in entry:
                product.description = entry['description']
                fields.append('description')
            product.save(using=alias, update_fields=fields)
        print(f'AG Parts fitment audit: {len(pending)} products applied.')


class Migration(migrations.Migration):
    dependencies = [('catalog', '0072_structured_product_fitment')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
