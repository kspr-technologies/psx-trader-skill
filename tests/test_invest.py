"""psx-invest (SPEC v2 section 3): long-term signals on a made-up market (tests/fixtures/psx-invest).

The market is written once per test run into a temp folder (make.py), so its 500 closing files
never ship. Every test works in its own temp folder. Nothing touches the network: the PSX site is
off, --offline or --now is used, and the site tests swap http_get (and SLEEP) for fakes.
Speed: each run reads 500 closing files. A file that is there is read once and kept by its path,
size and time (the tool's own daily_rows still reads it the first time), so the module stays quick.
"""

import datetime
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal, ROUND_CEILING

from _load import FIX, ROOT, load
from _words import check_words

invest = load("psx-invest", "invest")
HERE = os.path.join(FIX, "psx-invest")
CONTRACT = os.path.join(FIX, "psx-v2", "contract")
D = datetime.date
NOW = "2026-10-30 17:00"            # the evening of the newest session (a month-end)
TMP = MARKET = MAKE = None


def load_make():
    spec = importlib.util.spec_from_file_location("psx_invest_make", os.path.join(HERE, "make.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REAL_DAILY_ROWS, SEEN = invest.daily_rows, {}


def kept_daily_rows(d, root, cfg=None, offline=False):
    """invest.daily_rows, but a file that is there is parsed once (kept by path, size and time)."""
    path = invest.daily_path(root, d)
    if not os.path.exists(path):
        return REAL_DAILY_ROWS(d, root, cfg, offline)          # a fetch (fake site) or None
    st = os.stat(path)
    key = (os.path.realpath(path), st.st_size, st.st_mtime_ns)
    if key not in SEEN:
        SEEN[key] = REAL_DAILY_ROWS(d, root, cfg, offline)
    return SEEN[key]


def setUpModule():
    global TMP, MARKET, MAKE
    TMP = tempfile.mkdtemp(prefix="psx-invest-")
    MARKET = os.path.join(TMP, "market")
    MAKE = load_make()
    MAKE.write_market(MARKET)
    invest.daily_rows = kept_daily_rows


def tearDownModule():
    invest.daily_rows = REAL_DAILY_ROWS
    SEEN.clear()
    shutil.rmtree(TMP, ignore_errors=True)


def tree(root):
    """{path: sha256} of every file under root (to see what a run wrote)."""
    out = {}
    for base, _, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


class Folder(object):
    """A made-up long-term folder: psx/ with the fixture files. The data folder is the shared
    market (read only), or a copy of it (copy=True) for a test that changes files."""

    def __init__(self, copy=False):
        self.root = tempfile.mkdtemp(prefix="lt-", dir=TMP)
        os.makedirs(os.path.join(self.root, "psx"))
        for name in ("config.json", "invest.json", "portfolio.json", "metrics.json"):
            shutil.copy(os.path.join(HERE, name), self.path(name))
        self.data = MARKET
        if copy:
            self.data = self.path("data")
            shutil.copytree(MARKET, self.data)

    def path(self, *parts):
        return os.path.join(self.root, "psx", *parts)

    def read(self, name):
        with open(self.path(name), encoding="utf-8") as fh:
            return json.load(fh)

    def write(self, name, doc):
        os.makedirs(os.path.dirname(self.path(name)), exist_ok=True)
        with open(self.path(name), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)

    def edit(self, name, **changes):
        doc = self.read(name)
        doc.update(changes)
        self.write(name, doc)
        return doc

    def shares(self, sym, n):
        """Set a holding's shares in psx/portfolio.json (n = 0 takes it out)."""
        doc = self.read("portfolio.json")
        doc["positions"] = [dict(p, shares=n) if p["ticker"] == sym else p for p in doc["positions"]]
        doc["positions"] = [p for p in doc["positions"] if p["shares"]]
        self.write("portfolio.json", doc)

    def run(self, *args):
        """(exit code, stdout, stderr) of invest.py in this folder."""
        out, err, here = io.StringIO(), io.StringIO(), os.getcwd()
        os.chdir(self.root)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = invest.main(list(args) + ["--data", self.data])
        finally:
            os.chdir(here)
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args):
        code, out, err = self.run(*args)
        if code != 0:
            raise AssertionError("invest.py %s exited %s:\n%s%s" % (" ".join(args), code, out, err))
        return out

    def rows(self, out):
        """{symbol: (signal, weight text, summary)} from the summary rows of a signals report."""
        got = {}
        for line in out.splitlines():
            m = re.match(r"^(ADD|HOLD|TRIM|REVIEW) +(\S+) +(\d+\.\d)% +(.*)$", line)
            if m:
                got[m.group(2)] = (m.group(1), m.group(3), m.group(4))
        return got

    def signals(self, *more):
        return self.rows(self.ok("signals", "--now", NOW, *more))


def market(today=D(2026, 10, 30), cfg=None, no_session=(), root=None, sessions=500):
    rules = dict(invest.RULES, sessions=sessions)
    return invest.read_market(root or MARKET, today, cfg or {}, rules, list(no_session))


def series(sym, mk):
    """bars, split_map, adjusted closes and events of one share in a market."""
    bars = invest.daily_bars(sym, mk["sessions"])
    prev = invest.split_map(bars, mk["prev_of"])
    return bars, prev, invest.adjusted_closes(bars, prev, mk["missing"]), invest.share_events(bars, prev, mk["missing"])


def bar(d, close, ldcp=None, flags=()):
    return {"date": d, "open": close, "high": close, "low": close, "close": close, "volume": 1000,
            "ldcp": close if ldcp is None else ldcp, "flags": list(flags)}


class Market(unittest.TestCase):
    """The made-up market: made the same way every time, and each share shows its story."""

    def test_the_market_is_made_the_same_way_every_time(self):
        other = os.path.join(TMP, "market-again")
        MAKE.write_market(other)
        self.assertEqual(tree(other), tree(MARKET))
        names = sorted(os.listdir(os.path.join(MARKET, "mkt_summary")))
        self.assertEqual((len(names), names[0], names[-1]), (500, "2024-11-14.Z", "2026-10-30.Z"))
        self.assertNotIn("2026-09-14.Z", names)
        shutil.rmtree(other)

    def test_futures_are_left_out_and_right_letters_kept(self):
        mk = market()
        newest = mk["sessions"][-1][1]
        self.assertNotIn("ZUP-NOV", newest)                        # a stock future (sector 40)
        self.assertIn("ZRITR", newest)                             # the right letter stays: rule 10 looks for it
        self.assertEqual(invest.symbol_kind("ZRITR", "0808", newest["ZRITR"]["short_name"]), "right")
        self.assertEqual(len(mk["dates"]), 500)
        self.assertEqual(mk["missing"], [D(2026, 9, 14)])


class Adjust(unittest.TestCase):
    """3.3: closes are scaled for payouts and splits, by psx-invest's own split_map."""

    @classmethod
    def setUpClass(cls):
        cls.mk = market()

    def test_an_xd_payout_is_counted(self):
        bars, prev, adj, _ = series("ZUP", self.mk)
        i = [b["date"] for b in bars].index(D(2026, 8, 10))
        factor = bars[i]["ldcp"] / bars[i - 1]["close"]
        self.assertAlmostEqual(adj[i - 1] / bars[i - 1]["close"], factor, places=9)
        self.assertAlmostEqual(adj[i] / bars[i]["close"], 1.0, places=9)
        self.assertEqual(invest.payout_facts(bars, self.mk["missing"]),
                         [{"date": D(2026, 8, 10), "amount": 14.5, "flags": ["XD"]}])

    def test_an_xb_bonus_scales_by_its_factor(self):
        bars, _, adj, events = series("ZBON", self.mk)
        self.assertEqual([(e["date"], e["kind"]) for e in events], [(D(2026, 10, 12), "XB")])
        self.assertAlmostEqual(events[0]["factor"], 0.8, places=3)
        i = [b["date"] for b in bars].index(D(2026, 10, 12))
        self.assertAlmostEqual(adj[i - 1] / bars[i - 1]["close"], events[0]["factor"], places=9)

    def test_a_no_flag_split_after_days_with_no_trade_is_scaled(self):
        bars, prev, adj, events = series("ZSPL", self.mk)
        dates = [b["date"] for b in bars]
        self.assertNotIn(D(2026, 6, 30), dates)                     # 3 sessions with no trade
        self.assertEqual(prev[D(2026, 7, 2)], D(2026, 6, 24))       # the bar before: a split (50%)
        self.assertEqual([(e["date"], e["kind"]) for e in events], [(D(2026, 7, 2), "split")])
        i = dates.index(D(2026, 7, 2))
        self.assertAlmostEqual(adj[i - 1] / bars[i - 1]["close"], 0.5, places=3)
        # the scanner's map (the session right before) would not scale it: a false break
        raw = invest.adjusted_closes(bars, self.mk["prev_of"], self.mk["missing"])
        votes = invest.vote_history(invest.month_closes(bars, raw, self.mk["ends"]), invest.RULES)
        self.assertEqual(invest.state_now(invest.break_states(votes))[0], "out")
        votes = invest.vote_history(invest.month_closes(bars, adj, self.mk["ends"]), invest.RULES)
        self.assertEqual(invest.state_now(invest.break_states(votes))[0], "in")

    def test_a_small_move_after_days_with_no_trade_is_not_scaled(self):
        bars, prev, adj, events = series("ZTHN", self.mk)
        self.assertEqual(prev[D(2026, 8, 24)], D(2026, 8, 21))     # the session right before: not scaled
        self.assertEqual(adj, [float(b["close"]) for b in bars])   # nothing at all is scaled for ZTHN
        self.assertEqual(events, [])
        self.assertIn("On Mon 2026-08-24 PSX moved the reference price by +0.95% after days with no trade. It is "
                      "not a payout or a split, so nothing is scaled.",
                      invest.scale_lines(bars, prev, self.mk["missing"]))

    def test_split_map_cases(self):
        d = [D(2026, 3, 2), D(2026, 3, 3), D(2026, 3, 4), D(2026, 3, 5), D(2026, 3, 6)]
        prev_of = dict((d[i], d[i - 1]) for i in range(1, len(d)))
        bars = [bar(d[0], 100.0), bar(d[1], 101.0), bar(d[4], 101.0, 102.01)]      # +1% after 2 quiet days
        self.assertEqual(invest.split_map(bars, prev_of), {d[1]: d[0], d[4]: d[3]})
        bars = [bar(d[0], 100.0), bar(d[3], 50.0, 50.0)]                            # a 2-for-1 split
        self.assertEqual(invest.split_map(bars, prev_of), {d[3]: d[0]})
        bars = [bar(d[0], 100.0), bar(d[3], 91.0, 91.0)]                            # 9%: not a split
        self.assertEqual(invest.split_map(bars, prev_of), {d[3]: d[2]})
        flagged = [bar(d[0], 100.0), bar(d[3], 97.0, 97.0, ["XD"])]                 # a flag is always scaled
        adj = invest.adjusted_closes(flagged, invest.split_map(flagged, prev_of), [])
        self.assertAlmostEqual(adj[0], 97.0)

    def test_never_scaled_across_a_missing_file(self):
        bars, _, adj, events = series("ZGAP", self.mk)
        i = [b["date"] for b in bars].index(D(2026, 9, 15))
        self.assertAlmostEqual(bars[i]["ldcp"] / bars[i - 1]["close"], 1.06, places=2)
        self.assertEqual(adj[:i], [float(b["close"]) for b in bars[:i]])          # the jump is not scaled away
        self.assertEqual(events, [])

    def test_rows_with_no_trade_are_skipped(self):
        bars, _, _, _ = series("ZSTL", self.mk)
        self.assertEqual(bars[-1]["date"], D(2026, 10, 2))
        self.assertEqual(len([d for d in self.mk["dates"] if d > bars[-1]["date"]]), 20)

    def test_recorded_no_session_days_are_not_missing(self):
        f = Folder(copy=True)
        os.remove(os.path.join(f.data, "mkt_summary", "2025-06-10.Z"))
        self.assertEqual(market(root=f.data)["missing"], [D(2025, 6, 10), D(2026, 9, 14)])
        self.assertEqual(market(root=f.data, no_session=["2025-06-10"])["missing"], [D(2026, 9, 14)])
        f.write("state/invest.json", {"no_session": ["2025-06-10"]})
        out = f.ok("signals", "--now", NOW)
        self.assertIn("Note: no closing file for 1 PSX session by the calendar (Mon 2026-09-14).", out)

    def test_a_payout_with_a_bonus_the_same_day_is_not_known(self):
        d = [D(2026, 3, 2), D(2026, 3, 3)]
        bars = [bar(d[0], 100.0), bar(d[1], 80.0, 78.0, ["XD", "XB"])]
        self.assertEqual(invest.payout_facts(bars), [{"date": d[1], "amount": None, "flags": ["XD", "XB"]}])
        f = {"payouts": invest.payout_facts(bars), "events": []}
        self.assertEqual(invest.payout_line(f, d[1]), "Payouts seen: an amount not known on Tue 2026-03-03 "
                                                      "(XDXB). In the last 12 months: Rs 0.00 a share, and 1 "
                                                      "payout not known.")


def mini_market(out, sym, rate, first, last):
    """Closing files with one made-up share (sector 0818): its price moves by rate(year, month) each
    month, in equal steps. The same files every time."""
    days, d = [], first
    while d <= last:
        if invest.trading_day(d)[0]:
            days.append(d)
        d += datetime.timedelta(days=1)
    sizes, rows, px, prev = MAKE.month_sizes(days), {}, 100.0, 100.0
    for d in days:
        px *= (1 + rate(d.year, d.month)) ** (1.0 / sizes[(d.year, d.month)])
        rows[d] = dict(MAKE.bar(prev, MAKE.tick(px), 100000), short="Zback Limited", sector="0818")
        prev = rows[d]["close"]
    MAKE.write_closing(out, {sym: rows}, days)


def long_market(out, shares, first, last):
    """Closing files with a few made-up shares. shares: {symbol: (sector, first price, rate, listed or
    None)}; each price moves by rate(year, month) each month, in equal steps. The same files every time."""
    days, d = [], first
    while d <= last:
        if invest.trading_day(d)[0]:
            days.append(d)
        d += datetime.timedelta(days=1)
    sizes, market = MAKE.month_sizes(days), {}
    for sym, (sector, price, rate, listed) in shares.items():
        rows, px, prev = {}, price, price
        for d in days:
            if listed and d < listed:
                continue
            px *= (1 + rate(d.year, d.month)) ** (1.0 / sizes[(d.year, d.month)])
            rows[d] = dict(MAKE.bar(prev, MAKE.tick(px), 100000), short=sym.title() + " Limited", sector=sector)
            prev = rows[d]["close"]
        market[sym] = rows
    MAKE.write_closing(out, market, days)


def month_end_days(first, last):
    """The last PSX session of each month from first to last (the calendar)."""
    out, d = [], first.replace(day=1)
    while d <= last:
        e = invest.last_session_of_month(d.year, d.month)
        if first <= e <= last:
            out.append(e)
        d = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return out


class MonthEnds(unittest.TestCase):
    """3.2 words: a month-end close is the close of the last session of a month that has a file."""

    def test_the_last_file_of_each_month(self):
        ends = invest.month_ends(market()["dates"])
        self.assertEqual((len(ends), ends[0], ends[-1]), (24, D(2024, 11, 29), D(2026, 10, 30)))
        self.assertIn(D(2026, 7, 31), ends)                          # a Friday
        self.assertIn(D(2026, 2, 27), ends)                          # a Friday in Ramadan 2026
        self.assertIn(D(2026, 9, 30), ends)                          # the month with a missing file

    def test_a_month_in_progress_waits(self):
        mk = market(today=D(2026, 10, 29))
        self.assertEqual((mk["newest"], mk["month_end"], mk["next_end"], mk["at_month_end"]),
                         (D(2026, 10, 29), D(2026, 9, 30), D(2026, 10, 30), False))
        mk = market()
        self.assertEqual((mk["month_end"], mk["next_end"], mk["at_month_end"]), (D(2026, 10, 30), D(2026, 11, 30), True))

    def test_a_holiday_at_a_month_end(self):
        dates = [d for d in market()["dates"] if d <= D(2026, 9, 29)]
        self.assertEqual(invest.month_ends(dates)[-1], D(2026, 8, 31))                  # September waits
        cfg = {"holidays": {"2026-09-30": "made up"}}
        self.assertEqual(invest.month_ends(dates, cfg)[-1], D(2026, 9, 29))             # its last session is in
        self.assertEqual(invest.last_session_of_month(2026, 9, cfg), D(2026, 9, 29))
        self.assertEqual(invest.last_session_of_month(2026, 2), D(2026, 2, 27))         # Ramadan, a Friday

    def test_the_report_names_the_month_end_check(self):
        f = Folder()
        out = f.ok("signals", "--now", "2026-10-29 17:00")
        self.assertIn("Month-end check: Wed 2026-09-30. Next one: Fri 2026-10-30.", out.splitlines()[1])
        out = f.ok("signals", "--now", NOW)
        self.assertEqual(out.splitlines()[:3], [
            "Long-term signals, Fri 2026-10-30 5:00 PM PKT. Closes to Fri 2026-10-30 (500 closing files from "
            "2024-11-14).",
            "Month-end check: Fri 2026-10-30. Next one: Mon 2026-11-30.",
            "Your long-term money: Rs 944,559.90 in 12 shares and Rs 50,000.00 cash."])


class Votes(unittest.TestCase):
    """Rules 1 and 2: up at +2% or more, down at -5% or less, else no vote. Exact at the edges."""

    def test_the_trend_vote_at_its_edges(self):
        self.assertEqual(invest.trend_vote([100.0] * 8 + [98.0, 102.0]), (100.0, 2.0, "up"))
        self.assertEqual(invest.trend_vote([100.0] * 8 + [98.0, 101.99])[1:], (1.99, "none"))
        self.assertEqual(invest.trend_vote([101.0] * 5 + [100.0] * 4 + [95.0]), (100.0, -5.0, "down"))
        self.assertEqual(invest.trend_vote([101.0] * 5 + [100.0] * 4 + [95.01])[1:], (-4.99, "none"))
        self.assertEqual(invest.trend_vote([100.0] * 9), (None, None, None))           # 10 month-ends needed

    def test_the_12_month_vote_at_its_edges(self):
        self.assertEqual(invest.momentum_vote([100.0] + [90.0] * 11 + [102.0]), (2.0, "up"))
        self.assertEqual(invest.momentum_vote([100.0] + [90.0] * 11 + [101.99]), (1.99, "none"))
        self.assertEqual(invest.momentum_vote([100.0] + [90.0] * 11 + [95.0]), (-5.0, "down"))
        self.assertEqual(invest.momentum_vote([100.0] + [90.0] * 11 + [95.01]), (-4.99, "none"))
        self.assertEqual(invest.momentum_vote([100.0] * 12), (None, None))             # 13 month-ends needed

    def test_payouts_are_counted(self):
        mk = market()
        bars, _, adj, _ = series("ZUP", mk)
        raw = invest.momentum_vote([c for _, c in invest.month_closes(bars, [float(b["close"]) for b in bars],
                                                                        mk["ends"])])[0]
        counted = invest.momentum_vote([c for _, c in invest.month_closes(bars, adj, mk["ends"])])[0]
        self.assertEqual((raw, counted), (12.47, 19.48))            # the Rs 14.50 payout is not a fall
        d = [D(2025, m, 28) for m in range(1, 13)] + [D(2026, 1, 28)]
        flat = [bar(x, 100.0) for x in d[:6]] + [bar(d[6], 99.5, 97.0, ["XD"])] + [bar(x, 99.5) for x in d[7:]]
        adj = invest.adjusted_closes(flat)
        self.assertEqual(invest.momentum_vote([b["close"] for b in flat]), (-0.5, "none"))
        self.assertEqual(invest.momentum_vote(adj), (2.58, "up"))   # 99.50 against 100 x 0.97

    def test_the_words_of_each_vote(self):
        out = Folder().ok("explain", "ZUP", "--now", NOW)
        self.assertIn("Rule 1. Trend: 6.8% over its 10-month line. The rule wants 2% or more. Vote: up.", out)
        self.assertIn("Rule 2. 12-month change: +19.5%, payouts counted. Vote: up.", out)
        out = Folder().ok("explain", "ZOLD", "--now", NOW)
        self.assertIn("Rule 1. Trend: 14.1% under its 10-month line. The rule says 5% or more under. Vote: down.", out)
        self.assertIn("Rule 2. 12-month change: -17.2%, payouts counted. Vote: down.", out)
        out = Folder().ok("explain", "ZSPL", "--now", NOW)
        self.assertIn("Rule 1. Trend: -0.3% from its 10-month line. Too close for a vote.", out)
        self.assertIn("Rule 2. 12-month change: 0.0%, payouts counted. Too small for a vote.", out)
        out = Folder().ok("explain", "ZNEW", "--now", NOW)
        self.assertIn("Rule 1. Trend: not known yet. It needs 10 month-ends. ZNEW has 6 in the closing files.", out)
        f = Folder(copy=True)
        for name in os.listdir(os.path.join(f.data, "mkt_summary")):
            if name < "2026-08":
                os.remove(os.path.join(f.data, "mkt_summary", name))
        out = f.ok("explain", "ZUP", "--now", NOW)
        self.assertIn("Rule 1. Trend: not known yet. It needs 10 month-ends. The folder has 3. Run backfill.", out)
        self.assertIn("Rule 2. 12-month change: not known yet. It needs 13 month-ends. The folder has 3. Run "
                      "backfill.", out)

    def test_a_percent_never_shows_an_edge_it_does_not_reach(self):
        self.assertEqual(invest.pct_text(1.99, True, (2, 5)), "+1.99%")
        self.assertEqual(invest.pct_text(2.0, True, (2, 5)), "+2.0%")
        self.assertEqual(invest.pct_text(-4.96, True, (2, 5)), "-4.96%")
        self.assertEqual(invest.pct_text(3.14), "+3.1%")
        self.assertEqual(invest.pct_text(-0.04), "0.0%")


class Breaks(unittest.TestCase):
    """Rules 3 and 4: the break state, TRIM once after the watch began, REVIEW before it, back in."""

    def test_the_states_walk_the_month_ends(self):
        m = [D(2026, k, 28) for k in range(1, 8)]
        votes = [(m[0], None, None), (m[1], "down", "down"), (m[2], "none", "down"), (m[3], "none", "none"),
                 (m[4], "up", "none"), (m[5], "down", "down"), (m[6], "up", "up")]
        self.assertEqual(invest.break_states(votes), [(m[1], "out"), (m[2], "out"), (m[3], "out"), (m[4], "in"),
                                                      (m[5], "out"), (m[6], "in")])
        self.assertEqual(invest.state_now(invest.break_states(votes)), ("in", m[6], True))
        self.assertEqual(invest.state_now(invest.break_states(votes[:2])), ("out", m[1], False))   # the first known
        self.assertEqual(invest.state_now([]), ("not known", None, False))

    def test_a_break_before_the_watch_is_a_review(self):
        rows = Folder().signals()
        self.assertEqual(rows["ZOLD"][0::2], ("REVIEW", "both votes down since Fri 2026-07-31, before the watch "
                                                        "began (rule 3)"))
        self.assertEqual(rows["ZBRK"][0], "REVIEW")                 # no watch yet: Oct 30 is not after it
        out = Folder().ok("explain", "ZOLD", "--now", NOW)
        self.assertIn("Rule 3. Both votes have been down since Fri 2026-07-31. That was before the watch began. You "
                      "choose: keep, or trim half.", out)
        self.assertIn("Rule 3. Out since Fri 2026-07-31: both votes were down. No new money while out.", out)

    def test_a_break_after_the_watch_began_is_a_trim_of_half_once(self):
        f = Folder()
        f.ok("signals", "--write", "--now", "2026-10-02 17:00")                       # the watch begins: Sep 30
        self.assertEqual(f.read("state/invest.json")["watch_start"], "2026-09-30")
        rows = f.rows(f.ok("signals", "--write", "--now", NOW))
        self.assertEqual(rows["ZBRK"][0::2], ("TRIM", "break: both votes turned down at the end of October (rule 3)"))
        self.assertIn("Rule 3. Break: both votes turned down at the end of October. The rule says: sell half (650 "
                      "shares). You decide. Nothing is placed.", f.ok("explain", "ZBRK", "--now", NOW))
        rec = f.read("state/invest.json")["trims"]["ZBRK"]
        self.assertEqual(rec, [{"key": "trim:break:2026-10-30", "kind": "break", "date": "2026-10-30",
                                "shares_at": 1300, "amount": 650, "closed": None}])
        f.shares("ZBRK", 650)                                                         # you sold half
        rows = f.rows(f.ok("signals", "--write", "--now", NOW))
        self.assertEqual(rows["ZBRK"][0::2], ("HOLD", "out since Fri 2026-10-30: no new money while out (rule 3)"))
        self.assertEqual(f.read("state/invest.json")["trims"]["ZBRK"][0]["closed"],
                         {"why": "the shares dropped", "date": "2026-10-30"})

    def test_a_trim_shows_once_a_break(self):
        f = Folder()
        f.ok("signals", "--write", "--now", "2026-07-02 17:00")                       # the watch begins: Jun 30
        self.assertEqual(f.rows(f.ok("signals", "--write", "--now", "2026-08-03 17:00"))["ZOLD"][0], "TRIM")
        self.assertEqual(f.rows(f.ok("signals", "--write", "--now", "2026-09-01 17:00"))["ZOLD"][0], "TRIM")
        f.shares("ZOLD", 1000)
        self.assertEqual(f.rows(f.ok("signals", "--write", "--now", "2026-09-01 17:00"))["ZOLD"][0], "HOLD")
        self.assertEqual(f.rows(f.ok("signals", "--write", "--now", "2026-10-01 17:00"))["ZOLD"][0::2],
                         ("HOLD", "out since Fri 2026-07-31: no new money while out (rule 3)"))

    def test_back_in(self):
        f = Folder()
        f.data = os.path.join(f.root, "mini")
        mini_market(f.data, "ZBACK", lambda y, m: {(2026, 3): -0.08, (2026, 4): -0.08, (2026, 5): -0.08}.get(
            (y, m), 0.06 if (y, m) >= (2026, 6) else 0.01), D(2025, 1, 1), D(2026, 9, 30))
        f.write("portfolio.json", {"cash": 1000000, "positions": [{"ticker": "ZBACK", "shares": 100}]})
        out = f.ok("explain", "ZBACK", "--now", "2026-08-31 17:00")
        self.assertIn("Rule 3. Out since Thu 2026-04-30: both votes were down. No new money while out.", out)
        out = f.ok("explain", "ZBACK", "--now", "2026-09-30 17:00")
        self.assertIn("Rule 4. Back in at the end of September: no vote is down and both are up. New money is "
                      "allowed again.", out)
        self.assertEqual(out.splitlines()[1], invest.SIGNAL_LINES["ADD"])

    def test_ack_keep_and_trimmed(self):
        f = Folder()
        out = f.ok("ack", "ZOLD", "--keep", "--note", "I keep it for the long term", "--now", NOW)
        self.assertIn('ZOLD now: HOLD. You chose keep on Fri 2026-10-30: "I keep it for the long term".', out)
        self.assertEqual(f.signals()["ZOLD"][0::2], ("HOLD", "you chose keep on Fri 2026-10-30; out since Fri "
                                                             "2026-07-31 (rule 3)"))
        f.ok("signals", "--write", "--now", "2026-10-02 17:00")
        f.ok("signals", "--write", "--now", NOW)
        self.assertEqual(f.signals()["ZBRK"][0], "TRIM")
        out = f.ok("ack", "ZBRK", "--trimmed", "--now", NOW)
        self.assertIn("ZBRK now: HOLD. You chose trim on Fri 2026-10-30.", out)
        self.assertEqual(f.read("state/invest.json")["trims"]["ZBRK"][0]["closed"],
                         {"why": "your ack", "date": "2026-10-30"})
        self.assertEqual(f.signals()["ZBRK"][0], "HOLD")

    def test_a_new_event_of_another_kind_opens_a_new_review(self):
        f = Folder()
        f.ok("ack", "ZOLD", "--keep", "--now", NOW)
        doc = f.read("metrics.json")
        doc["companies"].append(dict(doc["companies"][2], symbol="ZOLD", profit_growth_yoy=-30.0))
        f.write("metrics.json", doc)
        self.assertEqual(f.signals()["ZOLD"][0::2], ("REVIEW", "profit -30.0% on the same quarter a year back "
                                                               "(rule 7)"))


def whipsaw(y, m):
    """ZWHP: up to 2024, a hard fall in Jan and Feb 2025 (both votes down), then sideways with never a
    vote up (so by rule 4 it never goes back in), then down again in Sep and Oct 2026."""
    if (y, m) < (2025, 1):
        return 0.015
    if (y, m) in ((2025, 1), (2025, 2)):
        return {1: -0.12, 2: -0.10}[m]
    if (y, m) < (2026, 9):
        return 0.003 if (y * 12 + m) % 2 else -0.003
    return -0.09 if (y, m) == (2026, 9) else -0.04


def falls(y, m):
    """ZDEC: up to June 2025, then a hard fall, then a slow one: a break after a watch from Jun 30."""
    return 0.015 if (y, m) < (2025, 7) else (-0.08 if (y, m) < (2026, 1) else -0.02)


class MovingWindow(unittest.TestCase):
    """3.4: the newest 500 files move on each month. The break state is saved at each --write (and
    ack) and walked on, so an ack holds, a TRIM comes once a break, and "before the watch" is true."""

    @classmethod
    def setUpClass(cls):
        cls.data = os.path.join(TMP, "moving")
        long_market(cls.data, {"ZWHP": ("0829", 100.0, whipsaw, None), "ZDEC": ("0819", 80.0, falls, None),
                               "ZLAT": ("0818", 60.0, lambda y, m: -0.03, D(2025, 3, 3))}, D(2023, 1, 2),
                    D(2026, 10, 30))

    def test_the_window_moves_one_month_at_a_time(self):
        f = Folder()
        f.data = self.data
        f.write("portfolio.json", {"cash": 10000000, "positions": [{"ticker": s, "shares": 1000}
                                                                   for s in ("ZWHP", "ZDEC", "ZLAT")]})
        rows = f.rows(f.ok("signals", "--write", "--now", "2025-07-01 17:00"))       # the watch begins: Jun 30
        self.assertEqual(rows["ZWHP"][0::2], ("REVIEW", "both votes down since Fri 2025-02-28, before the watch "
                                                        "began (rule 3)"))
        f.ok("ack", "ZWHP", "--keep", "--now", "2025-07-01 17:00")
        kept = ("HOLD", "you chose keep on Tue 2025-07-01; out since Fri 2025-02-28 (rule 3)")
        seen, told = {"ZDEC": [], "ZLAT": []}, []
        for day in month_end_days(D(2025, 7, 1), D(2026, 10, 30)):                    # 16 month-ends
            now = "%s 17:00" % day.isoformat()
            out = f.ok("signals", "--write", "--now", now)
            rows = f.rows(out)
            told += [x for x in out.splitlines() if x.startswith("notify: ZWHP")]
            self.assertEqual(rows["ZWHP"][0::2], kept, day)              # the ack holds: the same "since" each month
            for sym in seen:
                seen[sym].append(rows[sym][0])
            if rows["ZDEC"][0] == "TRIM":
                self.assertIn("Could sell 500 ZDEC at", out)
                f.shares("ZDEC", 500)                                     # you sold half
            if rows["ZLAT"][0] == "REVIEW":
                self.assertEqual(rows["ZLAT"][2], "both votes down since Tue 2026-03-31, the first month-end with both "
                                                  "votes known (rule 3)")
                why = f.ok("explain", "ZLAT", "--now", now)
                check_words(self, why)
                self.assertIn("Rule 3. Both votes have been down since Tue 2026-03-31, the first month-end with both "
                              "votes known. The files do not show when the break began. You choose: keep, or trim "
                              "half.", why)
                f.ok("ack", "ZLAT", "--keep", "--now", now)
        self.assertEqual(told, ["notify: ZWHP: no rule asks for a change now (HOLD)."])   # once, after the ack
        self.assertEqual((seen["ZDEC"].count("TRIM"), seen["ZDEC"][2], "REVIEW" in seen["ZDEC"]), (1, "TRIM", False))
        self.assertEqual(rows["ZDEC"][0::2], ("HOLD", "out since Tue 2025-09-30: no new money while out (rule 3)"))
        self.assertEqual((seen["ZLAT"].count("REVIEW"), rows["ZLAT"][0]), (1, "HOLD"))
        for sym in ("ZDEC", "ZLAT"):
            self.assertNotIn("before the watch began", f.ok("explain", sym, "--now", "2026-10-30 17:00"))
        breaks = f.read("state/invest.json")["breaks"]
        self.assertEqual(breaks["ZWHP"], {"state": "out", "since": "2025-02-28", "changed": True, "walked": "2026-10-30"})
        self.assertEqual(breaks["ZDEC"], {"state": "out", "since": "2025-09-30", "changed": True, "walked": "2026-10-30"})
        self.assertEqual([r["covers"] for r in f.read("state/invest.json")["acks"]["ZWHP"]], [["break:2025-02-28"]])

    def test_walk_on_from_a_saved_state(self):
        m = [D(2026, k, 28) for k in range(1, 7)]
        votes = [(m[0], None, None), (m[1], "down", "none"), (m[2], "none", "down"), (m[3], "none", "none"),
                 (m[4], "up", "none"), (m[5], "down", "down")]
        saved = {"state": "out", "since": D(2025, 2, 28), "changed": True, "walked": m[2]}
        self.assertEqual(invest.walk_on(saved, votes[:5]), ("in", m[4], True))       # back in: one up, none down
        self.assertEqual(invest.walk_on(saved, votes), ("out", m[5], True))          # then a new break
        self.assertEqual(invest.walk_on(dict(saved, walked=m[3]), votes[:4]), ("out", D(2025, 2, 28), True))
        # the walk of the files alone would start "in" at Feb (one vote down): the saved state keeps "out"
        self.assertEqual(invest.state_now(invest.break_states(votes[:5])), ("in", m[1], False))
        self.assertIsNone(invest.walk_on(dict(saved, walked=m[0]), votes))   # its month-end is not a known one
        self.assertIsNone(invest.walk_on(dict(saved, walked=D(2026, 7, 31)), votes))   # a run with an older --now
        self.assertIsNone(invest.walk_on(None, votes))
        self.assertIsNone(invest.saved_break({"breaks": {"ZUP": {"state": "out", "since": "bad"}}}, "ZUP"))


TOTAL = Decimal("994559.90")        # the fixture's long-term money: Rs 944,559.90 in shares + Rs 50,000.00 cash


class Size(unittest.TestCase):
    """Rules 5 and 6: the share cap and trim line, the sector cap and review line, sector groups."""

    def test_over_the_cap_gets_no_new_money(self):
        f = Folder()
        f.shares("ZUP", 1100)
        out = f.ok("explain", "ZUP", "--now", NOW)
        self.assertEqual(out.splitlines()[1], "HOLD. No rule asks for a change.")
        self.assertIn("Rule 5. Size: 22.0% of your long-term money. The cap is 20%. No new money while it is over.",
                      out)
        self.assertEqual(f.signals()["ZUP"][0::2], ("HOLD", "over the 20% cap (rule 5)"))

    def test_the_trim_line_in_whole_shares_rounded_up(self):
        f = Folder()
        rows = f.signals()
        self.assertEqual(rows["ZCAP"], ("TRIM", "27.2", "over the 25% trim line (rule 5)"))
        want = int(((Decimal(2950) * Decimal("91.55") - TOTAL / 5) / Decimal("91.55")).quantize(
            Decimal(1), rounding=ROUND_CEILING))
        self.assertEqual(want, 778)
        self.assertEqual(invest.trim_shares(2950 * 91.55, 20, float(TOTAL), 91.55, 2950), 778)
        out = f.ok("explain", "ZCAP", "--now", NOW)
        self.assertIn("Rule 5. Size: 27.2%. The trim line is 25%. The rule says: sell 778 shares, back to 20%. You "
                      "decide. Nothing is placed.", out)
        self.assertIn("Could sell 778 ZCAP at 91.55: about Rs 71,225.90, costs about Rs 127.57. You decide. Nothing "
                      "is placed.", out)

    def test_the_trim_line_waits_for_a_month_end_check(self):
        f = Folder()
        out = f.ok("explain", "ZCAP", "--now", "2026-10-29 17:00")
        self.assertEqual(out.splitlines()[1], "HOLD. No rule asks for a change.")
        self.assertIn("Rule 5. Size: 27.1% of your long-term money. The cap is 20%. No new money while it is over. "
                      "The 25% trim line is checked at the month-end.", out)

    def test_the_sector_cap_stops_an_add(self):
        f = Folder()
        f.edit("invest.json", sector_groups={"TC": ["0828", "0804"]})
        rows = f.signals()
        self.assertEqual(rows["ZUP"][0::2], ("HOLD", "trend up, 12-month up: sector TC over its 30% cap (rule 6)"))
        self.assertIn("Rule 6. Sector TC: 32.0%. The cap is 30%. No new money in this sector while it is over.",
                      f.ok("explain", "ZUP", "--now", NOW))

    def test_a_sector_over_the_review_line_is_a_review(self):
        f = Folder()
        f.edit("invest.json", sector_groups={"BIG": ["0828", "0804", "0823"]})
        rows = f.signals()
        self.assertEqual(rows["ZUP"][0::2], ("REVIEW", "sector BIG 36.8%, over the 35% review line (rule 6)"))
        self.assertEqual(rows["ZCAP"][0], "TRIM")                   # a TRIM comes before a REVIEW
        self.assertIn("Rule 6. Sector BIG: 36.8%. The review line is 35%. Please look at the mix.",
                      f.ok("explain", "ZUP", "--now", NOW))
        f.edit("invest.json", sector_groups={})
        self.assertEqual(f.signals()["ZUP"][0], "ADD")              # while the cause is there only

    def test_sector_groups_join_codes(self):
        f = Folder()
        self.assertIn("Rule 6. Sector AUTOMOBILE ASSEMBLER: 8.0%. The cap is 30%.", f.ok("explain", "ZSPL", "--now", NOW))
        f.edit("invest.json", sector_groups={"AUTOS": ["0801", "0802"]})
        self.assertIn("Rule 6. Sector AUTOS: 15.2%. The cap is 30%.", f.ok("explain", "ZSPL", "--now", NOW))


class Profit(unittest.TestCase):
    """Rule 7: psx/metrics.json, profit after tax only, never EPS, only with label_ok true."""

    def metrics(self, f, sym, **row):
        doc = f.read("metrics.json")
        doc["companies"] = [r for r in doc["companies"] if r["symbol"] != sym] + [
            dict({"symbol": sym, "quarter": "Q3 2026", "label_ok": True, "profit_000": 100000}, **row)]
        f.write("metrics.json", doc)

    def test_minus_25_or_worse_is_a_review(self):
        f = Folder()
        self.assertEqual(f.signals()["ZRES"][0::2], ("REVIEW", "profit -40.1% on the same quarter a year back "
                                                               "(rule 7)"))
        self.assertIn("Rule 7. Profit: -40.1% on the same quarter a year back (Q1 2027). The review line is -25%. "
                      "Read the results.", f.ok("explain", "ZRES", "--now", NOW))
        self.metrics(f, "ZUP", profit_growth_yoy=-25.0)
        self.assertEqual(f.signals()["ZUP"][0], "REVIEW")
        self.metrics(f, "ZUP", profit_growth_yoy=-24.9)
        self.assertEqual(f.signals()["ZUP"][0], "ADD")
        self.assertIn("Rule 7. Profit: -24.9% on the same quarter a year back (Q3 2026). The review line is -25%.",
                      f.ok("explain", "ZUP", "--now", NOW))

    def test_a_loss_is_a_review(self):
        f = Folder()
        self.metrics(f, "ZUP", profit_000=-5200, profit_growth_yoy=None)
        self.assertEqual(f.signals()["ZUP"][0::2], ("REVIEW", "a loss in Q3 2026 (rule 7)"))
        self.assertIn("Rule 7. Profit: a loss in Q3 2026. The rule asks for a look. Read the results.",
                      f.ok("explain", "ZUP", "--now", NOW))

    def test_a_label_that_looks_wrong_waits(self):
        f = Folder()
        self.assertEqual(f.signals()["ZSPL"][0], "HOLD")             # -60% but label_ok false: no REVIEW
        self.assertIn("Rule 7. Results: the quarter label looks wrong (psx-analyst). The profit rule waits until it "
                      "is checked.", f.ok("explain", "ZSPL", "--now", NOW))

    def test_no_row(self):
        out = Folder().ok("explain", "ZTHN", "--now", NOW)
        self.assertIn("Rule 7. Results: no row in psx/metrics.json. Run psx-analyst: facts.py ZTHN --metrics "
                      "psx/metrics.json.", out)
        f = Folder()
        os.remove(f.path("metrics.json"))
        self.assertIn("Note: no psx/metrics.json, so rule 7 (profit) is not checked.", f.ok("signals", "--now", NOW))

    def test_a_candidate_with_no_row_is_named(self):
        f = Folder()
        f.shares("ZRES", 0)
        f.edit("invest.json", candidates=["ZRES"])
        doc = f.read("metrics.json")
        doc["companies"] = [r for r in doc["companies"] if r["symbol"] != "ZRES"]
        f.write("metrics.json", doc)
        out = f.ok("signals", "--now", NOW)
        self.assertEqual(f.rows(out)["ZRES"][0], "ADD")                # rule 7 cannot stop it: no row
        self.assertIn("Note: no row in psx/metrics.json for ZOLD, ZBON, ZGAP, ZNEW, ZSTL, ZTHN, ZRIT, ZRES, so rule 7 "
                      "skips them. Run psx-analyst: facts.py ZOLD ZBON ZGAP ZNEW ZSTL ZTHN ZRIT ZRES --metrics "
                      "psx/metrics.json.", out)
        note = ("Note: no row in psx/metrics.json for ZRES, so rule 7 (profit) was not checked for it. Run psx-analyst: "
                "facts.py ZRES --metrics psx/metrics.json.")
        self.assertIn(note, f.ok("money", "--now", NOW))
        self.assertIn(note, f.ok("orders", "--now", MORNING))
        os.remove(f.path("metrics.json"))
        self.assertIn("facts.py ZUP ZCAP ZBRK ZOLD ZSPL ZBON ZGAP ZNEW ZSTL ZTHN ZRIT ZRES --metrics",
                      f.ok("signals", "--now", NOW))

    def test_eps_is_never_used(self):
        f = Folder()
        self.assertEqual(f.signals()["ZUP"][0], "ADD")               # EPS -60% in the fixture, profit +15.2%
        self.metrics(f, "ZUP", profit_growth_yoy=None, eps_growth_yoy=-90.0, eps=-1.0)
        self.assertEqual(f.signals()["ZUP"][0], "ADD")
        self.assertIn("Rule 7. Profit: Q3 2026 has no same quarter a year back in psx/metrics.json, so the rule cannot "
                      "check it.", f.ok("explain", "ZUP", "--now", NOW))

    def test_the_board_meeting_line(self):
        out = Folder().ok("explain", "ZBRK", "--now", NOW)
        self.assertIn("Rule 7. Board meeting on Thu 2026-11-12 (psx/metrics.json). Results can move the price.", out)
        out = Folder().ok("explain", "ZBRK", "--now", "2026-11-13 17:00")
        self.assertNotIn("Board meeting", out)

    def test_a_profit_review_lasts_until_a_newer_quarter_or_an_ack(self):
        f = Folder()
        f.ok("ack", "ZRES", "--keep", "--now", NOW)
        self.assertEqual(f.signals()["ZRES"][0::2], ("HOLD", "you chose keep on Fri 2026-10-30"))   # at its fair share
        doc = f.read("metrics.json")
        doc["companies"] = [dict(r, quarter="Q2 2027") if r["symbol"] == "ZRES" else r for r in doc["companies"]]
        f.write("metrics.json", doc)
        self.assertEqual(f.signals()["ZRES"][0], "REVIEW")           # a newer quarter: a new REVIEW


class Data(unittest.TestCase):
    """Rule 8: a missing file next to a jump, no close for 20 sessions."""

    def test_a_missing_file_next_to_a_jump(self):
        f = Folder()
        self.assertEqual(f.signals()["ZGAP"][0::2], ("REVIEW", "data: no closing file for Mon 2026-09-14 (rule 8)"))
        self.assertIn("Rule 8. Data: no closing file for Mon 2026-09-14. A payout or split there would be hidden. Run "
                      "backfill or save the file.", f.ok("explain", "ZGAP", "--now", NOW))
        self.assertEqual(f.signals()["ZUP"][0], "ADD")               # the same gap, no jump: no REVIEW

    def test_no_close_for_20_sessions(self):
        f = Folder()
        self.assertEqual(f.signals()["ZSTL"][0::2], ("REVIEW", "data: no close since Fri 2026-10-02 (rule 8)"))
        self.assertIn("Rule 8. Data: no close for ZSTL since Fri 2026-10-02. Check the PSX notices.",
                      f.ok("explain", "ZSTL", "--now", NOW))
        self.assertEqual(f.rows(f.ok("signals", "--now", "2026-10-29 17:00"))["ZSTL"][0], "HOLD")    # 19 sessions

    def test_a_data_ack_lasts_until_the_next_new_gap(self):
        f = Folder(copy=True)
        f.ok("ack", "ZGAP", "--keep", "--now", NOW)
        self.assertEqual(f.signals()["ZGAP"][0], "ADD")
        doc = f.read("state/invest.json")
        self.assertEqual(doc["acks"]["ZGAP"][0]["covers"], ["gap:2026-09-14"])


class NewMoney(unittest.TestCase):
    """3.6: whole shares, costs included, up to each share's fair share, inside the caps and limits."""

    def buys(self, out):
        return [(int(m.group(1).replace(",", "")), m.group(2)) for m in
                re.finditer(r"^Could buy ([\d,]+) (\S+) at", out, re.M)]

    def test_the_equal_fair_share(self):
        out = Folder().ok("money", "--now", NOW)
        self.assertIn("Could buy 142 ZUP at 242.04: Rs 34,369.68 plus about Rs 61.56 costs. You decide. Nothing is "
                      "placed.", out)
        self.assertIn("Rs 15,568.76 stays as cash. No ADD share has room for it.", out)
        room = TOTAL / 12 - Decimal("48408.00")                     # fair share 100% / 12 shares
        self.assertTrue(142 * Decimal("242.04") <= room < 143 * Decimal("242.04"))
        self.assertEqual(invest.leg_cost(242.04, 142, invest.COSTS), 61.56)

    def test_targets_and_the_cap(self):
        f = Folder()
        f.edit("invest.json", targets={"ZUP": 6})
        self.assertEqual(self.buys(f.ok("money", "--now", NOW)), [(46, "ZUP")])
        f = Folder()
        f.edit("invest.json", max_weight_pct=6)
        self.assertEqual(self.buys(f.ok("money", "--now", NOW)), [(46, "ZUP")])
        self.assertTrue(46 * Decimal("242.04") <= TOTAL * Decimal("0.06") - Decimal("48408.00") < 47 * Decimal("242.04"))

    def test_whole_shares_with_their_costs(self):
        out = Folder().ok("money", "10000", "--now", NOW)
        self.assertIn("Could buy 41 ZUP at 242.04: Rs 9,923.64 plus about Rs 17.77 costs. You decide. Nothing is "
                      "placed.", out)
        self.assertIn("Rs 58.59 stays as cash. No ADD share has room for it.", out)

    def test_the_max_order_cut(self):
        f = Folder()
        f.edit("config.json", max_order_pkr=20000)
        self.assertEqual(self.buys(f.ok("money", "--now", NOW)), [(82, "ZUP")])

    def test_a_slice_under_the_minimum_is_shared_out_and_max_trades(self):
        f = Folder()
        f.ok("ack", "ZBON", "--checked", "--now", NOW)
        self.assertEqual(self.buys(f.ok("money", "50000", "--now", NOW)), [(315, "ZBON"), (102, "ZUP")])
        self.assertEqual(self.buys(f.ok("money", "9000", "--now", NOW)), [(112, "ZBON")])     # no 2 slices under 5,000
        f.edit("config.json", max_trades_per_day=1)
        self.assertEqual(self.buys(f.ok("money", "50000", "--now", NOW)), [(435, "ZBON")])

    def test_the_money_waits(self):
        f = Folder()
        f.edit("config.json", blocked=["ZUP"])
        out = f.ok("money", "--now", NOW)
        self.assertIn("No share meets the add rules today. The money waits as cash. That is a rule, not a forecast.",
                      out)
        self.assertIn("Rule 9. This share is on your never-add list. No new money.", f.ok("explain", "ZUP", "--now", NOW))

    def test_money_over_the_recorded_cash_is_refused_for_orders(self):
        code, out, err = Folder().run("orders", "--money", "60000", "--now", MORNING)
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(err, "psx-invest: Only Rs 50,000.00 is recorded as long-term cash. Record new money first: "
                              "invest.py cash --add 10000.\n")
        f = Folder()
        f.edit("invest.json", keep_cash_pkr=20000)
        self.assertIn("Money to split: Rs 30,000.00 (the cash in psx/portfolio.json less keep_cash_pkr).",
                      f.ok("money", "--now", NOW))
        self.assertEqual(f.run("orders", "--money", "40000", "--now", MORNING)[0], 2)

    def test_money_n_is_a_what_if(self):
        out = Folder().ok("money", "60000", "--now", NOW)            # S3 runs money 46000 before cash --add
        self.assertIn("Money to split: Rs 60,000.00 (the amount you gave). Your long-term money: Rs 1,004,559.90.",
                      out)
        self.assertIn("Rs 10,000.00 of it is not recorded yet, so this is a what-if. Record it before any order: "
                      "invest.py cash --add 10000.", out)
        f = Folder()
        f.edit("portfolio.json", cash=0)
        out = f.ok("money", "46000", "--now", "2026-10-29 17:00")
        self.assertIn("Rs 46,000.00 of it is not recorded yet", out)


MORNING = "2026-11-02 10:30"        # the next session, in the session: orders are made then (3.7)


def contract(name):
    with open(os.path.join(CONTRACT, name), encoding="utf-8") as fh:
        return json.load(fh)


class Orders(unittest.TestCase):
    """3.7: psx/orders.json for psx-risk: TRIM sells first, then ADD buys; never with paper and real holdings."""

    def test_the_shape_and_the_trim_sells_first(self):
        f = Folder()
        f.ok("signals", "--write", "--now", "2026-10-02 17:00")                      # the watch began: ZBRK is a TRIM
        out = f.ok("orders", "--write", "--now", MORNING)
        doc, want = f.read("orders.json"), contract("orders-invest.json")
        self.assertEqual(list(doc), list(want))
        self.assertEqual([doc[k] for k in ("created_by", "created_at", "status", "paper", "note")],
                         ["psx-invest", "2026-11-02T05:30:00Z", "proposed", True, want["note"]])
        self.assertEqual([(o["side"], o["symbol"], o["qty"]) for o in doc["orders"]],
                         [("sell", "ZCAP", 778), ("sell", "ZBRK", 650), ("buy", "ZUP", 142)])
        for o in doc["orders"]:
            self.assertEqual(list(o), list(want["orders"][0]))
            self.assertEqual(list(o["plan"]), list(want["orders"][0]["plan"]))
            self.assertEqual([o[k] for k in ("type", "time_in_force", "board", "stop_loss", "take_profit")],
                             ["limit", "day", "REG", None, None])
            self.assertEqual((o["plan"]["style"], o["plan"]["entry"]), ("invest", o["limit_price"]))
        self.assertEqual(doc["orders"][0]["why"], "psx-invest TRIM: size 27.2%, over the 25% trim line, back to the "
                                                  "20% cap (rule 5)")
        self.assertEqual(doc["orders"][0]["plan"], {"style": "invest", "signal": "TRIM", "entry": 91.55,
                                                    "fair_share_pct": 8.33, "weight_after_pct": 19.99,
                                                    "rules": ["size over the trim line (27.2%)"]})
        self.assertEqual(doc["orders"][1]["why"], "psx-invest TRIM: break: both votes turned down at the end of "
                                                  "October, sell half (rule 3)")
        self.assertEqual(doc["orders"][2]["why"], "psx-invest ADD: no vote down, trend up (+6.8%), 12-month up "
                                                  "(+19.5%), size 4.9% to 8.3% (fair share 8.3%)")
        self.assertEqual(doc["orders"][2]["plan"]["rules"], ["trend up (+6.8%)", "12-month up (+19.5%)"])
        self.assertIn("Wrote 3 orders to psx/orders.json: the sells first, then the buys.", out)
        self.assertEqual(invest.read_journal(f.path("invest", "journal.jsonl"))[-1]["written"], True)

    def test_paper_with_real_holdings_writes_only_the_journal(self):
        f = Folder()
        pf = f.read("portfolio.json")
        f.write("portfolio.json", dict([("real_holdings", True)] + list(pf.items())))
        out = f.ok("orders", "--write", "--now", MORNING)
        self.assertFalse(os.path.exists(f.path("orders.json")))
        self.assertIn(invest.PAPER_REAL_LINE, out)
        self.assertIn("After you buy or sell in your own app, record each fill: ticket.py fill --symbol ZCAP --side sell "
                      "--qty Q --price P --real (psx-broker; Q and P as your app shows them). Record new money first "
                      "with invest.py cash --add N. Until you record it, the rules see the old shares and cash.", out)
        entry = invest.read_journal(f.path("invest", "journal.jsonl"))[-1]
        self.assertEqual((entry["kind"], entry["written"], entry["why"]), ("orders", False, "paper mode and real holdings"))
        self.assertEqual([(o["side"], o["symbol"], o["qty"], o["price"]) for o in entry["orders"]],
                         [("sell", "ZCAP", 778, 91.55), ("buy", "ZUP", 142, 242.04)])
        f.edit("config.json", paper=False)                                            # your own decision
        f.ok("orders", "--write", "--now", MORNING)
        self.assertIs(f.read("orders.json")["paper"], False)

    def test_without_write_nothing_is_written(self):
        f = Folder()
        before = tree(f.root)
        out = f.ok("orders", "--now", MORNING)
        self.assertIn("Nothing was written. Add --write to save them in psx/orders.json for psx-risk.", out)
        self.assertEqual(tree(f.root), before)

    def test_a_new_share_and_the_blocked_list(self):
        f = Folder()
        f.shares("ZUP", 0)
        f.edit("invest.json", candidates=["ZUP"])
        out = f.ok("orders", "--now", MORNING)
        self.assertIn("Could buy 206 ZUP at 242.04", out)
        self.assertIn("Add ZUP to the trading folder's blocked list, so the trading tools never touch it.", out)
        self.assertEqual(f.rows(f.ok("signals", "--now", NOW))["ZUP"][:2], ("ADD", "0.0"))

    def test_the_market_hours_and_a_fresh_price(self):
        f = Folder()
        out = f.ok("orders", "--now", NOW)
        self.assertIn("Note: PSX is not open now (closed for the day: the session ended at 4:30 PM PKT). psx-risk "
                      "passes a buy only in the session.", out)
        f.write("prices.json", {"as_of": "2026-11-02T05:26:00Z", "source": "psx-screener", "prices": {"ZUP": 245.1},
                                "detail": {"ZUP": {"time": "2026-11-02T05:25:00Z", "fetched": "2026-11-02T05:26:00Z"}}})
        out = f.ok("orders", "--now", MORNING)
        self.assertIn("(prices: psx/prices.json for ZUP; the last close for the rest).", out.splitlines()[0])
        self.assertIn("Could buy 140 ZUP at 245.10", out)
        self.assertNotIn("Note: PSX is not open", out)
        out = f.ok("orders", "--now", "2026-11-02 10:42")                             # 17 minutes old: not fresh
        self.assertIn("Could buy 142 ZUP at 242.04", out)

    def test_no_order_and_the_daily_limit(self):
        f = Folder()
        f.edit("portfolio.json", cash=0)
        code, out, _ = f.run("orders", "--now", "2026-10-29 17:00")
        self.assertEqual(code, 1)
        self.assertIn("No order: no TRIM signal, and no new money for an ADD share.", out)
        f = Folder()
        f.ok("signals", "--write", "--now", "2026-10-02 17:00")
        f.edit("config.json", max_orders_per_day=2)
        out = f.ok("orders", "--now", MORNING)
        self.assertIn("Note: max_orders_per_day is 2, so ZUP waits for another day.", out)
        self.assertNotIn("Could buy", out)

    def test_no_order_with_new_money_says_the_money_waits_and_keeps_a_journal_line(self):
        f = Folder()                                     # 7.3 S3: cash recorded, no ADD share, paper and real holdings
        pf = f.read("portfolio.json")
        f.write("portfolio.json", dict([("real_holdings", True)] + list(pf.items())))
        f.edit("config.json", blocked=["ZUP"])
        code, out, _ = f.run("orders", "--money", "46000", "--write", "--now", "2026-10-29 11:00")
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[1:4], ["No order: no TRIM signal.", invest.WAITS_LINE,
                                                 "The journal keeps this run (psx/invest/journal.jsonl). No order was "
                                                 "written."])
        self.assertFalse(os.path.exists(f.path("orders.json")))
        entry = invest.read_journal(f.path("invest", "journal.jsonl"))[-1]
        self.assertEqual((entry["kind"], entry["orders"], entry["written"]), ("orders", [], False))
        self.assertIn("  Thu 2026-10-29  orders: none (no TRIM signal; no share meets the add rules, so the money waits "
                      "as cash)", f.ok("journal"))
        f.edit("config.json", blocked=[])                # ZUP is ADD, but Rs 4,000 is under the minimum order
        code, out, _ = f.run("orders", "--money", "4000", "--now", "2026-10-29 11:00")
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines()[1:3], ["No order: no TRIM signal.", "Rs 4,000.00 stays as cash. No ADD share "
                                                                              "has room for it."])


