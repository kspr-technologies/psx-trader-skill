"""psx-entry tests. Data: the made-up sample market in tests/fixtures/psx-market/ (DEMO on
Thu 2026-10-29: tape/2026-10-29/DEMO.jsonl, tape/2026-10-29/market.jsonl, bars/DEMO.csv
and bars/DEMO-1m.csv; see its README and EXAMPLE section 5) and the made-up file in
tests/fixtures/psx-entry/:

DEMO-1m-wick.csv   SYNTHETIC 1-minute bars of Wed 2026-10-28 (not market data): a wick
                   above the range high, a close AT the high, a missing minute.

The network is never used: http_get and SLEEP are swapped for fakes (a fake PSX site for
the watch that polls), and the tests write only to temp folders.
"""

import datetime
import io
import json
import os
import re
import shlex
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("psx-entry", "entry")

MARKET = os.path.join(FIX, "psx-market")
TAPE = os.path.join(MARKET, "tape", "2026-10-29", "DEMO.jsonl")
MTAPE = os.path.join(MARKET, "tape", "2026-10-29", "market.jsonl")
BARS = os.path.join(MARKET, "bars", "DEMO.csv")
BARS1 = os.path.join(MARKET, "bars", "DEMO-1m.csv")
WICK = os.path.join(FIX, "psx-entry", "DEMO-1m-wick.csv")
DAY = datetime.date(2026, 10, 29)
DT = datetime.datetime
SAFETY = """## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order. psx-broker shows the order and waits. It prints the line to
  type into the broker app only after psx-risk passed that exact order today and the user typed an
  explicit yes for that one order in the chat. The user then places it in their own broker's app.
  If the user ever adds a broker tool that can place orders, set it to "Needs approval"
  (Customize > Connectors > tool permissions), never "Always allow".
- Long only. No short selling (PSX bans blank sales), no margin, no futures and no options.
- Broker logins, passwords and PINs never go in the chat.
"""
_SAVED = {}


def _no_network(*a, **k):
    raise AssertionError("a test tried to use the network or to really sleep")


def setUpModule():
    _SAVED.update(http_get=entry.http_get, SLEEP=entry.SLEEP, utc_now=entry.utc_now)
    entry.http_get, entry.SLEEP = _no_network, _no_network


def tearDownModule():
    for k, v in _SAVED.items():
        setattr(entry, k, v)


def pkt(text):
    return entry.parse_now(text)


def at(text, src=TAPE, data=MARKET, sym="DEMO", cfg=None, live=False, **kw):
    """evaluate() at a Karachi (data) time. src is the first source (--bars FILE); None
    leaves only the data folder (where bars/DEMO.csv comes first)."""
    now = pkt(text)
    chart = entry.load_chart(sym, entry.to_pkt(now).date(), data, src, cfg, live=live)
    return entry.evaluate(chart, now, cfg=cfg, **kw)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


