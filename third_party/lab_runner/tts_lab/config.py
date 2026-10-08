"""Paths and answer-free task data used by the LAB Codex runner."""
from __future__ import annotations
import json
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = BENCH_ROOT / "tasks"

def task_json(task_id: str) -> dict:
    data = json.loads((TASKS_DIR / task_id / "task.json").read_text(encoding="utf-8"))
    data.pop("criteria", None)
    return data

def documents_dir(task_id: str) -> Path:
    return TASKS_DIR / task_id / "documents"
