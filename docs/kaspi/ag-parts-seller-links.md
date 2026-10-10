# AG Parts: verified Kaspi links

This importer accepts seller-specific links copied from **Kaspi Pay → Товары → Копировать ссылку на товар в Вашем Магазине**. Links found by public product search are not proof of seller ownership.

Prepare a UTF-8 CSV:

```csv
master_sku,public_url
806873204,https://kaspi.kz/shop/p/example-123456789/?m=YOUR_VERIFIED_SELLER_ID
```

The example is illustrative only; replace it with a genuine copied seller link and do not import the example.

Run:

```bash
python manage.py import_ag_parts_kaspi_links links.csv
python manage.py import_ag_parts_kaspi_links links.csv --apply
```

The first command previews proposed changes. On apply, it updates only existing active listings of active AG Parts products, and aborts without any changes if CSV rows have invalid or ambiguous matches. It does not update retail prices, stock, publish flags or product branding.

Note: the application URL validator requires https://kaspi.kz/shop/p/ URLs, and permits query parameters. If Kaspi Pay gives a different link format, review it before importing; do not force it through the validator.
