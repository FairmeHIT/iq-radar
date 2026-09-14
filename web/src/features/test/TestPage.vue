<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { CircleHelp, CircleStop, Play, RefreshCw, Rocket, Trash2 } from 'lucide-vue-next'
import {
  cancelBatch,
  cancelDeepSweRun,
  cancelMultiBatch,
  cancelRetryGatewayFailuresBatch,
  deleteDeepSweRun,
  deleteDeepSweRuns,
  fetchBatch,
  fetchBenchmarks,
  fetchDeepSweRun,
  fetchDeepSweRunPublication,
  fetchDeepSweRuns,
  fetchGatewaySettings,
  fetchLatestBatch,
  fetchLatestDeepSweRun,
  fetchLatestMultiBatch,
  fetchModels,
  fetchMultiBatch,
  fetchRunLog,
  fetchRunLogSources,
  fetchRetryGatewayFailuresBatch,
  fetchEvaluationReport,
  fetchRunQuestions,
  publishDeepSweRun,
  publishDeepSweRuns,
  resumeBatch,
  resumeMultiBatch,
  retryRunQuestions,
  saveGatewaySettings,
  submitBatch,
  submitDeepSweRun,
  submitMultiBench,
  submitRetryGatewayFailuresBatch,
  testGatewayChat,
  testGatewayModels,
  unpublishDeepSweRun,
} from './api'
import type {
  BenchmarkChoice,
  DeepSweBatch,
  DeepSweRun,
  EvaluationReport,
  GatewaySettings,
  LogSource,
  ModelChoice,
  MultiBenchState,
  Publication,
  RunLogs,
  RunProgress,
  RunQuestion,
} from './types'

const models = ref<ModelChoice[]>([])
const modelsError = ref<string | null>(null)
const benchmarks = ref<BenchmarkChoice[]>([])
const benchmarksLoaded = ref(false)
const SELECTED_BENCHMARKS_STORAGE_KEY = 'iqradar:test:selected-benchmarks:v1'

function loadSelectedBenchmarkIds(): string[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(SELECTED_BENCHMARKS_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((id): id is string => typeof id === 'string' && id.trim().length > 0)
  } catch {
    return []
  }
}

function saveSelectedBenchmarkIds(ids: string[]) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(SELECTED_BENCHMARKS_STORAGE_KEY, JSON.stringify(ids))
  } catch {
    /* localStorage 可能被浏览器策略禁用；不影响本次页面内使用 */
  }
}

const SELECTED_MODELS_STORAGE_KEY = 'iqradar:test:selected-models:v1'
const SELECTED_PROVIDER_STORAGE_KEY = 'iqradar:test:selected-provider:v1'

function loadSelectedModelIds(): string[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(SELECTED_MODELS_STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((id): id is string => typeof id === 'string' && id.trim().length > 0)
  } catch {
    return []
  }
}

function saveSelectedModelIds(ids: string[]) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(SELECTED_MODELS_STORAGE_KEY, JSON.stringify(ids))
  } catch {
    /* localStorage 可能被浏览器策略禁用；不影响本次页面内使用 */
  }
}

function loadSelectedProvider(): string {
  if (typeof window === 'undefined') return 'all'
  try {
    return window.localStorage.getItem(SELECTED_PROVIDER_STORAGE_KEY) || 'all'
  } catch {
    return 'all'
  }
}

function saveSelectedProvider(provider: string) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(SELECTED_PROVIDER_STORAGE_KEY, provider)
  } catch {
    /* localStorage 可能被浏览器策略禁用；不影响本次页面内使用 */
  }
}

const selectedBenchmarks = ref<string[]>(loadSelectedBenchmarkIds())
const selectedModels = ref<string[]>(loadSelectedModelIds())
// 供应商筛选：provider 取模型名第一个 "/" 之前的部分（如 gateway/glm-5.2 -> gateway、
// gateway/minimax/minimax-m2.7 -> gateway）。后端 /api/models 已提供 provider 字段，
// 这里再做一次兜底解析（兼容 provider 缺失的旧数据）。
const selectedProvider = ref(loadSelectedProvider())

function modelProvider(model: ModelChoice): string {
  return model.provider || model.id.split('/')[0] || '其他'
}

const providerOptions = computed(() => {
  const providers = new Set<string>(models.value.map(modelProvider))
  return ['all', ...Array.from(providers).sort()]
})

const visibleModels = computed(() => {
  if (selectedProvider.value === 'all') return models.value
  return models.value.filter((model) => modelProvider(model) === selectedProvider.value)
})
// 推理强度：在「网关配置 · 模型调用」里设置，随网关配置持久化。
const effort = ref('high')
// 多模型默认并行：多选模型时同时启动多个模型 run。
const MAX_PARALLEL_RUNS = 16
// 多基准并发模式：勾选多个 api-eval bench，上限 N 个 run 同时跑。
const multiBatchInfo = ref<MultiBenchState | null>(null)
const effortOptions = [
  { value: 'low', label: '低 (low)' },
  { value: 'medium', label: '中 (medium)' },
  { value: 'high', label: '高 (high)' },
  { value: 'max', label: '极高 (max)' },
]
const error = ref<string | null>(null)
const loadingOptions = ref(false)
const running = ref(false)
const cancelling = ref(false)
const resuming = ref(false)
// 评测记录行级操作：正在发布 / 回撤中的 run id 集合（禁用对应按钮）
const publishingRunIds = ref<Set<string>>(new Set())
const execution = ref<DeepSweRun | null>(null)
const batchInfo = ref<DeepSweBatch | null>(null)
const publication = ref<Publication | null>(null)
const pollRequestId = ref(0)
const logSources = ref<LogSource[]>([])
const selectedLogSource = ref('')
const logContent = ref('')
const logError = ref<string | null>(null)
const loadingLog = ref(false)
const logTimer: number[] = []
const logHost = ref<HTMLElement | null>(null)
const logAutoScroll = ref(true)
// 题目进度（总共 N 题 / 已完成 M 题）：来自 run 状态与日志轮询响应；
// 未知（后端给不出，如 Docker 环境构建阶段）为 null，展示回退任务总数。
const runProgress = ref<RunProgress | null>(null)

// ── 评测记录列表：可按状态/模型/基准筛选，手动勾选后批量删除无效结果 ──
const allRuns = ref<DeepSweRun[]>([])
const loadingRuns = ref(false)
const runsError = ref<string | null>(null)
const runStatusFilter = ref('all')
const runModelFilter = ref('all')
const runBenchmarkFilter = ref('all')
const selectedRunIds = ref<string[]>([])
const deletingRuns = ref(false)
const publishingRuns = ref(false)
const retryingGatewayFailures = ref(false)
const retryingGatewayRunId = ref<string | null>(null)
const runsNotice = ref<string | null>(null)

// ── 网关配置（可填入 models 列表获取与模型调用的 baseurl/key，保存即生效）──
const gwModelsBaseUrl = ref('')
const gwModelsApiKey = ref('')
const gwInferenceBaseUrl = ref('')
const gwInferenceApiKey = ref('')
const gwChatModel = ref('')
const gwSavedAt = ref('')
const gwSaving = ref(false)
const gwTestingModels = ref(false)
const gwTestingChat = ref(false)
const gwModelsResult = ref<{ ok: boolean; text: string } | null>(null)
const gwChatResult = ref<{ ok: boolean; text: string } | null>(null)

const runId = computed(() => execution.value?.run_id ?? null)
const showLogs = computed(() => execution.value != null)
// 首次运行某个任务时需要构建 Docker 评测环境（拉取数 GB 基础镜像 + 安装依赖），
// 该阶段 pier 会吞掉 docker compose build 的全部输出（stdout=PIPE，完成后才输出），
// pier.log 保持为空，trial/agent/llm 等日志源也尚未产生——面板会一直显示
// “暂无日志输出”，容易被误认为卡死。运行中且尚无任何 trial 级日志源时给出明确提示。
const isBuildingEnvironment = computed(() => {
  if (!execution.value) return false
  const status = execution.value.status
  if (status !== 'queued' && status !== 'running') return false
  if (batchInfo.value?.status === 'running') return false
  // api-eval 无 Docker 构建阶段，也不产生 trial 级日志源，不适用该提示。
  const benchmark = benchmarks.value.find((b) => b.id === execution.value?.benchmark)
  if (benchmark?.type === 'api-eval') return false
  const names = new Set(logSources.value.map((source) => source.name))
  const trialSources = ['trial', 'agent', 'llm', 'verifier']
  return !trialSources.some((name) => names.has(name))
})
function benchmarkTaskCount(benchmarkId: string): number | undefined {
  const count = benchmarks.value.find((b) => b.id === benchmarkId)?.task_count
  return typeof count === 'number' && count > 0 ? count : undefined
}

// API 轻量基准允许逐数据集设置本次题数；默认跑完整题池。
// 用户输入持久化到 localStorage，避免刷新/重新进入测试页后被还原。
const BENCHMARK_TASK_COUNTS_STORAGE_KEY = 'iqradar:test:benchmark-task-counts:v1'

function loadBenchmarkTaskCounts(): Record<string, number> {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(BENCHMARK_TASK_COUNTS_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as unknown
    if (parsed == null || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const counts: Record<string, number> = {}
    for (const [benchmarkId, value] of Object.entries(parsed)) {
      if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
        counts[benchmarkId] = Math.floor(value)
      }
    }
    return counts
  } catch {
    return {}
  }
}

function saveBenchmarkTaskCounts(counts: Record<string, number>) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(BENCHMARK_TASK_COUNTS_STORAGE_KEY, JSON.stringify(counts))
  } catch {
    /* localStorage 可能被浏览器策略禁用；不影响本次页面内使用 */
  }
}

const benchmarkTaskCounts = ref<Record<string, number>>(loadBenchmarkTaskCounts())

function benchmarkQuestionCount(benchmarkId: string): number | undefined {
  const max = benchmarkTaskCount(benchmarkId)
  if (max == null) return undefined
  const configured = benchmarkTaskCounts.value[benchmarkId]
  return typeof configured === 'number' && Number.isFinite(configured)
    ? Math.max(1, Math.min(max, Math.floor(configured)))
    : max
}

function persistBenchmarkQuestionCount(benchmarkId: string, count: number) {
  benchmarkTaskCounts.value = {
    ...benchmarkTaskCounts.value,
    [benchmarkId]: count,
  }
  saveBenchmarkTaskCounts(benchmarkTaskCounts.value)
}

function setBenchmarkQuestionCount(benchmarkId: string, event: Event, fallbackToMax = false) {
  const max = benchmarkTaskCount(benchmarkId)
  if (max == null) return
  const input = event.target as HTMLInputElement
  if (!input.value.trim() && !fallbackToMax) return
  const parsed = Number(input.value)
  const nextCount = Number.isFinite(parsed) && parsed > 0
    ? Math.max(1, Math.min(max, Math.floor(parsed)))
    : max
  persistBenchmarkQuestionCount(benchmarkId, nextCount)
}

