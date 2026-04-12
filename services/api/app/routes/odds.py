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
def sync_odds_player_props(db: Session = Depends(get_db)):
    client = OddsApiClient()

    rows = db.execute(
        text(
            """
            SELECT provider_event_id
            FROM odds_events
            WHERE sport_key = :sport_key
            ORDER BY commence_time DESC NULLS LAST
            """
        ),
        {"sport_key": client.sport_key},
    ).mappings().all()

    if not rows:
        raise HTTPException(status_code=400, detail="No odds_events found. Run /odds/sync/events first.")

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
    return {"ok": True, "player_prop_rows_inserted": inserts}

@router.post("/odds/sync/historical_events")
def sync_historical_events(date: str, db: Session = Depends(get_db)):
    client = OddsApiClient()
    db.execute(
        text("DELETE FROM odds_events WHERE sport_key = :sport_key"),
        {"sport_key": client.sport_key},
    )
    try:
        resp = client.get_historical_events(date)
    except requests.HTTPError as e:
        detail = None
        try:
            detail = e.response.text
        except Exception:
            detail = str(e)
        raise HTTPException(status_code=502, detail=f"Odds API historical_events failed: {detail}")

    events = resp.get("data", [])
    
    print(f"[HIST EVENTS] fetched {len(events)} events for {date}")

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

@router.post("/odds/sync/historical_player_props")
def sync_historical_player_props(date: str, db: Session = Depends(get_db)):
    client = OddsApiClient()

    rows = db.execute(
        text(
            """
            SELECT provider_event_id
            FROM odds_events
            WHERE sport_key = :sport_key
            """
        ),
        {"sport_key": client.sport_key},
    ).mappings().all()

    if not rows:
        raise HTTPException(
            status_code=400,
            detail="No odds_events found for this sport/date window. Run /odds/sync/historical_events first."
        )

    market_keys = list(ODDS_API_MARKET_MAP.values())
    inserts = 0
    skipped_event_not_found = 0
    empty_bookmakers = 0

    for row in rows:
        event_id = row["provider_event_id"]

        try:
            payload = client.get_historical_event_player_props(
                event_id, market_keys, date
            )
        except requests.HTTPError as e:
            detail = ""
            try:
                detail = e.response.text or ""
            except Exception:
                detail = str(e)

            if "EVENT_NOT_FOUND" in detail:
                skipped_event_not_found += 1
                continue

            raise HTTPException(
                status_code=502,
                detail=f"Odds API historical_event_player_props failed: {detail}"
            )

        data = payload.get("data", {})
        bookmakers = data.get("bookmakers", []) or []

        if not bookmakers:
            empty_bookmakers += 1
            continue

        for book in bookmakers:
            for market in book.get("markets", []):
                for outcome in market.get("outcomes", []):
                    player_name = outcome.get("description") or outcome.get("name")
                    if not player_name:
                        continue

                    db.execute(
                        text(
                            """
                            INSERT INTO odds_player_props
                            (
                              provider_event_id,
                              sport_key,
                              bookmaker_key,
                              bookmaker_title,
                              market_key,
                              player_name,
                              outcome_name,
                              line,
                              price_american,
                              point_json,
                              last_update,
                              updated_at
                            )
                            VALUES
                            (
                              :provider_event_id,
                              :sport_key,
                              :bookmaker_key,
                              :bookmaker_title,
                              :market_key,
                              :player_name,
                              :outcome_name,
                              :line,
                              :price_american,
                              CAST(:point_json AS jsonb),
                              :last_update,
                              NOW()
                            )
                            """
                        ),
                        {
                            "provider_event_id": event_id,
                            "sport_key": client.sport_key,
                            "bookmaker_key": book.get("key"),
                            "bookmaker_title": book.get("title"),
                            "market_key": market.get("key"),
                            "player_name": player_name,
                            "outcome_name": outcome.get("name"),
                            "line": outcome.get("point"),
                            "price_american": outcome.get("price"),
                            "point_json": json.dumps(outcome),
                            "last_update": _parse_ts(book.get("last_update")),
                        },
                    )
                    inserts += 1

    db.commit()
    return {
        "ok": True,
        "rows_inserted": inserts,
        "events_seen": len(rows),
        "events_skipped_event_not_found": skipped_event_not_found,
        "events_with_empty_bookmakers": empty_bookmakers,
    }