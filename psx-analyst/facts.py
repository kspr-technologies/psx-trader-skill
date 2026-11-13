#!/usr/bin/env python3
"""Results reader for psx-analyst. Standard library only, Python 3.8.

Reads a company's results from its PSX company page and says what changed:
sales, profit after tax, EPS and the net margin of the newest quarter, against
the same quarter a year back and the quarter before, and the last full year.
It also finds the newest results notice and the next board meeting.

    python3 facts.py DEMO
    python3 facts.py DEMO FFC HBL --metrics psx/metrics.json
    python3 facts.py DEMO --json
    python3 facts.py DEMO --offline --data tests/fixtures/psx-market --now "2026-10-29 16:00"

Where the data comes from:
  <data>/company/<SYM>.html   a saved https://dps.psx.com.pk/company/<SYM> page
  <data>/docs/<id>.txt        a saved notice as text, or <id>.pdf (read with
                              pdftotext when it is installed)
  The PSX site, only when psx/config.json has "psx_site": true. PSX's terms
  ban automated use without its written permission. With the site on, the
  tool asks the site first, and reads the saved page only when the site does
  not answer. Never with --offline or --now (a replay reads only the folder).
  With the site off, Claude never fetches a notice link: the tool says to ask
  the user to save the file in <data>/docs/, or to paste a screenshot.

How the numbers are read:
  - Rs thousands, except EPS (rupees). The page shows unconsolidated figures.
  - The quarterly table holds the 3 newest quarters, then the same quarter a
    year back. Growth is the newest quarter against that quarter.
  - Labels follow the fiscal year end. A label that does not fit it is flagged.
  - Q4 is not on the page as a quarter. When a year and its Q1, Q2 and Q3 are
    all there, Q4 = the year minus those three. It is marked derived, and its
    EPS is approximate.
  - For a bank the top line is Mark-up Earned, like the page's own net margin.

--metrics FILE merges one row per symbol into a JSON file (for notes or a sheet).

Numbers only: this tool never says whether a stock is a buy.
"""

import argparse
import datetime
import gzip
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal, ROUND_HALF_UP


class FactsError(Exception):
    pass


# ---------------------------------------------------------------- PSX constants (copied from psx_core.py)

UA = "Mozilla/5.0 (compatible; psx-trader-skill/1.0)"
PSX = "https://dps.psx.com.pk"
PAUSE = 1.5         # seconds between two requests to the PSX site, at least
SLEEP = time.sleep  # tests swap this out
KARACHI_HOURS = 5   # Karachi time is UTC+5 all year: no daylight saving
SITE_OFF = ('The PSX site is off for this tool: save %s with your browser, or set "psx_site": true '
            'in psx/config.json if PSX has given you permission.')


# ---------------------------------------------------------------- time (copied from psx_core.py)

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


# ---------------------------------------------------------------- PSX page (copied from psx_core.py)

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


# ---------------------------------------------------------------- data access (copied from psx_core.py)

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


def company_html(symbol, root, cfg=None, offline=False):
    """A symbol's company page through page_html(): the PSX site first when it is on and not
    offline, else <root>/company/<SYM>.html. -> (html text, "site" or "saved"), or (None, a reason)."""
    sym = re.sub(r"[^A-Z0-9-]", "", str(symbol or "").upper())
    if not sym:
        return None, "no symbol"
    return page_html(os.path.join(root, "company", sym + ".html"), "%s/company/%s" % (PSX, sym), cfg, offline)


# ---------------------------------------------------------------- dates and fiscal quarters

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
LINES = {   # our name: the row names a PSX company page uses for it
    "sales": ("Sales", "Net Sales", "Revenue", "Net Revenue", "Turnover"),
    "markup": ("Mark-up Earned", "Markup Earned", "Mark-up / Return / Interest Earned"),
    "income": ("Total Income",),
    "profit": ("Profit after Taxation", "Profit / (Loss) after Taxation", "Net Profit"),
    "eps": ("EPS", "Earnings per Share"),
}
TOP_NAMES = {"sales": "sales", "markup": "mark-up earned", "income": "total income"}
FAR = 20                  # profit growth this many points above sales growth: check for one-off items
PDFTOTEXT = "pdftotext"   # poppler's tool; used only when it is installed
WEEKDAY = r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?"
DATE_PATTERN = (   # "@" becomes a tag, so two dates fit in one regex; "30,2026" needs no space
    r"(?:(?P<ma@>[A-Za-z]{3,9})\.?\s+(?P<da@>\d{1,2})(?:st|nd|rd|th)?(?:,\s*|\s+)(?P<ya@>\d{4})"
    r"|(?P<db@>\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?[\s-]+(?P<mb@>[A-Za-z]{3,9})\.?(?:,[\s-]*|[\s-]+)(?P<yb@>\d{4})"
    r"|(?P<yc@>\d{4})-(?P<mc@>\d{1,2})-(?P<dc@>\d{1,2})"
    r"|(?P<dd@>\d{1,2})[-/.](?P<md@>\d{1,2})[-/.](?P<yd@>\d{4}))")


def month_number(word):
    """"Sep", "September" or "sept." -> 9. Anything else -> None."""
    w = str(word or "").strip().rstrip(".").lower()
    if len(w) < 3:
        return None
    for i, name in enumerate(MONTHS):
        if name.lower().startswith(w):
            return i + 1
    return None


def fiscal_month(text):
    """The page's "Fiscal Year End" ("June", "30th June", "December 31") -> its month, or None."""
    for word in re.findall(r"[A-Za-z]+", str(text or "")):
        n = month_number(word)
        if n:
            return n
    return None


