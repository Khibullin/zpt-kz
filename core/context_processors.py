from core.kazakhstan_locations import FIRST_CIRCLE_CITIES, KAZAKHSTAN_CITIES


def kazakhstan_cities(request):
    from core.services.request_connections import enabled
    return {
        'request_connections_enabled': enabled(),
        'kz_cities': KAZAKHSTAN_CITIES,
        'first_circle_cities': FIRST_CIRCLE_CITIES,
    }
