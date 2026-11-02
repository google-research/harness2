<h3 align="center">
  <img src="../../../assets/Harness2-wordmark.png" alt="Harness²" width="360"><br>
  面向开放世界的递归式智能体 Harness 优化
</h3>

<p align="center">
  <a href="../../../README.md">English</a> | 简体中文
</p>

<p align="center">
  <a href="https://arxiv.org/pdf/2610.14224"><img src="https://img.shields.io/badge/论文-arXiv-EA4335?logo=arxiv&logoColor=white" alt="论文"></a>
  <a href="https://harness2.github.io"><img src="https://img.shields.io/badge/网站-harness2.github.io-4285F4?logo=googlechrome&logoColor=white" alt="网站"></a>
  <a href="BENCHMARKS.md"><img src="https://img.shields.io/badge/文档-基准测试指南-FBBC04" alt="基准测试指南"></a>
  <a href="../../../LICENSE"><img src="https://img.shields.io/badge/许可证-Apache%202.0-34A853" alt="许可证"></a>
</p>

<p align="center">
  <a href="BENCHMARKS.md#jobbench">
    <img src="../../../assets/harness2-setup.gif" alt="安装、配置并运行 JobBench：uv pip install -e '.[render]'、harness2-setup jb，然后运行 harness2 --bench jb --k 1 --mode parallel。点击查看前置要求和命令。" width="640">
  </a>
</p>

**Harness（智能体运行框架）**，就是模型之外那套让它能够真正作为智能体工作的系统，
包括提示词、工具、记忆、技能和编排方式等。**Harnessing** 指的是不断调整、优化这套系统。

Harness² 是一个即插即用的**递归式智能体 Harness 优化**框架，主要解决开放环境里的
新任务：这些任务在设计 Harness 时并没有出现过。它不改模型权重，而是把两层递归串在一起：

* **任务级递归**采用 **提出—探测—组合（Propose–Probe–Compose）**：先提出几种改法，
  再放到同一个任务上实际运行，最后对比运行结果，组合出更合适的 Harness。整个过程
  不需要标准答案参与反馈。
* **Harnessing 级递归**会记住每个任务最终采用的 Harness，以及有执行证据支持的经验，
  并把它们留给后续任务使用。

<p align="center">
  <a href="../../../assets/harness2_pipeline.png">
    <img src="../../../assets/harness2_pipeline.png" alt="Harness² 流程。任务级递归：智能体执行器运行初始 Harness，Harness 编辑器提出通用修改和领域修改，候选探测执行这些修改，Harness 组合器将它们合并为最终 Harness。Harnessing 级递归：经验反思器总结该步骤，并更新供下一任务使用的改进 Harness。" width="100%">
  </a>
</p>

我们在三个专业基准测试、四种模型与 Harness 组合上做了验证。只运行一步 Harness²，
GLM-5 和 Gemini 3.5 Flash 就能在各自基础 Harness 上继续提升：WorkBuddy-Bench
最高提升 11.5 分，JobBench 最高提升 4.5 分，LAB 的部分通过率最高提升 11.8 个百分点。
继续增加任务级递归预算，效果还能进一步提高。

目前支持**并行递归和顺序递归**、**OpenCode 和 Codex 执行器**，以及
**JobBench、LAB、WorkBuddy-Bench** 三套基准测试。

## 视频概览

https://github.com/user-attachments/assets/76b10548-b14b-4c8f-9aa5-a93662df4d56

## 目录

