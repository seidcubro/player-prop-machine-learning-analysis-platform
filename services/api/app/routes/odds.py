from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..odds_market_map import ODDS_API_MARKET_MAP
from ..services.odds_api_client import OddsApiClient

import requests

router = APIRouter()


def _parse_ts(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


@router.post("/odds/sync/events")
def sync_odds_events(db: Session = Depends(get_db)):
    client = OddsApiClient()
    events = client.get_upcoming_events()

    upserts = 0
    for ev in events:
        db.execute(
            text(
                """
                INSERT INTO odds_events
                  (provider_event_id, sport_key, commence_time, home_team, away_team, event_json, updated_at)
                VALUES
                  (:provider_event_id, :sport_key, :commence_time, :home_team, :away_team, CAST(:event_json AS jsonb), NOW())
                ON CONFLICT (provider_event_id, sport_key)
                DO UPDATE SET
                  commence_time = EXCLUDED.commence_time,
                  home_team = EXCLUDED.home_team,
                  away_team = EXCLUDED.away_team,
                  event_json = EXCLUDED.event_json,
                  updated_at = NOW()
                """
            ),
            {
                "provider_event_id": ev.get("id"),
                "sport_key": ev.get("sport_key"),
                "commence_time": _parse_ts(ev.get("commence_time")),
                "home_team": ev.get("home_team"),
                "away_team": ev.get("away_team"),
                "event_json": json.dumps(ev),
            },
        )
        upserts += 1

    db.commit()
    return {"ok": True, "events_upserted": upserts}


@router.post("/odds/sync/player_props")
def sync_odds_player_props(
    days_ahead: int = 8,
    limit: int | None = None,
    db: Session = Depends(get_db),
):
    """Pull player props for events kicking off within the next `days_ahead` days.

    The Odds API bills player props per event per market, so this is deliberately
    scoped to the upcoming slate rather than every row in `odds_events`. A full
    season sync is ~272 events x ~8 markets, which would burn thousands of credits
    in a single call and mostly fetch lines for games that are not priced yet.
    Widen `days_ahead` or set `limit` explicitly when a bigger pull is intended.
    """
    client = OddsApiClient()

    rows = db.execute(
        text(
            """
            SELECT provider_event_id
            FROM odds_events
            WHERE sport_key = :sport_key
              AND commence_time IS NOT NULL
              AND commence_time >= NOW()
              AND commence_time < NOW() + make_interval(days => :days_ahead)
            ORDER BY commence_time ASC
            """
        ),
        {"sport_key": client.sport_key, "days_ahead": days_ahead},
    ).mappings().all()

    if limit is not None:
        rows = rows[:limit]

    if not rows:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No odds_events kicking off in the next {days_ahead} days. "
                "Run /odds/sync/events first, or widen days_ahead."
            ),
        )

    market_keys = list(ODDS_API_MARKET_MAP.values())
    inserts = 0

    for row in rows:
        event_id = row["provider_event_id"]
        payload = client.get_event_player_props(event_id, market_keys)

        bookmakers = payload.get("bookmakers") or []
        for book in bookmakers:
            book_key = book.get("key")
            book_title = book.get("title")
            last_update = _parse_ts(book.get("last_update"))

            for market in book.get("markets") or []:
                market_key = market.get("key")

                for outcome in market.get("outcomes") or []:
                    player_name = outcome.get("description") or outcome.get("name")
                    outcome_name = outcome.get("name")
                    line = outcome.get("point")
                    price = outcome.get("price")

                    if not player_name or not market_key:
                        continue

                    db.execute(
                        text(
                            """
                            INSERT INTO odds_player_props
                              (
                                provider_event_id, sport_key, bookmaker_key, bookmaker_title,
                                market_key, player_name, outcome_name, line, price_american,
                                point_json, last_update, updated_at
                              )
                            VALUES
                              (
                                :provider_event_id, :sport_key, :bookmaker_key, :bookmaker_title,
                                :market_key, :player_name, :outcome_name, :line, :price_american,
                                CAST(:point_json AS jsonb), :last_update, NOW()
                              )
                            ON CONFLICT
                              (provider_event_id, bookmaker_key, market_key,
                               player_name, outcome_name)
                            DO UPDATE SET
                              line = EXCLUDED.line,
                              price_american = EXCLUDED.price_american,
                              point_json = EXCLUDED.point_json,
                              last_update = EXCLUDED.last_update,
                              updated_at = NOW()
                            """
                        ),
                        {
                            "provider_event_id": event_id,
                            "sport_key": client.sport_key,
                            "bookmaker_key": book_key,
                            "bookmaker_title": book_title,
                            "market_key": market_key,
                            "player_name": player_name,
                            "outcome_name": outcome_name,
                            "line": line,
                            "price_american": price,
                            "point_json": json.dumps(outcome),
                            "last_update": last_update,
                        },
                    )
                    inserts += 1

    db.commit()
    return {
        "ok": True,
        "events_synced": len(rows),
        "player_prop_rows_upserted": inserts,
    }

# The two historical sync endpoints that lived here have been removed.
#
# Both began with `DELETE FROM odds_events`, so pulling any historical slate
# wiped the upcoming one. They are replaced by
# services/training/backfill_historical_odds.py, which writes only to the
# append-only odds_snapshots table, prices a pull before spending credits, and
# never touches the tables the live site reads.
