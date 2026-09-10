import { delJson, getJson, postJson } from '../../shared/http'
import type {
  BatchRequest,
  BenchmarkChoice,
  DeepSweBatch,
  DeepSweRun,
  DeepSweRunRequest,
  EvaluationReport,
  GatewayChatProbe,
  GatewayModelsProbe,
  GatewaySettings,
  GatewaySettingsRequest,
  LogSource,
  ModelChoice,
  ModelsResponse,
  MultiBenchRequest,
  MultiBenchState,
  Publication,
  RunDeleteResult,
  RunLogs,
  RunPublishResult,
  RunQuestion,
} from './types'

export function fetchModels(): Promise<ModelsResponse> {
  return getJson<ModelsResponse>('/api/models')
}

export function fetchBenchmarks(): Promise<BenchmarkChoice[]> {
  return getJson<BenchmarkChoice[]>('/api/benchmarks')
}

export function submitDeepSweRun(request: DeepSweRunRequest): Promise<DeepSweRun> {
  return postJson<DeepSweRun>('/api/deepswe-runs', request)
}

export function submitBatch(request: BatchRequest): Promise<DeepSweBatch> {
  return postJson<DeepSweBatch>('/api/deepswe-batches', request)
}

export function fetchBatch(batchId: string): Promise<DeepSweBatch> {
  return getJson<DeepSweBatch>(`/api/deepswe-batches/${encodeURIComponent(batchId)}`)
}

export function fetchLatestBatch(): Promise<DeepSweBatch | null> {
  return getJson<DeepSweBatch | null>('/api/deepswe-batches/latest')
}

export function fetchDeepSweRun(runId: string): Promise<DeepSweRun> {
  return getJson<DeepSweRun>(`/api/deepswe-runs/${encodeURIComponent(runId)}`)
}

export function fetchLatestDeepSweRun(): Promise<DeepSweRun | null> {
  return getJson<DeepSweRun | null>('/api/deepswe-runs/latest')
}

export function fetchDeepSweRuns(): Promise<DeepSweRun[]> {
  return getJson<DeepSweRun[]>('/api/deepswe-runs')
}

export function deleteDeepSweRun(runId: string): Promise<{ run_id: string; deleted: boolean }> {
  return delJson<{ run_id: string; deleted: boolean }>(
    `/api/deepswe-runs/${encodeURIComponent(runId)}`,
  )
}

export function deleteDeepSweRuns(runIds: string[]): Promise<RunDeleteResult> {
  return postJson<RunDeleteResult>('/api/deepswe-runs/delete-batch', { run_ids: runIds })
}

export function publishDeepSweRun(runId: string): Promise<Publication> {
  return postJson<Publication>(`/api/deepswe-runs/${encodeURIComponent(runId)}/publish`)
}

/** 发布选中：把多条已完成 run 合并发布成一个累积快照。 */
export function publishDeepSweRuns(runIds: string[], merge = true): Promise<RunPublishResult> {
  const query = merge ? '' : '?merge=false'
  return postJson<RunPublishResult>('/api/deepswe-runs/publish-batch' + query, {
    run_ids: runIds,
  })
}

export function fetchRunQuestions(runId: string): Promise<RunQuestion[]> {
  return getJson<{ run_id: string; questions: RunQuestion[] }>(
    `/api/deepswe-runs/${encodeURIComponent(runId)}/questions`,
  ).then((payload) => payload.questions)
}

export function fetchEvaluationReport(runId: string): Promise<EvaluationReport> {
  return getJson<EvaluationReport>(
    `/api/deepswe-runs/${encodeURIComponent(runId)}/evaluation-report`,
  )
}

/** 重测指定题目：单题传一个 task_id，全部重测传该 run 的全部 task_id。 */
export function retryRunQuestions(runId: string, taskIds: string[]): Promise<DeepSweRun> {
  return postJson<DeepSweRun>(
    `/api/deepswe-runs/${encodeURIComponent(runId)}/retry-questions`,
    { task_ids: taskIds },
  )
}

export function unpublishDeepSweRun(
  runId: string,
): Promise<{ snapshot_id: string; current_snapshot_id: string | null }> {
  return delJson<{ snapshot_id: string; current_snapshot_id: string | null }>(
    `/api/deepswe-runs/${encodeURIComponent(runId)}/publish`,
  )
}

export function cancelDeepSweRun(runId: string): Promise<DeepSweRun> {
  return postJson<DeepSweRun>(`/api/deepswe-runs/${encodeURIComponent(runId)}/cancel`)
}

export function cancelBatch(batchId: string): Promise<DeepSweBatch> {
  return postJson<DeepSweBatch>(`/api/deepswe-batches/${encodeURIComponent(batchId)}/cancel`)
}

export function resumeBatch(batchId: string): Promise<DeepSweBatch> {
  return postJson<DeepSweBatch>(`/api/deepswe-batches/${encodeURIComponent(batchId)}/resume`)
}

export function fetchDeepSweRunPublication(runId: string): Promise<Publication | null> {
  return getJson<Publication | null>(`/api/deepswe-runs/${encodeURIComponent(runId)}/publication`)
}

export async function fetchRunLogSources(runId: string): Promise<LogSource[]> {
  const logs = await getJson<RunLogs>(`/api/deepswe-runs/${encodeURIComponent(runId)}/logs`)
  return logs.sources
}

export function fetchRunLog(runId: string, source: string, tail?: number): Promise<RunLogs> {
  const tailQuery = typeof tail === 'number' && tail > 0 ? `&tail=${tail}` : ''
  return getJson<RunLogs>(
    `/api/deepswe-runs/${encodeURIComponent(runId)}/logs?source=${encodeURIComponent(source)}${tailQuery}`,
  )
}

export function fetchGatewaySettings(): Promise<GatewaySettings> {
  return getJson<GatewaySettings>('/api/gateway-settings')
}

export function saveGatewaySettings(request: GatewaySettingsRequest): Promise<GatewaySettings> {
  return postJson<GatewaySettings>('/api/gateway-settings', request)
}

export function testGatewayModels(
  payload: { base_url?: string; api_key?: string } = {},
): Promise<GatewayModelsProbe> {
  return postJson<GatewayModelsProbe>('/api/gateway-settings/test-models', payload)
}

export function testGatewayChat(payload: {
  model: string
  base_url?: string
  api_key?: string
  prompt?: string
}): Promise<GatewayChatProbe> {
  return postJson<GatewayChatProbe>('/api/gateway-settings/test-chat', payload)
}

export function submitMultiBench(request: MultiBenchRequest): Promise<MultiBenchState> {
  return postJson<MultiBenchState>('/api/deepswe-multi-batches', request)
}

export function fetchMultiBatch(batchId: string): Promise<MultiBenchState> {
  return getJson<MultiBenchState>(`/api/deepswe-multi-batches/${encodeURIComponent(batchId)}`)
}

export function fetchLatestMultiBatch(): Promise<MultiBenchState | null> {
  return getJson<MultiBenchState | null>('/api/deepswe-multi-batches/latest')
}

export function cancelMultiBatch(batchId: string): Promise<MultiBenchState> {
  return postJson<MultiBenchState>(`/api/deepswe-multi-batches/${encodeURIComponent(batchId)}/cancel`)
}

export function resumeMultiBatch(batchId: string): Promise<MultiBenchState> {
  return postJson<MultiBenchState>(`/api/deepswe-multi-batches/${encodeURIComponent(batchId)}/resume`)
}