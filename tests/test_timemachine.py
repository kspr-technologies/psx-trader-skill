import datetime
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import tempfile
import time
import unittest
import urllib.error
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal, ROUND_HALF_UP

from _load import load, FIX, ROOT
from _words import check_words

tm = load("psx-timemachine", "timemachine")

SKILL_MD = os.path.join(ROOT, "psx-timemachine", "SKILL.md")
EXAMPLE_CFG = os.path.join(ROOT, "psx-budget", "config.example.json")   # its costs block is the defaults
MARKET = os.path.join(FIX, "psx-market")        # the made-up sample market (read only)
BARS = os.path.join(MARKET, "bars", "DEMO.csv")  # 31 sessions of DEMO 5-minute bars, 2026-09-17 to 2026-10-29
NOW = "2026-10-29 10:20"                         # Karachi time, 5 minutes after the 10:15 AM entry
NOW_UTC = tm.parse_now(NOW)

# the EXAMPLE.md plan, as psx-exit writes it
PLAN = {"created_by": "psx-exit", "symbol": "DEMO", "side": "buy", "date": "2026-10-29", "setup": "orb",
        "entry": 100.65, "entry_time": "10:15 AM", "entry_bar": 8, "interval": "5m",
        "opening_range": {"high": 100.2, "low": 98.9, "minutes": 15},
        "stop": 99.55, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 101.75, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 1.1, "reward_risk": 1.0, "flat_by": "15:20", "session": "normal",
        "limits": {"lower": 88.56, "upper": 108.24, "ldcp": 98.4, "from": "company page"},
        "costs_per_share_est": 0.3586}

# fingerprints of this tool's own runs on the sample market (seed 19, --until 15:20): any change shows
BARS_BANDS, BARS_PATHS, BARS_TRADES = "4093e6ea8df09f08", "757ccc83807818fe", "6c68b69cdfba3be1"
BARS_HIST = {"lo": 98.52, "hi": 103.46,
             "bins": [120, 116, 176, 211, 323, 389, 489, 605, 681, 754, 794, 811, 810, 687, 617, 546, 461, 355,
                      259, 210, 147, 136, 85, 53]}
DAILY_BANDS, DAILY_PATHS = "17fd4b8f1a6df26b", "5c91f0e8de9227ad"
DAILY_HIST = {"lo": 98.2, "hi": 103.18,
              "bins": [103, 111, 154, 220, 318, 363, 494, 560, 620, 700, 735, 795, 770, 743, 618, 595, 498, 410,
                       324, 236, 171, 151, 84, 64]}
BANNED = ("profit", "P&L", "guarantee", "real time", "tick by tick")


def plain(tc, text):
    """Tool output never shows a P&L or a profit, and never says real time or live."""
    for word in BANNED:
        tc.assertNotIn(word, text)
    tc.assertIsNone(re.search(r"\blive\b", text, re.I))


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def spec_cost_r(entry, exit_price, risk, pct=0.15, min_share=0.03, tax=15, other=0.0066):
    """SPEC section 5 by hand (no tool code): buy and sell 1,000 shares, each leg's commission
    max(pct of the value, min_share a share) plus sales tax, plus the other charges, each leg
    rounded half up to 0.01 -> the round trip a share, in R."""
    def leg(price):
        value = Decimal(str(price)) * 1000
        fee = max(Decimal(str(pct)) / 100 * value, Decimal(str(min_share)) * 1000)
        cost = fee * (1 + Decimal(str(tax)) / 100) + Decimal(str(other)) / 100 * value
        return cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(leg(entry) + leg(exit_price)) / 1000 / risk


def plan(cfg=None, **kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw, "flags", cfg)


def machine(p=None, root=MARKET, now=NOW_UTC, **kw):
    kw.setdefault("offline", True)
    return tm.time_machine(p or plan(kw.get("cfg")), root, now, **kw)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


