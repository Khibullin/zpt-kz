import io
import json
import socket
import traceback
from email.message import Message
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
    SellerLeadContactCandidate,
    SellerLeadDuplicateMatch,
    SellerLeadEvidence,
    SellerLeadLocation,
    SellerLeadPipelineRun,
    SellerLeadSource,
)
from core.services.seller_discovery_sources import add_seller_lead_evidence
from core.services.seller_contact_enrichment import (
    LocatorHit,
    SellerContactEnrichmentError,
    _annotate_locator_agreement,
    _unique_domains,
    enrich_seller_lead_contacts,
)
from core.services.seller_contact_google_places import GooglePlaceLocator
from core.services.seller_contact_website import crawl_host_key
from core.services.seller_contact_yandex import YandexOrgError, YandexOrgLocator, locate_yandex_organization
from core.services.seller_contact_sources import SOURCE_CAPABILITIES

ENABLED = {
    'SELLER_CONTACT_ENRICHMENT_ENABLED': True,
    'SELLER_CONTACT_WEBSITE_ENABLED': True,
    'SELLER_CONTACT_GOOGLE_PLACES_ENABLED': True,
    'SELLER_CONTACT_BRAVE_ENABLED': True,
    'SELLER_CONTACT_2GIS_ENABLED': True,
    'SELLER_CONTACT_YANDEX_ENABLED': True,
    'SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED': True,
    'GOOGLE_PLACES_API_KEY': 'google-secret-key',
    'BRAVE_SEARCH_API_KEY': 'brave-secret-key',
    'TWO_GIS_API_KEY': 'two-gis-secret-key',
    'YANDEX_ORG_SEARCH_API_KEY': 'yandex-secret-key',
}


def _public_getaddrinfo(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 0))]


def setUpModule():
    global _dns_patch
    _dns_patch = patch(
        'core.services.seller_contact_website.socket.getaddrinfo',
        side_effect=_public_getaddrinfo,
    )
    _dns_patch.start()


def tearDownModule():
    _dns_patch.stop()


class _Response:
    def __init__(self, status=200, body='', headers=None):
        self.status = status
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
        raw = body if isinstance(body, bytes) else body.encode('utf-8')
        self._body = raw
        self._offset = 0

    def read(self, amount=-1):
        if amount is None or amount < 0:
            amount = len(self._body) - self._offset
        chunk = self._body[self._offset:self._offset + amount]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _html(*, title='China Parts', body='', city='Алматы'):
    return (
        '<!doctype html><html><head><title>' + title + '</title></head>'
        '<body><h1>' + title + '</h1><p>' + city + ', ул. Абая, 10</p>'
        + body + '</body></html>'
    )


def _lead(**kwargs):
    defaults = {
        'name': 'China Parts',
        'city': 'Алматы',
        'website_url': '',
    }
    defaults.update(kwargs)
    return SellerLead.objects.create(**defaults)


def _counts():
    user_model = get_user_model()
    return (
        SellerLead.objects.count(),
        SellerLeadSource.objects.count(),
        SellerLeadEvidence.objects.count(),
        SellerLeadContactCandidate.objects.count(),
        SellerLeadLocation.objects.count(),
        SellerLeadDuplicateMatch.objects.count(),
        Seller.objects.count(),
        SellerProfile.objects.count(),
        user_model.objects.count(),
        Product.objects.count(),
        SellerLeadPipelineRun.objects.count(),
    )


def _runs(result):
    return {item.source: item.status for item in result.source_runs}


class _Brave:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def search(self, query, count=10):
        self.queries.append(query)
        return list(self.rows)


def _google_body(*, phone='+7 701 999 88 77', website='https://chinaparts.kz/'):
    return json.dumps({'places': [{
        'id': 'places/ChIJchina',
        'displayName': {'text': 'China Parts'},
        'formattedAddress': 'Алматы, ул. Абая, 10',
        'websiteUri': website,
        'nationalPhoneNumber': phone,
        'internationalPhoneNumber': phone,
    }]})


def _yandex_body(*, website='https://chinaparts.kz/', phone='+7 (701) 555-66-77'):
    return json.dumps({'features': [{
        'geometry': {'type': 'Point', 'coordinates': [76.9, 43.2]},
        'properties': {
            'name': 'China Parts',
            'description': 'Алматы',
            'CompanyMetaData': {
                'id': 'yandex-object-should-not-be-stored',
                'name': 'China Parts',
                'address': 'Алматы, ул. Яндекс, 1',
                'url': website,
                'Phones': [{'type': 'phone', 'formatted': phone}],
            },
        },
    }]})


def _two_gis_body(*, contacts):
    return json.dumps({
        'meta': {'code': 200},
        'result': {'items': [{
            'id': '70000000000000001',
            'name': 'China Parts',
            'address_name': 'ул. Секретная, 1',
            'point': {'lat': 43.2, 'lon': 76.9},
            'contact_groups': [{'contacts': contacts}],
        }]},
    })


def _router(*, website_html, google_json=None, yandex_json=None, two_gis_json=None, calls=None):
    seen = calls if calls is not None else []

    def urlopen(http_request, timeout):
        seen.append(http_request)
        host = (parse.urlsplit(http_request.full_url).hostname or '').lower()
        path = parse.urlsplit(http_request.full_url).path
        if host == 'places.googleapis.com':
            return _Response(body=google_json or _google_body(), headers={'Content-Type': 'application/json'})
        if host == 'search-maps.yandex.ru':
            return _Response(body=yandex_json or _yandex_body(), headers={'Content-Type': 'application/json'})
        if host == 'catalog.api.2gis.com':
            return _Response(body=two_gis_json or _two_gis_body(contacts=[]), headers={'Content-Type': 'application/json'})
        if path == '/robots.txt':
            return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
        return _Response(body=website_html)

    return seen, urlopen


_API_HOSTS = {'places.googleapis.com', 'search-maps.yandex.ru', 'catalog.api.2gis.com'}


def _fetched_site_hosts(calls):
    hosts = set()
    for call in calls:
        parts = parse.urlsplit(call.full_url)
        if parts.path == '/robots.txt':
            continue
        host = (parts.hostname or '').lower()
        if host in _API_HOSTS:
            continue
        hosts.add(host)
    return hosts


def _pool_router(*, google_site, yandex_site, two_gis_site, pages=None):
    calls = []
    pages = pages or {}
    contacts = [{'type': 'website', 'value': two_gis_site}] if two_gis_site else []
    two_gis_json = _two_gis_body(contacts=contacts)

    def urlopen(http_request, timeout):
        calls.append(http_request)
        parts = parse.urlsplit(http_request.full_url)
        host = (parts.hostname or '').lower()
        if host == 'places.googleapis.com':
            return _Response(
                body=_google_body(website=google_site),
                headers={'Content-Type': 'application/json'},
            )
        if host == 'search-maps.yandex.ru':
            return _Response(
                body=_yandex_body(website=yandex_site),
                headers={'Content-Type': 'application/json'},
            )
        if host == 'catalog.api.2gis.com':
            return _Response(body=two_gis_json, headers={'Content-Type': 'application/json'})
        if parts.path == '/robots.txt':
            return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
        return _Response(body=pages.get(host, _html()))

    return calls, urlopen