class PartSales(unittest.TestCase):
    """3.4: a TRIM lasts until the shares drop by its amount. After part of it is sold, the rule asks
    only for the rest, in signals, explain and psx/orders.json. orders --write saves the TRIM once."""

    def test_a_trim_sold_in_part_asks_only_for_the_rest(self):
        f = Folder()
        f.ok("signals", "--write", "--now", "2026-10-02 17:00")                       # the watch begins: Sep 30
        f.ok("signals", "--write", "--now", NOW)                                      # ZCAP 778 of 2950, ZBRK 650 of 1300
        f.shares("ZCAP", 2550)                                                        # you sold 400 ZCAP and 300 ZBRK
        f.shares("ZBRK", 1000)
        f.edit("portfolio.json", cash=50000 + 400 * 91.55 + 300 * 67.44)
        out = f.ok("signals", "--now", "2026-11-02 17:00")
        rows = f.rows(out)
        self.assertEqual(rows["ZCAP"], ("TRIM", "23.5", "TRIM from Fri 2026-10-30: 378 of 778 shares left (rule 5)"))
        self.assertEqual(rows["ZBRK"], ("TRIM", "6.8", "break: both votes turned down at the end of October, 350 of 650 "
                                                       "shares left (rule 3)"))
        self.assertIn("Could sell 378 ZCAP at 91.55: about Rs 34,605.90, costs about Rs 61.98. You decide. Nothing is "
                      "placed.", out)
        self.assertIn("Could sell 350 ZBRK at 67.44: about Rs 23,604.00, costs about Rs 42.27. You decide. Nothing is "
                      "placed.", out)
        self.assertIn("Rule 5. Size: 23.5%. The trim line is 25%. On Fri 2026-10-30 the rule asked to sell 778 shares, "
                      "back to 20%. Your shares dropped by 400 since then. The rule says: sell the rest, 378 shares. You "
                      "decide. Nothing is placed.", f.ok("explain", "ZCAP", "--now", "2026-11-02 17:00"))
        self.assertIn("Rule 3. Break: both votes turned down at the end of October. On Fri 2026-10-30 the rule asked to "
                      "sell half (650 shares). Your shares dropped by 300 since then. The rule says: sell the rest, 350 "
                      "shares. You decide. Nothing is placed.", f.ok("explain", "ZBRK", "--now", "2026-11-02 17:00"))
        f.ok("orders", "--write", "--now", MORNING)
        doc = f.read("orders.json")
        self.assertEqual([(o["side"], o["symbol"], o["qty"]) for o in doc["orders"]],
                         [("sell", "ZCAP", 378), ("sell", "ZBRK", 350), ("buy", "ZUP", 142)])
        self.assertEqual((doc["orders"][0]["why"], doc["orders"][0]["plan"]["weight_after_pct"]),
                         ("psx-invest TRIM: the TRIM from Fri 2026-10-30: 378 of 778 shares left, back to the 20% cap "
                          "(rule 5)", 19.99))
        self.assertEqual(doc["orders"][1]["why"], "psx-invest TRIM: break: both votes turned down at the end of "
                                                  "October, sell half: 350 of 650 shares left (rule 3)")
        f.shares("ZCAP", 2172)                                                        # you sold the rest
        f.shares("ZBRK", 650)
        rows = f.rows(f.ok("signals", "--write", "--now", "2026-11-02 17:00"))
        self.assertEqual((rows["ZCAP"][0], rows["ZBRK"][0]), ("HOLD", "HOLD"))
        trims = f.read("state/invest.json")["trims"]
        self.assertEqual([trims[s][0]["closed"] for s in ("ZCAP", "ZBRK")],
                         [{"why": "the shares dropped", "date": "2026-11-02"}] * 2)
        self.assertEqual(invest.trim_left({"shares_at": 1300, "amount": 650}, 1400), (650, 650, 0))   # bought more

    def test_orders_write_saves_the_trim_before_a_sale(self):
        f = Folder()
        f.ok("signals", "--write", "--now", "2026-10-02 17:00")                       # the watch begins: Sep 30
        out = f.ok("orders", "--write", "--now", MORNING)                             # no --write ran at Oct 30
        self.assertIn("Could sell 650 ZBRK at 67.44", out)
        self.assertIn("Note: 2 new TRIMs are saved in psx/state/invest.json, once. After a sale, the next run asks only "
                      "for the rest.", out)
        self.assertEqual(f.read("state/invest.json")["trims"]["ZBRK"],
                         [{"key": "trim:break:2026-10-30", "kind": "break", "date": "2026-11-02", "shares_at": 1300,
                           "amount": 650, "closed": None}])
        f.shares("ZBRK", 650)                                                         # you sold half
        rows = f.rows(f.ok("signals", "--write", "--now", "2026-11-02 17:00"))
        self.assertEqual(rows["ZBRK"][0::2], ("HOLD", "out since Fri 2026-10-30: no new money while out (rule 3)"))
        self.assertNotIn("Could sell", f.ok("explain", "ZBRK", "--now", "2026-11-02 17:00"))


