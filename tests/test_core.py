#!/usr/bin/env python3
"""Tests for psx_core.py, the canonical PSX helpers. Standard library only.

    cd tests && python3 -m unittest test_core -v

psx_core.py (next to this file) is the source every skill script copies its helpers
from. Real PSX files never ship with this pack (PSX bans sharing its data).

The real-data tests were written for one snapshot of real PSX files that is not shipped:
the 70 sessions to 2026-10-29 and 21 company pages, saved when this pack was built. They
check exact counts from that snapshot, so other files will not pass them. Without the
snapshot (PSX_FIXTURES) they skip. Nothing here touches the network: urlopen is swapped
for a guard that fails the test, and fake answers are given where needed.
"""

import ast
import calendar
import datetime
import dis
import gzip
import http.client
import inspect
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import psx_core as core  # noqa: E402

FIX = os.environ.get("PSX_FIXTURES") or os.path.join(HERE, "..", "psx-fixtures")
PROBE404 = os.path.join(FIX, "404", "2026-10-24.Z.status404")  # a saved PSX "not found" page
HAVE_FIX = os.path.isdir(os.path.join(FIX, "mkt_summary"))
SRC = os.path.join(HERE, "psx_core.py")
D = datetime.date
DT = datetime.datetime
T = datetime.time
THU = D(2026, 10, 29)    # a normal Mon to Thu session
FRI = D(2026, 10, 30)    # a Friday with the Juma break
RAM_MON = D(2026, 3, 2)  # Ramadan, Monday
RAM_FRI = D(2026, 2, 20)  # Ramadan, Friday
IMPORTED = {"bisect", "csv", "datetime", "gzip", "html", "io", "json", "os", "re", "time", "urllib", "zipfile",
            "Decimal", "InvalidOperation", "ROUND_FLOOR", "ROUND_HALF_UP"}
_REAL_URLOPEN = urllib.request.urlopen


def _guard(*a, **k):
    raise AssertionError("a test tried to use the network")


def setUpModule():
    urllib.request.urlopen = _guard


def tearDownModule():
    urllib.request.urlopen = _REAL_URLOPEN


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def zip_bytes(name, data):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, data)
    return buf.getvalue()


def put(data, at, piece):
    """data with `piece` written over it at byte `at` (to break a file on purpose)."""
    b = bytearray(data)
    b[at:at + len(piece)] = piece
    return bytes(b)


def lis(day="29OCT2026"):
    return ("%s|OGDC|0820|Oil & Gas DevXD|206.0|207.99|203|206.21|1357900|203.68|||\r\n"
            "%s|DEMO|0805|Demo Ind|100.2|101.8|99.9|101.5|1,234,567|100.00|||\r\n"
            "%s|ZERO|0805|Zero Trade|0|0|0|50.00|10|50.00|||\r\n"
            "%s|JLICL|0812|Jubilee LifeXDXB|400|410|395|405|1000|398|||\r\n"
            "%s|OGDC-NOV|40|OGDC-NOV|0|0|0|307.5|0|306.1|||\r\n" % ((day,) * 5))


def day_code(d):
    return "%02d%s%d" % (d.day, ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                 "DEC")[d.month - 1], d.year)


def utf16_names(records):
    """The symbol name file format: UTF-16BE, a BOM in front of every piece."""
    out = b""
    for sym, short, full in records:
        for piece in (sym, "|", short, "|", full + "|", "\r\n"):
            out += b"\xfe\xff" + piece.encode("utf-16-be")
    return out + b"\xfe\xff" + "|".encode("utf-16-be")


def stat(label, value, cls=""):
    return ('<div class="stats_item"><div class="stats_label">%s</div><div class="stats_value%s">%s</div></div>'
            % (label, cls, value))


def rng(label, lo, hi, cur):
    """A range stat as the page shows it: "lo <em dash> hi" plus a numRange div."""
    return ('<div class="stats_item"><div class="stats_label">%s</div><div class="stats_value">%s %s %s'
            '<div class="numRange" data-low="%s" data-high="%s" data-current="%s"></div></div></div>'
            % (label, lo, chr(0x2014), hi, lo, hi, cur))


def demo_page():
    """A small company page in the real markup (DEMO is made up)."""
    reg = ('<div class="tabs__panel" data-name="REG"><div class="stats stats--noborder">'
           + stat("Open", "100.20") + stat("High", "101.80") + stat("Low", "99.90") + stat("Volume", "1,234,567")
           + '</div><div class="stats company__quote__rangeStats">'
           + rng("CIRCUIT BREAKER", "90.00", "110.00", "101.5") + rng("DAY RANGE", "99.90", "101.80", "101.5")
           + rng("52-WEEK RANGE ^", "80.00", "120.00", "101.5") + '</div><div class="stats">'
           + stat("Ask Price", "101.55") + stat("Ask Volume", "500") + stat("Bid Price", "101.45")
           + stat("Bid Volume", "1,200") + '</div><div class="stats">' + stat("LDCP", "100.00") + stat("VAR", "12.00")
           + stat("HAIRCUT", "15.00") + stat("P/E Ratio (TTM) **", "N/A") + '</div><div class="stats">'
           + stat("1-Year Change * ^", '<i class="icon-down-dir"></i>  -5.25%', " change__text--neg")
           + stat("YTD Change * ^", '<i class="icon-up-dir"></i>  3.10%', " change__text--pos")
           + '<div class="stats_item"></div></div></div>')
    fut = ('<div class="tabs__panel" data-name="FUT"><div class="quote__stats__fut" data-symbol="DEMO-NOV">'
           + stat("Open", "999.00") + stat("LDCP", "998.00")
           + '</div></div><div class="tabs__panel" data-name="ODL">No record found</div>')
    quote = ('<div class="section section--padded company" id="quote"><div class="company__quote">'
             '<div class="quote__details"><div class="quote__name">Demo Industries Limited'
             '<div class="tag tag--skim tag--xd">XD</div></div><div class="quote__sector"><span>CHEMICAL</span></div>'
             '<div class="quote__price"><div class="quote__close">Rs.101.50</div>'
             '<div class="quote__change change__text--pos"><div class="change__direction"><i class="icon-up-dir"></i>'
             ' </div><div class="change__value">1.50</div><div class="change__percent">  (1.50%)</div></div></div>'
             '<div class="quote__date">^ As of Thu, Oct 29, 2026 10:16 AM</div>'
             '<div class="quote__stats" id="statsTab"><div class="tabs"><div class="tabs__panels">' + reg + fut
             + '</div></div></div></div></div>')
    profile = ('<div class="section section--padded company" id="profile"><div class="profile__items">'
               '<div class="item__head">BUSINESS DESCRIPTION</div><p>Demo makes &amp; sells demo goods. </p>'
               '<div class="item__head">KEY PEOPLE</div><table class="tbl"><tbody class="tbl__body">'
               '<tr><td><strong>A. Person</strong></td><td>CEO</td></tr>'
               '<tr><td><strong>B. Person</strong></td><td>Chairperson</td></tr></tbody></table>'
               '<div class="item__head">ADDRESS</div><p>1 Demo Road, Karachi</p>'
               '<div class="item__head">WEBSITE</div><p> <a href="http://www.demo.example">www.demo.example</a></p>'
               '<div class="item__head">REGISTRAR</div><p>Demo Registrar</p>'
               '<div class="item__head">AUDITOR</div><p>Demo &amp; Co.</p>'
               '<div class="item__head">Fiscal Year End</div><p>June</p></div></div>')
    equity = ('<div class="section section--padded companyEquity" id="equity"><div class="stats stats--wrappable">'
              + stat("Market Cap (000'<span style=\"text-transform: lowercase;\">s</span>)", "10,150,000.00")
              + stat("Shares", "100,000,000") + stat("Face Value", "10.00") + stat("Free Float", "25,000,000")
              + stat("Free Float", "25.00%") + '</div></div>')
    ann = ('<div class="section section--padded company" id="announcements"><div class="tabs__panels">'
           '<div class="tabs__panel" data-name="Financial Results"><table class="tbl"><tbody class="tbl__body">'
           '<tr><td>Oct 28, 2026</td><td>FINANCIAL RESULTS FOR THE QUARTER ENDED SEPTEMBER 30, 2026 </td><td> '
           '<i class="icon-file-image"></i><a href="javascript:" data-images="287001-1.gif">View</a>&nbsp;'
           '<i class="icon-file-pdf"></i><a href="/download/document/287001.pdf" target="_blank">PDF</a></td></tr>'
           '</tbody></table></div><div class="tabs__panel" data-name="Board Meetings"><div class="tbl__wrapper">'
           '<table class="tbl"><tbody class="tbl__body"><tr><td>Oct 1, 2026</td><td>Board Meeting '
           '<div class="tag tag--skim tag--teal">REVISED</div></td><td> <i class="icon-file-image"></i>'
           '<a href="javascript:" data-images="286001-1.gif">View</a></td></tr></tbody></table></div></div>'
           '<div class="tabs__panel" data-name="Others"><div class="tbl__wrapper"><table class="tbl">'
           '<tbody class="tbl__body"><tr><td>Sep 9, 2026</td><td>DEMO | Demo Industries Limited Notice\tto '
           'shareholders</td><td><a href="/download/attachment/285001-1.pdf" target="_blank">PDF</a></td></tr>'
           '</tbody></table></div></div></div></div>')
    fin = ('<div class="section section--padded company" id="financials"><p style="color:#666; float:left;">'
           'All numbers in thousands (000\'s) except EPS</p><div class="tabs__panel" data-name="Annual">'
           '<table class="tbl"><thead class="tbl__head"><tr><th></th><th class="right">2026</th>'
           '<th class="right">2025</th></tr><tbody class="tbl__body"><tr><td>Sales</td>'
           '<td class="right"><span class="">1,000,000</span></td><td class="right"><span class="">900,000</span>'
           '</td></tr><tr><td>EPS</td><td class="right"><span class="change__text--neg">(1.25)</span></td>'
           '<td class="right"></td></tr></tbody></thead></table></div><div class="tabs__panel" data-name="Quarterly">'
           '<table class="tbl"><thead class="tbl__head"><tr><th></th><th class="right">Q1 2027</th></tr>'
           '<tbody class="tbl__body"><tr><td>Sales</td><td class="right"><span class="">250,000</span></td></tr>'
           '</tbody></thead></table></div></div>')
    ratios = ('<div class="section section--padded company" id="ratios"><table class="tbl"><thead class="tbl__head">'
              '<tr><th></th><th class="right">2026</th></tr></thead><tbody class="tbl__body"><tr><td>EPS Growth (%)'
              '</td><td class="right"><span class="change__text--neg">(18.71)</span></td></tr></tbody></table></div>')
    return ('<html><body><div class="pageHeader__title">DEMO</div>' + quote + profile + equity + ann + fin + ratios
            + '<div class="footer">footer</div></body></html>')


def screener_page():
    def row(sym, title, tags, sector, listed, nums):
        tds = "".join('<td class="right change__text--pos" data-order="%s" data-filter="%s"><i class="icon-up-dir">'
                      '</i> %s</td>' % (n, n, n) for n in nums)
        return ('<tr><td data-order="%s"><a class="tbl__symbol" href="/company/%s" data-title="%s"><strong>%s</strong>'
                '</a>%s</td><td>%s</td><td>%s</td>%s</tr>' % (sym, sym, title, sym, tags, sector, listed, tds))
    return ('<div class="topbar__status__label">Oct 29, 2026 10:21 AM</div><table><tbody>'
            + row("DEMO", "Demo Industries Limited", '<div class="tag tag--skim tag--xd">XD</div>', "0805",
                  "ALLSHR,KSE100", ["10150000000", "101.5", "1.5", "-5.25", "8.4", "2.1", "25000000", "512345.5"])
            + row("NOPX", "No Price &amp; Co", '<div class="tag tag--skim tag--def">NC</div>', "", "",
                  ["0", "0", "0", "-100", "0", "0", "0", "0"])
            + '</tbody></table>')


def indices_page():
    return ('<div class="section section--padded" id="indicesTable"><div style="text-align: right; padding-bottom: '
            '0.4em;">As of  Oct 29, 2026 3:50 PM</div><table class="tbl"><tbody class="tbl__body">'
            '<tr><td><a class="link" href="javascript:;" data-code="KSE100"><b>KSE100</b></a></td>'
            '<td class="right" data-order="167483.48">167,483.48</td><td class="right" data-order="165907.29">x</td>'
            '<td class="right" data-order="167193.43">x</td><td class="right change__text--pos" data-order="1456.68">'
            '<i class="icon-up-dir"></i> 1456.68</td><td class="right change__text--pos" data-order="0.8789">x</td>'
            '</tr>'
            '<tr><td><b style="color:#009d49;">HBLTTI</b><span style="color:#888;"> (28-10-2026 18:30:00)</span></td>'
            '<td class="right" data-order="0"><span>N/A</span></td>'
            '<td class="right" data-order="0"><span>N/A</span></td>'
            '<td class="right" data-order="19290.31">x</td><td class="right" data-order="5.81">x</td>'
            '<td class="right" data-order="0.03">x</td></tr></tbody></table></div>')


class FakeResp(object):
    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CutResp(FakeResp):
    """An answer that is cut off while it is read."""

    def __init__(self):
        FakeResp.__init__(self, b"")

    def read(self):
        raise http.client.IncompleteRead(b"<html>", 1000)


class FakeNet(object):
    """Stands in for urlopen: {url: bytes, an HTTP code or an exception}. Records the calls
    and the SLEEP pauses."""

    def __init__(self, answers=None, headers=None):
        self.answers, self.headers, self.calls, self.sleeps = answers or {}, headers or {}, [], []

    def __enter__(self):
        self.old = (urllib.request.urlopen, core.SLEEP, getattr(core.http_get, "last", None))
        urllib.request.urlopen = self.urlopen
        core.SLEEP = self.sleeps.append
        core.http_get.last = None
        return self

    def __exit__(self, *a):
        urllib.request.urlopen, core.SLEEP, core.http_get.last = self.old
        return False

    def urlopen(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url, dict(req.header_items()), timeout))
        a = self.answers.get(url, 404)
        if isinstance(a, int):
            raise urllib.error.HTTPError(url, a, "HTTP %d" % a, {}, io.BytesIO(b"<html>not found</html>"))
        if isinstance(a, Exception):
            raise a
        if isinstance(a, FakeResp):
            return a
        return FakeResp(a, self.headers.get(url))


