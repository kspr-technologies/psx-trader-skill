#!/usr/bin/env python3
"""Time Machine for psx-timemachine. Standard library only, Python 3.8.

Replays a planned PSX day trade before you risk a rupee: 10,000 simulated runs of
the rest of the session from your entry, plus the same rule on past days. A swing plan
("style": "swing") gets swing mode: 10,000 runs of the next 20 sessions (see the end).

    python3 timemachine.py run                          # psx/plan.json, to its flat_by, 10,000 replays, seed 19
    python3 timemachine.py run --sessions 10            # a swing plan: 10 sessions, not to its exit-by day
    python3 timemachine.py run --write                  # also save psx/timemachine.json
    python3 timemachine.py run --mode daily             # daily mode, even when there are bars
    python3 timemachine.py run --seed 7 --replays 20000 --json
    python3 timemachine.py run --symbol DEMO --date 2026-10-29 --entry 100.65 \\
        --stop 99.55 --target 101.75 --entry-time "10:15 AM"
    python3 timemachine.py chart --from                 # ASCII fan chart of the saved run

Data: files on your computer only (psx/data, or --data DIR):
  bars/<SYM>.csv                5-minute or 1-minute bars from your broker (Karachi times)
  tape/YYYY-MM-DD/<SYM>.jsonl   company page snapshots the pack's tools recorded
  mkt_summary/YYYY-MM-DD.Z      PSX daily closing files (daily mode)
The PSX site is used only in daily mode, only when psx/config.json says "psx_site": true,
and never with --offline or --now (a replay of a past time). Then a missing closing file
is fetched once (plain GET, 1.5 s apart) and saved. The market tape (market.jsonl) is not
used: it samples too seldom.

Two modes. --mode auto (the default) picks one and says why:

bars: when the data folder holds at least 20 FULL past sessions of the stock's own
5-minute bars with the same hours as the plan's day (Mon to Thu, Friday, Ramadan or
Ramadan Friday). The method of the US pack:
  1. The pool: for every bar of the day after the first, each past session's bar as
     ratios to the bar before it: (open, high, low, close) / previous close. A bar's
     number follows the PSX grid: bar 0 is 09:30 Mon to Thu, 09:15 on a Friday and in
     Ramadan; the Friday break has no bars, so Friday 14:30 is bar 33.
  2. One replay starts at the entry price and walks the rest of the session. For each
     bar i it picks a slot j = randint(i - window, i + window), the window cut to bar i's
     own segment (a Friday morning bar never mixes with the afternoon) and never bar 0,
     then one past bar from pool[j] (random.choice), and scales it to the price. The
     first bar after the Juma break (Friday 14:30, bar 33) holds the move over the break:
     it draws only from pool[33], and no other bar draws slot 33.
  3. The stop is checked before the target in every bar, so a bar that touches both
     counts as the stop. No price goes past the day's price limits.
  4. History: the plan's own rule (an opening-range breakout with the same opening
     range, stop rule and target R) re-run on every one of those past sessions.

daily: otherwise. A simpler model from the stock's last 60 daily bars before the plan's
day (the closing files; a day with no regular-board trade is skipped):
  1. sigma_day = sqrt(mean(ln(high / low) ^ 2) / (4 ln 2)) (Parkinson).
  2. The steps are the plan's 5-minute bars left until --until. sigma_step = sigma_day /
     sqrt(the bars in a full session of that kind: 72 Mon to Thu, 57 Friday, 51
     Ramadan, 39 Ramadan Friday). Each step adds sigma_step x a normal draw to the log
     price (no drift). The step's high and low come from the Brownian bridge between its
     ends a and b (log prices, s = sigma_step, U1 and U2 uniform in (0, 1]):
       high = (a + b + sqrt((b - a)^2 - 2 s^2 ln U1)) / 2
       low  = (a + b - sqrt((b - a)^2 - 2 s^2 ln U2)) / 2
     Fixed draw order per step: two uniforms for the normal draw (Box-Muller), U1, U2.
  3. Stop first, then target; a step that touches both counts as the stop. No price
     goes past the day's price limits.
  4. History: the plan's stop and target as percents of its entry, put on each past day
     from its open: how many days reached the target only, the stop only, both (the
     order inside a day is not known) and neither.

Both modes:
  Outcomes: "target" or "stop" (whichever came first), or "neither" when the trade is
  still open at --until (default: the plan's flat_by, the last bar closed by then; with
  no flat_by the close of the plan's day, 15:30 Mon to Thu, 16:30 on a Friday, 13:30 in
  Ramadan, 12:30 on a Ramadan Friday). "green" = target first, or above the entry at
  --until (by price alone). R = the target's R at the target, -1 at the stop, else
  (price at --until - entry) / risk, and then less the PSX costs in R of that trade:
  round_trip_cost(entry, exit price, 1000) / 1000 / risk, with the plan's "costs" block,
  else the config's, else the defaults. The history's R counts costs the same way. A gap
  through the stop still counts at the stop here; a real fill can be worse, and at a
  price limit there may be no fill at all.
  The price at --until: median and the 5th to 95th percentile of all replays (pct(a, q)
  = sorted a at index int(q * n)). The fan chart uses a second run of 2,000 paths with
  the same seed. The histogram has 24 bins between the 1st and 99th percentile.
  Price limits: the plan's "limits" (psx-exit copies the company page's CIRCUIT
  BREAKER), else computed from the LDCP with price_limits().

The verdict line looks at the replays that hit one side (target or stop) and compares
the target's share of them, target / (target + stop), with the share that breaks even
at the plan's target R with the costs: (1 + c) / (1 + R), c = the costs in R of a trade
that ends at the stop. With no costs that is 50 % at 1R and 33 % at 2R; with costs of
0.33R it is 66 % at 1R. The gap, in points:
  less than 5 points    "Close to a coin flip" (1R, no costs) / "Close to break-even"
  5 to 15 points        "Leans toward the target" / "Leans toward the stop"
  15 points or more     "Strongly toward the target" / "Strongly toward the stop"
When c is the target's R or more, the line says the costs eat the target.
Below it: a note when more than 40 % of the replays hit neither (the price at --until
decides those), and the average R when its sign goes against the lean.

Long only (PSX bans blank sales on the regular market). The run refuses in one plain line
when the plan does not make sense, when the entry bar has not closed yet, or when there
are too few sessions. Same plan, same data and same --seed give the same numbers. It is
a simulation, not a prediction: results, news and halts can do what those days never did.
Times are Karachi time (PKT, UTC+5 all year); --now "2026-10-29 10:20" pins the clock.

Swing mode: a plan with "style": "swing" (psx-exit writes it in a swing folder) runs it by
itself and says why. --mode bars, --mode daily and --until are for day plans: a swing plan
refuses them. --sessions N is for a swing plan only.
  1. The pool: the stock's last 60 daily bars before the buy day (the closing files, as in
     daily mode), each as ratios (open, high, low, close) to its own LDCP. PSX lowers the
     LDCP on an ex-date, so a payout is not a fall. A bar with no LDCP is left out.
  2. One replay walks the buy day (session 1) and the N sessions after it: --sessions N,
     default the plan's max_hold_sessions (20), so it ends on the exit-by day. Each session
     is one past bar (random.choice from random.Random(seed)), scaled by the close before it
     and kept inside that session's price limits (float_limits: price_limits in floats; the
     buy day uses the plan's limits). One draw a session, in that order. The buy day is
     scaled by its own LDCP when the plan's limits give it (buy_day_ldcp: the company page,
     or the buy day's closing file), else by the last close. On an ex-date PSX lowers that
     LDCP under the last close: then the real closes before the buy day are scaled by
     LDCP / last close too (the trend rule), and a note says so.
     The buy day: an open at or under the limit fills at the open; else a low at or under
     the limit fills at the limit, and then only the stop counts that day (the high may
     have come first); else no fill: a PSX order lasts one day, so that replay ends.
     Each session held, in this order: an open at or under the stop sells at the open (a
     gap); an open at or over the target sells at the open; a low at or under the stop
     sells at the stop; a high at or over the target sells at the target. At the close:
     the trend rule, trend_break over the real adjusted closes before the buy day plus the
     replay's closes, sells at the next open (one more draw). The last session sells at
     its close (the time stop: psx-alerts rings it by the plan's flat-by time that day).
  3. R = (exit - fill) / (entry - stop), less the costs in R of that buy and sale, each leg
     leg_cost() of 1,000 shares on the 0.01 tick, with trade_costs() in swing style: the
     commission counts on both sides, whatever "day_trade_one_side" says. The average R
     counts the replays that filled.
  4. The verdict is the one above (target against stop, its break-even with the costs at a
     fill at the limit). A note follows when over 40 % end by the trend rule or the time
     stop. The fan chart: one column a session (its close), the dates as labels.
  5. History: the plan's stop and target as percents of its entry, put on each past day
     from its open (prices adjusted for payouts and splits), walked forward up to N real
     sessions with the same rules. Counts: target, stop (and how many were gaps), trend,
     time, and "the data ran out" (the newest closing file came first).
"""

import argparse
import bisect
import csv
import datetime
import gzip
import io
import json
import math
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

# ---------------------------------------------------------------- copied verbatim from tests/psx_core.py
# (never edit a copy: tests/test_repo.py checks them; write a new function with a new name instead)

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
SHIFT_SPLIT = datetime.time(12, 30)
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name
COSTS = {"commission_pct": 0.15, "commission_min_per_share": 0.03, "sales_tax_pct": 15,
         "other_charges_pct": 0.0066, "day_trade_one_side": False}
SWING = {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20, "trend_exit_sessions": 10,
         "entry_buffer_pct": 1.0, "max_open_positions": 3, "max_open_risk_pkr": None,
         "max_gap_loss_pkr": None, "stop_limit_gap_pct": 1.0}

# ---------------------------------------------------------------- this tool's own settings

MIN_SESSIONS = 20    # bars mode: full past sessions with the plan's hours; daily mode: daily bars
DAILY_BARS = 60      # daily mode: the last 60 daily bars before the plan's day
SAMPLE_PATHS = 160
BAND_PATHS = 2000
BAND_Q = (0.05, 0.25, 0.5, 0.75, 0.95)
HIST_BINS = 24
COIN_FLIP = 0.05     # verdict thresholds, see the docstring
STRONG = 0.15
MANY_NEITHER = 0.40  # also swing mode: the note when over 40 % end by the trend rule or the time stop
KIND_WORDS = {"normal": "Mon to Thu", "friday": "Friday", "ramadan": "Ramadan Mon to Thu",
              "ramadan-friday": "Ramadan Friday"}
MAX_SESSIONS = 250   # swing mode: --sessions and max_hold_sessions, 1 to about a year of sessions
MAX_TREND = 60       # swing mode: trend_sessions, 2 to 60 (as psx-alerts checks a trend rule)
SWING_NEXT = "Next: /psx-budget sizes it on the buy day, before you buy. Nothing was placed."


class TMError(Exception):
    pass


# ---------------------------------------------------------------- helpers copied verbatim from tests/psx_core.py

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


# ---------------------------------------------------------------- time and the day's grid

def parse_clock(text, what):
    """'10:15 AM', '10:15am', '15:20' or '3:20' (a bare 1 to 8 o'clock means p.m.) ->
    datetime.time, Karachi time."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*(?:([AaPp])\.?\s*[Mm]\.?)?\s*$", str(text or ""))
    if not m:
        raise TMError("%s wants a Karachi time like 10:15 AM or 15:20, not %r" % (what, text))
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mi > 59 or h > 23 or (ap and not 1 <= h <= 12):
        raise TMError("%s %r is not a clock time" % (what, text))
    if ap == "p" and h != 12:
        h += 12
    elif ap == "a" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 8:
        h += 12
    return datetime.time(h, mi)


def hhmm(t):
    """A datetime or a time -> "10:15"."""
    return "%02d:%02d" % (t.hour, t.minute)


def parse_hhmm(text, flag="--at"):
    """"10:15" -> datetime.time(10, 15). ValueError with a plain message otherwise."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("%s wants a Karachi time like 10:15" % flag)
    return datetime.time(int(m.group(1)), int(m.group(2)))


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


def day_grid(d, cfg=None):
    """(kind, spans, segs) of date d: session_kind(), the 5-minute slot_spans() and each
    segment as (first bar, bar after its last) by bar number, so [(0, 72)] Mon to Thu
    and [(0, 33), (33, 57)] on a Friday. A bad calendar block in the config is one
    plain error."""
    try:
        kind = session_kind(d, cfg)
        spans = slot_spans(d, 5, cfg)
        parts = session(d, cfg) or ()
    except ValueError as e:
        raise TMError(str(e))
    segs, i = [], 0
    for a, b in parts:
        lo, hi = datetime.datetime.combine(d, a), datetime.datetime.combine(d, b)
        n = sum(1 for s, _ in spans if lo <= s < hi)
        segs.append((i, i + n))
        i += n
    return kind, spans, segs


