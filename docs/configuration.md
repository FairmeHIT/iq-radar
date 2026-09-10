# IQRadar Local Catalog

The dashboard reads local data files. It never stores credentials or starts model calls.

| Resource | Location | Maintenance |
|---|---|---|
| DeepSWE checkout | `checkouts/deep-swe` (configurable in `configs/benchmark.yaml`) | Keep the checkout that contains `tasks/` and a `.env` with `OPENAI_BASE_URL`/`OPENAI_API_KEY`. |
| Eval datasets | `data/datasets/` | Git-bundled tasks are symlinks into `checkouts/`; HF sets are written by `scripts/prepare_*.py`. `start.sh` recreates the symlinks. |
| Benchmark defaults | `configs/benchmark.yaml` | Maintain task root, DeepSWE dir, timeout, and job output root. |
| Model catalog | `IQRADAR_MODEL_NAMES` in `.env` (preferred), else gateway `/v1/models` auto-discovery, else `configs/models.yaml` | `IQRADAR_MODEL_NAMES` is a comma/space-separated list of gateway model names; unset → discover from `{GATEWAY_BASE_URL}/models`; unreachable → YAML catalog. |
| Prices | `configs/prices.yaml` | Set token prices and weekly quota budget for IQ/quota metrics. |

## DeepSWE Benchmark

`configs/benchmark.yaml` locates the DeepSWE checkout to run:

```yaml
benchmarks:
  deep-swe:
    repo_url: https://github.com/datacurve-ai/deep-swe.git
    local_path: checkouts/deep-swe
    tasks_path: data/datasets/deep-swe
    default_timeout_sec: 7200
    default_concurrency: 1
    artifact_root: data/deepswe/jobs
```

`local_path` is also where the deep-swe `.env` is expected
(`local_path/.env`, supplying `OPENAI_BASE_URL`/`OPENAI_API_KEY`). Pier is
invoked once per run with `--env-file <local_path>/.env`, so the model API and
credentials follow the standalone deep-swe CLI flow. The test page loads
candidate models from `configs/models.yaml`; `GATEWAY_MODEL_NAME` in
`.env` remains the local probe/fallback default. Both point at the local
Bifrost gateway (model `gateway/deepseek-v4-flash`): host-side `.env` uses
`http://localhost:8080/v1`, while the deep-swe `.env` used by the no-network
agent containers must use the Docker bridge gateway
`http://172.17.0.1:8080/v1`.

## api-eval Benchmarks (轻量 API 评测)

`type: api-eval` 的基准（GPQA Diamond、AIME 2024、MMLU-Pro、ARC-AGI-2）不经
Docker/子进程，直连 Bifrost 网关逐题 `chat/completions` 打分。`tasks_path`
指向 `data/datasets/api-eval/` 下的题目集 JSONL（每行一题），由
`scripts/prepare_api_eval.py` 从 HuggingFace 准备：

```bash
uv run --with datasets python scripts/prepare_api_eval.py --preset mmlupro
uv run --with datasets python scripts/prepare_api_eval.py --preset arcagi2
```

题目行字段：`task_id` / `prompt` / `reference`，可选 `language` / `match`
（`exact | set | numeric | judge | grid`）/ `extract`
（`raw | last_line | boxed | final | json`）/ `tolerance`。

- **MMLU-Pro**（`TIGER-Lab/MMLU-Pro`，12032 题）：10 选项 A–J 知识题，
  `match=exact` + `extract=final`（判分含字母兜底，支持到 J）。
- **ARC-AGI-2**（`arc-agi-community/arc-agi-2` 的 `test` split，官方公开评测
  120 题 / 173 用例）：抽象栅格推理；栅格以 JSON 文本喂入，无需多模态。
  `match=grid`（结构判分）+ `extract=json`（从回答中抽取 JSON 栅格）。

注意：`--split` 缺省跟随 preset（MMLU-Pro/ARC-AGI-2 为 `test`），旧版脚本的
argparse 默认值 `"train"` 会遮蔽 preset split，勿回退该默认值。

### 网关失败重测（gateway-failure retest）

测试页对 api-eval 基准提供「网关失败重测」开关（评测参数卡，默认开启，可调
1–10 轮上限，缺省 2）。语义：**等全部题目跑完后**检查 `results.jsonl`，只对因
上游网关超时/临时错误失败的题目重新调用网关并原位替换结果；**模型答错的题目
不会重测**。请求字段：`retry_gateway_failures`（布尔）、`gateway_retry_rounds`
（1..10），单 run / 批量 / 多基准并发三类提交均支持，配置随 run 与批次状态
持久化（接续沿用）。

判定为「网关失败、值得重测」的结果（`src/iqradar/benchmarks/api_eval.py` 的
`is_gateway_failure`）：

- `runner_error` + 临时 HTTP 状态码（408/425/429/全部 5xx）；401/403/404 等
  配置类错误不重测（重测无意义）；
- `runner_error` + 传输层错误（URLError/Timeout/StreamError/响应解析失败）；
- `verifier_error` + `empty_response`（网关调用成功但没返回任何内容）；
- `timeout` 占位（整个 run 到达时限，该题根本没被执行过）；
- `failed` + `judge_error`（模型已作答，但 LLM judge 的网关调用失败）。

不重测：`passed`；模型答错的 `failed`（`*_match` / `judge_incorrect`，以及模型
有输出但抽不出答案的 `empty_response`）。重测过程写入运行日志（`检测到 N 题因
网关超时/临时错误失败…` / `[重测 i/N] …`），题目进度在重测期间先回退为已稳定
题数、随重测完成回到满值。Docker 基准（deep-swe / terminal-bench 等）不支持
按题重测，开关不展示、参数接受但忽略。
