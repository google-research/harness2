"""Test-time harness adaptation for the Harvey LAB benchmark.

The base model is frozen; only the harness is adapted per task, either as one AGENTS.md
body or as the c1..c8 component tree (selected by TTS_LAB_HARNESS_MODE). Only `config`
ships here. Nothing in this package reads `criteria` from a task.json, so no rubric or
judge output can reach a proposer prompt.
"""

from __future__ import annotations

__all__ = ["config"]
