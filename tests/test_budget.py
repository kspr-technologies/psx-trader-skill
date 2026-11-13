import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal, ROUND_HALF_UP

from _load import load, ROOT, FIX
from _words import check_words

size = load("psx-budget", "size")

SKILL = os.path.join(ROOT, "psx-budget")
CFG = os.path.join(SKILL, "config.example.json")
TARGETS = os.path.join(SKILL, "targets.example.json")
PLAN = os.path.join(FIX, "psx-budget", "plan-demo.json")
PRICES_1052 = os.path.join(FIX, "psx-budget", "prices-demo-1052.json")
ORDERS = os.path.join(FIX, "psx-budget", "orders-demo.json")
MARKET = os.path.join(FIX, "psx-market")               # the made-up sample market (read only)
NOPE = os.path.join(FIX, "psx-budget", "no-such-file")  # never there
NOW = "2026-10-29 10:15"
TODAY = "2026-10-29"
# the SPEC section 6 config, key for key and in this order
CONTRACT_CFG = {
    "budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_pkr": 400000,
    "min_order_pkr": 5000, "max_loss_per_trade_pkr": 5000, "max_daily_loss_pkr": 15000,
    "max_trades_per_day": 3, "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:45", "no_entries_after": "15:00", "flat_by": "15:20", "style": "day",
    "swing": {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20, "trend_exit_sessions": 10,
              "entry_buffer_pct": 1.0, "max_open_positions": 3, "max_open_risk_pkr": None,
              "max_gap_loss_pkr": None, "stop_limit_gap_pct": 1.0},
    "costs": {"commission_pct": 0.15, "commission_min_per_share": 0.03, "sales_tax_pct": 15,
              "other_charges_pct": 0.0066, "day_trade_one_side": False},
    "broker": "manual", "paper": True, "blocked": [], "allow_options": False, "allow_margin": False,
    "allow_short": False, "psx_site": False, "data_delay_minutes": 5, "holidays": {}, "open_days": [],
}
ORDER_FIELDS = ("symbol", "side", "qty", "type", "limit_price", "time_in_force", "board", "stop_loss",
                "take_profit")
