# Google Search Console Product / merchant warnings — 2026-10-22

## Scope

This change addresses Google Search Console warnings about Product structured
data on ZPT.KZ. It changes only the public `Product` JSON-LD category fallback
and adds a **read-only** diagnostic command. It does not change the product
records, pricing, seller dispatch, WhatsApp, Kaspi, inventory or return terms.

## Safe category fix

A Product with an assigned `Product.category.name` still emits that exact
category. A Product missing it now emits the factual general category
`Автозапчасти`. We must not infer a more specific category from vehicle
manufacturer, title, engine, or free-form seller text.

## Why some warnings cannot be removed automatically

* `offers.availability`: `stock_qty > 0` produces InStock and `0`
  produces OutOfStock. The database explicitly defines `NULL` as unknown,
  **not** zero. Never publish InStock, OutOfStock, BackOrder or PreOrder
  based solely on an unknown balance. Price can be displayed with an offer
  even when stock has not been confirmed.
* `offers.shippingDetails`: each marketplace seller has its own conditions;
  free-form `delivery_info` includes a default description that does not
  prove destinations, cost or transit days. Omit structured shipping terms
  unless the seller has verified them for that offer.
* `offers.hasMerchantReturnPolicy`: the platform is not the seller for
  every item. No seller-specific verified return window or policy URL is
  currently available in the Product model. Do not invent an all-seller policy.
* `Product.review` and `Product.aggregateRating`: do not publish fabricated
  review text, ratings, counts or third-party review scores.

Google may retain those **noncritical warnings**; this is preferable to
publishing incorrect structured data.

## Read-only diagnosis

In an explicitly authorized environment (never against a production database
without permission):

```bash
python manage.py audit_product_schema --limit 25
```

It reports priced active listings with unknown `stock_qty` or no specific
category and their public paths. The command does not write anything.
The general-category fallback prevents a category warning even when the
specific category is still missing from the record.

## After rollout

1. Check a freshly rendered public product page in Rich Results Test:
   https://search.google.com/test/rich-results
2. Confirm `Product.category` is present. Check `offers.availability`
   only when source stock is actually confirmed.
3. In Search Console inspect the actual affected product URLs and **dates
   of Google's last crawl** before treating prior warnings as current.
4. Have sellers confirm missing stock, shipping prices/times and return
   policy where appropriate. These are business data requests, not
   automated SEO guesses.
5. After a new crawl, use **Validate fix** only for issues genuinely resolved
   on every reported URL. Do not promise warnings on reviews, returns or
   delivery will disappear without verified data.

## Regressions

SEO CI tests verify category fallback, normal specific categories, known and
unknown stock, and the absence of invented shipping, policy and review data.
