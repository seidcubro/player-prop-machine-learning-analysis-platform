/* apps/web/src/api.ts
   Single source of truth for frontend -> API calls.
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
  model_name?: string;     // baseline
  model?: string;          // some endpoints return "model"
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

async function http<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    const txt = await r.text().catch(() => "");
    throw new Error(txt || `HTTP ${r.status}`);
  }
  return r.json();
}

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

export async function fetchPlayers(limit = 50, offset = 0) {
  const url = `${API_BASE}/players?${qs({ limit, offset })}`;
  const data = await http<{ ok: boolean; players: Player[] }>(url);
  return data.players;
}

export async function fetchPlayer(playerId: number) {
  const url = `${API_BASE}/players/${playerId}`;
  const data = await http<{ ok: boolean; player: Player }>(url);
  return data.player;
}

/* If you already have a real endpoint for games, keep it.
   If not, don’t call it from the UI or it’ll throw.
   (Leaving as-is, but this will 500/404 if you don’t have it.) */
export async function fetchPlayerGames(playerId: number, limit = 5) {
  const url = `${API_BASE}/players/${playerId}/games?${qs({ limit })}`;
  const data = await http<{ ok: boolean; games: PlayerGame[] }>(url);
  return data.games;
}

/* Baseline projection (existing endpoint you tested):
   GET /players/{player_id}/projection_baseline?market_code=pass_yds&model_name=baseline_v1 */
export async function fetchProjectionBaseline(args: {
  playerId: number;
  market_code: string;
  model_name?: string;
}) {
  const model_name = args.model_name ?? "baseline_v1";
  const url = `${API_BASE}/players/${args.playerId}/projection_baseline?${qs({
    market_code: args.market_code,
    model_name,
  })}`;
  return http<Projection>(url);
}

/* ML projection:
   GET /players/{player_id}/projection_ml?market_code=pass_yds&lookback=5 */
export async function fetchProjectionML(args: {
  playerId: number;
  market_code: string;
  lookback?: number;
}) {
  const lookback = args.lookback ?? 5;
  const url = `${API_BASE}/players/${args.playerId}/projection_ml?${qs({
    market_code: args.market_code,
    lookback,
  })}`;
  return http<MLProjection>(url);
}

/* ML projection history:
   GET /players/{player_id}/ml_projections?market_code=pass_yds&model_name=ridge_v1&lookback=5&limit=20 */
export async function fetchMLProjectionHistory(args: {
  playerId: number;
  market_code: string;
  model_name?: string;
  lookback?: number;
  limit?: number;
}) {
  const url = `${API_BASE}/players/${args.playerId}/ml_projections?${qs({
    market_code: args.market_code,
    model_name: args.model_name ?? "ridge_v1",
    lookback: args.lookback ?? 5,
    limit: args.limit ?? 20,
  })}`;
  const data = await http<{ ok: boolean; rows: MLProjectionRow[] }>(url);
  return data.rows;
}
