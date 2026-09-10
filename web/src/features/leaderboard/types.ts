export interface ScenarioDef {
  id: string
  label: string
  description: string
  icon: string
}

export interface ScenarioRanking {
  rank: number
  model_name: string
  model_display: string
  provider: string | null
  composite_score: number
  scores: Record<string, number>
  sources: string[]
}

export interface PosterDef {
  id: string
  name: string
  url: string
  description: string
  scenarios: string[]
  image_url: string | null
  update_frequency: string
  source_type: string
}

export interface ScenarioView {
  scenario_id: string
  scenario_label: string
  scenario_description: string
  rankings: ScenarioRanking[]
  posters: PosterDef[]
}

export interface SourceStatus {
  source_id: string
  display_name: string
  status: 'ok' | 'stale' | 'error' | 'disabled'
  last_fetched: string | null
  next_refresh: string | null
  entries_count: number
  error_message: string | null
}