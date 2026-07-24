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
