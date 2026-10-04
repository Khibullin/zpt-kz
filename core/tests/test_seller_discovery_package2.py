import io
import json
import traceback
from decimal import Decimal
from unittest.mock import patch
from urllib import error, parse

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from catalog.models import Product, SellerProfile
from core.models import (
    Seller,
    SellerLead,
    SellerLeadDuplicateMatch,
    SellerLeadEvidence,
    SellerLeadLocation,
    SellerLeadPipelineRun,
    SellerLeadSource,
)
from core.services.seller_discovery_ingestion import (
    FUTURE_MATCHING_ROLE,
    ingest_seller_discovery_hit,
)
from core.services.seller_discovery_identity import (
    normalize_address,
    normalize_seller_name,
    refresh_seller_lead_identity,
)
from core.services.seller_discovery_providers.base import (
    DiscoveryProviderConfigError,
    DiscoveryProviderError,
    SellerDiscoveryHit,
)
from core.services.seller_discovery_providers.brave import (
    BraveWebDiscoveryProvider,
    build_brave_discovery_query,
    is_probable_auto_parts_seller_result,
    parse_brave_web_result,
)
from core.services.seller_discovery_providers.catalog import (
    DISCOVERY_CITIES,
    TWO_GIS_MAX_RADIUS_M,
    TWO_GIS_MIN_RADIUS_M,
    resolve_city,
)
from core.services.seller_discovery_providers.two_gis import (
    ITEM_FIELDS,
    TwoGisDiscoveryProvider,
    TwoGisPlacesClient,
    parse_two_gis_item,
    two_gis_item_fields,
)
from core.services.seller_discovery_sources import add_seller_lead_evidence
from core.services.seller_lead_search import collect_instagram_seller_leads
from core.services.seller_discovery_runner import run_seller_discovery
from core.services.seller_lead_admin_workflow import convert_lead_to_request_seller

TWO_GIS_ITEM = {
    'id': '70000001000000001',
    'type': 'branch',
    'name': 'China Parts',
    'address_name': 'ул. Абая, 10',
    'full_address_name': 'Алматы, ул. Абая, 10',
    'point': {'lat': 43.24, 'lon': 76.95},
    'rubrics': [{'id': '1', 'name': 'Автозапчасти'}],
    'org': {'id': '9001', 'name': 'China Parts'},
    'adm_div': [{'type': 'city', 'name': 'Алматы'}],
    'contact_groups': [{
        'contacts': [
            {'type': 'phone', 'value': '+7 727 123 45 67', 'text': '+7 727 123 45 67'},
            {'type': 'website', 'url': 'https://chinaparts.kz'},
            {'type': 'instagram', 'url': 'https://instagram.com/chinaparts'},
        ],
    }],
    'key': 'super-secret-key',
}


class _FakeSearchClient:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def search(self, query, *, count=10):
        self.queries.append((query, count))
        return list(self.rows)


class _FakeProvider:
    name = 'two_gis'

    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, *, city, direction, limit=None, max_pages=None):
        self.calls.append((city, direction, limit, max_pages))
        return list(self.hits)


def _discovery_counts():
    user_model = get_user_model()
    return (
        SellerLead.objects.count(),
        SellerLeadSource.objects.count(),
        SellerLeadEvidence.objects.count(),
        SellerLeadLocation.objects.count(),
        SellerLeadDuplicateMatch.objects.count(),
        Seller.objects.count(),
        SellerProfile.objects.count(),
        user_model.objects.count(),
        Product.objects.count(),
        SellerLeadPipelineRun.objects.count(),
    )


def _json_response(payload):
    class _Response:
        status = 200
        headers = {'Content-Type': 'application/json'}

        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    return _Response()


def _branch_item(item_id, name='Shop'):
    return {
        'id': item_id,
        'type': 'branch',
        'name': name,
        'full_address_name': 'Алматы, ул. Абая, 10',
        'point': {'lat': 43.24, 'lon': 76.95},
        'adm_div': [{'type': 'city', 'name': 'Алматы'}],
        'rubrics': [{'name': 'Автозапчасти'}],
    }


def _hit(**kwargs):
    defaults = {
        'provider': 'two_gis',
        'source_type': SellerLeadSource.SOURCE_TWO_GIS,
        'external_id': '70000001000000001',
        'name': 'China Parts',
        'city': 'Алматы',
        'address': 'Алматы, ул. Абая, 10',
        'latitude': Decimal('43.240000'),
        'longitude': Decimal('76.950000'),
        'phone': '77271234567',
        'phones': ('77271234567',),
        'website': 'https://chinaparts.kz',
        'instagram_url': 'https://www.instagram.com/chinaparts/',
        'category_text': 'автозапчасти',
        'rubrics': ('Автозапчасти',),
        'source_url': '',
        'search_query': 'автозапчасти',
        'org_id': '9001',
        'confidence': 80,
        'raw_data': {'id': '70000001000000001', 'name': 'China Parts'},
        'observed_at': timezone.now(),
    }
    defaults.update(kwargs)
    return SellerDiscoveryHit(**defaults)


