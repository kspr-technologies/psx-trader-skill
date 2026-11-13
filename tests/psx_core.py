#!/usr/bin/env python3
"""psx_core: the canonical PSX helpers of psx-trader-skill. Standard library only, Python 3.8.

Every skill script stays self-contained, so it COPIES the helpers it uses from this
file, verbatim: same name, same body, same docstring. Never edit a copied helper. If
you need other behaviour, write a new function with a new name in your script.
tests/test_repo.py checks that each copy is the same text as here. Helpers raise
ValueError with a plain message; a script wraps that in its own error class.

Times are naive datetimes. Karachi time (PKT) is UTC+5 all year (no daylight saving),
so no time zone files are used and the machine's own zone never matters. Below, "UTC"
means a naive datetime that holds UTC and "PKT" one that holds Karachi time.

PSX data is read from the data folder (psx/data, or --data DIR) first. The PSX site is
used only when psx/config.json has "psx_site": true (PSX's terms ban automated use
without its written permission), never with --offline, with plain GETs at least PAUSE
seconds apart.
"""

# ---------------------------------------------------------------- copy together
# Imports used below (copy the ones your helpers need):
#   import bisect, csv, datetime, gzip, html, io, json, os, re, time,
#   import urllib.error, urllib.request, zipfile
#   from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
#
# Each line is a helper and the names from this file it uses. Copy those too, and
# what they use in turn. "-" means it uses nothing else from this file. A group is a
# set that works as one block: copy the whole group when you are not sure.
# Example: slots() also needs slot_spans, session, session_kind, trading_day,
# in_ramadan, ramadan_range, SESSIONS, PSX_HOLIDAYS, PSX_PROVISIONAL and RAMADAN.
#
# group clock
#   to_pkt: KARACHI_HOURS
#   pkt_to_utc: KARACHI_HOURS
#   ts_to_pkt: to_pkt
#   utc_now: -
#   parse_now: pkt_to_utc, utc_now
#   parse_hhmm: -
#   parse_utc: -
#   utc_iso: -
#   pkt_iso: KARACHI_HOURS, to_pkt
#   clock_label: -
#   day_label: -
# group calendar (needs nothing from clock)
#   ramadan_range: RAMADAN
#   in_ramadan: ramadan_range
#   trading_day: PSX_HOLIDAYS, PSX_PROVISIONAL
#   calendar_note: CALENDAR_YEARS, PSX_HOLIDAYS, PSX_PROVISIONAL, RAMADAN_PROVISIONAL, ramadan_range, trading_day
#   session_kind: in_ramadan, trading_day
#   session: SESSIONS, session_kind
#   add_sessions: trading_day
# group bars grid (with the calendar group)
#   slot_spans: session
#   slots: slot_spans
#   slot_index: slots
#   bar_close: slot_spans
#   opening_range_slots: session, slot_spans
#   shift_time: NORMAL_CLOSE, NORMAL_OPEN, SHIFT_SPLIT, session, session_kind
#   config_time: session, shift_time
#   market_state: clock_label, session, session_kind, trading_day
#   swing_window: MATCH_MINUTES, clock_label, market_state, session
# group prices
#   round_down_tick: -
#   round_half_up_tick: -
#   price_limits: -
#   money: -
# group costs (COST_WARN is the warning line for costs_vs_risk)
#   cost_settings: COSTS
#   leg_cost: COSTS
#   round_trip_cost: COSTS, leg_cost
#   max_loss_at_stop: round_trip_cost
#   costs_vs_risk: round_trip_cost
#   reward_risk: -
#   meets_reward_risk: -
#   gap_loss: max_loss_at_stop, price_limits
# group style (SWING is the default "swing" block of psx/config.json)
#   trade_style: -
#   swing_settings: SWING
#   trade_costs: cost_settings, trade_style
# group psx files
#   num: -
#   read_mkt_summary: FLAG_RE, num
#   read_symbol_names: -
#   symbol_kind: FUTURE_RE
#   html_text: -
#   page_time: -
#   parse_screener: html_text, num
#   parse_company: PSX, html_text, num, page_time
#   parse_indices: html_text, num, page_time
# group data access
#   site_on: -
#   http_get: PAUSE, SLEEP, UA
#   daily_path: -
#   daily_rows: PSX, daily_path, http_get, read_mkt_summary, site_on
#   recent_sessions: daily_path, daily_rows, trading_day
#   daily_bars: -
#   page_html: SITE_OFF, http_get, site_on
#   company_html: PSX, page_html
#   screener_html: PSX, page_html
# group daily levels (closing files: payouts, the daily ATR and the trend rule)
#   adjusted_closes: -
#   window_sessions: trading_day
#   adjusted_bars: adjusted_closes
#   daily_atr: -
#   trend_break: -
# group tape and bars
#   tape_path: -
#   append_tape: -
#   read_tape: -
#   snapshot_from_company: pkt_iso
#   snapshot_from_screener: pkt_iso, to_pkt
#   bars_from_tape: num, slot_spans, to_pkt
#   last_asof: to_pkt
#   read_bars_csv: num, to_pkt, ts_to_pkt
#   resample_bars: slot_spans

