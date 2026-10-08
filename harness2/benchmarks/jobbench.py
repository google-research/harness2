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

"""JobBench adapter: tasks, splits, prompts, launcher bodies, deliverables and scores.

Rollouts are addressed by LABEL, not by run directory: the run store is dataset-addressed
and shared by every setting, so the run_id becomes the label.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..core import runmark as RM
from ..core import tree as HT
from ..core.spec import BenchPrompts, BenchSpec, resolve_channel_model
from ..install import codex_config_error
from ..substrate.base import Install, RunRequest
from ..substrate.codex import CodexSubstrate
from ..substrate.opencode import OUTPUT_TOKEN_MAX, OpencodeSubstrate
from .tasks import discover, select

_RUNNER_DIR = config.THIRD_PARTY / "jb_runner"
_STORE_SEED_LOCK = threading.Lock()

OC_RUNNER = Path(__file__).resolve().parent / "jb_opencode_runner.py"
CX_RUNNER = _RUNNER_DIR / "run_benchmark_codex_cli_adapt.sh"
OC_BASE_HARNESS = _RUNNER_DIR / "harness_baseline_oc"
CX_BASE_HARNESS = _RUNNER_DIR / "codex_base_harness"


def run_detached(cmd: list[str], *, timeout: int, **kw) -> None:
    """Run `cmd` in its own process group and kill the group on a wall timeout."""
    proc = subprocess.Popen(cmd, start_new_session=True, **kw)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.wait()
        raise

    if proc.returncode != 0:
        raise RuntimeError(f"Benchmark runner exited with status {proc.returncode}")


def _join(*parts: str) -> str:
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _with_guidance(base: BenchPrompts, *guidance: str) -> BenchPrompts:
    return replace(base, domain_guidance=_join(base.domain_guidance, *guidance))


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


JOBBENCH_BASE = BenchPrompts(
    work=(
        "Professional workplace analysis across occupations. The agent receives a mixed workspace "
        "of data, documents, databases, reference material and instructions, then produces one or "
        "more immediately usable artifacts such as spreadsheets, reports, reconciliations, plans "
        "or technical files. Source fidelity, reproducible computation and professional "
        "conventions all matter; prose alone is not a substitute for a requested file."
    ),
    domain_word="occupational domain",
    deliverable_word="work product",
    inv_levers=(
        "- inventory inputs by format and authority; inspect more than the first obvious file and "
        "record how tables, identifiers and versions relate\n"
        "- use programmatic extraction and calculation for structured data, preserve intermediate "
        "counts and assumptions, and reconcile totals before drafting conclusions\n"
        "- turn the request into an output manifest with exact paths, sheets, columns and "
        "sections; write viable artifacts early and improve them incrementally\n"
        "- reopen every produced file with an independent reader and verify non-empty computed "
        "values, formats, formulas, links and cross-file consistency"
    ),
    dom_levers=(
        "- encode the occupation's decision procedure, source hierarchy, calculation method, "
        "standard deliverable structure and quality-control checks—not a task's names or numbers\n"
        "- distinguish authoritative inputs from derived data and resolve conflicting sources "
        "explicitly\n"
        "- verify standards, regulations, formulas and current reference values from supplied "
        "material or an authorised source; expose uncertainty when verification is unavailable\n"
        "- make every recommendation traceable to a calculation, source location or stated "
        "professional rule"
    ),
    yardstick=(
        "Did the run discover the full workspace, establish source-of-truth relationships, perform "
        "calculations reproducibly, satisfy the requested schemas and filenames, preserve "
        "traceability, and reopen the final files rather than trusting that a write succeeded?"
    ),
    selection_checks=(
        "Prefer the work product that delivers every requested artifact and schema; uses all "
        "material sources with an explicit hierarchy; shows reproducible calculations and "
        "denominators; preserves identifiers, units, dates and formulas; supports claims with "
        "source locations; and can be used or imported directly without filling blanks or "
        "repairing file structure."
    ),
    selection_lead=(
        "COVERAGE OF WHAT WAS NAMED. Count what the instructions explicitly ask to be produced, "
        "computed, stated or explained, and pick the candidate that answers the most of those "
        "points. A specific value, threshold, category or name that the source supports is worth "
        "more than a careful statement that the information was unavailable, and material beyond "
        "what was asked costs nothing here -- an omitted requirement is the failure that matters. "
        "Verify specifics against `context/source/` and reject the ones it contradicts, but do not "
        "prefer vagueness merely because a specific is hard to confirm."
    ),
    selection_conformance="value_only",
    prompt_pack="tts",
)


JOBBENCH_DOMAINS: dict[str, str] = {
    "Business / Financial Ops": (
        "DOMAIN LENS — BUSINESS / FINANCIAL OPERATIONS. Begin with reconciliation and definitions, "
        "then apply the supplied policy, regulatory or decision rules. Show denominators, periods, "
        "units, threshold logic and sensitivity; keep recommendations distinct from computed facts."
    ),
    "Other (Legal·Sales·Sci·Edu)": (
        "DOMAIN LENS — LEGAL / SALES / SCIENCE / EDUCATION. This grouping is heterogeneous, so the "
        "profession overlay controls. In every case identify the governing framework, exact output "
        "convention and source hierarchy before applying a legal, commercial or academic method."
    ),
    "Office / Admin Support": (
        "DOMAIN LENS — OFFICE / ADMINISTRATIVE SUPPORT. Preserve systems of record, exact strings, "
        "templates and formulas. Resolve duplicate entities and conflicting records through an "
        "explicit precedence rule, then produce an audit trail that another operator can follow."
    ),
    "Computer / Mathematical": (
        "DOMAIN LENS — COMPUTER / MATHEMATICAL. Define the population, variables, interfaces or "
        "system boundary precisely. Use executable calculations or diagnostics, test assumptions "
        "and edge cases, and report enough intermediate evidence for the result to be reproduced."
    ),
    "Architecture / Engineering": (
        "DOMAIN LENS — ARCHITECTURE / ENGINEERING. Track units, geometry, sign conventions, "
        "operating conditions, safety factors and governing standards. Use the exact supplied "
        "method and reference data, show intermediate calculations and perform independent "
        "magnitude and compliance checks."
    ),
    "Management": (
        "DOMAIN LENS — MANAGEMENT. Reconcile operational and financial baselines before comparing "
        "options. Make capacity, cost, risk, dependency and time-horizon assumptions explicit; "
        "connect recommendations to quantified evidence and an implementable owner or next step."
    ),
    "Arts / Media": (
        "DOMAIN LENS — ARTS / MEDIA. Synthesize disparate source material without changing its "
        "meaning, respect the supplied style or production constraints, separate public-facing "
        "content from internal notes, and preserve source attribution and transformation "
        "traceability."
    ),
}


JOBBENCH_PROFESSIONS: dict[str, str] = {
    "biostatisticians": (
        "PROFESSION LENS — Define cohort, endpoint and denominator; handle missingness, subgroup "
        "size, assumptions and sensitivity; make statistical work reproducible and cite supplied "
        "guidance precisely."
    ),
    "bookkeeping_accounting_and_auditing_clerks": (
        "PROFESSION LENS — Preserve ledger identity, period and account treatment; reconcile "
        "source transactions to control totals; expose unmatched items and keep formulas intact."
    ),
    "civil_engineers": (
        "PROFESSION LENS — Preserve geometry, units and item definitions; use supplied design or "
        "cost standards; show corrections, quantities, assumptions and safety or constructability "
        "checks."
    ),
    "computer_and_information_research_scientists": (
        "PROFESSION LENS — State experimental or model assumptions, data lineage and comparison "
        "baselines; use reproducible code and distinguish measured behavior from interpretation."
    ),
    "computer_and_information_systems_managers": (
        "PROFESSION LENS — Map systems, ownership, dependencies, controls, incidents and cost "
        "horizons; reconcile inventories before making governance or investment recommendations."
    ),
    "computer_user_support_specialists": (
        "PROFESSION LENS — Reproduce the symptom, establish environment and scope, follow evidence "
        "through logs and configuration, give safe ordered remediation and verify the result."
    ),
    "court_clerks": (
        "PROFESSION LENS — Preserve case identity, dates, status vocabulary, fee or filing rules "
        "and record provenance; use exact schemas and flag unresolved conflicts rather than "
        "silently normalising them."
    ),
    "customer_service_representatives": (
        "PROFESSION LENS — Reconstruct the customer timeline and account state, apply supplied "
        "policy consistently, quantify usage or charges, and provide an actionable resolution with "
        "required notices."
    ),
    "data_entry_keyers": (
        "PROFESSION LENS — Apply an explicit source precedence, validate codes and required "
        "fields, resolve duplicates and retain an exception log for every value that cannot be "
        "reconciled safely."
    ),
    "financial_managers_branch_or_department": (
        "PROFESSION LENS — Reconcile balances and classifications, calculate ratios from explicit "
        "periods and denominators, compare with supplied references and separate observed "
        "deterioration from forecast risk."
    ),
    "human_resources_specialists": (
        "PROFESSION LENS — Apply policy and thresholds consistently across people, preserve "
        "privacy, distinguish eligibility from recommendation and document the evidence behind "
        "every classification."
    ),
    "lawyers": (
        "PROFESSION LENS — Fix jurisdiction, date, issue and authority hierarchy; state the "
        "governing rule, apply it to sourced facts, address counterarguments and produce practical "
        "advice rather than an abstract survey."
    ),
    "licensing_examiners_and_inspectors": (
        "PROFESSION LENS — Verify entity identity, eligibility, duplicates, required documents and "
        "exceptions against the supplied rules; preserve a review trail and route ambiguous cases "
        "rather than guessing."
    ),
    "management_analysts": (
        "PROFESSION LENS — Establish a clean operational baseline, reconcile sources, define each "
        "metric and comparison group, test constraints and turn findings into quantified "
        "implementable actions."
    ),
    "mechanical_engineering_technicians": (
        "PROFESSION LENS — Preserve signal units, sampling conditions and equipment geometry; "
        "compute diagnostic frequencies or limits from supplied parameters and distinguish symptom "
        "from root cause."
    ),
    "mechanical_engineers": (
        "PROFESSION LENS — Apply the supplied design method under the correct geometry, material "
        "and operating conditions; show lookup provenance, intermediate factors, units and "
        "independent safety checks."
    ),
    "medical_and_health_services_managers": (
        "PROFESSION LENS — Reconcile service, payer, staffing and financial definitions; protect "
        "sensitive information, apply supplied reimbursement or policy rules and show operational "
        "consequences."
    ),
    "medical_secretaries": (
        "PROFESSION LENS — Preserve patient identity, laterality, dates, authorisation and code "
        "specificity; apply privacy and routing rules, retain exact required language and escalate "
        "unsafe ambiguity."
    ),
    "online_merchants": (
        "PROFESSION LENS — Reconcile catalog and transaction data, define ranking and exclusion "
        "logic, preserve platform-compatible formatting and connect merchandising actions to "
        "measured evidence."
    ),
    "personal_financial_advisors": (
        "PROFESSION LENS — Reconcile holdings and account types first; make tax, timing and risk "
        "assumptions explicit; run scenario calculations and avoid applying a strategy where the "
        "account structure defeats it."
    ),
    "petroleum_engineers": (
        "PROFESSION LENS — Normalise well identifiers and time series, preserve units and "
        "operating context, fit the appropriate supplied physical model and report parameter "
        "uncertainty and forecast limitations."
    ),
    "police_fire_and_ambulance_dispatchers": (
        "PROFESSION LENS — Reconstruct incident chronology and resource state, apply the supplied "
        "triage and dispatch protocol exactly, preserve canonical incident terms and make "
        "safety-critical conflicts explicit."
    ),
    "producers": (
        "PROFESSION LENS — Reconcile budget, schedule, labour and production constraints; "
        "distinguish committed from conditional resources and validate that the plan satisfies "
        "every timing and rest requirement."
    ),
    "purchasing_agents_except_wholesale_retail_and_farm_products": (
        "PROFESSION LENS — Normalise quantities and specifications, calculate fully loaded cost "
        "including conditional freight or installation, test compliance and document award "
        "trade-offs."
    ),
    "reporters_and_correspondents": (
        "PROFESSION LENS — Build a source log, reconcile conflicting datasets, distinguish "
        "observation from allegation or inference, quantify carefully and preserve "
        "publication-ready attribution."
    ),
    "sales_agents_securities_and_commodities": (
        "PROFESSION LENS — Reconcile trades, accounts, fees and reversals bidirectionally; apply "
        "supplied suitability and compliance rules with exact thresholds and retain an exception "
        "trail."
    ),
    "sales_representatives_wholesale_and_manufacturing_technical_and_scientific_products": (
        "PROFESSION LENS — Match customer requirements to verified product specifications, "
        "calculate lifecycle economics on comparable assumptions and separate documented "
        "capability from sales inference."
    ),
    "secretaries_and_administrative_assistants_except_legal_medical_and_executive": (
        "PROFESSION LENS — Preserve directive precedence, calendars, identities and exact template "
        "language; reconcile records, protect sensitive data and produce an operational handoff "
        "with unresolved items visible."
    ),
    "social_science_research_assistants": (
        "PROFESSION LENS — Define variables and populations, harmonise identifiers and coding, "
        "document imputation or exclusions, run reproducible analysis and separate association "
        "from causal claim."
    ),
    "sociology_teachers_postsecondary": (
        "PROFESSION LENS — Apply the supplied syllabus and weighting rules exactly, preserve "
        "transparent calculations, identify intervention cases consistently and write feedback "
        "grounded in the submitted work."
    ),
    "statisticians": (
        "PROFESSION LENS — Define estimand, population and denominator; inspect distributions and "
        "missingness, use the appropriate model, validate assumptions and provide reproducible "
        "tables with uncertainty."
    ),
    "supply_chain_managers": (
        "PROFESSION LENS — Reconcile demand, inventory, capacity, lanes and time periods; model "
        "physical constraints and cost scenarios, expose cross-shipping or bottlenecks and assign "
        "practical next steps."
    ),
    "technical_writers": (
        "PROFESSION LENS — Reconcile source, examples, configuration and observed behavior; "
        "preserve exact interface names, remove internal-only noise, follow the supplied style and "
        "make transformation decisions traceable."
    ),
    "training_and_development_specialists": (
        "PROFESSION LENS — Map audience, competency, risk and learning objective; align content, "
        "assessment and follow-up measures, apply supplied policy and make the deliverable "
        "executable by facilitators."
    ),
    "web_administrators": (
        "PROFESSION LENS — Establish topology, configuration source of truth and failure timeline; "
        "validate changes safely, preserve rollback instructions and connect recommendations to "
        "logs and observable system behavior."
    ),
}


def jobbench_prompts(*, domain: str = "", profession: str = "") -> BenchPrompts:
    """JobBench-wide profile plus SOC-style domain and occupation overlays."""
    return _with_guidance(
        JOBBENCH_BASE,
        _lookup(JOBBENCH_DOMAINS, domain),
        _lookup(JOBBENCH_PROFESSIONS, profession),
    )


_TOKEN = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _interp(text: str, ctx: dict) -> str:
    """Substitute {{ name }} tokens only; single braces in prose stay untouched."""
    return _TOKEN.sub(lambda m: str(ctx.get(m.group(1), "")), text)


def _strip_comments(text: str) -> str:
    """Drop HTML comment blocks, so a comment-only component renders to nothing."""
    return _COMMENT.sub("", text).strip()


def _read_component(staged: Path, rel: str) -> str:
    p = staged / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""


def _render_ctx(domain: str) -> dict:
    """Template slots used by the JobBench harness."""
    return {
        "env_description": "A command-line workspace containing the task folder and an "
        "output directory; standard CLI + Python tooling is available.",
        "domain_focus": domain,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "working_directory": "the temp workspace (task_folder/ has inputs; write to output/)",
    }


def _list_scripts(staged: Path) -> list[Path]:
    d = staged / "scripts"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.py") if not p.name.startswith("_"))


def _script_doc(p: Path) -> str:
    """First line of the script's module docstring, if any."""
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "helper script"
    m = re.search(r'^\s*(?:"""|\'\'\')(.+)', txt)
    return m.group(1).strip()[:120] if m else "helper script"


