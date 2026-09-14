"""Observability setup tests."""

import builtins
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

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

    def test_missing_connection_string_logs_disabled_and_no_ops(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            self.assertLogs(observability.logger, level="INFO") as logs,
        ):
            configured = observability.setup_observability()

        self.assertFalse(configured)
        self.assertIn("Observability disabled", logs.output[0])

    def test_available_exporters_configure_tracing(self):
        configure = Mock()
        exporter = Mock(side_effect=lambda **kwargs: kwargs)
        original_import = builtins.__import__

        def import_telemetry_modules(name, *args, **kwargs):
            if name == "azure.monitor.opentelemetry.exporter":
                return SimpleNamespace(
                    AzureMonitorLogExporter=exporter,
                    AzureMonitorMetricExporter=exporter,
                    AzureMonitorTraceExporter=exporter,
                )
            if name == "agent_framework.observability":
                return SimpleNamespace(configure_otel_providers=configure)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_telemetry_modules):
            configured = observability.setup_observability(
                connection_string="InstrumentationKey=test"
            )

        self.assertTrue(configured)
        configure.assert_called_once()
