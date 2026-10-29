"""Private import of audit results, never distributed with the public source code."""
import json
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from control_panel.auth import control_staff_required
from control_panel.views import _base_context
from core.models import Seller
from core.services.seller_audit import import_audits


@control_staff_required
@require_http_methods(['GET', 'POST'])
def audit_import(request):
    seller_admin = admin.site._registry[Seller]
    if not seller_admin.has_change_permission(request):
        raise PermissionDenied
    payload, error = '', ''
    if request.method == 'POST':
        payload = request.POST.get('payload', '')
        try:
            if len(payload) > 200000:
                raise ValidationError('Слишком большой пакет проверки.')
            rows = json.loads(payload)
            from django.db import transaction
            with transaction.atomic():
                sellers = import_audits(rows)
                for seller in sellers:
                    seller_admin.log_change(request, seller, 'Импорт результатов внешней проверки продавца')
            messages.success(request, f'Проверка сохранена: {len(sellers)} продавцов. Контакты и согласия не изменены.')
            return redirect('control_panel:seller_audit_import')
        except (ValueError, TypeError, ValidationError) as exc:
            error = '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Проверьте формат JSON.'
    context = _base_context(active_nav='sellers', title='Импорт проверки продавцов', breadcrumbs=[{'label':'Продавцы', 'url':'control_panel:seller_list'}, {'label':'Импорт проверки'}])
    context.update(payload=payload, error=error)
    return render(request, 'control_panel/seller_audit_import.html', context)
