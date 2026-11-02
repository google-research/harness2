# Codex tool-item protocol fixture

`codex_tool_items.jsonl` contains hand-authored, public-data-only protocol examples.
It is not a captured model trajectory and invokes no executor, MCP server or website.

The shapes come from the exact Codex version pinned by `harness2.config.CODEX_VERSION`:

- [Codex 0.144.6 exec event schema](https://github.com/openai/codex/blob/rust-v0.144.6/codex-rs/exec/src/exec_events.rs)
- [Actual JSONL event producer](https://github.com/openai/codex/blob/rust-v0.144.6/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
- [Producer MCP serialization test](https://github.com/openai/codex/blob/rust-v0.144.6/codex-rs/exec/src/event_processor_with_jsonl_output_tests.rs)
- [Web search action schema](https://github.com/openai/codex/blob/rust-v0.144.6/codex-rs/protocol/src/models.rs)

The MCP completion example follows the producer test's `search service.web_run`
request and `search result` response. The remaining examples exercise the schema's
file changes, tool failures, collaboration states and web-search action variants.
Shell and assistant events are unchanged behavior controls. A started/completed
MCP pair checks the existing last-state-per-item behavior.