def _between(d, spans, segs, got):
    """The plain refusal for an entry time that leaves no bar to replay."""
    ends = [e for _, e in spans]
    gap = ", outside the Juma break" if len(segs) > 1 else ""
    return ("the entry must be a bar close from %s to %s on %s%s, to leave bars to replay (got %s)"
            % (clock_label(ends[0]), clock_label(ends[-2]), day_label(d), gap, got))


# ---------------------------------------------------------------- the plan

def _num(v, what):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise TMError("the plan needs a number for %s, got %r" % (what, v))
    if not x > 0 or math.isinf(x):
        raise TMError("the plan's %s must be a positive price, got %r" % (what, v))
    return x


def load_config(path):
    """psx/config.json -> dict ({} when there is no such file)."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise TMError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(cfg, dict):
        raise TMError("%s is not a config: it wants {...}" % path)
    return cfg


def plan_limits(raw, keep_base=False):
    """The plan's "limits" block -> {"lower", "upper", "ldcp", "from"}, or None when it is
    missing or does not make sense. keep_base (a swing plan): also psx-exit's "base" of
    computed limits ("file": the LDCP in the day's own closing file; "last close"). A day
    plan never gets it (as in v1)."""
    lim = raw.get("limits") if isinstance(raw, dict) else None
    if not isinstance(lim, dict):
        return None
    lo, hi = lim.get("lower"), lim.get("upper")
    if isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, (int, float)) \
            or not isinstance(hi, (int, float)) or not 0 < lo < hi or math.isinf(hi):
        return None
    out = {"lower": float(lo), "upper": float(hi), "ldcp": lim.get("ldcp"), "from": str(lim.get("from") or "plan")}
    if keep_base and lim.get("base") in ("file", "last close"):
        out["base"] = lim["base"]
    return out


def normalize_plan(raw, src="flags", cfg=None):
    """A plan dict (psx/plan.json shape) -> the checked plan the time machine runs. A plan
    with "style": "swing" is checked by normalize_swing_plan() (swing mode)."""
    if raw.get("style") == "swing":
        return normalize_swing_plan(raw, src, cfg)
    sym = str(raw.get("symbol") or "").strip().upper()
    if not re.match(r"^[A-Z0-9][A-Z0-9-]{0,14}$", sym):
        raise TMError("the plan needs a PSX symbol, got %r" % raw.get("symbol"))
    side = str(raw.get("side") or "buy").lower()
    if side != "buy":
        raise TMError("the plan must be a buy, not %r: this pack is long only (PSX bans blank sales on the "
                      "regular market)" % raw.get("side"))
    interval = str(raw.get("interval") or "5m")
    if interval != "5m":
        raise TMError("5-minute bars only (the plan says %s): the replays run on the PSX 5-minute grid. A "
                      "1-minute bars file is fine, it is turned into 5-minute bars" % interval)
    try:
        d = datetime.date.fromisoformat(str(raw.get("date") or ""))
    except ValueError:
        raise TMError("the plan needs a date like 2026-10-29, got %r" % raw.get("date"))
    kind, spans, segs = day_grid(d, cfg)
    if kind is None:
        raise TMError("PSX has no session on %s (%s)" % (day_label(d), trading_day(d, cfg)[1]))
    entry, stop, target = (_num(raw.get(k), k) for k in ("entry", "stop", "target"))
    if not stop < entry < target:
        raise TMError("a buy needs stop < entry < target (got stop %s, entry %s, target %s)" % (stop, entry, target))
    ends = [e for _, e in spans]
    k = None
    if raw.get("entry_time"):
        when = datetime.datetime.combine(d, parse_clock(raw["entry_time"], "entry_time"))
        if when in ends:
            k = ends.index(when)
        elif any(s < when < e for s, e in spans):
            raise TMError("entry_time %s is not on the 5-minute grid (use the bar's close, e.g. 10:15 AM)"
                          % raw["entry_time"])
        else:
            raise TMError(_between(d, spans, segs, clock_label(when)))
    if raw.get("entry_bar") is not None:
        try:
            kb = int(raw["entry_bar"])
        except (TypeError, ValueError):
            raise TMError("entry_bar must be a bar number, got %r" % raw["entry_bar"])
        if k is not None and kb != k:
            raise TMError("the plan disagrees with itself: entry_bar %d but entry_time %s is bar %d on %s"
                          % (kb, raw["entry_time"], k, day_label(d)))
        k = kb
    if k is None:
        raise TMError("the plan needs entry_time (the entry bar's close, e.g. 10:15 AM) or entry_bar")
    if not 0 <= k <= len(spans) - 2:
        raise TMError(_between(d, spans, segs, "bar %d" % k))
    orng = raw.get("opening_range") or {}
    or_min = orng.get("minutes", 15) if isinstance(orng, dict) else 15
    try:
        or_min = int(or_min)
        n_or = len(opening_range_slots(d, or_min, 5, cfg))
    except (TypeError, ValueError):
        raise TMError("opening_range.minutes must be a multiple of 5 (got %r)" % (or_min,))
    if n_or >= segs[0][1] or n_or * 5 != or_min:
        raise TMError("opening_range.minutes must be a multiple of 5, shorter than the first segment of %s "
                      "(got %s)" % (day_label(d), or_min))
    risk = entry - stop
    return {
        "symbol": sym, "side": "buy", "date": d.isoformat(), "session": kind, "setup": str(raw.get("setup") or "orb"),
        "entry": entry, "entry_time": clock_label(ends[k]), "entry_bar": k,
        "stop": stop, "target": target, "risk_per_share": round(risk, 2),
        "target_r": round((target - entry) / risk, 2),
        "opening_range": dict(orng, minutes=or_min) if isinstance(orng, dict) else {"minutes": or_min},
        "stop_rule": str(raw.get("stop_rule") or "auto"), "interval": "5m",
        "flat_by": raw.get("flat_by"), "limits": plan_limits(raw), "from": src,
        "costs": raw.get("costs") if isinstance(raw.get("costs"), dict) else None,
    }


def load_plan(a, now_utc, cfg=None):
    """The plan from --plan (psx/plan.json) or from the flags, checked and normalized."""
    if a.symbol:
        missing = [f for f in ("entry", "stop", "target", "entry_time") if getattr(a, f) is None]
        if missing:
            raise TMError("with --symbol also give %s" % ", ".join("--" + m.replace("_", "-") for m in missing))
        raw = {"symbol": a.symbol, "side": "buy", "date": a.date or to_pkt(now_utc).date().isoformat(),
               "entry": a.entry, "stop": a.stop, "target": a.target, "entry_time": a.entry_time,
               "setup": a.setup, "opening_range": {"minutes": a.or_minutes}, "stop_rule": a.stop_rule,
               "interval": "5m"}
        src = "flags"
    else:
        if not os.path.exists(a.plan):
            raise TMError("no plan at %s: run /psx-exit first, or pass --symbol --date --entry --stop "
                          "--target --entry-time" % a.plan)
        try:
            with open(a.plan, encoding="utf-8") as fh:
                raw = json.load(fh)
        except ValueError as e:
            raise TMError("%s is not valid JSON: %s" % (a.plan, e))
        if not isinstance(raw, dict):
            raise TMError("%s is not a plan" % a.plan)
        src = a.plan
    return normalize_plan(raw, src, cfg)


# ---------------------------------------------------------------- data: 5-minute bars

def _full(bars):
    return bool(bars) and all(b is not None for b in bars)


def _count(bars):
    return sum(1 for b in bars or [] if b is not None)


def compact(bars, d):
    """resample_bars() or bars_from_tape() items -> [{date, slot, start "HH:MM", o, h, l, c, v}]
    (a missing bar stays None)."""
    return [None if b is None else {"date": d.isoformat(), "slot": i, "start": hhmm(b["start"]), "o": b["open"],
                                    "h": b["high"], "l": b["low"], "c": b["close"], "v": b.get("volume")}
            for i, b in enumerate(bars)]


def bar_days(sym, root, plan_day, now_utc, cfg=None):
    """The stock's 5-minute bars per day up to the plan's day -> ({date: bars}, {date: "bars"
    or "tape"}). Each list is aligned with the day's slots (None where a bar is missing).

    bars/<SYM>.csv first (1-minute bars become 5-minute bars), then
    tape/YYYY-MM-DD/<SYM>.jsonl for a day the file does not hold in full. On the plan's
    day only what was there by --now counts: tape snapshots fetched by then, and bars
    that had closed by then."""
    now_pkt = to_pkt(now_utc)
    days, src = {}, {}

    def by_now(d, bars):
        if d != plan_day:
            return bars
        spans = slot_spans(d, 5, cfg)
        return [b if b is not None and spans[i][1] <= now_pkt else None for i, b in enumerate(bars)]

    path = os.path.join(root, "bars", sym + ".csv")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                found = read_bars_csv(fh.read())
        except (OSError, ValueError) as e:
            raise TMError("%s: %s" % (path, e))
        for d, bars in found.items():
            if d <= plan_day:
                days[d], src[d] = by_now(d, resample_bars(bars, d, 5, cfg)), "bars"
    folder = os.path.join(root, "tape")
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        try:
            d = datetime.date.fromisoformat(name)
        except ValueError:
            continue
        p = tape_path(root, d, sym)
        if d > plan_day or _full(days.get(d)) or not os.path.exists(p):
            continue
        snaps = read_tape(p)
        if d == plan_day:
            snaps = [s for s in snaps if (parse_utc(s.get("fetched")) or now_utc) <= now_utc]
        try:
            bars = by_now(d, bars_from_tape(snaps, d, sym, 5, cfg))
        except ValueError as e:
            raise TMError("%s: %s" % (p, e))
        if _count(bars) > _count(days.get(d)):
            days[d], src[d] = bars, "tape"
    return days, src


def split_pool(days, plan_day, kind, cfg=None):
    """Past days -> (pool, short, other). pool: the FULL sessions before the plan's day with
    the plan's hours, oldest first, as (date, compact bars). short: days with a gap (a bar
    missing). other: full sessions with other hours (a Friday for a Thursday plan)."""
    pool, short, other = [], [], []
    for d in sorted(days):
        bars = days[d]
        if d >= plan_day or not bars:      # no bars list: the calendar has no session that day
            continue
        if not _full(bars):
            short.append({"date": d.isoformat(), "bars": _count(bars), "of": len(bars)})
        elif session_kind(d, cfg) != kind:
            other.append({"date": d.isoformat(), "session": session_kind(d, cfg)})
        else:
            pool.append((d.isoformat(), compact(bars, d)))
    return pool, short, other


def bars_from_words(src, plan_day, sym):
    """Where the past sessions' bars came from, in words: "bars/DEMO.csv", "the DEMO tape"
    or both. "" when there are none."""
    kinds = set(v for d, v in src.items() if d < plan_day)
    words = []
    if "bars" in kinds:
        words.append("bars/%s.csv" % sym)
    if "tape" in kinds:
        words.append("the %s tape" % sym)
    return " and ".join(words)


# ---------------------------------------------------------------- data: daily bars and price limits

def daily_data(sym, root, plan_day, cfg=None, offline=False, n=DAILY_BARS):
    """The newest n sessions before the plan's day and the stock's daily bars in them, oldest
    first (recent_sessions + daily_bars: a day it did not trade, or with zero prices, is
    skipped) -> (sessions, bars)."""
    try:
        sessions = recent_sessions(plan_day, n, root, cfg, offline, include_end=False)
    except ValueError as e:
        raise TMError(str(e))
    except (urllib.error.URLError, OSError) as e:
        raise TMError("could not reach PSX for the closing files: %s" % (getattr(e, "reason", None) or e))
    return sessions, daily_bars(sym, sessions)


def too_few_daily(sym, root, plan_day, have, sessions, cfg=None, offline=False, what="daily mode"):
    """The plain refusal when daily mode (or what: "swing mode") has fewer than MIN_SESSIONS
    daily bars."""
    if len(sessions) >= DAILY_BARS:
        return ("only %d daily bar(s) of %s in the last %d sessions before %s (%s to %s): it had no regular-board "
                "trade on the other days. %s needs at least %d" % (have, sym, len(sessions), plan_day.isoformat(),
                                                                   sessions[0][0], sessions[-1][0],
                                                                   what[:1].upper() + what[1:], MIN_SESSIONS))
    head = ("only %d daily bar(s) of %s before %s in %s; %s needs at least %d"
            % (have, sym, plan_day.isoformat(), os.path.join(root, "mkt_summary"), what, MIN_SESSIONS))
    files = ("the PSX closing files of the last %d sessions (%s/download/mkt_summary/YYYY-MM-DD.Z) as %s"
             % (DAILY_BARS, PSX, os.path.join(root, "mkt_summary", "YYYY-MM-DD.Z")))
    if not site_on(cfg):
        return "%s. %s" % (head, SITE_OFF % files)
    if offline:
        return "%s (with --offline or --now it reads only the data folder): save %s" % (head, files)
    return "%s: PSX has no more closing files for %s" % (head, sym)


def day_limits(plan, root, cfg=None, daily=None):
    """The day's price limits -> {"lower", "upper", "ldcp", "from"}, or None when not known.
    The plan's own first (psx-exit copies the company page's CIRCUIT BREAKER). Else
    computed with price_limits() from the LDCP: the day's closing file when it is saved,
    else the last close before the day (an ex-date would lower it). Reads only the data
    folder: it never fetches."""
    if plan.get("limits"):
        return dict(plan["limits"])
    sym, d = plan["symbol"], datetime.date.fromisoformat(plan["date"])
    ldcp = None
    try:
        rows = daily_rows(d, root, cfg, True) or []
    except ValueError:
        rows = []
    row = next((r for r in rows if r.get("symbol") == sym), None)
    if row and row.get("ldcp"):
        ldcp, base = row["ldcp"], "file"
    else:
        bars = daily if daily is not None else daily_data(sym, root, d, cfg, True, 5)[1]
        if bars:
            ldcp, base = bars[-1]["close"], "last close"
    if not ldcp or ldcp <= 0:
        return None
    lower, upper = price_limits(ldcp)
    return {"lower": lower, "upper": upper, "ldcp": ldcp, "from": "computed", "base": base}


# ---------------------------------------------------------------- costs in R

def costs_used(plan, cfg):
    """(settings, words): the PSX costs the R counts. The plan's own "costs" block when it
    has one, else the "costs" block of psx/config.json, else the pack's defaults (COSTS).
    A block that cannot be used is one plain error."""
    if isinstance(plan.get("costs"), dict):
        settings, words, where = cost_settings({"costs": plan["costs"]}), "the plan's costs settings", "the plan"
    elif isinstance(cfg.get("costs"), dict):
        settings, words, where = cost_settings(cfg), "your costs settings", "psx/config.json"
    else:
        settings, words, where = cost_settings({}), "the pack's default costs", "COSTS"
    try:
        round_trip_cost(plan["entry"], plan["target"], 1000, settings)
    except ValueError as e:
        raise TMError('the "costs" block in %s: %s' % (where, e))
    return settings, words


def exit_cost_r(entry, price, risk, costs):
    """The round-trip costs of one trade, buy at the entry and sell at `price`, in R:
    round_trip_cost(entry, price, 1000) / 1000 / the risk a share. The size is not known
    yet, so 1,000 shares stand in (as in the plan's costs_per_share_est)."""
    return round_trip_cost(entry, price, 1000, costs) / 1000.0 / risk


# ---------------------------------------------------------------- the simulation: bars mode

def build_pool(sessions):
    """pool[i] = every past session's bar i as (o, h, l, c) / bar i-1's close, in date order.
    Every session has the same layout (the plan's hours), so bar i is the same time of day."""
    n = len(sessions[0][1]) if sessions else 0
    pool = [[] for _ in range(n)]
    for _, D in sessions:
        for i in range(1, n):
            pc = D[i - 1]["c"]
            pool[i].append((D[i]["o"] / pc, D[i]["h"] / pc, D[i]["l"] / pc, D[i]["c"] / pc))
    return pool


def pct(a, q):
    return a[min(len(a) - 1, int(q * len(a)))]


def draw_bounds(n, segs=None):
    """For each bar i, the (lowest, highest) slot a replay may draw for it: bar i's own
    segment, never bar 0 (it has no bar before it). The first bar after a break (the
    2:30 PM bar on a Friday, bar 33) holds the move over the break, so it draws only
    itself, and the other bars of its segment never draw it."""
    out = []
    for i in range(n):
        a, b = next(((a, b) for a, b in (segs or [(0, n)]) if a <= i < b), (0, n))
        if a > 0:
            out.append((a, a) if i == a else (a + 1, b - 1))
        else:
            out.append((1, b - 1))
    return out


def simulate(pool, entry, stop, target, k, last, replays=10000, seed=19, window=2, segs=None, limits=None,
             costs=None):
    """Monte Carlo replays of bars k+1..last (bars mode), with the US pack's draws and order:
    for bar i a slot j = randint(i - window, i + window), the window cut to draw_bounds()
    (so a slot at the edge is not drawn more often), then one past bar from pool[j]
    (random.choice), scaled to the price. limits = (lower, upper) keeps every price inside
    the day's price limits. costs = the costs settings the R counts (see summary)."""
    bounds = draw_bounds(len(pool), segs)
    dn, up = limits if limits else (0.0, float("inf"))

    def slot(rng, i):
        lo, hi = bounds[i]
        if window == 0 or lo == hi:
            return i
        return rng.randint(max(lo, i - window), min(hi, i + window))

    rng = random.Random(seed)
    choice = rng.choice
    outcomes, closes, paths = [], [], []
    for n in range(replays):
        p = entry
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            hi, lo, cl = min(max(p * h, dn), up), min(max(p * l, dn), up), min(max(p * c, dn), up)
            if outcome is None:
                if lo <= stop:
                    outcome = "stop"        # both inside one bar: count the loss (conservative)
                elif hi >= target:
                    outcome = "target"
            p = cl
            if keep:
                path.append(round(p, 2))
        outcomes.append(outcome or "close")
        closes.append(p)
        if keep:
            paths.append(path)
    # percentile bands per bar for the fan chart: a second run of 2,000 paths, same seed
    rng = random.Random(seed)
    choice = rng.choice
    allp = []
    for n in range(BAND_PATHS):
        p = entry
        path = [entry]
        for i in range(k + 1, last + 1):
            o, h, l, c = choice(pool[slot(rng, i)])
            p = min(max(p * c, dn), up)
            path.append(p)
        allp.append(path)
    return summary(entry, stop, target, outcomes, closes, paths, allp, replays, seed, window, last - k, costs)


def summary(entry, stop, target, outcomes, closes, paths, allp, replays, seed, window, bars_left, costs=None):
    """The numbers of one run: the four odds, the price at --until, the average R, the
    fan-chart bands (from allp), the sample paths and a histogram of the price at --until.
    With costs (a costs settings dict) each replay's R is less its own round-trip costs
    in R (exit_cost_r: the sale at the target, the stop or the price at --until); green
    stays by price alone. cost_R_stop and cost_R_target are the costs in R at the stop
    and at the target (0 without costs)."""
    risk = entry - stop
    r_target = round((target - entry) / risk, 4)
    seen = {}

    def cost(price):                # the costs in R of a sale at this price, on the 0.01 tick
        if costs is None:
            return 0.0
        key = round(price, 2)
        if key not in seen:
            seen[key] = exit_cost_r(entry, key, risk, costs)
        return seen[key]
    cnt = {x: outcomes.count(x) for x in ("target", "stop", "close")}
    green = sum(1 for o, p in zip(outcomes, closes) if o == "target" or (o == "close" and p > entry))
    gross = [r_target if o == "target" else -1.0 if o == "stop" else (p - entry) / risk
             for o, p in zip(outcomes, closes)]
    Rs = [g - cost(target if o == "target" else stop if o == "stop" else p)
          for g, o, p in zip(gross, outcomes, closes)]
    srt = sorted(closes)
    bands = []
    for j in range(bars_left + 1):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    lo_, hi_ = pct(srt, 0.01), pct(srt, 0.99)
    W = (hi_ - lo_) / HIST_BINS
    bins = [0] * HIST_BINS
    for c in srt:
        b = int((c - lo_) / W) if W > 0 else 0
        if 0 <= b < HIST_BINS:
            bins[b] += 1
    return {
        "replays": replays, "seed": seed, "window": window, "bars_left": bars_left,
        "p_target": round(cnt["target"] / replays, 4), "p_stop": round(cnt["stop"] / replays, 4),
        "p_neither": round(cnt["close"] / replays, 4), "p_green": round(green / replays, 4),
        "median_close": round(pct(srt, 0.5), 2), "p5_close": round(pct(srt, 0.05), 2),
        "p95_close": round(pct(srt, 0.95), 2), "avg_R": round(statistics.mean(Rs), 3),
        "avg_R_before_costs": round(statistics.mean(gross), 3), "cost_R_stop": round(cost(stop), 4),
        "cost_R_target": round(cost(target), 4),
        "r_at_target": r_target, "band_paths": BAND_PATHS, "bands": bands, "sample_paths": paths,
        "hist": {"lo": round(lo_, 2), "hi": round(hi_, 2), "bins": bins},
    }


# ---------------------------------------------------------------- the simulation: daily mode

def parkinson(bars):
    """sigma_day from daily bars: sqrt(mean(ln(high / low) ^ 2) / (4 ln 2)), the Parkinson
    estimate of a day's spread of log prices, from each day's high and low."""
    v = [math.log(b["high"] / b["low"]) ** 2 for b in bars]
    return math.sqrt(sum(v) / len(v) / (4.0 * math.log(2.0)))


def bridge(a, b, s, u1, u2):
    """(high, low) of a Brownian step from a to b (log prices; s = the step's sigma), from
    two uniforms u1, u2 in [0, 1): high = (a + b + sqrt((b - a)^2 - 2 s^2 ln U1)) / 2 and
    low = (a + b - sqrt((b - a)^2 - 2 s^2 ln U2)) / 2, with U = 1 - u in (0, 1]."""
    dd = (b - a) * (b - a)
    s2 = 2.0 * s * s
    return ((a + b + math.sqrt(dd - s2 * math.log(1.0 - u1))) / 2.0,
            (a + b - math.sqrt(dd - s2 * math.log(1.0 - u2))) / 2.0)


def simulate_daily(entry, stop, target, steps, sigma_step, replays=10000, seed=19, limits=None, costs=None):
    """Monte Carlo replays of `steps` five-minute steps from the entry (daily mode). Each step
    adds sigma_step x a normal draw to the log price (no drift). Its high and low come from
    the Brownian bridge between the step's two ends. Fixed draw order per step: two
    uniforms for the normal draw (Box-Muller), then U1 for the high and U2 for the low.
    The stop is checked before the target, so a step that touches both counts as the stop.
    limits = (lower, upper) keeps every price inside the day's price limits. costs = the
    costs settings the R counts (see summary)."""
    s = float(sigma_step)
    inf = float("inf")
    dn, up = (math.log(limits[0]), math.log(limits[1])) if limits else (-inf, inf)
    ls, lt, le = math.log(stop), math.log(target), math.log(entry)
    log, sqrt, cos, exp, turn = math.log, math.sqrt, math.cos, math.exp, 2.0 * math.pi

    def stepper(rnd):
        def step(a):
            u, v, u1, u2 = rnd(), rnd(), rnd(), rnd()
            b = a + s * sqrt(-2.0 * log(1.0 - u)) * cos(turn * v)
            hi, lo = bridge(a, b, s, u1, u2)
            return min(max(hi, dn), up), min(max(lo, dn), up), min(max(b, dn), up)
        return step

    step = stepper(random.Random(seed).random)
    outcomes, closes, paths = [], [], []
    for n in range(replays):
        x = le
        outcome = None
        keep = n < SAMPLE_PATHS
        path = [entry]
        for _ in range(steps):
            hi, lo, x = step(x)
            if outcome is None:
                if lo <= ls:
                    outcome = "stop"        # both inside one step: count the loss (conservative)
                elif hi >= lt:
                    outcome = "target"
            if keep:
                path.append(round(exp(x), 2))
        outcomes.append(outcome or "close")
        closes.append(exp(x))
        if keep:
            paths.append(path)
    step = stepper(random.Random(seed).random)     # the fan chart: 2,000 paths, same seed
    allp = []
    for n in range(BAND_PATHS):
        x = le
        path = [entry]
        for _ in range(steps):
            x = step(x)[2]
            path.append(exp(x))
        allp.append(path)
    return summary(entry, stop, target, outcomes, closes, paths, allp, replays, seed, None, steps, costs)


# ---------------------------------------------------------------- the history

def opening_range(D, n):
    """(high, low) of the first n bars of a session (a list of compact bars)."""
    rows = [D[i] for i in range(n)]
    return max(b["h"] for b in rows), min(b["l"] for b in rows)


def rule_stop(rule, oh, ol, entry):
    """The stop of the history's rule, rounded down to the 0.01 tick (away from the entry)."""
    r = rule["stop_rule"]
    if r == "or_mid":
        return round_down_tick((oh + ol) / 2)
    if r == "or_low":
        return round_down_tick(ol)
    return round_down_tick(entry * (1 - rule["stop_pct"] / 100.0))


def no_entry_bars(d, ends, segs, cfg):
    """Bar numbers of date d that cannot start a trade, the rule psx-entry uses: a bar that
    closes when a session ends (12:00 on a Friday, or the close: an order cannot fill then),
    and a bar that closes before no_entries_before or after no_entries_after (config_time,
    so they move with the day's hours; null in the config turns that side off)."""
    c = cfg if isinstance(cfg, dict) else {}
    out = set(j - 1 for _, j in segs if j > 0)
    bounds = []
    for key, default in (("no_entries_before", "09:45"), ("no_entries_after", "15:00")):
        v = c.get(key, default)
        try:
            bounds.append(config_time(parse_hhmm(v, key), d, c) if v else None)
        except ValueError as e:
            raise TMError("%s in psx/config.json: %s" % (key, e))
    before, after = bounds
    for i, e in enumerate(ends):
        if (before and e.time() < before) or (after and e.time() > after):
            out.add(i)
    return out


def history_replay(sessions, rule, last, ends, costs=None, no_entry=None):
    """Bars mode: the plan's rule on every past session of the pool. Entry = the first bar
    after the opening range that closes above it (up to the --until bar), skipping the bars
    in no_entry (no_entry_bars: a session end, or outside the entry window, as psx-entry
    skips them), then stop first,
    then target, bar by bar, else the close at --until. ends = the bar close times of the
    plan's day (every session in the pool has the same ones). With costs (a costs settings
    dict) each trade's R is less its own round-trip costs in R (exit_cost_r, with that
    day's own entry and risk); green stays by price alone (R_before_costs above 0)."""
    n = rule["or_minutes"] // 5
    skip = no_entry or set()
    trades = []
    for d, D in sessions:
        oh, ol = opening_range(D, n)
        kk = next((i for i in range(n, last + 1) if i not in skip and D[i]["c"] > oh), None)
        if kk is None:
            continue
        e = round_half_up_tick(D[kk]["c"])
        s = rule_stop(rule, oh, ol, e)
        risk = e - s
        if risk <= 0:
            continue
        t = round_half_up_tick(e + rule["target_r"] * risk)
        oc = None
        for b in D[kk + 1:last + 1]:
            if b["l"] <= s:
                oc = "stop"
                break
            if b["h"] >= t:
                oc = "target"
                break
        cl = D[last]["c"]
        R = (t - e) / risk if oc == "target" else -1.0 if oc == "stop" else (cl - e) / risk
        sold = t if oc == "target" else s if oc == "stop" else cl
        c = exit_cost_r(e, sold, risk, costs) if costs is not None else 0.0
        trades.append({"date": d, "time": clock_label(ends[kk]), "entry": e, "stop": s, "target": t,
                       "outcome": oc or "close", "R": round(R - c, 2), "R_before_costs": round(R, 2),
                       "cost_R": round(c, 2)})
    return {
        "mode": "bars", "sessions": len(sessions), "triggered": len(trades),
        "target": sum(1 for x in trades if x["outcome"] == "target"),
        "stop": sum(1 for x in trades if x["outcome"] == "stop"),
        "close": sum(1 for x in trades if x["outcome"] == "close"),
        "green": sum(1 for x in trades if x["R_before_costs"] > 0),
        "avg_R": round(statistics.mean(x["R"] for x in trades), 3) if trades else None,
        "avg_R_before_costs": round(statistics.mean(x["R_before_costs"] for x in trades), 3) if trades else None,
        "rule": rule, "trades": trades,
    }


def daily_history(bars, entry, stop, target):
    """Daily mode: the plan's stop and target as percents of its entry, put on each past day
    from that day's open (the stop rounded down, the target half up, to the 0.01 tick).
    Counts the days that reached the target only, the stop only, both (the order inside
    a day is not known) and neither."""
    sp, tp = (entry - stop) / entry, (target - entry) / entry
    out = {"mode": "daily", "days": len(bars), "target_only": 0, "stop_only": 0, "both": 0, "neither": 0,
           "rule": {"from": "open", "stop_pct": round(100 * sp, 4), "target_pct": round(100 * tp, 4)}}
    for b in bars:
        hit_s = b["low"] <= round_down_tick(b["open"] * (1 - sp))
        hit_t = b["high"] >= round_half_up_tick(b["open"] * (1 + tp))
        out["both" if hit_s and hit_t else "stop_only" if hit_s else "target_only" if hit_t else "neither"] += 1
    return out


def resolve_rule(plan, today):
    """The rule the bars-mode history uses: orb, the plan's opening range, its stop rule
    (or_mid, or_low, or "pct" = the plan's stop distance as a percent of the entry), and
    the plan's target R. "auto" works the stop rule out from the plan day's own opening
    range (its bars, else the plan's opening_range high and low). None when not orb."""
    if plan["setup"].lower() != "orb":
        return None
    n = plan["opening_range"]["minutes"] // 5
    rule = {"setup": "orb", "or_minutes": plan["opening_range"]["minutes"], "stop_rule": plan["stop_rule"],
            "target_r": plan["target_r"], "stop_pct": round(100.0 * plan["risk_per_share"] / plan["entry"], 4)}
    if rule["stop_rule"] == "auto":
        rule["stop_rule"] = "pct"
        orng = plan["opening_range"]
        oh = ol = None
        if today and all(today[i] is not None for i in range(n)):
            oh, ol = opening_range(today, n)
        elif isinstance(orng.get("high"), (int, float)) and isinstance(orng.get("low"), (int, float)):
            oh, ol = float(orng["high"]), float(orng["low"])
        if oh is not None and abs(round_down_tick((oh + ol) / 2) - plan["stop"]) < 0.005:
            rule["stop_rule"] = "or_mid"
        elif oh is not None and abs(round_down_tick(ol) - plan["stop"]) < 0.005:
            rule["stop_rule"] = "or_low"
    elif rule["stop_rule"] not in ("or_mid", "or_low"):
        rule["stop_rule"] = "pct"
    if rule["stop_rule"] != "pct":
        del rule["stop_pct"]
    return rule


# ---------------------------------------------------------------- --until and the verdict

def until_bar(text, d, spans, segs):
    """--until -> the number of the last bar to replay. None -> the day's close."""
    ends = [e for _, e in spans]
    if text is None:
        return len(ends) - 1
    t = parse_clock(text, "--until")
    when = datetime.datetime.combine(d, t)
    if when in ends:
        return ends.index(when)
    if when > ends[-1]:
        raise TMError("--until %s is after the close: the session on %s ends at %s"
                      % (clock_label(t), day_label(d), clock_label(ends[-1])))
    for (a, b), (c, _) in zip(segs, segs[1:]):
        if ends[b - 1] < when <= spans[c][0]:
            raise TMError("--until %s is in the Juma break: use %s, or a time from %s"
                          % (clock_label(t), clock_label(ends[b - 1]), clock_label(ends[c])))
    if when < ends[0]:
        raise TMError("--until %s is before the first bar closes (%s)" % (clock_label(t), clock_label(ends[0])))
    raise TMError("--until must be a bar close on the 5-minute grid, like 3:20 PM (got %s)" % text)


def flat_by_bar(text, d, spans):
    """The plan's flat_by (psx-exit has already moved it to the day's hours) -> the number
    of the last bar that has closed by then. A time in the Juma break gives the 12:00 bar,
    a time after the close the last bar."""
    t = parse_clock(text, "the plan's flat_by")
    ends = [e for _, e in spans]
    i = bisect.bisect_right(ends, datetime.datetime.combine(d, t)) - 1
    if i < 0:
        raise TMError("the plan's flat_by %s is before the first bar closes (%s)" % (clock_label(t),
                                                                                   clock_label(ends[0])))
    return i


UNTIL_WORDS = {"until": "your --until time", "flat_by": "the plan's flat-by time", "close": "the close"}


def _until12(mc):
    t = mc.get("until")
    return clock_label(datetime.datetime.strptime(t, "%H:%M")) if t else "the close"


def lean(mc):
    """(gap, share, break_even): the target's share of the replays that hit one side,
    minus the share that breaks even at the target's R with the costs: (1 + c) / (1 + R),
    c = the costs in R of a trade that ends at the stop (mc's cost_R_stop, 0 when it has
    none). None when nothing hit a side."""
    pt, ps = mc["p_target"], mc["p_stop"]
    if pt + ps <= 0:
        return None
    r = mc.get("r_at_target") or 1.0
    c = mc.get("cost_R_stop") or 0.0
    share, even = pt / (pt + ps), (1.0 + c) / (1.0 + r)
    return share - even, share, even


def verdict(mc):
    """One line from the numbers, see the docstring for the thresholds."""
    pt, ps = mc["p_target"], mc["p_stop"]
    lv = lean(mc)
    if lv is None:
        return "No replay reached the target or the stop by %s: the price at %s decides every one." % (
            _until12(mc), _until12(mc))
    d, share, even = lv
    r = mc.get("r_at_target") or 1.0
    c = mc.get("cost_R_stop") or 0.0
    one_r = abs(r - 1.0) < 0.005 and not c > 0
    odds = "target first %.1f %% vs stop first %.1f %%" % (100 * pt, 100 * ps)
    if even >= 1:
        return ("Costs eat the target: about %.2fR a trade is as much as the %gR target, so the plan loses even "
                "when the target comes first (%s)." % (c, round(r, 2), odds))
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    if one_r:
        return "%s: %s." % (head, odds)
    return ("%s: at %gR%s the target has to come first in %.0f %% of the replays that hit one side; "
            "it did in %.0f %% (%s)." % (head, round(r, 2), " after costs" if c > 0 else "", 100 * even,
                                         100 * share, odds))


def verdict_notes(mc):
    """The lines printed right below the verdict, so nothing reads as one-sided."""
    notes = []
    pn = mc.get("p_neither", 0)
    if pn > MANY_NEITHER and lean(mc) is not None:
        notes.append("%.1f %% of the replays hit neither by %s; the price at %s decides those."
                     % (100 * pn, _until12(mc), _until12(mc)))
    lv = lean(mc)
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg is not None and avg != 0 and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the replays that hit neither pull it the other way." % avg)
    return notes


# ---------------------------------------------------------------- the run

def mode_reason(mode, used, n_pool, where, kind, sym, today_from=""):
    """Why this mode, in one plain sentence. today_from: where the plan day's own bars came
    from, when there are no past ones."""
    kw = KIND_WORDS[kind]
    if where:
        have = ("there %s %d full %s session%s of %s's 5-minute bars in %s before this day"
                % ("is" if n_pool == 1 else "are", n_pool, kw, "" if n_pool == 1 else "s", sym, where))
    elif today_from:
        have = "there are no past sessions of %s's 5-minute bars in %s (only the plan's day)" % (sym, today_from)
    else:
        have = "there are no 5-minute bars of %s in the data folder (bars/%s.csv or the tape)" % (sym, sym)
    if mode != "auto":
        return "You asked for it (--mode %s). T%s." % (used, have[1:])
    if used == "bars":
        return "T%s (%d needed)." % (have[1:], MIN_SESSIONS)
    return "T%s; bars mode needs %d full %s sessions." % (have[1:], MIN_SESSIONS, kw)


def time_machine(plan, root, now_utc, cfg=None, offline=False, replays=10000, seed=19, window=2, until=None,
                 mode="auto", sessions=None):
    """A checked plan + the data folder -> the timemachine.json document. A swing plan
    ("style": "swing") runs swing mode (swing_machine); sessions is for it only."""
    cfg = cfg if isinstance(cfg, dict) else {}
    if not 1 <= replays <= 1000000:
        raise TMError("--replays must be between 1 and 1,000,000")
    if not 0 <= window <= 10:
        raise TMError("--window must be between 0 and 10 bars")
    if mode not in ("auto", "bars", "daily"):
        raise TMError("--mode must be auto, bars or daily")
    if plan.get("style") == "swing":
        return swing_machine(plan, root, now_utc, cfg, offline, replays, seed, mode, until, sessions)
    if sessions is not None:
        raise TMError("--sessions is for a swing plan: a day plan runs to its flat-by time (or --until)")
    sym, k = plan["symbol"], plan["entry_bar"]
    d = datetime.date.fromisoformat(plan["date"])
    kind, spans, segs = day_grid(d, cfg)
    ends = [e for _, e in spans]
    now_pkt = to_pkt(now_utc)
    if now_pkt < ends[k]:
        raise TMError("the entry has not happened yet: the %s bar (bar %d) closes at %s on %s, and it is %s %s "
                      "Karachi time" % (hhmm(spans[k][0]), k, clock_label(ends[k]), day_label(d),
                                        day_label(now_pkt), clock_label(now_pkt)))
    if until is not None:
        last, until_from = until_bar(until, d, spans, segs), "until"
    elif plan.get("flat_by"):
        last, until_from = flat_by_bar(plan["flat_by"], d, spans), "flat_by"
    else:
        last, until_from = len(ends) - 1, "close"
    if last <= k:
        raise TMError("%s leaves nothing to replay after the %s entry" % (
            "--until %s" % clock_label(ends[last]) if until_from == "until" else "the plan's flat_by %s"
            % plan["flat_by"], plan["entry_time"]))
    costs, cost_words = costs_used(plan, cfg)
    days, src = bar_days(sym, root, d, now_utc, cfg)
    pool, short, other = split_pool(days, d, kind, cfg)
    where = bars_from_words(src, d, sym)
    today_from = "" if where or d not in src else "bars/%s.csv" % sym if src[d] == "bars" else "the %s tape" % sym
    used = mode if mode != "auto" else ("bars" if len(pool) >= MIN_SESSIONS else "daily")
    if used == "bars" and len(pool) < MIN_SESSIONS:
        raise TMError("only %d full %s session(s) of %s's 5-minute bars before %s in %s; bars mode needs at "
                      "least %d (or use --mode daily)" % (len(pool), KIND_WORDS[kind], sym, d.isoformat(), root,
                                                          MIN_SESSIONS))
    warn = []
    today = compact(days.get(d) or [], d)
    eb = today[k] if k < len(today) else None
    if eb is not None and not eb["l"] - 0.01 <= plan["entry"] <= eb["h"] + 0.01:
        warn.append("the plan's entry %.2f is outside that bar's range %.2f to %.2f"
                    % (plan["entry"], eb["l"], eb["h"]))
    sessions, dbars = daily_data(sym, root, d, cfg, offline) if used == "daily" else (None, None)
    if dbars is not None and len(dbars) < MIN_SESSIONS:
        raise TMError(too_few_daily(sym, root, d, len(dbars), sessions, cfg, offline))
    try:
        limits = day_limits(plan, root, cfg, dbars)
    except ValueError as e:
        raise TMError(str(e))
    if limits and not limits["lower"] <= plan["entry"] <= limits["upper"]:
        warn.append("the plan's entry %.2f is outside the day's price limits %.2f to %.2f, so the replays do not "
                    "use them" % (plan["entry"], limits["lower"], limits["upper"]))
        limits = dict(limits, used=False)
    elif limits:
        limits = dict(limits, used=True)
    else:
        warn.append("the day's price limits are not known (no limits in the plan and no closing file), so the "
                    "replays are not kept inside them")
    caps = (limits["lower"], limits["upper"]) if limits and limits["used"] else None
    note = calendar_note(d, cfg)
    if note:
        warn.append(note.rstrip("."))
    plan_out = dict((kk, v) for kk, v in plan.items() if v is not None)
    data = {"symbol": sym, "folder": root, "site": used == "daily" and site_on(cfg) and not offline, "limits": limits,
            "short_sessions_left_out": short, "other_hours_left_out": other}
    if used == "bars":
        mc = simulate(build_pool(pool), plan["entry"], plan["stop"], plan["target"], k, last, replays, seed,
                      window, segs, caps, costs)
        mc.update(pool_sessions=len(pool), pool_from=pool[0][0], pool_to=pool[-1][0])
        data.update(interval="5m", bars_from=where, sessions=len(pool))
        rule = resolve_rule(plan, today)
        if rule:
            plan_out["stop_rule"] = rule["stop_rule"]
            history = history_replay(pool, rule, last, ends, costs, no_entry_bars(d, ends, segs, cfg))
        else:
            history = {"mode": "bars", "sessions": len(pool), "triggered": 0, "rule": None, "trades": [],
                       "note": "skipped: the history replay knows the opening-range breakout (orb) only, "
                               "this plan's setup is %r" % plan["setup"]}
        about = "%s's own last %d full %s sessions" % (sym, len(pool), KIND_WORDS[kind])
    else:
        sigma = parkinson(dbars)
        if not sigma > 0:
            raise TMError("%s's daily bars show no range (high = low on every day), so daily mode cannot size a "
                          "move" % sym)
        per = sigma / math.sqrt(len(spans))
        mc = simulate_daily(plan["entry"], plan["stop"], plan["target"], last - k, per, replays, seed, caps, costs)
        mc.update(pool_sessions=len(dbars), pool_from=dbars[0]["date"].isoformat(),
                  pool_to=dbars[-1]["date"].isoformat(), sigma_day=round(sigma, 6), sigma_step=round(per, 6),
                  steps_per_session=len(spans))
        data.update(interval="1d", daily_bars=len(dbars), bars_from=where, sessions=len(pool))
        history = daily_history(dbars, plan["entry"], plan["stop"], plan["target"])
        about = "%s's last %d daily bars" % (sym, len(dbars))
    mc.update(until=hhmm(ends[last]), until_from=until_from, band_times=[hhmm(ends[j]) for j in range(k, last + 1)])
    return {
        "created_by": "psx-timemachine",
        "created_at": utc_iso(now_utc),
        "mode": used,
        "mode_why": mode_reason(mode, used, len(pool), where, kind, sym, today_from),
        "data": data,
        "plan": plan_out,
        "costs": {"from": cost_words, "settings": costs},
        "mc": mc,
        "history": history,
        "verdict": verdict(mc),
        "verdict_notes": verdict_notes(mc),
        "warnings": warn,
        "note": ("A simulation built from %s. It is not a prediction: results, news and halts can do things "
                 "those days never did." % about),
    }


# ---------------------------------------------------------------- output

def pc(x):
    return "%.1f %%" % (100.0 * x)


def limits_line(lim):
    """One line about the day's price limits, or None when they are not used."""
    if not lim or not lim.get("used"):
        return None
    if lim.get("from") == "computed":
        how = "computed"
        if isinstance(lim.get("ldcp"), (int, float)) and not isinstance(lim.get("ldcp"), bool):
            how = ("computed from the LDCP %.2f in the day's closing file" % lim["ldcp"] if lim.get("base") == "file"
                   else "computed from the last close %.2f; an ex-date would lower them" % lim["ldcp"])
    else:
        how = "from the %s" % lim.get("from", "plan")
    return "Price limits for the day: %.2f to %.2f (%s). No replay goes past them." % (lim["lower"], lim["upper"], how)


def report(doc):
    if doc.get("mode") == "swing":
        return swing_report(doc)
    p, m, h = doc["plan"], doc["mc"], doc["history"]
    daily = doc["mode"] == "daily"
    until = _until12(m)
    kind = KIND_WORDS.get(p.get("session"), "")
    lines = [
        "%s buy, %s: entry %.2f at %s (bar %d), stop %.2f, target %.2f (%sR, risk %.2f a share)."
        % (p["symbol"], day_label(datetime.date.fromisoformat(p["date"])), p["entry"], p["entry_time"],
           p["entry_bar"], p["stop"], p["target"], ("%g" % p["target_r"]), p["risk_per_share"]),
        "Mode: %s. %s" % (doc["mode"], doc["mode_why"]),
        "%s replays of the rest of the session, %s to %s%s (%d five-minute %s), seed %d."
        % ("{:,}".format(m["replays"]), p["entry_time"], until,
           ", " + UNTIL_WORDS[m["until_from"]] if m.get("until_from") in UNTIL_WORDS else "", m["bars_left"],
           "steps" if daily else "bars", m["seed"]),
    ]
    if daily:
        lines += [
            "Daily mode is a simpler model: each step is a random move, not a real bar.",
            "The moves are sized from %s's last %d daily bars, %s to %s." % (p["symbol"], m["pool_sessions"],
                                                                              m["pool_from"], m["pool_to"]),
            "sigma_day %.2f %% (from the daily highs and lows), so %.3f %% a step." % (100 * m["sigma_day"],
                                                                                  100 * m["sigma_step"]),
            "A tape or a bars file with 20 or more %s sessions gives %s's own 5-minute bars instead (bars mode)."
            % (kind, p["symbol"]),
        ]
    else:
        lines += ["Each bar is drawn whole from %s's own 5-minute bars at the same time of day (+-%d bars)."
                  % (p["symbol"], m["window"]),
                  "The pool: its last %d full %s sessions, %s to %s." % (m["pool_sessions"], kind, m["pool_from"],
                                                                         m["pool_to"])]
    lim = limits_line(doc["data"].get("limits"))
    if lim:
        lines.append(lim)
    if doc.get("costs"):
        c = m["cost_R_stop"]
        lines.append("Costs: %s a trade (%s), counted in the R and the verdict."
                     % ("about %.2fR" % c if c >= 0.005 else "under 0.01R", doc["costs"]["from"]))
    lines += [
        "",
        "  target first   %s" % pc(m["p_target"]),
        "  stop first     %s" % pc(m["p_stop"]),
        "  neither        %-8s still open at %s" % (pc(m["p_neither"]), until),
        "  green          %-8s target first, or above the entry at %s" % (pc(m["p_green"]), until),
        "",
        "  Price at %s: median %.2f, 90 %% of replays between %.2f and %.2f."
        % (until, m["median_close"], m["p5_close"], m["p95_close"]),
        "  Average R: %+.3f, costs included (R = the %.2f risk: %+.2fR at the target, %+.2fR at the stop)."
        % (m["avg_R"], p["risk_per_share"], m["r_at_target"] - m.get("cost_R_target", 0),
           -1 - m.get("cost_R_stop", 0)),
        "",
    ]
    if daily:
        ru = h["rule"]
        lines += ["History: on each of those %d days, the stop %.2f %% under the open and the target %.2f %% over it:"
                  % (h["days"], ru["stop_pct"], ru["target_pct"]),
                  "  target only %d, stop only %d, both %d, neither %d." % (h["target_only"], h["stop_only"],
                                                                            h["both"], h["neither"]),
                  "  On a day with both, the order inside the day is not known."]
    elif h.get("rule"):
        ru = h["rule"]
        stop_words = {"or_mid": "opening-range midpoint", "or_low": "opening-range low"}.get(
            ru["stop_rule"], "%.2f %% under the entry" % ru.get("stop_pct", 0))
        if h["triggered"]:
            lines += ["History: the same rule (%d-min opening range, stop at the %s, %gR target)"
                      % (ru["or_minutes"], stop_words, ru["target_r"]),
                      "  on each of those %d real sessions fired %d times: %d target, %d stop, %d neither. "
                      "Average R %+.3f, costs included." % (h["sessions"], h["triggered"], h["target"], h["stop"],
                                                            h["close"], h["avg_R"])]
            cr = [t["cost_R"] for t in h.get("trades") or [] if isinstance(t.get("cost_R"), (int, float))]
            if cr and max(cr) > 0:
                lines.append("  Costs: %s a trade on those days (the closer the stop, the more R the costs are)."
                             % ("%.2fR" % cr[0] if min(cr) == max(cr) else "%.2fR to %.2fR" % (min(cr), max(cr))))
        else:
            lines.append("History: the same rule never fired in those %d sessions." % h["sessions"])
    else:
        lines.append("History: " + h.get("note", "skipped."))
    lines += ["", doc["verdict"]] + list(doc.get("verdict_notes") or []) + [""]
    for w in doc.get("warnings") or []:
        lines.append("Note: " + w + ".")
    data = doc["data"]
    if not daily and data.get("short_sessions_left_out"):
        gaps = data["short_sessions_left_out"]
        lines.append("Left out %d short session(s) (a bar is missing): %s."
                     % (len(gaps), ", ".join(x["date"] for x in gaps)))
    if not daily and data.get("other_hours_left_out"):
        kinds = sorted(set(KIND_WORDS.get(x["session"], x["session"]) for x in data["other_hours_left_out"]))
        n = len(data["other_hours_left_out"])
        lines.append("Left out %d session%s with other hours (%s)." % (n, "" if n == 1 else "s", ", ".join(kinds)))
    head, _, rest = doc["note"].partition(" It is ")      # one sentence a line
    lines += [head] + (["It is " + rest] if rest else [])
    lines.append("Same seed, same numbers: repeat this run with --seed %d." % m["seed"])
    return lines


def render_chart(doc, height=17):
    """ASCII fan chart of the bands: one column per 5-minute bar (swing mode: one a session,
    render_swing_chart)."""
    if doc.get("mode") == "swing":
        return render_swing_chart(doc, height)
    p, m = doc["plan"], doc["mc"]
    bands, times = m["bands"], m["band_times"]
    lo = min([b[0] for b in bands] + [p["stop"], p["target"]])
    hi = max([b[4] for b in bands] + [p["stop"], p["target"]])
    pad = (hi - lo) * 0.04 or 0.01
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / (height - 1)

    def row(price):
        return max(0, min(height - 1, int(round((hi - price) / step))))
    grid = [[" "] * len(bands) for _ in range(height)]
    for j, b in enumerate(bands):
        r5, r25, r50, r75, r95 = [row(x) for x in b]
        for r in range(r95, r5 + 1):
            grid[r][j] = "."
        for r in range(r75, r25 + 1):
            grid[r][j] = ":"
        grid[r50][j] = "*"
    marks = {row(p["target"]): "target", row(p["stop"]): "stop"}
    if row(p["entry"]) not in marks:
        marks[row(p["entry"])] = "entry"
    for r in (row(p["target"]), row(p["stop"])):
        grid[r] = ["-" if ch == " " else ch for ch in grid[r]]
    out = ["%s %s, %s replay paths from the %s entry (seed %d, %s mode)" % (
        p["symbol"], p["date"], "{:,}".format(m.get("band_paths", BAND_PATHS)), p["entry_time"], m["seed"],
        doc.get("mode", "bars"))]
    for r in range(height):
        label = marks.get(r)
        price = {"target": p["target"], "stop": p["stop"], "entry": p["entry"]}.get(label, hi - r * step)
        left = ("%9.2f |" % price) if (label or r in (0, height - 1)) else "          |"
        out.append(left + "".join(grid[r]) + ("  " + label if label else ""))
    axis = [" "] * len(bands)
    for j in sorted({0, len(bands) // 2, len(bands) - 1}):
        t = times[j]
        hh, mm = int(t[:2]), t[3:]
        lab = "%d:%s" % (hh % 12 or 12, mm)
        s = max(0, min(j, len(bands) - len(lab)))
        axis[s:s + len(lab)] = list(lab)
    out.append("          +" + "-" * len(bands))
    out.append("           " + "".join(axis))
    out.append("* median   : middle half of the paths   . 90 % of the paths   - stop and target")
    return out


# ---------------------------------------------------------------- swing mode: the plan, the limits, the pool

def _whole(v, what, lo, hi):
    """A whole number from lo to hi (a whole float such as 20.0 is fine), else one plain error."""
    ok = isinstance(v, int) and not isinstance(v, bool)
    if isinstance(v, float) and v == v and not math.isinf(v) and v == int(v):
        v, ok = int(v), True
    if not ok or not lo <= v <= hi:
        raise TMError("%s must be a whole number from %d to %d, got %r" % (what, lo, hi, v))
    return v


def _sessions_words(n):
    """"the next session" or "the next 20 sessions"."""
    return "the next session" if n == 1 else "the next %d sessions" % n


def normalize_swing_plan(raw, src="flags", cfg=None):
    """A swing plan (psx/plan.json with "style": "swing", from psx-exit) -> the checked plan
    swing mode runs. max_hold_sessions and trend_sessions come from the plan, else from the
    config's "swing" block (swing_settings: 20 and 10)."""
    sym = str(raw.get("symbol") or "").strip().upper()
    if not re.match(r"^[A-Z0-9][A-Z0-9-]{0,14}$", sym):
        raise TMError("the plan needs a PSX symbol, got %r" % raw.get("symbol"))
    side = str(raw.get("side") or "buy").lower()
    if side != "buy":
        raise TMError("the plan must be a buy, not %r: this pack is long only (PSX bans blank sales on the "
                      "regular market)" % raw.get("side"))
    try:
        d = datetime.date.fromisoformat(str(raw.get("date") or ""))
    except ValueError:
        raise TMError("the plan needs a date like 2026-10-29, got %r" % raw.get("date"))
    try:
        is_open, why = trading_day(d, cfg)
        kind = session_kind(d, cfg)
    except ValueError as e:
        raise TMError(str(e))
    if not is_open:
        raise TMError("PSX has no session on %s (%s)" % (day_label(d), why))
    entry, stop, target = (_num(raw.get(k), k) for k in ("entry", "stop", "target"))
    if not stop < entry < target:
        raise TMError("a buy needs stop < entry < target (got stop %s, entry %s, target %s)" % (stop, entry, target))
    st = swing_settings(cfg)
    hold, trend = raw.get("max_hold_sessions"), raw.get("trend_sessions")
    hold_from = "plan" if hold is not None else "config"
    block = '"%s" in the "swing" block of psx/config.json'
    hold = (_whole(hold, "the plan's max_hold_sessions", 1, MAX_SESSIONS) if hold is not None else
            _whole(st.get("max_hold_sessions"), block % "max_hold_sessions", 1, MAX_SESSIONS))
    trend = (_whole(trend, "the plan's trend_sessions", 2, MAX_TREND) if trend is not None else
             _whole(st.get("trend_exit_sessions"), block % "trend_exit_sessions", 2, MAX_TREND))
    exit_by = raw.get("exit_by")
    if exit_by is not None:
        try:
            exit_by = datetime.date.fromisoformat(str(exit_by)).isoformat()
        except ValueError:
            raise TMError("the plan's exit_by wants a date like 2026-11-27, got %r" % (exit_by,))
    risk = entry - stop
    return {
        "style": "swing", "symbol": sym, "side": "buy", "date": d.isoformat(), "session": kind,
        "setup": str(raw.get("setup") or "swing"), "entry": entry, "entry_rule": raw.get("entry_rule"),
        "stop": stop, "stop_rule": raw.get("stop_rule"), "stop_label": raw.get("stop_label"),
        "target": target, "target_label": raw.get("target_label"), "risk_per_share": round(risk, 2),
        "target_r": round((target - entry) / risk, 2), "max_hold_sessions": hold, "hold_from": hold_from,
        "exit_by": exit_by, "exit_by_time": raw.get("exit_by_time"), "trend_sessions": trend,
        "trend_level": raw.get("trend_level"), "last_close": raw.get("last_close"),
        "last_close_date": raw.get("last_close_date"), "interval": "1d", "limits": plan_limits(raw, True),
        "from": src,
    }


def float_limits(p):
    """price_limits() in floats, for the swing replays' inner loop: (lower, upper) from the
    close before, p: p x 0.90 and x 1.10 (p - 1.00 and + 1.00 when 10% of p is under Rs 1),
    rounded half up to 0.01, the lower never under 0.01. The same numbers as price_limits()
    on every 0.01 tick (the tests check it)."""
    if p * 0.1 < 1.0:
        lo, hi = p - 1.0, p + 1.0
    else:
        lo, hi = p * 0.9, p * 1.1
    lo = math.floor(lo * 100.0 + 0.5 + 1e-7) / 100.0
    hi = math.floor(hi * 100.0 + 0.5 + 1e-7) / 100.0
    return (lo if lo > 0.01 else 0.01), hi


def buy_day_ldcp(lim):
    """The buy day's own LDCP from a swing plan's limits (plan_limits with keep_base), or
    None. psx-exit copies it from the buy day's company page (or a tape snapshot of it):
    "from": "company page"; or from the buy day's own closing file: "from": "computed",
    "base": "file". On an ex-date PSX has lowered it there. Limits computed from the last
    close hold no new LDCP. An LDCP that is not a price inside its own limits is not used."""
    v = lim.get("ldcp") if isinstance(lim, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lim["lower"] <= v <= lim["upper"]:
        return None
    if lim.get("from") == "company page" or (lim.get("from") == "computed" and lim.get("base") == "file"):
        return float(v)
    return None


def swing_costs(cfg, plan):
    """(settings, words): the PSX costs a swing replay counts: trade_costs() of the config in
    swing style, so the commission counts on both sides (a sale on a later day always pays
    it), whatever "day_trade_one_side" says. A block that cannot be used is one plain error."""
    mine = isinstance(cfg.get("costs"), dict)
    settings = trade_costs(dict(cfg, style="swing"))
    try:
        round_trip_cost(plan["entry"], plan["target"], 1000, settings)
    except ValueError as e:
        raise TMError('the "costs" block in %s: %s' % ("psx/config.json" if mine else "COSTS", e))
    return settings, ("your costs settings" if mine else "the pack's default costs")


def swing_pool(bars):
    """Daily bars -> (pool, used): pool = each bar's (open, high, low, close) as ratios to its
    own LDCP, oldest first; used = the bars kept. A bar with no LDCP (or a price of 0) is left
    out."""
    pool, used = [], []
    for b in bars:
        ref = b.get("ldcp")
        prices = [b.get(k) for k in ("open", "high", "low", "close")]
        if isinstance(ref, bool) or not isinstance(ref, (int, float)) or not ref > 0 \
                or not all(isinstance(x, (int, float)) and x > 0 for x in prices):
            continue
        pool.append(tuple(float(x) / ref for x in prices))
        used.append(b)
    return pool, used


def swing_dates(d, n, cfg=None):
    """The buy day and the n PSX sessions after it (add_sessions), as dates."""
    out = [d]
    try:
        for _ in range(n):
            out.append(add_sessions(out[-1], 1, cfg))
    except ValueError as e:
        raise TMError(str(e))
    return out


# ---------------------------------------------------------------- swing mode: the replays

def swing_replays(pool, base, entry, stop, target, closes, trend_n, days, first=None, replays=10000, seed=19,
                  rng=None):
    """Monte Carlo replays of a swing plan (the rules: see "Swing mode" at the top) -> one record
    a replay: (outcome, session, fill, exit, at_open). outcome is "target", "stop", "gap" (the
    stop, sold at the open), "trend", "time" or "nofill"; session is the one it ended in (1 =
    the buy day); fill the buy price (None: no fill); exit the sale price; at_open True when it
    filled at the open. pool: swing_pool() ratios; base: the buy day's LDCP (the last close
    before the buy day, unless an ex-date lowered it: buy_day_ldcp); closes: the real
    adjusted closes before the buy day, on base's price scale (the trend rule); days: the buy day
    plus the sessions after it; first: the buy day's (lower, upper) limits, else from base.
    One draw a session, rng.choice(pool), rng = random.Random(seed) unless one is given."""
    choice = (rng or random.Random(seed)).choice
    limits = float_limits
    lo1, hi1 = first or limits(base)
    n = int(trend_n)
    start = [float(x) for x in closes][-n:]
    out = []
    add = out.append
    for _ in range(replays):
        o, h, l, c = choice(pool)
        O, H, L, C = base * o, base * h, base * l, base * c
        O = lo1 if O < lo1 else hi1 if O > hi1 else O
        H = lo1 if H < lo1 else hi1 if H > hi1 else H
        L = lo1 if L < lo1 else hi1 if L > hi1 else L
        C = lo1 if C < lo1 else hi1 if C > hi1 else C
        if O <= entry:                      # filled at the open: the day is held from the open
            fill, opened = O, True
            if O <= stop:
                add(("gap", 1, fill, O, True))
                continue
            if L <= stop:
                add(("stop", 1, fill, stop, True))
                continue
            if H >= target:
                add(("target", 1, fill, target, True))
                continue
        elif L <= entry:                    # filled at the limit: only the stop counts that day
            fill, opened = entry, False
            if L <= stop:
                add(("stop", 1, fill, stop, False))
                continue
        else:
            add(("nofill", 1, None, None, False))
            continue
        if days <= 1:                       # the buy day is the last session too
            add(("time", 1, fill, C, opened))
            continue
        win = list(start)                   # the n closes before the newest one (trend_break)
        pending = len(win) >= n and C < min(win)
        win.append(C)
        if len(win) > n:
            del win[0]
        prev = C
        for d in range(2, days + 1):
            o, h, l, c = choice(pool)
            lo, hi = limits(prev)
            O = prev * o
            O = lo if O < lo else hi if O > hi else O
            if pending:                     # the trend rule rang at the close before
                add(("trend", d, fill, O, opened))
                break
            if O <= stop:
                add(("gap", d, fill, O, opened))
                break
            if O >= target:
                add(("target", d, fill, O, opened))
                break
            L = prev * l
            if L < lo:
                L = lo
            if L <= stop:
                add(("stop", d, fill, stop, opened))
                break
            H = prev * h
            if H > hi:
                H = hi
            if H >= target:
                add(("target", d, fill, target, opened))
                break
            C = prev * c
            C = lo if C < lo else hi if C > hi else C
            if d == days:                   # the time stop: out at the last session's close
                add(("time", d, fill, C, opened))
                break
            if len(win) >= n and C < min(win):
                pending = True
            win.append(C)
            if len(win) > n:
                del win[0]
            prev = C
    return out


def swing_summary(records, entry, stop, target, costs, replays):
    """The numbers of one swing run from swing_replays() records: the outcome shares of all
    replays (stop counts the gaps too), the average R of the replays that filled, before and
    after costs (each buy and sale: leg_cost of 1,000 shares on the 0.01 tick), and the costs
    in R at the stop and at the target for a fill at the limit (what the verdict uses)."""
    risk = entry - stop
    sell_fee = costs.get("day_trade_one_side") is not True
    buy, sell = {}, {}
    cnt = dict.fromkeys(("target", "stop", "gap", "trend", "time", "nofill"), 0)
    gross, net, opened = [], [], 0
    for out, _, fill, px, at_open in records:
        cnt[out] += 1
        if fill is None:
            continue
        kb, ks = round(fill, 2), round(px, 2)
        b = buy.get(kb)
        if b is None:
            b = buy[kb] = leg_cost(kb, 1000, costs, True)
        s = sell.get(ks)
        if s is None:
            s = sell[ks] = leg_cost(ks, 1000, costs, sell_fee)
        g = (px - fill) / risk
        gross.append(g)
        net.append(g - (b + s) / 1000.0 / risk)
        opened += at_open

    def share(k):
        return round(k / float(replays), 4)
    stops = cnt["stop"] + cnt["gap"]
    return {
        "replays": replays, "filled": len(gross), "counts": dict(cnt, stop=stops),
        "p_target": share(cnt["target"]), "p_stop": share(stops), "p_gap": share(cnt["gap"]),
        "p_trend": share(cnt["trend"]), "p_time": share(cnt["time"]), "p_nofill": share(cnt["nofill"]),
        "p_fill_open": share(opened),
        "avg_R": round(statistics.mean(net), 3) if net else None,
        "avg_R_before_costs": round(statistics.mean(gross), 3) if gross else None,
        "r_at_target": round((target - entry) / risk, 4),
        "cost_R_stop": round(exit_cost_r(entry, stop, risk, costs), 4),
        "cost_R_target": round(exit_cost_r(entry, target, risk, costs), 4),
    }


def swing_bands(pool, base, days, first=None, seed=19, paths=BAND_PATHS):
    """The fan chart of a swing run: a second run of `paths` price paths with the same seed, one
    draw a session (its close, from the close before, inside the limits), no trade in it ->
    (bands: [5th, 25th, 50th, 75th, 95th percentile] of each session's close, the first
    SAMPLE_PATHS paths rounded to 0.01)."""
    choice = random.Random(seed).choice
    limits = float_limits
    allp = []
    for _ in range(paths):
        p, path = base, []
        for d in range(days):
            lo, hi = first if (first and d == 0) else limits(p)
            x = p * choice(pool)[3]
            p = lo if x < lo else hi if x > hi else x
            path.append(p)
        allp.append(path)
    bands = []
    for j in range(days):
        col = sorted(pp[j] for pp in allp)
        bands.append([round(pct(col, q), 2) for q in BAND_Q])
    return bands, [[round(x, 2) for x in pp] for pp in allp[:SAMPLE_PATHS]]


def swing_history(bars, entry, stop, target, sessions, trend_n):
    """The plan's rule on the real days: the stop and target as percents of the entry, put on
    each past day from its open (the stop rounded down, the target half up, to the 0.01
    tick), walked forward up to `sessions` sessions with the replays' rules: the buy day (the
    stop first, then the target), then each session (a gap at the open, an open over the
    target, the stop, the target, the time stop), and the trend rule at each close
    (trend_break over the real closes up to that day). Give payout-adjusted bars
    (adjusted_bars). A walk that meets no rule before the newest bar: "the data ran out"."""
    sp, tp = (entry - stop) / entry, (target - entry) / entry
    closes = [b["close"] for b in bars]
    last = len(bars) - 1
    out = {"mode": "swing", "days": len(bars), "sessions": sessions, "target": 0, "stop": 0, "gap": 0, "trend": 0,
           "time": 0, "ran_out": 0, "rule": {"from": "open", "stop_pct": round(100 * sp, 4),
                                             "target_pct": round(100 * tp, 4), "trend_sessions": trend_n},
           "walks": []}
    for i, b in enumerate(bars):
        e = b["open"]
        s, t = round_down_tick(e * (1 - sp)), round_half_up_tick(e * (1 + tp))
        oc, j = None, i
        if b["low"] <= s:
            oc = "stop"
        elif b["high"] >= t:
            oc = "target"
        else:
            pending = trend_break(closes[:i + 1], trend_n)[1]
            while oc is None:
                if j == last:               # no newer bar: a trend rule met at this close still counts
                    oc = "trend" if pending else "ran_out"
                    j += 1 if pending else 0
                    break
                j += 1
                x = bars[j]
                if pending:
                    oc = "trend"
                elif x["open"] <= s:
                    oc = "gap"
                elif x["open"] >= t:
                    oc = "target"
                elif x["low"] <= s:
                    oc = "stop"
                elif x["high"] >= t:
                    oc = "target"
                elif j - i >= sessions:
                    oc = "time"
                else:
                    pending = trend_break(closes[:j + 1], trend_n)[1]
        out[oc] += 1
        if oc == "gap":
            out["stop"] += 1
        out["walks"].append({"date": b["date"].isoformat(), "outcome": oc, "sessions": j - i})
    return out


def swing_verdict(mc, plan):
    """The verdict of a swing run: verdict() as in the other modes (the target's share of the
    replays that hit the target or the stop, against its break-even with the costs), or one
    plain line when no replay filled, or none hit either side."""
    if not mc.get("filled"):
        return ("No replay filled: in every one the buy day opened and stayed over the limit %.2f, so the order "
                "ended at the close." % plan["entry"])
    if lean(mc) is None:
        return ("No replay reached the target or the stop in %s: the trend rule and the time stop decide every "
                "one." % _sessions_words(mc["sessions"]))
    return verdict(mc)


def swing_verdict_notes(mc):
    """The lines right below a swing verdict: a note when over 40 % of the replays end by the
    trend rule or the time stop, and the average R when its sign goes against the lean."""
    notes = []
    lv = lean(mc) if mc.get("filled") else None
    slow = mc.get("p_trend", 0) + mc.get("p_time", 0)
    if slow > MANY_NEITHER and lv is not None:
        notes.append("%.1f %% of the replays end by the trend rule or the time stop; the price then decides those."
                     % (100 * slow))
    avg = mc.get("avg_R")
    if lv and abs(lv[0]) >= COIN_FLIP and avg is not None and avg != 0 and (avg > 0) != (lv[0] > 0):
        notes.append("Average R is %+.3f all the same: the trend and time exits, the gaps and the fills under the "
                     "limit pull it the other way." % avg)
    return notes


def ldcp_notes(plan, lim, ldcp, last, last_day):
    """The notes of a swing run that starts from the buy day's own LDCP, not the last close
    (buy_day_ldcp): what the replays do, and, when psx-exit set the entry from the last
    close ("entry_rule": "last close + 1%"), that the plan did."""
    src = "the company page" if lim.get("from") == "company page" else "the buy day's closing file"
    if ldcp < last:
        out = ["the buy day's LDCP is %.2f (from %s), under the last close %.2f of %s: PSX lowers the LDCP on an "
               "ex-date. The replays start from %.2f, and the closes before it are scaled to it, so a payout is "
               "not a fall" % (ldcp, src, last, day_label(last_day), ldcp)]
    else:
        out = ["the buy day's LDCP is %.2f (from %s), over the last close %.2f of %s. The replays start from %.2f, "
               "and the closes before it are scaled to it" % (ldcp, src, last, day_label(last_day), ldcp)]
    lc = plan.get("last_close")
    old = lc if isinstance(lc, (int, float)) and not isinstance(lc, bool) else last
    if str(plan.get("entry_rule") or "").startswith("last close") and abs(old - ldcp) >= 0.005:
        out.append("the plan's entry %.2f was set from the last close %.2f, not from the LDCP: check the entry and "
                   "the stop" % (plan["entry"], old))
    return out


# ---------------------------------------------------------------- swing mode: the run

def swing_machine(plan, root, now_utc, cfg, offline, replays, seed, mode, until, sessions):
    """A checked swing plan + the data folder -> the timemachine.json document (mode "swing")."""
    if mode != "auto":
        raise TMError("--mode %s is for a day plan: a swing plan runs swing mode by itself (leave out --mode)" % mode)
    if until is not None:
        raise TMError("--until is for a day plan: a swing plan runs to its exit-by day (or give --sessions N)")
    if sessions is None:
        n, n_from = plan["max_hold_sessions"], plan.get("hold_from") or "plan"
    else:
        n, n_from = _whole(sessions, "--sessions", 1, MAX_SESSIONS), "flag"
    sym, d, trend_n = plan["symbol"], datetime.date.fromisoformat(plan["date"]), plan["trend_sessions"]
    entry, stop, target = plan["entry"], plan["stop"], plan["target"]
    dates = swing_dates(d, n, cfg)
    costs, cost_words = swing_costs(cfg, plan)
    sessions_read, bars = daily_data(sym, root, d, cfg, offline)
    if len(bars) < MIN_SESSIONS:
        raise TMError(too_few_daily(sym, root, d, len(bars), sessions_read, cfg, offline, "swing mode"))
    pool, used = swing_pool(bars)
    if len(pool) < MIN_SESSIONS:
        raise TMError("only %d of %s's %d daily bars before %s have an LDCP (the last close PSX gives each day); "
                      "swing mode needs at least %d" % (len(pool), sym, len(bars), d.isoformat(), MIN_SESSIONS))
    missing = window_sessions([x for x, _ in sessions_read], cfg)[1]
    adj = adjusted_closes(bars, None, missing)
    last = float(bars[-1]["close"])
    base, base_from = last, "last close"
    warn = []
    lim = plan.get("limits")
    if lim and lim["lower"] <= entry <= lim["upper"]:
        limits = dict(lim, used=True)
        ldcp = buy_day_ldcp(lim)
        if ldcp is not None and abs(ldcp - last) >= 0.005:
            # the pool is ratios to each bar's own LDCP, so the buy day starts from its own LDCP, and the
            # closes before it go on the same price scale (as adjusted_closes does at an ex-date)
            base, base_from = ldcp, "ldcp"
            f = ldcp / last
            adj = [x * f for x in adj]
            warn += ldcp_notes(plan, lim, ldcp, last, bars[-1]["date"])
    else:
        if lim:
            warn.append("the plan's entry %.2f is outside the buy day's price limits %.2f to %.2f, so the replays "
                        "use the limits from the last close" % (entry, lim["lower"], lim["upper"]))
        try:
            lo, hi = price_limits(base)
        except ValueError as e:
            raise TMError(str(e))
        limits = {"lower": lo, "upper": hi, "ldcp": round(base, 2), "from": "computed", "base": "last close",
                  "used": True}
    first = (limits["lower"], limits["upper"])
    records = swing_replays(pool, base, entry, stop, target, adj, trend_n, n + 1, first, replays, seed)
    mc = swing_summary(records, entry, stop, target, costs, replays)
    ended = [0] * (n + 1)                   # how many replays ended in each session, the buy day first
    for r in records:
        ended[r[1] - 1] += 1
    bands, samples = swing_bands(pool, base, n + 1, first, seed)
    level = min(adj[-trend_n:]) if len(adj) >= trend_n else None
    mc.update(seed=seed, sessions=n, sessions_from=n_from, end=dates[-1].isoformat(), ended=ended,
              band_paths=BAND_PATHS, bands=bands, band_dates=[x.isoformat() for x in dates], sample_paths=samples,
              pool_bars=len(pool),
              pool_from=used[0]["date"].isoformat(), pool_to=used[-1]["date"].isoformat(), base=round(base, 2),
              base_from=base_from, trend_sessions=trend_n, trend_level=round(level, 2) if level is not None else None)
    history = swing_history(adjusted_bars(bars, None, missing), entry, stop, target, n, trend_n)
    for day in (d, dates[-1]):
        note = calendar_note(day, cfg).rstrip(".")
        if note and note not in warn:
            warn.append(note)
    if n_from != "flag" and plan.get("exit_by") and plan["exit_by"] != dates[-1].isoformat():
        warn.append("the plan's exit-by day is %s, but %d session%s after the buy day is %s by the calendar now: "
                    "the replays end then" % (day_label(datetime.date.fromisoformat(plan["exit_by"])), n,
                                              "" if n == 1 else "s", day_label(dates[-1])))
    lc = plan.get("last_close")
    if isinstance(lc, (int, float)) and not isinstance(lc, bool) and abs(lc - last) >= 0.005:
        warn.append("the plan's last close %.2f is not the newest close in the data folder (%.2f on %s): the "
                    "replays start from %.2f" % (lc, last, bars[-1]["date"].isoformat(), base))
    data = {"symbol": sym, "folder": root, "site": site_on(cfg) and not offline, "limits": limits,
            "interval": "1d", "daily_bars": len(bars), "sessions_read": len(sessions_read),
            "last_close": round(last, 2)}
    return {
        "created_by": "psx-timemachine",
        "created_at": utc_iso(now_utc),
        "mode": "swing",
        "mode_why": "A swing plan: the replays walk %s, one daily bar at a time." % _sessions_words(n),
        "data": data,
        "plan": dict((k, v) for k, v in plan.items() if v is not None),
        "costs": {"from": cost_words, "settings": costs},
        "mc": mc,
        "history": history,
        "verdict": swing_verdict(mc, plan),
        "verdict_notes": swing_verdict_notes(mc),
        "warnings": warn,
        "note": ("A simulation built from %s's own last %d daily bars. It is not a prediction: results, news and "
                 "halts can do things those days never did." % (sym, len(pool))),
    }


# ---------------------------------------------------------------- swing mode: the output

def swing_report(doc):
    """The text report of a swing run, one line each."""
    p, m, h = doc["plan"], doc["mc"], doc["history"]
    sym, day = p["symbol"], day_label(datetime.date.fromisoformat(p["date"]))
    end = day_label(datetime.date.fromisoformat(m["end"]))
    rule = " (%s)" % p["entry_rule"] if p.get("entry_rule") else ""
    upto = "%s (your --sessions)" % end if m["sessions_from"] == "flag" else "the exit-by day " + end
    lines = [
        "%s swing buy, %s: a limit buy at %.2f%s, stop %.2f, target %.2f (%sR, risk %.2f a share)."
        % (sym, day, p["entry"], rule, p["stop"], p["target"], "%g" % p["target_r"], p["risk_per_share"]),
        "Mode: swing. %s" % doc["mode_why"],
        "%s replays of %s, %s to %s, seed %d." % ("{:,}".format(m["replays"]), _sessions_words(m["sessions"]), day,
                                                  upto, m["seed"]),
        "Each session is one of %s's own last %d daily bars, drawn whole, so gaps between sessions are in it."
        % (sym, m["pool_bars"]),
        "The pool: %s to %s, each bar as ratios to its own LDCP, so a payout is not a fall." % (m["pool_from"],
                                                                                               m["pool_to"]),
        "A gap through the stop sells at the open, not at the stop.",
        "The trend rule: a close under the lowest close of the %d sessions before it%s sells at the next open."
        % (m["trend_sessions"], " (now %.2f)" % m["trend_level"] if m.get("trend_level") is not None else ""),
    ]
    lim = doc["data"].get("limits") or {}
    ldcp = lim.get("ldcp")
    if lim.get("from") == "computed":
        if isinstance(ldcp, bool) or not isinstance(ldcp, (int, float)):
            how = "computed"
        elif lim.get("base") == "file":
            how = "computed from the LDCP %.2f in the buy day's closing file" % ldcp
        else:
            how = "computed from the last close %.2f" % ldcp
    else:
        how = "from the %s" % lim.get("from", "plan")
    lines.append("Price limits: %.2f to %.2f on the buy day (%s), then from each replayed close. No replay goes "
                 "past them." % (lim["lower"], lim["upper"], how))
    c = m["cost_R_stop"]
    lines.append("Costs: %s a trade (%s, commission on both sides), counted in the R and the verdict."
                 % ("about %.2fR" % c if c >= 0.005 else "under 0.01R", doc["costs"]["from"]))
    lines += [
        "",
        "  target         %-8s out at %.2f, or at the open when it opens over it" % (pc(m["p_target"]), p["target"]),
        "  stop           %-8s out at %.2f, or at the open when it opens under it (a gap: %s)"
        % (pc(m["p_stop"]), p["stop"], pc(m["p_gap"])),
        "  trend          %-8s out at the next open (the trend rule)" % pc(m["p_trend"]),
        "  time           %-8s out at the close of %s (%s)" % (pc(m["p_time"]), end, "your --sessions"
                                                           if m["sessions_from"] == "flag" else "the time stop"),
        "  no fill        %-8s the limit %.2f did not fill on %s: the order ends at the close"
        % (pc(m["p_nofill"]), p["entry"], day),
        "",
    ]
    if m.get("avg_R") is None:
        lines.append("  Average R: none, no replay filled.")
    else:
        lines.append("  Average R: %+.3f over the replays that filled, costs included (R = the %.2f risk: %+.2fR at "
                     "the target, %+.2fR at the stop, for a fill at %.2f)."
                     % (m["avg_R"], p["risk_per_share"], m["r_at_target"] - m["cost_R_target"],
                        -1 - m["cost_R_stop"], p["entry"]))
    ru = h["rule"]
    lines += [
        "",
        "History: a buy at the open of each of those %d days, with the stop %.2f %% under it and the target %.2f %% "
        "over it, walked forward up to %d real sessions:" % (h["days"], ru["stop_pct"], ru["target_pct"],
                                                             h["sessions"]),
        "  target %d, stop %d (%d gap%s), trend %d, time %d, the data ran out %d."
        % (h["target"], h["stop"], h["gap"], "" if h["gap"] == 1 else "s", h["trend"], h["time"], h["ran_out"]),
        "  The data ran out: those walks met no rule before the newest closing file.",
        "",
        doc["verdict"],
    ]
    lines += list(doc.get("verdict_notes") or []) + [""]
    for w in doc.get("warnings") or []:
        lines.append("Note: " + w + ".")
    head, _, rest = doc["note"].partition(" It is ")      # one sentence a line
    lines += [head] + (["It is " + rest] if rest else [])
    lines.append("Same seed, same numbers: repeat this run with --seed %d." % m["seed"])
    return lines


def render_swing_chart(doc, height=17):
    """ASCII fan chart of a swing run: one column a session (its close), the dates as labels."""
    p, m = doc["plan"], doc["mc"]
    bands, dates = m["bands"], m["band_dates"]
    width = max(1, min(3, 64 // max(1, len(bands))))
    lo = min([b[0] for b in bands] + [p["stop"], p["target"]])
    hi = max([b[4] for b in bands] + [p["stop"], p["target"]])
    pad = (hi - lo) * 0.04 or 0.01
    lo, hi = lo - pad, hi + pad
    step = (hi - lo) / (height - 1)

    def row(price):
        return max(0, min(height - 1, int(round((hi - price) / step))))
    cols = len(bands) * width
    grid = [[" "] * cols for _ in range(height)]
    for j, b in enumerate(bands):
        r5, r25, r50, r75, r95 = [row(x) for x in b]
        for k in range(j * width, (j + 1) * width):
            for r in range(r95, r5 + 1):
                grid[r][k] = "."
            for r in range(r75, r25 + 1):
                grid[r][k] = ":"
            grid[r50][k] = "*"
    marks = {row(p["target"]): "target", row(p["stop"]): "stop"}
    if row(p["entry"]) not in marks:
        marks[row(p["entry"])] = "entry"
    for r in (row(p["target"]), row(p["stop"])):
        grid[r] = ["-" if ch == " " else ch for ch in grid[r]]
    first, last = datetime.date.fromisoformat(dates[0]), datetime.date.fromisoformat(dates[-1])
    out = ["%s swing plan, %s replay paths of the close, %s to %s (seed %d, swing mode)" % (
        p["symbol"], "{:,}".format(m.get("band_paths", BAND_PATHS)), day_label(first), day_label(last), m["seed"])]
    for r in range(height):
        label = marks.get(r)
        price = {"target": p["target"], "stop": p["stop"], "entry": p["entry"]}.get(label, hi - r * step)
        left = ("%9.2f |" % price) if (label or r in (0, height - 1)) else "          |"
        out.append(left + "".join(grid[r]) + ("  " + label if label else ""))
    axis, taken = [" "] * cols, []
    for j in (0, len(bands) - 1, len(bands) // 2):     # the first and last date, then the middle one if it fits
        lab = dates[j][5:]                  # "2026-10-29" -> "10-29"
        s = max(0, min(j * width, cols - len(lab)))
        if all(s > b or s + len(lab) < a for a, b in taken):
            axis[s:s + len(lab)] = list(lab)
            taken.append((s, s + len(lab)))
    out.append("          +" + "-" * cols)
    out.append("           " + "".join(axis).rstrip())
    out.append("* median   : middle half of the paths   . 90 % of the paths   - stop and target")
    out.append("One column a session: its close.")
    return out


# ---------------------------------------------------------------- commands

def _write(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")


def compute(a):
    try:
        now = parse_now(a.now)
    except ValueError as e:
        raise TMError(str(e))
    cfg = load_config(a.config)
    plan = load_plan(a, now, cfg)
    offline = a.offline or bool(a.now)      # a replay of a past time (--now) never fetches
    return time_machine(plan, a.data, now, cfg, offline, a.replays, a.seed, a.window, a.until, a.mode,
                        getattr(a, "sessions", None))


def cmd_run(a):
    doc = compute(a)
    if a.write:
        _write(a.write, doc)
    if a.json:
        slim = dict(doc, mc={k: v for k, v in doc["mc"].items() if k != "sample_paths"})
        print(json.dumps(slim, indent=2))
    else:
        print("\n".join(report(doc)))
        if a.write:
            print("Wrote %s." % a.write)
        if doc.get("mode") == "swing":
            print(SWING_NEXT)
        else:
            print("Next: /psx-budget sizes it to your budget and your max loss per trade, costs included.")
    return 0


def cmd_chart(a):
    if a.from_file:
        if not os.path.exists(a.from_file):
            raise TMError("no %s: run timemachine.py run --write first" % a.from_file)
        try:
            with open(a.from_file, encoding="utf-8") as fh:
                doc = json.load(fh)
            doc["mc"]["bands"]
            if doc.get("mode") == "swing":
                doc["mc"]["band_dates"][0]
        except (ValueError, KeyError, TypeError, IndexError):
            raise TMError("%s is not a time machine result" % a.from_file)
    else:
        doc = compute(a)
    print("\n".join(render_chart(doc)))
    print("\n".join([doc.get("verdict", "")] + list(doc.get("verdict_notes") or [])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Time Machine: replay a planned PSX day trade or swing trade "
                                             "(psx-timemachine).")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--plan", default="psx/plan.json", help="the plan from psx-exit")
        p.add_argument("--symbol", help="or give the plan as flags: the PSX symbol")
        p.add_argument("--date", help="the plan's day, YYYY-MM-DD (default: today, Karachi time)")
        p.add_argument("--entry", type=float)
        p.add_argument("--stop", type=float)
        p.add_argument("--target", type=float)
        p.add_argument("--entry-time", help='the entry bar\'s close, e.g. "10:15 AM"')
        p.add_argument("--setup", default="orb")
        p.add_argument("--or-minutes", type=int, default=15)
        p.add_argument("--stop-rule", default="auto", choices=["auto", "or_mid", "or_low", "pct"])
        p.add_argument("--mode", default="auto", choices=["auto", "bars", "daily"],
                       help="bars: the stock's own 5-minute bars; daily: its last 60 daily bars; auto picks "
                            "(a swing plan always runs swing mode)")
        p.add_argument("--sessions", type=int,
                       help="a swing plan only: replay the buy day and this many sessions after it (default: the "
                            "plan's max_hold_sessions, to its exit-by day)")
        p.add_argument("--replays", type=int, default=10000)
        p.add_argument("--seed", type=int, default=19)
        p.add_argument("--window", type=int, default=2,
                       help="bars mode: draw from the same time of day +- this many bars")
        p.add_argument("--until", help="end each replay at this Karachi time, e.g. 15:20 (default: the plan's "
                                       "flat_by, else the close)")
        p.add_argument("--data", default="psx/data", help="the data folder (default psx/data)")
        p.add_argument("--config", default="psx/config.json",
                       help="calendar fixes and psx_site (default psx/config.json)")
        p.add_argument("--offline", action="store_true", help="never use the network, read only the data folder")
        p.add_argument("--now", help='pretend it is this time ("2026-10-29 10:20" Karachi, or ISO with Z); '
                                     'then it reads only the data folder too')

    p = sub.add_parser("run", help="replay the plan: 10,000 simulated sessions plus the history")
    common(p)
    p.add_argument("--json", action="store_true")
    p.add_argument("--write", nargs="?", const="psx/timemachine.json", metavar="PATH",
                   help="save the result (default psx/timemachine.json)")
    p = sub.add_parser("chart", help="ASCII fan chart of the replays")
    common(p)
    p.add_argument("--from", dest="from_file", nargs="?", const="psx/timemachine.json", metavar="PATH",
                   help="draw a saved result instead of running again")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"run": cmd_run, "chart": cmd_chart}[a.cmd](a)
    except (TMError, ValueError) as e:
        print("timemachine.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