def lines_of(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def write(path, text):
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def capped(folder, cap=100.10):
    """bars/DEMO.csv's 10-29 bars with every bar after the range kept under the 100.20 high."""
    out = ["datetime,open,high,low,close,volume"]
    for row in lines_of(BARS):
        if not row.startswith("2026-10-29"):
            continue
        t, o, h, lo, c, v = row.split(",")
        if t >= "2026-10-29 09:45":
            o, h, lo, c = ["%.2f" % min(float(p), cap) for p in (o, h, lo, c)]
        out.append(",".join((t, o, h, lo, c, v)))
    path = os.path.join(folder, "capped.csv")
    write(path, "\n".join(out) + "\n")
    return path


class Temp(unittest.TestCase):
    """A temp working folder for each test."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)


class TheExample(unittest.TestCase):
    """EXAMPLE.md section 5: DEMO on Thu 2026-10-29, a 15-minute range, the company tape."""

    def test_triggers_at_1015_on_the_1010_bar(self):
        r = at("2026-10-29 10:15")
        self.assertEqual((r["status"], r["session"], r["source"]), ("triggered", "normal", "company-tape"))
        self.assertEqual(r["opening_range"], {"high": 100.2, "low": 98.9, "minutes": 15, "bars": 3, "of": 3,
                                              "complete": True, "avg_volume": 263833})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("10:15 AM", "10:10", 8, 100.65))
        self.assertEqual((t["pct_over"], t["volume"], t["close_time"]), (0.45, 78000, "10:15"))
        self.assertEqual((r["level"], r["prev_close"], r["upper_limit"]), (100.2, 98.4, 108.24))
        self.assertEqual(r["data_time"], "2026-10-29 10:15:00")

    def test_at_1012_the_1010_bar_is_still_forming(self):
        r = at("2026-10-29 10:12")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual((r["last"]["bar_start"], r["last"]["close"], r["bars_closed"]), ("10:05", 100.05, 8))
        self.assertEqual(r["forming"], {"bar_start": "10:10", "closes": "10:15 AM", "price": 100.4})
        self.assertIn("0.15 under the range high 100.20 (-0.15%)", r["reason"])
        self.assertEqual(at("2026-10-29 10:14:59")["status"], "waiting")

    def test_the_lines_are_the_example_lines(self):
        lines = entry.describe(at("2026-10-29 10:12"))
        self.assertEqual(lines[1:], [
            "Opening range 9:30-9:45: high 100.20, low 98.90 (3 of 3 bars).",
            "WAITING  last close 100.05 (10:05 bar), 0.15 under the range high 100.20 (-0.15%).",
            "The 10:10 bar is still forming (100.40 so far, closes 10:15 AM); it does not count until it closes."])
        self.assertTrue(lines[0].startswith("DEMO  Thu 2026-10-29 10:12 PKT  opening-range breakout (15-minute "
                                            "range) on 5-minute bars from the company tape"), lines[0])
        lines = entry.describe(at("2026-10-29 10:15"))
        self.assertEqual(lines[2:], [
            "TRIGGERED 10:15 AM at 100.65: the 10:10 bar (bar 8) closed above the range high 100.20 (+0.45%).",
            "This is the moment to get in, if you take the trade. Nothing was placed.",
            "Free PSX data is about 5 minutes late: check the price in your broker app before you buy.",
            "Next: set the stop and target BEFORE you buy: /psx-exit (exit.py plan --from psx/entry.json)."])

    def test_before_the_open_and_while_the_range_forms(self):
        r = at("2026-10-29 09:20")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("pre-open: limit orders only", r["reason"])
        self.assertIn("the 15-minute range is set at 9:45", r["reason"])
        self.assertIn("the pre-open starts at 9:15 AM PKT", at("2026-10-29 09:10")["reason"])
        r = at("2026-10-29 09:44")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at("2026-10-29 09:46")
        self.assertEqual((r["status"], r["phase"], r["opening_range"]["bars"]), ("waiting", "open", 3))

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at("2026-10-29 16:05")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "10:15 AM")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)

    def test_the_bars_file_gives_the_same(self):
        r = at("2026-10-29 10:12", src=None)        # bars/DEMO.csv is the first source of the sample market
        self.assertEqual((r["source"], r["source_file"]), ("bars-file", os.path.join("bars", "DEMO.csv")))
        self.assertEqual((r["status"], r["last"]["bar_start"], r["last"]["close"]), ("waiting", "10:05", 100.05))
        self.assertIsNone(r["forming"])                           # a 5-minute file has no "so far" price
        self.assertEqual(r["data_time"], "2026-10-29 10:10:00")   # its 10:10 row has not ended at 10:12
        r = at("2026-10-29 10:15", src=None)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"], t["pct_over"], t["volume"]),
                         ("10:15 AM", 8, 100.65, 0.45, 78000))
        self.assertEqual(r["prev_close"], 98.4)                   # from the day's company tape

    def test_the_one_minute_file_gives_the_same_bars(self):
        r = at("2026-10-29 10:15", src=BARS1)
        self.assertEqual((r["opening_range"]["high"], r["opening_range"]["low"]), (100.2, 98.9))
        self.assertEqual((r["trigger"]["bar_index"], r["trigger"]["price"], r["trigger"]["volume"]), (8, 100.65, 78000))
        r = at("2026-10-29 10:12", src=BARS1)                      # 1-minute rows give a "so far" price
        self.assertEqual((r["status"], r["forming"]["bar_start"], r["forming"]["price"]), ("waiting", "10:10", 100.0))

    def test_the_screener_tape_has_prices_only(self):
        r = at("2026-10-29 10:15", src=MTAPE)
        self.assertEqual((r["status"], r["source"], r["data_time"]), ("waiting", "screener-tape", "2026-10-29 10:14:00"))
        self.assertIn("the data is as of 10:14 AM, before the 10:10 bar closed at 10:15 AM", r["lag_note"])
        self.assertEqual(r["last"]["close"], 100.03)              # sampled every 2 minutes (EXAMPLE section 4)
        r = at("2026-10-29 10:16", src=MTAPE)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"], t["volume"]), ("10:15 AM", 8, 100.65, None))
        self.assertEqual(r["minutes_since"], 1)


class Rules(Temp):
    def test_volume_confirmation_moves_the_trigger(self):
        r = at("2026-10-29 10:20", min_volume_x=0.25)
        self.assertEqual((r["trigger"]["bar_index"], r["trigger"]["volume_x"]), (8, 0.3))
        r = at("2026-10-29 10:15", min_volume_x=1)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("the 10:10 bar closed above at 100.65 but on 0.30x the range's volume, under your 1.00x",
                      r["reason"])
        # the 10:20 bar on 400,000 shares (1.52x the range's average) moves the trigger there
        rows = [x if not x.startswith("2026-10-29 10:20") else x.rsplit(",", 1)[0] + ",400000" for x in lines_of(BARS)]
        write("heavy.csv", "\n".join(rows) + "\n")
        r = at("2026-10-29 10:30", src="heavy.csv", min_volume_x=1.5)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"], t["volume_x"]), ("10:25 AM", 10, 100.95, 1.52))
        self.assertIn("1.52x the range's volume", "\n".join(entry.describe(r)))

    def test_level_setup(self):
        r = at("2026-10-29 10:30", setup="level", level=100.9)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"], r["trigger"]["price"]), ("10:25 AM", 10, 100.95))
        # the scanner's level 96.56 is under the open: the first close that counts is the 9:40 bar's, at 9:45
        r = at("2026-10-29 10:30", setup="level", level=96.56)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:45 AM", 2))
        with self.assertRaises(entry.EntryError):
            at("2026-10-29 10:30", setup="level")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        path = capped(self.d)
        self.assertEqual(at("2026-10-29 10:49", src=path)["status"], "waiting")
        self.assertEqual(at("2026-10-29 14:59", src=path)["status"], "waiting")
        r = at("2026-10-29 15:00", src=path)
        self.assertEqual((r["status"], r["phase"]), ("expired", "after-cutoff"))
        self.assertIn("no close above the range high 100.20 by 15:00", r["reason"])
        self.assertEqual(at("2026-10-29 15:45", src=path)["phase"], "after-close")
        self.assertIn("No entry on this setup today. Nothing was placed.", entry.describe(r))

    def test_a_bar_counts_only_once_the_data_has_moved_past_it(self):
        # the clock is 10:17, but the newest page (fetched 10:17:07) is as of 10:12
        snaps = [s for s in entry.read_tape(TAPE) if s["asof"] <= "2026-10-29 10:12"]
        path = os.path.join(self.d, "DEMO.jsonl")
        for s in snaps:
            entry.append_tape(path, s)
        r = at("2026-10-29 10:17", src=path)
        self.assertEqual((r["status"], r["forming"]["bar_start"], r["forming"]["price"]), ("waiting", "10:10", 100.4))
        self.assertIn("it counts once the data reaches 10:15 (free PSX data is about 5 minutes late)", r["lag_note"])
        self.assertIn("The data is as of 10:12 AM, before the 10:10 bar closed at 10:15 AM", "\n".join(entry.describe(r)))
        # the cutoff waits for the data too: a 10:15 cutoff does not expire while the data is at 10:12
        cfg = dict(entry.DEFAULTS, no_entries_after="10:15")
        self.assertEqual(at("2026-10-29 10:17", src=path, cfg=cfg)["status"], "waiting")
        # a page fetched at 10:21 that still says 10:12 means no trade since: now the 10:10 bar counts
        entry.append_tape(path, dict(snaps[-1], fetched="2026-10-29T10:21:00+05:00"))
        r = at("2026-10-29 10:21", src=path, cfg=cfg)
        self.assertEqual((r["status"], r["trigger"]["time"], r["trigger"]["price"]), ("triggered", "10:15 AM", 100.4))
        self.assertEqual(r["data_time"], "2026-10-29 10:15:00")

    def test_config_window(self):
        r = at("2026-10-29 10:15", cfg=dict(entry.DEFAULTS, no_entries_after="10:10"))
        self.assertEqual(r["status"], "expired")
        cfg = dict(entry.DEFAULTS, no_entries_before="10:20")
        r = at("2026-10-29 10:30", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "10:20 AM")       # the 10:15 bar, the first to close at or after 10:20
        r = at("2026-10-29 10:15", cfg=cfg)
        self.assertIn("the 10:10 bar closed above at 100.65, but no entries before 10:20 (config)", r["reason"])

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at("2026-10-29 10:15", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at("2026-10-29 10:15", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.now_arg("tomorrow")
        with self.assertRaises(ValueError):
            entry.parse_hhmm("25:00", "x")
        for text in ("2026-10-29 10:15", "2026-10-29T05:15:00Z", "2026-10-29 10:15 PKT", "2026-10-29T10:15:00+05:00"):
            self.assertEqual(entry.now_arg(text), DT(2026, 10, 29, 5, 15), text)
        today = entry.to_pkt(entry.utc_now()).date()
        self.assertEqual(entry.to_pkt(entry.now_arg("10:15")).date(), today)     # a bare time is today

    def test_or_minutes_30(self):
        r = at("2026-10-29 10:49", or_minutes=30)
        self.assertEqual((r["opening_range"]["bars"], r["opening_range"]["high"]), (6, 100.2))
        self.assertEqual(r["trigger"]["time"], "10:15 AM")

    def test_what_each_source_can_do(self):
        with self.assertRaises(entry.EntryError) as cm:
            at("2026-10-29 10:15", interval="1m")                # a tape makes 5-minute bars only
        self.assertIn("1-minute bars need a 1-minute bars file", str(cm.exception))
        with self.assertRaises(entry.EntryError) as cm:
            at("2026-10-29 10:15", src=BARS, interval="1m")
        self.assertIn("has 5-minute bars, not 1m: use --interval 5m", str(cm.exception))
        with self.assertRaises(entry.EntryError) as cm:
            at("2026-10-29 10:15", src=MTAPE, min_volume_x=1)    # the screener tape has no volumes
        self.assertIn("--min-volume-x needs volumes", str(cm.exception))

    def test_a_close_at_the_upper_price_limit_says_so(self):
        chart = entry.load_chart("DEMO", DAY, MARKET, TAPE)
        self.assertEqual((chart["lower"], chart["upper"]), (88.56, 108.24))
        chart["upper"] = 100.65                                  # pretend 100.65 were today's upper limit
        text = "\n".join(entry.describe(entry.evaluate(chart, pkt("2026-10-29 10:15"))))
        self.assertIn("100.65 is today's upper price limit: PSX trades up to it, never above", text)
        self.assertNotIn("upper price limit", "\n".join(entry.describe(at("2026-10-29 10:15"))))

    def test_after_the_cutoff_it_never_says_get_in(self):
        # live data is about 5 minutes late: the 14:55 bar (closes 15:00) shows at about 15:05
        rows = lines_of(capped(self.d))
        rows = [x if not x.startswith("2026-10-29 14:55") else ",".join(x.split(",")[:2] + ["100.50"] + x.split(
            ",")[3:4] + ["100.50", "9000"]) for x in rows]
        write("cut.csv", "\n".join(rows) + "\n")
        r = at("2026-10-29 15:00:30", src="cut.csv")
        self.assertEqual((r["trigger"]["time"], r["past_cutoff"]), ("3:00 PM", False))
        self.assertIn("This is the moment to get in, if you take the trade. Nothing was placed.", entry.describe(r))
        r = at("2026-10-29 15:05", src="cut.csv")
        self.assertEqual((r["status"], r["trigger"]["time"], r["past_cutoff"]), ("triggered", "3:00 PM", True))
        text = "\n".join(entry.describe(r))
        self.assertNotIn("moment to get in", text)
        self.assertIn("The bar closed by 15:00, but it is now 15:05, after 15:00, the last time for a new entry: "
                      "psx-risk will block a new entry. Nothing was placed.", text)
        bar = [b for b in r["_bars"] if b["slot"] == r["trigger"]["bar_index"]][0]
        line = entry.bar_line(bar, r)
        self.assertIn("TRIGGERED: closed above 100.20 (+0.30%). Too late for a new entry: 3:00 PM, 100.50", line)
        self.assertNotIn("Get in now", line)

    def test_expired_with_a_volume_check_says_the_closes_were_thin(self):
        r = at("2026-10-29 15:05", src=None, min_volume_x=1)
        self.assertEqual(r["status"], "expired")
        self.assertIn("no close above the range high 100.20 with enough volume (1.00x) by 15:00", r["reason"])
        r = at("2026-10-29 15:05", src=capped(self.d), min_volume_x=1)   # nothing closed above at all
        self.assertIn("no close above the range high 100.20 by 15:00", r["reason"])

    def test_the_watch_poll_never_reads_a_saved_page(self):
        write(os.path.join("data", "company", "DEMO.html"), "<html>an old saved page</html>")
        asked = []

        def down(url, timeout=30):
            asked.append(url)
            raise entry.urllib.error.URLError("no network")
        entry.http_get = down
        try:
            with self.assertRaises(entry.EntryError) as cm:
                entry.poll_company("DEMO", {"psx_site": True}, pkt("2026-10-29 10:15"))
        finally:
            entry.http_get = _no_network
        self.assertEqual(asked, ["https://dps.psx.com.pk/company/DEMO"])
        self.assertIn("could not reach PSX for https://dps.psx.com.pk/company/DEMO", str(cm.exception))
        self.assertFalse(hasattr(entry, "NO_SAVED_PAGES"))

    def test_no_banned_words_in_the_output(self):
        results = [at(t) for t in ("2026-10-29 09:20", "2026-10-29 09:44", "2026-10-29 10:12", "2026-10-29 10:15",
                                   "2026-10-29 16:05", "2026-10-31 10:00")]
        results += [at("2026-09-18 13:00", src=None), at("2026-10-29 10:15", src=MTAPE)]
        text = "\n".join("\n".join(entry.describe(r)) for r in results).lower()
        for never in ("real " + "time", "guaran" + "tee", "pro" + "fit", "live", "$"):
            self.assertNotIn(never, text)


class PartRange(Temp):
    """A tape that starts late, or has a gap, holds only part of the opening range (9:30 to 9:45)."""

    def tape(self, keep, drop=(), src=TAPE):
        path = os.path.join(self.d, "part.jsonl")
        for s in entry.read_tape(src):
            if keep(s["asof"]):
                entry.append_tape(path, {k: v for k, v in s.items() if k not in drop})
        return path

    def test_a_late_company_tape_takes_the_page_high_and_low(self):
        late = self.tape(lambda t: t >= "2026-10-29 09:41")        # the first page: 9:41, high 100.2, low 98.9
        r = at("2026-10-29 10:15", src=late)
        orr = r["opening_range"]
        self.assertEqual((orr["high"], orr["low"], orr["bars"], orr["of"]), (100.2, 98.9, 1, 3))   # not 100.10, 99.70
        self.assertEqual((r["status"], r["trigger"]["time"], r["level"]), ("triggered", "10:15 AM", 100.2))
        lines = entry.describe(r)
        self.assertEqual(lines[1:3], [
            "Opening range 9:30-9:45: high 100.20, low 98.90 (1 of 3 bars).",
            "The tape started at 9:41, inside the opening range (9:30 to 9:45), so the high and low use its first "
            "page's day high and low."])
        code, _, _ = run(["check", "DEMO", "--data", MARKET, "--bars", late, "--now", "2026-10-29 10:15", "--write"])
        with open(os.path.join("psx", "entry.json")) as fh:      # psx-exit refuses an or-mid stop from it
            self.assertEqual(json.load(fh)["opening_range"], {"high": 100.2, "low": 98.9, "minutes": 15, "bars": 1,
                                                              "of": 3})

    def test_without_the_page_high_and_low_there_is_no_range(self):
        late = self.tape(lambda t: t >= "2026-10-29 09:41", drop=("high", "low"))
        r = at("2026-10-29 10:15", src=late)
        self.assertEqual((r["status"], r["final"], r["trigger"]), ("no-data", True, None))
        self.assertIn("the tape started at 9:41, inside the opening range (9:30 to 9:45), and its first page has no "
                      "day high and low, so the range is not known", r["reason"])
        text = "\n".join(entry.describe(r))
        self.assertNotIn("Opening range", text)                   # the part range is not shown
        self.assertNotIn("TRIGGERED", text)
        lines = []
        clock = entry.SimClock(pkt("2026-10-29 09:50"))
        code, res = entry.watch_loop(lambda now: entry.load_chart("DEMO", DAY, MARKET, late), clock.now, clock.sleep,
                                     lines.append, dict(Watch.OPTS))
        self.assertEqual((code, res["status"]), (1, "no-data"))
        self.assertTrue(lines[-1].startswith("NO DATA  the tape started at 9:41"), lines[-1])
        market = self.tape(lambda t: t >= "2026-10-29 09:41", src=MTAPE)
        r = at("2026-10-29 10:16", src=market)
        self.assertEqual(r["status"], "no-data")
        self.assertIn("and a screener tape has no day high and low", r["reason"])

    def test_a_missing_range_bar_means_no_range(self):
        gap = self.tape(lambda t: not "2026-10-29 09:35" <= t < "2026-10-29 09:40")
        r = at("2026-10-29 10:15", src=gap)
        self.assertEqual((r["status"], r["trigger"]), ("no-data", None))
        self.assertIn("has no 9:35 bar, inside the opening range (9:30 to 9:45), so the range is not known",
                      r["reason"])
        write("gap.csv", "\n".join(x for x in lines_of(BARS) if not x.startswith("2026-10-29 09:3")) + "\n")
        r = at("2026-10-29 10:15", src="gap.csv")
        self.assertEqual(r["status"], "no-data")
        self.assertIn("the bars file gap.csv starts with the 9:40 bar, inside the opening range", r["reason"])
        r = at("2026-10-29 10:15", src="gap.csv", setup="level", level=100.2)    # a level needs no range
        self.assertEqual(r["trigger"]["time"], "10:15 AM")
        write("wick.csv", "\n".join(x for x in lines_of(WICK) if not x.startswith("2026-10-28 09:35")) + "\n")
        r = at("2026-10-28 10:06:30", src="wick.csv", interval="1m")
        self.assertEqual(r["status"], "no-data")
        self.assertIn("has no 9:35 bar, inside the opening range (9:30 to 9:45), so the range is not known and there "
                      "is no range to break; with --interval 5m a minute with no row may not matter", r["reason"])
        r = at("2026-10-28 10:06:30", src="wick.csv")             # as 5-minute bars the range is whole
        self.assertEqual((r["trigger"]["time"], r["opening_range"]["bars"]), ("10:00 AM", 3))


class OneMinute(unittest.TestCase):
    """DEMO-1m-wick.csv: made-up 1-minute bars of Wed 2026-10-28."""

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        r = at("2026-10-28 10:06:30", src=WICK, interval="1m")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10067})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:59 AM", "09:58", 28, 100.62))
        r = at("2026-10-28 09:58:59", src=WICK, interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["last"]["close"], 100.5)              # the 9:57 bar closed AT the high
        wick = [b for b in r["_bars"] if b["slot"] == 22][0]     # the 9:52 bar
        self.assertEqual((wick["high"], wick["close"]), (100.7, 100.3))

    def test_missing_minute_volume_and_the_five_minute_view(self):
        r = at("2026-10-28 10:06:30", src=WICK, interval="1m", min_volume_x=2)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["volume_x"]), ("10:02 AM", 31, 3.08))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                              # 9:49 has no row
        self.assertEqual(slots[-1], 35)                          # the 10:05 bar: the 10:06 row has not ended
        r = at("2026-10-28 10:06:30", src=WICK)                  # the same file as 5-minute bars
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"]), ("10:00 AM", 5, 100.6))


class Calendar(Temp):
    def test_friday_hours_move_the_window(self):
        r = at("2026-10-09 09:40", src=None)
        self.assertEqual((r["session"], r["no_entries_before"], r["no_entries_after"]), ("friday", "09:30", "16:00"))
        self.assertEqual(r["segments"], [["09:15", "12:00"], ["14:30", "16:30"]])
        self.assertEqual((r["opening_range"]["high"], r["opening_range"]["low"]), (91.83, 91.58))   # 9:15 to 9:25
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"]), ("9:35 AM", "09:30", 3))  # 09:45 would block it
        self.assertIn("Friday hours today: 9:15 AM to 12:00 PM and 2:30 PM to 4:30 PM PKT. No new entries before "
                      "9:30 or after 16:00.", entry.describe(r))
        self.assertEqual(len(entry.slot_spans(datetime.date(2026, 10, 9))), 57)

    def test_a_close_when_a_session_ends_is_not_a_trigger(self):
        # the 11:55 bar (bar 32) closes above at 12:00 PM, when the Friday session ends: no order can fill
        r = at("2026-10-02 12:00", src=None)
        self.assertEqual((r["status"], r["phase"], r["trigger"]), ("waiting", "break", None))
        text = "\n".join(entry.describe(r))
        self.assertIn("WAITING  the 11:55 bar closed above at 93.15, but it closed at 12:00 PM, when the session ends: "
                      "an order cannot fill then; the next chance is the 2:30 PM session, at a new price.", text)
        self.assertIn("The market is closed now for the Juma break. Trading starts again at 2:30 PM PKT "
                      "(pre-open from 2:15 PM PKT).", text)
        self.assertNotIn("This is the moment", text)
        r = at("2026-10-02 16:01", src=None)                     # no close above in the 2:30 PM session
        self.assertEqual(r["status"], "expired")
        self.assertIn("; the 11:55 bar closed above, but at 12:00 PM, when the session ended", r["reason"])
        lines = []
        clock = entry.SimClock(pkt("2026-10-02 11:50"))
        entry.watch_loop(lambda now: entry.load_chart("DEMO", datetime.date(2026, 10, 2), MARKET, None), clock.now,
                         clock.sleep, lines.append, dict(Watch.OPTS), until=DT(2026, 10, 2, 14, 40))
        self.assertIn("11:55 bar  closed 93.15  above 93.00, but the session ends at 12:00 PM: an order cannot fill "
                      "then", lines)
        self.assertNotIn("TRIGGERED", "\n".join(lines))
        # a close at the end of the day's last session cannot fill either (here no_entries_after is the close)
        rows = lines_of(capped(self.d))
        rows = [x if not x.startswith("2026-10-29 15:25") else ",".join(x.split(",")[:2] + ["100.50"] + x.split(
            ",")[3:4] + ["100.50", "9000"]) for x in rows]
        write("late.csv", "\n".join(rows) + "\n")
        r = at("2026-10-29 15:31", src="late.csv", cfg=dict(entry.DEFAULTS, no_entries_after="15:30"))
        self.assertEqual((r["status"], r["trigger"]), ("expired", None))
        self.assertIn("the 15:25 bar closed above, but at 3:30 PM, when the session ended", r["reason"])

    def test_a_friday_trigger_in_the_second_session(self):
        r = at("2026-09-18 13:00", src=None)
        self.assertEqual((r["status"], r["phase"]), ("waiting", "break"))
        self.assertIn("Juma break", "\n".join(entry.describe(r)))
        r = at("2026-09-18 15:40", src=None)
        t = r["trigger"]
        self.assertEqual((r["phase"], t["time"], t["bar_start"], t["bar_index"]), ("open", "3:40 PM", "15:35", 46))
        self.assertEqual(entry.slot_spans(datetime.date(2026, 9, 18))[33][0].strftime("%H:%M"), "14:30")

    def test_ramadan_hours(self):
        # the 10-29 bars moved to a Ramadan Monday (2026-02-23: 09:15 to 13:30, 51 bars)
        rows = [x for x in lines_of(BARS) if x.startswith("2026-10-29")][:51]
        start = DT(2026, 2, 23, 9, 15)
        text = "datetime,open,high,low,close,volume\n" + "".join(
            "%s,%s\n" % ((start + datetime.timedelta(minutes=5 * k)).strftime("%Y-%m-%d %H:%M"), x.split(",", 1)[1])
            for k, x in enumerate(rows))
        write("ramadan.csv", text)
        r = at("2026-02-23 10:00", src="ramadan.csv")
        self.assertEqual((r["session"], r["no_entries_before"], r["no_entries_after"], r["closes"]),
                         ("ramadan", "09:30", "13:00", "13:30"))
        self.assertEqual(r["opening_range"]["high"], 100.2)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["bar_start"]), ("10:00 AM", 8, "09:55"))
        self.assertIn("Ramadan hours today: 9:15 AM to 1:30 PM PKT.", "\n".join(entry.describe(r)))

    def test_weekend_holiday_and_next_open(self):
        r = at("2026-10-31 10:30")
        self.assertEqual((r["status"], r["phase"], r["next_open"]), ("no-data", "closed", "2026-11-02 09:30"))
        self.assertIn("weekend", r["reason"])
        self.assertIn("Next open: 2026-11-02 09:30 Karachi time.", entry.describe(r))
        r = at("2026-11-09 10:30")
        self.assertIn("PSX holiday: Iqbal Day", r["reason"])
        self.assertEqual(r["next_open"], "2026-11-10 09:30")
        self.assertEqual(entry.next_open(DAY), "2026-10-30 09:15")   # a Friday opens at 9:15

    def test_the_config_fixes_the_calendar(self):
        r = at("2026-10-29 10:15", cfg=dict(entry.DEFAULTS, holidays={"2026-10-29": "a test closure"}))
        self.assertEqual(r["status"], "no-data")
        self.assertIn("a test closure", r["reason"])
        r = at("2026-11-09 10:30", cfg=dict(entry.DEFAULTS, open_days=["2026-11-09"]))
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))   # open, but no bars for that day
        write("cfg.json", json.dumps({"holidays": {"29-10-2026": "x"}}))
        with self.assertRaises(entry.EntryError):
            entry.read_config("cfg.json")
        write("cfg.json", json.dumps({"ramadan": {"start": "2027-02-09"}}))
        with self.assertRaises(entry.EntryError):
            entry.read_config("cfg.json")

    def test_unknown_year_and_no_daylight_saving(self):
        r = at("2028-03-01 10:30")
        self.assertFalse(r["calendar_known"])
        self.assertIn("The PSX holiday list for 2028 is not in this tool", r["calendar_note"])
        self.assertIn(r["calendar_note"], entry.describe(r))
        for d in ("2026-01-15", "2026-07-15", "2026-12-07"):
            self.assertEqual(entry.now_arg("%s 10:15" % d), DT.strptime(d + " 05:15", "%Y-%m-%d %H:%M"), d)


class Sources(Temp):
    TAPE_REL = os.path.join("tape", "2026-10-29", "DEMO.jsonl")
    MARKET_REL = os.path.join("tape", "2026-10-29", "market.jsonl")
    BARS_REL = os.path.join("bars", "DEMO.csv")

    def data(self, *parts):
        """A temp data folder with some of the sample market's files."""
        root = os.path.join(self.d, "data")
        for p in parts:
            os.makedirs(os.path.dirname(os.path.join(root, p)), exist_ok=True)
            shutil.copy(os.path.join(MARKET, p), os.path.join(root, p))
        return root

    def test_the_first_source_that_has_bars(self):
        seen = []
        for parts, src, want in (((self.MARKET_REL,), None, ("screener-tape", self.MARKET_REL)),
                                 ((self.TAPE_REL,), None, ("company-tape", self.TAPE_REL)),
                                 ((self.BARS_REL,), None, ("bars-file", self.BARS_REL)),
                                 ((), self.TAPE_REL, ("company-tape", self.TAPE_REL))):
            root = self.data(*parts)
            r = at("2026-10-29 10:16", src=os.path.join(root, src) if src else None, data=root)
            self.assertEqual((r["source"], r["source_file"]), want)
            self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("10:15 AM", 8))
            seen.append(entry.describe(r)[0])
        self.assertIn("from the screener tape (%s, prices only)" % self.MARKET_REL, seen[0])
        self.assertIn("from the company tape (%s)" % self.TAPE_REL, seen[1])
        self.assertIn("from the bars file %s, data to 10:15" % self.BARS_REL, seen[2])

    def test_a_source_without_the_day_is_skipped(self):
        root = self.data(self.TAPE_REL)
        old = os.path.join(root, "bars", "DEMO.csv")
        write(old, "\n".join(x for x in lines_of(BARS) if not x.startswith("2026-10-29")) + "\n")
        r = at("2026-10-29 10:15", src=None, data=root)
        self.assertEqual(r["source"], "company-tape")
        r = at("2026-10-29 10:15", src=old, data=root)           # --bars without the day: the next source
        self.assertEqual(r["source"], "company-tape")
        self.assertIn("has no bars for DEMO on 2026-10-29", entry.describe(r)[1])

    def test_no_source_says_what_to_save(self):
        root = os.path.join(self.d, "empty")
        r = at("2026-10-29 10:15", src=None, data=root)
        self.assertEqual(r["status"], "no-data")
        self.assertIn("The PSX site is off for this tool. Export 5-minute bars from your broker and save them as %s, "
                      "or set" % os.path.join(root, "bars", "DEMO.csv"), r["reason"])
        self.assertNotIn("browser", r["reason"])
        r = at("2026-10-29 10:15", src=None, data=root, cfg=dict(entry.DEFAULTS, psx_site=True))
        self.assertIn("run entry.py watch DEMO to record the PSX company page, or export 5-minute bars from your "
                      "broker and save them as", r["reason"])
        code, text, _ = run(["check", "DEMO", "--data", root, "--now", "2026-10-29 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("NO DATA  no bars for DEMO on Thu 2026-10-29", text)

    def test_bad_bars_files(self):
        code, _, err = run(["check", "DEMO", "--bars", "nope.csv", "--now", "2026-10-29 10:15"])
        self.assertIn("no bars file at nope.csv", err)
        write("us.csv", "datetime,open,high,low,close\n10/29/2026 10:15,1,1,1,1\n")
        code, _, err = run(["check", "DEMO", "--bars", "us.csv", "--now", "2026-10-29 10:15"])
        self.assertIn("month first", err)
        write("q.csv", "datetime,open,high,low,close\n2026-10-29 09:30,1,1,1,1\n2026-10-29 09:45,1,1,1,1\n")
        code, _, err = run(["check", "DEMO", "--bars", "q.csv", "--now", "2026-10-29 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("15-minute rows", err)
        self.assertNotIn("Traceback", err)

    def test_without_now_a_bars_file_counts_only_to_the_time_it_was_saved(self):
        path = os.path.join(self.d, "DEMO.csv")
        shutil.copy(BARS, path)
        saved = (entry.pkt_to_utc(DT(2026, 10, 29, 10, 13)) - DT(1970, 1, 1)).total_seconds()
        os.utime(path, (saved, saved))
        now = pkt("2026-10-29 10:16")
        r = entry.evaluate(entry.load_chart("DEMO", DAY, MARKET, path, live=True), now)
        self.assertEqual((r["status"], r["data_time"]), ("waiting", "2026-10-29 10:10:00"))
        self.assertIn("the bars file has nothing newer", r["lag_note"])
        os.utime(path, (saved + 180, saved + 180))                # saved again at 10:16
        r = entry.evaluate(entry.load_chart("DEMO", DAY, MARKET, path, live=True), now)
        self.assertEqual(r["trigger"]["time"], "10:15 AM")


class Watch(Temp):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, start, src=TAPE, calls=None, **kw):
        def provider(now):
            if calls is not None:
                calls.append(entry.to_pkt(now).strftime("%H:%M:%S"))
            return entry.load_chart("DEMO", entry.to_pkt(now).date(), MARKET, src)
        clock = entry.SimClock(pkt(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar(self):
        calls = []
        code, res, lines, fired, clock = self.loop("2026-10-29 10:07", calls=calls)
        self.assertEqual((code, len(fired)), (0, 1))
        self.assertEqual((fired[0][0]["trigger"]["time"], fired[0][0]["trigger"]["price"]), ("10:15 AM", 100.65))
        self.assertEqual(calls, ["10:07:00", "10:10:02", "10:15:02"])    # just after each bar closes
        text = "\n".join(lines)
        self.assertIn("10:05 bar  closed 100.05  0.15 under 100.20", text)
        self.assertIn("10:10 bar  closed 100.65  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop("2026-10-29 09:00")
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "09:00 PKT  not open yet: watching from 9:35 AM PKT.")
        self.assertIn(" 9:40 bar  closed 99.85  opening range set: high 100.20, low 98.90", lines)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 9)   # 9:30 ... 10:10, one line each

    def test_waits_for_the_data_on_the_screener_tape(self):
        calls = []
        code, res, lines, fired, clock = self.loop("2026-10-29 10:12", src=MTAPE, calls=calls, every=60)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["10:12:00", "10:15:02", "10:16:02"])    # at 10:15:02 the data is as of 10:14
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:15 AM")

    def test_a_friday_replay_sleeps_through_the_juma_break(self):
        calls = []
        code, res, lines, fired, clock = self.loop("2026-09-18 11:40", src=None, calls=calls)
        self.assertEqual(code, 0)
        self.assertIn("12:00 PKT  Juma break: the market is closed until 2:30 PM PKT. Watching again from then.", lines)
        self.assertEqual([c for c in calls if "12:00:02" < c < "14:35:02"], [])
        self.assertEqual((res["trigger"]["time"], res["trigger"]["bar_index"]), ("3:40 PM", 46))

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(pkt("2026-10-29 09:46"))
        calls, lines = [], []

        def provider(now):
            calls.append(entry.to_pkt(now).strftime("%H:%M:%S"))
            return entry.load_chart("DEMO", DAY, MARKET, TAPE)
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        self.assertEqual(calls[:2], ["09:46:00", "10:04:30"])   # straight after waking, not a long sleep later
        for start in (" 9:45", " 9:50", " 9:55", "10:00"):
            self.assertIn(start + " bar  closed", "\n".join(lines))
        self.assertEqual(res["trigger"]["time"], "10:15 AM")

    def test_expires_and_until(self):
        path = capped(self.d)
        code, res, lines, fired, clock = self.loop("2026-10-29 14:00", src=path, every=300)
        self.assertEqual((code, res["status"], fired), (1, "expired", []))
        self.assertEqual(entry.to_pkt(clock.now()).strftime("%H:%M"), "15:00")
        self.assertTrue(lines[-1].startswith("EXPIRED  no close above the range high 100.20 by 15:00"), lines[-1])
        code, res, lines, fired, clock = self.loop("2026-10-29 10:30", src=path, until=DT(2026, 10, 29, 11, 0))
        self.assertEqual(code, 1)
        self.assertTrue(lines[-1].startswith("Stopped at --until 11:00 without a trigger. Last close"), lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop("2026-10-31 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])

        def broken(now):
            raise entry.EntryError("PSX answered HTTP 503 for https://dps.psx.com.pk/company/DEMO")
        clock = entry.SimClock(pkt("2026-10-29 10:00"))
        lines = []
        code, _ = entry.watch_loop(broken, clock.now, clock.sleep, lines.append, dict(self.OPTS), live=True)
        self.assertEqual(code, 1)
        self.assertIn("10:00:00 PKT  could not read the company page: PSX answered HTTP 503", lines[0])
        self.assertEqual(lines[-1], "Stopping after 5 failed reads in a row.")
        self.assertEqual(entry.to_pkt(clock.now()).strftime("%H:%M"), "10:04")   # one try a minute


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def page_for(snap):
    """The saved DEMO page with the quote of one tape snapshot: price, As of, open, high, low, volume."""
    with open(os.path.join(MARKET, "company", "DEMO.html"), encoding="utf-8") as fh:
        page = fh.read()
    when = DT.strptime(snap["asof"], "%Y-%m-%d %H:%M")
    asof = "%s, %s %d, %d %s" % (("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[when.weekday()],
                                 MONTHS[when.month - 1], when.day, when.year, entry.clock_label(when))
    page = re.sub(r'(<div class="quote__close">)Rs\.[^<]*', lambda m: m.group(1) + "Rs.%.2f" % snap["price"], page)
    page = re.sub(r"As of [^<]*", lambda m: "As of " + asof, page)
    for label, key in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Volume", "volume")):
        value = "{:,}".format(int(snap[key])) if key == "volume" else "%.2f" % snap[key]
        page = re.sub(r'(<div class="stats_label">%s</div><div class="stats_value">)[^<]*' % label,
                      lambda m: m.group(1) + value, page, count=1)
    return page.encode("utf-8")


def tape_from_bars(bars):
    """Company snapshots from 5-minute bars: as of each bar's minutes open, high, low, middle
    and close, fetched 5 minutes 7 seconds later, with the day's open, high, low and volume."""
    out, hi, lo, vol = [], None, None, 0
    for b in bars:
        prices = [b["open"], b["high"], b["low"], round((b["high"] + b["low"]) / 2, 2), b["close"]]
        for j, p in enumerate(prices):
            when = b["start"] + datetime.timedelta(minutes=j)
            hi, lo = max(hi or p, p), min(lo or p, p)
            out.append({"asof": when.strftime("%Y-%m-%d %H:%M"), "source": "company", "symbol": "DEMO", "price": p,
                        "open": bars[0]["open"], "high": hi, "low": lo, "volume": vol + b["volume"] * (j + 1) // 5,
                        "ldcp": 98.4, "lower": 88.56, "upper": 108.24,
                        "fetched": entry.pkt_iso(entry.pkt_to_utc(when + datetime.timedelta(minutes=5, seconds=7)))})
        vol += b["volume"]
    return out


class FakeSite(object):
    """The PSX site and the clock as a polling watch sees them: at the fake time, the page of
    the newest snapshot fetched by then (the page of the day before until the first one)."""

    def __init__(self, snaps, start, before):
        self.snaps = sorted(snaps, key=lambda s: s["fetched"])
        self.now = pkt(start)
        self.before = before
        self.polls = []

    def utc_now(self):
        return self.now

    def sleep(self, seconds):
        self.now += datetime.timedelta(seconds=seconds)

    def get(self, url, timeout=30):
        assert url == "https://dps.psx.com.pk/company/DEMO", url
        self.polls.append(entry.to_pkt(self.now))
        seen = [s for s in self.snaps if s["fetched"] <= entry.pkt_iso(self.now)]
        return page_for(seen[-1] if seen else self.before)


class LiveWatch(Temp):
    """watch with no --now and "psx_site": true polls the (fake) PSX site and records a tape."""

    YESTERDAY = {"asof": "2026-10-28 15:49", "price": 98.4, "open": 96.0, "high": 98.46, "low": 95.76, "volume": 3787500}

    def go(self, site, *extra):
        write("cfg.json", json.dumps({"psx_site": True}))
        entry.utc_now, entry.SLEEP, entry.http_get = site.utc_now, site.sleep, site.get
        try:
            return run(["watch", "DEMO", "--data", "data", "--config", "cfg.json"] + list(extra))
        finally:
            entry.utc_now, entry.SLEEP, entry.http_get = _SAVED["utc_now"], _no_network, _no_network

    def test_it_records_the_company_page_and_triggers(self):
        site = FakeSite(entry.read_tape(TAPE), "2026-10-29 09:20", self.YESTERDAY)
        code, text, err = self.go(site)
        self.assertEqual(code, 0, err)
        out = text.splitlines()
        self.assertEqual(out[0], "09:20 PKT  not open yet: polling the PSX company page every 60 seconds from "
                                 "9:30 AM PKT.")
        self.assertIn("10:10 bar  closed 100.65  TRIGGERED: closed above 100.20 (+0.45%). Get in now if you take it: "
                      "10:15 AM, 100.65", out)
        self.assertIn("This is the moment to get in, if you take the trade. Nothing was placed.", out)
        self.assertIn("Free PSX data is about 5 minutes late: check the price in your broker app before you buy.", out)
        polls = [p.strftime("%H:%M:%S") for p in site.polls]
        self.assertEqual((polls[0], polls[-1], len(polls)), ("09:30:00", "10:21:00", 52))   # one a minute
        tape = entry.read_tape(os.path.join("data", "tape", "2026-10-29", "DEMO.jsonl"))
        self.assertEqual(len(tape), 52)
        self.assertEqual((tape[-1]["asof"], tape[-1]["fetched"], tape[-1]["price"]),
                         ("2026-10-29 10:15", "2026-10-29T10:21:00+05:00", 100.65))
        with open(os.path.join("psx", "entry.json")) as fh:
            e = json.load(fh)
        self.assertEqual((e["bar_index"], e["time"], e["price"], e["source"], e["checked_at"]),
                         (8, "10:15 AM", 100.65, "company-tape", "2026-10-29T05:21:00Z"))

    def test_it_sleeps_through_the_juma_break(self):
        csv_bars = entry.read_bars_csv("\n".join(lines_of(BARS)))[datetime.date(2026, 9, 18)]
        site = FakeSite(tape_from_bars(csv_bars), "2026-09-18 09:10", dict(self.YESTERDAY, asof="2026-09-17 15:49"))
        code, text, err = self.go(site)
        self.assertEqual(code, 0, err)
        polls = [p.strftime("%H:%M") for p in site.polls]
        self.assertEqual([p for p in polls if "12:06" < p < "14:30"], [])     # no poll in the break
        self.assertIn("14:30", polls)
        self.assertIn("12:06 PKT  Juma break: the market is closed until 2:30 PM PKT. Watching again from then.",
                      text.splitlines())
        self.assertIn("15:35 bar  closed 91.05  TRIGGERED", text)
        self.assertEqual(polls[-1], "15:46")

    def test_it_needs_the_site_and_a_slow_poll(self):
        write("off.json", "{}")
        code, _, err = run(["watch", "DEMO", "--data", "data", "--config", "off.json"])
        self.assertEqual(code, 1)
        self.assertIn("watch needs the PSX site", err)
        self.assertIn(entry.NO_BARS % os.path.join("data", "bars", "DEMO.csv"), err)
        write("on.json", json.dumps({"psx_site": True}))
        code, _, err = run(["watch", "DEMO", "--data", "data", "--config", "on.json", "--every", "10"])
        self.assertIn("--every must be 30 seconds or more", err)
        code, _, err = run(["watch", "DEMO", "--data", "data", "--config", "on.json", "--bars", BARS])
        self.assertIn("--bars is for check and for replays", err)
        self.assertFalse(os.path.exists("data"))                   # nothing was fetched or written

    def test_a_page_from_the_future_means_a_wrong_clock(self):
        site = FakeSite([], "2026-10-28 09:25", self.YESTERDAY)   # the page says 10-28 15:49 at 09:30
        code, text, err = self.go(site)
        self.assertEqual((code, len(site.polls)), (1, 5))
        self.assertIn("later than this computer's clock (09:30 PKT): check the clock", text)
        self.assertIn("Stopping after 5 failed reads in a row.", text)

    def test_no_trade_today_stops_once_the_range_is_over(self):
        site = FakeSite([], "2026-11-02 09:25", self.YESTERDAY)   # the page still shows Wed 10-28
        code, text, err = self.go(site)
        self.assertEqual(code, 1)
        self.assertIn("the page shows no trade today (its newest As of time is 2026-10-28 15:49)", text)
        self.assertEqual(site.polls[-1].strftime("%H:%M"), "09:51")      # the range ends at 9:45, the data at 9:51


class Cli(Temp):
    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "DEMO", "--data", MARKET, "--bars", TAPE, "--now", "2026-10-29 10:15", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 10:15 AM at 100.65", text)
        self.assertIn("/psx-exit", text)
        with open(os.path.join("psx", "entry.json")) as fh:
            e = json.load(fh)
        want = {"created_by": "psx-entry", "symbol": "DEMO", "setup": "orb", "side": "buy", "date": "2026-10-29",
                "status": "triggered", "time": "10:15 AM", "bar_start": "10:10", "bar_index": 8, "price": 100.65,
                "level": 100.2, "interval": "5m", "session": "normal",
                "opening_range": {"high": 100.2, "low": 98.9, "minutes": 15, "bars": 3, "of": 3},
                "source": "company-tape",
                "source_file": os.path.join("tape", "2026-10-29", "DEMO.jsonl"), "checked_at": "2026-10-29T05:15:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "DEMO", "--data", MARKET, "--now", "2026-10-29 10:12", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual((r["status"], r["source"]), ("waiting", "bars-file"))
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "DEMO", "--data", MARKET, "--bars", TAPE, "--now", "2026-10-29 10:12",
                             "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)
        self.assertIn("Wrote e.json (status waiting).", text)

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "DEMO", "--data", MARKET, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)
        _, _, err = run(["check", "DEMO", "--data", MARKET, "--level", "300"])
        self.assertIn("--level is for --setup level", err)
        _, _, err = run(["check", "DEMO", "--data", MARKET, "--setup", "level", "--now", "2026-10-29 10:15"])
        self.assertIn("needs --level", err)
        _, _, err = run(["check", "DEMO", "--data", MARKET, "--min-volume-x", "0"])
        self.assertIn("--min-volume-x must be above 0", err)
        _, _, err = run(["watch", "DEMO", "--data", MARKET, "--now", "2026-10-29 10:15", "--until", "25:00"])
        self.assertIn("--until wants a Karachi time", err)

    def test_config_is_read(self):
        cfg = os.path.join("psx", "config.json")
        write(cfg, json.dumps({"no_entries_after": "10:10"}))
        code, text, _ = run(["check", "DEMO", "--data", MARKET, "--now", "2026-10-29 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        for bad, why in (("{nope", "not valid JSON"), ('{"no_entries_before": "9.45"}', "no_entries_before wants a "
                         "Karachi time"), ('{"data_delay_minutes": "five"}', "data_delay_minutes")):
            write(cfg, bad)
            code, _, err = run(["check", "DEMO", "--data", MARKET])
            self.assertEqual(code, 1)
            self.assertIn(why, err)

    def test_watch_replays_and_writes_the_entry(self):
        code, text, _ = run(["watch", "DEMO", "--data", MARKET, "--now", "2026-10-29 09:50"])
        self.assertEqual(code, 0)
        self.assertIn("10:10 bar  closed 100.65  TRIGGERED", text)
        with open(os.path.join("psx", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 8)
        code, text, _ = run(["watch", "DEMO", "--data", MARKET, "--now", "2026-10-29 09:50", "--until", "10:00",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        path = os.path.join("psx", "watchlist.json")
        write(path, json.dumps({"created_by": "psx-scanner", "mode": "daily", "hits": [
            {"symbol": "DEMO", "setup": "breakout_20d", "level": 96.56, "name": "Demo Industries Limited", "sector": "0818"},
            {"symbol": "ZZZZ", "setup": "oversold", "level": None}, {"symbol": "LUCK", "setup": "breakout_20d",
                                                                    "level": 461.8}]}))
        code, text, _ = run(["watchlist", "--data", MARKET, "--now", "2026-10-29 10:16"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("DEMO      triggered  10:15 AM  100.65    100.20"), rows[1])
        self.assertTrue(rows[1].endswith("bars file"), rows[1])
        self.assertTrue(rows[2].startswith("ZZZZ      no-data"), rows[2])
        self.assertTrue(rows[3].startswith("LUCK") and rows[3].endswith("screener tape"), rows[3])
        self.assertIn("No bars for ZZZZ. The PSX site is off for this tool. Export 5-minute bars from your broker and "
                      "save them as %s, or set" % os.path.join(MARKET, "bars", "<SYMBOL>.csv"), text)
        self.assertNotIn("browser", text)                         # bars come from a broker terminal
        self.assertIn("1 of 3 triggered", text)
        code, text, _ = run(["watchlist", "--data", MARKET, "--now", "2026-10-29 10:16", "--setup", "level", "--json"])
        got = dict((x["symbol"], x) for x in json.loads(text))
        self.assertEqual(got["DEMO"]["result"]["trigger"]["time"], "9:45 AM")    # level 96.56 from the hit
        self.assertIn("needs a level", got["ZZZZ"]["error"])
        write(path, json.dumps(["DEMO", {"symbol": "demo"}]))                   # a bare list, DEMO once
        code, text, _ = run(["watchlist", "--data", MARKET, "--now", "2026-10-29 10:12"])
        self.assertEqual(code, 1)
        self.assertIn("0 of 1 triggered", text)
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /psx-scanner first", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "psx-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: psx-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], SAFETY)
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "about 5 minutes late", "day trading loses most people money", "/psx-exit",
                     "longs only", "in the background", "/loop", "nothing was placed", "10:15 am", "100.65", "bar 8",
                     "psx_site", "juma break", "config_time"):
            self.assertIn(must, low)
        for never in (chr(0x2014), "tick " + "by tick", "real " + "time", "pro" + "fit", "guaran" + "tee"):
            self.assertNotIn(never, low)
        self.assertIsNone(re.search(r"\$\s?\d", text))              # no dollar amounts
        for must in ('"watch ogdc"', "--bars psx/data/bars/demo-1m.csv", "only one tool that polls the psx site at a time",
                     "level: the first closed bar that ends at or after `no_entries_before`", "when a session ends",
                     "a company tape that started inside the range", '"bars": 3, "of": 3', "psx-risk will block"):
            self.assertIn(must, low)
        self.assertNotIn("DEMO", text.split("\n---\n")[0])        # real tickers in the frontmatter's phrases

    def test_the_example_blocks_are_the_real_output(self):
        with open(os.path.join(ROOT, "psx-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        blocks = re.findall(r"```\n\$ (entry\.py .*?)\n(.*?)```", text[text.index("## Example"):], re.S)
        self.assertEqual(len(blocks), 2)
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            for cmd, want in blocks:
                code, out, err = run(shlex.split(cmd)[1:])
                self.assertEqual(out, want, cmd)
        finally:
            os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
