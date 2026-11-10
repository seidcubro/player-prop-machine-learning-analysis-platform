"""Apply this season's measured level to the predicted range.

fit_season_bias.py measures, per market, how far the published median has sat
from what players actually did this season, walk-forward, and writes
season_bias.json only for the markets where using that number would have picked
more winners. Until this file existed nothing read that artifact, so the
measurement was made every week and thrown away. Pass attempts read 2.7 low and
completions 2.1 low through Week 9 while the fix sat on disk unused.

The whole ladder moves, not only the median. The season running hot says where
the distribution sits and nothing about how wide it is, and P(over) is read off
the same ladder, so shifting one number would publish a median from one
distribution beside a probability from another.

The artifact keeps every bias it has ever shipped, with the date it took effect:

    {"season": 2026, "history": [{"from": "2026-11-10", "bias": {"pass_att": 1.4}}]}

The fitter needs that. Once the board is corrected, the medians it grades are
already shifted, so their residual shrinks toward zero, and a fitter that did
not know why would conclude the bias had gone and remove the correction that
made it go. applied_on() lets it subtract what was applied and measure the model
underneath.

No artifact, no correction. A market not listed is untouched.
"""

import json
from pathlib import Path

NAME = "season_bias.json"

_CACHE: dict | None = None


def read(artifact_dir) -> dict:
    """The artifact as written, or an empty one."""
    path = Path(artifact_dir) / NAME
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def history(doc: dict) -> list:
    """Every shipped bias, oldest first. Reads the first format too, which held
    one bias and no date, and treats it as never having been applied, because
    nothing consumed it."""
    h = (doc or {}).get("history")
    if not h:
        return []
    return sorted(h, key=lambda e: str(e.get("from", "")))


def current(doc: dict) -> dict:
    h = history(doc)
    return dict(h[-1].get("bias") or {}) if h else {}


def load(artifact_dir) -> dict:
    """The bias in force today, by market. Printed once so a build log says
    which markets were moved and by how much."""
    global _CACHE
    if _CACHE is None:
        _CACHE = {m: float(b) for m, b in current(read(artifact_dir)).items()
                  if b}
        if _CACHE:
            moved = ", ".join(f"{m} {b:+.2f}" for m, b in sorted(_CACHE.items()))
            print(f"  season bias: {moved}")
        else:
            print("  no season bias found; the range sits where the models put it")
    return _CACHE


def shift(bias: dict, market_code: str) -> float:
    return float((bias or {}).get(market_code, 0.0) or 0.0)


def applied_on(doc: dict, market_code: str, day) -> float:
    """What was being added to this market's median for a game on this day."""
    out = 0.0
    d = str(day)[:10]
    for e in history(doc):
        if str(e.get("from", ""))[:10] <= d:
            out = float((e.get("bias") or {}).get(market_code, 0.0) or 0.0)
    return out
