#!/usr/bin/env python3
"""Order tickets and the decision log for psx-broker. Standard library only, no network.

No PSX broker gives retail clients an order API. So this tool never talks to a
broker and never places anything. You place the order yourself, in your own
broker app. The tool does its work around the one moment that matters, your yes:

  show      shows each proposed order as a plain ticket: the order, the plan
            stop and target, the max loss at the stop (costs included, the
            number psx-risk checked), why, and the status. An order is READY
            only if psx-risk passed THIS exact order today (matched by
            fingerprint) at most 5 minutes ago, an entry is inside the entry
            window while PSX is open, and nobody has decided on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word, and only then prints the line to type into your broker app.
            It refuses "yes to all", "sure", "ok", a yes that names another
            stock, amount, side or order type or asks for a change, and any
            order that is not READY.
  decline   records a no.
  result    records what happened after your yes: placed (with the order
            number from your app), skipped or failed. In paper mode "placed"
            also fills the paper ledger, at the limit price (or --filled-price).
            Also expired (it ended at the close with no fill) and cancelled
            (you cancelled it in your app; after a part fill, the rest). An
            order of an earlier day is named by --fingerprint FP (--date D
            picks the day), or by --symbol X --date D.
  fill      records a fill in psx/portfolio.json: cash, shares and costs.
            --bonus N records bonus shares at cost 0. --real records a real
            fill in a paper folder. A buy of a share on the "blocked" list
            needs --blocked-ok (it may belong in the long-term folder).
  flatten   builds a market sell of the shares you hold, by flat_by (or a
            limit sell with --limit P). It still goes through psx-risk and
            your yes.
  protect   (swing) builds a stop-limit sell of a held swing position, from
            its plan stop in psx/portfolio.json. Paper mode writes no order.
  stop      (swing) moves the plan stop of a held position up, or scales the
            stop and the target for a bonus, rights or split ex-date (one must
            be in the closing files or in psx-alerts' record). Logged.
  cash      sets the cash in psx/portfolio.json. Logged.
  log       prints today's decisions.

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed     # paper: fills at the limit price
    python3 ticket.py result --fingerprint FP --status expired
    python3 ticket.py fill --symbol DEMO --side buy --qty 3428 --price 100.65
    python3 ticket.py flatten --symbol DEMO
    python3 ticket.py protect --symbol DEMO
    python3 ticket.py stop --symbol DEMO --set 97.00
    python3 ticket.py cash --set 46000
    python3 ticket.py log

In swing style ("style": "swing" in psx/config.json) a filled buy gets its plan
in psx/portfolio.json: stop, target, exit-by day and trend rule. psx-alerts
and psx-risk read it from there. A file with "real_holdings": true holds real
shares: a paper fill never writes into it. Nor into a file with no flag that
shows signs of real shares (a key the tools never write, a yes made with paper
off, or in swing a held share with no plan): "real_holdings": false says paper.

Everything is written to psx/state/broker.json (append only). Dates are
Karachi dates; --now "2026-10-29 10:15" pins the clock (Karachi time, PKT).
--now and --today are for replays, tests and paper: with paper off in swing
style, approve of a buy refuses a pinned clock, and approve of a sell prints a
note. Day style prints nothing new there (as in v1).
"""

import argparse
import datetime
import gzip
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

ORDER_FIELDS = ("symbol", "side", "qty", "type", "limit_price", "time_in_force", "board", "stop_loss", "take_profit",
                "trigger_price")
TIF_WORDS = {"day": "good for today", "ioc": "immediate or cancel", "fok": "fill or kill"}
TYPE_WORDS = {"limit": "limit", "market": "market", "market_to_limit": "market-to-limit", "stop_limit": "stop-limit"}
OTHER_TYPE = {"limit": "market", "market": "limit", "stop_limit": "market"}   # the type a yes must not say
CLOSE_WORDS = {"expired": "the order ended at the close without a fill",
               "cancelled": "you cancelled it in your broker app",
               "skipped": "you did not place it"}
OPEN_STATUSES = ("approved", "placed")    # a yes the user may have placed in the app: the order may be open
RETRY_STATUSES = ("failed", "skipped", "expired", "cancelled")   # READY again after a newer psx-risk check
REAL_GUARD = ('%s holds real shares ("real_holdings": true). A paper fill would change it, so nothing was '
              'written. Record a real fill with paper off, or with ticket.py fill --real, or use a paper folder.')
TOOL_KEYS = ("cash", "positions", "real_holdings")    # the only top keys the tools write in psx/portfolio.json
MAYBE_REAL = ('%s has no "real_holdings" key, but it may hold real shares: %s. A paper fill would change it, so '
              'nothing was written. Ask the user which it is. Real shares: put "real_holdings": true at the top, and '
              'practise in a separate paper folder. A paper file: put "real_holdings": false at the top.')
PART_WORDS = {"expired": "the rest of the order ended at the close with no fill",
              "cancelled": "you cancelled the rest in your broker app"}
BLOCKED_BUY = ("%s is on your blocked list in psx/config.json (long-term shares). Is this the right folder? A "
               "long-term fill goes in the long-term folder. Nothing was written. If %s is a trade of this folder, "
               "add --blocked-ok.")
BLOCKED_NOTE = ("%s is on your blocked list in psx/config.json: these may be long-term shares. Check that this is the "
                "right folder before you say yes.")
CLOCK_SLACK = 120      # seconds: with paper off, approve's --now must be this close to the clock of the computer
SPLIT_AT = 0.10        # a no-flag LDCP change is a split only 10% or more away from 1 (as psx-alerts sees it)
FACTOR_SLACK = 0.0005  # a --scale may be this much under an ex-date's factor (factors have 4 decimals)
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name
PAPER_STOP = "Paper: no stop order rests anywhere. psx-alerts rings at the stop; then ticket.py flatten."
STOP_PREOPEN = ("PSX is in the pre-open. Put this stop-limit in after the opening match: in the pre-open orders "
                "cannot be changed, and a stop works only on trades.")
PROTECT_NOTE = "A stop-limit sell of shares you hold. psx-risk checks it like any order."
NO_PLAN = "No swing plan for %s on %s: give --stop, --target and --exit-by, so its alerts and risk count."
NO_PLAN_WATCH = "Only your own rules in psx/alerts.json watch %s."   # a held share with no swing plan (F02)
HELD_NO_PLAN = ("%s is already in %s, with no swing plan. --stop, --target and --exit-by give a plan to a new "
                "position only. ticket.py stop cannot give it one either: it changes only a swing plan. "
                + NO_PLAN_WATCH + " Nothing was written.")
BONUS_WORDS = ("Record the bonus shares your broker app shows. The company may keep back 10% of them until a tax "
               "is paid (section 236Z).")
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each",
               "sab", "sare", "saare", "dono"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if", "when", "once", "until", "till", "provided", "assuming",
               "nahi", "nahin", "ruko", "ruk", "thehro", "mat", "lekin", "magar", "agar", "jab", "shayad"}
DOUBT_PHRASE = re.compile(r"\bas (?:long|soon) as\b")
SIDE_SAYS = {       # a yes that says the other side is not a yes for this order (Roman Urdu too)
    "buy": {"buy", "buying", "bought", "purchase", "long", "khareed", "khareedo", "kharid", "kharido",
            "khareedna", "kharidna", "khareedein", "kharidein"},
    "sell": {"sell", "selling", "sold", "sale", "exit", "close", "flatten", "out", "becho", "bech", "bechdo",
             "bechna", "bechein", "farokht", "square", "squared", "squareoff", "liquidate", "liquidating", "dump",
             "dumping", "offload", "unload", "cashout"},
}
NOT_CASH_WORDS = {"short", "shorting", "shorted", "blank", "margin", "leverage", "leveraged", "borrow", "borrowed",
                  "loan", "futures", "future", "options", "option", "derivative", "derivatives", "mtf", "mts",
                  "cfs", "credit", "udhaar", "udhar", "qarz", "qarza"}
TYPE_SAYS = {"market": {"market", "mkt"}, "limit": {"limit", "lmt"}}
ANY_PRICE = re.compile(r"\b(?:any|best|whatever|going|current)\s+(?:price|rate)\b")   # a market order in words
CHANGE_WORDS = {"double", "doubled", "half", "halve", "twice", "triple", "more", "less", "increase", "reduce",
                "decrease", "bigger", "smaller", "extra", "add", "tomorrow", "later", "next", "week", "tonight",
                "times", "lunch", "noon", "afternoon", "evening", "juma", "jumma",
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                "dugna", "aadha", "adha", "zyada", "ziyada", "kam", "kal", "parson", "baad", "agle", "agla", "kabhi"}
TIMES = re.compile(r"(?i)(?<![a-z0-9])(?:[x\u00d7]\s?\d+|\d+\s?[x\u00d7])(?![a-z0-9])")   # x2, 2x: a new size
PLAIN_CAPS = {w.upper() for w in set().union(*SIDE_SAYS.values(), *TYPE_SAYS.values())} | {
    "YES", "OK", "OKAY", "SURE", "IT", "PLACE", "GO", "AHEAD", "DO", "PLEASE", "PLS", "PLZ", "NOW", "THE", "ORDER",
    "THIS", "ONE", "CONFIRM", "CONFIRMED", "APPROVE", "APPROVED", "SHARES", "SHARE", "AT", "FOR", "AND", "MAX",
    "LOSS", "TARGET", "PRICE", "QTY", "REG", "PSX", "RS", "PKR", "DAY", "ME", "MY", "IS", "TO", "OF", "ON", "IN",
    "AM", "JI", "JEE", "HAAN", "KAR", "KARO", "KARDO", "BHAI", "SIR", "THANKS"}   # capitals that are not a stock
WORD_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*")
SYMBOL_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9.\-]*")              # PSX symbols: DEMO, BAPLR1, 786, OGDC-NOV
NUMBER = re.compile(r"(?<![A-Za-z0-9.,])\d[\d,]*(?:\.\d+)?")    # not the 1 in BAPLR1
RUPEES = re.compile(r"(?i)\b(?:rs|pkr)\.?(?=\s*\d)")            # "Rs 4,999.94", "Rs4999.94", "PKR 5000"
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
MATCH_MINUTES = 2   # the opening match after each pre-open: no new orders until it ends
SWING = {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20, "trend_exit_sessions": 10,
         "entry_buffer_pct": 1.0, "max_open_positions": 3, "max_open_risk_pkr": None,
         "max_gap_loss_pkr": None, "stop_limit_gap_pct": 1.0}
FRESH_CHECK_MINUTES = 5   # a psx-risk PASS counts this long; then check.py runs again (prices and the clock move)
ENTRY_WINDOW = {"no_entries_before": "09:45", "no_entries_after": "15:00"}   # psx-risk's defaults


class TicketError(Exception):
    pass

# ---------------------------------------------------------------- time (Karachi, copied from psx_core.py)

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


# ---------------------------------------------------------------- PSX calendar (copied from psx_core.py)

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


# ---------------------------------------------------------------- money and costs (copied from psx_core.py)

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


# ---------------------------------------------------------------- closing files (copied verbatim from tests/psx_core.py; never edit a copy)

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


def daily_path(root, d):
    """<root>/mkt_summary/YYYY-MM-DD.Z, or the .lis or .zip with that name when that is
    the file you saved. The .Z path when none of them is there."""
    base = os.path.join(root, "mkt_summary", d.isoformat())
    for ext in (".Z", ".lis", ".zip"):
        if os.path.exists(base + ext):
            return base + ext
    return base + ".Z"


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


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def read_doc(path, what, lists=(), required=False):
    """read_json, then the shape: a JSON object whose `lists` keys, when there, hold lists of
    JSON objects. A bad shape is one plain TicketError with the file name, never a traceback."""
    doc = read_json(path, what, required)
    if doc is None:
        return None
    if not isinstance(doc, dict):
        raise TicketError("%s (the %s) must be a JSON object: {...}" % (path, what))
    for key in lists:
        v = doc.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
            raise TicketError('%s (the %s): "%s" must be a list of JSON objects: [{...}]' % (path, what, key))
    return doc


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def universe_symbols(path):
    """The symbols in psx/universe.txt (SYMBOL|Name|Sector code), or [] with no file. A yes that
    names one of them (and not this order's symbol) is refused."""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            sym = line.split("|", 1)[0].strip().upper()
            if sym and not sym.startswith("#"):
                out.append(sym)
    return out


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function as psx-risk/check.py: identifies the exact order (ORDER_FIELDS only)."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _p(v):
    """A price for people: 100.65."""
    return "%.2f" % float(v)


def _n(v):
    """A number for messages, without float noise: 3428, 100.65."""
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return str(v)
    return str(int(f)) if f == int(f) else ("%.4f" % f).rstrip("0").rstrip(".")


def _shares(v):
    """A share count for people: 3,428."""
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return str(v)
    return "{:,}".format(int(f)) if f == int(f) else "{:,}".format(f)


