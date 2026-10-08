import io
import json
import socket
import ssl
from datetime import timedelta
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
from core.management.commands.enrich_seller_contacts import _observation_source_label
from core.services.seller_contact_enrichment import (
    EnrichmentObservation,
    SellerContactEnrichmentError,
    _brave_queries,
    _store_whatsapp,
    enrich_seller_lead_contacts,
)
from core.services.seller_contact_google_places import (
    PLACE_DETAILS_FIELD_MASK,
    PLACES_FIELD_MASK,
    GooglePlaceLocator,
    GooglePlacesError,
    locate_google_place,
)
from core.services.seller_contact_website import (
    MAX_RESPONSE_BYTES,
    _instagram_handle_visible_in_text,
    crawl_official_website,
    parse_seller_website_html,
    website_identity_accepted,
)
from core.services.seller_discovery_sources import add_seller_lead_evidence, upsert_seller_lead_source
from core.services.seller_lead_enrichment_selection import select_leads_needing_enrichment

ENABLED = {
    'SELLER_CONTACT_ENRICHMENT_ENABLED': True,
    'SELLER_CONTACT_WEBSITE_ENABLED': True,
    'SELLER_CONTACT_GOOGLE_PLACES_ENABLED': True,
    'SELLER_CONTACT_BRAVE_ENABLED': True,
    'GOOGLE_PLACES_API_KEY': 'google-secret-key',
    'BRAVE_SEARCH_API_KEY': 'brave-secret-key',
}


_dns_calls = []


def _public_getaddrinfo(host, port, *args, **kwargs):
    _dns_calls.append(host)
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


class BraveQueryPriorityTests(TestCase):
    def test_exact_instagram_handle_queries_run_before_generic_name_queries(self):
        lead = SellerLead(
            name='Автозапчасти Hyundai/Kia (@almaty_parts.kz)',
            instagram_username='almaty_parts.kz',
            city='Алматы',
        )

        queries = _brave_queries(lead)

        self.assertEqual(queries[0], '"almaty_parts.kz" WhatsApp')
        self.assertEqual(queries[1], '"almaty_parts.kz" Алматы контакты')
        self.assertEqual(queries[2], '"almaty_parts.kz" официальный сайт')
        self.assertTrue(
            all('Автозапчасти Hyundai/Kia' not in query for query in queries[:3])
        )


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

    def test_trailing_whatsapp_label_is_explicit_on_contact_page(self):
        html = _html(body='''
            <p><span>+7 (708) 486-94-04</span></p>
            <p><span>+7 (747) 232-32-90</span><span> — только WhatsApp</span></p>
        ''')
        extract = parse_seller_website_html(html, page_url='https://autobahn.kz/kontaktyi/')
        whatsapp = [
            item for item in extract.contacts
            if item.field_name == 'whatsapp'
        ]
        self.assertEqual([item.value for item in whatsapp], ['77472323290'])
        self.assertTrue(whatsapp[0].explicit_whatsapp)
        self.assertGreaterEqual(whatsapp[0].confidence, 90)

    def test_trailing_whatsapp_label_is_not_inferred_across_homepage_markup(self):
        html = _html(body='''
            <div><span>+7 (708) 486-94-04</span><span> — только WhatsApp</span></div>
            <div><span>+7 (747) 232-32-90</span></div>
        ''')
        extract = parse_seller_website_html(html, page_url='https://autobahn.kz/')
        self.assertFalse(any(
            item.field_name == 'whatsapp'
            for item in extract.contacts
        ))

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

    def test_generic_hrefs_are_not_instagram(self):
        html = _html(body='''
            <a href="javascript:void(0);">Instagram</a>
            <a href="mailto:info@solidgroup.kz">Email</a>
            <a href="tel:+77020080000">Позвонить</a>
            <a href="/contacts">Контакты</a>
            <a href="./contacts">Ещё</a>
            <a href="#">Туда</a>
            <a href="https://facebook.com/omega">Facebook</a>
            <a href="https://youtube.com/omega">YouTube</a>
            <a href="https://t.me/omega">Telegram</a>
            <a href="https://instagram.com/omega_auto_parts">Instagram</a>
            <a href="https://m.instagram.com/omega_mobile">Mobile</a>
        ''')
        extract = parse_seller_website_html(html, page_url='https://omega-auto-parts.kz/')
        instagram = {item.value for item in extract.contacts if item.field_name == 'instagram'}
        self.assertEqual(instagram, {'omega_auto_parts', 'omega_mobile'})
        self.assertFalse(any('javascript' in item.value for item in extract.contacts))
        self.assertFalse(any('mailto' in item.value for item in extract.contacts))
        self.assertFalse(any('solidgroup' in item.value for item in extract.contacts))

    def test_production_dry_run_fixture_keeps_whatsapp_and_drops_fake_instagram(self):
        html = _html(body='''
            <a href="https://wa.me/77768266888">WhatsApp</a>
            <a href="tel:+77020080000">Позвонить</a>
            <a href="javascript:void(0);">Instagram</a>
            <a href="mailto:info@solidgroup.kz">Email</a>
            <a href="https://instagram.com/omega_auto_parts">Instagram</a>
        ''')
        extract = parse_seller_website_html(html, page_url='https://omega-auto-parts.kz/')
        by_field = {}
        for item in extract.contacts:
            by_field.setdefault(item.field_name, set()).add(item.value)
        self.assertEqual(by_field['whatsapp'], {'77768266888'})
        self.assertIn('77020080000', by_field['phone'])
        self.assertEqual(by_field['instagram'], {'omega_auto_parts'})

    def test_instagram_username_supports_a_name_match_and_does_not_accept_alone(self):
        unrelated = parse_seller_website_html(
            '<html><head><title>Other Shop</title></head><body>omega_auto_parts</body></html>',
            page_url='https://other.kz/',
        )
        self.assertFalse(website_identity_accepted(
            unrelated,
            lead_name='China Motors',
            instagram='omega_auto_parts',
        ))
        weak = parse_seller_website_html(
            '<html><head><title>Omega catalog</title></head><body>omega_auto_parts</body></html>',
            page_url='https://omega-auto-parts.kz/',
        )
        self.assertTrue(website_identity_accepted(
            weak,
            lead_name='Omega Auto Parts',
            instagram='omega_auto_parts',
        ))
        self.assertFalse(website_identity_accepted(
            weak,
            lead_name='Omega Auto Parts',
            instagram='',
        ))

    def test_visible_instagram_handle_ignores_surrounding_punctuation(self):
        handle = 'omega_auto_parts'
        visible = (
            '@omega_auto_parts',
            'omega_auto_parts.',
            'omega_auto_parts,',
            '(omega_auto_parts)',
            'Instagram: @Omega_Auto_Parts',
        )
        hidden = (
            'myomega_auto_parts',
            'omega_auto_parts_shop',
            'omega_auto_partsx',
            'xomega_auto_parts',
            'omega_auto_parts.official',
            'omega_auto_parts@example.com',
            'omega_auto_parts@domain.kz',
            'prefix omega_auto_parts@example.com suffix',
        )
        for sample in visible:
            self.assertTrue(
                _instagram_handle_visible_in_text(sample, handle),
                sample,
            )
        for sample in hidden:
            self.assertFalse(
                _instagram_handle_visible_in_text(sample, handle),
                sample,
            )
        unrelated = parse_seller_website_html(
            '<html><head><title>Other Shop</title></head><body>@omega_auto_parts</body></html>',
            page_url='https://other.kz/',
        )
        self.assertFalse(website_identity_accepted(
            unrelated,
            lead_name='China Motors',
            instagram=handle,
        ))
        weak = parse_seller_website_html(
            '<html><head><title>Omega catalog</title></head>'
            '<body>Instagram: @omega_auto_parts</body></html>',
            page_url='https://omega-motors.kz/',
        )
        self.assertTrue(website_identity_accepted(
            weak,
            lead_name='Omega Motors',
            instagram=handle,
        ))
        self.assertFalse(website_identity_accepted(
            weak,
            lead_name='Omega Motors',
            instagram='',
        ))

    def test_email_local_part_is_not_instagram_corroboration(self):
        handle = 'omega_auto_parts'
        email_page = parse_seller_website_html(
            '<html><head><title>Omega catalog</title></head>'
            '<body>omega_auto_parts@example.com</body></html>',
            page_url='https://omega-motors.kz/',
        )
        self.assertFalse(_instagram_handle_visible_in_text(
            'omega_auto_parts@example.com',
            handle,
        ))
        self.assertFalse(website_identity_accepted(
            email_page,
            lead_name='Omega Motors',
            instagram=handle,
        ))
        visible_page = parse_seller_website_html(
            '<html><head><title>Omega catalog</title></head>'
            '<body>Instagram: @omega_auto_parts</body></html>',
            page_url='https://omega-motors.kz/',
        )
        self.assertTrue(website_identity_accepted(
            visible_page,
            lead_name='Omega Motors',
            instagram=handle,
        ))


