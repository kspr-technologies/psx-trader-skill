#!/usr/bin/env python3
"""Chart Scanner for psx-scanner: scan PSX charts for setups. Standard library only, Python 3.8.

    python3 scan.py universe                       # newest closing file -> psx/universe.txt
    python3 scan.py daily                          # before the open: daily setups -> psx/watchlist.json
    python3 scan.py daily --symbols OGDC,HUBC      # or --watchlist FILE, or --limit 50
    python3 scan.py tape                           # in the session: record the screener (site on)
    python3 scan.py orb                            # after 9:50 AM PKT: opening-range breakouts so far
    python3 scan.py orb --now "2026-10-29 10:15"   # replay a day from its tape

universe  Reads the newest 20 PSX closing files in the data folder (about a
          month), and the symbol name file when it is there, and keeps the
          ordinary shares. It leaves out GEM board, non-voting and preference
          shares, rights, ETFs, modarabas, REITs, closed-end funds, debt and
          futures. A share that did not trade on the newest day is still kept
          when it traded in the 19 sessions before. Kind and sector come from
          its newest row.

daily     Scans daily CLOSES from the closing files: the newest --sessions (60),
          30 closes or more a chart. Today's file counts only after the close.
          Floors on the last session: close Rs 10 or more (--min-price; under
          Rs 10 the Rs 0.03 a share minimum commission makes a round trip cost
          0.7% or more) and 500,000 shares or more (--min-volume). On an
          ex-date (XD, XB, XR) the older closes are scaled by LDCP / the close
          before, so a payout does not look like a fall. A PSX session in the
          window with no file gets a Note, and closes are never scaled across
          it. Setups:
            breakout_20d     the last close is above every close of the 20 before it
            bull_flag        up more than 10% over the 10 sessions before the
                             last 5, then the last 5 closes inside a 5% range
            reclaim_ma20     closed under the 20-day average, then closed above it
            oversold         RSI 14 (Wilder) under 30
            oversold_bounce  RSI 14 was under 30 the session before, and the last close is up

tape      Records the market tape: the PSX screener every --every (120) seconds,
          one JSON line a snapshot in <data>/tape/YYYY-MM-DD/market.jsonl.
          Needs "psx_site": true in psx/config.json. It starts in the pre-open
          or the session, and stops at --until or once the tape has seen the
          close (the screener is about 5 minutes late). It does not read while
          no bar can change: before the first bar shows (9:35 AM Mon to Thu),
          and in the Friday break once the tape has seen 12:00 (until 2:35 PM).
          Run only one tool that polls the PSX site at a time.

orb       Opening-range breakouts on 5-minute bars made from the day's tape.
          The range is the first --or-minutes (15) of the first segment. Only
          snapshots as of the cutoff (now, --now or --at) or before are used. A
          bar counts once it is complete: it ended by the cutoff, and the tape
          has a snapshot at or after its end. A chart is usable when every bar from
          the open to the last complete bar is there. A hit is the first
          complete bar after the range that CLOSES above the range high. The
          screener shows prices only, so a bar's high and low are only the
          prices the tape saw, about 5 minutes late. Floors: --min-price 10
          (the last complete bar's close), --min-volume 0 (the last closing file).

blocked   daily and orb leave out the shares on the "blocked" list in
          psx/config.json, in both styles, and say so in one line: "Left out
          2 on your blocked list: LUCK, DEMO." It names the blocked shares that
          matched, most traded first, and psx/watchlist.json then has
          "blocked_left_out". With "blocked": [] a scan is the same as before.
          universe and tape keep every share.

swing     With "style": "swing" in psx/config.json, run daily after the close,
          once the closing file is out. orb and tape are day tools: they still
          run, after one note.

Times are Karachi time (PKT, UTC+5 all year). --now pins the clock ("2026-10-29
10:15" is Karachi time, ...Z is UTC). In a replay, --now and --at are the tape's
own times (its "asof").

Data comes from the data folder (--data, default psx/data). The PSX site is used
only when psx/config.json says "psx_site": true (turn it on only if PSX has given
you permission), never with --offline or --now, with plain GETs at least 1.5 s apart.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import bisect
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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

BAR_MIN = 5
MIN_DAILY_BARS = 30
SESSIONS_BACK = 60      # daily: the newest 60 closing files (about 3 months)
UNIVERSE_SESSIONS = 20  # universe: the shares in the newest 20 closing files (about a month)
EVERY = 120             # tape: seconds between two screener snapshots
DEFAULT_DATA = "psx/data"
DEFAULT_UNIVERSE = "psx/universe.txt"
DEFAULT_CONFIG = "psx/config.json"
DEFAULT_WATCHLIST = "psx/watchlist.json"
NO_ENTRIES_AFTER = "15:00"  # when the config has no no_entries_after (written for Mon to Thu)
NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
LATE = "about 5 minutes late"
DAY_TOOLS = "orb and tape are for day trades; your style is swing."   # orb and tape, in swing style
KIND_WORDS = {"gem_equity": "GEM board share", "non_voting": "non-voting share", "preference": "preference share",
              "right": "right", "etf": "ETF", "modaraba": "modaraba", "reit": "REIT",
              "closed_end_fund": "closed-end fund", "debt": "debt", "stock_future": "stock future",
              "index_future": "index future"}

SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "back over the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- canonical PSX helpers
# Copied verbatim from tests/psx_core.py (never edit a copy; tests/test_repo.py checks them).

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
FUTURE_RE = re.compile(r"^([A-Z0-9]+)-(C?)(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)([A-Z]?\d*)$")
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name


# ---------------------------------------------------------------- time (canonical)

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


# ---------------------------------------------------------------- PSX calendar (canonical)

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


# ---------------------------------------------------------------- bars grid (canonical)

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


# ---------------------------------------------------------------- prices (canonical)

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


# ---------------------------------------------------------------- PSX files (canonical)

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


# ---------------------------------------------------------------- data access (canonical)

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


# ---------------------------------------------------------------- tape (canonical)

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


# ---------------------------------------------------------------- swing style and daily levels (copied verbatim from tests/psx_core.py; never edit a copy)

def trade_style(cfg):
    """"day" (the default) or "swing", from "style" in psx/config.json. Anything else
    raises ValueError with a plain message."""
    v = cfg.get("style") if isinstance(cfg, dict) else None
    if v is None or v == "day":
        return "day"
    if v == "swing":
        return "swing"
    raise ValueError('"style" in psx/config.json is "day" or "swing", not %r' % (v,))


# ---------------------------------------------------------------- universe

def names_file(root):
    """The newest symbol name file in <root>/symbol_name/ (YYYY-MM-DD.zip or .lis), or None."""
    folder = os.path.join(root, "symbol_name")
    if not os.path.isdir(folder):
        return None
    found = sorted(f for f in os.listdir(folder) if re.match(r"^\d{4}-\d{2}-\d{2}\.(zip|lis)$", f))
    return os.path.join(folder, found[-1]) if found else None


def fetch_names(root, day, cfg, a):
    """With the site on (and not --offline or --now), save PSX's symbol name file for `day`
    into <root>/symbol_name/YYYY-MM-DD.zip once, so the universe gets full company names.
    Returns a plain note when the fetch fails (the short names are used then), else None."""
    path = os.path.join(root, "symbol_name", day.isoformat() + ".zip")
    if os.path.exists(path) or not site_on(cfg) or no_network(a):
        return None
    url = "%s/download/symbol_name/%s.zip" % (PSX, day.isoformat())
    try:
        raw = http_get(url)
        read_symbol_names(raw)                              # a 404 page or a broken file is not saved
    except (urllib.error.URLError, OSError, ValueError) as e:
        return "no symbol name file from PSX for %s (%s), so the short names are used" % (
            day.isoformat(), getattr(e, "code", None) or getattr(e, "reason", None) or e)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as fh:
        fh.write(raw)
    os.replace(path + ".tmp", path)
    return None


def read_names(root):
    """(path or None, {SYM: (short name, full name)}) from the newest symbol name file."""
    path = names_file(root)
    if not path:
        return None, {}
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return path, read_symbol_names(raw)
    except ValueError as e:
        raise ScanError("%s: %s" % (path, e))


def clean_name(text):
    return " ".join(str(text or "").replace("|", "/").split())


def symbol_info(rows, names=None):
    """{SYM: {"name", "sector", "kind"}} for the rows of one closing file. The name is the
    symbol name file's full name, else the closing file's short name without its XD/XB/XR flag."""
    out = {}
    for r in rows or []:
        sym = str(r.get("symbol") or "").strip().upper()
        if not sym or sym in out:
            continue
        full = ((names or {}).get(sym) or ("", ""))[1]
        short = FLAG_RE.sub("", str(r.get("short_name") or "")).strip()
        out[sym] = {"name": clean_name(full) or clean_name(short) or None,
                    "sector": str(r.get("sector") or "").strip() or None,
                    "kind": symbol_kind(sym, r.get("sector"), full or r.get("short_name") or "")}
    return out


def build_universe(rows, names=None):
    """One closing file's rows -> (kept [(SYM, name, sector)] sorted, {kind: count} left out)."""
    info = symbol_info(rows, names)
    kept, left = [], {}
    for sym in sorted(info):
        kind = info[sym]["kind"]
        if kind == "ordinary":
            kept.append((sym, info[sym]["name"] or sym, info[sym]["sector"] or ""))
        else:
            left[kind] = left.get(kind, 0) + 1
    return kept, left