def compose_agent_body(staged: Path, ctx: dict) -> str:
    """c1 prose + c6 guardrails + c4 tool guidance + c3 memory + a c7 script note."""
    parts: list[str] = []
    sp = _strip_comments(_interp(_read_component(staged, "systemprompt.md"), ctx))
    if sp:
        parts.append(sp)
    gr = _strip_comments(_interp(_read_component(staged, "guardrails.md"), ctx))
    if gr:
        parts.append("# Operating Guardrails\n\n" + gr)
    tg = _strip_comments(
        _interp(_read_component(staged, "tool_descriptions/tool_guidance.md"), ctx)
    )
    if tg:
        parts.append(tg)
    mem = _strip_comments(_interp(_read_component(staged, "LongTermMEMORY.md"), ctx))
    if mem:
        parts.append("# Long-Term Memory\n\n" + mem)
    scripts = _list_scripts(staged)
    if scripts:
        listing = "\n".join(f"- `python scripts/{s.name}` — {_script_doc(s)}" for s in scripts)
        parts.append(
            "# Helper scripts (run via Bash)\n\nDeterministic helpers are available "
            "in `./scripts`. Prefer them over hand-rolling the same logic:\n\n" + listing
        )
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


def _ensure_mode_subagent(text: str) -> str:
    if re.search(r"(?m)^mode:\s*subagent", text):
        return text
    if text.lstrip().startswith("---"):
        return re.sub(r"^---\n", "---\nmode: subagent\n", text, count=1)
    return "---\nmode: subagent\ndescription: delegated subagent\n---\n\n" + text


