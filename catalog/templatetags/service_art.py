from django import template
from django.templatetags.static import static
from django.utils.html import format_html
register = template.Library()

@register.simple_tag
def service_art(key):
    repair = {'hero-sto':(0,0,620,305), 'hero-detailing':(630,0,624,310),
              'diagnostics':(0,315,620,280), 'engine':(630,315,624,280),
              'brakes':(0,595,620,305), 'electric':(630,610,624,275),
              'climate':(0,910,620,340), 'tires':(635,885,619,365)}
    detail = {'polish':(70,525,270,135), 'clean':(400,522,265,140),
              'ceramic':(730,530,270,130), 'film':(40,735,300,160),
              'tint':(370,735,305,165), 'tuning':(725,735,280,162)}
    is_repair = key in repair
    crop = (repair if is_repair else detail)[key]
    filename = 'repair-assets.webp' if is_repair else 'detailing-approved.webp'
    width, height = (1254,1254) if is_repair else (1036,1518)
    return format_html('<svg class="service-art" aria-hidden="true" focusable="false" viewBox="{}" overflow="hidden"><image href="{}" width="{}" height="{}"/></svg>', ' '.join(map(str,crop)), static('interface/services/'+filename),width,height)
