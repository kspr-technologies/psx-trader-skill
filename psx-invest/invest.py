#!/usr/bin/env python3
"""psx-invest: long-term signals for the Pakistan Stock Exchange (PSX). Standard library only, Python 3.8.

Run it in the folder that holds psx/, with psx/invest.json (a copy of invest.example.json):

    python3 invest.py signals                        # one signal per holding; writes nothing
    python3 invest.py signals --write                # also psx/signals.json, the state, the journal
    python3 invest.py explain OGDC                   # every fact and rule for one share
    python3 invest.py money 46000                    # how new money could be split; writes nothing
    python3 invest.py orders --money 46000 --write   # today's orders for psx-risk
    python3 invest.py cash --add 46000               # record new long-term money (or --set N)
    python3 invest.py ack SYS --keep                 # your choice on a REVIEW or TRIM
    python3 invest.py backfill --dry-run             # what the one-time backfill would fetch
    python3 invest.py backfill                       # fetch it (site on only), 1.5 s apart
    python3 invest.py rules                          # the rules and numbers in use
    python3 invest.py journal                        # what the rules said, month by month

Each holding gets one signal: ADD, HOLD, TRIM or REVIEW. A signal says which rules are
met. It is not a forecast. The price rules use month-end closes only, scaled for payouts
and splits. No rule uses, or shows, what you paid. Nothing is placed: an order goes to
psx/orders.json for psx-risk, then psx-broker shows a ticket, and you place it yourself.

It reads closing files (psx/data/mkt_summary), psx/portfolio.json, psx/invest.json,
psx/config.json, psx/metrics.json (psx-analyst) and psx/prices.json (psx-alerts). No company
pages. The PSX site is used only with "psx_site": true, never with --offline or --now: by
backfill (old closing files, once) and by signals (only the days after the newest file).

Exit codes: 0 ran; 1 nothing to show (no holdings, or no closing files); 2 an error.
"""

import argparse
import bisect
import datetime
import gzip
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

CONFIG_PATH = os.path.join("psx", "config.json")
INVEST_PATH = os.path.join("psx", "invest.json")
PORTFOLIO_PATH = os.path.join("psx", "portfolio.json")
STATE_PATH = os.path.join("psx", "state", "invest.json")
SIGNALS_PATH = os.path.join("psx", "signals.json")
JOURNAL_PATH = os.path.join("psx", "invest", "journal.jsonl")
ORDERS_PATH = os.path.join("psx", "orders.json")
METRICS_PATH = os.path.join("psx", "metrics.json")
PRICES_PATH = os.path.join("psx", "prices.json")
DATA_PATH = os.path.join("psx", "data")

# psx/invest.json: the rule numbers (psx-invest/invest.example.json holds the same)
RULES = {"trend_months": 10, "momentum_months": 12, "add_band_pct": 2, "down_band_pct": 5, "break_trim_pct": 50,
         "max_weight_pct": 20, "trim_band_pct": 5, "max_sector_pct": 30, "sector_band_pct": 5, "sector_groups": {},
         "targets": {}, "results_drop_pct": 25, "stale_trade_sessions": 20, "keep_cash_pkr": 0, "candidates": [],
         "sessions": 500}
# what it reads from psx/config.json when the key is not there (the pack's defaults)
CONFIG_DEFAULTS = {"psx_site": False, "paper": True, "blocked": [], "min_order_pkr": 5000, "max_order_pkr": 400000,
                   "max_trades_per_day": 3, "max_orders_per_day": 10, "data_delay_minutes": 5}
FRESH_MINUTES = 15         # a price in psx/prices.json is fresh when its data time is at most this old
SPLIT_PCT = 10             # a no-flag LDCP change is a split only at this much or more
JUMP_PCT = 2               # a missing file next to an LDCP jump this big hides a payout or split
REQUEST_SECONDS = 1.7      # one backfill request: the 1.5 s pause plus the download
FILE_MB = 0.0235           # a PSX closing file is about 23.5 KB
CENT = 0.005
MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December")
SIGNAL_LINES = {"ADD": "ADD. The add rules are met. You could put new money here. You decide.",
                "HOLD": "HOLD. No rule asks for a change.",
                "TRIM": "TRIM. A sell rule is met.",
                "REVIEW": "REVIEW. Please look at this before any change."}
NOTIFY_LINES = {"TRIM": "notify: %s: a sell rule is met (TRIM). Open the report. You decide.",
                "ADD": "notify: %s: the add rules are met (ADD). Open the report. You decide.",
                "REVIEW": "notify: %s: please look at this share (REVIEW). Open the report.",
                "HOLD": "notify: %s: no rule asks for a change now (HOLD)."}
FOOTER = "Signals are rules, not predictions. No tool knows where a price will go. Not financial advice."
TAX_LINE = "A sale with a gain owes capital gains tax. NCCPL takes it. Not tax advice."
WAITS_LINE = "No share meets the add rules today. The money waits as cash. That is a rule, not a forecast."
NO_ROOM_LINE = "%s stays as cash. No ADD share has room for it."
CASH_LINE = ("This money must not be in your trading budget too. One broker account holds both: the week report "
             "shows both cash numbers and their sum.")
PAPER_REAL_LINE = ("Paper mode and real holdings: nothing was written to psx/orders.json. The journal has what the "
                   "rules would do (psx/invest/journal.jsonl).")
PROPOSALS = ("Proposals only. Nothing in this pack places an order: psx-risk checks each one, psx-broker shows a "
             "ticket after your yes, and you place it yourself in your broker's app.")
NO_INVEST = "No %s: copy psx-invest/invest.example.json to %s first (step 1 in the skill page)."
NOT_HERE = ("This folder has no psx/invest.json, so psx-invest writes nothing here. Run it in the long-term folder "
            "(the one with psx/invest.json). With --invest, only the commands that write nothing work here.")
FILL_REAL_LINE = ("After you buy or sell in your own app, record each fill: ticket.py fill --symbol %s --side %s "
                  "--qty Q --price P --real (psx-broker; Q and P as your app shows them). Record new money first "
                  "with invest.py cash --add N. Until you record it, the rules see the old shares and cash.")
NO_MOVE = "It is not a payout or a split, so nothing is scaled."
NOT_KNOWN = "%s: not known yet. It needs %d month-ends. The folder has %d. Run backfill."
SHARE_NOT_KNOWN = "%s: not known yet. It needs %d month-ends. %s has %d in the closing files."
KIND_WORDS = {"XB": "a bonus (XB)", "XR": "a right issue (XR)", "split": "a split"}
STATE_KEYS = ("watch_start", "month_end", "signals", "trims", "acks", "no_session", "breaks")
NO_FILE_RUN = 6            # no PSX holiday has more weekdays in a row: a longer run of 404s is a site problem


class InvestError(Exception):
    """A plain message for the user: the tool stops with exit code 2."""


# ---------------------------------------------------------------- time (Karachi is UTC+5 all year: no time zone files)
# copied from tests/psx_core.py, verbatim: never edit a copy

KARACHI_HOURS = 5   # Karachi time is UTC+5 all year: no daylight saving


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


# ---------------------------------------------------------------- PSX calendar
# copied from tests/psx_core.py, verbatim: never edit a copy

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


# ---------------------------------------------------------------- prices and costs
# copied from tests/psx_core.py, verbatim: never edit a copy


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


COSTS = {"commission_pct": 0.15, "commission_min_per_share": 0.03, "sales_tax_pct": 15,
         "other_charges_pct": 0.0066, "day_trade_one_side": False}


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


# ---------------------------------------------------------------- PSX files
# copied from tests/psx_core.py, verbatim: never edit a copy

FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name
FUTURE_RE = re.compile(r"^([A-Z0-9]+)-(C?)(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)([A-Z]?\d*)$")
PSX = "https://dps.psx.com.pk"
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


# ---------------------------------------------------------------- data access
# copied from tests/psx_core.py, verbatim: never edit a copy

UA = "Mozilla/5.0 (compatible; psx-trader-skill/1.0)"
PAUSE = 1.5         # seconds between two requests to the PSX site, at least
SLEEP = time.sleep  # tests swap this out
SITE_OFF = ('The PSX site is off for this tool: save %s with your browser, or set "psx_site": true '
            'in psx/config.json if PSX has given you permission.')


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


# ---------------------------------------------------------------- daily levels (closing files: payouts and splits)
# copied from tests/psx_core.py, verbatim: never edit a copy


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


# ---------------------------------------------------------------- small helpers

def _f(x):
    """A number as a float, or None (never a bool, never NaN)."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def cents(x):
    """Rupees to 0.01, half up, as a float."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pct_change(a, b):
    """(a / b - 1) x 100, half up to 0.01, or None. Done in decimals, so +2.00% stays +2.00%."""
    if a is None or not b:
        return None
    v = (Decimal(str(a)) / Decimal(str(b)) - 1) * 100
    return float(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pct_text(v, sign=True, edges=()):
    """A percent for people, one decimal: "+3.1%". Two decimals when one decimal would show a
    rule's edge that the number does not reach (1.99 is "1.99%", never "2.0%")."""
    text = "%.1f" % abs(v)
    if float(text) in [float(e) for e in edges] and abs(float(text) - abs(v)) > 1e-9:
        text = "%.2f" % abs(v)
    if v < 0 and float(text) > 0:
        return "-" + text + "%"
    return ("+" if sign and float(text) > 0 else "") + text + "%"


def whole(n):
    """Shares for people: 1436 -> "1,436"."""
    return "{:,}".format(int(round(n)))


def amount_text(x):
    """An amount for a command line: 46000 -> "46000", 46000.5 -> "46000.50"."""
    return ("%d" % x) if abs(x - round(x)) < 0.005 else ("%.2f" % x)


def date_of(text, what):
    """"YYYY-MM-DD" -> a date. None stays None. Anything else is one plain InvestError."""
    if text is None:
        return None
    try:
        return datetime.date.fromisoformat(str(text).strip())
    except ValueError:
        raise InvestError("%s wants a date like 2026-11-04, not %r" % (what, text))


def month_label(d):
    """date -> "Oct 2026"."""
    return "%s %d" % (MONTH_NAMES[d.month - 1][:3], d.year)


def end_of_month(d):
    """date -> "the end of October"."""
    return "the end of " + MONTH_NAMES[d.month - 1]


# ---------------------------------------------------------------- files

def read_json(path, what):
    """A JSON file -> its data, or None when it is not there. Bad JSON is one plain InvestError."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise InvestError("%s (%s) is not valid JSON: %s" % (path, what, e))
    except OSError as e:
        raise InvestError("cannot read %s (%s): %s" % (path, what, e))


def write_json(path, data):
    """Write JSON through a temp file, then rename it, so a crash never leaves half a file."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def append_journal(path, entries):
    """Add entries to the journal: one JSON object a line. It only grows."""
    if not entries:
        return
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, sort_keys=True) + "\n")


def read_journal(path):
    """The journal entries, oldest first. A broken line is skipped."""
    out = []
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
    return out


# ---------------------------------------------------------------- settings

def load_rules(path):
    """psx/invest.json on top of RULES, checked. With no file: the "copy the example first" line."""
    doc = read_json(path, "the long-term rules")
    if doc is None:
        raise InvestError(NO_INVEST % (path, path))
    if not isinstance(doc, dict):
        raise InvestError("%s must be a JSON object: {...}" % path)
    r = dict(RULES)
    r.update(doc)
    for k, low in (("trend_months", 2), ("momentum_months", 1), ("stale_trade_sessions", 1), ("sessions", 20)):
        if isinstance(r[k], bool) or not isinstance(r[k], int) or r[k] < low:
            raise InvestError('"%s" in %s wants a whole number, %d or more' % (k, path, low))
    for k in ("add_band_pct", "down_band_pct", "break_trim_pct", "max_weight_pct", "trim_band_pct",
              "max_sector_pct", "sector_band_pct", "results_drop_pct"):
        v = _f(r[k])
        if v is None or not 0 <= v <= 100:
            raise InvestError('"%s" in %s wants a percent from 0 to 100' % (k, path))
        r[k] = v
    keep = _f(r["keep_cash_pkr"])
    if keep is None or keep < 0:
        raise InvestError('"keep_cash_pkr" in %s wants rupees, 0 or more' % path)
    r["keep_cash_pkr"] = keep
    groups = r["sector_groups"]
    if not isinstance(groups, dict) or not all(isinstance(v, list) and all(isinstance(c, str) for c in v)
                                               for v in groups.values()):
        raise InvestError('"sector_groups" in %s wants {"NAME": ["0801", "0802"]}: PSX sector codes as text' % path)
    r["group_of"] = dict((c.strip(), str(name)) for name, codes in groups.items() for c in codes)
    targets = r["targets"]
    if not isinstance(targets, dict) or any(_f(v) is None or not 0 <= _f(v) <= 100 for v in targets.values()):
        raise InvestError('"targets" in %s wants {"SYM": percent}, each from 0 to 100' % path)
    r["targets"] = dict((str(k).strip().upper(), _f(v)) for k, v in targets.items())
    if sum(r["targets"].values()) > 100 + 1e-9:
        raise InvestError('the "targets" in %s add up to more than 100%%' % path)
    cands = r["candidates"]
    if not isinstance(cands, list) or not all(isinstance(c, str) and c.strip() for c in cands):
        raise InvestError('"candidates" in %s wants a list of PSX symbols, like ["OGDC"]' % path)
    r["candidates"] = []
    for c in cands:
        if c.strip().upper() not in r["candidates"]:
            r["candidates"].append(c.strip().upper())
    return r


def load_config(path):
    """psx/config.json ({} when it is not there), with the keys psx-invest reads checked."""
    doc = read_json(path, "the config")
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise InvestError("%s must be a JSON object: {...}" % path)
    for k in ("min_order_pkr", "max_order_pkr", "max_trades_per_day", "max_orders_per_day"):
        if doc.get(k) is not None and (_f(doc[k]) is None or _f(doc[k]) < 0):
            raise InvestError('"%s" in %s wants a number, 0 or more' % (k, path))
    if not isinstance(doc.get("blocked", []), list):
        raise InvestError('"blocked" in %s wants a list of PSX symbols' % path)
    try:
        trading_day(datetime.date(2026, 1, 5), doc)
        calendar_note(datetime.date(2026, 1, 5), doc)
        ramadan_range(2026, doc)
    except ValueError as e:
        raise InvestError(str(e))
    return doc


def setting(cfg, key):
    """A psx/config.json value, or the pack's default when the key is not there."""
    return cfg[key] if key in cfg else CONFIG_DEFAULTS[key]


def load_portfolio(path):
    """psx/portfolio.json -> {"holdings": {SYM: shares}, "cash", "real", "as_of", "found", "doc"}.
    It reads the share counts and the cash only, never what you paid."""
    doc = read_json(path, "the ledger")
    out = {"holdings": {}, "cash": 0.0, "real": False, "as_of": None, "found": doc is not None, "doc": doc}
    if doc is None:
        return out
    if not isinstance(doc, dict):
        raise InvestError('%s must be a JSON object with "cash" and "positions"' % path)
    positions = doc.get("positions", [])
    if not isinstance(positions, list) or not all(isinstance(p, dict) for p in positions):
        raise InvestError('%s: "positions" must be a list of JSON objects: [{...}]' % path)
    for p in positions:
        sym = str(p.get("ticker") or p.get("symbol") or "").strip().upper()
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(x.get("shares")) or 0 for x in p.get("lots") or [] if isinstance(x, dict))
        if sym and n > 0:
            out["holdings"][sym] = out["holdings"].get(sym, 0) + (int(n) if n == int(n) else n)
    if doc.get("cash") is not None and _f(doc.get("cash")) is None:
        raise InvestError('%s: "cash" must be a number of rupees' % path)
    out["cash"] = _f(doc.get("cash")) or 0.0
    out["real"] = doc.get("real_holdings") is True
    out["as_of"] = date_of(doc.get("as_of"), '"as_of" in %s' % path)
    return out


