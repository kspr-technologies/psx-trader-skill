#!/usr/bin/env python3
"""Entry Timer for psx-entry: watches a setup bar by bar on the Pakistan Stock Exchange
and says the moment to get in. Standard library only, Python 3.8. Long side only. It
never places an order.

    python3 entry.py check DEMO                           # opening-range breakout, 5-minute bars
    python3 entry.py check DEMO --now "2026-10-29 10:15" --data tests/fixtures/psx-market
    python3 entry.py check DEMO --setup level --level 96.56     # first close above a price
    python3 entry.py check DEMO --bars psx/data/bars/DEMO-1m.csv --interval 1m --write
    python3 entry.py watch DEMO --until 15:00              # records the company page, one line a bar
    python3 entry.py watchlist                             # every hit in psx/watchlist.json

Setups (longs only):
  orb    opening-range breakout. The range is the high and low of the first
         --or-minutes (default 15) of the first session: the 9:30, 9:35 and 9:40
         bars Monday to Thursday, the 9:15, 9:20 and 9:25 bars on Friday and in
         Ramadan. It triggers on the first CLOSED bar after the range that CLOSES
         above the range high. A wick above the high is not a trigger.
  level  the first closed bar that closes above --level (for example a 20-day
         high from psx-scanner), from the first bar that ends at or after
         no_entries_before.
A bar that closes when a session ends (12:00 on a Friday, or the close) is not a
trigger: an order cannot fill then. If the opening range misses bars, there is no
trigger, unless a company tape started inside the range on a page with the day's
high and low: those make the range.
  --min-volume-x X  the trigger bar also needs at least X times the average
         volume of the opening-range bars. The first bar holds the opening match,
         so even 0.5 is a high bar.

A trigger's time is the trigger bar's CLOSE time (the 10:10 bar closes at 10:15 AM)
and its price is that close. A bar's number is its place in the day's 5-minute slots,
counted across the sessions (bar 8 is the 10:10 bar Monday to Thursday; Friday 14:30 is
bar 33). A bar counts only once it has closed: its end is at or before the clock and
the data has moved past it.

Bars come from the first source that has them for the day:
  --bars FILE                       a bars CSV from your broker (a tape .jsonl works too)
  <data>/bars/<SYM>.csv             a bars CSV from your broker, 1-minute or 5-minute rows
  <data>/tape/<date>/<SYM>.jsonl    company page snapshots (watch records them)
  <data>/tape/<date>/market.jsonl   screener snapshots (psx-scanner): prices only, no volume
Free PSX data is about 5 minutes late. A tape's time is the page's "As of" time, so
--now replays a day by data time.

Statuses: triggered; waiting (not open yet, range still forming, the Juma break, or no
close above the level yet); expired (no trigger by no_entries_after, 15:00, which
config_time moves for other hours: 16:00 on a Friday); no-data (PSX is closed today, or
no bars for the day).

psx/config.json (optional): "no_entries_before" (09:45) and "no_entries_after" (15:00),
written for Monday to Thursday; "psx_site" (watch needs it true); "data_delay_minutes"
(5); "holidays", "open_days" and "ramadan" fix the calendar.

watch polls the PSX company page every --every seconds (default 60) while PSX trades,
and only when psx/config.json says "psx_site": true (turn it on only if PSX has given
you permission). It adds each page to the symbol's tape, prints one line per closed bar
and stops on a trigger (exit 0, writes psx/entry.json), on expiry, at the close or at
--until (exit 1). With --now or --offline it replays the saved data on a simulated
clock without waiting, and never uses the site. Run only one tool that polls the PSX
site at a time.

Exit codes: check 0 = triggered, 1 = anything else or an error. watch 0 on a trigger,
1 otherwise. watchlist 0 when at least one symbol triggered.
"""

import argparse
import bisect
import csv
import datetime
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

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

GRACE = 2                # seconds after a bar closes before a replay looks at it
INTERVALS = {"1m": 1, "5m": 5}   # bar minutes
DEFAULTS = {"no_entries_before": "09:45", "no_entries_after": "15:00", "data_delay_minutes": 5}
MIN_EVERY = 30           # seconds between two polls of the PSX site, at least (the site is shared)
NO_BARS = ('The PSX site is off for this tool. Export 5-minute bars from your broker and save them as %s, '
           'or set "psx_site": true in psx/config.json if PSX has given you permission.')


class EntryError(Exception):
    pass


# ---------------------------------------------------------------- time (Karachi, UTC+5)

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


def hm(t):
    """10:10, 09:45: the contract's wall-clock form."""
    return "%02d:%02d" % (t.hour, t.minute)


def short(t):
    """9:45, 10:10: for sentences."""
    return "%d:%02d" % (t.hour, t.minute)


def now_arg(text):
    """--now -> naive UTC (parse_now: Karachi time unless it says Z or an offset). A bare
    HH:MM means today in Karachi."""
    t = str(text or "").strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_pkt(utc_now()).date().isoformat(), t.zfill(5))
    try:
        return parse_now(t)
    except ValueError as e:
        raise EntryError(str(e))


def pkt_field(snap, key):
    """A tape snapshot's "asof" or "fetched" as a naive Karachi datetime, or None. A time
    with no offset is Karachi time; one with Z or an offset is turned into Karachi time."""
    t = str((snap or {}).get(key) or "").strip()
    try:
        when = datetime.datetime.fromisoformat(t[:-1] + "+00:00" if t[-1:] in ("Z", "z") else t)
    except ValueError:
        return None
    return to_pkt(when) if when.tzinfo is not None else when


# ---------------------------------------------------------------- PSX calendar and the bars grid

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


def next_open(d, cfg=None):
    """The next session's first open after date d: "YYYY-MM-DD HH:MM" Karachi time, or None."""
    nd = d + datetime.timedelta(days=1)
    for _ in range(15):
        segs = session(nd, cfg)
        if segs:
            return "%s %s" % (nd.isoformat(), hm(segs[0][0]))
        nd += datetime.timedelta(days=1)
    return None


def cfg_time(cfg, key, d):
    """A window time from the config (written for Monday to Thursday) for date d, as a naive
    Karachi datetime: config_time() shifts it for other hours and keeps it in the session."""
    try:
        t = parse_hhmm(cfg.get(key) or DEFAULTS[key], key)
    except ValueError as e:
        raise EntryError(str(e))
    return datetime.datetime.combine(d, config_time(t, d, cfg))


# ---------------------------------------------------------------- PSX pages and the site

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


def _sleep(seconds):
    SLEEP(seconds)


