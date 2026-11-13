#!/usr/bin/env python3
"""News monitor for psx-news. Standard library only, Python 3.8.

Reads the PSX notices and the Pakistani business news on the stocks you watch or
hold, and prints only what is new since the last check, newest first, with how far
each stock is from its last close and a flag on what matters.

    python3 news.py                                  # watchlist + plan + portfolio tickers
    python3 news.py --tickers DEMO OGDC              # or --tickers DEMO,OGDC
    python3 news.py --tickers DEMO --all --hours 72  # everything in the last 3 days, seen or not
    python3 news.py --no-notices                     # headlines only
    python3 news.py --no-moves                       # skip the price line per ticker
    python3 news.py --market                         # also Pakistani SBP, IMF, budget and KSE-100 news

Your tickers, when --tickers is not given: every hit in psx/watchlist.json
(psx-scanner), the symbol in psx/plan.json (psx-exit) and the positions in
psx/portfolio.json (psx-broker).

Sources:
  - PSX notices: the 3 notice tabs of each company page (Financial Results, Board
    Meetings, Others; 5 rows each, with a date but no time). When psx/config.json says
    "psx_site": true (turn it on only if PSX allows you), https://dps.psx.com.pk/company/<SYM>
    first; else, or when the site fails, the saved page psx/data/company/<SYM>.html.
  - Headlines: RSS feeds, one request each: Business Recorder (markets, and business
    and finance), Dawn business, Express Tribune business and ProPakistani business.
    Change the list with "news_feeds" in psx/config.json or --feeds. A story touches a
    stock when it names the symbol in capitals as a whole word (DEMO, not Demo) or a
    short name for it (OGDCL for OGDC), or has the company name in its title or first
    lines (psx/universe.txt without "Limited", plus a short list such as Hubco for HUBC).
    A broker quoted as the source ("AHL said") does not count. Nor does "PSX": it is
    the exchange in most market stories (only "PSX Limited" is the PSX stock).
  - The move: the company page's price against the last close (LDCP) at its "As of"
    time, else the newest daily closing file in psx/data/mkt_summary/.

Times print in Karachi time (PKT). State: psx/state/news.json remembers what you have
seen (30 days), so an item shows once. --offline never touches the network: it reads
only the data folder (--data, default psx/data), and saved feeds as news/<name>.xml
there. --now pins the clock for a replay, so it reads only the data folder too. The
tests use both.

Headlines are leads, not facts: open the story or the notice before acting, and treat
nothing here as advice.
"""

import argparse
import datetime
import email.utils
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
from xml.etree import ElementTree

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
SESSIONS = {        # REG board segments: 5-minute grid start to end (trading starts 2 minutes later)
    "normal": (("09:30", "15:30"),),
    "friday": (("09:15", "12:00"), ("14:30", "16:30")),
    "ramadan": (("09:15", "13:30"),),
    "ramadan-friday": (("09:15", "12:30"),),
}
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name

FEEDS = [   # (name, source, URL); with --offline a feed is read from <data>/news/<name>.xml
    ("brecorder-markets", "Business Recorder", "https://www.brecorder.com/feeds/markets"),
    ("brecorder-business-finance", "Business Recorder", "https://www.brecorder.com/feeds/business-finance"),
    ("dawn-business", "Dawn", "https://www.dawn.com/feeds/business"),
    ("tribune-business", "Express Tribune", "https://tribune.com.pk/feed/business"),
    ("propakistani-business", "ProPakistani", "https://propakistani.pk/category/business/feed/"),
]
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from its last close is flagged as moving
LATE = datetime.timedelta(minutes=10)  # a pinned --now: nothing from more than this after it
BROKERS = {"AHL", "JSGCL"}  # brokers quoted as a source ("AHL said", "Arif Habib Limited said") do not count
STOP_TOKENS = BROKERS | {"PSX"}  # their symbol never matches; "PSX" is the exchange in most market stories
OWN_NAMES = {"PSX": ["PSX Limited"]}  # only these names count ("Pakistan Stock Exchange" is the exchange)
SHORT_NAMES = [   # short names the news writes in capitals, matched anywhere like a symbol: (name, symbol)
    ("OGDCL", "OGDC"), ("PTCL", "PTC"), ("SNGPL", "SNGP"), ("SSGCL", "SSGC"),
]
SAID = r"(?!,?\s+(?:(?:Limited|Ltd)\.?,?\s+)?(?:said|says|noted|notes|stated|states|added|wrote)\b)"  # a source
# "Board Meeting Other Than Financial Results", "by way of Other than Right Offer": not results, not rights
OTHER_THAN = re.compile(r"\bother\s+than\s+(?:the\s+)?(?:financial\s+results?|rights?[\s-]+(?:issue|shares?|offer)s?)",
                        re.I)
# --market keeps only Pakistani news: the general words (inflation, rupee, rates) also fit foreign wire stories
LOCAL = re.compile(r"\bPakistan|\bPSX\b|\bKSE\b|\bSBP\b|\bState Bank\b|\bPKR\b|\bRs\.?\s?\d"
                   r"|(?<!Indian )(?<!Lankan )(?<!Nepali )(?<!Nepalese )\brupees?\b", re.I)
