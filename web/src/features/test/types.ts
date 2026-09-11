export interface BenchmarkChoice {
  id: string
  type: string
  label: string
  /** 可取样任务池大小（test_tasks 固定子集时为其大小；无池概念时为 null） */
  task_count?: number | null
  /** 配置的默认任务级并发数（api-eval 运行时可被请求体覆盖） */
  default_concurrency?: number | null
  /** "api-eval"（HTTP，可多 run 并发）| "docker"（容器化，并发会争抢资源） */
  category?: string
}

export interface ModelChoice {
  id: string
  display_name: string
  provider?: string
  label?: string
  efforts: string[]
}

/** GET /api/models 响应：待测模型列表 + 来源（网关配置的 GET /v1/models 优先） */
export interface ModelsResponse {
  /** gateway=网关配置的 /v1/models；env-names=IQRADAR_MODEL_NAMES；yaml=本地目录回退；default=内置默认 */
  source: 'gateway' | 'env-names' | 'yaml' | 'default'
  /** 网关不可达等原因（走回退时非空，供页面提示） */
  error: string | null
  models: ModelChoice[]
}

/** 题目级进度：completed 为已完成题数（含失败/出错），running/pending 后端可得时附带 */
export interface RunProgress {
  total: number
  completed: number
  running?: number
  pending?: number
}

export interface ApiRunMetrics {
  avg_wall_time_sec?: number | null
  avg_first_token_sec?: number | null
  avg_first_content_sec?: number | null
  output_tokens_per_sec?: number | null
  input_tokens?: number | null
  output_tokens?: number | null
  cached_input_tokens?: number | null
}

export interface DeepSweRun {
  run_id: string
  status: 'queued' | 'running' | 'completed' | 'failed'
  model_id: string
  n_tasks: number
  sample_seed: number
  created_at: string
  completed_at: string | null
  error: string | null
  jobs_dir: string | null
  benchmark: string
  effort: string
  n_concurrent?: number | null
  /** 题目进度（后端能确定时才有；未知为 null，前端回退展示任务总数） */
  progress?: RunProgress | null
  /** 网关失败重测（仅 api-eval 生效；旧记录为 null） */
  retry_gateway_failures?: boolean | null
  /** 重测轮数上限（retry_gateway_failures 开启时生效） */
  gateway_retry_rounds?: number | null
  /** 该 run 是否已发布到大盘（list 接口附带；未发布为 null） */
  snapshot_id?: string | null
  /** 评测记录重测按钮可批量重测的基础设施/调用链失败题数量（仅 api-eval 列表附带）。 */
  retryable_infrastructure_failure_count?: number | null
  /** 是否存在可接续的磁盘中间结果（如 api-eval partial results / terminal-bench-2 harbor job）。 */
  resumable_partial_results?: boolean | null
  /** api-eval 运行聚合后的 API 质量指标，用于评测记录表展示。 */
  api_metrics?: ApiRunMetrics | null
}

export interface DeepSweRunRequest {
  model_id: string
  /** 缺省时后端使用该基准当前可用题池全量。 */
  n_tasks?: number
  sample_seed?: number
  benchmark?: string
  effort?: string
  n_concurrent?: number
  /** 接续中断的 run：api-eval 复用已写 results 前缀，terminal-bench-2 走 harbor job resume。 */
  resume_run_id?: string
  /** 网关失败重测：全部题目跑完后，仅重测因网关超时/临时错误失败的题目 */
  retry_gateway_failures?: boolean
  /** 重测轮数上限（1..10，缺省 2） */
  gateway_retry_rounds?: number
}

export interface BatchModel {
  model_id: string
  model_name: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  run_id: string | null
}

export interface DeepSweBatch {
  batch_id: string
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  n_tasks: number
  sample_seed: number
  created_at: string
  completed_at: string | null
  current_model: string | null
  error: string | null
  snapshot_id: string | null
  models: BatchModel[]
  benchmark: string
  n_concurrent?: number | null
  /** 同时并行跑几个模型 run（1 = 串行）；后端缺省视为 1 */
  max_concurrent?: number
}

