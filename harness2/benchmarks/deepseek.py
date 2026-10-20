# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared local benchmark transport; benchmarks retain task and judge ownership."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from .. import config
from ..core import context
from ..core import runmark as RM
from ..core import tree as HT
from ..core.locking import file_lock
from ..substrate.base import ExecutionError, Install, RunRequest
from ..substrate.deepseek import MANIFEST, DeepSeekSubstrate


def _substrate(adapter) -> DeepSeekSubstrate:
    substrate = adapter.substrate
    if not isinstance(substrate, DeepSeekSubstrate):
        raise TypeError("DeepSeek benchmark transport requires DeepSeekSubstrate")
    return substrate


def task_identity(adapter, task_id: str) -> dict:
    digest = hashlib.sha256()
    for rel, path in context.source_files(adapter, task_id):
        if path.is_symlink():
            raise ValueError(f"DeepSeek task inputs must not contain symlinks: {path}")
        digest.update(rel.encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "task_id": task_id,
        "source_sha256": digest.hexdigest(),
        "prompt_sha256": hashlib.sha256(adapter.task_prompt(task_id).encode("utf-8")).hexdigest(),
    }


def base_harness(adapter, body: str, *, scripts: dict[str, str] | None = None) -> Path:
    substrate = _substrate(adapter)
    path = adapter.spec.runs_root / "_base_harness_ds"
    tree = {name: "" for name in substrate.surface().always_on}
    tree["systemprompt.md"] = body
    tree.update(scripts or {})
    with file_lock(path.with_suffix(".lock")):
        if HT.read_tree(substrate.surface(), path) != tree:
            HT.write_tree(substrate.surface(), tree, path)
    return path


def _artifact_root(evidence_dir: Path, output_dir: Path) -> Path:
    return evidence_dir / "output" if evidence_dir == output_dir else output_dir


def execute(
    adapter,
    task_id: str,
    harness_dir: Path | None,
    run_id: str,
    *,
    timeout: int,
    evidence_dir: Path,
    output_dir: Path,
) -> Path:
    substrate = _substrate(adapter)
    with file_lock(evidence_dir.with_name(evidence_dir.name + ".adapter.lock")):
        src = harness_dir if harness_dir is not None else adapter.base_harness()
        install = substrate.materialise(src, adapter.spec, {})
        try:
            try:
                substrate.execute(task_id, install, [run_id], timeout)
            except ExecutionError as exc:
                # The benchmark's public run directory is the artifact directory, not
                # necessarily the worker's evidence directory (notably JobBench).
                _export(evidence_dir, output_dir)
                raise ExecutionError(str(exc), run_dir=output_dir) from exc
            _export(evidence_dir, output_dir)
            RM.mark_complete(
                evidence_dir,
                run_id=run_id,
                harness_dir=harness_dir,
                extra={
                    "output_fingerprint": RM.fingerprint(_artifact_root(evidence_dir, output_dir)),
                    "source_harness": install.fingerprint,
                },
            )
            return output_dir
        finally:
            shutil.rmtree(install.staging_dir)


def _export(evidence_dir: Path, output_dir: Path) -> None:
    source = evidence_dir / "output"
    if source == output_dir / "output":
        return  # LAB exposes the whole run directory.
    if source == output_dir:
        return
    if output_dir.exists():
        shutil.rmtree(output_dir)
    if source.is_dir():
        shutil.copytree(source, output_dir)
    else:
        output_dir.mkdir(parents=True)


def launch(
    adapter, req: RunRequest, install: Install, *, evidence_dir: Path, source_name: str
) -> list[Path]:
    if len(req.run_ids) != 1:
        raise ValueError("Local DeepSeek benchmark launchers take one rollout per invocation")
    with tempfile.TemporaryDirectory(prefix="adapt-ds-input-") as td:
        root = Path(td)
        context.copy_source(adapter, req.task_ref, root)
        source = root / "source"
        source.mkdir(exist_ok=True)
        prompt = (
            adapter.task_prompt(req.task_ref) + "\n\n"
            f"Source material is in {source_name}/ under the current workspace; treat it as "
            "read-only. Write the requested deliverable files to output/ in this workspace. "
            "The final chat response does not substitute for a requested file."
        )
        tool_python = {
            "jb": config.JB_JUDGE_PYTHON,
            "lab": config.LAB_PYTHON,
        }.get(adapter.spec.name)
        if tool_python is not None:
            prompt += f"\nUse {tool_python} for Python document and analysis helpers."
        result = _substrate(adapter).run_workspace(
            install,
            run_dir=evidence_dir,
            run_id=req.run_ids[0],
            prompt=prompt,
            timeout=req.timeout,
            inputs={source_name: source},
            provenance=task_identity(adapter, req.task_ref),
            tool_python=tool_python,
        )
    return [result]


def artifacts_intact(adapter, *, evidence_dir: Path, output_dir: Path) -> bool:
    if not _substrate(adapter).artifacts_intact(evidence_dir):
        return False
    mark = RM.read_mark(evidence_dir)
    if not mark:
        return False
    try:
        meta = json.loads((evidence_dir / MANIFEST).read_text(encoding="utf-8"))
        provenance = meta["identity"]["provenance"]
        if provenance != task_identity(adapter, provenance["task_id"]):
            return False
        if mark["harness"] == "base" and mark["source_harness"] != RM.fingerprint(
            adapter.base_harness()
        ):
            return False
        return mark["output_fingerprint"] == RM.fingerprint(
            _artifact_root(evidence_dir, output_dir)
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def run_metrics(evidence_dir: Path) -> dict:
    return json.loads((evidence_dir / "metrics.json").read_text(encoding="utf-8"))
