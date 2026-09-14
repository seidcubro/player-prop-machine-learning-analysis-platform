from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests


class OddsApiClient:
    # Balance from the most recent response, for logging and the floor check.
    last_remaining: Optional[int] = None
    last_used: Optional[int] = None

    def __init__(self) -> None:
        self.api_key = os.getenv("ODDS_API_KEY", "").strip()
        self.base_url = os.getenv("ODDS_API_BASE_URL", "https://api.the-odds-api.com/v4").rstrip("/")
        self.sport_key = os.getenv("ODDS_API_SPORT", "americanfootball_nfl").strip()
        self.regions = os.getenv("ODDS_API_REGIONS", "us").strip()
        self.odds_format = os.getenv("ODDS_API_ODDS_FORMAT", "american").strip()
        self.bookmakers = os.getenv("ODDS_API_BOOKMAKERS", "").strip()

        if not self.api_key:
            raise RuntimeError("ODDS_API_KEY is not configured")

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        query = dict(params or {})
        query["apiKey"] = self.api_key

        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = requests.get(url, params=query, timeout=20)
        resp.raise_for_status()

        # Record what the call cost and what is left.
        #
        # Every response carries the running balance in its headers and this
        # threw them away, so the only way to know how many credits a sync had
        # spent was to open the provider's dashboard. That is fine to do by hand
        # and not fine once a scheduler is calling this on a timer: a job that
        # spends money should be able to say how much.
        #
        # `ODDS_MIN_CREDITS` is a floor. Once the balance is below it the next
        # call refuses rather than draining the account, which matters most for
        # the runs nobody is watching.
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        if remaining is not None:
            try:
                OddsApiClient.last_remaining = int(float(remaining))
                OddsApiClient.last_used = int(float(used or 0))
            except (TypeError, ValueError):
                pass
            floor = int(os.getenv("ODDS_MIN_CREDITS", "0") or 0)
            if floor and OddsApiClient.last_remaining is not None                     and OddsApiClient.last_remaining < floor:
                raise RuntimeError(
                    f"odds credits exhausted: {OddsApiClient.last_remaining} "
                    f"left, floor is {floor}. Raise ODDS_MIN_CREDITS or top up."
                )
        return resp.json()

    def get_upcoming_events(self) -> List[Dict[str, Any]]:
        return self._get(
            f"sports/{self.sport_key}/odds",
            {
                "regions": self.regions,
                "oddsFormat": self.odds_format,
                "markets": "h2h",
                **({"bookmakers": self.bookmakers} if self.bookmakers else {}),
            },
        )

    def get_event_player_props(self, event_id: str, markets: List[str]) -> Dict[str, Any]:
        return self._get(
            f"sports/{self.sport_key}/events/{event_id}/odds",
            {
                "regions": self.regions,
                "oddsFormat": self.odds_format,
                "markets": ",".join(markets),
                **({"bookmakers": self.bookmakers} if self.bookmakers else {}),
            },
        )

    def get_historical_events(self, date: str) -> List[Dict[str, Any]]:
        return self._get(
            f"historical/sports/{self.sport_key}/events",
            {
                "date": date,
            },
        )

    def get_historical_event_player_props(self, event_id: str, markets: List[str], date: str) -> Dict[str, Any]:
        return self._get(
            f"historical/sports/{self.sport_key}/events/{event_id}/odds",
            {
                "date": date,
                "regions": self.regions,
                "oddsFormat": self.odds_format,
                "markets": ",".join(markets),
                **({"bookmakers": self.bookmakers} if self.bookmakers else {}),
            },
        )
        
    def get_historical_odds_snapshot(self, date: str, markets: list[str]):
        return self._get(
            f"historical/sports/{self.sport_key}/odds",
            {
                "date": date,
                "regions": self.regions,
                "oddsFormat": self.odds_format,
                "markets": ",".join(markets),
                **({"bookmakers": self.bookmakers} if self.bookmakers else {}),
            },
        )
        