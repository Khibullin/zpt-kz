"""One-off production audit of the current ZPT seller directory.

The campaign is deliberately conservative:
* every SellerLead in the 2026-10-08 snapshot is reclassified from stored evidence;
* public sites already attached to leads are crawled once for explicit contacts;
* a small set of manually verified facts is applied with provenance;
* registered Seller/SellerProfile rows are normalized without changing their
  self-declared assortment;
* messages are never sent and Seller/User/Product rows are never created.

The minute marketing cron temporarily calls this module so the campaign can
finish in bounded batches on Render.  Once complete the caller is removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone

from django.test.utils import override_settings
from django.utils import timezone

from catalog.models import SellerProfile
from core.models import Seller, SellerLead, SellerLeadSource
from core.phone_utils import normalize_kz_phone
from core.services.seller_contact_enrichment import (
    EnrichmentObservation,
    SellerContactEnrichmentError,
    _store_whatsapp,
    enrich_seller_lead_contacts,
)
from core.services.seller_discovery_identity import refresh_seller_lead_identity
from core.services.seller_discovery_sources import upsert_seller_lead_source
from core.services.seller_lead_classification import classify_seller_lead

CAMPAIGN_STARTED_AT = datetime(2026, 10, 8, 12, 25, 0, tzinfo=dt_timezone.utc)
CAMPAIGN_TAG = 'seller_directory_audit_2026_10_08'

# Snapshot taken from production immediately before the campaign.
CAMPAIGN_LEAD_IDS = (
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
    20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36,
    37, 38, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54,
    55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71,
    72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88,
    89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104,
    105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118,
    119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129,
)

# Corrections are based on explicit business identity in the stored Instagram
# profile/name or a matching public business page.  The discovery pipeline had
# historically defaulted many rows to Алматы, so these are identity repairs.
LEAD_CITY_FIXES = {
    17: 'Шымкент',
    25: 'Бишкек',
    29: 'Шымкент',
    38: 'Астана',
    40: 'Астана',
    46: 'Астана',
    47: 'Астана',
    48: 'Астана',
    49: 'Бишкек',
    52: 'Астана',
    54: 'Шымкент',
    58: 'Астана',
    70: 'Шымкент',
    71: 'Бишкек',
    73: 'Бишкек',
    76: 'Шымкент',
    77: 'Шымкент',
    82: 'Караганда',
    83: 'Минск',
    85: 'Астана',
    90: 'Караганда',
    91: 'Бишкек',
    94: 'Шымкент',
    100: 'Минск',
    101: 'Минск',
    111: 'Бишкек',
}

LEAD_WEBSITE_FIXES = {
    64: 'https://autotrade.kz/',
    70: 'http://www.grandkoreamotors.kz/',
}

# Explicitly verified business type where the generic classifier cannot safely
# infer from one phrase alone.  These are only strong, directly stated cases.
LEAD_BUSINESS_TYPE_FIXES = {
    8: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    23: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    27: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    37: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    44: SellerLead.BUSINESS_TYPE_SERVICE_PARTS,
    50: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    52: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    53: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    60: SellerLead.BUSINESS_TYPE_DISMANTLER,
    64: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    66: SellerLead.BUSINESS_TYPE_DISMANTLER,
    70: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    74: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    75: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    76: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    77: SellerLead.BUSINESS_TYPE_SERVICE_PARTS,
    78: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    79: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    80: SellerLead.BUSINESS_TYPE_SERVICE_ONLY,
    81: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    82: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    84: SellerLead.BUSINESS_TYPE_SERVICE_ONLY,
    85: SellerLead.BUSINESS_TYPE_SERVICE_PARTS,
    87: SellerLead.BUSINESS_TYPE_SERVICE_ONLY,
    88: SellerLead.BUSINESS_TYPE_DISMANTLER,
    89: SellerLead.BUSINESS_TYPE_SERVICE_PARTS,
    90: SellerLead.BUSINESS_TYPE_DISMANTLER,
    92: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    93: SellerLead.BUSINESS_TYPE_OTHER_AUTO,
    94: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    95: SellerLead.BUSINESS_TYPE_DEALER,
    96: SellerLead.BUSINESS_TYPE_SERVICE_ONLY,
    99: SellerLead.BUSINESS_TYPE_OTHER_AUTO,
    102: SellerLead.BUSINESS_TYPE_SERVICE_PARTS,
    103: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    105: SellerLead.BUSINESS_TYPE_OTHER_AUTO,
    106: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    107: SellerLead.BUSINESS_TYPE_DEALER,
    109: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    110: SellerLead.BUSINESS_TYPE_SERVICE_ONLY,
    112: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    113: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    114: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    116: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    119: SellerLead.BUSINESS_TYPE_DISMANTLER,
    122: SellerLead.BUSINESS_TYPE_DISMANTLER,
    125: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    126: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    127: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    128: SellerLead.BUSINESS_TYPE_NEW_PARTS,
    129: SellerLead.BUSINESS_TYPE_NEW_PARTS,
}

# These two rows are clear discovery false positives rather than automotive
# sellers.  They remain in the audit trail but are excluded operationally.
NOT_SELLER_LEAD_IDS = (61, 104)

# Strong foreign-market identities.  They are preserved as leads, but market
# scope is explicit so they cannot be invited as Kazakhstan sellers.
FOREIGN_LEAD_IDS = (25, 49, 71, 73, 83, 91, 100, 101, 111)

# Explicit WhatsApp evidence only.  Ordinary telephone numbers are excluded.
# (number, source_url, source_type, evidence text)
LEAD_WHATSAPP_FACTS = {
    4: (
        '77002510460',
        'https://www.instagram.com/kaz_avto.kz/',
        SellerLeadSource.SOURCE_INSTAGRAM,
        'Instagram profile contains explicit wa.me/77002510460.',
    ),
    44: (
        '77072900900',
        'https://t.me/bmwservice_almaty',
        SellerLeadSource.SOURCE_DIRECTORY,
        'Public BMW Motors channel explicitly labels +7 707 290 09 00 as WhatsApp.',
    ),
    47: (
        '77479262605',
        'https://www.instagram.com/korea_motors_astana/',
        SellerLeadSource.SOURCE_INSTAGRAM,
        'Instagram profile explicitly labels +7 747 926 26 05 as WhatsApp.',
    ),
    60: (
        '77773272874',
        'https://incatalog.kz/katalog-tovarov/almaty13356/avto-almaty/avtozapchasti/autorazbor/',
        SellerLeadSource.SOURCE_DIRECTORY,
        'Public directory WhatsApp action resolves to phone=77773272874.',
    ),
    116: (
        '77071212323',
        'https://parts.kz/',
        SellerLeadSource.SOURCE_WEBSITE,
        'Official Parts.kz public page explicitly states WhatsApp +7 707 121 23 23.',
    ),
    117: (
        '77760038662',
        'https://sayman.kz/almaty',
        SellerLeadSource.SOURCE_WEBSITE,
        'Official Sayman page explicitly labels +7 776 003 86 62 as Whatsapp.',
    ),
}

# Registered sellers: only exact-phone / official-identity matches are added.
REGISTERED_PROFILE_FACTS = {
    463: {
        'expected_name': 'Автотрейд Казахстан',
        'website': 'https://autotrade.kz/',
        'instagram': 'https://www.instagram.com/autotrade_kz/',
    },
    490: {
        'expected_name': 'Kaynar Avto',
        'website': 'http://kaynar-avto.kz/',
        'instagram': 'https://www.instagram.com/kaynaravto/',
    },
    494: {
        'expected_name': 'Гараж 27',
        'instagram': 'https://www.instagram.com/garage27.kz/',
    },
}


@dataclass(frozen=True)
class SellerDirectoryAuditResult:
    registered_audited: int
    registered_changed: int
    lead_classified: int
    lead_classification_errors: int
    classification_remaining: int
    websites_scanned: int
    website_scan_errors: int
    website_scan_remaining: int
    curated_whatsapp_added: int
    curated_lead_changes: int

    @property
    def complete(self) -> bool:
        return self.classification_remaining == 0 and self.website_scan_remaining == 0


def _apply_lead_identity_facts() -> int:
    changed = 0
    for lead_id, city in LEAD_CITY_FIXES.items():
        lead = SellerLead.objects.filter(pk=lead_id).first()
        if lead is None or lead.city == city:
            continue
        lead.city = city
        lead.save(update_fields=['city', 'updated_at'])
        changed += 1

    for lead_id, website in LEAD_WEBSITE_FIXES.items():
        lead = SellerLead.objects.filter(pk=lead_id).first()
        if lead is None or (lead.website_url or '').strip():
            continue
        lead.website_url = website
        lead.save(update_fields=['website_url', 'updated_at'])
        refresh_seller_lead_identity(lead)
        changed += 1
    return changed


def _apply_post_classification_overrides() -> int:
    changed = 0
    now = timezone.now()
    for lead_id, business_type in LEAD_BUSINESS_TYPE_FIXES.items():
        lead = SellerLead.objects.filter(pk=lead_id).first()
        if lead is None or lead.business_type == business_type:
            continue
        lead.business_type = business_type
        lead.business_type_confidence = max(int(lead.business_type_confidence or 0), 90)
        lead.business_type_evidence = (
            f'{CAMPAIGN_TAG}: explicit business identity from stored/public profile'
        )
        lead.last_classified_at = now
        lead.save(update_fields=[
            'business_type',
            'business_type_confidence',
            'business_type_evidence',
            'last_classified_at',
            'updated_at',
        ])
        changed += 1

    for lead_id in FOREIGN_LEAD_IDS:
        lead = SellerLead.objects.filter(pk=lead_id).first()
        if lead is None or lead.market_scope == SellerLead.MARKET_SCOPE_FOREIGN:
            continue
        lead.market_scope = SellerLead.MARKET_SCOPE_FOREIGN
        lead.market_scope_evidence = f'{CAMPAIGN_TAG}: explicit foreign business identity'
        lead.save(update_fields=['market_scope', 'market_scope_evidence', 'updated_at'])
        changed += 1

    for lead_id in NOT_SELLER_LEAD_IDS:
        lead = SellerLead.objects.filter(pk=lead_id).first()
        if lead is None:
            continue
        fields = []
        if lead.status != SellerLead.STATUS_NOT_SELLER:
            lead.status = SellerLead.STATUS_NOT_SELLER
            fields.append('status')
        if lead.review_status != SellerLead.REVIEW_REJECTED:
            lead.review_status = SellerLead.REVIEW_REJECTED
            fields.append('review_status')
        if lead.lifecycle_status != SellerLead.LIFECYCLE_REJECTED:
            lead.lifecycle_status = SellerLead.LIFECYCLE_REJECTED
            fields.append('lifecycle_status')
        if fields:
            fields.append('updated_at')
            lead.save(update_fields=fields)
            changed += 1
    return changed


def _store_curated_whatsapp() -> int:
    added = 0
    now = timezone.now()
    for lead_id, (number, source_url, source_type, evidence_text) in LEAD_WHATSAPP_FACTS.items():
        lead = SellerLead.objects.filter(pk=lead_id).first()
        if lead is None:
            continue
        before = (lead.whatsapp or '').strip()
        source = upsert_seller_lead_source(
            lead,
            source_type=source_type,
            provider='audit',
            source_url=source_url,
            display_name=lead.name[:255],
            fetched_at=now,
            source_confidence=100,
            is_active=True,
            metadata={'verified_by': CAMPAIGN_TAG, 'role': 'contact_evidence'},
            observed_at=now,
        )
        observation = EnrichmentObservation(
            field_name='whatsapp',
            value=number,
            confidence=100,
            explicit_whatsapp=True,
            source_url=source_url,
            excerpt=evidence_text,
            confirms_whatsapp=True,
            origin='website' if source_type == SellerLeadSource.SOURCE_WEBSITE else 'audit',
        )
        _store_whatsapp(lead, observation, source=source, preferred=True)
        lead.refresh_from_db()
        after = (lead.whatsapp or '').strip()
        if after == number:
            if not before:
                added += 1
            lead.last_enrichment_attempt_at = now
            lead.last_enriched_at = now
            lead.last_enrichment_result = 'verified_whatsapp'
            lead.save(update_fields=[
                'last_enrichment_attempt_at',
                'last_enriched_at',
                'last_enrichment_result',
                'updated_at',
            ])
    return added


def _audit_registered_sellers() -> tuple[int, int]:
    audited = 0
    changed = 0
    for seller in Seller.objects.filter(user_id__isnull=False).order_by('pk'):
        audited += 1
        profile = SellerProfile.objects.filter(user_id=seller.user_id).first()
        if profile is None:
            # The campaign never creates SellerProfile rows.
            continue

        normalized = normalize_kz_phone(seller.whatsapp)
        if normalized and seller.whatsapp != normalized:
            seller.whatsapp = normalized
            seller.save(update_fields=['whatsapp', 'updated_at'])
            if profile.phone != normalized:
                profile.phone = normalized
                profile.save(update_fields=['phone'])
            changed += 1

        if not (profile.city or '').strip() and (seller.city or '').strip():
            profile.city = seller.city.strip()
            profile.save(update_fields=['city'])
            changed += 1

        fact = REGISTERED_PROFILE_FACTS.get(seller.pk)
        if not fact or seller.name.strip() != fact['expected_name']:
            continue
        profile_fields = []
        website = fact.get('website', '')
        instagram = fact.get('instagram', '')
        if website and not (profile.website or '').strip():
            profile.website = website
            profile_fields.append('website')
        if instagram and not (profile.instagram or '').strip():
            profile.instagram = instagram
            profile_fields.append('instagram')
        if profile_fields:
            profile.save(update_fields=profile_fields)
            changed += 1

    return audited, changed


def _classification_pending_queryset():
    return (
        SellerLead.objects
        .filter(pk__in=CAMPAIGN_LEAD_IDS)
        .exclude(lifecycle_status=SellerLead.LIFECYCLE_DUPLICATE)
        .filter(last_classified_at__lt=CAMPAIGN_STARTED_AT)
        | SellerLead.objects
        .filter(pk__in=CAMPAIGN_LEAD_IDS, last_classified_at__isnull=True)
        .exclude(lifecycle_status=SellerLead.LIFECYCLE_DUPLICATE)
    )


def _classify_batch(batch_size: int) -> tuple[int, int]:
    processed = 0
    errors = 0
    leads = list(_classification_pending_queryset().order_by('pk')[:batch_size])
    for lead in leads:
        try:
            classify_seller_lead(lead, promote_lifecycle=False)
            processed += 1
        except Exception:
            # Move the failed row past the campaign watermark so one bad record
            # cannot hold the entire one-off campaign open forever.
            SellerLead.objects.filter(pk=lead.pk).update(last_classified_at=timezone.now())
            errors += 1
    return processed, errors


def _website_pending_queryset():
    return (
        SellerLead.objects
        .filter(pk__in=CAMPAIGN_LEAD_IDS)
        .exclude(website_url='')
        .filter(last_enrichment_attempt_at__lt=CAMPAIGN_STARTED_AT)
        | SellerLead.objects
        .filter(
            pk__in=CAMPAIGN_LEAD_IDS,
            website_url__isnull=False,
            last_enrichment_attempt_at__isnull=True,
        )
        .exclude(website_url='')
    )


def _scan_website_batch(batch_size: int) -> tuple[int, int]:
    scanned = 0
    errors = 0
    leads = list(_website_pending_queryset().order_by('pk')[:batch_size])
    if not leads:
        return scanned, errors

    # Website crawling itself requires no paid API key.  The production feature
    # flags are overridden only inside this bounded one-off call.
    with override_settings(
        SELLER_CONTACT_ENRICHMENT_ENABLED=True,
        SELLER_CONTACT_WEBSITE_ENABLED=True,
    ):
        for lead in leads:
            try:
                enrich_seller_lead_contacts(
                    lead,
                    sources=('website',),
                    dry_run=False,
                    stop_on_verified_whatsapp=True,
                )
                scanned += 1
            except (SellerContactEnrichmentError, Exception):
                SellerLead.objects.filter(pk=lead.pk).update(
                    last_enrichment_attempt_at=timezone.now(),
                    last_enrichment_result='error',
                )
                errors += 1
    return scanned, errors


def process_seller_directory_audit_batch(
    *,
    classification_batch_size: int = 20,
    website_batch_size: int = 3,
) -> SellerDirectoryAuditResult:
    """Run one bounded idempotent batch of the directory audit."""
    curated_changes = _apply_lead_identity_facts()
    registered_audited, registered_changed = _audit_registered_sellers()

    classified, classification_errors = _classify_batch(classification_batch_size)
    curated_changes += _apply_post_classification_overrides()
    whatsapp_added = _store_curated_whatsapp()

    websites_scanned, website_errors = _scan_website_batch(website_batch_size)

    # Website enrichment can add evidence which improves classification, but
    # it must not erase manually verified business/market facts.
    curated_changes += _apply_post_classification_overrides()

    return SellerDirectoryAuditResult(
        registered_audited=registered_audited,
        registered_changed=registered_changed,
        lead_classified=classified,
        lead_classification_errors=classification_errors,
        classification_remaining=_classification_pending_queryset().count(),
        websites_scanned=websites_scanned,
        website_scan_errors=website_errors,
        website_scan_remaining=_website_pending_queryset().count(),
        curated_whatsapp_added=whatsapp_added,
        curated_lead_changes=curated_changes,
    )
