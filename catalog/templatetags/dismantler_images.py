"""Presentation-only selection of the owner's approved directory illustrations."""

from django import template
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()

# Windows into the approved artwork; exclude its UI and baked-in Russian labels.
# One compressed source is shared by every card and downloaded only once.
SCENES = {
    'yard': (32, 193, 489, 245),
    'warehouse': (568, 193, 486, 245),
    'engines': (32, 624, 489, 250),
    'body': (568, 624, 486, 250),
    'suv': (32, 1056, 489, 238),
    'truck': (568, 1056, 486, 238),
}


def scene_for_seller(seller):
    # Public editorial descriptions only: dispatch settings and private notes
    # must not be interpreted as claims about a company's stock or premises.
    text = ' '.join((seller.name or '', seller.directory_description or '',
                     seller.directory_description_kk or '')).casefold()
    if any(word in text for word in ('грузов', 'жүк көлік')):
        return 'truck'
    if any(word in text for word in ('внедорож', 'жол талғамайтын', 'land cruiser', 'patrol', 'prado')):
        return 'suv'
    scenes = []
    if any(word in text for word in ('двигател', 'короб', 'трансмис', 'қозғалтқыш', 'беріліс')):
        scenes.append('engines')
    if any(word in text for word in ('кузов', 'оптик', 'фары', 'шанақ')):
        scenes.append('body')
    # A stable choice keeps a company's image consistent through filters/pages.
    if not scenes:
        return ('yard', 'warehouse')[((seller.pk or 0) // 3) % 2]
    return scenes[(seller.pk or 0) % len(scenes)]


@register.simple_tag
def dismantler_art(seller):
    scene = scene_for_seller(seller)
    viewbox = ' '.join(map(str, SCENES[scene]))
    return format_html(
        '<svg class="dismantle-company-art" data-scene="{}" '
        'aria-hidden="true" focusable="false" viewBox="{}" '
        'preserveAspectRatio="xMidYMid slice">'
        '<image href="{}" width="1086" height="1448"/></svg>',
        scene, viewbox, static('interface/dismantlers-approved-scenes.webp'),
    )
