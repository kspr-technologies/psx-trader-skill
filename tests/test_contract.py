"""The shared contracts of the PSX pack (v2). Standard library only, offline.

    cd tests && python3 -m unittest test_contract -v

- The keys `alerts.py clock --json` prints (psx-autorun reads them), and the "(expected" mark.
- The made-up contract files in fixtures/psx-v2/contract/: every file reads as JSON and has its keys.
- The shared word test (tests/_words.py) and its rules.
- The made-up swing paths in fixtures/psx-v2/paths/ show what their README says.
- The swing defaults are the same everywhere.
- A live day fill (paper off, no "style"): the v1 fill, plus the D39 flag and its one line (SPEC 2.6).
"""

import ast
import datetime
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load
from _words import V1_LINES, check_words, problems

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import psx_core as core  # noqa: E402

V2 = os.path.join(FIX, "psx-v2")
CONTRACT = os.path.join(V2, "contract")
MARKET = os.path.join(FIX, "psx-market")
D = datetime.date
NAMES = ["broker-approved-old.json", "broker-expired.json", "broker-placed-unfilled.json",
         "broker-protect-cancelled.json", "broker-protect-open.json", "clock-keys.json", "orders-invest.json",
         "orders-protect.json", "orders-swing.json", "plan-swing.json", "portfolio-real.json", "portfolio-swing.json"]
PLAN_KEYS = ["created_by", "style", "symbol", "side", "date", "setup", "level", "last_close", "last_close_date",
             "entry", "entry_rule", "entry_time", "entry_bar", "interval", "opening_range", "stop", "stop_rule",
             "stop_label", "stop_detail", "atr", "atr_bars", "atr_from", "target", "target_r", "target_label",
             "risk_per_share", "reward_risk", "flat_by", "max_hold_sessions", "exit_by", "exit_by_time",
             "trend_sessions", "trend_level", "session", "limits", "costs_per_share_est", "gap_low",
             "gap_loss_per_share", "gap_r", "created_at", "note"]
ORDER_PLAN_KEYS = ["style", "entry", "stop", "target", "risk_per_share", "max_loss", "costs", "setup", "entry_time",
                   "flat_by", "exit_by", "exit_by_time", "trend_sessions", "gap_loss"]
LEDGER_PLAN_KEYS = ["style", "date", "stop", "stop_at_buy", "target", "exit_by", "exit_by_time", "trend_sessions",
                    "setup", "max_loss", "scaled", "from"]
RECORD_KEYS = ["at", "date", "index", "fingerprint", "symbol", "side", "plain", "status"]   # "placed" and after
ORDER_DOC_KEYS = ["created_by", "created_at", "status", "paper", "note", "orders"]
SWING_CFG = {"style": "swing"}


def contract(name):
    with open(os.path.join(CONTRACT, name), encoding="utf-8") as fh:
        return json.load(fh)


