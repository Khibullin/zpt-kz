from django.core.management.base import BaseCommand
from core.models import Country, Brand, CarModel


class Command(BaseCommand):
    help = "Импорт легкового каталога (страны, марки, модели)"

    def handle(self, *args, **options):
        data = {
            "Китай": {
                "Chery": ["Tiggo 2", "Tiggo 4", "Tiggo 7", "Tiggo 7 Pro", "Tiggo 8", "Tiggo 8 Pro"],
                "Haval": ["H6", "Jolion", "Dargo", "F7", "F7x", "M6"],
                "Geely": ["Atlas", "Coolray", "Emgrand", "Tugella", "Monjaro"],
                "Changan": ["CS35", "CS55 Plus", "CS75 Plus", "UNI-K", "UNI-V"],
                "Exeed": ["LX", "TXL", "VX", "RX"],
                "Jetour": ["X70", "X70 Plus", "X90", "Dashing"],
                "Tank": ["300", "500"],
                "BYD": ["Song", "Tang", "Han", "Qin"],
                "GAC": ["GS3", "GS4", "GS8"],
                "Omoda": ["C5", "S5"],
                "Jaecoo": ["J7", "J8"],
                "JAC": ["JS4", "JS6", "J7"],
                "FAW": ["Bestune T77", "Bestune T99"],
                "Bestune": ["T55", "T77", "T99"],
                "BAIC": ["X35", "X55", "BJ40"],
                "Hongqi": ["HS5", "H5", "E-QM5"],
                "Great Wall": ["Poer", "Wingle 7"],
                "Wuling": ["Almaz"],
                "MG": ["MG5", "MG One", "MG HS"],
                "Lynk & Co": ["01", "03", "05"],
                "Zeekr": ["001", "X"],
                "Voyah": ["Free", "Dream"],
                "Dongfeng": ["Shine Max", "580"],
                "Li Auto": ["L7", "L8", "L9"],
                "Seres": ["M5", "M7"],
                "NIO": ["ES6", "ET5"],
                "XPeng": ["P7", "G9"],
            },
            "Европа": {
                "BMW": ["3 Series", "5 Series", "X5"],
                "Mercedes-Benz": ["C-Class", "E-Class", "S-Class"],
                "Audi": ["A4", "A6", "Q7"],
                "Volkswagen": ["Passat", "Tiguan", "Touareg"],
                "Skoda": ["Octavia", "Superb", "Kodiaq"],
                "Renault": ["Logan", "Duster", "Megane"],
                "Peugeot": ["301", "308", "3008"],
                "Citroen": ["C4", "C5 Aircross"],
                "Volvo": ["XC60", "XC90", "S60"],
            },
            "Япония": {
                "Toyota": ["Camry", "Corolla", "RAV4", "Land Cruiser Prado"],
                "Nissan": ["X-Trail", "Teana", "Qashqai", "Patrol"],
                "Honda": ["CR-V", "Accord", "Civic"],
                "Mazda": ["Mazda 3", "Mazda 6", "CX-5"],
                "Mitsubishi": ["Outlander", "L200", "Pajero Sport"],
                "Subaru": ["Forester", "Outback", "XV"],
                "Suzuki": ["Vitara", "Jimny"],
                "Lexus": ["RX", "GX", "LX"],
                "Infiniti": ["QX50", "QX60", "QX80"],
            },
            "Корея": {
                "Hyundai": ["Elantra", "Sonata", "Santa Fe", "Tucson"],
                "Kia": ["Rio", "Sportage", "Sorento", "Cerato"],
                "Genesis": ["G70", "G80", "GV70"],
                "Daewoo": ["Nexia", "Matiz"],
            },
            "США": {
                "Ford": ["Focus", "Explorer", "Edge"],
                "Chevrolet": ["Cruze", "Captiva", "Malibu"],
                "Jeep": ["Grand Cherokee", "Wrangler", "Compass"],
                "Cadillac": ["XT5", "Escalade"],
                "Tesla": ["Model 3", "Model Y", "Model X"],
            },
            "Россия": {
                "Lada": ["Granta", "Vesta", "Niva Legend", "Niva Travel"],
                "UAZ": ["Patriot", "Hunter"],
                "GAZ": ["Volga", "Gazelle"],
                "Moskvich": ["3", "6"],
            }
        }

        created_countries = 0
        created_brands = 0
        created_models = 0

        for country_name, brands in data.items():
            country, country_created = Country.objects.get_or_create(name=country_name)
            if country_created:
                created_countries += 1

            for brand_name, models in brands.items():
                brand, brand_created = Brand.objects.get_or_create(
                    country=country,
                    name=brand_name,
                    transport_type='car',
                )
                if brand_created:
                    created_brands += 1

                for model_name in models:
                    _, model_created = CarModel.objects.get_or_create(
                        brand=brand,
                        name=model_name,
                        transport_type='car',
                    )
                    if model_created:
                        created_models += 1

        self.stdout.write(self.style.SUCCESS(
            f"Готово: стран={created_countries}, марок={created_brands}, моделей={created_models}"
        ))