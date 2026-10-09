from django import forms
from django.contrib import admin
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from core.platform_help import build_help_whatsapp_reply_url
from core.kazakhstan_locations import canonical_kazakhstan_city
from .models import WhatsAppMessageLog

from openpyxl import load_workbook

from catalog.instagram_service import (
    approve_instagram_publication,
    cancel_instagram_publication,
    mark_stuck_instagram_publication_failed,
    queue_instagram_publication_for_processing,
)
from django.db.models import Count, Exists, OuterRef, Prefetch, Q

from core.services.seller_lead_whatsapp_state import (
    WHATSAPP_STATE_CHOICES,
    annotate_seller_leads_with_whatsapp_state,
    filter_seller_leads_by_whatsapp_state,
    seller_lead_whatsapp_state,
    whatsapp_state_from_annotations,
)
from core.services.seller_lead_marketplace_onboarding import (
    build_marketplace_invite_whatsapp_url,
    mark_seller_lead_invited,
)

from .models import (
    Country,
    Brand,
    CarModel,
    PartCategory,
    BroadcastSettings,
    Request,
    Seller,
    SellerLead,
    SellerLeadContactCandidate,
    SellerLeadDiscoveredBrand,
    SellerLeadDiscoveredCategory,
    SellerLeadDiscoveredModel,
    SellerLeadDuplicateMatch,
    SellerLeadEvidence,
    SellerLeadLocation,
    SellerLeadPipelineRun,
    SellerLeadSource,
    SELLER_LEAD_DISCOVERY_SOURCE_CHOICES,
    Match,
    RequestDispatch,
    SellerRequestPageEvent,
    SELLER_REQUEST_PAGE_EVENT_CALL_CLICK,
    SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
    Feedback,
    InstagramPublication,
    BuyerContact,
    BuyerVehicle,
    BuyerCategoryInterest,
    BuyerCityInterest,
    ContactConsent,
    BuyerAudience,
    BuyerBroadcastCampaign,
    BuyerBroadcastRecipient,
    BUYER_BROADCAST_RECIPIENT_SENT,
    BUYER_BROADCAST_RECIPIENT_FAILED,
    BUYER_BROADCAST_RECIPIENT_SKIPPED,
    CONTACT_CONSENT_CHANNEL_WHATSAPP,
    CONTACT_CONSENT_PURPOSE_MARKETING,
    PlatformHelpConversation,
    PlatformHelpMessage,
    SellerContactConsent,
    WhatsAppInboundEvent,
)
from core.buyer_audience_admin_forms import (
    BuyerAudienceAdminForm,
    format_criteria_details,
    format_criteria_summary,
)
from core.buyer_broadcast_admin_forms import BuyerBroadcastCampaignAdminForm
from core.buyer_contact_admin_filters import (
    BuyerActivityFilter,
    BuyerBrandFilter,
    BuyerCategoryFilter,
    BuyerMarketingConsentFilter,
    BuyerModelFilter,
    BuyerPrimaryCityFilter,
    BuyerRequestCountFilter,
    BuyerTransportTypeFilter,
    build_category_summary,
    build_vehicle_summary,
    marketing_consent_label,
)
from core.services.buyer_contact_utils import mask_phone
from core.services.seller_request_response import (
    REACTION_CANNOT_FULFILL,
    REACTION_CONTACT,
    REACTION_NO_REACTION,
    REACTION_OPENED,
    REACTION_OUT_OF_STOCK,
    classify_seller_request_reaction,
    summarize_request_reactions,
)
from core.services.buyer_audience_service import (
    audience_criteria_has_filters,
    preview_buyer_audience,
)
from core.services.buyer_broadcast_service import (
    prepare_test_campaign,
    preview_test_campaign,
)
from core.services.buyer_broadcast_settings import (
    get_buyer_broadcast_mode,
    get_buyer_broadcast_test_max_recipients,
)


class SellerImportForm(forms.Form):
    file = forms.FileField(label='Excel файл .xlsx')


def normalize_phone(value):
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


def split_values(value):
    if not value:
        return []

    raw = str(value).replace(',', ';')
    return [item.strip() for item in raw.split(';') if item and item.strip()]


def get_cell(row, headers, name, default=''):
    index = headers.get(name)

    if index is None:
        return default

    value = row[index]

    if value is None:
        return default

    return str(value).strip()


def parse_sellers_xlsx(file_obj):
    workbook = load_workbook(file_obj, data_only=True)
    sheet = workbook.active

    rows = list(sheet.iter_rows(values_only=True))

    if not rows:
        return [], ['Файл пустой']

    header_row = rows[0]

    headers = {
        str(value).strip().lower(): index
        for index, value in enumerate(header_row)
        if value
    }

    required = [
        'seller_name',
        'transport_type',
    ]

    errors = []

    for col in required:
        if col not in headers:
            errors.append(f'Нет обязательной колонки: {col}')

    if errors:
        return [], errors

    parsed_rows = []

    for row_number, row in enumerate(rows[1:], start=2):
        if not row or not any(row):
            continue

        seller_name = get_cell(row, headers, 'seller_name')
        whatsapp = normalize_phone(get_cell(row, headers, 'whatsapp'))
        phone2 = normalize_phone(get_cell(row, headers, 'phone2'))
        raw_city = get_cell(row, headers, 'city', 'Алматы')
        city = canonical_kazakhstan_city(raw_city) or ''
        market_location = get_cell(row, headers, 'market_location')
        transport_type = get_cell(row, headers, 'transport_type', 'car').lower()
        categories = get_cell(row, headers, 'categories')
        countries = get_cell(row, headers, 'countries')
        brands = get_cell(row, headers, 'brands')
        seller_type = get_cell(row, headers, 'seller_type', 'seller').lower()
        dispatch_priority_raw = get_cell(row, headers, 'dispatch_priority', '1000')
        notes = get_cell(row, headers, 'notes')

        if not seller_name:
            seller_name = f"Seller {row_number}"
            notes = (notes + " | " if notes else "") + "Требует проверки: нет названия"

        if raw_city and not city:
            notes = (notes + " | " if notes else "") + "Требует проверки: неизвестный город"

        if not whatsapp:
            whatsapp = f"NO-WA-{row_number}"
            notes = (notes + " | " if notes else "") + "Требует проверки: нет WhatsApp"

        if not categories:
            categories = 'general_parts'
            notes = (notes + " | " if notes else "") + "Требует проверки: нет категории"

        if not countries:
            countries = 'Multi'
            notes = (notes + " | " if notes else "") + "Требует проверки: нет страны"

        if not brands:
            brands = 'Multi'
            notes = (notes + " | " if notes else "") + "Требует проверки: нет марки"

        if transport_type not in ['car', 'truck']:
            transport_type = 'car'
            notes = (notes + " | " if notes else "") + "Требует проверки: transport_type исправлен на car"

        if seller_type not in ['seller', 'service', 'both']:
            seller_type = 'seller'
            notes = (notes + " | " if notes else "") + "Требует проверки: seller_type исправлен на seller"

        try:
            dispatch_priority = int(dispatch_priority_raw)
        except ValueError:
            dispatch_priority = 1000
            notes = (notes + " | " if notes else "") + "Требует проверки: priority исправлен на 1000"

        parsed_rows.append({
            'row_number': row_number,
            'seller_name': seller_name[:255],
            'whatsapp': whatsapp,
            'phone2': phone2,
            'city': city,
            'market_location': market_location[:255],
            'transport_type': transport_type,
            'categories': categories,
            'countries': countries,
            'brands': brands,
            'seller_type': seller_type,
            'receive_requests': False,
            'is_test_seller': False,
            'dispatch_priority': dispatch_priority,
            'notes': notes,
        })

    return parsed_rows, errors


def find_seller_by_whatsapp(whatsapp):
    target = normalize_phone(whatsapp)

    if not target:
        return None

    for seller in Seller.objects.all():
        if normalize_phone(seller.whatsapp) == target:
            return seller

    return None


def import_seller_row(row):
    seller = find_seller_by_whatsapp(row['whatsapp'])
    created = False

    if seller is None:
        seller = Seller()
        created = True

    category_names = split_values(row.get('categories'))
    country_names = split_values(row.get('countries'))
    brand_names = split_values(row.get('brands'))

    if not category_names:
        category_names = ['general_parts']

    if not country_names:
        country_names = ['Multi']

    if not brand_names:
        brand_names = ['Multi']

    seller.name = row['seller_name'][:255]
    seller.whatsapp = row['whatsapp'][:20]
    seller.phone2 = row['phone2'][:20]
    seller.city = row['city'][:100]
    seller.market_location = row['market_location'][:255]
    seller.transport_type = row['transport_type']
    seller.seller_type = row['seller_type']
    seller.dispatch_priority = row['dispatch_priority']
    seller.notes = row['notes']

    # Безопасность импорта: никто не получает заявки автоматически.
    seller.receive_requests = False
    seller.is_test_seller = False
    seller.is_active = True
    seller.is_paused = False

    seller.category = category_names[0] if category_names else ''
    seller.brand = brand_names[0] if brand_names else ''
    seller.model = ''

    seller.all_categories = not bool(category_names) or 'Multi' in category_names
    seller.all_countries = not bool(country_names) or 'Multi' in country_names
    seller.all_brands = not bool(brand_names) or 'Multi' in brand_names
    seller.all_models = True

    seller.save()

    seller.selected_categories.clear()
    seller.selected_countries.clear()
    seller.selected_brands.clear()
    seller.selected_models.clear()

    for category_name in category_names:
        if category_name == 'Multi':
            continue

        category, _ = PartCategory.objects.get_or_create(name=category_name)
        seller.selected_categories.add(category)

    for country_name in country_names:
        if country_name == 'Multi':
            continue

        country, _ = Country.objects.get_or_create(name=country_name)
        seller.selected_countries.add(country)

    for brand_name in brand_names:
        if brand_name == 'Multi':
            continue

        matches = Brand.objects.filter(
            name__iexact=brand_name,
            transport_type=row['transport_type']
        )

        for brand in matches:
            seller.selected_brands.add(brand)

    return created


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'country', 'transport_type')
    list_filter = ('country', 'transport_type')
    search_fields = ('name',)


