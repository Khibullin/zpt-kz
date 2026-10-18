"""Public fields from the supplied Almaty export; never change routing fields."""
from django.db import migrations


def fill(apps,schema_editor):
    Seller=apps.get_model('service_requests','ServiceSeller')
    # Exact identity previously reconciled against the source on 17 October.
    for pk,name,phone,brands in [
        (14,'Hyundai Premium Al-Farabi','77012207943','Hyundai, Genesis'),
        (34,'Vag Gans Service','77778349944',''),
        (64,'Транс Стартер Казахстан','77750074173',''),
        (122,'Subarushopkz','77002323146','Subaru'),
    ]:
        qs=Seller.objects.filter(pk=pk,name=name,whatsapp=phone)
        qs.filter(directory_transport='').update(directory_transport='Легковые автомобили')
        if brands:qs.filter(directory_brands='').update(directory_brands=brands)


class Migration(migrations.Migration):
    dependencies=[('service_requests','0012_public_directory_details')]
    operations=[migrations.RunPython(fill,migrations.RunPython.noop)]
