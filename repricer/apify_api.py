"""Staff-controlled run lifecycle; no scheduled launches or writes to Kaspi."""
import json

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.urls import reverse
from django.views.decorators.http import require_POST

from procurement.apify_cities import APIFY_CITIES
from . import apify_client as api
from .apify_json import export_units, input_and_mapping, remaining_input_and_mapping, saved_rules
from .apify_output import import_apify_result
from .models import KaspiApifyRun, KaspiRepricerSearchSettings
from .views import _latest_active_batch


@staff_member_required
def api_runs(request):
    history, error = {}, ''
    if request.GET.get('refresh') == '1':
        try:
            offset = int(request.GET.get('offset', 0))
            if offset < 0 or offset > 10000:
                raise api.ApifyError('Некорректная страница истории.')
            history = api.list_runs(offset)
            # Render only selected public metadata, never provider tokens or URLs.
            history = {**history, 'items': [{key: row.get(key) for key in
                ('id', 'status', 'startedAt', 'finishedAt', 'usageTotalUsd')}
                for row in history.get('items', [])]}
            history['next_offset'] = offset + len(history['items'])
        except (ValueError, TypeError) as exc:
            error = str(exc) if isinstance(exc, api.ApifyError) else 'Не удалось прочитать историю.'
    return render(request, 'repricer/apify_api.html', {
        'connected': api.configured(), 'history': history, 'error': error,
        'runs': KaspiApifyRun.objects.select_related('batch')[:20],
        'batch': _latest_active_batch(),
        'collect_id': request.GET.get('collect', ''),
    })


def _metadata(run, info):
    if info.get('actId') != api.ACTOR_ID:
        raise api.ApifyError('Некорректный Actor в ответе Apify.')
    run.external_id = api.identifier(info.get('id'))
    run.dataset_id = api.identifier(info.get('defaultDatasetId'))
    run.status = str(info.get('status') or '')[:32]
    captured = parse_datetime(str(info.get('startedAt') or ''))
    if captured is None or timezone.is_naive(captured):
        raise api.ApifyError('Apify не вернул дату начала проверки.')
    run.captured_at = captured
    run.actual_charge_usd = info.get('usageTotalUsd')


@staff_member_required
@require_POST
def api_attach(request):
    batch = _latest_active_batch()
    if not batch:
        messages.error(request, 'Сначала загрузите ACTIVE.xlsx.')
        return redirect('repricer:apify_api')
    try:
        info = api.run_info(request.POST.get('run_id'))
        payload = api.run_input(info)
        if not isinstance(payload, dict) or not isinstance(payload.get('cities'), list):
            raise api.ApifyError('В прогоне не указаны города; импорт не выполнен.')
        cities = [city for city in saved_rules().cities if city in payload['cities']]
        if not cities:
            raise api.ApifyError('Города прогона не совпадают с выбранными городами мониторинга.')
        local_id = request.POST.get('local_run_id')
        if local_id:
            if not local_id.isdecimal() or len(local_id) > 18:
                raise api.ApifyError('Некорректный номер локального запуска.')
            with transaction.atomic():
                run = get_object_or_404(KaspiApifyRun.objects.select_for_update(),
                                        pk=local_id, batch=batch, status__in=['UNKNOWN', 'STARTING'])
                if any(payload.get(key, []) != run.input_snapshot.get(key, [])
                       for key in ('cities', 'startUrls', 'searchQueries')):
                    raise api.ApifyError('Задание прогона не совпадает с неподтверждённым запуском.')
                if KaspiApifyRun.objects.filter(batch=batch, external_id=info['id']).exclude(pk=run.pk).exists():
                    raise api.ApifyError('Этот прогон уже подключён.')
                _metadata(run, info)
                run.error = ''
                run.save()
        else:
            run, _ = KaspiApifyRun.objects.get_or_create(batch=batch, external_id=info['id'], defaults={
                'created_by': request.user, 'scope': 'existing', 'input_snapshot': {**payload, 'cities': cities},
            })
        if not run.imported_at:
            _metadata(run, info)
            run.save()
        messages.success(request, 'Прошлый прогон подключён. Получение результатов не запускает новый поиск.')
        return redirect(reverse('repricer:apify_api') + f'?collect={run.pk}')
    except api.ApifyError as exc:
        messages.error(request, str(exc))
    return redirect('repricer:apify_api')


