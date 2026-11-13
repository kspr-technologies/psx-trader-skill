"""psx-exit tests. They run offline on the made-up sample market in tests/fixtures/psx-market/
(DEMO on Thu 2026-10-29, the numbers of EXAMPLE.md) and on tests/fixtures/psx-exit/."""

import datetime
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import urllib.error
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT
from _words import check_words

exitp = load("psx-exit", "exit")

MARKET = os.path.join(FIX, "psx-market")
MINE = os.path.join(FIX, "psx-exit")
ENTRY = os.path.join(MINE, "entry.json")
FRIDAY_BARS = os.path.join(MINE, "DEMO-friday.csv")
THU = datetime.date(2026, 10, 29)
FRI = datetime.date(2026, 10, 30)
RAMADAN_MON = datetime.date(2026, 2, 23)
RAMADAN_FRI = datetime.date(2026, 2, 20)

# psx/plan.json for the example (EXAMPLE.md section 6), exactly as the contract writes it
EXAMPLE_PLAN = {"created_by": "psx-exit", "symbol": "DEMO", "side": "buy", "date": "2026-10-29", "setup": "orb",
                "entry": 100.65, "entry_time": "10:15 AM", "entry_bar": 8, "interval": "5m",
                "opening_range": {"high": 100.2, "low": 98.9, "minutes": 15},
                "stop": 99.55, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
                "target": 101.75, "target_r": 1.0, "target_label": "1R (one times the risk)",
                "risk_per_share": 1.1, "reward_risk": 1.0, "flat_by": "15:20", "session": "normal",
                "limits": {"lower": 88.56, "upper": 108.24, "ldcp": 98.4, "from": "company page"},
                "costs_per_share_est": 0.3586}
EXAMPLE_LINES = [
    "Exit plan: DEMO long, Thu 2026-10-29, opening-range breakout on 5-minute bars",
    "  Entry         100.65 at 10:15 AM (bar 8)",
    "  Stop          99.55  opening-range midpoint: (100.20 + 98.90) / 2 = 99.55",
    "  Target        101.75  1R (one times the risk)",
    "  Risk a share  1.10 (1.09% of the entry)",
    "  Reward:risk   1.00",
    "  Costs         about 0.3586 a share, buy and sell at the stop (estimated)",
    "  Price limits  88.56 to 108.24, from the company page (LDCP 98.40)",
    "  Flat by       15:20 PKT, win or lose: no overnight hold",
]
# D1: round-trip costs of 1,000 shares over their price risk: 358.55 / 1,100.00 = 32.6% -> 33%
COSTS_NOTE = "Costs are about 33% of the price risk (over 25%). You pay them win or lose."
# SPEC section 12: the same block, word for word, in nine SKILL.md files
SAFETY = """## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order. psx-broker shows the order and waits. It prints the line to
  type into the broker app only after psx-risk passed that exact order today and the user typed an
  explicit yes for that one order in the chat. The user then places it in their own broker's app.
  If the user ever adds a broker tool that can place orders, set it to "Needs approval"
  (Customize > Connectors > tool permissions), never "Always allow".
- Long only. No short selling (PSX bans blank sales), no margin, no futures and no options.
- Broker logins, passwords and PINs never go in the chat."""


REAL_NETWORK = (exitp.SLEEP, exitp.http_get)
REAL_CLOCK = exitp.utc_now


def no_network(*args, **kwargs):
    raise AssertionError("a test touched the network")


def setUpModule():
    exitp.SLEEP = exitp.http_get = no_network        # every test runs offline


def tearDownModule():
    exitp.SLEEP, exitp.http_get = REAL_NETWORK


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=100.20, ol=98.90, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


def true_ranges(bars):
    """By hand: each bar's true range, the first one without a gap before it."""
    trs, prev = [], None
    for b in bars:
        r = b["high"] - b["low"]
        if prev is not None:
            r = max(r, abs(b["high"] - prev), abs(b["low"] - prev))
        trs.append(r)
        prev = b["close"]
    return trs


