#!/usr/bin/env python3
"""Position sizing for psx-budget. Standard library only, no network.

Default mode: sizes ONE day trade from psx/plan.json (written by psx-exit) to
your budget and your max loss per trade, costs included, then writes ONE order
to psx/orders.json: a limit order at the plan's entry, with the stop and the
target in it. It never places anything: psx-risk checks the order, psx-broker
shows you a ticket after your yes, and you place it in your broker's app.

    python3 size.py                                  # psx/plan.json + psx/config.json
    python3 size.py --write                          # write psx/orders.json
    python3 size.py --limit 100.60 --write           # a limit order at 100.60
    python3 size.py --market --json                  # a market order (see below)
    python3 size.py --now "2026-10-29 10:15" --json  # pin the clock (Karachi time)

How many shares. Whole shares only (the REG board lot is 1 share).

    shares = the smallest of
      max loss       the most shares whose loss at the stop, costs included,
                     fits max_loss_per_trade_pkr: qty x (entry - stop) plus the
                     costs of the buy and the sell. Costs are no straight line
                     (a minimum per share, rounding), so it searches.
      position cap   account x max_position_pct, minus what you already hold
                     of this stock, / entry
      max order      max_order_pkr / entry
      cash           (cash minus the cash buffer) / entry, the buy's costs
                     included; the buffer is account x cash_buffer_pct

The account is the one psx-risk checks against: with psx/portfolio.json, its cash
plus your holdings (each at a fresh price, else at its cost). With no portfolio,
it is the budget, and the cash is the budget too.

Costs come from the "costs" block of psx/config.json: commission max(0.15% of
the value, Rs 0.03 a share) on each side, 15% sales tax on it, and other
charges of 0.0066% of the value on each side.

The entry it sizes at: the limit price (the plan's entry, or --limit PRICE).
With --market, the plan's entry or a fresh price if that is higher, because a
price that ran up since the plan means a bigger loss at the same stop. A fresh
price is at most 15 minutes old by its own time. It looks in psx/prices.json
(psx-alerts writes it), then in the data folder (psx/data or --data): today's
tape, then a saved company page. The first fresh one wins, as in psx-risk. Free
PSX prices are about 5 minutes late, so a limit order is the default.

No order (a plain refusal, exit code 1) when zero shares fit, the order is
under min_order_pkr, reward:risk is under min_reward_risk, or the target (or
the entry) is outside today's price limits (the plan's "limits").

Swing style ("style": "swing" in psx/config.json). A swing plan from psx-exit
is held for days, so it changes these things (day style stays as it was):

  styles        a day plan in a swing folder, or a swing plan in a day folder, is
                refused with one plain line (they do not mix)
  costs         commission on both sides (trade_costs), whatever
                "day_trade_one_side" says: the sell comes on a later day
  gap limit     a fifth limit: the most shares whose loss, if the next session
                opens at its lower price limit, costs included (gap_loss), fits
                swing.max_gap_loss_pkr (null: 3 x max_loss_per_trade_pkr)
  ledger        the cash is psx/portfolio.json's cash less today's buys with no
                fill yet (psx/state/broker.json: approved or placed), the number
                psx-risk uses. No psx/portfolio.json but a buy placed or filled in
                psx/state/broker.json: refused, so open positions never count as
                cash. Neither: sized from the budget, with a note
  preview       a plan for the next session is sized as a preview, the evening
                before; --write is refused then (psx-risk checks the order's date)
  limits        a target over the buy day's upper price limit is a note, not a
                refusal: it can fill on a later day
  the order     no flat-by time; its plan says "style": "swing", the exit-by day
                and time, the trend rule and the gap loss

    python3 size.py --now "2026-10-28 17:00"         # a preview for the next session
    python3 size.py --now "2026-10-29 09:20" --write # the pre-open of the buy day

Weights mode (kept from v1, no longer the headline): split a budget across a
list of PSX shares by weight, in whole shares.

    python3 size.py --weights OGDC=30,HBL=25,LUCK=20,FFC=15,SYS=10
    python3 size.py --targets my-weights.json --budget 500000 --write

  1. cash buffer   budget x cash_buffer_pct stays in cash, never invested
  2. weights       scaled to add up to 100 over the shares you keep (blocked
                   tickers are dropped first; --top N keeps the N biggest)
  3. cap           no share gets more than max_position_pct of the budget;
                   anything over the cap is shared out to the others by
                   weight, again and again until nobody is over
  4. whole shares  each slice buys whole shares at its price, the buy's costs
                   included; prices come from psx/prices.json (or --prices),
                   else the saved screener page, else saved company pages
  5. min order     a slice under min_order_pkr is dropped and its weight
                   shared out, so you never place a tiny order

Money that cannot be invested is reported as "left idle", never hidden.
"""

import argparse
import datetime
import html
import json
import math
import os
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP


# ---------------------------------------------------------------- copied from tests/psx_core.py
# (verbatim; never edit a copy)

PSX = "https://dps.psx.com.pk"
KARACHI_HOURS = 5   # Karachi time is UTC+5 all year: no daylight saving
PSX_HOLIDAYS = {    # weekday closures; 2026 as they happened, 2027 expected
    "2026-02-05": "Kashmir Day",
    "2026-03-20": "Eid ul Fitr",
    "2026-03-23": "Pakistan Day",
    "2026-05-01": "Labour Day",
    "2026-05-26": "Eid ul Azha",
    "2026-05-27": "Eid ul Azha",
    "2026-05-28": "Eid ul Azha and Youm-e-Takbeer",
    "2026-06-25": "Ashura",
    "2026-06-26": "Ashura",
    "2026-08-14": "Independence Day",
    "2026-08-26": "Eid Milad-un-Nabi",
    "2026-11-09": "Iqbal Day",
    "2026-12-25": "Quaid-e-Azam Day",
    "2027-02-05": "Kashmir Day",
    "2027-03-05": "Juma-tul-Wida",
    "2027-03-10": "Eid ul Fitr",
    "2027-03-11": "Eid ul Fitr",
    "2027-03-12": "Eid ul Fitr",
    "2027-03-23": "Pakistan Day",
    "2027-05-17": "Eid ul Azha",
    "2027-05-18": "Eid ul Azha",
    "2027-05-19": "Eid ul Azha",
    "2027-05-28": "Youm-e-Takbeer",
    "2027-06-15": "Ashura",
    "2027-06-16": "Ashura",
    "2027-08-16": "Eid Milad-un-Nabi",
    "2027-11-09": "Iqbal Day",
}
PSX_PROVISIONAL = {  # moon dates: expected only, check PSX notices
    "2027-03-05", "2027-03-10", "2027-03-11", "2027-03-12", "2027-05-17", "2027-05-18",
    "2027-05-19", "2027-06-15", "2027-06-16", "2027-08-16",
}
RAMADAN = {2026: ("2026-02-19", "2026-03-19"), 2027: ("2027-02-09", "2027-03-09")}  # first and last day
SESSIONS = {        # REG board segments: 5-minute grid start to end (trading starts 2 minutes later)
    "normal": (("09:30", "15:30"),),
    "friday": (("09:15", "12:00"), ("14:30", "16:30")),
    "ramadan": (("09:15", "13:30"),),
    "ramadan-friday": (("09:15", "12:30"),),
}
NORMAL_OPEN = datetime.time(9, 30)
NORMAL_CLOSE = datetime.time(15, 30)
SHIFT_SPLIT = datetime.time(12, 30)  # config times at or before this keep their minutes after the open
COSTS = {"commission_pct": 0.15, "commission_min_per_share": 0.03, "sales_tax_pct": 15,
         "other_charges_pct": 0.0066, "day_trade_one_side": False}
COST_WARN = 0.25    # warn when round-trip costs are over this share of the price risk
SWING = {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20, "trend_exit_sessions": 10,
         "entry_buffer_pct": 1.0, "max_open_positions": 3, "max_open_risk_pkr": None,
         "max_gap_loss_pkr": None, "stop_limit_gap_pct": 1.0}


# ---------------------------------------------------------------- this tool

# The contract's psx-budget/config.example.json, key for key. Missing keys fall back
# to these. One exception: "budget" stays None, so no money is ever sized that you
# have not named (pass --budget or set it in psx/config.json).
DEFAULTS = {
    "budget": None,
    "cash_buffer_pct": 10,
    "max_position_pct": 75,
    "max_order_pkr": 400000,
    "min_order_pkr": 5000,
    "max_loss_per_trade_pkr": 5000,
    "max_daily_loss_pkr": 15000,
    "max_trades_per_day": 3,
    "max_orders_per_day": 10,
    "min_reward_risk": 1.0,
    "require_stop": True,
    "no_entries_before": "09:45",
    "no_entries_after": "15:00",
    "flat_by": "15:20",
    "style": "day",
    "swing": dict(SWING),
    "costs": dict(COSTS),
    "broker": "manual",
    "paper": True,
    "blocked": [],
    "allow_options": False,
    "allow_margin": False,
    "allow_short": False,
    "psx_site": False,
    "data_delay_minutes": 5,
    "holidays": {},
    "open_days": [],
}
EPS = 1e-9
FRESH_MINUTES = 15
SETUP_WORDS = {"orb": "opening-range breakout", "flag": "flag", "bounce": "oversold bounce",
               # the daily scan's labels (psx-scanner), for swing plans
               "breakout_20d": "20-day closing-high breakout", "reclaim_ma20": "back over the 20-day average",
               "bull_flag": "bull flag", "oversold": "oversold (RSI under 30)", "oversold_bounce": "oversold bounce"}
PLAN_PATH = "psx/plan.json"
CONFIG_PATH = "psx/config.json"
PRICES_PATH = "psx/prices.json"
PORTFOLIO_PATH = "psx/portfolio.json"
STATE_PATH = "psx/state/broker.json"
WAITING = ("approved", "placed")   # a yes the user may have placed in the app: its cash is not free yet
ORDERS_PATH = "psx/orders.json"
DATA_PATH = "psx/data"
SCREENER_URL = "https://dps.psx.com.pk/screener"
LIMITS_FROM = {"company page": "from the company page", "computed": "computed from the last close"}


class SizeError(Exception):
    pass


# ---------------------------------------------------------------- time (Karachi is UTC+5 all year: no time zone files)
# copied from tests/psx_core.py, verbatim: never edit a copy

def to_pkt(utc):
    """UTC -> naive Karachi time (UTC+5). An aware datetime is turned to UTC first."""
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc + datetime.timedelta(hours=KARACHI_HOURS)


def pkt_to_utc(local):
    """Naive Karachi time -> naive UTC. An aware datetime keeps its own offset."""
    if local.tzinfo is not None:
        return (local - local.utcoffset()).replace(tzinfo=None)
    return local - datetime.timedelta(hours=KARACHI_HOURS)


