# API-Eval Benchmark Adapter — Design & TDD Plan

> 目标：新增一个 **零 Docker、零子进程、零 checkout** 的轻量基准后端
> `type: api-eval`，直连 Bifrost 网关（OpenAI 兼容 `/v1/chat/completions`），
> 用 exact-match 对"回答正确与否"打分，产出与 DeepSWE 完全同构的
> `RunRecord`，从而复用现有的 `aggregate → publish → snapshot → 雷达` 全链路。
> IQ 口径、Wilson 置信区间、成本/额度指标都不改。

---

## 1. 定位与不改的东西

**改动只在执行侧**：`benchmarks/` 下加一个 adapter，注册进 `build_backends`。
以下全部保持不变：

- `RunRecord` / `AggregateSummary` 数据契约（`schemas/*`）；
- `aggregate_runs` 的 `IQ = pass_rate_percent * 1.5` 与 Wilson 区间（`metrics/*`）；
- 大盘/雷达的展示与发布流程（`reporting/`、`publication/`）；
- 架构边界：`benchmarks/` 仍不得 import `reporting`/`publication`
  （`tests/architecture/test_module_boundaries.py` 已强制）。

`api-eval` 与 DeepSWE 的唯一区别是**"通过率从哪来"**：DeepSWE 来自容器内
agent 的 verifier，api-eval 来自"模型答案 vs 参考答案"的归一化精确匹配。

---

## 2. 关键设计决策

### 2.1 运行模型：线程内同步 HTTP（无子进程、无 Docker）

`DeepSweService._execute` 已经把 `backend.run()` 放到一个 daemon 线程里跑，
所以 api-eval 的 `run()` **不需要再 fork 子进程**，直接在线程内：
`采样题目 → 逐题 POST 网关 → 打分 → 落盘`。相比 pier/docker，这是"轻"的根源。

### 2.2 配置 schema 扩展（最小改动）

- `config/schema.py`：`BenchmarkType` 增加 `"api-eval"`。
- **复用现有字段**，不新增必填字段：
  - `tasks_path` → 指向题目集 JSONL 文件（`anchor_benchmark_config` 已锚定相对路径）；
  - `local_path` → 数据根目录占位（api-eval 不读 `env_file`，用 `GATEWAY_*` 环境变量）；
  - `repo_url` → 数据集出处（写入 `benchmark.repo`）；
  - `artifact_root` → 运行产物目录（真实使用）；
  - `default_timeout_sec` / `default_concurrency` → 真实使用（MVP 并发固定 1）。

> 说明：不引入 `dataset_path` 新字段，是为了**不触碰 `anchor_benchmark_config`
> 与 `BenchmarkConfig` 的既有必填约束**；`tasks_path` 语义从"目录"扩展为
> "题目集文件或目录"，加载器按 `.is_file()` 判定。

### 2.3 题目集 JSONL 契约

一行一题，最小必填 `task_id` / `prompt` / `reference`：

```jsonl
{"task_id": "demo-0001", "prompt": "1 + 1 = ?（只回答数字）", "reference": "2", "language": "zh", "match": "exact"}
{"task_id": "demo-0002", "prompt": "列出小于 5 的质数", "reference": "2,3", "language": "zh", "match": "set"}
{"task_id": "demo-0003", "prompt": "估算 3.14159 取整到整数", "reference": "3", "language": "zh", "match": "numeric", "tolerance": 0.5}
```

可选字段：`language`（默认 `"en"`）、`match`（`exact`|`set`|`numeric`，默认 `exact`）、
`tolerance`（`numeric` 用，默认 `1e-6`）。

### 2.4 打分：归一化 + 三种 match