@override_settings(**ENABLED)
class MultiSourceEnrichmentTests(TestCase):
    def test_capability_matrix(self):
        website = SOURCE_CAPABILITIES['website']
        self.assertFalse(website.locator)
        self.assertEqual(website.whatsapp_evidence, 'verified')
        two_gis = SOURCE_CAPABILITIES['two_gis']
        self.assertTrue(two_gis.locator)
        self.assertEqual(two_gis.whatsapp_evidence, 'only_explicit_contact')
        self.assertTrue(two_gis.persistent_contact_data)
        google = SOURCE_CAPABILITIES['google_places']
        self.assertTrue(google.locator)
        self.assertEqual(google.whatsapp_evidence, 'false')
        self.assertFalse(google.persistent_contact_data)
        brave = SOURCE_CAPABILITIES['brave']
        self.assertEqual(brave.whatsapp_evidence, 'candidate')
        self.assertFalse(brave.persistent_contact_data)
        yandex = SOURCE_CAPABILITIES['yandex_org']
        self.assertTrue(yandex.locator)
        self.assertEqual(yandex.whatsapp_evidence, 'false')
        self.assertFalse(yandex.persistent_contact_data)
        kolesa = SOURCE_CAPABILITIES['kolesa']
        self.assertFalse(kolesa.enabled)
        self.assertEqual(kolesa.reason, 'permission_required')

    def test_source_all_runs_configured_sources_and_skips_kolesa(self):
        lead = _lead()
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''}])
        calls, urlopen = _router(website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))
        result = enrich_seller_lead_contacts(
            lead,
            sources=['all'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        statuses = _runs(result)
        self.assertEqual(statuses['website'], 'executed')
        self.assertEqual(statuses['two_gis'], 'executed')
        self.assertEqual(statuses['google_places'], 'executed')
        self.assertEqual(statuses['brave'], 'executed')
        self.assertEqual(statuses['yandex_org'], 'executed')
        self.assertNotIn('kolesa', statuses)
        self.assertFalse(any('kolesa' in call.full_url for call in calls))
        self.assertIn('77011234567', result.verified_whatsapp)

    def test_missing_google_key_does_not_stop_all_mode(self):
        lead = _lead()
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''}])
        calls, urlopen = _router(website_html=_html())

        def explode(*args, **kwargs):
            raise AssertionError('google')

        with override_settings(GOOGLE_PLACES_API_KEY=''):
            with patch('core.services.seller_contact_enrichment.locate_google_place', side_effect=explode):
                result = enrich_seller_lead_contacts(
                    lead,
                    sources=['all'],
                    dry_run=True,
                    urlopen=urlopen,
                    brave_client=brave,
                )
        self.assertEqual(_runs(result)['google_places'], 'skipped_missing_key')
        self.assertEqual(_runs(result)['brave'], 'executed')
        self.assertEqual(_runs(result)['website'], 'executed')
        self.assertTrue(brave.queries)

    def test_missing_two_gis_key_does_not_stop_all_mode(self):
        lead = _lead()
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''}])
        calls, urlopen = _router(website_html=_html())

        def explode(*args, **kwargs):
            raise AssertionError('2gis')

        with override_settings(TWO_GIS_API_KEY=''):
            with patch(
                'core.services.seller_contact_enrichment.TwoGisPlacesClient.search_items',
                side_effect=explode,
            ):
                result = enrich_seller_lead_contacts(
                    lead,
                    sources=['all'],
                    dry_run=True,
                    urlopen=urlopen,
                    brave_client=brave,
                )
        self.assertEqual(_runs(result)['two_gis'], 'skipped_missing_key')
        self.assertEqual(_runs(result)['yandex_org'], 'executed')
        self.assertEqual(_runs(result)['brave'], 'executed')

    def test_missing_yandex_key_does_not_stop_all_mode(self):
        lead = _lead()
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''}])
        calls, urlopen = _router(website_html=_html())

        def explode(*args, **kwargs):
            raise AssertionError('yandex')

        with override_settings(YANDEX_ORG_SEARCH_API_KEY=''):
            with patch(
                'core.services.seller_contact_enrichment.locate_yandex_organization',
                side_effect=explode,
            ):
                result = enrich_seller_lead_contacts(
                    lead,
                    sources=['all'],
                    dry_run=True,
                    urlopen=urlopen,
                    brave_client=brave,
                )
        self.assertEqual(_runs(result)['yandex_org'], 'skipped_missing_key')
        self.assertEqual(_runs(result)['google_places'], 'executed')
        self.assertEqual(_runs(result)['website'], 'executed')

    def test_explicit_source_without_key_is_an_error(self):
        lead = _lead()

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with override_settings(GOOGLE_PLACES_API_KEY=''):
            with patch('core.services.seller_contact_enrichment.locate_google_place', side_effect=explode):
                with self.assertRaises(SellerContactEnrichmentError) as ctx:
                    enrich_seller_lead_contacts(lead, sources=['google_places'], dry_run=True)
        self.assertIn('GOOGLE_PLACES_API_KEY', str(ctx.exception))

        with override_settings(TWO_GIS_API_KEY=''):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'enrich_seller_contacts',
                    '--lead-id', str(lead.pk),
                    '--source', 'two_gis',
                    '--dry-run',
                )
        self.assertIn('TWO_GIS_API_KEY', str(ctx.exception))

        with override_settings(YANDEX_ORG_SEARCH_API_KEY=''):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'enrich_seller_contacts',
                    '--lead-id', str(lead.pk),
                    '--source', 'yandex_org',
                    '--dry-run',
                )
        self.assertIn('YANDEX_ORG_SEARCH_API_KEY', str(ctx.exception))

    def test_source_all_continues_after_verified_website_whatsapp(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''}])
        body = _two_gis_body(contacts=[
            {'type': 'whatsapp', 'value': '77011234567', 'text': '77011234567'},
        ])
        calls, urlopen = _router(
            website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
            two_gis_json=body,
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['all'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        statuses = _runs(result)
        self.assertEqual(statuses['website'], 'executed')
        self.assertEqual(statuses['two_gis'], 'executed')
        self.assertEqual(statuses['google_places'], 'executed')
        self.assertEqual(statuses['brave'], 'executed')
        self.assertEqual(statuses['yandex_org'], 'executed')
        self.assertNotIn('skipped_verified_whatsapp', statuses.values())
        self.assertTrue(brave.queries)
        self.assertTrue(any(call.full_url.startswith('https://places.googleapis.com') for call in calls))
        self.assertTrue(any((parse.urlsplit(call.full_url).hostname or '') == 'catalog.api.2gis.com' for call in calls))
        self.assertTrue(any((parse.urlsplit(call.full_url).hostname or '') == 'search-maps.yandex.ru' for call in calls))
        self.assertEqual(result.verified_whatsapp, ['77011234567'])

    def test_explicit_source_list_checks_every_source(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''}])
        body = _two_gis_body(contacts=[
            {'type': 'whatsapp', 'value': '77011234567', 'text': '77011234567'},
        ])
        calls, urlopen = _router(
            website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
            two_gis_json=body,
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['website', 'two_gis', 'brave'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        statuses = _runs(result)
        self.assertEqual(statuses['website'], 'executed')
        self.assertEqual(statuses['two_gis'], 'executed')
        self.assertEqual(statuses['brave'], 'executed')
        self.assertNotIn('google_places', statuses)
        self.assertNotIn('skipped_verified_whatsapp', statuses.values())
        self.assertTrue(brave.queries)
        self.assertTrue(any((parse.urlsplit(call.full_url).hostname or '') == 'catalog.api.2gis.com' for call in calls))

    def test_verified_website_whatsapp_skips_later_sources(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        brave = _Brave([{'title': 'China Parts', 'url': 'https://other.kz/', 'description': ''}])
        calls, urlopen = _router(website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        def explode(*args, **kwargs):
            raise AssertionError('locator')

        with patch('core.services.seller_contact_enrichment.locate_google_place', side_effect=explode):
            with patch('core.services.seller_contact_enrichment.locate_yandex_organization', side_effect=explode):
                with patch(
                    'core.services.seller_contact_enrichment.TwoGisPlacesClient.search_items',
                    side_effect=explode,
                ):
                    result = enrich_seller_lead_contacts(
                        lead,
                        sources=['all'],
                        dry_run=True,
                        urlopen=urlopen,
                        brave_client=brave,
                        stop_on_verified_whatsapp=True,
                    )
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertEqual(_runs(result)['google_places'], 'skipped_verified_whatsapp')
        self.assertEqual(_runs(result)['two_gis'], 'skipped_verified_whatsapp')
        self.assertEqual(_runs(result)['brave'], 'skipped_verified_whatsapp')
        self.assertEqual(_runs(result)['yandex_org'], 'skipped_verified_whatsapp')
        self.assertEqual(brave.queries, [])
        self.assertFalse(any(call.full_url.startswith('https://places.googleapis.com') for call in calls))
        self.assertFalse(any('2gis' in (parse.urlsplit(call.full_url).hostname or '') for call in calls))
        self.assertFalse(any('yandex' in (parse.urlsplit(call.full_url).hostname or '') for call in calls))

    def test_google_and_brave_same_domain_is_crawled_once(self):
        lead = _lead()
        brave = _Brave([{'title': 'China Parts официальный сайт', 'url': 'https://chinaparts.kz/', 'description': ''}])
        calls, urlopen = _router(website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        page_gets = [
            call for call in calls
            if (parse.urlsplit(call.full_url).hostname or '') == 'chinaparts.kz'
            and parse.urlsplit(call.full_url).path != '/robots.txt'
        ]
        self.assertEqual(len(page_gets), 1)
        agreed = [hit for hit in result.locators if 'chinaparts.kz' in hit.website_url and hit.detail]
        self.assertGreaterEqual(len(agreed), 2)
        self.assertIn('google_places', agreed[0].detail)
        self.assertIn('brave', agreed[0].detail)
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertTrue(all(item.origin != 'google_places' for item in result.observations))

    def test_existing_site_retries_same_host_contact_page_from_brave(self):
        lead = _lead(name='Компания АВТОБАН', website_url='https://autobahn.kz/')
        brave = _Brave([{
            'title': 'Компания АВТОБАН — контакты',
            'url': 'https://autobahn.kz/kontaktyi/',
            'description': 'Контакты компании',
        }])
        calls = []

        def urlopen(http_request, timeout):
            calls.append(http_request)
            parts = parse.urlsplit(http_request.full_url)
            if parts.path == '/robots.txt':
                return _Response(
                    body='User-agent: *\nDisallow:\n',
                    headers={'Content-Type': 'text/plain'},
                )
            if parts.path == '/kontaktyi/':
                return _Response(body=_html(
                    title='Компания АВТОБАН',
                    body='<a href="https://wa.me/77011234567">WhatsApp</a>',
                ))
            return _Response(body=_html(title='Компания АВТОБАН'))

        result = enrich_seller_lead_contacts(
            lead,
            sources=['website', 'brave'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        page_paths = [
            parse.urlsplit(call.full_url).path
            for call in calls
            if (parse.urlsplit(call.full_url).hostname or '') == 'autobahn.kz'
            and parse.urlsplit(call.full_url).path != '/robots.txt'
        ]
        self.assertIn('/', page_paths)
        self.assertIn('/kontaktyi/', page_paths)
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertIn('https://autobahn.kz/kontaktyi/', result.websites_considered)


    def test_google_yandex_and_brave_same_domain_is_crawled_once(self):
        lead = _lead()
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/contacts', 'description': ''}])
        calls, urlopen = _router(website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'brave', 'yandex_org', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        page_gets = [
            call for call in calls
            if (parse.urlsplit(call.full_url).hostname or '') == 'chinaparts.kz'
            and parse.urlsplit(call.full_url).path != '/robots.txt'
        ]
        self.assertEqual(len(page_gets), 1)
        detail = next(hit.detail for hit in result.locators if hit.detail.startswith('agreement='))
        self.assertIn('agreement=3', detail)
        self.assertIn('yandex_org', detail)
        self.assertEqual(result.observations[0].origin, 'website')
        self.assertNotIn('77015556677', [item.value for item in result.observations])
        self.assertNotIn('yandex-object-should-not-be-stored', json.dumps([
            hit.detail for hit in result.locators
        ]))

    def test_search_query_text_is_not_evidence(self):
        lead = _lead()
        brave = _Brave([{
            'title': 'China Parts',
            'url': 'https://chinaparts.kz/',
            'description': 'WhatsApp +7 701 000 11 22',
        }])
        calls, urlopen = _router(website_html=_html())
        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        self.assertTrue(any('WhatsApp' in query for query in brave.queries))
        self.assertTrue(any('официальный сайт' in query for query in brave.queries))
        blob = ' '.join(
            f'{item.value} {item.excerpt} {item.source_url}' for item in result.observations
        )
        self.assertNotIn('77010001122', blob)
        for query in brave.queries:
            self.assertNotIn(query, blob)

    def test_ordinary_phone_is_not_whatsapp(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        calls, urlopen = _router(website_html=_html(body='<a href="tel:+77017778899">Позвонить</a>'))
        result = enrich_seller_lead_contacts(lead, sources=['website'], dry_run=False, urlopen=urlopen)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(result.verified_whatsapp, [])
        self.assertTrue(SellerLeadEvidence.objects.filter(field_name='phone', value='77017778899').exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp').exists())

    def test_explicit_two_gis_whatsapp_is_accepted(self):
        lead = _lead()
        body = _two_gis_body(contacts=[
            {'type': 'whatsapp', 'value': '77012222222', 'text': '77012222222'},
            {'type': 'phone', 'value': '77013333333', 'text': '77013333333'},
            {'type': 'website', 'value': 'https://chinaparts.kz/'},
        ])
        calls, urlopen = _router(
            website_html=_html(),
            two_gis_json=body,
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['two_gis', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77012222222')
        self.assertIn('77012222222', result.verified_whatsapp)
        self.assertNotIn('77013333333', result.verified_whatsapp)
        whatsapp = SellerLeadEvidence.objects.get(field_name='whatsapp', value='77012222222')
        self.assertEqual(whatsapp.source.provider, 'two_gis')
        self.assertTrue(whatsapp.is_selected)
        self.assertTrue(SellerLeadEvidence.objects.filter(field_name='phone', value='77013333333').exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp', value='77013333333').exists())
        source = SellerLeadSource.objects.get(provider='two_gis')
        self.assertEqual(source.external_id, '70000000000000001')
        self.assertEqual(source.display_name, '')
        blob = json.dumps(source.metadata)
        self.assertNotIn('Секретная', blob)
        self.assertNotIn('77013333333', blob)
        self.assertFalse(SellerLeadLocation.objects.exists())

    def test_ordinary_two_gis_phone_stays_phone(self):
        lead = _lead()
        body = _two_gis_body(contacts=[
            {'type': 'phone', 'value': '+7 701 333 33 33', 'text': '+7 701 333 33 33'},
        ])
        calls, urlopen = _router(website_html=_html(), two_gis_json=body)
        result = enrich_seller_lead_contacts(lead, sources=['two_gis'], dry_run=False, urlopen=urlopen)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(result.verified_whatsapp, [])
        self.assertTrue(SellerLeadEvidence.objects.filter(field_name='phone', value='77013333333').exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp').exists())
        self.assertFalse(SellerLeadContactCandidate.objects.filter(
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
        ).exists())

    def test_brave_wa_me_stays_pending(self):
        lead = _lead()
        brave = _Brave([{
            'title': 'China Parts WhatsApp',
            'url': 'https://wa.me/77011234567',
            'description': '+7 701 000 00 00',
        }])
        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave'],
            dry_run=False,
            brave_client=brave,
        )
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(result.pending_candidates, ['77011234567'])
        self.assertEqual(result.verified_whatsapp, [])
        candidate = SellerLeadContactCandidate.objects.get(value='77011234567')
        self.assertEqual(candidate.status, SellerLeadContactCandidate.STATUS_PENDING)
        self.assertEqual(candidate.confidence, 'medium')
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp', is_selected=True).exists())

    def test_website_wa_me_is_verified(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        calls, urlopen = _router(website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))
        result = enrich_seller_lead_contacts(lead, sources=['website'], dry_run=False, urlopen=urlopen)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        evidence = SellerLeadEvidence.objects.get(field_name='whatsapp')
        self.assertGreaterEqual(evidence.confidence, 90)
        self.assertEqual(evidence.source.provider, 'website')
        self.assertTrue(evidence.is_selected)

    def test_conflicting_verified_whatsapp_numbers_are_not_chosen(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        body = _two_gis_body(contacts=[
            {'type': 'whatsapp', 'value': '77012222222', 'text': '77012222222'},
        ])
        calls, urlopen = _router(
            website_html=_html(body='<a href="https://wa.me/77011111111">WhatsApp</a>'),
            two_gis_json=body,
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['website', 'two_gis'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'conflict')
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(result.verified_whatsapp, [])
        self.assertCountEqual(result.conflicts, ['77011111111', '77012222222'])
        self.assertEqual(SellerLeadContactCandidate.objects.count(), 2)
        self.assertFalse(SellerLeadContactCandidate.objects.exclude(
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        ).exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp', is_selected=True).exists())

    def test_same_whatsapp_from_website_and_two_gis_is_one_candidate(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        body = _two_gis_body(contacts=[
            {'type': 'whatsapp', 'value': '77011234567', 'text': '77011234567'},
        ])
        calls, urlopen = _router(
            website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
            two_gis_json=body,
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['website', 'two_gis'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertEqual(result.conflicts, [])
        self.assertEqual(SellerLeadContactCandidate.objects.filter(value='77011234567').count(), 1)
        self.assertEqual(SellerLeadEvidence.objects.filter(field_name='whatsapp', value='77011234567').count(), 2)
        providers = set(SellerLeadEvidence.objects.filter(field_name='whatsapp').values_list(
            'source__provider', flat=True,
        ))
        self.assertEqual(providers, {'website', 'two_gis'})
        selected = list(SellerLeadEvidence.objects.filter(field_name='whatsapp', is_selected=True))
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].source.provider, 'website')
        self.assertEqual(lead.whatsapp_source_url, selected[0].source.source_url)
        self.assertIn('chinaparts.kz', lead.whatsapp_source_url)

    def test_google_phone_is_not_persisted(self):
        lead = _lead()
        calls, urlopen = _router(
            website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
            google_json=_google_body(phone='+7 701 999 88 77'),
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertNotIn('77019998877', result.verified_whatsapp)
        self.assertFalse(SellerLeadEvidence.objects.filter(value__contains='77019998877').exists())
        self.assertFalse(SellerLeadContactCandidate.objects.filter(value='77019998877').exists())
        google = SellerLeadSource.objects.get(provider='google_places')
        self.assertEqual(google.external_id, 'places/ChIJchina')
        self.assertNotIn('77019998877', json.dumps(google.metadata))
        self.assertNotIn('websiteUri', json.dumps(google.metadata))

    def test_yandex_phone_and_organization_data_are_not_persisted(self):
        lead = _lead()
        calls, urlopen = _router(
            website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['yandex_org', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(result.observations[0].origin, 'website')
        blob = json.dumps({
            'evidence': list(SellerLeadEvidence.objects.values_list('value', 'field_name')),
            'sources': list(SellerLeadSource.objects.values_list('provider', 'external_id', 'metadata', 'display_name')),
            'candidates': list(SellerLeadContactCandidate.objects.values_list('value', flat=True)),
        }, ensure_ascii=False)
        self.assertNotIn('77015556677', blob)
        self.assertNotIn('yandex-object-should-not-be-stored', blob)
        self.assertNotIn('Яндекс', blob)
        self.assertFalse(SellerLeadSource.objects.filter(provider__icontains='yandex').exists())
        self.assertFalse(SellerLeadLocation.objects.exists())
        evidence = SellerLeadEvidence.objects.get(field_name='whatsapp')
        self.assertEqual(evidence.source.provider, 'website')

    def test_kolesa_explicit_source_errors_before_network(self):
        lead = _lead()

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=explode):
            with patch('core.services.seller_contact_enrichment.locate_google_place', side_effect=explode):
                with self.assertRaises(CommandError) as ctx:
                    call_command(
                        'enrich_seller_contacts',
                        '--lead-id', str(lead.pk),
                        '--source', 'kolesa',
                        '--dry-run',
                    )
        self.assertIn('permission', str(ctx.exception))

    def test_marketplaces_and_social_pages_are_not_opened(self):
        lead = _lead()
        brave = _Brave([
            {'title': 'China Parts', 'url': 'https://kaspi.kz/shop/china', 'description': ''},
            {'title': 'China Parts', 'url': 'https://olx.kz/list', 'description': ''},
            {'title': 'China Parts', 'url': 'https://satu.kz/seller', 'description': ''},
            {'title': 'China Parts', 'url': 'https://kolesa.kz/a/seller', 'description': ''},
            {'title': 'China Parts', 'url': 'https://instagram.com/chinaparts', 'description': ''},
            {'title': 'China Parts', 'url': 'https://yandex.kz/maps/org/1', 'description': ''},
            {'title': 'China Parts', 'url': 'https://website.informer.com/chinaparts.kz', 'description': ''},
            {'title': 'China Parts', 'url': 'https://bizfam.ru/company/chinaparts', 'description': ''},
            {'title': 'China Parts', 'url': 'https://razborka.org/almaty', 'description': ''},
            {'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''},
        ])
        calls, urlopen = _router(website_html=_html(
            body='<a href="https://instagram.com/chinaparts">Instagram</a>',
        ))
        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        hosts = {(parse.urlsplit(call.full_url).hostname or '') for call in calls}
        self.assertNotIn('kaspi.kz', hosts)
        self.assertNotIn('olx.kz', hosts)
        self.assertNotIn('satu.kz', hosts)
        self.assertNotIn('kolesa.kz', hosts)
        self.assertNotIn('instagram.com', hosts)
        self.assertNotIn('yandex.kz', hosts)
        self.assertNotIn('website.informer.com', hosts)
        self.assertNotIn('bizfam.ru', hosts)
        self.assertNotIn('razborka.org', hosts)
        self.assertIn('chinaparts.kz', hosts)
        self.assertTrue(any(item.field_name == 'instagram' and item.origin == 'website' for item in result.observations))

    def test_two_gis_permission_error_does_not_stop_other_sources(self):
        lead = _lead()
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''}])

        def urlopen(http_request, timeout):
            host = (parse.urlsplit(http_request.full_url).hostname or '').lower()
            if host == 'catalog.api.2gis.com':
                body = b'{"meta":{"error":{"message":"contact_groups permission denied"}}}'
                raise error.HTTPError(
                    http_request.full_url,
                    403,
                    'forbidden',
                    Message(),
                    io.BytesIO(body),
                )
            if (parse.urlsplit(http_request.full_url).path or '') == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        result = enrich_seller_lead_contacts(
            lead,
            sources=['two_gis', 'brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        self.assertEqual(_runs(result)['two_gis'], 'error')
        self.assertEqual(_runs(result)['brave'], 'executed')
        self.assertIn('77011234567', result.verified_whatsapp)
        self.assertTrue(result.errors)

    def test_two_gis_contacts_flag_skips_http(self):
        lead = _lead()
        calls = []

        def urlopen(http_request, timeout):
            calls.append(http_request)
            raise AssertionError(http_request.full_url)

        with override_settings(SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=False):
            with self.assertRaises(SellerContactEnrichmentError) as ctx:
                enrich_seller_lead_contacts(
                    lead,
                    sources=['two_gis'],
                    dry_run=True,
                    urlopen=urlopen,
                )
        self.assertIn('SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED', str(ctx.exception))
        self.assertEqual(calls, [])

    def test_all_mode_skips_two_gis_when_contacts_flag_is_false(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        calls, urlopen = _router(website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))
        with override_settings(SELLER_DISCOVERY_2GIS_CONTACTS_ENABLED=False):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['all'],
                dry_run=True,
                urlopen=urlopen,
                brave_client=_Brave([]),
            )
        runs = _runs(result)
        self.assertEqual(runs['two_gis'], 'skipped_disabled')
        self.assertEqual(runs['website'], 'executed')
        self.assertEqual(runs['brave'], 'executed')
        hosts = {(parse.urlsplit(call.full_url).hostname or '').lower() for call in calls}
        self.assertNotIn('catalog.api.2gis.com', hosts)

    def test_dry_run_all_sources_writes_nothing(self):
        lead = _lead()
        before = _counts()
        brave = _Brave([{'title': 'China Parts', 'url': 'https://chinaparts.kz/', 'description': ''}])
        calls, urlopen = _router(website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))
        result = enrich_seller_lead_contacts(
            lead,
            sources=['all'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        self.assertFalse(result.wrote)
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(_counts(), before)
        self.assertEqual(Seller.objects.count(), 0)
        self.assertEqual(SellerProfile.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_apply_does_not_create_seller_records(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        calls, urlopen = _router(website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))
        enrich_seller_lead_contacts(lead, sources=['website'], dry_run=False, urlopen=urlopen)
        self.assertEqual(Seller.objects.count(), 0)
        self.assertEqual(SellerProfile.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(SellerLeadPipelineRun.objects.count(), 0)
        self.assertEqual(SellerLead.objects.get(pk=lead.pk).whatsapp, '77011234567')

    def test_command_all_dry_run_reports_whatsapp_state(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        stdout = io.StringIO()

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        class _QuietBrave:
            def __init__(self, api_key):
                return None

            def search(self, query, count=10):
                return []

        with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=urlopen):
            with patch(
                'core.services.seller_contact_enrichment.locate_google_place',
                return_value=GooglePlaceLocator(place_id='', website_uri=''),
            ):
                with patch(
                    'core.services.seller_contact_enrichment.locate_yandex_organization',
                    return_value=YandexOrgLocator(),
                ):
                    with patch(
                        'core.services.seller_contact_enrichment.TwoGisPlacesClient.search_items',
                        return_value=[],
                    ):
                        with patch(
                            'core.services.seller_contact_enrichment.BraveSearchClient',
                            _QuietBrave,
                        ):
                            call_command(
                                'enrich_seller_contacts',
                                '--lead-id', str(lead.pk),
                                '--source', 'all',
                                '--dry-run',
                                stdout=stdout,
                            )
        text = stdout.getvalue()
        self.assertIn('whatsapp_state=VERIFIED', text)
        self.assertIn('source website: executed', text)
        self.assertIn('source two_gis: executed', text)
        self.assertIn('source google_places: executed', text)
        self.assertIn('source brave: executed', text)
        self.assertIn('source yandex_org: executed', text)
        self.assertNotIn('skipped_verified_whatsapp', text)
        self.assertIn('website_discovered https://chinaparts.kz/', text)
        self.assertIn('website_crawled https://chinaparts.kz/', text)
        self.assertNotIn('website_skipped_budget', text)
        self.assertIn('77011234567', text)
        self.assertEqual(SellerLead.objects.get(pk=lead.pk).whatsapp, '')

    def test_stop_flag_reports_skipped_verified_whatsapp(self):
        lead = _lead(website_url='https://chinaparts.kz/')
        stdout = io.StringIO()

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        def explode(*args, **kwargs):
            raise AssertionError('locator')

        with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=urlopen):
            with patch('core.services.seller_contact_enrichment.locate_google_place', side_effect=explode):
                with patch('core.services.seller_contact_enrichment.locate_yandex_organization', side_effect=explode):
                    with patch(
                        'core.services.seller_contact_enrichment.TwoGisPlacesClient.search_items',
                        side_effect=explode,
                    ):
                        with patch(
                            'core.services.seller_contact_enrichment.BraveSearchClient.search',
                            side_effect=explode,
                        ):
                            call_command(
                                'enrich_seller_contacts',
                                '--lead-id', str(lead.pk),
                                '--source', 'all',
                                '--dry-run',
                                '--stop-on-verified-whatsapp',
                                stdout=stdout,
                            )
        text = stdout.getvalue()
        self.assertIn('source website: executed', text)
        self.assertIn('source google_places: skipped_verified_whatsapp', text)
        self.assertIn('source two_gis: skipped_verified_whatsapp', text)
        self.assertIn('source brave: skipped_verified_whatsapp', text)
        self.assertIn('source yandex_org: skipped_verified_whatsapp', text)
        self.assertEqual(SellerLead.objects.get(pk=lead.pk).whatsapp, '')

    def _two_site_router(self, *, page_a, page_b, site_a='https://china-parts-a.kz/', site_b='https://china-parts-b.kz/'):
        calls = []
        host_a = parse.urlsplit(site_a).hostname
        host_b = parse.urlsplit(site_b).hostname

        def urlopen(http_request, timeout):
            calls.append(http_request)
            host = (parse.urlsplit(http_request.full_url).hostname or '').lower()
            if host == 'places.googleapis.com':
                return _Response(
                    body=_google_body(website=site_a),
                    headers={'Content-Type': 'application/json'},
                )
            if host == 'search-maps.yandex.ru':
                return _Response(
                    body=_yandex_body(website=site_b),
                    headers={'Content-Type': 'application/json'},
                )
            if parse.urlsplit(http_request.full_url).path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            if host == host_a:
                return _Response(body=page_a)
            if host == host_b:
                return _Response(body=page_b)
            raise AssertionError(http_request.full_url)

        return calls, urlopen

    def test_full_mode_crawls_two_websites_after_first_whatsapp(self):
        lead = _lead()
        calls, urlopen = self._two_site_router(
            page_a=_html(body='<a href="https://wa.me/77011111111">WhatsApp</a>'),
            page_b=_html(body='<a href="https://wa.me/77012222222">WhatsApp</a>'),
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'yandex_org', 'website'],
            dry_run=True,
            urlopen=urlopen,
        )
        page_hosts = {
            (parse.urlsplit(call.full_url).hostname or '')
            for call in calls
            if parse.urlsplit(call.full_url).path != '/robots.txt'
            and (parse.urlsplit(call.full_url).hostname or '') in {'china-parts-a.kz', 'china-parts-b.kz'}
        }
        self.assertEqual(page_hosts, {'china-parts-a.kz', 'china-parts-b.kz'})
        self.assertEqual(result.outcome, 'conflict')
        self.assertEqual(result.verified_whatsapp, [])
        self.assertCountEqual(result.conflicts, ['77011111111', '77012222222'])

    def test_two_websites_with_same_whatsapp_keep_two_sources(self):
        lead = _lead()
        calls, urlopen = self._two_site_router(
            page_a=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
            page_b=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'yandex_org', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertEqual(result.conflicts, [])
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(SellerLeadContactCandidate.objects.filter(value='77011234567').count(), 1)
        evidences = list(SellerLeadEvidence.objects.filter(field_name='whatsapp', value='77011234567'))
        self.assertEqual(len(evidences), 2)
        urls = {item.source.source_url for item in evidences}
        self.assertTrue(any('china-parts-a.kz' in url for url in urls))
        self.assertTrue(any('china-parts-b.kz' in url for url in urls))
        selected = [item for item in evidences if item.is_selected]
        self.assertEqual(len(selected), 1)
        self.assertIn('china-parts-a.kz', selected[0].source.source_url)
        self.assertEqual(lead.whatsapp_source_url, selected[0].source.source_url)
        self.assertEqual(
            set(SellerLeadSource.objects.filter(provider='website').values_list('source_url', flat=True)),
            urls,
        )

    def test_two_websites_with_different_whatsapp_conflict_without_a_winner(self):
        lead = _lead()
        calls, urlopen = self._two_site_router(
            page_a=_html(body='<a href="https://wa.me/77011111111">WhatsApp</a>'),
            page_b=_html(body='<a href="https://wa.me/77012222222">WhatsApp</a>'),
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'yandex_org', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'conflict')
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.whatsapp_source_url, '')
        self.assertEqual(result.verified_whatsapp, [])
        self.assertEqual(SellerLeadContactCandidate.objects.count(), 2)
        self.assertFalse(SellerLeadContactCandidate.objects.exclude(
            status=SellerLeadContactCandidate.STATUS_CONFLICT,
        ).exists())
        evidences = list(SellerLeadEvidence.objects.filter(field_name='whatsapp'))
        self.assertEqual(len(evidences), 2)
        self.assertFalse(any(item.is_selected for item in evidences))
        by_value = {item.value: item.source.source_url for item in evidences}
        self.assertIn('china-parts-a.kz', by_value['77011111111'])
        self.assertIn('china-parts-b.kz', by_value['77012222222'])

    def test_stop_flag_does_not_fetch_the_second_website(self):
        lead = _lead()
        calls, urlopen = self._two_site_router(
            page_a=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
            page_b=_html(body='<a href="https://wa.me/77019998877">WhatsApp</a>'),
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'yandex_org', 'website'],
            dry_run=True,
            urlopen=urlopen,
            stop_on_verified_whatsapp=True,
        )
        hosts = {(parse.urlsplit(call.full_url).hostname or '') for call in calls}
        self.assertIn('china-parts-a.kz', hosts)
        self.assertNotIn('china-parts-b.kz', hosts)
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertNotIn('77019998877', [item.value for item in result.observations])

    def test_locator_host_dedupe_keeps_www_and_splits_siblings(self):
        self.assertEqual(crawl_host_key('www.example.kz'), crawl_host_key('example.kz'))
        self.assertNotEqual(crawl_host_key('shop1.example.kz'), crawl_host_key('shop2.example.kz'))
        self.assertNotEqual(crawl_host_key('one.co.jp'), crawl_host_key('two.co.jp'))
        self.assertEqual(len(_unique_domains([
            'https://example.kz/contacts',
            'https://www.example.kz/',
        ])), 1)
        self.assertEqual(len(_unique_domains([
            'https://shop1.example.kz/',
            'https://shop2.example.kz/',
        ])), 2)
        self.assertEqual(len(_unique_domains([
            'https://one.co.jp/',
            'https://two.co.jp/',
        ])), 2)

        agreed = [
            LocatorHit(source='google_places', website_url='https://www.example.kz/'),
            LocatorHit(source='yandex_org', website_url='https://example.kz/'),
        ]
        _annotate_locator_agreement(agreed)
        self.assertIn('agreement=2', agreed[0].detail)
        self.assertIn('google_places', agreed[0].detail)
        self.assertIn('yandex_org', agreed[1].detail)

        siblings = [
            LocatorHit(source='google_places', website_url='https://shop1.example.kz/'),
            LocatorHit(source='yandex_org', website_url='https://shop2.example.kz/'),
        ]
        _annotate_locator_agreement(siblings)
        self.assertEqual(siblings[0].detail, '')
        self.assertEqual(siblings[1].detail, '')

        suffixes = [
            LocatorHit(source='google_places', website_url='https://one.co.jp/'),
            LocatorHit(source='brave', website_url='https://two.co.jp/'),
        ]
        _annotate_locator_agreement(suffixes)
        self.assertEqual(suffixes[0].detail, '')
        self.assertEqual(suffixes[1].detail, '')

    def test_yandex_api_key_is_absent_from_traceback(self):
        secret = 'yandex-super-secret-key'
        request_url = f'https://search-maps.yandex.ru/v1/?apikey={secret}&text=China+Parts'

        def urlopen(http_request, timeout):
            raise error.HTTPError(
                request_url,
                502,
                f'apikey={secret}',
                Message(),
                io.BytesIO(b'{"message":"denied"}'),
            )

        with override_settings(YANDEX_ORG_SEARCH_API_KEY=secret):
            with self.assertRaises(YandexOrgError) as ctx:
                locate_yandex_organization(name='China Parts', city='Алматы', urlopen=urlopen)
        rendered = str(ctx.exception)
        formatted = ''.join(traceback.format_exception(ctx.exception))
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret, formatted)
        self.assertNotIn(f'apikey={secret}', formatted)

    def test_four_locator_websites_are_all_crawled(self):
        lead = _lead()
        calls, urlopen = _pool_router(
            google_site='https://google-site.kz/',
            yandex_site='https://yandex-site.kz/',
            two_gis_site='https://two-gis-site.kz/',
            pages={'two-gis-site.kz': _html(body='<a href="https://wa.me/77011234567">WhatsApp</a>')},
        )
        brave = _Brave([{'title': 'China Parts', 'url': 'https://brave-site.kz/', 'description': ''}])
        result = enrich_seller_lead_contacts(
            lead,
            sources=['all'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        self.assertEqual(_fetched_site_hosts(calls), {
            'two-gis-site.kz',
            'google-site.kz',
            'yandex-site.kz',
            'brave-site.kz',
        })
        self.assertEqual(len(result.websites_considered), 4)
        self.assertEqual(result.websites_skipped_budget, [])
        self.assertIn('77011234567', result.verified_whatsapp)

    def test_existing_and_four_locators_crawl_five_websites(self):
        lead = _lead(website_url='https://known-site.kz/')
        calls, urlopen = _pool_router(
            google_site='https://google-site.kz/',
            yandex_site='https://yandex-site.kz/',
            two_gis_site='https://two-gis-site.kz/',
        )
        brave = _Brave([{'title': 'China Parts', 'url': 'https://brave-site.kz/', 'description': ''}])
        result = enrich_seller_lead_contacts(
            lead,
            sources=['all'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        self.assertEqual(_fetched_site_hosts(calls), {
            'known-site.kz',
            'two-gis-site.kz',
            'google-site.kz',
            'yandex-site.kz',
            'brave-site.kz',
        })
        self.assertEqual(len(result.websites_considered), 5)
        self.assertEqual(result.websites_skipped_budget, [])

    def test_sixth_website_candidate_is_skipped_by_budget(self):
        lead = _lead(website_url='https://known-site.kz/')
        calls, urlopen = _pool_router(
            google_site='https://google-site.kz/',
            yandex_site='https://yandex-site.kz/',
            two_gis_site='https://two-gis-site.kz/',
        )
        brave = _Brave([
            {'title': 'China Parts', 'url': 'https://brave-site.kz/', 'description': ''},
            {'title': 'China Parts', 'url': 'https://brave-extra.kz/', 'description': ''},
        ])
        result = enrich_seller_lead_contacts(
            lead,
            sources=['all'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        crawled = _fetched_site_hosts(calls)
        self.assertEqual(crawled, {
            'known-site.kz',
            'two-gis-site.kz',
            'google-site.kz',
            'yandex-site.kz',
            'brave-site.kz',
        })
        self.assertNotIn('brave-extra.kz', crawled)
        self.assertTrue(any('brave-extra.kz' in url for url in result.websites_discovered))
        self.assertTrue(any('brave-extra.kz' in url for url in result.websites_skipped_budget))
        self.assertFalse(any('brave-extra.kz' in url for url in result.websites_skipped_brave_cap))
        self.assertFalse(any('brave-extra.kz' in url for url in result.websites_considered))

    def test_brave_urls_do_not_displace_google_yandex_or_two_gis(self):
        lead = _lead()
        calls, urlopen = _pool_router(
            google_site='https://google-site.kz/',
            yandex_site='https://yandex-site.kz/',
            two_gis_site='https://two-gis-site.kz/',
        )
        brave_rows = [
            {'title': 'China Parts', 'url': f'https://brave-{index}.kz/', 'description': ''}
            for index in range(1, 11)
        ]
        result = enrich_seller_lead_contacts(
            lead,
            sources=['all'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=_Brave(brave_rows),
        )
        crawled = _fetched_site_hosts(calls)
        self.assertIn('two-gis-site.kz', crawled)
        self.assertIn('google-site.kz', crawled)
        self.assertIn('yandex-site.kz', crawled)
        brave_crawled = {host for host in crawled if host.startswith('brave-')}
        self.assertLessEqual(len(brave_crawled), 2)
        self.assertEqual(len(crawled), 5)
        skipped_brave = [url for url in result.websites_skipped_brave_cap if 'brave-' in url]
        self.assertGreaterEqual(len(skipped_brave), 8)
        self.assertFalse(any('brave-' in url for url in result.websites_skipped_budget))

    def test_locator_agreement_outranks_a_single_brave_candidate(self):
        lead = _lead()
        calls, urlopen = _pool_router(
            google_site='https://agreed.kz/',
            yandex_site='https://agreed.kz/',
            two_gis_site='https://gis-only.kz/',
        )
        brave = _Brave([
            {'title': 'China Parts', 'url': 'https://agreed.kz/', 'description': ''},
            {'title': 'China Parts', 'url': 'https://lone-brave.kz/', 'description': ''},
        ])
        with override_settings(SELLER_CONTACT_MAX_WEBSITE_DOMAINS=1):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['all'],
                dry_run=True,
                urlopen=urlopen,
                brave_client=brave,
            )
        self.assertEqual(_fetched_site_hosts(calls), {'agreed.kz'})
        self.assertEqual(result.verified_whatsapp, [])
        agreed = [hit for hit in result.locators if 'agreed.kz' in hit.website_url]
        self.assertTrue(agreed)
        self.assertIn('agreement=3', agreed[0].detail)
        self.assertTrue(any('gis-only.kz' in url for url in result.websites_skipped_budget))
        self.assertTrue(any('lone-brave.kz' in url for url in result.websites_skipped_budget))

    def test_www_and_bare_host_stay_one_crawl_candidate(self):
        lead = _lead()
        calls, urlopen = _pool_router(
            google_site='https://www.shop.kz/',
            yandex_site='https://shop.kz/',
            two_gis_site='',
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'yandex_org', 'website'],
            dry_run=True,
            urlopen=urlopen,
        )
        self.assertEqual(_fetched_site_hosts(calls), {'www.shop.kz'})
        self.assertEqual(len(result.websites_discovered), 1)
        self.assertEqual(len(result.websites_considered), 1)

    def test_sibling_subdomains_remain_separate_crawl_candidates(self):
        lead = _lead()
        calls, urlopen = _pool_router(
            google_site='https://shop1.example.kz/',
            yandex_site='https://shop2.example.kz/',
            two_gis_site='',
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'yandex_org', 'website'],
            dry_run=True,
            urlopen=urlopen,
        )
        self.assertEqual(_fetched_site_hosts(calls), {'shop1.example.kz', 'shop2.example.kz'})
        self.assertEqual(len(result.websites_discovered), 2)
        self.assertEqual(result.websites_skipped_budget, [])

    def test_stop_flag_crawls_only_the_first_verified_website(self):
        lead = _lead()
        calls, urlopen = _pool_router(
            google_site='https://google-site.kz/',
            yandex_site='https://yandex-site.kz/',
            two_gis_site='https://two-gis-site.kz/',
            pages={'two-gis-site.kz': _html(body='<a href="https://wa.me/77011234567">WhatsApp</a>')},
        )
        brave = _Brave([{'title': 'China Parts', 'url': 'https://brave-site.kz/', 'description': ''}])
        result = enrich_seller_lead_contacts(
            lead,
            sources=['all'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
            stop_on_verified_whatsapp=True,
        )
        self.assertEqual(_fetched_site_hosts(calls), {'two-gis-site.kz'})
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertTrue(result.websites_discovered)
        self.assertNotIn('google-site.kz', _fetched_site_hosts(calls))

    def test_selected_website_evidence_ignores_provider_order(self):
        lead = _lead()
        calls, urlopen = self._two_site_router(
            page_a=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
            page_b=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'),
            site_a='https://china-parts-b.kz/',
            site_b='https://china-parts-a.kz/',
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'yandex_org', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        evidences = list(SellerLeadEvidence.objects.filter(field_name='whatsapp', value='77011234567'))
        self.assertEqual(len(evidences), 2)
        selected = [item for item in evidences if item.is_selected]
        self.assertEqual(len(selected), 1)
        self.assertIn('china-parts-a.kz', selected[0].source.source_url)
        self.assertNotIn('china-parts-b.kz', selected[0].source.source_url)
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(lead.whatsapp_source_url, selected[0].source.source_url)
        self.assertEqual(result.verified_whatsapp, ['77011234567'])

    def test_owner_verified_selected_evidence_is_not_replaced(self):
        lead = _lead(name='Owner Shop', whatsapp='77013333333', website_url='https://ownershop.kz/')
        add_seller_lead_evidence(
            lead,
            field_name='whatsapp',
            value='77013333333',
            is_selected=True,
            is_owner_verified=True,
            confidence=100,
        )

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(
                title='Owner Shop',
                body='<a href="https://wa.me/77014444444">WhatsApp</a>',
            ))

        enrich_seller_lead_contacts(lead, sources=['website'], dry_run=False, urlopen=urlopen)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77013333333')
        self.assertEqual(lead.whatsapp_source_url, '')
        selected = list(SellerLeadEvidence.objects.filter(seller_lead=lead, field_name='whatsapp', is_selected=True))
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].is_owner_verified)
        self.assertEqual(selected[0].value, '77013333333')

        same = _lead(name='Owner Shop', whatsapp='77015555555', website_url='https://same-owner.kz/')
        add_seller_lead_evidence(
            same,
            field_name='whatsapp',
            value='77015555555',
            is_selected=True,
            is_owner_verified=True,
            confidence=100,
        )

        def same_open(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(
                title='Owner Shop',
                body='<a href="https://wa.me/77015555555">WhatsApp</a>',
            ))

        enrich_seller_lead_contacts(same, sources=['website'], dry_run=False, urlopen=same_open)
        same.refresh_from_db()
        self.assertEqual(same.whatsapp, '77015555555')
        self.assertEqual(same.whatsapp_source_url, '')
        selected_same = list(SellerLeadEvidence.objects.filter(
            seller_lead=same,
            field_name='whatsapp',
            is_selected=True,
        ))
        self.assertEqual(len(selected_same), 1)
        self.assertTrue(selected_same[0].is_owner_verified)
        self.assertEqual(
            SellerLeadEvidence.objects.filter(seller_lead=same, field_name='whatsapp').count(),
            2,
        )
