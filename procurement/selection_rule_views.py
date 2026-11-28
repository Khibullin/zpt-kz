"""Editable first stage of supplier-pricelist preselection.

This view previews the saved criteria against imported offers. It does not modify
supplier imports, stock, public catalogue, Kaspi prices, or existing ROI rules.
"""
from datetime import date
from hashlib import sha256
from time import time
from urllib.parse import urlencode
import json
from decimal import Decimal

from .suppliers import ACTIVE_SUPPLIER_NAMES

from django import forms
from django.core.exceptions import ValidationError
from django.core.cache import cache, caches
from django.contrib import messages
from django.db.models import Q, Count, Max, Exists, OuterRef
from django.shortcuts import redirect, render
from django.urls import reverse

from .models import Offer, Supplier, WholesaleSelectionRule, PriceUpdate, VehicleFitment
from .omega_policy import POSITIVE_STOCK_PATTERN
from .views import access, context

GROUPS = (
    ('consumables', 'Расходники'),
    ('suspension', 'Ходовая и подвеска'),
    ('engine', 'Двигатель'),
    ('electrical', 'Электрика'),
    ('brakes', 'Тормозная система'),
    ('cooling', 'Охлаждение'),
    ('body', 'Кузов и оптика'),
    ('other', 'Другие категории'),
)
# A grouping aid for supplier lines without an explicit category. Classification
# here is a preliminary guess, never a verified vehicle fitment or exact part ID.
KEYWORDS = {
    'consumables': ('фильтр', 'масло', 'свеча', 'щетк', 'щётк', 'ремень', 'filter', 'oil'),
    'suspension': ('подвес', 'рычаг', 'сайлентблок', 'шаровая', 'рулев', 'ступиц', 'амортиз', 'стойк'),
    'engine': ('двигател', 'мотор', 'поршен', 'коленвал', 'грм', 'прокладк', 'engine'),
    'electrical': ('электр', 'катушк', 'стартер', 'генератор', 'реле', 'датчик'),
    'brakes': ('тормоз', 'колодк', 'суппорт', 'brake'),
    'cooling': ('охлажден', 'радиатор', 'термостат', 'помпа', 'антифриз'),
    'body': ('кузов', 'фара', 'бампер', 'зеркал', 'фонар'),
}


class SelectionRuleForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.filter(name__in=ACTIVE_SUPPLIER_NAMES),
                                     required=True, empty_label=None)
    min_cost = forms.DecimalField(min_value=0, max_digits=14, decimal_places=2,
                                  widget=forms.NumberInput(attrs={'min': '0', 'step': '100'}))
    max_vehicle_age = forms.IntegerField(min_value=0, max_value=100,
                                         widget=forms.NumberInput(attrs={'min': '0', 'max': '100'}))
    category_groups = forms.MultipleChoiceField(choices=GROUPS, required=False,
                                               widget=forms.CheckboxSelectMultiple)
    classification_mode = forms.ChoiceField(choices=(
        ('auto', 'Из прайса или автоматически по наименованию'),
        ('supplier', 'Только категория из прайса'),
    ))
    in_stock_only = forms.BooleanField(required=False, initial=True)


def _group_filter(groups, mode):
    if not groups or 'other' in groups:
        return Q()
    combined = Q(pk__in=[])
    for group in groups:
        for word in KEYWORDS.get(group, ()):
            combined |= Q(category__icontains=word)
            if mode == 'auto':
                combined |= Q(name__icontains=word)
    return combined


def _stock_filter():
    # Exactly the positive-stock notation used by the buying rules and
    # stock_status(). Omega's "20>" means more than twenty on hand.
    return Q(stock__regex=POSITIVE_STOCK_PATTERN)


def matching_years(rows, age):
    """Keep one row per offer even when it has many matching fitments.

    The former OR across a reverse relation joined every fitment before running
    DISTINCT over the entire wide Offer row. EXISTS tests the same condition
    without multiplying catalogue rows or sorting their JSON/text fields.
    """
    years = range(max(1980, date.today().year - age), date.today().year + 2)
    year_pattern = '(' + '|'.join(str(year) for year in years) + ')'
    fitments = VehicleFitment.objects.filter(
        offer_id=OuterRef('pk'), verified=True, details__regex=year_pattern,
    )
    return rows.filter(Q(applicability__regex=year_pattern) | Exists(fitments))



