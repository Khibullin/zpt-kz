from django.db import migrations


def apply(apps, schema_editor):
    from procurement.reviewed_new12c_fitment import apply_review
    apply_review(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0059_restage_reviewed_new12b_photos')]
    operations = [migrations.RunPython(apply, migrations.RunPython.noop)]
