from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from urllib.parse import urlencode, urljoin, urlparse
from uuid import uuid4
import json
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.core.management.base import CommandError
from django.utils import timezone

from integrations.kaspi_competitors import (
    CompetitorPriceSourceError,
    CompetitorPriceSourceRateLimited,
    KaspiCompetitorOffer,
    KaspiCompetitorPriceSource,
    kaspi_public_product_id,
)
from repricer.collector_ingest import COLLECTOR_SOURCE
from repricer.competitor_display import _is_own_offer
from repricer.services import configured_own_merchants

DEFAULT_TIMEOUT_SECONDS = 10.0
MIN_SLEEP_SECONDS = 1.0
MAX_LISTINGS = 10
CONSECUTIVE_SOURCE_ERROR_LIMIT = 3
LISTINGS_PATH = "/internal/kaspi/competitor-collector/listings/"
BATCHES_PATH = "/internal/kaspi/competitor-collector/batches/"

ABORT_RATE_LIMIT = "RATE_LIMIT_429"
ABORT_ACCESS = "ACCESS_403"
ABORT_METHOD = "METHOD_405"
ABORT_SOURCE_THRESHOLD = "SOURCE_ERRORS_THRESHOLD"
SOURCE_ERROR_REASONS = frozenset(
    {"source_error", "rate_limit", "access_403", "method_405"}
)


class CollectorRemoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteListing:
    listing_id: int
    article: str
    master_sku: str
    merchant_sku: str
    last_known_our_price: int | None = None


@dataclass(frozen=True)
class CollectorManifest:
    listings: list[RemoteListing]
    own_merchant_ids: list[str]
    undercut_amount: int | None
    from_envelope: bool


@dataclass(frozen=True)
class ListingCollectResult:
    listing_id: int
    article: str
    master_sku: str
    offers_received: int
    posted: bool
    duplicate: bool
    snapshots_created: int
    skipped_reason: str = ""
    competitor_offers: int = 0
    own_only: bool = False
    product_id: str = ""
    own_seller_found: bool = False
    own_price: Decimal | None = None
    best_seller_name: str = ""
    best_seller_code: str = ""
    best_price: Decimal | None = None
    last_known_our_price: int | None = None
    recommended_price: Decimal | None = None

    @property
    def state(self) -> str:
        if self.skipped_reason == "unresolved_mapping":
            return "UNRESOLVED_MAPPING"
        if self.skipped_reason == "no_offers":
            return "NO_OFFERS"
        if self.skipped_reason == "rate_limit":
            return "RATE_LIMIT"
        if self.skipped_reason == "access_403":
            return "ACCESS_403"
        if self.skipped_reason == "method_405":
            return "METHOD_405"
        if self.skipped_reason == "source_error":
            return "SOURCE_ERROR"
        if self.skipped_reason == "scan_aborted":
            return "SCAN_ABORTED"
        if self.skipped_reason == "not in manifest":
            return "NOT_IN_MANIFEST"
        if self.own_only or (self.offers_received > 0 and self.competitor_offers == 0):
            return "NO_OTHER_OFFERS"
        if self.competitor_offers > 0:
            return "READY"
        return "NO_DATA"


@dataclass(frozen=True)
class CollectScanResult:
    listings: list[ListingCollectResult]
    abort_reason: str = ""
    kaspi_attempted: int = 0
    own_merchant_ids: tuple[str, ...] = ()
    undercut_amount: int = 300

    @property
    def aborted(self) -> bool:
        return bool(self.abort_reason)


def normalize_own_merchant_ids(values) -> set[str]:
    return {
        str(item).strip().casefold()
        for item in (values or [])
        if str(item).strip()
    }


def _parse_listing_row(row: dict) -> RemoteListing | None:
    try:
        listing_id = int(row["listing_id"])
    except (KeyError, TypeError, ValueError):
        return None
    raw_price = row.get("last_known_our_price")
    last_price = None
    if raw_price not in (None, ""):
        try:
            last_price = int(raw_price)
        except (TypeError, ValueError):
            last_price = None
    return RemoteListing(
        listing_id=listing_id,
        article=str(row.get("article") or ""),
        master_sku=str(row.get("master_sku") or ""),
        merchant_sku=str(row.get("merchant_sku") or ""),
        last_known_our_price=last_price,
    )


