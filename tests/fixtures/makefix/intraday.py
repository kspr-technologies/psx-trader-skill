"""The example day 2026-10-29: one price a minute for each ordinary stock, and the day's rows.

Opening-range roles (universe.ORDINARY): "orb@K" first closes above its range high in bar K
on the market tape (samples every 2 minutes); "missing" loses one slot on the tape;
"absent" did not trade today; "" stays at or under its range high until 10:15.
"""

import datetime

from . import demo
from .common import price_limits
from .daily import bar_for
from .universe import DEBT, FUTURES, KSE30_LEVEL, ORDINARY, OTHERS, THIN_CLOSE, XD_EVENTS
from .util import at, clamp_limits, minutes_between, r2, rng

DAY = datetime.date(2026, 10, 29)
T0, T_END = at(DAY, "09:32"), at(DAY, "15:30")
KSE30_DAY = round(KSE30_LEVEL * 1.0081, 2)     # made-up KSE-30 close of the day


# ---------------------------------------------------------------- one stock's minutes

def bar_minutes(k):
    """The five minutes of bar k (Mon to Thu grid)."""
    start = at(DAY, "09:30") + datetime.timedelta(minutes=5 * k)
    return [start + datetime.timedelta(minutes=j) for j in range(5)]


def path_for(sym, ldcp, role):
    """{minute: price} from 09:32 to 15:29, shaped by the stock's opening-range role."""
    g = rng("path", sym)
    lo_lim, hi_lim = price_limits(ldcp)
    step = 0.0011
    px = ldcp * (1 + g.gauss(0.001, 0.004))
    out = {}
    for t in minutes_between(T0, at(DAY, "09:45")):          # the opening range
        out[t] = clamp_limits(ldcp, px)
        px *= 1 + g.gauss(0, step)
    top = max(p for t, p in out.items() if t.minute % 2 == 0)
    cap = min(r2(top * 0.999), r2(top - 0.01))
    want = int(role.split("@")[1]) if role.startswith("orb@") else None
    px = min(px, cap)
    for k in range(3, 9):                                    # 09:45 to 10:14
        for j, t in enumerate(bar_minutes(k)):
            px *= 1 + g.gauss(0.0002, step)
            if want is not None and k == want and j >= 3:
                over = max(r2(top * (1 + g.uniform(0.0025, 0.006))), r2(top + 0.01))
                px = over if j == 3 else max(px, over)
                out[t] = clamp_limits(ldcp, px)
                continue
            if want is None or k < want:
                if px > cap:
                    px = cap * (1 - abs(g.gauss(0, 0.0008)))
                out[t] = min(clamp_limits(ldcp, px), cap)
            else:
                out[t] = clamp_limits(ldcp, px)
    target = ldcp * (1 + g.gauss(0.003, 0.010))
    for t in minutes_between(at(DAY, "10:15"), T_END):      # the rest of the day
        px += (target - px) * 0.02 + px * g.gauss(0, step)
        out[t] = min(max(clamp_limits(ldcp, px), lo_lim), hi_lim)
    return out


def row_from_path(sym, sector, short, ldcp, path, usual):
    """The day's closing-file row from the minutes: CLOSE is the last 30 minutes' average."""
    g = rng("dayvol", sym)
    last = [p for t, p in path.items() if t >= at(DAY, "15:00")]
    vol = int(round(max(usual * g.uniform(0.8, 1.5), 650000 if usual >= 500000 else 0), -2))
    return {"symbol": sym, "sector": sector, "short": short, "ldcp": ldcp, "open": path[T0],
            "high": max(path.values()), "low": min(path.values()), "close": r2(sum(last) / len(last)),
            "volume": vol}


# ---------------------------------------------------------------- the whole day

def build_day(market):
    """Add the 10-29 rows to market ({sym: {date: row}}). -> {sym: {minute: price}}."""
    paths = {}
    for r in ORDINARY:
        sym, sector, short, usual, role = r[0], r[1], r[2], r[5], r[10]
        by = market[sym]
        prev = by[max(by)]["close"]
        ldcp = r2(prev - XD_EVENTS[sym][1]) if sym in XD_EVENTS and XD_EVENTS[sym][0] == DAY.isoformat() else prev
        if role == "absent":
            continue
        if sym == "DEMO":
            paths[sym] = dict((m[0], m[4]) for m in demo.minutes() if m[0] >= T0)
            by[DAY] = {"symbol": sym, "sector": sector, "short": short, "ldcp": demo.LDCP, "open": demo.OPEN,
                       "high": demo.HIGH, "low": demo.LOW, "close": demo.CLOSE, "volume": demo.VOLUME}
            continue
        paths[sym] = path_for(sym, ldcp, role)
        by[DAY] = row_from_path(sym, sector, short, ldcp, paths[sym], usual)
    for sym, sector, short, full, last, usual, shares, ff, kind in OTHERS:
        by = market[sym]
        ldcp = by[max(by)]["close"]
        g = rng("other-day", sym)
        if sym == THIN_CLOSE[0]:
            op = r2(ldcp * 0.985)
            row = {"open": op, "high": r2(op + 0.10), "low": r2(op - 0.40), "close": ldcp, "volume": 2000}
        else:
            row = bar_for(sym, DAY, ldcp, ldcp * (1 + g.gauss(0, 0.008)), usual, g)
        row.update(symbol=sym, sector=sector, short=short, ldcp=ldcp)
        by[DAY] = row
    by = market[DEBT[0]]
    prev = by[max(by)]["close"]
    px = r2(prev + 0.05)
    by[DAY] = {"symbol": DEBT[0], "sector": DEBT[1], "short": DEBT[2], "open": px, "high": px, "low": px,
               "close": px, "volume": 12005000, "ldcp": prev}
    for sym, sector, under, prem, trades in FUTURES:
        by = market[sym]
        last = by[max(by)]
        if under:
            close = r2(market[under][DAY]["close"] * (1 + prem * 0.002))
        else:
            close = float(int(round(KSE30_DAY * (1 + prem * 0.002))))
        g = rng("future-day", sym)
        if trades == "all" or sym == "DEMO-NOV":
            row = bar_for(sym, DAY, last["close"], close, 900000 if trades == "all" else 60000, g)
        else:
            row = {"open": 0.0, "high": 0.0, "low": 0.0, "close": close, "volume": 0}
        row.update(symbol=sym, sector=sector, short=last["short"], ldcp=last["close"])
        by[DAY] = row
    return paths
