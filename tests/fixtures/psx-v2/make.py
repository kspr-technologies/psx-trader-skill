#!/usr/bin/env python3
"""Make the psx-v2 test files: made up, in the real PSX formats. Standard library only, Python 3.8.

    cd tests/fixtures/psx-v2 && python3 make.py            # rewrite the files here
    python3 make.py OUT                                     # or write them into the folder OUT

It gives the same files every time. DEMO is a made-up company, not a PSX symbol.
- paths/<name>/mkt_summary/YYYY-MM-DD.Z: closing files with DEMO rows only, for the 20 PSX
  sessions after the swing buy day Thu 2026-10-29 (Fri 2026-10-30 to Fri 2026-11-27; Mon
  2026-11-09 is Iqbal Day, so there is no file). Add one path to a copy of ../psx-market.
- contract/*.json: the shared file shapes of the v2 tools, made up.
The helpers come from ../makefix (the generator of ../psx-market). This file defines none
of the canonical helper names of tests/psx_core.py.
"""

import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.dirname(HERE)
if FIXTURES not in sys.path:
    sys.path.insert(0, FIXTURES)

from makefix.common import price_limits, round_half_up_tick  # noqa: E402
from makefix.dailyio import write_closing  # noqa: E402
from makefix.util import write_text  # noqa: E402

SYMBOL, SHORT, SECTOR = "DEMO", "Demo Industries", "0818"   # as in ../psx-market
LAST_CLOSE = 101.60                                          # DEMO's close on Thu 2026-10-29 there
DAYS = ("2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-10",
        "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19",
        "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27")
# the official close of each session, path by path (the plan: entry 99.38, stop 96.25, target 105.64)
CLOSES = {
    # rises; the high of Wed 11-04 is 105.90, the first time it reaches the target
    "up": (102.40, 103.20, 104.10, 105.30, 106.00, 106.50, 107.10, 107.64, 108.20, 108.50, 109.00, 109.40,
           109.90, 110.30, 110.60, 111.00, 111.40, 111.70, 112.10, 112.50),
    # Fri 10-30 closes at 97.10; Mon 11-02 opens at 95.40, under the stop (a gap), then a slow rise
    "gap": (97.10, 95.60, 96.00, 96.40, 96.90, 97.30, 97.60, 97.90, 98.20, 98.40, 98.70, 98.90, 99.20, 99.40,
            99.70, 99.90, 100.20, 100.40, 100.70, 100.90),
    # up to 104.00 on 11-12; every close from 11-11 to 11-24 at 101.20 or more; Wed 11-25 closes at 100.50
    "trend": (101.10, 100.70, 100.90, 101.00, 100.80, 101.30, 101.00, 101.20, 104.00, 103.70, 103.40, 103.00,
              102.70, 102.40, 102.10, 101.80, 101.50, 100.50, 100.80, 101.10),
    # small steps up: no stop, no target, no trend break, so the time stop on Fri 11-27 ends it
    "flat": (101.70, 101.80, 101.95, 102.10, 102.20, 102.35, 102.50, 102.60, 102.75, 102.90, 103.00, 103.15,
             103.30, 103.40, 103.55, 103.70, 103.80, 103.95, 104.10, 104.20),
}
# days whose open, high and low are set by hand (open, high, low)
FIXED = {
    ("up", "2026-11-04"): (104.30, 105.90, 103.90),
    ("gap", "2026-10-30"): (101.20, 101.50, 96.90),
    ("gap", "2026-11-02"): (95.40, 96.10, 94.80),
}
BONUS_FROM, BONUS_FLAG_UNTIL, BONUS = "2026-11-12", "2026-11-18", 1.2   # a made-up 20% bonus (XB)


def tick(x):
    """A price on the Rs 0.01 tick, half up (no float noise)."""
    return round_half_up_tick(x)


def volume(name, i):
    """A made-up day volume: the same numbers every time."""
    return 2000000 + (i * 7919 + len(name) * 104729) % 1500000 // 100 * 100


