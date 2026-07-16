from django.core.management.base import BaseCommand, CommandError

from core.models import BuyerBroadcastCampaign
from core.services.buyer_broadcast_service import (
    prepare_test_campaign,
    preview_test_campaign,
    process_buyer_broadcast_campaign,
)


class Command(BaseCommand):
    help = 'Предпросмотр, подготовка и тестовая отправка buyer broadcast кампаний.'

    def add_arguments(self, parser):
        parser.add_argument('--campaign-id', type=int, required=True)
        parser.add_argument('--prepare', action='store_true')
        parser.add_argument('--send', action='store_true')
        parser.add_argument('--limit', type=int)
        parser.add_argument('--recipient-id', type=int)

    def handle(self, *args, **options):
        if options['prepare'] and options['send']:
            raise CommandError('Нельзя одновременно использовать --prepare и --send.')

        campaign = BuyerBroadcastCampaign.objects.get(pk=options['campaign_id'])
        if options['prepare']:
            result = prepare_test_campaign(campaign)
            self._print_result(campaign, result, action='prepare')
            if result.errors:
                raise CommandError('; '.join(result.errors))
            return

        if options['send']:
            result = process_buyer_broadcast_campaign(
                campaign,
                limit=options.get('limit'),
                recipient_id=options.get('recipient_id'),
            )
            self._print_process_result(campaign, result)
            if result.errors:
                raise CommandError('; '.join(result.errors))
            return

        preview = preview_test_campaign(campaign)
        self._print_result(campaign, preview, action='dry-run')

    def _print_result(self, campaign, result, *, action: str):
        self.stdout.write(f'Кампания: {campaign.name} (#{campaign.pk})')
        self.stdout.write(f'Действие: {action}')
        self.stdout.write(f'Выбрано: {result.selected_count}')
        self.stdout.write(f'Допущено: {result.eligible_count}')
        self.stdout.write(f'Пропущено (не тест): {result.skipped_test_flag_count}')
        self.stdout.write(f'Пропущено (статус): {result.skipped_status_count}')
        self.stdout.write(f'Пропущено (согласие): {result.skipped_consent_count}')
        if action == 'prepare':
            self.stdout.write(f'Создано получателей: {result.created_recipient_count}')
            self.stdout.write(f'Существующих получателей: {result.existing_recipient_count}')
        for contact in result.contacts:
            line = (
                f'- {contact.masked_phone} | {contact.primary_city} | '
                f'marketing: {contact.marketing_consent_status}'
            )
            if contact.eligible:
                line += ' | допущен'
            else:
                line += f' | пропущен: {contact.skip_reason}'
            self.stdout.write(line)
        if result.errors:
            self.stdout.write('Ошибки:')
            for error in result.errors:
                self.stdout.write(f'- {error}')

    def _print_process_result(self, campaign, result):
        self.stdout.write(f'Кампания: {campaign.name} (#{campaign.pk})')
        self.stdout.write(f'Обработано: {result.processed_count}')
        self.stdout.write(f'Отправлено: {result.sent_count}')
        self.stdout.write(f'Ошибок: {result.failed_count}')
        self.stdout.write(f'Пропущено: {result.skipped_count}')
        self.stdout.write(f'Финальный статус: {result.final_status}')
        if result.errors:
            self.stdout.write('Ошибки:')
            for error in result.errors:
                self.stdout.write(f'- {error}')
