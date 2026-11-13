#!/usr/bin/env python3
"""Exit Planner for psx-exit: sets the stop and the target BEFORE you enter, so
you know when to get out. Pakistan Stock Exchange (PSX), REG board. Standard
library only, Python 3.8. Long side only. It never places an order.

    python3 exit.py plan                                    # from psx/entry.json: or-mid stop, 2R target
    python3 exit.py plan --stop or-mid --target 1R --write  # the example: stop 99.55, target 101.75
    python3 exit.py plan --symbol DEMO --entry 100.65 --or-high 100.20 --or-low 98.90
    python3 exit.py plan --stop atr:1.5                     # ATR needs today's bars (a bars file or a tape)
    python3 exit.py plan --symbol OGDC --entry 283.29 --setup level --stop pct:1
    python3 exit.py show                                    # the current psx/plan.json

Stops (a long's stop rounds DOWN to the Rs 0.01 tick, away from the entry):
  or-mid     the opening-range midpoint (the default for an orb entry)
  or-low     the opening-range low
  price:X    your price
  pct:X      X percent under the entry
  atr:K      K times the 14-bar ATR under the entry, on today's bars up to the
             entry bar: --bars FILE, else <data>/bars/<SYM>.csv, else the
             symbol tape, else the market tape. With fewer than 14 bars by then
             it averages all of them and says so.
  A level setup has no opening-range default: give a stop.

Targets (round half up to the tick):
  2R (default), 1R, 1.5R ...   entry + R x risk, where risk = entry - stop
  price:X                      your price

Today's price limits (the PSX circuit breaker): the company page's CIRCUIT
BREAKER when the page is for the plan's date (the PSX site first when it is on,
else the saved page; then the symbol tape). Else computed (LDCP x 0.90 and
x 1.10, or Rs 1 either side) from the LDCP in the day's own closing file, else
from the last close (an ex-date lowers it). Else unknown.

Refused, with the reason: a stop at or above the entry, a target at or below
it, an entry or a target above today's upper limit or an entry under its lower
limit (it cannot fill today), an entry at 12:00 PM on a Friday or at the close
(nothing trades after it), a risk under 0.05% of the price or under Rs 0.02
(too tight to survive the spread), an or-mid or or-low stop from an opening
range psx-entry saw only part of, an entry.json that has not triggered (unless
you give --entry), a short (PSX bans blank sales), an entry at or after
flat_by, a day PSX is closed.

Warned: a stop at or under today's lower limit (at the limit there may be no
buyers, so you may not get out), and costs over 25% of the price risk
(costs_vs_risk of 1,000 shares: the rule psx-budget and psx-risk use too).

flat_by (default 15:20, or "flat_by" in psx/config.json) is written for Monday
to Thursday and moved to the day's hours (config_time): 16:20 on a Friday,
13:20 on a Ramadan Monday. "min_reward_risk" in the config (default 1.0) only
warns here; psx-risk enforces it. Costs use the config's "costs" block.

Data: psx/data (or --data DIR) only, unless psx/config.json says
"psx_site": true. --offline never touches the network, and a replay with
--now never asks the PSX site (it only has today).

Swing style ("style": "swing" in psx/config.json, with its "swing" block):
  python3 exit.py plan --symbol DEMO --write     # the evening before: a limit buy for the next session
  The buy day: --date, else today until PSX closes, else the next PSX session.
  Entry: the last close + swing.entry_buffer_pct (1%), or --entry. A limit buy, a day order.
  Data: the newest 60 closing files before the buy day; 20 daily bars of the share are needed,
  and the newest file must be the session before the buy day.
  Stops (swing.stop, default atr:2), rounded down to the tick:
    atr:K      K times the daily ATR(14) under the entry, from the closing files (payouts and
               splits adjusted, so an ex-date is not a range)
    low:N      one tick under the lowest low of the last N sessions (adjusted)
    level:K    K times the daily ATR under the scan's level in psx/watchlist.json (same close)
    price:X    pct:X as above. or-mid and or-low are for a day trade: refused.
  Target: swing.target (2R). Over the buy day's upper limit it is a note: a later day can fill it.
  Exit by: swing.max_hold_sessions (20) sessions after the buy day, by flat_by that day.
  Trend rule: out after a close under the lowest close of swing.trend_exit_sessions (10)
  sessions before it. Costs count commission on both sides. Gap risk: the loss a share,
  costs included, if the next session opens at its lower limit. A board meeting in
  psx/metrics.json before the exit-by day is a note. The day flags --or-high, --or-low,
  --time, --entry-bar, --interval, --flat-by and --bars are refused.

A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
Exit codes: plan 0 = a plan, 1 = refused or an error. show 0 = a plan exists.
"""

import argparse
import bisect
import csv
import datetime
import gzip
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

INTERVALS = {"1m": 1, "5m": 5}     # bar size in minutes
DEFAULTS = {"flat_by": "15:20", "min_reward_risk": 1.0}
ATR_BARS = 14
MIN_RISK_PCT = Decimal("0.05")     # a risk under 0.05% of the price is inside the spread's noise
MIN_RISK = Decimal("0.02")         # and never under two ticks: one tick is noise on a cheap share
TICK = Decimal("0.01")             # the PSX tick, at every price
ENTRY_PATH = "psx/entry.json"
PLAN_PATH = "psx/plan.json"
CONFIG_PATH = "psx/config.json"
DATA_DIR = "psx/data"
NOTE = "A stop is a plan, not a guarantee: a gap or a fast market can fill past it."
NO_STOP_ORDER = "PSX has no stop order: sell at the stop yourself, or use your broker app's stop feature."
LATE = "Free PSX prices are about 5 minutes late: watch the stop on your broker's screen."
LONGS_ONLY = ("this pack plans longs only: PSX bans blank sales (selling shares you do not hold). "
              "Plan a buy instead.")
# swing style ("style": "swing" in psx/config.json)
SWING_FILES = 60                   # a swing plan reads the newest 60 closing files before the buy day
SWING_MIN_BARS = 20                # and needs 20 daily bars of the share in them
WATCHLIST_PATH = "psx/watchlist.json"
METRICS_PATH = "psx/metrics.json"
SWING_SETUPS = {"breakout_20d": "20-day closing-high breakout", "bull_flag": "bull flag",
                "reclaim_ma20": "back over the 20-day average", "oversold": "oversold (RSI under 30)",
                "oversold_bounce": "oversold bounce", "orb": "opening-range breakout",
                "level": "close above a level"}
SWING_STOPS = "A swing stop is atr:K, low:N, level:K, price:X or pct:X."
OVERNIGHT = "Held overnight: a gap can open past the stop. Then a sell fills at the open, not at the stop."
DAY_FLAGS = (   # (argparse name, flag, why a swing plan refuses it)
    ("or_high", "--or-high", "an opening-range stop is for a day trade. " + SWING_STOPS),
    ("or_low", "--or-low", "an opening-range stop is for a day trade. " + SWING_STOPS),
    ("time", "--time", "a swing buy is a limit order for the whole buy day, so it has no entry time."),
    ("entry_bar", "--entry-bar", "a swing plan reads daily bars from the closing files, not 5-minute bars."),
    ("interval", "--interval", "a swing plan reads daily bars from the closing files, not 5-minute bars."),
    ("flat_by", "--flat-by", "a swing plan has an exit-by day instead. Set swing.max_hold_sessions in "
                             "psx/config.json."),
    ("bars", "--bars", "a swing stop comes from the closing files, not from a bars file."),
)

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
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name


# ---------------------------------------------------------------- time (copied from tests/psx_core.py, word for word)

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


def ts_to_pkt(ts):
    """Unix seconds -> naive Karachi time. Never uses the machine's time zone."""
    return to_pkt(datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)))


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


# ---------------------------------------------------------------- PSX calendar (copied)

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


# ---------------------------------------------------------------- bars grid (copied)

def slot_spans(d, minutes=5, cfg=None):
    """[(start, end)] of date d's bars as naive Karachi datetimes, across the segments
    (the Friday break has none). A bar ends `minutes` after its start, or at the segment
    end when the bar size does not fit the segment. [] when PSX is closed."""
    if minutes != int(minutes) or int(minutes) < 1:
        raise ValueError("bar minutes must be a whole number, 1 or more")
    step = datetime.timedelta(minutes=int(minutes))
    out = []
    for a, b in session(d, cfg) or ():
        t, end = datetime.datetime.combine(d, a), datetime.datetime.combine(d, b)
        while t < end:
            out.append((t, min(t + step, end)))
            t += step
    return out


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


# ---------------------------------------------------------------- prices and costs (copied)

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


# ---------------------------------------------------------------- PSX files and data access (copied)

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


# ---------------------------------------------------------------- tape and bars (copied)

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


