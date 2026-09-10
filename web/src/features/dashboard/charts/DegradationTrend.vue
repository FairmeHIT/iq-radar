<script setup lang="ts">
import type { EChartsOption } from 'echarts/core'
import { computed, ref } from 'vue'
import { ChartNoAxesCombined } from 'lucide-vue-next'
import type { ModelEffortSummary } from '../types'
import { summaryRowName } from '../viewModel'
import { useEChart } from './useEChart'

const props = defineProps<{ summaries: ModelEffortSummary[] }>()
const chartHost = ref<HTMLElement | null>(null)
const labels = computed(() => props.summaries.map((item) => summaryRowName(item, props.summaries)))

function compactLabel(label: string) {
  const [model, effort] = label.split(' / ')
  const shortModel = model.length > 11 ? `${model.slice(0, 10)}…` : model
  return `${shortModel}\n${effort}`
}

const option = computed<EChartsOption>(() => ({
  animation: false,
  color: ['#2fd7ff', '#ffc857'],
  aria: { enabled: true, decal: { show: true } },
  grid: { left: 42, right: 46, top: 34, bottom: 62 },
  tooltip: { trigger: 'axis', backgroundColor: '#0a1929', borderColor: 'rgba(47,215,255,.4)', textStyle: { color: '#eaf4fb' } },
  legend: { top: 0, right: 0, textStyle: { color: '#7f9ab3', fontSize: 11 } },
  xAxis: { type: 'category', data: labels.value, axisLabel: { color: '#7f9ab3', fontSize: 10, interval: 0, formatter: compactLabel }, axisLine: { lineStyle: { color: 'rgba(47,215,255,.3)' } }, axisTick: { show: false } },
  yAxis: [
    { type: 'value', name: 'IQ', min: 0, max: 150, nameTextStyle: { color: '#7f9ab3' }, axisLabel: { color: '#7f9ab3' }, splitLine: { lineStyle: { color: 'rgba(47,215,255,.1)' } } },
    { type: 'value', name: '成本', nameTextStyle: { color: '#7f9ab3' }, axisLabel: { color: '#7f9ab3', formatter: '${value}' }, splitLine: { show: false } },
  ],
  series: [
    {
      name: 'IQ', type: 'bar', barMaxWidth: 32,
      data: props.summaries.map((item) => Number(item.iq.toFixed(1))),
      itemStyle: {
        borderRadius: [3, 3, 0, 0],
        color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(47,215,255,.95)' }, { offset: 1, color: 'rgba(47,215,255,.15)' }] },
      },
    },
    {
      name: '平均成本', type: 'line', yAxisIndex: 1, smooth: 0.25, symbol: 'circle', symbolSize: 7,
      data: props.summaries.map((item) => Number(item.avg_cost_usd.toFixed(3))),
      lineStyle: { width: 2.5, shadowBlur: 12, shadowColor: 'rgba(255,200,87,.5)' },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(255,200,87,.25)' }, { offset: 1, color: 'rgba(255,200,87,0)' }] } },
    },
  ],
}))

useEChart(chartHost, option)
</script>

<template>
  <section class="panel chart-panel" data-testid="degradation-trend">
    <i class="panel-corners" aria-hidden="true" />
    <div class="panel-head">
      <div class="panel-title">
        <span class="panel-icon panel-icon-green"><ChartNoAxesCombined :size="17" /></span>
        <div><h2>模型效率对比</h2><p>IQ 与单任务平均成本</p></div>
      </div>
      <span class="panel-count">{{ summaries.length }} 组</span>
    </div>
    <div class="chart-stage chart-stage-compact">
      <div ref="chartHost" class="chart-canvas" role="img" aria-label="模型智力与成本对比图" />
      <div v-if="!summaries.length" class="empty-state chart-empty">当前筛选下暂无汇总数据</div>
    </div>
  </section>
</template>
