import io
import json
import zipfile
from functools import wraps
from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers, patch_vary_headers
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .views import access, context
from .kaspi_workspace import purchased_batches, source_rows, enrich_rows, batch_url, source_signature
from .kaspi_materials import automatic_values, identity_for, pricing, competitor_data, readiness, snapshot, record, encode_image, store_image
from .kaspi_card_forms import CardMaterialForm, CardPriceForm, PricingRuleForm
from .models import KaspiPreparedCard, KaspiCardImage, KaspiCardPrice, KaspiPricingRule, Preferences, Supplier, Activity


def private_material_cache(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        if request.method == 'GET' and request.GET.get('image') and response.status_code == 200 and response.get('Content-Type') == 'image/jpeg':
            # Every read still passes the purchased-batch permission checks on a cache miss.
            response['Cache-Control'] = 'private, max-age=86400, immutable'
            response.office_image_cache = True
            patch_vary_headers(response, ['Cookie'])
        else:
            add_never_cache_headers(response)
        return response
    return wrapped


def material_initial(card):
    return dict(revision=card.revision if card.pk else 0, title=card.title, description=card.description,
        category=card.category, attributes='\n'.join(f'{k}: {v}' for k, v in card.characteristics.items()),
        applicability=card.applicability, applicability_source=card.applicability_source, verified=card.verified)


def image_url(batch, row, asset, thumb=False, download=False):
    return batch_url(batch['key'], line=row['index'], image=asset.pk,
        **({'thumb': '1'} if thumb else {}), **({'download_photo': '1'} if download else {}))


def purchased_image(request):
    key = request.GET.get('batch')
    batch = next((batch for batch in purchased_batches(request.user) if batch['key'] == key), None)
    if batch is None:
        raise Http404
    try:
        index = int(request.GET.get('line', ''))
        image_id = int(request.GET['image'])
    except ValueError:
        raise Http404
    rows, _, supplier_id = source_rows(batch, request.user)
    row = next((row for row in rows if row['index'] == index), None)
    if row is None:
        raise Http404
    field = 'thumbnail' if request.GET.get('thumb') else 'data'
    asset = KaspiCardImage.objects.filter(pk=image_id, active=True,
        card__identity=identity_for(batch,row,supplier_id)).values_list(field, flat=True).first()
    if asset is None:
        raise Http404
    response = HttpResponse(bytes(asset), content_type='image/jpeg')
    response['X-Content-Type-Options'] = 'nosniff'
    if request.GET.get('download_photo'):
        response['Content-Disposition'] = f'attachment; filename="kaspi-photo-{image_id}.jpg"'
    return response


def scope(request):
    batches = purchased_batches(request.user)
    key = request.GET.get('batch')
    batch = next((b for b in batches if b['key'] == key), None) if key else (batches[0] if batches else None)
    if key and batch is None:
        raise Http404
    rows, files, supplier_id = source_rows(batch, request.user) if batch else ([], [], None)
    material_rows = rows
    if 'line' in request.GET:
        material_rows = [row for row in rows if str(row['index']) == request.GET['line']]
    if batch:
        enrich_rows(batch, material_rows, supplier_id, compact='line' not in request.GET)
    cards = {card.identity: card for card in KaspiPreparedCard.objects.filter(
        identity__in=[identity_for(batch, row, supplier_id) for row in material_rows]).prefetch_related(
            Prefetch('images', queryset=KaspiCardImage.objects.filter(active=True).defer('data', 'thumbnail')))} if rows else {}
    list_page = 'line' not in request.GET
    price_settings = (KaspiPricingRule.objects.filter(user=request.user).first(),
        Preferences.objects.filter(user=request.user).first(),
        {c.card_id: c for c in KaspiCardPrice.objects.filter(user=request.user, card__in=cards.values())}) if list_page and cards else None
    for row in rows:
        row['identity'] = identity_for(batch, row, supplier_id)
        row['prepared'] = cards.get(row['identity'])
        if row['prepared']:
            card = row['prepared']
            assets = list(card.images.all())
            row['photo'] = image_url(batch, row, assets[0], thumb=True) if assets else ''
            row['card_title'] = card.title
            row['description'] = card.description
            checks, ready = readiness(card, assets, pricing(request.user, card, row, competitor_data(row.get('offer')), settings=price_settings)) if list_page else ([], False)
            reasons = {
                'Фотография от 500 × 500': 'Нет фото от 500 × 500',
                'Название и описание': 'Не заполнены название или описание',
                'Категория и характеристики': 'Не заполнены категория или характеристики Kaspi',
                'Применимость с источником': 'Не подтверждена применимость',
                'Проверены требования категории и комплектность': 'Ожидает проверки менеджером',
                'Наша цена': 'Не задана цена',
            }
            row['missing'] = [reasons[label] for label, ok in checks if not ok]
            row['status'] = 'Материалы проверены' if ready else 'Нужно дополнить материалы'
            if row['missing'] == ['Ожидает проверки менеджером']:
                row['status'] = 'Ожидает проверки менеджером'
                row['missing'] = []
        else:
            row['status'] = 'Автоматическая подготовка'
        params = request.GET.copy()
        params.pop('line', None)
        return_url = request.path + ('?' + params.urlencode() if params else '')
        row['url'] = batch_url(batch['key'], line=row['index'], return_to=return_url)
    return batches, batch, rows, files, supplier_id


def packet(card, row, price, images):
    return {'title': card.title, 'brand': card.brand, 'article': card.article,
        'category': card.category, 'description': card.description, 'attributes': card.characteristics,
        'applicability': card.applicability, 'price': str(price['amount']) if price['amount'] is not None else None,
        'recommended': str(price['recommended']) if price['recommended'] is not None else None,
        'quantity': row.get('stock'), 'images': [f'images/photo-{n + 1}.jpg' for n in range(len(images))]}


@private_material_cache
@access()
@require_http_methods(['GET', 'POST'])
def purchased_products(request):
    if request.method == 'GET' and request.GET.get('image'):
        return purchased_image(request)
    batches, batch, rows, files, supplier_id = scope(request)
    active = None
    if 'line' in request.GET:
        try:
            index = int(request.GET['line'])
        except ValueError:
            raise Http404
        active = next((row for row in rows if row['index'] == index), None)
        if active is None:
            raise Http404
    if request.method == 'POST' and request.procurement_role not in ('sales', 'admin'):
        return HttpResponse('Редактирование доступно менеджеру или администратору.', status=403)
    if request.GET.get('job_status') and active and active['prepared']:
        from .models import KaspiCardJob
        return JsonResponse({'jobs':list(KaspiCardJob.objects.filter(Q(user=request.user)|Q(kind='publish'),card=active['prepared']).values('kind','status','reason'))})
    supplier = Supplier.objects.filter(pk=supplier_id).first() if supplier_id else None
    token = signing.dumps({'batch': batch['key'], 'source': source_signature(batch, rows)}, salt='kaspi-card-source') if batch else ''
    event_query = Q(details__kaspi_batch=batch['key']) if batch else Q(pk__in=[])
    if batch and batch['kind'] == 'order':
        event_query |= Q(details__purchase=batch['obj'].pk, user=request.user)
    event_query |= Q(action='kaspi_pricing_rule_saved',user=request.user)
    events = list(Activity.objects.filter(event_query).select_related('user').order_by('-pk')[:50])
    labels = {'kaspi_card_auto': 'Автоматически подготовлены материалы', 'kaspi_card_reused': 'Использована сохранённая карточка',
        'kaspi_card_saved': 'Исправлены материалы', 'kaspi_card_photo': 'Загружена фотография',
        'kaspi_card_photo_auto': 'Автоматически сохранена фотография', 'kaspi_card_price': 'Изменена наша цена',
        'kaspi_card_enriched':'Автоматическая проверка и дополнение материалов', 'kaspi_refresh_queued':'Запрошено обновление конкурентов',
        'kaspi_refresh_result':'Результат обновления конкурентов', 'kaspi_publish_queued':'Запрошена публикация',
        'kaspi_publish_result':'Результат отправки в Kaspi', 'kaspi_card_main_photo':'Выбрано главное фото', 'kaspi_pricing_rule_saved':'Изменён алгоритм цены'}
    for event in events:
        event.label = labels.get(event.action, 'Изменение закупки')
    common = context(request, supplier=supplier, batches=batches, batch=batch, files=files, events=events,
        source_token=token, can_edit=request.procurement_role in ('sales', 'admin'))
    if active is None:
        if request.method == 'POST':
            return HttpResponse('Откройте карточку товара.', status=400)
        q = request.GET.get('q', '').strip()
        shown = [r for r in rows if not q or q.casefold() in f"{r['name']} {r['brand']} {r['article']}".casefold()]
        return render(request, 'procurement/kaspi_purchase_list.html', {**common, 'rows': shown,
            'q': q, 'tab': request.GET.get('tab', 'products')})
    events = [e for e in events if e.action=='kaspi_pricing_rule_saved' or active['prepared'] and e.details.get('card') == active['prepared'].pk]
    common['events'] = events
    listing = batch['obj'].listing_states.filter(line=active['index']).first() if batch['kind'] == 'order' else None
    common['listing'] = listing
    values, sources = automatic_values(active)
    card = active['prepared'] or KaspiPreparedCard(identity=active['identity'], offer=active.get('offer'), **values)
    images = list(card.images.all()) if card.pk else []
    if request.GET.get('image'):
        asset = next((a for a in images if str(a.pk) == request.GET['image']), None)
        if not asset:
            raise Http404
        response = HttpResponse(bytes(asset.thumbnail if request.GET.get('thumb') else asset.data), content_type='image/jpeg')
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, max-age=60'
        if request.GET.get('download_photo'):
            response['Content-Disposition'] = f'attachment; filename="kaspi-photo-{asset.pk}.jpg"'
        return response
    competitors = competitor_data(active.get('offer'))
    price = pricing(request.user, card, active, competitors)
    checks, ready = readiness(card, images, price)
    payload = packet(card, active, price, images)
    if request.GET.get('download') == 'zip':
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('card.json', json.dumps(payload, ensure_ascii=False, indent=2))
            archive.writestr('title.txt',card.title)
            archive.writestr('description.txt',card.description)
            archive.writestr('attributes.txt','\n'.join(f'{k}: {v}' for k,v in card.characteristics.items()))
            archive.writestr('applicability.txt',card.applicability)
            archive.writestr('price.txt',str(price['amount'] or ''))
            archive.writestr('readme.txt', 'Материалы для ручного размещения. В Kaspi не отправлено.\n'
                + '\n'.join(('Готово: ' if ok else 'Нужно дополнить: ') + label for label, ok in checks))
            for n, image in enumerate(images):
                archive.writestr(f'images/photo-{n + 1}.jpg', bytes(image.data))
        response = HttpResponse(output.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="kaspi-card-materials.zip"'
        return response
    action = request.POST.get('action')
    form = CardMaterialForm(request.POST if action == 'save_material' else None, initial=material_initial(card))
    price_form = CardPriceForm(request.POST if action == 'save_price' else None, initial={
        'revision': price['choice'].revision if price['choice'] else 0,
        'mode': price['mode'], 'amount': price['amount']})
    error = ''
    if request.method == 'POST':
        try:
            signed = signing.loads(request.POST.get('source_token', ''), salt='kaspi-card-source', max_age=86400)
        except signing.BadSignature:
            signed = None
        if signed != {'batch': batch['key'], 'source': source_signature(batch, rows)}:
            return HttpResponse('Состав закупки изменился. Обновите карточку.', status=409)
        if action in ('refresh_competitors','publish_kaspi','set_main_photo'):
            if not card.pk:
                messages.error(request,'Карточка подготавливается автоматически. Обновите страницу позже.')
                return redirect(request.get_full_path())
            from .kaspi_card_jobs import queue_job, freeze_publication
            try:
                if action=='set_main_photo':
                    with transaction.atomic():
                        current=KaspiPreparedCard.objects.select_for_update().get(pk=card.pk)
                        if str(current.revision)!=request.POST.get('revision'): raise ValueError('Карточка изменена. Обновите страницу.')
                        asset=current.images.filter(pk=request.POST.get('image_id'),active=True).first()
                        if not asset: raise ValueError('Фотография не найдена.')
                        from django.db.models import F
                        current.images.filter(active=True).update(position=F('position')+1)
                        asset.position=0;asset.save(update_fields=['position'])
                        current.revision+=1;current.save(update_fields=['revision','updated_at'])
                        record(current,request.user,batch['key'],active,'kaspi_card_main_photo')
                    messages.success(request,'Главное фото выбрано.')
                else:
                    payload=(freeze_publication(card,request.user,active,batch,request.POST['source_token']) if action=='publish_kaspi'
                        else dict(batch=batch['key'],line=active['index']))
                    job,created=queue_job(card,request.user,'publish' if action=='publish_kaspi' else 'refresh',payload)
                    messages.success(request,'Задача поставлена в очередь. Обработка выполняется в фоне.' if created else 'Эта задача уже сохранена: '+job.reason)
            except ValueError as exc:
                messages.error(request,str(exc))
            return redirect(request.get_full_path())
        if action == 'save_material' and form.is_valid():
            with transaction.atomic():
                current = KaspiPreparedCard.objects.select_for_update().filter(identity=active['identity']).first()
                revision = current.revision if current else 0
                if revision != form.cleaned_data['revision']:
                    form.add_error(None, 'Карточка изменена. Обновите её перед сохранением.')
                else:
                    current = current or KaspiPreparedCard(identity=active['identity'], offer=active.get('offer'), **values)
                    before = snapshot(current)
                    data = form.cleaned_data
                    changed = []
                    for key in ('title', 'description', 'category', 'applicability', 'applicability_source', 'verified'):
                        if getattr(current, key) != data[key]:
                            setattr(current, key, data[key])
                            changed.append(key)
                    if current.characteristics != data['attributes']:
                        current.characteristics = data['attributes']
                        changed.append('characteristics')
                    current.manual_fields = sorted(set(current.manual_fields) | set(changed))
                    current.revision = revision + 1
                    current.source = {**current.source, 'manual_checked_on': timezone.localdate().isoformat()}
                    current.save()
                    record(current, request.user, batch['key'], active, 'kaspi_card_saved', before)
                    messages.success(request, 'Карточка сохранена. Ручные правки защищены от автоматического обновления.')
                    return redirect(request.get_full_path())
        elif action == 'save_price' and price_form.is_valid():
            with transaction.atomic():
                current_card = KaspiPreparedCard.objects.filter(identity=active['identity']).first()
                if current_card is None:
                    current_card = KaspiPreparedCard.objects.create(identity=active['identity'], offer=active.get('offer'), **values)
                KaspiPreparedCard.objects.select_for_update().get(pk=current_card.pk)
                choice = KaspiCardPrice.objects.select_for_update().filter(card=current_card, user=request.user).first()
                revision = choice.revision if choice else 0
                if revision != price_form.cleaned_data['revision']:
                    price_form.add_error(None, 'Цена изменена в другой вкладке. Обновите карточку.')
                else:
                    choice = choice or KaspiCardPrice(card=current_card, user=request.user)
                    choice.mode = price_form.cleaned_data['mode']
                    choice.amount = price_form.cleaned_data['amount'] if choice.mode == 'manual' else None
                    choice.revision = revision + 1
                    choice.save()
                    Activity.objects.create(user=request.user, action='kaspi_card_price', details={
                        'kaspi_batch': batch['key'], 'card': current_card.pk, 'article': active['article'],
                        'mode': choice.mode, 'amount': str(choice.amount) if choice.amount is not None else None})
                    messages.success(request, 'Режим и цена сохранены.')
                    return redirect(request.get_full_path())
        elif action == 'upload_photo':
            uploaded = request.FILES.get('photo')
            try:
                if not uploaded or uploaded.size > 5 * 1024 * 1024:
                    raise ValueError('Выберите фотографию до 5 МБ.')
                encoded = encode_image(uploaded.read(), require_size=True)
                with transaction.atomic():
                    current = KaspiPreparedCard.objects.select_for_update().filter(identity=active['identity']).first()
                    revision = current.revision if current else 0
                    if str(revision) != request.POST.get('revision'):
                        raise ValueError('Карточка изменена. Обновите страницу перед загрузкой.')
                    current = current or KaspiPreparedCard.objects.create(identity=active['identity'], offer=active.get('offer'), **values)
                    before = snapshot(current)
                    replace_id = request.POST.get('replace_image')
                    if replace_id and not replace_id.isdigit():
                        raise ValueError('Выберите заменяемую фотографию.')
                    asset = store_image(current, encoded, replace_id=int(replace_id) if replace_id else None)
                    current.manual_fields = sorted(set(current.manual_fields) | {'images'})
                    current.verified = False
                    current.revision += 1
                    current.save()
                    record(current, request.user, batch['key'], active, 'kaspi_card_photo', before)
                messages.success(request, 'Фотография и миниатюра сохранены.')
                return redirect(request.get_full_path())
            except ValueError as exc:
                error = str(exc)
        elif action not in ('save_material', 'save_price'):
            return HttpResponse('Действие недоступно. Отправка в Kaspi не выполнялась.', status=400)
    back = request.GET.get('return_to', batch_url(batch['key']))
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()) or not back.startswith('/'):
        back = batch_url(batch['key'])
    gallery = [dict(obj=a, url=image_url(batch, active, a), download=image_url(batch, active, a, download=True),
        source=card.source.get('photo_sources',{}).get(str(a.pk),a.source_url)) for a in images]
    from .kaspi_card_jobs import LABELS, api_attributes
    from .kaspi_catalog_api import connected
    from .kaspi_price_feed import integration_ready
    from .models import KaspiCardJob
    jobs=list(KaspiCardJob.objects.filter(Q(user=request.user)|Q(kind='publish'),card=card)) if card.pk else []
    for job in jobs: job.label=LABELS.get(job.status,job.status)
    _,api_missing=api_attributes(card)
    publish_job=next((job for job in jobs if job.kind=='publish'),None)
    refresh_job=next((job for job in jobs if job.kind=='refresh'),None)
    can_publish=bool(ready and integration_ready() and active.get('stock') is not None and not api_missing and (not publish_job or publish_job.status=='error'))
    extra_images=sum(1 for a in images if card.source.get('photo_kinds',{}).get(str(a.pk))=='external')
    return render(request, 'procurement/kaspi_card.html', {**common, 'active': active, 'card': card,
        'images': gallery, 'form': form, 'price_form': price_form, 'price': price, 'competitors': competitors,
        'checks': checks, 'ready': ready, 'payload': payload, 'photo_error': error,
        'jobs':jobs,'refresh_job':refresh_job,'publish_job':publish_job,'can_publish':can_publish,
        'integration_connected':integration_ready(),'api_missing':api_missing,'extra_images':extra_images,
        'poll_jobs':any(job.status in ('pending','running','submitted','awaiting_listing') for job in jobs),
        'main_photo': gallery[0]['url'] if gallery else '',
        'back_url': back, 'card_page': True}, status=400 if request.method == 'POST' else 200)


@never_cache
@access('sales', 'admin')
@require_http_methods(['GET', 'POST'])
def card_pricing_settings(request):
    rule = KaspiPricingRule.objects.filter(user=request.user).first() or KaspiPricingRule(user=request.user)
    form = PricingRuleForm(request.POST if request.method == 'POST' else None, instance=rule)
    preference = Preferences.objects.filter(user=request.user).first()
    if request.method == 'POST' and form.is_valid():
        if form.cleaned_data['enabled'] and (not preference or preference.commission is None or preference.expenses is None):
            form.add_error(None, 'Сначала укажите комиссию и расходы в личных настройках закупок.')
        else:
            obj = form.save(commit=False)
            obj.user = request.user
            obj.save()
            Activity.objects.create(user=request.user, action='kaspi_pricing_rule_saved', details={
                'method': obj.method, 'value': str(obj.value), 'min_profit': str(obj.min_profit), 'enabled': obj.enabled})
            messages.success(request, 'Алгоритм сохранён. Ручные цены не изменены.')
            return redirect('procurement:kaspi_card_pricing')
    from .kaspi_price_feed import feed_url, integration_ready
    return render(request, 'procurement/kaspi_pricing.html', context(request, form=form, preference=preference, feed_url=feed_url(), integration_connected=integration_ready()),
        status=400 if request.method == 'POST' else 200)
