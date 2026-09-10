<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { BrainCircuit, CheckCircle2, CircleDollarSign, Clock3, Database, Gauge, Layers3, RefreshCw, SlidersHorizontal, Trash2 } from 'lucide-vue-next'
import DegradationTrend from './charts/DegradationTrend.vue'
import IqRadar from './charts/IqRadar.vue'
import QuotaRadar from './charts/QuotaRadar.vue'
import SnapshotSourceTable from './components/SnapshotSourceTable.vue'
import { deleteSnapshot, fetchDashboard, fetchSnapshots } from './api'
import type { DashboardView } from './viewModel'
import { aggregateMetrics, buildDashboardView, normalizeSelection } from './viewModel'
import type { RadarSeries, SnapshotInfo } from './types'

const dashboard = ref<DashboardView | null>(null)
const selectedModel = ref('all')
const selectedEffort = ref('all')
const selectedSnapshot = ref<string>('')
const snapshots = ref<SnapshotInfo[]>([])
const error = ref<string | null>(null)
const loading = ref(false)
const deletingSnapshot = ref(false)
const requestId = ref(0)

const summaries = computed(() => dashboard.value?.bundle.summary.summaries ?? [])
const iqRadar = computed(() => dashboard.value?.bundle.iq_radar ?? [])
const quotaRadar = computed(() => dashboard.value?.bundle.quota_radar ?? [])
const runs = computed(() => dashboard.value?.bundle.runs ?? [])
const models = computed(() => [...new Set(summaries.value.map((item) => item.model))])
const efforts = computed(() => [...new Set(summaries.value.map((item) => item.effort))])
const filteredSummaries = computed(() => summaries.value.filter(matchesFilters))
const metrics = computed(() => aggregateMetrics(filteredSummaries.value))
const filteredIqRadar = computed(() => filterRadar(iqRadar.value))
const filteredQuotaRadar = computed(() => filterRadar(quotaRadar.value))
const filteredRuns = computed(() => runs.value.filter(
  (item) => (selectedModel.value === 'all' || item.model.name === selectedModel.value)
    && (selectedEffort.value === 'all' || item.model.effort_requested === selectedEffort.value),
))
const generatedAt = computed(() => {
  const value = dashboard.value?.bundle.summary.generated_at
  return value ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) : '--'
})
const sourceState = computed(() => {
  if (error.value) return { label: '数据异常', className: 'error' }
  if (!dashboard.value) return { label: loading.value ? '正在连接' : '等待数据', className: 'pending' }
  return { label: dashboard.value.isDemo ? '模拟数据' : '实时数据', className: dashboard.value.isDemo ? 'demo' : '' }
})

function matchesFilters(item: { model: string; effort: string }) {
  return (selectedModel.value === 'all' || item.model === selectedModel.value)
    && (selectedEffort.value === 'all' || item.effort === selectedEffort.value)
}

function filterRadar(series: RadarSeries[]) {
  return series.filter(matchesFilters)
}

function formatDuration(seconds: number) {
  if (!seconds) return '--'
  return seconds >= 60 ? `${(seconds / 60).toFixed(1)}m` : `${seconds.toFixed(0)}s`
}

function snapshotLabel(snap: SnapshotInfo): string {
  const date = snap.published_at
    ? new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(snap.published_at))
    : '--'
  const models = snap.models.length > 2
    ? `${snap.models.length} 个模型`
    : snap.models.join(', ') || '未知'
  const tag = snap.is_current ? '最新 · ' : ''
  const orphan = snap.source_exists === false ? ' · 源已删' : ''
  return `${tag}${date} · ${models} · ${snap.tasks_total} 题${orphan}`
}

function snapshotSourceIds(snap: SnapshotInfo): string[] {
  if (snap.source_job_ids?.length) return snap.source_job_ids
  return snap.source_job_id ? [snap.source_job_id] : []
}

const selectedSnapshotInfo = computed(
  () => snapshots.value.find((s) => s.snapshot_id === selectedSnapshot.value) ?? null,
)

