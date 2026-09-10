<script setup lang="ts">
import { ExternalLink, Globe, Image } from 'lucide-vue-next'
import type { PosterDef } from '../types'

defineProps<{ poster: PosterDef }>()
</script>

<template>
  <a
    :href="poster.url"
    target="_blank"
    rel="noopener noreferrer"
    class="poster-card"
    data-testid="leaderboard-poster"
  >
    <div class="poster-thumb" :class="{ 'poster-thumb-empty': !poster.image_url }">
      <Image v-if="poster.image_url" :size="22" />
      <span v-else class="poster-thumb-placeholder" aria-hidden="true">AI</span>
    </div>
    <div class="poster-body">
      <div class="poster-head">
        <strong class="poster-name">{{ poster.name }}</strong>
        <span class="poster-freq">{{ poster.update_frequency }}</span>
      </div>
      <p class="poster-desc">{{ poster.description }}</p>
      <span class="poster-link">
        <Globe :size="12" />查看榜单
        <ExternalLink class="poster-external" :size="11" />
      </span>
    </div>
  </a>
</template>