def read_universe(path):
    """psx/universe.txt (SYMBOL|Name|Sector code lines) -> {SYM: {"name", "sector"}}.
    When a symbol is there twice, its first line counts."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            f = [x.strip() for x in ln.strip().split("|")]
            if not f[0] or f[0].startswith("#") or f[0].upper() in out:
                continue
            out[f[0].upper()] = {"name": (f[1] if len(f) > 1 else "") or None,
                                 "sector": (f[2] if len(f) > 2 else "") or None}
    return out


def kinds_text(left):
    """{kind: count} -> "3 stock futures, 1 debt, 1 ETF" (most first)."""
    parts = []
    for kind, n in sorted(left.items(), key=lambda kv: (-kv[1], kv[0])):
        word = KIND_WORDS.get(kind, kind)
        parts.append("%d %s%s" % (n, word, "s" if n != 1 and word != "debt" else ""))
    return ", ".join(parts)


# ---------------------------------------------------------------- daily setups

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


def gap_note(missing):
    """A plain note for PSX sessions inside the window with no closing file, else ""."""
    if not missing:
        return ""
    labels = [day_label(d) for d in missing]
    if len(labels) == 1:
        what = "file for %s, a PSX session" % labels[0]
    elif len(labels) <= 3:
        what = "files for %s and %s, PSX sessions" % (", ".join(labels[:-1]), labels[-1])
    else:
        what = "files for %d PSX sessions, from %s to %s," % (len(labels), labels[0], labels[-1])
    return ("Note: no closing %s by the calendar. Save %s in mkt_summary/. Closes are not scaled for a "
            "payout across a missing day." % (what, "it" if len(labels) == 1 else "them"))


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers)."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
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
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


def setup_level(tags, nums):
    """The price the setup broke: the top close of the 20 before (breakout_20d), else the
    20-day average with the last close (reclaim_ma20), half up to 0.01; else None."""
    if "breakout_20d" in tags:
        return round_half_up_tick(nums["high20"])
    if "reclaim_ma20" in tags:
        return round_half_up_tick(nums["ma20"])
    return None


def daily_end(now_pkt, cfg=None):
    """(the newest date to read, whether that day's file counts): today's closing file
    counts only after today's close. On a closed day it counts."""
    d = now_pkt.date()
    segs = session(d, cfg)
    if not segs:
        return d, True
    return d, now_pkt >= datetime.datetime.combine(d, segs[-1][1])


def stale_note(newest, end, include_end, cfg=None):
    """A plain note when the newest closing file is older than the last session by the
    PSX calendar (a file not saved yet, or not out yet), else ""."""
    d = end if include_end else end - datetime.timedelta(days=1)
    for _ in range(30):
        if trading_day(d, cfg)[0]:
            break
        d -= datetime.timedelta(days=1)
    else:
        return ""
    if newest >= d:
        return ""
    return ("Note: the newest closing file is of %s, but PSX had a session on %s. Save the newer files in "
            "mkt_summary/ (a day's file shows about 30 to 40 minutes after the close)." % (day_label(newest),
                                                                                        day_label(d)))


def scan_daily(sessions, symbols, min_price, min_volume, info=None, cfg=None):
    """Pure. sessions = recent_sessions() output, oldest first. Floors use the newest session:
    the close and that day's volume (0 when the share did not trade then). The session before
    a day comes from the PSX calendar, so a missing file is a gap, never the day before.
    -> (hits, counts, the newest session's date)."""
    hits = []
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0}
    last_date = sessions[-1][0] if sessions else None
    by_sym = [(d, rows if isinstance(rows, dict) else dict((r["symbol"], r) for r in rows))
              for d, rows in sessions]
    every, missing = window_sessions([d for d, _ in sessions], cfg)
    prev = dict(zip(every[1:], every[:-1]))
    for sym in symbols:
        bars = daily_bars(sym, by_sym)
        if not bars:
            continue
        counts["fetched"] += 1
        last = bars[-1]
        vol = int(last.get("volume") or 0) if last["date"] == last_date else 0
        if last["close"] < min_price or vol < min_volume:
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        tags, nums = find_setups(adjusted_closes(bars, prev, missing))
        if tags:
            meta = (info or {}).get(sym) or {}
            hits.append({"symbol": sym, "name": meta.get("name"), "sector": meta.get("sector"), "setups": tags,
                         "close": round_half_up_tick(last["close"]), "close_date": last["date"].isoformat(),
                         "level": setup_level(tags, nums), "rsi": round(nums["rsi"], 1), "volume": vol})
    hits.sort(key=lambda h: (-(h["close"] * h["volume"]), h["symbol"]))   # most rupees traded first
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