def _cents(x):
    """Rupees to the paisa, half up, without float noise."""
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def whole_shares(v, what="--qty"):
    """A share count as an int. PSX trades whole shares (a lot is 1 share on the REG board)."""
    f = _f(str(v).replace(",", "").strip()) if v is not None else None
    if f is None or f != f or f in (float("inf"), float("-inf")) or f <= 0 or f != int(f):
        raise TicketError("%s wants a whole number of shares above 0, like 3428 (PSX trades whole shares)"
                          % what)
    return int(f)


def _side(order):
    """The order's side in lower case ("buy", "sell"): "BUY" is the same order."""
    return str(order.get("side") or "").strip().lower()


def _finite(x):
    """A float that is a real number, else None."""
    f = _f(x)
    return f if f is not None and f == f and abs(f) != float("inf") else None


def max_loss(order, costs=None, rv=None):
    """(rupees, stop): what a buy loses if it fills at its entry and the stop fills exactly
    at the stop, costs included: qty x (entry - stop) + both legs' costs (SPEC section 5).
    With psx-risk's verdict rv for this order: its max loss and stop, the number psx-risk
    checked (the daily loss adds up the logged ones). With no verdict: the entry is the
    limit price of a limit order, else the plan's max loss (psx-budget worked it out) or
    the plan's entry. (None, stop) when it cannot be worked out."""
    plan = order.get("plan") if isinstance(order.get("plan"), dict) else {}
    stop = _f(order.get("stop_loss"))
    if stop is None:
        stop = _f(plan.get("stop"))
    if isinstance(rv, dict) and "max_loss" in rv:
        loss, rstop = _finite(rv.get("max_loss")), _finite(rv.get("stop"))
        stop = rstop if rstop is not None else stop
        return (_cents(loss) if loss is not None and stop is not None else None), stop
    q = _f(order.get("qty"))
    if not stop or not q or q <= 0 or _side(order) != "buy":
        return None, stop
    if str(order.get("type", "")).lower() == "limit" and _f(order.get("limit_price")):
        entry = _f(order["limit_price"])
    elif _f(plan.get("max_loss")) is not None:
        return _cents(plan["max_loss"]), stop
    else:
        entry = _f(plan.get("entry"))
    if not entry or entry <= stop:
        return None, stop
    try:
        return max_loss_at_stop(entry, stop, int(q) if q == int(q) else q, costs), stop
    except ValueError:
        return None, stop


def _stop_limit(order):
    """True for a stop-limit order with both prices (ticket.py protect makes one)."""
    return (str(order.get("type", "")).lower() == "stop_limit" and _f(order.get("trigger_price")) is not None
            and _f(order.get("limit_price")) is not None)


def order_line(order):
    """BUY 3,428 DEMO, limit 100.65, good for today, REG board
    (a stop-limit: SELL 143 DEMO, stop-limit, trigger 96.25, limit 95.28, good for today, REG board)"""
    side = str(order.get("side", "")).upper()
    sym = str(order.get("symbol", "?")).upper()
    typ = str(order.get("type", "")).lower()
    tif = str(order.get("time_in_force", "day")).lower()
    q = _f(order.get("qty"))
    if _stop_limit(order):
        how = "%s, trigger %s, limit %s" % (TYPE_WORDS["stop_limit"], _p(order["trigger_price"]),
                                            _p(order["limit_price"]))
    elif typ == "limit" and _f(order.get("limit_price")) is not None:
        how = "limit %s" % _p(order["limit_price"])
    else:
        how = TYPE_WORDS.get(typ, typ or "no order type")
    return "%s %s %s, %s, %s, %s board" % (side, _shares(q) if q is not None else order.get("qty"), sym, how,
                                           TIF_WORDS.get(tif, tif), str(order.get("board") or "REG").upper())


def risk_entry(order, rv):
    """The entry psx-risk measured a market order's max loss from (its entry_ref), or None:
    for a limit order the limit price is the entry, and with no verdict there is none."""
    if str(order.get("type", "")).lower() == "limit" or not isinstance(rv, dict):
        return None
    e = _finite(rv.get("entry_ref"))
    return e if e and e > 0 else None


def loss_words(order, costs=None, rv=None):
    """"max loss about Rs 4,999.94 if the stop fills at 99.55, costs included" (a market order
    also says the entry psx-risk used), or None when there is no max loss."""
    loss, stop = max_loss(order, costs, rv)
    if loss is None or stop is None:
        return None
    entry = risk_entry(order, rv)
    return "max loss about %s if %sthe stop fills at %s, costs included" % (
        money(loss), ("it fills at %s and " % _p(entry)) if entry else "", _p(stop))


def plain_parts(order, costs=None, rv=None):
    """The ticket in plain words, one part per line."""
    parts = [order_line(order)]
    if _f(order.get("stop_loss")) is not None:
        parts.append("plan stop %s" % _p(order["stop_loss"]))
    if _f(order.get("take_profit")) is not None:
        parts.append("plan target %s" % _p(order["take_profit"]))
    words = loss_words(order, costs, rv)
    if words:
        parts.append(words)
    return parts


def plain(order, costs=None, rv=None):
    """BUY 3,428 DEMO, limit 100.65, good for today, REG board / plan stop 99.55 /
    plan target 101.75 / max loss about Rs 4,999.94 if the stop fills at 99.55, costs included"""
    return " / ".join(plain_parts(order, costs, rv))


def app_line(order):
    """The one line to type into a PSX broker app:
    BUY DEMO, REG board, quantity 3428, LIMIT price 100.65, valid for the day
    SELL DEMO, REG board, quantity 143, STOP LIMIT trigger price 96.25, limit price 95.28, valid for the day"""
    typ = str(order.get("type", "")).lower()
    q = _f(order.get("qty"))
    if _stop_limit(order):
        how = "STOP LIMIT trigger price %s, limit price %s" % (_p(order["trigger_price"]), _p(order["limit_price"]))
    elif typ == "limit" and _f(order.get("limit_price")) is not None:
        how = "LIMIT price %s" % _p(order["limit_price"])
    else:
        how = "%s order" % (typ.replace("_", " ").upper() or "NO TYPE")
    tif = str(order.get("time_in_force", "day")).lower()
    return "%s %s, %s board, quantity %s, %s, %s" % (
        str(order.get("side", "")).upper(), str(order.get("symbol", "?")).upper(),
        str(order.get("board") or "REG").upper(), _n(q) if q is not None else order.get("qty"), how,
        "valid for the day" if tif == "day" else "validity " + tif.upper())


def amount_numbers(order, costs=None, rv=None):
    """Every number a yes may name: the qty, the limit, the stop, the target, the trigger (a
    stop-limit), the max loss and (a market order) the entry psx-risk measured it from."""
    nums = []
    for k in ("qty", "limit_price", "stop_loss", "take_profit", "trigger_price"):
        v = _f(order.get(k))
        if v is not None:
            nums.append(v)
    loss, stop = max_loss(order, costs, rv)
    if loss is not None:
        nums += [v for v in (loss, stop, risk_entry(order, rv)) if v is not None]
    return nums


def parse_yes(reply, order, batch_symbols=(), known_symbols=(), costs=None, rv=None):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why).

    batch_symbols are the other orders' symbols; known_symbols the universe (psx/universe.txt);
    rv is psx-risk's verdict for this order (its max loss). Not a yes: a reply that names one
    of those symbols or another word in capitals (it may be a stock), any number that is not
    this order's qty, limit, stop, target, max loss or entry, a batch word, a doubt word, the
    other side, short selling, margin, futures or options, the other order type, or a change
    (double, half, more, less, x2, tomorrow, Monday ...)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    low = raw.lower()
    words = re.findall(r"[a-z']+", low)
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it, haan)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    rest = set(words[1:])
    if DOUBT_WORDS & rest or DOUBT_PHRASE.search(low):
        return False, "the reply has a condition or a doubt in it; ask again"
    side, typ = _side(order), str(order.get("type", "")).lower()
    said = SIDE_SAYS.get({"buy": "sell", "sell": "buy"}.get(side), set()) & rest
    if said:
        return False, "the reply says %s, but this order is a %s" % (", ".join(sorted(said)), side)
    said = NOT_CASH_WORDS & rest
    if said:
        return False, ("the reply says %s, but this pack trades shares with your own cash only (no short "
                       "selling, margin, futures or options)" % ", ".join(sorted(said)))
    said = TYPE_SAYS.get(OTHER_TYPE.get(typ), set()) & rest
    if said:
        return False, "the reply says %s, but this is a %s order" % (", ".join(sorted(said)), TYPE_WORDS.get(typ, typ))
    if typ == "limit" and ANY_PRICE.search(low):
        return False, "the reply says \"%s\", but this is a limit order: it fills only at its limit or better" % (
            ANY_PRICE.search(low).group(0))
    if typ == "stop_limit" and ANY_PRICE.search(low):
        return False, ("the reply says \"%s\", but this is a stop-limit order: it sells only at its limit or better"
                       % ANY_PRICE.search(low).group(0))
    said = sorted(CHANGE_WORDS & rest) + TIMES.findall(raw)
    if said:
        return False, ("the reply asks for a change (%s); a changed order needs a new psx-risk check and a new "
                       "yes" % ", ".join(said))
    sym = str(order.get("symbol", "")).upper()
    text = raw.upper()
    tokens = set(t.rstrip(".-") for t in SYMBOL_TOKEN.findall(text))
    others = {str(s).upper() for s in batch_symbols if s}
    others |= {str(s).upper() for s in known_symbols if re.search(r"[A-Z]", str(s).upper())}
    named = tokens & (others - {sym})
    for t in WORD_TOKEN.findall(RUPEES.sub(" ", raw)):          # HBL in "yes HBL", with no universe file too
        t = t.rstrip(".-")
        if len(t) > 1 and re.search(r"[A-Z]", t) and t == t.upper() and t != sym and t not in PLAIN_CAPS:
            named.add(t)
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    if re.search(r"\d", sym):                         # a symbol like 786 is not an amount
        text = re.sub(r"(?<![A-Z0-9.,])%s(?![A-Z0-9])" % re.escape(sym), " ", text)
    nums = [float(n.replace(",", "")) for n in NUMBER.findall(RUPEES.sub(" ", text))]
    want = amount_numbers(order, costs, rv)
    wrong = [n for n in nums if not any(abs(n - w) < 0.005 for w in want)]
    if wrong:
        return False, "the reply says %s, but this order is %s" % (", ".join(_n(n) for n in wrong),
                                                                   plain(order, costs, rv))
    return True, "explicit yes for %s" % plain(order, costs, rv)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_doc(path, "broker state", ("orders",)) or {"orders": []}


def order_day(rec):
    """The day of the order a record belongs to: its "of" (a close record names the day of the order
    it closes), else its own day. psx-risk keys an order the same way."""
    return str(rec.get("of") or rec_day(rec))


