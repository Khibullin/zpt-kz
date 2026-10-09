from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0075_editorialcandidate')]
    operations = [
        migrations.AddField(
            model_name='editorialpage',
            name='source_candidate',
            field=models.OneToOneField(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='draft_page', to='core.editorialcandidate',
                verbose_name='Исходная тема',
            ),
        ),
    ]