def bars_from_tape(snaps, d, symbol=None, minutes=5, cfg=None):
    """Tape snapshots of date d -> bars, a list aligned with slots(d, minutes, cfg).

    Each item is None (no snapshot in that slot) or {"start", "open", "high", "low",
    "close", "volume", "n"}. A snapshot goes to the slot whose [start, end) holds its
    "asof" (Karachi time; "Z" or an offset is turned into Karachi time); others are
    dropped. A repeat (same asof, same numbers) counts once. open = the first price in
    the slot, but bar 0 takes the company page's day open. high and low = the prices
    seen, widened to the page's day high (low) when it is new since an earlier snapshot
    showed one; bar 0 also takes the first one. close = the last price; n = the
    snapshots used. volume = the page's day volume by the slot end minus the volume
    before the slot, each the highest seen so far, so a dip never counts twice
    (company snapshots only; else None). Screener snapshots need `symbol`, and so does
    a tape with company snapshots of two symbols (ValueError: they would mix). A tape
    only knows the prices it saw, about 5 minutes late."""
    spans = slot_spans(d, minutes, cfg)
    starts = [s for s, _ in spans]
    out = [None] * len(spans)
    want = str(symbol).strip().upper() if symbol else None
    items, keys, syms = [], set(), set()
    for i, s in enumerate(snaps or []):
        if not isinstance(s, dict):
            continue
        t = str(s.get("asof") or "").strip()
        try:
            when = datetime.datetime.fromisoformat(t[:-1] + "+00:00" if t[-1:] in ("Z", "z") else t)
        except ValueError:
            continue
        if when.tzinfo is not None:
            when = to_pkt(when)
        if when.date() != d:
            continue
        src = str(s.get("source") or "")
        company = src == "company" or (src != "screener" and "prices" not in s)
        if company and want and str(s.get("symbol") or want).strip().upper() != want:
            continue
        if company and not want and str(s.get("symbol") or "").strip():
            syms.add(str(s.get("symbol")).strip().upper())
        price = num(s.get("price")) if company else num((s.get("prices") or {}).get(want) if want else None)
        key = (when, company, price, str(s.get("symbol") or "").strip().upper() if company else "")
        key += tuple(num(s.get(x)) for x in ("open", "high", "low", "volume")) if company else ()
        if key in keys:
            continue
        keys.add(key)
        items.append((when, i, s, price, company))
    if len(syms) > 1:
        raise ValueError("the tape holds snapshots of %s: say which symbol you want" % ", ".join(sorted(syms)))
    items.sort(key=lambda x: (x[0], x[1]))
    day_hi, day_lo, vols, opened = None, None, [], set()
    for when, _, s, price, company in items:
        k = bisect.bisect_right(starts, when) - 1
        inside = k >= 0 and when < spans[k][1]
        b = None
        if inside and price is not None and price > 0:
            b = out[k]
            if b is None:
                b = out[k] = {"start": starts[k], "open": price, "high": price, "low": price, "close": price,
                              "volume": None, "n": 0}
            b["high"], b["low"], b["close"] = max(b["high"], price), min(b["low"], price), price
            b["n"] += 1
        if not company:
            continue
        op, hi, lo, v = num(s.get("open")), num(s.get("high")), num(s.get("low")), num(s.get("volume"))
        if b is not None:
            if k == 0 and op and op > 0 and k not in opened:
                opened.add(k)
                b["open"], b["high"], b["low"] = op, max(b["high"], op), min(b["low"], op)
            if hi and hi > 0 and (hi > day_hi if day_hi else k == 0):
                b["high"] = max(b["high"], hi)
            if lo and lo > 0 and (lo < day_lo if day_lo else k == 0):
                b["low"] = min(b["low"], lo)
        if hi and hi > 0:
            day_hi = max(day_hi or 0, hi)
        if lo and lo > 0:
            day_lo = min(day_lo, lo) if day_lo else lo
        if v is not None:
            vols.append((when, v))
    for k, b in enumerate(out):
        if b is None:
            continue
        start, end = spans[k]
        if not any(start <= w < end for w, _ in vols):
            continue
        before = [v for w, v in vols if w < start]
        base = max(before) if before else (0 if k == 0 else None)
        if base is not None:
            b["volume"] = int(max(0, max(v for w, v in vols if w < end) - base))
    return out


def last_asof(snaps):
    """The newest "asof" among snapshots as a naive Karachi datetime, or None. One with
    "Z" or an offset is turned into Karachi time."""
    best = None
    for s in snaps or []:
        try:
            t = str(s.get("asof") or "").strip()
            when = datetime.datetime.fromisoformat(t[:-1] + "+00:00" if t[-1:] in ("Z", "z") else t)
        except (AttributeError, ValueError):
            continue
        if when.tzinfo is not None:
            when = to_pkt(when)
        if best is None or when > best:
            best = when
    return best


def read_bars_csv(text):
    """A bars file exported from a broker terminal or vendor -> {date: [bars]}, each list
    oldest first, each bar {"start", "open", "high", "low", "close", "volume"}.

    The header names the columns (any case): datetime, timestamp or time (or date plus
    time), open, high, low, close, and volume (optional). Times are the bar START in
    Karachi time: 2026-10-29 10:15[:00], 2026-10-29T10:15, 29/10/2026 10:15 (day first)
    or Unix seconds; a time with Z or an offset is turned into Karachi time. Rows that
    do not read are skipped. ValueError when no bar is left, or when a date shows the
    file is month first (10/29/2026): such a file would put bars on the wrong days."""
    lines = [ln for ln in str(text or "").lstrip(chr(0xfeff)).splitlines() if ln.strip()]
    if not lines:
        raise ValueError("the bars file is empty")
    head = lines[0]
    delim = "," if "," in head else (";" if ";" in head else ("\t" if "\t" in head else ","))
    rows = list(csv.reader(lines, delimiter=delim))
    cols = [c.strip().strip('"').lower() for c in rows[0]]

    def col(*names):
        for n in names:
            if n in cols:
                return cols.index(n)
        return None

    i_dt, i_date, i_time = col("datetime", "date_time", "date time", "timestamp"), col("date"), col("time")
    i_o, i_h, i_l, i_c, i_v = col("open"), col("high"), col("low"), col("close"), col("volume", "vol")
    if None in (i_o, i_h, i_l, i_c) or (i_dt is None and i_date is None and i_time is None):
        raise ValueError("a bars file needs a header with datetime (or date and time), open, high, low and close")
    tail = r"[T ]+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*([AaPp][Mm])?\s*(Z|[+-]\d{2}:?\d{2})?$"
    month_first = []

    def start_of(t):
        t = t.strip()
        if re.match(r"^\d{9,13}(\.\d+)?$", t):
            sec = float(t)
            return ts_to_pkt(sec / 1000.0 if sec > 1e11 else sec)
        m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})" + tail, t)
        if m:
            y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})" + tail, t)
            if not m:
                return None
            dd, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if mo > 12 >= dd:
                month_first.append(t)
        h, ap = int(m.group(4)), (m.group(7) or "").upper()
        if ap == "PM" and h < 12:
            h += 12
        elif ap == "AM" and h == 12:
            h = 0
        try:
            dt = datetime.datetime(y, mo, dd, h, int(m.group(5)), int(m.group(6) or 0))
        except ValueError:
            return None
        z = m.group(8)
        if z:
            off = 0 if z.upper() == "Z" else (int(z[1:3]) * 60 + int(z[-2:])) * (1 if z[0] == "+" else -1)
            dt = to_pkt(dt - datetime.timedelta(minutes=off))
        return dt

    out = {}
    for r in rows[1:]:
        r = r + [""] * (len(cols) - len(r))
        if i_dt is not None:
            t = r[i_dt]
        elif i_date is not None and i_time is not None:
            t = "%s %s" % (r[i_date].strip(), r[i_time].strip())
        else:
            t = r[i_time if i_time is not None else i_date]
        start = start_of(t)
        o, h, lo, c = num(r[i_o]), num(r[i_h]), num(r[i_l]), num(r[i_c])
        if start is None or None in (o, h, lo, c) or min(o, h, lo, c) <= 0 or h < lo:
            continue
        v = num(r[i_v]) if i_v is not None else None
        out.setdefault(start.date(), {})[start] = {"start": start, "open": o, "high": h, "low": lo, "close": c,
                                                   "volume": int(v) if v is not None else None}
    if month_first:
        raise ValueError("the bars file looks month first (%s): save it with the day first (29/10/2026) or as "
                         "2026-10-29" % month_first[0].strip())
    if not out:
        raise ValueError("no bars read: times must look like 2026-10-29 10:15 or 29/10/2026 10:15 "
                         "(the bar start, Karachi time)")
    return dict((d, [bars[k] for k in sorted(bars)]) for d, bars in out.items())


def resample_bars(bars, d, minutes=5, cfg=None):
    """Bars of date d (from read_bars_csv) -> a list aligned with slots(d, minutes, cfg):
    1-minute bars become 5-minute bars, 5-minute bars pass through. Each item is None
    (no bar in that slot) or {"start", "open", "high", "low", "close", "volume", "n"};
    volume is the sum of the known volumes (None when none is known). Bars outside the
    session (pre-open, the Friday break, after the close) are dropped."""
    spans = slot_spans(d, minutes, cfg)
    starts = [s for s, _ in spans]
    out = [None] * len(spans)
    for b in sorted((x for x in bars or [] if isinstance(x, dict) and x.get("start")), key=lambda x: x["start"]):
        t = b["start"]
        if t.date() != d:
            continue
        k = bisect.bisect_right(starts, t) - 1
        if k < 0 or t >= spans[k][1]:
            continue
        cur = out[k]
        if cur is None:
            out[k] = {"start": starts[k], "open": b["open"], "high": b["high"], "low": b["low"],
                      "close": b["close"], "volume": b.get("volume"), "n": 1}
            continue
        cur["high"], cur["low"], cur["close"] = max(cur["high"], b["high"]), min(cur["low"], b["low"]), b["close"]
        if b.get("volume") is not None:
            cur["volume"] = (cur["volume"] or 0) + b["volume"]
        cur["n"] += 1
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


