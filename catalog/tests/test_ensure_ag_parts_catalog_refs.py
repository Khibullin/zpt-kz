from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from catalog.ag_parts_catalog_refs import REQUIRED_BRAND_MODELS, VERIFY_ONLY_BRAND_MODELS
from catalog.models import Brand, CarModel, Country, Product, SellerProfile

EXCLUDED_MODELS = (
    ('Great Wall', 'Wingle 6'),
    ('Chery', 'Amulet'),
    ('Chery', 'Bonus'),
    ('Chery', 'Very'),
    ('Chery', 'Tiggo 9'),
    ('Geely', 'Cityray'),
    ('Geely', 'Atlas Pro'),
    ('Lifan', 'Myway'),
    ('Belgee', 'X50'),
)

FORBIDDEN_BRANDS = (
    'Lifan',
    'Belgee',
    'Lixiang',
    'Citroen',
    'GWM',
)


class EnsureAgPartsCatalogRefsTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name='Китай')
        self.chery = Brand.objects.create(country=self.country, name='Chery')
        self.tiggo7 = CarModel.objects.create(brand=self.chery, name='Tiggo 7')
        self.tiggo8 = CarModel.objects.create(brand=self.chery, name='Tiggo 8')
        self.great_wall = Brand.objects.create(
            country=self.country,
            name='Great Wall',
        )
        self.wingle7 = CarModel.objects.create(
            brand=self.great_wall,
            name='Wingle 7',
        )
        self.haval = Brand.objects.create(country=self.country, name='Haval')
        self.jolion = CarModel.objects.create(brand=self.haval, name='Jolion')
        self.user = User.objects.create_user(
            username='agparts-refs',
            password='secret12345',
        )
        self.seller = SellerProfile.objects.create(
            user=self.user,
            name='AG Parts',
            phone='77771360740',
            city='Алматы',
        )
        self.product = Product.objects.create(
            title='Pilot cabin filter',
            article='F4J161012030',
            price=1950,
            seller_name=self.seller.name,
            whatsapp_number=self.seller.phone,
            status='active',
            seller_profile=self.seller,
            city='Алматы',
            brand=self.chery,
            car_model=self.tiggo7,
        )

    def _run(self, *args):
        output = StringIO()
        call_command('ensure_ag_parts_catalog_refs', *args, stdout=output)
        return output.getvalue()

    def _snapshot(self):
        return {
            'country_ids': set(Country.objects.values_list('pk', flat=True)),
            'brand_ids': set(Brand.objects.values_list('pk', 'name')),
            'model_ids': set(CarModel.objects.values_list('pk', 'brand_id', 'name')),
            'product_count': Product.objects.count(),
        }

    def test_dry_run_writes_nothing(self):
        before = self._snapshot()
        output = self._run()
        self.assertIn('mode: dry-run', output)
        self.assertIn('EXISTS Brand Chery', output)
        self.assertIn('WOULD_CREATE Model Chery / Tiggo 4', output)
        self.assertIn('WOULD_CREATE Model Chery / Tiggo 5', output)
        self.assertIn('WOULD_CREATE Model Chery / Tiggo 7 Pro', output)
        self.assertIn('WOULD_CREATE Model Chery / Tiggo 8 Pro', output)
        self.assertIn('WOULD_CREATE Brand Exeed', output)
        self.assertIn('WOULD_CREATE Model Exeed / TX', output)
        self.assertIn('WOULD_CREATE Model Exeed / TXL', output)
        self.assertIn('WOULD_CREATE Brand Jetour', output)
        self.assertIn('WOULD_CREATE Model Jetour / Dashing', output)
        self.assertIn('WOULD_CREATE Model Jetour / X70', output)
        self.assertIn('WOULD_CREATE Model Jetour / X90', output)
        self.assertIn('WOULD_CREATE Brand Zeekr', output)
        self.assertIn('WOULD_CREATE Model Zeekr / 001', output)
        self.assertIn('WOULD_CREATE Model Zeekr / 009', output)
        self.assertIn('WOULD_CREATE Model Great Wall / Poer King Kong', output)
        self.assertIn('WOULD_CREATE Model Haval / H2', output)
        self.assertIn('WOULD_CREATE Model Haval / H7', output)
        self.assertIn('WOULD_CREATE Brand MINI', output)
        self.assertIn('WOULD_CREATE Model MINI / Cooper', output)
        self.assertIn('WOULD_CREATE Brand Tank', output)
        self.assertIn('WOULD_CREATE Model Tank / 400', output)
        self.assertNotIn('CREATED ', output)
        self.assertEqual(self._snapshot(), before)

    def test_apply_creates_missing_brand(self):
        self.assertFalse(Brand.objects.filter(name='Exeed').exists())
        output = self._run('--apply')
        self.assertIn('CREATED Brand Exeed', output)
        self.assertEqual(Brand.objects.filter(name='Exeed').count(), 1)
        exeed = Brand.objects.get(name='Exeed')
        self.assertEqual(exeed.country_id, self.country.pk)

    def test_apply_creates_car_model_under_correct_brand(self):
        output = self._run('--apply')
        self.assertIn('CREATED Model Chery / Tiggo 4', output)
        tiggo4 = CarModel.objects.get(name='Tiggo 4')
        self.assertEqual(tiggo4.brand_id, self.chery.pk)
        self.assertEqual(CarModel.objects.filter(name='Tiggo 4').count(), 1)
        self.assertFalse(
            CarModel.objects.filter(brand=self.haval, name='Tiggo 4').exists()
        )

    def test_existing_brand_is_not_duplicated(self):
        output = self._run('--apply')
        self.assertIn('EXISTS Brand Chery', output)
        self.assertEqual(Brand.objects.filter(name='Chery').count(), 1)
        self.assertEqual(Brand.objects.get(name='Chery').pk, self.chery.pk)

    def test_existing_car_model_is_not_duplicated(self):
        existing = CarModel.objects.create(brand=self.chery, name='Tiggo 4')
        output = self._run('--apply')
        self.assertIn('EXISTS Model Chery / Tiggo 4', output)
        self.assertNotIn('CREATED Model Chery / Tiggo 4', output)
        self.assertEqual(CarModel.objects.filter(name='Tiggo 4').count(), 1)
        self.assertEqual(CarModel.objects.get(name='Tiggo 4').pk, existing.pk)

    def test_second_apply_is_idempotent(self):
        first = self._run('--apply')
        self.assertIn('CREATED Brand Exeed', first)
        snapshot = self._snapshot()
        second = self._run('--apply')
        self.assertNotIn('CREATED Brand', second)
        self.assertNotIn('CREATED Model', second)
        self.assertIn('EXISTS Brand Chery', second)
        self.assertIn('EXISTS Brand Exeed', second)
        self.assertIn('EXISTS Model Chery / Tiggo 4', second)
        self.assertIn('EXISTS Model Exeed / TXL', second)
        self.assertIn('EXISTS Model Zeekr / 001', second)
        self.assertEqual(self._snapshot(), snapshot)

    def test_existing_product_is_unchanged(self):
        before = {
            'pk': self.product.pk,
            'title': self.product.title,
            'article': self.product.article,
            'price': self.product.price,
            'status': self.product.status,
            'brand_id': self.product.brand_id,
            'car_model_id': self.product.car_model_id,
            'seller_profile_id': self.product.seller_profile_id,
            'updated_at': self.product.updated_at,
        }
        self._run('--apply')
        self.product.refresh_from_db()
        self.assertEqual(self.product.pk, before['pk'])
        self.assertEqual(self.product.title, before['title'])
        self.assertEqual(self.product.article, before['article'])
        self.assertEqual(self.product.price, before['price'])
        self.assertEqual(self.product.status, before['status'])
        self.assertEqual(self.product.brand_id, before['brand_id'])
        self.assertEqual(self.product.car_model_id, before['car_model_id'])
        self.assertEqual(self.product.seller_profile_id, before['seller_profile_id'])
        self.assertEqual(self.product.updated_at, before['updated_at'])
        self.assertEqual(Product.objects.count(), 1)

    def test_unrelated_brand_and_model_are_unchanged(self):
        haval_name = self.haval.name
        jolion_name = self.jolion.name
        self._run('--apply')
        self.haval.refresh_from_db()
        self.jolion.refresh_from_db()
        self.assertEqual(self.haval.name, haval_name)
        self.assertEqual(self.jolion.name, jolion_name)
        self.assertEqual(self.jolion.brand_id, self.haval.pk)
        self.assertEqual(Brand.objects.filter(name='Haval').count(), 1)
        self.assertTrue(
            CarModel.objects.filter(pk=self.jolion.pk, brand=self.haval, name='Jolion').exists()
        )
        self.assertTrue(
            CarModel.objects.filter(brand=self.haval, name='H2').exists()
        )
        self.assertTrue(
            CarModel.objects.filter(brand=self.haval, name='H7').exists()
        )

    def test_verify_great_wall_wingle_7_read_only(self):
        output = self._run()
        self.assertIn('EXISTS Brand Great Wall', output)
        self.assertIn('EXISTS Model Great Wall / Wingle 7', output)
        self.assertNotIn('WOULD_CREATE Brand Great Wall', output)
        self.assertNotIn('WOULD_CREATE Model Great Wall / Wingle 7', output)
        self.assertIn('WOULD_CREATE Model Great Wall / Poer King Kong', output)

    def test_apply_does_not_create_verify_only_or_excluded_refs(self):
        self.wingle7.delete()
        self.great_wall.delete()
        self._run('--apply')
        self.assertTrue(Brand.objects.filter(name='Great Wall').exists())
        self.assertTrue(
            CarModel.objects.filter(
                brand__name='Great Wall',
                name='Poer King Kong',
            ).exists()
        )
        self.assertFalse(CarModel.objects.filter(name='Wingle 7').exists())
        self.assertFalse(Brand.objects.filter(name='Lixiang').exists())
        self.assertFalse(Brand.objects.filter(name='Citroen').exists())
        self.assertFalse(Brand.objects.filter(name='Lifan').exists())
        self.assertFalse(Brand.objects.filter(name='Belgee').exists())
        self.assertFalse(Brand.objects.filter(name='GWM').exists())
        for brand_name, model_name in EXCLUDED_MODELS:
            self.assertFalse(
                CarModel.objects.filter(brand__name=brand_name, name=model_name).exists()
            )

    def test_required_list_covers_pilot_and_first_batch_confirmed_refs(self):
        pairs = {
            (brand, model)
            for brand, models in REQUIRED_BRAND_MODELS
            for model in models
        }
        self.assertIn(('Chery', 'Tiggo 4'), pairs)
        self.assertIn(('Chery', 'Tiggo 5'), pairs)
        self.assertIn(('Exeed', 'TX'), pairs)
        self.assertIn(('Exeed', 'TXL'), pairs)
        self.assertIn(('Great Wall', 'Poer King Kong'), pairs)
        self.assertIn(('MINI', 'Cooper'), pairs)
        self.assertIn(('Tank', '400'), pairs)
        self.assertIn(('Wey', '05'), pairs)
        self.assertNotIn(('Great Wall', 'Wingle 7'), pairs)
        for brand, model in EXCLUDED_MODELS:
            self.assertNotIn((brand, model), pairs)
        for brand in FORBIDDEN_BRANDS:
            self.assertNotIn(brand, {item[0] for item in REQUIRED_BRAND_MODELS})
            self.assertNotIn(brand, {item[0] for item in VERIFY_ONLY_BRAND_MODELS})

    def test_same_model_name_stays_under_its_own_brand(self):
        self._run('--apply')
        peugeot_207 = CarModel.objects.get(brand__name='Peugeot', name='207')
        self.assertEqual(peugeot_207.brand.name, 'Peugeot')
        self.assertFalse(
            CarModel.objects.filter(brand__name='Ford', name='207').exists()
        )
        jac_s2 = CarModel.objects.get(brand__name='JAC', name='S2')
        self.assertEqual(jac_s2.brand.name, 'JAC')
        self.assertFalse(
            CarModel.objects.filter(brand__name='Haval', name='S2').exists()
        )

    def test_case_and_whitespace_reuse_existing_brand(self):
        europe = Country.objects.create(name='Европа')
        mini = Brand.objects.create(country=europe, name='Mini')
        output = self._run('--apply')
        self.assertIn('EXISTS Brand MINI', output)
        self.assertNotIn('CREATED Brand MINI', output)
        self.assertEqual(Brand.objects.filter(name__iexact='MINI').count(), 1)
        self.assertEqual(Brand.objects.get(name__iexact='mini').pk, mini.pk)
        cooper = CarModel.objects.get(name='Cooper')
        self.assertEqual(cooper.brand_id, mini.pk)

    def test_probable_uncertain_refs_are_not_in_config(self):
        config_text = (
            str(REQUIRED_BRAND_MODELS) + str(VERIFY_ONLY_BRAND_MODELS)
        )
        for token in (
            'PROBABLE',
            'UNCERTAIN',
            'Lifan',
            'Bonus',
            'Very',
            'Tiggo 9',
            'Cityray',
            'Atlas Pro',
            'Wingle 6',
        ):
            self.assertNotIn(token, config_text)
