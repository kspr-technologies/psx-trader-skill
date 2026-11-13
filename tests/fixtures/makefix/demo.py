"""DEMO's fixed example day, Thursday 2026-10-29: 5-minute bars and the minutes inside them.

LDCP 98.40 (limits 88.56 to 108.24). Daily row: open 99.20, high 102.35, low 98.90,
close 101.60, volume 4,236,500. Opening range (09:30 to 09:40 bars) 98.90 to 100.20.
The 10:10 bar (bar 8) closes at 100.65: the first close above the range (10:15 AM).
"""

import datetime

from .util import at, r2, rng, split_int

DAY = datetime.date(2026, 10, 29)
LDCP, OPEN, HIGH, LOW, CLOSE, VOLUME = 98.40, 99.20, 102.35, 98.90, 101.60, 4236500
MATCH = 185000     # shares in the opening match (09:30)

# ---------------------------------------------------------------- the 72 bars (high, low, close)
# Bar k opens at the close of bar k - 1 (bar 0 opens at the opening match, 99.20).

FIXED = [
    (99.80, 98.90, 99.65), (100.20, 99.50, 99.95), (100.10, 99.70, 99.85),     # the opening range
    (100.00, 99.60, 99.75), (99.95, 99.55, 99.90), (100.15, 99.80, 100.10),    # 09:45 to 09:55
    (100.20, 99.85, 99.95), (100.15, 99.90, 100.05),                           # 10:00, 10:05
    (100.70, 100.00, 100.65),                                                  # 10:10: the trigger bar
]
CLOSES = [   # closes of bars 9 to 71 (10:15 to 15:25); highs and lows are added around them
    100.80, 100.95, 100.85, 101.05, 101.10, 101.00, 101.20, 101.25, 101.15, 101.35,   # 10:15 to 11:00
    101.30, 101.45, 101.40, 101.55, 101.50, 101.60, 101.70, 101.65, 101.80, 101.70,   # 11:05 to 11:50
    101.60, 101.75, 101.85, 101.80, 101.90, 101.95, 102.05, 102.10, 102.20, 102.25,   # 11:55 to 12:40
    102.15, 102.30, 102.20, 102.10, 102.15, 102.00, 101.95, 101.90, 101.85, 101.95,   # 12:45 to 13:30
    101.90, 101.80, 101.85, 101.75, 101.70, 101.80, 101.75, 101.70, 101.65, 101.70,   # 13:35 to 14:20
    101.60, 101.65, 101.70, 101.60, 101.55, 101.60, 101.65, 101.55, 101.60, 101.65,   # 14:25 to 15:10
    101.58, 101.62, 101.60,                                                           # 15:15 to 15:25
]
TARGET_BAR = 26        # 11:40: the first bar to reach the 1R target 101.75
HIGH_BAR = 40          # 12:50: the day high 102.35


def bars_hlc():
    """[(high, low, close)] of the 72 bars."""
    out = list(FIXED)
    g = rng("demo", "bars")
    prev = FIXED[-1][2]
    for k, c in enumerate(CLOSES, start=9):
        top, bot = max(prev, c), min(prev, c)
        hi = r2(top + g.choice((0.05, 0.05, 0.10, 0.15)))
        lo = r2(bot - g.choice((0.05, 0.05, 0.10)))
        if k < TARGET_BAR:
            hi = min(hi, 101.70)
        elif k == TARGET_BAR:
            hi = 101.80
        if k == HIGH_BAR:
            hi = HIGH
        hi = min(hi, 102.30) if k != HIGH_BAR else hi
        out.append((max(hi, top), max(lo, 99.60), c))
        prev = c
    return out


def bar_volumes():
    """Volume of each bar: about 600,000 in the first, then 30,000 to 150,000 a bar."""
    g = rng("demo", "vol")
    w = []
    for k in range(1, 72):
        base = 150 - 90 * min(k, 30) / 30.0 if k < 50 else 60 + 2 * (k - 50)
        w.append(base * g.uniform(0.85, 1.15))
    rest = split_int((VOLUME - 600000) // 500, w)
    vols = [600000] + [x * 500 for x in rest]
    vols[-1] += VOLUME - sum(vols)
    return vols


def minutes():
    """DEMO's one-minute bars of the day: [(start, open, high, low, close, volume)].

    09:30 holds the opening match; 09:31 has no trade (trading starts at 09:32). In every
    later bar the first minute trades at the bar's open, and the bar's high and low are
    minute closes, so the tape (one price a minute) sees the same bars as the broker file."""
    hlc, vols = bars_hlc(), bar_volumes()
    g = rng("demo", "minutes")
    out = []
    first = [(at(DAY, "09:30"), OPEN, MATCH), (at(DAY, "09:32"), 98.90, 160000),
             (at(DAY, "09:33"), 99.80, 140000), (at(DAY, "09:34"), 99.65, vols[0] - MATCH - 300000)]
    prev = OPEN
    for t, c, v in first:
        out.append((t, prev, max(prev, c), min(prev, c), c, v))
        prev = c
    for k in range(1, 72):
        hi, lo, c = hlc[k]
        start = at(DAY, "09:30") + datetime.timedelta(minutes=5 * k)
        mid = r2((hi + lo) / 2.0)
        order = [hi, lo, mid] if g.random() < 0.5 else [lo, hi, mid]
        if k == 1:
            order = [100.20, 99.50, 99.80]
        elif k == 2:
            order = [100.10, 99.70, 99.90]
        elif k == 8:
            order = [100.00, 100.40, 100.70]
        elif k == 16:                       # the price at 10:52 is 101.30
            order = [lo, 101.30, hi] if hi > 101.30 else [lo, 101.30, mid]
        xs = [prev] + order + [c]
        parts = split_int(vols[k] // 100, [g.uniform(0.6, 1.4) for _ in range(5)])
        parts = [p * 100 for p in parts]
        parts[-1] += vols[k] - sum(parts)
        for j in range(5):
            o = xs[j - 1] if j else prev
            out.append((start + datetime.timedelta(minutes=j), o, max(o, xs[j]), min(o, xs[j]), xs[j], parts[j]))
        prev = c
    return out
