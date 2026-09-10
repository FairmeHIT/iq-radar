# IQRadar 项目梳理（2026-08-14）

> 本文是对 `iq_radar/` 的一次全面梳理：定位、架构、数据流、指标口径、
> API、当前状态与待办观察点。作为 `docs/architecture.md` 等既有文档的
> 速览入口。

## 1. 一句话定位

**IQRadar 是一个本地运行的大模型"智力雷达"评测与监控工具**：通过
DeepSWE 基准（`checkouts/deep-swe` 检出 + 本地 `pier` 二进制）批量跑
coding-agent 任务，导入结果记录，计算 IQ / 速度 / 成本 / 额度等指标，
并以"测试页提交 → 显式发布 → 不可变快照 → 大盘展示"的方式呈现。

口号语义：`IQ = pass_rate_percent × 1.5`（上限 150）。

## 2. 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python ≥3.11，Flask（API）、Pydantic v2（schema）、PyYAML（配置）、Typer（CLI） |
| 前端 | Vue 3 + Vite 7 + TypeScript + ECharts 6 + lucide-vue-next，Vitest + Playwright 测试 |
| 任务执行 | `checkouts/deep-swe` 检出 + 本机 `pier` 二进制（`PYTHONPATH` 指向 deep-swe，agent 为 `cn_agent:CnMiniSweAgent`） |
| 数据 | 纯文件（JSON / JSONL / YAML），无数据库；写入走原子文件（tmp + fsync + rename） |

## 3. 目录结构

```text
iq_radar/
├── src/iqradar/
│   ├── cli.py                 # Typer CLI：doctor / probe / serve
│   ├── api/app.py             # 组合根：装配 blueprints、安全头、静态资源
│   ├── config/                # YAML 配置加载与 pydantic schema（模型目录/价格/基准）
│   ├── deepswe/               # DeepSWE 提交、Pier 执行、记录导入、运行存储
│   ├── ingest/redact.py       # sk- / Bearer / 已知值脱敏
│   ├── metrics/               # aggregate / wilson / cost / quota 指标计算
│   ├── publication/           # 唯一允许"运行记录 → 大盘快照"的桥接层
│   ├── reporting/             # 大盘查询 + 不可变快照仓库
│   ├── schemas/               # RunRecord / AggregateSummary 数据契约
│   └── shared/                # 原子文件写入、API 信封（ok/error）
├── web/                       # Vue 前端（dashboard 大盘 + deepswe 测试页）
├── configs/                   # benchmark.yaml / models.yaml / prices.yaml
├── data/                      # 题目 (datasets/)、运行产物、快照（gitignore）
├── docs/                      # 架构 / 配置 / 数据契约 / runbook / TDD 证据
├── tests/                     # 71 个 pytest（单元 + 集成 + 架构边界）
├── vendor/                    # 外部依赖：deep-swe 任务样例、dradar CLI（只读）
├── start.sh                   # 一键启动（uv sync + npm build + serve）
└── pyproject.toml / uv.lock
```

## 4. 核心数据流

```text
测试页(web/features/deepswe)
   │ POST /api/deepswe-runs        （model_id + n_tasks + sample_seed）
   ▼
DeepSweService.submit()
   │ 状态机 queued → running → completed/failed
   │ 后台线程 run_pier_sync()：启动 pier 子进程（独立进程组）
   ▼
data/deepswe/jobs/<run_id>/        Pier 任务输出（每个 trial 一个目录）
   │ 导入（importer.import_pier_run 读取 result.json / reward.json）
   ▼
data/deepswe/runs/<run_id>/        state.json + pier.log（records.jsonl 由发布时生成）
   │ 评测记录行「发布」POST /publish；「回撤」DELETE /publish
   │ api-eval run 网关失败题可点「重测」POST /retry-gateway-failures
   ▼
DashboardPublisher.publish_records()
   │ 与当前快照 records 合并去重（run_id 确定性键，新 created_at 获胜）
   │ 聚合（aggregate_runs + 价格表）→ 快照目录（create-once、校验摘要）
   ▼
data/reporting/snapshots/<id>/     summary.json + runs.jsonl + manifest.json
   │ 原子替换 current.json（旧快照不可变，可回看）
   ▼
大盘页(web/features/dashboard)  ←  GET /api/dashboard 读 current 快照
```