class RejectedWhatsAppGuardTests(TestCase):
    def test_manually_rejected_whatsapp_is_not_selected_again(self):
        lead = _lead(whatsapp='')
        SellerLeadContactCandidate.objects.create(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77011234567',
            confidence='high',
            status=SellerLeadContactCandidate.STATUS_REJECTED,
            source_url='https://chinaparts.kz/',
        )
        observation = EnrichmentObservation(
            field_name='whatsapp',
            value='77011234567',
            confidence=98,
            explicit_whatsapp=True,
            source_url='https://chinaparts.kz/',
            excerpt='WhatsApp',
            confirms_whatsapp=True,
            origin='website',
        )

        _store_whatsapp(lead, observation, source=None, preferred=True)

        lead.refresh_from_db()
        self.assertEqual(lead.whatsapp, '')
        self.assertFalse(
            SellerLeadEvidence.objects.filter(
                seller_lead=lead,
                field_name='whatsapp',
                normalized_value='77011234567',
                is_selected=True,
            ).exists()
        )
        candidate = SellerLeadContactCandidate.objects.get(
            seller_lead=lead,
            contact_type=SellerLeadContactCandidate.CONTACT_TYPE_WHATSAPP,
            value='77011234567',
        )
        self.assertEqual(candidate.status, SellerLeadContactCandidate.STATUS_REJECTED)


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

    def test_connection_reset_is_controlled_and_does_not_abort_crawl(self):
        calls = []

        def robots_reset_open(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            calls.append(path)
            if path == '/robots.txt':
                raise ConnectionResetError(104, 'Connection reset by peer')
            return _Response(body=_html())

        result = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=robots_reset_open,
        )
        self.assertEqual(result.outcome, 'ok')
        self.assertTrue(result.identity_accepted)
        self.assertIn('/robots.txt', calls)
        self.assertIn('/', calls)

        def page_reset_open(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(
                    body='User-agent: *\nDisallow:\n',
                    headers={'Content-Type': 'text/plain'},
                )
            raise ConnectionResetError(104, 'Connection reset by peer')

        failed = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=page_reset_open,
        )
        self.assertEqual(failed.outcome, 'error')
        self.assertIn('Соединение сброшено', failed.error)
        self.assertEqual(failed.error_kind, 'connection_reset')

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



    def test_unlinked_standard_contact_page_is_probed(self):
        calls = []

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            calls.append(path)
            if path == '/robots.txt':
                return _Response(
                    body='User-agent: *\nDisallow:\n',
                    headers={'Content-Type': 'text/plain'},
                )
            if path == '/':
                return _Response(body=_html(title='Компания АВТОБАН'))
            if path in {'/contacts', '/contact', '/kontakty'}:
                raise error.HTTPError(
                    http_request.full_url,
                    404,
                    'not found',
                    hdrs={'Content-Type': 'text/html'},
                    fp=io.BytesIO(b'not found'),
                )
            if path == '/kontaktyi/':
                return _Response(body=_html(
                    title='Компания АВТОБАН',
                    body='<a href="https://wa.me/77472323290">WhatsApp</a>',
                ))
            raise AssertionError(http_request.full_url)

        result = crawl_official_website(
            'https://autobahn.kz/',
            lead_name='Компания АВТОБАН',
            city='Алматы',
            known_domain='autobahn.kz',
            domain_is_prior=True,
            urlopen=urlopen,
        )

        self.assertEqual(result.outcome, 'ok')
        self.assertTrue(result.identity_accepted)
        self.assertEqual(result.pages_fetched, 2)
        self.assertIn('/kontaktyi/', calls)
        self.assertIn(
            '77472323290',
            [item.value for item in result.contacts if item.field_name == 'whatsapp'],
        )

    def test_foreign_contact_link_does_not_block_same_host_fallback(self):
        calls = []

        def urlopen(http_request, timeout):
            host = parse.urlsplit(http_request.full_url).hostname or ''
            path = parse.urlsplit(http_request.full_url).path
            calls.append((host, path))
            if path == '/robots.txt':
                return _Response(
                    body='User-agent: *\nDisallow:\n',
                    headers={'Content-Type': 'text/plain'},
                )
            if host == 'chinaparts.kz' and path == '/':
                return _Response(body=_html(
                    title='China Parts',
                    body='<a href="https://facebook.com/chinaparts/contact">Contact</a>',
                ))
            if host == 'chinaparts.kz' and path == '/contacts':
                return _Response(body=_html(
                    title='China Parts',
                    body='<a href="https://wa.me/77011234567">WhatsApp</a>',
                ))
            if host == 'chinaparts.kz' and path in {'/contact', '/kontakty', '/kontaktyi/'}:
                raise error.HTTPError(
                    http_request.full_url,
                    404,
                    'not found',
                    hdrs={'Content-Type': 'text/html'},
                    fp=io.BytesIO(b'not found'),
                )
            if host == 'facebook.com':
                raise AssertionError('foreign contact link must not be fetched')
            raise AssertionError(http_request.full_url)

        result = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=urlopen,
        )

        self.assertEqual(result.outcome, 'ok')
        self.assertIn(
            '77011234567',
            [item.value for item in result.contacts if item.field_name == 'whatsapp'],
        )
        self.assertFalse(any(host == 'facebook.com' for host, _path in calls))

    def test_standard_contact_page_fallback_respects_robots(self):
        calls = []

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            calls.append(path)
            if path == '/robots.txt':
                return _Response(
                    body=(
                        'User-agent: *\n'
                        'Disallow: /contact\n'
                        'Disallow: /kontakty\n'
                    ),
                    headers={'Content-Type': 'text/plain'},
                )
            if path == '/':
                return _Response(body=_html(title='China Parts'))
            if path in {'/contacts', '/contact', '/kontakty', '/kontaktyi/'}:
                raise AssertionError('robots must prevent fallback GET')
            raise AssertionError(http_request.full_url)

        result = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=urlopen,
        )

        self.assertEqual(result.outcome, 'ok')
        self.assertTrue(result.identity_accepted)
        self.assertEqual(result.pages_fetched, 1)
        self.assertNotIn('/contacts', calls)
        self.assertNotIn('/contact', calls)
        self.assertNotIn('/kontakty', calls)
        self.assertNotIn('/kontaktyi/', calls)


