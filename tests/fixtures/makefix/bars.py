"""bars/DEMO.csv (5-minute bars, 30 sessions before 10-29 plus 10-29) and bars/DEMO-1m.csv.

Bars are the bar START in Karachi time, like a broker export. Each day's bars agree with
DEMO's row in that day's closing file: first open = OPEN, highest high = HIGH, lowest
low = LOW, volumes add up to VOLUME, and the last 30 minutes trade around CLOSE.
"""

import datetime
import os

from . import demo
from .common import slots
from .util import r2, rng, split_int, write_text

HEAD = "datetime,open,high,low,close,volume\r\n"


def day_bars(d, row):
    """[(start, open, high, low, close, volume)] of a normal day d made to fit its daily row."""
    starts = slots(d)
    n = len(starts)
    g = rng("bars", d.isoformat())
    op, hi, lo, cl, vol = row["open"], row["high"], row["low"], row["close"], row["volume"]
    k_hi, k_lo = g.randrange(1, n - 8), g.randrange(1, n - 8)
    while k_lo == k_hi:
        k_lo = g.randrange(1, n - 8)
    anchors = sorted([(0, op), (k_hi, hi), (k_lo, lo), (n - 6, cl), (n - 1, cl)])
    closes = []
    for k in range(n):
        for (a, pa), (b, pb) in zip(anchors, anchors[1:]):
            if a <= k <= b:
                w = (k - a) / float(b - a) if b > a else 0.0
                base = pa + (pb - pa) * w
                break
        wiggle = 0.0 if k in (k_hi, k_lo, n - 1) else g.gauss(0, (hi - lo) * 0.04)
        closes.append(min(max(r2(base + wiggle), lo), hi))
    if n - 6 >= 0:
        for k in range(n - 6, n - 1):
            closes[k] = min(max(r2(cl + g.choice((-0.05, 0.0, 0.05))), lo), hi)
    closes[-1] = cl
    w = [(2.2 if k < 6 else (1.6 if k >= n - 6 else 1.0)) * g.uniform(0.7, 1.3) for k in range(n)]
    vols = [x * 100 for x in split_int(vol // 100, w)]
    vols[-1] += vol - sum(vols)
    out, prev = [], op
    for k, t in enumerate(starts):
        c = closes[k]
        top, bot = max(prev, c), min(prev, c)
        h = hi if k == k_hi else min(r2(top + abs(g.gauss(0, (hi - lo) * 0.03))), hi)
        lw = lo if k == k_lo else max(r2(bot - abs(g.gauss(0, (hi - lo) * 0.03))), lo)
        if k != k_hi and h >= hi:
            h = max(top, r2(hi - 0.01))
        if k != k_lo and lw <= lo:
            lw = min(bot, r2(lo + 0.01))
        out.append((t, prev, max(h, top), min(lw, bot), c, vols[k]))
        prev = c
    return out


def demo_day_bars():
    """10-29: DEMO's minutes put together into 5-minute bars (the same bars the tape shows)."""
    mins = demo.minutes()
    out = []
    for t in slots(demo.DAY):
        inside = [m for m in mins if t <= m[0] < t + datetime.timedelta(minutes=5)]
        out.append((t, inside[0][1], max(m[2] for m in inside), min(m[3] for m in inside), inside[-1][4],
                    sum(m[5] for m in inside)))
    return out


def line(b):
    """One CSV line: 2026-10-29 09:30:00,99.20,99.80,98.90,99.65,600000."""
    return "%s,%.2f,%.2f,%.2f,%.2f,%d\r\n" % (b[0].strftime("%Y-%m-%d %H:%M:%S"), b[1], b[2], b[3], b[4], b[5])


def write_bars(out, market, days):
    """bars/DEMO.csv and bars/DEMO-1m.csv. days = the 30 sessions before 10-29, oldest first."""
    rows = market["DEMO"]
    text = [HEAD]
    for d in days:
        text.extend(line(b) for b in day_bars(d, rows[d]))
    text.extend(line(b) for b in demo_day_bars())
    a = os.path.join(out, "bars", "DEMO.csv")
    write_text(a, "".join(text))
    one = [HEAD] + [line(m) for m in demo.minutes() if m[0] < datetime.datetime(2026, 10, 29, 11, 0)]
    b = os.path.join(out, "bars", "DEMO-1m.csv")
    write_text(b, "".join(one))
    return [a, b]
