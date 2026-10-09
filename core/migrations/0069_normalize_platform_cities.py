from django.db import migrations


CANONICAL_CITIES = (
    'Алматы', 'Астана', 'Шымкент', 'Актобе', 'Караганда', 'Тараз', 'Атырау',
    'Усть-Каменогорск', 'Павлодар', 'Семей', 'Кызылорда', 'Костанай', 'Актау',
    'Уральск', 'Петропавловск', 'Туркестан', 'Кокшетау', 'Темиртау',
    'Талдыкорган', 'Экибастуз', 'Рудный', 'Абай', 'Акколь', 'Аксай', 'Аксу',
    'Алатау', 'Алга', 'Алтай', 'Арал', 'Аркалык', 'Арыс', 'Атбасар', 'Аягоз',
    'Байконыр', 'Балхаш', 'Булаево', 'Державинск', 'Ерейментау', 'Есик',
    'Есиль', 'Жанаозен', 'Жанатас', 'Жаркент', 'Жезказган', 'Жем', 'Жетысай',
    'Житикара', 'Зайсан', 'Казалинск', 'Кандыагаш', 'Каражал', 'Каратау',
    'Каркаралинск', 'Каскелен', 'Кентау', 'Конаев', 'Косшы', 'Кулсары',
    'Курчатов', 'Ленгер', 'Лисаковск', 'Макинск', 'Мамлютка', 'Приозёрск',
    'Риддер', 'Сарань', 'Сарканд', 'Сарыагаш', 'Сатпаев', 'Сергеевка',
    'Серебрянск', 'Степногорск', 'Степняк', 'Тайынша', 'Талгар', 'Текели',
    'Темир', 'Тобыл', 'Ушарал', 'Уштобе', 'Форт-Шевченко', 'Хромтау',
    'Шалкар', 'Шар', 'Шардара', 'Шахтинск', 'Шемонаиха', 'Шу', 'Щучинск',
    'Эмба',
)

ALIASES = {
    'almaty': 'Алматы',
    'alma-ata': 'Алматы',
    'алма-ата': 'Алматы',
    'аматы': 'Алматы',
    'astana': 'Астана',
    'nur-sultan': 'Астана',
    'nursultan': 'Астана',
    'нур-султан': 'Астана',
    'нурсултан': 'Астана',
    'shymkent': 'Шымкент',
    'chimkent': 'Шымкент',
    'aktobe': 'Актобе',
    'aqtobe': 'Актобе',
    'karaganda': 'Караганда',
    'karagandy': 'Караганда',
    'qaragandy': 'Караганда',
    'taraz': 'Тараз',
    'atyrau': 'Атырау',
    'oskemen': 'Усть-Каменогорск',
    'öskemen': 'Усть-Каменогорск',
    'өскемен': 'Усть-Каменогорск',
    'усть каменогорск': 'Усть-Каменогорск',
    'ust-kamenogorsk': 'Усть-Каменогорск',
    'pavlodar': 'Павлодар',
    'semey': 'Семей',
    'semipalatinsk': 'Семей',
    'kyzylorda': 'Кызылорда',
    'qyzylorda': 'Кызылорда',
    'kostanay': 'Костанай',
    'kostanai': 'Костанай',
    'qostanai': 'Костанай',
    'aktau': 'Актау',
    'aqtau': 'Актау',
    'oral': 'Уральск',
    'uralsk': 'Уральск',
    'petropavl': 'Петропавловск',
    'petropavlovsk': 'Петропавловск',
    'turkistan': 'Туркестан',
    'turkestan': 'Туркестан',
    'kokshetau': 'Кокшетау',
    'taldykorgan': 'Талдыкорган',
    'taldyqorgan': 'Талдыкорган',
    'zhezkazgan': 'Жезказган',
    'jezkazgan': 'Жезказган',
    'konaev': 'Конаев',
    'qonaev': 'Конаев',
    'qonayev': 'Конаев',
    'konayev': 'Конаев',
    'капчагай': 'Конаев',
    'kapchagay': 'Конаев',
}

CANONICAL_BY_CASEFOLD = {city.casefold(): city for city in CANONICAL_CITIES}
CANONICAL_BY_CASEFOLD.update(ALIASES)


