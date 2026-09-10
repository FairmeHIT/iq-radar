# IQRadar 外部榜单汇聚模块设计

## 1. 设计目标

在 iq-radar 现有"大盘"和"测试"模块之外，新增**榜单汇聚模块**，让用户能按业务场景（如办公、运维、写作、UI设计、后端开发、创意等）查看主流公开榜单中模型/Agent 的得分排名，并最终可与自测数据叠加对比。

### 核心原则

- **场景驱动**：从用户实际使用场景出发，而非机械罗列 benchmark 名称
- **数据优先**：优先集成有官方 API 或可自动拉取的数据源，其次才是可解析的 HTML
- **可扩展**：每个外部榜单是一个独立 adapter，插件式注册
- **缓存友好**：拉取的数据在本地缓存，支持 TTL 过期刷新，不依赖持续在线
- **与现有架构一致**：遵循 iq-radar 的 module boundary 规则

---

## 2. 场景体系设计

### 2.1 场景定义

| 场景 ID | 场景名称 | 用户画像 | 典型活动 |
|---|---|---|---|
| `office` | 办公效率 | 文员/PM/行政 | 文档撰写、表格处理、邮件、PPT、会议纪要 |
| `writing` | 创意写作 | 文案/翻译/营销 | 文章、文案、翻译、润色、创意内容 |
| `backend` | 后端开发 | 后端工程师 | 代码生成、bug修复、重构、测试编写 |
| `frontend-ui` | 前端与 UI | 前端/设计 | 前端代码、UI生成、设计稿转代码 |
| `ops` | 运维自动化 | DevOps/SRE | Shell脚本、K8s、故障排查、监控告警 |
| `data` | 数据分析 | 数据分析师 | SQL、数据清洗、可视化、报表 |
| `creative` | 创意策划 | 市场/产品 | 头脑风暴、方案设计、营销创意 |
| `agent` | Agent 自动化 | AI工程师 | 多步任务编排、工具调用、浏览器操作 |

### 2.2 场景 → 能力维度 → 榜单映射

每个场景对应一组**能力维度**（如推理、代码、Agent、中文等），每个维度由若干榜单覆盖。

```
场景 ──→ 能力维度 ──→ 榜单 ──→ 模型得分
```

具体映射见下方第 3 节。

---

## 3. 数据源评估与推荐

### 3.1 数据源分级

| 等级 | 说明 | 推荐优先级 |
|---|---|---|
| ⭐⭐⭐ | 有官方 REST API，免/易认证，更新频繁 | 必集成 |
| ⭐⭐ | 有可下载数据文件（JSON/CSV/HF Dataset），定时同步 | 推荐集成 |
| ⭐ | 仅 HTML 页面，需爬虫解析 | 按需集成，优先度低 |

### 3.2 数据源详细清单

#### 优先集成（有官方 API 或标准数据接口）

| 榜单 | 数据获取方式 | 更新频率 | 认证 | 覆盖场景 | 备注 |
|---|---|---|---|---|---|
| **LLM-Stats** | 官方 REST API `GET /v1/benchmarks` `GET /v1/models` `GET /v1/scores` `GET /v1/rankings` | 每日 | 需 API key（免费） | 全部场景 | 覆盖 SWE-Bench Verified、GPQA、AIME、MMLU、MMLU-Pro、LiveCodeBench、HLE 等 30+ benchmark。每日更新。**最适合作为核心数据源。** |
| **OpenRouter Data API** | `GET /api/v1/datasets/rankings-daily` `GET /api/v1/datasets/app-rankings?category=coding` | 每日 | 需 API key（免费，30 req/min，500 req/day） | 全部场景 | 按模型排名 + 按应用分类（coding/productivity 等）。CC BY 4.0 许可。 |
| **Arena AI Leaderboards** (community) | `GET https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboard?name=text|code|vision|agent` | 每日 | 无认证，免费 | 全部场景 | 已验证可用。社区项目，每日自动更新 LMArena 所有榜单。code 榜 50 模型（ELO）、text 榜 20 模型（ELO）、agent 榜 10 模型（6 维得分：Net Improvement/Confirmed Success/Bash Recovery 等）、vision 榜 39 模型。**推荐作为首选免费数据源。** |
| **Artificial Analysis** | `https://artificialanalysis.ai/api/` 免费 REST API v2 | 每日 | 免费，无需 key | 全部场景 | 覆盖推理、速度、成本、上下文等多个维度。 |
| **HuggingFace Leaderboard API** | `GET https://huggingface.co/api/datasets/{dataset_id}/leaderboard` | 按榜单 | 可免费，gated 数据集需 HF token | 后端、Agent | 已验证可用。官方标准 API，支持 SWE-bench、GAIA、Open LLM Leaderboard 等。Python: `HfApi.get_dataset_leaderboard("SWE-bench/SWE-bench_Verified")` |
| **LiveBench** | HF 数据集 `livebench/` | 每月 | 免费 | 后端、数据、运维 | 无污染基准，覆盖推理、编程、数学。 |