def render_opencode_tree(staged: Path, render_dir: Path, ctx: dict) -> None:
    """Materialise the staged tree into OpenCode-loadable artifacts under render_dir:
    AGENTS.md, .opencode/skills/<n>/, .opencode/agent/<sub>.md, scripts/, plugin/.
    Skill directories are copied whole, so skills/<n>/scripts/*.py survive."""
    staged, rd = Path(staged), Path(render_dir)
    if rd.exists():
        shutil.rmtree(rd)
    (rd / ".opencode" / "agent").mkdir(parents=True)
    (rd / ".opencode" / "skills").mkdir(parents=True)

    body = compose_agent_body(staged, ctx)
    if body.strip():
        (rd / "AGENTS.md").write_text(body, encoding="utf-8")

    skills_dir = staged / "skills"
    if skills_dir.is_dir():
        for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
            shutil.copytree(skill_md.parent, rd / ".opencode" / "skills" / skill_md.parent.name)

    sub_dir = staged / "sub_agents"
    if sub_dir.is_dir():
        for agent_md in sorted(sub_dir.glob("*/AGENT.md")):
            (rd / ".opencode" / "agent" / f"{agent_md.parent.name}.md").write_text(
                _ensure_mode_subagent(agent_md.read_text(encoding="utf-8", errors="replace")),
                encoding="utf-8",
            )

    for s in _list_scripts(staged):
        (rd / "scripts").mkdir(exist_ok=True)
        shutil.copy(s, rd / "scripts" / s.name)

    plugin_dir = staged / "plugin"
    if plugin_dir.is_dir():
        for p in sorted(plugin_dir.glob("*.ts")):
            if p.name.startswith("_"):
                continue
            (rd / ".opencode" / "plugin").mkdir(parents=True, exist_ok=True)
            shutil.copy(p, rd / ".opencode" / "plugin" / p.name)


