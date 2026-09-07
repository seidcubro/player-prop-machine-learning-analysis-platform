/**
 * Frontend API client for the Player Prop ML platform.
 *
 * This file is the single source of truth for browser -> API calls.
 *
 * Goals:
 * - Centralize URL construction (base URL, query strings, routes).
 * - Centralize error handling (non-2xx -> throw with body text).
 * - Provide stable TypeScript types for UI code.
 *
 * IMPORTANT:
 * - Do NOT build URLs in React components.
 * - Components should call functions from this module only.
 */

/* =========================
   Base URL
   ========================= */

/**
 * Base URL for the FastAPI service.
 *
 * The web app reads `VITE_API_BASE` (Vite env) and falls back to local dev.
 *
 * Examples:
 * - Local:   http://localhost:8000/api/v1
 * - Remote:  https://your-domain.example/api/v1
 */
const DEFAULT_API_BASE = "http://localhost:8000/api/v1";

/**
 * Resolved API base URL (env override + local fallback).
 */
export const API_BASE =
  (import.meta as any).env?.VITE_API_BASE ?? DEFAULT_API_BASE;

/**
 * Get the API base URL or throw a clear error.
 *
 * Why:
 * - Prevents confusing runtime failures when code references an undefined base.
 * - Keeps URL construction consistent and debuggable.
 */
export function getApiBase(): string {
  if (!API_BASE || typeof API_BASE !== "string") {
    throw new Error(
      "API base URL is missing/invalid. Check VITE_API_BASE or apps/web/src/api.ts API_BASE definition."
    );
  }
  return API_BASE;
}

/* =========================
   Types
   ========================= */

export type Player = {
  id: number;
  external_id: string | null;
  first_name: string | null;
  last_name: string | null;
  name: string | null;
  display_name?: string | null;
  position: string | null;
  team: string | null;
  headshot: string | null;
  jersey_number: string | null;
  height: number | null;
  weight: number | null;
  college: string | null;
  years_exp: number | null;
  status: string | null;
  rookie_year: number | null;
};

export type PlayerGame = {
  player_id: number;
  game_date: string;
  opponent: string;
  receptions?: number | null;
  receiving_yards?: number | null;
  rush_attempts?: number | null;
  rushing_yards?: number | null;
  passing_yards?: number | null;
  passing_tds?: number | null;
  touchdowns?: number | null;
};




export type EdgeTier = "small" | "medium" | "strong" | "elite";

export type PropEdge = {
  id: number;
  event_id: string | null;
  commence_time: string | null;
  home_team: string | null;
  away_team: string | null;
  player_name: string;
  market_code: string;
  bookmaker_key: string;
  bookmaker_title: string | null;
  line: number | null;
  price_american: number | null;
  model_name: string;
  model_r2: number | null;
  /** The mean. Correct as a projection, wrong to compare against a line. */
  projection: number;
  /**
   * The median, calibrated the same way the probability is.
   *
   * This is the number to show beside a pick. The side is chosen from the
   * predicted distribution, and on a right-skewed market the mean sits 25-35%
   * above the median, so displaying the mean produced rows reading "model 75.6,
   * line 66.5, pick UNDER" that looked like a straightforward bug.
   */
  projection_median: number | null;
  raw_edge: number;
  win_prob: number | null;
  /** Last season's fantasy production. Display ordering only. */
  star_score: number | null;
  /**
   * Model probability minus the break-even the price demands. This is the
   * number that decides whether a pick is worth making; win_prob on its own
   * says nothing without the price next to it.
   */
  expected_value: number | null;
  recommended_side: string;
  edge_tier: EdgeTier;
  created_at: string;
  game_date: string | null;
  headshot: string | null;
  player_id: number | null;
  /** Matches the structural over-shade pattern. Needs no model. */
  value_flag: boolean;
  /**
   * The one selection verified profitable on a season never used to choose it:
   * top tier, under side, one pick per player-game. +7.1% per unit with a 95%
   * interval of [+1.6%, +13.2%].
   */
  best_bet: boolean;
  /**
   * The other books pricing this same prop.
   *
   * Deduplication happens in the API so the counts match the rows, so the
   * alternates arrive with the row rather than being reassembled in the
   * browser.
   */
  alts: Array<{
    id: number;
    bookmaker_key: string;
    bookmaker_title: string | null;
    line: number | null;
    price_american: number | null;
  }>;
};

export type EdgesResponse = {
  ok: boolean;
  total: number;
  limit: number;
  offset: number;
  edges: PropEdge[];
};

/** How much of the upcoming slate actually has posted prices. */
export type EdgeCoverage = {
  games_upcoming: number;
  games_priced: number;
  markets_priced: number;
};

export type EdgesSummary = {
  ok: boolean;
  by_market: { market_code: string; count: number; avg_abs_edge: number }[];
  by_tier: Partial<Record<EdgeTier, number>>;
  /** Count of best bets across the whole board, not just the loaded page. */
  best_bets: number;
  coverage: EdgeCoverage | null;
  last_updated: string | null;
};

