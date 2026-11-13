"""Tapes of 2026-10-29: market.jsonl (screener snapshots) and DEMO.jsonl (company page snapshots).

A tape line is what psx_core.snapshot_from_screener / snapshot_from_company write:
"fetched" is Karachi time with "+05:00", "asof" is Karachi wall time. The data is
about 5 minutes late, so "asof" is 5 minutes before "fetched".
"""

import datetime
import os

from . import demo
from .common import price_limits
from .universe import ORDINARY
from .util import at, jsonl, r2, write_text

DAY = datetime.date(2026, 10, 29)
DELAY = datetime.timedelta(minutes=5)
MISSING = (at(DAY, "09:56"), at(DAY, "09:58"))     # the screener lost PIOC in these two fetches


def iso_pkt(t):
    """naive Karachi datetime -> "2026-10-29T09:37:00+05:00"."""
    return t.strftime("%Y-%m-%dT%H:%M:%S") + "+05:00"


def price_at(path, t, fallback):
    """The price a page shows at as-of time t: the last minute at or before t."""
    if t in path:
        return path[t]
    before = [m for m in path if m <= t]
    return path[max(before)] if before else fallback


def market_snaps(paths, market):
    """Screener snapshots every 120 seconds of fetch time, 09:37 to 15:37."""
    snaps = []
    fetched = at(DAY, "09:37")
    while fetched <= at(DAY, "15:37"):
        asof = fetched - DELAY
        prices = {}
        for r in ORDINARY:
            sym = r[0]
            by = market[sym]
            before = [d for d in by if d < DAY]
            last_close = by[max(before)]["close"]
            if sym not in paths:
                prices[sym] = last_close          # no trade today: the page shows its last day
                continue
            if sym == "PIOC" and asof in MISSING:
                continue
            prices[sym] = price_at(paths[sym], asof, last_close)
        snaps.append({"fetched": iso_pkt(fetched), "asof": asof.strftime("%Y-%m-%d %H:%M:%S"),
                      "source": "screener", "prices": prices})
        fetched += datetime.timedelta(seconds=120)
    return snaps


def demo_snaps():
    """DEMO's company page every 60 seconds: As-of 09:33 to 15:35 (minute precision)."""
    mins = demo.minutes()
    lower, upper = price_limits(demo.LDCP)
    snaps = []
    t = at(DAY, "09:33")
    while t <= at(DAY, "15:35"):
        seen = [m for m in mins if m[0] <= t]
        price = seen[-1][4]
        hi = max(m[2] for m in seen)
        lo = min(m[3] for m in seen)
        vol = sum(m[5] for m in seen)
        live = t < at(DAY, "15:30")
        snaps.append({"fetched": iso_pkt(t + DELAY + datetime.timedelta(seconds=7)),
                      "asof": t.strftime("%Y-%m-%d %H:%M"), "source": "company", "symbol": "DEMO",
                      "price": price, "open": demo.OPEN, "high": hi, "low": lo, "volume": vol,
                      "ldcp": demo.LDCP, "lower": lower, "upper": upper,
                      "bid": r2(price - 0.05) if live else 0.0, "ask": r2(price + 0.05) if live else 0.0})
        t += datetime.timedelta(minutes=1)
    return snaps


def write_tapes(out, paths, market):
    """tape/2026-10-29/market.jsonl and tape/2026-10-29/DEMO.jsonl."""
    folder = os.path.join(out, "tape", DAY.isoformat())
    a = os.path.join(folder, "market.jsonl")
    b = os.path.join(folder, "DEMO.jsonl")
    write_text(a, jsonl(market_snaps(paths, market)))
    write_text(b, jsonl(demo_snaps()))
    return [a, b]