def selection_preview_counts(effective, *, refresh=False, force=False):
    supplier = effective.get('supplier')
    supplier_id = getattr(supplier, 'pk', supplier)
    age = int(effective.get('max_vehicle_age', 20))
    criteria = dict(supplier=supplier_id, min_cost=str(effective.get('min_cost') or 0),
                    stock=bool(effective.get('in_stock_only')),
                    groups=sorted(effective.get('category_groups') or []),
                    mode=effective.get('classification_mode', 'auto'), age=age,
                    year=date.today().year)
    key = 'zpt:selection-preview:v3:' + sha256(
        json.dumps(criteria, sort_keys=True).encode()).hexdigest()
    preview_cache = caches['procurement_options']
    completed = preview_cache.get(key)
    # Ordinary navigation keeps the last complete count while the consumer
    # refreshes it. Explicit form submissions always calculate their criteria.
    if completed is not None and not force:
        if not refresh or time() - completed['at'] < 60:
            return tuple(completed['counts'])
    rows = Offer.objects.filter(supplier__name__in=ACTIVE_SUPPLIER_NAMES)
    if supplier_id:
        rows = rows.filter(supplier_id=supplier_id)
    rows = rows.filter(cost__gte=effective.get('min_cost') or 0)
    if effective.get('in_stock_only'):
        rows = rows.filter(_stock_filter())
    rows = rows.filter(_group_filter(effective.get('category_groups') or [],
                                   effective.get('classification_mode', 'auto')))
    result = (rows.count(), matching_years(rows, age).count())
    preview_cache.set(key, {'counts': result, 'at': time()}, timeout=300)
    return result


def warm_selection_preview():
    # Shared saved rule only; no user/session, imports, prices or stock writes.
    rule = WholesaleSelectionRule.objects.filter(singleton_key=1).first()
    omega_id = Supplier.objects.filter(name='Omega').values_list('pk', flat=True).first()
    if rule is None or omega_id is None:
        return
    effective = {name: getattr(rule, name) for name in (
        'min_cost', 'max_vehicle_age', 'category_groups', 'in_stock_only', 'classification_mode')}
    selection_preview_counts(dict(effective, supplier=omega_id), refresh=True)


