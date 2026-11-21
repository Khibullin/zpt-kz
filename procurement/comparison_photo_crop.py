"""Extract a real product photo by cropping the image portion of a saved Kaspi screenshot.

The bounding box is a provisional visual observation, not proof of identity.
Only trusted stored screenshot bytes are cropped; no remote URLs are followed.
"""
import base64
import io
import json
from PIL import Image, UnidentifiedImageError

PREFIX = 'data:image/jpeg;base64,'


def normalize_box(value, screenshots_count):
    """[screenshot_index, left, top, right, bottom] in 0..1000 screenshot coordinates."""
    if not isinstance(value, (list, tuple)) or len(value) != 5:
        return None
    if any(isinstance(n, bool) or not isinstance(n, (float, int)) for n in value):
        return None
    i, x1, y1, x2, y2 = value
    if int(i) != i or not 0 <= i < screenshots_count:
        return None
    if not all(0 <= v <= 1000 for v in (x1, y1, x2, y2)):
        return None
    if x2 - x1 < 115 or y2 - y1 < 115:
        return None
    return [int(i), float(x1), float(y1), float(x2), float(y2)]


def crop_photo(screenshots, raw_box):
    """Return a compact JPEG data URI, or None when no reliable crop exists."""
    box = normalize_box(raw_box, len(screenshots))
    if box is None:
        return None
    i, x1, y1, x2, y2 = box
    source = screenshots[i]
    if not isinstance(source, str) or not source.startswith(PREFIX):
        return None
    try:
        binary = base64.b64decode(source[len(PREFIX):], validate=True)
        if len(binary) > 800_000:
            return None
        with Image.open(io.BytesIO(binary)) as im:
            im.load()
            if im.width < 160 or im.height < 160 or im.width * im.height > 8_000_000:
                return None
            rect = (
                max(0, round(im.width * x1 / 1000)),
                max(0, round(im.height * y1 / 1000)),
                min(im.width, round(im.width * x2 / 1000)),
                min(im.height, round(im.height * y2 / 1000)),
            )
            if rect[2] - rect[0] < 80 or rect[3] - rect[1] < 80:
                return None
            photo = im.convert('RGB').crop(rect)
            photo.thumbnail((850, 850))
            output = io.BytesIO()
            photo.save(output, format='JPEG', quality=84, optimize=True)
            if output.tell() > 400_000:
                return None
            return PREFIX + base64.b64encode(output.getvalue()).decode('ascii')
    except (ValueError, OSError, UnidentifiedImageError):
        return None


def photo_box_from_request(value, screenshots_count):
    try:
        raw = json.loads(value)
    except (ValueError, TypeError):
        return None
    return normalize_box(raw, screenshots_count)
