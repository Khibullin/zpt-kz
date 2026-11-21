"""Non-mutating deployment smoke check for the fixed product comparison 500.

This checks three existing offers after all source data is present.
"""
from django.db import migrations


def smoke_check(apps, schema_editor):
    from django.contrib.auth import get_user_model
    from django.test import RequestFactory
    from procurement.eep_comparison import _comparison_impl
    user = get_user_model().objects.filter(is_active=True).order_by('pk').first()
    if not user:
        return
    for pk in (10597, 8659, 8157):
        from procurement.models import Offer
        if not Offer.objects.filter(pk=pk).exists():
            continue
        request = RequestFactory().get('/office/procurement/comparison/%d/' % pk,
                                       {'next': '/office/procurement/?mode=best&batch=next50'})
        request.user = user
        request.procurement_role = 'admin'
        request.session = {}
        try:
            response = _comparison_impl(request, pk)
            print('COMPARISON_FIXED_SMOKE offer=%d HTTP=%d' % (pk, response.status_code), flush=True)
        except Exception as exc:
            print('COMPARISON_FIXED_SMOKE offer=%d ERROR=%s: %s' %
                  (pk, type(exc).__name__, str(exc)[:120]), flush=True)


class Migration(migrations.Migration):
    dependencies = [('procurement', '0131_probe_comparison_errors')]
    operations = [migrations.RunPython(smoke_check, migrations.RunPython.noop)]
