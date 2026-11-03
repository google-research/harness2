# SPDX-License-Identifier: Apache-2.0
"""The pinned WorkBuddy config loader reads generated jobs with yaml.safe_load."""

import unittest
from pathlib import Path
from unittest.mock import patch

from harness2 import config
from harness2.benchmarks.workbuddy import SUBSETS, _job_yaml

try:
    import yaml
except ImportError:
    yaml = None


@unittest.skipIf(yaml is None, "Requires WorkBuddy's PyYAML config parser")
class WorkbuddyYamlPathTests(unittest.TestCase):
    def test_generated_jobs_preserve_output_and_mount_paths(self):
        for name in (
            "ordinary",
            "campaign #1",
            "campaign: result",
            'campaign "quoted"',
            "campaign\nresult",
        ):
            for substrate in ("oc", "cx"):
                with self.subTest(path=name, substrate=substrate):
                    root = Path("/tmp") / name
                    with patch.object(config, "CODEX_DIST", root / "codex"):
                        body = _job_yaml(
                            SUBSETS["code"],
                            substrate,
                            "fixture",
                            ["task-1"],
                            None,
                            1,
                            root / "runs",
                        )
                    parsed = yaml.safe_load(body)
                    self.assertEqual(parsed["jobs_dir"], str(root / "runs"))
                    if substrate == "cx":
                        self.assertEqual(
                            parsed["environment_override"]["mounts"][0]["source"],
                            str(root / "codex"),
                        )
