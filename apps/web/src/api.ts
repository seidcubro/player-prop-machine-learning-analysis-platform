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

/**
 * DEV DIAGNOSTIC
 *
 * Confirms the browser is actually executing THIS module.
 * You should see this in the browser console on page load.
 */
console.log("[api.ts] API CLIENT MODULE LOADED", { API_BASE });

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
}): Promise<{ players: Player[]; total?: number }> {
  const url = `${getApiBase()}/players?${qs({
    search: args?.search ?? undefined,
    limit: args?.limit ?? 50,
    offset: args?.offset ?? 0,
    include_total: args?.include_total ?? false,
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
  const url = `${getApiBase()}/players/${args.playerId}/projection_baseline?${qs({
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
  const url = `${getApiBase()}/players/${args.playerId}/projection_ml?${qs({
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
  const url = `${getApiBase()}/players/${args.playerId}/ml_projections?${qs({
    market_code: args.market_code,
    model_name: args.model_name ?? "ridge_v1",
    lookback: args.lookback ?? 5,
    limit: args.limit ?? 20,
  })}`;
  const data = await http<{ ok: boolean; rows: MLProjectionRow[] }>(url);
  return data.rows;
}
