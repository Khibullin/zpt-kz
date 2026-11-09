from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from catalog.models import Product, SellerProfile
from catalog.commercial import resolve_commercial_price
from .models import Offer, Supplier, RetailListing, VehicleFitment
from .stock_retail import start_publication, advance_publication


class StockRetailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('stock-owner','test@example.test','pass')
        self.seller = SellerProfile.objects.create(user=self.user, slug='ag-parts', name='AG Parts')
        self.omega = Supplier.objects.create(name='Omega')
        self.phaeton = Supplier.objects.create(name='Фаетон')

    def offer(self, **values):
        data = dict(supplier=self.omega, article='A01', brand='555', name='Шаровая 555 A01',
                    cost=Decimal('1000'), stock='10', supplier_checked_on=timezone.localdate())
        data.update(values)
        return Offer.objects.create(**data)

    def finish(self, size=200):
        run = start_publication(self.user)
        for _ in range(20):
            run = advance_publication(run.pk, batch_size=size)
            if run.status == 'done':
                return run
        self.fail('Publication failed to finish')

    def test_all_stock_including_no_kaspi_no_sales_bearings_and_small_profit(self):
        offer = self.offer(name='Подшипник 555 A01', cost=100)
        VehicleFitment.objects.create(offer=offer, make='TOYOTA', model='CAMRY', verified=True)
        run = self.finish()
        product = Product.objects.get()
        self.assertEqual((product.price,product.status,product.stock_qty),(130,'active',10))
        self.assertTrue(resolve_commercial_price(product,1).can_buy)
        self.assertEqual(product.selected_models.get().name, 'CAMRY')
        self.assertEqual(run.processed, 1)
        self.assertFalse(offer.match_verified)

    def test_duplicate_sources_one_card_cheapest_and_distinct_brands(self):
        self.offer()
        self.offer(supplier=self.phaeton, cost=800)
        self.offer(supplier=self.phaeton, brand='OTHER', cost=2000)
        self.finish(size=1)
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(sorted(Product.objects.values_list('price',flat=True)), [1040,2600])
        self.finish(size=1)
        self.assertEqual(Product.objects.count(), 2)

    def test_resume_pack_price_and_source_switch_preserve_url_photo(self):
        a = self.offer(cost=1000, min_order=2)
        b = self.offer(supplier=self.phaeton,cost=1500,min_order=2)
        run = start_publication(self.user)
        run = advance_publication(run.pk,batch_size=1)
        self.assertEqual(run.processed, 1)
        self.assertEqual(start_publication(self.user).pk,run.pk)
        item = RetailListing.objects.get()
        url = item.product.get_absolute_url()
        item.product.main_image='kept.jpg';item.product.save()
        a.stock='0'; a.save()
        run = advance_publication(run.pk,batch_size=1)
        item.refresh_from_db();item.product.refresh_from_db()
        self.assertEqual(item.offer_id,b.pk)
        self.assertEqual(item.product.price,3900)
        self.assertEqual(item.product.main_image.name,'kept.jpg')
        self.assertEqual(item.product.get_absolute_url(),url)

    def test_no_stock_unknown_price_are_not_published_old_source_date_is_preserved(self):
        a = self.offer()
        self.offer(article='ZERO',stock='0')
        self.offer(article='UNKNOWN',stock='?')
        self.offer(article='FREE',cost=0)
        self.offer(article='OLD',supplier_checked_on=timezone.localdate()-timedelta(days=8))
        self.finish()
        self.assertEqual(Product.objects.count(),2)
        old = Product.objects.get(article='OLD')
        self.assertIn((timezone.localdate()-timedelta(days=8)).isoformat(), old.description)
        self.assertTrue(resolve_commercial_price(old,1).can_buy)
        a.stock='0';a.save()
        self.finish()
        self.assertEqual(Product.objects.get(article='A01').status,'hidden')

    def test_existing_unlinked_agparts_products_are_protected(self):
        self.offer()
        Product.objects.create(seller_profile=self.seller,article='A01',title='Existing',price=999)
        self.finish()
        self.assertEqual(Product.objects.count(),1)
        self.assertEqual(Product.objects.get().price,999)

    def test_workspace_no_preferences_or_kaspi_required(self):
        self.offer()
        self.client.force_login(self.user)
        response=self.client.post(reverse('procurement:retail'), {'action':'publish_stock'})
        self.assertEqual(response.status_code,302)
        self.finish()
        response=self.client.get(reverse('procurement:retail'))
        self.assertContains(response,'Обработка завершена')
        self.assertContains(response,'30%')
