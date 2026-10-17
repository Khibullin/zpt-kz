from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0093_import_20261017_dismantlers')]
    operations = [migrations.AddField(
        model_name='seller', name='directory_parts',
        field=models.BooleanField(default=False, verbose_name='Показывать в каталоге продавцов запчастей', help_text='Публикация по источнику. Не подтверждает новые запчасти и не включает рассылку.'),
    )]