@admin.register(CarModel)
class CarModelAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'brand', 'transport_type')
    list_filter = ('transport_type', 'brand', 'brand__country')
    search_fields = ('name',)


@admin.register(PartCategory)
class PartCategoryAdmin(admin.ModelAdmin):
    list_display = ('id', 'name')
    search_fields = ('name',)


@admin.register(BroadcastSettings)
class BroadcastSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'mode',
        'wave_size',
        'wave_interval_minutes',
        'emergency_stop',
        'updated_at',
    )

    readonly_fields = ('updated_at',)

    fieldsets = (
        ('Broadcast Control', {
            'fields': (
                'mode',
                'wave_size',
                'wave_interval_minutes',
                'emergency_stop',
                'updated_at',
            )
        }),
    )

    def has_add_permission(self, request):
        if BroadcastSettings.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'source',
        'dispatch_mode',
        'routing_strategy',
        'transport_type',
        'country',
        'brand',
        'model',
        'year',
        'category',
        'city',
        'phone',
        'status',
        'seller_reaction_summary',
        'created_at'
    )

    list_filter = (
        'source',
        'dispatch_mode',
        'routing_strategy',
        'transport_type',
        'status',
        'city',
        'category'
    )

    search_fields = (
        'country',
        'brand',
        'model',
        'article',
        'description',
        'phone',
        'vin',
    )

    readonly_fields = ('buyer_contact', 'idempotency_key')

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related(
                Prefetch(
                    'dispatches',
                    queryset=RequestDispatch.objects.select_related('seller'),
                ),
                'seller_page_events',
            )
        )

    @admin.display(description='Реакция продавцов')
    def seller_reaction_summary(self, obj):
        return summarize_request_reactions(obj).label


BUYER_INLINE_AGGREGATE_READONLY = (
    'first_seen_at',
    'last_seen_at',
    'requests_count',
    'created_at',
    'updated_at',
)


class BuyerVehicleInline(admin.TabularInline):
    model = BuyerVehicle
    extra = 0
    readonly_fields = BUYER_INLINE_AGGREGATE_READONLY + (
        'brand_normalized',
        'model_normalized',
    )
    fields = (
        'transport_type',
        'brand',
        'model',
        'brand_normalized',
        'model_normalized',
        'first_seen_at',
        'last_seen_at',
        'requests_count',
    )


class BuyerCategoryInterestInline(admin.TabularInline):
    model = BuyerCategoryInterest
    extra = 0
    readonly_fields = BUYER_INLINE_AGGREGATE_READONLY + ('category_normalized',)
    fields = (
        'category',
        'category_normalized',
        'first_seen_at',
        'last_seen_at',
        'requests_count',
    )


class BuyerCityInterestInline(admin.TabularInline):
    model = BuyerCityInterest
    extra = 0
    readonly_fields = BUYER_INLINE_AGGREGATE_READONLY + ('city_normalized',)
    fields = (
        'city',
        'city_normalized',
        'interest_type',
        'first_seen_at',
        'last_seen_at',
        'requests_count',
    )


class ContactConsentInline(admin.TabularInline):
    model = ContactConsent
    extra = 0
    readonly_fields = ('created_at', 'updated_at')
    fields = (
        'channel',
        'purpose',
        'status',
        'source',
        'consent_text_version',
        'evidence_reference',
        'consented_at',
        'revoked_at',
    )


@admin.action(description='Отметить как тестовые контакты')
def mark_buyer_contacts_as_test(modeladmin, request, queryset):
    updated = queryset.update(is_test_contact=True)
    messages.success(request, f'Отмечено как тестовые контакты: {updated}.')


@admin.action(description='Снять признак тестового контакта')
def unmark_buyer_contacts_as_test(modeladmin, request, queryset):
    updated = queryset.update(is_test_contact=False)
    messages.success(request, f'Снят признак тестового контакта: {updated}.')


@admin.action(description='Отметить как контрольные получатели')
def mark_buyer_contacts_as_control(modeladmin, request, queryset):
    updated = queryset.update(is_control_recipient=True)
    messages.success(request, f'Отмечено как контрольные получатели: {updated}.')


@admin.action(description='Снять признак контрольного получателя')
def unmark_buyer_contacts_as_control(modeladmin, request, queryset):
    updated = queryset.update(is_control_recipient=False)
    messages.success(request, f'Снят признак контрольного получателя: {updated}.')


@admin.register(BuyerContact)
class BuyerContactAdmin(admin.ModelAdmin):
    list_display = (
        'masked_phone',
        'primary_city',
        'vehicles_summary',
        'categories_summary',
        'requests_count',
        'last_request_at',
        'last_search_scope',
        'marketing_consent_status',
        'is_test_contact',
        'control_recipient_display',
        'status',
    )
    list_filter = (
        'is_test_contact',
        'is_control_recipient',
        'status',
        BuyerMarketingConsentFilter,
        BuyerActivityFilter,
        BuyerRequestCountFilter,
        'primary_country',
        BuyerPrimaryCityFilter,
        BuyerTransportTypeFilter,
        BuyerBrandFilter,
        BuyerModelFilter,
        BuyerCategoryFilter,
        'last_search_scope',
    )
    search_fields = (
        'phone_normalized',
        'primary_country',
        'primary_city',
        'vehicles__brand',
        'vehicles__model',
        'category_interests__category',
    )
    ordering = ('-last_request_at', '-id')
    list_select_related = ('portal_access',)
    readonly_fields = (
        'phone_normalized',
        'primary_country',
        'primary_city',
        'first_request_at',
        'last_request_at',
        'requests_count',
        'last_search_scope',
        'city_scope_requests_count',
        'kazakhstan_scope_requests_count',
        'custom_scope_requests_count',
        'portal_access',
        'created_at',
        'updated_at',
    )
    fields = (
        'phone_normalized',
        'status',
        'is_test_contact',
        'is_control_recipient',
        'primary_country',
        'primary_city',
        'first_request_at',
        'last_request_at',
        'requests_count',
        'last_search_scope',
        'city_scope_requests_count',
        'kazakhstan_scope_requests_count',
        'custom_scope_requests_count',
        'portal_access',
        'source',
        'created_at',
        'updated_at',
    )
    inlines = (
        BuyerVehicleInline,
        BuyerCategoryInterestInline,
        BuyerCityInterestInline,
        ContactConsentInline,
    )
    actions = (
        mark_buyer_contacts_as_test,
        unmark_buyer_contacts_as_test,
        mark_buyer_contacts_as_control,
        unmark_buyer_contacts_as_control,
    )

    def get_queryset(self, request):
        marketing_consents = ContactConsent.objects.filter(
            channel=CONTACT_CONSENT_CHANNEL_WHATSAPP,
            purpose=CONTACT_CONSENT_PURPOSE_MARKETING,
        )
        return super().get_queryset(request).select_related(
            'portal_access',
        ).prefetch_related(
            Prefetch(
                'vehicles',
                queryset=BuyerVehicle.objects.order_by('-last_seen_at', '-id'),
            ),
            Prefetch(
                'category_interests',
                queryset=BuyerCategoryInterest.objects.order_by(
                    '-last_seen_at',
                    '-id',
                ),
            ),
            Prefetch(
                'city_interests',
                queryset=BuyerCityInterest.objects.order_by('-last_seen_at', '-id'),
            ),
            Prefetch(
                'consents',
                queryset=marketing_consents,
                to_attr='marketing_consents',
            ),
        )

    def get_search_results(self, request, queryset, search_term):
        queryset, may_have_duplicates = super().get_search_results(
            request,
            queryset,
            search_term,
        )
        return queryset.distinct(), True

    @admin.display(description='Телефон', ordering='phone_normalized')
    def masked_phone(self, obj):
        return mask_phone(obj.phone_normalized)

    @admin.display(description='Контрольный', ordering='is_control_recipient')
    def control_recipient_display(self, obj):
        return 'CONTROL' if obj.is_control_recipient else '—'

    @admin.display(description='Автомобили')
    def vehicles_summary(self, obj):
        prefetched = getattr(obj, '_prefetched_objects_cache', {})
        vehicles = prefetched.get('vehicles')
        if vehicles is None:
            vehicles = obj.vehicles.all()
        return build_vehicle_summary(vehicles)

    @admin.display(description='Категории')
    def categories_summary(self, obj):
        prefetched = getattr(obj, '_prefetched_objects_cache', {})
        interests = prefetched.get('category_interests')
        if interests is None:
            interests = obj.category_interests.all()
        return build_category_summary(interests)

    @admin.display(description='Рекламное согласие')
    def marketing_consent_status(self, obj):
        consents = getattr(obj, 'marketing_consents', None)
        if consents:
            return marketing_consent_label(consents[0].status)
        return marketing_consent_label(None)


@admin.register(BuyerVehicle)
class BuyerVehicleAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'buyer',
        'transport_type',
        'brand',
        'model',
        'requests_count',
        'last_seen_at',
    )
    list_filter = ('transport_type',)
    search_fields = (
        'brand',
        'model',
        'brand_normalized',
        'model_normalized',
        'buyer__phone_normalized',
    )
    autocomplete_fields = ('buyer',)
    readonly_fields = (
        'brand_normalized',
        'model_normalized',
        'created_at',
        'updated_at',
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('buyer')


@admin.register(BuyerCategoryInterest)
class BuyerCategoryInterestAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'buyer',
        'category',
        'requests_count',
        'last_seen_at',
    )
    search_fields = ('category', 'category_normalized', 'buyer__phone_normalized')
    autocomplete_fields = ('buyer',)
    readonly_fields = ('category_normalized', 'created_at', 'updated_at')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('buyer')


