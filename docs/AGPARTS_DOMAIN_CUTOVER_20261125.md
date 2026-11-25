# Unified ZPT / AG Parts rollout — 25 November 2026

## Recovery points verified before rollout

- Render workspace market-parts: tea-d5l6gqn5r7bs73ciequ0.
- ZPT service srv-d7isi7dckfvc73fcp8v0, branch main, auto-deploy on commit.
- Pre-change live commit 1c686386b9d7f4a8f9446201a1125b3d957bce42, deployment dep-dc35qigjo6nc73eam5ng.
- GitHub backup/agparts-cutover-20261125-main preserves that commit.
- GitHub backup/agparts-cutover-20261125-migration preserves the previous migration branch.
- Old AG Parts service srv-dbc5fion74is73ff22n0, branch deploy/render-preview, auto-deploy off, live 64c2eca153d8935839d6fbeffec939f317e73bca / dep-dbraavd9fdbs73dpinvg.
- Old source backup/agparts-cutover-20261124 is retained.
- PostgreSQL dpg-d7jn5u67r5hc73b6cmu0-a: new complete logical export successfully created in Recovery, displayed timestamp 25 November 2026 04:06; file .dir.tar.gz. Signed download URL deliberately omitted.
- Existing PostgreSQL point-in-time recovery window: 3 days. No restore or paid replica created.
- Photo disk dsk-d8p4pfog4nts73fj5q5g stays mounted at /opt/render/project/src/products (1 GB). Existing Render snapshots verified, latest displayed 25 November 00:11, previous 24 November 00:13. No media rewrite or deletion is part of rollout.
- Before cutover, zpt.kz is verified/certificate issued on ZPT; www.zpt.kz waiting for DNS. agparts.kz and www.agparts.kz verified/certificates issued on old AG service; www redirects to apex.
- Domain assignments are to be moved, keeping the same total domain count and existing services. No paid resources to be created.

## Implementation

Feature switch DOMAIN_BOUNDARY_ENABLED=true enables a resolved-view allowlist on AGParts, applied to both catalog URL mounts. Office/admin/supplier mutation/internal APIs and private documents return 404 even with a staff session. Store renders as anonymous, preventing staff-only pricing or controls. ZPT root goes to the office login, retained public pages redirect to their AGParts counterparts, existing office views and authenticated office JSON remain in place. Legacy global public switches remain off.

Public media is restricted to image extensions in public catalog/logo/kit/directory directories. Other uploads require office access and are never served on AGParts. Path containment uses pathlib.is_relative_to. The existing catalog, search, vehicle fitment, product records, stock and images are reused, with no data migrations.

Brand assets were copied from the current AGParts service. Canonical URLs and sitemap cache/snapshot keys use the AGParts origin. Old multilingual preview category URLs have equivalent filter pages; ag-ID product links resolve only to the exact existing ZPT product ID. No guessed product matches. Old preview demo products are not redirected to unrelated products. Sitemap refresh no longer deletes snapshot rows.

Latest price-control download fix from main was merged into the migration work before rollout.

## Validation

Django check and git diff --check pass. Targeted host-isolation, catalog search/fitment, product metadata, media protection, price-control export and preserved supplier-price regression suites pass (see final rollout report for count). The original procurement purchase-list/supplier-workspace suites have eight failures also reproduced unchanged on the pre-rollout main commit; these are baseline assertions, not new migration regressions. The broader inventory lifecycle suite also has an unrelated assertion about displayed text; it is not modified by this rollout.

## Rollback procedure

1. Keep or resume the old agparts-kz service; never delete it or its repository.
2. Move agparts.kz and www.agparts.kz back to that recorded old service, restore recorded DNS if changed, verify HTTPS.
3. Set DOMAIN_BOUNDARY_ENABLED=false on ZPT and restore branch main / recorded live commit. Keep the preserved latest price-control fix.
4. Confirm ZPT office and old storefront in browser. Do not restore an older database over new transactions automatically. No database schema or business-data rollback is required by this code change.
5. If media recovery becomes necessary, evaluate post-snapshot uploads before using Render disk restore; it would overwrite later changes and is not part of ordinary code rollback.

## Publication status

Prepared for rollout. Final domain assignments, live deploy, browser checks and suspension status must be recorded after execution; this preparation document does not claim publication success.
