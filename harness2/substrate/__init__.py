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

"""The solver substrates behind one interface (substrate/base.py).

`OpencodeSubstrate` carries OPENCODE_SURFACE and three install modes; `CodexSubstrate`,
`ClaudeSubstrate` and `PiSubstrate` carry one each. Benchmark adapters supply the launcher
body.
"""

from .base import Install, Launcher, RunRequest, Substrate
from .claude import CLAUDE_SURFACE, ClaudeSubstrate
from .codex import CODEX_SURFACE, CodexSubstrate
from .opencode import OPENCODE_SURFACE, OpencodeSubstrate
from .pi import PI_SURFACE, PiSubstrate

__all__ = [
    "Install",
    "Launcher",
    "RunRequest",
    "Substrate",
    "OpencodeSubstrate",
    "OPENCODE_SURFACE",
    "CodexSubstrate",
    "CODEX_SURFACE",
    "ClaudeSubstrate",
    "CLAUDE_SURFACE",
    "PiSubstrate",
    "PI_SURFACE",
]
