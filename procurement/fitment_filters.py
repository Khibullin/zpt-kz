"""Filter only structured, sourced vehicle configurations."""
import re

from django.utils import timezone


def configuration_years(value):
    text = str(value or '').strip()
    end_limit = timezone.localdate().year
    match = re.fullmatch(r'(19\d{2}|20\d{2})\s*[-–—/]\s*(19\d{2}|20\d{2}|н\.?\s*в\.?|наст\.?\s*время|present|\.{3}|…)?', text, re.I)
    if match:
        start = int(match[1])
        end = int(match[2]) if match[2] and match[2].isdigit() else end_limit
        return range(start, min(end, end_limit) + 1)
    if re.fullmatch(r'(19\d{2}|20\d{2})', text):
        return [int(text)]
    return []


def matches_configuration(configurations, generation='', year=None, years=None):
    return any((not generation or config.get('generation') == generation)
               and (not year or int(year) in configuration_years(config.get('years')))
               and (not years or set(years).intersection(configuration_years(config.get('years'))))
               for config in configurations if isinstance(config, dict))
