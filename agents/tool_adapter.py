"""Adapt Copilot-SDK tools into Microsoft Agent Framework tools.

The monolith's tools are ``copilot.Tool`` objects whose raw implementations
(``async func(params: PydanticModel)``) are captured in
``tools._dual.TOOL_IMPLS``. Agent Framework's ``FunctionTool`` invokes a
function as ``func(**arguments)``, so we wrap each impl to accept keyword
arguments, rebuild the Pydantic model, and call the original impl unchanged.

``build_domain_tools(domain)`` returns the Agent Framework ``FunctionTool``
list for a specialist, preserving the exact tool names and descriptions the
monolith exposed (so eval routing/tool-accuracy comparisons stay apples-to-apples).
"""

from __future__ import annotations

import inspect
import logging
from functools import wraps

from agent_framework import FunctionTool

from tools import DOMAIN_TOOLS
from tools._dual import TOOL_IMPLS

logger = logging.getLogger(__name__)


def _model_of(impl):
    """Recover the Pydantic params model from an impl's single argument."""
    params = list(inspect.signature(impl).parameters.values())
    if not params:
        raise ValueError(f"{impl.__name__} has no params model")
    return params[0].annotation


def _to_function_tool(name: str, description: str) -> FunctionTool:
    impl = TOOL_IMPLS[name]
    model = _model_of(impl)

    @wraps(impl)
    async def wrapper(**kwargs):
        # AF passes validated fields as kwargs; rebuild the model the impl expects.
        try:
            return await impl(model(**kwargs))
        except Exception:
            logger.exception("Tool %s failed", name)
            raise

    wrapper.__name__ = name
    return FunctionTool(name=name, description=description, func=wrapper, input_model=model)


def build_domain_tools(domain: str) -> list[FunctionTool]:
    """Agent Framework tools for one specialist domain."""
    copilot_tools = DOMAIN_TOOLS[domain]
    return [_to_function_tool(t.name, t.description) for t in copilot_tools]


def build_all_domain_tools() -> dict[str, list[FunctionTool]]:
    return {domain: build_domain_tools(domain) for domain in DOMAIN_TOOLS}
