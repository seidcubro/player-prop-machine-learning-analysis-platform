from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests


class OddsApiClient:
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
        