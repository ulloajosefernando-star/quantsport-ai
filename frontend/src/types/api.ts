export type Category =
  | 'candidato_valor'
  | 'en_observacion'
  | 'ventaja_no_confirmada'
  | 'sin_valor'
  | 'datos_insuficientes'
  | 'cuota_desactualizada'

export interface Assessment {
  market: string
  selection: string
  line: number | null
  label: string
  category: Category
  category_label: string
  bookmaker: string | null
  odds: number | null
  odds_captured_at: string | null
  odds_age_minutes: number | null
  odds_source: string | null
  p_model: number | null
  p_push: number | null
  p_model_ci90: [number, number] | null
  p_market_fair: number | null
  fair_odds: number | null
  min_odds: number | null
  edge: number | null
  ev: number | null
  ev_ci90: [number, number] | null
  prob_ev_positive: number | null
  robustness: number | null
  kelly_full: number | null
  kelly_suggested_fraction: number | null
  n_offers: number
  reasons: string[]
  risks: string[]
  definitions: Record<string, string>
  quality_score: number
  is_synthetic: boolean
  model_status: string
}

export interface RankedItem extends Assessment {
  rank: number
  event_id: number
  sport: string
  competition: string
  competition_key: string
  event: string
  kickoff_utc: string
  kickoff_local: string
  model_version: string | null
  model_fingerprint: string | null
  sample_size: number | null
}

export interface ProviderRow {
  key: string
  name: string
  kind: 'real' | 'dataset' | 'demo' | 'pending'
  sports: string[]
  capabilities: string[]
  configured: boolean
  docs_url: string
  cost_note: string
  coverage_note: string
  license_note: string
  quota_note: string
}

export interface P1x2 { home: number; draw: number; away: number }

export interface EventRow {
  event_id: number
  sport: string
  competition: string
  competition_key: string
  home: string
  away: string
  kickoff_utc: string
  kickoff_local: string
  p1x2: P1x2 | null
  xg: [number | null, number | null]
  best_1x2: Record<string, { odds: number; bookmaker: string; captured_at: string }>
  bookmakers: string[]
  markets: string[]
  lineups_status: string
  last_odds_update: string | null
  summary_category: Category | null
  model_status: string
  is_synthetic: boolean
  data_issues: string[]
}

export interface Overview {
  data_mode: string
  synthetic_data_present: boolean
  events_today: OverviewEvent[]
  upcoming: OverviewEvent[]
  n_upcoming: number
  category_counts: Record<Category, number>
  ranking: RankedItem[]
  models_available: number
  last_odds_update: string | null
  last_event_update: string | null
  alerts: { event: string; issue: string }[]
  providers: ProviderRow[]
  sports: { key: string; name: string; status: string; reason: string }[]
}

export interface OverviewEvent {
  event_id: number
  competition: string
  home: string
  away: string
  kickoff_utc: string
  kickoff_local: string
  summary_category: Category | null
  is_synthetic: boolean
  p1x2: P1x2 | null
}

export interface ReliabilityBin {
  lower: number
  upper: number
  count: number
  mean_predicted: number
  observed_rate: number
  ci_low: number
  ci_high: number
}

export interface MarketEval {
  model: Record<string, number | null>
  baseline_frecuencias: Record<string, number>
  baseline_mercado?: Record<string, number>
  model_on_market_subset?: Record<string, number>
  vs_baseline_frecuencias: { diff: number; low: number; high: number }
  vs_baseline_mercado?: { diff: number; low: number; high: number }
  reliability: ReliabilityBin[]
  reliability_note?: string
}

export interface Betting {
  n_bets: number
  wins?: number
  losses?: number
  staked_units?: number
  profit_units?: number
  roi?: number
  roi_ci90?: [number, number]
  max_drawdown_units?: number
  longest_losing_streak?: number
  avg_odds?: number
  clv_mean?: number
  clv_n?: number
  by_market?: Record<string, { n: number; roi: number }>
  by_odds_range?: Record<string, { n: number; roi: number }>
  note?: string
}

export interface GateCheck { market: string; check: string; passed: boolean; detail: string }

export interface CompetitionPerf {
  competition: string
  competition_key: string
  is_synthetic: boolean
  model_status: string
  error: string | null
  gate: GateCheck[]
  model: Record<string, unknown> | null
  walkforward: {
    status: string
    reason?: string
    n_matches?: number
    n_eval?: number
    refits?: number
    test_period?: [string, string]
    leakage_check?: string
    odds_kind?: string
    '1x2'?: MarketEval
    'totals_2.5'?: MarketEval
    btts?: MarketEval
    betting?: Betting
  }
}
