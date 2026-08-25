from pathlib import Path


def resolve_media_root(raw_value, base_dir):
    """Return MEDIA_ROOT as a Path.

    If env MEDIA_ROOT is set, use it (Render Persistent Disk).
    Otherwise keep the historical local directory: <base_dir>/products.
    """
    value = (raw_value or '').strip()
    if value:
        return Path(value)
    return Path(base_dir) / 'products'