@admin.register(BuyerCityInterest)
class BuyerCityInterestAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'buyer',
        'city',
        'interest_type',
        'requests_count',
        'last_seen_at',
    )
    list_filter = ('interest_type',)
    search_fields = ('city', 'city_normalized', 'buyer__phone_normalized')
    autocomplete_fields = ('buyer',)
    readonly_fields = ('city_normalized', 'created_at', 'updated_at')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('buyer')


@admin.register(ContactConsent)
class ContactConsentAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'buyer',
        'channel',
        'purpose',
        'status',
        'source',
        'consented_at',
        'revoked_at',
    )
    list_filter = ('channel', 'purpose', 'status', 'source')
    search_fields = (
        'buyer__phone_normalized',
        'consent_text_version',
        'evidence_reference',
    )
    autocomplete_fields = ('buyer',)
    readonly_fields = ('created_at', 'updated_at')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('buyer')


@admin.register(SellerContactConsent)
class SellerContactConsentAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'seller',
        'masked_phone',
        'phone_normalized',
        'channel',
        'purpose',
        'status',
        'source',
        'consent_text_version',
        'consented_at',
        'revoked_at',
        'updated_at',
        'evidence_reference',
    )
    list_filter = ('channel', 'purpose', 'status', 'source')
    search_fields = (
        'phone_normalized',
        'consent_text_version',
        'evidence_reference',
        'seller__name',
        'seller__whatsapp',
    )
    autocomplete_fields = ('seller',)
    readonly_fields = ('created_at', 'updated_at')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('seller')

    @admin.display(description='Телефон')
    def masked_phone(self, obj):
        return mask_phone(obj.phone_normalized)


class SellerContactConsentInline(admin.TabularInline):
    model = SellerContactConsent
    extra = 0
    fields = (
        'phone_normalized',
        'channel',
        'purpose',
        'status',
        'source',
        'consent_text_version',
        'consented_at',
        'revoked_at',
        'evidence_reference',
    )
    readonly_fields = (
        'phone_normalized',
        'channel',
        'purpose',
        'status',
        'source',
        'consent_text_version',
        'consented_at',
        'revoked_at',
        'evidence_reference',
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(WhatsAppInboundEvent)
class WhatsAppInboundEventAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'provider_message_id',
        'phone_normalized',
        'message_type',
        'button_text',
        'action',
        'seller',
        'processing_status',
        'provider_timestamp',
        'created_at',
        'processed_at',
    )
    list_filter = ('processing_status', 'action', 'message_type')
    search_fields = (
        'provider_message_id',
        'phone_normalized',
        'button_text',
        'payload_hash',
        'seller__name',
    )
    readonly_fields = (
        'provider_message_id',
        'phone_normalized',
        'message_type',
        'button_text',
        'action',
        'seller',
        'processing_status',
        'provider_timestamp',
        'payload_hash',
        'created_at',
        'processed_at',
    )
    ordering = ('-created_at', '-id')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('seller')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BuyerAudience)
class BuyerAudienceAdmin(admin.ModelAdmin):
    form = BuyerAudienceAdminForm
    list_display = (
        'name',
        'is_active',
        'criteria_summary',
        'preview_link',
        'updated_at',
    )
    list_filter = ('is_active', 'updated_at')
    search_fields = ('name', 'description')
    readonly_fields = (
        'created_at',
        'updated_at',
        'criteria_readonly_summary',
    )
    fields = (
        'name',
        'description',
        'is_active',
        'countries',
        'cities',
        'transport_types',
        'brands',
        'models',
        'categories',
        'search_scopes',
        'activity_period',
        'request_count_min',
        'request_count_max',
        'criteria_readonly_summary',
        'created_at',
        'updated_at',
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/preview/',
                self.admin_site.admin_view(self.preview_view),
                name='core_buyeraudience_preview',
            ),
        ]
        return custom_urls + urls

    def preview_view(self, request, object_id):
        audience = get_object_or_404(BuyerAudience, pk=object_id)
        preview = preview_buyer_audience(audience)
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'audience': audience,
            'preview': preview,
            'sample_contacts': preview.sample_contacts,
            'criteria_rows': format_criteria_details(audience.criteria),
            'no_criteria': not audience_criteria_has_filters(audience.criteria),
            'calculated_at': timezone.now(),
            'title': f'Предпросмотр: {audience.name}',
        }
        return render(request, 'admin/core/buyeraudience/preview.html', context)

    @admin.display(description='Критерии')
    def criteria_summary(self, obj):
        return format_criteria_summary(obj.criteria)

    @admin.display(description='Критерии')
    def criteria_readonly_summary(self, obj):
        rows = format_criteria_details(obj.criteria)
        return mark_safe('<br>'.join(f'<strong>{label}:</strong> {escape(value)}' for label, value in rows))

    @admin.display(description='Предпросмотр')
    def preview_link(self, obj):
        if not obj.pk:
            return '—'
        url = reverse('admin:core_buyeraudience_preview', args=[obj.pk])
        return mark_safe(f'<a href="{escape(url)}">Предпросмотр</a>')


@admin.register(BuyerBroadcastCampaign)
class BuyerBroadcastCampaignAdmin(admin.ModelAdmin):
    form = BuyerBroadcastCampaignAdminForm
    filter_horizontal = ('test_contacts',)
    list_display = (
        'name',
        'mode',
        'status',
        'template_name',
        'selected_test_contacts_count',
        'queued_recipients_count',
        'sent_count',
        'failed_count',
        'created_at',
        'updated_at',
    )
    list_filter = ('mode', 'status', 'created_at')
    search_fields = ('name', 'description', 'template_name')
    readonly_fields = (
        'created_by',
        'queued_at',
        'started_at',
        'completed_at',
        'created_at',
        'updated_at',
        'test_preview',
        'recipient_statistics',
    )
    fields = (
        'name',
        'description',
        'mode',
        'status',
        'template_name',
        'template_language',
        'template_body_parameters',
        'message_preview',
        'test_contacts',
        'test_preview',
        'recipient_statistics',
        'created_by',
        'queued_at',
        'started_at',
        'completed_at',
        'created_at',
        'updated_at',
    )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/preview/',
                self.admin_site.admin_view(self.preview_view),
                name='core_buyerbroadcastcampaign_preview',
            ),
            path(
                '<path:object_id>/prepare/',
                self.admin_site.admin_view(self.prepare_view),
                name='core_buyerbroadcastcampaign_prepare',
            ),
        ]
        return custom_urls + urls

    def preview_view(self, request, object_id):
        campaign = get_object_or_404(BuyerBroadcastCampaign, pk=object_id)
        preview = preview_test_campaign(campaign)
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'campaign': campaign,
            'preview': preview,
            'broadcast_mode': get_buyer_broadcast_mode(),
            'max_recipients': get_buyer_broadcast_test_max_recipients(),
            'title': f'Предпросмотр: {campaign.name}',
        }
        return render(
            request,
            'admin/core/buyerbroadcastcampaign/preview.html',
            context,
        )

    def prepare_view(self, request, object_id):
        campaign = get_object_or_404(BuyerBroadcastCampaign, pk=object_id)
        preview = preview_test_campaign(campaign)
        if request.method == 'POST' and request.POST.get('confirm') == '1':
            result = prepare_test_campaign(campaign)
            if result.errors:
                for error in result.errors:
                    messages.error(request, error)
            else:
                messages.success(
                    request,
                    (
                        f'Очередь подготовлена: создано {result.created_recipient_count}, '
                        f'допущено {result.eligible_count}.'
                    ),
                )
                return redirect('admin:core_buyerbroadcastcampaign_change', campaign.pk)
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'campaign': campaign,
            'preview': preview,
            'broadcast_mode': get_buyer_broadcast_mode(),
            'title': f'Подготовка очереди: {campaign.name}',
        }
        return render(
            request,
            'admin/core/buyerbroadcastcampaign/prepare_confirm.html',
            context,
        )

    @admin.display(description='Тестовых контактов')
    def selected_test_contacts_count(self, obj):
        return obj.test_contacts.count()

    @admin.display(description='В очереди')
    def queued_recipients_count(self, obj):
        return obj.recipients.filter(status='queued').count()

    @admin.display(description='Отправлено')
    def sent_count(self, obj):
        return obj.recipients.filter(status=BUYER_BROADCAST_RECIPIENT_SENT).count()

    @admin.display(description='Ошибок')
    def failed_count(self, obj):
        return obj.recipients.filter(status=BUYER_BROADCAST_RECIPIENT_FAILED).count()

    @admin.display(description='Предпросмотр')
    def test_preview(self, obj):
        if not obj.pk:
            return '—'
        url = reverse('admin:core_buyerbroadcastcampaign_preview', args=[obj.pk])
        return mark_safe(f'<a href="{escape(url)}">Предпросмотр</a>')

    @admin.display(description='Статистика получателей')
    def recipient_statistics(self, obj):
        if not obj.pk:
            return '—'
        preview = preview_test_campaign(obj)
        return mark_safe(
            f'Выбрано: {preview.selected_count}<br>'
            f'Допущено: {preview.eligible_count}<br>'
            f'Пропущено (согласие): {preview.skipped_consent_count}',
        )


