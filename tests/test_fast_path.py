"""Tests for the direct-specialist fast path.

The fast path is the change that removes two sequential model generations from
time to first token, so the risk it carries is *mis-routing*: sending a turn
straight to one specialist when it actually needed orchestration, memory, or the
safety-critical health flow. These tests pin the decision boundary and prove the
streaming code takes the route the classifier chose.
"""

import asyncio
import os
import unittest
from unittest import mock

from agents import fast_path
from agents.config import DIRECT_REPLY_PROMPT, VOICE_RULES
from agents.fast_path import fast_path_enabled, parse_route_label, router_input
from agents.orchestrator import Orchestrator, RunTrace


class ParseRouteLabelTests(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"MM_FAST_PATH_EXCLUDE": "health"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_accepts_each_fast_pathable_domain(self):
        for label in ("kitchen", "planner", "admin_budget"):
            self.assertEqual(parse_route_label(label), label)

    def test_tolerates_model_formatting(self):
        for raw in ("Kitchen", "`kitchen`", "kitchen.", " kitchen \n", "**kitchen**", "admin-budget"):
            self.assertEqual(parse_route_label(raw), raw.strip(" `*.\n").lower().replace("-", "_"))

    def test_deferring_labels_fall_back_to_orchestration(self):
        for label in ("multi", "memory", "unsure", "none"):
            self.assertIsNone(parse_route_label(label), label)

    def test_health_is_excluded_by_default(self):
        self.assertIsNone(parse_route_label("health"))

    def test_health_can_be_opted_in_explicitly(self):
        with mock.patch.dict(os.environ, {"MM_FAST_PATH_EXCLUDE": ""}):
            self.assertEqual(parse_route_label("health"), "health")

    def test_unknown_or_chatty_answers_fall_back(self):
        for raw in (None, "", "   ", "cooking", "I think this is a kitchen question", "a" * 40):
            self.assertIsNone(parse_route_label(raw), repr(raw))


class RouterInputTests(unittest.TestCase):
    def test_accepts_a_bare_string(self):
        self.assertEqual(router_input("what's for tea?"), "user: what's for tea?")

    def test_keeps_only_the_most_recent_turns(self):
        messages = [
            {"role": "user", "content": f"turn {i}"} for i in range(10)
        ]
        lines = router_input(messages).splitlines()
        self.assertEqual(len(lines), fast_path.ROUTER_CONTEXT_TURNS)
        self.assertEqual(lines[-1], "user: turn 9")

    def test_truncates_long_turns_to_keep_the_routing_hop_cheap(self):
        messages = [{"role": "user", "content": "x" * 5000}]
        line = router_input(messages)
        self.assertEqual(len(line), len("user: ") + fast_path.ROUTER_TURN_CHARS)

    def test_returns_none_without_user_text(self):
        self.assertIsNone(router_input([]))
        self.assertIsNone(router_input(None))
        self.assertIsNone(router_input([{"role": "assistant", "content": "hi"}]))
        self.assertIsNone(router_input([{"role": "user", "content": "   "}]))


class FastPathToggleTests(unittest.TestCase):
    def test_on_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(fast_path_enabled())

    def test_can_be_switched_off_for_a_baseline_run(self):
        with mock.patch.dict(os.environ, {"MM_FAST_PATH": "false"}):
            self.assertFalse(fast_path_enabled())


class DirectReplyPromptTests(unittest.TestCase):
    def test_direct_specialists_inherit_the_orchestrator_voice(self):
        # A specialist answering alone owns the final voice; if this drifts, the
        # fast path silently changes the product's persona.
        self.assertIn(VOICE_RULES, DIRECT_REPLY_PROMPT)


class _FakeUpdate:
    def __init__(self, text):
        self.text = text


class _FakeStream:
    """Stands in for Agent Framework's ResponseStream."""

    def __init__(self, chunks, final=None):
        self._chunks = chunks
        self._final = final

    def __aiter__(self):
        async def gen():
            for chunk in self._chunks:
                yield _FakeUpdate(chunk)

        return gen()

    async def get_final_response(self):
        return self._final


class _FakeAgent:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def run(self, messages, stream=False):
        self.calls.append(messages)
        return _FakeStream(self.chunks)


def _bare_orchestrator(direct_agent=None, orchestrator_agent=None, domain=None):
    """Build an Orchestrator without touching Foundry credentials.

    ``Orchestrator.__init__`` constructs five live chat clients, so the routing
    logic is exercised against a hand-assembled instance instead.
    """
    orchestrator = object.__new__(Orchestrator)
    orchestrator._trace = RunTrace()
    orchestrator._timer = None
    orchestrator.agent = orchestrator_agent or _FakeAgent(["orchestrated "])
    orchestrator._fast_path = mock.Mock()
    orchestrator._fast_path.classify = mock.AsyncMock(return_value=domain)
    orchestrator._fast_path.direct_specialist = mock.Mock(
        return_value=direct_agent or _FakeAgent(["direct "])
    )
    return orchestrator


async def _collect(agen):
    return [chunk async for chunk in agen]


class StreamRoutingTests(unittest.TestCase):
    def test_confident_single_domain_streams_the_specialist_directly(self):
        direct = _FakeAgent(["Right, ", "pasta and peas."])
        orchestrator = _bare_orchestrator(direct_agent=direct, domain="kitchen")

        chunks = asyncio.run(_collect(orchestrator.stream_traced("dinner ideas?")))

        self.assertEqual("".join(chunks), "Right, pasta and peas.")
        self.assertEqual(orchestrator.last_route_mode(), "direct")
        self.assertEqual(orchestrator.last_agents_used(), ["kitchen"])
        orchestrator._fast_path.direct_specialist.assert_called_once_with("kitchen")
        # The specialist must see the full conversation, not just a routed string.
        self.assertEqual(direct.calls, ["dinner ideas?"])

    def test_an_undecided_turn_uses_the_orchestrated_path(self):
        composed = _FakeAgent(["Composed reply."])
        orchestrator = _bare_orchestrator(orchestrator_agent=composed, domain=None)

        chunks = asyncio.run(_collect(orchestrator.stream_traced("plan dinner and the school run")))

        self.assertEqual("".join(chunks), "Composed reply.")
        self.assertEqual(orchestrator.last_route_mode(), "orchestrated")
        self.assertEqual(composed.calls, ["plan dinner and the school run"])
        orchestrator._fast_path.direct_specialist.assert_not_called()

    def test_disabling_the_fast_path_skips_classification_entirely(self):
        composed = _FakeAgent(["Composed reply."])
        orchestrator = _bare_orchestrator(orchestrator_agent=composed, domain="kitchen")

        with mock.patch.dict(os.environ, {"MM_FAST_PATH": "false"}):
            chunks = asyncio.run(_collect(orchestrator.stream_traced("dinner ideas?")))

        self.assertEqual("".join(chunks), "Composed reply.")
        self.assertEqual(orchestrator.last_route_mode(), "orchestrated")
        orchestrator._fast_path.classify.assert_not_awaited()

    def test_a_classifier_failure_degrades_to_orchestration(self):
        composed = _FakeAgent(["Composed reply."])
        orchestrator = _bare_orchestrator(orchestrator_agent=composed)
        # FastPathRouter.classify swallows its own errors and returns None; this
        # asserts the orchestrator honours that contract rather than raising.
        orchestrator._fast_path.classify = mock.AsyncMock(return_value=None)

        chunks = asyncio.run(_collect(orchestrator.stream_traced("dinner ideas?")))

        self.assertEqual("".join(chunks), "Composed reply.")
        self.assertEqual(orchestrator.last_route_mode(), "orchestrated")

    def test_timings_are_recorded_for_both_routes(self):
        from agents.latency import TurnTimer

        orchestrator = _bare_orchestrator(direct_agent=_FakeAgent(["hi"]), domain="planner")
        timer = TurnTimer("hosted_agent", "req-1", cold=False)

        asyncio.run(_collect(orchestrator.stream_traced("what's on today?", timer=timer)))
        summary = timer.finish()

        self.assertEqual([s["stage"] for s in summary["stages"]], ["route_classify"])
        self.assertEqual(summary["stages"][0]["route_mode"], "direct")
        self.assertEqual(summary["route_mode"], "direct")
        self.assertEqual(summary["domain"], "planner")
        self.assertIn("first_token_ms", summary)


class ClassifierFailureTests(unittest.TestCase):
    def test_classify_never_propagates_an_error(self):
        router = fast_path.FastPathRouter()
        failing = mock.Mock()
        failing.run = mock.AsyncMock(side_effect=RuntimeError("Foundry down"))
        router._classifier = failing

        self.assertIsNone(asyncio.run(router.classify("dinner ideas?")))


if __name__ == "__main__":
    unittest.main()