def max_loss_at_stop(entry, stop, qty, costs=None):
    """The max loss of a long, costs included: qty x (entry - stop) + round_trip_cost(entry,
    stop, qty), in rupees to 0.01. The stop is below the entry: check that first."""
    cost = round_trip_cost(entry, stop, qty, costs)          # this also checks the numbers
    risk = (Decimal(str(entry)) - Decimal(str(stop))) * Decimal(str(qty))
    return float((risk + Decimal(str(cost))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


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


def adjusted_closes(bars, prev_session=None, missing=()):
    """daily_bars() -> closes, oldest first, scaled for payouts and splits. PSX closing files are
    not adjusted: on an ex-date (XD, XB, XR) or a split, the day's LDCP is the close before after
    the payout. When a bar's LDCP is not the close before, and the bar has an ex flag or that close
    is from the session right before (prev_session maps a date to the session before it), every
    older close is scaled by LDCP / that close. Never across a session in `missing` (a day with
    no closing file): there the LDCP is a close we never saw. The last close stays as it is."""
    out = [float(b["close"]) for b in bars]
    f = 1.0
    for i in range(len(bars) - 1, 0, -1):
        cur, before = bars[i], bars[i - 1]
        ldcp, close = cur.get("ldcp"), before["close"]
        moved = bool(ldcp and close and abs(ldcp - close) > 0.005)
        next_day = prev_session is None or prev_session.get(cur["date"]) == before["date"]
        lost = any(before["date"] < m < cur["date"] for m in missing)
        if moved and not lost and (cur.get("flags") or next_day):
            f *= ldcp / close
        out[i - 1] = close * f
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


def adjusted_bars(bars, prev_session=None, missing=()):
    """daily_bars() -> new bars with open, high, low, close and ldcp scaled by each bar's own
    factor from adjusted_closes() (payouts and splits), so daily_atr() and the lows of older
    bars are on today's price scale. The newest bar stays as it is. The input is not changed."""
    adj = adjusted_closes(bars, prev_session, missing)
    out = []
    for b, a in zip(bars or [], adj):
        f = a / b["close"] if b.get("close") else 1.0
        nb = dict(b)
        for k in ("open", "high", "low", "close", "ldcp"):
            if nb.get(k):
                nb[k] = nb[k] * f
        out.append(nb)
    return out


def daily_atr(bars, n=14):
    """(average true range, bars used) of the last n daily bars (daily_bars()), a plain
    mean. A bar's true range is max(high, ldcp) - min(low, ldcp): PSX lowers the LDCP on
    an ex-date, so a payout is not a range. With no LDCP it is high - low. (None, 0)
    with no bars."""
    trs = []
    for b in bars or []:
        hi, lo, ref = b.get("high"), b.get("low"), b.get("ldcp")
        if not hi or not lo:
            continue
        if ref and ref > 0:
            hi, lo = max(hi, ref), min(lo, ref)
        trs.append(hi - lo)
    use = trs[-int(n):]
    return (sum(use) / len(use), len(use)) if use else (None, 0)


def trend_break(closes, n=10):
    """(level, broke) for the newest close: level = the lowest of the n closes before it,
    broke = the newest close is under that level. (None, False) with fewer than n + 1
    closes. Give payout-adjusted closes (adjusted_closes)."""
    c = [float(x) for x in closes or []]
    if len(c) < int(n) + 1:
        return None, False
    level = min(c[-int(n) - 1:-1])
    return level, c[-1] < level


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- this script's own helpers

def now_of(text):
    """--now -> naive UTC (parse_now), with this script's error."""
    try:
        return parse_now(text)
    except ValueError as e:
        raise PlanError(str(e))


def hhmm(text, what):
    """"15:20" -> datetime.time (parse_hhmm), with this script's error."""
    try:
        return parse_hhmm(text, what)
    except ValueError as e:
        raise PlanError(str(e))


def parse_clock(text, what):
    """10:15 AM, 10:15, 3:05 PM -> time."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp][Mm])?\s*$", str(text))
    if not m:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    h, mi = int(m.group(1)), int(m.group(2))
    if m.group(3):
        if not 1 <= h <= 12:
            raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
        h = h % 12 + (12 if m.group(3).upper() == "PM" else 0)
    if h > 23 or mi > 59:
        raise PlanError("%s wants a time like 10:15 AM, not %r" % (what, text))
    return datetime.time(h, mi)


def hm(t):
    return "%02d:%02d" % (t.hour, t.minute)


def hours_text(segs):
    """The day's segments for people: "9:15 AM to 12:00 PM, then 2:30 PM to 4:30 PM"."""
    return ", then ".join("%s to %s" % (clock_label(a), clock_label(b)) for a, b in segs)


# ---------------------------------------------------------------- price math (Decimal, to the Rs 0.01 tick)

def D(x, what="that"):
    try:
        if isinstance(x, float):
            v = Decimal(repr(x))
        else:
            s = str(x).strip().replace(",", "")
            low = s.lower()
            for head in ("rs.", "rs", "pkr"):
                if low.startswith(head):
                    s = s[len(head):].strip()
                    break
            v = Decimal(s)
    except (InvalidOperation, ValueError):
        raise PlanError("%s is not a number: %r" % (what, x))
    if not v.is_finite():
        raise PlanError("%s is not a number: %r" % (what, x))
    return v


def tick(x):
    """Half up to the Rs 0.01 tick (an entry, a range end, a target), as a Decimal."""
    try:
        return D(round_half_up_tick(D(x)))
    except ValueError as e:
        raise PlanError(str(e))


def tick_down(x):
    """Down to the Rs 0.01 tick (a long's stop: away from the entry), as a Decimal."""
    try:
        return D(round_down_tick(D(x)))
    except ValueError as e:
        raise PlanError(str(e))


def px(x):
    """A price for people: 100.65 (2 decimals, no rupee sign)."""
    return format(D(x).quantize(TICK, rounding=ROUND_HALF_UP), "f")


def plain(x):
    """1, 1.5, 0.3586: a Decimal without trailing zeros."""
    return format(D(x).normalize(), "f")


def exact(x):
    """A worked-out price with all its decimals, but at least 2: 304.50, 99.555, 99.6435."""
    d = D(x).normalize()
    return px(d) if -d.as_tuple().exponent <= 2 else format(d, "f")


def ratio(x):
    """reward:risk for people, half up to 2 decimals: 1.00, 1.50, 1.37 (as psx-budget and psx-risk)."""
    return format(D(x).quantize(TICK, rounding=ROUND_HALF_UP), "f")


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid", "or-midpoint"):
        return "or_mid", None
    if t in ("or-low", "or_low", "low"):
        return "or_low", None
    m = re.match(r"^(price|pct|atr):\s*(?:rs\.?\s*|pkr\s*)?([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, price:X, pct:X or atr:K (got %r)" % text)
    kind, val = m.group(1), D(m.group(2), "--stop")
    if val <= 0:
        raise PlanError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 100:
        raise PlanError("--stop pct:X needs X under 100")
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = D(m.group(1), "--target")
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*(?:rs\.?\s*|pkr\s*)?([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", D(m.group(1), "--target")
    raise PlanError("--target is 2R, 1R, 1.5R ... or price:X (got %r)" % text)


def r_label(r):
    n = int(r) if r == r.to_integral_value() else None
    return "%sR (%s times the risk)" % (plain(r), WORDS.get(n, plain(r)))


def risk_floor(e):
    """The smallest risk a share for entry e: 0.05% of the price, and never under Rs 0.02."""
    return max(e * MIN_RISK_PCT / 100, MIN_RISK)


def compute(entry, stop_rule, stop_param, target_rule, target_param, or_high=None, or_low=None,
            atr=None, atr_bars=None, atr_from=None):
    """The stop, the target and the risk, in exact ticks. Raises PlanError with the reason."""
    e = tick(entry)
    if e <= 0:
        raise PlanError("the entry price must be above 0")
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        oh, ol = tick(or_high), tick(or_low)
        if oh <= ol:
            raise PlanError("the opening-range high %s must be above its low %s" % (px(oh), px(ol)))
        if stop_rule == "or_mid":
            raw = (oh + ol) / 2
            label = "opening-range midpoint"
            detail = "(%s + %s) / 2 = %s" % (px(oh), px(ol), exact(raw))
        else:
            raw, label = ol, "opening-range low"
    elif stop_rule == "price":
        raw, label = stop_param, "your price"
    elif stop_rule == "pct":
        raw = e * (1 - stop_param / 100)
        label = "%s%% under the entry" % plain(stop_param)
        detail = "%s x (1 - %s%%) = %s" % (px(e), plain(stop_param), exact(raw))
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs today's bars")
        a = D(round(atr, 6))
        raw = e - stop_param * a
        label = "%s x ATR(%d) under the entry" % (plain(stop_param), atr_bars)
        detail = "ATR %s over the %d bars to the entry%s%s" % (
            plain(a.quantize(Decimal("0.0001"))), atr_bars, " (fewer than 14 so far)" if atr_bars < ATR_BARS else "",
            (", from the " + atr_from) if atr_from else "")
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)
    stop = tick_down(raw)
    if stop != D(raw):
        detail = (detail + ", rounded down" if detail else "%s rounded down" % exact(raw))
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                        % (px(stop), label, px(e)))
    if stop <= 0:
        raise PlanError("the stop works out to %s: pick a stop above 0" % px(stop))
    risk = e - stop
    if risk < risk_floor(e):
        pct_floor = e * MIN_RISK_PCT / 100
        if pct_floor >= MIN_RISK:
            why = "less than 0.05%% of the price (%s on %s)" % (
                plain(pct_floor.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)), px(e))
        else:
            why = "less than %s (two ticks)" % money(MIN_RISK)
        raise PlanError("the stop is %s under the entry, %s: too tight to survive the spread, normal wiggle "
                        "would stop you out. Widen it." % (px(risk), why))
    if target_rule == "r":
        target = tick(e + target_param * risk)
        t_label = r_label(target_param)
    else:
        target = tick(target_param)
        t_label = None
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a long's target goes above it"
                        % (px(target), px(e)))
    rr = D(reward_risk(e, stop, target)).quantize(TICK, rounding=ROUND_HALF_UP)   # shown half up to 2 decimals
    if t_label is None:
        t_label = "your price (%sR)" % ratio(rr)
    return {"entry": e, "stop": stop, "stop_label": label, "stop_detail": detail, "target": target,
            "target_r": target_param if target_rule == "r" else rr, "target_label": t_label,
            "risk": risk, "reward_risk": rr}


# ---------------------------------------------------------------- bars for the ATR stop (the sources of psx-entry)

def bar_minutes(bars):
    """The smallest gap between bar starts, in minutes; None for fewer than 2 bars."""
    starts = sorted(set(b["start"] for b in bars))
    gaps = [(y - x).total_seconds() / 60.0 for x, y in zip(starts, starts[1:])]
    return min(gaps) if gaps else None


def atr_bars(sym, day, minutes, root, bars_file=None, cfg=None, upto=None, now_pkt=None):
    """Today's closed bars of sym up to bar `upto`, from the first source that has any:
    --bars FILE (only that one), else <root>/bars/<SYM>.csv, the symbol tape, the market
    tape. A bar counts once it has ended by now_pkt; on a tape, a snapshot must also show
    that it ended. -> (bars oldest first, each with its "index", source words, path), or
    ([], None, None) when no source has a bar."""
    spans = slot_spans(day, minutes, cfg)
    if bars_file:
        sources = [("bars file", bars_file)]
    else:
        sources = [("bars file", os.path.join(root, "bars", sym + ".csv")),
                   ("company page tape", tape_path(root, day, sym)),
                   ("market tape", tape_path(root, day, "market"))]
    for what, path in sources:
        if not os.path.exists(path):
            if bars_file:
                raise PlanError("no bars file at %s" % path)
            continue
        seen = None
        try:
            if what == "bars file":
                with open(path, encoding="utf-8", errors="replace") as fh:
                    got = read_bars_csv(fh.read()).get(day) or []
                step = bar_minutes(got)
                if step is not None and step > minutes:
                    raise PlanError("%s holds %g-minute bars: a %dm plan needs bars of %d minute%s or shorter"
                                    % (path, step, minutes, minutes, "" if minutes == 1 else "s"))
                aligned = resample_bars(got, day, minutes, cfg)
            else:
                snaps = read_tape(path)
                aligned = bars_from_tape(snaps, day, sym, minutes, cfg)
                seen = last_asof(snaps)
        except ValueError as e:
            raise PlanError("%s: %s" % (path, e))
        out = []
        for k, b in enumerate(aligned):
            end = spans[k][1]
            if b is None or (upto is not None and k > upto) or (now_pkt is not None and end > now_pkt):
                continue
            if what != "bars file" and (seen is None or end > seen):
                continue
            out.append(dict(b, index=k))
        if out or bars_file:
            return out, what, path
    return [], None, None


