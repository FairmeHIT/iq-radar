<script setup lang="ts">
import type { EChartsOption } from 'echarts/core'
import { computed, ref } from 'vue'
import { Gauge } from 'lucide-vue-next'
import type { RadarSeries } from '../types'
import { radarSeriesName } from '../viewModel'
import { useEChart } from './useEChart'

const props = defineProps<{ series: RadarSeries[] }>()
const chartHost = ref<HTMLElement | null>(null)
const colors = ['#2fd7ff', '#5ef2a0', '#ffc857', '#9b8cff', '#fb7185']

function value(item: RadarSeries, key: string) {
  return Math.max(0, Math.min(100, item.axes[key] ?? 0))
}

const option = computed<EChartsOption>(() => ({
  animation: false,
  color: colors,
  aria: { enabled: true, decal: { show: true } },
  tooltip: { trigger: 'item', backgroundColor: '#0a1929', borderColor: 'rgba(47,215,255,.4)', textStyle: { color: '#eaf4fb' } },
  legend: { bottom: 0, type: 'scroll', textStyle: { color: '#7f9ab3', fontSize: 11 }, icon: 'circle' },
  radar: {
    center: ['50%', '46%'],
    radius: '62%',
    splitNumber: 4,
    indicator: [
      { name: '额度友好', max: 100 },
      { name: '周任务量', max: 100 },
      { name: '周通过量', max: 100 },
      { name: '单次成本', max: 100 },
      { name: 'Token 效率', max: 100 },
    ],
    axisName: { color: '#a8d8f0', fontSize: 11 },
    axisLine: { lineStyle: { color: 'rgba(47,215,255,.25)' } },
    splitLine: { lineStyle: { color: ['rgba(47,215,255,.22)', 'rgba(47,215,255,.16)', 'rgba(47,215,255,.11)', 'rgba(47,215,255,.07)'] } },
    splitArea: { areaStyle: { color: ['rgba(47,215,255,.03)', 'rgba(47,215,255,.06)'] } },
  },
  series: [{
    type: 'radar',
    symbol: 'circle',
    symbolSize: 5,
    data: props.series.map((item, index) => ({
      name: radarSeriesName(item, props.series),
      value: ['quota_remaining_friendliness', 'tasks_per_week', 'passes_per_week', 'cost_per_pass_efficiency', 'token_efficiency'].map((key) => value(item, key)),
      lineStyle: { width: 2, shadowBlur: 8, shadowColor: colors[index % colors.length] },
      areaStyle: { opacity: 0.06 },
    })),
  }],
}))

useEChart(chartHost, option)
</script>

<template>
  <section class="panel chart-panel" data-testid="quota-radar">
    <i class="panel-corners" aria-hidden="true" />
    <div class="panel-head">
      <div class="panel-title">
        <span class="panel-icon panel-icon-amber"><Gauge :size="17" /></span>
        <div><h2>额度效率雷达</h2><p>可持续任务量与单位消耗</p></div>
      </div>
      <span class="panel-count">{{ series.length }} 组</span>
    </div>
    <div class="chart-stage">
      <div ref="chartHost" class="chart-canvas" role="img" aria-label="模型额度效率五维雷达图" />
      <div v-if="!series.length" class="empty-state chart-empty">当前筛选下暂无额度数据</div>
    </div>
  </section>
</template>
