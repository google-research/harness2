#!/usr/bin/env bash
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

# Run WorkBuddy-Bench end to end: check, adapt, judge, report.
#   bash scripts/run_workbuddy.sh                                          # every task
#   bash scripts/run_workbuddy.sh --only-domains doc-ops --max-tasks 1
# Settings: SUBSTRATE=oc|cx  SUBSET=office|web|code  K=1  MODE=parallel|sequential
# Extra arguments go to the adaptation command; --dry-run stops after it.
set -uo pipefail
cd "$(dirname "$0")/.."
PATH="$PWD/.venv/bin:$PATH"
SUBSTRATE=${SUBSTRATE:-oc}
SUBSET=${SUBSET:-office}
K=${K:-1}
MODE=${MODE:-parallel}
run=(--bench wb --subset "$SUBSET" --substrate "$SUBSTRATE" --k "$K" --mode "$MODE")

harness2-check --bench wb --subset "$SUBSET" --substrate "$SUBSTRATE" || exit
harness2 "${run[@]}" "$@"
status=$?
[[ " $* " == *" --dry-run "* ]] && exit $status
harness2-judge "${run[@]}"
judged=$?
harness2-report "${run[@]}"
exit $((status ? status : judged))
