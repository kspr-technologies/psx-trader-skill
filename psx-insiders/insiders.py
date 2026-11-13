#!/usr/bin/env python3
"""Insider trade notices on PSX for psx-insiders. Standard library only, Python 3.8.

Reads the "Others" tab of PSX company pages for the notices a director, the CEO, an
executive, their spouse or a substantial shareholder (10% or more) sends when they buy
or sell the company's shares. It also lists section 110 notices (someone went over 10% of
a company's shares; the notice can sit on the buyer's page and be about another company)
and buy-back reports (the company buying its own shares).

    python3 insiders.py index --symbols DEMO,SYS             # the notices on the company pages
    python3 insiders.py new --read                           # only what is new since the last check
    python3 insiders.py read 290210                          # read one notice by its doc id
    python3 insiders.py parse notice.txt --filed 2026-10-29  # rows from `pdftotext -layout` text

Where the data comes from:
  - Company pages: https://dps.psx.com.pk/company/<SYM> when the site is on (and the run is
    not --offline and not a --now replay), else, or when the site fails, the saved page
    <data>/company/<SYM>.html. Each tab of a page shows only its 5 newest rows, so check often.
  - Notice files: <data>/docs/<id>.txt (pdftotext -layout text), else <data>/docs/<id>.pdf
    (read with pdftotext from poppler, when it is installed), else the PDF from the site when
    it is on. Many notices are only a GIF image: then Claude reads the picture. With the site
    off, the user opens the link in a browser and saves the file (Claude never fetches it).
  - The PSX site only when psx/config.json has "psx_site": true (PSX's terms ban automated
    use without its written permission), never with --offline or --now. Plain GETs, 1.5 s apart.

What a notice is, and is not:
  - PSX Rule Book 5.6.4 (the notice title still says 5.6.1(d)) and the Securities Act
    2015, sections 101 to 106: the insider has 7 days to tell the company, and the company
    sends it on to PSX. So the notice on PSX can come more than 7 days after the trade.
    Reading it fast is not early.
  - It is a public notice. There is no inside information here.

State: `new` keeps the doc ids it has shown in psx/state/insiders.json, so each notice
shows once. Times in that file are UTC ("...Z"). Dates on PSX pages are Karachi dates.
"""

import argparse
import datetime
import gzip
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Copied verbatim from tests/psx_core.py (never edit a copy):
UA = "Mozilla/5.0 (compatible; psx-trader-skill/1.0)"
PSX = "https://dps.psx.com.pk"
PAUSE = 1.5         # seconds between two requests to the PSX site, at least
SLEEP = time.sleep  # tests swap this out
KARACHI_HOURS = 5   # Karachi time is UTC+5 all year: no daylight saving
SITE_OFF = ('The PSX site is off for this tool: save %s with your browser, or set "psx_site": true '
            'in psx/config.json if PSX has given you permission.')
FLAG_RE = re.compile(r"(?:XD|XB|XR)+$")  # ex-date flags at the end of a closing-file short name

LATE_DAYS = 7       # PSX Rule Book 5.6.4 and Securities Act 2015 s.101: tell the company within 7 days
FIRST_DAYS = 30     # the first `new` run shows the notices posted in the last 30 days
TITLE_WIDTH = 60    # titles are cut to this in the index lines (the JSON keeps them whole)
# (kind, label, title pattern) for rows of a company page's Others tab
KINDS = (
    ("insider", "insider notice", re.compile(r"disclosure\s+of\s+interest", re.I)),
    ("holder", "section 110 notice (someone went over 10%)", re.compile(r"\b(?:section|u/s)\s*110\b", re.I)),
    ("buyback", "buy-back report", re.compile(r"(?=.*\b(?:report\w*|purchased)\b).*\bbuy[\s-]?back", re.I | re.S)),
)
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")
MONTH_WORDS = dict([(m.lower(), i + 1) for i, m in enumerate(MONTHS)] +
                   [(m[:3].lower(), i + 1) for i, m in enumerate(MONTHS)] + [("sept", 9)])
_MON = r"(?P<mon>[A-Za-z]{3,9})"
DATE_RES = (        # dates in notices: day first when it is all numbers (Pakistan)
    re.compile(r"(?<![\d.,])(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})(?![\d])"),
    re.compile(r"(?<![\w.,])(?P<d>\d{1,2})(?:st|nd|rd|th)?[\s./-]{0,3}" + _MON +
               r"\.?[\s.,/-]{0,3}(?P<y>\d{4}|\d{2})(?![\d])"),
    re.compile(r"(?<![A-Za-z])" + _MON + r"\.?\s{0,3}(?P<d>\d{1,2})(?:st|nd|rd|th)?\s{0,2},?\s{0,3}(?P<y>\d{4})(?![\d])"),
    re.compile(r"(?<![\w.,/-])(?P<d>\d{1,2})[/.-](?P<m>\d{1,2})[/.-](?P<y>\d{4}|\d{2})(?![\d]|\.\d)"),
)
NATURE = re.compile(r"\b(buy|bought|purchased?|sell|sold|sale|gift(?:ed)?|transfer(?:red)?|transmission|"
                    r"pledged?|inherit(?:ed|ance)?)\b", re.I)
SIDES = {"buy": "buy", "bought": "buy", "purchase": "buy", "purchased": "buy",
         "sell": "sell", "sold": "sell", "sale": "sell"}
MARKET = re.compile(r"\b(ready|regular|spot|futures?|odd[\s-]?lot|off[\s-]?market|negotiated(?:\s+deal)?)\b", re.I)
FORM = re.compile(r"\b(CDC|CDS|physical)\b", re.I)
# a number in a table line (group 2: a percent sign), or an empty cell written "-", "N/A" or "Nil" (group 3)
TOKEN = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(\s?%)?(?![\w.])|(?<!\S)(-{1,2}|N/?A|Nil)(?!\S)", re.I)
NOTE = ("Fast, not early: the insider has 7 days to tell the company. The notice on PSX can come later.\n"
        "These are public PSX notices. There is no inside information here.\n"
        "A notice is a reason to look at a chart, never a setup.")
