from django.db import migrations, models


def seed_explicit_new_parts_specialization(apps, schema_editor):
    """Only strong existing lead evidence confirms a seller of new parts.

    Names, lack of a dismantler flag, and unclassified leads are not proof.
    Keep all other sellers unclassified for an administrator to review.
    """
    Seller = apps.get_model('core', 'Seller')
    SellerLead = apps.get_model('core', 'SellerLead')
    lead_rows = SellerLead.objects.filter(
        request_seller_id__isnull=False,
        business_type__in=('new_parts', 'dealer', 'wholesaler'),
        business_type_confidence__gte=75,
    ).values_list('request_seller_id', flat=True).distinct()
    Seller.objects.filter(
        pk__in=lead_rows,
        sells_new_parts__isnull=True,
    ).update(sells_new_parts=True)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0086_request_is_dismantler_request'),
    ]

    operations = [
        migrations.AddField(
            model_name='seller',
            name='sells_new_parts',
            field=models.BooleanField(
                null=True,
                blank=True,
                default=None,
                db_index=True,
                verbose_name='Продаёт новые запчасти',
                help_text=(
                    'Да — подтверждённый продавец новых запчастей; '
                    'Нет — не продаёт новые запчасти; '
                    'Неизвестно — требует проверки. '
                    'Возможна комбинация Да и Авторазбор.'
                ),
            ),
        ),
        migrations.RunPython(
            seed_explicit_new_parts_specialization,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
