from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0074_seed_editorial_intro'),
        ('catalog', '__first__'),
    ]

    operations = [
        migrations.CreateModel(
            name='EditorialCandidate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_key', models.CharField(max_length=100, unique=True)),
                ('title', models.CharField(max_length=240)),
                ('rationale', models.TextField()),
                ('status', models.CharField(max_length=16, default='new', choices=[('new','Новая тема'),('review','Проверить'),('rejected','Отклонена'),('used','Использована')])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('source_product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='editorial_candidates', to='catalog.product')),
            ],
            options={'verbose_name':'Тема для статьи', 'verbose_name_plural':'Темы для статей', 'ordering':['-created_at']},
        ),
    ]