class FakeSite(unittest.TestCase):
    """A fake PSX site: closing files from the made-up market, a 404 for any other day."""

    def setUp(self):
        self.calls, self.waits = [], []
        self.saved = (invest.http_get, invest.SLEEP, invest.utc_now)
        invest.SLEEP = self.waits.append
        invest.utc_now = lambda: datetime.datetime(2026, 10, 30, 12, 30)              # Fri 5:30 PM in Karachi
        self.fail_at = self.page_from = self.gone_from = None

        def get(url, timeout=30):
            self.calls.append(url)
            if self.fail_at and len(self.calls) == self.fail_at:
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, io.BytesIO(b""))
            if self.page_from and len(self.calls) >= self.page_from:          # HTTP 200 with a web page
                return b"<html><body>Please wait (a made-up busy page)</body></html>"
            path = os.path.join(MARKET, "mkt_summary", url.rsplit("/", 1)[1])
            if not os.path.exists(path) or (self.gone_from and len(self.calls) >= self.gone_from):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"<html>not found</html>"))
            with open(path, "rb") as fh:
                return fh.read()
        invest.http_get = get

    def tearDown(self):
        invest.http_get, invest.SLEEP, invest.utc_now = self.saved

    def folder(self, keep_from="2026-08", site=True, sessions=100):
        f = Folder(copy=True)
        for name in os.listdir(os.path.join(f.data, "mkt_summary")):
            if name < keep_from:
                os.remove(os.path.join(f.data, "mkt_summary", name))
        f.edit("config.json", psx_site=site)
        f.edit("invest.json", sessions=sessions)
        return f

    def days(self):
        return [u.rsplit("/", 1)[1][:-2] for u in self.calls]