async function loadSnapshots() {
  try {
    const list = await fetchSnapshots()
    snapshots.value = list
    if (!selectedSnapshot.value && list.length) {
      selectedSnapshot.value = list.find((s) => s.is_current)?.snapshot_id ?? list[0].snapshot_id
    }
  } catch {
    snapshots.value = []
  }
}

async function refreshDashboard() {
  const currentRequest = requestId.value + 1
  requestId.value = currentRequest
  loading.value = true
  error.value = null
  try {
    const bundle = await fetchDashboard(selectedSnapshot.value || undefined)
    if (currentRequest !== requestId.value) return
    const view = buildDashboardView(bundle)
    const nextModels = [...new Set(view.bundle.summary.summaries.map((item) => item.model))]
    const nextEfforts = [...new Set(view.bundle.summary.summaries.map((item) => item.effort))]
    selectedModel.value = normalizeSelection(selectedModel.value, nextModels)
    selectedEffort.value = normalizeSelection(selectedEffort.value, nextEfforts)
    dashboard.value = view
  } catch (caught) {
    if (currentRequest === requestId.value) {
      error.value = caught instanceof Error ? caught.message : '无法加载雷达数据'
    }
  } finally {
    if (currentRequest === requestId.value) loading.value = false
  }
}

function onSnapshotChange() {
  selectedModel.value = 'all'
  selectedEffort.value = 'all'
  void refreshDashboard()
}

async function deleteSelectedSnapshot() {
  const snap = selectedSnapshotInfo.value
  if (!snap || deletingSnapshot.value) return
  const consequence = snap.is_current
    ? '该快照是当前默认快照，删除后大盘会自动切到剩余最新的快照（若已无快照则回到空态）。'
    : '删除后该快照将不再出现在选择器中。'
  const sources = snapshotSourceIds(snap)
  const sourceNote = sources.length ? `${sources.join('、')}${sources.length > 1 ? `（共 ${sources.length} 个来源）` : ''}` : '未知'
  if (!window.confirm(`确认删除该评测快照？\n\n${snapshotLabel(snap)}\n源记录：${sourceNote}\n\n${consequence}\n快照数据将从磁盘删除，无法恢复。`)) return
  deletingSnapshot.value = true
  error.value = null
  try {
    await deleteSnapshot(snap.snapshot_id)
    selectedSnapshot.value = ''
    dashboard.value = null
    await loadSnapshots()
    await refreshDashboard()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '删除快照失败'
  } finally {
    deletingSnapshot.value = false
  }
}

onMounted(async () => {
  await loadSnapshots()
  await refreshDashboard()
})
</script>