def half_up(x, places=2):
    """A number rounded half up (0.449 -> 0.45 at 2 places)."""
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def tape_day(snaps, day, cutoff=None):
    """(the snapshots of `day` with an "asof" at or before the cutoff, the newest "asof" among
    them or None). A snapshot after the cutoff is never used, so a replay sees only what the
    tape held then (like psx-entry)."""
    mine = []
    for s in snaps or []:
        when = last_asof([s]) if isinstance(s, dict) else None
        if when is not None and when.date() == day and (cutoff is None or when <= cutoff):
            mine.append(s)
    return mine, last_asof(mine)


def complete_count(day, cutoff, last_seen, cfg=None):
    """How many bars of `day` are complete: a bar counts when it ended by the cutoff AND the
    tape has a snapshot at or after its end (last_seen = the tape's newest asof). Complete
    bars are always the first ones of the day."""
    if last_seen is None:
        return 0
    edge = min(cutoff, last_seen)
    return sum(1 for _, end in slot_spans(day, BAR_MIN, cfg) if end <= edge)


def usable(bars, n_done, n_range):
    """Every bar from bar 0 to the last complete one is there, and at least the range."""
    return n_done >= n_range and len(bars) >= n_done and all(b is not None for b in bars[:n_done])


def orb_hit(bars, n_range, day, cfg=None):
    """bars = a chart's complete bars (bar 0 first, no gap). -> the first bar after the range
    that CLOSES above the range high, or None. The range high and low are the prices seen."""
    rng = bars[:n_range]
    oh = max(b["high"] for b in rng)
    ol = min(b["low"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["close"] > oh:
            closes_at = bar_close(day, k, BAR_MIN, cfg)
            return {"price": round_half_up_tick(b["close"]), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round_half_up_tick(oh), "or_low": round_half_up_tick(ol),
                    "pct_over": half_up((b["close"] / oh - 1) * 100), "_t": closes_at}
    return None


def orb_window(now_utc, at, or_minutes, until, explicit_day=None, cfg=None):
    """-> (day, cutoff as naive Karachi time) or raise ScanError with one plain line.
    until = the last time for new entries that day (a datetime.time), or None."""
    now = to_pkt(now_utc)
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the classic)")
    ok, why = trading_day(day, cfg)
    if not ok:
        raise ScanError("No session on %s (%s): PSX is closed. Run orb on a trading day after the opening "
                        "range, or replay a day from its tape with --now." % (day_label(day), why))
    n_range = len(opening_range_slots(day, or_minutes, BAR_MIN, cfg))
    spans = slot_spans(day, BAR_MIN, cfg)
    range_start, range_end = spans[0][0], spans[n_range - 1][1]
    first_close = bar_close(day, n_range, BAR_MIN, cfg)
    close_dt = datetime.datetime.combine(day, session(day, cfg)[-1][1])
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s PKT)." % (at.strftime("%H:%M"), clock_label(now)))
        if cutoff > close_dt:
            raise ScanError("--at %s is after the %s close on %s." % (at.strftime("%H:%M"), clock_label(close_dt),
                                                                       day_label(day)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
        if cutoff >= close_dt:
            raise ScanError("PSX closed at %s PKT on %s. Replay the session with --at 10:15."
                            % (clock_label(close_dt), day_label(day)))
    if cutoff < first_close:
        raise ScanError("It is %s PKT. The %d-minute opening range runs %s to %s, and the first bar after it "
                        "closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_start), clock_label(range_end),
                           clock_label(first_close), clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s PKT, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb on the next trading day, or "
                        "replay today with --at 10:15." % (clock_label(cutoff), until.strftime("%H:%M")))
    return day, cutoff


def scan_orb(snaps, symbols, day, cutoff, or_minutes=15, min_price=0.0, min_volume=0, volumes=None, info=None,
             cfg=None):
    """Pure. snaps = the market tape (screener snapshots); volumes = {SYM: shares} from the
    last closing file. Snapshots after the cutoff are not used.
    -> (hits, counts, failed {sym: why}, unusable [sym])."""
    mine, last_seen = tape_day(snaps, day, cutoff)
    n_range = len(opening_range_slots(day, or_minutes, BAR_MIN, cfg))
    n_done = complete_count(day, cutoff, last_seen, cfg)
    seen = set()
    for s in mine:
        seen.update(str(k).upper() for k in (s.get("prices") or {}))
    counts = {"requested": len(symbols), "on_tape": 0, "unusable": 0, "illiquid": 0, "scanned": 0}
    hits, failed, bad = [], {}, []
    for sym in symbols:
        if sym not in seen:
            failed[sym] = "not on the tape"
            continue
        counts["on_tape"] += 1
        bars = bars_from_tape(mine, day, sym, BAR_MIN, cfg)[:n_done]
        if not usable(bars, n_done, n_range):
            counts["unusable"] += 1
            bad.append(sym)
            continue
        if bars[-1]["close"] < min_price or (volumes or {}).get(sym, 0) < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        h = orb_hit(bars, n_range, day, cfg)
        if h:
            meta = (info or {}).get(sym) or {}
            hits.append(dict({"symbol": sym, "name": meta.get("name"), "sector": meta.get("sector"),
                              "setups": ["orb"]}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))   # by the time the bar closed, then by symbol
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed, bad


def reg_volumes(rows):
    """{SYM: shares} of one closing file; 0 for a row with no regular-board trade."""
    out = {}
    for r in rows or []:
        traded = r.get("open") and r.get("high") and r.get("low")
        out[str(r.get("symbol") or "").upper()] = int(r.get("volume") or 0) if traded else 0
    return out


# ---------------------------------------------------------------- tape recorder

def fetch_screener():
    """GET the PSX screener page -> its HTML text. Only called when the site is on (tests swap it)."""
    return http_get(PSX + "/screener").decode("utf-8", "replace")


def tape_stop(now_pkt, cfg, delay, until=None):
    """Why the recorder stops now, or "" to go on. It records in pre-open, open and the Friday
    break, and a few minutes after the close, so the late data still shows the close."""
    if until is not None and now_pkt.time() >= until:
        return "it is %s, your --until time" % until.strftime("%H:%M")
    st = market_state(now_pkt, cfg)
    if st["state"] in ("pre-open", "open", "break"):
        return ""
    segs = session(now_pkt.date(), cfg)
    if segs:
        close = datetime.datetime.combine(now_pkt.date(), segs[-1][1])
        if close <= now_pkt < close + datetime.timedelta(minutes=delay + BAR_MIN):
            return ""
    return st["reason"] or "PSX is closed"


def tape_idle(now_pkt, cfg, delay, seen=None):
    """When a screener read cannot change a bar -> the Karachi time of the next useful read,
    else None (read now). That is before a segment's first bar shows on the late screener
    (grid start + delay), and, for a later segment, only once the tape has seen the end of
    the one before it (seen = the newest asof recorded, or None)."""
    d = now_pkt.date()
    segs = session(d, cfg)
    for i, (start, _) in enumerate(segs):
        first = datetime.datetime.combine(d, start) + datetime.timedelta(minutes=delay)
        ended = i == 0 or (seen is not None and seen >= datetime.datetime.combine(d, segs[i - 1][1]))
        if now_pkt < first and ended:
            return first
    return None


def data_delay(cfg):
    """The config's data_delay_minutes (5 when it is not set)."""
    v = (cfg or {}).get("data_delay_minutes", 5)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 60:
        raise ScanError('"data_delay_minutes" in psx/config.json wants a number of minutes from 0 to 60')
    return v


def record_tape(root, cfg, every, until=None, say=print):
    """Poll the screener every `every` seconds and append each snapshot to today's market
    tape, until tape_stop() says stop or the tape has seen the close. While no bar can change
    (tape_idle), it waits instead of reading. -> (snapshots, path, why)."""
    delay = data_delay(cfg)
    t0, k, n, errors, path, why, seen = utc_now(), 0, 0, 0, None, "", None
    try:
        while True:
            now = to_pkt(utc_now())
            why = tape_stop(now, cfg, delay, until)
            if why:
                break
            nxt = tape_idle(now, cfg, delay, seen)
            if nxt is not None:
                if until is not None:
                    nxt = min(nxt, datetime.datetime.combine(now.date(), until))
                say("%s PKT  no bar can change before %s: no reads until then"
                    % (clock_label(now), clock_label(nxt)))
                SLEEP(max(1.0, (nxt - now).total_seconds()))
                t0, k = utc_now(), 0
                continue
            try:
                page = fetch_screener()
            except (urllib.error.URLError, OSError) as e:      # an HTTPError is a URLError too
                errors += 1
                say("%s PKT  no snapshot: could not read the PSX screener (%s)"
                    % (clock_label(now), getattr(e, "reason", e)))
                if errors >= 5:
                    why = "5 reads of the screener in a row failed"
                    break
            else:
                errors = 0
                fetched = utc_now()
                snap = snapshot_from_screener(parse_screener(page), fetched, delay)
                if not snap["prices"]:
                    say("%s PKT  no prices on the screener page: nothing saved" % clock_label(to_pkt(fetched)))
                else:
                    path = tape_path(root, to_pkt(fetched).date(), "market")
                    append_tape(path, snap)
                    n += 1
                    asof = datetime.datetime.strptime(snap["asof"], "%Y-%m-%d %H:%M:%S")
                    seen = asof if seen is None else max(seen, asof)
                    say("%s PKT  %d prices, as of %s (%s)" % (clock_label(to_pkt(fetched)), len(snap["prices"]),
                                                             clock_label(asof), LATE))
                    segs = session(to_pkt(fetched).date(), cfg)
                    if segs and asof >= datetime.datetime.combine(asof.date(), segs[-1][1]):
                        why = "the tape has seen the %s close" % clock_label(segs[-1][1])
                        break
            k += 1
            wait = (t0 + datetime.timedelta(seconds=k * every) - utc_now()).total_seconds()
            if wait > 0:
                SLEEP(wait)
    except KeyboardInterrupt:
        why = "you pressed Ctrl+C"
    return n, path, why


# ---------------------------------------------------------------- symbol lists and files

def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def read_symbols(a, fallback=None):
    """-> (symbols, {SYM: {"name", "sector"}} from the universe file, where the list came from).
    The list: --symbols, else --watchlist, else --universe (psx/universe.txt). When that default
    file is not there, `fallback` is used (None: say to run `scan.py universe`). Duplicates are
    dropped and --limit cuts the list."""
    uni = a.universe or DEFAULT_UNIVERSE
    if a.symbols:
        syms, src = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s], "--symbols"
    elif a.watchlist:
        syms, src = symbols_from_file(a.watchlist), a.watchlist
    elif os.path.exists(uni):
        syms, src = symbols_from_file(uni), uni
    elif a.universe or fallback is None:
        raise ScanError("no %s yet: run `scan.py universe` first, or pass --symbols" % uni)
    else:
        syms, src = list(fallback), None
    out = []
    for s in syms:
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out, (read_universe(uni) if os.path.exists(uni) else {}), src


def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln.split("|")[0].strip().upper())
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def blocked_set(value):
    """The "blocked" tickers of psx/config.json as a set, the rule psx-budget and psx-risk use:
    a list, or one string split on commas ("ABC, XYZ"). Anything else is a plain error."""
    if value is None:
        return set()
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        raise ScanError("\"blocked\" in psx/config.json must be a list of tickers, like [\"ABC\", \"XYZ\"]")
    return {str(b).strip().upper() for b in value if b is not None and str(b).strip()}


