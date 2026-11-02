# 配置指南

第一次使用时，建议先走一遍[快速开始](README.md#安装)。这篇指南重点介绍基准测试
依赖、模型凭据，以及可选的 Codex 执行器。每套基准测试的具体用法见
[基准测试指南](BENCHMARKS.md)。

## 要求

基准测试建议在 Linux 和 Python 3.12 下运行；如果只使用 Python 包，3.13 也支持。
Debian/Ubuntu 可以直接安装下面这些系统依赖：

```bash
sudo apt-get update
sudo apt-get install -y git curl unzip ca-certificates pandoc poppler-utils
```

Pandoc 和 Poppler 是 LAB 需要的。另外还要准备：

- [Node.js](https://nodejs.org/en/download) 20 或更高版本；
- [uv](https://docs.astral.sh/uv/getting-started/installation/)；
- [Google Cloud CLI](https://cloud.google.com/sdk/docs/install)；
- [Docker Engine](https://docs.docker.com/engine/install/)（仅 WorkBuddy-Bench 需要），
  并确认当前用户可以运行 `docker info`。

运行基准测试安装命令时，项目会把锁定版本的 Bun 放到 `dependencies/` 下。
后面的命令都应在 Harness² 主环境已经激活的情况下运行。

## 模型与凭据

先把 `.env.example` 复制为 `.env`，再填入 Vertex AI 项目和模型 ID。最后登录：

```bash
gcloud auth application-default login
```

Shell 环境变量会覆盖 `.env` 中的同名设置。`GOOGLE_CLOUD_PROJECT` 指定项目，
`GOOGLE_CLOUD_LOCATION` 默认是 `global`。登录账号还需要拥有所配置模型的访问权限。

| 设置 | 含义 |
|---|---|
| `HARNESS2_SOLVER_MODEL` | OpenCode 的 `provider/model` 格式执行器模型 |
| `HARNESS2_IMPROVER_MODEL` | 相同格式的改进器模型；默认使用执行器模型 |
| `HARNESS2_JUDGE_MODEL` | 不带提供商前缀的评测模型 ID；适配器会添加 Vertex 前缀 |
| `HARNESS2_RUNS_DIR` | 输出根目录；默认为 `runs/` |

默认配置使用 Vertex Gemini 作为执行器和评测器，改进器则可以选择 Vertex Claude
或 Gemini。如果配好了对应凭据，LAB 也可以直接使用上游评测器支持的提供商。
WorkBuddy 会根据这些设置生成 Gemini 求解器和评测器路由；只要换了其中一个模型，
就需要重新运行 setup。

### 其他身份验证方式

如果机器已经绑定服务账号，应用默认凭据（ADC）可以直接使用它。也可以选择：

- `gcloud auth application-default login --no-browser`：终端会给出一条命令，
  可以拿到另一台有浏览器的电脑上完成登录；
- 将 `GOOGLE_APPLICATION_CREDENTIALS` 指向服务账号密钥文件；
- `gcloud auth application-default login --impersonate-service-account=<account>`。

无论采用哪种 Vertex 登录方式（包括密钥文件），都要确保能从 `PATH` 找到 `gcloud`。
安装检查会调用 `gcloud auth application-default print-access-token`。

## 安装基准测试

只需要安装准备运行的那套基准测试：

```bash
harness2-setup lab
harness2-check --bench lab
```

JobBench 的简称是 `jb`，WorkBuddy-Bench 是 `wb`。如何选择子集、各自采用什么许可，
可以查看[基准测试指南](BENCHMARKS.md)。

运行器覆盖文件和兼容性补丁都在 [`third_party/`](../../../third_party/README.md) 中。
运行 setup 后，项目会把支持的上游版本克隆到 `dependencies/`，接着应用补丁、创建
环境并下载任务数据。

每套基准测试都有自己的虚拟环境，位置如下：

- `dependencies/harvey-labs/.venv`
- `dependencies/job-bench-eval/.venv`
- `dependencies/workbuddy-bench/.venv`

Harness² 会自动找到正确的解释器，不要在运行主命令前手动激活这些环境。

### 安装检查

`harness2-setup <bench> --dry-run` 只展示安装计划，不会修改任何内容。
`harness2-check` 会检查依赖、凭据和配置，也不会调用模型。每次开始优化前还会自动
再检查一遍，包括评测器配置。

这里不会提前验证模型能否调用；执行器、改进器和评测器都会在第一次实际使用时验证。
如果优化或评测没有完整跑完，命令会返回非零退出码。

## Codex 执行器

Codex 执行器需要连接 Responses API 端点。如果模型是 Vertex AI 上的 Gemini，
可以使用项目自带的 `harness2-bridge`：它会在本地启动 LiteLLM 服务，把 Gemini
转换成所需接口，并复用应用默认凭据和 `GOOGLE_CLOUD_PROJECT`。

先安装可选依赖，再开一个终端单独运行桥接服务：

```bash
uv pip install -e '.[render,bridge]'
harness2-bridge                 # WorkBuddy 使用：harness2-bridge --host 0.0.0.0
```

启动后，桥接服务会打印一组需要写入 `.env` 的 `HARNESS2_CODEX_*` 设置。端口和自动
生成的访问密钥会保存在 `dependencies/codex/`，下次启动继续复用。之后在安装、检查、
优化、评测和报告命令中加上 `--substrate cx`：

```bash
harness2-setup lab --substrate cx
harness2-check --bench lab --substrate cx
harness2 --bench lab --substrate cx --k 1 --mode parallel \
  --only-domains antitrust-competition --max-tasks 1
```

WorkBuddy 在 Docker 容器里运行 Codex，所以桥接服务必须监听 `--host 0.0.0.0`，
同时会给出 `HARNESS2_CODEX_CONTAINER_URL`。注意，这会把服务绑定到所有网络接口，
请通过防火墙等方式只允许需要的客户端访问。

如果已经有其他 Responses API 端点，直接设置 `HARNESS2_CODEX_BASE_URL`、
`HARNESS2_CODEX_API_KEY` 和 `HARNESS2_CODEX_MODEL` 即可。setup 会把端点和模型写入
`dependencies/codex/home/config.toml`。修改这两个配置后要重新运行 setup，否则
`harness2-check` 会提示配置已经过期。

Codex 运行时默认关闭沙箱。正式评测前，请务必查看[执行器网络限制](EXPERIMENTS.md#网络访问)。

## 配置期间的网络访问

安装过程中会从这些主机下载依赖：

| 主机 | 用途 |
|---|---|
| `github.com` | 锁定版本的上游检出 |
| `registry.npmjs.org` | 锁定版本的 Bun 运行时、Codex CLI 和 `bun install` 解析的依赖 |
| `huggingface.co` 及其 CDN | JobBench 数据和 WorkBuddy 子集归档 |
| Docker 镜像仓库 | WorkBuddy 容器镜像 |
| `astral.sh` | 主机未安装系统 Python 3.12 时获取 CPython 3.12 |

如果需要提前配置网络策略，可以先运行 `harness2-setup <bench> --dry-run`，查看实际
会执行哪些安装命令。实验期间还需要访问 `.env` 里配置的模型端点。安装依赖和执行器
访问网页是两件事，具体区别见[评测期间的网络访问](EXPERIMENTS.md#网络访问)。

## Wheel 安装

Wheel 里只有 `harness2` 包，不包含完整源码。任务划分工具
`python scripts/split_tasks.py` 仍然需要这个仓库。通过 Wheel 安装时，建议单独建一个
工作目录；`.env`、`dependencies/` 和 `runs/` 都会以当前工作目录为基准。

---

[English version](../../SETUP.md)
