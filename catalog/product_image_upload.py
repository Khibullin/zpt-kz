"""Seller product image attach: signed remote tokens + local uploads.

Never trusts client URLs. Maximum one main image and four additional photos.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from django.core.files.base import ContentFile

from catalog.models import Product, ProductImage
from catalog.remote_image import RemoteImageError, fetch_signed_remote_image, read_remote_image_token

MAX_PRODUCT_IMAGES = 5
MAX_ADDITIONAL_IMAGES = 4
TOO_MANY_IMAGES_MESSAGE = (
    'Можно сохранить не больше 5 фото: одно главное и до 4 дополнительных.'
)


@dataclass
class ProductImagePlan:
    main_remote: ContentFile | None = None
    extra_remotes: list[ContentFile] = field(default_factory=list)
    extra_local: list = field(default_factory=list)
    replace_extras_with_local: bool = False
    remove_main: bool = False
    remove_extra: bool = False


def _unique_tokens(tokens: list[str]) -> list[str]:
    ordered: list[str] = []
    seen_tokens: set[str] = set()
    seen_urls: set[str] = set()
    for raw in tokens:
        token = str(raw or '').strip()
        if not token or token in seen_tokens:
            continue
        payload = read_remote_image_token(token)
        url = payload['image_url']
        seen_tokens.add(token)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        ordered.append(token)
    return ordered


def parse_remote_image_tokens(post) -> tuple[list[str], str]:
    """Return (deduped tokens, main token). Raises RemoteImageError."""
    main = str((post.get('remote_main_image_token') if post is not None else '') or '').strip()
    listed: list[str] = []
    raw = str((post.get('remote_image_tokens') if post is not None else '') or '').strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RemoteImageError('Некорректный выбор фото.') from exc
        if not isinstance(parsed, list):
            raise RemoteImageError('Некорректный выбор фото.')
        listed = [str(item or '').strip() for item in parsed if str(item or '').strip()]

    combined: list[str] = []
    if main:
        combined.append(main)
    combined.extend(listed)
    tokens = _unique_tokens(combined)
    if len(tokens) > MAX_PRODUCT_IMAGES:
        raise RemoteImageError(TOO_MANY_IMAGES_MESSAGE)
    main_token = ''
    if main and main in tokens:
        main_token = main
    return tokens, main_token


def extra_slot_count(
    product: Product | None,
    *,
    remove_extra: bool,
    replace_extras_with_local: bool,
) -> int:
    if product is None or remove_extra or replace_extras_with_local:
        used = 0
    else:
        used = product.images.count()
    return max(0, MAX_ADDITIONAL_IMAGES - used)


def build_product_image_plan(request, product: Product | None = None) -> ProductImagePlan:
    post = getattr(request, 'POST', {}) or {}
    files = getattr(request, 'FILES', None)
    local_main = bool(files and files.get('main_image'))
    local_extras = list(files.getlist('extra_images')) if files else []
    replace_extras_with_local = bool(local_extras)
    remove_main = bool(post.get('remove_main_image'))
    remove_extra = bool(post.get('remove_extra_images'))

    tokens, main_token = parse_remote_image_tokens(post)
    extra_remote_tokens = list(tokens)
    main_remote_token = ''
    if tokens and not local_main:
        if main_token:
            main_remote_token = main_token
            extra_remote_tokens = [token for token in tokens if token != main_remote_token]
        elif product is None or not getattr(product, 'main_image', None) or remove_main:
            main_remote_token = tokens[0]
            extra_remote_tokens = tokens[1:]

    extra_slots = extra_slot_count(
        product,
        remove_extra=remove_extra,
        replace_extras_with_local=replace_extras_with_local,
    )
    extra_count = len(extra_remote_tokens) + len(local_extras)
    if extra_count > extra_slots:
        raise RemoteImageError(TOO_MANY_IMAGES_MESSAGE)
    if extra_count > MAX_ADDITIONAL_IMAGES:
        raise RemoteImageError(TOO_MANY_IMAGES_MESSAGE)

    article_stem = 'product'
    plan = ProductImagePlan(
        extra_local=local_extras,
        replace_extras_with_local=replace_extras_with_local,
        remove_main=remove_main,
        remove_extra=remove_extra,
    )
    if not tokens:
        return plan

    from catalog.article_utils import normalize_article
    article_stem = normalize_article(
        post.get('article') or (product.article if product is not None else '') or ''
    ) or 'product'

    files_by_token = {
        token: fetch_signed_remote_image(token, filename_stem=article_stem)
        for token in tokens
    }
    if main_remote_token:
        plan.main_remote = files_by_token[main_remote_token]
    plan.extra_remotes = [files_by_token[token] for token in extra_remote_tokens]
    return plan


def apply_product_image_plan(product: Product, plan: ProductImagePlan, *, new_local_main: bool) -> None:
    if plan.remove_extra or plan.replace_extras_with_local:
        for img in list(product.images.all()):
            img.image.delete(save=False)
            img.delete()

    if plan.remove_main and not new_local_main and plan.main_remote is None:
        if product.main_image:
            product.main_image.delete(save=False)
        product.main_image = None

    if plan.main_remote is not None and not new_local_main:
        product.main_image.save(plan.main_remote.name, plan.main_remote, save=False)

    product.save()

    extra_files = list(plan.extra_remotes) + list(plan.extra_local)
    existing = product.images.count()
    remaining = max(0, MAX_ADDITIONAL_IMAGES - existing)
    if len(extra_files) > remaining:
        raise RemoteImageError(TOO_MANY_IMAGES_MESSAGE)

    start_order = existing
    for index, uploaded in enumerate(extra_files):
        ProductImage.objects.create(
            product=product,
            image=uploaded,
            sort_order=start_order + index,
        )

    product.refresh_from_db()
    if not product.main_image:
        first_image = product.images.order_by('sort_order', 'id').first()
        if first_image:
            product.main_image = first_image.image
            product.save(update_fields=['main_image'])
