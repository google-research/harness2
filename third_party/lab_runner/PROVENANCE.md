# harvey-labs runner assets

These runners derive from the harvey-labs integration and retain its
[upstream license](LICENSE). See the [pinned revision and patches](../../../dependencies/PINNED.md).

The integration adds editable harness installation and configurable models,
paths, and endpoints. Local model-provider names, environment variables, and
log paths use the `harness2` namespace. Setup installs these runners into the LAB checkout as overlays.

[ASSETS.json](../../../dependencies/ASSETS.json) records the shipped file checksums.
Verify them with `python scripts/check_provenance.py`.

Repeated Codex run IDs clear the prior output directory and completion metrics before
starting the next attempt, so stale deliverables cannot become current-run evidence.