关键边界（由架构测试强制）：
- `deepswe` 不得引用 `reporting`/`publication`；`reporting` 不得引用 `deepswe`；
- 只有 `publication` 可以桥接两边；
- **运行完成 ≠ 上大盘**：发布是显式用户动作（评测记录行级 `发布`/`回撤` 按钮）；
- **发布 = 累积（radar-v3 起）**：新 records 与当前版本化快照合并，跨批次/跨基准的模型同时呈现；`POST /publish?merge=false` 可发布仅含本次 records 的替换快照；`DELETE /publish` 回撤指定 run 的快照并把 `current.json` 重指向最新剩余（或清空）；
- **api-eval 网关失败重测**：`POST /retry-gateway-failures` 对已完成 run 仅重测上游网关超时/临时错误失败的题（模型答错不重测），重测后若已发布则自动重发布刷新大盘；
- 快照身份 = 合并后记录字节哈希 + 价格哈希 + `PROJECTION_VERSION`（现为 `radar-v3`）；
- 快照 manifest 带 `source_job_ids`（全部来源）；「源已删」判定要求所有来源仍存在于测试页。

## 5. 存储与数据契约

- 运行状态：`data/deepswe/runs/<run_id>/state.json`
- Pier 输出：`data/deepswe/jobs/<run_id>/`
- 记录格式：`RunRecord`（schema_version "1.0"；状态含 passed/failed/timeout/runner_error/verifier_error/budget_stopped/skipped；effort 为 low/medium/high/max）
- 快照：`data/reporting/snapshots/<id>/{summary,runs,manifest}.{json,jsonl}`，`current.json` 指向当前激活快照
- 旧版只读回退：`data/aggregate/radar.json`、`data/raw/runs`（发布第一个快照前兜底）

## 6. 指标口径

| 指标 | 公式 |
|---|---|
| IQ | `pass_rate_percent × 1.5`，clamp [0, 150] |
| 置信区间 | Wilson 95%（`metrics/confidence.py`，z=1.96） |
| 成本 | 按模型价格表：`tokens/1M × usd_per_1m`（含 cached 输入） |
| 额度 | 单任务占周预算 %、估算周任务数/通过数、输出 token 占比 |
| 聚合 | 按 benchmark×model×effort 分组，**skipped 不计入分母**；零通过时 `cost_per_pass_usd` 为 null |
| 评分口径 | `tasks_scored` = 剔除 runner_error/verifier_error 后的任务数，是 pass_rate/IQ 的分母；`infra_error_count` 单列。大盘前端同口径展示（KPI 标注"已剔除 N 个基础设施错误"，最近运行表对 infra 状态给中文标签与提示） |
| usage 采集 | deep-swe/api-eval 上报原生 usage；TB/TB2/SWE-bench-pro 的 agent_steps 与 cached tokens 从 mini-swe-agent trajectory 采集（TB1 经 `tb_agents` `-o /agent-logs`、TB2 读 harbor 落盘的 `agent/mini-swe-agent.trajectory.json`、SWE-bench-pro 经 `-o /workspace/trajectory.json`）。无轨迹的记录 agent_steps=0，大盘步数列显示 `--`（N/A） |
| 雷达轴 | IQ 雷达：iq/pass_rate/stability/speed/cost_efficiency；额度雷达：额度友好度/周任务/周通过/单通过成本/ token 效率（均归一化 0–100） |

## 7. API 一览（loopback-only，写请求校验 Origin）

