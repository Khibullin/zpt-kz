"""ZPT.KZ one-time import of 42 approved auto-dismantlers, 17 October 2026.
All records originate in the approved "К загрузке" sheet. Never overwrite an
existing seller; all remaining ambiguous candidates are excluded. Atomic import.
Reverse is a no-op: reversing migrations must not delete real business listings.
"""
import base64
import hashlib
import json
import re
import zlib
from datetime import datetime, time
from decimal import Decimal
from urllib.parse import urlsplit

from django.db import migrations
from django.db.models import Q

DATA_B64 = "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"
DATA_SHA256 = "6865c19fbd19514c62775dba01dd0973c211e32d98d5863096ea4ac5f41b5c2c"
HOSTS = {
    "instagram": ("instagram.com",), "telegram": ("t.me", "telegram.me"),
    "tiktok": ("tiktok.com",), "vk": ("vk.com", "vk.ru"),
    "youtube": ("youtube.com", "youtu.be"),
    "yandex": ("yandex.ru", "yandex.kz", "yandex.com"),
    "two_gis": ("2gis.ru", "2gis.kz", "2gis.com"),
}


def norm_phone(raw):
    p = re.sub(r"\D", "", str(raw or ""))
    if len(p) == 11 and p.startswith("8"):
        p = "7" + p[1:]
    return p if len(p) == 11 and p.startswith("7") else ""


def tokens(raw):
    return [p.strip() for p in re.split(r"[;|]", str(raw or "")) if p.strip()]


def safe_url(raw, source):
    try:
        parsed = urlsplit(str(raw or "").strip())
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return ""
        if not any(host == allowed or host.endswith("." + allowed) for allowed in HOSTS.get(source, ())):
            return ""
        return "https://" + host + parsed.path.rstrip("/")
    except ValueError:
        return ""


def schedule(original):
    result = []
    for chunk in tokens(str(original or "").replace(";", "|").lower()):
        m = re.fullmatch(
            r"(ежедневно|пн-пт|пн-сб|сб)\s*,?\s*(\d\d:\d\d)\s*[–-]\s*(\d\d:\d\d)"
            r"(?:,\s*перерыв\s*(\d\d:\d\d)\s*[–-]\s*(\d\d:\d\d))?", chunk
        )
        if not m:
            return []
        group, first, last, break1, break2 = m.groups()
        days = (range(7) if group == "ежедневно" else
                range(6) if group == "пн-сб" else
                range(5) if group == "пн-пт" else [5])
        opening, closing = time.fromisoformat(first), time.fromisoformat(last)
        if opening >= closing:
            return []
        intervals = [(opening, closing)]
        if break1:
            p1, p2 = time.fromisoformat(break1), time.fromisoformat(break2)
            if not opening < p1 < p2 < closing:
                return []
            intervals = [(opening, p1), (p2, closing)]
        for day in days:
            for index, (opens, closes) in enumerate(intervals):
                result.append((day, index, opens, closes))
    return result


