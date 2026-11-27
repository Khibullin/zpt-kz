"""Explicit, idempotent admission of audited Omega opportunities to buying register.

Apify JSON processing only creates price evidence.  The manager's separate
'Загрузить выгодные' POST admits eligible offers, never verifies identity or
authorizes a purchase. Earlier frozen values remain unchanged.
"""
import re
from decimal import Decimal

from .suppliers import ACTIVE_SUPPLIER_NAMES

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Activity, KaspiPriceAudit, PermanentBuyingItem
from .omega_policy import POSITIVE_STOCK_PATTERN

ROI_GATE = Decimal("50")


def complete_session(session):
    d = session.details or {}
    batches = int(d.get("total_batches") or 0)
    return bool(batches and int(d.get("next_batch") or 0) >= batches
                and int(d.get("next_upload_batch", batches) or 0) >= batches)


def eligible_source_audits(source):
    from .minimum_price_policy import reviewed_minimum_query
    from django.db.models import Q
    return (KaspiPriceAudit.objects.filter(
        source_filename=source,
        offer__supplier__name__in=ACTIVE_SUPPLIER_NAMES,
        roi__gte=ROI_GATE,
        cost_snapshot__gt=0,
        min_price__gt=0,
    ).filter(~Q(offer__supplier__name='Phaeton DC') | reviewed_minimum_query()).select_related("offer")
      .order_by("-roi", "offer_id"))


def _eligible(audit):
    offer = audit.offer
    if not re.match(POSITIVE_STOCK_PATTERN, str(offer.stock or "")):
        return False
    if not audit.product_id or not audit.card_url:
        return False
    if not audit.card_url.startswith("https://kaspi.kz/shop/p/"):
        return False
    # Observed min_price comes from seller price entries in the recheck importer.
    # Financially eligible does not mean a verified product execution.
    return True


def source_summary(session):
    source = str((session.details or {}).get("filename") or "")[:255]
    qs = eligible_source_audits(source)
    records = list(qs)
    eligible = [a for a in records if _eligible(a)]
    ids = [a.offer_id for a in eligible]
    existing = set(PermanentBuyingItem.objects.filter(
        offer_id__in=ids).values_list("offer_id", flat=True))
    return {
        "eligible": len(eligible),
        "already": len(existing),
        "new": len(eligible) - len(existing),
        "published": bool((session.details or {}).get("buying_import_result")),
    }


def publish_profitable(session_id, user):
    with transaction.atomic():
        session = Activity.objects.select_for_update().get(
            pk=session_id, user=user, action="apify_upload_session")
        if not complete_session(session):
            raise ValidationError("Сначала завершите обработку всех партий JSON.")
        source = str((session.details or {}).get("filename") or "")[:255]
        if not source.lower().endswith(".json"):
            raise ValidationError("Неверный источник выгрузки.")
        audits = list(eligible_source_audits(source))
        added = already = skipped = 0
        for a in audits:
            if not _eligible(a):
                skipped += 1
                continue
            obj, created = PermanentBuyingItem.objects.get_or_create(
                offer_id=a.offer_id,
                defaults={
                    "source": "apify",
                    "product_id": a.product_id,
                    "title": a.card_title or a.offer.name,
                    "kaspi_url": a.card_url,
                    "cost_snapshot": a.cost_snapshot,
                    "kaspi_price_snapshot": a.min_price,
                    "commission_snapshot": a.commission_snapshot,
                    "expenses_snapshot": a.expenses_snapshot,
                    "profit_snapshot": a.profit,
                    "roi_snapshot": a.roi,
                    "identity_status": "candidate",
                    "photo_url": "",
                },
            )
            if created:
                added += 1
            else:
                already += 1
        result = {
            "added": added, "already": already, "skipped": skipped,
            "source": source,
        }
        state = dict(session.details or {})
        state["buying_import_result"] = result
        session.details = state
        session.save(update_fields=["details"])
        Activity.objects.create(
            user=user, action="apify_profitable_buying_publish",
            details={"session_id": session.pk, **result},
        )
    return result