def bar_rows():
    """bars/DEMO.csv -> (header, rows), each row a list of strings."""
    with open(BARS, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    return lines[0], [ln.split(",") for ln in lines[1:] if ln.strip()]


class Folder(unittest.TestCase):
    """A temp data folder per test: copies of the sample market parts a test asks for."""

    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def data(self, closing=True, rows=None, header="datetime,open,high,low,close,volume", text=None, name="data"):
        root = os.path.join(self.d, name)
        os.makedirs(root)
        if closing:
            shutil.copytree(os.path.join(MARKET, "mkt_summary"), os.path.join(root, "mkt_summary"))
        if rows is not None or text is not None:
            os.makedirs(os.path.join(root, "bars"))
            with open(os.path.join(root, "bars", "DEMO.csv"), "w", encoding="utf-8") as fh:
                fh.write(text if text is not None else "\n".join([header] + [",".join(r) for r in rows]) + "\n")
        return root


def synth_rows(days, sessions):
    """5-minute rows that put the bars of `sessions` (lists of compact bars) on other `days`, slot by
    slot on each day's own PSX grid: a day with fewer slots takes the first ones."""
    out = []
    for day, D in zip(days, sessions):
        for (start, _), b in zip(tm.slot_spans(day, 5), D):
            out.append([start.strftime("%Y-%m-%d %H:%M:%S")] + ["%.2f" % b[k] for k in "ohlc"] + [str(b["v"] or 0)])
    return out


def normal_sessions():
    """The 24 full Mon to Thu sessions of bars/DEMO.csv before 2026-10-29, as compact bars."""
    days, _ = tm.bar_days("DEMO", MARKET, datetime.date(2026, 10, 29), NOW_UTC)
    pool, _, _ = tm.split_pool(days, datetime.date(2026, 10, 29), "normal")
    return [D for _, D in pool]


def fridays_before(day, n, cfg=None):
    """The n Friday sessions (PSX open, normal Friday hours) before `day`, oldest first."""
    out, d = [], day - datetime.timedelta(days=1)
    while len(out) < n:
        if d.weekday() == 4 and tm.session_kind(d, cfg) == "friday":
            out.append(d)
        d -= datetime.timedelta(days=1)
    return sorted(out)


def closing_rows(sym, before, n):
    """sym's rows in the newest n closing files before a date, read straight from the zip files (no
    tool code), oldest first: (date, open, high, low). A row with a zero price is left out."""
    folder = os.path.join(MARKET, "mkt_summary")
    out = []
    for name in sorted(x for x in os.listdir(folder) if x[:10] < before)[-n:]:
        with zipfile.ZipFile(os.path.join(folder, name)) as z:
            text = z.read(z.namelist()[0]).decode("ascii")
        for line in text.splitlines():
            f = line.split("|")
            if len(f) > 9 and f[1] == sym and float(f[4] or 0) > 0 and float(f[5] or 0) > 0 and float(f[6] or 0) > 0:
                out.append((name[:10], float(f[4]), float(f[5]), float(f[6])))
    return sorted(out)


class BarsExample(unittest.TestCase):
    """The EXAMPLE.md plan in bars mode: DEMO's own 5-minute bars from bars/DEMO.csv, to the plan's
    flat_by 3:20 PM (no --until: that is the default)."""

    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"], m["until_from"]),
                         (10000, 19, 2, "15:20", "flat_by"))
        self.assertEqual((m["pool_sessions"], m["bars_left"]), (24, 61))       # bar 8 to the 15:15 bar (bar 69)
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-09-17", "2026-10-28"))
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["p_green"]), (0.313, 0.2362, 0.4508, 0.5614))
        self.assertEqual((m["median_close"], m["p5_close"], m["p95_close"]), (100.85, 99.16, 102.67))
        # the R counts PSX costs: about 0.33R a trade here; green stays by price alone (above)
        self.assertEqual((m["avg_R"], m["avg_R_before_costs"], m["r_at_target"]), (-0.226, 0.102, 1.0))
        self.assertEqual((m["cost_R_stop"], m["cost_R_target"]), (0.326, 0.3295))
        self.assertEqual(m["cost_R_stop"], round(spec_cost_r(100.65, 99.55, 100.65 - 99.55), 4))
        self.assertEqual(m["cost_R_target"], round(spec_cost_r(100.65, 101.75, 100.65 - 99.55), 4))
        self.assertEqual(self.doc["costs"], {"from": "the pack's default costs", "settings": tm.COSTS})
        # psx-exit's costs_per_share_est 0.3586 is the same round trip at the stop, a share
        self.assertAlmostEqual(m["cost_R_stop"] * 1.1, PLAN["costs_per_share_est"], places=3)

    def test_bit_for_bit(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), BARS_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), BARS_PATHS)
        self.assertEqual(m["hist"], BARS_HIST)
        self.assertEqual(m["bands"][-1], [99.13, 100.15, 100.83, 101.56, 102.64])

    def test_the_mode_and_why(self):
        self.assertEqual(self.doc["mode"], "bars")
        self.assertEqual(self.doc["mode_why"], "There are 24 full Mon to Thu sessions of DEMO's 5-minute bars in "
                                               "bars/DEMO.csv before this day (20 needed).")
        data = self.doc["data"]
        self.assertEqual((data["interval"], data["sessions"], data["bars_from"]), ("5m", 24, "bars/DEMO.csv"))
        # the file has 30 past sessions: the 6 Fridays have other hours, so they stay out of a Thursday's pool
        self.assertEqual([x["date"] for x in data["other_hours_left_out"]],
                         ["2026-09-18", "2026-09-25", "2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23"])
        self.assertEqual(set(x["session"] for x in data["other_hours_left_out"]), {"friday"})
        self.assertEqual(data["short_sessions_left_out"], [])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["mode"], h["sessions"], h["triggered"]), ("bars", 24, 23))
        # green stays by price (19 targets); the average R counts each day's own costs
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (19, 4, 0, 19))
        self.assertEqual((h["avg_R"], h["avg_R_before_costs"]), (-1.67, 0.652))
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R_before_costs"]}
                for t in h["trades"]]
        self.assertEqual(sha(slim), BARS_TRADES)                    # the trades by price did not change
        self.assertEqual(h["trades"][0], {"date": "2026-09-17", "time": "10:00 AM", "entry": 90.75, "stop": 90.69,
                                          "target": 90.81, "outcome": "target", "R": -4.42, "R_before_costs": 1.0,
                                          "cost_R": 5.42})
        for t in h["trades"]:                                       # each day's costs, by hand
            out = t["target"] if t["outcome"] == "target" else t["stop"] if t["outcome"] == "stop" else None
            c = spec_cost_r(t["entry"], out, t["entry"] - t["stop"])
            self.assertEqual(t["cost_R"], round(c, 2), t["date"])
            self.assertAlmostEqual(t["R"], t["R_before_costs"] - c, delta=0.0051)
        self.assertEqual((min(t["cost_R"] for t in h["trades"]), max(t["cost_R"] for t in h["trades"])), (0.61, 8.16))

    def test_the_verdict(self):
        # 1R after costs of 0.326R breaks even when the target comes first in (1 + 0.326) / 2 = 66 % of the
        # replays that hit one side; it did in 57 %: once the costs count, that leans toward the stop
        self.assertEqual(self.doc["verdict"], "Leans toward the stop: at 1R after costs the target has to come "
                                              "first in 66 % of the replays that hit one side; it did in 57 % "
                                              "(target first 31.3 % vs stop first 23.6 %).")
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), round(even, 3), round(gap, 3)), (0.57, 0.663, -0.093))
        self.assertEqual(self.doc["verdict_notes"],
                         ["45.1 % of the replays hit neither by 3:20 PM; the price at 3:20 PM decides those."])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [100.65] * 5)
        self.assertEqual((len(m["bands"]), m["band_times"][0], m["band_times"][-1]), (62, "10:15", "15:20"))
        self.assertTrue(all(p[0] == 100.65 and len(p) == 62 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("DEMO", 100.65, 99.55, 101.75, 8, "10:15 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"], p["session"]), (1.1, 1.0, "15:20", "normal"))
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual(self.doc["created_by"], "psx-timemachine")

    def test_the_limits_come_from_the_plan(self):
        lim = self.doc["data"]["limits"]
        self.assertEqual((lim["lower"], lim["upper"], lim["from"], lim["used"]), (88.56, 108.24, "company page", True))
        self.assertIn("Price limits for the day: 88.56 to 108.24 (from the company page). No replay goes past them.",
                      tm.report(self.doc))

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("DEMO buy, Thu 2026-10-29: entry 100.65 at 10:15 AM (bar 8), stop 99.55, target 101.75 (1R, "
                     "risk 1.10 a share).", "Mode: bars. There are 24 full Mon to Thu sessions",
                     "10,000 replays of the rest of the session, 10:15 AM to 3:20 PM, the plan's flat-by time "
                     "(61 five-minute bars), seed 19.",
                     "The pool: its last 24 full Mon to Thu sessions, 2026-09-17 to 2026-10-28.",
                     "Costs: about 0.33R a trade (the pack's default costs), counted in the R and the verdict.",
                     "target first   31.3 %", "stop first     23.6 %", "neither        45.1 %   still open at 3:20 PM",
                     "green          56.1 %", "median 100.85, 90 % of replays between 99.16 and 102.67",
                     "  Average R: -0.226, costs included (R = the 1.10 risk: +0.67R at the target, -1.33R at the "
                     "stop).", "fired 23 times: 19 target, 4 stop, 0 neither. Average R -1.670, costs included.\n"
                     "  Costs: 0.61R to 8.16R a trade on those days (the closer the stop, the more R the costs are).",
                     self.doc["verdict"] + "\n45.1 % of the replays hit neither by 3:20 PM; the price at 3:20 PM "
                     "decides those.",
                     "Left out 6 sessions with other hours (Friday).", "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        plain(self, text)


class DailyExample(unittest.TestCase):
    """The EXAMPLE.md plan in daily mode: DEMO's last 60 daily bars, to the plan's flat_by 3:20 PM."""

    @classmethod
    def setUpClass(cls):
        cls.doc = machine(mode="daily")

    def test_sigma_day_is_parkinson(self):
        rows = closing_rows("DEMO", "2026-10-29", 60)        # read straight from the zip files
        self.assertEqual((len(rows), rows[0][0], rows[-1][0]), (60, "2026-08-04", "2026-10-28"))
        want = math.sqrt(sum(math.log(h / lo) ** 2 for _, _, h, lo in rows) / 60 / (4 * math.log(2)))
        m = self.doc["mc"]
        self.assertEqual(m["sigma_day"], round(want, 6))
        self.assertEqual(m["sigma_day"], 0.011585)
        self.assertEqual(m["sigma_step"], round(want / math.sqrt(72), 6))     # 72 bars in a Mon to Thu session
        self.assertEqual((m["steps_per_session"], m["pool_sessions"], m["pool_from"], m["pool_to"]),
                         (72, 60, "2026-08-04", "2026-10-28"))

    def test_the_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"], m["bars_left"]), (10000, 19, None, "15:20", 61))
        self.assertEqual((m["p_target"], m["p_stop"], m["p_neither"], m["p_green"]), (0.2943, 0.3098, 0.3959, 0.4937))
        self.assertEqual((m["median_close"], m["p5_close"], m["p95_close"]), (100.63, 98.89, 102.43))
        self.assertEqual((m["avg_R"], m["avg_R_before_costs"], m["cost_R_stop"]), (-0.344, -0.016, 0.326))
        # before costs this was "Close to a coin flip"; a coin flip loses once the costs count
        self.assertEqual(self.doc["verdict"], "Strongly toward the stop: at 1R after costs the target has to come "
                                              "first in 66 % of the replays that hit one side; it did in 49 % "
                                              "(target first 29.4 % vs stop first 31.0 %).")
        self.assertEqual(self.doc["verdict_notes"], [])

    def test_bit_for_bit(self):
        m = self.doc["mc"]
        self.assertEqual((sha(m["bands"]), sha(m["sample_paths"]), m["hist"]), (DAILY_BANDS, DAILY_PATHS, DAILY_HIST))
        self.assertEqual(m["bands"][0], [100.65] * 5)
        self.assertEqual(m["bands"][-1], [98.88, 99.92, 100.63, 101.39, 102.4])

    def test_the_daily_history(self):
        h = self.doc["history"]
        rows = closing_rows("DEMO", "2026-10-29", 60)
        sp, tp = (100.65 - 99.55) / 100.65, (101.75 - 100.65) / 100.65
        want = {"target_only": 0, "stop_only": 0, "both": 0, "neither": 0}
        for _, o, hi, lo in rows:
            s, t = math.floor(round(o * (1 - sp) * 100, 6)) / 100, math.floor(o * (1 + tp) * 100 + 0.5) / 100
            want["both" if lo <= s and hi >= t else "stop_only" if lo <= s else "target_only" if hi >= t else "neither"] += 1
        self.assertEqual(dict((k, h[k]) for k in want), want)
        self.assertEqual(want, {"target_only": 21, "stop_only": 13, "both": 1, "neither": 25})
        self.assertEqual((h["mode"], h["days"], h["rule"]), ("daily", 60, {"from": "open", "stop_pct": 1.0929,
                                                                          "target_pct": 1.0929}))

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("Mode: daily. You asked for it (--mode daily).",
                     "10,000 replays of the rest of the session, 10:15 AM to 3:20 PM, the plan's flat-by time "
                     "(61 five-minute steps), seed 19.",
                     "Costs: about 0.33R a trade (the pack's default costs), counted in the R and the verdict.",
                     "  Average R: -0.344, costs included (R = the 1.10 risk: +0.67R at the target, -1.33R at the "
                     "stop).",
                     "Daily mode is a simpler model", "DEMO's last 60 daily bars, 2026-08-04 to 2026-10-28.",
                     "sigma_day 1.16 % (from the daily highs and lows), so 0.136 % a step.", "A tape or a bars file with 20 or more Mon to Thu sessions gives DEMO's own "
                     "5-minute bars", "target first   29.4 %", "stop first     31.0 %",
                     "History: on each of those 60 days, the stop 1.09 % under the open and the target 1.09 % over "
                     "it:\n  target only 21, stop only 13, both 1, neither 25.",
                     "the order inside the day is not known", "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        self.assertEqual(text.count("simpler model"), 1)
        plain(self, text)


class EntryRuleInTheHistory(unittest.TestCase):
    """The history replay starts a trade only where psx-entry would: never on a bar that closes
    when a session ends (12:00 on a Friday, or the close), never outside the entry window."""

    RULE = {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0}

    def day(self, n, above_at):
        bars = [{"o": 99.5, "h": 100.0, "l": 99.0, "c": 99.5} for _ in range(n)]
        for i in above_at:                                  # closes above the 100.00 range high
            bars[i] = {"o": 99.9, "h": 100.6, "l": 99.9, "c": 100.5}
        return bars

    def grid(self, d, cfg=None):
        kind, spans, segs = tm.day_grid(d, cfg)
        ends = [e for _, e in spans]
        return ends, tm.no_entry_bars(d, ends, segs, cfg or {})

    def test_a_friday_noon_close_is_skipped_and_the_afternoon_counts(self):
        ends, skip = self.grid(datetime.date(2026, 10, 30))
        self.assertIn(32, skip)                             # the 11:55 bar closes at 12:00: the session ends
        self.assertIn(56, skip)                             # the 16:25 bar closes at the close
        self.assertNotIn(34, skip)
        D = self.day(57, [32, 34])
        h = tm.history_replay([("2026-10-23", D)], self.RULE, 56, ends, None, skip)
        self.assertEqual([t["time"] for t in h["trades"]], ["2:40 PM"])          # bar 34, after the break
        old = tm.history_replay([("2026-10-23", D)], self.RULE, 56, ends)       # without the rule: 12:00 PM
        self.assertEqual([t["time"] for t in old["trades"]], ["12:00 PM"])

    def test_no_trade_starts_after_no_entries_after(self):
        ends, skip = self.grid(datetime.date(2026, 10, 29))
        self.assertNotIn(65, skip)                          # the 14:55 bar closes at 15:00: still in the window
        self.assertIn(66, skip)                             # the 15:00 bar closes at 15:05: too late
        D = self.day(72, [66, 67])
        self.assertEqual(tm.history_replay([("2026-10-28", D)], self.RULE, 71, ends, None, skip)["triggered"], 0)
        self.assertEqual(tm.history_replay([("2026-10-28", D)], self.RULE, 71, ends)["triggered"], 1)
        ends2, off = self.grid(datetime.date(2026, 10, 29), {"no_entries_after": None})
        self.assertNotIn(66, off)                           # null turns that side of the window off
        self.assertIn(71, off)                              # but the close still cannot fill

    def test_a_bad_window_in_the_config_is_one_plain_error(self):
        with self.assertRaises(tm.TMError):
            self.grid(datetime.date(2026, 10, 29), {"no_entries_after": "3pm"})


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        for mode in ("bars", "daily"):
            a = machine(replays=2000, seed=5, mode=mode)["mc"]
            b = machine(replays=2000, seed=5, mode=mode)["mc"]
            self.assertEqual(a, b, mode)

    def test_another_seed_gives_other_numbers(self):
        for mode, pins in (("bars", (0.313, 0.2362, BARS_BANDS)), ("daily", (0.2943, 0.3098, DAILY_BANDS))):
            m = machine(seed=7, until="15:20", mode=mode)["mc"]
            self.assertNotEqual((m["p_target"], m["p_stop"], sha(m["bands"])), pins, mode)
            self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint, choice and random are stable since Python 3.2; the pinned runs above
        # are the real proof, these pin the first draws so a failure points at the random numbers
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(59)), 18)
        r = random.Random(19)
        self.assertEqual([round(r.random(), 12) for _ in range(4)],
                         [0.6771258268, 0.784911356087, 0.520466157203, 0.511491702493])

    def test_window_replays_and_mode(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        for kw, words in ((dict(replays=0), "--replays"), (dict(window=11), "--window"), (dict(mode="weekly"), "--mode")):
            with self.assertRaises(tm.TMError) as cm:
                machine(**kw)
            self.assertIn(words, str(cm.exception))


class Model(unittest.TestCase):
    def test_the_brownian_bridge(self):
        a, b, s = math.log(100.0), math.log(100.5), 0.002
        self.assertEqual(tm.bridge(a, b, s, 0.0, 0.0), (b, a))          # U = 1: the high and low are the ends
        hi, lo = tm.bridge(a, b, s, 0.9, 0.9)
        root = math.sqrt((b - a) ** 2 - 2 * s * s * math.log(0.1))
        self.assertAlmostEqual(hi, (a + b + root) / 2, places=12)
        self.assertAlmostEqual(lo, (a + b - root) / 2, places=12)
        for u1, u2 in ((0.3, 0.7), (0.999999, 0.5), (0.5, 0.999999)):
            hi, lo = tm.bridge(a, b, s, u1, u2)
            self.assertTrue(hi >= max(a, b) and lo <= min(a, b))

    def test_parkinson(self):
        bars = [{"high": 101.0, "low": 99.0}, {"high": 100.0, "low": 100.0}, {"high": 102.0, "low": 100.0}]
        want = math.sqrt((math.log(101 / 99.0) ** 2 + 0 + math.log(1.02) ** 2) / 3 / (4 * math.log(2)))
        self.assertAlmostEqual(tm.parkinson(bars), want, places=15)

    def test_a_step_that_touches_both_counts_as_the_stop(self):
        # one wide step: the high passes the target in almost every replay (see `far`), and so does the low
        # with the stop: a step that touches both is a stop, so only the few where the low stayed up are targets
        far = tm.simulate_daily(100.0, 1.0, 100.01, 1, 0.05, replays=200, seed=3)
        m = tm.simulate_daily(100.0, 99.99, 100.01, 1, 0.05, replays=200, seed=3)
        self.assertGreaterEqual(far["p_target"], 0.99)
        self.assertEqual((m["p_stop"], m["p_target"]), (0.995, 0.005))
        pool = [[], [(1.0, 1.10, 0.90, 1.0)]]           # bars mode: one bar that spans both
        m = tm.simulate(pool, 100.0, 95.0, 105.0, 0, 1, replays=50, seed=3, window=0)
        self.assertEqual((m["p_stop"], m["p_target"]), (1.0, 0.0))

    def test_the_draw_window_stays_inside_a_segment(self):
        segs = [(0, 33), (33, 57)]                       # a Friday: 09:15 to 12:00, then 14:30 to 16:30
        b = tm.draw_bounds(57, segs)
        # bar 33 (14:30) holds the move over the Juma break: it draws only itself, and no other bar draws it
        self.assertEqual((b[1], b[31], b[32], b[33], b[34], b[56]), ((1, 32), (1, 32), (1, 32), (33, 33), (34, 56), (34, 56)))
        self.assertEqual(tm.draw_bounds(72)[71], (1, 71))
        # bar 33 (14:30) draws only afternoon bars, bar 32 (11:55) only morning bars
        pool = [[(1.0, 1.0, 1.0, 1.0)]] + [[(1.0, 1.0, 1.0, 1.01 if i >= 33 else 0.99)] for i in range(1, 57)]
        up = tm.simulate(pool, 100.0, 50.0, 200.0, 32, 33, replays=300, seed=1, window=2, segs=segs)
        down = tm.simulate(pool, 100.0, 50.0, 200.0, 31, 32, replays=300, seed=1, window=2, segs=segs)
        self.assertEqual((up["p5_close"], up["p95_close"]), (101.0, 101.0))
        self.assertEqual((down["p5_close"], down["p95_close"]), (99.0, 99.0))

    def test_the_juma_gap_is_drawn_once_a_replay(self):
        # only slot 33 moves (+10 %, the gap over the break); every other slot is flat. Bars 33 to 35 use the
        # gap exactly once in every replay: before, a clamp drew it about 1.2 times a replay (0 to 3 times)
        segs = [(0, 33), (33, 57)]
        pool = [[(1.0, 1.0, 1.0, 1.0)]] + [[(1.0, 1.0, 1.0, 1.10 if i == 33 else 1.0)] for i in range(1, 57)]
        m = tm.simulate(pool, 100.0, 50.0, 200.0, 32, 35, replays=2000, seed=19, window=2, segs=segs)
        self.assertEqual((m["p5_close"], m["median_close"], m["p95_close"]), (110.0, 110.0, 110.0))
        m = tm.simulate(pool, 100.0, 50.0, 200.0, 33, 56, replays=500, seed=19, window=2, segs=segs)
        self.assertEqual((m["p5_close"], m["p95_close"]), (100.0, 100.0))    # after 14:30, never the gap
        # the window is cut at an edge, not clamped: for the last bar (71) slots 69, 70 and 71 are drawn a
        # third of the time each, so the middle close is slot 70's (a clamp drew slot 71 three times in five)
        pool = [[(1.0, 1.0, 1.0, 1.0)] for _ in range(69)] + [[(1.0, 1.0, 1.0, x)] for x in (0.99, 1.0, 1.01)]
        m = tm.simulate(pool, 100.0, 50.0, 200.0, 70, 71, replays=3000, seed=19, window=2)
        self.assertEqual((m["p5_close"], m["median_close"], m["p95_close"]), (99.0, 100.0, 101.0))
        self.assertEqual(m["cost_R_stop"], 0.0)                             # no costs given: none counted

    def test_no_price_goes_past_the_limits(self):
        # tight made-up limits 100.00 to 101.00: the stop 99.55 and the target 101.75 sit outside them
        tight = {"lower": 100.0, "upper": 101.0, "ldcp": 100.5, "from": "company page"}
        for mode in ("bars", "daily"):
            doc = machine(plan(limits=tight), replays=1000, until="15:20", mode=mode)
            m = doc["mc"]
            self.assertEqual((m["p_neither"], m["p_target"], m["p_stop"]), (1.0, 0.0, 0.0), mode)
            self.assertTrue(all(100.0 <= x <= 101.0 for p in m["sample_paths"] for x in p), mode)
            self.assertTrue(all(100.0 <= x <= 101.0 for b in m["bands"] for x in b), mode)
            self.assertEqual(doc["verdict"], "No replay reached the target or the stop by 3:20 PM: the price at "
                                             "3:20 PM decides every one.")

    def test_a_stop_on_the_limit_can_still_be_reached(self):
        edge = {"lower": 99.55, "upper": 108.24, "ldcp": 98.4, "from": "company page"}
        for mode in ("bars", "daily"):
            m = machine(plan(limits=edge), replays=2000, until="15:20", mode=mode)["mc"]
            self.assertGreater(m["p_stop"], 0.1, mode)
            self.assertGreaterEqual(min(x for p in m["sample_paths"] for x in p), 99.55, mode)

    def test_limits_are_computed_when_the_plan_has_none(self):
        doc = machine(plan(limits=None), replays=200)
        lim = doc["data"]["limits"]
        # the 10-29 closing file is saved: its LDCP 98.40 gives price_limits(98.40)
        self.assertEqual((lim["lower"], lim["upper"], lim["ldcp"], lim["from"]), (88.56, 108.24, 98.4, "computed"))
        self.assertIn("(computed from the LDCP 98.40 in the day's closing file)", "\n".join(tm.report(doc)))

    def test_an_entry_outside_the_limits_turns_them_off(self):
        doc = machine(plan(limits={"lower": 90.0, "upper": 100.0, "from": "company page"}), replays=200)
        self.assertFalse(doc["data"]["limits"]["used"])
        self.assertIn("outside the day's price limits 90.00 to 100.00", doc["warnings"][0])
        self.assertNotIn("Price limits for the day", "\n".join(tm.report(doc)))


class Bars(Folder):
    def test_the_bars_file_and_the_tape_agree_on_the_plan_day(self):
        day = datetime.date(2026, 10, 29)
        from_file = tm.compact(tm.bar_days("DEMO", MARKET, day, tm.parse_now("2026-10-29 16:00"))[0][day], day)
        snaps = tm.read_tape(os.path.join(MARKET, "tape", "2026-10-29", "DEMO.jsonl"))
        from_tape = tm.compact(tm.bars_from_tape(snaps, day, "DEMO"), day)
        self.assertEqual(len(from_file), 72)
        self.assertEqual([(b["o"], b["h"], b["l"], b["c"]) for b in from_file],
                         [(b["o"], b["h"], b["l"], b["c"]) for b in from_tape])
        self.assertEqual((from_file[8]["start"], from_file[8]["c"], from_file[8]["h"]), ("10:10", 100.65, 100.7))

    def test_only_what_closed_by_now_counts_on_the_plan_day(self):
        day = datetime.date(2026, 10, 29)
        today = tm.bar_days("DEMO", MARKET, day, NOW_UTC)[0][day]      # 10:20: bars 0 to 9 have closed
        self.assertEqual([i for i, b in enumerate(today) if b is not None], list(range(10)))

    def test_a_one_minute_file_gives_the_same_replays(self):
        header, rows = bar_rows()
        minute = []
        for r in rows:
            t = datetime.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
            o, h, lo, c = r[1:5]
            for i, (a, b, x, y) in enumerate(((o, h, o, o), (o, o, lo, o), (o, o, o, o), (o, o, o, o), (o, o, o, c))):
                hi_, lo_ = (max(a, b, x, y, key=float), min(a, b, x, y, key=float))
                minute.append([(t + datetime.timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S"), a, hi_, lo_, y,
                               str(int(r[5]) // 5)])
        root = self.data(rows=minute)
        a = machine(root=root, replays=500, until="15:20")
        b = machine(replays=500, until="15:20")
        self.assertEqual(a["mc"], b["mc"])
        self.assertEqual(a["history"], b["history"])

    def test_the_tape_fills_in_when_there_is_no_bars_file(self):
        root = self.data()
        header, rows = bar_rows()
        for r in rows:            # four company-page snapshots a bar: open, high, low, close
            t = datetime.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
            path = tm.tape_path(root, t.date(), "DEMO")
            for i, price in ((0, r[1]), (1, r[2]), (2, r[3]), (4, r[4])):
                _append(path, t + datetime.timedelta(minutes=i), price)
        doc = machine(root=root, replays=500, until="15:20")
        self.assertEqual((doc["mode"], doc["data"]["bars_from"]), ("bars", "the DEMO tape"))
        self.assertEqual(doc["mc"], machine(replays=500, until="15:20")["mc"])

    def test_a_gap_leaves_the_day_out(self):
        header, rows = bar_rows()
        gap = [r for r in rows if r[0] != "2026-09-17 10:00:00"]
        hole = [r[:4] + [""] + r[5:] if r[0] == "2026-09-22 11:00:00" else r for r in rows]   # no close
        for edited, day in ((gap, "2026-09-17"), (hole, "2026-09-22")):
            doc = machine(root=self.data(rows=edited, name=day), replays=200)
            self.assertEqual(doc["mc"]["pool_sessions"], 23)
            self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": day, "bars": 71, "of": 72}])
            self.assertNotIn(day, [t["date"] for t in doc["history"]["trades"]])
            self.assertIn("Left out 1 short session(s) (a bar is missing): %s." % day, "\n".join(tm.report(doc)))

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-28")
        self.assertNotIn("2026-10-29", [t["date"] for t in doc["history"]["trades"]])

    def test_fewer_than_20_sessions_goes_daily_or_is_refused(self):
        header, rows = bar_rows()
        root = self.data(rows=[r for r in rows if r[0] >= "2026-09-28"])           # 19 Mon to Thu sessions
        doc = machine(root=root, replays=200)
        self.assertEqual(doc["mode"], "daily")
        self.assertEqual(doc["mode_why"], "There are 19 full Mon to Thu sessions of DEMO's 5-minute bars in "
                                          "bars/DEMO.csv before this day; bars mode needs 20 full Mon to Thu sessions.")
        with self.assertRaises(tm.TMError) as cm:
            machine(root=root, mode="bars")
        self.assertIn("only 19 full Mon to Thu session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))
        twenty = self.data(rows=[r for r in rows if r[0] >= "2026-09-24"], name="twenty")
        self.assertEqual(machine(root=twenty, replays=200)["mc"]["pool_sessions"], 20)

    def test_no_bars_at_all_means_daily_mode(self):
        doc = machine(root=self.data(), replays=500, until="15:20")
        self.assertEqual(doc["mode"], "daily")
        self.assertIn("There are no 5-minute bars of DEMO in the data folder", doc["mode_why"])
        self.assertEqual(doc["mc"], machine(replays=500, until="15:20", mode="daily")["mc"])

    def test_a_bars_file_with_only_the_plan_day_says_so(self):
        header, rows = bar_rows()
        root = self.data(rows=[r for r in rows if r[0].startswith("2026-10-29")])
        doc = machine(root=root, replays=200)
        self.assertEqual(doc["mode"], "daily")
        self.assertEqual(doc["mode_why"], "There are no past sessions of DEMO's 5-minute bars in bars/DEMO.csv "
                                          "(only the plan's day); bars mode needs 20 full Mon to Thu sessions.")
        tape = self.data(name="tape")
        for r in rows:
            if r[0].startswith("2026-10-29"):
                _append(tm.tape_path(tape, datetime.date(2026, 10, 29), "DEMO"),
                        datetime.datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S"), r[4])
        self.assertIn("in the DEMO tape (only the plan's day)", machine(root=tape, replays=200)["mode_why"])

    def test_a_friday_plan_uses_friday_sessions_only(self):
        p = plan(date="2026-10-23", entry=94.9, stop=94.4, target=95.4, entry_time="10:00 AM", entry_bar=None,
                 limits=None, flat_by=None)                                 # no flat_by: to the close
        self.assertEqual((p["entry_bar"], p["session"]), (8, "friday"))     # the 09:55 bar closes at 10:00 AM
        doc = machine(p, now=tm.parse_now("2026-10-23 17:00"), replays=200)
        self.assertEqual(doc["mode"], "daily")
        self.assertIn("5 full Friday sessions", doc["mode_why"])
        self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"], doc["mc"]["steps_per_session"]), ("16:30", 48, 57))
        with self.assertRaises(tm.TMError) as cm:
            machine(p, now=tm.parse_now("2026-10-23 17:00"), mode="bars")
        self.assertIn("only 5 full Friday session(s)", str(cm.exception))

    def test_a_friday_plan_in_bars_mode(self):
        fridays = fridays_before(datetime.date(2026, 10, 30), 20)
        root = self.data(rows=synth_rows(fridays, normal_sessions()[:20]))
        p = plan(date="2026-10-30", entry_time="10:00 AM", entry_bar=None, limits=None, stop_rule="pct",
                 flat_by=None)
        later = tm.parse_now("2026-10-30 17:00")
        doc = machine(p, root=root, now=later, replays=500)
        m = doc["mc"]
        self.assertEqual((doc["mode"], m["pool_sessions"], m["pool_from"], m["pool_to"]),
                         ("bars", 20, fridays[0].isoformat(), "2026-10-23"))
        self.assertEqual((m["until"], m["bars_left"], len(m["band_times"])), ("16:30", 48, 49))
        i = m["band_times"].index("12:00")
        self.assertEqual(m["band_times"][i + 1], "14:35")           # no bar in the Juma break
        noon = machine(p, root=root, now=later, replays=200, until="12:00")["mc"]
        self.assertEqual((noon["bars_left"], noon["band_times"][-1]), (24, "12:00"))
        with self.assertRaises(tm.TMError) as cm:
            machine(p, root=root, now=later, until="13:00")
        self.assertIn("Juma break", str(cm.exception))

    def test_ramadan_hours(self):
        cfg = {"ramadan": {"start": "2026-10-26", "end": "2026-10-30"}}      # made up, to test the hours
        p = plan(cfg, entry_bar=None, limits=None, flat_by=None)
        self.assertEqual((p["session"], p["entry_bar"]), ("ramadan", 11))     # 09:15 grid: 10:10 is bar 11
        doc = machine(p, cfg=cfg, replays=300)
        m = doc["mc"]
        self.assertEqual((doc["mode"], m["until"], m["bars_left"], m["steps_per_session"]), ("daily", "13:30", 39, 51))
        self.assertEqual(m["sigma_step"], round(m["sigma_day"] / math.sqrt(51), 6))
        self.assertIn("still open at 1:30 PM", "\n".join(tm.report(doc)))
        with self.assertRaises(tm.TMError) as cm:
            plan(cfg)                                                         # entry_bar 8 is 09:55 here
        self.assertIn("disagrees with itself", str(cm.exception))

    def test_zero_price_rows_are_skipped_in_daily_mode(self):
        # NML's 2026-10-13 row has OPEN/HIGH/LOW 0 (no regular-board trade): not a price
        sessions, bars = tm.daily_data("NML", MARKET, datetime.date(2026, 10, 29), offline=True)
        self.assertEqual((len(sessions), len(bars)), (60, 59))
        self.assertNotIn(datetime.date(2026, 10, 13), [b["date"] for b in bars])
        rows = closing_rows("NML", "2026-10-29", 60)
        self.assertEqual([r[0] for r in rows], [b["date"].isoformat() for b in bars])

    def test_a_stock_that_seldom_trades_is_refused_in_daily_mode(self):
        root = os.path.join(self.d, "thin")
        os.makedirs(os.path.join(root, "mkt_summary"))
        folder = os.path.join(MARKET, "mkt_summary")
        for i, name in enumerate(sorted(os.listdir(folder))):    # bare .lis files; DEMO trades on 1 day in 4
            with zipfile.ZipFile(os.path.join(folder, name)) as z:
                lines = z.read(z.namelist()[0]).decode("ascii").splitlines()
            keep = [ln for ln in lines if i % 4 == 0 or "|DEMO|" not in ln]
            with open(os.path.join(root, "mkt_summary", name[:10] + ".lis"), "w") as fh:
                fh.write("\r\n".join(keep) + "\r\n")
        with self.assertRaises(tm.TMError) as cm:
            machine(root=root, mode="daily")
        self.assertIn("only 15 daily bar(s) of DEMO in the last 60 sessions before 2026-10-29 (2026-08-04 to "
                      "2026-10-28): it had no regular-board trade on the other days", str(cm.exception))
        self.assertNotIn("psx_site", str(cm.exception))               # the files are there: nothing to save

    def test_broken_bars_files_are_one_plain_error(self):
        for text, words in (("", "empty"), ("datetime,open,high,low,close\n10/29/2026 10:15,1,1,1,1\n", "month first"),
                            ("<html>not a file</html>\n", "needs a header")):
            root = self.data(closing=False, text=text, name="x%d" % len(text))
            with self.assertRaises(tm.TMError) as cm:
                machine(root=root)
            self.assertIn("DEMO.csv", str(cm.exception))
            self.assertIn(words, str(cm.exception))


def _append(path, asof, price):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"asof": asof.strftime("%Y-%m-%d %H:%M"), "source": "company", "symbol": "DEMO",
                             "price": float(price)}) + "\n")


class Plans(unittest.TestCase):
    def test_plan_checks(self):
        cases = [
            (dict(entry_time="10:17 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "from 9:35 AM to 3:25 PM on Thu 2026-10-29"),
            (dict(entry_time=None, entry_bar=71), "from 9:35 AM to 3:25 PM"),
            (dict(entry_bar=9), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "5-minute bars only"),
            (dict(stop=101.0), "stop < entry < target"),
            (dict(side="sell"), "long only"),
            (dict(symbol=""), "PSX symbol"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 29"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
            (dict(opening_range={"minutes": 360}), "shorter than the first segment"),
            (dict(date="2026-11-09"), "PSX has no session on Mon 2026-11-09 (PSX holiday: Iqbal Day)"),
            (dict(date="2026-10-31"), "(weekend)"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "10:15 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:15", entry_bar=None)["entry_bar"], 8)
        self.assertEqual(plan(entry_time="10:15am", entry_bar=None)["entry_bar"], 8)
        # a Friday: bar 0 is 09:15, the 11:55 bar (32) closes at 12:00, the 14:30 bar is bar 33
        fri = dict(date="2026-10-30", entry_bar=None, limits=None)
        self.assertEqual(plan(entry_time="12:00 PM", **fri)["entry_bar"], 32)
        self.assertEqual(plan(entry_time="2:35 PM", **fri)["entry_bar"], 33)
        with self.assertRaises(tm.TMError) as cm:
            plan(entry_time="2:30 PM", **fri)
        self.assertIn("outside the Juma break", str(cm.exception))

    def test_the_symbol_rules(self):
        self.assertEqual(plan(symbol="demo")["symbol"], "DEMO")
        self.assertEqual(plan(symbol="786")["symbol"], "786")        # PSX has symbols that start with a digit
        with self.assertRaises(tm.TMError):
            plan(symbol="DE MO")

    def test_until_cuts_the_replay(self):
        doc = machine(until="15:20", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("15:20", 61, "15:20"))
        self.assertIn("still open at 3:20 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="3:20 PM", replays=500)["mc"], m)
        self.assertEqual(machine(until="3:20", replays=500)["mc"], m)     # a bare 1 to 8 o'clock is p.m.
        self.assertIn("10:15 AM to 3:20 PM, your --until time (61 five-minute bars)", "\n".join(tm.report(doc)))
        close = machine(plan(flat_by=None), replays=500)                 # no flat_by in the plan: the close
        self.assertEqual((close["mc"]["until"], close["mc"]["until_from"], close["mc"]["bars_left"]),
                         ("15:30", "close", 63))
        self.assertIn("10:15 AM to 3:30 PM, the close (63 five-minute bars)", "\n".join(tm.report(close)))
        self.assertEqual(machine(until="15:30", replays=500)["mc"]["bars_left"], 63)   # --until beats flat_by
        for bad, words in (("10:15 AM", "nothing to replay"), ("15:35", "after the close"),
                           ("15:22", "5-minute grid"), ("noon", "Karachi time"), ("9:00", "before the first bar")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_the_plans_flat_by_is_the_default_end(self):
        # no --until: the plan's flat_by (psx-exit has moved it to the day's hours), the last bar closed by then
        m = machine(replays=300)["mc"]
        self.assertEqual((m["until"], m["until_from"], m["bars_left"]), ("15:20", "flat_by", 61))
        self.assertEqual(m, dict(machine(until="15:20", replays=300)["mc"], until_from="flat_by"))
        self.assertEqual(machine(plan(flat_by="15:22"), replays=100)["mc"]["until"], "15:20")    # off the grid
        self.assertEqual(machine(plan(flat_by="3:20 PM"), replays=100)["mc"]["until"], "15:20")
        self.assertEqual(machine(plan(flat_by="17:00"), replays=100)["mc"]["until"], "15:30")    # after the close
        # a Friday plan: psx-exit writes 16:20 (15:20 moved to Friday's 16:30 close); 12:40 is in the Juma break
        fri = dict(date="2026-10-30", entry_time="10:00 AM", entry_bar=None, limits=None)
        later = tm.parse_now("2026-10-30 17:00")
        doc = machine(plan(flat_by="16:20", **fri), now=later, replays=100)
        self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"]), ("16:20", 46))
        self.assertIn("10:00 AM to 4:20 PM, the plan's flat-by time (46 five-minute steps)", "\n".join(tm.report(doc)))
        self.assertEqual(machine(plan(flat_by="12:40", **fri), now=later, replays=100)["mc"]["until"], "12:00")
        for flat_by, words in (("10:15", "the plan's flat_by 10:15 leaves nothing to replay after the 10:15 AM "
                                         "entry"), ("9:00", "before the first bar closes"),
                               ("noon", "the plan's flat_by wants a Karachi time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(plan(flat_by=flat_by), replays=10)
            self.assertIn(words, str(cm.exception), flat_by)

    def test_the_entry_must_have_closed(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-29 10:12"))
        self.assertIn("has not happened yet", str(cm.exception))
        self.assertIn("the 10:10 bar (bar 8) closes at 10:15 AM on Thu 2026-10-29", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-29 10:15"), replays=200)["mc"]["pool_sessions"], 24)
        with self.assertRaises(tm.TMError):
            machine(plan(date="2026-11-02"))                               # a day that has not come yet

    def test_karachi_time_has_no_daylight_saving(self):
        for text in ("2026-03-10 10:15", "2026-07-01 10:15", "2026-11-10 10:15"):
            self.assertEqual(tm.parse_now(text), datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
                             - datetime.timedelta(hours=5))
        self.assertEqual(tm.parse_now("2026-10-29T05:20:00Z"), NOW_UTC)
        self.assertEqual(tm.to_pkt(NOW_UTC), datetime.datetime(2026, 10, 29, 10, 20))

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=101.0, stop=99.9, target=102.1), replays=200)
        self.assertIn("outside that bar's range 100.00 to 100.70", doc["warnings"][0])

    def test_stops_round_down_to_the_tick(self):
        rule = {"stop_rule": "or_mid"}
        self.assertEqual(tm.rule_stop(rule, 100.2, 98.9, 100.65), 99.55)          # (100.20 + 98.90) / 2
        self.assertEqual(tm.rule_stop(rule, 100.2, 98.91, 100.65), 99.55)         # 99.555 rounds down
        self.assertEqual(tm.rule_stop({"stop_rule": "or_low"}, 100.2, 98.9, 100.65), 98.9)
        self.assertEqual(tm.rule_stop({"stop_rule": "pct", "stop_pct": 1.0}, 0, 0, 100.65), 99.64)   # 99.6435
        self.assertEqual(tm.rule_stop({"stop_rule": "pct", "stop_pct": 1.0}, 0, 0, 100.0), 99.0)
        self.assertEqual(tm.round_down_tick(99.54999999999999), 99.55)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=98.9, target=102.4), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=99.9, target=102.15), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 0.7452, 2.0))

    def test_the_plans_own_opening_range_is_used_without_bars_that_day(self):
        before = tm.parse_now("2026-10-29 10:20")
        days, _ = tm.bar_days("DEMO", MARKET, datetime.date(2026, 10, 29), before)
        p = plan(stop_rule="auto")
        self.assertEqual(tm.resolve_rule(p, [])["stop_rule"], "or_mid")           # 100.2 and 98.9 from the plan
        self.assertEqual(tm.resolve_rule(dict(p, opening_range={"minutes": 15}), [])["stop_rule"], "pct")
        today = tm.compact(days[datetime.date(2026, 10, 29)], datetime.date(2026, 10, 29))
        self.assertEqual(tm.resolve_rule(dict(p, opening_range={"minutes": 15}), today)["stop_rule"], "or_mid")

    def test_a_setup_other_than_orb_skips_the_bars_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 24)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:20"}),
                         "No replay reached the target or the stop by 3:20 PM: the price at 3:20 PM decides every one.")
        self.assertIn("by the close", tm.verdict({"p_target": 0.0, "p_stop": 0.0}))

    def test_a_2r_target_is_judged_against_its_break_even(self):
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_costs_move_the_break_even(self):
        # break-even target share (1 + c) / (1 + R), c = the costs in R at the stop
        mc = {"p_target": 0.313, "p_stop": 0.2362, "r_at_target": 1.0, "cost_R_stop": 0.326}
        self.assertEqual(round(tm.lean(mc)[2], 3), 0.663)
        self.assertEqual(tm.verdict(mc), "Leans toward the stop: at 1R after costs the target has to come first in "
                                         "66 % of the replays that hit one side; it did in 57 % (target first 31.3 % "
                                         "vs stop first 23.6 %).")
        self.assertEqual(tm.verdict(dict(mc, cost_R_stop=0.0)),             # no costs: the old 1R line
                         "Leans toward the target: target first 31.3 % vs stop first 23.6 %.")
        # near the costed break-even, a 1R plan is "Close to break-even", not a coin flip
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.20)).startswith("Close to break-even: at 1R after "
                                                                                    "costs"))
        self.assertIn("at 2R after costs the target has to come first in 44 %",
                      tm.verdict(dict(mc, r_at_target=2.0)))
        self.assertEqual(tm.verdict(dict(mc, r_at_target=0.3, p_target=0.5, p_stop=0.1)),
                         "Costs eat the target: about 0.33R a trade is as much as the 0.3R target, so the plan loses "
                         "even when the target comes first (target first 50.0 % vs stop first 10.0 %).")

    def test_the_costs_settings_come_from_the_plan_or_the_config(self):
        free = {"commission_pct": 0, "commission_min_per_share": 0, "sales_tax_pct": 0, "other_charges_pct": 0}
        doc = machine(cfg={"costs": free}, replays=500, until="15:20")
        m = doc["mc"]
        self.assertEqual((m["cost_R_stop"], m["avg_R"]), (0.0, m["avg_R_before_costs"]))
        self.assertEqual(doc["costs"]["from"], "your costs settings")
        self.assertIn("Costs: under 0.01R a trade (your costs settings), counted in the R and the verdict.",
                      tm.report(doc))
        self.assertEqual(doc["history"]["avg_R"], doc["history"]["avg_R_before_costs"])
        # a broker that charges its minimum on one side of a same-day trade: the sell leg has no commission
        one = machine(cfg={"costs": {"day_trade_one_side": True}}, replays=500, until="15:20")["mc"]
        self.assertEqual(one["cost_R_stop"], round((180.26 + 6.57) / 1000 / 1.1, 4))
        # the plan's own block wins over the config's
        doc = machine(plan(costs=free), cfg={"costs": {"commission_pct": 1.0}}, replays=200)
        self.assertEqual((doc["costs"]["from"], doc["mc"]["cost_R_stop"]), ("the plan's costs settings", 0.0))
        with self.assertRaises(tm.TMError) as cm:
            machine(cfg={"costs": {"commission_pct": "a lot"}}, replays=10)
        self.assertIn('the "costs" block in psx/config.json: costs:', str(cm.exception))

    def test_2r_on_the_sample_market(self):
        # the example's entry and stop with the 2R target 102.85 (psx-exit's default)
        doc = machine(plan(target=102.85), until="15:20")
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"]), (2.0, 0.0571, 0.2366, 0.7063))
        # before costs the average R was +0.160 against a lean to the stop; costs of 0.33R a trade turn it
        self.assertEqual((m["avg_R"], m["avg_R_before_costs"], m["cost_R_stop"]), (-0.168, 0.16, 0.326))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R after costs the target has to come first in "
                                         "44 % of the replays that hit one side; it did in 19 % (target first 5.7 % "
                                         "vs stop first 23.7 %).")
        self.assertEqual(doc["verdict_notes"], [
            "70.6 % of the replays hit neither by 3:20 PM; the price at 3:20 PM decides those."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0] + "\n", text)
        self.assertIn("+1.67R at the target, -1.33R at the stop", text)
        daily = machine(plan(target=102.85), until="15:20", mode="daily")
        self.assertTrue(daily["verdict"].startswith("Strongly toward the stop: at 2R after costs"))
        self.assertEqual((daily["history"]["target_only"], daily["history"]["stop_only"]), (5, 14))


class Site(Folder):
    """Daily mode with "psx_site": true: missing closing files come from the PSX site (a fake here)."""

    def setUp(self):
        Folder.setUp(self)
        self.calls, self.sleeps, self.missing = [], [], set()
        self._get, self._sleep = tm.http_get, tm.SLEEP
        tm.SLEEP = self.sleeps.append

        def fake(url, timeout=30):
            self.calls.append(url)
            name = url.rsplit("/", 1)[1]
            path = os.path.join(MARKET, "mkt_summary", name)
            if name[:10] in self.missing or not os.path.exists(path):
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b"<html>not found</html>"))
            with open(path, "rb") as fh:
                return fh.read()
        tm.http_get = fake

    def tearDown(self):
        tm.http_get, tm.SLEEP = self._get, self._sleep
        Folder.tearDown(self)

    def test_the_site_fills_the_data_folder_once(self):
        root = self.data(closing=False)
        doc = machine(root=root, cfg={"psx_site": True}, offline=False, until="15:20", replays=500, mode="daily")
        self.assertEqual(self.calls[0], "https://dps.psx.com.pk/download/mkt_summary/2026-10-28.Z")
        self.assertEqual(len(self.calls), 60)          # 60 sessions back to 2026-08-04 (the plan has its limits)
        self.assertEqual(self.calls[-1], "https://dps.psx.com.pk/download/mkt_summary/2026-08-04.Z")
        self.assertEqual(len(os.listdir(os.path.join(root, "mkt_summary"))), 60)
        self.assertEqual(doc["mc"], machine(replays=500, until="15:20", mode="daily")["mc"])
        self.assertTrue(doc["data"]["site"])
        self.calls[:] = []
        machine(root=root, cfg={"psx_site": True}, offline=False, until="15:20", replays=100, mode="daily")
        self.assertEqual(self.calls, [])             # saved files are never fetched twice

    def test_a_404_day_is_skipped(self):
        self.missing.add("2026-10-27")
        root = self.data(closing=False)
        doc = machine(root=root, cfg={"psx_site": True}, offline=False, replays=200, mode="daily")
        self.assertEqual((doc["mc"]["pool_sessions"], doc["mc"]["pool_from"]), (60, "2026-08-03"))

    def test_offline_never_fetches(self):
        root = self.data(closing=False)
        with self.assertRaises(tm.TMError) as cm:
            machine(root=root, cfg={"psx_site": True}, offline=True, mode="daily")
        self.assertIn("(with --offline or --now it reads only the data folder)", str(cm.exception))
        self.assertEqual(self.calls, [])

    def test_a_replay_with_now_never_fetches(self):
        # --now replays a past time: the run reads only the data folder, even with the site on and no --offline
        root = self.data(closing=False)
        cfg, path = os.path.join(self.d, "config.json"), os.path.join(self.d, "plan.json")
        with open(cfg, "w") as fh:
            json.dump({"psx_site": True}, fh)
        with open(path, "w") as fh:
            json.dump(PLAN, fh)
        code, _, err = run(["run", "--plan", path, "--data", root, "--config", cfg, "--now", NOW, "--mode", "daily"])
        self.assertEqual(code, 1)
        self.assertIn("(with --offline or --now it reads only the data folder)", err)
        self.assertEqual(self.calls, [])
        machine(root=root, cfg={"psx_site": True}, offline=False, replays=100, mode="daily")
        self.assertEqual(len(self.calls), 60)          # without --now (here: the tool's own call) it fetches

    def test_site_off_says_what_to_save(self):
        root = self.data(closing=False)
        with self.assertRaises(tm.TMError) as cm:
            machine(root=root, cfg={}, offline=False, mode="daily")
        text = str(cm.exception)
        self.assertIn("only 0 daily bar(s) of DEMO before 2026-10-29", text)
        self.assertIn("The PSX site is off for this tool: save the PSX closing files of the last 60 sessions "
                      "(https://dps.psx.com.pk/download/mkt_summary/YYYY-MM-DD.Z)", text)
        self.assertIn('or set "psx_site": true in psx/config.json if PSX has given you permission.', text)
        self.assertEqual(self.calls, [])

    def test_a_server_error_is_one_plain_line(self):
        def broken(url, timeout=30):
            raise urllib.error.HTTPError(url, 503, "Busy", {}, io.BytesIO(b""))
        tm.http_get = broken
        with self.assertRaises(tm.TMError) as cm:
            machine(root=self.data(closing=False), cfg={"psx_site": True}, offline=False, mode="daily")
        self.assertIn("could not reach PSX", str(cm.exception))

    def test_the_polite_settings(self):
        self.assertEqual((tm.UA, tm.PAUSE, tm.PSX), ("Mozilla/5.0 (compatible; psx-trader-skill/1.0)", 1.5,
                                                     "https://dps.psx.com.pk"))


