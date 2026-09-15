"""Reasoning-effort configuration.

Reasoning budget turned out to be the dominant cost in time-to-first-token —
far larger than the routing architecture. These tests pin the resolution rules,
and in particular that a non-reasoning model can never be handed the parameter.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from agents.clients import options_for
from agents.config import AGENT_MODELS, ModelSpec

REASONING_DOMAINS = ("triage", "kitchen", "planner", "health", "memory")


class DefaultsTests(unittest.TestCase):
    def setUp(self):
        # Inherited overrides would mask the built-in defaults.
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_reasoning_models_default_to_a_reduced_budget(self):
        for domain in REASONING_DOMAINS:
            with self.subTest(domain=domain):
                self.assertIsNotNone(AGENT_MODELS[domain].effort())

    def test_routing_and_composition_use_the_smallest_budget(self):
        # Picking a label or restating an answer needs no deliberation, and this
        # hop sits directly in front of time-to-first-token.
        self.assertEqual(AGENT_MODELS["triage"].effort(), "minimal")

    def test_health_keeps_a_real_budget(self):
        # Safety-critical: faster than the model default, but not stripped to
        # minimal like the others.
        self.assertEqual(AGENT_MODELS["health"].effort(), "low")

    def test_non_reasoning_model_sends_nothing(self):
        self.assertIsNone(AGENT_MODELS["admin_budget"].effort())
        self.assertEqual(options_for("admin_budget"), {})

    def test_option_shape_matches_the_responses_api(self):
        self.assertEqual(options_for("kitchen"), {"reasoning": {"effort": "minimal"}})


class OverrideTests(unittest.TestCase):
    def test_global_override_applies_to_reasoning_models(self):
        with mock.patch.dict(os.environ, {"MM_REASONING_EFFORT": "medium"}):
            for domain in REASONING_DOMAINS:
                with self.subTest(domain=domain):
                    self.assertEqual(options_for(domain), {"reasoning": {"effort": "medium"}})

    def test_no_override_can_switch_on_an_unsupported_model(self):
        # DeepSeek rejects the request outright, so this would be an outage, not
        # a slow reply.
        for env in (
            {"MM_REASONING_EFFORT": "high"},
            {"MM_ADMIN_BUDGET_REASONING_EFFORT": "high"},
        ):
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                self.assertEqual(options_for("admin_budget"), {})

    def test_per_role_override_beats_the_global_one(self):
        env = {"MM_REASONING_EFFORT": "medium", "MM_KITCHEN_REASONING_EFFORT": "high"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(options_for("kitchen"), {"reasoning": {"effort": "high"}})
            self.assertEqual(options_for("planner"), {"reasoning": {"effort": "medium"}})

    def test_default_sentinel_restores_model_behaviour(self):
        # The escape hatch for reproducing the original slow baseline.
        for value in ("default", "none", "off", "DEFAULT"):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"MM_REASONING_EFFORT": value}
            ):
                self.assertEqual(options_for("kitchen"), {})

    def test_blank_override_falls_back_to_the_built_in_default(self):
        # Deployment templates substitute "" for variables nobody set.
        for value in ("", "   "):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"MM_REASONING_EFFORT": value}
            ):
                self.assertEqual(options_for("kitchen"), {"reasoning": {"effort": "minimal"}})

    def test_value_is_normalised(self):
        with mock.patch.dict(os.environ, {"MM_REASONING_EFFORT": "  HIGH  "}):
            self.assertEqual(options_for("kitchen"), {"reasoning": {"effort": "high"}})


class SpecTests(unittest.TestCase):
    def test_reasoning_is_supported_unless_opted_out(self):
        spec = ModelSpec("foundry", "x", "MM_X")
        self.assertTrue(spec.supports_reasoning)
        self.assertIsNone(spec.effort())

    def test_every_domain_has_an_effort_override_hook(self):
        for domain, spec in AGENT_MODELS.items():
            with self.subTest(domain=domain):
                self.assertTrue(spec.effort_env_var, f"{domain} has no per-role override")


if __name__ == "__main__":
    unittest.main(verbosity=1)
