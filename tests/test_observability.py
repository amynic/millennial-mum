"""Observability setup tests."""

import builtins
import unittest
from unittest.mock import patch

from agents import observability


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        observability._CONFIGURED = False
        self.addCleanup(setattr, observability, "_CONFIGURED", False)

    def test_missing_exporter_is_an_error_when_tracing_is_configured(self):
        original_import = builtins.__import__

        def missing_exporter(name, *args, **kwargs):
            if name == "azure.monitor.opentelemetry.exporter":
                raise ImportError("No module named 'azure.monitor'")
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=missing_exporter),
            self.assertLogs(observability.logger, level="ERROR") as logs,
        ):
            configured = observability.setup_observability(
                connection_string="InstrumentationKey=test"
            )

        self.assertFalse(configured)
        self.assertIn("traces will not reach Application Insights", logs.output[0])