#### 按需集成（有数据文件但需适配）

| 榜单 | 数据获取方式 | 更新频率 | 认证 | 覆盖场景 | 备注 |
|---|---|---|---|---|---|
| **Berkeley Function-Calling** | HF 数据集 `gorilla-llm/Berkeley-Function-Calling-Leaderboard` | 不定期 | 免费 | Agent、办公 | 函数调用，对企业 copilot 场景重要 |
| **AlpacaEval** | HF 数据集 `tatsu-lab/alpaca_eval` | 不定期 | 免费 | 写作、创意 | 指令跟随能力 |
| **MTEB** | HF 数据集 + GitHub JSON | 不定期 | 免费 | 全部 | 文本嵌入模型，适合 RAG 场景 |
| **SWE-bench (Verified)** | HF 榜单 `https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified/embed/leaderboard` | 不定期 | 免费 | 后端 | 也可通过 LLM-Stats API 获取 |
| **GAIA** | HF 榜单 `https://huggingface.co/spaces/gaia-benchmark/leaderboard` | 不定期 | 免费 | Agent | Agent 通用能力 |
| **AgentBench** | GitHub 公开数据 | 不定期 | 免费 | Agent | 多维度 Agent 评估 |

#### 低优先级（需爬虫）

| 榜单 | 原因 | 替代方案 |
|---|---|---|
| **SuperCLUE** | 仅 HTML 页面，无公开 API | 可以通过 LLM-Stats（覆盖中文模型）或 OpenCompass 替代 |
| **OpenCompass CompassRank** | 仅 HTML 页面，无公开 API | 同上 |
| **Vellum LLM Leaderboard** | 仅 HTML 页面 | 数据可通过 LLM-Stats 或 OpenRouter 替代 |
| **Scale SEAL** | 仅 HTML 页面，私有数据 | 数据价值高但获取困难 |
| **DataLearnerAI** | 仅 HTML 页面 | 聚合多个榜单，但无 API |
| **ARC Prize** | 仅 HTML 页面 | 特定场景（AGI 推理），可后续补充 |
| **BridgeBench** | 仅 HTML 页面 | vibe coding 场景，可后续补充 |
| **EQ-Bench** | 仅 HTML 页面 | 情绪智能，可后续补充 |
| **Kilo Code** | 仅 HTML 页面 | 编程 agent 榜单，可后续补充 |
| **Sonar** | 仅 HTML 页面 | Java 代码质量，针对性较强 |
| **CodexRadar** | 仅 HTML 页面 | 雷达站形式，可参考其 UI 设计 |

### 3.3 推荐数据源选择决策

```
核心数据源（必选，覆盖 80% 场景）：
  LLM-Stats API ─── 统一获取多 benchmark 排名
  ├── SWE-bench Verified（后端开发）
  ├── GPQA（推理/通用）
  ├── AIME（数学/推理）
  ├── MMLU-Pro（通用知识）
  ├── LiveCodeBench（编程）
  └── HLE（高难度推理）

补充数据源（按场景增强）：
  OpenRouter Data API ─── 按应用分类排名（coding, productivity 等）
  Arena AI Leaderboards ─── 社区投票排名（text, code 子榜单）
  Artificial Analysis ─── 速度、成本、延迟信息
  HF 标准 Leaderboard API ─── 专项榜单（GAIA for Agent, MTEB for embedding）
```

