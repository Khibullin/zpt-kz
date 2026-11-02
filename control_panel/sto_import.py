"""Staff-only bounded archive import with preview and audit log."""
import hashlib
import zipfile
from collections import Counter
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from control_panel.auth import control_staff_required
from control_panel.views import _base_context
from service_requests.sto_import import read_archive, apply_rows, repair_import_aliases


@control_staff_required
@require_http_methods(['GET', 'POST'])
def sto_import(request):
    if not request.user.has_perms(['service_requests.add_serviceseller', 'service_requests.change_serviceseller']):
        raise PermissionDenied
    context = _base_context(active_nav='sto', title='Загрузка каталога СТО', breadcrumbs=[{'label': 'СТО', 'url': 'control_panel:sto_list'}, {'label': 'Загрузка'}])
    if request.method == 'POST':
        try:
            upload = request.FILES.get('archive')
            if not upload or upload.size > 10_000_000:
                raise ValueError('Выберите ZIP до 10 МБ.')
            data = upload.read()
            digest = hashlib.sha256(data).hexdigest()
            start = int(request.POST.get('start', '0'))
            limit = int(request.POST.get('limit', '50'))
            if start < 0 or not 1 <= limit <= 500:
                raise ValueError('Начальная строка от 0, размер партии от 1 до 500.')
            ready, counts, issues = read_archive(data)
            apply = request.POST.get('action') == 'apply'
            preview_key = f'{digest}:{start}:{limit}'
            if apply and request.session.get('sto_import_preview') != preview_key:
                raise ValueError('Сначала выполните предпросмотр этой партии.')
            if apply:
                repair_import_aliases(ready, request.user)
            results = apply_rows(ready[start:start + limit], request.user, dry_run=not apply)
            if not apply:
                request.session['sto_import_preview'] = preview_key
            else:
                request.session.pop('sto_import_preview', None)
            context.update(counts=counts, issues=issues[:30], results=results,
                           totals=dict(Counter(r['status'] for r in results)), applied=apply,
                           start=start, limit=limit, next_start=min(start + limit, len(ready)), digest=digest)
        except (ValueError, KeyError, zipfile.BadZipFile) as exc:
            context['error'] = str(exc)
    return render(request, 'control_panel/sto_import.html', context)