import bisect
import csv
import datetime
import gzip
import html
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

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
MATCH_MINUTES = 2   # the opening match after each pre-open: no new orders until it ends

COSTS = {"commission_pct": 0.15, "commission_min_per_share": 0.03, "sales_tax_pct": 15,
         "other_charges_pct": 0.0066, "day_trade_one_side": False}

COST_WARN = 0.25    # warn when round-trip costs are over this share of the price risk
SWING = {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20, "trend_exit_sessions": 10,
         "entry_buffer_pct": 1.0, "max_open_positions": 3, "max_open_risk_pkr": None,
         "max_gap_loss_pkr": None, "stop_limit_gap_pct": 1.0}
SECTORS = {
    "0801": "AUTOMOBILE ASSEMBLER",
    "0802": "AUTOMOBILE PARTS & ACCESSORIES",
    "0803": "CABLE & ELECTRICAL GOODS",
    "0804": "CEMENT",
    "0805": "CHEMICAL",
    "0806": "CLOSE - END MUTUAL FUND",
    "0807": "COMMERCIAL BANKS",
    "0808": "ENGINEERING",
    "0809": "FERTILIZER",
    "0810": "FOOD & PERSONAL CARE PRODUCTS",
    "0811": "GLASS & CERAMICS",
    "0812": "INSURANCE",
    "0813": "INV. BANKS / INV. COS. / SECURITIES COS.",
    "0814": "JUTE",
    "0815": "LEASING COMPANIES",
    "0816": "LEATHER & TANNERIES",
    "0818": "MISCELLANEOUS",
    "0819": "MODARABAS",
    "0820": "OIL & GAS EXPLORATION COMPANIES",
    "0821": "OIL & GAS MARKETING COMPANIES",
    "0822": "PAPER, BOARD & PACKAGING",
    "0823": "PHARMACEUTICALS",
    "0824": "POWER GENERATION & DISTRIBUTION",
    "0825": "REFINERY",
    "0826": "SUGAR & ALLIED INDUSTRIES",
    "0827": "SYNTHETIC & RAYON",
    "0828": "TECHNOLOGY & COMMUNICATION",
    "0829": "TEXTILE COMPOSITE",
    "0830": "TEXTILE SPINNING",
    "0831": "TEXTILE WEAVING",
    "0832": "TOBACCO",
    "0833": "TRANSPORT",
    "0834": "VANASPATI & ALLIED INDUSTRIES",
    "0835": "WOOLLEN",
    "0836": "REAL ESTATE INVESTMENT TRUST",
    "0837": "EXCHANGE TRADED FUNDS",
    "0838": "PROPERTY",
    "0839": "APPAREL",
    "36": "DEBT",
    "40": "STOCK FUTURES",
    "41": "INDEX FUTURES",
}
FUTURE_RE = re.compile(r"^([A-Z0-9]+)-(C?)(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)([A-Z]?\d*)$")
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name


# ---------------------------------------------------------------- time

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


def pkt_iso(utc):
    """naive UTC -> Karachi time as "2026-10-29T10:15:00+05:00"."""
    return to_pkt(utc).strftime("%Y-%m-%dT%H:%M:%S") + "+%02d:00" % KARACHI_HOURS


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