def poll_company(sym, cfg, now_utc):
    """watch's poll: one fresh company page from the PSX site -> a tape snapshot fetched at
    now_utc. It gives page_html no saved file: a saved page never moves, so it must not
    stand in for the site when a read fails. Only call this with the site on."""
    text, how = page_html(None, "%s/company/%s" % (PSX, re.sub(r"[^A-Z0-9-]", "", str(sym).upper())), cfg)
    if text is None:
        raise EntryError(how)
    page = parse_company(text)
    if page.get("symbol") and page["symbol"] != sym:
        raise EntryError("the PSX page is for %s, not %s" % (page["symbol"], sym))
    if page.get("price") is None or page.get("as_of") is None:
        raise EntryError("the PSX page for %s shows no price or no As of time" % sym)
    if page["as_of"] > to_pkt(now_utc) + datetime.timedelta(minutes=2):
        raise EntryError("the PSX page for %s says As of %s, later than this computer's clock (%s PKT): check the "
                         "clock" % (sym, page["as_of"].strftime("%Y-%m-%d %H:%M"), to_pkt(now_utc).strftime("%H:%M")))
    return snapshot_from_company(page, now_utc)


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


def csv_step(rows):
    """The row size of a bars file in whole minutes: the smallest gap between two rows
    (5 when there is only one row)."""
    gaps = [(b["start"] - a["start"]).total_seconds() / 60.0 for a, b in zip(rows, rows[1:])]
    gaps = [g for g in gaps if g > 0]
    return int(round(min(gaps))) if gaps else 5


def _shown(path, data):
    """A path for people: inside the data folder it is shown from there (tape/...)."""
    try:
        rel = os.path.relpath(path, data)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel


def _read_source(kind, path, sym, day, loose=False):
    """One source -> its part of a chart when it has data of sym on day, else None. loose (the
    tape a polling watch records): any snapshot counts, even one from before today's first
    trade, so the data time still moves on."""
    if not os.path.isfile(path):
        return None
    if kind == "file":
        kind = "tape" if path.lower().endswith(".jsonl") else "bars-file"
    if kind == "bars-file":
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                rows = read_bars_csv(fh.read()).get(day) or []
        except (OSError, ValueError) as e:
            raise EntryError("could not read the bars file %s: %s" % (path, e))
        if not rows:
            return None
        step = csv_step(rows)
        if step not in (1, 5):
            raise EntryError("%s has %d-minute rows: entry reads 1-minute or 5-minute bars" % (path, step))
        return {"kind": "bars-file", "rows": rows, "step": step,
                "has_volume": any(r.get("volume") is not None for r in rows)}
    snaps = read_tape(path)
    if kind == "tape":
        kind = "screener-tape" if any("prices" in s for s in snaps) else "company-tape"
    mine = []
    for s in snaps:
        when = pkt_field(s, "asof")
        if when is None or when.date() != day:
            continue
        if kind == "screener-tape":
            price = num((s.get("prices") or {}).get(sym))
        elif str(s.get("symbol") or sym).strip().upper() == sym:
            price = num(s.get("price"))
        else:
            continue
        if price is not None and price > 0:
            mine.append(s)
    if not mine and not (loose and snaps):
        return None
    return {"kind": kind, "snaps": snaps, "has_volume": kind == "company-tape" and any(
        num(s.get("volume")) is not None for s in mine)}


def _page_facts(snaps, sym):
    """(last close, lower limit, upper limit) from the newest company snapshot that has them."""
    for s in reversed(snaps or []):
        if str(s.get("symbol") or sym).strip().upper() == sym and s.get("ldcp") is not None:
            return num(s.get("ldcp")), num(s.get("lower")), num(s.get("upper"))
    return None, None, None


def load_chart(sym, day, data, bars_file=None, cfg=None, live=False, tape_only=False):
    """The day's data of sym from the first source that has it -> a chart for evaluate().

    The order: bars_file (a bars CSV, or a tape .jsonl), <data>/bars/<SYM>.csv,
    <data>/tape/<date>/<SYM>.jsonl (company page snapshots) and <data>/tape/<date>/market.jsonl
    (screener snapshots: prices only, no volume). tape_only (a live watch) reads only the
    company tape it records. live means no --now: a bars file then counts only up to the
    time it was saved, and a tape's data is about data_delay_minutes behind the clock. With
    no source, "kind" is None and "missing" says what to save where."""
    cfg = cfg or {}
    sym = str(sym).strip().upper()
    company = tape_path(data, day, sym)
    if tape_only:
        tries = [("company-tape", company)]
    else:
        tries = []
        if bars_file:
            if not os.path.isfile(bars_file):
                raise EntryError("no bars file at %s" % bars_file)
            tries.append(("file", bars_file))
        tries += [("bars-file", os.path.join(data, "bars", sym + ".csv")), ("company-tape", company),
                  ("screener-tape", tape_path(data, day, "market"))]
    chart = {"symbol": sym, "date": day, "kind": None, "path": None, "shown": None, "label": None, "rows": None,
             "snaps": None, "step": None, "saved_at": None, "has_volume": False, "lag": 0, "note": None,
             "delay": cfg.get("data_delay_minutes", DEFAULTS["data_delay_minutes"]), "missing": None,
             "prev_close": None, "lower": None, "upper": None}
    for kind, path in tries:
        got = _read_source(kind, path, sym, day, loose=tape_only)
        if got is None:
            if kind == "file":
                chart["note"] = "%s has no bars for %s on %s." % (path, sym, day.isoformat())
            continue
        chart.update(got, path=path, shown=_shown(path, data))
        break
    if chart["kind"] is None:
        where = os.path.join(data, "bars", sym + ".csv")
        if site_on(cfg):
            chart["missing"] = ("no bars for %s on %s in %s: run entry.py watch %s to record the PSX company page, "
                                "or export 5-minute bars from your broker and save them as %s" % (
                                    sym, day_label(day), data, sym, where))
        else:
            chart["missing"] = "no bars for %s on %s in %s. %s" % (sym, day_label(day), data, NO_BARS % where)
        return chart
    if chart["kind"] == "bars-file":
        chart["label"] = "the bars file %s%s" % (chart["shown"], " (1-minute rows)" if chart["step"] == 1 else "")
        if live:
            chart["saved_at"] = ts_to_pkt(os.path.getmtime(chart["path"]))
    else:
        chart["label"] = ("the company tape (%s)" if chart["kind"] == "company-tape"
                          else "the screener tape (%s, prices only)") % chart["shown"]
        chart["lag"] = chart["delay"] if live else 0
    facts = chart["snaps"] if chart["kind"] == "company-tape" else read_tape(company)
    chart["prev_close"], chart["lower"], chart["upper"] = _page_facts(facts, sym)
    return chart


