from django.core.management.base import BaseCommand
from catalog.models import Country, Brand, CarModel


REFERENCE_DATA = {
    "Япония": {
        "Toyota": [
            "4Runner", "Alphard", "Aqua", "Avalon", "Avensis", "bB", "Camry",
            "Corolla", "Corolla Cross", "Corolla Fielder", "Corolla Rumion",
            "Crown", "Estima", "FJ Cruiser", "Fortuner", "Harrier", "Hiace",
            "Highlander", "Hilux", "Ipsum", "Ist", "Land Cruiser 100",
            "Land Cruiser 200", "Land Cruiser 300", "Land Cruiser Prado",
            "Mark II", "Passo", "Picnic", "Prius", "Prius Alpha", "Ractis",
            "Raum", "RAV4", "Sequoia", "Sienna", "Surf", "Tundra", "Venza",
            "Vitz", "Yaris"
        ],
        "Lexus": [
            "CT 200h", "ES 250", "ES 300", "ES 350", "GX 460", "GX 470",
            "IS 200", "IS 250", "IS 300", "IS 350", "LX 470", "LX 570",
            "LX 600", "NX 200", "NX 200t", "NX 300", "NX 350", "RX 270",
            "RX 300", "RX 330", "RX 350", "RX 400h", "RX 450h"
        ],
        "Nissan": [
            "Altima", "Almera", "Armada", "Bluebird", "Cube", "Juke",
            "Leaf", "Maxima", "Micra", "Murano", "Navara", "Note", "Pathfinder",
            "Patrol", "Primera", "Qashqai", "Serena", "Sunny", "Teana",
            "Terrano", "Tiida", "X-Trail"
        ],
        "Infiniti": [
            "EX35", "FX35", "FX37", "FX45", "FX50", "G25", "G35", "G37",
            "M35", "M37", "Q50", "Q60", "Q70", "QX50", "QX56", "QX60",
            "QX70", "QX80"
        ],
        "Honda": [
            "Accord", "Civic", "CR-V", "Crosstour", "Fit", "HR-V", "Insight",
            "Jazz", "Legend", "Odyssey", "Pilot", "Stepwgn", "Stream"
        ],
        "Mazda": [
            "CX-3", "CX-5", "CX-7", "CX-9", "Demio", "Mazda2", "Mazda3",
            "Mazda5", "Mazda6", "MPV", "Premacy", "RX-8", "Tribute"
        ],
        "Subaru": [
            "Forester", "Impreza", "Legacy", "Outback", "Tribeca", "WRX", "XV"
        ],
        "Mitsubishi": [
            "ASX", "Colt", "Delica", "Eclipse Cross", "Galant", "L200", "Lancer",
            "Montero", "Outlander", "Pajero", "Pajero Sport"
        ],
        "Suzuki": [
            "Escudo", "Grand Vitara", "Jimny", "Swift", "SX4", "Vitara"
        ]
    },

    "Германия": {
        "Mercedes-Benz": [
            "A-Class", "B-Class", "C-Class", "CLA", "CLC", "CLK", "CLS",
            "E-Class", "G-Class", "GLA", "GLB", "GLC", "GLE", "GLK", "GLS",
            "M-Class", "S-Class", "SL", "Sprinter", "V-Class", "Vito"
        ],
        "BMW": [
            "1 Series", "2 Series", "3 Series", "4 Series", "5 Series",
            "6 Series", "7 Series", "8 Series", "X1", "X2", "X3", "X4",
            "X5", "X6", "X7", "Z4"
        ],
        "Audi": [
            "A3", "A4", "A5", "A6", "A7", "A8", "Q3", "Q5", "Q7", "Q8", "TT"
        ],
        "Volkswagen": [
            "Amarok", "Arteon", "Caddy", "Golf", "Jetta", "Multivan", "Passat",
            "Polo", "Sharan", "Tiguan", "Touareg", "Touran", "Transporter"
        ],
        "Porsche": [
            "Cayenne", "Macan", "Panamera"
        ],
        "Opel": [
            "Astra", "Combo", "Corsa", "Insignia", "Meriva", "Vectra", "Zafira"
        ]
    },

    "США": {
        "Chevrolet": [
            "Aveo", "Camaro", "Captiva", "Cobalt", "Colorado", "Cruze",
            "Epica", "Equinox", "Lacetti", "Malibu", "Niva", "Silverado",
            "Spark", "Suburban", "Tahoe", "Tracker", "TrailBlazer"
        ],
        "Ford": [
            "C-Max", "EcoSport", "Edge", "Escape", "Explorer", "F-150",
            "Fiesta", "Focus", "Fusion", "Kuga", "Mondeo", "Mustang",
            "Ranger", "S-Max", "Transit"
        ],
        "Jeep": [
            "Cherokee", "Compass", "Grand Cherokee", "Patriot", "Renegade",
            "Wrangler"
        ],
        "Dodge": [
            "Caliber", "Challenger", "Charger", "Durango", "Journey", "RAM"
        ],
        "Cadillac": [
            "CTS", "Escalade", "SRX", "XT5"
        ],
        "Chrysler": [
            "200", "300", "Pacifica", "Voyager"
        ]
    },

    "Корея": {
        "Hyundai": [
            "Accent", "Creta", "Elantra", "Genesis", "Getz", "Grandeur",
            "H-1", "i30", "ix35", "Palisade", "Santa Fe", "Solaris", "Sonata",
            "Starex", "Tucson", "Veloster"
        ],
        "Kia": [
            "Carens", "Carnival", "Ceed", "Cerato", "K5", "K7", "Mohave",
            "Optima", "Picanto", "Rio", "Seltos", "Sorento", "Soul", "Sportage",
            "Stinger"
        ],
        "Genesis": [
            "G70", "G80", "G90", "GV70", "GV80"
        ],
        "Daewoo": [
            "Gentra", "Lanos", "Matiz", "Nexia"
        ]
    },

    "Китай": {
        "Chery": [
            "Arrizo 5", "Arrizo 8", "Tiggo 2", "Tiggo 4", "Tiggo 7 Pro",
            "Tiggo 8", "Tiggo 8 Pro"
        ],
        "Haval": [
            "Dargo", "F7", "F7x", "H5", "H6", "Jolion", "M6"
        ],
        "Geely": [
            "Atlas", "Coolray", "Emgrand", "Monjaro", "Tugella"
        ],
        "Changan": [
            "Alsvin", "CS35", "CS55", "CS75", "UNI-K", "UNI-T", "UNI-V"
        ],
        "Jetour": [
            "Dashing", "X70", "X90"
        ],
        "Exeed": [
            "LX", "TXL", "VX"
        ],
        "JAC": [
            "J7", "S3", "S5", "S7"
        ],
        "BYD": [
            "F3", "Han", "Song Plus", "Tang"
        ]
    },

    "Россия": {
        "Lada": [
            "2101", "2104", "2105", "2106", "2107", "2108", "2109", "2110",
            "2111", "2112", "2113", "2114", "2115", "4x4", "Granta", "Kalina",
            "Largus", "Niva", "Priora", "Vesta", "XRAY"
        ],
        "ГАЗ": [
            "24", "31", "3110", "Газель", "Соболь"
        ],
        "УАЗ": [
            "3151", "469", "Hunter", "Patriot", "Pickup"
        ]
    }
}


