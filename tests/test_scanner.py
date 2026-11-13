import ast
import datetime
import io
import json
import os
import re
import shlex
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX
from _words import check_words

scan = load("psx-scanner", "scan")

MARKET = os.path.join(FIX, "psx-market")                     # the made-up sample market (read only)
TAPE = os.path.join(MARKET, "tape", "2026-10-29", "market.jsonl")
SCREENER = os.path.join(MARKET, "screener.html")
NAMES = os.path.join(MARKET, "symbol_name", "2026-10-29.zip")
DT = datetime.datetime
D = datetime.date
T = datetime.time
THU = D(2026, 10, 29)        # the example day (a normal Mon to Thu session)
FRI = D(2026, 10, 30)        # a Friday: 09:15 to 12:00, then 14:30 to 16:30
RAMADAN_MON = D(2026, 2, 23)  # Ramadan 2026: 09:15 to 13:30


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def sample_sessions(n=60, end=THU, include_end=False):
    """The sample market's closing files, as the daily scan reads them before the open on 10-29."""
    return scan.recent_sessions(end, n, MARKET, {}, True, include_end)


def sample_names():
    with open(NAMES, "rb") as fh:
        return scan.read_symbol_names(fh.read())


def synthetic_tape(day, segments, prices, step=2):
    """Screener snapshots every `step` minutes of "asof" over (start, end) HH:MM pairs.
    prices(asof datetime) -> {SYM: price}. Fetched = asof + 5 minutes, like the real tape."""
    out = []
    for a, b in segments:
        t = DT.combine(day, DT.strptime(a, "%H:%M").time())
        end = DT.combine(day, DT.strptime(b, "%H:%M").time())
        while t <= end:
            out.append({"asof": t.strftime("%Y-%m-%d %H:%M:%S"), "source": "screener",
                        "fetched": (t + datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S+05:00"),
                        "prices": prices(t)})
            t += datetime.timedelta(minutes=step)
    return out


def row(d, sym, close, ldcp, volume=1000000, flags=None, traded=True):
    """One closing-file row as read_mkt_summary gives it."""
    px = close if traded else 0.0
    return {"date": d, "symbol": sym, "sector": "0818", "short_name": sym, "open": px, "high": px, "low": px,
            "close": close, "volume": volume, "ldcp": ldcp, "flags": list(flags or [])}


def trading_days(start, n):
    out, d = [], start
    while len(out) < n:
        if scan.trading_day(d)[0]:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


class Universe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = scan.daily_rows(D(2026, 10, 28), MARKET, {}, True)
        cls.names = sample_names()

    def test_the_kept_list(self):
        kept, left = scan.build_universe(self.rows, self.names)
        self.assertEqual((len(self.rows), len(self.names)), (43, 41))
        self.assertEqual(len(kept), 31)
        self.assertIn(("DEMO", "Demo Industries Limited", "0818"), kept)
        self.assertEqual([s for s, _, _ in kept], sorted(s for s, _, _ in kept))
        self.assertEqual(left, {"non_voting": 1, "right": 1, "reit": 1, "preference": 1, "modaraba": 1,
                                "gem_equity": 1, "etf": 1, "stock_future": 3, "index_future": 1, "debt": 1})
        names = dict((s, n) for s, n, _ in kept)
        self.assertEqual(names["CNERGY"], "Cnergyico PK Limited")     # two spaces squeezed to one
        self.assertEqual(names["OGDC"], "Oil & Gas Development Company Limited")

    def test_one_case_per_kind(self):
        info = scan.symbol_info(self.rows, self.names)
        cases = {"ANLNV": "non_voting", "BAPLR1": "right", "DCR": "reit", "EPCLPS": "preference",
                 "FEM": "modaraba", "GEMMEL": "gem_equity", "MZNPETF": "etf", "DEMO-NOV": "stock_future",
                 "OGDC-NOV": "stock_future", "OGDC-NOVB": "stock_future", "KSE30-NOV": "index_future",
                 "P01GIS210127": "debt", "OGDC": "ordinary", "DEMO": "ordinary", "SYS": "ordinary"}
        for sym, kind in cases.items():
            self.assertEqual(info[sym]["kind"], kind, sym)
        self.assertEqual((info["KSE30-NOV"]["sector"], info["P01GIS210127"]["sector"]), ("41", "36"))

    def test_short_names_when_there_is_no_symbol_file(self):
        rows = scan.daily_rows(THU, MARKET, {}, True)          # HBL went XD on 10-29: "Habib BankXD"
        kept, left = scan.build_universe(rows, {})
        names = dict((s, n) for s, n, _ in kept)
        self.assertEqual(len(kept), 30)                         # ILP did not trade on 10-29: not in that file
        self.assertNotIn("ILP", names)
        self.assertEqual(names["HBL"], "Habib Bank")            # the XD flag is not part of the name
        self.assertEqual(names["DEMO"], "Demo Industries")
        self.assertEqual(sum(left.values()), 12)
        self.assertEqual(scan.kinds_text({"stock_future": 3, "debt": 1, "etf": 1}), "3 stock futures, 1 debt, 1 ETF")

    def test_newest_symbol_name_file(self):
        self.assertEqual(scan.names_file(MARKET), NAMES)
        d = tempfile.mkdtemp()
        try:
            self.assertEqual(scan.read_names(d), (None, {}))
            os.makedirs(os.path.join(d, "symbol_name"))
            with open(os.path.join(d, "symbol_name", "2026-10-29.zip"), "w") as fh:
                fh.write("<html>not found</html>")
            with self.assertRaises(scan.ScanError):
                scan.read_names(d)
        finally:
            shutil.rmtree(d)


class Time(unittest.TestCase):
    def test_karachi_clock(self):
        self.assertEqual(scan.parse_now("2026-10-29 10:15"), DT(2026, 10, 29, 5, 15))   # PKT is UTC+5
        self.assertEqual(scan.parse_now("2026-10-29T05:15:00Z"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(scan.to_pkt(DT(2026, 10, 29, 5, 15)), DT(2026, 10, 29, 10, 15))
        self.assertEqual(scan.utc_iso(DT(2026, 10, 29, 5, 15)), "2026-10-29T05:15:00Z")
        self.assertEqual(scan.clock_label(DT(2026, 10, 29, 10, 15)), "10:15 AM")
        self.assertEqual(scan.clock_label(T(14, 35)), "2:35 PM")
        self.assertEqual(scan.day_label(THU), "Thu 2026-10-29")
        with self.assertRaises(ValueError):
            scan.parse_now("tomorrow")
        with self.assertRaises(ValueError):
            scan.parse_hhmm("25:00")

    def test_todays_file_counts_only_after_the_close(self):
        self.assertEqual(scan.daily_end(DT(2026, 10, 29, 9, 0)), (THU, False))
        self.assertEqual(scan.daily_end(DT(2026, 10, 29, 15, 29)), (THU, False))
        self.assertEqual(scan.daily_end(DT(2026, 10, 29, 15, 30)), (THU, True))
        self.assertEqual(scan.daily_end(DT(2026, 10, 30, 16, 29)), (FRI, False))        # Friday closes at 16:30
        self.assertEqual(scan.daily_end(DT(2026, 10, 30, 16, 30)), (FRI, True))
        self.assertEqual(scan.daily_end(DT(2026, 2, 23, 13, 30)), (RAMADAN_MON, True))  # Ramadan closes at 13:30
        self.assertEqual(scan.daily_end(DT(2026, 10, 31, 9, 0)), (D(2026, 10, 31), True))  # a weekend

    def test_config_times_follow_the_day(self):
        self.assertEqual(scan.config_time(T(15, 0), THU), T(15, 0))
        self.assertEqual(scan.config_time(T(15, 0), FRI), T(16, 0))
        self.assertEqual(scan.config_time(T(15, 0), RAMADAN_MON), T(13, 0))
        self.assertEqual(scan.opening_range_slots(FRI)[0], DT(2026, 10, 30, 9, 15))
        self.assertEqual(scan.bar_close(FRI, 33), DT(2026, 10, 30, 14, 35))             # bar 33 is 14:30


class Daily(unittest.TestCase):
    # EXAMPLE.md section 3: closes through Wed 10-28, floors Rs 10 and 500,000 shares
    EXPECT = {"PSO": ["oversold_bounce"],   # RSI 11.5 on 10-27, then 391.45 -> 418.00 (RSI 33.8)
              "PPL": ["bull_flag"],         # up 13.57 %, then the last 5 closes in a 1.10 % range
              "UBL": ["reclaim_ma20"],      # 379.06 under 387.57, then 392.00 over 388.049
              "LUCK": ["breakout_20d"],     # 468.00 > 461.80
              "TRG": ["oversold"],          # RSI 12.4
              "DEMO": ["breakout_20d"],     # 98.40 > 96.56 (10-27's close)
              "SEARL": ["oversold"]}        # RSI 12.3
    ORDER = ["PSO", "PPL", "UBL", "LUCK", "TRG", "DEMO", "SEARL"]   # close x volume of 10-28, high first

    @classmethod
    def setUpClass(cls):
        cls.sessions = sample_sessions()
        cls.info = scan.symbol_info(cls.sessions[-1][1], sample_names())
        cls.uni = [s for s in sorted(cls.info) if cls.info[s]["kind"] == "ordinary"]

    def scan(self, min_price=10, min_volume=500000, sessions=None, syms=None):
        return scan.scan_daily(sessions or self.sessions, syms or self.uni, min_price, min_volume, self.info)

    def test_the_example_scan_before_the_open(self):
        self.assertEqual((len(self.sessions), self.sessions[0][0], self.sessions[-1][0]),
                         (60, D(2026, 8, 4), D(2026, 10, 28)))
        hits, counts, last = self.scan()
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual([h["symbol"] for h in hits], self.ORDER)
        self.assertEqual(counts, {"requested": 31, "fetched": 31, "illiquid": 3, "short": 0, "scanned": 28,
                                  "hits": 7})
        self.assertEqual(last, D(2026, 10, 28))
        self.assertEqual(hits[5], {"symbol": "DEMO", "name": "Demo Industries Limited", "sector": "0818",
                                   "setups": ["breakout_20d"], "close": 98.4, "close_date": "2026-10-28",
                                   "level": 96.56, "rsi": 75.7, "volume": 3787500})

    def test_levels_sectors_and_numbers(self):
        hits, _, _ = self.scan()
        by = dict((h["symbol"], h) for h in hits)
        self.assertEqual((by["LUCK"]["level"], by["LUCK"]["rsi"]), (461.8, 65.5))
        self.assertEqual(by["UBL"]["level"], 388.05)          # the 20-day average 388.049, half up
        for sym in ("PSO", "PPL", "TRG", "SEARL"):
            self.assertIsNone(by[sym]["level"], sym)
        self.assertEqual((by["TRG"]["rsi"], by["SEARL"]["rsi"], by["PSO"]["rsi"]), (12.4, 12.3, 33.8))
        self.assertEqual(dict((s, by[s]["sector"]) for s in self.ORDER),
                         {"PSO": "0821", "PPL": "0820", "UBL": "0807", "LUCK": "0804", "TRG": "0828",
                          "DEMO": "0818", "SEARL": "0823"})
        self.assertEqual(by["PSO"]["close"] * by["PSO"]["volume"], 1644453800)
        _, nums = scan.find_setups(scan.adjusted_closes(scan.daily_bars("PPL", self.sessions)))
        self.assertEqual((round(nums["run_pct"], 2), round(nums["range5_pct"], 2)), (13.57, 1.1))
        _, nums = scan.find_setups(scan.adjusted_closes(scan.daily_bars("PSO", self.sessions)))
        self.assertEqual(round(nums["rsi_prev"], 1), 11.5)

    def test_the_floors(self):
        hits, counts, _ = self.scan(0, 0)
        by = dict((h["symbol"], h["setups"]) for h in hits)
        self.assertEqual(counts["illiquid"], 0)
        self.assertEqual(by["KEL"], ["breakout_20d"])         # 6.12 > 6.04: only the Rs 10 floor keeps it out
        self.assertIn("oversold", by["NML"])                  # only the volume floor keeps it out
        _, counts, _ = self.scan(syms=["CNERGY", "KEL", "NML", "DEMO"])
        self.assertEqual((counts["illiquid"], counts["scanned"]), (3, 1))   # 8.25, 6.12 and 319,100 shares

    def test_a_30_session_window(self):
        s30 = sample_sessions(30)
        hits, counts, _ = self.scan(sessions=s30)
        self.assertEqual((counts["short"], counts["scanned"]), (1, 27))     # ILP: 29 closes (no trade on 10-15)
        self.assertEqual(len(scan.daily_bars("ILP", s30)), 29)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)   # the same tags

    def test_short_missing_and_untraded_charts(self):
        days = trading_days(D(2026, 9, 1), 40)
        sessions = [(d, [row(d, "X", 20.0, 20.0)] if i >= 30 else []) for i, d in enumerate(days)]
        sessions[-1][1].append(row(days[-1], "Y", 50.0, 50.0, traded=False))   # OPEN/HIGH/LOW 0: no REG trade
        for d, rows in sessions[:-1]:
            rows.append(row(d, "Y", 50.0, 50.0))
        hits, counts, last = scan.scan_daily(sessions, ["X", "Y", "GONE"], 10, 500000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertEqual(last, days[-1])                      # Y did not trade on the last day: volume 0
        self.assertEqual(scan.scan_daily([], ["X"], 10, 0)[1]["fetched"], 0)

    def test_rsi_math(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)   # Wilder's textbook example

    def test_payouts_and_splits_are_scaled(self):
        d = trading_days(D(2026, 9, 1), 5)
        prev = dict(zip(d[1:], d[:-1]))                          # each session -> the session before it

        def bar(i, close, ldcp, flags=()):
            return {"date": d[i], "close": close, "ldcp": ldcp, "flags": list(flags)}
        payout = [bar(0, 100.0, 100.0), bar(1, 100.0, 100.0),
                  bar(2, 96.0, 95.0, ["XD"]),                    # ex-date: LDCP 95 after a close of 100 (Rs 5)
                  bar(4, 97.0, 98.0)]                            # a gap (no bar on d[3]) and no flag: not scaled
        self.assertEqual([round(x, 4) for x in scan.adjusted_closes(payout, prev)], [95.0, 95.0, 96.0, 97.0])
        split = [bar(0, 100.0, 100.0), bar(1, 100.0, 100.0),
                 bar(2, 10.2, 10.0)]                             # no flag, the next session: a 10:1 split
        out = scan.adjusted_closes(split, prev)
        self.assertEqual([round(x, 4) for x in out], [10.0, 10.0, 10.2])
        self.assertEqual(out[-1], 10.2)                          # the last close is as in the file
        flat = [bar(0, 100.0, 100.0), bar(1, 101.0, 100.0), bar(2, 102.0, 101.0)]
        self.assertEqual(scan.adjusted_closes(flat, prev), [100.0, 101.0, 102.0])   # no payout: unchanged

    def test_a_split_does_not_look_like_a_fall(self):
        days = trading_days(D(2026, 8, 3), 40)
        closes = [200.0 + i for i in range(34)] + [23.4 + 0.1 * i for i in range(6)]   # 10:1 after day 34
        sessions = []
        for i, d in enumerate(days):
            ldcp = closes[i - 1] if i else closes[0]
            if i == 34:
                ldcp = 23.3                                     # the file's LDCP: 233.00 / 10
            sessions.append((d, [row(d, "SPLT", round(closes[i], 2), round(ldcp, 2))]))
        self.assertIn("oversold", scan.find_setups(closes)[0])  # unscaled, the split looks like a crash
        hits, counts, _ = scan.scan_daily(sessions, ["SPLT"], 10, 500000)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["setups"], ["breakout_20d"])   # scaled: a steady rise, at a 20-day high
        self.assertEqual((hits[0]["close"], hits[0]["level"]), (23.9, 23.8))

    def test_a_missing_file_is_a_gap_not_a_payout(self):
        d = trading_days(D(2026, 9, 1), 5)
        every, missing = scan.window_sessions([d[0], d[1], d[3], d[4]])
        self.assertEqual((every, missing), (d, [d[2]]))          # d[2] is a session by the calendar, with no file
        prev = dict(zip(every[1:], every[:-1]))
        bars = [{"date": d[0], "close": 100.0, "ldcp": 100.0, "flags": []},
                {"date": d[1], "close": 100.0, "ldcp": 100.0, "flags": []},
                {"date": d[3], "close": 103.0, "ldcp": 102.0, "flags": ["XD"]}]   # LDCP 102: d[2]'s close, never seen
        self.assertEqual(scan.adjusted_closes(bars, prev, missing), [100.0, 100.0, 103.0])   # not scaled
        self.assertEqual(scan.adjusted_closes(bars, prev)[0], 102.0)          # without `missing` it would be
        # the sample market without the 10-27 file: every other close is scaled as with the file
        gap = [s for s in self.sessions if s[0] != D(2026, 10, 27)]
        self.assertEqual(scan.window_sessions([x for x, _ in gap])[1], [D(2026, 10, 27)])
        for sym in self.uni:
            def scaled(sessions):
                every, missing = scan.window_sessions([x for x, _ in sessions])
                bars = scan.daily_bars(sym, sessions)
                return dict(zip([b["date"] for b in bars],
                                scan.adjusted_closes(bars, dict(zip(every[1:], every[:-1])), missing)))
            full, cut = scaled(self.sessions), scaled(gap)
            self.assertEqual([round(cut[x], 6) for x in cut], [round(full[x], 6) for x in cut], sym)
        _, counts, _ = self.scan(sessions=gap)
        self.assertEqual(counts["scanned"], 28)                   # the scan still runs; the Note says what is missing

    def test_the_gap_note(self):
        self.assertEqual(scan.gap_note([]), "")
        self.assertEqual(scan.gap_note([D(2026, 10, 27)]),
                         "Note: no closing file for Tue 2026-10-27, a PSX session by the calendar. Save it in "
                         "mkt_summary/. Closes are not scaled for a payout across a missing day.")
        self.assertIn("files for Mon 2026-10-26 and Tue 2026-10-27, PSX sessions by the calendar. Save them",
                      scan.gap_note([D(2026, 10, 26), D(2026, 10, 27)]))
        self.assertIn("files for 4 PSX sessions, from Wed 2026-10-21 to Tue 2026-10-27, by the calendar.",
                      scan.gap_note([D(2026, 10, 21), D(2026, 10, 22), D(2026, 10, 26), D(2026, 10, 27)]))
        holiday = scan.window_sessions([D(2026, 8, 13), D(2026, 8, 14), D(2026, 8, 17)])
        self.assertEqual(holiday, ([D(2026, 8, 13), D(2026, 8, 14), D(2026, 8, 17)], []))   # a file on a holiday counts
        self.assertEqual(scan.window_sessions([D(2026, 8, 13), D(2026, 8, 17)])[1], [])     # 08-14 was closed


class Orb(unittest.TestCase):
    # EXAMPLE.md section 4: the market tape of 10-29, cut at 10:16 (the tape's own asof time). The tape
    # has a snapshot every 2 minutes: by 10:15 it reaches only asof 10:14, so DEMO's 10:10 bar (it ends
    # at 10:15) is complete only from the asof 10:16 snapshot on. psx-entry reads the tape the same way.
    HITS = [("9:50 AM", "HUBC", 163.16, 161.35, 162.41, 0.46, 3), ("9:55 AM", "OGDC", 283.29, 280.98, 281.9, 0.49, 4),
            ("10:00 AM", "FFC", 525.38, 523.3, 523.77, 0.31, 5), ("10:00 AM", "KEL", 6.14, 6.1, 6.12, 0.33, 5),
            ("10:05 AM", "MEBL", 442.89, 440.32, 441.65, 0.28, 6),
            ("10:10 AM", "ENGROH", 262.7, 261.24, 261.65, 0.4, 7),
            ("10:15 AM", "DEMO", 100.65, 98.9, 100.2, 0.45, 8)]

    @classmethod
    def setUpClass(cls):
        cls.snaps = scan.read_tape(TAPE)
        cls.syms = sorted(set(k for s in cls.snaps for k in s["prices"]))

    def scan_at(self, hhmm, min_price=0, syms=None, snaps=None, volumes=None, min_volume=0):
        cut = DT.combine(THU, scan.parse_hhmm(hhmm))
        return scan.scan_orb(snaps or self.snaps, syms or self.syms, THU, cut, 15, min_price, min_volume, volumes)

    def test_the_example_scan_at_1016(self):
        self.assertEqual((len(self.snaps), len(self.syms)), (181, 31))
        hits, counts, failed, bad = self.scan_at("10:16")
        got = [(h["at"], h["symbol"], h["price"], h["or_low"], h["or_high"], h["pct_over"], h["bar_index"])
               for h in hits]
        self.assertEqual(got, self.HITS)                         # by the time the bar closed, then by symbol
        self.assertEqual(counts, {"requested": 31, "on_tape": 31, "unusable": 1, "illiquid": 0, "scanned": 30,
                                  "hits": 7})
        self.assertEqual((failed, bad), ({}, ["PIOC"]))          # PIOC: the 09:55 slot is empty
        demo = hits[-1]
        self.assertEqual(demo, {"symbol": "DEMO", "name": None, "sector": None, "setups": ["orb"], "price": 100.65,
                                "at": "10:15 AM", "bar_start": "10:10", "bar_index": 8, "or_high": 100.2,
                                "or_low": 98.9, "pct_over": 0.45})
        self.assertNotIn("ILP", [h["symbol"] for h in hits])     # no trade today: 72.40 all day, usable, no hit
        hits, counts, _, _ = self.scan_at("10:15")               # DEMO's 10:10 bar is not complete yet
        self.assertEqual([h["symbol"] for h in hits], [h[1] for h in self.HITS[:6]])
        self.assertEqual(counts["scanned"], 30)

    def test_a_replay_never_sees_later_snapshots(self):
        cut = DT(2026, 10, 29, 10, 15)
        mine, last = scan.tape_day(self.snaps, THU, cut)
        self.assertEqual((len(mine), last), (22, DT(2026, 10, 29, 10, 14)))   # asof 09:32 to 10:14
        self.assertEqual(len(scan.tape_day(self.snaps, THU)[0]), 181)          # no cutoff: the whole day
        self.assertEqual(scan.complete_count(THU, cut, last), 8)               # bars 0 to 7
        later = [dict(s, prices=dict(s["prices"], DEMO=90.0)) if s["asof"] > "2026-10-29 10:15" else s
                 for s in self.snaps]                                          # a crash after 10:15 changes nothing
        self.assertEqual(self.scan_at("10:15", snaps=later), self.scan_at("10:15"))
        live = [s for s in self.snaps if s["fetched"] <= "2026-10-29T10:20:00+05:00"]   # the real clock at 10:20
        self.assertEqual(self.scan_at("10:15", snaps=live)[0], self.scan_at("10:15")[0])

    def test_the_price_floor(self):
        hits, counts, _, _ = self.scan_at("10:16", min_price=10)
        self.assertEqual((len(hits), counts["illiquid"], counts["scanned"]), (6, 2, 28))   # KEL 6.14, CNERGY 8.25
        self.assertNotIn("KEL", [h["symbol"] for h in hits])

    def test_the_volume_floor_uses_the_last_closing_file(self):
        vols = scan.reg_volumes(scan.daily_rows(D(2026, 10, 28), MARKET, {}, True))
        self.assertEqual((vols["NML"], vols["DEMO"], vols["OGDC-NOV"]), (319100, 3787500, 0))
        hits, counts, _, _ = self.scan_at("10:16", volumes=vols, min_volume=2500000)
        self.assertEqual([h["symbol"] for h in hits], ["HUBC", "OGDC", "FFC", "KEL", "ENGROH", "DEMO"])  # MEBL: 2,311,300
        under = sorted(s for s in self.syms if s != "PIOC" and vols.get(s, 0) < 2500000)
        self.assertEqual(counts["illiquid"], len(under))
        _, counts, _, _ = self.scan_at("10:16", syms=["DEMO"], volumes={}, min_volume=1)
        self.assertEqual(counts["illiquid"], 1)                  # no volume known counts as 0 shares

    def test_a_bar_counts_only_once_it_is_complete(self):
        hits, _, _, _ = self.scan_at("10:14", syms=["DEMO"])
        self.assertEqual(hits, [])                               # the 10:10 bar ends at 10:15
        hits, _, _, _ = self.scan_at("10:15", syms=["DEMO"])
        self.assertEqual(hits, [])                               # ended, but the tape by 10:15 reaches only 10:14
        hits, _, _, _ = self.scan_at("10:16", syms=["DEMO"])
        self.assertEqual(hits[0]["at"], "10:15 AM")              # the asof 10:16 snapshot shows it ended
        last = DT(2026, 10, 29, 10, 16)
        self.assertEqual(scan.complete_count(THU, DT(2026, 10, 29, 10, 15), last), 9)    # bars 0 to 8
        self.assertEqual(scan.complete_count(THU, DT(2026, 10, 29, 10, 15), DT(2026, 10, 29, 10, 14)), 8)
        self.assertEqual(scan.complete_count(THU, DT(2026, 10, 29, 10, 15), None), 0)

    def test_the_real_clock_sees_only_older_data(self):
        # at 10:15 on the real clock the tape holds the fetches up to 10:15 (asof up to 10:10)
        early = [s for s in self.snaps if s["fetched"] <= "2026-10-29T10:15:00+05:00"]
        self.assertEqual(early[-1]["asof"], "2026-10-29 10:10:00")
        hits, _, _, _ = self.scan_at("10:15", snaps=early)
        self.assertEqual([h["symbol"] for h in hits], ["HUBC", "OGDC", "FFC", "KEL", "MEBL", "ENGROH"])
        more = [s for s in self.snaps if s["fetched"] <= "2026-10-29T10:21:00+05:00"]   # the 10:21 fetch, asof 10:16
        hits, _, _, _ = self.scan_at("10:21", snaps=more)
        self.assertIn("DEMO", [h["symbol"] for h in hits])

    def test_a_missing_bar_makes_a_chart_unusable(self):
        missing = [s["asof"] for s in self.snaps if "PIOC" not in s["prices"]]
        self.assertEqual(missing, ["2026-10-29 09:56:00", "2026-10-29 09:58:00"])
        bars = scan.bars_from_tape(self.snaps, THU, "PIOC")
        self.assertIsNone(bars[5])                                # the 09:55 bar
        self.assertFalse(scan.usable(bars[:9], 9, 3))
        self.assertTrue(scan.usable(bars[:5], 5, 3))              # before 09:55 it was fine
        self.assertFalse(scan.usable(bars[:2], 2, 3))             # not even the range yet

    def test_a_friday_with_the_juma_break(self):
        def prices(t):
            if t.hour == 9 and t.minute == 21:
                return {"X": 100.4}                               # the range high (09:20 bar)
            return {"X": 100.6 if t.hour >= 14 else 100.1}        # first close above it: the 14:30 bar
        snaps = synthetic_tape(FRI, [("09:17", "12:01"), ("14:32", "16:32")], prices)
        hits, counts, _, _ = scan.scan_orb(snaps, ["X"], FRI, DT(2026, 10, 30, 15, 0), 15)
        self.assertEqual(counts["scanned"], 1)                    # usable: the break has no slots
        self.assertEqual({k: hits[0][k] for k in ("at", "bar_start", "bar_index", "or_high", "or_low")},
                         {"at": "2:35 PM", "bar_start": "14:30", "bar_index": 33, "or_high": 100.4,
                          "or_low": 100.1})
        self.assertEqual(scan.complete_count(FRI, DT(2026, 10, 30, 13, 0), DT(2026, 10, 30, 16, 32)), 33)
        hits, _, _, _ = scan.scan_orb(snaps, ["X"], FRI, DT(2026, 10, 30, 13, 0), 15)
        self.assertEqual(hits, [])                                # in the break: no bar after 12:00 yet

    def test_a_ramadan_day_and_a_30_minute_range(self):
        special = {"09:21": 50.1, "09:31": 50.3, "09:33": 50.2}

        def prices(t):
            return {"X": 50.5 if t.strftime("%H:%M") >= "09:46" else special.get(t.strftime("%H:%M"), 50.0)}
        snaps = synthetic_tape(RAMADAN_MON, [("09:17", "13:31")], prices)
        hits, _, _, _ = scan.scan_orb(snaps, ["X"], RAMADAN_MON, DT(2026, 2, 23, 11, 0), 15)
        # range 09:15, 09:20, 09:25 (high 50.1); the 09:30 bar closes at 50.2, so the hit closes at 9:35 AM
        self.assertEqual((hits[0]["at"], hits[0]["bar_index"], hits[0]["or_high"]), ("9:35 AM", 3, 50.1))
        hits, _, _, _ = scan.scan_orb(snaps, ["X"], RAMADAN_MON, DT(2026, 2, 23, 11, 0), 30)
        # a 30-minute range: bars 0 to 5 (high 50.3); the 09:45 bar closes at 50.5
        self.assertEqual((hits[0]["at"], hits[0]["bar_index"], hits[0]["bar_start"], hits[0]["or_high"]),
                         ("9:50 AM", 6, "09:45", 50.3))

    def test_window_messages(self):
        until = T(15, 0)

        def msg(now, at=None, until=until, or_minutes=15):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, or_minutes, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-10-31 10:15"))
        self.assertIn("PSX holiday: Iqbal Day", msg("2026-11-09 10:15"))
        self.assertIn("Run orb again after 9:50 AM", msg("2026-10-29 09:40"))
        self.assertIn("Run orb again after 9:50 AM", msg("2026-10-29 09:49"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-30 09:34"))          # Friday: range from 09:15
        self.assertIn("Run orb again after 9:35 AM", msg("2026-02-23 09:30"))          # Ramadan: range from 09:15
        self.assertIn("expired", msg("2026-10-29 15:05"))
        self.assertIn("expired", msg("2026-10-29 11:30", until=T(11, 0)))
        self.assertIn("closed at 3:30 PM", msg("2026-10-29 15:30"))
        self.assertIn("closed at 4:30 PM", msg("2026-10-30 16:45", until=T(16, 0)))
        self.assertIn("later than now", msg("2026-10-29 10:00", at=T(10, 15)))
        self.assertIn("after the 3:30 PM close", msg("2026-10-29 17:00", at=T(16, 30)))
        self.assertIn("multiple of 5", msg("2026-10-29 10:15", or_minutes=7))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-29 16:30"), T(10, 15), 15, until),
                         (THU, DT(2026, 10, 29, 10, 15)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-29 09:50"), None, 15, until)[1],
                         DT(2026, 10, 29, 9, 50))
        friday = scan.config_time(until, FRI)                                          # 16:00 on a Friday
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-30 15:30"), None, 15, friday)[1],
                         DT(2026, 10, 30, 15, 30))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-30 13:00"), None, 15, friday)[1],
                         DT(2026, 10, 30, 13, 0))                                       # the Juma break
        self.assertIn("expired", msg("2026-10-30 16:05", until=friday))
        ramadan = scan.config_time(until, RAMADAN_MON)                                 # 13:00 in Ramadan
        self.assertIn("expired", msg("2026-02-23 13:05", until=ramadan))


class Tape(unittest.TestCase):
    """The recorder, with a fake clock, a fake screener fetch and no sleeping."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)                                          # a default psx/ path lands in the temp folder
        self.data = os.path.join(self.d, "data")
        self.cfg = os.path.join(self.d, "config.json")
        with open(self.cfg, "w") as fh:
            json.dump({"psx_site": True}, fh)
        with open(SCREENER, encoding="utf-8") as fh:
            self.page = fh.read()
        self.sleeps, self.fetches, self.broken = [], [], set()
        self.saved = (scan.utc_now, scan.SLEEP, scan.fetch_screener)
        self.clock = [DT(2026, 10, 29, 10, 20)]                 # UTC: 3:20 PM in Karachi
        scan.utc_now = lambda: self.clock[0]
        scan.SLEEP = self.sleep
        scan.fetch_screener = self.fetch

    def tearDown(self):
        scan.utc_now, scan.SLEEP, scan.fetch_screener = self.saved
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def sleep(self, s):
        self.sleeps.append(s)
        self.clock[0] += datetime.timedelta(seconds=s)

    def fetch(self):
        self.clock[0] += datetime.timedelta(seconds=1)
        self.fetches.append(scan.to_pkt(self.clock[0]).strftime("%H:%M"))
        if self.fetches[-1] in self.broken:
            raise urllib.error.URLError("timed out")
        return self.page

    def lines(self, day="2026-10-29"):
        return scan.read_tape(os.path.join(self.data, "tape", day, "market.jsonl"))

    def test_records_until_the_tape_sees_the_close(self):
        code, text, err = run(["tape", "--data", self.data, "--config", self.cfg])
        self.assertEqual(code, 0, err)
        snaps = self.lines()
        self.assertEqual(len(snaps), 9)                          # 15:20, 15:22, ... 15:36
        self.assertEqual((snaps[0]["fetched"], snaps[0]["asof"]), ("2026-10-29T15:20:01+05:00", "2026-10-29 15:15:01"))
        self.assertEqual(snaps[-1]["asof"], "2026-10-29 15:31:01")   # the first one as of the close or later
        self.assertEqual(snaps[0]["source"], "screener")
        self.assertEqual(len(snaps[0]["prices"]), len(scan.parse_screener(self.page)["rows"]))
        self.assertEqual(self.sleeps, [119.0] * 8)                # every 120 s; the fetch took 1 s
        self.assertIn("3:20 PM PKT  38 prices, as of 3:15 PM (about 5 minutes late)", text)
        self.assertIn("Stopped. The tape has seen the 3:30 PM close. 9 snapshots in", text)

    def test_until_errors_and_the_delay_setting(self):
        with open(self.cfg, "w") as fh:
            json.dump({"psx_site": True, "data_delay_minutes": 0}, fh)
        self.broken = {"15:22"}
        code, text, _ = run(["tape", "--data", self.data, "--config", self.cfg, "--until", "15:27"])
        self.assertEqual(code, 0)
        self.assertEqual(self.fetches, ["15:20", "15:22", "15:24", "15:26"])
        self.assertIn("no snapshot: could not read the PSX screener (timed out)", text)
        snaps = self.lines()
        self.assertEqual([s["asof"][11:16] for s in snaps], ["15:20", "15:24", "15:26"])   # no delay: asof = fetch
        self.assertIn("Stopped. It is 15:27, your --until time. 3 snapshots", text)
        self.broken = set("10:%02d" % m for m in range(60))
        self.fetches = []
        self.clock[0] = DT(2026, 10, 29, 5, 0)                    # 10:00 AM: five failures in a row stop it
        code, text, _ = run(["tape", "--data", self.data, "--config", self.cfg, "--quiet"])
        self.assertEqual(code, 1)
        self.assertEqual(self.fetches, ["10:00", "10:02", "10:04", "10:06", "10:08"])
        self.assertNotIn("no snapshot", text)                     # --quiet hides the line per snapshot
        self.assertIn("Stopped. 5 reads of the screener in a row failed. 0 snapshots", text)

    def test_when_it_does_not_record(self):
        code, _, err = run(["tape", "--data", self.data])                     # no config: the site is off
        self.assertEqual(code, 1)
        self.assertIn('The tape needs "psx_site": true in psx/config.json', err)
        self.assertIn("A saved page cannot make a tape: without the site, use a bars file from your broker "
                      "with psx-entry.", err)
        self.assertNotIn("screener.html", err)                    # saving the page would not help
        code, _, err = run(["tape", "--data", self.data, "--config", self.cfg, "--offline"])
        self.assertIn("--offline never uses the network", err)
        self.clock[0] = DT(2026, 10, 29, 4, 0)                    # 9:00 AM: before the pre-open
        code, _, err = run(["tape", "--data", self.data, "--config", self.cfg])
        self.assertEqual(code, 1)
        self.assertIn("Not recording. Closed: the pre-open starts at 9:15 AM PKT.", err)
        self.clock[0] = DT(2026, 10, 31, 5, 0)                    # a Saturday
        code, _, err = run(["tape", "--data", self.data, "--config", self.cfg])
        self.assertIn("weekend", err)
        code, _, err = run(["tape", "--data", self.data, "--config", self.cfg, "--every", "30"])
        self.assertIn("--every wants 60 to 300 seconds", err)
        self.assertEqual(self.fetches, [])
        self.assertFalse(os.path.exists(os.path.join(self.data, "tape")))

    def test_the_friday_break_keeps_recording(self):
        cfg = {"psx_site": True}
        self.assertEqual(scan.tape_stop(DT(2026, 10, 30, 13, 0), cfg, 5), "")      # break: the data still runs
        self.assertEqual(scan.tape_stop(DT(2026, 10, 30, 14, 20), cfg, 5), "")     # pre-open of the 2nd segment
        self.assertEqual(scan.tape_stop(DT(2026, 10, 30, 16, 38), cfg, 5), "")     # the late data shows the close
        self.assertIn("post-close session", scan.tape_stop(DT(2026, 10, 30, 16, 45), cfg, 5))   # then it stops
        self.assertIn("pre-open starts at 9:00 AM", scan.tape_stop(DT(2026, 10, 30, 8, 50), cfg, 5))
        self.assertIn("--until", scan.tape_stop(DT(2026, 10, 30, 11, 0), cfg, 5, T(11, 0)))
        with self.assertRaises(scan.ScanError):
            scan.data_delay({"data_delay_minutes": "five"})

    def test_no_reads_while_no_bar_can_change(self):
        idle = scan.tape_idle
        self.assertEqual(idle(DT(2026, 10, 29, 9, 20), {}, 5), DT(2026, 10, 29, 9, 35))   # pre-open: 1st bar shows 9:35
        self.assertIsNone(idle(DT(2026, 10, 29, 9, 35), {}, 5))
        self.assertEqual(idle(DT(2026, 10, 29, 9, 20), {}, 0), DT(2026, 10, 29, 9, 30))   # no delay set
        self.assertIsNone(idle(DT(2026, 10, 30, 12, 3), {}, 5, DT(2026, 10, 30, 11, 58)))  # 12:00 not seen yet
        self.assertIsNone(idle(DT(2026, 10, 30, 13, 0), {}, 5))                            # started in the break
        self.assertEqual(idle(DT(2026, 10, 30, 12, 8), {}, 5, DT(2026, 10, 30, 12, 1)), DT(2026, 10, 30, 14, 35))
        self.clock[0] = DT(2026, 10, 30, 4, 5)                    # Fri 9:05 AM, in the pre-open
        code, text, err = run(["tape", "--data", self.data, "--config", self.cfg])
        self.assertEqual(code, 0, err)
        self.assertIn("9:05 AM PKT  no bar can change before 9:20 AM: no reads until then", text)
        self.assertIn("12:08 PM PKT  no bar can change before 2:35 PM: no reads until then", text)
        self.assertEqual((self.fetches[0], self.fetches[-1]), ("09:20", "16:35"))
        morning = [f for f in self.fetches if f < "14:00"]
        self.assertEqual((len(morning), morning[-1]), (84, "12:06"))   # the 12:06 read is asof 12:01: 12:00 seen
        self.assertEqual(len(self.fetches), 84 + 61)              # no read from 12:07 to 2:34 PM
        self.assertIn("Stopped. The tape has seen the 4:30 PM close.", text)
        self.fetches = []
        self.clock[0] = DT(2026, 10, 29, 4, 20)                   # Thu 9:20 AM, --until inside the wait
        code, text, _ = run(["tape", "--data", self.data, "--config", self.cfg, "--until", "09:30"])
        self.assertEqual((code, self.fetches), (1, []))
        self.assertIn("Stopped. It is 09:30, your --until time. 0 snapshots", text)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)                                          # the default psx/ paths land in the temp folder
        self.wl = os.path.join("psx", "watchlist.json")
        self.uni = os.path.join("psx", "universe.txt")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def config(self, **kw):
        path = os.path.join(self.d, "config.json")
        with open(path, "w") as fh:
            json.dump(kw, fh)
        return path

    def test_universe_daily_orb_end_to_end(self):
        code, text, _ = run(["universe", "--data", MARKET, "--now", "2026-10-29 09:00", "--offline"])
        self.assertEqual(code, 0)
        self.assertIn("PSX closing file of Wed 2026-10-28: 43 rows; names from 2026-10-29.zip (41 symbols).", text)
        self.assertIn("Kept 31 ordinary shares.", text)
        self.assertIn("Left out 12: 3 stock futures, 1 debt, 1 ETF, 1 GEM board share, 1 index future, 1 modaraba, "
                      "1 non-voting share, 1 preference share, 1 REIT, 1 right.", text)
        with open(self.uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 31)
        self.assertEqual(lines, sorted(lines))
        self.assertIn("DEMO|Demo Industries Limited|0818", lines)

        code, text, _ = run(["daily", "--data", MARKET, "--now", "2026-10-29 09:00", "--offline"])
        self.assertEqual(code, 0)
        self.assertIn("Closes through Wed 2026-10-28: 60 PSX closing files from 2026-08-04.", text)
        self.assertIn("31 symbols. 28 passed the floors (close Rs 10 or more, 500,000 shares or more on "
                      "2026-10-28). 28 scanned.", text)
        self.assertIn("  DEMO      98.40  RSI  75.7  20-day closing-high breakout (level 96.56)", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "failed", "source")},
                         {"created_by": "psx-scanner", "created_at": "2026-10-29T04:00:00Z", "mode": "daily",
                          "as_of": "2026-10-28 close", "universe": 31, "scanned": 28, "failed": 0,
                          "source": "psx-closing-files"})
        self.assertEqual([h["symbol"] for h in doc["hits"]], Daily.ORDER)

        code, text, _ = run(["orb", "--data", MARKET, "--now", "2026-10-29 10:16", "--min-price", "0", "--offline"])
        self.assertEqual(code, 0)
        self.assertIn("Tape: 23 screener snapshots, as of 9:32 AM to 10:16 AM PKT. Bars that ended by 10:16 AM count.",
                      text)                                       # the tape's snapshots after --now are not used
        self.assertIn("7 of 30 usable charts closed above their opening-range high by 10:16 AM:", text)
        self.assertIn("   9:50 AM  HUBC     163.16  range 161.35 to 162.41  +0.46% over   (bar 3)", text)
        self.assertIn("  10:00 AM  KEL        6.14  range 6.10 to 6.12      +0.33% over   (bar 5)", text)
        self.assertIn("  10:15 AM  DEMO     100.65  range 98.90 to 100.20   +0.45% over   (bar 8)", text)
        self.assertIn("about 5 minutes late, and a bar's high and low are only the prices the tape saw", text)
        self.assertIn("not a list of stocks to buy", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_at", "mode", "as_of", "universe", "scanned", "unusable",
                                              "source")},
                         {"created_at": "2026-10-29T05:16:00Z", "mode": "orb", "as_of": "2026-10-29 10:16 PKT",
                          "universe": 31, "scanned": 30, "unusable": 1, "source": "psx-screener-tape"})
        self.assertIn({"symbol": "DEMO", "name": "Demo Industries Limited", "sector": "0818", "setups": ["orb"],
                       "price": 100.65, "at": "10:15 AM", "bar_start": "10:10", "bar_index": 8, "or_high": 100.2,
                       "or_low": 98.9, "pct_over": 0.45}, doc["hits"])

        # the hits read back as a watchlist, in a replay of the tape file with --at
        code, text, _ = run(["orb", "--data", MARKET, "--tape", TAPE, "--at", "10:15", "--watchlist", self.wl,
                             "--min-price", "0", "--json", "--no-write", "--offline"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (7, 7, 6))   # DEMO: from 10:16 on
        self.assertEqual(out["tape_until"], "2026-10-29 10:14:00 PKT")

        # with the Rs 10 floor: KEL and CNERGY are left out
        code, text, _ = run(["orb", "--data", MARKET, "--now", "2026-10-29 10:16", "--offline", "--no-write"])
        self.assertIn("31 charts on the tape. 30 usable (PIOC has a missing bar). 28 scanned (2 under the price or "
                      "volume floor).", text)
        self.assertIn("6 of the 28 charts over the floors closed above their opening-range high by 10:16 AM:", text)
        late = os.path.join(self.d, "late.jsonl")
        for snap in scan.read_tape(TAPE):
            if snap["asof"] >= "2026-10-29 11:00":                # a tape started at 11:00
                scan.append_tape(late, snap)
        code, _, err = run(["orb", "--data", MARKET, "--tape", late, "--at", "10:15", "--offline", "--no-write"])
        self.assertIn("has no snapshots of Thu 2026-10-29 as of 10:15 AM PKT or before", err)

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--data", MARKET, "--now", "2026-10-31 10:15", "--offline"])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-31 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--data", MARKET, "--now", "2026-10-29 09:44", "--offline"])
        self.assertIn("after 9:50 AM", err)
        cfg = self.config(no_entries_after="10:00")
        code, _, err = run(["orb", "--data", MARKET, "--now", "2026-10-29 10:15", "--config", cfg, "--offline"])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        cfg = self.config(no_entries_after="15:00")                # 16:00 on a Friday, so 15:30 is fine
        code, _, err = run(["orb", "--data", MARKET, "--now", "2026-10-30 15:30", "--config", cfg, "--offline"])
        self.assertIn("No market tape for Fri 2026-10-30", err)
        code, _, err = run(["orb", "--data", MARKET, "--tape", TAPE, "--offline"])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--data", MARKET, "--now", "2026-10-29 10:10", "--symbols", "ILP", "--offline"])
        self.assertEqual(code, 1)                                 # no breakout, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 10:10 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        early = os.path.join(self.d, "early.jsonl")
        for s in scan.read_tape(TAPE)[:8]:                        # asof 09:32 to 09:46
            scan.append_tape(early, s)
        code, _, err = run(["orb", "--data", MARKET, "--tape", early, "--at", "10:15", "--offline", "--no-write"])
        self.assertIn("The tape reaches only 9:46 AM PKT", err)
        code, _, err = run(["daily", "--data", MARKET, "--universe", "nope.txt", "--offline"])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--data", os.path.join(self.d, "empty"), "--offline"])
        self.assertIn("The PSX site is off for this tool: save the PSX closing files", err)
        cfg = self.config(psx_site=True)
        code, _, err = run(["daily", "--data", os.path.join(self.d, "empty"), "--offline", "--config", cfg])
        self.assertIn("(--offline reads only the data folder)", err)
        code, _, err = run(["daily", "--data", MARKET, "--symbols", " , ", "--offline"])
        self.assertIn("no symbols to scan", err)
        code, _, err = run(["daily", "--data", MARKET, "--sessions", "20", "--offline"])
        self.assertIn("--sessions wants 30 or more", err)
        code, _, err = run(["orb", "--data", MARKET, "--now", "2026-10-29 10:15", "--or-minutes", "7", "--offline"])
        self.assertIn("--or-minutes wants a multiple of 5", err)
        code, _, err = run(["daily", "--data", MARKET, "--now", "soon", "--offline"])
        self.assertIn("--now wants a time like", err)

    def test_no_universe_file_yet(self):
        code, text, _ = run(["daily", "--data", MARKET, "--now", "2026-10-29 09:00", "--offline", "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("No psx/universe.txt yet: scanning the ordinary shares of the 2026-10-28 closing file", text)
        self.assertIn("31 symbols.", text)
        code, text, _ = run(["orb", "--data", MARKET, "--now", "2026-10-29 10:16", "--offline", "--no-write",
                             "--min-price", "0", "--json"])
        self.assertEqual((json.loads(text)["universe"], len(json.loads(text)["hits"])), (31, 7))
        only_tape = os.path.join(self.d, "data")                  # no closing file at all: every symbol on the tape
        scan.append_tape(scan.tape_path(only_tape, THU, "market"), scan.read_tape(TAPE)[0])
        for s in scan.read_tape(TAPE)[1:]:
            scan.append_tape(scan.tape_path(only_tape, THU, "market"), s)
        code, text, _ = run(["orb", "--data", only_tape, "--now", "2026-10-29 10:15", "--offline", "--no-write",
                             "--min-price", "0"])
        self.assertIn("No psx/universe.txt yet: scanning the symbols on the tape.", text)
        self.assertIn("31 charts on the tape.", text)
        code, _, err = run(["orb", "--data", only_tape, "--now", "2026-10-29 10:15", "--offline", "--no-write",
                            "--min-volume", "1000"])
        self.assertIn("--min-volume needs the closing file of the session before 2026-10-29", err)

    def test_todays_file_counts_only_after_the_close(self):
        def as_of(now):
            code, text, _ = run(["daily", "--data", MARKET, "--now", now, "--offline", "--json", "--no-write"])
            return json.loads(text)["as_of"]
        self.assertEqual(as_of("2026-10-29 09:00"), "2026-10-28 close")    # the 10-29 file is there, but too new
        self.assertEqual(as_of("2026-10-29 15:29"), "2026-10-28 close")
        self.assertEqual(as_of("2026-10-29 16:00"), "2026-10-29 close")
        self.assertEqual(as_of("2026-10-30 09:00"), "2026-10-29 close")
        code, text, _ = run(["universe", "--data", MARKET, "--now", "2026-10-29 16:00", "--offline", "--json",
                             "--no-write"])
        out = json.loads(text)
        self.assertEqual((out["date"], out["count"], out["out"]), ("2026-10-29", 31, None))
        self.assertEqual((out["sessions"], out["first_date"], out["not_traded_on_date"]),
                         (20, "2026-10-02", ["ILP"]))             # no trade on 10-29, but on 10-28: still kept
        code, text, _ = run(["daily", "--data", MARKET, "--now", "2026-11-05 09:00", "--offline", "--no-write"])
        self.assertIn("Note: the newest closing file is of Thu 2026-10-29, but PSX had a session on Wed 2026-11-04.",
                      text)                                       # old closes are never passed off as new
        code, text, _ = run(["daily", "--data", MARKET, "--now", "2026-10-29 09:00", "--offline", "--no-write"])
        self.assertNotIn("Note:", text)

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w") as fh:
            fh.write("# my list\nDEMO|Demo Industries Limited|0818\nogdc\n\nDEMO\n")
        self.assertEqual(scan.symbols_from_file(txt), ["DEMO", "OGDC", "DEMO"])
        self.assertEqual(scan.read_universe(txt)["DEMO"], {"name": "Demo Industries Limited", "sector": "0818"})
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "demo"}, {"symbol": "OGDC"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["DEMO", "OGDC"])
        code, text, _ = run(["daily", "--data", MARKET, "--watchlist", txt, "--now", "2026-10-29 09:00", "--json",
                             "--no-write", "--offline"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], [h["symbol"] for h in out["hits"]]), (2, 2, ["DEMO"]))
        code, text, _ = run(["daily", "--data", MARKET, "--limit", "3", "--now", "2026-10-29 09:00", "--json",
                             "--no-write", "--offline"])
        self.assertEqual(json.loads(text)["universe"], 3)
        code, text, _ = run(["daily", "--data", MARKET, "--symbols", "DEMO,NOPE", "--now", "2026-10-29 09:00",
                             "--no-write", "--offline"])
        self.assertIn("2 symbols, 1 with closes (1 had none).", text)

    def test_daily_json_and_a_30_session_window(self):
        code, text, _ = run(["daily", "--data", MARKET, "--now", "2026-10-29 09:00", "--json", "--no-write",
                             "--offline"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 7)
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")
        self.assertEqual(out["counts"], {"requested": 31, "fetched": 31, "illiquid": 3, "short": 0, "scanned": 28,
                                         "hits": 7})
        self.assertEqual((out["sessions"], out["first_date"], out["filters"]),
                         (60, "2026-08-04", {"min_price": 10.0, "min_volume": 500000}))
        code, text, _ = run(["daily", "--data", MARKET, "--now", "2026-10-29 09:00", "--sessions", "30",
                             "--no-write", "--offline"])
        self.assertIn("27 scanned (1 had under 30 closes).", text)
        for word in ("profit", "P&L", "return", "real time", "live", "guarantee"):
            self.assertNotIn(word, text)

    def test_the_site_fetches_a_missing_closing_file_once(self):
        data = os.path.join(self.d, "data")
        cfg = self.config(psx_site=True)
        calls = []

        def fake_get(url, timeout=30):
            calls.append(url)
            kind, name = url.rsplit("/", 2)[1:]                   # mkt_summary/<day>.Z or symbol_name/<day>.zip
            path = os.path.join(MARKET, kind, name)
            if not os.path.exists(path):                          # PSX: no session that day, or not out yet
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"<html>not found</html>"))
            with open(path, "rb") as fh:
                return fh.read()
        saved = scan.http_get, scan.utc_now
        scan.http_get = fake_get
        try:
            scan.utc_now = lambda: DT(2026, 10, 29, 4, 0)       # the real clock: Thu 9:00 AM in Karachi
            code, text, err = run(["universe", "--data", data, "--config", cfg])
            self.assertEqual(code, 0, err)
            self.assertIn("Kept 31 ordinary shares.", text)
            self.assertIn("missing ones come from the PSX site, 1.5 s apart", err)
            got = [c.rsplit("/", 1)[1] for c in calls if "/mkt_summary/" in c]
            self.assertEqual((len(got), got[0], got[-1]), (20, "2026-10-28.Z", "2026-10-01.Z"))   # the newest 20
            names = [c for c in calls if "/mkt_summary/" not in c]
            self.assertEqual(names, ["https://dps.psx.com.pk/download/symbol_name/2026-10-28.zip"])   # one name file
            self.assertIn("no symbol name file from PSX for 2026-10-28 (404), so the short names are used", text)
            self.assertTrue(os.path.exists(os.path.join(data, "mkt_summary", "2026-10-28.Z")))
            run(["universe", "--data", data, "--config", cfg, "--no-write"])
            self.assertEqual(len([c for c in calls if "/mkt_summary/" in c]), 20)   # saved, so never fetched twice
            del calls[:]
            scan.utc_now = lambda: DT(2026, 11, 1, 4, 0)        # Sunday: Fri 10-30 is a 404, so Thu 10-29
            code, text, _ = run(["universe", "--data", data, "--config", cfg, "--no-write"])
            self.assertEqual([c.rsplit("/", 1)[1] for c in calls], ["2026-10-30.Z", "2026-10-29.Z", "2026-10-29.zip"])
            self.assertIn("PSX closing file of Thu 2026-10-29", text)
            self.assertIn("names from 2026-10-29.zip (41 symbols)", text)          # fetched, saved and used
            self.assertTrue(os.path.exists(os.path.join(data, "symbol_name", "2026-10-29.zip")))
            del calls[:]
            run(["universe", "--data", data, "--config", cfg, "--no-write"])
            self.assertNotIn("https://dps.psx.com.pk/download/symbol_name/2026-10-29.zip", calls)   # saved: once
            del calls[:]
            other = os.path.join(self.d, "other")
            code, _, err = run(["universe", "--data", other, "--config", cfg, "--offline"])
            self.assertEqual((code, calls), (1, []))              # --offline never fetches
            self.assertIn("--offline reads only the data folder", err)
            scan.append_tape(scan.tape_path(other, THU, "market"), scan.read_tape(TAPE)[0])
            for snap in scan.read_tape(TAPE)[1:]:
                scan.append_tape(scan.tape_path(other, THU, "market"), snap)
            for argv in (["universe"], ["daily"], ["orb", "--min-volume", "1", "--no-write"]):
                code, _, err = run(argv + ["--data", other, "--config", cfg, "--now", "2026-10-29 10:16"])
                self.assertEqual((code, calls), (1, []), argv)    # a replay with --now never fetches
                self.assertIn("(a run with --now reads only the data folder)", err, argv)
        finally:
            scan.http_get, scan.utc_now = saved

    def test_a_missing_file_and_a_share_that_did_not_trade(self):
        data = os.path.join(self.d, "data")
        shutil.copytree(os.path.join(MARKET, "mkt_summary"), os.path.join(data, "mkt_summary"))
        os.remove(os.path.join(data, "mkt_summary", "2026-10-27.Z"))
        code, text, _ = run(["daily", "--data", data, "--now", "2026-10-29 09:00", "--offline", "--no-write"])
        self.assertIn("Note: no closing file for Tue 2026-10-27, a PSX session by the calendar. Save it in "
                      "mkt_summary/. Closes are not scaled for a payout across a missing day.", text)
        code, text, _ = run(["daily", "--data", data, "--now", "2026-10-29 09:00", "--offline", "--no-write",
                             "--json"])
        self.assertEqual(json.loads(text)["missing_files"], ["2026-10-27"])
        # Friday's universe comes from the 10-29 file, and ILP did not trade that day: it is kept anyway
        code, text, _ = run(["universe", "--data", MARKET, "--now", "2026-10-30 09:00", "--offline"])
        self.assertEqual(code, 0)
        self.assertIn("PSX closing file of Thu 2026-10-29: 42 rows", text)
        self.assertIn("Kept 31 ordinary shares: 30 traded on 2026-10-29, 1 only in the 19 sessions before (ILP).", text)
        with open(self.uni, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 31)
        self.assertEqual([x for x in lines if x.startswith("ILP|")], ["ILP|%s|%s" % (
            sample_names()["ILP"][1], scan.symbol_info(
                scan.daily_rows(D(2026, 10, 28), MARKET, {}, True))["ILP"]["sector"])])   # name and sector of 10-28

    def test_a_friday_tape(self):
        def prices(t):
            if t.hour == 9 and t.minute == 21:
                return {"X": 100.4}
            return {"X": 100.6 if t.hour >= 14 else 100.1}
        data = os.path.join(self.d, "data")
        for s in synthetic_tape(FRI, [("09:17", "12:01"), ("14:32", "16:32")], prices):
            scan.append_tape(scan.tape_path(data, FRI, "market"), s)
        code, text, _ = run(["orb", "--data", data, "--now", "2026-10-30 15:00", "--min-price", "0", "--offline",
                             "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("15-minute range (9:15 AM to 9:30 AM)", text)
        self.assertIn("No psx/universe.txt yet: scanning the symbols on the tape.", text)
        self.assertIn("   2:35 PM  X        100.60  range 100.10 to 100.40  +0.20% over   (bar 33)", text)
        code, _, err = run(["orb", "--data", data, "--now", "2026-10-30 16:05", "--offline"])
        self.assertIn("after 16:00, your last time for new entries", err)   # 15:00 moved to the Friday close
        self.assertFalse(os.path.exists(self.wl))

    def test_universe_without_the_symbol_file_and_a_broken_file(self):
        data = os.path.join(self.d, "data")
        os.makedirs(os.path.join(data, "mkt_summary"))
        shutil.copy(os.path.join(MARKET, "mkt_summary", "2026-10-28.Z"), os.path.join(data, "mkt_summary"))
        code, text, _ = run(["universe", "--data", data, "--now", "2026-10-29 09:00", "--offline"])
        self.assertEqual(code, 0)
        self.assertIn("43 rows; no symbol name file, so the short names are used.", text)
        self.assertIn("Kept 31 ordinary shares.", text)
        with open(self.uni, encoding="utf-8") as fh:
            self.assertIn("DEMO|Demo Industries|0818", fh.read().splitlines())
        with open(os.path.join(data, "mkt_summary", "2026-10-27.Z"), "wb") as fh:
            fh.write(b"PK\x03\x04 not really a zip")
        code, _, err = run(["daily", "--data", data, "--now", "2026-10-29 09:00", "--offline"])
        self.assertEqual(code, 1)
        self.assertIn("2026-10-27.Z: a broken zip, not a PSX closing file", err)
        self.assertNotIn("Traceback", err)

    def test_the_skill_example_is_the_tool_output(self):
        with open(os.path.join(os.path.dirname(scan.__file__), "SKILL.md"), encoding="utf-8") as fh:
            example = fh.read().split("## Example", 1)[1].split("\n## ", 1)[0]
        runs = 0
        for block in re.findall(r"```\n(.*?)```", example, re.S):
            cmd, want = None, []
            for line in block.splitlines() + ["python3 scan.py END"]:
                if line.startswith("python3 scan.py "):
                    if cmd:
                        argv = [MARKET if x == "tests/fixtures/psx-market" else x for x in shlex.split(cmd)[2:]]
                        code, got, err = run(argv)
                        self.assertEqual(got.splitlines(), want, cmd)
                        runs += 1
                    cmd, want = line, []
                elif line.strip():
                    want.append(line)
        self.assertEqual(runs, 3)                                 # universe, daily, orb: as printed in SKILL.md


# ---------------------------------------------------------------- v2: the blocked list and swing style

DAY_TOOLS = "orb and tape are for day trades; your style is swing."   # SPEC v2 5.2, word for word
SKILL = os.path.join(os.path.dirname(scan.__file__), "SKILL.md")
THIS = os.path.abspath(__file__)                                       # this test file


class V2Folder(unittest.TestCase):
    """A temp project folder as the cwd, so the default psx/ paths land in it."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        self.wl = os.path.join("psx", "watchlist.json")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def config(self, **kw):
        path = os.path.join(self.d, "config.json")
        with open(path, "w") as fh:
            json.dump(kw, fh)
        return path

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)


class Blocked(V2Folder):
    """D34: the shares on the config's "blocked" list are never hits, in both styles. One line says which."""

    def daily(self, cfg, *more):
        return run(["daily", "--data", MARKET, "--now", "2026-10-29 09:00", "--offline", "--config", cfg] + list(more))

    def orb(self, cfg, *more):
        return run(["orb", "--data", MARKET, "--now", "2026-10-29 10:16", "--min-price", "0", "--offline",
                    "--config", cfg] + list(more))

    def test_a_blocked_share_is_not_a_daily_hit(self):
        code, text, err = self.daily(self.config(blocked=["DEMO"]))
        self.assertEqual(code, 0, err)
        lines = text.splitlines()
        at = lines.index("Left out 1 on your blocked list: DEMO.")
        self.assertEqual(lines[at - 1:at + 2], [
            "6 charts matched a setup. By setup: 20-day closing-high breakout 1, bull flag 1, back over the 20-day "
            "average 1, oversold (RSI under 30) 2, oversold bounce 1.",
            "Left out 1 on your blocked list: DEMO.", "Most traded first (close x volume):"])
        self.assertIn("31 symbols. 28 passed the floors (close Rs 10 or more, 500,000 shares or more on 2026-10-28). "
                      "28 scanned.", lines)                         # DEMO is still read: the counts stay the same
        self.assertNotIn("  DEMO ", text)
        self.assertIn("Wrote psx/watchlist.json (mode daily, 6 hits).", lines)
        doc = self.read(self.wl)
        self.assertEqual([h["symbol"] for h in doc["hits"]], [s for s in Daily.ORDER if s != "DEMO"])
        self.assertEqual(doc["blocked_left_out"], ["DEMO"])
        self.assertEqual(list(doc)[-4:], ["filters", "blocked_left_out", "note", "hits"])
        self.assertEqual((doc["universe"], doc["scanned"], doc["failed"]), (31, 28, 0))
        check_words(self, text)

    def test_the_line_names_only_the_blocked_hits_most_traded_first(self):
        cfg = self.config(blocked="ogdc, demo,luck")               # one string, any case: the psx-budget rule
        code, text, _ = self.daily(cfg, "--json", "--no-write")
        out = json.loads(text)
        self.assertEqual(out["blocked_left_out"], ["LUCK", "DEMO"])  # OGDC matched nothing; LUCK traded more rupees
        self.assertEqual([h["symbol"] for h in out["hits"]], ["PSO", "PPL", "UBL", "TRG", "SEARL"])
        self.assertEqual(out["counts"], {"requested": 31, "fetched": 31, "illiquid": 3, "short": 0, "scanned": 28,
                                         "hits": 5})
        self.assertFalse(os.path.exists(self.wl))
        code, text, _ = self.daily(cfg, "--no-write")
        self.assertIn("Left out 2 on your blocked list: LUCK, DEMO.", text.splitlines())
        check_words(self, text)
        code, text, _ = self.daily(cfg, "--symbols", "DEMO,LUCK,PSO", "--show", "0")
        self.assertEqual(code, 0)
        self.assertIn("1 chart matched a setup. By setup: oversold bounce 1.\n"
                      "Left out 2 on your blocked list: LUCK, DEMO.\nMost traded first (close x volume):\n", text)
        code, text, _ = self.daily(cfg, "--symbols", "DEMO,LUCK")
        self.assertEqual(code, 1)                                   # every hit was blocked: nothing to show
        self.assertIn("No setups.\nLeft out 2 on your blocked list: LUCK, DEMO.\nWrote psx/watchlist.json (mode "
                      "daily, 0 hits).\n", text)
        self.assertEqual((self.read(self.wl)["hits"], self.read(self.wl)["blocked_left_out"]), ([], ["LUCK", "DEMO"]))
        check_words(self, text)

    def test_an_empty_list_or_no_blocked_hit_is_the_v1_scan(self):
        def once(argv):
            code, text, err = run(argv)
            with open(self.wl, encoding="utf-8") as fh:
                return code, text, err, fh.read()
        for cmd in (["daily", "--now", "2026-10-29 09:00"], ["orb", "--now", "2026-10-29 10:16", "--min-price", "0"]):
            base = cmd + ["--data", MARKET, "--offline"]
            v1 = once(base)                                         # no config at all: the v1 scan
            for cfg in ({}, {"blocked": []}, {"blocked": None}, {"blocked": ["SYS", "HBL"]}, {"blocked": ""}):
                got = once(base + ["--config", self.config(**cfg)])
                self.assertEqual(got, v1, (cmd[0], cfg))            # SYS and HBL are no hit: nothing to leave out
                self.assertNotIn("blocked_left_out", got[3])
                self.assertNotIn("blocked list", got[1])

    def test_a_blocked_share_is_not_an_orb_hit(self):
        code, text, err = self.orb(self.config(blocked=["DEMO"]))
        self.assertEqual(code, 0, err)
        self.assertIn("31 charts on the tape. 30 usable (PIOC has a missing bar). 30 scanned.\n"
                      "6 of 30 usable charts closed above their opening-range high by 10:16 AM:\n", text)
        self.assertIn("  10:10 AM  ENGROH   262.70  range 261.24 to 261.65  +0.40% over   (bar 7)\n"
                      "Left out 1 on your blocked list: DEMO.\nWrote psx/watchlist.json (mode orb, 6 hits).\n", text)
        self.assertNotIn("DEMO     100.65", text)
        doc = self.read(self.wl)
        self.assertEqual(doc["mode"], "orb")
        self.assertEqual([h["symbol"] for h in doc["hits"]], [h[1] for h in Orb.HITS if h[1] != "DEMO"])
        self.assertEqual(doc["blocked_left_out"], ["DEMO"])
        self.assertEqual(list(doc)[-4:], ["filters", "blocked_left_out", "note", "hits"])
        check_words(self, text)
        cfg = self.config(blocked=["DEMO", "HUBC"])
        code, text, _ = self.orb(cfg, "--json", "--no-write")
        out = json.loads(text)
        self.assertEqual((out["blocked_left_out"], out["counts"]["hits"], len(out["hits"])), (["HUBC", "DEMO"], 5, 5))
        code, text, _ = self.orb(cfg, "--symbols", "DEMO", "--no-write")
        self.assertEqual(code, 1)                                   # its only hit was blocked
        self.assertIn("No chart closed above its opening-range high by 10:16 AM.\n"
                      "Left out 1 on your blocked list: DEMO.\n", text)
        check_words(self, text)

    def test_a_bad_blocked_list_is_one_plain_line(self):
        self.assertEqual(scan.blocked_set(None), set())
        self.assertEqual(scan.blocked_set(" demo , ,LUCK"), {"DEMO", "LUCK"})
        self.assertEqual(scan.blocked_set(["demo", None, " ", "Luck "]), {"DEMO", "LUCK"})
        for bad in (5, {"DEMO": True}):
            cfg = self.config(blocked=bad)
            for argv in (["daily", "--now", "2026-10-29 09:00"], ["orb", "--now", "2026-10-29 10:16"]):
                code, text, err = run(argv + ["--data", MARKET, "--offline", "--config", cfg])
                self.assertEqual((code, text), (1, ""), argv)
                self.assertEqual(err, 'scan.py: "blocked" in psx/config.json must be a list of tickers, like '
                                      '["ABC", "XYZ"]\n', argv)
            self.assertFalse(os.path.exists(self.wl))
            code, _, err = run(["universe", "--data", MARKET, "--now", "2026-10-29 09:00", "--offline", "--config",
                                cfg, "--no-write"])
            self.assertEqual(code, 0, err)                          # the universe reads no blocked list

    def test_leaving_out_keeps_the_order_and_the_input(self):
        hits = [{"symbol": s, "close": 1.0} for s in ("PSO", "LUCK", "UBL", "DEMO")]
        before = json.dumps(hits)
        kept, left = scan.leave_out_blocked(hits, {"DEMO", "LUCK", "OGDC"})
        self.assertEqual(([h["symbol"] for h in kept], left), (["PSO", "UBL"], ["LUCK", "DEMO"]))
        self.assertEqual(json.dumps(hits), before)                  # the hits given are not changed
        self.assertEqual(scan.leave_out_blocked(hits, set()), (hits, []))
        self.assertEqual(scan.blocked_line([]), "")
        self.assertEqual(scan.blocked_line(["HUBC", "PPL", "UBL", "NBP", "KEL", "DEMO"]),
                         "Left out 6 on your blocked list: HUBC, PPL, UBL, NBP, KEL, DEMO.")   # SPEC v2 5.2


class Swing(V2Folder):
    """In swing style orb and tape print one note first, then run as usual. universe and daily do not change."""

    def test_orb_prints_the_note_first_and_still_runs(self):
        argv = ["orb", "--data", MARKET, "--now", "2026-10-29 10:16", "--min-price", "0", "--offline", "--no-write"]
        code, day, _ = run(argv + ["--config", self.config(style="day")])
        self.assertEqual(code, 0)
        self.assertNotIn(DAY_TOOLS, day)
        code, swing, err = run(argv + ["--config", self.config(style="swing")])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(swing, DAY_TOOLS + "\n" + day)              # one note, then the same scan
        check_words(self, swing)
        code, text, err = run(argv + ["--json", "--config", self.config(style="swing")])
        self.assertEqual(len(json.loads(text)["hits"]), 7)          # the JSON stays clean: the note goes to stderr
        self.assertEqual(err, DAY_TOOLS + "\n")
        code, text, err = run(["orb", "--data", MARKET, "--now", "2026-10-31 10:15", "--offline", "--config",
                               self.config(style="swing", blocked=["DEMO"])])
        self.assertEqual((code, text), (1, DAY_TOOLS + "\n"))       # a weekend: the note, then the v1 line
        self.assertIn("No session on Sat 2026-10-31 (weekend)", err)

    def test_tape_prints_the_note_first_and_still_runs(self):
        data = os.path.join(self.d, "data")
        code, text, err = run(["tape", "--data", data, "--config", self.config(style="swing")])
        self.assertEqual((code, text), (1, DAY_TOOLS + "\n"))       # the site is off: the v1 line, after the note
        self.assertIn('The tape needs "psx_site": true in psx/config.json', err)
        code, text, _ = run(["tape", "--data", data, "--config", self.config(style="day")])
        self.assertEqual((code, text), (1, ""))                     # day style: no note
        with open(SCREENER, encoding="utf-8") as fh:
            page = fh.read()
        clock = [DT(2026, 10, 29, 10, 20)]                          # UTC: 3:20 PM in Karachi

        def sleep(s):
            clock[0] += datetime.timedelta(seconds=s)

        def fetch():
            clock[0] += datetime.timedelta(seconds=1)
            return page

        def no_get(url, timeout=30):
            raise AssertionError("no web request in a test: " + url)
        saved = (scan.utc_now, scan.SLEEP, scan.fetch_screener, scan.http_get)
        scan.utc_now, scan.SLEEP, scan.fetch_screener, scan.http_get = (lambda: clock[0]), sleep, fetch, no_get
        try:
            code, text, err = run(["tape", "--data", data, "--config", self.config(style="swing", psx_site=True),
                                   "--quiet"])
        finally:
            scan.utc_now, scan.SLEEP, scan.fetch_screener, scan.http_get = saved
        self.assertEqual(code, 0, err)
        lines = text.splitlines()
        self.assertEqual(lines[0], DAY_TOOLS)                       # --quiet does not hide it
        self.assertTrue(lines[1].startswith("Recording the PSX screener every 120 s into "), lines[1])
        self.assertIn("Stopped. The tape has seen the 3:30 PM close. 9 snapshots in", text)
        self.assertEqual(len(scan.read_tape(os.path.join(data, "tape", "2026-10-29", "market.jsonl"))), 9)
        check_words(self, text)

    def test_universe_and_daily_are_the_same_in_swing(self):
        for argv in (["universe", "--now", "2026-10-29 09:00"], ["daily", "--now", "2026-10-29 09:00"],
                     ["daily", "--now", "2026-10-29 16:00", "--json"]):
            base = argv + ["--data", MARKET, "--offline", "--no-write"]
            day = run(base + ["--config", self.config(style="day")])
            swing = run(base + ["--config", self.config(style="swing")])
            self.assertEqual(swing, day, argv)
            self.assertNotIn(DAY_TOOLS, swing[1] + swing[2])

    def test_a_bad_style_is_one_plain_line(self):
        cfg = self.config(style="Swing")
        replay = ["--offline", "--now", "2026-10-29 10:16"]
        for argv in (["universe"] + replay, ["daily"] + replay, ["orb"] + replay, ["tape"]):
            code, text, err = run(argv + ["--data", MARKET, "--config", cfg])
            self.assertEqual((code, text), (1, ""), argv)
            self.assertEqual(err, "scan.py: \"style\" in psx/config.json is \"day\" or \"swing\", not 'Swing'\n", argv)
        self.assertFalse(os.path.exists("psx"))                     # nothing was written


class SkillPage(V2Folder):
    """The v2 words of the skill page (SPEC v2 5.2), and its blocked-list example is the tool's output."""

    def page(self):
        with open(SKILL, encoding="utf-8") as fh:
            return fh.read()

    def test_the_page_has_the_new_lines(self):
        page = self.page()
        for line in ("Left out 3 on your blocked list: HUBC, UBL, PPL.", DAY_TOOLS,
                     "## Your blocked list", "## Swing style", "Swing trading loses money for many people too.",
                     "Run `scan.py daily` after the close, once the day's closing file is out."):
            self.assertIn(line, page)
        self.assertEqual(page.count("## Example"), 1)               # the v1 doc test reads the first one only

    def test_the_blocked_list_example_is_the_tool_output(self):
        section = self.page().split("## Your blocked list", 1)[1].split("\n## ", 1)[0]
        named = re.search(r'with `("blocked": \[[^\]]*\])` in psx/config.json', section).group(1)
        cfg = json.loads("{%s}" % named)
        self.assertEqual(cfg, {"blocked": ["LUCK", "DEMO"]})
        os.makedirs("psx")
        with open(os.path.join("psx", "config.json"), "w") as fh:
            json.dump(cfg, fh)
        code, _, err = run(["universe", "--data", MARKET, "--now", "2026-10-29 09:00", "--offline"])
        self.assertEqual(code, 0, err)                              # "after scan.py universe"
        blocks = re.findall(r"```\n(.*?)```", section, re.S)
        self.assertEqual(len(blocks), 1)
        lines = [x for x in blocks[0].splitlines() if x.strip()]
        self.assertTrue(lines[0].startswith("python3 scan.py daily "))
        argv = [MARKET if x == "tests/fixtures/psx-market" else x for x in shlex.split(lines[0])[2:]]
        code, got, err = run(argv + ["--offline"])
        self.assertEqual((code, got.splitlines()), (0, lines[1:]), err)
        self.assertEqual(self.read(self.wl)["blocked_left_out"], ["LUCK", "DEMO"])
        self.assertIn('psx/watchlist.json has `"blocked_left_out": ["LUCK", "DEMO"]`.', section)
        check_words(self, got)

    # F06 (the v2 review): "Try it on paper first." said nothing about where. Turned on in a folder of real
    # shares, paper fills go into its psx/portfolio.json, next to the real shares, and change the real cash.
    PAPER = ("Try it on paper first, in a folder of its own. Never turn paper on in a folder that holds real "
             "shares or real cash.")

    def swing_part(self):
        """The "## Swing style" part of the page, on one line."""
        return " ".join(self.page().split("## Swing style", 1)[1].split("\n## ", 1)[0].split())

    def test_paper_first_is_in_a_folder_of_its_own(self):
        """Each "paper first" on the page says where (a folder of its own) and where never (a folder with
        real shares or real cash). "## Swing style" also says why, and how to make that folder."""
        page = " ".join(self.page().split())
        self.assertEqual(re.findall(r"on paper first[^.]*\.", page), ["on paper first, in a folder of its own."] * 2)
        self.assertEqual(page.count(self.PAPER), 2)                 # the list at the top and "## Swing style"
        swing = self.swing_part()
        for words in (self.PAPER,
                      "A paper fill can go into that folder's `psx/portfolio.json`, next to your real shares. "
                      "Then its cash and shares no longer match your broker app.",
                      "To make the paper folder, copy `psx/config.json` and `psx/data/` into the new folder's "
                      '`psx/`. Set `"paper": true` and `"style": "swing"` there. Do not copy '
                      "`psx/portfolio.json`: it holds the real shares and cash.",
                      "Auto-run does not scan the paper folder. It scans only the trading folder you gave it at "
                      "install. In the paper folder, run the scans yourself: `scan.py universe` first and once "
                      "a week, `scan.py daily` after each close."):
            self.assertIn(words, swing)
        check_words(self, swing)
        top = self.page().split("\n# psx-scanner\n", 1)[1].split("`${CLAUDE_SKILL_DIR}`", 1)[0]
        self.assertIn(self.PAPER + ' See "Swing style" below.', " ".join(top.split()))
        check_words(self, top)

    def test_the_paper_folder_scans_like_the_trading_folder(self):
        """The page's way to make a paper folder works for the scanner. Its scans are the trading folder's
        scans, word for word (the blocked list comes with psx/config.json), and it has no psx/portfolio.json.
        Auto-run's scans run in the trading folder only: it knows a trading and a long-term folder."""
        trading, paper = os.path.join(self.d, "trading"), os.path.join(self.d, "paper")
        shutil.copytree(MARKET, os.path.join(trading, "psx", "data"))
        with open(os.path.join(trading, "psx", "config.json"), "w") as fh:
            json.dump({"psx_site": False, "paper": False, "blocked": ["LUCK", "DEMO"]}, fh)
        real = os.path.join(trading, "psx", "portfolio.json")
        with open(real, "w") as fh:                                  # made up: real shares and real cash
            json.dump({"cash": 36673.06, "positions": [{"ticker": "DEMO", "shares": 24}]}, fh)
        with open(real, "rb") as fh:
            book = fh.read()
        # the page's way: copy psx/config.json and psx/data/, set "paper" and "style", no psx/portfolio.json
        shutil.copytree(os.path.join(trading, "psx", "data"), os.path.join(paper, "psx", "data"))
        cfg = dict(self.read(os.path.join(trading, "psx", "config.json")), paper=True, style="swing")
        with open(os.path.join(paper, "psx", "config.json"), "w") as fh:
            json.dump(cfg, fh)
        got = []
        for folder in (trading, paper):
            os.chdir(folder)
            runs = [run([cmd, "--now", "2026-10-29 16:00", "--offline"]) for cmd in ("universe", "daily")]
            with open(self.wl, encoding="utf-8") as fh:
                got.append((runs, fh.read()))
            os.chdir(self.d)
        self.assertEqual(got[1], got[0])                            # the same words, the same psx/watchlist.json
        uni, daily = got[0][0]
        self.assertEqual((uni[0], daily[0]), (0, 0), uni[2] + daily[2])
        self.assertIn("Daily scan, Thu 2026-10-29 4:00 PM PKT. Closes through Thu 2026-10-29: 60 PSX closing files "
                      "from 2026-08-05.", daily[1].splitlines())    # after the close: that day's close is in
        self.assertIn("Left out 1 on your blocked list: DEMO.", daily[1].splitlines())
        self.assertEqual(sorted(os.listdir(os.path.join(paper, "psx"))),
                         ["config.json", "data", "universe.txt", "watchlist.json"])     # still no psx/portfolio.json
        with open(real, "rb") as fh:
            self.assertEqual(fh.read(), book)                       # the scans never touch the real shares file
        auto = load("psx-autorun", "autorun")
        self.assertEqual(sorted(auto.DEFAULTS["folders"]), ["longterm", "trading"])
        scans = [s["folder"] for job in auto.DEFAULT_JOBS.values() for s in job if s["script"] == "psx-scanner/scan.py"]
        self.assertTrue(scans)
        self.assertEqual(set(scans), {"trading"})                   # "It scans only the trading folder"


class ClosedFiles(unittest.TestCase):
    """scan.py and this test file close every file they open: each open is the head of a with block.
    A file left open makes Python print a ResourceWarning in the test output. That noise can hide a real one."""

    OPENERS = ("open", "urlopen", "ZipFile")                         # the calls here that open a file or a link

    def test_every_open_is_in_a_with_block(self):
        for path in (scan.__file__, THIS):
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            heads = set(id(item.context_expr) for node in ast.walk(tree) if isinstance(node, ast.With)
                        for item in node.items)
            left_open = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
                         and getattr(node.func, "id", getattr(node.func, "attr", None)) in self.OPENERS
                         and id(node) not in heads]
            self.assertEqual(left_open, [], "%s: open these files in a with block" % os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