def load_state(path):
    """psx/state/invest.json, or a fresh state when it is not there."""
    try:
        doc = read_json(path, "the psx-invest state")
    except InvestError as e:
        raise InvestError("%s. Move it away to start again: the watch start, the open TRIMs and your acks "
                          "are kept there." % e)
    st = {"watch_start": None, "month_end": None, "signals": {}, "trims": {}, "acks": {}, "no_session": [],
          "breaks": {}}
    if isinstance(doc, dict):
        for k in STATE_KEYS:
            if k in doc and isinstance(doc[k], type(st[k]) if st[k] is not None else str):
                st[k] = doc[k]
    return st


def load_metrics(path):
    """psx/metrics.json (psx-analyst writes it) -> ({SYM: row}, True when the file is there)."""
    doc = read_json(path, "the results")
    rows = {}
    for r in (doc.get("companies") or []) if isinstance(doc, dict) else []:
        if isinstance(r, dict) and r.get("symbol"):
            rows[str(r["symbol"]).strip().upper()] = r
    return rows, doc is not None


# ---------------------------------------------------------------- the closing files

def share_rows(rows):
    """One closing file's rows -> {symbol: row} of shares only: futures and debt are left out
    (symbol_kind). Right letters stay in: the rights rule looks for them."""
    out = {}
    for r in rows or []:
        sym = str(r.get("symbol") or "").strip().upper()
        if sym and symbol_kind(sym, r.get("sector"), r.get("short_name")) not in ("stock_future", "index_future",
                                                                                  "debt"):
            out[sym] = r
    return out


def newest_file(root, today, back):
    """The date of the newest closing file in the data folder, up to today, or None."""
    d = today
    for _ in range(back):
        if d.weekday() < 5 and os.path.exists(daily_path(root, d)):
            return d
        d -= datetime.timedelta(days=1)
    return None


def site_error(e):
    """A site error in plain words."""
    if isinstance(e, urllib.error.HTTPError):
        return "PSX answered HTTP %d" % e.code
    if isinstance(e, urllib.error.URLError):
        return "could not reach PSX: %s" % (e.reason,)
    return str(e)


def fetch_new_days(root, cfg, today, newest):
    """With the site on: the closing files of the trading days after `newest`, up to today. Never an
    older day: that is the backfill's job. -> (the dates saved, notes)."""
    saved, notes = [], []
    d = newest + datetime.timedelta(days=1)
    while d <= today:
        if trading_day(d, cfg)[0] and not os.path.exists(daily_path(root, d)):
            try:
                rows = daily_rows(d, root, cfg, False)
            except (urllib.error.URLError, OSError, ValueError) as e:
                notes.append("The closing file of %s did not come from the PSX site (%s). The signals use the "
                             "files that are there." % (day_label(d), site_error(e)))
                break
            if rows:
                saved.append(d)
        d += datetime.timedelta(days=1)
    return saved, notes


