from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('core', '0079_editorialaiexecution')]
    operations = [
        migrations.CreateModel(
            name='EditorialSocialDraft',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('caption', models.TextField()),
                ('status', models.CharField(max_length=16, default='draft', choices=[('draft','Черновик'),('approved','Одобрено'),('published','Опубликовано')])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('article', models.OneToOneField(to='core.editorialpage', on_delete=django.db.models.deletion.CASCADE, related_name='social_draft')),
            ],
            options={'verbose_name':'Анонс статьи для соцсетей', 'verbose_name_plural':'Анонсы статей для соцсетей'},
        ),
    ]
