"""Dual-registration shim for tools.

Each tool is decorated with ``@define_tool(...)`` from the GitHub Copilot
SDK. During decomposition we also need the *raw* implementation (a plain
async ``func(params: PydanticModel)``) to hand to Microsoft Agent Framework.

This shim wraps Copilot's ``define_tool`` so that decorating a function:

* still returns the Copilot ``Tool`` object (the monolith is unchanged), and
* records the underlying implementation in ``TOOL_IMPLS`` keyed by function
  name, so the Agent Framework adapter can build native ``FunctionTool``s.

Modules opt in by importing ``define_tool`` from here instead of from
``copilot`` — a one-line change with identical call semantics.
"""

from __future__ import annotations

from copilot import define_tool as _copilot_define_tool

# function name -> raw async implementation (undecorated)
TOOL_IMPLS: dict = {}


def define_tool(**kwargs):
    """Drop-in replacement for ``copilot.define_tool`` that also captures impl."""

    def decorator(func):
        TOOL_IMPLS[func.__name__] = func
        return _copilot_define_tool(**kwargs)(func)

    return decorator