def cut_chart(chart, local, minutes, cfg=None):
    """The chart as it was at the clock `local` (naive Karachi) -> (bars, data_time).

    bars is aligned with slot_spans(day, minutes). data_time is how far the data goes:
    for a bars file, the end of its newest row that has ended by the clock (and, live, by
    the time the file was saved); for a tape, the newest snapshot's "asof" at or before
    the clock, or its fetch time minus the data delay and one more minute when that is
    later (a page fetched at 10:21 shows the market of about 10:16, so an older As of
    means no trade since 10:15). Never after the clock. None when there is no data yet."""
    day = chart["date"]
    if chart["kind"] == "bars-file":
        step = datetime.timedelta(minutes=chart["step"])
        cap = min(local, chart["saved_at"]) if chart.get("saved_at") else local
        rows = [r for r in chart["rows"] if r["start"] + step <= cap]
        data_time = max(r["start"] + step for r in rows) if rows else None
        return resample_bars(rows, day, minutes, cfg), data_time
    delay = datetime.timedelta(minutes=float(chart.get("delay") or 0) + 1)
    snaps, data_time = [], None
    for s in chart["snaps"] or []:
        when = pkt_field(s, "asof")
        if when is None or when > local:
            continue
        snaps.append(s)
        got = pkt_field(s, "fetched")
        seen = max(when, got - delay) if got is not None else when
        if data_time is None or seen > data_time:
            data_time = seen
    if data_time is not None:
        data_time = min(data_time, local)
    try:
        bars = bars_from_tape(snaps, day, chart["symbol"], minutes, cfg)
    except ValueError as e:
        raise EntryError("%s: %s" % (chart["shown"], e))
    return bars, data_time


# ---------------------------------------------------------------- the rule

def read_config(path):
    """psx/config.json (optional) on top of DEFAULTS. The window times, the data delay and
    the calendar keys ("holidays", "open_days", "ramadan") are checked here."""
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except ValueError as e:
            raise EntryError("%s is not valid JSON: %s" % (path, e))
        if not isinstance(doc, dict):
            raise EntryError("%s is not a JSON object" % path)
        for k, v in doc.items():
            if k in DEFAULTS and v in (None, ""):
                continue
            cfg[k] = v
    for k in ("no_entries_before", "no_entries_after"):
        try:
            parse_hhmm(cfg[k], k)
        except ValueError as e:
            raise EntryError(str(e))
    delay = cfg["data_delay_minutes"]
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 60:
        raise EntryError("data_delay_minutes in %s wants a number of minutes from 0 to 60" % path)
    try:
        trading_day(datetime.date(2026, 1, 5), cfg)
        ramadan_range(2026, cfg)
    except ValueError as e:
        raise EntryError(str(e))
    return cfg


def r2(x):
    return None if x is None else round(float(x), 2) + 0.0   # + 0.0 turns -0.0 into 0.0


def _phase(local, spans):
    """Where the clock is in the day: "open" in a session, "break" between two (the Friday
    Juma break) or "after-close". spans are the day's (start, end) segments."""
    for i, (a, b) in enumerate(spans):
        if a <= local < b:
            return "open"
        if i + 1 < len(spans) and b <= local < spans[i + 1][0]:
            return "break"
    return "after-close"


def evaluate(chart, now_utc, setup="orb", or_minutes=15, interval="5m", level=None,
             min_volume_x=None, cfg=None):
    """Pure function: the setup's status at now_utc from one day's data (load_chart).
    A bar counts only once it has closed: its end is at or before the clock and the data
    has moved past it (cut_chart's data_time)."""
    cfg = cfg or dict(DEFAULTS)
    if interval not in INTERVALS:
        raise EntryError("--interval is 5m or 1m")
    minutes = INTERVALS[interval]
    if setup not in ("orb", "level"):
        raise EntryError("--setup is orb or level")
    if setup == "level" and level is None:
        raise EntryError("--setup level needs --level PRICE (for example a 20-day high)")
    local = to_pkt(now_utc)
    day = local.date()
    try:
        or_starts = opening_range_slots(day, or_minutes, minutes, cfg)
    except ValueError:
        raise EntryError("--or-minutes must be a whole number of %s bars" % interval)
    kind = session_kind(day, cfg)
    res = {"symbol": chart["symbol"], "setup": setup, "side": "buy", "date": day.isoformat(), "interval": interval,
           "or_minutes": or_minutes, "now": local.strftime("%Y-%m-%d %H:%M:%S"), "status": "waiting", "phase": "",
           "reason": "", "session": kind, "opening_range": None, "or_slots": len(or_starts), "level": None,
           "trigger": None, "last": None, "forming": None, "min_volume_x": min_volume_x,
           "prev_close": chart.get("prev_close"), "upper_limit": chart.get("upper"),
           "source": chart.get("kind"), "source_file": chart.get("shown"), "source_label": chart.get("label"),
           "source_note": chart.get("note"), "lag_minutes": chart.get("lag") or 0, "data_time": None,
           "calendar_known": day.year in CALENDAR_YEARS, "calendar_note": calendar_note(day, cfg), "_bars": []}
    if kind is None:
        res.update(status="no-data", phase="closed", reason="PSX is closed today (%s)" % trading_day(day, cfg)[1],
                   next_open=next_open(day, cfg))
        return res
    segs = [(datetime.datetime.combine(day, a), datetime.datetime.combine(day, b)) for a, b in session(day, cfg)]
    spans = slot_spans(day, minutes, cfg)
    open_dt, close_dt = segs[0][0], segs[-1][1]
    before, cutoff = cfg_time(cfg, "no_entries_before", day), cfg_time(cfg, "no_entries_after", day)
    n_or = len(or_starts)
    or_end = spans[n_or - 1][1]
    res.update(opens=hm(open_dt), closes=hm(close_dt), segments=[[hm(a), hm(b)] for a, b in segs],
               no_entries_before=hm(before), no_entries_after=hm(cutoff))
    if local < open_dt:
        res.update(phase="pre-open", reason=market_state(local, cfg)["reason"] + (
            "; the %d-minute range is set at %s" % (or_minutes, short(or_end)) if setup == "orb" else ""))
        return res
    now_phase = _phase(local, segs)
    if chart.get("kind") is None:
        res.update(status="no-data", phase=now_phase, reason=chart.get("missing") or "no bars for today")
        return res
    if minutes == 1 and chart["kind"] != "bars-file":
        raise EntryError("1-minute bars need a 1-minute bars file (--bars FILE): a tape makes 5-minute bars only")
    if chart["kind"] == "bars-file" and chart["step"] > minutes:
        raise EntryError("%s has %d-minute bars, not %s: use --interval %dm" % (
            chart["shown"], chart["step"], interval, chart["step"]))
    if min_volume_x and not chart.get("has_volume"):
        raise EntryError("--min-volume-x needs volumes, and %s has none%s" % (chart["label"], (
            ": use bars from your broker, or the company tape that watch records")
            if chart["kind"] == "screener-tape" else ""))
    bars, data_time = cut_chart(chart, local, minutes, cfg)
    if data_time is None:
        res.update(status="no-data", phase=now_phase, reason="%s has no data for %s up to %s" % (
            chart["label"], day.isoformat(), local.strftime("%H:%M")))
        return res
    res["data_time"] = data_time.strftime("%Y-%m-%d %H:%M:%S")
    counted = [dict(b, slot=k, end=spans[k][1]) for k, b in enumerate(bars) if b and spans[k][1] <= data_time]
    res["_bars"] = counted
    res["bars_closed"] = len(counted)
    nxt = next((k for k, (_, e) in enumerate(spans) if e > data_time), None)
    if nxt is not None:
        start, end = spans[nxt]
        b = bars[nxt]
        info = {"bar_start": hm(start), "closes": clock_label(end), "price": r2(b["close"]) if b else None}
        if end <= local:
            res["forming"] = info
            res["lag_note"] = "the data is as of %s, before the %s bar closed at %s: it counts once the data reaches %s%s" % (
                clock_label(data_time), short(start), clock_label(end), short(end),
                " (free PSX data is about %s minutes late)" % ("%g" % chart["delay"]) if chart["kind"] != "bars-file"
                else " (the bars file has nothing newer)")
        elif b and start <= data_time:
            res["forming"] = info
    return _rule(res, chart, counted, local, data_time, setup, level, min_volume_x,
                 dict(n_or=n_or, or_end=or_end, open_dt=open_dt, close_dt=close_dt, before=before, cutoff=cutoff,
                      now_phase=now_phase, minutes=minutes, spans=spans, segs=segs))


