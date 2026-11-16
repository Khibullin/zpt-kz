"""Run a bounded, explicitly authorised cycle of reviewed supplier photographs."""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from catalog.models import Product, SupplierPhotoTask
from catalog.source_photos import process_photo_tasks

REPORT_RECIPIENT = 'rkhaibullin@gmail.com'
PENDING = ['pending', 'prepared', 'maker_ready', 'web_ready']


def snapshot(product_ids):
    """Capture saved image and fitment state, not task-processing counts."""
    if not product_ids:
        return {}
    return {
        row[0]: (bool(row[1]), bool(row[2]), bool(row[3]))
        for row in Product.objects.filter(pk__in=product_ids)
        .values_list('pk', 'main_image', 'compatibility', 'engine_compatibility')
    }


class Command(BaseCommand):
    help = 'Process up to 100 reviewed photo tasks and email actual cycle results.'

    def add_arguments(self, parser):
        parser.add_argument('--run', action='store_true',
                            help='Required to write reviewed results.')
        parser.add_argument('--limit', type=int, default=10,
                            help='Maximum tasks this invocation handles (1..100).')

    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 100:
            raise CommandError('--limit must be between 1 and 100.')
        pending = SupplierPhotoTask.objects.filter(status__in=PENDING).count()
        if not options['run']:
            self.stdout.write(
                f'DRY RUN: pending/prepared general photo tasks={pending}. '
                f'Use --run --limit {limit} for an explicit manual pass.'
            )
            return
        start = timezone.now()
        # A bounded view of already prepared tasks; this is not a measure of
        # all reviewed variants that downstream worker functions might touch.
        candidates = list(SupplierPhotoTask.objects.filter(status__in=PENDING)
                          .order_by('pk').values_list('product_id', flat=True)[:limit])
        before = snapshot(candidates)
        processed = process_photo_tasks(limit=limit)
        after = snapshot(candidates)
        new_photos = sum(
            not value[0] and after.get(pk, (False, False, False))[0]
            for pk, value in before.items()
        )
        new_fitments = sum(
            (not value[1] and after.get(pk, (False, False, False))[1])
            or (not value[2] and after.get(pk, (False, False, False))[2])
            for pk, value in before.items()
        )
        finished = timezone.now()
        subject = 'ZPT.KZ — завершён цикл обработки товаров'
        body = (
            f'Начало: {timezone.localtime(start):%d.%m.%Y %H:%M}\n'
            f'Окончание: {timezone.localtime(finished):%d.%m.%Y %H:%M}\n'
            f'Режим: ручной, ограниченный цикл\n'
            f'Задач выполнено обработчиком: {processed}\n'
            f'Проверено изменений в карточках: {len(before)}\n'
            f'Новых фотографий среди проверенных карточек: {new_photos}\n'
            f'Новой применимости среди проверенных карточек: {new_fitments}\n'
            f'Осталось задач в очереди: '
            f'{SupplierPhotoTask.objects.filter(status__in=PENDING).count()}\n\n'
            'Важно: число выполненных задач не равно числу новых фотографий. '
            'Приведённые новые фотографии/применимость подсчитаны только по '
            'карточкам, попавшим в контрольную выборку. '
            'Возможно наличие результатов за её пределами.\n'
        )
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL,
                      [REPORT_RECIPIENT], fail_silently=False)
        except Exception as exc:
            self.stderr.write(f'Cycle completed, but email notification failed: {exc}')
        self.stdout.write(
            self.style.SUCCESS(
                f'Manual photo pass completed: processed={processed}, limit={limit}. '
                f'New photos in monitored sample={new_photos}; '
                f'new fitments in monitored sample={new_fitments}.'
            )
        )
