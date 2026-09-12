import os
from unittest.mock import call, patch

from django.test import SimpleTestCase

from backend.startup_tasks import run_startup_tasks


class RepricerStartupTaskTests(SimpleTestCase):
    @patch("backend.startup_tasks.call_command")
    def test_default_startup_is_noop(self, call_command):
        with patch.dict(
            os.environ,
            {
                "RUN_MIGRATIONS_ON_STARTUP": "",
                "RUN_KASPI_REPRICER_PILOT_ON_STARTUP": "",
            },
            clear=False,
        ):
            run_startup_tasks()

        call_command.assert_not_called()

    @patch("backend.startup_tasks.call_command")
    def test_migration_flag_runs_only_migrate(self, call_command):
        with patch.dict(
            os.environ,
            {
                "RUN_MIGRATIONS_ON_STARTUP": "true",
                "RUN_KASPI_REPRICER_PILOT_ON_STARTUP": "",
            },
            clear=False,
        ):
            run_startup_tasks()

        call_command.assert_called_once_with(
            "migrate",
            interactive=False,
            verbosity=1,
        )

    @patch("backend.startup_tasks.call_command")
    def test_pilot_bootstrap_failure_does_not_break_web_start(self, call_command):
        call_command.side_effect = [None, RuntimeError("pilot failed")]
        with patch.dict(
            os.environ,
            {
                "RUN_MIGRATIONS_ON_STARTUP": "",
                "RUN_KASPI_REPRICER_PILOT_ON_STARTUP": "true",
            },
            clear=False,
        ):
            run_startup_tasks()

        self.assertEqual(
            call_command.call_args_list[:2],
            [
                call("migrate", interactive=False, verbosity=1),
                call("bootstrap_kaspi_repricer_pilot", "--apply", verbosity=1),
            ],
        )
