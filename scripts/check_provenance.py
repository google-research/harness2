#!/usr/bin/env python3
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

"""Verify the release's runner and patch files against dependencies/ASSETS.json."""

import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    try:
        expected = json.loads((root / "dependencies/ASSETS.json").read_text())
        failures = []
        for name, digest in expected.items():
            path = root / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                failures.append(name)
    except (OSError, ValueError) as exc:
        print(f"Provenance check failed: {exc}")
        return 1
    if failures:
        print("Changed or missing release assets: " + ", ".join(failures))
        return 1
    print(f"PASS: {len(expected)} release asset checksums")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