function benchmarkTaskPayload(benchmarkId: string): { n_tasks?: number } {
  const nTasks = benchmarkQuestionCount(benchmarkId)
  return nTasks == null ? {} : { n_tasks: nTasks }
}

const selectedBenchmark = computed(() => selectedBenchmarks.value[0] ?? benchmarks.value[0]?.id ?? 'deep-swe')
const selectedBenchmarkMeta = computed(() =>
  benchmarks.value.find((b) => b.id === selectedBenchmark.value),
)
const selectedBenchmarkMetas = computed(() =>
  selectedBenchmarks.value
    .map((id) => benchmarks.value.find((b) => b.id === id))
    .filter((b): b is BenchmarkChoice => b != null),
)
const isMultiBenchmark = computed(() => selectedBenchmarks.value.length > 1)
const selectedAllApiEval = computed(() =>
  selectedBenchmarkMetas.value.length > 0
  && selectedBenchmarkMetas.value.every((b) => (b.category ?? b.type) === 'api-eval'),
)
// 网关失败重测已从评测参数移除：完成后统一在「评测记录」里按题/全部重测。
// 基准分类：api-eval（HTTP，可多 run 并发）与 docker（容器化，串行）
const apiEvalBenchmarks = computed(() =>
  benchmarks.value.filter((b) => b.category === 'api-eval'),
)
const dockerBenchmarks = computed(() => benchmarks.value.filter((b) => b.category !== 'api-eval'))
// 多基准矩阵提交项 = 勾选 bench × 勾选 model；每个基准沿用自己的本次题数。
const multiBenchItems = computed(() => {
  if (!selectedBenchmarks.value.length || !selectedModels.value.length) return []
  const items: { benchmark: string; model_id: string; n_tasks?: number }[] = []
  for (const benchmark of selectedBenchmarks.value) {
    for (const model_id of selectedModels.value) {
      items.push({ benchmark, model_id, ...benchmarkTaskPayload(benchmark) })
    }
  }
  return items
})
const testPlanSummary = computed(() =>
  `${selectedBenchmarks.value.length || 0} 个基准 × ${selectedModels.value.length || 0} 个模型 = ${multiBenchItems.value.length} 个 run`,
)
function hint(text: string) {
  return text
}

function defaultRunConcurrency(totalRuns: number): number {
  return Math.max(1, Math.min(MAX_PARALLEL_RUNS, totalRuns || 1))
}
function multiBatchKindLabel(batch: MultiBenchState | null | undefined): string {
  return batch?.kind === 'gateway-retry' ? '一键重测' : '多基准并发'
}

const phase = computed(() => {
  if (multiBatchInfo.value?.status === 'running') return { tone: 'running', text: `${multiBatchKindLabel(multiBatchInfo.value)}进行中` }
  if (batchInfo.value?.status === 'running') return { tone: 'running', text: '批量评测进行中' }
  if (execution.value) {
    const status = execution.value.status
    if (status === 'queued') return { tone: 'running', text: '任务已排队' }
    if (status === 'running') return { tone: 'running', text: '评测运行中' }
    if (status === 'completed') return { tone: 'success', text: '评测完成' }
    return { tone: 'danger', text: '评测失败' }
  }
  if (multiBatchInfo.value) {
    const label = multiBatchKindLabel(multiBatchInfo.value)
    if (multiBatchInfo.value.status === 'completed') return { tone: 'success', text: `${label}完成` }
    if (multiBatchInfo.value.status === 'cancelled') return { tone: 'muted', text: `${label}已取消` }
    return { tone: 'danger', text: `${label}失败` }
  }
  if (batchInfo.value) {
    if (batchInfo.value.status === 'completed') return { tone: 'success', text: '批量评测完成' }
    if (batchInfo.value.status === 'cancelled') return { tone: 'muted', text: '批量评测已取消' }
    return { tone: 'danger', text: '批量评测失败' }
  }
  return { tone: 'idle', text: '空闲 · 等待配置' }
})
const availableLogSources = computed(() =>
  logSources.value.filter((source) => source.path != null),
)
// 题目进度条百分比（未知进度时不渲染）
const runProgressPercent = computed(() => {
  const progress = runProgress.value
  if (!progress || progress.total <= 0) return 0
  return Math.min(100, Math.round((progress.completed / progress.total) * 100))
})
// 进度细分：运行中时附带作答中/待处理的题数（pier/harbor 后端可得）
const runProgressDetail = computed(() => {
  const progress = runProgress.value
  if (!progress) return ''
  const parts: string[] = []
  if (progress.running) parts.push(`作答中 ${progress.running}`)
  if (progress.pending) parts.push(`待处理 ${progress.pending}`)
  return parts.length ? `（${parts.join(' · ')}）` : ''
})

// 评测记录筛选：状态 / 模型 / 基准 三个维度，选项随数据动态生成。
const runModelOptions = computed(() =>
  [...new Set(allRuns.value.map((run) => run.model_id))].sort(),
)
const runBenchmarkOptions = computed(() =>
  [...new Set(allRuns.value.map((run) => run.benchmark))].sort(),
)
const filteredRunsList = computed(() =>
  allRuns.value.filter(
    (run) =>
      (runStatusFilter.value === 'all' || run.status === runStatusFilter.value)
      && (runModelFilter.value === 'all' || run.model_id === runModelFilter.value)
      && (runBenchmarkFilter.value === 'all' || run.benchmark === runBenchmarkFilter.value),
  ),
)
const gatewayFailureRetestableRuns = computed(() =>
  allRuns.value.filter(
    (run) => runIsGatewayFailureRetestable(run),
  ),
)
const gatewayFailureRetestableQuestionCount = computed(() =>
  gatewayFailureRetestableRuns.value.reduce(
    (sum, run) => sum + (run.retryable_infrastructure_failure_count ?? 0),
    0,
  ),
)
const allFilteredSelected = computed(
  () => filteredRunsList.value.length > 0
    && filteredRunsList.value.every((run) => selectedRunIds.value.includes(run.run_id)),
)
const someFilteredSelected = computed(
  () => filteredRunsList.value.some((run) => selectedRunIds.value.includes(run.run_id)),
)

const logSourceLabels: Record<string, string> = {
  pier: '运行日志',
  run: '运行日志',
  job: 'Job 日志',
  trial: 'Trial 日志',
  agent: '模型响应',
  verifier: '验证器日志',
  llm: '模型响应',
  results: '评测结果',
  eval: '评测结果',
}

async function refreshOptions() {
  loadingOptions.value = true
  error.value = null
  try {
    const [modelData, benchmarkData, latestRun, latestBatch, latestMulti] = await Promise.all([
      fetchModels(),
      fetchBenchmarks(),
      fetchLatestDeepSweRun(),
      fetchLatestBatch(),
      fetchLatestMultiBatch(),
    ])
    // 待测模型来自「网关配置 · 模型列表获取」（GET /v1/models）；网关不可达
    // 时后端回退本地目录并在 error 里说明。
    models.value = modelData.models ?? []
    modelsError.value = modelData.error
    benchmarks.value = benchmarkData
    benchmarksLoaded.value = true
    // 换网关后新列表可能不含此前勾选/筛选的模型：剪掉失效勾选，
    // 供应商筛选不存在时重置为「全部」，避免列表看起来是空的。
    const modelIds = new Set(models.value.map((model) => model.id))
    selectedModels.value = selectedModels.value.filter((id) => modelIds.has(id))
    saveSelectedModelIds(selectedModels.value)
    if (
      selectedProvider.value !== 'all'
      && !models.value.some((model) => modelProvider(model) === selectedProvider.value)
    ) {
      selectedProvider.value = 'all'
    }
    saveSelectedProvider(selectedProvider.value)
    if (!gwChatModel.value) {
      gwChatModel.value = selectedModels.value[0] ?? models.value[0]?.id ?? ''
    }
    const benchmarkIds = new Set(benchmarkData.map((b) => b.id))
    selectedBenchmarks.value = selectedBenchmarks.value.filter((id) => benchmarkIds.has(id))
    if (benchmarkData.length && !selectedBenchmarks.value.length) {
      selectedBenchmarks.value = [benchmarkData[0].id]
    }
    saveSelectedBenchmarkIds(selectedBenchmarks.value)
    // 恢复批量评测：进行中的恢复轮询；已结束/已取消的批量在最近一次 run 属于
    // 该批量时展示最终状态（避免与之后单独发起的评测混淆）。
    const batchOwnsLatestRun = latestBatch?.models?.some(
      (entry) => entry.run_id != null && entry.run_id === latestRun?.run_id,
    )
    if (latestBatch && (latestBatch.status === 'running' || batchOwnsLatestRun)) {
      batchInfo.value = latestBatch
      if (latestBatch.status === 'running') {
        const requestId = pollRequestId.value + 1
        pollRequestId.value = requestId
        running.value = true
        void pollBatch(latestBatch.batch_id, requestId).finally(() => {
          if (requestId === pollRequestId.value) running.value = false
        })
      }
    }
    // 恢复多基准并发：进行中的恢复轮询；已结束的在其包含最近一次 run 时展示
    const multiOwnsLatestRun = latestMulti?.items?.some(
      (entry) => entry.run_id != null && entry.run_id === latestRun?.run_id,
    )
    if (latestMulti && (latestMulti.status === 'running' || multiOwnsLatestRun)) {
      multiBatchInfo.value = latestMulti
      if (latestMulti.status === 'running') {
        const requestId = pollRequestId.value + 1
        pollRequestId.value = requestId
        running.value = true
        void pollMultiBatch(latestMulti.batch_id, requestId).finally(() => {
          if (requestId === pollRequestId.value) running.value = false
        })
      }
    }
    if (latestRun) {
      publication.value = null
      execution.value = latestRun
      runProgress.value = latestRun.progress ?? null
      if (
        latestRun.benchmark
        && benchmarks.value.some((b) => b.id === latestRun.benchmark)
        && !selectedBenchmarks.value.length
      ) {
        selectedBenchmarks.value = [latestRun.benchmark]
        saveSelectedBenchmarkIds(selectedBenchmarks.value)
      }
      if (latestRun.status === 'queued' || latestRun.status === 'running') {
        const requestId = pollRequestId.value + 1
        pollRequestId.value = requestId
        running.value = true
        void pollRun(latestRun.run_id, requestId).finally(() => {
          if (requestId === pollRequestId.value) running.value = false
        })
      } else if (latestRun.status === 'completed') {
        publication.value = await fetchDeepSweRunPublication(latestRun.run_id)
      }
      void loadLogSources()
      void refreshLog()
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'Failed to load test options'
  } finally {
    loadingOptions.value = false
  }
  // 刷新配置时同步拉取评测记录列表（「刷新」按钮也走这里）。
  void loadRuns()
}

function toggleBenchmark(id: string) {
  const idx = selectedBenchmarks.value.indexOf(id)
  if (idx >= 0) {
    selectedBenchmarks.value.splice(idx, 1)
  } else {
    selectedBenchmarks.value.push(id)
  }
  saveSelectedBenchmarkIds(selectedBenchmarks.value)
}

function selectDockerBenchmark(id: string, checked: boolean) {
  selectedBenchmarks.value = checked ? [id] : []
  saveSelectedBenchmarkIds(selectedBenchmarks.value)
}

async function startMultiBench() {
  if (!multiBenchItems.value.length || hasActiveExecution()) return
  const requestId = pollRequestId.value + 1
  pollRequestId.value = requestId
  running.value = true
  publication.value = null
  multiBatchInfo.value = null
  execution.value = null
  logContent.value = ''
  logSources.value = []
  error.value = null
  try {
    const batch = await submitMultiBench({
      items: multiBenchItems.value,
      max_concurrent: defaultRunConcurrency(multiBenchItems.value.length),
      effort: effort.value,
    })
    multiBatchInfo.value = batch
    await pollMultiBatch(batch.batch_id, requestId)
  } catch (caught) {
    if (requestId === pollRequestId.value) {
      error.value = caught instanceof Error ? caught.message : '多基准并发启动失败'
    }
  } finally {
    if (requestId === pollRequestId.value) running.value = false
    void loadRuns()
  }
}

async function pollMultiBatch(
  batchId: string,
  requestId: number,
  fetcher: (batchId: string) => Promise<MultiBenchState> = fetchMultiBatch,
): Promise<void> {
  while (requestId === pollRequestId.value) {
    let batch: MultiBenchState
    try {
      batch = await fetcher(batchId)
    } catch (caught) {
      if (requestId !== pollRequestId.value) return
      error.value = caught instanceof Error ? caught.message : '多基准并发状态轮询失败'
      await new Promise((resolve) => setTimeout(resolve, 1000))
      continue
    }
    if (requestId !== pollRequestId.value) return
    multiBatchInfo.value = batch
    // 驱动日志面板：跟随当前 running 的 run
    const current = batch.items.find((i) => i.status === 'running' && i.run_id)
    if (current?.run_id) {
      try {
        retryingGatewayRunId.value = batch.kind === 'gateway-retry' ? current.run_id : null
        const currentRun = await fetchDeepSweRun(current.run_id)
        execution.value = currentRun
        runProgress.value = currentRun.progress ?? null
      } catch {
        /* transient */
      }
    }
    if (batch.status !== 'running') {
      // 批次完成不再自动发布：publication 仅在单 run 完成或手动发布后设置。
      await loadRuns()
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 3000))
  }
}