def path_rows(name):
    """{date text: row} of one path. Each LDCP is the close before. Days not set by hand open
    30% of the way from the close before to the close; the high is 0.35 over and the low
    0.40 under the open and the close."""
    rows, prev = {}, LAST_CLOSE
    for i, (day, close) in enumerate(zip(DAYS, CLOSES[name])):
        if (name, day) in FIXED:
            op, hi, lo = FIXED[(name, day)]
        else:
            op = tick(prev + 0.3 * (close - prev))
            hi, lo = tick(max(op, close) + 0.35), tick(min(op, close) - 0.40)
        rows[day] = {"short": SHORT, "sector": SECTOR, "open": op, "high": hi, "low": lo, "close": close,
                     "volume": volume(name, i), "ldcp": prev}
        prev = close
    return rows


def bonus_rows():
    """The "up" path to Wed 11-11; from Thu 11-12 every price is the "up" price / 1.2 on the
    tick. The LDCP of 11-12 is the close of 11-11 / 1.2 (PSX lowers it on the ex-date), and
    the short name shows XB from 11-12 to 11-18."""
    rows, prev = {}, None
    for day, r in sorted(path_rows("up").items()):
        r = dict(r)
        if day >= BONUS_FROM:
            for k in ("open", "high", "low", "close"):
                r[k] = tick(r[k] / BONUS)
            r["ldcp"] = tick(r["ldcp"] / BONUS) if day == BONUS_FROM else prev
            if day <= BONUS_FLAG_UNTIL:
                r["short"] = SHORT + "XB"
        rows[day] = r
        prev = r["close"]
    return rows


def check_rows(name, rows):
    """Stop with a plain message when a made-up row breaks a PSX rule."""
    for day, r in rows.items():
        lo, hi = price_limits(r["ldcp"])
        for k in ("open", "high", "low", "close", "ldcp"):
            if tick(r[k]) != r[k]:
                raise SystemExit("%s %s: %s %r is not on the tick" % (name, day, k, r[k]))
        if not (lo <= r["low"] <= min(r["open"], r["close"]) and max(r["open"], r["close"]) <= r["high"] <= hi):
            raise SystemExit("%s %s: a price is outside the day's limits %.2f to %.2f" % (name, day, lo, hi))


def write_paths(out):
    """paths/<name>/mkt_summary/YYYY-MM-DD.Z for up, gap, trend, flat and bonus."""
    files = []
    for name in ("up", "gap", "trend", "flat", "bonus"):
        rows = bonus_rows() if name == "bonus" else path_rows(name)
        check_rows(name, rows)
        market = {SYMBOL: dict((datetime.date.fromisoformat(d), r) for d, r in rows.items())}
        files += write_closing(os.path.join(out, "paths", name), market, sorted(market[SYMBOL]))
    return files


# ---------------------------------------------------------------- the contract files (made up)

FP_FIELDS = ("symbol", "side", "qty", "type", "limit_price", "time_in_force", "board", "stop_loss", "take_profit",
             "trigger_price")   # the order fields psx-risk and psx-broker hash (their ORDER_FIELDS)
NOTE = "A stop is a plan, not a guarantee: a gap or a fast market can fill past it."   # psx-exit's note
PROPOSALS = ("Proposals only. Nothing in this pack places an order: psx-risk checks each one, psx-broker shows "
             "a ticket after your yes, and you place it yourself in your broker's app.")


def order_fp(order):
    """The fingerprint psx-risk and psx-broker give an order: the sha256 of the FP_FIELDS it has,
    as sorted JSON, first 16 hex characters."""
    core = dict((k, order[k]) for k in FP_FIELDS if k in order and order[k] is not None)
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


