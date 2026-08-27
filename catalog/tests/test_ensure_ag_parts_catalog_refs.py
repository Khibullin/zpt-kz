from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from catalog.ag_parts_catalog_refs import (
    FIRST_BATCH_CONFIRMED_BRAND_MODELS,
    PILOT_BRAND_MODELS,
    REQUIRED_BRAND_MODELS,
    VERIFY_ONLY_BRAND_MODELS,
)
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

# Full unique structured set of first_batch_confirmed.xlsx, including
# Great Wall / Wingle 7 which stays verify-existing only.
FIRST_BATCH_CONFIRMED_PAIRS = frozenset(
    {
        (brand, model)
        for brand, models in FIRST_BATCH_CONFIRMED_BRAND_MODELS
        for model in models
    }
    | {('Great Wall', 'Wingle 7')}
)


def _pairs(groups):
    return {
        (brand, model)
        for brand, models in groups
        for model in models
    }


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
        self.assertIn('WOULD_CREATE Model Chery / Arrizo 8', output)
        self.assertIn('WOULD_CREATE Model Chery / Tiggo 7 Pro', output)
        self.assertIn('WOULD_CREATE Model Chery / Tiggo 7 Pro Max', output)
        self.assertIn('WOULD_CREATE Model Chery / Tiggo 8 Pro', output)
        self.assertIn('EXISTS Model Chery / Tiggo 7', output)
        self.assertIn('EXISTS Model Chery / Tiggo 8', output)
        self.assertIn('WOULD_CREATE Brand Exeed', output)
        self.assertIn('WOULD_CREATE Model Exeed / LX', output)
        self.assertIn('WOULD_CREATE Model Exeed / TXL', output)
        self.assertIn('WOULD_CREATE Model Exeed / VX', output)
        self.assertIn('WOULD_CREATE Brand Jetour', output)
        self.assertIn('WOULD_CREATE Model Jetour / Dashing', output)
        self.assertIn('WOULD_CREATE Model Jetour / X70', output)
        self.assertIn('WOULD_CREATE Model Jetour / X90', output)
        self.assertIn('WOULD_CREATE Brand Zeekr', output)
        self.assertIn('WOULD_CREATE Model Zeekr / 001', output)
        self.assertIn('WOULD_CREATE Model Zeekr / 009', output)
        self.assertIn('WOULD_CREATE Model Zeekr / X', output)
        self.assertIn('WOULD_CREATE Model Great Wall / Poer', output)
        self.assertIn('EXISTS Model Haval / Jolion', output)
        self.assertIn('WOULD_CREATE Model Haval / Dargo', output)
        self.assertIn('WOULD_CREATE Model Haval / F7', output)
        self.assertIn('WOULD_CREATE Model Haval / F7x', output)
        self.assertIn('WOULD_CREATE Model Haval / H9', output)
        self.assertIn('WOULD_CREATE Brand Li Auto', output)
        self.assertIn('WOULD_CREATE Model Li Auto / L6', output)
        self.assertIn('WOULD_CREATE Model Li Auto / L7', output)
        self.assertIn('WOULD_CREATE Model Li Auto / L8', output)
        self.assertIn('WOULD_CREATE Model Li Auto / L9', output)
        self.assertIn('WOULD_CREATE Brand Omoda', output)
        self.assertIn('WOULD_CREATE Model Omoda / C5', output)
        self.assertIn('WOULD_CREATE Brand Jaecoo', output)
        self.assertIn('WOULD_CREATE Model Jaecoo / J7', output)
        self.assertIn('WOULD_CREATE Brand Tank', output)
        self.assertIn('WOULD_CREATE Model Tank / 300', output)
        self.assertIn('WOULD_CREATE Model Tank / 500', output)
        self.assertIn('WOULD_CREATE Model Changan / CS35', output)
        self.assertIn('WOULD_CREATE Model Changan / CS75 Plus', output)
        self.assertIn('WOULD_CREATE Model Changan / UNI-V', output)
        self.assertNotIn('WOULD_CREATE Model Chery / Tiggo 5', output)
        self.assertNotIn('WOULD_CREATE Brand MINI', output)
        self.assertNotIn('WOULD_CREATE Brand Lixiang', output)
        self.assertNotIn('CREATED ', output)
        self.assertEqual(self._snapshot(), before)

    def test_apply_creates_missing_brand(self):
        self.assertFalse(Brand.objects.filter(name='Exeed').exists())
        output = self._run('--apply')
        self.assertIn('CREATED Brand Exeed', output)
        self.assertEqual(Brand.objects.filter(name='Exeed').count(), 1)
        exeed = Brand.objects.get(name='Exeed')
        self.assertEqual(exeed.country_id, self.country.pk)
        li_auto = Brand.objects.get(name='Li Auto')
        self.assertEqual(li_auto.country_id, self.country.pk)
        self.assertEqual(Brand.objects.filter(name='Lixiang').count(), 0)
        self.assertEqual(Brand.objects.get(name='Omoda').country_id, self.country.pk)
        self.assertEqual(Brand.objects.get(name='Jaecoo').country_id, self.country.pk)

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
            CarModel.objects.filter(brand=self.haval, name='Dargo').exists()
        )
        self.assertTrue(
            CarModel.objects.filter(brand=self.haval, name='F7').exists()
        )

    def test_verify_great_wall_wingle_7_read_only(self):
        output = self._run()
        self.assertIn('EXISTS Brand Great Wall', output)
        self.assertIn('EXISTS Model Great Wall / Wingle 7', output)
        self.assertNotIn('WOULD_CREATE Brand Great Wall', output)
        self.assertNotIn('WOULD_CREATE Model Great Wall / Wingle 7', output)
        self.assertIn('WOULD_CREATE Model Great Wall / Poer', output)

    def test_apply_does_not_create_verify_only_or_excluded_refs(self):
        self.wingle7.delete()
        self.great_wall.delete()
        self._run('--apply')
        self.assertTrue(Brand.objects.filter(name='Great Wall').exists())
        self.assertTrue(
            CarModel.objects.filter(
                brand__name='Great Wall',
                name='Poer',
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
        pairs = _pairs(REQUIRED_BRAND_MODELS)
        for brand, model in _pairs(PILOT_BRAND_MODELS):
            self.assertIn((brand, model), pairs)
        for brand, model in _pairs(FIRST_BATCH_CONFIRMED_BRAND_MODELS):
            self.assertIn((brand, model), pairs)
        self.assertNotIn(('Great Wall', 'Wingle 7'), pairs)
        for brand, model in EXCLUDED_MODELS:
            self.assertNotIn((brand, model), pairs)
        for brand in FORBIDDEN_BRANDS:
            self.assertNotIn(brand, {item[0] for item in REQUIRED_BRAND_MODELS})
            self.assertNotIn(brand, {item[0] for item in VERIFY_ONLY_BRAND_MODELS})

    def test_first_batch_confirmed_set_is_fully_covered(self):
        covered = _pairs(REQUIRED_BRAND_MODELS) | _pairs(VERIFY_ONLY_BRAND_MODELS)
        self.assertEqual(FIRST_BATCH_CONFIRMED_PAIRS - covered, set())
        self.assertIn(('Great Wall', 'Wingle 7'), _pairs(VERIFY_ONLY_BRAND_MODELS))
        self.assertIn(('Omoda', 'C5'), covered)
        self.assertIn(('Jaecoo', 'J7'), covered)
        self.assertIn(('Li Auto', 'L6'), covered)
        self.assertIn(('Li Auto', 'L7'), covered)
        self.assertIn(('Li Auto', 'L8'), covered)
        self.assertIn(('Li Auto', 'L9'), covered)
        self.assertNotIn(('Lixiang', 'L6'), covered)
        self.assertNotIn(('Lixiang', 'L7'), covered)
        self.assertNotIn(('BYD', 'Atto 3'), covered)
        self.assertNotIn(('BYD', 'Dolphin'), covered)
        self.assertNotIn(('Ford', 'Transit'), covered)
        self.assertNotIn(('MINI', 'Cooper'), covered)
        self.assertNotIn(('Wey', '05'), covered)
        self.assertNotIn(('Great Wall', 'Poer King Kong'), covered)

    def test_same_model_name_stays_under_its_own_brand(self):
        self._run('--apply')
        jac_j7 = CarModel.objects.get(brand__name='JAC', name='J7')
        jaecoo_j7 = CarModel.objects.get(brand__name='Jaecoo', name='J7')
        self.assertEqual(jac_j7.brand.name, 'JAC')
        self.assertEqual(jaecoo_j7.brand.name, 'Jaecoo')
        self.assertNotEqual(jac_j7.pk, jaecoo_j7.pk)

    def test_case_and_whitespace_reuse_existing_brand(self):
        existing = Brand.objects.create(country=self.country, name='li auto')
        output = self._run('--apply')
        self.assertIn('EXISTS Brand Li Auto', output)
        self.assertNotIn('CREATED Brand Li Auto', output)
        self.assertEqual(Brand.objects.filter(name__iexact='Li Auto').count(), 1)
        self.assertEqual(Brand.objects.get(name__iexact='li auto').pk, existing.pk)
        l7 = CarModel.objects.get(brand=existing, name='L7')
        self.assertEqual(l7.brand_id, existing.pk)
        self.assertFalse(Brand.objects.filter(name='Lixiang').exists())

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
            'Lixiang',
        ):
            self.assertNotIn(token, config_text)