class TwoGisParserTests(TestCase):
    def test_item_becomes_hit_without_api_key(self):
        city = resolve_city('Алматы')
        hit = parse_two_gis_item(TWO_GIS_ITEM, city=city, direction='автозапчасти')

        self.assertEqual(hit.provider, 'two_gis')
        self.assertEqual(hit.source_type, 'two_gis')
        self.assertEqual(hit.external_id, '70000001000000001')
        self.assertEqual(hit.name, 'China Parts')
        self.assertEqual(hit.city, 'Алматы')
        self.assertEqual(hit.address, 'Алматы, ул. Абая, 10')
        self.assertEqual(hit.phone, '77271234567')
        self.assertEqual(hit.whatsapp_phone, '')
        self.assertEqual(hit.category_text, 'Автозапчасти')
        self.assertEqual(hit.search_query, 'автозапчасти')
        self.assertEqual(hit.website, 'https://chinaparts.kz')
        self.assertEqual(hit.instagram_url, 'https://www.instagram.com/chinaparts/')
        self.assertEqual(hit.source_url, '')
        self.assertEqual(hit.rubrics, ('Автозапчасти',))
        self.assertEqual(hit.latitude, Decimal('43.240000'))
        self.assertEqual(hit.longitude, Decimal('76.950000'))
        self.assertNotIn('super-secret-key', json.dumps(hit.raw_data))
        self.assertEqual(SellerLead.objects.count(), 0)

    def test_other_city_is_dropped(self):
        item = dict(TWO_GIS_ITEM)
        item['adm_div'] = [{'type': 'city', 'name': 'Астана'}]
        hit = parse_two_gis_item(item, city=resolve_city('Алматы'), direction='автозапчасти')
        self.assertIsNone(hit)

    def test_http_error_redacts_api_key(self):
        captured = []

        def urlopen(http_request, timeout):
            captured.append(http_request.full_url)
            raise error.HTTPError(
                http_request.full_url,
                403,
                'forbidden',
                hdrs={'Content-Type': 'application/json'},
                fp=io.BytesIO(b'{"meta":{"code":403,"error":{"message":"bad key"}}}'),
            )

        client = TwoGisPlacesClient('super-secret-key', urlopen=urlopen)
        with self.assertRaises(Exception) as ctx:
            client.search_items('автозапчасти', city=resolve_city('Алматы'), limit=5)

        self.assertNotIn('super-secret-key', str(ctx.exception))
        self.assertIn('[REDACTED]', captured[0].replace('super-secret-key', '[REDACTED]'))
        self.assertNotIn('super-secret-key', str(ctx.exception))


class BraveDiscoveryTests(TestCase):
    def test_web_result_and_instagram_profile(self):
        website = parse_brave_web_result(
            {
                'title': 'China Parts | Магазин',
                'url': 'https://chinaparts.kz/shop',
                'description': 'Автозапчасти Алматы +7 727 123 45 67',
            },
            city='Алматы',
            direction='автозапчасти',
        )
        profile = parse_brave_web_result(
            {
                'title': 'China Parts (@chinaparts)',
                'url': 'https://www.instagram.com/chinaparts/',
                'description': '',
            },
            city='Астана',
            direction='Chery запчасти',
        )
        directory = parse_brave_web_result(
            {
                'title': 'China Parts',
                'url': 'https://2gis.kz/almaty/firm/1',
                'description': '+7 727 123 45 67',
            },
            city='Алматы',
            direction='автозапчасти',
        )

        self.assertEqual(website.source_type, 'brave_search')
        self.assertEqual(website.phone, '77271234567')
        self.assertEqual(website.website, 'https://chinaparts.kz/shop')
        self.assertEqual(profile.source_type, 'instagram')
        self.assertEqual(profile.instagram_url, 'https://www.instagram.com/chinaparts/')
        self.assertEqual(profile.city, 'Астана')
        self.assertIsNone(directory)
        self.assertNotIn('site:instagram.com', build_brave_discovery_query(
            city='Шымкент',
            direction='автоэлектрика',
        ))

    def test_provider_uses_existing_brave_client(self):
        client = _FakeSearchClient([
            {
                'title': 'Магазин фильтров',
                'url': 'https://shop-example.kz',
                'description': 'Продажа автозапчастей',
            },
        ])
        provider = BraveWebDiscoveryProvider(client=client)
        with override_settings(
            SELLER_DISCOVERY_ENABLED=True,
            SELLER_DISCOVERY_BRAVE_WEB_ENABLED=True,
        ):
            hits = provider.search(city='Шымкент', direction='фильтры', limit=5)

        self.assertEqual(len(hits), 1)
        self.assertEqual(client.queries[0][0], 'фильтры Шымкент Казахстан')
        self.assertNotIn('site:instagram.com', client.queries[0][0])


class BraveSuitabilityTests(TestCase):
    def _parse(self, title, description, url, direction='автозапчасти'):
        return parse_brave_web_result(
            {'title': title, 'description': description, 'url': url},
            city='Алматы',
            direction=direction,
        )

    def test_seller_pages_are_accepted_and_unrelated_pages_are_rejected(self):
        accepted = (
            ('China Parts — магазин автозапчастей', 'Алматы', 'https://chinaparts.kz/'),
            ('Запчасти Chery Haval Geely', 'Наличие и продажа', 'https://china-parts.kz/'),
            ('Магазин фильтров и масел для авто', 'Алматы', 'https://filters.kz/'),
            ('Авторазбор', 'Контрактные запчасти', 'https://razbor.kz/'),
            ('Кузовные запчасти', 'Магазин', 'https://kuzov.kz/'),
            ('Ходовая часть', 'Продажа запчастей', 'https://hodovaya.kz/'),
            ('Автоэлектрика', 'Магазин автозапчастей', 'https://electro.kz/'),
            ('Spare parts shop', 'Auto parts in Almaty', 'https://spare.kz/'),
            ('AutoFilter Алматы', 'Магазин автомобильных фильтров, продажа автозапчастей', 'https://autofilter.kz/'),
        )
        for title, description, url in accepted:
            with self.subTest(title=title):
                hit = self._parse(title, description, url)
                self.assertIsNotNone(hit)
                self.assertTrue(is_probable_auto_parts_seller_result(
                    title=title,
                    description=description,
                    name=hit.name,
                ))

        rejected = (
            ('Новости Chery Казахстан', 'Последние новости бренда', 'https://news.example.kz/chery', 'Chery запчасти Алматы'),
            ('Как выбрать масляный фильтр', 'Статья о выборе фильтра', 'https://blog.example.kz/filter', 'фильтры Алматы'),
            ('Обзор нового Chery', 'Информационная статья о модели', 'https://blog.example.kz/review', 'Chery запчасти'),
            ('Форум владельцев Haval', 'Обсуждение на форуме', 'https://forum.example.kz/', 'Haval запчасти'),
            ('ТОО Ромашка', 'Корпоративный сайт производственной компании', 'https://romashka.kz/', 'автозапчасти'),
            ('Chery', 'Официальный бренд', 'https://chery-brand.kz/', 'Chery запчасти Алматы'),
            ('Масляный фильтр', 'Описание детали', 'https://generic.kz/filter', 'фильтры'),
        )
        for title, description, url, direction in rejected:
            with self.subTest(title=title):
                self.assertIsNone(self._parse(title, description, url, direction))
                self.assertFalse(is_probable_auto_parts_seller_result(title=title, description=description))

    def test_instagram_profile_needs_seller_evidence(self):
        shop = self._parse(
            'China Parts (@chinaparts)',
            '',
            'https://www.instagram.com/chinaparts/',
        )
        club = self._parse(
            'Chery Club Almaty',
            'Клуб владельцев',
            'https://www.instagram.com/cheryclub/',
            'Chery запчасти Алматы',
        )
        self.assertIsNotNone(shop)
        self.assertEqual(shop.source_type, 'instagram')
        self.assertEqual(shop.instagram_url, 'https://www.instagram.com/chinaparts/')
        self.assertIsNone(club)

    def test_marketplace_hosts_stay_rejected(self):
        for url in (
            'https://kaspi.kz/shop/p/filter',
            'https://satu.kz/parts',
            'https://olx.kz/list',
            'https://kolesa.kz/a/1',
        ):
            with self.subTest(url=url):
                self.assertIsNone(self._parse('Магазин автозапчастей', 'Продажа запчастей', url))

    def test_search_query_does_not_make_a_weak_page_acceptable(self):
        title = 'Chery Казахстан'
        description = 'Официальный бренд'
        self.assertFalse(is_probable_auto_parts_seller_result(title=title, description=description))
        hit = self._parse(title, description, 'https://chery-brand.kz/', 'Chery запчасти Алматы')
        self.assertIsNone(hit)


