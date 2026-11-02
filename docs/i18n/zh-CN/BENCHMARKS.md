# 基准测试指南

开始前，请先完成[环境和凭据配置](SETUP.md)。三套基准测试彼此独立，按需安装即可。
下面的命令都可以加上 `--substrate cx`，改用 [Codex 执行器](SETUP.md#codex-执行器)，
默认则是 OpenCode。递归模式用 `--mode parallel` 或 `--mode sequential` 选择，
任务级递归预算用 `--k` 设置。运行任务、评测和生成报告时，这两个参数要保持一致。

## LAB

上游项目：[harveyai/harvey-labs](https://github.com/harveyai/harvey-labs)。
这是一组法律工作任务，每项任务都带有源文档，并按 rubric 评分。适配器会按业务领域
整理任务，同时确保改进器看不到评分条件。运行 LAB 需要 Pandoc 和 Poppler。

```bash
harness2-setup lab
harness2-check --bench lab
harness2 --bench lab --k 1 --mode parallel
harness2-judge --bench lab --k 1 --mode parallel
harness2-report --bench lab --k 1 --mode parallel
```

如果要跑顺序递归，把这些命令里的模式改成 `--mode sequential` 即可。

任务随锁定版本的上游仓库一起下载，位于
`dependencies/harvey-labs/tasks/<practice-area>/<task>/task.json`。适配器会自动发现
已经安装的任务，不用再准备任务列表。运行 setup 时，还会安装 LAB 的依赖环境、
运行器覆盖文件和项目自带的 Vertex 评测器补丁。

评测器会等任务选择结束后再读取输出。报告包含两个指标：Partial 表示通过了多少比例
的评分条件，All-pass 表示是否全部通过。某项任务评测失败时，不会直接记成 0 分，而是
标为未评分；详细输出保存在运行目录的 `judge.log`。如果日志里出现 Vertex
`429 RESOURCE_EXHAUSTED`，通常只是配额不足，稍后再运行 `harness2-judge` 即可。
更换评测模型会影响分数。所有结果都在 `runs/lab/` 下。

## JobBench

上游项目：[Job-Bench/job-bench-eval](https://github.com/Job-Bench/job-bench-eval)。
数据：[JobBench/job-bench](https://huggingface.co/datasets/JobBench/job-bench)。
这套基准测试包含多种专业工作任务，并按数据集中的职业目录分组。

```bash
harness2-setup jb
harness2-check --bench jb
harness2 --bench jb --k 1 --mode parallel
harness2-judge --bench jb --k 1 --mode parallel
harness2-report --bench jb --k 1 --mode parallel
```

如果要跑顺序递归，把这些命令里的模式改成 `--mode sequential` 即可。

setup 只会从 Hugging Face 下载主数据集。数据完全公开，不需要账号、Token，也不用
接受额外条款。下载完成后，版本信息会被记录下来，任务则放在
`dependencies/job-bench-eval/dataset/main/`。setup 还会安装文档解析依赖，以及处理
推理强度和空补全结果的评测器补丁。改进器拿到的上下文中不会包含评分标准。

如果 `HARNESS2_JUDGE_MODEL` 配的是 Gemini，评测器会走项目提供的 Vertex OpenAI
兼容端点，并使用应用默认凭据。要换成其他提供商，请设置
`HARNESS2_JUDGE_BASE_URL`、`HARNESS2_JUDGE_API_KEY`，以及该端点支持的模型 ID。
最终分数沿用上游 rubric 的权重，未评分任务会单独列出。结果保存在 `runs/jb/`。

注意：JobBench 的数据（包括 `RUBRICS.json`）在 Hugging Face 上是公开的，能访问
网络的执行器可能直接拿到这些内容。正式运行时，请按照[网络访问](EXPERIMENTS.md#网络访问)
一节的说明屏蔽相关主机。

## WorkBuddy-Bench

上游项目：[Tencent/workbuddy-bench](https://github.com/Tencent/workbuddy-bench)。
数据：[tencent/workbuddy-bench](https://huggingface.co/datasets/tencent/workbuddy-bench)。
它分为办公、网页和代码三个子集，运行时需要 Docker。

上游采用腾讯自定义许可证，其中包含地域限制。下载或使用前请先阅读
[许可证](https://github.com/Tencent/workbuddy-bench/blob/main/LICENSE)。本项目基于它
修改的运行器文件和补丁也沿用这份许可证。

```bash
docker info
harness2-setup wb --subset office
harness2-check --bench wb --subset office
harness2 --bench wb --subset office --k 1 --mode parallel
harness2-judge --bench wb --subset office --k 1 --mode parallel
harness2-report --bench wb --subset office --k 1 --mode parallel
```

如果要跑顺序递归，把这些命令里的模式改成 `--mode sequential` 即可。

直接运行 `harness2-setup wb` 会一次装好三个子集。如果只打算跑其中一个，安装时就加上
`--subset`。安装完成后，其他命令每次只能指定一个子集。

WorkBuddy 的 setup 步骤比较多：它会安装锁定版本的 Harbor 运行时，应用项目自带的
适配器、挂载及凭据/传输补丁，复制 Harness 运行器，根据 `.env` 创建求解器和评测器
路由，调用上游脚本下载所选子集，最后构建 OpenCode Harness 镜像。

公开的 Vertex 配置要求执行器使用 `google-vertex/gemini...`，评测器使用不带前缀的
`gemini...` ID，不需要再从其他仓库复制模型 YAML 或账号专属代理配置。如果
`HARNESS2_SOLVER_MODEL`、`HARNESS2_JUDGE_MODEL` 或 `GOOGLE_CLOUD_PROJECT` 不符合
这些要求，setup 会直接停止。使用 `--dry-run` 时仍会提示问题，但会继续打印剩余的
安装计划。任务容器里的本地代理会自动刷新 ADC Token，并保留 Gemini 工具调用的
thought signature。

setup 还会调整上游下载脚本，依次尝试 `curl`、`wget` 和 `hf`，并用数据集仓库里的
`SHA256SUMS` 做强制校验。校验不通过就不会解压；成功时会看到
`wb-bench-<subset>-v1.0.tar.gz: OK`。

Harness² 会从
`dependencies/workbuddy-bench/datasets/wb-bench-<subset>-v1.0/tasks/` 里的
`task.toml` 和 `instruction.md` 发现任务。代码子集按 `metadata.role` 建库，办公和网页
子集按 `metadata.category` 建库。每次 rollout 都会启动一个全新的基准测试容器。
适配器会读取验证器生成的 `score.json`，但不会把验证器输出交给改进器。没有验证结果
的任务会单独标为未评分，不会算作 0 分。结果保存在 `runs/wb/<subset>/`。

使用 Codex 时，要用 `harness2-bridge --host 0.0.0.0` 启动桥接服务，否则容器无法访问。

---

[English version](../../BENCHMARKS.md)