_INSTALL_MODE = {"oc": "workspace_agents_md", "cx": "codex_root"}

_DEFAULT_TIMEOUT = {"oc": 2400, "cx": 3600}


def _channels() -> dict:
    """Improver channel, resolved through core.spec.resolve_channel_model."""
    ch = resolve_channel_model(None, None, effort="high")
    return {"proposer": ch, "selector": ch}


def make_spec(substrate: str) -> BenchSpec:
    if substrate not in _INSTALL_MODE:
        raise ValueError(f"JobBench substrate must be 'oc' or 'cx', got {substrate!r}")
    return BenchSpec(
        name="jb",
        runs_root=config.RUNS / "jb",
        install_mode=_INSTALL_MODE[substrate],
        prompts=jobbench_prompts(),
        channels=_channels(),
        domain_word="profession",
        leak_globs=("RUBRICS.json", "eval_result/**", "expected/**", "reference/**"),
        allow_subagents=(substrate == "oc"),
        obs_trunc=config.JB_OBS_TRUNC,
        proposer_deliverables="contents",
    )


_MAX_CHARS_PER_FILE = 200_000
_VISION_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
_MAX_VISION = 8

_EXTRACT_DRIVER = """\
import importlib.util, json, sys
from pathlib import Path

judge_py, run_dir = sys.argv[1], Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("_jb_judge_extractor", judge_py)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
files = {}
for f in sorted(p for p in run_dir.rglob("*") if p.is_file()):
    rel = str(f.relative_to(run_dir))
    try:
        files[rel] = str(mod.convert_file_to_text(f))
    except Exception as exc:                       # per-file tool failure, recorded in place
        files[rel] = f"(extraction failed: {type(exc).__name__}: {exc})"
print(json.dumps({
    "files": files,
    "flat": str(mod.extract_all_file_contents(run_dir)),
    "images": [str(p) for p in mod.collect_image_paths(run_dir)],
}))
"""

_EXTRACT_CACHE: dict[str, dict] = {}


def _extractor_lacks_deps(got: dict) -> bool:
    """True when judge.py's extractor ran without its optional file readers."""
    return any(
        isinstance(v, str) and v.startswith("[ERROR:") and "not available" in v
        for v in (got.get("files") or {}).values()
    )


_JUDGE_FAILURE_PREFIXES = ("opus judge failed", "failed after", "failed to get judge response")


def _has_events(p: Path) -> bool:
    """True when the file holds at least one JSON event line."""
    try:
        with Path(p).open(encoding="utf-8", errors="replace") as fh:
            return any(ln.lstrip().startswith("{") for ln in fh)
    except OSError:
        return False


