# AG Parts → ZPT.KZ SEO redirect map

Status date: 2026-09-10

Purpose: preserve organic search signals when retiring or consolidating `agparts.kz` into `zpt.kz`.

## Rules

- Use HTTP 301 only after the ZPT destination is live, indexable, canonical to itself, and materially matches the old page intent.
- Never redirect a strong old category/product URL to a ZPT search/filter query because catalog query URLs are intentionally `noindex, follow`.
- Prefer exact product → exact product. If no exact product target is verified, keep the old URL live (`HOLD`) rather than redirecting it to a generic page.
- Preserve query-free destination URLs and avoid redirect chains.
- After cutover, keep redirects permanently and submit/recheck the ZPT sitemap in Google Search Console.

## READY — category / landing redirects

| Source on agparts.kz | Destination on zpt.kz | Status | Reason |
| --- | --- | --- | --- |
| `/` | `/seller/ag-parts/` | READY_AT_SITE_CUTOVER | The old root represents the AG Parts store; the public AG Parts seller page is the closest semantic replacement on ZPT. |
| `/changan` | `/avtozapchasti/changan/` | READY | Dedicated indexable Changan landing exists on ZPT. |
| `/chery` | `/avtozapchasti/chery/` | READY | Dedicated indexable Chery landing exists on ZPT. |
| `/haval` | `/avtozapchasti/haval/` | READY | Dedicated indexable Haval landing exists on ZPT. |
| `/zeekr` | `/avtozapchasti/zeekr/` | READY | Dedicated indexable Zeekr landing exists on ZPT. |
| `/oilfilters` | `/avtozapchasti/maslyanye-filtry/` | READY | Dedicated indexable oil-filter landing exists on ZPT. |

## READY — exact product redirects

| Source on agparts.kz | Destination on zpt.kz | Article | Status |
| --- | --- | --- | --- |
| `/product-page/filterscheryexceedjetour` | `/chery-tiggo-7-t151109111/` | `T151109111` | READY |
| `/product-page/воздушный-фильтр-1109190cr01` | `/changan-uni-k-1109190cr01/` | `1109190CR01` | READY |

When configuring the source platform, use the exact URL-encoded form actually served by Wix for non-ASCII paths; the readable paths above are identifiers for the mapping.

## HOLD — indexed product URLs needing an exact ZPT target check

| Source on agparts.kz | Article | Status | Required before 301 |
| --- | --- | --- | --- |
| `/product-page/sparks-chery` | `F4J16-3707010` / current ZPT article may be normalized as `F4J163707010` | HOLD | Verify the live canonical ZPT product URL and that it is the same product/pack quantity. |
| `/product-page/салонный-фильтр-8126100u1510-06` | `8126100U1510-06` | HOLD | Verify exact live ZPT product URL and applicability. |
| `/product-page/салонный-фильтр-8126100u851025` | `8126100U851025` | HOLD | Verify exact live ZPT product URL and applicability. |

## Discovery notes

The current search index still exposes the AG Parts root page, Changan, Chery, Haval, Zeekr, oil-filter category, and multiple individual product pages. These URLs therefore must not be collapsed blindly to the ZPT home page.

Search discovery did not reliably surface separate indexed `/geely`, `/jac`, `/jetour`, `/exeed`, or `/byd` landing URLs in the current pass. Do not infer that they do not exist; run another indexed-URL export/search before final domain cutover.

## Cutover checklist

1. Export indexed/known AG Parts URLs from Google Search Console if available and merge them into this map.
2. Resolve every `HOLD` row to an exact ZPT destination or intentionally leave the old URL available.
3. Verify every destination returns 200 and is `index, follow` with a self-canonical.
4. Configure path-level 301 redirects at the AG Parts/Wix/domain layer.
5. Verify no 301 → 301 chains and no redirect to `noindex` filter/query pages.
6. Keep `agparts.kz` domain/DNS and redirect service active long-term.
7. Monitor Google Search Console for indexing, redirect errors, 404s, and canonical changes after cutover.
