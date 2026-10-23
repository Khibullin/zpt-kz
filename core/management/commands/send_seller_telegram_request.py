from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from core.models import Request, Seller
from core.services.workflow_notifications import send_one, eligible


class Command(BaseCommand):
    help = 'План одной персональной Telegram-отправки. Нужны явные --apply и --actor.'
    def add_arguments(self, parser):
        parser.add_argument('--request', type=int, required=True)
        parser.add_argument('--seller', type=int, required=True)
        parser.add_argument('--actor', type=int)
        parser.add_argument('--apply', action='store_true')
    def handle(self, *args, **options):
        req = Request.objects.get(pk=options['request'])
        seller = Seller.objects.get(pk=options['seller'])
        if not eligible(req, seller, 'telegram'):
            raise CommandError('Нет согласия, подключения или заявка недоступна.')
        if not options['apply']:
            self.stdout.write('План одной отправки. Сообщения не отправлялись.')
            return
        actor = get_user_model().objects.filter(pk=options['actor'], is_staff=True, is_active=True).first()
        if not actor or not actor.has_perm('core.add_requestsellernotification'):
            raise CommandError('Нужен сотрудник с разрешением на отправку.')
        try:
            send_one(req, seller, actor)
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write('Telegram принял сообщение. Прочтение и контакт не подтверждены.')