- [视频概览](#视频概览)
- [Harness 组件](#harness-组件)
- [安装](#安装)
- [运行基准测试](#运行基准测试)
- [实验](#实验)
- [网络访问](#网络访问)
- [结果与断点续跑](#结果与断点续跑)
- [引用](#引用)
- [贡献](#贡献)
- [许可证](#许可证)
- [免责声明](#免责声明)

## Harness 组件

Harness² 把模型周围的八类组件都开放出来，方便直接修改：**系统提示词、防护规则、
记忆、工具指南、子智能体、技能、脚本和插件**。它们共同决定如何组织上下文、怎样
使用工具、如何分派任务，以及智能体在工具循环里怎么行动。

<p align="center">
  <a href="../../../assets/components.png">
    <img src="../../../assets/components.png" alt="按运行时角色划分的八个 Harness 组件：系统提示词、防护规则、记忆和技能负责构建上下文；工具指南和脚本定义工具接口；子智能体编排委派工作；插件控制中间件。" width="100%">
  </a>
</p>

## 安装

推荐使用 **Linux、Python 3.12 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)**。
在仓库根目录运行：

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e '.[render]'
harness2-demo --output runs/demo --k 2
```

本地演示会跑一遍两种递归模式，使用的是结果固定的编辑器和真实 Python 执行，
不需要配置模型凭据。

如果要跑模型驱动的基准测试，还需要安装 **Node.js 20+、Google Cloud CLI** 和对应的
[基准测试依赖](SETUP.md#要求)。其中 LAB 需要 Pandoc 和 Poppler，WorkBuddy-Bench
需要 Docker。接着配置模型：

```bash
cp .env.example .env
# 在 .env 中填写 Vertex AI 项目和模型 ID。
gcloud auth application-default login
```

模型设置、其他登录方式，以及 [Codex 执行器](SETUP.md#codex-执行器)的配置方法，
都可以在[配置指南](SETUP.md)里找到。

## 运行基准测试

下面以 LAB 为例：先安装依赖，跑一个任务，再完成评测并生成报告。调用模型会产生
API 费用，第一次运行建议先从少量任务开始。

```bash
harness2-setup lab
harness2-check --bench lab
harness2 --bench lab --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1
harness2-judge --bench lab --k 1 --mode parallel
harness2-report --bench lab --k 1 --mode parallel
```

要改用顺序递归，只需把模式换成 `--mode sequential`。`--k` 表示任务级递归预算，
详见[实验](#实验)。运行优化、评测和报告时，基准测试、执行器、子集、`--k`、
`--mode` 和随机种子必须保持一致。想先确认会选中哪些任务，可以给 `harness2`
加上 `--dry-run`，这样不会调用模型。

| 基准测试 | 任务 | 安装 | 指南 |
|---|---|---|---|
| JobBench | 专业工作 | `harness2-setup jb` | [JobBench](BENCHMARKS.md#jobbench) |
| LAB | 法律工作 | `harness2-setup lab` | [LAB](BENCHMARKS.md#lab) |
| WorkBuddy-Bench | 办公、网页和代码 | `harness2-setup wb` | [WorkBuddy](BENCHMARKS.md#workbuddy-bench) |

每次只能运行一套基准测试。WorkBuddy 安装完成后，每条命令还要指定
`--subset office`、`--subset web` 或 `--subset code`。数据来源、任务领域和评分方式
可查看[基准测试指南](BENCHMARKS.md)。

## 实验

任务级递归有两种扩展方式：**并行递归**会同时尝试多套候选 Harness，再根据实际运行
结果组合改进方案；**顺序递归**则连续执行多轮“提出—探测—组合”，每一轮都会继承
上一轮改好的 Harness 和已经积累的证据。

<p align="center">
  <a href="../../../assets/harness2_recursion_modes.png">
    <img src="../../../assets/harness2_recursion_modes.png" alt="扩展任务级递归的两种方式。并行：探测 k 个通用候选和 k 个领域候选 Harness，再基于共享证据生成 k 个组合结果。顺序：在 k 个“提出—探测—组合”步骤中传递改进后的 Harness 和累积证据。" width="100%">
  </a>
</p>
<p align="center"><em>并行递归拓宽探索范围，顺序递归加深优化过程。</em></p>

| 选项 | 行为 |
|---|---|
| `--k 1` | 执行一个“提出—探测—组合”步骤 |
| `--k K --mode parallel` | 探测 `K` 个通用候选和 `K` 个领域候选，然后生成 `K` 个组合结果 |
| `--k K --mode sequential` | 通过连续 `K` 个步骤改进 Harness |
| `--substrate cx` | 使用 Codex，而不是默认的 OpenCode 执行器 |

每个任务结束后，Harnessing 级递归都会留下最终采用的 Harness 和经过执行验证的经验，
供后续任务参考。任务怎么选、如何并发、怎样保证可复现，以及如何和基线比较，详见
[实验指南](EXPERIMENTS.md)。

## 网络访问

执行器默认可以自由访问网络，Codex 运行时也关闭了沙箱。由于部分基准测试的答案或
评分标准可以在网上找到，正式评测前请先配置好[网络限制](EXPERIMENTS.md#网络访问)，
避免执行器接触这些内容。

## 结果与断点续跑

结果统一保存在 `runs/` 下，并按基准测试和子集（如果有）分类。目录里会包含任务摘要、
交付文件、最终采用的 Harness、反思记录和评测结果。

同一条命令再跑一次就会从上次中断的位置继续。如果换了模型或任务范围，请改用新的
`HARNESS2_RUNS_DIR`。更多细节见[结果与断点续跑](EXPERIMENTS.md#结果与断点续跑)；
所有命令也都支持 `--help`。

## 引用

```bibtex
@article{xu2026harness2,
  title   = {Harness$^2$: Recursive Agent Harnessing for an Open World},
  author  = {Xu, Ruiyao and Chen, Yanfei and CuiZhu, Zhongying and Dalvi Mishra, Bhavana and Ming, Yifei and Yu, Han and Han, Rujun and Lee, Chen-Yu and Pfister, Tomas},
  journal = {arXiv preprint arXiv:2610.14224},
  year    = {2026},
  url     = {https://arxiv.org/abs/2610.14224}
}
```

## 贡献

想参与项目，请查看[中文贡献指南](CONTRIBUTING.md)；也可以直接阅读[英文原文](../../../CONTRIBUTING.md)。

## 许可证

Harness² 采用 [Apache 2.0](../../../LICENSE) 许可证。`third_party/` 下的第三方代码、
基准测试和数据集仍沿用各自的上游许可证，具体见
[第三方声明](../../../dependencies/README.md)。

## 免责声明

本项目不是 Google 官方支持的产品，也不在
[Google 开源软件漏洞奖励计划](https://bughunters.google.com/open-source-security)的适用范围内。
