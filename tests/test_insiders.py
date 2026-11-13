"""Tests for psx-insiders (insiders.py).

They read the made-up sample market (fixtures/psx-market) and the made-up files in
fixtures/psx-insiders. They write only to temp folders and never touch the network:
urlopen is swapped for a guard, and SLEEP for a no-op.
"""

import datetime
import io
import json
import os
import re
import shutil
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

ins = load("psx-insiders", "insiders")
MARKET = os.path.join(FIX, "psx-market")
MINE = os.path.join(FIX, "psx-insiders")
DATA = os.path.join(MINE, "data")
NOW = "2026-10-29 16:00"        # Thursday, after the close (Karachi time)
PSX = "https://dps.psx.com.pk"
_REAL_URLOPEN = urllib.request.urlopen

# EXAMPLE.md section 12, as the tool prints it (`new --read --symbols DEMO,SYS`, first run).
# SKILL.md shows the same lines; a test checks that they match.
EXAMPLE = """First run: notices posted since 2026-09-29 (the last 30 days). Older ones are marked as seen.

NEW insider notice: DEMO (Demo Industries Limited), doc 290210, posted 2026-10-29.
  PDF https://dps.psx.com.pk/download/document/290210.pdf
  GIF https://dps.psx.com.pk/download/image/290210-1.gif
Read from psx/data/docs/290210.txt: 2 trades.
  - Mr. Salman Akhtar (Director) bought 25,000 shares at 97.11 on 2026-10-28.
    Worth Rs 2,427,750.00. Ready market. Holds 1,265,000 shares after (0.51%).
    Posted 1 day after the trade.
    This trade date is inside the closed period DEMO announced (October 27 to November 3, 2026).
    PSX Rule Book 5.6.4 says directors, the CEO and executives may not deal in a closed period.
    Check the notice.
  - Mrs. Hina Salman (Spouse of Director) bought 10,000 shares at 97.26 on 2026-10-28.
    Worth Rs 972,600.00. Ready market. Holds 410,000 shares after (0.16%).
    Posted 1 day after the trade.
    This trade date is inside the closed period DEMO announced (October 27 to November 3, 2026).
    PSX Rule Book 5.6.4 says directors, the CEO and executives may not deal in a closed period.
    Check the notice.

NEW insider notice: SYS (Systems Limited), doc 290070, posted 2026-10-27.
  PDF https://dps.psx.com.pk/download/document/290070.pdf
  GIF https://dps.psx.com.pk/download/image/290070-1.gif
Read from psx/data/docs/290070.txt: 3 trades.
  - Mr. Danish Iqbal (Senior Management) sold 150,000 shares at 120.60 on 2026-10-20.
    Worth Rs 18,090,000.00. Ready market.
    Posted 7 days after the trade.
  - Mr. Danish Iqbal (Senior Management) sold 85,500 shares at 122.68 on 2026-10-21.
    Worth Rs 10,489,140.00. Ready market.
    Posted 6 days after the trade.
  - Ms. Mehwish Tariq (Executive) bought 5,000 shares at 124.16 on 2026-10-22.
    Worth Rs 620,800.00. Ready market.
    Posted 5 days after the trade.
  Closed period: not checked (no SYS board meeting notice text in docs/).

NEW insider notice: DEMO (Demo Industries Limited), doc 289612, posted 2026-10-23.
  GIF https://dps.psx.com.pk/download/image/289612-1.gif
Image only, no text to read. Ask the user to open https://dps.psx.com.pk/download/image/289612-1.gif in their \
browser and save it as psx/data/docs/289612.gif, or paste a screenshot. Do not fetch it yourself. Do not guess its rows.

Fast, not early: the insider has 7 days to tell the company. The notice on PSX can come later.
These are public PSX notices. There is no inside information here.
A notice is a reason to look at a chart, never a setup.
"""
CLOSED_DEMO = ("This trade date is inside the closed period DEMO announced (October 27 to November 3, 2026). "
               "PSX Rule Book 5.6.4 says directors, the CEO and executives may not deal in a closed period. "
               "Check the notice.")


def _guard(*a, **k):
    raise AssertionError("a test tried to use the network")


def opens_psx_itself(out):
    """Lines that tell Claude to open a PSX link itself. With the site off there must be none:
    only "Ask the user to open <link> in their browser ..." is fine (SPEC D17)."""
    return [x for x in out.splitlines() if re.search(r"(?<!Ask the user to )open https://dps\.psx\.com\.pk", x)]


def setUpModule():
    urllib.request.urlopen = _guard
    ins.SLEEP = lambda s: None


def tearDownModule():
    urllib.request.urlopen = _REAL_URLOPEN
    ins.SLEEP = time.sleep


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def flat(s):
    """Output with its line breaks made spaces, to check whole sentences."""
    return " ".join(s.split())


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ins.main(args)
    return code, out.getvalue(), err.getvalue()


