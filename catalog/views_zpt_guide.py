"""Public ZPT Гид page: FAQ, feedback, AI helper, install."""

from __future__ import annotations

import json

from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from core.forms import FeedbackForm
from core.models import GuideFaqVote
from core.platform_help import load_conversation_from_session
from core.services.seller_identity import get_logged_request_seller
from core.zpt_guide_faq import (
    PUBLIC_TOPICS,
    faq_client_payload,
    get_faq_item,
    public_faq_items,
    top10_items,
)


def _ensure_session_key(request) -> str:
    if not request.session.session_key:
        request.session.save()
    return str(request.session.session_key or '')


def faq_vote_stats_map(faq_ids=None) -> dict:
    qs = GuideFaqVote.objects.all()
    if faq_ids is not None:
        qs = qs.filter(faq_id__in=list(faq_ids))
    rows = qs.values('faq_id').annotate(
        total=Count('id'),
        yes=Count('id', filter=Q(helpful=True)),
    )
    result = {}
    for row in rows:
        total = int(row['total'] or 0)
        yes = int(row['yes'] or 0)
        result[row['faq_id']] = {
            'total': total,
            'helpful_percent': round(100 * yes / total) if total else None,
        }
    return result


def _vote_payload(faq_id: str, session_key: str = '') -> dict:
    stats = faq_vote_stats_map([faq_id]).get(faq_id) or {
        'total': 0,
        'helpful_percent': None,
    }
    my_vote = None
    if session_key:
        vote = GuideFaqVote.objects.filter(
            faq_id=faq_id,
            session_key=session_key,
        ).first()
        if vote is not None:
            my_vote = bool(vote.helpful)
    return {
        'faq_id': faq_id,
        'total': stats['total'],
        'helpful_percent': stats['helpful_percent'],
        'my_vote': my_vote,
    }


@ensure_csrf_cookie
@require_GET
def zpt_guide_view(request):
    seller = get_logged_request_seller(request)
    conversation = load_conversation_from_session(request)
    help_contact_is_seller = seller is not None
    help_contact_whatsapp = ''
    if seller is not None and str(getattr(seller, 'whatsapp', '') or '').strip():
        help_contact_whatsapp = str(seller.whatsapp).strip()
    elif conversation is not None and conversation.contact_whatsapp:
        help_contact_whatsapp = conversation.contact_whatsapp
    session_key = request.session.session_key or ''
    stats = faq_vote_stats_map()
    my_votes = {}
    if session_key:
        my_votes = {
            row.faq_id: bool(row.helpful)
            for row in GuideFaqVote.objects.filter(session_key=session_key)
        }
    items = faq_client_payload()
    for item in items:
        vote = stats.get(item['id']) or {'total': 0, 'helpful_percent': None}
        item['total'] = vote['total']
        item['helpful_percent'] = vote['helpful_percent']
        item['my_vote'] = my_votes.get(item['id'])
    return render(request, 'catalog/zpt_guide.html', {
        'faq_items': public_faq_items(),
        'faq_top10': top10_items(),
        'faq_topics': PUBLIC_TOPICS,
        'faq_payload': items,
        'faq_vote_stats': stats,
        'feedback_form': FeedbackForm(),
        'help_contact_whatsapp': help_contact_whatsapp,
        'help_contact_is_seller': help_contact_is_seller,
    })


@require_POST
def zpt_guide_faq_vote(request):
    try:
        payload = json.loads(request.body.decode('utf-8') or '')
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'ok': False, 'message': 'Некорректный запрос.'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'ok': False, 'message': 'Некорректный запрос.'}, status=400)
    item = get_faq_item(payload.get('faq_id'))
    if item is None:
        return JsonResponse({'ok': False, 'message': 'Вопрос не найден.'}, status=400)
    if 'helpful' not in payload:
        return JsonResponse({'ok': False, 'message': 'Укажите оценку.'}, status=400)
    helpful = payload.get('helpful')
    if not isinstance(helpful, bool):
        return JsonResponse({'ok': False, 'message': 'Укажите оценку.'}, status=400)
    session_key = _ensure_session_key(request)
    if not session_key:
        return JsonResponse(
            {'ok': False, 'message': 'Не удалось сохранить оценку. Обновите страницу.'},
            status=400,
        )
    vote, created = GuideFaqVote.objects.get_or_create(
        faq_id=item['id'],
        session_key=session_key,
        defaults={'helpful': helpful},
    )
    if not created and vote.helpful != helpful:
        vote.helpful = helpful
        vote.save(update_fields=['helpful', 'updated_at'])
    data = _vote_payload(item['id'], session_key)
    data['ok'] = True
    return JsonResponse(data)