class IngestionTests(TestCase):
    def setUp(self):
        self.seller = Seller.objects.create(
            name='Existing seller',
            whatsapp='77010000000',
            transport_type='car',
            city='Алматы',
            receive_requests=False,
        )

    def test_creates_foundation_rows_and_does_not_activate_seller(self):
        user_count = get_user_model().objects.count()
        with patch(
            'core.services.seller_lead_admin_workflow.convert_lead_to_request_seller',
            wraps=convert_lead_to_request_seller,
        ) as convert:
            result = ingest_seller_discovery_hit(_hit())

        self.assertEqual(result.action, 'create')
        self.assertEqual(SellerLead.objects.count(), 1)
        lead = SellerLead.objects.get()
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_FOUND)
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.car_brands, '')
        self.assertTrue(lead.evidences.filter(field_name='phone', value='77271234567').exists())
        self.assertFalse(lead.evidences.filter(field_name='whatsapp').exists())
        self.assertEqual(lead.website_url, 'https://chinaparts.kz')
        self.assertEqual(lead.instagram_username, 'chinaparts')
        self.assertEqual(lead.normalized_domain, 'chinaparts.kz')
        self.assertEqual(lead.overall_confidence, 80)
        self.assertEqual(lead.marketplace_invitation_status, '')
        self.assertIsNone(lead.request_seller_id)
        self.assertEqual(lead.sources.get().provider, 'two_gis')
        self.assertEqual(lead.sources.get().external_id, '70000001000000001')
        self.assertTrue(lead.evidences.filter(field_name='name', is_selected=True).exists())
        location = SellerLeadLocation.objects.get()
        self.assertTrue(location.is_primary)
        self.assertEqual(location.address, 'Алматы, ул. Абая, 10')
        self.assertEqual(Seller.objects.count(), 1)
        self.seller.refresh_from_db()
        self.assertFalse(self.seller.receive_requests)
        self.assertEqual(SellerProfile.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), user_count)
        convert.assert_not_called()
        self.assertEqual(FUTURE_MATCHING_ROLE, 'potential_recipient')

    def test_same_external_id_updates_one_lead(self):
        first = ingest_seller_discovery_hit(_hit())
        second = ingest_seller_discovery_hit(_hit(name='China Parts Shop'))

        self.assertEqual(SellerLead.objects.count(), 1)
        self.assertEqual(second.action, 'update')
        self.assertEqual(second.match_reason, 'external_id')
        self.assertEqual(second.seller_lead_id, first.seller_lead_id)
        self.assertEqual(SellerLeadSource.objects.count(), 1)
        lead = SellerLead.objects.get()
        self.assertEqual(lead.name, 'China Parts')
        self.assertIsNotNone(lead.last_seen_at)

    def test_phone_match_enriches_existing_lead(self):
        existing = SellerLead.objects.create(
            name='Already known',
            city='Алматы',
            whatsapp='77271234567',
            lifecycle_status=SellerLead.LIFECYCLE_FOUND,
        )
        result = ingest_seller_discovery_hit(_hit(
            external_id='70000001000000002',
            source_url='',
            website='',
            instagram_url='',
        ))

        self.assertEqual(result.action, 'update')
        self.assertEqual(result.match_reason, 'phone')
        self.assertEqual(result.seller_lead_id, existing.pk)
        self.assertEqual(SellerLead.objects.count(), 1)
        self.assertEqual(existing.sources.count(), 1)

    def test_same_address_different_name_is_possible_duplicate(self):
        ingest_seller_discovery_hit(_hit(
            phone='',
            phones=(),
            website='',
            instagram_url='',
        ))
        ingest_seller_discovery_hit(_hit(
            external_id='70000001000000099',
            name='Other Parts',
            phone='',
            phones=(),
            website='',
            instagram_url='',
            source_url='',
        ))

        self.assertEqual(SellerLead.objects.count(), 2)
        match = SellerLeadDuplicateMatch.objects.get()
        self.assertEqual(match.status, SellerLeadDuplicateMatch.STATUS_POSSIBLE)
        self.assertGreaterEqual(match.score, 50)

    def test_dry_run_writes_nothing(self):
        before = _discovery_counts()
        with patch('core.services.seller_discovery_ingestion.refresh_seller_lead_identity') as refresh:
            result = ingest_seller_discovery_hit(_hit(), dry_run=True)

        refresh.assert_not_called()
        self.assertTrue(result.dry_run)
        self.assertEqual(result.action, 'create')
        self.assertEqual(_discovery_counts(), before)

    def test_dry_run_does_not_refresh_existing_lead(self):
        ingest_seller_discovery_hit(_hit())
        lead = SellerLead.objects.get()
        lifecycle = lead.lifecycle_status
        updated_at = lead.updated_at
        before = _discovery_counts()
        with patch('core.services.seller_discovery_ingestion.refresh_seller_lead_identity') as refresh:
            result = ingest_seller_discovery_hit(_hit(), dry_run=True)

        refresh.assert_not_called()
        self.assertEqual(result.action, 'update')
        self.assertEqual(result.match_reason, 'external_id')
        self.assertEqual(_discovery_counts(), before)
        lead.refresh_from_db()
        self.assertEqual(lead.lifecycle_status, lifecycle)
        self.assertEqual(lead.updated_at, updated_at)
        self.assertEqual(SellerLeadPipelineRun.objects.count(), 0)

    def test_owner_verified_phone_is_kept(self):
        ingest_seller_discovery_hit(_hit())
        evidence = SellerLeadEvidence.objects.get(field_name='phone', is_selected=True)
        evidence.is_owner_verified = True
        evidence.save(update_fields=['is_owner_verified', 'updated_at'])

        ingest_seller_discovery_hit(_hit(phone='77055554433', phones=('77055554433',)))

        lead = SellerLead.objects.get()
        self.assertEqual(lead.whatsapp, '')
        selected = SellerLeadEvidence.objects.get(field_name='phone', is_selected=True)
        self.assertTrue(selected.is_owner_verified)
        self.assertIn('77271234567', selected.value)