<template>
  <div data-testid="dashboard-page" class="dashboard-page">
    <section class="dashboard-toolbar" aria-label="仪表盘控制">
      <div class="data-status">
        <span :class="['source-badge', sourceState.className]" data-testid="data-mode">
          <Database :size="13" />{{ sourceState.label }}
        </span>
        <span class="update-time">更新于 {{ generatedAt }}</span>
        <span v-if="loading" class="syncing"><RefreshCw :size="13" />同步中</span>
      </div>
      <div class="filters">
        <span class="filter-label"><SlidersHorizontal :size="15" />筛选</span>
        <label v-if="snapshots.length">
          <span class="sr-only">发布版本</span>
          <select :value="selectedSnapshot" data-testid="snapshot-filter" aria-label="发布版本" @change="selectedSnapshot = ($event.target as HTMLSelectElement).value; onSnapshotChange()">
            <option v-for="snap in snapshots" :key="snap.snapshot_id" :value="snap.snapshot_id">
              {{ snapshotLabel(snap) }}
            </option>
          </select>
        </label>
        <button
          v-if="snapshots.length"
          class="icon-button danger-action"
          type="button"
          :disabled="deletingSnapshot || loading || !selectedSnapshot"
          :title="deletingSnapshot ? '删除中...' : '删除选中的评测快照'"
          aria-label="删除选中的评测快照"
          data-testid="delete-snapshot"
          @click="deleteSelectedSnapshot"
        >
          <Trash2 :size="15" :class="{ spinning: deletingSnapshot }" />
        </button>
        <label>
          <span class="sr-only">模型</span>
          <select v-model="selectedModel" data-testid="model-filter" aria-label="模型">
            <option value="all">全部模型</option>
            <option v-for="model in models" :key="model" :value="model">{{ model }}</option>
          </select>
        </label>
        <label>
          <span class="sr-only">推理强度</span>
          <select v-model="selectedEffort" data-testid="effort-filter" aria-label="推理强度">
            <option value="all">全部强度</option>
            <option v-for="effort in efforts" :key="effort" :value="effort">{{ effort }}</option>
          </select>
        </label>
        <button class="icon-button refresh-button" type="button" :disabled="loading" aria-label="刷新数据" title="刷新数据" @click="refreshDashboard">
          <RefreshCw :size="17" :class="{ spinning: loading }" />
        </button>
      </div>
    </section>

    <div v-if="dashboard?.isDemo" class="demo-notice">
      <span>DEMO</span> 当前暂无真实评测数据，已载入一组固定模拟数据用于展示。
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <section class="kpis" aria-label="关键指标">
      <article class="kpi kpi-accent-cyan">
        <i class="panel-corners" aria-hidden="true" />
        <div class="kpi-head"><span class="kpi-icon"><BrainCircuit :size="18" /></span><span>综合 IQ</span></div>
        <strong>{{ metrics.tasksScored ? metrics.iq.toFixed(1) : '--' }}</strong>
        <small>按 {{ metrics.tasksScored }} 个评分任务加权</small>
      </article>
      <article class="kpi kpi-accent-green">
        <i class="panel-corners" aria-hidden="true" />
        <div class="kpi-head"><span class="kpi-icon"><CheckCircle2 :size="18" /></span><span>通过率</span></div>
        <strong>{{ metrics.tasksScored ? `${metrics.passRatePercent.toFixed(1)}%` : '--' }}</strong>
        <small>
          {{ metrics.tasksPassed }} / {{ metrics.tasksScored }} 已通过<template v-if="metrics.infraErrorCount"> · 已剔除 {{ metrics.infraErrorCount }} 个基础设施错误</template>
        </small>
      </article>
      <article class="kpi kpi-accent-blue">
        <i class="panel-corners" aria-hidden="true" />
        <div class="kpi-head"><span class="kpi-icon"><Layers3 :size="18" /></span><span>样本量</span></div>
        <strong>{{ metrics.tasksTotal || '--' }}</strong>
        <small>{{ filteredSummaries.length }} 个模型配置<template v-if="metrics.infraErrorCount"> · 含 {{ metrics.infraErrorCount }} 个基础设施错误</template></small>
      </article>
      <article class="kpi kpi-accent-amber">
        <i class="panel-corners" aria-hidden="true" />
        <div class="kpi-head"><span class="kpi-icon"><CircleDollarSign :size="18" /></span><span>总成本</span></div>
        <strong>{{ metrics.tasksTotal ? `$${metrics.totalCostUsd.toFixed(2)}` : '--' }}</strong>
        <small>{{ metrics.tasksTotal ? `$${(metrics.totalCostUsd / metrics.tasksTotal).toFixed(3)} / 任务` : '暂无消耗' }}</small>
      </article>
      <article class="kpi kpi-accent-violet">
        <i class="panel-corners" aria-hidden="true" />
        <div class="kpi-head"><span class="kpi-icon"><Clock3 :size="18" /></span><span>平均耗时</span></div>
        <strong>{{ formatDuration(metrics.avgWallTimeSec) }}</strong>
        <small>端到端 wall time</small>
      </article>
      <article class="kpi kpi-accent-rose">
        <i class="panel-corners" aria-hidden="true" />
        <div class="kpi-head"><span class="kpi-icon"><Gauge :size="18" /></span><span>周任务容量</span></div>
        <strong>{{ metrics.estimatedTasksPerWeek || '--' }}</strong>
        <small>基于当前额度估算</small>
      </article>
    </section>

    <section class="charts">
      <IqRadar :series="filteredIqRadar" />
      <QuotaRadar :series="filteredQuotaRadar" />
    </section>

    <section class="bottom">
      <DegradationTrend :summaries="filteredSummaries" />
      <SnapshotSourceTable :runs="filteredRuns" />
    </section>
  </div>
</template>
