"""
Единый справочник стран, марок и моделей для ZPT.KZ.

Используется:
- core (заявки на запчасти, кабинет продавца request-parts)
- catalog (ZPT Market: карточки товаров, фильтры покупателей)

Синхронизация в БД: manage.py import_car_catalog_kz
"""

# Китайские марки, активные на рынке KZ (2024–2026)
CHINA_VEHICLE_BRANDS = {
    'Zeekr': ['001', '007', '009', 'X'],
    'BYD': [
        'Song Plus (DM-i/EV)', 'Yuan Up', 'Destroyer 05', 'Qin L DM-i',
        'Han', 'Tang', 'Seal', 'Song', 'Qin',
    ],
    'Changan': [
        'CS55 Plus', 'CS75 Plus', 'X5 Plus', 'UNI-K', 'UNI-V', 'UNI-T',
        'CS35', 'Alsvin',
    ],
    'Deepal': ['S07', 'L07'],
    'Li Auto': ['L6', 'L7', 'L8', 'L9'],
    'Haval': ['Jolion', 'M6', 'F7', 'H6', 'Dargo', 'H9', 'F7x', 'H5'],
    'Chery': [
        'Tiggo 2', 'Tiggo 4 Pro', 'Tiggo 7 Pro Max', 'Tiggo 8 Pro Max',
        'Arrizo 8', 'Tiggo 4', 'Tiggo 7', 'Tiggo 7 Pro', 'Tiggo 8',
        'Tiggo 8 Pro', 'Arrizo 5',
    ],
    'Jetour': ['X70', 'X70 Plus', 'Dashing', 'T2', 'X90'],
    'Geely': ['Monjaro', 'Coolray', 'Atlas', 'Tugella', 'Emgrand'],
    'Tank': ['300', '500'],
    'Voyah': ['Free', 'Dream'],
    'Omoda': ['C5', 'S5'],
    'Jaecoo': ['J7', 'J8'],
    'Exeed': ['TXL', 'VX', 'LX', 'RX'],
    'JAC': ['JS6', 'J7', 'JS4', 'S3', 'S5', 'S7'],
    'GAC': ['GS8', 'GS3', 'GS4'],
    'Hongqi': ['H5', 'HS5', 'E-QM5'],
    'Avatr': ['11', '12', '07'],
    'MG': ['MG5', 'MG One', 'MG HS'],
    'Lynk & Co': ['01', '03', '05'],
    'FAW': ['Bestune T77', 'Bestune T99'],
    'Bestune': ['T55', 'T77', 'T99'],
    'BAIC': ['X35', 'X55', 'BJ40'],
    'Great Wall': ['Poer', 'Wingle 7'],
    'Wuling': ['Almaz'],
    'Dongfeng': ['Shine Max', '580'],
    'Seres': ['M5', 'M7'],
    'NIO': ['ES6', 'ET5'],
    'XPeng': ['P7', 'G9'],
}

VEHICLE_CATALOG = {
    'Китай': CHINA_VEHICLE_BRANDS,
    'Европа': {
        'BMW': ['3 Series', '5 Series', 'X5'],
        'Mercedes-Benz': ['C-Class', 'E-Class', 'S-Class'],
        'Audi': ['A4', 'A6', 'Q7'],
        'Volkswagen': ['Passat', 'Tiguan', 'Touareg'],
        'Skoda': ['Octavia', 'Superb', 'Kodiaq'],
        'Renault': ['Logan', 'Duster', 'Megane'],
        'Peugeot': ['301', '308', '3008'],
        'Citroen': ['C4', 'C5 Aircross'],
        'Volvo': ['XC60', 'XC90', 'S60'],
    },
    'Япония': {
        'Toyota': ['Camry', 'Corolla', 'RAV4', 'Land Cruiser Prado'],
        'Nissan': ['X-Trail', 'Teana', 'Qashqai', 'Patrol'],
        'Honda': ['CR-V', 'Accord', 'Civic'],
        'Mazda': ['Mazda 3', 'Mazda 6', 'CX-5'],
        'Mitsubishi': ['Outlander', 'L200', 'Pajero Sport'],
        'Subaru': ['Forester', 'Outback', 'XV'],
        'Suzuki': ['Vitara', 'Jimny'],
        'Lexus': ['RX', 'GX', 'LX'],
        'Infiniti': ['QX50', 'QX60', 'QX80'],
    },
    'Корея': {
        'Hyundai': ['Elantra', 'Sonata', 'Santa Fe', 'Tucson'],
        'Kia': ['Rio', 'Sportage', 'Sorento', 'Cerato'],
        'Genesis': ['G70', 'G80', 'GV70'],
        'Daewoo': ['Nexia', 'Matiz'],
    },
    'США': {
        'Ford': ['Focus', 'Explorer', 'Edge'],
        'Chevrolet': ['Cruze', 'Captiva', 'Malibu'],
        'Jeep': ['Grand Cherokee', 'Wrangler', 'Compass'],
        'Cadillac': ['XT5', 'Escalade'],
        'Tesla': ['Model 3', 'Model Y', 'Model X'],
    },
    'Россия': {
        'Lada': ['Granta', 'Vesta', 'Niva Legend', 'Niva Travel'],
        'UAZ': ['Patriot', 'Hunter'],
        'GAZ': ['Volga', 'Gazelle'],
        'Moskvich': ['3', '6'],
    },
}
