#!/usr/bin/env python3
"""psx-autorun: the PSX pack's tools on a timer, on a Mac. Standard library only, Python 3.8.

launchd starts this runner every 5 minutes, through the small "PSX Autorun" app. Each run asks
the pack's own PSX clock (psx-alerts/alerts.py clock) what kind of day it is, works out which
job is due, runs the pack's tools for that job, writes a short report in plain English, and
prints the lines that the app shows as Mac messages.

Jobs (Karachi time; every time comes from the PSX clock, so Fridays, Ramadan and holidays are
handled by the pack, not here):
  morning      75 minutes before the first session (8:15 AM Mon to Thu, 8:00 AM Fri)
  check        every 5 minutes while PSX is open, and one last check after each part of the day
  after_close  40 minutes after the close, then every 15 minutes until the closing file is out
  weekly       Saturday 10:00 AM
  probe        an expected moon holiday: one look at 10:00 AM, to see if PSX opened after all
  tape         off by default: the scanner's tape recorder (day style only)

It never places an order, never sizes one, never writes psx/orders.json, and never runs
psx-broker, psx-budget or psx-risk. A setup, an alert or a signal is a rule that matched.
It is not advice."""

import argparse
import datetime
import errno
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "2.0"
KARACHI = datetime.timezone(datetime.timedelta(hours=5), "PKT")   # UTC+5 all year, no daylight saving
HOME = os.path.expanduser("~")
LABEL_TICK = "local.psx-autorun.tick"
LABEL_TAPE = "local.psx-autorun.tape"
NEVER_RUN = ("psx-broker", "psx-budget", "psx-risk")        # orders need the user's yes in the chat
NEVER_SCRIPTS = ("ticket.py", "size.py", "check.py")        # their scripts, in whatever folder
NEVER_COMMANDS = {"invest.py": ("orders", "cash", "ack", "backfill"),   # orders, money, your choices, a bulk fetch
                  "alerts.py": ("add", "reset")}                       # your own alert rules
POLLERS = ("psx-scanner/scan.py tape", "psx-entry/entry.py watch")  # tools that poll the PSX site by themselves
PERM_RE = re.compile(r"Operation not permitted|\[Errno 1\]|PermissionError")
TOOL_ERR_RE = re.compile(r"^(scan\.py|alerts\.py|entry\.py|invest\.py|psx-invest|news|insiders|facts): (.+)$",
                         re.M)
ITEM_RE = re.compile(r"^[! ] (\d{4}-\d{2}-\d{2}|date unknown|time unknown)\b", re.M)   # one psx-news item
HALT_RE = re.compile(r"^\s+tags: [^\n]*\bhalt\b", re.M)                                # psx-news tags a halt
SIGNAL_RE = re.compile(r"^(ADD|HOLD|TRIM|REVIEW)\s+([A-Z0-9-]+)\s", re.M)              # psx-invest signal rows
# a HOLD row of a candidate you do not hold ("HOLD    HBL    0.0%  not held, a candidate; ..."): it is no "keep"
CANDIDATE_RE = re.compile(r"^HOLD\s+[A-Z0-9-]+\s+-?\d+(?:\.\d+)?%\s+not held, a candidate; ", re.M)
STALE_RE = re.compile(r"^error: ([A-Z0-9-]+): no price newer than (\d+) minutes", re.M)  # psx-alerts (D54)
OLD_RE = re.compile(r"^note: ([A-Z0-9-]+): the data is (\d+) minutes old", re.M)            # psx-alerts notes
NOT_TODAY_RE = re.compile(r"^note: ([A-Z0-9-]+): the newest data is from .+?, not today, so no alert used it",
                          re.M)
# psx-alerts' line when nothing new rang: "Nothing hit. 3 rule(s) watched today, DEMO 96.00 (as of 3:30 PM PKT)."
# While a swing rule of a share you still hold is met, it starts "No new alert." and one line for each met
# rule follows it, before the notes (D53, D54). Any first sentence is read, so a new word there keeps the count.
WATCHED_RE = re.compile(r"^([A-Z][^.\n]*)\. (\d+) rule\(s\) watched today(.*)$", re.M)
MET_HEAD = "No new alert"
SEEN_RE = re.compile(r"\b([A-Z0-9-]+) (\d+\.\d\d) \(as of ([^)]*)\)")     # "DEMO 380.00 (as of 3:30 PM PKT)"
FEED_RE = re.compile(r"^note: feed ([^:\s]+): ", re.M)                     # psx-news: a feed that did not answer
NEWS_FEEDS_DEFAULT = 5          # psx-news reads its 5 feeds unless "news_feeds" in psx/config.json names others
ASK_RE = re.compile(r"Ask the user to open (\S+) in their browser and save it as (\S+), or paste a screenshot\. "
                    r"Do not fetch it yourself\.")
ASK_WORDS = (r"To read it, open \1 in your browser and save it as \2, or paste a screenshot in the chat. "
             r"Claude does not fetch it.")
LEVEL_RULES = {"stop": "stop", "target": "target", "below": "alert level", "above": "alert level"}
SITE_GAP = 2.0          # seconds between two tools that use the PSX site (each tool keeps 1.5 s inside itself)
PROBE_TIME = datetime.time(10, 0)   # an expected moon holiday: the one look, after every open plus 15 minutes
# The look reads one company page: its "As of" time is the page's own. A screener price gets its time from
# the pack's calendar, so on a day the calendar has closed it can never be "today".
PROBE_SOURCE = "psx-company"
EXPECTED_MARK = "(expected"         # the clock's "reason" on an expected moon holiday
MESSAGE_MAX = 400                   # the longest message text before the footer
BREAK = "\\n"                       # a line break inside one message line: the app shows a new line there
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
FOLDER_WORDS = {"trading": "trading", "longterm": "long-term"}
FOOTER = ("A setup, an alert or a signal is a rule that matched. It is not advice. "
          "Nothing was bought or sold. Auto-run never places an order.")
BLOCKED = ("macOS blocks PSX Autorun from your Documents folder. Open System Settings, Privacy & Security, "
           "Files and Folders, and turn on Documents Folder for PSX Autorun.")
CRASH = ("Auto-run stopped with an error. The details are in Library/Logs/psx-autorun/autorun.log.")
MORE = "Open the report for the rest."
PAPER_LINE = "Paper trading is %s in the trading folder."
STOP_LIMIT_LIVE = ("Put in your stop-limit after the opening match. psx-alerts reminds you at the first check. "
                   "No order guards the opening price: a gap at the open can go past your stop.")
STOP_LIMIT_PAPER = ("Paper: no stop-limit rests at a broker. Auto-run checks the stop every 5 minutes in the "
                    "session.")
HALT_WORDS = ("A market halt is in the news. A halt cancels all open orders at PSX. Check your orders in your "
              "broker app.")
OPEN_BUY = ("%s: a buy %s on %s has no fill recorded. Record the fill (ticket.py fill), or record it expired "
            "(ticket.py result --fingerprint %s --status expired).")
SWING_WAIT = " New swing buys wait until then."
PART_BUY = ("%s: a buy %s on %s is filled in part: %s of %s shares are recorded. If more of it filled, record "
            "them (ticket.py fill). If not, the other %s ended at the close with no fill. To record that: "
            "ticket.py result --fingerprint %s --status expired.")
HOLIDAY_AHEAD = ("%s is an expected PSX holiday (%s). Moon dates can move. If PSX says it is open, add the day "
                 "under \"open_days\" in the trading folder's psx/config.json. Auto-run then runs as normal.")
LOOKS_OPEN = ("PSX looks open today, %s, but the calendar has it as a holiday. Add the day under \"open_days\" "
              "in psx/config.json. Until then auto-run checks nothing today.")
NOT_FOUND = ("The closing file of %s was not found. Was PSX closed that day? If so, add the day under "
             "\"holidays\" in psx/config.json.")
NOT_YET = ("The closing file of %s is not out yet. This run used the closing files that are there. The "
           "after-close check still runs by itself when the file is out.")
STALE_PROBLEM = ("the %s not checked for %d minutes: no price newer than %s minutes. Check the price in your "
                 "broker app.")      # "DEMO stop was" or "DEMO and HUBC stops were"
OLD_PROBLEM = ("the %s had no new price for %d minutes: %s. Check the price in your broker app.")
BUSY_WORDS = "Another PSX tool was running (a tape or an entry watch), so this check read saved prices only."
FILE_BAD = "%s in the %s folder %s. Auto-run did not change it. Fix the file."
USER_FILES = (("config.json",), ("portfolio.json",), ("state", "broker.json"))     # read only, never moved
CASH_ASK = "Does this match your broker app? If not: %s."
CASH_FIX = {"trading": "ticket.py cash --set N (trading)", "longterm": "invest.py cash --set N (long-term)"}


# ---------------------------------------------------------------- copied verbatim from tests/psx_core.py
# (never edit a copy: tests/test_repo.py checks each one)

SLEEP = time.sleep
COSTS = {"commission_pct": 0.15, "commission_min_per_share": 0.03, "sales_tax_pct": 15,
         "other_charges_pct": 0.0066, "day_trade_one_side": False}