def leave_out_blocked(hits, blocked):
    """Pure. hits = scan_daily() or scan_orb() hits, in their order. -> (the hits that are not
    on the blocked list, the symbols of the ones that are, in the same order). A blocked share
    is still read, so the counts stay the same: it is only never a hit."""
    kept = [h for h in hits if h["symbol"] not in blocked]
    return kept, [h["symbol"] for h in hits if h["symbol"] in blocked]


def blocked_line(left):
    """The one line for the blocked shares a scan left out, else "" (then the output is v1's)."""
    if not left:
        return ""
    return "Left out %d on your blocked list: %s." % (len(left), ", ".join(left))


def day_tools_note(a, cfg):
    """orb and tape are day tools. In swing style they print one note first, then run as
    usual. With --json the note goes to stderr, so the JSON stays clean."""
    if trade_style(cfg) == "swing":
        print(DAY_TOOLS, file=sys.stderr if getattr(a, "json", False) else sys.stdout)


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def no_network(a):
    """True when this run must not use the network: --offline, or a replay with --now."""
    return bool(a.offline or getattr(a, "now", None))


def read_sessions(end, n, a, cfg, include_end=True):
    """recent_sessions() from the data folder; with the site on, missing files are fetched
    (never with --offline or --now)."""
    if site_on(cfg) and not no_network(a):
        progress(a, "Reading closing files from %s (missing ones come from the PSX site, 1.5 s apart)..."
                 % os.path.join(a.data, "mkt_summary"))
    try:
        return recent_sessions(end, n, a.data, cfg, no_network(a), include_end)
    except urllib.error.HTTPError as e:
        raise ScanError("PSX answered HTTP %d for a closing file" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise ScanError("could not reach PSX for a closing file: %s" % getattr(e, "reason", e))


def no_files(a, cfg, before=None):
    """The one plain line for "no closing file"."""
    folder = os.path.join(a.data, "mkt_summary")
    if not site_on(cfg):
        return SITE_OFF % ("the PSX closing files (%s/download/mkt_summary/YYYY-MM-DD.Z) in %s" % (PSX, folder))
    if a.offline:
        return "no closing files in %s (--offline reads only the data folder)" % folder
    if getattr(a, "now", None):
        return "no closing files in %s (a run with --now reads only the data folder)" % folder
    return ("PSX had no closing file%s yet (a day's file shows about 30 to 40 minutes after the close)"
            % ((" before %s" % before.isoformat()) if before else ""))


def floor_text(price, volume):
    """"close Rs 10 or more, 500,000 shares or more"."""
    rs = money(price, 0) if float(price) == int(price) else money(price)
    return "close %s or more, %s shares or more" % (rs, "{:,}".format(int(volume)))


def names_list(syms, what):
    """"PIOC has a missing bar", "PIOC and SYS have ...", or "7 have ..." for many."""
    if len(syms) == 1:
        return "%s has %s" % (syms[0], what)
    if len(syms) <= 5:
        return "%s and %s have %s" % (", ".join(syms[:-1]), syms[-1], what)
    return "%d have %s" % (len(syms), what)


# ---------------------------------------------------------------- commands

def _n(x):
    return "{:,}".format(x)


def cmd_universe(a):
    t0 = time.time()
    cfg = read_config(a.config)
    trade_style(cfg)                  # "style" is "day" or "swing": anything else is one plain error
    end, with_end = daily_end(to_pkt(parse_now(a.now)), cfg)
    got = read_sessions(end, UNIVERSE_SESSIONS, a, cfg, with_end)
    if not got:
        raise ScanError(no_files(a, cfg))
    day, rows = got[-1]
    names_note = fetch_names(a.data, day, cfg, a)
    names_path, names = read_names(a.data)
    kept, left = build_universe([r for _, rs in reversed(got) for r in rs], names)   # a share's newest row first
    newest = set(str(r.get("symbol") or "").strip().upper() for r in rows)
    older = [s for s, _, _ in kept if s not in newest]       # kept, but no trade on the newest day
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            for sym, name, sector in kept:
                fh.write("%s|%s|%s\n" % (sym, name, sector))
    if a.json:
        print(json.dumps({"count": len(kept), "rows": len(rows), "date": day.isoformat(), "sessions": len(got),
                          "first_date": got[0][0].isoformat(), "not_traded_on_date": older,
                          "names_file": os.path.basename(names_path) if names_path else None, "left_out": left,
                          "out": None if a.no_write else a.out, "seconds": round(time.time() - t0, 1)}, indent=2))
        return 0 if kept else 1
    print("PSX closing file of %s: %s rows; %s." % (
        day_label(day), _n(len(rows)), ("names from %s (%s symbols)" % (os.path.basename(names_path), _n(len(names))))
        if names_path else names_note or "no symbol name file, so the short names are used"))
    if stale_note(day, end, with_end, cfg):
        print(stale_note(day, end, with_end, cfg))
    line = "Kept %s ordinary shares" % _n(len(kept))
    if older:
        line += ": %s traded on %s, %s only in the %d session%s before%s" % (
            _n(len(kept) - len(older)), day.isoformat(), _n(len(older)), len(got) - 1, "" if len(got) == 2 else "s",
            (" (%s)" % ", ".join(older)) if len(older) <= 5 else "")
    print(line + ".")
    if left:
        print("Left out %s: %s." % (_n(sum(left.values())), kinds_text(left)))
    if not a.no_write:
        print("Wrote %s (one SYMBOL|Name|Sector code a line)." % a.out)
    return 0 if kept else 1


def cmd_daily(a):
    t0 = time.time()
    cfg = read_config(a.config)
    trade_style(cfg)                  # "style" is "day" or "swing": anything else is one plain error
    blocked = blocked_set(cfg.get("blocked"))
    now = parse_now(a.now)
    local = to_pkt(now)
    if a.sessions < MIN_DAILY_BARS:
        raise ScanError("--sessions wants %d or more (a chart needs %d closes)" % (MIN_DAILY_BARS, MIN_DAILY_BARS))
    end, with_end = daily_end(local, cfg)
    sessions = read_sessions(end, a.sessions, a, cfg, with_end)
    if not sessions:
        raise ScanError(no_files(a, cfg))
    last_date, last_rows = sessions[-1]
    info = symbol_info(last_rows, read_names(a.data)[1])
    symbols, file_info, src = read_symbols(a, [s for s in sorted(info) if info[s]["kind"] == "ordinary"])
    for s, v in file_info.items():
        info.setdefault(s, {}).update(dict((k, x) for k, x in v.items() if x))
    hits, counts, _ = scan_daily(sessions, symbols, a.min_price, a.min_volume, info, cfg)
    hits, left = leave_out_blocked(hits, blocked)
    counts["hits"] = len(hits)
    missing = window_sessions([d for d, _ in sessions], cfg)[1]
    nfailed = len(symbols) - counts["fetched"]
    doc = {"created_by": "psx-scanner", "created_at": utc_iso(now), "mode": "daily",
           "as_of": "%s close" % last_date.isoformat(), "universe": len(symbols), "scanned": counts["scanned"],
           "failed": nfailed, "source": "psx-closing-files", "sessions": len(sessions),
           "first_date": sessions[0][0].isoformat(),
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume}}
    if left:
        doc["blocked_left_out"] = left
    doc["note"], doc["hits"] = NOTE, hits
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, missing_files=[d.isoformat() for d in missing],
                              seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    print("Daily scan, %s %s PKT. Closes through %s: %d PSX closing files from %s." % (
        day_label(local), clock_label(local), day_label(last_date), len(sessions), sessions[0][0].isoformat()))
    for note in (calendar_note(local.date(), cfg), stale_note(last_date, end, with_end, cfg), gap_note(missing)):
        if note:
            print(note)
    if src is None:
        print("No %s yet: scanning the ordinary shares of the %s closing file (scan.py universe saves the list)."
              % (a.universe or DEFAULT_UNIVERSE, last_date.isoformat()))
    have = "%s symbols" % _n(len(symbols))
    if nfailed:
        have += ", %s with closes (%s had none)" % (_n(counts["fetched"]), _n(nfailed))
    print("%s. %s passed the floors (%s on %s). %s scanned%s." % (
        have, _n(counts["fetched"] - counts["illiquid"]), floor_text(a.min_price, a.min_volume),
        last_date.isoformat(), _n(counts["scanned"]),
        (" (%d had under %d closes)" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else ""))
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s matched a setup. By setup: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                                            ", ".join("%s %d" % (LABEL[k], n) for k, n in per if n)))
    else:
        print("No setups.")
    if left:
        print(blocked_line(left))     # near the top, so a short report still shows it
    if hits:
        print("Most traded first (close x volume):")
    for h in hits[:a.show]:
        print("  %-6s %8s  RSI %5.1f  %s%s" % (h["symbol"], "%.2f" % h["close"], h["rsi"],
                                              ", ".join(LABEL[t] for t in h["setups"]),
                                              (" (level %.2f)" % h["level"]) if h["level"] is not None else ""))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /psx-news SYMBOL.")
    return 0 if hits else 1