async function startRun() {
  if (!selectedModels.value.length || !selectedBenchmarks.value.length || hasActiveExecution()) return
  if (isMultiBenchmark.value) {
    if (!selectedAllApiEval.value) {
      error.value = '多基准评测目前仅支持 API 轻量基准；Docker 容器化基准请单独运行。'
      return
    }
    await startMultiBench()
    return
  }
  const requestId = pollRequestId.value + 1
  pollRequestId.value = requestId
  running.value = true
  publication.value = null
  batchInfo.value = null
  logContent.value = ''
  logSources.value = []
  error.value = null
  try {
    if (selectedModels.value.length === 1) {
      execution.value = await submitDeepSweRun({
        model_id: selectedModels.value[0],
        benchmark: selectedBenchmark.value,
        effort: effort.value,
        ...benchmarkTaskPayload(selectedBenchmark.value),
      })
      await pollRun(execution.value.run_id, requestId)
    } else {
      execution.value = null
      const batch = await submitBatch({
        model_ids: selectedModels.value,
        benchmark: selectedBenchmark.value,
        effort: effort.value,
        max_concurrent: defaultRunConcurrency(selectedModels.value.length),
        ...benchmarkTaskPayload(selectedBenchmark.value),
      })
      batchInfo.value = batch
      await pollBatch(batch.batch_id, requestId)
    }
  } catch (caught) {
    if (requestId === pollRequestId.value) {
      error.value = caught instanceof Error ? caught.message : 'Test execution failed'
    }
  } finally {
    if (requestId === pollRequestId.value) running.value = false
    // 评测结束后刷新记录列表，让新 run/批次出现在「评测记录」中。
    void loadRuns()
  }
}

/** 接续中断的单 run：api-eval 复用已写 results 前缀继续跑，
 * terminal-bench-2 走 harbor job resume。模型/题数/种子/档位沿用原 run 参数。 */
async function resumeSingleRun(run: DeepSweRun) {
  if (running.value || hasActiveExecution()) return
  const requestId = pollRequestId.value + 1
  pollRequestId.value = requestId
  running.value = true
  publication.value = null
  batchInfo.value = null
  multiBatchInfo.value = null
  logContent.value = ''
  logSources.value = []
  error.value = null
  try {
    execution.value = await submitDeepSweRun({
      model_id: run.model_id,
      n_tasks: run.n_tasks,
      sample_seed: run.sample_seed,
      benchmark: run.benchmark,
      effort: run.effort,
      resume_run_id: run.run_id,
    })
    await pollRun(execution.value.run_id, requestId)
  } catch (caught) {
    if (requestId === pollRequestId.value) {
      error.value = caught instanceof Error ? caught.message : '接续失败'
    }
  } finally {
    if (requestId === pollRequestId.value) running.value = false
    void loadRuns()
  }
}

async function pollBatch(batchId: string, requestId: number): Promise<void> {
  while (requestId === pollRequestId.value) {
    let batch: DeepSweBatch
    try {
      batch = await fetchBatch(batchId)
    } catch (caught) {
      if (requestId !== pollRequestId.value) return
      error.value = caught instanceof Error ? caught.message : 'Batch polling failed'
      await new Promise((resolve) => setTimeout(resolve, 1000))
      continue
    }
    if (requestId !== pollRequestId.value) return
    batchInfo.value = batch
    // 驱动日志面板：跟随当前正在运行的模型
    const current = batch.models.find((entry) => entry.status === 'running' && entry.run_id)
    if (current?.run_id) {
      try {
        const currentRun = await fetchDeepSweRun(current.run_id)
        execution.value = currentRun
        runProgress.value = currentRun.progress ?? null
      } catch {
        /* transient */
      }
    }
    if (batch.status !== 'running') {
      // 批次完成不再自动发布：publication 仅在单 run 完成或手动发布后设置。
      return
    }
    await new Promise((resolve) => setTimeout(resolve, 3000))
  }
}

