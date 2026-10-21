# AG Parts Kaspi review — 21 October 2026

Owner authorized completion, live Kaspi checks and publication in the AG Parts conversation.

56 of 57 approved AG Parts articles have a currently observed own offer (merchant 30363568, Almaty 750000000). F188107041 has no matching own offer in the 73-item merchant storefront checked across seven pages; it remains price pending.

The frozen catalog/data/agparts_kaspi_verified_20261021.json records exact product IDs, merchant listing SKUs, public URLs, prices and observation timestamps. 50 stored public cards were checked (49 unique articles); seven additional article mappings were found in the merchant storefront. Filter offers are individual filters with matching manufacturer article and type, not maintenance sets. F4J163707010 is the owner's explicitly approved four-plug set. D20T0120700 retains its individual-plug sale unit (separate owner rule). EM2E-8121211E uses its branded card; both checked cards cost 2373 KZT.

8126100U1510-06 also has an AG Parts card incorrectly titled as an air filter. The reviewed link deliberately selects the cabin-filter card, priced at 1920 KZT. Do not switch to the 1590 KZT air-labelled card automatically.

Migration 0074 reconciles only exact owner-product listings and records source=manual_browser_review observations. It does not change Product.price, stock, orders, repricer rules or Kaspi export flags. New listings are available through the existing collector manifest; this release does not start a collector or add a schedule.

Runtime API applies only the dated explicit package approvals, and uses the latest own-seller observation from office_collector or manual_browser_review. Prices expire after 24 hours. Newer unavailable observations suppress older available quotes. The audit price JSON is never a permanent runtime fallback. Existing explicit manifest package selections take precedence.

Validation: isolated SQLite tests cover idempotent review import, product/stock preservation, exact owner ID, explicit overrides, four-pack-only F4J and suppression by newer unavailable snapshots. Verify live public API has 57 products and 56 priced merchant links after deployment; compare each against the frozen audit and inspect fresh collector updates independently.
