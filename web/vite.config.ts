import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 后端端口可通过 IQ_RADAR_BACKEND_PORT 覆盖，默认 8081。
// 注意：start.sh 启动的端口由 IQ_RADAR_PORT 控制（默认 8080，常因网关占用
// 而改成 8081）。前端 dev server 必须代理到同一个后端端口才能让 /api/* 命中。
const backendPort = process.env.IQ_RADAR_BACKEND_PORT ?? '8081'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': `http://127.0.0.1:${backendPort}`,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json-summary'],
      thresholds: { lines: 80 },
    },
  },
})