def _first_snap(chart, start, local):
    """The first snapshot of the chart's tape (its symbol, with a price) from `start` to the
    clock `local`, or None."""
    sym = chart["symbol"]
    first, first_t = None, None
    for s in chart.get("snaps") or []:
        when = pkt_field(s, "asof")
        if when is None or not start <= when <= local:
            continue
        if chart["kind"] == "screener-tape":
            price = num((s.get("prices") or {}).get(sym))
        elif str(s.get("symbol") or sym).strip().upper() == sym:
            price = num(s.get("price"))
        else:
            continue
        if price is not None and price > 0 and (first_t is None or when < first_t):
            first, first_t = s, when
    return first


def _part_range(chart, or_bars, local, ctx):
    """An opening range with bars missing (a tape that started late, or a gap) -> (high, low,
    note) when a company tape started inside the range on a page with the day's high and
    low, and has every range bar from there on: the range is then that high and low and the
    prices seen until the range ends. Else (None, None, why the range is not known)."""
    n_or, open_dt, or_end = ctx["n_or"], ctx["open_dt"], ctx["or_end"]
    have = set(b["slot"] for b in or_bars)
    first_slot = min(have)
    gap = [k for k in range(first_slot, n_or) if k not in have]
    where = "(%s to %s)" % (short(open_dt), short(or_end))
    tail = "so the range is not known and there is no range to break"
    if chart["kind"] == "bars-file" and ctx["minutes"] == 1:
        tail += "; with --interval 5m a minute with no row may not matter"
    if gap:
        return None, None, "%s has no %s bar, inside the opening range %s, %s" % (
            chart["label"], short(ctx["spans"][gap[0]][0]), where, tail)
    if chart["kind"] == "bars-file":
        return None, None, "%s starts with the %s bar, inside the opening range %s, %s" % (
            chart["label"], short(ctx["spans"][first_slot][0]), where, tail)
    s = _first_snap(chart, open_dt, local)
    when = pkt_field(s, "asof") if s else ctx["spans"][first_slot][0]
    hi, lo = (num(s.get("high")), num(s.get("low"))) if s and chart["kind"] == "company-tape" else (None, None)
    started = "the tape started at %s, inside the opening range %s" % (short(when), where)
    if hi and lo and hi > 0 and lo > 0:
        return (max([hi] + [b["high"] for b in or_bars]), min([lo] + [b["low"] for b in or_bars]),
                "T" + started[1:] + ", so the high and low use its first page's day high and low")
    return None, None, "%s, and %s, %s; a tape must start before %s PKT to hold the range, or use bars from " \
        "your broker" % (started, "a screener tape has no day high and low" if chart["kind"] == "screener-tape"
                         else "its first page has no day high and low", tail, clock_label(open_dt))