class Backfill(FakeSite):
    """3.11: only with the site on, never --offline or --now; closing files only; a 404 day is recorded."""

    def test_the_site_off_asks_nothing(self):
        f = self.folder(site=False)
        code, out, _ = f.run("backfill")
        self.assertEqual((code, self.calls), (1, []))
        self.assertIn('The PSX site is off for this tool: save the closing files '
                      '(https://dps.psx.com.pk/download/mkt_summary/YYYY-MM-DD.Z) in ', out)

    def test_offline_and_now_ask_nothing(self):
        f = self.folder()
        self.assertEqual(f.run("backfill", "--offline")[:2], (1, "--offline: the backfill fetches nothing.\n"))
        self.assertEqual(f.run("backfill", "--now", NOW)[:2],
                         (1, "A run with --now never uses the PSX site, so the backfill fetches nothing.\n"))
        self.assertEqual(self.calls, [])

    def test_only_missing_closing_files_and_a_404_day_once(self):
        f = self.folder()
        out = f.ok("backfill")
        self.assertTrue(all(re.match(r"^https://dps\.psx\.com\.pk/download/mkt_summary/\d{4}-\d\d-\d\d\.Z$", u)
                            for u in self.calls), self.calls)
        days = self.days()
        self.assertEqual((len(days), len(set(days)), days[0], days[1], days[-1]),
                         (39, 39, "2026-09-14", "2026-07-31", "2026-06-08"))
        self.assertEqual(f.read("state/invest.json")["no_session"], ["2026-09-14"])
        self.assertTrue(os.path.exists(os.path.join(f.data, "mkt_summary", "2026-06-08.Z")))
        self.assertIn("20 of 38 saved, about 1 minute left.", out)
        self.assertIn("Backfill done: 38 closing files saved, 1 day with no session recorded (PSX answered 404). "
                      "The folder has 100 closing files now.", out)
        del self.calls[:]
        self.assertIn("Backfill done: 0 closing files saved, 0 days", f.ok("backfill"))
        self.assertEqual(self.calls, [])                                              # never asked again

    def test_another_error_stops_and_the_next_run_goes_on(self):
        f = self.folder()
        self.fail_at = 5
        code, out, _ = f.run("backfill")
        self.assertEqual(code, 2)
        self.assertIn("The backfill stopped at Tue 2026-07-28: PSX answered HTTP 503. 3 closing files "
                      "were saved before that. Run backfill again later: it goes on from there.", out)
        self.assertEqual(f.read("state/invest.json")["no_session"], ["2026-09-14"])
        self.fail_at, first = None, self.days()
        del self.calls[:]
        f.ok("backfill")
        self.assertEqual(self.days()[0], "2026-07-28")
        self.assertFalse(set(self.days()) & set(first[:4]))

    def test_the_pause_is_kept(self):
        f = self.folder(sessions=66)
        invest.http_get = self.saved[0]                                               # the real http_get ...
        real, asked = invest.urllib.request.urlopen, []

        class Answer(object):
            headers = {}

            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def urlopen(req, timeout=30):                                                 # ... with a fake network
            asked.append(req.full_url)
            path = os.path.join(MARKET, "mkt_summary", req.full_url.rsplit("/", 1)[1])
            if not os.path.exists(path):
                raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))
            with open(path, "rb") as fh:
                return Answer(fh.read())
        invest.urllib.request.urlopen = urlopen
        try:
            f.ok("backfill")
        finally:
            invest.urllib.request.urlopen = real
        self.assertEqual(len(asked), 5)                                               # 09-14 and 4 older files
        self.assertGreaterEqual(len(self.waits), 4)
        self.assertTrue(all(invest.PAUSE - 0.2 <= w <= invest.PAUSE for w in self.waits), self.waits)

    def test_the_dry_run_asks_nothing(self):
        f = self.folder(sessions=500)
        out = f.ok("backfill", "--dry-run")
        self.assertEqual(self.calls, [])
        self.assertEqual(out.splitlines()[0], "Backfill plan: 438 closing files to fetch, from Mon 2026-09-14 back to "
                                              "Fri 2024-11-15 (62 are there already). About 453 requests: an old PSX "
                                              "holiday answers 404 and is asked once. At 1.5 s apart that is about 12 "
                                              "to 15 minutes, and about 10 MB. Nothing was fetched.")

    def test_a_web_page_stops_the_backfill_and_is_not_a_holiday(self):
        f = self.folder()
        self.page_from = 4                                   # 09-14 (404), 2 files, then a page with HTTP 200
        code, out, _ = f.run("backfill")
        check_words(self, out)
        self.assertEqual((code, len(self.calls)), (2, 4))
        self.assertEqual(out, "The backfill stopped at Wed 2026-07-29: PSX sent a web page, not a closing file (a busy, "
                              "blocked or error page). Nothing was recorded for that day. 2 closing files were saved "
                              "before that. Run backfill again later: it goes on from there.\n")
        self.assertEqual(f.read("state/invest.json")["no_session"], ["2026-09-14"])          # only the real 404

    def test_a_long_run_of_404s_stops_and_none_of_it_is_recorded(self):
        f = self.folder()
        self.gone_from = 5                                   # 09-14 (404), 3 files, then every day answers 404
        code, out, _ = f.run("backfill")
        check_words(self, out)
        self.assertEqual((code, len(self.calls)), (2, 4 + invest.NO_FILE_RUN + 1))
        self.assertEqual(out, "The backfill stopped at Mon 2026-07-20: PSX answered 404 for 7 weekdays in a row (Tue "
                              "2026-07-28 back to Mon 2026-07-20). No PSX holiday is that long, so these days were not "
                              "recorded. If it stops at the same day again, PSX may have no older files. 3 closing files "
                              "were saved before that. Run backfill again later: it goes on from there.\n")
        self.assertEqual(f.read("state/invest.json")["no_session"], ["2026-09-14"])
        self.gone_from, first = None, len(self.calls)
        f.ok("backfill")                                     # the next run asks for those days again
        self.assertEqual(self.days()[first], "2026-07-28")

    def test_forget_no_session_clears_only_that_list(self):
        f = self.folder()
        acks = {"ZOLD": [{"date": "2026-10-30", "choice": "keep", "note": "", "covers": ["break:2026-07-31"]}]}
        f.write("state/invest.json", {"watch_start": "2026-09-30", "acks": acks, "no_session": ["2026-06-01",
                                                                                                "2026-09-14"]})
        out = f.ok("backfill", "--forget-no-session")
        check_words(self, out)
        self.assertEqual(out, "Cleared 2 days with no session from psx/state/invest.json. Nothing else changed. The next "
                              "backfill asks PSX for them again.\n")
        st = f.read("state/invest.json")
        self.assertEqual((st["no_session"], st["watch_start"], st["acks"]), ([], "2026-09-30", acks))
        self.assertEqual(self.calls, [])

    def test_no_progress_line_when_nothing_is_left(self):
        f = self.folder(sessions=102)                         # 40 to fetch
        out = f.ok("backfill")
        check_words(self, out)
        self.assertIn("20 of 40 saved, about 1 minute left.", out)
        self.assertNotIn("40 of 40 saved", out)
        self.assertIn("Backfill done: 40 closing files saved", out)
        f = self.folder(sessions=99)                          # 37 to fetch: 17 left at the first line
        self.assertIn("20 of 37 saved, under a minute left.", f.ok("backfill"))