def load(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "postgresql" or connection.settings_dict.get("NAME") != "zpt_kz":
        print("ZPT dismantlers: skipping non-production database")
        return
    # Serialize concurrent auto-deploy pre-migrate processes.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [202610170093])
        cursor.execute(
            "SELECT 1 FROM django_migrations WHERE app=%s AND name=%s LIMIT 1",
            ["core", "0093_import_20261017_dismantlers"],
        )
        if cursor.fetchone():
            print("ZPT dismantlers: migration already applied")
            return

    raw = zlib.decompress(base64.b64decode(DATA_B64))
    if hashlib.sha256(raw).hexdigest() != DATA_SHA256:
        raise RuntimeError("Approved auto-dismantler payload integrity check failed")
    entries = json.loads(raw.decode("utf-8"))
    if len(entries) != 42:
        raise RuntimeError("Expected exactly 42 approved entries")
    using = connection.alias
    Seller = apps.get_model("core", "Seller")
    Extra = apps.get_model("core", "SellerExtraPhone")
    Social = apps.get_model("core", "SellerSocialLink")
    Work = apps.get_model("core", "SellerWorkInterval")
    External = apps.get_model("core", "SellerExternalCard")
    generic = {"центр авторазбора", "авторазбор", "автозапчасти"}
    added, skipped = [], []

    for entry in entries:
        name, city = entry["name"].strip(), entry["city"].strip()
        main, second = norm_phone(entry["whatsapp"]), norm_phone(entry["phone2"])
        extras = {norm_phone(value) for value in tokens(entry["extra_phones"])}
        for url in tokens(entry["wa_urls"]):
            parsed = urlsplit(url)
            if (parsed.hostname or "").lower() == "wa.me":
                extras.add(norm_phone(parsed.path.strip("/").split("/")[0]))
        numbers = {p for p in {main, second, *extras} if p}
        source, ext_id = entry["source"], str(entry["external_id"])
        link = safe_url(entry["card_url"], source)
        if not main or not link or not ext_id:
            raise RuntimeError("Bad phone/card in approved import: " + name)

        duplicate = ""
        if External.objects.using(using).filter(source=source, external_id=ext_id).exists():
            duplicate = "source card"
        if not duplicate and Seller.objects.using(using).filter(
            Q(whatsapp__in=numbers) | Q(phone2__in=numbers) |
            Q(extra_phones__phone__in=numbers)
        ).exists():
            duplicate = "phone"
        if not duplicate and name.casefold() not in generic and Seller.objects.using(using).filter(
            name__iexact=name, city__iexact=city
        ).exists():
            duplicate = "name+city"
        if not duplicate and entry["website"] and Seller.objects.using(using).filter(
            city__iexact=city, website__iexact=entry["website"]
        ).exists():
            duplicate = "website"
        social_urls = []
        for kind in ("instagram", "telegram", "tiktok", "vk", "youtube"):
            for raw_url in tokens(entry[kind]):
                cleaned = safe_url(raw_url, kind)
                if cleaned:
                    social_urls.append((kind, cleaned))
        for kind, url in social_urls:
            if not duplicate and Social.objects.using(using).filter(
                kind=kind, url__iexact=url, seller__city__iexact=city
            ).exists():
                duplicate = "social link"
        if duplicate:
            skipped.append((name, duplicate))
            continue

        lat = Decimal(str(entry["latitude"])) if entry["latitude"] is not None else None
        lon = Decimal(str(entry["longitude"])) if entry["longitude"] is not None else None
        if (lat is None) != (lon is None):
            lat = lon = None
        if lat is not None and not (40 <= lat <= 56 and 46 <= lon <= 88):
            raise RuntimeError("Out-of-country coordinates in approved import: " + name)

        notes = "Импорт 17.10.2026; источник: " + link
        if entry["hours"]:
            notes += "; режим работы: " + entry["hours"]
        seller = Seller.objects.using(using).create(
            name=name, city=city, whatsapp=main, phone2=second,
            transport_type=entry["transport_type"], seller_type="seller",
            is_dismantler=True, sells_new_parts=None, catalog_only=True,
            is_active=True, is_paused=False, is_test_seller=False,
            receive_requests=True, all_brands=True, all_models=True,
            all_categories=True, all_countries=True,
            address=entry["address"], latitude=lat, longitude=lon,
            website=entry["website"], notes=notes,
        )
        for number in sorted(numbers - {main, second}):
            Extra.objects.using(using).create(seller_id=seller.pk, phone=number)
        for kind, url in sorted(set(social_urls)):
            Social.objects.using(using).create(seller_id=seller.pk, kind=kind, url=url)
        for day, position, opens, closes in schedule(entry["hours"]):
            Work.objects.using(using).create(
                seller_id=seller.pk, weekday=day, position=position,
                opens=opens, closes=closes, is_closed=False,
            )

        def add_source(src, eid, url):
            safe = safe_url(url, src)
            if not safe or not eid or External.objects.using(using).filter(
                source=src, external_id=eid
            ).exists():
                return
            try:
                exported = datetime.fromisoformat(entry["exported_at"])
            except (TypeError, ValueError):
                exported = None
            External.objects.using(using).create(
                seller_id=seller.pk, source=src, external_id=eid,
                card_url=safe, exported_at=exported,
            )

        add_source(source, ext_id, entry["card_url"])
        for extra_card in tokens(entry["additional_cards"]):
            segments = extra_card.split(":", 2)
            if len(segments) == 3:
                add_source(*segments)
        added.append((seller.pk, name))

    print("ZPT dismantlers 17.10.2026: added={}, skipped={}".format(
        len(added), len(skipped)
    ))
    for sid, name in added:
        print("ADDED", sid, name)
    for name, reason in skipped:
        print("SKIPPED", name, reason)


class Migration(migrations.Migration):
    dependencies = [("core", "0092_alter_sellerworkinterval_weekday")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
