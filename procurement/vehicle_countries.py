"""Country of the vehicle marque, independent of the spare part manufacturer."""
from core.models import Brand
from .models import VehicleFitment


def country_makes(country_id):
    # A name occurring in multiple countries is ambiguous and stays unclassified.
    countries = {}
    for name, identifier in Brand.objects.values_list('name', 'country_id'):
        countries.setdefault(name.strip().casefold(), set()).add(identifier)
    target = {int(country_id)}
    names = VehicleFitment.objects.filter(verified=True).values_list('make', flat=True).distinct()
    return [name for name in names if countries.get(name.strip().casefold()) == target]
