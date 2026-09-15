"""Direct-specialist fast path — the fix for time-to-first-token.

The problem
-----------
In the router-as-tools flow the parent waits for **three** sequential model
generations before seeing a single character:

1. the orchestrator decides which specialist to call (a tool-call generation),
2. the specialist generates its *entire* answer (the orchestrator is blocked on
   the tool result — nothing streams during this),
3. the orchestrator regenerates that answer in the Katherine Ryan voice, and
   only *this* third generation is what streams to the PWA.

That serialisation is why time to first token sits at 50-70s while total time is
28-80s: the visible stream can't start until two full generations have finished.

The fix
-------
For a turn that is confidently a *single* domain, skip steps 1 and 3. A cheap
one-label classifier picks the domain (a handful of output tokens), then that
specialist streams straight to the parent. First token then costs roughly one
classification plus one generation's first token instead of two complete
generations.

Correctness guards
------------------
The fast path only triggers when the classifier commits to exactly one domain.
Anything else — multi-domain, memory-dependent, small talk, an unparseable
answer, or a classifier error — falls back to the untouched orchestrated path,
so the fallback is always the previously shipped behaviour.

``health`` is excluded by default (``MM_FAST_PATH_EXCLUDE``). Toddler health is
the safety-critical domain with the hardened NHS-only prompt and an evaluated
safety score of 1.0; it keeps full orchestration until the fast path has its own
safety evaluation. Because a single specialist now speaks to the parent directly
it also owns the final voice, via ``DIRECT_REPLY_PROMPT``.
"""

from __future__ import annotations

import logging
import os

from agent_framework import Agent

from agents.clients import client_for, options_for
from agents.config import (
    AGENT_DESCRIPTIONS,
    AGENT_NAMES,
    DIRECT_REPLY_PROMPT,
    ROUTER_PROMPT,
)
from agents.latency import env_flag
from agents.specialists import SPECIALIST_DOMAINS, specialist_instructions

logger = logging.getLogger("millennial_mum.fast_path")

#: Classifier labels that deliberately mean "do not fast path".
FALLBACK_LABELS = frozenset({"multi", "memory", "unsure", "none"})

#: Domains kept on the orchestrated path regardless of the classifier. Health is
#: safety-critical and evaluated as such.
DEFAULT_EXCLUDED_DOMAINS = ("health",)

#: Conversation turns handed to the classifier. Routing only needs the latest
#: request plus a little context, and every extra token is latency in the hop
#: that sits directly in front of time-to-first-token.
ROUTER_CONTEXT_TURNS = 4

#: Per-turn character cap for each message given to the classifier.
ROUTER_TURN_CHARS = 600


def fast_path_enabled() -> bool:
    """Whether the direct-specialist fast path is on (``MM_FAST_PATH``)."""
    return env_flag("MM_FAST_PATH", True)


def excluded_domains() -> frozenset[str]:
    """Domains that must always use full orchestration (``MM_FAST_PATH_EXCLUDE``).

    A blank value means "unset", not "exclude nothing". ``azure.yaml`` substitutes
    an empty string for an azd variable that was never set, so treating blank as
    an explicit empty list would silently drop Health onto the fast path — the
    one domain that must not go there by accident. Clearing the list is possible,
    but it has to be deliberate: set the value to ``none``.
    """
    raw = os.getenv("MM_FAST_PATH_EXCLUDE")
    if raw is None or not raw.strip():
        return frozenset(DEFAULT_EXCLUDED_DOMAINS)
    if raw.strip().lower() == "none":
        return frozenset()
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def parse_route_label(text: str | None) -> str | None:
    """Map a classifier reply to a fast-pathable domain, or ``None``.

    ``None`` means "use the orchestrated path" and is returned for every
    uncertain, multi-domain, memory-dependent, excluded, or unrecognised answer.
    The classifier is told to emit a bare label, but models add stray
    punctuation, backticks, or a trailing full stop often enough that we
    normalise rather than trust the format.
    """
    if not text:
        return None

    label = text.strip().strip("`\"'*. \n\t").lower()
    if not label:
        return None

    # Guard against a chatty answer: a real label is a single short token.
    if len(label.split()) != 1 or len(label) > 32:
        return None

    label = label.replace("-", "_")
    if label in FALLBACK_LABELS:
        return None
    if label not in SPECIALIST_DOMAINS:
        return None
    if label in excluded_domains():
        return None
    return label


def _message_parts(messages) -> list[tuple[str, str]]:
    """Normalise agent input into ``(role, text)`` pairs, newest last."""
    if isinstance(messages, str):
        return [("user", messages)]

    parts: list[tuple[str, str]] = []
    for message in messages or []:
        if isinstance(message, dict):
            role = message.get("role") or "user"
            text = message.get("content") or ""
        else:
            role = getattr(message, "role", None) or "user"
            text = getattr(message, "text", None) or ""
        role = getattr(role, "value", role)
        text = (text or "").strip()
        if text:
            parts.append((str(role), text))
    return parts


def router_input(messages) -> str | None:
    """Build the compact transcript the classifier sees.

    Returns ``None`` when there is no user text to classify, which callers treat
    as "don't fast path".
    """
    parts = _message_parts(messages)
    if not parts:
        return None
    if not any(role == "user" for role, _ in parts):
        return None

    recent = parts[-ROUTER_CONTEXT_TURNS:]
    lines = [f"{role}: {text[:ROUTER_TURN_CHARS]}" for role, text in recent]
    return "\n".join(lines)


class FastPathRouter:
    """Cheap single-label classifier plus the direct-reply specialist agents.

    Agents are built lazily and cached: constructing a Foundry client acquires
    credentials, and doing that on the request path would hand back part of the
    latency this class exists to remove.
    """

    def __init__(self):
        self._classifier: Agent | None = None
        self._direct: dict[str, Agent] = {}

    def classifier(self) -> Agent:
        if self._classifier is None:
            self._classifier = Agent(
                client=client_for("triage"),
                name="Route Classifier",
                description="Classifies a turn into a single specialist domain, or defers.",
                instructions=ROUTER_PROMPT,
                default_options=options_for("triage"),
            )
        return self._classifier

    def direct_specialist(self, domain: str) -> Agent:
        """A specialist that answers the parent directly, in the final voice."""
        if domain not in self._direct:
            self._direct[domain] = Agent(
                client=client_for(domain),
                name=AGENT_NAMES[domain],
                description=AGENT_DESCRIPTIONS[domain],
                instructions=specialist_instructions(domain) + DIRECT_REPLY_PROMPT,
                tools=_domain_tools(domain),
                default_options=options_for(domain),
            )
        return self._direct[domain]

    async def classify(self, messages) -> str | None:
        """Return the domain to fast path to, or ``None`` to orchestrate.

        Never raises: a classifier failure is a latency optimisation failing,
        not a turn failing, so it degrades to the orchestrated path.
        """
        if not fast_path_enabled():
            return None

        transcript = router_input(messages)
        if transcript is None:
            return None

        try:
            response = await self.classifier().run(transcript)
        except Exception as exc:  # pragma: no cover - depends on live Foundry
            logger.warning("Route classification failed, using orchestrated path: %s", exc)
            return None

        return parse_route_label(getattr(response, "text", None))


def _domain_tools(domain: str):
    # Imported here rather than at module import time to mirror specialists.py's
    # lazy posture: tool construction can touch blob storage configuration.
    from agents.tool_adapter import build_domain_tools

    return build_domain_tools(domain)