PLAN_SWING = {
    "created_by": "psx-exit", "style": "swing", "symbol": "DEMO", "side": "buy", "date": "2026-10-29",
    "setup": "breakout_20d", "level": 96.56, "last_close": 98.4, "last_close_date": "2026-10-28",
    "entry": 99.38, "entry_rule": "last close + 1%", "entry_time": None, "entry_bar": None,
    "interval": "1d", "opening_range": None,
    "stop": 96.25, "stop_rule": "atr", "stop_label": "2 x ATR(14) under the entry",
    "stop_detail": "daily ATR 1.5650 over the 14 sessions to Wed 2026-10-28", "atr": 1.565, "atr_bars": 14,
    "atr_from": "closing files", "target": 105.64, "target_r": 2.0, "target_label": "2R (two times the risk)",
    "risk_per_share": 3.13, "reward_risk": 2.0, "flat_by": None,
    "max_hold_sessions": 20, "exit_by": "2026-11-27", "exit_by_time": "16:20",
    "trend_sessions": 10, "trend_level": 92.36, "session": "normal",
    "limits": {"lower": 88.56, "upper": 108.24, "ldcp": 98.4, "from": "computed", "base": "last close"},
    "costs_per_share_est": 0.3504, "gap_low": 89.44, "gap_loss_per_share": 10.2782, "gap_r": 2.95,
    "created_at": "2026-10-28T12:00:00Z", "note": NOTE}

SWING_ORDER = {
    "symbol": "DEMO", "side": "buy", "qty": 1436, "type": "limit", "limit_price": 99.38, "time_in_force": "day",
    "board": "REG", "stop_loss": 96.25, "take_profit": 105.64,
    "why": ("20-day closing-high breakout on DEMO: 1436 shares, bound by your Rs 5,000 max loss per trade "
            "(1436 x 3.13 = Rs 4,494.68 at the stop + Rs 503.13 costs = Rs 4,997.81, costs included)"),
    "plan": {"style": "swing", "entry": 99.38, "stop": 96.25, "target": 105.64, "risk_per_share": 3.13,
             "max_loss": 4997.81, "costs": 503.13, "setup": "breakout_20d", "entry_time": None, "flat_by": None,
             "exit_by": "2026-11-27", "exit_by_time": "16:20", "trend_sessions": 10, "gap_loss": 14759.46}}
ORDERS_SWING = {"created_by": "psx-budget", "created_at": "2026-10-29T04:20:00Z", "status": "proposed",
                "paper": True, "note": PROPOSALS, "source": "psx/plan.json (DEMO breakout_20d 2026-10-29)",
                "orders": [SWING_ORDER]}

PORTFOLIO_SWING = {
    "cash": 357034.73,
    "positions": [{"ticker": "DEMO", "shares": 1436,
                   "lots": [{"id": "DEMO-1", "date": "2026-10-29", "shares": 1436, "cost": 142709.68, "costs": 255.59}],
                   "plan": {"style": "swing", "date": "2026-10-29", "stop": 96.25, "stop_at_buy": 96.25,
                            "target": 105.64, "exit_by": "2026-11-27", "exit_by_time": "16:20",
                            "trend_sessions": 10, "setup": "breakout_20d", "max_loss": 4997.81,
                            "scaled": [], "from": "psx/orders.json"}}]}

PROTECT_ORDER = {"symbol": "DEMO", "side": "sell", "qty": 1436, "type": "stop_limit", "trigger_price": 96.25,
                 "limit_price": 95.28, "time_in_force": "day", "board": "REG",
                 "why": "protect DEMO: the plan stop 96.25 as a stop-limit sell in your broker app"}
ORDERS_PROTECT = {"created_by": "psx-broker", "created_at": "2026-10-29T04:36:00Z", "status": "proposed",
                  "paper": False, "note": "A stop-limit sell of shares you hold. psx-risk checks it like any order.",
                  "orders": [PROTECT_ORDER]}

