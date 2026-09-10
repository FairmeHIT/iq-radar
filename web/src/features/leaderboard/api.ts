import { getJson, postJson } from '../../shared/http'
import type { ScenarioDef, ScenarioView, SourceStatus } from './types'

export function fetchScenarios(): Promise<ScenarioDef[]> {
  return getJson<ScenarioDef[]>('/api/leaderboards/scenarios')
}

export function fetchScenarioView(scenarioId: string): Promise<ScenarioView> {
  return getJson<ScenarioView>(`/api/leaderboards/scenarios/${encodeURIComponent(scenarioId)}`)
}

export function refreshLeaderboards(): Promise<{ message: string }> {
  return postJson<{ message: string }>('/api/leaderboards/refresh')
}

export function fetchSourceStatuses(): Promise<SourceStatus[]> {
  return getJson<SourceStatus[]>('/api/leaderboards/sources')
}