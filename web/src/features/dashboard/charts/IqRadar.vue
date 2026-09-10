<script setup lang="ts">
import type { EChartsOption } from 'echarts/core'
import { computed, ref } from 'vue'
import { BrainCircuit } from 'lucide-vue-next'
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
      { name: '智力', max: 100 },
      { name: '通过率', max: 100 },
      { name: '稳定性', max: 100 },
      { name: '速度', max: 100 },
      { name: '成本效率', max: 100 },
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
      value: ['iq', 'pass_rate', 'stability', 'speed', 'cost_efficiency'].map((key) => value(item, key)),
      lineStyle: { width: 2, shadowBlur: 8, shadowColor: colors[index % colors.length] },
      areaStyle: { opacity: 0.06 },
    })),
  }],
}))

useEChart(chartHost, option)
</script>

<template>
  <section class="panel chart-panel" data-testid="iq-radar">
    <i class="panel-corners" aria-hidden="true" />
    <div class="panel-head">
      <div class="panel-title">
        <span class="panel-icon"><BrainCircuit :size="17" /></span>
        <div><h2>模型能力雷达</h2><p>智力、稳定性与执行效率</p></div>
      </div>
      <span class="panel-count">{{ series.length }} 组</span>
    </div>
    <div class="chart-stage">
      <div ref="chartHost" class="chart-canvas" role="img" aria-label="模型能力五维雷达图" />
      <div v-if="!series.length" class="empty-state chart-empty">当前筛选下暂无能力数据</div>
    </div>
  </section>
</template>