# with the site off, the user opens a PSX notice in their browser: Claude never fetches it
ASK_NOTICE = ("To read a notice: ask the user to open its link in their browser and save it as %s (an image as "
              "<id>.gif), or paste a screenshot. Do not fetch it yourself.")
SYMBOL_RE = re.compile(r"^[A-Z0-9-]+$")  # a PSX symbol: capitals, digits and a dash
ALIASES = [   # names Pakistani news uses beside the listed name: (name, symbol)
    ("Hubco", "HUBC"), ("Hub Power", "HUBC"), ("Lucky Cement", "LUCK"), ("Meezan Bank", "MEBL"),
    ("Engro Fertilizers", "EFERT"), ("Fauji Fertilizer", "FFC"), ("Pakistan Petroleum", "PPL"),
    ("Oil and Gas Development", "OGDC"),
    ("Systems Limited", "SYS"), ("Pakistan State Oil", "PSO"), ("Mari Energies", "MARI"),
    ("Habib Bank", "HBL"), ("United Bank", "UBL"), ("MCB Bank", "MCB"), ("National Bank", "NBP"),
    ("Bank Alfalah", "BAFL"), ("K-Electric", "KEL"), ("Cnergyico", "CNERGY"), ("Attock Refinery", "ATRL"),
    ("DG Khan Cement", "DGKC"), ("Maple Leaf Cement", "MLCF"), ("Fauji Cement", "FCCL"),
    ("Pioneer Cement", "PIOC"), ("Engro Holdings", "ENGROH"), ("Searle", "SEARL"), ("Airlink", "AIRLINK"),
    ("Bank of Punjab", "BOP"), ("TRG Pakistan", "TRG"),
]
KEYWORDS = [   # tag, words (any case), read in the title and the first lines of the story
    ("results", r"\bresults\b|\bearnings\b|\bEPS\b|\bprofits?\b(?![- ]taking)|\bnet (?:income|loss)\b"
                r"|\b(?:quarterly|annual|half[- ]yearly) (?:accounts|report)\b|\bfinancial statements\b"),
    ("dividend", r"\bdividends?\b|\bpayouts?\b"),
    ("board", r"\bboard meetings?\b|\bboard of directors\b|\bmeeting of the board\b|\bboard (?:will |to )?meets?\b"
              r"|\bboard met\b|\bAGM\b|\bEOGM\b|\bgeneral meeting\b|\bclosed period\b"),
    ("material", r"\bmaterial information\b|\bprice[- ]sensitive\b|\binside information\b"),
    ("insider", r"\bdisclosure of interest\b|\binsiders?\b|\bsubstantial (?:share)?holders?\b|\bsection 110\b"
                r"|\bform[- ]?29\b"),
    ("rights", r"\brights?[\s-]+(?:issue|shares?|offer)\b"),
    ("bonus", r"\bbonus\b"),
    ("deal", r"\bacqui(?:re|res|red|ring|sition|sitions)\b|\bmergers?\b|\bmerge[sd]?\b|\bamalgamation\b"
             r"|\btake[- ]?overs?\b|\bbuyout\b|\bdeals?\b|\bcontracts?\b|\bagreements?\b|\bMoU\b"
             r"|\bjoint venture\b|\bstake\b"),
    ("capital", r"\bbuy[- ]?backs?\b|\brepurchase|\b(?:share|stock) split\b|\boffering\b|\bIPO\b|\bsukuks?\b"
                r"|\bTFCs?\b|\b(?:raise|raises|raised|raising) (?:capital|funds)\b"),
    ("people", r"\bappoint(?:s|ed|ment|ments)?\b|\bresign(?:s|ed|ation|ations)?\b|\bsteps? down\b"
               r"|\bchange (?:of|in) (?:the )?(?:CEO|CFO|chief executive|chairman|company secretary|directorship"
               r"|directors?|auditors?)\b|\b(?:new|names|named) (?:CEO|CFO|chairman|chief executive)\b"
               r"|\belection of directors\b"),
    ("legal", r"\blawsuits?\b|\bsued\b|\bsues\b|\bcourts?\b|\bjudge\b|\bpetition\b|\blitigation\b|\bstay order\b"
              r"|\bNAB\b|\bFIA\b|\bprobe\b|\binvestigation\b|\binquiry\b|\bfined\b|\bpenalt(?:y|ies)\b"
              r"|\bshow[- ]cause\b"),
    ("regulatory", r"\bSECP\b|\bOGRA\b|\bNEPRA\b|\bDRAP\b|\bPTA\b|\bCCP\b|\bFBR\b|\bregulators?\b"
                   r"|\blicen[cs]es?\b|\btariffs?\b"),
    ("risk", r"\bdefault(?:s|ed)?\b|\bdefaulters?\b|\bbankrupt|\bwinding up\b|\bliquidation\b|\bdelist"
             r"|\bnon[- ]?complian|\bfire\b|\bshut ?down\b|\bstrike\b|\bhack|\bcyber\b|\bbreach\b|\boutage\b"
             r"|\bcircular debt\b|\bfraud\b"),
    ("halt", r"\b(?:trading|market)[- ](?:halt|halts|halted|pause|suspension)\b|\bhalt(?:s|ed|ing)? (?:all )?trading\b"
             r"|\btrading (?:was |is |has been |remains )?(?:halted|suspended|paused)\b"
             r"|\bsuspen(?:d|ds|ded|sion) (?:of )?trading\b"),
    ("macro", r"\bSBP\b|\bState Bank\b|\bpolicy rate\b|\binterest rates?\b|\bmonetary policy\b|\bIMF\b|\bbudget\b"
              r"|\bCPI\b|\binflation\b|\brupee\b|\bKSE[- ]?100\b|\bKSE[- ]?30\b|\bKIBOR\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|\bprice target\b|\btarget price\b|\boutperform\b"
                r"|\bunderperform\b|\boverweight\b|\bunderweight\b|\bbrokerage\b|\banalysts?\b|\bcredit rating\b"
                r"|\bbuy (?:rating|call|stance)\b"),
]
IMPORTANT = [   # what "!" marks, and its words (any case)
    ("financial results", r"\bfinancial results?\b"),
    ("material information", r"\bmaterial information\b"),
    ("rights issue", r"\brights?[\s-]+(?:issue|shares?|offer)\b"),
    ("suspension or non-compliance", r"\bsuspen(?:d|ds|ded|sion)\b|\bnon[- ]?complian(?:t|ce)\b"
                                     r"|\bdefaulters?(?:'|\s)+segment\b"),
    ("merger or takeover", r"\bmergers?\b|\bamalgamation\b|\btake[- ]?overs?\b|\bintention to acquire\b"),
    ("market halt", dict(KEYWORDS)["halt"]),
]
TAB_TAGS = {"Financial Results": "results", "Board Meetings": "board"}  # the notice tab gives a tag too
FLAG_WORDS = {"XD": "XD (ex-dividend)", "XB": "XB (ex-bonus)", "XR": "XR (ex-rights)"}  # company page tags


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time (copied from tests/psx_core.py)

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