def canonical(value):
    text = ' '.join(str(value or '').split())
    if not text:
        return ''
    return CANONICAL_BY_CASEFOLD.get(text.casefold(), text)


def normalize_model_city(model, field='city'):
    for row in model.objects.all().iterator():
        old = getattr(row, field, '') or ''
        new = canonical(old)
        if new != old:
            setattr(row, field, new)
            row.save(update_fields=[field])


def normalize_selected_cities(Request):
    for row in Request.objects.exclude(selected_cities='').iterator():
        values = []
        seen = set()
        for part in (row.selected_cities or '').split(','):
            city = canonical(part)
            if not city or city in seen:
                continue
            seen.add(city)
            values.append(city)
        new = ','.join(values)
        if new != row.selected_cities:
            row.selected_cities = new
            row.save(update_fields=['selected_cities'])


def sync_linked_seller_cities(Seller, SellerProfile, Product):
    profiles = {
        profile.user_id: profile
        for profile in SellerProfile.objects.exclude(user_id=None).iterator()
    }
    for seller in Seller.objects.exclude(user_id=None).iterator():
        profile = profiles.get(seller.user_id)
        if profile is None:
            continue
        seller_city = canonical(seller.city)
        profile_city = canonical(profile.city)
        chosen = seller_city or profile_city
        if not chosen:
            continue
        if seller.city != chosen:
            seller.city = chosen
            seller.save(update_fields=['city'])
        if profile.city != chosen:
            profile.city = chosen
            profile.save(update_fields=['city'])
        Product.objects.filter(seller_profile_id=profile.pk).exclude(city=chosen).update(city=chosen)


def normalize_buyer_city_interest(BuyerCityInterest):
    for row in BuyerCityInterest.objects.all().iterator():
        old = row.city or ''
        new = canonical(old)
        normalized = new.casefold()
        fields = []
        if new != old:
            row.city = new
            fields.append('city')
        if row.city_normalized != normalized:
            row.city_normalized = normalized
            fields.append('city_normalized')
        if fields:
            row.save(update_fields=fields)


def normalize_platform_cities(apps, schema_editor):
    Request = apps.get_model('core', 'Request')
    Seller = apps.get_model('core', 'Seller')
    SellerLead = apps.get_model('core', 'SellerLead')
    SellerLeadLocation = apps.get_model('core', 'SellerLeadLocation')
    SellerLeadPipelineRun = apps.get_model('core', 'SellerLeadPipelineRun')
    BuyerContact = apps.get_model('core', 'BuyerContact')
    BuyerCityInterest = apps.get_model('core', 'BuyerCityInterest')

    SellerProfile = apps.get_model('catalog', 'SellerProfile')
    Product = apps.get_model('catalog', 'Product')
    SellerWholesaleTerms = apps.get_model('catalog', 'SellerWholesaleTerms')

    ServiceRequest = apps.get_model('service_requests', 'ServiceRequest')
    ServiceSeller = apps.get_model('service_requests', 'ServiceSeller')

    for model in (
        Request,
        Seller,
        SellerLead,
        SellerLeadLocation,
        SellerLeadPipelineRun,
        BuyerContact,
        SellerProfile,
        Product,
        ServiceRequest,
        ServiceSeller,
    ):
        field = 'primary_city' if model is BuyerContact else 'city'
        normalize_model_city(model, field=field)

    normalize_model_city(SellerWholesaleTerms, field='pickup_city')
    normalize_selected_cities(Request)
    normalize_buyer_city_interest(BuyerCityInterest)
    sync_linked_seller_cities(Seller, SellerProfile, Product)

    # Product.city is a denormalized marketplace field. For every linked product
    # the seller profile is authoritative after the seller/profile sync above.
    for product in Product.objects.exclude(seller_profile_id=None).iterator():
        profile = SellerProfile.objects.filter(pk=product.seller_profile_id).first()
        if profile is None:
            continue
        desired = canonical(profile.city)
        if desired and product.city != desired:
            product.city = desired
            product.save(update_fields=['city'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0068_activate_second_seller_batch_for_requests'),
        ('catalog', '0057_sync_three_kaspi_prices_20261002'),
        ('service_requests', '0009_servicerequestdispatch'),
    ]

    operations = [
        migrations.RunPython(normalize_platform_cities, migrations.RunPython.noop),
    ]
