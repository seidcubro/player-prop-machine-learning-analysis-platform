/**
 * Player profile.
 *
 * Answers, in order: who is this, what should I bet on them right now, how have
 * our calls on them actually gone, and what have they been doing lately.
 *
 * Deliberately free of pipeline internals. An earlier version surfaced database
 * ids, the model class, and `POST /jobs/...` instructions, useful while
 * building the pipeline, meaningless to someone deciding on a bet.
 */

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Avatar from "../components/Avatar";
import PropCards from "../components/PropCards";
import WeekProjections from "../components/WeekProjections";
import TrackRecord from "../components/TrackRecord";
import {
  fetchPlayer,
  fetchPlayerGames,
  type Player,
  type PlayerGame,
} from "../api";

/** Height arrives in inches. */
function fmtHeight(inches: number | null): string | null {
  if (!inches) return null;
  return `${Math.floor(inches / 12)}'${Math.round(inches % 12)}"`;
}

function fmtDate(d: string): string {
  const dt = new Date(`${d.slice(0, 10)}T00:00:00`);
  return dt.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** Show the stat columns that matter for the player's position. */
function statColumns(
  position: string | null,
): Array<{ key: keyof PlayerGame; label: string }> {
  const pos = (position ?? "").toUpperCase();
  if (pos === "QB") {
    return [
      { key: "passing_yards", label: "Pass Yds" },
      { key: "passing_tds", label: "Pass TD" },
      { key: "rushing_yards", label: "Rush Yds" },
    ];
  }
  if (pos === "RB" || pos === "FB") {
    return [
      { key: "rush_attempts", label: "Att" },
      { key: "rushing_yards", label: "Rush Yds" },
      { key: "receptions", label: "Rec" },
      { key: "receiving_yards", label: "Rec Yds" },
    ];
  }
  return [
    { key: "receptions", label: "Rec" },
    { key: "receiving_yards", label: "Rec Yds" },
    { key: "touchdowns", label: "TD" },
  ];
}

export default function PlayerDetail() {
  const { id } = useParams();
  const playerId = Number(id);

  const [player, setPlayer] = useState<Player | null>(null);
  const [games, setGames] = useState<PlayerGame[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!Number.isFinite(playerId)) {
      setErr("That player link is not valid.");
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setErr(null);

    fetchPlayer(playerId)
      .then((p) => {
        if (cancelled) return;
        setPlayer(p);
        // The game log is a nice-to-have; a failure must not blank the page.
        return fetchPlayerGames(playerId, 10)
          .then((g) => !cancelled && setGames(g))
          .catch(() => !cancelled && setGames([]));
      })
      .catch((e) => !cancelled && setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [playerId]);

  if (loading) return <div className="ps-empty">Loading player...</div>;
  if (err) return <div className="ps-empty">{err}</div>;
  if (!player) return <div className="ps-empty">Player not found.</div>;

  const name =
    player.name ??
    `${player.first_name ?? ""} ${player.last_name ?? ""}`.trim() ??
    "Unknown";

  const meta: Array<[string, string]> = [];
  if (player.team) meta.push(["Team", player.team]);
  if (player.jersey_number) meta.push(["Number", `#${player.jersey_number}`]);
  const h = fmtHeight(player.height);
  if (h) meta.push(["Height", h]);
  if (player.weight) meta.push(["Weight", `${player.weight} lb`]);
  if (player.college) meta.push(["College", player.college]);
  if (player.years_exp !== null && player.years_exp !== undefined) {
    meta.push([
      "Experience",
      player.years_exp === 0 ? "Rookie" : `${player.years_exp} yrs`,
    ]);
  }

  const cols = statColumns(player.position);

  return (
    <div>
      <Link to="/players" className="ps-back">All Players</Link>

      <div className="ps-profilehead" style={{ marginTop: 12 }}>
        <Avatar name={name} src={player.headshot} size="xl" />
        <div>
          <h1>{name}</h1>
          <div className="ps-chips">
            <span className="pos-chip">{player.position ?? "-"}</span>
            {player.status && player.status !== "ACT" && (
              <span className="pos-chip">{player.status}</span>
            )}
          </div>
          <div className="ps-profilemeta">
            {meta.map(([k, v]) => (
              <span key={k}>
                {k} <b>{v}</b>
              </span>
            ))}
          </div>
        </div>
      </div>

      <PropCards playerName={name} playerId={playerId} />

      <WeekProjections playerId={playerId} />

      <TrackRecord playerId={playerId} />

      <div className="ps-section">
        <h3>Recent Games</h3>
        {games.length === 0 ? (
          <div className="ps-empty">No recent games on record.</div>
        ) : (
          <div className="ps-tablewrap">
            <table className="ps-table">
              <caption className="sr-only">Recent Game Log</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Opp</th>
                  {cols.map((c) => (
                    <th scope="col" key={String(c.key)}>
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {games.map((g, i) => (
                  <tr key={`${g.game_date}-${i}`}>
                    <td data-label="Date">
                      <span className="ps-gamedate">{fmtDate(g.game_date)}</span>
                    </td>
                    <td data-label="Opp">{g.opponent ?? "-"}</td>
                    {cols.map((c) => {
                      const v = g[c.key];
                      return (
                        <td
                          data-label={c.label}
                          className="num"
                          key={String(c.key)}
                        >
                          {v === null || v === undefined
                            ? "-"
                            : Number(v).toFixed(0)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
