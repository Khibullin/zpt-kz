import io
import json
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
from core.services.seller_contact_enrichment import (
    SellerContactEnrichmentError,
    enrich_seller_lead_contacts,
)
from core.services.seller_contact_google_places import (
    PLACE_DETAILS_FIELD_MASK,
    PLACES_FIELD_MASK,
    GooglePlacesError,
    locate_google_place,
)
from core.services.seller_contact_website import (
    MAX_RESPONSE_BYTES,
    crawl_official_website,
    parse_seller_website_html,
    website_identity_accepted,
)
from core.services.seller_discovery_sources import add_seller_lead_evidence

ENABLED = {
    'SELLER_CONTACT_ENRICHMENT_ENABLED': True,
    'SELLER_CONTACT_WEBSITE_ENABLED': True,
    'SELLER_CONTACT_GOOGLE_PLACES_ENABLED': True,
    'SELLER_CONTACT_BRAVE_ENABLED': True,
    'GOOGLE_PLACES_API_KEY': 'google-secret-key',
    'BRAVE_SEARCH_API_KEY': 'brave-secret-key',
}


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
        'website_url': 'https://chinaparts.kz/',
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


def _route(routes):
    calls = []

    def urlopen(http_request, timeout):
        calls.append(http_request)
        path = parse.urlsplit(http_request.full_url).path
        if path not in routes and http_request.full_url not in routes:
            raise AssertionError(http_request.full_url)
        payload = routes.get(http_request.full_url) or routes.get(path)
        if callable(payload):
            payload = payload(http_request)
        if isinstance(payload, Exception):
            raise payload
        return payload

    return calls, urlopen


class WebsiteExtractionTests(TestCase):
    def test_explicit_whatsapp_links_and_text(self):
        html = _html(body='''
            <a href="https://wa.me/77011234567">WhatsApp</a>
            <a href="https://api.whatsapp.com/send?phone=77019998877">Написать</a>
            <a href="tel:+77012223344">Позвонить</a>
            <a href="tel:+77015556677">WhatsApp</a>
            <p>WhatsApp: +7 701 123 45 67</p>
            <p>Телефон: +7 701 000 00 99</p>
            <a href="https://instagram.com/chinaparts">Instagram</a>
            <article><p>В статье упомянут +7 702 000 00 00 без контакта магазина.</p></article>
        ''')
        extract = parse_seller_website_html(html, page_url='https://chinaparts.kz/')
        by_field = {}
        for item in extract.contacts:
            by_field.setdefault(item.field_name, set()).add(item.value)
        self.assertEqual(by_field['whatsapp'], {'77011234567', '77019998877', '77015556677'})
        self.assertIn('77012223344', by_field['phone'])
        self.assertNotIn('77020000000', by_field.get('phone', set()))
        self.assertNotIn('77020000000', by_field.get('whatsapp', set()))
        self.assertNotIn('77010000099', by_field.get('phone', set()))
        self.assertEqual(by_field['instagram'], {'chinaparts'})
        labelled = next(item for item in extract.contacts if item.value == '77015556677')
        self.assertTrue(labelled.explicit_whatsapp)
        ordinary = next(item for item in extract.contacts if item.value == '77012223344')
        self.assertFalse(ordinary.explicit_whatsapp)
        self.assertEqual(ordinary.field_name, 'phone')

    def test_json_ld_phone_is_not_whatsapp(self):
        html = _html(body='''
            <script type="application/ld+json">
            {"@type":"LocalBusiness","name":"China Parts","telephone":"+7 701 222 33 44"}
            </script>
        ''')
        extract = parse_seller_website_html(html, page_url='https://chinaparts.kz/')
        phones = [item for item in extract.contacts if item.value == '77012223344']
        self.assertEqual(len(phones), 1)
        self.assertEqual(phones[0].field_name, 'phone')
        self.assertFalse(phones[0].explicit_whatsapp)