@staff_member_required
@require_POST
def api_start(request):
    batch = _latest_active_batch()
    if not batch:
        messages.error(request, 'Сначала загрузите ACTIVE.xlsx.')
        return redirect('repricer:apify_api')
    try:
        if request.POST.get('authorize_charge') != 'yes':
            raise api.ApifyError('Подтвердите запуск с указанным лимитом расходов.')
        if not api.configured():
            raise api.ApifyError('Не настроен ключ подключения Apify на сервере.')
        amount = api.budget(request.POST.get('max_charge_usd'))
        rules = saved_rules()
        if not rules.cities or any(city not in APIFY_CITIES for city in rules.cities):
            raise api.ApifyError('Сначала сохраните города мониторинга.')
        scope = request.POST.get('scope', 'remaining')
        if scope not in ('remaining', 'all'):
            raise api.ApifyError('Некорректный режим поиска.')
        builder = remaining_input_and_mapping if scope == 'remaining' else input_and_mapping
        payload, mapping = builder(export_units(batch), rules.cities, rules.cards_per_product)
        if not mapping or not (payload['searchQueries'] or payload['startUrls']):
            raise api.ApifyError('Нет товаров для выбранного режима поиска.')
        with transaction.atomic():
            # Serialize starts globally, including double clicks from different staff users.
            KaspiRepricerSearchSettings.objects.select_for_update().get(pk=rules.pk)
            if KaspiApifyRun.objects.exclude(status__in=api.TERMINAL | {'IMPORTED', 'REVIEW'}).exists():
                raise api.ApifyError('Есть незавершённый запуск. Сначала проверьте его историю и результаты.')
            if KaspiApifyRun.objects.filter(batch=batch).exclude(scope='existing').count() >= 2:
                raise api.ApifyError('Достигнут предел двух запусков для этого ACTIVE. Сначала разберите пропуски.')
            run = KaspiApifyRun.objects.create(batch=batch, created_by=request.user,
                scope=scope, input_snapshot=payload, max_charge_usd=amount)
        try:
            _metadata(run, api.start_run(payload, amount))
            run.save()
        except api.ApifyError:
            # A timed-out POST may have started a paid run. Never retry automatically.
            run.status = 'UNKNOWN'
            run.error = 'Запуск не подтверждён. Проверьте историю Apify; автоматический повтор запрещён.'
            run.save(update_fields=['status', 'error', 'updated_at'])
            raise
        messages.success(request, 'Прогон запущен. Получение результатов продолжится на этой странице.')
        return redirect(reverse('repricer:apify_api') + f'?collect={run.pk}')
    except api.ApifyError as exc:
        messages.error(request, str(exc))
    return redirect('repricer:apify_api')


@staff_member_required
@require_POST
def api_step(request, pk):
    try:
        with transaction.atomic():
            run = get_object_or_404(KaspiApifyRun.objects.select_for_update(), pk=pk)
            if run.imported_at:
                return JsonResponse({'done': True, 'status': 'IMPORTED', 'result': run.result})
            if not run.external_id:
                raise api.ApifyError('Сначала найдите этот запуск в истории Apify и подключите по ID.')
            info = api.run_info(run.external_id)
            _metadata(run, info)
            if run.status not in api.TERMINAL:
                run.save()
                return JsonResponse({'done': False, 'status': run.status, 'offset': run.offset})
            page = api.dataset_page(run.dataset_id, run.offset)
            run.raw_items += page
            run.offset += len(page)
            if run.offset > 30000 or len(json.dumps(run.raw_items, ensure_ascii=False).encode()) > 25 * 1024 * 1024:
                raise api.ApifyError('Превышен предел 30 000 записей или 25 МБ. Импорт не применён.')
            if len(page) < 500:
                # Freeze import to the original ACTIVE file, even if a new one has been uploaded.
                cities = [city for city in run.input_snapshot['cities'] if city in saved_rules().cities]
                if not cities:
                    raise api.ApifyError('Выбранные города изменились. Импорт этого прогона остановлен.')
                try:
                    run.result = import_apify_result(run.raw_items, run.batch,
                        filename=f'Apify {run.external_id}', cities=cities,
                        captured_at=run.captured_at, merge_mode=True)
                except ValueError as exc:
                    run.status = 'REVIEW'
                    run.error = str(exc)[:255]
                    run.total = run.offset
                    run.save()
                    return JsonResponse({'done': True, 'error': run.error}, status=400)
                run.result['provider_status'] = run.status
                run.result['cities'] = cities
                run.imported_at = timezone.now()
                run.status = 'IMPORTED'
                request.session['repricer_apify_result'] = run.result
            run.total = run.offset
            run.error = ''
            run.save()
        return JsonResponse({'done': bool(run.imported_at), 'status': run.status,
                             'offset': run.offset, 'result': run.result})
    except (api.ApifyError, ValueError) as exc:
        return JsonResponse({'done': True, 'error': str(exc)}, status=400)