class SiteFetch(FakeSite):
    """3.3: with the site on, signals fetches only the days after the newest file, never older."""

    def test_signals_asks_only_for_days_after_the_newest_file(self):
        f = self.folder(keep_from="2024", sessions=500)
        for day in ("2026-10-29", "2026-10-30"):
            os.remove(os.path.join(f.data, "mkt_summary", day + ".Z"))
        out = f.ok("signals")
        self.assertEqual(self.days(), ["2026-10-29", "2026-10-30"])                  # never 09-14 or older
        self.assertIn("Note: Saved 2 new closing files from the PSX site (to Fri 2026-10-30).", out)
        self.assertIn("Closes to Fri 2026-10-30 (500 closing files", out)
        del self.calls[:]
        os.remove(os.path.join(f.data, "mkt_summary", "2026-10-30.Z"))
        f.ok("signals", "--offline")
        f.ok("signals", "--now", NOW)
        self.assertEqual(self.calls, [])                                              # --offline and --now: no request


def every_output():
    """The text of every command on the made-up folder (the word test reads it all)."""
    f, outs = Folder(), []
    outs.append(f.ok("signals", "--write", "--now", "2026-10-02 17:00"))
    outs.append(f.ok("signals", "--write", "--now", NOW))
    for sym in ("ZUP", "ZCAP", "ZBRK", "ZOLD", "ZSPL", "ZBON", "ZGAP", "ZNEW", "ZRES", "ZSTL", "ZTHN", "ZRIT"):
        outs.append(f.ok("explain", sym, "--now", NOW))
    outs += [f.ok("money", "--now", NOW), f.ok("money", "9000", "--now", NOW), f.ok("orders", "--now", MORNING),
             f.ok("orders", "--write", "--now", MORNING), f.ok("cash", "--add", "46000", "--now", MORNING),
             f.ok("ack", "ZOLD", "--keep", "--note", "I keep it", "--now", MORNING),
             f.ok("ack", "ZBON", "--checked", "--now", MORNING), f.ok("signals", "--write", "--now", MORNING),
             f.ok("rules"), f.ok("journal"), f.ok("backfill", "--dry-run", "--now", NOW)]
    pf = f.read("portfolio.json")
    f.write("portfolio.json", dict(pf, real_holdings=True))
    outs.append(f.ok("orders", "--write", "--now", MORNING))
    for args in (("money", "999999", "--now", NOW), ("ack", "ZUP", "--keep", "--now", NOW),
                 ("ack", "ZRIT", "--keep", "--now", NOW), ("explain", "OGDC", "--now", NOW)):
        code, out, err = f.run(*args)
        outs.append(out + err)
    os.remove(f.path("invest.json"))
    outs.append("".join(f.run("signals")[1:]))
    # the new lines of round 1: a TRIM sold in part, no order, a candidate, the folder guard, other rules
    g = Folder()
    g.ok("signals", "--write", "--now", "2026-10-02 17:00")
    g.ok("signals", "--write", "--now", NOW)
    g.shares("ZCAP", 2550)
    g.shares("ZBRK", 1000)
    outs += [g.ok("signals", "--now", "2026-11-02 17:00"), g.ok("explain", "ZCAP", "--now", "2026-11-02 17:00"),
             g.ok("explain", "ZBRK", "--now", "2026-11-02 17:00"), g.ok("orders", "--write", "--now", MORNING)]
    g.shares("ZBON", 0)
    g.edit("invest.json", candidates=["ZBON"])
    g.edit("config.json", blocked=["ZUP"])
    outs += [g.ok("explain", "ZBON", "--now", NOW), g.ok("signals", "--now", NOW), g.ok("money", "--now", NOW),
             "".join(g.run("orders", "--money", "1000", "--write", "--now", "2026-10-29 11:00")[1:]), g.ok("journal")]
    rules = g.path("invest.json")                    # f has no psx/invest.json now: the folder guard
    outs += ["".join(f.run(*(args + ("--invest", rules)))[1:]) for args in
             (("orders", "--write", "--now", MORNING), ("signals", "--now", NOW), ("orders", "--now", MORNING))]
    return outs


