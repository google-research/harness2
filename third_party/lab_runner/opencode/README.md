# LAB OpenCode runner overlay

This runner uses LAB's tasks, system prompt, skill manuals, and document parsing
helpers with OpenCode's execution loop. The adapter supplies the model, installed
OpenCode path, Bun executable, harness tree, and results directory.

Harness² uses host document parsing and LAB's base tool surface. `render.py`
adds environment instructions for workspace paths and document access. Each run
records its configuration, metrics, transcript, raw OpenCode events, and output.

Use the [LAB setup and experiment guide](../../../../docs/BENCHMARKS.md#lab)
instead of invoking this internal runner directly. Its provenance is recorded in
[`../PROVENANCE.md`](../PROVENANCE.md).
