# Pinned deployer policy

Set `BenchSpec.pinned_policy` through the Python adapter API before starting a run:

```python
from dataclasses import replace
from pathlib import Path

adapter.spec = replace(
    adapter.spec,
    pinned_policy=Path("/trusted/config/policy.md").read_text(encoding="utf-8"),
)
```

Keep the policy source outside the editable harness directory. This is an API setting;
there is no additional CLI flag. Empty or whitespace-only text disables the feature.
Leading and trailing whitespace is stripped once when constructing the policy block;
internal text is preserved literally, including template syntax and HTML comments.

## Delivery contract

1. Materialisation stages the editable tree without inserting the policy into its files
   or templates. `Install.extras["pinned_policy"]` carries the trusted policy separately.
2. Each benchmark renders its editable content using its existing rules.
3. The launcher prepends the literal policy block after the final rendering or workspace
   installation step, then verifies the execution input before starting the model.

| Execution path | Final policy delivery |
|---|---|
| JobBench / OpenCode | After template interpolation and comment removal; the copied workspace `AGENTS.md` is checked before each attempt. |
| LAB / OpenCode | After the base or candidate renderer; the final `AGENTS.md` or primary agent prompt is read back. |
| WorkBuddy / OpenCode | After Harbor renders the Jinja instruction template; the resulting string is passed directly to the CLI. |
| Codex | After installing the workspace harness; the final `AGENTS.md` is read back before execution. |

LAB and WorkBuddy base runs retain their original base rendering and component selection.
They receive the same policy as candidates. A WorkBuddy Codex base run prepends to any
`AGENTS.md` already supplied by the task image instead of replacing its instructions.
The local deterministic demo does not execute an LLM prompt and is not a policy-enforcement
integration; its scheduler can still exercise policy cache invalidation.

## Limits and caching

The policy block consumes the editable always-on budget (24,000 characters for OpenCode,
8,000 for Codex). Direct materialisation and final delivery both check the applicable
budget. Codex also checks the 32,768-byte UTF-8 limit. In WorkBuddy's composed instruction,
the task instruction itself is excluded from the always-on character budget. Oversized
content fails before model execution rather than relying on runtime truncation.

Run markers include the full SHA-256 policy identity. Task summaries record and compare
that identity before resuming, and summaries from different policies cannot merge their
mode results. Adding, changing, or removing a policy invalidates previous execution and
whole-task results. With no policy, legacy execution fingerprints and summary resume
behavior remain compatible.

## Installing updated runners

LAB and WorkBuddy use isolated Python environments. Setup copies the standard-library-only
`harness2/core/policy.py` beside their runner overlays so all paths use the same code.
After updating Harness², rerun the relevant setup command (`harness2-setup lab` or
`harness2-setup wb`, with the selected substrate). Configured-policy launches reject
missing or outdated runner overlays and explain which setup command to run.

For a custom substrate or launcher, carrying the setting in an `Install` is insufficient:
implement the same final-input injection and verification contract. Keep the expected
policy in trusted launcher configuration, not in a candidate-controlled file.

This protects policy text against editing and delivery-time transformations. It does not
make the model obey that text or prevent a running hook, plugin, or agent from modifying
its environment. Tool restrictions and runtime isolation require separate enforcement.