# ---------------------------------------------------------------- bars grid

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


def slots(d, minutes=5, cfg=None):
    """Bar start times of date d (naive Karachi datetimes), counted across segments. A bar's
    number is its index here: bar 0 is 09:30 Mon to Thu, 09:15 on Friday and in Ramadan;
    Friday 14:30 is bar 33. [] when PSX is closed."""
    return [s for s, _ in slot_spans(d, minutes, cfg)]


def slot_index(d, when, minutes=5, cfg=None):
    """The bar number of a slot start (a naive Karachi datetime, or a time on day d).
    None when `when` is not exactly a slot start that day."""
    if isinstance(when, datetime.time):
        when = datetime.datetime.combine(d, when)
    starts = slots(d, minutes, cfg)
    return starts.index(when) if when in starts else None


def bar_close(d, index, minutes=5, cfg=None):
    """When bar `index` of date d closes: its start plus `minutes` (naive Karachi time).
    Mon to Thu bar 8 (the 10:10 bar) closes at 10:15. ValueError when there is no such bar."""
    spans = slot_spans(d, minutes, cfg)
    if not isinstance(index, int) or not 0 <= index < len(spans):
        raise ValueError("there is no bar %s on %s" % (index, d.isoformat()))
    return spans[index][1]


def opening_range_slots(d, or_minutes=15, minutes=5, cfg=None):
    """Slot starts of the opening range: the first or_minutes of the FIRST segment
    (09:30, 09:35, 09:40 Mon to Thu; 09:15, 09:20, 09:25 on Friday and in Ramadan).
    [] when closed. ValueError unless or_minutes is a multiple of the bar minutes."""
    spans = slot_spans(d, minutes, cfg)
    if or_minutes != int(or_minutes) or int(or_minutes) < minutes or int(or_minutes) % int(minutes):
        raise ValueError("the opening range wants a multiple of %d minutes" % int(minutes))
    if not spans:
        return []
    first = session(d, cfg)[0]
    start = datetime.datetime.combine(d, first[0])
    end = min(start + datetime.timedelta(minutes=int(or_minutes)), datetime.datetime.combine(d, first[1]))
    return [s for s, _ in spans if s < end]


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


# ---------------------------------------------------------------- prices and rules

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


def gap_loss(entry, qty, costs=None):
    """The loss of a long, costs included, if the next session opens at its lower price limit:
    max_loss_at_stop(entry, the lower limit of price_limits(entry), qty, costs). It takes the
    buy day's close to be the entry. More days down can lose more."""
    return max_loss_at_stop(entry, price_limits(entry)[0], qty, costs)


# ---------------------------------------------------------------- style (day or swing)

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


def read_symbol_names(raw):
    """PSX symbol name file (bytes of symbol_name/YYYY-MM-DD.zip with symbolname.lis, or
    the bare .lis) -> {symbol: (short name, full name)}. The text is UTF-16BE with a BOM
    in front of every piece; the BOMs are dropped. ValueError for an HTML page."""
    data = raw or b""
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                lis = [n for n in names if n.lower().endswith(".lis")] or names
                data = z.read(lis[0]) if lis else None
        except Exception:       # a broken zip raises BadZipFile, zlib.error, EOFError, RuntimeError, ...
            raise ValueError("a broken zip, not a PSX symbol name file")
        if data is None:
            raise ValueError("an empty zip, not a PSX symbol name file")
    if data[:2] == b"\xfe\xff":
        text = data.decode("utf-16-be", "replace")
    elif data[:2] == b"\xff\xfe":
        text = data.decode("utf-16-le", "replace")
    else:
        text = data.decode("utf-8", "replace")
    text = text.replace(chr(0xfeff), "")
    if text.lstrip()[:1] == "<":
        raise ValueError("an HTML page, not a PSX symbol name file")
    out = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        f = line.split("|")
        if len(f) >= 3 and f[0].strip():
            out[f[0].strip()] = (f[1].strip(), f[2].strip())
    return out


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