class Words(unittest.TestCase):
    """3.5 and D60: plain rule words, never advice, never a profit; the footer on every report."""

    @classmethod
    def setUpClass(cls):
        cls.outs = every_output()

    def test_every_line_passes_the_word_test(self):
        checked = sum(check_words(self, out) for out in self.outs)
        self.assertGreater(checked, 300)

    def test_no_banned_words_and_no_profit(self):
        text = "\n".join(self.outs)
        for word in ("you would have made", "P&L", "real time", "real-time", "guarantee", "forecast:"):
            self.assertNotIn(word.lower(), text.lower())
        self.assertIsNone(re.search(r"\blive\b", text, re.I))
        self.assertIsNone(re.search(r"(?m)^\s*(Buy|Sell) ", text))
        self.assertNotIn(chr(0x2014), text)

    def test_the_footer_and_the_money_format(self):
        reports = [o for o in self.outs if o.startswith(("Long-term signals", "ZUP: every fact", "New money,",
                                                          "Orders for psx-risk", "The rules psx-invest", "The journal"))]
        self.assertGreater(len(reports), 10)
        for out in reports:
            self.assertEqual(out.splitlines()[-1] if not out.startswith("Long-term signals") else
                             [x for x in out.splitlines() if not x.startswith("notify:")][-1], invest.FOOTER)
        for amount in re.findall(r"Rs [\d,.]+", "\n".join(self.outs)):
            self.assertRegex(amount, r"^Rs \d{1,3}(,\d{3})*\.\d\d[.,]?$")

    def test_the_exact_lines_of_3_5(self):
        self.assertEqual(invest.SIGNAL_LINES, {
            "ADD": "ADD. The add rules are met. You could put new money here. You decide.",
            "HOLD": "HOLD. No rule asks for a change.", "TRIM": "TRIM. A sell rule is met.",
            "REVIEW": "REVIEW. Please look at this before any change."})
        self.assertEqual(invest.FOOTER, "Signals are rules, not predictions. No tool knows where a price will go. Not "
                                        "financial advice.")
        self.assertEqual(invest.TAX_LINE, "A sale with a gain owes capital gains tax. NCCPL takes it. Not tax advice.")
        self.assertEqual(invest.WAITS_LINE, "No share meets the add rules today. The money waits as cash. That is a "
                                            "rule, not a forecast.")
        self.assertEqual(invest.PAPER_REAL_LINE, "Paper mode and real holdings: nothing was written to psx/orders.json. "
                                                 "The journal has what the rules would do (psx/invest/journal.jsonl).")
        self.assertEqual(invest.CASH_LINE, "This money must not be in your trading budget too. One broker account "
                                           "holds both: the week report shows both cash numbers and their sum.")
        self.assertEqual([invest.NOTIFY_LINES[k] % "LUCK" for k in ("TRIM", "ADD", "REVIEW", "HOLD")], [
            "notify: LUCK: a sell rule is met (TRIM). Open the report. You decide.",
            "notify: LUCK: the add rules are met (ADD). Open the report. You decide.",
            "notify: LUCK: please look at this share (REVIEW). Open the report.",
            "notify: LUCK: no rule asks for a change now (HOLD)."])
        text = "\n".join(self.outs)
        for line in ("notify: ZBRK: a sell rule is met (TRIM). Open the report. You decide.",
                     "notify: ZCAP: a sell rule is met (TRIM). Open the report. You decide.",
                     "notify: ZBON: please look at this share (REVIEW). Open the report.",
                     "What the rules would do (prices: the last close):",
                     "New money: the rules would split Rs 50,000.00 like this: 142 ZUP. You decide. Nothing is placed.",
                     "(invest.py money 50000 shows how.)", invest.CASH_LINE, invest.PAPER_REAL_LINE, invest.TAX_LINE):
            self.assertIn(line, text)

    def test_what_you_paid_never_shows(self):
        text = "\n".join(self.outs)
        for p in contract_free_portfolio()["positions"]:
            costs = [p["avg_cost"]] + [lot["cost"] for lot in p["lots"]]
            for v in costs:
                for shown in ("%.2f" % v, "{:,.2f}".format(v), "%g" % v):
                    self.assertNotIn(shown, text, (p["ticker"], shown))