def latest(state, fp, today):
    """The latest decision recorded for today's order of this fingerprint, or None. A record that
    closes an order of an earlier day ("of" that day) is not a decision on today's order."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and order_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, order, today):
    if not risk:
        return None, "psx-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    fps = {fingerprint(order), fingerprint(dict(order, side=_side(order)))}   # "BUY" is the same order
    for r in risk.get("results") or []:
        if r.get("fingerprint") in fps:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def check_time(risk):
    """When psx-risk ran its check, as naive UTC: the earlier of "checked_at" (UTC, its --now
    or its clock) and "now_karachi" (Karachi time), so the age is never too small. None when
    neither is a time."""
    times = [t for t in (parse_utc(risk.get("checked_at")),) if t is not None]
    try:
        times.append(pkt_to_utc(datetime.datetime.strptime(str(risk.get("now_karachi") or "").strip(),
                                                           "%Y-%m-%d %H:%M")))
    except ValueError:
        pass
    return min(times) if times else None


def stale_check(checked, now):
    """Why psx-risk's check no longer counts at `now` (naive UTC), else "": it has no time, it
    is from after now, or it is more than FRESH_CHECK_MINUTES old (prices and the clock move)."""
    if checked is None:
        return "the psx-risk check has no time in it; run check.py again"
    age = (now - checked).total_seconds() / 60.0
    if age < -1:
        return "the psx-risk check is from %s, after now; run check.py again" % clock_label(to_pkt(checked))
    if age > FRESH_CHECK_MINUTES:
        return ("the psx-risk check is from %s, more than %d minutes ago (it is %s now); run check.py again"
                % (clock_label(to_pkt(checked)), FRESH_CHECK_MINUTES, clock_label(to_pkt(now))))
    return ""


def entry_closed(now_pkt, cfg):
    """Why a new entry cannot go in at this naive Karachi time, else "": PSX must be open
    (market_state) and the time inside the entry window, config_time() of no_entries_before
    and no_entries_after (a key set to null has no bound). psx-risk checks the same."""
    st = market_state(now_pkt, cfg)
    if st["state"] != "open":
        return "no new entries now. PSX: %s" % st["reason"]
    d, t = now_pkt.date(), now_pkt.time().replace(second=0, microsecond=0)
    edge = {}
    for key in ("no_entries_before", "no_entries_after"):
        raw = cfg.get(key, ENTRY_WINDOW[key])
        edge[key] = config_time(parse_hhmm(raw, '"%s" in psx/config.json' % key), d, cfg) if raw else None
    a, b = edge["no_entries_before"], edge["no_entries_after"]
    if a is not None and t < a:
        return "no new entries before %s Karachi time (it is %s)" % (a.strftime("%H:%M"), t.strftime("%H:%M"))
    if b is not None and t > b:
        return "no new entries after %s Karachi time (it is %s)" % (b.strftime("%H:%M"), t.strftime("%H:%M"))
    return ""


def status_of(order, risk, state, today, now=None, cfg=None):
    """(ready, label, risk_result). READY: psx-risk passed this exact order today, its check is
    FRESH_CHECK_MINUTES old or less, an entry is inside the entry window while PSX is open, and
    nobody decided on it today. A failed or skipped order is READY again after a newer check.
    In swing style the entry window is swing_window(): no times, and a limit buy may go in in
    the pre-open. An expired or cancelled order is READY again after a newer check too. A
    stop-limit is never READY in paper mode: a paper stop rests nowhere. A placed stop-limit says
    how to record it cancelled (a market halt cancels every open order), so a new one can go in."""
    now = now or utc_now()
    c = cfg if isinstance(cfg, dict) else {}
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, order, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by psx-risk: " + "; ".join(str(x) for x in rv.get("reasons") or []), rv
    checked = check_time(risk)
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, waiting for the result (ticket.py result)" % rec.get("at"), rv
        decided = parse_utc(rec.get("at"))
        retry = st in RETRY_STATUSES
        if not (retry and checked is not None and decided is not None and checked > decided):
            more = ". To try it again: check.py, then a new yes" if retry else ""
            if st == "placed" and _stop_limit(order):
                more = (". If PSX or you cancelled it (a market halt cancels every open order), record it: ticket.py "
                        "result --fingerprint %s --status cancelled. Then check.py and a new yes" % fp)
            return False, "DONE: %s at %s%s" % (st, rec.get("at"), more), rv
    late = stale_check(checked, now)
    if late:
        return False, "NOT READY: " + late, rv
    if kind_of(order, rv) == "entry":
        if trade_style(c) == "swing":
            shut = swing_window(to_pkt(now), order.get("type"), c)
        else:
            shut = entry_closed(to_pkt(now), c)
        if shut:
            return False, "NOT READY: " + shut, rv
    if str(order.get("type", "")).lower() == "stop_limit" and _is_paper(c):
        return False, "NOT READY: " + PAPER_STOP, rv
    return True, "READY: psx-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as psx-risk saw it (no kind: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if _side(order) == "buy" else "exit"


def market_note(order, now_pkt, cfg):
    """A plain note when PSX cannot take this order right now, else ""."""
    try:
        st = market_state(now_pkt, cfg)
    except ValueError:
        return ""
    if st["state"] == "open":
        return ""
    if st["state"] == "pre-open" and str(order.get("type", "")).lower() == "stop_limit":
        return STOP_PREOPEN
    if st["state"] == "pre-open" and str(order.get("type", "")).lower() == "limit":
        return ("PSX is in the pre-open. A limit order waits for the opening match, and you cannot change or "
                "cancel it until then.")
    if st["state"] == "pre-open":
        return "PSX is in the pre-open, which takes limit orders only. A market order must wait for the open."
    return "PSX now: %s. This order cannot trade until the market opens." % st["reason"]


# ---------------------------------------------------------------- rendering

def _swing_order(order, cfg):
    """True for a swing order: its plan says "style": "swing", or the folder is swing style
    and the plan is not psx-invest's ("style": "invest")."""
    plan = order.get("plan") if isinstance(order.get("plan"), dict) else {}
    if plan.get("style") == "swing":
        return True
    if plan.get("style") == "invest":
        return False
    return trade_style(cfg if isinstance(cfg, dict) else {}) == "swing"


def protect_notes(order):
    """The notes on a stop-limit ticket (SPEC v2 section 5.2): its one-day life, the opening
    price, its fill risk, a halt, and the second sell it blocks."""
    sym = str(order.get("symbol", "?")).upper()
    limit = _p(order["limit_price"])
    return ["PSX orders last one day. If your broker app ends this order at the close, put it in again each day "
            "after the opening match, while you hold %s (ticket.py protect --symbol %s). psx-alerts reminds you at "
            "the first check. Check its validity in your app." % (sym, sym),
            "No order guards the opening price. A gap at the open can go past your stop.",
            "A stop-limit sells only at %s or better. If the price opens or falls under %s, it may not fill: then "
            "sell by hand (ticket.py flatten)." % (limit, limit),
            "A market halt cancels every open order at PSX, and may cancel this one too.",
            "While this order rests, a second sell of the same shares is refused by psx-risk. Cancel it in your app "
            "first, and record it: ticket.py result --fingerprint %s --status cancelled." % fingerprint(order)]


def render(order, idx, total, cfg, risk, state, today, now_pkt=None, now=None):
    """One ticket for people. It never has the line for the broker app: approve prints
    that line, after the yes (the Safety block)."""
    ready, label, rv = status_of(order, risk, state, today, now, cfg)
    costs = trade_costs(cfg)
    paper = _is_paper(cfg)
    head = "PAPER trade: practice, nothing goes to a broker" if paper else \
        "REAL MONEY: you place it yourself in your broker app"
    lines = ["ORDER %d of %d, %s" % (idx, total, head), "  " + order_line(order)]
    stop, target = _f(order.get("stop_loss")), _f(order.get("take_profit"))
    if stop is not None and _side(order) == "buy" and _swing_order(order, cfg):
        lines.append("  plan stop %s: after the buy fills, ticket.py protect makes the stop-limit ticket (psx-risk"
                     " and your yes first)" % _p(stop))
    elif stop is not None:
        lines.append("  plan stop %s: after the buy fills, put it in your app as a stop-loss order, if your app"
                     " has one" % _p(stop))
    if target is not None:
        lines.append("  plan target %s: PSX apps have no bracket order, so psx-alerts watches the stop and the"
                     " target" % _p(target))
    words = loss_words(order, costs, rv)
    if words:
        lines.append("  %s (a fast market can fill it worse)" % words)
    if order.get("why"):
        lines.append("  why: " + str(order["why"]))
    lines.append("  " + label)
    for w in (rv or {}).get("warnings") or []:
        if "only in the plan" in str(w):               # psx-risk's note on the plan stop: the lines above say it
            continue
        lines.append("  note: " + str(w))
    if not (ready or label.startswith("APPROVED")):
        return "\n".join(lines), ready                  # nothing to place
    note = market_note(order, now_pkt, cfg) if now_pkt is not None else ""
    if note:
        lines.append("  note: " + note)
    if _stop_limit(order):
        lines += ["  note: " + n for n in protect_notes(order)]
    if paper and _stop_limit(order):
        lines.append("  " + PAPER_STOP)
    elif paper:
        how = " --filled-price P" if str(order.get("type", "")).lower() != "limit" else ""
        lines.append("  Paper: do not place it in your app. After the yes, ticket.py result --index %d --status"
                     " placed%s records the paper fill." % (idx, how))
    elif ready:
        lines.append("  Real money: the line to type into your broker app comes after your yes.")
    if ready:
        word = ("yes %s" % _p(order["trigger_price"])) if _stop_limit(order) else "yes"
        lines.append("  Reply \"%s\" to approve this one order, or \"no\" to skip it." % word)
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today, now=None):
    """One ticket as data. "app_line" is null until the yes is recorded (approve prints it)."""
    ready, label, rv = status_of(order, risk, state, today, now, cfg)
    costs = trade_costs(cfg)
    loss, stop = max_loss(order, costs, rv)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order, costs, rv),
            "fingerprint": fingerprint(order), "paper": _is_paper(cfg), "broker": cfg.get("broker", "manual"),
            "app_line": app_line(order) if label.startswith("APPROVED") else None,
            "order": {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None},
            "why": order.get("why", ""), "max_loss": loss, "entry_ref": _finite((rv or {}).get("entry_ref")),
            "stop": stop, "target": _f(order.get("take_profit"))}


# ---------------------------------------------------------------- fills -> portfolio

def _count(x):
    """A share count to store: an int when whole."""
    f = round(float(x), 6)
    return int(f) if f == int(f) else f


def is_real(pf):
    """True when psx/portfolio.json says it holds real shares ("real_holdings": true)."""
    return isinstance(pf, dict) and bool(pf.get("real_holdings"))


def mark_real(pf, mark):
    """The portfolio with "real_holdings": true at the top, when mark is true (a fill with
    paper off, or ticket.py fill --real). Paper mode never sets it."""
    if not mark:
        return pf
    out = {"real_holdings": True}
    out.update((k, v) for k, v in pf.items() if k != "real_holdings")
    return out


def maybe_real(pf, cfg, state=None, state_path="psx/state/broker.json"):
    """Why a portfolio with no "real_holdings" key may hold real shares, else "". A ledger from before
    v2, or one made by hand, has no flag even when its shares are real (D39 sets it only on a v2 fill
    with paper off). The signs: a top key the tools never write (a "note", "as_of"), a yes in the log
    made with paper off, or (swing style) a held share with no swing plan, which no swing fill made.
    "real_holdings": false says it is a paper file. A file with no shares and no cash has nothing to
    protect."""
    if not isinstance(pf, dict) or "real_holdings" in pf:
        return ""
    held = [p for p in pf.get("positions") or [] if isinstance(p, dict)
            and (_f(p.get("shares", p.get("qty"))) or 0) > 1e-9]
    if not held and not _f(pf.get("cash")):
        return ""
    extra = [k for k in pf if k not in TOOL_KEYS]
    if extra:
        return "it has %s, which the tools never write" % ", ".join('"%s"' % k for k in extra)
    for rec in (state or {}).get("orders") or []:
        if isinstance(rec, dict) and rec.get("status") == "approved" and rec.get("paper") is False:
            return "%s has a yes made with paper off (%s %s on %s)" % (
                state_path, str(rec.get("symbol") or "?").upper(), _side(rec) or "order", rec_day(rec))
    if trade_style(cfg) == "swing":
        for p in held:
            plan = p.get("plan")
            if not (isinstance(plan, dict) and plan.get("style") == "swing"):
                return "%s in it has no swing plan, so no swing fill made it" % str(
                    p.get("ticker") or p.get("symbol") or "?").upper()
    return ""


def real_guard(pf, cfg, real, path, state=None, state_path="psx/state/broker.json"):
    """A paper fill never writes into a file of real shares (D39): TicketError before
    anything is written. --real ("a real fill from my broker app") passes. A file with no
    flag that may hold real shares (maybe_real) is refused too, until the user says which
    it is."""
    if not _is_paper(cfg) or real:
        return
    if is_real(pf):
        raise TicketError(REAL_GUARD % path)
    why = maybe_real(pf, cfg, state, state_path)
    if why:
        raise TicketError(MAYBE_REAL % (path, why))