def clock_label(t):
    """datetime or time -> "10:15 AM"."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    """date -> "Thu 2026-10-29" (English day names, whatever the machine's language)."""
    if isinstance(d, datetime.datetime):
        d = d.date()
    return "%s %s" % (("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()], d.isoformat())


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


def trade_costs(cfg):
    """cost_settings(cfg). In swing style "day_trade_one_side" is False: a sell on a later
    day always pays commission, and a same-day stop-out is counted the safe way."""
    c = cost_settings(cfg)
    if trade_style(cfg) == "swing":
        c["day_trade_one_side"] = False
    return c


def site_on(cfg):
    """True only when psx/config.json says "psx_site": true (the JSON true, nothing else).
    Turn it on only if PSX has given you permission (marketdatarequest@psx.com.pk)."""
    return isinstance(cfg, dict) and cfg.get("psx_site") is True


class AutorunError(Exception):
    """A problem to tell the user in plain words."""


# ---------------------------------------------------------------- places

def home_dir():
    """Where auto-run keeps its own files (outside Documents, so macOS never blocks them)."""
    return os.environ.get("PSX_AUTORUN_HOME") or os.path.join(HOME, "Library", "Application Support",
                                                              "psx-autorun")


def log_dir():
    """Where auto-run keeps its logs."""
    return os.environ.get("PSX_AUTORUN_LOGS") or os.path.join(HOME, "Library", "Logs", "psx-autorun")


def state_dir():
    return os.path.join(home_dir(), "state")


def state_path():
    return os.path.join(state_dir(), "state.json")


def probe_request_path():
    """install.sh writes this file (step 7b). The next tick answers it and does nothing else."""
    return os.path.join(state_dir(), "probe.request")


# ---------------------------------------------------------------- the jobs

def step(sid, folder, script, args=(), site="yes", ok=(0, 1), kind="text", optional=False, quiet=(),
         busy_args=("--offline",), needs_file=None, timeout=None, title=None):
    """One tool run inside a job. site: "yes" (the tool may read the PSX site itself), "tape" (with
    --offline when the trading folder's alerts step just put a new line on the shared tape, else the
    site itself), "no" (never). quiet: words that mean "nothing to check", not a problem."""
    return {"id": sid, "folder": folder, "script": script, "args": list(args), "site": site, "ok": list(ok),
            "kind": kind, "optional": optional, "quiet": list(quiet), "busy_args": list(busy_args),
            "needs_file": needs_file, "timeout": timeout, "title": title or sid}


NEWS_BUSY = ("--no-notices", "--no-moves")      # another poller: news feeds only, no PSX page
SIGNALS = "psx-invest/invest.py"
SIGNALS_ARGS = ["signals", "--write", "--offline"]   # prints "notify:" lines only when a signal changed
ALERTS_ARGS = ["check", "--source", "{price_source}"]


def alerts_step(sid, folder, title, site="yes"):
    return step(sid, folder, "psx-alerts/alerts.py", ALERTS_ARGS, site=site, ok=(0, 1, 2), kind="alerts",
                quiet=["alerts.py: no rules"], timeout=120, title=title)


def news_step(sid, folder, args, title):
    return step(sid, folder, "psx-news/news.py", args, kind="news", busy_args=NEWS_BUSY,
                quiet=["news: no tickers"], title=title)


def insiders_step(sid, folder, title):
    return step(sid, folder, "psx-insiders/insiders.py", ["new", "--read"], kind="insiders",
                quiet=["No symbols to check"], title=title)


def signals_step(title):
    return step("signals", "longterm", SIGNALS, SIGNALS_ARGS, site="no", kind="invest", optional=True,
                needs_file=os.path.join("psx", "invest.json"), title=title)


# SPEC v2 section 4.3. The trading folder goes first in every round that reads prices (D44).
DEFAULT_JOBS = {
    "morning": [
        step("scan", "trading", "psx-scanner/scan.py", ["daily", "--quiet"], kind="scan",
             title="Daily scan (trading folder)"),
        alerts_step("alerts-trading", "trading", "Alerts (trading folder)"),
        news_step("news-trading", "trading", ["--market"], "News and market news (trading folder)"),
        insiders_step("insiders-trading", "trading", "Insider notices (trading folder)"),
        signals_step("Long-term signals"),
        news_step("news-longterm", "longterm", [], "News (long-term folder)"),
        insiders_step("insiders-longterm", "longterm", "Insider notices (long-term folder)"),
    ],
    "check": [
        alerts_step("alerts-trading", "trading", "Alerts (trading folder)"),
        alerts_step("alerts-longterm", "longterm", "Alerts (long-term folder)", site="tape"),
    ],
    "after_close": [
        step("scan", "trading", "psx-scanner/scan.py", ["daily", "--quiet"], kind="scan",
             title="Daily scan for the next session (trading folder)"),
        alerts_step("alerts-trading", "trading", "Alerts on the close (trading folder)"),
        alerts_step("alerts-longterm", "longterm", "Alerts on the close (long-term folder)", site="tape"),
        signals_step("Long-term signals on the close"),
        news_step("news-longterm", "longterm", [], "News (long-term folder)"),
        news_step("news-trading", "trading", ["--market"], "News and market news (trading folder)"),
    ],
    "weekly": [
        step("universe", "trading", "psx-scanner/scan.py", ["universe"], title="Share list (trading folder)"),
        step("results", "longterm", "psx-analyst/facts.py", ["@holdings", "--metrics", "psx/metrics.json"],
             ok=(0,), kind="results", title="Results of your long-term shares"),
    ],
}

DEFAULTS = {
    "python": "",
    "pack": "",
    "folders": {"trading": "", "longterm": ""},
    "reports": "",
    "calendar_folder": "trading",     # whose psx/config.json the clock reads (holidays, open_days, ramadan)
    "morning_lead_min": 75,
    "check_every_min": 5,
    "check_start_after_min": 7,       # trading starts 2 minutes in, and free prices are 5 minutes late
    "final_check_after_min": 10,
    "after_close_delay_min": 40,      # the closing file shows about 30 to 40 minutes after the close
    "after_close_retry_min": 15,
    "after_close_give_up_min": 240,
    "weekly": {"weekday": 5, "time": "10:00"},     # 5 = Saturday
    "price_source": "psx-screener",   # one page for every price; "psx-company" reads one page a share
    "tape": False,                    # true: the scanner's tape recorder (a page every 2 minutes, day style)
    "keep_awake": True,               # no idle sleep from the morning job to the after-close job
    "notify": {"morning": True, "alerts": True, "after_close": True, "weekly": True, "problems": True,
               "sound": "Glass"},
    "tape_max_restarts": 3,
    "problem_after_fails": 3,         # price checks: tell after 3 failures in a row (15 minutes)
    "problem_repeat_min": 60,
    "step_timeout_s": 600,
    "show_lines": 25,
    "jobs": DEFAULT_JOBS,
}
MINUTE_KEYS = ("morning_lead_min", "check_every_min", "check_start_after_min", "final_check_after_min",
               "after_close_delay_min", "after_close_retry_min", "after_close_give_up_min", "problem_after_fails",
               "problem_repeat_min", "step_timeout_s", "show_lines", "tape_max_restarts")


def deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "jobs":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def config_path():
    return os.path.join(home_dir(), "autorun.json")


def whole(v):
    return isinstance(v, int) and not isinstance(v, bool)


def check_values(cfg):
    """Plain errors for settings that cannot work."""
    for k in MINUTE_KEYS:
        if not whole(cfg.get(k)) or cfg[k] < (1 if k in ("check_every_min", "step_timeout_s") else 0):
            raise AutorunError('"%s" in autorun.json wants a whole number, like %s' % (k, DEFAULTS[k]))
    wk = cfg.get("weekly") if isinstance(cfg.get("weekly"), dict) else {}
    if not whole(wk.get("weekday")) or not 0 <= wk["weekday"] <= 6:
        raise AutorunError('"weekly": {"weekday": ...} wants 0 (Monday) to 6 (Sunday); 5 is Saturday')
    try:
        hm(str(wk.get("time")))
    except ValueError:
        raise AutorunError('"weekly": {"time": ...} wants "HH:MM", like "10:00"')
    if cfg.get("price_source") not in ("psx-screener", "psx-company"):
        raise AutorunError('"price_source" in autorun.json is "psx-screener" or "psx-company"')
    if cfg.get("calendar_folder") not in ("trading", "longterm"):
        raise AutorunError('"calendar_folder" in autorun.json is "trading" or "longterm"')
    if not isinstance(cfg.get("notify"), dict) or not isinstance(cfg.get("folders"), dict):
        raise AutorunError('"notify" and "folders" in autorun.json are blocks: {...}')


def load_config(path=None):
    """autorun.json on top of DEFAULTS, with the paths filled in. Every step of every job passes
    check_safe() here, whatever the file says."""
    path = path or config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            user = json.load(fh)
    except FileNotFoundError:
        raise AutorunError("no settings file at %s: run install.sh first" % path)
    except ValueError as e:
        raise AutorunError("%s is not valid JSON (%s)" % (path, e))
    if not isinstance(user, dict):
        raise AutorunError("%s wants a JSON object: {...}" % path)
    cfg = deep_merge(DEFAULTS, user)
    check_values(cfg)
    # Every tool runs with the Python that runs auto-run (run.sh starts it from python.path). The
    # "python" key of the settings file has no say: a settings file must never pick the program.
    cfg["python"] = own_python()
    for k in ("pack", "reports"):
        cfg[k] = full_path('"%s"' % k, cfg.get(k))
    folders = {"trading": "", "longterm": ""}
    for k, v in cfg["folders"].items():
        if k not in folders:
            raise AutorunError('"folders" in autorun.json has "trading" and "longterm" only, not "%s"' % k)
        folders[k] = full_path('The "%s" folder' % k, v)
    cfg["folders"] = folders
    if not cfg["reports"] and folders["trading"]:
        cfg["reports"] = os.path.join(folders["trading"], "autorun")
    jobs = dict(DEFAULT_JOBS)
    jobs.update(cfg.get("jobs") or {})
    cfg["jobs"] = jobs
    for name, steps in jobs.items():
        if not isinstance(steps, list):
            raise AutorunError('"jobs": {"%s": ...} wants a list of steps' % name)
        for s in steps:
            check_safe(s)
            if not isinstance(s, dict) or s.get("folder") not in folders:
                raise AutorunError('a step in "jobs" wants "folder": "trading" or "longterm"')
    return cfg


def own_python():
    """The Python that runs auto-run now. The tools run with it too."""
    if not sys.executable:
        raise AutorunError("auto-run cannot find its own Python: run install.sh again")
    return sys.executable


def full_path(what, value):
    """A path from autorun.json: "" stays "", "~" is your home folder, and anything else must be a full
    path. launchd starts auto-run in its own folder, so a path like "myreports" would point there."""
    text = os.path.expanduser(str(value or "").strip())
    if text and not os.path.isabs(text):
        raise AutorunError('%s in autorun.json wants a full path (one that starts with /), not "%s". Run '
                           'install.sh again, or edit autorun.json' % (what, value))
    return text


def check_safe(s):
    """Auto-run never runs an order tool, or a tool's order, money or rule command, whatever the
    settings file says. It also runs only scripts inside the pack. -> the script's path in the pack."""
    raw = str((s or {}).get("script") or "").replace("\\", "/") if isinstance(s, dict) else ""
    rel = os.path.normpath(raw).replace("\\", "/") if raw.strip() else ""
    low = rel.lower()
    if not rel or rel == "." or os.path.isabs(raw) or low == ".." or low.startswith("../"):
        raise AutorunError("auto-run runs only the pack's own tools, not %r" % raw)
    parts = low.split("/")
    for p in parts:
        if p in NEVER_RUN:
            raise AutorunError("auto-run never runs %s: an order needs your own yes in the chat" % p)
    if parts[-1] in NEVER_SCRIPTS:
        raise AutorunError("auto-run never runs %s: an order needs your own yes in the chat" % rel)
    words = [str(x).strip().lower() for x in s.get("args") or []]
    for name, banned in NEVER_COMMANDS.items():
        if parts[-1] == name:
            for x in banned:
                if x in words:
                    raise AutorunError("auto-run never runs %s %s: that needs you in the chat" % (name, x))
    return rel


def check_places(cfg):
    """Plain problems with the paths in the settings, or []."""
    out = []
    if not (cfg["python"] and os.access(cfg["python"], os.X_OK)):
        out.append("no Python at %s: run install.sh again" % (cfg["python"] or "(not set)"))
    try:
        if not cfg["pack"] or not os.path.isfile(os.path.join(cfg["pack"], "psx-alerts", "alerts.py")):
            out.append("no PSX tools at %s" % (cfg["pack"] or "(not set): run install.sh again"))
        if not cfg["folders"].get("trading"):
            out.append("no trading folder in autorun.json: run install.sh again with --trading")
        if not cfg["folders"].get(cfg["calendar_folder"]):
            out.append('"calendar_folder" names the %s folder, but it is not set'
                       % FOLDER_WORDS[cfg["calendar_folder"]])
        for name, folder in sorted(cfg["folders"].items()):
            if folder and not os.path.isfile(os.path.join(folder, "psx", "config.json")):
                out.append("no psx/config.json in the %s folder (%s)" % (FOLDER_WORDS[name], folder))
    except PermissionError:
        out.append(BLOCKED)
    return out


def probe_access(cfg):
    """"" when the work folders can be read, else the BLOCKED text (macOS privacy control)."""
    try:
        if cfg["pack"]:
            os.listdir(cfg["pack"])
        for folder in cfg["folders"].values():
            if folder:
                with open(os.path.join(folder, "psx", "config.json"), "rb") as fh:
                    fh.read(1)
    except PermissionError:
        return BLOCKED
    except OSError:
        return ""          # missing files are told by check_places
    return ""


# ---------------------------------------------------------------- time words

def now_karachi(text=None):
    """Naive Karachi wall time: from text ("YYYY-MM-DD HH:MM", Karachi time), else the clock.
    The Mac's own time zone does not matter."""
    if text:
        try:
            return datetime.datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            raise AutorunError('--now wants "YYYY-MM-DD HH:MM" in Karachi time, like "2026-11-06 09:40"')
    return datetime.datetime.now(KARACHI).replace(tzinfo=None, second=0, microsecond=0)


def minutes(n):
    return datetime.timedelta(minutes=n)


def hm(text):
    return datetime.datetime.strptime(text, "%H:%M").time()


def day_words(d):
    """Friday 6 November 2026 (a report title)."""
    return "%s %d %s %d" % (WEEKDAYS[d.weekday()], d.day, MONTHS[d.month - 1], d.year)


def when_words(t, today=None):
    """today 9:15 AM / Friday 6 November, 9:15 AM."""
    if today is not None and t.date() == today:
        return "today " + clock_label(t)
    return "%s %d %s, %s" % (WEEKDAYS[t.weekday()], t.day, MONTHS[t.month - 1], clock_label(t))


def iso(t):
    return t.strftime("%Y-%m-%d %H:%M")


def from_iso(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def iso_epoch(text):
    """A tool's ISO time ("2026-11-02T10:02:00+05:00") -> seconds since the epoch, or None.
    A time with no offset is Karachi time."""
    try:
        t = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=KARACHI)
    return t.timestamp()


# ---------------------------------------------------------------- the PSX clock (the pack's own)

def tool_env():
    """A small, fixed environment for the tools (launchd gives almost none)."""
    env = {"HOME": HOME, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin",
           "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    for k in ("USER", "LOGNAME", "TMPDIR", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY",
              "no_proxy"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def calendar_config(cfg):
    return os.path.join(cfg["folders"][cfg["calendar_folder"]], "psx", "config.json")


def config_stamp(cfg):
    """The sha256 of the calendar config (its bytes, not its time): a day added under "holidays" or
    "open_days" counts at the next tick, even when the file time did not change."""
    try:
        with open(calendar_config(cfg), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def ask_clock(cfg, d):
    """The pack's PSX clock for day d -> its JSON (segments, session, reason, calendar notes)."""
    argv = [cfg["python"], os.path.join(cfg["pack"], "psx-alerts", "alerts.py"), "clock", "--json",
            "--now", d.isoformat() + " 00:01", "--config", calendar_config(cfg)]
    try:
        p = subprocess.run(argv, cwd=cfg["folders"][cfg["calendar_folder"]], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=tool_env(), timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise AutorunError("the PSX clock (psx-alerts/alerts.py clock) did not run: %s" % e)
    try:
        doc = json.loads(p.stdout.decode("utf-8", "replace"))
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AutorunError("the PSX clock did not answer: %s" % (err[-1] if err else "no output"))
    return doc


def day_info(cfg, state, d, clock=None):
    """The PSX day d: {"segments": [["09:30", "15:30"]], "session", "reason", "calendar_known",
    "calendar_note"}. Cached in the state by date and by the sha256 of the calendar config."""
    clock = clock or ask_clock
    cache = state.setdefault("clock", {})
    key, stamp = d.isoformat(), config_stamp(cfg)
    hit = cache.get(key)
    if isinstance(hit, dict) and hit.get("stamp") == stamp:
        return hit
    st = clock(cfg, d)
    info = {"stamp": stamp, "segments": st.get("segments") or [], "session": st.get("session"),
            "reason": st.get("reason") or "", "calendar_known": bool(st.get("calendar_known", True)),
            "calendar_note": st.get("calendar_note") or ""}
    cache[key] = info
    for old in sorted(cache)[:-40]:
        del cache[old]
    return info


def expected_holiday(info):
    """The holiday's name when the clock says closed for an expected moon holiday, else ""."""
    if not info or info.get("segments") or EXPECTED_MARK not in (info.get("reason") or ""):
        return ""
    m = re.search(r"PSX holiday: (.+?) \(expected", info["reason"])
    return m.group(1).strip() if m else "a moon holiday"


def day_plan(cfg, info, d):
    """When each job runs on day d (naive Karachi datetimes), or None on a closed day."""
    segs = [(datetime.datetime.combine(d, hm(a)), datetime.datetime.combine(d, hm(b)))
            for a, b in (info or {}).get("segments") or []]
    if not segs:
        return None
    first, last = segs[0][0], segs[-1][1]
    lead = cfg["morning_lead_min"]
    return {"open": first, "close": last, "segments": segs,
            "morning": first - minutes(lead),
            "checks": [(a + minutes(cfg["check_start_after_min"]), b) for a, b in segs],
            "finals": [b + minutes(cfg["final_check_after_min"]) for a, b in segs],
            "after_close": last + minutes(cfg["after_close_delay_min"]),
            "give_up": last + minutes(cfg["after_close_give_up_min"]),
            "tape": (first - minutes(15), last + minutes(10)),
            "awake": (first - minutes(lead + 5), last + minutes(cfg["after_close_delay_min"] + 20))}


def busy_hours(plan, now):
    """From the pre-open to 15 minutes after the close: no long jobs then."""
    return bool(plan) and plan["open"] - minutes(15) <= now < plan["close"] + minutes(15)


def last_trading_day(cfg, state, d, clock=None, back=10):
    """The newest PSX trading day before d, with its info, or (None, None)."""
    for i in range(1, back + 1):
        x = d - datetime.timedelta(days=i)
        info = day_info(cfg, state, x, clock)
        if info["segments"]:
            return x, info
    return None, None


def holiday_ahead(cfg, state, d, clock=None):
    """The first expected moon holiday after day d and before the next PSX session -> (day, name),
    or (None, "")."""
    for i in range(1, 8):
        x = d + datetime.timedelta(days=i)
        info = day_info(cfg, state, x, clock)
        if info["segments"]:
            return None, ""
        name = expected_holiday(info)
        if name:
            return x, name
    return None, ""


# ---------------------------------------------------------------- what is due

def done(state, job, d):
    return bool(((state.get("done") or {}).get(d.isoformat()) or {}).get(job))


def mark(state, job, d, value):
    state.setdefault("done", {}).setdefault(d.isoformat(), {})[job] = value
    days = state["done"]
    for old in sorted(days)[:-30]:
        del days[old]


def before_install(state, d):
    t = from_iso(state.get("installed"))
    return t is not None and d < t.date()


def weekly_slot(cfg, now):
    """The newest weekly time at or before now."""
    wd, t = int(cfg["weekly"]["weekday"]), hm(cfg["weekly"]["time"])
    slot = datetime.datetime.combine(now.date() - datetime.timedelta(days=(now.weekday() - wd) % 7), t)
    if slot > now:
        slot -= datetime.timedelta(days=7)
    return slot


def paused_all(state, now):
    """True while "pause all" holds: until resume, or until its end time (pause all --minutes N)."""
    p = state.get("pause") or {}
    if not p.get("all"):
        return False
    until = from_iso(p.get("until"))
    return until is None or now < until


def checks_paused(state, d):
    return (state.get("pause") or {}).get("checks_off") == d.isoformat()


def end_pause(state, now):
    """A "pause all --minutes N" that is over ends by itself. -> True when it just ended."""
    p = state.get("pause") or {}
    until = from_iso(p.get("until"))
    if p.get("all") and until is not None and now >= until:
        p.pop("all", None)
        p.pop("until", None)
        if not p:
            state.pop("pause", None)
        return True
    return False


def due(cfg, state, now, today_info, prev_day=None, prev_info=None):
    """What to run now, in order: [(job, date, note)]. No side effects (the tests call it)."""
    out = []
    d = now.date()
    plan = day_plan(cfg, today_info, d)
    if paused_all(state, now):
        return out
    # 1. the after-close job of the last trading day, if the Mac was asleep or off then
    if prev_day is not None and not busy_hours(plan, now) and not before_install(state, prev_day):
        if day_plan(cfg, prev_info, prev_day) and not done(state, "after_close", prev_day) \
                and (d - prev_day).days <= 4:
            out.append(("after_close", prev_day, "late"))
    checks_on = not checks_paused(state, d)
    if plan:
        # 2. the morning job: from 75 minutes before the open until the close
        if plan["morning"] <= now < plan["close"] and not done(state, "morning", d) \
                and not before_install(state, d):
            out.append(("morning", d, "late" if now >= plan["open"] else ""))
        # 3. price checks while PSX is open, and one last check after each part of the day
        last = from_iso(state.get("last_check"))
        every = minutes(max(1, cfg["check_every_min"] - 1))
        if checks_on and any(a <= now < b for a, b in plan["checks"]) and (last is None or now - last >= every):
            out.append(("check", d, ""))
        for i, t in enumerate(plan["finals"]):
            if checks_on and t <= now < t + minutes(60) and not done(state, "final%d" % i, d):
                out.append(("check", d, "final%d" % i))
        # 4. the after-close job, and its retries while the closing file is not out
        if now >= plan["after_close"] and not done(state, "after_close", d):
            wait = from_iso(((state.get("waiting") or {}).get(d.isoformat())))
            if wait is None or now >= wait:
                out.append(("after_close", d, ""))
    elif expected_holiday(today_info) and not before_install(state, d):
        # 5. an expected moon holiday: the clock says closed, but PSX may open. One look at 10:00.
        if checks_on and now >= datetime.datetime.combine(d, PROBE_TIME) and not done(state, "probe", d):
            out.append(("probe", d, ""))
    # 6. the weekly job, never from the pre-open to 15 minutes after the close
    slot = weekly_slot(cfg, now)
    if not done(state, "weekly", slot.date()) and not busy_hours(plan, now) \
            and not before_install(state, slot.date()):
        out.append(("weekly", slot.date(), "late" if now - slot > datetime.timedelta(hours=6) else ""))
    return out


# ---------------------------------------------------------------- files, locks, logs

def read_json(path, default):
    """(the JSON file, "") or (default, why). Read only: it never moves, changes or writes the file.
    why is "" when the file is missing; else "is not valid JSON (line 2, column 20)" or the like."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return default, ""
    except ValueError as e:
        where = " (line %d, column %d)" % (e.lineno, e.colno) if getattr(e, "lineno", None) else ""
        return default, "is not valid JSON%s" % where
    except OSError as e:
        return default, "could not be read (%s)" % (e.strerror or e)
    if not isinstance(doc, type(default)):
        return default, "is not a JSON %s" % ("object ({...})" if isinstance(default, dict) else "list ([...])")
    return doc, ""


def load_json(path, default):
    """A JSON file, or default when it is missing, broken or not that kind. Read only: a user's file
    in a psx/ folder is never moved or changed (file_problems() tells a broken one)."""
    return read_json(path, default)[0]


def load_own(path, default):
    """One of auto-run's own files (state.json, tape.json, told.json, under state/). A broken one is
    moved aside (to <file>.bad), never fatal. Never use this for a file in a psx/ folder."""
    doc, why = read_json(path, default)
    if why.startswith("is not valid JSON"):
        try:
            os.replace(path, path + ".bad")
        except OSError:
            pass
    return doc


def write_text(path, text):
    """Write a file the safe way: a temp file, then a rename."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = "%s.tmp%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def save_json(path, doc):
    write_text(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


class Lock(object):
    """A lock file (flock). The lock goes away by itself when the process ends, even in a crash."""

    def __init__(self, name):
        self.path = os.path.join(state_dir(), name)
        self.fh = None

    def acquire(self):
        import fcntl      # macOS and Linux have it; the tests skip the lock where it is missing
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            self.fh.close()
            self.fh = None
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                return False
            raise
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write("%d\n" % os.getpid())
        self.fh.flush()
        return True

    def release(self):
        if self.fh is not None:
            import fcntl
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None

    def held_by_other(self):
        if self.acquire():
            self.release()
            return False
        return True


def log(msg):
    try:
        os.makedirs(log_dir(), exist_ok=True)
        path = os.path.join(log_dir(), "autorun.log")
        if os.path.exists(path) and os.path.getsize(path) > 2000000:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s PKT  %s\n" % (iso(now_karachi()), msg))
    except OSError:
        pass


# ---------------------------------------------------------------- the Mac tools (the tests swap these)

def run_osascript(title, text, sound):
    """Show one message with osascript (plan B: when the app is not the one that runs us)."""
    script = 'display notification ("%s" & linefeed & "%s") with title "%s"' % (esc(text), esc(FOOTER), esc(title))
    if sound:
        script += ' sound name "%s"' % esc(sound)
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass


def start_caffeinate(seconds):
    """No idle sleep for a while: caffeinate in its own session, with no shared output, so it outlives
    this run and never holds the app."""
    try:
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-t", str(int(seconds))], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                         close_fds=True)
    except OSError:
        pass


def caffeinate_while(pid):
    """No idle sleep while process pid runs (the tape). -> the caffeinate process, or None."""
    try:
        return subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(pid)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


def kickstart(label):
    """Ask launchd to start one of auto-run's own jobs now (the tape)."""
    subprocess.run(["/bin/launchctl", "kickstart", "gui/%d/%s" % (os.getuid(), label)],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30)


def open_file(path):
    """Open a report (TextEdit) or a folder (Finder)."""
    argv = ["/usr/bin/open", "-e", path] if os.path.isfile(path) else ["/usr/bin/open", path]
    subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def other_pollers():
    """PIDs of PSX tools that poll the site by themselves now (a tape, an entry watch). Read only."""
    found = []
    for pat in POLLERS:
        try:
            r = subprocess.run(["/usr/bin/pgrep", "-f", pat], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        found += [int(x) for x in r.stdout.split() if x.strip().isdigit() and int(x) != os.getpid()]
    return found


def esc(text):
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------- messages

def is_action(line):
    """The word rule of the pack (tests/_words.py): a line that offers an action."""
    low = line.lower()
    return "could buy" in low or "could sell" in low or "You could" in line or "The rule says:" in line


def clean(text, n):
    text = re.sub(r"[\t\r\n]+", " ", str(text)).strip()
    return text if len(text) <= n else text[:n - 3].rstrip() + "..."


def clip(text, n=MESSAGE_MAX):
    """One message: no tabs or line breaks, at most n characters. A cut never leaves an action line
    without its "You decide.": a long text keeps the facts before the first action, then says to
    open the report."""
    t = re.sub(r"[\t\r\n]+", " ", str(text)).strip()
    if len(t) <= n:
        return t
    keep = []
    for s in re.split(r"(?<=[.!?])\s+", t):
        if is_action(s) or len(" ".join(keep + [s, MORE])) > n:
            break
        keep.append(s)
    if keep:
        return " ".join(keep + [MORE])
    cut = t[:n - len(MORE) - 5].rsplit(" ", 1)[0] + "..."
    return MORE if is_action(cut) else cut + " " + MORE


class Notes(object):
    """Mac messages. Mode "applet": print lines that the PSX Autorun app shows (under its own name).
    "osascript": show them here. "print" (the default by hand): write them in the terminal.
    Alerts and problems come first, then the other messages, then the counts (rank 0, 1, 2).
    More than 4: the first 3, then "N more messages: open the report.". Each message ends with
    the footer, on a line of its own."""

    def __init__(self, cfg=None, mode=None):
        self.items = []
        self.cfg = cfg or DEFAULTS
        self.mode = mode or os.environ.get("PSX_NOTIFY") or "print"

    def add(self, kind, title, text, sound=False, rank=1):
        notify = self.cfg.get("notify") or {}
        if not notify.get(kind, True):
            return
        self.items.append((rank, clean(title, 60), clip(text), (notify.get("sound") or "") if sound else ""))

    def ordered(self):
        items = sorted(self.items, key=lambda x: x[0])     # a stable sort keeps the order inside a rank
        if len(items) > 4:
            items = items[:3] + [(1, "PSX auto-run", "%d more messages: open the report." % (len(items) - 3), "")]
        return [(title, text, sound) for _, title, text, sound in items]

    def emit(self):
        shown = self.ordered()
        for title, text, sound in shown:
            if self.mode == "applet":
                print("notify\t%s\t%s%s%s\t%s" % (title, text, BREAK, FOOTER, sound))
            elif self.mode == "osascript":
                run_osascript(title, text, sound)
            else:
                print("Notification: %s: %s" % (title, text))
            log("told: %s: %s" % (title, text))
        if shown and self.mode not in ("applet", "osascript"):
            print(FOOTER)
        self.items = []


# ---------------------------------------------------------------- running the tools

def folder_file(folder, *parts):
    return os.path.join(folder, "psx", *parts)


def positions(folder):
    """The positions in <folder>/psx/portfolio.json with a symbol and shares above 0."""
    doc = load_json(folder_file(folder, "portfolio.json"), {}) if folder else {}
    out = []
    for p in doc.get("positions") or []:
        if isinstance(p, dict) and p.get("ticker") and isinstance(p.get("shares"), (int, float)) \
                and not isinstance(p.get("shares"), bool) and p["shares"] > 0:
            out.append(p)
    return out


def holdings(folder):
    return [str(p["ticker"]).upper() for p in positions(folder)]


def folder_config(folder):
    return load_json(folder_file(folder, "config.json"), {}) if folder else {}


def file_error(folder, *parts):
    """"" when <folder>/psx/<parts> reads (or is missing), else why it does not, in plain words."""
    return read_json(folder_file(folder, *parts), {})[1] if folder else ""


def broken_files(cfg):
    """The user's files in the psx/ folders that auto-run reads but cannot parse now. Read only: auto-run
    never moves or changes them. -> [(key, words)]."""
    out = []
    for name in ("trading", "longterm"):
        folder = cfg["folders"].get(name)
        for parts in USER_FILES if folder else ():
            why = file_error(folder, *parts)
            if why:
                rel = "/".join(("psx",) + parts)
                out.append(("file:%s:%s" % (name, rel), FILE_BAD % (rel, FOLDER_WORDS[name], why)))
    return out


def file_problems(cfg, state, notes, now):
    """Tell each broken user file (at once, then at most once an hour) and keep it open until the file
    reads again; then "working again". -> the words of the broken ones."""
    bad = broken_files(cfg)
    keys = set(k for k, _ in bad)
    for key, words in bad:
        problem(state, notes, cfg, key, words, now)
    for key in [k for k in state.get("problems") or {} if k.startswith("file:") and k not in keys]:
        _, name, rel = key.split(":", 2)
        solved(state, notes, key, "%s in the %s folder is fixed." % (rel, FOLDER_WORDS.get(name, name)))
    return [w for _, w in bad]


def folder_site_on(folder):
    """True only when the folder's psx/config.json says "psx_site": true (site_on, the pack's own rule)."""
    return site_on(folder_config(folder))


def classify(s, rc, out, err, timed_out=False):
    """("ok" | "skipped" | "failed", why) for one tool run."""
    if timed_out:
        return "failed", "it did not finish in time"
    text = (err or "") + "\n" + (out or "")
    for q in s.get("quiet") or []:
        if q in text:
            return "skipped", q
    if PERM_RE.search(text):
        return "failed", "macOS blocked a file (Operation not permitted)"
    if "Traceback (most recent call last)" in (err or ""):
        lines = [x for x in err.strip().splitlines() if x.strip()]
        return "failed", "the tool crashed: " + (lines[-1] if lines else "no message")
    m = TOOL_ERR_RE.search(err or "")
    if rc not in s.get("ok", [0, 1]):
        return "failed", (m.group(2) if m else first_line(err) or first_line(out) or "exit code %s" % rc)
    if s["kind"] == "alerts" and rc == 2:
        errs = [x[7:] for x in (out or "").splitlines() if x.startswith("error: ")]
        return "failed", errs[0] if errs else "a rule or a file could not be read"
    if rc == 1 and m:
        return "failed", m.group(2)
    return "ok", ""


def feed_count(out, folder):
    """(the news feeds that did not answer, the feeds psx-news reads) from its "note: feed X: ..." lines
    and the folder's "news_feeds" (psx-news reads 5 feeds when it has none)."""
    spec = folder_config(folder).get("news_feeds") if folder else None
    tried = len([x for x in spec if x]) if isinstance(spec, list) else NEWS_FEEDS_DEFAULT
    return len(set(FEED_RE.findall(out or ""))), tried


def first_line(text):
    for x in (text or "").splitlines():
        if x.strip():
            return x.strip()
    return ""


def tape_file(folder, d):
    """Today's market tape in a folder (alerts.py adds each screener page it reads)."""
    return folder_file(folder, "data", "tape", d.isoformat(), "market.jsonl")


def shared_tape(cfg):
    """True when the long-term folder's tape is the trading folder's tape (a link), so the long-term
    check can read the page the trading check just saved."""
    t, l = cfg["folders"].get("trading"), cfg["folders"].get("longterm")
    if not (t and l):
        return False
    return os.path.realpath(folder_file(t, "data", "tape")) == os.path.realpath(folder_file(l, "data", "tape"))


def tape_mark(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def tape_grew(path, mark, began):
    """True when the tape file got a new last line with a fetch time at or after `began` (seconds
    since the epoch, the time the step began)."""
    try:
        if os.path.getsize(path) <= mark:
            return False
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 65536))
            lines = [x for x in fh.read().decode("utf-8", "replace").splitlines() if x.strip()]
        snap = json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return False
    t = iso_epoch(snap.get("fetched")) if isinstance(snap, dict) else None
    return t is not None and t >= int(began)


def run_step(cfg, s, site_busy, offline_ok=False):
    """Run one tool. -> a result dict. offline_ok: a "tape" step reads the page the trading folder's
    step just saved (--offline). site_busy: another poller reads the site now, so no step does."""
    rel = check_safe(s)
    folder = cfg["folders"].get(s["folder"]) or ""
    script = os.path.join(cfg["pack"], rel)
    if cfg["pack"] and os.path.exists(script):     # the guard again, on where the file really is (a link)
        check_safe(dict(s, script=os.path.relpath(os.path.realpath(script), os.path.realpath(cfg["pack"]))))
    res = {"id": s["id"], "status": "ok", "why": "", "out": "", "err": "", "rc": None, "argv": [], "secs": 0.0,
           "silent": False, "online": False}
    if not folder:
        res.update(status="skipped", why="no %s folder" % FOLDER_WORDS.get(s["folder"], s["folder"]), silent=True)
        return res
    if not os.path.isfile(script):
        if s.get("optional"):
            res.update(status="skipped", why="not in the pack", silent=True)
        else:
            res.update(status="failed", why="no tool at %s" % script)
        return res
    if s.get("needs_file") and not os.path.isfile(os.path.join(folder, s["needs_file"])):
        res.update(status="skipped", why="no %s" % s["needs_file"], silent=bool(s.get("optional")))
        return res
    args = []
    for x in s["args"]:
        if x == "@holdings":
            args += holdings(folder)
        else:
            args.append(str(x).replace("{price_source}", cfg["price_source"]))
    if "@holdings" in s["args"] and not holdings(folder):
        bad = file_error(folder, "portfolio.json")
        res.update(status="skipped", why=("psx/portfolio.json %s, so no share was read" % bad) if bad
                   else "no shares in psx/portfolio.json")
        return res
    flags = []
    if s["site"] in ("yes", "tape"):
        if site_busy:
            flags = list(s["busy_args"])
        elif s["site"] == "tape" and offline_ok:
            flags = ["--offline"]
    argv = [cfg["python"], script] + args + flags
    res["argv"] = argv
    res["online"] = s["site"] in ("yes", "tape") and not flags and folder_site_on(folder)
    t0 = time.time()
    try:
        p = subprocess.run(argv, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=tool_env(), timeout=s.get("timeout") or cfg["step_timeout_s"])
        out, err, rc, late = p.stdout, p.stderr, p.returncode, False
    except subprocess.TimeoutExpired as e:
        out, err, rc, late = e.stdout or b"", e.stderr or b"", None, True
    except OSError as e:
        out, err, rc, late = b"", str(e).encode(), None, False
        if isinstance(e, PermissionError):
            err = b"Operation not permitted: " + err
    res["secs"] = round(time.time() - t0, 1)
    res["out"], res["err"], res["rc"] = out.decode("utf-8", "replace"), err.decode("utf-8", "replace"), rc
    res["status"], res["why"] = classify(s, rc, res["out"], res["err"], late)
    if s["kind"] == "news" and res["status"] == "ok":
        res["feeds"] = feed_count(res["out"], folder)
        if res["feeds"][1] and res["feeds"][0] >= res["feeds"][1]:
            res.update(status="failed", why="no news feed answered (%d of %d). Check the Mac's internet"
                       % res["feeds"])
    if res["online"]:
        SLEEP(SITE_GAP)        # the next tool's first request comes at least 1.5 seconds after this one's last
    log("%s %s: %s %s (%.1f s)" % (s["folder"], s["id"], res["status"], res["why"], res["secs"]))
    return res


def summarize(s, res, folder, today=None):
    """One plain line about a tool run, for "In short". Also sets res["count"]: setups found,
    news items, alerts that rang, signals to read, or 1 for new insider notices. today: the PSX day the
    tools saw (for the alert rules that hold a date)."""
    out, title = res["out"], s["title"]
    res["count"] = 0
    if res["status"] == "skipped":
        return "%s: nothing to check (%s)." % (title, res["why"])
    if res["status"] == "failed":
        return "%s: PROBLEM: %s." % (title, res["why"].rstrip("."))
    if s["kind"] == "scan":
        doc, bad = read_json(folder_file(folder, "watchlist.json"), {})
        if bad:
            return "%s: psx/watchlist.json %s, so the setups were not counted. The scan's own words are below." % (
                title, bad)
        hits = [str(h.get("symbol")) for h in doc.get("hits") or [] if isinstance(h, dict) and h.get("symbol")]
        blocked = set(str(x).upper() for x in folder_config(folder).get("blocked") or [])
        mine = [h for h in hits if h in blocked]
        mine += [str(x) for x in doc.get("blocked_left_out") or [] if str(x) not in mine]
        hits = [h for h in hits if h not in blocked]
        res["count"] = len(hits)
        stale = " Note: the newest closing file is old." if "newest closing file is of" in out else ""
        held = (" Also matched, but on your blocked list (long-term shares): %s." % ", ".join(mine)) if mine else ""
        if not hits:
            return "%s: no setups.%s%s" % (title, held, stale)
        more = " and %d more" % (len(hits) - 4) if len(hits) > 4 else ""
        return "%s: %d setup%s: %s%s.%s%s" % (title, len(hits), "" if len(hits) == 1 else "s",
                                              ", ".join(hits[:4]), more, held, stale)
    if s["kind"] == "news":
        n = 0 if res["rc"] == 1 else len(ITEM_RE.findall(out))
        res["count"] = n
        down, tried = res.get("feeds") or feed_count(out, folder)
        part = (" %d of %d news feeds did not answer." % (down, tried)) if down else ""
        return "%s: %s.%s" % (title, "%d item%s to read" % (n, "" if n == 1 else "s") if n else "nothing new", part)
    if s["kind"] == "insiders":
        res["count"] = 1 if res["rc"] == 0 else 0
        return "%s: %s." % (title, "new notices to read" if res["rc"] == 0 else "nothing new")
    if s["kind"] == "alerts":
        rings = [x for x in out.splitlines() if x.startswith("ALERT ")]
        res["count"] = len(rings)
        m = WATCHED_RE.search(out)
        own = [] if rings else still_met(out)           # psx-alerts' own line for each met swing rule
        kept = [] if own else held_met(folder)          # else from its state file, read only
        res["met"] = 0 if rings else (len(own) or len(kept))
        head = "%d rang" % len(rings) if rings else "no new alert rang" + (
            " (%s rule%s watched)" % (m.group(2), "" if m.group(2) == "1" else "s") if m else "")
        if own:              # never just "no new alert" while a share you still hold has a met rule (D54)
            head += ". %s still met" % ("A swing rule is" if len(own) == 1 else "%d swing rules are" % len(own))
        return " ".join(["%s: %s." % (title, head)] + own + alert_facts(folder, out, today, kept))
    if s["kind"] == "results":
        return "%s: %s" % (title, results_words(res))
    if s["kind"] == "invest":
        rows = SIGNAL_RE.findall(out)
        if not rows:
            return "%s: %s" % (title, first_line(out) or "no signals.")
        by = dict((k, [sym for sig, sym in rows if sig == k]) for k in ("ADD", "TRIM", "REVIEW", "HOLD"))
        res["count"] = len(rows) - len(by["HOLD"])
        bits = ["%s: %s" % (k, ", ".join(by[k])) for k in ("TRIM", "REVIEW", "ADD") if by[k]]
        cand = len(CANDIDATE_RE.findall(out))           # a HOLD of a share you do not hold is no "keep"
        bits.append("HOLD: %d" % (len(by["HOLD"]) - cand) +
                    (" of your shares and %d candidate%s" % (cand, "" if cand == 1 else "s") if cand else ""))
        return "%s: %s. Rules, not predictions." % (title, ". ".join(bits))
    for x in out.splitlines():
        if x.lower().startswith("summary: "):
            return "%s: %s" % (title, x[9:].strip())
    return "%s: %s" % (title, first_line(out) or "done.")


def names(items):
    """["A"] -> "A"; ["A", "B", "C"] -> "A, B and C"."""
    items = list(items)
    return items[0] if len(items) == 1 else "%s and %s" % (", ".join(items[:-1]), items[-1])


def rang_on(stamp):
    """psx-alerts' ring time (UTC, "2026-11-12T05:05:00Z") -> "Thu 2026-11-12" (Karachi), or None."""
    t = iso_epoch(stamp)
    return day_label(datetime.datetime.fromtimestamp(t, KARACHI).date()) if t is not None else None


def still_met(out):
    """psx-alerts' own lines for the met rules of the swing positions you still hold: the lines right
    after its "No new alert." line, up to its notes, like "DEMO: the stop rule was met on Mon
    2026-11-02. It rang today at 10:00 AM PKT. You still hold 1,436 shares in psx/portfolio.json."
    [] after "Nothing hit." (no such rule). -> [lines]."""
    lines = (out or "").splitlines()
    for i, x in enumerate(lines):
        m = WATCHED_RE.match(x)
        if not m:
            continue
        if m.group(1) != MET_HEAD:
            return []
        got = []
        for y in lines[i + 1:]:
            if not y.strip() or y.startswith(("note: ", "error: ", "ALERT ")):
                break
            got.append(y.strip())
        return got
    return []


def swing_held(folder):
    """The shares held with a swing plan in <folder>/psx/portfolio.json: psx-alerts reads each closing
    file for them (D53). Read only."""
    return set(str(p["ticker"]).upper() for p in positions(folder)
               if isinstance(p.get("plan"), dict) and p["plan"].get("style") == "swing")


def held_met(folder):
    """The met swing rules of the shares you still hold, from psx-alerts' state (psx/state/alerts.json
    "swing_met"), read only: ["DEMO stop rule (met Mon 2026-11-02)"]. For a psx-alerts line with no
    rule lines after it."""
    held = set(holdings(folder)) if folder else set()
    state = load_json(folder_file(folder, "state", "alerts.json"), {}) if held else {}
    met = state.get("swing_met") if isinstance(state.get("swing_met"), dict) else {}
    out = []
    for key in sorted(met):
        sym, kinds = str(key)[:-11].upper(), met[key] if isinstance(met[key], dict) else {}
        for kind in ("stop", "target", "trend"):
            if sym in held and kinds.get(kind):
                out.append("%s %s rule (met %s)" % (sym, kind, date_words(kinds[kind])))
    return out


def alert_facts(folder, out, today=None, met_words=None):
    """What a "no new alert" line must not hide (D54), from the folder's own files, read only:
    a swing rule met earlier while the share is still held (met_words, else held_met: psx/state/alerts.json
    "swing_met"), a stop, target or level rule in psx/alerts.json that rang earlier while the share is still
    held (it rings once), and prices no rule could use (alerts.py's notes). -> [sentences]."""
    held = set(holdings(folder)) if folder else set()
    state = load_json(folder_file(folder, "state", "alerts.json"), {}) if held else {}
    seen = {}
    m = WATCHED_RE.search(out or "")
    for sym, price, when in SEEN_RE.findall(m.group(3) if m else ""):
        seen[sym] = "%s %s as of %s" % (sym, price, when)
    facts, rang_words = [], []
    met_words = held_met(folder) if met_words is None else met_words
    rung = state.get("rung") if isinstance(state.get("rung"), dict) else {}
    once = False
    doc = load_json(folder_file(folder, "alerts.json"), {}) if held else {}
    for r in doc.get("rules") if isinstance(doc.get("rules"), list) else []:
        if not isinstance(r, dict) or r.get("type") not in LEVEL_RULES or not isinstance(r.get("price"), (int, float)):
            continue
        sym = str(r.get("symbol") or "").upper()
        rid = str(r.get("id") or "%s-%s" % (r.get("symbol") or "market", r["type"]))
        if sym not in held or rid not in rung or (r.get("date") and str(r["date"]) != str(today)):
            continue
        when = rang_on(rung[rid])
        bits = ["rang " + when] if when else []
        if seen.get(sym):
            bits.append(seen[sym])
        rang_words.append("%s %s %.2f%s" % (sym, LEVEL_RULES[r["type"]], r["price"],
                                            " (%s)" % "; ".join(bits) if bits else ""))
        once = once or not r.get("rearm")
    if met_words:
        facts.append("Met, and you still hold the share: %s." % "; ".join(met_words))
    if rang_words:
        facts.append("Rang, and you still hold the share: %s.%s" % (
            "; ".join(rang_words), " Such a rule rings once, then stays quiet until you reset it." if once else ""))
    late = list(dict.fromkeys(NOT_TODAY_RE.findall(out or "")))
    swing = swing_held(folder) if late else set()           # their swing rules read each closing file (D53)
    closes, plain = [x for x in late if x in swing], [x for x in late if x not in swing]
    if closes:
        facts.append("%s %s no price from today, so %s swing rules were checked on the closing files only." % (
            names(closes), "has" if len(closes) == 1 else "have", "its" if len(closes) == 1 else "their"))
    if plain:
        facts.append("%s %s no price from today, so %s rules were not checked." % (
            names(plain), "has" if len(plain) == 1 else "have", "its" if len(plain) == 1 else "their"))
    old = ["%s %s minutes" % (sym, n) for sym, n in OLD_RE.findall(out or "")]
    if old:
        facts.append("Old prices: %s old." % names(old))
    return facts


def results_words(res):
    """The week report's results line: how many shares were read (facts.py exits 0 only when every one
    was), from the step's own command line."""
    argv = res.get("argv") or []
    syms = argv[2:argv.index("--metrics")] if "--metrics" in argv else argv[2:]
    if not syms:
        return first_line(res["out"]) or "done."
    shown = ", ".join(syms[:4]) + (" and %d more" % (len(syms) - 4) if len(syms) > 4 else "")
    return ("Results read for %d share%s (%s). Saved in psx/metrics.json. The full output file has %s."
            % (len(syms), "" if len(syms) == 1 else "s", shown, "it" if len(syms) == 1 else "each one"))


def notify_text(job, results, failed, told=0):
    """The short text of a job's message: counts only, no advice. told: the problems of this run that
    are not a tool step (a closing file not found, the reports in reports-fallback, a broken file).
    A met swing rule of a share you still hold that rang earlier (no new alert now) is counted first."""
    setups, news, insiders, signals, met = None, 0, False, 0, 0
    for s, r in results:
        if r["status"] != "ok":
            continue
        if s["kind"] == "scan":
            setups = r.get("count", 0)
        elif s["kind"] == "news":
            news += r.get("count", 0)
        elif s["kind"] == "insiders" and r.get("count"):
            insiders = True
        elif s["kind"] == "invest":
            signals = r.get("count", 0)
        elif s["kind"] == "alerts":
            met += r.get("met", 0)
    bits = ["Swing rules still met: %d." % met] if met else []
    if setups is not None:
        bits.append("Setups: %d." % setups)
    if signals:
        bits.append("Long-term signals to read: %d." % signals)
    if job != "weekly":
        bits.append("News: %s." % ("%d new" % news if news else "nothing new"))
    if insiders:
        bits.append("New insider notices.")
    bits.append("Problems: %d." % (len(failed) + told))
    return " ".join(bits) + " Click to open the report."


# ---------------------------------------------------------------- reports

FALLBACK_WORDS = BLOCKED + " The reports go to Library/Application Support/psx-autorun/reports-fallback until then."
JOB_TITLES = {"morning": "Morning check", "after_close": "After-close check", "weekly": "Week report",
              "check": "Price checks", "probe": "Holiday look"}


def report_root(cfg):
    """The reports folder, or auto-run's own reports-fallback/ when macOS blocks Documents."""
    if cfg.get("_fallback") or not cfg.get("reports"):
        return os.path.join(home_dir(), "reports-fallback")
    return cfg["reports"]


def put_report(cfg, rel, text):
    """Write <reports>/<rel> (a temp file, then a rename). -> the path. When macOS blocks the reports
    folder, the file goes to reports-fallback/ and cfg["_fallback"] is set, so the job tells you."""
    try:
        path = os.path.join(report_root(cfg), rel)
        write_text(path, text)
        return path
    except PermissionError:
        cfg["_fallback"] = True
        path = os.path.join(report_root(cfg), rel)
        write_text(path, text)
        return path


def reports_writable(cfg):
    """Test the reports folder before a job or a round writes, so its report can count the problem.
    -> False when it is blocked: then cfg["_fallback"] is set, and the reports go to reports-fallback/."""
    if cfg.get("_fallback"):
        return False
    if not cfg.get("reports"):
        return True
    test = os.path.join(cfg["reports"], ".write-test")
    try:
        write_text(test, "ok\n")
        os.remove(test)
    except PermissionError:
        cfg["_fallback"] = True
        return False
    except OSError:
        pass                 # another kind of error shows when the report itself is written
    return True


def for_you(text):
    """Tool words for the reports, which you read: a line written for Claude ("Ask the user to open ...")
    is said to you. The link stays. The full output file keeps the tool's own words."""
    return ASK_RE.sub(ASK_WORDS, text or "")


def indent(text, n):
    lines = [x.rstrip() for x in for_you(text).strip().splitlines()]
    cut = len(lines) > n
    lines = lines[:n] + (["(more lines in the full output file)"] if cut else [])
    return "\n".join("    " + x for x in lines) if lines else "    (no output)"


def full_block(title, res):
    return "\n".join(["=" * 70, title, "command: " + " ".join(res.get("argv") or []),
                      "result: %s %s (exit code %s, %.1f s)" % (res["status"], res["why"], res["rc"], res["secs"]),
                      "-" * 70, res["out"], res["err"]])


def write_report(cfg, job, d, now, short, sections, late_note="", extra_top=()):
    """Write reports/<date>/<job>.md (and -full.txt), and LATEST.md. -> the report path."""
    folder = os.path.join("reports", d.isoformat() if job != "weekly" else "weekly")
    name = job.replace("_", "-") if job != "weekly" else "week-of-%s" % d.isoformat()
    made = clock_label(now) if now.date() == d else when_words(now)
    lines = ["# %s: %s" % (JOB_TITLES.get(job, job), day_words(d)), "",
             "Made by PSX auto-run at %s, Karachi time." % made]
    if late_note:
        lines += ["", late_note]
    lines += ["", "## In short", ""] + ["- " + x for x in list(extra_top) + list(short)]
    lines += ["", FOOTER, ""]
    full = []
    for title, res in sections:
        lines += ["## " + title, "", indent(res["out"] if res["out"].strip() else res["err"], cfg["show_lines"]), ""]
        full.append(full_block(title, res))
    lines += ["Full tool output: %s-full.txt" % name, "", FOOTER, ""]
    text = "\n".join(lines)
    path = put_report(cfg, os.path.join(folder, name + ".md"), text)
    put_report(cfg, os.path.join(folder, name + "-full.txt"), "\n".join(full) + "\n")
    put_report(cfg, "LATEST.md", text)
    return path


def append_checks(cfg, d, now, lines, sections=(), what="", latest=False):
    """Add the alerts that rang (or a problem) to reports/<date>/checks.md, which keeps the footer at
    its end, and the tool output of that round to checks-full.txt. what: the job, when not a check.
    latest: this is now the newest report (a check round or the holiday look), so LATEST.md gets it too:
    a click on the message, and Claude's first read, must find the alert."""
    rel = os.path.join("reports", d.isoformat(), "checks.md")
    text = read_text(os.path.join(report_root(cfg), rel))
    if text.rstrip().endswith(FOOTER):
        text = text.rstrip()[:-len(FOOTER)].rstrip() + "\n\n"
    if not text.strip():
        text = "# Price checks: %s\n\nPSX prices here are about 5 minutes late.\n\n" % day_words(d)
    head = clock_label(now) if now.date() == d else when_words(now)
    text += "## %s%s\n\n%s\n\n%s\n" % (head, ", " + what if what else "", "\n".join("- " + x for x in lines), FOOTER)
    path = put_report(cfg, rel, text)
    if latest:
        top, rest = text.split("\n", 1)
        put_report(cfg, "LATEST.md", "%s\n\nThe newest report: today's price checks that rang or had a problem, "
                                     "oldest first. Today's other reports are in reports/%s/.\n%s"
                   % (top, d.isoformat(), rest))
    if sections:
        frel = os.path.join("reports", d.isoformat(), "checks-full.txt")
        old = read_text(os.path.join(report_root(cfg), frel))
        new = "\n".join(["# %s" % clock_label(now)] + [full_block(t, r) for t, r in sections])
        put_report(cfg, frel, old + new + "\n")
    return path


def last_words(state, job, now):
    x = (state.get("last") or {}).get(job)
    if not x:
        return "not yet"
    t = from_iso(x.get("at"))
    return "%s (%s)%s" % (when_words(t, now.date()) if t else x.get("at"), x.get("result", "?"),
                          (". " + x["note"]) if x.get("note") else "")


def tape_words(cfg):
    """How the price checks read prices, from the settings and the trading folder's psx/config.json."""
    if cfg["tape"]:
        return "on (day style): the price checks read the tape"
    if not folder_site_on(cfg["folders"].get("trading")):
        return "off. The PSX site is off in the trading folder, so the price checks read only saved files"
    return ("off. The price checks read the PSX site every %d minutes: the screener page, and the indices page "
            "while a swing share is held" % cfg["check_every_min"])


def rang_today(state, d, job=None):
    """How many alerts rang on day d (auto-run's own history), in one job ("check") or in all."""
    return len([h for h in state.get("history") or [] if str(h.get("at", ""))[:10] == d.isoformat()
                and (job is None or h.get("job") == job)])


def pause_words(state, now):
    p = state.get("pause") or {}
    bits = []
    if paused_all(state, now):
        until = from_iso(p.get("until"))
        bits.append("everything is paused " + ("until %s" % when_words(until, now.date()) if until
                                                else "until you run: autorun.py resume"))
    if checks_paused(state, now.date()):
        bits.append("price checks are off today")
    return "; ".join(bits) if bits else "no"


def write_status(cfg, state, now, plan, info=None):
    """STATUS.md: when each job last ran, open problems, and today's plan."""
    lines = ["# PSX auto-run status", "", "Updated %s Karachi time." % when_words(now, now.date()), "",
             "- Morning check: " + last_words(state, "morning", now),
             "- Price check: " + last_words(state, "check", now),
             "- After-close check: " + last_words(state, "after_close", now),
             "- Week report: " + last_words(state, "weekly", now),
             "- Paused: " + pause_words(state, now),
             "- Tape recorder: " + tape_words(cfg)]
    n = rang_today(state, now.date())
    if n:
        lines.append("- Alerts that rang today: %d (each one is in reports/%s/checks.md)" % (n, now.date().isoformat()))
    probs = [p for p in (state.get("problems") or {}).values() if p.get("told")]
    lines += ["- Problems now: " + ("; ".join(p["text"] for p in probs) if probs else "none"), ""]
    lines += ["## Today", ""] + ["- " + x for x in plan_lines(cfg, plan, now.date(), info)] + ["", FOOTER, ""]
    try:
        put_report(cfg, "STATUS.md", "\n".join(lines))
    except OSError:
        pass


TICK_MIN = 5         # launchd starts a tick at minute 0, 5, 10 ... 55 (launchd/local.psx-autorun.tick.plist)


def first_tick(t):
    """The first launchd tick at or after t: a check window that opens at 9:37 gets its first check at 9:40."""
    return t + minutes((-t.minute) % TICK_MIN)


def weekly_line(cfg, plan, d):
    """The week report's line in day d's plan, on the weekly day only."""
    if d.weekday() != int(cfg["weekly"]["weekday"]):
        return []
    at = datetime.datetime.combine(d, hm(cfg["weekly"]["time"]))
    wait = " (it waits until 15 minutes after the close)" if busy_hours(plan, at) else ""
    return ["%s  week report%s" % (clock_label(at), wait)]


def plan_lines(cfg, plan, d, info=None):
    if not plan:
        why = (info or {}).get("reason") or "PSX is closed"
        out = ["PSX is closed on %s (%s). No market jobs." % (day_words(d), why)]
        if expected_holiday(info):
            out.append("%s  one look at the PSX site, to see if PSX opened after all (only with the site on and "
                       "a share held)" % clock_label(PROBE_TIME))
        return out + weekly_line(cfg, plan, d)
    out = ["%s  morning check" % clock_label(plan["morning"])]
    for i, (a, b) in enumerate(plan["checks"]):
        out.append("%s to %s  price checks every %d minutes" % (clock_label(first_tick(a)), clock_label(b),
                                                               cfg["check_every_min"]))
        out.append("%s  last check of %s" % (clock_label(plan["finals"][i]),
                                            "the day" if i == len(plan["checks"]) - 1 else "this part of the day"))
    out.append("%s  after-close check (it waits for the closing file)" % clock_label(plan["after_close"]))
    if cfg["tape"]:
        out.append("%s to %s  the tape recorder (day style only)" % (clock_label(plan["tape"][0]),
                                                                     clock_label(plan["tape"][1])))
    return out + weekly_line(cfg, plan, d)


# ---------------------------------------------------------------- problems

def problem(state, notes, cfg, key, text, now, after=1):
    """Count a problem. Tell the user once it comes `after` times in a row, then at most once an
    hour. text may be a function of the count (the price checks say how long it has been)."""
    probs = state.setdefault("problems", {})
    p = probs.get(key) or {"count": 0, "first": iso(now), "told": None}
    last = from_iso(p.get("last"))
    if last is not None and last.date() != now.date():     # a new day starts a new count
        p["count"], p["first"] = 0, iso(now)
    p["count"] += 1
    p["last"] = iso(now)
    p["text"] = text(p["count"]) if callable(text) else text
    told = from_iso(p.get("told"))
    if p["count"] >= after and (told is None or now - told >= minutes(cfg["problem_repeat_min"])):
        notes.add("problems", "PSX auto-run: problem", p["text"], sound=True, rank=0)
        p["told"] = iso(now)
    probs[key] = p


def solved(state, notes, key, text):
    p = (state.get("problems") or {}).pop(key, None)
    if p and p.get("told"):
        notes.add("problems", "PSX auto-run: working again", text, rank=1)


def stale_words(out, every):
    """The D54 words of psx-alerts ("error: DEMO: no price newer than 15 minutes ...") as a problem
    text that counts the rounds, or None."""
    found = STALE_RE.findall(out or "")
    if not found:
        return None
    syms = []
    for sym, _ in found:
        if sym not in syms:
            syms.append(sym)
    what = "%s stop was" % syms[0] if len(syms) == 1 else "%s and %s stops were" % (", ".join(syms[:-1]), syms[-1])
    return lambda count: STALE_PROBLEM % (what, count * every, found[0][1])


def old_price(out, every):
    """A check round in the session whose prices were old (psx-alerts' "note: SYM: the data is N minutes
    old" or "... not today, so no alert used it"): the rules of those shares saw no new price. This
    happens when another PSX tool runs (an entry watch saves no market prices), or with the site off.
    -> (why, words for problem()) or ("", None)."""
    old = OLD_RE.findall(out or "")
    late = NOT_TODAY_RE.findall(out or "")
    syms = list(dict.fromkeys([s for s, _ in old] + late))
    if not syms:
        return "", None
    reason = ("the newest one is %s minutes old" % old[0][1]) if old else "the newest one is not from today"
    what = "%s alerts" % names(syms)
    why = "no new price for %s (%s)" % (names(syms), reason)
    return why, lambda count: OLD_PROBLEM % (what, count * every, reason)


# ---------------------------------------------------------------- facts for the reports (read only)

def shares_words(n):
    n = float(n)
    return "{:,}".format(int(n)) if n.is_integer() else "{:,}".format(n)


def date_words(text):
    try:
        return day_label(datetime.date.fromisoformat(str(text)))
    except ValueError:
        return str(text)


def gap_words(p, tcfg):
    """The gap loss of a held swing position: the loss if the next session opens at the lower limit of
    its buy price, costs included (gap_loss, D55). "" when the ledger has no cost."""
    try:
        cost = sum(Decimal(str(lot["cost"])) for lot in p.get("lots") or [])
        price = float((cost / Decimal(str(p["shares"]))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if price <= 0:
            return ""
        loss = gap_loss(price, p["shares"], trade_costs(tcfg))
        low = price_limits(price)[0]
    except (KeyError, TypeError, ValueError, InvalidOperation, ArithmeticError):
        return ""
    return (" Gap loss: about %s, costs included, if it opens at %.2f (the lower limit from the buy price %.2f)."
            % (money(loss), low, price))


def position_lines(folder):
    """The shares held in the trading folder (psx/portfolio.json): each swing position with its plan
    stop, target, exit-by day and gap loss. -> (lines, how many swing positions)."""
    tcfg = folder_config(folder)
    out, swing = [], 0
    for p in positions(folder):
        pl = p.get("plan") if isinstance(p.get("plan"), dict) else {}
        if pl.get("style") != "swing":
            out.append("You hold %s %s." % (shares_words(p["shares"]), p["ticker"]))
            continue
        swing += 1
        bits = [("%s %.2f" if isinstance(pl[k], (int, float)) else "%s %s") % (word, pl[k])
                for k, word in (("stop", "Plan stop"), ("target", "target")) if pl.get(k) is not None]
        if pl.get("exit_by"):
            bits.append("exit by " + date_words(pl["exit_by"]))
        plan = (" " + ", ".join(bits) + ".") if bits else ""
        out.append("%s: you hold %s shares (swing).%s%s" % (p["ticker"], shares_words(p["shares"]), plan,
                                                            gap_words(p, tcfg)))
    return out, swing


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def share_count(x):
    """A share count from a record: a number above 0, else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 < x < float("inf"):
        return None
    return x


def buy_book(folder):
    """Each buy in <folder>/psx/state/broker.json whose newest record is approved or placed, read only.
    -> [(symbol, status, day, fp, filled, qty)]. filled: the shares in the "filled" buy records of its
    symbol on or after its day (0 with none; None when one has no share count: then any fill closes the
    order, the old rule). qty: the newest share count in its records, or None. A fill names no order, so
    a fill of another buy of the same share counts too (as in psx-risk and psx-broker)."""
    doc = load_json(folder_file(folder, "state", "broker.json"), {}) if folder else {}
    recs = [r for r in doc.get("orders") or [] if isinstance(r, dict)]
    newest, qty, order = {}, {}, []
    for r in recs:
        fp = r.get("fingerprint")
        if not fp or r.get("status") == "filled":
            continue
        if fp not in newest:
            order.append(fp)
        newest[fp] = r
        if share_count(r.get("qty")):
            qty[fp] = share_count(r.get("qty"))
    fills = [(str(r.get("symbol") or "").upper(), rec_day(r), share_count(r.get("qty"))) for r in recs
             if r.get("status") == "filled" and str(r.get("side") or "").lower() == "buy"]
    out = []
    for fp in order:
        r = newest[fp]
        if str(r.get("side") or "").lower() != "buy" or r.get("status") not in ("approved", "placed"):
            continue
        sym, day = str(r.get("symbol") or "").upper(), rec_day(r)
        mine = [n for s, d, n in fills if s == sym and d >= day]
        out.append((sym, r["status"], day, fp, None if None in mine else sum(mine), qty.get(fp)))
    return out


def open_buys(folder):
    """Buys in <folder>/psx/state/broker.json approved or placed, with no fill on or after that day and
    no newer expired, skipped, cancelled or failed record. Read only. -> [(symbol, status, day, fp)]."""
    return [(sym, status, day, fp) for sym, status, day, fp, filled, _ in buy_book(folder) if filled == 0]


def part_buys(folder, day):
    """The buys of `day` (an ISO date) filled in part: fewer shares recorded than the order's qty. A PSX
    order ends at the close, so psx-risk counts the rest only on that day, and they are told only for that
    day. -> [(symbol, status, fp, filled, qty)]."""
    return [(sym, status, fp, filled, qty) for sym, status, d, fp, filled, qty in buy_book(folder)
            if d == day and filled and qty and filled < qty]


def open_buy_lines(cfg):
    """The after-close lines for buys with no fill recorded (D33), both folders.
    -> [(line, who, day)]. "New swing buys wait until then." only where psx-risk blocks them."""
    out = []
    for name in ("trading", "longterm"):
        folder = cfg["folders"].get(name)
        swing = name == "trading" and folder_config(folder).get("style") == "swing"
        for sym, status, day, fp in open_buys(folder):
            who = sym if name == "trading" else "%s (long-term folder)" % sym
            out.append((OPEN_BUY % (who, status, date_words(day), fp) + (SWING_WAIT if swing else ""), who, day))
    return out


def part_buy_lines(cfg, d):
    """The after-close lines for the buys of day d filled in part, both folders: the rest may have filled
    too (record it), or it ended at the close with no fill (record that, or leave it: psx-risk counts it
    only on day d)."""
    out = []
    for name in ("trading", "longterm"):
        for sym, status, fp, filled, qty in part_buys(cfg["folders"].get(name), d.isoformat()):
            who = sym if name == "trading" else "%s (long-term folder)" % sym
            out.append(PART_BUY % (who, status, date_words(d.isoformat()), shares_words(filled), shares_words(qty),
                                   shares_words(qty - filled), fp))
    return out


def cash_line(cfg):
    """The week report's cash check (D57): both ledgers and their sum."""
    parts, total, n, fixes = [], Decimal(0), 0, []
    for name in ("trading", "longterm"):
        folder = cfg["folders"].get(name)
        if not folder:
            continue
        doc, bad = read_json(folder_file(folder, "portfolio.json"), {})
        cash = doc.get("cash")
        if bad:
            parts.append("%s not read (psx/portfolio.json %s)" % (FOLDER_WORDS[name], bad))
        elif isinstance(cash, (int, float)) and not isinstance(cash, bool):
            parts.append("%s %s" % (FOLDER_WORDS[name], money(cash)))
            total += Decimal(str(cash))
            n += 1
        else:
            parts.append("%s not recorded (no cash in psx/portfolio.json)" % FOLDER_WORDS[name])
        fixes.append(CASH_FIX[name])
    if n == 2:
        parts.append("together %s" % money(total))
    return "Cash: %s. %s" % (", ".join(parts), CASH_ASK % " or ".join(fixes))


def calendar_mismatch(cfg):
    """A plain note when the folders' psx/config.json files list different holidays, open days or
    Ramadan dates (the clock reads only one of them), else ""."""
    cals = set(json.dumps(dict((k, folder_config(f).get(k) or None) for k in ("holidays", "open_days", "ramadan")),
                          sort_keys=True) for f in cfg["folders"].values()
               if f and not file_error(f, "config.json"))     # a missing key = an empty one; a broken file is told
    if len(cals) <= 1:
        return ""
    return ("the psx/config.json files list different holidays or Ramadan dates. The clock uses the %s "
            "folder's file. Make them the same." % FOLDER_WORDS[cfg["calendar_folder"]])


def closing_file(cfg, d):
    base = folder_file(cfg["folders"]["trading"], "data", "mkt_summary", d.isoformat())
    return any(os.path.exists(base + ext) for ext in (".Z", ".lis", ".zip"))


def week_lines(cfg, state, now):
    """The week in short, the PSX days of the next 7 days, and the cash check."""
    since = now - datetime.timedelta(days=7)
    rang = [h for h in state.get("history") or [] if (from_iso(h.get("at")) or now) >= since]
    out = ["Alerts that auto-run sent this week: %d. An alert from a check you ran yourself, or in the chat, "
           "is not counted here." % len(rang)]
    closed, odd = [], []
    for i in range(1, 8):
        x = now.date() + datetime.timedelta(days=i)
        info = day_info(cfg, state, x)
        if not info["segments"] and x.weekday() < 5:
            closed.append("%s (%s)" % (day_label(x), info["reason"]))
        elif info["segments"] and info.get("session") in ("ramadan", "ramadan-friday"):
            odd.append(day_label(x))
        if not info.get("calendar_known", True):
            out.append("The PSX holiday list for %d is not in the tools. Add closures under \"holidays\" in "
                       "psx/config.json when PSX announces them." % x.year)
            break
    out.append("Weekdays with PSX closed in the next 7 days: %s." % ("; ".join(closed) if closed else "none"))
    if odd:
        out.append("Ramadan hours on: %s." % "; ".join(odd))
    out.append(cash_line(cfg))
    return out


def holiday_words(day, name, now):
    """The evening-before words for an expected moon holiday (4.2)."""
    when = "Tomorrow, %s," % day_label(day) if day == now.date() + datetime.timedelta(days=1) else day_label(day)
    return HOLIDAY_AHEAD % (when, name)


def ring_lines(out):
    return [x[6:].strip() for x in (out or "").splitlines() if x.startswith("ALERT ")]


def keep_history(state, now, lines, job=""):
    state.setdefault("history", []).extend({"at": iso(now), "text": x, "job": job} for x in lines)
    state["history"] = state["history"][-300:]


def run_steps(cfg, steps, site_busy, today, stop_after=None):
    """Run a job's steps in order. The trading folder's alerts step goes first; a "tape" step after it
    reads that step's new tape line (--offline) only when it really added one (4.8): the tape of
    `today` (the date the tools see now). stop_after(s, res) may end the job early (True).
    -> [(step, result)]."""
    results, tape_new, shared = [], False, shared_tape(cfg)
    for s in steps:
        path = mark = began = None
        if s["kind"] == "alerts" and s["folder"] == "trading" and cfg["folders"].get("trading"):
            path = tape_file(cfg["folders"]["trading"], today)
            mark, began = tape_mark(path), time.time()
        res = run_step(cfg, s, site_busy, tape_new and shared)
        if path is not None:
            tape_new = tape_grew(path, mark, began)
        results.append((s, res))
        if stop_after and stop_after(s, res):
            break
    return results


def buys_message(items):
    """One message for every buy with no fill recorded."""
    if len(items) == 1:
        return items[0][0]
    names = ", ".join("%s (%s)" % (who, date_words(day)) for _, who, day in items)
    return ("%d buys have no fill recorded: %s. Record each fill (ticket.py fill), or record it expired "
            "(ticket.py result --fingerprint FP --status expired). The after-close report has each line."
            % (len(items), names))


def run_job(cfg, state, notes, job, d, note, now):
    """Run one job's steps, write its report, add its messages."""
    if job == "check":
        return run_checks(cfg, state, notes, d, note, now)
    if job == "probe":
        return run_probe(cfg, state, notes, d, now)
    plan = day_plan(cfg, day_info(cfg, state, d), d)
    busy = other_pollers()
    trading = cfg["folders"]["trading"]

    def wait_for_file(s, res):
        # the after-close job waits for the day's closing file (about 30 to 40 minutes after the close);
        # nothing else runs yet, so no news item is marked as seen and then lost
        if job != "after_close" or s["id"] != "scan" or closing_file(cfg, d):
            return False
        return now < (plan["give_up"] if plan else now) and note not in ("late", "hand")

    results = run_steps(cfg, cfg["jobs"].get(job) or [], bool(busy), now.date(), wait_for_file)
    if results and wait_for_file(*results[-1]):
        nxt = now + minutes(cfg["after_close_retry_min"])
        state.setdefault("waiting", {})[d.isoformat()] = iso(nxt)
        log("after_close %s: the closing file is not out yet; next try %s" % (d, clock_label(nxt)))
        return
    reports_writable(cfg)                     # a blocked reports folder is known before the counts are made
    short, sections, top, rang = [], [], [], []
    for s, r in results:
        rang += ring_lines(r["out"])
        if r["silent"]:
            continue
        short.append(summarize(s, r, cfg["folders"].get(s["folder"]) or "", now.date()))
        if r["status"] != "skipped":
            sections.append((s["title"], r))
    failed = [(s, r) for s, r in results if r["status"] == "failed"]
    told = ["PROBLEM: " + w for w in file_problems(cfg, state, notes, now)]     # problems that are not a step
    early = job == "after_close" and not closing_file(cfg, d) and note == "hand" and bool(plan) \
        and now < plan["give_up"]
    if early:
        short.insert(0, NOT_YET % day_label(d))   # by hand, early: not a problem yet, and the real job still runs
    elif job == "after_close" and not closing_file(cfg, d):
        told.append(NOT_FOUND % day_label(d))
        problem(state, notes, cfg, "closing:%s" % d.isoformat(), NOT_FOUND % day_label(d), now)
    if cfg.get("_fallback"):
        told.append(FALLBACK_WORDS)
        problem(state, notes, cfg, "reports", FALLBACK_WORDS, now)
    else:
        solved(state, notes, "reports", "The reports go to your reports folder again.")
    short[:0] = told
    for x in rang:            # alerts on the close: the swing stop, target, trend and time rules
        notes.add("alerts", "PSX alert", x, sound=True, rank=0)
    if rang:
        append_checks(cfg, d, now, rang, what=JOB_TITLES.get(job, job).lower())
        keep_history(state, now, rang, job)
    if any(HALT_RE.search(r["out"]) for s, r in results if s["kind"] == "news"):
        top.append(HALT_WORDS)
        notes.add("alerts", "PSX: a market halt is in the news", HALT_WORDS.split(". ", 1)[1], sound=True, rank=0)
    if job == "morning" and plan:
        top.append("PSX is open today: %s." % ", then ".join("%s to %s" % (clock_label(a), clock_label(b))
                                                            for a, b in plan["segments"]))
    paper = folder_config(trading).get("paper")
    if job in ("morning", "after_close"):
        held, swing = position_lines(trading)
        top += held
        if job == "morning" and swing:
            top.append(STOP_LIMIT_LIVE if paper is False else STOP_LIMIT_PAPER)
    if busy:
        top.append("Another PSX tool was using the PSX site, so the PSX pages were not read again.")
    if job in ("morning", "after_close") and checks_paused(state, d):
        top.append("Price checks are off today (autorun.py pause checks): auto-run checks no alert and no stop in "
                   "the session. They come back tomorrow, or with: autorun.py resume." if job == "morning" else
                   "Price checks were off today (autorun.py pause checks): auto-run checked no alert and no stop "
                   "in the session.")
    info = day_info(cfg, state, d)
    if info.get("calendar_note"):
        top.append("Calendar: " + info["calendar_note"])
    if job in ("morning", "weekly") and calendar_mismatch(cfg):
        top.append("Note: " + calendar_mismatch(cfg))
    for name in ("trading", "longterm"):
        f = cfg["folders"].get(name)
        if f and not folder_site_on(f) and not file_error(f, "config.json"):
            top.append("The PSX site is off in the %s folder (\"psx_site\": false): only saved files were read."
                       % FOLDER_WORDS[name])
    if job in ("morning", "after_close"):
        top.append(PAPER_LINE % ("OFF" if paper is False else "on"))
    if job == "weekly":
        top += week_lines(cfg, state, now)
    if job == "after_close":
        items = open_buy_lines(cfg)
        top += [line for line, _, _ in items] + part_buy_lines(cfg, d)
        if items:
            notes.add("alerts", "PSX: a buy has no fill recorded", buys_message(items), rank=1)
        day, name = holiday_ahead(cfg, state, d)
        if day is not None and day > now.date():
            words = holiday_words(day, name, now)
            top.append(words)
            tomorrow = day == now.date() + datetime.timedelta(days=1)
            notes.add("alerts", "PSX: tomorrow may be a holiday" if tomorrow else
                      "PSX: %s may be a holiday" % day_label(day), words, rank=1)
    late_note = ""
    at = clock_label(now) if now.date() == d else when_words(now)
    if note == "late":
        late_note = "This ran late, at %s. The Mac was asleep or off at the planned time." % at
    elif note == "hand":
        late_note = "This run was started by hand, at %s." % at
    checked = rang_today(state, d, "check")
    if job == "after_close" and checked:
        short.append("Alerts that rang today in the price checks: %d (each one is in reports/%s/checks.md)."
                     % (checked, d.isoformat()))
    n = len(failed) + len(told)
    short.append("Problems: %s." % ("none" if not n else "%d (each one is in a line above)" % n))
    path = write_report(cfg, job, d, now, short, sections, late_note, top)
    for s, r in results:            # what a tool asks to be told now ("notify: ..." lines)
        for x in r["out"].splitlines():
            if x.lower().startswith("notify: "):
                notes.add("alerts", "PSX " + ("signal" if s["folder"] == "longterm" else "swing"), x[8:].strip())
    head = {"morning": "Morning check done", "after_close": "After-close check done",
            "weekly": "Week report ready"}.get(job, JOB_TITLES.get(job, job))
    notes.add(job, "PSX: " + head, notify_text(job, results, failed, len(told)), rank=2)
    for s, r in failed:
        problem(state, notes, cfg, "%s:%s" % (job, s["id"]), "%s: %s" % (s["title"], r["why"]), now)
    for s, r in results:
        if r["status"] in ("ok", "skipped") and not r["silent"]:
            solved(state, notes, "%s:%s" % (job, s["id"]), "%s works again." % s["title"])
    state.setdefault("last", {})[job] = {"at": iso(now), "result": "ok" if not n else "problems", "report": path}
    if early:                 # an early run by hand does not stand in for the day's after-close job
        state["last"][job]["note"] = "By hand, before the closing file: the after-close check still runs by itself"
        return
    mark(state, job, d, iso(now))
    (state.get("waiting") or {}).pop(d.isoformat(), None)


def run_checks(cfg, state, notes, d, note, now):
    """One round of price checks: the trading folder first, then the long-term folder (4.3, 4.8). A last
    check (note "final0", "final1") runs after a part of the day: PSX is closed then, so it can fail but
    never proves that a problem is fixed."""
    busy, final = bool(other_pollers()), note.startswith("final")
    results = run_steps(cfg, cfg["jobs"].get("check") or [], busy, now.date())
    rang, probs, sections = [], [], []
    for s, res in results:
        lines = ring_lines(res["out"])
        for x in lines:
            notes.add("alerts", "PSX alert", x, sound=True, rank=0)
        rang += lines
        key, words = "check:%s" % s["id"], None
        if res["status"] == "ok" and s["kind"] == "alerts" and s["folder"] == "trading" and not final:
            why, words = old_price(res["out"], cfg["check_every_min"])
            if why:                  # the trading folder's rules saw no new price in the session (D44, D54)
                res.update(status="failed", why=why)
        if res["status"] == "failed":
            words = words or stale_words(res["out"], cfg["check_every_min"]) or \
                "Price checks: %s: %s" % (s["title"], res["why"])
            probs.append("%s: %s" % (s["title"], res["why"]))
            problem(state, notes, cfg, key, words, now, after=cfg["problem_after_fails"])
        elif not res["silent"] and not final:
            solved(state, notes, key, "Price checks work again (%s)." % s["title"])
        if lines or res["status"] == "failed":
            sections.append((s["title"], res))
    file_problems(cfg, state, notes, now)
    if rang or probs:                         # only then does a round write a report
        reports_writable(cfg)
        append_checks(cfg, d, now, rang + ["PROBLEM: " + p for p in probs] + ([BUSY_WORDS] if busy else []),
                      sections, latest=True)
        keep_history(state, now, rang, "check")
        if not cfg.get("_fallback"):
            solved(state, notes, "reports", "The reports go to your reports folder again.")
    if cfg.get("_fallback"):
        problem(state, notes, cfg, "reports", FALLBACK_WORDS, now)
    state["last_check"] = iso(now)
    if final:
        mark(state, note, d, iso(now))
    state.setdefault("last", {})["check"] = {"at": iso(now), "result": "problems" if probs else "ok"}
    if busy:
        state["last"]["check"]["note"] = BUSY_WORDS.replace("this check", "it")


def quote_is_today(out, sym, d):
    """True when alerts.py quote --json gave sym a price whose data time is on day d (Karachi)."""
    try:
        doc = json.loads(out or "")
        t = iso_epoch(((doc.get("quotes") or {}).get(sym) or {}).get("time"))
    except (ValueError, AttributeError):
        return False
    return t is not None and datetime.datetime.fromtimestamp(t, KARACHI).date() == d


def run_probe(cfg, state, notes, d, now):
    """An expected moon holiday: the clock says closed, but PSX may open. One look (4.2): one quote of a
    held share, in the trading folder, with the site on. Auto-run still checks nothing against the
    clock (D45): you add the day under "open_days", and the next tick sees it."""
    trading = cfg["folders"]["trading"]
    held = holdings(trading) or holdings(cfg["folders"].get("longterm"))
    why = ""
    if not folder_site_on(trading):
        why = "the PSX site is off in the trading folder"
    elif not held:
        why = "no share is held in either folder"
    if why:
        log("probe %s: no look (%s)" % (d, why))
        mark(state, "probe", d, iso(now))
        state.setdefault("last", {})["probe"] = {"at": iso(now), "result": "no look: " + why}
        return
    s = step("probe", "trading", "psx-alerts/alerts.py", ["quote", held[0], "--source", PROBE_SOURCE, "--json"],
             kind="probe", timeout=120, title="A look at the PSX site (an expected holiday)")
    res = run_step(cfg, s, bool(other_pollers()))
    opened = quote_is_today(res["out"], held[0], d)
    if opened:
        text = LOOKS_OPEN % day_label(d)
        notes.add("alerts", "PSX looks open today", text, sound=True, rank=0)
        reports_writable(cfg)
        append_checks(cfg, d, now, [text], [(s["title"], res)], what="the holiday look", latest=True)
        keep_history(state, now, [text], "probe")
    mark(state, "probe", d, iso(now))
    state.setdefault("last", {})["probe"] = {"at": iso(now), "result": "open" if opened else res["status"]}


# ---------------------------------------------------------------- sleep, the tape, the install probe

def keep_awake(cfg, plan, now):
    """No idle sleep from 5 minutes before the morning job to 20 minutes after the after-close time
    (a shut lid still sleeps). Each tick asks for 400 seconds more."""
    if cfg["keep_awake"] and plan and plan["awake"][0] <= now < plan["awake"][1]:
        start_caffeinate(400)


def tape_wanted(cfg, state, plan, now):
    """The tape recorder runs only with "tape": true, in day style, from the pre-open to the close
    plus 10 minutes, and at most tape_max_restarts times a day."""
    if not cfg["tape"] or not plan or checks_paused(state, now.date()):
        return False
    try:
        if trade_style(folder_config(cfg["folders"]["trading"])) != "day":
            return False
    except ValueError:
        return False
    tape = load_own(os.path.join(state_dir(), "tape.json"), {}).get(now.date().isoformat()) or {}
    return plan["tape"][0] <= now < plan["tape"][1] and not tape.get("done") \
        and tape.get("fails", 0) < cfg["tape_max_restarts"]


def start_tape(cfg, state, notes, now):
    if Lock("tape.lock").held_by_other():         # auto-run's own tape runs now
        solved(state, notes, "tape:start", "The tape recorder runs again.")
        return
    if other_pollers():
        return
    try:
        kickstart(LABEL_TAPE)
        log("asked launchd to start the tape")
    except (OSError, subprocess.SubprocessError) as e:
        problem(state, notes, cfg, "tape:start", "The tape recorder did not start: %s" % e, now)
        return
    solved(state, notes, "tape:start", "The tape recorder starts again.")


def answer_probe(cfg, now):
    """install.sh step 7b: a tick that finds state/probe.request writes <reports>/autorun-probe.txt
    (inside Documents, so it needs the grant), deletes the request, and does nothing else.
    -> True when there was a request."""
    path = probe_request_path()
    if not os.path.isfile(path):
        return False
    token = read_text(path).strip()
    out = os.path.join(cfg["reports"] or report_root(cfg), "autorun-probe.txt")
    try:
        write_text(out, "PSX auto-run probe: %s\nThe scheduled run wrote this file at %s, Karachi time.\n"
                   % (token or "no token", iso(now)))
        log("probe: wrote %s" % out)
    except OSError as e:
        log("probe: could not write %s: %s" % (out, e))
    try:
        os.remove(path)
    except OSError:
        pass
    return True


# ---------------------------------------------------------------- commands

def longterm_gone(cfg):
    """The long-term folder is set but its psx/config.json is gone: its steps are skipped for now (a
    problem, told), and the trading jobs still run. -> the words, or ""."""
    folder = cfg["folders"].get("longterm")
    if not folder or cfg["calendar_folder"] == "longterm":
        return ""
    try:
        if os.path.isfile(os.path.join(folder, "psx", "config.json")):
            return ""
    except OSError:
        return ""
    return "no psx/config.json in the long-term folder (%s): its steps are skipped until it is back" % folder


def tell_trouble(a, state, notes, cfg, key, text, now, path):
    """A tick that cannot run: a problem message (at once, then at most once an hour)."""
    if getattr(a, "dry_run", False):
        print("problem: " + text)
        return 1
    problem(state, notes, cfg, key, text, now)
    save_json(path, state)
    return 0


def cmd_tick(a, cfg, notes):
    """launchd calls this every 5 minutes (through the app): run whatever is due now."""
    lock = Lock("run.lock")
    if not lock.acquire():
        if a.dry_run:
            print("Another auto-run job is running now. Try again in a few minutes.")
        log("tick: another run is still going")
        return 0
    try:
        now = now_karachi(a.now)
        if not a.dry_run and answer_probe(cfg, now):
            return 0                      # install.sh's probe (step 7b): nothing else this tick
        path = state_path()
        state = load_own(path, {})        # a dry run changes it in memory only: it is never saved
        state.setdefault("installed", iso(now))
        state["heartbeat"] = iso(now)
        if end_pause(state, now) and not a.dry_run:
            log("the pause ended by itself")
        old = (now.date() - datetime.timedelta(days=7)).isoformat()
        for k in [k for k in state.get("waiting") or {} if k < old]:
            del state["waiting"][k]         # an after-close retry of a day long gone
        trouble = probe_access(cfg)
        if trouble:
            return tell_trouble(a, state, notes, cfg, "access", trouble, now, path)
        solved(state, notes, "access", "PSX Autorun can read your folders again.")
        trouble = "; ".join(check_places(cfg_without_gone(cfg, state, notes, now, a)))
        if trouble:
            return tell_trouble(a, state, notes, cfg, "places", trouble, now, path)
        solved(state, notes, "places", "The PSX tools and folders are found again.")
        d = now.date()
        try:
            info = day_info(cfg, state, d)
            prev, prev_info = last_trading_day(cfg, state, d)
        except AutorunError as e:
            return tell_trouble(a, state, notes, cfg, "clock", str(e), now, path)
        solved(state, notes, "clock", "The PSX clock works again.")
        if not a.dry_run:
            closing_solved(cfg, state, notes)
        plan = day_plan(cfg, info, d)
        jobs = due(cfg, state, now, info, prev, prev_info)
        if a.dry_run:
            for job, day, note in jobs:
                print("due: %s for %s%s" % (job, day.isoformat(), " (%s)" % note if note else ""))
            if not jobs:
                print("Nothing is due now (%s, Karachi time)." % when_words(now, now.date()))
            return 0
        keep_awake(cfg, plan, now)
        if tape_wanted(cfg, state, plan, now):
            start_tape(cfg, state, notes, now)
        for job, day, note in jobs:
            log("run %s for %s %s" % (job, day, note))
            try:
                run_job(cfg, state, notes, job, day, note, now_karachi(a.now))
            except AutorunError as e:
                problem(state, notes, cfg, "job:%s" % job, "%s: %s" % (JOB_TITLES.get(job, job), e), now)
            else:
                solved(state, notes, "job:%s" % job, "%s works again." % JOB_TITLES.get(job, job))
            save_json(path, state)
        if jobs:
            write_status(cfg, state, now_karachi(a.now), plan, info)
        save_json(path, state)
        return 0
    finally:
        lock.release()


def closing_solved(cfg, state, notes):
    """A told "closing file not found" problem ends when the file is there, or when the clock has the
    day as closed (you added it under "holidays")."""
    for key in [k for k in state.get("problems") or {} if k.startswith("closing:")]:
        try:
            day = datetime.date.fromisoformat(key.split(":", 1)[1])
            if closing_file(cfg, day):
                words = "The closing file of %s is there now." % day_label(day)
            elif not day_info(cfg, state, day)["segments"]:
                words = "%s is a PSX holiday in psx/config.json now." % day_label(day)
            else:
                continue
        except (ValueError, AutorunError):
            continue
        solved(state, notes, key, words)


def cfg_without_gone(cfg, state, notes, now, a):
    """cfg itself; a long-term folder that is gone is taken out for this run (and told once an hour)."""
    gone = longterm_gone(cfg)
    if gone:
        cfg["folders"]["longterm"] = ""
        if not getattr(a, "dry_run", False):
            problem(state, notes, cfg, "places:longterm", gone, now)
        else:
            print("problem: " + gone)
    else:
        solved(state, notes, "places:longterm", "The long-term folder is found again.")
    return cfg


def by_date(text, now):
    if not text:
        return now.date()
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise AutorunError("--date wants YYYY-MM-DD, like 2026-11-06")


def cmd_run(a, cfg, notes):
    """Run one job now, by hand: autorun.py run morning (it runs even while paused)."""
    lock = Lock("run.lock")
    if not lock.acquire():
        print("Another auto-run job is still going. Try again in a few minutes.")
        return 1
    try:
        path = state_path()
        state = load_own(path, {})
        now = now_karachi(a.now)
        trouble = probe_access(cfg) or "; ".join(check_places(cfg_without_gone(cfg, state, notes, now, a)))
        if trouble:
            raise AutorunError(trouble)
        d = by_date(a.date, now)
        if a.job == "after_close" and not day_plan(cfg, day_info(cfg, state, d), d):
            print("PSX was closed on %s: no after-close check." % day_label(d))
            return 1
        run_job(cfg, state, notes, a.job, d, "hand", now)
        write_status(cfg, state, now, day_plan(cfg, day_info(cfg, state, now.date()), now.date()))
        save_json(path, state)
        print("Done. The report is in %s" % os.path.join(report_root(cfg), "LATEST.md"))
        return 0
    finally:
        lock.release()


def cmd_tape(a, cfg, notes):
    """The tape recorder for today (launchd starts it when the tick asks). It waits for the pre-open,
    keeps the Mac awake, and runs the scanner's own recorder until the close."""
    if not cfg["tape"]:
        print('The tape is off ("tape": true in autorun.json turns it on).')
        return 0
    try:
        style = trade_style(folder_config(cfg["folders"]["trading"]))
    except ValueError as e:
        raise AutorunError(str(e))
    if style != "day":
        print("The tape is for the day style. The trading folder's style is swing, so it does not run.")
        return 0
    lock = Lock("tape.lock")
    if not lock.acquire():
        return 0
    try:
        tpath = os.path.join(state_dir(), "tape.json")
        d = now_karachi().date()
        plan = day_plan(cfg, day_info(cfg, load_own(state_path(), {}), d), d)
        if not plan or now_karachi() >= plan["tape"][1]:
            return 0
        while now_karachi() < plan["tape"][0]:
            time.sleep(30)
        if other_pollers():
            log("tape: another PSX tool is polling the site; not starting")
            return 0
        os.makedirs(log_dir(), exist_ok=True)
        out_path = os.path.join(log_dir(), "tape-%s.log" % d.isoformat())
        argv = [cfg["python"], os.path.join(cfg["pack"], "psx-scanner", "scan.py"), "tape", "--quiet"]
        with open(out_path, "a", encoding="utf-8") as out:
            p = subprocess.Popen(argv, cwd=cfg["folders"]["trading"], stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=subprocess.STDOUT, env=tool_env())
            awake = caffeinate_while(p.pid) if cfg["keep_awake"] else None
            rc = p.wait()
            if awake is not None:
                awake.wait()
        tail = read_text(out_path)[-2000:]
        tapes = load_own(tpath, {})
        t = tapes.get(d.isoformat()) or {"fails": 0}
        if "The tape has seen the" in tail:
            t["done"] = True
        elif rc != 0:
            t["fails"] = t.get("fails", 0) + 1
            if t["fails"] >= cfg["tape_max_restarts"]:
                notes.add("problems", "PSX auto-run: problem", "The tape recorder stopped %d times today: %s"
                          % (t["fails"], first_line(tail[-300:])), sound=True, rank=0)
        tapes[d.isoformat()] = t
        for old in sorted(tapes)[:-14]:
            del tapes[old]
        save_json(tpath, tapes)
        return 0
    finally:
        lock.release()


def cmd_selftest(a, cfg, notes):
    """Checks for install.sh: Python, the folders, macOS access, the clock, a test file in the reports
    folder (the real one: no fallback here)."""
    ok, lines = True, []
    try:
        v = subprocess.run([cfg["python"], "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=tool_env(), timeout=60)
        lines.append("Python %s at %s" % (v.stdout.decode("utf-8", "replace").strip(), cfg["python"]))
    except (OSError, subprocess.SubprocessError) as e:
        raise AutorunError("Python did not start at %s (%s): run install.sh again" % (cfg["python"], e))
    blocked = probe_access(cfg)
    if blocked:
        ok = False
        lines.append("PROBLEM: " + blocked)
    for p in check_places(cfg) if not blocked else []:
        ok = False
        lines.append("PROBLEM: " + p)
    if ok:
        now = now_karachi()
        try:
            info = day_info(cfg, {}, now.date())
            plan = day_plan(cfg, info, now.date())
            lines.append("The PSX clock works. Today: " + "; ".join(plan_lines(cfg, plan, now.date(), info)))
        except AutorunError as e:
            ok = False
            lines.append("PROBLEM: %s" % e)
        if calendar_mismatch(cfg):
            lines.append("Note: " + calendar_mismatch(cfg))
        try:
            test = os.path.join(cfg["reports"], ".write-test")
            write_text(test, "ok\n")
            os.remove(test)
            lines.append("Reports go to %s" % cfg["reports"])
        except OSError as e:
            ok = False
            lines.append("PROBLEM: " + (BLOCKED if isinstance(e, PermissionError) else
                                        "cannot write the reports folder (%s)" % e))
    write_text(os.path.join(home_dir(), "selftest.txt"), ("OK\n" if ok else "PROBLEM\n") + "\n".join(lines) + "\n")
    if ok:
        notes.add("problems", "PSX auto-run works", "You will get messages like this one. Nothing is ever bought "
                  "or sold for you.", rank=0)
    else:
        notes.add("problems", "PSX auto-run: problem", [x for x in lines if x.startswith("PROBLEM")][0][9:],
                  sound=True, rank=0)
    print("\n".join(lines))
    return 0 if ok else 1


def cmd_status(a, cfg, notes):
    state = load_own(state_path(), {})
    now = now_karachi(a.now)
    print("PSX auto-run %s. Karachi time now: %s." % (VERSION, when_words(now, now.date())))
    for job in ("morning", "check", "after_close", "weekly"):
        print("- %s: %s" % (JOB_TITLES[job], last_words(state, job, now)))
    print("- Paused: %s" % pause_words(state, now))
    try:
        info = day_info(cfg, state, now.date())
        for x in plan_lines(cfg, day_plan(cfg, info, now.date()), now.date(), info):
            print("  " + x)
    except AutorunError as e:
        print("  problem: %s" % e)
    probs = [p["text"] for p in (state.get("problems") or {}).values()]
    print("Problems: %s" % ("; ".join(probs) if probs else "none"))
    return 0


def cmd_plan(a, cfg, notes):
    now = now_karachi(a.now)
    d = by_date(a.date, now)
    info = day_info(cfg, {}, d)
    print("PSX auto-run plan for %s (Karachi time):" % day_words(d))
    for x in plan_lines(cfg, day_plan(cfg, info, d), d, info):
        print("- " + x)
    if info.get("calendar_note"):
        print("Calendar: " + info["calendar_note"])
    paused = pause_words(load_own(state_path(), {}), now)
    if paused != "no":
        print("Paused now: " + paused)
    return 0


def wait_lock(lock, most=900):
    """Wait for auto-run's lock (a job may be running), at most `most` seconds."""
    t0 = time.time()
    while not lock.acquire():
        if time.time() - t0 > most:
            raise AutorunError("auto-run is busy with a job. Try again in a few minutes.")
        time.sleep(2)


def cmd_pause(a, cfg, notes):
    if a.minutes is not None and a.what != "all":
        raise AutorunError("--minutes goes with pause all, like: autorun.py pause all --minutes 10")
    if a.minutes is not None and not 1 <= a.minutes <= 1440:
        raise AutorunError("--minutes wants a whole number from 1 to 1440")
    lock = Lock("run.lock")
    wait_lock(lock)
    try:
        state = load_own(state_path(), {})
        now = now_karachi(a.now)
        p = state.setdefault("pause", {})
        if a.what == "checks":
            p["checks_off"] = now.date().isoformat()
            print("Price checks (and the tape) are off for today: auto-run checks no alert and no stop. They come "
                  "back tomorrow, or with: autorun.py resume")
        elif a.minutes:
            p["all"], p["until"] = True, iso(now + minutes(a.minutes))
            print("Auto-run is paused for %d minutes, until %s. Then it goes on by itself. To go on now: "
                  "autorun.py resume" % (a.minutes, clock_label(now + minutes(a.minutes))))
        else:
            p["all"] = True
            p.pop("until", None)
            print("Auto-run is paused. Nothing runs until: autorun.py resume")
        save_json(state_path(), state)
        return 0
    finally:
        lock.release()


def cmd_resume(a, cfg, notes):
    """Undo a pause: "pause all" first; with no "pause all", "pause checks"."""
    lock = Lock("run.lock")
    wait_lock(lock)
    try:
        state = load_own(state_path(), {})
        now = now_karachi(a.now)
        p = state.get("pause") or {}
        if p.get("checks_off") and p["checks_off"] != now.date().isoformat():
            p.pop("checks_off")                      # an old "pause checks" ended with its day
        if p.get("all"):
            p.pop("all", None)
            p.pop("until", None)
            words = "Auto-run is on again."
            if p.get("checks_off"):
                words += " Price checks stay off today (pause checks): run resume again to turn them on."
        elif p.get("checks_off"):
            p.pop("checks_off")
            words = "Auto-run is on again. Price checks are back on today."
        else:
            words = "Auto-run is on. Nothing was paused."
        if p:
            state["pause"] = p
        else:
            state.pop("pause", None)
        save_json(state_path(), state)
        print(words)
        return 0
    finally:
        lock.release()


def cmd_open_latest(a, cfg, notes):
    """A click on a message (or a double-click on the app) opens the newest report."""
    path = os.path.join(report_root(cfg), "LATEST.md")
    open_file(path if os.path.exists(path) else report_root(cfg))
    return 0


def first_in_an_hour(key):
    """True when this problem was not told in the last hour (so a broken setup does not ring every 5
    minutes). Kept in its own small file: the main state file may be the broken part."""
    path = os.path.join(state_dir(), "told.json")
    try:
        doc = load_own(path, {})
        now = now_karachi()
        last = from_iso(doc.get(key))
        if last is not None and now - last < minutes(60):
            return False
        doc[key] = iso(now)
        for k in sorted(doc, key=lambda k: doc[k])[:-50]:
            del doc[k]
        save_json(path, doc)
    except OSError:
        pass
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description="PSX auto-run: the PSX pack's tools on a timer, on a Mac. "
                                             "It never places an order.")
    ap.add_argument("--config", help="the settings file (default: autorun.json in %s)" % home_dir())
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("tick", help="run whatever is due now (launchd calls this every 5 minutes)")
    p.add_argument("--now", help='pretend it is this Karachi time, "YYYY-MM-DD HH:MM"')
    p.add_argument("--dry-run", action="store_true", help="say what is due, run nothing")
    p = sub.add_parser("run", help="run one job now (morning, after_close or weekly)")
    p.add_argument("job", choices=["morning", "after_close", "weekly"])
    p.add_argument("--date", help="the PSX day (YYYY-MM-DD), for after_close")
    p.add_argument("--now", help=argparse.SUPPRESS)
    sub.add_parser("tape", help="record today's tape (launchd starts this; needs \"tape\": true)")
    sub.add_parser("selftest", help="check the setup (install.sh runs this through the app)")
    p = sub.add_parser("status", help="when each job last ran, and today's plan")
    p.add_argument("--now", help=argparse.SUPPRESS)
    p = sub.add_parser("plan", help="the jobs of a day, from the PSX clock")
    p.add_argument("--now", help='another day and time, "YYYY-MM-DD HH:MM" (Karachi time)')
    p.add_argument("--date", help="another day, YYYY-MM-DD")
    p = sub.add_parser("pause", help="pause the price checks for today, or everything")
    p.add_argument("what", choices=["checks", "all"])
    p.add_argument("--minutes", type=int, help="with all: go on by itself after this many minutes")
    p.add_argument("--now", help=argparse.SUPPRESS)
    p = sub.add_parser("resume", help="undo pause")
    p.add_argument("--now", help=argparse.SUPPRESS)
    sub.add_parser("open-latest", help="open the newest report")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    notes = Notes()
    cmds = {"tick": cmd_tick, "run": cmd_run, "tape": cmd_tape, "selftest": cmd_selftest, "status": cmd_status,
            "plan": cmd_plan, "pause": cmd_pause, "resume": cmd_resume, "open-latest": cmd_open_latest}
    try:
        cfg = load_config(a.config)
        notes.cfg = cfg
        rc = cmds[a.cmd](a, cfg, notes)
        return 0 if notes.mode == "applet" else rc    # the app shows our own messages; an exit code would hide them
    except AutorunError as e:
        log("problem: %s" % e)
        if notes.mode != "applet" or first_in_an_hour("problem:" + str(e)):
            notes.add("problems", "PSX auto-run: problem", str(e), sound=True, rank=0)
        if notes.mode != "applet":
            print("autorun.py: %s" % e, file=sys.stderr)
        return 0 if notes.mode == "applet" else 1
    except Exception:           # never fail in silence: log it, and tell the user (once an hour)
        log("crash:\n" + traceback.format_exc())
        if notes.mode != "applet" or first_in_an_hour("crash"):
            notes.add("problems", "PSX auto-run: problem", CRASH, sound=True, rank=0)
        if notes.mode != "applet":
            traceback.print_exc()
        return 0 if notes.mode == "applet" else 1
    finally:
        notes.emit()


if __name__ == "__main__":
    sys.exit(main())
