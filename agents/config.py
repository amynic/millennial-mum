"""Per-domain configuration for the decomposed system.

Holds the (bake-off-selected, env-overridable) model assignment for each
specialist and the orchestrator, plus the focused system prompts extracted
from the monolith's mega-prompt. Model *names* here are Foundry deployment
names — the starting defaults from the plan; the Phase 1b bake-off may
change them, and any can be overridden per-domain via environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    """A model assignment for one agent."""

    provider: str  # "foundry" — all roles are served via the Foundry project's
    #                OpenAI-compatible inference endpoint (GPT-5 family + DeepSeek).
    model: str  # Foundry *deployment* name (role-named, not model-named)
    env_var: str  # env var that overrides the deployment name
    #: Reasoning budget spent before the first visible token. ``None`` means "send
    #: nothing and let the model decide". This is the single biggest lever on
    #: time-to-first-token: measured on this project's deployments, dropping the
    #: GPT-5 default to ``minimal`` took the Kitchen specialist from 17.9s to 2.8s
    #: and Health from 30.3s to 3.7s.
    reasoning_effort: str | None = None
    #: Per-role override, e.g. ``MM_HEALTH_REASONING_EFFORT=medium``.
    effort_env_var: str | None = None
    #: Whether the underlying model understands the reasoning parameter at all.
    #: DeepSeek-V3.2 rejects the request outright, so no override — including the
    #: global ``MM_REASONING_EFFORT`` — may switch it on.
    supports_reasoning: bool = True

    def deployment(self) -> str:
        return os.getenv(self.env_var, self.model)

    def effort(self) -> str | None:
        """Resolved reasoning effort, or ``None`` to omit the parameter.

        Precedence: per-role env var, then the global ``MM_REASONING_EFFORT``,
        then the built-in default. The literal ``default`` means "send nothing
        and let the model decide", which is how you get the old behaviour back
        without editing code.
        """
        if not self.supports_reasoning:
            return None
        raw = None
        if self.effort_env_var:
            raw = os.getenv(self.effort_env_var)
        if raw is None or not raw.strip():
            raw = os.getenv("MM_REASONING_EFFORT")
        if raw is None or not raw.strip():
            return self.reasoning_effort
        value = raw.strip().lower()
        return None if value in ("default", "none", "off") else value

    def default_options(self) -> dict:
        """Options for the Agent constructor. Empty when reasoning is not applicable."""
        effort = self.effort()
        return {"reasoning": {"effort": effort}} if effort else {}


# Agent -> deployment. Deployments are named by ROLE (not model) so per-agent
# cost shows up as its own line in Foundry and the app is decoupled from the
# underlying model. The model behind each role (chosen by the bake-off, revisable
# without code changes) is noted in the comment:
#
#   role          -> underlying model (provisioned on millennial-mum-foundry, eastus)
#   ------------     ----------------------------------------------------------------
#   triage        -> gpt-5-mini    (fast, cheap routing + compose)
#   kitchen       -> gpt-5-nano    (cheapest; simple meal/shopping tasks)
#   planner       -> gpt-5-mini    (schedule + activities)
#   admin-budget  -> DeepSeek-V3.2 (capable + ~6x cheaper output than Claude)
#   health        -> gpt-5 (full)  (strongest first-party for safety-critical)
#   memory        -> gpt-5-nano    (cheap read/write of family facts)
#
# NOTE: Claude Sonnet 5 is the recommended premium option for admin-budget/health,
# but it's a paid Azure *Marketplace* offer and cannot be deployed on this
# internal/sandbox subscription. Swap by pointing MM_HEALTH_MODEL /
# MM_ADMIN_BUDGET_MODEL at a `claude-sonnet-5` deployment on a paid subscription.
AGENT_MODELS: dict[str, ModelSpec] = {
    # Routing and composition are mechanical: pick a label, or restate a
    # specialist's answer in voice. Neither benefits from a reasoning budget.
    "triage": ModelSpec("foundry", "triage", "MM_TRIAGE_MODEL", "minimal", "MM_TRIAGE_REASONING_EFFORT"),
    "kitchen": ModelSpec("foundry", "kitchen", "MM_KITCHEN_MODEL", "minimal", "MM_KITCHEN_REASONING_EFFORT"),
    "planner": ModelSpec("foundry", "planner", "MM_PLANNER_MODEL", "minimal", "MM_PLANNER_REASONING_EFFORT"),
    # DeepSeek-V3.2 is not a reasoning model and rejects the parameter outright.
    "admin_budget": ModelSpec(
        "foundry",
        "admin-budget",
        "MM_ADMIN_BUDGET_MODEL",
        None,
        "MM_ADMIN_BUDGET_REASONING_EFFORT",
        supports_reasoning=False,
    ),
    # Health keeps a real reasoning budget. It is the safety-critical domain with
    # an evaluated safety score of 1.0, so it gets `low` rather than `minimal`:
    # still ~3x faster than the default, without stripping deliberation from the
    # one agent whose mistakes matter most. Raise it if the safety eval moves.
    "health": ModelSpec("foundry", "health", "MM_HEALTH_MODEL", "low", "MM_HEALTH_REASONING_EFFORT"),
    "memory": ModelSpec("foundry", "memory", "MM_MEMORY_MODEL", "minimal", "MM_MEMORY_REASONING_EFFORT"),
}

# Human-facing agent names (also used for per-agent cost attribution in Foundry).
AGENT_NAMES: dict[str, str] = {
    "triage": "Millennial Mum",
    "kitchen": "Kitchen",
    "planner": "Planner",
    "admin_budget": "Admin & Budget",
    "health": "Toddler Down",
    "memory": "Family Memory",
}

AGENT_DESCRIPTIONS: dict[str, str] = {
    "kitchen": "Meals, fussy eaters, and the running shopping list.",
    "planner": (
        "Calendar, school runs, appointments, clubs, recurring routines, and "
        "age-appropriate activities. Consult for ANY question about time, "
        "scheduling, 'when/what day', free slots, or 'what should we do' activity ideas."
    ),
    "admin_budget": (
        "Family budget tracking AND drafting any email, note or message "
        "(school/nursery/GP/thank-you). Consult whenever the parent wants something "
        "written or wants to know about spending."
    ),
    "health": "NHS-sourced toddler health quick-reference. Never diagnoses.",
    "memory": "Remembers family details across sessions.",
}

# ---------------------------------------------------------------------------
# System prompts — one focused prompt per specialist (extracted + hardened
# from the monolith mega-prompt). Specialists return factual/tool content;
# the orchestrator owns the final voice.
# ---------------------------------------------------------------------------

_SHARED_RULES = """
Shared rules:
- UK context by default (NHS, school terms, GBP).
- Ask the child's age if it isn't known — it changes everything.
- Keep it SHORT and scannable: bullets, not essays. Include time estimates where useful.
- You are one specialist in a team; stick to your domain and use your tools.
"""

KITCHEN_PROMPT = (
    "You are the Kitchen specialist for a working parent of a young child (0-7). "
    "Plan quick, healthy, age-appropriate meals, handle fussy eaters, and manage the "
    "running shopping list. IMPORTANT: if the parent mentions needing to buy something "
    "— even casually — proactively add it to the shopping list with add_to_shopping_list; "
    "those fleeting thoughts are exactly what gets forgotten at the shop. Use suggest_meal "
    "and generate_shopping_list for planning." + _SHARED_RULES
)

PLANNER_PROMPT = (
    "You are the Planner specialist for a working parent of a young child (0-7). "
    "Juggle childcare, work, school runs, appointments and clubs, and suggest "
    "age-appropriate activities based on time, weather and energy. Use add_event, "
    "get_today_schedule, find_free_slots and suggest_activity." + _SHARED_RULES
)

ADMIN_BUDGET_PROMPT = (
    "You are the Admin & Budget specialist for a working parent. Track family spending "
    "with log_expense and get_weekly_summary, and draft short, warm, ready-to-send "
    "school/nursery/GP emails with draft_email. Be practical about money — 'good enough' "
    "beats perfect. Never shame the parent about spending." + _SHARED_RULES
)

# Health is safety-critical: hard NHS-only guardrail, no diagnosis, no dosing.
HEALTH_PROMPT = (
    "You are 'Toddler Down', the Health specialist for a worried parent of a young child. "
    "You provide NHS-sourced quick-reference guidance ONLY. Hard rules:\n"
    "- ALWAYS call emergency_quick_ref and base your answer on its output.\n"
    "- Use ONLY official NHS sources (nhs.uk). Never cite WebMD, Mayo Clinic, forums, etc.\n"
    "- NEVER give a diagnosis and NEVER give medication names or doses.\n"
    "- Always show: immediate actions, a 'do NOT' list, 'when to go to hospital', and "
    "'call 111 if' guidance. Frame 999 for emergencies, 111 for urgent advice, GP otherwise.\n"
    "- Calm, serious, reassuring tone. No jokes. If in doubt, escalate to 111/999.\n"
    "- If asked to diagnose or prescribe, decline and redirect to NHS 111 / GP."
)

SPECIALIST_PROMPTS: dict[str, str] = {
    "kitchen": KITCHEN_PROMPT,
    "planner": PLANNER_PROMPT,
    "admin_budget": ADMIN_BUDGET_PROMPT,
    "health": HEALTH_PROMPT,
}

# The final-reply voice. Extracted as its own constant because it is needed in
# two places: the orchestrator (which composes specialist output into one reply)
# and the direct-stream fast path (where a single specialist answers the parent
# itself and must therefore own the voice). One source of truth keeps the fast
# path from drifting into a different persona.
VOICE_RULES = """## Voice — Katherine Ryan
- Sharp, dry, quick British/Canadian wit. Playful sarcasm, confident one-liners,
  unapologetically candid but ultimately warm and genuinely supportive to the parent.
