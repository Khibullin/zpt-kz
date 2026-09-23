"""Attach one general cover photo to existing MaintenanceKit rows.

Writes through Django storage to MEDIA_ROOT (persistent disk on Render).
Does not create kits, change composition, prices, stock or is_active.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from catalog.image_upload_policy import optimize_uploaded_image
from catalog.maintenance_kit_seed import KIT_SPECS
from catalog.models import MaintenanceKit

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}

FOLDER_SLUGS = {
    'комплект 1': 'komplekt-to-exeed-txl-16t',
    'комплект 2': 'komplekt-to-chery-tiggo-7-pro-15t',
    'комплект 3': 'komplekt-to-changan-uni-k-20t',
}

EXACT_COVER_FILES = {
    'komplekt-to-exeed-txl-16t': 'img-2026-09-21-17-10-43.png',
    'komplekt-to-chery-tiggo-7-pro-15t': 'img-2026-09-21-17-12-32.png',
    'komplekt-to-changan-uni-k-20t': 'img-2026-09-21-17-14-38.png',
}

COVER_NAME_HINTS = (
    'общ',
    'cover',
    'kit',
    'комплект',
    'набор',
    'layout',
    'group',
    'general',
    'сборк',
    'all',
)

PART_NAME_HINTS = (
    'фильтр',
    'filter',
    'свеч',
    'spark',
    'oil',
    'air',
    'cabin',
    'салон',
    'масл',
    'воздуш',
    'детал',
)


def _norm(value: str) -> str:
    return ' '.join(str(value or '').strip().lower().replace('ё', 'е').split())


def known_kit_articles() -> frozenset[str]:
    articles = []
    for spec in KIT_SPECS:
        for article, _qty in spec['items']:
            articles.append(str(article).strip())
    return frozenset(articles)


def compact_token(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', _norm(value))


def looks_like_part_photo(path: Path, articles: frozenset[str]) -> bool:
    haystacks = [_norm(path.stem), _norm(path.name)]
    parent = _norm(path.parent.name)
    compact = compact_token(path.stem)
    for article in articles:
        token = compact_token(article)
        if token and token in compact:
            return True
    if any(hint in parent for hint in PART_NAME_HINTS):
        if not any(hint in parent for hint in COVER_NAME_HINTS):
            return True
    text = ' '.join(haystacks)
    has_part = any(hint in text for hint in PART_NAME_HINTS)
    has_cover = any(hint in text for hint in COVER_NAME_HINTS)
    return has_part and not has_cover


def collect_images(folder: Path) -> list[Path]:
    files = []
    for path in sorted(folder.rglob('*')):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            files.append(path)
    return files


def score_cover_candidate(path: Path, folder: Path, articles: frozenset[str]) -> int:
    if looks_like_part_photo(path, articles):
        return -1000
    score = 0
    name = _norm(path.stem)
    if any(hint in name for hint in COVER_NAME_HINTS):
        score += 80
    try:
        relative = path.relative_to(folder)
    except ValueError:
        relative = Path(path.name)
    if len(relative.parts) == 1:
        score += 25
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    score += min(size // 50_000, 20)
    return score


def pick_cover_image(folder: Path, articles: frozenset[str] | None = None, *, slug: str = ''):
    articles = articles if articles is not None else known_kit_articles()
    images = collect_images(folder)
    if not images:
        return None, 'нет изображений'
    exact_name = EXACT_COVER_FILES.get(slug or '')
    if exact_name:
        exact = [
            path for path in images
            if path.name.lower() == exact_name.lower()
        ]
        if exact:
            return exact[0], ''
    ranked = sorted(
        (
            (score_cover_candidate(path, folder, articles), path.stat().st_size, path)
            for path in images
        ),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    best_score, _size, best_path = ranked[0]
    if best_score < 0:
        return None, 'только фото деталей, общий снимок не найден'
    return best_path, ''


def resolve_source_folders(source: Path) -> list[tuple[str, Path]]:
    source = source.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f'Каталог не найден: {source}')
    found: list[tuple[str, Path]] = []
    seen_slugs: set[str] = set()
    for child in sorted(source.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        key = _norm(child.name)
        slug = FOLDER_SLUGS.get(key)
        if slug is None:
            slug = FOLDER_SLUGS.get(key.replace('_', ' '))
        if slug is None and child.name in {spec['slug'] for spec in KIT_SPECS}:
            slug = child.name
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        found.append((slug, child))
    return found


@dataclass
class CoverPlan:
    slug: str
    folder: Path
    kit_id: int | None
    source_path: Path | None
    skip_reason: str = ''
    previous_name: str = ''
    would_replace: bool = False

    @property
    def can_apply(self) -> bool:
        return (
            self.kit_id is not None
            and self.source_path is not None
            and not self.skip_reason
        )


def plan_cover_attach(source: Path) -> list[CoverPlan]:
    articles = known_kit_articles()
    folders = resolve_source_folders(source)
    plans = []
    mapped_slugs = {slug for slug, _folder in folders}
    for slug, folder in folders:
        kit = MaintenanceKit.objects.filter(slug=slug).first()
        chosen, reason = pick_cover_image(folder, articles, slug=slug)
        plan = CoverPlan(
            slug=slug,
            folder=folder,
            kit_id=kit.pk if kit else None,
            source_path=chosen,
            skip_reason='',
            previous_name=str(kit.cover) if kit and kit.cover else '',
            would_replace=bool(kit and kit.cover),
        )
        if kit is None:
            plan.skip_reason = 'комплект с этим slug ещё не создан'
        elif chosen is None:
            plan.skip_reason = reason
        plans.append(plan)
    for spec in KIT_SPECS:
        if spec['slug'] not in mapped_slugs:
            kit = MaintenanceKit.objects.filter(slug=spec['slug']).first()
            plans.append(
                CoverPlan(
                    slug=spec['slug'],
                    folder=source,
                    kit_id=kit.pk if kit else None,
                    source_path=None,
                    skip_reason='папка источника не найдена',
                )
            )
    return plans


def _optimize_cover_file(path: Path) -> ContentFile:
    uploaded = SimpleUploadedFile(
        path.name,
        path.read_bytes(),
        content_type='application/octet-stream',
    )
    return optimize_uploaded_image(uploaded)


def apply_cover_attach(plans: list[CoverPlan]) -> list[CoverPlan]:
    for plan in plans:
        if not plan.can_apply:
            continue
        kit = MaintenanceKit.objects.get(pk=plan.kit_id)
        previous_name = kit.cover.name if kit.cover else ''
        content = _optimize_cover_file(plan.source_path)
        filename = content.name or f'{plan.slug}.webp'
        kit.cover.save(filename, content, save=True)
        if previous_name and previous_name != kit.cover.name:
            if default_storage.exists(previous_name):
                default_storage.delete(previous_name)
        plan.previous_name = previous_name
        plan.would_replace = bool(previous_name)
    return plans


def format_cover_report(plans: list[CoverPlan], *, apply: bool) -> str:
    mode = 'apply' if apply else 'dry-run'
    lines = [f'mode: {mode}']
    for plan in plans:
        lines.append(f'--- {plan.slug} ---')
        lines.append(f'folder: {plan.folder}')
        if plan.kit_id:
            lines.append(f'kit_id: {plan.kit_id}')
        else:
            lines.append('kit_id: missing')
        if plan.source_path:
            lines.append(f'source: {plan.source_path.name}')
        if plan.skip_reason:
            lines.append(f'result: SKIP ({plan.skip_reason})')
        elif apply:
            action = 'replaced' if plan.would_replace else 'attached'
            lines.append(f'result: {action}')
        else:
            action = 'replace' if plan.would_replace else 'attach'
            lines.append(f'result: WOULD {action}')
    return '\n'.join(lines)