@override_settings(
    SELLER_DISCOVERY_ENABLED=True,
    SELLER_DISCOVERY_2GIS_ENABLED=True,
    SELLER_DISCOVERY_BRAVE_WEB_ENABLED=False,
    TWO_GIS_API_KEY='test-key',
    BRAVE_SEARCH_API_KEY='test-key',
    SELLER_SEARCH_ENABLED=False,
)
class DiscoveryCommandTests(TestCase):
    def test_requires_explicit_mode(self):
        with self.assertRaises(CommandError):
            call_command('discover_seller_sources', '--provider', 'two_gis', '--city', 'Алматы')

    def test_disabled_flag_does_not_call_provider(self):
        with override_settings(SELLER_DISCOVERY_ENABLED=False):
            with patch(
                'core.services.seller_discovery_runner.build_discovery_provider',
            ) as build:
                with self.assertRaises(CommandError):
                    call_command(
                        'discover_seller_sources',
                        '--provider', 'two_gis',
                        '--city', 'Алматы',
                        '--apply',
                    )
        build.assert_not_called()

    def test_dry_run_does_not_write(self):
        provider = _FakeProvider([_hit()])
        out = io.StringIO()
        with patch(
            'core.services.seller_discovery_runner.build_discovery_provider',
            return_value=provider,
        ):
            call_command(
                'discover_seller_sources',
                '--provider', 'two_gis',
                '--city', 'Алматы',
                '--direction', 'автозапчасти',
                '--dry-run',
                stdout=out,
            )

        self.assertEqual(SellerLead.objects.count(), 0)
        self.assertIn('Dry-run: записи в базу не сохранялись.', out.getvalue())
        self.assertNotIn('test-key', out.getvalue())

    def test_execute_saves_lead_without_network(self):
        provider = _FakeProvider([_hit()])

        def explode(*args, **kwargs):
            raise AssertionError('network')

        out = io.StringIO()
        with patch('urllib.request.urlopen', side_effect=explode):
            with patch(
                'core.services.seller_discovery_providers.two_gis._urlopen_without_proxy',
                side_effect=explode,
            ):
                with patch(
                    'core.services.seller_discovery_runner.build_discovery_provider',
                    return_value=provider,
                ):
                    call_command(
                        'discover_seller_sources',
                        '--provider', 'two_gis',
                        '--city', 'Алматы',
                        '--direction', 'автозапчасти',
                        '--apply',
                        stdout=out,
                    )

        self.assertEqual(SellerLead.objects.count(), 1)
        self.assertIsNone(SellerLead.objects.get().request_seller_id)
        self.assertIn('Создано SellerLead: 1', out.getvalue())
        self.assertNotIn('test-key', out.getvalue())

    def test_runner_stops_at_max_hits(self):
        provider = _FakeProvider([
            _hit(external_id='1', source_url='', phone='', phones=(), website='', instagram_url=''),
            _hit(external_id='2', source_url='', name='Second', phone='', phones=(), website='', instagram_url=''),
        ])
        stats = run_seller_discovery(
            provider_names=['two_gis'],
            cities=['Алматы'],
            directions=['автозапчасти'],
            max_hits=1,
            dry_run=True,
            providers=[provider],
        )
        self.assertEqual(stats.hits_received, 1)
        self.assertEqual(SellerLead.objects.count(), 0)

    def test_missing_mode_and_both_modes_do_not_call_network(self):
        def explode(*args, **kwargs):
            raise AssertionError('network')

        with patch('urllib.request.urlopen', side_effect=explode):
            with patch(
                'core.services.seller_discovery_providers.two_gis._urlopen_without_proxy',
                side_effect=explode,
            ):
                with self.assertRaises(CommandError):
                    call_command(
                        'discover_seller_sources',
                        '--provider', 'two_gis',
                        '--city', 'Алматы',
                    )
                with self.assertRaises(CommandError):
                    call_command(
                        'discover_seller_sources',
                        '--provider', 'two_gis',
                        '--city', 'Алматы',
                        '--dry-run',
                        '--apply',
                    )

    def test_provider_flag_blocks_network(self):
        def explode(*args, **kwargs):
            raise AssertionError('network')

        with override_settings(SELLER_DISCOVERY_2GIS_ENABLED=False):
            with patch(
                'core.services.seller_discovery_providers.two_gis._urlopen_without_proxy',
                side_effect=explode,
            ):
                with self.assertRaises(CommandError) as ctx:
                    call_command(
                        'discover_seller_sources',
                        '--provider', 'two_gis',
                        '--city', 'Алматы',
                        '--dry-run',
                    )
        self.assertIn('SELLER_DISCOVERY_2GIS_ENABLED', str(ctx.exception))

    def test_max_pages_limits_2gis_requests(self):
        pages = []

        def urlopen(http_request, timeout):
            query = parse.parse_qs(parse.urlsplit(http_request.full_url).query)
            pages.append(query['page'][0])
            return _json_response({
                'meta': {'code': 200},
                'result': {'items': [_branch_item(query['page'][0])]},
            })

        with override_settings(SELLER_DISCOVERY_2GIS_PAGE_SIZE=1):
            with patch(
                'core.services.seller_discovery_providers.two_gis._urlopen_without_proxy',
                side_effect=urlopen,
            ):
                call_command(
                    'discover_seller_sources',
                    '--provider', 'two_gis',
                    '--city', 'Алматы',
                    '--direction', 'автозапчасти',
                    '--max-pages', '1',
                    '--dry-run',
                    stdout=io.StringIO(),
                )
        self.assertEqual(pages, ['1'])