# weights mode: Rs 500,000, 10% cash, 25% cap; the sample screener's prices
W = {"budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 25, "min_order_pkr": 5000, "blocked": []}
SCREENER = {"OGDC": 279.54, "HBL": 273.36, "LUCK": 467.0, "FFC": 514.17, "SYS": 122.32}
SPLIT = {"OGDC": 446, "HBL": 424, "LUCK": 199, "FFC": 135, "SYS": 378}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def w(**over):
    c = dict(W)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def rows(path):
    with open(path) as fh:
        return size.parse_weights(json.load(fh))


def shares(result):
    return dict((p["ticker"], p["shares"]) for p in result["positions"])


def run(argv):
    """Run size.py in an empty folder, so a default path (psx/prices.json, psx/data, ...)
    never finds a stray file and nothing is written into the repo."""
    out, err = io.StringIO(), io.StringIO()
    here = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = size.main(argv)
        finally:
            os.chdir(here)
    return code, out.getvalue(), err.getvalue()


def base(*more):
    return ["--plan", PLAN, "--config", CFG, "--portfolio", NOPE, "--data", NOPE] + list(more)


def write_json(path, doc):
    with open(path, "w") as fh:
        json.dump(doc, fh)
    return path


# swing style (SPEC v2 2.4 psx-budget): the made-up contract files of tests/fixtures/psx-v2 (read only)
V2 = os.path.join(FIX, "psx-v2", "contract")
SWING_PLAN = os.path.join(V2, "plan-swing.json")   # DEMO, buy day Thu 2026-10-29: 99.38, stop 96.25, target 105.64
SWING_NOW = "2026-10-29 09:20"                      # the pre-open of the buy day
EVENING = "2026-10-28 17:00"                        # the evening before: a preview


def contract(name):
    with open(os.path.join(V2, name)) as fh:
        return json.load(fh)


def swing_cfg(**over):
    return cfg(**dict({"style": "swing"}, **over))


def swing_plan(**over):
    p = contract("plan-swing.json")
    p.update(over)
    return p


def moved(doc, day):
    """A broker.json contract file with every record moved to `day`: its "date", the date in "at", and "of"."""
    out = json.loads(json.dumps(doc))
    for r in out["orders"]:
        for k in ("date", "of"):
            if k in r:
                r[k] = day
        if "at" in r:
            r["at"] = day + r["at"][10:]
    return out


class TheExample(unittest.TestCase):
    """Rs 500,000, 10% buffer, Rs 5,000 max loss: DEMO, risk 1.10 -> 3,428 shares, max loss Rs 4,999.94."""

    def test_config_example_is_the_contract(self):
        with open(CFG) as fh:
            c = json.load(fh)
        self.assertEqual(c, CONTRACT_CFG)
        self.assertEqual(list(c), list(CONTRACT_CFG))
        self.assertEqual(list(c["costs"]), list(CONTRACT_CFG["costs"]))

    def test_defaults_are_the_example_without_a_budget(self):
        # no money is sized that the user has not named: the budget has no default
        expect = dict(CONTRACT_CFG, budget=None)
        self.assertEqual(size.DEFAULTS, expect)
        self.assertEqual(list(size.DEFAULTS), list(expect))
        self.assertEqual(size.DEFAULTS["costs"], size.COSTS)

    def test_demo_is_3428_shares_bound_by_the_max_loss(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["risk_per_share"]), (3428, "max loss", 1.1))
        self.assertEqual((s["value"], s["price_risk"], s["max_loss"]), (345028.2, 3770.8, 4999.94))
        self.assertEqual((s["buy_cost"], s["sell_cost"], s["costs"]), (617.95, 611.19, 1229.14))
        self.assertEqual((s["reward_risk"], s["cash_buffer"], s["cash_available"]), (1.0, 50000, 450000))
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 3428), ("position cap", 3725), ("max order", 3974), ("cash", 4462)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["max loss"])
        self.assertEqual(s["limits"][0]["no_costs"], 4545)        # 5,000 / 1.10 without costs
        self.assertEqual(s["one_more"], {"qty": 3429, "max_loss": 5001.4})
        self.assertEqual((s["order_type"], s["entry_from"], s["flat_by"]), ("limit", "plan", "15:20"))
        self.assertIsNone(s["refusal"])
        self.assertEqual(s["warnings"], [])

    def test_the_max_loss_search_finds_the_edge(self):
        c = size.COSTS
        for q, loss in ((3427, 4998.48), (3428, 4999.94), (3429, 5001.40)):
            self.assertEqual(size.max_loss_at_stop(100.65, 99.55, q, c), loss)
        self.assertEqual(size.loss_shares(100.65, 99.55, 5000, c), 3428)
        self.assertEqual(size.loss_shares(100.65, 99.55, 4999.94, c), 3428)
        self.assertEqual(size.loss_shares(100.65, 99.55, 4999.93, c), 3427)
        self.assertEqual(size.loss_shares(101.30, 99.55, 5000, c), 2369)
        self.assertEqual(size.loss_shares(100.65, 99.55, 1, c), 0)
        free = dict(c, commission_pct=0, commission_min_per_share=0, other_charges_pct=0)
        self.assertEqual(size.loss_shares(100.65, 99.55, 5000, free), 4545)
        for cap in (50, 333, 1000):    # a cheap share, where the Rs 0.03 minimum a share bites
            brute = max(q for q in range(0, 5000) if size.max_loss_at_stop(8.25, 8.0, q, c) <= cap)
            self.assertEqual(size.loss_shares(8.25, 8.0, cap, c), brute)

    def test_the_order(self):
        o = size.make_order(size.size_trade(plan(), cfg(), today=TODAY))
        self.assertEqual(list(o), ["symbol", "side", "qty", "type", "limit_price", "time_in_force", "board",
                                   "stop_loss", "take_profit", "why", "plan"])
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "DEMO", "side": "buy", "qty": 3428, "type": "limit", "limit_price": 100.65,
            "time_in_force": "day", "board": "REG", "stop_loss": 99.55, "take_profit": 101.75,
            "plan": {"entry": 100.65, "stop": 99.55, "target": 101.75, "risk_per_share": 1.1,
                     "max_loss": 4999.94, "costs": 1229.14, "setup": "orb", "entry_time": "10:15 AM",
                     "flat_by": "15:20"}})
        for part in ("bound by your Rs 5,000 max loss per trade", "Rs 3,770.80", "Rs 1,229.14", "Rs 4,999.94",
                     "costs included"):
            self.assertIn(part, why)

    def test_the_fingerprint_is_the_example_one(self):
        o = size.make_order(size.size_trade(plan(), cfg(), today=TODAY))
        core = dict((k, o[k]) for k in ORDER_FIELDS if k in o and o[k] is not None)
        blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
        self.assertEqual(hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16], "e9d01b16c66c4a20")

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "psx", "orders.json")
            self.assertEqual(run(base("--now", NOW, "--write", out))[0], 0)
            with open(out) as fh, open(ORDERS) as fx:
                self.assertEqual(json.load(fh), json.load(fx))

    def test_cli_report(self):
        code, text, _ = run(base("--now", NOW))
        self.assertEqual(code, 0)
        for line in ("Size: 3,428 shares of DEMO = Rs 345,028.20 at 100.65.",
                     "Max loss at the stop: Rs 4,999.94, costs included.",
                     "price risk  3,428 x 1.10 = Rs 3,770.80",
                     "costs       buy Rs 617.95 + sell at the stop Rs 611.19 = Rs 1,229.14",
                     "one more share (3,429) would lose Rs 5,001.40: over your Rs 5,000",
                     "without them the max loss limit would be 4,545 shares",
                     "Today's price limits: 88.56 to 108.24 (from the company page).",
                     "buy 3,428 DEMO, limit 100.65, day, REG board",
                     "A stop is a plan, not a promise.", "Flat by 15:20 Karachi time",
                     "<- binds", "not financial advice"):
            self.assertIn(line, text)

    def test_cli_writes_one_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "psx", "orders.json")
            code, text, _ = run(base("--now", "2026-10-29T05:15:00Z", "--write", out))
            self.assertEqual(code, 0)
            self.assertIn("Wrote 1 proposed order to %s" % out, text)
            with open(out) as fh:
                doc = json.load(fh)
        self.assertEqual((doc["created_by"], doc["status"], doc["paper"]), ("psx-budget", "proposed", True))
        self.assertEqual(doc["created_at"], "2026-10-29T05:15:00Z")      # 10:15 Karachi time
        self.assertEqual(len(doc["orders"]), 1)
        self.assertEqual((doc["orders"][0]["plan"]["max_loss"], doc["orders"][0]["plan"]["costs"]),
                         (4999.94, 1229.14))

    def test_only_json_false_is_live(self):
        # the rule of psx-broker and psx-risk: "paper": null or 0 is still paper
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "orders.json")
            for value, paper in ((None, True), (0, True), ("no", True), (True, True), (False, False)):
                size.write_orders(out, [], {"paper": value})
                with open(out) as fh:
                    self.assertIs(json.load(fh)["paper"], paper, value)

    def test_cli_json(self):
        code, text, _ = run(base("--now", NOW, "--limit", "--json"))
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 3428)
        self.assertEqual((out["orders"][0]["qty"], out["orders"][0]["limit_price"]), (3428, 100.65))
        self.assertEqual(out["notes"], [])


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(), cfg(max_loss_per_trade_pkr=1), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("one share loses Rs 1.46 at the stop, costs included", s["refusal"])
        self.assertIn("more than your Rs 1 max loss per trade", s["refusal"])
        self.assertIsNone(size.make_order(s))
        with tempfile.TemporaryDirectory() as d:
            c = write_json(os.path.join(d, "config.json"), cfg(max_loss_per_trade_pkr=1))
            out = os.path.join(d, "orders.json")
            code, text, _ = run(base("--config", c, "--now", NOW, "--write", out))
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_each_limit_can_bind(self):
        s = size.size_trade(plan(), cfg(max_order_pkr=200000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (1987, "max order"))
        s = size.size_trade(plan(), cfg(max_position_pct=20), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (993, "position cap"))
        # a Rs 500,000 account: Rs 200,000 cash and Rs 300,000 of OGDC, so only the cash is short
        s = size.size_trade(plan(), cfg(), cash=200000, holdings={"OGDC": 300000}, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"], s["cash_from"]),
                         (1487, "cash", 150000, "portfolio"))
        s = size.size_trade(plan(), cfg(max_loss_per_trade_pkr=10000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (3725, "position cap"))

    def test_the_cash_limit_pays_the_buy_costs(self):
        big = cfg(max_position_pct=100, max_order_pkr=10 ** 7, max_loss_per_trade_pkr=10 ** 6)
        s = size.size_trade(plan(), big, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (4462, "cash"))
        c = size.COSTS
        self.assertLessEqual(size.spend(100.65, 4462, c), 450000)
        self.assertGreater(size.spend(100.65, 4463, c), 450000)
        # what psx-risk checks: the cash left after the value and the buy costs keeps the buffer
        self.assertGreaterEqual(500000 - s["value"] - s["buy_cost"], 50000)
        self.assertEqual(size.shares_for(450000, 100.65), 4470)   # what the value alone would give

    def test_the_1052_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=0), market=True, price=101.30, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (101.3, "price", 1.75))
        self.assertEqual((s["qty"], s["max_loss"], s["reward_risk"]), (2369, 4997.93, 0.26))
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]][1:3],
                         [("position cap", 3701), ("max order", 3948)])
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), market=True, price=101.30, today=TODAY)
        self.assertIn("reward:risk 0.26 (target 101.75, entry 101.30, stop 99.55) is under your minimum 1, "
                      "so psx-risk would block it", s["refusal"])
        self.assertIsNone(size.make_order(s))
        lower = size.size_trade(plan(), cfg(), market=True, price=100.00, today=TODAY)
        self.assertEqual((lower["entry"], lower["entry_from"], lower["qty"]), (100.65, "plan", 3428))
        limit = size.size_trade(plan(), cfg(), price=101.30, today=TODAY)      # a limit order ignores it
        self.assertEqual((limit["entry"], limit["qty"]), (100.65, 3428))

    def test_a_limit_at_101_30_is_refused_the_same_way(self):
        s = size.size_trade(plan(), cfg(), limit=101.30, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["qty"]), (101.3, "limit", 2369))
        self.assertIn("reward:risk 0.26", s["refusal"])
        code, text, _ = run(base("--now", "2026-10-29 10:52", "--limit", "101.30"))
        self.assertEqual(code, 1)
        self.assertIn("No order: reward:risk 0.26 (target 101.75, entry 101.30, stop 99.55)", text)

    def test_a_limit_price(self):
        s = size.size_trade(plan(), cfg(), limit=100.60, today=TODAY)
        q = s["qty"]
        self.assertEqual((s["risk_per_share"], s["bound_by"]), (1.05, "max loss"))
        self.assertLessEqual(size.max_loss_at_stop(100.60, 99.55, q), 5000)        # the most that fits
        self.assertGreater(size.max_loss_at_stop(100.60, 99.55, q + 1), 5000)
        self.assertEqual(s["max_loss"], size.max_loss_at_stop(100.60, 99.55, q))
        o = size.make_order(s)
        self.assertEqual((o["type"], o["limit_price"], o["qty"]), ("limit", 100.6, q))

    def test_a_market_order_has_no_limit_price(self):
        o = size.make_order(size.size_trade(plan(), cfg(), market=True, today=TODAY))
        self.assertEqual((o["type"], o["qty"]), ("market", 3428))
        self.assertNotIn("limit_price", o)
        self.assertEqual(list(o), ["symbol", "side", "qty", "type", "time_in_force", "board", "stop_loss",
                                   "take_profit", "why", "plan"])

    def test_prices_file_is_used_only_when_fresh(self):
        def go(now):
            code, text, _ = run(base("--market", "--prices", PRICES_1052, "--now", now, "--json"))
            return code, json.loads(text)
        for now in ("2026-10-29 10:52", "2026-10-29 10:57:07"):
            code, out = go(now)
            self.assertEqual((code, out["sizing"]["qty"], out["orders"]), (1, 2369, []), now)
            self.assertIn("above the plan's entry", out["notes"][0])
            self.assertIn("as of 10:52 Karachi time", out["notes"][0])
        # 38 minutes old, or not there yet at 10:40: a market order is refused, as psx-risk would block it
        for now, why in (("2026-10-29 11:30", "not used (older than 15 minutes)"), ("2026-10-29 10:40", "No price for DEMO")):
            code, text, err = run(base("--market", "--prices", PRICES_1052, "--now", now, "--json"))
            self.assertEqual((code, text), (1, ""), now)
            self.assertIn("No order: a market order needs a fresh price to measure its risk", err)
            self.assertIn(why, err, now)

    def test_a_limit_order_ignores_a_fresh_price(self):
        code, text, _ = run(base("--prices", PRICES_1052, "--now", "2026-10-29 10:52", "--json"))
        out = json.loads(text)
        self.assertEqual((code, out["sizing"]["qty"], out["sizing"]["entry"], out["notes"]), (0, 3428, 100.65, []))

    def test_the_tape_gives_the_fresh_price(self):
        code, text, _ = run(base("--data", MARKET, "--market", "--now", "2026-10-29 10:52", "--json"))
        out = json.loads(text)
        self.assertEqual((code, out["sizing"]["qty"], out["sizing"]["entry"]), (1, 2369, 101.3))
        self.assertIn("the tape", out["notes"][0])
        self.assertIn("reward:risk 0.26", out["sizing"]["refusal"])
        code, text, _ = run(base("--data", MARKET, "--market", "--now", NOW, "--json"))   # 100.65 at 10:15
        out = json.loads(text)
        self.assertEqual((code, out["sizing"]["qty"], out["orders"][0]["type"]), (0, 3428, "market"))
        self.assertIn("not above the plan's entry", out["notes"][0])

    def test_a_saved_company_page_gives_a_fresh_price(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "company"))
            shutil.copy(os.path.join(MARKET, "company", "DEMO.html"), os.path.join(d, "company"))
            code, text, _ = run(base("--data", d, "--market", "--now", "2026-10-29 15:55", "--json"))
            out = json.loads(text)
            self.assertEqual((code, out["sizing"]["entry"], out["sizing"]["reward_risk"]), (1, 101.6, 0.07))
            self.assertIn("the saved page", out["notes"][0])
            self.assertIn("as of 15:49 Karachi time", out["notes"][0])
            code, text, err = run(base("--data", d, "--market", "--now", "2026-10-29 16:10", "--json"))
            self.assertEqual(code, 1)                                       # the page is 21 minutes old by 16:10
            self.assertIn("not used", err)
            self.assertIn("No order: a market order needs a fresh price to measure its risk", err)

    def test_no_fresh_price_means_no_market_order(self):
        # psx-risk blocks a market buy with no fresh price (the plan's entry can be hours old); so does budget
        code, text, err = run(base("--market", "--now", NOW, "--json"))
        self.assertEqual((code, text), (1, ""))
        self.assertIn("No order: a market order needs a fresh price to measure its risk, and the plan's entry 100.65 can be hours old", err)
        self.assertIn("No price for DEMO", err)
        self.assertIn("drop --market for a limit order", err)
        code, text, _ = run(base("--data", MARKET, "--market", "--now", NOW, "--json"))    # the tape: 100.65 fresh
        out = json.loads(text)
        self.assertEqual((code, out["sizing"]["qty"], out["orders"][0]["type"]), (0, 3428, "market"))
        self.assertNotIn("limit_price", out["orders"][0])
        code, text, _ = run(base("--data", MARKET, "--market", "--now", NOW))
        self.assertIn("A market order fills at the price of the moment", text)
        self.assertIn("PSX takes no market orders in the pre-open", text)

    def test_the_first_fresh_source_in_the_lookup_order_wins(self):
        # the rule psx-risk uses too: psx/prices.json, then today's tape, then the saved company page
        with tempfile.TemporaryDirectory() as d:
            doc = {"prices": {"DEMO": 101.0}, "detail": {"DEMO": {"time": "2026-10-29T05:45:00Z"}}}
            pf = write_json(os.path.join(d, "prices.json"), doc)               # 10:45: 7 minutes old
            argv = base("--prices", pf, "--data", MARKET, "--market", "--now", "2026-10-29 10:52", "--json")
            out = json.loads(run(argv)[1])
            self.assertEqual(out["sizing"]["entry"], 101.0)                    # not the tape's newer 101.30
            self.assertIn(pf, out["notes"][0])
            doc["detail"]["DEMO"]["time"] = "2026-10-29T05:30:00Z"             # 10:30: too old, so the tape
            write_json(pf, doc)
            out = json.loads(run(argv)[1])
            self.assertEqual(out["sizing"]["entry"], 101.3)
            self.assertIn("the tape", out["notes"][0])

    def test_a_fetch_time_is_not_the_data_time(self):
        # no "time" in the detail: the fetch time minus the 5-minute data delay, as psx-risk dates it
        doc = {"prices": {"DEMO": 101.3}, "detail": {"DEMO": {"fetched": "2026-10-29T05:52:00Z"}}}
        self.assertEqual(size.file_quote(doc, "DEMO", "prices.json", 5)["time"], size.parse_now("2026-10-29 10:47"))
        with tempfile.TemporaryDirectory() as d:
            pf = write_json(os.path.join(d, "prices.json"), doc)
            out = json.loads(run(base("--prices", pf, "--market", "--now", "2026-10-29 11:02", "--json"))[1])
            self.assertEqual(out["sizing"]["entry"], 101.3)                 # 15 minutes after 10:47: still fresh
            code, text, err = run(base("--prices", pf, "--market", "--now", "2026-10-29 11:03", "--json"))
            self.assertEqual(code, 1)                                       # 16 minutes: too old, so no market order
            self.assertIn("No order: a market order needs a fresh price to measure its risk", err)

    def test_reward_risk_is_shown_half_up_and_compared_exactly(self):
        # one rule with psx-exit and psx-risk: meets_reward_risk decides, reward_risk shows (half up)
        s = size.size_trade(plan(entry=100.0, stop=92.0, target=101.0, limits=None), cfg(min_reward_risk=0),
                            today=TODAY)
        self.assertEqual(s["reward_risk"], 0.13)                           # 0.125; round() would give 0.12
        self.assertIn("Reward:risk 0.13.", size.plan_report(s, size.make_order(s)))
        s = size.size_trade(plan(entry=100.0, stop=97.95, target=102.04, limits=None), cfg(), today=TODAY)
        self.assertIn("reward:risk 1.00 (target 102.04, entry 100.00, stop 97.95) is under your minimum 1",
                      s["refusal"])                                        # 0.9951 is under 1
        s = size.size_trade(plan(entry=100.0, stop=97.0, target=104.0, limits=None), cfg(min_reward_risk=1.333),
                            today=TODAY)
        self.assertEqual((s["qty"], s["reward_risk"], s["refusal"]), (1491, 1.33, None))   # 1.3333 is not under
        self.assertTrue(size.meets_reward_risk(100.0, 97.0, 104.0, 1.333))
        self.assertFalse(size.meets_reward_risk(100.0, 97.95, 102.04, 1.0))

    def test_a_blocked_list_written_as_a_string_still_blocks(self):
        for blocked in ("DEMO", "ogdc, demo", ["demo"]):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(plan(), cfg(blocked=blocked), today=TODAY)
            self.assertIn("DEMO is on your blocked list", str(e.exception))
        for bad in (5, {"DEMO": True}, True):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(plan(), cfg(blocked=bad), today=TODAY)
            self.assertIn('"blocked" in psx/config.json must be a list', str(e.exception))
        self.assertEqual(size.size_trade(plan(), cfg(blocked=None), today=TODAY)["qty"], 3428)
        self.assertNotIn("LUCK", shares(size.size(rows(TARGETS), w(blocked="LUCK"), prices=SCREENER)))
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), w(blocked=5), prices=SCREENER)
        with tempfile.TemporaryDirectory() as d:
            c = write_json(os.path.join(d, "config.json"), cfg(blocked=5))
            code, _, err = run(base("--config", c, "--now", NOW))
            self.assertEqual(code, 1)
            self.assertIn("must be a list", err)
            self.assertNotIn("Traceback", err)

    def test_a_config_file_you_name_must_be_there(self):
        # a typo in --config never falls back to the defaults
        code, _, err = run(base("--now", NOW, "--config", NOPE))
        self.assertEqual(code, 1)
        self.assertIn("no config file at %s" % NOPE, err)
        code, _, err = run(["--weights", "OGDC=60,HBL=40", "--config", NOPE, "--data", MARKET])
        self.assertEqual(code, 1)
        self.assertIn("no config file at %s" % NOPE, err)
        # the default psx/config.json may be missing: then --budget names the money
        argv = ["--plan", PLAN, "--portfolio", NOPE, "--data", NOPE, "--now", NOW]
        self.assertIn("no budget", run(argv)[2])
        code, text, _ = run(argv + ["--budget", "500000"])
        self.assertEqual(code, 0)
        self.assertIn("Size: 3,428 shares of DEMO", text)

    def test_the_portfolio_is_the_account(self):
        # with psx/portfolio.json the account is its cash plus holdings, as in psx-risk (not the budget)
        with tempfile.TemporaryDirectory() as d:
            pf = write_json(os.path.join(d, "portfolio.json"), {"cash": 200000, "positions": []})
            code, text, _ = run(base("--portfolio", pf, "--now", NOW, "--json"))
            s = json.loads(text)["sizing"]
            self.assertEqual((s["qty"], s["bound_by"], s["cash_from"]), (1490, "position cap", "portfolio"))
            self.assertEqual((s["account"], s["cash_buffer"], s["cash_available"]), (200000, 20000, 180000))
            rich = write_json(os.path.join(d, "rich.json"), {"cash": 1000000, "positions": []})
            s = json.loads(run(base("--portfolio", rich, "--now", NOW, "--json"))[1])["sizing"]
            self.assertEqual((s["qty"], s["cash_from"], s["limits"][1]["shares"], s["limits"][3]["shares"]),
                             (3428, "portfolio", 7451, 8925))

    def test_bad_plans(self):
        for p, word in ((plan(stop=101.0), "not below the entry"),
                        (plan(target=100.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol"),
                        ([], "not a JSON object")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        for kw, word in ((dict(market=True, price=102.00), "already happened"),
                         (dict(today="2026-10-30"), "make a new plan")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(plan(), cfg(), **dict({"today": TODAY}, **kw))
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["demo"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="ABC", entry=4.0, stop=3.0, target=5.0, limits=None)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_pkr=1.2), today=TODAY)
        self.assertEqual(s["qty"], 1)
        self.assertIn("under your Rs 5,000 minimum order", s["refusal"])
        self.assertIsNone(size.make_order(s))

    def test_missing_plan_and_bad_flags(self):
        code, _, err = run(["--plan", NOPE, "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        for argv, word in ((["--top", "2"], "--weights"), (["--market", "--limit", "100.70"], "pick one"),
                           (["--limit", "abc"], "--limit wants a price"), (["--limit", "0"], "--limit wants a price")):
            code, _, err = run(base("--now", NOW, *argv))
            self.assertEqual(code, 1, argv)
            self.assertIn(word, err)
        code, _, err = run(base("--now", "tomorrow"))
        self.assertEqual(code, 1)
        self.assertIn("Karachi time", err)
        self.assertNotIn("Traceback", err)

    def test_bad_config_values(self):
        for over, word in ((dict(max_loss_per_trade_pkr=-5000), "0 or more"), (dict(cash_buffer_pct=100), "between 0"),
                           (dict(budget=0), "no budget"), (dict(budget="lots"), "no budget")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(plan(), cfg(**over), today=TODAY)
            self.assertIn(word, str(e.exception))
        with tempfile.TemporaryDirectory() as d:
            c = write_json(os.path.join(d, "config.json"), [1, 2])
            code, _, err = run(base("--config", c, "--now", NOW))
            self.assertEqual(code, 1)
            self.assertIn("not a JSON object", err)


OGDC_PLAN = dict(symbol="OGDC", entry=283.29, stop=281.44, target=285.14, limits=None)
DEMO_LOT = {"id": "DEMO-1", "date": TODAY, "shares": 3428, "cost": 345646.15}
BOOK = {"cash": 154353.85, "positions": [{"ticker": "DEMO", "shares": 3428, "lots": [DEMO_LOT]}]}


class Account(unittest.TestCase):
    """The account psx-risk checks against (one rule for both). With psx/portfolio.json it is the
    cash plus the holdings (at a fresh price, else at their cost); with none it is the budget.
    Position cap = account x max_position_pct minus what is held of that stock. Buffer = account x 10%."""

    def risk_would_pass(self, s, cash):
        # psx-risk's cash and position rules for a buy (check.py), from the same account
        self.assertGreaterEqual(cash - s["value"] - s["buy_cost"], s["account"] * 0.10 - 0.01)
        self.assertLessEqual(s["held_value"] + s["value"], s["account"] * 0.75 + 0.01)

    def test_a_smaller_account_shrinks_the_cap(self):
        # rv-money/t-poscap2: Rs 495,000 cash. The old 3,725 shares were 75.7% of the account.
        s = size.size_trade(plan(stop=99.95), cfg(), cash=495000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["account"]), (3688, "position cap", 495000))
        self.assertEqual(s["limits"][1]["how"], "75% of the Rs 495,000 account = Rs 371,250 / 100.65")
        self.risk_would_pass(s, 495000)
        self.assertGreater(3725 * 100.65, 0.75 * 495000)
        s = size.size_trade(plan(), cfg(), cash=300000, today=TODAY)       # rv-money/t-poscap
        self.assertEqual((s["qty"], s["bound_by"]), (2235, "position cap"))
        self.risk_would_pass(s, 300000)

    def test_the_buffer_is_ten_percent_of_the_account(self):
        # the second trade: Rs 154,353.85 cash and 3,428 DEMO worth Rs 348,970.40 (was 367 shares)
        s = size.size_trade(plan(**OGDC_PLAN), cfg(), cash=154353.85, holdings={"DEMO": 348970.40}, today=TODAY)
        self.assertEqual((s["account"], s["cash_buffer"], s["cash_available"]), (503324.25, 50332.43, 104021.42))
        self.assertEqual((s["qty"], s["bound_by"]), (366, "cash"))
        self.risk_would_pass(s, 154353.85)

    def test_what_you_hold_of_the_stock_counts_against_its_cap(self):
        s = size.size_trade(plan(), cfg(), cash=400000, holdings={"DEMO": 100000}, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["held_value"]), (2732, "position cap", 100000))
        self.assertEqual(s["limits"][1]["how"],
                         "75% of the Rs 500,000 account = Rs 375,000, minus Rs 100,000 held = Rs 275,000 / 100.65")
        self.risk_would_pass(s, 400000)

    def test_holdings_at_a_fresh_price_else_at_their_cost(self):
        with tempfile.TemporaryDirectory() as d:
            pf = write_json(os.path.join(d, "portfolio.json"), BOOK)
            # 10:21: the tape says 101.05, so the 3,428 DEMO are worth Rs 346,399.40
            code, text, _ = run(base("--portfolio", pf, "--data", MARKET, "--now", "2026-10-29 10:21"))
            self.assertEqual(code, 0)
            self.assertIn("Account Rs 500,753.25 in psx/portfolio.json: cash Rs 154,353.85 plus holdings "
                          "Rs 346,399.40. Cash buffer 10% = Rs 50,075.33.", text)
            self.assertIn("minus Rs 346,399.40 held = Rs 29,165.54 / 100.65", text)
            self.assertIn("Size: 289 shares of DEMO", text)
            # no fresh price anywhere: the holding counts at its cost, and it says so
            code, text, _ = run(base("--portfolio", pf, "--now", "2026-10-29 10:21", "--json"))
            out = json.loads(text)
            self.assertEqual((out["sizing"]["account"], out["sizing"]["holdings_value"]), (500000, 345646.15))
            self.assertIn("No fresh price for DEMO: its 3,428 shares count at their cost, Rs 345,646.15.",
                          out["notes"])
            out = json.loads(run(base("--portfolio", pf, "--budget", "900000", "--now", NOW, "--json"))[1])
            self.assertEqual(out["sizing"]["account"], 500000)
            self.assertIn("--budget is not used", out["notes"][-1])

    def test_no_portfolio_means_the_budget(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual((s["account"], s["cash_from"], s["held_value"], s["holdings_value"]), (500000, "budget", 0, 0))
        self.assertNotIn("Account Rs", run(base("--now", NOW))[1])

    def test_a_bad_portfolio_is_a_plain_error(self):
        with tempfile.TemporaryDirectory() as d:
            for doc, word in (({"cash": "lots"}, "needs a \"cash\" number"), ([1], "must be a JSON object"),
                              ({"cash": 1000, "positions": 5}, "must be a list")):
                pf = write_json(os.path.join(d, "portfolio.json"), doc)
                code, _, err = run(base("--portfolio", pf, "--now", NOW))
                self.assertEqual(code, 1, doc)
                self.assertIn(word, err)
                self.assertNotIn("Traceback", err)


class PriceLimits(unittest.TestCase):
    """PSX daily price limits: a price can reach a limit, never pass it."""

    def test_a_target_above_the_upper_limit_is_refused(self):
        s = size.size_trade(plan(target=110.0), cfg(), today=TODAY)
        self.assertIn("the target 110.00 is above today's upper price limit 108.24 (from the company page): "
                      "it cannot fill today", s["refusal"])
        self.assertIsNone(size.make_order(s))
        with tempfile.TemporaryDirectory() as d:
            p = write_json(os.path.join(d, "plan.json"), plan(target=110.0))
            out = os.path.join(d, "orders.json")
            code, text, _ = run(base("--plan", p, "--now", NOW, "--write", out))
            self.assertEqual(code, 1)
            self.assertIn("No order: the target 110.00 is above", text)
            self.assertFalse(os.path.exists(out))

    def test_a_target_at_the_upper_limit_can_fill(self):
        s = size.size_trade(plan(target=108.24), cfg(), today=TODAY)
        self.assertIsNone(s["refusal"])
        self.assertEqual(s["qty"], 3428)

    def test_an_entry_outside_the_limits_is_refused(self):
        s = size.size_trade(plan(target=112.0), cfg(), limit=109.0, today=TODAY)
        self.assertIn("the entry 109.00 is outside today's price limits 88.56 to 108.24", s["refusal"])
        low = {"lower": 101.0, "upper": 120.0, "from": "computed"}
        s = size.size_trade(plan(limits=low), cfg(), today=TODAY)
        self.assertIn("the entry 100.65 is outside today's price limits 101.00 to 120.00 (computed from the "
                      "last close)", s["refusal"])

    def test_a_stop_under_the_lower_limit_is_a_warning(self):
        lim = {"lower": 88.56, "upper": 120.0, "ldcp": 98.4, "from": "computed"}
        s = size.size_trade(plan(stop=88.0, target=112.0, limits=lim), cfg(min_reward_risk=0), today=TODAY)
        self.assertIsNone(s["refusal"])
        self.assertGreater(s["qty"], 0)
        self.assertIn("The stop 88.00 is under today's lower price limit 88.56", s["warnings"][0])
        text = size.plan_report(s, size.make_order(s))
        self.assertIn("Today's price limits: 88.56 to 120.00 (computed from the last close).", text)

    def test_limits_from_a_quote_when_the_plan_has_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_json(os.path.join(d, "plan.json"), plan(limits=None))
            code, text, _ = run(base("--plan", p, "--prices", PRICES_1052, "--now", "2026-10-29 10:52", "--json"))
            s = json.loads(text)["sizing"]
            self.assertEqual((code, s["qty"], s["entry"], s["warnings"]), (0, 3428, 100.65, []))
            self.assertEqual(s["day_limits"], {"lower": 88.56, "upper": 108.24, "from": PRICES_1052})
            p = write_json(os.path.join(d, "plan2.json"), plan(limits=None, target=110.0))
            code, text, _ = run(base("--plan", p, "--prices", PRICES_1052, "--now", "2026-10-29 10:52"))
            self.assertEqual(code, 1)
            self.assertIn("above today's upper price limit 108.24 (from %s)" % PRICES_1052, text)
            code, text, _ = run(base("--plan", p, "--data", MARKET, "--now", NOW, "--json"))
            s = json.loads(text)["sizing"]
            self.assertEqual((s["day_limits"]["upper"], s["day_limits"]["from"][:9]), (108.24, "the tape "))

    def test_no_limits_anywhere_is_a_warning(self):
        s = size.size_trade(plan(limits=None), cfg(), today=TODAY)
        self.assertIsNone(s["day_limits"])
        self.assertIn("Today's price limits are not in the plan", s["warnings"][0])
        self.assertEqual(s["qty"], 3428)
        yesterday = {"prices": {"DEMO": 98.4}, "detail": {"DEMO": {"time": "2026-10-28T10:30:00Z",
                                                                    "lower": 85.0, "upper": 104.0}}}
        q = size.file_quote(yesterday, "DEMO", "prices.json")
        self.assertIsNone(size.quote_limits([q], size.parse_now(NOW)))      # another day's limits do not count
        self.assertEqual(size.plan_limits(plan(limits={"lower": 5, "upper": 4})), None)


class Clock(unittest.TestCase):
    """Karachi time (UTC+5, no daylight saving) and the config times moved to the day's hours."""

    def test_flat_by_comes_from_the_plan(self):
        o = size.make_order(size.size_trade(plan(flat_by="15:10"), cfg(), today=TODAY))
        self.assertEqual(o["plan"]["flat_by"], "15:10")

    def test_flat_by_from_the_config_moves_with_the_day(self):
        for day, flat_by, want in (("2026-10-29", "15:20", "15:20"),     # a normal Thursday
                                   ("2026-10-30", "15:20", "16:20"),     # Friday: 10 minutes before 16:30
                                   ("2026-02-23", "15:20", "13:20"),     # a Ramadan Monday: before 13:30
                                   ("2026-10-29", "15:55", "15:30")):    # kept inside the session
            s = size.size_trade(plan(date=day, flat_by=None), cfg(flat_by=flat_by), today=day)
            self.assertEqual(s["flat_by"], want, day)
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(flat_by=None), cfg(flat_by="3pm"), today=TODAY)
        self.assertIn("flat_by wants a Karachi time like 10:15", str(e.exception))

    def test_today_is_the_karachi_date(self):
        code, _, _ = run(base("--now", "2026-10-28T19:30:00Z"))      # 00:30 on 10-29 in Karachi
        self.assertEqual(code, 0)
        code, _, err = run(base("--now", "2026-10-28T18:59:00Z"))    # 23:59 on 10-28 in Karachi
        self.assertEqual(code, 1)
        self.assertIn("the plan is for 2026-10-29 and today is 2026-10-28 in Karachi", err)

    def test_now_without_an_offset_is_karachi_time(self):
        with tempfile.TemporaryDirectory() as d:
            for i, now in enumerate(("2026-10-29 10:15", "2026-10-29 10:15 PKT", "2026-10-29T10:15:00+05:00")):
                out = os.path.join(d, "orders%d.json" % i)
                self.assertEqual(run(base("--now", now, "--write", out))[0], 0)
                with open(out) as fh:
                    self.assertEqual(json.load(fh)["created_at"], "2026-10-29T05:15:00Z", now)


class Costs(unittest.TestCase):
    """Commission max(0.15%, Rs 0.03 a share) each side, 15% sales tax on it, other charges 0.0066%."""

    def test_costs_change_the_size(self):
        one = size.size_trade(plan(), cfg(costs=dict(size.COSTS, day_trade_one_side=True)), today=TODAY)
        self.assertGreater(one["limits"][0]["shares"], 3428)            # the sell leg has no commission
        self.assertLess(one["sell_cost"], one["buy_cost"])
        free = dict(size.COSTS, commission_pct=0, commission_min_per_share=0, other_charges_pct=0)
        s = size.size_trade(plan(), cfg(costs=free), today=TODAY)
        self.assertEqual((s["limits"][0]["shares"], s["costs"]), (4545, 0))
        self.assertEqual((s["qty"], s["bound_by"]), (3725, "position cap"))

    def test_a_partial_costs_block_keeps_the_other_defaults(self):
        s = size.size_trade(plan(), cfg(costs={"commission_pct": 0.25}), today=TODAY)
        self.assertEqual(s["costs_used"], dict(size.COSTS, commission_pct=0.25))
        self.assertLess(s["qty"], 3428)
        self.assertLessEqual(s["max_loss"], 5000)

    def test_the_minimum_a_share_bites_on_a_cheap_share(self):
        cheap = plan(symbol="CHEAP", entry=8.25, stop=8.0, target=8.75, limits=None)
        s = size.size_trade(cheap, cfg(), today=TODAY)
        q = s["qty"]
        self.assertEqual(s["bound_by"], "max loss")
        fee = Decimal("0.03") * q * Decimal("1.15") + Decimal("8.25") * q * Decimal("0.0066") / 100   # Rs 0.03 a share
        self.assertEqual(s["buy_cost"], float(fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)))
        self.assertLessEqual(s["max_loss"], 5000)
        self.assertGreater(size.max_loss_at_stop(8.25, 8.0, q + 1), 5000)

    def test_costs_over_a_quarter_of_the_price_risk_are_named(self):
        # one rule with psx-exit and psx-risk: costs_vs_risk(entry, stop, the sized qty) over COST_WARN
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertAlmostEqual(s["costs_vs_risk"], 1229.14 / 3770.80)
        line = "Costs are about 33% of the price risk (over 25%). You pay them win or lose."
        self.assertEqual(s["cost_warning"], line)
        self.assertIn(line, run(base("--now", NOW))[1])
        wide = size.size_trade(plan(stop=95.0, target=108.0), cfg(), today=TODAY)
        self.assertLess(wide["costs_vs_risk"], size.COST_WARN)
        self.assertIsNone(wide["cost_warning"])
        self.assertNotIn("Costs are about", size.plan_report(wide, size.make_order(wide)))

    def test_bad_cost_settings_are_a_plain_error(self):
        for bad in ({"commission_pct": "abc"}, {"sales_tax_pct": -1}):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(plan(), cfg(costs=bad), today=TODAY)
            self.assertIn('the "costs" block in psx/config.json', str(e.exception))
        with tempfile.TemporaryDirectory() as d:
            c = write_json(os.path.join(d, "config.json"), cfg(costs={"commission_pct": None}))
            code, _, err = run(base("--config", c, "--now", NOW))
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", err)


class WeightsMode(unittest.TestCase):
    """v1's split, kept behind --weights / --targets: Rs 500,000, 10% cash, 25% cap, whole shares."""

    def check_split(self, r):
        self.assertEqual(shares(r), SPLIT)
        self.assertEqual((r["cash_buffer"], r["investable"], r["cap"]), (50000, 450000, 125000))
        self.assertEqual([p["ticker"] for p in r["positions"] if p["capped"]], ["OGDC"])
        self.assertEqual((r["invested"], r["buy_costs"], r["idle"]), (449162.39, 804.45, 33.16))
        self.assertAlmostEqual(r["invested"] + r["buy_costs"] + r["idle"], 450000, places=2)

    def test_from_the_example_file(self):
        self.check_split(size.size(rows(TARGETS), w(), prices=SCREENER))

    def test_from_inline_weights(self):
        ws = size.parse_weights(size.weights_from_arg("OGDC=30,HBL=25,LUCK=20,FFC=15,SYS=10"))
        self.check_split(size.size(ws, w(), prices=SCREENER))

    def test_renormalised_weights(self):
        ws = size.parse_weights({"OGDC": 3, "HBL": 2.5, "LUCK": 2, "FFC": 1.5, "SYS": 1})
        r = size.size(ws, w(), prices=SCREENER)
        self.check_split(r)
        self.assertEqual(dict((p["ticker"], p["weight"]) for p in r["positions"]),
                         {"OGDC": 30, "HBL": 25, "LUCK": 20, "FFC": 15, "SYS": 10})

    def test_cap_redistributes_until_nobody_is_over(self):
        ws = size.parse_weights({"A": 60, "B": 30, "C": 10})
        r = size.size(ws, {"budget": 1000000, "cash_buffer_pct": 0, "max_position_pct": 40, "min_order_pkr": 1},
                      prices={"A": 100, "B": 100, "C": 100})
        slices = dict((p["ticker"], p["slice"]) for p in r["positions"])
        self.assertEqual(slices, {"A": 400000, "B": 400000, "C": 200000})
        self.assertEqual(sorted(p["ticker"] for p in r["positions"] if p["capped"]), ["A", "B"])
        self.assertTrue(all(p["value"] <= 400000 for p in r["positions"]))

    def test_everything_capped_leaves_money_idle(self):
        r = size.size(size.parse_weights({"OGDC": 50, "HBL": 50}), w(), prices=SCREENER)
        self.assertTrue(all(p["capped"] and p["value"] <= 125000 for p in r["positions"]))
        self.assertGreater(r["idle"], 199000)
        self.assertIn("every stock is at its cap", size.report(r, size.orders_from(r)))

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        ws = size.parse_weights({"A": 90, "B": 7, "C": 3})
        r = size.size(ws, {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_pkr": 5000},
                      prices={"A": 10, "B": 10, "C": 10})
        self.assertNotIn("C", shares(r))
        self.assertEqual(r["dropped"][0]["ticker"], "C")
        self.assertIn("under your Rs 5,000 minimum order", r["dropped"][0]["reason"])
        self.assertTrue(all(p["value"] >= 5000 for p in r["positions"]))
        self.assertLessEqual(r["invested"] + r["buy_costs"], 90000)

    def test_a_share_too_dear_for_its_slice_is_dropped(self):
        ws = size.parse_weights({"A": 90, "B": 10})
        r = size.size(ws, {"budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 100, "min_order_pkr": 1000},
                      prices={"A": 10, "B": 20000})
        self.assertEqual(list(shares(r)), ["A"])
        self.assertIn("one share costs Rs 20,000, more than its slice", r["dropped"][0]["reason"])

    def test_blocked_tickers_are_dropped_first(self):
        r = size.size(rows(TARGETS), w(blocked=["luck"]), prices=SCREENER)
        self.assertNotIn("LUCK", shares(r))
        self.assertIn("blocked", r["dropped"][0]["reason"])

    def test_top_n(self):
        r = size.size(rows(TARGETS), w(max_position_pct=100), prices=SCREENER, top=2)
        self.assertEqual(sorted(shares(r)), ["HBL", "OGDC"])

    def test_whole_shares_need_prices(self):
        with self.assertRaises(size.SizeError) as e:
            size.size(rows(TARGETS), w(), prices={"OGDC": 279.54})
        for word in ("HBL, LUCK, FFC, SYS", "psx/data/screener.html", "alerts.py quote HBL"):
            self.assertIn(word, str(e.exception))

    def test_buy_costs_come_out_of_the_cash(self):
        r = size.size(rows(TARGETS), w(), prices=SCREENER)
        c = size.COSTS
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            self.assertEqual(p["buy_cost"], size.leg_cost(p["price"], p["shares"], c))
            self.assertLessEqual(p["value"], r["cap"])
        spent = sum(size.spend(p["price"], p["shares"], c) for p in r["positions"])
        self.assertLessEqual(spent, r["investable"])
        for p in r["positions"]:      # no stock could take one more share inside the cash and the cap
            more = size.spend(p["price"], p["shares"] + 1, c) - size.spend(p["price"], p["shares"], c)
            self.assertTrue(more > r["idle"] or (p["shares"] + 1) * p["price"] > r["cap"], p["ticker"])

    def test_orders_are_limit_orders_in_whole_shares(self):
        r = size.size(rows(TARGETS), w(), prices=SCREENER)
        orders = size.orders_from(r, "a made-up split")
        self.assertEqual(orders[0], {"symbol": "OGDC", "side": "buy", "qty": 446, "type": "limit", "limit_price": 279.54,
                                     "time_in_force": "day", "board": "REG", "why": orders[0]["why"]})
        self.assertIn("capped at 25%", orders[0]["why"])
        self.assertEqual([(o["symbol"], o["qty"]) for o in orders], [(t, SPLIT[t]) for t in SPLIT])
        for o in size.orders_from(r, market=True):
            self.assertEqual(o["type"], "market")
            self.assertNotIn("limit_price", o)

    def test_prices_come_from_the_file_then_the_screener_then_a_company_page(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(MARKET, "screener.html"), d)
            os.makedirs(os.path.join(d, "company"))
            pf = write_json(os.path.join(d, "prices.json"), {"prices": {"OGDC": 280.0}})
            prices, where = size.weights_prices(["OGDC", "HBL", "ZZZZ"], pf, d)
            self.assertEqual(prices, {"OGDC": 280.0, "HBL": 273.36})
            self.assertEqual(where, {"OGDC": pf, "HBL": os.path.join(d, "screener.html")})
            os.remove(os.path.join(d, "screener.html"))
            shutil.copy(os.path.join(MARKET, "company", "SYS.html"), os.path.join(d, "company"))
            prices, where = size.weights_prices(["SYS", "HBL"], None, d)
            self.assertEqual(list(prices), ["SYS"])
            self.assertIn("the saved page", where["SYS"])

    def test_no_budget_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(rows(TARGETS), {"cash_buffer_pct": 10}, prices=SCREENER)
        with self.assertRaises(size.SizeError):
            size.size([{"ticker": "HBL", "name": "", "weight": 1}, {"ticker": "HBL", "name": "", "weight": 2}], w(),
                      prices=SCREENER)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            c = write_json(os.path.join(d, "config.json"), w())
            out = os.path.join(d, "psx", "orders.json")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--data", MARKET, "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            for word in ("left idle", "require_stop", "buy 446 OGDC, limit 279.54, day, REG board", "buy costs"):
                self.assertIn(word, text)
            with open(out) as fh:
                doc = json.load(fh)
            self.assertEqual([o["symbol"] for o in doc["orders"]], ["OGDC", "HBL", "LUCK", "FFC", "SYS"])
            self.assertEqual(doc["created_at"], "2026-10-29T05:15:00Z")
            code, text, _ = run(["--targets", TARGETS, "--config", c, "--data", MARKET, "--json"])
            self.assertEqual(json.loads(text)["result"]["idle"], 33.16)
            code, _, err = run(["--targets", NOPE, "--config", c])
            self.assertEqual(code, 1)
            self.assertIn("no weights file", err)
            code, _, err = run(["--targets", TARGETS, "--config", c, "--data", MARKET, "--limit", "100"])
            self.assertEqual(code, 1)
            self.assertIn("--limit PRICE is for one trade", err)
            code, _, err = run(["--targets", TARGETS, "--config", c, "--data", NOPE])
            self.assertEqual(code, 1)
            self.assertIn("whole shares need a price", err)


class Words(unittest.TestCase):
    """Plain words, no US leftovers, and the SKILL.md example is the tool's own output."""

    US_WORDS = ("Alpaca", "Yahoo", "Stooq", "Nasdaq", "EDGAR", "NYSE", "FINRA", "New York", "Eastern",
                "Congress", "Pelosi", "TSLA", "trader-", "trader/")

    def test_no_us_leftovers_in_the_skill_folder(self):
        for f in sorted(os.listdir(SKILL)):
            path = os.path.join(SKILL, f)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for word in self.US_WORDS:
                self.assertFalse(word in text, "%s: %s" % (f, word))
            for pat in (r"\bSEC\b", r"\bET\b", r"\$\s?\d", chr(0x2014)):
                self.assertIsNone(re.search(pat, text), "%s: %s" % (f, pat))

    def test_the_output_stays_plain(self):
        texts = [run(base("--now", NOW))[1], run(base("--data", MARKET, "--now", NOW, "--market"))[1],
                 run(base("--now", "2026-10-29 10:52", "--limit", "101.30"))[1],
                 size.report(size.size(rows(TARGETS), w(), prices=SCREENER), [])]
        for text in texts:
            self.assertIn("not financial advice", text)
            for bad in (r"\$", r"\blive\b", r"real[ -]time", r"guarantee", r"profit", r"\bP&L\b", chr(0x2014)):
                self.assertIsNone(re.search(bad, text, re.I), bad)

    def test_skill_md_uses_easy_words(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            doc = fh.read().lower()
        for word in ("renormalis", "renormaliz", "percentile", "histogram"):
            self.assertFalse(word in doc, word)
        # a command that names a config file says how to make it
        if "--config split.json" in doc:
            self.assertIn("copy `config.example.json` to `split.json`", doc)

    def test_skill_md_shows_the_tools_real_output(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            doc = fh.read()
        shown = [l for l in run(base("--now", NOW))[1].splitlines() if l.strip()]
        shown += [l for l in run(base("--now", "2026-10-29 10:52", "--limit", "101.30"))[1].splitlines()
                  if l.startswith("No order:")]
        for line in shown:
            self.assertTrue(line + "\n" in doc, "SKILL.md lacks the line: %s" % line)


class SwingCase(unittest.TestCase):
    """A temp folder with the example config set to "style": "swing" (the files the CLI tests name)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psx-budget-swing-")
        self.cfg_path = write_json(os.path.join(self.tmp, "config.json"), swing_cfg())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name, doc):
        return write_json(os.path.join(self.tmp, name), doc)

    def sargs(self, *more, **kw):
        return (["--plan", kw.get("plan", SWING_PLAN), "--config", kw.get("config", self.cfg_path),
                 "--portfolio", kw.get("portfolio", NOPE), "--data", NOPE, "--state", kw.get("state", NOPE)]
                + list(more))


class Swing(SwingCase):
    """SPEC v2 2.4 psx-budget: a swing plan, sized in the pre-open of its buy day (Thu 2026-10-29)."""

    def test_the_example_is_1436_shares_bound_by_the_max_loss(self):
        s = size.size_trade(swing_plan(), swing_cfg(), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["entry"], s["stop"], s["target"]), (1436, "max loss", 99.38,
                                                                                       96.25, 105.64))
        self.assertEqual((s["value"], s["price_risk"], s["max_loss"]), (142709.68, 4494.68, 4997.81))
        self.assertEqual((s["buy_cost"], s["sell_cost"], s["costs"]), (255.59, 247.54, 503.13))
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 1436), ("gap limit", 1459), ("position cap", 3773), ("max order", 4024),
                          ("cash", 4519)])
        self.assertEqual((s["gap_loss"], s["gap_limit"], s["gap_low"]), (14759.46, 15000, 89.44))
        self.assertEqual((s["style"], s["preview"], s["flat_by"]), ("swing", None, None))
        self.assertEqual((s["exit_by"], s["exit_by_time"], s["trend_sessions"], s["hold_sessions"]),
                         ("2026-11-27", "16:20", 10, 20))
        self.assertEqual(s["one_more"], {"qty": 1437, "max_loss": 5001.3})
        self.assertEqual((s["refusal"], s["warnings"], s["waiting"]), (None, [], None))

    def test_rs_500_a_trade_is_143_shares(self):
        s = size.size_trade(swing_plan(), swing_cfg(max_loss_per_trade_pkr=500), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["value"], s["max_loss"]), (143, "max loss", 14211.34, 497.69))
        self.assertEqual((s["gap_loss"], s["gap_limit"]), (1469.78, 1500))      # null: 3 x the max loss per trade
        self.assertEqual([l["shares"] for l in s["limits"][:2]], [143, 145])
        self.assertIn("Loss if it opens at the lower limit the next day: about Rs 1,469.78, costs included (your gap "
                      "limit Rs 1,500). More days down can lose more.", size.plan_report(s, size.make_order(s)))

    def test_the_order_is_the_contract_shape(self):
        o = size.make_order(size.size_trade(swing_plan(), swing_cfg(), today=TODAY))
        want = contract("orders-swing.json")["orders"][0]
        self.assertEqual(o, want)
        self.assertEqual(list(o["plan"]), list(want["plan"]))
        self.assertEqual((o["plan"]["style"], o["plan"]["flat_by"], o["plan"]["exit_by"], o["plan"]["gap_loss"]),
                         ("swing", None, "2026-11-27", 14759.46))
        self.assertNotIn("trigger_price", o)
        self.assertIn("20-day closing-high breakout on DEMO: 1436 shares, bound by your Rs 5,000 max loss", o["why"])

    def test_the_cli_writes_the_contract_orders_file(self):
        out = os.path.join(self.tmp, "psx", "orders.json")
        code, text, err = run(self.sargs("--now", SWING_NOW, "--write", out))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Wrote 1 proposed order to %s" % out, text)
        with open(out) as fh:
            self.assertEqual(json.load(fh), contract("orders-swing.json"))     # created_at 2026-10-29T04:20:00Z

    def test_the_report_lines(self):
        code, text, _ = run(self.sargs("--now", SWING_NOW))
        self.assertEqual(code, 0)
        for line in ("DEMO swing buy on Thu 2026-10-29, 20-day closing-high breakout, entry 99.38 (last close + 1%)",
                     "Price limits for Thu 2026-10-29: 88.56 to 108.24 (computed from the last close).",
                     "  gap limit     Rs 15,000 if it opens at the lower limit 89.44, costs included",
                     "Size: 1,436 shares of DEMO = Rs 142,709.68 at 99.38.",
                     "Max loss at the stop: Rs 4,997.81, costs included.",
                     "Loss if it opens at the lower limit the next day: about Rs 14,759.46, costs included (your gap "
                     "limit Rs 15,000). More days down can lose more.",
                     "PSX orders last one day: a limit buy that does not fill today ends at the close. "
                     "Do not chase it.",
                     "Hold up to Fri 2026-11-27 (20 sessions). Out earlier at the stop, the target or a trend break.",
                     "Next: psx-risk checks it"):
            self.assertIn(line, text)
        for gone in ("Flat by", "Preview", "bound by your gap limit"):
            self.assertNotIn(gone, text)
        self.assertGreater(check_words(self, text), 20)

    def test_a_target_over_the_upper_limit_is_a_note(self):
        s = size.size_trade(swing_plan(target=110.0), swing_cfg(), today=TODAY)
        self.assertIsNone(s["refusal"])
        self.assertEqual(s["qty"], 1436)
        self.assertEqual(s["warnings"], ["The target 110.00 is over the upper price limit 108.24 (computed from the "
                                         "last close): it cannot fill on Thu 2026-10-29; it can on a later day."])
        self.assertEqual(size.make_order(s)["take_profit"], 110.0)
        day = size.size_trade(plan(target=110.0), cfg(), today=TODAY)          # day style: still refused
        self.assertIn("above today's upper price limit 108.24", day["refusal"])

    def test_a_stop_under_the_lower_limit_gets_the_swing_words(self):
        s = size.size_trade(swing_plan(stop=88.0, target=112.0), swing_cfg(min_reward_risk=0), today=TODAY)
        self.assertIsNone(s["refusal"])
        self.assertIn("The stop 88.00 is under the lower price limit 88.56: it cannot be hit on Thu 2026-10-29; it "
                      "counts from the next session.", s["warnings"])
        s = size.size_trade(swing_plan(limits=None), swing_cfg(), today=TODAY)
        self.assertEqual(s["warnings"], ["The price limits for Thu 2026-10-29 are not in the plan, so the target was "
                                         "not checked against them (psx-risk checks them)."])
        s = size.size_trade(swing_plan(target=130.0), swing_cfg(), limit=109.40, today=TODAY)
        self.assertIn("the entry 109.40 is outside the price limits for Thu 2026-10-29, 88.56 to 108.24", s["refusal"])
        self.assertIsNone(size.make_order(s))

    def test_a_stop_at_the_lower_limit_can_be_hit_that_day(self):
        # review F39: PSX trades at a limit, never past it. A stop AT the buy day's lower limit 88.56 can be hit that
        # day, so it gets its own words; only a stop UNDER it waits for the next session (the rule of psx-exit)
        def stop_notes(stop):
            s = size.size_trade(swing_plan(stop=stop, target=108.0), swing_cfg(min_reward_risk=0), today=TODAY)
            self.assertEqual((s["refusal"], s["day_limits"]["lower"], s["day_limits"]["upper"]), (None, 88.56, 108.24))
            self.assertGreater(s["qty"], 0)
            return [x for x in s["warnings"] if x.startswith("The stop")]
        at = ("The stop 88.56 is at the lower price limit 88.56: the price can trade at the limit, so the stop can be "
              "hit on Thu 2026-10-29. At the limit there may be no buyers, so you may not get out.")
        self.assertEqual(stop_notes(88.56), [at])
        self.assertEqual(stop_notes(88.55), ["The stop 88.55 is under the lower price limit 88.56: it cannot be hit on "
                                             "Thu 2026-10-29; it counts from the next session."])
        self.assertEqual(stop_notes(88.57), [])
        check_words(self, at)
        s = size.size_trade(swing_plan(stop=88.56, target=108.0), swing_cfg(min_reward_risk=0), today=TODAY)
        self.assertIn(at, size.plan_report(s, size.make_order(s)).splitlines())
        # day style keeps its v1 rule and words: only a stop under today's lower limit gets a warning
        for stop, want in ((88.56, []), (88.55, ["The stop 88.55 is under today's lower price limit 88.56: the price "
                                                 "cannot reach it today, and at the limit there may be no buyers."])):
            s = size.size_trade(plan(stop=stop, target=108.0), cfg(min_reward_risk=0), today=TODAY)
            self.assertEqual([x for x in s["warnings"] if x.startswith("The stop")], want, stop)

    def test_styles_do_not_mix(self):
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(swing_plan(), cfg(), today=TODAY)
        self.assertEqual(str(e.exception), 'this is a swing plan, and psx/config.json says "style": "day". Styles do '
                                           'not mix: make a day plan with psx-exit')
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), swing_cfg(), today=TODAY)
        self.assertEqual(str(e.exception), 'this is a day plan, and psx/config.json says "style": "swing". Styles do '
                                           'not mix: make a swing plan with psx-exit (exit.py plan --symbol DEMO)')
        self.assertEqual(size.size_trade(plan(style="day"), cfg(), today=TODAY)["qty"], 3428)
        for p, word in ((swing_plan(style="invest"), "invest.py orders"), (swing_plan(style="week"), '"week"')):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, swing_cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        day_cfg = self.path("day.json", cfg())
        out = os.path.join(self.tmp, "orders.json")
        for argv in (self.sargs("--now", SWING_NOW, "--write", out, config=day_cfg),
                     self.sargs("--now", NOW, "--write", out, plan=PLAN)):
            code, text, err = run(argv)
            self.assertEqual((code, text), (1, ""))
            self.assertIn("Styles do not mix", err)
            self.assertFalse(os.path.exists(out))
        bad = self.path("bad.json", cfg(style="week"))
        code, _, err = run(self.sargs("--now", SWING_NOW, config=bad))
        self.assertEqual(code, 1)
        self.assertIn('"style" in psx/config.json is "day" or "swing"', err)
        self.assertNotIn("Traceback", err)

    def test_the_style_line_names_no_stock_you_hold_or_an_old_plan(self):
        # F85: the v1 day plan of a stock you hold, in a folder set to swing (a share bought before the switch).
        # Swing blocks a second buy of a held stock, and an old plan is no reason to plan its stock again. So the
        # line names the plan's stock only for a plan of today (or a later day) of a stock you do not hold.
        head = 'this is a day plan, and psx/config.json says "style": "swing". Styles do not mix'
        old = ('this is a day plan for Thu 2026-10-29, an earlier day, and psx/config.json says "style": "swing". '
               'Styles do not mix')
        held = ('. You already hold DEMO in psx/portfolio.json. In swing style psx-risk blocks a second buy of a stock '
                'you hold. To size a buy of another stock, first make a swing plan for it with psx-exit (exit.py plan '
                '--symbol X).')
        same = ": make a swing plan with psx-exit (exit.py plan --symbol DEMO)"
        book = dict(cash=BOOK["cash"], holdings={"DEMO": 345646.15})
        for kw, want in ((dict(book, today=TODAY), head + held),
                         (dict(book, today="2026-10-30"), old + held),
                         (dict(today="2026-10-30"), old + ": make a new swing plan with psx-exit"),
                         (dict(today="2026-10-28"), head + same),                  # the evening before: not old
                         (dict(cash=BOOK["cash"], holdings={"SYS": 12232.0}, today=TODAY), head + same)):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(plan(), swing_cfg(), **kw)
            self.assertEqual(str(e.exception), want)
            check_words(self, want)
        # the CLI reads psx/portfolio.json for this line. A bad ledger is "not held" here: its error comes later
        out = os.path.join(self.tmp, "orders.json")
        for doc, now, want in (
                (BOOK, "2026-10-30 10:30", old + held),                            # the v1 ledger: no plan block
                ({"cash": 1000, "positions": [{"symbol": "demo", "lots": [{"shares": 24, "cost": 2400}]}]}, NOW,
                 head + held),
                ({"cash": 1000, "positions": [{"ticker": "SYS", "lots": 5}, "x", {"ticker": "DEMO", "shares": 24}]},
                 NOW, head + held),
                ({"cash": 1000, "positions": [{"ticker": "DEMO", "shares": 0}]}, NOW, head + same),
                ({"cash": 1000, "positions": 5}, NOW, head + same), ([], NOW, head + same), ("{", NOW, head + same)):
            pf = os.path.join(self.tmp, "pf.json")
            with open(pf, "w") as fh:
                fh.write(doc if isinstance(doc, str) else json.dumps(doc))
            code, text, err = run(self.sargs("--now", now, "--write", out, plan=PLAN, portfolio=pf))
            self.assertEqual((code, text, err), (1, "", "size.py: %s\n" % want), doc)
            self.assertFalse(os.path.exists(out))
        # day style is as in v1: a day plan of a stock you hold is sized (the cap counts what you hold)
        code, text, err = run(base("--now", NOW, "--portfolio", self.path("book.json", BOOK)))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Size: 291 shares of DEMO = Rs 29,289.15 at 100.65.", text)
        self.assertNotIn("You already hold", text)

    def test_one_side_commission_changes_nothing_in_swing(self):
        one = dict(size.COSTS, day_trade_one_side=True)
        s = size.size_trade(swing_plan(), swing_cfg(costs=one), today=TODAY)
        self.assertEqual((s["qty"], s["costs"], s["max_loss"], s["gap_loss"]), (1436, 503.13, 4997.81, 14759.46))
        self.assertIs(s["costs_used"]["day_trade_one_side"], False)        # trade_costs: both sides in swing
        day = size.size_trade(plan(), cfg(costs=one), today=TODAY)
        self.assertIs(day["costs_used"]["day_trade_one_side"], True)        # day style: as set

    def test_day_style_keeps_the_v1_keys(self):
        # D23: a day sizing and a day order carry exactly the v1 keys (the golden diff reads --json)
        s = size.size_trade(plan(), cfg(), today=TODAY)
        for k in ("style", "preview", "gap_loss", "exit_by", "waiting"):
            self.assertNotIn(k, s)
        self.assertEqual(list(size.make_order(s)["plan"]), ["entry", "stop", "target", "risk_per_share", "max_loss",
                                                            "costs", "setup", "entry_time", "flat_by"])
        self.assertNotIn("gap limit", [l["name"] for l in s["limits"]])

    def test_the_preview_the_evening_before(self):
        s = size.size_trade(swing_plan(), swing_cfg(), today="2026-10-28")
        self.assertEqual((s["preview"], s["qty"], s["refusal"]), ("2026-10-29", 1436, None))
        code, text, err = run(self.sargs("--now", EVENING))
        self.assertEqual((code, err), (0, ""))
        for line in ("Preview of the order (nothing is written and nothing is placed):",
                     "Preview for Thu 2026-10-29: size it again on that day, before you buy."):
            self.assertIn(line, text)
        self.assertNotIn("Next: psx-risk", text)
        check_words(self, text)
        out = os.path.join(self.tmp, "orders.json")
        code, text, err = run(self.sargs("--now", EVENING, "--write", out))
        self.assertEqual((code, text), (1, ""))
        self.assertIn("--write is refused for a preview: the plan is for Thu 2026-10-29, the next session. "
                      "psx/orders.json must be made on the buy day", err)
        self.assertFalse(os.path.exists(out))
        check_words(self, err)

    def test_the_next_session_skips_weekends_and_holidays(self):
        # Fri 2026-11-06 in the evening: the next session is Tue 2026-11-10 (Mon 2026-11-09 is Iqbal Day)
        p = swing_plan(date="2026-11-10", limits=None)
        self.assertEqual(size.size_trade(p, swing_cfg(), today="2026-11-06")["preview"], "2026-11-10")
        self.assertEqual(size.size_trade(p, swing_cfg(), today="2026-11-07")["preview"], "2026-11-10")   # Saturday
        for today in ("2026-11-05", "2026-11-11"):          # two sessions early, or a day late: a new plan
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, swing_cfg(), today=today)
            self.assertIn("the plan is for 2026-11-10 and today is %s in Karachi: make a new plan" % today,
                          str(e.exception))
        with self.assertRaises(size.SizeError):              # day style has no preview
            size.size_trade(plan(), cfg(), today="2026-10-28")

    def test_a_preview_does_not_use_todays_limits(self):
        # the quote of today gives today's limits, not the buy day's: only the plan's limits count then
        doc = {"prices": {"DEMO": 98.4}, "detail": {"DEMO": {"time": "2026-10-28T10:30:00Z", "lower": 85.0,
                                                             "upper": 104.0}}}
        prices = self.path("prices.json", doc)
        p = self.path("plan.json", swing_plan(limits=None))
        code, text, _ = run(self.sargs("--now", "2026-10-28 15:40", "--prices", prices, "--json", plan=p))
        s = json.loads(text)["sizing"]
        self.assertEqual((code, s["preview"], s["day_limits"]), (0, "2026-10-29", None))
        self.assertIn("The price limits for Thu 2026-10-29 are not in the plan", s["warnings"][0])

    def test_a_swing_plan_needs_its_exit_by_day(self):
        for p, word in ((swing_plan(exit_by=None), "no exit-by day"), (swing_plan(exit_by="soon"), "no exit-by day"),
                        (swing_plan(exit_by="2026-10-20"), "is before its buy day")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, swing_cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        s = size.size_trade(swing_plan(exit_by_time=None, max_hold_sessions=None, trend_sessions=None), swing_cfg(),
                            today=TODAY)
        self.assertEqual((s["exit_by_time"], s["hold_sessions"], s["trend_sessions"]), ("16:20", 20, 10))  # Friday
        self.assertIsNone(size.make_order(size.size_trade(swing_plan(flat_by="15:20"), swing_cfg(),
                                                          today=TODAY))["plan"]["flat_by"])

    def test_json(self):
        code, text, _ = run(self.sargs("--now", SWING_NOW, "--json"))
        out = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual((out["sizing"]["style"], out["sizing"]["qty"], out["sizing"]["gap_loss"]),
                         ("swing", 1436, 14759.46))
        self.assertEqual(out["orders"], contract("orders-swing.json")["orders"])
        self.assertEqual(out["notes"], ["No psx/portfolio.json yet: sized as if nothing is open. Record each fill "
                                        "with psx-broker."])


class SwingGap(SwingCase):
    """D55: the gap loss (the next session opens at its lower price limit, costs included) and its limit."""

    def test_the_gap_limit_binds_with_a_tight_stop(self):
        s = size.size_trade(swing_plan(stop=98.38, target=101.38), swing_cfg(), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["gap_loss"]), (1459, "gap limit", 14995.86))
        self.assertEqual([l["shares"] for l in s["limits"][:2]], [3692, 1459])     # the max loss would allow 3,692
        self.assertEqual(s["gap_more"], {"qty": 1460, "gap_loss": 15006.13})
        o = size.make_order(s)
        self.assertEqual((o["qty"], o["plan"]["gap_loss"], o["plan"]["max_loss"]), (1459, 14995.86, 1975.76))
        self.assertIn("1459 shares, bound by your Rs 15,000 gap limit (Rs 14,995.86 if the next session opens at the "
                      "lower limit 89.44, costs included", o["why"])
        text = size.plan_report(s, o)
        self.assertIn("  bound by your gap limit: one more share (1,460) would lose Rs 15,006.13 on such an open: over "
                      "your Rs 15,000", text)
        row = [l for l in text.splitlines() if l.startswith("  gap limit ")]
        self.assertEqual(len(row), 1)
        self.assertTrue(row[0].endswith("1,459 shares  <- binds"), row[0])
        check_words(self, text)

    def test_a_gap_limit_set_by_hand(self):
        s = size.size_trade(swing_plan(), swing_cfg(swing={"max_gap_loss_pkr": 10000}), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["gap_limit"], s["gap_loss"]), (972, "gap limit", 10000, 9990.39))
        self.assertEqual(s["limits"][1]["how"], "Rs 10,000 if it opens at the lower limit 89.44, costs included")
        self.assertEqual(s["exit_by"], "2026-11-27")         # the rest of the swing block keeps its defaults
        s = size.size_trade(swing_plan(), swing_cfg(swing={"max_gap_loss_pkr": 20000}), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["gap_limit"]), (1436, "max loss", 20000))
        for bad in ("lots", -1, True, [5]):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(swing_plan(), swing_cfg(swing={"max_gap_loss_pkr": bad}), today=TODAY)
            self.assertIn("swing.max_gap_loss_pkr in psx/config.json must be a number", str(e.exception))
        s = size.size_trade(swing_plan(), swing_cfg(swing={"max_gap_loss_pkr": 5}), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertEqual(s["refusal"], "one share loses Rs 10.28 if the next session opens at its lower limit 89.44, "
                                       "costs included, more than your Rs 5 gap limit (swing.max_gap_loss_pkr). Skip "
                                       "this trade.")
        self.assertIsNone(size.make_order(s))

    def test_0_is_a_limit_of_rs_0_in_both_tools(self):
        # review F22: swing.max_gap_loss_pkr 0 is a Rs 0 cap (the gap loss must have a cap, D55), so every swing buy
        # is refused here, and psx-risk reads 0 the same way. Null is 3 x the max loss per trade; no limit only when
        # there is no max loss per trade either. SKILL.md says so
        s = size.size_trade(swing_plan(), swing_cfg(swing={"max_gap_loss_pkr": 0}), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["gap_limit"]), (0, "gap limit", 0.0))
        self.assertEqual(s["refusal"], "one share loses Rs 10.28 if the next session opens at its lower limit 89.44, "
                                       "costs included, more than your Rs 0 gap limit (swing.max_gap_loss_pkr). Skip "
                                       "this trade.")
        self.assertIsNone(size.make_order(s))
        check = load("psx-risk", "check")
        caps = []
        for per, cap in ((5000, None), (500, None), (500, 0), (500, 1400), (0, None), (0, 0), (0, 900)):
            c = swing_cfg(max_loss_per_trade_pkr=per, swing={"max_gap_loss_pkr": cap})
            caps.append(size.gap_cap(c))
            self.assertEqual(caps[-1], check.swing_gap_cap(check.swing_settings(c), per), (per, cap))
        self.assertEqual(caps, [15000, 1500, 0, 1400, None, 0, 900])
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            doc = " ".join(fh.read().split())
        self.assertIn("`swing.max_gap_loss_pkr` set to 0 is a limit of Rs 0, so every swing buy is refused (the gap "
                      "loss must have a cap). psx-risk reads 0 the same way. With null and no "
                      "`max_loss_per_trade_pkr`, there is no gap limit, in psx-risk too.", doc)

    def test_no_max_loss_means_no_default_gap_limit(self):
        s = size.size_trade(swing_plan(), swing_cfg(max_loss_per_trade_pkr=None), today=TODAY)
        self.assertEqual(([l["name"] for l in s["limits"]], s["gap_limit"]), (["position cap", "max order", "cash"],
                                                                              None))
        self.assertEqual((s["qty"], s["bound_by"], s["gap_loss"]), (3773, "position cap", 38779.56))
        self.assertEqual(s["gap_loss"], size.gap_loss(99.38, 3773, size.COSTS))
        self.assertIn("Loss if it opens at the lower limit the next day: about Rs 38,779.56, costs included. More days "
                      "down can lose more.", size.plan_report(s, size.make_order(s)))

    def test_the_gap_search_finds_the_edge(self):
        c = size.COSTS
        for entry in (99.38, 8.25, 0.85):
            for cap in (50, 1500, 15000):
                q = size.gap_shares(entry, cap, c)
                self.assertLessEqual(size.gap_loss(entry, q, c), cap)
                self.assertGreater(size.gap_loss(entry, q + 1, c), cap)
        self.assertEqual((size.gap_shares(99.38, 15000, c), size.gap_shares(99.38, 1500, c)), (1459, 145))
        self.assertEqual(size.gap_loss(99.38, 1436, c), 14759.46)


class SwingLedger(SwingCase):
    """The cash in swing: psx/portfolio.json less today's buys with no fill yet (D57), as psx-risk counts it."""

    def sized(self, *more, **kw):
        code, text, err = run(self.sargs(*(more + ("--json",)), **kw))
        self.assertEqual((code, err), (0, ""), err)
        return json.loads(text)

    def test_an_open_swing_position_lowers_the_cash_and_counts_toward_the_cap(self):
        pf = os.path.join(V2, "portfolio-swing.json")      # cash 357,034.73 and 1,436 DEMO at a cost of 142,709.68
        out = self.sized("--now", SWING_NOW, portfolio=pf)
        s = out["sizing"]
        self.assertEqual((s["cash_from"], s["account_cash"], s["holdings_value"], s["account"]),
                         ("portfolio", 357034.73, 142709.68, 499744.41))
        self.assertEqual((s["cash_buffer"], s["cash_available"], s["held_value"]), (49974.44, 307060.29, 142709.68))
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]][2:],
                         [("position cap", 2335), ("max order", 4024), ("cash", 3084)])
        self.assertEqual(s["limits"][2]["how"], "75% of the Rs 499,744.41 account = Rs 374,808.31, minus "
                                                "Rs 142,709.68 held = Rs 232,098.63 / 99.38")
        self.assertIn("You already hold DEMO in psx/portfolio.json. In swing style psx-risk blocks a second buy of a "
                      "stock you hold.", s["warnings"])
        self.assertIn("No fresh price for DEMO: its 1,436 shares count at their cost, Rs 142,709.68.", out["notes"])
        other = size.size_trade(swing_plan(symbol="SYS"), swing_cfg(), cash=357034.73,
                                holdings={"DEMO": 142709.68}, today=TODAY)
        self.assertEqual((other["held_value"], other["cash_available"], other["limits"][2]["shares"],
                          other["warnings"]), (0, 307060.29, 3771, []))
        short = size.size_trade(swing_plan(symbol="SYS"), swing_cfg(), cash=100000, holdings={"DEMO": 142709.68},
                                today=TODAY)
        self.assertEqual((short["qty"], short["bound_by"], short["cash_available"]), (760, "cash", 75729.03))

    def test_no_ledger_but_buys_is_refused(self):
        line = ("A swing account needs its ledger: record your fills with psx-broker (ticket.py fill), so open "
                "positions are not counted as cash.")
        filled = {"orders": [{"at": "2026-10-27T10:00:00+05:00", "date": "2026-10-27", "status": "filled",
                              "symbol": "OGDC", "side": "buy", "qty": 100, "price": 280.0, "costs": 50.21}]}
        out = os.path.join(self.tmp, "orders.json")
        for state in (os.path.join(V2, "broker-placed-unfilled.json"), self.path("filled.json", filled)):
            code, text, err = run(self.sargs("--now", "2026-10-30 09:20", "--write", out, state=state,
                                             plan=self.path("plan30.json", swing_plan(date="2026-10-30"))))
            self.assertEqual((code, text), (1, ""))
            self.assertEqual(err.strip(), "size.py: " + line)
            self.assertFalse(os.path.exists(out))
        # a buy approved on an earlier day and never placed is no position: sized from the budget, with the note
        s = self.sized("--now", "2026-10-30 09:20", state=os.path.join(V2, "broker-approved-old.json"),
                       plan=self.path("plan30.json", swing_plan(date="2026-10-30")))
        self.assertEqual((s["sizing"]["cash_from"], s["sizing"]["cash_available"], s["sizing"]["waiting"]),
                         ("budget", 450000, None))
        self.assertEqual(s["notes"], ["No psx/portfolio.json yet: sized as if nothing is open. Record each fill with "
                                      "psx-broker."])
        # day style never reads the broker log here: v1
        code, text, _ = run(base("--now", NOW, "--state", os.path.join(V2, "broker-placed-unfilled.json")))
        self.assertEqual(code, 0)
        self.assertIn("Size: 3,428 shares of DEMO", text)
        self.assertNotIn("No psx/portfolio.json yet", text)

    def test_a_buy_approved_today_with_no_fill_lowers_the_cash(self):
        pf = self.path("portfolio.json", {"cash": 500000, "positions": []})
        p30 = self.path("plan30.json", swing_plan(date="2026-10-30"))
        today = self.path("today.json", moved(contract("broker-placed-unfilled.json"), "2026-10-30"))
        out = self.sized("--now", "2026-10-30 09:20", portfolio=pf, plan=p30, state=today)
        s = out["sizing"]
        self.assertEqual((s["account_cash"], s["holdings_value"], s["account"]), (357034.73, 142709.68, 499744.41))
        self.assertEqual((s["cash_available"], s["qty"]), (307060.29, 1436))
        self.assertEqual([(w["symbol"], w["qty"], w["entry_ref"], w["value"], w["pkr"]) for w in s["waiting"]["rows"]],
                         [("DEMO", 1436, 99.38, 142709.68, 142965.27)])
        self.assertIn("A buy of DEMO from today has no fill yet (psx/state/broker.json). psx-risk blocks a second buy "
                      "of DEMO today.", s["warnings"])
        self.assertIn("minus Rs 142,709.68 held or waiting = Rs 232,098.63 / 99.38", s["limits"][2]["how"])
        code, text, _ = run(self.sargs("--now", "2026-10-30 09:20", portfolio=pf, plan=p30, state=today))
        self.assertIn("Waiting buys today with no fill yet (psx/state/broker.json): 1,436 DEMO at 99.38 (Rs 142,965.27 "
                      "with costs). They come off the cash and count as held until they fill or end.", text)
        self.assertIn("Rs 307,060.29 (cash in psx/portfolio.json after today's waiting buys, minus the buffer)", text)
        check_words(self, text)
        # the same buy on its own day (yesterday now), approved only, ended, or filled
        for name, doc, avail in (
                ("old.json", contract("broker-placed-unfilled.json"), 450000),
                ("approved.json", moved(contract("broker-approved-old.json"), "2026-10-30"), 307060.29),
                ("expired.json", moved(contract("broker-expired.json"), "2026-10-30"), 450000)):
            s = self.sized("--now", "2026-10-30 09:20", portfolio=pf, plan=p30, state=self.path(name, doc))["sizing"]
            self.assertEqual(s["cash_available"], avail, name)
        filled = moved(contract("broker-placed-unfilled.json"), "2026-10-30")
        filled["orders"].append({"at": "2026-10-30T09:40:00+05:00", "date": "2026-10-30", "status": "filled",
                                 "symbol": "DEMO", "side": "buy", "qty": 1436, "price": 99.38, "costs": 255.59})
        s = self.sized("--now", "2026-10-30 09:45", portfolio=pf, plan=p30, state=self.path("f.json", filled))["sizing"]
        self.assertEqual((s["cash_available"], s["waiting"]), (450000, None))   # the fill is in the ledger by now

    def test_a_waiting_buy_with_no_price_is_a_plain_error(self):
        doc = moved(contract("broker-approved-old.json"), "2026-10-30")
        del doc["orders"][0]["entry_ref"]
        code, text, err = run(self.sargs("--now", "2026-10-30 09:20", state=self.path("noref.json", doc),
                                         plan=self.path("plan30.json", swing_plan(date="2026-10-30"))))
        self.assertEqual((code, text), (1, ""))
        self.assertIn("an earlier buy today (DEMO) has no price in the log (psx/state/broker.json), so the cash cannot "
                      "be counted. Record its fill (ticket.py fill), or record it expired (ticket.py result "
                      "--fingerprint 0d80ffbb0d898a06 --status expired)", err)
        self.assertNotIn("Traceback", err)
        check_words(self, err)

    def test_waiting_buys_follow_the_newest_record(self):
        day = "2026-10-29"
        self.assertEqual(size.waiting_buys(contract("broker-placed-unfilled.json"), day),
                         [{"symbol": "DEMO", "qty": 1436, "entry_ref": 99.38, "fingerprint": "0d80ffbb0d898a06",
                           "status": "placed"}])
        self.assertEqual(size.waiting_buys(contract("broker-placed-unfilled.json"), "2026-10-30"), [])
        self.assertEqual(size.waiting_buys(moved(contract("broker-expired.json"), day), day), [])
        self.assertEqual(size.waiting_buys(contract("broker-protect-open.json"), "2026-10-30"), [])     # a sell
        approve = contract("broker-approved-old.json")["orders"][0]
        for status, n in (("skipped", 0), ("failed", 0), ("cancelled", 0), ("declined", 0), ("placed", 1)):
            later = {"at": "2026-10-29T09:30:00+05:00", "date": day, "fingerprint": approve["fingerprint"],
                     "symbol": "DEMO", "side": "buy", "status": status}
            self.assertEqual(len(size.waiting_buys({"orders": [approve, later]}, day)), n, status)
        others = [{"at": "2026-10-29T10:00:00+05:00", "date": day, "status": s, "symbol": "DEMO"}
                  for s in ("bonus", "cash", "stop")]
        self.assertEqual(len(size.waiting_buys({"orders": [approve] + others}, day)), 1)
        self.assertEqual(size.waiting_buys({}, day), [])
        self.assertTrue(size.has_buys(contract("broker-placed-unfilled.json")))
        self.assertFalse(size.has_buys(contract("broker-approved-old.json")))
        self.assertFalse(size.has_buys(contract("broker-protect-open.json")))
        self.assertFalse(size.has_buys({"orders": []}))

    def test_a_buy_filled_in_part_keeps_the_rest_waiting(self):
        # review F60: one fill used to end the whole buy here, so the rest, which may still fill today, looked like
        # free cash. Now the rest waits, the way psx-risk counts it (check.py fill_today): each fill of today goes to
        # the oldest open buy of its stock first, and only to a buy whose first record is not after the fill
        day, fp = TODAY, "0d80ffbb0d898a06"
        buy = contract("broker-placed-unfilled.json")["orders"]       # DEMO 1,436 at 99.38: yes 09:20, placed 09:22

        def fill(n, at="09:40", on=day, **more):
            r = dict({"at": "%sT%s:00+05:00" % (on, at), "date": on, "status": "filled", "symbol": "DEMO",
                      "side": "buy", "qty": n, "price": 99.38}, **more)
            return dict((k, v) for k, v in r.items() if v is not None)

        def rec(status, at, f, **more):
            return dict({"at": "%sT%s:00+05:00" % (day, at), "date": day, "fingerprint": f, "symbol": "DEMO",
                         "side": "buy", "status": status}, **more)
        part = {"orders": buy + [fill(436)]}
        rest = [{"symbol": "DEMO", "qty": 1000, "entry_ref": 99.38, "fingerprint": fp, "status": "placed",
                 "filled": 436, "order_qty": 1436}]
        self.assertEqual(size.waiting_buys(part, day), rest)
        cash = size.waiting_cash(rest, size.COSTS)
        self.assertEqual((cash["pkr"], cash["value"], cash["by_symbol"]), (99557.99, 99380.0, {"DEMO": 99380.0}))
        self.assertEqual((cash["rows"][0]["filled"], cash["rows"][0]["order_qty"]), (436, 1436))
        whole = [{"symbol": "DEMO", "qty": 1436, "entry_ref": 99.38, "fingerprint": fp, "status": "placed"}]
        a, b = rec("approved", "10:00", "fpB", qty=100, entry_ref=99.0), rec("approved", "10:05", "fpC", qty=50,
                                                                               entry_ref=99.5)
        for orders, want in (
                (buy + [fill(436), fill(1000, at="10:30")], []),                 # the rest filled: nothing waits
                (buy + [fill(2000)], []),                                        # more than the order: done
                (buy + [fill(436, at="09:10")], whole),                          # before its first record: not its fill
                (buy + [fill(436, on="2026-10-28")], whole),                     # a fill of another day
                (buy + [fill(436, side="sell")], whole),                         # a sell is no fill of a buy
                (buy + [fill(None)], []),                                        # no shares in the fill: the old rule
                (buy + [rec("expired", "09:30", fp, of="2026-10-28")], whole),   # it closes the order of 10-28
                (buy + [a, b, fill(1460, at="10:30")],                           # the oldest buy first: 1,436 + 24
                 [{"symbol": "DEMO", "qty": 76, "entry_ref": 99.0, "fingerprint": "fpB", "status": "approved",
                   "filled": 24, "order_qty": 100},
                  {"symbol": "DEMO", "qty": 50, "entry_ref": 99.5, "fingerprint": "fpC", "status": "approved"}]),
                (buy + [a, b, fill(30, at="10:02")],                             # fpC is from 10:05: not its fill
                 [dict(whole[0], qty=1406, filled=30, order_qty=1436),
                  {"symbol": "DEMO", "qty": 100, "entry_ref": 99.0, "fingerprint": "fpB", "status": "approved"},
                  {"symbol": "DEMO", "qty": 50, "entry_ref": 99.5, "fingerprint": "fpC", "status": "approved"}]),
                ([rec("approved", "09:20", "fpN", entry_ref=99.38), fill(10)], [])):      # no qty: any fill ends it
            self.assertEqual(size.waiting_buys({"orders": orders}, day), want, orders[len(buy):])
        # the same buys and the same cash as psx-risk, so an order sized here meets the same cash check there
        check = load("psx-risk", "check")
        logs = [buy, part["orders"], buy + [fill(436, at="09:10")], buy + [a, b, fill(1460, at="10:30")],
                buy + [a, b, fill(30, at="10:02")], buy + [rec("expired", "09:30", fp, of="2026-10-28")]]
        for orders in logs:
            mine = [(w["fingerprint"], w["qty"], r["pkr"]) for w, r in
                    zip(size.waiting_buys({"orders": orders}, day),
                        size.waiting_cash(size.waiting_buys({"orders": orders}, day), size.COSTS)["rows"])]
            theirs = [(o["fingerprint"], check.waiting_qty(o), check.waiting_value(o, check.COSTS))
                      for o in check.open_orders({"orders": orders}, day)[0]]
            self.assertEqual(mine, theirs, orders[len(buy):])
        # the CLI: 436 DEMO filled and in the ledger, the other 1,000 still waiting, then a SYS plan is sized
        pf = self.path("pf.json", {"cash": 456000.0, "positions": [{"ticker": "DEMO", "shares": 436, "lots": [
            {"id": "DEMO-1", "date": day, "shares": 436, "cost": 43329.68}]}]})
        st, p = self.path("part.json", part), self.path("sys.json", swing_plan(symbol="SYS"))
        s = self.sized("--now", "2026-10-29 09:45", portfolio=pf, plan=p, state=st)["sizing"]
        self.assertEqual((s["account_cash"], s["holdings_value"], s["account"]), (356442.01, 142709.68, 499151.69))
        self.assertEqual(s["cash_available"], 306526.84)                  # 356,442.01 less the 10% buffer 49,915.17
        code, text, err = run(self.sargs("--now", "2026-10-29 09:45", portfolio=pf, plan=p, state=st))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("Waiting buys today with no fill yet (psx/state/broker.json): 1,000 DEMO at 99.38 (Rs 99,557.99 "
                      "with costs; 436 of the 1,436 are filled). They come off the cash and count as held until they "
                      "fill or end.", text)
        check_words(self, text)

    def test_a_bad_broker_log_is_a_plain_error(self):
        bad = os.path.join(self.tmp, "broken.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        for state in (bad, self.path("list.json", [1, 2])):
            code, _, err = run(self.sargs("--now", SWING_NOW, state=state))
            self.assertEqual(code, 1)
            self.assertNotIn("Traceback", err)


class SwingWords(SwingCase):
    """Every new line passes the shared word test (tests/_words.py, D60), and SKILL.md shows the real output."""

    PLAIN = (r"\$", r"\blive\b", r"real[ -]time", r"guarantee", r"profit", r"\bP&L\b", chr(0x2014))

    def texts(self):
        out = []
        pf = os.path.join(V2, "portfolio-swing.json")
        p30 = self.path("plan30.json", swing_plan(date="2026-10-30"))
        today = self.path("today.json", moved(contract("broker-placed-unfilled.json"), "2026-10-30"))
        noref = moved(contract("broker-approved-old.json"), "2026-10-30")
        del noref["orders"][0]["entry_ref"]
        part = moved(contract("broker-placed-unfilled.json"), "2026-10-30")       # review F60: 436 of 1,436 filled
        part["orders"].append({"at": "2026-10-30T09:40:00+05:00", "date": "2026-10-30", "status": "filled",
                               "symbol": "DEMO", "side": "buy", "qty": 436, "price": 99.38})
        part_pf = {"cash": 456000.0, "positions": [{"ticker": "DEMO", "shares": 436, "lots": [
            {"id": "DEMO-1", "date": "2026-10-30", "shares": 436, "cost": 43329.68}]}]}
        for argv in (self.sargs("--now", "2026-10-30 09:45", plan=self.path("sys30.json", swing_plan(
                         symbol="SYS", date="2026-10-30")), state=self.path("part.json", part),
                         portfolio=self.path("part-pf.json", part_pf)),
                     self.sargs("--now", SWING_NOW), self.sargs("--now", EVENING),
                     self.sargs("--now", EVENING, "--write", os.path.join(self.tmp, "o.json")),
                     self.sargs("--now", SWING_NOW, portfolio=pf),
                     self.sargs("--now", "2026-10-30 09:20", plan=p30, state=today,
                                portfolio=self.path("pf.json", {"cash": 500000, "positions": []})),
                     self.sargs("--now", "2026-10-30 09:20", plan=p30, state=today),
                     self.sargs("--now", "2026-10-30 09:20", plan=p30, state=self.path("noref.json", noref)),
                     self.sargs("--now", SWING_NOW, config=self.path("day.json", cfg())),
                     self.sargs("--now", NOW, plan=PLAN),
                     self.sargs("--now", "2026-10-30 10:30", plan=PLAN, portfolio=self.path("book.json", BOOK)),
                     self.sargs("--now", SWING_NOW, plan=self.path("inv.json", swing_plan(style="invest"))),
                     self.sargs("--now", SWING_NOW, plan=self.path("noex.json", swing_plan(exit_by=None)))):
            code, text, err = run(argv)
            out += [text, err]
        for p, c in ((swing_plan(stop=98.38, target=101.38), swing_cfg()),
                     (swing_plan(target=110.0, stop=88.0), swing_cfg(min_reward_risk=0)),
                     (swing_plan(target=108.0, stop=88.56), swing_cfg(min_reward_risk=0)),     # review F39
                     (swing_plan(), swing_cfg(swing={"max_gap_loss_pkr": 0})),                # review F22
                     (swing_plan(limits=None), swing_cfg()),
                     (swing_plan(), swing_cfg(swing={"max_gap_loss_pkr": 5})),
                     (swing_plan(), swing_cfg(max_loss_per_trade_pkr=None))):
            s = size.size_trade(p, c, today=TODAY)
            out.append(size.plan_report(s, size.make_order(s)))
            if size.make_order(s):
                out.append(size.make_order(s)["why"])
        return out

    def test_every_new_line_passes_the_word_rules(self):
        texts = self.texts()
        checked = sum(check_words(self, t) for t in texts)
        self.assertGreater(checked, 150)
        joined = "\n".join(texts)
        for bad in self.PLAIN:
            self.assertIsNone(re.search(bad, joined, re.I), bad)
        for line in ("Styles do not mix", "A swing account needs its ledger", "--write is refused for a preview",
                     "has no price in the log", "no exit-by day", "invest.py orders", "436 of the 1,436 are filled",
                     "is at the lower price limit 88.56", "more than your Rs 0 gap limit"):
            self.assertIn(line, joined)
        self.assertNotIn("Traceback", joined)

    def test_skill_md_shows_the_swing_example(self):
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn("Sizes a day trade or a\n  swing trade to your budget", doc)
        shown = [l for l in run(self.sargs("--now", SWING_NOW))[1].splitlines() if l.strip()]
        self.assertGreater(len(shown), 25)
        s = size.size_trade(swing_plan(stop=98.38, target=101.38), swing_cfg(), today=TODAY)
        r500 = size.size_trade(swing_plan(), swing_cfg(max_loss_per_trade_pkr=500), today=TODAY)
        extra = ([l for l in size.plan_report(s, size.make_order(s)).splitlines() if "bound by your gap limit" in l]
                 + [l for l in size.plan_report(r500, size.make_order(r500)).splitlines() if l.startswith("Loss if")]
                 + [l for l in run(self.sargs("--now", EVENING))[1].splitlines() if l.startswith("Preview for")])
        self.assertEqual(len(extra), 3)
        for line in shown + extra:
            self.assertTrue(line + "\n" in doc, "SKILL.md lacks the line: %s" % line)
        blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", doc, re.S)]
        self.assertIn(contract("orders-swing.json")["orders"][0], blocks)
        with open(ORDERS) as fh:
            self.assertIn(json.load(fh)["orders"][0], blocks)                  # the day example stays too
        for word in ("## Swing style", "Styles do not mix", "bound by your", "Preview for", "psx/state/broker.json",
                     "max_gap_loss_pkr", "Swing trading loses money for many people too.", "paper first"):
            self.assertIn(word, doc)

    def test_skill_md_says_when_to_switch_and_where_to_try_paper(self):
        # review F07: a day trade, or a share bought before the switch, has no swing plan, so the swing tools do not
        # watch it. Review F06: paper turned on in a folder of real shares can mix paper fills into the real ledger
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            doc = " ".join(fh.read().split())
        self.assertNotIn("Switch only when no day position is open and no order is waiting.", doc)
        for words in ("Switch only when no order is waiting and you hold no share without a swing plan. A day trade "
                      "has no plan. A share you bought before the switch has none either. Such a share is watched "
                      "only by its own rules in `psx/alerts.json`, and only with today's prices, never a closing file",
                      "Never turn paper on in a folder that already holds real shares or real cash.",
                      'do not copy `psx/portfolio.json`. Then the paper cash starts from `"budget"`',
                      'put `"real_holdings": true` at the top of its `psx/portfolio.json`: then psx-broker refuses a '
                      "paper fill into it.",
                      "Try swing trades on paper first, in a new paper folder (see Step 1). Never turn paper on in a "
                      "folder that holds real shares or real cash."):
            self.assertIn(words, doc)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        for head in ("If you already hold shares", "Paper and real shares"):      # the README parts it points to
            self.assertIn('README "%s"' % head, doc)
            self.assertIn("\n### %s\n" % head, readme)

    def test_skill_md_result_lines_run_as_written(self):
        # F87: each `ticket.py result ...` line in SKILL.md names its order, so it runs as written. Review F61: by its
        # fingerprint, never --index: that is a number in psx/orders.json, which holds only the newest order sized,
        # so it can close another order. Each line closes the contract swing buy that did not fill (placed, no fill),
        # after the close of its buy day and the next morning.
        ticket = load("psx-broker", "ticket")
        with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
            doc = fh.read()
        lines = re.findall(r"`ticket\.py (result [^`]*)`", doc)
        self.assertTrue(lines)
        for line in lines:
            self.assertIn("--fingerprint FP", line)
            self.assertNotIn("--index", line)
        self.assertIsNone(re.search(r"ticket\.py result --index", doc))
        log = contract("broker-placed-unfilled.json")
        fp = log["orders"][-1]["fingerprint"]
        here = os.getcwd()
        for line, now in [(l, n) for l in lines for n in ("2026-10-29 15:45", "2026-10-30 09:06")]:
            argv = line.replace("FP", fp).split()
            argv += ["--now", now]
            d = tempfile.mkdtemp(dir=self.tmp)
            os.makedirs(os.path.join(d, "psx", "state"))
            write_json(os.path.join(d, "psx", "orders.json"), contract("orders-swing.json"))
            write_json(os.path.join(d, "psx", "config.json"), swing_cfg())
            write_json(os.path.join(d, "psx", "state", "broker.json"), log)
            out, err = io.StringIO(), io.StringIO()
            os.chdir(d)
            try:
                with redirect_stdout(out), redirect_stderr(err):
                    code = ticket.main(argv)
            except SystemExit as e:                  # argparse refused the line as written
                code = "exit %s" % e.code
            finally:
                os.chdir(here)
            self.assertEqual((code, err.getvalue()), (0, ""), (line, now))
            with open(os.path.join(d, "psx", "state", "broker.json")) as fh:
                last = json.load(fh)["orders"][-1]
            self.assertEqual((last["status"], last["fingerprint"], last.get("of")),
                             (argv[argv.index("--status") + 1], fp, "2026-10-29"), (line, now))


if __name__ == "__main__":
    unittest.main()
