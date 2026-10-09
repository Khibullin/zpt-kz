from core.kazakhstan_locations import FIRST_CIRCLE_CITIES, KAZAKHSTAN_CITIES


def kazakhstan_cities(request):
    return {
        'kz_cities': KAZAKHSTAN_CITIES,
        'first_circle_cities': FIRST_CIRCLE_CITIES,
    }
