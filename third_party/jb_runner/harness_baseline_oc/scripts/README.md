<!-- c7 (helper-scripts-via-Bash) — deterministic Python helpers (placeholder).
     The evolve loop may add scripts here, e.g. scripts/validate_workbook.py. render_opencode.py
     copies this dir into the temp workspace as ./scripts/, and a skill/guardrail instructs the
     agent to run them via Bash (e.g. `python scripts/validate_workbook.py output/Foo.xlsx`).
     This is how we get c7's deterministic-fix value without an OpenCode TS plugin.
     Each script must be stdlib-or-pip importable and runnable as `python scripts/<name>.py`.
     None defined in the v0 baseline. -->