def last_session_of_month(year, month, cfg=None):
    """The last PSX session of a month by the calendar (weekends and holidays left out)."""
    d = datetime.date(year + month // 12, month % 12 + 1, 1) - datetime.timedelta(days=1)
    for _ in range(31):
        if trading_day(d, cfg)[0]:
            return d
        d -= datetime.timedelta(days=1)
    return d


def month_ends(dates, cfg=None):
    """The month-end sessions among the closing files read, oldest first: the last file of each
    month. The newest month counts only when the file of its last session by the calendar is in,
    so a month in progress waits."""
    last = {}
    for d in dates or []:
        k = (d.year, d.month)
        if k not in last or d > last[k]:
            last[k] = d
    keys = sorted(last)
    out = [last[k] for k in keys]
    if keys and out[-1] < last_session_of_month(keys[-1][0], keys[-1][1], cfg):
        out.pop()
    return out


def read_market(root, today, cfg, rules, no_session):
    """The newest `sessions` closing files up to today, read once (offline) -> the facts every share
    uses: the sessions as {symbol: row} dicts, the missing sessions (less the recorded no-session
    days) and the month-ends."""
    n = rules["sessions"]
    got = recent_sessions(today, n, root, cfg, True, True, 3 * n + 10)
    sessions = [(d, share_rows(rows)) for d, rows in got]
    dates = [d for d, _ in sessions]
    skip = set(no_session or [])
    window, missing = window_sessions(dates, cfg)
    window = [d for d in window if d.isoformat() not in skip]
    missing = [d for d in missing if d.isoformat() not in skip]
    ends = month_ends(dates, cfg)
    mk = {"sessions": sessions, "dates": dates, "missing": missing, "ends": ends,
          "prev_of": dict((window[i], window[i - 1]) for i in range(1, len(window))),
          "month_end": ends[-1] if ends else None, "newest": dates[-1] if dates else None,
          "first": dates[0] if dates else None}
    mk["at_month_end"] = bool(ends) and ends[-1] == mk["newest"]
    base = mk["month_end"] or mk["newest"]
    if base is None:
        mk["next_end"] = None
    elif mk["month_end"] is None:
        mk["next_end"] = last_session_of_month(base.year, base.month, cfg)
    else:
        nxt = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
        mk["next_end"] = last_session_of_month(nxt[0], nxt[1], cfg)
    return mk


# ---------------------------------------------------------------- one share's prices

def split_map(bars, prev_of, split_pct=SPLIT_PCT):
    """psx-invest's map for adjusted_closes(): {bar date: the date its LDCP is scaled against}.
    - Next sessions (prev_of[bar] is the bar before): the bar before, so any LDCP change is
      scaled, as in the scanner.
    - Days with no trade between: the bar before only when the LDCP is split_pct (10%) or more
      off the close before (a split). Else the session right before, so nothing is scaled: PSX
      sets a close on a day with no trade, and that small move is real.
    A flagged bar (XD, XB, XR) is always scaled, and never across a missing file:
    adjusted_closes() does both."""
    out = {}
    for i in range(1, len(bars or [])):
        cur, before = bars[i], bars[i - 1]
        right_before = prev_of.get(cur["date"])
        ldcp, close = cur.get("ldcp"), before.get("close")
        if right_before == before["date"]:
            out[cur["date"]] = before["date"]
        elif ldcp and close and abs(ldcp / close - 1) * 100 >= split_pct - 1e-9:
            out[cur["date"]] = before["date"]
        else:
            out[cur["date"]] = right_before
    return out


def month_closes(bars, adj, ends):
    """[(month-end, the share's adjusted close on or before it)], from its first month-end on."""
    dates = [b["date"] for b in bars]
    out = []
    for m in ends:
        k = bisect.bisect_right(dates, m) - 1
        if k >= 0:
            out.append((m, adj[k]))
    return out


def vote_of(pct, up, down):
    """"up" at +up% or more, "down" at -down% or less, else "none". None stays None."""
    if pct is None:
        return None
    if pct >= up:
        return "up"
    if pct <= -down:
        return "down"
    return "none"


def trend_vote(closes, months=10, up=2, down=5):
    """(line, gap %, vote) of the newest month-end close. The line is the plain mean of the last
    `months` month-end closes, this one included. (None, None, None) with fewer closes."""
    c = [float(x) for x in closes or []]
    if len(c) < int(months):
        return None, None, None
    line = sum(c[-int(months):]) / int(months)
    gap = pct_change(c[-1], line)
    return line, gap, vote_of(gap, up, down)


def momentum_vote(closes, months=12, up=2, down=5):
    """(change %, vote) of the newest month-end close against the one `months` month-ends back.
    (None, None) with fewer than months + 1 closes."""
    c = [float(x) for x in closes or []]
    if len(c) < int(months) + 1:
        return None, None
    change = pct_change(c[-1], c[-1 - int(months)])
    return change, vote_of(change, up, down)


def vote_history(mcl, rules):
    """[(month-end, trend vote, 12-month vote)] at each month-end of a share (None: not known)."""
    out = []
    closes = [c for _, c in mcl]
    for k, (d, _) in enumerate(mcl):
        use = closes[:k + 1]
        t = trend_vote(use, rules["trend_months"], rules["add_band_pct"], rules["down_band_pct"])[2]
        m = momentum_vote(use, rules["momentum_months"], rules["add_band_pct"], rules["down_band_pct"])[1]
        out.append((d, t, m))
    return out


def next_state(state, t, m):
    """One month-end of the break walk. In to out when both votes are down; out to in when no vote
    is down and at least one is up. With no state yet (None): "out" when both are down, else "in"."""
    both_down = t == "down" and m == "down"
    if state is None:
        return "out" if both_down else "in"
    if state == "in" and both_down:
        return "out"
    if state == "out" and "down" not in (t, m) and "up" in (t, m):
        return "in"
    return state


def break_states(votes):
    """[(month-end, "in" or "out")], oldest first, from the first month-end with both votes known.
    votes: [(month-end, trend vote, 12-month vote)]. In to out when both votes are down; out to in
    when no vote is down and at least one is up. The first known state is "out" when both votes are
    down then, else "in". This walk of the files read is used when the state has no saved one."""
    out, state = [], None
    for d, t, m in votes:
        if t is None or m is None:
            continue
        state = next_state(state, t, m)
        out.append((d, state))
    return out


def saved_break(state, sym):
    """The break state of a share saved in psx/state/invest.json: {"state", "since", "changed",
    "walked"} with dates, or None when there is none or it is broken."""
    rec = (state.get("breaks") or {}).get(sym)
    if not isinstance(rec, dict) or rec.get("state") not in ("in", "out") or not isinstance(rec.get("changed"), bool):
        return None
    try:
        since, walked = date_of(rec.get("since"), "since"), date_of(rec.get("walked"), "walked")
    except InvestError:
        return None
    if since is None or walked is None or since > walked:
        return None
    return {"state": rec["state"], "since": since, "changed": rec["changed"], "walked": walked}


def walk_on(saved, votes):
    """(state, since, changed): the saved break state, walked on over the month-ends after the one
    it was saved at. So the newest 500 files can move on, and a share that stays out keeps its
    "since" date (and with it its ack and its TRIM). None when the saved state does not fit these
    votes: no saved state, or the month-end it was saved at is not among the known votes (a run
    with an older --now, or a gap of more than a year): then the walk of the files read is used."""
    known = [(d, t, m) for d, t, m in votes if t is not None and m is not None]
    if saved is None or not known or not known[0][0] <= saved["walked"] <= known[-1][0]:
        return None
    state, since, changed = saved["state"], saved["since"], saved["changed"]
    for d, t, m in known:
        if d > saved["walked"]:
            new = next_state(state, t, m)
            if new != state:
                state, since, changed = new, d, True
    return state, since, changed


def state_now(states):
    """(state, the month-end it began, True when it began by a change that came after an earlier
    known state). ("not known", None, False) with no known state."""
    if not states:
        return "not known", None, False
    now = states[-1][1]
    i = len(states) - 1
    while i > 0 and states[i - 1][1] == now:
        i -= 1
    return now, states[i][0], i > 0


# ---------------------------------------------------------------- events: payouts, bonus, rights, splits

def lost_between(a, b, missing):
    """True when a session with no closing file lies between dates a and b."""
    return any(a < m < b for m in missing or ())


def payout_facts(bars, missing=()):
    """Cash payouts seen in the closing files, oldest first: [{"date", "amount", "flags"}]. On the
    first day a new XD flag shows and the LDCP is not the close before, the payout is the close
    before less the LDCP. With XB or XR the same day, or a missing file between, the amount is not
    known (None). Shown as facts only: no rule uses them."""
    out = []
    for i in range(1, len(bars or [])):
        cur, before = bars[i], bars[i - 1]
        flags = cur.get("flags") or []
        if "XD" not in flags or "XD" in (before.get("flags") or []):
            continue
        ldcp, close = cur.get("ldcp"), before.get("close")
        if not ldcp or not close or abs(ldcp - close) <= CENT:
            continue
        known = not ("XB" in flags or "XR" in flags or lost_between(before["date"], cur["date"], missing))
        if known and ldcp > close:
            continue
        out.append({"date": cur["date"], "amount": cents(close - ldcp) if known else None, "flags": list(flags)})
    return out


def share_events(bars, prev, missing=(), split_pct=SPLIT_PCT):
    """Bonus, right and split events, oldest first: [{"date", "kind": "XB", "XR" or "split",
    "factor"}]. A flag is new when the bar before has no such flag. factor is LDCP / the close
    before (None across a missing file). A split is an LDCP change of split_pct (10%) or more
    with no flag; a smaller one is never called a split."""
    out = []
    for i in range(1, len(bars or [])):
        cur, before = bars[i], bars[i - 1]
        ldcp, close = cur.get("ldcp"), before.get("close")
        lost = lost_between(before["date"], cur["date"], missing)
        f = ldcp / close if ldcp and close and not lost else None
        flags, old = cur.get("flags") or [], before.get("flags") or []
        for kind in ("XB", "XR"):
            if kind in flags and kind not in old:
                out.append({"date": cur["date"], "kind": kind, "factor": f})
        if not flags and f and abs(f - 1) * 100 >= split_pct - 1e-9 and prev.get(cur["date"]) == before["date"]:
            out.append({"date": cur["date"], "kind": "split", "factor": f})
    return out


def scale_lines(bars, prev, missing=()):
    """Explain lines for every LDCP change with no payout: what was scaled, and what was not."""
    out = []
    for i in range(1, len(bars or [])):
        cur, before = bars[i], bars[i - 1]
        ldcp, close = cur.get("ldcp"), before.get("close")
        flags = cur.get("flags") or []
        if not ldcp or not close or abs(ldcp - close) <= CENT or flags == ["XD"]:
            continue
        day, f = day_label(cur["date"]), ldcp / close
        if lost_between(before["date"], cur["date"], missing):
            out.append("On %s the reference price is %s off the close before, but a closing file before it is "
                       "missing, so nothing is scaled there." % (day, pct_text(pct_change(ldcp, close))))
        elif "XB" in flags or "XR" in flags:
            kind = "XB" if "XB" in flags else "XR"
            out.append("Prices before %s are scaled by %.2f: %s on that day." % (day, f, KIND_WORDS[kind]))
        elif flags:
            out.append("Prices before %s are scaled by %.2f: an ex-date (%s) on that day." % (day, f, "".join(flags)))
        elif prev.get(cur["date"]) == before["date"] and abs(f - 1) * 100 >= SPLIT_PCT - 1e-9:
            out.append("Prices before %s are scaled by %.2f: a split on that day (the LDCP changed by 10%% or more "
                       "with no payout flag)." % (day, f))
        elif prev.get(cur["date"]) == before["date"]:
            out.append("Prices before %s are scaled by %.2f: PSX changed the reference price that day with no "
                       "flag." % (day, f))
        else:
            out.append("On %s PSX moved the reference price by %+.2f%% after days with no trade. %s"
                       % (day, pct_change(ldcp, close), NO_MOVE))
    return out


def right_letters(sym, mk):
    """The right letters of a share in the closing files: the symbol plus R, R1 or R2 that
    symbol_kind() calls "right" -> [{"symbol", "first", "last", "now"}], oldest first."""
    seen = {}
    for d, rows in mk["sessions"]:
        for suffix in ("R", "R1", "R2"):
            r = rows.get(sym + suffix)
            if r and symbol_kind(sym + suffix, r.get("sector"), r.get("short_name")) == "right":
                seen.setdefault(sym + suffix, {"symbol": sym + suffix, "first": d})["last"] = d
    out = sorted(seen.values(), key=lambda x: x["first"])
    for x in out:
        x["now"] = x["last"] == mk["newest"]
    return out


def make_cause(keys, kind, rule, ack, words, short):
    """A reason for a REVIEW or a TRIM. It is answered (acked) when every key is acked."""
    return {"keys": list(keys), "kind": kind, "rule": rule, "ack": ack, "words": words, "short": short}


def data_causes(sym, bars, mk, rules, events, ref, ref_words):
    """Rule 8: a missing file next to an LDCP jump of 2% or more, no close for stale_trade_sessions
    sessions, or a bonus, a right issue with a lower LDCP or a split after `ref` (as_of, else the
    watch start: the share count may be old)."""
    if not bars:
        return [make_cause(["noclose"], "data", 8, "keep", "Data: no close for %s in the closing files. Check "
                           "the symbol and the PSX notices." % sym, "data: no close in the closing files (rule 8)")]
    out = []
    for i in range(1, len(bars)):
        before, cur = bars[i - 1], bars[i]
        gap = [m for m in mk["missing"] if before["date"] < m < cur["date"]]
        ldcp, close = cur.get("ldcp"), before.get("close")
        if gap and ldcp and close and abs(ldcp / close - 1) * 100 >= JUMP_PCT - 1e-9:
            out.append(make_cause(["gap:" + gap[0].isoformat()], "data", 8, "keep",
                                  "Data: no closing file for %s. A payout or split there would be hidden. Run "
                                  "backfill or save the file." % day_label(gap[0]),
                                  "data: no closing file for %s (rule 8)" % day_label(gap[0])))
    last = bars[-1]["date"]
    if len([d for d in mk["dates"] if d > last]) >= rules["stale_trade_sessions"]:
        out.append(make_cause(["stale:" + last.isoformat()], "data", 8, "keep",
                              "Data: no close for %s since %s. Check the PSX notices." % (sym, day_label(last)),
                              "data: no close since %s (rule 8)" % day_label(last)))
    for e in events:
        if (ref is not None and e["date"] <= ref) or (e["kind"] == "XR" and e["factor"] and e["factor"] >= 1):
            continue
        more = " Bonus shares come a few days later." if e["kind"] == "XB" else ""
        out.append(make_cause(["count:%s:%s" % (e["kind"], e["date"].isoformat())], "data", 8, "checked",
                              "Data: %s on %s, %s. Check your shares in psx/portfolio.json against your broker "
                              "app.%s No new money until you check." % (KIND_WORDS[e["kind"]], day_label(e["date"]),
                                                                        ref_words, more),
                              "data: %s on %s: check your shares (rule 8)" % (KIND_WORDS[e["kind"]], day_label(e["date"]))))
    return out


def rights_cause(sym, events, letters, ref):
    """Rule 10: an XR of a holding after `ref`, or a right letter of it that first shows after `ref`
    or trades now -> one REVIEW (rights) cause, or None. It never says which choice to make."""
    xrs = [e for e in events if e["kind"] == "XR" and (ref is None or e["date"] > ref)]
    lets = [x for x in letters if x["now"] or ref is None or x["first"] > ref]
    if not xrs and not lets:
        return None
    if xrs:
        text = "Rights: %s went ex-right (XR) on %s." % (sym, day_label(xrs[-1]["date"]))
        if lets and lets[-1]["now"]:
            text += " A right letter, %s, trades now." % lets[-1]["symbol"]
        elif lets:
            text += " A right letter, %s, traded from %s to %s." % (lets[-1]["symbol"], day_label(lets[-1]["first"]),
                                                                  day_label(lets[-1]["last"]))
        else:
            text += " No right letter is in the closing files yet."
    elif lets[-1]["now"]:
        text = "Rights: a right letter of %s, %s, trades now." % (sym, lets[-1]["symbol"])
    else:
        text = "Rights: a right letter of %s, %s, traded from %s to %s." % (
            sym, lets[-1]["symbol"], day_label(lets[-1]["first"]), day_label(lets[-1]["last"]))
    text += (" Pay for the right shares, or sell the right letter, by the date in the company's notice. If you do "
             "neither, the right is lost. You decide.")
    keys = ["rights:XR:" + e["date"].isoformat() for e in xrs] + ["rights:letter:" + x["symbol"] for x in lets]
    short = "rights: %s" % ("%s trades now" % lets[-1]["symbol"] if lets and lets[-1]["now"]
                            else "ex-right (XR) on %s" % day_label(xrs[-1]["date"]) if xrs
                            else "a right letter, %s" % lets[-1]["symbol"]) + " (rule 10)"
    return make_cause(keys, "rights", 10, "checked", text, short)


def num_text(v):
    """25.0 -> "25", 2.5 -> "2.5"."""
    return ("%d" % v) if abs(v - round(v)) < 1e-9 else ("%g" % v)


def profit_facts(sym, row, rules, today):
    """Rule 7 from the psx/metrics.json row (psx-analyst): (fact lines, a REVIEW cause or None).
    Profit after tax only, never EPS, and only when psx-analyst's label_ok is true."""
    if row is None:
        return ["Results: no row in psx/metrics.json. Run psx-analyst: facts.py %s --metrics psx/metrics.json."
                % sym], None
    lines, cause, q = [], None, str(row.get("quarter") or "the newest quarter")
    if row.get("label_ok") is not True:
        lines.append("Results: the quarter label looks wrong (psx-analyst). The profit rule waits until it is "
                     "checked.")
    else:
        profit, growth, line = _f(row.get("profit_000")), _f(row.get("profit_growth_yoy")), rules["results_drop_pct"]
        if profit is not None and profit < 0:
            cause = make_cause(["profit:" + q], "review", 7, "keep", "Profit: a loss in %s. The rule asks for a "
                               "look. Read the results." % q, "a loss in %s (rule 7)" % q)
        elif growth is not None and growth <= -line + 1e-9:
            cause = make_cause(["profit:" + q], "review", 7, "keep",
                               "Profit: %s on the same quarter a year back (%s). The review line is -%s%%. Read the "
                               "results." % (pct_text(growth), q, num_text(line)),
                               "profit %s on the same quarter a year back (rule 7)" % pct_text(growth))
        elif growth is not None:
            lines.append("Profit: %s on the same quarter a year back (%s). The review line is -%s%%."
                         % (pct_text(growth), q, num_text(line)))
        else:
            lines.append("Profit: %s has no same quarter a year back in psx/metrics.json, so the rule cannot check "
                         "it." % q)
    try:
        board = date_of(row.get("next_board_meeting"), "next_board_meeting")
    except InvestError:
        board = None
    if board and board >= today:
        lines.append("Board meeting on %s (psx/metrics.json). Results can move the price." % day_label(board))
    return lines, cause


# ---------------------------------------------------------------- one share's facts

def reference_date(ctx, mk):
    """(date, words): a bonus, right or split after this date can mean an old share count in
    psx/portfolio.json. It is "as_of" there, else the watch start, else the newest month-end."""
    if ctx["pf"]["as_of"]:
        d = ctx["pf"]["as_of"]
        return d, "after your shares were checked (as_of %s)" % day_label(d)
    watch = date_of(ctx["state"].get("watch_start"), "watch_start in the state")
    if watch:
        return watch, "after the watch began (%s)" % day_label(watch)
    if mk["month_end"]:
        return mk["month_end"], "after the newest month-end (%s)" % day_label(mk["month_end"])
    return None, "in the closing files"


def share_facts(sym, mk, ctx, ref, ref_words):
    """Everything the rules need about one share, from the closing files and psx/metrics.json."""
    rules = ctx["rules"]
    bars = daily_bars(sym, mk["sessions"])
    prev = split_map(bars, mk["prev_of"])
    adj = adjusted_closes(bars, prev, mk["missing"])
    events = share_events(bars, prev, mk["missing"])
    shares = ctx["pf"]["holdings"].get(sym, 0)
    f = {"symbol": sym, "held": sym in ctx["pf"]["holdings"], "shares": shares, "bars": bars, "prev": prev,
         "adj": adj, "events": events, "close": None, "close_date": None, "value": None, "sector_code": None}
    if bars:
        f["close"], f["close_date"] = bars[-1]["close"], bars[-1]["date"]
        f["value"] = shares * bars[-1]["close"]
    for _, rows in reversed(mk["sessions"]):
        if sym in rows:
            f["sector_code"] = str(rows[sym].get("sector") or "").strip() or None
            break
    code = f["sector_code"]
    f["sector"] = rules["group_of"].get(code) or SECTORS.get(code) or code or "sector not known"
    f["month_closes"] = month_closes(bars, adj, mk["ends"])
    closes = [c for _, c in f["month_closes"]]
    line, gap, tv = trend_vote(closes, rules["trend_months"], rules["add_band_pct"], rules["down_band_pct"])
    change, mv = momentum_vote(closes, rules["momentum_months"], rules["add_band_pct"], rules["down_band_pct"])
    f["trend"] = {"line": round(line, 2) if line is not None else None, "gap_pct": gap, "vote": tv}
    f["momentum"] = {"change_pct": change, "vote": mv}
    votes = vote_history(f["month_closes"], rules)
    f["states"] = break_states(votes)
    f["state"], f["since"], f["changed"] = state_now(f["states"])
    kept = walk_on(saved_break(ctx["state"], sym), votes)      # the saved state, walked on (3.4)
    if kept:
        f["state"], f["since"], f["changed"] = kept
    f["walked"] = next((d for d, t, m in reversed(votes) if t is not None and m is not None), None)
    f["state_new"] = f["changed"] and f["since"] == mk["month_end"]
    f["payouts"] = payout_facts(bars, mk["missing"])
    f["letters"] = right_letters(sym, mk)
    held_events = events if f["held"] else []          # rules 8 and 10 look at the shares you hold
    f["data"] = data_causes(sym, bars, mk, rules, held_events, ref, ref_words)
    f["rights"] = rights_cause(sym, held_events, f["letters"], ref) if f["held"] else None
    f["profit_lines"], f["profit"] = profit_facts(sym, ctx["metrics"].get(sym), rules, ctx["today"])
    return f


def fair_shares(syms, rules):
    """{SYM: fair share %}: the share's "targets" value in psx/invest.json, else 100% / (holdings +
    candidates). Never over max_weight_pct."""
    out = {}
    for s in syms:
        v = rules["targets"][s] if s in rules["targets"] else (100.0 / len(syms) if syms else 0.0)
        out[s] = min(v, rules["max_weight_pct"])
    return out


def half_up(x):
    """A whole number, half up (Python's round() would take 2.5 to 2)."""
    return int(Decimal(str(x)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def trim_shares(value, cap_pct, total, price, shares):
    """The whole shares to sell to bring a share back to cap_pct of the long-term money:
    ceil((value - cap x M) / price), at most the shares held."""
    over = Decimal(str(value)) - Decimal(str(cap_pct)) / 100 * Decimal(str(total))
    if over <= 0 or not price:
        return 0
    q = (over / Decimal(str(price))).quantize(Decimal(1), rounding=ROUND_CEILING)
    return int(min(q, Decimal(str(shares))))


def break_shares(shares, pct):
    """The whole shares a break TRIM sells: shares x break_trim_pct / 100, half up, at least 1."""
    return int(min(shares, max(1, half_up(Decimal(str(shares)) * Decimal(str(pct)) / 100))))


def trim_record(state, sym, key):
    """The state's TRIM record with this key, or None."""
    for rec in (state.get("trims") or {}).get(sym) or []:
        if isinstance(rec, dict) and rec.get("key") == key:
            return rec
    return None


def open_size_record(state, sym):
    """The newest size TRIM record of a share that is not closed, or None."""
    recs = [r for r in (state.get("trims") or {}).get(sym) or []
            if isinstance(r, dict) and r.get("kind") == "size" and not r.get("closed")]
    return recs[-1] if recs else None


def trim_done(rec, shares):
    """True when the shares in psx/portfolio.json dropped by the TRIM amount since its signal."""
    return bool(rec) and shares <= (_f(rec.get("shares_at")) or 0) - (_f(rec.get("amount")) or 0) + 1e-9


def trim_left(rec, shares):
    """A saved TRIM after a sale: (the shares it still asks for, the shares it asked for, the drop
    in psx/portfolio.json since it was saved). What is left is the amount less the drop, never
    under 0 and never more than the shares held. 0 left is a TRIM that is done (trim_done)."""
    amount = int(round(_f(rec.get("amount")) or 0))
    sold = max(0, int(round((_f(rec.get("shares_at")) or 0) - shares)))
    return max(0, min(amount - sold, int(shares))), amount, sold


def trim_day(rec):
    """The day a TRIM was saved, in words (its "date"), or None."""
    try:
        d = date_of(rec.get("date"), "a TRIM date")
    except InvestError:
        return None
    return day_label(d) if d else None


def acks_of(state, sym):
    """(the keys your acks of a share cover, its acks newest last)."""
    recs = [r for r in (state.get("acks") or {}).get(sym) or [] if isinstance(r, dict)]
    keys = set()
    for r in recs:
        keys |= set(r.get("covers") or [])
    return keys, recs


def ack_line(rec):
    """The HOLD line of an ack."""
    when = day_label(date_of(rec.get("date"), "an ack date"))
    if rec.get("choice") == "checked":
        return "HOLD. You checked your shares on %s." % when
    word = "trim" if rec.get("choice") == "trimmed" else "keep"
    note = str(rec.get("note") or "").strip()
    return "HOLD. You chose %s on %s%s." % (word, when, (': "%s"' % note) if note else "")


# ---------------------------------------------------------------- the signal (3.4)

def vote_lines(f, mk, rules):
    """The trend and 12-month reason lines (3.5)."""
    t, m, edges = f["trend"], f["momentum"], (rules["add_band_pct"], rules["down_band_pct"])
    folder, own, need_t, need_m = len(mk["ends"]), len(f["month_closes"]), rules["trend_months"], \
        rules["momentum_months"] + 1
    up, down = num_text(rules["add_band_pct"]), num_text(rules["down_band_pct"])
    if t["vote"] is None:
        tw = (NOT_KNOWN % ("Trend", need_t, folder) if folder < need_t
              else SHARE_NOT_KNOWN % ("Trend", need_t, f["symbol"], own))
    elif t["vote"] == "up":
        tw = "Trend: %s over its %d-month line. The rule wants %s%% or more. Vote: up." % (
            pct_text(t["gap_pct"], False, edges), need_t, up)
    elif t["vote"] == "down":
        tw = "Trend: %s under its %d-month line. The rule says %s%% or more under. Vote: down." % (
            pct_text(abs(t["gap_pct"]), False, edges), need_t, down)
    else:
        tw = "Trend: %s from its %d-month line. Too close for a vote." % (pct_text(t["gap_pct"], False, edges), need_t)
    if m["vote"] is None:
        mw = (NOT_KNOWN % ("12-month change", need_m, folder) if folder < need_m
              else SHARE_NOT_KNOWN % ("12-month change", need_m, f["symbol"], own))
    else:
        mw = "12-month change: %s, payouts counted. %s" % (
            pct_text(m["change_pct"], True, edges),
            {"up": "Vote: up.", "down": "Vote: down.", "none": "Too small for a vote."}[m["vote"]])
    return tw, mw


def trim_words(pct):
    """break_trim_pct in words: "half", "all", "30%"."""
    return {50.0: "half", 100.0: "all"}.get(float(pct), "%s%%" % num_text(pct))


def sold_words(part, ask):
    """A TRIM after part of it was sold: what the rule asked, the drop since, and the rest.
    part: {"amount", "sold", "left", "day"}."""
    return ("On %s the rule asked to sell %s. Your shares dropped by %s since then. The rule says: sell the rest, "
            "%s shares. You decide. Nothing is placed." % (part["day"] or "the day it was saved", ask,
                                                          whole(part["sold"]), whole(part["left"])))


def size_line(f, rules, at_month_end, trim_qty, part=None):
    """Rule 5 in words. part: a TRIM that is sold in part (sold_words)."""
    cap, line = rules["max_weight_pct"], rules["max_weight_pct"] + rules["trim_band_pct"]
    w = pct_text(f["weight"], False, (cap, line))
    if trim_qty and part:
        return "Size: %s. The trim line is %s%%. %s" % (w, num_text(line), sold_words(
            part, "%s shares, back to %s%%" % (whole(part["amount"]), num_text(cap))))
    if trim_qty:
        return ("Size: %s. The trim line is %s%%. The rule says: sell %s shares, back to %s%%. You decide. Nothing "
                "is placed." % (w, num_text(line), whole(trim_qty), num_text(cap)))
    if not f["held"]:
        return "Size: %s of your long-term money (not held). The cap is %s%%." % (w, num_text(cap))
    text = "Size: %s of your long-term money. The cap is %s%%." % (w, num_text(cap))
    if f["weight"] > cap + 1e-9:
        text += " No new money while it is over."
        if f["weight"] > line + 1e-9 and not at_month_end:
            text += " The %s%% trim line is checked at the month-end." % num_text(line)
    return text


def sector_line(f, rules):
    """Rule 6 in words."""
    cap, line = rules["max_sector_pct"], rules["max_sector_pct"] + rules["sector_band_pct"]
    w = pct_text(f["sector_pct"], False, (cap, line))
    if f["sector_pct"] > line + 1e-9:
        return "Sector %s: %s. The review line is %s%%. Please look at the mix." % (f["sector"], w, num_text(line))
    text = "Sector %s: %s. The cap is %s%%." % (f["sector"], w, num_text(cap))
    if f["sector_pct"] > cap + 1e-9:
        text += " No new money in this sector while it is over."
    return text


def pick_signal(f, ctx, mk, total, sector_values):
    """The first case that fits is the signal (3.4): REVIEW (data), REVIEW (rights), TRIM, REVIEW,
    ADD, else HOLD. Sets f["signal"], f["line"], f["summary"], f["reasons"] and f["trim"]."""
    rules, state, sym = ctx["rules"], ctx["state"], f["symbol"]
    acked, ack_recs = acks_of(state, sym)

    def is_open(c):
        return c is not None and not c.get("closed") and not all(k in acked for k in c["keys"])

    reasons = []

    def say(rule, met, words):
        reasons.append({"rule": rule, "met": met, "words": words})

    tw, mw = vote_lines(f, mk, rules)
    tv, mv = f["trend"]["vote"], f["momentum"]["vote"]
    say("trend", tv in ("up", "down"), tw)
    say("12-month", mv in ("up", "down"), mw)
    watch = date_of(state.get("watch_start"), "watch_start in the state")
    pct, since = rules["break_trim_pct"], f["since"]
    break_trim = break_review = size_trim = None
    if f["state"] == "out":
        after = watch is not None and f["changed"] and since > watch
        key = "trim:break:" + since.isoformat()
        rec = trim_record(state, sym, key)
        turned = "Break: both votes turned down at %s." % end_of_month(since)
        choice = "sell all" if pct >= 100 else ("trim" if pct <= 0 else "trim " + trim_words(pct))
        if after and pct > 0 and f["held"]:
            qty, part = break_shares(f["shares"], pct), None
            if rec:                              # a saved TRIM: only what is left of it after a sale
                left, amount, sold = trim_left(rec, f["shares"])
                qty = left or amount
                if sold and left:
                    part = {"amount": amount, "sold": sold, "left": left, "day": trim_day(rec)}
            if part:
                words = turned + " " + sold_words(part, "%s (%s shares)" % (trim_words(pct), whole(part["amount"])))
                short = "break: both votes turned down at %s, %s of %s shares left (rule 3)" % (
                    end_of_month(since), whole(qty), whole(part["amount"]))
            else:
                words = turned + " The rule says: sell %s (%s shares). You decide. Nothing is placed." % (
                    trim_words(pct), whole(qty))
                short = "break: both votes turned down at %s (rule 3)" % end_of_month(since)
            break_trim = make_cause([key], "trim", 3, "keep", words, short)
            break_trim["qty"], break_trim["part"], break_trim["day"] = qty, part, trim_day(rec) if rec else None
            break_trim["closed"] = bool(rec and (rec.get("closed") or trim_done(rec, f["shares"])))
            say("break", True, break_trim["words"] if is_open(break_trim) else turned)
        elif after and pct <= 0:
            break_review = make_cause(["break:" + since.isoformat()], "review", 3, "keep", turned + " break_trim_pct "
                                      "is 0 in psx/invest.json, so the rule asks for a look. You choose: keep, or "
                                      "trim.", "break: both votes turned down at %s (rule 3)" % end_of_month(since))
        elif not after and f["held"] and watch is not None and since > watch:
            # the first month-end with both votes known came after the watch began: the files do not say
            # when the break began, so it is not called "before the watch"
            break_review = make_cause(["break:" + since.isoformat()], "review", 3, "keep",
                                      "Both votes have been down since %s, the first month-end with both votes "
                                      "known. The files do not show when the break began. You choose: keep, or %s."
                                      % (day_label(since), choice),
                                      "both votes down since %s, the first month-end with both votes known (rule 3)"
                                      % day_label(since))
        elif not after and f["held"]:
            break_review = make_cause(["break:" + since.isoformat()], "review", 3, "keep",
                                      "Both votes have been down since %s. That was before the watch began. You "
                                      "choose: keep, or %s." % (day_label(since), choice),
                                      "both votes down since %s, before the watch began (rule 3)" % day_label(since))
        if break_review:
            say("break", True, break_review["words"])
        say("break", True, "Out since %s: both votes were down. No new money while out." % day_label(since))
    elif f["state"] == "in" and f["state_new"]:
        say("back in", True, "Back in at %s: no vote is down and %s up. New money is allowed again."
            % (end_of_month(since), "both are" if (tv, mv) == ("up", "up") else "one is"))
    elif f["state"] == "in":
        say("break", False, "State: in since %s. No break." % day_label(since))
    else:
        say("break", False, "State: not known yet. The break rule needs both votes.")
    cap, line = rules["max_weight_pct"], rules["max_weight_pct"] + rules["trim_band_pct"]
    if f["held"] and f["close"]:
        key, rec, qty, part = None, open_size_record(state, sym), 0, None
        if rec and trim_done(rec, f["shares"]):
            rec = None
        if rec:                                  # a TRIM from an earlier month-end that is not done yet
            key = rec.get("key")
        elif mk["at_month_end"] and f["weight"] > line + 1e-9:
            key = "trim:size:" + mk["month_end"].isoformat()
            rec = trim_record(state, sym, key)
            if not rec:
                qty = trim_shares(f["value"], cap, total, f["close"], f["shares"])
        if rec:                                  # a saved TRIM: only what is left of it after a sale
            left, amount, sold = trim_left(rec, f["shares"])
            qty = left or amount
            if sold and left:
                part = {"amount": amount, "sold": sold, "left": left, "day": trim_day(rec)}
        if key and qty > 0:
            if part:
                short = "TRIM from %s: %s of %s shares left (rule 5)" % (
                    part["day"] or "an earlier day", whole(qty), whole(part["amount"]))
            elif f["weight"] > line + 1e-9:
                short = "over the %s%% trim line (rule 5)" % num_text(line)
            else:                                # saved at a month-end; the size moved since then
                short = "TRIM from %s: %s shares (rule 5)" % (trim_day(rec) or "an earlier day", whole(qty))
            size_trim = make_cause([key], "trim", 5, "keep", "", short)
            size_trim["qty"], size_trim["part"], size_trim["day"] = qty, part, trim_day(rec) if rec else None
            size_trim["closed"] = bool(rec and (rec.get("closed") or trim_done(rec, f["shares"])))
    size_text = size_line(f, rules, mk["at_month_end"], size_trim["qty"] if is_open(size_trim) else 0,
                          size_trim["part"] if is_open(size_trim) else None)
    if size_trim:
        size_trim["words"] = size_text
    say("size", is_open(size_trim) or f["weight"] > cap + 1e-9, size_text)
    sector_review = None
    if f["sector_pct"] > rules["max_sector_pct"] + rules["sector_band_pct"] + 1e-9:
        sector_review = make_cause(["sector:" + f["sector"]], "review", 6, "keep", sector_line(f, rules),
                                   "sector %s %s, over the %s%% review line (rule 6)" % (
                                       f["sector"], pct_text(f["sector_pct"], False),
                                       num_text(rules["max_sector_pct"] + rules["sector_band_pct"])))
    say("sector", f["sector_pct"] > rules["max_sector_pct"] + 1e-9, sector_line(f, rules))
    for text in f["profit_lines"]:
        say("profit", False, text)
    if f["profit"]:
        say("profit", True, f["profit"]["words"])
    for c in f["data"]:
        say("data", is_open(c), c["words"])
    blocked = sym in ctx["blocked"]
    if blocked:
        say("never add", True, "This share is on your never-add list. No new money.")
    if f["rights"]:
        say("rights", is_open(f["rights"]), f["rights"]["words"])
    f["reasons"], f["trim"] = reasons, None
    open_data = [c for c in f["data"] if is_open(c)] + ([f["rights"]] if is_open(f["rights"]) else [])
    trims = [c for c in (break_trim, size_trim) if c is not None and is_open(c)]
    reviews = [c for c in (break_review, f["profit"], sector_review) if is_open(c)]
    present = [c for c in f["data"] + [f["rights"], break_trim, break_review, size_trim, f["profit"], sector_review]
               if c is not None]
    in_effect = [r for r in ack_recs if any(k in (r.get("covers") or []) for c in present for k in c["keys"])]
    f["ack"] = in_effect[-1] if in_effect else None
    f["trim_causes"], f["present"] = trims, present
    f["open_causes"] = open_data + trims + reviews
    if f["ack"]:
        say("ack", True, ack_line(f["ack"])[6:])
    if open_data:
        f["signal"], f["summary"] = "REVIEW", "; ".join(c["short"] for c in open_data)
    elif trims:
        big = max(trims, key=lambda c: c["qty"])
        f["signal"], f["summary"], f["trim"] = "TRIM", big["short"], {"qty": big["qty"], "rule": big["rule"],
                                                                         "short": big["short"], "part": big["part"],
                                                                         "day": big["day"]}
    elif reviews:
        f["signal"], f["summary"] = "REVIEW", "; ".join(c["short"] for c in reviews)
    else:
        f["signal"], f["summary"] = add_or_hold(f, ctx, total, sector_values, blocked)
    f["line"] = SIGNAL_LINES[f["signal"]]
    if f["signal"] == "HOLD" and f["ack"]:
        f["line"] = ack_line(f["ack"])
    return f


def add_or_hold(f, ctx, total, sector_values, blocked):
    """Case 4 or 5 of 3.4: ("ADD", why) when every add rule is met, else ("HOLD", why)."""
    rules = ctx["rules"]
    tv, mv = f["trend"]["vote"], f["momentum"]["vote"]
    words = {"up": "up", "down": "down", "none": "no vote", None: "not known"}
    votes = "trend %s, 12-month %s" % (words[tv], words[mv])
    cap, sector_cap = rules["max_weight_pct"], rules["max_sector_pct"]
    room_ok = sector_ok = False
    if f["close"] and total > 0:
        qmin = max(1, int(math.ceil(float(setting(ctx["cfg"], "min_order_pkr") or 0) / f["close"] - 1e-9)))
        after = (f["value"] or 0) + qmin * f["close"]
        room_ok = after <= f["fair"] / 100 * total + CENT and after <= cap / 100 * total + CENT
        sector_ok = sector_values.get(f["sector"], 0) + qmin * f["close"] <= sector_cap / 100 * total + CENT
    if f["ack"]:
        why = ("you checked your shares on %s" if f["ack"].get("choice") == "checked" else "you chose %s on %%s"
               % ("trim" if f["ack"].get("choice") == "trimmed" else "keep")) % day_label(
            date_of(f["ack"].get("date"), "an ack date"))
        if f["state"] == "out":
            return "HOLD", why + "; out since %s (rule 3)" % day_label(f["since"])
        if not (f["state"] == "in" and "down" not in (tv, mv) and "up" in (tv, mv) and not blocked and room_ok
                and sector_ok):
            return "HOLD", why
    if f["state"] == "out":
        return "HOLD", "out since %s: no new money while out (rule 3)" % day_label(f["since"])
    if f["state"] == "not known" or tv is None:
        return "HOLD", "%s: the add rules need both votes (rules 1, 2)" % votes
    if blocked:
        return "HOLD", "on your never-add list (rule 9)"
    if not f["close"]:
        return "HOLD", "no close in the closing files"
    if f["weight"] > cap + 1e-9:
        return "HOLD", "over the %s%% cap (rule 5)" % num_text(cap)
    if f["sector_pct"] > sector_cap + 1e-9 or not sector_ok:
        return "HOLD", "%s: sector %s %s its %s%% cap (rule 6)" % (
            votes, f["sector"], "over" if f["sector_pct"] > sector_cap + 1e-9 else "at", num_text(sector_cap))
    if "down" in (tv, mv):
        return "HOLD", "%s: no new money while a vote is down (rules 1, 2)" % votes
    if "up" not in (tv, mv):
        return "HOLD", "%s: the add rules need one vote up (rules 1, 2)" % votes
    if not room_ok:
        return "HOLD", "%s; at its fair share (rule 5)" % votes
    ups = [("trend up (%s)" % pct_text(f["trend"]["gap_pct"])) if tv == "up" else None,
           ("12-month up (%s)" % pct_text(f["momentum"]["change_pct"])) if mv == "up" else None]
    return "ADD", "no vote down, %s, room to its %s fair share (rules 1, 2, 5)" % (
        ", ".join(x for x in ups if x), pct_text(f["fair"], False))


# ---------------------------------------------------------------- the account and the money

def weights(facts, cash, rules):
    """The long-term money M (each holding at its last close, plus the cash) -> (M, the value of each
    sector or sector group). Sets each share's fair share, weight and sector weight, in percent."""
    total = sum(f["value"] for f in facts if f["held"] and f["value"]) + cash
    fair = fair_shares([f["symbol"] for f in facts], rules)
    sectors = {}
    for f in facts:
        sectors[f["sector"]] = sectors.get(f["sector"], 0.0) + (f["value"] or 0.0)
    for f in facts:
        f["fair"] = fair[f["symbol"]]
        f["weight"] = 100.0 * (f["value"] or 0.0) / total if total > 0 else 0.0
        f["sector_pct"] = 100.0 * sectors[f["sector"]] / total if total > 0 else 0.0
    return total, sectors


def evaluate(ctx, mk, new_cash=0.0):
    """Every holding and candidate: its facts, its weight in the long-term money, then its signal.
    new_cash is money not recorded yet (a what-if of `money N`). -> (facts, account)."""
    rules, pf = ctx["rules"], ctx["pf"]
    syms = list(pf["holdings"]) + [c for c in rules["candidates"] if c not in pf["holdings"]]
    ref, ref_words = reference_date(ctx, mk)
    facts = [share_facts(s, mk, ctx, ref, ref_words) for s in syms]
    total, sectors = weights(facts, pf["cash"] + new_cash, rules)
    for f in facts:
        pick_signal(f, ctx, mk, total, sectors)
    ctx["total"], ctx["sectors"] = total, sectors
    held = total - pf["cash"] - new_cash
    return facts, {"holdings_pkr": cents(held), "cash_pkr": cents(pf["cash"]), "total_pkr": cents(total)}


def fresh_price(sym, doc, now_utc, delay=5, minutes=FRESH_MINUTES):
    """sym's price in psx/prices.json (psx-alerts writes it) when it is fresh: its data time is at
    most `minutes` old and not over a minute after now. -> (price, data time) or (None, None).
    The data time is the quote's own time; without it, the fetch time less the data delay."""
    if not isinstance(doc, dict) or not isinstance(doc.get("prices"), dict):
        return None, None
    p = _f(doc["prices"].get(sym))
    if p is None or p <= 0:
        return None, None
    detail = doc.get("detail") if isinstance(doc.get("detail"), dict) else {}
    det = detail.get(sym) if isinstance(detail.get(sym), dict) else {}
    t = parse_utc(det.get("time"))
    if t is None:
        got = parse_utc(det.get("fetched")) or parse_utc(doc.get("as_of"))
        t = got - datetime.timedelta(minutes=_f(delay) or 0) if got else None
    if t is None:
        return None, None
    age = (now_utc - t).total_seconds()
    if age < -60 or age > minutes * 60:
        return None, None
    return p, t


def price_for(f, ctx):
    """(price, "fresh" or "close") for an action: a fresh price in the session, else the last close."""
    if ctx["open"]:
        p, _ = fresh_price(f["symbol"], ctx["prices"], ctx["now_utc"], setting(ctx["cfg"], "data_delay_minutes"))
        if p:
            return round_half_up_tick(p), "fresh"
    return f["close"], "close"


def price_note(where):
    """The words for the prices of a block of actions."""
    fresh = sorted(s for s, w in where.items() if w == "fresh")
    if not fresh:
        return "prices: the last close"
    if len(fresh) == len(where):
        return "prices: psx/prices.json, about 5 minutes late"
    return "prices: psx/prices.json for %s; the last close for the rest" % ", ".join(fresh)


def order_cost(price, qty, costs):
    """A buy of qty shares and its costs (leg_cost), in rupees as a Decimal. 0 for no shares."""
    if not qty:
        return Decimal(0)
    return Decimal(str(cents(qty * price))) + Decimal(str(leg_cost(price, qty, costs, True)))


def split_money(money_pkr, facts, ctx, prices):
    """How new money could go to the ADD shares (3.6) -> (buys, money left). One share at a time:
    it goes to the ADD share with the most room left (its fair share of M less its value) whose next
    share fits: the money left pays for it with the buy costs of the whole order, the share stays at
    or under its fair share and the cap, its sector at or under the sector cap, and the order at or
    under max_order_pkr. Ties: the lower weight, then the symbol. A slice under min_order_pkr is
    dropped and its money shared out again. At most max_trades_per_day buys."""
    rules, cfg, costs, total, sectors = ctx["rules"], ctx["cfg"], ctx["costs"], ctx["total"], ctx["sectors"]
    adds = [f for f in facts if f["signal"] == "ADD" and prices.get(f["symbol"])]
    min_order = float(setting(cfg, "min_order_pkr") or 0)
    max_order = _f(setting(cfg, "max_order_pkr"))
    max_buys = int(setting(cfg, "max_trades_per_day") or 0) or len(adds)
    budget, dropped = Decimal(str(cents(max(money_pkr, 0)))), set()
    while True:
        qty = dict((f["symbol"], 0) for f in adds)
        cost = dict((f["symbol"], Decimal(0)) for f in adds)
        spent, sector_add = Decimal(0), {}
        while True:
            best, best_key, started = None, None, len([s for s in qty if qty[s] > 0])
            for f in adds:
                s, p = f["symbol"], prices[f["symbol"]]
                q, value = qty[s] + 1, f["value"] or 0.0
                if s in dropped or (qty[s] == 0 and started >= max_buys):
                    continue
                if (max_order and q * p > max_order + CENT) or value + q * p > f["fair"] / 100 * total + CENT \
                        or value + q * p > rules["max_weight_pct"] / 100 * total + CENT:
                    continue
                if sectors.get(f["sector"], 0) + sector_add.get(f["sector"], 0) + p > \
                        rules["max_sector_pct"] / 100 * total + CENT:
                    continue
                if spent - cost[s] + order_cost(p, q, costs) > budget:
                    continue
                key = (-(f["fair"] / 100 * total - value - qty[s] * p), f["weight"], s)
                if best_key is None or key < best_key:
                    best, best_key = f, key
            if best is None:
                break
            s, p = best["symbol"], prices[best["symbol"]]
            new = order_cost(p, qty[s] + 1, costs)
            spent, cost[s], qty[s] = spent - cost[s] + new, new, qty[s] + 1
            sector_add[best["sector"]] = sector_add.get(best["sector"], 0) + p
        small = [f for f in adds if 0 < qty[f["symbol"]] * prices[f["symbol"]] < min_order - CENT]
        if not small:
            break
        dropped.add(min(small, key=lambda f: (qty[f["symbol"]] * prices[f["symbol"]], f["symbol"]))["symbol"])
    buys = []
    for f in adds:
        q, p = qty[f["symbol"]], prices[f["symbol"]]
        if q > 0:
            value = cents(q * p)
            buys.append({"symbol": f["symbol"], "qty": q, "price": p, "value": value,
                         "costs": leg_cost(p, q, costs, True), "fair_share_pct": round(f["fair"], 2),
                         "weight_before_pct": round(f["weight"], 2),
                         "weight_after_pct": round(100.0 * ((f["value"] or 0) + value) / total, 2) if total else 0.0})
    buys.sort(key=lambda b: (-b["value"], b["symbol"]))
    return buys, float(budget - spent)


def sell_line(a):
    """A TRIM's action line (3.5)."""
    return ("Could sell %s %s at %.2f: about %s, costs about %s. You decide. Nothing is placed."
            % (whole(a["qty"]), a["symbol"], a["price"], money(a["value"]), money(a["costs"])))


def buy_line(b):
    """An ADD buy's action line (3.5)."""
    return ("Could buy %s %s at %.2f: %s plus about %s costs. You decide. Nothing is placed."
            % (whole(b["qty"]), b["symbol"], b["price"], money(b["value"]), money(b["costs"])))


def trim_actions(facts, ctx):
    """The sells of every TRIM signal, at a fresh price in the session, else the last close."""
    out = []
    for f in facts:
        if f["signal"] != "TRIM" or not f["trim"]:
            continue
        p, where = price_for(f, ctx)
        q = f["trim"]["qty"]
        value = cents(q * p)
        out.append({"symbol": f["symbol"], "side": "sell", "qty": q, "price": p, "where": where, "value": value,
                    "costs": leg_cost(p, q, ctx["costs"], True), "rule": f["trim"]["rule"],
                    "short": f["trim"]["short"], "fair_share_pct": round(f["fair"], 2),
                    "weight_before_pct": round(f["weight"], 2),
                    "weight_after_pct": round(100.0 * ((f["value"] or 0) - value) / ctx["total"], 2)
                    if ctx["total"] else 0.0})
    return out


def new_money(money_pkr, facts, ctx):
    """(buys, money left, {SYM: "fresh" or "close"}) of splitting money_pkr over the ADD shares."""
    prices, where = {}, {}
    for f in facts:
        if f["signal"] == "ADD" and f["close"]:
            prices[f["symbol"]], where[f["symbol"]] = price_for(f, ctx)
    if money_pkr <= 0 or not prices:
        return [], cents(max(money_pkr, 0)), where
    buys, left = split_money(money_pkr, facts, ctx, prices)
    return buys, cents(left), where


# ---------------------------------------------------------------- one run

class NothingToShow(InvestError):
    """No holdings, or no closing files: the tool stops with exit code 1."""


def clock(text):
    """--now -> naive UTC (a time with no offset is Karachi time), with a plain error."""
    try:
        return parse_now(text)
    except ValueError as e:
        raise InvestError(str(e))


def writes(a):
    """True for a command that writes in this folder: signals --write, orders --write, cash, ack and
    backfill (also --forget-no-session; not --dry-run). They run only where psx/invest.json is,
    whatever --invest says."""
    return (a.cmd in ("cash", "ack") or (a.cmd in ("signals", "orders") and bool(a.write))
            or (a.cmd == "backfill" and not a.dry_run))


def load_context(a):
    """Everything a command reads, once: the rules, the config, the ledger, the state, the results."""
    ctx = {"a": a, "now_utc": clock(a.now)}
    ctx["now_pkt"] = to_pkt(ctx["now_utc"])
    ctx["today"] = ctx["now_pkt"].date()
    ctx["replay"] = bool(a.now)
    ctx["offline"] = bool(a.offline) or ctx["replay"]
    ctx["rules"] = load_rules(a.invest)
    if writes(a) and not os.path.isfile(INVEST_PATH):    # the folder guard: --invest never moves it (3.1)
        raise InvestError(NOT_HERE)
    ctx["other_rules"] = None
    if os.path.realpath(a.invest) != os.path.realpath(INVEST_PATH):
        ctx["other_rules"] = "Note: the rules come from %s, not from psx/invest.json in this folder." % a.invest
    ctx["cfg"] = load_config(a.config)
    ctx["costs"] = cost_settings(ctx["cfg"])
    ctx["paper"] = ctx["cfg"].get("paper", True) is not False        # only JSON false is paper off
    ctx["blocked"] = set(str(x).strip().upper() for x in setting(ctx["cfg"], "blocked") or [])
    ctx["pf"] = load_portfolio(a.portfolio)
    ctx["state"] = load_state(a.state)
    ctx["state_text"] = json.dumps(ctx["state"], sort_keys=True)
    ctx["metrics"], ctx["metrics_found"] = load_metrics(a.metrics)
    ctx["prices_note"] = None
    try:
        ctx["prices"] = read_json(a.prices, "the prices")
    except InvestError as e:                       # quotes only matter for an order in the session
        ctx["prices"], ctx["prices_note"] = None, "%s. The last close is used." % e
    try:
        ctx["market_now"] = market_state(ctx["now_pkt"], ctx["cfg"])
    except ValueError as e:
        raise InvestError(str(e))
    ctx["open"] = ctx["market_now"]["state"] == "open"
    return ctx


def load_market(ctx):
    """The closing files of the folder. With the site on (and not --offline or --now) it first
    fetches the trading days after the newest file, up to today. Never an older day."""
    a, cfg, notes = ctx["a"], ctx["cfg"], []
    n = ctx["rules"]["sessions"]
    if site_on(cfg) and not ctx["offline"]:
        newest = newest_file(a.data, ctx["today"], 3 * n + 10)
        if newest is not None and newest < ctx["today"]:
            saved, more = fetch_new_days(a.data, cfg, ctx["today"], newest)
            notes += more
            if saved:
                notes.append("Saved %d new closing file%s from the PSX site (to %s)."
                             % (len(saved), "" if len(saved) == 1 else "s", day_label(saved[-1])))
    mk = read_market(a.data, ctx["today"], cfg, ctx["rules"], ctx["state"].get("no_session"))
    mk["notes"] = notes
    if not mk["dates"]:
        raise NothingToShow("No closing files in %s. Save them there, or run backfill (PSX site on)."
                            % os.path.join(a.data, "mkt_summary"))
    return mk


def evaluate_folder(ctx, mk, new_cash=0.0):
    """evaluate() for the folder's holdings and candidates, or a plain stop when there are none."""
    if not ctx["pf"]["holdings"] and not ctx["rules"]["candidates"]:
        raise NothingToShow("No holdings in %s, and no candidates in %s." % (ctx["a"].portfolio, ctx["a"].invest))
    return evaluate(ctx, mk, new_cash)


def header_lines(ctx, mk, title):
    """The first lines of a report: when, the closes used, the month-end check."""
    now = ctx["now_pkt"]
    out = ["%s, %s %s PKT. Closes to %s (%d closing file%s from %s)." % (
        title, day_label(now), clock_label(now), day_label(mk["newest"]), len(mk["dates"]),
        "" if len(mk["dates"]) == 1 else "s", mk["first"].isoformat())]
    nxt = (" Next one: %s." % day_label(mk["next_end"])) if mk["next_end"] else ""
    if mk["month_end"]:
        out.append("Month-end check: %s.%s" % (day_label(mk["month_end"]), nxt))
    else:
        out.append("Month-end check: none yet (no whole month in the closing files).%s" % nxt)
    return out


def money_line(ctx, account):
    """Your long-term money: the holdings at their last close, and the cash."""
    n = len(ctx["pf"]["holdings"])
    return "Your long-term money: %s in %d share%s and %s cash." % (
        money(account["holdings_pkr"]), n, "" if n == 1 else "s", money(account["cash_pkr"]))


def data_notes(ctx, mk, facts):
    """Notes about the data and the calendar, for the end of a report."""
    out = ["Note: " + x for x in mk["notes"] + ([ctx["prices_note"]] if ctx.get("prices_note") else [])]
    cal = calendar_note(ctx["today"], ctx["cfg"])
    if cal:
        out.append("Note: " + cal)
    if mk["missing"]:
        days = ", ".join(day_label(d) for d in mk["missing"][-3:])
        out.append("Note: no closing file for %d PSX session%s by the calendar (%s%s). Prices are not scaled "
                   "across a missing day. Run backfill or save the file." % (
                       len(mk["missing"]), "" if len(mk["missing"]) == 1 else "s",
                       "the newest: " if len(mk["missing"]) > 3 else "", days))
    late, d = [], mk["newest"] + datetime.timedelta(days=1)
    while d < ctx["today"]:
        if trading_day(d, ctx["cfg"])[0]:
            late.append(d)
        d += datetime.timedelta(days=1)
    if late:
        out.append("Note: no closing file yet for %s%s. The signals use the files up to %s." % (
            day_label(late[0]), (" and %d more session%s" % (len(late) - 1, "" if len(late) == 2 else "s"))
            if len(late) > 1 else "", day_label(mk["newest"])))
    for f in facts:
        if f["held"] and not f["close"]:
            out.append("Note: %s has no close in the closing files, so it is not in your long-term money." % f["symbol"])
    syms = [f["symbol"] for f in facts]               # held shares and candidates: rule 7 looks at both
    if not ctx["metrics_found"]:
        out.append("Note: no psx/metrics.json, so rule 7 (profit) is not checked. Run psx-analyst: facts.py %s "
                   "--metrics psx/metrics.json." % " ".join(syms))
    else:
        lack = [s for s in syms if s not in ctx["metrics"]]
        if lack:
            out.append("Note: no row in psx/metrics.json for %s, so rule 7 skips %s. Run psx-analyst: facts.py %s "
                       "--metrics psx/metrics.json." % (", ".join(lack), "it" if len(lack) == 1 else "them",
                                                         " ".join(lack)))
    if not ctx["state"].get("watch_start") and mk["month_end"]:
        out.append("Note: the watch has not begun. The first signals --write starts it at %s, so a break that is "
                   "already there is a REVIEW, not a TRIM." % day_label(mk["month_end"]))
    if ctx.get("other_rules"):
        out.append(ctx["other_rules"])
    return out


def profit_note(ctx, syms):
    """A note for the shares new money would go to that rule 7 (profit) could not check."""
    lack = [s for s in syms if not ctx["metrics_found"] or s not in ctx["metrics"]]
    if not lack:
        return []
    if not ctx["metrics_found"]:
        head = "Note: no psx/metrics.json, so rule 7 (profit) was not checked for %s." % ", ".join(lack)
    else:
        head = "Note: no row in psx/metrics.json for %s, so rule 7 (profit) was not checked for %s." % (
            ", ".join(lack), "it" if len(lack) == 1 else "them")
    return [head + " Run psx-analyst: facts.py %s --metrics psx/metrics.json." % " ".join(lack)]


def summary_rows(facts):
    """One line a share: the signal, the symbol, the weight and the reason in short. A candidate you
    do not hold says so first, so "HOLD" never reads as "keep your shares"."""
    width = max([6] + [len(f["symbol"]) + 1 for f in facts])
    return ["%-8s%-*s%4.1f%%  %s%s" % (f["signal"], width, f["symbol"], f["weight"],
                                       "" if f["held"] else "not held, a candidate; ", f["summary"]) for f in facts]


def actions_block(sells, buys, money_pkr, left, adds, where):
    """"What the rules would do": the TRIM sells, then how new money would be split."""
    if not sells and money_pkr < 0.01:
        return []
    out = ["What the rules would do (%s):" % price_note(where)]
    out += [sell_line(a) for a in sells]
    if sells:
        out.append(TAX_LINE)
    if money_pkr >= 0.01:
        if not adds:
            out.append(WAITS_LINE)
        elif not buys:
            out.append(NO_ROOM_LINE % money(money_pkr))
        else:
            out.append("New money: the rules would split %s like this: %s. You decide. Nothing is placed." % (
                money(money_pkr), ", ".join("%s %s" % (whole(b["qty"]), b["symbol"]) for b in buys)))
            if left >= 0.01:
                out.append(NO_ROOM_LINE % money(left))
            out.append("(invest.py money %s shows how.)" % amount_text(money_pkr))
    return out


# ---------------------------------------------------------------- signals and explain

RULE_NUMBERS = {"trend": 1, "12-month": 2, "break": 3, "back in": 4, "size": 5, "sector": 6, "profit": 7, "data": 8,
                "never add": 9, "rights": 10}


def iso(d):
    """date -> "YYYY-MM-DD"; None stays None."""
    return d.isoformat() if d else None


def action_doc(a):
    """An action as it goes into psx/signals.json."""
    return dict((k, a[k]) for k in ("side", "qty", "price", "value", "costs") if k in a) if a else None


def run_signals(ctx, start_watch=False, new_cash=0.0):
    """The signals of the folder and what the rules would do -> a dict for the reports. With
    start_watch (signals --write), a folder with no watch yet starts it at the newest month-end.
    new_cash: money not recorded yet, counted in the long-term money (the what-if of `money N`)."""
    mk = load_market(ctx)
    began = bool(start_watch and not ctx["state"].get("watch_start") and mk["month_end"])
    if began:
        ctx["state"]["watch_start"] = mk["month_end"].isoformat()
    facts, account = evaluate_folder(ctx, mk, new_cash)
    sells = trim_actions(facts, ctx)
    avail = max(0.0, ctx["pf"]["cash"] - ctx["rules"]["keep_cash_pkr"])
    buys, left, where = new_money(avail, facts, ctx)
    for s in sells:
        where[s["symbol"]] = s["where"]
    acts = dict((s["symbol"], dict(s, side="sell")) for s in sells)
    for b in buys:
        acts[b["symbol"]] = dict(b, side="buy")
    notes = data_notes(ctx, mk, facts)
    doc = {"created_by": "psx-invest", "created_at": utc_iso(ctx["now_utc"]), "as_of": mk["newest"].isoformat() + " close",
           "month_end": iso(mk["month_end"]), "next_month_end": iso(mk["next_end"]), "sessions": len(mk["dates"]),
           "first_date": iso(mk["first"]), "account": account,
           "holdings": [{"symbol": f["symbol"], "signal": f["signal"], "weight_pct": round(f["weight"], 2),
                         "trend": f["trend"], "momentum": f["momentum"], "state": f["state"], "reasons": f["reasons"],
                         "action": action_doc(acts.get(f["symbol"])), "held": f["held"], "shares": f["shares"],
                         "close": f["close"], "close_date": iso(f["close_date"]), "sector": f["sector"],
                         "sector_pct": round(f["sector_pct"], 2), "fair_share_pct": round(f["fair"], 2),
                         "summary": f["summary"], "line": f["line"]} for f in facts],
           "new_money": {"amount_pkr": cents(avail), "buys": buys, "left_pkr": left}, "notes": notes}
    return {"mk": mk, "facts": facts, "account": account, "sells": sells, "buys": buys, "money": avail,
            "left": left, "where": where, "notes": notes, "doc": doc, "began": began,
            "adds": [f for f in facts if f["signal"] == "ADD"]}


def keep_trims(st, f, today):
    """signals --write: record each open TRIM of a share once, and close the ones that are done."""
    sym = f["symbol"]
    recs = [r for r in (st["trims"].get(sym) or []) if isinstance(r, dict)]
    acked = acks_of(st, sym)[0]
    for c in f["trim_causes"]:
        key = c["keys"][0]
        if not any(r.get("key") == key for r in recs):
            recs.append({"key": key, "kind": "break" if key.startswith("trim:break:") else "size", "date": today,
                         "shares_at": f["shares"], "amount": c["qty"], "closed": None})
    for rec in recs:
        if rec.get("closed"):
            continue
        if trim_done(rec, f["shares"]):
            why = "the shares dropped"
        elif rec.get("key") in acked:
            why = "your ack"
        elif rec.get("kind") == "break" and f["state"] == "in":
            why = "back in"
        else:
            continue
        rec["closed"] = {"why": why, "date": today}
    if recs:
        st["trims"][sym] = recs


def keep_breaks(st, facts):
    """Save the break state of each share, so the next run walks on from it (walk_on), not from the
    newest 500 files alone: the state, since when, whether it began by a change seen in the files,
    and the month-end it was walked to. A share whose state is not known has none saved."""
    breaks = st.setdefault("breaks", {})
    for f in facts:
        if f["state"] in ("in", "out") and f["since"] and f["walked"]:
            breaks[f["symbol"]] = {"state": f["state"], "since": iso(f["since"]), "changed": bool(f["changed"]),
                                   "walked": iso(f["walked"])}
        else:
            breaks.pop(f["symbol"], None)


def write_run(ctx, r):
    """signals --write: psx/signals.json, the state (only when it changed) and the journal (only
    for a signal that changed). -> the notify lines, one per change."""
    st, mk, today = ctx["state"], r["mk"], ctx["today"].isoformat()
    if not st.get("watch_start") and mk["month_end"]:
        st["watch_start"] = mk["month_end"].isoformat()
    st["month_end"] = iso(mk["month_end"])
    entries, notify = [], []
    for f in r["facts"]:
        sym = f["symbol"]
        old = st["signals"].get(sym) if isinstance(st["signals"].get(sym), dict) else {}
        since = old.get("since") if f["signal"] == old.get("signal") else today
        if f["signal"] != old.get("signal"):
            if old.get("signal") is not None or f["signal"] != "HOLD":
                notify.append(NOTIFY_LINES[f["signal"]] % sym)
            entries.append({"kind": "signal", "date": today, "at": pkt_iso(ctx["now_utc"]), "symbol": sym,
                            "signal": f["signal"], "from": old.get("signal"), "close": f["close"],
                            "close_date": iso(f["close_date"]), "why": f["summary"],
                            "reasons": [x["words"] for x in f["reasons"] if x["met"]]})
        st["signals"][sym] = {"signal": f["signal"], "since": since, "state": f["state"],   # the break state too
                              "state_since": iso(f["since"])}
        keep_trims(st, f, today)
        present = set(k for c in f["present"] for k in c["keys"])
        for rec in (st["acks"].get(sym) or []):
            if isinstance(rec, dict) and rec.get("covers"):
                rec["covers"] = [k for k in rec["covers"] if not k.startswith("sector:") or k in present]
    keep_breaks(st, r["facts"])
    write_json(SIGNALS_PATH, r["doc"])
    if json.dumps(st, sort_keys=True) != ctx["state_text"]:
        write_json(ctx["a"].state, st)
    append_journal(JOURNAL_PATH, entries)
    return notify


def cmd_signals(ctx):
    r = run_signals(ctx, ctx["a"].write)
    lines = header_lines(ctx, r["mk"], "Long-term signals") + [money_line(ctx, r["account"]), ""]
    lines += summary_rows(r["facts"])
    notes = r["notes"] + (["Note: the watch begins now, at %s. A break that is already there is a REVIEW, not a "
                           "TRIM." % day_label(r["mk"]["month_end"])] if r["began"] else [])
    if notes:
        lines += [""] + notes
    block = actions_block(r["sells"], r["buys"], r["money"], r["left"], r["adds"], r["where"])
    lines += ([""] + block) if block else []
    lines.append(FOOTER)
    notify = write_run(ctx, r) if ctx["a"].write else []
    if ctx["a"].json:
        print(json.dumps(dict(r["doc"], notify=notify), indent=2))
    else:
        print("\n".join(lines + notify))
    return 0


def payout_line(f, newest):
    """The payouts seen in the closing files, and their sum in the last 12 months (a fact)."""
    if not f["payouts"]:
        return "Payouts seen: none in the closing files."
    parts = ["%s on %s (%s)" % (money(p["amount"]) if p["amount"] is not None else "an amount not known",
                                day_label(p["date"]), "".join(p["flags"])) for p in f["payouts"][-4:]]
    year = [p for p in f["payouts"] if p["date"] > newest - datetime.timedelta(days=365)]
    total, scaled = Decimal(0), False
    for p in year:
        if p["amount"] is None:
            continue
        k = Decimal(1)
        for e in f["events"]:
            if e["kind"] in ("XB", "split") and e["date"] > p["date"] and e["factor"]:
                k *= Decimal(str(e["factor"]))
        scaled = scaled or k != 1
        total += Decimal(str(p["amount"])) * k
    unknown = len([p for p in year if p["amount"] is None])
    return "Payouts seen: %s. In the last 12 months: %s a share%s%s." % (
        ", ".join(parts), money(total), " (on today's share count)" if scaled else "",
        (", and %d payout%s not known" % (unknown, "" if unknown == 1 else "s")) if unknown else "")


def cmd_explain(ctx, symbol):
    sym = str(symbol or "").strip().upper()
    if sym not in ctx["pf"]["holdings"] and sym not in ctx["rules"]["candidates"]:
        raise NothingToShow('%s is not in %s and not a candidate in %s. Add it under "candidates" to see its rules.'
                            % (sym, ctx["a"].portfolio, ctx["a"].invest))
    r = run_signals(ctx)
    f = next(x for x in r["facts"] if x["symbol"] == sym)
    mk, total = r["mk"], ctx["total"]
    lines = header_lines(ctx, mk, "%s: every fact and rule" % sym)[:1] + [f["line"]]
    if not f["held"]:
        lines.append("You hold no %s. It is a candidate for new money (\"candidates\" in %s)." % (sym, ctx["a"].invest))
    if f["close"]:
        lines.append("Shares: %s in %s. Last close: %.2f on %s. Value: %s, %s of your long-term money (%s)." % (
            whole(f["shares"]), ctx["a"].portfolio, f["close"], day_label(f["close_date"]), money(f["value"] or 0),
            pct_text(f["weight"], False), money(total)))
    lines.append("Sector: %s (code %s). Fair share: %s (%s). Room: %s." % (
        f["sector"], f["sector_code"] or "not known", pct_text(f["fair"], False), money(f["fair"] / 100 * total),
        money(max(0.0, f["fair"] / 100 * total - (f["value"] or 0)))))
    lines += header_lines(ctx, mk, "")[1:]
    if f["month_closes"]:
        lines.append("Month-end closes, payouts and splits counted: %s." % ", ".join(
            "%s %.2f" % (month_label(d), c) for d, c in f["month_closes"][-13:]))
    for rs in f["reasons"]:
        n = RULE_NUMBERS.get(rs["rule"])
        lines.append(("Rule %d. %s" % (n, rs["words"])) if n else rs["words"])
    if not f["data"]:
        tail = ("No bonus, right or split came %s." % reference_date(ctx, mk)[1]) if f["held"] else (
            "You hold no %s, so the share-count check does not apply." % sym)
        lines.append("Rule 8. Data: no problem found. No file is missing next to a jump. It has a close in the "
                     "newest %d files. %s" % (ctx["rules"]["stale_trade_sessions"], tail))
    lines.append(payout_line(f, mk["newest"]))
    lines += scale_lines(f["bars"], f["prev"], mk["missing"])
    sells = [a for a in r["sells"] if a["symbol"] == sym]
    buys = [b for b in r["buys"] if b["symbol"] == sym]
    where = dict((k, v) for k, v in r["where"].items() if k == sym)
    if sells or buys:
        lines.append("What the rules would do (%s):" % price_note(where))
        lines += [sell_line(a) for a in sells] + ([TAX_LINE] if sells else []) + [buy_line(b) for b in buys]
    else:
        lines.append("What the rules would do: nothing for %s now." % sym)
    lines.append(FOOTER)
    if ctx["a"].json:
        doc = next(h for h in r["doc"]["holdings"] if h["symbol"] == sym)
        doc = dict(doc, month_closes=[[iso(d), round(c, 4)] for d, c in f["month_closes"]],
                   payouts=[{"date": iso(p["date"]), "amount": p["amount"], "flags": p["flags"]} for p in f["payouts"]],
                   events=[{"date": iso(e["date"]), "kind": e["kind"], "factor": e["factor"]} for e in f["events"]],
                   lines=lines)
        print(json.dumps(doc, indent=2))
    else:
        print("\n".join(lines))
    return 0


# ---------------------------------------------------------------- money and orders

def parse_amount(text, what):
    """"46000", "46,000" or "46000.50" -> rupees above 0, with a plain error."""
    v = _f(str(text).replace(",", "").strip()) if text is not None else None
    if v is None or v <= 0:
        raise InvestError("%s wants rupees above 0, like 46000" % what)
    return cents(v)


def money_to_split(ctx, text, what_if=False):
    """(rupees, where they come from, new money not recorded yet). No amount: the cash in
    psx/portfolio.json less keep_cash_pkr. `orders --money N` is checked against the recorded cash
    (3.6 step 7). `money N` is a what-if: money over the cash counts as new money."""
    cash, keep = ctx["pf"]["cash"], ctx["rules"]["keep_cash_pkr"]
    avail = max(0.0, cents(cash - keep))
    if text is None:
        return avail, "the cash in %s%s" % (ctx["a"].portfolio, " less keep_cash_pkr" if keep else ""), 0.0
    n = parse_amount(text, "the money")
    if what_if:
        return n, "the amount you gave", max(0.0, cents(n - avail))
    if n > cash + CENT:
        raise InvestError("Only %s is recorded as long-term cash. Record new money first: invest.py cash --add %s."
                          % (money(cash), amount_text(n - cash)))
    if n > avail + CENT:
        raise InvestError("keep_cash_pkr in %s keeps %s as cash, so at most %s can be split."
                          % (ctx["a"].invest, money(keep), money(avail)))
    return n, "--money", 0.0


def vote_rule(name, vote, pct):
    """"trend up (+3.1%)", "12-month no vote (+1.0%)"."""
    return "%s %s (%s)" % (name, {"up": "up", "down": "down", "none": "no vote"}.get(vote, "not known"),
                           pct_text(pct) if pct is not None else "not known")


def order_of(a, f, rules):
    """One psx/orders.json order (psx-budget's shape, 3.7): a limit order, no stop, plan style "invest"."""
    cap, line = rules["max_weight_pct"], rules["max_weight_pct"] + rules["trim_band_pct"]
    part = f["trim"].get("part") if a["side"] == "sell" else None
    rest = ("%s of %s shares left" % (whole(a["qty"]), whole(part["amount"]))) if part else ""
    if a["side"] == "sell" and f["trim"]["rule"] == 5 and (part or f["weight"] <= line + 1e-9):
        signal, why = "TRIM", "psx-invest TRIM: the TRIM from %s%s, back to the %s%% cap (rule 5)" % (
            f["trim"].get("day") or "an earlier day", (": " + rest) if rest else " (%s shares)" % whole(a["qty"]),
            num_text(cap))
        used = ["a TRIM from an earlier run (%s)" % (rest or "%s shares" % whole(a["qty"]))]
    elif a["side"] == "sell" and f["trim"]["rule"] == 5:
        signal, why = "TRIM", "psx-invest TRIM: size %s, over the %s%% trim line, back to the %s%% cap (rule 5)" % (
            pct_text(f["weight"], False), num_text(line), num_text(cap))
        used = ["size over the trim line (%s)" % pct_text(f["weight"], False)]
    elif a["side"] == "sell":
        signal, why = "TRIM", "psx-invest TRIM: break: both votes turned down at %s, sell %s%s (rule 3)" % (
            end_of_month(f["since"]), trim_words(rules["break_trim_pct"]), (": " + rest) if rest else "")
        used = ["break: both votes down at %s" % end_of_month(f["since"])]
    else:
        ups = [vote_rule(n, v["vote"], v.get("gap_pct", v.get("change_pct"))) for n, v in
               (("trend", f["trend"]), ("12-month", f["momentum"])) if v["vote"] == "up"]
        signal, why = "ADD", "psx-invest ADD: no vote down, %s, size %s to %s (fair share %s)" % (
            ", ".join(ups), pct_text(a["weight_before_pct"], False), pct_text(a["weight_after_pct"], False),
            pct_text(f["fair"], False))
        used = [vote_rule("trend", f["trend"]["vote"], f["trend"]["gap_pct"]),
                vote_rule("12-month", f["momentum"]["vote"], f["momentum"]["change_pct"])]
    return {"symbol": a["symbol"], "side": a["side"], "qty": a["qty"], "type": "limit", "limit_price": a["price"],
            "time_in_force": "day", "board": "REG", "stop_loss": None, "take_profit": None, "why": why,
            "plan": {"style": "invest", "signal": signal, "entry": a["price"], "fair_share_pct": round(f["fair"], 2),
                     "weight_after_pct": a["weight_after_pct"], "rules": used}}


def cmd_money(ctx, text):
    n, source, new = money_to_split(ctx, text, what_if=True)
    r = run_signals(ctx, False, new)
    buys, left, where = new_money(n, r["facts"], ctx)
    total, cfg = ctx["total"], ctx["cfg"]
    lines = header_lines(ctx, r["mk"], "New money")[:1]
    lines.append("Money to split: %s (%s). Your long-term money: %s." % (money(n), source, money(total)))
    if new >= 0.01:
        lines.append("%s of it is not recorded yet, so this is a what-if. Record it before any order: invest.py "
                     "cash --add %s." % (money(new), amount_text(new)))
    if n < 0.01:
        lines.append("No long-term cash is recorded in %s. Record new money first: invest.py cash --add N."
                     % ctx["a"].portfolio)
    elif not r["adds"]:
        lines.append(WAITS_LINE)
    else:
        lines.append("ADD shares: %s." % ", ".join(f["symbol"] for f in r["adds"]))
        for f in r["adds"]:
            lines.append("  %s: %s now, fair share %s (room %s). Sector %s: %s, cap %s%%." % (
                f["symbol"], pct_text(f["weight"], False), pct_text(f["fair"], False),
                money(max(0.0, f["fair"] / 100 * total - (f["value"] or 0))), f["sector"],
                pct_text(f["sector_pct"], False), num_text(ctx["rules"]["max_sector_pct"])))
        lines.append("What the rules would do (%s):" % price_note(where))
        for b in buys:
            lines.append(buy_line(b))
            lines.append("  %s: %s to %s of your long-term money (fair share %s)." % (
                b["symbol"], pct_text(b["weight_before_pct"], False), pct_text(b["weight_after_pct"], False),
                pct_text(b["fair_share_pct"], False)))
        if left >= 0.01:
            lines.append(NO_ROOM_LINE % money(left))
        mx = setting(cfg, "max_order_pkr")
        lines.append("Limits: min order %s, max order %s, at most %s buys a day (%s). Cash from a TRIM waits for the "
                     "next run." % (money(setting(cfg, "min_order_pkr") or 0), money(mx) if mx else "none",
                                    setting(cfg, "max_trades_per_day"), ctx["a"].config))
        lines += profit_note(ctx, [b["symbol"] for b in buys])
    lines += [ctx["other_rules"]] if ctx.get("other_rules") else []
    lines.append(FOOTER)
    if ctx["a"].json:
        print(json.dumps({"amount_pkr": n, "source": source, "new_pkr": new, "total_pkr": cents(total), "buys": buys,
                          "left_pkr": left, "adds": [f["symbol"] for f in r["adds"]], "lines": lines}, indent=2))
    else:
        print("\n".join(lines))
    return 0 if n >= 0.01 else 1


def cmd_orders(ctx, text, write):
    n, _, _ = money_to_split(ctx, text)
    r = run_signals(ctx)
    buys, left, where = new_money(n, r["facts"], ctx)
    sells, by = list(r["sells"]), dict((f["symbol"], f) for f in r["facts"])
    for s in sells:
        where[s["symbol"]] = s["where"]
    notes, cap = [], int(setting(ctx["cfg"], "max_orders_per_day") or 0)
    if cap and len(sells) + len(buys) > cap:
        cut = buys[max(0, cap - len(sells)):] + sells[cap:]
        sells, buys = sells[:cap], buys[:max(0, cap - len(sells))]
        notes.append("Note: max_orders_per_day is %d, so %s wait%s for another day." % (
            cap, ", ".join(x["symbol"] for x in cut), "s" if len(cut) == 1 else ""))
    orders = [order_of(s, by[s["symbol"]], ctx["rules"]) for s in sells] + \
             [order_of(dict(b, side="buy"), by[b["symbol"]], ctx["rules"]) for b in buys]
    lines = ["Orders for psx-risk, %s %s PKT (%s)." % (day_label(ctx["now_pkt"]), clock_label(ctx["now_pkt"]),
                                                       price_note(where))]
    paper_real = ctx["paper"] and ctx["pf"]["real"]
    kept = save_trims(ctx, r["facts"]) if write else 0      # each TRIM shown is saved once (3.4)
    if kept:
        notes.append("Note: %s saved in %s, once. After a sale, the next run asks only for the rest." % (
            "1 new TRIM is" if kept == 1 else "%d new TRIMs are" % kept, ctx["a"].state))
    doc = {"created_by": "psx-invest", "created_at": utc_iso(ctx["now_utc"]), "status": "proposed",
           "paper": ctx["paper"], "note": PROPOSALS, "orders": orders}
    entry = {"kind": "orders", "date": ctx["today"].isoformat(), "at": pkt_iso(ctx["now_utc"]), "written": False,
             "orders": [{"symbol": o["symbol"], "side": o["side"], "qty": o["qty"], "price": o["limit_price"],
                         "signal": o["plan"]["signal"]} for o in orders]}
    if not orders:
        if n < 0.01:
            lines.append("No order: no TRIM signal, and no new money for an ADD share.")
            why = "no TRIM signal and no new money"
        elif not r["adds"]:
            lines += ["No order: no TRIM signal.", WAITS_LINE]
            why = "no TRIM signal; no share meets the add rules, so the money waits as cash"
        else:
            lines += ["No order: no TRIM signal.", NO_ROOM_LINE % money(n)]
            why = "no TRIM signal; no ADD share has room for the money"
        lines += notes + ([ctx["other_rules"]] if ctx.get("other_rules") else [])
        if write:                         # the journal keeps this run too (3.10)
            append_journal(JOURNAL_PATH, [dict(entry, why=why)])
            lines.append("The journal keeps this run (%s). No order was written." % JOURNAL_PATH)
        lines.append(FOOTER)
        print(json.dumps(dict(doc, lines=lines), indent=2) if ctx["a"].json else "\n".join(lines))
        return 1
    lines += [sell_line(s) for s in sells] + ([TAX_LINE] if sells else []) + [buy_line(b) for b in buys]
    if n >= 0.01 and not buys:
        lines.append(WAITS_LINE if not r["adds"] else NO_ROOM_LINE % money(n))
    elif buys and left >= 0.01:
        lines.append(NO_ROOM_LINE % money(left))
    lines += ["Add %s to the trading folder's blocked list, so the trading tools never touch it." % b["symbol"]
              for b in buys if b["symbol"] not in ctx["pf"]["holdings"]]
    lines += notes + profit_note(ctx, [b["symbol"] for b in buys])
    lines += [ctx["other_rules"]] if ctx.get("other_rules") else []
    if not ctx["open"]:
        lines.append("Note: PSX is not open now (%s). psx-risk passes a buy only in the session."
                     % ctx["market_now"]["reason"])
    if write and paper_real:
        append_journal(JOURNAL_PATH, [dict(entry, why="paper mode and real holdings")])
        lines.append(PAPER_REAL_LINE)
    elif write:
        write_json(ORDERS_PATH, doc)
        append_journal(JOURNAL_PATH, [dict(entry, written=True)])
        lines.append("Wrote %d order%s to %s: the sells first, then the buys. Next: psx-risk (check.py), then "
                     "psx-broker (ticket.py show) and your yes. Nothing is placed." % (
                         len(orders), "" if len(orders) == 1 else "s", ORDERS_PATH))
    elif paper_real:
        lines.append("Nothing was written. With --write the journal keeps these orders (paper mode and real "
                     "holdings: never %s)." % ORDERS_PATH)
    else:
        lines.append("Nothing was written. Add --write to save them in %s for psx-risk." % ORDERS_PATH)
    if paper_real:
        lines.append(FILL_REAL_LINE % (orders[0]["symbol"], orders[0]["side"]))
    lines.append(FOOTER)
    if ctx["a"].json:
        print(json.dumps(dict(doc, lines=lines), indent=2))
    else:
        print("\n".join(lines))
    return 0


def save_trims(ctx, facts):
    """orders --write: save each TRIM it shows (once) and the break states in psx/state/invest.json,
    as signals --write does. Then a sale after this order counts against the amount the rule asked,
    and the next run asks only for the rest. The state is read again just before the write, so a
    change that another run made in between is kept. -> the number of new TRIMs saved."""
    st = load_state(ctx["a"].state)
    before, today = json.dumps(st, sort_keys=True), ctx["today"].isoformat()

    def count():
        return sum(len([x for x in recs if isinstance(x, dict)]) for recs in st["trims"].values()
                   if isinstance(recs, list))
    had = count()
    for f in facts:
        keep_trims(st, f, today)
    keep_breaks(st, facts)
    if json.dumps(st, sort_keys=True) != before:
        write_json(ctx["a"].state, st)
    return count() - had


# ---------------------------------------------------------------- cash, ack

def cmd_cash(ctx, add, to):
    """Record long-term money: only "cash" in psx/portfolio.json changes. Logged in the journal."""
    path, doc = ctx["a"].portfolio, ctx["pf"]["doc"]
    if not isinstance(doc, dict):
        raise InvestError("No %s: put your shares and cash in it first (step 1 in the skill page)." % path)
    old = ctx["pf"]["cash"]
    if add is not None:
        n = parse_amount(add, "--add")
        new = cents(old + n)
    else:
        n = _f(str(to).replace(",", "").strip())
        if n is None or n < 0:
            raise InvestError("--set wants rupees, 0 or more, like 46000")
        new = cents(n)
    doc["cash"] = new
    write_json(path, doc)
    append_journal(JOURNAL_PATH, [{"kind": "cash", "date": ctx["today"].isoformat(), "at": pkt_iso(ctx["now_utc"]),
                                   "from": old, "to": new, "add": n if add is not None else None}])
    print("Long-term cash in %s: %s to %s%s. Nothing else changed." % (
        path, money(old), money(new), (" (%s added)" % money(n)) if add is not None else ""))
    print(CASH_LINE)
    return 0


def cmd_ack(ctx, symbol, choice, note):
    """Your answer to an open REVIEW or TRIM of one share: it goes into the state and the journal."""
    sym = str(symbol or "").strip().upper()
    if sym not in ctx["pf"]["holdings"] and sym not in ctx["rules"]["candidates"]:
        raise InvestError("%s is not in %s and not a candidate in %s." % (sym, ctx["a"].portfolio, ctx["a"].invest))
    r = run_signals(ctx)
    f = next(x for x in r["facts"] if x["symbol"] == sym)
    if not f["open_causes"]:
        raise InvestError("%s has no REVIEW or TRIM to answer now (it is %s)." % (sym, f["signal"]))
    word = "checked" if choice == "checked" else "keep"
    fits = [c for c in f["open_causes"] if c["ack"] == word]
    if not fits and word == "checked":
        raise InvestError("%s has no open share count or rights REVIEW. Answer it with --keep or --trimmed." % sym)
    if not fits:
        raise InvestError("%s's open REVIEW is about its share count or a right. Answer it with --checked (you "
                          "checked your shares)." % sym)
    st, today = ctx["state"], ctx["today"].isoformat()
    covers = sorted(set(k for c in fits for k in c["keys"]))
    st["acks"].setdefault(sym, []).append({"date": today, "choice": choice, "note": str(note or "").strip(),
                                           "covers": covers})
    for rec in st["trims"].get(sym) or []:
        if isinstance(rec, dict) and rec.get("key") in covers and not rec.get("closed"):
            rec["closed"] = {"why": "your ack", "date": today}
    keep_breaks(st, r["facts"])         # the break state the ack answers stays the same when the files move on
    write_json(ctx["a"].state, st)
    append_journal(JOURNAL_PATH, [{"kind": "ack", "date": today, "at": pkt_iso(ctx["now_utc"]), "symbol": sym,
                                   "choice": choice, "note": str(note or "").strip(), "covers": covers}])
    after = next(x for x in run_signals(ctx)["facts"] if x["symbol"] == sym)
    print("Recorded for %s on %s: %s. It answers: %s." % (
        sym, day_label(ctx["today"]), {"keep": "keep", "trimmed": "trim", "checked": "you checked your shares"}[choice],
        "; ".join(c["short"] for c in fits)))
    print("%s now: %s" % (sym, after["line"]))
    print("Saved in %s and the journal. A new event opens a new REVIEW." % ctx["a"].state)
    return 0


# ---------------------------------------------------------------- the one-time backfill (3.11)

def backfill_dates(root, cfg, start, no_session, sessions, today):
    """The days the backfill would ask PSX for, newest first: weekdays from `start` back that are
    trading days by the calendar (every weekday before 2026), with no file and not in no_session,
    until there would be `sessions` files, at most 3 x sessions weekdays back. -> (days, files there)."""
    plan, have, weekdays, d = [], 0, 0, start
    while weekdays < 3 * sessions and have + len(plan) < sessions:
        if d.weekday() < 5:
            weekdays += 1
            if os.path.exists(daily_path(root, d)):
                have += 1
            elif trading_day(d, cfg)[0] and d.isoformat() not in no_session and d < today:
                plan.append(d)
        d -= datetime.timedelta(days=1)
    return plan, have


def backfill_day(d, root):
    """One backfill GET (3.11) -> ("file", rows), ("404", None) or ("page", None). It is the pack's plain
    GET (http_get: its User-Agent, at least 1.5 s apart), as daily_rows() makes it, but it tells a clean
    404 (PSX's answer for a day with no session) from a web page with no closing file in it (a site
    problem: busy, blocked or an error). A file is checked (read_mkt_summary, its own date) and saved
    through a temp file. Any other error is raised."""
    path = daily_path(root, d)
    try:
        raw = http_get("%s/download/mkt_summary/%s.Z" % (PSX, d.isoformat()))
    except urllib.error.HTTPError as e:
        try:
            e.close()
        except Exception:
            pass
        if e.code == 404:
            return "404", None
        raise
    head = raw[:500].lstrip().lower()
    if head.startswith(b"<") or b"<html" in head:
        return "page", None
    try:
        rows = read_mkt_summary(raw)
    except ValueError as e:
        raise ValueError("%s: %s" % (path, e))
    if rows[0]["date"] != d:
        raise ValueError("%s holds the closing file of %s, not %s" % (path, rows[0]["date"].isoformat(), d.isoformat()))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as fh:
        fh.write(raw)
    os.replace(path + ".tmp", path)
    return "file", rows


def add_no_session(path, days):
    """Add days to the no_session list of the state. The state is read again just before the write,
    so a change another run made meanwhile (signals, acks, TRIMs) is kept: only this list changes."""
    st = load_state(path)
    new = sorted(set(st.get("no_session") or []) | set(days))
    if new != st.get("no_session"):
        st["no_session"] = new
        write_json(path, st)


def forget_no_session(ctx):
    """backfill --forget-no-session: clear only the list of days with no session in the state."""
    path = ctx["a"].state
    st = load_state(path)
    old = list(st.get("no_session") or [])
    st["no_session"] = []
    if old:
        write_json(path, st)
    print("Cleared %d day%s with no session from %s. Nothing else changed. The next backfill asks PSX for %s "
          "again." % (len(old), "" if len(old) == 1 else "s", path, "it" if len(old) == 1 else "them"))
    return 0


def cmd_backfill(ctx, dry):
    a, cfg, st, n, today = ctx["a"], ctx["cfg"], ctx["state"], ctx["rules"]["sessions"], ctx["today"]
    if a.forget_no_session:
        return forget_no_session(ctx)
    start = newest_file(a.data, today, 3 * n + 10) or today
    no_sess = set(st.get("no_session") or [])
    plan, have = backfill_dates(a.data, cfg, start, no_sess, n, today)
    off = SITE_OFF % "the closing files (%s/download/mkt_summary/YYYY-MM-DD.Z) in %s" % (
        PSX, os.path.join(a.data, "mkt_summary"))
    if dry:
        if not plan:
            print("Backfill plan: nothing to fetch. The folder has %d closing files (the rules use up to %d)."
                  % (have, n))
        else:
            old = len([d for d in plan if d.year < CALENDAR_YEARS[0]])
            asks = len(plan) + int(round(old * 0.05))
            low, high = int(round(asks * 1.6 / 60)), int(round(asks * 2.0 / 60))
            span = ("about %d to %d minutes" % (low, high)) if high > low else (
                "about %d minute%s" % (high, "" if high == 1 else "s") if high else "under a minute")
            print("Backfill plan: %d closing file%s to fetch, from %s back to %s (%d %s there already). About %d "
                  "request%s: an old PSX holiday answers 404 and is asked once. At 1.5 s apart that is %s, and "
                  "about %d MB. Nothing was fetched." % (
                      len(plan), "" if len(plan) == 1 else "s", day_label(plan[0]), day_label(plan[-1]), have,
                      "is" if have == 1 else "are", asks, "" if asks == 1 else "s", span,
                      max(1, int(round(len(plan) * FILE_MB)))))
        if not site_on(cfg):
            print(off)
        return 0
    if ctx["replay"]:
        print("A run with --now never uses the PSX site, so the backfill fetches nothing.")
        return 1
    if a.offline:
        print("--offline: the backfill fetches nothing.")
        return 1
    if not site_on(cfg):
        print(off)
        return 1
    saved, weekdays, d, seen, total, run, first = [], 0, start, 0, len(plan), [], set(no_sess)

    def keep_run():
        """A file came after the 404 days just before it: they were real days with no session.
        They are saved in the state at once, so a stopped run (Ctrl-C) keeps them."""
        if run:
            no_sess.update(run)
            del run[:]
            add_no_session(a.state, no_sess)

    def stop(why):
        print("The backfill stopped at %s: %s. %d closing file%s %s saved before that. Run backfill again later: it "
              "goes on from there." % (day_label(d), why, len(saved), "" if len(saved) == 1 else "s",
                                       "was" if len(saved) == 1 else "were"))
        return 2

    try:
        while weekdays < 3 * n and seen + len(saved) < n:
            if d.weekday() < 5:
                weekdays += 1
                if os.path.exists(daily_path(a.data, d)):
                    seen += 1
                    keep_run()
                elif trading_day(d, cfg)[0] and d.isoformat() not in no_sess and d < today:
                    try:
                        got, _ = backfill_day(d, a.data)
                    except (urllib.error.URLError, OSError, ValueError) as e:
                        return stop(site_error(e))
                    if got == "page":
                        return stop("PSX sent a web page, not a closing file (a busy, blocked or error page). "
                                    "Nothing was recorded for that day")
                    if got == "404":
                        run.append(d.isoformat())
                        if len(run) > NO_FILE_RUN:
                            return stop("PSX answered 404 for %d weekdays in a row (%s back to %s). No PSX holiday "
                                        "is that long, so these days were not recorded. If it stops at the same day "
                                        "again, PSX may have no older files" % (
                                            len(run), day_label(date_of(run[0], "a day")), day_label(d)))
                    else:
                        saved.append(d)
                        keep_run()
                        if len(saved) % 20 == 0 and len(saved) < total:
                            mins = int(round((total - len(saved)) * REQUEST_SECONDS / 60))
                            print("%d of %d saved, %s left." % (len(saved), total, (
                                "about %d minute%s" % (mins, "" if mins == 1 else "s")) if mins else "under a minute"))
            d -= datetime.timedelta(days=1)
    except KeyboardInterrupt:
        return stop("you stopped it")
    added = len(no_sess - first)
    add_no_session(a.state, no_sess)
    print("Backfill done: %d closing file%s saved, %d day%s with no session recorded (PSX answered 404). The folder "
          "has %d closing files now." % (len(saved), "" if len(saved) == 1 else "s", added,
                                          "" if added == 1 else "s", seen + len(saved)))
    return 0


# ---------------------------------------------------------------- rules, journal

def cmd_rules(ctx):
    r, cfg, a = ctx["rules"], ctx["cfg"], ctx["a"]
    up, down = num_text(r["add_band_pct"]), num_text(r["down_band_pct"])
    cap, line = num_text(r["max_weight_pct"]), num_text(r["max_weight_pct"] + r["trim_band_pct"])
    scap, sline = num_text(r["max_sector_pct"]), num_text(r["max_sector_pct"] + r["sector_band_pct"])
    blocked = sorted(ctx["blocked"])
    watch = date_of(ctx["state"].get("watch_start"), "watch_start in the state")
    mx = setting(cfg, "max_order_pkr")
    lines = ["The rules psx-invest uses (%s and %s):" % (a.invest, a.config),
             "1. Trend vote: the month-end close against the mean of the last %d month-end closes, payouts and splits "
             "counted. Up at +%s%% or more. Down at -%s%% or less." % (r["trend_months"], up, down),
             "2. 12-month vote: the month-end close against the one %d month-ends back, payouts counted. Up at +%s%% "
             "or more. Down at -%s%% or less." % (r["momentum_months"], up, down),
             "3. Break: both votes down. The share goes out. TRIM %s once if the break began after the watch began, "
             "else REVIEW." % trim_words(r["break_trim_pct"]),
             "4. Back in: no vote down and at least one up. New money is allowed again.",
             "5. Share size: the cap is %s%% of your long-term money (no new money over it). The trim line is %s%%: at "
             "a month-end, TRIM back to %s%%." % (cap, line, cap),
             "6. Sector size: the cap is %s%% (no new money in that sector over it). The review line is %s%%: "
             "REVIEW." % (scap, sline),
             "7. Profit: the newest quarter's profit after tax against the same quarter a year back (psx/metrics.json). "
             "-%s%% or worse, or a loss: REVIEW." % num_text(r["results_drop_pct"]),
             "8. Data: a missing closing file next to an LDCP jump of %d%% or more, no close for %d sessions, or a "
             "bonus, right or split after \"as_of\": REVIEW." % (JUMP_PCT, r["stale_trade_sessions"]),
             "9. Never add: the \"blocked\" list in %s (%s). No new money." % (a.config, ", ".join(blocked) or "none"),
             "10. Rights: an XR, or a right letter of a holding: REVIEW. Pay, or sell the right letter, by the date "
             "in the notice.",
             "Fair share: %s. New money: whole shares, costs included, inside the caps." % (
                 "your \"targets\", else equal" if r["targets"] else "equal (100% shared by your holdings and "
                                                                      "candidates)"),
             "Orders: min order %s, max order %s, at most %s buys and %s orders a day. Kept as cash: %s." % (
                 money(setting(cfg, "min_order_pkr") or 0), money(mx) if mx else "none",
                 setting(cfg, "max_trades_per_day"), setting(cfg, "max_orders_per_day"), money(r["keep_cash_pkr"])),
             "Paper trading: %s. PSX site: %s. Closing files used: up to %d." % (
                 "on" if ctx["paper"] else "off", "on" if site_on(cfg) else "off", r["sessions"]),
             ("The watch began: %s." % day_label(watch)) if watch else
             "The watch has not begun: the first signals --write starts it.",
             FOOTER]
    if a.json:
        print(json.dumps(dict((k, v) for k, v in r.items() if k != "group_of"), indent=2))
    else:
        print("\n".join(lines))
    return 0


def journal_line(e):
    """One journal entry in words (prices only, never a profit)."""
    when = e.get("date") or ""
    try:
        when = day_label(datetime.date.fromisoformat(when))
    except ValueError:
        pass
    kind = e.get("kind")
    if kind == "signal":
        return "  %s  %s  %s%s, close %s: %s" % (when, e.get("symbol"), e.get("signal"),
                                                (" (was %s)" % e["from"]) if e.get("from") else "",
                                                "%.2f" % e["close"] if _f(e.get("close")) else "not known", e.get("why"))
    if kind == "orders" and not e.get("orders"):
        return "  %s  orders: none (%s)" % (when, e.get("why") or "no order")
    if kind == "orders":
        what = "; ".join("%s %s %s at %.2f" % (o.get("side"), whole(o.get("qty") or 0), o.get("symbol"),
                                               _f(o.get("price")) or 0) for o in e.get("orders") or [])
        return "  %s  orders %s: %s" % (when, "written to psx/orders.json" if e.get("written") else
                                       "not written (%s)" % (e.get("why") or "a preview"), what)
    if kind == "cash":
        return "  %s  cash: %s to %s" % (when, money(_f(e.get("from")) or 0), money(_f(e.get("to")) or 0))
    if kind == "ack":
        return "  %s  %s  ack: %s%s" % (when, e.get("symbol"), e.get("choice"),
                                       (' ("%s")' % e["note"]) if e.get("note") else "")
    return "  %s  %s" % (when, kind)


def cmd_journal(ctx):
    entries = read_journal(JOURNAL_PATH)
    if ctx["a"].json:
        print(json.dumps(entries, indent=2))
        return 0 if entries else 1
    if not entries:
        print("The journal is empty: signals --write, orders --write, cash and ack add to it.")
        return 1
    lines, month = ["The journal (%s): what the rules said, month by month. Prices only." % JOURNAL_PATH], None
    for e in entries:
        m = str(e.get("date") or "")[:7]
        if m != month:
            month = m
            try:
                lines.append("%s %s:" % (MONTH_NAMES[int(m[5:7]) - 1], m[:4]))
            except (ValueError, IndexError):
                lines.append(m + ":")
        lines.append(journal_line(e))
    lines.append(FOOTER)
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------- command line

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=DATA_PATH, metavar="DIR", help="data folder (default psx/data)")
    common.add_argument("--config", default=CONFIG_PATH, help="default psx/config.json")
    common.add_argument("--invest", default=INVEST_PATH, help="the rule numbers (default psx/invest.json)")
    common.add_argument("--portfolio", default=PORTFOLIO_PATH, help="default psx/portfolio.json")
    common.add_argument("--state", default=STATE_PATH, help="default psx/state/invest.json")
    common.add_argument("--metrics", default=METRICS_PATH, help="psx-analyst's results (default psx/metrics.json)")
    common.add_argument("--prices", default=PRICES_PATH, help="psx-alerts' quotes (default psx/prices.json)")
    common.add_argument("--offline", action="store_true", help="never use the PSX site: read only the data folder")
    common.add_argument("--now", help='pin the clock, Karachi time: "2026-10-30 17:00" (a replay never uses the site)')
    common.add_argument("--json", action="store_true", help="print JSON")
    ap = argparse.ArgumentParser(prog="invest.py", description="psx-invest: long-term signals for the Pakistan Stock "
                                 "Exchange. Rules, not predictions. It never places an order.")
    sub = ap.add_subparsers(dest="cmd", metavar="command")
    p = sub.add_parser("signals", parents=[common], help="one signal per holding")
    p.add_argument("--write", action="store_true", help="also write psx/signals.json, the state and the journal")
    p = sub.add_parser("explain", parents=[common], help="every fact and rule for one share")
    p.add_argument("symbol")
    p = sub.add_parser("money", parents=[common], help="how new money could be split (writes nothing)")
    p.add_argument("amount", nargs="?", help="rupees (default: the long-term cash)")
    p = sub.add_parser("orders", parents=[common], help="today's orders for psx-risk")
    p.add_argument("--money", help="rupees to split (default: the long-term cash)")
    p.add_argument("--write", action="store_true", help="write psx/orders.json")
    p = sub.add_parser("cash", parents=[common], help='record long-term money: only "cash" in psx/portfolio.json')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--add", help="add rupees")
    g.add_argument("--set", help="set the cash to this many rupees")
    p = sub.add_parser("ack", parents=[common], help="your choice on a REVIEW or TRIM")
    p.add_argument("symbol")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--keep", dest="choice", action="store_const", const="keep", help="keep it")
    g.add_argument("--trimmed", dest="choice", action="store_const", const="trimmed", help="you trimmed it")
    g.add_argument("--checked", dest="choice", action="store_const", const="checked", help="you checked your shares")
    p.add_argument("--note", help="your words, kept in the state and the journal")
    p = sub.add_parser("backfill", parents=[common], help="fetch old closing files once (site on only)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="what it would fetch; asks nothing")
    g.add_argument("--forget-no-session", action="store_true",
                   help="clear the list of days with no session in the state (only that); asks nothing")
    sub.add_parser("rules", parents=[common], help="the rules and numbers in use")
    sub.add_parser("journal", parents=[common], help="what the rules said, month by month")
    return ap


def main(argv=None):
    ap = build_parser()
    try:
        a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2
    if not a.cmd:
        ap.print_usage(sys.stderr)
        sys.stderr.write("psx-invest: give a command: signals, explain, money, orders, cash, ack, backfill, rules "
                         "or journal\n")
        return 2
    try:
        ctx = load_context(a)
        if a.cmd == "signals":
            return cmd_signals(ctx)
        if a.cmd == "explain":
            return cmd_explain(ctx, a.symbol)
        if a.cmd == "money":
            return cmd_money(ctx, a.amount)
        if a.cmd == "orders":
            return cmd_orders(ctx, a.money, a.write)
        if a.cmd == "cash":
            return cmd_cash(ctx, a.add, a.set)
        if a.cmd == "ack":
            return cmd_ack(ctx, a.symbol, a.choice, a.note)
        if a.cmd == "backfill":
            return cmd_backfill(ctx, a.dry_run)
        if a.cmd == "rules":
            return cmd_rules(ctx)
        return cmd_journal(ctx)
    except NothingToShow as e:
        print(str(e))
        return 1
    except (InvestError, ValueError) as e:
        sys.stderr.write("psx-invest: %s\n" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
