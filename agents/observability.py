"""Observability wiring for the decomposed Millennial Mum agents.

Emits OpenTelemetry traces (GenAI semantic conventions) for every orchestrator
hop and specialist/tool call, and ships them to **Application Insights** so the
router-as-tools flow is traceable end-to-end in the Foundry portal.

Design notes
------------
* Agent Framework already instruments agents/chat-clients/tools with OTel. We
  just need to configure a trace/log/metric *exporter* that points at App
  Insights, then AF's spans flow through automatically.
* We use the low-level ``azure-monitor-opentelemetry-exporter`` and hand the
  exporters to ``agent_framework.observability.configure_otel_providers`` so we
  don't fight AF over global provider ownership.
* Everything is imported lazily and guarded: an unset connection string is a
  deliberate no-op, while a missing exporter with a configured connection is
  logged as an error without crashing the app. Tracing is an operational
  nicety, never a hard dependency.

Enable by setting ``APPLICATIONINSIGHTS_CONNECTION_STRING`` (the Foundry project's
connected App Insights resource exposes this) before starting the app.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("millennial_mum.observability")

_CONFIGURED = False


def setup_observability(
    *,
    connection_string: str | None = None,
    enable_sensitive_data: bool | None = None,
) -> bool:
    """Configure OTel → Application Insights exporters for the agent app.

    Returns ``True`` if tracing was configured, ``False`` if it was skipped
    (no connection string, or exporter unavailable). A configured connection
    with an unavailable exporter is logged as an error. Safe to call more than
    once; only the first successful call takes effect.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return True

    conn = connection_string or os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        logger.info(
            "Observability disabled: set APPLICATIONINSIGHTS_CONNECTION_STRING "
            "(from the Foundry project's connected App Insights) to enable tracing."
        )
        return False

    # Whether to record prompt/response content on spans. Default off — toddler
    # health chats and family data shouldn't land in telemetry unless opted in.
    if enable_sensitive_data is None:
        enable_sensitive_data = os.getenv(
            "MM_TRACE_SENSITIVE_DATA", "false"
        ).lower() in ("1", "true", "yes")

    try:
        from azure.monitor.opentelemetry.exporter import (  # type: ignore
            AzureMonitorLogExporter,
            AzureMonitorMetricExporter,
            AzureMonitorTraceExporter,
        )
    except ImportError as exc:
        logger.error(
            "Observability is configured but the Azure Monitor exporter could not "
            "be imported (%s). Agent traces will not reach Application Insights.",
            exc,
        )
        return False

    try:
        from agent_framework.observability import configure_otel_providers
    except ImportError as exc:
        logger.error(
            "Observability is configured but the Agent Framework observability "
            "package could not be imported (%s). Agent traces will not reach "
            "Application Insights.",
            exc,
        )
        return False

    try:
        exporters = [
            AzureMonitorTraceExporter(connection_string=conn),
            AzureMonitorLogExporter(connection_string=conn),
            AzureMonitorMetricExporter(connection_string=conn),
        ]
        configure_otel_providers(
            enable_sensitive_data=enable_sensitive_data,
            exporters=exporters,
        )
        _CONFIGURED = True
        logger.info(
            "Observability enabled → Application Insights (sensitive_data=%s).",
            enable_sensitive_data,
        )
        return True
    except Exception as exc:  # pragma: no cover - depends on live Azure env
        logger.warning(
            "Observability setup failed (%s). The app will run without tracing.",
            exc,
        )
        return False