def clock_label(t):
    """datetime or time -> "10:15 AM"."""
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    """date -> "Thu 2026-10-29" (English day names, whatever the machine's language)."""
    if isinstance(d, datetime.datetime):
        d = d.date()
    return "%s %s" % (("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.weekday()], d.isoformat())


# ---------------------------------------------------------------- PSX calendar (copied from tests/psx_core.py)

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


# ---------------------------------------------------------------- prices and rules (copied from tests/psx_core.py)

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


# ---------------------------------------------------------------- PSX files (copied from tests/psx_core.py)

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


# ---------------------------------------------------------------- data access (copied from tests/psx_core.py)

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


# ---------------------------------------------------------------- inputs

def load_json(path):
    """A JSON file -> its value, or None when there is no file. Bad JSON raises NewsError."""
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError as exc:
            raise NewsError("%s is not valid JSON: %s" % (path, exc))
    return None


def load_config(path):
    """psx/config.json -> dict ({} when there is none)."""
    cfg = load_json(path)
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise NewsError("%s should hold a JSON object" % path)
    return cfg


def _rows(doc, key):
    """The dicts in doc[key], when doc is a dict and doc[key] a list."""
    rows = doc.get(key) if isinstance(doc, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def watch_from_files(portfolio="psx/portfolio.json", watchlist="psx/watchlist.json", plan="psx/plan.json"):
    """{ticker: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    for h in _rows(load_json(watchlist), "hits"):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[str(sym).strip().upper()] = h.get("name")
    pl = load_json(plan)
    if isinstance(pl, dict) and pl.get("symbol"):
        out.setdefault(str(pl["symbol"]).strip().upper(), pl.get("name"))
    for p in _rows(load_json(portfolio), "positions"):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            s = str(sym).strip().upper()
            out[s] = p.get("name") or out.get(s)
    return out


def read_universe(path):
    """psx/universe.txt (one SYMBOL|Name|Sector code a line; psx-scanner writes it) ->
    {SYMBOL: name}. No file -> {}."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            f = [x.strip() for x in line.replace(chr(0xfeff), "").split("|")]
            if len(f) > 1 and f[0] and f[1] and not f[0].startswith("#"):
                out[f[0].upper()] = f[1]
    return out


def load_state(path, errors):
    """psx/state/news.json -> dict. A broken file is noted and started again."""
    try:
        state = load_json(path)
    except NewsError as exc:
        errors.append("%s; starting a new one" % exc)
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path, state):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    os.replace(path + ".tmp", path)


# ---------------------------------------------------------------- feeds

def fetch(url):
    """http_get(url) with its errors as one plain NewsError."""
    try:
        return http_get(url)
    except urllib.error.HTTPError as exc:
        try:
            exc.close()
        except Exception:
            pass
        raise NewsError("HTTP %d for %s" % (exc.code, url))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NewsError("could not reach %s: %s" % (url, getattr(exc, "reason", exc)))


def feed_slug(url):
    """A feed URL -> a file name for it: https://www.arabnews.pk/rss.xml -> arabnews-pk-rss-xml."""
    s = re.sub(r"^[a-z]+://(?:www\.)?", "", str(url).strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "feed"


def feed_list(spec=None, root="psx/data", offline=False):
    """The feeds to read -> [{"name", "source", "url"}]. spec is --feeds (comma separated)
    or "news_feeds" from psx/config.json (a list): names from FEEDS, URLs, or
    {"url", "name", "source"}. No spec: FEEDS, but --offline reads every saved
    <root>/news/<name>.xml."""
    known = dict((n, {"name": n, "source": s, "url": u}) for n, s, u in FEEDS)
    if spec is None:
        if not offline:
            return [dict(known[n]) for n, _, _ in FEEDS]
        folder = os.path.join(root, "news")
        names = sorted(f[:-4] for f in os.listdir(folder) if f.endswith(".xml")) if os.path.isdir(folder) else []
        return [dict(known.get(n) or {"name": n, "source": None, "url": None}) for n in names]
    if isinstance(spec, str):
        spec = spec.split(",")
    if not isinstance(spec, (list, tuple)):
        raise NewsError('"news_feeds" in psx/config.json wants a list of feed names or URLs')
    out = []
    for e in spec:
        url = str((e.get("url") if isinstance(e, dict) else e) or "").strip()
        name = str((e.get("name") if isinstance(e, dict) else "") or "").strip()
        if not url and not name:
            continue
        if url in known or (not url and name in known):
            out.append(dict(known[url or name]))
        elif re.match(r"https?://", url, re.I):
            source = str((e.get("source") if isinstance(e, dict) else "") or "").strip()
            host = re.sub(r"^[a-z]+://(?:www\.)?", "", url, flags=re.I).split("/")[0]
            out.append({"name": re.sub(r"[^A-Za-z0-9._-]+", "-", name) or feed_slug(url),
                        "source": source or host, "url": url})
        else:
            raise NewsError('unknown feed "%s": use a URL or one of %s' % (url or name, ", ".join(known)))
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """A feed date -> naive UTC, or None. RFC 822 dates go through
    email.utils.parsedate_to_datetime (it reads Tribune's 2-digit year: "Thu, 29 Oct 26
    17:20:11 +0500" is 2026); ISO 8601 (Atom) works too. A date with no zone is UTC."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        return parse_utc(value)
    if d.tzinfo is not None:
        d = (d - d.utcoffset()).replace(tzinfo=None)
    return d


def clean(text):
    """Feed HTML or text -> one plain line: tags out, entities decoded, soft hyphens and
    zero-width marks out (Dawn puts them inside words), an em dash made "-"."""
    return re.sub("[­​-‍⁠﻿]", "", html_text(text)).replace(chr(0x2014), "-")


def lead(text, size=300):
    """The start of a story, cut at a word: the summary and the text the tags read."""
    t = re.sub(r"\s*(?:…|\.\.\.)?\s*Read More\b.*$", "", text or "")   # ProPakistani's tail
    if len(t) > size:
        t = t[:size].rsplit(" ", 1)[0].rstrip(",;:") + " ..."
    return t


def parse_feed(data, feed):
    """RSS 2.0 or Atom bytes -> headline items. "time" is naive UTC (or None), "date" the
    Karachi date. "_text" is the whole story, for matching names; it is not printed."""
    raw = (data or b"").lstrip()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:].lstrip()
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise NewsError("not valid XML: %s" % exc)
    source = feed.get("source") or clean(_text(root, "title"))[:40] or feed["name"]
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = clean(_text(el, "title"))
        link = _text(el, "link")
        if not link:
            hrefs = [c for c in el.iter() if _local(c.tag) == "link" and c.get("href")]
            best = [c for c in hrefs if c.get("rel", "alternate") == "alternate"] or hrefs
            link = best[0].get("href").strip() if best else ""
        guid = _text(el, "guid") or _text(el, "id") or link
        desc = clean(_text(el, "description") or _text(el, "summary"))
        body = clean(_text(el, "encoded") or _text(el, "content"))
        when = parse_time(_text(el, "pubDate") or _text(el, "published") or _text(el, "updated")
                          or _text(el, "date"))
        if not title and not link:
            continue
        out.append({"id": "rss:" + (guid or title), "source": source, "kind": "headline", "feed": feed["name"],
                    "ticker": None, "title": title, "summary": lead(desc or body), "link": link, "time": when,
                    "date": to_pkt(when).date().isoformat() if when else None,
                    "_text": " ".join(x for x in (title, desc, body if body != desc else "") if x)})
    return out


def collect_headlines(feeds, root, offline=False, errors=None):
    """Every item of every feed: one request a feed, or with --offline the saved file
    <root>/news/<name>.xml."""
    errors = [] if errors is None else errors
    items = []
    for f in feeds:
        try:
            if offline or not f.get("url"):
                path = os.path.join(root, "news", f["name"] + ".xml")
                if not os.path.isfile(path):
                    raise NewsError("no saved feed at %s (--offline and --now read only the data folder)" % path)
                with open(path, "rb") as fh:
                    data = fh.read()
            else:
                data = fetch(f["url"])
            items += parse_feed(data, f)
        except NewsError as exc:
            errors.append("feed %s: %s" % (f["name"], exc))
    return items


# ---------------------------------------------------------------- PSX company pages

def company_page(symbol, root, cfg=None, offline=False, errors=None):
    """A symbol's company page for news, through company_html(): the PSX site first when it
    is on and not offline (--offline, --now), else the saved page <root>/company/<SYM>.html.
    When the site is on but fails, the saved page is used with a note: its notices can be
    old. -> (html text, "site" or "saved"), or (None, a plain reason)."""
    text, how = company_html(symbol, root, cfg, offline)
    if text is not None and how == "saved" and site_on(cfg) and not offline and errors is not None:
        errors.append("%s: the PSX site did not give the page, so the saved page was used. Its notices "
                      "can be old." % symbol)
    return text, how


def read_pages(watch, root, cfg=None, offline=False, errors=None):
    """{ticker: parse_company(page)} for each ticker that has a page. The tickers with no
    page get one note that says what to save where."""
    errors = [] if errors is None else errors
    pages, missing = {}, []
    for t in sorted(watch):
        text, how = company_page(t, root, cfg, offline, errors)
        if text is None:
            missing.append((t, how))
            continue
        p = parse_company(text)
        if p.get("symbol") and p["symbol"] != t:
            errors.append("%s: the %s page is the page of %s, so it was skipped" % (t, how, p["symbol"]))
        elif p.get("price") is None and not any(p["announcements"].values()):
            errors.append("%s: the %s page has no price and no notices (is it a PSX company page?)" % (t, how))
        else:
            p["how"] = how
            pages[t] = p
    if len(missing) > 1 and not site_on(cfg):
        syms = ", ".join(t for t, _ in missing)
        errors.append("no company page for %s. " % syms + SITE_OFF % ("%s/company/<SYM> as %s for each of them" % (
            PSX, os.path.join(root, "company", "<SYM>.html"))))
    else:
        errors += ["no company page for %s. %s" % (t, why[:1].upper() + why[1:]) for t, why in missing]
    return pages


def notices_from_page(parsed, ticker):
    """parse_company() output -> notice items from its 3 tabs (5 rows each). PSX gives a
    notice a date but no time: "time" is None and "date" is the date on the page."""
    out = []
    for tab in ("Financial Results", "Board Meetings", "Others"):
        for row in (parsed.get("announcements") or {}).get(tab) or []:
            title = clean(row.get("title"))
            doc = row.get("doc_id")
            link = row.get("pdf") or row.get("attachment") or row.get("image") or "%s/company/%s" % (PSX, ticker)
            out.append({"id": "psx:" + (doc or "%s:%s:%s" % (ticker, row.get("date"), title)),
                        "source": "PSX notice", "kind": "notice", "ticker": ticker, "tab": tab,
                        "title": title, "summary": "", "doc_id": doc, "link": link, "time": None,
                        "date": row.get("date"), "_text": title})
    return out


def _limit_hit(price, lower, upper):
    if upper is not None and price >= upper:
        return "upper"
    if lower is not None and price <= lower:
        return "lower"
    return None


def page_move(ticker, parsed, now):
    """The price line from a company page: the price against LDCP at the page's As of
    time, and today's price limits (the page's CIRCUIT BREAKER, else computed from LDCP).
    None when the page has no price, or its As of time is after a pinned --now.

    "no_trade" is True when the stock has not traded since an older As of day: the page's
    LDCP has moved on to a newer close, so price - change is no longer the LDCP."""
    reg = parsed.get("reg") or {}
    price, ldcp, asof, change = parsed.get("price"), reg.get("ldcp"), parsed.get("as_of"), parsed.get("change")
    if ldcp is None and price is not None and change is not None:
        ldcp = round(price - change, 2)
    if not price or not ldcp or ldcp <= 0 or (asof and pkt_to_utc(asof) > now + LATE):
        return None
    lower, upper, frm = reg.get("lower"), reg.get("upper"), "company page"
    if lower is None or upper is None:
        (lower, upper), frm = price_limits(ldcp), "computed"
    no_trade = bool(asof and asof.date() < to_pkt(now).date() and change is not None
                    and abs(price - change - ldcp) >= 0.005)
    return {"ticker": ticker, "price": round(price, 2), "prev_close": round(ldcp, 2),
            "pct": round((price / ldcp - 1) * 100, 2), "time": pkt_to_utc(asof) if asof else None,
            "date": asof.date().isoformat() if asof else None, "source": "company page",
            "lower": lower, "upper": upper, "limits_from": frm, "at_limit": _limit_hit(price, lower, upper),
            "no_trade": no_trade, "flags": list(parsed.get("flags") or [])}


def daily_moves(tickers, now, root, cfg=None, offline=False, errors=None):
    """Price lines from the newest daily closing file up to now (today's file counts
    only after today's close): the official close against LDCP, limits computed from
    LDCP. A ticker that did not trade on the regular board that day gets none."""
    errors = [] if errors is None else errors
    now_pkt = to_pkt(now)
    segs = session(now_pkt.date(), cfg)
    done = not segs or now_pkt.time() >= segs[-1][1]
    try:
        found = recent_sessions(now_pkt.date(), 1, root, cfg, offline, include_end=done)
    except (ValueError, urllib.error.URLError, OSError) as exc:
        errors.append("daily closing file: %s" % getattr(exc, "reason", exc))
        return []
    if not found:
        return []
    day, rows = found[-1]
    by = dict((r["symbol"], r) for r in rows)
    out = []
    for t in tickers:
        r = by.get(t) or {}
        close, ldcp = r.get("close"), r.get("ldcp")
        if not (r.get("open") and r.get("high") and r.get("low") and close and ldcp and ldcp > 0):
            continue
        lower, upper = price_limits(ldcp)
        out.append({"ticker": t, "price": round(close, 2), "prev_close": round(ldcp, 2),
                    "pct": round((close / ldcp - 1) * 100, 2), "time": None, "date": day.isoformat(),
                    "source": "daily file", "lower": lower, "upper": upper, "limits_from": "computed",
                    "at_limit": _limit_hit(close, lower, upper), "no_trade": False,
                    "flags": list(r.get("flags") or [])})
    return out


def collect_moves(watch, pages, now, root, cfg=None, offline=False, errors=None):
    """One price line per ticker: its company page, else the newest daily closing file."""
    out, rest = [], []
    for t in sorted(watch):
        m = page_move(t, pages[t], now) if t in pages else None
        if m:
            out.append(m)
        else:
            rest.append(t)
    if rest:
        out += daily_moves(rest, now, root, cfg, offline, errors)
    return sorted(out, key=lambda m: m["ticker"])


# ---------------------------------------------------------------- matching and tags

def name_forms(name):
    """A company name -> the form a story uses for it: without "Limited" and a leading
    "The" ("The Hub Power Company Limited" -> "Hub Power Company"). A name left with one
    word keeps its "Limited" ("Systems Limited", never just "Systems")."""
    full = " ".join(str(name or "").split())
    core = re.sub(r"(?i)^the\s+", "", re.sub(r"(?i)[\s,]+(?:limited|ltd\.?)$", "", full)).strip()
    if len(core.split()) >= 2:
        return [core]
    return [full] if len(full.split()) >= 2 else []


def name_regex(form):
    """A name -> a regex for it as whole words: a space or a hyphen between words, "&" and
    "and" the same, "ize" and "ise" the same (Fertilizer, Fertiliser). Case counts: the
    name as written matches, and so does all capitals. A capitalized word right after it
    (other than Limited, Ltd, Company or Co), or AG or PLC, means a longer name, so no
    match: "Pakistan Petroleum Dealers Association" is not Pakistan Petroleum, "Habib Bank
    AG Zurich" is not Habib Bank."""
    alts = []
    for f in (form, form.upper()):
        words = [r"(?:&|and|AND)" if w in ("&", "and", "AND") else
                 re.escape(w).replace("iz", "i[sz]").replace("IZ", "I[SZ]") for w in f.split()]
        alts.append(r"[\s-]+".join(words))
    return (r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])"
            r"(?!\s+(?:(?!(?:Limited|Ltd|Company|Co)\b)[A-Z][a-z]|(?:AG|PLC)\b))" % "|".join(alts))


def mention_patterns(watch):
    """{ticker: (symbol regex or None, name regex or None)}. The symbol: in capitals as a
    whole word (DEMO, not Demo), or a short name from SHORT_NAMES (OGDCL for OGDC); never
    for a ticker on STOP_TOKENS. The name: name_forms() and the ALIASES, or only OWN_NAMES
    when it has some (PSX). A broker's name followed by "said" (and the like) is the source
    of a story, not its subject."""
    pats = {}
    for t, name in watch.items():
        sym = None
        if t not in STOP_TOKENS and re.search(r"[A-Z]", t):
            toks = [t] + [a for a, s in SHORT_NAMES if s == t]
            sym = re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(re.escape(x) for x in toks))
        forms = OWN_NAMES.get(t) or name_forms(name) + [a for a, s in ALIASES if s == t]
        rxs = [name_regex(f) for f in forms]
        if t in BROKERS:   # "Arif Habib Limited said", "according to Arif Habib": the source, not the subject
            rxs = [r"(?<!according to )(?<!According to )(?:%s)%s" % (rx, SAID) for rx in rxs]
        named = re.compile("|".join(rxs)) if rxs else None
        if sym or named:
            pats[t] = (sym, named)
    return pats


