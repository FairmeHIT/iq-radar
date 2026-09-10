<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { BarChart, Bot, Code, FileText, Globe, Layout, Lightbulb, PenLine, RefreshCw, Terminal } from 'lucide-vue-next'
import { fetchScenarioView, fetchScenarios, refreshLeaderboards } from './api'
import { createDemoScenarioView } from './demoData'
import type { ScenarioDef, ScenarioView } from './types'
import LeaderboardPoster from './components/LeaderboardPoster.vue'
import ModelRankingTable from './components/ModelRankingTable.vue'

const ICON_MAP: Record<string, object> = {
  FileText, PenLine, Code, Layout, Terminal, BarChart, Lightbulb, Bot,
}

const scenarios = ref<ScenarioDef[]>([])
const scenarioViews = ref<Record<string, ScenarioView | null>>({})
const activeScenario = ref('')
const loading = ref(false)
const refreshing = ref(false)
const error = ref<string | null>(null)
const requestId = ref(0)

const activeView = computed(() => {
  const id = activeScenario.value
  if (!id) return null
  return scenarioViews.value[id] ?? null
})

const activePosters = computed(() => {
  return activeView.value?.posters ?? []
})

const hasRankings = computed(() => {
  return (activeView.value?.rankings.length ?? 0) > 0
})

onMounted(async () => {
  loading.value = true
  try {
    const list = await fetchScenarios()
    scenarios.value = list
    if (list.length > 0) {
      activeScenario.value = list[0].id
      await loadScenario(list[0].id)
    }
  } catch (caught) {
    error.value = '无法加载场景列表'
    // 加载 demo 数据兜底
    scenarios.value = [
      { id: 'backend', label: '后端开发', description: '代码生成、Bug修复、重构', icon: 'Code' },
      { id: 'office', label: '办公效率', description: '文档、表格、邮件', icon: 'FileText' },
      { id: 'writing', label: '创意写作', description: '文案、翻译、润色', icon: 'PenLine' },
      { id: 'agent', label: 'Agent 自动化', description: '工具调用、多步规划', icon: 'Bot' },
    ]
    activeScenario.value = 'backend'
    scenarioViews.value['backend'] = createDemoScenarioView('backend')
  } finally {
    loading.value = false
  }
})

async function loadScenario(id: string) {
  if (scenarioViews.value[id]) return
  try {
    error.value = null
    const view = await fetchScenarioView(id)
    scenarioViews.value[id] = view
  } catch {
    // 使用 demo 数据兜底
    scenarioViews.value[id] = createDemoScenarioView(id)
  }
}

async function switchScenario(id: string) {
  activeScenario.value = id
  if (!scenarioViews.value[id]) {
    await loadScenario(id)
  }
}

async function refresh() {
  const currentRequest = ++requestId.value
  refreshing.value = true
  error.value = null
  try {
    await refreshLeaderboards()
    if (currentRequest !== requestId.value) return
    // 重新加载所有场景视图
    scenarioViews.value = {}
    if (activeScenario.value) {
      await loadScenario(activeScenario.value)
    }
  } catch (caught) {
    if (currentRequest === requestId.value) {
      error.value = '刷新失败，请稍后重试'
    }
  } finally {
    if (currentRequest === requestId.value) {
      refreshing.value = false
    }
  }
}

function scenarioIcon(icon: string): object {
  return ICON_MAP[icon] ?? FileText
}
</script>

<template>
  <section class="leaderboard-page">
    <header class="lb-toolbar">
      <div class="lb-toolbar-left">
        <span class="lb-title">外部榜单汇聚</span>
        <span class="lb-subtitle">按场景查看模型排名</span>
      </div>
      <div class="lb-toolbar-right">
        <span v-if="refreshing" class="refresh-status">刷新中…</span>
        <button type="button" :disabled="refreshing" @click="refresh">
          <RefreshCw :size="14" :class="{ spinning: refreshing }" />刷新榜单
        </button>
      </div>
    </header>

    <p v-if="error" class="lb-error">{{ error }}</p>

    <nav class="lb-scenario-tabs" aria-label="场景选择">
      <button
        v-for="sc in scenarios"
        :key="sc.id"
        type="button"
        :class="['lb-scenario-tab', { active: activeScenario === sc.id }]"
        :aria-current="activeScenario === sc.id ? 'true' : undefined"
        @click="switchScenario(sc.id)"
      >
        <component :is="scenarioIcon(sc.icon)" :size="15" />
        <span>{{ sc.label }}</span>
      </button>
    </nav>

    <div class="lb-content">
      <template v-if="activeView">
        <!-- 排名表格 -->
        <ModelRankingTable :rankings="activeView.rankings" />

        <!-- 海报式榜单（黑板书） -->
        <section v-if="activePosters.length" class="panel lb-posters" data-testid="poster-section">
          <i class="panel-corners" aria-hidden="true" />
          <div class="panel-head">
            <div class="panel-title">
              <span class="panel-icon panel-icon-amber"><Globe :size="17" /></span>
              <div><h2>更多榜单</h2><p>无法直接获取数据的公开榜单，点击跳转查看</p></div>
            </div>
            <span class="panel-count">{{ activePosters.length }} 个</span>
          </div>
          <div class="poster-grid">
            <LeaderboardPoster
              v-for="poster in activePosters"
              :key="poster.id"
              :poster="poster"
            />
          </div>
        </section>
      </template>

      <div v-else-if="loading" class="empty-state">正在加载榜单数据…</div>
      <div v-else class="empty-state">请选择一个场景查看</div>
    </div>
  </section>
</template>