def _omega_selection_fingerprint(post):
    """Canonicalize the submitted criteria for explicit preview -> save flow."""
    criteria = {
        'batch': str(post.get('price_batch_id', '')).strip(),
        'categories': sorted({str(v).strip().casefold() for v in post.getlist('omega_category')}),
        'price': str(post.get('omega_min_cost', '')).strip(),
        'age': str(post.get('omega_max_vehicle_age', '')).strip(),
        'stock': str(post.get('omega_in_stock_only', '')).strip(),
        'exclude_springs': str(post.get('omega_exclude_springs', '')).strip(),
    }
    return sha256(json.dumps(criteria, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


@access('sales', 'admin')
def omega_selection_rules_help(request):
    return render(request, 'procurement/omega_selection_rules_help.html', context(request))


@access('sales', 'admin')
def selection_rules(request):
    selected_id = request.POST.get('supplier') or request.GET.get('supplier') or request.session.get('preparation_supplier')
    omega = Supplier.objects.filter(pk=selected_id, name__in=ACTIVE_SUPPLIER_NAMES).first() if str(selected_id or '').isdigit() else None
    omega = omega or Supplier.objects.get(name='Omega')
    request.session['preparation_supplier'] = omega.pk
    if request.method == 'POST' and request.POST.get('action') == 'reset_apify_preparation':
        request.session.pop('omega_confirmed_preview', None)
        request.session.pop('apify_prepared_request_id', None)
        messages.success(request, 'Подготовка сброшена. Прайсы, история запросов и результаты проверок сохранены.')
        return redirect(reverse('procurement:selection_rules') + f'?supplier={omega.pk}#omega-pick-title')
    from .selection_exports import EXPORT_BATCH_SIZES, JSON_BATCH_SIZE, export_batch_size
    try:
        selected_batch_size = export_batch_size(request.GET.get('batch_size', JSON_BATCH_SIZE))
    except ValueError:
        selected_batch_size = JSON_BATCH_SIZE
    ui_cities = ('Almaty', 'Astana', 'Shymkent', 'Karaganda', 'Aktobe', 'Atyrau')
    export_cities = [city for city in request.GET.getlist('city') if city in ui_cities] or list(ui_cities)
    try:
        export_cards = int(request.GET.get('cards', '20'))
    except (TypeError, ValueError):
        export_cards = 20
    if export_cards not in range(1, 21):
        export_cards = 20
    rule, _ = WholesaleSelectionRule.objects.get_or_create(singleton_key=1)
    from .price_update_views import SupplierPriceUploadForm, price_history_context
    uploading_price = request.method == 'POST' and request.POST.get('action') == 'upload_price'
    price_form = SupplierPriceUploadForm(request.POST if uploading_price else None,
        request.FILES if uploading_price else None, initial={'supplier': omega.pk}, auto_id='price_%s')
    if uploading_price and price_form.is_valid():
        omega = price_form.cleaned_data['supplier']
        request.session['preparation_supplier'] = omega.pk
        from .price_updates import stage_update
        import hashlib
        uploaded = price_form.cleaned_data['file']
        digest = hashlib.sha256(uploaded.read()).hexdigest()
        uploaded.seek(0)
        prior = PriceUpdate.objects.filter(
            supplier=price_form.cleaned_data['supplier'],
            checked_on=price_form.cleaned_data['checked_on'],
            sha256=digest, full_catalog=False).exists()
        try:
            stage_update(uploaded, price_form.cleaned_data['supplier'],
                         request.user, price_form.cleaned_data['checked_on'], False)
        except ValueError as error:
            price_form.add_error(None, str(error))
        else:
            if prior:
                messages.info(request, 'Этот прайс уже загружен. Используйте существующий; повторной записи нет.')
            else:
                messages.success(request, 'Прайс сохранён для отбора товаров. Рабочие остатки не изменены.')
            request.session.pop('apify_prepared_request_id', None)
            request.session.pop('apify_buying_session_id', None)
            return redirect('procurement:selection_rules')
    initial = {
        'supplier': omega.pk,
        'min_cost': rule.min_cost,
        'max_vehicle_age': rule.max_vehicle_age,
        'category_groups': rule.category_groups,
        'in_stock_only': rule.in_stock_only,
        'classification_mode': rule.classification_mode,
    }
    if request.method == 'POST' and request.POST.get('action') in (
            'begin_apify_chunk_upload', 'append_apify_chunk_upload'):
        from django.http import JsonResponse
        from .apify_batch_intake import begin_chunked_upload, append_chunked_upload
        action = request.POST['action']
        try:
            if action == 'begin_apify_chunk_upload':
                from .request_snapshot_import import upload_request
                selected = upload_request(request, omega)
                session = begin_chunked_upload(
                    filename=request.POST.get('filename', ''),
                    total=int(request.POST.get('total', '0')),
                    digest=request.POST.get('digest', ''),
                    user=request.user, request_id=selected.pk if selected else None)
                request.session['apify_buying_session_id'] = session.pk
                details = session.details
                return JsonResponse({'session_id': session.pk,
                                     'uploaded_batches': details.get('next_upload_batch', details['total_batches']),
                                     'total_batches': details['total_batches']})
            session_id = int(request.POST.get('session_id', '0'))
            if session_id != request.session.get('apify_buying_session_id'):
                return JsonResponse({'error': 'Нет доступа к этой загрузке.'}, status=403)
            raw_chunk = request.POST.get('rows', '')
            if len(raw_chunk) > 8 * 1024 * 1024:
                raise ValidationError('Слишком большая партия JSON.')
            rows = json.loads(raw_chunk)
            result = append_chunked_upload(
                session_id=session_id, batch=int(request.POST.get('batch', '-1')),
                rows=rows, user=request.user)
            return JsonResponse(result)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as error:
            message = '; '.join(error.messages) if isinstance(error, ValidationError) else 'Некорректные данные партии.'
            return JsonResponse({'error': message}, status=400)
    if request.method == 'POST' and request.POST.get('action') == 'import_apify':
        from .apify_batch_intake import stage_apify_file
        uploaded = request.FILES.get('apify_json')
        if uploaded is not None and uploaded.size > 2 * 1024 * 1024:
            messages.error(request, 'Для большого JSON используйте современный браузер: файл отправляется партиями по 500, без одной долгой загрузки.')
            return redirect('procurement:selection_rules')
        if uploaded is None:
            messages.error(request, 'Выберите JSON-файл результатов Apify.')
        else:
            try:
                from .request_snapshot_import import upload_request
                selected = upload_request(request, omega)
                staged = stage_apify_file(uploaded, request.user, request_id=selected.pk if selected else None)
            except ValidationError as error:
                messages.error(request, '; '.join(error.messages))
            else:
                request.session['apify_buying_session_id'] = staged.pk
                messages.success(request, 'Все исходные строки сохранены. Запустите обработку партиями по 500.')
        return redirect('procurement:selection_rules')
    if request.method == 'POST' and request.POST.get('action') == 'publish_apify_profitable':
        from .apify_profitable_publish import publish_profitable
        session_id = request.session.get('apify_buying_session_id')
        if not session_id:
            messages.error(request, 'Не найдена текущая сессия загрузки Apify.')
        else:
            try:
                result = publish_profitable(session_id, request.user)
            except (ValidationError, ValueError, TypeError):
                messages.error(request, 'Не удалось загрузить выгодные. Проверьте завершение JSON и повторите.')
            else:
                messages.success(request,
                    f"Выгодные загружены: добавлено {result['added']}; "
                    f"уже имеется {result['already']}; "
                    f"не допущено {result['skipped']}. "
                    "Совпадение товаров остаётся на проверке.")
        return redirect('procurement:selection_rules')
    if request.method == 'POST' and request.POST.get('action') == 'process_apify_batch':
        from django.http import JsonResponse
        from .apify_batch_intake import process_next_apify_batch
        session_id = request.session.get('apify_buying_session_id')
        if not session_id:
            return JsonResponse({'error': 'Сначала загрузите JSON Apify.'}, status=400)
        if request.POST.get('session_id') and str(session_id) != request.POST['session_id']:
            return JsonResponse({'error': 'Открыта другая загрузка. Обновите страницу текущего запроса.'}, status=409)
        try:
            progress = process_next_apify_batch(session_id, request.user)
        except Exception:
            # A failed transaction leaves the batch cursor unchanged for retry.
            # Persist an explicit failure to distinguish it from a paused browser.
            from django.utils import timezone
            from .models import Activity
            session = Activity.objects.filter(
                pk=session_id, user=request.user, action='apify_upload_session').first()
            if session:
                state = dict(session.details or {})
                state['last_error'] = 'Партия не завершена. Продолжите обработку.'
                state['last_progress_at'] = timezone.now().isoformat()
                session.details = state
                session.save(update_fields=['details'])
            return JsonResponse({'error': 'Партия не завершена. Повторите обработку.'}, status=500)
        return JsonResponse(progress)
    if request.method == 'POST' and request.POST.get('action') in ('preview_omega_request', 'save_omega_request'):
        if not str(request.POST.get('price_batch_id', '')).isdigit() or not PriceUpdate.objects.filter(pk=request.POST['price_batch_id'], supplier=omega).exists():
            from django.http import JsonResponse
            return JsonResponse({'error': 'Выберите прайс текущего поставщика.'}, status=400)
    if request.method == 'POST' and request.POST.get('action') == 'preview_omega_request':
        from django.http import JsonResponse
        from .omega_selection_requests import preview_request
        try:
            result = preview_request(
                batch_id=int(request.POST.get('price_batch_id', '0')),
                categories=request.POST.getlist('omega_category'),
                minimum=request.POST.get('omega_min_cost', '2000'),
                max_vehicle_age=request.POST.get('omega_max_vehicle_age', '20'),
                in_stock_only=(request.POST.get('omega_in_stock_only') == 'on' if request.POST.get('omega_stock_submitted') else True),
                exclude_springs=(request.POST.get('omega_exclude_springs') == 'on'),
            )
            request.session['omega_confirmed_preview'] = _omega_selection_fingerprint(request.POST)
            return JsonResponse(result)
        except (ValidationError, ValueError, TypeError, PriceUpdate.DoesNotExist) as error:
            detail = '; '.join(error.messages) if isinstance(error, ValidationError) else 'Не удалось выполнить отбор.'
            return JsonResponse({'error': detail}, status=400)
    if request.method == 'POST' and request.POST.get('action') == 'save_omega_request':
        from .omega_selection_requests import save_request
        if request.session.get('omega_confirmed_preview') != _omega_selection_fingerprint(request.POST):
            messages.error(request, 'Сначала нажмите «Выбрать товары», проверьте результат и затем сохраните запрос.')
            return redirect('procurement:selection_rules')
        try:
            saved = save_request(
                batch_id=int(request.POST.get('price_batch_id', '0')),
                categories=request.POST.getlist('omega_category'),
                minimum=request.POST.get('omega_min_cost', '2000'),
                max_vehicle_age=request.POST.get('omega_max_vehicle_age', '20'),
                in_stock_only=(request.POST.get('omega_in_stock_only') == 'on' if request.POST.get('omega_stock_submitted') else True),
                exclude_springs=(request.POST.get('omega_exclude_springs') == 'on'),
                user=request.user,
            )
        except (ValidationError, ValueError, TypeError, PriceUpdate.DoesNotExist) as error:
            detail = '; '.join(error.messages) if isinstance(error, ValidationError) else 'Проверьте выбранный прайс и категории.'
            messages.error(request, detail)
        else:
            request.session.pop('omega_confirmed_preview', None)
            messages.success(request, f"{saved.details['title']} сохранён. Можно перейти к формированию JSON.")
            request.session['apify_prepared_request_id'] = saved.pk
            request.session.pop('apify_buying_session_id', None)
            params = [('supplier', str(omega.pk)), ('saved_request', str(saved.pk))]
            if request.POST.get('apify_export_settings'):
                try:
                    size = export_batch_size(request.POST.get('apify_batch_size', JSON_BATCH_SIZE))
                    cards = int(request.POST.get('apify_cards', 20))
                    if cards not in range(1, 21):
                        raise ValueError
                    params.extend([('batch_size', str(size)), ('cards', str(cards))])
                    params.extend(('city', city) for city in request.POST.getlist('apify_city') if city in ui_cities)
                except (TypeError, ValueError):
                    pass
            anchor = '#apify-step-title' if request.POST.get('apify_generate') else '#omega-pick-saved'
            return redirect(reverse('procurement:selection_rules') + '?' + urlencode(params) + anchor)
        return redirect('procurement:selection_rules')
    if request.method == 'POST' and not uploading_price:
        form = SelectionRuleForm(request.POST)
        if request.POST.get('action') == 'reset':
            rule.supplier = omega
            rule.min_cost = Decimal('2000')
            rule.max_vehicle_age = 20
            rule.category_groups = []
            rule.in_stock_only = True
            rule.classification_mode = 'auto'
            rule.updated_by = request.user
            rule.save()
            messages.success(request, 'Установлены исходные значения предварительного отбора.')
            return redirect('procurement:selection_rules')
        if form.is_valid():
            data = form.cleaned_data
            rule.supplier = data['supplier']
            rule.min_cost = data['min_cost']
            rule.max_vehicle_age = data['max_vehicle_age']
            rule.category_groups = data['category_groups']
            rule.in_stock_only = data['in_stock_only']
            rule.classification_mode = data['classification_mode']
            rule.updated_by = request.user
            rule.save()
            messages.success(request, 'Правила предварительного отбора сохранены.')
            return redirect('procurement:selection_rules')
    else:
        form = SelectionRuleForm(initial=initial)

    # Preview against the already imported offers. No destructive updates or
    # automatic purchases / Kaspi calls are initiated by this screen.
    effective = form.cleaned_data if form.is_bound and form.is_valid() else initial
    before_years, matched = selection_preview_counts(
        effective, force=request.method != 'GET')
    unknown_years = max(0, before_years - matched)
    # All download actions use the last saved rule, not unsaved form values.
    # Preview is intentionally short and read-only; full records go to Excel.
    from .selection_exports import JSON_BATCH_SIZE
    # Keep the cheap cached count on the page; exact variant query counts are
    # recorded for each numbered Omega request instead of rescanning fitments.
    exported_total = matched
    query_count = exported_total
    batches = (exported_total + JSON_BATCH_SIZE - 1) // JSON_BATCH_SIZE
    from .models import KaspiBuyingCandidate
    # Compact saved-result summary; the full card list lives only in the buying catalogue.
    buying_qs = KaspiBuyingCandidate.objects.filter(offer__supplier=omega)
    buying_total = buying_qs.filter(
        match_status__in=['candidate', 'verified'], offer__isnull=False,
    ).count()
    last_import = (buying_qs.exclude(source_filename='')
        .values('source_filename')
        .annotate(saved=Count('id'),
                  linked=Count('id', filter=Q(match_status__in=['candidate', 'verified'], offer__isnull=False)),
                  verified=Count('id', filter=Q(match_status='verified', offer__isnull=False)),
                  unmatched=Count('id', filter=Q(match_status='unmatched')),
                  last_saved=Max('imported_at'))
        .order_by('-last_saved', 'source_filename').first())
    from .models import Activity
    apify_session = None
    session_id = request.session.get('apify_buying_session_id')
    if session_id:
        apify_session = Activity.objects.filter(
            pk=session_id, user=request.user, action='apify_upload_session'
        ).first()
    apify_buying_summary = None
    if apify_session:
        from .apify_profitable_publish import complete_session, source_summary
        if complete_session(apify_session):
            apify_buying_summary = source_summary(apify_session)
    from .omega_selection_requests import (
        DEFAULT_CATEGORIES, latest_price_batch, price_categories, request_history,
        category_key, category_usage, get_display_history,
    )
    omega_price_batch = latest_price_batch(omega)
    omega_categories = price_categories(omega_price_batch) if omega_price_batch else []
    default_category_keys = [category_key(name) for name in DEFAULT_CATEGORIES]
    saved_omega_requests = list(request_history(omega))
    previous_category_usage = category_usage(saved_omega_requests, include_legacy=omega.name == 'Omega')
    for category in omega_categories:
        category['previous_requests'] = previous_category_usage.get(category['key'], [])
    # Historically selected categories lead the compact two-column list.
    omega_categories.sort(key=lambda item: (
        not bool(item['previous_requests']), item['name'].casefold(),
    ))
    from .omega_category_groups import group_categories
    omega_category_groups = group_categories(omega_categories)
    omega_requests = get_display_history(saved_omega_requests, include_legacy=omega.name == 'Omega', batch_size=selected_batch_size)
    next_omega_number = max(
        [int(saved.details.get('number', 0)) for saved in saved_omega_requests] + [1 if omega.name == 'Omega' else 0]
    ) + 1
    from .models import Activity
    selected_saved_id = request.GET.get('saved_request', '')
    just_saved_request = None
    if selected_saved_id.isdigit():
        just_saved_request = next((saved for saved in saved_omega_requests if saved.pk == int(selected_saved_id)), None)
    just_saved_batches = range(
        1, (int(just_saved_request.details.get('query_count', 0)) + selected_batch_size - 1) // selected_batch_size + 1
    ) if just_saved_request else ()
    return render(request, 'procurement/selection_rules.html',
                  context(request, apify_session=apify_session, form=form, rule=rule, matched=exported_total,
                          price_form=price_form, **price_history_context(request, omega),
                          year_unknown=unknown_years, before_years=before_years,
                          group_choices=GROUPS,
                          selected_supplier=omega,
                          omega_price_batch=omega_price_batch,
                          omega_categories=omega_categories,
                          omega_category_groups=omega_category_groups,
                          omega_requests=omega_requests,
                          default_category_keys=default_category_keys,
                          next_omega_number=next_omega_number,
                          just_saved_request=just_saved_request,
                          just_saved_batches=just_saved_batches,
                          export_batch_sizes=EXPORT_BATCH_SIZES,
                          export_cities=export_cities, export_cards=export_cards,
                          export_card_choices=range(1, 21),
                          selected_batch_size=selected_batch_size,
                          apify_query_count=query_count,
                          json_batches=range(1, batches + 1),
                          json_batch_size=JSON_BATCH_SIZE,
                          buying_total=buying_total, last_import=last_import,
                          apify_buying_summary=apify_buying_summary))