@admin.register(BuyerBroadcastRecipient)
class BuyerBroadcastRecipientAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'campaign',
        'masked_phone_snapshot',
        'buyer_link',
        'status',
        'skip_reason',
        'attempts_count',
        'provider_message_id_short',
        'queued_at',
        'last_attempt_at',
        'sent_at',
    )
    list_filter = ('campaign', 'status', 'skip_reason', 'sent_at')
    search_fields = (
        'campaign__name',
        'buyer__phone_normalized',
        'provider_message_id',
    )
    readonly_fields = (
        'campaign',
        'buyer',
        'phone_snapshot',
        'masked_phone_snapshot',
        'status',
        'skip_reason',
        'provider_message_id',
        'error_message',
        'attempts_count',
        'queued_at',
        'last_attempt_at',
        'sent_at',
        'created_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False

    @admin.display(description='Покупатель')
    def buyer_link(self, obj):
        url = reverse('admin:core_buyercontact_change', args=[obj.buyer_id])
        return mark_safe(
            f'<a href="{escape(url)}">{escape(mask_phone(obj.buyer.phone_normalized))}</a>',
        )

    @admin.display(description='Message ID')
    def provider_message_id_short(self, obj):
        value = obj.provider_message_id or '—'
        if len(value) > 24:
            return f'{value[:24]}…'
        return value


@admin.action(description='Включить получение заявок')
def enable_receive_requests(modeladmin, request, queryset):
    updated = queryset.update(receive_requests=True)
    messages.success(request, f'Получение заявок включено: {updated} продавцов.')


@admin.action(description='Выключить получение заявок')
def disable_receive_requests(modeladmin, request, queryset):
    updated = queryset.update(receive_requests=False)
    messages.warning(request, f'Получение заявок выключено: {updated} продавцов.')


@admin.action(description='Пометить как тестовых продавцов')
def mark_as_test_seller(modeladmin, request, queryset):
    updated = queryset.update(is_test_seller=True)
    messages.success(request, f'Помечено как тестовые продавцы: {updated}.')


@admin.action(description='Снять признак тестовых продавцов')
def unmark_as_test_seller(modeladmin, request, queryset):
    updated = queryset.update(is_test_seller=False)
    messages.warning(request, f'Снят признак тестовых продавцов: {updated}.')


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    change_list_template = 'admin/core/seller/change_list.html'
    inlines = (SellerContactConsentInline,)

    list_display = (
        'id',
        'name',
        'whatsapp',
        'phone2',
        'transport_type',
        'seller_type',
        'dispatch_priority',
        'receive_requests',
        'is_test_seller',
        'city',
        'market_location',
        'is_active',
        'is_paused',
    )

    list_editable = (
        'dispatch_priority',
        'receive_requests',
        'is_test_seller',
    )

    list_filter = (
        'transport_type',
        'seller_type',
        'receive_requests',
        'is_test_seller',
        'is_active',
        'is_paused',
        'all_categories',
        'all_brands',
        'all_models',
        'all_countries',
        'city'
    )

    search_fields = (
        'name',
        'whatsapp',
        'phone2',
        'brand',
        'model',
        'market_location',
        'notes',
    )

    filter_horizontal = (
        'selected_categories',
        'selected_countries',
        'selected_brands',
        'selected_models'
    )

    actions = (
        enable_receive_requests,
        disable_receive_requests,
        mark_as_test_seller,
        unmark_as_test_seller,
    )

    fieldsets = (
        ('Основное', {
            'fields': (
                'name',
                'whatsapp',
                'phone2',
                'seller_type',
                'transport_type',
                'city',
                'market_location',
                'dispatch_priority',
                'is_active',
                'is_paused',
                'receive_requests',
                'is_test_seller',
                'notes',
            )
        }),

        ('Старый одиночный режим', {
            'fields': (
                'category',
                'country_fk',
                'brand_fk',
                'model_fk'
            )
        }),

        ('Новый множественный выбор', {
            'fields': (
                'selected_categories',
                'selected_countries',
                'selected_brands',
                'selected_models'
            )
        }),

        ('Режимы "Все"', {
            'fields': (
                'all_categories',
                'all_countries',
                'all_brands',
                'all_models'
            )
        }),
    )

    def get_urls(self):
        urls = super().get_urls()

        custom_urls = [
            path(
                'import-xlsx/',
                self.admin_site.admin_view(self.import_xlsx_view),
                name='core_seller_import_xlsx'
            ),
        ]

        return custom_urls + urls

    def import_xlsx_view(self, request):
        context = {
            **self.admin_site.each_context(request),
            'title': 'Импорт продавцов XLSX',
            'opts': self.model._meta,
            'form': SellerImportForm(),
            'preview_rows': None,
            'errors': [],
        }

        if request.method == 'POST' and request.POST.get('action') == 'preview':
            form = SellerImportForm(request.POST, request.FILES)

            if form.is_valid():
                rows, errors = parse_sellers_xlsx(request.FILES['file'])

                preview_rows = []

                for row in rows:
                    exists = find_seller_by_whatsapp(row['whatsapp']) is not None
                    preview_rows.append({
                        **row,
                        'status': 'Обновление' if exists else 'Новый',
                    })

                request.session['seller_import_rows'] = rows

                context.update({
                    'form': form,
                    'preview_rows': preview_rows,
                    'errors': errors,
                })

                return render(request, 'admin/core/seller/import_sellers.html', context)

        if request.method == 'POST' and request.POST.get('action') == 'import':
            rows = request.session.get('seller_import_rows', [])

            created_count = 0
            updated_count = 0
            failed_count = 0

            for row in rows:
                try:
                    created = import_seller_row(row)

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                except Exception as exc:
                    failed_count += 1
                    messages.error(
                        request,
                        f"Ошибка строки {row.get('row_number')}: "
                        f"{row.get('seller_name')} — {exc}"
                    )

            request.session.pop('seller_import_rows', None)

            messages.success(
                request,
                f'Импорт завершён. Создано: {created_count}. '
                f'Обновлено: {updated_count}. Ошибок: {failed_count}.'
            )

            return redirect('..')

        return render(request, 'admin/core/seller/import_sellers.html', context)


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'request',
        'seller',
        'status',
        'created_at',
        'sent_at'
    )

    list_filter = ('status',)

    search_fields = (
        'request__phone',
        'seller__name'
    )


class SellerRequestReactionFilter(admin.SimpleListFilter):
    title = 'Реакция продавца'
    parameter_name = 'seller_reaction'

    def lookups(self, request, model_admin):
        return (
            (REACTION_NO_REACTION, 'Без реакции'),
            (REACTION_OPENED, 'Открыл заявку'),
            (REACTION_CONTACT, 'Перешёл к контакту'),
            ('declined', 'Отказ'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == REACTION_NO_REACTION:
            return queryset.filter(
                status=RequestDispatch.STATUS_SENT,
                sr_has_page_open=False,
                sr_has_whatsapp_click=False,
                sr_has_call_click=False,
                sr_has_out_of_stock=False,
                sr_has_cannot_fulfill=False,
            )
        if value == REACTION_OPENED:
            return queryset.filter(
                status=RequestDispatch.STATUS_SENT,
                sr_has_page_open=True,
                sr_has_whatsapp_click=False,
                sr_has_call_click=False,
                sr_has_out_of_stock=False,
                sr_has_cannot_fulfill=False,
            )
        if value == REACTION_CONTACT:
            return queryset.filter(
                status=RequestDispatch.STATUS_SENT,
                sr_has_out_of_stock=False,
                sr_has_cannot_fulfill=False,
            ).filter(
                Q(sr_has_whatsapp_click=True) | Q(sr_has_call_click=True)
            )
        if value == 'declined':
            return queryset.filter(
                status=RequestDispatch.STATUS_SENT,
            ).filter(
                Q(sr_has_out_of_stock=True) | Q(sr_has_cannot_fulfill=True)
            )
        return queryset


@admin.action(description='Остановить выбранные волны')
def pause_dispatches(modeladmin, request, queryset):
    updated = queryset.exclude(status=RequestDispatch.STATUS_SENT).update(
        status=RequestDispatch.STATUS_PAUSED
    )
    messages.warning(request, f'Остановлено волн/отправок: {updated}.')


@admin.action(description='Вернуть выбранные волны в очередь')
def queue_dispatches(modeladmin, request, queryset):
    updated = queryset.exclude(status=RequestDispatch.STATUS_SENT).update(
        status=RequestDispatch.STATUS_QUEUED
    )
    messages.success(request, f'Возвращено в очередь: {updated}.')


@admin.register(RequestDispatch)
class RequestDispatchAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'request',
        'seller',
        'wave_number',
        'position_number',
        'status',
        'seller_reaction',
        'scheduled_at',
        'sent_at',
        'created_at'
    )

    list_filter = (
        SellerRequestReactionFilter,
        'status',
        'wave_number',
        'scheduled_at',
        'sent_at'
    )

    search_fields = (
        'request__phone',
        'request__brand',
        'request__model',
        'seller__name',
        'seller__whatsapp'
    )

    readonly_fields = (
        'created_at',
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('request', 'seller')
        events = SellerRequestPageEvent.objects.filter(
            request_id=OuterRef('request_id'),
            seller_id=OuterRef('seller_id'),
        )
        return queryset.annotate(
            sr_has_page_open=Exists(
                events.filter(event_type=SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN)
            ),
            sr_has_whatsapp_click=Exists(
                events.filter(event_type=SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK)
            ),
            sr_has_call_click=Exists(
                events.filter(event_type=SELLER_REQUEST_PAGE_EVENT_CALL_CLICK)
            ),
            sr_has_out_of_stock=Exists(
                events.filter(event_type=SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK)
            ),
            sr_has_cannot_fulfill=Exists(
                events.filter(event_type=SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL)
            ),
        )

    @admin.display(description='Реакция продавца')
    def seller_reaction(self, obj):
        event_types = set()
        if getattr(obj, 'sr_has_page_open', False):
            event_types.add(SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN)
        if getattr(obj, 'sr_has_whatsapp_click', False):
            event_types.add(SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK)
        if getattr(obj, 'sr_has_call_click', False):
            event_types.add(SELLER_REQUEST_PAGE_EVENT_CALL_CLICK)
        if getattr(obj, 'sr_has_out_of_stock', False):
            event_types.add(SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK)
        if getattr(obj, 'sr_has_cannot_fulfill', False):
            event_types.add(SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL)
        return classify_seller_request_reaction(
            dispatch_status=obj.status,
            event_types=event_types,
        ).label

    actions = (
        pause_dispatches,
        queue_dispatches,
    )


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'phone', 'created_at')
    search_fields = ('name', 'phone', 'message')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)


