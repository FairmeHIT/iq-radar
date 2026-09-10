import * as echarts from 'echarts/core'
import type { EChartsType, EChartsOption } from 'echarts/core'
import { BarChart, LineChart, RadarChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, RadarComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { nextTick, onBeforeUnmount, onMounted, watch, type Ref } from 'vue'

echarts.use([AriaComponent, BarChart, GridComponent, LegendComponent, LineChart, RadarChart, RadarComponent, SVGRenderer, TooltipComponent])

export function useEChart(host: Ref<HTMLElement | null>, option: Ref<EChartsOption>) {
  let chart: EChartsType | null = null
  let resizeObserver: ResizeObserver | null = null

  function render() {
    if (!chart) return
    chart.setOption(option.value, { notMerge: true })
  }

  function dispose() {
    resizeObserver?.disconnect()
    resizeObserver = null
    chart?.dispose()
    chart = null
  }

  async function initialize(element: HTMLElement | null) {
    dispose()
    if (!element) return
    await nextTick()
    chart = echarts.init(element, undefined, { renderer: 'svg' })
    render()
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => chart?.resize())
      resizeObserver.observe(element)
    }
  }

  onMounted(() => {
    if (import.meta.env.MODE !== 'test') void initialize(host.value)
  })

  watch(option, render, { deep: true })

  onBeforeUnmount(dispose)
}
