<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Activity, ChartNoAxesCombined, FlaskConical, Globe } from 'lucide-vue-next'
import DashboardPage from './features/dashboard/DashboardPage.vue'
import TestPage from './features/test/TestPage.vue'
import LeaderboardPage from './features/leaderboard/LeaderboardPage.vue'

type ActivePage = 'dashboard' | 'evaluation' | 'leaderboard'

const activePage = ref<ActivePage>('dashboard')

const clockTime = ref('--:--:--')
const clockDate = ref('')
let clockTimer: number | undefined

function tickClock() {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  clockTime.value = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  const week = ['日', '一', '二', '三', '四', '五', '六'][now.getDay()]
  clockDate.value = `${now.getFullYear()}年${pad(now.getMonth() + 1)}月${pad(now.getDate())}日 周${week}`
}

onMounted(() => {
  tickClock()
  clockTimer = window.setInterval(tickClock, 1000)
})

onUnmounted(() => {
  window.clearInterval(clockTimer)
})
</script>

<template>
  <main class="shell">
    <header class="topbar">
      <div class="top-left">
        <div class="clock" aria-label="当前时间">
          <span>{{ clockTime }}</span>
          <small>{{ clockDate }}</small>
        </div>
      </div>
      <div class="top-title">
        <h1>IQRadar 模型监控大盘</h1>
        <p>MODEL INTELLIGENCE · COST · QUOTA OBSERVATORY</p>
      </div>
      <div class="top-right">
        <span class="system-status"><Activity :size="14" />本地监控</span>
        <nav class="page-tabs" aria-label="Primary pages">
          <button
            type="button"
            data-testid="dashboard-page-tab"
            :class="{ active: activePage === 'dashboard' }"
            :aria-current="activePage === 'dashboard' ? 'page' : undefined"
            @click="activePage = 'dashboard'"
          >
            <ChartNoAxesCombined :size="16" />大盘
          </button>
          <button
            type="button"
            data-testid="test-page-tab"
            :class="{ active: activePage === 'evaluation' }"
            :aria-current="activePage === 'evaluation' ? 'page' : undefined"
            @click="activePage = 'evaluation'"
          >
            <FlaskConical :size="16" />测试
          </button>
          <button
            type="button"
            data-testid="leaderboard-page-tab"
            :class="{ active: activePage === 'leaderboard' }"
            :aria-current="activePage === 'leaderboard' ? 'page' : undefined"
            @click="activePage = 'leaderboard'"
          >
            <Globe :size="16" />榜单
          </button>
        </nav>
      </div>
    </header>

    <DashboardPage v-if="activePage === 'dashboard'" />
    <TestPage v-else-if="activePage === 'evaluation'" />
    <LeaderboardPage v-else />
  </main>
</template>