class WebsiteSafetyTests(TestCase):
    def test_cross_domain_redirect_is_rejected(self):
        calls, urlopen = _route({
            '/robots.txt': _Response(status=404, body='missing', headers={'Content-Type': 'text/plain'}),
            '/': _Response(status=302, body='', headers={'Location': 'https://evil.example/steal', 'Content-Type': 'text/html'}),
        })
        result = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=urlopen,
        )
        self.assertEqual(result.outcome, 'redirect_rejected')
        self.assertFalse(any(parse.urlsplit(call.full_url).hostname == 'evil.example' for call in calls))
        self.assertTrue(all(call.get_method() == 'GET' for call in calls))

    def test_oversized_response_is_rejected(self):
        huge = b'x' * (MAX_RESPONSE_BYTES + 50)

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=huge)

        result = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            urlopen=urlopen,
        )
        self.assertEqual(result.outcome, 'truncated')
        self.assertEqual(result.contacts, [])

    def test_timeout_challenge_non_html_and_robots(self):
        def timeout_open(http_request, timeout):
            raise error.URLError('timed out')

        timed = crawl_official_website('https://chinaparts.kz/', lead_name='China Parts', urlopen=timeout_open)
        self.assertEqual(timed.outcome, 'error')
        self.assertIn('Таймаут', timed.error)

        def challenge_open(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body='<html>cf-challenge enable javascript and cookies</html>')

        blocked = crawl_official_website('https://chinaparts.kz/', lead_name='China Parts', urlopen=challenge_open)
        self.assertEqual(blocked.outcome, 'challenge')
        self.assertEqual(blocked.contacts, [])

        def pdf_open(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body='%PDF', headers={'Content-Type': 'application/pdf'})

        ignored = crawl_official_website('https://chinaparts.kz/', lead_name='China Parts', urlopen=pdf_open)
        self.assertEqual(ignored.outcome, 'ignored')

        calls = []

        def robots_open(http_request, timeout):
            calls.append(parse.urlsplit(http_request.full_url).path)
            path = calls[-1]
            if path == '/robots.txt':
                return _Response(
                    body='User-agent: *\nDisallow: /contacts\n',
                    headers={'Content-Type': 'text/plain'},
                )
            if path == '/contacts':
                raise AssertionError('robots disallow')
            self.assertEqual(http_request.get_method(), 'GET')
            return _Response(body=_html(body='<a href="/contacts">Контакты</a><form action="/submit"></form>'))

        crawled = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=robots_open,
        )
        self.assertNotIn('/contacts', calls)
        self.assertNotIn('/submit', calls)
        self.assertLessEqual(crawled.pages_fetched, 5)
        self.assertTrue(crawled.identity_accepted)


