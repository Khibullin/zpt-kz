# Unified AG Parts storefront — approved redirect map (25 November 2026)

This replaces the September plan to retire AGParts into ZPT. AGParts.kz is now the public canonical origin; ZPT.KZ is the private office. Never redirect AGParts public traffic to the office.

| Source | Destination | Rule |
| --- | --- | --- |
| ZPT root | /office/login/ on ZPT | 302; private/noindex |
| ZPT retained catalog URLs | Same canonical product or public path on AGParts | 301; office paths and private APIs excluded |
| AGParts www | AGParts apex, same path/query | 301; Render domain redirect |
| AGParts /market/ prefix | Canonical path without /market | 301 |
| AGParts /ru/, /kk/, /zh/ | AGParts / | 301 |
| /{lang}/catalog/, /{lang}/selection/ | AGParts / | 301 |
| /{lang}/catalog/cabin/, air/, oil/, spark/ | /filters/{category}/ | 301; equivalent filtered storefront page |
| /{lang}/product/ag-{id}/ | Exact active ZPT product canonical URL on AGParts | 301; ID lookup, no guessed matches |
| /changan/, /chery/, /haval/, /zeekr/ | /avtozapchasti/{brand}/ | 301 on AGParts; old ZPT product aliases resolve to their exact product before crossing hosts |
| /oilfilters/ | /avtozapchasti/maslyanye-filtry/ | 301 |
| /product-page/filterscheryexceedjetour | /chery-tiggo-7-t151109111/ | Reviewed exact product mapping |
| /product-page/воздушный-фильтр-1109190cr01 | /changan-uni-k-1109190cr01/ | Reviewed exact product mapping |

Unmatched old demo products and unknown Wix product paths return 404 until an exact product mapping is verified. Do not collapse them to an unrelated product or generic homepage.

Canonical URLs, JSON-LD public URLs, sitemap parts and cache/snapshot keys use https://agparts.kz when DOMAIN_BOUNDARY_ENABLED=true. ZPT robots disallows crawling; private routes and responses are noindex/no-store. Storefront query filters remain noindex under existing SEO rules. Store sitemap excludes blocked old registration/request routes.

Recovery points, actual service/deploy references and rollback procedure: ../AGPARTS_DOMAIN_CUTOVER_20261125.md. Verify both domains in browser before suspending the old service.