def cmd_orb(a):
    t0 = time.time()
    cfg = read_config(a.config)
    day_tools_note(a, cfg)            # swing: one note first, then the scan runs as usual
    blocked = blocked_set(cfg.get("blocked"))
    now = parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    snaps, explicit_day, path = None, None, a.tape
    if a.tape:
        if not os.path.exists(a.tape):
            raise ScanError("no tape file at %s" % a.tape)
        snaps = read_tape(a.tape)
        if not a.now:
            newest = last_asof(snaps)
            if newest is None:
                raise ScanError("%s has no snapshots with an asof time" % a.tape)
            explicit_day = newest.date()
            if at is None:
                raise ScanError('a tape replay wants --at HH:MM or --now "YYYY-MM-DD HH:MM"')
    day0 = explicit_day or to_pkt(now).date()
    if a.until:
        until = parse_hhmm(a.until, "--until")
    else:
        until = config_time(parse_hhmm(cfg.get("no_entries_after") or NO_ENTRIES_AFTER, "no_entries_after"),
                            day0, cfg)
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day, cfg)
    if snaps is None:
        path = tape_path(a.data, day, "market")
        if not os.path.exists(path):
            raise ScanError('No market tape for %s at %s. Record one with "scan.py tape" in the session (it needs '
                            '"psx_site": true), or pass --tape FILE.' % (day_label(day), path))
        snaps = read_tape(path)
    mine, last_seen = tape_day(snaps, day, cutoff)
    if not mine:
        raise ScanError("%s has no snapshots of %s%s" % (path, day_label(day), (" as of %s PKT or before"
                        % clock_label(cutoff)) if tape_day(snaps, day)[0] else ""))
    spans = slot_spans(day, BAR_MIN, cfg)
    n_range = len(opening_range_slots(day, a.or_minutes, BAR_MIN, cfg))
    if complete_count(day, cutoff, last_seen, cfg) <= n_range:
        raise ScanError("The tape reaches only %s PKT (the screener is %s), so no bar after the opening range is "
                        "complete yet. Run orb again in a few minutes." % (clock_label(last_seen), LATE))
    prev = read_sessions(day, 1, a, cfg, include_end=False)
    if a.min_volume > 0 and not prev:
        raise ScanError("--min-volume needs the closing file of the session before %s. %s"
                        % (day.isoformat(), no_files(a, cfg, day)))
    rows = prev[-1][1] if prev else []
    info = symbol_info(rows, read_names(a.data)[1])
    seen = sorted(set(str(k).upper() for s in mine for k in (s.get("prices") or {})))
    fallback = [s for s in sorted(info) if info[s]["kind"] == "ordinary"] if rows else seen
    symbols, file_info, src = read_symbols(a, fallback)
    for s, v in file_info.items():
        info.setdefault(s, {}).update(dict((k, x) for k, x in v.items() if x))
    hits, counts, failed, bad = scan_orb(mine, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_volume,
                                         reg_volumes(rows), info, cfg)
    hits, left = leave_out_blocked(hits, blocked)
    counts["hits"] = len(hits)
    edge = min(cutoff, last_seen)
    doc = {"created_by": "psx-scanner", "created_at": utc_iso(now), "mode": "orb",
           "as_of": "%s %s PKT" % (day.isoformat(), cutoff.strftime("%H:%M")),
           "tape_until": "%s PKT" % edge.strftime("%Y-%m-%d %H:%M:%S"), "universe": len(symbols),
           "scanned": counts["scanned"], "failed": len(failed), "unusable": counts["unusable"],
           "source": "psx-screener-tape", "or_minutes": a.or_minutes, "interval": "%dm" % BAR_MIN,
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume}}
    if left:
        doc["blocked_left_out"] = left
    doc["note"], doc["hits"] = NOTE, hits
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2))
        return 0 if hits else 1
    print("Opening-range breakout scan, %s %s PKT: %d-minute range (%s to %s), 5-minute bars from the market tape."
          % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(spans[0][0]),
             clock_label(spans[n_range - 1][1])))
    if calendar_note(day, cfg):
        print(calendar_note(day, cfg))
    print("Tape: %d screener snapshots, as of %s to %s PKT. Bars that ended by %s count%s." % (
        len(mine), clock_label(min(last_asof([s]) for s in mine)), clock_label(last_seen), clock_label(edge),
        " (the tape has nothing newer)" if edge < cutoff else ""))
    print("The screener is %s, and a bar's high and low are only the prices the tape saw." % LATE)
    if src is None:
        print("No %s yet: scanning the %s." % (a.universe or DEFAULT_UNIVERSE, (
            "ordinary shares of the %s closing file" % prev[-1][0].isoformat()) if rows else "symbols on the tape"))
    line = "%s chart%s on the tape" % (_n(counts["on_tape"]), "" if counts["on_tape"] == 1 else "s")
    if failed:
        line = "%s of %s charts on the tape (%s not on it)" % (_n(counts["on_tape"]), _n(len(symbols)),
                                                                _n(len(failed)))
    line += ". %s usable%s. %s scanned%s." % (
        _n(counts["on_tape"] - counts["unusable"]), (" (%s)" % names_list(bad, "a missing bar")) if bad else "",
        _n(counts["scanned"]),
        (" (%s under the price or volume floor)" % _n(counts["illiquid"])) if counts["illiquid"] else "")
    print(line)
    if hits:
        print("%d of %s closed above their opening-range high by %s:" % (
            len(hits), ("the %s charts over the floors" if counts["illiquid"] else "%s usable charts")
            % _n(counts["scanned"]), clock_label(edge)))
        for h in hits[:a.show]:
            print("  %8s  %-6s %8s  %-22s  %+.2f%% over   (bar %d)" % (
                h["at"], h["symbol"], "%.2f" % h["price"], "range %.2f to %.2f" % (h["or_low"], h["or_high"]),
                h["pct_over"], h["bar_index"]))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(edge))
    if left:
        print(blocked_line(left))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE + " Next: pick one chart, then /psx-news SYMBOL and /psx-entry SYMBOL.")
    return 0 if hits else 1