`normalize_answer(text)`：`strip → 去首尾引号/句号 → NFKC → lowercase →
去 LaTeX 包裹（`\boxed{}`、`$…$`、`\(…\)`）→ 压缩空白 → 数字去千分位逗号、
去尾随 `.0`。`score_item()` 按 `match` 分派：

- `exact`：`normalize(model) == normalize(reference)`；
- `set`：以 `,;/` 分词后比较集合；
- `numeric`：解析 float，`abs(a-b) <= tolerance`。

### 2.5 网关客户端（stdlib urllib，零新依赖）

项目 deps 只有 `flask/pydantic/pyyaml/typer`，所以用 `urllib.request`（与
`cli.py probe`、`deepswe/api.py` 一致），**不引入 requests/httpx**。

```python
def gateway_complete(
    base_url: str, api_key: str, model: str, prompt: str,
    *, timeout_sec: int = 60, temperature: float = 0.0,
) -> tuple[str | None, dict[str, int], str | None]:
    """返回 (content, usage, error)。content 为 None 时表示请求失败。"""
```

- POST `{base_url}/chat/completions`，`Authorization: Bearer <key>`（有 key 时）；
- 读 `choices[0].message.content`，为空时回退 `message.reasoning_content`；
- 读 `usage.prompt_tokens` / `completion_tokens` /
  `usage.prompt_tokens_details.cached_tokens`，缺失时 `usage_estimated=True`；
- HTTPError/OSError → 返回 `(None, {}, f"{type}:{msg}")`。

### 2.6 产物布局与 `run()` / `import_records()` 分离

沿用 deep-swe 的"run 写原始产物 → import 建 RunRecord"分层，使 import 可
无网络单测：

```text
<jobs_root>/<run_id>/
  run.log          ← 进度日志（也是 service 传入的 log_path）
  result.json      ← {"finished_at": ...}  ← job_finished() 判据
  results.jsonl    ← 每行一个任务结果（import_records 的输入）
