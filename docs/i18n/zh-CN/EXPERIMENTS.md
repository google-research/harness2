# 实验指南

开始实验前，请先完成[环境配置](SETUP.md)，并选好要跑的[基准测试](BENCHMARKS.md)。
下面会介绍递归预算、任务选择、可复现性和评测方式。

## 递归模式

无论选择哪种模式，都会先用同一套基础 Harness 运行 `k` 次，作为参考。

- **并行递归**会同时尝试 `k` 个通用候选方案和 `k` 个领域专用方案。接着进行 `k` 次
  独立组合，每次都能看到完整的候选池和执行结果，并据此生成一套改进后的 Harness。
- **顺序递归**会连续运行 `k` 轮“提出—探测—组合”。每一轮都会接着上一轮改好的
  Harness 继续，并带上之前积累的执行结果。

确定采用哪套结果后，反思阶段会把最终 Harness 和有执行证据支持的经验存进对应领域
的资料库。同一领域的任务必须按顺序运行，而且只能参考之前任务留下的内容；不同领域
之间可以并发。基准测试、执行器、模式、预算或随机种子只要不同，就会使用独立资料库。

| 选项 | 行为 |
|---|---|
| `--k 1` | 运行一轮“提出—探测—组合”，采用结果后再做反思 |
| `--k K --mode parallel` | 通用和领域两个范围各尝试 `K` 个候选方案，再生成 `K` 个组合结果 |
| `--k K --mode sequential` | 连续运行 `K` 轮，逐步改进 Harness |
| `--workers N` | 同时运行 `N` 个领域；同一领域里的任务仍按顺序执行 |
| `--substrate cx` | 使用 Codex，而不是默认的 OpenCode 执行器 |
| `--seed N` | 用固定种子打乱任务顺序；它不会影响模型采样 |

优化、评测和报告使用的基准测试、执行器、子集、`k`、模式和随机种子必须完全一致。
例如：

```bash
export HARNESS2_RUNS_DIR=runs/lab-sequential-k3
harness2 --bench lab --k 3 --mode sequential \
  --only-domains antitrust-competition --max-tasks 1
harness2-judge --bench lab --k 3 --mode sequential
harness2-report --bench lab --k 3 --mode sequential
```

如果调整了预算或模式，最好换一个输出根目录，避免不同实验的结果混在一起。

## 任务选择与成本

执行器 rollout 和改进阶段都会调用模型，因此会产生 API 费用。完整的 LAB 共有
1,143 个任务，建议先用少量任务试跑，确认费用后再决定是否跑完整数据集。

- `--only-domains`：只运行指定领域。
- `--max-tasks`：限制**每个领域**的任务数，不是整个实验的任务总数。
- `--dry-run`：只预览会选中哪些任务和领域，不调用模型。

每套基准测试也提供了一个一键脚本，会依次完成检查、优化、评测和报告。通过
`SUBSTRATE`、`K`、`MODE` 设置主要参数；WorkBuddy 还需要 `SUBSET`。其余参数会原样
传给优化命令：

```bash
bash scripts/run_lab.sh --only-domains antitrust-competition --max-tasks 1
SUBSTRATE=cx K=3 MODE=sequential bash scripts/run_jobbench.sh --max-tasks 1
SUBSET=web bash scripts/run_workbuddy.sh --max-tasks 1
```

## 可复现性

比较不同预算或模式时，任务范围、执行器、改进器、评测器和网络策略都要保持不变。
如果换了模型或任务范围，请使用新的 `HARNESS2_RUNS_DIR`。项目会保存每次运行的配置，
发现配置不兼容时会拒绝继续旧任务，避免混入错误结果。

JobBench 没有锁定到固定提交，而是在下载时确定数据集版本。setup 会把实际下载的
Hugging Face 版本写入
`dependencies/job-bench-eval/dataset/main/.harness2-download.json`。对外发布
JobBench 结果时，记得同时说明这个版本。

### 任务划分

下面的命令可以把已安装任务按领域分层，并生成可复现的数据划分：

```bash
python scripts/split_tasks.py --bench lab --seed 42 --output splits/lab.json
harness2 --bench lab --split-file splits/lab.json --split test --mode parallel
```

训练集默认占 50%。划分文件只保存在本地，不属于项目发布内容。为防止误覆盖，
目标文件已经存在时脚本会直接停止。

## 基线评测

运行 `harness2-judge --bases` 可以给同一实验下的参考执行打分。结果会写入
`judged.json` 旁边的 `base_judged.json`，方便与基础 Harness 比较。
`harness2-report` 默认只统计改进后的 Harness。

## 网络访问

三套基准测试的数据都是公开的。JobBench 甚至在 Hugging Face 上直接提供了每项任务的
`RUBRICS.json`，所以能访问网络的执行器有可能找到评分条件。我们在验证这个版本时就
遇到过：一次 Codex rollout 在没有明确指示的情况下，自行下载了对应任务的 rubric。

Harness² 本身不会限制网络。安装完成后，建议在正式评测期间屏蔽下面这些主机，避免
执行器接触任务数据或评分标准：

- `huggingface.co`、`hf.co` 和 `*.hf.co`；
- 基准测试仓库所在的 `github.com` 和 `raw.githubusercontent.com`。

注意，安装阶段仍然需要访问这些主机来下载依赖和数据。Codex 运行时会关闭沙箱，
因此应在执行器主机的网络层限制访问，比如配置出口防火墙或 DNS 策略，不要只依赖
工具设置。发布实验结果时，也请说明采用了哪些网络限制。

## 结果与断点续跑

默认输出目录分别是 `runs/lab/`、`runs/jb/` 和 `runs/wb/<subset>/`。任务摘要、执行
产物、最终采用的 Harness、反思记录和评分结果都会保存在这里。

中途退出后，重新执行同一条命令就能继续。评测失败的任务会标为未评分，不会直接按
0 分处理。如果更换模型或任务范围，请改用新的 `HARNESS2_RUNS_DIR`。

优化、评测和报告对应的命令分别是 `harness2`、`harness2-judge` 和
`harness2-report`，都可以通过 `--help` 查看完整参数。

---

[English version](../../EXPERIMENTS.md)
