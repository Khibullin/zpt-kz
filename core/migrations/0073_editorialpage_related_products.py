from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0072_editorialpage'),
        ('catalog', '__first__'),
    ]

    operations = [
        migrations.AddField(
            model_name='editorialpage',
            name='related_products',
            field=models.ManyToManyField(
                blank=True,
                related_name='editorial_pages',
                to='catalog.product',
                verbose_name='Товары по теме',
            ),
        ),
    ]
