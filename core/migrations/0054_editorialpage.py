from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0053_instagrampublication_feed_placement')]

    operations = [
        migrations.CreateModel(
            name='EditorialPage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(max_length=180, unique=True, verbose_name='Адрес страницы')),
                ('title', models.CharField(max_length=240, verbose_name='Заголовок')),
                ('seo_title', models.CharField(blank=True, default='', max_length=240, verbose_name='SEO-заголовок')),
                ('meta_description', models.CharField(max_length=300, verbose_name='Описание для поиска')),
                ('body', models.TextField(verbose_name='Проверенный текст')),
                ('status', models.CharField(choices=[('draft','Черновик'), ('review','На проверке'), ('published','Опубликовано')], default='draft', max_length=16, verbose_name='Статус')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('published_at', models.DateTimeField(blank=True, null=True, verbose_name='Дата публикации')),
            ],
            options={'verbose_name': 'SEO-материал', 'verbose_name_plural': 'SEO-материалы', 'ordering': ['-updated_at']},
        ),
    ]
