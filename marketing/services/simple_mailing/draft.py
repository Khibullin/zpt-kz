from __future__ import annotations

from marketing.services.simple_mailing.constants import SESSION_DRAFT_KEY


def save_simple_mailing_draft(session, draft: dict) -> None:
    session[SESSION_DRAFT_KEY] = draft
    session.modified = True


def load_simple_mailing_draft(session) -> dict | None:
    draft = session.get(SESSION_DRAFT_KEY)
    if isinstance(draft, dict):
        return draft
    return None


def clear_simple_mailing_draft(session) -> None:
    if SESSION_DRAFT_KEY in session:
        del session[SESSION_DRAFT_KEY]
        session.modified = True


def save_template_to_draft(session, template_id: int) -> dict:
    draft = load_simple_mailing_draft(session)
    if not draft:
        raise ValueError('Simple mailing draft is missing.')
    draft['template_id'] = template_id
    save_simple_mailing_draft(session, draft)
    return draft


def load_draft_template_id(session) -> int | None:
    draft = load_simple_mailing_draft(session)
    if not draft:
        return None
    template_id = draft.get('template_id')
    if template_id in (None, ''):
        return None
    try:
        return int(template_id)
    except (TypeError, ValueError):
        return None
