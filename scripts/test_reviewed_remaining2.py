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
spec=importlib.util.spec_from_file_location('reviewed_top50',HERE/'catalog/reviewed_remaining2.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
manifest=mod.reviewed_rows()
def save_shared(storage,data):
    import hashlib
    name='products/optimized/'+hashlib.sha256(data).hexdigest()+'.webp'
    if not storage.exists(name):name=storage.save(name,ContentFile(data))
    return name

class Tests(unittest.TestCase):
    def test_bounded_import_and_photo_processing_are_idempotent(self):
        Supplier.objects.create(pk=3,name='Фаетон')
        for row in manifest:
            o=Offer.objects.create(pk=row['offer_id'],supplier_id=3,brand=row['brand'],article=row['article'],characteristics={'keep':True})
            p=Product.objects.create(pk=row['product_id'],article=row['article'])
            RetailListing.objects.create(offer=o,product=p)
            SupplierPhotoTask.objects.create(pk=row['task_id'],offer=o,product=p,status='needs_source',evidence={})
        apps=types.SimpleNamespace(get_model=lambda app,name:next(m for m in MODELS if m.__name__==name))
        with patch.dict(sys.modules,{'catalog.models':types.SimpleNamespace(Product=Product,SupplierPhotoTask=SupplierPhotoTask),'procurement.models':types.SimpleNamespace(Offer=Offer,RetailListing=RetailListing),'catalog.source_photos':types.SimpleNamespace(save_shared_image=save_shared)}):
            mod.stage_review(apps,types.SimpleNamespace(connection=connection))
            self.assertEqual(VehicleFitment.objects.count(),4)
            self.assertEqual(sum(len(f.configurations) for f in VehicleFitment.objects.all()),8)
            self.assertEqual(mod.process_reviewed_photos(),2)
            mod.stage_review(apps,types.SimpleNamespace(connection=connection))
            self.assertEqual(mod.process_reviewed_photos(),0)
            self.assertEqual(VehicleFitment.objects.count(),4)
            for o in Offer.objects.all():
                self.assertEqual((o.cost,o.stock),(12345,'7'));self.assertTrue(o.characteristics['keep'])
                self.assertEqual(o.characteristics['reviewed_applicability']['source_kind'],'retailer_listing')
            for p in Product.objects.all():self.assertTrue(p.main_image);self.assertTrue(p.supplier_thumbnail)
            toyota=VehicleFitment.objects.get(make='TOYOTA');self.assertEqual(toyota.configurations[0]['years'],'')
            self.assertEqual(toyota.configurations[0]['engine'],'')
if __name__=='__main__':unittest.main()