export interface BatchRequest {
  model_ids: string[]
  /** 缺省时后端使用该基准当前可用题池全量。 */
  n_tasks?: number
  sample_seed?: number
  benchmark?: string
  effort?: string
  n_concurrent?: number
  /** 模型级并行上限；1 = 串行（缺省） */
  max_concurrent?: number
  /** 网关失败重测（仅 api-eval 生效） */
  retry_gateway_failures?: boolean
  gateway_retry_rounds?: number
}

export interface Publication {
  snapshot_id: string
  source_job_id: string
}

/** POST /api/deepswe-runs/publish-batch 响应：多条 run 合并发布成一个快照 */
export interface RunPublishResult {
  snapshot_id: string
  source_job_id: string
  /** 实际发布成功的 run id（过滤掉非完成/无记录后的子集，顺序同请求） */
  published_run_ids: string[]
}

/** POST /api/deepswe-runs/delete-batch 响应：逐条删除结果 */
export interface RunDeleteResult {
  deleted: string[]
  skipped: { run_id: string; reason: string }[]
}

export interface LogSource {
  name: string
  path: string | null
}

export interface RunLogs {
  sources: LogSource[]
  selected: string | null
  path?: string | null
  content?: string
  /** 题目进度：日志轮询在 run 状态轮询停止后仍持续，借此保持进度展示 */
  progress?: RunProgress | null
}

export interface GatewaySettings {
  models_base_url: string
  models_api_key: string
  inference_base_url: string
  inference_api_key: string
  /** 新评测的推理强度（low/medium/high/max；空 = 前端回退 high） */
  inference_effort?: string
  updated_at?: string
}

export interface GatewaySettingsRequest {
  models_base_url: string
  models_api_key: string
  inference_base_url: string
  inference_api_key: string
  inference_effort?: string
}

export interface GatewayModelsProbe {
  ok: boolean
  base_url: string
  model_count: number
  model_ids: string[]
  latency_ms: number
  error: string | null
}

export interface GatewayChatProbe {
  ok: boolean
  base_url: string
  model: string
  content: string | null
  latency_ms: number
  error: string | null
}

// ── 多基准并发：多个 api-eval bench 同时跑，上限 N 个并发 run ──────────
export interface MultiBenchItemRequest {
  benchmark: string
  model_id: string
  /** 每项独立样本数（按基准粒度覆盖批量级 n_tasks）；缺省回退批量级值。 */
  n_tasks?: number
}

export interface MultiBenchRequest {
  items: MultiBenchItemRequest[]
  max_concurrent: number
  /** 缺省时后端使用所有条目的最小题池数作为批次默认值；条目自身缺省则用各自题池全量。 */
  n_tasks?: number
  sample_seed?: number
  effort?: string
  n_concurrent?: number
  /** 网关失败重测（多基准均为 api-eval） */
  retry_gateway_failures?: boolean
  gateway_retry_rounds?: number
}

export interface MultiBenchItem {
  benchmark: string
  model_id: string
  model_name: string
  /** 该项实际生效的样本数（persisted；旧 batch 可能没有该字段）。 */
  n_tasks?: number
  status: 'pending' | 'running' | 'completed' | 'failed'
  run_id: string | null
}

export interface MultiBenchState {
  batch_id: string
  kind: 'multi-bench'
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  max_concurrent: number
  n_tasks: number
  sample_seed: number
  effort: string
  n_concurrent: number | null
  created_at: string
  completed_at: string | null
  snapshot_id: string | null
  error: string | null
  items: MultiBenchItem[]
}

/** 逐题结果（api-eval run），供评测记录的题目级重测。 */
export interface RunQuestion {
  index: number
  task_id: string
  recorded?: boolean
  status: string
  outcome?: string
  failure_category?: string | null
  error_type?: string | null
  error_message?: string | null
  verifier_passed?: boolean
  [key: string]: unknown
}

export interface EvaluationCoverage {
  requested_tasks: number
  recorded_tasks: number
  missing_tasks: number
  coverage_rate: number
  status_counts: Record<string, number>
  success_count: number
  model_failure_count: number
  infrastructure_error_count: number
  not_executed_count: number
  scored_count_current_metric: number
  pass_rate_current_metric: number
  failure_category_counts: Record<string, number>
  error_type_counts: Record<string, number>
}

export interface EvaluationReport {
  report_version: string
  generated_at: string
  run: DeepSweRun
  coverage: EvaluationCoverage
  questions: RunQuestion[]
  taxonomy: Record<string, string>
}