async function pollRun(runId: string, requestId: number): Promise<void> {
  while (requestId === pollRequestId.value) {
    let run: DeepSweRun
    try {
      run = await fetchDeepSweRun(runId)
    } catch (caught) {
      if (requestId !== pollRequestId.value) return
      error.value = caught instanceof Error ? caught.message : 'Status polling failed'
      await new Promise((resolve) => setTimeout(resolve, 1000))
      continue
    }
    if (requestId !== pollRequestId.value) return
    execution.value = run
    runProgress.value = run.progress ?? null
    error.value = null
    if (run.status === 'completed') {
      publication.value = await fetchDeepSweRunPublication(run.run_id)
      return
    }
    if (run.status !== 'queued' && run.status !== 'running') return
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
}

async function cancelRun() {
  if (!hasActiveExecution()) return
  cancelling.value = true
  error.value = null
  try {
    if (multiBatchInfo.value?.status === 'running' && multiBatchInfo.value.batch_id) {
      multiBatchInfo.value = multiBatchInfo.value.kind === 'gateway-retry'
        ? await cancelRetryGatewayFailuresBatch(multiBatchInfo.value.batch_id)
        : await cancelMultiBatch(multiBatchInfo.value.batch_id)
    } else if (batchInfo.value?.status === 'running' && batchInfo.value.batch_id) {
      batchInfo.value = await cancelBatch(batchInfo.value.batch_id)
    } else if (execution.value?.run_id) {
      execution.value = await cancelDeepSweRun(execution.value.run_id)
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : 'Cancellation failed'
  } finally {
    cancelling.value = false
  }
}

const isBatchResumable = computed(() => {
  const batch = batchInfo.value
  if (!batch) return false
  // 批次不再自动发布，所以「完成」即终态、无可接续的发布动作；只有
  // 失败/取消且存在未完成模型时才可接续（重跑未完成的模型）。
  if (batch.status === 'completed') return false
  if (batch.status !== 'failed' && batch.status !== 'cancelled') return false
  return batch.models.some((m) => m.status !== 'completed')
})

const isMultiBatchResumable = computed(() => {
  const batch = multiBatchInfo.value
  if (!batch) return false
  if (batch.status === 'completed') return false
  if (batch.status !== 'failed' && batch.status !== 'cancelled') return false
  return batch.items.some((i) => i.status !== 'completed')
})

// ── 网关配置：填入 baseurl/key 保存即生效，无需改代码或 .env ────────────
async function loadGatewaySettings() {
  try {
    const settings: GatewaySettings = await fetchGatewaySettings()
    gwModelsBaseUrl.value = settings.models_base_url
    gwModelsApiKey.value = settings.models_api_key
    gwInferenceBaseUrl.value = settings.inference_base_url
    gwInferenceApiKey.value = settings.inference_api_key
    const savedEffort = settings.inference_effort ?? ''
    if (effortOptions.some((opt) => opt.value === savedEffort)) {
      effort.value = savedEffort
    }
    gwSavedAt.value = settings.updated_at ?? ''
    if (!gwChatModel.value) {
      gwChatModel.value = selectedModels.value[0] ?? models.value[0]?.id ?? ''
    }
  } catch {
    /* 静默：表单留空即使用 .env 基线配置 */
  }
}

/** 组合当前网关配置（含推理强度），供保存/探测后自动落盘复用。 */
function currentGatewaySettingsPayload() {
  return {
    models_base_url: gwModelsBaseUrl.value.trim(),
    models_api_key: gwModelsApiKey.value.trim(),
    inference_base_url: gwInferenceBaseUrl.value.trim(),
    inference_api_key: gwInferenceApiKey.value.trim(),
    inference_effort: effort.value,
  }
}

async function saveGateway() {
  gwSaving.value = true
  error.value = null
  try {
    const saved = await saveGatewaySettings(currentGatewaySettingsPayload())
    gwSavedAt.value = saved.updated_at ?? ''
    gwModelsResult.value = null
    gwChatResult.value = null
    // 保存立即生效：按新网关重新拉取待测模型列表
    await refreshOptions()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '网关配置保存失败'
  } finally {
    gwSaving.value = false
  }
}

/** 推理强度变更：随网关配置立即持久化（无需再点「保存配置」）。 */
async function changeEffort() {
  error.value = null
  try {
    const saved = await saveGatewaySettings(currentGatewaySettingsPayload())
    gwSavedAt.value = saved.updated_at ?? ''
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '推理强度保存失败'
  }
}

async function probeGatewayModels() {
  gwTestingModels.value = true
  gwModelsResult.value = null
  try {
    const probe = await testGatewayModels({
      base_url: gwModelsBaseUrl.value.trim() || undefined,
      api_key: gwModelsApiKey.value.trim() || undefined,
    })
    if (!probe.ok) {
      gwModelsResult.value = { ok: false, text: probe.error || '连接失败' }
      return
    }
    // 测试成功即应用：保存当前表单配置，并按该网关刷新待测模型列表，
    // 让下面「待测模型」立即变成可勾选的新列表（无需再手动点保存）。
    const saved = await saveGatewaySettings(currentGatewaySettingsPayload())
    gwSavedAt.value = saved.updated_at ?? ''
    gwChatResult.value = null
    await refreshOptions()
    gwModelsResult.value = {
      ok: true,
      text: `连接成功 · ${probe.model_count} 个模型（${probe.latency_ms}ms）· 待测模型已更新，可直接勾选`,
    }
  } catch (caught) {
    gwModelsResult.value = { ok: false, text: caught instanceof Error ? caught.message : '连接失败' }
  } finally {
    gwTestingModels.value = false
  }
}

async function probeGatewayChat() {
  const model = gwChatModel.value.trim()
  if (!model) {
    gwChatResult.value = { ok: false, text: '请先填写测试模型名（或先在上方选择模型）' }
    return
  }
  gwTestingChat.value = true
  gwChatResult.value = null
  try {
    const probe = await testGatewayChat({
      model,
      base_url: gwInferenceBaseUrl.value.trim() || undefined,
      api_key: gwInferenceApiKey.value.trim() || undefined,
    })
    gwChatResult.value = probe.ok
      ? {
          ok: true,
          text: `调用成功（${probe.latency_ms}ms）· ${probe.content ? probe.content.slice(0, 80) : '（空回复）'}`,
        }
      : { ok: false, text: probe.error || '调用失败' }
  } catch (caught) {
    gwChatResult.value = { ok: false, text: caught instanceof Error ? caught.message : '调用失败' }
  } finally {
    gwTestingChat.value = false
  }
}

async function resumeRun() {
  if (multiBatchInfo.value?.batch_id && isMultiBatchResumable.value) {
    const requestId = pollRequestId.value + 1
    pollRequestId.value = requestId
    resuming.value = true
    error.value = null
    try {
      const batch = await resumeMultiBatch(multiBatchInfo.value.batch_id)
      multiBatchInfo.value = batch
      if (batch.status === 'running') {
        await pollMultiBatch(batch.batch_id, requestId)
      } else {
        error.value = '接续失败，请稍后重试'
      }
    } catch (caught) {
      if (requestId === pollRequestId.value) {
        error.value = caught instanceof Error ? caught.message : '接续失败'
      }
    } finally {
      if (requestId === pollRequestId.value) resuming.value = false
    }
    return
  }
  if (!batchInfo.value?.batch_id || !isBatchResumable.value) return
  const requestId = pollRequestId.value + 1
  pollRequestId.value = requestId
  resuming.value = true
  error.value = null
  try {
    const batch = await resumeBatch(batchInfo.value.batch_id)
    batchInfo.value = batch
    if (batch.status === 'running') {
      await pollBatch(batch.batch_id, requestId)
    } else {
      error.value = '接续失败，请稍后重试'
    }
  } catch (caught) {
    if (requestId === pollRequestId.value) {
      error.value = caught instanceof Error ? caught.message : '接续失败'
    }
  } finally {
    if (requestId === pollRequestId.value) resuming.value = false
  }
}

// ── 评测记录 · 逐题重测：查看某 run 的逐题结果，可单题重测或全部重测 ──
const questionsRun = ref<DeepSweRun | null>(null)
const questions = ref<RunQuestion[]>([])
const questionsLoading = ref(false)
const questionsError = ref<string | null>(null)
const retryingQuestionIds = ref<Set<string>>(new Set())
const evaluationReport = ref<EvaluationReport | null>(null)
const reportLoading = ref(false)
const reportError = ref<string | null>(null)

const QUESTION_STATUS_LABELS: Record<string, string> = {
  passed: '通过',
  failed: '失败',
  timeout: '超时',
  runner_error: '网关错误',
  verifier_error: '校验错误',
  budget_stopped: '预算中止',
  skipped: '跳过',
  not_executed: '未执行',
}

const FAILURE_CATEGORY_LABELS: Record<string, string> = {
  model_wrong_answer: '模型答案错误',
  model_empty_or_unparseable: '模型未给出可判定答案',
  gateway_error: '网关/调用错误',
  judge_error: '裁判调用错误',
  judge_unclear: '裁判结果不明确',
  harness_error: '评测框架错误',
  verifier_error: '验证器错误',
  timeout: '超时',
  budget_stopped: '预算中止',
  skipped: '跳过',
  not_executed: '未执行',
  unknown_failure: '未分类失败',
}

function questionStatusLabel(status: string) {
  return QUESTION_STATUS_LABELS[status] ?? status
}

function failureCategoryLabel(category: string | null | undefined) {
  return category ? (FAILURE_CATEGORY_LABELS[category] ?? category) : '成功'
}

/**
 * 「全部重测」只批量重跑基础设施/调用链失败题。
 * 通过题、模型作答错误（含空答案、超时等模型失败）不应被批量重测。
 */
function isTransientGatewayError(errorType: string | null | undefined, errorMessage: string | null | undefined) {
  if (errorType !== 'HTTPError') return true
  const match = String(errorMessage || '').match(/^HTTPError:(\d{3}):/)
  if (!match) return true
  const status = Number(match[1])
  return status === 408 || status === 425 || status === 429 || (status >= 500 && status < 600)
}

function isAllRetryQuestion(q: RunQuestion) {
  if (q.recorded === false || q.status === 'passed') return false
  if (q.status === 'runner_error') return isTransientGatewayError(q.error_type, q.error_message)
  if (q.status === 'verifier_error') return q.error_type === 'empty_response'
  if (q.status === 'timeout') return true
  if (q.status === 'failed') return q.error_type === 'judge_error'
  return false
}

const allRetryQuestions = computed(() => questions.value.filter(isAllRetryQuestion))

async function openRunQuestions(run: DeepSweRun) {
  questionsRun.value = run
  questions.value = []
  questionsError.value = null
  reportError.value = null
  evaluationReport.value = null
  questionsLoading.value = true
  reportLoading.value = true
  try {
    const [report, rawQuestions] = await Promise.all([
      fetchEvaluationReport(run.run_id),
      fetchRunQuestions(run.run_id),
    ])
    evaluationReport.value = report
    questions.value = report.questions.length ? report.questions : rawQuestions
  } catch (caught) {
    reportError.value = caught instanceof Error ? caught.message : '评测报告加载失败'
    try {
      questions.value = await fetchRunQuestions(run.run_id)
    } catch (fallbackCaught) {
      questionsError.value = fallbackCaught instanceof Error ? fallbackCaught.message : '题目结果加载失败'
    }
  } finally {
    questionsLoading.value = false
    reportLoading.value = false
  }
}

function closeRunQuestions() {
  questionsRun.value = null
  questions.value = []
  questionsError.value = null
  reportError.value = null
  evaluationReport.value = null
}

function downloadEvaluationReport() {
  const report = evaluationReport.value
  if (!report || !questionsRun.value) return
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `evaluation-report-${questionsRun.value.run_id}.json`
  anchor.click()
  URL.revokeObjectURL(url)
}

/** 重测指定题目：taskIds 传一题即单题重测，传全部即全部重测。 */
async function retryAllGatewayFailureRuns() {
  const runs = gatewayFailureRetestableRuns.value
  if (!runs.length || retryingGatewayFailures.value || hasActiveExecution()) return
  const questionCount = gatewayFailureRetestableQuestionCount.value
  const maxConcurrent = defaultRunConcurrency(runs.length)
  if (
    !window.confirm(
      `确认一键并发重测所有因模型网关不可达/临时错误导致失败的题目？\n\n将按多模型 × 多基准批次并发重测 ${runs.length} 个 run，共 ${questionCount} 道题，并发上限 ${maxConcurrent}。模型答错的题目不会重测。`,
    )
  ) return

  const requestId = pollRequestId.value + 1
  pollRequestId.value = requestId
  retryingGatewayFailures.value = true
  running.value = true
  runsError.value = null
  runsNotice.value = null
  error.value = null
  batchInfo.value = null
  multiBatchInfo.value = null
  execution.value = null
  logContent.value = ''
  logSources.value = []

  try {
    const batch = await submitRetryGatewayFailuresBatch({
      run_ids: runs.map((run) => run.run_id),
      max_concurrent: maxConcurrent,
    })
    multiBatchInfo.value = batch
    runsNotice.value = `已启动一键并发重测：${batch.items.length} 个 run，${questionCount} 道网关失败题，并发上限 ${maxConcurrent}`
    await pollMultiBatch(batch.batch_id, requestId, fetchRetryGatewayFailuresBatch)
    runsNotice.value = '一键并发重测完成'
  } catch (caught) {
    if (requestId === pollRequestId.value) {
      runsError.value = caught instanceof Error ? caught.message : '一键并发重测启动失败'
    }
  } finally {
    retryingGatewayRunId.value = null
    if (requestId === pollRequestId.value) {
      retryingGatewayFailures.value = false
      running.value = false
    }
    await loadRuns()
  }
}

async function retryQuestions(taskIds: string[]) {
  const run = questionsRun.value
  if (!run || !taskIds.length || retryingQuestionIds.value.size) return
  retryingQuestionIds.value = new Set([...retryingQuestionIds.value, ...taskIds])
  questionsError.value = null
  try {
    error.value = null
    batchInfo.value = null
    multiBatchInfo.value = null
    const updated = await retryRunQuestions(run.run_id, taskIds)
    execution.value = updated
    runProgress.value = updated.progress ?? null
    // POST 返回 202 时后端已把 run 切到 running；立即同步本地列表，
    // 避免弹窗关闭后评测记录仍显示旧的 completed，用户误以为没有反应。
    const listedIndex = allRuns.value.findIndex((item) => item.run_id === run.run_id)
    if (listedIndex >= 0) {
      allRuns.value[listedIndex] = { ...allRuns.value[listedIndex], ...updated }
    }
    if (execution.value?.run_id === run.run_id) {
      execution.value = { ...execution.value, ...updated }
      runProgress.value = updated.progress ?? null
    }
    runsNotice.value = `已开始重测 ${taskIds.length} 道题（${run.model_id} · ${run.benchmark}），当前状态：重测中`
    closeRunQuestions()
    await pollRetryRun(run.run_id)
  } catch (caught) {
    questionsError.value = caught instanceof Error ? caught.message : '重测失败'
  } finally {
    const next = new Set(retryingQuestionIds.value)
    for (const id of taskIds) next.delete(id)
    retryingQuestionIds.value = next
  }
}

async function pollRetryRun(runId: string) {
  // 重测是后端线程：run 置 running → 完成后回 completed。轮询到终态再刷新列表。
  // 首轮立即检查（与 pollRun 一致），随后每 1s 轮询一次。
  const requestId = ++pollRequestId.value
  for (let i = 0; i < 240; i++) {
    if (requestId !== pollRequestId.value) return
    try {
      const run = await fetchDeepSweRun(runId)
      if (run.status === 'completed' || run.status === 'failed') {
        await loadRuns()
        // 旧的列表请求可能在重测期间返回，终态以本次轮询的 run 为准，
        // 防止并发响应把已完成的重测覆盖回 running。
        const listedIndex = allRuns.value.findIndex((item) => item.run_id === runId)
        if (listedIndex >= 0) {
          allRuns.value[listedIndex] = { ...allRuns.value[listedIndex], ...run }
        }
        // 若重测的是当前展示的 run，同步刷新执行区的发布状态
        if (execution.value?.run_id === runId) {
          publication.value = await fetchDeepSweRunPublication(runId)
        }
        return
      }
    } catch {
      /* 单次轮询失败：忽略，下一轮重试 */
    }
    await new Promise((resolve) => setTimeout(resolve, 1000))
  }
  await loadRuns()
}

/** 评测记录：发布指定 run 到大盘。 */
async function publishRun(run: DeepSweRun) {
  if (publishingRunIds.value.has(run.run_id)) return
  publishingRunIds.value = new Set(publishingRunIds.value).add(run.run_id)
  error.value = null
  try {
    await publishDeepSweRun(run.run_id)
    await loadRuns()
    if (execution.value?.run_id === run.run_id) {
      publication.value = await fetchDeepSweRunPublication(run.run_id)
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '发布失败'
  } finally {
    const next = new Set(publishingRunIds.value)
    next.delete(run.run_id)
    publishingRunIds.value = next
  }
}

/** 评测记录：回撤该 run 的发布（删除其快照，把大盘 current 重指向）。 */
async function unpublishRun(run: DeepSweRun) {
  if (publishingRunIds.value.has(run.run_id)) return
  publishingRunIds.value = new Set(publishingRunIds.value).add(run.run_id)
  if (!window.confirm('回撤发布将从大盘移除该 run 的快照，确认吗？')) {
    const next = new Set(publishingRunIds.value)
    next.delete(run.run_id)
    publishingRunIds.value = next
    return
  }
  error.value = null
  try {
    await unpublishDeepSweRun(run.run_id)
    await loadRuns()
    if (execution.value?.run_id === run.run_id) {
      publication.value = null
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '回撤失败'
  } finally {
    const next = new Set(publishingRunIds.value)
    next.delete(run.run_id)
    publishingRunIds.value = next
  }
}

/** 判断某 run 是否为 api-eval（用于显示「重测」按钮）。 */
function runIsApiEval(run: DeepSweRun): boolean {
  const benchmark = benchmarks.value.find((b) => b.id === run.benchmark)
  return benchmark?.type === 'api-eval'
}

/** 模型网关不可达/临时错误导致失败的题目，可由「一键重测」安全批量处理。 */
function runIsGatewayFailureRetestable(run: DeepSweRun): boolean {
  return runIsApiEval(run)
    && (run.status === 'completed' || run.status === 'failed')
    && (run.retryable_infrastructure_failure_count ?? 0) > 0
}

/** 记录表里的断点续跑入口：后端确认有 partial results 时才展示。 */
function runCanResume(run: DeepSweRun): boolean {
  return run.status === 'failed' && run.resumable_partial_results === true
}

function hasActiveExecution(): boolean {
  if (multiBatchInfo.value?.status === 'running') return true
  if (batchInfo.value?.status === 'running') return true
  return execution.value?.status === 'queued' || execution.value?.status === 'running'
}

function setSelectedProvider(provider: string) {
  selectedProvider.value = provider
  saveSelectedProvider(provider)
}

function toggleModel(id: string) {
  const index = selectedModels.value.indexOf(id)
  if (index >= 0) {
    selectedModels.value.splice(index, 1)
  } else {
    selectedModels.value.push(id)
  }
  saveSelectedModelIds(selectedModels.value)
}

function selectAllModels() {
  // 有供应商筛选时“全选”只选当前可见（该供应商）的模型
  selectedModels.value = visibleModels.value.map((model) => model.id)
  saveSelectedModelIds(selectedModels.value)
}

function clearModels() {
  selectedModels.value = []
  saveSelectedModelIds(selectedModels.value)
}

function executionTitle(run: DeepSweRun): string {
  if (run.status === 'completed') return '评测完成'
  if (run.status === 'failed') return '评测失败'
  if (run.status === 'queued') return '已排队'
  return '评测运行中'
}

function modelLabel(model: ModelChoice): string {
  return model.label || `${model.provider || ''} / ${model.display_name || model.id}`
}

function preferredLogSource(benchmark?: string | null): string {
  return benchmark === 'deep-swe' ? 'llm' : 'run'
}

function chooseLogSource(sources: LogSource[], benchmark?: string | null): string | null {
  const available = sources.filter((source) => source.path != null)
  if (!available.length) return null
  if (selectedLogSource.value && available.some((source) => source.name === selectedLogSource.value)) {
    return selectedLogSource.value
  }
  const preferred = preferredLogSource(benchmark)
  return available.find((source) => source.name === preferred)?.name ?? available[0].name
}

async function loadLogSources() {
  if (!runId.value) return
  try {
    const sources = await fetchRunLogSources(runId.value)
    logSources.value = sources
    const source = chooseLogSource(sources, execution.value?.benchmark)
    if (source) selectedLogSource.value = source
  } catch {
    logSources.value = []
  }
}

async function refreshLog() {
  if (!runId.value) return
  loadingLog.value = true
  logError.value = null
  try {
    // 先刷新可用来源，避免跨基准沿用旧选择（例如 terminal-bench 沿用 deep-swe 的 llm）
    // 时向后端请求不存在的 source，导致 “invalid log source”。
    const sources = await fetchRunLogSources(runId.value)
    logSources.value = sources
    const source = chooseLogSource(sources, execution.value?.benchmark)
    if (!source) {
      logContent.value = execution.value?.status === 'running'
        ? '评测已启动，正在等待首条日志输出…'
        : ''
      return
    }
    if (selectedLogSource.value !== source) selectedLogSource.value = source
    // 模型响应源只保留最近几条（内容本身很长），配合压缩后的窗口即时可见
    const tail = source === 'llm' ? 6 : undefined
    const logs: RunLogs = await fetchRunLog(runId.value, source, tail)
    // 来源列表随响应同步（如运行中才出现的模型响应/verifier 源），并保持
    // 当前选择在路径不可用（例如运行尚未产生该源）时自动回退。
    if (logs.sources?.length) {
      logSources.value = logs.sources
      const nextSource = chooseLogSource(logs.sources, execution.value?.benchmark)
      if (nextSource) selectedLogSource.value = nextSource
    }
    // 日志轮询比 run 状态轮询存活更久：run 结束后靠它保持最终进度展示。
    runProgress.value = logs.progress ?? null
    logContent.value = logs.content ?? ''
    if (logAutoScroll.value && logHost.value) {
      await nextTick()
      logHost.value.scrollTop = logHost.value.scrollHeight
    }
  } catch (caught) {
    logError.value = caught instanceof Error ? caught.message : 'Log loading failed'
  } finally {
    loadingLog.value = false
  }
}

function startLogPolling() {
  stopLogPolling()
  logTimer.push(window.setInterval(() => {
    void refreshLog()
  }, 3000))
  void loadLogSources()
  void refreshLog()
}

function stopLogPolling() {
  while (logTimer.length) {
    window.clearInterval(logTimer.pop() as number)
  }
}

function runStatusLabel(status: string): string {
  switch (status) {
    case 'queued': return '排队中'
    case 'running': return '运行中'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    default: return status
  }
}

function formatSeconds(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}s` : '--'
}

function formatTokensPerSecond(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(1)}` : '--'
}

function formatTokenCount(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value)) : '--'
}

function questionMetric(question: RunQuestion, key: string): number | null | undefined {
  const direct = question[key]
  if (typeof direct === 'number') return direct
  const timing = question.timing
  if (timing && typeof timing === 'object' && !Array.isArray(timing)) {
    const nested = (timing as Record<string, unknown>)[key]
    if (typeof nested === 'number') return nested
  }
  return null
}

function formatRunTime(iso: string): string {
  if (!iso) return '--'
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(iso))
  } catch {
    return iso
  }
}

