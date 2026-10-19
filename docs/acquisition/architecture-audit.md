# ZPT.KZ — Acquisition architecture audit (issue #82)

Date: 2026-10-09. Source: `main` repository files as inspected, not production metrics. This document is an audit/design deliverable; **no deployment or publishing is authorized**.

## What exists (verified)

| Area | Evidence | Reuse decision |
|---|---|---|
| SEO routes | `backend/urls.py`: `robots.txt`, `sitemap.xml`, `sitemap-static.xml`, `sitemap-products.xml` | Extend, do not replace |
| Canonical and noindex | `core/seo.py`: `canonical_path`, `robots_directive`, `seo_context` | Reuse; regression tests |
| Product sitemap | `core/seo_views.py`: only active products with slug, title, brand, category, image and description >= 40 characters; de-duplicates public slugs | Preserve filter; test scalability |
| Sitemap feature flag | `backend/settings.py`: `SEO_PRODUCT_SITEMAP_ENABLED` (code default True) | Keep independently controllable |
| Static indexable paths | `core/seo_views.py`: selected `/avtozapchasti/` brand/category pages | Audit destination content and 200/indexable state |
| Home SEO links | `catalog/views.py`: `_build_home_seo_links` points mainly to `?brand=`, `?category=`, `?city=` filters | Improve links to approved crawlable landing pages when available; do not index arbitrary filtered URLs |
| Instagram publication model | `core/models.py`: `InstagramPublication` requires `Request` FK, has placement `story/feed`, approval/publish state, unique(request, placement) | **Do not reuse directly for editorial content without separate source model/adapter** |
| Instagram controls | `backend/settings.py`: `INSTAGRAM_PUBLISH_MODE`, `INSTAGRAM_FEED_PUBLISH_ENABLED`, `INSTAGRAM_DAILY_PUBLISH_LIMIT` | Preserve existing protections |
| Marketing | `marketing/models.py`, `marketing/urls.py`: audiences, campaigns and WhatsApp templates | Keep distinct from SEO and social editorial workflow |
| Buyer requests | `core/urls.py`, `core/models.py`: request flows | Integrate attribution conservatively |

## Key constraints and findings

1. A public SEO article must be a real, useful server-rendered ZPT page, not an Instagram crosspost. Content is only eligible for sitemap on editorial approval/publication; draft and review states must not be indexed.
2. Current product sitemap eligibility is **not** proof of correct fitment. Editorial generation must use independently verified applicability evidence, including generation/engine if relevant.
3. The Instagram model is request-specific. Editorial materials must not create fake requests or alter unique constraints for existing publications.
4. Existing filtered search pages are explicitly noindex; do not undermine this by letting `?q` and `?brand` act as keyword landing pages.
5. Images must be only preexisting assets with usage rights; no image creation/editing without explicit authorization.
6. Search Console/GA4 connection status, actual indexing, structured data, conversion tracking, and existing cron/worker framework **have not been verified by this code audit**. Do not present these as installed.
7. Do not add Celery just because it was proposed: first audit existing scheduling/execution infrastructure.

## Implementation plan (separate incremental PRs)

### A. SEO/public content foundation
- Inventory existing catalog route names, tested SEO pages and existing JSON-LD; assess overlap with proposed article URLs.
- Define `EditorialPage` or reuse an existing content model after searching all apps: immutable slug, page type, source references, approved content, editor, status, published/updated timestamps.
- Use a non-conflicting namespace such as `/guide/parts/<slug>/` only after URL conflict audit.
- Output self-canonical, title, description, heading, related verified products, clear CTA to product or request; add `noindex` for all unpublished previews, private access.
- Add a separate `sitemap-content.xml` only if enabled and with approved public pages; sitemap index includes it under feature flag.
- Test unpublished exclusion, sitemap XML escaping, published 200, collision prevention, canonical, redirects, output escaping, permission checks, no duplicate pages.
- Default new content publishing OFF in deployment config.

### B. Candidate + drafting pipeline
- Define candidate source + key, dedupe key, priority rationale, data provenance, draft/version and approval audit.
- Seed with small verified catalog/request signals; later expand to Search Console if connected.
- Require verified make/model/engine/art number/compatibility fields where relevant. If uncertainty remains, fail closed and require review.
- Candidate selection deterministic and bounded; LLM optional with token/cost caps and validation. Never invent inventory, fitment or price.

### C. Editorial social integration
- Separate `EditorialSocialPublication` (or suitable existing general model) and an adapter to existing Meta publisher; keep request-specific `InstagramPublication` intact.
- Allow feed/story with explicit media rights and asset validation; Reels only with existing eligible video and explicit supported mode.
- Reuse rate limit, publish mode, retry/idempotency, moderation. No auto-publish without approved item and explicit feature enable.
- Connect channel UTM links to public ZPT canonical target; separate engagement from actual confirmed orders.

### D. Attribution, measurement, rollout
- Track first/last touch conservatively, using non-sensitive campaign/click identifiers and consent-aware collection; keep a clear source confidence flag for WhatsApp off-platform conversions.
- Define event funnel: qualified pageview → product click → request submitted/checkout → confirmed order, where observable.
- Pilot 20–30 curated pages and <= 15 reviewed Instagram posts; observe GSC indexing/impressions and actual requests, not assumed Google rankings.
- Ship behind flags with rollback; no automatic deployment until reviewed.

## Validation checklist prior to first production release
- [ ] Scan complete URL patterns and existing template metadata/schema coverage.
- [ ] Confirm environment-specific index flags and sitemap URLs via staging/production HTTP (read only).
- [ ] Confirm availability/permissions of GSC and GA4; do not require them for v1 page delivery.
- [ ] Identify scheduled jobs/worker on Render; select current mechanism.
- [ ] Verify privacy and UTM policy.
- [ ] Automated Django tests and template checks; ensure existing product sitemap, Instagram requests and seller workflow pass.
- [ ] Document migration, flag defaults, rollback and release approval.

## Next engineering change recommended
A small **code PR** that safely adds the editorial page foundation and its tests after route/model inventory, separately from Instagram or AI generation. This audit PR changes documentation only.