PORTFOLIO_REAL = {
    "real_holdings": True, "as_of": "2026-10-28",
    "note": "Made up for the tests: a long-term folder that holds real shares. DEMO is not a PSX symbol.",
    "cash": 100000,
    "positions": [{"ticker": "DEMO", "shares": 500, "avg_cost": 92.5,
                   "lots": [{"id": "DEMO-1", "date": None, "shares": 500, "cost": 46250.0,
                             "note": "made up: from a broker statement"}]}]}

ORDERS_INVEST = {
    "created_by": "psx-invest", "created_at": "2026-10-29T05:20:00Z", "status": "proposed", "paper": False,
    "note": PROPOSALS,
    "orders": [
        {"symbol": "LUCK", "side": "sell", "qty": 12, "type": "limit", "limit_price": 468.0, "time_in_force": "day",
         "board": "REG", "stop_loss": None, "take_profit": None,
         "why": "psx-invest TRIM: size 26.1%, over the 25% trim line, back to the 20% cap (rule 5)",
         "plan": {"style": "invest", "signal": "TRIM", "entry": 468.0, "fair_share_pct": 12.5,
                  "weight_after_pct": 20.0, "rules": ["size over the trim line (26.1%)"]}},
        {"symbol": "DEMO", "side": "buy", "qty": 100, "type": "limit", "limit_price": 98.4, "time_in_force": "day",
         "board": "REG", "stop_loss": None, "take_profit": None,
         "why": "psx-invest ADD: no vote down, 12-month up (+19.5%), size 6.3% to 8.2% (fair share 12.5%)",
         "plan": {"style": "invest", "signal": "ADD", "entry": 98.4, "fair_share_pct": 12.5,
                  "weight_after_pct": 8.15, "rules": ["trend no vote (+1.1%)", "12-month up (+19.5%)"]}}]}

# psx/state/broker.json records: a live swing buy (1,436 DEMO) and a live protect sell of 143 DEMO
BUY_FP = order_fp(SWING_ORDER)
BUY_PLAIN = ("BUY 1,436 DEMO, limit 99.38, good for today, REG board / plan stop 96.25 / plan target 105.64 / "
             "max loss about Rs 4,997.81 if the stop fills at 96.25, costs included")
BUY_APPROVED = {"at": "2026-10-29T09:20:00+05:00", "date": "2026-10-29", "index": 1, "fingerprint": BUY_FP,
                "symbol": "DEMO", "side": "buy", "qty": 1436, "kind": "entry", "max_loss": 4997.81, "stop": 96.25,
                "plain": BUY_PLAIN, "status": "approved", "reply": "yes", "paper": False, "broker": "manual",
                "entry_ref": 99.38}
BUY_PLACED = {"at": "2026-10-29T09:22:00+05:00", "date": "2026-10-29", "index": 1, "fingerprint": BUY_FP,
              "symbol": "DEMO", "side": "buy", "plain": BUY_PLAIN, "status": "placed", "qty": 1436, "kind": "entry",
              "max_loss": 4997.81, "stop": 96.25, "entry_ref": 99.38, "broker_order_id": "FS-1001"}
BUY_EXPIRED = {"at": "2026-10-30T16:15:00+05:00", "date": "2026-10-30", "index": 1, "fingerprint": BUY_FP,
               "symbol": "DEMO", "side": "buy", "plain": BUY_PLAIN, "status": "expired", "of": "2026-10-29"}
PROTECT_143 = dict(PROTECT_ORDER, qty=143)
SELL_FP = order_fp(PROTECT_143)
SELL_PLAIN = "SELL 143 DEMO, stop-limit, trigger 96.25, limit 95.28, good for today, REG board"
SELL_APPROVED = {"at": "2026-10-30T09:35:00+05:00", "date": "2026-10-30", "index": 1, "fingerprint": SELL_FP,
                 "symbol": "DEMO", "side": "sell", "qty": 143, "kind": "exit", "max_loss": None, "stop": None,
                 "plain": SELL_PLAIN, "status": "approved", "reply": "yes 96.25", "paper": False, "broker": "manual"}
