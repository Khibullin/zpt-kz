from __future__ import annotations

from django.contrib import messages
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
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
    build_valid_brand_index,
    get_brand_model_tree,
    get_vehicle_stats_rows,
    parse_extra_filters_from_post,
    parse_vehicle_selection_from_post,
    suggest_audience_name,
    validate_audience_criteria,
)
from marketing.views import MarketingCabinetMixin


class BuyerVehiclesView(MarketingCabinetMixin, View):
    template_name = 'marketing/buyer_vehicles/index.html'
    active_nav = 'buyer_vehicles'

    def get(self, request):
        return self._render_page(request)

    def post(self, request):
        action = request.POST.get('action', '')
        if action not in {'calculate', 'create_audience'}:
            messages.error(request, 'Неизвестное действие.')
            return self._render_page(request)

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

    def _render_page(
        self,
        request,
        *,
        posted=None,
        criteria=None,
        calculation=None,
        suggested_name='',
    ):
        sort = request.GET.get('sort') or request.POST.get('sort') or SORT_COUNT_ASC
        search = (request.GET.get('search') or request.POST.get('search') or '').strip()
        brand_tree = get_brand_model_tree(include_test=False)
        brand_index = build_valid_brand_index(brand_tree)
        registry = build_contact_registry()
        filter_options = build_audience_filter_options(
            contact_group=GROUP_BUYERS,
            contact_subtype=SUBTYPE_PARTS_REQUESTS,
            registry=registry,
        )

        selected_brands = set()
        selected_all_models: dict[str, bool] = {}
        selected_models: dict[str, set[str]] = {}

        preselect_brand = (request.GET.get('preselect_brand') or '').strip()
        preselect_model = (request.GET.get('preselect_model') or '').strip()
        if preselect_brand and preselect_brand in brand_index:
            selected_brands.add(preselect_brand)
            if preselect_model:
                option = brand_index[preselect_brand]
                model_map = {model.casefold(): model for model in option.models}
                matched_model = model_map.get(preselect_model.casefold())
                if matched_model:
                    selected_models.setdefault(preselect_brand, set()).add(matched_model)
            else:
                selected_all_models[preselect_brand] = True

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

        context = {
            **self.get_broadcast_mode_context(),
            **self.get_marketing_send_mode_context(),
            **self.get_nav_context(),
            'stats_rows': get_vehicle_stats_rows(sort=sort, search=search),
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
