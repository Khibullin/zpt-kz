# Omega grouped supplier invoices

The original XLS order dated 25 November exposed the receipt parser treating row 9,
`Итого, Nissan:`, as an incomplete product. Repeated headers, group headings and
footer totals followed the same layout.

The preview now recognizes the full Omega header (including Модель, OEM and
Сумма со скидкой). Group headings are matched against explicit subtotal labels;
known footer rows and repeated headers are reported separately. Every other
nonempty row remains in the editable preview, with original Excel row number,
raw cell values and validation problems. Valid rows alone may match catalogue
items. No receipt, item, attachment or stock operation is created by parsing.
Units are populated from explicit unit columns, package/kit wording in names
and descriptions, or the ordinary individual-item convention of the recognized
Omega format. Kits stay in supplier document units with multiplier one.
Explicit pack sizes may convert to an existing piece card via the multiplier,
provided its unit price is exactly representable in cents. Other conflicts require
review and never change the confirmed catalogue unit during preview. Unknown formats without unit evidence also
require review. Missing-unit validation points to the unit field.

Declared supplier totals and calculated line totals are compared in the preview.
Any difference is shown without modifying original prices or uploaded bytes.
Package quantities are not converted to pieces automatically. Private supplier
files and their financial details are excluded from this repository.

Regression coverage includes grouped rows, repeated headings, incomplete and
invalid products, unknown lines, product names resembling totals, XLS resource
handling, limits, and the preview endpoint's absence of accounting writes.

Rollback: revert this change. No schema or production data changes are needed.
Previous live commit: 1bd7ecf124605b8b75e724a6c0c228035f99e514.