def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date, plan=None, real=False, state=None,
               state_path="psx/state/broker.json"):
    """Record a filled order in psx/portfolio.json: cash and shares, with a lot per buy (a
    sell takes from the oldest lots first; a lot with no date counts as the oldest). The
    estimated costs of this leg (leg_cost with trade_costs, SPEC section 5) come off the cash
    and are noted in the lot as "costs". Whole shares only. With "day_trade_one_side": true
    (day style only), a sell of shares bought the same day pays no commission. The cash
    moves at once (PSX settles T+1; a day trade nets). A buy that opens a position gets
    `plan` (a swing plan, ledger_plan()) as pos["plan"]; a day buy gets no "plan" key.
    A paper fill into a file with "real_holdings": true, or into one that may hold real
    shares (maybe_real, with the log `state`), is refused (real=True passes); a fill with
    paper off, or real=True, sets the flag. Returns (portfolio, costs)."""
    side = str(side or "").strip().lower()
    if side not in ("buy", "sell"):
        raise TicketError("a fill is a buy or a sell, not %r" % (side,))
    pf = read_doc(portfolio_path, "portfolio", ("positions",))
    real_guard(pf, cfg, real, portfolio_path, state, state_path)
    mark = bool(real) or not _is_paper(cfg)
    if pf is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": budget, "positions": []}
    symbol = str(symbol).strip().upper()
    qty = whole_shares(qty)
    price = _f(price)
    if price is None or price != price or not 0 < price < float("inf"):
        raise TicketError("a fill needs a price above 0, like 100.65")
    costs = trade_costs(cfg)
    value = _cents(Decimal(str(price)) * qty)
    cash = Decimal(str(_f(pf.get("cash")) or 0))
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker") or p.get("symbol") or "").upper() == symbol:
            pos = p
    if side == "buy":
        fee = leg_cost(price, qty, costs, True)
        opened = pos is None
        if pos is None:
            pos = {"ticker": symbol, "shares": 0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": value,
                     "costs": fee})
        pos["shares"] = _count((_f(pos.get("shares", pos.get("qty"))) or 0) + qty)
        if opened and plan:
            pos["plan"] = plan
        pf["cash"] = _cents(cash - Decimal(str(value)) - Decimal(str(fee)))
        pf = mark_real(pf, mark)
        write_json(portfolio_path, pf)
        return pf, fee
    held = (_f(pos.get("shares", pos.get("qty"))) or 0) if pos else 0
    if pos is None or held + 1e-9 < qty:
        raise TicketError("cannot record selling %s %s: the portfolio holds %s (PSX bans selling shares you "
                          "do not hold)" % (_n(qty), symbol, _n(held)))
    left, same_day = qty, True
    for lot in sorted(pos.get("lots", []), key=lambda l: str(l.get("date") or "")):
        n = _f(lot.get("shares")) or 0
        take = min(left, n)
        if take <= 0:
            continue
        same_day = same_day and str(lot.get("date")) == date
        keep = Decimal(1) - Decimal(str(take)) / Decimal(str(n))
        lot["cost"] = _cents(Decimal(str(_f(lot.get("cost")) or 0)) * keep)
        if lot.get("costs") is not None:
            lot["costs"] = _cents(Decimal(str(_f(lot.get("costs")) or 0)) * keep)
        lot["shares"] = _count(n - take)
        left -= take
    same_day = same_day and left <= 1e-9
    pos["lots"] = [l for l in pos.get("lots", []) if (_f(l.get("shares")) or 0) > 1e-9]
    pos["shares"] = _count(held - qty)
    if pos["shares"] <= 1e-9:
        pf["positions"].remove(pos)
    fee = leg_cost(price, qty, costs, not (costs.get("day_trade_one_side") is True and same_day))
    pf["cash"] = _cents(cash + Decimal(str(value)) - Decimal(str(fee)))
    pf = mark_real(pf, mark)
    write_json(portfolio_path, pf)
    return pf, fee


def fill_record(now, today, symbol, side, qty, price, fee):
    """A fill in the log, so psx-risk can see what was bought and sold today."""
    side = str(side or "").strip().lower()
    return {"at": pkt_iso(now), "date": today, "status": "filled", "symbol": str(symbol).upper(), "side": side,
            "qty": _count(qty), "price": float(price), "costs": fee,
            "plain": "%s %s %s at %s" % (side.upper(), _shares(qty), str(symbol).upper(), _p(price))}


def ledger_plan(src, day, price, qty, cfg, source):
    """The swing plan of a new position in psx/portfolio.json (SPEC v2 section 2.5). src holds
    the plan's stop, target, exit_by, exit_by_time, trend_sessions and setup; day is the buy
    day (the fill's date). "stop_at_buy" is the stop when the position opened, and it never
    changes. "max_loss" is the loss from this fill to the stop, costs included (0 when the
    stop is at or over the fill price)."""
    stop, target = _finite(src.get("stop")), _finite(src.get("target"))
    loss = 0.0
    if stop is not None and stop < price:
        loss = max_loss_at_stop(price, stop, qty, trade_costs(cfg))
    trend = src.get("trend_sessions")
    if trend is None:
        trend = swing_settings(cfg).get("trend_exit_sessions")
    return {"style": "swing", "date": day, "stop": stop, "stop_at_buy": stop, "target": target,
            "exit_by": src.get("exit_by"), "exit_by_time": src.get("exit_by_time"), "trend_sessions": trend,
            "setup": src.get("setup"), "max_loss": loss, "scaled": [], "from": source}


def order_plan(order):
    """The plan of a swing order in psx/orders.json, with the order's own stop and target
    (the fields psx-risk checked) on top."""
    plan = dict(order.get("plan") if isinstance(order.get("plan"), dict) else {})
    if _f(order.get("stop_loss")) is not None:
        plan["stop"] = _f(order["stop_loss"])
    if _f(order.get("take_profit")) is not None:
        plan["target"] = _f(order["take_profit"])
    return plan


def plan_words(plan, path):
    """ "Swing plan from psx/orders.json: stop 96.25, target 105.64, exit by Fri 2026-11-27 at 4:20 PM PKT." """
    parts = ["stop %s" % (_p(plan["stop"]) if plan.get("stop") is not None else "none"),
             "target %s" % (_p(plan["target"]) if plan.get("target") is not None else "none")]
    try:
        when = day_label(datetime.date.fromisoformat(str(plan.get("exit_by"))))
        if plan.get("exit_by_time"):
            when += " at %s PKT" % clock_label(parse_hhmm(plan["exit_by_time"], "exit_by_time"))
        parts.append("exit by " + when)
    except ValueError:
        pass
    return "Swing plan from %s: %s." % (path, ", ".join(parts))


def hold_words(plan, sym, portfolio_path, filled=True):
    """The next step after a swing buy fills (SPEC v2 2.4 psx-broker item 7). filled=False: the
    buy is placed, and its fill is not recorded yet."""
    out = "%s: ticket.py protect --symbol %s for the stop-limit order." % ("Next" if filled else "After the fill",
                                                                          sym)
    try:
        out += " Hold up to %s." % day_label(datetime.date.fromisoformat(str(plan.get("exit_by"))))
    except ValueError:
        pass
    return out + " The alerts come from %s %s." % (portfolio_path, "now" if filled else "then")


def newest_record(state, fp):
    """The newest record of this fingerprint in psx/state/broker.json, of any day, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp:
            found = rec
    return found


def order_records(state, fp):
    """{order day: the newest record of that day's order} for one fingerprint. The same fingerprint
    is a new order each day it is put in again (a protect sell with the same trigger and qty)."""
    out = {}
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp:
            out[order_day(rec)] = rec
    return out


def order_fill(state, fp, day):
    """(shares filled, shares of the order) for the order of this fingerprint on `day`: the shares
    of its symbol and side in "filled" records on or after that day, and its qty (None when no
    record of it has one). Records do not link a fill to an order, so a fill of another order of
    the same stock and side counts too (as in psx-risk)."""
    recs = [r for r in state.get("orders", []) if r.get("fingerprint") == fp and order_day(r) == day]
    if not recs:
        return 0.0, None
    sym = str(recs[-1].get("symbol") or "").strip().upper()
    side = str(recs[-1].get("side") or "").strip().lower()
    want = None
    for r in recs:
        q = _finite(r.get("qty"))
        if q is not None and q > 0:
            want = q
    got = 0.0
    for r in state.get("orders", []):
        if (r.get("status") == "filled" and str(r.get("symbol") or "").strip().upper() == sym
                and str(r.get("side") or "").strip().lower() == side and rec_day(r) >= day):
            got += _finite(r.get("qty")) or 0.0
    return got, want


def all_filled(got, want):
    """True when the fills cover the order: every share filled (or a fill and no qty to compare)."""
    return got > 1e-9 and (want is None or got >= want - 1e-9)


def is_open(state, fp, day):
    """True when the order of this fingerprint on `day` may still rest at the broker: its newest
    record is "approved" or "placed", and not all of it filled (a part fill leaves the rest open)."""
    rec = order_records(state, fp).get(day)
    return bool(rec) and rec.get("status") in OPEN_STATUSES and not all_filled(*order_fill(state, fp, day))


def part_words(got, want):
    """The line after a part-filled order is closed."""
    return ("%s of its %s shares had filled: that fill stays in the log. The other %s are closed now."
            % (_shares(got), _shares(want), _shares(want - got)))


def rest_words(state, fp, day, sym):
    """After a part fill of a buy (F60, D57): the line that says the rest may still fill today, else "".
    psx-risk counts the rest as a buy with no fill yet until the close."""
    got, want = order_fill(state, fp, day)
    if got <= 1e-9 or want is None or all_filled(got, want):
        return ""
    return ("The other %s shares may still fill today: record them with ticket.py fill --symbol %s --side buy --qty Q "
            "--price P. A PSX order ends at the close." % (_shares(want - got), sym))


def open_buy_rest(state, sym, day):
    """rest_words() for the newest buy of sym on `day` that is still open (part filled), else ""."""
    fps = []
    for rec in state.get("orders", []):
        fp = rec.get("fingerprint")
        if (fp and fp not in fps and order_day(rec) == day and str(rec.get("symbol") or "").strip().upper() == sym
                and str(rec.get("side") or "").strip().lower() == "buy"):
            fps.append(fp)
    still = [fp for fp in fps if is_open(state, fp, day)]
    return rest_words(state, still[-1], day, sym) if still else ""


def plan_kind(order):
    """The "style" of an order's plan block ("swing", "invest", ...), or "" with no plan block."""
    plan = order.get("plan") if isinstance(order, dict) else None
    return str(plan.get("style") or "").strip().lower() if isinstance(plan, dict) else ""


def open_protects(state, symbol, today):
    """[(fingerprint, shares filled, shares of the order)] of today's stop-limit sells of this symbol
    that may rest at the broker (D58): today's order of that fingerprint is "approved" or "placed",
    and not all of it filled. A record that closes an order of an earlier day does not count."""
    newest, keys = {}, []
    for rec in state.get("orders", []):
        fp = rec.get("fingerprint")
        if not fp or order_day(rec) != today:
            continue
        if fp not in newest:
            keys.append(fp)
        newest[fp] = rec
    out = []
    for fp in keys:
        rec = newest[fp]
        if (rec.get("status") in OPEN_STATUSES and str(rec.get("symbol") or "").strip().upper() == symbol
                and str(rec.get("side") or "").strip().lower() == "sell" and "stop-limit" in str(rec.get("plain") or "")):
            got, want = order_fill(state, fp, today)
            if not all_filled(got, want):
                out.append((fp, got, want))
    return out


# ---------------------------------------------------------------- commands

def _date(text, flag):
    try:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(text)):
            raise ValueError(text)
        return datetime.date.fromisoformat(str(text))
    except ValueError:
        raise TicketError("%s wants a date like 2026-10-29" % flag)


def _clock(a):
    try:
        now = parse_now(getattr(a, "now", None))
    except ValueError as e:
        raise TicketError(str(e))
    today = getattr(a, "today", None) or to_pkt(now).date().isoformat()
    _date(today, "--today")
    return now, today


def _now_pkt(now, today):
    """Karachi time of `now`, or None when --today names another day (no market note then)."""
    t = to_pkt(now)
    return t if t.date().isoformat() == today else None


def _is_paper(cfg):
    return cfg.get("paper", True) is not False


def blocked_set(value):
    """The "blocked" tickers as a set, the rule psx-budget and psx-risk use: a string is one ticker, or a
    few split on commas ("ABC, XYZ"). Anything but a list or a string is a plain error, never ignored."""
    if value is None:
        return set()
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        raise TicketError("\"blocked\" in psx/config.json must be a list of tickers, like [\"ABC\", \"XYZ\"]")
    return {str(b).strip().upper() for b in value if b is not None and str(b).strip()}


def is_blocked(cfg, sym):
    """True when sym is on the config's "blocked" list (the long-term shares). A bad list never stops
    an exit ticket: then False."""
    try:
        return sym in blocked_set(cfg.get("blocked") if isinstance(cfg, dict) else None)
    except TicketError:
        return False


def _load(a):
    doc = read_json(a.orders, "orders file (run psx-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    if not all(isinstance(o, dict) for o in orders):
        raise TicketError("every order in %s must be a JSON object" % a.orders)
    cfg = read_doc(a.config, "config") or {}
    risk = read_doc(a.risk, "risk check", ("results",))
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today, now) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today, _now_pkt(now, today), now)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Nothing here places an order. "
          "Education and tooling, not financial advice.")
    return 0 if any_ready else 1