@override_settings(**ENABLED)
class GooglePlacesStorageTests(TestCase):
    def _places_router(self, places, website_html=None):
        calls = []

        def urlopen(http_request, timeout):
            calls.append(http_request)
            if http_request.full_url.endswith(':searchText'):
                self.assertEqual(http_request.get_method(), 'POST')
                self.assertEqual(http_request.headers['X-goog-fieldmask'], PLACES_FIELD_MASK)
                self.assertNotIn('nationalPhoneNumber', PLACES_FIELD_MASK)
                self.assertNotIn('reviews', PLACES_FIELD_MASK.casefold())
                return _Response(
                    body=json.dumps({'places': places}),
                    headers={'Content-Type': 'application/json'},
                )
            if '/places/' in http_request.full_url and http_request.get_method() == 'GET':
                self.assertEqual(http_request.headers['X-goog-fieldmask'], PLACE_DETAILS_FIELD_MASK)
                return _Response(
                    body=json.dumps(places[0]),
                    headers={'Content-Type': 'application/json'},
                )
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=website_html or _html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        return calls, urlopen

    def test_place_id_is_saved_and_google_content_is_not(self):
        lead = _lead(website_url='')
        place = {
            'id': 'places/ChIJchina',
            'displayName': {'text': 'Совсем другое имя'},
            'formattedAddress': 'Алматы, ул. Абая, 10',
            'websiteUri': 'https://chinaparts.kz/',
            'nationalPhoneNumber': '8 701 999 88 77',
            'internationalPhoneNumber': '+7 701 999 88 77',
            'location': {'latitude': 43.2, 'longitude': 76.9},
        }
        calls, urlopen = self._places_router(
            [{
                **place,
                'displayName': {'text': 'China Parts'},
            }],
        )
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(result.google_place_id, 'places/ChIJchina')
        self.assertEqual(lead.name, 'China Parts')
        self.assertNotEqual(lead.whatsapp, '77019998877')
        self.assertFalse(SellerLeadEvidence.objects.filter(value__contains='77019998877').exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='address').exists())
        self.assertFalse(SellerLeadLocation.objects.exists())
        google = SellerLeadSource.objects.get(provider='google_places')
        self.assertEqual(google.external_id, 'places/ChIJchina')
        self.assertEqual(google.source_url, '')
        self.assertEqual(google.display_name, '')
        blob = json.dumps(google.metadata)
        self.assertNotIn('websiteUri', blob)
        self.assertNotIn('77019998877', blob)
        self.assertNotIn('Совсем другое', blob)
        website = SellerLeadSource.objects.get(provider='website')
        self.assertEqual(website.source_type, SellerLeadSource.SOURCE_WEBSITE)
        self.assertIn('chinaparts.kz', website.source_url)
        self.assertTrue(SellerLeadEvidence.objects.filter(field_name='whatsapp', value='77011234567').exists())
        self.assertTrue(any(call.full_url.startswith('https://chinaparts.kz') for call in calls))

    def test_ambiguous_google_match_is_not_stored(self):
        lead = _lead(website_url='')
        place = {
            'displayName': {'text': 'China Parts'},
            'formattedAddress': 'Алматы, ул. Абая, 10',
            'websiteUri': 'https://chinaparts.kz/',
        }
        calls, urlopen = self._places_router([
            {**place, 'id': 'places/one'},
            {**place, 'id': 'places/two', 'websiteUri': 'https://china-parts.kz/'},
        ])
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places'],
            dry_run=False,
            urlopen=urlopen,
        )
        self.assertEqual(result.outcome, 'ambiguous_google')
        self.assertFalse(SellerLeadSource.objects.filter(provider='google_places').exists())
        self.assertEqual(len(calls), 1)

    def test_weak_name_is_not_auto_linked_and_429_is_not_retried(self):
        lead = _lead(name='Chery запчасти', website_url='')
        calls, urlopen = self._places_router([{
            'id': 'places/chery',
            'displayName': {'text': 'Chery'},
            'formattedAddress': 'Алматы',
            'websiteUri': 'https://chery.example/',
            'nationalPhoneNumber': '+7 701 000 11 22',
        }])
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places'],
            dry_run=False,
            urlopen=urlopen,
        )
        self.assertEqual(result.google_place_id, '')
        self.assertFalse(SellerLeadSource.objects.exists())
        self.assertFalse(SellerLeadEvidence.objects.exists())

        def rate_limited(http_request, timeout):
            calls.append(http_request)
            raise error.HTTPError(
                http_request.full_url,
                429,
                'rate',
                hdrs={'Content-Type': 'application/json'},
                fp=io.BytesIO(b'{"error":{"message":"rate google-secret-key"}}'),
            )

        calls.clear()
        with self.assertRaises(GooglePlacesError) as ctx:
            locate_google_place(name='China Parts', city='Алматы', urlopen=rate_limited)
        self.assertEqual(len(calls), 1)
        self.assertIn('429', str(ctx.exception))
        self.assertNotIn('google-secret-key', str(ctx.exception))

    def test_google_website_uri_does_not_confirm_a_weak_site(self):
        lead = _lead(name='Zapchasti', website_url='', whatsapp='')
        now = timezone.now()
        SellerLeadLocation.objects.create(
            seller_lead=lead,
            city='Алматы',
            address='ул. Абая, 10',
            is_primary=True,
            first_seen_at=now,
            last_seen_at=now,
        )
        weak_page = (
            '<!doctype html><html><head><title>Zapchasti</title></head>'
            '<body><h1>Zapchasti</h1><p>Каталог</p></body></html>'
        )
        calls, urlopen = self._places_router([{
            'id': 'places/zap',
            'displayName': {'text': 'Zapchasti'},
            'formattedAddress': 'Алматы, ул. Абая, 10',
            'websiteUri': 'https://zapchasti.kz/',
            'nationalPhoneNumber': '+7 701 999 88 77',
        }], website_html=weak_page)
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'ambiguous_website')
        self.assertEqual(result.google_place_id, 'places/zap')
        self.assertEqual(lead.name, 'Zapchasti')
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.website_url, '')
        self.assertTrue(SellerLeadSource.objects.filter(provider='google_places', external_id='places/zap').exists())
        self.assertFalse(SellerLeadSource.objects.filter(provider='website').exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp').exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='phone').exists())
        google = SellerLeadSource.objects.get(provider='google_places')
        self.assertEqual(google.display_name, '')
        self.assertNotIn('websiteUri', json.dumps(google.metadata))
        self.assertNotIn('77019998877', json.dumps(google.metadata))
        self.assertTrue(any(call.full_url.startswith('https://zapchasti.kz') for call in calls))
        extract = parse_seller_website_html(weak_page, page_url='https://zapchasti.kz/')
        self.assertFalse(website_identity_accepted(
            extract,
            lead_name='Zapchasti',
            city='Алматы',
            address='ул. Абая, 10',
            known_domain='zapchasti.kz',
            page_domain='zapchasti.kz',
            domain_is_prior=False,
        ))

    def test_google_website_is_accepted_from_independent_lead_signals(self):
        lead = _lead(website_url='', whatsapp='77011234567')
        page = (
            '<!doctype html><html><head><title>China Parts</title></head>'
            '<body><h1>China Parts</h1><p>Алматы</p>'
            '<a href="https://wa.me/77011234567">WhatsApp</a></body></html>'
        )
        calls, urlopen = self._places_router([{
            'id': 'places/china-ok',
            'displayName': {'text': 'China Parts'},
            'formattedAddress': 'Алматы, проспект Достык, 88',
            'websiteUri': 'https://chinaparts.kz/',
            'nationalPhoneNumber': '+7 701 999 88 77',
        }], website_html=page)
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'website'],
            dry_run=False,
            urlopen=urlopen,
        )
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'enriched')
        self.assertEqual(lead.name, 'China Parts')
        self.assertEqual(lead.whatsapp, '77011234567')
        website = SellerLeadSource.objects.get(provider='website')
        self.assertEqual(website.source_type, SellerLeadSource.SOURCE_WEBSITE)
        self.assertIn('chinaparts.kz', website.source_url)
        evidence = SellerLeadEvidence.objects.get(field_name='whatsapp', value='77011234567')
        self.assertGreaterEqual(evidence.confidence, 90)
        self.assertEqual(evidence.source.provider, 'website')
        self.assertTrue(evidence.is_selected)
        google = SellerLeadSource.objects.get(provider='google_places')
        blob = json.dumps(google.metadata)
        self.assertNotIn('websiteUri', blob)
        self.assertNotIn('Достык', blob)
        self.assertNotIn('77019998877', blob)
        self.assertTrue(any(call.full_url.startswith('https://chinaparts.kz') for call in calls))