def name_found(rx, text):
    """True when the name regex rx (name_regex) is in text, and not as the end of a longer
    name: a capitalized word right before it ("Saudi National Bank", "Hatton National
    Bank") is another firm. "The" before it is fine."""
    for m in rx.finditer(text):
        w = re.search(r"(?:^|\s)(\S+)\s+$", text[max(0, m.start() - 40):m.start()])
        if not (w and re.match(r"^[A-Z][a-z]+$", w.group(1)) and w.group(1) != "The"):
            return True
    return False


def tag(items, watch):
    """Which of your tickers each item touches: the symbol anywhere in the story, the name in
    the title or the first lines (a name deep in a long story is too often another firm).
    The tags and the "!" read the title and the first lines too, without the "other than
    financial results" and "other than right offer" phrases. A Board Meetings notice gets
    no "financial results" "!": it is a meeting, often one still to come."""
    pats = mention_patterns(watch)
    for it in items:
        head = "%s %s" % (it["title"], it.get("summary") or "")
        hay = it.get("_text") or head
        touches = {it["ticker"]} if it.get("ticker") in watch else set()
        for t, (sym, named) in pats.items():
            if (sym and sym.search(hay)) or (named and (name_found(named, it["title"]) or
                                                        name_found(named, it.get("summary") or ""))):
                touches.add(t)
        it["touches"] = sorted(touches)
        head = OTHER_THAN.sub(" ", head)
        found = set(label for label, rx in KEYWORDS if re.search(rx, head, re.I))
        if it.get("tab") in TAB_TAGS:
            found.add(TAB_TAGS[it["tab"]])
        it["tags"] = [label for label, _ in KEYWORDS if label in found]
        it["why"] = [label for label, rx in IMPORTANT if re.search(rx, head, re.I)
                     and not (label == "financial results" and it.get("tab") == "Board Meetings")]
        it["important"] = bool(it["why"])
    return items