async function loadRuns() {
  loadingRuns.value = true
  runsError.value = null
  try {
    const runs = await fetchDeepSweRuns()
    allRuns.value = runs
    // 列表刷新后剪掉已不存在的勾选，避免对已删除记录重复操作。
    const ids = new Set(runs.map((run) => run.run_id))
    selectedRunIds.value = selectedRunIds.value.filter((id) => ids.has(id))
    // 切换网关/基准后模型/基准筛选可能失效：重置为「全部」避免空表。
    if (runModelFilter.value !== 'all' && !runs.some((run) => run.model_id === runModelFilter.value)) {
      runModelFilter.value = 'all'
    }
    if (runBenchmarkFilter.value !== 'all' && !runs.some((run) => run.benchmark === runBenchmarkFilter.value)) {
      runBenchmarkFilter.value = 'all'
    }
  } catch (caught) {
    runsError.value = caught instanceof Error ? caught.message : '评测记录加载失败'
  } finally {
    loadingRuns.value = false
  }
}

function toggleRunSelected(runId: string) {
  const index = selectedRunIds.value.indexOf(runId)
  if (index >= 0) selectedRunIds.value.splice(index, 1)
  else selectedRunIds.value.push(runId)
}

function toggleSelectAllFiltered(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  if (checked) {
    const ids = new Set(selectedRunIds.value)
    for (const run of filteredRunsList.value) ids.add(run.run_id)
    selectedRunIds.value = [...ids]
  } else {
    const ids = new Set(filteredRunsList.value.map((run) => run.run_id))
    selectedRunIds.value = selectedRunIds.value.filter((id) => !ids.has(id))
  }
}

async function deleteSingleRun(runId: string) {
  const run = allRuns.value.find((item) => item.run_id === runId)
  const label = run ? `${run.model_id} · ${runStatusLabel(run.status)} · ${run.benchmark}` : runId
  if (!window.confirm(`确认删除该评测记录？\n\n${label}\n\n删除后磁盘上的 run 数据将无法恢复。`)) return
  deletingRuns.value = true
  runsError.value = null
  runsNotice.value = null
  try {
    await deleteDeepSweRun(runId)
    selectedRunIds.value = selectedRunIds.value.filter((id) => id !== runId)
    runsNotice.value = `已删除 1 条评测记录`
    // 刷新记录列表，并同步「评测进程」最新 run（被删的若是最新 run 则更新展示）。
    void refreshOptions()
  } catch (caught) {
    runsError.value = caught instanceof Error ? caught.message : '删除失败'
  } finally {
    deletingRuns.value = false
  }
}

