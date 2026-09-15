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
* Everything is imported lazily and guarded: if the connection string is unset
  or the exporter package isn't installed, we no-op with a warning instead of
  crashing the app. Tracing is an operational nicety, never a hard dependency.

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
    (no connection string, or exporter unavailable). Safe to call more than
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

    # On the Foundry hosted runtime the platform configures Azure Monitor itself
    # (microsoft-opentelemetry distro) from this same connection string, but it
    # runs *after* app import. Whoever sets the global providers first wins, and
    # OTel refuses any later override -- so configuring them here silently
    # disabled the platform's exporter and the app shipped no telemetry at all.
    # Defer to the platform when hosted; our spans/logs flow through its
    # exporter, which is already pointed at the right resource.
    if os.getenv("FOUNDRY_HOSTING_ENVIRONMENT"):
        _CONFIGURED = True
        logger.info(
            "Observability delegated to the Foundry hosted runtime; not claiming "
            "the global OTel providers (doing so would suppress its exporter)."
        )
        return True

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
        from agent_framework.observability import configure_otel_providers

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


def flush_telemetry(timeout_ms: int = 5_000) -> bool:
    """Force-export buffered spans and logs before the container can be frozen.

    The hosted agent runtime provisions a container per request and stops it as
    soon as the response completes. OTel's batch processors export on a timer
    (several seconds by default), so without an explicit flush the process is
    gone before anything is sent — which is why this app appeared to emit no
    telemetry at all despite being configured correctly.

    Returns ``True`` if every provider flushed cleanly. Never raises: telemetry
    must not be able to fail a user's turn.
    """
    if not _CONFIGURED:
        return False

    ok = True
    try:
        from opentelemetry import trace
        from opentelemetry._logs import get_logger_provider

        for provider in (trace.get_tracer_provider(), get_logger_provider()):
            force_flush = getattr(provider, "force_flush", None)
            if force_flush is None:
                continue
            try:
                ok = bool(force_flush(timeout_ms)) and ok
            except Exception as exc:  # pragma: no cover - exporter/network dependent
                logger.debug("Telemetry flush failed for %s: %s", type(provider).__name__, exc)
                ok = False
    except Exception as exc:  # pragma: no cover - OTel not installed
        logger.debug("Telemetry flush skipped (%s).", exc)
        return False
    return ok
