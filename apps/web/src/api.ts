/**
 * Frontend API client for the Player Prop ML platform.
 *
 * This file is the single source of truth for browser -> API calls.
 * Keeping requests here (instead of scattered across components) makes it easy to:
 * - change base URLs (local vs. deployed),
 * - centralize error handling,
 * - keep response typing consistent as the backend evolves.
 */

/**
 * Base URL for the FastAPI service.
 *
 * The web app reads `VITE_API_BASE` at build/runtime (Vite env) and falls back to
 * the local dev API.
 *
 * Examples:
 * - Local:  http://localhost:8000/api/v1
 * - Deployed: https://your-domain.example/api/v1
 */
export const API_BASE =
  (import.meta as any).env?.VITE_API_BASE ?? "http://localhost:8000/api/v1";

/* =========================
   Types
   ========================= */

export type Player = {
  id: number;
  external_id?: string | null;
  first_name: string;
  last_name: string;
  name?: string;
  position?: string | null;
  team?: string | null;
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

export type Projection = {
  ok: boolean;
  player_id: number;
  market_code: string;
  market_name?: string;
  model_name?: string; // baseline
  model?: string; // some endpoints return "model"
  game_date: string;
  opponent: string;
  lookback?: number;
  line?: number;
  mean: number;
  stddev: number;
  p_over: number;
  created_at?: string;
};

export type MLProjection = {
  ok: boolean;
  player_id: number;
  market_code: string;
  model_name: string;
  lookback: number;
  as_of_game_date: string;
  opponent: string;
  prediction: number;
  features: {
    mean: number;
    stddev: number;
    weighted_mean: number;
    trend: number;
  };
  artifact_path?: string;
};

export type MLProjectionRow = {
  player_id: number;
  market_code: string;
  model_name: string;
  lookback: number;
  as_of_game_date: string;
  prediction: number;
  features: any;
  created_at: string;
};

/* =========================
   Helpers
   ========================= */

/**
 * Perform a JSON HTTP request and throw an Error on non-2xx responses.
 *
 * The backend returns JSON on success. On error, we try to include the response
 * body text (useful when FastAPI returns detail messages).
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
 * Fetch a paginated list of players.
 *
 * Backend: GET /players?limit=...&offset=...
 */
export async function fetchPlayers(limit = 50, offset = 0): Promise<Player[]> {
  const url = `${API_BASE}/players?${qs({ limit, offset })}`;
  const data = await http<{ ok: boolean; players: Player[] }>(url);
  return data.players;
}

/**
 * Fetch a single player by internal id.
 *
 * Backend: GET /players/{player_id}
 */
export async function fetchPlayer(playerId: number): Promise<Player> {
  const url = `${API_BASE}/players/${playerId}`;
  const data = await http<{ ok: boolean; player: Player }>(url);
  return data.player;
}

/**
 * Fetch recent games for a player.
 *
 * Backend: GET /players/{player_id}/games?limit=...
 *
 * Notes:
 * - This endpoint must exist server-side; otherwise this call will throw.
 * - If you haven't implemented it yet, remove calls to this from the UI.
 */
export async function fetchPlayerGames(
  playerId: number,
  limit = 5,
): Promise<PlayerGame[]> {
  const url = `${API_BASE}/players/${playerId}/games?${qs({ limit })}`;
  const data = await http<{ ok: boolean; games: PlayerGame[] }>(url);
  return data.games;
}

/**
 * Fetch the latest stored baseline projection for a player and market.
 *
 * Backend: GET /players/{player_id}/projection_baseline?market_code=...&model_name=...
 */
export async function fetchProjectionBaseline(args: {
  playerId: number;
  market_code: string;
  model_name?: string;
}): Promise<Projection> {
  const model_name = args.model_name ?? "baseline_v1";
  const url = `${API_BASE}/players/${args.playerId}/projection_baseline?${qs({
    market_code: args.market_code,
    model_name,
  })}`;
  return http<Projection>(url);
}

/**
 * Generate and persist an ML projection using the active model for the market.
 *
 * Backend: GET /players/{player_id}/projection_ml?market_code=...&lookback=...
 */
export async function fetchProjectionML(args: {
  playerId: number;
  market_code: string;
  lookback?: number;
}): Promise<MLProjection> {
  const lookback = args.lookback ?? 5;
  const url = `${API_BASE}/players/${args.playerId}/projection_ml?${qs({
    market_code: args.market_code,
    lookback,
  })}`;
  return http<MLProjection>(url);
}

/**
 * Fetch previously generated ML projections for a player (history).
 *
 * Backend: GET /players/{player_id}/ml_projections?market_code=...&model_name=...&lookback=...&limit=...
 */
export async function fetchMLProjectionHistory(args: {
  playerId: number;
  market_code: string;
  model_name?: string;
  lookback?: number;
  limit?: number;
}): Promise<MLProjectionRow[]> {
  const url = `${API_BASE}/players/${args.playerId}/ml_projections?${qs({
    market_code: args.market_code,
    model_name: args.model_name ?? "ridge_v1",
    lookback: args.lookback ?? 5,
    limit: args.limit ?? 20,
  })}`;
  const data = await http<{ ok: boolean; rows: MLProjectionRow[] }>(url);
  return data.rows;
}