def parse_manifest_payload(payload) -> CollectorManifest:
    """Accept the envelope contract; legacy JSON lists are parsed without metadata."""

    if isinstance(payload, list):
        listings = []
        for row in payload:
            if isinstance(row, dict):
                parsed = _parse_listing_row(row)
                if parsed is not None:
                    listings.append(parsed)
        return CollectorManifest(
            listings=listings,
            own_merchant_ids=[],
            undercut_amount=None,
            from_envelope=False,
        )
    if not isinstance(payload, dict) or "listings" not in payload:
        raise CollectorRemoteError("Manifest must be a JSON object with listings")
    raw_listings = payload.get("listings")
    if not isinstance(raw_listings, list):
        raise CollectorRemoteError("Manifest listings must be a JSON list")
    listings = []
    for row in raw_listings:
        if isinstance(row, dict):
            parsed = _parse_listing_row(row)
            if parsed is not None:
                listings.append(parsed)
    raw_ids = payload.get("own_merchant_ids") or []
    if not isinstance(raw_ids, list):
        raise CollectorRemoteError("own_merchant_ids must be a JSON list")
    own_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
    undercut = payload.get("undercut_amount")
    undercut_amount = None
    if undercut not in (None, ""):
        try:
            undercut_amount = int(undercut)
        except (TypeError, ValueError) as exc:
            raise CollectorRemoteError("undercut_amount must be an integer") from exc
        if undercut_amount < 0:
            undercut_amount = 300
    return CollectorManifest(
        listings=listings,
        own_merchant_ids=own_ids,
        undercut_amount=undercut_amount,
        from_envelope=True,
    )


def configured_collector_token() -> str:
    return str(getattr(settings, "KASPI_COMPETITOR_COLLECTOR_TOKEN", "") or "").strip()


def configured_base_url() -> str:
    return str(getattr(settings, "ZPT_KASPI_COLLECTOR_BASE_URL", "") or "").strip().rstrip("/")


def assert_collector_base_url(url: str, *, allow_localhost: bool) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return
    if (
        allow_localhost
        and parsed.scheme == "http"
        and host in {"127.0.0.1", "localhost"}
    ):
        return
    raise CommandError(
        "ZPT_KASPI_COLLECTOR_BASE_URL must be HTTPS "
        "(http allowed only for localhost in tests/dev)."
    )


