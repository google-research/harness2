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

"""Repeatable installation of benchmark tools and release-owned runner overlays."""

from __future__ import annotations

import json
import shlex
import shutil
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

from . import config

PATCHES = {
    "opencode": "opencode-compat.diff",
    "job-bench-eval": "job-bench-eval-judge.diff",
    "harvey-labs": "harvey-labs-judge-vertex-anthropic.diff",
    "workbuddy-bench": "workbuddy-bench-runtime.diff",
}

RUNNERS = (
    "jb_runner/run_benchmark_codex_cli_adapt.sh",
    "lab_runner/opencode/run_opencode.py",
    "wb_runner/agents/oc_agent.py",
)


def missing_runners() -> str:
    """Explain which setup runners or patches are absent from third_party/, if any.

    A copy of the code without third-party material still runs the demo and tests, but
    setup needs these files to install a benchmark.
    """
    need = [config.THIRD_PARTY / "patches" / name for name in PATCHES.values()]
    need += [config.THIRD_PARTY / name for name in RUNNERS]
    absent = [path for path in need if not path.exists()]
    if not absent:
        return ""
    return (
        "benchmark runners and patches are not included in this copy "
        f"({len(absent)} of {len(need)} missing under third_party/); "
        "see third_party/README.md"
    )


def run(cmd, *, cwd=None, dry_run=False):
    cmd = [str(c) for c in cmd]
    print((f"[{cwd}] " if cwd else "") + shlex.join(cmd), flush=True)
    if not dry_run:
        subprocess.run(cmd, cwd=cwd, check=True)


def clone(name: str, *, dry_run=False):
    repo = config.DEPENDENCIES / name
    pin = config.DEPENDENCY_PINS[name]
    if (repo / ".git").exists():
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
        )
        if revision.returncode == 0:
            head = revision.stdout.strip()
            if head == pin:
                return
            raise RuntimeError(
                f"{repo} is at {head}, expected {pin}; move this checkout aside before setup"
            )
        origin = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        if any(path.name != ".git" for path in repo.iterdir()) or (
            origin.stdout.strip() != config.DEPENDENCY_ORIGINS[name]
        ):
            raise RuntimeError(f"Incomplete checkout at {repo}; move it aside before setup")
    else:
        if repo.exists() and any(repo.iterdir()):
            raise RuntimeError(f"Refusing to overwrite nonempty {repo}")
        run(["git", "init", "-q", repo], dry_run=dry_run)
        run(
            ["git", "-C", repo, "remote", "add", "origin", config.DEPENDENCY_ORIGINS[name]],
            dry_run=dry_run,
        )
    run(["git", "-C", repo, "fetch", "--depth", "1", "origin", pin], dry_run=dry_run)
    run(["git", "-C", repo, "checkout", "--detach", "FETCH_HEAD"], dry_run=dry_run)


def patch(name: str, *, dry_run=False):
    repo = config.DEPENDENCIES / name
    source = config.THIRD_PARTY / "patches" / PATCHES[name]
    if dry_run:
        run(["git", "-C", repo, "apply", "--check", source], dry_run=True)
        run(["git", "-C", repo, "apply", source], dry_run=True)
        return
    if (
        subprocess.run(
            ["git", "-C", str(repo), "apply", "--reverse", "--check", str(source)],
            capture_output=True,
        ).returncode
        == 0
    ):
        return
    run(["git", "-C", repo, "apply", "--check", source])
    run(["git", "-C", repo, "apply", source])


def overlays(name: str, *, dry_run=False):
    assets = config.THIRD_PARTY
    repo = config.DEPENDENCIES / name
    pairs = []
    if name == "harvey-labs":
        pairs = [
            (assets / "lab_runner" / "opencode", repo / "opencode"),
            (assets / "lab_runner" / "tts_lab", repo / "external" / "tts_lab"),
            (assets / "lab_runner" / "adapt_runs_cx", repo / "external" / "adapt_runs_cx"),
        ]
    if name == "workbuddy-bench":
        pairs = [
            (assets / "wb_runner" / "agents", repo / "src" / "workbuddy_bench" / "agents"),
            (assets / "wb_runner" / "configs" / "harnesses", repo / "configs" / "harnesses"),
        ]
    for source, dest in pairs:
        print(f"Copy runner assets: {source} -> {dest}", flush=True)
        if not dry_run:
            shutil.copytree(
                source,
                dest,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "PROVENANCE.md"),
            )


def workbuddy_models(*, dry_run=False):
    """Generate the two Vertex routes from the user's model IDs, never credentials."""
    solver = config.SOLVER_MODEL_OC.removeprefix("google-vertex/")
    judge = config.JUDGE_MODEL
    problem = ""
    if not config.SOLVER_MODEL_OC.startswith("google-vertex/gemini") or not judge.startswith(
        "gemini"
    ):
        problem = (
            "WorkBuddy setup requires a google-vertex/gemini... executor "
            "and a gemini... judge; choose IDs available in your project"
        )
    elif not config.VERTEX_PROJECT:
        problem = "Set GOOGLE_CLOUD_PROJECT for WorkBuddy's Vertex routes"
    if problem:
        if not dry_run:
            raise config.ConfigError(problem)
        print(f"Configure WorkBuddy solver and judge routes -- {problem}", flush=True)
        return
    for role, model in (("solver", solver), ("judge", judge)):
        payload = {
            "model": {
                "name": "google/" + model,
                "protocols": ["openai"],
                "backend_url_env": "HARNESS2_WB_VERTEX_URL",
                "backend_key_command": "gcloud auth application-default print-access-token",
                "backend_key_ttl": 1800,
                "thought_signature_passback": True,
                "params": {
                    "max_output_tokens": 64000 if role == "solver" else 16384,
                    "extra_body": {"reasoning_effort": "high" if role == "solver" else "low"},
                },
            }
        }
        path = config.wb_repo() / "configs" / "models" / "harness2" / f"{role}.yaml"
        print(f"Configure WorkBuddy {role}: {model}", flush=True)
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)

            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def codex(*, dry_run=False):
    root = config.DEPENDENCIES / "codex"
    run(
        ["npm", "install", "--prefix", root, f"@openai/codex@{config.CODEX_VERSION}"],
        dry_run=dry_run,
    )
    if not dry_run:
        candidates = list((root / "node_modules" / "@openai").glob("**/bin/codex"))
        if len(candidates) != 1:
            raise RuntimeError(
                f"Expected one native Codex binary in {root}; found {len(candidates)}"
            )
        dest = Path(config.CODEX_DIST)
        platform = candidates[0].parent.parent
        shutil.copytree(platform, dest, dirs_exist_ok=True)
        _share_with_containers(dest)
        binary = dest / "codex"
        if binary.is_symlink():
            binary.unlink()
        binary.symlink_to("bin/codex")
    configure_codex(dry_run=dry_run)