def clock_off(a, now, today):
    """Why approve's clock is not the clock of this computer, else "": --now more than CLOCK_SLACK
    seconds away from it, or --today another Karachi day. --now and --today are for replays, tests
    and paper."""
    real = utc_now()
    if getattr(a, "now", None) and abs((now - real).total_seconds()) > CLOCK_SLACK:
        return "--now says %s %s, but the clock of this computer says %s %s (Karachi time)" % (
            day_label(to_pkt(now)), clock_label(to_pkt(now)), day_label(to_pkt(real)), clock_label(to_pkt(real)))
    real_day = to_pkt(real).date().isoformat()
    if getattr(a, "today", None) and today != real_day:
        return "--today says %s, but today in Karachi is %s" % (today, real_day)
    return ""


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today, now, cfg)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    costs = trade_costs(cfg)
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders], universe_symbols(a.universe),
                        costs, rv)
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order, costs, rv)                  # psx-risk's number: the daily loss adds it up
    paper = _is_paper(cfg)
    swing = trade_style(cfg) == "swing"
    off = clock_off(a, now, today) if swing and not paper else ""     # day style: as in v1, no word (A3)
    if off and kind_of(order, rv) == "entry":
        raise TicketError("%s. A real-money yes for a buy counts only on the real clock, so nothing was recorded. "
                          "Run approve again without --now and --today." % off)
    if off:                                                  # a swing exit: a note
        print("Note: %s. A real-money yes should be on the real clock: check in your app that PSX is open and that "
              "the order still fits now." % off, file=sys.stderr)
    rec = {"at": pkt_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": _side(order),
           "qty": order.get("qty"), "kind": kind_of(order, rv), "max_loss": loss, "stop": stop,
           "plain": plain(order, costs, rv), "status": "approved", "reply": a.reply,
           "paper": paper, "broker": cfg.get("broker", "manual")}
    if _finite((rv or {}).get("entry_ref")) is not None:
        rec["entry_ref"] = _finite(rv["entry_ref"])          # the entry psx-risk measured the max loss from
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order, costs, rv)))
    swing_buy = rec["kind"] == "entry" and _swing_order(order, cfg)
    if paper:
        limit = str(order.get("type", "")).lower() == "limit" and _f(order.get("limit_price")) is not None
        print("  In your broker app: " + app_line(order))
        print("Paper trade: nothing goes to a broker, so do not place it in your app. Record the paper fill:")
        print("  ticket.py result --index %d --status placed%s" % (
            a.index, "" if limit else " --filled-price P   (a market order has no price of its own)"))
        if swing_buy:
            print(expired_words(a.index, rec["fingerprint"]))
        return 0
    print("Now place it yourself in your broker app:")
    print("  " + app_line(order))
    print("Free PSX prices are about 5 minutes late: check the price in your app before you place it.")
    print("Your app asks for your passwords and PIN. Type them in the app only, never in this chat.")
    print("Then record what happened: ticket.py result --index %d --status placed --broker-id <the app's order "
          "number>, or --status skipped, or --status failed --note \"why\"." % a.index)
    if swing_buy:
        print(expired_words(a.index, rec["fingerprint"]))
    if _stop_limit(order):                 # the same fingerprint can be a new order each day: name this day's
        print("If you cancel it later in your app, record it: ticket.py result --fingerprint %s --date %s --status "
              "cancelled." % (rec["fingerprint"], today))
    return 0