class _ResetOnRead:
    def __init__(self):
        self.status = 200
        self.headers = {'Content-Type': 'text/html; charset=utf-8'}

    def read(self, amount=-1):
        raise ConnectionResetError(104, 'Connection reset by peer')

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class WebsiteTransportErrorTests(TestCase):
    def test_robots_reset_does_not_abort_the_homepage(self):
        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                raise ConnectionResetError(104, 'Connection reset by peer')
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        result = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=urlopen,
        )
        self.assertEqual(result.outcome, 'ok')
        self.assertEqual(result.robots_status, 'robots_unavailable')
        self.assertEqual(result.robots_kind, 'connection_reset')
        self.assertEqual(result.contacts[0].value, '77011234567')

    def test_page_reset_is_a_finished_website_error(self):
        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _ResetOnRead()

        result = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=urlopen,
        )
        self.assertEqual(result.outcome, 'error')
        self.assertEqual(result.error_kind, 'connection_reset')
        self.assertIn('Соединение сброшено', result.error)
        self.assertEqual(result.contacts, [])

    def test_timeout_ssl_and_dns_are_classified(self):
        def timeout_open(http_request, timeout):
            raise TimeoutError('timed out')

        timed = crawl_official_website('https://chinaparts.kz/', lead_name='China Parts', urlopen=timeout_open)
        self.assertEqual(timed.error_kind, 'timeout')
        self.assertIn('Таймаут', timed.error)
        self.assertEqual(timed.robots_status, 'robots_unavailable')
        self.assertEqual(timed.robots_kind, 'timeout')

        def ssl_open(http_request, timeout):
            raise ssl.SSLError('certificate verify failed')

        secured = crawl_official_website('https://chinaparts.kz/', lead_name='China Parts', urlopen=ssl_open)
        self.assertEqual(secured.error_kind, 'ssl_error')
        self.assertIn('SSL', secured.error)

        def dns_open(http_request, timeout):
            raise socket.gaierror('name or service not known')

        missing = crawl_official_website('https://chinaparts.kz/', lead_name='China Parts', urlopen=dns_open)
        self.assertEqual(missing.error_kind, 'network_error')

    def test_http_403_is_not_a_connection_reset(self):
        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            raise error.HTTPError(
                http_request.full_url,
                403,
                'forbidden',
                hdrs={'Content-Type': 'text/html'},
                fp=io.BytesIO(b'forbidden'),
            )

        result = crawl_official_website(
            'https://chinaparts.kz/',
            lead_name='China Parts',
            urlopen=urlopen,
        )
        self.assertEqual(result.outcome, 'error')
        self.assertEqual(result.error_kind, 'http_403')
        self.assertEqual(result.error, 'Сайт ответил HTTP 403')
        self.assertNotIn('connection_reset', result.error)
        self.assertEqual(result.robots_status, '')

    def test_forbidden_site_does_not_block_the_next_candidate(self):
        lead = _lead(name='China Parts', website_url='', whatsapp='')
        calls = []

        class _Brave:
            def search(self, query, count=10):
                return [
                    {'title': 'China Parts', 'url': 'https://forbidden.kz/', 'description': ''},
                    {'title': 'China Parts', 'url': 'https://reset.kz/', 'description': ''},
                    {'title': 'China Parts', 'url': 'https://later.kz/', 'description': ''},
                ]

        def urlopen(http_request, timeout):
            calls.append(http_request.full_url)
            parts = parse.urlsplit(http_request.full_url)
            if parts.hostname == 'forbidden.kz' and parts.path != '/robots.txt':
                raise error.HTTPError(
                    http_request.full_url,
                    403,
                    'forbidden',
                    hdrs={'Content-Type': 'text/html'},
                    fp=io.BytesIO(b'forbidden'),
                )
            if parts.hostname == 'reset.kz' and parts.path == '/robots.txt':
                raise ConnectionResetError(104, 'Connection reset by peer')
            if parts.path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        with override_settings(**ENABLED):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['brave', 'website'],
                dry_run=True,
                urlopen=urlopen,
                brave_client=_Brave(),
                stop_on_verified_whatsapp=True,
            )
        self.assertTrue(any('Сайт ответил HTTP 403' in message for message in result.errors))
        self.assertTrue(any(message.startswith('robots_unavailable connection_reset') for message in result.errors))
        self.assertTrue(any('https://reset.kz/' in url and parse.urlsplit(url).path != '/robots.txt' for url in calls))
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertFalse(any('later.kz' in url for url in calls))
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(Seller.objects.count(), 0)

    def test_unreachable_website_does_not_stop_google(self):
        lead = _lead(website_url='https://down.kz/', whatsapp='')
        calls = []

        def urlopen(http_request, timeout):
            calls.append(http_request.full_url)
            host = parse.urlsplit(http_request.full_url).hostname
            if host == 'down.kz':
                raise ConnectionResetError(104, 'Connection reset by peer')
            if host == 'places.googleapis.com':
                return _Response(
                    body='{"places": []}',
                    headers={'Content-Type': 'application/json'},
                )
            raise AssertionError(http_request.full_url)

        with override_settings(**ENABLED):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['website', 'google_places'],
                dry_run=True,
                urlopen=urlopen,
            )
        self.assertTrue(any('connection_reset' in message for message in result.errors))
        self.assertTrue(any(call.startswith('https://places.googleapis.com/') for call in calls))
        self.assertEqual(result.dry_run, True)
        self.assertFalse(result.wrote)
        self.assertEqual(Seller.objects.count(), 0)




