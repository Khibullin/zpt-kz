"""Mark nine potentially profitable SRR cards for identity review, not publication.
Preserve verified/done review tasks and all underlying supplier/price records.
"""
from django.db import migrations

ITEMS = (
    ('SRR/JAPAN','SRR61246','143949413'),
    ('SRR OE','SA-160004','179824674'),
    ('SRR OE','SA-160013','179820654'),
    ('SRR OE','SA-160017','143104463'),
    ('SRR OE','SA-160018','179820604'),
    ('SRR OE','SA-160032','179237174'),
    ('SRR OE','SA-160061','144011751'),
    ('SRR OE','SA-160095','142774669'),
    ('SRR OE','SA-160126','143207140'),
)

def mark_identity_reviews(apps, schema_editor):
    Offer = apps.get_model('procurement', 'Offer')
    Task = apps.get_model('procurement', 'KaspiReviewTask')
    for brand, article, product_id in ITEMS:
        offers=list(Offer.objects.filter(supplier__name='Omega',
            brand__iexact=brand, article__iexact=article))
        if len(offers)!=1:
            continue
        offer=offers[0]
        reason=('Apify 21.11.2026: артикул '+article+
            ', Kaspi ID '+product_id+
            '. В Kaspi бренд SRR; у Omega '+brand+
            '. Подтвердить производителя, комплектность и исполнение до публикации.')
        task,created=Task.objects.get_or_create(offer_id=offer.pk,
            defaults={'reason':reason,'priority':1,'status':'pending'})
        if not created and task.status=='pending' and task.reason=='Найти и проверить карточку Kaspi':
            task.reason=reason
            task.priority=1
            task.save(update_fields=['reason','priority','updated_at'])

class Migration(migrations.Migration):
    dependencies=[('procurement','0142_kaspi_comparison_images_20261121')]
    operations=[migrations.RunPython(mark_identity_reviews,migrations.RunPython.noop)]