def top_level(path):
    """The names of a file's top-level functions and simple assignments (as tests/test_repo.py reads them)."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out.add(node.name)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out.add(node.targets[0].id)
    return out


class ClockKeys(unittest.TestCase):
    """alerts.py clock --json: the keys and the words auto-run reads (the contract clock-keys.json)."""

    @classmethod
    def setUpClass(cls):
        cls.alerts = load("psx-alerts", "alerts")
        cls.tmp = tempfile.mkdtemp(prefix="psx-contract-")
        cls.cfg = os.path.join(cls.tmp, "config.json")
        with open(cls.cfg, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        cls.doc = contract("clock-keys.json")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def clock(self, now):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.alerts.main(["clock", "--json", "--now", now, "--config", self.cfg])
        return code, json.loads(out.getvalue())

    def test_the_keys_in_order_on_every_day(self):
        days = [d["now"][:10] for d in self.doc["days"]]
        self.assertIn("2026-10-29", days)        # a normal day
        self.assertIn("2026-11-06", days)        # a Friday
        self.assertIn("2027-03-05", days)        # an expected moon holiday
        for day in self.doc["days"]:
            code, st = self.clock(day["now"])
            self.assertEqual(list(st), self.doc["keys"], day["now"])
            self.assertEqual(code, day["exit"], day["now"])
            for k, want in day.items():
                if k in ("now", "exit"):
                    continue
                if k.endswith("_has"):
                    self.assertIn(want, st[k[:-4]], day["now"])
                else:
                    self.assertEqual(st[k], want, "%s: %s" % (day["now"], k))

    def test_an_expected_holiday_has_the_mark(self):
        _, st = self.clock("2027-03-05 10:00")
        self.assertIn(self.doc["expected_mark"], st["reason"])
        self.assertFalse(st["open"])
        _, st = self.clock("2026-11-09 10:00")             # Iqbal Day: a known holiday, no mark
        self.assertNotIn(self.doc["expected_mark"], st["reason"])
        self.assertEqual(st["reason"], "PSX holiday: Iqbal Day")


class ContractFiles(unittest.TestCase):
    """Each made-up contract file reads as JSON and has the keys of the shared shapes."""

    def test_every_file_reads_as_json(self):
        self.assertEqual(sorted(f for f in os.listdir(CONTRACT) if f.endswith(".json")), NAMES)
        for name in NAMES:
            self.assertIsInstance(contract(name), dict, name)

    def test_the_swing_plan(self):
        p = contract("plan-swing.json")
        self.assertEqual(list(p), PLAN_KEYS)
        self.assertEqual((p["style"], p["entry"], p["stop"], p["target"], p["flat_by"]), ("swing", 99.38, 96.25, 105.64,
                                                                                         None))
        self.assertEqual(p["exit_by"], core.add_sessions(D(2026, 10, 29), p["max_hold_sessions"]).isoformat())
        self.assertEqual((p["limits"]["lower"], p["limits"]["upper"]), core.price_limits(p["last_close"]))
        self.assertEqual(p["gap_low"], core.price_limits(p["entry"])[0])
        costs = core.trade_costs(SWING_CFG)
        self.assertEqual(round(core.gap_loss(p["entry"], 1000, costs) / 1000, 4), p["gap_loss_per_share"])
        self.assertEqual(round(core.round_trip_cost(p["entry"], p["stop"], 1000, costs) / 1000, 4),
                         p["costs_per_share_est"])
        self.assertEqual(round(p["gap_loss_per_share"] / (p["risk_per_share"] + p["costs_per_share_est"]), 2),
                         p["gap_r"])

    def test_the_swing_order_and_ledger(self):
        doc = contract("orders-swing.json")
        self.assertEqual([k for k in doc if k in ORDER_DOC_KEYS], ORDER_DOC_KEYS)
        o = doc["orders"][0]
        self.assertEqual(list(o["plan"]), ORDER_PLAN_KEYS)
        self.assertNotIn("trigger_price", o)
        costs = core.trade_costs(SWING_CFG)
        self.assertEqual(core.max_loss_at_stop(o["limit_price"], o["stop_loss"], o["qty"], costs), o["plan"]["max_loss"])
        self.assertEqual(core.round_trip_cost(o["limit_price"], o["stop_loss"], o["qty"], costs), o["plan"]["costs"])
        self.assertEqual(core.gap_loss(o["limit_price"], o["qty"], costs), o["plan"]["gap_loss"])
        pf = contract("portfolio-swing.json")
        pos = pf["positions"][0]
        self.assertEqual(list(pos["plan"]), LEDGER_PLAN_KEYS)
        lot = pos["lots"][0]
        self.assertEqual(lot["cost"], round(o["qty"] * o["limit_price"], 2))
        self.assertEqual(lot["costs"], core.leg_cost(o["limit_price"], o["qty"], costs))
        self.assertEqual(pf["cash"], round(500000 - lot["cost"] - lot["costs"], 2))
        self.assertEqual(pos["plan"]["stop"], pos["plan"]["stop_at_buy"])
        self.assertNotIn("real_holdings", pf)

    def test_the_protect_order(self):
        o = contract("orders-protect.json")["orders"][0]
        self.assertEqual(list(o), ["symbol", "side", "qty", "type", "trigger_price", "limit_price", "time_in_force",
                                   "board", "why"])
        self.assertEqual((o["side"], o["type"], o["trigger_price"]), ("sell", "stop_limit", 96.25))
        gap = core.swing_settings({})["stop_limit_gap_pct"]
        self.assertEqual(o["limit_price"], core.round_down_tick(o["trigger_price"] * (1 - gap / 100)))
        self.assertIs(contract("orders-protect.json")["paper"], False)        # paper mode writes no protect order

    def test_the_real_holdings_and_the_invest_orders(self):
        pf = contract("portfolio-real.json")
        self.assertEqual(list(pf)[:2], ["real_holdings", "as_of"])
        self.assertIs(pf["real_holdings"], True)
        self.assertEqual((pf["as_of"], pf["cash"], pf["positions"][0]["ticker"]), ("2026-10-28", 100000, "DEMO"))
        doc = contract("orders-invest.json")
        self.assertEqual([k for k in doc if k in ORDER_DOC_KEYS], ORDER_DOC_KEYS)
        self.assertEqual([o["side"] for o in doc["orders"]], ["sell", "buy"])     # TRIM sells first
        for o in doc["orders"]:
            self.assertIsNone(o["stop_loss"])
            self.assertEqual(o["plan"]["style"], "invest")
            self.assertEqual(list(o["plan"]), ["style", "signal", "entry", "fair_share_pct", "weight_after_pct",
                                               "rules"])
        self.assertEqual([o["plan"]["signal"] for o in doc["orders"]], ["TRIM", "ADD"])
        self.assertEqual(doc["orders"][1]["symbol"], "DEMO")

    def test_the_broker_records(self):
        ticket, check = load("psx-broker", "ticket"), load("psx-risk", "check")
        buy = contract("orders-swing.json")["orders"][0]
        sell = dict(contract("orders-protect.json")["orders"][0], qty=143)
        fps = {"buy": ticket.fingerprint(buy), "sell": ticket.fingerprint(sell)}
        self.assertEqual(fps, {"buy": check.fingerprint(buy), "sell": check.fingerprint(sell)})
        self.assertNotEqual(fps["sell"], ticket.fingerprint(dict(sell, trigger_price=None)))   # the trigger counts
        want = {"broker-placed-unfilled.json": ["approved", "placed"],
                "broker-expired.json": ["approved", "placed", "expired"],
                "broker-approved-old.json": ["approved"],
                "broker-protect-open.json": ["approved", "placed"],
                "broker-protect-cancelled.json": ["approved", "placed", "cancelled"]}
        for name, statuses in want.items():
            recs = contract(name)["orders"]
            self.assertEqual([r["status"] for r in recs], statuses, name)
            for r in recs:
                for k in RECORD_KEYS:
                    self.assertIn(k, r, "%s: %s" % (name, k))
                self.assertEqual(r["fingerprint"], fps[r["side"]], name)
                if r["status"] in ("expired", "cancelled"):
                    self.assertEqual(r["of"], recs[0]["date"], name)
                if r["side"] == "buy" and r["status"] == "approved":
                    self.assertEqual(r["entry_ref"], buy["limit_price"], name)      # the cash check needs it
        self.assertEqual(contract("broker-expired.json")["orders"][-1]["date"], "2026-10-30")
        # psx-risk's log reader: "expired" and "cancelled" free an order the way "skipped" does
        orders, unfilled = check.placed_today(contract("broker-protect-open.json"), "2026-10-30")
        self.assertEqual([(o["symbol"], o["side"], o["status"]) for o in orders], [("DEMO", "sell", "placed")])
        self.assertEqual(check.placed_today(contract("broker-protect-cancelled.json"), "2026-10-30"), ([], []))
        self.assertEqual(check.placed_today(contract("broker-placed-unfilled.json"), "2026-10-29")[1], ["buy DEMO"])
        self.assertEqual(check.placed_today(contract("broker-expired.json"), "2026-10-30"), ([], []))


class Words(unittest.TestCase):
    """tests/_words.py: the shared word test."""
    GOOD = [
        "ADD. The add rules are met. You could put new money here. You decide.",
        "Could sell 550 LUCK at 400.64: about Rs 220,352.00, costs about Rs 394.65. You decide. Nothing is placed.",
        "Could buy 151 OGDC at 303.63: Rs 45,848.13 plus about Rs 82.11 costs. You decide. Nothing is placed.",
        "Break: both votes turned down at the end of October. The rule says: sell half (550 shares). You decide. "
        "Nothing is placed.",
        "ALERT DEMO traded at or under your stop 96.25 on Fri 2026-11-20: the low in that day's closing file was 95.90 "
        "(official close 96.40). Your plan's stop rule is met. You could sell: psx-broker flatten --symbol DEMO, then "
        "psx-risk and your yes. You decide.",
        "SELL DEMO, REG board, quantity 143, STOP LIMIT trigger price 96.25, limit price 95.28, valid for the day",
        "  BUY 3,428 DEMO, limit 100.65, good for today, REG board",
        "notify: LUCK: a sell rule is met (TRIM). Open the report. You decide.",
        "Signals are rules, not predictions. No tool knows where a price will go. Not financial advice.",
        V1_LINES[0],
    ]
    BAD = [("Buy 100 DEMO now.", "starts with"), ("Sell OGDC before the close.", "starts with"),
           ("- Buy 10 HBL", "starts with"), ("2. Sell the rest", "starts with"), ("Prices in real time.", "real time"),
           ("Real-time data", "real-time"), ("A guaranteed exit.", "guarantee"),
           ("You would have made Rs 5,000.", "you would have made"), ("one " + chr(0x2014) + " two", "em dash"),
           ("You could sell it now.", "action line"), ("The rule says: sell half.", "action line"),
           ("The rules could buy more for you", "action line")]

    def test_good_lines_pass(self):
        self.assertEqual(problems("\n".join(self.GOOD)), [])
        self.assertEqual(check_words(self, "\n".join(self.GOOD) + "\n\n"), len(self.GOOD))

    def test_each_bad_line_is_caught(self):
        for text, why in self.BAD:
            found = problems(text)
            self.assertEqual(len(found), 1, text)
            self.assertIn(why, found[0], text)

    def test_check_words_fails_the_test_and_names_the_line(self):
        with self.assertRaises(AssertionError) as e:
            check_words(self, "A fine line.\nBuy 100 DEMO now.\n")
        self.assertIn("Buy 100 DEMO now.", str(e.exception))
        self.assertEqual(check_words(self, "Buy 1 DEMO", skip=["Buy 1 DEMO"]), 1)     # an old line, passed in
        self.assertEqual((problems(""), problems(None)), ([], []))

    def test_a_plain_module(self):
        canon = top_level(os.path.join(HERE, "psx_core.py"))
        self.assertEqual(top_level(os.path.join(HERE, "_words.py")) & canon, set())

    def test_the_new_lines_of_the_shared_contracts_pass(self):
        # The new lines of the shared helpers in psx_core.py.
        # The first versions of psx-invest and psx-autorun are built now (Tasks 8 and 9).
        # So their "not built yet" line is gone. Their own tests run the word test on their lines.
        lines = []
        for when, typ in (("2026-10-29 09:20", "market"), ("2026-10-29 09:31", "limit"), ("2026-10-30 12:30", "limit"),
                          ("2026-10-31 10:00", "limit"), ("2026-11-09 10:00", "limit")):
            lines.append(core.swing_window(datetime.datetime.fromisoformat(when), typ))
        for bad in (lambda: core.trade_style({"style": "weekly"}), lambda: core.add_sessions(D(2026, 10, 29), -1)):
            with self.assertRaises(ValueError) as e:
                bad()
            lines.append(str(e.exception))
        self.assertEqual(check_words(self, "\n".join(lines)), len(lines))


class Fixtures(unittest.TestCase):
    """fixtures/psx-v2: made up, made the same way every time, and each path shows its story."""
    DAYS = ["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-10",
            "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19",
            "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"]

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("psx_v2_make", os.path.join(V2, "make.py"))
        cls.make = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.make)
        cls.market = core.recent_sessions(D(2026, 10, 29), 70, MARKET, None, True)

    def files(self, root):
        out = {}
        for base, _, names in os.walk(root):
            for n in names:
                with open(os.path.join(base, n), "rb") as fh:
                    out[os.path.relpath(os.path.join(base, n), root)] = fh.read()
        return out

    def bars(self, name):
        """DEMO's daily bars: the sample market to Thu 10-29, then the path's 20 files."""
        folder = os.path.join(V2, "paths", name, "mkt_summary")
        extra = []
        for f in sorted(os.listdir(folder)):
            with open(os.path.join(folder, f), "rb") as fh:
                rows = core.read_mkt_summary(fh.read())
            self.assertEqual(rows[0]["date"].isoformat() + ".Z", f)
            extra.append((rows[0]["date"], rows))
        return core.daily_bars("DEMO", self.market + extra)

    def walk(self, bars, stop=96.25, target=105.64, n=10):
        """The first day from Fri 10-30 on that the low meets the stop, the high the target, and the
        close breaks the trend rule (payout-adjusted closes)."""
        hit = {"stop": None, "target": None, "trend": None}
        for i, b in enumerate(bars):
            if b["date"] < D(2026, 10, 30):
                continue
            if hit["stop"] is None and b["low"] <= stop:
                hit["stop"] = b["date"].isoformat()
            if hit["target"] is None and b["high"] >= target:
                hit["target"] = b["date"].isoformat()
            level, broke = core.trend_break(core.adjusted_closes(bars[:i + 1]), n)
            if broke and hit["trend"] is None:
                hit["trend"] = (b["date"].isoformat(), round(level, 2), b["close"])
        return hit

    def test_the_readme_says_made_up(self):
        with open(os.path.join(V2, "README.md"), encoding="utf-8") as fh:
            note = fh.read().lower()
        self.assertIn("made up", note)

    def test_the_generator_makes_the_same_files_twice(self):
        tmp = tempfile.mkdtemp(prefix="psx-v2-make-")
        try:
            for run in ("one", "two"):
                out = os.path.join(tmp, run)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(self.make.main([out]), 0)
                for part in ("paths", "contract"):
                    self.assertEqual(self.files(os.path.join(out, part)), self.files(os.path.join(V2, part)), part)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_the_generator_defines_no_canonical_name(self):
        canon = top_level(os.path.join(HERE, "psx_core.py"))
        self.assertEqual(top_level(os.path.join(V2, "make.py")) & canon, set())

    def test_every_path_has_the_20_sessions_inside_the_limits(self):
        self.assertEqual(self.DAYS, [d.isoformat() for d in core.window_sessions(
            [D(2026, 10, 30), D(2026, 11, 27)])[0]])                                  # Iqbal Day is not a session
        for name in ("up", "gap", "trend", "flat", "bonus"):
            bars = self.bars(name)
            path = [b for b in bars if b["date"] >= D(2026, 10, 30)]
            self.assertEqual([b["date"].isoformat() for b in path], self.DAYS, name)
            for before, b in zip(bars[-21:-1], path):
                if not (name == "bonus" and b["date"] == D(2026, 11, 12)):
                    self.assertEqual(b["ldcp"], before["close"], "%s %s" % (name, b["date"]))
                lo, hi = core.price_limits(b["ldcp"])
                self.assertTrue(lo <= b["low"] <= min(b["open"], b["close"]), "%s %s" % (name, b["date"]))
                self.assertTrue(max(b["open"], b["close"]) <= b["high"] <= hi, "%s %s" % (name, b["date"]))

    def test_the_stories(self):
        self.assertEqual(self.walk(self.bars("up")), {"stop": None, "target": "2026-11-04", "trend": None})
        up = dict((b["date"].isoformat(), b) for b in self.bars("up"))
        self.assertEqual(up["2026-11-04"]["high"], 105.9)
        self.assertEqual(self.walk(self.bars("gap")), {"stop": "2026-11-02", "target": None, "trend": None})
        gap = dict((b["date"].isoformat(), b) for b in self.bars("gap"))
        self.assertEqual(gap["2026-10-30"]["close"], 97.1)
        self.assertEqual((gap["2026-11-02"]["open"], gap["2026-11-02"]["low"], gap["2026-11-02"]["close"]),
                         (95.4, 94.8, 95.6))
        trend = self.bars("trend")
        self.assertEqual(self.walk(trend), {"stop": None, "target": None, "trend": ("2026-11-25", 101.2, 100.5)})
        self.assertEqual(self.walk(trend, target=112.0)["target"], None)
        self.assertEqual(min(b["close"] for b in trend if D(2026, 11, 11) <= b["date"] <= D(2026, 11, 24)), 101.2)
        self.assertEqual(self.walk(self.bars("flat")), {"stop": None, "target": None, "trend": None})

    def test_the_bonus_path(self):
        up = dict((b["date"], b) for b in self.bars("up"))
        bonus = self.bars("bonus")
        for b in bonus:
            if b["date"] < D(2026, 11, 12):
                self.assertEqual(b, up[b["date"]])
        xb = [b["date"].isoformat() for b in bonus if b["flags"]]
        self.assertEqual(xb, ["2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17", "2026-11-18"])
        day = next(b for b in bonus if b["date"] == D(2026, 11, 12))
        self.assertEqual((day["ldcp"], day["flags"]), (89.7, ["XB"]))
        self.assertEqual(round(day["ldcp"] / up[D(2026, 11, 11)]["close"], 4), 0.8333)
        hit = self.walk(bonus)
        self.assertEqual(hit["stop"], "2026-11-12")      # the old stop meets the bonus alone: the alert must ask
        self.assertIsNone(hit["trend"])                  # payout-adjusted closes: a bonus is never a trend break


class SwingDefaults(unittest.TestCase):
    """The "style" and "swing" defaults are the same in psx_core, the configs and the tools."""

    def test_the_swing_block_is_the_same_everywhere(self):
        check, size = load("psx-risk", "check"), load("psx-budget", "size")
        for path in (os.path.join(ROOT, "psx-budget", "config.example.json"), os.path.join(FIX, "psx-risk", "config.json"),
                     os.path.join(FIX, "psx-broker", "config.json")):
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
            self.assertEqual((cfg["style"], cfg["swing"]), ("day", core.SWING), path)
            self.assertEqual(list(cfg["swing"]), list(core.SWING), path)
        for mod in (check, size):
            self.assertEqual((mod.DEFAULTS["style"], mod.DEFAULTS["swing"]), ("day", core.SWING))
            self.assertEqual(list(mod.DEFAULTS["swing"]), list(core.SWING))
            self.assertEqual((mod.trade_style(mod.DEFAULTS), mod.swing_settings(mod.DEFAULTS)), ("day", core.SWING))
        self.assertIsNot(size.DEFAULTS["swing"], size.SWING)
        self.assertEqual(check.ORDER_FIELDS[-1], "trigger_price")


class LiveDayFill(unittest.TestCase):
    """SPEC 2.6 and D39 (F114). A live day folder ("paper": false, no "style"), like a real trading folder,
    with a ledger written by hand in v1. A v2 fill there prints and writes what v1 did, plus two things:
    "real_holdings": true at the top of psx/portfolio.json (it stays there), and one line that says so
    (only on the fill that sets it). Nothing else changes. The V1 values below are what the v1 pack printed
    and wrote for the same steps. The day golden diff (SPEC 7.2) checks the same in its "live" config."""
    HAND_NOTE = "Made up. Written by hand, as in v1."
    LEDGER = {"cash": 50000.0, "positions": [{"ticker": "DEMO", "shares": 100, "lots": [
        {"id": "DEMO-1", "date": "2026-10-27", "shares": 60, "cost": 6030.0, "costs": 10.85},
        {"id": "DEMO-2", "date": "2026-10-28", "shares": 40, "cost": 4026.0, "costs": 7.25}]}], "note": HAND_NOTE}
    # (the fill, the line v1 printed, psx/portfolio.json as v1 wrote it)
    V1_STEPS = [
        (["--qty", "40", "--price", "101.75", "--now", "2026-10-29 11:00"],
         "Recorded sell 40 DEMO at 101.75 on 2026-10-29 in psx/portfolio.json, costs about Rs 7.29. "
         "Cash now Rs 54,062.71.",
         {"cash": 54062.71, "positions": [{"ticker": "DEMO", "shares": 60, "lots": [
             {"id": "DEMO-1", "date": "2026-10-27", "shares": 20, "cost": 2010.0, "costs": 3.62},
             {"id": "DEMO-2", "date": "2026-10-28", "shares": 40, "cost": 4026.0, "costs": 7.25}]}],
          "note": HAND_NOTE}),
        (["--qty", "60", "--price", "102.00", "--now", "2026-10-29 11:05"],
         "Recorded sell 60 DEMO at 102.00 on 2026-10-29 in psx/portfolio.json, costs about Rs 10.96. "
         "Cash now Rs 60,171.75.",
         {"cash": 60171.75, "positions": [], "note": HAND_NOTE}),
    ]
    # psx/state/broker.json after both fills, as v1 wrote it
    V1_RECORDS = [
        {"at": "2026-10-29T11:00:00+05:00", "date": "2026-10-29", "status": "filled", "symbol": "DEMO", "side": "sell",
         "qty": 40, "price": 101.75, "costs": 7.29, "plain": "SELL 40 DEMO at 101.75"},
        {"at": "2026-10-29T11:05:00+05:00", "date": "2026-10-29", "status": "filled", "symbol": "DEMO", "side": "sell",
         "qty": 60, "price": 102.0, "costs": 10.96, "plain": "SELL 60 DEMO at 102.00"}]
    NOTE = ('psx/portfolio.json now says "real_holdings": true: it holds real shares, so a paper fill can never '
            'change it.')

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psx-live-day-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.makedirs(os.path.join(self.tmp, "psx"))
        with open(os.path.join(ROOT, "psx-budget", "config.example.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        for k in ("style", "swing"):          # a v1 config has no "style" key: day style
            del cfg[k]
        cfg["paper"] = False                  # live: real money
        self.assertIs(cfg["psx_site"], False)
        self.put("config.json", cfg)
        self.put("portfolio.json", self.LEDGER)

    def put(self, name, doc):
        with open(os.path.join(self.tmp, "psx", name), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=2) + "\n")

    def raw(self, rel):
        with open(os.path.join(self.tmp, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_a_live_fill_is_the_v1_fill_plus_the_flag(self):
        tool = os.path.join(ROOT, "psx-broker", "ticket.py")
        for i, (args, line, ledger) in enumerate(self.V1_STEPS):
            p = subprocess.run([sys.executable, "-B", tool, "fill", "--symbol", "DEMO", "--side", "sell"] + args,
                               cwd=self.tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                               timeout=120)
            note = [self.NOTE] if i == 0 else []          # the line comes once: on the fill that sets the flag
            self.assertEqual((p.returncode, p.stderr, p.stdout.splitlines()), (0, "", [line] + note), args)
            v1 = json.dumps(ledger, indent=2) + "\n"      # the v1 file, byte for byte
            self.assertEqual(self.raw(os.path.join("psx", "portfolio.json")),
                             v1.replace("{\n", '{\n  "real_holdings": true,\n', 1), args)
            check_words(self, p.stdout)
        self.assertEqual(json.loads(self.raw(os.path.join("psx", "state", "broker.json"))), {"orders": self.V1_RECORDS})


if __name__ == "__main__":
    unittest.main()