def utc_now():
    """The real clock as naive UTC, without microseconds."""
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def parse_now(text):
    """--now text -> naive UTC. Empty -> the real clock. A time without an offset is
    Karachi time ("2026-10-29 10:15", a " PKT" at the end is fine); "...Z" is UTC and
    an offset like "+05:00" is honoured."""
    if not text:
        return utc_now()
    t = str(text).strip()
    if t.upper().endswith("PKT"):
        t = t[:-3].strip()
    if t[-1:] in ("Z", "z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ValueError('--now wants a time like "2026-10-29 10:15" (Karachi time) or ...Z (UTC)')
    return pkt_to_utc(dt)


def parse_hhmm(text, flag="--at"):
    """"10:15" -> datetime.time(10, 15). ValueError with a plain message otherwise."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("%s wants a Karachi time like 10:15" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


def parse_utc(text):
    """ISO time -> naive UTC. A time without an offset is taken as UTC.
    Empty, or not a time -> None."""
    if not text:
        return None
    t = str(text).strip()
    if t[-1:] in ("Z", "z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def utc_iso(dt):
    """naive UTC -> "2026-10-29T05:15:00Z" (how JSON files store machine times). None stays None."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def day_label(d):
    """date -> "Thu 2026-10-29" (English day names, whatever the machine's language)."""
    if isinstance(d, datetime.datetime):
        d = d.date()
    return "%s %s" % (("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()], d.isoformat())


# ---------------------------------------------------------------- PSX calendar (only for config_time)
# copied from tests/psx_core.py, verbatim: never edit a copy

def ramadan_range(year, cfg=None):
    """(first day, last day) of Ramadan in a year as dates, or None when not known.
    The config's "ramadan": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} replaces the
    year it falls in. A bad "ramadan" block raises ValueError."""
    r = cfg.get("ramadan") if isinstance(cfg, dict) else None
    if r:
        try:
            a = datetime.date.fromisoformat(str(r["start"]).strip())
            b = datetime.date.fromisoformat(str(r["end"]).strip())
        except (KeyError, TypeError, ValueError):
            raise ValueError('"ramadan" in psx/config.json wants {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}')
        if b < a:
            raise ValueError('"ramadan" in psx/config.json ends before it starts')
        if year in (a.year, b.year):
            return a, b
    v = RAMADAN.get(year)
    if not v:
        return None
    return datetime.date.fromisoformat(v[0]), datetime.date.fromisoformat(v[1])


def in_ramadan(d, cfg=None):
    """True when date d is in Ramadan (PSX keeps shorter hours then)."""
    r = ramadan_range(d.year, cfg)
    return bool(r) and r[0] <= d <= r[1]


def trading_day(d, cfg=None):
    """(is_trading_day, reason) for a Karachi date. Closed: weekends, PSX_HOLIDAYS and
    the config's "holidays" ({"YYYY-MM-DD": "name"}). The config's "open_days"
    (["YYYY-MM-DD"]) reopen a listed holiday. Bank-only holidays (Jan 1, Jul 1, 1st
    Ramadan) are trading days. A config date that is not YYYY-MM-DD raises ValueError
    (it is never skipped without a word)."""
    c = cfg if isinstance(cfg, dict) else {}
    extra = c.get("holidays") or {}
    if isinstance(extra, str):
        extra = [extra]
    if isinstance(extra, (list, tuple, set)):
        extra = dict((str(x), "") for x in extra)
    if not isinstance(extra, dict):
        raise ValueError('"holidays" in psx/config.json wants {"YYYY-MM-DD": "name"}')
    opened = c.get("open_days") or []
    if isinstance(opened, str):
        opened = [opened]
    if not isinstance(opened, (list, tuple, set)):
        raise ValueError('"open_days" in psx/config.json wants ["YYYY-MM-DD"]')
    try:
        extra = dict((datetime.date.fromisoformat(str(k).strip()).isoformat(), v) for k, v in extra.items())
    except ValueError:
        raise ValueError('"holidays" in psx/config.json wants {"YYYY-MM-DD": "name"}')
    try:
        opened = set(datetime.date.fromisoformat(str(x).strip()).isoformat() for x in opened)
    except ValueError:
        raise ValueError('"open_days" in psx/config.json wants ["YYYY-MM-DD"]')
    if d.weekday() >= 5:
        return False, "weekend"
    key = d.isoformat()
    if key in extra:
        return False, "PSX holiday: %s" % (str(extra[key] or "").strip() or "set in psx/config.json")
    if key in PSX_HOLIDAYS and key not in opened:
        name = PSX_HOLIDAYS[key]
        if key in PSX_PROVISIONAL:
            name += " (expected, check PSX notices)"
        return False, "PSX holiday: " + name
    return True, ""


def session_kind(d, cfg=None):
    """"normal", "friday", "ramadan" or "ramadan-friday" for date d; None when PSX is closed."""
    if not trading_day(d, cfg)[0]:
        return None
    fri = d.weekday() == 4
    if in_ramadan(d, cfg):
        return "ramadan-friday" if fri else "ramadan"
    return "friday" if fri else "normal"


def session(d, cfg=None):
    """Date d's REG board segments as ((start, end), ...) datetime.time pairs, or None
    when closed. The 5-minute grid starts at each start; trading starts 2 minutes later."""
    kind = session_kind(d, cfg)
    if kind is None:
        return None
    return tuple(tuple(datetime.datetime.strptime(x, "%H:%M").time() for x in seg) for seg in SESSIONS[kind])


def shift_time(t, d, cfg=None):
    """A config time (written for a normal Monday to Thursday) moved to date d's hours.
    At or before 12:30 it keeps its minutes after the open; later it keeps its minutes
    before the close. 09:45 -> 09:30 on a Friday; 15:00 -> 16:00 on a Friday and 13:00
    on a Ramadan Monday; 15:20 -> 16:20 on a Friday. A normal day or a closed day keeps
    t; None stays None. The result never crosses midnight: 00:00 stays 00:00 and a late
    time stops at 23:59."""
    if t is None:
        return None
    kind = session_kind(d, cfg)
    if kind is None or kind == "normal":
        return t
    segs = session(d, cfg)
    base = datetime.date(2000, 1, 3)
    at = datetime.datetime.combine(base, t)
    if t <= SHIFT_SPLIT:
        out = datetime.datetime.combine(base, segs[0][0]) + (at - datetime.datetime.combine(base, NORMAL_OPEN))
    else:
        out = datetime.datetime.combine(base, segs[-1][1]) - (datetime.datetime.combine(base, NORMAL_CLOSE) - at)
    if out.date() < base:
        return datetime.time(0, 0)
    if out.date() > base:
        return datetime.time(23, 59)
    return out.time()


def config_time(t, d, cfg=None):
    """A config time (no_entries_before, no_entries_after, flat_by) for date d: shift_time(),
    then kept inside the day's session. Before the open it becomes the open, after the
    close the close, and inside the Friday break the break's start (12:00). So "flat_by"
    15:55 is 15:30 on a normal day. A closed day keeps the shifted time; None stays None.
    Every skill that reads those config keys uses this."""
    if t is None:
        return None
    s = shift_time(t, d, cfg)
    segs = session(d, cfg)
    if not segs:
        return s
    if s < segs[0][0]:
        return segs[0][0]
    if s > segs[-1][1]:
        return segs[-1][1]
    for (a, b), (c, _) in zip(segs, segs[1:]):
        if b < s < c:
            return b
    return s


# ---------------------------------------------------------------- prices, money and costs
# copied from tests/psx_core.py, verbatim: never edit a copy

def round_down_tick(x):
    """A price down to the Rs 0.01 tick (a long's stop: away from the entry). Float
    noise under a billionth is cleaned first, so 99.54999999999999 counts as 99.55."""
    try:
        d = Decimal(str(x)).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("not a price: %r" % (x,))
    if not d.is_finite():
        raise ValueError("not a price: %r" % (x,))
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_FLOOR))


def round_half_up_tick(x):
    """A price to the Rs 0.01 tick, half up (a target: 101.745 -> 101.75). Float noise
    under a billionth is cleaned first."""
    try:
        d = Decimal(str(x)).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("not a price: %r" % (x,))
    if not d.is_finite():
        raise ValueError("not a price: %r" % (x,))
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def money(x, decimals=2):
    """Rupees for people: "Rs 1,234.56" or "-Rs 1,234.56"; decimals=0 gives "Rs 5,000"."""
    try:
        v = Decimal(str(x)).quantize(Decimal(1).scaleb(-int(decimals)), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("not an amount of money: %r" % (x,))
    if not v.is_finite():
        raise ValueError("not an amount of money: %r" % (x,))
    return "%sRs %s" % ("-" if v < 0 else "", format(abs(v), ",f"))


def cost_settings(cfg):
    """COSTS with the "costs" block of psx/config.json on top."""
    c = dict(COSTS)
    block = cfg.get("costs") if isinstance(cfg, dict) else None
    if isinstance(block, dict):
        c.update(block)
    return c


def leg_cost(price, qty, costs=None, commission=True):
    """Estimated cost of one buy or one sell, in rupees, rounded half up to 0.01.
    commission = max(commission_pct % of the value, commission_min_per_share x qty);
    cost = commission x (1 + sales_tax_pct %) + other_charges_pct % of the value.
    commission=False leaves the commission out; the other charges still apply."""
    c = dict(COSTS)
    c.update(costs or {})
    keys = ("commission_pct", "commission_min_per_share", "sales_tax_pct", "other_charges_pct")
    try:
        q = Decimal(str(qty))
        value = Decimal(str(price)) * q
        rate = dict((k, Decimal(str(c[k]))) for k in keys)
    except (InvalidOperation, ValueError):
        raise ValueError("costs: the price, the quantity and every cost setting must be numbers")
    if not value.is_finite() or value < 0 or q < 0 or any(not v.is_finite() or v < 0 for v in rate.values()):
        raise ValueError("costs: the price, the quantity and every cost setting must be 0 or more")
    cost = rate["other_charges_pct"] / 100 * value
    if commission:
        fee = max(rate["commission_pct"] / 100 * value, rate["commission_min_per_share"] * q)
        cost += fee * (1 + rate["sales_tax_pct"] / 100)
    return float(cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round_trip_cost(buy_price, sell_price, qty, costs=None):
    """Buy leg plus sell leg (leg_cost each), in rupees. With "day_trade_one_side": true
    the sell leg has no commission (PSX charges its minimum on one side of a same-day trade)."""
    c = dict(COSTS)
    c.update(costs or {})
    buy = leg_cost(buy_price, qty, c, True)
    sell = leg_cost(sell_price, qty, c, c.get("day_trade_one_side") is not True)
    return float(Decimal(str(buy)) + Decimal(str(sell)))


def max_loss_at_stop(entry, stop, qty, costs=None):
    """The max loss of a long, costs included: qty x (entry - stop) + round_trip_cost(entry,
    stop, qty), in rupees to 0.01. The stop is below the entry: check that first."""
    cost = round_trip_cost(entry, stop, qty, costs)          # this also checks the numbers
    risk = (Decimal(str(entry)) - Decimal(str(stop))) * Decimal(str(qty))
    return float((risk + Decimal(str(cost))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def costs_vs_risk(entry, stop, qty, costs=None):
    """Round-trip costs (buy at the entry, sell at the stop) as a share of the price risk,
    qty x (entry - stop): 0.33 means the costs are a third of the risk. None when the risk
    is 0 or less. psx-exit, psx-budget and psx-risk warn above COST_WARN with this number."""
    risk = (Decimal(str(entry)) - Decimal(str(stop))) * Decimal(str(qty))
    if risk <= 0:
        return None
    return float(Decimal(str(round_trip_cost(entry, stop, qty, costs))) / risk)


def reward_risk(entry, stop, target):
    """(target - entry) / (entry - stop) for a long, from the prices in paisa: an exact
    number to show (round it only for display), or None when the risk is 0 or less."""
    e, s, t = (Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for x in (entry, stop, target))
    if e - s <= 0:
        return None
    return float((t - e) / (e - s))


def meets_reward_risk(entry, stop, target, minimum):
    """True when (target - entry) >= minimum x (entry - stop), compared exactly in paisa,
    with no rounding first, so psx-exit, psx-budget and psx-risk always agree."""
    e, s, t = (Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) for x in (entry, stop, target))
    if e - s <= 0:
        return False
    return (t - e) >= Decimal(str(minimum)) * (e - s)


# ---------------------------------------------------------------- saved PSX pages and the tape (read only)
# copied from tests/psx_core.py, verbatim: never edit a copy

def num(text):
    """A number from PSX text -> float: "1,234.5" -> 1234.5, "(18.71)" -> -18.71,
    "19.34%" -> 19.34, a unicode minus is a minus. "N/A", "-" or empty -> None."""
    if text is None or isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        v = float(text)
        return v if v == v and abs(v) != float("inf") else None
    s = str(text).replace(chr(0x2212), "-").replace("\xa0", " ").replace(",", "").replace("%", "").strip()
    if s[:3].lower() == "rs.":
        s = s[3:].strip()
    if s in ("", "-", "--") or s.upper() in ("N/A", "NA"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1].strip()
    try:
        v = float(s)
    except ValueError:
        return None
    if v != v or abs(v) == float("inf"):
        return None
    return -v if neg else v


def html_text(s):
    """HTML -> plain text: tags out, entities decoded, runs of spaces made one."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def page_time(text):
    """A PSX page time ("Thu, Oct 29, 2026 3:49 PM", "Oct 29, 2026 3:50 PM") -> naive
    datetime (Karachi time), or None. Month names are read in English, whatever the
    machine's language."""
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?",
                  str(text or ""))
    if not m:
        return None
    months = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    if m.group(1).lower() not in months:
        return None
    h, ap = int(m.group(4)), (m.group(7) or "").upper()
    if ap == "PM" and h < 12:
        h += 12
    elif ap == "AM" and h == 12:
        h = 0
    try:
        return datetime.datetime(int(m.group(3)), months.index(m.group(1).lower()) + 1, int(m.group(2)),
                                 h, int(m.group(5)), int(m.group(6) or 0))
    except ValueError:
        return None


def parse_screener(html):
    """A saved https://dps.psx.com.pk/screener page -> {"clock", "rows"}.

    clock is the page's top-bar clock text: when the page was made. The screener has
    no as-of time of its own, and its data is delayed 5 minutes. rows maps each symbol
    to {symbol, name, flags (NC, XD, XR), sector (the code), listed_in (index codes),
    market_cap (Rs), price, change_pct, change_1y_pct, pe, dividend_yield, free_float
    (shares), volume_30d_avg}. A stock with no trade today shows its last day's price."""
    page = html if isinstance(html, str) else ""
    cell = r'[^<]*(?:<(?!/td>)[^<]*)*'
    rx = re.compile(r'<tr><td data-order="([^"]*)"><a class="tbl__symbol" href="/company/[^"]*" data-title="([^"]*)">'
                    r'<strong>[^<]*</strong></a>((?:<div class="tag[^"]*">[^<]*</div>)*)</td><td>([^<]*)</td>'
                    r'<td>([^<]*)</td>((?:<td class="right[^"]*" data-order="[^"]*" data-filter="[^"]*">'
                    + cell + r'</td>){8})</tr>')
    keys = ("market_cap", "price", "change_pct", "change_1y_pct", "pe", "dividend_yield", "free_float",
            "volume_30d_avg")
    rows = {}
    for m in rx.finditer(page):
        sym = html_text(m.group(1)).upper()
        row = {"symbol": sym, "name": html_text(m.group(2)) or None,
               "flags": [html_text(t) for t in re.findall(r'<div class="tag[^"]*">([^<]*)</div>', m.group(3))
                         if html_text(t)],
               "sector": html_text(m.group(4)) or None,
               "listed_in": [x.strip() for x in html_text(m.group(5)).split(",") if x.strip()]}
        row.update(zip(keys, [num(v) for v in re.findall(r'data-order="([^"]*)" data-filter=', m.group(6))]))
        rows[sym] = row
    clock = re.search(r'<div class="topbar__status__label">([^<]*)</div>', page)
    return {"clock": (html_text(clock.group(1)) or None) if clock else None, "rows": rows}


def parse_company(html):
    """A saved https://dps.psx.com.pk/company/<SYM> page -> dict.

    symbol, name, flags (tags such as XD, SUSPENDED, NON-COMPLIANT), sector (the name),
    price, change, change_pct, as_of (the page's "As of" time, naive Karachi time; a
    stock with no trade today shows an older one), reg (REG board: open, high, low,
    volume, lower and upper = the CIRCUIT BREAKER for the day, day_low, day_high,
    wk52_low, wk52_high, ask, ask_volume, bid, bid_volume, ldcp, var, haircut, pe,
    change_1y, change_ytd), profile (fiscal_year_end, website, auditor, registrar,
    address, description, people [[name, role]]), equity (market_cap_000 in Rs
    thousands, shares, face_value, free_float_shares, free_float_pct), announcements
    (5 newest per tab: {"date": "YYYY-MM-DD", "title", "doc_id", "pdf", "image",
    "attachment"}, links in full), financials ("annual", "quarterly": {"columns",
    "rows": {label: [values]}}, Rs thousands except EPS; quarters stand alone and their
    labels can be wrong) and ratios. A part the page does not have is None or empty."""
    page = html if isinstance(html, str) else ""
    reg_keys = ("open", "high", "low", "volume", "lower", "upper", "day_low", "day_high", "wk52_low",
                "wk52_high", "ask", "ask_volume", "bid", "bid_volume", "ldcp", "var", "haircut", "pe",
                "change_1y", "change_ytd")
    out = {"symbol": None, "name": None, "flags": [], "sector": None, "price": None, "change": None,
           "change_pct": None, "as_of": None, "reg": dict.fromkeys(reg_keys),
           "profile": dict(dict.fromkeys(("fiscal_year_end", "website", "auditor", "registrar", "address",
                                          "description")), people=[]),
           "equity": dict.fromkeys(("market_cap_000", "shares", "face_value", "free_float_shares",
                                    "free_float_pct")),
           "announcements": {"Financial Results": [], "Board Meetings": [], "Others": []},
           "financials": {"annual": {"columns": [], "rows": {}}, "quarterly": {"columns": [], "rows": {}}},
           "ratios": {"columns": [], "rows": {}}}
    cell = r'[^<]*(?:<(?!/td>)[^<]*)*'
    marks = [(m.group(1), m.start()) for m in re.finditer(
        r'<div class="section section--padded (?:company|companyEquity)" id="([a-z]+)">', page)]
    sec = {}
    for i, (name, start) in enumerate(marks):
        sec[name] = page[start:marks[i + 1][1] if i + 1 < len(marks) else len(page)]

    def stats(block):
        found = []
        for it in re.finditer(r'<div class="stats_item"><div class="stats_label">(.*?)</div>'
                              r'<div class="stats_value[^"]*">(.*?)</div>', block, re.S):
            found.append((re.sub(r"\s+", " ", re.sub(r"[*^]", "", html_text(it.group(1)))).strip().lower(),
                          it.group(2)))
        return found

    def table(block):
        rows = {}
        for label, cells in re.findall(r'<tr><td>([^<]+)</td>(.*?)</tr>', block, re.S):
            rows[html_text(label)] = [num(c) for c in re.findall(
                r'<td class="right">(?:<span[^>]*>([^<]*)</span>)?</td>', cells)]
        return {"columns": [html_text(c) for c in re.findall(r'<th class="right">([^<]*)</th>', block)],
                "rows": rows}

    q = sec.get("quote") or page
    m = re.search(r'<div class="pageHeader__title">([^<]*)</div>', page)
    if m:
        out["symbol"] = html_text(m.group(1)).upper() or None
    m = re.search(r'<div class="quote__name">(.*?)(?:<div class="tag|</div>)', q, re.S)
    if m:
        out["name"] = html_text(m.group(1)) or None
    m = re.search(r'<div class="quote__name">(.*?)<div class="quote__(?:sector|price)', q, re.S)
    if m:
        out["flags"] = [html_text(t) for t in re.findall(r'<div class="tag[^"]*">([^<]*)</div>', m.group(1))
                        if html_text(t)]
    m = re.search(r'<div class="quote__sector"><span>(.*?)</span>', q, re.S)
    if m:
        out["sector"] = html_text(m.group(1)) or None
    m = re.search(r'<div class="quote__close">\s*(?:Rs\.?)?\s*([^<]*)</div>', q)
    if m:
        out["price"] = num(m.group(1))
    m = re.search(r'<div class="quote__change[^"]*">.*?<div class="change__value">([^<]*)</div>\s*'
                  r'<div class="change__percent">\s*\(?([^)<]*)\)?\s*</div>', q, re.S)
    if m:
        out["change"], out["change_pct"] = num(m.group(1)), num(m.group(2))
    m = re.search(r'<div class="quote__date">\^?\s*As of ([^<]+)</div>', q)
    if m:
        out["as_of"] = page_time(m.group(1))
    panels = list(re.finditer(r'<div class="tabs__panel" data-name="([A-Za-z]+)">', q))
    reg = ""
    for i, p in enumerate(panels):
        if p.group(1) == "REG":
            reg = q[p.start():panels[i + 1].start() if i + 1 < len(panels) else len(q)]
    names = {"open": "open", "high": "high", "low": "low", "volume": "volume", "ask price": "ask",
             "ask volume": "ask_volume", "bid price": "bid", "bid volume": "bid_volume", "ldcp": "ldcp",
             "var": "var", "haircut": "haircut", "p/e ratio (ttm)": "pe", "1-year change": "change_1y",
             "ytd change": "change_ytd"}
    ranges = {"circuit breaker": ("lower", "upper"), "day range": ("day_low", "day_high"),
              "52-week range": ("wk52_low", "wk52_high")}
    for label, raw in stats(reg):
        if label in names:
            out["reg"][names[label]] = num(html_text(raw))
        elif label in ranges:
            lo, hi = re.search(r'data-low="([^"]*)"', raw), re.search(r'data-high="([^"]*)"', raw)
            if lo and hi:
                pair = (num(lo.group(1)), num(hi.group(1)))
            else:
                parts = re.split("%s|%s| - " % (chr(0x2014), chr(0x2013)), html_text(raw))
                pair = (num(parts[0]), num(parts[-1])) if len(parts) == 2 else (None, None)
            out["reg"][ranges[label][0]], out["reg"][ranges[label][1]] = pair
    for k in ("volume", "ask_volume", "bid_volume"):
        if out["reg"][k] is not None:
            out["reg"][k] = int(out["reg"][k])
    pr = sec.get("profile", "")
    heads = {"fiscal year end": "fiscal_year_end", "website": "website", "auditor": "auditor",
             "registrar": "registrar", "address": "address", "business description": "description"}
    for m in re.finditer(r'<div class="item__head">([^<]+)</div>\s*<p>(.*?)</p>', pr, re.S):
        key = heads.get(html_text(m.group(1)).lower())
        if key:
            out["profile"][key] = html_text(m.group(2)) or None
    out["profile"]["people"] = [[html_text(a), html_text(b)] for a, b in re.findall(
        r'<tr><td><strong>(.*?)</strong></td><td>(' + cell + r')</td></tr>', pr, re.S)]
    for label, raw in stats(sec.get("equity", "")):
        v = num(html_text(raw))
        if label.startswith("market cap"):
            out["equity"]["market_cap_000"] = v
        elif label == "shares":
            out["equity"]["shares"] = int(v) if v is not None else None
        elif label == "face value":
            out["equity"]["face_value"] = v
        elif label == "free float" and "%" in raw:
            out["equity"]["free_float_pct"] = v
        elif label == "free float":
            out["equity"]["free_float_shares"] = int(v) if v is not None else None
    months = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    for m in re.finditer(r'<div class="tabs__panel" data-name="(Financial Results|Board Meetings|Others)">(.*?)'
                         r'(?=<div class="tabs__panel" data-name=|$)', sec.get("announcements", ""), re.S):
        rows = []
        for day, title, docs in re.findall(r'<tr><td>([A-Z][a-z]{2} \d{1,2}, \d{4})</td><td>(' + cell +
                                           r')</td><td>(' + cell + r')</td></tr>', m.group(2)):
            dm = re.match(r"([A-Za-z]{3}) (\d{1,2}), (\d{4})", day)
            try:
                day = datetime.date(int(dm.group(3)), months.index(dm.group(1).lower()) + 1,
                                    int(dm.group(2))).isoformat()
            except ValueError:
                day = None
            pdf = re.search(r'href="(/download/document/(\d+)[^"]*\.pdf)"', docs)
            att = re.search(r'href="(/download/attachment/(\d+)[^"]*\.pdf)"', docs)
            img = re.search(r'data-images="([^",]+)', docs)
            doc_id = (pdf and pdf.group(2)) or (att and att.group(2)) or None
            if not doc_id and img and re.match(r"\d+", img.group(1).strip()):
                doc_id = re.match(r"\d+", img.group(1).strip()).group(0)
            rows.append({"date": day,
                         "title": html_text(re.sub(r'<div class="tag[^"]*">([^<]*)</div>', r" (\1)", title)),
                         "doc_id": doc_id, "pdf": PSX + pdf.group(1) if pdf else None,
                         "image": PSX + "/download/image/" + img.group(1).strip() if img else None,
                         "attachment": PSX + att.group(1) if att else None})
        out["announcements"][m.group(1)] = rows
    for m in re.finditer(r'<div class="tabs__panel" data-name="(Annual|Quarterly)">(.*?)</table>',
                         sec.get("financials", ""), re.S):
        out["financials"][m.group(1).lower()] = table(m.group(2))
    if sec.get("ratios"):
        out["ratios"] = table(sec["ratios"])
    return out


def tape_path(root, d, name):
    """<root>/tape/YYYY-MM-DD/<name>.jsonl: "market" for screener snapshots, a symbol
    for its company page snapshots."""
    return os.path.join(root, "tape", d.isoformat(), "%s.jsonl" % re.sub(r"[^A-Za-z0-9_-]", "_", str(name)))


def read_tape(path):
    """A tape file -> its snapshots (dicts) in file order. Bad lines are skipped; no file -> []."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except ValueError:
                continue
            if isinstance(snap, dict):
                out.append(snap)
    return out


# ---------------------------------------------------------------- swing style and daily levels (copied verbatim from tests/psx_core.py; never edit a copy)

def add_sessions(d, n, cfg=None):
    """The date n PSX sessions after date d (d is not counted; n = 0 gives d). Weekends,
    PSX holidays and the config's "holidays" are skipped (trading_day). ValueError when
    n is not a whole number, 0 or more."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError("a number of sessions must be a whole number, 0 or more")
    out = d
    while n > 0:
        out += datetime.timedelta(days=1)
        if trading_day(out, cfg)[0]:
            n -= 1
    return out


def price_limits(ldcp):
    """(lower, upper) daily price limits of a PSX share from its LDCP (the last close,
    ex-adjusted on an ex-date): LDCP x 0.90 and x 1.10, rounded half up to 0.01; when
    10% of LDCP is under Rs 1, LDCP - 1.00 and + 1.00. Never below 0.01. Trading can
    reach a limit, not pass it. ETF limits come from the NAV: not covered here. Float
    noise under a billionth is cleaned first, so 60.149999999999 counts as 60.15."""
    try:
        p = Decimal(str(ldcp))
        if p.is_finite():
            p = p.quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("price limits need a last close (LDCP), got %r" % (ldcp,))
    if not p.is_finite() or p <= 0:
        raise ValueError("price limits need a last close (LDCP) above 0, got %r" % (ldcp,))
    cent = Decimal("0.01")
    if p * Decimal("0.10") < 1:
        lower, upper = p - 1, p + 1
    else:
        lower, upper = p * Decimal("0.90"), p * Decimal("1.10")
    lower = max(lower.quantize(cent, rounding=ROUND_HALF_UP), cent)
    return float(lower), float(upper.quantize(cent, rounding=ROUND_HALF_UP))


def gap_loss(entry, qty, costs=None):
    """The loss of a long, costs included, if the next session opens at its lower price limit:
    max_loss_at_stop(entry, the lower limit of price_limits(entry), qty, costs). It takes the
    buy day's close to be the entry. More days down can lose more."""
    return max_loss_at_stop(entry, price_limits(entry)[0], qty, costs)


def trade_style(cfg):
    """"day" (the default) or "swing", from "style" in psx/config.json. Anything else
    raises ValueError with a plain message."""
    v = cfg.get("style") if isinstance(cfg, dict) else None
    if v is None or v == "day":
        return "day"
    if v == "swing":
        return "swing"
    raise ValueError('"style" in psx/config.json is "day" or "swing", not %r' % (v,))


def swing_settings(cfg):
    """SWING with the "swing" block of psx/config.json on top."""
    s = dict(SWING)
    block = cfg.get("swing") if isinstance(cfg, dict) else None
    if isinstance(block, dict):
        s.update(block)
    return s


def trade_costs(cfg):
    """cost_settings(cfg). In swing style "day_trade_one_side" is False: a sell on a later
    day always pays commission, and a same-day stop-out is counted the safe way."""
    c = cost_settings(cfg)
    if trade_style(cfg) == "swing":
        c["day_trade_one_side"] = False
    return c


# ---------------------------------------------------------------- inputs

def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SizeError("no %s at %s" % (what, path))
    except json.JSONDecodeError as e:
        raise SizeError("%s is not valid JSON: %s" % (path, e))


def load_config(path, required=False):
    """DEFAULTS with the config file on top. A file you named with --config (required) must be
    there: a typo never falls back to the defaults. The default psx/config.json may be missing."""
    cfg = dict(DEFAULTS)
    if path and not os.path.exists(path):
        if required:
            raise SizeError("no config file at %s (copy config.example.json from this skill's folder)" % path)
        return cfg
    if path:
        data = read_json(path, "config")
        if not isinstance(data, dict):
            raise SizeError("%s is not a JSON object" % path)
        cfg.update(data)
    return cfg


def blocked_set(value):
    """The "blocked" tickers as a set. A string is one ticker, or a few split on commas
    ("ABC, XYZ"). Anything but a list or a string is a plain error, never ignored."""
    if value is None:
        return set()
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        raise SizeError("\"blocked\" in psx/config.json must be a list of tickers, like [\"ABC\", \"XYZ\"]")
    return {str(b).strip().upper() for b in value if b is not None and str(b).strip()}


def _f(x):
    """A finite number as a float, else None. True and False are not numbers here."""
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_weights(data):
    """Accept a weights file, a {ticker: weight} dict or a list of rows.

    Returns [{"ticker", "name", "weight"}] with weight as a float."""
    if isinstance(data, dict) and "weights" in data:
        data = data["weights"]
    rows = []
    if isinstance(data, dict):
        for t, w in data.items():
            rows.append({"ticker": t, "name": "", "weight": w})
    elif isinstance(data, list):
        for i, r in enumerate(data):
            if not isinstance(r, dict):
                raise SizeError("weights[%d] is not an object" % i)
            t = r.get("ticker") or r.get("symbol")
            w = r.get("weight", r.get("weight_pct", r.get("pct")))
            if not t or w is None:
                raise SizeError("weights[%d] needs a ticker and a weight" % i)
            rows.append({"ticker": t, "name": r.get("name", ""), "weight": w})
    else:
        raise SizeError("weights must be a list or an object")
    out = []
    for r in rows:
        w = _f(r["weight"])
        if w is None:
            raise SizeError("weight for %s is not a number: %r" % (r["ticker"], r["weight"]))
        out.append({"ticker": str(r["ticker"]).strip().upper(), "name": r["name"] or "", "weight": w})
    return out


def weights_from_arg(text):
    """OGDC=30,HBL=25 -> {ticker: weight}"""
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise SizeError("--weights wants TICKER=WEIGHT pairs, got %r" % part)
        t, w = part.split("=", 1)
        out[t.strip().upper()] = w.strip()
    return out


def load_prices(path):
    """A prices file (psx/prices.json, or a bare {SYM: price} map) -> {SYM: price}."""
    if not path:
        return {}
    data = read_json(path, "prices")
    prices = data.get("prices", data) if isinstance(data, dict) else {}
    out = {}
    for t, p in (prices.items() if isinstance(prices, dict) else ()):
        v = _f(p)
        if v is not None and v > 0:
            out[str(t).strip().upper()] = v
    return out


# ---------------------------------------------------------------- quotes (a market order's fresh price)
# A quote is {"price", "time" (naive UTC: its data time, or None), "source", "lower", "upper"}.

def price_time(doc, sym, delay_minutes=0):
    """This symbol's data time: the quote's own time (detail[sym].time, the page's As of).
    Without it, the fetch time (detail[sym].fetched, else the file's as_of) minus the data
    delay, since free PSX data is about 5 minutes late. psx-risk dates a price the same way."""
    detail = (doc or {}).get("detail")
    det = (detail.get(sym) if isinstance(detail, dict) else None) or {}
    det = det if isinstance(det, dict) else {}
    t = parse_utc(det.get("time"))
    if t:
        return t
    f = parse_utc(det.get("fetched")) or parse_utc((doc or {}).get("as_of"))
    return f - datetime.timedelta(minutes=delay_minutes) if f else None


def file_quote(doc, sym, path, delay_minutes=0):
    """sym's quote in a prices.json doc (psx-alerts writes it), or None."""
    if not isinstance(doc, dict):
        return None
    prices = doc.get("prices")
    p = _f(prices.get(sym)) if isinstance(prices, dict) else None
    if p is None or p <= 0:
        return None
    detail = doc.get("detail")
    det = (detail.get(sym) if isinstance(detail, dict) else None) or {}
    det = det if isinstance(det, dict) else {}
    return {"price": p, "time": price_time(doc, sym, delay_minutes), "source": path,
            "lower": _f(det.get("lower")), "upper": _f(det.get("upper"))}


def tape_time(text):
    """A tape "asof" ("2026-10-29 10:52", Karachi) -> naive Karachi time, or None.
    A time with Z or an offset is turned into Karachi time."""
    t = str(text or "").strip()
    try:
        when = datetime.datetime.fromisoformat(t[:-1] + "+00:00" if t[-1:] in ("Z", "z") else t)
    except ValueError:
        return None
    return to_pkt(when) if when.tzinfo is not None else when


def tape_quote(root, sym, now_utc):
    """sym's newest price on today's company tape in the data folder
    (tape/YYYY-MM-DD/SYM.jsonl, Karachi date), or None. A snapshot counts when its "asof"
    (its data time) is not after now: the way the pack replays a tape. The screener tape
    is not used: it shows a share with no trade today at its last day's price."""
    now_pkt = to_pkt(now_utc)
    path = tape_path(root, now_pkt.date(), sym)
    best = None
    for s in read_tape(path):
        if "prices" in s or str(s.get("symbol") or sym).strip().upper() != sym:
            continue
        price, when = _f(s.get("price")), tape_time(s.get("asof"))
        if price is None or price <= 0 or when is None or when > now_pkt:
            continue
        if best is None or when > best[0]:
            best = (when, {"price": price, "time": pkt_to_utc(when), "source": "the tape " + path,
                           "lower": _f(s.get("lower")), "upper": _f(s.get("upper"))})
    return best[1] if best else None


def page_quote(root, sym):
    """sym's price on a saved company page (data folder company/SYM.html), or None. The page's
    "As of" time is the data time; its CIRCUIT BREAKER gives the day's price limits."""
    name = re.sub(r"[^A-Z0-9-]", "", str(sym or "").upper())
    path = os.path.join(root, "company", name + ".html")
    if not name or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        p = parse_company(fh.read())
    price = _f(p.get("price"))
    if (p.get("symbol") and p["symbol"] != name) or price is None or price <= 0:
        return None
    reg = p.get("reg") or {}
    return {"price": price, "time": pkt_to_utc(p["as_of"]) if p.get("as_of") else None,
            "source": "the saved page " + path, "lower": _f(reg.get("lower")), "upper": _f(reg.get("upper"))}


def quotes_for(sym, now_utc, prices_path=None, data_root=None, delay_minutes=0):
    """Every quote for sym this tool can see, in the lookup order: the prices file first, then
    the data folder: today's tape, then the saved company page. Nothing is fetched."""
    out = []
    if prices_path:
        q = file_quote(read_json(prices_path, "prices"), sym, prices_path, delay_minutes)
        if q:
            out.append(q)
    if data_root and os.path.isdir(data_root):
        for q in (tape_quote(data_root, sym, now_utc), page_quote(data_root, sym)):
            if q:
                out.append(q)
    return out


def pick_fresh(quotes, now_utc, minutes=FRESH_MINUTES):
    """(the fresh quote, the newest one that is too old), either may be None. The quotes come
    in the lookup order (quotes_for) and the FIRST fresh one wins, the rule psx-risk uses too.
    Fresh: at most `minutes` old by its own time (the data time, not the fetch time). A quote
    more than a minute after now does not exist yet at that time, so it is skipped. An undated
    quote is never fresh."""
    old = None
    for q in quotes:
        t = q.get("time")
        if t is None:
            old = old or q
            continue
        age = (now_utc - t).total_seconds()
        if age < -60:
            continue
        if age <= minutes * 60:
            return q, old
        if old is None or old.get("time") is None or t > old["time"]:
            old = q
    return None, old


def account_from(pf, now_utc, prices_path=None, data_root=None, delay_minutes=0):
    """psx/portfolio.json -> (cash, {SYM: value held}, notes): the account psx-risk checks
    against too. A holding is valued at its fresh price (the lookup of a market order's
    price), else at its cost (its lots' "cost"), and a note says so."""
    if not isinstance(pf, dict):
        raise SizeError("psx/portfolio.json must be a JSON object with \"cash\" and \"positions\"")
    cash = _f(pf.get("cash"))
    if cash is None:
        raise SizeError("psx/portfolio.json needs a \"cash\" number (rupees)")
    positions = pf.get("positions") or []
    if not isinstance(positions, list):
        raise SizeError("\"positions\" in psx/portfolio.json must be a list")
    held, notes = {}, []
    for p in positions:
        t = str(p.get("ticker") or p.get("symbol") or "").strip().upper() if isinstance(p, dict) else ""
        if not t:
            continue
        lots = [x for x in p.get("lots") or [] if isinstance(x, dict)]
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(x.get("shares")) or 0 for x in lots)
        if n <= 0:
            continue
        q = pick_fresh(quotes_for(t, now_utc, prices_path, data_root, delay_minutes), now_utc)[0]
        if q:
            value = n * q["price"]
        else:
            value = sum(_f(x.get("cost")) or 0 for x in lots)
            notes.append("No fresh price for %s: its %s shares count at their cost, %s." % (t, count(n), rs(value)))
        held[t] = round(held.get(t, 0.0) + value, 2)
    return cash, held, notes


def plan_limits(plan):
    """The plan's "limits" (psx-exit writes {"lower", "upper", "ldcp", "from"}) as
    {"lower", "upper", "from"}, or None when the plan has none."""
    lim = plan.get("limits") if isinstance(plan, dict) else None
    if not isinstance(lim, dict):
        return None
    lo, hi = _f(lim.get("lower")), _f(lim.get("upper"))
    if lo is None or hi is None or not 0 < lo < hi:
        return None
    return {"lower": lo, "upper": hi, "from": str(lim.get("from") or "the plan")}


def quote_limits(quotes, now_utc):
    """Today's price limits from the newest quote of today (Karachi date) that has them, or
    None. The limits hold all day, so the quote need not be fresh."""
    today = to_pkt(now_utc).date()
    best = None
    for q in quotes:
        t, lo, hi = q.get("time"), q.get("lower"), q.get("upper")
        if t is None or lo is None or hi is None or not 0 < lo < hi:
            continue
        if to_pkt(t).date() != today or (now_utc - t).total_seconds() < -60:
            continue
        if best is None or t > best[0]:
            best = (t, {"lower": lo, "upper": hi, "from": q["source"]})
    return best[1] if best else None


def limits_words(lim):
    """Where today's price limits came from, in words."""
    src = lim.get("from") or "the plan"
    return LIMITS_FROM.get(src, "from " + src)


def check_costs(costs):
    """A plain error when the "costs" block cannot be used."""
    try:
        leg_cost(100.0, 1, costs)
    except (TypeError, ValueError) as e:
        raise SizeError('the "costs" block in psx/config.json: %s' % e)


def day_time(text, day, cfg):
    """A config time ("15:20", written for a normal Monday to Thursday) for that Karachi
    day as "HH:MM": config_time() moves it to the day's hours and keeps it in the session."""
    try:
        t = parse_hhmm(text, "flat_by")
        d = datetime.date.fromisoformat(str(day).strip())
        return config_time(t, d, cfg).strftime("%H:%M")
    except (TypeError, ValueError) as e:
        raise SizeError("%s (check flat_by in psx/config.json and the plan's date)" % e)


# ---------------------------------------------------------------- numbers for people, in paisa

def shares_for(pkr, per_share):
    """Whole shares that fit in pkr rupees at per_share, in exact paisa."""
    u, p = int(round(float(pkr) * 100)), int(round(float(per_share) * 100))
    if p <= 0 or u <= 0:
        return 0
    return u // p


def pct_of(pkr, pct):
    """pct % of pkr rupees, rounded half up to the paisa (the buffer and the position cap)."""
    return float((Decimal(str(pkr)) * Decimal(str(pct)) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def spend(price, qty, costs):
    """What a buy takes from your cash: qty x price plus the buy's costs (leg_cost)."""
    return float(Decimal(str(price)) * Decimal(int(qty)) + Decimal(str(leg_cost(price, qty, costs))))


def loss_shares(entry, stop, cap, costs):
    """The most whole shares whose max loss at the stop, costs included
    (max_loss_at_stop), is at most cap rupees. That loss only grows with the shares, so a
    search finds the edge. Costs make it no straight line (a minimum per share, rounding)."""
    lo, hi = 0, shares_for(cap, entry - stop)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if max_loss_at_stop(entry, stop, mid, costs) <= cap + EPS:
            lo = mid
        else:
            hi = mid - 1
    return lo


def buy_shares(price, pkr, costs):
    """The most whole shares whose value plus the buy's costs fit in pkr rupees."""
    lo, hi = 0, shares_for(pkr, price)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if spend(price, mid, costs) <= pkr + EPS:
            lo = mid
        else:
            hi = mid - 1
    return lo


def px(x):
    """A price for people: 100.65, 99.50 (two decimals, half up)."""
    return "%.2f" % round_half_up_tick(x)


def rs(x):
    """Rupees for people: "Rs 5,000" for whole rupees, else "Rs 4,999.94" (money())."""
    v = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return money(v, 0) if v == v.to_integral_value() else money(v)


def count(n):
    """3428 -> "3,428"."""
    return "{:,}".format(int(n))


def _num(x):
    """75 -> "75", 1.5 -> "1.5", 33.333 -> "33.33" (percents and ratios in sentences)."""
    x = round(float(x), 2)
    if abs(x - round(x)) < 0.005:
        return str(int(round(x)))
    return ("%.2f" % x).rstrip("0").rstrip(".")


# ---------------------------------------------------------------- swing style (SPEC v2: D31, D33, D55, D57)

def plan_style(plan):
    """"day" or "swing", from the plan's "style" (a day plan has none: the v1 shape).
    An invest plan, or any other value, is a plain error: psx-budget sizes day and swing plans."""
    v = plan.get("style")
    if v is None or v == "day":
        return "day"
    if v == "swing":
        return "swing"
    if v == "invest":
        raise SizeError("this plan is from psx-invest (\"style\": \"invest\"). psx-budget sizes day and swing plans "
                        "only: psx-invest makes its own orders (invest.py orders)")
    raise SizeError("the plan's \"style\" is \"%s\": psx-budget sizes \"day\" and \"swing\" plans only. Make a new "
                    "plan with psx-exit" % (v,))


def style_refusal(plan_kind, style, sym, plan_date=None, today=None, held=False):
    """One plain line when the plan's style is not the folder's style (D31: they do not mix), else None.
    A day plan in a swing folder names its stock in the psx-exit line only when a swing plan of that
    stock can be bought: not when you hold it (held: psx/portfolio.json has it, and swing blocks a
    second buy of a held stock), and not when the plan is from an earlier day (an old plan is no
    reason to plan that stock again)."""
    if plan_kind == style:
        return None
    if plan_kind == "swing":
        return ("this is a swing plan, and psx/config.json says \"style\": \"day\". Styles do not mix: "
                "make a day plan with psx-exit")
    try:
        old = bool(today) and datetime.date.fromisoformat(str(plan_date).strip()).isoformat() < str(today)
    except ValueError:
        old = False
    head = ("this is a day plan%s, and psx/config.json says \"style\": \"swing\". Styles do not mix"
            % ((" for %s, an earlier day" % _day(plan_date)) if old else ""))
    if held:
        return (head + ". You already hold %s in psx/portfolio.json. In swing style psx-risk blocks a second buy of "
                "a stock you hold. To size a buy of another stock, first make a swing plan for it with psx-exit "
                "(exit.py plan --symbol X)." % sym)
    if old:
        return head + ": make a new swing plan with psx-exit"
    return head + ": make a swing plan with psx-exit (exit.py plan --symbol %s)" % (sym or "SYMBOL")


def holds(path, sym):
    """True when the ledger at path (psx/portfolio.json) has shares of sym, counted the way
    account_from counts them. For the style line only, read before the sizing reads the ledger:
    a missing or bad file is False here, and account_from still says what is wrong with it."""
    if not sym or not path or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            positions = json.load(fh).get("positions") or []
        for p in [p for p in positions if isinstance(p, dict)
                  and str(p.get("ticker") or p.get("symbol") or "").strip().upper() == sym]:
            n = _f(p.get("shares", p.get("qty")))
            if n is None:
                n = sum(_f(x.get("shares")) or 0 for x in p.get("lots") or [] if isinstance(x, dict))
            if n > 0:
                return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False
    return False


def swing_exits(plan, c):
    """The exits a swing order carries, from the plan: {"exit_by", "exit_by_time", "trend_sessions",
    "hold_sessions"}. psx-exit always writes exit_by; a plan without it is refused. A missing
    exit-by time is the config's flat_by on that day (config_time, the way psx-exit sets it)."""
    sw = swing_settings(c)
    try:
        exit_by = datetime.date.fromisoformat(str(plan.get("exit_by") or "").strip())
    except ValueError:
        raise SizeError("the swing plan has no exit-by day (\"exit_by\": YYYY-MM-DD): make a new plan with psx-exit")
    if str(plan.get("date") or "") > exit_by.isoformat():
        raise SizeError("the swing plan's exit-by day %s is before its buy day %s: make a new plan with psx-exit"
                        % (exit_by.isoformat(), plan.get("date")))
    t = plan.get("exit_by_time")
    if t:
        try:
            t = parse_hhmm(t, "the plan's exit_by_time").strftime("%H:%M")
        except ValueError as e:
            raise SizeError(str(e))
    else:
        t = day_time(c.get("flat_by") or "15:20", exit_by.isoformat(), c)

    def whole(v, default):
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else default
    return {"exit_by": exit_by.isoformat(), "exit_by_time": t,
            "trend_sessions": whole(plan.get("trend_sessions"), whole(sw.get("trend_exit_sessions"), 10)),
            "hold_sessions": whole(plan.get("max_hold_sessions"), whole(sw.get("max_hold_sessions"), 20))}


def gap_cap(c):
    """The cap on one buy's gap loss in rupees (D55): swing.max_gap_loss_pkr, else (null) 3 x
    max_loss_per_trade_pkr. None when neither is set. A value that is not a number is a plain error."""
    v = swing_settings(c).get("max_gap_loss_pkr")
    if v is not None:
        if _f(v) is None or _f(v) < 0:
            raise SizeError("swing.max_gap_loss_pkr in psx/config.json must be a number, 0 or more, or null "
                            "(null: 3 x max_loss_per_trade_pkr)")
        return _f(v)
    per = _f(c.get("max_loss_per_trade_pkr"))
    return round(3 * per, 2) if per else None


def gap_shares(entry, cap, costs):
    """The most whole shares whose gap loss (gap_loss: the next session opens at its lower price
    limit, costs included) is at most cap rupees. That loss only grows with the shares, so a
    search finds the edge, the way loss_shares does."""
    drop = max(entry - price_limits(entry)[0], 0.01)
    lo, hi = 0, shares_for(cap, drop)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if gap_loss(entry, mid, costs) <= cap + EPS:
            lo = mid
        else:
            hi = mid - 1
    return lo


def read_state(path):
    """psx/state/broker.json (what psx-broker recorded) as a dict; no file -> no records."""
    if not path or not os.path.exists(path):
        return {"orders": []}
    doc = read_json(path, "broker log")
    if not isinstance(doc, dict) or not isinstance(doc.get("orders", []), list):
        raise SizeError("%s must be a JSON object with an \"orders\" list (psx-broker writes it)" % path)
    return doc


def _recs(state):
    """The records of psx/state/broker.json with "symbol" in capitals, "side" in lower case and
    "day" (the record's Karachi date)."""
    out = []
    for r in (state or {}).get("orders") or []:
        if isinstance(r, dict):
            out.append(dict(r, symbol=str(r.get("symbol") or "").strip().upper(),
                            side=str(r.get("side") or "").strip().lower(),
                            day=str(r.get("date") or str(r.get("at") or "")[:10])))
    return out


def has_buys(state):
    """True when psx/state/broker.json records a buy that was placed or filled, on any day."""
    return any(r["side"] == "buy" and r.get("status") in ("placed", "filled") for r in _recs(state))


def waiting_buys(state, today):
    """Today's buys that may still fill (D57): one per order (fingerprint), by its newest record
    of today, "approved" or "placed". A record with "of" another day is about that day's order.
    qty and entry_ref come from its approve record, the way psx-risk reads them.

    Today's fills go to these buys the way psx-risk gives them (review F60; fill_today in
    check.py): each "filled" buy record of the same symbol, dated today, goes to the oldest
    buy first, and only to a buy whose first record is not after the fill. A buy filled in
    part keeps the rest: it may still fill today, so its cash still counts. A buy is done when
    its fills cover its qty. A fill with no shares, or a buy with no qty in the log, ends it
    (the old rule: any fill).
    -> [{"symbol", "qty" (the shares with no fill yet), "entry_ref", "fingerprint", "status"}],
    plus "filled" (the shares recorded) and "order_qty" (its whole qty) for a buy filled in
    part; entry_ref may be None."""
    newest, approved, first, keys, fills = {}, {}, {}, [], []
    for r in _recs(state):
        if r.get("status") == "filled":
            if r["side"] == "buy" and r["day"] >= today:
                n = _f(r.get("qty"))
                fills.append((r["symbol"], n if n is not None and n > 0 else None, tape_time(r.get("at"))))
            continue
        fp = r.get("fingerprint")
        if not fp or r["day"] != today or str(r.get("of") or today) != today:
            continue
        if fp not in first:
            if r.get("status") not in WAITING:
                continue                 # it closes no buy that is open today
            first[fp] = tape_time(r.get("at"))
            keys.append(fp)
        newest[fp] = r
        if r.get("status") == "approved":
            approved[fp] = r
    buys = []
    for fp in keys:
        r = newest[fp]
        if r.get("status") not in WAITING or r["side"] != "buy":
            continue
        src = approved.get(fp) or r
        qty = _f(src.get("qty")) if _f(src.get("qty")) is not None else _f(r.get("qty"))
        ref = _f(src.get("entry_ref")) if _f(src.get("entry_ref")) is not None else _f(r.get("entry_ref"))
        buys.append({"symbol": r["symbol"], "qty": qty, "entry_ref": ref, "fingerprint": fp,
                     "status": r.get("status"), "first": first[fp], "filled": 0.0,
                     "left": qty if qty is not None and qty > 0 else None, "done": False})
    for sym, n, when in fills:           # in the order of the log, as psx-risk reads them
        for b in [b for b in buys if b["symbol"] == sym and not b["done"]
                  and not (when and b["first"] and b["first"] > when)]:
            if n is None or b["left"] is None:
                b["done"] = True
                if n is None:
                    continue
                break
            take = min(n, b["left"])
            b["filled"], b["left"], n = b["filled"] + take, b["left"] - take, n - take
            b["done"] = b["left"] <= EPS
            if n <= EPS:
                break
    out = []
    for b in buys:
        if b["done"]:
            continue
        w = {"symbol": b["symbol"], "qty": b["qty"], "entry_ref": b["entry_ref"], "fingerprint": b["fingerprint"],
             "status": b["status"]}
        if b["filled"] > EPS:            # filled in part: the rest waits
            w.update(qty=_whole(b["left"]), filled=_whole(b["filled"]), order_qty=_whole(b["qty"]))
        out.append(w)
    return out


def _whole(n):
    """A share count as an int when it is whole (fills come as numbers from JSON)."""
    return int(round(n)) if n is not None and abs(n - round(n)) <= EPS else n


def waiting_cash(waiting, costs):
    """What today's waiting buys take from the cash: each one's qty x entry_ref plus its buy
    costs (leg_cost), the rule psx-risk uses (D57). For a buy filled in part, qty is the rest
    (waiting_buys). They count as bought: their value is held.
    A buy with no price or no quantity in the log is a plain error: then the cash cannot be counted.
    -> {"rows": [...], "pkr": total off the cash, "value": total value, "by_symbol": {SYM: value}}."""
    rows, by = [], {}
    pkr, value = Decimal(0), Decimal(0)
    for w in waiting or []:
        q, ref = w.get("qty"), w.get("entry_ref")
        if q is None or q <= 0 or ref is None or ref <= 0:
            raise SizeError("an earlier buy today (%s) has no price in the log (psx/state/broker.json), so the cash "
                            "cannot be counted. Record its fill (ticket.py fill), or record it expired (ticket.py "
                            "result --fingerprint %s --status expired)" % (w.get("symbol") or "?",
                                                                         w.get("fingerprint") or "FP"))
        v = Decimal(str(ref)) * Decimal(str(int(q)))
        fee = Decimal(str(leg_cost(ref, int(q), costs, True)))
        rows.append({"symbol": w.get("symbol"), "qty": int(q), "entry_ref": ref, "value": float(v),
                     "pkr": float(v + fee), "fingerprint": w.get("fingerprint"), "status": w.get("status")})
        if w.get("filled"):              # filled in part: only the rest is counted here
            rows[-1].update(filled=w["filled"], order_qty=w.get("order_qty"))
        by[w.get("symbol")] = round(by.get(w.get("symbol"), 0.0) + float(v), 2)
        pkr += v + fee
        value += v
    return {"rows": rows, "pkr": round(float(pkr), 2), "value": round(float(value), 2), "by_symbol": by}


# ---------------------------------------------------------------- plan mode (the default)

def size_trade(plan, cfg, budget=None, limit=None, price=None, cash=None, today=None, market=False,
               day_limits=None, holdings=None, waiting=None):
    """Pure function: one plan + your rules -> the sizing (a dict). qty may be 0.

    limit       a limit price (None: a limit order at the plan's entry)
    market      True: a market order, sized at the higher of the plan's entry and price
    price       a FRESH latest price (used only for a market order)
    cash        the cash in psx/portfolio.json, if there is one. Then the account is this cash
                plus the holdings, as in psx-risk; with no portfolio it is the budget
    holdings    {SYM: value} of what psx/portfolio.json holds (account_from)
    today       the Karachi date (YYYY-MM-DD); a plan for another day is refused
    day_limits  today's price limits {"lower", "upper", "from"}, used when the plan has none
    waiting     swing only: today's buys with no fill yet (waiting_buys; filled in part: the
                rest). They come off the cash and count as held, as if filled (D57)

    The account sets the position cap (account x max_position_pct, minus what you already
    hold of this stock) and the cash buffer (account x cash_buffer_pct).

    Swing style ("style": "swing"): the plan must be a swing plan (and a day plan in a day
    folder); a plan for the next session is a preview ("preview" is its date); costs on both
    sides; a fifth limit, the gap limit; a target over the upper price limit is a note."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    if not isinstance(plan, dict):
        raise SizeError("the plan is not a JSON object")
    style = trade_style(c)                                   # a bad "style" is one plain error (D22)
    swing = style == "swing"
    sym = str(plan.get("symbol") or "").strip().upper()
    if not sym:
        raise SizeError("the plan has no symbol (run psx-exit)")
    mix = style_refusal(plan_style(plan), style, sym, plan.get("date"), today, sym in (holdings or {}))  # D31
    if mix:
        raise SizeError(mix)
    side = str(plan.get("side") or "buy").strip().lower()
    if side != "buy":
        raise SizeError("this pack sizes long (buy) %s only, and PSX bans selling shares you do "
                        "not hold; the plan says %r" % ("swing trades" if swing else "day trades", side))
    preview = None
    if today and plan.get("date") and str(plan["date"]) != today:
        nxt = add_sessions(datetime.date.fromisoformat(today), 1, c).isoformat() if swing else None
        if str(plan["date"]) != nxt:
            raise SizeError("the plan is for %s and today is %s in Karachi: make a new plan with psx-exit"
                            % (plan["date"], today))
        preview = nxt                                        # the evening before: sized, never written
    exits = swing_exits(plan, c) if swing else None
    entry_plan, stop, target = _f(plan.get("entry")), _f(plan.get("stop")), _f(plan.get("target"))
    for name, v in (("entry", entry_plan), ("stop", stop), ("target", target)):
        if v is None or v <= 0:
            raise SizeError("the plan needs a positive %s (run psx-exit); the order carries the entry, "
                            "the stop and the target" % name)
    if sym in blocked_set(c.get("blocked")):
        raise SizeError("%s is on your blocked list" % sym)
    for key in ("max_loss_per_trade_pkr", "max_position_pct", "max_order_pkr", "min_order_pkr", "min_reward_risk"):
        if c.get(key) is not None and (_f(c[key]) is None or _f(c[key]) < 0):
            raise SizeError("%s in psx/config.json must be a number, 0 or more" % key)

    budget = _f(budget if budget is not None else c.get("budget")) or 0.0
    if cash is None and budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in psx/config.json")
    buf_pct = _f(c.get("cash_buffer_pct") or 0)
    if buf_pct is None or not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    costs = trade_costs(c)                                   # day style: cost_settings, as in v1
    check_costs(costs)
    gcap = gap_cap(c) if swing else None

    if market:
        if price is not None and round_half_up_tick(price) > round_half_up_tick(entry_plan):
            ref, ref_from = round_half_up_tick(price), "price"
        else:
            ref, ref_from = round_half_up_tick(entry_plan), "plan"
    elif limit is not None:
        ref, ref_from = round_half_up_tick(limit), "limit"
    else:
        ref, ref_from = round_half_up_tick(entry_plan), "plan"
    stop, target = round_down_tick(stop), round_half_up_tick(target)
    if stop >= ref:
        raise SizeError("the stop %s is not below the entry %s; a long trade needs its stop underneath"
                        % (px(stop), px(ref)))
    if target <= ref:
        raise SizeError("the target %s is not above the entry %s: the move has already happened. "
                        "Skip it or make a new plan with psx-exit" % (px(target), px(ref)))
    risk_c = int(round((ref - stop) * 100))
    risk = risk_c / 100.0
    rr = float(Decimal(str(reward_risk(ref, stop, target))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    if cash is None:                     # no portfolio: the account is the budget
        account, start_cash, held, held_all, cash_from = budget, budget, 0.0, 0.0, "budget"
    else:                                # the portfolio: cash plus holdings, as psx-risk counts it
        holdings = dict(holdings or {})
        held_all = round(sum(holdings.values()), 2)
        account, start_cash, cash_from = round(cash + held_all, 2), cash, "portfolio"
        held = round(holdings.get(sym, 0.0), 2)
    owned = held                         # what the ledger holds of this stock, before today's waiting buys
    wait = waiting_cash(waiting, costs) if swing and waiting else None
    if wait:                             # swing: today's buys with no fill yet come off the cash (D57)
        start_cash = round(start_cash - wait["pkr"], 2)     # and count as held, as if filled
        held_all = round(held_all + wait["value"], 2)
        held = round(held + wait["by_symbol"].get(sym, 0.0), 2)
        account = round(start_cash + held_all, 2)
    cash_buffer = pct_of(account, buf_pct)
    available = max(round(start_cash - cash_buffer, 2), 0.0)

    limits = []
    cap_loss = _f(c.get("max_loss_per_trade_pkr"))
    if cap_loss:
        limits.append({"name": "max loss", "pkr": cap_loss, "per": risk,
                       "shares": loss_shares(ref, stop, cap_loss, costs), "no_costs": shares_for(cap_loss, risk),
                       "how": "%s at the stop, costs included" % rs(cap_loss)})
    gap_low = price_limits(ref)[0] if swing else None
    if gcap is not None:                 # swing: the fifth limit (D55)
        limits.append({"name": "gap limit", "pkr": gcap, "per": round(ref - gap_low, 2),
                       "shares": gap_shares(ref, gcap, costs),
                       "how": "%s if it opens at the lower limit %s, costs included" % (rs(gcap), px(gap_low))})
    cap_pct = _f(c.get("max_position_pct"))
    if cap_pct:
        cap = pct_of(account, cap_pct)
        room = max(round(cap - held, 2), 0.0)
        if cash_from == "budget" and not wait:
            how = "%s%% of %s = %s / %s" % (_num(cap_pct), rs(account), rs(cap), px(ref))
        elif held > 0:
            how = ("%s%% of the %s account = %s, minus %s %s = %s / %s"
                   % (_num(cap_pct), rs(account), rs(cap), rs(held),
                      "held or waiting" if held > owned else "held", rs(room), px(ref)))
        else:
            how = "%s%% of the %s account = %s / %s" % (_num(cap_pct), rs(account), rs(cap), px(ref))
        limits.append({"name": "position cap", "pkr": room, "per": ref, "shares": shares_for(room, ref),
                       "how": how})
    max_order = _f(c.get("max_order_pkr"))
    if max_order:
        limits.append({"name": "max order", "pkr": max_order, "per": ref, "shares": shares_for(max_order, ref),
                       "how": "%s / %s" % (rs(max_order), px(ref))})
    where = "budget" if cash_from == "budget" else "cash in psx/portfolio.json"
    if wait:
        where += " after today's waiting buys,"
    limits.append({"name": "cash", "pkr": available, "per": ref, "shares": buy_shares(ref, available, costs),
                   "how": "%s (%s minus the buffer) / %s, with buy costs" % (rs(available), where, px(ref))})

    qty = max(0, min(l["shares"] for l in limits))
    bound = [l["name"] for l in limits if l["shares"] == qty]
    for l in limits:
        l["binds"] = l["name"] == bound[0]
    value = round(float(Decimal(str(ref)) * qty), 2)
    total = round_trip_cost(ref, stop, qty, costs)
    buy = leg_cost(ref, qty, costs)
    sell = round(float(Decimal(str(total)) - Decimal(str(buy))), 2)
    price_risk = round(qty * risk_c / 100.0, 2)
    loss = max_loss_at_stop(ref, stop, qty, costs)
    one_more = None
    if bound[0] == "max loss":
        one_more = {"qty": qty + 1, "max_loss": max_loss_at_stop(ref, stop, qty + 1, costs)}
    gap, gap_more = None, None
    if swing:                            # the loss if the next session opens at its lower limit (D55)
        gap = gap_loss(ref, qty, costs) if qty > 0 else 0.0
        if bound[0] == "gap limit":
            gap_more = {"qty": qty + 1, "gap_loss": gap_loss(ref, qty + 1, costs)}
    share = costs_vs_risk(ref, stop, qty, costs) if qty > 0 else None   # the rule of psx-exit and psx-risk
    cost_warning = None
    if share is not None and share > COST_WARN:
        cost_warning = ("Costs are about %s%% of the price risk (over %s%%). You pay them win or lose."
                        % ((Decimal(str(share)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP),
                           _num(COST_WARN * 100)))

    warnings = []
    lim = plan_limits(plan) or day_limits
    refusal = None
    if swing:
        try:
            on = day_label(datetime.date.fromisoformat(str(plan.get("date") or today)))
        except ValueError:
            on = "the buy day"
    if lim and swing:                    # the buy day's limits: a target over them can fill on a later day
        lo, hi, words = px(lim["lower"]), px(lim["upper"]), limits_words(lim)
        if not lim["lower"] - EPS <= ref <= lim["upper"] + EPS:
            refusal = ("the entry %s is outside the price limits for %s, %s to %s (%s): PSX does not take that "
                       "order that day." % (px(ref), on, lo, hi, words))
        elif target > lim["upper"] + EPS:
            warnings.append("The target %s is over the upper price limit %s (%s): it cannot fill on %s; it can on "
                            "a later day." % (px(target), hi, words, on))
        if stop < lim["lower"] - EPS:     # PSX trades at a limit, never past it (the rule of psx-exit)
            warnings.append("The stop %s is under the lower price limit %s: it cannot be hit on %s; it counts from "
                            "the next session." % (px(stop), lo, on))
        elif stop <= lim["lower"] + EPS:
            warnings.append("The stop %s is at the lower price limit %s: the price can trade at the limit, so the "
                            "stop can be hit on %s. At the limit there may be no buyers, so you may not get out."
                            % (px(stop), lo, on))
    elif swing:
        warnings.append("The price limits for %s are not in the plan, so the target was not checked against them "
                        "(psx-risk checks them)." % on)
    elif lim:
        lo, hi, words = px(lim["lower"]), px(lim["upper"]), limits_words(lim)
        if not lim["lower"] - EPS <= ref <= lim["upper"] + EPS:
            refusal = ("the entry %s is outside today's price limits %s to %s (%s): PSX does not take that "
                       "order today." % (px(ref), lo, hi, words))
        elif target > lim["upper"] + EPS:
            refusal = ("the target %s is above today's upper price limit %s (%s): it cannot fill today. "
                       "Make a new plan with psx-exit." % (px(target), hi, words))
        if stop < lim["lower"] - EPS:
            warnings.append("The stop %s is under today's lower price limit %s: the price cannot reach it "
                            "today, and at the limit there may be no buyers." % (px(stop), lo))
    else:
        warnings.append("Today's price limits are not in the plan, so the target was not checked against "
                        "them (psx-risk checks them).")
    min_rr = _f(c.get("min_reward_risk"))
    min_order = _f(c.get("min_order_pkr")) or 0.0
    if refusal:
        pass
    elif qty == 0:
        if bound[0] == "max loss":
            refusal = ("one share loses %s at the stop, costs included (entry %s minus stop %s, plus costs), "
                       "more than your %s max loss per trade. Skip this trade, or plan a closer stop with psx-exit."
                       % (money(max_loss_at_stop(ref, stop, 1, costs)), px(ref), px(stop), rs(cap_loss)))
        elif bound[0] == "gap limit":
            refusal = ("one share loses %s if the next session opens at its lower limit %s, costs included, more "
                       "than your %s gap limit (swing.max_gap_loss_pkr). Skip this trade."
                       % (money(gap_loss(ref, 1, costs)), px(gap_low), rs(gcap)))
        else:
            refusal = ("one share of %s at %s, with the buy costs, is more than your %s limit (%s)."
                       % (sym, px(ref), bound[0], limits[[l["name"] for l in limits].index(bound[0])]["how"]))
    elif min_rr and not meets_reward_risk(ref, stop, target, min_rr):
        # the same rule and the same price lookup as psx-risk, so its block is a fact here
        refusal = ("reward:risk %.2f (target %s, entry %s, stop %s) is under your minimum %s, so psx-risk "
                   "would block it. Skip it, or make a new plan with psx-exit."
                   % (rr, px(target), px(ref), px(stop), _num(min_rr)))
    elif value < min_order - 0.005:
        refusal = ("%s share%s of %s is %s, under your %s minimum order."
                   % (count(qty), "" if qty == 1 else "s", sym, money(value), rs(min_order)))

    if swing and owned > 0:
        warnings.append("You already hold %s in psx/portfolio.json. In swing style psx-risk blocks a second buy of a "
                        "stock you hold." % sym)
    elif swing and held > owned:
        warnings.append("A buy of %s from today has no fill yet (psx/state/broker.json). psx-risk blocks a second "
                        "buy of %s today." % (sym, sym))
    if swing:
        flat_by = None                   # a swing plan has no flat-by time: it holds to its exit-by day
    else:
        flat_by = plan.get("flat_by") or None
        day = plan.get("date") or today
        if not flat_by and c.get("flat_by"):
            flat_by = day_time(c["flat_by"], day, c) if day else str(c["flat_by"])
    s = {
        "symbol": sym, "side": "buy", "setup": plan.get("setup", ""), "date": plan.get("date"),
        "entry_time": plan.get("entry_time", ""), "entry_plan": round_half_up_tick(entry_plan),
        "entry": ref, "entry_from": ref_from, "stop": stop, "target": target,
        "risk_per_share": risk, "reward_risk": rr,
        "stop_label": plan.get("stop_label", ""), "target_label": plan.get("target_label", ""),
        "budget": budget, "account": account, "account_cash": start_cash, "holdings_value": held_all,
        "held_value": held, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer, "cash_available": available,
        "cash_from": cash_from, "max_loss_per_trade_pkr": cap_loss, "limits": limits, "qty": qty,
        "bound_by": bound[0], "value": value, "price_risk": price_risk, "buy_cost": buy, "sell_cost": sell,
        "costs": total, "max_loss": loss, "one_more": one_more, "costs_vs_risk": share,
        "cost_warning": cost_warning, "costs_used": costs, "day_limits": lim,
        "flat_by": flat_by, "order_type": "market" if market else "limit", "warnings": warnings,
        "refusal": refusal,
    }
    if swing:                            # day style keeps the v1 keys, and only them
        s.update({"style": "swing", "preview": preview, "entry_rule": plan.get("entry_rule"),
                  "exit_by": exits["exit_by"], "exit_by_time": exits["exit_by_time"],
                  "trend_sessions": exits["trend_sessions"],
                  "hold_sessions": exits["hold_sessions"], "gap_low": gap_low, "gap_loss": gap,
                  "gap_limit": gcap, "gap_more": gap_more, "held_before": owned, "waiting": wait})
    return s


def make_order(s):
    """The one proposed order (the SPEC section 7 shape, numbers as JSON numbers). PSX has no
    bracket orders, so the stop and the target ride along for psx-risk and psx-broker.
    why and plan are notes for people; they are not part of what you place."""
    if s["refusal"] or s["qty"] <= 0:
        return None
    o = {"symbol": s["symbol"], "side": "buy", "qty": int(s["qty"]), "type": s["order_type"]}
    if s["order_type"] == "limit":
        o["limit_price"] = s["entry"]
    o.update({"time_in_force": "day", "board": "REG", "stop_loss": s["stop"], "take_profit": s["target"]})
    swing = s.get("style") == "swing"
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or ("swing trade" if swing else "day trade"))
    bound = {"max loss": "%s max loss per trade" % rs(s["max_loss_per_trade_pkr"] or 0),
             "gap limit": "gap limit", "position cap": "position cap", "max order": "max order",
             "cash": "cash after the buffer"}[s["bound_by"]]
    if swing and s["bound_by"] == "gap limit":
        o["why"] = ("%s on %s: %d shares, bound by your %s gap limit (%s if the next session opens at the lower limit "
                    "%s, costs included; at the stop %d x %s = %s + %s costs = %s)"
                    % (setup, s["symbol"], s["qty"], rs(s["gap_limit"]), money(s["gap_loss"]), px(s["gap_low"]),
                       s["qty"], px(s["risk_per_share"]), money(s["price_risk"]), money(s["costs"]),
                       money(s["max_loss"])))
    else:
        o["why"] = ("%s on %s: %d shares, bound by your %s (%d x %s = %s at the stop + %s costs = %s, costs included)"
                    % (setup, s["symbol"], s["qty"], bound, s["qty"], px(s["risk_per_share"]), money(s["price_risk"]),
                       money(s["costs"]), money(s["max_loss"])))
    o["plan"] = {"entry": s["entry"], "stop": s["stop"], "target": s["target"],
                 "risk_per_share": s["risk_per_share"], "max_loss": s["max_loss"], "costs": s["costs"],
                 "setup": s["setup"], "entry_time": s["entry_time"], "flat_by": s["flat_by"]}
    if swing:                            # the swing order's plan: the shape of orders-swing.json (SPEC v2 2.5)
        o["plan"] = dict([("style", "swing")] + list(o["plan"].items()))
        o["plan"]["flat_by"] = None
        o["plan"].update({"exit_by": s["exit_by"], "exit_by_time": s["exit_by_time"],
                          "trend_sessions": s["trend_sessions"], "gap_loss": s["gap_loss"]})
    return o


def _day(text):
    """"2026-10-29" -> "Thu 2026-10-29" (day_label); anything else stays as it is."""
    try:
        return day_label(datetime.date.fromisoformat(str(text).strip()))
    except ValueError:
        return str(text)


def plan_report(s, order, notes=()):
    lines = []
    swing = s.get("style") == "swing"
    setup = SETUP_WORDS.get(s["setup"], s["setup"] or "setup")
    if swing:
        rule = (" (%s)" % s["entry_rule"]) if s.get("entry_rule") else ""
        lines.append("%s swing buy on %s, %s, entry %s%s" % (s["symbol"], _day(s["date"]), setup, px(s["entry_plan"]),
                                                             rule))
    else:
        lines.append("%s buy, %s, entry %s%s" % (s["symbol"], setup, px(s["entry_plan"]),
                                                (" at " + s["entry_time"]) if s["entry_time"] else ""))
    lines.append("Stop %s%s. Target %s%s." % (px(s["stop"]), (": " + s["stop_label"]) if s["stop_label"] else "",
                                             px(s["target"]),
                                             (": " + s["target_label"]) if s["target_label"] else ""))
    for n in list(notes) + list(s["warnings"]):
        lines.append(n)
    src = {"plan": "the plan's entry", "price": "the latest price, above the plan's entry",
           "limit": "your limit price"}[s["entry_from"]]
    lines.append("Sized at %s (%s). Risk %s a share (entry %s minus stop %s). Reward:risk %.2f."
                 % (px(s["entry"]), src, px(s["risk_per_share"]), px(s["entry"]), px(s["stop"]), s["reward_risk"]))
    lim = s["day_limits"]
    if lim and swing:
        lines.append("Price limits for %s: %s to %s (%s)." % (_day(s["date"]), px(lim["lower"]), px(lim["upper"]),
                                                            limits_words(lim)))
    elif lim:
        lines.append("Today's price limits: %s to %s (%s)." % (px(lim["lower"]), px(lim["upper"]), limits_words(lim)))
    if s["cash_from"] == "portfolio":
        lines.append("Account %s in psx/portfolio.json: cash %s plus holdings %s. Cash buffer %s%% = %s."
                     % (rs(s["account"]), rs(s["account_cash"]), rs(s["holdings_value"]),
                        _num(s["cash_buffer_pct"]), rs(s["cash_buffer"])))
    if swing and s.get("waiting"):                 # a buy filled in part shows only its rest (review F60)
        lines.append("Waiting buys today with no fill yet (psx/state/broker.json): %s. They come off the cash and "
                     "count as held until they fill or end."
                     % ", ".join("%s %s at %s (%s with costs%s)"
                                 % (count(w["qty"]), w["symbol"], px(w["entry_ref"]), money(w["pkr"]),
                                    "; %s of the %s are filled" % (count(w["filled"]), count(w["order_qty"]))
                                    if w.get("filled") and w.get("order_qty") else "")
                                 for w in s["waiting"]["rows"]))
    lines.append("")
    lines.append("Limits (whole shares; the smallest one wins):")
    width = max(len(l["how"]) for l in s["limits"])
    for l in s["limits"]:
        lines.append("  %-13s %-*s %7s shares%s" % (l["name"], width, l["how"], count(l["shares"]),
                                                    "  <- binds" if l["binds"] else ""))
    ml = [l for l in s["limits"] if l["name"] == "max loss"]
    if ml and ml[0]["no_costs"] != ml[0]["shares"]:
        lines.append("  Costs count: without them the max loss limit would be %s shares (%s / %s)."
                     % (count(ml[0]["no_costs"]), rs(ml[0]["pkr"]), px(ml[0]["per"])))
    lines.append("")
    if order is None:
        lines.append("No order: " + s["refusal"])
        lines.append("Education and tooling, not financial advice.")
        return "\n".join(lines)
    lines.append("Size: %s shares of %s = %s at %s." % (count(s["qty"]), s["symbol"], money(s["value"]),
                                                       px(s["entry"])))
    lines.append("Max loss at the stop: %s, costs included." % money(s["max_loss"]))
    lines.append("  price risk  %s x %s = %s" % (count(s["qty"]), px(s["risk_per_share"]), money(s["price_risk"])))
    lines.append("  costs       buy %s + sell at the stop %s = %s"
                 % (money(s["buy_cost"]), money(s["sell_cost"]), money(s["costs"])))
    if s["one_more"]:
        lines.append("  one more share (%s) would lose %s: over your %s"
                     % (count(s["one_more"]["qty"]), money(s["one_more"]["max_loss"]),
                        rs(s["max_loss_per_trade_pkr"])))
    if swing:                            # the gap loss (D55): held overnight, a share can open at its limit
        lines.append("Loss if it opens at the lower limit the next day: about %s, costs included%s. More days down "
                     "can lose more." % (money(s["gap_loss"]), (" (your gap limit %s)" % rs(s["gap_limit"]))
                                         if s["gap_limit"] is not None else ""))
        if s["gap_more"]:
            lines.append("  bound by your gap limit: one more share (%s) would lose %s on such an open: over your %s"
                         % (count(s["gap_more"]["qty"]), money(s["gap_more"]["gap_loss"]), rs(s["gap_limit"])))
    if s["cost_warning"]:
        lines.append(s["cost_warning"])
    lines.append("A stop is a plan, not a promise. PSX has no stop orders: you, or your broker's app, sell")
    lines.append("when the price gets there. In a fast market, or at the lower price limit, the sell can")
    lines.append("fill below %s." % px(s["stop"]))
    lines.append("")
    if swing and s["preview"]:
        lines.append("Preview of the order (nothing is written and nothing is placed):")
    else:
        lines.append("Proposed order (nothing has been placed):")
    how = ("limit %s" % px(order["limit_price"])) if order["type"] == "limit" else "market"
    lines.append("  buy %s %s, %s, day, REG board. Stop %s and target %s go with it."
                 % (count(order["qty"]), order["symbol"], how, px(order["stop_loss"]), px(order["take_profit"])))
    if order["type"] == "limit":
        lines.append("A limit order never pays more than %s. Free PSX prices are about 5 minutes late."
                     % px(order["limit_price"]))
    else:
        lines.append("A market order fills at the price of the moment, which can be far from %s: free PSX"
                     % px(s["entry"]))
        lines.append("prices are about 5 minutes late. PSX takes no market orders in the pre-open.")
    if swing:                            # no flat-by time: the swing exits
        if order["type"] == "limit":
            lines.append("PSX orders last one day: a limit buy that does not fill today ends at the close. "
                         "Do not chase it.")
        lines.append("Hold up to %s (%d sessions). Out earlier at the stop, the target or a trend break."
                     % (_day(s["exit_by"]), s["hold_sessions"]))
    elif s["flat_by"]:
        lines.append("Flat by %s Karachi time: anything still open then gets closed (psx-alerts rings, "
                     "psx-broker flatten)." % s["flat_by"])
    lines.append("")
    if swing and s["preview"]:
        lines.append("Preview for %s: size it again on that day, before you buy." % _day(s["preview"]))
    else:
        lines.append("Next: psx-risk checks it, then psx-broker shows you the ticket and asks yes or no.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- weights mode (v1)

def normalise(rows, blocked=(), top=None):
    """Drop blocked and non-positive weights, keep the top N, scale the rest to add up to 100."""
    blocked = blocked_set(blocked)
    dropped, keep, seen = [], [], set()
    for r in rows:
        if r["ticker"] in seen:
            raise SizeError("%s is listed twice in the weights" % r["ticker"])
        seen.add(r["ticker"])
        if r["ticker"] in blocked:
            dropped.append({"ticker": r["ticker"], "reason": "on your blocked list"})
        elif r["weight"] <= 0:
            dropped.append({"ticker": r["ticker"], "reason": "weight is zero or negative"})
        else:
            keep.append(dict(r))
    keep.sort(key=lambda r: -r["weight"])
    if top:
        for r in keep[top:]:
            dropped.append({"ticker": r["ticker"], "reason": "outside the top %d" % top})
        keep = keep[:top]
    total = sum(r["weight"] for r in keep)
    for r in keep:
        r["source_weight"] = r["weight"]
        r["weight"] = 100.0 * r["weight"] / total if total else 0.0
    return keep, dropped


def allocate(rows, investable, cap):
    """Exact (unrounded) rupees per ticker with the cap enforced.

    Proportional to weight; any share over the cap is pinned at the cap and
    the rest of the money is shared over the others, repeated until no share
    is over. Returns ({ticker: rupees}, set of capped tickers)."""
    alloc, capped = {}, set()
    active = [r for r in rows]
    money_left = investable
    while active:
        wsum = sum(r["weight"] for r in active)
        trial = {r["ticker"]: money_left * r["weight"] / wsum for r in active}
        over = [r for r in active if trial[r["ticker"]] > cap + EPS]
        if not over:
            alloc.update(trial)
            break
        for r in over:
            alloc[r["ticker"]] = cap
            capped.add(r["ticker"])
            money_left -= cap
        active = [r for r in active if r["ticker"] not in capped]
        if money_left <= EPS:
            for r in active:
                alloc[r["ticker"]] = 0.0
            break
    return alloc, capped


def round_to_unit(exact, capped, order, cap, unit):
    """Round to the unit (0.01: the paisa) and keep the total.

    The total placed is the exact total rounded DOWN to the unit, so it never
    spends money you do not have. Each slice is rounded to the nearest unit,
    then the difference (usually one unit) goes to the biggest share that is
    not capped, then the next, never pushing anyone over the cap."""
    def units(x):
        return int(math.floor(x / unit + EPS))

    cap_u = units(cap)
    total_u = units(sum(exact.values()))
    out = {}
    for t, v in exact.items():
        out[t] = cap_u if t in capped else int(math.floor(v / unit + 0.5 + EPS))
    diff = total_u - sum(out.values())
    takers = [t for t in order if t not in capped]
    guard = 0
    while diff != 0 and takers and guard < 100000:
        moved = False
        for t in takers:
            if diff == 0:
                break
            if diff > 0 and out[t] + 1 <= cap_u:
                out[t] += 1
                diff -= 1
                moved = True
            elif diff < 0 and out[t] > 0:
                out[t] -= 1
                diff += 1
                moved = True
        if not moved:
            break
        guard += 1
    return {t: round(u * unit, 2) for t, u in out.items()}


def whole_shares(slices, prices, order, cap, cash, costs):
    """Whole shares: the most each slice pays for with the buy's costs, then one more share
    at a time for the share furthest below its slice, while the cash lasts (the buy's
    costs included) and the cap holds (the cap is on the value)."""
    shares = {}
    for t in order:
        shares[t] = buy_shares(prices[t], min(slices[t], cap), costs) if slices[t] > 0 else 0
    left = cash - sum(spend(prices[t], shares[t], costs) for t in order)
    while True:
        best, gap, extra = None, None, None
        for t in order:
            p, q = prices[t], shares[t]
            more = spend(p, q + 1, costs) - spend(p, q, costs)
            if more > left + EPS or (q + 1) * p > cap + EPS:
                continue
            g = slices[t] - q * p
            if best is None or g > gap + EPS:
                best, gap, extra = t, g, more
        if best is None:
            break
        shares[best] += 1
        left -= extra
    return shares


def weights_prices(tickers, prices_path=None, data_root=None):
    """A price for each ticker -> ({SYM: price}, {SYM: where it came from}): the prices file
    first, then the saved screener page in the data folder, then saved company pages.
    Weights mode splits a budget; it does not time a trade, so a price need not be fresh."""
    prices, where = {}, {}
    for t, p in load_prices(prices_path).items():
        if t in tickers:
            prices[t], where[t] = p, prices_path
    if data_root and os.path.isdir(data_root) and any(t not in prices for t in tickers):
        path = os.path.join(data_root, "screener.html")
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as fh:
                rows = parse_screener(fh.read())["rows"]
            for t in tickers:
                p = _f((rows.get(t) or {}).get("price"))
                if t not in prices and p and p > 0:
                    prices[t], where[t] = p, path
        for t in tickers:
            q = page_quote(data_root, t) if t not in prices else None
            if q:
                prices[t], where[t] = q["price"], q["source"]
    return prices, where


def size(rows, cfg, budget=None, prices=None, top=None, where=None):
    """Weights mode. Pure function: weights + rules + prices -> the split in whole shares (a dict)."""
    budget = _f(budget if budget is not None else (cfg.get("budget") or 0)) or 0.0
    if budget <= 0:
        raise SizeError("no budget: pass --budget or set \"budget\" in psx/config.json")
    buf_pct = _f(cfg.get("cash_buffer_pct", DEFAULTS["cash_buffer_pct"]) or 0)
    cap_pct = _f(cfg.get("max_position_pct", DEFAULTS["max_position_pct"]))
    min_order = _f(cfg.get("min_order_pkr", DEFAULTS["min_order_pkr"]) or 0) or 0.0
    if buf_pct is None or not 0 <= buf_pct < 100:
        raise SizeError("cash_buffer_pct must be between 0 and 100")
    if cap_pct is None or not 0 < cap_pct <= 100:
        raise SizeError("max_position_pct must be above 0 and at most 100")
    costs = cost_settings(cfg)
    check_costs(costs)
    prices, where = dict(prices or {}), dict(where or {})

    keep, dropped = normalise(rows, cfg.get("blocked"), top)
    if not keep:
        raise SizeError("nothing to size: no weights left after the blocked list")
    missing = [r["ticker"] for r in keep if not prices.get(r["ticker"])]
    if missing:
        raise SizeError("whole shares need a price for: %s. Save %s as psx/data/screener.html, or put the "
                        "prices in psx/prices.json (psx-alerts: alerts.py quote %s --write)"
                        % (", ".join(missing), SCREENER_URL, " ".join(missing)))

    cash_buffer = round(budget * buf_pct / 100.0, 2)
    investable = round(budget - cash_buffer, 2)
    cap = round(budget * cap_pct / 100.0, 2)
    while True:
        now_rows = [dict(r) for r in keep]
        tot = sum(r["source_weight"] for r in now_rows)
        for r in now_rows:
            r["weight"] = 100.0 * r["source_weight"] / tot
        exact, capped = allocate(now_rows, investable, cap)
        order = [r["ticker"] for r in sorted(now_rows, key=lambda r: -exact[r["ticker"]])]
        slices = round_to_unit(exact, capped, order, cap, 0.01)
        shares = whole_shares(slices, prices, order, cap, investable, costs)
        value = dict((t, round(shares[t] * prices[t], 2)) for t in order)
        small = [r for r in now_rows if value[r["ticker"]] < min_order - EPS]
        if not small:
            break
        worst = min(small, key=lambda r: (r["source_weight"], r["ticker"]))
        t = worst["ticker"]
        if shares[t] == 0:
            reason = "one share costs %s, more than its slice; it cannot be bought in whole shares" % rs(prices[t])
        else:
            reason = "its slice (%s) is under your %s minimum order" % (rs(value[t]), rs(min_order))
        dropped.append({"ticker": t, "reason": reason})
        keep = [r for r in keep if r["ticker"] != t]
        if not keep:
            raise SizeError("every slice is under your minimum order; raise the budget or hold fewer stocks")

    positions = []
    for r in sorted(now_rows, key=lambda r: -r["weight"]):
        t = r["ticker"]
        positions.append({"ticker": t, "name": r["name"], "weight": round(r["weight"], 2),
                          "source_weight": r["source_weight"], "slice": slices[t], "price": prices[t],
                          "price_from": where.get(t), "shares": shares[t], "value": value[t],
                          "buy_cost": leg_cost(prices[t], shares[t], costs), "capped": t in capped})
    invested = round(sum(p["value"] for p in positions), 2)
    buy_costs = round(sum(p["buy_cost"] for p in positions), 2)
    return {
        "budget": budget, "cash_buffer_pct": buf_pct, "cash_buffer": cash_buffer,
        "investable": investable, "max_position_pct": cap_pct, "cap": cap,
        "min_order_pkr": min_order, "whole_shares": True,
        "positions": positions, "invested": invested, "buy_costs": buy_costs,
        "idle": round(investable - invested - buy_costs, 2), "dropped": dropped,
    }


def orders_from(result, source="", market=False):
    """Weights mode: proposed buys, one per position, whole shares; a limit at the price it
    sized at (or a market order with --market). No stop: these are not day trades."""
    out = []
    for p in result["positions"]:
        if p["shares"] <= 0:
            continue
        why = "%.2f%% of the split" % p["weight"]
        if source:
            why = "%s, weights from %s" % (why, source)
        if p["capped"]:
            why += "; capped at %s%% of the %s budget" % (_num(result["max_position_pct"]), rs(result["budget"]))
        o = {"symbol": p["ticker"], "side": "buy", "qty": int(p["shares"]), "type": "market" if market else "limit"}
        if not market:
            o["limit_price"] = p["price"]
        o.update({"time_in_force": "day", "board": "REG", "why": why})
        out.append(o)
    return out


def report(result, orders, source=""):
    """Weights mode table."""
    r = result
    lines = []
    if source:
        lines.append("Weights from: %s" % source)
    lines.append("Budget %s, cash buffer %s%% = %s, investing %s, cap %s%% = %s per stock"
                 % (rs(r["budget"]), _num(r["cash_buffer_pct"]), rs(r["cash_buffer"]),
                    rs(r["investable"]), _num(r["max_position_pct"]), rs(r["cap"])))
    lines.append("")
    lines.append("%-8s %-24s %7s %10s %8s %15s  note" % ("ticker", "name", "weight", "price", "shares", "value"))
    for p in r["positions"]:
        row = "%-8s %-24s %6.2f%% %10s %8s %15s" % (p["ticker"], (p["name"] or "")[:24], p["weight"], px(p["price"]),
                                                   count(p["shares"]), money(p["value"]))
        if p["capped"]:
            row += "  capped at %s%%" % _num(r["max_position_pct"])
        lines.append(row)
    for label, v in (("invested", r["invested"]), ("buy costs", r["buy_costs"]), ("cash buffer", r["cash_buffer"]),
                     ("left idle", r["idle"])):
        lines.append("%-8s %-24s %7s %10s %8s %15s" % ("", label, "", "", "", money(v)))
    if r["idle"] >= 1:
        lines.append("")
        if all(p["capped"] for p in r["positions"]):
            why = "every stock is at its cap; add stocks or raise max_position_pct"
        else:
            why = "whole shares do not divide evenly"
        lines.append("Left idle %s: %s." % (money(r["idle"]), why))
    froms = {}
    for p in r["positions"]:
        froms.setdefault(p["price_from"] or "--prices", []).append(p["ticker"])
    for f, ts in froms.items():
        lines.append("Prices for %s: %s." % (", ".join(ts), f))
    for d in r["dropped"]:
        lines.append("Dropped %s: %s" % (d["ticker"], d["reason"]))
    lines.append("")
    lines.append("Proposed orders (nothing has been placed):")
    for o in orders:
        how = ("limit %s" % px(o["limit_price"])) if o["type"] == "limit" else "market"
        lines.append("  buy %s %s, %s, day, REG board" % (count(o["qty"]), o["symbol"], how))
    lines.append("")
    lines.append("These buys have no stop. psx-risk blocks them while \"require_stop\" is true, and it applies "
                 "your day-trade limits to every buy.")
    lines.append("Next: psx-risk checks every order, then psx-broker shows each ticket and asks you yes or no.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


# ---------------------------------------------------------------- orders file

def write_orders(path, orders, cfg, source="", now_utc=None):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    doc = {
        "created_by": "psx-budget",
        "created_at": utc_iso(now_utc or utc_now()),
        "status": "proposed",
        "paper": cfg.get("paper", True) is not False,     # only JSON false is live (as in psx-broker)
        "note": ("Proposals only. Nothing in this pack places an order: psx-risk checks each one, psx-broker "
                 "shows a ticket after your yes, and you place it yourself in your broker's app."),
        "source": source,
        "orders": orders,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------- main

def clock(text):
    """--now -> naive UTC (parse_now: a time with no offset is Karachi time), with a plain error."""
    try:
        return parse_now(text)
    except ValueError as e:
        raise SizeError(str(e))


def price_notes(sym, plan, quotes, now, places):
    """(a fresh price for a market order or None, the notes that say what was used)."""
    q, old = pick_fresh(quotes, now)
    entry = _f(plan.get("entry")) or 0
    if q:
        seen = "%s last %s (as of %s Karachi time, %s)" % (sym, px(q["price"]), to_pkt(q["time"]).strftime("%H:%M"),
                                                           q["source"])
        if round_half_up_tick(q["price"]) > round_half_up_tick(entry):
            return q["price"], ["%s is above the plan's entry %s, so the risk is bigger: sized at %s."
                                % (seen, px(entry), px(q["price"]))]
        return q["price"], ["%s is not above the plan's entry %s: sized at the plan's entry." % (seen, px(entry))]
    if old:
        when = ("from " + to_pkt(old["time"]).strftime("%Y-%m-%d %H:%M") + " Karachi time") if old["time"] else "undated"
        return None, ["%s in %s is %s: not used (older than %d minutes). Sized at the plan's entry."
                      % (sym, old["source"], when, FRESH_MINUTES)]
    return None, ["No price for %s in %s: sized at the plan's entry." % (sym, " or ".join(places))]


def run_plan(a, cfg):
    now = clock(a.now)
    today = to_pkt(now).date().isoformat()
    plan = read_json(a.plan, "plan (run psx-exit first, or use --weights for a split)")
    limit = None
    if a.limit not in (None, "plan"):
        if a.market:
            raise SizeError("pick one: --limit PRICE or --market")
        limit = _f(a.limit)
        if limit is None or limit <= 0:
            raise SizeError("--limit wants a price, like --limit 100.60")
    sym = str(plan.get("symbol") or "").strip().upper() if isinstance(plan, dict) else ""
    style = trade_style(cfg)
    swing = style == "swing"
    preview = False
    if isinstance(plan, dict):
        kind = plan_style(plan)
        held = kind == "day" and swing and holds(a.portfolio, sym)     # the style line never says to plan it again
        mix = style_refusal(kind, style, sym, plan.get("date"), today, held)    # first: styles do not mix (D31)
        if mix:
            raise SizeError(mix)
        if swing:                                             # a plan for the next session: the evening before
            nxt = add_sessions(datetime.date.fromisoformat(today), 1, cfg).isoformat()
            preview = str(plan.get("date") or "") == nxt
    notes, price, day_limits = [], None, None
    ppath = a.prices or (PRICES_PATH if os.path.exists(PRICES_PATH) else None)
    delay = _f(cfg.get("data_delay_minutes")) or 0
    if sym and (a.market or plan_limits(plan) is None):
        quotes = quotes_for(sym, now, ppath, a.data, delay)
        if a.market:
            price, notes = price_notes(sym, plan, quotes, now, [ppath or PRICES_PATH, a.data])
            if price is None:       # psx-risk blocks a market buy with no fresh price: the plan's entry can be old
                raise SizeError("No order: a market order needs a fresh price to measure its risk, and the plan's "
                                "entry %s can be hours old. %s Get a fresh price (psx-alerts: alerts.py quote %s "
                                "--write), or drop --market for a limit order at the plan's entry."
                                % (px(_f(plan.get("entry")) or 0), notes[-1].replace(" Sized at the plan's entry.", "")
                                   .replace(": sized at the plan's entry.", "."), sym))
        if plan_limits(plan) is None and not preview:         # today's quotes are not the buy day's limits
            day_limits = quote_limits(quotes, now)
    cash, holdings = None, None
    ledger = bool(a.portfolio and os.path.exists(a.portfolio))
    if ledger:
        cash, holdings, more = account_from(read_json(a.portfolio, "portfolio"), now, ppath, a.data, delay)
        notes += more
        if a.budget is not None:
            notes.append("--budget is not used: psx/portfolio.json is your account (cash plus holdings), "
                         "as psx-risk counts it.")
    waiting = None
    if swing:                            # the ledger and today's waiting buys (D57), as psx-risk counts them
        state = read_state(a.state)
        if not ledger:
            if has_buys(state):
                raise SizeError("A swing account needs its ledger: record your fills with psx-broker (ticket.py "
                                "fill), so open positions are not counted as cash.")
            notes.append("No psx/portfolio.json yet: sized as if nothing is open. Record each fill with psx-broker.")
        waiting = waiting_buys(state, today)
    s = size_trade(plan, cfg, a.budget, limit, price, cash, today, a.market, day_limits, holdings, waiting)
    order = make_order(s)
    if order and a.write and s.get("preview"):
        raise SizeError("--write is refused for a preview: the plan is for %s, the next session. psx/orders.json "
                        "must be made on the buy day, as psx-risk checks the order's date. Run size.py --write on "
                        "that day, before you buy." % _day(s["preview"]))
    if order and a.write:
        src = "psx/plan.json (%s %s %s)" % (s["symbol"], s["setup"] or "plan", s["date"] or today)
        write_orders(a.write, [order], cfg, src, now)
    if a.json:
        print(json.dumps({"sizing": s, "orders": [order] if order else [], "notes": notes}, indent=2))
    else:
        print(plan_report(s, order, notes))
        if order and a.write:
            print("Wrote 1 proposed order to %s" % a.write)
    return 0 if order else 1


def run_weights(a, cfg):
    if a.limit not in (None, "plan"):
        raise SizeError("--limit PRICE is for one trade; weights mode puts each limit at the price it sized at")
    source = ""
    if a.weights:
        rows = parse_weights(weights_from_arg(a.weights))
    else:
        data = read_json(a.targets, "weights file")
        rows = parse_weights(data)
        if isinstance(data, dict):
            source = data.get("source", "")
    ppath = a.prices or (PRICES_PATH if os.path.exists(PRICES_PATH) else None)
    prices, where = weights_prices([r["ticker"] for r in rows], ppath, a.data)
    result = size(rows, cfg, a.budget, prices, a.top, where)
    orders = orders_from(result, source, a.market)
    if a.write:
        write_orders(a.write, orders, cfg, source, clock(a.now))
    if a.json:
        print(json.dumps({"result": result, "orders": orders}, indent=2))
    else:
        print(report(result, orders, source))
        if a.write:
            print("Wrote %d proposed orders to %s" % (len(orders), a.write))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Size a day trade or a swing trade to your budget and max loss, "
                                             "costs included (psx-budget).")
    ap.add_argument("--plan", default=PLAN_PATH, help="the trade plan (default psx/plan.json)")
    ap.add_argument("--config", help="rules (default psx/config.json); a file you name must be there")
    ap.add_argument("--budget", type=float,
                    help="override the budget in the config, in rupees (used when there is no psx/portfolio.json)")
    ap.add_argument("--limit", nargs="?", const="plan", metavar="PRICE",
                    help="a limit order at PRICE (the default is a limit order at the plan's entry)")
    ap.add_argument("--market", action="store_true",
                    help="a market order, sized at the higher of the plan's entry and a fresh price")
    ap.add_argument("--prices", help="prices JSON (default psx/prices.json if it exists)")
    ap.add_argument("--data", default=DATA_PATH,
                    help="the PSX data folder: the tape and saved pages (default psx/data)")
    ap.add_argument("--offline", action="store_true",
                    help="accepted like in the other tools: size.py never uses the network")
    ap.add_argument("--portfolio", default=PORTFOLIO_PATH,
                    help="your account: if it exists, its cash plus holdings set the cap and the buffer")
    ap.add_argument("--state", default=STATE_PATH,
                    help="swing style: what psx-broker recorded (default psx/state/broker.json); today's buys "
                         "with no fill yet come off the cash")
    ap.add_argument("--now", help="pretend it is this Karachi time, e.g. \"2026-10-29 10:15\"")
    ap.add_argument("--write", nargs="?", const=ORDERS_PATH, metavar="PATH",
                    help="write the proposed order (default psx/orders.json)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the report")
    g = ap.add_argument_group("weights mode (split a budget across a list)")
    g.add_argument("--weights", help="inline weights: OGDC=30,HBL=25")
    g.add_argument("--targets", help="a weights file: {\"weights\": [{\"ticker\", \"weight\"}]}")
    g.add_argument("--top", type=int, help="keep only the N biggest weights")
    a = ap.parse_args(argv)
    try:
        cfg = load_config(a.config or CONFIG_PATH, required=a.config is not None)
        if a.weights or a.targets:
            return run_weights(a, cfg)
        if a.top:
            raise SizeError("--top works with --weights or --targets")
        return run_plan(a, cfg)
    except (SizeError, ValueError) as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