class Math(unittest.TestCase):
    def test_the_example_or_mid_1r(self):
        p = c(100.65, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("99.55"))              # (100.20 + 98.90) / 2, already on the tick
        self.assertEqual(p["risk"], Decimal("1.10"))
        self.assertEqual(p["target"], Decimal("101.75"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertEqual(p["stop_detail"], "(100.20 + 98.90) / 2 = 99.55")
        self.assertNotIn("rounded down", p["stop_detail"])

    def test_the_default_2r(self):
        p = c(100.65, "or-mid")
        self.assertEqual(p["target"], Decimal("102.85"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_floats_give_the_same_ticks(self):
        p = c(100.64999999999999, "or-mid", "1R", 100.19999999999999, 98.90000000000001)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("100.65"), Decimal("99.55"), Decimal("101.75")))

    def test_other_stops(self):
        p = c(100.65, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("98.90"), Decimal("1.75"), Decimal("104.15")))
        p = c(100.65, "price:99.559")
        self.assertEqual(p["stop"], Decimal("99.55"))               # rounds away from the entry
        p = c(100.65, "pct:1")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("99.64"), Decimal("1.01"), Decimal("102.67")))
        self.assertEqual(p["stop_detail"], "100.65 x (1 - 1%) = 99.6435, rounded down")
        p = c(100.65, "atr:1.5", atr=4.45 / 9, n=9)
        self.assertEqual(p["stop"], Decimal("99.90"))               # 100.65 - 1.5 x 0.494444 = 99.908334
        self.assertEqual(p["stop_label"], "1.5 x ATR(9) under the entry")
        self.assertIn("fewer than 14 so far", p["stop_detail"])
        self.assertEqual(c(100.65, "price:Rs 99.55")["stop"], Decimal("99.55"))   # "Rs" is fine in a price

    def test_target_rounds_half_up_and_price_targets(self):
        p = c(100, "price:99.01", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("101.49"))            # 100 + 1.485
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        p = c(100.65, "or-mid", "price:102")
        self.assertEqual(p["reward_risk"], Decimal("1.23"))         # 1.35 / 1.10
        self.assertEqual(p["target_label"], "your price (1.23R)")
        self.assertEqual(p["target_r"], Decimal("1.23"))

    def test_refusals_say_why(self):
        cases = [("price:100.65", "2R", "at or above the entry"),
                 ("price:101", "2R", "at or above the entry"),
                 ("or-mid", "price:100.65", "at or below the entry"),
                 ("price:100.60", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(100.65, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 100.65 is 0.0503: a 0.06 risk is allowed, 0.05 is not
        with self.assertRaises(exitp.PlanError) as cm:
            c(100.65, "price:100.60")
        self.assertIn("0.05% of the price (0.0503 on 100.65)", str(cm.exception))
        self.assertEqual(c(100.65, "price:100.59")["risk"], Decimal("0.06"))
        # on a cheap share 0.05% is under a tick: the floor is then two ticks (Rs 0.02)
        with self.assertRaises(exitp.PlanError) as cm:
            c(10.00, "price:9.99", "2R", None, None)
        self.assertIn("less than Rs 0.02", str(cm.exception))
        self.assertEqual(c(10.00, "price:9.98", "2R", None, None)["risk"], Decimal("0.02"))
        with self.assertRaises(exitp.PlanError):
            c(99.00, "or-mid")                                     # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(100.65, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(100.65, "or-mid", oh=98.90, ol=100.20)
        with self.assertRaises(exitp.PlanError):
            c(float("nan"), "or-mid")


class Bars(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def only(self, *paths):
        """A data folder with only these files of the sample market."""
        for p in paths:
            os.makedirs(os.path.dirname(os.path.join(self.d, p)), exist_ok=True)
            shutil.copy(os.path.join(MARKET, p), os.path.join(self.d, p))
        return self.d

    def test_atr_on_the_days_bars(self):
        bars, what, path = exitp.atr_bars("DEMO", THU, 5, MARKET, upto=8)
        self.assertEqual((what, path), ("bars file", os.path.join(MARKET, "bars", "DEMO.csv")))
        self.assertEqual([b["index"] for b in bars], list(range(9)))     # 9:30 ... the 10:10 entry bar
        self.assertEqual([b["close"] for b in bars[:3]], [99.65, 99.95, 99.85])
        trs = true_ranges(bars)
        self.assertAlmostEqual(trs[0], 99.80 - 98.90, places=9)
        self.assertAlmostEqual(sum(trs), 4.45, places=9)                 # EXAMPLE.md section 5, bars 0 to 8
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 9)
        self.assertAlmostEqual(atr, 4.45 / 9, places=9)
        all_bars, _, _ = exitp.atr_bars("DEMO", THU, 5, MARKET)
        self.assertEqual(len(all_bars), 72)                              # Mon to Thu: 9:30 to 3:30 PM
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)                  # later in the day: the last 14

    def test_every_source_gives_the_same_bars(self):
        want = [(b["open"], b["high"], b["low"], b["close"]) for b in exitp.atr_bars("DEMO", THU, 5, MARKET, upto=8)[0]]
        one = exitp.atr_bars("DEMO", THU, 5, MARKET, os.path.join(MARKET, "bars", "DEMO-1m.csv"), upto=8)
        self.assertEqual([(b["open"], b["high"], b["low"], b["close"]) for b in one[0]], want)
        tape = exitp.atr_bars("DEMO", THU, 5, self.only(os.path.join("tape", "2026-10-29", "DEMO.jsonl")), upto=8)
        self.assertEqual(tape[1], "company page tape")
        self.assertEqual([(b["open"], b["high"], b["low"], b["close"]) for b in tape[0]], want)

    def test_the_market_tape_is_the_last_source(self):
        root = self.only(os.path.join("tape", "2026-10-29", "market.jsonl"))
        bars, what, _ = exitp.atr_bars("DEMO", THU, 5, root, upto=8)
        self.assertEqual((what, len(bars)), ("market tape", 9))
        self.assertEqual(bars[7]["close"], 100.03)                       # the 10:05 bar, from asof 10:08
        self.assertEqual((bars[8]["low"], bars[8]["close"]), (100.05, 100.65))   # asof 10:10, 10:12, 10:14
        self.assertEqual(exitp.atr_bars("DEMO", THU, 5, os.path.join(self.d, "nothing")), ([], None, None))

    def test_a_bar_counts_only_once_it_has_ended(self):
        at = datetime.datetime(2026, 10, 29, 10, 12)
        bars, _, _ = exitp.atr_bars("DEMO", THU, 5, MARKET, now_pkt=at)
        self.assertEqual([b["index"] for b in bars], list(range(8)))     # the 10:10 bar ends at 10:15
        root = self.only(os.path.join("tape", "2026-10-29", "DEMO.jsonl"))
        bars, _, _ = exitp.atr_bars("DEMO", THU, 5, root, now_pkt=at)
        self.assertEqual([b["index"] for b in bars], list(range(8)))
        bars, _, _ = exitp.atr_bars("DEMO", THU, 1, MARKET, os.path.join(MARKET, "bars", "DEMO-1m.csv"), upto=2)
        self.assertEqual([b["index"] for b in bars], [0, 2])             # 1-minute bars 9:30 and 9:32 (no 9:31)
        with self.assertRaises(exitp.PlanError) as cm:                   # 5-minute bars cannot be 1-minute bars
            exitp.atr_bars("DEMO", THU, 1, MARKET)
        self.assertIn("5-minute bars", str(cm.exception))

    def test_friday_bars_skip_the_juma_break(self):
        bars, _, _ = exitp.atr_bars("DEMO", FRI, 5, MARKET, FRIDAY_BARS, upto=35)
        self.assertEqual([b["index"] for b in bars], list(range(36)))
        self.assertEqual(bars[32]["start"], datetime.datetime(2026, 10, 30, 11, 55))
        self.assertEqual(bars[33]["start"], datetime.datetime(2026, 10, 30, 14, 30))   # bar 33 is the 14:30 bar
        trs = true_ranges(bars)
        self.assertAlmostEqual(trs[33], 103.95 - 103.25, places=9)       # the gap over the break counts
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 14)
        self.assertAlmostEqual(atr, (13 * 0.30 + 0.70) / 14, places=9)


class PsxRules(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def only(self, *paths):
        for p in paths:
            os.makedirs(os.path.dirname(os.path.join(self.d, p)), exist_ok=True)
            shutil.copy(os.path.join(MARKET, p), os.path.join(self.d, p))
        return self.d

    def test_limits_from_the_company_page(self):
        lim, why = exitp.todays_limits("DEMO", THU, MARKET, {}, True)
        self.assertEqual(lim, EXAMPLE_PLAN["limits"])                    # CIRCUIT BREAKER 88.56 to 108.24
        self.assertEqual(why, "")
        root = self.only(os.path.join("tape", "2026-10-29", "DEMO.jsonl"))
        self.assertEqual(exitp.todays_limits("DEMO", THU, root, {}, True), (EXAMPLE_PLAN["limits"], ""))

    def test_other_days_use_the_last_close(self):
        # the saved page is as of Thu 10-29: on Friday the limits come from the 10-29 close 101.60
        lim, _ = exitp.todays_limits("DEMO", FRI, MARKET, {}, True)
        self.assertEqual(lim, {"lower": 91.44, "upper": 111.76, "ldcp": 101.6, "from": "computed",
                               "base": "last close"})
        root = self.only(os.path.join("mkt_summary", "2026-10-28.Z"))
        lim, _ = exitp.todays_limits("DEMO", THU, root, {}, True)       # the same numbers as the page
        self.assertEqual(lim, {"lower": 88.56, "upper": 108.24, "ldcp": 98.4, "from": "computed",
                               "base": "last close"})
        self.assertEqual(exitp.limits_text(lim), "88.56 to 108.24 (LDCP 98.40): computed from the last close; an "
                                                 "ex-date lowers it")
        # ILP did not trade on 10-15: the 10-16 closing file is saved, and its LDCP is still the 10-14 close 72.93
        lim, _ = exitp.todays_limits("ILP", datetime.date(2026, 10, 16), MARKET, {}, True)
        self.assertEqual(lim, {"lower": 65.64, "upper": 80.22, "ldcp": 72.93, "from": "computed", "base": "file"})

    def test_an_ex_date_uses_the_ldcp_in_the_days_file(self):
        # D14: DEMO goes ex-dividend on Fri 10-30. The 10-29 close is 101.60, but the 10-30 file has LDCP 96.60
        root = self.only(os.path.join("mkt_summary", "2026-10-29.Z"))
        with open(os.path.join(root, "mkt_summary", "2026-10-30.lis"), "w") as fh:
            fh.write("30OCT2026|DEMO|0818|Demo IndustriesXD|97.00|98.50|96.10|98.00|1500000|96.60|\n")
        lim, why = exitp.todays_limits("DEMO", FRI, root, {}, True)
        self.assertEqual((lim, why), ({"lower": 86.94, "upper": 106.26, "ldcp": 96.6, "from": "computed",
                                       "base": "file"}, ""))              # not 91.44 to 111.76 from 101.60
        self.assertEqual(exitp.limits_text(lim), "86.94 to 106.26 (LDCP 96.60): computed from the LDCP in the day's "
                                                 "closing file")
        code, _, err = run(exitp, ["plan", "--symbol", "DEMO", "--entry", "98", "--setup", "level", "--stop",
                                   "pct:1", "--target", "price:107", "--date", "2026-10-30", "--time", "10:00 AM",
                                   "--data", root, "--offline", "--now", "2026-10-30 10:01", "--config", "none.json"])
        self.assertEqual(code, 1)
        self.assertIn("the target 107.00 is above today's upper price limit 106.26", err)

    def test_unknown_limits_say_why(self):
        lim, why = exitp.todays_limits("DEMO", THU, self.d, {}, True)
        self.assertIsNone(lim)
        self.assertIn("no closing file before 2026-10-29", why)
        self.assertIn('"psx_site": true', why)                            # SITE_OFF: what to save where
        self.assertIn(os.path.join(self.d, "company", "DEMO.html"), why)
        root = self.only(os.path.join("mkt_summary", "2026-10-27.Z"))   # the 10-28 file is missing
        lim, why = exitp.todays_limits("DEMO", THU, root, {}, True)
        self.assertIsNone(lim)
        self.assertIn("the newest closing file is from 2026-10-27, not 2026-10-28", why)
        lim, why = exitp.todays_limits("DEMO", THU, self.d, {"psx_site": True}, True)
        self.assertIn("a run with --offline or --now, or a plan for another day, reads only the data folder", why)

    def test_costs_over_a_quarter_of_the_price_risk(self):
        # D1: costs_vs_risk() of 1,000 shares over COST_WARN, the rule of psx-budget and psx-risk too
        self.assertEqual(exitp.costs_note(100.65, 99.55, {}), COSTS_NOTE)
        self.assertEqual(exitp.costs_note(100.65, 98.63, {}), "")           # 2.02 risk: about 18%
        for entry, stop in ((100.65, 99.44), (283.29, 281.44), (50.0, 49.4)):   # 1.07% to 1.42% under
            share = exitp.costs_vs_risk(entry, stop, 1000, exitp.cost_settings({}))
            pct = int(Decimal(str(share * 100)).quantize(Decimal(1), rounding="ROUND_HALF_UP"))
            self.assertGreater(share, 0.25)
            self.assertEqual(exitp.costs_note(entry, stop, {}),
                             "Costs are about %d%% of the price risk (over 25%%). You pay them win or lose." % pct)
        self.assertIn("Costs are about 55% of the price risk", exitp.costs_note(283.29, 281.44, {}))
        with self.assertRaises(exitp.PlanError):
            exitp.costs_note(100.65, 99.55, {"costs": {"commission_pct": "lots"}})

    def test_reward_risk_is_shown_to_2_decimals_and_checked_exactly(self):
        self.assertEqual([exitp.ratio(x) for x in (1.0, 1.5, 1.2272727, 1.005)], ["1.00", "1.50", "1.23", "1.01"])
        p = c(100.00, "price:98.00", "price:102.99", None, None)           # 2.99 / 2.00 = 1.495
        self.assertEqual((p["reward_risk"], p["target_label"]), (Decimal("1.50"), "your price (1.50R)"))
        self.assertFalse(exitp.meets_reward_risk(100.00, 98.00, 102.99, 1.5))   # under 1.5, exactly

    def test_an_entry_at_a_session_end_cannot_fill(self):
        fri = exitp.session(FRI)
        why = exitp.segment_end_refusal(FRI, datetime.time(12, 0), fri)
        self.assertIn("nothing trades from 12:00 PM to 2:30 PM, so it cannot fill", why)
        self.assertIn("The next chance is the 2:30 PM session, at a new price", why)
        self.assertIn("at the close on Fri 2026-10-30", exitp.segment_end_refusal(FRI, datetime.time(16, 30), fri))
        self.assertIn("at the close on Thu 2026-10-29",
                      exitp.segment_end_refusal(THU, datetime.time(15, 30), exitp.session(THU)))
        for t in (datetime.time(11, 55), datetime.time(14, 35), datetime.time(15, 25), None):
            self.assertEqual(exitp.segment_end_refusal(FRI, t, fri), "")

    def test_costs_a_share(self):
        self.assertEqual(exitp.costs_per_share(100.65, 99.55, {}), Decimal("0.3586"))      # 358.55 / 1000
        one_side = {"costs": {"day_trade_one_side": True}}
        self.assertEqual(exitp.costs_per_share(100.65, 99.55, one_side), Decimal("0.1868"))
        self.assertEqual(exitp.costs_per_share(10.00, 9.90, {}), Decimal("0.0703"))         # Rs 0.03 minimum
        with self.assertRaises(exitp.PlanError):
            exitp.costs_per_share(100.65, 99.55, {"costs": {"commission_pct": "lots"}})

    def test_flat_by_goes_through_config_time(self):
        t, note = exitp.resolve_flat_by(THU, None, {}, None)
        self.assertEqual((exitp.hm(t), note), ("15:20", ""))
        t, note = exitp.resolve_flat_by(FRI, None, {}, None)
        self.assertEqual(exitp.hm(t), "16:20")
        self.assertIn("4:30 PM on Fri 2026-10-30, so it is 16:20: the same 10 minutes before the close", note)
        self.assertEqual(exitp.hm(exitp.resolve_flat_by(RAMADAN_MON, None, {}, None)[0]), "13:20")
        self.assertEqual(exitp.hm(exitp.resolve_flat_by(RAMADAN_FRI, None, {}, None)[0]), "12:20")
        t, note = exitp.resolve_flat_by(FRI, None, {"flat_by": "10:00"}, None)
        self.assertEqual(exitp.hm(t), "09:45")
        self.assertIn("the same 30 minutes after the open", note)
        t, note = exitp.resolve_flat_by(THU, None, {"flat_by": "15:55"}, None)
        self.assertEqual(exitp.hm(t), "15:30")                           # kept inside the session
        self.assertIn("falls outside PSX hours", note)
        for day, flag, why in ((THU, "15:30", "3:30 PM close"), (FRI, "16:30", "4:30 PM close"),
                               (THU, "09:30", "9:30 AM open"), (FRI, "13:00", "Juma break")):
            with self.assertRaises(exitp.PlanError) as cm:
                exitp.resolve_flat_by(day, flag, {}, None)
            self.assertIn(why, str(cm.exception), flag)
        self.assertEqual(exitp.hm(exitp.resolve_flat_by(FRI, "16:00", {}, None)[0]), "16:00")
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.resolve_flat_by(THU, None, {"flat_by": "09:00"}, None)
        self.assertIn("at or before the 9:30 AM open", str(cm.exception))
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.resolve_flat_by(THU, None, {}, datetime.time(15, 25))
        self.assertIn("no time left to hold it", str(cm.exception))

    def test_bar_numbers_follow_the_psx_grid(self):
        def clock(day, t=None, k=None, minutes=5):
            got = exitp.entry_clock(day, {"entry_time": t, "entry_bar": k}, minutes)
            return (exitp.clock_label(got[0]) if got[0] else None), got[1]
        self.assertEqual(clock(THU, k=8), ("10:15 AM", 8))               # the 10:10 bar closes at 10:15
        self.assertEqual(clock(THU, "10:15 AM"), ("10:15 AM", 8))
        self.assertEqual(clock(FRI, k=8), ("10:00 AM", 8))               # Friday starts at 9:15
        self.assertEqual(clock(FRI, "12:00 PM"), ("12:00 PM", 32))
        self.assertEqual(clock(FRI, k=33), ("2:35 PM", 33))              # the 14:30 bar, after the break
        self.assertEqual(clock(FRI, "2:45 PM"), ("2:45 PM", 35))
        self.assertEqual(clock(RAMADAN_MON, k=50), ("1:30 PM", 50))      # Ramadan: 9:15 to 1:30 PM, 51 bars
        self.assertEqual(clock(THU, "10:17"), ("10:17 AM", None))        # not a bar close: no bar number
        for day, t, k, why in ((THU, None, 72, "no bar 72"), (THU, "9:00 AM", None, "outside PSX hours"),
                               (FRI, "1:00 PM", None, "outside PSX hours"),
                               (THU, "10:20 AM", 8, "not the close of bar 8 (10:15 AM)")):
            with self.assertRaises(exitp.PlanError) as cm:
                clock(day, t, k)
            self.assertIn(why, str(cm.exception))


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("psx")
        exitp.SLEEP = exitp.http_get = no_network

    def tearDown(self):
        exitp.SLEEP = exitp.http_get = no_network       # undo a test's own fake site
        exitp.utc_now = REAL_CLOCK                      # and its own clock
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def entry(self, **change):
        with open(ENTRY) as fh:
            doc = json.load(fh)
        doc.update(change)
        self.write(os.path.join("psx", "entry.json"), doc)

    def plan(self, *args, **kw):
        return run(exitp, ["plan", "--now", kw.get("now", "2026-10-29 10:16"), "--data", kw.get("data", MARKET),
                           "--offline"] + list(args))

    def read_plan(self):
        with open(os.path.join("psx", "plan.json")) as fh:
            return json.load(fh)

    def test_the_example_end_to_end(self):
        self.entry()
        code, text, err = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in EXAMPLE_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["created_at"], "2026-10-29T05:16:00Z")      # 10:16 Karachi time is 05:16 UTC
        lines = text.splitlines()
        self.assertEqual(lines[:9], EXAMPLE_LINES)
        self.assertIn("Note: " + COSTS_NOTE, lines)
        for want in ("not a guarantee", "PSX has no stop order", "/psx-timemachine", "Wrote psx/plan.json."):
            self.assertIn(want, text)
        low = text.lower()
        for never in ("pro" + "fit", "p&l", "real " + "time", "live", "$"):
            self.assertNotIn(never, low)                                 # never on screen
        code, text, _ = self.plan("--write")                            # the defaults: or-mid, 2R
        self.assertEqual(self.read_plan()["target"], 102.85)
        self.assertEqual(self.read_plan()["target_r"], 2.0)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-29 10:20"])
        self.assertEqual(code, 0)
        self.assertIn("  Target        102.85  2R (two times the risk)", text)
        self.assertIn("  Price limits  88.56 to 108.24, from the company page (LDCP 98.40)", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-30 10:20"])
        self.assertIn("not today", text)
        code, text, _ = run(exitp, ["show", "--json"])
        self.assertEqual(json.loads(text), self.read_plan())

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "demo", "--entry", "100.65", "--or-high", "100.20", "--or-low", "98.90",
                                  "--time", "10:15", "--entry-bar", "8", "--date", "2026-10-29", "--target", "1R",
                                  "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in EXAMPLE_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [COSTS_NOTE])

    def test_atr_stop_reads_bars(self):
        self.entry()
        code, text, err = self.plan("--stop", "atr:1.5", "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (99.9, 0.75, 102.15))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 9))
        self.assertAlmostEqual(p["atr"], 0.4944, places=4)
        self.assertIn("from the bars file", p["stop_detail"])
        code, text, _ = self.plan("--stop", "atr:1.5", "--json", "--bars", os.path.join(MARKET, "bars", "DEMO-1m.csv"))
        self.assertEqual(json.loads(text)["stop"], 99.9)                 # 1-minute bars, made into 5-minute bars
        code, _, err = self.plan("--stop", "atr:1.5", "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed 5-minute bars", err)
        os.mkdir("nothing")
        code, _, err = self.plan("--stop", "atr:1.5", data="nothing")
        self.assertEqual(code, 1)
        self.assertIn("there are none for 2026-10-29", err)
        self.assertEqual(len(err.strip().splitlines()), 1)
        # a Friday afternoon entry: bars across the Juma break; bar 35 closes at 2:45 PM
        code, text, err = self.plan("--symbol", "DEMO", "--entry", "103.90", "--setup", "level", "--stop", "atr:1.5",
                                    "--date", "2026-10-30", "--time", "2:45 PM", "--bars", FRIDAY_BARS, "--json",
                                    now="2026-10-30 14:46")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["entry_bar"], p["atr_bars"], p["stop"], p["session"]), (35, 14, 103.4, "friday"))
        self.assertFalse(any("Juma" in w for w in p["warnings"]))        # an afternoon trade is not held over it

    def test_refusals_from_the_cli(self):
        self.entry(status="waiting", time=None, price=None, bar_index=None, bar_start=None)
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("DEMO is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "100.21", "--json")        # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"], p["entry_bar"]), (100.21, 99.55, None, None))

        self.entry(setup="level", level=100.0)
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("longs only", err)
        self.assertIn("blank sales", err)
        self.entry(side="sell")
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:100.70")
        self.assertIn("longs only", err)
        os.remove(os.path.join("psx", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        self.assertIn("/psx-entry", err)
        code, _, err = self.plan("--symbol", "DEMO")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "DEMO", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "DEMO", "--entry", "100.65", "--or-high", "100.20")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_price_limits_in_the_plan(self):
        self.entry()
        code, _, err = self.plan("--target", "price:108.25")
        self.assertEqual(code, 1)
        self.assertIn("the target 108.25 is above today's upper price limit 108.24: it cannot fill today", err)
        self.assertEqual(self.plan("--target", "price:108.24")[0], 0)    # trading can reach the limit
        code, text, _ = self.plan("--stop", "price:88.56", "--target", "price:101.75", "--json")
        self.assertEqual(code, 0)
        w = " ".join(json.loads(text)["warnings"])
        self.assertIn("The stop 88.56 is at or under today's lower price limit 88.56", w)
        self.assertIn("no buyers, so you may not get out", w)
        code, _, err = self.plan("--entry", "120", "--stop", "price:118")
        self.assertIn("the entry 120.00 is above today's upper price limit 108.24", err)
        # Friday: the saved page is Thursday's, so the limits come from the 10-29 close 101.60
        self.entry(date="2026-10-30", time="10:00 AM")
        code, text, err = self.plan(now="2026-10-30 10:01")
        self.assertEqual(code, 0, err)
        self.assertIn("  Price limits  91.44 to 111.76 (LDCP 101.60): computed from the last close; an ex-date "
                      "lowers it", text)
        # no page and no closing file: the limits are unknown, and the note says what to save
        os.mkdir("empty")
        code, text, _ = self.plan("--json", data="empty", now="2026-10-30 10:01")
        p = json.loads(text)
        self.assertIsNone(p["limits"])
        self.assertTrue(any("unknown" in w and '"psx_site": true' in w for w in p["warnings"]))
        code, text, _ = self.plan(data="empty", now="2026-10-30 10:01")
        self.assertIn("  Price limits  unknown", text)

    def test_entries_that_cannot_fill(self):
        # rules-intraday-7: an entry under the day's lower limit cannot trade
        code, _, err = self.plan("--symbol", "DEMO", "--entry", "85", "--setup", "level", "--stop", "pct:1",
                                 "--target", "1R", "--date", "2026-10-29", "--time", "10:15 AM")
        self.assertEqual(code, 1)
        self.assertIn("the entry 85.00 is under today's lower price limit 88.56: it cannot fill today", err)
        # D16: a Friday trigger at 12:00 PM (bar 32): nothing trades until 2:30 PM, so no plan
        self.entry(date="2026-10-30", time="12:00 PM", bar_index=32, price=100.40)
        for extra in ((), ("--flat-by", "11:55")):
            code, _, err = self.plan(*extra, now="2026-10-30 12:01")
            self.assertEqual(code, 1, err)
            self.assertIn("the entry at 12:00 PM is when PSX stops on Fri 2026-10-30", err)
            self.assertIn("The next chance is the 2:30 PM session, at a new price", err)
        code, _, err = self.plan("--symbol", "DEMO", "--entry", "100.65", "--setup", "level", "--stop", "pct:1",
                                 "--date", "2026-10-29", "--time", "3:30 PM", now="2026-10-29 15:31")
        self.assertIn("the entry at 3:30 PM is at the close on Thu 2026-10-29", err)
        # an 11:55 AM entry can fill, but --flat-by 11:55 is no way out: the note leaves that advice out
        self.entry(date="2026-10-30", time="11:55 AM", bar_index=31, price=100.40)
        code, text, err = self.plan("--json", now="2026-10-30 11:56")
        self.assertEqual(code, 0, err)
        juma = [w for w in json.loads(text)["warnings"] if "Juma" in w]
        self.assertEqual(juma, ["Friday: PSX stops from 12:00 PM to 2:30 PM (the Juma break). A morning trade is "
                                "held through it, and you cannot sell until 2:30 PM."])

    def test_a_part_opening_range_needs_another_stop(self):
        # D13: psx-entry saw 2 of the 3 range bars (its tape started late), so the midpoint is not sure
        self.entry(opening_range={"high": 100.2, "low": 98.9, "minutes": 15, "bars": 2, "of": 3})
        for extra in ((), ("--stop", "or-low")):
            code, _, err = self.plan(*extra)
            self.assertEqual(code, 1)
            self.assertIn("has only 2 of its 3 bars (the tape started late)", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)
        self.assertEqual(self.plan("--or-high", "100.20", "--or-low", "98.90")[0], 0)   # the full range, by hand
        self.entry(opening_range={"high": 100.2, "low": 98.9, "minutes": 15, "bars": 3, "of": 3})
        self.assertEqual(self.plan()[0], 0)

    def test_min_reward_risk_is_checked_exactly(self):
        # D9: 2.99 / 2.00 = 1.495 shows as 1.50, but it is under 1.5: meets_reward_risk() decides
        self.write(os.path.join("psx", "config.json"), {"min_reward_risk": 1.5})
        flags = ["--symbol", "DEMO", "--entry", "100", "--setup", "level", "--stop", "price:98", "--date",
                 "2026-10-29", "--time", "10:15 AM", "--json", "--target"]
        p = json.loads(self.plan(*(flags + ["price:102.99"]))[1])
        self.assertEqual(p["reward_risk"], 1.5)
        self.assertIn("Reward:risk 1.50 is under your min_reward_risk 1.5 in the config: the target is 2.99 over "
                      "the entry, and 1.5 x the risk is 3.00. psx-risk checks that.", p["warnings"])
        p = json.loads(self.plan(*(flags + ["price:103"]))[1])
        self.assertFalse(any("min_reward_risk" in w for w in p["warnings"]))

    def test_flat_by_config_friday_ramadan_and_late_entries(self):
        self.entry()
        self.write(os.path.join("psx", "config.json"), {"flat_by": "15:25", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:25")
        self.assertTrue(any("under your min_reward_risk 1.5" in w for w in p["warnings"]))
        os.remove(os.path.join("psx", "config.json"))

        self.entry(date="2026-10-30", time="10:00 AM")                  # Friday bar 8 closes at 10:00
        code, text, _ = self.plan(now="2026-10-30 10:01")
        self.assertEqual(code, 0)
        self.assertIn("Flat by       16:20 PKT", text)
        self.assertIn("so it is 16:20: the same 10 minutes before the close", text)
        self.assertIn("Juma break", text)                               # a morning trade is held over it
        self.assertIn("--flat-by 11:55", text)
        self.assertEqual(json.loads(self.plan("--json", now="2026-10-30 10:01")[1])["session"], "friday")
        code, _, err = self.plan("--flat-by", "13:00", now="2026-10-30 10:01")
        self.assertEqual(code, 1)
        self.assertIn("Juma break", err)
        code, _, err = self.plan("--flat-by", "16:30", now="2026-10-30 10:01")
        self.assertIn("at or after the 4:30 PM close", err)

        self.entry()
        code, _, err = self.plan("--flat-by", "15:30")
        self.assertIn("at or after the 3:30 PM close", err)
        self.entry(time="3:25 PM", bar_index=70)
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

        # a Ramadan Monday runs 9:15 AM to 1:30 PM, so flat_by 15:20 is 13:20
        code, text, err = self.plan("--symbol", "DEMO", "--entry", "100.65", "--or-high", "100.20", "--or-low",
                                    "98.90", "--date", "2026-02-23", "--json", now="2026-02-23 10:01")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["flat_by"], p["session"]), ("13:20", "ramadan"))

    def test_closed_days_are_refused(self):
        flags = ["--symbol", "DEMO", "--entry", "100.65", "--or-high", "100.20", "--or-low", "98.90", "--date"]
        for day, why in (("2026-11-09", "PSX holiday: Iqbal Day"), ("2026-10-31", "weekend")):
            code, _, err = self.plan(*(flags + [day]))
            self.assertEqual(code, 1)
            self.assertIn("PSX is closed on", err)
            self.assertIn(why, err)
        self.write(os.path.join("psx", "config.json"), {"open_days": ["2026-11-09"]})   # PSX reopened it
        self.assertEqual(self.plan(*(flags + ["2026-11-09"]))[0], 0)
        self.write(os.path.join("psx", "config.json"), {"holidays": {"2026-10-29": "a PSX notice"}})
        code, _, err = self.plan(*(flags + ["2026-10-29"]))
        self.assertIn("PSX holiday: a PSX notice", err)

    def test_errors_are_one_plain_line(self):
        self.entry()
        cases = []
        with open(os.path.join("psx", "config.json"), "w") as fh:
            fh.write("{not json")
        cases.append(self.plan())
        for cfg in ({"holidays": {"soon": "x"}}, {"flat_by": "late"}, {"costs": {"sales_tax_pct": "x"}}, [1]):
            self.write(os.path.join("psx", "config.json"), cfg)
            cases.append(self.plan())
        os.remove(os.path.join("psx", "config.json"))
        cases.append(run(exitp, ["plan", "--now", "yesterday", "--data", MARKET, "--offline"]))
        with open("bad.csv", "w") as fh:
            fh.write("when,price\n1,2\n")
        cases.append(self.plan("--stop", "atr:1", "--bars", "bad.csv"))
        cases.append(self.plan("--stop", "atr:1", "--bars", "missing.csv"))
        cases.append(self.plan("--entry-bar", "99"))
        cases.append(self.plan("--entry", "nan"))
        with open(os.path.join("psx", "entry.json"), "w") as fh:
            fh.write("[1, 2]")
        cases.append(self.plan())
        for code, _, err in cases:
            self.assertEqual(code, 1, err)
            lines = err.strip().splitlines()
            self.assertEqual(len(lines), 1, err)
            self.assertTrue(lines[0].startswith("exit.py: "), err)
            self.assertNotIn("Traceback", err)

    def test_the_psx_site_only_when_it_is_on(self):
        self.entry()
        os.mkdir("empty")
        exitp.utc_now = lambda: datetime.datetime(2026, 10, 29, 5, 16)   # a live run at 10:16 AM on 10-29
        live = ["plan", "--data", "empty", "--json"]
        code, text, err = run(exitp, live)                              # the site is off by default
        self.assertEqual(code, 0, err)
        self.assertIsNone(json.loads(text)["limits"])
        self.write(os.path.join("psx", "config.json"), {"psx_site": True})
        code, text, err = run(exitp, live + ["--offline"])              # --offline: never a request
        self.assertEqual(code, 0, err)
        with open(os.path.join(MARKET, "company", "DEMO.html"), "rb") as fh:
            page = fh.read()
        asked = []

        def fake(url, timeout=30):
            asked.append(url)
            if url.endswith("/company/DEMO"):
                return page
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        exitp.http_get = fake
        code, text, err = run(exitp, live)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(text)["limits"], EXAMPLE_PLAN["limits"])
        self.assertEqual(asked, ["https://dps.psx.com.pk/company/DEMO"])  # one plain GET of the company page
        # D3: a replay with --now never asks the site (it only has today), even with the site on
        del asked[:]
        code, text, err = run(exitp, ["plan", "--now", "2026-10-29 10:16", "--data", "empty", "--json"])
        self.assertEqual((code, asked, json.loads(text)["limits"]), (0, [], None))
        # D3: with the site on, the site comes first, before the saved page (here the site has other limits)
        page = page.replace(b"108.24", b"108.20")
        code, text, err = run(exitp, ["plan", "--data", MARKET, "--json"])
        self.assertEqual((code, asked), (0, ["https://dps.psx.com.pk/company/DEMO"]), err)
        self.assertEqual(json.loads(text)["limits"]["upper"], 108.2)
        # a plan for another day reads only the saved page: the site only has today's
        exitp.utc_now = lambda: datetime.datetime(2026, 10, 30, 5, 16)
        del asked[:]
        code, text, err = run(exitp, ["plan", "--data", MARKET, "--json"])
        self.assertEqual((code, asked, json.loads(text)["limits"]), (0, [], EXAMPLE_PLAN["limits"]), err)
        exitp.utc_now = lambda: datetime.datetime(2026, 10, 29, 5, 16)

        def down(url, timeout=30):
            raise urllib.error.URLError("no route to the host")
        exitp.http_get = down
        code, text, err = run(exitp, live)
        self.assertEqual(code, 0, err)
        w = " ".join(json.loads(text)["warnings"])
        self.assertIn("Could not reach PSX for https://dps.psx.com.pk/company/DEMO", w)
        code, text, err = run(exitp, ["plan", "--data", MARKET, "--json"])   # the site is down: the saved page
        self.assertEqual(json.loads(text)["limits"], EXAMPLE_PLAN["limits"])

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)
        self.write(os.path.join("psx", "plan.json"), [1])
        code, _, err = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("not a plan", err)
        for broken in (dict(EXAMPLE_PLAN, interval=["5m"]), dict(EXAMPLE_PLAN, entry="lots"),
                       dict(EXAMPLE_PLAN, limits="high"), dict(EXAMPLE_PLAN, date="soon")):
            self.write(os.path.join("psx", "plan.json"), broken)
            code, _, err = run(exitp, ["show"])
            self.assertEqual(code, 1, broken)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertNotIn("Traceback", err)