class TempRoot(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="psxcore-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def put(self, rel, data):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    def put_day(self, d, ext=".Z"):
        return self.put(os.path.join("mkt_summary", d.isoformat() + ext), zip_bytes("closing11.lis", lis(day_code(d))))


# ---------------------------------------------------------------- the file itself

class TestSource(unittest.TestCase):
    def setUp(self):
        self.text = read_text(SRC)
        self.tree = ast.parse(self.text)

    def public_functions(self):
        return dict((n, f) for n, f in vars(core).items()
                    if callable(f) and getattr(f, "__module__", "") == "psx_core" and not n.startswith("_"))

    def test_parses_as_python_38(self):
        ast.parse(self.text, feature_version=(3, 8))
        ast.parse(read_text(os.path.abspath(__file__)), feature_version=(3, 8))

    def test_no_newer_python(self):
        for bad in ("zoneinfo", ":=", "removeprefix", "removesuffix", "functools.cache", "| {"):
            self.assertNotIn(bad, re.sub(r'"""[\s\S]*?"""', "", self.text), bad)
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNone(node.returns, node.name)
                for a in node.args.args + node.args.kwonlyargs:
                    self.assertIsNone(a.annotation, node.name)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "").startswith("bisect"):
                self.assertEqual(node.keywords, [], "bisect key= is Python 3.10")

    def test_no_em_dash_and_stdlib_only(self):
        self.assertNotIn(chr(0x2014), self.text)
        mods = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                mods |= set(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                mods.add(node.module.split(".")[0])
        self.assertEqual(mods, {"bisect", "csv", "datetime", "gzip", "html", "io", "json", "os", "re", "time",
                                "urllib", "zipfile", "decimal"})

    def test_every_helper_has_a_short_plain_docstring(self):
        for name, f in self.public_functions().items():
            self.assertTrue(f.__doc__ and f.__doc__.strip(), name)

    def test_every_helper_is_tested(self):
        mine = read_text(os.path.abspath(__file__))
        for name in self.public_functions():
            self.assertIn("core.%s(" % name, mine, name)

    def test_signatures_are_the_contract(self):
        want = {
            "to_pkt": "(utc)", "pkt_to_utc": "(local)", "ts_to_pkt": "(ts)", "utc_now": "()", "parse_now": "(text)",
            "parse_hhmm": "(text, flag='--at')", "parse_utc": "(text)", "utc_iso": "(dt)", "pkt_iso": "(utc)",
            "clock_label": "(t)", "day_label": "(d)", "ramadan_range": "(year, cfg=None)",
            "in_ramadan": "(d, cfg=None)", "trading_day": "(d, cfg=None)", "calendar_note": "(d, cfg=None)",
            "session_kind": "(d, cfg=None)", "session": "(d, cfg=None)", "slot_spans": "(d, minutes=5, cfg=None)",
            "slots": "(d, minutes=5, cfg=None)", "slot_index": "(d, when, minutes=5, cfg=None)",
            "bar_close": "(d, index, minutes=5, cfg=None)",
            "opening_range_slots": "(d, or_minutes=15, minutes=5, cfg=None)", "shift_time": "(t, d, cfg=None)",
            "config_time": "(t, d, cfg=None)", "costs_vs_risk": "(entry, stop, qty, costs=None)",
            "reward_risk": "(entry, stop, target)", "meets_reward_risk": "(entry, stop, target, minimum)",
            "market_state": "(now_pkt, cfg=None)", "round_down_tick": "(x)", "round_half_up_tick": "(x)",
            "price_limits": "(ldcp)", "money": "(x, decimals=2)", "cost_settings": "(cfg)",
            "leg_cost": "(price, qty, costs=None, commission=True)",
            "round_trip_cost": "(buy_price, sell_price, qty, costs=None)",
            "max_loss_at_stop": "(entry, stop, qty, costs=None)", "num": "(text)", "read_mkt_summary": "(raw)",
            "read_symbol_names": "(raw)", "symbol_kind": "(symbol, sector, full_name='')", "html_text": "(s)",
            "page_time": "(text)", "parse_screener": "(html)", "parse_company": "(html)", "parse_indices": "(html)",
            "site_on": "(cfg)", "http_get": "(url, timeout=30)", "daily_path": "(root, d)",
            "daily_rows": "(d, root, cfg=None, offline=False)",
            "recent_sessions": "(end_date, n, root, cfg=None, offline=False, include_end=True, max_back=None)",
            "daily_bars": "(symbol, sessions)", "page_html": "(path, url, cfg=None, offline=False)",
            "company_html": "(symbol, root, cfg=None, offline=False)",
            "screener_html": "(root, cfg=None, offline=False)", "tape_path": "(root, d, name)",
            "append_tape": "(path, snap)", "read_tape": "(path)", "snapshot_from_company": "(parsed, fetched_utc)",
            "snapshot_from_screener": "(parsed, fetched_utc, delay_minutes=5)",
            "bars_from_tape": "(snaps, d, symbol=None, minutes=5, cfg=None)", "last_asof": "(snaps)",
            "read_bars_csv": "(text)", "resample_bars": "(bars, d, minutes=5, cfg=None)",
            # v2 (swing style): the style group, a calendar and a bars-grid helper, the daily levels
            "trade_style": "(cfg)", "swing_settings": "(cfg)", "trade_costs": "(cfg)",
            "add_sessions": "(d, n, cfg=None)", "swing_window": "(now_pkt, order_type, cfg=None)",
            "adjusted_closes": "(bars, prev_session=None, missing=())", "window_sessions": "(dates, cfg=None)",
            "adjusted_bars": "(bars, prev_session=None, missing=())", "gap_loss": "(entry, qty, costs=None)",
            "daily_atr": "(bars, n=14)", "trend_break": "(closes, n=10)"}
        funcs = self.public_functions()
        self.assertEqual(set(want), set(funcs))
        for name, sig in want.items():
            self.assertEqual(str(inspect.signature(funcs[name])), sig, name)

    def test_copy_together_block_matches_the_code(self):
        block = self.text.split("# ---------------------------------------------------------------- copy together")[1]
        block = block.split("\nimport ")[0]
        needs = {}
        for line in block.splitlines():
            m = re.match(r"^#   ([a-z_0-9]+): (.+)$", line)
            if m:
                self.assertNotIn(m.group(1), needs, "listed twice: " + m.group(1))
                deps = m.group(2).strip()
                needs[m.group(1)] = set() if deps == "-" else set(x.strip() for x in deps.split(","))
        funcs = self.public_functions()
        self.assertEqual(set(needs), set(funcs))
        canonical = set(n for n in vars(core) if not n.startswith("_") and n not in IMPORTED)
        for name, f in funcs.items():
            used = set()
            todo = [f.__code__]
            while todo:
                code = todo.pop()
                used |= set(i.argval for i in dis.get_instructions(code) if i.opname == "LOAD_GLOBAL")
                todo.extend(c for c in code.co_consts if hasattr(c, "co_code"))
            self.assertEqual(used & canonical - {name}, needs[name], name)
        for mod in IMPORTED - {"Decimal", "InvalidOperation", "ROUND_FLOOR", "ROUND_HALF_UP"}:
            self.assertIn(mod, block)


# ---------------------------------------------------------------- config times

class TestConfigTime(unittest.TestCase):
    def T(self, text):
        return datetime.time(*map(int, text.split(":")))

    def test_normal_day_keeps_a_time_inside_and_clamps_outside(self):
        d = datetime.date(2026, 10, 29)                                   # a Thursday
        self.assertEqual(core.config_time(self.T("09:45"), d), self.T("09:45"))
        self.assertEqual(core.config_time(self.T("15:20"), d), self.T("15:20"))
        self.assertEqual(core.config_time(self.T("15:55"), d), self.T("15:30"))  # a US flat_by
        self.assertEqual(core.config_time(self.T("09:00"), d), self.T("09:30"))
        self.assertIsNone(core.config_time(None, d))

    def test_friday_shifts_and_the_break_means_its_start(self):
        d = datetime.date(2026, 10, 30)                                   # a Friday
        self.assertEqual(core.config_time(self.T("09:45"), d), self.T("09:30"))
        self.assertEqual(core.config_time(self.T("15:00"), d), self.T("16:00"))
        self.assertEqual(core.config_time(self.T("15:20"), d), self.T("16:20"))
        self.assertEqual(core.shift_time(self.T("13:00"), d), self.T("14:00"))  # in the Juma break
        self.assertEqual(core.config_time(self.T("13:00"), d), self.T("12:00"))
        self.assertEqual(core.config_time(self.T("12:00"), d), self.T("11:45"))

    def test_ramadan_and_closed_days(self):
        mon = datetime.date(2026, 3, 2)                                   # a Ramadan Monday
        self.assertEqual(core.config_time(self.T("15:00"), mon), self.T("13:00"))
        self.assertEqual(core.config_time(self.T("15:55"), mon), self.T("13:30"))
        fri = datetime.date(2026, 3, 6)                                   # a Ramadan Friday
        self.assertEqual(core.config_time(self.T("15:20"), fri), self.T("12:20"))
        self.assertEqual(core.config_time(self.T("09:00"), fri), self.T("09:15"))
        sat = datetime.date(2026, 10, 31)
        self.assertEqual(core.config_time(self.T("15:20"), sat), self.T("15:20"))


# ---------------------------------------------------------------- cost share and reward:risk

class TestOneRuleForCostsAndRewardRisk(unittest.TestCase):
    def test_costs_vs_risk_is_a_share_of_the_price_risk(self):
        # the DEMO example: Rs 1,229.14 of costs on Rs 3,770.80 of price risk
        share = core.costs_vs_risk(100.65, 99.55, 3428)
        self.assertAlmostEqual(share, 1229.14 / 3770.80, places=9)
        self.assertGreater(share, core.COST_WARN)                   # 33%: over the 25% line
        self.assertEqual(core.COST_WARN, 0.25)
        self.assertIsNone(core.costs_vs_risk(100, 100, 10))
        self.assertIsNone(core.costs_vs_risk(100, 101, 10))
        one_side = dict(core.COSTS, day_trade_one_side=True)
        self.assertLess(core.costs_vs_risk(100.65, 99.55, 3428, one_side), share)

    def test_reward_risk_exact_and_compared_without_rounding(self):
        self.assertEqual(core.reward_risk(100.65, 99.55, 101.75), 1.0)
        self.assertAlmostEqual(core.reward_risk(101.30, 99.55, 101.75), 0.45 / 1.75, places=12)
        self.assertIsNone(core.reward_risk(100, 100, 101))
        self.assertTrue(core.meets_reward_risk(100.65, 99.55, 101.75, 1.0))
        # 0.9951 shows as 1.00 but is under a 1.0 minimum: all three tools must say no
        self.assertFalse(core.meets_reward_risk(100.00, 97.95, 102.04, 1.0))
        self.assertFalse(core.meets_reward_risk(100.00, 98.00, 101.99, 1.0))
        self.assertTrue(core.meets_reward_risk(100.00, 98.00, 102.00, 1.0))
        self.assertTrue(core.meets_reward_risk(100.00, 98.00, 103.00, 1.5))
        self.assertFalse(core.meets_reward_risk(100.00, 100.00, 103.00, 1.0))
        # float noise in the inputs does not change the answer
        self.assertTrue(core.meets_reward_risk(0.1 + 0.2, 0.2, 0.4, 1.0))


# ---------------------------------------------------------------- constants

class TestConstants(unittest.TestCase):
    def test_site_and_pause(self):
        self.assertEqual(core.UA, "Mozilla/5.0 (compatible; psx-trader-skill/1.0)")
        self.assertEqual(core.PSX, "https://dps.psx.com.pk")
        self.assertEqual(core.PAUSE, 1.5)
        self.assertIs(core.SLEEP, time.sleep)
        self.assertEqual(core.KARACHI_HOURS, 5)

    def test_site_off_is_one_plain_sentence(self):
        s = core.SITE_OFF % "https://dps.psx.com.pk/screener as psx/data/screener.html"
        self.assertIn('"psx_site": true', s)
        self.assertIn("psx/config.json", s)
        self.assertIn("browser", s)
        self.assertIn("permission", s)
        self.assertTrue(s.endswith("."))
        self.assertEqual(s.count(". "), 0)
        self.assertEqual(core.SITE_OFF.count("%s"), 1)

    def test_holidays_2026_and_2027(self):
        h26 = sorted(k for k in core.PSX_HOLIDAYS if k.startswith("2026"))
        h27 = sorted(k for k in core.PSX_HOLIDAYS if k.startswith("2027"))
        self.assertEqual(h26, ["2026-02-05", "2026-03-20", "2026-03-23", "2026-05-01", "2026-05-26", "2026-05-27",
                               "2026-05-28", "2026-06-25", "2026-06-26", "2026-08-14", "2026-08-26", "2026-11-09",
                               "2026-12-25"])
        self.assertEqual(h27, ["2027-02-05", "2027-03-05", "2027-03-10", "2027-03-11", "2027-03-12", "2027-03-23",
                               "2027-05-17", "2027-05-18", "2027-05-19", "2027-05-28", "2027-06-15", "2027-06-16",
                               "2027-08-16", "2027-11-09"])
        self.assertIn("Youm-e-Takbeer", core.PSX_HOLIDAYS["2026-05-28"])
        self.assertIn("Eid ul Azha", core.PSX_HOLIDAYS["2026-05-28"])
        for k in core.PSX_HOLIDAYS:
            self.assertLess(D.fromisoformat(k).weekday(), 5, k)

    def test_provisional_moon_dates(self):
        self.assertEqual(core.PSX_PROVISIONAL, {"2027-03-05", "2027-03-10", "2027-03-11", "2027-03-12", "2027-05-17",
                                                "2027-05-18", "2027-05-19", "2027-06-15", "2027-06-16", "2027-08-16"})
        self.assertTrue(core.PSX_PROVISIONAL <= set(core.PSX_HOLIDAYS))

    def test_ramadan_and_years(self):
        self.assertEqual(core.RAMADAN, {2026: ("2026-02-19", "2026-03-19"), 2027: ("2027-02-09", "2027-03-09")})
        self.assertEqual(core.RAMADAN_PROVISIONAL, {2027})
        self.assertEqual(core.CALENDAR_YEARS, (2026, 2027))

    def test_sessions_and_times(self):
        self.assertEqual(core.SESSIONS, {"normal": (("09:30", "15:30"),),
                                         "friday": (("09:15", "12:00"), ("14:30", "16:30")),
                                         "ramadan": (("09:15", "13:30"),), "ramadan-friday": (("09:15", "12:30"),)})
        self.assertEqual((core.NORMAL_OPEN, core.NORMAL_CLOSE, core.SHIFT_SPLIT), (T(9, 30), T(15, 30), T(12, 30)))

    def test_costs_are_the_spec_block(self):
        self.assertEqual(core.COSTS, {"commission_pct": 0.15, "commission_min_per_share": 0.03, "sales_tax_pct": 15,
                                      "other_charges_pct": 0.0066, "day_trade_one_side": False})

    def test_sectors(self):
        eq = [k for k in core.SECTORS if k.startswith("08")]
        self.assertEqual(len(eq), 38)
        self.assertNotIn("0817", core.SECTORS)
        self.assertEqual(core.SECTORS["0820"], "OIL & GAS EXPLORATION COMPANIES")
        self.assertEqual(core.SECTORS["0813"], "INV. BANKS / INV. COS. / SECURITIES COS.")
        self.assertEqual(core.SECTORS["0806"], "CLOSE - END MUTUAL FUND")
        self.assertEqual((core.SECTORS["36"], core.SECTORS["40"], core.SECTORS["41"]),
                         ("DEBT", "STOCK FUTURES", "INDEX FUTURES"))

    def test_future_and_flag_regexes(self):
        for s in ("UBL-OCTB", "OGDC-NOV", "OGDC-COCT", "LOTCHEM-CSEPN1", "KSE30-DEC", "UBL-NOVC"):
            self.assertTrue(core.FUTURE_RE.match(s), s)
        for s in ("OGDC", "786R", "BAPLR1", "OGDC-XYZ"):
            self.assertFalse(core.FUTURE_RE.match(s), s)
        self.assertEqual(core.FLAG_RE.search("Oil & Gas DevXD").group(0), "XD")
        self.assertEqual(core.FLAG_RE.search("Jubilee LifeXDXB").group(0), "XDXB")
        self.assertIsNone(core.FLAG_RE.search("Lucky Cement"))


# ---------------------------------------------------------------- time

class TestTime(unittest.TestCase):
    def test_to_pkt_and_back(self):
        self.assertEqual(core.to_pkt(DT(2026, 10, 29, 5, 15)), DT(2026, 10, 29, 10, 15))
        self.assertEqual(core.pkt_to_utc(DT(2026, 10, 29, 10, 15)), DT(2026, 10, 29, 5, 15))
        self.assertEqual(core.to_pkt(DT(2026, 12, 31, 20, 0)), DT(2027, 1, 1, 1, 0))
        aware = DT(2026, 10, 29, 10, 15, tzinfo=datetime.timezone(datetime.timedelta(hours=5)))
        self.assertEqual(core.to_pkt(aware), DT(2026, 10, 29, 10, 15))
        self.assertEqual(core.pkt_to_utc(aware), DT(2026, 10, 29, 5, 15))
        self.assertIsNone(core.to_pkt(DT(2026, 10, 29)).tzinfo)

    def test_ts_to_pkt(self):
        ts = calendar.timegm((2026, 10, 29, 5, 15, 0))
        self.assertEqual(core.ts_to_pkt(ts), DT(2026, 10, 29, 10, 15))

    def test_no_machine_time_zone(self):
        if not hasattr(time, "tzset"):
            self.skipTest("no tzset here")
        old = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/New_York"
            time.tzset()
            self.assertEqual(core.ts_to_pkt(calendar.timegm((2026, 10, 29, 5, 15, 0))), DT(2026, 10, 29, 10, 15))
            self.assertEqual(core.parse_now("2026-10-29 10:15"), DT(2026, 10, 29, 5, 15))
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_utc_now(self):
        now = core.utc_now()
        real = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        self.assertIsNone(now.tzinfo)
        self.assertEqual(now.microsecond, 0)
        self.assertLess(abs((real - now).total_seconds()), 5)

    def test_parse_now_karachi_by_default(self):
        self.assertEqual(core.parse_now("2026-10-29 10:15"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(core.parse_now("2026-10-29T10:15:30"), DT(2026, 10, 29, 5, 15, 30))
        self.assertEqual(core.parse_now("2026-10-29 10:15 PKT"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(core.parse_now("2026-10-29"), DT(2026, 10, 28, 19, 0))

    def test_parse_now_offsets(self):
        self.assertEqual(core.parse_now("2026-10-29T05:15:00Z"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(core.parse_now("2026-10-29T05:15z"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(core.parse_now("2026-10-29 10:15+05:00"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(core.parse_now("2026-10-29T01:15:00-04:00"), DT(2026, 10, 29, 5, 15))

    def test_parse_now_empty_and_bad(self):
        self.assertLess(abs((core.parse_now("") - core.utc_now()).total_seconds()), 5)
        self.assertLess(abs((core.parse_now(None) - core.utc_now()).total_seconds()), 5)
        with self.assertRaises(ValueError) as cm:
            core.parse_now("tomorrow at ten")
        self.assertEqual(str(cm.exception), '--now wants a time like "2026-10-29 10:15" (Karachi time) or ...Z (UTC)')

    def test_parse_hhmm(self):
        self.assertEqual(core.parse_hhmm("10:15"), T(10, 15))
        self.assertEqual(core.parse_hhmm(" 9:05 "), T(9, 5))
        for bad in ("24:00", "10:60", "1015", "", None, "10:5"):
            with self.assertRaises(ValueError) as cm:
                core.parse_hhmm(bad)
            self.assertEqual(str(cm.exception), "--at wants a Karachi time like 10:15")
        with self.assertRaises(ValueError) as cm:
            core.parse_hhmm("x", "no_entries_after")
        self.assertEqual(str(cm.exception), "no_entries_after wants a Karachi time like 10:15")

    def test_parse_utc(self):
        self.assertEqual(core.parse_utc("2026-10-29T05:15:00Z"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(core.parse_utc("2026-10-29T05:15:00"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(core.parse_utc("2026-10-29T10:15:00+05:00"), DT(2026, 10, 29, 5, 15))
        for x in ("", None, "not a time", "2026-13-01"):
            self.assertIsNone(core.parse_utc(x))

    def test_iso_strings(self):
        self.assertEqual(core.utc_iso(DT(2026, 10, 29, 5, 15)), "2026-10-29T05:15:00Z")
        self.assertIsNone(core.utc_iso(None))
        aware = DT(2026, 10, 29, 10, 15, tzinfo=datetime.timezone(datetime.timedelta(hours=5)))
        self.assertEqual(core.utc_iso(aware), "2026-10-29T05:15:00Z")
        self.assertEqual(core.pkt_iso(DT(2026, 10, 29, 5, 15)), "2026-10-29T10:15:00+05:00")
        self.assertEqual(core.parse_utc(core.pkt_iso(DT(2026, 10, 29, 5, 15))), DT(2026, 10, 29, 5, 15))

    def test_round_trips_and_no_local_clock(self):
        for x in (DT(2026, 10, 29, 0, 0), DT(2026, 1, 1, 2, 30), DT(2026, 12, 31, 23, 59, 59)):
            self.assertEqual(core.to_pkt(core.pkt_to_utc(x)), x)
            self.assertEqual(core.pkt_to_utc(core.to_pkt(x)), x)
            self.assertEqual(core.parse_now(core.pkt_iso(core.pkt_to_utc(x))), core.pkt_to_utc(x))
            self.assertEqual(core.parse_now(core.utc_iso(core.pkt_to_utc(x))), core.pkt_to_utc(x))
        src = read_text(SRC)
        for bad in ("localtime", "mktime", "fromtimestamp", "astimezone", "utcnow", ".now()", "today()"):
            self.assertNotIn(bad, src, bad)

    def test_labels(self):
        self.assertEqual(core.clock_label(T(10, 15)), "10:15 AM")
        self.assertEqual(core.clock_label(T(0, 5)), "12:05 AM")
        self.assertEqual(core.clock_label(T(12, 0)), "12:00 PM")
        self.assertEqual(core.clock_label(DT(2026, 10, 29, 15, 30)), "3:30 PM")
        self.assertEqual(core.day_label(THU), "Thu 2026-10-29")
        self.assertEqual(core.day_label(DT(2026, 10, 30, 9, 0)), "Fri 2026-10-30")


# ---------------------------------------------------------------- calendar

class TestCalendar(unittest.TestCase):
    def test_weekend_and_normal_days(self):
        self.assertEqual(core.trading_day(D(2026, 10, 31)), (False, "weekend"))
        self.assertEqual(core.trading_day(D(2026, 11, 1)), (False, "weekend"))
        self.assertEqual(core.trading_day(THU), (True, ""))
        self.assertEqual(core.trading_day(FRI), (True, ""))

    def test_2026_holidays(self):
        self.assertEqual(core.trading_day(D(2026, 11, 9)), (False, "PSX holiday: Iqbal Day"))
        self.assertEqual(core.trading_day(D(2026, 8, 26)), (False, "PSX holiday: Eid Milad-un-Nabi"))
        self.assertEqual(core.trading_day(D(2026, 5, 28)), (False, "PSX holiday: Eid ul Azha and Youm-e-Takbeer"))
        for d in (D(2026, 5, 29), D(2026, 6, 24), D(2026, 8, 25)):   # moon dates that moved: these traded
            self.assertTrue(core.trading_day(d)[0], d)

    def test_every_2026_holiday_in_the_spec_is_closed(self):
        for k in ("2026-02-05", "2026-03-20", "2026-03-23", "2026-05-01", "2026-05-26", "2026-05-27", "2026-05-28",
                  "2026-06-25", "2026-06-26", "2026-08-14", "2026-08-26", "2026-11-09", "2026-12-25"):
            ok, why = core.trading_day(D.fromisoformat(k))
            self.assertEqual((ok, why[:13]), (False, "PSX holiday: "), k)
            self.assertEqual(core.slots(D.fromisoformat(k)), [], k)
        for d, kind in ((D(2026, 3, 24), "normal"), (D(2026, 5, 29), "friday"), (D(2026, 7, 1), "normal"),
                        (D(2026, 1, 1), "normal"), (D(2026, 2, 18), "normal"), (D(2026, 2, 19), "ramadan"),
                        (D(2026, 2, 20), "ramadan-friday"), (D(2026, 3, 19), "ramadan"), (D(2028, 2, 10), "normal")):
            self.assertEqual(core.session_kind(d), kind, d)

    def test_config_dates_must_be_real_dates(self):
        # "2026-1-5" was skipped without a word (so the fix never worked), and a list holding a dict raised TypeError
        for cfg in ({"holidays": {"2026-1-5": "x"}}, {"holidays": ["2026-02-30"]}, {"holidays": [{"2026-01-05": "x"}]},
                    {"open_days": ["2026-11-9"]}, {"open_days": ["2026-11-09", 5]}, {"open_days": [["2026-11-09"]]}):
            for f in (core.trading_day, core.calendar_note, core.session_kind):
                with self.assertRaises(ValueError) as cm:
                    f(THU, cfg)
                self.assertIn("psx/config.json", str(cm.exception))
        self.assertEqual(core.trading_day(THU, {"holidays": {" 2026-10-29 ": "Test"}}), (False, "PSX holiday: Test"))
        self.assertEqual(core.trading_day(D(2026, 11, 9), {"open_days": [" 2026-11-09 "]}), (True, ""))
        self.assertEqual(core.calendar_note(D(2027, 3, 10), {"holidays": {" 2027-03-10 ": "Eid ul Fitr"}}), "")

    def test_bank_only_holidays_are_open(self):
        for d in (D(2026, 1, 1), D(2026, 7, 1), D(2026, 2, 19), D(2027, 1, 1), D(2027, 7, 1)):
            self.assertEqual(core.trading_day(d), (True, ""), d)

    def test_2027_provisional_reason(self):
        self.assertEqual(core.trading_day(D(2027, 3, 10)),
                         (False, "PSX holiday: Eid ul Fitr (expected, check PSX notices)"))
        self.assertEqual(core.trading_day(D(2027, 11, 9)), (False, "PSX holiday: Iqbal Day"))

    def test_config_holidays_and_open_days(self):
        cfg = {"holidays": {"2026-10-29": "Test closure"}, "open_days": ["2026-11-09"]}
        self.assertEqual(core.trading_day(THU, cfg), (False, "PSX holiday: Test closure"))
        self.assertEqual(core.trading_day(D(2026, 11, 9), cfg), (True, ""))
        self.assertEqual(core.trading_day(D(2026, 10, 28), {"holidays": ["2026-10-28"]}),
                         (False, "PSX holiday: set in psx/config.json"))
        self.assertEqual(core.trading_day(D(2026, 11, 9), {"open_days": "2026-11-09"}), (True, ""))
        self.assertEqual(core.trading_day(D(2027, 3, 10), {"holidays": {"2027-03-10": "Eid ul Fitr"}}),
                         (False, "PSX holiday: Eid ul Fitr"))
        self.assertEqual(core.trading_day(THU, None), (True, ""))
        self.assertEqual(core.trading_day(THU, "not a dict"), (True, ""))

    def test_bad_config_types_raise_plain_errors(self):
        for cfg in ({"holidays": 5}, {"open_days": {"2026-11-09": "x"}}, {"open_days": 7}):
            with self.assertRaises(ValueError) as cm:
                core.trading_day(THU, cfg)
            self.assertIn("psx/config.json", str(cm.exception))
            with self.assertRaises(ValueError):
                core.calendar_note(THU, cfg)
            with self.assertRaises(ValueError):
                core.slots(THU, 5, cfg)

    def test_ramadan_range(self):
        self.assertEqual(core.ramadan_range(2026), (D(2026, 2, 19), D(2026, 3, 19)))
        self.assertEqual(core.ramadan_range(2027), (D(2027, 2, 9), D(2027, 3, 9)))
        self.assertIsNone(core.ramadan_range(2028))
        cfg = {"ramadan": {"start": "2027-02-08", "end": "2027-03-09"}}
        self.assertEqual(core.ramadan_range(2027, cfg), (D(2027, 2, 8), D(2027, 3, 9)))
        self.assertEqual(core.ramadan_range(2026, cfg), (D(2026, 2, 19), D(2026, 3, 19)))
        for bad in ({"start": "2027-02-08"}, {"start": "8 Feb", "end": "2027-03-09"}, "2027-02-08",
                    {"start": "2027-03-09", "end": "2027-02-08"}):
            with self.assertRaises(ValueError):
                core.ramadan_range(2027, {"ramadan": bad})

    def test_in_ramadan(self):
        self.assertTrue(core.in_ramadan(D(2026, 2, 19)))
        self.assertTrue(core.in_ramadan(D(2026, 3, 19)))
        self.assertFalse(core.in_ramadan(D(2026, 2, 18)))
        self.assertFalse(core.in_ramadan(D(2026, 3, 20)))
        self.assertFalse(core.in_ramadan(D(2027, 2, 8)))
        self.assertTrue(core.in_ramadan(D(2027, 2, 8), {"ramadan": {"start": "2027-02-08", "end": "2027-03-09"}}))
        self.assertFalse(core.in_ramadan(D(2028, 1, 20)))

    def test_session_kind(self):
        self.assertEqual(core.session_kind(THU), "normal")
        self.assertEqual(core.session_kind(FRI), "friday")
        self.assertEqual(core.session_kind(RAM_MON), "ramadan")
        self.assertEqual(core.session_kind(RAM_FRI), "ramadan-friday")
        self.assertEqual(core.session_kind(D(2026, 2, 19)), "ramadan")    # 1st Ramadan: banks shut, PSX open
        self.assertIsNone(core.session_kind(D(2026, 11, 9)))
        self.assertIsNone(core.session_kind(D(2026, 10, 31)))
        self.assertEqual(core.session_kind(D(2027, 2, 12)), "ramadan-friday")

    def test_session(self):
        self.assertEqual(core.session(THU), ((T(9, 30), T(15, 30)),))
        self.assertEqual(core.session(FRI), ((T(9, 15), T(12, 0)), (T(14, 30), T(16, 30))))
        self.assertEqual(core.session(RAM_MON), ((T(9, 15), T(13, 30)),))
        self.assertEqual(core.session(RAM_FRI), ((T(9, 15), T(12, 30)),))
        self.assertIsNone(core.session(D(2026, 12, 25)))
        self.assertIsNone(core.session(THU, {"holidays": {"2026-10-29": "x"}}))

    def test_calendar_note_quiet_on_known_days(self):
        for d in (THU, FRI, RAM_MON, D(2026, 11, 9), D(2026, 10, 31), D(2027, 4, 6), D(2028, 1, 1)):
            self.assertEqual(core.calendar_note(d), "", d)

    def test_calendar_note_unknown_year(self):
        note = core.calendar_note(D(2028, 1, 4))
        self.assertIn("2028", note)
        self.assertIn("only weekends", note)
        self.assertIn('"holidays"', note)
        self.assertIn("2025", core.calendar_note(D(2025, 6, 3)))

    def test_calendar_note_moon_dates(self):
        note = core.calendar_note(D(2027, 3, 10))
        self.assertIn("expected PSX holiday (Eid ul Fitr)", note)
        self.assertIn("check PSX notices", note)
        self.assertIn("next to 2027-05-20", core.calendar_note(D(2027, 5, 20)))
        self.assertIn("next to 2027-06-14", core.calendar_note(D(2027, 6, 14)))
        self.assertEqual(core.calendar_note(D(2027, 3, 10), {"holidays": {"2027-03-10": "Eid ul Fitr"}}), "")
        self.assertEqual(core.calendar_note(D(2027, 5, 20), {"open_days": ["2027-05-19"]}), "")

    def test_calendar_note_ramadan(self):
        self.assertIn("Ramadan hours are used", core.calendar_note(D(2027, 2, 15)))
        self.assertIn("a day off", core.calendar_note(D(2027, 2, 8)))
        cfg = {"ramadan": {"start": "2027-02-08", "end": "2027-03-08"}}
        self.assertEqual(core.calendar_note(D(2027, 2, 15), cfg), "")
        self.assertEqual(core.calendar_note(D(2026, 3, 2)), "")


# ---------------------------------------------------------------- bars grid

class TestBarsGrid(unittest.TestCase):
    def test_normal_day_72_bars(self):
        s = core.slots(THU)
        self.assertEqual(len(s), 72)
        self.assertEqual((s[0], s[-1]), (DT(2026, 10, 29, 9, 30), DT(2026, 10, 29, 15, 25)))
        self.assertEqual(s[8], DT(2026, 10, 29, 10, 10))                 # SPEC: bar 8 is the 10:10 bar
        self.assertEqual(core.bar_close(THU, 8), DT(2026, 10, 29, 10, 15))
        self.assertEqual(core.clock_label(core.bar_close(THU, 8)), "10:15 AM")

    def test_friday_57_bars_with_the_break(self):
        s = core.slots(FRI)
        self.assertEqual(len(s), 57)
        self.assertEqual(s[0], DT(2026, 10, 30, 9, 15))
        self.assertEqual(s[32], DT(2026, 10, 30, 11, 55))
        self.assertEqual(s[33], DT(2026, 10, 30, 14, 30))
        self.assertEqual(s[-1], DT(2026, 10, 30, 16, 25))
        self.assertEqual(core.slot_index(FRI, DT(2026, 10, 30, 14, 30)), 33)
        self.assertEqual(core.bar_close(FRI, 32), DT(2026, 10, 30, 12, 0))
        self.assertEqual(core.bar_close(FRI, 33), DT(2026, 10, 30, 14, 35))
        self.assertFalse([x for x in s if T(12, 0) <= x.time() < T(14, 30)])

    def test_ramadan_bars(self):
        s = core.slots(RAM_MON)
        self.assertEqual((len(s), s[0].time(), s[-1].time()), (51, T(9, 15), T(13, 25)))
        s = core.slots(RAM_FRI)
        self.assertEqual((len(s), s[0].time(), s[-1].time()), (39, T(9, 15), T(12, 25)))

    def test_closed_day_has_no_bars(self):
        self.assertEqual(core.slots(D(2026, 11, 9)), [])
        self.assertEqual(core.slots(D(2026, 10, 31)), [])
        self.assertEqual(core.slot_spans(D(2026, 10, 31)), [])

    def test_other_bar_sizes(self):
        self.assertEqual(len(core.slots(THU, 1)), 360)
        self.assertEqual(len(core.slots(THU, 15)), 24)
        self.assertEqual(len(core.slots(FRI, 15)), 19)
        spans = core.slot_spans(FRI, 30)          # 165 minutes do not fit 30-minute bars: the last one is short
        self.assertEqual(spans[5], (DT(2026, 10, 30, 11, 45), DT(2026, 10, 30, 12, 0)))
        self.assertEqual(spans[6][0], DT(2026, 10, 30, 14, 30))
        self.assertEqual(len(spans), 10)
        self.assertEqual(core.bar_close(FRI, 5, 30), DT(2026, 10, 30, 12, 0))
        for bad in (0, -5, 2.5):
            with self.assertRaises(ValueError):
                core.slots(THU, bad)

    def test_slot_index(self):
        self.assertEqual(core.slot_index(THU, DT(2026, 10, 29, 9, 30)), 0)
        self.assertEqual(core.slot_index(THU, T(10, 10)), 8)
        self.assertIsNone(core.slot_index(THU, DT(2026, 10, 29, 10, 11)))          # not a slot start
        self.assertIsNone(core.slot_index(THU, DT(2026, 10, 29, 9, 30, 30)))
        self.assertIsNone(core.slot_index(THU, DT(2026, 10, 29, 15, 30)))          # the close
        self.assertIsNone(core.slot_index(THU, DT(2026, 10, 28, 9, 30)))           # another day
        self.assertIsNone(core.slot_index(FRI, DT(2026, 10, 30, 13, 0)))           # the Juma break
        self.assertEqual(core.slot_index(FRI, T(9, 15)), 0)

    def test_friday_after_the_break(self):
        self.assertEqual([core.slot_index(FRI, t) for t in (T(14, 30), T(14, 35), T(16, 25), T(12, 0), T(14, 25))],
                         [33, 34, 56, None, None])
        self.assertEqual(core.bar_close(FRI, 56), DT(2026, 10, 30, 16, 30))
        self.assertEqual(core.opening_range_slots(FRI, 30)[-1], DT(2026, 10, 30, 9, 40))

    def test_bar_close_bad_index(self):
        for bad in (-1, 72, "3", None):
            with self.assertRaises(ValueError):
                core.bar_close(THU, bad)
        with self.assertRaises(ValueError):
            core.bar_close(D(2026, 11, 9), 0)

    def test_opening_range(self):
        self.assertEqual(core.opening_range_slots(THU), [DT(2026, 10, 29, 9, 30), DT(2026, 10, 29, 9, 35),
                                                         DT(2026, 10, 29, 9, 40)])
        self.assertEqual(core.opening_range_slots(FRI), [DT(2026, 10, 30, 9, 15), DT(2026, 10, 30, 9, 20),
                                                         DT(2026, 10, 30, 9, 25)])
        self.assertEqual([x.time() for x in core.opening_range_slots(RAM_MON)], [T(9, 15), T(9, 20), T(9, 25)])
        self.assertEqual(len(core.opening_range_slots(THU, 30)), 6)
        self.assertEqual(len(core.opening_range_slots(FRI, 180)), 33)     # never past the first segment
        self.assertEqual(core.opening_range_slots(D(2026, 11, 9)), [])
        for bad in (12, 0, 3):
            with self.assertRaises(ValueError):
                core.opening_range_slots(THU, bad)


class TestShiftTime(unittest.TestCase):
    def test_spec_examples(self):
        self.assertEqual(core.shift_time(T(9, 45), FRI), T(9, 30))
        self.assertEqual(core.shift_time(T(15, 0), FRI), T(16, 0))
        self.assertEqual(core.shift_time(T(15, 0), RAM_MON), T(13, 0))
        self.assertEqual(core.shift_time(T(15, 20), FRI), T(16, 20))

    def test_more_days(self):
        self.assertEqual(core.shift_time(T(9, 45), RAM_MON), T(9, 30))
        self.assertEqual(core.shift_time(T(15, 0), RAM_FRI), T(12, 0))
        self.assertEqual(core.shift_time(T(15, 20), RAM_FRI), T(12, 20))
        self.assertEqual(core.shift_time(T(12, 30), FRI), T(12, 15))      # at the split: minutes after the open
        self.assertEqual(core.shift_time(T(12, 31), FRI), T(13, 31))      # after it: minutes before the close

    def test_unchanged(self):
        for t in (T(9, 45), T(15, 0), T(15, 20)):
            self.assertEqual(core.shift_time(t, THU), t)
            self.assertEqual(core.shift_time(t, D(2026, 11, 9)), t)
        self.assertIsNone(core.shift_time(None, FRI))

    def test_all_four_kinds(self):
        want = {THU: ("09:45", "12:30", "12:31", "15:00", "15:20"),
                FRI: ("09:30", "12:15", "13:31", "16:00", "16:20"),
                RAM_MON: ("09:30", "12:15", "10:31", "13:00", "13:20"),
                RAM_FRI: ("09:30", "12:15", "09:31", "12:00", "12:20")}
        for d, outs in want.items():
            got = tuple(core.shift_time(t, d).strftime("%H:%M") for t in (T(9, 45), T(12, 30), T(12, 31), T(15, 0),
                                                                          T(15, 20)))
            self.assertEqual(got, outs, d)
            self.assertIsNone(core.shift_time(None, d))

    def test_never_crosses_midnight(self):
        # 00:00 used to become 23:45 on a Friday (so every entry was "too early") and 23:00 became 00:00
        for d in (FRI, RAM_MON, RAM_FRI):
            self.assertEqual(core.shift_time(T(0, 0), d), T(0, 0), d)
        self.assertEqual(core.shift_time(T(23, 0), FRI), T(23, 59))
        self.assertEqual(core.shift_time(T(23, 59), FRI), T(23, 59))
        self.assertEqual(core.shift_time(T(23, 59), RAM_MON), T(21, 59))
        self.assertEqual(core.shift_time(T(23, 59), RAM_FRI), T(20, 59))
        self.assertEqual(core.shift_time(T(9, 0), FRI), T(8, 45))

    def test_config_can_change_the_day(self):
        cfg = {"ramadan": {"start": "2027-02-08", "end": "2027-03-09"}}
        self.assertEqual(core.shift_time(T(15, 0), D(2027, 2, 8)), T(15, 0))
        self.assertEqual(core.shift_time(T(15, 0), D(2027, 2, 8), cfg), T(13, 0))


class TestMarketState(unittest.TestCase):
    def state(self, d, hh, mm, cfg=None):
        return core.market_state(DT(d.year, d.month, d.day, hh, mm), cfg)

    def test_normal_day(self):
        want = [(8, 0, "closed"), (9, 14, "closed"), (9, 15, "pre-open"), (9, 29, "pre-open"), (9, 30, "open"),
                (9, 32, "open"), (12, 0, "open"), (15, 29, "open"), (15, 30, "closed"), (15, 34, "closed"),
                (15, 35, "post-close"), (15, 49, "post-close"), (15, 50, "closed"), (20, 0, "closed")]
        for hh, mm, st in want:
            self.assertEqual(self.state(THU, hh, mm)["state"], st, (hh, mm))
        s = self.state(THU, 8, 0)
        self.assertEqual((s["kind"], s["open_at"], s["close_at"]), ("normal", DT(2026, 10, 29, 9, 30),
                                                                    DT(2026, 10, 29, 15, 30)))
        self.assertIn("9:15 AM", s["reason"])
        s = self.state(THU, 9, 20)
        self.assertEqual((s["open_at"], s["close_at"]), (DT(2026, 10, 29, 9, 30), DT(2026, 10, 29, 15, 30)))
        self.assertIn("limit orders only", s["reason"])
        s = self.state(THU, 10, 0)
        self.assertEqual((s["open_at"], s["close_at"], s["reason"]), (None, DT(2026, 10, 29, 15, 30),
                                                                      "open until 3:30 PM PKT"))
        s = self.state(THU, 15, 40)
        self.assertEqual((s["open_at"], s["close_at"]), (None, None))
        self.assertIn("closing price", s["reason"])
        self.assertIn("ended at 3:30 PM", self.state(THU, 16, 0)["reason"])

    def test_friday_break(self):
        want = [(8, 59, "closed"), (9, 0, "pre-open"), (9, 15, "open"), (11, 59, "open"), (12, 0, "break"),
                (14, 14, "break"), (14, 15, "pre-open"), (14, 30, "open"), (16, 29, "open"), (16, 30, "closed"),
                (16, 35, "post-close"), (16, 49, "post-close"), (16, 50, "closed")]
        for hh, mm, st in want:
            self.assertEqual(self.state(FRI, hh, mm)["state"], st, (hh, mm))
        s = self.state(FRI, 11, 0)
        self.assertEqual((s["kind"], s["open_at"], s["close_at"]), ("friday", DT(2026, 10, 30, 14, 30),
                                                                    DT(2026, 10, 30, 12, 0)))
        self.assertIn("Juma break", s["reason"])
        s = self.state(FRI, 13, 0)
        self.assertEqual((s["open_at"], s["close_at"]), (DT(2026, 10, 30, 14, 30), DT(2026, 10, 30, 16, 30)))
        self.assertIn("2:15 PM", s["reason"])
        s = self.state(FRI, 15, 0)
        self.assertEqual((s["open_at"], s["close_at"]), (None, DT(2026, 10, 30, 16, 30)))

    def test_ramadan(self):
        for hh, mm, st in [(9, 0, "pre-open"), (9, 15, "open"), (13, 29, "open"), (13, 30, "closed"),
                           (13, 35, "post-close"), (13, 50, "closed")]:
            self.assertEqual(self.state(RAM_MON, hh, mm)["state"], st, (hh, mm))
        for hh, mm, st in [(12, 29, "open"), (12, 30, "closed"), (12, 35, "post-close"), (12, 50, "closed"),
                           (14, 30, "closed")]:
            self.assertEqual(self.state(RAM_FRI, hh, mm)["state"], st, (hh, mm))
        self.assertEqual(self.state(RAM_FRI, 10, 0)["kind"], "ramadan-friday")

    def test_closed_days(self):
        s = self.state(D(2026, 11, 9), 10, 0)
        self.assertEqual(s, {"state": "closed", "kind": None, "reason": "closed all day (PSX holiday: Iqbal Day)",
                             "open_at": None, "close_at": None})
        self.assertEqual(self.state(D(2026, 10, 31), 10, 0)["reason"], "closed all day (weekend)")

    def test_config_overrides(self):
        self.assertEqual(self.state(THU, 10, 0, {"holidays": {"2026-10-29": "Test closure"}})["reason"],
                         "closed all day (PSX holiday: Test closure)")
        self.assertEqual(self.state(D(2026, 11, 9), 10, 0, {"open_days": ["2026-11-09"]})["state"], "open")


# ---------------------------------------------------------------- prices and rules

class TestPrices(unittest.TestCase):
    def test_round_down_tick(self):
        self.assertEqual(core.round_down_tick(358.325), 358.32)
        self.assertEqual(core.round_down_tick(359.35285), 359.35)
        self.assertEqual(core.round_down_tick(100.65 - 1.1), 99.55)
        self.assertEqual(core.round_down_tick(99.54999999999999), 99.55)    # float noise is cleaned
        self.assertEqual(core.round_down_tick(99.549), 99.54)
        self.assertEqual(core.round_down_tick(5), 5.0)
        self.assertEqual(core.round_down_tick("7.019"), 7.01)

    def test_round_half_up_tick(self):
        self.assertEqual(core.round_half_up_tick(2.675), 2.68)
        self.assertEqual(core.round_half_up_tick(101.745), 101.75)
        self.assertEqual(core.round_half_up_tick(370.034), 370.03)
        self.assertEqual(core.round_half_up_tick(1.005), 1.01)
        with self.assertRaises(ValueError):
            core.round_half_up_tick("abc")
        with self.assertRaises(ValueError):
            core.round_down_tick(None)

    def test_price_limits_examples(self):
        self.assertEqual(core.price_limits(203.68), (183.31, 224.05))       # a made-up LDCP
        self.assertEqual(core.price_limits("206.21"), (185.59, 226.83))     # the made-up close
        self.assertEqual(core.price_limits(60.15), (54.14, 66.17))          # x 0.90 rounded half up (54.135)
        self.assertEqual(core.price_limits(10.49), (9.44, 11.54))           # a made-up LDCP
        self.assertEqual(core.price_limits(33.63), (30.27, 36.99))          # a made-up LDCP
        self.assertEqual(core.price_limits(1133.86), (1020.47, 1247.25))    # a made-up LDCP
        self.assertEqual(core.price_limits(10), (9.0, 11.0))

    def test_price_limits_rupee_floor(self):
        self.assertEqual(core.price_limits(6.62), (5.62, 7.62))             # 10% is under Rs 1
        self.assertEqual(core.price_limits(9.99), (8.99, 10.99))
        self.assertEqual(core.price_limits(0.01), (0.01, 1.01))             # never below 0.01
        self.assertEqual(core.price_limits(0.5), (0.01, 1.5))

    def test_price_limits_near_rs_10_and_rs_1(self):
        self.assertEqual(core.price_limits(9.99), (8.99, 10.99))      # 10% is 0.999: the Rs 1 floor
        self.assertEqual(core.price_limits(10.00), (9.0, 11.0))       # both rules meet
        self.assertEqual(core.price_limits(10.01), (9.01, 11.01))     # 10% is 1.001: x 0.90 and x 1.10
        self.assertEqual(core.price_limits(1.00), (0.01, 2.0))
        self.assertEqual(core.price_limits(0.50), (0.01, 1.5))

    def test_price_limits_ignore_float_noise(self):
        # 60.15 with float noise under a tick used to give 54.13 and 66.16 (the real limits are 54.14 and 66.17)
        self.assertEqual(core.price_limits(60.15 - 1e-12), (54.14, 66.17))
        self.assertEqual(core.price_limits(60.15 + 1e-12), (54.14, 66.17))
        self.assertEqual(core.price_limits(203.68), (183.31, 224.05))
        for cents in range(1, 20001):
            p = cents / 100.0
            want = core.price_limits(p)
            self.assertEqual((core.price_limits(p - 1e-11), core.price_limits(p + 1e-11)), (want, want), p)

    def test_price_limits_bad_input(self):
        for bad in (0, -5, None, "x", float("nan"), "inf", 1e30):
            with self.assertRaises(ValueError):
                core.price_limits(bad)

    def test_nan_is_not_a_price_or_money(self):
        # the tick helpers gave NaN back, and money(nan) raised decimal.InvalidOperation, not ValueError
        for x in (float("nan"), "nan", "NaN"):
            for f in (core.round_down_tick, core.round_half_up_tick, core.money, core.price_limits):
                with self.assertRaises(ValueError):
                    f(x)

    def test_money(self):
        self.assertEqual(core.money(1234.56), "Rs 1,234.56")
        self.assertEqual(core.money(-1234.56), "-Rs 1,234.56")
        self.assertEqual(core.money(5000, 0), "Rs 5,000")
        self.assertEqual(core.money(5000), "Rs 5,000.00")
        self.assertEqual(core.money(1234567.891), "Rs 1,234,567.89")
        self.assertEqual(core.money(2.675), "Rs 2.68")
        self.assertEqual(core.money(-0.001), "Rs 0.00")
        self.assertEqual(core.money(1079.665, 2), "Rs 1,079.67")
        self.assertEqual(core.money(4999.5, 0), "Rs 5,000")
        with self.assertRaises(ValueError):
            core.money(None)


# ---------------------------------------------------------------- costs

class TestCosts(unittest.TestCase):
    def test_leg_cost(self):
        self.assertEqual(core.leg_cost(300, 1000), 537.3)       # 450 x 1.15 + 19.80
        self.assertEqual(core.leg_cost(303, 1000), 542.67)      # 454.50 x 1.15 + 19.998, half up
        self.assertEqual(core.leg_cost(100.65, 2000), 360.53)
        self.assertEqual(core.leg_cost(99.55, 2000), 356.59)

    def test_per_share_minimum(self):
        self.assertEqual(core.leg_cost(10, 1000), 35.16)        # 0.03 x 1000 = 30 beats 0.15% of 10,000
        self.assertEqual(core.leg_cost(20, 1000), 35.82)        # at Rs 20 the two minimums meet: 30 x 1.15 + 1.32
        self.assertEqual(core.leg_cost(10, 1000, commission=False), 0.66)

    def test_per_share_minimum_on_a_cheap_share(self):
        self.assertEqual(core.leg_cost(5, 1000), 34.83)       # 0.15% of 5,000 is 7.50, 0.03 x 1,000 = 30 wins: 34.50 + 0.33
        self.assertEqual(core.leg_cost(5, 1000, commission=False), 0.33)
        self.assertEqual(core.round_trip_cost(5, 5.5, 1000), 69.69)
        self.assertEqual(core.round_trip_cost(5, 5.5, 1000, {"day_trade_one_side": True}), 35.19)
        self.assertEqual(core.max_loss_at_stop(5.0, 4.5, 1000), 569.63)      # 500 + 34.83 + 34.80
        self.assertEqual((core.leg_cost(300, 0), core.round_trip_cost(300, 303, 0)), (0.0, 0.0))

    def test_round_trip_and_the_research_example(self):
        # research-costs.md: 1,000 shares 300 -> 303 is about Rs 1,079.67. It used Rs 6.548966 per Rs 100,000
        # of other fees (0.0065490%); the spec rounds that to 0.0066%, which gives Rs 1,079.97.
        self.assertEqual(core.round_trip_cost(300, 303, 1000), 1079.97)
        self.assertEqual(core.round_trip_cost(300, 303, 1000, dict(core.COSTS, other_charges_pct=0.00655)), 1079.67)
        self.assertEqual(core.round_trip_cost(300, 303, 1000, {"day_trade_one_side": True}), 557.3)
        self.assertEqual(core.round_trip_cost(300, 303, 1000, {"day_trade_one_side": "true"}), 1079.97)

    def test_max_loss_at_stop(self):
        self.assertEqual(core.max_loss_at_stop(100.65, 99.55, 2000), 2917.12)    # 2,200 + 360.53 + 356.59
        self.assertEqual(core.max_loss_at_stop(300, 297, 1000), 3000 + core.round_trip_cost(300, 297, 1000))
        self.assertEqual(core.max_loss_at_stop(100.65, 99.55, 0), 0.0)
        for bad in ((None, 99.55, 2000), (100.65, "x", 2000), (100.65, 99.55, None)):
            with self.assertRaises(ValueError):
                core.max_loss_at_stop(*bad)

    def test_cost_settings(self):
        self.assertEqual(core.cost_settings({}), core.COSTS)
        self.assertEqual(core.cost_settings(None), core.COSTS)
        c = core.cost_settings({"costs": {"commission_pct": 0.25, "sales_tax_pct": 16}})
        self.assertEqual((c["commission_pct"], c["sales_tax_pct"], c["commission_min_per_share"]), (0.25, 16, 0.03))
        self.assertIsNot(core.cost_settings({}), core.COSTS)
        self.assertEqual(core.leg_cost(300, 1000, c), 889.8)     # 750 x 1.16 + 19.80

    def test_bad_cost_settings(self):
        with self.assertRaises(ValueError):
            core.leg_cost(300, 1000, {"commission_pct": "lots"})
        with self.assertRaises(ValueError):
            core.leg_cost(300, 1000, {"sales_tax_pct": None})
        with self.assertRaises(ValueError):
            core.leg_cost(300, -5)
        with self.assertRaises(ValueError):
            core.leg_cost(300, 1000, {"other_charges_pct": -1})


# ---------------------------------------------------------------- v2: the style group and the swing helpers

MARKET = os.path.join(HERE, "fixtures", "psx-market")   # the made-up sample market (read only)
SWING_KEYS = ["stop", "target", "max_hold_sessions", "trend_exit_sessions", "entry_buffer_pct", "max_open_positions",
              "max_open_risk_pkr", "max_gap_loss_pkr", "stop_limit_gap_pct"]


def demo_to_10_28():
    """DEMO's daily bars from the 60 sample closing files to Wed 2026-10-28, and the missing sessions."""
    ss = core.recent_sessions(D(2026, 10, 28), 60, MARKET, None, True)
    return core.daily_bars("DEMO", ss), core.window_sessions([d for d, _ in ss])[1]


class TestStyle(unittest.TestCase):
    def test_the_constants(self):
        self.assertEqual(core.MATCH_MINUTES, 2)
        self.assertEqual(list(core.SWING), SWING_KEYS)
        self.assertEqual(core.SWING, {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20,
                                      "trend_exit_sessions": 10, "entry_buffer_pct": 1.0, "max_open_positions": 3,
                                      "max_open_risk_pkr": None, "max_gap_loss_pkr": None,
                                      "stop_limit_gap_pct": 1.0})

    def test_trade_style(self):
        for cfg in ({}, None, {"style": None}, {"style": "day"}, {"budget": 500000}):
            self.assertEqual(core.trade_style(cfg), "day", cfg)
        self.assertEqual(core.trade_style({"style": "swing"}), "swing")
        for bad in ("Swing", "invest", "", 1, True, ["swing"]):
            with self.assertRaises(ValueError) as e:
                core.trade_style({"style": bad})
            self.assertIn('"style" in psx/config.json is "day" or "swing", not ', str(e.exception))

    def test_swing_settings(self):
        self.assertEqual(core.swing_settings({}), core.SWING)
        self.assertEqual(core.swing_settings(None), core.SWING)
        self.assertEqual(core.swing_settings({"swing": "atr:3"}), core.SWING)        # not a block: the defaults
        s = core.swing_settings({"style": "swing", "swing": {"stop": "low:10", "max_gap_loss_pkr": 1500}})
        self.assertEqual((s["stop"], s["max_gap_loss_pkr"], s["target"], s["max_hold_sessions"]),
                         ("low:10", 1500, "2R", 20))
        self.assertEqual(list(s), SWING_KEYS)
        s["stop"] = "price:90"
        self.assertEqual(core.SWING["stop"], "atr:2")                                # a copy: SWING never changes

    def test_trade_costs(self):
        one = {"costs": {"day_trade_one_side": True}}
        self.assertIs(core.trade_costs(one)["day_trade_one_side"], True)             # day: as set
        self.assertIs(core.trade_costs({})["day_trade_one_side"], False)
        self.assertEqual(core.trade_costs(dict(one, style="day")), core.cost_settings(one))
        self.assertIs(core.trade_costs(dict(one, style="swing"))["day_trade_one_side"], False)   # swing: both sides
        c = core.trade_costs({"style": "swing", "costs": {"commission_pct": 0.2}})
        self.assertEqual((c["commission_pct"], c["sales_tax_pct"]), (0.2, 15))
        self.assertIsNot(core.trade_costs({}), core.COSTS)
        with self.assertRaises(ValueError):
            core.trade_costs({"style": "weekly"})
        # the swing example: 1,436 DEMO at 99.38, stop 96.25, commission on both sides
        swing = core.trade_costs(dict(one, style="swing"))
        self.assertEqual(core.max_loss_at_stop(99.38, 96.25, 1436, swing), 4997.81)
        self.assertEqual(core.max_loss_at_stop(99.38, 96.25, 143, swing), 497.69)
        self.assertEqual(core.round_trip_cost(99.38, 96.25, 1000, swing), 350.37)     # about 0.3504 a share


class TestAddSessions(unittest.TestCase):
    def test_the_swing_example(self):
        self.assertEqual(core.add_sessions(THU, 10), D(2026, 11, 13))        # Iqbal Day, Mon 2026-11-09, is skipped
        self.assertEqual(core.add_sessions(THU, 20), D(2026, 11, 27))
        self.assertEqual(core.add_sessions(THU, 1), FRI)
        self.assertEqual(core.add_sessions(FRI, 1), D(2026, 11, 2))          # over the weekend
        self.assertEqual(core.add_sessions(D(2026, 11, 6), 1), D(2026, 11, 10))   # Friday to Tuesday

    def test_zero_gives_the_same_day(self):
        self.assertEqual(core.add_sessions(THU, 0), THU)
        sat = D(2026, 10, 31)
        self.assertEqual(core.add_sessions(sat, 0), sat)
        self.assertEqual(core.add_sessions(sat, 1), D(2026, 11, 2))

    def test_the_config_calendar_counts(self):
        self.assertEqual(core.add_sessions(THU, 10, {"holidays": {"2026-11-12": "a test closure"}}), D(2026, 11, 16))
        self.assertEqual(core.add_sessions(THU, 10, {"open_days": ["2026-11-09"]}), D(2026, 11, 12))

    def test_bad_counts_raise(self):
        for bad in (-1, 1.5, True, None, "2"):
            with self.assertRaises(ValueError) as e:
                core.add_sessions(THU, bad)
            self.assertEqual(str(e.exception), "a number of sessions must be a whole number, 0 or more")


class TestSwingWindow(unittest.TestCase):
    def at(self, text, typ="limit", cfg=None):
        return core.swing_window(DT.fromisoformat(text), typ, cfg)

    def test_mon_to_thu(self):
        self.assertEqual(self.at("2026-10-29 09:20"), "")                           # a limit buy waits in the pre-open
        self.assertEqual(self.at("2026-10-29 09:20", " LIMIT "), "")
        self.assertEqual(self.at("2026-10-29 09:20", "market"),
                         "PSX takes no market orders in the pre-open: use a limit order")
        self.assertEqual(self.at("2026-10-29 09:20", None),
                         "PSX takes no market orders in the pre-open: use a limit order")
        self.assertEqual(self.at("2026-10-29 09:31"), "the opening match runs until 9:32 AM PKT: no new orders yet")
        self.assertEqual(self.at("2026-10-29 09:33"), "")
        self.assertEqual(self.at("2026-10-29 09:33", "market"), "")
        self.assertEqual(self.at("2026-10-29 15:25"), "")
        self.assertEqual(self.at("2026-10-29 15:10", "market"), "")                 # no_entries_after is a day rule
        self.assertEqual(self.at("2026-10-29 09:33", cfg={"no_entries_before": "09:45"}), "")
        self.assertEqual(self.at("2026-10-29 09:14"), "no new entries now: closed: the pre-open starts at 9:15 AM PKT")
        self.assertTrue(self.at("2026-10-29 15:31").startswith("no new entries now: closed: the session ended"))

    def test_friday(self):
        self.assertEqual(self.at("2026-10-30 14:20"), "")                           # the pre-open after the break
        self.assertTrue(self.at("2026-10-30 12:30").startswith("no new entries now: Juma break: "))
        self.assertEqual(self.at("2026-10-30 09:16"), "the opening match runs until 9:17 AM PKT: no new orders yet")
        self.assertEqual(self.at("2026-10-30 09:17"), "")
        self.assertEqual(self.at("2026-10-30 14:31"), "the opening match runs until 2:32 PM PKT: no new orders yet")
        self.assertEqual(self.at("2026-10-30 14:32"), "")

    def test_ramadan_and_closed_days(self):
        self.assertEqual(self.at("2026-03-02 09:16"), "the opening match runs until 9:17 AM PKT: no new orders yet")
        self.assertEqual(self.at("2026-10-31 10:00"), "no new entries now: closed all day (weekend)")
        self.assertEqual(self.at("2026-11-09 10:00"), "no new entries now: closed all day (PSX holiday: Iqbal Day)")
        self.assertEqual(self.at("2026-11-09 10:00", cfg={"open_days": ["2026-11-09"]}), "")

    def test_the_match_time_comes_from_the_constant(self):
        old = core.MATCH_MINUTES
        try:
            core.MATCH_MINUTES = 5
            self.assertEqual(self.at("2026-10-29 09:33"), "the opening match runs until 9:35 AM PKT: no new orders yet")
        finally:
            core.MATCH_MINUTES = old


class TestDailyLevels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.missing = demo_to_10_28()

    def bonus(self, back=5, factor=1.2):
        """DEMO's bars with a made-up bonus `back` bars from the end: older prices and LDCPs x factor (exact),
        the ex-date LDCP lowered to the close before / factor, the flag XB set."""
        k = len(self.bars) - back
        out = []
        for i, b in enumerate(self.bars):
            nb = dict(b)
            if i < k:
                for key in ("open", "high", "low", "close", "ldcp"):
                    nb[key] = b[key] * factor
            elif i == k:
                nb["ldcp"], nb["flags"] = self.bars[k - 1]["close"], ["XB"]
            out.append(nb)
        return out

    def test_demo_on_the_sample_market(self):
        self.assertEqual((len(self.bars), self.missing), (60, []))
        atr, n = core.daily_atr(self.bars, 14)
        self.assertEqual((round(atr, 4), n), (1.565, 14))
        adj = core.adjusted_bars(self.bars, None, self.missing)
        self.assertAlmostEqual(core.daily_atr(adj, 14)[0], atr, places=9)          # no payout: the same bars
        self.assertEqual(core.trend_break(core.adjusted_closes(self.bars, None, self.missing), 10), (92.36, False))
        self.assertEqual(min(b["low"] for b in self.bars[-10:]), 92.07)             # the lowest low of the last 10
        self.assertEqual(core.round_down_tick(99.38 - 2 * atr), 96.25)              # the atr:2 swing stop
        self.assertEqual(core.price_limits(98.40), (88.56, 108.24))

    def test_daily_atr(self):
        xd = [{"high": 101.0, "low": 99.0, "ldcp": 100.0}, {"high": 98.0, "low": 96.5, "ldcp": 97.0}]
        self.assertEqual(core.daily_atr(xd, 14), (1.75, 2))        # the XD bar's LDCP 97.00 is inside its range
        self.assertEqual(core.daily_atr([{"high": 105.0, "low": 104.0, "ldcp": 100.0}]), (5.0, 1))   # a gap up
        self.assertEqual(core.daily_atr([{"high": 105.0, "low": 104.0}]), (1.0, 1))      # no LDCP: high less low
        self.assertEqual(core.daily_atr([{"high": 105.0, "low": 104.0, "ldcp": None}]), (1.0, 1))
        self.assertEqual(core.daily_atr([{"high": 0, "low": 0, "ldcp": 50.0}]), (None, 0))   # no trade that day
        self.assertEqual(core.daily_atr([]), (None, 0))
        self.assertEqual(core.daily_atr(None), (None, 0))
        many = [{"high": 10.0 + i, "low": 9.0 + i} for i in range(20)]
        self.assertEqual(core.daily_atr(many, 5), (1.0, 5))

    def test_trend_break(self):
        self.assertEqual(core.trend_break([10, 11, 12, 11.5], 3), (10.0, False))
        self.assertEqual(core.trend_break([10, 11, 12, 9.99], 3), (10.0, True))
        self.assertEqual(core.trend_break([10, 11, 12, 10], 3), (10.0, False))      # at the level is not under it
        self.assertEqual(core.trend_break([1, 10, 11, 12, 9.5], 3), (10.0, True))   # only the n closes before count
        self.assertEqual(core.trend_break(["10", "11", "12", "9"], 3), (10.0, True))
        self.assertEqual(core.trend_break([1, 2, 3], 3), (None, False))             # too few closes
        self.assertEqual(core.trend_break([], 10), (None, False))
        self.assertEqual(core.trend_break(None), (None, False))

    def test_adjusted_bars(self):
        d = [D(2026, 10, 26), D(2026, 10, 27), D(2026, 10, 28)]
        bars = [{"date": d[0], "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "ldcp": 100.5, "flags": []},
                {"date": d[1], "open": 98.0, "high": 99.0, "low": 96.0, "close": 98.5, "ldcp": 98.0, "flags": ["XD"]},
                {"date": d[2], "open": 98.6, "high": 99.5, "low": 98.1, "close": 99.0, "ldcp": None, "flags": []}]
        before = [dict(b) for b in bars]
        adj = core.adjusted_bars(bars)
        self.assertEqual(bars, before)                                               # the input is not changed
        self.assertEqual([b["close"] for b in adj], core.adjusted_closes(bars))
        f = 98.0 / 101.0                                                             # the XD factor
        for key in ("open", "high", "low", "close", "ldcp"):
            self.assertAlmostEqual(adj[0][key], bars[0][key] * f, places=9)
        self.assertEqual(adj[1], bars[1])                                            # the ex-date bar: LDCP lowered already
        self.assertIsNone(adj[2]["ldcp"])                                            # no LDCP keeps none
        self.assertEqual(adj[2], bars[2])                                            # the newest bar stays as it is
        self.assertEqual(core.adjusted_bars([]), [])

    def test_a_bonus_does_not_change_the_atr(self):
        raw = self.bonus()
        plain = core.daily_atr(self.bars, 14)[0]
        adj = core.adjusted_bars(raw)
        self.assertAlmostEqual(core.daily_atr(adj, 14)[0], plain, places=9)          # 1.565, the same as no bonus
        self.assertEqual(round(core.daily_atr(adj, 14)[0], 4), 1.565)
        self.assertGreater(core.daily_atr(raw, 14)[0], plain + 0.1)                  # raw: older ranges on the old scale
        old = [dict(b, ldcp=r["ldcp"]) for b, r in zip(adj, raw)]                    # LDCPs left on the old scale
        self.assertEqual(round(core.daily_atr(old, 14)[0], 2), 12.75)

    def test_gap_loss(self):
        self.assertEqual(core.price_limits(99.38), (89.44, 109.32))
        self.assertEqual(core.gap_loss(99.38, 143, core.COSTS), 1469.78)
        self.assertEqual(core.gap_loss(99.38, 1436, core.COSTS), 14759.46)
        self.assertEqual(core.gap_loss(99.38, 143), core.max_loss_at_stop(99.38, 89.44, 143, core.COSTS))
        self.assertEqual(round(core.gap_loss(99.38, 1000, core.COSTS) / 1000, 4), 10.2782)
        self.assertEqual(core.gap_loss(5.0, 1000), core.max_loss_at_stop(5.0, 4.0, 1000))    # Rs 1 under Rs 10
        self.assertEqual(core.gap_loss(99.38, 0), 0.0)
        with self.assertRaises(ValueError):
            core.gap_loss(0, 100)

    def test_adjusted_closes(self):
        d = [D(2026, 10, 23), D(2026, 10, 27), D(2026, 10, 28)]
        xd = [{"date": d[0], "close": 100.0, "ldcp": 99.0, "flags": []},
              {"date": d[1], "close": 96.0, "ldcp": 97.0, "flags": ["XD"]},     # a Rs 3 payout: LDCP 97, not 100
              {"date": d[2], "close": 97.0, "ldcp": 96.0, "flags": []}]
        self.assertEqual([round(x, 4) for x in core.adjusted_closes(xd)], [97.0, 96.0, 97.0])
        self.assertEqual(core.adjusted_closes(xd, None, [D(2026, 10, 26)]), [100.0, 96.0, 97.0])   # never across a gap
        split = [dict(xd[0], close=100.0), dict(xd[1], close=51.0, ldcp=50.0, flags=[]), xd[2]]
        self.assertEqual(core.adjusted_closes(split, {d[1]: d[0]})[0], 50.0)       # sessions next to each other
        self.assertEqual(core.adjusted_closes(split, {d[1]: D(2026, 10, 26)})[0], 100.0)   # a day with no trade between
        self.assertEqual(core.adjusted_closes([]), [])

    def test_window_sessions(self):
        self.assertEqual(core.window_sessions([]), ([], []))
        every, missing = core.window_sessions([D(2026, 11, 11), D(2026, 11, 6)])
        self.assertEqual(every, [D(2026, 11, 6), D(2026, 11, 10), D(2026, 11, 11)])   # Iqbal Day is not a session
        self.assertEqual(missing, [D(2026, 11, 10)])
        self.assertEqual(core.window_sessions([D(2026, 11, 6), D(2026, 11, 9), D(2026, 11, 10)]),
                         ([D(2026, 11, 6), D(2026, 11, 9), D(2026, 11, 10)], []))      # a file on a holiday counts
        self.assertEqual(core.window_sessions([D(2026, 11, 6), D(2026, 11, 10)], {"open_days": ["2026-11-09"]})[1],
                         [D(2026, 11, 9)])


# ---------------------------------------------------------------- PSX files (made-up data)

class TestNum(unittest.TestCase):
    def test_numbers(self):
        cases = [("1,234.5", 1234.5), ("(18.71)", -18.71), ("19.34%", 19.34), ("-0.43", -0.43),
                 (chr(0x2212) + "5.5", -5.5),
                 ("Rs.206.21", 206.21), (" 1,357,900 ", 1357900.0), ("+1.5", 1.5), (3, 3.0), (2.5, 2.5),
                 ("(0.30)", -0.3), ("4.80", 4.8)]
        for text, want in cases:
            self.assertEqual(core.num(text), want, text)

    def test_not_numbers(self):
        for text in ("N/A", "n/a", "", "-", "  ", None, "abc", "nan", "inf", True, float("nan")):
            self.assertIsNone(core.num(text), text)


class TestMktSummary(unittest.TestCase):
    def test_zip(self):
        rows = core.read_mkt_summary(zip_bytes("closing11.lis", lis()))
        self.assertEqual(len(rows), 5)
        og = rows[0]
        self.assertEqual(og, {"date": THU, "symbol": "OGDC", "sector": "0820", "short_name": "Oil & Gas DevXD",
                              "open": 206.0, "high": 207.99, "low": 203.0, "close": 206.21, "volume": 1357900,
                              "ldcp": 203.68, "flags": ["XD"]})
        self.assertEqual(rows[1]["volume"], 1234567)
        self.assertEqual(rows[3]["flags"], ["XD", "XB"])
        self.assertEqual(rows[2]["open"], 0.0)

    def test_gzip_and_plain(self):
        a = core.read_mkt_summary(gzip.compress(lis().encode("ascii")))
        b = core.read_mkt_summary(lis().encode("ascii"))
        c = core.read_mkt_summary(b"\xef\xbb\xbf" + lis().replace("\r\n", "\n").encode("ascii"))
        self.assertEqual(a, b)
        self.assertEqual(b, c)

    def test_empty_open_and_odd_numbers(self):
        rows = core.read_mkt_summary(b"02JAN2015|NPSM|3760|N. P. Spinning||36.89|36.89|38.83|500|38.8278|||\r\n")
        self.assertEqual((rows[0]["open"], rows[0]["ldcp"], rows[0]["date"]), (None, 38.8278, D(2015, 1, 2)))

    def test_skips_lines_that_are_not_rows(self):
        text = "header line\r\n\r\nxx|YY|0805|n|1|1|1|1|1|1|||\r\n" + lis()
        self.assertEqual(len(core.read_mkt_summary(text.encode("ascii"))), 5)

    def test_html_and_junk_raise(self):
        with self.assertRaises(ValueError) as cm:
            core.read_mkt_summary(b"<!DOCTYPE html><html><head></head><body>not found</body></html>")
        self.assertIn("HTML", str(cm.exception))
        for junk in (b"", b"PK\x03\x04broken", b"\x1f\x8bbroken", b"hello|world"):
            with self.assertRaises(ValueError):
                core.read_mkt_summary(junk)

    def test_broken_zip_and_gzip_raise_value_error(self):
        # a broken file used to raise RuntimeError, NotImplementedError, zlib.error or EOFError
        raw = zip_bytes("closing11.lis", lis())
        cd = raw.rfind(b"PK\x01\x02")                          # the zip's central directory entry
        gz = gzip.compress(lis().encode("ascii"))
        for data in (put(raw, cd + 8, b"\x01\x00"),            # says it is encrypted: RuntimeError
                     put(raw, cd + 10, b"\x63\x00"),           # an unknown way to pack it: NotImplementedError
                     put(raw, 30 + len("closing11.lis"), b"\xff\xff\xff"),     # bad packed data: zlib.error
                     put(gz, 10, b"\xff\xff\xff"),             # zlib.error
                     gz[:40]):                                 # cut short: EOFError
            with self.assertRaises(ValueError) as cm:
                core.read_mkt_summary(data)
            self.assertIn("not a PSX closing file", str(cm.exception))

    @unittest.skipUnless(os.path.exists(PROBE404), "no saved 404 page")
    def test_real_404_page(self):
        with self.assertRaises(ValueError):
            core.read_mkt_summary(read_bytes(PROBE404))


class TestSymbolNames(unittest.TestCase):
    def test_utf16_with_a_bom_on_every_piece(self):
        raw = utf16_names([("786", "786 Invest Ltd", "786 Investments Limited"),
                           ("BAPLR1", "Bawany Air(R)", "Bawany Air Products LimitedRight")])
        want = {"786": ("786 Invest Ltd", "786 Investments Limited"),
                "BAPLR1": ("Bawany Air(R)", "Bawany Air Products LimitedRight")}
        self.assertEqual(core.read_symbol_names(zip_bytes("symbolname.lis", raw)), want)
        self.assertEqual(core.read_symbol_names(raw), want)
        self.assertEqual(core.read_symbol_names("786|a|b|\nX|c|d|\n".encode("utf-8")),
                         {"786": ("a", "b"), "X": ("c", "d")})

    def test_html_raises(self):
        with self.assertRaises(ValueError):
            core.read_symbol_names(b"<html>not found</html>")

    def test_broken_zip_raises_value_error(self):
        raw = zip_bytes("symbolname.lis", utf16_names([("786", "786 Invest Ltd", "786 Investments Limited")]))
        cd = raw.rfind(b"PK\x01\x02")
        for data in (put(raw, cd + 8, b"\x01\x00"), put(raw, cd + 10, b"\x63\x00"),
                     put(raw, 30 + len("symbolname.lis"), b"\xff\xff\xff"), raw[:20]):
            with self.assertRaises(ValueError) as cm:
                core.read_symbol_names(data)
            self.assertIn("not a PSX symbol name file", str(cm.exception))


class TestSymbolKind(unittest.TestCase):
    def test_kinds(self):
        cases = [("OGDC", "0820", "Oil & Gas Development Company Limited", "ordinary"),
                 ("PIAHCLB", "0813", "PIA Holding Company LimitedB", "ordinary"),
                 ("DCR", "0836", "Dolmen City REIT", "reit"),
                 ("POWER", "0804", "Power Cement Limited", "ordinary"),
                 ("BAPLR1", "0805", "Bawany Air Products LimitedRight", "right"),
                 ("CENIR", "0812", "Century Insurance(R)", "right"),
                 ("PINLR", "0812", "Premier Insurance Limited (RIGHT)", "right"),
                 ("POWERPS", "0804", "Power Cement (Pref)", "preference"),
                 ("ASLCPS", "0808", "Aisha Steel Convt (Pref) Right", "preference"),
                 ("POWERPS", "0804", "Power Cem (PR)", "preference"),
                 ("ANLNV", "0829", "Azgard Nine Non-Voting Ordinary Shares", "non_voting"),
                 ("ANLNV", "0829", "Azgard(Non-Voti)(PR)", "non_voting"),
                 ("AGLNCPS", "0805", "Agritech Non-Voting (Pref) Class A", "preference"),
                 ("GEMMEL", "0824", "Mughal Energy Limited(GEM)", "gem_equity"),
                 ("GEMBCEM", "0819", "Burj Clean Energy Modaraba(GEM)", "modaraba"),
                 ("HGFA", "0806", "", "closed_end_fund"), ("MIIETF", "0837", "", "etf"),
                 ("P01GHS020927", "36", "1 Year GHS", "debt"), ("OGDC-NOV", "40", "", "stock_future"),
                 ("KSE30-NOV", "41", "", "index_future"), ("OGDC-NOVB", "", "", "stock_future"),
                 ("KSE30-DEC", "", "", "index_future"), ("SBR", "0829", "Shams Bright", "ordinary"),
                 ("786R", "0813", "786 Investment (Right)", "right")]
        for sym, sec, name, want in cases:
            self.assertEqual(core.symbol_kind(sym, sec, name), want, (sym, name))
        self.assertEqual(core.symbol_kind("ogdc", " 0820 "), "ordinary")
        self.assertEqual(core.symbol_kind(None, None), "ordinary")


class TestPages(unittest.TestCase):
    def test_parse_company_made_up_page(self):
        r = core.parse_company(demo_page())
        self.assertEqual((r["symbol"], r["name"], r["flags"], r["sector"]),
                         ("DEMO", "Demo Industries Limited", ["XD"], "CHEMICAL"))
        self.assertEqual((r["price"], r["change"], r["change_pct"], r["as_of"]),
                         (101.5, 1.5, 1.5, DT(2026, 10, 29, 10, 16)))
        self.assertEqual(r["reg"], {"open": 100.2, "high": 101.8, "low": 99.9, "volume": 1234567, "lower": 90.0,
                                    "upper": 110.0, "day_low": 99.9, "day_high": 101.8, "wk52_low": 80.0,
                                    "wk52_high": 120.0, "ask": 101.55, "ask_volume": 500, "bid": 101.45,
                                    "bid_volume": 1200, "ldcp": 100.0, "var": 12.0, "haircut": 15.0, "pe": None,
                                    "change_1y": -5.25, "change_ytd": 3.1})
        self.assertEqual(r["profile"], {"fiscal_year_end": "June", "website": "www.demo.example",
                                        "auditor": "Demo & Co.", "registrar": "Demo Registrar",
                                        "address": "1 Demo Road, Karachi",
                                        "description": "Demo makes & sells demo goods.",
                                        "people": [["A. Person", "CEO"], ["B. Person", "Chairperson"]]})
        self.assertEqual(r["equity"], {"market_cap_000": 10150000.0, "shares": 100000000, "face_value": 10.0,
                                       "free_float_shares": 25000000, "free_float_pct": 25.0})

    def test_parse_company_announcements(self):
        a = core.parse_company(demo_page())["announcements"]
        self.assertEqual(a["Financial Results"], [{
            "date": "2026-10-28", "title": "FINANCIAL RESULTS FOR THE QUARTER ENDED SEPTEMBER 30, 2026",
            "doc_id": "287001", "pdf": "https://dps.psx.com.pk/download/document/287001.pdf",
            "image": "https://dps.psx.com.pk/download/image/287001-1.gif", "attachment": None}])
        self.assertEqual(a["Board Meetings"][0]["title"], "Board Meeting (REVISED)")
        self.assertEqual((a["Board Meetings"][0]["doc_id"], a["Board Meetings"][0]["pdf"]), ("286001", None))
        o = a["Others"][0]
        self.assertEqual((o["date"], o["title"], o["doc_id"], o["attachment"], o["image"]),
                         ("2026-09-09", "DEMO | Demo Industries Limited Notice to shareholders", "285001",
                          "https://dps.psx.com.pk/download/attachment/285001-1.pdf", None))

    def test_parse_company_tables(self):
        r = core.parse_company(demo_page())
        self.assertEqual(r["financials"]["annual"], {"columns": ["2026", "2025"],
                                                     "rows": {"Sales": [1000000.0, 900000.0], "EPS": [-1.25, None]}})
        self.assertEqual(r["financials"]["quarterly"], {"columns": ["Q1 2027"], "rows": {"Sales": [250000.0]}})
        self.assertEqual(r["ratios"], {"columns": ["2026"], "rows": {"EPS Growth (%)": [-18.71]}})

    def test_parse_company_never_fails(self):
        for page in ("", "<html>not found</html>", None, demo_page()[:3000], demo_page()[:9000], 42):
            r = core.parse_company(page)
            self.assertEqual(sorted(r), sorted(["symbol", "name", "flags", "sector", "price", "change", "change_pct",
                                                "as_of", "reg", "profile", "equity", "announcements", "financials",
                                                "ratios"]))
            self.assertEqual(sorted(r["announcements"]), ["Board Meetings", "Financial Results", "Others"])
            self.assertEqual(sorted(r["financials"]), ["annual", "quarterly"])
        r = core.parse_company("")
        self.assertIsNone(r["reg"]["lower"])
        self.assertEqual(r["profile"]["people"], [])

    def test_parse_screener_made_up_page(self):
        s = core.parse_screener(screener_page())
        self.assertEqual(s["clock"], "Oct 29, 2026 10:21 AM")
        self.assertEqual(s["rows"]["DEMO"], {
            "symbol": "DEMO", "name": "Demo Industries Limited", "flags": ["XD"], "sector": "0805",
            "listed_in": ["ALLSHR", "KSE100"], "market_cap": 10150000000.0, "price": 101.5, "change_pct": 1.5,
            "change_1y_pct": -5.25, "pe": 8.4, "dividend_yield": 2.1, "free_float": 25000000.0,
            "volume_30d_avg": 512345.5})
        n = s["rows"]["NOPX"]
        self.assertEqual((n["name"], n["flags"], n["sector"], n["listed_in"], n["price"]),
                         ("No Price & Co", ["NC"], None, [], 0.0))
        self.assertEqual(core.parse_screener(""), {"clock": None, "rows": {}})

    def test_parse_indices_made_up_page(self):
        ix = core.parse_indices(indices_page())
        self.assertEqual(ix["as_of"], DT(2026, 10, 29, 15, 50))
        self.assertEqual(ix["rows"]["KSE100"], {"high": 167483.48, "low": 165907.29, "current": 167193.43,
                                                "change": 1456.68, "change_pct": 0.8789})
        self.assertEqual(ix["rows"]["HBLTTI"], {"high": None, "low": None, "current": 19290.31, "change": 5.81,
                                                "change_pct": 0.03})
        self.assertEqual(core.parse_indices(""), {"as_of": None, "rows": {}})

    def test_html_text_and_page_time(self):
        self.assertEqual(core.html_text("<b>Oil &amp; Gas</b>\t  Dev <i></i>"), "Oil & Gas Dev")
        self.assertEqual(core.html_text(None), "")
        self.assertEqual(core.page_time("Thu, Oct 29, 2026 3:49 PM"), DT(2026, 10, 29, 15, 49))
        self.assertEqual(core.page_time("As of  Oct 29, 2026 3:50 PM"), DT(2026, 10, 29, 15, 50))
        self.assertEqual(core.page_time("Oct 30, 2026 12:18 AM"), DT(2026, 10, 30, 0, 18))
        self.assertEqual(core.page_time("Fri, Feb 16, 2018 12:10 PM"), DT(2018, 2, 16, 12, 10))
        for bad in ("", None, "Foo 29, 2026 3:49 PM", "Feb 30, 2026 3:49 PM", "yesterday"):
            self.assertIsNone(core.page_time(bad))


# ---------------------------------------------------------------- data access

class TestHttpGet(unittest.TestCase):
    def test_plain_get_with_two_headers(self):
        url = "https://dps.psx.com.pk/screener"
        with FakeNet({url: b"<html>ok</html>"}) as net:
            self.assertEqual(core.http_get(url), b"<html>ok</html>")
        self.assertEqual(net.calls, [(url, {"User-agent": core.UA, "Accept-encoding": "gzip"}, 30)])

    def test_gunzip_only_when_the_server_says_so(self):
        url = "https://dps.psx.com.pk/indices"
        with FakeNet({url: gzip.compress(b"<html>x</html>")}, {url: {"Content-Encoding": "gzip"}}):
            self.assertEqual(core.http_get(url), b"<html>x</html>")
        old = gzip.compress(b"02JAN2015|A|0805|a|1|1|1|1|1|1|||")
        with FakeNet({url: old}):
            self.assertEqual(core.http_get(url), old)       # an old gzip closing file stays as it is

    def test_pause_between_requests(self):
        url = "https://dps.psx.com.pk/screener"
        with FakeNet({url: b"x"}) as net:
            core.http_get(url)
            core.http_get(url, timeout=5)
        self.assertEqual(len(net.sleeps), 1)
        self.assertTrue(1.0 < net.sleeps[0] <= core.PAUSE, net.sleeps)
        self.assertEqual(net.calls[1][2], 5)

    def test_errors_raise(self):
        url = "https://dps.psx.com.pk/download/mkt_summary/2026-10-31.Z"
        with FakeNet({url: 404}):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                core.http_get(url)
            self.assertEqual(cm.exception.code, 404)
        with FakeNet({url: urllib.error.URLError("no route")}):
            with self.assertRaises(urllib.error.URLError):
                core.http_get(url)

    def test_cut_or_broken_answers_are_url_errors(self):
        # an answer cut off while reading (IncompleteRead) or a broken gzip body used to escape as another error
        url = "https://dps.psx.com.pk/screener"
        for resp in (CutResp(), FakeResp(b"\x1f\x8b\x08\x00broken", {"Content-Encoding": "gzip"})):
            with FakeNet({url: resp}) as net:
                with self.assertRaises(urllib.error.URLError):
                    core.http_get(url)
            self.assertEqual(len(net.calls), 1)

    def test_site_on(self):
        self.assertTrue(core.site_on({"psx_site": True}))
        for cfg in ({"psx_site": "true"}, {"psx_site": 1}, {"psx_site": False}, {}, None, "psx_site"):
            self.assertFalse(core.site_on(cfg), cfg)


class TestDailyFiles(TempRoot):
    def test_daily_path(self):
        base = os.path.join(self.root, "mkt_summary", "2026-10-29")
        self.assertEqual(core.daily_path(self.root, THU), base + ".Z")
        self.put_day(THU, ".lis")
        self.assertEqual(core.daily_path(self.root, THU), base + ".lis")
        self.put_day(THU, ".zip")
        self.assertEqual(core.daily_path(self.root, THU), base + ".lis")
        self.put_day(THU, ".Z")
        self.assertEqual(core.daily_path(self.root, THU), base + ".Z")

    def test_local_file_first(self):
        self.put_day(THU)
        rows = core.daily_rows(THU, self.root, {"psx_site": True})
        self.assertEqual([r["symbol"] for r in rows], ["OGDC", "DEMO", "ZERO", "JLICL", "OGDC-NOV"])
        self.put(os.path.join("mkt_summary", "2026-10-28.lis"), lis(day_code(D(2026, 10, 28))))
        self.assertEqual(core.daily_rows(D(2026, 10, 28), self.root)[0]["date"], D(2026, 10, 28))

    def test_missing_with_the_site_off_or_offline(self):
        self.assertIsNone(core.daily_rows(THU, self.root))
        self.assertIsNone(core.daily_rows(THU, self.root, {"psx_site": False}))
        self.assertIsNone(core.daily_rows(THU, self.root, {"psx_site": True}, offline=True))

    def test_fetch_and_save_when_the_site_is_on(self):
        url = "https://dps.psx.com.pk/download/mkt_summary/2026-10-29.Z"
        raw = zip_bytes("closing11.lis", lis())
        with FakeNet({url: raw}) as net:
            rows = core.daily_rows(THU, self.root, {"psx_site": True})
            again = core.daily_rows(THU, self.root, {"psx_site": True})
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows, again)
        self.assertEqual([c[0] for c in net.calls], [url])                 # fetched once, then read from disk
        self.assertEqual(read_bytes(os.path.join(self.root, "mkt_summary", "2026-10-29.Z")), raw)
        self.assertEqual(os.listdir(os.path.join(self.root, "mkt_summary")), ["2026-10-29.Z"])

    def test_404_and_html_mean_no_session(self):
        cfg = {"psx_site": True}
        with FakeNet({}) as net:
            self.assertIsNone(core.daily_rows(D(2026, 10, 31), self.root, cfg))
        self.assertEqual(len(net.calls), 1)
        url = "https://dps.psx.com.pk/download/mkt_summary/2026-08-14.Z"
        with FakeNet({url: b"<!DOCTYPE html><html>Page not found</html>"}):
            self.assertIsNone(core.daily_rows(D(2026, 8, 14), self.root, cfg))
        self.assertFalse(os.path.exists(os.path.join(self.root, "mkt_summary")))
        self.put(os.path.join("mkt_summary", "2026-10-24.Z"), b"<!DOCTYPE html><html>saved 404</html>")
        self.assertIsNone(core.daily_rows(D(2026, 10, 24), self.root))

    def test_other_http_errors_raise(self):
        url = "https://dps.psx.com.pk/download/mkt_summary/2026-10-29.Z"
        with FakeNet({url: 503}):
            with self.assertRaises(urllib.error.HTTPError):
                core.daily_rows(THU, self.root, {"psx_site": True})

    def test_broken_or_misnamed_file_raises(self):
        self.put(os.path.join("mkt_summary", "2026-10-29.Z"), b"PK\x03\x04broken")
        with self.assertRaises(ValueError) as cm:
            core.daily_rows(THU, self.root)
        self.assertIn("2026-10-29.Z", str(cm.exception))
        self.put(os.path.join("mkt_summary", "2026-10-28.Z"), zip_bytes("closing11.lis", lis()))
        with self.assertRaises(ValueError) as cm:
            core.daily_rows(D(2026, 10, 28), self.root)
        self.assertIn("2026-10-29", str(cm.exception))

    def test_recent_sessions_walks_back_over_trading_days(self):
        for d in (D(2026, 10, 23), D(2026, 10, 26), D(2026, 10, 27), D(2026, 10, 28), THU):
            self.put_day(d)
        got = core.recent_sessions(FRI, 3, self.root)
        self.assertEqual([d for d, _ in got], [D(2026, 10, 27), D(2026, 10, 28), THU])     # newest last
        self.assertEqual(len(got[0][1]), 5)
        got = core.recent_sessions(THU, 3, self.root, include_end=False)
        self.assertEqual([d for d, _ in got], [D(2026, 10, 26), D(2026, 10, 27), D(2026, 10, 28)])
        got = core.recent_sessions(FRI, 5, self.root)
        self.assertEqual([d for d, _ in got][0], D(2026, 10, 23))                          # over the weekend
        self.assertEqual(len(core.recent_sessions(FRI, 10, self.root, max_back=2)), 1)
        self.assertEqual(core.recent_sessions(FRI, 0, self.root), [])

    def test_recent_sessions_never_fetches_weekends_or_holidays(self):
        cfg = {"psx_site": True}
        answers = {}
        for d in (D(2026, 8, 13), D(2026, 8, 17)):
            answers["https://dps.psx.com.pk/download/mkt_summary/%s.Z" % d.isoformat()] = zip_bytes(
                "closing11.lis", lis(day_code(d)))
        with FakeNet(answers) as net:
            got = core.recent_sessions(D(2026, 8, 17), 2, self.root, cfg)
        self.assertEqual([d for d, _ in got], [D(2026, 8, 13), D(2026, 8, 17)])
        self.assertEqual([c[0][-12:] for c in net.calls], ["2026-08-17.Z", "2026-08-13.Z"])
        self.assertEqual(len(net.sleeps), 1)
        with FakeNet({}) as net:
            self.assertEqual([d for d, _ in core.recent_sessions(D(2026, 8, 17), 2, self.root, cfg, offline=True)],
                             [D(2026, 8, 13), D(2026, 8, 17)])
        self.assertEqual(net.calls, [])

    def test_a_saved_file_on_a_listed_holiday_is_used(self):
        self.put_day(D(2026, 11, 9))
        self.put_day(D(2026, 11, 10))
        got = core.recent_sessions(D(2026, 11, 10), 2, self.root, {"psx_site": True})
        self.assertEqual([d for d, _ in got], [D(2026, 11, 9), D(2026, 11, 10)])

    def test_daily_bars(self):
        sessions = [(D(2026, 10, 28), core.read_mkt_summary(lis(day_code(D(2026, 10, 28))).encode())),
                    (THU, core.read_mkt_summary(lis().encode()))]
        bars = core.daily_bars("ogdc", sessions)
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[1], {"date": THU, "open": 206.0, "high": 207.99, "low": 203.0, "close": 206.21,
                                   "volume": 1357900, "ldcp": 203.68, "flags": ["XD"]})
        self.assertEqual(core.daily_bars("ZERO", sessions), [])                     # OPEN/HIGH/LOW of 0
        self.assertEqual(core.daily_bars("NONE", sessions), [])
        as_dict = [(d, dict((r["symbol"], r) for r in rows)) for d, rows in reversed(sessions)]
        self.assertEqual(core.daily_bars("OGDC", as_dict), bars)


class TestPagesOnDisk(TempRoot):
    def test_saved_page_when_the_site_is_off(self):
        self.put(os.path.join("company", "DEMO.html"), demo_page())
        with FakeNet({}) as net:
            text, src = core.company_html("demo", self.root, {"psx_site": False})
        self.assertEqual(src, "saved")
        self.assertEqual(core.parse_company(text)["symbol"], "DEMO")
        self.assertEqual(net.calls, [])

    def test_site_on_asks_the_site_first_so_an_old_page_never_hides_today(self):
        self.put(os.path.join("company", "DEMO.html"), demo_page().replace("Rs.101.50", "Rs.99.00"))
        url = "https://dps.psx.com.pk/company/DEMO"
        with FakeNet({url: demo_page().encode("utf-8")}) as net:
            text, src = core.company_html("DEMO", self.root, {"psx_site": True})
        self.assertEqual((src, core.parse_company(text)["price"]), ("site", 101.5))
        self.assertEqual(len(net.calls), 1)

    def test_site_failure_falls_back_to_the_saved_page(self):
        self.put(os.path.join("company", "DEMO.html"), demo_page())
        with FakeNet({"https://dps.psx.com.pk/company/DEMO": 404}):
            text, src = core.company_html("DEMO", self.root, {"psx_site": True})
        self.assertEqual((src, core.parse_company(text)["symbol"]), ("saved", "DEMO"))
        with FakeNet({}) as net:
            text, src = core.company_html("DEMO", self.root, {"psx_site": True}, offline=True)
        self.assertEqual((src, net.calls), ("saved", []))

    def test_site_off_says_what_to_save(self):
        text, why = core.company_html("OGDC", self.root)
        self.assertIsNone(text)
        self.assertEqual(why, core.SITE_OFF % ("https://dps.psx.com.pk/company/OGDC as %s"
                                               % os.path.join(self.root, "company", "OGDC.html")))
        text, why = core.screener_html(self.root, {"psx_site": False})
        self.assertIn("https://dps.psx.com.pk/screener as " + os.path.join(self.root, "screener.html"), why)

    def test_offline_never_fetches(self):
        with FakeNet({}) as net:
            text, why = core.company_html("OGDC", self.root, {"psx_site": True}, offline=True)
        self.assertIsNone(text)
        self.assertIn("--offline", why)
        self.assertEqual(net.calls, [])

    def test_site_on_fetches_and_does_not_save(self):
        url = "https://dps.psx.com.pk/company/DEMO"
        with FakeNet({url: demo_page().encode("utf-8")}) as net:
            text, src = core.company_html("DEMO", self.root, {"psx_site": True})
        self.assertEqual((src, core.parse_company(text)["price"]), ("site", 101.5))
        self.assertEqual(net.calls[0][0], url)
        self.assertFalse(os.path.exists(os.path.join(self.root, "company")))
        with FakeNet({"https://dps.psx.com.pk/screener": screener_page().encode("utf-8")}):
            text, src = core.screener_html(self.root, {"psx_site": True})
        self.assertEqual((src, len(core.parse_screener(text)["rows"])), ("site", 2))

    def test_fetch_problems_are_plain_reasons(self):
        with FakeNet({"https://dps.psx.com.pk/company/NOPE": 404}):
            self.assertEqual(core.company_html("NOPE", self.root, {"psx_site": True}),
                             (None, "PSX answered HTTP 404 for https://dps.psx.com.pk/company/NOPE"))
        with FakeNet({"https://dps.psx.com.pk/company/OGDC": urllib.error.URLError("no route")}):
            text, why = core.company_html("OGDC", self.root, {"psx_site": True})
        self.assertIsNone(text)
        self.assertIn("could not reach PSX", why)

    def test_a_cut_answer_is_a_plain_reason(self):
        url = "https://dps.psx.com.pk/company/OGDC"
        for resp, words in ((CutResp(), "cut off"),
                            (FakeResp(b"\x1f\x8b\x08\x00broken", {"Content-Encoding": "gzip"}), "broken gzip")):
            with FakeNet({url: resp}):
                text, why = core.company_html("OGDC", self.root, {"psx_site": True})
            self.assertIsNone(text)
            self.assertIn("could not reach PSX for %s" % url, why)
            self.assertIn(words, why)
        self.assertFalse(os.path.exists(os.path.join(self.root, "company")))

    def test_symbol_is_cleaned(self):
        self.assertEqual(core.company_html("", self.root), (None, "no symbol"))
        text, why = core.company_html("../../etc", self.root)
        self.assertIn(os.path.join(self.root, "company", "ETC.html"), why)

    def test_page_html_any_page(self):
        path = self.put("indices.html", indices_page())
        text, src = core.page_html(path, core.PSX + "/indices")
        self.assertEqual((src, core.parse_indices(text)["as_of"]), ("saved", DT(2026, 10, 29, 15, 50)))


# ---------------------------------------------------------------- tape and bars

def csnap(asof, price, op, hi, lo, vol, sym="DEMO"):
    return {"fetched": "", "asof": asof, "source": "company", "symbol": sym, "price": price, "open": op, "high": hi,
            "low": lo, "volume": vol, "ldcp": 100.0, "lower": 90.0, "upper": 110.0, "bid": None, "ask": None}


COMPANY_TAPE = [
    csnap("2026-10-29 09:25", 100.0, 0, 0, 0, 0),              # pre-open: no trade yet, dropped
    csnap("2026-10-29 09:33", 100.4, 100.2, 100.5, 100.1, 10000),
    csnap("2026-10-29 09:34", 100.3, 100.2, 100.5, 100.1, 12000),
    csnap("2026-10-29 09:41", 100.6, 100.2, 100.7, 100.1, 20000),   # a new day high
    csnap("2026-10-29 09:44", 100.55, 100.2, 100.7, 100.1, 21000),
    csnap("2026-10-29 09:52", 99.9, 100.2, 100.7, 99.8, 30000),     # a new day low
    csnap("2026-10-29 15:49", 101.0, 100.2, 101.2, 99.8, 90000),    # post-close: dropped
    csnap("2026-10-28 10:00", 95.0, 95.0, 95.0, 95.0, 5),           # another day: dropped
    csnap("2026-10-29 10:00", 50.0, 50.0, 50.0, 50.0, 5, "OTHER"),  # another symbol
]


class TestTape(TempRoot):
    def test_tape_path_and_files(self):
        path = core.tape_path(self.root, THU, "market")
        self.assertEqual(path, os.path.join(self.root, "tape", "2026-10-29", "market.jsonl"))
        self.assertEqual(os.path.basename(core.tape_path(self.root, THU, "../x")), "___x.jsonl")
        self.assertEqual(core.read_tape(path), [])
        core.append_tape(path, {"asof": "2026-10-29 09:31:00", "prices": {"DEMO": 100.4}})
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("not json\n[1, 2]\n\n")
        core.append_tape(path, {"asof": "2026-10-29 09:36:00", "prices": {"DEMO": 100.5}})
        got = core.read_tape(path)
        self.assertEqual([s["asof"] for s in got], ["2026-10-29 09:31:00", "2026-10-29 09:36:00"])
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.loads(fh.readline()), got[0])

    def test_snapshot_from_company(self):
        snap = core.snapshot_from_company(core.parse_company(demo_page()), DT(2026, 10, 29, 5, 21, 7))
        self.assertEqual(snap, {"fetched": "2026-10-29T10:21:07+05:00", "asof": "2026-10-29 10:16",
                                "source": "company", "symbol": "DEMO", "price": 101.5, "open": 100.2, "high": 101.8,
                                "low": 99.9, "volume": 1234567, "ldcp": 100.0, "lower": 90.0, "upper": 110.0,
                                "bid": 101.45, "ask": 101.55})
        empty = core.snapshot_from_company(core.parse_company(""), DT(2026, 10, 29, 5, 21))
        self.assertEqual((empty["asof"], empty["price"], empty["symbol"]), (None, None, None))

    def test_snapshot_from_screener(self):
        snap = core.snapshot_from_screener(core.parse_screener(screener_page()), DT(2026, 10, 29, 5, 21, 30))
        self.assertEqual(snap, {"fetched": "2026-10-29T10:21:30+05:00", "asof": "2026-10-29 10:16:30",
                                "source": "screener", "prices": {"DEMO": 101.5}})
        snap = core.snapshot_from_screener({"rows": {}}, DT(2026, 10, 29, 5, 21), delay_minutes=0)
        self.assertEqual(snap["asof"], "2026-10-29 10:21:00")

    def test_bars_from_a_company_tape(self):
        bars = core.bars_from_tape(COMPANY_TAPE, THU, "DEMO")
        self.assertEqual(len(bars), 72)
        self.assertEqual(bars[0], {"start": DT(2026, 10, 29, 9, 30), "open": 100.2, "high": 100.5, "low": 100.1,
                                   "close": 100.3, "volume": 12000, "n": 2})
        self.assertIsNone(bars[1])
        self.assertEqual(bars[2], {"start": DT(2026, 10, 29, 9, 40), "open": 100.6, "high": 100.7, "low": 100.55,
                                   "close": 100.55, "volume": 9000, "n": 2})
        self.assertEqual(bars[4], {"start": DT(2026, 10, 29, 9, 50), "open": 99.9, "high": 99.9, "low": 99.8,
                                   "close": 99.9, "volume": 9000, "n": 1})
        self.assertEqual([i for i, b in enumerate(bars) if b], [0, 2, 4])

    def test_bars_from_tape_order_and_symbol(self):
        want = core.bars_from_tape(COMPANY_TAPE, THU, "DEMO")
        self.assertEqual(core.bars_from_tape(list(reversed(COMPANY_TAPE)), THU, "demo"), want)
        self.assertEqual(core.bars_from_tape(COMPANY_TAPE[:-1], THU), want)
        self.assertEqual(core.bars_from_tape([], THU, "DEMO"), [None] * 72)
        self.assertEqual(core.bars_from_tape(COMPANY_TAPE, D(2026, 11, 9), "DEMO"), [])

    def test_a_late_tape_does_not_guess(self):
        late = [csnap("2026-10-29 10:12", 100.6, 100.2, 101.0, 99.0, 50000),
                csnap("2026-10-29 10:16", 100.8, 100.2, 101.0, 99.0, 52000)]
        bars = core.bars_from_tape(late, THU, "DEMO")
        self.assertEqual(bars[8], {"start": DT(2026, 10, 29, 10, 10), "open": 100.6, "high": 100.6, "low": 100.6,
                                   "close": 100.6, "volume": None, "n": 1})
        self.assertEqual((bars[9]["volume"], bars[9]["high"]), (2000, 100.8))

    def test_bars_from_a_screener_tape(self):
        tape = [{"asof": "2026-10-29 09:31:00", "source": "screener", "prices": {"DEMO": 100.4, "OGDC": 306.0}},
                {"asof": "2026-10-29 09:33:30", "source": "screener", "prices": {"DEMO": 100.6}},
                {"asof": "2026-10-29 09:36:00", "source": "screener", "prices": {"DEMO": 100.5, "OGDC": 306.5}},
                {"asof": "2026-10-29 09:29:00", "source": "screener", "prices": {"DEMO": 100.0}},
                {"asof": "bad", "prices": {"DEMO": 1.0}}]
        bars = core.bars_from_tape(tape, THU, "DEMO")
        self.assertEqual(bars[0], {"start": DT(2026, 10, 29, 9, 30), "open": 100.4, "high": 100.6, "low": 100.4,
                                   "close": 100.6, "volume": None, "n": 2})
        self.assertEqual(bars[1], {"start": DT(2026, 10, 29, 9, 35), "open": 100.5, "high": 100.5, "low": 100.5,
                                   "close": 100.5, "volume": None, "n": 1})
        self.assertEqual(core.bars_from_tape(tape, THU), [None] * 72)
        self.assertEqual(core.bars_from_tape(tape, THU, "NOPE"), [None] * 72)
        self.assertEqual(core.bars_from_tape(tape, THU, "OGDC")[1]["close"], 306.5)
        mixed = tape[:1] + [csnap("2026-10-29 09:34", 100.3, 100.2, 100.7, 100.1, 12000)]
        bar = core.bars_from_tape(mixed, THU, "DEMO")[0]
        self.assertEqual((bar["open"], bar["high"], bar["close"], bar["volume"], bar["n"]),
                         (100.2, 100.7, 100.3, 12000, 2))     # the company snapshot adds the day open, high, volume
        odd = [{"asof": "2026-10-29 09:31:00", "source": "screener", "price": 5.0, "prices": {}}]
        self.assertEqual(core.bars_from_tape(odd, THU, "DEMO"), [None] * 72)

    def test_bars_from_a_friday_tape(self):
        tape = [csnap("2026-10-30 11:58", 101.0, 100.0, 101.5, 99.5, 50000),
                csnap("2026-10-30 12:00", 101.1, 100.0, 101.5, 99.5, 51000),   # the break: no bar, its volume counts
                csnap("2026-10-30 14:31", 101.4, 100.0, 101.6, 99.5, 52500)]
        bars = core.bars_from_tape(tape, FRI, "DEMO")
        self.assertEqual(len(bars), 57)
        self.assertEqual((bars[32]["close"], bars[32]["volume"]), (101.0, None))
        self.assertEqual(bars[33], {"start": DT(2026, 10, 30, 14, 30), "open": 101.4, "high": 101.6, "low": 101.4,
                                    "close": 101.4, "volume": 1500, "n": 1})

    def test_a_snapshot_on_a_slot_boundary(self):
        tape = [csnap("2026-10-29 09:34:59", 100.4, 100.2, 100.5, 100.1, 1000),
                csnap("2026-10-29 09:35", 100.6, 100.2, 100.6, 100.1, 1500),
                csnap("2026-10-29 09:40:00", 100.7, 100.2, 100.7, 100.1, 1700)]
        bars = core.bars_from_tape(tape, THU)
        self.assertEqual([(b["close"], b["volume"]) for b in bars[:3]], [(100.4, 1000), (100.6, 500), (100.7, 200)])
        friday = core.bars_from_tape([csnap("2026-10-30 14:30", 101.0, 100.0, 101.0, 99.5, 100)], FRI)
        self.assertEqual([i for i, b in enumerate(friday) if b], [33])

    def test_a_day_high_that_rises_between_snapshots(self):
        tape = [csnap("2026-10-29 09:33", 100.4, 100.2, 100.5, 100.1, 1000),
                csnap("2026-10-29 09:41", 100.6, 100.2, 100.9, 100.1, 2000),    # the high rose since 09:33
                csnap("2026-10-29 09:43", 100.5, 100.2, 101.2, 100.1, 2500),    # and again in the same bar
                csnap("2026-10-29 09:47", 100.3, 100.2, 101.2, 99.7, 3000)]     # now the low fell
        bars = core.bars_from_tape(tape, THU)
        self.assertEqual((bars[2]["high"], bars[2]["low"], bars[2]["close"]), (101.2, 100.5, 100.5))
        self.assertEqual((bars[3]["high"], bars[3]["low"]), (100.3, 99.7))

    def test_a_pre_open_snapshot_does_not_make_a_late_tape_guess(self):
        # a pre-open snapshot (no trade yet) used to let the first later bar take the whole day's range
        late = [csnap("2026-10-29 10:12", 100.6, 100.2, 101.0, 99.0, 50000)]
        for tape in (late, [csnap("2026-10-29 09:25", 100.0, 0, 0, 0, 0)] + late):
            b = core.bars_from_tape(tape, THU)[8]
            self.assertEqual((b["high"], b["low"]), (100.6, 100.6))
        early = [csnap("2026-10-29 09:25", 100.0, 0, 0, 0, 0), csnap("2026-10-29 09:31", 100.4, 100.2, 100.5, 100.1, 900)]
        b = core.bars_from_tape(early, THU)[0]
        self.assertEqual((b["open"], b["high"], b["low"], b["volume"]), (100.2, 100.5, 100.1, 900))

    def test_a_volume_dip_never_counts_twice(self):
        # a page that showed 0 (or an older number) once used to add that volume again in the next bar
        for dip in (0, 9000, None):
            tape = [csnap("2026-10-29 09:33", 100.4, 100.2, 100.5, 100.1, 10000),
                    csnap("2026-10-29 09:41", 100.6, 100.2, 100.7, 100.1, dip),
                    csnap("2026-10-29 09:52", 100.6, 100.2, 100.7, 100.1, 30000)]
            bars = core.bars_from_tape(tape, THU)
            self.assertEqual([bars[k]["volume"] for k in (0, 2, 4)], [10000, None if dip is None else 0, 20000], dip)

    def test_repeats_count_once(self):
        # a quiet page polled again keeps its asof: each repeat used to add 1 to n
        one = csnap("2026-10-29 09:33", 100.4, 100.2, 100.5, 100.1, 1000)
        many = [dict(one, fetched="2026-10-29T09:%02d:00+05:00" % m) for m in (34, 35, 36)]
        self.assertEqual(core.bars_from_tape(many, THU), core.bars_from_tape([one], THU))
        self.assertEqual(core.bars_from_tape(many, THU)[0]["n"], 1)
        moved = many + [csnap("2026-10-29 09:33", 100.45, 100.2, 100.5, 100.1, 1100)]   # same minute, new numbers
        self.assertEqual(core.bars_from_tape(moved, THU)[0]["n"], 2)
        screener = [{"asof": "2026-10-29 09:31:00", "source": "screener", "prices": {"DEMO": 100.4}}] * 2
        self.assertEqual(core.bars_from_tape(screener, THU, "DEMO")[0]["n"], 1)

    def test_two_symbols_need_a_symbol(self):
        # with no symbol, OGDC and HBL snapshots used to mix into the same bars without a word
        tape = [csnap("2026-10-29 09:33", 306.0, 305.0, 306.5, 304.0, 1000, "OGDC"),
                csnap("2026-10-29 09:34", 288.0, 288.0, 289.0, 287.0, 500, "HBL")]
        with self.assertRaises(ValueError) as cm:
            core.bars_from_tape(tape, THU)
        self.assertIn("HBL, OGDC", str(cm.exception))
        self.assertEqual((core.bars_from_tape(tape, THU, "hbl")[0]["low"], core.bars_from_tape(tape, THU, "OGDC")[0]["low"]),
                         (287.0, 304.0))
        self.assertEqual(core.bars_from_tape(tape, D(2026, 10, 28)), [None] * 72)     # none of them is that day

    def test_utc_times_work_on_every_python(self):
        # "...Z" was dropped on Python 3.8 to 3.10 (fromisoformat cannot read it) but used on 3.11 and later
        tape = [{"asof": "2026-10-29T04:33:00Z", "source": "screener", "prices": {"DEMO": 100.4}},
                {"asof": "2026-10-29T04:36:00z", "source": "screener", "prices": {"DEMO": 100.5}},
                csnap("2026-10-29T09:38:00+05:00", 100.6, 100.2, 100.7, 100.1, 9000)]
        bars = core.bars_from_tape(tape, THU, "DEMO")
        self.assertEqual((bars[0]["close"], bars[1]["open"], bars[1]["close"]), (100.4, 100.5, 100.6))
        self.assertEqual(core.last_asof(tape), DT(2026, 10, 29, 9, 38))
        self.assertEqual(core.last_asof([{"asof": "2026-10-29T10:45:00Z"}]), DT(2026, 10, 29, 15, 45))

    def test_last_asof(self):
        self.assertEqual(core.last_asof(COMPANY_TAPE), DT(2026, 10, 29, 15, 49))
        self.assertEqual(core.last_asof([{"asof": "2026-10-29 09:31:00"}, {"asof": None}, "junk", {}]),
                         DT(2026, 10, 29, 9, 31))
        self.assertIsNone(core.last_asof([]))
        self.assertIsNone(core.last_asof(None))


class TestBarsFile(unittest.TestCase):
    CSV = ("Datetime,Open,High,Low,Close,Volume\n"
           "2026-10-29 09:29,99.0,99.5,98.9,99.2,100\n"
           "2026-10-29 09:30,100.0,100.5,99.9,100.2,1000\n"
           "2026-10-29 09:31,100.2,100.6,100.1,100.5,800\n"
           "2026-10-29 09:32:00,100.5,100.9,100.4,100.8,600\n"
           "2026-10-29 09:33,100.8,100.8,100.3,100.4,400\n"
           "2026-10-29 09:34,100.4,100.5,100.2,100.3,200\n"
           "2026-10-29 09:36,100.3,100.4,100.0,100.1,300\n"
           "2026-10-29 15:30,101,101,101,101,5000\n"
           "2026-10-30 09:15,101.0,101.2,100.9,101.1,\"1,700\"\n"
           "not a time,1,1,1,1,1\n"
           "2026-10-29 09:37,bad,1,1,1,1\n"
           "2026-10-29 09:38,2,1,3,2,1\n")

    def test_read_bars_csv(self):
        got = core.read_bars_csv(self.CSV)
        self.assertEqual(sorted(got), [THU, FRI])
        self.assertEqual(len(got[THU]), 8)
        self.assertEqual(got[THU][1], {"start": DT(2026, 10, 29, 9, 30), "open": 100.0, "high": 100.5, "low": 99.9,
                                       "close": 100.2, "volume": 1000})
        self.assertEqual(got[FRI][0]["volume"], 1700)

    def test_other_layouts(self):
        a = core.read_bars_csv("date;time;open;high;low;close\n29/10/2026;09:30;100;101;99;100.5\n"
                               "29/10/2026;9:35;100.5;100.7;100.1;100.2\n")
        self.assertEqual([b["start"] for b in a[THU]], [DT(2026, 10, 29, 9, 30), DT(2026, 10, 29, 9, 35)])
        self.assertIsNone(a[THU][0]["volume"])
        b = core.read_bars_csv("TIMESTAMP\tOPEN\tHIGH\tLOW\tCLOSE\tVOLUME\n2026-10-29T09:30:00\t1\t2\t0.5\t1.5\t10\n")
        self.assertEqual(b[THU][0]["start"], DT(2026, 10, 29, 9, 30))
        ts = calendar.timegm((2026, 10, 29, 4, 30, 0))
        c = core.read_bars_csv("timestamp,open,high,low,close\n%d,1,2,0.5,1.5\n%d000,1,2,0.5,1.5\n" % (ts, ts + 300))
        self.assertEqual([x["start"] for x in c[THU]], [DT(2026, 10, 29, 9, 30), DT(2026, 10, 29, 9, 35)])
        d = core.read_bars_csv("Time,Open,High,Low,Close\n2026-10-29T04:30:00Z,1,2,0.5,1.5\n"
                               "2026-10-29 09:35+05:00,1,2,0.5,1.5\n2026-10-29 01:05 PM,1,2,0.5,1.5\n")
        self.assertEqual([x["start"] for x in d[THU]], [DT(2026, 10, 29, 9, 30), DT(2026, 10, 29, 9, 35),
                                                        DT(2026, 10, 29, 13, 5)])

    def test_bad_files(self):
        for text in ("", "\n\n", "Datetime,Open,Close\n2026-10-29 09:30,1,1\n",
                     "Datetime,Open,High,Low,Close\n10/29/2026 09:30,1,2,0.5,1.5\n"):
            with self.assertRaises(ValueError):
                core.read_bars_csv(text)

    def test_bom_crlf_extra_columns_and_no_volume(self):
        text = ("﻿Symbol,Date,Time,Open,High,Low,Close,OpenInt\r\n"
                "OGDC,29/10/2026,09:30,100,101,99,100.5,0\r\nOGDC,29/10/2026,09:35,100.5,101,100,100.7,0\r\n")
        got = core.read_bars_csv(text)
        self.assertEqual([(b["start"], b["close"], b["volume"]) for b in got[THU]],
                         [(DT(2026, 10, 29, 9, 30), 100.5, None), (DT(2026, 10, 29, 9, 35), 100.7, None)])

    def test_a_month_first_file_is_refused(self):
        # 10/05/2026 used to be read as 10 May without a word, while 10/29/2026 was dropped
        us = "Date,Time,Open,High,Low,Close\n10/05/2026,09:30,100,101,99,100.5\n10/29/2026,09:30,100,101,99,100.5\n"
        with self.assertRaises(ValueError) as cm:
            core.read_bars_csv(us)
        self.assertIn("month first", str(cm.exception))
        uk = "Date,Time,Open,High,Low,Close\n05/10/2026,09:30,100,101,99,100.5\n12/12/2026,09:30,1,1,1,1\n"
        self.assertEqual(sorted(core.read_bars_csv(uk)), [D(2026, 10, 5), D(2026, 12, 12)])

    def test_resample_1_minute_to_5(self):
        bars = core.read_bars_csv(self.CSV)[THU]
        five = core.resample_bars(bars, THU)
        self.assertEqual(len(five), 72)
        self.assertEqual(five[0], {"start": DT(2026, 10, 29, 9, 30), "open": 100.0, "high": 100.9, "low": 99.9,
                                   "close": 100.3, "volume": 3000, "n": 5})
        self.assertEqual(five[1], {"start": DT(2026, 10, 29, 9, 35), "open": 100.3, "high": 100.4, "low": 100.0,
                                   "close": 100.1, "volume": 300, "n": 1})
        self.assertEqual([i for i, b in enumerate(five) if b], [0, 1])       # 09:29 and 15:30 prints dropped

    def test_resample_passes_5_minute_bars_and_handles_friday(self):
        bars = [{"start": DT(2026, 10, 30, 9, 15), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": None},
                {"start": DT(2026, 10, 30, 12, 5), "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 9},
                {"start": DT(2026, 10, 30, 14, 30), "open": 1, "high": 3, "low": 0.5, "close": 2.5, "volume": 7},
                {"start": DT(2026, 10, 29, 9, 30), "open": 1, "high": 3, "low": 0.5, "close": 2.5, "volume": 7}]
        five = core.resample_bars(bars, FRI)
        self.assertEqual(len(five), 57)
        self.assertEqual((five[0]["n"], five[0]["volume"]), (1, None))
        self.assertEqual(five[33], {"start": DT(2026, 10, 30, 14, 30), "open": 1, "high": 3, "low": 0.5,
                                    "close": 2.5, "volume": 7, "n": 1})
        self.assertEqual(sum(1 for b in five if b), 2)
        self.assertEqual(len(core.resample_bars(bars, FRI, 15)), 19)
        self.assertEqual(core.resample_bars([], FRI)[0], None)


# ---------------------------------------------------------------- the real PSX files (skip when absent)

@unittest.skipUnless(HAVE_FIX, "real PSX fixtures not here")
class TestRealFiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = os.path.join(FIX, "mkt_summary")
        cls.days = {}
        for f in sorted(os.listdir(folder)):
            if f.endswith(".Z"):
                cls.days[f[:10]] = core.read_mkt_summary(read_bytes(os.path.join(folder, f)))
        cls.names = core.read_symbol_names(read_bytes(os.path.join(FIX, "symbol_name", "2026-10-29.zip")))

    def test_70_daily_files(self):
        self.assertEqual(len(self.days), 70)
        for name, rows in self.days.items():
            self.assertTrue(all(r["date"].isoformat() == name for r in rows), name)
            self.assertGreater(len(rows), 900, name)
            self.assertTrue(all(isinstance(r["volume"], int) for r in rows))
        og = [r for r in self.days["2026-10-29"] if r["symbol"] == "OGDC"][0]
        self.assertEqual(og, {"date": THU, "symbol": "OGDC", "sector": "0820", "short_name": "Oil & Gas DevXD",
                              "open": 206.0, "high": 207.99, "low": 203.0, "close": 206.21, "volume": 1357900,
                              "ldcp": 203.68, "flags": ["XD"]})
        self.assertEqual(len(self.days["2026-10-29"]), 1049)

    def test_recent_sessions_on_the_70_real_files(self):
        want = sorted(D.fromisoformat(k) for k in self.days)
        root = tempfile.mkdtemp(prefix="psxcore-")
        try:
            os.makedirs(os.path.join(root, "mkt_summary"))
            for k in self.days:
                shutil.copy(os.path.join(FIX, "mkt_summary", k + ".Z"), os.path.join(root, "mkt_summary"))
            self.assertEqual([d for d, _ in core.recent_sessions(THU, 70, root, offline=True)], want)
            self.assertEqual([d for d, _ in core.recent_sessions(FRI, 200, root, offline=True)], want)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        cal, d = [], want[0]
        while d <= want[-1]:
            if core.trading_day(d)[0]:
                cal.append(d)
            d += datetime.timedelta(days=1)
        self.assertEqual((want[0], want[-1], len(want)), (D(2026, 7, 22), THU, 70))
        self.assertEqual(cal, want)     # no gap the calendar did not expect, and no file on a closed day

    def test_diil_lower_limit(self):
        row = [r for r in self.days["2026-07-22"] if r["symbol"] == "DIIL"][0]
        self.assertEqual((row["ldcp"], row["low"]), (60.15, 54.14))
        self.assertEqual(core.price_limits(row["ldcp"])[0], row["low"])     # x 0.90 half up, not 60.15 - 6.02

    def test_flags_and_ex_date_ldcp(self):
        flags = {}
        for rows in self.days.values():
            for r in rows:
                if r["flags"] and r["sector"] not in ("40", "41"):       # futures short names carry XD too
                    k = "".join(r["flags"])
                    flags[k] = flags.get(k, 0) + 1
        self.assertEqual(flags, {"XD": 1862, "XR": 49, "XB": 27, "XDXB": 6, "XBXD": 2})   # research-data.md
        a = [r for r in self.days["2026-10-07"] if r["symbol"] == "OGDC"][0]
        b = [r for r in self.days["2026-10-08"] if r["symbol"] == "OGDC"][0]
        self.assertEqual((a["close"], b["ldcp"], b["flags"]), (315.91, 309.91, ["XD"]))

    def test_old_files(self):
        folder = os.path.join(FIX, "mkt_summary_old")
        got = {}
        for f in sorted(os.listdir(folder)):
            rows = core.read_mkt_summary(read_bytes(os.path.join(folder, f)))
            self.assertTrue(all(r["date"].isoformat() == f[:10] for r in rows), f)
            got[f[:10]] = rows
        self.assertEqual(len(got), 8)
        self.assertEqual(len(got["2015-01-02"]), 534)
        npsm = [r for r in got["2015-01-02"] if r["symbol"] == "NPSM"][0]
        self.assertEqual((npsm["open"], npsm["ldcp"], npsm["sector"]), (None, 38.8278, "3760"))
        self.assertEqual([r["sector"] for r in got["2015-01-02"] if r["symbol"] == "GASF"], ["8980"])

    @unittest.skipUnless(os.path.exists(PROBE404), "no saved 404 page")
    def test_real_404_page_means_no_session(self):
        root = tempfile.mkdtemp(prefix="psxcore-")
        try:
            os.makedirs(os.path.join(root, "mkt_summary"))
            shutil.copy(PROBE404, os.path.join(root, "mkt_summary", "2026-10-24.Z"))
            self.assertIsNone(core.daily_rows(D(2026, 10, 24), root))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_symbol_names(self):
        self.assertEqual(len(self.names), 1106)
        self.assertEqual(self.names["786"], ("786 Invest Ltd", "786 Investments Limited"))
        self.assertEqual(sum(1 for s in self.names if "-" in s), 535)

    def test_kinds_on_2026_10_29(self):
        rows = self.days["2026-10-29"]
        want = {"ordinary": 446, "stock_future": 524, "modaraba": 20, "debt": 19, "etf": 9, "index_future": 9,
                "reit": 7, "preference": 6, "right": 3, "closed_end_fund": 3, "gem_equity": 2, "non_voting": 1}
        for name_of in (lambda r: self.names.get(r["symbol"], ("", ""))[1], lambda r: r["short_name"]):
            got = {}
            for r in rows:
                k = core.symbol_kind(r["symbol"], r["sector"], name_of(r))
                got[k] = got.get(k, 0) + 1
            self.assertEqual(got, want)
        rights = sorted(r["symbol"] for r in rows if core.symbol_kind(r["symbol"], r["sector"], r["short_name"])
                        == "right")
        self.assertEqual(rights, ["BAPLR1", "CENIR", "PINLR"])

    def test_price_limits_on_every_priced_row(self):
        shares = etf_miss = debt_miss = futures = 0
        for rows in self.days.values():
            for r in rows:
                if not (r["open"] and r["high"] and r["low"] and r["ldcp"]):
                    continue
                lo, up = core.price_limits(r["ldcp"])
                inside = r["low"] >= lo - 1e-9 and r["high"] <= up + 1e-9
                if r["sector"] in ("40", "41"):
                    futures += 1
                    self.assertTrue(inside, r)
                elif r["sector"] == "0837":
                    etf_miss += not inside
                    self.assertTrue(inside or r["symbol"] == "MIIETF", r)
                elif r["sector"] == "36":
                    debt_miss += not inside
                else:
                    shares += 1
                    self.assertTrue(inside, (r["date"], r["symbol"], r["ldcp"], r["low"], r["high"], lo, up))
        self.assertEqual((shares, futures), (33867, 8958))
        self.assertEqual((etf_miss, debt_miss), (3, 3))     # ETFs work from NAV (MIIETF); debt is not a share

    def test_circuit_breakers_page(self):
        page = read_text(os.path.join(FIX, "pages", "circuit-breakers.html"))
        seen = 0
        for name, body in re.findall(r'<table class="tbl" id="(upperCap|lowerCap)"[^>]*>(.*?)</table>', page, re.S):
            for sym, ldcp, high, low, cur in re.findall(
                    r'<tr><td data-search="([^"]+)".*?</td><td class="right" data-order="([^"]*)">[^<]*</td>'
                    r'<td class="right" data-order="[^"]*">[^<]*</td><td class="right" data-order="([^"]*)">[^<]*</td>'
                    r'<td class="right" data-order="([^"]*)">[^<]*</td><td class="right" data-order="([^"]*)">', body):
                lo, up = core.price_limits(ldcp)
                if name == "upperCap":
                    self.assertEqual((float(high), float(cur)), (up, up), sym)
                else:
                    self.assertEqual((float(low), float(cur)), (lo, lo), sym)
                seen += 1
        self.assertEqual(seen, 22)

    def company_pages(self):
        out = []
        for folder in (os.path.join(FIX, "company"), os.path.join(FIX, "insiders", "company-pages")):
            if os.path.isdir(folder):
                out.extend(os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.endswith(".html"))
        return out

    def test_every_company_page(self):
        pages = self.company_pages()
        self.assertGreaterEqual(len(pages), 51)
        file_rows = dict((r["symbol"], r) for r in self.days["2026-10-29"])
        for path in pages:
            r = core.parse_company(read_text(path))
            name = os.path.basename(path)
            self.assertEqual(r["symbol"], name[:-5], name)
            self.assertIsNotNone(r["price"], name)
            self.assertIsNotNone(r["as_of"], name)
            reg = r["reg"]
            self.assertEqual(core.price_limits(reg["ldcp"]), (reg["lower"], reg["upper"]), name)
            row = file_rows.get(r["symbol"])
            if row and r["as_of"].date() == THU:
                self.assertEqual((reg["open"], reg["high"], reg["low"], reg["volume"], reg["ldcp"], r["price"]),
                                 (row["open"], row["high"], row["low"], row["volume"], row["ldcp"], row["close"]), name)

    def test_ogdc_exact(self):
        r = core.parse_company(read_text(os.path.join(FIX, "company", "OGDC.html")))
        self.assertEqual((r["price"], r["reg"]["lower"], r["reg"]["upper"], r["reg"]["ldcp"]),
                         (206.21, 183.31, 224.05, 203.68))
        self.assertEqual(r["as_of"], DT(2026, 10, 29, 15, 49))
        self.assertEqual(r["financials"]["annual"]["columns"][0], "2026")
        self.assertEqual(r["financials"]["annual"]["rows"]["Sales"][0], 449190752)
        q = r["financials"]["quarterly"]
        self.assertEqual(q["rows"]["EPS"][q["columns"].index("Q3 2026")], 9.82)
        self.assertEqual((r["flags"], r["sector"], r["profile"]["fiscal_year_end"]),
                         (["XD"], "OIL & GAS EXPLORATION COMPANIES", "June"))
        self.assertEqual(r["equity"]["shares"], 4300928400)
        self.assertEqual(r["announcements"]["Others"][2]["doc_id"], "282420")
        snap = core.snapshot_from_company(r, DT(2026, 10, 29, 10, 50))
        self.assertEqual((snap["asof"], snap["price"], snap["volume"], snap["lower"]),
                         ("2026-10-29 15:49", 206.21, 1357900, 183.31))

    def test_sparse_company_pages(self):
        b = core.parse_company(read_text(os.path.join(FIX, "company", "BAPLR1.html")))
        self.assertEqual((b["price"], b["reg"]["lower"], b["reg"]["upper"]), (5.62, 5.62, 7.62))
        self.assertEqual((b["profile"]["fiscal_year_end"], b["announcements"]["Others"], b["ratios"]["rows"]),
                         (None, [], {}))
        a = core.parse_company(read_text(os.path.join(FIX, "company", "AASM.html")))
        self.assertEqual((a["flags"], a["as_of"]), (["SUSPENDED", "WINDING-UP"], DT(2018, 2, 16, 11, 10)))
        j = core.parse_company(read_text(os.path.join(FIX, "company", "JSGCL.html")))
        self.assertEqual((j["as_of"], j["reg"]["ldcp"]), (DT(2026, 10, 28, 15, 36), 133.56))
        self.assertEqual(core.parse_company(read_text(os.path.join(FIX, "company", "ARUJ.html")))["flags"],
                         ["NON-COMPLIANT"])

    def test_announcement_links(self):
        kinds = {}
        for f in sorted(os.listdir(os.path.join(FIX, "company"))):
            r = core.parse_company(read_text(os.path.join(FIX, "company", f)))
            for rows in r["announcements"].values():
                for a in rows:
                    self.assertTrue(a["doc_id"] and re.match(r"^\d{4}-\d{2}-\d{2}$", a["date"]), a)
                    k = (bool(a["pdf"]), bool(a["image"]), bool(a["attachment"]))
                    kinds[k] = kinds.get(k, 0) + 1
        self.assertEqual(kinds, {(True, True, False): 275, (False, True, False): 22, (False, False, True): 3})

    def test_announcement_links_on_every_page(self):
        kinds = set()
        for path in self.company_pages():
            for rows in core.parse_company(read_text(path))["announcements"].values():
                for a in rows:
                    self.assertTrue(a["doc_id"] and (a["pdf"] or a["image"] or a["attachment"]), (path, a))
                    kinds.add((bool(a["pdf"]), bool(a["image"]), bool(a["attachment"])))
        self.assertEqual(kinds, {(True, True, False), (False, True, False), (False, False, True)})
        e = core.parse_company(read_text(os.path.join(FIX, "company", "EFERT.html")))["announcements"]
        gif = [a for a in e["Board Meetings"] if not a["pdf"]][0]                   # a GIF only
        self.assertEqual((gif["doc_id"], gif["image"]), ("286848", core.PSX + "/download/image/286848-1.gif"))
        m = core.parse_company(read_text(os.path.join(FIX, "company", "MCB.html")))["announcements"]
        att = [a for a in m["Financial Results"] if a["attachment"]][0]              # an attachment only
        self.assertEqual((att["doc_id"], att["pdf"], att["image"]), ("287767", None, None))

    def test_banks_holdings_and_thin_pages(self):
        e = core.parse_company(read_text(os.path.join(FIX, "company", "ENGROH.html")))["financials"]
        self.assertEqual(e["annual"]["rows"]["Sales"], [None, None, None, None])      # a holding: no sales
        self.assertEqual((e["annual"]["rows"]["Total Income"][0], e["quarterly"]["rows"]["EPS"][2]), (548958, -0.09))
        for sym in ("HBL", "MCB", "MEBL", "UBL"):
            rows = core.parse_company(read_text(os.path.join(FIX, "company", sym + ".html")))["financials"]["annual"]
            self.assertNotIn("Sales", rows["rows"], sym)
            self.assertEqual(list(rows["rows"])[:2], ["Mark-up Earned", "Total Income"], sym)
        r = core.parse_company(read_text(os.path.join(FIX, "company", "REDCO.html")))
        # 1 share traded at 27.50: too little for a new official close, so the price stays the LDCP
        self.assertEqual((r["price"], r["reg"]["ldcp"], r["reg"]["high"], r["reg"]["volume"]), (26.39, 26.39, 27.5, 1))
        b = core.parse_company(read_text(os.path.join(FIX, "company", "BAPLR1.html")))
        self.assertEqual(core.symbol_kind("BAPLR1", "0805", b["name"]), "right")

    def test_real_screener(self):
        s = core.parse_screener(read_text(os.path.join(FIX, "pages", "screener.html")))
        self.assertEqual(len(s["rows"]), 754)
        self.assertEqual(s["clock"], "Oct 30, 2026 1:18 AM")
        flags = {}
        for r in s["rows"].values():
            for f in r["flags"]:
                flags[f] = flags.get(f, 0) + 1
        self.assertEqual(flags, {"NC": 93, "XD": 90, "XR": 2})
        self.assertEqual(sum(1 for r in s["rows"].values() if not r["sector"]), 53)
        self.assertEqual((s["rows"]["JSGCL"]["price"], s["rows"]["JSGCL"]["change_pct"]), (133.56, -2.689))
        self.assertEqual(s["rows"]["AAL"]["flags"], ["NC"])
        self.assertIn("KSE100", s["rows"]["OGDC"]["listed_in"])
        closes = dict((r["symbol"], r["close"]) for r in self.days["2026-10-29"])
        both = [k for k in s["rows"] if k in closes]
        self.assertGreater(len(both), 450)
        for k in both:
            self.assertEqual(s["rows"][k]["price"], closes[k], k)
        snap = core.snapshot_from_screener(s, DT(2026, 10, 29, 10, 0))
        self.assertEqual(len(snap["prices"]), sum(1 for r in s["rows"].values() if r["price"]))
        self.assertNotIn("AAL", snap["prices"])

    def test_real_indices(self):
        ix = core.parse_indices(read_text(os.path.join(FIX, "pages", "indices.html")))
        self.assertEqual(ix["as_of"], DT(2026, 10, 29, 15, 50))
        self.assertEqual(len(ix["rows"]), 18)
        self.assertEqual(ix["rows"]["KSE100"]["current"], 167193.43)
        self.assertEqual((ix["rows"]["HBLTTI"]["high"], ix["rows"]["HBLTTI"]["current"]), (None, 19290.31))


if __name__ == "__main__":
    unittest.main()