class FakeResp(object):
    def __init__(self, body):
        self.body, self.headers = body, {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeNet(object):
    """Stands in for urlopen: {url: bytes or an HTTP code}. Records the calls and the pauses."""

    def __init__(self, answers=None):
        self.answers, self.calls, self.sleeps = answers or {}, [], []

    def __enter__(self):
        self.old = (urllib.request.urlopen, ins.SLEEP, getattr(ins.http_get, "last", None))
        urllib.request.urlopen, ins.SLEEP = self.urlopen, self.sleeps.append
        ins.http_get.last = None
        return self

    def __exit__(self, *a):
        urllib.request.urlopen, ins.SLEEP, ins.http_get.last = self.old
        return False

    def urlopen(self, req, timeout=None):
        url = req.full_url
        self.calls.append((url, dict(req.header_items())))
        a = self.answers.get(url, 404)
        if isinstance(a, int):
            raise urllib.error.HTTPError(url, a, "HTTP %d" % a, {}, io.BytesIO(b"<html>not found</html>"))
        return FakeResp(a)


class Temp(unittest.TestCase):
    """A temp folder for the user's psx/ files; nothing exists there until a test writes it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psx-insiders-")
        self.state = self.p("state", "insiders.json")
        self.old_pdf = ins.pdf_to_text

    def tearDown(self):
        ins.pdf_to_text = self.old_pdf
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)

    def put(self, rel, data):
        path = self.p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(path, mode) as fh:
            fh.write(data if isinstance(data, (bytes, str)) else json.dumps(data))
        return path

    def files(self, many=True):
        """The user's files, all in the temp folder (the real psx/ folder is never touched)."""
        out = ["--config", self.p("config.json"), "--portfolio", self.p("portfolio.json")]
        if many:
            out += ["--watchlist", self.p("watchlist.json"), "--plan", self.p("plan.json")]
        return out

    def data_copy(self, src=DATA):
        """A writable copy of a data folder."""
        dst = self.p("data")
        shutil.copytree(src, dst)
        return dst


class Dates(unittest.TestCase):
    def test_every_date_style_in_the_notices(self):
        for text, want in (("Oct. 07, 2026", "2026-10-07"), ("21-09-26", "2026-09-21"), ("2026-10-29", "2026-10-29"),
                           ("05-October-2026", "2026-10-05"), ("11-Sep-2026", "2026-09-11"),
                           ("20-Oct-2026", "2026-10-20"), ("7 October 2026", "2026-10-07"),
                           ("October 7th, 2026", "2026-10-07"), ("Dated: October 29, 2026", "2026-10-29"),
                           ("21.09.2026", "2026-09-21"), ("23-Sept-2026", "2026-09-23")):
            self.assertEqual(ins.notice_date(text), want, text)

    def test_numbers_are_day_first_and_never_turned_around(self):
        self.assertEqual(ins.notice_date("05/10/2026"), "2026-10-05")    # 5 October, as in Pakistan
        for text in ("10/29/2026", "13/45/2026", "31-02-2026", "Rate 97.11", "u/c 5.6.1.(d) of PSX", "1 Market Street"):
            self.assertIsNone(ins.notice_date(text), text)

    def test_closed_periods_in_board_meeting_notices(self):
        D = datetime.date
        demo = read(os.path.join(MARKET, "docs", "289306.txt"))      # EXAMPLE.md section 11
        self.assertEqual(ins.find_closed_periods(demo), [(D(2026, 10, 27), D(2026, 11, 3))])
        acme = read(os.path.join(DATA, "docs", "288900.txt"))        # the period runs over two lines
        self.assertEqual(ins.find_closed_periods(acme), [(D(2026, 10, 16), D(2026, 10, 23))])
        before = "The period from December 28, 2026 to January 4, 2027 shall be the closed period."
        self.assertEqual(ins.find_closed_periods(before), [(D(2026, 12, 28), D(2027, 1, 4))])
        self.assertEqual(ins.find_closed_periods(read(os.path.join(MARKET, "docs", "290000.txt"))), [])
        self.assertEqual(ins.span_text(D(2026, 10, 27), D(2026, 11, 3)), "October 27 to November 3, 2026")
        self.assertEqual(ins.span_text(D(2026, 12, 28), D(2027, 1, 4)), "December 28, 2026 to January 4, 2027")

    def test_doc_ids_sort_as_numbers(self):
        self.assertEqual(sorted(["290210", "99999", "100000", "x1"], key=ins.id_key),
                         ["99999", "100000", "290210", "x1"])


class Pages(Temp):
    def test_the_example_notices_on_the_saved_pages(self):
        pages, problems = ins.read_pages(["DEMO", "SYS"], MARKET, {}, True)
        self.assertEqual(problems, [])
        rows = ins.notice_rows(pages)
        self.assertEqual([(r["date"], r["symbol"], r["doc_id"], r["kind"]) for r in rows],
                         [("2026-10-29", "DEMO", "290210", "insider"), ("2026-10-27", "SYS", "290070", "insider"),
                          ("2026-10-23", "DEMO", "289612", "insider")])
        self.assertEqual(rows[0]["pdf"], PSX + "/download/document/290210.pdf")
        self.assertEqual(rows[0]["company"], "Demo Industries Limited")
        self.assertIsNone(rows[2]["pdf"])                               # the GIF-only notice
        self.assertEqual(rows[2]["image"], PSX + "/download/image/289612-1.gif")
        code, out, _ = run(["index", "--symbols", "DEMO,SYS", "--data", MARKET, "--offline", "--now", NOW]
                           + self.files())
        self.assertEqual(code, 0)
        self.assertIn("2026-10-29  DEMO    doc 290210  insider notice: Disclosure of Interest by a Director", out)
        self.assertIn("    PDF https://dps.psx.com.pk/download/document/290210.pdf", out)
        self.assertIn("    GIF https://dps.psx.com.pk/download/image/289612-1.gif", out)
        code, out, _ = run(["index", "--symbols", "DEMO", "--data", MARKET, "--offline", "--json"] + self.files())
        self.assertEqual([r["doc_id"] for r in json.loads(out)], ["290210", "289612"])

    def test_insider_section_110_and_buy_back_titles(self):
        self.assertEqual(ins.match_kind("MATERIAL INFORMATION - DISCLOSURE OF INTEREST"), "insider")
        self.assertEqual(ins.match_kind("Disclosure pursuant to section 110 (1) of the Securities Act, 2015"), "holder")
        self.assertEqual(ins.match_kind("Reporting of shares purchased (buy-back) by Engro Holdings Limited"), "buyback")
        for title in ("Material Information", "COMPLETION OF PURCHASE PERIOD FOR BUY-BACK OF SHARES",
                      "Submission of Newspaper Clippings of Public Announcement of Closure of Buy-Back of Shares"):
            self.assertIsNone(ins.match_kind(title), title)
        pages, _ = ins.read_pages(["ACME"], DATA, {}, True)
        rows = ins.notice_rows(pages)
        self.assertEqual([(r["doc_id"], r["kind"]) for r in rows],
                         [("290301", "insider"), ("290290", "holder"), ("290280", "buyback")])
        self.assertEqual(rows[0]["attachment"], PSX + "/download/attachment/290301-1.pdf")
        self.assertIsNone(rows[0]["pdf"])

    def test_since_and_newest_first(self):
        pages, _ = ins.read_pages(["DEMO", "FFC", "HBL", "OGDC", "SYS"], MARKET, {}, True)
        rows = ins.notice_rows(pages, since="2026-10-27")
        self.assertEqual([(r["symbol"], r["doc_id"]) for r in rows],
                         [("FFC", "290255"), ("DEMO", "290210"), ("SYS", "290070")])   # same day: higher id first
        self.assertEqual(len(ins.notice_rows(pages)), 5)               # HBL's 09-15 notice too

    def test_which_symbols_are_read(self):
        args = ins._parser().parse_args(["index", "--symbols", "demo, sys,DEMO"] + self.files())
        self.assertEqual(ins.pick_symbols(args, MARKET), (["DEMO", "SYS"], "--symbols"))
        args = ins._parser().parse_args(["index"] + self.files())
        self.assertEqual(ins.pick_symbols(args, MARKET)[0], ["DEMO", "FFC", "HBL", "OGDC", "SYS"])  # saved pages
        self.put("watchlist.json", {"hits": [{"symbol": "PSO"}, {"symbol": "DEMO"}]})
        self.put("plan.json", {"symbol": "DEMO"})
        self.put("portfolio.json", {"cash": 1000.0, "positions": [{"ticker": "HBL", "shares": 100},
                                                                  {"symbol": "OGDC", "shares": 0}]})
        self.assertEqual(ins.pick_symbols(args, MARKET), (["PSO", "DEMO", "HBL"], "your watchlist, plan and portfolio"))

    def test_site_off_says_what_to_save_and_never_fetches(self):
        code, out, _ = run(["index", "--symbols", "DEMO,NOPE", "--data", MARKET, "--now", NOW] + self.files())
        self.assertEqual(code, 0)
        self.assertIn("NOPE: The PSX site is off for this tool: save https://dps.psx.com.pk/company/NOPE as "
                      + os.path.join(MARKET, "company", "NOPE.html"), out)
        self.assertIn('"psx_site": true', out)

    def test_site_on_reads_a_page_politely_and_offline_never(self):
        self.put("config.json", {"psx_site": True})
        empty = self.p("empty")
        os.makedirs(empty)
        page = read_bytes(os.path.join(MARKET, "company", "DEMO.html"))
        with FakeNet({PSX + "/company/DEMO": page, PSX + "/company/SYS": 404}) as net:
            code, out, _ = run(["index", "--symbols", "DEMO,SYS", "--data", empty] + self.files())
        self.assertEqual(code, 0)
        self.assertIn("doc 290210", out)
        self.assertIn("SYS: PSX answered HTTP 404 for https://dps.psx.com.pk/company/SYS", out)
        self.assertEqual([c[0] for c in net.calls], [PSX + "/company/DEMO", PSX + "/company/SYS"])
        self.assertEqual(net.calls[0][1], {"User-agent": ins.UA, "Accept-encoding": "gzip"})  # nothing else
        self.assertEqual(len(net.sleeps), 1)
        self.assertGreater(net.sleeps[0], 1.4)                          # PAUSE: 1.5 s between requests
        self.assertEqual(os.listdir(empty), [])                         # a fetched page is never saved
        with FakeNet({}) as net:
            code, out, _ = run(["index", "--symbols", "DEMO", "--data", empty, "--offline"] + self.files())
        self.assertEqual((code, net.calls), (1, []))
        self.assertIn("--offline reads only the data folder", out)
        with FakeNet({PSX + "/company/DEMO": page}) as net:      # a --now replay never fetches (D3)
            code, out, _ = run(["index", "--symbols", "DEMO", "--data", empty, "--now", NOW] + self.files())
        self.assertEqual((code, net.calls), (1, []))

    def test_site_on_asks_the_site_before_an_old_saved_page(self):
        # the saved DEMO page is from before notice 290210; the site has it (D3: the site first)
        self.put("config.json", {"psx_site": True})
        data = self.data_copy(MARKET)
        saved = os.path.join(data, "company", "DEMO.html")
        page = read(saved)
        old = re.sub(r"<tr><td>Oct 29, 2026</td><td>Disclosure of Interest.*?</tr>", "", page, count=1, flags=re.S)
        self.assertNotIn("290210", old)
        with open(saved, "w", encoding="utf-8") as fh:
            fh.write(old)
        self.put("state/insiders.json", {"seen": ["289612"], "last_check": "2026-10-28T11:00:00Z"})
        new = ["new", "--symbols", "DEMO", "--data", data, "--state", self.state, "--dry-run"] + self.files()
        with FakeNet({PSX + "/company/DEMO": page.encode("utf-8")}) as net:
            code, out, _ = run(new)
        self.assertEqual((code, [c[0] for c in net.calls]), (0, [PSX + "/company/DEMO"]))
        self.assertIn("NEW insider notice: DEMO (Demo Industries Limited), doc 290210, posted 2026-10-29.", out)
        self.assertNotIn("Saved pages from before today", out)       # it came from the site
        with FakeNet({}) as net:                                        # the site fails: the saved page, with a note
            code, out, _ = run(new)
        self.assertEqual((code, len(net.calls)), (1, 1))
        self.assertIn("Nothing new since Wed 2026-10-28 4:00 PM PKT.", out)
        self.assertIn("DEMO: the PSX site could not be read, so this uses the saved page %s" % saved, out)
        with FakeNet({PSX + "/company/DEMO": page.encode("utf-8")}) as net:   # a replay: the saved page only
            code, out, _ = run(new + ["--now", NOW])
        self.assertEqual((code, net.calls), (1, []))
        self.assertNotIn("could not be read", out)

    def test_a_saved_page_is_old_by_its_clock_not_by_its_last_trade(self):
        # top bar "Oct 30, 2026 1:39 AM": saved after midnight; "As of" Oct 28: no trade on Oct 29
        page = read(os.path.join(MARKET, "company", "DEMO.html"))
        page = page.replace(">Oct 29, 2026 3:52 PM<", ">Oct 30, 2026 1:39 AM<").replace(
            "As of Thu, Oct 29, 2026 3:49 PM", "As of Wed, Oct 28, 2026 3:49 PM")
        self.put("data/company/DEMO.html", page)
        index = ["index", "--symbols", "DEMO", "--data", self.p("data"), "--offline"] + self.files()
        _, out, _ = run(index + ["--now", "2026-10-30 09:00"])
        self.assertNotIn("Saved pages from before today", out)          # made today, before the open
        _, out, _ = run(index + ["--now", "2026-10-31 10:00"])
        self.assertIn("Saved pages from before today (Fri 2026-10-30: DEMO). Newer notices are not in them.", out)
        self.put("data/company/DEMO.html", re.sub(r'<div class="topbar__status__label">[^<]*</div>', "", page))
        _, out, _ = run(index + ["--now", "2026-10-30 09:00"])            # no clock: the As of time stands in
        self.assertIn("Saved pages from before today (Wed 2026-10-28: DEMO).", out)


class NewSinceLastCheck(Temp):
    def new(self, *extra):
        return run(["new", "--data", MARKET, "--offline", "--now", NOW, "--state", self.state] + self.files()
                   + list(extra))

    def test_alerts_once(self):
        code, out, _ = self.new("--symbols", "DEMO,SYS,HBL")
        self.assertEqual(code, 0)
        self.assertIn("First run: notices posted since 2026-09-29 (the last 30 days).", out)
        for doc in ("290210", "290070", "289612"):
            self.assertIn("doc %s, posted" % doc, out)
        self.assertNotIn("285842", out)                    # HBL's notice of 2026-09-15: older than 30 days
        saved = json.loads(read(self.state))
        self.assertEqual(saved["seen"], ["285842", "289612", "290070", "290210"])   # it is marked as seen
        self.assertEqual(saved["last_check"], "2026-10-29T11:00:00Z")              # 16:00 Karachi time, in UTC
        code, out, _ = self.new("--symbols", "DEMO,SYS,HBL")
        self.assertEqual(code, 1)
        self.assertIn("Nothing new since Thu 2026-10-29 4:00 PM PKT.", out)

    def test_a_new_doc_id_is_new(self):
        pages, _ = ins.read_pages(["DEMO", "SYS"], MARKET, {}, True)
        rows = ins.notice_rows(pages)
        state = {"seen": ["289612", "290070"], "last_check": "2026-10-28T11:00:00Z"}
        fresh = ins.new_since(rows, state, datetime.date(2026, 10, 29))
        self.assertEqual([r["doc_id"] for r in fresh], ["290210"])
        first = ins.new_since(rows, {"seen": [], "last_check": None}, datetime.date(2026, 11, 27))
        self.assertEqual([r["doc_id"] for r in first], ["290210"])      # 30 days back is 2026-10-28

    def test_dry_run_keeps_state(self):
        code, out, _ = self.new("--symbols", "DEMO", "--dry-run")
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.state))

    def test_the_example_with_read(self):
        code, out, _ = self.new("--symbols", "DEMO,SYS", "--read")
        self.assertEqual(code, 0)
        self.assertEqual(out.replace(MARKET, "psx/data"), EXAMPLE)

    def test_the_skill_page_shows_the_real_example(self):
        skill = read(os.path.join(ROOT, "psx-insiders", "SKILL.md"))
        for line in EXAMPLE.splitlines():
            self.assertIn(line, skill)

    def test_the_skill_page_says_the_rules(self):
        skill = read(os.path.join(ROOT, "psx-insiders", "SKILL.md"))
        head, text = flat(skill.split("\n---\n")[0]), flat(skill)
        self.assertIn('"are directors buying SYS"', head)               # real tickers, never DEMO (D19)
        self.assertNotIn("DEMO", head)
        self.assertNotIn("0 to 7 days", text)                           # the notice on PSX can come later
        self.assertIn("The rule gives the insider 7 days to tell the company.", text)
        self.assertIn("So the notice on PSX can come later.", text)
        self.assertNotIn("saved page wins", text)                       # the site first when it is on (D3)
        self.assertIn("With the site on, the tool asks the site first. If the site fails, it uses the saved page "
                      "and says so.", text)
        self.assertIn("With the site off, never open a `dps.psx.com.pk` link yourself. Ask the user to open it "
                      "in their browser and save it as `psx/data/docs/<id>.<ext>`, or paste a screenshot.", text)
        self.assertIn("Never opens a `dps.psx.com.pk` link itself while the site is off.", text)
        self.assertIn("It can sit on the buyer's page and be about another company's shares. Check whose shares "
                      "it names.", text)
        self.assertIn('"Not checked: board meeting doc ..."', text)

    def test_you_hold_flags_your_stock(self):
        self.put("portfolio.json", {"cash": 154353.85, "positions": [{"ticker": "DEMO", "shares": 3428}]})
        code, out, _ = self.new("--symbols", "DEMO,SYS")
        self.assertEqual(out.count("  You hold DEMO."), 2)           # both DEMO notices, not SYS
        self.assertNotIn("You hold SYS", out)

    def test_json_and_the_watchlist(self):
        self.put("watchlist.json", {"hits": [{"symbol": "SYS"}]})
        code, out, err = self.new("--json")
        doc = json.loads(out)
        self.assertEqual((code, doc["first_run"], [r["doc_id"] for r in doc["new"]]), (0, True, ["290070"]))
        self.assertEqual(doc["reports"], [])
        code, out, _ = self.new("--json", "--read")
        self.assertEqual(code, 1)                                       # seen now: nothing new
        self.assertEqual(json.loads(out)["new"], [])

    def test_no_symbols_and_stale_pages(self):
        empty = self.p("empty")
        os.makedirs(empty)
        code, out, _ = run(["new", "--data", empty, "--offline", "--state", self.state] + self.files())
        self.assertEqual(code, 1)
        self.assertIn("No symbols to check: pass --symbols DEMO,SYS", out)
        code, out, _ = run(["new", "--symbols", "DEMO", "--data", MARKET, "--offline", "--now", "2026-11-02 09:00",
                            "--state", self.state] + self.files())
        self.assertIn("Saved pages from before today (Thu 2026-10-29: DEMO). Newer notices are not in them.", out)


