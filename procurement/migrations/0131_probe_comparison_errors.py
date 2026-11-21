"""One-time non-mutating production smoke probe for 500 errors in comparison views.

No data is edited; exceptions are printed in deploy logs for diagnosis.
"""
import traceback
from django.db import migrations


def probe_comparison(apps, schema_editor):
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory
    from procurement.eep_comparison import _comparison_impl
    user = get_user_model().objects.filter(is_active=True).order_by('id').first()
    if user is None:
        print('COMPARISON_PROBE: no user; skipped', flush=True)
        return
    for pk in (10597, 8659, 8157):
        request = RequestFactory().get(
            '/office/procurement/comparison/{}/'.format(pk),
            {'next': '/office/procurement/?mode=best&batch=next50'})
        request.user = user
        request.procurement_role = 'admin'
        request.session = {}
        try:
            response = _comparison_impl(request, pk)
            print('COMPARISON_PROBE: offer {} status {}'.format(pk, response.status_code), flush=True)
        except Exception:
            print('COMPARISON_PROBE: offer {} FAILED'.format(pk), flush=True)
            traceback.print_exc()


class Migration(migrations.Migration):
    dependencies = [('procurement', '0130_prepare_next_fifty_comparisons')]
    operations = [migrations.RunPython(probe_comparison, migrations.RunPython.noop)]
