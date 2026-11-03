"""LAB's existing adapter with native Codex rollouts and CLI-based judging.

The reference runner still owns document installation, prompts and trace metrics.
Only its process/auth setup is replaced; core recursion and scoring are unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .. import config
from ..subscription import check_auth, environment, invoke
from .lab import LabAdapter, run_detached


class SubscriptionLabAdapter(LabAdapter):
    def __init__(self, **kwargs):
        super().__init__(substrate="cx", judge_model=config.JUDGE_MODEL, **kwargs)
        sys.path.insert(0, str(config.lab_repo()))
        path = config.lab_repo() / "external/adapt_runs_cx/run_codex_lab.py"
        if not path.is_file():
            self.runner = None
            return
        spec = importlib.util.spec_from_file_location("_lab_reference_runner", path)
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)

    def preflight(self) -> None:
        for channel in {"codex_cli", config.DEFAULT_PROPOSER_CHANNEL, config.JUDGE_CHANNEL}:
            check_auth(channel)
        if self.runner is None or not config.LAB_PYTHON.is_file():
            raise RuntimeError("Install LAB first: harness2-setup lab --substrate cx")
        revision = subprocess.check_output(
            ["git", "-C", str(config.lab_repo()), "rev-parse", "HEAD"], text=True, timeout=30
        ).strip()
        if revision != config.DEPENDENCY_PINS["harvey-labs"]:
            raise RuntimeError("LAB checkout differs from the supported dependency pin")
        for source, installed in (
            ("adapt_runs_cx/run_codex_lab.py", "external/adapt_runs_cx/run_codex_lab.py"),
            ("tts_lab/config.py", "external/tts_lab/config.py"),
            ("opencode/scripts/read_doc.py", "opencode/scripts/read_doc.py"),
        ):
            if (config.THIRD_PARTY / "lab_runner" / source).read_bytes() != (
                config.lab_repo() / installed
            ).read_bytes():
                raise RuntimeError(f"LAB runtime helper changed: {installed}; rerun setup")
        for name in ("pandoc", "pdftoppm"):
            if not shutil.which(name):
                raise RuntimeError(f"Missing LAB document tool: {name}")
        subprocess.run(
            [str(config.LAB_PYTHON), "-c", "import evaluation.scoring"],
            cwd=config.lab_repo(),
            env=environment(),
            check=True,
            capture_output=True,
            timeout=60,
        )

    def _launch_cx(self, req, install) -> list[Path]:
        dirs = []
        for run_id in req.run_ids:
            out = self._run_dir(run_id)
            out.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="harness2-lab-") as td:
                ws = Path(td)
                (ws / "output").mkdir()
                docs = self.task_files(req.task_ref)
                if docs:
                    shutil.copytree(docs, ws / "documents")
                    for p in (ws / "documents").rglob("*"):
                        if p.is_file():
                            p.chmod(0o444)
                else:
                    (ws / "documents").mkdir()
                installed = self.runner.install_harness(ws, install.staging_dir)
                manifest = {
                    "transport": "subscription",
                    "model": self.model,
                    "task": req.task_ref,
                    "run_id": run_id,
                    "workspace": str(ws),
                    "harness_fingerprint": install.fingerprint,
                    "installed_files": {
                        name: hashlib.sha256((ws / name).read_bytes()).hexdigest()
                        for name in installed
                    },
                }
                (out / "config.json").write_text(json.dumps(manifest, indent=2) + "\n")
                result = invoke(
                    "codex_cli",
                    self.model,
                    self.runner.build_prompt(req.task_ref, ws)
                    + "\n\nUse $TMPDIR for scratch files; shared /tmp and other workspaces are inaccessible.",
                    workspace=ws,
                    audit=out / "transport",
                    effort=self.solver_effort,
                    timeout=req.timeout,
                    read_roots=(
                        config.LAB_PYTHON.parent.parent,
                        # uv's interpreter alias is a sibling symlink of the
                        # versioned install; Seatbelt must traverse both.
                        config.LAB_PYTHON.resolve().parent.parent.parent,
                        config.lab_repo() / "sandbox/parsers",
                    ),
                    extra_env={
                        "PATH": str(config.LAB_PYTHON.parent) + os.pathsep + os.environ["PATH"],
                        "LAB_DOC_PARSER": "host",
                        "LAB_BENCH_ROOT": str(config.lab_repo()),
                    },
                )
                shutil.copy2(out / "transport/stdout.jsonl", out / "codex_session.jsonl")
                shutil.copytree(ws / "output", out / "output", dirs_exist_ok=True)
                if not self.delivered(out):
                    raise RuntimeError(f"Codex completed without LAB deliverables: {out}")
                summary = self.runner.parse_session(out / "codex_session.jsonl")
                metrics = {
                    **summary,
                    "transport": "subscription",
                    "model": self.model,
                    "session_id": result["session_id"],
                    "finished_cleanly": True,
                    "exit_code": 0,
                    "wall_clock_seconds": result["wall_s"],
                    "harness_fingerprint": install.fingerprint,
                    "deliverables": sorted(self.deliverable_names(out)),
                }
                (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
            dirs.append(out)
        return dirs

    @staticmethod
    def deliverable_names(out: Path) -> list[str]:
        return [
            str(p.relative_to(out / "output")) for p in (out / "output").rglob("*") if p.is_file()
        ]

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        run_dir = Path(run_dir).resolve()
        if not self.artifacts_intact(run_dir):
            return None
        if not json.loads((run_dir / "metrics.json").read_text()).get("finished_cleanly"):
            return None
        identity = {
            "transport": "subscription",
            "channel": config.JUDGE_CHANNEL,
            "model": config.JUDGE_MODEL,
            "cli_version": check_auth(config.JUDGE_CHANNEL),
            "output": self._output_fingerprint(run_dir),
        }
        cache = run_dir / "subscription-judge.json"
        scores = run_dir / "scores.json"
        if not (cache.is_file() and json.loads(cache.read_text()) == identity and scores.is_file()):
            with (run_dir / "judge.log").open("w") as log:
                run_detached(
                    [
                        str(config.LAB_PYTHON),
                        "-m",
                        "harness2.subscription_judge",
                        str(run_dir),
                        task_id,
                        config.JUDGE_CHANNEL,
                        config.JUDGE_MODEL,
                    ],
                    cwd=config.lab_repo(),
                    env=environment(
                        {"PYTHONPATH": str(config.ROOT) + os.pathsep + str(config.lab_repo())}
                    ),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    timeout=3600,
                )
            cache.write_text(json.dumps(identity, indent=2) + "\n")
        data = json.loads(scores.read_text())
        return {k: data[k] for k in ("n_passed", "n_criteria", "all_pass")}
