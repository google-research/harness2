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

# Run JobBench end to end: check, adapt, judge, report.
#   bash scripts/run_jobbench.sh                                          # every task
#   bash scripts/run_jobbench.sh --only-domains biostatisticians --max-tasks 1
# Settings: SUBSTRATE=oc|cx  K=1  MODE=parallel|sequential
# Extra arguments go to the adaptation command; --dry-run stops after it.
set -uo pipefail
cd "$(dirname "$0")/.."
PATH="$PWD/.venv/bin:$PATH"
SUBSTRATE=${SUBSTRATE:-oc}
K=${K:-1}
MODE=${MODE:-parallel}
run=(--bench jb --substrate "$SUBSTRATE" --k "$K" --mode "$MODE")

# Keep refinement, judging and reporting in the same seeded namespace.
seed_value=""
has_seed=0
expect_seed=0
for arg in "$@"; do
  if ((expect_seed)); then
    seed_value=$arg; has_seed=1; expect_seed=0
    continue
  fi
  case "$arg" in
    --seed) expect_seed=1 ;;
    --seed=*) seed_value=${arg#--seed=}; has_seed=1 ;;
  esac
done
if ((has_seed)); then run+=(--seed "$seed_value"); fi

harness2-check --bench jb --substrate "$SUBSTRATE" || exit
harness2 "${run[@]}" "$@"
status=$?
[[ " $* " == *" --dry-run "* ]] && exit $status
harness2-judge "${run[@]}"
judged=$?
harness2-report "${run[@]}"
exit $((status ? status : judged))
