#!/usr/bin/env python3
"""Make the psx-invest test market: made up, in the real PSX formats. Standard library only, Python 3.8.

    python3 make.py OUT          # writes OUT/mkt_summary/YYYY-MM-DD.Z

It gives the same files every time: 500 closing files of made-up shares for the PSX
sessions from Thu 2024-11-14 to Fri 2026-10-30 by the calendar (every weekday before
2026 counts), less one missing file (Mon 2026-09-14). The tests call it once into a temp
folder, so the 500 files do not ship. The Z symbols are made up. They were checked once
against a real PSX symbol list (not shipped): none of them is a PSX symbol.
README.md says what each share shows. The helpers come from ../makefix (the generator of
../psx-market). This file defines none of the canonical helper names of tests/psx_core.py.
"""

import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.dirname(HERE)
if FIXTURES not in sys.path:
    sys.path.insert(0, FIXTURES)

from makefix.common import price_limits, round_half_up_tick, trading_day  # noqa: E402
from makefix.dailyio import write_closing  # noqa: E402

ISO = datetime.date.fromisoformat
LAST = datetime.date(2026, 10, 30)       # the newest file: the last session of October 2026
FILES = 500                              # closing files written
MISSING = ISO("2026-09-14")              # no file for this session (ZGAP's story)
AS_OF = "2026-10-01"                     # the fixture portfolio's "as_of"


def steady(rate):
    """The same return every month."""
    return lambda y, m: rate


def flat(y, m):
    """Up and down by 0.5% in turn: no vote either way."""
    return 0.005 if (y * 12 + m) % 2 else -0.005


def zbrk(y, m):
    """Up 2% a month to June 2026, then down: one vote down at Sep 30, both at Oct 30."""
    return {(2026, 7): -0.03, (2026, 8): -0.06, (2026, 9): -0.06, (2026, 10): -0.12}.get((y, m), 0.02)


def zold(y, m):
    """Up 1.5% a month to April 2026, then down: both votes down from Jul 31 on."""
    return {(2026, 5): -0.06, (2026, 6): -0.07, (2026, 7): -0.08, (2026, 8): -0.04,
            (2026, 9): -0.02, (2026, 10): 0.0}.get((y, m), 0.015)


# symbol: (short name, sector code, first price, monthly return, usual volume, listed from)
SHARES = {
    "ZUP": ("Zup Technologies", "0828", 180.00, steady(0.015), 410000, None),
    "ZCAP": ("Zcap Cement", "0804", 55.00, steady(0.025), 820000, None),
    "ZBRK": ("Zbrk Bank", "0807", 60.00, zbrk, 650000, None),
    "ZOLD": ("Zold Fertilizer", "0809", 40.00, zold, 530000, None),
    "ZSPL": ("Zspl Motors", "0801", 110.00, flat, 210000, None),
    "ZBON": ("Zbon Pharma", "0823", 70.00, steady(0.015), 330000, None),
    "ZGAP": ("Zgap Oil and Gas", "0820", 120.00, steady(0.012), 470000, None),
    "ZNEW": ("Znew Power", "0824", 20.00, steady(0.01), 900000, "2026-05-04"),
    "ZRES": ("Zres Chemical", "0805", 150.00, steady(0.015), 260000, None),
    "ZSTL": ("Zstl Spinning", "0830", 30.00, flat, 120000, None),
    "ZTHN": ("Zthn Parts", "0802", 64.00, flat, 150000, None),
    "ZRIT": ("Zrit Engineering", "0808", 40.00, steady(0.01), 380000, None),
}
# ex-dates: (symbol, date): (flag, cash payout or factor, sessions the flag shows)
EVENTS = {
    ("ZUP", "2026-08-10"): ("XD", 14.50, 3),       # a cash payout of Rs 14.50
    ("ZCAP", "2025-09-16"): ("XD", 3.00, 3),
    ("ZCAP", "2026-09-16"): ("XD", 3.50, 3),
    ("ZBON", "2026-10-12"): ("XB", 0.8, 5),        # a 25% bonus: one new share for four
    ("ZRIT", "2026-10-15"): ("XR", 0.9, 5),        # a right issue: the LDCP falls 10%
}
SPLITS = {("ZSPL", "2026-07-02"): 0.5}            # a 2-for-1 split with no flag
NO_TRADE = {                                      # sessions with no trade (OPEN, HIGH and LOW are 0)
    "ZSPL": ("2026-06-29", "2026-06-30", "2026-07-01"),
    "ZTHN": ("2026-08-20", "2026-08-21"),
}
SET_CLOSE = {("ZTHN", "2026-08-21"): 1.0095}     # PSX sets the close of a day with no trade: +0.95%
STALE_FROM = {"ZSTL": "2026-10-05"}               # no trade from this session on (20 sessions)
JUMP = {("ZGAP", "2026-09-14"): 1.06}             # +6% on the day whose file is missing
RIGHT = ("ZRITR", "Zrit Engineering(R)", "0808", "2026-10-23", 4.00)   # the right letter of ZRIT
FUTURE = ("ZUP-NOV", "40", "Zup Technologies", "2026-10-01")          # a stock future: not a share


def tick(x):
    """A price on the Rs 0.01 tick, half up (no float noise)."""
    return round_half_up_tick(x)


