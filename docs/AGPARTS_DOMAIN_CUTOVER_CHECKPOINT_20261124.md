# AG Parts domain cutover — checkpoint 24 November 2026

Status: NOT DEPLOYED. The approved architecture remains the implementation target.
This document records observed state and rollback references; it is not a completion report.

## Approved scope

- ZPT.KZ: private office; AGParts.kz: public shop.
- One Django codebase, existing PostgreSQL database and photo storage.
- Work in migration/agparts-domain-split in Khibullin/zpt-kz.
- Existing Render workspace market-parts and service zpt-kz-backend.
- No new paid resources; do not delete database rows, photos or repositories.
- Suspend the old AG Parts service only after successful browser verification of both domains.

## Verified source snapshots

The following backup branches were created via GitHub on 24 November 2026:

| Repository | Backup branch | Source commit |
| --- | --- | --- |
| Khibullin/zpt-kz | backup/agparts-cutover-20261124-main | 35e3006b92f19ea4d243ad20e61793503eef49d0 |
| Khibullin/zpt-kz | backup/agparts-cutover-20261124-migration | b416d31a577d580299e1ee22281fda5c88af886d |
| Khibullin/agparts-kz | backup/agparts-cutover-20261124 | 64c2eca153d8935839d6fbeffec939f317e73bca |

These are source rollback references, NOT PostgreSQL or photo backups.
No fresh database dump, photo archive or Render configuration restore was performed.

## Verified Render service configuration

Workspace ID: tea-d5l6gqn5r7bs73ciequ0.

| Service | ID | Configured branch | Auto deploy | Suspension |
| --- | --- | --- | --- | --- |
| zpt-kz-backend | srv-d7isi7dckfvc73fcp8v0 | main | enabled, on commit | not suspended |
| agparts-kz | srv-dbc5fion74is73ff22n0 | deploy/render-preview | off | not suspended |

ZPT uses the existing 1 GB disk dsk-d8p4pfog4nts73fj5q5g,
mounted at /opt/render/project/src/products.
No service configuration, environment variables, DNS or domain attachment was changed.
The configured branch head is not a verified live deployment SHA.

## Findings from source inspection

The migration branch was three commits ahead of main, with no main-only commits:
16f92deb, e6356f4f and b416d31a. They prepare an opt-in host boundary and tests.

1. core/domain_boundary.py is disabled by default through DOMAIN_BOUNDARY_ENABLED.
   It blocks selected private prefixes on AGParts hosts and sends the ZPT root to
   /office/login/. It is explicitly not a complete public-route allowlist.
2. PUBLIC_SITE_ENABLED and PUBLIC_CATALOG_ENABLED default to false.
   core/public_site_suspension.py and core/minimal_public_middleware.py still block
   the storefront. Enabling the domain boundary alone does not open the shop.
   Avoid enabling all legacy routes globally without a host-specific route audit.
3. backend/urls.py mounts catalog.urls at both /market/ and /.
   Review duplicate URLs, redirects, root dispatch and all AJAX dependencies.
4. Review seller modification routes, API, cart/order/payment routes, internal
   endpoints and downloadable uploads before allowing traffic through AGParts.
   A prefix denylist alone is insufficient to establish isolation.
5. backend/media_views.py serves paths beneath MEDIA_ROOT. Audit uploaded
   procurement documents and photo paths before exposing this origin publicly.
6. templates/base.html still has ZPT branding and absolute ZPT asset URLs.
   Preserve office branding while adapting the public shop to existing AG Parts assets.
7. core/seo.py uses PUBLIC_BASE_URL; stored sitemap snapshots in core/seo_views.py
   must be isolated by the new canonical origin so old ZPT snapshots cannot leak
   into the AGParts sitemap. Audit structured data and hardcoded absolute URLs.
8. Default ALLOWED_HOSTS does not include agparts.kz. Check actual Render settings,
   CSRF origins and host-only session cookies before cutover.
9. Read the existing office rules in AGENTS.md. Do not change procurement pricing,
   balances, stock, Kaspi publication or communications as part of domain QA.

## Required continuation

1. Restore an execution environment and Cloud Browser access. The browser opened
   Render's sign-in screen, but authentication did not finish. Do not assume a
   signed-in browser session exists.
2. Before production changes, obtain verified existing backup/recovery points for
   PostgreSQL and photos; save non-secret service/domain/DNS configuration and
   actual live deploy IDs. Keep secrets out of Git and logs.
3. Implement host-scoped public route access, office isolation, media protection,
   AG Parts branding, canonical/robots/sitemap and reviewed redirect mappings.
4. Run Django checks and meaningful host-boundary/SEO tests; verify procurement,
   supplier comparisons, purchase lists, stock and Kaspi without sending real
   messages, placing orders or publishing Kaspi listings.
5. Deploy using the existing service only. Through the requested Cloud Browser
   Render flow, attach agparts.kz and www only after checking the existing
   attachments and authoritative DNS. Record exact before/after settings.
6. Verify HTTPS, catalogue, exact product cards, images, search, vehicle fitment,
   public navigation and all office entry points directly in the browser on
   both domains. Verify anonymous AND authenticated attempts against private
   paths on AGParts. Confirm there are no private data responses.
7. Only after these checks succeed, suspend (do not delete) the old agparts-kz
   service. Leave its source and data intact.

## Rollback

No production rollback is currently necessary because no cutover occurred.
For a later cutover, preserve exact pre-change domain attachments, DNS and env
values before changing them. On failure restore those recorded settings and
redeploy the previously verified live commit; resume the old service if it had
been suspended. Source snapshot refs above are available for code recovery.
Do not restore an old database over new business transactions automatically.
The boundary feature flag is not a complete rollback of domains, branding or SEO.

## Blockers at this checkpoint

The Cloud Browser sign-in request was interrupted. Browser tools subsequently
became unavailable. The execution environment then failed startup with
environment_offline / 409 Conflict. Therefore no new application code was written,
no tests were run in this continuation, no live browser QA was completed, no
domain was moved, and neither service was stopped.
