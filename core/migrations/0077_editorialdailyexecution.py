from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0076_editorialpage_source_candidate')]
    operations = [
        migrations.CreateModel(
            name='EditorialDailyExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField(unique=True)),
                ('status', models.CharField(max_length=16, choices=[('running','Выполняется'),('complete','Завершено'),('failed','Ошибка')], default='running')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'verbose_name':'Ежедневный SEO-запуск', 'verbose_name_plural':'Ежедневные SEO-запуски'},
        ),
    ]
