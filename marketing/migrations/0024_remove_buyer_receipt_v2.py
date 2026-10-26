from django.db import migrations

def remove_template(apps, schema_editor):
    Template = apps.get_model('marketing', 'MarketingWhatsAppTemplate')
    Template.objects.filter(meta_template_name='zpt_customer_request_receipt_v1', language_code='ru').delete()

class Migration(migrations.Migration):
    dependencies = [('marketing', '0023_buyer_receipt_v2')]
    operations = [migrations.RunPython(remove_template, migrations.RunPython.noop)]