/* =========================
   Helpers
   ========================= */

/**
 * Perform a JSON HTTP request and throw an Error on non-2xx responses.
 *
 * The backend returns JSON on success.
 * On error, we try to include response body text (useful for FastAPI `detail` messages).
 */
async function http<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || `HTTP ${r.status}`);
  }
  return r.json();
}

/**
 * Build a query string from an object, skipping null/undefined values.
 */
function qs(params: Record<string, any>) {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null) return;
    sp.set(k, String(v));
  });
  return sp.toString();
}

/* =========================
   API
   ========================= */

/**
 * Fetch a paginated list of players with optional server-side search.
 *
 * Backend: GET /players?search=...&limit=...&offset=...&include_total=true
 *
 * Returns:
 * - { players: Player[], total?: number }
 */
export async function fetchPlayersPaged(args?: {
  search?: string | null;
  limit?: number;
  offset?: number;
  include_total?: boolean;
  /** Offensive positions only, defensive/K/P players have no prop markets. */
  positions?: string | null;
  /** Hide the ~24k historical players who have not played recently. */
  active_only?: boolean;
}): Promise<{ players: Player[]; total?: number }> {
  const url = `${getApiBase()}/players?${qs({
    search: args?.search ?? undefined,
    limit: args?.limit ?? 50,
    offset: args?.offset ?? 0,
    include_total: args?.include_total ?? false,
    positions: args?.positions ?? undefined,
    active_only: args?.active_only ?? undefined,
  })}`;

  const data = await http<{ ok: boolean; players: Player[]; total?: number }>(url);
  return { players: data.players, total: data.total };
}

/**
 * Back-compat wrapper.
 *
 * Some UI code historically imported `fetchPlayers`. Keep it as an alias
 * so old code doesn't break, but route everything through fetchPlayersPaged().
 */
export async function fetchPlayers(args?: {
  limit?: number;
  offset?: number;
  search?: string | null;
  include_total?: boolean;
}): Promise<{ players: Player[]; total?: number }> {
  return fetchPlayersPaged(args);
}

/**
 * Fetch a single player by internal id.
 *
 * Backend: GET /players/{player_id}
 */
export async function fetchPlayer(playerId: number): Promise<Player> {
  const url = `${getApiBase()}/players/${playerId}`;
  const data = await http<{ ok: boolean; player: Player }>(url);
  return data.player;
}

/**
 * Fetch recent games for a player.
 *
 * Backend: GET /players/{player_id}/games?limit=...
 */
export async function fetchPlayerGames(
  playerId: number,
  limit = 5,
): Promise<PlayerGame[]> {
  const url = `${getApiBase()}/players/${playerId}/games?${qs({ limit })}`;
  const data = await http<{ ok: boolean; games: PlayerGame[] }>(url);
  return data.games;
}




/**
 * Fetch computed betting edges (sportsbook line vs. model projection).
 *
 * Backend: GET /edges?market_code=&min_tier=&side=&search=&sort=&order=&limit=&offset=
 */
export async function fetchEdges(args?: {
  market_code?: string | null;
  min_tier?: EdgeTier | null;
  side?: "over" | "under" | null;
  search?: string | null;
  sort?: string;
  order?: "asc" | "desc";
  best_bets_only?: boolean;
  /** Exact tier, unlike min_tier which is "and up". */
  tier?: EdgeTier | null;
  limit?: number;
  offset?: number;
}): Promise<EdgesResponse> {
  const url = `${getApiBase()}/edges?${qs({
    market_code: args?.market_code ?? undefined,
    min_tier: args?.min_tier ?? undefined,
    side: args?.side ?? undefined,
    search: args?.search ?? undefined,
    sort: args?.sort ?? "featured",
    best_bets_only: args?.best_bets_only ? true : undefined,
    tier: args?.tier ?? undefined,
    order: args?.order ?? "desc",
    limit: args?.limit ?? 50,
    offset: args?.offset ?? 0,
  })}`;
  return http<EdgesResponse>(url);
}

/**
 * Fetch dashboard summary stats (counts per market / tier, last run time).
 *
 * Backend: GET /edges/summary
 */
export async function fetchEdgesSummary(): Promise<EdgesSummary> {
  return http<EdgesSummary>(`${getApiBase()}/edges/summary`);
}

/** One graded historical pick: what we said, and what actually happened. */
export type EdgeHistoryRow = {
  edge_id: number;
  game_date: string | null;
  market_code: string;
  line: number | null;
  projection: number;
  recommended_side: string;
  win_prob: number | null;
  edge_tier: EdgeTier;
  actual: number | null;
  /** true = pick won, false = lost, null = push (excluded from hit rate). */
  hit: boolean | null;
  home_team: string | null;
  away_team: string | null;
  bookmaker_title: string | null;
  price_american: number | null;
};

export type EdgeHistory = {
  ok: boolean;
  player_id: number;
  summary: {
    graded: number;
    wins: number;
    losses: number;
    pushes: number;
    hit_rate: number | null;
    avg_predicted: number | null;
  };
  history: EdgeHistoryRow[];
};