async function deleteSelectedRuns() {
  if (!selectedRunIds.value.length) return
  const count = selectedRunIds.value.length
  if (!window.confirm(`确认删除选中的 ${count} 条评测记录？\n\n运行中的记录将被跳过（请先中断）。删除后磁盘上的 run 数据将无法恢复。`)) return
  deletingRuns.value = true
  runsError.value = null
  runsNotice.value = null
  try {
    const result = await deleteDeepSweRuns(selectedRunIds.value)
    const skipped = result.skipped
    if (skipped.length) {
      const active = skipped.filter((item) => item.reason.includes('active')).length
      const other = skipped.length - active
      const parts: string[] = []
      if (result.deleted.length) parts.push(`已删除 ${result.deleted.length} 条`)
      if (active) parts.push(`跳过 ${active} 条运行中（请先中断）`)
      if (other) parts.push(`跳过 ${other} 条（未找到）`)
      runsNotice.value = parts.join(' · ')
    } else {
      runsNotice.value = `已删除 ${result.deleted.length} 条评测记录`
    }
    // 只清掉本次实际删除的勾选；被跳过的保留勾选以便用户处理。
    const deletedSet = new Set(result.deleted)
    selectedRunIds.value = selectedRunIds.value.filter((id) => !deletedSet.has(id))
    void refreshOptions()
  } catch (caught) {
    runsError.value = caught instanceof Error ? caught.message : '批量删除失败'
  } finally {
    deletingRuns.value = false
  }
}

/** 评测记录：发布选中——把勾选的多条已完成且未发布的记录合并发布成一个快照。 */
async function publishSelectedRuns() {
  const selected = selectedRunIds.value
  if (!selected.length || publishingRuns.value) return
  // 仅「完成且未发布」的记录可发布；其余跳过并提示。
  const byId = new Map(allRuns.value.map((run) => [run.run_id, run]))
  const publishable = selected.filter((id) => {
    const run = byId.get(id)
    return run && run.status === 'completed' && !run.snapshot_id
  })
  const skippedCount = selected.length - publishable.length
  if (!publishable.length) {
    runsNotice.value = skippedCount
      ? `选中的 ${selected.length} 条均不可发布（仅完成且未发布的记录可发布）`
      : '未选中任何记录'
    return
  }
  const label = publishable.length === 1 ? '该 1 条记录' : `这 ${publishable.length} 条记录`
  if (
    !window.confirm(
      `确认发布${label}到大盘？\n\n将合并成一个版本化快照并设为当前展示。${skippedCount ? `\n（另外 ${skippedCount} 条因非完成或已发布被跳过）` : ''}`,
    )
  )
    return
  publishingRuns.value = true
  runsError.value = null
  runsNotice.value = null
  try {
    const result = await publishDeepSweRuns(publishable)
    runsNotice.value = `已发布 ${result.published_run_ids.length} 条记录到大盘（快照 ${result.snapshot_id}）`
    // 已发布的清出勾选；被跳过的保留勾选以便用户处理。
    const publishedSet = new Set(result.published_run_ids)
    selectedRunIds.value = selectedRunIds.value.filter((id) => !publishedSet.has(id))
    await loadRuns()
    // 若当前展示的 run 也在本次发布中，同步其发布状态。
    if (execution.value?.run_id && publishedSet.has(execution.value.run_id)) {
      publication.value = await fetchDeepSweRunPublication(execution.value.run_id)
    }
  } catch (caught) {
    runsError.value = caught instanceof Error ? caught.message : '发布失败'
  } finally {
    publishingRuns.value = false
  }
}

watch(
  () => execution.value?.run_id,
  (runIdValue, previous) => {
    if (runIdValue === previous) return
    // 切换（或清空）当前 run 时重置题目进度，避免上一个 run 的进度串台。
    runProgress.value = null
    if (runIdValue) {
      void loadLogSources()
      void refreshLog()
      startLogPolling()
    }
  },
)

watch(selectedLogSource, () => {
  void refreshLog()
})

onMounted(() => {
  void refreshOptions()
  void loadGatewaySettings()
  void loadRuns()
})
onUnmounted(() => {
  pollRequestId.value += 1
  stopLogPolling()
})
</script>