# What a stand-in pdftotext gives for ACME's attachment-only notice 290301 (made up).
ACME_FORM29 = """
                                              FORM-29
   Karachi                                                                      Dated: October 28, 2026

   Sr.  Name of Relevant Person(s)     Details of Transaction(s)                                    Cumulative Shareholding
   No.  with Description               Form of      Market   Date         Nature   No. of      Rate      Number of     Percentage
   1    Mr. Kamran Yousuf              CDC          Ready    2026-10-20   Buy      3,000       118.40    53,000        0.02%
        (Chief Executive)
   2    Mr. Kamran Yousuf              CDC          Ready    2026-10-26   Sell     1,000       121.10    52,000        0.02%
        (Chief Executive)
\f"""


class ReadANotice(Temp):
    def test_saved_text_first_then_the_image(self):
        pages, _ = ins.read_pages(["DEMO"], MARKET, {}, True)
        rows = dict((r["doc_id"], r) for r in ins.notice_rows(pages))
        text, how = ins.read_notice(rows["290210"], MARKET, {}, True)
        self.assertIn("FORM-29", text)
        self.assertEqual(how, os.path.join(MARKET, "docs", "290210.txt"))
        text, how = ins.read_notice(rows["289612"], MARKET, {}, True)
        self.assertIsNone(text)                                         # the site is off: the user saves it (D17)
        self.assertEqual(how, "Image only, no text to read. Ask the user to open "
                              "https://dps.psx.com.pk/download/image/289612-1.gif in their browser and save it as "
                              "%s, or paste a screenshot. Do not fetch it yourself. Do not guess its rows."
                         % os.path.join(MARKET, "docs", "289612.gif"))
        text, how = ins.read_notice(rows["289612"], MARKET, {"psx_site": True}, True)
        self.assertEqual(how, "Image only, no text to read: open https://dps.psx.com.pk/download/image/289612-1.gif "
                              "and read the table (Claude can read images). Do not guess its rows.")
        gif = self.put("data/docs/289612.gif", b"GIF89a made up")      # the name the user is asked to use
        text, how = ins.read_notice(rows["289612"], self.p("data"), {}, True)
        self.assertEqual(how, "Image only, no text to read: read the saved image %s (Claude can read images)." % gif)
        gif = self.put("data/docs/289612-1.gif", b"GIF89a made up")
        text, how = ins.read_notice(rows["289612"], self.p("data"), {}, True)
        self.assertEqual(how, "Image only, no text to read: read the saved image %s (Claude can read images)." % gif)

    def test_a_saved_pdf_goes_through_pdftotext(self):
        data = self.data_copy()
        self.put("data/docs/290301.pdf", b"%PDF-1.4 made up")
        seen = []
        ins.pdf_to_text = lambda path: seen.append(path) or ACME_FORM29
        code, out, _ = run(["read", "290301", "--symbols", "ACME", "--data", data, "--offline"] + self.files())
        self.assertEqual(code, 0)
        self.assertEqual(seen, [os.path.join(data, "docs", "290301.pdf")])
        self.assertTrue(out.startswith("Insider notice: ACME (Acme Textiles Limited), doc 290301, posted 2026-10-29."))
        self.assertIn("  Attachment https://dps.psx.com.pk/download/attachment/290301-1.pdf", out)
        self.assertIn("Read from %s: 2 trades." % os.path.join(data, "docs", "290301.pdf"), out)
        self.assertIn("This trade date is inside the closed period ACME announced (October 16 to October 23, 2026).",
                      out)
        self.assertEqual(out.count("inside the closed period"), 1)     # the sell of 10-26 is after it
        self.assertIn("Posted 9 days after the trade. Late: over 7 days.", out)

    def test_pdftotext_missing_or_a_scan(self):
        data = self.data_copy()
        self.put("data/docs/290301.pdf", b"%PDF-1.4 made up")
        row = {"doc_id": "290301", "attachment": PSX + "/download/attachment/290301-1.pdf", "kind": "insider"}
        ins.pdf_to_text = lambda path: None
        text, how = ins.read_notice(row, data, {}, True)
        self.assertIsNone(text)
        self.assertIn("pdftotext is not installed or could not read it: open the PDF and read the table "
                      "(Claude can read PDFs).", how)
        ins.pdf_to_text = lambda path: "  \f\n"
        text, how = ins.read_notice(row, data, {}, True)
        self.assertIsNone(text)
        self.assertIn("has no text (a scanned page): open it and read the table", how)

    def test_site_off_or_offline_says_what_to_save(self):
        row = {"doc_id": "290280", "pdf": PSX + "/download/document/290280.pdf",
               "image": PSX + "/download/image/290280-1.gif"}
        text, how = ins.read_notice(row, DATA, {}, False)
        self.assertIsNone(text)
        self.assertEqual(how, "Ask the user to open https://dps.psx.com.pk/download/document/290280.pdf in their "
                              "browser and save it as %s, or paste a screenshot. Do not fetch it yourself."
                         % os.path.join(DATA, "docs", "290280.pdf"))  # never "open <link>" with the site off (D17)
        text, how = ins.read_notice(row, DATA, {"psx_site": True}, True)
        self.assertIn("(--offline reads only the data folder)", how)
        self.assertIn(" Or open https://dps.psx.com.pk/download/image/290280-1.gif and read the table", how)

    def test_an_empty_saved_text_is_a_scan(self):
        # pdftotext of a scanned notice gives an empty text, or only form feeds
        data = self.data_copy()
        self.put("data/docs/290301.txt", "\f\f\n")
        self.put("data/docs/290290.txt", " \n")
        code, out, _ = run(["read", "290301", "--symbols", "ACME", "--data", data, "--offline"] + self.files())
        txt = os.path.join(data, "docs", "290301.txt")
        self.assertEqual(code, 0)
        self.assertNotIn("trades", out)
        self.assertIn("%s is empty: the notice is most likely a scan. Ask the user to open "
                      "https://dps.psx.com.pk/download/attachment/290301-1.pdf in their browser and save it as %s, "
                      "or paste a screenshot." % (txt, os.path.join(data, "docs", "290301.pdf")), out)
        code, out, _ = run(["read", "290290", "--symbols", "ACME", "--data", data, "--offline"] + self.files())
        self.assertIn("290290.txt is empty: the notice is most likely a scan.", out)
        self.assertNotIn("This tool reads only insider tables", out)
        self.put("data/docs/290301.pdf", b"%PDF-1.4 made up")
        ins.pdf_to_text = lambda path: ACME_FORM29                     # a saved PDF with text still reads
        text, how = ins.read_notice({"doc_id": "290301", "kind": "insider"}, data, {}, True)
        self.assertEqual((how, "FORM-29" in text), (os.path.join(data, "docs", "290301.pdf"), True))

    def test_site_on_saves_the_pdf_once(self):
        data = self.data_copy()
        url = PSX + "/download/attachment/290301-1.pdf"
        row = {"doc_id": "290301", "attachment": url, "kind": "insider"}
        ins.pdf_to_text = lambda path: ACME_FORM29
        with FakeNet({url: b"%PDF-1.4 made up"}) as net:
            text, how = ins.read_notice(row, data, {"psx_site": True}, False)
            again = ins.read_notice(row, data, {"psx_site": True}, False)
        self.assertEqual([c[0] for c in net.calls], [url])              # saved, so never fetched twice
        self.assertEqual((how, again[1]), (os.path.join(data, "docs", "290301.pdf"),) * 2)
        self.assertEqual(read_bytes(how), b"%PDF-1.4 made up")
        bad = {"doc_id": "290280", "pdf": PSX + "/download/document/290280.pdf"}
        with FakeNet({bad["pdf"]: b"<html>busy</html>"}):
            text, how = ins.read_notice(bad, data, {"psx_site": True}, False)
        self.assertIn("did not come back as a PDF", how)
        self.assertFalse(os.path.exists(os.path.join(data, "docs", "290280.pdf")))
        with FakeNet({}):
            text, how = ins.read_notice(dict(bad, doc_id="290270"), data, {"psx_site": True}, False)
        self.assertIn("PSX answered HTTP 404", how)

    def test_other_kinds_are_listed_not_parsed(self):
        code, out, _ = run(["new", "--read", "--symbols", "ACME", "--data", DATA, "--offline", "--now", NOW,
                            "--state", self.state] + self.files())
        self.assertEqual(code, 0)
        self.assertIn("NEW section 110 notice (someone went over 10%): ACME (Acme Textiles Limited), doc 290290, "
                      "posted 2026-10-28.", out)
        self.assertIn("\n  Check whose shares it names: it can be ACME buying shares of another company.\n", out)
        self.assertIn("Text: %s. This tool reads only insider tables, so read it yourself."
                      % os.path.join(DATA, "docs", "290290.txt"), out)
        self.assertIn("NEW buy-back report: ACME", out)
        self.assertIn("Ask the user to open https://dps.psx.com.pk/download/document/290280.pdf in their browser", out)
        self.assertNotIn("COMPLETION", out)
        self.assertEqual(opens_psx_itself(out), [])                     # the site is off (D17)
        self.assertEqual(out.count("Check whose shares"), 1)            # only on the section 110 notice
        code, out, _ = run(["index", "--symbols", "ACME", "--data", DATA, "--offline"] + self.files())
        self.assertIn("doc 290290  section 110 notice (someone went over 10%): Disclosure pursuant to section 110",
                      out)
        self.assertIn("\n    Check whose shares it names: it can be ACME buying shares of another company.\n", out)
        code, out, _ = run(["read", "290290", "--symbols", "ACME", "--data", DATA, "--offline"] + self.files())
        self.assertTrue(out.startswith("Section 110 notice (someone went over 10%): ACME (Acme Textiles Limited), "
                                       "doc 290290, posted 2026-10-28.\n"))
        self.assertIn("Check whose shares it names: it can be ACME buying shares of another company.", out)

    def test_read_a_doc_by_its_id(self):
        code, out, _ = run(["read", "290210", "--data", MARKET, "--offline"] + self.files())
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Insider notice: DEMO (Demo Industries Limited), doc 290210, "
                                       "posted 2026-10-29."))
        self.assertIn(CLOSED_DEMO, flat(out))
        code, out, _ = run(["read", "289306", "--data", MARKET, "--offline"] + self.files())
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Notice: DEMO (Demo Industries Limited), doc 289306, posted 2026-10-20."))
        self.assertIn("It is not an insider notice: NOTICE OF BOARD MEETING.", out)
        self.assertIn("Its text names a closed period: October 27 to November 3, 2026.", out)
        code, out, _ = run(["read", "999999", "--data", MARKET, "--offline"] + self.files())
        self.assertEqual(code, 1)
        self.assertIn("Doc 999999 is not on the company pages read.", out)
        code, out, _ = run(["read", "289612", "--data", MARKET, "--offline", "--json"] + self.files())
        self.assertEqual((code, json.loads(out)["report"]), (0, None))  # image only: read it yourself


