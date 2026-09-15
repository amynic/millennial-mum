"""Tests for the end-to-end latency instrumentation.

These cover the parts that must not drift: the shape of what gets measured, and
the privacy guard that stops prompt or reply text reaching telemetry.
"""

import os
import time
import unittest
from unittest import mock

from agents.latency import (
    REQUEST_ID_HEADER,
    TurnTimer,
    env_flag,
    extract_request_id,
    safe_attributes,
)
from api.function_app import _resolve_request_id


class SafeAttributeTests(unittest.TestCase):
    def test_keeps_short_scalars(self):
        self.assertEqual(
            safe_attributes({"domain": "kitchen", "turns": 3, "cold": True, "ms": 12.5}),
            {"domain": "kitchen", "turns": 3, "cold": True, "ms": 12.5},
        )

    def test_drops_long_strings_so_prompts_cannot_leak(self):
        prompt = "my two year old has a temperature of 38.5 and won't eat anything " * 3
        self.assertEqual(safe_attributes({"query": prompt}), {})

    def test_drops_non_scalars(self):
        self.assertEqual(
            safe_attributes({"messages": ["hello"], "profile": {"child": "Sam"}, "nothing": None}),
            {},
        )


class TurnTimerTests(unittest.TestCase):
    def test_records_stage_durations_and_totals(self):
        timer = TurnTimer("hosted_agent", "abc123", cold=False)
        with timer.stage("route_classify", domain="kitchen"):
            time.sleep(0.01)
        timer.mark_first_token()
        with timer.stage("specialist_call", domain="kitchen"):
            time.sleep(0.01)

        summary = timer.finish(route_mode="direct")

        self.assertEqual(summary["request_id"], "abc123")
        self.assertEqual(summary["component"], "hosted_agent")
        self.assertIs(summary["cold"], False)
        self.assertEqual(summary["route_mode"], "direct")
        self.assertEqual(
            [stage["stage"] for stage in summary["stages"]],
            ["route_classify", "specialist_call"],
        )
        self.assertGreater(summary["stages"][0]["duration_ms"], 0)
        self.assertEqual(summary["stages"][0]["domain"], "kitchen")
        self.assertGreater(summary["first_token_ms"], 0)
        self.assertGreaterEqual(summary["total_ms"], summary["first_token_ms"])

    def test_first_token_is_recorded_once(self):
        timer = TurnTimer("hosted_agent", "abc123", cold=False)
        first = timer.mark_first_token()
        time.sleep(0.01)
        self.assertEqual(timer.mark_first_token(), first)

    def test_failed_stage_is_recorded_and_reraised(self):
        timer = TurnTimer("hosted_agent", "abc123", cold=False)
        with self.assertRaises(ValueError):
            with timer.stage("specialist_call"):
                raise ValueError("boom")

        summary = timer.finish()
        self.assertTrue(summary["stages"][0]["failed"])

    def test_summary_is_content_free(self):
        timer = TurnTimer("hosted_agent", "abc123", cold=False)
        with timer.stage("build_conversation", query="a very long user question " * 10):
            pass
        summary = timer.finish(reply="the assistant reply text " * 10)

        rendered = repr(summary)
        self.assertNotIn("user question", rendered)
        self.assertNotIn("assistant reply", rendered)

    def test_missing_request_id_is_generated(self):
        timer = TurnTimer("hosted_agent", None, cold=False)
        self.assertTrue(timer.request_id)
        self.assertEqual(len(timer.request_id), 32)


class ExtractRequestIdTests(unittest.TestCase):
    def test_reads_from_a_dict(self):
        self.assertEqual(extract_request_id({REQUEST_ID_HEADER: "abc"}), "abc")

    def test_is_case_insensitive_over_raw_header_pairs(self):
        headers = [(b"Content-Type", b"application/json"), (b"X-Client-MM-Request-Id", b"abc")]
        self.assertEqual(extract_request_id(headers), "abc")

    def test_returns_none_when_absent(self):
        self.assertIsNone(extract_request_id({"content-type": "application/json"}))
        self.assertIsNone(extract_request_id(None))


class ProxyRequestIdTests(unittest.TestCase):
    """The proxy must accept the id from either transport, or mint one."""

    def _request(self, headers=None):
        return mock.Mock(headers=headers or {})

    def test_prefers_the_header(self):
        req = self._request({REQUEST_ID_HEADER: "from-header"})
        self.assertEqual(_resolve_request_id(req, {"request_id": "from-body"}), "from-header")

    def test_falls_back_to_the_body(self):
        self.assertEqual(_resolve_request_id(self._request(), {"request_id": "from-body"}), "from-body")

    def test_generates_one_when_the_caller_sends_nothing(self):
        generated = _resolve_request_id(self._request(), {})
        self.assertEqual(len(generated), 32)

    def test_truncates_a_hostile_id(self):
        self.assertEqual(len(_resolve_request_id(self._request(), {"request_id": "x" * 500})), 64)


class EnvFlagTests(unittest.TestCase):
    def test_defaults_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(env_flag("MM_FAST_PATH", True))
            self.assertFalse(env_flag("MM_FAST_PATH", False))

    def test_reads_truthy_and_falsy_spellings(self):
        for raw, expected in (("true", True), ("1", True), ("on", True), ("false", False), ("no", False)):
            with mock.patch.dict(os.environ, {"MM_FAST_PATH": raw}):
                self.assertIs(env_flag("MM_FAST_PATH", not expected), expected, raw)


if __name__ == "__main__":
    unittest.main()
