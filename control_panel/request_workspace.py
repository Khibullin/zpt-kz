"""Operator-facing stages, separate from delivery and buyer lifecycle states."""
from django.contrib.admin.models import LogEntry
from django.db.models import Case, CharField, Exists, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Cast
from core.models import Match, RequestDispatch

STAGE_PREFIX = 'Рабочий этап: '
STAGES = [('new', 'Новые'), ('waiting', 'Ждём уточнения'), ('active', 'В работе'), ('done', 'Завершённые')]
STAGE_LABELS = {'new': 'Новая', 'waiting': 'Ждём уточнения', 'active': 'В работе', 'done': 'Завершена'}


def with_workspace_stage(queryset):
    latest = LogEntry.objects.filter(content_type__app_label='core', content_type__model='request',
        object_id=Cast(OuterRef('pk'), CharField()), change_message__startswith=STAGE_PREFIX).order_by('-action_time', '-pk')
    return queryset.annotate(
        operator_stage=Subquery(latest.values('change_message')[:1]),
        has_dispatch=Exists(RequestDispatch.objects.filter(request_id=OuterRef('pk'))),
        has_match=Exists(Match.objects.filter(request_id=OuterRef('pk'))),
    ).annotate(workspace_stage=Case(
        When(purchase_result__in=['purchased', 'closed'], then=Value('done')),
        When(operator_stage=STAGE_PREFIX+'waiting', then=Value('waiting')),
        When(Q(operator_stage=STAGE_PREFIX+'active') | Q(has_dispatch=True) | Q(has_match=True), then=Value('active')),
        default=Value('new'), output_field=CharField()))