/**
 * Fetch a player's graded pick history.
 *
 * Backend: GET /players/{id}/edge_history
 */
export async function fetchEdgeHistory(
  playerId: number,
  limit = 50,
): Promise<EdgeHistory> {
  return http<EdgeHistory>(
    `${getApiBase()}/players/${playerId}/edge_history?limit=${limit}`,
  );
}

/** A projection for one player in one market for one upcoming game. */
export type Projection = {
  player_id: string;
  player_name: string;
  position: string | null;
  team: string | null;
  opponent: string | null;
  game_date: string | null;
  market_code: string;
  projection: number;
  p10: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p90: number | null;
  model_name: string | null;
  depth_rank: number | null;
  is_starter: boolean | null;
  app_player_id: number | null;
  headshot: string | null;
};

export type ProjectionsResponse = {
  ok: boolean;
  total: number;
  limit: number;
  offset: number;
  projections: Projection[];
};

/**
 * Projections for every eligible player with an upcoming game.
 *
 * Backend: GET /projections. Unlike /edges this does not require a sportsbook
 * line to exist, so it covers the whole slate rather than the handful of
 * players a book happens to have priced.
 */
export async function fetchProjections(args?: {
  market_code?: string | null;
  position?: string | null;
  team?: string | null;
  search?: string | null;
  starters_only?: boolean;
  sort?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}): Promise<ProjectionsResponse> {
  const url = `${getApiBase()}/projections?${qs({
    market_code: args?.market_code ?? undefined,
    position: args?.position ?? undefined,
    team: args?.team ?? undefined,
    search: args?.search ?? undefined,
    starters_only: args?.starters_only ?? undefined,
    sort: args?.sort ?? "projection",
    order: args?.order ?? "desc",
    limit: args?.limit ?? 100,
    offset: args?.offset ?? 0,
  })}`;
  return http<ProjectionsResponse>(url);
}

/** Every upcoming projection for one player, across all markets. */
export async function fetchPlayerProjections(
  playerId: number,
): Promise<{ ok: boolean; projections: Projection[] }> {
  return http(`${getApiBase()}/players/${playerId}/projections`);
}

/* =========================
   Track record
   ========================= */

/**
 * Where a graded pick came from.
 *
 * `live` picks were published by the edge builder before kickoff. `backtest`
 * picks were reconstructed by `backfill_track_record.py` using models refit on
 * strictly earlier seasons. They are different claims, so the UI never merges
 * them without saying which it is showing.
 */
export type RecordSource = "all" | "live" | "backtest";

export type SeasonRecord = {
  season: number;
  source: string;
  picks: number;
  players: number;
  hit_rate: number | null;
  model_predicted: number | null;
  avg_ev: number | null;
  units: number | null;
  roi: number | null;
};

export type MarketRecord = {
  market_code: string;
  picks: number;
  hit_rate: number | null;
  model_predicted: number | null;
  units: number | null;
  roi: number | null;
};

export type TierRecord = {
  edge_tier: string;
  picks: number;
  hit_rate: number | null;
  model_predicted: number | null;
  units: number | null;
  roi: number | null;
};

export type LeaderRow = {
  player_id: string;
  player_name: string;
  app_player_id: number | null;
  position: string | null;
  team: string | null;
  headshot: string | null;
  picks: number;
  hit_rate: number | null;
  units: number | null;
  roi: number | null;
};

export type CalibrationBucket = {
  bucket: number;
  picks: number;
  predicted: number;
  actual: number;
};

export async function fetchSeasonRecord(source: RecordSource = "all") {
  return http<{ ok: boolean; seasons: SeasonRecord[] }>(
    `${API_BASE}/record/seasons?${qs({ source })}`,
  );
}

export async function fetchMarketRecord(
  season?: number | null,
  source: RecordSource = "all",
) {
  return http<{ ok: boolean; markets: MarketRecord[] }>(
    `${API_BASE}/record/by_market?${qs({ season, source })}`,
  );
}

export async function fetchTierRecord(
  season?: number | null,
  source: RecordSource = "all",
) {
  return http<{ ok: boolean; tiers: TierRecord[] }>(
    `${API_BASE}/record/by_tier?${qs({ season, source })}`,
  );
}

export async function fetchLeaders(args?: {
  season?: number | null;
  source?: RecordSource;
  direction?: "best" | "worst";
  min_picks?: number;
  limit?: number;
}) {
  return http<{
    ok: boolean;
    direction: string;
    min_picks: number;
    leaders: LeaderRow[];
  }>(
    `${API_BASE}/record/leaders?${qs({
      season: args?.season ?? null,
      source: args?.source ?? "all",
      direction: args?.direction ?? "best",
      min_picks: args?.min_picks ?? 20,
      limit: args?.limit ?? 10,
    })}`,
  );
}

export async function fetchCalibration(
  season?: number | null,
  source: RecordSource = "all",
) {
  return http<{ ok: boolean; buckets: CalibrationBucket[] }>(
    `${API_BASE}/record/calibration?${qs({ season, source })}`,
  );
}
