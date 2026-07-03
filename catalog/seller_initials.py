import re

LEGAL_PREFIXES = frozenset({
    'тоо', 'too', 'ooo', 'ооо', 'ип', 'ip', 'ao', 'ао', 'llp', 'ltd', 'inc',
})

DEFAULT_INITIAL = 'M'


def seller_initials(name, *, max_length=2):
    """Build up to two uppercase initials from a seller display name."""
    text = re.sub(r'\s+', ' ', str(name or '').strip())
    if not text:
        return DEFAULT_INITIAL

    words = text.split(' ')
    while words and words[0].lower().rstrip('.') in LEGAL_PREFIXES:
        words.pop(0)

    if not words:
        return DEFAULT_INITIAL

    if len(words) == 1:
        return words[0][:1].upper()

    initials = words[0][:1] + words[1][:1]
    return initials[:max_length].upper()
