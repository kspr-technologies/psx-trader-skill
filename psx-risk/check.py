#!/usr/bin/env python3
"""Pre-trade risk check for psx-risk (Pakistan Stock Exchange). Standard library only.

Reads the proposed orders (psx/orders.json) and checks every one against your
rules (psx/config.json), your account (psx/portfolio.json), prices
(psx/prices.json), what was placed today (psx/state/broker.json) and today's
PSX price limits. Each order gets PASS or BLOCK with the reasons. Orders are
checked in file order as if each PASS had filled, so buys that are fine one by
one but break a limit together are caught.

    python3 check.py                                 # the psx/ files, writes psx/state/risk.json
    python3 check.py --now "2026-10-29 10:15"        # pin the clock (Karachi time)
    python3 check.py --orders psx/orders.json --json
    python3 check.py --no-write                      # check without recording the verdicts
    python3 check.py --offline --data DIR            # PSX data from DIR only, no network

Network: none by default. Only when psx/config.json says "psx_site": true (and
never with --offline or --now) it may GET a company page from the PSX site, for
today's price limits, at least PAUSE seconds apart. Then the site comes first,
and the saved page is the fallback.

Entries and exits. An ENTRY opens or adds to a position (a buy, or a sell of
more than you hold). An EXIT only cuts what you hold. Getting out must always
be possible: an exit is checked for format and blank sales only (and a future,
index, GEM board share or debt, which are not on the REG board); every other
rule turns into a warning for it.

What blocks an order (every order):

  format        no symbol; side not buy or sell; qty not a whole number above 0;
                type not limit or market; a limit order without a limit_price on
                the Rs 0.01 tick; time_in_force not day; board not REG; a market
                order in the pre-open; a field PSX does not have (notional,
                order_class, stop_price, extended_hours)
  asset         not an ordinary PSX share: a future (OGDC-NOV), an ETF, debt, an
                index, a fund, a right or a preference share
  short         a sell of more than you hold. PSX bans blank sales, so this blocks
                whatever "allow_short" says

What blocks an entry (on top):

  universe      the symbol is not in psx/universe.txt (when that file is there)
  suspended     its company page says SUSPENDED
  blocked       a symbol on your "blocked" list
  size          under min_order_pkr, over max_order_pkr
  position      the stock would be over max_position_pct of the account
  cash          the buy and its costs would dip into your cash buffer (no margin)
  stop          no stop while "require_stop": true (the order's stop_loss or the
                plan's stop); a stop on the wrong side of the entry
  loss          the max loss at the stop, costs included, over
                max_loss_per_trade_pkr. The entry is the limit price for a limit
                order, else the HIGHER of the plan's entry and a fresh price (15
                minutes old or less by its data time: psx/prices.json, else
                today's tape, else the saved company page)
  price         a market order with no fresh price and no plan entry
  reward:risk   (target - entry) / (entry - stop) under min_reward_risk, compared
                exactly in paisa (meets_reward_risk)
  daily loss    the max losses of today's entries plus this one over
                max_daily_loss_pkr (win or lose, each counts in full)
  trades        max_trades_per_day entries already placed today
  window        before no_entries_before or after no_entries_after (Karachi time,
                moved to the day's hours by config_time), or the market is not
                open (closed, pre-open, the opening match, the Friday break).
                A null time switches off only that time, never the market check
  date          psx/orders.json was made on another day (created_at)
  limits        a limit price or entry outside today's price limits; a target
                above the upper limit (it cannot fill today)
  duplicate     the same symbol and side twice in this batch, or already placed
                today (psx/state/broker.json: a yes until it is skipped or failed,
                a placed order, or a fill recorded by hand)
  daily limit   max_orders_per_day orders placed today

Swing style ("style": "swing" in psx/config.json; "day" is the default). On top, for
a buy (an exit is never blocked by these):

  style         a day plan, or a buy with no plan (a day folder: a swing plan). A
                psx-invest plan ("style": "invest") passes in both styles
  window        swing_window: PSX open past the opening match, or the pre-open for
                a limit order. no_entries_before and no_entries_after are day rules
  open          you already hold the stock: one swing plan a stock
  fill          a buy approved or placed on an earlier day has no fill, and no
                "expired", "skipped" or "cancelled" record
  positions     what you hold, today's buys with no fill yet, earlier passes in this
                batch and this one, over swing.max_open_positions
  open risk     the max loss at the stop of each of those, costs included, over
                swing.max_open_risk_pkr (null: max_daily_loss_pkr)
  gap           the loss if the next session opens at the lower price limit, costs
                included (gap_loss), over swing.max_gap_loss_pkr (null: 3 x
                max_loss_per_trade_pkr; 0: a Rs 0 limit, as in psx-budget)
  cash          today's buys with no fill yet come off the cash first, and count as
                held in the account total (a psx-invest order gets this in both styles)
  ex-date       today's LDCP (the company page, or psx/prices.json) is not the LDCP
                the plan was made on (plan_ldcp: its last close). An ex-date, a split
                or days with no trade moved it, so the plan's entry and stop are on old
                prices. A stop at or over today's LDCP only warns

In swing a target over the upper limit is a note, not a block, and every sell pays
commission (trade_costs). A stop-limit sell ("type": "stop_limit", trigger_price and
limit_price) of shares you hold is an exit; its trigger must be under the price now.
A sell of shares that a sell placed today already covers is a blank sale: blocked
until that sell is recorded cancelled (ticket.py result --fingerprint FP --status
cancelled) or filled. An order of today filled in part keeps its other shares open
until the close, or until a "cancelled" or "expired" record of that day closes it.
A ledger stop under its "stop_at_buy" warns (lowered by hand). A ticket.py stop --scale
record counts only when the data shows a bonus, rights or split ex-date for it
(checked_scales: the closing files, or psx-alerts' record in psx/state/alerts.json).
With paper on, a ledger that holds or may hold real shares warns (real_ledger): psx-broker
records no paper fill in it.

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to psx/state/risk.json with a fingerprint of each order and
"checked_at" (the --now time, UTC), so psx-broker only shows an order as ready
if THIS exact order passed today, and asks for a new check when it is old.
"""

import argparse
import datetime
import gzip
import hashlib
import html
import io
import json
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_pkr": 400000,
    "min_order_pkr": 5000, "max_loss_per_trade_pkr": 5000, "max_daily_loss_pkr": 15000,
    "max_trades_per_day": 3, "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:45", "no_entries_after": "15:00", "flat_by": "15:20", "style": "day",
    "swing": {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20, "trend_exit_sessions": 10,
              "entry_buffer_pct": 1.0, "max_open_positions": 3, "max_open_risk_pkr": None,
              "max_gap_loss_pkr": None, "stop_limit_gap_pct": 1.0},
    "paper": True, "blocked": [], "allow_options": False, "allow_margin": False, "allow_short": False,
    "psx_site": False, "data_delay_minutes": 5,
}
ORDER_FIELDS = ("symbol", "side", "qty", "type", "limit_price", "time_in_force", "board", "stop_loss", "take_profit",
                "trigger_price")
TYPES = ("limit", "market", "stop_limit")     # stop_limit: in swing, a sell of shares you hold only
PRICE_FIELDS = ("limit_price", "stop_loss", "take_profit", "trigger_price")
NOT_AT_PSX = {      # fields of other markets' orders: PSX has no such thing
    "notional": "PSX orders are in whole shares: give qty, not notional",
    "order_class": "PSX has no bracket or OCO orders: the stop and target stay in the plan",
    "stop_price": "PSX has no stop orders: put the stop in stop_loss",
    "trail_price": "PSX has no trailing stop orders",
    "trail_percent": "PSX has no trailing stop orders",
    "extended_hours": "PSX has no extended hours",
}
INDEX_CODES = ("ACI", "ALLSHR", "BKTI", "HBLTTI", "JSGBKTI", "JSMFI", "KMI30", "KMIALLSHR", "KSE100", "KSE100PR",
               "KSE30", "MII30", "MZNPI", "NBPPGI", "NITPGI", "OGTI", "PSXDIV20", "UPP9")
KIND_WORDS = {
    "gem_equity": "a GEM board share", "non_voting": "a non-voting share", "preference": "a preference share",
    "right": "a rights letter", "etf": "an ETF (it trades in lots of 500 units)", "modaraba": "a modaraba",
    "reit": "a REIT", "closed_end_fund": "a closed-end fund", "debt": "a debt security (a bond or sukuk)",
    "stock_future": "a stock futures contract", "index_future": "an index futures contract",
    "index": "an index, not something you can buy",
}
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,12}$")
DEBT_RE = re.compile(r"^P\d{2}[A-Z]{3}\d{6}$|TFC\d*$|SUK\d*$")
CENT = 0.005
FRESH_MINUTES = 15
MATCH_MINUTES = 2           # the opening match after each pre-open: no new orders
BREAK_WARN_MINUTES = 30     # warn when an entry is this close to the Friday break
WIDTH = 99                  # the report wraps long lines here
REF_WORDS = {"plan": "the plan's entry", "price": "the latest price", "limit": "the limit price",
             "stale price": "an old price", "stop limit": "the stop-limit's limit price"}
STILL_COUNTS = ("approved", "placed")   # a yes the user may have placed in the app counts as placed
CLOSES_OLD = ("expired", "cancelled", "skipped")  # what ticket.py result --fingerprint records for an older order
ORDER_WORDS ={"limit": "limit", "market": "market", "stop_limit": "stop-limit", "market_to_limit": "market-to-limit"}
GAP_NOTE = "A gap can open past a stop: a loss can be bigger than its max loss."   # swing, once a report
SPLIT_AT = 0.10     # a no-tag LDCP change is a split only 10% or more away from 1 (as psx-alerts)
FACTOR_SLACK = 0.0005   # a scale may be this much under its ex-date's factor (factors have 4 decimals; ticket.py)
TOOL_KEYS = ("cash", "positions", "real_holdings")     # the only top keys the tools write in psx/portfolio.json
# kinds that do not trade on the REG board: even the exit of a holding of one is blocked
NOT_REG = ("stock_future", "index_future", "index", "gem_equity", "debt")
HALT_NOTE = ("A market halt at PSX (the KSE-30 index 5% up or down for 5 minutes) cancels every open order. "
             "Delayed data cannot show a halt: after one, check your orders in your broker app.")


class RiskError(Exception):
    pass


# ---------------------------------------------------------------- copied from tests/psx_core.py
# Canonical helpers, copied verbatim (tests/test_repo.py checks it). Never edit them here.

UA = "Mozilla/5.0 (compatible; psx-trader-skill/1.0)"
PSX = "https://dps.psx.com.pk"
PAUSE = 1.5         # seconds between two requests to the PSX site, at least
SLEEP = time.sleep  # tests swap this out
KARACHI_HOURS = 5   # Karachi time is UTC+5 all year: no daylight saving
SITE_OFF = ('The PSX site is off for this tool: save %s with your browser, or set "psx_site": true '
            'in psx/config.json if PSX has given you permission.')
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
RAMADAN_PROVISIONAL = {2027}
CALENDAR_YEARS = (2026, 2027)
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
FUTURE_RE = re.compile(r"^([A-Z0-9]+)-(C?)(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)([A-Z]?\d*)$")
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name


# ---------------------------------------------------------------- time (Karachi time is UTC+5 all year)

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