def calendar_sessions():
    """The FILES + 1 PSX sessions up to LAST by the calendar, oldest first (one has no file)."""
    out, d = [], LAST
    while len(out) < FILES + 1:
        if trading_day(d)[0]:
            out.append(d)
        d -= datetime.timedelta(days=1)
    return out[::-1]


def month_sizes(days):
    """{(year, month): sessions in that month} over days."""
    out = {}
    for d in days:
        out[(d.year, d.month)] = out.get((d.year, d.month), 0) + 1
    return out


def flag_days(sym, days):
    """{date: flag} of the sessions a share's short name shows an ex flag."""
    out = {}
    for (s, day), (flag, _, n) in EVENTS.items():
        if s != sym:
            continue
        start = days.index(ISO(day))
        for d in days[start:start + n]:
            out[d] = flag
    return out


def bar(ldcp, close, volume):
    """A traded day: open 30% of the way from the LDCP to the close, the high and the low a little out."""
    op = tick(ldcp + 0.3 * (close - ldcp))
    hi = tick(max(op, close) * 1.004 + 0.01)
    lo = tick(min(op, close) * 0.996 - 0.01)
    return {"open": op, "high": hi, "low": lo, "close": close, "volume": volume, "ldcp": ldcp}


def share_rows(sym, days):
    """{date: row} of one share over days (the missing day too: its row is never written)."""
    short, sector, price, ret, volume, listed = SHARES[sym]
    sizes, flags = month_sizes(days), flag_days(sym, days)
    stale = ISO(STALE_FROM[sym]) if sym in STALE_FROM else None
    quiet = set(ISO(x) for x in NO_TRADE.get(sym, ()))
    rows, px, prev = {}, price, tick(price)
    for i, d in enumerate(days):
        if listed and d < ISO(listed):
            continue
        key = (sym, d.isoformat())
        ldcp = prev
        if key in EVENTS:
            flag, amount, _ = EVENTS[key]
            if flag == "XD":
                ldcp, px = tick(prev - amount), px - amount
            else:
                ldcp, px = tick(prev * amount), px * amount
        if key in SPLITS:
            ldcp, px = tick(prev * SPLITS[key]), px * SPLITS[key]
        if d in quiet or (stale and d >= stale):
            close = tick(ldcp * SET_CLOSE.get(key, 1.0))
            px = px * SET_CLOSE.get(key, 1.0)
            row = {"open": 0.0, "high": 0.0, "low": 0.0, "close": close, "volume": 0, "ldcp": ldcp}
        else:
            if key in JUMP:
                px = px * JUMP[key]
            elif d == MISSING:
                px = px                      # no move that day: the next LDCP is the close we saw
            else:
                px = px * (1 + ret(d.year, d.month)) ** (1.0 / sizes[(d.year, d.month)])
            row = bar(ldcp, tick(px), volume + (i * 7919) % 50000 // 100 * 100)
        row.update(short=short + flags.get(d, ""), sector=sector)
        rows[d] = row
        prev = row["close"]
    return rows


def right_rows(days):
    """The right letter ZRITR: it trades from its first day to the newest file."""
    sym, short, sector, first, price = RIGHT
    rows, prev = {}, price
    for i, d in enumerate(x for x in days if x >= ISO(first)):
        close = tick(prev * 1.02)
        rows[d] = dict(bar(prev, close, 150000 + i * 10000), short=short, sector=sector)
        prev = close
    return sym, rows


def future_rows(days, zup):
    """A stock future of ZUP: its LDCP moves with no flag, as futures rows do. The tool skips it."""
    sym, sector, short, first = FUTURE
    rows = {}
    for d in (x for x in days if x >= ISO(first)):
        ref = zup[d]["close"]
        rows[d] = dict(bar(tick(ref * 1.01), tick(ref * 1.012), 5000), short=short, sector=sector)
    return sym, rows


def check(market):
    """Stop with a plain message when a made-up row breaks a PSX rule."""
    for sym, by_day in market.items():
        for d, r in by_day.items():
            lo, hi = price_limits(r["ldcp"])
            for k in ("open", "high", "low", "close", "ldcp"):
                if r[k] and tick(r[k]) != r[k]:
                    raise SystemExit("%s %s: %s %r is not on the tick" % (sym, d, k, r[k]))
            if r["open"] and not (lo <= r["low"] <= min(r["open"], r["close"])
                                  and max(r["open"], r["close"]) <= r["high"] <= hi):
                raise SystemExit("%s %s: a price is outside the day's limits %.2f to %.2f" % (sym, d, lo, hi))


def build():
    """{symbol: {date: row}} of the whole made-up market, and the dates that get a file."""
    days = calendar_sessions()
    market = dict((sym, share_rows(sym, days)) for sym in SHARES)
    for sym, rows in (right_rows(days), future_rows(days, market["ZUP"])):
        market[sym] = rows
    check(market)
    return market, [d for d in days if d != MISSING]


def write_market(out):
    """Write out/mkt_summary/YYYY-MM-DD.Z, one file a session. -> the paths written."""
    market, days = build()
    return write_closing(out, market, days)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("usage: python3 make.py OUT  (the folder that gets mkt_summary/)\n")
        return 2
    paths = write_market(args[0])
    print("wrote %d closing files in %s" % (len(paths), os.path.join(args[0], "mkt_summary")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