class TwoGisPaginationTests(TestCase):
    def _client(self, urlopen):
        return TwoGisPlacesClient('test-key', urlopen=urlopen)

    def test_two_pages_and_max_pages_and_empty_page_and_429(self):
        def scripted(pages_payload):
            calls = []

            def urlopen(http_request, timeout):
                query = parse.parse_qs(parse.urlsplit(http_request.full_url).query)
                page = int(query['page'][0])
                calls.append(page)
                self.assertEqual(query['page_size'], ['1'])
                self.assertNotIn('region_id', query)
                self.assertIn('items.rubrics', query['fields'][0])
                self.assertIn('items.org', query['fields'][0])
                self.assertIn('items.brand', query['fields'][0])
                self.assertNotIn('items.contact_groups', query['fields'][0])
                self.assertIn('items.point', query['fields'][0])
                self.assertIn('items.full_address_name', query['fields'][0])
                payload = pages_payload.get(page, {'meta': {'code': 200}, 'result': {'items': []}})
                return _json_response(payload)

            return calls, urlopen

        full = {'meta': {'code': 200}, 'result': {'items': [_branch_item('1', 'One')]}}
        second = {'meta': {'code': 200}, 'result': {'items': [_branch_item('2', 'Two')]}}
        empty = {'meta': {'code': 200}, 'result': {'items': []}}

        calls, urlopen = scripted({1: full})
        items = self._client(urlopen).search_items(
            'автозапчасти',
            city=resolve_city('Алматы'),
            page_size=1,
            max_pages=1,
        )
        self.assertEqual(calls, [1])
        self.assertEqual(len(items), 1)

        calls, urlopen = scripted({1: full, 2: second})
        items = self._client(urlopen).search_items(
            'автозапчасти',
            city=resolve_city('Алматы'),
            page_size=1,
            max_pages=2,
        )
        self.assertEqual(calls, [1, 2])
        self.assertEqual([item['id'] for item in items], ['1', '2'])

        calls, urlopen = scripted({1: full, 2: second, 3: second})
        self._client(urlopen).search_items(
            'автозапчасти',
            city=resolve_city('Алматы'),
            page_size=1,
            max_pages=1,
        )
        self.assertEqual(calls, [1])

        calls, urlopen = scripted({1: full, 2: empty, 3: second})
        items = self._client(urlopen).search_items(
            'автозапчасти',
            city=resolve_city('Алматы'),
            page_size=1,
            max_pages=3,
        )
        self.assertEqual(calls, [1, 2])
        self.assertEqual(len(items), 1)

        calls = []

        def rate_limited(http_request, timeout):
            calls.append(http_request.full_url)
            raise error.HTTPError(
                http_request.full_url,
                429,
                'rate limit',
                hdrs={'Content-Type': 'application/json'},
                fp=io.BytesIO(b'{"meta":{"code":429,"error":{"message":"rate"}}}'),
            )

        with self.assertRaises(DiscoveryProviderError) as ctx:
            self._client(rate_limited).search_items(
                'автозапчасти',
                city=resolve_city('Алматы'),
                page_size=1,
                max_pages=5,
            )
        self.assertEqual(len(calls), 1)
        self.assertIn('429', str(ctx.exception))
        self.assertNotIn('permission', str(ctx.exception).casefold())
        self.assertNotIn('items.contact_groups', ITEM_FIELDS)

    def test_search_areas_are_bounded_and_unknown_city_does_not_search(self):
        calls = []

        def urlopen(http_request, timeout):
            query = parse.parse_qs(parse.urlsplit(http_request.full_url).query)
            calls.append(query)
            return _json_response({'meta': {'code': 200}, 'result': {'items': []}})

        client = self._client(urlopen)
        for name, city in DISCOVERY_CITIES.items():
            self.assertGreaterEqual(city.radius_m, TWO_GIS_MIN_RADIUS_M)
            self.assertLessEqual(city.radius_m, TWO_GIS_MAX_RADIUS_M)
            client.search_items('автозапчасти', city=city, max_pages=1)
            query = calls[-1]
            self.assertEqual(query['point'], [f'{city.longitude:.6f},{city.latitude:.6f}'])
            self.assertEqual(query['radius'], [str(city.radius_m)])
            self.assertNotIn('region_id', query)
            self.assertEqual(name, city.name)

        def explode(*args, **kwargs):
            raise AssertionError('network')

        provider = TwoGisDiscoveryProvider(client=TwoGisPlacesClient('test-key', urlopen=explode))
        with override_settings(SELLER_DISCOVERY_ENABLED=True, SELLER_DISCOVERY_2GIS_ENABLED=True):
            with self.assertRaises(DiscoveryProviderError) as ctx:
                provider.search(city='Берлин', direction='автозапчасти', limit=5)
        self.assertIn('Берлин', str(ctx.exception))


