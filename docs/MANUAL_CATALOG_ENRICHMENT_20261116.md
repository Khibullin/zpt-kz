# ZPT.KZ — manual-only catalogue enrichment (16 November 2026)

## Owner decision
The public website and normal authenticated procurement operations remain online.
Supplier photo/fitment enrichment has **no recurring ChatGPT automation** and should
not be initiated by an idle web-server worker. Run enrichment only after a
specific owner request. This supersedes the former unattended nightly/weekend
enrichment instruction; it does not authorize altering supplier prices, stock,
profit rules, or Kaspi publication.

## Existing production worker
The Gunicorn `process_price_updates --watch` process serves multiple separate
functions: user-staged uploads, snapshot refreshes, Kaspi-related workflows,
and (formerly) automatic supplier photo processing. Do not disable the entire
price-update worker as a shortcut: it could affect unrelated functions.
`CATALOG_PHOTO_AUTORUN_ENABLED` is now hard-disabled by default, and the
watch loop must skip supplier photo draining. This is independent of
`PRICE_UPDATE_WORKER_ENABLED`.

## Operator procedure, only after explicit owner instruction
1. Confirm current production database, backups, disk free space and the
   existing supplier-photo queue. Do not create a second consumer/cron.
2. Preview without writes:
   `python manage.py run_manual_photo_enrichment --limit 10`
3. After owner has instructed this specific pass, execute:
   `python manage.py run_manual_photo_enrichment --run --limit 10`
   Maximum per invocation: 100. This reuses `process_photo_tasks`,
   including exact identity checks, source refusal, disk guard and row locks.
4. Review statuses and evidence; report actual successes, skips, blocked
   sources, errors and next checkpoint. A processed task is not necessarily
   a successful new photo.
5. For fitment, use existing evidence-reviewed pathways only; there is no
   generic safe automatic fitment inference. Stage independently and verify
   exact brand, article, variant, engine, dates and source before applying.
6. Do not push product-specific reviewed data to `main` one item at a time.
   A release branch may collect reviewed manifests; release program changes
   separately, in a controlled deployment. Prefer existing database/storage
   import workflows when safe for validated catalogue data.
7. Do not create a recurring automation or enable automatic release on
   behalf of this workflow. Deployment of this branch itself is a separate
   manual decision; no code has been deployed by creating the branch.

## Validation
- `python manage.py check`
- `python manage.py test catalog.tests.test_manual_photo_enrichment`
- Inspect Render build, migration, deployment and request logs after any
  separately approved deployment.
- Monitor disk growth, counts of commits/deploys, response latency and
  photo-task outcomes. No promised savings until measured.

## Unchanged
- Public service uptime, user requests and supplier account access
- Product prices, purchases, price markup, stock, ownership, order history
- Manual Kaspi preparation and current no-auto-publication policy
- Owner-reviewed evidence / identity / exclusion rules
- Morning reporting is a separate task; this command does not send emails.
