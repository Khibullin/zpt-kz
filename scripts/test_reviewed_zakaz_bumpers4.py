import importlib.util, json, sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
from django.conf import settings

TMP=tempfile.TemporaryDirectory()
settings.configure(INSTALLED_APPS=[],DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':':memory:'}},MEDIA_ROOT=TMP.name,MEDIA_URL='/media/',USE_TZ=True,SECRET_KEY='test')
import django
django.setup()
from django.db import models, connection
from django.core.files.base import ContentFile
from PIL import Image

class Supplier(models.Model):
    name=models.CharField(max_length=100)
    class Meta:app_label='test'
class Offer(models.Model):
    supplier=models.ForeignKey(Supplier,on_delete=models.CASCADE)
    brand=models.CharField(max_length=100);article=models.CharField(max_length=120)
    cost=models.IntegerField(default=12345);stock=models.CharField(max_length=20,default='7')
    photo_url=models.URLField(blank=True);characteristics=models.JSONField(default=dict)
    class Meta:app_label='test'
class VehicleFitment(models.Model):
    offer=models.ForeignKey(Offer,on_delete=models.CASCADE)
    make=models.CharField(max_length=100);model=models.CharField(max_length=150)
    configurations=models.JSONField(default=list);source_url=models.URLField(max_length=2048)
    verified=models.BooleanField(default=False)
    class Meta:
        app_label='test'
        constraints=[models.UniqueConstraint(fields=['offer','make','model'],name='unique_fitment_pair')]
class Product(models.Model):
    article=models.CharField(max_length=120);status=models.CharField(max_length=20,default='active')
    main_image=models.ImageField(blank=True);supplier_thumbnail=models.ImageField(blank=True)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:app_label='test'
class RetailListing(models.Model):
    product=models.OneToOneField(Product,on_delete=models.CASCADE)
    offer=models.OneToOneField(Offer,on_delete=models.CASCADE);enabled=models.BooleanField(default=True)
    class Meta:app_label='test'
class SupplierPhotoTask(models.Model):
    product=models.OneToOneField(Product,on_delete=models.CASCADE)
    offer=models.ForeignKey(Offer,on_delete=models.CASCADE)
    source_url=models.URLField(max_length=600);status=models.CharField(max_length=16)
    note=models.TextField(blank=True);evidence=models.JSONField(default=dict)
    updated_at=models.DateTimeField(auto_now=True)
    class Meta:app_label='test'
class Activity(models.Model):
    user_id=models.IntegerField(null=True);action=models.CharField(max_length=100);details=models.JSONField()
    class Meta:app_label='test'

MODELS=[Supplier,Offer,VehicleFitment,Product,RetailListing,SupplierPhotoTask,Activity]
with connection.schema_editor() as ed:
    for m in MODELS:ed.create_model(m)
HERE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('reviewed_top50',HERE/'catalog/reviewed_zakaz_bumpers4.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
manifest=mod.reviewed_rows()
def save_shared(storage,data):
    import hashlib
    name='products/optimized/'+hashlib.sha256(data).hexdigest()+'.webp'
    if not storage.exists(name):name=storage.save(name,ContentFile(data))
    return name

class Tests(unittest.TestCase):
    def setUp(self):
        with connection.cursor() as c:
            for m in reversed(MODELS):c.execute('DELETE FROM '+m._meta.db_table)
        Supplier.objects.create(pk=5,name='ZakazAuto — Костанай');Supplier.objects.create(pk=4,name='Other')
        self.apps=types.SimpleNamespace(get_model=lambda app,name:next(m for m in MODELS if m.__name__==name))
        self.schema=types.SimpleNamespace(connection=connection)
        self.rows=[manifest[0]]
        self.offers=[]
        for row in self.rows:
            o=Offer.objects.create(pk=row['offer_id'],supplier_id=5,brand=row['brand'],article=row['article'],characteristics={'keep':True})
            p=Product.objects.create(article=o.article);RetailListing.objects.create(offer=o,product=p)
            self.offers.append(o)
        self.mockmods=patch.dict(sys.modules,{'catalog.models':types.SimpleNamespace(Product=Product,SupplierPhotoTask=SupplierPhotoTask),'procurement.models':types.SimpleNamespace(Offer=Offer,RetailListing=RetailListing),'catalog.source_photos':types.SimpleNamespace(save_shared_image=save_shared)})
        self.mockmods.start();self.addCleanup(self.mockmods.stop)
        self.reader=patch.object(mod,'reviewed_rows',return_value=manifest);self.reader.start();self.addCleanup(self.reader.stop)
    def test_bounded_write_preserves_trade_data_and_repeats_without_duplicates(self):
        mod.stage_review(self.apps,self.schema)
        self.assertEqual(VehicleFitment.objects.count(),1);self.assertEqual(SupplierPhotoTask.objects.count(),1)
        self.assertEqual(mod.process_reviewed_photos(1),1);self.assertEqual(mod.process_reviewed_photos(10),0)
        self.assertEqual(mod.process_reviewed_photos(10),0)
        for o in self.offers:
            o.refresh_from_db();self.assertEqual((o.cost,o.stock),(12345,'7'));self.assertTrue(o.characteristics['keep'])
            if o.pk==self.offers[0].pk:
                self.assertEqual(o.characteristics['reviewed_applicability']['source_label'],manifest[0]['source_label'])
            p=RetailListing.objects.get(offer=o).product
            self.assertTrue(p.main_image);self.assertTrue(p.supplier_thumbnail)
            self.assertTrue(o.photo_url.endswith(p.main_image.name))
            with Image.open(p.supplier_thumbnail.path) as im:self.assertLessEqual(max(im.size),180)
        names=list(Product.objects.values_list('main_image',flat=True))
        mod.stage_review(self.apps,self.schema)
        self.assertEqual(VehicleFitment.objects.count(),1);self.assertEqual(mod.process_reviewed_photos(10),0)
        self.assertEqual(list(Product.objects.values_list('main_image',flat=True)),names)
    def test_different_supplier_and_changed_article_are_untouched(self):
        Offer.objects.filter(pk=self.offers[0].pk).update(supplier_id=4)
        mod.stage_review(self.apps,self.schema)
        self.assertEqual(VehicleFitment.objects.count(),0);self.assertEqual(SupplierPhotoTask.objects.count(),0)
    def test_existing_manager_fitment_and_photograph_survive(self):
        o=self.offers[0]
        VehicleFitment.objects.create(offer=o,make='MANAGER',model='REVIEW',source_url='https://example.com/',configurations=[{'years':'2001'}],verified=True)
        p=RetailListing.objects.get(offer=o).product;p.main_image='manager-existing.webp';p.save()
        mod.stage_review(self.apps,self.schema);mod.process_reviewed_photos()
        self.assertEqual(list(VehicleFitment.objects.values_list('make','model')),[('MANAGER','REVIEW')])
        p.refresh_from_db();self.assertEqual(p.main_image.name,'manager-existing.webp')
    def test_tampered_image_is_rejected(self):
        row=dict(manifest[0]);row['main_sha256']='0'*64
        with self.assertRaises(ValueError):mod.photo_bytes(row,'main')
    def test_contradictory_years_are_not_imported(self):
        self.assertEqual(manifest[2]['fitments'],[])
        self.assertNotIn('years',manifest[3]['fitments'][0])
        self.assertEqual(manifest[3]['fitments'][0]['model'],'JS6')
    def test_changed_exact_article_cannot_be_staged(self):
        Offer.objects.filter(pk=self.offers[0].pk).update(article='2803101U1557')
        mod.stage_review(self.apps,self.schema)
        self.assertEqual(VehicleFitment.objects.count(),0)
        self.assertEqual(SupplierPhotoTask.objects.count(),0)
    def test_each_reviewed_asset_decodes_to_bounded_real_webp(self):
        self.assertEqual(len(manifest),4);self.assertEqual(sum(bool(r['fitments']) for r in manifest),2)
        for row in manifest:
            self.assertLessEqual(len(row['source_url']),2048)
            if 'main_b64' not in row:continue
            for key,edge in [('main',800),('thumbnail',180)]:
                import io
                with Image.open(io.BytesIO(mod.photo_bytes(row,key))) as im:
                    self.assertEqual(im.format,'WEBP');self.assertLessEqual(max(im.size),edge)

if __name__=='__main__':unittest.main()