class ContactAndIdentityTests(TestCase):
    def test_missing_contact_groups_still_ingests(self):
        item = dict(TWO_GIS_ITEM)
        item.pop('contact_groups')
        hit = parse_two_gis_item(item, city=resolve_city('Алматы'), direction='Chery запчасти')
        self.assertIsNotNone(hit)
        self.assertEqual(hit.phone, '')
        self.assertEqual(hit.whatsapp_phone, '')
        self.assertNotIn('Chery', hit.category_text)
        result = ingest_seller_discovery_hit(hit)
        lead = SellerLead.objects.get(pk=result.seller_lead_id)
        self.assertEqual(lead.name, 'China Parts')
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.car_brands, '')
        self.assertFalse(lead.evidences.filter(field_name='phone').exists())
        self.assertFalse(lead.evidences.filter(field_name='whatsapp').exists())
        self.assertFalse(lead.evidences.filter(field_name='vehicle_brand').exists())
        self.assertEqual(lead.sources.get().metadata['search_query'], 'Chery запчасти')
        self.assertEqual(lead.sources.get().source_url, '')
        self.assertEqual(hit.source_url, '')

    def test_source_url_is_not_synthesized_and_external_id_reuses_the_lead(self):
        hit = parse_two_gis_item(TWO_GIS_ITEM, city=resolve_city('Алматы'), direction='автозапчасти')
        self.assertEqual(hit.source_url, '')
        self.assertNotIn('2gis.kz', hit.source_url)
        first = ingest_seller_discovery_hit(hit)
        second = ingest_seller_discovery_hit(hit)
        self.assertEqual(first.action, 'create')
        self.assertEqual(second.action, 'update')
        self.assertEqual(second.match_reason, 'external_id')
        self.assertEqual(second.seller_lead_id, first.seller_lead_id)
        self.assertEqual(SellerLead.objects.count(), 1)
        source = SellerLeadSource.objects.get()
        self.assertEqual(source.provider, 'two_gis')
        self.assertEqual(source.external_id, '70000001000000001')
        self.assertEqual(source.source_url, '')

    def test_related_links_are_not_website_or_instagram(self):
        item = json.loads(json.dumps(TWO_GIS_ITEM))
        item.pop('contact_groups')
        item['links'] = {
            'nearest_stations': [{'id': 'station-1', 'name': 'Абая'}],
            'website': 'https://not-the-shop.example',
            'instagram': 'related_shop',
            'attractions': [{'id': 'park-1'}],
        }
        hit = parse_two_gis_item(item, city=resolve_city('Алматы'), direction='автозапчасти')
        self.assertIsNotNone(hit)
        self.assertEqual(hit.website, '')
        self.assertEqual(hit.instagram_url, '')
        self.assertNotIn('items.links', ITEM_FIELDS)
        ingest_seller_discovery_hit(hit)
        lead = SellerLead.objects.get()
        self.assertEqual(lead.website_url, '')
        self.assertEqual(lead.instagram_username, '')

    def test_website_and_instagram_contacts_are_kept_when_links_exist(self):
        item = json.loads(json.dumps(TWO_GIS_ITEM))
        item['links'] = {
            'website': 'https://ignored.example',
            'instagram': 'ignored_profile',
        }
        item['contact_groups'] = [{
            'contacts': [
                {'type': 'website', 'url': 'https://chinaparts.kz'},
                {'type': 'instagram', 'value': 'chinaparts'},
            ],
        }]
        hit = parse_two_gis_item(item, city=resolve_city('Алматы'), direction='автозапчасти')
        self.assertEqual(hit.website, 'https://chinaparts.kz')
        self.assertEqual(hit.instagram_url, 'https://www.instagram.com/chinaparts/')
        self.assertEqual(hit.source_url, '')

    def test_phone_contact_is_not_whatsapp(self):
        hit = parse_two_gis_item(TWO_GIS_ITEM, city=resolve_city('Алматы'), direction='автозапчасти')
        ingest_seller_discovery_hit(hit)
        lead = SellerLead.objects.get()
        self.assertEqual(lead.whatsapp, '')
        self.assertTrue(lead.evidences.filter(field_name='phone').exists())
        self.assertFalse(lead.evidences.filter(field_name='whatsapp').exists())

    def test_explicit_whatsapp_contact_is_stored_as_whatsapp(self):
        item = json.loads(json.dumps(TWO_GIS_ITEM))
        item['contact_groups'][0]['contacts'] = [{
            'type': 'whatsapp',
            'value': '+7 701 555 44 33',
        }]
        hit = parse_two_gis_item(item, city=resolve_city('Алматы'), direction='автозапчасти')
        self.assertEqual(hit.whatsapp_phone, '77015554433')
        self.assertEqual(hit.phones, ())
        ingest_seller_discovery_hit(hit)
        lead = SellerLead.objects.get()
        self.assertEqual(lead.whatsapp, '77015554433')
        self.assertTrue(lead.evidences.filter(field_name='whatsapp').exists())
        self.assertFalse(lead.evidences.filter(field_name='phone').exists())

    def test_search_query_does_not_become_brand_evidence(self):
        queries = (
            'Chery запчасти Алматы',
            'Haval запчасти',
            'Geely запчасти',
            'Jetour запчасти',
            'масла',
            'фильтры',
        )
        for index, query in enumerate(queries, start=1):
            with self.subTest(query=query):
                result = ingest_seller_discovery_hit(_hit(
                    name='Auto Parts Shop',
                    external_id=f'query-{index}',
                    source_url='',
                    address=f'Алматы, ул. Тест, {index}',
                    category_text='',
                    rubrics=(),
                    search_query=query,
                    phone='',
                    phones=(),
                    website='',
                    instagram_url='',
                ))
                lead = SellerLead.objects.get(pk=result.seller_lead_id)
                self.assertEqual(lead.car_brands, '')
                self.assertFalse(lead.evidences.filter(field_name='vehicle_brand').exists())
                self.assertFalse(lead.evidences.filter(value__icontains='Chery').exists())
                self.assertFalse(lead.evidences.filter(value__icontains='Haval').exists())
                self.assertFalse(lead.evidences.filter(value__icontains='Geely').exists())
                self.assertFalse(lead.evidences.filter(value__icontains='Jetour').exists())
                self.assertEqual(lead.sources.get().metadata['search_query'], query)

    def test_ambiguous_phone_and_domain_do_not_pick_the_first_lead(self):
        first = SellerLead.objects.create(name='First', city='Алматы')
        second = SellerLead.objects.create(name='Second', city='Алматы')
        for lead in (first, second):
            add_seller_lead_evidence(lead, field_name='phone', value='77271234567')
        phone_result = ingest_seller_discovery_hit(_hit(
            external_id='phone-new',
            source_url='',
            name='Third shop',
            phone='77271234567',
            phones=('77271234567',),
            website='',
            instagram_url='',
        ))
        self.assertEqual(phone_result.action, 'create')
        self.assertEqual(phone_result.match_reason, 'ambiguous_phone')
        self.assertNotIn(phone_result.seller_lead_id, {first.pk, second.pk})

        left = SellerLead.objects.create(name='Left', city='Алматы', website_url='https://shop.kz')
        right = SellerLead.objects.create(name='Right', city='Астана', website_url='https://www.shop.kz/about')
        refresh_seller_lead_identity(left)
        refresh_seller_lead_identity(right)
        domain_result = ingest_seller_discovery_hit(_hit(
            external_id='domain-new',
            source_url='',
            name='Domain shop',
            phone='',
            phones=(),
            website='https://shop.kz/catalog',
            instagram_url='',
        ))
        self.assertEqual(domain_result.match_reason, 'ambiguous_domain')
        self.assertEqual(domain_result.action, 'create')
        self.assertNotIn(domain_result.seller_lead_id, {left.pk, right.pk})

    def test_name_alone_is_not_identity_and_exact_address_must_be_unique(self):
        SellerLead.objects.create(name='Auto Parts Shop', city='Алматы')
        SellerLead.objects.create(name='Auto Parts Shop', city='Алматы')
        name_only = ingest_seller_discovery_hit(_hit(
            external_id='name-only',
            source_url='',
            name='Auto Parts Shop',
            address='',
            phone='',
            phones=(),
            website='',
            instagram_url='',
            latitude=None,
            longitude=None,
        ))
        self.assertEqual(name_only.action, 'create')
        self.assertEqual(name_only.match_reason, 'created')
        self.assertEqual(SellerLead.objects.count(), 3)

        address = 'Алматы, ул. Абая, 10'
        normalized_name = normalize_seller_name('Exact Shop')
        normalized = normalize_address(address)
        SellerLead.objects.create(
            name='Exact Shop',
            city='Алматы',
            normalized_name=normalized_name,
            normalized_address=normalized,
        )
        unique = ingest_seller_discovery_hit(_hit(
            external_id='exact-1',
            source_url='',
            name='Exact Shop',
            address=address,
            phone='',
            phones=(),
            website='',
            instagram_url='',
        ))
        self.assertEqual(unique.match_reason, 'name_address_city')
        self.assertEqual(unique.action, 'update')

        SellerLead.objects.create(
            name='Exact Shop',
            city='Алматы',
            normalized_name=normalized_name,
            normalized_address=normalized,
        )
        ambiguous = ingest_seller_discovery_hit(_hit(
            external_id='exact-2',
            source_url='',
            name='Exact Shop',
            address=address,
            phone='',
            phones=(),
            website='',
            instagram_url='',
        ))
        self.assertEqual(ambiguous.match_reason, 'ambiguous_name_address_city')
        self.assertEqual(ambiguous.action, 'create')


