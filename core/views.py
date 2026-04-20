from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

from .models import Request, Country, Brand, CarModel


@csrf_exempt
def create_request(request):
    if request.method == 'POST':
        data = json.loads(request.body)

        req = Request.objects.create(
            transport_type=data.get('transport_type'),
            country=data.get('country', ''),
            brand=data.get('brand', ''),
            model=data.get('model', ''),
            category=data.get('category', ''),
            article=data.get('article', ''),
            description=data.get('description', ''),
            city=data.get('city', ''),
            phone=data.get('phone', ''),
        )

        return JsonResponse({'status': 'ok', 'id': req.id})

    return JsonResponse({'error': 'invalid method'}, status=405)


def countries_list(request):
    countries = Country.objects.order_by('name')
    data = [{'id': country.id, 'name': country.name} for country in countries]
    return JsonResponse({'countries': data})


def brands_by_country(request):
    country_id = request.GET.get('country_id')
    transport_type = request.GET.get('transport_type')

    if not country_id or not transport_type:
        return JsonResponse({'brands': []})

    brands = Brand.objects.filter(
        country_id=country_id,
        transport_type=transport_type
    ).order_by('name')

    data = [{'id': brand.id, 'name': brand.name} for brand in brands]
    return JsonResponse({'brands': data})


def models_by_brand(request):
    brand_id = request.GET.get('brand_id')
    transport_type = request.GET.get('transport_type')

    if not brand_id or not transport_type:
        return JsonResponse({'models': []})

    models = CarModel.objects.filter(
        brand_id=brand_id,
        transport_type=transport_type
    ).order_by('name')

    data = [{'id': model.id, 'name': model.name} for model in models]
    return JsonResponse({'models': data})