def _share_with_containers(root: Path) -> None:
    """Make the staged dist readable by the container user it is bind-mounted for.

    The copy inherits the installing shell's umask, and under a private one (077) the
    WorkBuddy agent, which runs as a different user, cannot open /opt/codex at all.
    """
    for path in (root, *root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            continue
        extra = stat.S_IRGRP | stat.S_IROTH
        if stat.S_ISDIR(mode) or mode & stat.S_IXUSR:
            extra |= stat.S_IXGRP | stat.S_IXOTH
        path.chmod(mode | extra)


CODEX_CONFIG_REMEDY = "rerun harness2-setup <bench> --substrate cx"
CODEX_PROVIDER = "harness2"


def configure_codex(*, dry_run=False):
    config.require("HARNESS2_CODEX_BASE_URL", needed_by="Codex setup")
    body = (
        f"model = {json.dumps(config.SOLVER_MODEL_CX)}\n"
        f"model_provider = {json.dumps(CODEX_PROVIDER)}\n"
        f'[model_providers.{CODEX_PROVIDER}]\nname = "Configured Responses endpoint"\n'
        f"base_url = {json.dumps(config.CODEX_BASE_URL)}\n"
        'wire_api = "responses"\nenv_key = "VERTEX_PROXY_KEY"\n'
    )
    if not dry_run:
        config.CODEX_HOME_DIR.mkdir(parents=True, exist_ok=True)
        (config.CODEX_HOME_DIR / "config.toml").write_text(body, encoding="utf-8")


def codex_config_error() -> str | None:
    """Why `CODEX_HOME/config.toml` no longer matches the live Codex configuration.

    `configure_codex` bakes the endpoint and the model into a file that the Codex CLI
    reads at rollout time, and setup is the only thing that writes it. Nothing else
    re-reads `HARNESS2_CODEX_BASE_URL` / `HARNESS2_CODEX_MODEL` afterwards, so callers
    verify the derived file here before a rollout is paid for -- otherwise a changed
    endpoint is silently ignored and the run contacts the stale one. Returns None when
    the file agrees with the environment, else a message naming the remedy.
    """
    path = config.CODEX_HOME_DIR / "config.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return (
            f"CODEX_HOME has no readable config.toml: {path} -- the configured provider "
            f"block there routes Codex to your Responses endpoint; {CODEX_CONFIG_REMEDY}"
        )
    except tomllib.TOMLDecodeError as exc:
        return f"{path} is not valid TOML ({exc}); {CODEX_CONFIG_REMEDY}"
    provider = data.get("model_provider")
    block = (data.get("model_providers") or {}).get(provider) or {}
    stale = [
        f"{key} is {found!r}, configuration says {want!r}"
        for key, want, found in (
            ("base_url", config.CODEX_BASE_URL, block.get("base_url")),
            ("model", config.SOLVER_MODEL_CX, data.get("model")),
        )
        if found != want
    ]
    if stale:
        return f"{path} is stale ({'; '.join(stale)}); {CODEX_CONFIG_REMEDY}"
    return None


def download_jobbench(*, dry_run=False):
    """Install the JobBench task tree from its public Hugging Face dataset repo.

    The dataset is public, so an executor with web access may retrieve it;
    see `docs/BENCHMARKS.md`.
    """
    repo = config.jb_repo()
    dest = repo / "dataset" / "main"
    if (dest / ".harness2-download.json").is_file():
        return

    code = """from huggingface_hub import HfApi, snapshot_download
import json, sys
revision = HfApi().dataset_info("JobBench/job-bench").sha
snapshot_download("JobBench/job-bench", repo_type="dataset", revision=revision,
                  allow_patterns=["dataset/**"], local_dir=sys.argv[1])
open(sys.argv[1]+"/revision.json", "w").write(json.dumps({"revision": revision}))
"""
    if dry_run:
        print("Download JobBench/job-bench dataset/** from Hugging Face; record its revision")
        return
    if dest.exists() and any(dest.iterdir()):
        raise RuntimeError(
            f"{dest} already contains data without a download record; move it aside first"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=dest.parent) as tmp:
        run([repo / ".venv" / "bin" / "python", "-c", code, tmp])
        staged = Path(tmp) / "dataset"
        if not list(staged.glob("*/*/task_folder/TASK_INSTRUCTIONS.txt")):
            raise RuntimeError("JobBench download did not contain task instructions")
        if dest.exists():
            dest.rmdir()
        staged.rename(dest)
        shutil.copy2(Path(tmp) / "revision.json", dest / ".harness2-download.json")