class WebsiteSsrfTests(TestCase):
    def _forbid(self, calls):
        def urlopen(http_request, timeout):
            calls.append(http_request.full_url)
            raise AssertionError(http_request.full_url)
        return urlopen

    def _serve(self, calls, *, redirect_to=''):
        def urlopen(http_request, timeout):
            calls.append(http_request.full_url)
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            page_hits = [
                url for url in calls
                if parse.urlsplit(url).path in {'', '/'}
            ]
            if redirect_to and len(page_hits) == 1:
                return _Response(
                    status=302,
                    body='',
                    headers={'Location': redirect_to, 'Content-Type': 'text/html'},
                )
            return _Response(body=_html())
        return urlopen

    def _assert_blocked(self, url):
        calls = []
        before = len(_dns_calls)
        result = crawl_official_website(url, lead_name='China Parts', urlopen=self._forbid(calls))
        self.assertEqual(result.outcome, 'error')
        self.assertEqual(calls, [])
        self.assertEqual(len(_dns_calls), before)
        return result

    def test_loopback_and_shorthand_ipv4_are_rejected_before_urlopen(self):
        self._assert_blocked('http://127.0.0.1/')
        self._assert_blocked('http://127.1/')

    def test_private_ipv4_is_rejected(self):
        self._assert_blocked('http://10.0.0.1/')

    def test_link_local_metadata_ip_is_rejected(self):
        self._assert_blocked('http://169.254.169.254/')

    def test_ipv6_loopback_is_rejected(self):
        self._assert_blocked('http://[::1]/')

    def test_hostname_mocked_to_private_ip_is_rejected(self):
        calls = []
        lookups = []

        def lookup(host, port, *args, **kwargs):
            lookups.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.20', 0))]

        with patch('core.services.seller_contact_website.socket.getaddrinfo', side_effect=lookup):
            result = crawl_official_website(
                'http://shop.example.test/',
                lead_name='Shop',
                urlopen=self._forbid(calls),
            )
        self.assertEqual(result.outcome, 'error')
        self.assertEqual(calls, [])
        self.assertEqual(lookups, ['shop.example.test', 'shop.example.test'])

    def test_hostname_with_mixed_public_and_private_ips_is_rejected(self):
        calls = []

        def lookup(host, port, *args, **kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.5', 0)),
            ]

        with patch('core.services.seller_contact_website.socket.getaddrinfo', side_effect=lookup):
            result = crawl_official_website(
                'https://shop.example/',
                lead_name='Shop',
                urlopen=self._forbid(calls),
            )
        self.assertEqual(result.outcome, 'error')
        self.assertEqual(calls, [])

    def test_public_hostname_mocked_to_public_ip_is_accepted(self):
        calls = []
        result = crawl_official_website(
            'https://example.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=self._serve(calls),
        )
        self.assertEqual(result.outcome, 'ok')
        self.assertTrue(calls)
        self.assertTrue(all(parse.urlsplit(url).hostname == 'example.kz' for url in calls))

    def test_localhost_names_are_rejected(self):
        self._assert_blocked('http://localhost/')
        self._assert_blocked('http://shop.localhost/')

    def test_local_tld_is_rejected(self):
        self._assert_blocked('http://printer.local/')

    def test_credentials_in_url_are_rejected(self):
        self._assert_blocked('http://user:pass@example.com/')

    def test_unlisted_port_is_rejected(self):
        self._assert_blocked('http://example.com:8080/')
        self._assert_blocked('https://example.com:8443/')

    def test_default_http_and_https_ports_are_accepted(self):
        https_calls = []
        https_result = crawl_official_website(
            'https://example.kz:443/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=self._serve(https_calls),
        )
        self.assertEqual(https_result.outcome, 'ok')
        self.assertTrue(any(':443' in url for url in https_calls))
        http_calls = []
        http_result = crawl_official_website(
            'http://example.kz:80/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=self._serve(http_calls),
        )
        self.assertEqual(http_result.outcome, 'ok')
        self.assertTrue(any(':80' in url for url in http_calls))

    def test_redirect_to_private_ip_is_rejected_before_second_get(self):
        calls = []
        result = crawl_official_website(
            'https://example.kz/',
            lead_name='China Parts',
            urlopen=self._serve(calls, redirect_to='http://10.0.0.1/secret'),
        )
        self.assertEqual(result.outcome, 'error')
        self.assertFalse(any(parse.urlsplit(url).hostname == '10.0.0.1' for url in calls))

    def test_redirect_to_foreign_hostname_is_rejected_before_second_get(self):
        calls = []
        result = crawl_official_website(
            'https://example.kz/',
            lead_name='China Parts',
            urlopen=self._serve(calls, redirect_to='https://other.kz/steal'),
        )
        self.assertEqual(result.outcome, 'redirect_rejected')
        self.assertFalse(any(parse.urlsplit(url).hostname == 'other.kz' for url in calls))

    def test_www_redirect_stays_on_the_same_site(self):
        calls = []
        result = crawl_official_website(
            'https://example.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=self._serve(calls, redirect_to='https://www.example.kz/'),
        )
        self.assertEqual(result.outcome, 'ok')
        self.assertTrue(any(parse.urlsplit(url).hostname == 'www.example.kz' for url in calls))

    def test_separate_co_jp_hosts_are_not_the_same_site(self):
        calls = []
        result = crawl_official_website(
            'https://one.co.jp/',
            lead_name='China Parts',
            urlopen=self._serve(calls, redirect_to='https://two.co.jp/'),
        )
        self.assertEqual(result.outcome, 'redirect_rejected')
        self.assertFalse(any(parse.urlsplit(url).hostname == 'two.co.jp' for url in calls))

    def test_contact_link_on_another_hostname_is_not_crawled(self):
        calls = []

        def urlopen(http_request, timeout):
            calls.append(http_request.full_url)
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://shop.example.kz/contacts">Контакты</a>'))

        crawl_official_website(
            'https://example.kz/',
            lead_name='China Parts',
            city='Алматы',
            urlopen=urlopen,
        )
        self.assertFalse(any(parse.urlsplit(url).hostname == 'shop.example.kz' for url in calls))

    def test_robots_fetch_obeys_ssrf_guard(self):
        calls = []
        result = crawl_official_website(
            'http://192.168.1.1/',
            lead_name='China Parts',
            urlopen=self._forbid(calls),
        )
        self.assertEqual(result.outcome, 'error')
        self.assertFalse(any(parse.urlsplit(url).path == '/robots.txt' for url in calls))
        self.assertEqual(calls, [])

    def test_dns_failure_is_not_retried(self):
        calls = []
        lookups = []

        def lookup(host, port, *args, **kwargs):
            lookups.append(host)
            raise socket.gaierror('dns failed')

        with patch('core.services.seller_contact_website.socket.getaddrinfo', side_effect=lookup):
            result = crawl_official_website(
                'https://missing.example/',
                lead_name='Shop',
                urlopen=self._forbid(calls),
            )
        self.assertEqual(result.outcome, 'error')
        self.assertIn('определить', result.error)
        self.assertEqual(calls, [])
        self.assertEqual(lookups, ['missing.example', 'missing.example'])


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

    def test_google_summary_hides_places_content(self):
        lead = _lead(website_url='')
        calls, urlopen = self._places_router([{
            'id': 'places/ChIJhidden',
            'displayName': {'text': 'China Parts'},
            'formattedAddress': 'Алматы, ул. Секретная, 1',
            'websiteUri': 'https://chinaparts.kz/',
            'nationalPhoneNumber': '+7 701 999 88 77',
            'location': {'latitude': 43.2, 'longitude': 76.9},
        }], website_html=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))
        result = enrich_seller_lead_contacts(
            lead,
            sources=['google_places', 'website'],
            dry_run=True,
            urlopen=urlopen,
        )
        google_run = next(run for run in result.source_runs if run.source == 'google_places')
        detail = google_run.detail
        self.assertEqual(detail, 'matched place_id_present=True website_candidate=True')
        self.assertNotIn('ChIJ', detail)
        self.assertNotIn('Секрет', detail)
        self.assertNotIn('999', detail)
        self.assertNotIn('43.2', detail)
        self.assertNotIn('google-secret-key', detail)

        empty_calls, empty_open = self._places_router([])
        missed = enrich_seller_lead_contacts(
            _lead(website_url=''),
            sources=['google_places'],
            dry_run=True,
            urlopen=empty_open,
        )
        missed_run = next(run for run in missed.source_runs if run.source == 'google_places')
        self.assertEqual(missed_run.detail, 'no confident match')
        self.assertNotIn('google-secret-key', missed_run.detail)

    def test_external_id_conflict_keeps_the_owner_and_other_evidence(self):
        owner = _lead(name='Owner Parts', website_url='')
        seen = timezone.now() - timedelta(days=1)
        source = upsert_seller_lead_source(
            owner,
            source_type=SellerLeadSource.SOURCE_GOOGLE_PLACES,
            provider='google_places',
            external_id='places/shared-owner',
            observed_at=seen,
        )
        source.refresh_from_db()
        owner_stamp = (
            source.seller_lead_id,
            source.external_id,
            source.last_seen_at,
            source.updated_at,
            source.display_name,
        )
        challenger = _lead(name='China Parts', website_url='https://chinaparts.kz/', whatsapp='')

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        locator = GooglePlaceLocator(place_id='places/shared-owner', website_uri='')
        with patch(
            'core.services.seller_contact_enrichment.locate_google_place',
            return_value=locator,
        ):
            result = enrich_seller_lead_contacts(
                challenger,
                sources=['google_places', 'website'],
                dry_run=False,
                urlopen=urlopen,
            )
        source.refresh_from_db()
        challenger.refresh_from_db()
        self.assertEqual(
            (
                source.seller_lead_id,
                source.external_id,
                source.last_seen_at,
                source.updated_at,
                source.display_name,
            ),
            owner_stamp,
        )
        self.assertEqual(
            SellerLeadSource.objects.filter(external_id='places/shared-owner').count(),
            1,
        )
        self.assertFalse(
            SellerLeadSource.objects.filter(seller_lead=challenger, provider='google_places').exists(),
        )
        self.assertTrue(
            SellerLeadEvidence.objects.filter(
                seller_lead=challenger,
                field_name='whatsapp',
                value='77011234567',
            ).exists(),
        )
        self.assertEqual(result.verified_whatsapp, ['77011234567'])
        self.assertTrue(result.identity_conflicts)
        match = SellerLeadDuplicateMatch.objects.get()
        self.assertEqual(match.status, SellerLeadDuplicateMatch.STATUS_POSSIBLE)
        self.assertIn('external_id', match.reasons)
        self.assertIsNone(challenger.duplicate_of_id)
        self.assertNotEqual(challenger.lifecycle_status, SellerLead.LIFECYCLE_DUPLICATE)
        self.assertEqual(challenger.lifecycle_status, SellerLead.LIFECYCLE_POSSIBLE_DUPLICATE)


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

    def test_one_lead_network_error_does_not_stop_the_next_lead(self):
        broken = _lead(name='Broken Shop', website_url='https://broken.kz/', whatsapp='')
        healthy = _lead(name='China Parts', website_url='https://chinaparts.kz/', whatsapp='')

        def urlopen(http_request, timeout):
            host = parse.urlsplit(http_request.full_url).hostname
            if host == 'broken.kz':
                raise ConnectionResetError(104, 'Connection reset by peer')
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        stdout = io.StringIO()
        with override_settings(**ENABLED):
            with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=urlopen):
                call_command(
                    'enrich_seller_contacts',
                    '--lead-id', str(broken.pk),
                    '--lead-id', str(healthy.pk),
                    '--source', 'website',
                    '--dry-run',
                    stdout=stdout,
                )
        text = stdout.getvalue()
        self.assertIn(f'#{broken.pk}', text)
        self.assertIn('connection_reset', text)
        self.assertIn('robots_unavailable', text)
        self.assertIn(f'#{healthy.pk}', text)
        self.assertIn('77011234567', text)
        self.assertIn('Dry-run: записи в базу не сохранялись.', text)
        self.assertNotIn(f'#{broken.pk} failed error', text)
        self.assertEqual(broken.whatsapp, '')
        self.assertEqual(healthy.whatsapp, '')

    def test_unexpected_lead_error_does_not_stop_the_next_lead(self):
        broken = _lead(name='Parser Shop', website_url='https://parser.kz/', whatsapp='')
        healthy = _lead(name='China Parts', website_url='https://chinaparts.kz/', whatsapp='')

        def urlopen(http_request, timeout):
            host = parse.urlsplit(http_request.full_url).hostname
            if host == 'parser.kz':
                raise RuntimeError('parser blew up')
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        stdout = io.StringIO()
        with override_settings(**ENABLED):
            with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=urlopen):
                call_command(
                    'enrich_seller_contacts',
                    '--lead-id', str(broken.pk),
                    '--lead-id', str(healthy.pk),
                    '--source', 'website',
                    '--dry-run',
                    stdout=stdout,
                )
        text = stdout.getvalue()
        self.assertIn(f'#{broken.pk} failed error RuntimeError: parser blew up', text)
        self.assertIn(f'#{healthy.pk}', text)
        self.assertIn('77011234567', text)
        self.assertEqual(Seller.objects.count(), 0)

    def test_keyboard_interrupt_is_not_swallowed(self):
        lead = _lead()

        def urlopen(http_request, timeout):
            raise KeyboardInterrupt

        with override_settings(**ENABLED):
            with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=urlopen):
                with self.assertRaises(KeyboardInterrupt):
                    call_command(
                        'enrich_seller_contacts',
                        '--lead-id', str(lead.pk),
                        '--source', 'website',
                        '--dry-run',
                        stdout=io.StringIO(),
                    )