def dedupe(items):
    """One item per story: the same id; for headlines also the same link, or the same
    title on the same Karachi day. Notices merge only on the same PSX doc id (two
    companies can post a "Material Information" the same day)."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"]]
        if it["kind"] != "notice":
            keys += [it["link"], "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(), it.get("date") or "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


# ---------------------------------------------------------------- the check

def select(items, state, now, hours=48, show_all=False):
    """Items inside the window; only unseen ones unless show_all. A notice has a date but
    no time, so it counts by its date. Newest first; notices of one day keep the page
    order (Financial Results, Board Meetings, Others)."""
    seen = state.setdefault("seen", {})
    cutoff, ahead = now - datetime.timedelta(hours=hours), now + LATE
    first, last = to_pkt(cutoff).date().isoformat(), to_pkt(ahead).date().isoformat()
    out = []
    for it in items:
        if it["time"] is not None:
            if it["time"] < cutoff or it["time"] > ahead:
                continue
        elif it.get("date") and not first <= it["date"] <= last:
            continue
        it["new"] = it["id"] not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i.get("date") or "", i["time"] or datetime.datetime.max), reverse=True)
    return out


def remember(items, state, now):
    """Mark the shown items as seen (the feed guid or link, the PSX doc id) and forget
    what is older than KEEP_DAYS."""
    seen = state.setdefault("seen", {})
    stamp = utc_iso(now)
    for it in items:
        seen.setdefault(it["id"], stamp)
    cutoff = now - datetime.timedelta(days=KEEP_DAYS)
    state["seen"] = dict((k, v) for k, v in seen.items() if (parse_utc(v) or cutoff - LATE) >= cutoff)
    state["last_check"] = stamp


def _price(x):
    return "{:,.2f}".format(x)


def render_moves(moves, move_pct=MOVE_PCT, now=None):
    """One line per ticker: the price against the last close (LDCP), when, and flags."""
    out = []
    today = to_pkt(now).date() if now else None
    for m in moves:
        flags = ["MOVING"] if abs(m["pct"]) >= move_pct else []
        if m.get("at_limit"):
            flags.append("AT %s LIMIT %s" % (m["at_limit"].upper(), _price(m[m["at_limit"]])))
        flags += [FLAG_WORDS.get(f, f) for f in m.get("flags") or []]
        if m["source"] == "company page" and m.get("time"):
            when = to_pkt(m["time"])
            at = "%s %s PKT" % (", no trade since" if m.get("no_trade") else " at", clock_label(when))
            if when.date() != today:
                at += " on %s" % day_label(when)
        elif m["source"] == "company page":
            at = ""
        else:
            at = ", official close of %s" % day_label(datetime.date.fromisoformat(m["date"]))
        out.append("%-7s %8s %+.2f%% vs the last close %s%s%s" % (
            m["ticker"], _price(m["price"]), m["pct"], _price(m["prev_close"]), at,
            "".join("  " + f for f in flags)))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT, ask=None):
    """The text report. ask: the line under the notices on how to read one (site off)."""
    out = render_moves(moves or [], move_pct, now)
    if out:
        out.append("")
    notices = [i for i in items if i["kind"] == "notice"]
    heads = [i for i in items if i["kind"] != "notice"]
    if not items:
        out.append("Nothing new.")
    if notices:
        out.append("PSX notices (PSX gives a date, not a time):")
    for it in notices:
        out.append("%s %s  %-7s %s" % ("!" if it.get("important") else " ", it["date"] or "date unknown",
                                       ",".join(it["touches"]) or it["ticker"], it["title"]))
        out.append("      " + "  ".join(([("tags: " + ", ".join(it["tags"]))] if it["tags"] else []) + [it["link"]]))
    if notices and ask:
        out.append(ask)
    if notices and heads:
        out.append("")
    if heads:
        out.append("Headlines:")
    for it in heads:
        when = to_pkt(it["time"]).strftime("%Y-%m-%d %H:%M PKT") if it["time"] else "time unknown"
        out.append("%s %s  %-11s %-17s  %s" % ("!" if it.get("important") else " ", when,
                                              ",".join(it["touches"]) or "market", it["source"], it["title"]))
        out.append("      " + "  ".join(([("tags: " + ", ".join(it["tags"]))] if it["tags"] else []) + [it["link"]]))
    for e in errors:
        out.append("note: %s" % e)
    pages = [m for m in moves or [] if m["source"] == "company page"]
    today = to_pkt(now).date().isoformat()
    late = ""
    if pages and all(m.get("date") == today and not m.get("no_trade") for m in pages):
        late = " Page prices are about 5 minutes late."
    elif pages:
        late = " Page prices were about 5 minutes late when each page was saved."
    out.append("Checked %s.%s Headlines are leads, not facts: read the story or the notice before acting. "
               "Not financial advice." % (to_pkt(now).strftime("%Y-%m-%d %H:%M PKT"), late))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict((k, v) for k, v in it.items() if not k.startswith("_"))
        d["time"] = utc_iso(it["time"])
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="OGDC HUBC or OGDC,HUBC (default: watchlist + plan + portfolio)")
    ap.add_argument("--portfolio", default="psx/portfolio.json")
    ap.add_argument("--watchlist", default="psx/watchlist.json")
    ap.add_argument("--plan", default="psx/plan.json")
    ap.add_argument("--universe", default="psx/universe.txt", help="company names, SYMBOL|Name|Sector a line")
    ap.add_argument("--state", default="psx/state/news.json")
    ap.add_argument("--config", default="psx/config.json")
    ap.add_argument("--data", default="psx/data", help="data folder: company/, mkt_summary/, news/")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--feeds", help="feed names or URLs, comma separated (default: news_feeds in the config)")
    ap.add_argument("--no-feeds", action="store_true", help="PSX notices only")
    ap.add_argument("--no-notices", action="store_true", help="headlines only")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per ticker")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--market", action="store_true",
                    help="also Pakistani market news (SBP, IMF, budget, KSE-100, halts)")
    ap.add_argument("--offline", action="store_true", help="never touch the network: read the data folder only")
    ap.add_argument("--now", help='Karachi time like "2026-10-29 17:00" (or ...Z for UTC), for a replay: '
                                  'like --offline, it reads only the data folder')
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        return check(args)
    except (NewsError, ValueError) as exc:
        print("news: %s" % exc, file=sys.stderr)
        return 2


def local_news(item):
    """True when a market item's title or first lines name Pakistan, PSX, KSE, the SBP,
    PKR, a Rs amount or the rupee (not the Indian one): --market keeps only those."""
    return bool(LOCAL.search("%s %s" % (item["title"], item.get("summary") or "")))


def check(args):
    cfg = load_config(args.config)
    known = watch_from_files(args.portfolio, args.watchlist, args.plan)
    errors = []
    if args.tickers:   # "OGDC HUBC" or "OGDC,HUBC"; never fetch a page for a junk symbol
        syms = [s.strip().upper() for t in args.tickers for s in t.split(",") if s.strip()]
        bad = [s for s in syms if not SYMBOL_RE.match(s)]
        if bad:
            raise NewsError("not a PSX symbol: %s. Use capitals, digits and a dash, like --tickers OGDC HUBC "
                            "or --tickers OGDC,HUBC" % ", ".join(bad))
        watch = dict((s, known.get(s)) for s in syms)
    else:
        watch = dict((s, n) for s, n in known.items() if SYMBOL_RE.match(s))
        errors += ['skipped "%s" in your files: not a PSX symbol' % s for s in sorted(known) if s not in watch]
    if not watch and not args.market:
        print("news: no tickers. Pass --tickers, or run /psx-scanner first (it writes psx/watchlist.json).",
              file=sys.stderr)
        return 2
    now = parse_now(args.now)
    offline = args.offline or bool(args.now)   # a replay with --now never fetches: the site only has today
    state = load_state(args.state, errors)
    names = read_universe(args.universe)
    watch = dict((t, n or names.get(t)) for t, n in watch.items())
    items, pages = [], {}
    if watch and not (args.no_notices and args.no_moves):
        pages = read_pages(watch, args.data, cfg, offline, errors)
        for t, p in pages.items():
            watch[t] = watch[t] or p.get("name")
            if not args.no_notices:
                items += notices_from_page(p, t)
    if not args.no_feeds:
        spec = args.feeds if args.feeds is not None else cfg.get("news_feeds")
        feeds = feed_list(spec, args.data, offline)
        if not feeds and spec is None:
            errors.append("no saved feeds in %s (--offline and --now read only the data folder)"
                          % os.path.join(args.data, "news"))
        items += collect_headlines(feeds, args.data, offline, errors)
    items = dedupe(tag(items, watch))
    items = [i for i in items if i["touches"] or
             (args.market and set(i["tags"]) & {"macro", "halt"} and local_news(i))]
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(watch, pages, now, args.data, cfg, offline, errors)
    ask = None
    if not site_on(cfg) and any(i["kind"] == "notice" for i in shown):
        ask = ASK_NOTICE % os.path.join(args.data, "docs", "<id>.pdf")
    if args.json:
        mv = [dict(m, time=utc_iso(m["time"]), moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": utc_iso(now), "moves": mv, "items": _json_ready(shown),
                          "notes": errors + ([ask] if ask else [])}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct, ask))
    if not args.dry_run:
        remember(shown, state, now)
        save_state(args.state, state)
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