def contract_free_portfolio():
    with open(os.path.join(HERE, "portfolio.json"), encoding="utf-8") as fh:
        return json.load(fh)


class Files(unittest.TestCase):
    """3.10: what each command writes, and what it never writes."""

    def test_signals_without_write_writes_nothing(self):
        f = Folder()
        before = tree(f.root)
        for args in (("signals",), ("explain", "ZUP"), ("money",), ("orders",), ("rules",)):
            f.ok(*(args + ("--now", NOW)))
        f.ok("backfill", "--dry-run")
        self.assertEqual(tree(f.root), before)

    def test_the_signals_json_shape(self):
        f = Folder()
        f.ok("signals", "--write", "--now", NOW)
        doc = f.read("signals.json")
        self.assertEqual(list(doc), ["created_by", "created_at", "as_of", "month_end", "next_month_end", "sessions",
                                     "first_date", "account", "holdings", "new_money", "notes"])
        self.assertEqual([doc[k] for k in ("created_by", "created_at", "as_of", "month_end", "next_month_end",
                                           "sessions", "first_date")],
                         ["psx-invest", "2026-10-30T12:00:00Z", "2026-10-30 close", "2026-10-30", "2026-11-30", 500,
                          "2024-11-14"])
        self.assertEqual(doc["account"], {"holdings_pkr": 944559.9, "cash_pkr": 50000.0, "total_pkr": 994559.9})
        zup = doc["holdings"][0]
        self.assertEqual(list(zup)[:8], ["symbol", "signal", "weight_pct", "trend", "momentum", "state", "reasons",
                                         "action"])
        self.assertEqual((zup["symbol"], zup["signal"], zup["weight_pct"], zup["state"]), ("ZUP", "ADD", 4.87, "in"))
        self.assertEqual(zup["trend"]["vote"], "up")
        self.assertEqual(zup["momentum"], {"change_pct": 19.48, "vote": "up"})
        self.assertEqual(list(zup["reasons"][0]), ["rule", "met", "words"])
        self.assertEqual(zup["action"], {"side": "buy", "qty": 142, "price": 242.04, "value": 34369.68, "costs": 61.56})
        self.assertEqual((doc["new_money"]["amount_pkr"], doc["new_money"]["left_pkr"]), (50000.0, 15568.76))
        self.assertEqual(doc["holdings"][1]["action"]["side"], "sell")

    def test_a_second_run_changes_nothing_and_the_journal_only_grows(self):
        f = Folder()
        first = f.ok("signals", "--write", "--now", NOW)
        self.assertIn("notify:", first)
        files = tree(f.root)
        with open(f.path("invest", "journal.jsonl"), encoding="utf-8") as fh:
            journal = fh.read()
        self.assertEqual(len(journal.splitlines()), 12)
        again = f.ok("signals", "--write", "--now", NOW)
        self.assertNotIn("notify:", again)
        self.assertEqual(tree(f.root), files)
        later = f.ok("signals", "--write", "--now", "2026-10-31 11:00")
        self.assertNotIn("notify:", later)
        with open(f.path("invest", "journal.jsonl"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), journal)
        f.edit("config.json", blocked=["ZUP"])
        out = f.ok("signals", "--write", "--now", "2026-10-31 11:00")
        self.assertEqual([x for x in out.splitlines() if x.startswith("notify:")],
                         ["notify: ZUP: no rule asks for a change now (HOLD)."])
        with open(f.path("invest", "journal.jsonl"), encoding="utf-8") as fh:
            grown = fh.read()
        self.assertTrue(grown.startswith(journal) and len(grown.splitlines()) == 13)

    def test_cash_changes_only_the_cash(self):
        f = Folder()
        before = f.read("portfolio.json")
        out = f.ok("cash", "--add", "46,000", "--now", NOW)
        self.assertEqual(out.splitlines()[0], "Long-term cash in psx/portfolio.json: Rs 50,000.00 to Rs 96,000.00 "
                                              "(Rs 46,000.00 added). Nothing else changed.")
        after = f.read("portfolio.json")
        self.assertEqual(dict(after, cash=None), dict(before, cash=None))
        self.assertEqual(after["cash"], 96000.0)
        f.ok("cash", "--set", "1000", "--now", NOW)
        self.assertEqual(f.read("portfolio.json")["cash"], 1000.0)
        entries = invest.read_journal(f.path("invest", "journal.jsonl"))
        self.assertEqual([(e["kind"], e["from"], e["to"], e["add"]) for e in entries],
                         [("cash", 50000.0, 96000.0, 46000.0), ("cash", 96000.0, 1000.0, None)])
        self.assertEqual(f.run("cash", "--add", "-5", "--now", NOW)[0], 2)


class ShareCounts(unittest.TestCase):
    """Rule 8 and D56: a bonus, a right issue or a split after "as_of" (else the watch start) can mean
    an old share count in psx/portfolio.json. REVIEW (data) until ack --checked."""

    def test_a_bonus_after_as_of(self):
        f = Folder()
        rows = f.signals()
        self.assertEqual(rows["ZBON"][0::2], ("REVIEW", "data: a bonus (XB) on Mon 2026-10-12: check your shares "
                                                        "(rule 8)"))
        out = f.ok("explain", "ZBON", "--now", NOW)
        self.assertIn("Rule 8. Data: a bonus (XB) on Mon 2026-10-12, after your shares were checked (as_of Thu "
                      "2026-10-01). Check your shares in psx/portfolio.json against your broker app. Bonus shares come "
                      "a few days later. No new money until you check.", out)
        self.assertIn("Prices before Mon 2026-10-12 are scaled by 0.80: a bonus (XB) on that day.", out)
        self.assertEqual(rows["ZSPL"][0], "HOLD")                                    # its split came before as_of
        f.edit("portfolio.json", as_of="2026-06-30")
        self.assertEqual(f.signals()["ZSPL"][0::2], ("REVIEW", "data: a split on Thu 2026-07-02: check your shares "
                                                               "(rule 8)"))

    def test_with_no_as_of_the_watch_start_counts(self):
        f = Folder()
        pf = f.read("portfolio.json")
        del pf["as_of"]
        f.write("portfolio.json", pf)
        rows = f.signals()
        self.assertEqual(rows["ZBON"][0], "ADD")              # no as_of and no watch: only after the newest month-end
        self.assertEqual(rows["ZRIT"][0::2], ("REVIEW", "rights: ZRITR trades now (rule 10)"))
        f.write("state/invest.json", {"watch_start": "2026-09-30"})
        self.assertEqual(f.signals()["ZBON"][0], "REVIEW")
        self.assertIn("Rule 8. Data: a bonus (XB) on Mon 2026-10-12, after the watch began (Wed 2026-09-30).",
                      f.ok("explain", "ZBON", "--now", NOW))

    def test_a_candidate_you_do_not_hold_has_no_share_count(self):
        f = Folder()
        for sym in ("ZBON", "ZRIT", "ZOLD"):
            f.shares(sym, 0)
        f.edit("invest.json", candidates=["ZBON", "ZRIT", "ZOLD"])
        rows = f.signals()
        self.assertEqual((rows["ZBON"][0], rows["ZRIT"][0]), ("ADD", "ADD"))       # no shares to check
        self.assertEqual(rows["ZOLD"][0::2], ("HOLD", "not held, a candidate; out since Fri 2026-07-31: no new money "
                                                      "while out (rule 3)"))

    def test_a_candidate_says_it_is_not_held(self):
        f = Folder()
        f.shares("ZBON", 0)
        f.edit("invest.json", candidates=["ZBON"])
        self.assertTrue(f.signals()["ZBON"][2].startswith("not held, a candidate; no vote down"))
        out = f.ok("explain", "ZBON", "--now", NOW)
        self.assertEqual(out.splitlines()[1:3], [invest.SIGNAL_LINES["ADD"], 'You hold no ZBON. It is a candidate for '
                                                 'new money ("candidates" in psx/invest.json).'])
        self.assertIn("Rule 8. Data: no problem found. No file is missing next to a jump. It has a close in the newest "
                      "20 files. You hold no ZBON, so the share-count check does not apply.", out)
        self.assertNotIn("No bonus, right or split came", out)                        # its bonus is shown below
        self.assertIn("Prices before Mon 2026-10-12 are scaled by 0.80: a bonus (XB) on that day.", out)

    def test_ack_checked_gives_the_signal_back(self):
        f = Folder()
        code, out, err = f.run("ack", "ZBON", "--keep", "--now", NOW)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("ZBON's open REVIEW is about its share count or a right. Answer it with --checked", err)
        out = f.ok("ack", "ZBON", "--checked", "--now", NOW)
        self.assertIn("ZBON now: ADD. The add rules are met. You could put new money here. You decide.", out)
        self.assertEqual(f.signals()["ZBON"][0], "ADD")
        f.edit("config.json", blocked=["ZBON"])
        self.assertEqual(f.ok("explain", "ZBON", "--now", NOW).splitlines()[1],
                         "HOLD. You checked your shares on Fri 2026-10-30.")
        self.assertIn("ZUP has no REVIEW or TRIM to answer now (it is ADD).", f.run("ack", "ZUP", "--checked", "--now",
                                                                                     NOW)[2])
        self.assertIn("Answer it with --keep or --trimmed.", f.run("ack", "ZOLD", "--checked", "--now", NOW)[2])