class SkillDoc(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(ROOT, "psx-exit", name), encoding="utf-8") as fh:
            return fh.read()

    def test_skill_md(self):
        text = self.read("SKILL.md")
        self.assertTrue(text.startswith("---\nname: psx-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):].strip(), SAFETY)
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money",
                     "/psx-timemachine", "/psx-budget", "longs only", "99.55", "101.75", "1.10", "102.85", "0.3586",
                     "88.56 to 108.24", "computed from the last close; an ex-date lowers it",
                     "too tight to survive the spread", "15:20", "16:20", "about 5 minutes late"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit"):
            self.assertNotIn(never, low)

    def test_the_example_shows_the_tools_real_output(self):
        text = self.read("SKILL.md")
        block = text[text.index("## Example"):]
        block = block[block.index("```\n") + 4:]
        block = block[:block.index("```")].strip("\n").splitlines()
        self.assertGreater(len(block), 9)
        d, cwd = tempfile.mkdtemp(), os.getcwd()
        os.chdir(d)
        try:
            os.mkdir("psx")
            shutil.copy(ENTRY, os.path.join("psx", "entry.json"))
            code, out, err = run(exitp, ["plan", "--stop", "or-mid", "--target", "1R", "--write", "--now",
                                         "2026-10-29 10:16", "--data", MARKET, "--offline"])
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)
        self.assertEqual(code, 0, err)
        self.assertEqual(block, out.strip("\n").splitlines())

    def test_no_us_leftovers_and_python_3_8(self):
        import ast
        words = ("Alpaca", "Yahoo", "Stooq", "Nasdaq", "EDGAR", "NYSE", "FINRA", "New York", "Eastern", "Congress",
                 "Pelosi", "TSLA", "trader-", "trader/")
        for f in sorted(os.listdir(os.path.join(ROOT, "psx-exit"))):
            if not f.endswith((".md", ".py", ".json")):
                continue
            src = self.read(f)
            mine = src.replace("psx-trader-skill", "")     # the canonical UA names the pack itself
            for word in words:
                self.assertFalse(word in mine, "%s: %s" % (f, word))
            for pat in (r"\bSEC\b", r"\bET\b", r"\$\s?\d", "\u2014"):
                self.assertIsNone(re.search(pat, src), "%s: %s" % (f, pat))
        ast.parse(self.read("exit.py"), feature_version=(3, 8))


# ---------------------------------------------------------------- swing style (SPEC v2 2.4 and 2.5, Task 1)

V2 = os.path.join(FIX, "psx-v2")
SWING_PLAN = os.path.join(V2, "contract", "plan-swing.json")
UP = os.path.join(V2, "paths", "up", "mkt_summary")
EXAMPLE_CONFIG = os.path.join(ROOT, "psx-budget", "config.example.json")
EVENING = "2026-10-28 17:00"            # the evening before the buy day Thu 2026-10-29
BONUS_DAY = datetime.date(2026, 10, 21)   # 5 sessions before Wed 2026-10-28
SWING_LINES = [
    "Swing plan: DEMO long, buy on Thu 2026-10-29, 20-day closing-high breakout (level 96.56)",
    "  Entry         99.38  limit buy: the last close 98.40 (Wed 2026-10-28) + 1%",
    "  Stop          96.25  2 x ATR(14) under the entry: daily ATR 1.5650 over the 14 sessions to Wed 2026-10-28",
    "  Target        105.64  2R (two times the risk)",
    "  Risk a share  3.13 (3.15% of the entry)",
    "  Reward:risk   2.00",
    "  Costs         about 0.3504 a share, buy and sell at the stop (estimated, commission both sides)",
    "  Price limits  88.56 to 108.24 (LDCP 98.40): computed from the last close; an ex-date lowers it",
    "  Exit by       Fri 2026-11-27 at 16:20 PKT: 20 sessions after the buy day (the time stop)",
    "  Trend rule    out after a close under the lowest close of the 10 sessions before it (now 92.36)",
    "  Gap risk      a next-day open at the lower limit 89.44 loses about 10.28 a share, costs included: 2.95 times "
    "the loss at the stop",
    "Set before the entry. If price hits the stop, you are out; never move it further away.",
    "A stop is a plan, not a guarantee: a gap or a fast market can fill past it.",
    "PSX has no stop order: sell at the stop yourself, or use your broker app's stop feature.",
    "Free PSX prices are about 5 minutes late: watch the stop on your broker's screen.",
    "Held overnight: a gap can open past the stop. Then a sell fills at the open, not at the stop.",
]
SWING_END = ["Note: This plan is for 2026-10-29, not today (2026-10-28).", "Wrote psx/plan.json.",
             "Next: /psx-timemachine replays the next 20 sessions, then /psx-budget sizes it on the buy day. "
             "Nothing was placed."]
# psx-scanner's hit for DEMO at the Wed 2026-10-28 close (scan.py daily on the sample market)
DEMO_HIT = {"symbol": "DEMO", "name": None, "sector": None, "setups": ["breakout_20d"], "close": 98.4,
            "close_date": "2026-10-28", "level": 96.56, "rsi": 75.7, "volume": 3787500}


def closing_files(dest, keep=lambda day: True, *sources):
    """Copy closing files (the sample market's, then each source folder's) into dest/mkt_summary."""
    folder = os.path.join(dest, "mkt_summary")
    os.makedirs(folder, exist_ok=True)
    for src in (os.path.join(MARKET, "mkt_summary"),) + sources:
        for f in sorted(os.listdir(src)):
            if keep(datetime.date.fromisoformat(f[:10])):
                shutil.copy(os.path.join(src, f), os.path.join(folder, f))
    return dest


def make_bonus(dest):
    """The sample market's closing files before Thu 2026-10-29 with a made-up 20% bonus (XB) on DEMO on
    Wed 2026-10-21, 5 sessions before 2026-10-28: every older DEMO price and LDCP x 1.2 (on the tick),
    the ex-date's own LDCP lowered to the new price scale (the old close) and its short name flagged XB."""
    folder = os.path.join(dest, "mkt_summary")
    os.makedirs(folder)
    for f in sorted(os.listdir(os.path.join(MARKET, "mkt_summary"))):
        day, src = datetime.date.fromisoformat(f[:10]), os.path.join(MARKET, "mkt_summary", f)
        if day >= THU:
            continue
        if day > BONUS_DAY:
            shutil.copy(src, os.path.join(folder, f))
            continue
        with zipfile.ZipFile(src) as z:
            text = z.read(z.namelist()[0]).decode("latin-1")
        lines = []
        for line in text.split("\r\n"):
            cells = line.split("|")
            if len(cells) > 9 and cells[1] == "DEMO":
                if day == BONUS_DAY:
                    cells[3] += "XB"         # its LDCP stays the old close: PSX lowered it for the bonus
                else:
                    for i in (4, 5, 6, 7, 9):
                        cells[i] = "%.2f" % exitp.round_half_up_tick(float(cells[i].replace(",", "")) * 1.2)
                line = "|".join(cells)
            lines.append(line)
        with open(os.path.join(folder, f[:10] + ".lis"), "w", encoding="latin-1", newline="") as fh:
            fh.write("\r\n".join(lines))
    return dest


class Swing(unittest.TestCase):
    """psx-exit in swing style: a limit buy for the buy day, from the closing files (SPEC v2 2.4)."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp()
        # the evening of Wed 10-28: no company page, no tape and no closing file of Thu 10-29 yet
        cls.evening = closing_files(os.path.join(cls.base, "evening"), lambda day: day < THU)
        cls.later = closing_files(os.path.join(cls.base, "later"), lambda day: True, UP)   # to Fri 11-27
        cls.bonus = make_bonus(os.path.join(cls.base, "bonus"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("psx")
        self.config()
        self.watch(DEMO_HIT)
        exitp.SLEEP = exitp.http_get = no_network

    def tearDown(self):
        exitp.SLEEP = exitp.http_get = no_network
        exitp.utc_now = REAL_CLOCK
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def config(self, swing=None, **change):
        """psx/config.json: the example config with "style": "swing", the swing keys and others changed."""
        with open(EXAMPLE_CONFIG) as fh:
            cfg = json.load(fh)
        cfg["style"] = "swing"
        cfg["swing"].update(swing or {})
        cfg.update(change)
        self.write(os.path.join("psx", "config.json"), cfg)

    def watch(self, *hits):
        self.write(os.path.join("psx", "watchlist.json"), {"created_by": "psx-scanner", "mode": "daily",
                                                           "as_of": "2026-10-28 close", "hits": list(hits)})

    def plan(self, *args, **kw):
        return run(exitp, ["plan", "--now", kw.get("now", EVENING), "--data", kw.get("data", self.evening),
                           "--offline"] + list(args))

    def ok(self, *args, **kw):
        """A plan with --json that must pass -> the plan, with its notes under "warnings"."""
        code, text, err = self.plan(*(args + ("--json",)), **kw)
        self.assertEqual(code, 0, err)
        return json.loads(text)

    def refused(self, *args, **kw):
        """A plan that must be refused with one plain line -> that line."""
        code, text, err = self.plan(*args, **kw)
        self.assertEqual(code, 1, text + err)
        lines = err.strip().splitlines()
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith("exit.py: "), err)
        self.assertNotIn("Traceback", err)
        return lines[0]

    def read_plan(self):
        with open(os.path.join("psx", "plan.json")) as fh:
            return json.load(fh)

    def test_the_example_plan_is_the_contract_file(self):
        code, text, err = self.plan("--symbol", "DEMO", "--write")
        self.assertEqual(code, 0, err)
        with open(SWING_PLAN) as fh:
            want = json.load(fh)
        p = self.read_plan()
        self.assertEqual(list(p), list(want))                          # the keys of 2.5, in order
        for k in want:
            self.assertEqual(p[k], want[k], k)
        # the acceptance numbers of Task 1
        self.assertEqual((p["entry"], p["stop"], p["target"], p["exit_by"], p["exit_by_time"]),
                         (99.38, 96.25, 105.64, "2026-11-27", "16:20"))
        self.assertEqual((p["trend_level"], p["limits"]["lower"], p["limits"]["upper"]), (92.36, 88.56, 108.24))
        self.assertEqual((p["costs_per_share_est"], p["gap_loss_per_share"]), (0.3504, 10.2782))
        self.assertEqual((p["style"], p["flat_by"], p["interval"], p["atr_from"]), ("swing", None, "1d",
                                                                                     "closing files"))

    def test_the_describe_lines(self):
        code, text, err = self.plan("--symbol", "DEMO", "--write")
        self.assertEqual(code, 0, err)
        self.assertEqual(text.splitlines(), SWING_LINES + SWING_END)
        self.assertNotIn("Flat by", text)
        self.assertEqual(SWING_LINES[-1], exitp.OVERNIGHT)             # the last line of a swing plan (5.2)
        low = text.lower()
        for never in ("pro" + "fit", "p&l", "real " + "time", "live", "$"):
            self.assertNotIn(never, low)

    def test_the_sample_market_gives_the_same_plan_from_the_page(self):
        # the saved company page DEMO.html is "As of" Thu 10-29 (made up), so its CIRCUIT BREAKER counts
        with open(SWING_PLAN) as fh:
            want = json.load(fh)
        p = self.ok("--symbol", "DEMO", data=MARKET)
        self.assertEqual(p.pop("limits"), {"lower": 88.56, "upper": 108.24, "ldcp": 98.4, "from": "company page"})
        self.assertEqual(p.pop("warnings"), ["This plan is for 2026-10-29, not today (2026-10-28)."])
        self.assertEqual(p, dict((k, v) for k, v in want.items() if k != "limits"))

    def test_atr_uses_the_closing_files_not_the_bars_file(self):
        # the sample market has DEMO's 5-minute bars of 10-29 too: a swing atr:2 never reads them
        p = self.ok("--symbol", "DEMO", "--stop", "atr:2", data=MARKET, now="2026-10-29 10:16")
        self.assertEqual((p["date"], p["stop"], p["atr"], p["atr_bars"], p["atr_from"]),
                         ("2026-10-29", 96.25, 1.565, 14, "closing files"))
        self.assertEqual(p["stop_detail"], "daily ATR 1.5650 over the 14 sessions to Wed 2026-10-28")
        self.assertIn("--bars is a day-trade flag", self.refused("--symbol", "DEMO", "--bars",
                                                                os.path.join(MARKET, "bars", "DEMO.csv")))
        # the same stop rule in day style is the intraday ATR of today's bars, from the bars file
        self.write(os.path.join("psx", "config.json"), {"style": "day"})
        shutil.copy(ENTRY, os.path.join("psx", "entry.json"))
        code, text, err = run(exitp, ["plan", "--stop", "atr:2", "--json", "--now", "2026-10-29 10:16", "--data",
                                      MARKET, "--offline"])
        self.assertEqual(code, 0, err)
        day = json.loads(text)
        self.assertEqual((day["stop"], day["atr_bars"]), (99.66, 9))    # 100.65 - 2 x 0.4944, on 9 bars
        self.assertIn("from the bars file", day["stop_detail"])
        self.assertNotIn("style", day)                                  # a day plan keeps the v1 shape

    def test_low_and_level_stops(self):
        p = self.ok("--symbol", "DEMO", "--stop", "low:10")
        self.assertEqual((p["stop"], p["stop_rule"]), (92.06, "low"))  # the lowest low of 10 sessions 92.07, less 0.01
        self.assertEqual(p["stop_label"], "one tick under the lowest low of 10 sessions")
        self.assertEqual(p["stop_detail"], "low 92.07 in the 10 sessions to Wed 2026-10-28, less 0.01")
        p = self.ok("--symbol", "DEMO", "--stop", "level:0.5")
        self.assertEqual((p["stop"], p["stop_rule"], p["level"]), (95.77, "level", 96.56))
        self.assertEqual(p["stop_label"], "0.5 x ATR(14) under the scan's level")
        self.assertEqual(p["stop_detail"], "96.56 - 0.5 x 1.5650 (the daily ATR) = 95.7775, rounded down")
        p = self.ok("--symbol", "DEMO", "--stop", "pct:3")                # as in a day trade
        self.assertEqual((p["stop"], p["stop_label"]), (96.39, "3% under the entry"))
        self.assertEqual(self.ok("--symbol", "DEMO", "--stop", "price:95")["stop"], 95.0)
        for bad, why in (("low", "like low:10"), ("low:0", "low:N (--stop) needs a number above 0"),
                         ("low:1.5", "a whole number of sessions from 1 to 60"), ("low:61", "from 1 to 60"),
                         ("atr:0", "atr:K (--stop) needs a number above 0"), ("pct:100", "under 100"),
                         ("wide", "is not a swing stop")):
            self.assertIn(why, self.refused("--symbol", "DEMO", "--stop", bad), bad)

    def test_a_level_stop_needs_the_scans_level(self):
        cases = []
        self.watch(dict(DEMO_HIT, symbol="OGDC"))
        cases.append(("DEMO is not in psx/watchlist.json", self.refused("--symbol", "DEMO", "--stop", "level:0.5")))
        self.watch(dict(DEMO_HIT, setups=["bull_flag"], level=None))
        cases.append(("its setup (bull flag) has no level", self.refused("--symbol", "DEMO", "--stop", "level:1")))
        self.watch(dict(DEMO_HIT, close_date="2026-10-27"))
        cases.append(("is from the 2026-10-27 close, not the 2026-10-28 close (run scan.py daily again)",
                      self.refused("--symbol", "DEMO", "--stop", "level:1")))
        os.remove(os.path.join("psx", "watchlist.json"))
        cases.append(("there is no psx/watchlist.json yet (run /psx-scanner: scan.py daily)",
                      self.refused("--symbol", "DEMO", "--stop", "level:1")))
        for why, line in cases:
            self.assertIn("a level:K stop needs the scan's level of DEMO for the 2026-10-28 close", line)
            self.assertIn(why, line)
        # with no scan the atr:2 plan still works: it just names no setup
        p = self.ok("--symbol", "DEMO")
        self.assertEqual((p["setup"], p["level"], p["stop"]), (None, None, 96.25))
        code, text, _ = self.plan("--symbol", "DEMO")
        self.assertEqual(text.splitlines()[0], "Swing plan: DEMO long, buy on Thu 2026-10-29")

    def test_day_stops_and_day_flags_are_refused(self):
        for stop in ("or-mid", "or-low"):
            line = self.refused("--symbol", "DEMO", "--stop", stop)
            self.assertIn("the stop %s (--stop) is an opening-range stop, for a day trade, and your style is swing"
                          % stop, line)
        for flag, value in (("--or-high", "100.20"), ("--or-low", "98.90"), ("--time", "10:15 AM"),
                            ("--entry-bar", "8"), ("--interval", "5m"), ("--flat-by", "15:00"), ("--bars", "x.csv")):
            line = self.refused("--symbol", "DEMO", flag, value)
            self.assertIn("exit.py: %s is a day-trade flag, and your style is swing: " % flag, line)
        self.assertIn("Set swing.max_hold_sessions in psx/config.json", self.refused("--symbol", "DEMO", "--flat-by",
                                                                                     "15:00"))
        self.config(swing={"stop": "or-mid"})
        self.assertIn("the stop or-mid (swing.stop in psx/config.json) is an opening-range stop",
                      self.refused("--symbol", "DEMO"))

    def test_the_buy_day(self):
        def buy(text):
            return exitp.swing_day(datetime.datetime.strptime(text, "%Y-%m-%d %H:%M"))
        self.assertEqual(buy("2026-10-29 11:00"), THU)                               # a trading day before the close
        self.assertEqual(buy("2026-10-29 09:20"), THU)                               # the pre-open
        self.assertEqual(buy("2026-10-28 17:00"), THU)                               # after the close: the next session
        self.assertEqual(buy("2026-10-29 15:30"), FRI)                               # at the close
        self.assertEqual(buy("2026-10-31 10:00"), datetime.date(2026, 11, 2))        # a Saturday gives Monday
        self.assertEqual(buy("2026-11-06 12:30"), datetime.date(2026, 11, 6))        # the Juma break: still today
        self.assertEqual(buy("2026-11-06 17:00"), datetime.date(2026, 11, 10))       # Mon 11-09 is Iqbal Day
        self.assertEqual(self.ok("--symbol", "DEMO", now="2026-10-29 11:00")["date"], "2026-10-29")
        p = self.ok("--symbol", "DEMO", now="2026-10-31 10:00", data=self.later)
        self.assertEqual((p["date"], p["last_close_date"], p["exit_by"]), ("2026-11-02", "2026-10-30", "2026-12-01"))
        p = self.ok("--symbol", "DEMO", now="2026-11-06 17:00", data=self.later)
        self.assertEqual((p["date"], p["last_close_date"], p["exit_by"]), ("2026-11-10", "2026-11-06", "2026-12-08"))
        self.assertIn("This plan is for 2026-11-10, not today (2026-11-06).", p["warnings"])
        self.assertIn("PSX is closed on Mon 2026-11-09 (PSX holiday: Iqbal Day)",
                      self.refused("--symbol", "DEMO", "--date", "2026-11-09", data=self.later))
        self.assertIn("date wants YYYY-MM-DD", self.refused("--symbol", "DEMO", "--date", "soon"))

    def test_a_target_over_the_limit_is_a_note_in_swing_and_a_refusal_in_day(self):
        p = self.ok("--symbol", "DEMO", "--target", "price:110")
        self.assertEqual(p["target"], 110.0)
        self.assertIn("The target 110.00 is over the upper price limit 108.24 of Thu 2026-10-29: it cannot fill on "
                      "Thu 2026-10-29; it can on a later day.", p["warnings"])
        self.assertIn("the entry 120.00 is above the Thu 2026-10-29 upper price limit 108.24: it cannot fill that day",
                      self.refused("--symbol", "DEMO", "--entry", "120"))     # an entry outside them: as in v1
        self.assertIn("the entry 85.00 is under the Thu 2026-10-29 lower price limit 88.56",
                      self.refused("--symbol", "DEMO", "--entry", "85", "--stop", "price:84"))
        # day style: a target over the limit is still refused (the v1 rule)
        self.write(os.path.join("psx", "config.json"), {"style": "day"})
        line = self.refused("--symbol", "DEMO", "--entry", "99.38", "--setup", "level", "--stop", "price:96.25",
                            "--target", "price:110", "--date", "2026-10-29")
        self.assertIn("the target 110.00 is above the Thu 2026-10-29 upper price limit 108.24: it cannot fill that day",
                      line)

    def test_a_stop_at_the_lower_limit_can_be_hit_that_day(self):
        # PSX trades at a limit, never past it: a stop AT the buy day's lower limit 88.56 can be hit that day
        # (psx-alerts rings it from that day's low), and only a stop UNDER it waits for the next session
        def stop_notes(stop):
            p = self.ok("--symbol", "DEMO", "--stop", "price:" + stop, "--target", "price:108")
            self.assertEqual(p["limits"]["lower"], 88.56)
            return [w for w in p["warnings"] if w.startswith("The stop")]
        self.assertEqual(stop_notes("88.56"), ["The stop 88.56 is at the lower price limit 88.56 of Thu 2026-10-29: the "
                                               "price can trade at the limit, so the stop can be hit that day. At the "
                                               "limit there may be no buyers, so you may not get out."])
        self.assertEqual(stop_notes("88.55"), ["The stop 88.55 is under the lower price limit 88.56 of Thu 2026-10-29: "
                                               "it cannot be hit that day; it counts from the next session."])
        self.assertEqual(stop_notes("88.57"), [])
        # day style keeps its v1 words for both (one warning, "at or under")
        self.write(os.path.join("psx", "config.json"), {"style": "day"})
        for stop in ("88.56", "88.55"):
            code, text, err = self.plan("--symbol", "DEMO", "--entry", "99.38", "--setup", "level", "--stop",
                                        "price:" + stop, "--target", "price:108", "--date", "2026-10-29", "--json")
            self.assertEqual(code, 0, err)
            self.assertIn("The stop %s is at or under the Thu 2026-10-29 lower price limit 88.56. At the limit there may "
                          "be no buyers, so you may not get out." % stop, json.loads(text)["warnings"])

    def test_one_side_commission_changes_nothing_in_swing(self):
        # D30: a swing sell on a later day always pays commission, whatever "day_trade_one_side" says
        with open(SWING_PLAN) as fh:
            want = json.load(fh)
        self.config(costs={"commission_pct": 0.15, "commission_min_per_share": 0.03, "sales_tax_pct": 15,
                           "other_charges_pct": 0.0066, "day_trade_one_side": True})
        p = self.ok("--symbol", "DEMO")
        self.assertEqual((p["costs_per_share_est"], p["gap_loss_per_share"], p["gap_r"]), (0.3504, 10.2782, 2.95))
        self.assertEqual(p["costs_per_share_est"], want["costs_per_share_est"])
        day_one_side = exitp.costs_per_share(99.38, 96.25, {"costs": {"day_trade_one_side": True}})
        self.assertEqual(day_one_side, Decimal("0.1843"))             # a day plan counts one side, as set

    def test_show_reads_a_swing_plan(self):
        code, _, err = self.plan("--symbol", "DEMO", "--write")
        self.assertEqual(code, 0, err)
        code, text, err = run(exitp, ["show", "--now", "2026-10-28 17:05"])
        self.assertEqual(code, 0, err)
        self.assertEqual(text.splitlines(), SWING_LINES + ["Note: this plan is for 2026-10-29, not today (2026-10-28)."])
        code, text, _ = run(exitp, ["show", "--now", "2026-10-29 09:20"])
        self.assertEqual(text.splitlines(), SWING_LINES)
        code, text, _ = run(exitp, ["show", "--json"])
        self.assertEqual(json.loads(text), self.read_plan())
        code, text, err = run(exitp, ["show", "--plan", SWING_PLAN, "--now", "2026-10-29 09:20"])   # the contract file
        self.assertEqual((code, text.splitlines()), (0, SWING_LINES), err)
        plan = self.read_plan()
        for broken, why in ((dict(plan, exit_by=None), "is missing 'exit_by'"),
                            (dict((k, v) for k, v in plan.items() if k != "exit_by"), "is missing 'exit_by'"),
                            (dict(plan, exit_by="soon"), "has a bad exit_by 'soon'"),
                            (dict(plan, gap_r="lots"), "not a number")):
            self.write(os.path.join("psx", "plan.json"), broken)
            code, _, err = run(exitp, ["show"])
            self.assertEqual(code, 1, broken)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertIn(why, err)
            self.assertNotIn("Traceback", err)

    def test_too_few_closing_files_give_the_plain_line(self):
        ten = closing_files("ten", lambda day: datetime.date(2026, 10, 15) <= day < THU)    # 10 sessions
        line = self.refused("--symbol", "DEMO", data=ten)
        folder = os.path.join("ten", "mkt_summary")
        self.assertIn("a swing plan needs 20 daily bars of DEMO from the closing files before Thu 2026-10-29, and %s "
                      "gives 10 (from 10 files)." % folder, line)
        self.assertIn("The PSX site is off for this tool: save the PSX closing files "
                      "(https://dps.psx.com.pk/download/mkt_summary/YYYY-MM-DD.Z) in %s" % folder, line)   # SITE_OFF
        os.mkdir("none")
        self.assertIn("gives 0 (from 0 files)", self.refused("--symbol", "DEMO", data="none"))
        self.config(psx_site=True)
        self.assertIn("Save the closing files in %s (a run with --offline or --now reads only the data folder)."
                      % folder, self.refused("--symbol", "DEMO", data=ten))
        # the site on and a live run: it asks PSX only for the missing closing files, and says what it got
        asked = []

        def fake(url, timeout=30):
            asked.append(url)
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        exitp.http_get = fake
        exitp.utc_now = lambda: datetime.datetime(2026, 10, 28, 12, 0)         # 17:00 in Karachi
        code, _, err = run(exitp, ["plan", "--symbol", "DEMO", "--data", ten])
        self.assertEqual(code, 1, err)
        self.assertIn("PSX had no more closing files for those days.", err)
        self.assertTrue(asked and all(u.startswith("https://dps.psx.com.pk/download/mkt_summary/") for u in asked))
        self.assertEqual(len(asked), len(set(asked)))                          # each day once
        # the newest file must be the session before the buy day
        old = closing_files("old", lambda day: day <= datetime.date(2026, 10, 27))
        line = self.refused("--symbol", "DEMO", data=old)
        self.assertIn("the newest closing file in %s is from Tue 2026-10-27, not Wed 2026-10-28, the session before "
                      "the buy day: save it as %s" % (os.path.join("old", "mkt_summary"),
                                                       os.path.join("old", "mkt_summary", "2026-10-28.Z")), line)

    def test_the_board_meeting_note(self):
        # a made-up psx/metrics.json in psx-analyst's shape: a meeting from the buy day to the exit-by day is a note
        note = "DEMO has a board meeting on Tue 2026-11-03 (psx/metrics.json). Results can gap the price."

        def meeting(when, symbol="DEMO"):
            self.write(os.path.join("psx", "metrics.json"), {"as_of": "2026-10-28", "units": "Rs thousands",
                                                             "companies": [{"symbol": symbol,
                                                                            "next_board_meeting": when}]})
            return [w for w in self.ok("--symbol", "DEMO")["warnings"] if "board meeting" in w]
        self.assertEqual(meeting("2026-11-03"), [note])
        self.assertEqual(meeting("2026-10-29"), ["DEMO has a board meeting on Thu 2026-10-29 (psx/metrics.json). "
                                                 "Results can gap the price."])          # the buy day counts
        self.assertEqual(len(meeting("2026-11-27")), 1)                                  # so does the exit-by day
        for when in ("2026-12-01", "2026-10-27", None, "soon"):
            self.assertEqual(meeting(when), [], when)
        self.assertEqual(meeting("2026-11-03", symbol="OGDC"), [])
        code, text, err = self.plan("--symbol", "DEMO")
        self.assertEqual(code, 0, err)
        with open(os.path.join("psx", "metrics.json"), "w") as fh:
            fh.write("{broken")
        self.assertIn("psx/metrics.json could not be read, so its board meeting dates were not checked.",
                      self.ok("--symbol", "DEMO")["warnings"])                           # never a refusal

    def test_the_gap_fields_and_the_gap_line(self):
        p = self.ok("--symbol", "DEMO")
        self.assertEqual((p["gap_low"], p["gap_loss_per_share"], p["gap_r"]), (89.44, 10.2782, 2.95))
        self.assertEqual(exitp.price_limits(99.38)[0], 89.44)                # the lower limit of the entry
        costs = exitp.round_trip_cost(99.38, 89.44, 1000, exitp.trade_costs({"style": "swing"})) / 1000
        self.assertAlmostEqual(99.38 - 89.44 + costs, 10.2782, places=4)     # 9.94 of price, 0.3382 of costs
        self.assertEqual(round(10.2782 / (3.13 + 0.3504), 2), 2.95)         # times the loss at the stop
        code, text, _ = self.plan("--symbol", "DEMO")
        self.assertIn("  Gap risk      a next-day open at the lower limit 89.44 loses about 10.28 a share, costs "
                      "included: 2.95 times the loss at the stop", text.splitlines())
        tight = self.ok("--symbol", "DEMO", "--stop", "price:98.00")          # a tighter stop: the same gap, more R
        self.assertEqual((tight["risk_per_share"], tight["costs_per_share_est"]), (1.38, 0.3535))
        self.assertEqual((tight["gap_loss_per_share"], tight["gap_r"]), (10.2782, 5.93))   # 10.2782 / 1.7335

    def test_a_bonus_inside_the_14_bars_gives_the_same_atr_and_stop(self):
        # a made-up 20% XB on DEMO 5 sessions before 10-28: older prices and LDCPs x 1.2, the ex-date LDCP lowered
        p = self.ok("--symbol", "DEMO", data=self.bonus)
        plain_files = self.ok("--symbol", "DEMO")
        self.assertEqual((p["stop"], plain_files["stop"]), (96.25, 96.25))
        # the closing files keep prices on the Rs 0.01 tick, so the adjusted ATR is 1.565 only to 3 decimals
        self.assertAlmostEqual(p["atr"], 1.565, delta=0.0005)
        self.assertEqual((p["atr_bars"], p["atr_from"]), (14, "closing files"))
        sessions = exitp.recent_sessions(THU, 60, self.bonus, {}, True, include_end=False)
        bars = exitp.daily_bars("DEMO", sessions)
        self.assertEqual([b["date"] for b in bars if b["flags"]], [BONUS_DAY])
        self.assertGreater(exitp.daily_atr(bars, 14)[0], 1.7)               # raw bars: older ranges on the old scale
        # D27 and 2.4 step 9: the trend level is the lowest of the last 10 payout-adjusted closes, the same as
        # with no bonus. The raw closes would give 94.28: their older closes are on the old price scale.
        self.assertEqual((p["trend_level"], plain_files["trend_level"]), (92.36, 92.36))
        self.assertEqual(min(b["close"] for b in bars[-10:]), 94.28)
        code, text, err = self.plan("--symbol", "DEMO", data=self.bonus)
        self.assertEqual(code, 0, err)
        self.assertIn("  Trend rule    out after a close under the lowest close of the 10 sessions before it (now 92.36)",
                      text.splitlines())

    def xd_page(self, folder):
        """The sample company page of DEMO made into a made-up ex-date page of Thu 2026-10-29 at 9:20 AM, before
        the open: a cash payout of 3.00 (XD), so PSX lowered the LDCP from the close 98.40 to 95.40 and the
        CIRCUIT BREAKER to 85.86 to 104.94."""
        with open(os.path.join(MARKET, "company", "DEMO.html"), encoding="utf-8") as fh:
            page = fh.read()
        for old, new in (('quote__name">Demo Industries Limited<',
                          'quote__name">Demo Industries Limited<div class="tag tag--yellow">XD</div><'),
                         ("As of Thu, Oct 29, 2026 3:49 PM", "As of Thu, Oct 29, 2026 9:20 AM"),
                         ('data-low="88.56" data-high="108.24"', 'data-low="85.86" data-high="104.94"'),
                         ('LDCP</div><div class="stats_value">98.40', 'LDCP</div><div class="stats_value">95.40')):
            self.assertEqual(page.count(old), 1, old)
            page = page.replace(old, new)
        os.makedirs(os.path.join(folder, "company"))
        with open(os.path.join(folder, "company", "DEMO.html"), "w", encoding="utf-8") as fh:
            fh.write(page)
        self.assertEqual(exitp.parse_company(page)["flags"], ["XD"])
        return folder

    def day_file(self, folder, day, row):
        """A made-up closing file of one DEMO row (the closing file's own columns) for date `day`."""
        with open(os.path.join(folder, "mkt_summary", day.isoformat() + ".lis"), "w") as fh:
            fh.write("%s|DEMO|0818|%s|\n" % (day.strftime("%d%b%Y").upper(), row))

    def test_an_ex_date_on_the_buy_day_is_not_planned_on_the_old_prices(self):
        # the plan is made on the buy day, Thu 10-29, and that day's page shows the ex-date: its LDCP is 95.40,
        # not the last close 98.40. The entry 99.38 and the stop 96.25 from the last close would be on the old
        # prices: the stop over the day's LDCP, so a buy that fills near the open is under the stop at once.
        xd = self.xd_page(closing_files("xd", lambda day: day < THU))
        pre_open = "2026-10-29 09:20"
        ex = "the LDCP of DEMO on Thu 2026-10-29 is 95.40 in its company page, not the last close 98.40."
        line = self.refused("--symbol", "DEMO", "--write", data=xd, now=pre_open)
        self.assertEqual(line, "exit.py: " + ex + " PSX moves it on an ex-date (a payout, bonus or right) or a split. "
                         "It can also move after days with no trade. So the closing files before that day do not fit "
                         "its prices: give --entry 96.35 (the new LDCP + 1%), or plan for the next session once the "
                         "closing file of Thu 2026-10-29 is saved")
        self.assertFalse(os.path.exists(os.path.join("psx", "plan.json")))
        # your own entry plans, with a note
        note = ("The LDCP of DEMO on Thu 2026-10-29 is 95.40 in its company page, not the last close 98.40. PSX moves "
                "it on an ex-date or a split, and it can move after days with no trade. The ATR and the trend level "
                "here come from the closing files before that day, so they may be on the old prices.")
        p = self.ok("--symbol", "DEMO", "--entry", "96.35", data=xd, now=pre_open)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (96.35, 93.22, 102.61))
        self.assertEqual(p["limits"], {"lower": 85.86, "upper": 104.94, "ldcp": 95.4, "from": "company page"})
        self.assertEqual(p["warnings"], [note])
        # the old entry again, now by hand: the stop is over the day's LDCP, and the plan says so
        p = self.ok("--symbol", "DEMO", "--entry", "99.38", data=xd, now=pre_open)
        self.assertEqual(p["stop"], 96.25)
        self.assertIn("The stop 96.25 is at or over 95.40, the LDCP of Thu 2026-10-29. A limit buy at 99.38 can fill "
                      "lower, and a fill at 95.40 or under is already at or under the stop.", p["warnings"])
        out = [line] + p["warnings"]
        # low:N and level:K are prices from the old closing files: refused that day
        for stop in ("low:10", "level:0.5"):
            out.append(self.refused("--symbol", "DEMO", "--entry", "96.35", "--stop", stop, data=xd, now=pre_open))
            self.assertIn(ex + " The stop %s would come from the closing files before that day, and they do not fit "
                          "that day's prices. Use --stop atr:K, pct:X or price:X" % stop, out[-1])
        # the same from that day's own closing file (no page): a plan for a past day
        xdfile = closing_files("xdfile", lambda day: day < THU)
        self.day_file(xdfile, THU, "Demo IndustriesXD|95.40|96.10|94.90|95.60|4236500|95.40")
        out.append(self.refused("--symbol", "DEMO", "--date", "2026-10-29", data=xdfile, now="2026-10-30 10:00"))
        self.assertIn("the LDCP of DEMO on Thu 2026-10-29 is 95.40 in that day's closing file, not the last close "
                      "98.40. PSX moves it on an ex-date", out[-1])
        # what the line says works: after that file, the next session's plan is on the new prices
        p = self.ok("--symbol", "DEMO", data=xdfile, now="2026-10-29 17:00")
        self.assertEqual((p["date"], p["last_close"], p["entry"], p["trend_level"]), ("2026-10-30", 95.6, 96.56, 89.54))
        self.assertFalse([w for w in p["warnings"] if "LDCP" in w], p["warnings"])
        self.assertGreater(check_words(self, "\n".join(out + [note])), 5)

    def test_a_stop_at_or_over_the_ldcp_is_a_note(self):
        # a limit buy fills at its price or lower: with the stop at or over the day's LDCP (here the last close
        # 98.40), a fill at the LDCP or under is at or under the stop
        def ldcp_notes(stop):
            p = self.ok("--symbol", "DEMO", "--stop", "price:" + stop, "--target", "price:104")
            return [w for w in p["warnings"] if "the LDCP of" in w]
        self.assertEqual(ldcp_notes("98.40"), ["The stop 98.40 is at or over 98.40, the LDCP of Thu 2026-10-29. A limit "
                                               "buy at 99.38 can fill lower, and a fill at 98.40 or under is already "
                                               "at or under the stop."])
        self.assertEqual(len(ldcp_notes("98.90")), 1)
        self.assertEqual(ldcp_notes("98.39"), [])
        self.assertEqual([w for w in self.ok("--symbol", "DEMO")["warnings"] if "LDCP" in w], [])   # 96.25: none

    def test_an_ldcp_that_did_not_move_is_no_ex_date(self):
        # ILP did not trade on Thu 10-15 (no row): its LDCP on Fri 10-16 is still the 10-14 close 72.93
        p = self.ok("--symbol", "ILP", "--date", "2026-10-16", data=MARKET, now="2026-10-16 17:00")
        self.assertEqual(p["limits"], {"lower": 65.64, "upper": 80.22, "ldcp": 72.93, "from": "computed",
                                       "base": "file"})
        self.assertEqual(p["warnings"], ["ILP did not trade on Thu 2026-10-15: its last close is from Wed 2026-10-14."])
        # DEMO did not trade on Wed 10-28, and PSX set a close (98.00) in that day's file: the 10-29 LDCP 98.00 is
        # that close, not a moved one, though the last traded close (Tue 10-27) is 96.56
        data = closing_files("set", lambda day: day < datetime.date(2026, 10, 28))
        self.day_file(data, datetime.date(2026, 10, 28), "Demo Industries|0|0|0|98.00|0|96.56")
        self.day_file(data, THU, "Demo Industries|98.10|98.90|97.70|98.50|1500000|98.00")
        p = self.ok("--symbol", "DEMO", "--date", "2026-10-29", data=data, now="2026-10-30 10:00")
        self.assertEqual((p["last_close"], p["limits"]["ldcp"], p["limits"]["base"]), (96.56, 98.0, "file"))
        self.assertIn("DEMO did not trade on Wed 2026-10-28: its last close is from Tue 2026-10-27.", p["warnings"])
        self.assertFalse([w for w in p["warnings"] if "LDCP of" in w], p["warnings"])

    def test_the_entry(self):
        p = self.ok("--symbol", "DEMO", "--entry", "99.50")
        self.assertEqual((p["entry"], p["entry_rule"], p["risk_per_share"]), (99.5, "your price", 3.13))
        code, text, _ = self.plan("--symbol", "DEMO", "--entry", "99.50")
        self.assertIn("  Entry         99.50  limit buy: your price (the last close was 98.40 on Wed 2026-10-28)",
                      text.splitlines())
        self.config(swing={"entry_buffer_pct": 0.5})
        p = self.ok("--symbol", "DEMO")
        self.assertEqual((p["entry"], p["entry_rule"]), (98.89, "last close + 0.5%"))    # 98.40 x 1.005 = 98.892
        self.config(swing={"entry_buffer_pct": 0})
        self.assertEqual(self.ok("--symbol", "DEMO")["entry"], 98.4)
        # --from a triggered psx-entry signal: its price is the entry, its date the buy day
        self.config()
        shutil.copy(ENTRY, os.path.join("psx", "entry.json"))
        p = self.ok("--from", os.path.join("psx", "entry.json"), now="2026-10-29 10:16")
        self.assertEqual((p["date"], p["entry"], p["entry_rule"], p["setup"]), ("2026-10-29", 100.65, "entry signal",
                                                                                "breakout_20d"))
        code, text, _ = self.plan("--from", os.path.join("psx", "entry.json"), now="2026-10-29 10:16")
        self.assertIn("limit buy: the price of your entry signal (the last close was 98.40 on Wed 2026-10-28)", text)
        with open(ENTRY) as fh:
            waiting = dict(json.load(fh), status="waiting", price=None)
        self.write(os.path.join("psx", "entry.json"), waiting)
        self.assertIn("psx/entry.json says DEMO is waiting, not triggered: give --entry PRICE",
                      self.refused("--from", os.path.join("psx", "entry.json")))
        self.assertEqual(self.ok("--from", os.path.join("psx", "entry.json"), "--entry", "99.38")["entry"], 99.38)
        self.assertIn("say which stock: --symbol DEMO (see psx/watchlist.json)", self.refused())
        self.assertIn("not both", self.refused("--symbol", "DEMO", "--from", os.path.join("psx", "entry.json")))
        self.assertIn("longs only", self.refused("--symbol", "DEMO", "--side", "sell"))

    def test_odd_files_do_not_break_the_plan(self):
        self.watch(dict(DEMO_HIT, setups="breakout_20d"))                 # "setups" should be a list
        self.assertEqual(self.ok("--symbol", "DEMO")["setup"], None)
        self.write(os.path.join("psx", "watchlist.json"), [1, 2])
        self.assertEqual(self.ok("--symbol", "DEMO")["setup"], None)
        with open(os.path.join("psx", "watchlist.json"), "w") as fh:
            fh.write("{broken")
        self.assertIn("psx/watchlist.json is not valid JSON", self.refused("--symbol", "DEMO", "--stop", "level:1"))
        with open(ENTRY) as fh:
            self.write(os.path.join("psx", "entry.json"), dict(json.load(fh), setup=["orb"]))
        p = self.ok("--from", os.path.join("psx", "entry.json"), now="2026-10-29 10:16")
        self.assertEqual((p["setup"], p["entry"]), (None, 100.65))

    def test_bad_swing_settings_are_one_plain_line(self):
        cases = [({"style": "weekly"}, '"style" in psx/config.json is "day" or "swing", not \'weekly\''),
                 ({"flat_by": "late"}, "flat_by wants a Karachi time like 10:15")]
        for key, bad, low, high in (("max_hold_sessions", 0, 1, 250), ("max_hold_sessions", "20", 1, 250),
                                    ("max_hold_sessions", True, 1, 250), ("max_hold_sessions", 1.5, 1, 250),
                                    ("trend_exit_sessions", 1, 2, 60), ("trend_exit_sessions", 61, 2, 60)):
            cases.append(({"swing": {key: bad}}, "swing.%s in psx/config.json wants a whole number from %d to %d, "
                                                 "not %r" % (key, low, high, bad)))
        for bad in (-1, "1%", 10):
            cases.append(({"swing": {"entry_buffer_pct": bad}}, "swing.entry_buffer_pct in psx/config.json wants a "
                                                                "number from 0 to under 10"))
        cases.append(({"swing": {"target": "lots"}}, "swing.target in psx/config.json: --target is 2R, 1R"))
        cases.append(({"swing": {"stop": "wide"}}, "the stop 'wide' (swing.stop in psx/config.json) is not a swing "
                                                   "stop"))
        for change, why in cases:
            change = dict(change)
            self.config(swing=change.pop("swing", None), **change)
            self.assertIn(why, self.refused("--symbol", "DEMO"), change)
        # the numbers can change: 5 sessions to hold, a 5-session trend rule (20.0 reads as 20)
        self.config(swing={"max_hold_sessions": 5, "trend_exit_sessions": 5})
        p = self.ok("--symbol", "DEMO")
        self.assertEqual((p["max_hold_sessions"], p["exit_by"], p["trend_sessions"], p["trend_level"]),
                         (5, "2026-11-05", 5, 94.28))
        self.config(swing={"max_hold_sessions": 20.0})
        self.assertEqual(self.ok("--symbol", "DEMO")["exit_by"], "2026-11-27")

    def test_every_new_line_passes_the_word_test(self):
        out = []
        for args in (("--symbol", "DEMO", "--write"), ("--symbol", "DEMO", "--target", "price:110"),
                     ("--symbol", "DEMO", "--stop", "price:88.56", "--target", "price:108"),
                     ("--symbol", "DEMO", "--stop", "price:88.55", "--target", "price:108"),
                     ("--symbol", "DEMO", "--stop", "price:98.40", "--target", "price:104"),
                     ("--symbol", "DEMO", "--stop", "low:10"), ("--symbol", "DEMO", "--stop", "level:0.5"),
                     ("--symbol", "DEMO", "--entry", "99.50"), ("--symbol", "DEMO", "--stop", "or-mid"),
                     ("--symbol", "DEMO", "--flat-by", "15:00"), ("--symbol", "DEMO", "--time", "10:15 AM"), (),
                     ("--symbol", "DEMO", "--entry", "120")):
            code, text, err = self.plan(*args)
            out.append(text + err)
        out.append(run(exitp, ["show", "--now", "2026-10-28 17:05"])[1])
        out.append(self.refused("--symbol", "DEMO", data=closing_files("few", lambda day: day == THU - datetime.
                                                                       timedelta(days=1))))
        out.append("\n".join([exitp.OVERNIGHT, exitp.SWING_STOPS] + [why for _, _, why in exitp.DAY_FLAGS]))
        self.assertGreater(check_words(self, "\n".join(out)), 60)

    def test_a_day_folder_with_the_v2_keys_is_the_v1_example(self):
        # D23: "style": "day", or the example config with the swing block, gives the v1 plan word for word
        with open(EXAMPLE_CONFIG) as fh:
            shipped = json.load(fh)
        shutil.copy(ENTRY, os.path.join("psx", "entry.json"))
        for cfg in ({"style": "day"}, shipped, None):
            if cfg is None:
                os.remove(os.path.join("psx", "config.json"))
            else:
                self.write(os.path.join("psx", "config.json"), cfg)
            argv = ["plan", "--stop", "or-mid", "--target", "1R", "--now", "2026-10-29 10:16", "--data", MARKET,
                    "--offline"]
            code, text, err = run(exitp, argv)
            self.assertEqual(code, 0, err)
            self.assertEqual(text.splitlines()[:9], EXAMPLE_LINES)
            self.assertEqual(text.splitlines()[-1], "Next: /psx-timemachine replays it, then /psx-budget sizes it to "
                                                    "your max loss, costs included. Nothing was placed.")
            p = json.loads(run(exitp, argv + ["--json"])[1])
            for k, v in EXAMPLE_PLAN.items():
                self.assertEqual(p[k], v, k)
            self.assertNotIn("style", p)


class SwingDoc(unittest.TestCase):
    def read(self):
        with open(os.path.join(ROOT, "psx-exit", "SKILL.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_swing_section_comes_after_the_first_example(self):
        text = self.read()
        self.assertLess(text.index("## Example"), text.index("## Swing style"))
        self.assertLess(text.index("## Swing style"), text.index("## Rules"))
        low = " ".join(text.lower().split())
        for must in ("day trading loses most people money", "swing trading loses money for many people too.",
                     "tax on gains is not in these costs.", "paper first", "gap risk", "lower price limit",
                     "a gap goes past the stop", "more days down can lose more", "times the loss at the stop",
                     "atr:2", "low:10", "level:0.5", "92.06", "95.77", "10.2782", "2.95", "exit-by day", "trend rule",
                     "not a refusal", "do not chase it", "or-mid", "--flat-by", "psx/metrics.json",
                     "a stop at the limit can be hit that day", "cannot be hit that day",
                     "a fill at the ldcp or under is at or under the stop", "ex-date on the buy day",
                     "`low:n` and `level:k` stops are refused that day", "plan again on the buy day"):
            self.assertIn(must, low)
        swing = text[text.index("## Swing style"):text.index("## Rules")]
        self.assertGreater(check_words(self, swing), 40)

    def test_the_swing_words_are_plain_and_short(self):
        # the user's rule: plain words and short sentences. The swing part says what the ATR is (the
        # same words as the README), and no sentence of its prose (not the code, not the tables) has
        # more than 22 words
        text = self.read()
        swing = text[text.index("## Swing style"):text.index("## Rules")]
        low = " ".join(swing.lower().split())
        for must in ("atr(14) is how far the share moves in a normal day. it is the average of the last 14 "
                     "sessions, each from its low to its high. a jump from the last close counts too.",
                     "say what the atr is in plain words"):
            self.assertIn(must, low)
        paras, cur, code = [], [], False
        for line in swing.splitlines() + [""]:
            s = line.strip()
            if s.startswith("```"):
                code = not code
            item = re.match(r"([-*]|\d+\.)\s+", s)
            skip = code or not s or s.startswith(("```", "|", "#"))
            if skip or item:
                paras.append(" ".join(cur))
                cur = []
            if not skip:
                cur.append(s[item.end():] if item else s)
        long_ones = []
        for para in paras:
            for sent in re.split(r"(?<=[.!?:;])\s+(?=[A-Z(\"`])", re.sub(r"`[^`]*`", "X", para)):
                words = [w for w in sent.split() if re.search(r"[A-Za-z0-9]", w)]
                if len(words) > 22:
                    long_ones.append("%d words: %s" % (len(words), sent))
        self.assertEqual(long_ones, [])

    def test_the_swing_example_shows_the_tools_real_output(self):
        text = self.read()
        block = text[text.index("**Swing example"):]
        block = block[block.index("```\n") + 4:]
        block = block[:block.index("```")].strip("\n").splitlines()
        self.assertEqual(block[0], SWING_LINES[0])
        d, cwd = tempfile.mkdtemp(), os.getcwd()
        os.chdir(d)
        try:
            os.mkdir("psx")
            closing_files("data", lambda day: day < THU)
            with open(EXAMPLE_CONFIG) as fh:
                cfg = dict(json.load(fh), style="swing")
            with open(os.path.join("psx", "config.json"), "w") as fh:
                json.dump(cfg, fh)
            with open(os.path.join("psx", "watchlist.json"), "w") as fh:
                json.dump({"created_by": "psx-scanner", "mode": "daily", "hits": [DEMO_HIT]}, fh)
            code, out, err = run(exitp, ["plan", "--symbol", "DEMO", "--write", "--now", EVENING, "--data", "data",
                                         "--offline"])
        finally:
            os.chdir(cwd)
            shutil.rmtree(d)
        self.assertEqual(code, 0, err)
        self.assertEqual(block, out.strip("\n").splitlines())


if __name__ == "__main__":
    unittest.main()
