from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from marketing.models import MarketingAudience
from marketing.services.audiences import GROUP_BUYERS, SUBTYPE_PARTS_REQUESTS, calculate_audience
from marketing.services.audiences.constants import (
    ACTIVITY_PERIOD_CHOICES,
    CATEGORY_PERIOD_CHOICES,
    EXCLUSION_LABELS,
)
from marketing.services.audiences.options import build_audience_filter_options
from marketing.services.contacts import build_contact_registry
from marketing.services.buyer_vehicles import (
    BuyerVehicleFormError,
    SORT_COUNT_ASC,
    build_audience_criteria,
    build_stats_row_index,
    build_vehicle_selection_from_table_keys,
    build_valid_brand_index,
    compute_selection_totals,
    get_brand_model_tree,
    get_vehicle_stats_rows,
    make_table_row_key,
    parse_extra_filters_from_post,
    parse_vehicle_selection_from_post,
    suggest_audience_name,
    table_selection_to_builder_state,
    validate_audience_criteria,
    validate_table_row_keys,
)
from marketing.services.buyer_vehicles.selection import TableSelectionError
from marketing.views import MarketingCabinetMixin


class BuyerVehiclesView(MarketingCabinetMixin, View):
    template_name = 'marketing/buyer_vehicles/index.html'
    active_nav = 'buyer_vehicles'

    def get(self, request):
        return self._render_page(request)

    def post(self, request):
        action = request.POST.get('action', '')
        sort = request.POST.get('sort') or SORT_COUNT_ASC
        search = (request.POST.get('search') or '').strip()
        stats_rows = get_vehicle_stats_rows(sort=sort, search=search)
        row_index = build_stats_row_index(stats_rows)

        if action == 'selection_totals':
            try:
                keys = validate_table_row_keys(
                    request.POST.getlist('table_row'),
                    row_index,
                )
            except TableSelectionError as exc:
                return JsonResponse({'error': str(exc)}, status=400)
            totals = compute_selection_totals(keys, row_index)
            return JsonResponse({
                'model_count': totals.model_count,
                'unique_buyers': totals.unique_buyers,
                'granted_count': totals.granted_count,
                'live_eligible_count': totals.live_eligible_count,
            })

        if action == 'prepare_selection':
            try:
                keys = validate_table_row_keys(
                    request.POST.getlist('table_row'),
                    row_index,
                )
            except TableSelectionError as exc:
                messages.error(request, str(exc))
                return self._render_page(request, sort=sort, search=search)
            if not keys:
                messages.error(request, 'Выберите хотя бы одну модель в таблице.')
                return self._render_page(request, sort=sort, search=search)
            params = [('sort', sort)]
            if search:
                params.append(('search', search))
            for key in keys:
                params.append(('table_select', key))
            url = reverse('marketing:buyer_vehicles')
            return redirect(f'{url}?{urlencode(params)}#builder')

        if action not in {'calculate', 'create_audience'}:
            messages.error(request, 'Неизвестное действие.')
            return self._render_page(request, sort=sort, search=search)

        brand_tree = get_brand_model_tree(include_test=False)
        brand_index = build_valid_brand_index(brand_tree)
        try:
            vehicle_selection = parse_vehicle_selection_from_post(
                request.POST,
                brand_index=brand_index,
            )
            extra = parse_extra_filters_from_post(request.POST)
            criteria = validate_audience_criteria(
                build_audience_criteria(vehicle_selection, extra),
            )
        except BuyerVehicleFormError as exc:
            messages.error(request, str(exc))
            return self._render_page(
                request,
                posted=request.POST,
                sort=sort,
                search=search,
            )

        calculation = calculate_audience(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
        )

        if action == 'calculate':
            return self._render_page(
                request,
                posted=request.POST,
                criteria=criteria,
                calculation=calculation,
                suggested_name=suggest_audience_name(criteria.get('vehicle_selection') or []),
                sort=sort,
                search=search,
            )

        audience_name = (request.POST.get('audience_name') or '').strip()
        if not audience_name:
            audience_name = suggest_audience_name(criteria.get('vehicle_selection') or [])

        audience = MarketingAudience.objects.create(
            name=audience_name,
            description='Сформировано из раздела «Автомобили покупателей».',
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            criteria=criteria,
            is_active=True,
            created_by=request.user,
            last_matched_count=calculation.matched_count,
            last_eligible_count=calculation.eligible_count,
        )
        messages.success(
            request,
            f'Аудитория «{audience.name}» создана. Найдено: {calculation.matched_count}, '
            f'доступно LIVE: {calculation.eligible_count}.',
        )
        return redirect('marketing:audience_detail', pk=audience.pk)

    def _resolve_table_selected_keys(
        self,
        request,
        stats_rows,
    ) -> list[str]:
        row_index = build_stats_row_index(stats_rows)
        keys = [
            value.strip()
            for value in request.GET.getlist('table_select')
            if str(value).strip()
        ]
        if not keys:
            brand = (request.GET.get('preselect_brand') or '').strip()
            model = (request.GET.get('preselect_model') or '').strip()
            if brand and model:
                candidate = make_table_row_key(
                    brand_normalized=brand,
                    model_normalized=model,
                )
                if candidate in row_index:
                    keys = [candidate]
        if not keys:
            return []
        try:
            return validate_table_row_keys(keys, row_index)
        except TableSelectionError:
            return []

    def _render_page(
        self,
        request,
        *,
        posted=None,
        criteria=None,
        calculation=None,
        suggested_name='',
        sort: str | None = None,
        search: str | None = None,
    ):
        sort = sort or request.GET.get('sort') or request.POST.get('sort') or SORT_COUNT_ASC
        search = (
            search
            if search is not None
            else (request.GET.get('search') or request.POST.get('search') or '').strip()
        )
        stats_rows = get_vehicle_stats_rows(sort=sort, search=search)
        row_index = build_stats_row_index(stats_rows)
        table_selected_keys = self._resolve_table_selected_keys(request, stats_rows)
        selection_totals = compute_selection_totals(table_selected_keys, row_index)
        builder_from_table = False

        brand_tree = get_brand_model_tree(include_test=False)
        brand_index = build_valid_brand_index(brand_tree)
        registry = build_contact_registry()
        filter_options = build_audience_filter_options(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            registry=registry,
        )

        selected_brands: set[str] = set()
        selected_all_models: dict[str, bool] = {}
        selected_models: dict[str, set[str]] = {}

        if table_selected_keys:
            try:
                vehicle_selection = build_vehicle_selection_from_table_keys(
                    table_selected_keys,
                    row_index,
                )
                selected_brands, selected_all_models, selected_models = (
                    table_selection_to_builder_state(vehicle_selection)
                )
                builder_from_table = True
            except TableSelectionError:
                table_selected_keys = []

        if posted is not None:
            selected_brands = {
                value.strip()
                for value in posted.getlist('selection_brand')
                if str(value).strip()
            }
            selected_all_models = {}
            selected_models = {}
            for brand_key in selected_brands:
                selected_all_models[brand_key] = posted.get(f'selection_all_models__{brand_key}') == '1'
                selected_models[brand_key] = {
                    value.strip()
                    for value in posted.getlist(f'selection_model__{brand_key}')
                    if str(value).strip()
                }

        if criteria is None and posted is not None:
            try:
                vehicle_selection = parse_vehicle_selection_from_post(
                    posted,
                    brand_index=brand_index,
                )
                extra = parse_extra_filters_from_post(posted)
                criteria = build_audience_criteria(vehicle_selection, extra)
            except BuyerVehicleFormError:
                criteria = None

        brand_form_states = []
        for brand in brand_tree:
            brand_key = brand.brand_normalized
            brand_form_states.append({
                'brand': brand,
                'checked': brand_key in selected_brands,
                'all_models': selected_all_models.get(brand_key, False),
                'model_states': [
                    {
                        'name': model,
                        'checked': model in selected_models.get(brand_key, set()),
                    }
                    for model in brand.models
                ],
            })

        stats_rows_with_keys = [
            {
                'row': row,
                'selection_key': make_table_row_key(
                    brand_normalized=row.brand_normalized,
                    model_normalized=row.model_normalized,
                ),
            }
            for row in stats_rows
        ]

        context = {
            **self.get_broadcast_mode_context(),
            **self.get_marketing_send_mode_context(),
            **self.get_nav_context(),
            'stats_rows_with_keys': stats_rows_with_keys,
            'table_selected_keys': table_selected_keys,
            'selection_totals': selection_totals,
            'builder_from_table': builder_from_table,
            'brand_tree': brand_tree,
            'brand_form_states': brand_form_states,
            'sort': sort,
            'search': search,
            'filter_options': filter_options,
            'activity_period_choices': ACTIVITY_PERIOD_CHOICES,
            'category_period_choices': CATEGORY_PERIOD_CHOICES,
            'selected_brands': selected_brands,
            'selected_all_models': selected_all_models,
            'selected_models': selected_models,
            'criteria': criteria or {},
            'calculation': calculation,
            'suggested_name': suggested_name or (
                suggest_audience_name((criteria or {}).get('vehicle_selection') or [])
                if criteria else ''
            ),
            'exclusion_labels': EXCLUSION_LABELS,
            'posted': posted,
        }
        return render(request, self.template_name, context)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(['GET', 'POST'])