---

## 4. 场景 → 榜单映射矩阵

| 场景 | 首选榜单 | 数据源 | 补充榜单 | 关键指标 |
|---|---|---|---|---|
| **办公效率** | MMLU-Pro, HLE | LLM-Stats | OpenRouter (productivity), Arena (text) | 通用知识、指令跟随 |
| **创意写作** | AlpacaEval, Arena (text) | HF/Arena | LLM-Stats, EQ-Bench | 指令跟随、风格、情感 |
| **后端开发** | SWE-bench Verified, LiveCodeBench | LLM-Stats/LiveBench | Arena (code), OpenRouter (coding) | 代码解决率、pass@1 |
| **前端与 UI** | SWE-bench Multimodal, Arena (code) | LLM-Stats/Arena | BridgeBench | 前端代码、UI 生成 |
| **运维自动化** | LiveBench, Berkeley FC | LiveBench/HF | Terminal-Bench, SWE-bench | 工具调用、推理 |
| **数据分析** | GPQA, MMLU-Pro, LiveBench | LLM-Stats/LiveBench | Berkeley FC | 推理、SQL、结构化 |
| **创意策划** | AlpacaEval, Arena (text) | HF/Arena | EQ-Bench, OpenRouter | 指令跟随、创意 |
| **Agent 自动化** | GAIA, Berkeley FC, SWE-bench | HF/LLM-Stats | AgentBench, Terminal-Bench | 工具调用、多步规划 |

---

## 5. 模块架构设计

### 5.1 目录结构

```
src/iqradar/leaderboards/          # 后端榜单汇聚模块
├── __init__.py
├── base.py                        # LeaderboardSource ABC
├── adapters/
│   ├── __init__.py
│   ├── llm_stats.py               # LLM-Stats API adapter
│   ├── openrouter.py              # OpenRouter Data API adapter
│   ├── arena_ai.py                # Arena AI Leaderboards adapter
│   ├── artificial_analysis.py     # Artificial Analysis adapter
│   ├── hf_leaderboard.py          # HuggingFace standard leaderboard adapter
│   └── livebench.py               # LiveBench HF dataset adapter
├── registry.py                    # 注册 & 构建数据源
├── service.py                     # LeaderboardService — 聚合、缓存、场景映射
├── repository.py                  # FileLeaderboardRepository — 本地缓存
├── scenarios.py                   # 场景定义 & 榜单→场景映射
├── api.py                         # Flask Blueprint
└── schemas.py                     # 内部数据模型

web/src/features/leaderboards/     # 前端榜单汇聚页面
├── api.ts
├── types.ts
├── demoData.ts
├── LeaderboardPage.vue            # 主页面
├── ScenarioCard.vue               # 场景卡片
├── ModelRankingTable.vue          # 模型排名表格
└── LeaderboardSourceBadge.vue     # 数据源标签

configs/leaderboards.yaml          # 数据源配置
data/leaderboards/                 # 本地缓存（.gitignore）
```

### 5.2 核心抽象

```python
# src/iqradar/leaderboards/base.py
class LeaderboardSource(ABC):
    """一个外部榜单数据源。"""
    source_id: str                  # 唯一标识，如 "llm-stats"
    display_name: str               # 显示名称，如 "LLM-Stats"
    refresh_interval_hours: int     # 默认刷新间隔
    scenario_weights: dict[str, float]  # 场景→权重映射

    @abstractmethod
    async def fetch(self) -> list[LeaderboardEntry]:
        """拉取最新数据，返回归一化后的榜单条目。"""
        ...

    @abstractmethod
    def cached_data_path(self) -> Path:
        """本地缓存文件路径。"""
        ...

class LeaderboardEntry(BaseModel):
    """归一化的榜单条目（数据源内部统一的格式）。"""
    source_id: str
    model_name: str                 # 归一化模型名
    model_display_name: str
    provider: str | None            # 如 OpenAI, Anthropic
    benchmark_name: str             # 如 swe_bench_verified, gpqa
    benchmark_label: str            # 如 "SWE-bench Verified"
    score: float
    score_label: str                # 如 "pass@1", "accuracy"
    rank: int | None
    metadata: dict[str, Any] = {}   # 额外信息（成本、延迟等）
    fetched_at: datetime
```

