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

"""WorkBuddy-Bench adapter: one (subset, substrate) cell per `WbAdapter` instance.

Execution is a Harbor batch, not a single run: one label is one rollout-store namespace
and one batch is one generated job YAML with n_attempts=k. Everything a model sees of a
deliverable comes through wb_render.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.selection import order_graded_first
from ..core.spec import BenchPrompts, BenchSpec, resolve_channel_model
from ..substrate import opencode as OC
from ..substrate.base import Install, RunRequest
from ..substrate.codex import CODEX_SURFACE, CodexSubstrate
from ..substrate.opencode import OPENCODE_SURFACE, OpencodeSubstrate
from . import wb_render as RENDER
from .tasks import discover, select

SCOREABLE = frozenset({"code", "web", "office"})
SUBSTRATES = ("oc", "cx")

_BASE_LOCK = threading.Lock()
_BASE_DONE: set[Path] = set()

TRAJ_MAX_CHARS = 2_000_000


def _harness_oc(fork: bool) -> str:
    return f"{'oc-harness' if fork else 'opencode'}/{config.WB_OC_HARNESS_VER}"


def _harness_cx() -> str:
    return f"codex-harness/{config.WB_CX_HARNESS_VER}"


@dataclass(frozen=True)
class Subset:
    """One WB subset: its dataset, domain unit, deliverable and scoring."""

    name: str
    dataset_id: str
    domain_field: str
    deliverable: str
    scoring: str
    leak_globs: tuple[str, ...]
    llm_judge_model: str = ""

    @property
    def tasks_dir(self) -> Path:
        return config.wb_repo() / "datasets" / self.dataset_id / "tasks"


SUBSETS: dict[str, Subset] = {
    "code": Subset(
        name="code",
        dataset_id="wb-bench-code-v1.0",
        domain_field="role",
        deliverable="a diff against a real repository",
        scoring="rule",
        leak_globs=("tests/**", "solution/**", "environment/**"),
    ),
    "web": Subset(
        name="web",
        dataset_id="wb-bench-web-v1.0",
        domain_field="category",
        deliverable="a working web page or app",
        scoring="llm_judge",
        leak_globs=("tests/**", "environment/**"),
        llm_judge_model=config.WB_WEB_JUDGE,
    ),
    "office": Subset(
        name="office",
        dataset_id="wb-bench-office-v1.0",
        domain_field="category",
        deliverable="office documents (spreadsheets, docs) written to disk",
        scoring="rubric",
        leak_globs=("tests/**", "environment/**"),
    ),
}


def label_sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", s)


def _task_universe(subset: str) -> tuple[str, ...]:
    return tuple(discover("wb", subset))


_HARBOR_NAME_MAX = 32


@lru_cache(maxsize=8)
def _exact_index(subset: str) -> dict[str, str]:
    idx: dict[str, str] = {}
    for tid in _task_universe(subset):
        idx.setdefault(tid, tid)
        idx.setdefault(tid[:_HARBOR_NAME_MAX], tid)
    return idx


@lru_cache(maxsize=4096)
def resolve_task(name: str, subset: str) -> str | None:
    hit = _exact_index(subset).get(name)
    if hit is not None:
        return hit
    cands = [t for t in _task_universe(subset) if t.startswith(name)]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        raise RuntimeError(
            f"trial name {name!r} is ambiguous across {len(cands)} tasks: {cands[:3]}"
        )
    return None


def _join(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _norm(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (value or "").lower())).strip("_")


def _lookup(table: dict[str, str], value: str) -> str:
    if value in table:
        return table[value]
    key = _norm(value)
    for name, text in table.items():
        if _norm(name) == key:
            return text
    return ""


WORKBUDDY_BASES: dict[str, BenchPrompts] = {
    "code": BenchPrompts(
        work=(
            "Software maintenance in a real repository from a concise user request. The work "
            "product is a focused repository change, not an explanatory document. Surrounding "
            "code, public interfaces and the repository's own test suite are the available "
            "specification; preserve compatible behavior outside the requested change."
        ),
        domain_word="role",
        deliverable_word="repository change",
        inv_levers=(
            "- translate terse instructions, including non-English requests, into observable "
            "behavior before editing\n"
            "- reproduce or localise the behavior, read neighboring code and tests, then make the "
            "smallest idiomatic change\n"
            "- run the narrowest relevant repository tests after the edit and a broader compatible "
            "check when affordable\n"
            "- inspect the final diff for accidental files, API drift, generated noise and "
            "unfinished diagnostics"
        ),
        dom_levers=(
            "- encode reusable mechanics for the role or change family: tracing control flow, "
            "preserving error contracts, testing data transformations, reviewing configuration or "
            "validating an API\n"
            "- learn conventions from the repository in front of the agent rather than asserting a "
            "library API from memory\n"
            "- preserve public names, error shapes, ordering, determinism and edge-case behavior "
            "implied by adjacent code"
        ),
        yardstick=(
            "Did it understand the requested observable behavior, locate the responsible code, "
            "reproduce or test the issue, keep the change scoped and idiomatic, run meaningful "
            "checks and review the diff?"
        ),
        selection_checks=(
            "Prefer the change that directly satisfies the request, follows local design and "
            "style, preserves unrelated public behavior and error contracts, includes or updates "
            "appropriate tests when the request warrants them, avoids broad rewrites, and contains "
            "no generated or unfinished debris."
        ),
    ),
    "web": BenchPrompts(
        work=(
            "Building or repairing a self-contained web artifact from supplied assets and "
            "requirements. The delivered package must load in the stated environment and "
            "communicate through its rendered, interactive result; source code that never builds "
            "or renders is unfinished work."
        ),
        domain_word="web-work category",
        deliverable_word="web artifact",
        inv_levers=(
            "- inventory starter code, public assets, runtime and exact output paths before "
            "choosing a stack\n"
            "- establish a runnable skeleton early, then inspect the rendered result at target "
            "viewport sizes\n"
            "- check build/load errors, console output, missing assets and each named interaction; "
            "keep delivery self-contained\n"
            "- verify keyboard, focus, state transitions and responsive behavior where the request "
            "makes them relevant"
        ),
        dom_levers=(
            "- encode mechanics for the component family: data visualisation, durable UI state, "
            "accessible controls, document conversion, analytical reporting or frontend tests\n"
            "- treat labels, axes, units, visual hierarchy, semantics and interaction feedback as "
            "functional information\n"
            "- derive content and data from supplied assets; never substitute plausible sample "
            "content"
        ),
        yardstick=(
            "Did it deliver at the exact runnable path, load without errors or missing assets, "
            "render the supplied content legibly, implement every named interaction and inspect "
            "the result rather than source alone?"
        ),
        selection_lead=(
            "DOES IT WORK. Judge the artifact as something that RUNS, before anything about its "
            "shape or tidiness. Where a candidate directory holds rendered evidence, OPEN IT "
            "FIRST -- it shows what the artifact actually produced. Then read the source for what "
            "the images cannot show: does every named interaction have a handler that is actually "
            "wired up, is state re-rendered after it changes, are events bound to elements that "
            "exist, do referenced assets and paths resolve, would anything here throw on load? A "
            "candidate that renders and behaves correctly beats a tidier one that does not, and a "
            "broken interaction outweighs any number of cosmetic or inventory differences."
        ),
        selection_checks=(
            "Prefer the artifact that runs directly from the required path; renders without blank "
            "states, broken assets or console failures; preserves supplied data and meaning; "
            "implements every requested state and interaction; remains readable and usable across "
            "relevant viewports and input methods; and feels complete rather than like a static "
            "mock-up."
        ),
    ),
    "office": BenchPrompts(
        work=(
            "Office and workspace operations over supplied files: extracting, reconciling, "
            "transforming or documenting data and producing exact spreadsheets, documents, JSON, "
            "reports or other requested files. Execution boundaries in the request—such as "
            "read-only or offline work—are part of the job."
        ),
        domain_word="office-work category",
        deliverable_word="workspace artifact",
        inv_levers=(
            "- inventory every supplied source and restriction, establish the authoritative copy "
            "and record deduplication or filtering rules before calculating\n"
            "- create exact requested files, sheet names, columns and schemas early; use code for "
            "repeatable transformation and arithmetic when allowed\n"
            "- preserve source files, formulas, encodings and read-only boundaries; do not invent "
            "an external call the task forbids\n"
            "- reopen outputs with an independent reader and verify formulas, types, non-empty "
            "values, totals and cross-format consistency"
        ),
        dom_levers=(
            "- encode reusable mechanics for document extraction, evidence-backed repository "
            "explanation, data cleaning, reconciliation, aggregation and office-file construction\n"
            "- make source locations and transformation lineage reviewable\n"
            "- preserve exact field order, naming, units, date rules and null semantics requested "
            "by the user"
        ),
        yardstick=(
            "Did it obey the workspace boundary, use every material source, apply cleaning and "
            "calculation rules reproducibly, write the exact artifacts and validate their contents "
            "and structure after writing?"
        ),
        selection_checks=(
            "Prefer the artifacts that obey read/write and network boundaries; contain every "
            "required file, sheet, column and section; preserve source meaning and formulas; apply "
            "stated filters, joins and calculations correctly; expose missing or conflicting data; "
            "and reopen successfully in their real format."
        ),
    ),
}


WORKBUDDY_DOMAINS: dict[str, dict[str, str]] = {
    "code": {
        "developer": (
            "ROLE LENS — DEVELOPER. Preserve public interfaces and local architecture; trace "
            "callers and tests before changing implementation; prefer a small maintainable patch "
            "over a parallel abstraction."
        ),
        "algo": (
            "ROLE LENS — ALGORITHM / DATA. State invariants, ordering and complexity; test "
            "boundary cases, determinism, leakage and numeric behavior; use fixtures that expose "
            "the transformation rather than examples that merely pass."
        ),
        "pm": (
            "ROLE LENS — PRODUCT. Turn the request into explicit user-visible behavior and stable "
            "data contracts; preserve export schemas, policy precedence and edge-case handling; "
            "validate usefulness with representative inputs."
        ),
        "ops": (
            "ROLE LENS — OPERATIONS. Prioritise failure recovery, portability, cleanup, resource "
            "ownership, configuration precedence and observable diagnostics; test the failure path "
            "as well as the happy path."
        ),
        "qa": (
            "ROLE LENS — QUALITY. Reproduce the defect, isolate the smallest behavior contract, "
            "add regression coverage against the real module rather than reimplementing it, and "
            "verify that error messages and negative cases remain stable."
        ),
    },
    "web": {
        "data-visualization": (
            "CATEGORY LENS — DATA VISUALISATION. Preserve data values and definitions; verify "
            "scales, units, axes, legends, tooltips and empty states; keep comparisons readable "
            "and accessible without relying on color alone."
        ),
        "page-interaction": (
            "CATEGORY LENS — PAGE INTERACTION. Model state transitions explicitly, including "
            "refresh or persistence rules, validation, disabled and error states, keyboard "
            "operation and feedback after every action."
        ),
        "analytical-report": (
            "CATEGORY LENS — ANALYTICAL REPORT. Build one traceable evidence chain from supplied "
            "logs, metrics or feedback to a narrow finding, impact, remediation, validation and "
            "release recommendation; do not produce a generic dashboard."
        ),
        "page-implementation": (
            "CATEGORY LENS — PAGE IMPLEMENTATION. Honour the required runtime and packaging path, "
            "build before delivery, remove external dependency surprises and test the page from a "
            "clean static-server start."
        ),
        "visual-design": (
            "CATEGORY LENS — VISUAL DESIGN. Use coherent tokens, hierarchy, spacing, typography, "
            "contrast and responsive composition; preserve visible focus and semantic states, and "
            "make variants complete systems rather than color swaps."
        ),
        "document-conversion": (
            "CATEGORY LENS — DOCUMENT CONVERSION. Preserve source structure and meaning—headings, "
            "tables, code, links and media notes—without summarising; provide a concrete "
            "transformation ledger for non-trivial choices."
        ),
        "code-testing": (
            "CATEGORY LENS — FRONTEND TESTING. Import and exercise the real component or a thin "
            "adapter, cover named positive and negative cases with meaningful fixtures, keep the "
            "package runnable offline and prove the suite executes."
        ),
    },
    "office": {
        "doc-ops": (
            "CATEGORY LENS — DOCUMENT OPERATIONS. Extract exact fields with source locations, "
            "preserve canonical names and schema order, separate missing from zero or "
            "not-applicable, and keep generated documents readable as well as machine-usable."
        ),
        "automation-workdir": (
            "CATEGORY LENS — WORKSPACE EXPLORATION. Respect read-only and offline boundaries, "
            "examine source, docs, configuration, schema, examples and outputs together, and "
            "support every interface or behavior claim with a workspace path."
        ),
        "data-file-ops": (
            "CATEGORY LENS — DATA FILE OPERATIONS. Define deduplication, null filtering, join "
            "keys, periods, denominators and aggregation rules first; reconcile control totals and "
            "deliver populated workbooks with exact requested sheets."
        ),
    },
}


def workbuddy_prompts(subset: str, *, domain: str = "") -> BenchPrompts:
    """WorkBuddy subset profile plus its role/category overlay."""
    key = _norm(subset)
    if key not in WORKBUDDY_BASES:
        raise ValueError(f"unknown WorkBuddy subset {subset!r}; expected code, web or office")
    guidance = _lookup(WORKBUDDY_DOMAINS[key], domain)
    base = WORKBUDDY_BASES[key]
    return replace(base, domain_guidance=_join(base.domain_guidance, guidance))


def _unit_float(v) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if 0.0 <= n <= 1.0 else None


def _int_or_none(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def score_from_payload(payload: dict) -> float | None:
    """Mirror of the bench scorer's precedence: reward > overall > test_pass_rate >
    tests_passed/tests_total. Values outside [0,1] are rejected, not clamped."""
    for key in ("reward", "overall", "test_pass_rate"):
        sc = _unit_float(payload.get(key))
        if sc is not None:
            return sc
    passed = _int_or_none(payload.get("tests_passed"))
    total = _int_or_none(payload.get("tests_total"))
    if passed is not None and total and total > 0:
        return max(0.0, min(passed / total, 1.0))
    return None


def _trial_finished(trial: Path) -> bool:
    """True when the trial reached a terminal state (verifier/score.json exists); only
    `score()` may read the VALUE in that file."""
    return (Path(trial) / "verifier" / "score.json").is_file()


def _json_or_none(p: Path) -> dict | None:
    """Parse `p` as a JSON object; None when it is missing, torn, or not an object."""
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return got if isinstance(got, dict) else None


def _trial_graded(trial: Path) -> bool:
    """Ranking predicate only: a malformed score.json ranks last, never drops out."""
    payload = _json_or_none(Path(trial) / "verifier" / "score.json")
    return payload is not None and score_from_payload(payload) is not None


EPHEMERAL_PREFIX = "_ab-"

MAX_JOBS = dict(config.WB_MAX_JOBS)
MAX_TRIALS = dict(config.WB_MAX_TRIALS)
_slots = {s: threading.Semaphore(MAX_JOBS[s]) for s in SUBSTRATES}
_label_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

_TRIAL_RE = re.compile(r"^(?P<task>.+)__[A-Za-z0-9]+$")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] wb | {msg}", flush=True)


def _kill_group(proc: "subprocess.Popen", sig: int, *, grace: int) -> None:
    """Signal the whole process group, wait out `grace`, then SIGKILL what is left."""
    for attempt in (sig, signal.SIGKILL):
        try:
            os.killpg(proc.pid, attempt)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.communicate(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue
    proc.wait()


def _label_lock(label: str) -> threading.Lock:
    with _locks_guard:
        return _label_locks.setdefault(label, threading.Lock())


def _conc(substrate: str) -> int:
    return max(1, MAX_TRIALS[substrate] // MAX_JOBS[substrate])


def sweep_orphans(max_age_s: int = 86400) -> int:
    """Delete ephemeral job YAMLs left by a killed run; scoped to our own prefix."""
    jobs_dir = config.wb_repo() / "configs" / "jobs"
    now, n = time.time(), 0
    for f in jobs_dir.glob(f"{EPHEMERAL_PREFIX}*.yaml"):
        if now - f.stat().st_mtime > max_age_s:
            f.unlink(missing_ok=True)
            n += 1
    return n


def _job_yaml(
    subset: Subset,
    substrate: str,
    label: str,
    tasks: list[str],
    ws: Path | None,
    k: int,
    jobs_dir_abs: Path,
) -> str:
    sel = "\n".join(f"    - {t}" for t in tasks)
    if substrate == "oc":
        harness = _harness_oc(fork=ws is not None)
        hp: dict = {"OPENCODE_VARIANT": "high"}
        if ws is not None:
            hp["WB_HARNESS_WS"] = str(ws.resolve())
            composed = ws / "_composed" / "prompt.j2"
            if composed.is_file():
                hp["prompt_template_path"] = str(composed.resolve())
        hp_lines = "\n".join(f"  {key}: {json.dumps(val)}" for key, val in sorted(hp.items()))
        extra = f"harness_params_override:\n{hp_lines}\n"
    else:
        harness = _harness_cx()
        hp = {"CODEX_MODEL": config.SOLVER_MODEL_CX}
        if ws is not None:
            hp["WB_HARNESS_WS"] = str(ws.resolve())
        hp_block = ""
        if hp:
            hp_lines = "\n".join(f"  {key}: {json.dumps(val)}" for key, val in sorted(hp.items()))
            hp_block = f"harness_params_override:\n{hp_lines}\n"
        extra = (
            "extra_allowed_hosts:\n"
            "  - host.docker.internal\n"
            "environment_override:\n"
            "  mounts:\n"
            "    - type: bind\n"
            f"      source: {config.CODEX_DIST}\n"
            "      target: /opt/codex\n"
            "      read_only: true\n"
            "env_override:\n"
            f"  VERTEX_PROXY_KEY: {json.dumps(config.VERTEX_PROXY_KEY)}\n"
            f"  VERTEXAI_LOCATION: {config.VERTEX_REGION}\n"
            f"  CODEX_BRIDGE_URL: {json.dumps(config.CODEX_CONTAINER_URL)}\n"
            f"{hp_block}"
        )
    judge = (
        f"llm_judge_override:\n  model: {subset.llm_judge_model}\n"
        if subset.llm_judge_model
        else ""
    )
    head = (
        f"# GENERATED by harness2/benchmarks/workbuddy.py -- ephemeral, safe to "
        f"delete. label={label}\n"
    )
    return (
        head
        + f"""model: {config.WB_SOLVER_MODEL}
