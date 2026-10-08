"""Read-only seller reaction states for request dispatch operations.

The existing SellerRequestPageEvent stream remains the source of truth.
No dispatch or request rows are mutated here.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.models import (
    RequestDispatch,
    SELLER_REQUEST_PAGE_EVENT_CALL_CLICK,
    SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
)

REACTION_NO_REACTION = 'no_reaction'
REACTION_OPENED = 'opened'
REACTION_CONTACT = 'contact'
REACTION_OUT_OF_STOCK = 'out_of_stock'
REACTION_CANNOT_FULFILL = 'cannot_fulfill'
REACTION_NOT_SENT = 'not_sent'

RELEVANT_EVENT_TYPES = frozenset({
    SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN,
    SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK,
    SELLER_REQUEST_PAGE_EVENT_CALL_CLICK,
    SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK,
    SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL,
})


@dataclass(frozen=True)
class SellerRequestReaction:
    code: str
    label: str


@dataclass(frozen=True)
class RequestReactionSummary:
    sent: int = 0
    opened: int = 0
    contact: int = 0
    declined: int = 0
    no_reaction: int = 0

    @property
    def label(self) -> str:
        if self.sent == 0:
            return 'Отправок нет'
        return (
            f'Отправлено: {self.sent} · '
            f'открыли: {self.opened} · '
            f'к контакту: {self.contact} · '
            f'отказ: {self.declined} · '
            f'без реакции: {self.no_reaction}'
        )


def classify_seller_request_reaction(
    *,
    dispatch_status: str,
    event_types,
) -> SellerRequestReaction:
    events = set(event_types or ())

    if dispatch_status != RequestDispatch.STATUS_SENT:
        return SellerRequestReaction(
            REACTION_NOT_SENT,
            dict(RequestDispatch.STATUS_CHOICES).get(
                dispatch_status,
                dispatch_status or 'Не отправлено',
            ),
        )

    if SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL in events:
        return SellerRequestReaction(
            REACTION_CANNOT_FULFILL,
            'Не может выполнить',
        )
    if SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK in events:
        return SellerRequestReaction(
            REACTION_OUT_OF_STOCK,
            'Нет в наличии',
        )
    if (
        SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK in events
        or SELLER_REQUEST_PAGE_EVENT_CALL_CLICK in events
    ):
        return SellerRequestReaction(
            REACTION_CONTACT,
            'Перешёл к контакту',
        )
    if SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN in events:
        return SellerRequestReaction(
            REACTION_OPENED,
            'Открыл заявку',
        )
    return SellerRequestReaction(
        REACTION_NO_REACTION,
        'Без реакции',
    )


def summarize_request_reactions(request_obj) -> RequestReactionSummary:
    dispatches = list(request_obj.dispatches.all())
    sent_dispatches = [
        dispatch
        for dispatch in dispatches
        if dispatch.status == RequestDispatch.STATUS_SENT
    ]
    if not sent_dispatches:
        return RequestReactionSummary()

    events_by_seller: dict[int, set[str]] = {}
    for event in request_obj.seller_page_events.all():
        if event.event_type not in RELEVANT_EVENT_TYPES:
            continue
        events_by_seller.setdefault(event.seller_id, set()).add(event.event_type)

    opened = 0
    contact = 0
    declined = 0
    no_reaction = 0

    for dispatch in sent_dispatches:
        events = events_by_seller.get(dispatch.seller_id, set())
        if (
            SELLER_REQUEST_PAGE_EVENT_PAGE_OPEN in events
            or SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK in events
            or SELLER_REQUEST_PAGE_EVENT_CALL_CLICK in events
            or SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK in events
            or SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL in events
        ):
            opened += 1
        if (
            SELLER_REQUEST_PAGE_EVENT_WHATSAPP_CLICK in events
            or SELLER_REQUEST_PAGE_EVENT_CALL_CLICK in events
        ):
            contact += 1
        if (
            SELLER_REQUEST_PAGE_EVENT_OUT_OF_STOCK in events
            or SELLER_REQUEST_PAGE_EVENT_CANNOT_FULFILL in events
        ):
            declined += 1
        if not events:
            no_reaction += 1

    return RequestReactionSummary(
        sent=len(sent_dispatches),
        opened=opened,
        contact=contact,
        declined=declined,
        no_reaction=no_reaction,
    )