class Rights(unittest.TestCase):
    """Rule 10: an XR, or a right letter of a holding, is REVIEW (rights) until ack --checked."""

    def test_an_xr_and_its_right_letter(self):
        f = Folder()
        self.assertEqual(f.signals()["ZRIT"][0::2], ("REVIEW", "data: a right issue (XR) on Thu 2026-10-15: check "
                                                               "your shares (rule 8); rights: ZRITR trades now (rule 10)"))
        self.assertIn("Rule 10. Rights: ZRIT went ex-right (XR) on Thu 2026-10-15. A right letter, ZRITR, trades now. "
                      "Pay for the right shares, or sell the right letter, by the date in the company's notice. If you "
                      "do neither, the right is lost. You decide.", f.ok("explain", "ZRIT", "--now", NOW))
        f.ok("ack", "ZRIT", "--checked", "--now", NOW)
        self.assertEqual(f.read("state/invest.json")["acks"]["ZRIT"][0]["covers"],
                         ["count:XR:2026-10-15", "rights:XR:2026-10-15", "rights:letter:ZRITR"])
        self.assertEqual(f.signals()["ZRIT"][0], "ADD")

    def test_the_words_when_the_letter_is_gone_or_not_there(self):
        xr = [{"date": D(2026, 10, 15), "kind": "XR", "factor": 0.9}]
        self.assertIn("No right letter is in the closing files yet.",
                      invest.rights_cause("DEMO", xr, [], D(2026, 10, 1))["words"])
        gone = [{"symbol": "DEMOR", "first": D(2026, 10, 23), "last": D(2026, 10, 29), "now": False}]
        self.assertIn("A right letter, DEMOR, traded from Fri 2026-10-23 to Thu 2026-10-29.",
                      invest.rights_cause("DEMO", xr, gone, D(2026, 10, 1))["words"])
        alone = invest.rights_cause("DEMO", [], [dict(gone[0], last=D(2026, 10, 30), now=True)], D(2026, 10, 25))
        self.assertTrue(alone["words"].startswith("Rights: a right letter of DEMO, DEMOR, trades now. Pay for the "
                                                  "right shares, or sell the right letter"))
        self.assertEqual((alone["keys"], alone["ack"]), (["rights:letter:DEMOR"], "checked"))
        self.assertIsNone(invest.rights_cause("DEMO", xr, gone, D(2026, 10, 30)))   # all before the date, and gone


class Commands(unittest.TestCase):
    """3.1 and 3.9: the commands, the exit codes, the plain errors."""

    def test_no_invest_json_stops(self):
        f = Folder()
        os.remove(f.path("invest.json"))
        for args in (("signals",), ("cash", "--add", "100"), ("rules",)):
            self.assertEqual(f.run(*args), (2, "", "psx-invest: No psx/invest.json: copy psx-invest/invest.example.json "
                                                   "to psx/invest.json first (step 1 in the skill page).\n"))

    def test_write_commands_need_psx_invest_json_in_the_folder(self):
        trade, rules = Folder(), Folder().path("invest.json")       # a folder with no psx/invest.json, and other rules
        os.remove(trade.path("invest.json"))
        before = tree(trade.root)
        for args in (("orders", "--write", "--now", MORNING), ("signals", "--write", "--now", NOW),
                     ("cash", "--add", "100"), ("ack", "ZOLD", "--keep", "--now", NOW), ("backfill",),
                     ("backfill", "--forget-no-session")):
            self.assertEqual(trade.run(*(args + ("--invest", rules))), (2, "", "psx-invest: %s\n" % invest.NOT_HERE))
        self.assertEqual(tree(trade.root), before)                  # no psx/orders.json, no journal, no state
        note = "Note: the rules come from %s, not from psx/invest.json in this folder." % rules
        self.assertIn(note, trade.ok("signals", "--now", NOW, "--invest", rules))      # a look still works (7.3 S6)
        self.assertIn(note, trade.ok("orders", "--now", MORNING, "--invest", rules))
        self.assertEqual(tree(trade.root), before)

    def test_nothing_to_show_is_exit_1(self):
        f = Folder()
        f.write("portfolio.json", {"cash": 1000, "positions": []})
        self.assertEqual(f.run("signals", "--now", NOW)[:2],
                         (1, "No holdings in psx/portfolio.json, and no candidates in psx/invest.json.\n"))
        f = Folder()
        f.data = os.path.join(f.root, "empty")
        code, out, _ = f.run("signals", "--now", NOW)
        self.assertEqual(code, 1)
        self.assertIn("No closing files in", out)
        self.assertEqual(Folder().run("explain", "OGDC", "--now", NOW)[0], 1)
        self.assertEqual(Folder().run("journal")[0], 1)

    def test_the_example_rules_are_the_defaults(self):
        with open(os.path.join(ROOT, "psx-invest", "invest.example.json"), encoding="utf-8") as fh:
            example = json.load(fh)
        self.assertEqual(list(example.items()), list(invest.RULES.items()))
        with open(os.path.join(HERE, "invest.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), example)
        cfg = Folder().read("config.json")
        self.assertEqual([cfg[k] for k in ("require_stop", "cash_buffer_pct", "max_position_pct", "max_order_pkr",
                                           "max_trades_per_day")], [False, 0, 20, 100000, 3])

    def test_bad_settings_are_one_plain_line(self):
        f = Folder()
        f.edit("invest.json", trend_months=1)
        self.assertEqual(f.run("rules")[2], 'psx-invest: "trend_months" in psx/invest.json wants a whole number, 2 or '
                                            'more\n')
        f = Folder()
        f.edit("invest.json", targets={"ZUP": 60, "ZCAP": 50})
        self.assertIn("add up to more than 100%", f.run("rules")[2])
        f = Folder()
        f.edit("config.json", holidays={"2026-13-01": "bad"})
        self.assertEqual(f.run("signals", "--now", NOW)[0], 2)
        f = Folder()
        f.edit("portfolio.json", as_of="Nov 4")
        self.assertIn('"as_of" in psx/portfolio.json wants a date like 2026-11-04', f.run("signals", "--now", NOW)[2])
        self.assertIn("--now wants a time", Folder().run("signals", "--now", "soon")[2])

    def test_a_broken_prices_file_is_a_note_and_a_broken_state_is_an_error(self):
        f = Folder()
        with open(f.path("prices.json"), "w", encoding="utf-8") as fh:
            fh.write("{bad")
        out = f.ok("signals", "--now", NOW)
        self.assertIn("Note: psx/prices.json (the prices) is not valid JSON", out)
        self.assertIn("The last close is used.", out)
        os.makedirs(f.path("state"))
        with open(f.path("state", "invest.json"), "w", encoding="utf-8") as fh:
            fh.write("{bad")
        code, out, err = f.run("signals", "--now", NOW)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("Move it away to start again: the watch start, the open TRIMs and your acks are kept there.", err)

    def test_json_and_no_command(self):
        f = Folder()
        doc = json.loads(f.ok("signals", "--json", "--now", NOW))
        self.assertEqual((doc["holdings"][1]["symbol"], doc["holdings"][1]["signal"], doc["notify"]), ("ZCAP", "TRIM", []))
        self.assertEqual(json.loads(f.ok("money", "--json", "--now", NOW))["buys"][0]["qty"], 142)
        self.assertEqual(json.loads(f.ok("orders", "--json", "--now", MORNING))["orders"][0]["symbol"], "ZCAP")
        self.assertEqual(json.loads(f.ok("explain", "ZUP", "--json", "--now", NOW))["payouts"],
                         [{"date": "2026-08-10", "amount": 14.5, "flags": ["XD"]}])
        self.assertEqual(json.loads(f.ok("rules", "--json"))["sessions"], 500)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(invest.main(["signals", "--bad-flag"]), 2)
            self.assertEqual(invest.main([]), 2)
        self.assertIn("psx-invest: give a command: signals, explain, money, orders, cash, ack, backfill, rules or "
                      "journal", err.getvalue())

    def test_rules_and_the_journal(self):
        f = Folder()
        out = f.ok("rules")
        self.assertIn("3. Break: both votes down. The share goes out. TRIM half once if the break began after the "
                      "watch began, else REVIEW.", out)
        self.assertIn("The watch has not begun: the first signals --write starts it.", out)
        f.ok("signals", "--write", "--now", "2026-10-02 17:00")
        f.ok("signals", "--write", "--now", NOW)
        f.ok("cash", "--add", "1000", "--now", NOW)
        out = f.ok("journal")
        self.assertIn("October 2026:", out)
        self.assertIn("  Fri 2026-10-30  ZBRK  TRIM (was HOLD), close 67.44: break: both votes turned down at the end "
                      "of October (rule 3)", out)
        self.assertIn("  Fri 2026-10-30  cash: Rs 50,000.00 to Rs 51,000.00", out)
        self.assertIn("The watch began: Wed 2026-09-30.", f.ok("rules"))


class Docs(unittest.TestCase):
    """psx-invest/SKILL.md: the example is the tool's real output, and the page says what 5.5 asks."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "psx-invest", "SKILL.md"), encoding="utf-8") as fh:
            cls.page = fh.read()

    def test_the_example_is_the_real_output(self):
        part = self.page.split("\n## Example\n", 1)[1]
        block = part.split("```text\n", 1)[1].split("```", 1)[0]
        command, shown = block.split("\n", 1)
        self.assertEqual(command, '$ python3 invest.py signals --now "2026-10-30 17:00"')
        f = Folder()
        f.ok("signals", "--write", "--now", "2026-10-02 17:00")
        self.assertEqual(shown, f.ok("signals", "--now", NOW))

    def test_the_page_says_what_the_spec_asks(self):
        page = " ".join(self.page.split())
        for words in ('"as_of"', "ack SYM --checked", "Rule 10, rights", "real_holdings",
                      "One broker account holds both folders' cash. Never count the same money twice.",
                      "## What the rules cannot do", "## Rules for Claude", "## What it never does",
                      "autorun.py pause all --minutes 10", "Is psx/portfolio.json what you really own?",
                      "Faber (2007)", "Moskowitz, Ooi and Pedersen (2012)", "Bessembinder (2018)",
                      "DeMiguel, Garlappi and Uppal (2009)", "Odean (1998)", "Whipsaws", "Taxes", "Data gaps"):
            self.assertIn(words, page)
        self.assertEqual(self.page.count("\n## Safety\n"), 1)
        self.assertTrue(self.page.rstrip().endswith("Broker logins, passwords and PINs never go in the chat."))

    def test_the_page_lines_pass_the_word_test(self):
        lines = [x for x in self.page.splitlines() if not x.startswith(("|", "python3", "$"))]
        self.assertGreater(check_words(self, "\n".join(lines)), 150)

    def test_the_page_says_how_to_quote_record_and_clear(self):
        page = " ".join(self.page.split())
        for words in ("`alerts.py quote --holdings --write`", "Name each candidate too",
                      "`ticket.py fill --symbol DEMO --side buy --qty 151 --price 303.63 --real`",
                      "For new money, run `invest.py cash --add N` first: `fill --real` does not check the cash",
                      "Every command that writes stops in a folder without it, even with `--invest`.",
                      "It also saves each TRIM it shows in psx/state/invest.json, once.",
                      'a share that stays out keeps its "since" date', "`invest.py backfill --forget-no-session`",
                      "So do more than 6 weekdays in a row with a 404", '"not held, a candidate"'):
            self.assertIn(words, page)
        for old in ("quote FFC LUCK MARI", "The same files always give the same state",
                    "So it never runs in the trading folder by mistake."):
            self.assertNotIn(old, page)
        block = self.page.split("```bash\n", 1)[1].split("```", 1)[0]
        for line in block.splitlines():                          # each command line parses with the tool's own flags
            args = shlex.split(line.split("  #", 1)[0])[2:]
            self.assertTrue(invest.build_parser().parse_args(args).cmd, line)
