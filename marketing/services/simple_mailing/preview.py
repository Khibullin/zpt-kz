from __future__ import annotations

from marketing.services.simple_mailing.constants import PREVIEW_SESSION_KEY


def build_selection_key(
    *,
    recipient_type: str,
    all_brands: bool,
    brands: list[str],
) -> dict:
    return {
        'recipient_type': recipient_type,
        'all_brands': all_brands,
        'brands': tuple(sorted(brands)) if not all_brands else (),
    }


def _normalized_brands(value) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(sorted(str(item) for item in value))


def save_preview_state(session, *, selection_key: dict, count: int) -> None:
    session[PREVIEW_SESSION_KEY] = {
        **selection_key,
        'count': count,
    }
    session.modified = True


def load_preview_state(session) -> dict | None:
    preview = session.get(PREVIEW_SESSION_KEY)
    if isinstance(preview, dict):
        return preview
    return None


def clear_preview_state(session) -> None:
    if PREVIEW_SESSION_KEY in session:
        del session[PREVIEW_SESSION_KEY]
        session.modified = True


def preview_matches(session, selection_key: dict) -> bool:
    stored = load_preview_state(session)
    if stored is None:
        return False
    return (
        stored.get('recipient_type') == selection_key['recipient_type']
        and stored.get('all_brands') == selection_key['all_brands']
        and _normalized_brands(stored.get('brands')) == _normalized_brands(selection_key['brands'])
    )