def _iso_utc(value: datetime) -> str:
    return value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ZptCollectorHttpClient:
    def __init__(self, *, base_url: str, token: str, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, *, payload: dict | None = None, query: dict | None = None):
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                data = {}
            error = data.get("error") if isinstance(data, dict) else None
            raise CollectorRemoteError(
                f"ZPT collector HTTP {status}" + (f": {error}" if error else "")
            ) from exc
        except urllib.error.URLError as exc:
            raise CollectorRemoteError(f"ZPT collector unreachable: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except ValueError as exc:
            raise CollectorRemoteError("ZPT collector returned non-JSON") from exc

    def fetch_manifest(self, listing_ids: list[int] | None = None) -> CollectorManifest:
        query = None
        if listing_ids:
            query = {"ids": ",".join(str(item) for item in listing_ids)}
        payload = self._request("GET", LISTINGS_PATH, query=query)
        return parse_manifest_payload(payload)

    def post_batch(self, payload: dict) -> dict:
        data = self._request("POST", BATCHES_PATH, payload=payload)
        if not isinstance(data, dict):
            raise CollectorRemoteError("Ingest response must be a JSON object")
        return data


def _offer_payload(offer: KaspiCompetitorOffer) -> dict:
    return {
        "seller_name": offer.seller_name,
        "seller_code": offer.seller_code,
        "price": str(offer.price),
        "position": offer.position,
        "is_available": bool(offer.is_available),
    }


def _competition_detail(
    offers: list[KaspiCompetitorOffer],
    own_ids: set[str],
) -> tuple[
    int,
    bool,
    bool,
    Decimal | None,
    str,
    str,
    Decimal | None,
]:
    own_offers: list[KaspiCompetitorOffer] = []
    others: list[KaspiCompetitorOffer] = []
    for offer in offers:
        if not offer.is_available:
            continue
        if _is_own_offer(offer.seller_code, offer.seller_name, own_ids):
            own_offers.append(offer)
        else:
            others.append(offer)
    best = min(others, key=lambda item: item.price) if others else None
    own_price = min((item.price for item in own_offers), default=None)
    own_only = bool(offers) and not others
    return (
        len(others),
        own_only,
        bool(own_offers),
        own_price,
        best.seller_name if best else "",
        best.seller_code if best else "",
        best.price if best else None,
    )


def _abort_reason_for(exc: CompetitorPriceSourceError) -> str:
    status = getattr(exc, "http_status", None)
    text = str(exc)
    if isinstance(exc, CompetitorPriceSourceRateLimited) or status == 429:
        return ABORT_RATE_LIMIT
    if "ограничил частоту" in text or "(429)" in text:
        return ABORT_RATE_LIMIT
    if status == 403 or "(403)" in text:
        return ABORT_ACCESS
    if status == 405 or "(405)" in text:
        return ABORT_METHOD
    return ""


def _skipped_for_abort(reason: str) -> str:
    if reason == ABORT_RATE_LIMIT:
        return "rate_limit"
    if reason == ABORT_ACCESS:
        return "access_403"
    if reason == ABORT_METHOD:
        return "method_405"
    return "source_error"


def _empty_result(
    listing: RemoteListing | None,
    *,
    listing_id: int,
    skipped_reason: str,
    product_id: str = "",
) -> ListingCollectResult:
    return ListingCollectResult(
        listing_id=listing_id,
        article=listing.article if listing else "",
        master_sku=listing.master_sku if listing else "",
        offers_received=0,
        posted=False,
        duplicate=False,
        snapshots_created=0,
        skipped_reason=skipped_reason,
        product_id=product_id,
        last_known_our_price=listing.last_known_our_price if listing else None,
    )


def _recommended_price(best_price: Decimal | None, undercut: int, *, foreign_offers: int) -> Decimal | None:
    if foreign_offers <= 0 or best_price is None:
        return None
    recommended = best_price - Decimal(undercut)
    if recommended <= 0:
        return None
    return recommended


def _chunks(items: list, size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _resolve_own_ids(envelope: CollectorManifest, *, all_active: bool) -> set[str]:
    if all_active:
        if not envelope.from_envelope:
            raise CollectorRemoteError(
                "all-active requires production manifest metadata "
                "(own_merchant_ids). Deploy the collector API first."
            )
        own_ids = normalize_own_merchant_ids(envelope.own_merchant_ids)
        if not own_ids:
            raise CollectorRemoteError(
                "all-active requires production own_merchant_ids; "
                "an empty list is not a valid authority."
            )
        return own_ids
    if envelope.from_envelope and envelope.own_merchant_ids:
        return normalize_own_merchant_ids(envelope.own_merchant_ids)
    own_ids, _names = configured_own_merchants()
    return own_ids


def _resolve_undercut(envelope: CollectorManifest, *, all_active: bool) -> int:
    if envelope.undercut_amount is not None:
        return envelope.undercut_amount
    if all_active:
        return 300
    raw = getattr(settings, "KASPI_REPRICER_UNDERCUT_AMOUNT", 300)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 300
    return 300 if value < 0 else value


def collect_remote_listings(
    *,
    listing_ids: list[int] | None = None,
    source: KaspiCompetitorPriceSource,
    client: ZptCollectorHttpClient,
    dry_run: bool,
    sleep_seconds: float,
    stdout=None,
    all_active: bool = False,
    batch_size: int = MAX_LISTINGS,
) -> CollectScanResult:
    if all_active:
        envelope = client.fetch_manifest()
        requested = [item.listing_id for item in envelope.listings]
    else:
        requested = list(dict.fromkeys(int(item) for item in (listing_ids or [])))
        envelope = client.fetch_manifest(requested)
    own_ids = _resolve_own_ids(envelope, all_active=all_active)
    undercut = _resolve_undercut(envelope, all_active=all_active)
    by_id = {item.listing_id: item for item in envelope.listings}
    results: list[ListingCollectResult] = []
    kaspi_queue: list[RemoteListing] = []

    for listing_id in requested:
        listing = by_id.get(listing_id)
        if listing is None:
            results.append(
                _empty_result(None, listing_id=listing_id, skipped_reason="not in manifest")
            )
            continue
        product_id = (
            kaspi_public_product_id(listing.master_sku, merchant_sku=listing.merchant_sku)
            or ""
        )
        if not product_id:
            result = _empty_result(
                listing,
                listing_id=listing.listing_id,
                skipped_reason="unresolved_mapping",
            )
            results.append(result)
            if stdout is not None:
                stdout.write(
                    f"LISTING id={listing.listing_id} article={listing.article} "
                    f"master_sku={listing.master_sku} product_id= "
                    "UNRESOLVED_MAPPING"
                )
            continue
        kaspi_queue.append(listing)

    kaspi_remaining = len(kaspi_queue)
    size = max(1, int(batch_size))
    batch_total = (len(kaspi_queue) + size - 1) // size if kaspi_queue else 0
    processed = 0
    attempted = 0
    consecutive_errors = 0
    abort_reason = ""

    def _write_listing(result: ListingCollectResult, extra: str = "") -> None:
        if stdout is None:
            return
        own = "YES" if result.own_seller_found else "NO"
        own_price = result.own_price if result.own_price is not None else "—"
        our_price = (
            result.last_known_our_price
            if result.last_known_our_price is not None
            else "—"
        )
        best_price = result.best_price if result.best_price is not None else "—"
        rec = result.recommended_price if result.recommended_price is not None else "—"
        stdout.write(
            f"LISTING id={result.listing_id} article={result.article} "
            f"product_id={result.product_id} last_known_our_price={our_price} "
            f"offers={result.offers_received} own_found={own} "
            f"own_offer_price={own_price} "
            f"foreign_offers={result.competitor_offers} "
            f"best_foreign_seller={result.best_seller_name or '—'} "
            f"best_foreign_price={best_price} "
            f"state={result.state} recommended_price={rec}"
            + (f" {extra}" if extra else "")
        )

    def _result_from_offers(
        listing: RemoteListing,
        *,
        product_id: str,
        offers: list,
        posted: bool,
        duplicate: bool,
        snapshots_created: int,
        skipped_reason: str,
        others: int,
        own_only: bool,
        own_found: bool,
        own_price: Decimal | None,
        best_name: str,
        best_code: str,
        best_price: Decimal | None,
    ) -> ListingCollectResult:
        return ListingCollectResult(
            listing_id=listing.listing_id,
            article=listing.article,
            master_sku=listing.master_sku,
            offers_received=len(offers),
            posted=posted,
            duplicate=duplicate,
            snapshots_created=snapshots_created,
            skipped_reason=skipped_reason,
            competitor_offers=others,
            own_only=own_only,
            product_id=product_id,
            own_seller_found=own_found,
            own_price=own_price,
            best_seller_name=best_name,
            best_seller_code=best_code,
            best_price=best_price,
            last_known_our_price=listing.last_known_our_price,
            recommended_price=_recommended_price(
                best_price, undercut, foreign_offers=others
            ),
        )

    for batch_number, chunk in enumerate(_chunks(kaspi_queue, size), start=1):
        if abort_reason:
            break
        if stdout is not None and batch_total:
            stdout.write(
                f"BATCH {batch_number}/{batch_total} kaspi_requests={len(chunk)}"
            )
        for listing in chunk:
            if abort_reason:
                break
            product_id = (
                kaspi_public_product_id(
                    listing.master_sku, merchant_sku=listing.merchant_sku
                )
                or ""
            )
            try:
                offers = list(
                    source.fetch_offers(
                        master_sku=listing.master_sku,
                        merchant_sku=listing.merchant_sku,
                    )
                )
            except CompetitorPriceSourceError as exc:
                attempted += 1
                hard_abort = _abort_reason_for(exc)
                skipped = _skipped_for_abort(hard_abort) if hard_abort else "source_error"
                result = _empty_result(
                    listing,
                    listing_id=listing.listing_id,
                    skipped_reason=skipped,
                    product_id=product_id,
                )
                results.append(result)
                _write_listing(result, extra=f"SOURCE_ERROR {exc}")
                if hard_abort:
                    abort_reason = hard_abort
                    break
                consecutive_errors += 1
                if consecutive_errors >= CONSECUTIVE_SOURCE_ERROR_LIMIT:
                    abort_reason = ABORT_SOURCE_THRESHOLD
                    break
            else:
                attempted += 1
                consecutive_errors = 0
                others, own_only, own_found, own_price, best_name, best_code, best_price = (
                    _competition_detail(offers, own_ids)
                )
                if not offers:
                    result = _result_from_offers(
                        listing,
                        product_id=product_id,
                        offers=offers,
                        posted=False,
                        duplicate=False,
                        snapshots_created=0,
                        skipped_reason="no_offers",
                        others=0,
                        own_only=False,
                        own_found=own_found,
                        own_price=own_price,
                        best_name="",
                        best_code="",
                        best_price=None,
                    )
                    results.append(result)
                    _write_listing(result)
                elif dry_run:
                    result = _result_from_offers(
                        listing,
                        product_id=product_id,
                        offers=offers,
                        posted=False,
                        duplicate=False,
                        snapshots_created=0,
                        skipped_reason="dry_run",
                        others=others,
                        own_only=own_only,
                        own_found=own_found,
                        own_price=own_price,
                        best_name=best_name,
                        best_code=best_code,
                        best_price=best_price,
                    )
                    results.append(result)
                    _write_listing(result, extra="DRY-RUN")
                else:
                    captured_at = timezone.now()
                    payload = {
                        "batch_id": str(uuid4()),
                        "listing_id": listing.listing_id,
                        "master_sku": listing.master_sku,
                        "captured_at": _iso_utc(captured_at),
                        "offers": [_offer_payload(offer) for offer in offers],
                        "source": COLLECTOR_SOURCE,
                    }
                    response = client.post_batch(payload)
                    result = _result_from_offers(
                        listing,
                        product_id=product_id,
                        offers=offers,
                        posted=True,
                        duplicate=bool(response.get("duplicate")),
                        snapshots_created=int(response.get("snapshots_created") or 0),
                        skipped_reason="",
                        others=others,
                        own_only=own_only,
                        own_found=own_found,
                        own_price=own_price,
                        best_name=best_name,
                        best_code=best_code,
                        best_price=best_price,
                    )
                    results.append(result)
                    _write_listing(
                        result,
                        extra=f"snapshots={result.snapshots_created}",
                    )
            processed += 1
            if abort_reason:
                break
            if processed < kaspi_remaining:
                time.sleep(sleep_seconds)
        if abort_reason:
            break

    seen = {item.listing_id for item in results}
    if abort_reason:
        for listing in kaspi_queue:
            if listing.listing_id in seen:
                continue
            product_id = (
                kaspi_public_product_id(
                    listing.master_sku, merchant_sku=listing.merchant_sku
                )
                or ""
            )
            result = _empty_result(
                listing,
                listing_id=listing.listing_id,
                skipped_reason="scan_aborted",
                product_id=product_id,
            )
            results.append(result)
            _write_listing(result)

    if abort_reason and stdout is not None:
        stdout.write(f"SCAN_ABORTED {abort_reason}")

    return CollectScanResult(
        listings=results,
        abort_reason=abort_reason,
        kaspi_attempted=attempted,
        own_merchant_ids=tuple(sorted(own_ids)),
        undercut_amount=undercut,
    )
