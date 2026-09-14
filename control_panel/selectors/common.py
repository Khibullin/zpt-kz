from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import QueryDict

PAGE_SIZE = 50
SELLER_MATCH_HISTORY_LIMIT = 30
SELLER_EVENT_HISTORY_LIMIT = 50
STO_MATCH_HISTORY_LIMIT = 30
WA_LOG_HISTORY_LIMIT = 30


def fetch_latest(queryset, *, limit: int) -> list:
    return list(queryset[:limit])


@dataclass(frozen=True)
class PageResult:
    object_list: list
    page: object
    querystring: str
    total: int


def preserved_querystring(params: QueryDict, *, exclude: tuple[str, ...] = ('page',)) -> str:
    items = []
    for key in params.keys():
        if key in exclude:
            continue
        for value in params.getlist(key):
            if value == '':
                continue
            items.append((key, value))
    return urlencode(items)


def paginate(queryset, params: QueryDict, *, per_page: int = PAGE_SIZE) -> PageResult:
    paginator = Paginator(queryset, per_page)
    page = paginator.get_page(params.get('page') or 1)
    return PageResult(
        object_list=list(page.object_list),
        page=page,
        querystring=preserved_querystring(params),
        total=paginator.count,
    )


def first_value(params: QueryDict, name: str) -> str:
    return str(params.get(name) or '').strip()
