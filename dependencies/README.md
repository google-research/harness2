# Third-party code and data

Benchmark tools are downloaded into this directory and excluded from version
control. Pins are defined in `harness2/config.py`; setup commands are in the
[benchmark guides](../docs/BENCHMARKS.md).

| Upstream | License | Release integration |
|---|---|---|
| [Job-Bench/job-bench-eval](https://github.com/Job-Bench/job-bench-eval) | Apache 2.0 | Derived Codex shell runner, baseline harnesses, and judge patch |
| [harveyai/harvey-labs](https://github.com/harveyai/harvey-labs) | MIT | Derived LAB runners and Vertex judge patch |
| [Tencent/workbuddy-bench](https://github.com/Tencent/workbuddy-bench) | Custom Tencent license | Derived Harbor agents, harness configs, and runtime patch |
| [anomalyco/opencode](https://github.com/anomalyco/opencode) | MIT | Runtime dependency and compatibility patch |

The WorkBuddy license includes a territorial limitation concerning use within
the European Union. The derived runners, the patches, and the upstream license
notices that cover them are not included in this copy; see
[`harness2/third_party/`](../harness2/third_party/README.md).

Dataset licenses are separate from software licenses. Data is downloaded from
its upstream distribution and is not redistributed in Harness². Generated
model configuration, cloud credentials, local task partitions, and run artifacts
are not release assets.

See [pins and patch purposes](PINNED.md).