### 5.3 数据流

```
外部 API ──→ LeaderboardSource.fetch() ──→ 归一化 LeaderboardEntry[]
                                                │
                                          本地缓存 JSON
                                                │
                                    LeaderboardService.scenario_rankings()
                                                │
                                    ┌───────────┴───────────┐
                                    ▼                       ▼
                             场景-模型排名表          模型详细卡片
                           (前端 LeaderboardPage)   (前端 ScenarioCard)
```

### 5.4 模型名归一化

不同榜单对同一模型命名不同（如 `deepseek-r1` vs `DeepSeek-R1` vs `deepseek/deepseek-r1`）。需要维护一个映射表：

```yaml
# configs/leaderboards.yaml 中的 model_name_aliases
model_name_aliases:
  deepseek-r1:
    - deepseek-r1
    - DeepSeek-R1
    - deepseek/deepseek-r1
    - deepseek-r1-0528
  gpt-4o:
    - gpt-4o
    - GPT-4o
    - openai/gpt-4o
    - gpt-4o-2024-08-06
```

### 5.5 缓存策略

- 每次请求先读本地缓存
- 缓存超过 `refresh_interval_hours` 时，后台异步触发刷新
- 支持手动刷新：`POST /api/leaderboards/refresh`
- 刷新失败时继续使用旧缓存（不阻塞页面）
- 无网络时完全使用本地缓存

### 5.6 与现有模块的归属关系

```
iq-radar app 组成：
  ├── 大盘模块 (reporting)          ← 只读快照
  ├── 测试模块 (deepswe)            ← 评测执行
  └── 榜单汇聚模块 (leaderboards)    ← 外部榜单（新增）
```

**边界规则**（与现有 architecture 一致）：
- `leaderboards` 不得引用 `deepswe`、`reporting`、`publication`
- `leaderboards` 可以引用 `config`（加载配置）、`shared`（工具函数）
- `leaderboards` 可以引用 `schemas` 中的 `RunRecord`（用于叠加自测数据）
- 榜单模块的数据是"只读汇聚"，不修改外部数据

---

## 6. 前端设计

### 6.1 页面布局

```
App.vue 新增第三个 tab："榜单"

LeaderboardPage.vue 页面结构：
  ┌──────────────────────────────────────────────────┐
  │  场景选择器（8 个场景标签页）                      │
  │  [办公效率] [创意写作] [后端开发] [前端] [运维] ... │
  │                                                   │
  │  ┌──────────────────────────────────────────────┐ │
  │  │  场景 KPI 摘要（该场景覆盖模型数/已缓存榜单数） │ │
  │  └──────────────────────────────────────────────┘ │
  │                                                   │
  │  ┌──────────────┐  ┌───────────────────────────┐ │
  │  │ 能力雷达图    │  │ 模型排名表格               │ │
  │  │ (ECharts)    │  │ ┌─────┬──────┬──────┬───┐ │ │
  │  │              │  │ │排名 │ 模型 │ 得分 │来源│ │ │
  │  │              │  │ ├─────┼──────┼──────┼───┤ │ │
  │  │              │  │ │ #1  │  XX  │ 0.95 │ S │ │ │
  │  │              │  │ │ #2  │  YY  │ 0.92 │ L │ │ │
  │  └──────────────┘  │ └─────┴──────┴──────┴───┘ │ │
  │                    └───────────────────────────┘ │
  │                                                   │
  │  数据源状态栏（各榜单最后一次刷新时间/状态）       │
  └──────────────────────────────────────────────────┘
```

### 6.2 前端组件树

