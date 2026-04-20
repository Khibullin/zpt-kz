from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

from .models import Request, Country, Brand, CarModel, Seller, Match


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

        # 🔥 ПОДБОР ПРОДАВЦОВ
        sellers = Seller.objects.filter(
            is_active=True,
            is_paused=False,
            transport_type=req.transport_type
        )

        if req.city:
            sellers = sellers.filter(city=req.city)

        if req.category:
            sellers = sellers.filter(category=req.category)

        if req.brand:
            sellers = sellers.filter(brand=req.brand)

        if req.model:
            sellers = sellers.filter(model=req.model)

        # 🔥 СОЗДАЁМ MATCH
        matches_created = 0

        for seller in sellers:
            Match.objects.create(
                request=req,
                seller=seller,
                status='prepared'
            )
            matches_created += 1

        return JsonResponse({
            'status': 'ok',
            'id': req.id,
            'matches': matches_created
        })

    return JsonResponse({'error': 'invalid method'}, status=405)