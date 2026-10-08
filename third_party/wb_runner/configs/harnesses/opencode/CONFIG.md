# OpenCode container harness

Version 1.14.18 uses `presets/settings/v1_14.json`. The base and adaptive
harnesses share the same binary and tool policy. The adaptive agent additionally
installs the candidate harness files. `docker/Dockerfile` builds the mounted CLI
and ripgrep; the setup workflow builds this image before a trial runs.