class Command(BaseCommand):
    help = "Загрузка справочников стран, марок и моделей для каталога"

    def handle(self, *args, **options):
        created_countries = 0
        created_brands = 0
        created_models = 0

        for country_name, brands in REFERENCE_DATA.items():
            country, country_created = Country.objects.get_or_create(name=country_name)
            if country_created:
                created_countries += 1
                self.stdout.write(self.style.SUCCESS(f"Страна добавлена: {country_name}"))
            else:
                self.stdout.write(f"Страна уже есть: {country_name}")

            for brand_name, models in brands.items():
                brand, brand_created = Brand.objects.get_or_create(
                    country=country,
                    name=brand_name,
                )
                if brand_created:
                    created_brands += 1
                    self.stdout.write(self.style.SUCCESS(f"  Марка добавлена: {brand_name}"))
                else:
                    self.stdout.write(f"  Марка уже есть: {brand_name}")

                for model_name in models:
                    model, model_created = CarModel.objects.get_or_create(
                        brand=brand,
                        name=model_name,
                    )
                    if model_created:
                        created_models += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Загрузка завершена"))
        self.stdout.write(self.style.SUCCESS(f"Новых стран: {created_countries}"))
        self.stdout.write(self.style.SUCCESS(f"Новых марок: {created_brands}"))
        self.stdout.write(self.style.SUCCESS(f"Новых моделей: {created_models}"))