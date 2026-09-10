<script setup lang="ts">
import { Crosshair, Medal, TrendingUp } from 'lucide-vue-next'
import type { ScenarioRanking } from '../types'

defineProps<{ rankings: ScenarioRanking[] }>()

const SOURCE_LABELS: Record<string, string> = {
  'arena-ai': 'Arena',
  'llm-stats': 'LLM-Stats',
}

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}

function sourceBadgeClass(index: number): string {
  return ['badge-cyan', 'badge-amber', 'badge-green', 'badge-violet'][index % 4]
}
</script>

<template>
  <section class="panel run-panel" data-testid="ranking-table">
    <i class="panel-corners" aria-hidden="true" />
    <div class="panel-head">
      <div class="panel-title">
        <span class="panel-icon"><TrendingUp :size="17" /></span>
        <div><h2>场景模型排名</h2><p>综合外部榜单得分</p></div>
      </div>
      <span class="panel-count">{{ rankings.length }} 个模型</span>
    </div>
    <div v-if="rankings.length" class="ranking-table">
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>模型</th>
            <th>供应商</th>
            <th>综合得分</th>
            <th>数据来源</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rankings" :key="row.model_name">
            <td>
              <span class="rank-badge" :class="{ 'rank-top': row.rank <= 3 }">
                <Medal v-if="row.rank <= 3" :size="14" />
                <template v-else>{{ row.rank }}</template>
              </span>
            </td>
            <td>
              <strong class="model-name">{{ row.model_display }}</strong>
              <small class="model-id">{{ row.model_name }}</small>
            </td>
            <td><span class="provider">{{ row.provider ?? '—' }}</span></td>
            <td>
              <span class="score">{{ row.composite_score.toFixed(1) }}</span>
              <span class="score-bar">
                <i :style="{ width: `${Math.max(2, Math.min(100, row.composite_score))}%` }" />
              </span>
            </td>
            <td>
              <span class="source-badges">
                <span
                  v-for="(source, index) in row.sources"
                  :key="source"
                  :class="['source-badge', sourceBadgeClass(index)]"
                >
                  <Crosshair :size="10" />{{ sourceLabel(source) }}
                </span>
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty-state">当前场景暂无实时数据，可点击"刷新榜单"拉取</div>
  </section>
</template>