- `GET /api/models` — 测试页模型目录（来自 `configs/models.yaml`）
- `POST /api/deepswe-runs` — 提交运行（单活跃运行限制，409 冲突）
- `GET /api/deepswe-runs/{id}` `/latest` `/records` `/logs?source=` — 状态 / 恢复 / 记录 / 实时日志（pier、job、trial、agent、verifier 五个来源，3s 轮询）
- `POST /api/deepswe-runs/{id}/cancel` — 中断（SIGTERM → SIGKILL 进程组）
- `POST /api/deepswe-runs/{id}/publish` — 发布快照（仅 completed）
- `GET /api/dashboard` `/summary` `/radar/iq` `/radar/quota` `/runs` — 大盘只读查询
- `GET /api/health` `/health/readiness` `/openapi.json` `/docs` — 健康与文档
- 统一信封：`{"success": bool, "data": ..., "error": ...}`

## 8. 前端结构

- `App.vue`：仅导航壳（顶部时钟 + 大盘/测试两个 tab）
- `features/dashboard/`：大盘页 —— KPI 卡片（综合 IQ/通过率/样本量/总成本/平均耗时/周容量）、IQ 雷达图、额度雷达图、降智趋势、运行明细表；模型/强度筛选；无数据时用 demo 数据展示
- `features/deepswe/`：测试工作台 —— 模型/样本数/种子选择、启动/中断/发布、实时日志面板
- 前后端类型在 `features/*/types.ts` 各自维护，边界有 `architecture.test.ts` 校验

## 9. 当前状态（截至梳理时）

- 运行记录：3 次 DeepSWE 运行（`data/deepswe/runs/`）：
  - `577f2b24…` completed，1 任务（deepseek-v4-flash）
  - `8de185b2…` failed（10 任务，被服务重启中断，旧 runner 缺 cn_agent）
  - `cb5c8102…` completed，2 任务 ← **当前已发布快照**（2 任务全失败，IQ=0）
- 快照：3 个不可变快照；`current.json` 指向 `cb5c8102…-d3bd11b8…`
- 价格表 `configs/prices.yaml` 全部为 0（价格未填，成本/额度指标暂失真）
- 待测模型列表：优先级 `IQRADAR_MODEL_NAMES`（.env，逗号分隔）→ 网关 `/v1/models` 自动发现（需 GATEWAY_BASE_URL）→ `configs/models.yaml` 兜底
- 模型网关：本地 **Bifrost** 网关（OpenAI 兼容，独立部署，`BIFROST_HOST=0.0.0.0` 监听 `*:8080`），模型名 `gateway/deepseek-v4-flash`，key 见 `deep-swe/.env` 与 `iq_radar/.env`。agent 容器内必须用 `http://172.17.0.1:8080/v1`（宿主回环不可达）；`cn_agent.py` 已固定 `model_class=litellm`（chat/completions，Bifrost 不支持 Responses API）。已用 1 任务冒烟验证：agent 119 步、调用消耗 1040 万 input tokens
- 注意：`8080` 现在是模型网关，IQRadar 自身 Web 服务需换端口启动（`IQ_RADAR_PORT=8081`，service-console 已配置）

## 10. 观察与建议

1. **价格表为 0**：`prices.yaml` 未填真实单价，导致成本/额度/周容量等指标与雷达图失真，建议先补价格。
2. **样本量过小**：当前快照仅 2 个任务、0 通过，IQ=0 无统计意义；发布前应至少跑满有代表性的样本并看 Wilson 区间。
3. **旧版数据路径**：`data/aggregate/radar.json`、`data/raw/runs`、`data/artifacts/` 属于旧管线遗留（legacy fallback），首个快照发布后即不再写入，可考虑归档清理（需确认 dashboard 不再依赖）。
4. **`NUL` 文件**：`iq_radar/NUL`（0 字节）疑为 Windows 重定向误生成，可删除。
5. **文档多而散**：既有 `architecture.md` / `configuration.md` / `data-schema.md` / `runbook.md` + 6 份 TDD 证据 + 2 份计划文档，内容有重叠，本文档作为入口可逐步收敛。
6. **单活跃运行**：服务层面限制一次只能跑一个 DeepSWE 批次（`n_concurrent=1`），多模型对比需要排队或扩展并发。
7. **安全性**：loopback-only + Origin 校验 + 脱敏 + 目录 0700 已有；若未来要对外暴露，必须先加认证与限流（README 已注明）。