def clock_label(t):
    """datetime or time -> "10:15 AM"."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    """date -> "Thu 2026-10-29" (English day names, whatever the machine's language)."""
    if isinstance(d, datetime.datetime):
        d = d.date()
    return "%s %s" % (("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()], d.isoformat())


# ---------------------------------------------------------------- PSX calendar

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


def calendar_note(d, cfg=None):
    """A plain note when the calendar for weekday d is not sure, else "": a year with no
    holiday list here, an expected moon holiday on or next to d, or expected Ramadan
    dates (on a trading day). Days the config's "holidays" or "open_days" name are
    taken as checked."""
    c = cfg if isinstance(cfg, dict) else {}
    is_open = trading_day(d, c)[0]      # this also checks the config's holidays and open_days
    if d.weekday() >= 5:
        return ""
    mine = set()
    for k in ("holidays", "open_days"):
        v = c.get(k) or []
        mine |= set(datetime.date.fromisoformat(str(x).strip()).isoformat() for x in ([v] if isinstance(v, str) else v))
    notes = []
    if d.year not in CALENDAR_YEARS:
        notes.append('The PSX holiday list for %d is not in this tool, so only weekends count as closed: '
                     'check PSX notices and add closures under "holidays" in psx/config.json.' % d.year)
    key = d.isoformat()
    one = datetime.timedelta(days=1)
    near = [x.isoformat() for x in (d - one, d + one)]
    if key in PSX_PROVISIONAL and key not in mine:
        notes.append("%s is an expected PSX holiday (%s): moon dates can move, so check PSX notices."
                     % (key, PSX_HOLIDAYS.get(key, "a moon date")))
    elif key not in PSX_HOLIDAYS and key not in mine and any(x in PSX_PROVISIONAL and x not in mine for x in near):
        notes.append("A moon holiday is expected next to %s and can move by a day: check PSX notices." % key)
    rc = c.get("ramadan")
    own = isinstance(rc, dict) and str(d.year) in (str(rc.get("start"))[:4], str(rc.get("end"))[:4])
    r = ramadan_range(d.year, c)
    if d.year in RAMADAN_PROVISIONAL and not own and r and is_open:
        if r[0] <= d <= r[1]:
            notes.append('Ramadan hours are used: the %d Ramadan dates are expected ones, so check PSX notices '
                         '(fix them under "ramadan" in psx/config.json).' % d.year)
        elif r[0] - one <= d <= r[1] + one:
            notes.append("Ramadan may start or end a day off the expected dates: check PSX notices.")
    return " ".join(notes)


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


# ---------------------------------------------------------------- the day's hours

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


def market_state(now_pkt, cfg=None):
    """The REG board at a naive Karachi time -> {"state", "kind", "reason", "open_at", "close_at"}.

    state is "closed", "pre-open" (the 15 minutes before each segment's grid start:
    order entry, limit orders only, no trades), "open" (from the grid start to the
    segment end; trading itself starts 2 minutes after the grid start, after the
    opening match), "break" (Friday 12:00 to 14:15) or "post-close" (5 to 20 minutes
    after the last close: trades only at the closing price, so the tools treat it as
    closed). kind is session_kind(). open_at is the start of the next segment today,
    close_at the end of the segment in progress (or of the next one); else None."""
    d = now_pkt.date()
    kind = session_kind(d, cfg)
    out = {"state": "closed", "kind": kind, "reason": "", "open_at": None, "close_at": None}
    if kind is None:
        out["reason"] = "closed all day (%s)" % trading_day(d, cfg)[1]
        return out
    segs = [(datetime.datetime.combine(d, a), datetime.datetime.combine(d, b)) for a, b in session(d, cfg)]
    pre = datetime.timedelta(minutes=15)
    later = [s for s, _ in segs if s > now_pkt]
    for i, (s, e) in enumerate(segs):
        if s - pre <= now_pkt < s:
            out.update(state="pre-open", open_at=s, close_at=e,
                       reason="pre-open: limit orders only, nothing trades until the opening match at %s PKT"
                       % clock_label(s))
            return out
        if s <= now_pkt < e:
            more = (", then the Juma break to %s PKT" % clock_label(segs[i + 1][0])) if i + 1 < len(segs) else ""
            out.update(state="open", open_at=later[0] if later else None, close_at=e,
                       reason="open until %s PKT%s" % (clock_label(e), more))
            return out
    last = segs[-1][1]
    five, twenty = datetime.timedelta(minutes=5), datetime.timedelta(minutes=20)
    if now_pkt < segs[0][0]:
        out.update(open_at=segs[0][0], close_at=segs[0][1],
                   reason="closed: the pre-open starts at %s PKT" % clock_label(segs[0][0] - pre))
    elif now_pkt < last:
        nxt = [(s, e) for s, e in segs if s > now_pkt][0]
        out.update(state="break", open_at=nxt[0], close_at=nxt[1],
                   reason="Juma break: the pre-open starts at %s PKT and trading again at %s PKT"
                   % (clock_label(nxt[0] - pre), clock_label(nxt[0])))
    elif now_pkt < last + five:
        out["reason"] = ("closed: the session ended at %s PKT; the post-close session (closing price only) "
                         "runs %s to %s" % (clock_label(last), clock_label(last + five), clock_label(last + twenty)))
    elif now_pkt < last + twenty:
        out.update(state="post-close",
                   reason="post-close session: trades only at the closing price (the tools treat it as closed)")
    else:
        out["reason"] = "closed for the day: the session ended at %s PKT" % clock_label(last)
    return out


# ---------------------------------------------------------------- prices and rules

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


def money(x, decimals=2):
    """Rupees for people: "Rs 1,234.56" or "-Rs 1,234.56"; decimals=0 gives "Rs 5,000"."""
    try:
        v = Decimal(str(x)).quantize(Decimal(1).scaleb(-int(decimals)), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("not an amount of money: %r" % (x,))
    if not v.is_finite():
        raise ValueError("not an amount of money: %r" % (x,))
    return "%sRs %s" % ("-" if v < 0 else "", format(abs(v), ",f"))


# ---------------------------------------------------------------- costs

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


# ---------------------------------------------------------------- PSX files

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


def read_mkt_summary(raw):
    """A PSX closing file (bytes of mkt_summary/YYYY-MM-DD.Z: a zip with closing11.lis,
    gzip before 2019, or the bare .lis) -> rows: {date (datetime.date), symbol, sector,
    short_name, open, high, low, close, volume (int), ldcp, flags (["XD"], ...)}.

    CLOSE is the official close (a 30-minute VWAP), not the last trade. OPEN/HIGH/LOW
    of 0 means no regular-board trade. ValueError for an HTML page (PSX's answer for a
    day with no session) or anything else that is not a closing file."""
    data = raw or b""
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                lis = [n for n in names if n.lower().endswith(".lis")] or names
                data = z.read(lis[0]) if lis else None
        except Exception:       # a broken zip raises BadZipFile, zlib.error, EOFError, RuntimeError, ...
            raise ValueError("a broken zip, not a PSX closing file")
        if data is None:
            raise ValueError("an empty zip, not a PSX closing file")
    elif data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except Exception:       # a broken gzip raises OSError, EOFError or zlib.error
            raise ValueError("a broken gzip file, not a PSX closing file")
    text = data.decode("utf-8", "replace").lstrip(chr(0xfeff))
    head = text.lstrip()[:500].lower()
    if head.startswith("<") or "<html" in head:
        raise ValueError("an HTML page, not a PSX closing file (PSX answers a day with no session with "
                         "its not-found page)")
    months = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    rows = []
    for line in text.splitlines():
        f = [x.strip() for x in line.split("|")]
        if len(f) < 10 or not f[1]:
            continue
        m = re.match(r"^(\d{1,2})([A-Za-z]{3})(\d{4})$", f[0])
        if not m or m.group(2).upper() not in months:
            continue
        try:
            day = datetime.date(int(m.group(3)), months.index(m.group(2).upper()) + 1, int(m.group(1)))
        except ValueError:
            continue
        fm = FLAG_RE.search(f[3])
        rows.append({"date": day, "symbol": f[1], "sector": f[2], "short_name": f[3],
                     "open": num(f[4]), "high": num(f[5]), "low": num(f[6]), "close": num(f[7]),
                     "volume": int(num(f[8]) or 0), "ldcp": num(f[9]),
                     "flags": re.findall("XD|XB|XR", fm.group(0)) if fm else []})
    if not rows:
        raise ValueError("no rows found: not a PSX closing file")
    return rows


def symbol_kind(symbol, sector, full_name=""):
    """What a PSX symbol is: "ordinary", "gem_equity", "non_voting", "preference",
    "right", "etf", "modaraba", "reit", "closed_end_fund", "debt", "stock_future" or
    "index_future". sector is the closing file's code (36 debt, 40 stock futures, 41
    index futures). full_name is the symbol name file's full name; the closing file's
    short name works too. A class B share (PIAHCLB) counts as ordinary."""
    sym = str(symbol or "").strip().upper()
    sec = str(sector or "").strip()
    name = str(full_name or "")
    if sec == "41":
        return "index_future"
    if sec == "40":
        return "stock_future"
    if sec == "36":
        return "debt"
    funds = {"0806": "closed_end_fund", "0819": "modaraba", "0836": "reit", "0837": "etf"}
    if sec in funds:
        return funds[sec]
    fut = FUTURE_RE.match(sym)
    if not sec and fut:
        return "index_future" if fut.group(1) in ("KSE30", "BKTI", "OGTI") else "stock_future"
    if re.match(r"^[A-Z0-9]+?R\d?$", sym) and re.search(r"\(R\)|\bright\b|limitedright", name, re.I):
        return "right"
    if re.search(r"\(pref\)|\bpref\b|preference", name, re.I):
        return "preference"
    if re.search(r"non[- ]?vot", name, re.I):
        return "non_voting"
    if re.search(r"\(PR\)", name):
        return "preference"
    if sym.startswith("GEM"):
        return "gem_equity"
    return "ordinary"


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


# ---------------------------------------------------------------- data access

def site_on(cfg):
    """True only when psx/config.json says "psx_site": true (the JSON true, nothing else).
    Turn it on only if PSX has given you permission (marketdatarequest@psx.com.pk)."""
    return isinstance(cfg, dict) and cfg.get("psx_site") is True


def http_get(url, timeout=30):
    """A plain GET -> the body as bytes (un-gzipped when the server gzipped it). Sends only
    our User-Agent and "Accept-Encoding: gzip". Waits (SLEEP) so two requests are at least
    PAUSE seconds apart. urllib errors (HTTPError for a 404, URLError) are raised; an
    answer that is cut off or a broken gzip body is a URLError too."""
    last = getattr(http_get, "last", None)
    if last is not None:
        wait = PAUSE - (time.monotonic() - last)
        if wait > 0:
            SLEEP(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            enc = str(r.headers.get("Content-Encoding") or "").lower()
            try:
                body = r.read()
            except Exception as e:      # IncompleteRead, a timeout or a reset while reading
                raise urllib.error.URLError("the answer was cut off (%s)" % (e,))
    finally:
        http_get.last = time.monotonic()
    if "gzip" in enc and body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:               # OSError, EOFError or zlib.error
            raise urllib.error.URLError("a broken gzip answer")
    return body


def daily_path(root, d):
    """<root>/mkt_summary/YYYY-MM-DD.Z, or the .lis or .zip with that name when that is
    the file you saved. The .Z path when none of them is there."""
    base = os.path.join(root, "mkt_summary", d.isoformat())
    for ext in (".Z", ".lis", ".zip"):
        if os.path.exists(base + ext):
            return base + ext
    return base + ".Z"


def daily_rows(d, root, cfg=None, offline=False):
    """The PSX closing file of date d -> rows (read_mkt_summary), or None when there is none.

    The data folder first (daily_path). Else, only when the site is on and not offline:
    GET PSX /download/mkt_summary/YYYY-MM-DD.Z, save it there (so it is never fetched
    twice) and read it. A 404 or an HTML page means no session that day, or not
    published yet (about 30 to 40 minutes after the close): None. ValueError for a
    broken file, or one that holds another day."""
    path = daily_path(root, d)
    fetched = False
    if os.path.exists(path):
        with open(path, "rb") as fh:
            raw = fh.read()
    elif site_on(cfg) and not offline:
        try:
            raw = http_get("%s/download/mkt_summary/%s.Z" % (PSX, d.isoformat()))
        except urllib.error.HTTPError as e:
            try:
                e.close()
            except Exception:
                pass
            if e.code == 404:
                return None
            raise
        fetched = True
    else:
        return None
    head = raw[:500].lstrip().lower()
    if head.startswith(b"<") or b"<html" in head:
        return None
    try:
        rows = read_mkt_summary(raw)
    except ValueError as e:
        raise ValueError("%s: %s" % (path, e))
    if rows[0]["date"] != d:
        raise ValueError("%s holds the closing file of %s, not %s"
                         % (path, rows[0]["date"].isoformat(), d.isoformat()))
    if fetched:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "wb") as fh:
            fh.write(raw)
        os.replace(path + ".tmp", path)
    return rows


def recent_sessions(end_date, n, root, cfg=None, offline=False, include_end=True, max_back=None):
    """The newest n sessions up to end_date -> [(date, rows)], oldest first (newest LAST).

    Walks back day by day over trading days by the calendar, so weekends and PSX
    holidays are never fetched, and skips a day with no file (or a 404). A weekday the
    calendar calls a holiday is still used when its file is in the data folder. With
    include_end=False it starts the day before end_date. max_back caps the calendar days
    it looks at (default 3 x n + 10)."""
    out = []
    d = end_date if include_end else end_date - datetime.timedelta(days=1)
    for _ in range(max_back if max_back is not None else 3 * n + 10):
        if len(out) >= n:
            break
        rows = None
        if trading_day(d, cfg)[0]:
            rows = daily_rows(d, root, cfg, offline)
        elif d.weekday() < 5 and os.path.exists(daily_path(root, d)):
            rows = daily_rows(d, root, cfg, True)
        if rows:
            out.append((d, rows))
        d -= datetime.timedelta(days=1)
    out.reverse()
    return out


def page_html(path, url, cfg=None, offline=False):
    """A PSX page -> (html text, "site" or "saved"), or (None, a plain reason to show).
    With the site on (and not offline) it asks the site first, so a page saved earlier
    never hides today's data; if the site fails it falls back to the saved file at
    `path`. With the site off, or offline, it reads only the saved file. A replay with
    --now should pass offline=True: the site only has today."""
    saved = bool(path) and os.path.exists(path)
    if site_on(cfg) and not offline:
        try:
            return http_get(url).decode("utf-8", "replace"), "site"
        except urllib.error.HTTPError as e:
            try:
                e.close()
            except Exception:
                pass
            why = "PSX answered HTTP %d for %s" % (e.code, url)
        except (urllib.error.URLError, OSError) as e:
            why = "could not reach PSX for %s: %s" % (url, getattr(e, "reason", e))
        if not saved:
            return None, why
    if saved:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(), "saved"
    if not site_on(cfg):
        return None, SITE_OFF % ("%s as %s" % (url, path))
    return None, "no saved page at %s (--offline reads only the data folder): save %s there" % (path, url)


def company_html(symbol, root, cfg=None, offline=False):
    """A symbol's company page through page_html(): the PSX site first when it is on and not
    offline, else <root>/company/<SYM>.html. -> (html text, "site" or "saved"), or (None, a reason)."""
    sym = re.sub(r"[^A-Z0-9-]", "", str(symbol or "").upper())
    if not sym:
        return None, "no symbol"
    return page_html(os.path.join(root, "company", sym + ".html"), "%s/company/%s" % (PSX, sym), cfg, offline)


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

def swing_window(now_pkt, order_type, cfg=None):
    """Why a swing buy cannot go in at this naive Karachi time, else "". PSX must be open,
    past the 2-minute opening match, or in the pre-open with a limit order (it waits for the
    opening match). no_entries_before and no_entries_after are day-trade rules: not used."""
    st = market_state(now_pkt, cfg)
    if st["state"] == "pre-open":
        if str(order_type or "").strip().lower() == "limit":
            return ""
        return "PSX takes no market orders in the pre-open: use a limit order"
    if st["state"] != "open":
        return "no new entries now: " + st["reason"]
    d = now_pkt.date()
    match = datetime.timedelta(minutes=MATCH_MINUTES)
    for a, _ in session(d, cfg):
        start = datetime.datetime.combine(d, a)
        if start <= now_pkt < start + match:
            return "the opening match runs until %s PKT: no new orders yet" % clock_label(start + match)
    return ""


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


def daily_bars(symbol, sessions):
    """One symbol's daily bars from recent_sessions() -> [{date, open, high, low, close,
    volume, ldcp, flags}], oldest first. A day it did not trade on the regular board
    (no row, or OPEN/HIGH/LOW of 0) is skipped. A session's rows may also be a dict
    {symbol: row}, which is faster when you look up many symbols."""
    sym = str(symbol or "").strip().upper()
    out = []
    for d, rows in sessions:
        if isinstance(rows, dict):
            r = rows.get(sym)
        else:
            r = next((x for x in rows if x.get("symbol") == sym), None)
        if not r or not r.get("open") or not r.get("high") or not r.get("low") or r.get("close") is None:
            continue
        out.append({"date": r.get("date") or d, "open": r["open"], "high": r["high"], "low": r["low"],
                    "close": r["close"], "volume": r.get("volume"), "ldcp": r.get("ldcp"),
                    "flags": list(r.get("flags") or [])})
    out.sort(key=lambda b: b["date"])
    return out


def window_sessions(dates, cfg=None):
    """The dates of the closing files read, oldest first -> (every PSX session from the first to
    the last by the calendar, the ones among them with no file). A file on a day the calendar
    calls a holiday counts as a session."""
    if not dates:
        return [], []
    have, out, d = set(dates), [], min(dates)
    while d <= max(dates):
        if d in have or trading_day(d, cfg)[0]:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out, [x for x in out if x not in have]


# ---------------------------------------------------------------- files and small helpers

def blocked_set(value):
    """The "blocked" tickers as a set, the rule psx-budget uses: a string is one ticker, or a few
    split on commas ("ABC, XYZ"). Anything but a list or a string is a plain error, never ignored."""
    if value is None:
        return set()
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        raise RiskError("\"blocked\" in psx/config.json must be a list of tickers, like [\"ABC\", \"XYZ\"]")
    return {str(b).strip().upper() for b in value if b is not None and str(b).strip()}


def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


def read_optional(path, then):
    """A file that only helps a swing check (psx/plan.json, psx/state/alerts.json) -> (doc or None,
    warning or None). Missing: None. Not valid JSON: None, and a warning that ends with `then` (what
    the check does without it). It never stops a check: an exit must always be possible."""
    try:
        return read_json(path, "file", required=False), None
    except (RiskError, OSError) as e:
        return None, "%s. So %s" % (str(e).rstrip("."), then)


def fingerprint(order):
    """Same function in psx-broker/ticket.py: the exact order that was checked. The
    sha256 of the ORDER_FIELDS the order has, as sorted JSON, first 16 hex characters."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    """A number from JSON (a number or a number in a string) -> float, else None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def _price(x):
    """A price above 0 as a float, else None (the format check blocks a bad one)."""
    v = _f(x)
    return v if v is not None and v > 0 else None


def rs(x):
    """Rupees for people: "Rs 5,000" for whole rupees, else "Rs 4,999.94"."""
    v = Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return money(v, 0) if v == v.to_integral_value() else money(v)


def _p(x):
    return "%.2f" % float(x)


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def whole_pct(share):
    """0.326 -> 33: a share as a whole percent, rounded half up."""
    return int((Decimal(str(share)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def shares(q):
    """3428 -> "3428 shares"; 1 -> "1 share"."""
    return "%s share%s" % (_num(q), "" if float(q) == 1 else "s")


def on_tick(x):
    """True when a price sits on the Rs 0.01 tick (float noise under a millionth is fine)."""
    return abs(x * 100 - round(x * 100)) < 1e-6


def hhmm(value, key):
    """A config time: "09:45" -> time(9, 45); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    try:
        return parse_hhmm(value, key)
    except ValueError as e:
        raise RiskError("%s in psx/config.json (got %r)" % (e, value))


# ---------------------------------------------------------------- account and the broker log

def account(cfg, portfolio, marks):
    """Starting account: cash, shares, position values and each stock's lots as (date, shares).
    No portfolio = a fresh account funded with the budget. A holding is valued at its fresh
    price (marks: {SYM: price}, fresh ones only), else at its cost.
    For swing it also keeps each position's "plan" block ("plans"), its cost a share (the
    lots' "cost" / its shares: "avg", None when a lot has no cost), its first lot's date
    ("since") and the dates of its bonus lots ("bonus": ticket.py fill --bonus, cost 0)."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no psx/portfolio.json and no budget in psx/config.json: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}, "lots": {}, "plans": {}, "avg": {}, "since": {},
                "bonus": {}}, warnings
    if not isinstance(portfolio, dict):
        raise RiskError("portfolio.json must be a JSON object with \"cash\" and \"positions\"")
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number (rupees)")
    shares, values, lots_of = {}, {}, {}
    plans, cost_of, since, bonus = {}, {}, {}, {}
    for p in portfolio.get("positions") or []:
        if not isinstance(p, dict):
            continue
        t = str(p.get("ticker") or p.get("symbol") or "").strip().upper()
        if not t:
            continue
        lots = [x for x in p.get("lots") or [] if isinstance(x, dict)]
        lots_of.setdefault(t, []).extend((str(x.get("date") or ""), _f(x.get("shares")) or 0) for x in lots)
        bonus.setdefault(t, []).extend(str(x.get("date") or "") for x in lots
                                       if x.get("bonus") is True or _f(x.get("cost")) == 0)
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(x.get("shares")) or 0 for x in lots)
        shares[t] = shares.get(t, 0) + n
        if marks.get(t):
            values[t] = shares[t] * marks[t]
        else:
            basis = sum(_f(x.get("cost")) or 0 for x in lots)
            values[t] = values.get(t, 0) + basis
            warnings.append("no fresh price for %s: valued at its cost %s" % (t, rs(basis)))
        if isinstance(p.get("plan"), dict) and t not in plans:
            plans[t] = p["plan"]
        known = bool(lots) and all(_f(x.get("cost")) is not None for x in lots)
        cost_of[t] = (cost_of.get(t, 0.0) + sum(_f(x.get("cost")) for x in lots)) \
            if known and cost_of.get(t, 0.0) is not None else None
        days = sorted(str(x["date"]) for x in lots if x.get("date"))
        if days and (t not in since or days[0] < since[t]):
            since[t] = days[0]
    avg = dict((t, cost_of[t] / shares[t] if cost_of.get(t) is not None and shares[t] > 1e-9 else None)
               for t in shares)
    return {"cash": cash, "shares": shares, "values": values, "lots": lots_of, "plans": plans, "avg": avg,
            "since": since, "bonus": bonus}, warnings


def real_ledger(portfolio, state, swing=True):
    """Does psx/portfolio.json hold real shares? The rule psx-broker uses before a paper fill (D39)
    -> ("holds", why), ("may hold", why) or ("", ""). "real_holdings": true holds them, and
    "real_holdings": false says it is a paper file. With no such key, a file with shares or cash
    may hold them when it shows a sign: a top key the tools never write (a "note"), a yes in the
    broker log made with paper off, or (swing) a held share with no swing plan."""
    if not isinstance(portfolio, dict):
        return "", ""
    if "real_holdings" in portfolio:
        return ("holds", "") if portfolio.get("real_holdings") else ("", "")
    held = [p for p in portfolio.get("positions") or [] if isinstance(p, dict)
            and (_f(p.get("shares", p.get("qty"))) or 0) > 1e-9]
    if not held and not _f(portfolio.get("cash")):
        return "", ""
    extra = [k for k in portfolio if k not in TOOL_KEYS]
    if extra:
        return "may hold", "it has %s, which the tools never write" % ", ".join('"%s"' % k for k in extra)
    recs = state.get("orders") if isinstance(state, dict) else None
    for rec in recs if isinstance(recs, list) else []:
        if isinstance(rec, dict) and rec.get("status") == "approved" and rec.get("paper") is False:
            return "may hold", "the broker log has a yes made with paper off (%s %s on %s)" % (
                str(rec.get("symbol") or "?").strip().upper(), str(rec.get("side") or "order").strip().lower(),
                rec_day(rec))
    for p in held if swing else []:
        plan = p.get("plan")
        if not (isinstance(plan, dict) and plan.get("style") == "swing"):
            return "may hold", "%s in it has no swing plan, so no swing fill made it" % str(
                p.get("ticker") or p.get("symbol") or "?").strip().upper()
    return "", ""


def same_day_sell(lots, qty, today):
    """True when selling qty takes only lots bought on `today` (oldest lots first, the way
    psx-broker sells them). With "day_trade_one_side": true such a sell pays no commission."""
    left = qty
    for day, n in sorted(lots or [], key=lambda x: x[0]):
        take = min(left, n)
        if take <= 0:
            continue
        if day != today:
            return False
        left -= take
    return left <= 1e-9


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    """psx-broker logs the kind psx-risk gave; older records: a buy is an entry."""
    k = rec.get("kind")
    if k:
        return k == "entry"
    return str(rec.get("side") or "").strip().lower() == "buy"


def placed_today(state, today):
    """The orders that count as placed today in psx/state/broker.json, with "symbol" in
    capitals and "side" in lower case. One order per fingerprint, by its newest record of
    today: "approved" (you said yes, so it may be placed in your broker app) or "placed"
    counts; "skipped", "failed" or "declined" does not. A "filled" record counts too when no
    counted order has its symbol and side today (a fill recorded by hand: ticket.py fill);
    it has no max loss. A record that closes an order of another day ("of": that day, as
    "expired" and "cancelled" do) is not one of today's. "expired" and "cancelled" free an
    order the way "skipped" does. -> (orders, "SYM side" of each counted buy with no fill yet)."""
    recs = []
    for rec in (state or {}).get("orders", []) if isinstance(state, dict) else []:
        if isinstance(rec, dict) and rec_day(rec) == today and str(rec.get("of") or today) == today:
            recs.append(dict(rec, symbol=str(rec.get("symbol") or "").strip().upper(),
                             side=str(rec.get("side") or "").strip().lower()))
    newest, keys = {}, []
    for i, r in enumerate(recs):
        if r.get("status") == "filled":
            continue
        key = r.get("fingerprint") or i
        if key not in newest:
            keys.append(key)
        newest[key] = r
    out = [newest[k] for k in keys if newest[k].get("status") in STILL_COUNTS]
    have = set((r["symbol"], r["side"]) for r in out)
    filled = set((r["symbol"], r["side"]) for r in recs if r.get("status") == "filled")
    for r in recs:
        if r.get("status") == "filled" and (r["symbol"], r["side"]) not in have:
            out.append(dict(r, by_hand=True))
            have.add((r["symbol"], r["side"]))
    unfilled = ["%s %s" % (r["side"], r["symbol"]) for r in out
                if r["side"] == "buy" and not r.get("by_hand") and (r["symbol"], "buy") not in filled]
    return out, unfilled


# ---------------------------------------------------------------- swing: open orders, the ledger and its risk

def order_book(state):
    """Each order in psx/state/broker.json once, with its newest status, and every fill.

    An order is one fingerprint on one day. A later record with "of" (the day of the order
    it closes, as "expired" and "cancelled" have) belongs to that day's order; an "expired",
    "cancelled" or "skipped" record with no "of" and no order of its own day closes the
    newest order with its fingerprint (a "declined" or "failed" one never closes another
    day's order). A record with no fingerprint is an order of its own when it is "approved"
    or "placed". "stop", "bonus" and "cash" records are no order and no fill.
    -> (orders, fills): orders [{"fingerprint", "day", "symbol", "side", "status", "at" (its
    newest record), "first_at" (its first record), and "qty", "entry_ref", "max_loss",
    "plain", "type" when a record has them}], in the order they first show; fills
    [(symbol, side, day, shares or None, time or None)] of the "filled" records."""
    recs = state.get("orders") if isinstance(state, dict) else None
    book, keys, fills = {}, [], []
    for i, r in enumerate(recs if isinstance(recs, list) else []):
        if not isinstance(r, dict):
            continue
        st, day = r.get("status"), rec_day(r)
        sym = str(r.get("symbol") or "").strip().upper()
        side = str(r.get("side") or "").strip().lower()
        if st == "filled":
            q = _f(r.get("qty"))
            fills.append((sym, side, day, q if q is not None and q > 0 else None, _tape_time(r.get("at"))))
            continue
        fp = r.get("fingerprint") or None
        if fp is None:
            key = (None, i)
        else:
            key = (fp, str(r.get("of") or day))
            if key not in book and st in CLOSES_OLD and not r.get("of"):
                older = [k for k in keys if k[0] == fp]
                key = older[-1] if older else key
        if key not in book:
            if st not in STILL_COUNTS:
                continue                        # it closes nothing that is open, or it is no order
            book[key] = {"fingerprint": fp, "day": key[1] if fp else day, "symbol": sym, "side": side,
                         "first_at": r.get("at")}
            keys.append(key)
        o = book[key]
        for k in ("qty", "entry_ref", "max_loss", "plain", "type"):
            if r.get(k) is not None:
                o[k] = r[k]
        o["status"], o["at"] = st, r.get("at")
    return [book[k] for k in keys], fills


def has_fill(order, fills):
    """True when a "filled" record of the order's symbol and side is on or after its day."""
    return any(f[0] == order["symbol"] and f[1] == order["side"] and f[2] >= order["day"] for f in fills)


def fill_today(orders, fills, today):
    """Give each fill of today to today's open orders: an order gets "filled" (the shares
    recorded for it so far) and "left" (its qty less those; None when the log has no qty).
    A fill goes to the oldest order of its symbol and side first, and only to an order whose
    first record is not after the fill. An order with no qty, or a fill with no shares,
    closes the old way: any fill. -> the orders that still have shares open."""
    for o in orders:
        q = _f(o.get("qty"))
        o["filled"], o["left"], o["done"] = 0.0, q if q is not None and q > 0 else None, False
    for sym, side, day, n, when in fills:
        if day < today:
            continue
        mine = [o for o in orders if o["symbol"] == sym and o["side"] == side and not o["done"]
                and not (when and _tape_time(o.get("first_at")) and _tape_time(o["first_at"]) > when)]
        for o in mine:
            if n is None or o["left"] is None:
                o["done"] = True                # the old rule: any fill closes the order
                if n is None:
                    continue
                break
            take = min(n, o["left"])
            o["filled"], o["left"], n = o["filled"] + take, o["left"] - take, n - take
            o["done"] = o["left"] <= 1e-9
            if n <= 1e-9:
                break
    return [o for o in orders if not o.pop("done")]


def open_orders(state, today):
    """Orders in psx/state/broker.json whose newest record is "approved" or "placed", with no
    fill or with shares not filled yet: (today's buys, today's sells, buys of an earlier day).
    An order of today filled in part keeps its other shares open ("left"): they may still
    fill today (fill_today). A PSX order ends at the close, so an earlier day's buy counts
    only with no fill on or after its day, and only with a fingerprint: ticket.py result
    --fingerprint FP closes it."""
    orders, fills = order_book(state)
    live = [o for o in orders if o["status"] in STILL_COUNTS]
    now_open = fill_today([o for o in live if o["day"] == today], fills, today)
    return ([o for o in now_open if o["side"] == "buy"],
            [o for o in now_open if o["side"] == "sell"],
            [o for o in live if o["fingerprint"] and o["day"] < today and o["side"] == "buy"
             and not has_fill(o, fills)])


def order_words(o):
    """"stop-limit, 09:36" for an order in the log: its type (the record's "type", else the
    first type word in the order part of its plain text) and the Karachi time of its newest
    record."""
    typ = ORDER_WORDS.get(str(o.get("type") or "").strip().lower(), "")
    if not typ:
        head = str(o.get("plain") or "").split(" / ")[0].lower()
        for pat, word in ((r"\bstop[- ]limit\b", "stop-limit"), (r"\bmarket[- ]to[- ]limit\b", "market-to-limit"),
                          (r"\blimit\b", "limit"), (r"\bmarket\b", "market")):
            if re.search(pat, head):
                typ = word
                break
    when = _tape_time(o.get("at"))
    return ", ".join(x for x in (typ, when.strftime("%H:%M") if when else "") if x)


def clear_line(o, status):
    """The psx-broker line that records an order of the log as `status`."""
    if o.get("fingerprint"):
        return "ticket.py result --fingerprint %s --status %s" % (o["fingerprint"], status)
    return "ticket.py result --symbol %s --date %s --status %s" % (o["symbol"], o["day"], status)


def open_sell_words(sym, n, sells):
    """The blank-sale block when sells placed today already cover the shares (D58): "short: 143
    DEMO shares are already in a sell placed today (stop-limit, 09:36). Cancel it in your
    broker app, then record it: ticket.py result --fingerprint FP --status cancelled".
    A sell filled in part says so: "(stop-limit, 09:52; 100 of 253 filled)"."""
    many = len(sells) > 1

    def words(s):
        w = order_words(s) or s["status"]
        if s.get("filled"):
            w += "; %s of %s filled" % (_num(s["filled"]), _num(_f(s.get("qty"))))
        return w
    return "short: %s %s share%s %s already in %s placed today (%s). Cancel %s in your broker app, then record %s: %s" % (
        _num(n), sym, "" if float(n) == 1 else "s", "is" if float(n) == 1 else "are",
        ("%d sells" % len(sells)) if many else "a sell", "; ".join(words(s) for s in sells),
        "them" if many else "it", "each one" if many else "it",
        "; ".join(clear_line(s, "cancelled") for s in sells))


def waiting_qty(o):
    """The shares of a buy with no fill yet: the part not filled yet ("left", fill_today),
    else the qty of its approve record. None when the log has no qty."""
    q = _f(o.get("left")) if o.get("left") is not None else _f(o.get("qty"))
    return q if q is not None and q > 0 else None


def waiting_worth(o):
    """What the shares of a buy with no fill yet are worth at its entry_ref (qty x entry_ref,
    to the paisa): the value it adds to the account once it fills. None with no price."""
    q, p = waiting_qty(o), _price(o.get("entry_ref"))
    if q is None or p is None:
        return None
    return float((Decimal(str(p)) * Decimal(str(q))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def waiting_value(o, costs):
    """The cash a buy with no fill yet holds back: qty x entry_ref plus its buy costs
    (leg_cost, commission on), both from its approve record. Filled in part: only the part
    not filled yet. None with no entry_ref."""
    q, p = waiting_qty(o), _price(o.get("entry_ref"))
    if q is None or p is None:
        return None
    value = Decimal(str(p)) * Decimal(str(q))
    return float((value + Decimal(str(leg_cost(p, q, costs, True)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def waiting_risk(o, per_trade):
    """The open risk of a buy with no fill yet: the max loss of its approve record (filled in
    part: its share for the part not filled yet), or per_trade (your max_loss_per_trade_pkr)
    when the log has no max loss."""
    ml, q, left = _f(o.get("max_loss")), _f(o.get("qty")), waiting_qty(o)
    if ml is None:
        return per_trade
    if q and left is not None and left < q - 1e-9:
        return float((Decimal(str(ml)) * Decimal(str(left)) / Decimal(str(q))).quantize(Decimal("0.01"),
                                                                                       rounding=ROUND_HALF_UP))
    return ml


def waiting_words(waiting, costs, swing):
    """The top warnings for today's buys that may still fill (D57: swing, and psx-invest orders):
    their cash comes off here, with the exact line to record each one expired (clear_line, never
    --index: that names the newest ticket). A buy filled in part gets its own line."""
    out, whole = [], [w for w in waiting if not w.get("filled")]

    def cash_words(rows, it):
        cash = round(sum(waiting_value(w, costs) or 0.0 for w in rows), 2)
        return ("%s (costs included) is taken off the cash here" % rs(cash)) if cash else \
            "the log has no price for %s, so %s cash cannot be taken off here" % (it, "its" if it == "it" else "their")
    if whole:
        it = "them" if len(whole) > 1 else "it"
        if swing:
            what = ", and %s as %s and as open risk" % (("they count", "open positions") if it == "them" else
                                                         ("it counts", "an open position"))
        else:
            what = " for the psx-invest orders"
        out.append("no fill recorded yet for %s, which you said yes to today: %s%s. When %s, record %s "
                   "(psx-broker: ticket.py fill). If %s at the close with no fill, record %s expired: %s"
                   % (", ".join("buy %s" % w["symbol"] for w in whole), cash_words(whole, it), what,
                      "they fill" if it == "them" else "it fills", "each one" if it == "them" else "it",
                      "they end" if it == "them" else "it ends", "each one" if it == "them" else "it",
                      "; ".join(clear_line(w, "expired") for w in whole)))
    for w in waiting:
        if w.get("filled"):
            out.append("buy %s is filled in part: %s of %s shares are recorded. The other %s may still fill today: "
                       "%s%s. Record each fill with ticket.py fill. A PSX order ends at the close, so the rest stops "
                       "counting after today" % (w["symbol"], _num(w["filled"]), _num(_f(w["qty"])), _num(w["left"]),
                                                cash_words([w], "them"),
                                                ", and they count as open risk" if swing else " for the psx-invest orders"))
    return out


def plan_style(order):
    """The style of an order's plan: "swing", "invest", "day" (a plan with no "style": v1), or
    the plan's own word; "" when the order has no plan block."""
    plan = order.get("plan") if isinstance(order, dict) else None
    if not isinstance(plan, dict):
        return ""
    return str(plan.get("style") or "day").strip().lower()


def ex_events(sym, since, today, root, alerts=None, cfg=None):
    """The bonus, rights and split ex-dates of sym from `since` (its buy day) to `today` (dates),
    oldest first: [{"date", "kind", "factor"}]. Found the way ticket.py stop --scale finds them
    (D56), so a scale it took counts here too. From the closing files in <root>/mkt_summary (read
    only, never fetched): a new XB or XR flag, or with no flag an LDCP 10% or more away from the
    close of the session before. Also from psx-alerts' record ("swing_ex" in psx/state/alerts.json:
    alerts), which has an ex-date it saw on the company page before that day's closing file.
    An XD (a cash payout) is not one: the stop stays (D27)."""
    found, sessions, d = {}, [], since - datetime.timedelta(days=15)
    while d <= today:
        path = daily_path(root, d)
        if os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    rows = read_mkt_summary(fh.read())
            except (OSError, ValueError):
                rows = None                     # a broken file shows no ex-date
            if rows and rows[0]["date"] == d:
                sessions.append((d, rows))
        d += datetime.timedelta(days=1)
    bars = daily_bars(sym, sessions)
    spans, _ = window_sessions([x for x, _ in sessions], cfg)
    order = dict((x, i) for i, x in enumerate(spans))
    for prev, cur in zip(bars, bars[1:]):
        if not since <= cur["date"] <= today:
            continue
        ldcp, close = _price(cur.get("ldcp")), _price(prev.get("close"))
        if not ldcp or not close:
            continue
        factor = ldcp / close
        new = [f for f in cur.get("flags") or [] if f not in (prev.get("flags") or [])]
        if ("XB" in new or "XR" in new) and abs(factor - 1) >= 0.0005:
            kind = "".join(new)
        elif not new and abs(factor - 1) >= SPLIT_AT and order.get(cur["date"], -9) - order.get(prev["date"], -99) == 1:
            kind = "split"
        else:
            continue
        found[cur["date"].isoformat()] = {"date": cur["date"].isoformat(), "kind": kind, "factor": round(factor, 4)}
    seen = alerts.get("swing_ex") if isinstance(alerts, dict) else None
    events = seen.get("%s-%s" % (sym, since.isoformat())) if isinstance(seen, dict) else None
    for key, ev in sorted(events.items()) if isinstance(events, dict) else []:
        factor = _f(ev.get("factor")) if isinstance(ev, dict) else None
        if (factor is None or factor <= 0 or str(ev.get("kind") or "XD") == "XD" or key in found
                or not since.isoformat() <= str(key) <= today.isoformat()):
            continue
        found[key] = {"date": key, "kind": str(ev["kind"]), "factor": round(factor, 4)}
    return [found[k] for k in sorted(found)]


def scale_events(portfolio, today, root, alerts=None, cfg=None):
    """{SYM: ex_events()} for each position in psx/portfolio.json whose swing plan has a ticket.py
    stop --scale record with a factor not 1: only those need it. today: a date. Data folder only."""
    out = {}
    positions = portfolio.get("positions") if isinstance(portfolio, dict) else None
    for p in positions if isinstance(positions, list) else []:
        if not isinstance(p, dict):
            continue
        plan = p.get("plan")
        sym = str(p.get("ticker") or p.get("symbol") or "").strip().upper()
        if not sym or not isinstance(plan, dict) or not any(
                isinstance(s, dict) and _f(s.get("factor")) is not None and abs(_f(s["factor"]) - 1) > 1e-9
                for s in plan.get("scaled") or []):
            continue
        try:
            since = datetime.date.fromisoformat(str(plan.get("date")))
        except ValueError:
            continue                            # no buy day: no scale can be checked, so none counts
        out[sym] = ex_events(sym, since, today, root, alerts, cfg)
    return out


def checked_scales(plan, events):
    """The ticket.py stop --scale records of a ledger plan that the data backs (D56, review F15)
    -> (counted [(date, factor)], not counted [(date, factor)], cut [(date, factor, counted as)]),
    oldest first. A record with a factor under 1 counts only when a bonus, rights or split ex-date
    (events, from ex_events) from the buy day on, on or before its date, has no earlier counted
    record. It counts at no less than that ex-date's factor (two ex-dates: both factors), the most
    ticket.py allows: a record under it is in "cut" too. So a "scaled" entry added by hand never
    hides a stop lowered by hand. "keep" (1) is in no list, nor is a factor over 1 (no tool writes
    one)."""
    if not isinstance(plan, dict):
        return [], [], []
    buy = str(plan.get("date") or "")
    evs = sorted((e for e in events or [] if isinstance(e, dict) and str(e.get("date") or "") >= buy
                  and _price(e.get("factor"))), key=lambda e: str(e["date"]))
    done, counted, skipped, cut = [], [], [], []
    for s in plan.get("scaled") or []:
        f = _f(s.get("factor")) if isinstance(s, dict) else None
        if f is None or not 0 < f < 1 - 1e-9:
            continue
        day = str(s.get("date") or "")
        left = [e for e in evs if str(e["date"]) <= day and not any(x >= str(e["date"]) for x in done)]
        floor = 1.0
        for e in left:
            floor *= float(e["factor"])
        if not left or floor >= 1 - 1e-9:
            skipped.append((day, f))
            continue
        if f < floor - FACTOR_SLACK:
            cut.append((day, f, round(floor, 4)))
            f = round(floor, 4)
        counted.append((day, f))
        done.append(day)
    return counted, skipped, cut


def stop_at_buy_now(plan, events=None):
    """A ledger plan's "stop_at_buy" after each recorded scale that the data backs (checked_scales:
    ticket.py stop --scale F, the stop x F, rounded down to the tick, one scale at a time), or None.
    events: the ex-dates of the share (ex_events); none known, no scale counts."""
    ref = _price(plan.get("stop_at_buy")) if isinstance(plan, dict) else None
    if ref is None:
        return None
    for _, f in checked_scales(plan, events)[0]:
        ref = round_down_tick(ref * f)
    return ref


def swing_limit(s, key, whole=False):
    """A limit from the swing block (swing_settings) as a number, or None when it is null.
    0 switches max_open_positions and max_open_risk_pkr off; for max_gap_loss_pkr it is a
    Rs 0 limit (swing_gap_cap). Anything else is a plain error, never ignored."""
    v = s.get(key)
    if v is None:
        return None
    x = _f(v)
    if x is None or x < 0 or (whole and x != int(x)):
        raise RiskError('"%s" in the "swing" block of psx/config.json must be %s, or null (got %r)'
                        % (key, "a whole number, 0 or more" if whole else "rupees, 0 or more", v))
    return x


def swing_gap_cap(s, per_trade):
    """The cap on one swing buy's gap loss (D55), the rule psx-budget uses: swing.max_gap_loss_pkr
    (0 is a Rs 0 limit: the gap loss must have a cap), else (null) 3 x max_loss_per_trade_pkr.
    None (no gap limit) only when both are unset."""
    cap = swing_limit(s, "max_gap_loss_pkr")
    if cap is not None:
        return cap
    return round(3 * per_trade, 2) if per_trade else None


def pending_scales(plan, bonus_days, events=None):
    """The scales of a ledger plan whose new shares are not recorded yet (D56): its ticket.py
    stop --scale records with a factor under 1 that the data backs (checked_scales, with the
    share's ex-dates `events`), while the position has no bonus lot (ticket.py fill --bonus N)
    dated on or after the plan's buy day. -> [(date, factor)], oldest first. A scale the data
    does not back is not here: its stop counts as it is, as a stop lowered by hand does."""
    if not isinstance(plan, dict):
        return []
    buy = str(plan.get("date") or "")
    if any(not d or d >= buy for d in bonus_days or []):
        return []
    return checked_scales(plan, events)[0]


def unscaled_stop(stop, factors):
    """The stop before ticket.py stop --scale (each scale: the stop x F, rounded down to the
    tick): the lowest tick that scales back to it, the newest scale first. 80.20 / 0.8333
    gives 96.25 (96.25 x 0.8333 = 80.205..., rounded down 80.20)."""
    for f in reversed(factors):
        stop = float((Decimal(str(stop)) / Decimal(str(f))).quantize(Decimal("0.01"), rounding=ROUND_CEILING))
    return stop


def position_risk(sym, n, acct, cap, costs):
    """(open risk in rupees, warning or None) of n shares of a held position, costs included:
    max_loss_at_stop(its cost a share, its ledger plan stop, n, costs), 0 when the stop is at
    or over that cost. With no plan stop, or no cost in its lots, it counts as cap (your
    max_loss_per_trade_pkr), and the warning says so.
    After a scale (ticket.py stop --scale F) the stop is on the new price scale, but the cost a
    share and the shares move to it only when the new shares are recorded (ticket.py fill
    --bonus N). Until then the stop before the scale counts (pending_scales), so all three are
    on the old scale, and the warning says so. Only a scale the data backs counts (acct["ex"]:
    the share's ex-dates); with none, the stop counts as it is."""
    plan = acct["plans"].get(sym) or {}
    stop = _price(plan.get("stop"))
    avg = acct["avg"].get(sym)
    if stop is None or avg is None:
        what = "no plan stop" if stop is None else "no cost in its lots"
        if cap:
            return cap, "%s has %s in psx/portfolio.json: its open risk counts as your %s max loss per trade" \
                % (sym, what, rs(cap))
        return 0.0, "%s has %s in psx/portfolio.json, and there is no max_loss_per_trade_pkr: its open risk is " \
                    "not counted" % (sym, what)
    note = None
    pend = pending_scales(plan, (acct.get("bonus") or {}).get(sym), (acct.get("ex") or {}).get(sym))
    if pend:
        stop = unscaled_stop(stop, [f for _, f in pend])
        note = ("%s's plan was scaled (%s), and no bonus or split shares are recorded since the buy. So its open risk "
                "is counted on the old price scale, with the stop before the scale (%s). When the new shares show in "
                "your broker app, record them: ticket.py fill --symbol %s --bonus N"
                % (sym, "; ".join("x%s on %s" % (_num(f), _day(d)) for d, f in pend), _p(stop), sym))
    if stop >= avg - 1e-9:
        return 0.0, note
    return max_loss_at_stop(avg, stop, n, costs), note


def lowered_by_hand(sym, plan, events=None):
    """The warning when a ledger plan's stop is under its "stop_at_buy" (after each recorded
    scale that the data backs: checked_scales, with the share's ex-dates `events`), else None.
    Only ticket.py stop moves a stop: up, or scaled for a bonus, rights or split ex-date. A scale
    with no such ex-date does not count, and the warning names it."""
    stop, ref = _price((plan or {}).get("stop")), stop_at_buy_now(plan, events)
    if stop is None or ref is None or stop >= ref - CENT:
        return None
    counted, skipped, cut = checked_scales(plan, events)
    words = "%s's stop in psx/portfolio.json (%s) is under its stop at the buy (%s%s): it was lowered by hand." % (
        sym, _p(stop), _p(ref), ", after the recorded scale" if counted else "")
    if skipped:
        many = len(skipped) > 1
        words += (" The scale%s %s %s not count: the closing files and psx/state/alerts.json show no bonus, rights "
                  "or split ex-date of %s for %s." % ("s" if many else "", " and ".join(
                      "x%s of %s" % (_num(f), _day(d)) for d, f in skipped), "do" if many else "does", sym,
                      "them" if many else "it"))
    for d, f, used in cut:
        words += " The scale x%s of %s is under its ex-date's factor %s, so it counts as x%s." % (
            _num(f), _day(d), _num(used), _num(used))
    return words


def _day(text):
    """"2026-10-29" -> "Thu 2026-10-29"; anything else as it is."""
    try:
        return day_label(datetime.date.fromisoformat(str(text)))
    except ValueError:
        return str(text or "")


def open_names(held, wait, batch):
    """"DEMO and LUCK held; PPL with no fill yet": the open positions in words."""
    parts = []
    for names, what in ((held, "held"), (wait, "with no fill yet"), (batch, "passed earlier in this batch")):
        if names:
            parts.append("%s %s" % (names[0] if len(names) == 1 else
                                    ", ".join(names[:-1]) + " and " + names[-1], what))
    return "; ".join(parts)


def swing_gates(sym, n, acct, ledger, waiting, batch_open, old_buys, max_pos, risk_cap, risk_from, per_trade,
                costs):
    """The swing gates of one buy (D29, D33, D57): one plan a stock, an earlier buy with no fill,
    the open positions and the open risk. ledger: {SYM: shares} still held (psx/portfolio.json
    less the exits passed earlier in this batch); waiting: today's other buys with no fill yet;
    batch_open: [(SYM, open risk)] of the buys passed earlier in this batch; per_trade: the
    max_loss_per_trade_pkr that a risk with no stop counts as. A waiting buy filled in part
    counts its held shares in the ledger and the rest at its share of the max loss (waiting_risk).
    -> (reasons, warnings, the "open before this" info line, this buy's open risk)."""
    later, warns = [], []
    if ledger.get(sym, 0) > 1e-9:
        since = acct["since"].get(sym) or (acct["plans"].get(sym) or {}).get("date")
        later.append("open: you already hold %s%s. Swing style keeps one plan a stock, so a second buy of %s is "
                     "blocked while you hold it" % (sym, (" (since %s)" % _day(since)) if since else "", sym))
    for o in old_buys:
        later.append("fill: the buy of %s %s on %s has no fill recorded. If it filled, record the fill (ticket.py "
                     "fill). If not, record it expired: %s" % (o["symbol"], "placed" if o["status"] == "placed" else
                                                              "you said yes to", _day(o["day"]),
                                                              clear_line(o, "expired")))
    held = [s for s in sorted(ledger) if ledger[s] > 1e-9]
    before = sum(position_risk(s, ledger[s], acct, per_trade, costs)[0] for s in held)
    wait = []
    for w in waiting:
        before += waiting_risk(w, per_trade)
        if w["symbol"] not in held and w["symbol"] not in wait:
            wait.append(w["symbol"])
    batch = []
    for s, r in batch_open:
        before += r
        if s not in held and s not in wait and s not in batch:
            batch.append(s)
    before = round(before, 2)
    this_risk = n["max_loss"] if n["max_loss"] is not None else per_trade
    if n["max_loss"] is None and n["stop"] is None:
        warns.append("no stop: in the open risk this buy counts as %s" % (
            ("your %s max loss per trade" % rs(per_trade)) if per_trade else "0 (no max_loss_per_trade_pkr)"))
    names = held + wait + batch
    count = len(set(names) | {sym})
    if max_pos and count > max_pos:
        later.append("open positions: %d open (%s) plus this one is %d, your limit is %d (swing.max_open_positions)"
                     % (len(names), open_names(held, wait, batch), count, int(max_pos)))
    if risk_cap and before + this_risk > risk_cap + CENT:
        later.append("open risk: %s at the stops of your open positions plus %s for this one = %s, over your %s "
                     "limit (%s; costs included)" % (rs(before), rs(this_risk), rs(before + this_risk), rs(risk_cap),
                                                     risk_from))
    line = "open before this: %d of %s positions, %s of %s open risk (costs included)" % (
        len(names), int(max_pos) if max_pos else "unlimited", rs(before), rs(risk_cap) if risk_cap else "no limit on")
    return later, warns, line, this_risk


# ---------------------------------------------------------------- the entry window

def window_check(now_pkt, c):
    """(reasons, warnings) for a new entry now, a naive Karachi time. The market must be
    open, past the opening match: that always holds. The config times are for a normal
    Monday to Thursday: config_time moves them to the day's hours. Setting no_entries_* to
    null switches off only that time, never the market check."""
    d = now_pkt.date()
    before = config_time(hhmm(c.get("no_entries_before"), "no_entries_before"), d, c)
    after = config_time(hhmm(c.get("no_entries_after"), "no_entries_after"), d, c)
    stamp = "%s %s Karachi time" % (day_label(d), now_pkt.strftime("%H:%M"))
    st = market_state(now_pkt, c)
    if st["state"] != "open":
        return ["window: no new entries now (%s): %s" % (stamp, st["reason"])], []
    match = datetime.timedelta(minutes=MATCH_MINUTES)
    segs = [(datetime.datetime.combine(d, a), datetime.datetime.combine(d, b)) for a, b in session(d, c)]
    for start, _ in segs:
        if start <= now_pkt < start + match:
            return ["window: the opening match runs until %s PKT, so PSX takes no new orders yet (%s)"
                    % (clock_label(start + match), stamp)], []
    t = now_pkt.time().replace(second=0, microsecond=0)
    if before is not None and t < before:
        return ["window: no new entries before %s Karachi time (it is %s)"
                % (before.strftime("%H:%M"), t.strftime("%H:%M"))], []
    if after is not None and t > after:
        return ["window: no new entries after %s Karachi time (it is %s)"
                % (after.strftime("%H:%M"), t.strftime("%H:%M"))], []
    warns = []
    for (_, end), (nxt, _) in zip(segs, segs[1:]):
        left = end - now_pkt
        if datetime.timedelta(0) < left <= datetime.timedelta(minutes=BREAK_WARN_MINUTES):
            warns.append("the Juma break starts at %s PKT, in %d minutes: a position you open now sits "
                         "through the break, and the second session starts at %s PKT"
                         % (clock_label(end), (left.seconds + 59) // 60, clock_label(nxt)))
    return [], warns


# ---------------------------------------------------------------- prices, universe and today's limits

def load_prices(pdoc, delay_minutes=5):
    """prices.json -> ({SYM: price}, {SYM: data time as naive UTC or None}, {SYM: detail}).

    The data time is the quote's own time (detail.time, the page's As of). Without it,
    the fetch time (detail.fetched, else the file's as_of) less the data delay: free
    PSX data is about 5 minutes late."""
    if not isinstance(pdoc, dict):
        return {}, {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}, {}
    detail = pdoc.get("detail") if isinstance(pdoc.get("detail"), dict) else {}
    as_of = parse_utc(pdoc.get("as_of"))
    lag = datetime.timedelta(minutes=_f(delay_minutes) or 0)
    out, times, details = {}, {}, {}
    for s, p in prices.items():
        v = _f(p)
        if v is None or v <= 0:
            continue
        s = str(s).strip().upper()
        d = detail.get(s) if isinstance(detail.get(s), dict) else {}
        t = parse_utc(d.get("time"))
        if t is None:
            f = parse_utc(d.get("fetched")) or as_of
            t = f - lag if f else None
        out[s], times[s], details[s] = v, t, d
    return out, times, details


def _tape_time(text):
    """A tape "asof" ("2026-10-29 10:52", Karachi) -> naive Karachi time, or None. A time with
    Z or an offset is turned into Karachi time."""
    t = str(text or "").strip()
    try:
        when = datetime.datetime.fromisoformat(t[:-1] + "+00:00" if t[-1:] in ("Z", "z") else t)
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = to_pkt((when - when.utcoffset()).replace(tzinfo=None))
    return when


def tape_price(root, sym, now_utc):
    """sym's newest price on today's company tape (<root>/tape/YYYY-MM-DD/SYM.jsonl, Karachi
    date) with its "asof" (the data time) not after now: {"price", "time" (naive UTC),
    "source"}, or None. The screener tape is not used: it shows a share with no trade
    today at its last day's price."""
    now_pkt = to_pkt(now_utc)
    best = None
    for s in read_tape(tape_path(root, now_pkt.date(), sym)):
        if "prices" in s or str(s.get("symbol") or sym).strip().upper() != sym:
            continue
        price, when = _price(s.get("price")), _tape_time(s.get("asof"))
        if price is None or when is None or when > now_pkt:
            continue
        if best is None or when > best[0]:
            best = (when, price)
    return {"price": best[1], "time": pkt_to_utc(best[0]), "source": "today's tape"} if best else None


def saved_page_price(root, sym):
    """sym's price on its saved company page (<root>/company/SYM.html): {"price", "time"
    (the page's As of, naive UTC), "source"}, or None. Read from the file only."""
    path = os.path.join(root, "company", re.sub(r"[^A-Z0-9-]", "", sym) + ".html")
    if not sym or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        p = parse_company(fh.read())
    price = _price(p.get("price"))
    if price is None or not p.get("as_of") or (p.get("symbol") and p["symbol"] != sym):
        return None
    return {"price": price, "time": pkt_to_utc(p["as_of"]), "source": "the saved company page"}


def data_quotes(symbols, now_utc, root):
    """{SYM: [quotes]} from the data folder, in the order they are tried after psx/prices.json:
    today's tape, then the saved company page. Nothing is fetched."""
    out = {}
    for sym in sorted(set(str(s).strip().upper() for s in symbols if s)):
        try:
            got = [q for q in (tape_price(root, sym, now_utc), saved_page_price(root, sym)) if q]
        except (OSError, ValueError):
            got = []
        if got:
            out[sym] = got
    return out


def read_universe(path):
    """psx/universe.txt (SYMBOL|Name|Sector code per line) -> a set of symbols, or None
    when the file is not there."""
    if not path or not os.path.exists(path):
        return None
    out = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            sym = line.split("|")[0].strip().upper()
            if sym and not sym.startswith("#"):
                out.add(sym)
    return out


def last_closes(symbols, day, root, cfg=None):
    """{SYM: {"close", "date", "sector", "short_name", "flags"}} from the newest closing file
    before `day` that has the symbol (up to 5 sessions back), read from the data folder
    only. "flags": its ex-date tags (XD, XB, XR). -> (closes, warnings)."""
    want, out = set(symbols), {}
    try:
        sessions = recent_sessions(day, 5, root, cfg, True, include_end=False)
    except (ValueError, OSError) as e:
        return out, ["could not read the PSX closing files in %s: %s" % (os.path.join(root, "mkt_summary"), e)]
    for d, rows in reversed(sessions):              # newest first
        for r in rows:
            s = r.get("symbol")
            if s in want and s not in out and r.get("close"):
                out[s] = {"close": r["close"], "date": d.isoformat(), "sector": r.get("sector") or "",
                          "short_name": r.get("short_name") or "", "flags": list(r.get("flags") or [])}
    return out, []


def _page_limits(text, where, sym, today):
    """Today's limits from one company page -> (limits or None, why not or None, tags).
    tags: {"flags", "as_of", "page"} of the page (SUSPENDED, NON-COMPLIANT, ...), or None;
    "ex": its ex-date tags (XD, XB, XR), and "today": True when the page is as of today."""
    if text is None:
        return None, where, None                    # where is the reason then
    p = parse_company(text)
    reg, asof = p.get("reg") or {}, p.get("as_of")
    page = "the %s company page" % ("saved" if where == "saved" else "PSX site's")
    if p.get("symbol") and p["symbol"] != sym:
        return None, "%s is for %s, not %s" % (page, p["symbol"], sym), None
    when = ("%s %s" % (day_label(asof), clock_label(asof))) if asof else "an unknown time"
    tags = {"flags": [f for f in p.get("flags") or [] if f not in ("XD", "XB", "XR")], "as_of": when, "page": page,
            "ex": [f for f in ("XD", "XB", "XR") if any(f in str(t).upper() for t in p.get("flags") or [])],
            "today": asof is not None and asof.date().isoformat() == today}
    if asof is None or asof.date().isoformat() != today:
        return None, "%s is as of %s, not today, so its limits may be another day's" % (page, when), tags
    if reg.get("lower") is None or reg.get("upper") is None:
        return None, "%s shows no circuit breaker" % page, tags
    return {"lower": reg["lower"], "upper": reg["upper"], "ldcp": reg.get("ldcp"), "from": "company page",
            "label": "company page" if where == "saved" else "company page, PSX site"}, None, tags


def page_limits(sym, today, root, cfg=None, offline=False):
    """Today's limits from the company page (company_html: the PSX site first when it is on
    and not offline, else the saved <data>/company/<SYM>.html). A page whose As of day is
    not today is not used for limits. -> (limits or None, why not or None, tags of the page)."""
    text, where = company_html(sym, root, cfg, offline)
    return _page_limits(text, where, sym, today)


def market_facts(symbols, today, root, cfg=None, offline=False, details=None, times=None, universe_file=None):
    """What the check needs from the market for these symbols, on day `today`
    ("YYYY-MM-DD"): today's price limits, the last close and your universe.

    Limits, in order: psx/prices.json (a quote of today whose lower and upper are from the
    company page: "limits_from"), the company page as of today (the PSX site first when it
    is on, else the saved page), else computed from the last close in the closing files
    (an ex-date lowers the real ones).
    The tags of the company page (SUSPENDED, NON-COMPLIANT, ...) go to "pages".
    -> {"limits": {SYM: {...}}, "unknown": {SYM: why}, "closes": {SYM: {...}}, "pages": {SYM: tags},
        "universe": set or None, "universe_file": path, "warnings": [...]}"""
    c = cfg if isinstance(cfg, dict) else {}
    day = datetime.date.fromisoformat(today)
    syms = sorted(set(str(s).strip().upper() for s in symbols if s))
    closes, warns = last_closes(syms, day, root, c)
    out = {"limits": {}, "unknown": {}, "closes": closes, "pages": {}, "universe": read_universe(universe_file),
           "universe_file": universe_file, "warnings": warns}
    for sym in syms:
        d, t = (details or {}).get(sym) or {}, (times or {}).get(sym)
        lo, hi = _f(d.get("lower")), _f(d.get("upper"))
        if lo and hi and t is not None and to_pkt(t).date() == day and d.get("limits_from") == "company page":
            out["limits"][sym] = {"lower": lo, "upper": hi, "ldcp": _f(d.get("prev_close")), "from": "prices file",
                                  "label": "psx/prices.json"}
            text, where = company_html(sym, root, c, True)      # the saved page only, for its tags
            out["pages"][sym] = _page_limits(text, where, sym, today)[2]
            continue
        got, why, out["pages"][sym] = page_limits(sym, today, root, c, offline)
        if got:
            out["limits"][sym] = got
            continue
        row = closes.get(sym)
        if row:
            lo, hi = price_limits(row["close"])
            out["limits"][sym] = {"lower": lo, "upper": hi, "ldcp": row["close"], "from": "computed",
                                  "label": "computed from the %s close %s" % (row["date"], _p(row["close"])),
                                  "why": why}
            continue
        why = str(why or "no company page").strip().rstrip(".")
        out["unknown"][sym] = "%s%s. No closing file before %s in %s has %s either." % (
            why[:1].upper(), why[1:], today, os.path.join(root, "mkt_summary"), sym)
    return out


def prior_session(d, cfg=None):
    """The PSX session before date d by the calendar (trading_day), or None (15 days back)."""
    x = d
    for _ in range(15):
        x -= datetime.timedelta(days=1)
        if trading_day(x, cfg)[0]:
            return x
    return None


def ex_seen(sym, market, lim, today, cfg=None):
    """The ex-date the data shows for sym (D27, D56), or None -> {"kind", "where", "date"} (a
    split: {"kind", "date", "ldcp", "close", "close_date"}).
    An XB or XR tag on today's company page, else in the newest closing file before today (a
    tag stays many days, so "date" is the newest day it shows); else an XD tag (a cash payout:
    the stop stays); else, with no tag, today's LDCP from the company page 10% or more away
    from the close of the session before ("split", the rule psx-alerts uses)."""
    page = (market.get("pages") or {}).get(sym) or {}
    row = (market.get("closes") or {}).get(sym) or {}
    tags = []
    if page.get("today"):
        tags.append((page.get("ex") or [], "on today's company page", today))
    if row.get("date"):
        tags.append((row.get("flags") or [], "in the closing file of %s" % _day(row["date"]), row["date"]))
    for want in (("XB", "XR"), ("XD",)):
        for flags, where, day in tags:
            kind = "".join(f for f in want if f in flags)
            if kind:
                return {"kind": kind, "where": where, "date": day}
    ldcp, close = _price((lim or {}).get("ldcp")), _price(row.get("close"))
    if lim and lim.get("from") != "computed" and ldcp and close and row.get("date"):
        try:
            before = prior_session(datetime.date.fromisoformat(today), cfg)
        except ValueError:
            before = None
        if before and row["date"] == before.isoformat() and abs(ldcp / close - 1) >= SPLIT_AT:
            return {"kind": "split", "date": today, "ldcp": ldcp, "close": close, "close_date": row["date"]}
    return None


def trigger_words(sym, trig, now_p, now_from, plan, ex):
    """The block of a stop-limit whose trigger is at or over the price now (it would fire at
    once), with what it means: a bonus, right or split the data shows and the plan has no
    scale for yet (ticket.py stop --scale); a cash payout (XD: the stop stays, D27); the stop
    rule met (to sell: ticket.py flatten); or a plan stop under the price (a new protect)."""
    out = ["format: the trigger %s is at or over %s (%s), so it would fire at once." % (_p(trig), now_from, _p(now_p))]
    plan = plan if isinstance(plan, dict) else {}
    pstop = _price(plan.get("stop"))
    if ex and ex["kind"] != "XD" and not any(isinstance(s, dict) and str(s.get("date") or "") >= ex["date"]
                                             for s in plan.get("scaled") or []):
        if ex["kind"] == "split":
            out.append("%s's LDCP today (%s) is %d%% %s the close %s of %s, with no tag: a split?" % (
                sym, _p(ex["ldcp"]), int(round(abs(ex["ldcp"] / ex["close"] - 1) * 100)),
                "under" if ex["ldcp"] < ex["close"] else "over", _p(ex["close"]), _day(ex["close_date"])))
        else:
            out.append("%s shows %s %s." % (sym, ex["kind"], ex["where"]))
        out.append("A bonus, right or split lowers the price, and your plan is not scaled for it yet. You choose: "
                   "scale it (ticket.py stop --symbol %s --scale F; psx-alerts gives F) or keep it (--scale 1)." % sym)
        return " ".join(out)
    if ex and ex["kind"] == "XD":
        out.append("%s shows XD %s: a cash payout. The stop stays as it is." % (sym, ex["where"]))
    if pstop and now_p <= pstop + CENT:
        out.append("%s%s is %s, at or under your plan's stop %s: the stop rule is met. To sell: ticket.py flatten "
                   "--symbol %s (--limit P in the pre-open). You decide." % (now_from[:1].upper(), now_from[1:],
                                                                          _p(now_p), _p(pstop), sym))
    elif pstop:
        out.append("Your plan's stop is %s: ticket.py protect --symbol %s makes a new stop-limit from it."
                   % (_p(pstop), sym))
    return " ".join(out)


def same_plan(doc, sym, plan, today):
    """True when psx/plan.json (doc) is the plan of this swing buy: a swing plan of the same symbol
    for today (its buy day), with the same stop to the paisa (psx-budget copies the plan's stop)."""
    if not isinstance(doc, dict) or not isinstance(plan, dict):
        return False
    a, b = _price(doc.get("stop")), _price(plan.get("stop"))
    return (str(doc.get("symbol") or "").strip().upper() == sym and str(doc.get("style") or "").strip().lower() == "swing"
            and str(doc.get("date") or "") == today and a is not None and b is not None and abs(a - b) <= CENT)


def plan_ldcp(sym, plan, plan_doc, market, today):
    """The LDCP a swing buy's plan was made on (review F41): psx-exit works out the entry and the
    stop from it -> (price, words) or (None, None). The LDCP of the plan's "limits" when they come
    from a company page (a plan made on the buy day), else the plan's "last_close". From the
    order's plan block, else from psx/plan.json when it is this order's plan (same_plan), else
    the newest close in the closing files before today (psx-exit plans from them)."""
    for doc in (plan, plan_doc if same_plan(plan_doc, sym, plan, today) else None):
        if not isinstance(doc, dict):
            continue
        lims = doc.get("limits") if isinstance(doc.get("limits"), dict) else {}
        ldcp, last = _price(lims.get("ldcp")), _price(doc.get("last_close"))
        if ldcp and lims.get("from") not in (None, "computed"):
            return ldcp, "the plan's LDCP %s" % _p(ldcp)
        if last or ldcp:
            return last or ldcp, "the plan's last close %s" % _p(last or ldcp)
    row = (market.get("closes") or {}).get(sym) or {}
    if _price(row.get("close")):
        return row["close"], "the last close %s (%s)" % (_p(row["close"]), _day(row.get("date")))
    return None, None


def ldcp_words(sym, lim, base, base_words, stop, page=None):
    """A swing buy against today's LDCP (review F41) -> (reasons, warnings). Only with today's limits
    from the company page or psx/prices.json: computed limits are the last close, so no news. An
    LDCP more than a tick away from the one the plan was made on (base, plan_ldcp) blocks: an
    ex-date, a split or days with no trade moved it, so the plan's entry and stop are on old prices.
    A stop at or over today's LDCP warns: a limit buy fills at its price or lower. page: the tags of
    the company page (an XD, XB or XR tag on today's page is named)."""
    ldcp = _price((lim or {}).get("ldcp"))
    if not lim or lim.get("from") == "computed" or ldcp is None:
        return [], []
    later, warns = [], []
    if base and abs(ldcp - base) > 0.01 + CENT:
        tags = (page or {}).get("ex") if (page or {}).get("today") else None
        later.append("ex-date: today's LDCP %s (%s) is not %s.%s An ex-date, a split or days with no trade moved it, so "
                     "the plan's entry and stop do not fit today's prices. Make a new plan with psx-exit (exit.py plan "
                     "--symbol %s), then size it again with psx-budget"
                     % (_p(ldcp), lim["label"], base_words, (" Today's company page shows %s." % " and ".join(tags))
                        if tags else "", sym))
    if stop and stop >= ldcp - CENT:
        warns.append("the stop %s is at or over today's LDCP %s: a limit buy can fill lower, at or under the stop"
                     % (_p(stop), _p(ldcp)))
    return later, warns


# ---------------------------------------------------------------- the checks of one order

def format_check(o, sym, side, typ, tif, board, state_now, swing=False):
    """Format reasons (they block every order, exits too) and the qty as a float
    (None when it is not a whole number above 0). Only swing takes a "stop_limit" order:
    it needs a trigger_price and a limit_price on the tick, the limit at or under the
    trigger (check() adds: a sell of shares you hold, a trigger under the price now)."""
    reasons = []
    if not sym:
        reasons.append("format: no symbol")
    if side not in ("buy", "sell"):
        reasons.append("format: side must be buy or sell, got %r" % (o.get("side"),))
    q = _f(o.get("qty"))
    if q is None or q <= 0 or q != int(q):
        reasons.append("format: qty must be a whole number of shares above 0, got %r" % (o.get("qty"),))
        q = None
    for k, why in NOT_AT_PSX.items():
        if o.get(k) not in (None, False, ""):
            reasons.append("format: %s (the order has %s)" % (why, k))
    if swing and typ not in TYPES:
        reasons.append("format: type must be limit, market or stop_limit (a stop-limit sell), got %r"
                       % (o.get("type"),))
    elif not swing and typ not in ("limit", "market"):
        reasons.append("format: type must be limit or market, got %r" % (o.get("type"),))
    if tif != "day":
        reasons.append("format: PSX orders are day orders, so time_in_force must be day, got %r"
                       % (o.get("time_in_force"),))
    if board != "REG":
        reasons.append("format: board must be REG (the regular board), got %r" % (o.get("board"),))
    for k in PRICE_FIELDS:
        if o.get(k) is None:
            continue
        v = _f(o.get(k))
        if v is None or v <= 0:
            reasons.append("format: %s must be a price above 0, got %r" % (k, o.get(k)))
        elif not on_tick(v):
            reasons.append("format: %s %r is not on the Rs 0.01 tick" % (k, o.get(k)))
    if typ == "limit" and o.get("limit_price") is None:
        reasons.append("format: a limit order needs a limit_price")
    if swing and typ == "stop_limit":
        trig, lim_p = _price(o.get("trigger_price")), _price(o.get("limit_price"))
        for k, v in (("trigger_price", o.get("trigger_price")), ("limit_price", o.get("limit_price"))):
            if v is None:
                reasons.append("format: a stop-limit order needs a %s" % k)
        if trig and lim_p and lim_p > trig + CENT:
            reasons.append("format: the limit price %s of a stop-limit sell must be at or under its trigger %s"
                           % (_p(lim_p), _p(trig)))
    if typ == "market" and state_now["state"] == "pre-open":
        reasons.append("format: PSX takes no market orders in the pre-open (limit orders only): %s"
                       % state_now["reason"])
    return reasons, q


def asset_check(sym, market):
    """(reasons that block every order but an exit, reasons that block an entry, warnings,
    the kind of symbol). An ordinary PSX share only: no future, ETF, debt, index, fund,
    right or preference share. When the universe file is there, an entry must be in it."""
    reasons, later, warns = [], [], []
    if not sym:
        return reasons, later, warns, None
    row = market["closes"].get(sym)
    if sym in INDEX_CODES:
        kind = "index"
    else:
        kind = symbol_kind(sym, row["sector"] if row else "", row["short_name"] if row else "")
        if kind == "ordinary" and not row and sym.endswith("ETF"):
            kind = "etf"
        elif kind == "ordinary" and not row and DEBT_RE.search(sym):
            kind = "debt"
    if kind != "ordinary":
        reasons.append("asset: %s is %s; this pack trades ordinary PSX shares only"
                       % (sym, KIND_WORDS.get(kind, kind)))
    elif not SYMBOL_RE.match(sym):
        reasons.append("asset: %s does not look like a PSX symbol (capital letters and digits, like OGDC)" % sym)
    elif market["universe"] is not None and sym not in market["universe"]:
        later.append("asset: %s is not in %s, your list of ordinary PSX shares (psx-scanner writes it)"
                     % (sym, market["universe_file"]))
    elif market["universe"] is None and market["universe_file"] and not row:
        warns.append("%s is in no closing file of the last 5 sessions and there is no %s: check that it is "
                     "an ordinary PSX share" % (sym, market["universe_file"]))
    tags = (market.get("pages") or {}).get(sym) or {}
    if "SUSPENDED" in tags.get("flags", []):
        later.append("asset: %s is SUSPENDED on %s (as of %s): PSX does not trade it now"
                     % (sym, tags["page"], tags["as_of"]))
    other = [f for f in tags.get("flags", []) if f != "SUSPENDED"]
    if other:
        warns.append("%s shows %s on %s (as of %s)" % (sym, ", ".join(other), tags["page"], tags["as_of"]))
    return reasons, later, warns, kind


def buy_checks(o, plan, ref, ref_from, q, c, costs, lim, swing=False):
    """Stop, loss, reward:risk and today's price limits of a buy entry.
    -> (numbers, entry-only reasons, warnings). In swing a target over today's upper limit is
    no block: numbers["limit_note"] says it (it can fill on a later day)."""
    later, warns = [], []
    n = {"stop": None, "target": None, "risk_per_share": None, "max_loss": None, "costs": None,
         "reward_risk": None, "limit_note": None}
    order_stop, plan_stop = _price(o.get("stop_loss")), _price(plan.get("stop"))
    stop = order_stop or plan_stop
    target = _price(o.get("take_profit")) or _price(plan.get("target"))
    n["stop"], n["target"] = stop, target
    if order_stop and plan_stop and abs(order_stop - plan_stop) > CENT:
        warns.append("the order's stop_loss %s differs from the plan's stop %s: the check uses the order's"
                     % (_p(order_stop), _p(plan_stop)))
    if not stop:
        if c.get("require_stop", True):
            later.append("stop: no stop. Every entry needs one (\"require_stop\": true): the order's stop_loss "
                         "or the plan's stop, from psx-exit and psx-budget")
        else:
            warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
    elif ref:
        if stop >= ref - CENT:
            later.append("stop: the stop %s must be below the entry %s" % (_p(stop), _p(ref)))
        else:
            risk = Decimal(str(ref)) - Decimal(str(stop))
            n["risk_per_share"] = float(risk.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            if q:
                n["costs"] = round_trip_cost(ref, stop, q, costs)
                n["max_loss"] = max_loss_at_stop(ref, stop, q, costs)
                price_risk = float((risk * Decimal(str(q))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                cap = _f(c.get("max_loss_per_trade_pkr"))
                if cap and n["max_loss"] > cap + CENT:
                    later.append("loss: %s x %s = %s at the stop + %s costs = %s, over your %s max loss "
                                 "per trade (costs included)" % (shares(q), _p(risk), rs(price_risk), rs(n["costs"]),
                                                                 rs(n["max_loss"]), rs(cap)))
                share = costs_vs_risk(ref, stop, q, costs)
                if share is not None and share > COST_WARN:
                    warns.append("Costs are about %d%% of the price risk (over %d%%). You pay them win or lose."
                                 % (whole_pct(share), whole_pct(COST_WARN)))
            min_rr = _f(c.get("min_reward_risk"))
            if target:
                if target <= ref + CENT:
                    later.append("target: the target %s must be above the entry %s" % (_p(target), _p(ref)))
                else:
                    exact = reward_risk(ref, stop, target)
                    n["reward_risk"] = float(Decimal(str(exact)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                    if min_rr and not meets_reward_risk(ref, stop, target, min_rr):
                        more = ""
                        if n["reward_risk"] >= min_rr:          # rounded, it would look like it is enough
                            more = ": %s / %s is only %s" % (_p(Decimal(str(target)) - Decimal(str(ref))), _p(risk),
                                                             Decimal(str(exact)).quantize(Decimal("0.0001"),
                                                                                          rounding=ROUND_FLOOR))
                        later.append("reward:risk: %.2f (target %s, entry %s, stop %s) is under your minimum %s%s"
                                     % (n["reward_risk"], _p(target), _p(ref), _p(stop), _num(min_rr), more))
            elif min_rr:
                later.append("reward:risk: no target to measure it against (min_reward_risk %s)" % _num(min_rr))
    if stop and swing:
        warns.append("the stop %s%s %s only in the plan (PSX has no stop or bracket order): after the buy fills, "
                     "ticket.py protect makes a stop-limit ticket for your broker app; psx-alerts watches %s"
                     % (_p(stop), (" and the target %s" % _p(target)) if target else "",
                        "live" if target else "lives", "both" if target else "it"))
    elif stop:
        warns.append("the stop %s%s %s only in the plan (PSX has no stop or bracket order): set a stop in your "
                     "broker app if it has one; psx-alerts watches %s"
                     % (_p(stop), (" and the target %s" % _p(target)) if target else "",
                        "live" if target else "lives", "both" if target else "it"))
    if lim:
        lo, hi = lim["lower"], lim["upper"]
        where = lim["label"]
        if ref and (ref < lo - CENT or ref > hi + CENT):
            later.append("limits: %s %s is outside today's price limits %s to %s (%s): PSX does not trade "
                         "there today" % (REF_WORDS.get(ref_from, "the entry"), _p(ref), _p(lo), _p(hi), where))
        if target and target > hi + CENT and swing:
            n["limit_note"] = ("the target %s is above today's upper limit %s (%s): it cannot fill today, only on "
                               "a later day" % (_p(target), _p(hi), where))
        elif target and target > hi + CENT:
            later.append("limits: the target %s is above today's upper limit %s (%s), so it cannot fill today"
                         % (_p(target), _p(hi), where))
        if stop and stop < lo - CENT and swing:
            warns.append("the stop %s is below today's lower limit %s (%s): today the price cannot go under "
                         "the limit, so the stop counts from the next session" % (_p(stop), _p(lo), where))
        elif stop and stop <= lo + CENT and swing:      # at the limit: a trade there meets it today
            warns.append("the stop %s is at today's lower limit %s (%s): a trade at the limit meets it today, and "
                         "at the limit there may be no buyers" % (_p(stop), _p(lo), where))
        elif stop and stop <= lo + CENT:
            warns.append("the stop %s is at or below today's lower limit %s (%s): the price cannot trade below "
                         "the limit, and at the limit there may be no buyers" % (_p(stop), _p(lo), where))
        if lim["from"] == "computed":
            warns.append("today's price limits are %s: on an ex-date PSX lowers the base, so the real limits "
                         "can be lower (the company page shows them)" % where)
    return n, later, warns


# ---------------------------------------------------------------- the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None, price_times=None,
          market=None, quotes=None, orders_date=None, plan_doc=None, ex_dates=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...], ...}.

    now          naive UTC (default: the real clock); its Karachi date is "today"
    price_times  {SYM: naive UTC data time of that price}; None = every price counts as fresh
    market       market_facts() output: today's price limits, last closes, the universe
    quotes       data_quotes() output: today's tape and saved page, tried after prices
    orders_date  the Karachi date the orders were made (orders.json created_at), or None
    plan_doc     swing: psx/plan.json, or None (plan_ldcp: the LDCP a swing buy's plan was made on)
    ex_dates     swing: {SYM: ex_events()} of the held shares, or None (none known: no scale
                 record counts, checked_scales)

    The style comes from the config (trade_style: ValueError for any word but day or swing).
    Day style is v1. Swing adds the swing gates (the module docstring) and "style": "swing"."""
    c = dict(DEFAULTS)
    c.update(cfg or {})
    swing = trade_style(c) == "swing"
    costs = trade_costs(c)          # cost_settings(c); in swing every sell pays commission (D30)
    m = {"limits": {}, "unknown": {}, "closes": {}, "pages": {}, "universe": None, "universe_file": None,
         "warnings": []}
    m.update(market or {})
    prices = {str(k).upper(): _f(v) for k, v in (prices or {}).items() if _f(v)}
    now_utc = now or parse_now(None)
    now_pkt = to_pkt(now_utc)
    today = today or now_pkt.date().isoformat()
    paper = c.get("paper", True) is not False       # only JSON false is a live account (as in psx-broker)

    def is_fresh(t):
        return t is not None and -60 <= (now_utc - t).total_seconds() <= FRESH_MINUTES * 60

    def fresh(sym):
        """(price, where) of the first fresh price: psx/prices.json, then today's tape, then
        the saved company page (fresh: 15 minutes old or less by its data time)."""
        if sym in prices and (price_times is None or is_fresh(price_times.get(sym))):
            return prices[sym], "psx/prices.json"
        for qt in (quotes or {}).get(sym) or []:
            if is_fresh(qt.get("time")) and _price(qt.get("price")):
                return qt["price"], qt.get("source") or "the data folder"
        return None, None

    marks = {}                                      # fresh prices, to value what you hold
    for s in set(prices) | set(quotes or {}):
        p = fresh(s)[0]
        if p:
            marks[s] = p
    acct, top_warn = account(c, portfolio, marks)
    acct["ex"] = dict((str(k).strip().upper(), v) for k, v in (ex_dates or {}).items())
    top_warn = list(m["warnings"]) + top_warn
    blocked = blocked_set(c.get("blocked"))
    done_today, unfilled = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss"))
        if ml is None and r.get("by_hand"):
            top_warn.append("a %s of %s recorded by hand today (ticket.py fill) has no max loss in the log; not "
                            "counted in the daily loss" % (r["side"], r["symbol"]))
        elif ml is None:
            top_warn.append("an entry placed today (%s %s) has no max loss in the log; not counted in "
                            "the daily loss" % (r["side"], r["symbol"]))
        else:
            loss_today += ml
    # today's buys with no fill: their cash comes off first in swing and for psx-invest orders (D57)
    waiting, sells_open, old_buys = open_orders(state, today)
    takes_waiting = swing or any(plan_style(o) == "invest" for o in orders)
    waiting_cash = sum(waiting_value(w, costs) or 0.0 for w in waiting)
    if takes_waiting:
        top_warn.extend(waiting_words(waiting, costs, swing))
    elif unfilled:
        top_warn.append("no fill recorded yet for %s, which you said yes to today: the cash here does not take it "
                        "off. When it fills, record it (psx-broker: ticket.py fill)" % ", ".join(unfilled))
    placed_keys = {(r["symbol"], r["side"]) for r in done_today}
    win_reasons, win_warns = ([], []) if swing else window_check(now_pkt, c)
    state_now = market_state(now_pkt, c)
    note = calendar_note(now_pkt.date(), c)
    if note:
        top_warn.append(note)
    if not paper:
        top_warn.append("LIVE account (\"paper\": false): these orders use real money")
    real, real_why = real_ledger(portfolio, state, True) if swing and paper else ("", "")
    if real == "holds":                     # psx-broker refuses a paper fill into it (D39, review F04)
        top_warn.append("paper is on, but psx/portfolio.json holds real shares (\"real_holdings\": true). psx-broker "
                        "records no paper fill in it. Keep paper off here, and try paper in a folder of its own "
                        "(README: \"Paper and real shares\")")
    elif real:
        top_warn.append("paper is on, but psx/portfolio.json may hold real shares: %s. psx-broker records no paper "
                        "fill in it until you say which it is. Real shares: put \"real_holdings\": true at the top, "
                        "and try paper in a folder of its own. A paper file: put \"real_holdings\": false at the top "
                        "(README: \"Paper and real shares\")" % real_why)
    on = [k for k in ("allow_short", "allow_margin", "allow_options") if c.get(k)]
    if on:
        top_warn.append("%s %s on in psx/config.json, but this pack never uses %s: PSX bans blank sales, and there "
                        "is no margin and no options here" % (" and ".join(on), "is" if len(on) == 1 else "are",
                                                              "it" if len(on) == 1 else "them"))
    if m["universe_file"] and m["universe"] is None:
        top_warn.append("there is no %s, so the symbols were not checked against your universe (psx-scanner "
                        "makes it: scan.py universe)" % m["universe_file"])
    old_orders = None
    if orders_date and orders_date != today:
        old_orders = ("date: these orders were made on %s, and today is %s in Karachi. Make a new plan "
                      "(psx-exit, then psx-budget)" % (orders_date, today))
    seen, results = set(), []
    batch_entries, batch_loss = 0, 0.0
    # swing: the limits for the whole account, and what psx/portfolio.json holds now (D29, D55)
    per_trade = _f(c.get("max_loss_per_trade_pkr")) or 0.0
    ledger, batch_open, max_pos, risk_cap, risk_from, gap_cap = {}, [], None, None, "", None
    if swing:
        sw = swing_settings(c)
        max_pos = swing_limit(sw, "max_open_positions", True)
        risk_cap, risk_from = swing_limit(sw, "max_open_risk_pkr"), "swing.max_open_risk_pkr"
        if risk_cap is None:
            risk_cap = _f(c.get("max_daily_loss_pkr"))
            risk_from = "max_daily_loss_pkr, as swing.max_open_risk_pkr is null"
        gap_cap = swing_gap_cap(sw, per_trade)          # 0 is a Rs 0 limit, as in psx-budget (D55)
        ledger = dict((s, v) for s, v in acct["shares"].items() if v > 1e-9)
        for s in sorted(ledger):
            low = lowered_by_hand(s, acct["plans"].get(s), acct["ex"].get(s))
            if low:
                top_warn.append(low)
            why = position_risk(s, ledger[s], acct, per_trade, costs)[1]
            if why:
                top_warn.append(why)
        for w in waiting:
            if _f(w.get("max_loss")) is None:
                top_warn.append("a buy of %s with no fill yet has no max loss in the log: its open risk counts as %s"
                                % (w["symbol"], ("your %s max loss per trade" % rs(per_trade)) if per_trade else "0"))

    for i, o in enumerate(orders, 1):
        warns, info = [], []
        sym = str(o.get("symbol") or "").strip().upper()
        side = str(o.get("side") or "").strip().lower()
        typ = str(o.get("type") or "market").strip().lower()
        tif = str(o.get("time_in_force") or "day").strip().lower()
        board = str(o.get("board") or "REG").strip().upper()
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        fp = fingerprint(o)
        reasons, q = format_check(o, sym, side, typ, tif, board, state_now, swing)
        always, later, more, sort = asset_check(sym, m)
        warns += more
        lim = None if always else m["limits"].get(sym)      # no limit checks for what is not a share

        # entry or exit: an exit only cuts what you hold
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = side == "sell" and held > 1e-9 and q is not None and q <= held + 1e-9
        kind = "exit" if exiting else "entry"
        if always and kind == "exit" and sort not in NOT_REG and sort != "ordinary":
            later = always + later          # getting out of a held ETF, right, REIT ...: a warning only
        else:
            reasons += always
        if old_orders:
            later.append(old_orders)
        style = plan_style(o)
        if kind == "entry" and side == "buy" and style != "invest":     # D31: styles do not mix
            if swing and style in ("", "day"):
                later.append("style: this is %s, and your style is swing (psx/config.json). Make a swing plan with "
                             "psx-exit, then size it with psx-budget" % ("a day plan" if style else "a buy with no plan"))
            elif not swing and style == "swing":
                later.append("style: this is a swing plan, and your style is day (psx/config.json). Make a new plan "
                             "with psx-exit")
            elif style not in ("", "day", "swing"):
                later.append("style: the plan's style is %r, and a plan is day, swing or invest. Make a new plan with "
                             "psx-exit" % (plan.get("style"),))

        # the reference price: what one share costs to get in (or what a sell gets)
        plan_entry = _price(plan.get("entry"))
        live, live_from = fresh(sym)
        ref, ref_from = None, None
        if typ == "limit":
            ref, ref_from = _price(o.get("limit_price")), "limit"
        elif swing and typ == "stop_limit":
            ref, ref_from = _price(o.get("limit_price")), "stop limit"
        if ref is None:
            cands = [(p, w) for p, w in ((plan_entry, "plan"), (live, "price")) if p]
            if kind == "entry" and typ == "market" and live is None:
                cands = []      # a market buy is never measured from the plan's entry alone: it can be hours old
            if cands:
                ref, ref_from = max(cands, key=lambda x: x[0])
                ref = round_half_up_tick(ref)
                if live and ref_from == "price" and plan_entry and live > plan_entry + CENT:
                    info.append("%s price %s is above the plan's entry %s: the risk is measured from %s"
                                % (sym, _p(ref), _p(plan_entry), _p(ref)))
            elif kind == "exit" and sym in prices:
                ref, ref_from = prices[sym], "stale price"      # only to show what an exit is worth
        if kind == "exit" and plan_entry and live is None and sym in prices and price_times is not None and typ == "market":
            warns.append("the %s price in psx/prices.json is older than %d minutes by its data time: not used "
                         "(free PSX data is about 5 minutes late)" % (sym, FRESH_MINUTES))
        ref_words = REF_WORDS.get(ref_from, ref_from)
        if ref_from == "price" and live_from != "psx/prices.json":
            ref_words = "the latest price, on %s" % live_from
        value = q * ref if (q and ref) else None
        one_side = (side == "sell" and costs.get("day_trade_one_side") is True and q is not None
                    and same_day_sell(acct["lots"].get(sym), q, today))
        leg = leg_cost(ref, q, costs, not one_side) if value is not None else None
        if kind == "entry" and ref is None and q and typ != "limit":
            why = (("the %s price in psx/prices.json is not fresh: not within %d minutes of now by its data time"
                    % (sym, FRESH_MINUTES)) if sym in prices else
                   "no %s price in psx/prices.json, on today's tape or on a saved company page" % sym)
            if plan_entry:
                later.append("price: a market order needs a fresh price to measure its risk, and the plan's entry %s "
                             "can be hours old (%s). Get a fresh price (psx-alerts: alerts.py quote %s --write) or use "
                             "a limit order" % (_p(plan_entry), why, sym))
            else:
                later.append("price: no fresh price and no plan entry to measure the risk from (%s). Get a fresh "
                             "price (psx-alerts: alerts.py quote %s --write) or use a limit order" % (why, sym))
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            if value < float(c.get("min_order_pkr") or 0) - CENT:
                later.append("size: %s is under your %s minimum order" % (rs(value), rs(c["min_order_pkr"])))
            if c.get("max_order_pkr") and value > float(c["max_order_pkr"]) + CENT:
                later.append("size: %s is over your %s max order" % (rs(value), rs(c["max_order_pkr"])))

        # account effects, simulated in order (a buy pays its costs too; no margin)
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_pkr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        mine = [w for w in waiting if w["fingerprint"] != fp]       # today's other buys with no fill yet
        held_back, held_for = 0.0, ""
        if side == "buy" and kind == "entry" and (swing or style == "invest") and mine:      # D57
            priced = [(w["symbol"], waiting_value(w, costs)) for w in mine]
            no_price = sorted(set(s for s, v in priced if v is None))
            if no_price:
                later.append("cash: an earlier buy today (%s) has no price in the log, so the cash cannot be checked. "
                             "Record its fill, or record it expired" % ", ".join(no_price))
            held_back = round(sum(v for _, v in priced if v is not None), 2)
            if held_back:
                cash = round(cash - held_back, 2)
                held_for = " once today's buys with no fill yet are taken off (%s: %s, costs included)" % (
                    ", ".join(sorted(set(s for s, v in priced if v is not None))), rs(held_back))
                # they count as held at qty x entry_ref, as if filled: their costs are gone. This is the
                # account total psx-budget uses, so the buffer and the position cap match it (D57)
                worth = round(sum(waiting_worth(w) or 0.0 for w in mine if waiting_value(w, costs) is not None), 2)
                total = round(cash + sum(acct["values"].values()) + worth, 2)
                buffer_pkr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy":
            after_cash = cash - value - leg
            if after_cash < -CENT:
                later.append("cash: needs %s plus %s costs but there is only %s cash%s; that would be margin "
                             "(borrowed money), and this pack never uses margin" % (rs(value), rs(leg), rs(cash),
                                                                                   held_for))
            elif after_cash < buffer_pkr - CENT:
                later.append("cash: leaves %s cash after costs%s, under your %s%% buffer of %s"
                             % (rs(max(after_cash, 0)), held_for, _pct(c["cash_buffer_pct"]), rs(buffer_pkr)))
            pos_after = held_value + value
            cap_pos = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > cap_pos + CENT:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                             % (sym, rs(pos_after), 100.0 * pos_after / total, rs(total),
                                _pct(c["max_position_pct"])))
        if side == "sell" and sym and kind == "entry":
            reasons.append("short: selling more %s than you hold (%s). PSX bans blank sales (selling shares "
                           "you do not hold), whatever \"allow_short\" says. If you bought it, record the fill first "
                           "(psx-broker: ticket.py fill)" % (sym, shares(held)))
        if swing and typ == "stop_limit":                   # a stop-limit sells shares you hold, and only later
            trig = _price(o.get("trigger_price"))
            if side != "sell" or kind != "exit":
                reasons.append("format: a stop-limit order is for selling shares you hold only")
            elif trig:
                now_p, now_from = live, "the price now"
                if now_p is None and lim and lim.get("from") != "computed" and _price(lim.get("ldcp")):
                    now_p, now_from = lim["ldcp"], "the last close"         # today's LDCP: an ex-date lowers it
                elif now_p is None and (m["closes"].get(sym) or {}).get("close"):
                    now_p, now_from = m["closes"][sym]["close"], "the last close"
                if now_p is None:
                    warns.append("no price for %s now and no last close, so the trigger %s was not checked against "
                                 "the price" % (sym, _p(trig)))
                elif trig >= now_p - CENT:
                    reasons.append(trigger_words(sym, trig, now_p, now_from, acct["plans"].get(sym),
                                                 ex_seen(sym, m, lim, today, c)))
                else:
                    info.append("stop-limit: the trigger %s is under %s (%s)" % (_p(trig), now_from, _p(now_p)))
        open_sell = False
        if swing and side == "sell" and kind == "exit" and q is not None:      # D58: no second sell of the same shares
            others = [s for s in sells_open if s["symbol"] == sym and s["fingerprint"] != fp]
            # a sell filled in part still holds the rest ("left"); a log with no qty: all you hold
            taken = sum(s["left"] if s.get("left") is not None else held for s in others)
            if others and q > held - taken + 1e-9:
                reasons.append(open_sell_words(sym, min(taken, held), others))
                open_sell = True

        # checks of a buy: stop, loss, reward:risk, limits, daily loss, trades, window (and the swing gates)
        n = {"stop": None, "target": None, "risk_per_share": None, "max_loss": None, "costs": None,
             "reward_risk": None}
        gap, gap_low, this_risk, open_line = None, None, 0.0, None
        if kind == "entry" and side == "buy":
            n, more_later, more_warns = buy_checks(o, plan, ref, ref_from, q, c, costs, lim, swing)
            later += more_later
            warns += more_warns
            daily = _f(c.get("max_daily_loss_pkr"))
            if daily and n["max_loss"] is not None:
                used = loss_today + batch_loss
                if used + n["max_loss"] > daily + CENT:
                    later.append("daily loss: %s already at risk today plus %s for this one = %s, over your %s "
                                 "daily loss limit (costs included)" % (rs(used), rs(n["max_loss"]),
                                                                        rs(used + n["max_loss"]), rs(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries placed today plus %d passed earlier in this batch, your limit "
                             "is %d a day" % (len(entries_today), batch_entries, cap_t))
            later.extend(win_reasons)
            warns.extend(win_warns)
            if swing:                                       # D29, D33, D55, D57: the account across days
                why = swing_window(now_pkt, typ, c)
                if why:
                    later.append("window: %s (%s %s Karachi time)" % (why, day_label(now_pkt.date()),
                                                                       now_pkt.strftime("%H:%M")))
                if style == "swing" and not always:         # review F41: a plan on the prices of an earlier day
                    base, base_words = plan_ldcp(sym, plan, plan_doc, m, today)
                    more_later, more_warns = ldcp_words(sym, lim, base, base_words, n["stop"],
                                                        (m.get("pages") or {}).get(sym))
                    later += more_later
                    warns += more_warns
                more_later, more_warns, open_line, this_risk = swing_gates(
                    sym, n, acct, ledger, mine, batch_open, old_buys, max_pos, risk_cap, risk_from, per_trade, costs)
                later += more_later
                warns += more_warns
                if ref and q:
                    gap, gap_low = gap_loss(ref, q, costs), price_limits(ref)[0]
                    if gap_cap is not None and gap > gap_cap + CENT:
                        later.append("gap: %s if it opens at the lower limit %s, over your %s gap limit "
                                     "(swing.max_gap_loss_pkr)" % (rs(gap), _p(gap_low), rs(gap_cap)))
            if n["stop"] and ref and n["risk_per_share"]:
                info.append("entry %s (%s), stop %s, target %s: risk %s a share"
                            % (_p(ref), ref_words, _p(n["stop"]),
                               _p(n["target"]) if n["target"] else "none", _p(n["risk_per_share"])))
            if n["max_loss"] is not None:
                cap = _f(c.get("max_loss_per_trade_pkr"))
                min_rr = _f(c.get("min_reward_risk"))
                info.append("max loss %s%s, costs included, reward:risk %s%s"
                            % (rs(n["max_loss"]), (" (limit %s)" % rs(cap)) if cap else "",
                               ("%.2f" % n["reward_risk"]) if n["reward_risk"] is not None else "n/a",
                               (" (min %s)" % _num(min_rr)) if min_rr else ""))
            if gap is not None:
                info.append("gap loss %s if it opens at the lower limit %s%s"
                            % (rs(gap), _p(gap_low), (" (limit %s)" % rs(gap_cap)) if gap_cap is not None else ""))
            if daily or cap_t:
                info.append("today before this: %d of %s trades, %s of %s daily loss"
                            % (len(entries_today) + batch_entries, cap_t or "unlimited",
                               rs(loss_today + batch_loss), rs(daily) if daily else "no limit on"))
            if open_line:
                info.append(open_line)
            if lim:
                info.append("today's price limits %s to %s (%s)" % (_p(lim["lower"]), _p(lim["upper"]), lim["label"]))
            elif sym in m["unknown"] and not always:
                warns.append("today's price limits for %s are not known, so they were not checked. %s"
                             % (sym, m["unknown"][sym]))
            if n.get("limit_note"):
                info.append(n["limit_note"])
        elif kind == "exit":
            priced = typ == "limit" or (swing and typ == "stop_limit")
            if lim and priced and ref and (ref < lim["lower"] - CENT or ref > lim["upper"] + CENT):
                later.append("limits: the limit price %s is outside today's price limits %s to %s (%s): PSX "
                             "does not trade there today" % (_p(ref), _p(lim["lower"]), _p(lim["upper"]), lim["label"]))
            if lim and ref and ref <= lim["lower"] + CENT:
                warns.append("%s is at today's lower limit %s (%s): a sell may not fill, as there may be no buyers"
                             % (sym, _p(lim["lower"]), lim["label"]))
            if state_now["state"] not in ("open", "pre-open"):
                warns.append("the market is not open now: %s" % state_now["reason"])
            if value is not None:
                info.append("value %s at %s (%s), sell costs about %s"
                            % (rs(value), _p(ref), ref_words, rs(leg)))

        # duplicates and the daily order limit
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys and not open_sell:        # the "short:" block above says it, with the cancel line
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                         "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry" and side == "buy":
                batch_entries += 1
                batch_loss += n["max_loss"] or 0.0
                batch_open.append((sym, this_risk))
            if swing and side == "sell" and q is not None and sym in ledger:
                ledger[sym] = max(ledger[sym] - q, 0.0)
            if side == "buy":
                if value is not None:
                    acct["cash"] = round(acct["cash"] - value - leg, 2)
                    acct["values"][sym] = round(acct["values"].get(sym, 0.0) + value, 2)
                if q is not None:
                    acct["shares"][sym] = held + q
            elif side == "sell":
                if value is not None:
                    acct["cash"] = round(acct["cash"] + value - leg, 2)
                    acct["values"][sym] = max(round(acct["values"].get(sym, 0.0) - value, 2), 0.0)
                if q is not None:
                    acct["shares"][sym] = held - q
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind,
            "amount": shares(q) if q is not None else ("qty %r" % (o.get("qty"),)),
            "value_pkr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": n["stop"], "target": n["target"], "risk_per_share": n["risk_per_share"],
            "max_loss": n["max_loss"], "costs": n["costs"], "reward_risk": n["reward_risk"],
            "limits": lim, "fingerprint": fp, "verdict": verdict,
            "reasons": reasons, "warnings": warns, "info": info,
        })
        if swing:
            results[-1].update(gap_loss=gap, gap_low=gap_low)

    passed = sum(1 for r in results if r["verdict"] == "PASS")
    out = {
        "date": today,
        "now_karachi": now_pkt.strftime("%Y-%m-%d %H:%M"),
        "paper": paper,
        "results": results,
        "pass": passed,
        "block": len(results) - passed,
        "warnings": top_warn,
        "notes": ([GAP_NOTE] if swing else []) + ([HALT_NOTE] if passed else []),
        "cash_after": round(acct["cash"] - (waiting_cash if takes_waiting else 0.0), 2),
        "today": {"orders_placed": count_today, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2)},
    }
    if swing:
        out["style"] = "swing"
    if real:
        out["real_ledger"] = real               # "holds" or "may hold": the report's last line says so
    return out


# ---------------------------------------------------------------- report and command line

def wrap(text, first, rest=None):
    """One report line, wrapped at WIDTH characters. "Rs 5,000" never breaks after "Rs"."""
    rest = first if rest is None else rest
    out = textwrap.wrap(text.replace("Rs ", "Rs\xa0"), width=WIDTH, initial_indent=first,
                        subsequent_indent=rest, break_long_words=False, break_on_hyphens=False)
    return [x.replace("\xa0", " ") for x in out] or [first.rstrip()]


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.extend(wrap("WARNING: " + w, "", " " * 9))
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s, %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"],
                                                r["kind"]))
        for x in r.get("info", []):
            lines.extend(wrap(x, " " * 10))
        for x in r["reasons"]:
            lines.extend(wrap(x, "        - ", " " * 10))
        for x in r["warnings"]:
            lines.extend(wrap(x, "        ~ ", " " * 10))
    lines.append("")
    if res.get("real_ledger") and res["paper"]:     # paper on, and a ledger of real shares (review F04)
        account_words = "paper is on, but psx/portfolio.json %s real shares" % res["real_ledger"]
    else:
        account_words = "%s account" % ("paper" if res["paper"] else "LIVE")
    lines.append("%d passed, %d blocked at %s Karachi time. Cash after the passed orders: %s (%s)."
                 % (res["pass"], res["block"], res["now_karachi"], rs(res["cash_after"]), account_words))
    if res["block"]:
        lines.append("Blocked orders get no ticket from psx-broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. psx-broker still asks you yes or no for each one.")
    for x in res.get("notes", []):
        lines.extend(wrap(x, ""))
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits and the PSX rules "
                                             "(psx-risk).")
    ap.add_argument("--orders", default="psx/orders.json")
    ap.add_argument("--config", default="psx/config.json")
    ap.add_argument("--portfolio", default="psx/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="psx/prices.json", help="the latest quotes (psx-alerts writes them)")
    ap.add_argument("--state", default="psx/state/broker.json", help="what psx-broker recorded today")
    ap.add_argument("--universe", default="psx/universe.txt", help="your ordinary PSX shares (psx-scanner)")
    ap.add_argument("--data", default="psx/data", help="the PSX data folder (company/, mkt_summary/)")
    ap.add_argument("--plan", default="psx/plan.json",
                    help="swing: psx-exit's plan, for the LDCP the plan was made on (read only)")
    ap.add_argument("--alerts", default="psx/state/alerts.json",
                    help="swing: psx-alerts' record of ex-dates, for ticket.py stop --scale (read only)")
    ap.add_argument("--offline", action="store_true", help="never use the network: read the data folder only")
    ap.add_argument("--now", help="pretend it is this Karachi time, e.g. \"2026-10-29 10:15\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in Karachi)")
    ap.add_argument("--out", default="psx/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        try:
            now = parse_now(a.now)
        except ValueError as e:
            raise RiskError(str(e))
        if a.today:
            try:
                ok = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", a.today)) and bool(datetime.date.fromisoformat(a.today))
            except ValueError:
                ok = False
            if not ok:
                raise RiskError("--today wants a date like 2026-10-29")
        doc = read_json(a.orders, "orders file (run psx-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        if not all(isinstance(o, dict) for o in orders):
            raise RiskError("every order in %s must be a JSON object" % a.orders)
        cfg = read_json(a.config, "config", required=a.config != "psx/config.json") or {}
        if not isinstance(cfg, dict):
            raise RiskError("%s must be a JSON object" % a.config)
        portfolio = read_json(a.portfolio, "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        pdoc = pdoc if isinstance(pdoc, dict) else {}
        prices, times, details = load_prices(pdoc, cfg.get("data_delay_minutes", DEFAULTS["data_delay_minutes"]))
        state = read_json(a.state, "broker state", required=False)
        today = a.today or to_pkt(now).date().isoformat()
        syms = [str(o.get("symbol") or "").strip().upper() for o in orders]
        offline = a.offline or a.now is not None        # a replay never asks the site: it only has today
        market = market_facts(syms, today, a.data, cfg, offline, details, times, a.universe)
        made = parse_utc(doc.get("created_at")) if isinstance(doc, dict) else None
        held = [str(p.get("ticker") or p.get("symbol") or "").strip().upper()
                for p in (portfolio.get("positions") or []) if isinstance(p, dict)] if isinstance(portfolio, dict) else []
        try:
            swing = trade_style(cfg) == "swing"
        except ValueError:
            swing = False                               # check() says why, in one plain line
        plan_doc = ex = None
        if swing:                                       # read only; a broken file never stops a check
            plan_doc, why = read_optional(a.plan, "the LDCP the plan was made on is taken from the closing files")
            alerts, why_alerts = read_optional(a.alerts, "psx-alerts' record of ex-dates was not read")
            market["warnings"] += [w for w in (why, why_alerts) if w]
            ex = scale_events(portfolio, datetime.date.fromisoformat(today), a.data, alerts, cfg)
        res = check(orders, cfg, portfolio, prices, state, today, now, times, market,
                    data_quotes(sorted(set(syms + [h for h in held if h])), now, a.data),
                    to_pkt(made).date().isoformat() if made else None, plan_doc, ex)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = utc_iso(now)       # the --now time: psx-broker reads the verdict's age
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, a.out)
    except (RiskError, ValueError) as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
