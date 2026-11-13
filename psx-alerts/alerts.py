#!/usr/bin/env python3
"""Price, limit, time and bell alerts on the PSX clock, for psx-alerts. Standard library only.

    python3 alerts.py clock                          # is PSX open? the next open, today's hours
    python3 alerts.py quote DEMO --write             # quotes -> psx/prices.json
    python3 alerts.py quote --holdings --write       # every symbol in your portfolio and plan
    python3 alerts.py add --from-plan                # stop, target and flat by close from psx/plan.json
    python3 alerts.py add --symbol DEMO --stop 99.55
    python3 alerts.py add --symbol DEMO --target 101.75
    python3 alerts.py add --at 15:20 --label "flat by close" --before-close
    python3 alerts.py add --symbol DEMO --pct 3
    python3 alerts.py add --symbol DEMO --limit      # at the upper or lower price limit
    python3 alerts.py add --index KSE30 --pct 4.5    # early warning before a market halt
    python3 alerts.py add --opening-bell             # add --session 2 for the Friday 14:30 restart
    python3 alerts.py check                          # rings what hit, once; exit 0 rang, 1 nothing rang,
                                                     # 2 a rule or a file could not be read
    python3 alerts.py check --now "2026-10-29 10:53" --offline --data tests/fixtures/psx-market
    python3 alerts.py list                           # with the swing rules "(from psx/portfolio.json)"
    python3 alerts.py reset demo-plan-stop

Rule types:
  above / target  the price is at or above "price"
  below / stop    the price is at or below "price"
  pct_move        the price moved "pct" percent or more from the last close (LDCP),
                  or from "ref" if you give one; "direction": "up" or "down"
  limit           the price is at the upper or lower daily price limit;
                  "direction": "up", "down" or "any" (the default)
  index_move      an index ("index", default KSE30) moved "pct" percent or more
                  from its last close; "direction": "up" or "down"
  opening_bell    PSX opened today (once per trading day); "session": 2 rings
                  at the Friday restart after the Juma break instead
  time            the Karachi clock passed "at" (HH:MM) on a trading day (once
                  per trading day); "before_close": true moves a rule without a
                  "date" with each day's close (config_time), so 15:20 rings at
                  16:20 on a Friday and 13:20 on a Ramadan Monday
  trend           (swing) an official close under the lowest close of the
                  "sessions" closes before it, from the closing files

A rule with "date" is only watched on that Karachi date, and its "at" is already
that day's time (rules from a plan are for the plan's day only). An above / below
alert rings ONCE and then stays quiet until you reset it, unless the rule has
"rearm": true (it re-arms when the price crosses back) or "rearm": "daily" (at
most once a day). pct_move, limit and index_move ring at most once per day. One
bad rule never stops the others: the check skips it with an "error: rule <id>:
..." line and checks the rest.

Swing positions (SPEC v2 2.4): psx/portfolio.json is the source of truth. For each
held position with a plan block "style": "swing", check builds its rules at each
run (never written to psx/alerts.json): the stop and target (from quotes, and from
each closing file), the trend rule, the time stop on the exit-by day and, in live
mode, the stop-limit reminder (never while the stop rule is met: then the plan
says sell); plus a KSE30 5% halt rule while one is held. Every
closing file after the newest one checked is read once, oldest first (a Mac that
slept loses no close). A met rule rings once a day until the share is sold, and a
check that rings nothing new says "No new alert." and names it (never "Nothing hit").
In the session, a held swing share with no price newer than 15 minutes is an error
(exit 2). psx/state/broker.json is read too (read only): today's stop-limit, and each
stop raise (a day's low is judged on the stop in force when that day began). In a
swing folder, a share held with no swing plan gets a note: the swing checks skip it.

Quotes (about 5 minutes late; for alerts and size checks, never for timing an order):
  psx-company   the company page of each symbol: price, As-of time, LDCP and the
                day's price limits (the default)
  psx-screener  one screener page for all symbols: price only, so the data time
                is the fetch time minus "data_delay_minutes" and the last close
                comes from the daily closing files
Pages come through page_html() (one rule for the whole pack): with "psx_site": true
in psx/config.json, the PSX site first, and the saved page (company/<SYM>.html,
screener.html, indices.html in psx/data, or --data DIR) only when the site fails.
With the site off, or with --offline or --now, only the saved pages. Today's tape
(tape/YYYY-MM-DD/<SYM>.jsonl or market.jsonl) is read too: the newest price as of
now wins. A page read from the site is added to the tape.

PSX calendar: the 2026 and 2027 holidays and the Friday and Ramadan hours are in
the copied helpers below. Outside those years the clock says the list is unknown.
"""

import argparse
import datetime
import gzip
import html
import http.client
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


class AlertError(Exception):
    pass


# ---------------------------------------------------------------- copied from tests/psx_core.py
# These constants and helpers are verbatim copies (SPEC section 8). Never edit a copy:
# tests/test_repo.py checks each one against tests/psx_core.py. New behaviour goes in a new
# function with a new name, further down.

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
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name
MATCH_MINUTES = 2   # the opening match after each pre-open: no new orders until it ends
SWING = {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20, "trend_exit_sessions": 10,
         "entry_buffer_pct": 1.0, "max_open_positions": 3, "max_open_risk_pkr": None,
         "max_gap_loss_pkr": None, "stop_limit_gap_pct": 1.0}


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


def screener_html(root, cfg=None, offline=False):
    """The screener page through page_html(): the PSX site first when it is on and not
    offline, else <root>/screener.html. -> (html text, "site" or "saved"), or (None, a reason)."""
    return page_html(os.path.join(root, "screener.html"), PSX + "/screener", cfg, offline)


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


# ---------------------------------------------------------------- swing style and daily levels (copied verbatim from tests/psx_core.py; never edit a copy)

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


def trend_break(closes, n=10):
    """(level, broke) for the newest close: level = the lowest of the n closes before it,
    broke = the newest close is under that level. (None, False) with fewer than n + 1
    closes. Give payout-adjusted closes (adjusted_closes)."""
    c = [float(x) for x in closes or []]
    if len(c) < int(n) + 1:
        return None, False
    level = min(c[-int(n) - 1:-1])
    return level, c[-1] < level


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