@override_settings(**ENABLED)
class ApplyAndConflictTests(TestCase):
    def test_dry_run_writes_nothing(self):
        lead = _lead()
        before = _counts()

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        result = enrich_seller_lead_contacts(lead, sources=['website'], dry_run=True, urlopen=urlopen)
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'enriched')
        self.assertFalse(result.wrote)
        self.assertEqual(result.observations[0].value, '77011234567')
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(_counts(), before)

    def test_apply_sets_explicit_whatsapp_only(self):
        lead = _lead()

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='''
                <a href="tel:+77012223344">Позвонить</a>
                <a href="https://wa.me/77011234567">WhatsApp</a>
                <a href="https://instagram.com/chinaparts">Instagram</a>
            '''))

        result = enrich_seller_lead_contacts(lead, sources=['website'], dry_run=False, urlopen=urlopen)
        lead.refresh_from_db()
        self.assertTrue(result.wrote)
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertEqual(lead.instagram_username, 'chinaparts')
        self.assertTrue(SellerLeadEvidence.objects.filter(field_name='phone', value='77012223344').exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp', value='77012223344').exists())
        self.assertEqual(Seller.objects.count(), 0)
        self.assertEqual(SellerProfile.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(SellerLeadPipelineRun.objects.count(), 0)

    def test_existing_and_owner_verified_whatsapp_are_not_replaced(self):
        lead = _lead(whatsapp='77011111111')

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77012222222">WhatsApp</a>'))

        enrich_seller_lead_contacts(lead, sources=['website'], dry_run=False, urlopen=urlopen)
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011111111')
        candidate = SellerLeadContactCandidate.objects.get(value='77012222222')
        self.assertEqual(candidate.status, SellerLeadContactCandidate.STATUS_CONFLICT)
        self.assertFalse(candidate.is_primary)

        owner = _lead(name='Owner Shop', whatsapp='77013333333', website_url='https://ownershop.kz/')
        add_seller_lead_evidence(
            owner,
            field_name='whatsapp',
            value='77013333333',
            is_selected=True,
            is_owner_verified=True,
            confidence=100,
        )

        def owner_open(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(
                title='Owner Shop',
                body='<a href="https://wa.me/77014444444">WhatsApp</a>',
            ))

        enrich_seller_lead_contacts(owner, sources=['website'], dry_run=False, urlopen=owner_open)
        owner.refresh_from_db()
        self.assertEqual(owner.whatsapp, '77013333333')
        self.assertTrue(SellerLeadEvidence.objects.get(
            seller_lead=owner,
            field_name='whatsapp',
            normalized_value='77013333333',
        ).is_owner_verified)

    def test_search_query_does_not_create_whatsapp(self):
        lead = _lead(website_url='')

        class _Brave:
            def search(self, query, count=10):
                self.query = query
                return [{
                    'title': 'China Parts',
                    'url': 'https://chinaparts.kz/',
                    'description': 'Haval запчасти WhatsApp Алматы +7 701 123 45 67',
                }]

        brave = _Brave()

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html())

        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=brave,
        )
        self.assertIn('официальный сайт', brave.query)
        self.assertFalse(any(item.field_name == 'whatsapp' for item in result.observations))
        self.assertNotIn('77011234567', [item.value for item in result.observations])

    def test_brave_only_wa_me_stays_pending_candidate(self):
        lead = _lead(website_url='')

        class _Brave:
            def search(self, query, count=10):
                return [{
                    'title': 'China Parts WhatsApp',
                    'url': 'https://wa.me/77011234567',
                    'description': 'Haval запчасти WhatsApp Алматы +7 701 000 00 00',
                }]

        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave'],
            dry_run=False,
            brave_client=_Brave(),
        )
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertFalse(result.observations[0].confirms_whatsapp)
        candidate = SellerLeadContactCandidate.objects.get(value='77011234567')
        self.assertEqual(candidate.contact_type, SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP)
        self.assertEqual(candidate.status, SellerLeadContactCandidate.STATUS_PENDING)
        self.assertEqual(candidate.confidence, 'medium')
        self.assertFalse(candidate.is_primary)
        self.assertIn('Brave', candidate.source_text)
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp', is_selected=True).exists())
        self.assertFalse(SellerLeadEvidence.objects.filter(field_name='whatsapp', confidence__gte=90).exists())
        self.assertFalse(SellerLeadSource.objects.filter(provider='website').exists())
        self.assertEqual(SellerLeadSource.objects.get(provider='brave').source_type, SellerLeadSource.SOURCE_WEB_SEARCH)
        self.assertFalse(SellerLeadContactCandidate.objects.filter(value='77010000000').exists())

    def test_website_confirms_the_same_brave_wa_me(self):
        lead = _lead()

        class _Brave:
            def search(self, query, count=10):
                return [{
                    'title': 'China Parts WhatsApp',
                    'url': 'https://wa.me/77011234567',
                    'description': '',
                }]

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=False,
            urlopen=urlopen,
            brave_client=_Brave(),
        )
        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '77011234567')
        evidence = SellerLeadEvidence.objects.get(field_name='whatsapp', is_selected=True)
        self.assertEqual(evidence.value, '77011234567')
        self.assertGreaterEqual(evidence.confidence, 90)
        self.assertEqual(evidence.source.provider, 'website')
        candidate = SellerLeadContactCandidate.objects.get(value='77011234567')
        self.assertEqual(candidate.status, SellerLeadContactCandidate.STATUS_PENDING)
        self.assertNotEqual(candidate.status, SellerLeadContactCandidate.STATUS_APPROVED)


