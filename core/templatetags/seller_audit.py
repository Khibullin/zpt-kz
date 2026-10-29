from django import template
from core.services.seller_audit import presentation

register = template.Library()

@register.inclusion_tag('core/connections/seller_audit_public.html')
def seller_audit_public(seller):
    return {'audit': presentation(seller)}

@register.inclusion_tag('core/connections/seller_audit_internal.html')
def seller_audit_internal(seller):
    countries = ', '.join(x.name for x in seller.selected_countries.all())
    if not countries and seller.country_fk_id:
        countries = seller.country_fk.name
    brands = ', '.join(x.name for x in seller.selected_brands.all()) or seller.brand
    models = ', '.join(x.name for x in seller.selected_models.all()) or seller.model
    categories = ', '.join(x.name for x in seller.selected_categories.all()) or seller.category
    return {'audit': presentation(seller, internal=True), 'seller': seller,
            'countries': countries or ('Все страны' if seller.all_countries else 'Страна не указана'),
            'brands': brands or ('Все марки' if seller.all_brands else 'Марки не указаны'),
            'models': models or ('Все модели' if seller.all_models else 'Модели не указаны'),
            'categories': categories or ('Все категории' if seller.all_categories else 'Категории не указаны')}
