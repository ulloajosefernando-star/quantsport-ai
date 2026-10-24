import type { Category } from './api'

export interface MarketDefinition {
  key: string; sport: string; label: string; group: string; settlement: string
  selections: string[] | null; selection_pattern: string | null; line: 'none' | 'required' | 'optional'
  line_step: number | null; exhaustive: boolean; periods: string[]; participant: boolean; live_only: boolean
  needs: string[]; settlement_note: string; selection_labels: Record<string, string>
  has_model: boolean; model_reason: string | null
}
export interface Registry {
  sports: { key: string; label: string; markets: MarketDefinition[] }[]
  formats: string[]; margin_methods: string[]; definitions: Record<string, string>; note: string
}

export interface EvaluatedSelection {
  selection: { stable_id: string; selection: string; label: string }
  quote: { stable_id: string; bookmaker: string; odds_decimal: number; implied_probability: number; original_format: string | null; original_value: string | null; source: string; updated_at: string; is_synthetic: boolean }
  display: Record<string, string>
  implied_probability: number; p_no_vig: number | null; p_model: number | null; p_push: number | null
  p_model_ci90: [number, number] | null; fair_odds: number | null; min_odds: number | null; edge: number | null
  ev: number | null; ev_ci90: [number, number] | null; prob_ev_positive: number | null
  kelly_suggested_fraction: number | null; category: Category; category_label: string; model_status: string
  quality_score: number; reasons: string[]; risks: string[]
  uncertainty: 'baja' | 'media' | 'alta' | 'desconocida'
  data_quality: { odds_age_minutes: number; stale: boolean; is_synthetic: boolean; margin_available: boolean; model: string | null; issues: string[] }
}

export interface EvaluatedMarket {
  event: { id: number; stable_id: string; sport: string; competition: string; home: string; away: string; kickoff_utc: string; is_synthetic: boolean }
  market: { stable_id: string; key: string; label: string; period: string; line: number | null; participant: string | null; is_live: boolean; status: string; settlement_note: string }
  bookmaker: string; group: string
  margin: { available: boolean; overround?: number; method?: string; method_disagreement?: number; note: string }
  model_note: string | null
  selections: EvaluatedSelection[]
}

export interface Board { markets: EvaluatedMarket[]; warnings: string[]; events_considered: number; margin_method: string; disclaimer: string }

export interface Conversion {
  decimal: number; american: number; fractional: string; percent: number; implied_probability: number
  display: Record<string, string>; format_detected: string
  evaluation?: { p: number; p_push: number; ev: number; fair_odds: number; min_odds_for_target: number; target_ev: number; edge_vs_implied: number; kelly_full: number; stake: { capped_fraction: number; stake_amount: number; capped_by_limit: boolean; warning: string } }
}
