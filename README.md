# IQRadar · 降智雷达

**本地优先的大模型「智力」评测与观测工作台** —— 对接任意 OpenAI 兼容网关，批量运行
coding-agent 基准与 API 评测，计算 IQ / 速度 / 成本 / 额度等指标，并用本地 Web
仪表盘（降智雷达 · 额度雷达 · 外部榜单汇聚）呈现结果。

> 口号语义：`IQ = pass_rate_percent × 1.5`（上限 150）。
> 「降智」是社区俚语：模型被服务方悄悄降级/限流导致变笨。IQRadar 用同一套基准
> 持续打分，帮你发现模型什么时候「变笨了」。

## 功能特性

- **多基准评测**：DeepSWE（pier 驱动的 coding-agent 基准）、Terminal-Bench 2
  （Harbor 框架 + mini-swe-agent），以及 10 个轻量 API 评测
  （GPQA-Diamond、AIME 2024、MMLU-Pro、ARC-AGI-2、HLE、LiveCodeBench、
  Chinese-SimpleQA、HMMT、Codeforces、IMO-AnswerBench），全部可在
  `configs/benchmark.yaml` 中增删。
- **任意 OpenAI 兼容网关**：模型列表自动发现（`GET /v1/models`），也支持在测试页
  「网关配置」卡片里直接填 baseURL + key 热切换，无需改代码或重启。
- **测试工作台**：选择模型 / 样本数 / 随机种子提交批次，实时串流 pier 日志、
  trial 日志、agent 轨迹与 verifier 日志，展示题目级进度。
- **显式发布**：运行完成不会自动上大盘；在评测记录列表里勾选后「发布」才生成
  不可变版本化快照（支持累积合并、单条发布/回撤）。
- **指标体系**：IQ、通过率、Wilson 95% 置信区间、耗时、成本（按价格表）、
  周额度容量、token 效率；雷达图多维归一化呈现。
- **榜单汇聚**：聚合多个公开 LLM 榜单数据源做横向参考（可配置开关）。
- **本地与私密**：纯文件存储（JSON/JSONL，无数据库），服务只绑定回环地址，
  日志与 API 输出自动脱敏 `sk-` / Bearer 凭据。

## 架构总览

```text
测试页 (Vue)  ──POST /api/deepswe-runs──▶  DeepSweService（状态机 + 后台 pier 子进程）
      │                                          │
      │ 实时日志/进度轮询                          ▼
      │                              data/deepswe/jobs/<run_id>/   Pier 产物
      │                                          │ 导入 result/reward
      ▼                                          ▼
评测记录列表 ──显式「发布」──▶ DashboardPublisher（合并去重 + 聚合 + 快照）
                                                 │
                                                 ▼
                              data/reporting/snapshots/<id>/  不可变快照
                                                 │
      大盘页 (Vue)  ◀──GET /api/dashboard────────┘
```

模块边界由架构测试强制：只有 `publication` 层可以把「运行记录」桥接为「大盘快照」。
详见 [docs/architecture.md](docs/architecture.md) 与 [docs/overview.md](docs/overview.md)。

## 快速开始

### 前置要求