def _judge_failed(got: dict) -> bool:
    """True when the JUDGE failed (a transport error, or an error-only report), as
    opposed to the run merely scoring badly."""
    err = got.get("error")
    if isinstance(err, str) and err.strip() and not (got.get("rubrics") or got.get("details")):
        return True
    for key in ("overall_reasoning", "reasoning", "error"):
        v = got.get(key)
        if isinstance(v, str) and "judge failed" in v.lower():
            return True
    reasons = (
        (r.get("result") or {}).get("overall_reasoning") or r.get("overall_reasoning")
        for r in (got.get("rubrics") or got.get("details") or [])
        if isinstance(r, dict)
    )
    return any(str(v or "").lower().startswith(_JUDGE_FAILURE_PREFIXES) for v in reasons)


def _parse_details(got: dict) -> dict | None:
    """Weighted total_score / max_score, with a labelled unweighted fallback."""
    try:
        results = [r.get("result") or {} for r in got.get("rubrics") or []]
        n_c = sum(int(r.get("criteria_count") or 0) for r in results)
        n_p = sum(int(r.get("criteria_passed") or 0) for r in results)
        total = float(got.get("total_score") or 0)
        mx = float(got.get("max_score") or 0)
    except (TypeError, ValueError):
        return None
    if not mx:
        if n_c <= 0:
            return None
        return {
            "n_passed": n_p,
            "n_criteria": n_c,
            "all_pass": n_p == n_c,
            "metric": "unweighted-fallback",
        }
    return {
        "n_passed": total,
        "n_criteria": mx,
        "all_pass": bool(n_c > 0 and n_p == n_c),
        "metric": "weighted",
        "criteria_passed": n_p,
        "criteria_count": n_c,
        "judge_model": str(got.get("judge_model") or ""),
    }