@admin.register(WhatsAppMessageLog)
class WhatsAppMessageLogAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'created_at',
        'seller_name',
        'phone_clean',
        'is_success',
        'status_text',
        'message_id',
    )
    search_fields = ('seller_name', 'phone_clean', 'message_id')
    list_filter = ('is_success', 'status_text')


@admin.action(description='Одобрить выбранные публикации')
def approve_instagram_publications(modeladmin, request, queryset):
    updated = 0
    for publication in queryset:
        approve_instagram_publication(publication)
        updated += 1
    modeladmin.message_user(request, f'Одобрено публикаций: {updated}')


@admin.action(description='Опубликовать выбранные карточки')
def publish_instagram_publications(modeladmin, request, queryset):
    queued = 0
    skipped = 0
    for publication in queryset:
        if publication.status == InstagramPublication.STATUS_PUBLISHED:
            skipped += 1
            continue
        queue_instagram_publication_for_processing(publication)
        queued += 1
    modeladmin.message_user(
        request,
        f'Публикация поставлена в очередь: {queued}. Пропущено (уже опубликовано): {skipped}.',
    )


@admin.action(description='Повторить публикацию для выбранных')
def retry_instagram_publications(modeladmin, request, queryset):
    queued = 0
    for publication in queryset.exclude(status=InstagramPublication.STATUS_PUBLISHED):
        queue_instagram_publication_for_processing(publication)
        queued += 1
    modeladmin.message_user(request, f'Публикация поставлена в очередь: {queued}.')


@admin.action(description='Пометить зависшие публикации как ошибку (>5 мин)')
def mark_stuck_instagram_publications_failed(modeladmin, request, queryset):
    updated = 0
    skipped = 0
    for publication in queryset.filter(status=InstagramPublication.STATUS_PUBLISHING):
        before = publication.status
        mark_stuck_instagram_publication_failed(publication)
        publication.refresh_from_db()
        if publication.status == InstagramPublication.STATUS_FAILED and before != publication.status:
            updated += 1
        else:
            skipped += 1
    modeladmin.message_user(
        request,
        f'Помечено как ошибка: {updated}. Пропущено (ещё не зависли): {skipped}.',
    )


@admin.action(description='Отменить выбранные публикации')
def cancel_instagram_publications(modeladmin, request, queryset):
    cancelled = 0
    for publication in queryset:
        if publication.status != InstagramPublication.STATUS_PUBLISHED:
            cancel_instagram_publication(publication)
            cancelled += 1
    modeladmin.message_user(request, f'Отменено публикаций: {cancelled}')


@admin.register(InstagramPublication)
class InstagramPublicationAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'request',
        'placement',
        'status',
        'review_reason',
        'image_preview_list',
        'created_at',
        'published_at',
    )
    list_filter = ('placement', 'status', 'review_reason', 'created_at', 'published_at')
    search_fields = (
        'request__id',
        'request__brand',
        'request__model',
        'caption',
        'instagram_media_id',
        'error_message',
    )
    readonly_fields = (
        'request',
        'placement',
        'image',
        'image_preview',
        'caption',
        'instagram_container_id',
        'instagram_media_id',
        'created_at',
        'publishing_started_at',
        'published_at',
        'retry_count',
        'last_attempt_at',
        'next_attempt_at',
        'review_reason',
        'review_detail',
        'public_payload',
        'approved_at',
    )
    fields = (
        'request',
        'placement',
        'status',
        'review_reason',
        'review_detail',
        'public_payload',
        'approved_at',
        'image_preview',
        'image',
        'caption',
        'instagram_container_id',
        'instagram_media_id',
        'error_message',
        'retry_count',
        'last_attempt_at',
        'next_attempt_at',
        'created_at',
        'publishing_started_at',
        'published_at',
    )
    actions = (
        approve_instagram_publications,
        publish_instagram_publications,
        retry_instagram_publications,
        mark_stuck_instagram_publications_failed,
        cancel_instagram_publications,
    )

    @admin.display(description='Превью')
    def image_preview_list(self, obj):
        return self._render_preview(obj, max_height=48)

    @admin.display(description='Превью карточки')
    def image_preview(self, obj):
        return self._render_preview(obj, max_height=320)

    def _render_preview(self, obj, *, max_height: int):
        from django.utils.html import format_html

        if not obj.image:
            return '—'
        return format_html(
            '<img src="{}" alt="Instagram story preview" '
            'style="max-height:{}px;border-radius:8px;border:1px solid #e5e7eb;" />',
            obj.image.url,
            max_height,
        )


def _seller_lead_external_link(url: str, label: str):
    from django.utils.html import format_html

    if not url:
        return '—'
    return format_html(
        '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
        url,
        label,
    )


@admin.action(description='Пометить как проверенные')
def mark_seller_leads_verified(modeladmin, request, queryset):
    from django.utils import timezone

    now = timezone.now()
    updated = 0
    for lead in queryset:
        lead.status = SellerLead.STATUS_VERIFIED
        if not lead.checked_at:
            lead.checked_at = now
        lead.save(update_fields=['status', 'checked_at', 'updated_at'])
        updated += 1
    messages.success(request, f'Помечено как проверенные: {updated}.')


@admin.action(description='Пометить как дубликаты')
def mark_seller_leads_duplicate(modeladmin, request, queryset):
    updated = queryset.update(status=SellerLead.STATUS_DUPLICATE)
    messages.warning(request, f'Помечено как дубликаты: {updated}.')


@admin.action(description='Пометить как не продавцов')
def mark_seller_leads_not_seller(modeladmin, request, queryset):
    updated = queryset.update(status=SellerLead.STATUS_NOT_SELLER)
    messages.warning(request, f'Помечено как не продавцы: {updated}.')


@admin.action(description='Пометить как «Нет WhatsApp»')
def mark_seller_leads_no_whatsapp(modeladmin, request, queryset):
    updated = queryset.update(status=SellerLead.STATUS_NO_WHATSAPP)
    messages.warning(request, f'Помечено как «Нет WhatsApp»: {updated}.')


@admin.action(description='Пометить как «Написали»')
def mark_seller_leads_contacted(modeladmin, request, queryset):
    updated = queryset.update(status=SellerLead.STATUS_CONTACTED)
    messages.success(request, f'Помечено как «Написали»: {updated}.')


def _apply_workflow_action(modeladmin, request, queryset, action_callable):
    from core.services.seller_lead_admin_workflow import WorkflowResultKind

    success_count = 0
    warning_count = 0
    error_count = 0

    for lead in queryset:
        lead.refresh_from_db()
        result = action_callable(lead)
        if result.kind == WorkflowResultKind.SUCCESS:
            success_count += 1
            messages.success(request, result.message)
        elif result.kind == WorkflowResultKind.WARNING:
            warning_count += 1
            messages.warning(request, result.message)
        else:
            error_count += 1
            messages.error(request, result.message)

    if success_count:
        messages.success(request, f'Успешно обработано: {success_count}.')
    if warning_count:
        messages.warning(request, f'С предупреждениями: {warning_count}.')
    if error_count:
        messages.error(request, f'С ошибками: {error_count}.')


@admin.action(description='Подключить к активным продавцам заявок')
def convert_seller_leads_to_request_sellers(modeladmin, request, queryset):
    from core.services.seller_lead_admin_workflow import convert_lead_to_request_seller

    _apply_workflow_action(modeladmin, request, queryset, convert_lead_to_request_seller)


@admin.action(description='Отметить для приглашения в маркетплейс')
def mark_seller_leads_marketplace_planned(modeladmin, request, queryset):
    from core.services.seller_lead_admin_workflow import mark_marketplace_invitation_planned

    _apply_workflow_action(modeladmin, request, queryset, mark_marketplace_invitation_planned)


@admin.action(description='Добавить в продавцы заявок + отметить для маркетплейса')
def convert_seller_leads_to_both(modeladmin, request, queryset):
    from core.services.seller_lead_admin_workflow import convert_lead_and_mark_marketplace_planned

    _apply_workflow_action(modeladmin, request, queryset, convert_lead_and_mark_marketplace_planned)


@admin.action(description='Отклонить')
def reject_seller_leads(modeladmin, request, queryset):
    from core.services.seller_lead_admin_workflow import reject_lead

    _apply_workflow_action(modeladmin, request, queryset, reject_lead)


@admin.action(description='Вернуть на проверку')
def return_seller_leads_to_review(modeladmin, request, queryset):
    from core.services.seller_lead_admin_workflow import return_lead_to_review

    _apply_workflow_action(modeladmin, request, queryset, return_lead_to_review)


