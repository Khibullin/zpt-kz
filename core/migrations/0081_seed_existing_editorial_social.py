from django.db import migrations


def seed_existing_editorial_social_drafts(apps, schema_editor):
    Page = apps.get_model('core', 'EditorialPage')
    Draft = apps.get_model('core', 'EditorialSocialDraft')
    for page in Page.objects.filter(status='published'):
        url = (
            f'https://zpt.kz/guide/parts/{page.slug}/'
            '?utm_source=instagram&utm_medium=social&utm_campaign=editorial'
        )
        caption = (
            f'{page.title}\n\n'
            'Перед заказом запчасти проверьте артикул и совместимость '
            'по каталогу производителя или VIN.\n\n'
            f'Подробнее: {url}\n'
            '#автозапчасти #Казахстан #ZPTKZ'
        )
        Draft.objects.get_or_create(article_id=page.pk, defaults={
            'caption': caption, 'status': 'draft',
        })


class Migration(migrations.Migration):
    dependencies = [('core', '0080_editorialsocialdraft')]
    operations = [
        migrations.RunPython(seed_existing_editorial_social_drafts,
                             migrations.RunPython.noop),
    ]