# with the site off, the user opens a PSX file in their browser: Claude never fetches it
ASK_USER = ("Ask the user to open %s in their browser and save it as %s, or paste a screenshot. "
            "Do not fetch it yourself.")
# a section 110 notice can sit on the buyer's page and be about another company's shares
WHOSE = "Check whose shares it names: it can be %s buying shares of another company."
CLOSED = ("This trade date is inside the closed period %s announced (%s).\n"
          "PSX Rule Book 5.6.4 says directors, the CEO and executives may not deal in a closed period.\n"
          "Check the notice.")


class InsidersError(Exception):
    pass


# ---------------------------------------------------------------- copied from tests/psx_core.py
# These helpers are verbatim copies (same name, body and docstring). Never edit them here.

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


def money(x, decimals=2):
    """Rupees for people: "Rs 1,234.56" or "-Rs 1,234.56"; decimals=0 gives "Rs 5,000"."""
    try:
        v = Decimal(str(x)).quantize(Decimal(1).scaleb(-int(decimals)), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise ValueError("not an amount of money: %r" % (x,))
    if not v.is_finite():
        raise ValueError("not an amount of money: %r" % (x,))
    return "%sRs %s" % ("-" if v < 0 else "", format(abs(v), ",f"))


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


# ---------------------------------------------------------------- dates in notices

def find_dates(text):
    """Every date in a piece of notice text -> [(start, end, datetime.date)], left to right.

    Reads "Oct. 07, 2026", "October 7th, 2026", "21-09-26", "21/09/2026", "2026-10-29",
    "05-October-2026", "11-Sep-2026" and "7 October 2026". A date written all in numbers
    is day first, as in Pakistan: "05/10/2026" is 5 October. A date that does not exist
    that way ("10/29/2026") is not read at all; it is never turned around."""
    s = str(text or "")
    found = []
    for rx in DATE_RES:
        for m in rx.finditer(s):
            g = m.groupdict()
            if g.get("mon") is not None:
                month = MONTH_WORDS.get(g["mon"].lower())
                if month is None:
                    continue
            else:
                month = int(g["m"])
            year = int(g["y"])
            if year < 100:
                year += 2000
            try:
                day = datetime.date(year, month, int(g["d"]))
            except ValueError:
                continue
            found.append((m.start(), m.end(), day))
    found.sort(key=lambda x: (x[0], -x[1]))
    out, end = [], -1
    for start, stop, day in found:
        if start >= end:
            out.append((start, stop, day))
            end = stop
    return out


def notice_date(text):
    """The first date in a piece of text as "YYYY-MM-DD", or None (see find_dates)."""
    dates = find_dates(text)
    return dates[0][2].isoformat() if dates else None


def date_arg(text, flag):
    """A YYYY-MM-DD command-line date -> datetime.date. None stays None."""
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(str(text).strip())
    except ValueError:
        raise InsidersError("%s wants a date like 2026-10-29" % flag)


def span_text(a, b):
    """Two dates -> "October 27 to November 3, 2026" (the year once when both share it)."""
    if a.year == b.year:
        return "%s %d to %s %d, %d" % (MONTHS[a.month - 1], a.day, MONTHS[b.month - 1], b.day, b.year)
    return "%s %d, %d to %s %d, %d" % (MONTHS[a.month - 1], a.day, a.year, MONTHS[b.month - 1], b.day, b.year)


def id_key(doc_id):
    """Sort key for doc ids: by number (they grow over time), odd ones last."""
    s = str(doc_id or "")
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


# ---------------------------------------------------------------- files

def load_json(path, what="file"):
    """A JSON file -> its data, or None when there is no file. Bad JSON is an error."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        raise InsidersError("%s (%s) is not valid JSON: %s" % (path, what, exc))


def write_json(path, data):
    """Write JSON through a temp file, so a crash never leaves half a file."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(path + ".tmp", path)


def load_config(path):
    cfg = load_json(path, "config")
    if cfg is None:
        return {}
    if not isinstance(cfg, dict):
        raise InsidersError("%s should hold one JSON object" % path)
    return cfg


def held_symbols(path):
    """The symbols you hold in psx/portfolio.json (positions[].ticker or .symbol, shares above 0)."""
    pf = load_json(path, "portfolio") or {}
    out = set()
    for p in (pf.get("positions") or []) if isinstance(pf, dict) else []:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("ticker") or p.get("symbol") or "").strip().upper()
        shares = p.get("shares", p.get("qty"))
        if sym and (shares is None or (num(shares) or 0) > 0):
            out.add(sym)
    return out


def pick_symbols(args, root):
    """(symbols, where they came from): --symbols, else your watchlist hits, plan and
    holdings, else every saved company page in <data>/company/."""
    if getattr(args, "symbols", None):
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        return list(dict.fromkeys(syms)), "--symbols"
    syms = []
    wl = load_json(args.watchlist, "watchlist") or {}
    for h in (wl.get("hits") or []) if isinstance(wl, dict) else []:
        sym = h.get("symbol") if isinstance(h, dict) else h
        if sym:
            syms.append(str(sym).strip().upper())
    plan = load_json(args.plan, "plan") or {}
    if isinstance(plan, dict) and plan.get("symbol"):
        syms.append(str(plan["symbol"]).strip().upper())
    syms += sorted(held_symbols(args.portfolio))
    if syms:
        return list(dict.fromkeys(syms)), "your watchlist, plan and portfolio"
    folder = os.path.join(root, "company")
    saved = sorted(f[:-5].upper() for f in os.listdir(folder) if f.lower().endswith(".html")) \
        if os.path.isdir(folder) else []
    return saved, "the saved company pages"


