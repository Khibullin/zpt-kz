from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0078_publish_reviewed_peugeot_article')]
    operations = [
        migrations.CreateModel(
            name='EditorialAIExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('day', models.DateField(db_index=True)),
                ('status', models.CharField(max_length=16, default='running')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('page', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ai_execution', to='core.editorialpage')),
            ],
        ),
    ]