```
LeaderboardPage.vue
├── ScenarioTabs              — 场景标签页
├── ScenarioKPIs              — 场景 KPI 卡片
├── ScenarioRadarChart        — 能力雷达图（ECharts）
├── ModelRankingTable         — 模型排名表（多列排序）
│   └── LeaderboardSourceBadge — 数据源标签
├── DataSourceStatusBar        — 数据源状态
└── RefreshButton             — 手动刷新
```

### 6.3 API 端点

```
GET /api/leaderboards/scenarios              — 场景列表
GET /api/leaderboards/scenarios/{scenario_id} — 某场景排名
GET /api/leaderboards/models/{model_name}     — 某模型所有榜单得分
GET /api/leaderboards/sources                 — 数据源状态
POST /api/leaderboards/refresh                — 强制刷新
```

---

## 7. 配置设计

```yaml
# configs/leaderboards.yaml
sources:
  - id: llm-stats
    enabled: true
    api_key_env: LLM_STATS_API_KEY       # 从 .env 读取
    base_url: https://api.llm-stats.com/stats
    refresh_interval_hours: 12
    benchmarks:
      - swe_bench_verified
      - gpqa
      - aime_2025
      - mmlu_pro
      - live_code_bench

  - id: openrouter
    enabled: true
    api_key_env: OPENROUTER_API_KEY
    refresh_interval_hours: 24
    categories: ["coding", "productivity"]

  - id: arena-ai
    enabled: true
    base_url: https://api.wulong.dev/arena-ai-leaderboards/v1
    refresh_interval_hours: 24
    leaderboards: ["text", "code", "vision"]

  - id: artificial-analysis
    enabled: true
    refresh_interval_hours: 24

scenarios:
  - id: office
    label: 办公效率
    icon: FileText
    description: 文档处理、表格、邮件、会议纪要
    weights:
      mmlu_pro: 0.4
      hle: 0.2
      arena_text: 0.2
      openrouter_productivity: 0.2

  - id: writing
    label: 创意写作
    icon: PenLine
    description: 文案、文章、翻译、润色
    weights:
      alpaca_eval: 0.4
      arena_text: 0.3
      eq_bench: 0.3

  # ... 更多场景

model_name_aliases:
  # ... 模型名归一化映射
```

---

## 8. 实施路线图

### Phase 1（MVP）— 核心数据源 + 基础页面

- 实现 `LeaderboardSource` 基类
- 实现 `LLM-Stats` adapter（核心数据源）
- 实现 `Arena AI` adapter（社区排名补充）
- 实现 `FileLeaderboardRepository`（本地缓存）
- 实现场景定义和映射
- 前端 `LeaderboardPage.vue` 基础版（场景切换 + 排名表格 + 雷达图）
- 配置 `configs/leaderboards.yaml`

### Phase 2 — 增强数据源

- 实现 `OpenRouter` adapter（应用分类排名）
- 实现 `Artificial Analysis` adapter（成本/速度维度）
- 实现 `HF Leaderboard` adapter（GAIA、MTEB 等）
- 前端增强：数据源状态栏、模型详情弹窗

### Phase 3 — 高级功能

- 自测数据与外部榜单叠加对比
- 历史趋势追踪（榜单变化曲线）
- 模型名归一化改进（自动匹配）
- 自定义场景配置（用户在 UI 中配置）

---

## 9. API 验证结果

### 9.1 LLM-Stats API ⭐⭐⭐

**基础 URL**: `https://api.llm-stats.com/stats/v1`

**OpenAPI**: `https://api.llm-stats.com/stats/openapi.json` ✅ 已验证

```bash
# 获取所有 benchmark（需要 API key）
curl -H "Authorization: Bearer YOUR_KEY" https://api.llm-stats.com/stats/v1/benchmarks
```

**端点清单**：
- `GET /v1/models` — 列出所有模型
- `GET /v1/models/{model_id}` — 获取单个模型详情
- `GET /v1/benchmarks` — 列出所有基准
- `GET /v1/scores` — 获取分数
- `GET /v1/rankings` — 获取排名
- `GET /v1/updates` — 更新记录