def _rule(res, chart, counted, local, data_time, setup, level, min_volume_x, ctx):
    """evaluate's second half: the opening range, the trigger, expiry and the waiting words.
    ctx holds the day's numbers (n_or, or_end, close_dt, before, cutoff, now_phase, ...)."""
    n_or, or_end, cutoff, before = ctx["n_or"], ctx["or_end"], ctx["cutoff"], ctx["before"]
    or_bars = [b for b in counted if b["slot"] < n_or]
    vols = [b["volume"] for b in or_bars if b.get("volume") is not None]
    avg_vol = (sum(vols) / float(len(vols))) if vols else None
    complete = data_time >= or_end
    if or_bars:
        res["opening_range"] = {"high": r2(max(b["high"] for b in or_bars)), "low": r2(min(b["low"] for b in or_bars)),
                                "minutes": res["or_minutes"], "bars": len(or_bars), "of": n_or, "complete": complete,
                                "avg_volume": int(round(avg_vol)) if avg_vol is not None else None}
    if setup == "orb":
        if not complete:
            res.update(phase="forming", reason="%d of %d range bars closed; the range is set at %s" % (
                len(or_bars), n_or, short(or_end)))
            return res
        if not or_bars:
            seen = [t for t in (pkt_field(s, "asof") for s in chart.get("snaps") or []) if t is not None]
            res.update(status="no-data", phase=ctx["now_phase"], final=True, reason=(
                "%s has no bars in the opening range (%s to %s), so there is no range to break%s" % (
                    chart["label"], short(ctx["open_dt"]), short(or_end), (
                        "; the page shows no trade today (its newest As of time is %s)" % max(seen).strftime(
                            "%Y-%m-%d %H:%M")) if seen and max(seen).date() < local.date() else (
                        "; a tape must start before %s PKT to hold the range, or use bars from your broker" % (
                            clock_label(ctx["open_dt"])) if seen else ""))))
            return res
        lvl = max(b["high"] for b in or_bars)
        if len(or_bars) < n_or:
            hi, lo, why = _part_range(chart, or_bars, local, ctx)
            if hi is None:
                res.update(status="no-data", phase=ctx["now_phase"], final=True, reason=why)
                return res
            lvl = hi
            res["opening_range"].update(high=r2(hi), low=r2(lo))
            res["range_note"] = why
    else:
        lvl = float(level)
    res["level"] = r2(lvl)

    first_slot = n_or if setup == "orb" else 0
    ends = [b for _, b in ctx["segs"]]
    trig, thin, at_end = None, [], []
    for b in counted:
        if b["slot"] < first_slot or b["end"] < before or b["end"] > cutoff:
            continue
        if b["close"] > lvl and b["end"] in ends:     # it closes when a session ends: an order cannot fill
            at_end.append(b)
            continue
        if b["close"] > lvl:
            vx = (b["volume"] / avg_vol) if (avg_vol and b.get("volume") is not None) else None
            if min_volume_x and (vx is None or vx < min_volume_x):
                thin.append(b)
                continue
            trig = b
            break
    last = counted[-1] if counted else None
    if last:
        res["last"] = {"bar_start": hm(last["start"]), "close": r2(last["close"]),
                       "vs_level_pct": r2(100.0 * (last["close"] / lvl - 1))}

    if trig:
        vx = (trig["volume"] / avg_vol) if (avg_vol and trig.get("volume") is not None) else None
        res.update(status="triggered", phase=ctx["now_phase"], reason="the %s bar closed above %s" % (
            short(trig["start"]), "the range high" if setup == "orb" else "the level"))
        res["trigger"] = {"time": clock_label(trig["end"]), "close_time": hm(trig["end"]),
                          "bar_start": hm(trig["start"]), "bar_index": trig["slot"], "price": r2(trig["close"]),
                          "close": trig["close"], "volume": trig.get("volume"), "volume_x": r2(vx),
                          "pct_over": r2(100.0 * (trig["close"] / lvl - 1))}
        res["minutes_since"] = int((local - trig["end"]).total_seconds() // 60)
        res["past_cutoff"] = local.replace(second=0, microsecond=0) > cutoff   # psx-risk's window rule
        upper = chart.get("upper")
        if upper and trig["close"] >= upper - 0.005:
            res["limit_note"] = ("%.2f is today's upper price limit: PSX trades up to it, never above, so a buy "
                                 "there may not fill" % upper)
        return res
    if not counted:
        res.update(phase=ctx["now_phase"], reason="no bar has closed yet: the first closes at %s" % (
            clock_label(ctx["spans"][0][1])))
        return res
    what = "close above the range high %.2f" % lvl if setup == "orb" else "close above %.2f" % lvl
    if thin:
        what += " with enough volume (%.2fx)" % min_volume_x
    ended = local >= ctx["close_dt"] + datetime.timedelta(minutes=res.get("lag_minutes") or 0)
    if data_time >= cutoff or (local >= cutoff and ended):
        res.update(status="expired", phase="after-close" if local >= ctx["close_dt"] else "after-cutoff",
                   reason="no %s by %s, the last time for a new entry%s%s" % (
                       what, short(cutoff), "" if data_time >= cutoff else " (the data stops at %s)" % (
                           clock_label(data_time)), "; the %s bar closed above, but at %s, when the session ended: "
                       "an order cannot fill then" % (short(at_end[0]["start"]), clock_label(at_end[0]["end"]))
                       if at_end else ""))
        return res
    res["phase"] = ctx["now_phase"]
    if last["close"] > lvl and last in thin:
        res["reason"] = "the %s bar closed above at %.2f but %s" % (short(last["start"]), last["close"], (
            "on %.2fx the range's volume, under your %.2fx" % (last["volume"] / avg_vol, min_volume_x))
            if (avg_vol and last.get("volume") is not None) else "its volume is not known")
    elif last["close"] > lvl and last["end"] < before:
        res["reason"] = "the %s bar closed above at %.2f, but no entries before %s (config)" % (
            short(last["start"]), last["close"], short(before))
    elif last in at_end:
        later = [a for a, _ in ctx["segs"] if a >= last["end"]]
        res["reason"] = "the %s bar closed above at %.2f, but it closed at %s, when %s: an order cannot fill then%s" % (
            short(last["start"]), last["close"], clock_label(last["end"]),
            "the session ends" if later else "the market closes",
            "; the next chance is the %s session, at a new price" % clock_label(later[0]) if later else "")
    else:
        gap = lvl - last["close"]
        res["reason"] = "last close %.2f (%s bar), %.2f %s %s (%+.2f%%)" % (
            last["close"], short(last["start"]), abs(gap), "under" if gap >= 0 else "over",
            "the range high %.2f" % lvl if setup == "orb" else "the level %.2f" % lvl,
            100.0 * (last["close"] / lvl - 1))
    return res


def public(res):
    """The result without the internal bar list, ready for JSON."""
    return {k: v for k, v in res.items() if not k.startswith("_")}


def entry_doc(res, now_utc):
    """psx/entry.json, the contract shape (plus a plain-words reason)."""
    t = res.get("trigger") or {}
    orr = res.get("opening_range")
    return {"created_by": "psx-entry", "symbol": res["symbol"], "setup": res["setup"], "side": "buy",
            "date": res["date"], "status": res["status"], "time": t.get("time"), "bar_start": t.get("bar_start"),
            "bar_index": t.get("bar_index"), "price": t.get("price"), "level": res.get("level"),
            "interval": res["interval"], "session": res.get("session"),
            "opening_range": {"high": orr["high"], "low": orr["low"], "minutes": orr["minutes"], "bars": orr["bars"],
                              "of": orr["of"]} if orr else None,
            "source": res.get("source"), "source_file": res.get("source_file"), "checked_at": utc_iso(now_utc),
            "reason": res.get("reason")}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------- words

LABEL = {"triggered": "TRIGGERED", "waiting": "WAITING", "expired": "EXPIRED", "no-data": "NO DATA"}
PHASE_LABEL = {"pre-open": "NOT OPEN YET", "forming": "RANGE FORMING", "closed": "MARKET CLOSED"}
SHORT = {"pre-open": "not open", "forming": "forming", "closed": "closed"}
KIND_NAME = {"friday": "Friday", "ramadan": "Ramadan", "ramadan-friday": "Ramadan Friday"}


def setup_name(res):
    if res["setup"] == "orb":
        return "opening-range breakout (%d-minute range)" % res["or_minutes"]
    return "close above %.2f" % res["level"] if res["level"] is not None else "close above a level"


def _sentence(text):
    return text if text.endswith(".") else text + "."


def _cap(text):
    """A reason as a sentence of its own: a capital first letter and a full stop."""
    return _sentence(text[:1].upper() + text[1:])


def hours_line(res):
    """A line for a day with other hours (Friday, Ramadan), else None."""
    name = KIND_NAME.get(res.get("session"))
    if not name or not res.get("segments"):
        return None
    times = " and ".join("%s to %s" % (clock_label(datetime.datetime.strptime(a, "%H:%M")),
                                       clock_label(datetime.datetime.strptime(b, "%H:%M"))) for a, b in res["segments"])
    return "%s hours today: %s PKT. No new entries before %s or after %s." % (
        name, times, res["no_entries_before"].lstrip("0"), res["no_entries_after"].lstrip("0"))


def break_line(res):
    """When the clock is in the Friday Juma break: the market is closed, say so."""
    if res.get("phase") != "break":
        return None
    when = datetime.datetime.strptime(res["now"], "%Y-%m-%d %H:%M:%S")
    nxt = [datetime.datetime.strptime("%s %s" % (res["date"], a), "%Y-%m-%d %H:%M") for a, _ in res["segments"]]
    nxt = [t for t in nxt if t > when]
    if not nxt:
        return None
    return "The market is closed now for the Juma break. Trading starts again at %s PKT (pre-open from %s PKT)." % (
        clock_label(nxt[0]), clock_label(nxt[0] - datetime.timedelta(minutes=15)))


def describe(res):
    """Plain lines for a person."""
    when = datetime.datetime.strptime(res["now"], "%Y-%m-%d %H:%M:%S")
    bars = "5-minute" if res["interval"] == "5m" else "1-minute"
    head = "%s  %s %s PKT  %s on %s bars" % (res["symbol"], day_label(when), when.strftime("%H:%M"),
                                             setup_name(res), bars)
    if res.get("source_label"):
        head += " from %s" % res["source_label"]
    if res.get("data_time") and res["data_time"][:10] == res["date"]:
        head += ", data to %s" % res["data_time"][11:16]
    out = [head]
    if res.get("source_note") and res.get("phase") != "closed":
        out.append(res["source_note"])
    if hours_line(res):
        out.append(hours_line(res))
    orr = res.get("opening_range")
    if orr and res["status"] != "no-data":      # a part range that is not known is not shown
        start = datetime.datetime.strptime(res["opens"], "%H:%M")
        end = start + datetime.timedelta(minutes=orr["minutes"])
        out.append("Opening range %s-%s: high %.2f, low %.2f (%d of %d bars%s)." % (
            short(start), short(end), orr["high"], orr["low"], orr["bars"], orr["of"],
            "" if orr["complete"] else ", still forming"))
    if res.get("range_note"):
        out.append(_sentence(res["range_note"]))
    label = PHASE_LABEL.get(res["phase"]) or LABEL.get(res["status"], res["status"].upper())
    t = res.get("trigger")
    if t:
        out.append("TRIGGERED %s at %.2f: the %s bar (bar %d) closed above %s %.2f (%+.2f%%%s)." % (
            t["time"], t["price"], short(datetime.datetime.strptime(t["bar_start"], "%H:%M")), t["bar_index"],
            "the range high" if res["setup"] == "orb" else "the level", res["level"], t["pct_over"],
            (", %.2fx the range's volume" % t["volume_x"]) if res.get("min_volume_x") and t.get("volume_x") else ""))
        late = res.get("minutes_since", 0) > INTERVALS[res["interval"]] + (res.get("lag_minutes") or 0)
        if late and res.get("last"):
            out.append("That was %d minutes ago; the last close is %.2f (%s bar). A late entry is a different "
                       "trade: the price, the stop distance and the odds have moved." % (
                           res["minutes_since"], res["last"]["close"], res["last"]["bar_start"]))
        if res.get("limit_note"):
            out.append(_cap(res["limit_note"]))
        if res.get("past_cutoff") and res.get("phase") not in ("break", "after-close"):
            cut = res["no_entries_after"].lstrip("0")
            out.append("The bar closed by %s, but it is now %s, after %s, the last time for a new entry: psx-risk "
                       "will block a new entry. Nothing was placed." % (cut, when.strftime("%H:%M"), cut))
        elif late or res.get("phase") in ("break", "after-close"):
            out.append("Nothing was placed.")
        else:
            out.append("This is the moment to get in, if you take the trade. Nothing was placed.")
        out.append("Free PSX data is about 5 minutes late: check the price in your broker app before you buy.")
        out.append("Next: set the stop and target BEFORE you buy: /psx-exit (exit.py plan --from psx/entry.json).")
    else:
        out.append("%s  %s" % (label, _sentence(res["reason"])))
        f = res.get("forming")
        if res.get("lag_note") and res["status"] == "waiting":
            out.append(_cap(res["lag_note"]))
        elif f and f.get("price") is not None and res["status"] == "waiting" and res["phase"] in ("open", "forming"):
            out.append("The %s bar is still forming (%.2f so far, closes %s); it does not count until it closes." % (
                f["bar_start"].lstrip("0"), f["price"], f["closes"]))
        if res["status"] == "expired":
            out.append("No entry on this setup today. Nothing was placed.")
        if res.get("next_open"):
            out.append("Next open: %s Karachi time." % res["next_open"])
    if break_line(res):
        out.append(break_line(res))
    if res.get("calendar_note"):
        out.append(res["calendar_note"])
    return out


def bar_line(b, res):
    """One watch line for a bar that just closed."""
    start = short(b["start"])
    orr = res.get("opening_range") or {}
    lvl = res.get("level")
    t = res.get("trigger")
    n_or = res.get("or_slots") or 0
    if res["setup"] == "orb" and b["slot"] < n_or:
        seen = [x for x in res["_bars"] if x["slot"] <= b["slot"]]
        note = "range so far %.2f to %.2f" % (min(x["low"] for x in seen), max(x["high"] for x in seen))
        if b["slot"] == n_or - 1 and orr:
            note = "opening range set: high %.2f, low %.2f" % (orr["high"], orr["low"])
    elif t and t["bar_index"] == b["slot"]:
        note = "TRIGGERED: closed above %.2f (%+.2f%%). %s: %s, %.2f" % (
            lvl, t["pct_over"], "Too late for a new entry" if res.get("past_cutoff") else "Get in now if you take it",
            t["time"], t["price"])
    elif lvl is None:
        note = ""
    elif b["close"] > lvl:
        note = "above %.2f" % lvl
        if res["setup"] == "level" or b["slot"] >= n_or:
            avg = orr.get("avg_volume")
            day = b["start"].date()
            if hm(b["end"]) in [e for _, e in res.get("segments") or []]:
                note += ", but the session ends at %s: an order cannot fill then" % clock_label(b["end"])
            elif res.get("min_volume_x") and avg:
                note += (", but volume %.2fx is under %.2fx" % (b["volume"] / float(avg), res["min_volume_x"])
                         if b.get("volume") is not None else ", but its volume is not known")
            elif b["end"] < datetime.datetime.combine(day, parse_hhmm(res["no_entries_before"], "x")):
                note += ", but no entries before %s" % res["no_entries_before"]
            elif b["end"] > datetime.datetime.combine(day, parse_hhmm(res["no_entries_after"], "x")):
                note += ", but no new entries after %s" % res["no_entries_after"]
    else:
        note = "%.2f under %.2f" % (lvl - b["close"], lvl)
    return "%5s bar  closed %.2f  %s" % (start, b["close"], note)


# ---------------------------------------------------------------- watch

class SimClock(object):
    """A clock that moves only when it sleeps: replays a day without waiting."""

    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += datetime.timedelta(seconds=max(1, int(round(seconds))))


def watch_loop(provider, clock, sleep, emit, opts, every=60, until=None, max_errors=5, on_trigger=None,
               live=False):
    """Poll bar by bar until a trigger (0), expiry, the close or --until (1).

    provider(now_utc) -> a chart (load_chart), clock() -> naive UTC, sleep(seconds),
    emit(line). live: poll every `every` seconds while PSX trades (each poll records the
    company page), from the open, through the end of each session until the data has
    passed it, then sleep through the Juma break. A replay wakes just after each bar
    closes, and every `every` seconds while the data is behind the clock. It sleeps in
    steps of at most `every` seconds and reads the clock again each time, so a computer
    that dozed off catches up as soon as it wakes."""
    minutes = INTERVALS[opts.get("interval", "5m")]
    cfg = opts.get("cfg") or dict(DEFAULTS)
    grace = datetime.timedelta(seconds=GRACE)
    printed, errors, said, first = -1, 0, set(), True
    next_fetch = None
    while True:
        now = clock()
        if next_fetch is not None and now < next_fetch:
            sleep(max(1, min(every, (next_fetch - now).total_seconds())))
            continue
        local = to_pkt(now)
        day = local.date()
        segs = session(day, cfg)
        if not segs:
            emit("%s: PSX is closed today (%s). Nothing to watch." % (day_label(day), trading_day(day, cfg)[1]))
            return 1, None
        segs = [(datetime.datetime.combine(day, a), datetime.datetime.combine(day, b)) for a, b in segs]
        spans = slot_spans(day, minutes, cfg)
        open_dt, close_dt = segs[0][0], segs[-1][1]
        start_at = open_dt if live else spans[0][1] + grace
        if local < start_at:
            if "open" not in said:
                emit("%s PKT  %s: %s from %s PKT." % (
                    local.strftime("%H:%M"), "not open yet" if local < open_dt else "open, no bar closed yet",
                    "polling the PSX company page every %g seconds" % every if live else "watching",
                    clock_label(start_at)))
                said.add("open")
            next_fetch = pkt_to_utc(start_at)
            continue
        try:
            chart = provider(now)
            errors = 0
        except EntryError as e:
            errors += 1
            emit("%s PKT  could not read %s: %s" % (local.strftime("%H:%M:%S"),
                                                     "the company page" if live else "the bars", e))
            if errors >= max_errors:
                emit("Stopping after %d failed reads in a row." % errors)
                return 1, None
            next_fetch = now + datetime.timedelta(seconds=every)
            continue
        res = evaluate(chart, now, **opts)
        bars = res["_bars"]
        lines = describe(res)
        heads = [x for x in lines[1:] if x.startswith("Opening range") or " hours today: " in x]
        fresh = first
        if first:
            emit(lines[0])
            for line in heads:
                emit(line)
            if bars and not res.get("trigger"):
                emit(bar_line(bars[-1], res))
            printed = bars[-1]["slot"] if bars else -1
            first = False
        else:
            for b in bars:
                if b["slot"] > printed:
                    emit(bar_line(b, res))
                    printed = b["slot"]
        if res["status"] == "triggered":
            for line in lines[1:]:
                if line in heads or (line.startswith("TRIGGERED") and not fresh):
                    continue
                emit(line)
            if on_trigger:
                on_trigger(res, now)
            return 0, res
        lag = datetime.timedelta(minutes=res.get("lag_minutes") or 0)
        if res["status"] == "no-data" and (res.get("final") or local >= close_dt or (
                not live and chart.get("kind") is None)):
            emit("NO DATA  %s" % _sentence(res["reason"]))
            return 1, res
        if res["status"] == "expired" or local >= close_dt + lag + datetime.timedelta(minutes=10):
            emit("EXPIRED  %s" % _sentence(res["reason"] or "the market closed without a trigger"))
            return 1, res
        if until and local >= until:
            emit("Stopped at --until %s without a trigger. %s" % (short(until), _cap(res["reason"])))
            return 1, res
        data_time = datetime.datetime.strptime(res["data_time"], "%Y-%m-%d %H:%M:%S") if res["data_time"] else None
        ended = [e for _, e in segs if e <= local]
        later = [s for s, _ in segs if s > local]
        wait = every
        if ended and later and (not live or (data_time is not None and data_time >= ended[-1])):
            if "break" not in said:
                emit("%s PKT  Juma break: the market is closed until %s PKT. Watching again from then." % (
                    local.strftime("%H:%M"), clock_label(later[0])))
                said.add("break")
            wake = later[0] if live else later[0] + datetime.timedelta(minutes=minutes) + grace
            wait = (wake - local).total_seconds()
        elif not live and not res.get("lag_note"):
            ends = [e for _, e in spans if e > local]
            if ends:
                wait = (ends[0] + grace - local).total_seconds()
        cutoff = datetime.datetime.combine(day, parse_hhmm(res.get("no_entries_after") or "15:00", "cutoff"))
        for stop_at in (until, None if live else cutoff, close_dt):
            if stop_at and stop_at > local:
                wait = min(wait, (stop_at - local).total_seconds())
        next_fetch = now + datetime.timedelta(seconds=max(1, wait))


# ---------------------------------------------------------------- commands

def _opts(a, cfg):
    if a.side in ("sell", "short"):
        raise EntryError("longs only: no short entries (PSX bans blank sales). Watch for a buy (--side buy) instead.")
    if a.setup == "orb" and a.level is not None:
        raise EntryError("--level is for --setup level (an orb's level is its range high)")
    if a.min_volume_x is not None and a.min_volume_x <= 0:
        raise EntryError("--min-volume-x must be above 0")
    if a.level is not None and a.level <= 0:
        raise EntryError("--level must be a price above 0")
    return {"setup": a.setup, "or_minutes": a.or_minutes, "interval": a.interval, "level": a.level,
            "min_volume_x": a.min_volume_x, "cfg": cfg}


def cmd_check(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    sym = a.symbol.upper()
    now = now_arg(a.now) if a.now else utc_now()
    chart = load_chart(sym, to_pkt(now).date(), a.data, a.bars, cfg, live=a.now is None)
    res = evaluate(chart, now, **opts)
    if a.write:
        write_json(a.write, entry_doc(res, now))
    if a.json:
        print(json.dumps(public(res), indent=2))
    else:
        for line in describe(res):
            print(line)
        if a.write:
            print("Wrote %s (status %s)." % (a.write, res["status"]))
    return 0 if res["status"] == "triggered" else 1


def cmd_watch(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    sym = a.symbol.upper()
    if a.every <= 0:
        raise EntryError("--every must be above 0 seconds")
    live = a.now is None and not a.offline
    if live:
        if a.bars:
            raise EntryError("--bars is for check and for replays (--now or --offline): a watch that polls "
                             "the PSX site reads the tape it records")
        if not site_on(cfg):
            raise EntryError("watch needs the PSX site (it reads the company page every minute); check works on "
                             "a bars file. " + NO_BARS % os.path.join(a.data, "bars", sym + ".csv"))
        if a.every < MIN_EVERY:
            raise EntryError("--every must be %d seconds or more when watch polls the PSX site: the site is "
                             "shared, and its data changes about once a minute" % MIN_EVERY)
        now_fn, sleep_fn = utc_now, _sleep
    else:
        clock = SimClock(now_arg(a.now) if a.now else utc_now())
        now_fn, sleep_fn = clock.now, clock.sleep

    def provider(now):
        day = to_pkt(now).date()
        if live:
            append_tape(tape_path(a.data, day, sym), poll_company(sym, cfg, now))
            return load_chart(sym, day, a.data, None, cfg, live=True, tape_only=True)
        return load_chart(sym, day, a.data, a.bars, cfg)

    until = None
    if a.until:
        try:
            t = parse_hhmm(a.until, "--until")
        except ValueError as e:
            raise EntryError(str(e))
        until = datetime.datetime.combine(to_pkt(now_fn()).date(), t)

    def on_trigger(res, now):
        write_json(a.out, entry_doc(res, now))
        print("Wrote %s." % a.out, flush=True)

    def emit(line):
        print(line, flush=True)

    code, _ = watch_loop(provider, now_fn, sleep_fn, emit, opts, every=a.every, until=until,
                         on_trigger=on_trigger, live=live)
    return code


def _hits(path):
    """The scanner's hits in a watchlist file: {"hits": [...]} or a bare list, of dicts with
    a "symbol" (or of symbols). The same symbol twice counts once."""
    if not os.path.exists(path):
        raise EntryError("no watchlist at %s: run /psx-scanner first (it writes the hits there)" % path)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise EntryError("%s is not valid JSON: %s" % (path, e))
    raw = doc if isinstance(doc, list) else ((doc.get("hits") if isinstance(doc, dict) else None) or [])
    hits, seen = [], set()
    for h in raw if isinstance(raw, list) else []:
        h = {"symbol": h} if isinstance(h, str) else h
        sym = str(h.get("symbol") or "").strip().upper() if isinstance(h, dict) else ""
        if sym and sym not in seen:
            seen.add(sym)
            hits.append(dict(h, symbol=sym))
    if not hits:
        raise EntryError("no hits in %s: the scanner found nothing to watch" % path)
    return hits


def cmd_watchlist(a):
    cfg = read_config(a.config)
    opts = _opts(a, cfg)
    hits = _hits(a.watchlist)
    now = now_arg(a.now) if a.now else utc_now()
    rows = []
    for h in hits:
        sym = h["symbol"]
        o = dict(opts)
        if a.setup == "level" and o["level"] is None:
            o["level"] = num(h.get("level"))
        try:
            if a.setup == "level" and o["level"] is None:
                raise EntryError("needs a level: --level PRICE or a \"level\" in the watchlist")
            chart = load_chart(sym, to_pkt(now).date(), a.data, None, cfg, live=a.now is None)
            rows.append((sym, evaluate(chart, now, **o), None))
        except EntryError as e:
            rows.append((sym, None, str(e)))
    if a.json:
        print(json.dumps([{"symbol": s, "result": public(r) if r else None, "error": e} for s, r, e in rows], indent=2))
        return 0 if any(r and r["status"] == "triggered" for _, r, _ in rows) else 1
    form = "%-8s  %-9s  %-8s  %-8s  %-8s  %-8s  %-8s  %s"
    print(form % ("SYMBOL", "STATUS", "TIME", "PRICE", "LEVEL", "LAST", "VS LEVEL", "BARS FROM"))
    words = {"bars-file": "bars file", "company-tape": "company tape", "screener-tape": "screener tape"}
    for s, r, e in rows:
        if e:
            print("%-8s  error: %s" % (s, e))
            continue
        t = r.get("trigger") or {}
        last = r.get("last") or {}
        status = SHORT.get(r["phase"], r["status"]) if r["status"] != "triggered" else "triggered"
        print(form % (s, status[:9], t.get("time") or "-", ("%.2f" % t["price"]) if t else "-",
                      ("%.2f" % r["level"]) if r.get("level") is not None else "-",
                      ("%.2f" % last["close"]) if last else "-",
                      ("%+.2f%%" % last["vs_level_pct"]) if last.get("vs_level_pct") is not None else "-",
                      words.get(r.get("source"), "-")))
    n = sum(1 for _, r, _ in rows if r and r["status"] == "triggered")
    none = [s for s, r, _ in rows if r and r["status"] == "no-data" and not r.get("source") and r["phase"] != "closed"]
    if none:
        where = os.path.join(a.data, "bars", "<SYMBOL>.csv")
        if site_on(cfg):
            print("No bars for %s: run entry.py watch <SYMBOL> to record the PSX company page, or export 5-minute "
                  "bars from your broker and save them as %s." % (", ".join(none), where))
        else:
            print("No bars for %s. %s" % (", ".join(none), NO_BARS % where))
    print("%d of %d triggered. Pick one, then: entry.py check <SYMBOL> --write, and /psx-exit." % (n, len(rows)))
    print("Free PSX data is about 5 minutes late. Nothing was placed.")
    return 0 if n else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Entry Timer (psx-entry): watches a PSX setup bar by bar.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--setup", choices=["orb", "level"], default="orb")
        p.add_argument("--or-minutes", type=int, default=15, help="opening range length (default 15)")
        p.add_argument("--interval", choices=["5m", "1m"], default="5m", help="1m needs a 1-minute bars file")
        p.add_argument("--level", type=float, help="the price to close above (--setup level)")
        p.add_argument("--min-volume-x", type=float, help="trigger bar volume vs the range bars' average")
        p.add_argument("--side", default="buy", choices=["buy", "long", "sell", "short"], help="buy only")
        p.add_argument("--now", help='pretend it is this Karachi time, e.g. "2026-10-29 10:15" (a data time)')
        p.add_argument("--data", default=os.path.join("psx", "data"), help="the data folder (default psx/data)")
        p.add_argument("--offline", action="store_true", help="never touch the network: read the data folder only")
        p.add_argument("--config", default=os.path.join("psx", "config.json"))

    p = sub.add_parser("check", help="the setup's status right now")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--bars", metavar="FILE", help="a bars CSV from your broker (or a tape .jsonl): read first")
    p.add_argument("--write", nargs="?", const=os.path.join("psx", "entry.json"), metavar="PATH",
                   help="save the result (default psx/entry.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("watch", help="poll bar by bar until it triggers or expires")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--bars", metavar="FILE", help="replay this bars CSV or tape (with --now or --offline)")
    p.add_argument("--every", type=float, default=60, help="seconds between polls (default 60)")
    p.add_argument("--until", help="stop at this Karachi time (HH:MM)")
    p.add_argument("--out", default=os.path.join("psx", "entry.json"), help="where a trigger is written")
    p = sub.add_parser("watchlist", help="check every hit in psx/watchlist.json")
    common(p)
    p.add_argument("--watchlist", default=os.path.join("psx", "watchlist.json"))
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"check": cmd_check, "watch": cmd_watch, "watchlist": cmd_watchlist}[a.cmd](a)
    except EntryError as e:
        print("entry.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("entry.py: stopped.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
