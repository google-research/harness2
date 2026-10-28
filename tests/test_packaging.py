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

"""The source distribution must carry the tracked trees its own release check hashes.

`scripts/check_provenance.py` ships inside the sdist and verifies every path listed in
`dependencies/ASSETS.json`, so the archive has to contain that manifest and every file it
names. The regression pinned here is a mistyped root: `recursive-include
harness2/third_party *` named a path that has never existed, so setuptools silently
shipped no `third_party/` files and the sdist failed its own provenance check.

Only `recursive-include` roots are asserted to exist. A mistyped file in `include` is
ignored by setuptools with no diagnostic, and the one such entry today (`.env.example`)
arrives with a separate contribution, so asserting it would fail on a file this module
does not own.
"""

from __future__ import annotations

import fnmatch
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.in"
ASSETS = ROOT / "dependencies" / "ASSETS.json"


def directives() -> list[tuple[str, list[str]]]:
    """(directive, tokens) for every non-comment line of MANIFEST.in."""
    out: list[tuple[str, list[str]]] = []
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        out.append((parts[0], parts[1:]))
    return out


def shipped(rel: str) -> bool:
    """True when setuptools would put `rel` in the sdist, per MANIFEST.in's include rules.

    `recursive-include <dir> <pattern...>` matches patterns against the file's name, the
    same way setuptools does, so a bare `*` selects the whole tree below `<dir>`.
    """
    rel = rel.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    for directive, tokens in directives():
        if directive == "include" and rel in tokens:
            return True
        if directive == "recursive-include":
            root, patterns = tokens[0].rstrip("/"), tokens[1:]
            if rel.startswith(root + "/") and any(fnmatch.fnmatch(name, p) for p in patterns):
                return True
    return False


class PackagingTests(unittest.TestCase):
    def test_recursive_include_roots_exist(self) -> None:
        roots = [tokens[0] for directive, tokens in directives() if directive == "recursive-include"]
        self.assertTrue(roots, "MANIFEST.in declares no recursive-include roots")
        absent = [root for root in roots if not (ROOT / root).exists()]
        self.assertEqual(
            [],
            absent,
            f"MANIFEST.in names recursive-include roots that do not exist: {absent}; "
            f"setuptools drops them without a diagnostic",
        )

    def test_every_hashed_release_asset_is_shipped(self) -> None:
        assets = json.loads(ASSETS.read_text(encoding="utf-8"))
        self.assertTrue(assets, "dependencies/ASSETS.json lists no release asset")
        absent = sorted(rel for rel in assets if not (ROOT / rel).is_file())
        self.assertEqual(
            [], absent, f"dependencies/ASSETS.json hashes files absent from the tree: {absent[:5]}"
        )
        omitted = sorted(rel for rel in assets if not shipped(rel))
        self.assertEqual(
            [],
            omitted,
            "the sdist omits files that dependencies/ASSETS.json hashes, so "
            f"scripts/check_provenance.py cannot pass inside it: {omitted[:5]}",
        )

    def test_provenance_manifest_is_shipped(self) -> None:
        self.assertTrue(
            shipped("dependencies/ASSETS.json"),
            "MANIFEST.in does not include dependencies/ASSETS.json; the sdist then cannot "
            "run scripts/check_provenance.py at all",
        )


if __name__ == "__main__":
    unittest.main()