SELL_PLACED = {"at": "2026-10-30T09:36:00+05:00", "date": "2026-10-30", "index": 1, "fingerprint": SELL_FP,
               "symbol": "DEMO", "side": "sell", "plain": SELL_PLAIN, "status": "placed", "qty": 143, "kind": "exit",
               "broker_order_id": "FS-2001"}
SELL_CANCELLED = {"at": "2026-10-30T11:05:00+05:00", "date": "2026-10-30", "index": 1, "fingerprint": SELL_FP,
                  "symbol": "DEMO", "side": "sell", "plain": SELL_PLAIN, "status": "cancelled", "of": "2026-10-30"}

# what alerts.py clock --json prints (psx-autorun reads it), on four days
CLOCK_KEYS = {
    "about": "Made up for the tests: the keys of alerts.py clock --json, and what it says on four days.",
    "keys": ["now_karachi", "date", "weekday", "calendar_known", "calendar_note", "state", "open", "session",
             "segment", "segments", "opens", "closes", "reason", "detail", "next_open"],
    "expected_mark": "(expected",
    "days": [
        {"now": "2026-10-29 10:00", "exit": 0, "state": "open", "open": True, "session": "normal", "segment": 0,
         "segments": [["09:30", "15:30"]], "opens": "09:30", "closes": "15:30", "calendar_known": True,
         "calendar_note": "", "reason": "regular session", "next_open": "2026-10-30 09:15"},
        {"now": "2026-11-06 00:01", "exit": 1, "state": "closed", "open": False, "session": "friday",
         "segment": None, "segments": [["09:15", "12:00"], ["14:30", "16:30"]], "opens": "09:15",
         "closes": "16:30", "calendar_note": "", "reason": "before the open, pre-open from 9:00 AM PKT",
         "next_open": "2026-11-06 09:15"},
        {"now": "2027-03-05 10:00", "exit": 1, "state": "closed", "open": False, "session": None, "segment": None,
         "segments": [], "opens": None, "closes": None, "calendar_known": True,
         "reason": "PSX holiday: Juma-tul-Wida (expected, check PSX notices)",
         "calendar_note": "2027-03-05 is an expected PSX holiday (Juma-tul-Wida): moon dates can move, so check "
                          "PSX notices.", "next_open": "2027-03-08 09:15"},
        {"now": "2027-03-04 17:00", "exit": 1, "state": "closed", "session": "ramadan",
         "calendar_note_has": "A moon holiday is expected next to 2027-03-04"}]}

CONTRACT = {
    "plan-swing.json": PLAN_SWING,
    "orders-swing.json": ORDERS_SWING,
    "portfolio-swing.json": PORTFOLIO_SWING,
    "orders-protect.json": ORDERS_PROTECT,
    "portfolio-real.json": PORTFOLIO_REAL,
    "orders-invest.json": ORDERS_INVEST,
    "broker-placed-unfilled.json": {"orders": [BUY_APPROVED, BUY_PLACED]},
    "broker-expired.json": {"orders": [BUY_APPROVED, BUY_PLACED, BUY_EXPIRED]},
    "broker-approved-old.json": {"orders": [BUY_APPROVED]},
    "broker-protect-open.json": {"orders": [SELL_APPROVED, SELL_PLACED]},
    "broker-protect-cancelled.json": {"orders": [SELL_APPROVED, SELL_PLACED, SELL_CANCELLED]},
    "clock-keys.json": CLOCK_KEYS,
}


def write_contract(out):
    """contract/<name>.json: indented JSON, keys in the order above."""
    files = []
    for name, doc in CONTRACT.items():
        path = os.path.join(out, "contract", name)
        write_text(path, json.dumps(doc, indent=2) + "\n")
        files.append(path)
    return files


def main(argv=None):
    """Write every file into the folder named first (default: this folder)."""
    args = list(sys.argv[1:] if argv is None else argv)
    out = os.path.abspath(args[0]) if args else HERE
    files = write_paths(out) + write_contract(out)
    print("wrote %d files to %s" % (len(files), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