class CommandSafetyTests(TestCase):
    def test_missing_or_both_modes_do_not_call_network(self):
        lead = _lead()

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=explode):
            with patch('core.services.seller_contact_google_places._urlopen_without_proxy', side_effect=explode):
                with self.assertRaises(CommandError):
                    call_command('enrich_seller_contacts', '--lead-id', str(lead.pk), '--source', 'website')
                with self.assertRaises(CommandError):
                    call_command(
                        'enrich_seller_contacts',
                        '--lead-id', str(lead.pk),
                        '--source', 'website',
                        '--dry-run',
                        '--apply',
                    )

    def test_flags_and_missing_google_key_block_network(self):
        lead = _lead(website_url='')

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=explode):
            with patch('core.services.seller_contact_google_places._urlopen_without_proxy', side_effect=explode):
                with override_settings(SELLER_CONTACT_ENRICHMENT_ENABLED=False):
                    with self.assertRaises(CommandError) as ctx:
                        call_command(
                            'enrich_seller_contacts',
                            '--lead-id', str(lead.pk),
                            '--source', 'google_places',
                            '--dry-run',
                        )
                self.assertIn('SELLER_CONTACT_ENRICHMENT_ENABLED', str(ctx.exception))
                with override_settings(
                    SELLER_CONTACT_ENRICHMENT_ENABLED=True,
                    SELLER_CONTACT_GOOGLE_PLACES_ENABLED=False,
                    GOOGLE_PLACES_API_KEY='google-secret-key',
                ):
                    with self.assertRaises(CommandError) as ctx:
                        call_command(
                            'enrich_seller_contacts',
                            '--lead-id', str(lead.pk),
                            '--source', 'google_places',
                            '--dry-run',
                        )
                self.assertIn('SELLER_CONTACT_GOOGLE_PLACES_ENABLED', str(ctx.exception))
                with override_settings(
                    SELLER_CONTACT_ENRICHMENT_ENABLED=True,
                    SELLER_CONTACT_GOOGLE_PLACES_ENABLED=True,
                    GOOGLE_PLACES_API_KEY='',
                ):
                    with self.assertRaises(CommandError) as ctx:
                        call_command(
                            'enrich_seller_contacts',
                            '--lead-id', str(lead.pk),
                            '--source', 'google_places',
                            '--dry-run',
                        )
                self.assertIn('GOOGLE_PLACES_API_KEY', str(ctx.exception))
                self.assertNotIn('google-secret-key', str(ctx.exception))

    def test_kolesa_is_rejected_without_network(self):
        lead = _lead()

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=explode):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'enrich_seller_contacts',
                    '--lead-id', str(lead.pk),
                    '--source', 'kolesa',
                    '--dry-run',
                )
        self.assertIn('permission', str(ctx.exception))

    def test_apply_does_not_create_seller_records(self):
        lead = _lead()

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        with override_settings(**ENABLED):
            with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=urlopen):
                call_command(
                    'enrich_seller_contacts',
                    '--lead-id', str(lead.pk),
                    '--source', 'website',
                    '--apply',
                    stdout=io.StringIO(),
                )
        self.assertEqual(Seller.objects.count(), 0)
        self.assertEqual(SellerProfile.objects.count(), 0)
        self.assertEqual(Product.objects.count(), 0)
        self.assertEqual(SellerLead.objects.get(pk=lead.pk).whatsapp, '77011234567')


class EnrichmentGuardTests(TestCase):
    def test_direct_call_without_flags_does_not_fetch(self):
        lead = _lead()

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with self.assertRaises(SellerContactEnrichmentError):
            enrich_seller_lead_contacts(lead, sources=['website'], dry_run=True, urlopen=explode)
