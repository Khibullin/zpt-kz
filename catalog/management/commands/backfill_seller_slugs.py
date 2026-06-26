from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils.text import slugify

from catalog.models import SellerProfile


class Command(BaseCommand):
    help = (
        'Заполняет пустые slug у SellerProfile на основе названия магазина. '
        'Использует ту же логику, что и SellerProfile.save().'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Показать изменения без записи в базу данных',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        sellers = SellerProfile.objects.filter(
            Q(slug__isnull=True) | Q(slug='')
        ).order_by('pk')

        total = sellers.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Профили без slug не найдены.'))
            return

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'Режим dry-run: будет обновлено профилей: {total}'
                )
            )
        else:
            self.stdout.write(f'Найдено профилей без slug: {total}')

        updated = 0

        for seller in sellers:
            preview_slug = self._preview_slug(seller)

            if dry_run:
                self.stdout.write(
                    f'  [{seller.pk}] {seller.name!r} -> {preview_slug}'
                )
                updated += 1
                continue

            seller.save()
            updated += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f'  [{seller.pk}] {seller.name!r} -> {seller.slug}'
                )
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'Dry-run завершён. Будет обновлено: {updated}. '
                    'Запустите без --dry-run для записи.'
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Готово. Обновлено профилей: {updated}')
            )

    def _preview_slug(self, seller):
        base_slug = slugify(seller.name, allow_unicode=True) if seller.name else ''
        if not base_slug:
            base_slug = 'seller'

        slug = base_slug
        counter = 1

        while SellerProfile.objects.filter(slug=slug).exclude(pk=seller.pk).exists():
            counter += 1
            slug = f'{base_slug}-{counter}'

        return slug