@override_settings(**ENABLED)
class ProductionFindingTests(TestCase):
    def _pages(self, pages):
        calls = []

        def urlopen(http_request, timeout):
            calls.append(http_request)
            parts = parse.urlsplit(http_request.full_url)
            if parts.path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            host = parts.hostname or ''
            if host not in pages:
                raise AssertionError(http_request.full_url)
            return _Response(body=pages[host])

        return calls, urlopen

    def test_optoviki_is_blocked_and_is_not_a_website_source(self):
        lead = _lead(website_url='')
        calls, urlopen = self._pages({
            'omega-auto-parts.kz': _html(body='<a href="https://wa.me/77768266888">WhatsApp</a>'),
        })

        class _Brave:
            def search(self, query, count=10):
                return [
                    {'title': 'China Parts', 'url': 'https://optoviki.kz/optom', 'description': ''},
                    {'title': 'China Parts', 'url': 'https://www.optoviki.kz/optom-avtozapchasti/almaty', 'description': ''},
                    {'title': 'China Parts', 'url': 'https://omega-auto-parts.kz/', 'description': ''},
                ]

        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=False,
            urlopen=urlopen,
            brave_client=_Brave(),
        )
        hosts = {(parse.urlsplit(call.full_url).hostname or '') for call in calls}
        self.assertNotIn('optoviki.kz', hosts)
        self.assertNotIn('www.optoviki.kz', hosts)
        self.assertTrue(any('optoviki.kz' in url for url in result.websites_skipped_blocked))
        self.assertFalse(any('optoviki.kz' in url for url in result.websites_considered))
        self.assertFalse(SellerLeadSource.objects.filter(source_url__icontains='optoviki').exists())
        self.assertEqual(result.verified_whatsapp, ['77768266888'])

    def test_third_brave_only_candidate_is_brave_cap_not_budget(self):
        lead = _lead(website_url='')
        calls, urlopen = self._pages({
            'brave-1.kz': _html(),
            'brave-2.kz': _html(),
        })

        class _Brave:
            def search(self, query, count=10):
                return [
                    {'title': 'China Parts', 'url': 'https://brave-1.kz/', 'description': ''},
                    {'title': 'China Parts', 'url': 'https://brave-2.kz/', 'description': ''},
                    {'title': 'China Parts', 'url': 'https://brave-3.kz/', 'description': ''},
                ]

        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=_Brave(),
        )
        self.assertTrue(any('brave-3.kz' in url for url in result.websites_skipped_brave_cap))
        self.assertFalse(any('brave-3.kz' in url for url in result.websites_skipped_budget))
        hosts = {(parse.urlsplit(call.full_url).hostname or '') for call in calls}
        self.assertNotIn('brave-3.kz', hosts)

    def test_verified_whatsapp_wins_over_an_ambiguous_candidate(self):
        lead = _lead(website_url='')
        calls, urlopen = self._pages({
            'omegaauto.kz': _html(title='Другой магазин', city='Астана', body='<p>контакты позже</p>'),
            'omega-auto-parts.kz': _html(body='<a href="https://wa.me/77768266888">WhatsApp</a>'),
        })

        class _Brave:
            def search(self, query, count=10):
                return [
                    {'title': 'China Parts', 'url': 'https://omegaauto.kz/', 'description': ''},
                    {'title': 'China Parts', 'url': 'https://omega-auto-parts.kz/', 'description': ''},
                ]

        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=_Brave(),
        )
        self.assertEqual(result.outcome, 'enriched')
        self.assertEqual(result.verified_whatsapp, ['77768266888'])
        self.assertTrue(any(
            'ambiguous website candidate: https://omegaauto.kz/' in message
            for message in result.errors
        ))

    def test_two_verified_whatsapp_numbers_conflict(self):
        lead = _lead(website_url='')
        calls, urlopen = self._pages({
            'one.kz': _html(body='<a href="https://wa.me/77011111111">WhatsApp</a>'),
            'two.kz': _html(body='<a href="https://wa.me/77012222222">WhatsApp</a>'),
        })

        class _Brave:
            def search(self, query, count=10):
                return [
                    {'title': 'China Parts', 'url': 'https://one.kz/', 'description': ''},
                    {'title': 'China Parts', 'url': 'https://two.kz/', 'description': ''},
                ]

        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=_Brave(),
        )
        self.assertEqual(result.outcome, 'conflict')
        self.assertEqual(result.verified_whatsapp, [])
        self.assertEqual(set(result.conflicts), {'77011111111', '77012222222'})

    def test_instagram_handle_is_a_brave_query_and_not_enough_alone(self):
        lead = _lead(website_url='', instagram_username='omega_auto_parts')
        queries = []

        class _Brave:
            def search(self, query, count=10):
                queries.append(query)
                return []

        enrich_seller_lead_contacts(
            lead,
            sources=['brave'],
            dry_run=True,
            brave_client=_Brave(),
        )
        self.assertTrue(any('omega_auto_parts' in query for query in queries))
        official_at = next(index for index, query in enumerate(queries) if 'официальный сайт' in query)
        handle_at = next(index for index, query in enumerate(queries) if 'omega_auto_parts' in query)
        self.assertLess(official_at, handle_at)

    def test_official_site_query_is_not_displaced_by_noisy_handle_results(self):
        lead = _lead(website_url='', instagram_username='omega_auto_parts')
        calls, urlopen = self._pages({
            'official-seller.kz': _html(body='<a href="https://wa.me/77768266888">WhatsApp</a>'),
            'noise-1.kz': _html(title='Другой магазин', city='Астана', body='<p>каталог</p>'),
        })
        queries = []

        class _Brave:
            def search(self, query, count=10):
                queries.append(query)
                if 'официальный сайт' in query:
                    return [{'title': 'China Parts', 'url': 'https://official-seller.kz/', 'description': ''}]
                if 'omega_auto_parts' in query:
                    return [
                        {'title': 'Noise', 'url': 'https://noise-1.kz/', 'description': ''},
                        {'title': 'Noise', 'url': 'https://noise-2.kz/', 'description': ''},
                    ]
                return []

        result = enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=True,
            urlopen=urlopen,
            brave_client=_Brave(),
        )
        official_at = next(index for index, query in enumerate(queries) if 'официальный сайт' in query)
        handle_at = next(index for index, query in enumerate(queries) if 'omega_auto_parts' in query)
        self.assertLess(official_at, handle_at)
        considered_hosts = {
            parse.urlsplit(url).hostname for url in result.websites_considered
        }
        self.assertIn('official-seller.kz', considered_hosts)
        self.assertFalse(any('official-seller.kz' in url for url in result.websites_skipped_brave_cap))
        self.assertTrue(any('noise-2.kz' in url for url in result.websites_skipped_brave_cap))
        fetched_hosts = {(parse.urlsplit(call.full_url).hostname or '') for call in calls}
        self.assertIn('official-seller.kz', fetched_hosts)
        self.assertNotIn('noise-2.kz', fetched_hosts)

    def test_two_gis_stdout_uses_external_id_when_url_is_empty(self):
        observation = type('Obs', (), {'source_url': '', 'origin': 'two_gis'})()
        self.assertEqual(_observation_source_label(observation, '70000001000000001'), '2gis:70000001000000001')
        linked = type('Obs', (), {'source_url': 'https://wa.me/77768266888', 'origin': 'brave'})()
        self.assertEqual(_observation_source_label(linked, ''), 'https://wa.me/77768266888')

    def test_command_stdout_shows_source_and_duplicate_phone(self):
        lead = _lead()

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='''
                <a href="https://wa.me/77768266888">WhatsApp</a>
                <a href="tel:+77768266888">тот же</a>
                <a href="tel:+77020080000">другой</a>
            '''))

        stdout = io.StringIO()
        with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=urlopen):
            call_command(
                'enrich_seller_contacts',
                '--lead-id', str(lead.pk),
                '--source', 'website',
                '--dry-run',
                stdout=stdout,
            )
        text = stdout.getvalue()
        self.assertIn('[whatsapp] 77768266888', text)
        self.assertIn('origin=website source=https://chinaparts.kz/', text)
        self.assertIn('[phone] 77768266888', text)
        self.assertIn('(same as verified WhatsApp)', text)
        other = next(line for line in text.splitlines() if '[phone] 77020080000' in line)
        self.assertNotIn('same as verified WhatsApp', other)
        self.assertNotIn('google-secret-key', text)
        self.assertNotIn('brave-secret-key', text)

    def test_command_google_summary_does_not_print_places_content(self):
        lead = _lead(website_url='')

        def urlopen(http_request, timeout):
            if http_request.full_url.endswith(':searchText'):
                return _Response(
                    body=json.dumps({'places': [{
                        'id': 'places/ChIJhidden',
                        'displayName': {'text': 'China Parts'},
                        'formattedAddress': 'Алматы, ул. Секретная, 1',
                        'websiteUri': 'https://chinaparts.kz/',
                        'nationalPhoneNumber': '+7 701 999 88 77',
                    }]}),
                    headers={'Content-Type': 'application/json'},
                )
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        stdout = io.StringIO()
        with patch('core.services.seller_contact_google_places._urlopen_without_proxy', side_effect=urlopen):
            with patch('core.services.seller_contact_website._urlopen_without_proxy', side_effect=urlopen):
                call_command(
                    'enrich_seller_contacts',
                    '--lead-id', str(lead.pk),
                    '--source', 'google_places',
                    '--source', 'website',
                    '--dry-run',
                    stdout=stdout,
                )
        text = stdout.getvalue()
        self.assertIn(
            'source google_places: executed matched place_id_present=True website_candidate=True',
            text,
        )
        self.assertNotIn('ChIJ', text)
        self.assertNotIn('Секрет', text)
        self.assertNotIn('999 88 77', text)
        self.assertNotIn('google-secret-key', text)