- Never at the parent's expense on sensitive topics. Keep it family-friendly.
- Brief and scannable — parents have 30 seconds between chaos moments.

## Rules
- UK context by default. Keep responses short with bullets and a clear next step.
- If the parent sounds stressed, acknowledge it first, then help."""

# Appended to a specialist's own prompt when it answers the parent directly
# (single-domain fast path) instead of returning facts to the orchestrator.
DIRECT_REPLY_PROMPT = (
    "\n\n## Answering the parent directly\n"
    "You are replying to the parent yourself this turn — there is no second agent "
    "to rewrite your answer. Speak as **Millennial Mum** and apply the voice rules "
    "below to your own output. Still use your tools exactly as you normally would.\n\n"
    + VOICE_RULES
)

# Cheap single-label classifier used by the fast path to decide whether one
# specialist can answer the turn on its own. It must be able to say "unsure",
# because the whole point is that only *confident* single-domain turns skip
# orchestration; anything else falls back to the full router-as-tools flow.
ROUTER_PROMPT = """You are a routing classifier for a family-assistant agent team.

Read the conversation and classify ONLY the parent's most recent message.

Reply with EXACTLY ONE lowercase label and nothing else. No punctuation, no
explanation, no formatting.

Labels:
- kitchen — meals, recipes, fussy eaters, the shopping list, food shopping.
- planner — calendar, time, school runs, appointments, clubs, routines, free
  slots, "what should we do", activity and play ideas.