class SellerLeadHasWhatsAppFilter(admin.SimpleListFilter):
    title = 'Наличие WhatsApp'
    parameter_name = 'has_whatsapp'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Есть WhatsApp'),
            ('no', 'Нет WhatsApp'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(whatsapp='')
        if self.value() == 'no':
            return queryset.filter(whatsapp='')
        return queryset


class SellerLeadWhatsAppStateFilter(admin.SimpleListFilter):
    title = 'WhatsApp state'
    parameter_name = 'whatsapp_state'

    def lookups(self, request, model_admin):
        return WHATSAPP_STATE_CHOICES

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return filter_seller_leads_by_whatsapp_state(queryset, value)


class SellerLeadDiscoveredBrandFilter(admin.SimpleListFilter):
    title = 'Найденная марка'
    parameter_name = 'discovered_brand'

    def lookups(self, request, model_admin):
        return list(
            Brand.objects.filter(discovered_seller_leads__isnull=False)
            .distinct()
            .order_by('name')
            .values_list('pk', 'name')[:100]
        )

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(discovered_brands__pk=value).distinct()


class SellerLeadDiscoveredModelFilter(admin.SimpleListFilter):
    title = 'Найденная модель'
    parameter_name = 'discovered_model'

    def lookups(self, request, model_admin):
        return list(
            CarModel.objects.filter(discovered_seller_leads__isnull=False)
            .distinct()
            .order_by('name')
            .values_list('pk', 'name')[:100]
        )

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(discovered_models__pk=value).distinct()


class SellerLeadDiscoveredCategoryFilter(admin.SimpleListFilter):
    title = 'Найденная категория'
    parameter_name = 'discovered_category'

    def lookups(self, request, model_admin):
        return list(
            PartCategory.objects.filter(discovered_seller_leads__isnull=False)
            .distinct()
            .order_by('name')
            .values_list('pk', 'name')[:100]
        )

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(discovered_categories__pk=value).distinct()


class SellerLeadSourceProviderFilter(admin.SimpleListFilter):
    title = 'Провайдер источника'
    parameter_name = 'source_provider'

    def lookups(self, request, model_admin):
        return (
            ('two_gis', '2GIS'),
            ('brave', 'Brave'),
            ('website', 'Сайт'),
            ('google_places', 'Google Places'),
            ('yandex_org', 'Яндекс'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(sources__provider=value).distinct()


class SellerLeadHasRequestSellerFilter(admin.SimpleListFilter):
    title = 'Связь с продавцом заявок'
    parameter_name = 'has_request_seller'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Связан'),
            ('no', 'Не связан'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(request_seller__isnull=False)
        if self.value() == 'no':
            return queryset.filter(request_seller__isnull=True)
        return queryset


class SellerLeadMarketplacePlannedFilter(admin.SimpleListFilter):
    title = 'Приглашение в маркетплейс'
    parameter_name = 'marketplace_planned'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Запланировано'),
            ('no', 'Не запланировано'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(
                marketplace_invitation_status=SellerLead.MARKETPLACE_INVITATION_PLANNED,
            )
        if self.value() == 'no':
            return queryset.exclude(
                marketplace_invitation_status=SellerLead.MARKETPLACE_INVITATION_PLANNED,
            )
        return queryset


class SellerLeadDiscoverySourceTypeFilter(admin.SimpleListFilter):
    title = 'Источник discovery'
    parameter_name = 'discovery_source_type'

    def lookups(self, request, model_admin):
        return SELLER_LEAD_DISCOVERY_SOURCE_CHOICES

    def queryset(self, request, queryset):
        value = self.value()
        if not value:
            return queryset
        return queryset.filter(sources__source_type=value).distinct()


class SellerLeadConfidenceFilter(admin.SimpleListFilter):
    title = 'Общая уверенность'
    parameter_name = 'overall_confidence_band'

    def lookups(self, request, model_admin):
        return (
            ('none', 'Не указана'),
            ('low', '0–49'),
            ('mid', '50–79'),
            ('high', '80–100'),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == 'none':
            return queryset.filter(overall_confidence__isnull=True)
        if value == 'low':
            return queryset.filter(overall_confidence__gte=0, overall_confidence__lt=50)
        if value == 'mid':
            return queryset.filter(overall_confidence__gte=50, overall_confidence__lt=80)
        if value == 'high':
            return queryset.filter(overall_confidence__gte=80, overall_confidence__lte=100)
        return queryset


class _ClassificationLinkInline(admin.TabularInline):
    extra = 0
    can_delete = False
    readonly_fields = (
        'confidence',
        'source',
        'evidence',
        'source_kind',
        'source_text',
        'source_link',
        'observed_at',
    )

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description='Ссылка источника')
    def source_link(self, obj):
        source = getattr(obj, 'source', None)
        if source is None:
            return '—'
        provider = source.provider or source.get_source_type_display()
        if source.source_url:
            return format_html(
                '{} · <a href="{}" target="_blank" rel="noopener noreferrer">ссылка</a>',
                provider,
                source.source_url,
            )
        return provider


class SellerLeadDiscoveredBrandInline(_ClassificationLinkInline):
    model = SellerLeadDiscoveredBrand
    readonly_fields = ('brand',) + _ClassificationLinkInline.readonly_fields
    fields = (
        'brand',
        'confidence',
        'source',
        'evidence',
        'source_kind',
        'source_text',
        'source_link',
        'observed_at',
    )


class SellerLeadDiscoveredModelInline(_ClassificationLinkInline):
    model = SellerLeadDiscoveredModel
    readonly_fields = ('car_model',) + _ClassificationLinkInline.readonly_fields
    fields = (
        'car_model',
        'confidence',
        'source',
        'evidence',
        'source_kind',
        'source_text',
        'source_link',
        'observed_at',
    )


class SellerLeadDiscoveredCategoryInline(_ClassificationLinkInline):
    model = SellerLeadDiscoveredCategory
    readonly_fields = ('category',) + _ClassificationLinkInline.readonly_fields
    fields = (
        'category',
        'confidence',
        'source',
        'evidence',
        'source_kind',
        'source_text',
        'source_link',
        'observed_at',
    )


class SellerLeadSourceInline(admin.TabularInline):
    model = SellerLeadSource
    extra = 0
    fields = (
        'source_type',
        'provider',
        'external_id',
        'source_url',
        'display_name',
        'source_confidence',
        'is_active',
        'first_seen_at',
        'last_seen_at',
        'fetched_at',
    )
    show_change_link = True


class SellerLeadEvidenceInline(admin.TabularInline):
    model = SellerLeadEvidence
    extra = 0
    raw_id_fields = ('source',)
    fields = (
        'field_name',
        'value',
        'normalized_value',
        'confidence',
        'extraction_method',
        'source',
        'is_selected',
        'is_owner_verified',
        'observed_at',
    )
    show_change_link = True


class SellerLeadLocationInline(admin.TabularInline):
    model = SellerLeadLocation
    extra = 0
    raw_id_fields = ('source',)
    fields = (
        'city',
        'address',
        'normalized_address',
        'is_primary',
        'confidence',
        'latitude',
        'longitude',
        'source',
        'first_seen_at',
        'last_seen_at',
    )
    show_change_link = True


class SellerLeadContactCandidateInline(admin.TabularInline):
    model = SellerLeadContactCandidate
    extra = 0
    fields = (
        'value',
        'contact_type',
        'role',
        'label',
        'confidence',
        'status',
        'is_primary',
        'source_type',
        'source_url',
        'found_at',
        'reviewed_at',
    )
    readonly_fields = ('found_at', 'reviewed_at')
    show_change_link = True


@admin.action(description='Подтвердить кандидата как основной контакт')
def approve_contact_candidates_as_primary(modeladmin, request, queryset):
    lead_ids = set(queryset.values_list('seller_lead_id', flat=True))
    if len(lead_ids) > 1:
        messages.error(
            request,
            'Нельзя подтвердить кандидатов из разных SellerLead одновременно. Выберите кандидатов одного лида.',
        )
        return
    if queryset.count() > 1:
        messages.error(
            request,
            'Нельзя подтвердить несколько кандидатов одновременно. Выберите одного кандидата для основного контакта.',
        )
        return
    candidate = queryset.first()
    if candidate is None:
        return
    try:
        candidate.approve_as_primary()
    except ValueError as exc:
        messages.error(request, str(exc))
        return
    messages.success(request, f'Основной контакт подтверждён: {candidate.value}.')


@admin.action(description='Пометить кандидата как отклонённый')
def reject_contact_candidates(modeladmin, request, queryset):
    updated = queryset.update(
        status=SellerLeadContactCandidate.STATUS_REJECTED,
        is_primary=False,
        reviewed_at=timezone.now(),
    )
    messages.warning(request, f'Отклонено кандидатов: {updated}.')


@admin.action(description='Пометить кандидата как конфликтующий')
def mark_contact_candidates_conflict(modeladmin, request, queryset):
    updated = queryset.update(
        status=SellerLeadContactCandidate.STATUS_CONFLICT,
        is_primary=False,
        reviewed_at=timezone.now(),
    )
    messages.warning(request, f'Помечено как конфликт: {updated}.')


@admin.action(description='Вернуть кандидата в ожидание проверки')
def mark_contact_candidates_pending(modeladmin, request, queryset):
    updated = queryset.update(
        status=SellerLeadContactCandidate.STATUS_PENDING,
        is_primary=False,
        reviewed_at=None,
    )
    messages.info(request, f'Возвращено в ожидание проверки: {updated}.')


@admin.register(SellerLeadContactCandidate)
class SellerLeadContactCandidateAdmin(admin.ModelAdmin):
    list_display = (
        'seller_lead',
        'value',
        'contact_type',
        'role',
        'label',
        'confidence',
        'status',
        'is_primary',
        'source_type',
        'found_at',
        'reviewed_at',
    )
    list_filter = (
        'contact_type',
        'role',
        'confidence',
        'status',
        'is_primary',
        'source_type',
        'found_at',
    )
    search_fields = (
        'seller_lead__name',
        'seller_lead__instagram_username',
        'value',
        'label',
        'notes',
    )
    readonly_fields = (
        'created_at',
        'updated_at',
        'whatsapp_link',
        'source_link',
    )
    actions = (
        approve_contact_candidates_as_primary,
        reject_contact_candidates,
        mark_contact_candidates_conflict,
        mark_contact_candidates_pending,
    )
    fieldsets = (
        ('Контакт', {
            'fields': (
                'seller_lead',
                'contact_type',
                'value',
                'role',
                'label',
                'confidence',
                'status',
                'is_primary',
                'whatsapp_link',
            ),
        }),
        ('Источник', {
            'fields': (
                'source_type',
                'source_url',
                'source_link',
                'source_text',
                'found_at',
                'reviewed_at',
            ),
        }),
        ('Служебное', {
            'fields': (
                'notes',
                'created_at',
                'updated_at',
            ),
        }),
    )

    @admin.display(description='WhatsApp')
    def whatsapp_link(self, obj):
        return _seller_lead_external_link(
            obj.get_whatsapp_url(),
            'Открыть WhatsApp',
        )

    @admin.display(description='Источник')
    def source_link(self, obj):
        return _seller_lead_external_link(obj.source_url, 'Открыть источник')


@admin.action(description='Пересчитать нормализованные identity')
def refresh_seller_lead_identities(modeladmin, request, queryset):
    from core.services.seller_discovery_identity import refresh_seller_lead_identity

    updated = 0
    for lead in queryset:
        refresh_seller_lead_identity(lead)
        updated += 1
    messages.success(request, f'Identity пересчитан: {updated}.')


@admin.action(description='Найти возможные дубли')
def find_seller_lead_duplicates(modeladmin, request, queryset):
    from core.services.seller_discovery_dedup import find_possible_duplicates_for_leads

    matches = find_possible_duplicates_for_leads(queryset)
    messages.success(request, f'Проверены возможные дубли. Пар в результате: {len(matches)}.')


@admin.action(description='Отметить готовность к приглашению')
def mark_seller_leads_ready_to_invite(modeladmin, request, queryset):
    updated = queryset.update(lifecycle_status=SellerLead.LIFECYCLE_READY_TO_INVITE)
    messages.success(request, f'Готовы к приглашению: {updated}. Сообщения не отправлялись.')


@admin.action(description='Отметить как приглашённых в маркетплейс')
def mark_seller_leads_invited(modeladmin, request, queryset):
    invited = 0
    skipped = 0
    for lead in queryset:
        if mark_seller_lead_invited(lead):
            invited += 1
        else:
            skipped += 1
    if invited:
        messages.success(request, f'Отмечены как приглашённые: {invited}.')
    if skipped:
        messages.warning(
            request,
            (
                f'Пропущено: {skipped}. Действие доступно только для '
                'ready_to_invite с корректным WhatsApp.'
            ),
        )


@admin.action(description='Отметить lifecycle как отклонённый')
def mark_seller_leads_lifecycle_rejected(modeladmin, request, queryset):
    updated = queryset.update(lifecycle_status=SellerLead.LIFECYCLE_REJECTED)
    messages.warning(request, f'Lifecycle «Отклонён»: {updated}. Старый статус pipeline не изменён.')


@admin.action(description='Подтвердить: A является дублем B')
def confirm_card_a_is_duplicate_of_card_b(modeladmin, request, queryset):
    _confirm_duplicate_direction(request, queryset, canonical_side='b')


@admin.action(description='Подтвердить: B является дублем A')
def confirm_card_b_is_duplicate_of_card_a(modeladmin, request, queryset):
    _confirm_duplicate_direction(request, queryset, canonical_side='a')


def _confirm_duplicate_direction(request, queryset, *, canonical_side: str):
    from core.services.seller_discovery_dedup import (
        SellerDiscoveryDedupError,
        confirm_seller_lead_duplicate,
    )

    confirmed = 0
    for match in queryset:
        canonical = match.lead_b if canonical_side == 'b' else match.lead_a
        try:
            confirm_seller_lead_duplicate(
                match,
                canonical_lead=canonical,
                resolved_by=request.user,
            )
        except SellerDiscoveryDedupError as exc:
            messages.error(request, str(exc))
            continue
        confirmed += 1
    messages.success(
        request,
        f'Подтверждено дублей: {confirmed}. Карточки не удалялись и данные не переносились.',
    )


@admin.action(description='Отклонить пару дублей')
def reject_seller_lead_duplicate_matches(modeladmin, request, queryset):
    from core.services.seller_discovery_dedup import reject_seller_lead_duplicate

    rejected = 0
    for match in queryset:
        reject_seller_lead_duplicate(match, resolved_by=request.user)
        rejected += 1
    messages.info(
        request,
        f'Отклонено пар: {rejected}. Lifecycle карточек не изменялся.',
    )


@admin.register(SellerLead)
class SellerLeadAdmin(admin.ModelAdmin):
    inlines = (
        SellerLeadSourceInline,
        SellerLeadEvidenceInline,
        SellerLeadLocationInline,
        SellerLeadContactCandidateInline,
        SellerLeadDiscoveredBrandInline,
        SellerLeadDiscoveredModelInline,
        SellerLeadDiscoveredCategoryInline,
    )
    list_display = (
        'lead_id',
        'name',
        'city',
        'business_type',
        'brands_summary',
        'categories_summary',
        'lifecycle_status',
        'review_status',
        'overall_confidence',
        'whatsapp',
        'marketplace_invite_link',
        'website_url',
        'instagram_username',
        'sources_count',
        'possible_duplicates_count',
        'collected_at',
        'last_seen_at',
        'checked_at',
    )
    list_filter = (
        'business_type',
        'lifecycle_status',
        'city',
        SellerLeadWhatsAppStateFilter,
        SellerLeadConfidenceFilter,
        SellerLeadDiscoveredBrandFilter,
        SellerLeadDiscoveredModelFilter,
        SellerLeadDiscoveredCategoryFilter,
        SellerLeadSourceProviderFilter,
        SellerLeadDiscoverySourceTypeFilter,
        'status',
        'collected_at',
        'checked_at',
        SellerLeadHasWhatsAppFilter,
        SellerLeadMarketplacePlannedFilter,
        SellerLeadHasRequestSellerFilter,
        'review_status',
        'source_type',
        'category',
    )
    search_fields = (
        'name',
        'whatsapp',
        'instagram_username',
        'website_url',
        'normalized_name',
        'normalized_phone',
        'normalized_domain',
        'normalized_instagram',
        'city',
        'notes',
    )
    readonly_fields = (
        'lead_id',
        'business_type',
        'business_type_confidence',
        'business_type_evidence',
        'business_type_source',
        'business_type_evidence_item',
        'business_type_source_link',
        'market_scope',
        'market_scope_evidence',
        'next_enrichment_at',
        'enrichment_attempt_count',
        'last_enrichment_result',
        'whatsapp_state_display',
        'created_at',
        'updated_at',
        'normalized_name',
        'normalized_phone',
        'normalized_domain',
        'normalized_instagram',
        'normalized_address',
        'instagram_profile_link',
        'whatsapp_link',
        'marketplace_invite_link',
        'website_link',
        'source_link',
        'possible_duplicates_display',
        'duplicate_of',
    )
    actions = (
        refresh_seller_lead_identities,
        find_seller_lead_duplicates,
        mark_seller_leads_lifecycle_rejected,
        convert_seller_leads_to_request_sellers,
        mark_seller_leads_marketplace_planned,
        convert_seller_leads_to_both,
        reject_seller_leads,
        return_seller_leads_to_review,
        mark_seller_leads_verified,
        mark_seller_leads_duplicate,
        mark_seller_leads_not_seller,
        mark_seller_leads_no_whatsapp,
        mark_seller_leads_contacted,
    )
    fieldsets = (
        ('Основная карточка', {
            'fields': (
                'lead_id',
                'name',
                'city',
                'category',
                'car_brands',
                'business_type',
                'business_type_confidence',
                'business_type_evidence',
                'business_type_source',
                'business_type_evidence_item',
                'business_type_source_link',
                'market_scope',
                'market_scope_evidence',
                'next_enrichment_at',
                'enrichment_attempt_count',
                'last_enrichment_result',
                'profile_description',
                'overall_confidence',
                'collected_at',
                'last_seen_at',
                'checked_at',
            ),
        }),
        ('Lifecycle', {
            'fields': (
                'lifecycle_status',
                'duplicate_of',
                'last_enriched_at',
                'last_classified_at',
            ),
        }),
        ('Контакты', {
            'fields': (
                'instagram_username',
                'instagram_url',
                'instagram_profile_link',
                'normalized_instagram',
                'whatsapp',
                'whatsapp_state_display',
                'whatsapp_source_url',
                'whatsapp_source_text',
                'whatsapp_confidence',
                'whatsapp_found_at',
                'whatsapp_link',
                'marketplace_invite_link',
                'normalized_phone',
                'website_url',
                'website_link',
                'normalized_domain',
                'normalized_name',
                'normalized_address',
            ),
        }),
        ('Источники', {
            'fields': (
                'source_type',
                'source_url',
                'source_link',
            ),
        }),
        ('Возможные дубли', {
            'fields': ('possible_duplicates_display',),
        }),
        ('Legacy workflow', {
            'fields': (
                'status',
                'review_status',
                'request_seller_transport_type',
                'request_seller',
                'marketplace_invitation_status',
                'marketplace_invitation_planned_at',
                'reviewed_at',
                'rejected_at',
            ),
        }),
        ('Notes', {
            'fields': (
                'notes',
                'created_at',
                'updated_at',
            ),
        }),
    )

    @admin.display(description='Марки')
    def brands_summary(self, obj):
        names = [brand.name for brand in obj.discovered_brands.all()]
        if not names:
            return '—'
        shown = ', '.join(names[:3])
        if len(names) > 3:
            return f'{shown} +{len(names) - 3}'
        return shown

    @admin.display(description='Категории')
    def categories_summary(self, obj):
        names = [category.name for category in obj.discovered_categories.all()]
        if not names:
            return '—'
        shown = ', '.join(names[:3])
        if len(names) > 3:
            return f'{shown} +{len(names) - 3}'
        return shown

    @admin.display(description='Источник классификации')
    def business_type_source_link(self, obj):
        source = obj.business_type_source
        if source is None:
            return '—'
        provider = source.provider or source.get_source_type_display()
        if source.source_url:
            return format_html(
                '{} · <a href="{}" target="_blank" rel="noopener noreferrer">ссылка</a>',
                provider,
                source.source_url,
            )
        return provider

    @admin.display(description='WhatsApp state')
    def whatsapp_state_display(self, obj):
        labels = dict(WHATSAPP_STATE_CHOICES)
        if hasattr(obj, 'has_whatsapp_conflict'):
            state = whatsapp_state_from_annotations(obj)
        else:
            state = seller_lead_whatsapp_state(obj)
        return labels.get(state, state)

    def get_queryset(self, request):
        queryset = (
            super()
            .get_queryset(request)
            .select_related('request_seller', 'duplicate_of')
            .prefetch_related('discovered_brands', 'discovered_categories')
            .annotate(
                sources_count=Count('sources', distinct=True),
                possible_duplicates_as_a=Count(
                    'duplicate_matches_as_a',
                    filter=Q(
                        duplicate_matches_as_a__status=SellerLeadDuplicateMatch.STATUS_POSSIBLE,
                    ),
                    distinct=True,
                ),
                possible_duplicates_as_b=Count(
                    'duplicate_matches_as_b',
                    filter=Q(
                        duplicate_matches_as_b__status=SellerLeadDuplicateMatch.STATUS_POSSIBLE,
                    ),
                    distinct=True,
                ),
            )
            .prefetch_related(
                'sources',
                'evidences',
                'locations',
                'contact_candidates',
            )
        )
        return annotate_seller_leads_with_whatsapp_state(queryset)

    @admin.display(description='ID', ordering='pk')
    def lead_id(self, obj):
        return obj.pk

    @admin.display(description='Источники', ordering='sources_count')
    def sources_count(self, obj):
        return obj.sources_count

    @admin.display(description='Возможные дубли')
    def possible_duplicates_count(self, obj):
        return obj.possible_duplicates_as_a + obj.possible_duplicates_as_b

    @admin.display(description='Instagram')
    def instagram_profile_link(self, obj):
        return _seller_lead_external_link(
            obj.get_instagram_profile_url(),
            'Открыть Instagram',
        )

    @admin.display(description='WhatsApp')
    def whatsapp_link(self, obj):
        return _seller_lead_external_link(
            obj.get_whatsapp_url(),
            'Открыть WhatsApp',
        )

    @admin.display(description='Приглашение')
    def marketplace_invite_link(self, obj):
        if not obj or not obj.pk:
            return '—'
        url = build_marketplace_invite_whatsapp_url(obj)
        if not url:
            return 'Нет WhatsApp'
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">Открыть приглашение</a>',
            url,
        )

    @admin.display(description='Сайт')
    def website_link(self, obj):
        return _seller_lead_external_link(obj.website_url, 'Открыть сайт')

    @admin.display(description='Источник')
    def source_link(self, obj):
        return _seller_lead_external_link(obj.source_url, 'Открыть источник')

    @admin.display(description='Пары')
    def possible_duplicates_display(self, obj):
        if not obj or not obj.pk:
            return '—'
        matches = SellerLeadDuplicateMatch.objects.filter(
            Q(lead_a=obj) | Q(lead_b=obj),
        ).select_related('lead_a', 'lead_b')
        if not matches:
            return '—'
        lines = []
        for match in matches:
            other = match.lead_b if match.lead_a_id == obj.pk else match.lead_a
            url = reverse('admin:core_sellerlead_change', args=[other.pk])
            lines.append(format_html(
                '<div><a href="{}">#{} {}</a> — {} — {}</div>',
                url,
                other.pk,
                other.name,
                match.score,
                match.get_status_display(),
            ))
        return mark_safe(''.join(str(line) for line in lines))


@admin.register(SellerLeadDuplicateMatch)
class SellerLeadDuplicateMatchAdmin(admin.ModelAdmin):
    list_display = (
        'lead_a',
        'lead_b',
        'score',
        'status',
        'resolved_at',
        'resolved_by',
        'updated_at',
    )
    list_filter = ('status', 'score')
    search_fields = (
        'lead_a__name',
        'lead_b__name',
        'lead_a__whatsapp',
        'lead_b__whatsapp',
        'lead_a__normalized_phone',
        'lead_b__normalized_phone',
    )
    readonly_fields = (
        'lead_a',
        'lead_b',
        'score',
        'status',
        'reasons',
        'resolved_at',
        'resolved_by',
        'created_at',
        'updated_at',
    )
    actions = (
        confirm_card_a_is_duplicate_of_card_b,
        confirm_card_b_is_duplicate_of_card_a,
        reject_seller_lead_duplicate_matches,
    )

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'lead_a',
            'lead_b',
            'resolved_by',
        )


def _format_json_for_admin(value) -> str:
    import json

    if not value:
        return '—'
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError:
        rendered = str(value)
    return mark_safe(f'<pre style="white-space:pre-wrap;">{escape(rendered)}</pre>')


@admin.register(SellerLeadPipelineRun)
class SellerLeadPipelineRunAdmin(admin.ModelAdmin):
    list_display = (
        'started_at',
        'trigger',
        'status',
        'city',
        'search_term',
        'category',
        'lead_limit',
        'discovery_new_profiles',
        'enrichment_saved_contacts',
        'enrichment_conflicts',
    )
    list_filter = ('status', 'trigger', 'city', 'category', 'rotation_enabled')
    search_fields = ('run_uuid',)
    ordering = ('-started_at',)
    readonly_fields = (
        'run_uuid',
        'trigger',
        'status',
        'is_dry_run',
        'city',
        'category',
        'search_term',
        'rotation_enabled',
        'rotation_slug',
        'rotation_index',
        'search_limit',
        'lead_limit',
        'max_queries_per_lead',
        'skip_discovery',
        'skip_enrichment',
        'cooldown_minutes',
        'force_run',
        'started_at',
        'finished_at',
        'discovery_stats_display',
        'enrichment_stats_display',
        'created_lead_ids_display',
        'error_message',
        'skip_reason',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Новых профилей')
    def discovery_new_profiles(self, obj):
        return int((obj.discovery_stats or {}).get('new_profiles', 0))

    @admin.display(description='Сохранено WhatsApp')
    def enrichment_saved_contacts(self, obj):
        return int((obj.enrichment_stats or {}).get('saved_primary_whatsapp', 0))

    @admin.display(description='Конфликтов')
    def enrichment_conflicts(self, obj):
        return int((obj.enrichment_stats or {}).get('conflicts', 0))

    @admin.display(description='Discovery stats')
    def discovery_stats_display(self, obj):
        return _format_json_for_admin(obj.discovery_stats)

    @admin.display(description='Enrichment stats')
    def enrichment_stats_display(self, obj):
        return _format_json_for_admin(obj.enrichment_stats)

    @admin.display(description='Созданные лиды (ID)')
    def created_lead_ids_display(self, obj):
        return _format_json_for_admin(obj.created_lead_ids)


class PlatformHelpMessageInline(admin.TabularInline):
    model = PlatformHelpMessage
    extra = 0
    can_delete = False
    readonly_fields = (
        'role',
        'input_mode',
        'content',
        'ai_model',
        'created_at',
    )
    fields = readonly_fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PlatformHelpConversation)
class PlatformHelpConversationAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'public_id',
        'user',
        'contact_whatsapp',
        'whatsapp_reply_link',
        'created_at',
        'updated_at',
        'message_count',
    )
    ordering = ('-created_at',)
    search_fields = ('public_id', 'user__username', 'contact_whatsapp')
    readonly_fields = (
        'public_id',
        'user',
        'contact_whatsapp',
        'contact_source',
        'whatsapp_reply_link',
        'created_at',
        'updated_at',
        'message_count',
    )
    inlines = (PlatformHelpMessageInline,)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('messages')

    @admin.display(description='Сообщений')
    def message_count(self, obj):
        return obj.messages.count()

    @admin.display(description='Открыть WhatsApp')
    def whatsapp_reply_link(self, obj):
        digits = str(getattr(obj, 'contact_whatsapp', '') or '').strip()
        if not digits:
            return '—'
        url = build_help_whatsapp_reply_url(digits)
        if not url:
            return '—'
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">Открыть WhatsApp</a>',
            url,
        )


@admin.register(PlatformHelpMessage)
class PlatformHelpMessageAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'conversation',
        'role',
        'input_mode',
        'ai_model',
        'created_at',
        'content_short',
    )
    list_filter = ('role', 'input_mode')
    ordering = ('-created_at', '-id')
    readonly_fields = (
        'conversation',
        'role',
        'input_mode',
        'content',
        'ai_model',
        'created_at',
    )
    search_fields = ('content', 'conversation__public_id')

    def has_add_permission(self, request):
        return False

    @admin.display(description='Текст')
    def content_short(self, obj):
        text = obj.content or ''
        return text if len(text) <= 80 else f'{text[:80]}…'