@override_settings(**ENABLED)
class ApplyPersistenceTests(TestCase):
    def _open(self, pages):
        def urlopen(http_request, timeout):
            parts = parse.urlsplit(http_request.full_url)
            if parts.path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            host = parts.hostname or ''
            if host not in pages:
                raise AssertionError(http_request.full_url)
            return _Response(body=pages[host])

        return urlopen

    def _apply_official_site(self, lead, *, body='<p>Алматы, контакты магазина</p>'):
        class _Brave:
            def search(self, query, count=10):
                return [{
                    'title': 'China Parts',
                    'url': 'https://omega-auto-parts.kz/',
                    'description': '',
                }]

        return enrich_seller_lead_contacts(
            lead,
            sources=['brave', 'website'],
            dry_run=False,
            urlopen=self._open({
                'omega-auto-parts.kz': _html(body=body),
                'kept-shop.kz': _html(title='Другой магазин', city='Астана', body='<p>каталог</p>'),
            }),
            brave_client=_Brave(),
        )

    def test_accepted_website_is_saved_and_normalizes_domain(self):
        lead = _lead(website_url='', lifecycle_status=SellerLead.LIFECYCLE_FOUND)
        self.assertEqual(lead.normalized_domain, '')
        result = self._apply_official_site(
            lead,
            body='<a href="https://wa.me/77768266888">WhatsApp</a>',
        )
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'enriched')
        self.assertEqual(lead.website_url, 'https://omega-auto-parts.kz/')
        self.assertEqual(lead.normalized_domain, 'omega-auto-parts.kz')
        self.assertEqual(lead.whatsapp, '77768266888')
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_ENRICHED)
        self.assertIsNotNone(lead.last_enriched_at)

    def test_successful_apply_keeps_later_lifecycle_and_existing_website(self):
        for status in (
            SellerLead.LIFECYCLE_ENRICHED,
            SellerLead.LIFECYCLE_CLASSIFIED,
            SellerLead.LIFECYCLE_READY_TO_INVITE,
            SellerLead.LIFECYCLE_VERIFIED,
            SellerLead.LIFECYCLE_ACTIVE,
        ):
            with self.subTest(status=status):
                lead = _lead(
                    name=f'China Parts {status}',
                    website_url='https://kept-shop.kz/',
                    lifecycle_status=status,
                )
                self._apply_official_site(lead)
                lead.refresh_from_db()
                self.assertEqual(lead.lifecycle_status, status)
                self.assertEqual(lead.website_url, 'https://kept-shop.kz/')
                self.assertEqual(lead.normalized_domain, 'kept-shop.kz')
                self.assertIsNotNone(lead.last_enriched_at)
                self.assertEqual(lead.whatsapp, '')

    def test_empty_apply_does_not_promote_found(self):
        lead = _lead(website_url='https://other.kz/', lifecycle_status=SellerLead.LIFECYCLE_FOUND)
        result = enrich_seller_lead_contacts(
            lead,
            sources=['website'],
            dry_run=False,
            urlopen=self._open({
                'other.kz': _html(title='Другой магазин', city='Астана', body='<p>каталог</p>'),
            }),
        )
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'ambiguous_website')
        self.assertEqual(result.observations, [])
        self.assertEqual(lead.lifecycle_status, SellerLead.LIFECYCLE_FOUND)
        self.assertIsNone(lead.last_enriched_at)
        self.assertEqual(lead.website_url, 'https://other.kz/')


