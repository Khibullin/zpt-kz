from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
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
    KaspiCompetitorOffer,
    KaspiCompetitorPriceSource,
)
from repricer.collector_ingest import COLLECTOR_SOURCE

DEFAULT_TIMEOUT_SECONDS = 10.0
MIN_SLEEP_SECONDS = 1.0
MAX_LISTINGS = 10
LISTINGS_PATH = "/internal/kaspi/competitor-collector/listings/"
BATCHES_PATH = "/internal/kaspi/competitor-collector/batches/"


class CollectorRemoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteListing:
    listing_id: int
    article: str
    master_sku: str
    merchant_sku: str


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

    def fetch_manifest(self, listing_ids: list[int]) -> list[RemoteListing]:
        query = {"ids": ",".join(str(item) for item in listing_ids)}
        payload = self._request("GET", LISTINGS_PATH, query=query)
        if not isinstance(payload, list):
            raise CollectorRemoteError("Manifest must be a JSON list")
        listings = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            listings.append(
                RemoteListing(
                    listing_id=int(row["listing_id"]),
                    article=str(row.get("article") or ""),
                    master_sku=str(row.get("master_sku") or ""),
                    merchant_sku=str(row.get("merchant_sku") or ""),
                )
            )
        return listings

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


def collect_remote_listings(
    *,
    listing_ids: list[int],
    source: KaspiCompetitorPriceSource,
    client: ZptCollectorHttpClient,
    dry_run: bool,
    sleep_seconds: float,
    stdout=None,
) -> list[ListingCollectResult]:
    requested = list(dict.fromkeys(int(item) for item in listing_ids))
    manifest = client.fetch_manifest(requested)
    by_id = {item.listing_id: item for item in manifest}
    results: list[ListingCollectResult] = []

    for index, listing_id in enumerate(requested):
        listing = by_id.get(listing_id)
        if listing is None:
            results.append(
                ListingCollectResult(
                    listing_id=listing_id,
                    article="",
                    master_sku="",
                    offers_received=0,
                    posted=False,
                    duplicate=False,
                    snapshots_created=0,
                    skipped_reason="not in manifest",
                )
            )
            continue
        try:
            offers = list(
                source.fetch_offers(
                    master_sku=listing.master_sku,
                    merchant_sku=listing.merchant_sku,
                )
            )
        except CompetitorPriceSourceError as exc:
            if stdout is not None:
                stdout.write(
                    f"{listing.article} | master_sku={listing.master_sku} | SOURCE_ERROR {exc}"
                )
            results.append(
                ListingCollectResult(
                    listing_id=listing.listing_id,
                    article=listing.article,
                    master_sku=listing.master_sku,
                    offers_received=0,
                    posted=False,
                    duplicate=False,
                    snapshots_created=0,
                    skipped_reason="source_error",
                )
            )
        else:
            if not offers:
                if stdout is not None:
                    stdout.write(
                        f"{listing.article} | master_sku={listing.master_sku} | NO_OFFERS"
                    )
                results.append(
                    ListingCollectResult(
                        listing_id=listing.listing_id,
                        article=listing.article,
                        master_sku=listing.master_sku,
                        offers_received=0,
                        posted=False,
                        duplicate=False,
                        snapshots_created=0,
                        skipped_reason="no_offers",
                    )
                )
            elif dry_run:
                if stdout is not None:
                    stdout.write(
                        f"{listing.article} | master_sku={listing.master_sku} | "
                        f"DRY-RUN offers={len(offers)}"
                    )
                    for offer in offers:
                        stdout.write(
                            f"  pos={offer.position} {offer.seller_code} "
                            f"{offer.seller_name} {offer.price}"
                        )
                results.append(
                    ListingCollectResult(
                        listing_id=listing.listing_id,
                        article=listing.article,
                        master_sku=listing.master_sku,
                        offers_received=len(offers),
                        posted=False,
                        duplicate=False,
                        snapshots_created=0,
                        skipped_reason="dry_run",
                    )
                )
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
                results.append(
                    ListingCollectResult(
                        listing_id=listing.listing_id,
                        article=listing.article,
                        master_sku=listing.master_sku,
                        offers_received=len(offers),
                        posted=True,
                        duplicate=bool(response.get("duplicate")),
                        snapshots_created=int(response.get("snapshots_created") or 0),
                    )
                )
                if stdout is not None:
                    stdout.write(
                        f"{listing.article} | master_sku={listing.master_sku} | "
                        f"offers={len(offers)} | snapshots={results[-1].snapshots_created}"
                    )
        if index < len(requested) - 1:
            time.sleep(sleep_seconds)
    return results
