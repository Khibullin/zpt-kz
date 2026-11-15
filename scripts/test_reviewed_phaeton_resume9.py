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
spec=importlib.util.spec_from_file_location('reviewed_top50',HERE/'catalog/reviewed_phaeton_resume9.py')
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
            for m in reversed(MODELS): c.execute('DELETE FROM '+m._meta.db_table)
        Supplier.objects.create(pk=3,name='Фаетон')
        Supplier.objects.create(pk=4,name='Other')
        self.apps=types.SimpleNamespace(get_model=lambda app,name:next(m for m in MODELS if m.__name__==name))
        self.schema=types.SimpleNamespace(connection=connection)
        self.row=manifest[0]
        self.o=Offer.objects.create(pk=self.row['offer_id'],supplier_id=3,brand=self.row['brand'],article=self.row['article'],characteristics={'keep':True})
        self.p=Product.objects.create(pk=self.row['product_id'],article=self.o.article)
        RetailListing.objects.create(offer=self.o,product=self.p)
        VehicleFitment.objects.create(offer=self.o,make='JAC',model='existing',source_url='https://example.com/',configurations=[{'years':'2024'}],verified=True)
        patcher=patch.dict(sys.modules,{'catalog.models':types.SimpleNamespace(Product=Product,SupplierPhotoTask=SupplierPhotoTask),'procurement.models':types.SimpleNamespace(Offer=Offer,RetailListing=RetailListing),'catalog.source_photos':types.SimpleNamespace(save_shared_image=save_shared)})
        patcher.start();self.addCleanup(patcher.stop)
    def test_exact_one_addressed_item(self):
        self.assertEqual(len(manifest),10);self.assertEqual({r['offer_id'] for r in manifest},{35183,35168,35143,35182,35176,22494,22515,35142,22432,22123})
        self.assertEqual(self.row['article'],'353-87K');self.assertEqual(self.row['brand'],'MOTORAD');self.assertTrue(all(not r['fitments'] for r in manifest))
    def test_save_and_idempotence_preserve_trade_metadata_and_fitment(self):
        fit=list(VehicleFitment.objects.values())
        mod.stage_review(self.apps,self.schema)
        self.assertEqual(SupplierPhotoTask.objects.count(),1)
        self.assertEqual(mod.process_reviewed_photos(10),1);self.assertEqual(mod.process_reviewed_photos(10),0)
        self.o.refresh_from_db();self.assertEqual((self.o.cost,self.o.stock),(12345,'7'));self.assertEqual(self.o.characteristics,{'keep':True});self.assertEqual(list(VehicleFitment.objects.values()),fit)
        self.p.refresh_from_db();names=(self.p.main_image.name,self.p.supplier_thumbnail.name)
        self.assertTrue(all(names));mod.stage_review(self.apps,self.schema);self.assertEqual(mod.process_reviewed_photos(10),0)
        self.p.refresh_from_db();self.assertEqual((self.p.main_image.name,self.p.supplier_thumbnail.name),names)
    def test_no_stock_no_staging(self):
        Offer.objects.update(stock='0');mod.stage_review(self.apps,self.schema);self.assertEqual(SupplierPhotoTask.objects.count(),0)
    def test_changed_supplier_no_staging(self):
        Offer.objects.update(supplier_id=4);mod.stage_review(self.apps,self.schema);self.assertEqual(SupplierPhotoTask.objects.count(),0)
    def test_changed_article_no_staging(self):
        Offer.objects.update(article='1308010P3040');mod.stage_review(self.apps,self.schema);self.assertEqual(SupplierPhotoTask.objects.count(),0)
    def test_existing_photo_preserved(self):
        self.p.main_image='manager.webp';self.p.save();mod.stage_review(self.apps,self.schema);self.assertEqual(SupplierPhotoTask.objects.count(),0);self.p.refresh_from_db();self.assertEqual(self.p.main_image.name,'manager.webp')
    def test_disabled_listing_no_staging(self):
        RetailListing.objects.update(enabled=False);mod.stage_review(self.apps,self.schema);self.assertEqual(SupplierPhotoTask.objects.count(),0)
    def test_asset_checksums_and_bounds(self):
        import io
        for row in manifest:
            for key,edge in [('main',800),('thumbnail',180)]:
                with Image.open(io.BytesIO(mod.photo_bytes(row,key))) as im:self.assertEqual(im.format,'WEBP');self.assertLessEqual(max(im.size),edge)
        row=dict(self.row);row['main_sha256']='0'*64
        with self.assertRaises(ValueError):mod.photo_bytes(row,'main')

if __name__=='__main__':unittest.main()
