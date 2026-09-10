import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchDashboard, fetchSummary } from './api'

describe('api client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('reports an empty API response without exposing the browser JSON parser error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 502 })))

    await expect(fetchSummary()).rejects.toThrow('Request failed: /api/summary returned an empty response (502)')
    await expect(fetchSummary()).rejects.not.toThrow('Unexpected end of JSON input')
  })

  it('maps the missing initial dashboard snapshot to an empty bundle', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: false,
      data: null,
      error: 'aggregate data not found',
    }), { status: 404, headers: { 'Content-Type': 'application/json' } })))

    await expect(fetchDashboard()).resolves.toMatchObject({
      snapshot_id: 'empty',
      summary: { summaries: [] },
      iq_radar: [],
      quota_radar: [],
      runs: [],
    })
  })
})
