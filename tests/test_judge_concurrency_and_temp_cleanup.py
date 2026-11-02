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

"""Tests for parallel judge_bases, concurrency env vars, and temp dir cleanup."""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks import jobbench
from harness2.cli import judge
from harness2.core.proposer import ChannelSpec, Proposer
from harness2.substrate.base import Install, RunRequest


class ParallelJudgeBasesTests(unittest.TestCase):
    def test_judge_bases_runs_parallel_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as td:
            ns_dir = Path(td)
            # Create two task directories with base entries
            for i in range(3):
                t_dir = ns_dir / f"task_{i}"
                t_dir.mkdir()
                run_dir = t_dir / "base0"
                run_dir.mkdir()
                (t_dir / "summary.json").write_text(
                    json.dumps(
                        {
                            "task_id": f"task_{i}",
                            "base": [{"label": "base0", "run_id": f"r_{i}", "run_dir": str(run_dir)}],
                        }
                    )
                )

            max_active = 0
            cur_active = 0
            lock = threading.Lock()

            class FakeAdapter:
                def score(self, run_dir: Path, task_id: str):
                    nonlocal max_active, cur_active
                    with lock:
                        cur_active += 1
                        if cur_active > max_active:
                            max_active = cur_active
                    try:
                        return {"n_passed": 1, "n_criteria": 1, "all_pass": True}
                    finally:
                        with lock:
                            cur_active -= 1

            adapter = FakeAdapter()
            rc = judge.judge_bases(adapter, ns_dir, parallel=3)
            self.assertEqual(rc, 0)

            out_file = ns_dir / "base_judged.json"
            self.assertTrue(out_file.is_file())
            rows = json.loads(out_file.read_text())
            self.assertEqual(len(rows), 3)
            self.assertEqual([r["task"] for r in rows], ["task_0", "task_1", "task_2"])
            self.assertEqual(rows[0]["score"], {"n_passed": 1, "n_criteria": 1, "all_pass": True})


class ConcurrencyConfigTests(unittest.TestCase):
    def test_env_int_defaults_and_overrides(self):
        with patch.dict(os.environ, {"HARNESS2_PROPOSER_CONCURRENCY": "7"}):
            self.assertEqual(config.env_int("HARNESS2_PROPOSER_CONCURRENCY", 2), 7)
        with patch.dict(os.environ, {"HARNESS2_JUDGE_PARALLEL": "16"}):
            self.assertEqual(config.env_int("HARNESS2_JUDGE_PARALLEL", 8), 16)
        with patch.dict(os.environ, {"HARNESS2_ROLLOUT_PARALLEL": "4"}):
            self.assertEqual(config.env_int("HARNESS2_ROLLOUT_PARALLEL", 2), 4)


class TempCleanupTests(unittest.TestCase):
    def test_proposer_transient_harvest_cleanup(self):
        spec = ChannelSpec(kind="opencode", model="dummy/model")
        prop = Proposer(spec, audit_dir=None)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            src = tmp / "context" / "harness"
            src.mkdir(parents=True)
            (src / "test.txt").write_text("hello")

            dest = prop._harvest(tmp, "context/harness", "label_1")
            self.assertIsNotNone(dest)
            self.assertTrue(dest.is_dir())
            harvest_parent = Path(prop._transient_dir.name)
            self.assertTrue(harvest_parent.is_dir())

            # Explicit cleanup removes the entire transient tree
            prop.cleanup()
            self.assertFalse(harvest_parent.exists())

    def test_jobbench_render_cleanup_on_success_and_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            class Substrate:
                name = "oc"
                def preflight(self):
                    pass

            class Stub:
                substrate = Substrate()
                store = root
                rendered = None

                def _launch_common(self, req):
                    return req.task_ref, req.run_ids[0].replace("/", "__"), int(req.timeout)

                def domain_of(self, task_id):
                    return task_id.split("/", 1)[0]

                def _run_runner(self, runner, env, *, wall, label):
                    self.rendered = Path(env["HARNESS_RENDER_DIR"])
                    if not self.rendered.is_dir():
                        raise AssertionError("render directory not created")
                    if "fail" in label:
                        raise RuntimeError("runner error")

                def _require_artifacts(self, task_id, label):
                    return root / "out"

            stub = Stub()
            install = Install(staging_dir=root / "staging", fingerprint="x")
            req_ok = RunRequest(task_ref="dom/task", run_ids=("ns/dom/task/base0",), timeout=5)

            with patch.object(jobbench, "render_opencode_tree", lambda *a, **k: None):
                jobbench.JobBenchAdapter._launch_opencode(stub, req_ok, install)
                self.assertIsNotNone(stub.rendered)
                self.assertFalse(stub.rendered.exists())

                req_fail = RunRequest(task_ref="dom/task", run_ids=("ns/dom/task/fail_base",), timeout=5)
                with self.assertRaises(RuntimeError):
                    jobbench.JobBenchAdapter._launch_opencode(stub, req_fail, install)
                self.assertIsNotNone(stub.rendered)
                self.assertFalse(stub.rendered.exists())


if __name__ == "__main__":
    unittest.main()