def match_kind(title):
    """The kind of an Others row: "insider", "holder" (section 110) or "buyback"; else None."""
    for kind, _, rx in KINDS:
        if rx.search(title or ""):
            return kind
    return None


def kind_label(kind):
    return dict((k, label) for k, label, _ in KINDS).get(kind, kind or "notice")


# ---------------------------------------------------------------- company pages

def page_made(text):
    """When a PSX page was made: the clock in its top bar ("Oct 30, 2026 1:39 AM") -> naive
    Karachi time, or None. The quote's "As of" time can be days older (no trade that day)."""
    for label in re.findall(r'<div class="topbar__status__label">([^<]*)</div>', text or ""):
        made = page_time(html_text(label))
        if made:
            return made
    return None


def read_pages(symbols, root, cfg, offline):
    """Each symbol's company page -> ({SYM: parse_company() page plus "_source" and "_made"},
    [(SYM, problem)]). The site first when it is on and not offline (company_html); when the
    site fails and a saved page is used instead, a problem line says so."""
    pages, problems = {}, []
    for sym in symbols:
        text, src = company_html(sym, root, cfg, offline)
        if text is None:
            problems.append((sym, src))
            continue
        page = parse_company(text)
        if not page.get("symbol") and not page.get("name"):
            problems.append((sym, "the page for %s does not look like a PSX company page" % sym))
            continue
        if page.get("symbol") and page["symbol"] != sym:
            problems.append((sym, "%s holds the page of %s: save the right page there"
                             % (os.path.join(root, "company", sym + ".html"), page["symbol"])))
            continue
        if src == "saved" and site_on(cfg) and not offline:
            problems.append((sym, "the PSX site could not be read, so this uses the saved page %s"
                             % os.path.join(root, "company", sym + ".html")))
        page["_source"], page["_made"] = src, page_made(text)
        pages[sym] = page
    return pages, problems


def notice_rows(pages, since=None):
    """The insider, section 110 and buy-back rows of the pages' Others tabs, newest first."""
    out = []
    for sym, page in pages.items():
        for r in (page.get("announcements") or {}).get("Others") or []:
            kind = match_kind(r.get("title"))
            if not kind or (since and (r.get("date") or "") < since):
                continue
            out.append(dict(r, symbol=sym, company=page.get("name"), kind=kind, source=page.get("_source")))
    out.sort(key=lambda r: (r.get("date") or "", id_key(r.get("doc_id")), r["symbol"]), reverse=True)
    keys, unique = set(), []
    for r in out:                       # one row per doc id, even when a page lists it twice
        if notice_key(r) not in keys:
            keys.add(notice_key(r))
            unique.append(r)
    return unique


def notice_key(row):
    """What the state file remembers for a notice: its doc id (the PUCARS PostID)."""
    return str(row.get("doc_id") or "%s %s %s" % (row.get("symbol"), row.get("date"), row.get("title")))


def stale_pages(pages, today):
    """Saved pages made before today -> [(SYM, when it was made)]. When it was made is the
    page's top-bar clock; the quote's "As of" time only stands in when a page has no clock."""
    out = []
    for sym, p in sorted(pages.items()):
        made = p.get("_made") or p.get("as_of")
        if p.get("_source") == "saved" and made and made.date() < today:
            out.append((sym, made))
    return out


# ---------------------------------------------------------------- notice files

def pdf_to_text(path):
    """`pdftotext -layout` text of a PDF; None when pdftotext is not installed or fails."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-layout", path, "-"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8", "replace") if out.returncode == 0 else None


def local_text(root, doc_id):
    """A saved notice's text: <root>/docs/<id>.txt, else <id>.pdf through pdftotext. Else None."""
    base = os.path.join(root, "docs", str(doc_id))
    if os.path.isfile(base + ".txt"):
        with open(base + ".txt", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    if os.path.isfile(base + ".pdf"):
        return pdf_to_text(base + ".pdf")
    return None


def save_pdf(url, path):
    """GET a notice PDF from PSX and save it at path. -> None, or a plain reason it failed."""
    try:
        data = http_get(url, timeout=60)
    except urllib.error.HTTPError as e:
        try:
            e.close()
        except Exception:
            pass
        return "PSX answered HTTP %d for %s" % (e.code, url)
    except (urllib.error.URLError, OSError) as e:
        return "could not reach PSX for %s: %s" % (url, getattr(e, "reason", e))
    if not data.startswith(b"%PDF"):
        return "%s did not come back as a PDF" % url
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "wb") as fh:
        fh.write(data)
    os.replace(path + ".tmp", path)
    return None