class TwoGisOptionalContactsTests(TestCase):
    def _query_fields(self, *, settings_overrides):
        captured = []

        def urlopen(http_request, timeout):
            query = parse.parse_qs(parse.urlsplit(http_request.full_url).query)
            captured.append(query['fields'][0])
            return _json_response({'meta': {'code': 200}, 'result': {'items': []}})

        with override_settings(**settings_overrides):
            TwoGisPlacesClient('test-key', urlopen=urlopen).search_items(
                'автозапчасти',
                city=resolve_city('Алматы'),
                page_size=1,
                max_pages=1,
            )
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_default_contacts_flag_omits_contact_groups_field(self):
        fields = self._query_fields(settings_overrides={
            'SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED': False,
        })
        self.assertNotIn('items.contact_groups', fields)
        self.assertEqual(fields, two_gis_item_fields(include_contacts=False))
        for name in (
            'items.point',
            'items.full_address_name',
            'items.rubrics',
            'items.org',
            'items.brand',
            'items.adm_div',
        ):
            self.assertIn(name, fields)
        self.assertNotIn('items.contact_groups', ITEM_FIELDS)

    def test_organization_without_contacts_becomes_hit(self):
        item = json.loads(json.dumps(TWO_GIS_ITEM))
        item.pop('contact_groups')
        item['brand'] = {'name': 'China Parts Brand'}
        with override_settings(SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=False):
            hit = parse_two_gis_item(item, city=resolve_city('Алматы'), direction='автозапчасти')
        self.assertIsNotNone(hit)
        self.assertEqual(hit.name, 'China Parts')
        self.assertEqual(hit.external_id, '70000001000000001')
        self.assertEqual(hit.city, 'Алматы')
        self.assertEqual(hit.address, 'Алматы, ул. Абая, 10')
        self.assertEqual(hit.latitude, Decimal('43.240000'))
        self.assertEqual(hit.longitude, Decimal('76.950000'))
        self.assertEqual(hit.rubrics, ('Автозапчасти',))
        self.assertEqual(hit.org_id, '9001')
        self.assertEqual(hit.brand_name, 'China Parts Brand')
        self.assertEqual(hit.phone, '')
        self.assertEqual(hit.phones, ())
        self.assertEqual(hit.whatsapp_phone, '')
        self.assertEqual(hit.website, '')
        self.assertEqual(hit.instagram_url, '')

    def test_contacts_flag_true_requests_contact_groups(self):
        fields = self._query_fields(settings_overrides={
            'SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED': True,
        })
        self.assertIn('items.contact_groups', fields)
        self.assertTrue(fields.endswith('items.contact_groups') or 'items.contact_groups' in fields.split(','))

    def test_contacts_flag_true_reads_real_contacts(self):
        item = json.loads(json.dumps(TWO_GIS_ITEM))
        item['contact_groups'][0]['contacts'].append({
            'type': 'whatsapp',
            'value': '+7 701 555 44 33',
        })
        captured = []

        def urlopen(http_request, timeout):
            query = parse.parse_qs(parse.urlsplit(http_request.full_url).query)
            captured.append(query['fields'][0])
            return _json_response({'meta': {'code': 200}, 'result': {'items': [item]}})

        with override_settings(
            SELLER_DISCOVERY_ENABLED=True,
            SELLER_DISCOVERY_2GIS_ENABLED=True,
            SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=True,
        ):
            hits = TwoGisDiscoveryProvider(
                client=TwoGisPlacesClient('test-key', urlopen=urlopen),
            ).search(city='Алматы', direction='автозапчасти', limit=10, max_pages=1)
        self.assertIn('items.contact_groups', captured[0])
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(hit.phone, '77271234567')
        self.assertEqual(hit.website, 'https://chinaparts.kz')
        self.assertEqual(hit.instagram_url, 'https://www.instagram.com/chinaparts/')
        self.assertEqual(hit.whatsapp_phone, '77015554433')

    def test_contacts_permission_error_is_not_retried(self):
        calls = []

        def urlopen(http_request, timeout):
            calls.append(http_request.full_url)
            raise error.HTTPError(
                http_request.full_url,
                403,
                'forbidden',
                hdrs={'Content-Type': 'application/json'},
                fp=io.BytesIO(
                    b'{"meta":{"code":403,"error":{"message":"You have no access to field items.contact_groups"}}}'
                ),
            )

        with override_settings(SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=True):
            with self.assertRaises(DiscoveryProviderError) as ctx:
                TwoGisPlacesClient('test-key', urlopen=urlopen).search_items(
                    'автозапчасти',
                    city=resolve_city('Алматы'),
                    page_size=1,
                    max_pages=5,
                )
        self.assertEqual(len(calls), 1)
        message = str(ctx.exception)
        self.assertIn('items.contact_groups', message)
        self.assertIn('permission', message)
        self.assertIn('no access to field items.contact_groups', message)

    def test_contacts_permission_error_redacts_api_key(self):
        secret = 'two-gis-live-key-9f3a'
        body = json.dumps({
            'meta': {
                'code': 403,
                'error': {
                    'message': (
                        'You have no access to field items.contact_groups. '
                        f'https://catalog.api.2gis.com/3.0/items?key={secret}'
                    ),
                },
            },
        }).encode()

        def urlopen(http_request, timeout):
            self.assertIn(f'key={secret}', http_request.full_url)
            raise error.HTTPError(
                http_request.full_url,
                403,
                'forbidden',
                hdrs={'Content-Type': 'application/json'},
                fp=io.BytesIO(body),
            )

        with override_settings(SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=True):
            with self.assertRaises(DiscoveryProviderError) as ctx:
                TwoGisPlacesClient(secret, urlopen=urlopen).search_items(
                    'автозапчасти',
                    city=resolve_city('Алматы'),
                    page_size=1,
                    max_pages=1,
                )

        message = str(ctx.exception)
        rendered = ''.join(traceback.format_exception(ctx.exception))
        self.assertIn('items.contact_groups', message)
        self.assertIn('permission', message)
        self.assertIn('[REDACTED]', message)
        self.assertNotIn(secret, message)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(f'key={secret}', message)
        self.assertNotIn(f'key={secret}', rendered)
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_contacts_flag_does_not_replace_master_or_provider_flags(self):
        def explode(*args, **kwargs):
            raise AssertionError('network')

        provider = TwoGisDiscoveryProvider(
            client=TwoGisPlacesClient('test-key', urlopen=explode),
        )
        with override_settings(
            SELLER_DISCOVERY_ENABLED=False,
            SELLER_DISCOVERY_2GIS_ENABLED=True,
            SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=True,
        ):
            with self.assertRaises(DiscoveryProviderConfigError) as ctx:
                provider.search(city='Алматы', direction='автозапчасти', limit=10)
        self.assertIn('SELLER_DISCOVERY_ENABLED', str(ctx.exception))

        with override_settings(
            SELLER_DISCOVERY_ENABLED=True,
            SELLER_DISCOVERY_2GIS_ENABLED=False,
            SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=True,
        ):
            with self.assertRaises(DiscoveryProviderConfigError) as ctx:
                provider.search(city='Алматы', direction='автозапчасти', limit=10)
        self.assertIn('SELLER_DISCOVERY_2GIS_ENABLED', str(ctx.exception))