**认证**：需要 API key，免费注册 → https://llm-stats.com/settings?tab=api-keys
**文档**: https://docs.llm-stats.com/api-reference/introduction

### 9.2 Arena AI Leaderboards ⭐⭐⭐（已验证，推荐首选）

**基础 URL**: `https://api.wulong.dev/arena-ai-leaderboards/v1`

**已验证**：完全免费，无需认证，稳定返回 JSON。

```bash
# 获取所有 leaderboard 列表
curl https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboards
# 返回：code (50 models), text (20), vision (39), document (39),
#       text-to-image (76), agent (10), search (32), image-edit (52),
#       text-to-video (45), image-to-video (45), video-edit (9)

# 获取 code 排行榜（含 ELO 分数、置信区间、投票数）
curl https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboard?name=code

# 获取 text 排行榜
curl https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboard?name=text

# 获取 agent 排行榜（Agent 自动化场景特别有用）
curl https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboard?name=agent
```

**数据格式**（已验证）：
- **code/text 榜**：ELO 评分 + 置信区间 + 投票数
- **agent 榜**：6 维能力得分（Net Improvement、Confirmed Success、Praise vs Complaint、Steerability、Bash Recovery、Tool Hallucination）+ session 数
- **vision 榜**：ELO 评分 + 置信区间

**GitHub**: https://github.com/oolong-tea-2026/arena-ai-leaderboards

### 9.3 OpenRouter Data API ⭐⭐

**基础 URL**: `https://openrouter.ai/api/v1`

```bash
# 模型日排名（按 token 用量）
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://openrouter.ai/api/v1/datasets/rankings-daily"

# 应用排名（按分类，例如 coding）
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://openrouter.ai/api/v1/datasets/app-rankings?sort=popular&category=coding"
```

**认证**：需要 OpenRouter API key（免费注册），30 req/min，500 req/day
**许可**：CC BY 4.0
**文档**: https://openrouter.ai/docs/cookbook/administration/data-api

### 9.2 OpenRouter Data API

**基础 URL**: `https://openrouter.ai/api/v1`

```bash
# 模型日排名（按 token 用量）
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://openrouter.ai/api/v1/datasets/rankings-daily?start_date=2026-08-01&end_date=2026-08-17"

# 应用排名（按分类）
curl -H "Authorization: Bearer YOUR_KEY" \
  "https://openrouter.ai/api/v1/datasets/app-rankings?sort=popular&category=coding"
```

**文档**: https://openrouter.ai/docs/cookbook/administration/data-api

### 9.3 Arena AI Leaderboards

**基础 URL**: `https://api.wulong.dev/arena-ai-leaderboards/v1`

```bash
# 获取所有 learderboard 列表
curl https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboards

# 获取 code 排行榜
curl https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboard?name=code

# 获取 text 排行榜
curl https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboard?name=text
```

**GitHub**: https://github.com/oolong-tea-2026/arena-ai-leaderboards

### 9.4 Artificial Analysis

**基础 URL**: `https://artificialanalysis.ai/api/v2`

- 免费，无需 API key
- 覆盖模型排名、推理、速度、成本、上下文窗口
- API 文档: https://artificialanalysis.ai/api-reference

### 9.5 HuggingFace Leaderboard API ⭐⭐（已验证）

HF 提供官方 Leaderboard 数据 API，可通过 REST 或 Python `huggingface_hub` 库获取。

```bash
# 发现所有官方 benchmark 数据集
GET https://huggingface.co/api/datasets?filter=benchmark:official

# 获取某个 benchmark 的排名
GET https://huggingface.co/api/datasets/SWE-bench/SWE-bench_Verified/leaderboard
```

```python
from huggingface_hub import HfApi

api = HfApi()
leaderboard = api.get_dataset_leaderboard("SWE-bench/SWE-bench_Verified")
for entry in leaderboard[:5]:
    print(f"#{entry.rank} {entry.model_id}: {entry.value}")
```

**文档**: https://huggingface.co/docs/hub/en/leaderboard-data-guide