class SkippedSourceAttemptTests(TestCase):
    def test_source_all_with_every_source_disabled_does_not_stamp_attempt(self):
        lead = _lead(website_url='', whatsapp='')

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=False,
            SELLER_CONTACT_GOOGLE_PLACES_ENABLED=False,
            SELLER_CONTACT_BRAVE_ENABLED=False,
            SELLER_CONTACT_2GIS_ENABLED=False,
            SELLER_CONTACT_YANDEX_ENABLED=False,
        ):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['all'],
                dry_run=False,
                urlopen=explode,
            )
        lead.refresh_from_db()
        self.assertFalse(result.wrote)
        self.assertTrue(result.source_runs)
        self.assertTrue(all(run.status == 'skipped_disabled' for run in result.source_runs))
        self.assertIsNone(lead.last_enrichment_attempt_at)
        self.assertIsNone(lead.last_enriched_at)

    def test_requested_sources_that_never_execute_do_not_stamp_attempt(self):
        lead = _lead(website_url='', whatsapp='')

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=False,
            SELLER_CONTACT_GOOGLE_PLACES_ENABLED=True,
            SELLER_CONTACT_BRAVE_ENABLED=True,
            SELLER_CONTACT_2GIS_ENABLED=True,
            SELLER_CONTACT_YANDEX_ENABLED=True,
            GOOGLE_PLACES_API_KEY='',
            BRAVE_SEARCH_API_KEY='',
            TWO_GIS_API_KEY='',
            YANDEX_ORG_SEARCH_API_KEY='',
        ):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['all'],
                dry_run=False,
                urlopen=explode,
            )
        lead.refresh_from_db()
        self.assertFalse(result.wrote)
        self.assertTrue(result.source_runs)
        self.assertTrue(all(run.status.startswith('skipped_') for run in result.source_runs))
        self.assertFalse(any(run.status == 'executed' for run in result.source_runs))
        self.assertIsNone(lead.last_enrichment_attempt_at)
        self.assertIsNone(lead.last_enriched_at)

    def test_website_without_a_candidate_is_not_executed(self):
        lead = _lead(name='Empty Shop', website_url='', whatsapp='')

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=True,
        ):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['website'],
                dry_run=False,
                urlopen=explode,
            )
        lead.refresh_from_db()
        self.assertEqual([run.status for run in result.source_runs], ['skipped_no_candidate'])
        self.assertFalse(result.wrote)
        self.assertEqual(result.websites_considered, [])
        self.assertIsNone(lead.last_enrichment_attempt_at)
        self.assertIsNone(lead.last_enriched_at)

    def test_crawled_website_without_contacts_is_executed(self):
        lead = _lead(name='China Parts', website_url='https://chinaparts.kz/', whatsapp='')

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<p>Каталог без контактов</p>'))

        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=True,
        ):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['website'],
                dry_run=False,
                urlopen=urlopen,
            )
        lead.refresh_from_db()
        self.assertEqual([run.status for run in result.source_runs], ['executed'])
        self.assertTrue(result.websites_considered)
        self.assertEqual(result.observations, [])
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(SellerLeadContactCandidate.objects.filter(seller_lead=lead).count(), 0)
        self.assertIsNotNone(lead.last_enrichment_attempt_at)

    def test_website_without_a_candidate_dry_run_writes_nothing(self):
        lead = _lead(name='Dry Shop', website_url='', whatsapp='')
        before = _counts()

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=True,
        ):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['website'],
                dry_run=True,
                urlopen=explode,
            )
        lead.refresh_from_db()
        self.assertEqual([run.status for run in result.source_runs], ['skipped_no_candidate'])
        self.assertFalse(result.wrote)
        self.assertEqual(_counts(), before)
        self.assertIsNone(lead.last_enrichment_attempt_at)
        self.assertIsNone(lead.last_enriched_at)

    def test_website_without_a_candidate_stays_selectable(self):
        lead = _lead(name='Retry Shop', website_url='', whatsapp='')
        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=True,
        ):
            enrich_seller_lead_contacts(lead, sources=['website'], dry_run=False)
        lead.refresh_from_db()
        self.assertIsNone(lead.last_enrichment_attempt_at)
        self.assertIn(lead, list(select_leads_needing_enrichment(limit=5)))

    def test_executed_source_with_useful_enrichment_stamps_both_timestamps(self):
        lead = _lead(website_url='https://chinaparts.kz/', whatsapp='')

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=True,
        ):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['website'],
                dry_run=False,
                urlopen=urlopen,
            )
        lead.refresh_from_db()
        self.assertEqual([run.status for run in result.source_runs], ['executed'])
        self.assertEqual(lead.whatsapp, '77011234567')
        self.assertIsNotNone(lead.last_enrichment_attempt_at)
        self.assertIsNotNone(lead.last_enriched_at)

    def test_dry_run_with_executed_source_does_not_change_timestamps(self):
        lead = _lead(website_url='https://chinaparts.kz/', whatsapp='')
        stamp = timezone.now() - timedelta(days=3)
        lead.last_enrichment_attempt_at = stamp
        lead.last_enriched_at = stamp
        lead.save(update_fields=['last_enrichment_attempt_at', 'last_enriched_at', 'updated_at'])
        lead.refresh_from_db()
        attempt_at = lead.last_enrichment_attempt_at
        enriched_at = lead.last_enriched_at

        def urlopen(http_request, timeout):
            path = parse.urlsplit(http_request.full_url).path
            if path == '/robots.txt':
                return _Response(body='User-agent: *\nDisallow:\n', headers={'Content-Type': 'text/plain'})
            return _Response(body=_html(body='<a href="https://wa.me/77011234567">WhatsApp</a>'))

        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=True,
        ):
            result = enrich_seller_lead_contacts(
                lead,
                sources=['website'],
                dry_run=True,
                urlopen=urlopen,
            )
        lead.refresh_from_db()
        self.assertEqual(result.source_runs[0].status, 'executed')
        self.assertFalse(result.wrote)
        self.assertEqual(lead.whatsapp, '')
        self.assertEqual(lead.last_enrichment_attempt_at, attempt_at)
        self.assertEqual(lead.last_enriched_at, enriched_at)

    def test_skipped_all_sources_lead_stays_selectable(self):
        lead = _lead(name='Skipped Shop', website_url='', whatsapp='')
        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=False,
            SELLER_CONTACT_GOOGLE_PLACES_ENABLED=False,
            SELLER_CONTACT_BRAVE_ENABLED=False,
            SELLER_CONTACT_2GIS_ENABLED=False,
            SELLER_CONTACT_YANDEX_ENABLED=False,
        ):
            enrich_seller_lead_contacts(lead, sources=['all'], dry_run=False)
        lead.refresh_from_db()
        self.assertIsNone(lead.last_enrichment_attempt_at)
        selected = list(select_leads_needing_enrichment(limit=5))
        self.assertIn(lead, selected)

    def test_ambiguous_google_apply_reports_the_attempt_write(self):
        lead = _lead(name='Ambiguous Shop', website_url='', whatsapp='')
        stamp = timezone.now() - timedelta(days=3)
        lead.last_enrichment_attempt_at = stamp
        lead.save(update_fields=['last_enrichment_attempt_at', 'updated_at'])
        lead.refresh_from_db()
        before = lead.last_enrichment_attempt_at
        locator = GooglePlaceLocator(place_id='', website_uri='', ambiguous=True, error='неоднозначно')
        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_GOOGLE_PLACES_ENABLED=True,
            GOOGLE_PLACES_API_KEY='test-key',
        ):
            with patch(
                'core.services.seller_contact_enrichment.locate_google_place',
                return_value=locator,
            ):
                result = enrich_seller_lead_contacts(lead, sources=['google_places'], dry_run=False)
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'ambiguous_google')
        self.assertTrue(result.wrote)
        self.assertGreater(lead.last_enrichment_attempt_at, before)
        self.assertIsNone(lead.last_enriched_at)

    def test_ambiguous_google_dry_run_does_not_write(self):
        lead = _lead(name='Ambiguous Dry', website_url='', whatsapp='')
        stamp = timezone.now() - timedelta(days=3)
        lead.last_enrichment_attempt_at = stamp
        lead.last_enriched_at = stamp
        lead.save(update_fields=['last_enrichment_attempt_at', 'last_enriched_at', 'updated_at'])
        lead.refresh_from_db()
        attempt_at = lead.last_enrichment_attempt_at
        enriched_at = lead.last_enriched_at
        locator = GooglePlaceLocator(place_id='', website_uri='', ambiguous=True, error='неоднозначно')
        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_GOOGLE_PLACES_ENABLED=True,
            GOOGLE_PLACES_API_KEY='test-key',
        ):
            with patch(
                'core.services.seller_contact_enrichment.locate_google_place',
                return_value=locator,
            ):
                result = enrich_seller_lead_contacts(lead, sources=['google_places'], dry_run=True)
        lead.refresh_from_db()
        self.assertEqual(result.outcome, 'ambiguous_google')
        self.assertFalse(result.wrote)
        self.assertEqual(lead.last_enrichment_attempt_at, attempt_at)
        self.assertEqual(lead.last_enriched_at, enriched_at)

    def test_skipped_sources_keep_wrote_false_and_the_timestamp(self):
        lead = _lead(name='Skipped Stamp', website_url='', whatsapp='')
        stamp = timezone.now() - timedelta(days=3)
        lead.last_enrichment_attempt_at = stamp
        lead.save(update_fields=['last_enrichment_attempt_at', 'updated_at'])
        lead.refresh_from_db()
        attempt_at = lead.last_enrichment_attempt_at
        with override_settings(
            SELLER_CONTACT_ENRICHMENT_ENABLED=True,
            SELLER_CONTACT_WEBSITE_ENABLED=False,
            SELLER_CONTACT_GOOGLE_PLACES_ENABLED=False,
            SELLER_CONTACT_BRAVE_ENABLED=False,
            SELLER_CONTACT_2GIS_ENABLED=False,
            SELLER_CONTACT_YANDEX_ENABLED=False,
        ):
            result = enrich_seller_lead_contacts(lead, sources=['all'], dry_run=False)
        lead.refresh_from_db()
        self.assertFalse(result.wrote)
        self.assertEqual(lead.last_enrichment_attempt_at, attempt_at)
        self.assertIsNone(lead.last_enriched_at)


class EnrichmentGuardTests(TestCase):
    def test_direct_call_without_flags_does_not_fetch(self):
        lead = _lead()

        def explode(*args, **kwargs):
            raise AssertionError('network')

        with self.assertRaises(SellerContactEnrichmentError):
            enrich_seller_lead_contacts(lead, sources=['website'], dry_run=True, urlopen=explode)