```

`run()` 只写 `results.jsonl`（含 `status`/`usage`/`wall_time_sec`/`error_*` 与
原始 `prompt`/`reference`/`response`）；`import_records()` 读回并组 `RunRecord`。

### 2.7 重启对账（关键回归点）

api-eval **没有子进程**，默认 `runner_alive()` 走 `process_alive(marker)` 会
恒为 False——而测试页每 3s 轮询 `get()`，一旦运行超过 `RECONCILE_AGE_SEC=60`
且 `job_finished()==False` 且 `runner_alive()==False`，正在跑的 run 会被**误判
为 failed**。

因此 `ApiEvalBackend`：

- 维护 `self._active: set[str]`，`run()` 进入时 add、`finally` 中 discard；
- 覆写 `runner_alive(run_id)` → `run_id in self._active`（服务重启后集合为空 →
  正确判 failed）；
- `job_finished(run_id)` → `result.json` 存在且含 `finished_at`。

### 2.8 `base_url_hash` 口径

`service.records()` 用 `run.base_url` 做 hash，而 `run.base_url` 在单 run 提交
路径来自 `service.base_url`（默认 backend，即 deep-swe 的 `172.17.0.1:8080/v1`），
不是 api-eval 实际请求的 `GATEWAY_BASE_URL`。为保证 hash 反映真实端点，做一处
最小改动：`deepswe/api.py` 的 `submit_run()` 按**所选 benchmark** 取 base_url
（`submit_batch` 已用 `_backend_base_url(benchmark)`，单 run 路径对齐即可）。

### 2.9 `RunRecord` 字段映射

| 字段 | 来源 |
|---|---|
| `run_id` | `f"{config_key}__{task_id}__{model_id}__{effort}"` |
| `benchmark.name` | config key（如 `gpqa-diamond`） |
| `benchmark.version` | `"local"` |
| `benchmark.task_id` | item.task_id |
| `benchmark.repo` | config.repo_url |
| `benchmark.language` | item.language 或 `"en"` |
| `benchmark.task_path` | `str(dataset_path)` |
| `model.provider` | `"openai-compatible"` |
| `model.base_url_hash` | service 传入 |
| `model.name` | model_id |
| `model.effort_requested` | 默认 `"high"` |
| `model.effort_effective` | `False`（MVP 不发 reasoning_effort） |
| `result.status` | `passed`/`failed`/`verifier_error`/`runner_error`/`timeout`/`skipped` |
| `result.verifier_passed` | `status == "passed"` |
| `result.error_type` | 空响应→`verifier_error`；HTTP 错→异常类名；超时→`timeout` |
| `usage.input/output/cached_input_tokens` | usage 字段（缺失置 0） |
| `usage.agent_steps` | `1`（单轮问答计 1 步） |
| `usage.wall_time_sec` | 实测 monotonic 差 |
| `usage.usage_estimated` | usage 缺失时为 True |
| `cost.*` | 全 0（`aggregate_runs` 用价格表按 tokens 重算，与 deep-swe 同） |
| `artifacts` | `patch_path=None`；`log_path=run.log`；`verifier_path=results.jsonl` |
| `created_at` | now UTC |

状态口径（与 deep-swe 一致：只有 `passed` 计入通过，`skipped` 不进分母）：
正确→`passed`；错误→`failed`；空/不可解析响应→`verifier_error`；
请求失败→`runner_error`；单题超时→`timeout`。

---

## 3. 新增/改动文件清单

| 文件 | 动作 |
|---|---|
| `src/iqradar/benchmarks/api_eval.py` | 新增（backend + item + normalize/score + gateway_complete） |
| `src/iqradar/benchmarks/registry.py` | 改：注册 `"api-eval"`、加 `TYPE_LABELS`、import |
| `src/iqradar/config/schema.py` | 改：`BenchmarkType` 加 `"api-eval"` |
| `src/iqradar/deepswe/api.py` | 改：`submit_run()` 按所选 benchmark 取 base_url |
| `configs/benchmark.yaml` | 改：新增 `api-eval` 示例块 |
| `tests/unit/test_api_eval.py` | 新增 |
| `tests/integration/test_api.py` | 改：加 api-eval 提交/记录/长跑对账用例 |
| `tests/unit/test_benchmarks.py` | 改：注册表/label/配置加载断言 |

> 示例题目集放 `data/datasets/api-eval/`（gitignore），文档内给出样例；不随代码备份，
> 可按需准备 GPQA/AIME 等真实子集。

---

## 4. 配置示例（`configs/benchmark.yaml` 追加）

```yaml
  gpqa-diamond:
    type: api-eval
    repo_url: https://huggingface.co/datasets/Idavidrein/gpqa
    local_path: data/datasets/api-eval
    tasks_path: data/datasets/api-eval/gpqa-diamond.sample.jsonl
    default_timeout_sec: 3600
    default_concurrency: 1
    artifact_root: data/api-eval/jobs
