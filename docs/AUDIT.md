## Adversarial audit (no code changes — discussion)

**Method:** 86 agents across 5 phases (map, deep-dive on 8 dimensions, adversarial verify with 3 independent voters per finding, completeness critic, 4 follow-up investigations).

**Result:** 55 findings; 22 bug/security findings put through verification; **16 confirmed** (all with 3/3 votes).

All 70 tests in `tests/` pass (`python3 -m unittest discover -s tests` → OK). Five of the confirmed bugs have standalone fixes already submitted as PRs.

### PRs submitted (quick wins, each with tests green)

| PR | Fix | Severity |
|---|---|---|
| #1 `fix/packaging-manifest` | `MANIFEST.in` points at `harness2/third_party` (does not exist); sdist ships 0 runners → clean `pip install` always hits `missing_runners 7/7` | **high** |
| #2 `fix/recursion-preserve-diagnostic` | `err = str(exc)` on attempt 2 overwrites the attempt-1 validation diagnostic | medium |
| #3 `fix/selection-dropped-labels` | Dropped-list cleanup compares suffixed labels against pure `pool` keys → never matches | low |
| #4 `fix/schedule-roll-bases-missing-dir` | `_roll_bases._one()` returns `ok=True` even when the run_dir does not exist | medium |
| #5 `fix/lab-torn-scores` | Torn `scores.json` bricks the cell as `ungraded` forever (JB re-judges; LAB didn't) | high |

### Confirmed findings with no PR yet (for discussion)

| Sev | Finding | File |
|---|---|---|
| high | Tripwire `GRADER_VERDICT_MARKERS` is only 7 literal strings; error includes a slice of the verdict and `stage.error` propagates as `decisions[{label}]` → cascade failure | `harness2/core/context.py:419-427`, `recursion.py:436,565,518-520` |
| high | Thread-unsafe core under `workers>1`: `Library._counter`/`ledger`/`runmark`/`workdir` shared across 8 rollout threads; `flock` only on `summary.json` | `schedule.py:241-250`, `library.py:50-110`, `recursion.py:408-430`, `judge.py:69-115` |
| high | Zero cost accounting: `tokens_in`/`tokens_out`/`usd`/`stage_time_seconds` rendered by `report.py` but never populated by `write_sweep_summary` | `schedule.py:341-455`, `report.py:102-116` |
| high | WB label sanitisation collides namespaces and leaks trials between rollouts (`tid[:32]` truncation) | `workbuddy.py` |
| medium | Proposer `claude_cli` runs `--dangerously-skip-permissions` with no filesystem fence (can read `.env`, `~/.config`, `~/.ssh`) | `harness2/core/proposer.py:207` |
| medium | `VERTEX_PROXY_KEY` persisted in generated YAML (WB) and passed via env (JB/LAB) | `workbuddy.py:532`, `jobbench.py:834`, `lab.py:740` |
| medium | WB dead rollout with partial delivery scored as real zero, not `ungraded`; `_killed` under-detects on torn JSON | `workbuddy.py score()` |
| medium | `materialise` diverges: OpenCode honours `file:<rel>`; Codex ignores it and copies the rest to `extras` | `harness2/substrate/{opencode,codex}.py` |
| low | Bridge prints the generated key to stdout; can bind `0.0.0.0` with no TLS/rate limit | `harness2/cli/bridge.py:88-94` |
| low | No symlink checks in tree read/write/harvest (`rglob`/`copytree` dereference) | `harness2/core/tree.py` |
| low | Proposer audits persist `mission`/`response`/`meta` without redaction | `harness2/core/proposer.py _write_audit` |
| low | `setup --dry-run` without runners exits 0 as "Setup plan complete" (masks broken copy); `bun@1.3.11` hardcoded; no check of `node>=20`/gcloud/pandoc | `harness2/cli/setup.py` |
| low | `wb_render.py` (577 lines) has 0 tests; `prompts.py` (1163 lines) has 0 direct tests | `tests/` |

### Notes

- R=4k/P=3k budget invariants, stale-state defences (`runmark` + `inputs_fingerprint` + `artifacts_intact`), and parallel/sequential modes are sound and well-tested offline.
- Paper claims (11.5 / 4.5 / 11.8 point gains on WorkBuddy / JobBench / LAB) have no artefacts, configs or logs in the repo to reproduce them; worth a follow-up if reproducibility is in scope.
- All fixes in PRs #1–#5 were verified against the 70-test suite (green).

---
*Adversarial audit: 86 agents, 8 dimensions, 22 findings verified with 3 independent voters each, 16 confirmed.*
