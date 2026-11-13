"""Daily rows of the sample market, 2026-07-22 to 2026-10-28 (10-29 comes from intraday.py)."""

import datetime
import math

from .common import price_limits
from .setups import design, find_setups
from .universe import ABSENT, DEBT, FUTURES, KSE30_LEVEL, NO_REG_TRADE, ORDINARY, OTHERS, XD_EVENTS
from .util import clamp_limits, r2, rng

ISO = datetime.date.fromisoformat


# ---------------------------------------------------------------- one stock

def bar_for(sym, d, ldcp, close, usual, g):
    """open, high, low and volume of a normal day, inside the day's price limits."""
    lo_lim, hi_lim = price_limits(ldcp)
    close = min(max(close, lo_lim), hi_lim)
    op = clamp_limits(ldcp, ldcp * (1 + g.gauss(0, 0.005)))
    top, bot = max(op, close), min(op, close)
    hi = clamp_limits(ldcp, top * (1 + abs(g.gauss(0, 0.006))))
    lo = clamp_limits(ldcp, bot * (1 - abs(g.gauss(0, 0.006))))
    vol = int(usual * math.exp(g.gauss(0, 0.3)))
    vol = max(100, int(round(vol, -2 if vol > 10000 else 0)))
    return {"open": op, "high": max(hi, top), "low": min(lo, bot), "close": r2(close), "volume": vol}


def stock_rows(sym, sector, short, usual, role, last, days):
    """{date: row} of one listed stock over `days` (sessions through 10-28)."""
    gone = set(ISO(x) for x in ABSENT.get(sym, ()))
    flat = ISO(NO_REG_TRADE[1]) if NO_REG_TRADE[0] == sym else None
    trade = [d for d in days if d not in gone and d != flat]
    drop = None
    if sym in XD_EVENTS:
        ex, amt, _ = XD_EVENTS[sym]
        if ISO(ex) in trade:
            drop = (trade.index(ISO(ex)), amt)
    closes = dict(zip(trade, design(sym, role, len(trade), last, drop)))
    g = rng("daily", sym)
    prev = r2(closes[trade[0]] * (1 + g.gauss(0, 0.01)))
    out = {}
    for d in days:
        if d in gone:
            continue
        ldcp = prev
        if sym in XD_EVENTS and XD_EVENTS[sym][0] == d.isoformat():
            ldcp = r2(prev - XD_EVENTS[sym][1])
        if d == flat:
            row = {"open": 0.0, "high": 0.0, "low": 0.0, "close": ldcp, "volume": 15000}
        else:
            row = bar_for(sym, d, ldcp, closes[d], usual, g)
        row.update(symbol=sym, sector=sector, short=short, ldcp=ldcp)
        out[d] = row
        prev = row["close"]
    return out


def liquid_last_day(rows, days, usual):
    """Set the 10-28 volume: clearly over 500,000 for a liquid stock, clearly under for a thin one."""
    d = days[-1]
    if d in rows and rows[d]["volume"]:
        g = rng("lastvol", rows[d]["symbol"])
        if usual >= 500000:
            rows[d]["volume"] = max(rows[d]["volume"], int(round(usual * g.uniform(1.1, 1.6), -2)))
        else:
            rows[d]["volume"] = min(rows[d]["volume"], int(round(usual * g.uniform(0.55, 0.85), -2)))


# ---------------------------------------------------------------- the whole market

def build(days):
    """{sym: {date: row}} for every symbol over days (sessions through 10-28)."""
    market = {}
    for r in ORDINARY:
        sym, sector, short, full, last, usual = r[:6]
        rows = stock_rows(sym, sector, short, usual, r[9], last, days)
        liquid_last_day(rows, days, usual)
        market[sym] = rows
    for sym, sector, short, full, last, usual, shares, ff, kind in OTHERS:
        rows = stock_rows(sym, sector, short, usual, "", last, days)
        liquid_last_day(rows, days, usual)
        market[sym] = rows
    market[DEBT[0]] = debt_rows(days)
    kse = index_path(days)
    for spec in FUTURES:
        market[spec[0]] = future_rows(spec, days, market, kse)
    return market


def debt_rows(days):
    """A one-year government bond: the price creeps up to 100 by its maturity (2027-01-21)."""
    sym, sector, short, _ = DEBT
    out, prev = {}, None
    g = rng("debt")
    for d in days:
        left = (datetime.date(2027, 1, 21) - d).days
        px = r2(100.0 / (1 + 0.106 * left / 365.0))
        if d == days[-1] and prev is not None:
            px = r2(prev - 0.02)          # a small dip, so the bond is no 20-day closing high
        ldcp = prev if prev is not None else r2(px - 0.03)
        vol = int(round(g.uniform(5, 30), 0)) * 1000000 + 5000
        out[d] = {"symbol": sym, "sector": sector, "short": short, "open": px, "high": px, "low": px,
                  "close": px, "volume": vol, "ldcp": ldcp}
        prev = px
    return out


def index_path(days):
    """A made-up KSE-30 close for each day, ending at KSE30_LEVEL on the last day."""
    g = rng("kse30")
    r = [g.gauss(0.0008, 0.009) for _ in days]
    s, path = 0.0, []
    for x in r:
        s += x
        path.append(s)
    return dict((d, round(KSE30_LEVEL * math.exp(p - path[-1]), 2)) for d, p in zip(days, path))


def future_rows(spec, days, market, kse):
    """Rows of one future from 2026-08-31: close = underlying x (1 + a premium that shrinks).
    Seeds are tried until the future's own closes match no daily setup."""
    for seed in range(1, 200):
        out = _future_try(spec, days, market, kse, seed)
        cl = [out[d]["close"] for d in sorted(out) if out[d]["open"]]
        if len(cl) < 30 or not find_setups(cl):
            return out
    raise ValueError("no clean future rows for %s" % spec[0])


def _future_try(spec, days, market, kse, seed):
    """One try of future_rows()."""
    sym, sector, under, prem, trades = spec
    start = datetime.date(2026, 8, 31)
    live = [d for d in days if d >= start]
    g = rng("future", sym, seed)
    out, prev = {}, None
    for i, d in enumerate(live):
        p = prem * (1 - 0.8 * i / max(1, len(live) - 1)) / 100.0 + g.gauss(0, 0.0015)
        if under:
            u = market[under].get(d)
            base = u["close"] if u else None
            short = u["short"] if u else market[under][max(market[under])]["short"]
        else:
            base, short = kse[d], "KSE30"
        close = r2(base * (1 + p)) if under else float(int(round(base * (1 + p))))
        ldcp = prev if prev is not None else r2(close * (1 - g.gauss(0, 0.005)))
        if not under and prev is None:
            ldcp = float(int(round(ldcp)))
        on = trades == "all" or (trades == "some" and g.random() < 0.45)
        if on:
            row = bar_for(sym, d, ldcp, close, 900000 if trades == "all" else 120000, g)
        else:
            row = {"open": 0.0, "high": 0.0, "low": 0.0, "close": close, "volume": 0}
        row.update(symbol=sym, sector=sector, short=short, ldcp=ldcp)
        out[d] = row
        prev = row["close"]
    return out