```

---

## 5. RED/GREEN 测试清单

| # | Guarantee | Test | Type |
|---|---|---|---|
| 1 | `type="api-eval"` 是合法配置，能构建 | `test_api_eval_config_constructs` | Unit |
| 2 | registry 按 `api-eval` 构建 `ApiEvalBackend` | `test_build_backends_registers_api_eval` | Unit |
| 3 | `label("gpqa-diamond", "api-eval")` 有非空标签 | `test_api_eval_label` | Unit |
| 4 | 加载 JSONL 题目集 | `test_load_dataset_reads_jsonl` | Unit |
| 5 | 题目集文件缺失返回空列表（不抛） | `test_load_dataset_missing_returns_empty` | Unit |
| 6 | 采样确定、seed 可复现、n 超量返全量 | `test_sample_items_deterministic` | Unit |
| 7 | `normalize_answer` 去空白/大小写/引号/句号 | `test_normalize_answer_basic` | Unit |
| 8 | `normalize_answer` 去 `\boxed{}`/`$` | `test_normalize_answer_latex` | Unit |
| 9 | `normalize_answer` 数字去逗号与尾随 `.0` | `test_normalize_answer_numbers` | Unit |
| 10 | `score_item` exact 命中/未命中 | `test_score_exact` | Unit |
| 11 | `score_item` set 无序命中 | `test_score_set` | Unit |
| 12 | `score_item` numeric 容差命中 | `test_score_numeric` | Unit |
| 13 | `gateway_complete` 解析 content+usage | `test_gateway_complete_parses` | Unit |
| 14 | `gateway_complete` HTTP 错误→error、content=None | `test_gateway_complete_http_error` | Unit |
| 15 | `gateway_complete` 缺 usage→usage_estimated | `test_gateway_complete_missing_usage` | Unit |
| 16 | `run()` 写 result.json(finished_at)+results.jsonl，返回 0 | `test_run_writes_results`（monkeypatch gateway） | Unit |
| 17 | `run()` cancel_event 提前返回非 0 | `test_run_cancels` | Unit |
| 18 | `import_records()` 从 results.jsonl 组 RunRecord，字段正确 | `test_import_records_builds_run_records` | Unit |
| 19 | `job_finished()` 依据 result.json 的 finished_at | `test_job_finished_checks_result_json` | Unit |
| 20 | `runner_alive()` 跟踪 active 集合 | `test_runner_alive_tracks_active` | Unit |
| 21 | `model_name` 去 `openai/` 前缀得到网关模型名 | `test_gateway_model_name_strips_provider` | Unit |
| 22 | 服务：api-eval 长跑（>RECONCILE_AGE）不被误判 failed | `test_api_eval_long_run_not_misclassified` | Integration |
| 23 | API：`POST /api/deepswe-runs` benchmark=api-eval 走 `ApiEvalBackend` | `test_api_submit_api_eval_run` | Integration |
| 24 | API：records 端点返回 exact-match 打分结果 | `test_api_eval_records_endpoint` | Integration |
| 25 | `configs/benchmark.yaml` 含 api-eval 块 | `test_load_benchmark_yaml_includes_api_eval` | Unit |
| 26 | 架构：`benchmarks/` 仍不 import reporting/publication | 既有 `test_module_boundaries` | 架构 |

---

## 6. 落地步骤（执行顺序）

1. **Task 1** — schema：`BenchmarkType` 加 `"api-eval"`；跑红→绿 `#1`。
2. **Task 2** — registry：注册 backend + label；绿 `#2/#3`。
3. **Task 3** — `api_eval.py` 纯函数层：`ApiEvalItem`/`load_dataset`/`sample_items`/
   `normalize_answer`/`score_item`；绿 `#4-#12`。
4. **Task 4** — 网关客户端 `gateway_complete`；绿 `#13-#15`。
5. **Task 5** — `ApiEvalBackend`（run/import_records/job_finished/runner_alive/
   base_url）；绿 `#16-#21`。
6. **Task 6** — `deepswe/api.py` base_url 口径对齐；绿 `#22-#24`。
7. **Task 7** — `configs/benchmark.yaml` 示例 + 文档；绿 `#25`；全量 `pytest` 绿。

---

## 7. 已知取舍与 Phase 2

- **effort**：MVP 固定 `"high"`、`effort_effective=False`；后续读 `ModelConfig.effort`
  发 `reasoning_effort`。
- **judge 打分**：MVP 只 exact-match；`scoring: judge`（LLM 裁判）与答案抽取器
  （`extract` 字段，处理 reasoning 模型的"思考+最终答案"）留作 Phase 2。
- **并发**：MVP `n_concurrent=1` 顺序请求；后续用 `ThreadPoolExecutor` 并行。
- **n_tasks 上界**：blueprint 目前硬编码 `<=117`（DeepSWE 专用）；若单批要跑
  超 117 题，给 `BenchmarkBackend` 暴露 `max_tasks`（默认 117）并按所选 benchmark
  校验。
- **成本字段**：单条 `RunRecord.cost` 置 0，真实成本由 `aggregate_runs` 按
  `prices.yaml` 重算（与 deep-swe 一致）；若大盘运行明细表要显示单题成本，Phase 2
  在 import 时直接带价格表计算。

---

## 8. 验证命令

```bash
uv run pytest tests/unit/test_api_eval.py -v
uv run pytest tests/unit/test_benchmarks.py tests/integration/test_api.py -v
uv run pytest -v
```
