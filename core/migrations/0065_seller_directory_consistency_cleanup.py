from django.db import migrations


def repair_lead_consistency(apps, schema_editor):
    SellerLead = apps.get_model('core', 'SellerLead')

    # This lead was manually marked no_whatsapp before the later audit found
    # explicit wa.me evidence. Preserve its review history, but remove the stale
    # contradictory pipeline status.
    lead = SellerLead.objects.filter(
        pk=4,
        status='no_whatsapp',
        whatsapp='77002510460',
        whatsapp_confidence='high',
    ).first()
    if lead is not None:
        lead.status = 'needs_review'
        lead.save(update_fields=['status', 'updated_at'])

    # Parts.kz is an explicit Kazakhstan seller identity (.kz domain, Almaty
    # location and delivery wording across Kazakhstan). This only clarifies
    # market scope and does not promote invitation lifecycle.
    lead = SellerLead.objects.filter(
        pk=116,
        website_url__icontains='parts.kz',
        city='Алматы',
    ).first()
    if lead is not None and lead.market_scope == 'unknown':
        lead.market_scope = 'kz'
        lead.market_scope_evidence = (
            'seller_directory_cleanup_2026_10_08: official .kz seller site '
            'and explicit Kazakhstan delivery/location identity'
        )
        lead.save(update_fields=[
            'market_scope',
            'market_scope_evidence',
            'updated_at',
        ])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0064_registered_seller_type_and_profile_cleanup'),
    ]

    operations = [
        migrations.RunPython(repair_lead_consistency, migrations.RunPython.noop),
    ]