def month_end(year, month):
    """The last day of a month."""
    return datetime.date(year + month // 12, month % 12 + 1, 1) - datetime.timedelta(days=1)


def quarter_end(n, year, fye):
    """The last day of quarter n of the fiscal year that ends in month fye of `year`.
    Q1 2027 with a June year end -> 2026-09-30. Q3 2026 with a December one -> 2026-09-30."""
    k = (year - 1) * 12 + fye - 1 + 3 * n
    return month_end(k // 12, k % 12 + 1)


def quarter_of(d, fye):
    """The fiscal quarter (n, year) that ends in the month of date d, or None when that
    month is not a quarter end for this year end."""
    if not fye or (d.month - fye) % 3:
        return None
    return (d.month - fye - 1) % 12 // 3 + 1, d.year + (1 if d.month > fye else 0)


def parse_label(text):
    """"Q1 2027" -> (1, 2027). Anything else -> None."""
    m = re.match(r"^\s*Q([1-4])\s*[-/]?\s*(\d{4})\s*$", str(text or ""), re.I)
    return (int(m.group(1)), int(m.group(2))) if m else None


def qlabel(q):
    """(1, 2027) -> "Q1 2027"."""
    return "Q%d %d" % q


def step(q, k):
    """The quarter k quarters after q (k below 0: before it)."""
    i = q[1] * 4 + q[0] - 1 + k
    return i % 4 + 1, i // 4


def qindex(label):
    """A number that orders quarter labels: later quarters are bigger."""
    q = parse_label(label)
    return q[1] * 4 + q[0] - 1


def span_text(end):
    """The months of the quarter that ends on date `end`: "Jul to Sep 2026"."""
    k = end.year * 12 + end.month - 3
    y, m = k // 12, k % 12 + 1
    if y == end.year:
        return "%s to %s %d" % (MONTHS[m - 1][:3], MONTHS[end.month - 1][:3], end.year)
    return "%s %d to %s %d" % (MONTHS[m - 1][:3], y, MONTHS[end.month - 1][:3], end.year)


def iso_date(text):
    """"2026-09-30" -> datetime.date, or None."""
    try:
        return datetime.date(*[int(x) for x in str(text).split("-")])
    except (TypeError, ValueError):
        return None


def date_rx(tag=""):
    """DATE_PATTERN with its group names ending in tag. It reads dates as PSX letters write
    them: "November 3, 2026", "3rd November, 2026", "30 Sep 2026", "20-Oct-2026",
    "2026-09-30" and "30-06-2026" (day first)."""
    return DATE_PATTERN.replace("@", tag)


def date_from(m, tag=""):
    """The date in a date_rx(tag) match, or None when it is not a real date."""
    g = m.groupdict()
    for k in "abcd":
        if g.get("y" + k + tag):
            mo = g["m" + k + tag]
            mo = int(mo) if mo.isdigit() else month_number(mo)
            try:
                return datetime.date(int(g["y" + k + tag]), mo, int(g["d" + k + tag])) if mo else None
            except ValueError:
                return None
    return None


def time_after(text):
    """A meeting time at the start of text: " at 11:00 a.m.", " at 05:00 p.m." or
    " at 1100 hours" -> (datetime.time, the words it used), or (None, "")."""
    m = re.match(r"\s*,?\s*(?:at\s+)?(\d{1,2})(?:([:.])?(\d{2}))?\s*(a\.?\s?m\b\.?|p\.?\s?m\b\.?|hours\b|hrs\b)?",
                 str(text or ""), re.I)
    if not m or (not m.group(4) and not m.group(2)):
        return None, ""
    h, mi, ap = int(m.group(1)), int(m.group(3) or 0), (m.group(4) or "").lower()[:1]
    if ap in ("a", "p"):
        if not 1 <= h <= 12:
            return None, ""
        h = h % 12 + (12 if ap == "p" else 0)
    if h > 23 or mi > 59:
        return None, ""
    return datetime.time(h, mi), m.group(0).strip(" ,")


# ---------------------------------------------------------------- the numbers

def pct(a, b):
    """The change from b to a in percent, half up to 0.01: pct(838074, 686721) -> 22.04.
    None when a or b is unknown, or b is 0."""
    if a is None or b is None or b == 0:
        return None
    v = (Decimal(str(a)) - Decimal(str(b))) / abs(Decimal(str(b))) * 100
    return float(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def margin(part, whole):
    """part / whole in percent, half up to 0.01. None when unknown, or whole is 0 or less."""
    if part is None or whole is None or whole <= 0:
        return None
    v = Decimal(str(part)) / Decimal(str(whole)) * 100
    return float(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def minus(total, parts):
    """total minus the sum of parts (Decimal, so 11.69 - 8.53 is 3.16). None if any is unknown."""
    if total is None or None in parts:
        return None
    return float(Decimal(str(total)) - sum(Decimal(str(p)) for p in parts))


def thousands(v):
    """Rs thousands as a whole number when they are one: 10210451.0 -> 10210451."""
    return int(v) if isinstance(v, float) and v == int(v) else v


def find_row(table, key):
    """The values of the page row for one of our lines (LINES), or [] when the page has none."""
    want = set(" ".join(n.lower().split()) for n in LINES[key])
    for name, vals in (table.get("rows") or {}).items():
        if " ".join(name.lower().split()) in want:
            return list(vals)
    return []


def top_line(table):
    """The line a table's margins use: sales, else mark-up earned (banks), else total income."""
    for key in ("sales", "markup", "income"):
        if any(v is not None for v in find_row(table, key)):
            return key
    return None


def read_table(table, bank):
    """A page table -> (its top line, [{label, sales, income, profit, eps}] per column).
    "sales" holds the top line. "income" is Total Income, kept for banks."""
    top = top_line(table)
    rows = dict((key, find_row(table, key)) for key in ("income", "profit", "eps"))
    rows["sales"] = find_row(table, top) if top else []
    cols = []
    for i, head in enumerate(table.get("columns") or []):
        col = {"label": head}
        for key, vals in rows.items():
            v = vals[i] if i < len(vals) else None
            col[key] = v if key == "eps" else thousands(v)
        if not bank or top == "income":
            col["income"] = None
        cols.append(col)
    return top, cols


def ratio(ratios, column, names):
    """One value of the page's ratios table (by its row names), for the column with this head."""
    heads = ratios.get("columns") or []
    if column not in heads:
        return None
    want = set(" ".join(n.lower().split()) for n in names)
    for name, vals in (ratios.get("rows") or {}).items():
        if " ".join(name.lower().split()) in want:
            i = heads.index(column)
            return vals[i] if i < len(vals) else None
    return None


def check_labels(cols, fye, today):
    """Check the newest quarter label against the fiscal year end and the page layout (the 3
    newest quarters, then the same quarter a year back). A wrong newest label gets
    label_ok False, and "fiscal" holds the quarter the layout says it is (or None).
    -> a plain note, or None when the labels fit."""
    if not cols or not fye:
        return None
    new, last = cols[0], cols[-1]
    q, back = parse_label(new["label"]), parse_label(last["label"])
    if q is None:
        why = "%s: this is not a quarter label; check the results notice." % new["label"]
    elif quarter_end(q[0], q[1], fye) > today:
        why = ("%s: the label looks wrong; check the results notice. With a %s year end, %s would end on %s, "
               "and that day has not come yet." % (new["label"], MONTHS[fye - 1], new["label"],
                                                    quarter_end(q[0], q[1], fye).isoformat()))
    elif len(cols) >= 4 and back and back != step(q, -4):
        return ("%s and %s: the labels do not fit; check the results notice. The page puts the same quarter "
                "a year back last, and that is %s." % (new["label"], last["label"], qlabel(step(q, -4))))
    else:
        return None
    new.update(label_ok=False, fiscal=None, end=None)
    if len(cols) >= 4 and back:
        guess = step(back, 4)
        end = quarter_end(guess[0], guess[1], fye)
        if end <= today and qlabel(guess) not in [c["label"] for c in cols[1:]]:
            new.update(fiscal=qlabel(guess), end=end.isoformat())
            why += (" The page puts %s last (the same quarter a year back), so it is most likely %s (%s)."
                    % (last["label"], qlabel(guess), span_text(end)))
    return why


def derive_q4(cols, years, same_top, fye):
    """Q4 of each year on the page whose Q1, Q2 and Q3 are there too: the year minus the
    three. -> rows marked derived (their EPS is approximate)."""
    have = dict((c["fiscal"], c) for c in cols if c["fiscal"])
    out = []
    for y in years:
        if y["year"] is None or "Q4 %d" % y["year"] in have:
            continue
        three = [have.get("Q%d %d" % (n, y["year"])) for n in (1, 2, 3)]
        if None in three:
            continue
        row = {"label": "Q4 %d" % y["year"], "fiscal": "Q4 %d" % y["year"], "label_ok": True, "derived": True,
               "end": quarter_end(4, y["year"], fye).isoformat() if fye else None}
        for key in ("sales", "income", "profit", "eps"):
            ok = same_top or key != "sales"
            v = minus(y[key], [c[key] for c in three]) if ok else None
            row[key] = v if key == "eps" else thousands(v)
        if all(row[k] is None for k in ("sales", "income", "profit", "eps")):
            continue
        out.append(row)
    return out


def analyse(page, today):
    """Every number in the report, as a dict. page is parse_company() output; today is a
    Karachi date (it shows when a quarter label has not ended yet)."""
    fin = page.get("financials") or {}
    qt, at = fin.get("quarterly") or {}, fin.get("annual") or {}
    if not qt.get("columns") and not at.get("columns"):
        raise FactsError("the page has no financials (a right, a fund or a new listing?)")
    fye = fiscal_month((page.get("profile") or {}).get("fiscal_year_end"))
    bank = bool(find_row(qt, "markup") or find_row(at, "markup"))
    qtop, cols = read_table(qt, bank)
    atop, years = read_table(at, bank)
    for c in cols:
        q = parse_label(c["label"])
        c.update(fiscal=qlabel(q) if q else None, label_ok=True, derived=False,
                 end=quarter_end(q[0], q[1], fye).isoformat() if q and fye else None)
    for y in years:
        y["year"] = int(y["label"]) if re.match(r"^\d{4}$", y["label"].strip()) else None
        y["end"] = quarter_end(4, y["year"], fye).isoformat() if y["year"] and fye else None
    label_note = check_labels(cols, fye, today)
    rows = cols + derive_q4(cols, years, qtop == atop, fye)
    dated = sorted([r for r in rows if r["fiscal"]], key=lambda r: qindex(r["fiscal"]), reverse=True)
    rows = [r for r in rows if not r["fiscal"]] + dated
    for r in rows + years:
        r["net_margin"] = margin(r["profit"], r["sales"])
    by = dict((r["fiscal"], r) for r in rows if r["fiscal"])

    newest = None
    if cols:
        new = cols[0]
        if new["label_ok"]:
            back = by.get(qlabel(step(parse_label(new["fiscal"]), -4))) if new["fiscal"] else None
        else:
            back = cols[-1] if new["fiscal"] and len(cols) >= 4 else None
        newest = {"label": new["label"], "fiscal": new["fiscal"], "back": back["label"] if back else None}
        for key in ("sales", "income", "profit", "eps"):
            newest[key] = pct(new[key], back[key]) if back else None
        newest["net_margin"], newest["net_margin_back"] = new["net_margin"], back["net_margin"] if back else None
    qoq, ttm = None, None
    first = rows[0] if rows else None
    if first and first["fiscal"]:
        q = parse_label(first["fiscal"])
        before = by.get(qlabel(step(q, -1)))
        if before:
            qoq = {"label": first["label"], "before": before["label"],
                   "derived": bool(first["derived"] or before["derived"]),
                   "sales": pct(first["sales"], before["sales"]), "profit": pct(first["profit"], before["profit"])}
        four = [by.get(qlabel(step(q, -k))) for k in range(4)]
        if None not in four:
            full_year = q[0] == 4 and str(q[1]) in [y["label"] for y in years]   # the 4 quarters are a year on the page
            ttm = {"quarters": [r["label"] for r in four], "derived": any(r["derived"] for r in four),
                   "year": str(q[1]) if full_year else None}
            for key in ("sales", "profit", "eps"):
                vals = [r[key] for r in four]
                v = None if None in vals else float(sum(Decimal(str(x)) for x in vals))
                ttm[key] = v if key == "eps" else thousands(v)
    year = None
    if years:
        y, prev = years[0], (years[1] if len(years) > 1 else {})
        ratios = page.get("ratios") or {}
        year = {"label": y["label"], "end": y["end"], "sales": y["sales"], "profit": y["profit"], "eps": y["eps"],
                "net_margin": y["net_margin"],
                "gross_margin": None if bank else ratio(ratios, y["label"], ("Gross Profit Margin (%)",)),
                "before": prev.get("label"), "sales_growth": pct(y["sales"], prev.get("sales")),
                "income_growth": pct(y["income"], prev.get("income")),
                "profit_growth": pct(y["profit"], prev.get("profit")), "eps_growth": pct(y["eps"], prev.get("eps"))}

    top = TOP_NAMES.get(qtop or atop or "sales")
    checks = []
    if label_note:
        checks.append(("label", label_note))
    if cols and not fye:
        checks.append(("fiscal_year", "The page gives no fiscal year end, so the quarter labels were not checked."))
    checks.append(("unconsolidated", "Figures are unconsolidated: the company alone, not its group."))
    if bank:
        checks.append(("bank", "A bank has no gross margin: it is blank, not zero. Its net margin is profit / "
                               "mark-up earned, as on the page."))
    elif year and year["gross_margin"] is None:
        checks.append(("no_gross_margin", "The page has no gross margin for %s: it is blank, not zero."
                       % year["label"]))
    above = [r["label"] for r in rows + years[:1]
             if r["profit"] is not None and r["sales"] is not None and r["profit"] > r["sales"] > 0]
    if above:
        checks.append(("one_off", "Profit is above %s in %s: check for one-off items (other income, a gain or "
                                  "a tax item)." % (top, ", ".join(above))))
    for name, g, gs, gi, gp in (("the newest quarter", newest, "sales", "income", "profit"),
                                ("the last year", year, "sales_growth", "income_growth", "profit_growth")):
        if bank and g and g[gi] is not None:   # a bank's mark-up earned moves with interest rates
            gs, line = gi, "total income"
        else:
            line = top
        if g and g[gs] is not None and g[gp] is not None and g[gp] - g[gs] >= FAR:
            checks.append(("one_off", "In %s, profit growth (%+.2f%%) is far above %s growth (%+.2f%%): check "
                                      "for one-off items." % (name, g[gp], line, g[gs])))
    for r in rows:
        if r["derived"]:
            checks.append(("derived", "%s is derived: the year %s minus Q1, Q2 and Q3 on the page. Its EPS is "
                                      "approximate." % (r["label"], r["label"][3:])))
    reg, eq = page.get("reg") or {}, page.get("equity") or {}
    as_of = page.get("as_of")
    old = as_of.date().isoformat() if as_of and (today - as_of.date()).days > 4 else None
    return {"symbol": page.get("symbol"), "name": page.get("name"), "sector": page.get("sector"),
            "fiscal_year_end": MONTHS[fye - 1] if fye else None, "bank": bank, "top_line": top,
            "units": "Rs thousands, except EPS in rupees", "figures": "unconsolidated",
            "quarters": rows, "years": years, "newest": newest, "qoq": qoq, "ttm": ttm, "year": year,
            "checks": [{"code": c, "text": t} for c, t in checks],
            "price": page.get("price"), "pe": reg.get("pe"), "shares": eq.get("shares"),
            "free_float_pct": eq.get("free_float_pct"), "price_old": old,
            "as_of": utc_iso(pkt_to_utc(as_of)) if as_of else None}


# ---------------------------------------------------------------- words

def amount(v):
    """Rs thousands as the page writes them: 10,210,451. Unknown -> "-"."""
    return "-" if v is None else "{:,.0f}".format(v)


def eps_text(v):
    return "-" if v is None else "%.2f" % v


def sign(v):
    """A change in percent: "+13.00%". Unknown -> "-"."""
    return "-" if v is None else "%+.2f%%" % v


def share(v):
    """A margin in percent: "8.21%". Unknown -> "-"."""
    return "-" if v is None else "%.2f%%" % v


def figures(top, sales, profit, eps, about=False):
    """Words like "sales 38,450,200, profit 2,922,215 (Rs thousands), EPS 11.69", without the unknowns."""
    parts = ["%s %s" % (name, amount(v)) for name, v in ((top, sales), ("profit", profit)) if v is not None]
    text = ", ".join(parts) + (" (Rs thousands)" if parts else "")
    if eps is not None:
        text += (", " if text else "") + "EPS %s%s" % ("about " if about else "", eps_text(eps))
    return text or "no figures"


def changes(result):
    """Plain sentences: what changed in the newest quarter and the last full year."""
    out = []
    top = result["top_line"]
    rows = dict((r["label"], r) for r in result["quarters"])
    n = result["newest"]
    if n:
        new = rows[n["label"]]
        span = span_text(iso_date(new["end"])) if new["end"] else None
        if not new["label_ok"]:
            what = n["label"] + (" (the label looks wrong; most likely %s, %s)" % (new["fiscal"], span)
                                 if new["fiscal"] and span else " (the label looks wrong)")
        else:
            what = n["label"] + (" (%s)" % span if span else "")
        if n["back"]:
            back = rows[n["back"]]
            parts = ["%s %s" % (name, sign(n[key])) for key, name in (("sales", top), ("profit", "profit"),
                                                                      ("eps", "EPS")) if n[key] is not None]
            line = "%s against %s, a year back: %s" % (what, n["back"], ", ".join(parts) or "no figures to compare")
            if n["eps"] is not None:
                line += " (%s against %s)" % (eps_text(new["eps"]), eps_text(back["eps"]))
            out.append(line + ".")
        else:
            out.append("%s: the page has no quarter a year back to compare with." % what)
        if n["net_margin"] is not None:
            line = "Net margin %s (profit / %s)." % (share(n["net_margin"]), top)
            if n["net_margin_back"] is not None:
                line += " A year back: %s." % share(n["net_margin_back"])
            out.append(line)
    q = result["qoq"]
    if q and (q["sales"] is not None or q["profit"] is not None):
        first, before = rows[q["label"]], rows[q["before"]]
        nums = ", ".join("%s %s" % (name, sign(q[key])) for key, name in (("sales", top), ("profit", "profit"))
                         if q[key] is not None)
        b = q["before"] + (" (derived)" if before["derived"] else "")
        if n and q["label"] == n["label"]:
            line = "Against the quarter before, %s: %s." % (b, nums)
        else:
            line = "%s%s against the quarter before, %s: %s." % (
                q["label"], " (derived)" if first["derived"] else "", b, nums)
        out.append(line + " Many businesses are seasonal: look at the year-back change first.")
    y = result["year"]
    if y:
        line = "Last full year, %s%s: %s." % (y["label"], " (to %s)" % y["end"] if y["end"] else "",
                                             figures(top, y["sales"], y["profit"], y["eps"]))
        grow = ["%s %s" % (name, sign(y[key])) for key, name in (("sales_growth", top), ("profit_growth", "profit"),
                                                                 ("eps_growth", "EPS")) if y[key] is not None]
        if y["before"] and grow:
            line += " Against %s: %s." % (y["before"], ", ".join(grow))
        out.append(line)
        if y["net_margin"] is not None or y["gross_margin"] is not None:
            bits = []
            if y["net_margin"] is not None:
                bits.append("net margin %s" % share(y["net_margin"]))
            if y["gross_margin"] is not None:
                bits.append("gross margin %s (from the page's ratios)" % share(y["gross_margin"]))
            out.append("The year's " + ", ".join(bits) + ".")
    t = result["ttm"]
    if t and not t["year"] and (t["sales"] is not None or t["profit"] is not None):
        out.append("Last 4 quarters%s: %s." % (" (one derived)" if t["derived"] else "",
                                               figures(top, t["sales"], t["profit"], t["eps"], t["derived"])))
    if result["price"] is not None:
        line = "On the page: price %.2f" % result["price"]
        if result["price_old"]:
            line += " (last trade %s: an old price)" % result["price_old"]
        if result["pe"] is not None:
            line += ", P/E (TTM) %.2f" % result["pe"]
        out.append(line + ".")
    return out


# ---------------------------------------------------------------- notices

ENDED_RX = re.compile(r"\bend(?:ed|ing)\b[\s:\u2013-]*(?:on\s+)?(?:the\s+)?(?:" + date_rx() + ")", re.I)
HELD_RX = re.compile(r"\b(?:will|shall)\s+(?:now\s+)?be\s+held\b|\b(?:is|are|has\s+been|have\s+been)\s+(?:now\s+)?"
                     r"(?:re-?)?(?:scheduled|convened|fixed)\b", re.I)
ON_DATE_RX = re.compile(r"\bon\s+" + WEEKDAY + r"(?:the\s+)?(?:" + date_rx() + ")", re.I)
FROM_TO_RX = re.compile(r"\b(?:from|commencing(?:\s+from)?|starting(?:\s+from)?)\s+" + WEEKDAY + r"(?:" + date_rx("1")
                        + r")\s*(?:to|till|until|up\s*to|through|and\s+ending\s+on|-)\s*" + WEEKDAY
                        + r"(?:" + date_rx("2") + ")", re.I)
NOT_BOARD = re.compile(r"general\s+meeting|\bA?GM\b|\bEOGM\b|briefing|in\s+progress", re.I)
NOT_RESULTS = re.compile(r"other\s+than\s+(?:the\s+)?financial\s+results?", re.I)   # a board meeting for other matters
# with the site off, Claude never fetches a PSX link: the user opens it and saves the file
ASK_USER = ("Ask the user to open %s in their browser and save it as %s, or paste a screenshot. "
            "Do not fetch it yourself.")


def notice_order(row):
    """Newest first: by date, then by document id (ids grow over time)."""
    doc = str(row.get("doc_id") or "")
    return row.get("date") or "", int(doc) if doc.isdigit() else 0


def notice_link(row):
    return row.get("pdf") or row.get("attachment") or row.get("image")


def period_end(title):
    """The period end a notice title names ("... QUARTER ENDED SEPTEMBER 30, 2026") as ISO, or None."""
    m = ENDED_RX.search(" ".join(str(title or "").split()))
    d = date_from(m) if m else None
    return d.isoformat() if d else None


def sentence(text, start, size):
    """text[start:start+size], cut at the end of a sentence (a full stop, then a capital)."""
    part = text[start:start + size]
    stop = re.search(r"\.\s+[A-Z]", part)
    return part[:stop.start()] if stop else part


def pdf_text(path):
    """The text of a PDF by pdftotext -layout. None when pdftotext is not installed or fails."""
    exe = shutil.which(PDFTOTEXT)
    if not exe:
        return None
    try:
        done = subprocess.run([exe, "-layout", "-q", path, "-"], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else None


def notice_text(root, doc_id):
    """The text of a saved notice: <root>/docs/<id>.txt, else pdftotext on <root>/docs/<id>.pdf.
    -> (text, the file it came from), or (None, why there is no text; None when nothing is saved)."""
    if not doc_id:
        return None, None
    base = os.path.join(root, "docs", str(doc_id))
    rel = "docs/%s" % doc_id
    if os.path.isfile(base + ".txt"):
        with open(base + ".txt", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if text.strip():
            return text, rel + ".txt"
        return None, "%s.txt is empty: the PDF is most likely a scan (many PSX notices are)" % rel
    if os.path.isfile(base + ".pdf"):
        text = pdf_text(base + ".pdf")
        if text is None:
            return None, "%s.pdf was not read: pdftotext (poppler) is not installed, or it failed" % rel
        if not text.strip():
            return None, "%s.pdf has no text: it is most likely a scan (many PSX notices are)" % rel
        return text, rel + ".pdf"
    for ext in (".gif", ".png", ".jpg"):
        if os.path.isfile(base + ext):
            return None, "%s%s is an image only: there is no text to read" % (rel, ext)
    return None, None


def board_facts(text):
    """What a board notice's text says: the meeting date and time, the closed period, the
    period of the accounts, and whether they are the consolidated accounts."""
    flat = " ".join(str(text or "").split())
    out = {"meeting": None, "time": None, "quote": None, "closed": None, "period_end": None,
           "consolidated": bool(re.search(r"(?<![-\w])consolidated", flat, re.I))
           and not re.search(r"un-?consolidated|stand-?alone", flat, re.I)}
    for h in HELD_RX.finditer(flat):
        window = sentence(flat, h.end(), 300)
        m = ON_DATE_RX.search(window)
        d = date_from(m) if m else None
        if not d:
            continue
        t, words = time_after(window[m.end():])
        gap = window[:m.start()].strip()
        out["meeting"], out["time"] = d.isoformat(), t.strftime("%H:%M") if t else None
        out["quote"] = h.group(0) + (" ... " if gap else " ") + m.group(0) + (" " + words if words else "")
        break
    for c in re.finditer(r"closed\s+period", flat, re.I):
        m = FROM_TO_RX.search(sentence(flat, c.end(), 250))
        a, b = (date_from(m, "1"), date_from(m, "2")) if m else (None, None)
        if a and b:
            out["closed"] = [a.isoformat(), b.isoformat()]
            break
    m = re.search(r"\bended\s+(?:on\s+)?(?:the\s+)?(?:" + date_rx() + ")", flat, re.I)
    d = date_from(m) if m else None
    out["period_end"] = d.isoformat() if d else None
    return out


def same_numbers(text, row):
    """True when a notice text shows the row's top line, profit and EPS as the page writes them."""
    want = ["{:,.0f}".format(abs(row[k])) for k in ("sales", "profit") if row.get(k) is not None]
    if row.get("eps") is not None:
        want.append("%.2f" % abs(row["eps"]))
    return bool(text and want) and all(
        re.search(r"(?<![\d,.])%s(?!\d|[.,]\d)" % re.escape(w), text) for w in want)


def ask_for(link, doc_id, root, site):
    """The line that says how to get a notice's text, or None with no link. With the site on:
    open the link. With it off, Claude never fetches a PSX link: the user saves the file."""
    if not link:
        return None
    if site:
        return "Open the notice: " + link
    name = "%s%s" % (doc_id, os.path.splitext(link)[1] or ".pdf") if doc_id else os.path.basename(link)
    return ASK_USER % (link, os.path.join(root, "docs", name))


def board_notice(row, root, today, site):
    """A board notice row and what its saved text says (board_facts), with where the text came
    from, or why there is none and how to get it."""
    text, where = notice_text(root, row.get("doc_id"))
    facts = board_facts(text) if text else dict.fromkeys(("meeting", "time", "quote", "closed", "period_end"))
    facts.setdefault("consolidated", False)
    facts["period_end"] = facts["period_end"] or period_end(row.get("title"))
    link = notice_link(row)
    return dict(facts, date=row.get("date"), title=row.get("title"), doc_id=row.get("doc_id"), link=link,
                text_from=where if text else None, why=None if text else where,
                get=None if text else ask_for(link, row.get("doc_id"), root, site), newer=[],
                ahead=bool(facts["meeting"] and facts["meeting"] >= today.isoformat()))


def read_notices(page, root, today, result, site=False):
    """The newest results notice, the board notices, and a note when the page lags them.
    site: the PSX site is on for this run, so Claude may open a notice link."""
    ann = page.get("announcements") or {}
    out = {"results": None, "met": None, "board": None, "other": None, "note": None}
    res = [r for r in ann.get("Financial Results") or [] if re.search(r"financial\s+results?\b", r.get("title") or "", re.I)]
    if res:
        r = max(res, key=notice_order)
        out["results"] = {"date": r.get("date"), "title": r.get("title"), "doc_id": r.get("doc_id"),
                          "link": notice_link(r), "period_end": period_end(r.get("title")), "period": None,
                          "page_label": None, "check": None, "text_from": None, "why": None}
        fye, end = fiscal_month(result["fiscal_year_end"]), iso_date(out["results"]["period_end"])
        q = quarter_of(end, fye) if end and fye else None
        if q:   # which quarter (or year) the notice is for, by the fiscal year end
            whole_year = q[0] == 4 and re.search(r"\byear\b", r.get("title") or "", re.I)
            out["results"]["period"] = "the year %d" % q[1] if whole_year else qlabel(q)
    board = sorted(ann.get("Board Meetings") or [], key=notice_order, reverse=True)
    met = next((r for r in board if re.search(r"in\s+progress", r.get("title") or "", re.I)), None)
    notes = [r for r in board if re.search(r"\bboard\b", r.get("title") or "", re.I)
             and not NOT_BOARD.search(r.get("title") or "")]
    newer = []   # newer results board notices with no meeting date in a saved text
    for row in (r for r in notes if not NOT_RESULTS.search(r.get("title") or "")):
        n = board_notice(row, root, today, site)
        if n["meeting"] and (n["ahead"] or not newer):   # an older notice counts while its meeting is ahead
            out["board"] = dict(n, newer=newer)
        if n["meeting"]:
            break
        newer.append(n)
    b = out["board"] = out["board"] or (newer[0] if newer else None)
    other = next((r for r in notes if NOT_RESULTS.search(r.get("title") or "")), None)
    if other and (not b or notice_order(other) > notice_order(b)):
        out["other"] = board_notice(other, root, today, site)
    if met and (not b or notice_order(met) > notice_order(b)):
        out["met"] = {"date": met.get("date"), "title": met.get("title"), "doc_id": met.get("doc_id"),
                      "today": met.get("date") == today.isoformat()}
    rows = result["quarters"]
    new = next((r for r in rows if r["label"] == (result["newest"] or {}).get("label")), None)
    res = out["results"]
    if res and new and not new["label_ok"] and res["period"] and res["period"] == new["fiscal"]:
        res["page_label"] = new["label"]
    if res and new and res["period_end"] and res["period_end"] == new["end"]:
        text, where = notice_text(root, res["doc_id"])
        if text:
            res["check"], res["text_from"] = ("same" if same_numbers(text, new) else "differs"), where
        else:
            res["why"] = where
    ends = [r["end"] for r in rows if r["end"]] + [y["end"] for y in result["years"] if y["end"]]
    lost = bool(rows) and not rows[0]["fiscal"]
    if res and res["period_end"] and ends and not lost and res["period_end"] > max(ends):
        text, where = notice_text(root, res["doc_id"])
        if text:
            how = " (%s)." % where
        else:
            how = ". " + (ask_for(res["link"], res["doc_id"], root, False) if res["link"] and not site
                          else res["link"] or "")
        out["note"] = ("The newest results notice (%s) is for the period ended %s, but the page's newest figures "
                       "end %s. The page has not caught up: read the notice%s"
                       % (res["date"], res["period_end"], max(ends), how.rstrip()))
    return out


# ---------------------------------------------------------------- output

def notice_lines(n, today):
    """The notices part of the report, as lines."""
    out = []
    res = n["results"]
    if res:
        when = day_label(iso_date(res["date"])) if iso_date(res["date"]) else "date unknown"
        out.append("Results notice: %s%s, %s" % (when, " (today)" if res["date"] == today.isoformat() else "",
                                                  res["title"]))
        if res["link"]:
            out.append("  " + res["link"])
        if res["page_label"]:
            out.append("  It is for %s: the quarter the page calls %s." % (res["period"], res["page_label"]))
        if res["check"] == "same":
            out.append("  Its text (%s) shows the same numbers as the page." % res["text_from"])
        elif res["check"] == "differs":
            out.append("  Its text (%s) does not show the page's numbers in the same form: read it." % res["text_from"])
        elif res["why"]:
            out.append("  %s." % res["why"])
    else:
        out.append("Results notice: none in the page's Financial Results tab.")
    met = n["met"]
    if met and met["today"]:
        out.append("The board is meeting today (Board Meeting In Progress): results can come out during the session.")
    elif met:
        out.append("Last board meeting: %s (Board Meeting In Progress)." % day_label(iso_date(met["date"])))
    b, other = n["board"], n.get("other")
    if b:
        out += board_lines(b, met, today, "Newest board notice for results" if other else "Newest board notice")
    else:
        out.append("Board meetings: no board notice%s in the page's Board Meetings tab."
                   % (" for results" if other else ""))
    if other:   # a board meeting for other matters: no warning about results
        when = ("%s (%s), from the " % (meeting_when(other), days_from(iso_date(other["meeting"]), today))
                if other["meeting"] else "")
        out.append("Board meeting not for results: %snotice of %s (%s)." % (when, posted_day(other), other["title"]))
        if other["link"]:
            out.append("  " + other["link"])
    return out


def posted_day(n):
    """The day a notice was posted: "Tue 2026-10-20", or "on a date not shown"."""
    return day_label(iso_date(n["date"])) if iso_date(n["date"]) else "on a date not shown"


def days_from(d, today):
    """"today", "in 5 days" or "1 day ago"."""
    days = (d - today).days
    if not days:
        return "today"
    return ("in %d day%s" if days > 0 else "%d day%s ago") % (abs(days), "" if abs(days) == 1 else "s")


def meeting_when(n):
    """A notice's meeting day and time: "Tue 2026-11-03 at 11:00 AM PKT"."""
    t = datetime.time(*map(int, n["time"].split(":"))) if n["time"] else None
    return day_label(iso_date(n["meeting"])) + (" at %s PKT" % clock_label(t) if t else "")


def board_lines(b, met, today, newest="Newest board notice"):
    """The board notice part of the report, as lines."""
    out, posted = [], posted_day(b)
    if b["meeting"]:
        d = iso_date(b["meeting"])
        when = "%s (%s)" % (meeting_when(b), days_from(d, today))
        if d > today:
            out.append("Next board meeting: %s." % when)
        elif d == today:
            out.append("Next board meeting: %s. Results can come out during the session." % when)
        else:
            out.append("%s: the meeting was on %s." % (newest, when))
        out.append("  From the notice of %s (%s), text in %s." % (posted, b["title"], b["text_from"]))
        if b["quote"]:
            out.append('  It says: "%s"' % b["quote"])
    else:
        out.append("%s: %s, %s." % (newest, posted, b["title"]))
        if b["text_from"]:
            out.append("  Its text (%s) gives no meeting date." % b["text_from"])
        elif b["why"]:
            out.append("  %s." % b["why"])
    over = bool(not b["meeting"] and met and not met["today"])   # then its text is not needed
    if over:
        out.append("  It is most likely for the meeting of %s, which is over." % day_label(iso_date(met["date"])))
    if b["period_end"]:
        out.append("  For the period ended %s%s." % (b["period_end"], ", the consolidated accounts"
                                                     if b["consolidated"] else ""))
    if b["closed"]:
        inside = b["closed"][0] <= today.isoformat() <= b["closed"][1]
        out.append("  Closed period: %s to %s%s." % (b["closed"][0], b["closed"][1],
                                                     " (today is inside it)" if inside else ""))
    if b["link"]:
        out.append("  " + (b["get"] if b["get"] and not over else b["link"]))
    for x in b["newer"]:   # newer notices with no date: the date above came from an older one
        out.append("  A newer notice can change the date or time: %s, %s." % (posted_day(x), x["title"]))
        if x["text_from"]:
            out.append("  Its text (%s) gives no meeting date: read it." % x["text_from"])
        elif x["why"]:
            out.append("  %s." % x["why"])
        if x["get"]:
            out.append("  " + x["get"])
    return out


def render(symbol, result, notices, today, source="saved", made=None, fell_back=False):
    """The report as text. fell_back: the site is on but did not answer, so this is the saved page."""
    r = result
    head = "%s (%s)%s" % (r["name"] or symbol, symbol, ", " + r["sector"] if r["sector"] else "")
    head += "" if head.endswith(".") else "."
    if r["fiscal_year_end"]:
        head += " Fiscal year ends in %s." % r["fiscal_year_end"]
    if source == "site":
        src = "Source: the PSX company page, from the site (about 5 minutes late)."
    else:
        src = "Source: the saved PSX company page%s." % (" (page time %s %s PKT)" % (day_label(made), clock_label(made))
                                                         if made else "")
        if fell_back:
            src += " The PSX site did not answer."
        age = (today - made.date()).days if made else 0
        if age > 7:
            src += " It is %d days old: newer results or notices may be out." % age
    out = [head, src, "Rs thousands, except EPS in rupees.", ""]
    bank = any(q["income"] is not None for q in r["quarters"])
    table = [["quarter", "ends", r["top_line"]] + (["total income"] if bank else []) + ["profit", "EPS", "net margin"]]
    tails = [""]
    for q in r["quarters"]:
        table.append([q["label"], q["end"] or "-", amount(q["sales"])] + ([amount(q["income"])] if bank else [])
                     + [amount(q["profit"]), eps_text(q["eps"]), share(q["net_margin"])])
        if q["derived"]:
            tails.append("  derived")
        elif not q["label_ok"]:
            tails.append("  label looks wrong" + (" (most likely %s)" % q["fiscal"] if q["fiscal"] else ""))
        else:
            tails.append("")
    if r["quarters"]:
        width = [max(len(row[i]) for row in table) for i in range(len(table[0]))]
        for row, tail in zip(table, tails):
            cells = [row[0].ljust(width[0]), row[1].ljust(width[1])] + [c.rjust(w) for c, w in zip(row[2:], width[2:])]
            out.append("  ".join(cells) + tail)
    else:
        out.append("The page has no quarterly figures.")
    out += ["", "What changed:"] + ["  - " + line for line in changes(r)]
    out += ["", "Check:"] + ["  - " + c["text"] for c in r["checks"]]
    out += [""] + notice_lines(notices, today)
    if notices["note"]:
        out += ["", "NOTE: " + notices["note"]]
    return "\n".join(out) + "\n"


def metrics_row(symbol, result, notices):
    """One row for psx/metrics.json (Rs thousands, except EPS)."""
    n, y, t = result["newest"] or {}, result["year"] or {}, result["ttm"] or {}
    new = next((q for q in result["quarters"] if q["label"] == n.get("label")), {})
    b, res = notices.get("board") or {}, notices.get("results") or {}
    return {"symbol": symbol, "name": result["name"], "sector": result["sector"],
            "fiscal_year_end": result["fiscal_year_end"], "quarter": n.get("label"),
            "quarter_end": new.get("end"), "label_ok": new.get("label_ok"), "sales_label": result["top_line"],
            "sales_000": new.get("sales"), "profit_000": new.get("profit"), "eps": new.get("eps"),
            "sales_growth_yoy": n.get("sales"), "profit_growth_yoy": n.get("profit"), "eps_growth_yoy": n.get("eps"),
            "sales_growth_qoq": (result["qoq"] or {}).get("sales"), "net_margin": n.get("net_margin"),
            "year": y.get("label"), "year_sales_000": y.get("sales"), "year_profit_000": y.get("profit"),
            "year_eps": y.get("eps"), "gross_margin": y.get("gross_margin"),
            "sales_ttm_000": t.get("sales"), "profit_ttm_000": t.get("profit"), "eps_ttm": t.get("eps"),
            "pe": result["pe"], "results_notice": res.get("date"),
            "next_board_meeting": b.get("meeting") if b.get("ahead") else None,
            "figures": "unconsolidated", "source": "PSX company page"}


# ---------------------------------------------------------------- files and command line

def load_config(path):
    """psx/config.json as a dict ({} when it is missing). Bad JSON -> {} and a warning:
    then the PSX site stays off."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        print("facts: %s is not valid JSON, so the PSX site stays off" % path, file=sys.stderr)
        return {}
    return cfg if isinstance(cfg, dict) else {}


def page_clock(text):
    """When a PSX page was made: its top-bar clock -> naive Karachi time, or None."""
    m = re.search(r'<div class="topbar__status__label">([^<]*)</div>', text or "")
    return page_time(html_text(m.group(1))) if m else None


def write_metrics(path, rows, today):
    """Merge rows into the metrics file by symbol. -> how many companies it holds."""
    existing = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            raise FactsError("%s is not valid JSON: fix it or move it away" % path)
        for r in (old.get("companies") or []) if isinstance(old, dict) else []:
            if isinstance(r, dict) and r.get("symbol"):
                existing[r["symbol"]] = r
    for r in rows:
        existing[r["symbol"]] = r
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"as_of": today.isoformat(), "units": "Rs thousands, except EPS in rupees",
                   "companies": sorted(existing.values(), key=lambda r: r["symbol"])}, fh, indent=2)
        fh.write("\n")
    os.replace(path + ".tmp", path)
    return len(existing)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("symbols", nargs="*", help="PSX symbols, e.g. DEMO FFC")
    ap.add_argument("--data", default=os.path.join("psx", "data"), metavar="DIR",
                    help="data folder: company/<SYM>.html pages, docs/<id>.txt or .pdf notices (default psx/data)")
    ap.add_argument("--offline", action="store_true", help="never touch the network: read only the data folder")
    ap.add_argument("--config", default=os.path.join("psx", "config.json"),
                    help='your config; "psx_site": true lets the tool read the PSX site')
    ap.add_argument("--now", help='Karachi time to use as now, like "2026-10-29 16:00" (default: the clock). '
                    "A replay: it reads only the data folder")
    ap.add_argument("--metrics", metavar="FILE", help="merge one row per symbol into this JSON (psx/metrics.json)")
    ap.add_argument("--json", action="store_true", help="print everything as JSON")
    args = ap.parse_args(argv)
    if not args.symbols:
        ap.print_help()
        return 2
    try:
        today = to_pkt(parse_now(args.now)).date()
    except ValueError as exc:
        print("facts: %s" % exc, file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    offline = args.offline or bool(args.now)   # a replay with --now never asks the site: it only has today
    site = site_on(cfg) and not offline
    results, rows, failed = [], [], 0
    for raw in args.symbols:
        sym = re.sub(r"[^A-Z0-9-]", "", raw.upper())
        try:
            text, source = company_html(sym, args.data, cfg, offline)
            if text is None:
                if not args.offline:
                    source = source.replace("(--offline reads", "(a --now replay reads")
                raise FactsError(source)
            page = parse_company(text)
            if page["symbol"] and page["symbol"] != sym:
                raise FactsError("the saved page is the page of %s, not %s" % (page["symbol"], sym))
            result = analyse(page, today)
            notices = read_notices(page, args.data, today, result, site)
        except FactsError as exc:
            print("facts: %s: %s" % (sym or raw, exc), file=sys.stderr)
            failed += 1
            continue
        made = page_clock(text)
        results.append({"symbol": sym, "source": source, "page_made": utc_iso(pkt_to_utc(made)) if made else None,
                        "result": result, "notices": notices, "changes": changes(result)})
        rows.append(metrics_row(sym, result, notices))
        if not args.json:
            sys.stdout.write(render(sym, result, notices, today, source, made, site and source == "saved"))
            sys.stdout.write("\n")
    if args.json:
        print(json.dumps(results, indent=2))
    if args.metrics and rows:
        try:
            count = write_metrics(args.metrics, rows, today)
        except (FactsError, OSError) as exc:
            print("facts: %s" % exc, file=sys.stderr)
            return 2
        if not args.json:
            print("wrote %s (%d compan%s)" % (args.metrics, count, "y" if count == 1 else "ies"))
    return 0 if results and not failed else (1 if results else 2)


if __name__ == "__main__":
    sys.exit(main())
