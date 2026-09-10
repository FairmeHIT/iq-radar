<script setup lang="ts">
import { CheckCircle2, CircleDotDashed, ListChecks, XCircle } from 'lucide-vue-next'
import type { RunRecord } from '../types'

defineProps<{ runs: RunRecord[] }>()

const STATUS_LABELS: Record<string, string> = {
  passed: '通过',
  failed: '失败',
  timeout: '超时',
  runner_error: '基础设施错误',
  verifier_error: '校验器错误',
  budget_stopped: '预算中止',
  skipped: '跳过',
}

// 与后端 metrics/aggregate.py 的 INFRA_ERROR_STATUSES 一致：这些状态没有
// 走到评分裁决，不计入通过率与 IQ 的分母（tasks_scored）。
const INFRA_STATUSES = new Set(['runner_error', 'verifier_error'])

function statusLabel(status: string) {
  return STATUS_LABELS[status] ?? status
}

function isInfraError(status: string) {
  return INFRA_STATUSES.has(status)
}

function formatTokens(value: number) {
  return value >= 10000 ? `${(value / 1000).toFixed(1)}k` : value.toLocaleString()
}
</script>

<template>
  <section class="panel run-panel" data-testid="run-table">
    <i class="panel-corners" aria-hidden="true" />
    <div class="panel-head">
      <div class="panel-title">
        <span class="panel-icon panel-icon-violet"><ListChecks :size="17" /></span>
        <div><h2>当前快照来源</h2><p>只读展示已发布到大盘的评测明细</p></div>
      </div>
      <span class="panel-count">{{ runs.length }} 条</span>
    </div>
    <div v-if="runs.length" class="run-table">
      <table>
        <thead><tr><th>任务</th><th>模型</th><th>强度</th><th>状态</th><th>耗时</th><th>成本</th><th>输出</th><th>缓存</th><th>步数</th></tr></thead>
        <tbody>
          <tr v-for="run in runs" :key="run.run_id">
            <td><strong class="task-name">{{ run.benchmark.task_id }}</strong><small>{{ run.benchmark.language }}</small></td>
            <td>{{ run.model.name }}</td>
            <td><span class="effort-tag">{{ run.model.effort_requested }}</span></td>
            <td>
              <span
                :class="['status-badge', `status-${run.result.status}`]"
                :title="isInfraError(run.result.status) ? '基础设施错误：未完成评分，不计入通过率与 IQ 分母' : undefined"
              >
                <CheckCircle2 v-if="run.result.status === 'passed'" :size="13" />
                <XCircle v-else-if="run.result.status === 'failed'" :size="13" />
                <CircleDotDashed v-else :size="13" />
                {{ statusLabel(run.result.status) }}
              </span>
            </td>
            <td>{{ (run.usage.wall_time_sec / 60).toFixed(1) }}m</td>
            <td>${{ run.cost.total_cost.toFixed(2) }}</td>
            <td>{{ formatTokens(run.usage.output_tokens) }}</td>
            <td>{{ formatTokens(run.usage.cached_input_tokens) }}</td>
            <!-- agent_steps=0 表示该记录没有 agent 轨迹（采集上线前的旧记录或
                 agent 异常退出），显示 N/A 而不是把"未采集"当"0 步"展示。 -->
            <td>
              <span :title="run.usage.agent_steps ? undefined : '未采集：该记录没有 agent 轨迹（采集上线前的旧记录或 agent 异常退出）'">
                {{ run.usage.agent_steps > 0 ? run.usage.agent_steps : '--' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty-state">当前筛选下暂无已发布来源明细</div>
  </section>
</template>