<template>
  <section class="workbench" data-testid="test-page" aria-label="Test workbench">
    <i class="panel-corners" aria-hidden="true" />
    <div class="workbench-head">
      <div class="workbench-title">
        <h2>工作台</h2>
      </div>
      <span :class="['phase-badge', phase.tone]" data-testid="run-phase">{{ phase.text }}</span>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="loadingOptions" class="loading" data-testid="test-options-loading">正在加载测试配置...</p>

    <!-- 网关配置：横贯工作台顶部，两组 baseurl/key 并排，保存即生效 -->
    <section class="gateway-bar" data-testid="gateway-settings">
      <div class="gateway-bar-head">
        <span class="gateway-bar-title">网关配置</span>
        <span v-if="gwSavedAt" class="gateway-saved">已保存 · {{ new Date(gwSavedAt).toLocaleString() }}</span>
      </div>
      <div class="gateway-bar-body">
        <div class="gateway-cell">
          <div class="gateway-cell-title">
            <span class="gateway-group-title">模型列表获取</span>
            <code class="gateway-endpoint">GET /v1/models</code>
            <span class="gateway-cell-note">驱动下方待测模型列表</span>
          </div>
          <div class="gateway-inputs">
            <input
              v-model="gwModelsBaseUrl"
              data-testid="gw-models-base-url"
              type="text"
              placeholder="Base URL，如 http://localhost:8080/v1"
              autocomplete="off"
              spellcheck="false"
            />
            <input
              v-model="gwModelsApiKey"
              data-testid="gw-models-key"
              type="password"
              placeholder="API Key，如 sk-..."
              autocomplete="off"
            />
            <button type="button" :disabled="gwTestingModels" data-testid="gw-test-models" @click="probeGatewayModels">
              {{ gwTestingModels ? '测试中...' : '测试' }}
            </button>
          </div>
          <span v-if="gwModelsResult" :class="['gateway-result', gwModelsResult.ok ? 'ok' : 'bad']">
            {{ gwModelsResult.text }}
          </span>
        </div>

        <i class="gateway-divider" aria-hidden="true" />

        <div class="gateway-cell">
          <div class="gateway-cell-title">
            <span class="gateway-group-title">模型调用</span>
            <code class="gateway-endpoint">POST /v1/chat/completions</code>
            <span class="gateway-cell-note">评测时调用模型的入口</span>
          </div>
          <div class="gateway-inputs five">
            <input
              v-model="gwInferenceBaseUrl"
              data-testid="gw-inference-base-url"
              type="text"
              placeholder="Base URL，如 http://localhost:8080/v1"
              autocomplete="off"
              spellcheck="false"
            />
            <input
              v-model="gwInferenceApiKey"
              data-testid="gw-inference-key"
              type="password"
              placeholder="API Key，如 sk-..."
              autocomplete="off"
            />
            <input
              v-model="gwChatModel"
              data-testid="gw-chat-model"
              type="text"
              placeholder="测试模型名"
              autocomplete="off"
              spellcheck="false"
            />
            <select
              v-model="effort"
              data-testid="effort-select"
              aria-label="推理强度"
              title="推理强度：新评测默认使用的推理档位"
              @change="changeEffort"
            >
              <option v-for="opt in effortOptions" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
            <button type="button" :disabled="gwTestingChat" data-testid="gw-test-chat" @click="probeGatewayChat">
              {{ gwTestingChat ? '测试中...' : '测试' }}
            </button>
          </div>
          <span v-if="gwChatResult" :class="['gateway-result', gwChatResult.ok ? 'ok' : 'bad']">
            {{ gwChatResult.text }}
          </span>
        </div>

        <div class="gateway-bar-actions">
          <button type="button" class="primary-action" :disabled="gwSaving" data-testid="gw-save" @click="saveGateway">
            {{ gwSaving ? '保存中...' : '保存配置' }}
          </button>
        </div>
      </div>
    </section>

    <div class="workbench-body">
      <div class="config-column">
        <section class="config-card model-picker">
          <div class="model-picker-head">
            <span class="model-picker-title">
              待测模型（可多选）
            </span>
            <span class="model-picker-meta">
              <span class="model-picker-count" data-testid="model-picker-count">已选 {{ selectedModels.length }}/{{ models.length }}</span>
              <button type="button" class="picker-link" :disabled="!visibleModels.length || visibleModels.every((m) => selectedModels.includes(m.id))" @click="selectAllModels">全选</button>
              <button type="button" class="picker-link" :disabled="!selectedModels.length" @click="clearModels">清空</button>
            </span>
          </div>
          <p v-if="modelsError" class="model-source-warning" data-testid="model-source-warning">
            ⚠ 网关模型列表获取失败：{{ modelsError }}。当前展示本地目录回退，请检查上方「网关配置 · 模型列表获取」。
          </p>
          <div v-if="providerOptions.length > 1" class="provider-filter" data-testid="provider-filter">
            <span class="provider-filter-title">供应商</span>
            <div class="provider-filter-list">
              <button
                v-for="provider in providerOptions"
                :key="provider"
                type="button"
                class="provider-chip"
                :class="{ active: selectedProvider === provider }"
                @click="setSelectedProvider(provider)"
              >
                {{ provider === 'all' ? '全部' : provider }}
              </button>
            </div>
          </div>
          <div class="model-picker-list" data-testid="model-picker">
            <label v-for="model in visibleModels" :key="model.id" class="model-option">
              <input
                type="checkbox"
                :value="model.id"
                :checked="selectedModels.includes(model.id)"
                @change="toggleModel(model.id)"
              />
              <span>{{ modelLabel(model) }}</span>
            </label>
          </div>
        </section>

        <section v-if="benchmarksLoaded && benchmarks.length > 1" class="config-card benchmark-picker">
          <div class="benchmark-picker-head">
            <span class="benchmark-picker-title">评测方案</span>
            <span class="benchmark-plan-summary" data-testid="benchmark-plan-summary">{{ testPlanSummary }}</span>
          </div>

          <div class="benchmark-mode-note">
            <button
              type="button"
              class="hint-icon"
              :title="hint('多基准评测目前仅支持 API 轻量基准；Docker 容器化基准资源较重，请单独选择 1 个运行。多选模型会默认并行启动多个模型 run。')"
              aria-label="评测方案说明"
            >
              <CircleHelp :size="13" />
            </button>
          </div>

          <div class="benchmark-single-mode" data-testid="benchmark-picker">
            <div v-if="apiEvalBenchmarks.length" class="benchmark-group">
              <div class="benchmark-group-label">
                API 轻量 · 可多选
                <button
                  type="button"
                  class="hint-icon"
                  :title="hint('API 轻量基准直接通过模型调用网关作答，适合快速横向对比；发生临时网关错误时可按题重测。')"
                  aria-label="API 轻量基准说明"
                >
                  <CircleHelp :size="12" />
                </button>
              </div>
              <div class="benchmark-picker-list">
                <label
                  v-for="bm in apiEvalBenchmarks"
                  :key="bm.id"
                  class="benchmark-option"
                  :class="{ active: selectedBenchmarks.includes(bm.id) }"
                >
                  <input
                    type="checkbox"
                    :value="bm.id"
                    :checked="selectedBenchmarks.includes(bm.id)"
                    :disabled="running || hasActiveExecution()"
                    @change="toggleBenchmark(bm.id)"
                  />
                  <span class="benchmark-option-name">{{ bm.label }}</span>
                  <small v-if="bm.task_count">共 {{ bm.task_count }} 题</small>
                  <span v-if="bm.task_count" class="benchmark-count-control" @click.stop>
                    <span>本次</span>
                    <input
                      type="number"
                      min="1"
                      :max="bm.task_count"
                      :value="benchmarkQuestionCount(bm.id)"
                      :disabled="running || hasActiveExecution()"
                      :aria-label="`${bm.label}本次评测题数`"
                      @input.stop="setBenchmarkQuestionCount(bm.id, $event)"
                      @change.stop="setBenchmarkQuestionCount(bm.id, $event, true)"
                    />
                    <span>题</span>
                  </span>
                </label>
              </div>
            </div>
            <div v-if="dockerBenchmarks.length" class="benchmark-group">
              <div class="benchmark-group-label">
                Docker 容器化 · 单选运行
                <button
                  type="button"
                  class="hint-icon"
                  :title="hint('Docker 基准会启动容器化评测环境，首次运行可能需要构建镜像，资源消耗更高；当前不与其他基准混跑。')"
                  aria-label="Docker 容器化基准说明"
                >
                  <CircleHelp :size="12" />
                </button>
              </div>
              <div class="benchmark-picker-list">
                <label
                  v-for="bm in dockerBenchmarks"
                  :key="bm.id"
                  class="benchmark-option"
                  :class="{ active: selectedBenchmarks.includes(bm.id) }"
                >
                  <input
                    type="checkbox"
                    :value="bm.id"
                    :checked="selectedBenchmarks.includes(bm.id)"
                    :disabled="running || hasActiveExecution()"
                    @change="selectDockerBenchmark(bm.id, ($event.target as HTMLInputElement).checked)"
                  />
                  <span>{{ bm.label }}</span>
                  <small v-if="bm.task_count">{{ bm.task_count }}题</small>
                </label>
              </div>
            </div>
          </div>
        </section>

        <div class="run-actions">
          <button data-testid="start-test" class="primary-action" type="button" :disabled="running || hasActiveExecution() || !selectedModels.length || !selectedBenchmarks.length" @click="startRun">
            <Rocket :size="15" />
            {{ running ? '运行中...' : '开始评测' }}
          </button>
          <button data-testid="cancel-test" type="button" class="danger-action" :disabled="cancelling || !hasActiveExecution()" @click="cancelRun">
            <CircleStop :size="14" />
            {{ cancelling ? '中断中...' : '中断' }}
          </button>
          <button data-testid="resume-test" type="button" class="action" :disabled="resuming || (!isBatchResumable && !isMultiBatchResumable)" @click="resumeRun">
            <Play :size="14" />
            {{ resuming ? '接续中...' : '接续' }}
          </button>
          <button type="button" :disabled="loadingOptions" @click="refreshOptions">
            <RefreshCw :size="14" :class="{ spinning: loadingOptions }" />
            刷新
          </button>
        </div>
      </div>

      <div class="status-column">
        <section class="config-card status-card">
          <span class="config-card-title">评测进程</span>
          <p v-if="!execution && !batchInfo" class="status-empty">暂无评测任务，在左侧完成配置后点击「开始评测」</p>
          <div v-if="execution" class="execution-result" data-testid="execution-result">
            <b>{{ executionTitle(execution) }}</b>
            <div v-if="runProgress" class="task-progress" data-testid="task-progress">
              <div class="task-progress-bar" role="progressbar"
                :aria-valuenow="runProgress.completed" :aria-valuemin="0" :aria-valuemax="runProgress.total"
                aria-label="题目进度"
              >
                <i class="task-progress-fill" :style="{ width: `${runProgressPercent}%` }" />
              </div>
              <span class="task-progress-label">
                题目进度 {{ runProgress.completed }}/{{ runProgress.total }}{{ runProgressDetail }}
              </span>
            </div>
            <span v-else>任务 {{ execution.n_tasks }} 个</span>
            <span v-if="execution.effort">推理强度 {{ execution.effort }}</span>
            <span v-if="execution.error">{{ execution.error }}</span>
            <span v-if="publication">已发布 {{ publication.snapshot_id }}</span>
          </div>
          <div v-if="batchInfo" class="batch-progress" data-testid="batch-progress">
            <b>
              批量评测
              {{ batchInfo.models.filter((m) => m.status === 'completed' || m.status === 'failed').length }}/{{ batchInfo.models.length }}
              <template v-if="batchInfo.max_concurrent && batchInfo.max_concurrent > 1"> · 并行上限 {{ batchInfo.max_concurrent }}</template>
              <template v-else-if="batchInfo.current_model"> · 当前: {{ batchInfo.current_model }}</template>
            </b>
            <div class="batch-chips">
              <span v-for="m in batchInfo.models" :key="m.model_id" :class="['batch-chip', m.status]">
                {{ m.status === 'running' ? '▶' : '' }}{{ m.model_id }}
              </span>
            </div>
            <span v-if="batchInfo.status === 'failed'" class="error">{{ batchInfo.error }}</span>
            <span v-if="batchInfo.status === 'cancelled'" class="error">已取消</span>
            <span v-if="batchInfo.status === 'completed'" class="hint">已完成，请在下方「评测记录」选择记录发布到大盘</span>
          </div>
          <div v-if="multiBatchInfo" class="batch-progress" data-testid="multi-batch-progress">
            <b>
              {{ multiBatchKindLabel(multiBatchInfo) }}
              {{ multiBatchInfo.items.filter((i) => i.status === 'completed' || i.status === 'failed').length }}/{{ multiBatchInfo.items.length }}
              · 上限 {{ multiBatchInfo.max_concurrent }}
            </b>
            <div class="batch-chips">
              <span v-for="(i, idx) in multiBatchInfo.items" :key="idx" :class="['batch-chip', i.status]">
                {{ i.status === 'running' ? '▶' : '' }}{{ i.benchmark }}
              </span>
            </div>
            <span v-if="multiBatchInfo.status === 'failed'" class="error">{{ multiBatchInfo.error }}</span>
            <span v-if="multiBatchInfo.status === 'cancelled'" class="error">已取消</span>
            <span v-if="multiBatchInfo.status === 'completed' && multiBatchInfo.kind === 'gateway-retry'" class="hint">已完成，网关失败题已原位更新；已发布记录会自动重发布</span>
            <span v-else-if="multiBatchInfo.status === 'completed'" class="hint">已完成，请在下方「评测记录」选择记录发布到大盘</span>
          </div>
        </section>

        <section v-if="showLogs" class="config-card log-panel" data-testid="run-logs">
          <div class="log-head">
            <div class="log-title">
              <span class="log-icon" aria-hidden="true">></span>
              <h3>实时日志</h3>
              <span v-if="runProgress" class="log-progress" data-testid="log-progress">
                题目进度 {{ runProgress.completed }}/{{ runProgress.total }}
              </span>
            </div>
            <div class="log-controls">
              <label v-if="availableLogSources.length > 1">
                <span class="sr-only">日志来源</span>
                <select v-model="selectedLogSource" data-testid="log-source">
                  <option v-for="source in availableLogSources" :key="source.name" :value="source.name">
                    {{ logSourceLabels[source.name] ?? source.name }}
                  </option>
                </select>
              </label>
              <label class="autoscroll">
                <input v-model="logAutoScroll" type="checkbox" />
                自动滚动
              </label>
              <button type="button" :disabled="loadingLog" @click="refreshLog">刷新</button>
            </div>
          </div>
          <p v-if="logError" class="error">{{ logError }}</p>
          <div
            ref="logHost"
            class="log-viewport"
            data-testid="log-content"
            :data-loading="loadingLog || undefined"
          >
            <pre v-if="logContent">{{ logContent }}</pre>
            <p v-else-if="isBuildingEnvironment" class="log-hint" data-testid="build-hint">
              正在构建评测环境（首次运行该任务需拉取并构建 Docker 镜像，约 10–30 分钟）……
              此阶段暂无日志输出属正常现象，环境就绪后日志将自动出现。
            </p>
            <pre v-else>{{ '（暂无日志输出）' }}</pre>
          </div>
        </section>
      </div>
    </div>

    <!-- 评测记录：可筛选的历史 run，手动勾选后批量删除无效结果 -->
    <section class="config-card records-panel" data-testid="runs-panel">
      <div class="records-head">
        <span class="config-card-title">评测记录</span>
        <span class="panel-count">{{ allRuns.length }} 条</span>
        <div class="records-filters">
          <label>
            <span class="sr-only">状态</span>
            <select v-model="runStatusFilter" data-testid="runs-status-filter" aria-label="状态">
              <option value="all">全部状态</option>
              <option value="failed">仅失败</option>
              <option value="completed">已完成</option>
              <option value="queued">排队中</option>
              <option value="running">运行中</option>
            </select>
          </label>
          <label>
            <span class="sr-only">模型</span>
            <select v-model="runModelFilter" data-testid="runs-model-filter" aria-label="模型">
              <option value="all">全部模型</option>
              <option v-for="m in runModelOptions" :key="m" :value="m">{{ m }}</option>
            </select>
          </label>
          <label>
            <span class="sr-only">基准</span>
            <select v-model="runBenchmarkFilter" data-testid="runs-benchmark-filter" aria-label="基准">
              <option value="all">全部基准</option>
              <option v-for="b in runBenchmarkOptions" :key="b" :value="b">{{ b }}</option>
            </select>
          </label>
          <button type="button" :disabled="loadingRuns" data-testid="runs-refresh" @click="loadRuns">
            <RefreshCw :size="13" :class="{ spinning: loadingRuns }" />刷新
          </button>
        </div>
      </div>
      <p v-if="runsError" class="error">{{ runsError }}</p>
      <p v-if="runsNotice" class="notice" data-testid="runs-notice">{{ runsNotice }}</p>
      <div class="records-actions">
        <label class="records-select-all">
          <input
            type="checkbox"
            :checked="allFilteredSelected"
            :indeterminate.prop="someFilteredSelected && !allFilteredSelected"
            :disabled="!filteredRunsList.length"
            data-testid="runs-select-all"
            @change="toggleSelectAllFiltered"
          />
          <span>全选当前筛选</span>
        </label>
        <span class="records-selected-count" data-testid="runs-selected-count">已选 {{ selectedRunIds.length }} / {{ filteredRunsList.length }}</span>
        <button
          type="button"
          class="publish-action"
          :disabled="!selectedRunIds.length || publishingRuns || deletingRuns || retryingGatewayFailures"
          data-testid="publish-selected-runs"
          @click="publishSelectedRuns"
        >
          <Rocket :size="13" />
          {{ publishingRuns ? '发布中...' : `发布选中 (${selectedRunIds.length})` }}
        </button>
        <button
          type="button"
          class="publish-action"
          :disabled="!gatewayFailureRetestableRuns.length || retryingGatewayFailures || deletingRuns || publishingRuns || hasActiveExecution()"
          data-testid="retry-all-gateway-failures"
          title="一键重测所有 api-eval 记录中因模型网关不可达/超时/5xx/限流等临时错误失败的题目；模型答错不重测"
          @click="retryAllGatewayFailureRuns"
        >
          <RefreshCw :size="13" :class="{ spinning: retryingGatewayFailures }" />
          {{ retryingGatewayFailures ? '重测中...' : `一键重测 (${gatewayFailureRetestableQuestionCount})` }}
        </button>
        <button
          type="button"
          class="danger-action"
          :disabled="!selectedRunIds.length || deletingRuns || publishingRuns || retryingGatewayFailures"
          data-testid="delete-selected-runs"
          @click="deleteSelectedRuns"
        >
          <Trash2 :size="13" />
          {{ deletingRuns ? '删除中...' : `删除选中 (${selectedRunIds.length})` }}
        </button>
      </div>
      <p v-if="loadingRuns && !allRuns.length" class="loading">正在加载评测记录...</p>
      <div v-if="filteredRunsList.length" class="records-table" data-testid="runs-table">
        <table>
          <thead>
            <tr>
              <th class="col-check"></th>
              <th>模型</th>
              <th>基准</th>
              <th>状态</th>
              <th>任务数</th>
              <th>TTFT</th>
              <th>端到端</th>
              <th>tok/s</th>
              <th>输出Tok</th>
              <th>强度</th>
              <th>创建</th>
              <th>完成</th>
              <th>错误</th>
              <th class="col-action"></th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="run in filteredRunsList"
              :key="run.run_id"
              :class="{ selected: selectedRunIds.includes(run.run_id) }"
            >
              <td class="col-check">
                <input
                  type="checkbox"
                  :checked="selectedRunIds.includes(run.run_id)"
                  :data-testid="`run-select-${run.run_id}`"
                  @change="toggleRunSelected(run.run_id)"
                />
              </td>
              <td class="col-model" :title="run.model_id">{{ run.model_id }}</td>
              <td>{{ run.benchmark }}</td>
              <td>
                <span :class="['status-badge', `status-${run.status}`]">{{ runStatusLabel(run.status) }}</span>
              </td>
              <td>{{ run.n_tasks }}</td>
              <td>{{ formatSeconds(run.api_metrics?.avg_first_token_sec) }}</td>
              <td>{{ formatSeconds(run.api_metrics?.avg_wall_time_sec) }}</td>
              <td>{{ formatTokensPerSecond(run.api_metrics?.output_tokens_per_sec) }}</td>
              <td>{{ formatTokenCount(run.api_metrics?.output_tokens) }}</td>
              <td><span class="effort-tag">{{ run.effort }}</span></td>
              <td>{{ formatRunTime(run.created_at) }}</td>
              <td>{{ run.completed_at ? formatRunTime(run.completed_at) : '--' }}</td>
              <td class="run-error" :title="run.error || ''">{{ run.error || '--' }}</td>
              <td class="col-action">
                <button
                  v-if="runCanResume(run)"
                  type="button"
                  class="picker-link"
                  :disabled="running || deletingRuns"
                  :data-testid="`resume-run-${run.run_id}`"
                  title="接续中断的评测：已完成题目不重跑，中断题目继续执行"
                  @click="resumeSingleRun(run)"
                >
                  接续
                </button>
                <button
                  v-if="runIsApiEval(run) && (run.status === 'completed' || run.status === 'failed')"
                  type="button"
                  class="picker-link"
                  :disabled="publishingRunIds.has(run.run_id) || deletingRuns || retryingGatewayFailures || run.retryable_infrastructure_failure_count === 0"
                  :data-testid="`retry-run-${run.run_id}`"
                  :title="run.retryable_infrastructure_failure_count === 0 ? '没有基础设施/调用链失败题可重测' : '查看逐题结果：可单题重测或全部重测基础设施/调用链失败题'"
                  @click="openRunQuestions(run)"
                >
                  {{ retryingGatewayRunId === run.run_id ? '重测中...' : '重测' }}
                </button>
                <button
                  v-if="run.status === 'completed' && !run.snapshot_id"
                  type="button"
                  class="picker-link"
                  :disabled="publishingRunIds.has(run.run_id) || deletingRuns"
                  :data-testid="`publish-run-${run.run_id}`"
                  title="将该评测结果发布到大盘"
                  @click="publishRun(run)"
                >
                  {{ publishingRunIds.has(run.run_id) ? '发布中...' : '发布' }}
                </button>
                <button
                  v-if="run.status === 'completed' && run.snapshot_id"
                  type="button"
                  class="picker-link danger-link"
                  :disabled="publishingRunIds.has(run.run_id) || deletingRuns"
                  :data-testid="`unpublish-run-${run.run_id}`"
                  title="回撤发布：从大盘移除该 run 的快照"
                  @click="unpublishRun(run)"
                >
                  {{ publishingRunIds.has(run.run_id) ? '回撤中...' : '回撤' }}
                </button>
                <button
                  type="button"
                  class="picker-link danger-link"
                  :disabled="deletingRuns"
                  :data-testid="`delete-run-${run.run_id}`"
                  @click="deleteSingleRun(run.run_id)"
                >
                  删除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else-if="!loadingRuns" class="empty-state">当前筛选下暂无评测记录</div>
    </section>

    <!-- 逐题重测：展示某 api-eval run 的逐题结果，支持单题重测 / 全部重测 -->
    <div
      v-if="questionsRun"
      class="modal-backdrop"
      data-testid="questions-modal"
      @click.self="closeRunQuestions"
    >
      <div class="modal-card" role="dialog" aria-modal="true" aria-label="逐题结果">
        <div class="modal-head">
          <div class="modal-title">
            <h3>逐题结果</h3>
            <span class="modal-sub" :title="questionsRun.model_id">
              {{ questionsRun.model_id }} · {{ questionsRun.benchmark }} · {{ questionsRun.effort }}
            </span>
          </div>
          <div class="modal-actions">
            <button
              type="button"
              class="action"
              data-testid="retry-all-questions"
              :disabled="questionsLoading || !allRetryQuestions.length || retryingQuestionIds.size > 0"
              title="仅重测基础设施/调用链失败题；通过题和模型答案错误不重测"
              @click="retryQuestions(allRetryQuestions.map((q) => q.task_id))"
            >
              {{ retryingQuestionIds.size > 1 ? '重测中...' : '全部重测' }}
            </button>
            <button
              type="button"
              class="action"
              data-testid="download-evaluation-report"
              :disabled="reportLoading || !evaluationReport"
              title="下载包含逐题结果和覆盖统计的 JSON 报告"
              @click="downloadEvaluationReport"
            >
              下载报告
            </button>
            <button type="button" data-testid="questions-close" @click="closeRunQuestions">关闭</button>
          </div>
        </div>
        <p v-if="questionsError" class="error">{{ questionsError }}</p>
        <p v-if="reportError" class="error">{{ reportError }}</p>
        <p v-if="questionsLoading" class="loading">正在加载题目结果...</p>
        <div v-if="evaluationReport" class="evaluation-summary" data-testid="evaluation-summary">
          <span>覆盖 {{ evaluationReport.coverage.recorded_tasks }}/{{ evaluationReport.coverage.requested_tasks }}（{{ Math.round(evaluationReport.coverage.coverage_rate * 100) }}%）</span>
          <span>成功 {{ evaluationReport.coverage.success_count }}</span>
          <span>模型失败 {{ evaluationReport.coverage.model_failure_count }}</span>
          <span>基础设施错误 {{ evaluationReport.coverage.infrastructure_error_count }}</span>
          <span>未执行 {{ evaluationReport.coverage.not_executed_count }}</span>
          <span>当前口径通过率 {{ Math.round(evaluationReport.coverage.pass_rate_current_metric * 100) }}%</span>
        </div>
        <div v-if="evaluationReport && Object.keys(evaluationReport.coverage.failure_category_counts).length" class="failure-summary" data-testid="failure-summary">
          <span v-for="(count, category) in evaluationReport.coverage.failure_category_counts" :key="category">
            {{ failureCategoryLabel(category) }} {{ count }}
          </span>
        </div>
        <div v-else-if="!reportLoading && !evaluationReport && !questionsLoading" class="report-hint">
          当前 run 没有可生成的结构化报告，以下显示原始逐题结果。
        </div>
        <div v-if="questions.length" class="modal-body">
          <table>
            <thead>
              <tr>
                <th>题目</th>
                <th>状态</th>
                <th>失败类别</th>
                <th>TTFT</th>
                <th>端到端</th>
                <th>tok/s</th>
                <th>Token</th>
                <th>错误</th>
                <th class="col-action"></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="q in questions" :key="q.index">
                <td class="col-model" :title="q.task_id">{{ q.task_id }}</td>
                <td>
                  <span :class="['status-badge', `status-${q.status}`]">{{ questionStatusLabel(q.status) }}</span>
                </td>
                <td>{{ failureCategoryLabel(q.failure_category) }}</td>
                <td>{{ formatSeconds(questionMetric(q, 'ttft_sec') ?? questionMetric(q, 'first_token_sec')) }}</td>
                <td>{{ formatSeconds(questionMetric(q, 'wall_time_sec')) }}</td>
                <td>{{ formatTokensPerSecond(questionMetric(q, 'output_tokens_per_sec')) }}</td>
                <td>{{ formatTokenCount(questionMetric(q, 'output_tokens')) }}</td>
                <td class="run-error" :title="q.error_message || ''">{{ q.error_message || '--' }}</td>
                <td class="col-action">
                  <button
                    type="button"
                    class="picker-link"
                    :data-testid="`retry-question-${q.task_id}`"
                    :disabled="retryingQuestionIds.has(q.task_id) || q.recorded === false"
                    title="重测该题：重新调用模型作答并原位更新结果"
                    @click="retryQuestions([q.task_id])"
                  >
                    {{ retryingQuestionIds.has(q.task_id) ? '重测中...' : '重测' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else-if="!questionsLoading" class="empty-state">该 run 暂无逐题结果</div>
      </div>
    </div>
  </section>
</template>