def parse_indices(html):
    """A saved https://dps.psx.com.pk/indices page -> {"as_of": naive Karachi datetime
    or None, "rows": {CODE: {"high", "low", "current", "change", "change_pct"}}}.
    A value the page shows as N/A is None."""
    page = html if isinstance(html, str) else ""
    i = page.find('id="indicesTable"')
    body = page[i:] if i >= 0 else page
    j = body.find("</table>")
    body = body[:j] if j >= 0 else body
    cell = r'[^<]*(?:<(?!/td>)[^<]*)*'
    m = re.search(r"As of\s+([^<]+)</div>", body)
    out = {"as_of": page_time(m.group(1)) if m else None, "rows": {}}
    for r in re.finditer(r'<tr><td>(' + cell + r')</td>((?:<td class="right[^"]*" data-order="[^"]*">' + cell +
                         r'</td>){5})</tr>', body):
        cm = re.search(r'data-code="([^"]+)"', r.group(1)) or re.search(r"<b[^>]*>([^<]+)</b>", r.group(1))
        if not cm:
            continue
        vals = [None if html_text(t).upper() == "N/A" else num(o) for o, t in re.findall(
            r'<td class="right[^"]*" data-order="([^"]*)">(' + cell + r')</td>', r.group(2))]
        keys = ("high", "low", "current", "change", "change_pct")
        out["rows"][html_text(cm.group(1)).upper()] = dict(zip(keys, vals))
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


def screener_html(root, cfg=None, offline=False):
    """The screener page through page_html(): the PSX site first when it is on and not
    offline, else <root>/screener.html. -> (html text, "site" or "saved"), or (None, a reason)."""
    return page_html(os.path.join(root, "screener.html"), PSX + "/screener", cfg, offline)


# ---------------------------------------------------------------- daily levels (closing files)

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


# ---------------------------------------------------------------- tape and bars

def tape_path(root, d, name):
    """<root>/tape/YYYY-MM-DD/<name>.jsonl: "market" for screener snapshots, a symbol
    for its company page snapshots."""
    return os.path.join(root, "tape", d.isoformat(), "%s.jsonl" % re.sub(r"[^A-Za-z0-9_-]", "_", str(name)))


def append_tape(path, snap):
    """Add one snapshot to a tape file as one JSON line. Makes the folders."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, sort_keys=True) + "\n")


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


def snapshot_from_company(parsed, fetched_utc):
    """parse_company() output and the fetch time (UTC) -> a tape snapshot: {"fetched"
    (Karachi ISO), "asof" ("YYYY-MM-DD HH:MM" Karachi, the page's As of), "source":
    "company", "symbol", "price", "open", "high", "low", "volume", "ldcp", "lower",
    "upper", "bid", "ask"}. open/high/low/volume are the day's so far."""
    p = parsed if isinstance(parsed, dict) else {}
    reg = p.get("reg") or {}
    asof = p.get("as_of")
    snap = {"fetched": pkt_iso(fetched_utc), "asof": asof.strftime("%Y-%m-%d %H:%M") if asof else None,
            "source": "company", "symbol": p.get("symbol"), "price": p.get("price")}
    for k in ("open", "high", "low", "volume", "ldcp", "lower", "upper", "bid", "ask"):
        snap[k] = reg.get(k)
    return snap


def snapshot_from_screener(parsed, fetched_utc, delay_minutes=5):
    """parse_screener() output and the fetch time (UTC) -> a tape snapshot: {"fetched"
    (Karachi ISO), "asof" (the fetch time minus the data delay, Karachi "YYYY-MM-DD
    HH:MM:SS"), "source": "screener", "prices": {SYM: price}}. Only prices above 0.
    The screener shows a stock with no trade today at its last day's price."""
    p = parsed if isinstance(parsed, dict) else {}
    asof = to_pkt(fetched_utc) - datetime.timedelta(minutes=delay_minutes)
    prices = {}
    for sym, row in (p.get("rows") or {}).items():
        price = (row or {}).get("price")
        if isinstance(price, (int, float)) and price > 0:
            prices[sym] = price
    return {"fetched": pkt_iso(fetched_utc), "asof": asof.strftime("%Y-%m-%d %H:%M:%S"), "source": "screener",
            "prices": prices}


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
