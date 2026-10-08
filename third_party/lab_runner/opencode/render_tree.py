"""Compatibility alias: the component-tree renderer lives in `opencode.render`.

This module re-exports rather than re-implements, so the c1-c8 destinations have exactly
one definition.
"""

from __future__ import annotations

from opencode.render import (  # noqa: F401
    ALWAYS_ON,
    AGENT_NAME,
    ENV_DELTA,
    RESERVED_SCRIPTS,
    RESERVED_SKILLS,
    RenderResult,
    compose_body,
    is_tree,
    plugin_tool_names,
    render,
    render_tree,
)

__all__ = [
    "ALWAYS_ON",
    "AGENT_NAME",
    "ENV_DELTA",
    "RESERVED_SCRIPTS",
    "RESERVED_SKILLS",
    "RenderResult",
    "compose_body",
    "is_tree",
    "plugin_tool_names",
    "render",
    "render_tree",
]
