import io
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from catalog.models import Product,SellerProfile
from catalog.commercial import resolve_commercial_price
from .models import Offer,Supplier,RetailListing,KaspiSellerSnapshot
from .retail import price_for,prepare_updates,apply_updates,refresh_listing,publish_one,validate_image_url


class RetailTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_superuser('owner','owner@example.test','pass')
        self.seller=SellerProfile.objects.create(user=self.user,slug='ag-parts',name='AG Parts',phone='+77713607040')
        self.supplier=Supplier.objects.create(name='Omega')
        self.offer=Offer.objects.create(supplier=self.supplier,brand='WXQP',article='00042',name='Шланг WXQP',cost=Decimal('2000'),stock='>20 шт.',min_order=1,checked_on=timezone.localdate(),supplier_url='https://omega-auto-parts.kz/tormoznaya-sistema/shlang')
        self.product=Product.objects.create(title='Шланг WXQP',seller_profile=self.seller,seller_name='AG Parts',whatsapp_number=self.seller.phone,supplier='omega',price=2600,stock_qty=20,article='00042')
        self.listing=RetailListing.objects.create(offer=self.offer,product=self.product,source_content={'title':'Шланг WXQP','description':'Передний левый','compatibility':'Honda CR-V','oem':'01465-S10-010'})

    def csv(self,text):return SimpleUploadedFile('update.csv',text.encode())

    def test_markup_and_confirmed_fresh_cap(self):
        self.assertEqual(price_for(self.offer,1),(2600,''))
        self.offer.match_verified=True;self.offer.kaspi_price=2500;self.offer.save()
        KaspiSellerSnapshot.objects.create(offer=self.offer,source_url='https://kaspi.kz/shop/p/1/',checked_on=timezone.localdate(),complete=True,sellers=[{'price':2400}])
        self.offer.refresh_from_db()
        self.assertEqual(price_for(self.offer,1),(2400,''))
        self.assertEqual(price_for(self.offer,4),(9600,''))

    def test_unverified_candidate_does_not_set_cap(self):
        self.offer.kaspi_price=100;self.offer.save()
        self.assertEqual(price_for(self.offer,1),(2600,''))

    def test_package_and_no_automatic_loss(self):
        self.offer.min_order=4;self.offer.save();self.product.refresh_from_db()
        self.assertEqual(self.product.price,10400);self.assertEqual(self.product.stock_qty,5)
        self.offer.cost=680;self.offer.min_order=1;self.offer.save();self.product.refresh_from_db()
        self.assertTrue(self.product.price_on_request)
        self.assertFalse(resolve_commercial_price(self.product,1).can_buy)

    def test_excel_preview_no_write_and_atomic_apply(self):
        rows=prepare_updates(self.csv('brand,article,cost,stock,active\nWXQP,00042,2500,0,0\n'),timezone.localdate())
        self.offer.refresh_from_db();self.assertEqual(self.offer.cost,2000)
        apply_updates(rows,self.user);self.product.refresh_from_db()
        self.assertEqual(self.product.price,3250);self.assertEqual(self.product.stock_qty,0);self.assertEqual(self.product.status,'hidden')

    def test_preview_rejects_concurrent_change(self):
        rows=prepare_updates(self.csv('brand,article,cost\nWXQP,00042,2500\n'),timezone.localdate())
        self.offer.cost=3000;self.offer.save()
        with self.assertRaisesMessage(ValueError,'изменились'):apply_updates(rows,self.user)
        self.offer.refresh_from_db();self.assertEqual(self.offer.cost,3000)

    def test_old_file_and_duplicates_rejected(self):
        with self.assertRaises(ValueError):prepare_updates(self.csv('brand,article,cost\nWXQP,00042,1000\n'),timezone.localdate()-timedelta(days=1))
        with self.assertRaises(ValueError):prepare_updates(self.csv('brand,article,stock\nWXQP,00042,1\nWXQP,00042,2\n'),timezone.localdate())

    def test_cart_stale_and_stock(self):
        self.assertTrue(resolve_commercial_price(self.product,1).can_buy)
        self.assertFalse(resolve_commercial_price(self.product,21).can_buy)
        self.offer.checked_on=timezone.localdate()-timedelta(days=8);self.offer.save();self.product.refresh_from_db()
        self.assertFalse(resolve_commercial_price(self.product,1).can_buy)

    def test_private_access_and_public_cart_without_cost(self):
        response=self.client.get(reverse('procurement:retail'));self.assertEqual(response.status_code,302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('procurement:retail')).status_code,200)
        self.assertEqual(self.client.get(reverse('procurement:retail_excel')).status_code,200)
        self.client.logout()
        response=self.client.get(self.product.get_absolute_url())
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-cart-add');self.assertNotContains(response,'Переход на Kaspi для этого товара')
        self.assertNotContains(response,'Себестоимость')

    def test_image_ssrf_and_redirect_host_rejected(self):
        for url in ['http://127.0.0.1/x.jpg','https://omega-auto-parts.kz.evil.test/content/files/images/products_images/a.jpg','https://omega-auto-parts.kz/content/files/images/products_images/../x.jpg']:
            with self.assertRaises(ValueError):validate_image_url(url)

    def test_unknown_products_do_not_expand_pilot(self):
        with self.assertRaises(ValueError):prepare_updates(self.csv('brand,article,cost\nWXQP,other,2000\n'),timezone.localdate())
        self.assertEqual(RetailListing.objects.count(),1)

    @override_settings(MEDIA_ROOT='/tmp/omega-retail-test-media')
    def test_publish_idempotent_photos_and_no_private_description(self):
        offer=Offer.objects.create(supplier=self.supplier,brand='TAIHO',article='T-1',name='Вкладыши',cost=2500,stock='3 шт.',checked_on=timezone.localdate(),supplier_url='https://omega-auto-parts.kz/dvigatel/t-1')
        row={'offer_id':offer.pk,'brand':offer.brand,'article':offer.article,'title':'Вкладыши TAIHO T-1','description':'Размер 0.25','compatibility':'Mazda 626','oem':'FE1H','source_url':offer.supplier_url,'checked_on':timezone.localdate().isoformat(),'cost':'2500','stock':'3 шт.','min_order':1,'images':['https://omega-auto-parts.kz/content/files/images/products_images/T-1.jpg']}
        from PIL import Image
        output=io.BytesIO();Image.new('RGB',(10,10),'white').save(output,'JPEG')
        with patch('procurement.retail.download_image',return_value=output.getvalue()) as fetch:
            listing=publish_one(row,self.user);path=listing.product.main_image.name
            listing=publish_one(row,self.user)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(listing.product.main_image.name,path)
        self.assertEqual(Product.objects.filter(supplier='omega',article='T-1').count(),1)
        self.assertEqual(listing.product.price,3250)
        self.assertNotIn('2500',listing.product.description)
