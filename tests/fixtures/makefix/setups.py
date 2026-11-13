"""Daily closes shaped so the pre-market scan of 2026-10-29 tags exactly the planned setups.

rsi() and find_setups() follow the US scanner's rules on closes ("The daily scan" in
trader-scanner/SKILL.md). They are here only to check the made-up closes.
"""

import math

from .util import r2, rng


# ---------------------------------------------------------------- the scan rules

def rsi(closes, n=14):
    """Wilder's RSI on closes."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def find_setups(c):
    """closes (oldest first, 30 or more) -> sorted setup tags."""
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r, r_prev = rsi(c), rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags


def stable(c, want, shortest=30, margin=1.0):
    """True when every scan window, from the newest `shortest` closes to all of them, tags
    exactly `want`, and no RSI there is within `margin` of 30. So a scanner that reads
    fewer sessions (30, 45, 60 ...) still finds the same setups."""
    for n in range(shortest, len(c) + 1):
        part = c[-n:]
        if find_setups(part) != want:
            return False
        for r in (rsi(part), rsi(part[:-1])):
            if r is not None and abs(r - 30) < margin:
                return False
    return True


# ---------------------------------------------------------------- shapes

def _returns(role, n, g):
    """Daily log returns (n - 1 of them) for a role, before the last-day fix."""
    r = [g.gauss(0.0004, 0.012) for _ in range(n - 1)]
    if role == "breakout_20d":
        for i in range(n - 31, n - 1):
            r[i] = g.gauss(0.0012, 0.009)
    elif role == "bull_flag":
        for i in range(n - 16, n - 6):
            r[i] = g.gauss(0.0135, 0.004)
        for i in range(n - 6, n - 1):
            r[i] = g.gauss(-0.002, 0.003)
    elif role == "reclaim_ma20":
        for i in range(n - 25, n - 5):
            r[i] = g.gauss(0.0, 0.007)
        for i in range(n - 5, n - 2):
            r[i] = g.gauss(-0.011, 0.003)
        r[n - 2] = g.uniform(0.028, 0.040)
    elif role == "oversold":
        for i in range(n - 22, n - 1):
            r[i] = g.gauss(-0.012, 0.007)
        r[n - 2] = -abs(r[n - 2]) - 0.002
    elif role == "oversold_bounce":
        for i in range(n - 22, n - 2):
            r[i] = g.gauss(-0.013, 0.006)
        r[n - 2] = g.uniform(0.045, 0.075)
    return r


def _closes(r, last):
    """Closes from log returns, scaled so the last close is `last`."""
    s, path = 0.0, [0.0]
    for x in r:
        s += x
        path.append(s)
    return [r2(last * math.exp(p - path[-1])) for p in path]


def design(sym, role, n, last, drop=None):
    """n closes (oldest first) for sym ending at `last` whose setup tags are exactly [role]
    (or none) in every scan window of 30 closes or more (stable()). drop=(index, rupees)
    lowers every close from that index on (an ex-date). Seeds are tried in order, so the
    answer is the same every run."""
    want = [role] if role else []
    for seed in range(1, 4000):
        g = rng("closes", sym, seed)
        r = _returns(role, n, g)
        c = _closes(r, last)
        if role == "breakout_20d":
            top = max(c[-21:-1])
            c[-1] = r2(top * g.uniform(1.012, 1.022))
            if c[-1] / c[-2] - 1 > 0.05:
                continue
            scale = last / c[-1]
            c = [r2(x * scale) for x in c]
            c[-1] = last
        if drop:
            i, amt = drop
            c = c[:i] + [r2(x - amt) for x in c[i:]]
        if min(c) <= 0.5 or max(c) / min(c) > 1.6:
            continue
        if stable(c, want):
            return c
    raise ValueError("no closes found for %s (%s)" % (sym, role))
