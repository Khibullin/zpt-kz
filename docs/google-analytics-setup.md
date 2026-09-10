# ZPT.KZ — Google Tag Manager / GA4 / Google Ads setup

## Current state

The application is prepared for Google Tag Manager but the container is fail-closed: nothing is loaded unless `GOOGLE_TAG_MANAGER_ID` contains a valid `GTM-...` value.

The site already exposes privacy-safe `dataLayer` events:

- `zpt_request_submitted` — a buyer request was accepted successfully by ZPT.KZ.
- `zpt_order_created` — an order record was created and its protected success page was reached. This event must not be treated as a completed payment or `purchase` unless payment confirmation is implemented separately.

No event sends a buyer name, phone, WhatsApp, email, street address, delivery address, or free-form request text.

## GTM container

1. Create or select one GTM Web container for `zpt.kz`.
2. Copy its container ID in the form `GTM-XXXXXXX`.
3. Set Render environment variable `GOOGLE_TAG_MANAGER_ID` to that exact ID.
4. Verify the container with GTM Preview before publishing tags.

Do not add a second hard-coded GTM snippet to templates. The application already covers base templates and standalone public HTML through the existing analytics/SEO middleware.

## GA4

Recommended first configuration inside GTM:

- one GA4 Google tag for the ZPT.KZ GA4 web data stream;
- Custom Event trigger: `zpt_request_submitted`;
- Custom Event trigger: `zpt_order_created`.

Mark `zpt_request_submitted` as the primary lead/key event for buyer-demand generation.

Keep `zpt_order_created` separate from a paid purchase. It represents an order created before seller confirmation/payment.

## Google Ads

After GA4/GTM validation:

- import or create a Google Ads conversion for the successful buyer request;
- use `zpt_request_submitted` as the primary lead conversion;
- keep `zpt_order_created` secondary until the business decides that created orders should be an optimization target;
- do not use page-button clicks as the primary conversion when the successful backend result is already available.

## Privacy

The public privacy policy is available at `/privacy/` and states the operator details and use of analytics/advertising services.

Never pass personally identifiable information to `dataLayer`, GA4 or Google Ads event parameters. In particular do not send names, phone/WhatsApp numbers, email addresses, delivery addresses, uploaded-photo URLs or the free-form request description.

## Verification checklist before enabling optimization

- GTM Preview sees one container load per public HTML page.
- Navigating through catalog, seller and service standalone pages does not lose GTM.
- A successful buyer request fires `zpt_request_submitted` once.
- Failed/validation requests do not fire the event.
- Order success fires `zpt_order_created` and repeat refresh in the same browser does not create duplicate local events.
- GA4 Realtime/DebugView receives the expected events without PII.
- Google Ads conversion diagnostics show the configured lead action only after the GTM/GA4 setup is published.