| 组件 | 版本要求 | 说明 |
|---|---|---|
| Linux / WSL2 | — | agent 容器依赖 Docker 桥接网关 |
| Python | ≥ 3.12 | 依赖管理用 [uv](https://docs.astral.sh/uv/) |
| Node.js | ≥ 20.19 | Vite 7 要求 |
| Docker | daemon 可用 | agent 容器运行必需 |
| 模型网关 | OpenAI 兼容 | 需提供 `/v1/models` 与 `/v1/chat/completions`（开发时用的是本地部署的 Bifrost 网关，其他 OpenAI 兼容网关同样可用） |

DeepSWE / Terminal-Bench 2 的任务集不在本仓库内：把上游 benchmark 检出放到
本项目的 `checkouts/` 下（路径在 `configs/benchmark.yaml` 的 `local_path` 配置），
`start.sh` 会在缺失时补建 `data/datasets/` 符号链接。

### 启动

```bash
git clone <this-repo> && cd iq_radar

# 1. 配置环境（网关地址 / 模型名 / key）
cp .env.example .env && chmod 600 .env

# 2. 价格表（按需填写各模型单价，成本/额度指标才准确）
cp configs/prices.example.yaml configs/prices.yaml

# 3. 一键启动：uv sync + 前端构建 + 启动服务
./start.sh
# 网关占用 8080 时换端口：IQ_RADAR_PORT=8081 ./start.sh
```

打开 `http://127.0.0.1:<port>` 即可进入仪表盘与测试页。

### 环境变量（`.env`）

| 变量 | 说明 |
|---|---|
| `GATEWAY_BASE_URL` | 模型网关地址（默认 `http://localhost:8080/v1`） |
| `GATEWAY_MODEL_NAME` | 默认模型名（如 `gateway/deepseek-v4-flash`） |
| `GATEWAY_API_KEY` | 网关 key（仅网关需要鉴权时设置） |
| `IQRADAR_MODEL_NAMES` | 测试页待测模型列表（逗号分隔）；不设则从网关 `/v1/models` 自动发现，再退回 `configs/models.yaml` |
| `IQ_RADAR_PORT` | Web 服务端口（默认 8080） |
| `IQRADAR_DEEPSWE_DIR` | DeepSWE 检出路径（默认 `checkouts/deep-swe`） |

测试页「网关配置」卡片里填写的 baseURL/key 会保存到
`data/settings/gateway.json`（0600），优先级高于 `.env`，保存即生效。

## 使用

### 测试页（评测工作台）

1. 选择待测模型、样本数与随机种子，提交 DeepSWE 批次；
2. 实时查看 pier / trial / agent / verifier 日志与题目进度；
3. 完成后在「评测记录」列表勾选记录，点「发布选中」合并进当前大盘快照
   （`POST /api/deepswe-runs/publish-batch`），也可单条发布/回撤；
4. api-eval 运行可对网关超时/临时失败的题目执行「重测」。

### CLI

```bash
uv run iqradar doctor                      # 环境/配置/基准路径自检
uv run iqradar probe --prompt "你好"       # 网关连通性测试
uv run iqradar serve --host 127.0.0.1 --port 8080
```

### 批量评测

`scripts/batch_run.py` 提供「N 个模型 × N 题 → 合并发布」的批量脚本，
`scripts/prewarm_environments.py` 可预构建 Docker 镜像实现零网络评测，
`scripts/stall_guard.py` 监控长时间无输出的 trial 并清理容器内僵尸进程。

## 指标口径

| 指标 | 定义 |
|---|---|
| IQ | `pass_rate_percent × 1.5`，clamp 到 [0, 150] |
| 置信区间 | Wilson 95%（z = 1.96） |
| 成本 | `tokens / 1M × usd_per_1m`（含 cached 输入，按 `configs/prices.yaml`） |
| 额度 | 单任务占周预算百分比、估算周任务数 / 周通过数 |
| 聚合 | 按 benchmark × model × effort 分组；`skipped` 与基础设施错误不计入分母 |
| 雷达轴 | IQ 雷达：iq / pass_rate / stability / speed / cost_efficiency；额度雷达：额度友好度 / 周任务 / 周通过 / 单通过成本 / token 效率 |

详见 [docs/data-schema.md](docs/data-schema.md)（数据契约）与
[docs/leaderboard-aggregation.md](docs/leaderboard-aggregation.md)（榜单汇聚）。

## 安全须知

- 服务**只绑定回环地址**（`serve` 会拒绝非 loopback host），写请求校验 Origin；
  不要在没有加认证与限流的情况下暴露到公网。
- `.env`、`data/`、`checkouts/` 均被 gitignore；日志与 API 错误信息会对
  `sk-` / Bearer / 已知密钥值做脱敏。
- 运行产物目录默认 `0700` 权限。

## 开发与测试

```bash
# 后端（单元 + 集成 + 架构边界）
uv run pytest -v

# 前端（单测 + 构建 + e2e）
cd web && npm ci
npm test && npm run build && npm run test:e2e
```

目录结构：

```text
├── src/iqradar/        # Flask API / CLI / 基准后端 / 指标 / 发布 / 报表
│   ├── benchmarks/     # deep-swe、terminal-bench-2、api-eval 适配器
│   ├── deepswe/        # 运行状态机、pier 执行、记录导入
│   ├── metrics/        # 聚合 / Wilson / 成本 / 额度
│   ├── publication/    # 运行记录 → 大盘快照的唯一桥接层
│   └── reporting/      # 不可变快照仓库
├── web/                # Vue 3 + Vite + ECharts 前端（大盘 / 测试页）
├── configs/            # benchmark.yaml / models.yaml / prices*.yaml
├── docs/               # 架构、配置、数据契约、runbook、测试设计
├── scripts/            # 批量评测 / 镜像预热与备份 / 批次守护
└── tests/              # pytest 单元 + 集成 + 架构边界
```

更多文档：[docs/overview.md](docs/overview.md) ·
[docs/architecture.md](docs/architecture.md) ·
[docs/configuration.md](docs/configuration.md) ·
[docs/runbook.md](docs/runbook.md) ·
[docs/testing/](docs/testing/)（TDD 证据）

## License

[MIT](LICENSE)