def money(x, decimals=2):
    """Rupees for people: "Rs 1,234.56" or "-Rs 1,234.56"; decimals=0 gives "Rs 5,000"."""
    try:
        v = Decimal(str(x)).quantize(Decimal(1).scaleb(-int(decimals)), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("not an amount of money: %r" % (x,))
    if not v.is_finite():
        raise ValueError("not an amount of money: %r" % (x,))
    return "%sRs %s" % ("-" if v < 0 else "", format(abs(v), ",f"))


# ---------------------------------------------------------------- alerts: settings

HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
FLAT_BY_DEFAULT = "15:20"   # the config default (SPEC section 6), written for a normal Monday to Thursday
FRESH_MINUTES = 15          # a price is fresh when its data time is at most this many minutes old
DELAY_DEFAULT = 5           # "data_delay_minutes": the free PSX pages are about 5 minutes late
HALT_INDEX = "KSE30"        # Rulebook 19.4: KSE-30 5% from its last close for 5 minutes halts PSX
SOURCES = ("psx-company", "psx-screener")
NOTES = {"psx-company": "PSX company page, about 5 minutes late",
         "psx-screener": "PSX screener, about 5 minutes late"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
STATE_WORD = {"open": "OPEN", "pre-open": "PRE-OPEN", "break": "BREAK"}
AFTER_RING = ("An alert is information, not an order. PSX prices here are about 5 minutes late, and PSX holds "
              "no stop order for you. To close a position: psx-broker flatten builds the sell ticket, then "
              "psx-risk and your yes. Record any fill with psx-broker.")
MANUAL_HINT = ('No price found. You can read the price in your broker app and write %s by hand, like '
               '{"as_of": "2026-10-29T10:52:00+05:00", "source": "manual", "prices": {"DEMO": 101.30}} '
               '(as_of is the time the app showed, Karachi time).')


# ---------------------------------------------------------------- clock

def _now(text):
    """--now -> naive UTC (parse_now), with this tool's own error."""
    try:
        return parse_now(text)
    except ValueError as e:
        raise AlertError(str(e))


def _hhmm(text):
    m = HHMM.match(str(text or "").strip())
    if not m:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _pkt_of(stamp):
    """A "...Z" time from a JSON file -> naive Karachi time, or None."""
    t = parse_utc(stamp)
    return to_pkt(t) if t else None


def _asof_pkt(text):
    """A tape "asof" -> naive Karachi time. No offset means Karachi time; "Z" or an offset is
    turned into Karachi time. None when it does not read."""
    t = str(text or "").strip()
    try:
        when = datetime.datetime.fromisoformat(t[:-1] + "+00:00" if t[-1:] in ("Z", "z") else t)
    except ValueError:
        return None
    return to_pkt(when) if when.tzinfo is not None else when


def _when(local, today=None):
    """naive Karachi time -> "10:52 AM PKT", with the day in front when it is not `today`."""
    if local is None:
        return "an unknown time"
    if today is not None and local.date() != today:
        return "%s %s PKT" % (day_label(local), clock_label(local))
    return "%s PKT" % clock_label(local)


def _session_label(kind, seg):
    if kind == "friday":
        return "Friday afternoon session" if seg else "Friday morning session"
    return {"ramadan": "Ramadan session", "ramadan-friday": "Ramadan Friday session"}.get(kind, "regular session")


def _next_open(local, cfg=None):
    """The next segment start after naive Karachi time `local` (later today or on a later trading day)."""
    for i in range(16):
        day = local.date() + datetime.timedelta(days=i)
        for a, _ in session(day, cfg) or ():
            start = datetime.datetime.combine(day, a)
            if start > local:
                return start
    return None


def market_status(now_utc, cfg=None):
    """The PSX clock at a UTC time -> a dict: now_karachi, date, weekday, calendar_known,
    calendar_note, state (closed, pre-open, open, break or post-close, from market_state), open
    (true only in "open"), session (session_kind), segment (0 or 1 while open), segments
    ([["09:30", "15:30"]]), opens, closes, reason (short words), detail (market_state's words)
    and next_open ("YYYY-MM-DD HH:MM", Karachi)."""
    local = to_pkt(now_utc)
    d = local.date()
    try:
        ms = market_state(local, cfg)
        segs = session(d, cfg) or ()
        note = calendar_note(d, cfg)
        why = trading_day(d, cfg)[1]
        nxt = _next_open(local, cfg)
    except ValueError as e:
        raise AlertError(str(e))
    spans = [(datetime.datetime.combine(d, a), datetime.datetime.combine(d, b)) for a, b in segs]
    state = ms["state"]
    seg = next((i for i, (a, b) in enumerate(spans) if a <= local < b), None) if state == "open" else None
    if not spans:
        reason = why
    elif state == "open":
        reason = _session_label(ms["kind"], seg)
    elif state == "pre-open":
        reason = "limit orders only, trading from %s PKT" % clock_label(ms["open_at"])
    elif state == "break":
        reason = "Juma break, trading again at %s PKT" % clock_label(ms["open_at"])
    elif state == "post-close":
        reason = "post-close session, closing price only"
    elif local < spans[0][0]:
        reason = "before the open, pre-open from %s PKT" % clock_label(spans[0][0] - datetime.timedelta(minutes=15))
    else:
        reason = "after the close"
    return {"now_karachi": local.strftime("%Y-%m-%d %H:%M"), "date": d.isoformat(),
            "weekday": WEEKDAYS[d.weekday()], "calendar_known": d.year in CALENDAR_YEARS,
            "calendar_note": note, "state": state, "open": state == "open", "session": ms["kind"],
            "segment": seg, "segments": [[a.strftime("%H:%M"), b.strftime("%H:%M")] for a, b in segs],
            "opens": segs[0][0].strftime("%H:%M") if segs else None,
            "closes": segs[-1][1].strftime("%H:%M") if segs else None,
            "reason": reason, "detail": ms["reason"],
            "next_open": nxt.strftime("%Y-%m-%d %H:%M") if nxt else None}


def status_line(st):
    """The PSX clock in one line: "PSX OPEN (2026-10-29 10:53 PKT, regular session)."."""
    return "PSX %s (%s PKT, %s)." % (STATE_WORD.get(st["state"], "closed"), st["now_karachi"], st["reason"])


def hours_line(st):
    """Today's hours in one line, or None on a closed day."""
    if not st["segments"]:
        return None
    parts = ", then ".join("%s to %s" % (a, b) for a, b in st["segments"])
    extra = {"friday": " (the Juma break is in between)", "ramadan": " (Ramadan hours)",
             "ramadan-friday": " (Ramadan hours, one session)"}.get(st["session"], "")
    return ("Hours on %s %s: %s%s. The first trades come 2 minutes after each start, after the opening match."
            % (st["weekday"], st["date"], parts, extra))


# ---------------------------------------------------------------- quotes

def _sym(text):
    """A PSX symbol as company_html() writes it: upper case letters, digits and "-"."""
    return re.sub(r"[^A-Z0-9-]", "", str(text or "").upper())


def _pos(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0


def _delay(cfg):
    v = (cfg or {}).get("data_delay_minutes", DELAY_DEFAULT)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise AlertError('"data_delay_minutes" in psx/config.json wants a number of minutes, like 5')
    return v


def _fresh(q, now_utc):
    """True when a quote's data time is at most FRESH_MINUTES old."""
    t = parse_utc((q or {}).get("time"))
    return t is not None and (now_utc - t).total_seconds() <= FRESH_MINUTES * 60


def _page_clock(page):
    """The top-bar clock of a saved PSX page (when PSX made the page) -> naive Karachi time, or None."""
    m = re.search(r'<div class="topbar__status__label">([^<]*)</div>', page or "")
    return page_time(html_text(m.group(1))) if m else None


def _session_end_before(t, cfg=None):
    """The data time of a screener price seen at naive Karachi time t: t itself inside a
    session, else the end of the last segment before t (outside the session the page shows
    the closes, so a page saved at night is not taken for fresh data)."""
    for i in range(16):
        day = t.date() - datetime.timedelta(days=i)
        for a, b in reversed(session(day, cfg) or ()):
            start, end = datetime.datetime.combine(day, a), datetime.datetime.combine(day, b)
            if t >= end:
                return end
            if t >= start:
                return t
    return t


def _record(root, now_utc, name, snap, errors):
    """Add a page read from the PSX site to today's tape, so the other tools can use it."""
    try:
        append_tape(tape_path(root, to_pkt(now_utc).date(), name), snap)
    except OSError as e:
        errors.append("could not add the page to the tape in %s: %s" % (root, e))


def _company_quote(sym, f, fetched_utc, how):
    """Company page fields (price, asof as naive Karachi time, ldcp, lower, upper, open, high,
    low, flags) -> a quote. Without the page's limits they are computed from LDCP."""
    lower, upper, limits = f.get("lower"), f.get("upper"), None
    if _pos(lower) and _pos(upper):
        limits = "company page"
    elif _pos(f.get("ldcp")):
        lower, upper = price_limits(f["ldcp"])
        limits = "computed"
    else:
        lower = upper = None
    q = {"symbol": sym, "price": float(f["price"]), "prev_close": f["ldcp"] if _pos(f.get("ldcp")) else None,
         "time": utc_iso(pkt_to_utc(f["asof"])), "fetched": utc_iso(fetched_utc), "source": "psx-company",
         "from": how, "lower": lower, "upper": upper, "limits": limits}
    for k in ("open", "high", "low"):
        q[k] = f[k] if _pos(f.get(k)) else None
    if f.get("flags"):
        q["flags"] = list(f["flags"])
    return q


def _tape_company(root, day, sym, now_pkt):
    """The newest company page snapshot of sym on the tape of `day`, as of now_pkt or earlier."""
    best = None
    for s in read_tape(tape_path(root, day, sym)):
        if str(s.get("source") or "company") != "company" or _sym(s.get("symbol") or sym) != sym:
            continue
        when, price = _asof_pkt(s.get("asof")), num(s.get("price"))
        if when is None or when > now_pkt or not _pos(price):
            continue
        if best is None or when >= best[0]:
            best = (when, s)
    if best is None:
        return None
    when, s = best
    f = dict((k, num(s.get(k))) for k in ("price", "ldcp", "lower", "upper", "open", "high", "low"))
    f["asof"] = when
    return _company_quote(sym, f, parse_utc(s.get("fetched")) or pkt_to_utc(when), "tape")


def _page_quote(sym, page, fetched_utc, how):
    """(quote, None) from a company page's HTML, or (None, a plain reason)."""
    p = parse_company(page)
    if p.get("symbol") and _sym(p["symbol"]) != sym:
        return None, "the page is for %s, not %s" % (p["symbol"], sym)
    if not _pos(p.get("price")) or not p.get("as_of"):
        return None, "the page has no price with an As-of time (check the symbol)"
    reg = p.get("reg") or {}
    f = dict((k, reg.get(k)) for k in ("ldcp", "lower", "upper", "open", "high", "low"))
    f.update(price=p["price"], asof=p["as_of"], flags=p.get("flags"))
    return _company_quote(sym, f, fetched_utc, how), None


def company_quote(sym, root, cfg, now_utc, offline):
    """(quote or None, [errors]) for one symbol from its company page. The page comes through
    company_html(): the PSX site first when it is on and not offline (that page also goes on
    the tape), else the saved page, which is also the fallback when the site fails. Today's
    tape is read too. The newest price as of now wins (the site wins a tie)."""
    now_pkt = to_pkt(now_utc)
    path = os.path.join(root, "company", sym + ".html")
    url = "%s/company/%s" % (PSX, sym)
    asked = site_on(cfg) and not offline
    found, later, errors = [], None, []
    q = _tape_company(root, now_pkt.date(), sym, now_pkt)
    if q:
        found.append(q)
    page, how = company_html(sym, root, cfg, offline)
    if page is None:
        if asked:
            errors.append("%s: %s" % (sym, how))            # the site failed and no page is saved
    elif how == "site":
        q, why = _page_quote(sym, page, now_utc, "site")
        if q is None:
            errors.append("%s: %s at %s" % (sym, why, url))
        else:
            _record(root, now_utc, sym, snapshot_from_company(parse_company(page), now_utc), errors)
            found.insert(0, q)
    else:
        if asked:                                           # the site failed, so page_html read the saved page
            errors.append("%s: could not read the PSX site for %s, so only the data folder was read" % (sym, url))
        made = _page_clock(page)
        q, why = _page_quote(sym, page, pkt_to_utc(made) if made else now_utc, "saved page")
        if q is None:
            errors.append("%s: %s (%s)" % (sym, why, path))
        elif _pkt_of(q["time"]) <= now_pkt:
            found.append(q)
        else:
            later = q
    if found:
        return max(found, key=lambda x: x["time"]), errors
    if later:
        errors.append("%s: the saved page is as of %s, later than the time asked for" % (
            sym, _when(_pkt_of(later["time"]), now_pkt.date())))
    if errors:
        return None, errors
    if not site_on(cfg):
        return None, ["%s: %s" % (sym, SITE_OFF % ("%s as %s" % (url, path)))]
    return None, ["%s: no price in %s as of %s PKT (%s reads only the data folder)" % (
        sym, root, now_pkt.strftime("%Y-%m-%d %H:%M"), _off_words(offline))]


def _off_words(offline):
    """Why the site was not used: "--offline" or "--now" (both read only the data folder)."""
    return offline if isinstance(offline, str) else "--offline"


def _last_close(sym, day, root, cfg, offline, cache, errors):
    """(last close, "ldcp" or "close") of sym before date `day`, from the daily closing files:
    the LDCP in the file of `day` when that file is already in the folder (PSX ex-adjusts it),
    else the CLOSE of the session before `day`. (None, None) when neither file has it. A closing
    file the PSX site does not give (a timeout, HTTP 500, a cut-off answer) is one error line, asked
    once per check: the price is still used, with no last close and no price limits."""
    c = cache.setdefault(day, {})
    try:
        if "today" not in c:
            rows = daily_rows(day, root, cfg, True) if os.path.exists(daily_path(root, day)) else None
            c["today"] = dict((r["symbol"], r) for r in rows or [])
        if _pos((c["today"].get(sym) or {}).get("ldcp")):
            return c["today"][sym]["ldcp"], "ldcp"
        if "before" not in c:
            back = recent_sessions(day, 1, root, cfg, offline, include_end=False)
            c["before"] = dict((r["symbol"], r) for r in (back[-1][1] if back else []))
    except ValueError as e:
        errors.append(str(e))
        c.setdefault("today", {})
        c.setdefault("before", {})
        return None, None
    except (OSError, http.client.HTTPException) as e:     # urllib's URLError and HTTPError are OSErrors
        errors.append("could not read the closing file of the session before %s from the PSX site (%s), so the "
                      "screener prices have no last close and no price limits" % (day_label(day), _net_why(e)))
        c.setdefault("today", {})
        c.setdefault("before", {})
        return None, None
    if _pos((c["before"].get(sym) or {}).get("close")):
        return c["before"][sym]["close"], "close"
    return None, None


def screener_quotes(symbols, root, cfg, now_utc, offline):
    """({SYM: quote}, [errors]) from the screener (one page for all). The page comes through
    screener_html(): the PSX site first when it is on and not offline (that page also goes on
    the tape), else the saved screener.html, which is also the fallback when the site fails.
    Today's market tape is read too: the newest price as of now wins. The screener has no
    As-of time: the data time is the fetch time, or the page's top-bar clock when that is
    older (an old page from the site is old data), minus "data_delay_minutes" (outside the
    session, the end of the last segment). A page from the site with no share table (an
    error page, a new layout) is one error and never goes on the tape. The last close comes
    from the daily files."""
    now_pkt, delay = to_pkt(now_utc), _delay(cfg)
    lag = datetime.timedelta(minutes=delay)
    best, errors = {}, []
    saw = None                                  # "page" once a screener page (or tape line) was read

    def offer(sym, price, seen, fetched_utc, how):
        when = _session_end_before(seen, cfg)
        if _pos(price) and (sym not in best or (when, seen) >= best[sym][:2]):
            best[sym] = (when, seen, float(price), fetched_utc, how)

    def take(prices, seen, fetched_utc, how):
        for sym in symbols:
            offer(sym, num((prices or {}).get(sym)), seen, fetched_utc, how)

    for s in read_tape(tape_path(root, now_pkt.date(), "market")):
        seen = _asof_pkt(s.get("asof"))
        if str(s.get("source") or "screener") == "screener" and seen is not None and seen <= now_pkt:
            take(s.get("prices"), seen, parse_utc(s.get("fetched")) or pkt_to_utc(seen + lag), "tape")
            saw = "page"
    path = os.path.join(root, "screener.html")
    asked = site_on(cfg) and not offline
    page, how = screener_html(root, cfg, offline)
    if page is None:
        if asked:
            errors.append(how)                              # the site failed and no page is saved
    elif how == "site":
        parsed = parse_screener(page)
        snap = snapshot_from_screener(parsed, now_utc, delay)
        made = page_time(parsed["clock"])       # the page's own top-bar clock: an old page is old data
        seen = min(now_pkt, made) - lag if made else now_pkt - lag
        if not snap["prices"]:                  # an error page or a new layout: no tape line, one error
            errors.append("the PSX screener page had no share table at %s/screener (an error page or a new "
                          "layout?)" % PSX)
        else:
            snap["asof"] = seen.strftime("%Y-%m-%d %H:%M:%S")
            _record(root, now_utc, "market", snap, errors)
            saw = "page"
            take(snap["prices"], seen, now_utc, "site")
    else:
        if asked:                                           # the site failed, so page_html read the saved page
            errors.append("could not read the PSX site for %s/screener, so only the data folder was read" % PSX)
        parsed = parse_screener(page)
        saw = saw or "later"
        made = page_time(parsed["clock"]) or datetime.datetime.fromtimestamp(
            os.path.getmtime(path), datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(
            hours=KARACHI_HOURS)
        if made - lag <= now_pkt:
            saw = "page"
            take(dict((k, v.get("price")) for k, v in parsed["rows"].items()), made - lag, pkt_to_utc(made),
                 "saved page")
    quotes, cache = {}, {}
    for sym in symbols:
        if sym not in best:
            if saw == "page":
                errors.append("%s: not on the screener with a price (check the symbol)" % sym)
            elif saw == "later":
                errors.append("%s: the saved screener page is from later than the time asked for" % sym)
            elif not site_on(cfg):
                errors.append("%s: %s" % (sym, SITE_OFF % ("%s as %s" % (PSX + "/screener", path))))
            elif asked:
                errors.append("%s: no screener price (the PSX site could not be read)" % sym)
            else:
                errors.append("%s: no screener price in %s as of %s PKT (%s reads only the data folder)"
                              % (sym, root, now_pkt.strftime("%Y-%m-%d %H:%M"), _off_words(offline)))
            continue
        when, seen, price, fetched, how = best[sym]
        prev, kind = _last_close(sym, when.date(), root, cfg, offline, cache, errors)
        lower, upper = price_limits(prev) if prev else (None, None)
        quotes[sym] = {"symbol": sym, "price": price, "prev_close": prev, "time": utc_iso(pkt_to_utc(when)),
                       "fetched": utc_iso(fetched), "source": "psx-screener", "from": how, "lower": lower,
                       "upper": upper, "limits": "computed" if prev else None, "prev_close_from": kind,
                       "open": None, "high": None, "low": None}
    return quotes, errors


def get_quotes(symbols, source="psx-company", root="psx/data", cfg=None, now_utc=None, offline=False):
    """({SYM: quote}, [errors]) for the symbols. offline is False, or why the site is not used
    ("--offline" or "--now")."""
    now_utc = now_utc or utc_now()
    syms = [_sym(s) for s in symbols if _sym(s)]
    if source == "psx-screener":
        return screener_quotes(syms, root, cfg or {}, now_utc, offline)
    if source != "psx-company":
        raise AlertError("unknown quote source %r: psx-company or psx-screener" % (source,))
    quotes, errors = {}, []
    for sym in syms:
        q, errs = company_quote(sym, root, cfg or {}, now_utc, offline)
        if q:
            quotes[sym] = q
        errors += errs
    return quotes, errors


def get_indices(codes, root="psx/data", cfg=None, now_utc=None, offline=False):
    """({CODE: index reading}, [errors]) from the indices page, through page_html(): the PSX
    site first when it is on and not offline, else the saved indices.html (when its As-of is
    at or before now), which is also the fallback when the site fails. A reading: {index,
    current, change, change_pct, high, low, time, fetched, from}. change_pct is the move from
    the index's last close."""
    now_utc = now_utc or utc_now()
    now_pkt, cfg = to_pkt(now_utc), cfg or {}
    path, url = os.path.join(root, "indices.html"), PSX + "/indices"
    asked = site_on(cfg) and not offline
    found, errors, later, broken = None, [], None, False
    page, how = page_html(path, url, cfg, offline)
    if page is None:
        if asked:
            errors.append(how)                              # the site failed and no page is saved
    elif how == "site":
        p = parse_indices(page)
        if p["rows"] and p["as_of"]:
            found = (p, now_utc, "site")
        else:
            errors.append("the PSX indices page had no index table at %s" % url)
    else:
        if asked:                                           # the site failed, so page_html read the saved page
            errors.append("could not read the PSX site for %s, so only the data folder was read" % url)
        p = parse_indices(page)
        made = _page_clock(page)
        if p["as_of"] and p["as_of"] <= now_pkt:
            found = (p, pkt_to_utc(made) if made else now_utc, "saved page")
        elif p["as_of"]:
            later = p["as_of"]
        else:
            broken = True
    if found is None:
        if later:
            errors.append("the saved indices page is as of %s, later than the time asked for"
                          % _when(later, now_pkt.date()))
        elif broken:
            errors.append("the saved indices page %s has no As-of time or no index table: save it again" % path)
        elif not site_on(cfg):
            errors.append(SITE_OFF % ("%s as %s" % (url, path)))
        elif not errors:
            errors.append("no indices page in %s (%s reads only the data folder)" % (root, _off_words(offline)))
        return {}, errors
    p, fetched, how = found
    out = {}
    for code in codes:
        row = p["rows"].get(str(code).upper())
        cur = (row or {}).get("current")
        if cur is None:
            errors.append("%s is not on the indices page" % code)
            continue
        chg, pct = row.get("change"), row.get("change_pct")
        if pct is None and chg is not None and cur - chg:
            pct = 100.0 * chg / (cur - chg)
        out[str(code).upper()] = {"index": str(code).upper(), "current": cur, "change": chg, "change_pct": pct,
                                  "high": row.get("high"), "low": row.get("low"),
                                  "time": utc_iso(pkt_to_utc(p["as_of"])), "fetched": utc_iso(fetched), "from": how}
    return out, errors


def write_prices(path, quotes, now_utc, source, keep_newer=False):
    """Merge quotes into prices.json (SPEC section 7). as_of is the newest fetch time of these
    quotes; detail.time is each price's data time (the page's As-of), in UTC; detail.limits_from
    says where lower and upper come from: "company page" or "computed" (from the last close).
    A price kept from the old file keeps its own time, so the new as_of never makes it look
    fresh: one with no detail.time (written by hand) gets the old file's as_of, and one with
    no time at all is dropped. keep_newer (a run on the real clock, not a --now replay): a price
    in the old file with a newer data time than the new quote (and not later than now) stays,
    so an older saved page or tape line never takes the place of a newer price, or of one you
    wrote by hand."""
    old = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (ValueError, OSError):
            old = {}
    old = old if isinstance(old, dict) else {}
    prices = dict(old["prices"]) if isinstance(old.get("prices"), dict) else {}
    detail = dict(old["detail"]) if isinstance(old.get("detail"), dict) else {}
    old_time = utc_iso(parse_utc(old.get("as_of")))
    for s in [k for k in prices if k not in quotes]:
        d = detail.get(s) if isinstance(detail.get(s), dict) else {}
        if parse_utc(d.get("time")):
            continue
        if old_time is None:
            del prices[s]
            detail.pop(s, None)
            continue
        src = old.get("source") if isinstance(old.get("source"), str) else "manual"
        detail[s] = dict(d, time=old_time, fetched=d.get("fetched") or old_time, source=d.get("source") or src)
    stamp = utc_iso(now_utc)
    seen = []
    for s, q in quotes.items():
        d = detail.get(s) if isinstance(detail.get(s), dict) else {}
        mine = parse_utc(d.get("time")) or (parse_utc(old_time) if s in prices else None)
        new = parse_utc(q.get("time"))
        if keep_newer and s in prices and mine is not None and new is not None and new < mine <= now_utc:
            if not parse_utc(d.get("time")):    # written by hand: its time is the old file's as_of
                src = old.get("source") if isinstance(old.get("source"), str) else "manual"
                detail[s] = dict(d, time=old_time, fetched=d.get("fetched") or old_time, source=d.get("source") or src)
            seen.append(detail[s].get("fetched") or detail[s]["time"])
            continue
        prices[s] = q["price"]
        fetched = q.get("fetched") or stamp
        seen.append(fetched)
        detail[s] = {"prev_close": q.get("prev_close"), "time": q.get("time"), "source": q.get("source"),
                     "fetched": fetched, "lower": q.get("lower"), "upper": q.get("upper"),
                     "limits_from": q.get("limits")}
    doc = {"as_of": max(seen) if seen else stamp, "source": source,
           "note": NOTES.get(source, "PSX prices, about 5 minutes late"), "prices": prices, "detail": detail}
    _write(path, doc)
    return doc


def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def _read(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s is not valid JSON: %s" % (path, e))
    except OSError as e:
        raise AlertError("could not read %s: %s" % (path, e))


# ---------------------------------------------------------------- rules

PRICE_TYPES = {"above", "below", "stop", "target", "pct_move", "limit", "index_move", "opening_bell", "time",
               "trend"}
NO_QUOTE = {"opening_bell", "time", "index_move", "trend"}     # "trend" reads the closing files, not a quote


def rule_id(rule, n=None):
    """The id a rule rings under: its "id", else symbol-type (like DEMO-stop), else its place
    in the rules file (#3) when it is not even a rule."""
    if isinstance(rule, dict):
        if rule.get("id"):
            return str(rule["id"])
        if isinstance(rule.get("type"), str) and rule["type"]:
            return "%s-%s" % (rule.get("symbol") or rule.get("index") or "market", rule["type"])
    return "#%d" % n if n else "?"


def validate(rule, n=None):
    """Raise AlertError("rule <id>: what is wrong") for a rule the check cannot use. n is the
    rule's place in the rules file, for a rule with no id."""
    rid = rule_id(rule, n)
    if not isinstance(rule, dict):
        raise AlertError("rule %s: a rule must be a JSON object, not %r" % (rid, rule))
    t = rule.get("type")
    if not isinstance(t, str) or t not in PRICE_TYPES:
        raise AlertError("rule %s: unknown type %r (the types: %s)" % (rid, t, ", ".join(sorted(PRICE_TYPES))))
    for k in ("label", "note"):
        if rule.get(k) is not None and not isinstance(rule[k], str):
            raise AlertError('rule %s: "%s" must be text in quotes' % (rid, k))
    if t not in NO_QUOTE and not rule.get("symbol"):
        raise AlertError("rule %s: needs a symbol" % rid)
    if t in ("above", "below", "stop", "target") and not _pos(rule.get("price")):
        raise AlertError('rule %s: needs "price" as a number above 0, like 101.5 (no quotes)' % rid)
    if t in ("pct_move", "index_move") and not _pos(rule.get("pct")):
        raise AlertError('rule %s: needs "pct" as a number above 0, like 3 (no quotes)' % rid)
    if t in ("pct_move", "index_move") and rule.get("direction") not in (None, "up", "down"):
        raise AlertError("rule %s: direction is up or down" % rid)
    if t == "limit" and rule.get("direction") not in (None, "up", "down", "any"):
        raise AlertError("rule %s: direction is up, down or any" % rid)
    if t == "index_move" and not re.match(r"^[A-Za-z0-9]+$", str(rule.get("index") or HALT_INDEX)):
        raise AlertError("rule %s: index wants a code from the indices page, like KSE30" % rid)
    if t == "opening_bell" and rule.get("session") is not None and (
            isinstance(rule["session"], bool) or rule["session"] not in (1, 2)):
        raise AlertError("rule %s: session is 1 or 2 (2 is the Friday restart after the Juma break)" % rid)
    if t == "time" and _hhmm(rule.get("at")) is None:
        raise AlertError("rule %s: needs \"at\" as a Karachi time like \"15:20\"" % rid)
    if rule.get("ref") is not None and not _pos(rule["ref"]):
        raise AlertError("rule %s: ref must be a price above 0" % rid)
    if rule.get("date"):
        try:
            ok = bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(rule["date"]))) and datetime.date.fromisoformat(rule["date"])
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise AlertError("rule %s: date must be YYYY-MM-DD" % rid)
    rearm = rule.get("rearm")
    if rearm is not None and not isinstance(rearm, bool) and rearm != "daily":
        raise AlertError('rule %s: "rearm" is true, false or "daily"' % rid)
    if rule.get("swing") is not None and not isinstance(rule["swing"], bool):
        raise AlertError('rule %s: "swing" is true or false' % rid)
    if t == "trend":
        n = rule.get("sessions")
        if isinstance(n, bool) or not isinstance(n, int) or not 2 <= n <= 60:
            raise AlertError('rule %s: needs "sessions", a whole number from 2 to 60, like 10' % rid)
        if not rule.get("symbol"):
            raise AlertError("rule %s: needs a symbol" % rid)


def active(rule, today):
    """A rule with a "date" is watched on that Karachi date only."""
    return not rule.get("date") or str(rule["date"]) == today


def _rang_today(stamp, today):
    """Did a rule that rang at this UTC stamp ring on this Karachi date?"""
    if not stamp:
        return False
    t = parse_utc(stamp)
    if t is None:
        return str(stamp)[:10] == today
    return to_pkt(t).date().isoformat() == today


def ring_time(rule, d, cfg=None):
    """When a time rule rings on date d (a Karachi datetime.time). On a rule WITHOUT a "date",
    "before_close": true moves "at" (a Monday to Thursday time) with day d's hours, through
    config_time. A rule WITH a "date" already holds that day's own time (a plan's flat_by is
    moved for its day by psx-exit), so it is never moved twice."""
    at = _hhmm(rule.get("at"))
    if rule.get("before_close") and not rule.get("date"):
        try:
            at = config_time(at, d, cfg)
        except ValueError as e:
            raise AlertError(str(e))
    return at


def _time_tail(st):
    """How PSX stands, for the end of a time alert."""
    state, segs, seg = st["state"], st["segments"], st["segment"]
    if state == "open":
        if seg is not None and seg + 1 < len(segs):
            return ", the Juma break starts at %s (trading again at %s)." % (segs[seg][1], segs[seg + 1][0])
        return ", the market closes at %s." % st["closes"]
    nxt = (st.get("next_open") or "")[-5:]
    if state == "break":
        return ", PSX is in the Juma break until %s." % nxt
    if state == "pre-open":
        return ", PSX opens at %s (the pre-open is on)." % nxt
    if state == "closed" and st["opens"] and st["now_karachi"][-5:] < st["opens"]:
        return ", PSX opens at %s." % st["opens"]
    return ", the market has closed."


def _quote_day(q):
    """The Karachi date ("YYYY-MM-DD") of a quote's data time, or None."""
    when = _pkt_of((q or {}).get("time"))
    return when.date().isoformat() if when else None


def _big(x):
    return "{:,.2f}".format(x)


def evaluate(rules, quotes, status, state, now_utc, indices=None, cfg=None):
    """Pure function. Returns (rings, new_state). Each ring: {id, symbol, message}. A price
    rule uses a quote only when its data time is on today's Karachi date (an older price is
    not news). Above / below ring once; the other types at most once per Karachi day."""
    state = json.loads(json.dumps(state or {}))
    if not isinstance(state.get("rung"), dict):
        state["rung"] = {}
    rung = state["rung"]
    today = status["date"]
    day = datetime.date.fromisoformat(today)
    local = to_pkt(now_utc)
    stamp = utc_iso(now_utc)
    rings = []
    for r in rules:
        validate(r)
        rid = rule_id(r)
        if not active(r, today):
            continue
        typ = {"stop": "below", "target": "above"}.get(r["type"], r["type"])
        if typ == "opening_bell":
            second = r.get("session") == 2
            if status["open"] and (status["segment"] == 1 or not second) and not _rang_today(rung.get(rid), today):
                msg = ("Opening bell: PSX is open again after the Juma break (%s PKT, %s)." if second else
                       "Opening bell: PSX is open (%s PKT, %s).") % (status["now_karachi"], status["reason"])
                rings.append({"id": rid, "symbol": "", "message": msg})
                rung[rid] = stamp
            continue
        if typ == "time":
            at = ring_time(r, day, cfg)
            if status["segments"] and local.time() >= at and not _rang_today(rung.get(rid), today):
                label = r.get("label") or "time alert"
                set_for = r["at"] if _hhmm(r["at"]) == at else "%s, moved to %s for today's hours" % (
                    _hhmm(r["at"]).strftime("%H:%M"), at.strftime("%H:%M"))
                msg = "%s%s: it is %s PKT (set for %s)" % (label[:1].upper(), label[1:], local.strftime("%H:%M"),
                                                           set_for)
                msg += _time_tail(status)
                sym = _sym(r.get("symbol"))
                if sym:
                    msg += (" If %s is still open, close it: psx-broker flatten --symbol %s builds the sell "
                            "ticket, then psx-risk and your yes." % (sym, sym))
                rings.append({"id": rid, "symbol": sym, "message": msg})
                rung[rid] = stamp
            continue
        if typ == "index_move":
            code = str(r.get("index") or HALT_INDEX).upper()
            ix = (indices or {}).get(code)
            if not ix or _quote_day(ix) != today or ix.get("change_pct") is None:
                continue
            move = float(ix["change_pct"])
            want = r.get("direction")
            hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
            if hit and not _rang_today(rung.get(rid), today):
                msg = "%s moved %+.2f%% to %s at %s" % (code, move, _big(ix["current"]),
                                                        _when(_pkt_of(ix["time"]), day))
                if ix.get("change") is not None:
                    msg += " (its last close was %s)" % _big(ix["current"] - ix["change"])
                msg += "."
                if code == HALT_INDEX:
                    msg += (" If it stays 5% away from its last close for 5 minutes, PSX halts all trading for "
                            "60 minutes and cancels all open orders.")
                rings.append({"id": rid, "symbol": code, "message": msg})
                rung[rid] = stamp
            continue
        if typ == "trend":              # the closing-file walk checks it (swing_walk), never a quote
            continue
        sym = _sym(r["symbol"])
        q = quotes.get(sym)
        if not q or _quote_day(q) != today:
            continue
        p = q["price"]
        verb = "is" if status["open"] else "was"
        when = _when(_pkt_of(q.get("time")), day)
        if typ in ("above", "below"):
            hit = p >= r["price"] if typ == "above" else p <= r["price"]
            daily = r.get("rearm") == "daily"         # a swing rule: at most once a Karachi day
            if hit and (not _rang_today(rung.get(rid), today) if daily else rid not in rung):
                word = "at or above" if typ == "above" else "at or below"
                label = {"stop": "stop", "target": "target"}.get(r["type"], "alert level")
                rings.append({"id": rid, "symbol": sym, "message": "%s %s %.2f at %s: %s your %s %.2f%s."
                              % (sym, verb, p, when, word, label, r["price"],
                                 (" (" + r["note"] + ")") if r.get("note") else "")})
                rung[rid] = stamp
            elif not hit and rid in rung and r.get("rearm") and not daily:
                del rung[rid]
        elif typ == "pct_move":
            ref = r.get("ref") or q.get("prev_close") or q.get("open")
            if not ref:
                continue
            base = "your ref" if r.get("ref") else ("the last close" if q.get("prev_close") else "the open")
            move = 100.0 * (p / float(ref) - 1)
            want = r.get("direction")
            hit = abs(move) >= r["pct"] and (not want or (want == "up") == (move > 0))
            if hit and not _rang_today(rung.get(rid), today):
                rings.append({"id": rid, "symbol": sym, "message": "%s moved %+.2f%% to %.2f at %s (from %.2f, %s)."
                              % (sym, move, p, when, float(ref), base)})
                rung[rid] = stamp
        elif typ == "limit":
            want = r.get("direction") or "any"
            lo, hi = q.get("lower"), q.get("upper")
            up = want in ("up", "any") and _pos(hi) and round(p, 2) >= round(hi, 2)
            down = want in ("down", "any") and _pos(lo) and round(p, 2) <= round(lo, 2)
            if (up or down) and not _rang_today(rung.get(rid), today):
                msg = ("%s %s %.2f at %s: at its %s price limit %.2f. It cannot trade %s that today, so a %s may "
                       "not fill." % (sym, verb, p, when, "upper" if up else "lower", hi if up else lo,
                                      "above" if up else "below", "buy" if up else "sell"))
                if q.get("limits") == "computed":
                    msg += (" This limit is worked out from the last close: an ex-date moves it, so check the "
                            "company page.")
                rings.append({"id": rid, "symbol": sym, "message": msg})
                rung[rid] = stamp
    state["last_check"] = stamp
    return rings, state


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x).rstrip("0").rstrip(".")


def rules_from_plan(plan, cfg=None):
    """psx/plan.json -> a stop alert, a target alert and a flat-by-close time alert, for the plan's
    day. The flat-by time is the plan's own flat_by (psx-exit already moved it for that day);
    without one it is config_time(the config's flat_by, or 15:20) on the plan's day. A swing plan
    ("style": "swing") gives only the stop and the target, dated its buy day: from the fill on,
    the swing rules come from psx/portfolio.json (rules_from_positions)."""
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise AlertError("the plan has no symbol (run psx-exit first)")
    sym = _sym(plan["symbol"])
    out = []
    for key, typ, word in (("stop", "stop", "plan stop"), ("target", "target", "plan target")):
        v = plan.get(key)
        if not _pos(v):
            raise AlertError("the plan has no %s (run psx-exit)" % key)
        label = plan.get("%s_label" % key)
        out.append({"id": "%s-plan-%s" % (sym.lower(), key), "symbol": sym, "type": typ, "price": float(v),
                    "note": word + (": " + label if label else "")})
    day = None
    if plan.get("date"):
        try:
            day = datetime.date.fromisoformat(str(plan["date"]))
        except ValueError:
            raise AlertError("the plan's date %r is not YYYY-MM-DD" % plan["date"])
    if plan.get("style") == "swing":            # day 1 only, and no flat-by rule: a swing trade is held
        if day is None:
            raise AlertError('the swing plan has no buy day ("date"): run psx-exit again')
        for r in out:
            r["date"] = day.isoformat()
        return out
    flat = plan.get("flat_by")
    if not flat:
        flat = (cfg or {}).get("flat_by") or FLAT_BY_DEFAULT
        if _hhmm(flat) is None:
            raise AlertError('"flat_by" %r in psx/config.json is not a time like 15:20' % flat)
        if day:
            try:
                flat = config_time(_hhmm(flat), day, cfg).strftime("%H:%M")
            except ValueError as e:
                raise AlertError(str(e))
    if _hhmm(flat) is None:
        raise AlertError("the plan's flat_by %r is not a time like 15:20" % flat)
    out.append({"id": "%s-flat-by" % sym.lower(), "symbol": sym, "type": "time", "at": _hhmm(flat).strftime("%H:%M"),
                "label": "flat by close", "before_close": True})
    if day:
        for r in out:
            r["date"] = day.isoformat()
    return out


# ---------------------------------------------------------------- swing positions (psx/portfolio.json)

LEDGER = "psx/portfolio.json"   # the ledger: the source of truth for open swing positions (psx-broker writes it)
HALT_PCT = 5                    # the KSE30 move of a market halt (Rulebook 19.4)
SPLIT_AT = 0.10                 # a no-flag LDCP change is a split only 10% or more away from 1
FETCH_AFTER = 30                # today's closing file: asked for from 30 minutes after the close
FETCH_EVERY = 15                # and at most once every 15 minutes
PAGE_READS = 4                  # the ex-date look: company page reads a share a day, at most
PAGE_SHARES = 3                 # and at most this many shares
LOOK_BACK = 45                  # calendar days of closing files before the first day the walk checks, at least
LOOK_BACK_MAX = 200             # and at most: enough for the trend rule's sessions (up to 60) before that day
BROKER = "psx/state/broker.json"    # psx-broker's log (read only): today's stop-limit, and each stop raise
STILL_OPEN = ("approved", "placed")  # a yes the user may have typed in the app counts as placed (as psx-risk)
SWING_RULE = re.compile(r"^(.+)-swing-(stop|target|trend|time|protect)-(\d{4}-\d{2}-\d{2})$")
LIVE_STOP = ("If your stop-limit is in, it may have filled %s. Check your broker app. Record a fill with "
             "ticket.py fill, or record the order cancelled.")
HALT_WORDS = (" PSX may halt. A halt cancels all open orders at PSX, and may cancel a stop-limit your broker "
              "holds. After the restart, check it in your broker app. If PSX cancelled your stop-limit, record it "
              "first: ticket.py result --symbol %s --date %s --status cancelled. Then ticket.py protect --symbol %s "
              "makes a new one.%s")
FACTOR_SLACK = 0.0005           # a --scale may be this much under its ex-date's factor (as ticket.py stop --scale)
MISSING_FILE = "no closing file for %s yet: its stop, target and trend checks wait."
TODAY_FILE_OFF = ("The PSX site is off for this tool, so this closing file does not come by itself. Save %s as %s "
                  "with your browser (PSX puts it out about 30 to 40 minutes after the close).")
UNPLANNED = "%s: you hold it with no swing plan in %s, so the swing checks do not watch it. %s"
FROM_PLAN_SWING = ("From the fill on, the swing alerts come from psx/portfolio.json and stay on until %s is sold. "
                   "Auto-run (psx-autorun) checks every 5 minutes in the session, once after the close, and each "
                   "morning. Without it, run alerts.py check yourself.")
EX_FLAGS = ("XD", "XB", "XR")
ONE_DAY = datetime.timedelta(days=1)
MAYBE_BUY = "It may have come before your buy."


def _broker_log(path):
    """psx/state/broker.json (psx-broker's log) -> its records, a list of dicts. No file, or one that
    does not read, gives []: the alerts never fail on psx-broker's log."""
    try:
        doc = _read(path, None) if path else None
    except AlertError:
        return []
    recs = doc.get("orders") if isinstance(doc, dict) else None
    return [r for r in recs if isinstance(r, dict)] if isinstance(recs, list) else []


def _rec_day(rec):
    """The Karachi date ("YYYY-MM-DD") of a psx/state/broker.json record: its "date", else the day of its "at"."""
    d = _day(rec.get("date"))
    if d is None:
        when = _asof_pkt(rec.get("at"))
        d = when.date() if when else None
    return d.isoformat() if d else None


def _is_stop_limit(rec):
    """True for a record of a stop-limit order (psx-broker protect): its "type", else its plain words."""
    if str(rec.get("type") or "").strip().lower().replace("-", "_") == "stop_limit":
        return True
    return bool(re.search(r"\bstop[- ]limit\b", str(rec.get("plain") or "").split(" / ")[0].lower()))


def stop_limit_today(recs, sym, today):
    """True when psx/state/broker.json has a stop-limit sell of sym of today (YYYY-MM-DD) whose newest
    record is "approved" or "placed", and no sell of sym filled today: today's stop-limit is in."""
    newest, filled = {}, False
    for i, r in enumerate(recs):
        if _rec_day(r) != today or str(r.get("of") or today) != today:
            continue                            # another day, or a record that closes an older order
        if _sym(r.get("symbol")) != sym or str(r.get("side") or "").strip().lower() != "sell":
            continue
        if r.get("status") == "filled":
            filled = True
            continue
        newest[r.get("fingerprint") or i] = r
    return not filled and any(r.get("status") in STILL_OPEN and _is_stop_limit(r) for r in newest.values())


def stop_raises(recs, sym):
    """The stop raises of sym in psx/state/broker.json (ticket.py stop --set, which only moves a stop
    up): [(naive Karachi time, the stop before, the stop after)], oldest first. A scale is no raise."""
    out = []
    for r in recs:
        if r.get("status") != "stop" or _sym(r.get("symbol")) != sym or r.get("scale") is not None:
            continue
        when = _asof_pkt(r.get("at"))
        if when is not None and _pos(r.get("from")) and _pos(r.get("to")) and r["to"] > r["from"]:
            out.append((when, float(r["from"]), float(r["to"])))
    return sorted(out)


def stop_on(p, d, raises, cfg=None):
    """The plan stop that was in force when the session of day d began: the stop now, unless
    ticket.py stop --set raised it after that (then the stop before the first such raise). A raise
    on day d itself came after some of that day's trading, so that day's low is judged on the stop
    before it; the quote checks after the raise watch the rest of the day."""
    segs = session(d, cfg)
    start = datetime.datetime.combine(d, segs[0][0] if segs else datetime.time(0, 0))
    before = [old for when, old, new in raises if when >= start]
    return min([p["stop"]] + before)


def _day(text):
    """"YYYY-MM-DD" -> a date, else None."""
    t = str(text or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        return None
    try:
        return datetime.date.fromisoformat(t)
    except ValueError:
        return None


def _live(cfg):
    """True only when psx/config.json says "paper": false (the JSON false, as psx-broker reads it)."""
    return isinstance(cfg, dict) and cfg.get("paper", True) is False


def _shares(n):
    """A share count for people: 1436 -> "1,436"."""
    n = float(n)
    return "{:,}".format(int(n)) if n == int(n) else ("%g" % n)


def _act(kind, sym):
    """What the user could do when a swing rule is met (SPEC 5.2). It always ends with "You decide."."""
    how = {"target": " --limit P", "trend": " (--limit P in the pre-open)"}.get(kind, "")
    return "You could sell: psx-broker flatten --symbol %s%s, then psx-risk and your yes. You decide." % (sym, how)


def _holds_swing(pf):
    """True when psx/portfolio.json holds shares with a "plan" block of "style": "swing"."""
    rows = pf.get("positions") if isinstance(pf, dict) else None
    for p in rows if isinstance(rows, list) else []:
        if (isinstance(p, dict) and isinstance(p.get("plan"), dict) and p["plan"].get("style") == "swing"
                and _pos(p.get("shares", p.get("qty")))):
            return True
    return False


def swing_positions(pf, cfg=None):
    """psx/portfolio.json -> ([position], [errors]). A position counts when it holds shares above 0
    and its "plan" block (psx-broker writes it at the fill) says "style": "swing". Each position:
    symbol, key ("DEMO-2026-10-29"), date (the buy day), day (its text), shares, stop, target,
    exit_by, exit_by_time, trend_sessions, stop_at_buy and scaled (the ticket.py stop --scale
    records). A swing plan with no buy day or no stop is an error: that stop cannot be watched."""
    out, errors = [], []
    s = swing_settings(cfg)
    rows = pf.get("positions") if isinstance(pf, dict) else None
    for p in rows if isinstance(rows, list) else []:
        if not isinstance(p, dict) or not isinstance(p.get("plan"), dict) or p["plan"].get("style") != "swing":
            continue
        sym, shares, plan = _sym(p.get("ticker") or p.get("symbol")), p.get("shares", p.get("qty")), p["plan"]
        if not sym or not _pos(shares):
            continue
        day = _day(plan.get("date"))
        if day is None or not _pos(plan.get("stop")):
            errors.append('%s: the swing plan in %s needs a buy day ("date") and a "stop", so its stop could not '
                          'be watched' % (sym, LEDGER))
            continue
        n = plan.get("trend_sessions")
        if isinstance(n, bool) or not isinstance(n, int) or not 2 <= n <= 60:
            n = s.get("trend_exit_sessions")
            n = n if isinstance(n, int) and not isinstance(n, bool) and 2 <= n <= 60 else 10
        exit_by = _day(plan.get("exit_by"))
        at = _hhmm(plan.get("exit_by_time"))
        if exit_by and at is None:
            at = config_time(_hhmm((cfg or {}).get("flat_by")) or _hhmm(FLAT_BY_DEFAULT), exit_by, cfg)
        scaled = [x for x in plan.get("scaled") or [] if isinstance(x, dict) and _day(x.get("date"))
                  and _pos(x.get("factor"))]
        out.append({"symbol": sym, "key": "%s-%s" % (sym, day.isoformat()), "date": day, "day": day.isoformat(),
                    "shares": float(shares), "stop": float(plan["stop"]),
                    "target": float(plan["target"]) if _pos(plan.get("target")) else None,
                    "exit_by": exit_by, "exit_by_time": at.strftime("%H:%M") if at else None,
                    "trend_sessions": n, "scaled": scaled,
                    "stop_at_buy": float(plan["stop_at_buy"]) if _pos(plan.get("stop_at_buy")) else None})
    return out, errors


def unplanned(pf):
    """The symbols psx/portfolio.json holds (shares above 0) with no "plan" block of "style": "swing".
    The swing checks never watch them: no closing-file walk, no daily ring, no old-price error."""
    out = []
    rows = pf.get("positions") if isinstance(pf, dict) else None
    for p in rows if isinstance(rows, list) else []:
        if not isinstance(p, dict) or (isinstance(p.get("plan"), dict) and p["plan"].get("style") == "swing"):
            continue
        sym = _sym(p.get("ticker") or p.get("symbol"))
        if sym and _pos(p.get("shares", p.get("qty"))):
            out.append(sym)
    return _dedupe(out)


def unplanned_notes(pf, cfg, rules, today):
    """In a swing folder ("style": "swing"): one note for each share held with no swing plan, and how
    many rules in psx/alerts.json watch it on date today ("YYYY-MM-DD"). A day folder gets none (v1)."""
    try:
        if trade_style(cfg) != "swing":
            return []
    except ValueError:
        return []
    out = []
    for sym in unplanned(pf):
        n = len([r for r in rules or [] if isinstance(r, dict) and _sym(r.get("symbol")) == sym and active(r, today)])
        out.append(UNPLANNED % (sym, LEDGER, ("Only your own rules in psx/alerts.json watch it: %d today." % n) if n
                                else "No rule in psx/alerts.json watches it today."))
    return out


def _first_match(day, cfg=None):
    """The end of the first opening match of date d (the first segment start + MATCH_MINUTES), or None."""
    segs = session(day, cfg)
    if not segs:
        return None
    return datetime.datetime.combine(day, segs[0][0]) + datetime.timedelta(minutes=MATCH_MINUTES)


def _position_rules(positions, cfg=None, today=None):
    """The rules of the held swing positions (rules_from_positions does the reading)."""
    out = []
    match = _first_match(today, cfg) if today and _live(cfg) else None
    for p in positions:
        s, sym, d = p["symbol"].lower(), p["symbol"], p["day"]
        out.append({"id": "%s-swing-stop-%s" % (s, d), "symbol": sym, "type": "stop", "price": p["stop"],
                    "rearm": "daily", "swing": True, "note": "the swing plan in %s" % LEDGER})
        if p["target"]:
            out.append({"id": "%s-swing-target-%s" % (s, d), "symbol": sym, "type": "target", "price": p["target"],
                        "rearm": "daily", "swing": True, "note": "the swing plan in %s" % LEDGER})
        out.append({"id": "%s-swing-trend-%s" % (s, d), "symbol": sym, "type": "trend",
                    "sessions": p["trend_sessions"], "swing": True})
        if p["exit_by"] and p["exit_by_time"]:
            out.append({"id": "%s-swing-time-%s" % (s, d), "symbol": sym, "type": "time",
                        "date": p["exit_by"].isoformat(), "at": p["exit_by_time"], "label": "time stop",
                        "swing": True})
        if match:
            out.append({"id": "%s-swing-protect-%s" % (s, d), "symbol": sym, "type": "time",
                        "at": match.strftime("%H:%M"), "label": "stop-limit reminder", "swing": True})
    if positions:
        out.append({"id": "swing-halt", "type": "index_move", "index": HALT_INDEX, "pct": HALT_PCT, "swing": True})
    return out


def rules_from_positions(pf, cfg=None, today=None):
    """psx/portfolio.json -> the swing rules, built at each check and never written to psx/alerts.json.
    For each held position with a swing plan block: <sym>-swing-stop-<buy day> and -target- (daily
    re-arm), -trend- (a close under the lowest close of the plan's "trend_sessions" before it), -time-
    (the time stop: its exit-by day at its exit-by time) and, in live mode ("paper": false), -protect-
    (the stop-limit reminder at the end of today's first opening match). Once, while any swing share
    is held: swing-halt (KSE30 5%). A day position gives none. today is a Karachi date (default: now)."""
    positions = swing_positions(pf, cfg)[0]
    if today is None:
        today = to_pkt(utc_now()).date()
    return _position_rules(positions, cfg, today)


def _closing(cache, root, cfg, d, errors):
    """The closing file of date d in the data folder -> {symbol: row}, or None when it is not there.
    Read once per check (cache). The site is never asked here (see _fetch_today)."""
    if d not in cache:
        rows = None
        if os.path.exists(daily_path(root, d)):
            try:
                rows = daily_rows(d, root, cfg, True)
            except ValueError as e:
                errors.append(str(e))
        cache[d] = dict((r["symbol"], r) for r in rows) if rows else None
    return cache[d]


def _fetch_today(cache, root, cfg, now_utc, state, offline, errors):
    """Today's closing file from the PSX site, saved in the data folder: only with the site on and not
    offline, from 30 minutes after the close, at most once every 15 minutes (state["closing_file_tried"]).
    Older missing files are left to scan.py daily. -> {symbol: row} or None."""
    local = to_pkt(now_utc)
    d = local.date()
    segs = session(d, cfg)
    if not site_on(cfg) or offline or not segs:
        return None
    if local < datetime.datetime.combine(d, segs[-1][1]) + datetime.timedelta(minutes=FETCH_AFTER):
        return None
    tried = state.get("closing_file_tried") if isinstance(state.get("closing_file_tried"), dict) else {}
    last = parse_utc(tried.get(d.isoformat()))
    if last is not None and (now_utc - last).total_seconds() < FETCH_EVERY * 60:
        return None
    state["closing_file_tried"] = {d.isoformat(): utc_iso(now_utc)}
    try:
        rows = daily_rows(d, root, cfg, False)
    except ValueError as e:
        errors.append(str(e))
        return None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        errors.append("could not read today's closing file from the PSX site: %s" % (getattr(e, "reason", None) or e))
        return None
    if rows:
        cache[d] = dict((r["symbol"], r) for r in rows)
    return cache.get(d)


def _ex_event(flags, ldcp, close, adjacent):
    """An ex-date from a share's day (its flags and LDCP) and the close before -> an event dict, or None.
    A flag (XD, XB, XR) is new when the session before had no such flag: give only the new ones. With
    no new flag, an LDCP change of 10% or more counts as a split, but only next to the session before
    (adjacent), never across a missing file."""
    if not _pos(ldcp) or not _pos(close):
        return None
    factor = ldcp / close
    ev = {"flags": list(flags), "from": round(close, 2), "to": round(ldcp, 2), "factor": round(factor, 4)}
    if flags and not ("XB" in flags or "XR" in flags):
        payout = round(close - ldcp, 2)
        return dict(ev, kind="XD", payout=payout) if payout > 0 else None
    if flags:
        return dict(ev, kind="".join(flags)) if abs(factor - 1) >= 0.0005 else None
    if adjacent and abs(factor - 1) >= SPLIT_AT:
        return dict(ev, kind="split")
    return None


def _answered(p, ev):
    """True when the ledger plan has a ticket.py stop --scale record dated on or after the ex-date."""
    return any(str(x.get("date")) >= ev["date"] for x in p["scaled"])


def _ex_words(p, ev, today):
    """The ex-date words (SPEC 5.2): XD keeps the stop (cash); XB, XR or a split gives the factor.
    An ex-date on the buy day: a buy on the ex-date gets no payout, no bonus and no right, and its
    plan may be on either price scale. A factor over 1 (a reverse split?): ticket.py stop --scale
    takes 1 or less, so the words say what the user can do instead."""
    sym = p["symbol"]
    when = "today" if ev["date"] == today.isoformat() else "on " + day_label(_day(ev["date"]))
    bought = ev["date"] == p["day"]
    if ev["kind"] == "XD":
        if bought:
            return ("%s went ex %s (XD), the day you bought it. The reference price is about %s lower. A buy on the "
                    "ex-date gets no payout, so this payout is not yours. Your stop stays %.2f."
                    % (sym, when, money(ev["payout"]), p["stop"]))
        return ("%s went ex %s (XD). The reference price is about %s lower: the payout, paid to you in cash. "
                "Your stop stays %.2f, so a fall by the payout alone can meet it."
                % (sym, when, money(ev["payout"]), p["stop"]))
    what = "no flag: a split?" if ev["kind"] == "split" else ev["kind"]
    levels = ("Your stop %.2f and target %.2f are" % (p["stop"], p["target"]) if p["target"]
              else "Your stop %.2f is" % p["stop"])
    head = ("%s went ex %s (%s): the reference price %s from %.2f to %.2f (factor %.4f)."
            % (sym, when, what, "fell" if ev["factor"] < 1 else "rose", ev["from"], ev["to"], ev["factor"]))
    if ev["factor"] > 1:
        ring = ("so the target rule can ring on this alone, and the stop is far under the price" if p["target"]
                else "so the stop is far under the price")
        return ("%s %s on the old price scale, %s. ticket.py stop --scale takes a factor of 1 or less, so it cannot "
                "scale them up. You can raise the stop (ticket.py stop --symbol %s --set X; the stop x %.4f is about "
                "%.2f). Then record your choice: ticket.py stop --symbol %s --scale 1 (this alert then stops). Check "
                "your share count in your broker app: it may have gone down. No command lowers it in %s."
                % (head, levels, ring, sym, ev["factor"], round_down_tick(p["stop"] * ev["factor"]), sym, LEDGER))
    if bought:
        got = [w for f, w in (("XB", "bonus share"), ("XR", "right")) if f in ev["flags"]] or ["new share"]
        return ("%s You bought on the ex-date, so no %s comes to you for it. If your plan was made before the "
                "ex-date, %s on the old price scale. Check them against your buy price, then choose: scale them "
                "(ticket.py stop --symbol %s --scale %.4f) or keep them (--scale 1)."
                % (head, " or ".join(got), levels[0].lower() + levels[1:], sym, ev["factor"]))
    out = ("%s %s on the old price scale, so the stop rule can ring on this alone. You choose: scale them "
           "(ticket.py stop --symbol %s --scale %.4f) or keep them (--scale 1)." % (head, levels, sym, ev["factor"]))
    if "XB" in ev["flags"]:
        out += " Record the bonus shares when they come (ticket.py fill --bonus N)."
    elif ev["kind"] == "split":
        out += " Record the new shares when they come (ticket.py fill --bonus N)."
    if "XR" in ev["flags"]:
        out += (" A right letter may trade too. Pay for the right shares, or sell the right letter, by the date "
                "in the company's notice.")
    return out


def _ex_on(p, state, d, today):
    """The ex-date words for a stop ring on day d, or "" (an XB, XR or split that is scaled already: none)."""
    ev = ((state.get("swing_ex") or {}).get(p["key"]) or {}).get(d.isoformat())
    if not ev or (ev["kind"] != "XD" and _answered(p, ev)):
        return ""
    return _ex_words(p, ev, today)


def _file_ring(kind, p, bar, d, today, live, level=None, ex="", scale=False, stop=None):
    """The words of a rule met in the closing file of day d (SPEC 5.2). A day before today (a late
    check, a Mac that slept) says when the rule was met and that the shares are still held. scale:
    d is a bonus, rights or split ex-date not scaled yet, so a low open is the new price scale, not
    a gap (the ex words say so). stop: the stop in force when day d began (stop_on), default the
    plan stop. The gap words come only when the open was under the stop: an open at the stop sells
    at about the stop."""
    sym, day = p["symbol"], day_label(d)
    stop = p["stop"] if stop is None else stop
    if kind == "stop":
        words = ["%s traded at or under your stop %.2f on %s: the low in that day's closing file was %.2f "
                 "(official close %.2f)." % (sym, stop, day, bar["low"], bar["close"])]
        if bar["open"] < stop - 1e-9 and not scale:
            words.append("%s opened at %.2f on %s, under your stop %.2f (a gap): a sell fills near the open, not "
                         "at the stop." % (sym, bar["open"], day, stop))
    elif kind == "target":
        words = ["%s traded at or over your target %.2f on %s: the high in that day's closing file was %.2f."
                 % (sym, p["target"], day, bar["high"])]
    else:
        words = ["%s closed at %.2f on %s (the official close), under %.2f, the lowest close of the %d sessions "
                 "before." % (sym, bar["close"], day, level, p["trend_sessions"])]
    if kind != "trend" and d == p["date"]:
        words.append(MAYBE_BUY)
    if ex:
        words.append(ex)
    words.append("Your plan's %s rule is met." % kind if d == today else
                 "Your plan's %s rule was met on %s." % (kind, day))
    if kind == "trend":
        words.append("It says: sell in the next session.")
    if kind == "stop" and live:
        words.append(LIVE_STOP % ("today" if d == today else "on " + day))
    if d != today:
        words.append("You still hold %s shares in %s." % (_shares(p["shares"]), LEDGER))
    words.append(_act(kind, sym))
    return " ".join(words)


def _walk_days(p, state, root, cfg, local):
    """The days the walk of one position reads now, oldest first: every weekday after the newest one
    checked (from the buy day on) that is a PSX session or has a closing file, plus the days skipped
    before for a missing file. Today counts only after the close."""
    today = local.date()
    checked = _day((state.get("closes_checked") or {}).get(p["key"]))
    waiting = set(x for x in (_day(t) for t in (state.get("closes_missing") or {}).get(p["key"]) or []) if x)
    d = p["date"] if checked is None else max(p["date"], checked + ONE_DAY)
    out = set(x for x in waiting if x >= p["date"] and x < today and (
        trading_day(x, cfg)[0] or os.path.exists(daily_path(root, x))))
    while d <= today:
        if d.weekday() < 5 and (trading_day(d, cfg)[0] or os.path.exists(daily_path(root, d))):
            out.add(d)
        d += ONE_DAY
    segs = session(today, cfg)
    if today in out and segs and local < datetime.datetime.combine(today, segs[-1][1]):
        out.discard(today)                      # PSX is not closed yet: today's file is not out
    return sorted(out)


def _look_back(p, cache, root, cfg, first, errors):
    """The days with a closing file before date `first` that the walk needs: at least LOOK_BACK
    calendar days, and on until the share has the closes of its trend rule's sessions before
    `first` (at most LOOK_BACK_MAX calendar days), so a trend rule of up to 60 sessions is checked.
    -> [days], oldest first."""
    out, closes, d = [], 0, first - ONE_DAY
    least, most = first - datetime.timedelta(days=LOOK_BACK), first - datetime.timedelta(days=LOOK_BACK_MAX)
    while d >= most and (d >= least or closes < p["trend_sessions"]):
        rows = _closing(cache, root, cfg, d, errors)
        if rows is not None:
            out.append(d)
            closes += 1 if daily_bars(p["symbol"], [(d, rows)]) else 0
        d -= ONE_DAY
    return sorted(out)


def _trend_note(p, d, closes):
    """The note when a trend rule could not be checked on day d: too few closes in the data folder."""
    return ("%s: the trend rule (%d sessions) was not checked on %s: the data folder has only %d close%s of %s "
            "before that day." % (p["symbol"], p["trend_sessions"], day_label(d), closes, "" if closes == 1 else "s",
                                  p["symbol"]))


def swing_walk(p, cache, root, cfg, now_utc, state, offline, errors, notes=None, raises=()):
    """The closing-file walk of one swing position (D53). Each closing file in the data folder after
    the newest one checked (state["closes_checked"]), from the buy day on, is read once, oldest
    first: the stop on that day's low (the stop in force when that day began: stop_on, with the
    raises of psx/state/broker.json), the target on its high, the trend rule on its close
    (trend_break over the payout-adjusted closes up to that day), the gap words when it opened under
    the stop, and a new ex flag. A rule met there is kept in state["swing_met"] (its first day) and
    rings once; a rule already met rings no more here. A stop or target met only on the buy day's
    file may have come before the buy: it is kept in state["swing_maybe"] too, and a later day that
    meets it rings again. -> (rings, [missing days], [days read], [today, when its file is not out
    yet after the close])."""
    local = to_pkt(now_utc)
    today, stamp = local.date(), utc_iso(now_utc)
    notes = [] if notes is None else notes
    days = _walk_days(p, state, root, cfg, local)
    if not days:
        return [], [], [], []
    if today in days and _closing(cache, root, cfg, today, errors) is None:
        _fetch_today(cache, root, cfg, now_utc, state, offline, errors)
    have, d = _look_back(p, cache, root, cfg, days[0], errors), days[0]
    while d <= days[-1]:
        if _closing(cache, root, cfg, d, errors) is not None:
            have.append(d)
        d += ONE_DAY
    bars = daily_bars(p["symbol"], [(x, cache[x]) for x in have])
    spans, missing = window_sessions(have, cfg)
    order = dict((x, i) for i, x in enumerate(spans))
    at = dict((b["date"], i) for i, b in enumerate(bars))
    rung = state.setdefault("rung", {})
    met = state.setdefault("swing_met", {}).setdefault(p["key"], {})
    events = state.setdefault("swing_ex", {}).setdefault(p["key"], {})
    maybe = set(_maybe(state, p["key"]))
    s, live = p["symbol"].lower(), _live(cfg)
    rings, gaps, read, late = [], [], [], []
    for d in days:
        if cache.get(d) is None:
            if d != today:
                gaps.append(d)                  # checked when it comes
            else:
                late.append(d)                  # after the close, not out yet: the next walk reads it
            continue
        read.append(d)
        i = at.get(d)
        if i is None:
            continue                            # the share did not trade that day
        bar, prev = bars[i], (bars[i - 1] if i else None)
        if prev is not None and d.isoformat() not in events:
            new = [f for f in bar.get("flags") or [] if f in EX_FLAGS and f not in (prev.get("flags") or [])]
            next_to = order.get(d, -9) - order.get(prev["date"], -99) == 1     # no missing session between
            ev = _ex_event(new, bar.get("ldcp"), prev.get("close"), next_to)
            if ev:
                events[d.isoformat()] = dict(ev, date=d.isoformat(), seen="closing file")
        day_stop = stop_on(p, d, raises, cfg)
        checks = [("stop", bar["low"] <= day_stop + 1e-9, None)]
        if p["target"]:
            checks.append(("target", bar["high"] >= p["target"] - 1e-9, None))
        if not met.get("trend") or met["trend"] > d.isoformat():
            level, broke = trend_break(adjusted_closes(bars[:i + 1], None, missing), p["trend_sessions"])
            checks.append(("trend", broke, level))
            if level is None:
                notes.append(_trend_note(p, d, i))
        for kind, hit, level in checks:
            if not hit:
                continue
            if met.get(kind) and not (kind in maybe and d > p["date"]):
                if met[kind] > d.isoformat() and (kind == "trend" or d != p["date"]):
                    met[kind] = d.isoformat()   # a file that came late: the rule was met first on its day
                continue
            if kind != "trend" and d == p["date"]:
                maybe.add(kind)                 # the buy day's low or high may have come before the buy
            else:
                maybe.discard(kind)             # met on a later day: no doubt now (it rings again)
            met[kind] = d.isoformat()
            ex = _ex_on(p, state, d, today) if kind == "stop" else ""
            ev = events.get(d.isoformat())
            scale = bool(ex) and ev is not None and ev.get("kind") != "XD"
            rid = "%s-swing-%s-%s" % (s, kind, p["day"])
            rings.append({"id": rid, "symbol": p["symbol"],
                          "message": _file_ring(kind, p, bar, d, today, live, level, ex, scale, day_stop)})
            rung[rid] = stamp
            if ex:
                rung["%s-swing-ex-%s" % (s, d.isoformat())] = stamp
    _set_maybe(state, p["key"], maybe)
    checked = (state.get("closes_checked") or {}).get(p["key"])
    if read and (checked is None or read[-1].isoformat() > checked):
        state.setdefault("closes_checked", {})[p["key"]] = read[-1].isoformat()
    waiting = state.setdefault("closes_missing", {})
    if gaps:
        waiting[p["key"]] = [x.isoformat() for x in gaps]
    else:
        waiting.pop(p["key"], None)
    return rings, gaps, read, late


def _maybe(state, key):
    """The rules of a position met only on its buy day's file (state["swing_maybe"]): [kinds]."""
    got = (state.get("swing_maybe") or {}).get(key) if isinstance(state.get("swing_maybe"), dict) else None
    return [k for k in got if k in ("stop", "target")] if isinstance(got, list) else []


def _set_maybe(state, key, kinds):
    """Keep the "may have come before your buy" rules of a position, or drop the entry when there is none."""
    if kinds:
        state.setdefault("swing_maybe", {})[key] = sorted(kinds)
    elif isinstance(state.get("swing_maybe"), dict):
        state["swing_maybe"].pop(key, None)
        if not state["swing_maybe"]:
            del state["swing_maybe"]


def _session_before(today, root, cfg=None):
    """The PSX session before date today: the newest earlier weekday that is a trading day or has a
    closing file in the data folder (None after 15 days)."""
    d = today
    for _ in range(15):
        d -= ONE_DAY
        if d.weekday() < 5 and (trading_day(d, cfg)[0] or os.path.exists(daily_path(root, d))):
            return d
    return None


def ex_look(positions, cache, root, cfg, now_utc, state, offline, errors, notes):
    """The ex-date look at a held swing share's company page (D27, D56), in the morning job and at each
    check until the page shows today's data: the PSX site when it is on and not offline (at most 4
    reads a share a day, at most 3 shares), else the saved page. A flag after the name (XD, XB, XR)
    that the closing file of the session before does not have is new: the ex-date is today, and the
    factor is the page's LDCP / that close. A page with no new LDCP yet is read again later."""
    local = to_pkt(now_utc)
    today = local.date()
    look = state.get("ex_look") if isinstance(state.get("ex_look"), dict) else {}
    if look.get("date") != today.isoformat():
        look = {"date": today.isoformat()}
    state["ex_look"] = look
    before = _session_before(today, root, cfg)
    rows = _closing(cache, root, cfg, before, errors) if before else None
    asked = site_on(cfg) and not offline
    for p in positions[:PAGE_SHARES]:
        sym, key = p["symbol"], p["key"]
        mine = look.get(sym) if isinstance(look.get(sym), dict) else {"reads": 0}
        look[sym] = mine
        prev = (rows or {}).get(sym)
        events = state.setdefault("swing_ex", {}).setdefault(key, {})
        if mine.get("done") or today.isoformat() in events or not prev or not _pos(prev.get("close")):
            continue
        if asked:
            if mine.get("reads", 0) >= PAGE_READS:
                continue
            mine["reads"] = mine.get("reads", 0) + 1
        page, how = company_html(sym, root, cfg, offline)
        info = parse_company(page) if page is not None else {}
        asof = info.get("as_of")
        if asof is None or asof > local or asof.date() < before:
            if asked and not mine.get("noted"):         # the site was asked: say why the look got nothing, once a day
                if page is None:
                    why = how
                elif how == "site":
                    why = ("the page from the PSX site has no As-of time: an error page or a new layout?" if asof is None
                           else "the page from the PSX site is as of %s" % _when(asof, today))
                else:
                    why = "the PSX site did not give the page, and the saved page is %s" % (
                        ("as of " + _when(asof, today)) if asof else "not readable")
                notes.append("%s: the company page could not be read for the ex-date look (%s). The closing file "
                             "shows an ex-date after the close." % (sym, why))
                mine["noted"] = True
            continue                            # no page, a page from later, or an old one: not used
        if asof.date() == today:
            mine["done"] = True
        flags = [f for t in info.get("flags") or [] for f in re.findall("XD|XB|XR", str(t).upper())]
        new = [f for f in EX_FLAGS if f in flags and f not in (prev.get("flags") or [])]
        ev = _ex_event(new, (info.get("reg") or {}).get("ldcp"), prev["close"], True) if new else None
        if ev:
            events[today.isoformat()] = dict(ev, date=today.isoformat(), seen="company page")
            mine["done"] = True


def scale_answers(p, state):
    """[(record, [ex-dates])] for each ticket.py stop --scale record of a held swing position, in the
    ledger's order: the bonus, rights or split ex-dates (state["swing_ex"], never an XD) on or before
    the record's date that no earlier record answers. A record with none has [] (a scale typed before
    the ex-date was seen, or one written by hand). apply_scales and lowered_note both use this."""
    events = ((state or {}).get("swing_ex") or {}).get(p["key"]) or {}
    ex_days = sorted(d for d, ev in events.items() if isinstance(ev, dict) and ev.get("kind") != "XD")
    answered, out = set(), []
    for rec in p["scaled"]:
        mine = [d for d in ex_days if d <= str(rec["date"]) and d not in answered]
        answered |= set(mine)
        out.append((rec, mine))
    return out


def apply_scales(p, state):
    """Each ticket.py stop --scale record answers the bonus, rights or split ex-dates (never an XD) on
    or before its date that no earlier record answers (scale_answers). A record with a factor not 1 is
    used once that ex-date is known: a stop or target met from the ex-date on was met on the old price
    scale, so it is forgotten, and the closing files from the ex-date on are read again with the scaled
    plan. A record with no such ex-date yet waits (it is not marked used), so a scale typed before the
    walk or the company page saw the ex-date still counts. -> True when a record was used now."""
    key = p["key"]
    seen = state.setdefault("swing_scales", {})
    known = set(seen.get(key) or []) if isinstance(seen.get(key), list) else set()
    used, done = [], False
    for rec, mine in scale_answers(p, state):
        name = "%s x%s" % (rec["date"], rec["factor"])
        if name in known:
            used.append(name)                   # used before
            continue
        if not mine:
            continue                            # no ex-date for it yet: tried again at the next check
        used.append(name)
        if abs(float(rec["factor"]) - 1) < 1e-9:
            continue                            # "keep": the plan does not change
        met = (state.get("swing_met") or {}).get(key) or {}
        for kind in ("stop", "target"):
            if met.get(kind) and met[kind] >= mine[0]:
                del met[kind]
        _set_maybe(state, key, [k for k in _maybe(state, key) if met.get(k)])
        back = (_day(mine[0]) - ONE_DAY).isoformat()
        cc = (state.get("closes_checked") or {}).get(key)
        if cc and cc > back:
            state["closes_checked"][key] = back
        done = True
    seen[key] = _dedupe(used)
    return done


def swing_daily(p, state, now_utc):
    """The once-a-day swing rings of one held position: each rule met earlier (pending, D53), each
    ex-date not yet answered (an XD once), and past the exit-by day. -> rings."""
    local = to_pkt(now_utc)
    today, stamp = local.date(), utc_iso(now_utc)
    ts = today.isoformat()
    rung = state.setdefault("rung", {})
    s, sym, rings = p["symbol"].lower(), p["symbol"], []
    met = (state.get("swing_met") or {}).get(p["key"]) or {}
    maybe = _maybe(state, p["key"])
    for kind in ("stop", "target", "trend"):
        rid = "%s-swing-%s-%s" % (s, kind, p["day"])
        since = _day(met.get(kind))
        if since is None or (kind == "target" and not p["target"]) or _rang_today(rung.get(rid), ts):
            continue
        says = " It says: sell in the next session." if kind == "trend" else ""
        says += (" That was the buy day: " + MAYBE_BUY[0].lower() + MAYBE_BUY[1:]) if kind in maybe else ""
        rings.append({"id": rid, "symbol": sym, "message":
                      "%s: the %s rule was met on %s.%s You still hold %s shares in %s. You could sell: "
                      "psx-broker flatten --symbol %s, then psx-risk and your yes. You decide."
                      % (sym, kind, day_label(since), says, _shares(p["shares"]), LEDGER, sym)})
        rung[rid] = stamp
    for d, ev in sorted(((state.get("swing_ex") or {}).get(p["key"]) or {}).items()):
        rid = "%s-swing-ex-%s" % (s, d)
        if d < p["day"] or (rid in rung if ev.get("kind") == "XD" else (_answered(p, ev) or
                                                                     _rang_today(rung.get(rid), ts))):
            continue
        rings.append({"id": rid, "symbol": sym, "message": _ex_words(p, ev, today)})
        rung[rid] = stamp
    rid = "%s-swing-past-%s" % (s, p["day"])
    if p["exit_by"] and today > p["exit_by"] and not _rang_today(rung.get(rid), ts):
        rings.append({"id": rid, "symbol": sym, "message":
                      "%s is past its exit-by day (%s) and still in %s. The time stop is met. You could sell: "
                      "psx-broker flatten --symbol %s, then psx-risk and your yes. You decide."
                      % (sym, day_label(p["exit_by"]), LEDGER, sym)})
        rung[rid] = stamp
    return rings


def _sessions_after(a, b, cfg=None):
    """How many PSX sessions come after date a, up to and with date b."""
    n, d = 0, a
    while d < b:
        d += ONE_DAY
        if trading_day(d, cfg)[0]:
            n += 1
    return n


def _rule_kind(rid):
    """(the swing rule kind, its buy day) from an id like demo-swing-stop-2026-10-29, or ("halt", None)
    for swing-halt, or (None, None)."""
    if rid == "swing-halt":
        return "halt", None
    m = SWING_RULE.match(str(rid or ""))
    return (m.group(2), m.group(3)) if m else (None, None)


def swing_words(ring, positions, state, now_utc, cfg=None):
    """The swing words of a ring evaluate() gave a ledger rule (SPEC 5.2): the stop or target from a
    quote (it is then "met"), the time stop, the stop-limit reminder, and the halt rule."""
    kind, day = _rule_kind(ring["id"])
    today = to_pkt(now_utc).date()
    if kind == "halt":                          # a cancelled stop-limit is recorded first, so protect makes a new one
        syms = _dedupe([p["symbol"] for p in positions]) or ["X"]
        more = (" Do the same for %s." % " and ".join(syms[1:])) if len(syms) > 1 else ""
        return ring["message"] + HALT_WORDS % (syms[0], today.isoformat(), syms[0], more)
    p = next((x for x in positions if x["symbol"] == ring["symbol"] and x["day"] == day), None)
    if p is None:
        return ring["message"]
    sym = p["symbol"]
    if kind == "time":
        return ("%s: today is the exit-by day of your swing plan (the time stop, %d sessions). The plan says: out "
                "by %s PKT. You could sell: psx-broker flatten --symbol %s, then psx-risk and your yes. You decide."
                % (sym, _sessions_after(p["date"], p["exit_by"], cfg), clock_label(_hhmm(p["exit_by_time"])), sym))
    if kind == "protect":                       # no prices here: they come from ticket.py protect, after psx-risk
        wait = [ev for d, ev in sorted(((state.get("swing_ex") or {}).get(p["key"]) or {}).items())
                if d <= today.isoformat() and isinstance(ev, dict) and ev.get("kind") != "XD" and not _answered(p, ev)]
        how = (" Answer the ex-date alert first (scale or keep the stop). Then, if your broker app has none now, make "
               "a new ticket" if wait else
               " A stop-limit ticket is good for one day. If your broker app has none now, make a new ticket")
        return ("%s: %s has no stop-limit sell of today.%s: ticket.py protect --symbol %s. psx-risk checks it, then "
                "your yes." % (sym, BROKER, how, sym))
    met = state.setdefault("swing_met", {}).setdefault(p["key"], {})
    maybe = _maybe(state, p["key"])
    if kind in maybe:                           # met on the buy day's file; a quote after the fill meets it again
        met[kind] = today.isoformat()
        _set_maybe(state, p["key"], [k for k in maybe if k != kind])
    met.setdefault(kind, today.isoformat())
    words = [ring["message"]]
    if kind == "stop":
        ex = _ex_on(p, state, today, today)
        if ex:
            words.append(ex)
            state.setdefault("rung", {})["%s-swing-ex-%s" % (sym.lower(), today.isoformat())] = utc_iso(now_utc)
    words.append("Your plan's %s rule is met." % kind)
    if kind == "stop" and _live(cfg):
        words.append(LIVE_STOP % "today")
    words.append(_act(kind, sym))
    return " ".join(words)


def plan_alert(plan, pf, state, now_utc, cfg=None):
    """A swing plan in psx/plan.json with no position of its symbol in psx/portfolio.json: an ALERT
    once a day, from the close of the buy day, on that day and the next session only (then the
    after-close report lists the buy with no fill). None when it is not due."""
    if not isinstance(plan, dict) or plan.get("style") != "swing":
        return None
    sym, day = _sym(plan.get("symbol")), _day(plan.get("date"))
    if not sym or day is None:
        return None
    rows = pf.get("positions") if isinstance(pf, dict) else None
    for p in rows if isinstance(rows, list) else []:
        if isinstance(p, dict) and _sym(p.get("ticker") or p.get("symbol")) == sym and _pos(
                p.get("shares", p.get("qty"))):
            return None
    if "%s-%s" % (sym, day.isoformat()) in (state.get("swing_held") or {}):
        return None                             # it was held and is sold now: no "record the fill"
    local = to_pkt(now_utc)
    segs = session(day, cfg)
    if local.date() == day:
        if segs and local < datetime.datetime.combine(day, segs[-1][1]):
            return None
    elif local.date() != add_sessions(day, 1, cfg):
        return None
    rid = "%s-swing-plan-%s" % (sym.lower(), day.isoformat())
    if _rang_today((state.get("rung") or {}).get(rid), local.date().isoformat()):
        return None
    return {"id": rid, "symbol": sym, "message":
            "%s: a swing plan for %s and no %s position in %s. If you bought it, record the fill (ticket.py fill): "
            "until then its stop and target are not watched." % (sym, day_label(day), sym, LEDGER)}


def old_prices(positions, quotes, status, now_utc):
    """D54: in the session (PSX open), a held swing share with no price from today, or one over
    FRESH_MINUTES old by its data time, is an error: its stop was not checked. -> ([errors], {symbols})."""
    out, syms = [], set()
    if status["state"] != "open":
        return out, syms
    day = datetime.date.fromisoformat(status["date"])
    for sym in _dedupe([p["symbol"] for p in positions]):
        q = quotes.get(sym)
        if q and _quote_day(q) == status["date"] and _fresh(q, now_utc):
            continue
        when = _pkt_of((q or {}).get("time"))
        out.append("%s: no price newer than %d minutes (%s). The swing stop was not checked. Check the price in "
                   "your broker app." % (sym, FRESH_MINUTES, ("newest " + _when(when, day)) if when else "none found"))
        syms.add(sym)
    return out, syms


def lowered_note(p, state=None):
    """A ledger stop under its "stop_at_buy" (after the scales that count): it was lowered by hand. -> a note
    or None. A ticket.py stop --scale record counts only when it answers a bonus, rights or split ex-date
    this tool has seen (state["swing_ex"]) on or before its date, as in apply_scales (scale_answers). Then
    the stop at the buy is scaled by its factor, but never by less than the factor of those ex-dates
    (ticket.py stop --scale refuses less). A record with no such ex-date (one written by hand) counts for
    nothing. One record at a time, rounded down to the tick after each one, as ticket.py stop --scale and
    psx-risk do, so two scales never look like a stop lowered by hand."""
    if not p.get("stop_at_buy"):
        return None
    events = ((state or {}).get("swing_ex") or {}).get(p["key"]) or {}
    floor, n = p["stop_at_buy"], 0
    for rec, mine in scale_answers(p, state):
        f = float(rec["factor"])
        if not mine or abs(f - 1) < 1e-9:
            continue                            # no ex-date for it, or "keep" (1): the stop at the buy stays
        least, known = 1.0, True
        for d in mine:
            fx = events[d].get("factor")
            if _pos(fx):
                least *= float(fx)
            else:
                known = False                   # an ex-date with no factor: the record's own factor counts
        if known and f < least - FACTOR_SLACK:
            f = least                           # under the ex-date's factor: ticket.py refuses that
        if f < 1 - 1e-9:                        # a factor of 1 or more never lowers it
            floor, n = round_down_tick(floor * f), n + 1
    if p["stop"] >= floor - 0.005:
        return None
    return ("%s's stop in %s (%.2f) is under its stop at the buy (%.2f%s): it was lowered by hand."
            % (p["symbol"], LEDGER, p["stop"], floor, "" if not n else (" after the scale" if n == 1 else
                                                                        " after the scales")))


def _read_note(sym, days):
    """"DEMO: checked the closing files of ..." for the days the walk read."""
    labels = [day_label(d) for d in days]
    if len(labels) == 1:
        return "%s: checked the closing file of %s." % (sym, labels[0])
    if len(labels) <= 3:
        return "%s: checked the closing files of %s and %s." % (sym, ", ".join(labels[:-1]), labels[-1])
    return "%s: checked the closing files of %s to %s (%d files)." % (sym, labels[0], labels[-1], len(labels))


def _ledger(path):
    """psx/portfolio.json -> (doc or None, an error or None). No file is no error."""
    if not path or not os.path.exists(path):
        return None, None
    try:
        doc = _read(path, None)
    except AlertError as e:
        return None, "%s, so no swing position could be checked" % e
    if doc is not None and not isinstance(doc, dict):
        return None, "%s is not a JSON object, so no swing position could be checked" % path
    return doc, None


def _plan_doc(path):
    """psx/plan.json for the "a swing plan and no position" ALERT, or None (a file that does not read is
    left to psx-exit: the alerts never fail on it)."""
    try:
        doc = _read(path, None) if path else None
    except AlertError:
        return None
    return doc if isinstance(doc, dict) else None


def prune_swing(state, positions, now_utc):
    """Forget the swing state of positions no longer held (sold: their rules are gone too), and keep
    the held ones in state["swing_held"] for 10 days (so a sold plan never asks to record its fill)."""
    today = to_pkt(now_utc).date()
    held = set(p["key"] for p in positions)
    for name in ("closes_checked", "closes_missing", "swing_met", "swing_ex", "swing_scales", "swing_maybe"):
        if isinstance(state.get(name), dict):
            for k in [k for k, v in state[name].items() if k not in held or not v]:
                del state[name][k]              # sold, or nothing kept for it
            if not state[name]:
                del state[name]
    seen = state.get("swing_held") if isinstance(state.get("swing_held"), dict) else {}
    for k in held:
        seen[k] = today.isoformat()
    old = (today - datetime.timedelta(days=10)).isoformat()
    seen = dict((k, v) for k, v in seen.items() if str(v) >= old)
    if seen:
        state["swing_held"] = seen
    else:
        state.pop("swing_held", None)
    rung = state.get("rung") if isinstance(state.get("rung"), dict) else {}
    gone = utc_iso(now_utc - datetime.timedelta(days=40))
    mine = set(p["symbol"].lower() for p in positions)
    for rid in [r for r, t in rung.items() if str(t) < gone and (
            ("-swing-" in r and r.split("-swing-")[0] not in mine) or (r == "swing-halt" and not mine))]:
        del rung[rid]                           # old rings of shares no longer held


def check_swing(positions, rings, quotes, status, state, now_utc, root, cfg, offline, errors, cache, notes, recs=()):
    """The swing part of a check, after evaluate(): the swing words of its rings, the closing-file walk
    of each held position, the once-a-day rings, old prices (D54) and notes. cache holds the closing
    files read in this check; recs are the records of psx/state/broker.json (its stop raises). When
    the walk finds the ex-date of a scale typed before it, the files from that ex-date on are read
    again at once with the scaled plan. A stop-limit reminder of a share whose stop rule is met now
    is dropped (no_reminder could not see a stop the walk met). -> (every ring, {symbols whose price
    was too old})."""
    for ring in rings:
        if _rule_kind(ring["id"])[0]:
            ring["message"] = swing_words(ring, positions, state, now_utc, cfg)
    gaps, late, more = set(), set(), []
    for p in positions:
        raises = stop_raises(recs, p["symbol"])
        got, missing, read, now_missing = swing_walk(p, cache, root, cfg, now_utc, state, offline, errors, notes,
                                                     raises)
        if apply_scales(p, state):
            again, missing, read2, now_missing = swing_walk(p, cache, root, cfg, now_utc, state, offline, errors,
                                                            notes, raises)
            one = {}                            # the second read gives the same ring again: one per rule
            for r in got + again:
                one[r["id"]] = r
            got, read = list(one.values()), sorted(set(read) | set(read2))
        more += got
        gaps |= set(missing)
        late |= set(now_missing)
        if read:
            notes.append(_read_note(p["symbol"], read))
    for p in positions:
        more += swing_daily(p, state, now_utc)
    old, stale = old_prices(positions, quotes, status, now_utc)
    errors += old
    notes += [MISSING_FILE % day_label(d) for d in sorted(gaps | late)]
    for d in sorted(late) if not site_on(cfg) else []:
        notes.append(TODAY_FILE_OFF % ("%s/download/mkt_summary/%s.Z" % (PSX, d.isoformat()), daily_path(root, d)))
    notes += [n for n in (lowered_note(p, state) for p in positions) if n]
    notes[:] = _dedupe(notes)
    quiet = set("%s-swing-protect-%s" % (p["symbol"].lower(), p["day"]) for p in positions if stop_met(p, state))
    for r in rings:
        if r["id"] in quiet:                    # the stop is met in this check (the walk): the plan says sell
            state.get("rung", {}).pop(r["id"], None)    # not rung: it may ring if a scale forgets the met stop
    return [r for r in rings if r["id"] not in quiet] + more, stale


# ---------------------------------------------------------------- commands

def _config(a):
    path = getattr(a, "config", None)
    cfg = _read(path, {}) if path else {}
    if not isinstance(cfg, dict):
        raise AlertError("%s is not a JSON object" % path)
    return cfg


def _offline(a):
    """False, or why the site is not used: --offline, or --now (a replay sees only the data folder)."""
    return "--offline" if a.offline else ("--now" if a.now else False)


def holdings(a):
    syms = []
    pf = _read(a.portfolio, {})
    for p in pf.get("positions", []) if isinstance(pf, dict) else []:
        if isinstance(p, dict):
            syms.append(_sym(p.get("ticker") or p.get("symbol")))
    plan = _read(a.plan, {})
    if isinstance(plan, dict) and plan.get("symbol"):
        syms.append(_sym(plan["symbol"]))
    return [s for s in syms if s]


def _dedupe(items):
    out = []
    for s in items:
        if s and s not in out:
            out.append(s)
    return out


def _seen(quotes, indices, syms, codes, day):
    """", DEMO 101.23 (as of 10:53 AM PKT)" for the "Nothing hit" line."""
    parts = ["%s %.2f (as of %s)" % (s, quotes[s]["price"], _when(_pkt_of(quotes[s]["time"]), day))
             for s in syms if s in quotes]
    parts += ["%s %+.2f%% (as of %s)" % (c, indices[c]["change_pct"], _when(_pkt_of(indices[c]["time"]), day))
              for c in codes if c in indices and indices[c].get("change_pct") is not None]
    return (", " + ", ".join(parts)) if parts else ""


def _notes(quotes, indices, status, now_utc):
    """Plain notes about prices no alert could use (another day) or that are old."""
    out, today = [], status["date"]
    day = datetime.date.fromisoformat(today)
    for name, q in sorted(list(quotes.items()) + list(indices.items()), key=lambda x: x[0]):
        when = _pkt_of(q.get("time"))
        if when is None or when.date().isoformat() != today:
            out.append("%s: the newest data is from %s, not today, so no alert used it."
                       % (name, _when(when, day) if when else "an unknown time"))
        elif status["open"] and not _fresh(q, now_utc):
            out.append("%s: the data is %d minutes old (as of %s). Save a new page, or check the price in your "
                       "broker app."
                       % (name, (now_utc - pkt_to_utc(when)).total_seconds() // 60, _when(when, day)))
    return out


def cmd_clock(a):
    st = market_status(_now(a.now), _config(a))
    if a.json:
        print(json.dumps(st, indent=2))
    else:
        print(status_line(st))
        if hours_line(st):
            print(hours_line(st))
        if not st["open"] and st.get("next_open"):
            pre = datetime.datetime.strptime(st["next_open"], "%Y-%m-%d %H:%M") - datetime.timedelta(minutes=15)
            print("Next open: %s PKT (the pre-open starts at %s PKT)." % (st["next_open"], clock_label(pre)))
        if st["calendar_note"]:
            print("Calendar: " + st["calendar_note"])
    return 0 if st["open"] else 1


def cmd_quote(a):
    syms = _dedupe([_sym(s) for s in a.symbols] + (holdings(a) if a.holdings else []))
    if not syms:
        raise AlertError("which symbols? alerts.py quote DEMO OGDC, or --holdings")
    cfg, now = _config(a), _now(a.now)
    quotes, errors = get_quotes(syms, a.source, a.data, cfg, now, _offline(a))
    if a.write and quotes:
        write_prices(a.write, quotes, now, a.source, keep_newer=not a.now)
    if a.json:
        print(json.dumps({"quotes": quotes, "errors": errors}, indent=2))
        return 0 if quotes else 1
    day = to_pkt(now).date()
    for s in syms:
        q = quotes.get(s)
        if not q:
            continue
        chg = ""
        if q.get("prev_close"):
            chg = " (%+.2f%% vs last close %.2f)" % (100.0 * (q["price"] / q["prev_close"] - 1), q["prev_close"])
        lim = ""
        if q.get("lower") and q.get("upper"):
            lim = "  limits %.2f to %.2f%s" % (q["lower"], q["upper"],
                                               " (computed)" if q["limits"] == "computed" else "")
        flags = ("  " + " ".join(q["flags"])) if q.get("flags") else ""
        print("%-6s %.2f%s%s  as of %s%s  [%s, %s]" % (s, q["price"], chg, lim, _when(_pkt_of(q["time"]), day),
                                                        flags, q["source"], q["from"]))
    for e in errors:
        print("error: " + e)
    if a.write and quotes:
        print("Wrote %d price(s) to %s." % (len(quotes), a.write))
    if not quotes:
        print(MANUAL_HINT % (a.write or "psx/prices.json"))
    print("PSX prices here are about 5 minutes late: for alerts and size checks only. "
          "Your broker's price is the one that fills.")
    return 0 if quotes else 1


def check_rules(rules, quotes, status, state, now_utc, indices=None, cfg=None):
    """evaluate() one rule at a time, so a rule that fails is skipped and the others are still
    checked. Returns (rings, new_state, [errors])."""
    rings, errors = [], []
    _, state = evaluate([], quotes, status, state, now_utc, indices, cfg)
    for r in rules:
        try:
            got, state = evaluate([r], quotes, status, state, now_utc, indices, cfg)
        except AlertError as e:
            errors.append(str(e))
            continue
        except Exception as e:      # one bad rule must never stop the others
            errors.append("rule %s: could not be checked (%s)" % (rule_id(r), e))
            continue
        rings += got
    return rings, state, errors


def _net_why(e):
    """A site or file error in plain words: "PSX answered HTTP 500", or its reason (like "timed out")."""
    if isinstance(e, urllib.error.HTTPError):
        return "PSX answered HTTP %d" % e.code
    return str(getattr(e, "reason", None) or e) or e.__class__.__name__


def _broker_path(a):
    """psx-broker's log: --broker, else broker.json next to the --state file (psx/state/broker.json)."""
    return getattr(a, "broker", None) or os.path.join(os.path.dirname(a.state) or ".", "broker.json")


def _quiet_notes(a, pf, rules=()):
    """The notes of the shares held with no swing plan (unplanned_notes) for a command that has not read
    the config yet: a config or a --now that does not read gives none (the command says so itself)."""
    try:
        cfg = _config(a)
        today = to_pkt(_now(getattr(a, "now", None))).date().isoformat()
    except AlertError:
        return []
    return unplanned_notes(pf, cfg, rules, today)


def stop_met(p, state):
    """True when the stop rule of a held swing position is met (state["swing_met"]): it rang from a price
    or a closing file, today or on an earlier day, and it stays met until the share is sold (or a scale
    for an ex-date forgets it). A low only in the buy day's file (state["swing_maybe"]) may have come
    before the buy: that does not count here."""
    met = state.get("swing_met") if isinstance(state, dict) else None
    mine = met.get(p["key"]) if isinstance(met, dict) else None
    return isinstance(mine, dict) and _day(mine.get("stop")) is not None and "stop" not in _maybe(state, p["key"])


def no_reminder(positions, quotes, recs, status, now_utc, state=None):
    """The ids of the stop-limit reminder rules that must not ring now: psx/state/broker.json has
    today's stop-limit sell of the share (approved or placed, no fill); a fresh price is at or under
    the stop (a stop-limit with that trigger would fire at once, and the stop alert says so); or the
    plan's stop rule is met (stop_met). Then the plan says sell (psx-broker flatten), not a new
    stop-limit, and the stop alert says so once a day. check_swing also drops a reminder of this check
    when the closing-file walk meets the stop. Else the reminder can still ring at a later check today
    (after a stop-limit cancelled in the app, for one)."""
    out = set()
    for p in positions:
        q = quotes.get(p["symbol"])
        low = bool(q) and _quote_day(q) == status["date"] and _fresh(q, now_utc) and q["price"] <= p["stop"] + 1e-9
        if low or stop_limit_today(recs, p["symbol"], status["date"]) or stop_met(p, state or {}):
            out.add("%s-swing-protect-%s" % (p["symbol"].lower(), p["day"]))
    return out


def met_lines(positions, state, now_utc):
    """For a check that rang nothing new: one line for each met rule of a held swing position
    (state["swing_met"]), so a met stop never reads as "Nothing hit"."""
    out, today = [], to_pkt(now_utc).date()
    rung = state.get("rung") if isinstance(state.get("rung"), dict) else {}
    for p in positions:
        met = (state.get("swing_met") or {}).get(p["key"]) or {}
        maybe = _maybe(state, p["key"])
        for kind in ("stop", "target", "trend"):
            since = _day(met.get(kind))
            if since is None or (kind == "target" and not p["target"]):
                continue
            when = _pkt_of(rung.get("%s-swing-%s-%s" % (p["symbol"].lower(), kind, p["day"])))
            out.append("%s: the %s rule was met on %s.%s%s You still hold %s shares in %s." % (
                p["symbol"], kind, day_label(since),
                (" That was the buy day: " + MAYBE_BUY[0].lower() + MAYBE_BUY[1:]) if kind in maybe else "",
                (" It rang today at %s." % _when(when)) if when and when.date() == today else "",
                _shares(p["shares"]), LEDGER))
        if p["exit_by"] and today >= p["exit_by"]:             # the time stop: on the exit-by day once it rang
            rid = "%s-swing-%s-%s" % (p["symbol"].lower(), "time" if today == p["exit_by"] else "past", p["day"])
            when = _pkt_of(rung.get(rid))
            rang = bool(when) and when.date() == today
            if rang or today > p["exit_by"]:
                out.append("%s: the time stop is met: %s.%s You still hold %s shares in %s." % (
                    p["symbol"], "today is the exit-by day" if today == p["exit_by"] else
                    "the exit-by day was %s" % day_label(p["exit_by"]),
                    (" It rang today at %s." % _when(when)) if rang else "", _shares(p["shares"]), LEDGER))
    return out


def cmd_check(a):
    """Exit 0: something rang. 1: nothing rang. 2: a rule or a file could not be read, or a held
    swing share had no fresh price in the session (also when something else rang: both are
    printed). A bad rule is skipped; the rest are checked. The swing positions in psx/portfolio.json
    are checked too, with or without rules in psx/alerts.json."""
    doc = _read(a.rules, None)
    empty = doc is None or (isinstance(doc, dict) and not doc.get("rules"))
    pf, pf_error = _ledger(a.portfolio)
    plan = _plan_doc(a.plan)
    no_rules = "alerts.py: no rules in %s: add some with alerts.py add --from-plan" % a.rules
    if empty and pf_error is None and not _holds_swing(pf) and (plan or {}).get("style") != "swing":
        print(no_rules, file=sys.stderr)
        for n in _quiet_notes(a, pf):           # a swing folder: a share held with no swing plan is not watched
            print("note: " + n, file=sys.stderr)
        return 1
    if not empty and (not isinstance(doc, dict) or not isinstance(doc["rules"], list)):
        raise AlertError('%s wants {"rules": [...]}, a list of rules' % a.rules)
    rules, errors = [], []
    for n, r in enumerate([] if empty else doc["rules"], 1):
        try:
            validate(r, n)
        except AlertError as e:
            errors.append(str(e))
            continue
        rules.append(r)
    cfg, now = _config(a), _now(a.now)
    status = market_status(now, cfg)
    if pf_error is not None and trade_style(cfg) != "swing":
        pf_error = None                         # a day folder: the alerts do not read the ledger (as in v1)
    positions, bad = swing_positions(pf, cfg)
    loose = unplanned_notes(pf, cfg, rules, status["date"])
    state = _read(a.state, {})
    state = state if isinstance(state, dict) else {}
    nopos = plan_alert(plan, pf, state, now, cfg)
    if empty and not positions and not bad and pf_error is None and nopos is None:
        print(no_rules, file=sys.stderr)
        for n in loose:
            print("note: " + n, file=sys.stderr)
        return 1
    errors += ([pf_error] if pf_error else []) + bad
    covered = set()                             # day 1 plan rules that the ledger rules take over
    for p in positions:
        covered |= set([("%s-plan-stop" % p["symbol"].lower(), p["day"]),
                        ("%s-plan-target" % p["symbol"].lower(), p["day"])])
    rules = [r for r in rules if (r.get("id"), r.get("date")) not in covered]
    rules += [r for r in _position_rules(positions, cfg, datetime.date.fromisoformat(status["date"]))
              if status["open"] or _rule_kind(r["id"])[0] not in ("protect", "halt")]   # these are for the session
    watched = [r for r in rules if active(r, status["date"])]
    syms = _dedupe([_sym(r.get("symbol")) for r in watched if r.get("type") not in NO_QUOTE])
    codes = _dedupe([str(r.get("index") or HALT_INDEX).upper() for r in watched if r.get("type") == "index_move"])
    try:
        quotes, more = get_quotes(syms, a.source, a.data, cfg, now, _offline(a)) if syms else ({}, [])
    except (OSError, http.client.HTTPException) as e:     # one bad answer must never stop the check
        quotes, more = {}, ["could not read the prices: %s" % _net_why(e)]
    errors += more
    try:
        indices, more = get_indices(codes, a.data, cfg, now, _offline(a)) if codes else ({}, [])
    except (OSError, http.client.HTTPException) as e:
        indices, more = {}, ["could not read the indices page: %s" % _net_why(e)]
    errors += more
    recs = _broker_log(_broker_path(a)) if positions else []
    cache, notes = {}, list(loose)
    for p in positions:
        apply_scales(p, state)
    if positions:
        try:
            ex_look(positions, cache, a.data, cfg, now, state, _offline(a), errors, notes)
        except (OSError, http.client.HTTPException) as e:
            errors.append("could not read the company pages for the ex-date look: %s" % _net_why(e))
    skip = no_reminder(positions, quotes, recs, status, now, state)     # after the scales: a met stop may be gone
    rules = [r for r in rules if r.get("id") not in skip]
    rings, state, more = check_rules(rules, quotes, status, state, now, indices, cfg)
    errors += more
    stale = set()
    if positions:
        rings, stale = check_swing(positions, rings, quotes, status, state, now, a.data, cfg, _offline(a), errors,
                                   cache, notes, recs)
    if nopos:
        rings.append(nopos)
        state.setdefault("rung", {})[nopos["id"]] = utc_iso(now)
    if pf_error is None and not bad:
        prune_swing(state, positions, now)
    if not a.no_write:
        _write(a.state, state)
        if quotes:
            write_prices(a.prices_out, quotes, now, a.source, keep_newer=not a.now)
    code = 2 if errors else (0 if rings else 1)
    if a.json:
        doc = {"market": status, "rings": rings, "errors": errors,
               "quotes": dict((s, q["price"]) for s, q in quotes.items()),
               "indices": dict((c, x.get("change_pct")) for c, x in indices.items())}
        if positions or loose:
            doc["notes"] = notes
            doc["swing"] = [{"symbol": p["symbol"], "date": p["day"], "stop": p["stop"], "target": p["target"],
                             "exit_by": p["exit_by"].isoformat() if p["exit_by"] else None,
                             "closes_checked": (state.get("closes_checked") or {}).get(p["key"]),
                             "met": (state.get("swing_met") or {}).get(p["key"]) or {}} for p in positions]
        print(json.dumps(doc, indent=2))
        return code
    print(status_line(status))
    if status["calendar_note"]:
        print("Calendar: " + status["calendar_note"])
    for r in rings:
        print("ALERT " + r["message"])
    if not rings:
        held = met_lines(positions, state, now)         # a met swing rule is never "Nothing hit"
        print("%s %d rule(s) watched today%s." % ("No new alert." if held else "Nothing hit.", len(watched),
                                                  _seen(quotes, indices, syms, codes,
                                                        datetime.date.fromisoformat(status["date"]))))
        for x in held:
            print(x)
    for n in _notes(dict((s, q) for s, q in quotes.items() if s not in stale), indices, status, now) + notes:
        print("note: " + n)
    for e in errors:
        print("error: " + e)
    if rings:
        print(AFTER_RING)
    return code


def cmd_add(a):
    doc = _read(a.rules, {"rules": []})
    if not isinstance(doc, dict):
        raise AlertError("%s is not a JSON object with \"rules\"" % a.rules)
    doc.setdefault("rules", [])
    if not isinstance(doc["rules"], list):
        raise AlertError('%s wants {"rules": [...]}, a list of rules' % a.rules)
    if a.from_plan:
        plan = _read(a.plan, None)
        if plan is None:
            raise AlertError("no plan at %s (run psx-exit first)" % a.plan)
        cfg = _config(a)
        new = rules_from_plan(plan, cfg)
        ids = set(r["id"] for r in new)
        doc["rules"] = [r for r in doc["rules"] if not isinstance(r, dict) or r.get("id") not in ids] + new
        state = _read(a.state, {})
        if isinstance(state, dict) and state.get("rung"):
            for i in ids:
                state["rung"].pop(i, None)
            _write(a.state, state)
        _write(a.rules, doc)
        for r in new:
            print("Added %s: %s" % (r["id"], json.dumps(r)))
        if new[0].get("date"):
            ok, why = trading_day(datetime.date.fromisoformat(new[0]["date"]), cfg)
            if not ok:
                print("Note: %s is not a PSX trading day (%s), so these alerts will not ring." % (new[0]["date"], why))
        if plan.get("style") == "swing":
            print(FROM_PLAN_SWING % new[0]["symbol"])
        else:
            print("Run alerts.py check every few minutes while the trade is open (a scheduled task or /loop). "
                  "PSX prices here are about 5 minutes late, and PSX holds no stop order for you: when the stop "
                  "rings, you sell in your broker app.")
        if not site_on(cfg):
            print("The PSX site is off, so no new price comes in by itself: save a fresh company page before "
                  "each check, or watch your stop in your broker app.")
        return 0
    moved = None
    if a.opening_bell:
        rule = {"id": a.id or ("opening-bell-2" if a.session == 2 else "opening-bell"), "type": "opening_bell"}
        if a.session == 2:
            rule["session"] = 2
    elif a.at:
        t = _hhmm(a.at)
        if t is None:
            raise AlertError("--at wants a Karachi time like 15:20")
        if a.before_close and a.date:
            # a rule with a date holds that day's own time, so move it here, once
            try:
                day = datetime.date.fromisoformat(a.date)
            except ValueError:
                raise AlertError("--date wants a Karachi date like 2026-10-29")
            t2 = config_time(t, day, _config(a))
            if t2 != t:
                moved = "%s on a Monday to Thursday is %s on %s." % (t.strftime("%H:%M"), t2.strftime("%H:%M"),
                                                                      day_label(day))
            t = t2
        at = t.strftime("%H:%M")
        rule = {"id": a.id or ("time-%s" % at.replace(":", "")), "type": "time", "at": at,
                "label": a.label or "time alert"}
        if a.symbol:
            rule["symbol"] = _sym(a.symbol)
        if a.before_close:
            rule["before_close"] = True
    elif a.index:
        if a.pct is None:
            raise AlertError("--index wants --pct, like --index KSE30 --pct 4.5")
        code = a.index.strip().upper()
        rule = {"id": a.id or ("%s-move-%s" % (code.lower(), _n(a.pct))), "type": "index_move", "index": code,
                "pct": a.pct}
        if a.direction:
            rule["direction"] = a.direction
    else:
        if not a.symbol:
            raise AlertError("--symbol is required (or --from-plan, --at, --opening-bell, --index)")
        sym = _sym(a.symbol)
        if a.above is not None:
            rule = {"type": "above", "price": a.above}
        elif a.below is not None:
            rule = {"type": "below", "price": a.below}
        elif a.stop is not None:
            rule = {"type": "stop", "price": a.stop}
        elif a.target is not None:
            rule = {"type": "target", "price": a.target}
        elif a.pct is not None:
            rule = {"type": "pct_move", "pct": a.pct}
            if a.direction:
                rule["direction"] = a.direction
        elif a.limit is not None:
            rule = {"type": "limit", "direction": a.limit}
        else:
            raise AlertError("give one of --above, --below, --stop, --target, --pct or --limit")
        val = rule.get("price", rule.get("pct", rule.get("direction")))
        rid = a.id or ("%s-%s-%s" % (sym.lower(), rule["type"], val if rule["type"] == "limit" else _n(val)))
        rule = dict({"id": rid}, symbol=sym, **rule)
        if a.rearm:
            rule["rearm"] = True
        if a.note:
            rule["note"] = a.note
    if a.date:
        rule["date"] = a.date
    validate(rule)
    if any(r.get("id") == rule["id"] for r in doc["rules"] if isinstance(r, dict)):
        raise AlertError("a rule with id %s already exists" % rule["id"])
    doc["rules"].append(rule)
    _write(a.rules, doc)
    print("Added %s: %s" % (rule["id"], json.dumps(rule)))
    if moved:
        print(moved)
    return 0


def cmd_list(a):
    doc = _read(a.rules, {"rules": []})
    state = _read(a.state, {})
    pf, _ = _ledger(a.portfolio)
    ledger = rules_from_positions(pf, _config(a)) if _holds_swing(pf) else []   # built now, never stored
    has = isinstance(doc, dict) and bool(doc.get("rules"))
    loose = _quiet_notes(a, pf, doc["rules"] if has and isinstance(doc["rules"], list) else [])
    if not has and not ledger:
        print("No rules yet.")
        for n in loose:                         # a swing folder: a share held with no swing plan
            print("note: " + n)
        return 1
    if has and not isinstance(doc["rules"], list):
        raise AlertError('%s wants {"rules": [...]}, a list of rules' % a.rules)
    rung = state.get("rung") if isinstance(state, dict) else None
    rung = rung if isinstance(rung, dict) else {}
    for n, r in enumerate(doc["rules"] if has else [], 1):
        rid = rule_id(r, n)                     # a bad rule is listed too; check says what is wrong with it
        body = dict((k, v) for k, v in r.items() if k != "id") if isinstance(r, dict) else r
        print("%-24s %s%s" % (rid, json.dumps(body), ("  (rang %s)" % rung[rid]) if rung.get(rid) else ""))
    for r in ledger:
        body = dict((k, v) for k, v in r.items() if k != "id")
        print("%-24s %s%s  (from %s)" % (r["id"], json.dumps(body), ("  (rang %s)" % rung[r["id"]])
                                         if rung.get(r["id"]) else "", a.portfolio))
    for n in loose:
        print("note: " + n)
    return 0


def cmd_reset(a):
    state = _read(a.state, {})
    if not isinstance(state, dict) or a.id not in (state.get("rung") or {}):
        print("%s has not rung (nothing to reset)." % a.id)
        return 1
    del state["rung"][a.id]
    _write(a.state, state)
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price, limit, time and bell alerts on the PSX clock (psx-alerts).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--now", help="pretend it is this time (no offset = Karachi time); then only the data "
                                     "folder is read, never the PSX site")
        p.add_argument("--config", default="psx/config.json",
                       help="psx_site, data_delay_minutes, holidays (default psx/config.json)")
        p.add_argument("--json", action="store_true")

    def net(p):
        common(p)
        p.add_argument("--source", default="psx-company", choices=list(SOURCES))
        p.add_argument("--data", default="psx/data", metavar="DIR", help="the data folder (default psx/data)")
        p.add_argument("--offline", action="store_true", help="never use the PSX site: read only the data folder")

    common(sub.add_parser("clock", help="is PSX open?"))
    p = sub.add_parser("quote", help="the latest PSX quotes (about 5 minutes late)")
    net(p)
    p.add_argument("symbols", nargs="*")
    p.add_argument("--holdings", action="store_true", help="add the symbols in your portfolio and plan")
    p.add_argument("--portfolio", default="psx/portfolio.json")
    p.add_argument("--plan", default="psx/plan.json")
    p.add_argument("--write", nargs="?", const="psx/prices.json", metavar="PATH",
                   help="save to prices.json (default psx/prices.json)")
    p = sub.add_parser("check", help="check every rule, ring what hit")
    net(p)
    p.add_argument("--rules", default="psx/alerts.json")
    p.add_argument("--state", default="psx/state/alerts.json")
    p.add_argument("--prices-out", default="psx/prices.json")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--portfolio", default=LEDGER,
                   help="the ledger: its swing positions are checked too (default psx/portfolio.json)")
    p.add_argument("--plan", default="psx/plan.json",
                   help='a swing plan with no position gives an alert (default psx/plan.json)')
    p.add_argument("--broker", metavar="FILE",
                   help="psx-broker's log, read only: today's stop-limit and each stop raise (default: broker.json "
                        "next to --state, so psx/state/broker.json)")
    p = sub.add_parser("add", help="add a rule")
    p.add_argument("--rules", default="psx/alerts.json")
    p.add_argument("--state", default="psx/state/alerts.json")
    p.add_argument("--symbol")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--above", type=float)
    g.add_argument("--below", type=float)
    g.add_argument("--stop", type=float)
    g.add_argument("--target", type=float)
    g.add_argument("--pct", type=float, help="a percent move from the last close (with --index: of that index)")
    g.add_argument("--limit", nargs="?", const="any", choices=["up", "down", "any"],
                   help="rings at the upper (up) or lower (down) daily price limit, or either (any)")
    g.add_argument("--opening-bell", action="store_true")
    g.add_argument("--at", metavar="HH:MM", help="a time alert, Karachi time")
    g.add_argument("--from-plan", action="store_true", help="stop, target and flat-by-close from the plan")
    p.add_argument("--index", help="with --pct: an index from the indices page, like KSE30")
    p.add_argument("--session", type=int, choices=[1, 2], help="with --opening-bell: 2 is the Friday restart")
    p.add_argument("--plan", default="psx/plan.json")
    p.add_argument("--config", default="psx/config.json", help="flat_by when the plan has none, and the calendar")
    p.add_argument("--label", help="what a time alert says, e.g. \"flat by close\"")
    p.add_argument("--before-close", action="store_true",
                   help="a time alert that moves with each day's close (15:20 is 16:20 on a Friday)")
    p.add_argument("--date", help="watch this rule on this Karachi date only (YYYY-MM-DD)")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--rearm", action="store_true")
    p.add_argument("--note")
    p.add_argument("--id")
    p = sub.add_parser("list", help="list the rules")
    p.add_argument("--rules", default="psx/alerts.json")
    p.add_argument("--state", default="psx/state/alerts.json")
    p.add_argument("--portfolio", default=LEDGER,
                   help="the swing rules of its positions are listed too (default psx/portfolio.json)")
    p.add_argument("--config", default="psx/config.json", help="paper mode and the calendar, for the swing rules")
    p = sub.add_parser("reset", help="re-arm a rule that rang")
    p.add_argument("id")
    p.add_argument("--state", default="psx/state/alerts.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "check": cmd_check, "add": cmd_add,
                "list": cmd_list, "reset": cmd_reset}[a.cmd](a)
    except (AlertError, ValueError) as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 2 if a.cmd == "check" else 1     # check: 2 is "a rule or a file could not be read"


if __name__ == "__main__":
    sys.exit(main())
