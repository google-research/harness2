# workbuddy-bench runner assets

These runners derive from the workbuddy-bench integration and retain its
[upstream license](LICENSE). See the [pinned revision and patches](../../../dependencies/PINNED.md).

The integration adds editable harness installation and configurable models,
paths, and endpoints. Local model-provider names, environment variables, and
log paths use the `harness2` namespace. Setup installs these agents and harness configurations into the WorkBuddy checkout.

The policy integration preserves base execution behavior, injects literal deployer
policy after rendering or workspace installation, and verifies the final input.
Setup also copies the first-party `harness2/core/policy.py` module beside these
runners as `pinned_policy.py`; it is not an upstream-derived asset.

[ASSETS.json](../../../dependencies/ASSETS.json) records the shipped file checksums.
Verify them with `python scripts/check_provenance.py`.
