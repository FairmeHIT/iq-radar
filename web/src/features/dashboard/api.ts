import { delJson, getJson, HttpError } from '../../shared/http'
import type { AggregateSummary, DashboardBundle, RadarSeries, RunRecord, SnapshotDeleteResult, SnapshotInfo } from './types'

export async function fetchDashboard(snapshotId?: string): Promise<DashboardBundle> {
  const query = snapshotId ? `?snapshot=${encodeURIComponent(snapshotId)}` : ''
  try {
    return await getJson<DashboardBundle>(`/api/dashboard${query}`)
  } catch (error) {
    if (error instanceof HttpError && error.status === 404 && error.message === 'aggregate data not found') {
      return {
        snapshot_id: 'empty',
        summary: { schema_version: '1.0', generated_at: '', summaries: [] },
        iq_radar: [],
        quota_radar: [],
        runs: [],
      }
    }
    throw error
  }
}

export function fetchSnapshots(): Promise<SnapshotInfo[]> {
  return getJson<SnapshotInfo[]>('/api/snapshots')
}

export function fetchSummary(): Promise<AggregateSummary> {
  return getJson<AggregateSummary>('/api/summary')
}

export function fetchIqRadar(): Promise<RadarSeries[]> {
  return getJson<RadarSeries[]>('/api/radar/iq')
}

export function fetchQuotaRadar(): Promise<RadarSeries[]> {
  return getJson<RadarSeries[]>('/api/radar/quota')
}

export function fetchRuns(): Promise<RunRecord[]> {
  return getJson<RunRecord[]>('/api/runs?limit=100')
}

export function deleteSnapshot(snapshotId: string): Promise<SnapshotDeleteResult> {
  return delJson<SnapshotDeleteResult>(`/api/snapshots/${encodeURIComponent(snapshotId)}`)
}
