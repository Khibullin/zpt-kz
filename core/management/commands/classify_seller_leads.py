"""Classify stored SellerLead text. No network, no messages, no seller rows."""

from django.core.management.base import BaseCommand, CommandError

from core.models import SELLER_LEAD_BUSINESS_TYPE_CHOICES, SellerLead
from core.services.seller_lead_classification import PROMOTABLE_LIFECYCLES, classify_seller_lead
from core.services.seller_lead_classification_selection import select_leads_needing_classification
from core.services.seller_lead_market import qualify_market

MAX_LIMIT = 20


class Command(BaseCommand):
    help = (
        'Классифицирует уже сохранённые SellerLead по тексту в базе. '
        'Не ходит в сеть, не отправляет сообщения и не создаёт продавцов.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--lead-id', action='append', dest='lead_ids', type=int, default=[])
        parser.add_argument('--city', default='')
        parser.add_argument('--limit', type=int, default=5)
        parser.add_argument('--business-type', default='')
        parser.add_argument(
            '--needs-classification',
            action='store_true',
            help=(
                'Лиды вне duplicate/rejected/closed без классификации, '
                'со свежими source/evidence, либо unknown старше 30 дней.'
            ),
        )
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        dry_run = bool(options['dry_run'])
        apply = bool(options['apply'])
        if dry_run and apply:
            raise CommandError('Укажите только один режим: --dry-run или --apply.')
        if not dry_run and not apply:
            raise CommandError('Укажите --dry-run или --apply. Без режима запись не выполняется.')

        business_type = str(options['business_type'] or '').strip()
        if business_type and business_type not in {value for value, _label in SELLER_LEAD_BUSINESS_TYPE_CHOICES}:
            raise CommandError('Неизвестный --business-type.')
        limit = options['limit']
        if limit < 1 or limit > MAX_LIMIT:
            raise CommandError(f'--limit должен быть от 1 до {MAX_LIMIT}.')
        lead_ids = options['lead_ids'] or []
        if not options['needs_classification'] and not lead_ids and not options['city']:
            raise CommandError('Укажите --lead-id, --city или --needs-classification.')

        if options['needs_classification']:
            leads = list(select_leads_needing_classification(
                city=options['city'],
                business_type=business_type,
                lead_ids=lead_ids,
                limit=limit,
            ))
        else:
            queryset = SellerLead.objects.all()
            if lead_ids:
                queryset = queryset.filter(pk__in=lead_ids)
            if options['city']:
                queryset = queryset.filter(city=options['city'])
            if business_type:
                queryset = queryset.filter(business_type=business_type)
            leads = list(queryset.order_by('pk')[:limit])
        if not leads:
            raise CommandError('SellerLead для классификации не найден.')

        for lead in leads:
            planned = classify_seller_lead(lead, dry_run=True)
            if dry_run:
                market, reason = qualify_market(lead)
                self.stdout.write(
                    f'#{lead.pk} {lead.name} | dry-run | business_type={planned} | '
                    f'market={market} | {reason} | lifecycle={lead.lifecycle_status}'
                )
                continue
            previous = lead.lifecycle_status
            classify_seller_lead(lead, promote_lifecycle=True)
            lead.refresh_from_db()
            self.stdout.write(
                f'#{lead.pk} {lead.name} | business_type={lead.business_type} | '
                f'confidence={lead.business_type_confidence} | '
                f'lifecycle={previous}->{lead.lifecycle_status}'
            )
            if previous in PROMOTABLE_LIFECYCLES and lead.lifecycle_status != previous:
                self.stdout.write(f'  lifecycle promoted to {lead.lifecycle_status}')
        if dry_run:
            self.stdout.write(self.style.WARNING('Dry-run: записи в базу не сохранялись.'))
        else:
            self.stdout.write(self.style.SUCCESS(
                'Классификация завершена. Продавцы, пользователи и товары не создавались. '
                'Сообщения не отправлялись.',
            ))