def atr_of(bars, n=ATR_BARS):
    """Average true range of the last n bars (all of them if fewer). The first bar used
    takes its own high - low (no overnight gap)."""
    trs, prev = [], None
    for b in bars:
        tr = b["high"] - b["low"]
        if prev is not None:
            tr = max(tr, abs(b["high"] - prev), abs(b["low"] - prev))
        trs.append(tr)
        prev = b["close"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


# ---------------------------------------------------------------- files

def read_json(path, what):
    if not path or not os.path.exists(path):
        raise PlanError("no %s at %s" % (what, path))
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise PlanError("%s is not valid JSON: %s" % (path, e))
    except OSError as e:
        raise PlanError("could not read %s: %s" % (path, e))


def read_config(path):
    """DEFAULTS with psx/config.json on top: every key, so the calendar fixes ("holidays",
    "open_days", "ramadan"), "costs" and "psx_site" count too."""
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        doc = read_json(path, "config")
        if not isinstance(doc, dict):
            raise PlanError("%s is not a JSON object" % path)
        for k, v in doc.items():
            if v is not None:
                cfg[k] = v
    return cfg


def write_json(path, doc):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(path + ".tmp", path)


# ---------------------------------------------------------------- today's price limits and the costs

def page_limits(page, day):
    """A company page's CIRCUIT BREAKER as plan limits when its "As of" is on date `day`;
    else None (an older page holds another day's limits)."""
    p = parse_company(page)
    when, reg = p.get("as_of"), p.get("reg") or {}
    lo, hi = reg.get("lower"), reg.get("upper")
    if when is None or when.date() != day or not lo or not hi or lo <= 0 or hi < lo:
        return None
    return {"lower": lo, "upper": hi, "ldcp": reg.get("ldcp"), "from": "company page"}


def tape_limits(snaps, day, sym):
    """The newest lower and upper on a symbol tape for date `day`, as plan limits, or None."""
    best = out = None
    for s in snaps:
        when = last_asof([s])
        if when is None or when.date() != day or str(s.get("symbol") or sym).strip().upper() != sym:
            continue
        lo, hi = num(s.get("lower")), num(s.get("upper"))
        if not lo or not hi or lo <= 0 or hi < lo:
            continue
        if best is None or when >= best:
            best, out = when, {"lower": lo, "upper": hi, "ldcp": num(s.get("ldcp")), "from": "company page"}
    return out


def prev_session(day, cfg=None):
    """The trading day before `day` by the PSX calendar, or None."""
    d = day
    for _ in range(31):
        d -= datetime.timedelta(days=1)
        if trading_day(d, cfg)[0]:
            return d
    return None


def last_close(sym, day, root, cfg=None, offline=False):
    """The symbol's newest official CLOSE before date `day`, from the closing files ->
    (close, its date, "") or (None, None, why). That close is the next session's LDCP,
    unless the next session is an ex-date (then PSX lowers it). When the newest file is
    older than the session before `day`, the close is not known."""
    newest = recent_sessions(day, 1, root, cfg, offline, include_end=False)
    if not newest:
        return None, None, "no closing file before %s in %s" % (day.isoformat(), os.path.join(root, "mkt_summary"))
    want = prev_session(day, cfg)
    if want is not None and newest[-1][0] < want:
        return None, None, ("the newest closing file is from %s, not %s (save it as %s)"
                            % (newest[-1][0].isoformat(), want.isoformat(), daily_path(root, want)))
    for n in (1, 5):
        sessions = newest if n == 1 else recent_sessions(day, n, root, cfg, offline, include_end=False)
        for d, rows in reversed(sessions):
            r = next((x for x in rows if x.get("symbol") == sym), None)
            if r and r.get("close") and r["close"] > 0:
                return r["close"], d, ""
    return None, None, "%s is not in the closing files before %s" % (sym, day.isoformat())


def day_ldcp(sym, day, root, cfg=None):
    """The symbol's LDCP in the plan day's own closing file, when that file is saved (on an
    ex-date PSX has already lowered it), or None. Reads only the data folder."""
    try:
        rows = daily_rows(day, root, cfg, True) or []
    except ValueError:
        return None
    row = next((r for r in rows if r.get("symbol") == sym), None)
    v = row.get("ldcp") if row else None
    return v if v and v > 0 else None


def todays_limits(sym, day, root, cfg=None, offline=False, today=None):
    """The plan day's price limits -> (limits or None, why they are unknown). First the
    company page's CIRCUIT BREAKER for date `day` (company_html: the PSX site first when it
    is on and not offline, else the saved page), then the symbol tape. Else price_limits()
    of the LDCP in the day's own closing file ("from": "computed", "base": "file"), else of
    the last close ("base": "last close": an ex-date lowers it). Else None and the reason.
    The site only has today's page, so a plan for another day reads only the saved page."""
    path = os.path.join(root, "company", sym + ".html")
    url = "%s/company/%s" % (PSX, sym)
    page_offline = offline or (today is not None and day != today)
    page, how = company_html(sym, root, cfg, page_offline)
    lim = page_limits(page, day) if page else None
    if lim:
        return lim, ""
    lim = tape_limits(read_tape(tape_path(root, day, sym)), day, sym)
    if lim:
        return lim, ""
    ldcp = day_ldcp(sym, day, root, cfg)
    if ldcp:
        lo, hi = price_limits(ldcp)
        return {"lower": lo, "upper": hi, "ldcp": ldcp, "from": "computed", "base": "file"}, ""
    try:
        close, _, why = last_close(sym, day, root, cfg, offline)
    except (urllib.error.URLError, OSError, ValueError) as e:
        close, why = None, str(getattr(e, "reason", None) or e)
    if close:
        lo, hi = price_limits(close)
        return {"lower": lo, "upper": hi, "ldcp": close, "from": "computed", "base": "last close"}, ""
    if site_on(cfg) and not page_offline:
        if page is None:
            fix = how[:1].upper() + how[1:] + "."           # PSX's HTTP answer, or why it could not be reached
        elif how == "site":
            fix = "The PSX company page is not for %s yet." % day.isoformat()
        else:
            fix = "The PSX site did not answer, and the saved page %s is not for %s." % (path, day.isoformat())
    elif site_on(cfg):
        fix = ("Save %s as %s (a run with --offline or --now, or a plan for another day, reads only the data "
               "folder)." % (url, path))
    else:
        fix = SITE_OFF % ("%s as %s" % (url, path))
    return None, "%s. %s" % (why, fix)


def costs_per_share(entry, stop, cfg=None):
    """round_trip_cost(entry, stop, 1000) / 1000, half up to 0.0001: the estimated costs a
    share of a buy at the entry and a sell at the stop (the size is not known yet)."""
    try:
        rt = round_trip_cost(float(entry), float(stop), 1000, cost_settings(cfg))
    except ValueError as e:
        raise PlanError(str(e))
    return (Decimal(str(rt)) / 1000).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def costs_note(entry, stop, cfg=None):
    """The cost note, or "": round-trip costs of 1,000 shares (the size is not known yet)
    over COST_WARN of their price risk, by costs_vs_risk(), the rule psx-budget and psx-risk
    use too. The percent is rounded half up to a whole number."""
    try:
        share = costs_vs_risk(float(entry), float(stop), 1000, cost_settings(cfg))
    except ValueError as e:
        raise PlanError(str(e))
    if share is None or share <= COST_WARN:
        return ""
    pct = (Decimal(str(share)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return ("Costs are about %s%% of the price risk (over %s%%). You pay them win or lose."
            % (pct, plain(Decimal(str(COST_WARN)) * 100)))


# ---------------------------------------------------------------- plan

def entry_clock(day, info, minutes, cfg=None):
    """(entry time or None, entry bar or None) on the plan's day. A bar's number is its
    index in the day's bars (slots), counted across the Friday break; the entry time is
    that bar's close. Given one of the two, the other follows; given both, they must agree."""
    spans = slot_spans(day, minutes, cfg)
    t = parse_clock(info["entry_time"], "the entry time") if info["entry_time"] else None
    k = info["entry_bar"]
    if k is not None:
        try:
            if isinstance(k, bool) or int(k) != float(k):
                raise ValueError
            k = int(k)
        except (TypeError, ValueError):
            raise PlanError("the entry bar wants a whole number, not %r" % (k,))
        if not 0 <= k < len(spans):
            raise PlanError("there is no bar %d on %s: its %d-minute bars are 0 to %d"
                            % (k, day_label(day), minutes, len(spans) - 1))
        close = spans[k][1].time()
        if t is None:
            t = close
        elif t != close:
            raise PlanError("the entry time %s is not the close of bar %d (%s): bars count the day's %d-minute "
                            "bars from %s" % (clock_label(t), k, clock_label(close), minutes,
                                               clock_label(spans[0][0])))
    elif t is not None:
        hits = [i for i, (_, end) in enumerate(spans) if end.time() == t]
        k = hits[0] if hits else None
    if t is not None:
        segs = session(day, cfg)
        if not any(a < t <= b for a, b in segs):
            raise PlanError("the entry time %s is outside PSX hours on %s (%s)"
                            % (clock_label(t), day_label(day), hours_text(segs)))
    return t, k


def segment_end_refusal(day, entry_time, segs):
    """Why an entry at the end of a PSX session cannot fill, or "". Nothing trades after
    12:00 PM on a Friday until 2:30 PM, and nothing after the close."""
    for i, (_, end) in enumerate(segs):
        if entry_time != end:
            continue
        if i + 1 < len(segs):
            nxt = clock_label(segs[i + 1][0])
            return ("the entry at %s is when PSX stops on %s: nothing trades from %s to %s, so it cannot fill. "
                    "The next chance is the %s session, at a new price: plan that entry then"
                    % (clock_label(end), day_label(day), clock_label(end), nxt, nxt))
        return ("the entry at %s is at the close on %s: nothing trades after it, so it cannot fill"
                % (clock_label(end), day_label(day)))
    return ""


def minutes_between(a, b):
    base = datetime.date(2000, 1, 3)
    return int((datetime.datetime.combine(base, b) - datetime.datetime.combine(base, a)).total_seconds() // 60)


def resolve_flat_by(day, explicit, cfg, entry_time):
    """(time, note) for the plan's day. The config's flat_by is written for Monday to
    Thursday, so config_time() moves it to that day's hours. --flat-by is a time on the
    day itself: inside its hours, not in the Friday break."""
    segs = session(day, cfg)
    open_t, close_t = segs[0][0], segs[-1][1]
    note = ""
    if explicit:
        t = hhmm(explicit, "--flat-by")
        if t >= close_t:
            raise PlanError("--flat-by %s is at or after the %s close on %s" % (hm(t), clock_label(close_t), day_label(day)))
        if t <= open_t:
            raise PlanError("--flat-by %s is at or before the %s open on %s" % (hm(t), clock_label(open_t), day_label(day)))
        for (a, b), (c, _) in zip(segs, segs[1:]):
            if b < t < c:
                raise PlanError("--flat-by %s is in the Juma break on %s (nothing trades from %s to %s): pick %s or "
                                "earlier, or a time after %s" % (hm(t), day_label(day), clock_label(b), clock_label(c),
                                                                 hm(b), clock_label(c)))
    else:
        base = hhmm(cfg.get("flat_by") or DEFAULTS["flat_by"], "flat_by")
        t = config_time(base, day, cfg)
        if t <= open_t:
            raise PlanError("flat_by %s in the config is at or before the %s open on %s: there is no time to hold a "
                            "trade" % (hm(base), clock_label(open_t), day_label(day)))
        if t != base:
            if t != shift_time(base, day, cfg):
                note = ("The config's flat_by %s falls outside PSX hours on %s (%s), so it is %s."
                        % (hm(base), day_label(day), hours_text(segs), hm(t)))
            elif base <= SHIFT_SPLIT:
                note = ("The config's flat_by %s is for Mon to Thu. PSX opens at %s on %s, so it is %s: the same "
                        "%d minutes after the open." % (hm(base), clock_label(open_t), day_label(day), hm(t),
                                                         minutes_between(NORMAL_OPEN, base)))
            else:
                note = ("The config's flat_by %s is for Mon to Thu. PSX closes at %s on %s, so it is %s: the same "
                        "%d minutes before the close." % (hm(base), clock_label(close_t), day_label(day), hm(t),
                                                           minutes_between(base, NORMAL_CLOSE)))
    if entry_time and entry_time >= t:
        raise PlanError("the entry at %s is at or after your flat-by %s: there is no time left to hold it"
                        % (clock_label(entry_time), hm(t)))
    return t, note


def clean_symbol(text):
    return re.sub(r"[^A-Z0-9-]", "", str(text or "").upper())


def gather(a):
    """The entry facts, from psx/entry.json or the flags."""
    if a.symbol and a.from_path:
        raise PlanError("use --from or --symbol, not both")
    if a.side in ("sell", "short"):
        raise PlanError(LONGS_ONLY)
    if a.symbol:
        if a.entry is None:
            raise PlanError("--symbol needs --entry PRICE")
        if (a.or_high is None) != (a.or_low is None):
            raise PlanError("give both --or-high and --or-low, or neither")
        info = {"symbol": clean_symbol(a.symbol), "entry": a.entry,
                "setup": a.setup or ("orb" if a.or_high is not None else "level"),
                "date": a.date, "entry_time": a.time, "entry_bar": a.entry_bar, "interval": a.interval or "5m",
                "or_high": a.or_high, "or_low": a.or_low, "or_minutes": a.or_minutes, "source": "flags"}
        if not info["symbol"]:
            raise PlanError("--symbol wants a PSX symbol like DEMO")
    else:
        path = a.from_path or ENTRY_PATH
        doc = read_json(path, "entry (run /psx-entry first, or give --symbol and --entry)")
        if not isinstance(doc, dict):
            raise PlanError("%s is not an entry file (a JSON object)" % path)
        if str(doc.get("side") or "buy").lower() not in ("buy", "long"):
            raise PlanError("%s is a %s, and %s" % (path, doc.get("side"), LONGS_ONLY))
        if doc.get("status") != "triggered" and a.entry is None:
            raise PlanError("%s says %s is %s, not triggered: plan once it triggers, or give --entry PRICE to plan ahead"
                            % (path, doc.get("symbol"), doc.get("status") or "unknown"))
        orr = doc.get("opening_range") or {}
        if not isinstance(orr, dict):
            orr = {}
        own = a.entry is None and a.time is None and a.entry_bar is None   # the file's time and bar go together
        info = {"symbol": clean_symbol(doc.get("symbol")), "entry": a.entry if a.entry is not None else doc.get("price"),
                "setup": a.setup or doc.get("setup") or "orb", "date": a.date or doc.get("date"),
                "entry_time": a.time or (doc.get("time") if own else None),
                "entry_bar": a.entry_bar if a.entry_bar is not None else (doc.get("bar_index") if own else None),
                "interval": a.interval or doc.get("interval") or "5m",
                "or_high": a.or_high if a.or_high is not None else orr.get("high"),
                "or_low": a.or_low if a.or_low is not None else orr.get("low"),
                "or_minutes": orr.get("minutes") or a.or_minutes, "source": path}
        seen, of = orr.get("bars"), orr.get("of")      # psx-entry: range bars seen, and bars in the range
        if a.or_high is None and a.or_low is None and all(isinstance(x, int) and not isinstance(x, bool)
                                                          for x in (seen, of)) and seen < of:
            info["or_part"] = (seen, of)
        if not info["symbol"] or info["entry"] is None:
            raise PlanError("%s has no symbol or price" % path)
    if info["interval"] not in INTERVALS:
        raise PlanError("interval is 5m or 1m")
    try:
        info["or_minutes"] = int(info["or_minutes"] or 15)
    except (TypeError, ValueError):
        raise PlanError("the opening range minutes want a whole number, not %r" % (info["or_minutes"],))
    return info


def day_words(day, today):
    """("today's", "today"), or ("the Fri 2026-10-30", "that day") for a plan on another day."""
    return ("today's", "today") if day == today else ("the %s" % day_label(day), "that day")


def c4(x):
    """Costs a share for people: 0.3586 (always 4 decimals)."""
    return format(D(x).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP), "f")


def build_plan(a, now_utc):
    try:
        early = read_config(a.config)       # read first to know the style; a broken file is reported
    except PlanError:                       # by the day steps below, in the v1 order
        early = None
    if early is not None:
        try:
            style = trade_style(early)
        except ValueError as e:
            raise PlanError(str(e))
        if style == "swing":
            return build_swing_plan(a, now_utc, early)
    info = gather(a)
    cfg = read_config(a.config)
    stop_text = a.stop
    if not stop_text:
        if info["setup"] != "orb":
            raise PlanError("a %s setup has no opening-range stop to default to: give --stop price:X, pct:X or atr:K"
                            % info["setup"])
        stop_text = "or-mid"
    stop_rule, stop_param = parse_stop(stop_text)
    if stop_rule in ("or_mid", "or_low") and info.get("or_part"):
        raise PlanError("the opening range in %s has only %d of its %d bars (the tape started late), so its "
                        "midpoint and low are not sure: give --stop price:X, pct:X or atr:K, or the full range "
                        "with --or-high and --or-low" % ((info["source"],) + info["or_part"]))
    target_rule, target_param = parse_target(a.target or "2R")
    now_pkt = to_pkt(now_utc)
    today = day = now_pkt.date()
    if info["date"]:
        try:
            day = datetime.date.fromisoformat(str(info["date"]).strip())
        except ValueError:
            raise PlanError("date wants YYYY-MM-DD, not %r" % info["date"])
    try:
        is_open, why = trading_day(day, cfg)
        kind = session_kind(day, cfg)
        cal_note = calendar_note(day, cfg)
    except ValueError as e:
        raise PlanError(str(e))
    if not is_open:
        raise PlanError("PSX is closed on %s (%s): plan for a trading day" % (day_label(day), why))
    segs = session(day, cfg)
    minutes = INTERVALS[info["interval"]]
    entry_time, entry_bar = entry_clock(day, info, minutes, cfg)
    no_fill = segment_end_refusal(day, entry_time, segs)
    if no_fill:
        raise PlanError(no_fill)
    atr = atr_n = atr_from = None
    if stop_rule == "atr":
        sym = info["symbol"]
        bars, atr_from, where = atr_bars(sym, day, minutes, a.data, a.bars, cfg, entry_bar, now_pkt)
        mine = os.path.join(a.data, "bars", sym + ".csv")
        if atr_from is None:
            raise PlanError("an atr stop needs today's %d-minute bars of %s, and there are none for %s (looked in %s, "
                            "%s and %s). Export them from your broker as %s, or give --bars FILE"
                            % (minutes, sym, day.isoformat(), mine, tape_path(a.data, day, sym),
                               tape_path(a.data, day, "market"), mine))
        if len(bars) < 3:
            raise PlanError("an atr stop needs at least 3 closed %d-minute bars of %s on %s by the entry; found %d in %s"
                            % (minutes, sym, day.isoformat(), len(bars), where))
        atr, atr_n = atr_of(bars)
    c = compute(info["entry"], stop_rule, stop_param, target_rule, target_param, info["or_high"], info["or_low"],
                atr, atr_n, atr_from)
    flat_t, flat_note = resolve_flat_by(day, a.flat_by, cfg, entry_time)
    offline = a.offline or bool(a.now)     # a replay with --now never asks the site: it only has today
    limits, lim_why = todays_limits(info["symbol"], day, a.data, cfg, offline, today)
    whose, when = day_words(day, today)
    warnings = []
    if flat_note:
        warnings.append(flat_note)
    if len(segs) > 1 and flat_t > segs[0][1] and (entry_time is None or entry_time < segs[0][1]):
        before = (datetime.datetime.combine(day, segs[0][1]) - datetime.timedelta(minutes=5)).time()
        advice = " Use --flat-by %s to be out before it." % hm(before)
        warnings.append("Friday: PSX stops from %s to %s (the Juma break). A morning trade is held through it, and "
                        "you cannot sell until %s.%s"
                        % (clock_label(segs[0][1]), clock_label(segs[1][0]), clock_label(segs[1][0]),
                           advice if entry_time is None or entry_time < before else ""))
    if limits:
        lo, hi = D(limits["lower"]), D(limits["upper"])
        if c["entry"] > hi:
            raise PlanError("the entry %s is above %s upper price limit %s: it cannot fill %s"
                            % (px(c["entry"]), whose, px(hi), when))
        if c["entry"] < lo:
            raise PlanError("the entry %s is under %s lower price limit %s: it cannot fill %s"
                            % (px(c["entry"]), whose, px(lo), when))
        if c["target"] > hi:
            raise PlanError("the target %s is above %s upper price limit %s: it cannot fill %s. Pick a target "
                            "at or under %s" % (px(c["target"]), whose, px(hi), when, px(hi)))
        if c["stop"] <= lo:
            warnings.append("The stop %s is at or under %s lower price limit %s. At the limit there may be no "
                            "buyers, so you may not get out." % (px(c["stop"]), whose, px(lo)))
    else:
        warnings.append("The price limits for %s are unknown: %s" % (day.isoformat(), lim_why))
    cps = costs_per_share(c["entry"], c["stop"], cfg)
    note = costs_note(c["entry"], c["stop"], cfg)
    if note:
        warnings.append(note)
    mrr = cfg.get("min_reward_risk")
    try:
        floor = D(mrr) if mrr is not None and not isinstance(mrr, bool) else None
    except PlanError:
        floor = None                        # not a number: psx-risk reports it
    if floor is not None and not meets_reward_risk(c["entry"], c["stop"], c["target"], floor):
        warnings.append("Reward:risk %s is under your min_reward_risk %s in the config: the target is %s over the "
                        "entry, and %s x the risk is %s. psx-risk checks that."
                        % (ratio(c["reward_risk"]), plain(floor), px(c["target"] - c["entry"]), plain(floor),
                           exact(floor * c["risk"])))
    if cal_note:
        warnings.append(cal_note)
    if day != today:
        warnings.append("This plan is for %s, not today (%s)." % (day.isoformat(), today.isoformat()))
    orr = None
    if info["or_high"] is not None and info["or_low"] is not None:
        orr = {"high": float(tick(info["or_high"])), "low": float(tick(info["or_low"])),
               "minutes": info["or_minutes"]}
    plan = {"created_by": "psx-exit", "symbol": info["symbol"], "side": "buy", "date": day.isoformat(),
            "setup": info["setup"], "entry": float(c["entry"]),
            "entry_time": clock_label(entry_time) if entry_time else None,
            "entry_bar": entry_bar, "interval": info["interval"], "opening_range": orr,
            "stop": float(c["stop"]), "stop_rule": stop_rule, "stop_label": c["stop_label"],
            "target": float(c["target"]), "target_r": float(c["target_r"]), "target_label": c["target_label"],
            "risk_per_share": float(c["risk"]), "reward_risk": float(c["reward_risk"]), "flat_by": hm(flat_t),
            "session": kind, "limits": limits, "costs_per_share_est": float(cps),
            "stop_detail": c["stop_detail"], "created_at": utc_iso(now_utc), "note": NOTE}
    if atr is not None:
        plan["atr"] = round(atr, 4)
        plan["atr_bars"] = atr_n
    return plan, warnings


def limits_text(lim):
    """The plan's "limits" for people."""
    if not lim:
        return "unknown: no company page for that day and no last close"
    ldcp = ("LDCP %s" % px(lim["ldcp"])) if lim.get("ldcp") else "LDCP unknown"
    span = "%s to %s" % (px(lim["lower"]), px(lim["upper"]))
    if lim.get("from") == "computed" and lim.get("base") == "file":
        return "%s (%s): computed from the LDCP in the day's closing file" % (span, ldcp)
    if lim.get("from") == "computed":
        return "%s (%s): computed from the last close; an ex-date lowers it" % (span, ldcp)
    return "%s, from the company page (%s)" % (span, ldcp)


def describe(plan):
    day = datetime.date.fromisoformat(plan["date"])
    setup = {"orb": "opening-range breakout", "level": "close above a level"}.get(plan["setup"], plan["setup"])
    e = D(plan["entry"])
    risk = D(plan["risk_per_share"])
    pct = (risk / e * 100).quantize(TICK, rounding=ROUND_HALF_UP)
    when = ""
    if plan.get("entry_time"):
        when = " at %s" % plan["entry_time"]
        if plan.get("entry_bar") is not None:
            when += " (bar %s)" % plan["entry_bar"]
    out = ["Exit plan: %s long, %s, %s on %d-minute bars" % (
        plan["symbol"], day_label(day), setup, INTERVALS.get(plan["interval"], 5))]
    out.append("  Entry         %s%s" % (px(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (px(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (px(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s (%s%% of the entry)" % (px(plan["risk_per_share"]), pct))
    out.append("  Reward:risk   %s" % ratio(plan["reward_risk"]))
    if plan.get("costs_per_share_est") is not None:
        out.append("  Costs         about %s a share, buy and sell at the stop (estimated)"
                   % c4(plan["costs_per_share_est"]))
    if "limits" in plan:
        out.append("  Price limits  %s" % limits_text(plan.get("limits")))
    out.append("  Flat by       %s PKT, win or lose: no overnight hold" % plan["flat_by"])
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(NOTE)
    out.append(NO_STOP_ORDER)
    out.append(LATE)
    return out


# ---------------------------------------------------------------- swing style ("style": "swing" in psx/config.json)

def swing_day(now_pkt, cfg=None):
    """The buy day of a swing plan made at naive Karachi time now_pkt: today while PSX has not
    closed yet today, else the next PSX session (add_sessions). Fri 2026-11-06 at 17:00 gives
    Tue 2026-11-10 (Mon 2026-11-09 is Iqbal Day)."""
    today = now_pkt.date()
    segs = session(today, cfg)
    if segs and now_pkt < datetime.datetime.combine(today, segs[-1][1]):
        return today
    return add_sessions(today, 1, cfg)


def swing_stop_rule(text, what):
    """A swing stop rule -> (kind, number): atr:K, low:N, level:K, price:X or pct:X. `what` says
    where the rule came from (--stop, or swing.stop in psx/config.json)."""
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid", "or-midpoint", "or-low", "or_low"):
        raise PlanError("the stop %s (%s) is an opening-range stop, for a day trade, and your style is swing. %s"
                        % (text, what, SWING_STOPS))
    if t == "low":
        raise PlanError("the stop low (%s) needs a number of sessions in swing style, like low:10" % what)
    m = re.match(r"^(price|pct|atr|low|level):\s*(?:rs\.?\s*|pkr\s*)?([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("the stop %r (%s) is not a swing stop. %s" % (text, what, SWING_STOPS))
    kind, val = m.group(1), D(m.group(2), what)
    if val <= 0:
        raise PlanError("the stop %s:%s (%s) needs a number above 0"
                        % (kind, {"low": "N", "atr": "K", "level": "K"}.get(kind, "X"), what))
    if kind == "pct" and val >= 100:
        raise PlanError("the stop pct:X (%s) needs X under 100" % what)
    if kind == "low" and (val != val.to_integral_value() or val > SWING_FILES):
        raise PlanError("the stop low:N (%s) wants a whole number of sessions from 1 to %d" % (what, SWING_FILES))
    return kind, val


def swing_count(s, key, low, high):
    """swing.<key> of the swing settings as a whole number from low to high, else a plain error."""
    v = s.get(key)
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
        raise PlanError("swing.%s in psx/config.json wants a whole number from %d to %d, not %r"
                        % (key, low, high, v))
    return v


def scan_hit(path, sym, close_date):
    """psx-scanner's hit for sym in the watchlist at `path`, when that scan read the same close
    (close_date) -> (hit, ""). Else (None, why there is none)."""
    if not os.path.exists(path):
        return None, "there is no %s yet (run /psx-scanner: scan.py daily)" % path
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None, "%s is not valid JSON" % path
    hits = doc.get("hits") if isinstance(doc, dict) else None
    hit = None
    if isinstance(hits, list):
        hit = next((h for h in hits if isinstance(h, dict) and clean_symbol(h.get("symbol")) == sym), None)
    if hit is None:
        return None, "%s is not in %s" % (sym, path)
    if str(hit.get("close_date")) != close_date.isoformat():
        return None, ("%s in %s is from the %s close, not the %s close (run scan.py daily again)"
                      % (sym, path, hit.get("close_date") or "unknown", close_date.isoformat()))
    return hit, ""


def scan_setups(hit):
    """The setup keys of a scan hit (its "setups" list), text only."""
    tags = hit.get("setups") if isinstance(hit, dict) else None
    return [t for t in tags if isinstance(t, str) and t.strip()] if isinstance(tags, list) else []


def board_note(path, sym, first, last):
    """The note for a board meeting of sym in psx/metrics.json (psx-analyst) from date first to
    date last, both counted, or "". Results can gap the price. It is never a refusal."""
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return "%s could not be read, so its board meeting dates were not checked." % path
    rows = doc.get("companies") if isinstance(doc, dict) else None
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or clean_symbol(r.get("symbol")) != sym:
            continue
        try:
            d = datetime.date.fromisoformat(str(r.get("next_board_meeting") or "")[:10])
        except ValueError:
            return ""
        if first <= d <= last:
            return "%s has a board meeting on %s (%s). Results can gap the price." % (sym, day_label(d), path)
    return ""


def swing_facts(a):
    """The entry facts of a swing plan: --symbol, or a --from entry.json that has triggered (its
    price is the entry). The day-trade flags are refused, one plain line each."""
    for attr, flag, why in DAY_FLAGS:
        if getattr(a, attr, None) is not None:
            raise PlanError("%s is a day-trade flag, and your style is swing: %s" % (flag, why))
    if a.side in ("sell", "short"):
        raise PlanError(LONGS_ONLY)
    if a.symbol and a.from_path:
        raise PlanError("use --from or --symbol, not both")
    if a.symbol:
        sym = clean_symbol(a.symbol)
        if not sym:
            raise PlanError("--symbol wants a PSX symbol like DEMO")
        return {"symbol": sym, "entry": None, "setup": None, "date": a.date}
    if not a.from_path:
        raise PlanError("say which stock: --symbol DEMO (see psx/watchlist.json)")
    path = a.from_path
    doc = read_json(path, "entry")
    if not isinstance(doc, dict):
        raise PlanError("%s is not an entry file (a JSON object)" % path)
    if str(doc.get("side") or "buy").lower() not in ("buy", "long"):
        raise PlanError("%s is a %s, and %s" % (path, doc.get("side"), LONGS_ONLY))
    if doc.get("status") != "triggered" and a.entry is None:
        raise PlanError("%s says %s is %s, not triggered: give --entry PRICE, or plan from the daily scan with "
                        "--symbol" % (path, doc.get("symbol"), doc.get("status") or "unknown"))
    sym, price = clean_symbol(doc.get("symbol")), doc.get("price") if a.entry is None else None
    if not sym or (a.entry is None and price is None):
        raise PlanError("%s has no symbol or price" % path)
    return {"symbol": sym, "entry": price, "setup": doc.get("setup"), "date": a.date or doc.get("date")}


def swing_stop(rule, k, e, adj, atr, atr_n, last_date, hit, hit_why, sym):
    """(raw stop as a Decimal, label, detail) of an atr:K, low:N or level:K swing stop, on the
    payout-adjusted daily bars `adj`. compute() rounds it down to the tick and checks it."""
    a = D(round(atr, 6))
    to = day_label(last_date)
    if rule == "atr":
        return (e - k * a, "%s x ATR(%d) under the entry" % (plain(k), ATR_BARS),
                "daily ATR %s over the %d sessions to %s" % (c4(a), atr_n, to))
    if rule == "low":
        n = int(k)
        if n > len(adj):
            raise PlanError("the stop low:%d needs %d daily bars of %s, and the closing files have %d"
                            % (n, n, sym, len(adj)))
        low = D(round(min(b["low"] for b in adj[-n:]), 6))
        return (low - TICK, "one tick under the lowest low of %d sessions" % n,
                "low %s in the %d sessions to %s, less 0.01" % (exact(low), n, to))
    level = num(hit.get("level")) if hit else None
    if level is None:
        if hit:
            tags = scan_setups(hit)
            hit_why = "its setup (%s) has no level" % (", ".join(SWING_SETUPS.get(t, t) for t in tags) or "none")
        raise PlanError("a level:K stop needs the scan's level of %s for the %s close in %s, but %s"
                        % (sym, last_date.isoformat(), WATCHLIST_PATH, hit_why))
    lv = D(round(level, 6))
    raw = lv - k * a
    return (raw, "%s x ATR(%d) under the scan's level" % (plain(k), ATR_BARS),
            "%s - %s x %s (the daily ATR) = %s" % (px(lv), plain(k), c4(a), exact(raw)))


def entry_words(plan):
    """How a swing plan's limit price was set, for people."""
    rule = str(plan.get("entry_rule") or "")
    lc, lcd = plan.get("last_close"), plan.get("last_close_date")
    if lc is None or not lcd:
        return rule or "your price"
    when = day_label(datetime.date.fromisoformat(str(lcd)))
    if rule.startswith("last close + "):
        return "the last close %s (%s) + %s" % (px(lc), when, rule[len("last close + "):])
    what = "the price of your entry signal" if rule == "entry signal" else "your price"
    return "%s (the last close was %s on %s)" % (what, px(lc), when)


def moved_ldcp(limits, sessions, sym, close):
    """(the buy day's own LDCP, the close before it), as Decimals, when they differ by more than a
    tick, else None. Only limits from the buy day itself count: its company page or tape, or its own
    closing file. PSX moves the LDCP on an ex-date (a payout, bonus or right) or a split, so the
    closing files before that day are on the old prices. The close before is the share's close in
    the newest closing file (PSX sets one on a day with no trade), else `close` (the last close)."""
    if not limits or not (limits.get("from") == "company page" or limits.get("base") == "file"):
        return None
    ref = num(limits.get("ldcp"))
    if not ref or ref <= 0:
        return None
    rows = sessions[-1][1] if sessions else []
    row = next((r for r in rows if isinstance(r, dict) and r.get("symbol") == sym), None)
    before = D(row["close"] if row and row.get("close") and row["close"] > 0 else close)
    ref = D(ref)
    return (ref, before) if abs(ref - before) > TICK else None


def build_swing_plan(a, now_utc, cfg):
    """The swing plan: a limit buy on the buy day, its stop and target from the closing files,
    the exit-by day (the time stop), the trend rule and the gap risk. -> (plan, notes).
    Raises PlanError with the reason. Day plans never come here."""
    s = swing_settings(cfg)
    facts = swing_facts(a)
    sym = facts["symbol"]
    where = "--stop" if a.stop else "swing.stop in psx/config.json"
    stop_rule, stop_param = swing_stop_rule(a.stop or s.get("stop") or SWING["stop"], where)
    try:
        target_rule, target_param = parse_target(a.target or s.get("target") or SWING["target"])
    except PlanError as e:
        if a.target:
            raise
        raise PlanError("swing.target in psx/config.json: %s" % e)
    hold = swing_count(s, "max_hold_sessions", 1, 250)
    n_trend = swing_count(s, "trend_exit_sessions", 2, SWING_FILES)
    now_pkt = to_pkt(now_utc)
    today = now_pkt.date()
    day = None
    if facts["date"]:
        try:
            day = datetime.date.fromisoformat(str(facts["date"]).strip())
        except ValueError:
            raise PlanError("date wants YYYY-MM-DD, not %r" % facts["date"])
    try:
        day = day or swing_day(now_pkt, cfg)
        is_open, why = trading_day(day, cfg)
        kind = session_kind(day, cfg)
        want = prev_session(day, cfg)
    except ValueError as e:
        raise PlanError(str(e))
    if not is_open:
        raise PlanError("PSX is closed on %s (%s): plan for a trading day" % (day_label(day), why))

    # the daily bars: the newest closing files before the buy day
    offline = a.offline or bool(a.now)     # a replay with --now never asks the site: it only has today
    folder = os.path.join(a.data, "mkt_summary")
    try:
        sessions = recent_sessions(day, SWING_FILES, a.data, cfg, offline, include_end=False)
    except urllib.error.HTTPError as e:
        raise PlanError("PSX answered HTTP %d for a closing file" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise PlanError("could not reach PSX for a closing file: %s" % getattr(e, "reason", e))
    except ValueError as e:
        raise PlanError(str(e))
    if sessions and want is not None and sessions[-1][0] < want:
        raise PlanError("the newest closing file in %s is from %s, not %s, the session before the buy day: save it "
                        "as %s (a day's file shows about 30 to 40 minutes after the close)"
                        % (folder, day_label(sessions[-1][0]), day_label(want), daily_path(a.data, want)))
    bars = daily_bars(sym, sessions)
    if len(bars) < SWING_MIN_BARS:
        if len(sessions) >= SWING_FILES:
            raise PlanError("%s traded on only %d of the %d sessions before %s: a swing plan needs %d daily bars "
                            "(a new listing, or a share that seldom trades)"
                            % (sym, len(bars), len(sessions), day_label(day), SWING_MIN_BARS))
        if not site_on(cfg):
            fix = SITE_OFF % ("the PSX closing files (%s/download/mkt_summary/YYYY-MM-DD.Z) in %s" % (PSX, folder))
        elif offline:
            fix = "Save the closing files in %s (a run with --offline or --now reads only the data folder)." % folder
        else:
            fix = "PSX had no more closing files for those days."
        raise PlanError("a swing plan needs %d daily bars of %s from the closing files before %s, and %s gives %d "
                        "(from %d files). %s" % (SWING_MIN_BARS, sym, day_label(day), folder, len(bars),
                                                 len(sessions), fix))
    missing = window_sessions([d for d, _ in sessions], cfg)[1]
    adj = adjusted_bars(bars, None, missing)       # payouts and splits: older bars on today's price scale
    atr, atr_n = daily_atr(adj, ATR_BARS)
    if atr is None or atr_n < ATR_BARS:
        raise PlanError("a swing plan needs %d daily bars of %s with a high and a low for the ATR, and the closing "
                        "files have %d" % (ATR_BARS, sym, atr_n))
    closes = [b["close"] for b in adj]
    if n_trend > len(closes):
        raise PlanError("swing.trend_exit_sessions %d needs %d closes of %s, and the closing files have %d"
                        % (n_trend, n_trend, sym, len(closes)))
    last_close, last_date = tick(bars[-1]["close"]), bars[-1]["date"]
    notes = []
    if last_date < sessions[-1][0]:
        notes.append("%s did not trade on %s: its last close is from %s."
                     % (sym, day_label(sessions[-1][0]), day_label(last_date)))

    # the setup and its level: psx-scanner's hit for the same close
    hit, hit_why = scan_hit(WATCHLIST_PATH, sym, last_date)
    setup = level = None
    if hit:
        tags = scan_setups(hit)
        level = num(hit.get("level"))
        first = [t for t in ("breakout_20d", "reclaim_ma20") if t in tags] if level is not None else []
        setup = (first or tags or [None])[0]
    setup = a.setup or setup or facts["setup"]
    if not isinstance(setup, str) or not setup.strip():
        setup = None                        # a setup is a name, like breakout_20d

    # the buy day's price limits, and its own LDCP when an ex-date or a split moved it off the last close:
    # then the closing files before that day are on the old prices
    limits, lim_why = todays_limits(sym, day, a.data, cfg, offline, today)
    moved = moved_ldcp(limits, sessions, sym, last_close)
    if moved is not None:
        ex = ("the LDCP of %s on %s is %s in %s, not the last close %s"
              % (sym, day_label(day), px(moved[0]), "its company page" if limits.get("from") == "company page"
                 else "that day's closing file", px(moved[1])))

    # the entry, the stop and the target
    if a.entry is not None:
        entry, entry_rule = a.entry, "your price"
    elif facts["entry"] is not None:
        entry, entry_rule = facts["entry"], "entry signal"
    else:
        buf = s.get("entry_buffer_pct")
        if isinstance(buf, bool) or not isinstance(buf, (int, float)) or not 0 <= buf < 10:
            raise PlanError("swing.entry_buffer_pct in psx/config.json wants a number from 0 to under 10, not %r"
                            % (buf,))
        if moved is not None:
            raise PlanError("%s. PSX moves it on an ex-date (a payout, bonus or right) or a split. It can also move "
                            "after days with no trade. So the closing files before that day do not fit its prices: "
                            "give --entry %s (the new LDCP + %s%%), or plan for the next session once the closing "
                            "file of %s is saved"
                            % (ex, px(tick(moved[0] * (1 + D(buf) / 100))), plain(D(buf)), day_label(day)))
        entry, entry_rule = last_close * (1 + D(buf) / 100), "last close + %s%%" % plain(D(buf))
    if moved is not None:
        if stop_rule in ("low", "level"):
            raise PlanError("%s. The stop %s:%s would come from the closing files before that day, and they do not "
                            "fit that day's prices. Use --stop atr:K, pct:X or price:X"
                            % (ex, stop_rule, plain(stop_param)))
        notes.append("%s%s. PSX moves it on an ex-date or a split, and it can move after days with no trade. The ATR "
                     "and the trend level here come from the closing files before that day, so they may be on the old "
                     "prices." % (ex[:1].upper(), ex[1:]))
    if stop_rule in ("price", "pct"):
        c = compute(entry, stop_rule, stop_param, target_rule, target_param)
    else:
        e = tick(entry)
        if e <= 0:
            raise PlanError("the entry price must be above 0")
        raw, label, detail = swing_stop(stop_rule, stop_param, e, adj, atr, atr_n, last_date, hit, hit_why, sym)
        if tick_down(raw) >= e:
            raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                            % (px(tick_down(raw)), label, px(e)))
        c = compute(e, "price", raw, target_rule, target_param)
        c["stop_label"] = label
        c["stop_detail"] = detail + (", rounded down" if c["stop"] != raw else "")

    # the buy day's price limits: an entry outside them cannot fill; a target over them can, later.
    # The price can trade at a limit, never past it: a stop under the lower limit cannot be hit that
    # day, but a stop at the limit can.
    whose, when = day_words(day, today)
    if limits:
        lo, hi = D(limits["lower"]), D(limits["upper"])
        if c["entry"] > hi:
            raise PlanError("the entry %s is above %s upper price limit %s: it cannot fill %s"
                            % (px(c["entry"]), whose, px(hi), when))
        if c["entry"] < lo:
            raise PlanError("the entry %s is under %s lower price limit %s: it cannot fill %s"
                            % (px(c["entry"]), whose, px(lo), when))
        if c["target"] > hi:
            notes.append("The target %s is over the upper price limit %s of %s: it cannot fill on %s; it can on a "
                         "later day." % (px(c["target"]), px(hi), day_label(day), day_label(day)))
        if c["stop"] < lo:
            notes.append("The stop %s is under the lower price limit %s of %s: it cannot be hit that day; it counts "
                         "from the next session." % (px(c["stop"]), px(lo), day_label(day)))
        elif c["stop"] == lo:
            notes.append("The stop %s is at the lower price limit %s of %s: the price can trade at the limit, so the "
                         "stop can be hit that day. At the limit there may be no buyers, so you may not get out."
                         % (px(c["stop"]), px(lo), day_label(day)))
        ref = num(limits.get("ldcp"))
        if ref and ref > 0 and c["stop"] >= D(ref):
            notes.append("The stop %s is at or over %s, the LDCP of %s. A limit buy at %s can fill lower, and a fill "
                         "at %s or under is already at or under the stop."
                         % (px(c["stop"]), px(ref), day_label(day), px(c["entry"]), px(ref)))
    else:
        notes.append("The price limits for %s are unknown: %s" % (day.isoformat(), lim_why))

    # the time stop, the trend rule, the costs (commission both sides) and the gap risk
    try:
        exit_by = add_sessions(day, hold, cfg)
        exit_t = config_time(hhmm(cfg.get("flat_by") or DEFAULTS["flat_by"], "flat_by"), exit_by, cfg)
        cal = [calendar_note(d, cfg) for d in (day, exit_by)]
    except ValueError as e:
        raise PlanError(str(e))
    trend_level = tick(min(closes[-n_trend:]))
    tc = trade_costs(cfg)
    both = dict(cfg, costs=tc)          # costs_per_share() and costs_note() read cost_settings(): here that is tc
    cps = costs_per_share(c["entry"], c["stop"], both)
    note = costs_note(c["entry"], c["stop"], both)
    if note:
        notes.append(note)
    gap_low = D(price_limits(float(c["entry"]))[0])
    try:
        loss = D(gap_loss(float(c["entry"]), 1000, tc))
    except ValueError as e:
        raise PlanError(str(e))
    gps = (loss / 1000).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    gap_r = (gps / (c["risk"] + cps)).quantize(TICK, rounding=ROUND_HALF_UP)
    mrr = cfg.get("min_reward_risk")
    try:
        floor = D(mrr) if mrr is not None and not isinstance(mrr, bool) else None
    except PlanError:
        floor = None                        # not a number: psx-risk reports it
    if floor is not None and not meets_reward_risk(c["entry"], c["stop"], c["target"], floor):
        notes.append("Reward:risk %s is under your min_reward_risk %s in the config: the target is %s over the "
                     "entry, and %s x the risk is %s. psx-risk checks that."
                     % (ratio(c["reward_risk"]), plain(floor), px(c["target"] - c["entry"]), plain(floor),
                        exact(floor * c["risk"])))
    meeting = board_note(METRICS_PATH, sym, day, exit_by)
    if meeting:
        notes.append(meeting)
    for n in cal:
        if n and n not in notes:
            notes.append(n)
    if day != today:
        notes.append("This plan is for %s, not today (%s)." % (day.isoformat(), today.isoformat()))
    plan = {"created_by": "psx-exit", "style": "swing", "symbol": sym, "side": "buy", "date": day.isoformat(),
            "setup": setup, "level": level, "last_close": float(last_close), "last_close_date": last_date.isoformat(),
            "entry": float(c["entry"]), "entry_rule": entry_rule, "entry_time": None, "entry_bar": None,
            "interval": "1d", "opening_range": None,
            "stop": float(c["stop"]), "stop_rule": stop_rule, "stop_label": c["stop_label"],
            "stop_detail": c["stop_detail"], "atr": round(atr, 4), "atr_bars": atr_n, "atr_from": "closing files",
            "target": float(c["target"]), "target_r": float(c["target_r"]), "target_label": c["target_label"],
            "risk_per_share": float(c["risk"]), "reward_risk": float(c["reward_risk"]), "flat_by": None,
            "max_hold_sessions": hold, "exit_by": exit_by.isoformat(), "exit_by_time": hm(exit_t),
            "trend_sessions": n_trend, "trend_level": float(trend_level), "session": kind, "limits": limits,
            "costs_per_share_est": float(cps), "gap_low": float(gap_low), "gap_loss_per_share": float(gps),
            "gap_r": float(gap_r), "created_at": utc_iso(now_utc), "note": NOTE}
    return plan, notes


def describe_swing(plan):
    """A swing plan for people: the day lines with the exit-by day, the trend rule and the gap risk
    in place of "Flat by", then the four closing lines and the overnight line last."""
    day = datetime.date.fromisoformat(plan["date"])
    setup = plan.get("setup")
    head = "Swing plan: %s long, buy on %s" % (plan["symbol"], day_label(day))
    if setup:
        head += ", " + SWING_SETUPS.get(setup, str(setup))
        if plan.get("level") is not None:
            head += " (level %s)" % px(plan["level"])
    e = D(plan["entry"])
    risk = D(plan["risk_per_share"])
    pct = (risk / e * 100).quantize(TICK, rounding=ROUND_HALF_UP)
    out = [head, "  Entry         %s  limit buy: %s" % (px(e), entry_words(plan))]
    out.append("  Stop          %s  %s%s" % (px(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (px(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s (%s%% of the entry)" % (px(risk), pct))
    out.append("  Reward:risk   %s" % ratio(plan["reward_risk"]))
    if plan.get("costs_per_share_est") is not None:
        out.append("  Costs         about %s a share, buy and sell at the stop (estimated, commission both sides)"
                   % c4(plan["costs_per_share_est"]))
    if "limits" in plan:
        out.append("  Price limits  %s" % limits_text(plan.get("limits")))
    exit_by = datetime.date.fromisoformat(str(plan["exit_by"]))
    at = (" at %s PKT" % plan["exit_by_time"]) if plan.get("exit_by_time") else ""
    held = plan.get("max_hold_sessions")
    out.append("  Exit by       %s%s: %s" % (day_label(exit_by), at, ("%s sessions after the buy day (the time stop)"
                                                                     % held) if held else "the time stop"))
    if plan.get("trend_level") is not None and plan.get("trend_sessions"):
        out.append("  Trend rule    out after a close under the lowest close of the %s sessions before it (now %s)"
                   % (plan["trend_sessions"], px(plan["trend_level"])))
    if all(plan.get(k) is not None for k in ("gap_low", "gap_loss_per_share", "gap_r")):
        out.append("  Gap risk      a next-day open at the lower limit %s loses about %s a share, costs included: "
                   "%s times the loss at the stop" % (px(plan["gap_low"]), px(plan["gap_loss_per_share"]),
                                                      ratio(plan["gap_r"])))
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.extend([NOTE, NO_STOP_ORDER, LATE, OVERNIGHT])
    return out


def cmd_plan(a):
    now = now_of(a.now)
    plan, warnings = build_plan(a, now)
    if a.write:
        write_json(a.write, plan)
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings), indent=2))
        return 0
    swing = plan.get("style") == "swing"
    for line in describe_swing(plan) if swing else describe(plan):
        print(line)
    for w in warnings:
        print("Note: " + w)
    if a.write:
        print("Wrote %s." % a.write)
    if swing:
        print("Next: /psx-timemachine replays the next %d sessions, then /psx-budget sizes it on the buy day. "
              "Nothing was placed." % plan["max_hold_sessions"])
        return 0
    print("Next: /psx-timemachine replays it, then /psx-budget sizes it to your max loss, costs included. "
          "Nothing was placed.")
    return 0


def cmd_show(a):
    if not os.path.exists(a.plan):
        print("No plan at %s yet. Make one: exit.py plan --write" % a.plan)
        return 1
    plan = read_json(a.plan, "plan")
    if not isinstance(plan, dict):
        raise PlanError("%s is not a plan (a JSON object): make a new one with exit.py plan --write" % a.plan)
    swing = plan.get("style") == "swing"        # a swing plan has an exit-by day in place of flat_by
    for k in ("symbol", "date", "entry", "stop", "target", "risk_per_share", "reward_risk",
              "exit_by" if swing else "flat_by"):
        if plan.get(k) is None:
            raise PlanError("%s is missing %r: make a new plan with exit.py plan --write" % (a.plan, k))
    for k in ("date", "exit_by") if swing else ("date",):
        try:
            datetime.date.fromisoformat(str(plan[k]))
        except ValueError:
            raise PlanError("%s has a bad %s %r: make a new plan with exit.py plan --write" % (a.plan, k, plan[k]))
    if a.json:
        print(json.dumps(plan, indent=2))
        return 0
    try:
        if swing:
            lines = describe_swing(dict({"stop_label": "", "target_label": ""}, **plan))
        else:
            lines = describe(dict({"setup": "orb", "interval": "5m", "stop_label": "", "target_label": ""}, **plan))
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        raise PlanError("%s is not a plan this tool can read (%s): make a new one with exit.py plan --write"
                        % (a.plan, e))
    for line in lines:
        print(line)
    today = to_pkt(now_of(a.now)).date().isoformat()
    if plan["date"] != today:
        print("Note: this plan is for %s, not today (%s)." % (plan["date"], today))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit Planner (psx-exit): the stop and target before the entry.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="set the stop and target")
    p.add_argument("--from", dest="from_path", metavar="PATH", help="entry.json to plan from (default psx/entry.json)")
    p.add_argument("--symbol")
    p.add_argument("--entry", type=float, help="entry price (or a plan-ahead price with --from)")
    p.add_argument("--or-high", type=float)
    p.add_argument("--or-low", type=float)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--setup", choices=["orb", "level"])
    p.add_argument("--date", help="session date YYYY-MM-DD (default: the entry's, or today in Karachi)")
    p.add_argument("--time", help="entry time, the entry bar's close, e.g. 10:15 AM")
    p.add_argument("--entry-bar", type=int, help="the entry bar's number that day (bar 0 is the first bar)")
    p.add_argument("--interval", choices=["5m", "1m"])
    p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="buy only")
    p.add_argument("--stop", help="or-mid (orb default), or-low, price:X, pct:X, atr:K; "
                                  "swing style: atr:K (default atr:2), low:N, level:K, price:X, pct:X")
    p.add_argument("--target", help="2R (default), 1R, 1.5R, price:X")
    p.add_argument("--flat-by", help="HH:MM Karachi time on the plan's day (default: the config's flat_by, "
                                     "15:20 on Mon to Thu, moved to the day's hours)")
    p.add_argument("--config", default=CONFIG_PATH)
    p.add_argument("--data", default=DATA_DIR, metavar="DIR", help="the PSX data folder (default psx/data)")
    p.add_argument("--bars", metavar="FILE", help="today's bars for an atr stop (a CSV from your broker)")
    p.add_argument("--offline", action="store_true", help="never touch the network: read only the data folder")
    p.add_argument("--now", help='pretend it is this Karachi time, e.g. "2026-10-29 10:15"')
    p.add_argument("--write", nargs="?", const=PLAN_PATH, metavar="PATH",
                   help="save the plan (default psx/plan.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="print the current plan")
    p.add_argument("--plan", default=PLAN_PATH)
    p.add_argument("--now")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except PlanError as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("exit.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