def read_notice(row, root, cfg=None, offline=False):
    """The text of one notice -> (text, the file it came from) or (None, what to do).

    <root>/docs/<id>.txt first (an empty one is a scan), else <root>/docs/<id>.pdf through
    pdftotext, else (site on, not offline) the notice's PDF from PSX, saved as
    <root>/docs/<id>.pdf. With no text, the message says how to read it: open the PDF, or
    the GIF image. With the site off, Claude never opens a PSX link: the user saves the file."""
    doc = str(row.get("doc_id") or "")
    base = os.path.join(root, "docs", doc)
    scan = ""
    if doc and os.path.isfile(base + ".txt"):
        with open(base + ".txt", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if text.strip():
            return text, base + ".txt"
        scan = "%s is empty: the notice is most likely a scan. " % (base + ".txt")
    url, why = row.get("pdf") or row.get("attachment"), None
    if doc and url and not os.path.isfile(base + ".pdf"):
        if not site_on(cfg):
            why = ASK_USER % (url, base + ".pdf")
        elif offline:
            why = "No saved file at %s (--offline reads only the data folder): save %s there." % (base + ".pdf", url)
        else:
            why = save_pdf(url, base + ".pdf")
    if doc and os.path.isfile(base + ".pdf"):
        text = pdf_to_text(base + ".pdf")
        if text is None:
            return None, scan + ("%s is saved, but pdftotext is not installed or could not read it: open the PDF "
                                 "and read the table (Claude can read PDFs)." % (base + ".pdf"))
        if not text.strip():
            return None, scan + ("%s has no text (a scanned page): open it and read the table (Claude can read "
                                 "PDFs)." % (base + ".pdf"))
        return text, base + ".pdf"
    gif = next((base + x for x in ("-1.gif", ".gif") if doc and os.path.isfile(base + x)), None)
    if gif:
        return None, scan + "Image only, no text to read: read the saved image %s (Claude can read images)." % gif
    if why:
        if row.get("image") and site_on(cfg):
            why += " Or open %s and read the table (Claude can read images)." % row["image"]
        return None, scan + why
    if row.get("image") and not site_on(cfg):
        return None, scan + "Image only, no text to read. %s Do not guess its rows." % (
            ASK_USER % (row["image"], base + ".gif"))
    if row.get("image"):
        return None, scan + ("Image only, no text to read: open %s and read the table (Claude can read images). "
                             "Do not guess its rows." % row["image"])
    return None, scan + "No text, PDF or image link for doc %s." % (doc or "?")


# ---------------------------------------------------------------- closed periods

def _date_pair(s, last=False):
    """The first (or last) two dates in s joined by "to", "till", "until", "through", "and" or a dash."""
    dates = find_dates(s)
    pairs = []
    for (_, a1, a), (b0, _, b) in zip(dates, dates[1:]):
        if a <= b and re.match(r"^\s*,?\s*(?:to|till|until|up\s*to|upto|through|and|-|–)\s*$", s[a1:b0], re.I):
            pairs.append((a, b))
    if not pairs:
        return None
    return pairs[-1] if last else pairs[0]


def find_closed_periods(text):
    """Closed periods named in a board meeting notice -> [(first day, last day)]: the two
    dates nearest after the words "closed period", else just before them."""
    flat = " ".join(str(text or "").split())
    out = []
    for m in re.finditer(r"closed\s+period", flat, re.I):
        pair = _date_pair(flat[m.end():m.end() + 300]) or _date_pair(flat[max(0, m.start() - 300):m.start()], True)
        if pair and pair not in out:
            out.append(pair)
    return out


def meeting_notices(page, root):
    """One company's board meeting notices (the rows of its Board Meetings tab, and any row
    whose title names a closed period) -> ([{"from", "to", "doc_id"}]: the closed periods in
    their text saved in <root>/docs/, [doc ids with no text there, so not checked]).
    Nothing is fetched for this."""
    ann = (page or {}).get("announcements") or {}
    rows = list(ann.get("Board Meetings") or [])
    for tab in ("Others", "Financial Results"):
        rows += [r for r in ann.get(tab) or [] if re.search(r"closed\s+period", r.get("title") or "", re.I)]
    out, unread = [], []
    for r in rows:
        if not r.get("doc_id"):
            continue
        text = local_text(root, r["doc_id"])
        if not (text or "").strip():
            if r["doc_id"] not in unread:
                unread.append(r["doc_id"])
            continue
        for a, b in find_closed_periods(text):
            p = {"from": a.isoformat(), "to": b.isoformat(), "doc_id": r["doc_id"]}
            if p not in out:
                out.append(p)
    return out, unread


def closed_periods(page, root):
    """[{"from", "to", "doc_id"}] of one company: the closed periods in the text of its
    board meeting notices saved in <root>/docs/ (see meeting_notices)."""
    return meeting_notices(page, root)[0]


# ---------------------------------------------------------------- reading a notice table

def _numbers(text):
    """The cells of a piece of a table line -> (numbers as text in order, None for an empty
    cell such as "-", and the first percent or None)."""
    clean = re.sub(r"\b(?:Rs|PKR)\b\.?", " ", text, flags=re.I)
    plain, pct = [], None
    for m in TOKEN.finditer(clean):
        if m.group(3):
            plain.append(None)
        elif m.group(2):
            if pct is None:
                pct = num(m.group(1))
        else:
            plain.append(m.group(1))
    return plain, pct


def parse_row_line(line):
    """One line of a notice table -> a trade row, {"_bad": True} for a line that looks like
    a trade but does not read, or None for any other line.

    A trade line holds a date, a nature (Buy, Sell, ...) and the number of shares, in
    either PSX layout: Form-29 (Sr. No., name, form, market, date, nature, shares, rate,
    shares held after, percent) or a company's own table (name, date, nature, shares,
    rate, market)."""
    dates = find_dates(line)
    if not dates:
        return None
    start, end, day = dates[0]
    before, after = line[:start], line[end:]
    nat = NATURE.search(after)
    rest = after[nat.end():] if nat else after
    if not nat:
        nat = NATURE.search(before)
        if not nat:
            return None
        before = before[:nat.start()]
    for a, b, _ in reversed(find_dates(rest)):      # a second date column is not a number of shares
        rest = rest[:a] + " " + rest[b:]
    plain, pct = _numbers(rest)
    if not any(plain):
        return None
    plain += [None] * 4
    shares = num(plain[0])
    if shares is None or shares <= 0 or shares != int(shares):
        return {"_bad": True}
    rate, held = num(plain[1]), num(plain[2])
    if pct is None:
        pct = num(plain[3])
    cells = [c for c in re.split(r"\s{2,}", re.sub(r"^\s*\d{1,3}[.)]?(?=\s)", "", before).strip()) if c]
    cells = [c for c in cells if not (FORM.fullmatch(c) or MARKET.fullmatch(c))]
    if not cells:
        return {"_bad": True}
    name = re.sub(r"(?:\s+(?:%s|%s))+\s*$" % (FORM.pattern, MARKET.pattern), "", cells[0], flags=re.I).strip()
    role, still_open = None, False
    inline = re.findall(r"\(([^()]+)\)", name)
    if inline:
        role = "; ".join(x.strip() for x in inline)
        name = re.sub(r"\s*\([^()]*\)", "", name).strip()
    elif "(" in name:                       # "Mrs. Sara Tariq (Spouse of": the role ends on the next line
        name, _, part = name.partition("(")
        name, role, still_open = name.strip(), part.strip(), True
    elif len(cells) > 1 and not re.search(r"\d", cells[1]):
        role = cells[1].strip()
    market, form = MARKET.search(line), FORM.search(line)
    form = form.group(1) if form else None
    word = nat.group(1)
    row = {"name": name, "role": role, "date": day.isoformat(), "nature": word,
           "side": SIDES.get(word.lower(), word.lower()), "shares": int(shares),
           "rate": rate if rate and rate > 0 else None,
           "market": market.group(1).capitalize() if market else None,
           "form": (form.capitalize() if form.lower() == "physical" else form.upper()) if form else None,
           "holding_after": ({"shares": int(held), "pct": pct} if held is not None and held == int(held) else None)}
    if still_open:
        row["_open"] = True
    return row


def looks_like_trade(s):
    """A line with a nature word and numbers like shares and a rate (a comma or a decimal point)."""
    plain = [x for x in _numbers(s)[0] if x]
    return bool(NATURE.search(s)) and len(plain) >= 2 and any("," in x or "." in x for x in plain)


def _more(cur, s):
    """A line under a trade row: its role in brackets, or more of the name. -> True when used."""
    if cur.get("_open"):
        cur["role"] = ("%s %s" % (cur["role"], s.rstrip(")").strip())).strip()
        if s.endswith(")"):
            del cur["_open"]
        return True
    roles = re.findall(r"\(([^()]+)\)", s)
    rest = re.sub(r"\s*\([^()]*\)", " ", s).strip()
    if roles:
        if not cur.get("role"):
            cur["role"] = "; ".join(x.strip() for x in roles)
        if rest and not re.search(r"\d", rest) and len(rest) <= 40:
            cur["name"] = "%s %s" % (cur["name"], rest)
        return True
    if s.startswith("(") and ")" not in s and not cur.get("role"):
        cur["role"], cur["_open"] = s[1:].strip(), True
        return True
    if not re.search(r"\d", s) and len(s) <= 40 and not re.search(r"\s{2,}", s):
        cur["name"] = "%s %s" % (cur["name"], s)
        return True
    return False


def letter_date(text):
    """The date the letter carries ("Dated: October 29, 2026", or a date alone on a line near
    the top) -> "YYYY-MM-DD" or None."""
    t, rx = str(text or ""), re.compile(r"\bDated?\s*:?\s*")
    m = rx.search(t)
    while m:
        d = find_dates(t[m.end():m.end() + 40])
        if d and d[0][0] == 0:
            return d[0][2].isoformat()
        m = rx.search(t, m.end())
    for line in [x.strip() for x in t.splitlines() if x.strip()][:25]:
        d = find_dates(line)
        if d and d[0][0] == 0 and d[0][1] >= len(line.rstrip(".,")):
            return d[0][2].isoformat()
    return None


def parse_notice_text(text, filed=None):
    """Rows from `pdftotext -layout` text of a PSX insider notice.

    -> {"layout": "form-29" or "own", "letter_date", "filed", "filed_from" ("posted",
    "letter" or None), "rows": [...], "skipped": [lines that look like trades but do not
    read]}. Each row: name, role (the words in brackets), date, nature, side ("buy",
    "sell", or the nature word), shares, rate, value (shares x rate, Rs), market, form,
    holding_after ({"shares", "pct"} or None), and when the filing day is known
    days_to_disclose and late (over 7 days). filed is the day PSX posted the notice
    (YYYY-MM-DD); without it the letter's own date is used and filed_from says "letter"."""
    rows, skipped, cur = [], [], None
    for raw in str(text or "").replace("\f", "\n").splitlines():
        s = raw.strip()
        if not s:
            cur = None
            continue
        row = parse_row_line(raw)
        if row and not row.get("_bad"):
            rows.append(row)
            cur = row
            continue
        if row is None and cur is not None and _more(cur, s):
            continue
        cur = None
        if row or looks_like_trade(s):
            skipped.append(s)
    for r in rows:
        r.pop("_open", None)
        r["name"] = " ".join(r["name"].split())
        if r.get("role"):
            r["role"] = " ".join(r["role"].split())
    form29 = re.search(r"FORM[\s-]*29\b|Cumulative\s+Shareholding|\bSr\.?\s*No\b", str(text or ""), re.I)
    when = letter_date(text)
    report = {"layout": "form-29" if form29 else "own", "letter_date": when,
              "filed": filed or when, "filed_from": "posted" if filed else ("letter" if when else None),
              "rows": rows, "skipped": skipped}
    finish_rows(report)
    return report


def finish_rows(report):
    """Value of each trade, and the days from the trade to the filing (late: over 7)."""
    filed = datetime.date.fromisoformat(report["filed"]) if report.get("filed") else None
    for r in report["rows"]:
        if r.get("rate") is not None:
            v = (Decimal(str(r["shares"])) * Decimal(str(r["rate"]))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            r["value"] = float(v)
        else:
            r["value"] = None
        if filed:
            days = (filed - datetime.date.fromisoformat(r["date"])).days
            r["days_to_disclose"] = days
            r["late"] = days > LATE_DAYS
    return report


# ---------------------------------------------------------------- context

def day_range(symbol, d, root, cache):
    """(low, high) of a symbol on day d from the closing file in <root>/mkt_summary/, or
    None. Only the data folder is read for this; nothing is fetched."""
    if d not in cache:
        try:
            rows = daily_rows(d, root, None, True)
        except ValueError:
            rows = None
        cache[d] = dict((r["symbol"], r) for r in rows or [])
    r = cache[d].get(symbol)
    if r and r.get("low") and r.get("high"):
        return r["low"], r["high"]
    return None


def add_context(report, symbol, page, root, held=()):
    """What is known around the rows: the company, whether you hold it, its closed periods
    (and the trades dated inside one) and each trade day's range from the closing file."""
    sym = str(symbol or "").strip().upper() or None
    periods, unread = meeting_notices(page, root) if page else ([], [])
    report.update(symbol=sym, you_hold=bool(sym and sym in held), closed_periods=periods)
    cache = {}
    for r in report["rows"]:
        hit = next((p for p in periods if p["from"] <= r["date"] <= p["to"]), None)
        if hit:
            r["closed_period"] = hit
        rng = day_range(sym, datetime.date.fromisoformat(r["date"]), root, cache) if sym and r.get("rate") else None
        if rng:
            r["day_range"] = {"low": rng[0], "high": rng[1], "inside": rng[0] <= r["rate"] <= rng[1]}
    if not sym:
        note = "Closed period: not checked (give --symbol to check it)."
    elif not page:
        note = "Closed period: not checked (no %s company page, so no board meeting notices)." % sym
    elif not periods:
        note = "Closed period: not checked (no %s board meeting notice text in docs/)." % sym
    elif not any(r.get("closed_period") for r in report["rows"]):
        note = "Closed period: %s announced %s. No trade here is inside it." % (
            sym, " and ".join(span_text(datetime.date.fromisoformat(p["from"]), datetime.date.fromisoformat(p["to"]))
                              for p in periods))
    else:
        note = None
    if periods and unread and not all(r.get("closed_period") for r in report["rows"]):
        # a missing flag must never quietly mean "outside": say which notices were not read
        note = ((note + " ") if note else "") + "Not checked: board meeting %s %s (no text in docs/)." % (
            "doc" if len(unread) == 1 else "docs", ", ".join(unread))
    report["closed_note"] = note
    return report


# ---------------------------------------------------------------- output

def plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def comma(n):
    return "{:,}".format(int(n))


def short(title, width=TITLE_WIDTH):
    """A title cut to about width characters at a space, with " ..." when cut."""
    t = " ".join(str(title or "").split())
    if len(t) <= width:
        return t
    return t[:width].rsplit(" ", 1)[0].rstrip(" ,;:") + " ..."


def describe_row(r, symbol=None, filed_from=None):
    """Plain lines for one trade: who, what, when, and the flags."""
    who = r["name"] + (" (%s)" % r["role"] if r.get("role") else "")
    verb = {"buy": "bought", "sell": "sold"}.get(r.get("side"))
    what = (" %s %s shares" % (verb, comma(r["shares"])) if verb else
            ": %s of %s shares" % (r["nature"].lower(), comma(r["shares"])))      # a gift, a transfer, ...
    at = " at %.2f" % r["rate"] if r.get("rate") is not None else ""
    out = ["  - %s%s%s on %s." % (who, what, at, r["date"])]
    bits = []
    if r.get("value") is not None:
        bits.append("Worth %s." % money(r["value"]))
    if r.get("market"):
        bits.append("%s market." % r["market"])
    h = r.get("holding_after")
    if h:
        bits.append("Holds %s shares after%s." % (comma(h["shares"]),
                                                  " (%.2f%%)" % h["pct"] if h.get("pct") is not None else ""))
    if bits:
        out.append("    " + " ".join(bits))
    days, said = r.get("days_to_disclose"), "The letter is dated" if filed_from == "letter" else "Posted"
    if days is not None and days < 0:
        out.append("    The trade date is after the %s date. Check the notice."
                   % ("letter" if filed_from == "letter" else "posting"))
    elif days == 0:
        out.append("    %s the same day as the trade." % said)
    elif days is not None:
        out.append("    %s %s after the trade.%s" % (said, plural(days, "day"),
                                                    " Late: over %d days." % LATE_DAYS if r.get("late") else ""))
    cp = r.get("closed_period")
    if cp:
        span = span_text(datetime.date.fromisoformat(cp["from"]), datetime.date.fromisoformat(cp["to"]))
        out += ["    " + x for x in (CLOSED % (symbol or "the company", span)).split("\n")]
    rng = r.get("day_range")
    if rng and not rng["inside"]:
        out.append("    The rate is outside %s's range that day (%.2f to %.2f). It may be an off-market deal, "
                   "or a misread. Check the notice." % (symbol, rng["low"], rng["high"]))
    return out


def render_report(report, source, hold=False):
    """The rows of one notice as plain lines."""
    rows = report["rows"]
    head = "Read from %s: %s." % (source, plural(len(rows), "trade"))
    if report.get("letter_date") and report.get("filed_from") != "posted":
        head += " Letter dated %s." % report["letter_date"]
    out = [head]
    if hold and report.get("you_hold"):
        out.append("  You hold %s." % report["symbol"])
    for r in rows:
        out += describe_row(r, report.get("symbol"), report.get("filed_from"))
    if not rows:
        out.append("  No trade rows found: open the notice and read the table.")
    if report.get("skipped"):
        out.append("  Lines that look like trades but did not read: %d. Check the notice." % len(report["skipped"]))
    if rows and report.get("closed_note"):
        out.append("  " + report["closed_note"])
    return out


def link_lines(row, pad="    "):
    """The notice's links: PDF, attachment and GIF image."""
    return ["%s%s %s" % (pad, label, row[key]) for label, key in
            (("PDF", "pdf"), ("Attachment", "attachment"), ("GIF", "image")) if row.get(key)]


def render_index(rows):
    out = []
    for r in rows:
        out.append("%s  %-7s doc %-7s %s: %s" % (r.get("date") or "?", r["symbol"], r.get("doc_id") or "?",
                                                 kind_label(r["kind"]), short(r.get("title"))))
        out += link_lines(r)
        if r["kind"] == "holder":
            out.append("    " + WHOSE % r["symbol"])
    return out


def notice_head(row, held=(), new=True):
    """The first lines about one notice: what, whose, which doc, when, the links."""
    who = row.get("symbol") or "?"
    if row.get("company"):
        who += " (%s)" % row["company"]
    label = kind_label(row.get("kind"))
    label = "NEW " + label if new else label[:1].upper() + label[1:]
    out = ["%s: %s, doc %s, posted %s." % (label, who, row.get("doc_id") or "?", row.get("date") or "?")]
    out += link_lines(row, "  ")
    if row.get("kind") == "holder":
        out.append("  " + WHOSE % (row.get("symbol") or "this company"))
    if row.get("symbol") in held:
        out.append("  You hold %s." % row["symbol"])
    return out


def footer(problems, stale=()):
    """Pages that could not be read, and saved pages made before today (stale_pages)."""
    out = ["%s: %s" % (sym, why) for sym, why in problems]
    days = {}
    for sym, made in stale:
        days.setdefault(made.date(), []).append(sym)
    if days:
        out.append("Saved pages from before today (%s). Newer notices are not in them." % "; ".join(
            "%s: %s" % (day_label(d), ", ".join(sorted(syms))) for d, syms in sorted(days.items(), reverse=True)))
    return out


# ---------------------------------------------------------------- state

def load_state(path):
    """psx/state/insiders.json -> {"seen": [doc ids], "last_check": "...Z" or None}."""
    st = load_json(path, "state")
    if not isinstance(st, dict):
        st = {}
    st["seen"] = [str(x) for x in st.get("seen") or []]
    st.setdefault("last_check", None)
    return st


def new_since(rows, state, today, first_days=FIRST_DAYS):
    """Rows not seen before. The first run (no last_check) shows only the notices posted
    in the last first_days days, so you are not flooded with old ones."""
    seen = set(state.get("seen") or [])
    fresh = [r for r in rows if notice_key(r) not in seen]
    if state.get("last_check") is None:
        cutoff = (today - datetime.timedelta(days=first_days)).isoformat()
        fresh = [r for r in fresh if (r.get("date") or "") >= cutoff]
    return fresh


def when_text(stamp):
    """A "...Z" time from the state file -> "Thu 2026-10-29 4:00 PM PKT"."""
    t = parse_utc(stamp)
    if t is None:
        return "the last check"
    k = to_pkt(t)
    return "%s %s PKT" % (day_label(k), clock_label(k))


def read_one(row, root, cfg, offline, held, page):
    """Read one notice -> (report or None, how it was read or what to do, plain lines)."""
    text, how = read_notice(row, root, cfg, offline)
    if text is None:
        return None, how, [how]
    if row.get("kind") not in (None, "insider"):
        return None, how, ["Text: %s. This tool reads only insider tables, so read it yourself." % how]
    report = parse_notice_text(text, row.get("date"))
    add_context(report, row.get("symbol"), page, root, held)
    report["text_from"] = how
    return report, how, render_report(report, how)


# ---------------------------------------------------------------- CLI

def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    def common(p, many=True):
        p.add_argument("--data", default="psx/data", help="the data folder: company/, docs/, mkt_summary/")
        p.add_argument("--config", default="psx/config.json", help='"psx_site": true lets the tool use the PSX site')
        p.add_argument("--offline", action="store_true", help="never use the network: read only the data folder")
        p.add_argument("--portfolio", default="psx/portfolio.json", help="flags notices on stocks you hold")
        p.add_argument("--json", action="store_true")
        if many:
            p.add_argument("--symbols", help="DEMO,SYS (default: your watchlist, plan and portfolio, "
                                             "else every saved company page)")
            p.add_argument("--watchlist", default="psx/watchlist.json")
            p.add_argument("--plan", default="psx/plan.json")

    p = sub.add_parser("index", help="list the notices on the company pages")
    common(p)
    p.add_argument("--since", help="only notices posted on or after YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--now", help='the time for a replay, like "2026-10-29 16:00" (Karachi time); '
                                 'a replay reads only the data folder')

    p = sub.add_parser("new", help="notices not seen at the last check")
    common(p)
    p.add_argument("--since", help="only notices posted on or after YYYY-MM-DD")
    p.add_argument("--now", help='the time for a replay, like "2026-10-29 16:00" (Karachi time); '
                                 'a replay reads only the data folder')
    p.add_argument("--state", default="psx/state/insiders.json")
    p.add_argument("--dry-run", action="store_true", help="do not update the state file")
    p.add_argument("--read", action="store_true", help="also read each new notice")

    p = sub.add_parser("read", help="read one notice by its doc id")
    p.add_argument("doc_id")
    common(p)
    p.add_argument("--filed", help="the day PSX posted it, YYYY-MM-DD (default: from the page)")

    p = sub.add_parser("parse", help="rows from pdftotext -layout text of a notice")
    p.add_argument("text_file")
    common(p, many=False)
    p.add_argument("--filed", help="the day PSX posted it, YYYY-MM-DD (default: the letter's date)")
    p.add_argument("--symbol", help="the company: checks its closed period, your holdings and the day's range")
    return ap


def main(argv=None):
    args = _parser().parse_args(argv)
    if not args.cmd:
        _parser().print_help()
        return 2
    try:
        return _run(args)
    except (InsidersError, OSError) as exc:
        print("insiders: %s" % exc, file=sys.stderr)
        return 2


def _say(lines, json_data=None, args=None):
    if args is not None and args.json:
        print(json.dumps(json_data, indent=2))
    else:
        sys.stdout.write("\n".join(lines) + "\n")


def _run(args):
    held = held_symbols(args.portfolio)
    cfg = load_config(args.config)
    root = args.data
    if args.cmd == "parse":
        return _parse_cmd(args, cfg, held)
    if args.cmd == "read":
        return _read_cmd(args, cfg, held)
    try:
        now = parse_now(args.now)
    except ValueError as exc:
        raise InsidersError(str(exc))
    offline = args.offline or bool(args.now)    # a --now replay never fetches: the site only has today
    today = to_pkt(now).date()
    since = date_arg(args.since, "--since")
    symbols, _ = pick_symbols(args, root)
    if not symbols:
        print("No symbols to check: pass --symbols DEMO,SYS, run psx-scanner (psx/watchlist.json), or save "
              "company pages in %s." % os.path.join(root, "company"))
        return 1
    pages, problems = read_pages(symbols, root, cfg, offline)
    rows = notice_rows(pages, since.isoformat() if since else None)
    stale = stale_pages(pages, today)
    if args.json:
        for sym, why in problems:
            print("%s: %s" % (sym, why), file=sys.stderr)

    if args.cmd == "index":
        picked = rows[:args.limit]
        lines = render_index(picked)
        if not picked:
            lines.append("No insider, section 110 or buy-back notice on the Others tab of %s. Each tab shows "
                         "only its 5 newest rows." % (", ".join(sorted(pages)) or "any page read"))
        _say(lines + footer(problems, stale) + NOTE.split("\n"), picked, args)
        return 0 if picked else 1

    state = load_state(args.state)
    first = state.get("last_check") is None
    fresh = new_since(rows, state, today)
    lines, reports = [], []
    if first:
        lines.append("First run: notices posted since %s (the last %d days). Older ones are marked as seen."
                     % ((today - datetime.timedelta(days=FIRST_DAYS)).isoformat(), FIRST_DAYS))
    if not fresh:
        lines.append("Nothing new %s." % ("in the last %d days" % FIRST_DAYS if first
                                          else "since " + when_text(state.get("last_check"))))
    for r in fresh:
        if lines:
            lines.append("")
        lines += notice_head(r, held)
        if args.read:
            report, how, more = read_one(r, root, cfg, offline, held, pages.get(r["symbol"]))
            reports.append({"notice": r, "report": report, "read": how})
            lines += more
    if fresh:
        lines.append("")
    _say(lines + footer(problems, stale) + NOTE.split("\n"),
         {"new": fresh, "first_run": first, "reports": reports,
          "problems": [{"symbol": s, "why": w} for s, w in problems]}, args)
    if not args.dry_run:
        seen = set(state["seen"]) | set(notice_key(r) for r in rows)
        state["seen"] = sorted(seen, key=id_key)
        state["last_check"] = utc_iso(now)
        write_json(args.state, state)
    return 0 if fresh else 1


def _parse_cmd(args, cfg, held):
    with open(args.text_file, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    filed = date_arg(args.filed, "--filed")
    report = parse_notice_text(text, filed.isoformat() if filed else None)
    page, why = None, None
    if args.symbol:
        page_text, why = company_html(args.symbol, args.data, cfg, args.offline)
        page = parse_company(page_text) if page_text is not None else None
    add_context(report, args.symbol, page, args.data, held)
    lines = render_report(report, args.text_file, hold=True)
    if page is None and why:
        lines.append("  %s: %s" % (report["symbol"], why))
    _say(lines + NOTE.split("\n"), report, args)
    return 0 if report["rows"] else 1


def _read_cmd(args, cfg, held):
    root, doc = args.data, str(args.doc_id).strip()
    if not re.match(r"^[0-9A-Za-z_-]+$", doc):
        raise InsidersError("a doc id is a number like 290210")
    filed = date_arg(args.filed, "--filed")
    symbols, _ = pick_symbols(args, root)
    pages, problems = read_pages(symbols, root, cfg, args.offline)
    row = next((r for r in notice_rows(pages) if str(r.get("doc_id")) == doc), None)
    for sym, page in sorted(pages.items()):
        for tab_rows in (page.get("announcements") or {}).values():
            for r in tab_rows:
                if row is None and str(r.get("doc_id")) == doc:
                    row = dict(r, symbol=sym, company=page.get("name"), kind=match_kind(r.get("title")),
                               source=page.get("_source"))
    found = row is not None
    if row is None:
        row = {"doc_id": doc, "symbol": symbols[0] if args.symbols and len(symbols) == 1 else None,
               "kind": "insider", "date": None, "title": None, "pdf": None, "image": None, "attachment": None}
    if filed:
        row["date"] = filed.isoformat()
    lines = notice_head(row, held, new=False) if found else ["Doc %s is not on the company pages read." % doc]
    if found and row.get("kind") is None:
        text = local_text(root, doc)
        periods = find_closed_periods(text or "")
        lines.append("It is not an insider notice: %s." % (row.get("title") or "?"))
        if periods:
            lines.append("Its text names a closed period: %s." % " and ".join(span_text(a, b) for a, b in periods))
        _say(lines + NOTE.split("\n"), {"notice": row, "closed_periods": [
            {"from": a.isoformat(), "to": b.isoformat()} for a, b in periods]}, args)
        return 0
    report, how, more = read_one(row, root, cfg, args.offline, held, pages.get(row.get("symbol")))
    lines += more
    if not found:
        lines += footer(problems)
    _say(lines + NOTE.split("\n"), {"notice": row if found else None, "report": report, "read": how}, args)
    if report is not None:
        return 0 if report["rows"] else 1
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