class ParseNotice(Temp):
    def report(self, path, filed=None):
        return ins.parse_notice_text(read(path), filed)

    def test_form29_rows_of_the_example(self):
        rep = self.report(os.path.join(MARKET, "docs", "290210.txt"), "2026-10-29")
        self.assertEqual((rep["layout"], rep["letter_date"], rep["filed_from"]), ("form-29", "2026-10-29", "posted"))
        got = [(r["name"], r["role"], r["date"], r["side"], r["shares"], r["rate"], r["value"], r["market"],
                r["form"], r["holding_after"], r["days_to_disclose"], r["late"]) for r in rep["rows"]]
        self.assertEqual(got, [
            ("Mr. Salman Akhtar", "Director", "2026-10-28", "buy", 25000, 97.11, 2427750.0, "Ready", "CDC",
             {"shares": 1265000, "pct": 0.51}, 1, False),
            ("Mrs. Hina Salman", "Spouse of Director", "2026-10-28", "buy", 10000, 97.26, 972600.0, "Ready", "CDC",
             {"shares": 410000, "pct": 0.16}, 1, False)])
        self.assertEqual(rep["skipped"], [])

    def test_own_table_rows_of_the_example(self):
        rep = self.report(os.path.join(MARKET, "docs", "290070.txt"), "2026-10-27")
        self.assertEqual(rep["layout"], "own")
        got = [(r["name"], r["role"], r["date"], r["side"], r["shares"], r["rate"], r["market"], r["holding_after"],
                r["days_to_disclose"], r["late"]) for r in rep["rows"]]
        self.assertEqual(got, [
            ("Mr. Danish Iqbal", "Senior Management", "2026-10-20", "sell", 150000, 120.6, "Ready", None, 7, False),
            ("Mr. Danish Iqbal", "Senior Management", "2026-10-21", "sell", 85500, 122.68, "Ready", None, 6, False),
            ("Ms. Mehwish Tariq", "Executive", "2026-10-22", "buy", 5000, 124.16, "Ready", None, 5, False)])

    def test_late_is_over_7_days(self):
        rep = self.report(os.path.join(MARKET, "docs", "290070.txt"), "2026-10-28")
        self.assertEqual([(r["days_to_disclose"], r["late"]) for r in rep["rows"]], [(8, True), (7, False), (6, False)])
        rep = self.report(os.path.join(MINE, "own-five-sells.txt"), "2026-10-02")
        self.assertEqual([r["days_to_disclose"] for r in rep["rows"]], [21, 14, 11, 10, 9])
        self.assertTrue(all(r["late"] and r["side"] == "sell" for r in rep["rows"]))
        self.assertEqual([r["rate"] for r in rep["rows"]], [117.0, 122.14, 121.14, 121.05, 122.0])
        self.assertEqual(rep["rows"][0]["name"], "Ms. Sana Iqbal")

    def test_one_row_letter_with_an_address_footer(self):
        rep = self.report(os.path.join(MINE, "own-one-row.txt"))
        self.assertEqual([(r["name"], r["role"], r["date"], r["nature"], r["shares"], r["rate"], r["market"])
                          for r in rep["rows"]],
                         [("Mr. Kamran Yousuf", "Senior Management", "2026-10-05", "Buy", 2000, 113.45, "Ready")])
        self.assertEqual((rep["letter_date"], rep["filed_from"], rep["rows"][0]["days_to_disclose"]),
                         ("2026-10-07", "letter", 2))
        self.assertEqual(rep["skipped"], [])                            # the footer is not a trade

    def test_other_date_styles_and_names_over_two_lines(self):
        rep = self.report(os.path.join(MINE, "form29-styles.txt"), "2026-10-09")
        self.assertEqual([(r["name"], r["role"], r["date"], r["nature"], r["side"]) for r in rep["rows"]], [
            ("Mr. Tariq Mehmood", "Exec. Finance", "2026-10-07", "Sell", "sell"),
            ("Mr. Muhammad Azhar Iqbal Chaudhry", "Chief Financial Officer", "2026-09-21", "BUY", "buy"),
            ("Mrs. Sara Tariq", "Spouse of Executive", "2026-10-05", "Buy", "buy")])
        self.assertEqual((rep["rows"][0]["rate"], rep["rows"][0]["holding_after"]), (766.5, {"shares": 557, "pct": 0.0}))
        self.assertEqual(rep["letter_date"], "2026-10-09")
        self.assertEqual(len(rep["skipped"]), 1)                        # 10/29/2026 is month first: not read
        self.assertIn("10/29/2026", rep["skipped"][0])
        code, out, _ = run(["parse", os.path.join(MINE, "form29-styles.txt")] + self.files(False))
        self.assertIn("Lines that look like trades but did not read: 1. Check the notice.", out)

    def test_parse_cli_with_the_closed_period_and_the_day_range(self):
        path = os.path.join(MARKET, "docs", "290210.txt")
        code, out, _ = run(["parse", path, "--symbol", "DEMO", "--filed", "2026-10-29", "--data", MARKET,
                            "--offline"] + self.files(False))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("Read from %s: 2 trades.\n" % path))
        self.assertEqual(flat(out).count(CLOSED_DEMO), 2)
        self.assertNotIn("outside", out)                                # both rates inside 95.76 to 98.46
        code, out, _ = run(["parse", path, "--symbol", "DEMO", "--data", MARKET, "--offline", "--json"]
                           + self.files(False))
        rep = json.loads(out)
        self.assertEqual([r["day_range"] for r in rep["rows"]], [{"low": 95.76, "high": 98.46, "inside": True}] * 2)
        self.assertEqual(rep["closed_periods"], [{"from": "2026-10-27", "to": "2026-11-03", "doc_id": "289306"}])
        odd = self.put("odd.txt", read(path).replace("97.11", "99.90"))
        code, out, _ = run(["parse", odd, "--symbol", "DEMO", "--data", MARKET, "--offline"] + self.files(False))
        self.assertIn("The rate is outside DEMO's range that day (95.76 to 98.46). It may be an off-market deal, "
                      "or a misread. Check the notice.", out)
        code, out, _ = run(["parse", os.path.join(MINE, "own-five-sells.txt"), "--symbol", "ACME", "--data", DATA,
                            "--offline"] + self.files(False))
        self.assertIn("Closed period: ACME announced October 16 to October 23, 2026. No trade here is inside it. "
                      "Not checked: board meeting doc 283300 (no text in docs/).", out)   # its text is not saved
        self.assertNotIn("inside the closed period", out)
        self.put("data/docs/283300.txt", "NOTICE OF BOARD MEETING on August 25, 2026. No closed period dates.")
        shutil.copytree(os.path.join(DATA, "company"), self.p("data", "company"))
        shutil.copy(os.path.join(DATA, "docs", "288900.txt"), self.p("data", "docs"))
        code, out, _ = run(["parse", os.path.join(MINE, "own-five-sells.txt"), "--symbol", "ACME", "--data",
                            self.p("data"), "--offline"] + self.files(False))
        self.assertIn("No trade here is inside it.\n", out)              # every board meeting notice was read
        self.assertNotIn("Not checked", out)
        code, out, _ = run(["parse", path, "--symbol", "DEMO", "--data", MARKET, "--offline"] + self.files(False))
        self.assertNotIn("Not checked", out)                            # both trades are flagged: nothing to add

    def test_you_hold_and_no_rows(self):
        self.put("portfolio.json", {"cash": 0, "positions": [{"ticker": "DEMO", "shares": 3428}]})
        path = os.path.join(MARKET, "docs", "290210.txt")
        code, out, _ = run(["parse", path, "--symbol", "DEMO", "--data", MARKET, "--offline"] + self.files(False))
        self.assertIn("\n  You hold DEMO.\n", out)
        code, out, _ = run(["parse", os.path.join(MARKET, "docs", "289306.txt")] + self.files(False))
        self.assertEqual(code, 1)
        self.assertIn("No trade rows found: open the notice and read the table.", out)

    def test_a_gift_with_an_empty_rate_cell(self):
        line = ("   1    Mr. Omar Saeed                 CDC          Ready    2026-10-28   Gift     5,000       -"
                "         45,000        0.02%\n        (Director)\n")
        rep = ins.parse_notice_text(line, "2026-10-29")
        r = rep["rows"][0]
        self.assertEqual((r["side"], r["shares"], r["rate"], r["value"], r["role"], r["holding_after"]),
                         ("gift", 5000, None, None, "Director", {"shares": 45000, "pct": 0.02}))
        self.assertEqual(ins.describe_row(r, "DEMO", "posted")[0],
                         "  - Mr. Omar Saeed (Director): gift of 5,000 shares on 2026-10-28.")

    def test_a_second_date_column_is_not_a_number(self):
        line = "    Mr. Omar Saeed      20-Oct-2026     Buy     3,000     118.40     Ready     22-Oct-2026\n    (Director)\n"
        r = ins.parse_notice_text(line)["rows"][0]
        self.assertEqual((r["name"], r["date"], r["shares"], r["rate"], r["holding_after"]),
                         ("Mr. Omar Saeed", "2026-10-20", 3000, 118.4, None))

    def test_every_output_says_fast_not_early_and_no_inside_information(self):
        outs = [self.new_out(), run(["index", "--data", MARKET, "--offline", "--now", NOW] + self.files())[1],
                run(["read", "290070", "--data", MARKET, "--offline"] + self.files())[1],
                run(["parse", os.path.join(MINE, "own-one-row.txt")] + self.files(False))[1]]
        outs.append(run(["read", "289612", "--data", MARKET, "--offline"] + self.files())[1])   # image only
        for out in outs:
            for line in ins.NOTE.split("\n"):
                self.assertIn(line, out)
            self.assertNotIn(chr(0x2014), out)
            self.assertEqual(opens_psx_itself(out), [])                 # the site is off: the user opens links
            low = out.lower()
            self.assertEqual(low.count("inside information"), 1)       # only "There is no inside information here."
            for word in ("$", "profit", "p&l", "return", "real time", "guarantee"):
                self.assertNotIn(word, low, word)
            self.assertNotRegex(low, r"\blive\b")

    def new_out(self):
        return run(["new", "--read", "--data", MARKET, "--offline", "--now", NOW, "--state", self.state]
                   + self.files())[1]

    def test_errors_exit_2(self):
        self.assertEqual(run([])[0], 2)
        code, _, err = run(["parse", self.p("missing.txt")] + self.files(False))
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("insiders: "))
        code, _, err = run(["new", "--now", "soon", "--data", MARKET, "--state", self.state] + self.files())
        self.assertEqual(code, 2)
        self.put("state/insiders.json", "{not json")
        code, _, err = run(["new", "--data", MARKET, "--offline", "--state", self.state] + self.files())
        self.assertEqual(code, 2)
        self.assertIn("is not valid JSON", err)
        code, _, err = run(["index", "--since", "29/10/2026", "--data", MARKET, "--offline"] + self.files())
        self.assertIn("--since wants a date like 2026-10-29", err)


if __name__ == "__main__":
    unittest.main()