def cmd_tape(a):
    cfg = read_config(a.config)
    day_tools_note(a, cfg)            # swing: one note first, then the tape runs as usual
    if a.offline:
        raise ScanError("the tape reads the PSX screener, and --offline never uses the network: drop --offline")
    if not site_on(cfg):
        raise ScanError('The tape needs "psx_site": true in psx/config.json (turn it on only if PSX has given you '
                        'permission). A saved page cannot make a tape: without the site, use a bars file from your '
                        'broker with psx-entry.')
    if not 60 <= a.every <= 300:
        raise ScanError("--every wants 60 to 300 seconds (the bars are 5 minutes)")
    until = parse_hhmm(a.until, "--until") if a.until else None
    now = to_pkt(utc_now())
    why = tape_stop(now, cfg, data_delay(cfg), until)
    if why:
        raise ScanError("Not recording. %s." % (why[:1].upper() + why[1:]))
    path = tape_path(a.data, now.date(), "market")
    print("Recording the PSX screener every %d s into %s (Ctrl+C stops it). The screener is %s."
          % (a.every, path, LATE))
    n, got, why = record_tape(a.data, cfg, a.every, until, (lambda s: None) if a.quiet else print)
    print("Stopped. %s. %d snapshot%s in %s." % (why[:1].upper() + why[1:], n, "" if n == 1 else "s", got or path))
    return 0 if n else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Chart Scanner (psx-scanner): PSX daily setups and opening-range "
                                             "breakouts. Times are Karachi time (PKT).")
    sub = ap.add_subparsers(dest="cmd")

    def base(p):
        p.add_argument("--data", default=DEFAULT_DATA, help="the data folder (default %s)" % DEFAULT_DATA)
        p.add_argument("--config", default=DEFAULT_CONFIG, help="default %s" % DEFAULT_CONFIG)
        p.add_argument("--offline", action="store_true", help="never use the network: read only the data folder")

    def common(p, min_volume):
        base(p)
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of symbols")
        p.add_argument("--universe", help="one SYMBOL|Name|Sector code a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=10.0,
                       help="rupees (10: under Rs 10 the Rs 0.03 a share minimum commission makes a round trip "
                            "cost 0.7%% or more)")
        p.add_argument("--min-volume", type=int, default=min_volume, help="shares on the last session")
        p.add_argument("--now", help='pretend it is this time ("2026-10-29 10:15" Karachi time, or ...Z)')
        p.add_argument("--out", default=DEFAULT_WATCHLIST)
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("universe", help="the newest closing file -> %s (ordinary shares)" % DEFAULT_UNIVERSE)
    base(p)
    p.add_argument("--now", help='pretend it is this time ("2026-10-29 09:00" Karachi time, or ...Z)')
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("daily", help="setups from daily closes, before the open")
    common(p, 500000)
    p.add_argument("--sessions", type=int, default=SESSIONS_BACK, help="closing files to read (60)")
    p = sub.add_parser("orb", help="opening-range breakouts so far today, from the market tape")
    common(p, 0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--at", help="cut the clock at this Karachi time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM that day (default: no_entries_after in the config)")
    p.add_argument("--tape", metavar="FILE", help="a market tape file (default <data>/tape/<day>/market.jsonl)")
    p = sub.add_parser("tape", help="record the market tape from the PSX screener (needs the site on)")
    base(p)
    p.add_argument("--every", type=int, default=EVERY, help="seconds between snapshots (%d)" % EVERY)
    p.add_argument("--until", help="stop at this Karachi time, HH:MM (default: once the tape has seen the close)")
    p.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb, "tape": cmd_tape}[a.cmd](a)
    except (ScanError, ValueError) as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