@override_settings(
    SELLER_SEARCH_ENABLED=True,
    SELLER_SEARCH_PROVIDER='brave',
    BRAVE_SEARCH_API_KEY='test-key',
    SELLER_DISCOVERY_ENABLED=False,
    SELLER_DISCOVERY_2GIS_ENABLED=False,
    SELLER_DISCOVERY_BRAVE_WEB_ENABLED=False,
)
class LegacyInstagramPipelineTests(TestCase):
    def test_collect_instagram_seller_leads_ignores_discovery_flags(self):
        client = _FakeSearchClient([
            {
                'title': 'China Parts',
                'url': 'https://www.instagram.com/chinaparts/',
                'description': 'автозапчасти',
            },
        ])
        stats = collect_instagram_seller_leads(
            city='Алматы',
            category='автозапчасти',
            limit=1,
            dry_run=True,
            client=client,
        )
        self.assertGreaterEqual(stats.queries_executed, 1)
        self.assertIn('site:instagram.com', client.queries[0][0])
        self.assertEqual(SellerLead.objects.count(), 0)

    def test_brave_web_flag_off_does_not_call_network(self):
        def explode(*args, **kwargs):
            raise AssertionError('network')

        with override_settings(SELLER_DISCOVERY_ENABLED=True):
            with patch('urllib.request.urlopen', side_effect=explode):
                with patch(
                    'core.services.seller_discovery_providers.two_gis._urlopen_without_proxy',
                    side_effect=explode,
                ):
                    with self.assertRaises(CommandError) as ctx:
                        call_command(
                            'discover_seller_sources',
                            '--provider', 'brave',
                            '--city', 'Алматы',
                            '--dry-run',
                        )
        self.assertIn('SELLER_DISCOVERY_BRAVE_WEB_ENABLED', str(ctx.exception))

    def test_contacts_flag_does_not_change_instagram_pipeline(self):
        client = _FakeSearchClient([
            {
                'title': 'China Parts',
                'url': 'https://www.instagram.com/chinaparts/',
                'description': 'автозапчасти',
            },
        ])
        with override_settings(SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=True):
            stats = collect_instagram_seller_leads(
                city='Алматы',
                category='автозапчасти',
                limit=1,
                dry_run=True,
                client=client,
            )
        self.assertGreaterEqual(stats.queries_executed, 1)
        self.assertIn('site:instagram.com', client.queries[0][0])
        self.assertNotIn('contact_groups', client.queries[0][0])
        self.assertEqual(SellerLead.objects.count(), 0)

