from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase


class ProcessMarketingLiveSendsRecheckTests(SimpleTestCase):
    @patch(
        'marketing.management.commands.process_marketing_live_sends.'
        'marketing_live_whatsapp_send_enabled',
        return_value=False,
    )
    @patch(
        'marketing.management.commands.process_marketing_live_sends.'
        'process_seller_whatsapp_recheck_batch',
    )
    def test_recheck_runs_even_when_marketing_live_is_off(
        self,
        recheck_batch,
        live_enabled,
    ):
        recheck_batch.return_value = [
            SimpleNamespace(
                lead_id=57,
                outcome='enriched',
                found_whatsapp=True,
                active_sources=('website', 'brave'),
                remaining=7,
                error='',
            )
        ]
        stdout = StringIO()

        call_command('process_marketing_live_sends', stdout=stdout)

        recheck_batch.assert_called_once_with(batch_size=2)
        live_enabled.assert_called_once()
        text = stdout.getvalue()
        self.assertIn('SELLER_WHATSAPP_RECHECK lead=57 outcome=enriched', text)
        self.assertIn('found=1 remaining=7', text)
        self.assertIn('MARKETING_WHATSAPP_SEND_MODE is not LIVE', text)

    @patch(
        'marketing.management.commands.process_marketing_live_sends.'
        'marketing_live_whatsapp_send_enabled',
        return_value=False,
    )
    @patch(
        'marketing.management.commands.process_marketing_live_sends.'
        'process_seller_whatsapp_recheck_batch',
        side_effect=RuntimeError('test failure'),
    )
    def test_recheck_error_does_not_break_marketing_command(
        self,
        recheck_batch,
        live_enabled,
    ):
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            'process_marketing_live_sends',
            stdout=stdout,
            stderr=stderr,
        )

        recheck_batch.assert_called_once_with(batch_size=2)
        live_enabled.assert_called_once()
        self.assertIn('SELLER_WHATSAPP_RECHECK internal_error=RuntimeError', stderr.getvalue())
        self.assertIn('MARKETING_WHATSAPP_SEND_MODE is not LIVE', stdout.getvalue())