class Cli(Folder):
    def setUp(self):
        Folder.setUp(self)
        self.plan = os.path.join(self.d, "psx", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "psx", "timemachine.json")
        self.base = ["--plan", self.plan, "--data", MARKET, "--offline", "--now", NOW]

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run"] + self.base + ["--until", "15:20", "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   31.3 %", text)
        self.assertIn("Leans toward the stop: at 1R after costs the target has to come first in 66 %", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("Next: /psx-budget", text)
        plain(self, text)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history", "data") if k in doc), ["data", "history", "mc", "plan"])
        self.assertEqual((doc["created_by"], doc["mode"]), ("psx-timemachine", "bars"))
        self.assertTrue(doc["created_at"].endswith("Z"))
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"], sha(doc["mc"]["sample_paths"])),
                         (0.313, -0.226, BARS_PATHS))
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (23, -1.67))
        self.assertEqual(doc["costs"]["settings"], tm.COSTS)
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_json_output(self):
        code, text, _ = run(["run"] + self.base + ["--until", "15:20", "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.2362)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:21], "Leans toward the stop")
        self.assertEqual((doc["mc"]["avg_R"], doc["mc"]["cost_R_stop"]), (-0.226, 0.326))
        self.assertFalse(os.path.exists(self.out))

    def test_daily_mode_on_the_command_line(self):
        code, text, err = run(["run"] + self.base + ["--until", "15:20", "--mode", "daily"])
        self.assertEqual(code, 0, err)
        for line in ("Mode: daily.", "sigma_day 1.16 %", "target first   29.4 %", "target only 21, stop only 13"):
            self.assertIn(line, text)

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "demo", "--date", "2026-10-29", "--entry", "100.65", "--stop",
                               "99.55", "--target", "101.75", "--entry-time", "10:15 AM", "--data", MARKET,
                               "--offline", "--now", NOW, "--until", "15:20", "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.313, 0.5614))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")              # worked out from the day's own bars
        self.assertEqual(doc["data"]["limits"]["from"], "computed")       # flags carry no limits
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"]), (19, 4))
        code, _, err = run(["run", "--symbol", "DEMO", "--entry", "100.65", "--data", MARKET, "--offline"])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_default_paths(self):
        shutil.copytree(MARKET, os.path.join(self.d, "psx", "data"))
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("psx", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("psx", "timemachine.json")) as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_the_config_file_is_read(self):
        cfg = os.path.join(self.d, "psx", "config.json")
        with open(cfg, "w") as fh:
            json.dump({"holidays": {"2026-10-29": "a test closure"}}, fh)
        code, _, err = run(["run"] + self.base + ["--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("PSX has no session on Thu 2026-10-29 (PSX holiday: a test closure)", err)

    def test_errors_are_one_plain_line(self):
        bad_cfg = os.path.join(self.d, "bad.json")
        with open(bad_cfg, "w") as fh:
            fh.write("{not json")
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json"), "--offline"],
                     ["run"] + self.base + ["--until", "9:00"],
                     ["run"] + self.base + ["--now", "yesterday"],
                     ["run"] + self.base + ["--config", bad_cfg],
                     ["run"] + self.base + ["--config", bad_cfg.replace("bad", "none"), "--data", self.d],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run"] + self.base + ["--until", "15:20", "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertEqual(lines[0], "DEMO 2026-10-29, 2,000 replay paths from the 10:15 AM entry (seed 19, bars mode)")
        self.assertTrue(any(l.startswith("   101.75 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("    99.55 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("   100.65 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("10:15", text)
        self.assertIn("3:20", text)
        self.assertEqual(lines[-2:], ["Leans toward the stop: at 1R after costs the target has to come first in 66 % "
                                      "of the replays that hit one side; it did in 57 % (target first 31.3 % vs stop "
                                      "first 23.6 %).",
                                      "45.1 % of the replays hit neither by 3:20 PM; the price at 3:20 PM decides those."])
        code, again, _ = run(["chart"] + self.base + ["--until", "15:20"])
        self.assertEqual(again, text)

    def test_the_skill_md_examples_are_the_real_output(self):
        # SKILL.md's two Example blocks, word for word: the sample market, psx-budget's example config
        with open(SKILL_MD, encoding="utf-8") as fh:
            text = fh.read()
        blocks = re.findall(r"\*\*(Bars|Daily) mode\.\*\*.*?```\n(.*?)```", text, re.S)
        self.assertEqual([name for name, _ in blocks], ["Bars", "Daily"])
        for (name, block), extra in zip(blocks, ([], ["--mode", "daily"])):
            code, out, err = run(["run"] + self.base + ["--config", EXAMPLE_CFG] + extra)
            self.assertEqual(code, 0, err)
            self.assertEqual(out, block, name)
        self.assertIn("Costs: about 0.33R a trade (your costs settings), counted in the R and the verdict.", out)


# ---------------------------------------------------------------- swing mode (SPEC v2 section 2.4, Task 6)

SWING_PATH = os.path.join(FIX, "psx-v2", "contract", "plan-swing.json")    # psx-exit's swing plan (made up)
with open(SWING_PATH, encoding="utf-8") as _fh:
    SWING_PLAN = json.load(_fh)
SWING_NOW = "2026-10-28 17:00"                   # the evening before the buy day, Thu 2026-10-29
SWING_NOW_UTC = tm.parse_now(SWING_NOW)
# fingerprints of the DEMO swing run on the sample market (seed 19, 10,000 replays, 20 sessions)
SWING_BANDS, SWING_PATHS, SWING_WALKS = "aa24bb1f9324197c", "059ddd750de69ae8", "6ad3af2a315cd8de"
DAY_BLOCKS = "a692a015b8ba234f"                  # the two day Example blocks of the v1 SKILL.md, word for word
FLAT, GAP = (1.0, 1.0, 1.0, 1.0), (0.85, 0.85, 0.85, 0.85)


def swing_plan(cfg=None, **kw):
    raw = dict(SWING_PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw, "flags", cfg)


def swing(p=None, root=MARKET, now=SWING_NOW_UTC, **kw):
    kw.setdefault("offline", True)
    return tm.time_machine(p or swing_plan(kw.get("cfg")), root, now, **kw)


def swing_args(*more):
    return ["run", "--plan", SWING_PATH, "--data", MARKET, "--config", EXAMPLE_CFG, "--offline",
            "--now", SWING_NOW] + list(more)


class Recorder(random.Random):
    """random.Random that keeps every choice() it makes, to see which past day each session drew."""

    def __init__(self, seed):
        random.Random.__init__(self, seed)
        self.drawn = []

    def choice(self, seq):
        x = random.Random.choice(self, seq)
        self.drawn.append(x)
        return x


def by_replay(records, drawn):
    """Split the draws into one list a replay: a replay that ends in session s drew s days."""
    out, i = [], 0
    for r in records:
        out.append(drawn[i:i + r[1]])
        i += r[1]
    return out


def reference_replays(pool, base, entry, stop, target, closes, n, days, first, replays, seed):
    """The swing rules of SPEC 2.4 written out plainly with the canonical price_limits and
    trend_break, slow and simple, with the same draws (one random.choice a session)."""
    rng, out = random.Random(seed), []
    for _ in range(replays):
        prev, path, fill, opened, pending, rec = base, [float(x) for x in closes], None, False, False, None
        for d in range(1, days + 1):
            o, h, l, c = rng.choice(pool)
            lo, hi = first if d == 1 else tm.price_limits(prev)
            O, H, L, C = (min(max(prev * x, lo), hi) for x in (o, h, l, c))
            if d == 1:
                if O <= entry:
                    fill, opened = O, True
                    rec = (("gap", 1, O, O, True) if O <= stop else ("stop", 1, O, stop, True) if L <= stop
                           else ("target", 1, O, target, True) if H >= target else None)
                elif L <= entry:
                    fill, opened = entry, False
                    rec = ("stop", 1, entry, stop, False) if L <= stop else None
                else:
                    rec = ("nofill", 1, None, None, False)
            elif pending:
                rec = ("trend", d, fill, O, opened)
            elif O <= stop:
                rec = ("gap", d, fill, O, opened)
            elif O >= target:
                rec = ("target", d, fill, O, opened)
            elif L <= stop:
                rec = ("stop", d, fill, stop, opened)
            elif H >= target:
                rec = ("target", d, fill, target, opened)
            elif d == days:
                rec = ("time", d, fill, C, opened)
            if rec:
                break
            path.append(C)
            pending = tm.trend_break(path, n)[1]
            prev = C
        out.append(rec)
    return out


def demo_pool():
    """DEMO's swing pool and the real adjusted closes before the buy day, from the sample market."""
    sessions, bars = tm.daily_data("DEMO", MARKET, datetime.date(2026, 10, 29), offline=True)
    missing = tm.window_sessions([d for d, _ in sessions])[1]
    return tm.swing_pool(bars)[0], tm.adjusted_closes(bars, None, missing)


def made_up_market(root, prices, end=datetime.date(2026, 10, 28), names=None):
    """Closing files with DEMO rows only (made up, bare .lis text), on the PSX sessions up to `end`:
    prices is a list of (open, high, low, close, ldcp), oldest first; ldcp None leaves the field
    blank. names: {index: the short name}, for an ex flag. -> the dates used."""
    days, d = [], end
    while len(days) < len(prices):
        if tm.trading_day(d)[0]:
            days.append(d)
        d -= datetime.timedelta(days=1)
    days.reverse()
    folder = os.path.join(root, "mkt_summary")
    os.makedirs(folder, exist_ok=True)
    months = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    for i, (day, (o, h, lo, c, ldcp)) in enumerate(zip(days, prices)):
        line = "%02d%s%d|DEMO|0818|%s|%.2f|%.2f|%.2f|%.2f|100000|%s|||" % (
            day.day, months[day.month - 1], day.year, (names or {}).get(i, "Demo Industries"), o, h, lo, c,
            "" if ldcp is None else "%.2f" % ldcp)
        with open(os.path.join(folder, day.isoformat() + ".lis"), "w") as fh:
            fh.write(line + "\r\n")
    return days


def walk_by_hand(bars, entry, stop, target, n, trend_n, i):
    """One history walk from bar i, the rules in plain steps (no tool loop): the outcome."""
    sp, tp = (entry - stop) / entry, (target - entry) / entry
    e = bars[i]["open"]
    s, t = tm.round_down_tick(e * (1 - sp)), tm.round_half_up_tick(e * (1 + tp))
    closes = [b["close"] for b in bars]
    if bars[i]["low"] <= s:
        return "stop"
    if bars[i]["high"] >= t:
        return "target"
    for j in range(i + 1, i + n + 1):
        broke = tm.trend_break(closes[:j], trend_n)[1]          # the close the day before
        if j >= len(bars):
            return "trend" if broke else "ran_out"
        b = bars[j]
        for rule, met in (("trend", broke), ("gap", b["open"] <= s), ("target", b["open"] >= t),
                          ("stop", b["low"] <= s), ("target", b["high"] >= t)):
            if met:
                return rule
    return "time"


class Swing(Folder):
    """Swing mode (SPEC v2 2.4). psx-exit's swing plan for DEMO (tests/fixtures/psx-v2/contract/
    plan-swing.json) on the sample market: 10,000 replays of the buy day Thu 2026-10-29 and the 20
    sessions after it. Then the rules on made-up pools, made-up markets in temp folders, and the
    command line."""

    @classmethod
    def setUpClass(cls):
        cls.doc = swing()
        cls.text = "\n".join(tm.report(cls.doc))

    def test_a_swing_plan_picks_swing_mode_and_says_why(self):
        self.assertEqual(self.doc["mode"], "swing")
        self.assertEqual(self.doc["mode_why"], "A swing plan: the replays walk the next 20 sessions, one daily bar "
                                               "at a time.")
        self.assertIn("\nMode: swing. A swing plan: the replays walk the next 20 sessions, one daily bar at a "
                      "time.\n", self.text)
        p = self.doc["plan"]
        self.assertEqual((p["style"], p["entry"], p["stop"], p["target"], p["risk_per_share"], p["target_r"]),
                         ("swing", 99.38, 96.25, 105.64, 3.13, 2.0))
        self.assertEqual((p["max_hold_sessions"], p["trend_sessions"], p["exit_by"]), (20, 10, "2026-11-27"))
        self.assertEqual(self.doc["warnings"], [])

    def test_the_same_plan_and_seed_give_the_same_numbers(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["sessions"], m["end"]), (10000, 19, 20, "2026-11-27"))
        self.assertEqual(m["counts"], {"target": 3449, "stop": 3401, "gap": 277, "trend": 1978, "time": 1172,
                                       "nofill": 0})
        self.assertEqual((m["p_target"], m["p_stop"], m["p_gap"], m["p_trend"], m["p_time"], m["p_nofill"]),
                         (0.3449, 0.3401, 0.0277, 0.1978, 0.1172, 0.0))
        self.assertEqual((m["filled"], m["p_fill_open"]), (10000, 1.0))      # DEMO never opened 1% over its LDCP
        self.assertEqual((m["avg_R"], m["avg_R_before_costs"], m["r_at_target"]), (0.584, 0.698, 2.0))
        self.assertEqual((m["cost_R_stop"], m["cost_R_target"]), (0.1119, 0.1173))
        self.assertEqual((sha(m["bands"]), sha(m["sample_paths"]), sha(self.doc["history"]["walks"])),
                         (SWING_BANDS, SWING_PATHS, SWING_WALKS))
        self.assertEqual((m["bands"][0], m["bands"][-1]), ([97.14, 98.1, 98.71, 99.4, 100.21],
                                                           [94.76, 99.32, 102.51, 105.7, 110.56]))
        again = swing()
        self.assertEqual(again["mc"], m)
        self.assertEqual(again["history"], self.doc["history"])
        other = swing(seed=7, replays=2000)["mc"]
        self.assertNotEqual((other["p_target"], sha(other["bands"])), (m["p_target"], SWING_BANDS))
        self.assertEqual(sum(other["counts"][k] for k in ("target", "stop", "trend", "time", "nofill")), 2000)

    def test_the_spec_lines_and_their_order(self):
        lines = tm.report(self.doc)
        for line in ("Mode: swing. A swing plan: the replays walk the next 20 sessions, one daily bar at a time.",
                     "10,000 replays of the next 20 sessions, Thu 2026-10-29 to the exit-by day Fri 2026-11-27, "
                     "seed 19.",
                     "Each session is one of DEMO's own last 60 daily bars, drawn whole, so gaps between sessions are "
                     "in it.",
                     "A gap through the stop sells at the open, not at the stop.",
                     "Costs: about 0.11R a trade (the pack's default costs, commission on both sides), counted in the "
                     "R and the verdict."):
            self.assertIn(line, lines)
        text = self.text
        order = [text.index(x) for x in (
            "  target         34.5 %   out at 105.64, or at the open when it opens over it",
            "  stop           34.0 %   out at 96.25, or at the open when it opens under it (a gap: 2.8 %)",
            "  trend          19.8 %   out at the next open (the trend rule)",
            "  time           11.7 %   out at the close of Fri 2026-11-27 (the time stop)",
            "  no fill        0.0 %    the limit 99.38 did not fill on Thu 2026-10-29: the order ends at the close",
            "  Average R: +0.584 over the replays that filled, costs included (R = the 3.13 risk: +1.88R at the "
            "target, -1.11R at the stop, for a fill at 99.38).",
            "History: a buy at the open of each of those 60 days", self.doc["verdict"], "--seed 19")]
        self.assertEqual(order, sorted(order))
        self.assertIn("  target 10, stop 6 (0 gaps), trend 35, time 0, the data ran out 9.", text)
        self.assertEqual(self.doc["verdict"], "Leans toward the target: at 2R after costs the target has to come "
                                              "first in 37 % of the replays that hit one side; it did in 50 % (target "
                                              "first 34.5 % vs stop first 34.0 %).")
        self.assertEqual(self.doc["verdict_notes"], [])          # 31.5 % end by the trend rule or the time stop
        check_words(self, text)
        plain(self, text)

    def test_the_day_flags_are_refused(self):
        for kw, words in ((dict(mode="bars"), "--mode bars is for a day plan: a swing plan runs swing mode by itself"),
                          (dict(mode="daily"), "--mode daily is for a day plan"),
                          (dict(until="15:20"), "--until is for a day plan: a swing plan runs to its exit-by day")):
            with self.assertRaises(tm.TMError) as cm:
                swing(replays=10, **kw)
            self.assertIn(words, str(cm.exception))
        for more in (["--mode", "bars"], ["--mode", "daily"], ["--until", "15:20"], ["--sessions", "0"],
                     ["--sessions", "251"]):
            code, out, err = run(swing_args(*more))
            self.assertEqual((code, out), (1, ""), more)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
        self.assertIn("--sessions must be a whole number from 1 to 250, got 0", run(swing_args("--sessions", "0"))[2])
        # --sessions is for a swing plan; a day plan refuses it, and runs as before without it
        with self.assertRaises(tm.TMError) as cm:
            machine(sessions=5, replays=10)
        self.assertIn("--sessions is for a swing plan", str(cm.exception))
        self.assertEqual(machine(replays=200)["mode"], "bars")
        code, out, _ = run(swing_args("--mode", "auto", "--replays", "500"))
        self.assertEqual(code, 0)
        self.assertIn("Mode: swing.", out)

    def test_the_cli_run_matches_and_hands_off(self):
        code, out, err = run(swing_args())
        self.assertEqual(code, 0, err)
        self.assertEqual(out, self.text.replace("the pack's default costs", "your costs settings") + "\n" +
                         "Next: /psx-budget sizes it on the buy day, before you buy. Nothing was placed.\n")
        check_words(self, out)
        plain(self, out)
        code, out, _ = run(swing_args("--json", "--replays", "1000"))
        doc = json.loads(out)
        self.assertEqual((code, doc["mode"], doc["plan"]["style"]), (0, "swing", "swing"))
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["costs"]["settings"], dict(tm.COSTS, day_trade_one_side=False))

    # ------------------------------------------------ the rules on made-up pools: base 100, a limit buy at 101,
    # stop 97, target 110

    ARGS = dict(base=100.0, entry=101.0, stop=97.0, target=110.0, closes=[100.0] * 10, trend_n=10, days=21)

    def replays(self, pool, rng=None, n=2000, **kw):
        args = dict(self.ARGS, **kw)
        return tm.swing_replays(pool, args.pop("base"), args.pop("entry"), args.pop("stop"), args.pop("target"),
                                args.pop("closes"), args.pop("trend_n"), args.pop("days"), replays=n, seed=19,
                                rng=rng, **args)

    def test_the_fast_loop_follows_the_rules_word_for_word(self):
        # DEMO's own pool: the inner loop (float_limits, a window for the trend rule) and the rules written
        # plainly with the canonical price_limits and trend_break give the same replays, record for record
        pool, adj = demo_pool()
        args = (pool, 98.4, 99.38, 96.25, 105.64, adj, 10, 21, (88.56, 108.24))
        self.assertEqual(tm.swing_replays(*args, replays=3000, seed=19), reference_replays(*args, 3000, 19))
        tight = (pool, 98.4, 99.38, 98.0, 99.9, adj, 3, 6, (88.56, 108.24))        # many day-1 and trend exits
        self.assertEqual(tm.swing_replays(*tight, replays=3000, seed=5), reference_replays(*tight, 3000, 5))

    def test_one_big_gap_down_day_sells_at_the_open(self):
        # nine flat days and one that opens 15 % down (the lower limit stops it at -10 %): every replay that
        # meets it while held sells at that open, under the stop, for more than 1R; the others reach the time stop
        rng = Recorder(19)
        recs = self.replays([FLAT] * 9 + [GAP], rng=rng)
        met = 0
        for rec, drawn in zip(recs, by_replay(recs, rng.drawn)):
            outcome, session, fill, sold, opened = rec
            if drawn[0] == GAP:                     # on the buy day: it fills at that open, under the stop
                self.assertEqual(rec, ("gap", 1, 90.0, 90.0, True))
            elif GAP in drawn:
                met += 1
                self.assertEqual((outcome, session, fill, sold), ("gap", drawn.index(GAP) + 1, 100.0, 90.0))
                self.assertLess((sold - fill) / (101.0 - 97.0), -1)          # -2.5R, before costs
            else:
                self.assertEqual((outcome, session, fill, sold), ("time", 21, 100.0, 100.0))
        self.assertAlmostEqual(met / 2000.0, 0.9 * (1 - 0.9 ** 20), delta=0.03)
        mc = tm.swing_summary(recs, 101.0, 97.0, 110.0, tm.COSTS, 2000)
        self.assertEqual((mc["counts"]["stop"], mc["p_stop"], mc["p_target"]), (mc["counts"]["gap"], mc["p_gap"], 0.0))
        self.assertLess(mc["avg_R"], 0)

    def test_no_fill_when_every_day_opens_over_the_limit(self):
        recs = self.replays([(1.02, 1.04, 1.015, 1.03), (1.05, 1.06, 1.02, 1.04)])
        self.assertEqual(set(recs), {("nofill", 1, None, None, False)})
        mc = tm.swing_summary(recs, 101.0, 97.0, 110.0, tm.COSTS, 2000)
        self.assertEqual((mc["p_nofill"], mc["filled"], mc["avg_R"], mc["p_target"], mc["p_stop"]),
                         (1.0, 0, None, 0.0, 0.0))
        self.assertTrue(tm.swing_verdict(dict(mc, sessions=20), {"entry": 101.0}).startswith(
            "No replay filled: in every one the buy day opened and stayed over the limit 101.00"))

    def test_a_fill_at_the_limit_counts_only_the_stop_that_day(self):
        # the buy day opens over the limit and comes down to it: the high may have come first, so the target
        # does not count that day; a low under the stop does
        up = self.replays([(1.02, 1.12, 1.005, 1.0)], n=50, days=1)     # days=1: the buy day is the last too
        self.assertEqual(set(up), {("time", 1, 101.0, 100.0, False)})
        down = self.replays([(1.02, 1.12, 0.95, 1.0)], n=50, days=1)
        self.assertEqual(set(down), {("stop", 1, 101.0, 97.0, False)})
        # a fill at the open counts both sides that day, the stop first
        both = self.replays([(1.0, 1.12, 0.95, 1.0)], n=50, days=1)
        self.assertEqual(set(both), {("stop", 1, 100.0, 97.0, True)})
        high = self.replays([(1.0, 1.12, 0.99, 1.0)], n=50, days=1)
        self.assertEqual(set(high), {("target", 1, 100.0, 110.0, True)})

    def test_the_trend_rule_and_the_time_stop(self):
        # every close 0.5 % lower: the first close is under the 10 closes of 100 before it, so the trend rule
        # sells at the next open, in session 2
        trend = self.replays([(1.0, 1.001, 0.999, 0.995)], n=100)
        self.assertEqual(set(r[:2] for r in trend), {("trend", 2)})
        self.assertEqual(set(round(r[3], 4) for r in trend), {99.5})
        # flat closes are never under the lowest close before them: the time stop sells at the last close
        flat = self.replays([FLAT], n=100)
        self.assertEqual(set(flat), {("time", 21, 100.0, 100.0, True)})
        # a mix: the counts add up to the replays, and the trend exits come one session after the break
        mixed = self.replays([FLAT, (1.0, 1.004, 0.996, 0.996), (1.0, 1.005, 0.997, 1.004)], n=3000)
        mc = tm.swing_summary(mixed, 101.0, 97.0, 110.0, tm.COSTS, 3000)
        c = mc["counts"]
        self.assertEqual(c["target"] + c["stop"] + c["trend"] + c["time"] + c["nofill"], 3000)
        self.assertGreater(c["trend"], 0)
        self.assertGreater(c["time"], 0)
        self.assertEqual((c["stop"], c["target"], c["nofill"]), (0, 0, 0))
        self.assertTrue(all(r[1] >= 2 for r in mixed if r[0] == "trend"))
        self.assertTrue(all(r[1] == 21 for r in mixed if r[0] == "time"))
        self.assertAlmostEqual(mc["p_trend"] + mc["p_time"], 1.0, places=9)

    def test_the_price_limits_hold_in_every_session(self):
        # made-up days of +-20 %: no open, high, low or close goes past the limits from the close before
        pool = [(1.2, 1.25, 1.15, 1.2), (0.8, 0.85, 0.75, 0.8), (1.0, 1.2, 0.8, 1.0)]
        bands, paths = tm.swing_bands(pool, 100.0, 21, (90.0, 110.0), 19, 300)
        for path in paths:
            prev = 100.0
            for close in path:                  # the paths are rounded to 0.01, so allow 2 paisa
                lo, hi = tm.price_limits(prev)
                self.assertTrue(lo - 0.02 <= close <= hi + 0.02, (prev, close))
                prev = close
        recs = self.replays(pool, n=500, stop=50.0, target=500.0, closes=[100.0] * 2, trend_n=2)
        self.assertTrue(all(90.0 <= r[2] <= 110.0 for r in recs if r[2] is not None))

    def test_the_float_limits_are_price_limits(self):
        for cents in list(range(1, 30001)) + list(range(30001, 2000000, 997)):
            p = cents / 100.0
            self.assertEqual(tm.float_limits(p), tm.price_limits(p), p)

    # ------------------------------------------------ end to end in temp data folders

    def test_sessions_5_ends_at_the_5th_session(self):
        doc = swing(sessions=5, replays=3000)
        m = doc["mc"]
        self.assertEqual((m["sessions"], m["sessions_from"], m["end"]), (5, "flag", "2026-11-05"))
        self.assertEqual(m["band_dates"], ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04",
                                           "2026-11-05"])
        self.assertEqual((len(m["ended"]), sum(m["ended"]), len(m["bands"])), (6, 3000, 6))
        self.assertEqual(m["ended"][-1] >= m["counts"]["time"] > 0, True)    # every time stop is on 11-05
        self.assertEqual(doc["history"]["sessions"], 5)
        text = "\n".join(tm.report(doc))
        self.assertIn("3,000 replays of the next 5 sessions, Thu 2026-10-29 to Thu 2026-11-05 (your --sessions), "
                      "seed 19.", text)
        self.assertIn("out at the close of Thu 2026-11-05 (your --sessions)", text)
        self.assertIn("A swing plan: the replays walk the next 5 sessions, one daily bar at a time.", text)
        self.assertEqual(doc["warnings"], [])           # the plan's exit-by day is not a mismatch here
        # the records end in session 6 at the latest: the buy day and 5 sessions after it
        pool, adj = demo_pool()
        recs = tm.swing_replays(pool, 98.4, 99.38, 96.25, 105.64, adj, 10, 6, (88.56, 108.24), 3000, 19)
        self.assertEqual(max(r[1] for r in recs), 6)
        self.assertEqual(set(r[1] for r in recs if r[0] == "time"), {6})
        self.assertEqual(swing(sessions=20, replays=500)["mc"]["counts"], swing(replays=500)["mc"]["counts"])
        one = swing(sessions=1, replays=200)
        self.assertIn("replays of the next session, Thu 2026-10-29 to Fri 2026-10-30", "\n".join(tm.report(one)))

    def test_day_trade_one_side_changes_nothing_in_swing(self):
        base = swing(replays=3000)
        for cfg in ({"costs": {"day_trade_one_side": True}}, {"style": "swing"},
                    {"style": "swing", "costs": {"day_trade_one_side": True}}, {"style": "day"}):
            doc = swing(cfg=cfg, replays=3000)
            self.assertEqual(doc["mc"], base["mc"], cfg)
            self.assertIs(doc["costs"]["settings"]["day_trade_one_side"], False, cfg)
        # the commission counts on both sides: SPEC section 5 by hand, at the stop and at the target
        m = base["mc"]
        self.assertEqual(m["cost_R_stop"], round(spec_cost_r(99.38, 96.25, 99.38 - 96.25), 4))
        self.assertEqual(m["cost_R_target"], round(spec_cost_r(99.38, 105.64, 99.38 - 96.25), 4))
        # a day plan still honours "day_trade_one_side" (v1)
        self.assertNotEqual(machine(cfg={"costs": {"day_trade_one_side": True}}, replays=200)["mc"]["cost_R_stop"],
                            machine(replays=200)["mc"]["cost_R_stop"])

    def test_the_history_counts_add_up_and_the_data_runs_out_near_the_end(self):
        doc = swing(replays=200)
        h = doc["history"]
        self.assertEqual((h["days"], h["sessions"], h["rule"]), (60, 20, {"from": "open", "stop_pct": 3.1495,
                                                                          "target_pct": 6.2991, "trend_sessions": 10}))
        self.assertEqual((h["target"], h["stop"], h["gap"], h["trend"], h["time"], h["ran_out"]), (10, 6, 0, 35, 0, 9))
        self.assertEqual(h["target"] + h["stop"] + h["trend"] + h["time"] + h["ran_out"], h["days"])
        self.assertLessEqual(h["gap"], h["stop"])
        walks = h["walks"]
        self.assertEqual(len(walks), 60)
        out = [w["outcome"] for w in walks]
        ran = [i for i, x in enumerate(out) if x == "ran_out"]
        self.assertTrue(ran and all(i + 20 > 59 for i in ran), ran)       # only where 20 sessions do not fit
        self.assertEqual(ran, [50] + list(range(52, 60)))     # Fri 10-16 (51) reached its target on Wed 10-28
        self.assertEqual((walks[50]["date"], walks[51]["outcome"]), ("2026-10-15", "target"))
        self.assertTrue(all(x != "time" for x in out[40:]) and "ran_out" not in out[:40])
        # each walk by hand: the same outcome
        sessions, bars = tm.daily_data("DEMO", MARKET, datetime.date(2026, 10, 29), offline=True)
        abars = tm.adjusted_bars(bars, None, tm.window_sessions([d for d, _ in sessions])[1])
        self.assertEqual([walk_by_hand(abars, 99.38, 96.25, 105.64, 20, 10, i) for i in range(60)], out)
        five = swing(sessions=5, replays=100)["history"]
        self.assertEqual(sum(five[k] for k in ("target", "stop", "trend", "time", "ran_out")), 60)
        self.assertGreater(five["time"], 0)
        self.assertTrue(all(i + 5 > 59 for i, w in enumerate(five["walks"]) if w["outcome"] == "ran_out"))
        self.assertIn("  target 10, stop 6 (0 gaps), trend 35, time 0, the data ran out 9.",
                      "\n".join(tm.report(doc)))

    def test_a_payout_is_not_a_fall(self):
        # 60 made-up flat days at 100.00, then an XD of Rs 3.00 five sessions before the buy day: PSX lowers the
        # LDCP to 97.00. The pool takes each bar against its own LDCP, and the history and the trend rule use
        # adjusted prices: no stop, no gap and no trend exit come from the payout
        rows = [(100.0, 100.5, 99.5, 100.0, 100.0)] * 55 + [(97.0, 97.5, 96.5, 97.0, 97.0)] * 5
        root = os.path.join(self.d, "xd")
        made_up_market(root, rows, names={55: "Demo Industries XD"})
        p = swing_plan(entry=97.97, stop=95.0, target=103.91, last_close=97.0, limits=None)
        doc = swing(p, root=root, replays=2000)
        m, h = doc["mc"], doc["history"]
        self.assertEqual((m["pool_bars"], m["counts"]["stop"], m["counts"]["trend"]), (60, 0, 0))
        self.assertEqual(m["counts"]["time"], 2000)
        self.assertEqual((h["stop"], h["gap"], h["trend"], h["time"] + h["ran_out"]), (0, 0, 0, 60))
        # the raw prices would show the payout as a fall to the stop
        sessions, bars = tm.daily_data("DEMO", root, datetime.date(2026, 10, 29), offline=True)
        self.assertEqual(bars[55]["flags"], ["XD"])
        raw = tm.swing_history(bars, 97.97, 95.0, 103.91, 20, 10)
        self.assertGreater(raw["stop"], 0)
        self.assertEqual(doc["data"]["limits"]["from"], "computed")
        self.assertEqual((doc["data"]["limits"]["lower"], doc["data"]["limits"]["upper"]), tm.price_limits(97.0))

    # the buy day's company page (made up): DEMO is XD that day, a Rs 3.00 payout, so PSX's LDCP is 95.40, under
    # the last close 98.40 of Wed 10-28, and the CIRCUIT BREAKER is 85.86 to 104.94 (95.40 x 0.90 and x 1.10)
    XD = {"lower": 85.86, "upper": 104.94, "ldcp": 95.4, "from": "company page"}

    def test_an_ex_date_on_the_buy_day_starts_from_its_ldcp(self):
        # the pool is ratios to each bar's own LDCP, so the buy day starts from 95.40, not 98.40, and the real
        # closes before it are scaled by 95.40 / 98.40 (the trend rule). From 98.40 the ex-date changed nothing:
        # target 34.5 %, stop 34.0 %, "Leans toward the target"
        doc = swing(swing_plan(limits=self.XD))
        m = doc["mc"]
        pool, adj = demo_pool()
        moved = [x * (95.4 / 98.4) for x in adj]
        recs = tm.swing_replays(pool, 95.4, 99.38, 96.25, 105.64, moved, 10, 21, (85.86, 104.94), 10000, 19)
        want = tm.swing_summary(recs, 99.38, 96.25, 105.64, doc["costs"]["settings"], 10000)
        self.assertEqual(dict((k, m[k]) for k in want), want)
        self.assertEqual((m["base"], m["base_from"], doc["data"]["last_close"]), (95.4, "ldcp", 98.4))
        self.assertEqual((m["trend_level"], round(min(moved[-10:]), 2)), (89.54, 89.54))
        self.assertEqual(m["bands"], tm.swing_bands(pool, 95.4, 21, (85.86, 104.94), 19)[0])
        self.assertEqual(m["bands"][0], [94.18, 95.11, 95.7, 96.37, 97.16])
        # the plan's entry and stop came from the old close: the stop 96.25 is over the LDCP, so the buy day mostly
        # opens under it, and the replay sells at that open
        self.assertEqual(m["counts"], {"target": 0, "stop": 10000, "gap": 9516, "trend": 0, "time": 0, "nofill": 0})
        self.assertEqual((m["p_stop"], m["p_gap"], m["p_nofill"], m["avg_R"]), (1.0, 0.9516, 0.0, -0.11))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R after costs the target has to come first "
                                         "in 37 % of the replays that hit one side; it did in 0 % (target first 0.0 "
                                         "% vs stop first 100.0 %).")
        self.assertEqual(doc["warnings"], [
            "the buy day's LDCP is 95.40 (from the company page), under the last close 98.40 of Wed 2026-10-28: PSX "
            "lowers the LDCP on an ex-date. The replays start from 95.40, and the closes before it are scaled to it, "
            "so a payout is not a fall",
            "the plan's entry 99.38 was set from the last close 98.40, not from the LDCP: check the entry and the stop"])
        self.assertEqual(doc["history"], self.doc["history"])        # percents of each past day's open: no change
        # an entry set from the LDCP (your price 96.35, stop 93.22) fills as usual: from 98.40, 98.3 % did not fill
        mine = swing(swing_plan(limits=self.XD, entry=96.35, entry_rule="your price", stop=93.22, target=102.61))
        mm = mine["mc"]
        self.assertEqual((mm["p_nofill"], mm["p_fill_open"], mm["p_target"], mm["p_stop"], mm["p_gap"], mm["avg_R"]),
                         (0.0, 1.0, 0.3365, 0.3207, 0.0259, 0.593))
        self.assertTrue(mine["verdict"].startswith("Leans toward the target: "), mine["verdict"])
        self.assertEqual(mine["warnings"], doc["warnings"][:1])      # the entry is your own price: no note on it
        # the plan's last close is not the data folder's: the replays still start from the LDCP, and say so
        odd = swing(swing_plan(limits=self.XD, last_close=97.0), replays=500)
        self.assertEqual(odd["warnings"][1:], [
            "the plan's entry 99.38 was set from the last close 97.00, not from the LDCP: check the entry and the stop",
            "the plan's last close 97.00 is not the newest close in the data folder (98.40 on 2026-10-28): the replays "
            "start from 95.40"])

    def test_only_the_buy_days_own_ldcp_moves_the_start(self):
        # the LDCP in the buy day's own closing file ("computed", "base": "file") is the buy day's too
        page = swing(swing_plan(limits=self.XD), replays=2000)
        f_doc = swing(swing_plan(limits=dict(self.XD, **{"from": "computed", "base": "file"})), replays=2000)
        self.assertEqual(f_doc["mc"], page["mc"])
        self.assertEqual(f_doc["warnings"][0], page["warnings"][0].replace("the company page", "the buy day's closing "
                                                                                              "file"))
        self.assertIn("Price limits: 85.86 to 104.94 on the buy day (computed from the LDCP 95.40 in the buy day's "
                      "closing file), then from each replayed close.", "\n".join(tm.report(f_doc)))
        # an LDCP over the last close (a made-up consolidation): the same rule, other words
        up = swing(swing_plan(limits={"lower": 90.9, "upper": 111.1, "ldcp": 101.0, "from": "company page"}),
                   replays=500)
        self.assertEqual((up["mc"]["base"], up["mc"]["base_from"]), (101.0, "ldcp"))
        self.assertEqual(up["warnings"][0], "the buy day's LDCP is 101.00 (from the company page), over the last close "
                                            "98.40 of Wed 2026-10-28. The replays start from 101.00, and the closes "
                                            "before it are scaled to it")
        for doc in (f_doc, up):
            check_words(self, "\n".join(tm.report(doc)))
            plain(self, "\n".join(tm.report(doc)))
        # limits computed from the last close, an LDCP with no source, none, not a price inside its limits, or
        # within half a paisa of the last close: the buy day starts from the last close 98.40, as before
        pool, adj = demo_pool()
        for lim in (dict(self.XD, **{"from": "computed", "base": "last close"}), dict(self.XD, **{"from": "computed"}),
                    dict(self.XD, ldcp=None), dict(self.XD, ldcp=True), dict(self.XD, ldcp=954.0),
                    dict(self.XD, ldcp=98.4), dict(self.XD, ldcp=98.404)):
            doc = swing(swing_plan(limits=lim), replays=1000)
            m = doc["mc"]
            self.assertEqual((m["base"], m["base_from"], m["trend_level"]), (98.4, "last close", 92.36), lim)
            recs = tm.swing_replays(pool, 98.4, 99.38, 96.25, 105.64, adj, 10, 21, (85.86, 104.94), 1000, 19)
            self.assertEqual(m["counts"], tm.swing_summary(recs, 99.38, 96.25, 105.64, tm.COSTS, 1000)["counts"], lim)
            self.assertFalse([w for w in doc["warnings"] if "LDCP" in w], lim)
        # a swing plan keeps psx-exit's "base"; a day plan does not, as in v1 (day style stays the same)
        self.assertEqual(swing_plan()["limits"]["base"], "last close")
        day = plan(limits={"lower": 88.56, "upper": 108.24, "ldcp": 98.4, "from": "computed", "base": "file"})
        self.assertEqual(day["limits"], {"lower": 88.56, "upper": 108.24, "ldcp": 98.4, "from": "computed"})

    def test_the_ex_date_notes_on_the_command_line(self):
        path = os.path.join(self.d, "xd.json")
        with open(path, "w") as fh:
            json.dump(dict(SWING_PLAN, limits=self.XD), fh)
        code, out, err = run(["run", "--plan", path, "--data", MARKET, "--config", EXAMPLE_CFG, "--offline", "--now",
                              "2026-10-29 09:21"])
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        for line in ("  stop           100.0 %  out at 96.25, or at the open when it opens under it (a gap: 95.2 %)",
                     "The trend rule: a close under the lowest close of the 10 sessions before it (now 89.54) sells at "
                     "the next open.",
                     "Note: the buy day's LDCP is 95.40 (from the company page), under the last close 98.40 of Wed "
                     "2026-10-28: PSX lowers the LDCP on an ex-date. The replays start from 95.40, and the closes "
                     "before it are scaled to it, so a payout is not a fall.",
                     "Note: the plan's entry 99.38 was set from the last close 98.40, not from the LDCP: check the "
                     "entry and the stop."):
            self.assertIn(line, lines)
        check_words(self, out)
        plain(self, out)
        code, out, _ = run(["chart", "--plan", path, "--data", MARKET, "--config", EXAMPLE_CFG, "--offline", "--now",
                            "2026-10-29 09:21", "--replays", "500"])
        self.assertEqual(code, 0)
        self.assertTrue(out.splitlines()[-1].startswith("Strongly toward the stop: "), out)

    def test_a_bar_with_no_ldcp_is_left_out(self):
        rows = [(100.0, 101.0, 99.0, 100.0, 100.0)] * 60
        rows[10] = (100.0, 101.0, 99.0, 100.0, None)
        root = os.path.join(self.d, "noldcp")
        made_up_market(root, rows)
        p = swing_plan(entry=101.0, stop=97.0, target=109.0, last_close=100.0, limits=None)
        doc = swing(p, root=root, replays=300)
        self.assertEqual((doc["data"]["daily_bars"], doc["mc"]["pool_bars"]), (60, 59))
        self.assertIn("Each session is one of DEMO's own last 59 daily bars", "\n".join(tm.report(doc)))
        pool, used = tm.swing_pool([{"open": 1.0, "high": 2.0, "low": 1.0, "close": 2.0, "ldcp": None},
                                    {"open": 1.0, "high": 2.0, "low": 1.0, "close": 2.0, "ldcp": 0},
                                    {"open": 1.0, "high": 2.0, "low": 1.0, "close": 2.0, "ldcp": 2.0}])
        self.assertEqual((pool, len(used)), ([(0.5, 1.0, 0.5, 1.0)], 1))

    def test_no_fill_in_any_replay(self):
        # every made-up day opens 2 % over its LDCP and never trades under 1.5 % over it: a limit 1 % over the
        # last close never fills
        root = os.path.join(self.d, "high")
        made_up_market(root, [(102.0, 104.0, 101.5, 102.0, 100.0)] * 60)
        p = swing_plan(entry=103.02, stop=100.0, target=109.06, last_close=102.0, limits=None)
        doc = swing(p, root=root, replays=1000)
        m = doc["mc"]
        self.assertEqual((m["p_nofill"], m["filled"], m["avg_R"], m["counts"]["nofill"]), (1.0, 0, None, 1000))
        self.assertEqual(m["ended"][0], 1000)
        text = "\n".join(tm.report(doc))
        for line in ("  no fill        100.0 %  the limit 103.02 did not fill on Thu 2026-10-29: the order ends at "
                     "the close", "  Average R: none, no replay filled.",
                     "No replay filled: in every one the buy day opened and stayed over the limit 103.02, so the "
                     "order ended at the close."):
            self.assertIn(line, text)
        self.assertEqual(doc["verdict_notes"], [])
        check_words(self, text)
        plain(self, text)

    def test_too_few_daily_bars_is_one_plain_line(self):
        with self.assertRaises(tm.TMError) as cm:
            swing(root=self.data(closing=False), replays=10)
        text = str(cm.exception)
        self.assertIn("only 0 daily bar(s) of DEMO before 2026-10-29", text)
        self.assertIn("swing mode needs at least 20. The PSX site is off for this tool: save the PSX closing files "
                      "of the last 60 sessions", text)
        root = os.path.join(self.d, "short")
        made_up_market(root, [(100.0, 101.0, 99.0, 100.0, 100.0)] * 19)
        with self.assertRaises(tm.TMError) as cm:
            swing(root=root, replays=10)
        self.assertIn("only 19 daily bar(s) of DEMO", str(cm.exception))
        rows = [(100.0, 101.0, 99.0, 100.0, None)] * 41 + [(100.0, 101.0, 99.0, 100.0, 100.0)] * 19
        made_up_market(os.path.join(self.d, "few"), rows)
        with self.assertRaises(tm.TMError) as cm:
            swing(root=os.path.join(self.d, "few"), replays=10)
        self.assertIn("only 19 of DEMO's 60 daily bars before 2026-10-29 have an LDCP", str(cm.exception))

    def test_the_swing_plan_checks(self):
        cases = [(dict(stop=100.0), "stop < entry < target"), (dict(side="sell"), "long only"),
                 (dict(date="2026-11-09"), "PSX has no session on Mon 2026-11-09 (PSX holiday: Iqbal Day)"),
                 (dict(symbol="DE MO"), "PSX symbol"), (dict(entry="abc"), "number for entry"),
                 (dict(max_hold_sessions=0), "the plan's max_hold_sessions must be a whole number from 1 to 250"),
                 (dict(max_hold_sessions=1.5), "max_hold_sessions"), (dict(max_hold_sessions=True), "whole number"),
                 (dict(trend_sessions=1), "the plan's trend_sessions must be a whole number from 2 to 60"),
                 (dict(exit_by="soon"), "the plan's exit_by wants a date")]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                swing_plan(**change)
            self.assertIn(words, str(cm.exception), change)
        # missing hold and trend numbers come from the config's swing block (else 20 and 10)
        p = swing_plan({"swing": {"max_hold_sessions": 7}}, max_hold_sessions=None, trend_sessions=None)
        self.assertEqual((p["max_hold_sessions"], p["hold_from"], p["trend_sessions"]), (7, "config", 10))
        with self.assertRaises(tm.TMError) as cm:
            swing_plan({"swing": {"trend_exit_sessions": "x"}}, trend_sessions=None)
        self.assertIn('"trend_exit_sessions" in the "swing" block of psx/config.json must be a whole number from 2 '
                      'to 60', str(cm.exception))
        self.assertEqual(swing_plan(max_hold_sessions=20.0)["max_hold_sessions"], 20)
        # the plan's limits are used for the buy day, else they are computed from the last close
        self.assertEqual(swing(replays=50)["data"]["limits"]["from"], "computed")
        page = swing(swing_plan(limits={"lower": 88.56, "upper": 108.24, "ldcp": 98.4, "from": "company page"}),
                     replays=50)
        self.assertIn("Price limits: 88.56 to 108.24 on the buy day (from the company page), then from each "
                      "replayed close. No replay goes past them.", "\n".join(tm.report(page)))
        bare = swing(swing_plan(limits={"lower": 88.56, "upper": 108.24, "from": "computed"}), replays=50)
        self.assertIn("Price limits: 88.56 to 108.24 on the buy day (computed), then", "\n".join(tm.report(bare)))

    def test_the_plan_and_the_data_disagree_gives_a_note(self):
        doc = swing(swing_plan(exit_by="2026-11-26", last_close=97.0), replays=100)
        self.assertEqual(doc["warnings"], [
            "the plan's exit-by day is Thu 2026-11-26, but 20 sessions after the buy day is Fri 2026-11-27 by the "
            "calendar now: the replays end then",
            "the plan's last close 97.00 is not the newest close in the data folder (98.40 on 2026-10-28): the "
            "replays start from 98.40"])
        self.assertIn("Note: the plan's last close 97.00 is not the newest close", "\n".join(tm.report(doc)))
        # a closure added under "holidays" moves the end: the replays follow the calendar
        later = swing(cfg={"holidays": {"2026-11-20": "a test closure"}}, replays=100)
        self.assertEqual(later["mc"]["end"], "2026-11-30")
        self.assertIn("by the calendar now", later["warnings"][0])
        self.assertNotIn("2026-11-20", later["mc"]["band_dates"])
        outside = swing(swing_plan(limits={"lower": 80.0, "upper": 90.0, "from": "company page"}), replays=100)
        self.assertIn("outside the buy day's price limits 80.00 to 90.00", outside["warnings"][0])
        self.assertEqual(outside["data"]["limits"]["from"], "computed")

    # ------------------------------------------------ the command line: the SKILL.md block, --write, chart, the
    # words, the speed

    def test_the_skill_md_swing_block_is_the_real_output(self):
        with open(SKILL_MD, encoding="utf-8") as fh:
            text = fh.read()
        found = re.findall(r"\*\*Swing mode\.\*\*(.*?)```\n(.*?)```\n(.*?)\n\n", text, re.S)
        self.assertEqual(len(found), 1)
        words, block, note = found[0]
        self.assertIn("The command is `timemachine.py run`", words)
        code, out, err = run(swing_args())
        self.assertEqual(code, 0, err)
        self.assertEqual(out, block)
        # the flags the note gives (paths from the pack's root) print the same lines
        flags = re.search(r"add `(.*?)` to get these exact lines", note, re.S).group(1).split()
        flags = [os.path.join(ROOT, x) if x.startswith(("tests/", "psx-")) else x.strip('"') for x in flags]
        flags[flags.index("--now") + 1:] = [" ".join(flags[flags.index("--now") + 1:])]
        self.assertEqual(run(["run"] + flags), (0, block, ""))

    def test_the_two_day_example_blocks_stay_word_for_word(self):
        with open(SKILL_MD, encoding="utf-8") as fh:
            text = fh.read()
        blocks = re.findall(r"\*\*(Bars|Daily) mode\.\*\*.*?```\n(.*?)```", text, re.S)
        self.assertEqual([name for name, _ in blocks], ["Bars", "Daily"])
        self.assertEqual(hashlib.sha256("".join(b for _, b in blocks).encode("utf-8")).hexdigest()[:16], DAY_BLOCKS)

    def test_write_then_chart_from_the_file(self):
        out = os.path.join(self.d, "psx", "timemachine.json")
        code, text, err = run(swing_args("--write", out))
        self.assertEqual(code, 0, err)
        self.assertIn("Wrote %s.\nNext: /psx-budget sizes it on the buy day" % out, text)
        with open(out) as fh:
            doc = json.load(fh)
        self.assertEqual((doc["mode"], doc["created_at"], len(doc["history"]["walks"])),
                         ("swing", "2026-10-28T12:00:00Z", 60))
        self.assertEqual((len(doc["mc"]["sample_paths"]), len(doc["mc"]["sample_paths"][0])), (160, 21))
        code, chart, _ = run(["chart", "--from", out])
        self.assertEqual(code, 0)
        lines = chart.splitlines()
        self.assertEqual(lines[0], "DEMO swing plan, 2,000 replay paths of the close, Thu 2026-10-29 to Fri "
                                   "2026-11-27 (seed 19, swing mode)")
        axis = lines.index("          +" + "-" * 63)                     # one column a session: 21 of 3 characters
        self.assertEqual(lines[axis + 1], "           10-29" + " " * 25 + "11-13" + " " * 23 + "11-27")
        self.assertIn("One column a session: its close.", lines)
        self.assertTrue(any(x.startswith("   105.64 |") and x.endswith("  target") for x in lines))
        self.assertTrue(any(x.startswith("    96.25 |") and x.endswith("  stop") for x in lines))
        self.assertTrue(any(x.startswith("    99.38 |") and x.endswith("  entry") for x in lines))
        self.assertEqual(lines[-1], doc["verdict"])
        self.assertEqual(run(["chart"] + swing_args()[1:]), (0, chart, ""))
        short = run(["chart"] + swing_args("--sessions", "5", "--replays", "200")[1:])[1].splitlines()
        self.assertIn("          +" + "-" * 18, short)
        self.assertIn("           10-29        11-05", short)
        doc["mc"].pop("band_dates")                      # a cut file is one plain line, not a crash
        with open(out, "w") as fh:
            json.dump(doc, fh)
        code, text, err = run(["chart", "--from", out])
        self.assertEqual((code, text), (1, ""))
        self.assertIn("is not a time machine result", err)

    def test_every_new_line_passes_the_word_test(self):
        root = os.path.join(self.d, "high")
        made_up_market(root, [(102.0, 104.0, 101.5, 102.0, 100.0)] * 60)
        nofill = os.path.join(self.d, "nofill.json")
        with open(nofill, "w") as fh:
            json.dump(dict(SWING_PLAN, entry=103.02, stop=100.0, target=109.06, last_close=102.0, limits=None), fh)
        odd = os.path.join(self.d, "odd.json")
        with open(odd, "w") as fh:
            json.dump(dict(SWING_PLAN, exit_by="2026-11-26", last_close=97.0), fh)
        texts, none = [], ["--config", os.path.join(self.d, "no-config.json"), "--offline", "--now", SWING_NOW]
        for argv in (swing_args(), swing_args("--sessions", "5"), swing_args("--sessions", "1"),
                     ["chart"] + swing_args()[1:], ["chart"] + swing_args("--sessions", "5")[1:],
                     ["run", "--plan", nofill, "--data", root] + none,
                     ["run", "--plan", odd, "--data", MARKET] + none,
                     swing_args("--mode", "bars"), swing_args("--until", "15:20"), swing_args("--sessions", "0"),
                     ["run", "--plan", odd, "--data", self.d] + none):
            code, out, err = run(argv)
            self.assertIn(code, (0, 1), argv)
            texts.append(out + err)
        texts.append(str(self._day_refusal()))
        text = "\n".join(texts)
        self.assertGreater(check_words(self, text), 150)
        plain(self, text)
        for words in ("No replay filled", "Note: the plan's exit-by day is Thu 2026-11-26",
                      "end by the trend rule or the time stop", "swing mode needs at least 20",
                      "--mode bars is for a day plan", "--sessions is for a swing plan"):
            self.assertIn(words, text)

    def _day_refusal(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(sessions=3, replays=10)
        return cm.exception

    def test_a_swing_run_is_no_slower_than_the_day_run(self):
        # SPEC 6.4 (Task 6 acceptance): 10,000 swing replays of the DEMO plan take no longer than the v1 day
        # run of the DEMO example (bars mode, 10,000 replays) on the same machine
        t0 = time.perf_counter()
        machine()
        t1 = time.perf_counter()
        swing()
        t2 = time.perf_counter()
        self.assertLessEqual(t2 - t1, t1 - t0, "swing %.2f s, day %.2f s" % (t2 - t1, t1 - t0))


if __name__ == "__main__":
    unittest.main()