harness: {harness}
dataset: datasets/{subset.dataset_id}/tasks
harness_backend: local
model_connection: {config.WB_MODEL_CONNECTION}
n_attempts: {k}
jobs_dir: {jobs_dir_abs}
task_selection:
  mode: name
  names:
{sel}
orchestrator_override:
  n_concurrent_trials: {_conc(substrate)}
{extra}{judge}"""
    )


def _run_dirs(label_dir: Path) -> list[Path]:
    if not label_dir.is_dir():
        return []
    return sorted(d for d in label_dir.iterdir() if d.is_dir())


def _trials_in(run_dirs: list[Path], task_id: str, subset: str) -> list[Path]:
    out: list[Path] = []
    for run in run_dirs:
        for d in sorted(run.iterdir()):
            m = _TRIAL_RE.match(d.name)
            if d.is_dir() and m and resolve_task(m.group("task"), subset) == task_id:
                out.append(d)
    return out


def run_batch(
    subset: Subset,
    substrate: str,
    label_dir: Path,
    label: str,
    tasks: list[str],
    ws: Path | None,
    k: int,
    *,
    done,
    need=None,
    timeout_s: int | None = None,
) -> list[Path]:
    """Roll `tasks` k times each under harness workspace `ws`, returning the run dirs THIS
    invocation created. `done(task_id)` is the caller's skip predicate; `need(task_id)` is
    the optional shortfall, so a partial resume asks for k-j attempts rather than k."""
    with _label_lock(label):
        todo = [t for t in tasks if not done(t)]
        if not todo:
            _log(f"{label}: all {len(tasks)} task(s) already complete -- skipping")
            return []
        if len(todo) < len(tasks):
            _log(f"{label}: resuming, {len(tasks) - len(todo)} of {len(tasks)} already done")
        if need is not None:
            short = max(1, max(int(need(t)) for t in todo))
            if short != k:
                _log(f"{label}: partial resume, asking for {short} attempt(s) not {k}")
            k = short

        before = set(_run_dirs(label_dir))
        digest = hashlib.sha1(f"{label}|{subset.name}|{sorted(todo)}".encode()).hexdigest()[:8]
        slug = f"{EPHEMERAL_PREFIX}{label_sanitize(label)[:40]}-{digest}"
        yaml_path = config.wb_repo() / "configs" / "jobs" / f"{slug}.yaml"
        label_dir.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(
            _job_yaml(subset, substrate, label, todo, ws, k, label_dir), encoding="utf-8"
        )

        timeout_s = timeout_s or int((len(todo) * k / _conc(substrate)) * 3600 * 1.2 + 900)
        env = {
            **os.environ,
            "NO_FORCE_BUILD": "1",
            "HARNESS2_WB_VERTEX_URL": config.VERTEX_OPENAI_URL + "/chat/completions",
        }
        try:
            with _slots[substrate]:
                _log(f"{label}: {len(todo)} task(s) x k={k} [{substrate}]")
                proc = subprocess.Popen(
                    ["uv", "run", "./scripts/run.sh", "--job", slug],
                    cwd=config.wb_repo(),
                    env=env,
                    start_new_session=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    _, errtxt = proc.communicate(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    _log(f"{label}: TIMEOUT after {timeout_s}s -- stopping the job group")
                    _kill_group(proc, signal.SIGTERM, grace=180)
                    _, errtxt = "", "[timeout: job group killed]"
                if proc.returncode not in (0, None):
                    _log(
                        f"{label}: run.sh exit {proc.returncode} "
                        f"(partial results are still usable) :: {(errtxt or '')[-300:]}"
                    )
        finally:
            yaml_path.unlink(missing_ok=True)
        return sorted(set(_run_dirs(label_dir)) - before)


def spec_for(subset: str, substrate: str) -> BenchSpec:
    s = SUBSETS[subset]
    channel = resolve_channel_model(None, None, effort="high")
    return BenchSpec(
        name=f"wb-{subset}",
        runs_root=config.RUNS / "wb" / subset,
        install_mode="home_dir_jinja" if substrate == "oc" else "codex_root",
        prompts=workbuddy_prompts(subset),
        channels={"proposer": channel, "selector": channel},
        domain_word="category",
        leak_globs=s.leak_globs,
        allow_subagents=(substrate == "oc"),
        obs_trunc=50_000,
        proposer_deliverables=("names" if subset == "code" else "contents"),
        state_deliverable=True,
    )


class WbAdapter:
    """One (subset, substrate) cell of WB-Bench. A different cell is a different instance."""

    def __init__(
        self, subset: str = "office", *, substrate: str = "oc", split_file: Path | None = None
    ):
        self.split_file = split_file
        if subset not in SUBSETS:
            raise ValueError(f"unknown subset {subset!r}; one of {sorted(SUBSETS)}")
        if substrate not in SUBSTRATES:
            raise ValueError(f"unknown substrate {substrate!r}; one of {SUBSTRATES}")
        self.subset = subset
        self.subset_info = SUBSETS[subset]
        self.spec = spec_for(subset, substrate)
        self.substrate = (
            OpencodeSubstrate(launcher=self._launch)
            if substrate == "oc"
            else CodexSubstrate(launcher=self._launch)
        )

    def tasks(self, split: str = "all") -> list[str]:
        return select(
            discover("wb", self.subset), split, self.split_file, benchmark=f"wb-{self.subset}"
        )

    def domain_of(self, task_id: str) -> str:
        return discover("wb", self.subset)[task_id]

    def reference_domain_order(self) -> list[str]:
        return sorted(set(discover("wb", self.subset).values()))

    def prompts_for(self, task_id: str) -> BenchPrompts:
        return workbuddy_prompts(self.subset, domain=self.domain_of(task_id))

    def task_prompt(self, task_id: str) -> str:
        """instruction.md verbatim; `leak_globs` covers the answer-key paths."""
        return (self.subset_info.tasks_dir / task_id / "instruction.md").read_text(
            encoding="utf-8", errors="replace"
        )

    def task_files(self, task_id: str) -> Path | None:
        """The task directory; spec.leak_globs strips the answer key before any copy."""
        d = self.subset_info.tasks_dir / task_id
        return d if d.is_dir() else None

    def _label(self, run_id: str, n: int = 1) -> str:
        label = run_id.replace("/", "__") + (f"__x{n}" if n > 1 else "")
        prefix = f"{self.spec.name}-{self.substrate.name}-"
        if not label.startswith(prefix):
            raise ValueError(
                f"run id {run_id!r} does not belong to this cell (expected a namespace "
                f"starting {prefix!r}); labels are constructed by core.schedule.ns only"
            )
        return label

    def _label_dir(self, label: str) -> Path:
        return self.spec.runs_root / "rollouts" / label_sanitize(label)

    def _trials(self, label: str, task_id: str) -> list[Path]:
        """Every trial of `task_id` under this label, merged across timestamped run dirs."""
        return _trials_in(_run_dirs(self._label_dir(label)), task_id, self.subset)

    def _rank(self, trials: list[Path]) -> list[Path]:
        """Newest-first, graded preferred but never filtered out."""
        newest = sorted(trials, key=lambda q: q.stat().st_mtime, reverse=True)
        return order_graded_first(newest, _trial_graded)

    def _cell_identity(self) -> dict:
        """What makes two rollouts of one label the same rollout, beyond the harness."""
        return {
            "solver": config.SOLVER_MODEL_OC
            if self.substrate.name == "oc"
            else config.SOLVER_MODEL_CX,
            "oc_ver": config.WB_OC_HARNESS_VER,
            "connection": config.WB_MODEL_CONNECTION,
        }

    def _identity_matches(self, trial: Path) -> bool:
        """True when `trial`'s marker names this cell's executor identity."""
        m = RM.read_mark(Path(trial)) or {}
        return all(m.get(key) == val for key, val in self._cell_identity().items())

    def _reusable(self, trial: Path, harness_dir: Path | None) -> bool:
        return (
            _trial_finished(trial)
            and RM.is_complete(trial, harness_dir=harness_dir)
            and self._identity_matches(trial)
        )

    def _install(self, harness_dir: Path | None) -> Install:
        if harness_dir is None:
            return Install(
                staging_dir=self.spec.runs_root, fingerprint="base", extras={"install_mode": "none"}
            )
        return self.substrate.materialise(Path(harness_dir), self.spec, {})

    @staticmethod
    def _drop_staging(install: Install) -> None:
        if install.fingerprint != "base":
            shutil.rmtree(install.staging_dir, ignore_errors=True)

    def _launch(self, req: RunRequest, install: Install) -> list[Path]:
        """The benchmark-supplied launcher body: the finished trials among the run dirs
        this invocation created, ranked; the adapter trims and marks them."""
        ws = None if install.fingerprint == "base" else install.staging_dir
        k = len(req.run_ids)
        label = self._label(req.run_ids[0], k)
        if self.substrate.name == "cx":
            self.substrate.preflight(require_dist=True, require_bridge=True)

        def _have(task_id: str) -> int:
            return sum(
                1
                for t in self._trials(label, task_id)
                if _trial_finished(t)
                and _mark_matches(t, install.fingerprint)
                and self._identity_matches(t)
            )

        new_dirs = run_batch(
            self.subset_info,
            self.substrate.name,
            self._label_dir(label),
            label,
            [req.task_ref],
            ws,
            k,
            done=lambda tid: _have(tid) >= k,
            need=lambda tid: k - _have(tid),
            timeout_s=req.timeout or None,
        )
        fresh = [t for t in _trials_in(new_dirs, req.task_ref, self.subset) if _trial_finished(t)]
        return self._rank(fresh)

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        """One rollout = a one-task Harbor batch, idempotent by run id and harness id."""
        label = self._label(run_id)
        usable = [t for t in self._trials(label, task_id) if self._reusable(t, harness_dir)]
        if usable:
            return max(usable, key=lambda q: q.stat().st_mtime)
        install = self._install(harness_dir)
        try:
            fresh = self.substrate.execute(task_id, install, [run_id], int(timeout or 0))
        finally:
            self._drop_staging(install)
        if not fresh:
            raise RuntimeError(f"Harbor produced no finished trial for {label} / {task_id}")
        chosen = fresh[0]
        RM.mark_complete(
            chosen, run_id=run_id, harness_dir=harness_dir, extra=self._cell_identity()
        )
        return chosen

    def execute_many(
        self,
        task_id: str,
        harness_dir: Path | None,
        run_ids: list[str],
        *,
        timeout: int | None = None,
    ) -> list[Path]:
        """k replicates in ONE batch. Only the trials this invocation produced are marked,
        so a killed batch's leftovers cannot be laundered into the new identity."""
        if not run_ids:
            return []
        k = len(run_ids)
        label = self._label(run_ids[0], k)

        def _mine() -> list[Path]:
            return [t for t in self._trials(label, task_id) if self._reusable(t, harness_dir)]

        mine = _mine()
        if len(mine) < k:
            install = self._install(harness_dir)
            try:
                fresh = self.substrate.execute(task_id, install, list(run_ids), int(timeout or 0))
            finally:
                self._drop_staging(install)
            for t in fresh:
                if not RM.read_mark(t):
                    RM.mark_complete(
                        t, run_id=run_ids[0], harness_dir=harness_dir, extra=self._cell_identity()
                    )
            mine = _mine()
        return self._rank(mine)[:k]

    def delivered(self, run_dir: Path) -> bool:
        """Did this rollout produce an artifact? Counted over artifact BODIES only."""
        return RENDER.body_chars(Path(run_dir), self.subset) >= 200

    def trajectory_path(self, run_dir: Path) -> Path:
        stream = "oc-output.txt" if self.substrate.name == "oc" else "codex-output.jsonl"
        return Path(run_dir) / "agent" / stream

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        """The capture stream in the shared [ACTION]/[OBSERVATION] grammar."""
        f = self.trajectory_path(run_dir)
        if not f.is_file():
            return "(no trajectory)"
        return self.substrate.read_trajectory(f, obs_trunc=obs_trunc, max_chars=TRAJ_MAX_CHARS)

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        """filename -> our rendering of the agent's own bytes, one entry per artifact."""
        return RENDER.render(Path(run_dir), self.subset)

    def deliverable_listing(self, run_dir: Path) -> list[str]:
        """The deliverable NAMES only, from agent-authored sources."""
        return RENDER.listing(Path(run_dir), self.subset)

    def deliverable_text(self, run_dir: Path) -> str:
        """The rendered artifact as one document: the selector's view, untrimmed."""
        return RENDER.flat_text(Path(run_dir), self.subset)

    def deliverable_assets(self, run_dir: Path) -> list[Path]:
        if self.subset == "office":
            return [path for _name, path in RENDER.raw_files(Path(run_dir), self.subset)]

        return []

    def base_harness(self) -> Path:
        """H0, the starting harness the improvement pass edits, materialised once per
        process. It is always the stock surface written below."""
        d = self.spec.runs_root / f"_base_harness_{self.substrate.name}_{os.getpid()}"
        with _BASE_LOCK:
            if d not in _BASE_DONE:
                if self.substrate.name == "oc":
                    HT.write_tree(
                        OPENCODE_SURFACE,
                        {
                            "systemprompt.md": "",
                            "guardrails.md": "",
                            "LongTermMEMORY.md": "",
                            "tool_descriptions/tool_guidance.md": "",
                        },
                        d,
                        stubs=True,
                    )
                else:
                    HT.write_tree(
                        CODEX_SURFACE,
                        {
                            "AGENTS.md": (
                                "Follow the task instructions and complete the requested work in the "
                                "workspace, writing every deliverable where the instructions say."
                            )
                        },
                        d,
                        stubs=True,
                    )
                _BASE_DONE.add(d)
        return d

    def validate_tree(self, tree: dict[str, str]) -> None:
        """oc: check the Jinja invariant on the COMPOSED template, so a brace in a skill's
        `description:` is a rejected proposal rather than a dead trial. cx: nothing."""
        if self.substrate.name != "oc":
            return
        with tempfile.TemporaryDirectory() as td:
            staged = HT.write_tree(OPENCODE_SURFACE, tree, Path(td) / "h")
            OC.assert_jinja_safe(OC.compose_prompt(staged))

    def preflight(self) -> None:
        """Check the benchmark checkout and executor before starting containers."""
        if shutil.which("uv") is None:
            raise RuntimeError(
                "`uv` is not on PATH; WB batches run as `uv run ./scripts/run.sh` inside "
                "the benchmark repo (see docs/BENCHMARKS.md)"
            )
        repo = config.wb_repo()
        if not (repo / "scripts" / "run.sh").is_file():
            raise RuntimeError(
                f"the WorkBuddy-Bench checkout at {repo} has no scripts/run.sh — run "
                f"scripts/setup_benchmark.py wb"
            )
        slugs = (
            [_harness_oc(fork=False), _harness_oc(fork=True)]
            if self.substrate.name == "oc"
            else [_harness_cx()]
        )
        for slug in slugs:
            family, _, ver = slug.partition("/")
            entry = repo / "configs" / "harnesses" / family / "versions" / f"{ver}.yaml"
            if not entry.is_file():
                raise RuntimeError(
                    f"harness entry {slug!r} is missing at {entry} — the harness configs "
                    f"ship as third_party/wb_runner/configs/harnesses/; run "
                    f"python scripts/setup_benchmark.py wb"
                )
        if self.substrate.name == "cx":
            self.substrate.preflight(require_dist=True, require_bridge=True)
        else:
            self.substrate.preflight()

    def _killed(self, run_dir: Path) -> bool:
        """True when the capture stream ends on a provider error object, i.e. the rollout was
        cut short rather than finished."""
        try:
            raw = self.trajectory_path(run_dir).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        for ln in reversed(raw.splitlines()):
            ln = ln.strip()
            if not ln.startswith("{"):
                continue
            try:
                ev = json.loads(ln)
            except json.JSONDecodeError:
                return False
            if not isinstance(ev, dict):
                return False
            return ev.get("type") == "error" or isinstance(ev.get("error"), dict)
        return False

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        """Read the verifier's own score.json; missing or unparseable is a JUDGE failure
        and returns None, never 0. A rollout the provider cut short before any artifact was
        written is the same failure, so its verifier zero is not reported as a score."""
        run_dir = Path(run_dir)
        if not self.delivered(run_dir) and self._killed(run_dir):
            return None
        payload = _json_or_none(run_dir / "verifier" / "score.json")
        if payload is None:
            return None
        sc = score_from_payload(payload)
        if sc is None:
            return None
        return {
            "n_passed": round(sc * 100),
            "n_criteria": 100,
            "all_pass": sc >= 1.0,
            "raw_score": sc,
        }


def _mark_matches(trial: Path, fingerprint: str) -> bool:
    """runmark's is_complete against a known fingerprint string."""
    m = RM.read_mark(Path(trial))
    return bool(m) and m.get("version") == RM.VERSION and m.get("harness") == fingerprint


__all__ = [
    "WbAdapter",
    "Subset",
    "SUBSETS",
    "SCOREABLE",
    "SUBSTRATES",
    "spec_for",
    "workbuddy_prompts",
    "WORKBUDDY_BASES",
    "WORKBUDDY_DOMAINS",
    "resolve_task",
    "score_from_payload",
    "run_batch",
    "sweep_orphans",
    "label_sanitize",
    "TRAJ_MAX_CHARS",
]
