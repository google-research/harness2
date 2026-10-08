"""Translate an OpenCode `--format json` event stream into LAB run artifacts.

OpenCode emits one JSON object per line. `to_transcript` renders the stream as the
role/tool_calls records the native loop writes to transcript.jsonl, and `to_metrics`
produces the metrics.json fields `run_eval` reads.
"""

from __future__ import annotations

import json
from pathlib import Path


def iter_events(session_path: Path):
    """Yield parsed events, skipping the non-JSON lines OpenCode interleaves."""
    if not Path(session_path).exists():
        return
    for line in Path(session_path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def parse(session_path: Path) -> dict:
    """Fold the event stream into a summary dict."""
    steps = 0
    input_tokens = output_tokens = reasoning_tokens = 0
    cost = 0.0
    tool_calls: list[dict] = []
    texts: list[str] = []
    session_id = ""

    for ev in iter_events(session_path):
        session_id = ev.get("sessionID") or session_id
        part = ev.get("part") or {}
        ptype = part.get("type") or ev.get("type")

        if ptype == "step-start":
            steps += 1
        elif ptype == "step-finish":
            tok = part.get("tokens") or {}
            input_tokens += int(tok.get("input") or 0)
            output_tokens += int(tok.get("output") or 0)
            reasoning_tokens += int(tok.get("reasoning") or 0)
            cost += float(part.get("cost") or 0.0)
        elif ptype == "tool" and part.get("tool"):
            state = part.get("state") or {}
            if state.get("status") in (None, "completed", "error"):
                tool_calls.append({
                    "name": part["tool"],
                    "input": state.get("input") or {},
                    "output": state.get("output") or "",
                    "status": state.get("status") or "completed",
                })
        elif ptype == "text" and part.get("text"):
            texts.append(part["text"])

    return {
        "session_id": session_id,
        "steps": steps,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cost": cost,
        "tool_calls": tool_calls,
        "texts": texts,
        "final_text": texts[-1] if texts else "",
    }


def to_transcript(session_path: Path, transcript_path: Path) -> None:
    """Write transcript.jsonl: one record per assistant text and per tool call, in order."""
    records: list[dict] = []
    for ev in iter_events(session_path):
        part = ev.get("part") or {}
        ptype = part.get("type") or ev.get("type")
        ts = ev.get("timestamp")
        if ptype == "tool" and part.get("tool"):
            state = part.get("state") or {}
            if state.get("status") not in (None, "completed", "error"):
                continue
            records.append({
                "role": "tool",
                "timestamp": ts,
                "tool": part["tool"],
                "input": state.get("input") or {},
                "output": state.get("output") or "",
                "status": state.get("status") or "completed",
            })
        elif ptype == "text" and part.get("text"):
            records.append({"role": "assistant", "timestamp": ts, "text": part["text"]})
        elif ptype == "step-finish":
            records.append({"role": "usage", "timestamp": ts,
                            "tokens": part.get("tokens") or {}, "cost": part.get("cost")})

    with open(transcript_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


_READ_TOOLS = {"read", "Read"}


def doc_coverage(summary: dict, documents_dir: Path) -> tuple[int, int]:
    """(documents_read, total_documents) across every tool call that names a document.

    Matches on the filename stem, so a read of a derived text file still counts.
    """
    documents_dir = Path(documents_dir)
    all_docs = [p for p in documents_dir.rglob("*") if p.is_file()]
    stems = {p.stem: p.name for p in all_docs}
    seen: set[str] = set()

    for call in summary["tool_calls"]:
        if call["name"] in _READ_TOOLS:
            blob = str(call["input"].get("filePath") or call["input"].get("path") or "")
        else:
            blob = json.dumps(call["input"])
        for stem, name in stems.items():
            if stem in blob:
                seen.add(name)

    return len(seen), len(all_docs)
