from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from catalog.ag_parts_fitment_audit import (
    apply_fitment_plans,
    load_batch,
    plan_fitment_batch,
)
from catalog.legacy_product_urls import LEGACY_PRODUCT_SLUG_REDIRECTS
from catalog.models import Brand, CarModel, Country, Product, ProductPriceTier
from catalog.product_quality import detect_internal_research_text


OLD_HU_SLUG = 'audi'
NEW_HU_SLUG = 'peugeot-308-hu71151x'


def _product(**kwargs):
    defaults = {
        'title': 'Тестовый товар',
        'price': 1000,
        'seller_name': 'AG Parts',
        'whatsapp_number': '+77713607040',
        'status': 'active',
        'article': 'FIT-B9',
        'stock_qty': 9,
        'cost_price': 400,
        'city': 'Алматы',
    }
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class AgPartsFitmentBatch09Tests(TestCase):
    def setUp(self):
        country = Country.objects.create(name='Китай B9')
        self.peugeot = Brand.objects.create(country=country, name='Peugeot')
        self.peugeot_308 = CarModel.objects.create(brand=self.peugeot, name='308')

        self.mann = _product(
            article='HU71151X',
            title='Масляный фильтр Peugeot 308 — HU71151X',
            slug=OLD_HU_SLUG,
            brand=self.peugeot,
            car_model=self.peugeot_308,
            price=2200,
            stock_qty=8,
            compatibility='Peugeot 308 1.4 EP3C / 1.6 EP6 / EP6DT / EP6CDT / 2.0 DW10BTED4 (с 06.2007); Citroen C4 Picasso / C5; MINI Cooper II.',
            engine_compatibility='EP3C\nEP6\nEP6DT\nEP6CDT\nDW10BTED4',
            oem_cross_references='HU71151X\nHU 711/51 X\n1109AH\n1109AJ\n1109CK',
        )
        self.mann.selected_models.add(self.peugeot_308)
        ProductPriceTier.objects.create(product=self.mann, min_qty=1, price=2200)

    def test_legacy_map_already_points_audi_to_canonical(self):
        self.assertEqual(LEGACY_PRODUCT_SLUG_REDIRECTS[OLD_HU_SLUG], NEW_HU_SLUG)

    def test_batch_09_has_no_research_notes(self):
        spec = load_batch('09')
        for row in spec['articles']:
            for value in (row.get('fields') or {}).values():
                self.assertFalse(detect_internal_research_text(value))

    def test_apply_batch_09_rewrites_stored_audi_slug(self):
        apply_fitment_plans(plan_fitment_batch(load_batch('09')), apply=True)

        self.mann.refresh_from_db()
        self.assertEqual(self.mann.slug, NEW_HU_SLUG)
        self.assertEqual(self.mann.price, 2200)
        self.assertEqual(self.mann.stock_qty, 8)
        self.assertEqual(self.mann.title, 'Масляный фильтр Peugeot 308 — HU71151X')
        self.assertIn('EP6CDT', self.mann.engine_compatibility)
        self.assertEqual(
            set(self.mann.selected_models.values_list('name', flat=True)),
            {'308'},
        )

        canonical = self.client.get(f'/{NEW_HU_SLUG}/')
        self.assertEqual(canonical.status_code, 200)
        self.assertContains(canonical, 'HU71151X')
        self.assertContains(canonical, 'EP6CDT')

        old = self.client.get(f'/{OLD_HU_SLUG}/', follow=False)
        self.assertEqual(old.status_code, 301)
        self.assertEqual(old['Location'], f'/{NEW_HU_SLUG}/')

        search = self.client.get('/', {'q': 'HU71151X'})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, f'/{NEW_HU_SLUG}/')
        self.assertNotContains(search, f'href="/{OLD_HU_SLUG}/"')

    def test_command_dry_run(self):
        out = StringIO()
        call_command('apply_ag_parts_fitment', '--batch', '09', stdout=out)
        self.mann.refresh_from_db()
        self.assertEqual(self.mann.slug, OLD_HU_SLUG)
        self.assertIn('dry-run', out.getvalue())
        self.assertIn('WOULD_CHANGE', out.getvalue())