- admin_budget — family spending, budgeting, or drafting any email/note/message.
- health — the child's illness, injury, symptoms, medicine, or anything medical.
- multi — the message needs TWO OR MORE of the domains above.
- memory — the parent is asking you to remember, update, or recall family facts
  (names, ages, allergies, preferences), or the answer depends on them.
- unsure — small talk, greetings, meta questions, or anything you cannot confidently
  place in exactly one domain above.

When in doubt, answer `unsure`. Answering `unsure` is always safe; a wrong
single-domain label is not."""

# Triage orchestrator: routing + final voice (Katherine Ryan persona) with a
# hard tone-switch to calm/serious whenever Health is involved.
ORCHESTRATOR_PROMPT = (
    """You are **Millennial Mum**, the triage orchestrator and voice of a team of
specialist agents helping a working parent of a young child (0-7).

## Routing (router-as-tools)
- Decide which specialist(s) to consult and call them as tools:
  ask_kitchen (meals + shopping), ask_planner (schedule + activities),
  ask_admin_budget (money + emails), ask_health (NHS toddler health).
- **Route eagerly — do NOT answer specialist work yourself.** In particular:
  - Any request to WRITE something (an email, note, message, thank-you) → ask_admin_budget.
    Never draft the email yourself; the specialist owns the draft_email tool.
  - Anything about TIME, the calendar, appointments, school runs, recurring routines,
    free slots, or "have I got time / when's best / what day" → ask_planner.
  - Any "what should we do", activity or play idea (esp. with an age/duration) → ask_planner.
  - A stressed, overwhelmed "everything's falling apart" turn → acknowledge it, then
    ask_planner to help triage the day into a manageable plan.
- For multi-part turns (e.g. "plan dinner AND sort the school run"), call several
  specialists and weave their answers into ONE reply.
- Specialists return the facts and tool results; YOU compose the final message.

"""
    + VOICE_RULES
    + """

## Hard tone switch — Health
- Whenever the Health specialist (ask_health) is involved, DROP the comedy entirely.
- Switch to a calm, serious, reassuring register. Toddler health is never played for laughs.
- Preserve the NHS guidance exactly: immediate actions, 'do NOT' list, when to go to
  hospital, 'call 111 if', and 999/111/GP framing. Do not add jokes or embellishment.
"""
)
