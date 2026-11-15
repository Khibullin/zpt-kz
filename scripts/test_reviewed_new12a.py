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
    kaspi_price=models.IntegerField(default=20560);match_verified=models.BooleanField(default=True);applicability=models.TextField(blank=True)
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
spec=importlib.util.spec_from_file_location('reviewed_top50',HERE/'catalog/reviewed_new12a.py')
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
        Supplier.objects.create(pk=5,name='ZakazAuto — Костанай')
        Supplier.objects.create(pk=4,name='Other')
        self.apps=types.SimpleNamespace(get_model=lambda app,name:next(m for m in MODELS if m.__name__==name))
        self.schema=types.SimpleNamespace(connection=connection)
        self.row=manifest[0]
        self.o=Offer.objects.create(pk=self.row['offer_id'],supplier_id=5,brand=self.row['brand'],article=self.row['article'],cost=self.row['cost'],stock=self.row['stock'],kaspi_price=self.row['kaspi_price'],characteristics={'keep':True})
        self.p=Product.objects.create(pk=self.row['product_id'],article=self.o.article)
        RetailListing.objects.create(offer=self.o,product=self.p)
        VehicleFitment.objects.create(offer=self.o,make='JAC',model='existing',source_url='https://example.com/',configurations=[{'years':'2024'}],verified=True)
        patcher=patch.dict(sys.modules,{'catalog.models':types.SimpleNamespace(Product=Product,SupplierPhotoTask=SupplierPhotoTask),'procurement.models':types.SimpleNamespace(Offer=Offer,RetailListing=RetailListing),'catalog.source_photos':types.SimpleNamespace(save_shared_image=save_shared)})
        patcher.start();self.addCleanup(patcher.stop)
    def test_exact_addressed_items(self):
        self.assertEqual({r['offer_id'] for r in manifest},{26817,25225,25401,25630,26477,26925,26495,26929,26923,26928,26709})
        self.assertEqual(sum(bool(r.get('main_b64')) for r in manifest),7)
    def test_save_and_idempotence_preserve_trade_metadata_and_fitment(self):
        fit=list(VehicleFitment.objects.values())
        mod.stage_review(self.apps,self.schema)
        self.assertEqual(SupplierPhotoTask.objects.count(),1)
        self.assertEqual(mod.process_reviewed_photos(10),1);self.assertEqual(mod.process_reviewed_photos(10),0)
        self.o.refresh_from_db();self.assertEqual((self.o.cost,self.o.stock),(self.row['cost'],self.row['stock']));self.assertEqual(self.o.characteristics,{'keep':True});self.assertEqual(list(VehicleFitment.objects.values()),fit)
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
            if not row.get('main_b64'):continue
            for key,edge in [('main',800),('thumbnail',180)]:
                with Image.open(io.BytesIO(mod.photo_bytes(row,key))) as im:self.assertEqual(im.format,'WEBP');self.assertLessEqual(max(im.size),edge)
        row=dict(self.row);row['main_sha256']='0'*64
        with self.assertRaises(ValueError):mod.photo_bytes(row,'main')

class AdditionalTests(Tests):
    def test_new_fitments_universal_and_repeat(self):
        VehicleFitment.objects.all().delete()
        for row in manifest[1:]:
            Offer.objects.create(pk=row['offer_id'],supplier_id=5,brand=row['brand'],article=row['article'],cost=row['cost'],stock=row['stock'],kaspi_price=row['kaspi_price'],characteristics={'keep':True})
        trade=list(Offer.objects.order_by('pk').values_list('pk','cost','stock','kaspi_price'))
        mod.stage_review(self.apps,self.schema)
        self.assertEqual(VehicleFitment.objects.count(),11)
        universal=Offer.objects.get(pk=25630)
        self.assertEqual(universal.characteristics['reviewed_applicability']['status'],'verified_universal')
        self.assertFalse(VehicleFitment.objects.filter(offer_id=25630).exists())
        c=Offer.objects.get(pk=25401)
        self.assertTrue(c.characteristics['keep'])
        configurations=list(VehicleFitment.objects.filter(offer_id=25401).values_list('configurations',flat=True))
        self.assertIn('chery-tiggo-4.html',configurations[1][0]['source_url'])
        mod.stage_review(self.apps,self.schema)
        self.assertEqual(VehicleFitment.objects.count(),11)
        self.assertEqual(list(Offer.objects.order_by('pk').values_list('pk','cost','stock','kaspi_price')),trade)
    def test_universal_template(self):
        from django.template import Engine, Context
        template=Engine(dirs=[str(HERE/'procurement/templates')]).get_template('procurement/fitments.html')
        universal=types.SimpleNamespace(applicability='check',characteristics={'reviewed_applicability':{'status':'verified_universal','universal':True,'note':'7-контактная розетка'}},pk=1)
        html=template.render(Context({'fitments':[],'offer':universal}))
        self.assertIn('Универсальный товар',html)
        self.assertNotIn('требует проверки',html)
        universal.characteristics={}
        self.assertIn('требует проверки',template.render(Context({'fitments':[],'offer':universal})))

if __name__=='__main__':unittest.main()