class JobBenchAdapter:
    """JobBench behind the BenchmarkAdapter protocol, on either substrate."""

    def __init__(
        self,
        substrate: str = "oc",
        *,
        timeout: int | None = None,
        store_root: Path | None = None,
        split_file: Path | None = None,
    ):
        self.split_file = split_file
        self.spec = make_spec(substrate)
        launcher = self._launch_opencode if substrate == "oc" else self._launch_codex
        self.substrate = (
            OpencodeSubstrate(launcher=launcher)
            if substrate == "oc"
            else CodexSubstrate(launcher=launcher)
        )
        self.timeout = int(timeout or _DEFAULT_TIMEOUT[substrate])
        self.store = Path(store_root) if store_root else config.RUNS / "jb" / "dataset"

    def tasks(self, split: str = "all") -> list[str]:
        return select(discover("jb"), split, self.split_file, benchmark="jb")

    def domain_of(self, task_id: str) -> str:
        return task_id.split("/", 1)[0]

    def reference_domain_order(self) -> list[str]:
        return sorted(set(discover("jb").values()))

    def prompts_for(self, task_id: str) -> BenchPrompts:
        return jobbench_prompts(domain=self.domain_of(task_id), profession=task_id.split("/", 1)[0])

    def task_prompt(self, task_id: str) -> str:
        """TASK_INSTRUCTIONS.txt, as the agent received it."""
        p = self._dataset_task(task_id) / "task_folder" / "TASK_INSTRUCTIONS.txt"
        if not p.is_file():
            raise FileNotFoundError(
                f"task instructions missing: {p} — re-materialise dependencies/job-bench-eval's "
                f"dataset/main (recipe in dependencies/PINNED.md)"
            )
        return p.read_text(encoding="utf-8", errors="replace")

    def task_files(self, task_id: str) -> Path | None:
        d = self._dataset_task(task_id) / "task_folder"
        return d if d.is_dir() else None

    def _dataset_task(self, task_id: str) -> Path:
        if "/" not in task_id or ".." in task_id or task_id.startswith("/"):
            raise ValueError(
                f"malformed JobBench task id {task_id!r} (expected '<profession>/task<N>')"
            )
        return config.jb_repo() / "dataset" / "main" / task_id

    def _out_dir(self, task_id: str, label: str) -> Path:
        return self.store / task_id / "model_output" / label

    def _traj_dir(self, task_id: str, label: str) -> Path:
        return self.store / task_id / "model_traj" / label

    def _task_and_label(self, run_dir: Path) -> tuple[str, str]:
        """`<store>/<profession>/<taskN>/model_output/<label>` -> (task_id, label)."""
        p = Path(run_dir)
        parts = p.parts
        if "model_output" not in parts:
            raise ValueError(f"{p} is not a JobBench model_output run directory")
        i = parts.index("model_output")
        return "/".join(parts[i - 2 : i]), p.name

    def _ensure_store_task(self, task_id: str) -> Path:
        """Copy the clean task inputs from the dataset into the run store, once, atomically."""
        dst = self.store / task_id / "task_folder"
        if dst.is_dir():
            return dst
        src = self._dataset_task(task_id) / "task_folder"
        if not src.is_dir():
            raise FileNotFoundError(
                f"dataset task inputs missing: {src} — re-materialise dependencies/job-bench-eval "
                f"(recipe in dependencies/PINNED.md)"
            )
        dst.parent.mkdir(parents=True, exist_ok=True)
        with _STORE_SEED_LOCK:
            if dst.is_dir():
                return dst
            tmp = dst.parent / f".task_folder.tmp.{os.getpid()}.{threading.get_ident()}"
            shutil.copytree(src, tmp, dirs_exist_ok=True)
            try:
                os.rename(tmp, dst)
            except OSError:
                shutil.rmtree(tmp, ignore_errors=True)
                if not dst.is_dir():
                    raise
        return dst

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        """One rollout; the label carries the whole namespace (run_id, / rewritten __)."""
        label = run_id.replace("/", "__")
        out = self._out_dir(task_id, label)
        mark = self._traj_dir(task_id, label)
        if RM.is_complete(mark, harness_dir=harness_dir) and self.artifacts_intact(out):
            out.mkdir(parents=True, exist_ok=True)
            return out
        if out.is_dir() or mark.is_dir():
            reason = RM.why_not(mark, harness_dir=harness_dir)
            if reason == "reusable":
                reason = "output fingerprint missing or changed"
            print(
                f"  [jb-{self.substrate.name}] re-rolling {label}: {reason}",
                flush=True,
            )
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(mark, ignore_errors=True)
        _EXTRACT_CACHE.pop(str(out.resolve()), None)
        reporting, own = self._details_candidates(task_id, label)
        if reporting is not None:
            reporting.unlink(missing_ok=True)
        if own.parent.is_dir():
            shutil.rmtree(own.parent)
        self._ensure_store_task(task_id)
        src = Path(harness_dir) if harness_dir is not None else self.base_harness()
        install = self.substrate.materialise(src, self.spec, {})
        try:
            dirs = self.substrate.execute(task_id, install, [run_id], int(timeout or self.timeout))
        finally:
            shutil.rmtree(install.staging_dir, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        RM.mark_complete(
            mark,
            run_id=run_id,
            harness_dir=harness_dir,
            extra={"output_fingerprint": self._output_fingerprint(out)},
        )
        return dirs[0]

    def _output_fingerprint(self, run_dir: Path) -> str | None:
        return RM.fingerprint(run_dir) if self.delivered(run_dir) else None

    def artifacts_intact(self, run_dir: Path) -> bool:
        task_id, label = self._task_and_label(run_dir)
        trajectory_dir = self._traj_dir(task_id, label)
        marker = RM.read_mark(trajectory_dir) or {}
        return (
            "output_fingerprint" in marker
            and marker["output_fingerprint"] == self._output_fingerprint(run_dir)
            and (
                self.delivered(run_dir)
                or any(_has_events(path) for path in trajectory_dir.glob("*.jsonl"))
            )
        )

    def delivered(self, run_dir: Path) -> bool:
        return Path(run_dir).is_dir() and any(p.is_file() for p in Path(run_dir).rglob("*"))

    def _launch_common(self, req: RunRequest) -> tuple[str, str, int]:
        if len(req.run_ids) != 1:
            raise ValueError(
                f"JobBench rolls one run per invocation (no batch substrate "
                f"here); got {len(req.run_ids)} run ids"
            )
        return req.task_ref, req.run_ids[0].replace("/", "__"), int(req.timeout)

    def _launch_opencode(self, req: RunRequest, install: Install) -> list[Path]:
        task_id, label, t = self._launch_common(req)
        self.substrate.preflight()
        render_dir = Path(tempfile.mkdtemp(prefix=f"hrender_{label}_"))
        render_opencode_tree(install.staging_dir, render_dir, _render_ctx(self.domain_of(task_id)))
        env = {
            **os.environ,
            "TASK_IDS": task_id,
            "LABEL": label,
            "HARNESS_RENDER_DIR": str(render_dir),
            "AGENT_NAME": "",
            "TASKS_BASE_DIR": str(self.store),
            "OPENCODE_DIR": str(config.OPENCODE_DIR),
            "MODEL_ID": config.SOLVER_MODEL_OC,
            "TIMEOUT_PER_TASK": str(t),
            "MAX_RETRIES": "2",
            "MAX_CONCURRENT": "1",
            "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": str(OUTPUT_TOKEN_MAX),
        }
        self._run_runner(OC_RUNNER, env, wall=2 * t + 600, label=label)
        return [self._require_artifacts(task_id, label)]

    def _launch_codex(self, req: RunRequest, install: Install) -> list[Path]:
        task_id, label, t = self._launch_common(req)
        self.substrate.preflight()
        codex_home = config.CODEX_HOME_DIR
        stale = codex_config_error()
        if stale is not None:
            raise RuntimeError(stale)
        env = self.substrate.exec_env(os.environ, codex_home=codex_home)
        env.update(
            {
                "HARNESS2_CODEX_BIN": str(Path(config.CODEX_DIST) / "codex"),
                "TASK_IDS": task_id,
                "LABEL": label,
                "HARNESS_RENDER_DIR": str(install.staging_dir),
                "TASKS_BASE_DIR": str(self.store),
                "BENCHMARK_MODELS": config.SOLVER_MODEL_CX,
                "MAX_CONCURRENT_PER_MODEL": "1",
                "TIMEOUT_PER_TASK": str(t),
                "VERTEX_PROXY_KEY": config.VERTEX_PROXY_KEY,
            }
        )
        self._run_runner(CX_RUNNER, env, wall=t + 600, label=label)
        return [self._require_artifacts(task_id, label)]

    def _run_runner(self, runner: Path, env: dict, *, wall: int, label: str) -> None:
        """Run the packaged launcher; timeouts leave the rollout incomplete."""
        if not runner.is_file():
            raise FileNotFoundError(
                f"JobBench runner missing: {runner}; reinstall the Harness² package."
            )
        env = {
            **env,
            "PATH": f"{config.JB_JUDGE_PYTHON.parent}:{config.BUN.parent}:" + env.get("PATH", ""),
        }
        argv = ["bash", str(runner)] if runner.suffix == ".sh" else [sys.executable, str(runner)]
        try:
            run_detached(argv, env=env, stdin=subprocess.DEVNULL, timeout=wall)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"runner wall-timeout ({wall}s) for {label}; artifacts left UNMARKED so the "
                f"next attempt re-rolls instead of adopting a truncated rollout"
            ) from None

    def _require_artifacts(self, task_id: str, label: str) -> Path:
        out, traj = self._out_dir(task_id, label), self._traj_dir(task_id, label)
        has_out = out.is_dir() and any(out.iterdir())
        if not has_out:
            # A trajectory is not liveness: an attempt abandoned on a 401 or a rate-limit
            # storm still leaves .jsonl events behind. Both launchers already exit
            # non-zero here, so this only catches a launcher that forgot to; the rollout
            # stays UNMARKED so the next attempt re-rolls rather than being cached and
            # scored as a real zero.
            has_traj = traj.is_dir() and any(_has_events(p) for p in traj.glob("*.jsonl"))
            raise RuntimeError(
                f"JobBench produced no output for {task_id} @ {label} "
                f"({'trajectory without deliverables' if has_traj else 'dead agent'}: "
                f"proxy/auth/binary) -- left UNMARKED so the next attempt re-rolls "
                f"instead of serving an empty rollout as evidence"
            )
        return out

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        """Newest attempt's event stream, via the substrate's renderer."""
        task_id, label = self._task_and_label(run_dir)
        td = self.store / task_id / "model_traj" / label
        files = (
            sorted(td.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            if td.is_dir()
            else []
        )
        if not files:
            return f"(no trajectory for {task_id} @ {label})"
        return self.substrate.read_trajectory(files[0], obs_trunc=obs_trunc, max_chars=8_000_000)

    def _extract(self, run_dir: Path) -> dict:
        """The judge's own extractor in a subprocess; raw text reads as the visible fallback."""
        key = str(Path(run_dir).resolve())
        hit = _EXTRACT_CACHE.get(key)
        if hit is not None:
            return hit
        judge_py = config.jb_repo() / "eval" / "judge.py"
        py = config.JB_JUDGE_PYTHON if config.JB_JUDGE_PYTHON.is_file() else Path(sys.executable)
        got: dict | None = None
        if judge_py.is_file():
            try:
                r = subprocess.run(
                    [str(py), "-c", _EXTRACT_DRIVER, str(judge_py), key],
                    capture_output=True,
                    text=True,
                    timeout=900,
                )
                if r.returncode == 0:
                    got = json.loads(r.stdout)
                    if isinstance(got, dict) and _extractor_lacks_deps(got):
                        got = None
            except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
                got = None
        if not isinstance(got, dict) or "files" not in got:
            print(
                f"  [jb] judge extractor unavailable for {key}; serving raw text reads", flush=True
            )
            got = self._raw_extract(Path(run_dir))
        _EXTRACT_CACHE[key] = got
        return got

    @staticmethod
    def _raw_extract(d: Path) -> dict:
        files: dict[str, str] = {}
        parts: list[str] = []
        images: list[str] = []
        for f in sorted(p for p in d.rglob("*") if p.is_file()):
            rel = str(f.relative_to(d))
            if f.suffix.lower().lstrip(".") in _VISION_EXTS:
                images.append(str(f))
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                text = f"(unreadable: {exc})"
            files[rel] = text
            clipped = (
                text
                if len(text) <= _MAX_CHARS_PER_FILE
                else text[:_MAX_CHARS_PER_FILE]
                + f"\n... [Content truncated at {_MAX_CHARS_PER_FILE} characters]"
            )
            parts.append(f"=== FILE: {f.name} ===\n{clipped}\n")
        return {"files": files, "flat": "\n".join(parts), "images": images[:_MAX_VISION]}

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        """filename -> extracted text, via the judge's own per-file reader."""
        d = Path(run_dir)
        return dict(self._extract(d)["files"]) if d.is_dir() else {}

    def deliverable_images(self, run_dir: Path) -> list[Path]:
        d = Path(run_dir)
        return [Path(p) for p in self._extract(d)["images"]] if d.is_dir() else []

    def deliverable_text(self, run_dir: Path) -> str:
        """The deliverable CONTENTS (the judge's flat rendering), not just the names."""
        d = Path(run_dir)
        return str(self._extract(d)["flat"]) if d.is_dir() else ""

    def base_harness(self) -> Path:
        base = OC_BASE_HARNESS if self.substrate.name == "oc" else CX_BASE_HARNESS
        if not HT.is_tree(self.substrate.surface(), base):
            raise FileNotFoundError(
                f"JobBench base harness missing or not a {self.substrate.surface().name} "
                f"tree: {base} — restore third_party/jb_runner/; an absent base would make "
                f"every base rollout silently harness-free"
            )
        return base

    def validate_tree(self, tree: dict) -> None:
        """JobBench adds no environment invariant beyond the framework rules."""
        return None

    def preflight(self) -> None:
        """Check everything money-adjacent before a rollout is paid for."""
        self.substrate.preflight()
        judge_py = config.jb_repo() / "eval" / "judge.py"
        if not judge_py.is_file():
            raise RuntimeError(
                f"upstream JobBench judge missing at {judge_py}; run scripts/setup_benchmark.py jb"
            )
        if self.substrate.name == "cx":
            config.require("HARNESS2_CODEX_API_KEY", needed_by="JB codex cells")
            # The rollout reads the endpoint from CODEX_HOME/config.toml, not from the
            # environment the substrate preflight just probed; refuse to report ready
            # for an endpoint the run would never contact.
            stale = codex_config_error()
            if stale is not None:
                raise RuntimeError(stale)
        py = config.JB_JUDGE_PYTHON
        remedy = "python scripts/setup_benchmark.py jb"
        if not py.is_file():
            raise RuntimeError(f"JB judge interpreter missing at {py}; {remedy}")
        try:
            r = subprocess.run(
                [str(py), "-c", "import pandas, openpyxl, docx"],
                capture_output=True,
                text=True,
                timeout=300,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise RuntimeError(
                f"JB judge interpreter {py} could not be run: {exc}; {remedy}"
            ) from exc
        if r.returncode != 0:
            raise RuntimeError(
                f"JB judge interpreter {py} lacks the extraction deps (pandas, openpyxl, "
                f"python-docx): {r.stderr.strip()[-300:]} -- {remedy}. Without them the judge "
                f"grades '[ERROR: ... not available]' text as a real zero."
            )
        if not config.JB_JUDGE_API_BASE:
            print(
                "  [jb] WARNING: HARNESS2_JUDGE_BASE_URL is empty; rollouts will run "
                "but judging refuses until it is set (see .env.example)",
                flush=True,
            )

    def _details_candidates(self, task_id: str, label: str) -> tuple[Path | None, Path]:
        """(reporting judge, configured judge) detail files, in serving order."""
        er = self.store / task_id / "eval_result"
        safe = config.JB_JUDGE_MODEL.split("/")[-1].replace(".", "-")
        pattern = config.JB_REPORTING_JUDGE_DETAILS
        reporting = (er / pattern.format(label=label)) if pattern else None
        return (reporting, er / f"eval_{label}" / f"{safe}_judge.json")

    def _read_details(self, details: Path) -> dict | None:
        if not details.is_file():
            return None
        try:
            got = json.loads(details.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(got, dict):
            return None
        if _judge_failed(got):
            q = details.with_name(details.name + ".judge_failed")
            try:
                os.replace(details, q)
            except OSError as exc:
                print(
                    f"  [jb] could not quarantine judge-failed details {details}: {exc}", flush=True
                )
            return None
        rubrics, tc = got.get("rubrics"), got.get("total_count")
        if isinstance(rubrics, list) and isinstance(tc, int) and tc > 0 and len(rubrics) < tc:
            print(
                f"  [jb] partial judge details ({len(rubrics)}/{tc} rubrics) at {details}; "
                f"not served, judge will resume",
                flush=True,
            )
            return None
        return got

    def _no_output_score(self, task_id: str) -> dict | None:
        """A run that delivered nothing is a real zero, built from the rubric weights."""
        rp = self._dataset_task(task_id) / "RUBRICS.json"
        try:
            rubrics = json.loads(rp.read_text(encoding="utf-8")).get("rubrics") or []
        except (OSError, json.JSONDecodeError, AttributeError):
            return None
        mx, n_c = 0.0, 0
        for r in rubrics:
            if not isinstance(r, dict):
                continue
            mx += float(r.get("weight") or 0)
            crit = r.get("criterion", [])
            n_c += 1 if isinstance(crit, str) else len(list(crit))
        if not mx:
            return None
        return {
            "n_passed": 0.0,
            "n_criteria": mx,
            "all_pass": False,
            "metric": "weighted",
            "criteria_passed": 0,
            "criteria_count": n_c,
            "no_output": True,
        }

    def _run_judge(self, task_id: str, label: str, run_dir: Path) -> bool:
        """Invoke the upstream judge CLI in a subprocess; False on any failure."""
        judge_py = config.jb_repo() / "eval" / "judge.py"
        rubrics = self._dataset_task(task_id) / "RUBRICS.json"
        for need, what in ((judge_py, "upstream judge"), (rubrics, "task rubrics")):
            if not need.is_file():
                print(f"  [jb] {what} missing ({need}); judge not run", flush=True)
                return False
        py = config.JB_JUDGE_PYTHON
        if not py.is_file():
            print(
                f"  [jb] judge interpreter missing ({py}); run scripts/setup_benchmark.py jb "
                f"to a python with pandas/openpyxl/python-docx/mammoth/pdfplumber -- judge "
                f"not run (None, never 0)",
                flush=True,
            )
            return False
        api_base = config.JB_JUDGE_API_BASE
        try:
            token = (
                config.JB_JUDGE_API_KEY
                or subprocess.run(
                    ["gcloud", "auth", "application-default", "print-access-token"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                ).stdout.strip()
            )
        except (subprocess.SubprocessError, OSError):
            token = ""
        if not token:
            print(
                "  [jb] no Vertex access token (gcloud auth print-access-token); judge not run",
                flush=True,
            )
            return False
        _reporting, own = self._details_candidates(task_id, label)
        own.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(py),
            str(judge_py),
            "--output-dir",
            str(run_dir),
            "--rubrics-file",
            str(rubrics),
            "--details-file",
            str(own),
            "--judge-model",
            config.JB_JUDGE_MODEL,
            "--api-base",
            api_base,
            "--max-workers",
            "8",
            "--max-retries",
            "3",
            "--timeout-per-rubric",
            "240",
            "--evaluated-model",
            label,
        ]
        env = {
            **os.environ,
            "JUDGE_API_KEY": token,
            "JUDGE_REASONING_EFFORT": config.JB_JUDGE_REASONING_EFFORT,
            "VERTEXAI_LOCATION": config.VERTEX_REGION,
        }
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"  [jb] judge subprocess failed for {label}: {exc}", flush=True)
            return False
        if r.returncode != 0:
            print(f"  [jb] judge exited {r.returncode} for {label}: {r.stderr[-300:]}", flush=True)
            return False
        return True

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        """Judge one run. None means the judge FAILED, which is not a zero."""
        run_dir = Path(run_dir)
        label = run_dir.name
        reporting, own = self._details_candidates(task_id, label)
        for details in (reporting, own):
            if details is None:
                continue
            got = self._read_details(details)
            if got is not None:
                return _parse_details(got)
        if not self.delivered(run_dir):
            return self._no_output_score(task_id)
        if not self._run_judge(task_id, label, run_dir):
            return None
        got = self._read_details(own)
        return _parse_details(got) if got is not None else None


__all__ = [
    "JobBenchAdapter",
    "make_spec",
    "jobbench_prompts",
    "compose_agent_body",
    "render_opencode_tree",
    "OC_RUNNER",
    "CX_RUNNER",
    "OC_BASE_HARNESS",
    "CX_BASE_HARNESS",
]
