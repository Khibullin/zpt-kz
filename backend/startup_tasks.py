from __future__ import annotations

import logging
import os
from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone


logger = logging.getLogger(__name__)

PILOT_MASTER_SKUS = (
    "115801437_271928151",
    "136510902_627349511",
    "129914457_677517150",
    "120214535_560663169",
    "131096019_815347049",
    "136896550_140830184",
    "116207063_792647100",
    "835932711",
)


def _enabled(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def run_startup_tasks() -> None:
    """Run explicitly enabled deploy-time tasks before serving requests.

    Render's current ZPT web service build command does not run migrations.
    This hook is therefore deliberately opt-in via environment variables and is
    intended for the repricer pilot rollout only.  Normal starts do nothing.
    """

    run_migrations = _enabled("RUN_MIGRATIONS_ON_STARTUP")
    run_pilot = _enabled("RUN_KASPI_REPRICER_PILOT_ON_STARTUP")
    if not (run_migrations or run_pilot):
        return

    logger.warning(
        "Startup tasks enabled: migrations=%s, kaspi_repricing_pilot=%s",
        run_migrations,
        run_pilot,
    )

    # The pilot depends on new repricer tables, so it always requires migrations.
    call_command("migrate", interactive=False, verbosity=1)

    if not run_pilot:
        return

    try:
        call_command("bootstrap_kaspi_repricer_pilot", "--apply", verbosity=1)

        from repricer.models import KaspiCompetitorOfferSnapshot

        recent_cutoff = timezone.now() - timedelta(hours=12)
        already_collected = KaspiCompetitorOfferSnapshot.objects.filter(
            listing__master_sku__in=PILOT_MASTER_SKUS,
            source="kaspi_public",
            captured_at__gte=recent_cutoff,
        ).exists()
        if already_collected:
            logger.warning(
                "Kaspi repricer pilot already has recent public snapshots; "
                "startup collection skipped."
            )
            return

        call_command(
            "sync_kaspi_competitors",
            master_sku=list(PILOT_MASTER_SKUS),
            city_id=os.getenv("KASPI_REPRICER_CITY_ID", "750000000"),
            max_offers=32,
            sleep_seconds=1.0,
            verbosity=1,
        )
    except Exception:
        # Pilot collection must never make the customer-facing site unavailable.
        logger.exception("Kaspi repricer startup pilot failed; web startup continues.")