def expired_words(index, fp):
    """After the yes for a swing buy: a PSX order lasts one day (D25, D33)."""
    return ("PSX orders last one day: a limit buy that does not fill today ends at the close. Do not chase it. "
            "If it does not fill, record it: ticket.py result --index %d --status expired (on a later day: "
            "--fingerprint %s)." % (index, fp))


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    costs = trade_costs(cfg)
    rec = {"at": pkt_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": _side(order),
           "plain": plain(order, costs), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was placed." % (a.index, plain(order, costs)))
    return 0


def _fill_for(a, order, paper):
    """(qty, price) of the fill that `result` records, or None. Checked before anything is written.
    A stop-limit never fills here: it fills at the broker later, and ticket.py fill records that."""
    if a.status != "placed":
        if a.filled_qty or a.filled_price:
            raise TicketError("--filled-qty and --filled-price go with --status placed only")
        return None
    if str(order.get("type", "")).lower() == "stop_limit":
        if a.filled_qty or a.filled_price:
            raise TicketError("a stop-limit order fills at your broker later: when it fills, record it with "
                              "ticket.py fill, not with --filled-qty or --filled-price here")
        return None
    want = whole_shares(order.get("qty"), "the order's qty")
    qty = whole_shares(a.filled_qty, "--filled-qty") if a.filled_qty else want
    if qty > want:
        raise TicketError("--filled-qty %s is more than the order's %s shares" % (_n(qty), _n(want)))
    if a.filled_price:
        price = _f(a.filled_price)
        if price is None or price != price or not 0 < price < float("inf"):
            raise TicketError("--filled-price wants a price above 0, like 100.65")
        return qty, price
    if not paper:
        if a.filled_qty:
            raise TicketError("--filled-qty needs --filled-price too (the price your app shows for the fill)")
        return None
    if str(order.get("type", "")).lower() == "limit" and _f(order.get("limit_price")):
        return qty, _f(order["limit_price"])
    raise TicketError("a paper market order has no price to fill at: pass --filled-price P (the price you see "
                      "now; free PSX prices are about 5 minutes late)")


def _day_words(day):
    """ "2026-10-29" -> "Thu 2026-10-29"; anything else stays as it is."""
    try:
        return day_label(datetime.date.fromisoformat(str(day)))
    except ValueError:
        return str(day)


def close_record(now, today, index, rec, fp, a, day):
    """The record that closes an order as expired, cancelled or skipped (SPEC v2 2.5): the shape
    of "placed", with "date" the day it was recorded and "of" the day of the order it closes."""
    new = {"at": pkt_iso(now), "date": today, "index": index, "fingerprint": fp,
           "symbol": str(rec.get("symbol") or "").strip().upper(), "side": str(rec.get("side") or "").strip().lower(),
           "plain": rec.get("plain"), "status": a.status, "of": day}
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    return new


def find_order(state, symbol, day, path):
    """(fingerprint, newest record) of the one order of `symbol` with a yes on `day` that is
    still open (an order that filled is not). Only that day's order: never one of another day
    with the same fingerprint. TicketError when there is none, or when two match (then use
    --fingerprint)."""
    sym = str(symbol).strip().upper()
    matched = []
    for rec in state.get("orders", []):
        fp = rec.get("fingerprint")
        if (fp and fp not in matched and rec.get("status") in OPEN_STATUSES and order_day(rec) == day
                and str(rec.get("symbol") or "").strip().upper() == sym):
            matched.append(fp)
    if not matched:
        raise TicketError("no order of %s with a yes on %s in %s" % (sym, day, path))
    still = [fp for fp in matched if is_open(state, fp, day)]
    if len(still) > 1:
        raise TicketError("%d orders of %s on %s match: name one with --fingerprint (%s)"
                          % (len(still), sym, day, ", ".join(still)))
    fp = (still or matched)[0]
    return fp, order_records(state, fp)[day]


def pick_order_day(state, fp, a):
    """(day, newest record) of the order of fingerprint fp that result closes: the day of --date,
    else the newest day whose order is still open, else the day of the newest record."""
    days = order_records(state, fp)
    if not days:
        raise TicketError("no order with the fingerprint %s in %s" % (fp, a.state))
    if a.date:
        day = _date(a.date, "--date").isoformat()
        if day not in days:
            raise TicketError("no order with the fingerprint %s on %s in %s (its orders are of %s)"
                              % (fp, day, a.state, ", ".join(sorted(days))))
        return day, days[day]
    still = [d for d in sorted(days) if is_open(state, fp, d)]
    day = still[-1] if still else order_day(newest_record(state, fp))
    return day, days[day]


def _result_of_record(a):
    """result --fingerprint FP [--date D], or --symbol X --date D (D33): close one order that is
    still "approved" or "placed", as expired, skipped or cancelled. Orders are kept by day: the
    same fingerprint put in again on another day is another order, and --date names its day. An
    order with a part fill can be closed as expired or cancelled: that closes the rest. It needs
    no psx/orders.json, and it writes that one record only."""
    if a.index is not None or (a.fingerprint and a.symbol):
        raise TicketError("name the order one way: --index N, or --fingerprint FP, or --symbol X --date D")
    if not a.fingerprint and not (a.symbol and a.date):
        raise TicketError("--symbol and --date go together, like --symbol DEMO --date 2026-10-29")
    if a.status not in CLOSE_WORDS:
        raise TicketError("with --fingerprint, or --symbol and --date, only --status expired, skipped or cancelled "
                          "can be recorded. A fill is recorded with ticket.py fill")
    if a.filled_qty or a.filled_price:
        raise TicketError("--filled-qty and --filled-price go with --status placed only")
    state = load_state(a.state)
    now, today = _clock(a)
    if a.fingerprint:
        fp = a.fingerprint.strip().lower()
        day, rec = pick_order_day(state, fp, a)
    else:
        day = _date(a.date, "--date").isoformat()
        fp, rec = find_order(state, a.symbol, day, a.state)
    sym, side = str(rec.get("symbol") or "").strip().upper(), str(rec.get("side") or "").strip().lower()
    if rec.get("status") not in OPEN_STATUSES:
        raise TicketError('the newest record of this order is "%s" on %s, not approved or placed: nothing to '
                          'record' % (rec.get("status"), rec_day(rec)))
    got, want = order_fill(state, fp, day)
    if all_filled(got, want):
        raise TicketError("%s %s has a fill recorded on or after %s, so this order is not open: nothing to record"
                          % (side, sym, day))
    part = got > 1e-9
    if part and a.status == "skipped":
        raise TicketError("%s of the %s shares of this order filled on or after %s, so it was placed, not skipped. "
                          "Record the rest as cancelled or expired" % (_shares(got), _shares(want), day))
    what = str(rec.get("plain") or "").split(" / ")[0]
    head = "Recorded the %s %s of %s as %s: %s." % (sym, side, _day_words(day), a.status,
                                                    (PART_WORDS if part else CLOSE_WORDS)[a.status])
    state.setdefault("orders", []).append(close_record(now, today, rec.get("index"), rec, fp, a, day))
    write_json(a.state, state)
    print(head)
    if what:
        print("  %s (fingerprint %s)" % (what, fp))
    if part:
        print(part_words(got, want))
    others = [d for d in sorted(order_records(state, fp)) if d != day and is_open(state, fp, d)]
    if others and not a.date:
        print("This fingerprint also has an open order of %s in %s. %s, add --date %s." % (
            " and ".join(_day_words(d) for d in others), a.state,
            "To record that one" if len(others) == 1 else "To record one of them", others[-1]))
    print("Nothing else was written. psx-risk counts this order as closed now.")
    return 0


def real_note(before, pf, path):
    """One line when this fill set "real_holdings": true, else ""."""
    if is_real(pf) and not is_real(before):
        return '%s now says "real_holdings": true: it holds real shares, so a paper fill can never change it.' % path
    return ""


def no_orders_real(a):
    """The words for result with no psx/orders.json in a paper folder of real shares ("real_holdings": true),
    like the long-term folder: psx-invest writes no orders file there, so the way is ticket.py fill --real (F45),
    not psx-budget. "" anywhere else: then the v1 words stay."""
    if os.path.exists(a.orders):
        return ""
    try:
        cfg = read_doc(a.config, "config") or {}
        pf = read_doc(a.portfolio, "portfolio", ("positions",))
    except (TicketError, OSError, ValueError):
        return ""                          # a broken file gets its own words from the normal path (as in v1)
    if not _is_paper(cfg) or not is_real(pf):
        return ""
    return ('no orders file at %s, so there is no order to record. %s holds real shares ("real_holdings": true) '
            'and paper is on: then psx-invest writes no orders file. To record a real fill from your broker app: '
            'ticket.py fill --symbol X --side buy --qty Q --price P --real (or --side sell)' % (a.orders, a.portfolio))


def cmd_result(a):
    if a.fingerprint or a.symbol or a.date:
        return _result_of_record(a)
    why = no_orders_real(a)
    if why:
        raise TicketError(why)
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    closing = a.status in ("expired", "cancelled")
    if closing and (not rec or rec.get("status") not in OPEN_STATUSES):
        raise TicketError("order %d has no yes recorded today, so there is nothing to record as %s. For an order of "
                          "an earlier day: ticket.py result --fingerprint FP --status %s" % (a.index, a.status, a.status))
    if not closing and (not rec or rec.get("status") != "approved"):
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    paper = _is_paper(cfg)
    fill = _fill_for(a, order, paper)
    side = str(rec.get("side") or "").strip().lower()
    if closing:
        got, want = order_fill(state, fp, today)
        if all_filled(got, want):
            raise TicketError("%s %s has a fill recorded today, so order %d is not open: nothing to record"
                              % (side, rec["symbol"], a.index))
        part = got > 1e-9                  # a part fill: this closes the rest of the order
        state.setdefault("orders", []).append(close_record(now, today, a.index, rec, fp, a, today))
        write_json(a.state, state)
        print("Recorded order %d as %s: %s." % (a.index, a.status, (PART_WORDS if part else CLOSE_WORDS)[a.status]))
        if part:
            print(part_words(got, want))
        return 0
    new = {"at": pkt_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": side, "plain": rec["plain"], "status": a.status}
    for k in ("qty", "kind", "max_loss", "stop", "entry_ref"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state.setdefault("orders", []).append(new)
    lines = ["Recorded order %d as %s." % (a.index, a.status)]
    swing = rec.get("kind") == "entry" and _swing_order(order, cfg)
    if fill:
        block = None
        if side == "buy" and swing:      # the ledger gets the swing plan (D28)
            block = ledger_plan(order_plan(order), today, fill[1], fill[0], cfg, a.orders)
        before = read_doc(a.portfolio, "portfolio", ("positions",))
        pf, fee = apply_fill(a.portfolio, cfg, rec["symbol"], side, fill[0], fill[1], today, plan=block,
                             state=state, state_path=a.state)
        state["orders"].append(fill_record(now, today, rec["symbol"], side, fill[0], fill[1], fee))
        lines.append("%s: %s %s %s at %s, costs about %s. Cash now %s in %s." % (
            "Paper fill" if paper else "Fill", side.upper(), _shares(fill[0]), rec["symbol"],
            _p(fill[1]), money(fee), money(pf["cash"]), a.portfolio))
        if side == "buy" and (swing or plan_kind(order) == "invest"):    # F60: the rest may still fill (D57)
            rest = rest_words(state, fp, today, rec["symbol"])           # day style: as in v1, no line
            if rest:
                lines.append(rest)
        note = real_note(before, pf, a.portfolio)
        if note:
            lines.append(note)
    elif a.status == "placed":
        lines.append("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
                     % (rec["symbol"], side))
        if _stop_limit(order):
            lines.append("It rests at your broker until it fills or the day ends. Before any other sell of %s, "
                         "cancel it in your app and record it: ticket.py result --fingerprint %s --date %s --status "
                         "cancelled." % (rec["symbol"], fp, today))
    write_json(a.state, state)
    print("\n".join(lines))
    if a.status == "placed" and swing:
        print(hold_words(order_plan(order), rec["symbol"], a.portfolio, filled=bool(fill)))
        return 0
    if a.status == "placed" and rec.get("kind") == "entry":
        plan = order.get("plan") if isinstance(order.get("plan"), dict) else {}
        stop, target = _f(order.get("stop_loss")), _f(order.get("take_profit"))
        if stop is not None and not paper:
            print("Next: after the buy fills, put the stop %s in your app as a stop-loss order, if your app "
                  "has one." % _p(stop))
        if stop is not None or target is not None:
            print("psx-alerts watches the plan (alerts.py add --from-plan): stop %s, target %s." % (
                _p(stop) if stop is not None else "none", _p(target) if target is not None else "none"))
        if plan.get("flat_by"):
            print("Be flat by %s Karachi time: ticket.py flatten --symbol %s." % (plan["flat_by"], rec["symbol"]))
    return 0


def _quiet_json(path):
    """A JSON file, or None when it is missing or not valid JSON (a fill never stops for it)."""
    try:
        return read_json(path, "file", required=False)
    except (TicketError, OSError, ValueError):
        return None


def _invest_buy(path, sym):
    """True when psx/orders.json holds a psx-invest buy of sym: psx-risk counts the rest of such a buy, filled in
    part, as a buy with no fill yet (D57). A missing or broken file is no such buy."""
    doc = _quiet_json(path)
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    return any(isinstance(o, dict) and str(o.get("symbol") or "").strip().upper() == sym and _side(o) == "buy"
               and plan_kind(o) == "invest" for o in (orders if isinstance(orders, list) else []))


def _fill_price(text):
    """The price of a fill: above 0 (the same words as apply_fill)."""
    price = _f(text)
    if price is None or price != price or not 0 < price < float("inf"):
        raise TicketError("a fill needs a price above 0, like 100.65")
    return price


def fill_plan(a, cfg, sym, day, price, qty):
    """(ledger plan, where it came from) for a swing buy fill that opens a position (SPEC v2 2.4
    psx-broker item 2): --stop, --target and --exit-by; else the newest swing buy of sym in
    psx/orders.json; else psx/plan.json. A plan counts only when its buy day is the fill's
    date. TicketError (the "No swing plan" line) when there is none: nothing is written."""
    flags = (a.stop, a.target, a.exit_by)
    if any(v is not None for v in flags):
        if any(v is None for v in flags):
            raise TicketError("--stop, --target and --exit-by go together: give all three")
        stop, target = _finite(a.stop), _finite(a.target)
        if stop is None or stop <= 0 or target is None or target <= 0:
            raise TicketError("--stop and --target want prices above 0, like 96.25 and 105.64")
        stop, target = round_down_tick(stop), round_half_up_tick(target)
        if stop >= target:
            raise TicketError("--stop %s must be under --target %s" % (_p(stop), _p(target)))
        exit_by = _date(a.exit_by, "--exit-by")
        if exit_by.isoformat() < day:
            raise TicketError("--exit-by %s is before the fill date %s" % (exit_by.isoformat(), day))
        open_day, why = trading_day(exit_by, cfg)
        if not open_day:
            raise TicketError("--exit-by %s is not a PSX trading day (%s): give a day PSX is open"
                              % (exit_by.isoformat(), why))
        t = parse_hhmm(cfg.get("flat_by") or "15:20", '"flat_by" in psx/config.json')
        src = {"stop": stop, "target": target, "exit_by": exit_by.isoformat(),
               "exit_by_time": config_time(t, exit_by, cfg).strftime("%H:%M"), "setup": None}
        return ledger_plan(src, day, price, qty, cfg, "by hand"), "your --stop, --target and --exit-by"
    old = None
    doc = _quiet_json(a.orders)
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    pick = None
    for o in orders if isinstance(orders, list) else []:
        if (isinstance(o, dict) and str(o.get("symbol") or "").strip().upper() == sym and _side(o) == "buy"
                and isinstance(o.get("plan"), dict) and o["plan"].get("style") == "swing"):
            pick = o
    if pick is not None:
        made = parse_utc(doc.get("created_at")) if isinstance(doc, dict) else None
        when = pick["plan"].get("date") or (to_pkt(made).date().isoformat() if made else None)
        if when == day:
            return ledger_plan(order_plan(pick), day, price, qty, cfg, a.orders), a.orders
        old = (a.orders, when)
    plan = _quiet_json(a.plan)
    if isinstance(plan, dict) and str(plan.get("symbol") or "").strip().upper() == sym and plan.get("style") == "swing":
        if plan.get("date") == day:
            return ledger_plan(plan, day, price, qty, cfg, a.plan), a.plan
        old = old or (a.plan, plan.get("date"))
    why = NO_PLAN % (sym, day)
    if old:
        why += " The newest swing plan of %s (%s) is for %s." % (sym, old[0], old[1] or "another day")
    raise TicketError(why)


def _fill_bonus(a, cfg, now, date):
    """fill --bonus N (D56): N bonus shares at cost 0 and costs 0, so the old cost is spread over
    all the shares (FBR rule 13N). No cash change. A "bonus" record, not a "filled" one, so
    psx-risk never counts it as a trade. The real-holdings guard holds (--real passes)."""
    if any(v is not None for v in (a.side, a.qty, a.price, a.stop, a.target, a.exit_by)):
        raise TicketError("--bonus records bonus shares only: give --symbol and --bonus N (and --date), not --side, "
                          "--qty, --price, --stop, --target or --exit-by")
    n = whole_shares(a.bonus, "--bonus")
    sym = a.symbol.strip().upper()
    state = load_state(a.state)
    pf = read_doc(a.portfolio, "portfolio", ("positions",))
    pos = None
    for p in (pf or {}).get("positions", []):
        if str(p.get("ticker") or p.get("symbol") or "").upper() == sym:
            pos = p
    held = (_f(pos.get("shares", pos.get("qty"))) or 0) if pos else 0
    if pos is None or held <= 1e-9:
        raise TicketError("no %s shares in %s: bonus shares come only on shares you hold" % (sym, a.portfolio))
    real_guard(pf, cfg, a.real, a.portfolio, state, a.state)
    lots = pos.setdefault("lots", [])
    lots.append({"id": "%s-%d" % (sym, len(lots) + 1), "date": date, "shares": n, "cost": 0.0, "costs": 0.0,
                 "bonus": True})
    pos["shares"] = _count(held + n)
    out = mark_real(pf, bool(a.real) or not _is_paper(cfg))
    write_json(a.portfolio, out)
    state.setdefault("orders", []).append({"at": pkt_iso(now), "date": date, "status": "bonus", "symbol": sym,
                                           "qty": n, "plain": "%s bonus shares of %s at cost 0" % (_shares(n), sym)})
    write_json(a.state, state)
    print("Recorded %s bonus shares of %s at cost 0: %s shares now, the same total cost. %s"
          % (_shares(n), sym, _shares(pos["shares"]), BONUS_WORDS))
    note = real_note(pf, out, a.portfolio)
    if note:
        print(note)
    return 0


def cmd_fill(a):
    cfg = read_doc(a.config, "config") or {}
    try:
        now = parse_now(a.now)
    except ValueError as e:
        raise TicketError(str(e))
    date = a.date or to_pkt(now).date().isoformat()
    _date(date, "--date")
    if a.bonus is not None:
        return _fill_bonus(a, cfg, now, date)
    sym = a.symbol.strip().upper()
    if a.side == "buy" and not a.blocked_ok and sym in blocked_set(cfg.get("blocked")):
        raise TicketError(BLOCKED_BUY % (sym, sym))     # a long-term buy in the trading folder? (a sell stays free)
    swing = trade_style(cfg) == "swing"
    flags = any(v is not None for v in (a.stop, a.target, a.exit_by))
    if flags and not swing:
        raise TicketError('--stop, --target and --exit-by set a swing plan: they need "style": "swing" in '
                          'psx/config.json')
    if flags and a.side != "buy":
        raise TicketError("--stop, --target and --exit-by go with a buy that opens a position")
    plan, where = None, None
    if swing and a.side == "buy":
        pf = read_doc(a.portfolio, "portfolio", ("positions",))
        mine = [p for p in (pf or {}).get("positions", []) if str(p.get("ticker") or p.get("symbol") or "").upper() == sym]
        held = bool(mine)
        if held and flags and not any(_swing_plan(p) for p in mine):    # F02: stop --set cannot help it either
            raise TicketError(HELD_NO_PLAN % (sym, a.portfolio, sym))
        if held and flags:
            raise TicketError("%s is already in %s: --stop, --target and --exit-by set the plan of a new position "
                              "only. To move its stop: ticket.py stop --symbol %s --set X" % (sym, a.portfolio, sym))
        if not held:
            plan, where = fill_plan(a, cfg, sym, date, _fill_price(a.price), whole_shares(a.qty))
    state = load_state(a.state)            # read first: a bad log stops the fill before anything is written
    before = read_doc(a.portfolio, "portfolio", ("positions",))
    pf, fee = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date, plan=plan, real=a.real,
                         state=state, state_path=a.state)
    state.setdefault("orders", []).append(fill_record(now, date, a.symbol, a.side, a.qty, a.price, fee))
    write_json(a.state, state)
    print("Recorded %s %s %s at %s on %s in %s, costs about %s. Cash now %s." % (
        a.side, _shares(whole_shares(a.qty)), a.symbol.strip().upper(), _p(a.price), date, a.portfolio,
        money(fee), money(pf["cash"])))
    rest = ""                              # F60: a part fill of today's buy (swing, psx-invest; day style as in v1)
    if a.side == "buy" and date == to_pkt(now).date().isoformat() and (swing or _invest_buy(a.orders, sym)):
        rest = open_buy_rest(state, sym, date)
    if plan:
        print(plan_words(plan, where))
    if rest:
        print(rest)
    if plan:
        print(hold_words(plan, sym, a.portfolio))
    note = real_note(before, pf, a.portfolio)
    if note:
        print(note)
    return 0


def _flat_by(plan_path, sym, day, cfg):
    """HH:MM to be flat by on `day`: today's plan for this symbol (psx-exit already moved it to
    the day's hours), else config_time() of the config's flat_by (default 15:20)."""
    plan = read_json(plan_path, "plan", required=False) or {}
    if isinstance(plan, dict) and str(plan.get("symbol", "")).upper() == sym and plan.get("date") == day.isoformat():
        try:
            return parse_hhmm(plan.get("flat_by"), "flat_by").strftime("%H:%M")
        except ValueError:
            pass
    t = parse_hhmm(cfg.get("flat_by") or "15:20", '"flat_by" in psx/config.json')
    return config_time(t, day, cfg).strftime("%H:%M")


def _tick_price(text, flag):
    """A price above 0 on the Rs 0.01 tick, like 97.00."""
    p = _finite(text)
    if p is None or p <= 0 or Decimal(str(p)) % Decimal("0.01") != 0:
        raise TicketError("%s wants a price above 0 on the Rs 0.01 tick, like 97.00" % flag)
    return p


def cmd_flatten(a):
    cfg = read_doc(a.config, "config") or {}
    sym = a.symbol.strip().upper()
    now, today = _clock(a)
    day = _date(today, "--today")
    swing = trade_style(cfg) == "swing"
    limit = _tick_price(a.limit, "--limit") if a.limit is not None else None
    source = None
    if a.qty is not None:
        qty = whole_shares(a.qty, "--qty (the shares you hold)")
    else:
        pf = read_doc(a.portfolio, "portfolio", ("positions",)) or {}
        mine = [p for p in pf.get("positions", []) if str(p.get("ticker") or p.get("symbol") or "").upper() == sym]
        held = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in mine)
        if held <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check your broker app and pass --qty "
                              "if you do hold some" % (sym, a.portfolio))
        qty = whole_shares(held, "the %s shares in %s" % (sym, a.portfolio))
        source = a.portfolio
        swing = swing or any(isinstance(p.get("plan"), dict) and p["plan"].get("style") == "swing" for p in mine)
    flat_by = None if swing else _flat_by(a.plan, sym, day, cfg)
    order = {"symbol": sym, "side": "sell", "qty": qty, "type": "market"}
    if limit is not None:
        order.update(type="limit", limit_price=limit)
    order.update(time_in_force="day", board="REG")
    blocked = is_blocked(cfg, sym)         # maybe long-term shares: never call them a day trade
    if blocked:
        order["why"] = ("close %s, a share on your blocked list" % sym) if swing else (
            "flat by %s: close %s, a share on your blocked list" % (flat_by, sym))
    else:
        order["why"] = ("close the %s swing position" % sym) if swing else (
            "flat by %s: close the %s day trade" % (flat_by, sym))
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else len(old) if isinstance(old, list) else 0
    paper = _is_paper(cfg)
    doc = {"created_by": "psx-broker", "created_at": utc_iso(now), "status": "proposed", "paper": paper,
           "note": "Flatten ticket. Nothing is placed until psx-risk passes it and you say yes in the chat.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    if swing:
        print("Flatten %s: %s." % (sym, order_line(order)))
    else:
        print("Flatten %s: %s (flat by %s Karachi time)." % (sym, order_line(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if source:
        print("The qty comes from %s. Check it in your broker app before you say yes." % source)
    if blocked:
        print(BLOCKED_NOTE % sym)
    now_pkt = _now_pkt(now, today)
    note = market_note(order, now_pkt, cfg) if now_pkt else ""
    if note:
        print("Note: " + note)
    if swing and limit is None and now_pkt and "pre-open" in note:
        print("For a sell in the pre-open, make a limit sell: ticket.py flatten --symbol %s --limit P." % sym)
    protects = []
    if swing and not paper:                # a stop-limit of these shares may rest at the broker (D58)
        try:
            protects = open_protects(load_state(a.state), sym, today)
        except TicketError:
            protects = []                  # a bad log never stops an exit ticket
    print("Next:")
    step = 1
    if protects:
        print("  1. If you put a stop-loss order in your app, cancel it first, so it cannot sell these shares again.")
        for fp, got, want in protects:
            if got > 1e-9:                 # a part fill: the rest still rests at the broker
                print("     Part of it filled (%s of %s shares): cancel the rest in your app." % (_shares(got),
                                                                                            _shares(want)))
            print("     Then record it: ticket.py result --fingerprint %s --status cancelled" % fp)
        step = 2
    print("  %d. psx-risk: check.py. A sell of shares you hold is an exit: the entry window and the daily" % step)
    print("     limits never block it.")
    print("  %d. ticket.py show, then the user's yes for this one order." % (step + 1))
    if paper and limit is not None:
        print("  3. Paper: ticket.py result --index 1 --status placed records a fill at the limit %s. If PSX never"
              % _p(limit))
        print("     traded at your limit, record it expired (--status expired).")
        return 0
    if paper:
        print("  3. Paper: ticket.py result --index 1 --status placed --filled-price P (the price you see then).")
        return 0
    if not protects:
        print("  3. If you put a stop-loss order in your app, cancel it first, so it cannot sell these shares again.")
    if swing:                              # new text: no line starts with "Sell " (D60); day keeps the v1 line
        print("  4. Place the sell in your broker app, then ticket.py result. A stock stuck at its lower price limit")
        print("     may find no buyer: watch the fill in your app.")
        return 0
    print("  4. Sell in your broker app, then ticket.py result. A stock stuck at its lower price limit may find")
    print("     no buyer: watch the fill in your app.")
    return 0


def _position(pf, sym):
    """The position of sym in a portfolio, with shares above 0, or None."""
    for p in (pf or {}).get("positions", []):
        if str(p.get("ticker") or p.get("symbol") or "").upper() == sym and (_f(p.get("shares", p.get("qty"))) or 0) > 1e-9:
            return p
    return None


def _swing_plan(pos):
    """The swing plan block of a position (SPEC v2 2.5), or None."""
    plan = pos.get("plan") if isinstance(pos, dict) else None
    return plan if isinstance(plan, dict) and plan.get("style") == "swing" else None


def _replaced(old):
    """ " (it replaced 1 earlier proposal)" for the "Wrote it to" line, or ""."""
    n = len(old.get("orders", [])) if isinstance(old, dict) else len(old) if isinstance(old, list) else 0
    return (" (it replaced %d earlier proposal%s)" % (n, "" if n == 1 else "s")) if n else ""


def cmd_protect(a):
    """protect (D32): ONE stop-limit sell of a held swing position, for the user's broker app.
    The trigger is the ledger plan's stop; the limit is stop_limit_gap_pct under it, rounded
    down. It goes through psx-risk and a yes like every order. Paper mode writes no order."""
    cfg = read_doc(a.config, "config") or {}
    sym = a.symbol.strip().upper()
    now, today = _clock(a)
    pos = _position(read_doc(a.portfolio, "portfolio", ("positions",)), sym)
    if pos is None:
        raise TicketError("nothing to protect: no %s shares in %s" % (sym, a.portfolio))
    plan = _swing_plan(pos)
    stop = _finite(plan.get("stop")) if plan else None
    if plan is None:                       # F02: a share held from before the switch, or a day trade
        raise TicketError("no swing plan stop for %s in %s, so there is no stop to protect. A swing plan is written "
                          "only when a swing buy fills (ticket.py result, or ticket.py fill --stop --target --exit-by). "
                          % (sym, a.portfolio) + NO_PLAN_WATCH % sym)
    if stop is None or stop <= 0:
        raise TicketError("no swing plan stop for %s in %s, so there is no stop to protect. A swing fill records its "
                          "plan: ticket.py result, or ticket.py fill --stop --target --exit-by" % (sym, a.portfolio))
    held = whole_shares(_f(pos.get("shares", pos.get("qty"))), "the %s shares in %s" % (sym, a.portfolio))
    qty = whole_shares(a.qty, "--qty") if a.qty is not None else held
    if qty > held:
        raise TicketError("--qty %s is more than the %s %s shares in %s" % (_shares(qty), _shares(held), sym,
                                                                          a.portfolio))
    gap = _finite(swing_settings(cfg).get("stop_limit_gap_pct"))
    if gap is None or not 0 <= gap < 100:
        raise TicketError('"stop_limit_gap_pct" in the "swing" block of psx/config.json wants a percent from 0 to 99, '
                          'like 1.0')
    trigger = round_down_tick(stop)
    limit = round_down_tick(trigger * (1 - gap / 100.0))
    if _is_paper(cfg):
        print(PAPER_STOP)                  # a paper stop rests nowhere: psx-alerts watches it
        return 0
    order = {"symbol": sym, "side": "sell", "qty": qty, "type": "stop_limit", "trigger_price": trigger,
             "limit_price": limit, "time_in_force": "day", "board": "REG",
             "why": "protect %s: the plan stop %s as a stop-limit sell in your broker app" % (sym, _p(trigger))}
    old = read_json(a.orders, "orders", required=False)
    write_json(a.orders, {"created_by": "psx-broker", "created_at": utc_iso(now), "status": "proposed", "paper": False,
                          "note": PROTECT_NOTE, "orders": [order]})
    print("Protect %s: %s." % (sym, order_line(order)))
    print("The trigger is the plan stop in %s. The limit is %s%% under it (swing.stop_limit_gap_pct in "
          "psx/config.json)." % (a.portfolio, _n(gap)))
    print("Wrote it to %s%s." % (a.orders, _replaced(old)))
    if a.qty is None:
        print("The qty comes from %s. Check it in your broker app before you say yes." % a.portfolio)
    if is_blocked(cfg, sym):
        print(BLOCKED_NOTE % sym)
    now_pkt = _now_pkt(now, today)
    note = market_note(order, now_pkt, cfg) if now_pkt else ""
    if note:
        print("Note: " + note)
    try:
        protects = open_protects(load_state(a.state), sym, today)
    except TicketError:
        protects = []                      # a bad log never stops a protect ticket
    print("Next:")
    step = 1
    if protects:                           # after a halt the old one may be gone: it must be recorded first
        print("  1. A stop-limit sell of these shares is open today in %s. If it still rests in your app, you need"
              % a.state)
        print("     no new one. If PSX or you cancelled it (a market halt cancels every open order), record that first:")
        for fp, got, want in protects:
            print("     ticket.py result --fingerprint %s --status cancelled" % fp)
        step = 2
    print("  %d. psx-risk: check.py. A sell of shares you hold is an exit: the entry window and the daily" % step)
    print("     limits never block it.")
    print("  %d. ticket.py show, then the user's yes for this one order: \"yes %s\"." % (step + 1, _p(trigger)))
    print("  %d. ticket.py approve prints the line to type into your broker app. Then record it: ticket.py result"
          % (step + 2))
    print("     --index 1 --status placed --broker-id ID. When it fills: ticket.py fill.")
    return 0


def _pp(v):
    """A price for people, or "none"."""
    return _p(v) if v is not None else "none"


def ex_dates(sym, since, today, data, alerts_path, cfg=None):
    """The bonus, rights and split ex-dates of sym from `since` (the buy day) to today, oldest first:
    [{"date", "kind", "factor"}]. From the closing files in <data>/mkt_summary (read only, never
    fetched): a new XB or XR flag, or with no flag an LDCP change of 10% or more next to the session
    before, as psx-alerts sees them. Also from psx-alerts' own record ("swing_ex" in its state file),
    which has an ex-date it saw on the company page in the morning, before that day's closing file.
    An XD (a cash payout) is not one: the stop stays (D27)."""
    found, sessions, d = {}, [], since - datetime.timedelta(days=15)
    while d <= today:
        path = daily_path(data, d)
        if os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    rows = read_mkt_summary(fh.read())
            except (OSError, ValueError):
                rows = None                # a broken file shows no ex-date
            if rows and rows[0]["date"] == d:
                sessions.append((d, rows))
        d += datetime.timedelta(days=1)
    bars = daily_bars(sym, sessions)
    spans, _ = window_sessions([x for x, _ in sessions], cfg)
    order = dict((x, i) for i, x in enumerate(spans))
    for prev, cur in zip(bars, bars[1:]):
        if not since <= cur["date"] <= today:
            continue
        ldcp, close = _finite(cur.get("ldcp")), _finite(prev.get("close"))
        if not ldcp or not close or ldcp <= 0 or close <= 0:
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
    doc = _quiet_json(alerts_path)
    seen = (doc.get("swing_ex") or {}) if isinstance(doc, dict) else {}
    events = seen.get("%s-%s" % (sym, since.isoformat())) if isinstance(seen, dict) else None
    for key, ev in sorted(events.items()) if isinstance(events, dict) else []:
        factor = _finite(ev.get("factor")) if isinstance(ev, dict) else None
        if (factor is None or factor <= 0 or str(ev.get("kind") or "XD") == "XD" or key in found
                or not since.isoformat() <= str(key) <= today.isoformat()):
            continue
        found[key] = {"date": key, "kind": str(ev["kind"]), "factor": round(factor, 4)}
    return [found[k] for k in sorted(found)]


def scale_check(sym, plan, scale, today, a, cfg):
    """--scale F under 1 lowers the stop (D26: up only, or scaled by an ex-date factor). TicketError,
    and nothing written, unless sym has a bonus, rights or split ex-date from the buy day to today
    that has no scale yet, and F is not under its factor. A fall in the price is not an ex-date."""
    try:
        since = datetime.date.fromisoformat(str(plan.get("date")))
    except ValueError:
        raise TicketError('the swing plan of %s in %s has no buy day ("date"), so its ex-dates cannot be found'
                          % (sym, a.portfolio))
    day = datetime.date.fromisoformat(today)
    events = ex_dates(sym, since, day, a.data, a.alerts, cfg)
    done = [str(x.get("date")) for x in plan.get("scaled") or [] if isinstance(x, dict)
            and _finite(x.get("factor")) is not None and abs(_finite(x["factor"]) - 1) > 1e-9]
    left = [e for e in events if not any(d >= e["date"] for d in done)]
    if not left and events:
        e = events[-1]
        raise TicketError("the %s ex-date of %s on %s has a scale already. A second scale would lower the stop again, "
                          "so nothing was changed. To move the stop up: --set." % (e["kind"], sym, _day_words(e["date"])))
    if not left:
        raise TicketError("--scale %s lowers the stop, and that needs a bonus, rights or split ex-date of %s. None is in "
                          "the closing files in %s or in %s from %s (the buy day) to %s, so nothing was changed. A fall "
                          "in the price is not an ex-date: the stop moves up only (--set)."
                          % (_n(scale), sym, a.data, a.alerts, day_label(since), day_label(day)))
    floor = 1.0
    for e in left:
        floor *= e["factor"]
    if scale < floor - FACTOR_SLACK:
        raise TicketError("--scale %s is under %.4f, the factor of the %s ex-date%s of %s (%s). It would lower the stop "
                          "more than the ex-date did, so nothing was changed. Use --scale %.4f, or --scale 1 to keep "
                          "the stop." % (_n(scale), floor, "/".join(e["kind"] for e in left), "s" if len(left) > 1 else "",
                                         sym, " and ".join(_day_words(e["date"]) for e in left), floor))


def cmd_stop(a):
    """stop (D26, D56): the one command that changes a held swing position's stop. --set X moves
    it up only; --scale F (over 0, at most 1) scales the stop (down) and the target (half up)
    for a bonus, rights or split ex-date (scale_check: one must be known, and F is not under its
    factor), and F = 1 records "keep". It writes only the ledger plan in psx/portfolio.json and
    logs a "stop" record. "stop_at_buy" never changes."""
    cfg = read_doc(a.config, "config") or {}
    sym = a.symbol.strip().upper()
    now, today = _clock(a)
    state = load_state(a.state)
    pf = read_doc(a.portfolio, "portfolio", ("positions",))
    plan = _swing_plan(_position(pf, sym))
    if plan is None:
        held = (". " + NO_PLAN_WATCH % sym) if _position(pf, sym) else ""     # F02: held, with no swing plan
        raise TicketError("no swing plan for %s in %s: ticket.py stop changes the stop of a held swing position%s"
                          % (sym, a.portfolio, held))
    old_stop, old_target = _finite(plan.get("stop")), _finite(plan.get("target"))
    why = (a.why or "").strip() or None
    if a.set is not None:
        new = _finite(a.set)
        if new is None or new <= 0:
            raise TicketError("--set wants a price above 0, like 97.00")
        new = round_down_tick(new)
        if old_stop is not None and new < old_stop:
            raise TicketError("move a stop up only: %s is under the stop %s. To match a bonus or split, use --scale."
                              % (_p(new), _p(old_stop)))
        if old_stop is not None and new == old_stop:
            raise TicketError("move a stop up only: %s is the stop now. To match a bonus or split, use --scale."
                              % _p(new))
        plan["stop"], scale, changed = new, None, True
        head = "%s stop %s to %s (the target stays %s)." % (sym, _pp(old_stop), _p(new), _pp(old_target))
        what = "%s stop %s to %s" % (sym, _pp(old_stop), _p(new))
    else:
        scale = _finite(a.scale)
        if scale is None or not 0 < scale <= 1:
            raise TicketError("--scale wants a factor over 0 and at most 1, like 0.8333 (1 keeps the stop and the "
                              "target)")
        changed = scale != 1
        if changed:
            scale_check(sym, plan, scale, today, a, cfg)      # only for a bonus, rights or split ex-date
            plan["stop"] = round_down_tick(old_stop * scale) if old_stop is not None else None
            plan["target"] = round_half_up_tick(old_target * scale) if old_target is not None else None
        else:
            why = "keep" + (": " + why if why else "")
        if not isinstance(plan.get("scaled"), list):
            plan["scaled"] = []
        plan["scaled"].append({"date": today, "factor": scale, "why": why})
        tag = "(scale %s%s on %s)" % (_n(scale), (": " + why) if why else "", _day_words(today))
        if changed:
            head = "%s stop %s to %s, target %s to %s %s." % (sym, _pp(old_stop), _pp(plan["stop"]), _pp(old_target),
                                                              _pp(plan["target"]), tag)
            what = "%s stop %s to %s, target %s to %s (scale %s)" % (sym, _pp(old_stop), _pp(plan["stop"]),
                                                                    _pp(old_target), _pp(plan["target"]), _n(scale))
        else:
            head = "%s stop %s and target %s stay as they are %s." % (sym, _pp(old_stop), _pp(old_target), tag)
            what = "%s stop %s and target %s kept (scale 1)" % (sym, _pp(old_stop), _pp(old_target))
    write_json(a.portfolio, pf)
    state.setdefault("orders", []).append({"at": pkt_iso(now), "date": today, "status": "stop", "symbol": sym,
                                           "from": old_stop, "to": _finite(plan.get("stop")), "scale": scale,
                                           "why": why, "plain": what})
    write_json(a.state, state)
    if _is_paper(cfg):
        tail = " Paper: no stop order rests anywhere, so psx-alerts watches the new stop." if changed else ""
    elif changed:
        tail = (" Your stop-limit in the broker app is not changed. Make a new one: ticket.py protect --symbol %s."
                % sym)
    else:
        tail = " Your stop-limit in the broker app is not changed."
    print(head + tail)
    return 0


def cmd_cash(a):
    """cash --set N (D57): sets "cash" in psx/portfolio.json, prints the old and the new number,
    and logs a "cash" record in psx/state/broker.json. Nothing else changes."""
    now, today = _clock(a)
    amount = _finite(str(a.set).replace(",", "").strip())
    if amount is None or amount < 0:
        raise TicketError("--set wants rupees, 0 or more, like 46000")
    amount = _cents(amount)
    state = load_state(a.state)
    pf = read_doc(a.portfolio, "portfolio", ("positions",))
    old = _finite(pf.get("cash")) if pf is not None else None
    if pf is None:
        pf = {"cash": amount, "positions": []}
        words = "Cash in %s set to %s (a new file, with no positions)." % (a.portfolio, money(amount))
    else:
        pf["cash"] = amount
        words = "Cash in %s set from %s to %s. Nothing else changed." % (
            a.portfolio, money(old) if old is not None else "none", money(amount))
    write_json(a.portfolio, pf)
    state.setdefault("orders", []).append({"at": pkt_iso(now), "date": today, "status": "cash", "from": old,
                                           "to": amount, "plain": "cash %s to %s" % (
                                               money(old) if old is not None else "none", money(amount))})
    write_json(a.state, state)
    print(words)
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (", id %s" % r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (', reply "%s"' % r["reply"]) if r.get("reply") and r.get("status") in ("approved", "declined") else ""
        loss = (", max loss %s (costs included)" % money(r["max_loss"])) \
            if r.get("max_loss") is not None and r.get("status") == "placed" else ""
        cost = (", costs %s" % money(r["costs"])) if r.get("costs") is not None and r.get("status") == "filled" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        what = str(r.get("plain") or "").split(" / ")[0]      # the order line, without the plan parts
        print("%s  %s %-9s %s%s%s%s%s" % (r.get("at"), idx, r.get("status"), what, reply, loss, cost, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (psx-broker). Never places orders: "
                                             "you place them yourself in your broker app.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this Karachi time, e.g. \"2026-10-29 10:15\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in Karachi)")

    def common(p):
        p.add_argument("--orders", default="psx/orders.json")
        p.add_argument("--config", default="psx/config.json")
        p.add_argument("--risk", default="psx/state/risk.json")
        p.add_argument("--state", default="psx/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="show the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p.add_argument("--universe", default="psx/universe.txt", help="known symbols: a yes that names one is refused")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p_result = p = sub.add_parser("result", help="record what happened after the yes (paper: also the paper fill)")
    common(p)
    p.add_argument("--index", type=int, help="the order in psx/orders.json (its yes is from today)")
    p.add_argument("--fingerprint", help="an order of any day, by its fingerprint in psx/state/broker.json")
    p.add_argument("--symbol", help="with --date: the one order of this stock with a yes on that day")
    p.add_argument("--date", help="YYYY-MM-DD, with --symbol, or with --fingerprint (the order of that day)")
    p.add_argument("--status", required=True, choices=["placed", "skipped", "failed", "expired", "cancelled"],
                   help="placed in your app (or on paper), skipped (you did not place it), failed (the app refused it), "
                        "expired (it ended at the close with no fill), cancelled (you cancelled it in your app)")
    p.add_argument("--broker-id", help="the order number your broker app shows")
    p.add_argument("--note")
    p.add_argument("--filled-qty", help="shares filled (default: the order's qty)")
    p.add_argument("--filled-price", help="the fill price (paper default: the limit price)")
    p.add_argument("--portfolio", default="psx/portfolio.json")
    p_fill = p = sub.add_parser("fill", help="record a fill in psx/portfolio.json (or --bonus N bonus shares)")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", choices=["buy", "sell"], type=str.lower)
    p.add_argument("--qty", help="whole shares")
    p.add_argument("--price")
    p.add_argument("--date", help="YYYY-MM-DD (default: today in Karachi)")
    p.add_argument("--now", help="pretend it is this Karachi time, e.g. \"2026-10-29 10:15\"")
    p.add_argument("--portfolio", default="psx/portfolio.json")
    p.add_argument("--config", default="psx/config.json")
    p.add_argument("--state", default="psx/state/broker.json")
    p.add_argument("--orders", default="psx/orders.json", help="swing: the plan of today's buy comes from here")
    p.add_argument("--plan", default="psx/plan.json", help="swing: else from today's plan here")
    p.add_argument("--stop", help="swing: the plan stop of a new position, by hand (with --target and --exit-by)")
    p.add_argument("--target", help="swing: the plan target, by hand")
    p.add_argument("--exit-by", help="swing: the exit-by day YYYY-MM-DD, by hand")
    p.add_argument("--bonus", help="record N bonus shares at cost 0 (no --side, --qty or --price)")
    p.add_argument("--real", action="store_true",
                   help="this is a real fill from my broker app (for a file with \"real_holdings\": true)")
    p.add_argument("--blocked-ok", action="store_true",
                   help="a buy of a share on the \"blocked\" list belongs in this folder after all")
    p = sub.add_parser("flatten", help="a market sell of the shares you hold, by flat_by (or --limit P)")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from your broker app (default: psx/portfolio.json)")
    p.add_argument("--limit", help="a limit sell at this price (PSX takes no market order in the pre-open)")
    p.add_argument("--orders", default="psx/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="psx/portfolio.json")
    p.add_argument("--plan", default="psx/plan.json")
    p.add_argument("--config", default="psx/config.json")
    p.add_argument("--state", default="psx/state/broker.json", help="swing: an open stop-limit sell of these shares")
    clock(p)
    p = sub.add_parser("protect", help="swing: a stop-limit sell of a held position, at its plan stop")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares to protect (default: all the shares in psx/portfolio.json)")
    p.add_argument("--orders", default="psx/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="psx/portfolio.json")
    p.add_argument("--config", default="psx/config.json")
    p.add_argument("--state", default="psx/state/broker.json", help="a stop-limit sell of these shares open today")
    clock(p)
    p = sub.add_parser("stop", help="swing: move a held position's plan stop up, or scale it for a bonus or split")
    p.add_argument("--symbol", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--set", help="a new stop, over the stop now (rounded down to the tick)")
    g.add_argument("--scale", help="a factor over 0 and at most 1: the stop and the target times it (1 keeps them)")
    p.add_argument("--why", help="why, like \"XB 20%%\"")
    p.add_argument("--portfolio", default="psx/portfolio.json")
    p.add_argument("--config", default="psx/config.json")
    p.add_argument("--state", default="psx/state/broker.json")
    p.add_argument("--data", default="psx/data", help="--scale: the closing files of the ex-date (read only)")
    p.add_argument("--alerts", default="psx/state/alerts.json",
                   help="--scale: psx-alerts' record of the ex-dates it saw (read only)")
    clock(p)
    p = sub.add_parser("cash", help="set the cash in psx/portfolio.json (logged)")
    p.add_argument("--set", required=True, help="rupees, like 46000")
    p.add_argument("--portfolio", default="psx/portfolio.json")
    p.add_argument("--state", default="psx/state/broker.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="psx/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    if a.cmd == "result" and a.index is None and not (a.fingerprint or a.symbol or a.date):
        p_result.error("the following arguments are required: --index (or --fingerprint FP, or --symbol X --date D)")
    if a.cmd == "fill" and a.bonus is None:
        missing = [flag for flag, v in (("--side", a.side), ("--qty", a.qty), ("--price", a.price)) if v is None]
        if missing:
            p_fill.error("the following arguments are required: " + ", ".join(missing))
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "flatten": cmd_flatten, "protect": cmd_protect, "stop": cmd_stop, "cash": cmd_cash,
                "log": cmd_log}[a.cmd](a)
    except (TicketError, ValueError) as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
