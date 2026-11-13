import copy
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
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _load import load, ROOT, FIX
from _words import check_words

check = load("psx-risk", "check")

RISK = os.path.join(FIX, "psx-risk")
MARKET = os.path.join(FIX, "psx-market")        # the made-up sample market (read only)
CFG_PATH = os.path.join(RISK, "config.json")    # SPEC section 6, psx-budget/config.example.json
DEMO_PATH = os.path.join(RISK, "orders-demo.json")
FLAT_PATH = os.path.join(RISK, "orders-flatten.json")
PF_PATH = os.path.join(RISK, "portfolio-filled.json")
STATE_PATH = os.path.join(RISK, "broker-placed.json")
P1052 = os.path.join(RISK, "prices-1052.json")
P1520 = os.path.join(RISK, "prices-1520.json")
UNIVERSE = os.path.join(RISK, "universe.txt")
NOW = "2026-10-29 10:15"
TODAY = "2026-10-29"
QUOTE_TIME = datetime.datetime(2026, 10, 29, 5, 52)    # DEMO 101.30 as of 10:52 Karachi time (05:52 UTC)
# long-term rules with every day-trade check switched off: the account checks still hold
V1 = {"budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_pkr": 200000,
      "min_order_pkr": 5000, "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None,
      "no_entries_after": None, "max_trades_per_day": 0, "max_daily_loss_pkr": 0, "max_loss_per_trade_pkr": 0,
      "min_reward_risk": 0}
MKT = {}


def no_network(*args, **kwargs):
    raise AssertionError("a test tried to use the network")


def setUpModule():
    check.SLEEP = lambda s: None            # never wait in a test
    check.http_get = no_network             # never touch the network in a test
    MKT.update(market())


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def v1(**over):
    c = dict(V1)
    c.update(over)
    return c


def demo(**over):
    with open(DEMO_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def read(path):
    with open(path) as fh:
        return json.load(fh)


def market(today=TODAY, symbols=("DEMO",), root=MARKET, universe=UNIVERSE, config=None, **kw):
    return check.market_facts(list(symbols), today, root, config or {}, True, universe_file=universe, **kw)


def limit_buy(sym, qty, price, stop=None, target=None):
    o = {"symbol": sym, "side": "buy", "qty": qty, "type": "limit", "limit_price": price, "time_in_force": "day",
         "board": "REG"}
    if stop is not None:
        o["stop_loss"] = stop
    if target is not None:
        o["take_profit"] = target
    return o


def placed(sym, side="buy", day=TODAY, max_loss=4999.94, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T10:20:00+05:00"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check.main(args)
    return code, out.getvalue(), err.getvalue()


class TheExample(unittest.TestCase):
    """EXAMPLE.md section 8: DEMO on Thu 2026-10-29, a fresh Rs 500,000 paper account."""

    def test_the_demo_order_passes_every_check(self):
        res = check.check([demo()], cfg(), now=at(NOW), market=MKT)
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["reasons"], [])
        self.assertEqual(len(r["warnings"]), 2, r["warnings"])
        self.assertEqual(r["warnings"][0], "Costs are about 33% of the price risk (over 25%). You pay them win or lose.")
        self.assertIn("live only in the plan (PSX has no stop or bracket order)", r["warnings"][1])
        self.assertEqual((r["kind"], r["entry_ref"], r["entry_ref_from"], r["stop"], r["target"]),
                         ("entry", 100.65, "limit", 99.55, 101.75))
        self.assertEqual((r["risk_per_share"], r["max_loss"], r["costs"], r["reward_risk"]),
                         (1.1, 4999.94, 1229.14, 1.0))
        self.assertEqual(r["value_pkr"], 345028.2)
        self.assertEqual((r["limits"]["lower"], r["limits"]["upper"], r["limits"]["from"]),
                         (88.56, 108.24, "company page"))
        self.assertEqual(r["fingerprint"], "e9d01b16c66c4a20")
        self.assertEqual(res["cash_after"], 154353.85)      # 500,000 - 345,028.20 - 617.95 buy costs
        self.assertEqual((res["date"], res["now_karachi"]), (TODAY, "2026-10-29 10:15"))
        self.assertIs(res["paper"], True)
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["notes"], [check.HALT_NOTE])

    def test_one_more_share_is_blocked_by_the_max_loss_only(self):
        res = check.check([demo(qty=3429)], cfg(), now=at(NOW), market=MKT)
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 3429 shares x 1.10 = Rs 3,771.90 at the stop + Rs 1,229.50 costs = Rs 5,001.40, "
                          "over your Rs 5,000 max loss per trade (costs included)"])
        self.assertEqual(res["notes"], [])

    def test_at_0940_the_window_blocks_it(self):
        res = check.check([demo()], cfg(), now=at("2026-10-29 09:40"), market=MKT)
        self.assertEqual(res["results"][0]["reasons"],
                         ["window: no new entries before 09:45 Karachi time (it is 09:40)"])

    def test_the_1520_flatten_sell_passes_as_an_exit(self):
        prices, times, details = check.load_prices(read(P1520))
        mk = market(details=details, times=times)
        res = check.check(read(FLAT_PATH)["orders"], cfg(), portfolio=read(PF_PATH), prices=prices,
                          state=read(STATE_PATH), now=at("2026-10-29 15:20"), price_times=times, market=mk)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["reasons"], r["warnings"]), ("PASS", "exit", [], []))
        self.assertEqual(r["fingerprint"], "2f046115ed2a3498")
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["value_pkr"]), (101.58, "price", 348216.24))
        self.assertIn("sell costs about Rs 623.66", r["info"][0])
        self.assertEqual(r["limits"]["from"], "prices file")
        self.assertEqual(res["cash_after"], 501946.43)       # 154,353.85 + 348,216.24 - 623.66
        self.assertEqual(res["today"], {"orders_placed": 1, "entries_placed": 1, "loss_at_risk": 4999.94})

    def test_the_1052_quote_blocks_the_same_shares_as_a_market_order(self):
        o = demo(type="market", limit_price=None)
        res = check.check([o], cfg(), prices={"DEMO": 101.3}, now=at("2026-10-29 10:55"),
                          price_times={"DEMO": QUOTE_TIME}, market=MKT)
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"]), (101.3, "price", 1.75))
        self.assertEqual(r["reward_risk"], 0.26)
        why = reasons(res)
        self.assertIn("loss: 3428 shares x 1.75 = Rs 5,999 at the stop + Rs 1,233.13 costs = Rs 7,232.13", why)
        self.assertIn("reward:risk: 0.26 (target 101.75, entry 101.30, stop 99.55) is under your minimum 1", why)
        self.assertIn("DEMO price 101.30 is above the plan's entry 100.65", r["info"][0])

    def test_fingerprint_and_the_order_fields(self):
        self.assertEqual(check.ORDER_FIELDS, ("symbol", "side", "qty", "type", "limit_price", "time_in_force",
                                              "board", "stop_loss", "take_profit", "trigger_price"))
        o = demo()
        self.assertEqual(check.fingerprint(o), "e9d01b16c66c4a20")
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, qty=3429)), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop_loss=99.5)), check.fingerprint(o))
        self.assertEqual(check.fingerprint(read(FLAT_PATH)["orders"][0]), "2f046115ed2a3498")

    def test_the_broker_makes_the_same_fingerprint(self):
        try:
            ticket = load("psx-broker", "ticket")
        except Exception as e:                  # another skill, built in parallel
            self.skipTest("psx-broker/ticket.py does not load: %s" % e)
        if not hasattr(ticket, "ORDER_FIELDS"):
            self.skipTest("psx-broker/ticket.py has no ORDER_FIELDS yet")
        self.assertEqual(ticket.ORDER_FIELDS, check.ORDER_FIELDS)
        self.assertEqual(ticket.fingerprint(demo()), check.fingerprint(demo()))


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, mkt=True, **over):
        return check.check([order], cfg(**over), now=at(now), market=MKT if mkt else None)

    def test_a_stop_is_required(self):
        o = demo()
        for k in ("stop_loss", "take_profit"):
            o.pop(k)
        o["plan"] = {"entry": 100.65}
        self.assertIn("stop: no stop", reasons(self.one(o)))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop: the max loss is not capped", warnings(res))

    def test_a_plan_stop_counts_but_lives_only_in_the_plan(self):
        o = demo()
        for k in ("stop_loss", "take_profit"):
            o.pop(k)
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["results"][0]["max_loss"], 4999.94)
        self.assertIn("the stop 99.55 and the target 101.75 live only in the plan", warnings(res))
        self.assertIn("psx-alerts watches both", warnings(res))

    def test_the_order_stop_wins_over_the_plan_stop(self):
        res = self.one(demo(stop_loss=99.65))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["stop"], r["risk_per_share"]), ("PASS", 99.65, 1.0))
        self.assertEqual(r["max_loss"], check.max_loss_at_stop(100.65, 99.65, 3428))
        self.assertIn("the order's stop_loss 99.65 differs from the plan's stop 99.55", warnings(res))

    def test_stop_and_target_on_the_right_side(self):
        self.assertIn("stop: the stop 100.70 must be below the entry 100.65", reasons(self.one(demo(stop_loss=100.7))))
        res = self.one(demo(take_profit=100.6))
        self.assertIn("target: the target 100.60 must be above the entry 100.65", reasons(res))

    def test_reward_risk(self):
        res = self.one(demo(take_profit=101.4))
        self.assertEqual(res["results"][0]["reasons"],
                         ["reward:risk: 0.68 (target 101.40, entry 100.65, stop 99.55) is under your minimum 1"])
        self.assertEqual(verdicts(self.one(demo(take_profit=101.4), min_reward_risk=0.5)), ["PASS"])

    def test_a_stale_or_future_price_never_measures_a_market_buy(self):
        # a market buy with no fresh price is blocked: the plan's entry alone can be hours old
        o = demo(type="market", limit_price=None)
        for now in ("2026-10-29 11:30", "2026-10-29 10:15"):     # 38 minutes old; 37 minutes ahead
            res = check.check([o], cfg(), prices={"DEMO": 101.3}, now=at(now), price_times={"DEMO": QUOTE_TIME},
                              market=MKT)
            self.assertEqual(verdicts(res), ["BLOCK"], now)
            self.assertIsNone(res["results"][0]["entry_ref"])
            self.assertIn("price: a market order needs a fresh price to measure its risk, and the plan's entry 100.65 "
                          "can be hours old (the DEMO price in psx/prices.json is not fresh", reasons(res))
        res = check.check([demo()], cfg(), prices={"DEMO": 101.3}, now=at("2026-10-29 11:30"),
                          price_times={"DEMO": QUOTE_TIME}, market=MKT)
        self.assertEqual(verdicts(res), ["PASS"])                  # a limit order is measured from its limit

    def test_a_lower_price_does_not_shrink_the_risk(self):
        o = demo(type="market", limit_price=None)
        res = check.check([o], cfg(), prices={"DEMO": 100.0}, now=at(NOW), market=MKT)
        self.assertEqual((res["results"][0]["entry_ref"], res["results"][0]["entry_ref_from"]), (100.65, "plan"))

    def test_a_limit_order_uses_its_limit_price(self):
        res = check.check([demo(limit_price=100.5)], cfg(), prices={"DEMO": 101.3}, now=at(NOW), market=MKT)
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["max_loss"], r["verdict"]), (100.5, 4484.81, "PASS"))

    def test_costs_are_in_the_max_loss(self):
        zero = {"commission_pct": 0, "commission_min_per_share": 0, "sales_tax_pct": 15, "other_charges_pct": 0}
        r = self.one(demo(), costs=zero)["results"][0]
        self.assertEqual((r["max_loss"], r["costs"]), (3770.8, 0.0))
        one_side = dict(check.COSTS, day_trade_one_side=True)
        r = self.one(demo(), costs=one_side)["results"][0]
        self.assertEqual((r["max_loss"], r["costs"]), (4411.27, 640.47))

    def test_costs_over_a_quarter_of_the_price_risk_warn(self):
        # one rule in psx-exit, psx-budget and psx-risk: costs_vs_risk over COST_WARN, the price risk as the base
        self.assertEqual(check.COST_WARN, 0.25)
        res = self.one(demo())                                  # 1,229.14 / 3,770.80 = 32.6%
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("Costs are about 33% of the price risk (over 25%). You pay them win or lose.", warnings(res))
        res = self.one(demo(stop_loss=100.15))                  # 1,232.83 / 1,714 = 71.9%
        self.assertIn("Costs are about 72% of the price risk (over 25%).", warnings(res))
        res = self.one(demo(qty=2000, stop_loss=98.65, take_profit=102.65))      # 713.89 / 4,000 = 17.8%
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertNotIn("Costs are about", warnings(res))

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("OGDC"), placed("HBL")]}
        res = check.check([demo()], cfg(), state=two, now=at(NOW), market=MKT)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("today before this: 2 of 3 trades, Rs 9,999.88 of Rs 15,000 daily loss",
                      res["results"][0]["info"])
        three = {"orders": [placed("OGDC"), placed("HBL"), placed("FFC")]}
        why = reasons(check.check([demo()], cfg(), state=three, now=at(NOW), market=MKT))
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: Rs 14,999.82 already at risk today plus Rs 4,999.94 for this one = "
                      "Rs 19,999.76, over your Rs 15,000 daily loss limit (costs included)", why)
        yesterday = {"orders": [placed(s, day="2026-10-28") for s in ("OGDC", "HBL", "FFC")]}
        self.assertEqual(verdicts(check.check([demo()], cfg(), state=yesterday, now=at(NOW), market=MKT)), ["PASS"])

    def test_exits_and_declines_do_not_count_as_trades(self):
        st = {"orders": [placed("OGDC"), placed("OGDC", side="sell", kind="exit", max_loss=None),
                         placed("HBL", status="declined"), placed("FFC", status="failed")]}
        res = check.check([demo()], cfg(), state=st, now=at(NOW), market=MKT)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["today"]["entries_placed"], 1)
        self.assertEqual(res["today"]["orders_placed"], 2)

    def test_the_batch_counts_toward_the_daily_limits(self):
        orders = [limit_buy(s, 100, 60.0, 20.0, 100.0) for s in ("AAA", "BBB", "CCC", "DDD")]
        res = self.one(orders[0], mkt=False)
        self.assertEqual(res["results"][0]["max_loss"], 4014.33)          # 4,000 price risk + 14.33 costs
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        why = reasons(res, 3)
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", why)
        self.assertIn("daily loss: Rs 12,042.99 already at risk today plus Rs 4,014.33 for this one = Rs 16,057.32",
                      why)

    def test_cash_after_includes_the_buy_costs(self):
        res = check.check([limit_buy("OGDC", 1601, 281.0)], v1(max_order_pkr=0, max_position_pct=100), now=at(NOW))
        self.assertIn("cash: leaves Rs 49,313.26 cash after costs, under your 10% buffer of Rs 50,000", reasons(res))
        res = check.check([limit_buy("OGDC", 1601, 281.0)], v1(max_order_pkr=0, max_position_pct=100,
                                                                 costs={"commission_pct": 0, "sales_tax_pct": 0,
                                                                        "commission_min_per_share": 0,
                                                                        "other_charges_pct": 0}), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])                       # 50,119 left without costs


class Window(unittest.TestCase):
    """The entry window: config_time for the config times, market_state for the session."""

    def one(self, now, **over):
        return check.check([demo()], cfg(**over), now=at(now))

    def blocked(self, now, want, **over):
        res = self.one(now, **over)
        self.assertEqual(verdicts(res), ["BLOCK"], now)
        why = [x for x in res["results"][0]["reasons"] if x.startswith("window: ")]
        self.assertEqual(len(why), 1, (now, res["results"][0]["reasons"]))
        self.assertIn(want, why[0], now)

    def test_a_normal_day_in_karachi_time(self):
        self.blocked("2026-10-29 09:44", "no new entries before 09:45 Karachi time (it is 09:44)")
        self.blocked("2026-10-29 15:01", "no new entries after 15:00 Karachi time (it is 15:01)")
        self.blocked("2026-10-29 09:10", "no new entries now (Thu 2026-10-29 09:10 Karachi time): closed: the "
                                         "pre-open starts at 9:15 AM PKT")
        self.blocked("2026-10-29 09:20", "pre-open: limit orders only, nothing trades until the opening match")
        self.blocked("2026-10-29 09:31", "the opening match runs until 9:32 AM PKT, so PSX takes no new orders yet")
        self.blocked("2026-10-29 15:30", "closed: the session ended at 3:30 PM PKT")
        self.blocked("2026-10-29 15:36", "post-close session: trades only at the closing price")
        self.blocked("2026-10-29 16:00", "closed for the day")
        for now in ("2026-10-29 09:45", "2026-10-29 10:15", "2026-10-29 15:00"):
            self.assertEqual(verdicts(self.one(now)), ["PASS"], now)

    def test_weekends_and_psx_holidays_are_closed(self):
        self.blocked("2026-10-31 10:15", "no new entries now (Sat 2026-10-31 10:15 Karachi time): closed all day "
                                         "(weekend)")
        self.blocked("2026-11-09 10:15", "closed all day (PSX holiday: Iqbal Day)")
        self.blocked("2026-10-29 10:15", "closed all day (PSX holiday: Market closed)",
                     holidays={"2026-10-29": "Market closed"})
        self.assertEqual(verdicts(self.one("2026-11-09 10:15", open_days=["2026-11-09"])), ["PASS"])

    def test_friday_hours_and_the_juma_break(self):
        self.blocked("2026-10-30 09:29", "no new entries before 09:30 Karachi time (it is 09:29)")
        self.blocked("2026-10-30 09:16", "the opening match runs until 9:17 AM PKT")
        self.blocked("2026-10-30 12:30", "Juma break: the pre-open starts at 2:15 PM PKT")
        self.blocked("2026-10-30 14:31", "the opening match runs until 2:32 PM PKT")
        self.blocked("2026-10-30 16:01", "no new entries after 16:00 Karachi time (it is 16:01)")
        for now in ("2026-10-30 09:30", "2026-10-30 11:20", "2026-10-30 15:30", "2026-10-30 16:00"):
            res = self.one(now)
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertNotIn("Juma break", warnings(res), now)
        res = self.one("2026-10-30 11:45")
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("the Juma break starts at 12:00 PM PKT, in 15 minutes: a position you open now sits through "
                      "the break, and the second session starts at 2:30 PM PKT", warnings(res))

    def test_ramadan_hours(self):
        self.blocked("2026-02-23 09:29", "no new entries before 09:30 Karachi time (it is 09:29)")
        self.blocked("2026-02-23 13:01", "no new entries after 13:00 Karachi time (it is 13:01)")
        self.blocked("2026-02-23 14:00", "closed for the day: the session ended at 1:30 PM PKT")
        self.assertEqual(verdicts(self.one("2026-02-23 13:00")), ["PASS"])
        self.blocked("2026-10-29 13:05", "no new entries after 13:00 Karachi time (it is 13:05)",
                     ramadan={"start": "2026-10-20", "end": "2026-10-29"})    # a Ramadan date set in the config
        self.blocked("2026-10-29 11:50", "no new entries after 11:45 Karachi time (it is 11:50)",
                     ramadan={"start": "2026-10-20", "end": "2026-10-29"}, no_entries_after="12:00")

    def test_the_window_uses_karachi_time_not_the_machine(self):
        self.assertEqual(at("2026-10-29 10:15"), datetime.datetime(2026, 10, 29, 5, 15))
        self.assertEqual(at("2026-10-29T10:15:00+05:00"), datetime.datetime(2026, 10, 29, 5, 15))
        self.assertEqual(verdicts(self.one("2026-10-29T05:15:00Z")), ["PASS"])
        self.assertIn("(it is 09:40)", reasons(self.one("2026-10-29T04:40:00Z")))
        if not hasattr(time, "tzset"):
            return
        old = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "America/Los_Angeles"
            time.tzset()
            res = self.one(NOW)
            self.assertEqual((verdicts(res), res["now_karachi"]), (["PASS"], "2026-10-29 10:15"))
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()

    def test_null_times_switch_off_only_your_window_and_bad_times_are_errors(self):
        off = {"no_entries_before": None, "no_entries_after": None}
        for now in ("2026-10-29 09:40", "2026-10-29 15:10"):            # outside your times: they are off
            self.assertEqual(verdicts(self.one(now, **off)), ["PASS"], now)
        for now, want in (("2026-11-01 10:16", "closed all day (weekend)"),
                          ("2026-11-09 10:16", "closed all day (PSX holiday: Iqbal Day)"),
                          ("2026-10-29 03:00", "closed"), ("2026-10-29 09:20", "pre-open"),
                          ("2026-10-29 09:31", "the opening match runs until 9:32 AM PKT"),
                          ("2026-10-30 13:00", "Juma break")):
            self.blocked(now, want, **off)                              # the market must still be open
        self.blocked("2026-10-29 15:10", "no new entries after 15:00", no_entries_before=None)
        with self.assertRaises(check.RiskError):
            self.one(NOW, no_entries_before="9.45am")

    def test_a_market_order_in_the_pre_open_is_blocked_even_to_get_out(self):
        pf = read(PF_PATH)
        sell = read(FLAT_PATH)["orders"][0]
        res = check.check([sell], cfg(), portfolio=pf, now=at("2026-10-29 09:20"))
        self.assertEqual((verdicts(res), res["results"][0]["kind"]), (["BLOCK"], "exit"))
        self.assertIn("format: PSX takes no market orders in the pre-open (limit orders only)", reasons(res))
        lim = dict(sell, type="limit", limit_price=100.5)
        self.assertEqual(verdicts(check.check([lim], cfg(), portfolio=pf, now=at("2026-10-29 09:20"))), ["PASS"])
        res = check.check([sell], cfg(), portfolio=pf, now=at("2026-10-29 09:40"))
        self.assertEqual(verdicts(res), ["PASS"])

    def test_calendar_notes_for_expected_moon_dates(self):
        res = self.one("2027-03-09 10:15")
        self.assertEqual(verdicts(res), ["PASS"])                       # a Ramadan day: 09:30 to 13:00
        self.assertIn("check PSX notices", " ".join(res["warnings"]))
        self.assertEqual(self.one(NOW)["warnings"], [])


class PsxRules(unittest.TestCase):
    def one(self, order, mkt=None, **over):
        return check.check([order], cfg(**over), now=at(NOW), market=mkt)

    def test_format_rules(self):
        cases = [(dict(demo(), qty=8.5), "format: qty must be a whole number of shares above 0, got 8.5"),
                 (dict(demo(), qty=0), "qty must be a whole number"), (dict(demo(), qty=True), "qty must be"),
                 (dict(demo(), notional=345000), "PSX orders are in whole shares: give qty, not notional"),
                 (dict(demo(), type="stop"), "format: type must be limit or market, got 'stop'"),
                 (dict(demo(), time_in_force="gtc"), "PSX orders are day orders, so time_in_force must be day"),
                 (dict(demo(), board="ODL"), "format: board must be REG (the regular board), got 'ODL'"),
                 (dict(demo(), limit_price=None), "format: a limit order needs a limit_price"),
                 (dict(demo(), limit_price=100.655), "format: limit_price 100.655 is not on the Rs 0.01 tick"),
                 (dict(demo(), stop_loss=-1), "format: stop_loss must be a price above 0, got -1"),
                 (dict(demo(), order_class="bracket"), "PSX has no bracket or OCO orders"),
                 (dict(demo(), extended_hours=True), "PSX has no extended hours"),
                 (dict(demo(), side="hold"), "format: side must be buy or sell, got 'hold'"),
                 (dict(demo(), symbol=""), "format: no symbol")]
        for order, want in cases:
            self.assertIn(want, reasons(self.one(order)), want)
        self.assertEqual(verdicts(self.one(dict(demo(), qty="3428", board="reg"))), ["PASS"])

    def test_ordinary_shares_only(self):
        syms = ["OGDC-NOV", "KSE30-NOV", "MZNPETF", "KSE100", "P01GIS210127", "DCR", "BAPLR1", "EPCLPS", "GEMMEL",
                "ANLNV", "FEM", "BTC/USD"]
        mk = market(symbols=syms, universe=None)
        want = ["a stock futures contract", "an index futures contract", "an ETF", "an index, not something",
                "a debt security", "a REIT", "a rights letter", "a preference share", "a GEM board share",
                "a non-voting share", "a modaraba", "does not look like a PSX symbol"]
        for sym, w in zip(syms, want):
            why = reasons(self.one(limit_buy(sym, 100, 50.0, 49.0, 52.0), mk))
            self.assertIn("asset: %s " % sym, why)
            self.assertIn(w, why, sym)
        for sym, w in (("MZNPETF", "an ETF"), ("P01GIS210127", "a debt"), ("OGDC-DEC", "a stock futures")):
            self.assertIn(w, reasons(self.one(limit_buy(sym, 100, 50.0, 49.0, 52.0))), sym)    # no data folder

    def test_the_universe_file(self):
        mk = market(symbols=["DEMO", "ZZZZ"])
        self.assertEqual(verdicts(self.one(demo(), mk)), ["PASS"])
        why = reasons(self.one(demo(symbol="ZZZZ"), mk))
        self.assertIn("asset: ZZZZ is not in %s, your list of ordinary PSX shares" % UNIVERSE, why)
        with tempfile.TemporaryDirectory() as d:
            gone = os.path.join(d, "universe.txt")
            mk = market(symbols=["DEMO", "ZZZZ"], universe=gone)
            res = self.one(demo(), mk)
            self.assertEqual(verdicts(res), ["PASS"])
            self.assertIn("there is no %s, so the symbols were not checked against your universe" % gone,
                          " ".join(res["warnings"]))
            res = self.one(demo(symbol="ZZZZ", limit_price=50.0, stop_loss=49.0, take_profit=51.0), mk)
            self.assertIn("ZZZZ is in no closing file of the last 5 sessions", warnings(res))
            with open(gone, "w") as fh:
                fh.write("# my universe\n\nDEMO|Demo Industries Limited|0818\n")
            self.assertEqual(check.read_universe(gone), {"DEMO"})

    def test_a_suspended_share_is_blocked_and_other_tags_warn(self):
        with open(os.path.join(MARKET, "company", "DEMO.html"), encoding="utf-8") as fh:
            page = fh.read()
        name = '<div class="quote__name">Demo Industries Limited</div>'
        self.assertIn(name, page)
        prices, times, details = check.load_prices(read(P1052))
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "company"))
            for tag, verdict in (("SUSPENDED", "BLOCK"), ("NON-COMPLIANT", "PASS")):
                with open(os.path.join(d, "company", "DEMO.html"), "w", encoding="utf-8") as fh:
                    fh.write(page.replace(name, name[:-6] + '<div class="tag tag--skim">%s</div></div>' % tag))
                for mk in (market(root=d), market(root=d, details=details, times=times)):
                    res = self.one(demo(), mk)
                    self.assertEqual(verdicts(res), [verdict], tag)
                if tag == "SUSPENDED":
                    self.assertIn("asset: DEMO is SUSPENDED on the saved company page (as of Thu 2026-10-29 3:49 PM): "
                                  "PSX does not trade it now", reasons(res))
                    sell = check.check(read(FLAT_PATH)["orders"], cfg(), portfolio=read(PF_PATH), now=at(NOW),
                                       market=mk)
                    self.assertEqual(verdicts(sell), ["PASS"])
                    self.assertIn("exit, not blocked: asset: DEMO is SUSPENDED", warnings(sell))
                else:
                    self.assertIn("DEMO shows NON-COMPLIANT on the saved company page", warnings(res))

    def test_blank_sales_are_always_blocked(self):
        sell = {"symbol": "DEMO", "side": "sell", "qty": 10, "type": "limit", "limit_price": 100.65,
                "time_in_force": "day", "board": "REG"}
        for allow in (False, True):
            res = self.one(sell, allow_short=allow)
            self.assertEqual((verdicts(res), res["results"][0]["kind"]), (["BLOCK"], "entry"))
            self.assertIn("short: selling more DEMO than you hold (0 shares). PSX bans blank sales", reasons(res))
        self.assertIn("allow_short is on in psx/config.json, but this pack never uses it",
                      " ".join(self.one(sell, allow_short=True)["warnings"]))

    def test_no_margin_even_when_allowed(self):
        o = limit_buy("OGDC", 400, 281.0)
        for allow in (False, True):
            res = check.check([o], v1(cash_buffer_pct=0, max_position_pct=100, allow_margin=allow),
                              portfolio={"cash": 100000, "positions": []}, now=at(NOW))
            self.assertIn("that would be margin (borrowed money), and this pack never uses margin", reasons(res))

    def test_todays_price_limits_block_and_warn(self):
        res = self.one(demo(limit_price=108.3, stop_loss=107.2, take_profit=108.24), MKT)
        self.assertIn("limits: the limit price 108.30 is outside today's price limits 88.56 to 108.24 "
                      "(company page): PSX does not trade there today", reasons(res))
        res = self.one(demo(take_profit=108.5), MKT)
        self.assertEqual(res["results"][0]["reasons"], ["limits: the target 108.50 is above today's upper limit "
                                                        "108.24 (company page), so it cannot fill today"])
        res = self.one(demo(qty=100, stop_loss=88.56, take_profit=106.0), MKT, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("the stop 88.56 is at or below today's lower limit 88.56 (company page)", warnings(res))
        old_plan = dict(demo(type="market", limit_price=None, stop_loss=None, take_profit=None),
                        plan={"entry": 110.0, "stop": 109.0, "target": 111.0})
        res = check.check([old_plan], cfg(), prices={"DEMO": 100.0}, now=at(NOW), market=MKT)
        self.assertIn("limits: the plan's entry 110.00 is outside today's price limits", reasons(res))
        res = self.one(old_plan, MKT)                       # with no fresh price a market buy is not measured at all
        self.assertIn("price: a market order needs a fresh price", reasons(res))
        self.assertIn("today's price limits 88.56 to 108.24 (company page)", self.one(demo(), MKT)["results"][0]["info"])

    def test_where_todays_limits_come_from(self):
        prices, times, details = check.load_prices(read(P1052))
        lim = market(details=details, times=times)["limits"]["DEMO"]
        self.assertEqual((lim["lower"], lim["upper"], lim["from"], lim["label"]),
                         (88.56, 108.24, "prices file", "psx/prices.json"))
        lim = MKT["limits"]["DEMO"]                                          # the saved page, as of 10-29 3:49 PM
        self.assertEqual((lim["lower"], lim["upper"], lim["ldcp"], lim["from"]), (88.56, 108.24, 98.4, "company page"))
        lim = market(today="2026-10-30", details=details, times=times)["limits"]["DEMO"]   # yesterday's page and quote
        self.assertEqual((lim["lower"], lim["upper"], lim["from"]), (91.44, 111.76, "computed"))
        self.assertEqual(lim["label"], "computed from the 2026-10-29 close 101.60")
        self.assertIn("as of Thu 2026-10-29 3:49 PM, not today", lim["why"])

    def test_an_ex_date_makes_computed_limits_too_high(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "mkt_summary"))
            shutil.copy(os.path.join(MARKET, "mkt_summary", "2026-10-28.Z"), os.path.join(d, "mkt_summary"))
            mk = market(symbols=["HBL"], root=d)
            self.assertEqual((mk["limits"]["HBL"]["lower"], mk["limits"]["HBL"]["upper"]), (251.1, 306.9))
            res = self.one(limit_buy("HBL", 100, 275.0, 273.0, 279.0), mk)
            self.assertIn("today's price limits are computed from the 2026-10-28 close 279.00: on an ex-date PSX "
                          "lowers the base", warnings(res))
            os.makedirs(os.path.join(d, "company"))
            shutil.copy(os.path.join(MARKET, "company", "HBL.html"), os.path.join(d, "company"))
            lim = market(symbols=["HBL"], root=d)["limits"]["HBL"]      # HBL went ex on 10-29: LDCP 275.00
            self.assertEqual((lim["lower"], lim["upper"], lim["ldcp"], lim["from"]), (247.5, 302.5, 275.0, "company page"))

    def test_the_psx_site_is_used_only_when_it_is_on(self):
        with open(os.path.join(MARKET, "company", "DEMO.html"), "rb") as fh:
            page = fh.read()
        calls = []

        def site(url, timeout=30):
            calls.append(url)
            if url.endswith("/company/DEMO"):
                return page.replace(b"As of Thu, Oct 29, 2026 3:49 PM", b"As of Fri, Oct 30, 2026 10:10 AM")
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(check, "http_get", site):
            off = market(root=d)["unknown"]["DEMO"]
            self.assertIn("The PSX site is off for this tool: save https://dps.psx.com.pk/company/DEMO", off)
            on = {"psx_site": True}
            self.assertIn("--offline reads only the data folder", market(root=d, config=on)["unknown"]["DEMO"])
            self.assertEqual(calls, [])
            lim = check.market_facts(["DEMO"], "2026-10-30", d, on, False)["limits"]["DEMO"]
            self.assertEqual((lim["lower"], lim["from"], lim["label"]), (88.56, "company page", "company page, PSX site"))
            self.assertEqual(calls, ["https://dps.psx.com.pk/company/DEMO"])
            os.makedirs(os.path.join(d, "company"))
            shutil.copy(os.path.join(MARKET, "company", "DEMO.html"), os.path.join(d, "company"))
            lim = check.market_facts(["DEMO"], "2026-10-30", d, on, False)["limits"]["DEMO"]   # old saved page
            self.assertEqual((lim["from"], lim["label"], len(calls)), ("company page", "company page, PSX site", 2))
            res = check.check([demo()], cfg(), now=at("2026-10-30 10:15"), market=market(root=d, today="2026-10-30"))
            self.assertIn("today's price limits for DEMO are not known, so they were not checked", warnings(res))


class Exits(unittest.TestCase):
    """A sell that only cuts what you hold is an exit: only format, asset and blank sales block it."""

    def setUp(self):
        self.pf = read(PF_PATH)
        self.sell = read(FLAT_PATH)["orders"][0]

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("DEMO"), placed("OGDC"), placed("HBL")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("DEMO", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["DEMO"], max_order_pkr=100, max_daily_loss_pkr=50)
        res = check.check([self.sell], c, portfolio=self.pf, state=full, now=at("2026-10-29 15:28"), market=MKT)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, why)
        self.assertNotIn("price", why)
        res = check.check([self.sell], cfg(), portfolio=self.pf, now=at("2026-10-31 12:00"))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("the market is not open now: closed all day (weekend)", warnings(res))
        res = check.check([self.sell], cfg(), portfolio=self.pf, now=at("2026-10-30 12:30"))
        self.assertIn("the market is not open now: Juma break", warnings(res))

    def test_a_partial_exit_and_the_rest(self):
        half = dict(self.sell, qty=1714)
        res = check.check([half, half], cfg(), portfolio=self.pf, prices={"DEMO": 101.58}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([half, half, half], cfg(), portfolio=self.pf, prices={"DEMO": 101.58}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        res = check.check([dict(self.sell, type="limit")], cfg(), portfolio=self.pf, now=at(NOW))
        self.assertIn("needs a limit_price", reasons(res))
        res = check.check([dict(self.sell, qty=3429)], cfg(), portfolio=self.pf, now=at(NOW))
        self.assertIn("short: selling more DEMO than you hold (3428 shares)", reasons(res))
        res = check.check([self.sell], cfg(), now=at(NOW))
        self.assertIn("record the fill first (psx-broker: ticket.py fill)", reasons(res))
        pf = {"cash": 0, "positions": [{"symbol": "OGDC-NOV", "shares": 500}]}
        res = check.check([dict(self.sell, symbol="OGDC-NOV", qty=500)], cfg(), portfolio=pf, now=at(NOW))
        self.assertIn("asset: OGDC-NOV is a stock futures contract", reasons(res))

    def test_a_held_stock_outside_the_universe_can_still_be_sold(self):
        pf = {"cash": 1000, "positions": [{"symbol": "ZZZZ", "shares": 100, "lots": [{"shares": 100, "cost": 5000}]}]}
        mk = market(symbols=["ZZZZ"])
        res = check.check([dict(self.sell, symbol="ZZZZ", qty=100)], cfg(), portfolio=pf, now=at(NOW), market=mk)
        self.assertEqual((verdicts(res), res["results"][0]["kind"]), (["PASS"], "exit"))
        self.assertIn("exit, not blocked: asset: ZZZZ is not in", warnings(res))
        self.assertIn("no fresh price for ZZZZ: valued at its cost Rs 5,000", " ".join(res["warnings"]))

    def test_exit_limits_and_the_lower_limit(self):
        lim = dict(self.sell, type="limit", limit_price=88.5)
        res = check.check([lim], cfg(), portfolio=self.pf, now=at(NOW), market=MKT)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("exit, not blocked: limits: the limit price 88.50 is outside today's price limits", warnings(res))
        res = check.check([self.sell], cfg(), portfolio=self.pf, prices={"DEMO": 88.56}, now=at(NOW), market=MKT)
        self.assertIn("DEMO is at today's lower limit 88.56 (company page): a sell may not fill", warnings(res))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([demo(), self.sell], cfg(), now=at(NOW), market=MKT)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")


class AccountChecks(unittest.TestCase):
    """The long-term checks still hold with every day-trade check off."""
    BUYS = [("OGDC", 400, 281.0), ("HBL", 400, 279.0), ("FFC", 200, 521.0), ("LUCK", 200, 468.0), ("SYS", 100, 121.0)]

    def orders(self):
        return [limit_buy(s, q, p) for s, q, p in self.BUYS]

    def one(self, order, **over):
        return check.check([order], v1(**over), now=at(NOW))["results"][0]

    def test_five_buys_pass_on_a_fresh_paper_account(self):
        res = check.check(self.orders(), v1(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        self.assertEqual(res["cash_after"], 65322.88)          # 500,000 less the values and the buy costs
        self.assertEqual((res["results"][0]["value_pkr"], res["results"][0]["amount"]), (112400.0, "400 shares"))

    def test_a_sixth_buy_is_blocked_three_ways(self):
        res = check.check(self.orders() + [limit_buy("OGDC", 100, 281.0)], v1(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash: leaves Rs 37,172.55 cash after costs, under your 10% buffer of Rs 49,922.29", why)
        self.assertIn("position: OGDC would be Rs 140,500", why)
        self.assertIn("duplicate: a second buy OGDC in this batch", why)

    def test_the_day_trade_config_blocks_buys_without_a_stop(self):
        res = check.check(self.orders(), cfg(), now=at(NOW))
        self.assertEqual(verdicts(res)[0], "BLOCK")
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        r = self.one(limit_buy("OGDC", 800, 281.0), max_position_pct=100)
        self.assertIn("size: Rs 224,800 is over your Rs 200,000 max order", " ".join(r["reasons"]))
        self.assertIn("size: Rs 2,810 is under your Rs 5,000 minimum order",
                      " ".join(self.one(limit_buy("OGDC", 10, 281.0))["reasons"]))

    def test_blocked_list(self):
        r = self.one(limit_buy("OGDC", 100, 281.0), blocked=["ogdc"])
        self.assertEqual(r["reasons"], ["blocked: OGDC is on your blocked list"])

    def test_share_orders_need_a_price(self):
        q = {"symbol": "ZZZZ", "side": "buy", "qty": 20, "type": "market", "time_in_force": "day", "board": "REG"}
        self.assertIn("price: no fresh price and no plan entry to measure the risk from (no ZZZZ price in "
                      "psx/prices.json, on today's tape or on a saved company page)", " ".join(self.one(q)["reasons"]))
        r = check.check([dict(q, symbol="OGDC")], v1(), prices={"OGDC": 281.0}, now=at(NOW))["results"][0]
        self.assertEqual((r["verdict"], r["value_pkr"], r["entry_ref_from"]), ("PASS", 5620.0, "price"))

    PF = {"cash": 60000.0, "positions": [{"ticker": "OGDC", "shares": 400,
                                           "lots": [{"date": "2026-10-28", "shares": 400, "cost": 112400.0}]}]}

    def test_short_sales_are_blocked_and_a_sell_frees_cash(self):
        sell = {"symbol": "OGDC", "side": "sell", "qty": 500, "type": "market", "time_in_force": "day", "board": "REG"}
        r = check.check([sell], v1(), portfolio=self.PF, prices={"OGDC": 281.0}, now=at(NOW))["results"][0]
        self.assertIn("short: selling more OGDC than you hold (400 shares)", " ".join(r["reasons"]))
        sell["qty"] = 200
        orders = [sell, limit_buy("HBL", 400, 279.0)]
        c = v1(max_position_pct=100, cash_buffer_pct=0)
        res = check.check(orders, c, portfolio=self.PF, prices={"OGDC": 281.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["cash_after"], 4299.47)            # 60,000 + 56,200 - 100.65 - 111,600 - 199.88
        res = check.check(orders[1:], c, portfolio=self.PF, prices={"OGDC": 281.0}, now=at(NOW))
        self.assertIn("that would be margin", reasons(res))

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed", "at": TODAY + "T10:00:00+05:00"}
                         for i in range(10)]}
        o = limit_buy("OGDC", 100, 281.0)
        res = check.check([o], v1(), state=st, now=at(NOW))
        self.assertIn("daily limit: 10 placed today plus 0 passed earlier in this batch", reasons(res))
        self.assertEqual(verdicts(check.check([o], v1(), state=st, today="2026-10-30", now=at(NOW))), ["PASS"])
        st = {"orders": [{"symbol": "OGDC", "side": "buy", "status": "placed", "at": TODAY + "T10:00:00+05:00"}]}
        self.assertIn("already placed today", reasons(check.check([o], v1(), state=st, now=at(NOW))))
        st["orders"][0]["status"] = "declined"
        self.assertEqual(verdicts(check.check([o], v1(), state=st, now=at(NOW))), ["PASS"])

    def test_live_account_warns(self):
        res = check.check([limit_buy("OGDC", 100, 281.0)], v1(paper=False), now=at(NOW))
        self.assertIn("LIVE account", res["warnings"][0])
        self.assertIs(res["paper"], False)

    def test_money_prints_in_rupees(self):
        self.assertEqual([check.rs(x) for x in (5000, 4999.94, 0, 154353.84999999998, 1229.1)],
                         ["Rs 5,000", "Rs 4,999.94", "Rs 0", "Rs 154,353.85", "Rs 1,229.10"])
        self.assertEqual((check.shares(1), check.shares(3428.0)), ("1 share", "3428 shares"))
        self.assertEqual(self.one(limit_buy("OGDC", 18, 281.0))["amount"], "18 shares")


class Cli(unittest.TestCase):
    def base(self, d, orders=DEMO_PATH, **files):
        none = os.path.join(d, "none")
        args = ["--orders", orders, "--config", files.get("config", CFG_PATH), "--portfolio", files.get("pf", none),
                "--prices", files.get("prices", none), "--state", files.get("state", none),
                "--universe", files.get("universe", UNIVERSE), "--data", files.get("data", MARKET), "--offline"]
        return args

    def test_the_example_run_prints_and_records_the_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            code, text, err = run(self.base(d) + ["--now", NOW, "--out", out])
            self.assertEqual((code, err), (0, ""))
            for line in ("PASS  #1 buy DEMO 3428 shares, entry",
                         "          entry 100.65 (the limit price), stop 99.55, target 101.75: risk 1.10 a share",
                         "          max loss Rs 4,999.94 (limit Rs 5,000), costs included, reward:risk 1.00 (min 1)",
                         "          today before this: 0 of 3 trades, Rs 0 of Rs 15,000 daily loss",
                         "          today's price limits 88.56 to 108.24 (company page)",
                         "1 passed, 0 blocked at 2026-10-29 10:15 Karachi time. Cash after the passed orders: "
                         "Rs 154,353.85 (paper account).",
                         "Every order passed. psx-broker still asks you yes or no for each one.",
                         "Education and tooling, not financial advice."):
                self.assertIn(line + "\n", text + "\n")
            self.assertIn("A market halt at PSX", text)
            for word in ("profit", "P&L", "return", "guarantee", "real time", "live data"):
                self.assertNotIn(word, text.lower() if word.islower() else text)
            rec = read(out)
            self.assertEqual((rec["date"], rec["now_karachi"]), (TODAY, "2026-10-29 10:15"))
            self.assertEqual((rec["results"][0]["kind"], rec["results"][0]["fingerprint"]), ("entry", "e9d01b16c66c4a20"))
            self.assertEqual(rec["results"][0]["limits"]["from"], "company page")
            self.assertTrue(rec["checked_at"].endswith("Z"))
            self.assertEqual(rec["orders_file"], DEMO_PATH)
            self.assertFalse(os.path.exists(out + ".tmp"))

    def test_one_more_share_and_0940_exit_1(self):
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "orders.json")
            with open(bad, "w") as fh:
                json.dump({"orders": [demo(qty=3429)]}, fh)
            code, text, _ = run(self.base(d, orders=bad) + ["--now", NOW, "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("        - loss: 3429 shares x 1.10 = Rs 3,771.90 at the stop + Rs 1,229.50 costs = Rs 5,001.40,\n"
                          "          over your Rs 5,000 max loss per trade (costs included)\n", text)
            self.assertIn("Blocked orders get no ticket from psx-broker.", text)
            self.assertNotIn("A market halt", text)
            code, text, _ = run(self.base(d) + ["--now", "2026-10-29 09:40", "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("        - window: no new entries before 09:45 Karachi time (it is 09:40)\n", text)

    def test_the_flatten_sell_at_1520(self):
        with tempfile.TemporaryDirectory() as d:
            code, text, _ = run(self.base(d, orders=FLAT_PATH, pf=PF_PATH, prices=P1520, state=STATE_PATH) +
                                ["--now", "2026-10-29 15:20", "--no-write"])
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 sell DEMO 3428 shares, exit\n", text)
            self.assertIn("value Rs 348,216.24 at 101.58 (the latest price), sell costs about Rs 623.66", text)

    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            mkt = os.path.join(d, "orders.json")
            with open(mkt, "w") as fh:
                json.dump({"orders": [demo(type="market", limit_price=None)]}, fh)
            base = self.base(d, orders=mkt, prices=P1052) + ["--no-write"]
            code, text, _ = run(base + ["--now", "2026-10-29 10:55"])
            self.assertEqual(code, 1)
            self.assertIn("loss: 3428 shares x 1.75 = Rs 5,999 at the stop", text)
            self.assertIn("today's price limits 88.56 to 108.24 (psx/prices.json)", text)
            code, text, _ = run(base + ["--now", "2026-10-29 11:30"])      # today's tape has 101.50 at 11:29
            self.assertEqual(code, 1)
            self.assertIn("entry 101.50 (the latest price, on today's tape)", text)
            self.assertNotIn("older than 15 minutes", text)
            empty = os.path.join(d, "data")
            os.makedirs(empty)
            code, text, _ = run(self.base(d, orders=mkt, prices=P1052, data=empty) + ["--no-write", "--now",
                                                                                      "2026-10-29 11:30"])
            self.assertEqual(code, 1)                              # the 10:52 quote is stale at 11:30: blocked
            self.assertIn("price: a market order needs a fresh price to measure its risk, and the plan's entry 100.65",
                          " ".join(text.split()))

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as d:
            code, text, _ = run(self.base(d) + ["--now", NOW, "--no-write", "--json"])
            res = json.loads(text)
            self.assertEqual((code, res["now_karachi"], res["results"][0]["max_loss"]), (0, "2026-10-29 10:15", 4999.94))

    def test_offline_never_touches_the_network(self):
        with tempfile.TemporaryDirectory() as d:
            site = os.path.join(d, "config.json")
            with open(site, "w") as fh:
                json.dump(cfg(psx_site=True), fh)
            with mock.patch.object(check, "http_get", no_network):
                code, text, err = run(self.base(d, config=site, data=os.path.join(d, "data")) + ["--now", NOW,
                                                                                                   "--no-write"])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("today's price limits for DEMO are not known, so they were not checked", text)
            self.assertIn("--offline reads only the data folder", text)

    def test_errors_are_one_line(self):
        with tempfile.TemporaryDirectory() as d:
            bad_cfg = os.path.join(d, "config.json")
            with open(bad_cfg, "w") as fh:
                json.dump(cfg(holidays={"29/10/2026": "x"}), fh)
            cases = [(["--orders", os.path.join(d, "nothing.json"), "--no-write"], "no orders file"),
                     (self.base(d) + ["--now", "noon", "--no-write"], "--now wants"),
                     (self.base(d) + ["--today", "monday", "--no-write"], "--today wants a date like 2026-10-29"),
                     (self.base(d) + ["--today", "2026-13-45", "--no-write"], "--today wants"),
                     (self.base(d, config=bad_cfg) + ["--now", NOW, "--no-write"], "\"holidays\" in psx/config.json")]
            for args, want in cases:
                code, text, err = run(args)
                self.assertEqual(code, 1, want)
                self.assertTrue(err.startswith("check.py: "), err)
                self.assertIn(want, err)
                self.assertNotIn("Traceback", err)


class Files(unittest.TestCase):
    def test_load_prices_uses_the_data_time(self):
        prices, times, details = check.load_prices(read(P1052))
        self.assertEqual((prices, times["DEMO"]), ({"DEMO": 101.3}, QUOTE_TIME))
        self.assertEqual(details["DEMO"]["upper"], 108.24)
        doc = {"as_of": "2026-10-29T05:57:07Z", "prices": {"DEMO": 101.3}, "detail": {"DEMO": {"fetched": "2026-10-29T05:57:07Z"}}}
        self.assertEqual(check.load_prices(doc)[1]["DEMO"], datetime.datetime(2026, 10, 29, 5, 52, 7))   # less 5 minutes
        self.assertEqual(check.load_prices({"DEMO": "101.30", "BAD": "x"})[0], {"DEMO": 101.3})
        self.assertEqual(check.load_prices([1, 2]), ({}, {}, {}))

    def test_the_defaults_are_the_spec_config(self):
        spec = read(CFG_PATH)
        for k, v in check.DEFAULTS.items():
            if k != "budget":
                self.assertEqual(spec[k], v, k)
        self.assertEqual(check.cost_settings(spec), check.COSTS)
        for k in ("pdt_guard", "max_order_usd", "max_loss_per_trade_usd", "fractional"):
            self.assertNotIn(k, check.DEFAULTS)

    def test_the_budget_example_config_matches_when_it_is_converted(self):
        path = os.path.join(ROOT, "psx-budget", "config.example.json")
        try:
            budget = read(path)
        except (OSError, ValueError) as e:
            self.skipTest("psx-budget/config.example.json does not read: %s" % e)
        if "max_loss_per_trade_pkr" not in budget:
            self.skipTest("psx-budget/config.example.json is not converted yet")
        spec = read(CFG_PATH)
        for k in list(check.DEFAULTS) + ["costs"]:
            self.assertEqual(budget.get(k), spec.get(k), k)


class FreshPrices(unittest.TestCase):
    """An entry is never measured from a stale price. The lookup: psx/prices.json, then today's tape,
    then the saved company page, each 15 minutes old or less by its data time (psx-budget does the same)."""
    OLD, OLD_TIME = {"DEMO": 99.8}, {"DEMO": datetime.datetime(2026, 10, 28, 10, 30)}     # yesterday's price

    def market_buy(self, **over):
        o = {"symbol": "DEMO", "side": "buy", "qty": 3700, "type": "market", "time_in_force": "day", "board": "REG",
             "stop_loss": 99.55, "take_profit": 101.75}
        o.update(over)
        return o

    def test_a_market_buy_with_only_an_old_price_is_blocked(self):
        res = check.check([self.market_buy()], cfg(), prices=self.OLD, price_times=self.OLD_TIME,
                          now=at("2026-10-29 10:16"), market=MKT)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["entry_ref"], r["max_loss"]), ("BLOCK", None, None))
        self.assertIn("price: no fresh price and no plan entry to measure the risk from (the DEMO price in "
                      "psx/prices.json is not fresh: not within 15 minutes of now by its data time)", reasons(res))

    def test_an_exit_still_shows_its_value_at_an_old_price(self):
        res = check.check(read(FLAT_PATH)["orders"], cfg(), portfolio=read(PF_PATH), prices=self.OLD,
                          price_times=self.OLD_TIME, now=at("2026-10-29 15:20"), market=MKT)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["entry_ref_from"]), ("PASS", "exit", "stale price"))
        self.assertIn("value Rs 342,114.40 at 99.80 (an old price)", r["info"][0])
        self.assertIn("no fresh price for DEMO: valued at its cost Rs 345,028.20", " ".join(res["warnings"]))

    def test_todays_tape_then_the_saved_page(self):
        now = at("2026-10-29 10:16")
        quotes = check.data_quotes(["DEMO"], now, MARKET)
        self.assertEqual([(q["price"], q["source"]) for q in quotes["DEMO"]],
                         [(100.9, "today's tape"), (101.6, "the saved company page")])
        plan = dict(demo(type="market", limit_price=None)["plan"])
        res = check.check([self.market_buy(qty=1000, plan=plan, take_profit=102.3)], cfg(), prices=self.OLD,
                          price_times=self.OLD_TIME, now=now, market=MKT, quotes=quotes)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["entry_ref"], r["entry_ref_from"]), ("PASS", 100.9, "price"))
        self.assertIn("DEMO price 100.90 is above the plan's entry 100.65", r["info"][0])
        self.assertIn("entry 100.90 (the latest price, on today's tape), stop 99.55", " ".join(r["info"]))
        res = check.check([self.market_buy()], cfg(), now=now, market=MKT, quotes=quotes)    # no plan entry: fine
        self.assertEqual(res["results"][0]["entry_ref"], 100.9)
        late = at("2026-10-29 15:55")              # the tape ends at 15:35; the saved page is as of 3:49 PM
        res = check.check(read(FLAT_PATH)["orders"], cfg(), portfolio=read(PF_PATH), now=late, market=MKT,
                          quotes=check.data_quotes(["DEMO"], late, MARKET))
        self.assertIn("value Rs 348,284.80 at 101.60 (the latest price, on the saved company page)",
                      res["results"][0]["info"][0])
        self.assertEqual(check.data_quotes(["DEMO"], at("2026-10-30 10:16"), MARKET)["DEMO"][0]["source"],
                         "the saved company page")              # no tape for 10-30: yesterday's page is not fresh

    def test_a_fresh_prices_file_comes_first(self):
        now = at("2026-10-29 10:52")                       # the tape says 101.30 at 10:52
        res = check.check([self.market_buy()], cfg(), prices={"DEMO": 101.0},
                          price_times={"DEMO": datetime.datetime(2026, 10, 29, 5, 50)}, now=now, market=MKT,
                          quotes=check.data_quotes(["DEMO"], now, MARKET))
        self.assertEqual((res["results"][0]["entry_ref"], res["results"][0]["entry_ref_from"]), (101.0, "price"))
        self.assertIn("entry 101.00 (the latest price), stop", " ".join(res["results"][0]["info"]))

    def test_the_cli_reads_the_tape(self):
        with tempfile.TemporaryDirectory() as d:
            mkt = os.path.join(d, "orders.json")
            with open(mkt, "w") as fh:
                json.dump({"orders": [self.market_buy()]}, fh)
            code, text, _ = run(Cli().base(d, orders=mkt) + ["--now", "2026-10-29 10:52", "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("entry 101.30 (the latest price, on today's tape)", text)
            self.assertIn("max loss Rs 7,805.97 (limit Rs 5,000)", text)


class LimitsSource(unittest.TestCase):
    """psx/prices.json limits count only when they come from the company page ("limits_from")."""

    def test_screener_limits_in_the_prices_file_are_not_used(self):
        with open(os.path.join(MARKET, "company", "DEMO.html"), encoding="utf-8") as fh:
            page = fh.read()
        doc = read(P1052)
        doc["detail"]["DEMO"].update(source="psx-screener", limits_from="computed")
        prices, times, details = check.load_prices(doc)
        o = limit_buy("DEMO", 1000, 100.65, 99.55, 106.0)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "company"))
            with open(os.path.join(d, "company", "DEMO.html"), "w", encoding="utf-8") as fh:   # an ex-date page
                fh.write(page.replace("88.56 \u2014 108.24", "86.00 \u2014 105.11")
                         .replace('data-low="88.56" data-high="108.24"', 'data-low="86.0" data-high="105.11"'))
            mk = market(root=d, details=details, times=times)
            self.assertEqual((mk["limits"]["DEMO"]["upper"], mk["limits"]["DEMO"]["from"]), (105.11, "company page"))
            res = check.check([o], cfg(), now=at(NOW), market=mk)
            self.assertIn("limits: the target 106.00 is above today's upper limit 105.11 (company page)", reasons(res))
            doc["detail"]["DEMO"].pop("limits_from")                 # an older file: where they came from is not known
            prices, times, details = check.load_prices(doc)
            self.assertEqual(market(root=d, details=details, times=times)["limits"]["DEMO"]["upper"], 105.11)
        with tempfile.TemporaryDirectory() as d:                     # no page: computed, with the ex-date warning
            os.makedirs(os.path.join(d, "mkt_summary"))
            shutil.copy(os.path.join(MARKET, "mkt_summary", "2026-10-28.Z"), os.path.join(d, "mkt_summary"))
            lim = market(root=d, details=details, times=times)["limits"]["DEMO"]
            self.assertEqual(lim["from"], "computed")
            res = check.check([o], cfg(), now=at(NOW), market=market(root=d, details=details, times=times))
            self.assertIn("on an ex-date PSX lowers the base", warnings(res))
        doc["detail"]["DEMO"]["limits_from"] = "company page"
        prices, times, details = check.load_prices(doc)
        self.assertEqual(market(details=details, times=times)["limits"]["DEMO"]["from"], "prices file")


class RewardRisk(unittest.TestCase):
    """meets_reward_risk decides (exact, in paisa); reward_risk rounded half up to 2 decimals is shown."""

    def one(self, entry, stop, target, qty=1000, **over):
        return check.check([limit_buy("DEMO", qty, entry, stop, target)], cfg(**over), now=at(NOW), market=MKT)

    def test_no_rounding_before_the_compare(self):
        res = self.one(100.0, 97.95, 102.04)                     # 2.04 / 2.05 = 0.9951
        self.assertEqual((verdicts(res), res["results"][0]["reward_risk"]), (["BLOCK"], 1.0))
        self.assertIn("reward:risk: 1.00 (target 102.04, entry 100.00, stop 97.95) is under your minimum 1: "
                      "2.04 / 2.05 is only 0.9951", reasons(res))
        res = self.one(100.0, 97.5, 102.49)                      # 2.49 / 2.50 = 0.996
        self.assertIn("2.49 / 2.50 is only 0.9960", reasons(res))
        res = self.one(100.0, 97.0, 104.0, min_reward_risk=1.333)   # 4 / 3 = 1.3333: psx-budget sizes it
        self.assertEqual((verdicts(res), res["results"][0]["reward_risk"]), (["PASS"], 1.33))
        self.assertIn("reward:risk 1.33 (min 1.333)", " ".join(res["results"][0]["info"]))
        self.assertEqual(verdicts(self.one(100.0, 98.0, 102.0)), ["PASS"])          # exactly 1
        self.assertIn("reward:risk: 0.68 (target 101.40, entry 100.65, stop 99.55) is under your minimum 1\n",
                      reasons(self.one(100.65, 99.55, 101.4)) + "\n")              # no extra words when it shows


class TheBrokerLog(unittest.TestCase):
    """What counts as placed today: a yes (approved), placed, or a fill recorded by hand."""

    def one(self, recs, order=None):
        return check.check([order or limit_buy("OGDC", 100, 281.0, 280.0, 283.0)], cfg(), state={"orders": recs},
                           now=at("2026-10-29 10:20"))

    def test_fills_recorded_by_hand_count_as_trades(self):
        recs = [{"at": "2026-10-29T10:00:00+05:00", "date": TODAY, "status": "filled", "symbol": s, "side": "buy",
                 "qty": 1000, "price": 100.0} for s in ("DEMO", "HBL", "FFC")]
        res = self.one(recs)
        self.assertIn("trades: 3 entries placed today", reasons(res))
        self.assertIn("a buy of DEMO recorded by hand today (ticket.py fill) has no max loss in the log; not counted "
                      "in the daily loss", " ".join(res["warnings"]))
        self.assertEqual(res["today"], {"orders_placed": 3, "entries_placed": 3, "loss_at_risk": 0.0})
        st = read(STATE_PATH)                       # approved, placed and its paper fill: one order, not two
        res = check.check([limit_buy("OGDC", 100, 281.0, 280.0, 283.0)], cfg(), state=st, now=at("2026-10-29 10:20"))
        self.assertEqual(res["today"], {"orders_placed": 1, "entries_placed": 1, "loss_at_risk": 4999.94})
        self.assertNotIn("no fill recorded yet", " ".join(res["warnings"]))

    def test_a_yes_counts_until_it_is_skipped_or_failed(self):
        yes = read(STATE_PATH)["orders"][:1]                        # approved, no result yet (a live order)
        res = self.one(yes)
        self.assertIn("today before this: 1 of 3 trades, Rs 4,999.94 of Rs 15,000 daily loss",
                      res["results"][0]["info"])
        self.assertIn("no fill recorded yet for buy DEMO, which you said yes to today: the cash here does not take "
                      "it off", " ".join(res["warnings"]))
        self.assertIn("duplicate: a buy DEMO was already placed today", reasons(self.one(yes, demo())))
        for status in ("skipped", "failed"):
            res = self.one(yes + [dict(yes[0], status=status, at="2026-10-29T10:18:00+05:00")])
            self.assertEqual(res["today"]["orders_placed"], 0, status)

    def test_the_side_is_read_in_any_case(self):
        res = self.one([placed("DEMO", side="BUY")], demo())
        self.assertIn("duplicate: a buy DEMO was already placed today", reasons(res))
        self.assertEqual(res["today"]["entries_placed"], 1)


class OldOrders(unittest.TestCase):
    def test_orders_from_another_day_block_an_entry(self):
        res = check.check([demo()], cfg(), now=at("2026-10-30 10:00"), orders_date="2026-10-29")
        self.assertIn("date: these orders were made on 2026-10-29, and today is 2026-10-30 in Karachi. Make a new "
                      "plan (psx-exit, then psx-budget)", reasons(res))
        sell = check.check(read(FLAT_PATH)["orders"], cfg(), portfolio=read(PF_PATH), now=at("2026-10-30 10:00"),
                           orders_date="2026-10-29")
        self.assertEqual(verdicts(sell), ["PASS"])                       # getting out is never blocked
        self.assertIn("exit, not blocked: date: these orders were made on 2026-10-29", warnings(sell))
        self.assertEqual(verdicts(check.check([demo()], cfg(), now=at(NOW), orders_date=TODAY)), ["PASS"])

    def test_the_cli_reads_created_at(self):
        with tempfile.TemporaryDirectory() as d:
            code, text, _ = run(Cli().base(d) + ["--now", "2026-10-30 10:00", "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("- date: these orders were made on 2026-10-29, and today is 2026-10-30", text)


class SmallRules(unittest.TestCase):
    def test_a_held_etf_or_right_can_be_sold_but_a_future_cannot(self):
        for sym, word in (("MZNPETF", "an ETF"), ("BAPLR1", "a rights letter")):
            pf = {"cash": 0, "positions": [{"symbol": sym, "shares": 500}]}
            sell = {"symbol": sym, "side": "sell", "qty": 500, "type": "market", "time_in_force": "day", "board": "REG"}
            res = check.check([sell], cfg(), portfolio=pf, prices={sym: 10.0}, now=at(NOW), market=market(symbols=[sym]))
            self.assertEqual((verdicts(res), res["results"][0]["kind"]), (["PASS"], "exit"), sym)
            self.assertIn("exit, not blocked: asset: %s is %s" % (sym, word), warnings(res))
            buy = dict(sell, side="buy", qty=100, type="limit", limit_price=10.0, stop_loss=9.0, take_profit=12.0)
            res = check.check([buy], cfg(), now=at(NOW), market=market(symbols=[sym]))
            self.assertIn("asset: %s is %s" % (sym, word), reasons(res))          # buying one is still blocked
        pf = {"cash": 0, "positions": [{"symbol": "OGDC-NOV", "shares": 500}]}
        res = check.check([{"symbol": "OGDC-NOV", "side": "sell", "qty": 500, "type": "market"}], cfg(), portfolio=pf,
                          prices={"OGDC-NOV": 280.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_only_json_false_is_a_live_account(self):
        for value, paper in ((None, True), (0, True), ("no", True), (True, True), (False, False)):
            res = check.check([demo()], cfg(paper=value), now=at(NOW), market=MKT)
            self.assertIs(res["paper"], paper, value)
            self.assertEqual("LIVE account" in " ".join(res["warnings"]), not paper, value)

    def test_a_same_day_sell_pays_one_commission_when_set(self):
        prices, times, details = check.load_prices(read(P1520))
        one_side = cfg(costs=dict(check.COSTS, day_trade_one_side=True))
        for pf_lot_day, want in ((TODAY, "sell costs about Rs 22.98"), ("2026-10-28", "sell costs about Rs 623.66")):
            pf = read(PF_PATH)
            pf["positions"][0]["lots"][0]["date"] = pf_lot_day
            res = check.check(read(FLAT_PATH)["orders"], one_side, portfolio=pf, prices=prices, price_times=times,
                              now=at("2026-10-29 15:20"), market=MKT)
            self.assertIn(want, res["results"][0]["info"][0], pf_lot_day)


class OneRuleWithBudget(unittest.TestCase):
    """The blocked list, a named config file and the holdings' prices are read the way psx-budget reads them."""

    def test_a_blocked_string_is_one_ticker_or_a_comma_list(self):
        res = check.check([demo()], cfg(blocked="DEMO, XYZ"), now=at(NOW), market=MKT)
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertIn("blocked", reasons(res))
        with self.assertRaises(check.RiskError):
            check.check([demo()], cfg(blocked=5), now=at(NOW), market=MKT)

    def test_a_config_file_you_name_must_exist(self):
        with tempfile.TemporaryDirectory() as d:
            none = os.path.join(d, "none")
            code, text, err = run(["--orders", DEMO_PATH, "--config", os.path.join(d, "split.json"), "--portfolio",
                                   none, "--prices", none, "--state", none, "--universe", UNIVERSE, "--data", MARKET,
                                   "--offline", "--now", NOW, "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("no config at %s" % os.path.join(d, "split.json"), err)

    def test_a_holding_is_priced_from_the_tape_like_psx_budget_does(self):
        order = limit_buy("OGDC", 10, 283.29, 281.44, 285.14)
        cost_note = "no fresh price for DEMO: valued at its cost"
        res = check.check([order], cfg(), portfolio=read(PF_PATH), now=at("2026-10-29 10:52"), market=MKT,
                          quotes=check.data_quotes(["OGDC"], at("2026-10-29 10:52"), MARKET))
        self.assertIn(cost_note, " | ".join(res["warnings"]))               # the tape not asked for DEMO
        with tempfile.TemporaryDirectory() as d:
            none = os.path.join(d, "none")
            orders = os.path.join(d, "orders.json")
            with open(orders, "w") as fh:
                json.dump({"orders": [order]}, fh)
            code, text, err = run(["--orders", orders, "--config", CFG_PATH, "--portfolio", PF_PATH, "--prices", none,
                                   "--state", none, "--universe", UNIVERSE, "--data", MARKET, "--offline",
                                   "--now", "2026-10-29 10:52", "--no-write", "--json"])
            self.assertEqual(err, "")
            self.assertNotIn(cost_note, " | ".join(json.loads(text)["warnings"]))   # the main run asks for holdings too


class ClockAndSite(unittest.TestCase):
    def test_checked_at_is_the_now_time(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "risk.json")
            run(Cli().base(d) + ["--now", NOW, "--out", out])
            self.assertEqual(read(out)["checked_at"], "2026-10-29T05:15:00Z")      # psx-broker reads its age

    def test_a_replay_with_now_never_asks_the_site(self):
        with tempfile.TemporaryDirectory() as d:
            site = os.path.join(d, "config.json")
            with open(site, "w") as fh:
                json.dump(cfg(psx_site=True), fh)
            args = [x for x in Cli().base(d, config=site) if x != "--offline"]
            with mock.patch.object(check, "http_get", no_network):
                code, text, err = run(args + ["--now", NOW, "--no-write"])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("today's price limits 88.56 to 108.24 (company page)", text)


class TheSkillDoc(unittest.TestCase):
    def test_the_example_block_is_the_real_output(self):
        with open(os.path.join(ROOT, "psx-risk", "SKILL.md"), encoding="utf-8") as fh:
            doc = fh.read()
        block = doc.split("## Example", 1)[1].split("```\n", 2)[1]
        with tempfile.TemporaryDirectory() as d:
            code, text, _ = run(Cli().base(d) + ["--now", NOW, "--no-write"])
        self.assertEqual(code, 0)
        self.assertEqual(block, text)
        self.assertNotIn(chr(0x2014), doc)


# ---------------------------------------------------------------- swing style (SPEC v2 2.4 psx-risk, Task 3)

V2 = os.path.join(FIX, "psx-v2", "contract")    # the shared v2 file shapes (made up, read only)
SWING_CFG = os.path.join(RISK, "config-swing.json")
PRE = "2026-10-29 09:20"                         # the pre-open of the buy day
FRI = "2026-10-30 10:00"
BUY_FP = "0d80ffbb0d898a06"                      # the swing buy of the contract files
PROTECT_FP = "4f1f7cf1107d6b31"                  # the protect sell of 143 DEMO in the contract files
SWING_MKT = {}
# a made-up XB ex-date of DEMO (20% bonus: factor 0.8333) on Thu 2026-11-12, as ex_events() finds it in the data
XB_EX = {"DEMO": [{"date": "2026-11-12", "kind": "XB", "factor": 0.8333}]}


def contract(name):
    return read(os.path.join(V2, name))


def swing(**over):
    """The example config with "style": "swing". swing={...} changes keys of its swing block."""
    c = cfg(style="swing")
    block = over.pop("swing", None)
    if block:
        c["swing"] = dict(c["swing"], **block)
    c.update(over)
    return c


def swing_buy(**over):
    """The DEMO swing buy of the contract: 1,436 at 99.38, stop 96.25, target 105.64 (plan style swing).
    plan={...} changes keys of its plan."""
    o = copy.deepcopy(contract("orders-swing.json")["orders"][0])
    o["plan"].update(over.pop("plan", {}))
    o.update(over)
    return o


def swing_order(sym, qty, entry, stop, target):
    """A swing limit buy with its plan (the max loss at the stop as psx-budget writes it)."""
    plan = dict(swing_buy()["plan"], entry=entry, stop=stop, target=target, risk_per_share=round(entry - stop, 2),
                max_loss=check.max_loss_at_stop(entry, stop, qty, check.COSTS))
    return dict(limit_buy(sym, qty, entry, stop, target), plan=plan)


def swing_ogdc(qty=500):
    """A second swing buy: OGDC 500 at 281.00, stop 272.00, target 299.00 (max loss Rs 4,995.22)."""
    return swing_order("OGDC", qty, 281.0, 272.0, 299.0)


def ledger(qty=1436, cash=None, **plan):
    """psx/portfolio.json of the contract: DEMO held from Thu 2026-10-29 with its swing plan."""
    pf = copy.deepcopy(contract("portfolio-swing.json"))
    p = pf["positions"][0]
    if qty != 1436:
        p["shares"] = qty
        p["lots"][0].update(shares=qty, cost=round(qty * 99.38, 2))
    p["plan"].update(plan)
    if cash is not None:
        pf["cash"] = cash
    return pf


def held(sym, qty, price, stop, day="2026-10-27", **plan):
    """A swing position: one lot bought at price, and a ledger plan with this stop."""
    p = {"style": "swing", "date": day, "stop": stop, "stop_at_buy": stop, "target": round(price * 1.1, 2),
         "exit_by": "2026-11-24", "exit_by_time": "15:20", "trend_sessions": 10, "scaled": []}
    p.update(plan)
    return {"ticker": sym, "shares": qty, "plan": p,
            "lots": [{"id": sym + "-1", "date": day, "shares": qty, "cost": round(qty * price, 2), "costs": 10.0}]}


def three_held(cash=400000.0):
    """OGDC, HBL and LUCK held: Rs 895.28 + Rs 895.28 + Rs 779.34 = Rs 2,569.90 at their stops."""
    return {"cash": cash, "positions": [held("OGDC", 100, 270.0, 262.0), held("HBL", 100, 270.0, 262.0),
                                        held("LUCK", 50, 450.0, 436.0)]}


def yes(order, day=TODAY, at="09:20", status="approved", **over):
    """psx-broker's record of a yes ("approved") or "placed" for this order, as ticket.py approve writes it."""
    r = {"at": "%sT%s:00+05:00" % (day, at), "date": day, "index": 1, "fingerprint": check.fingerprint(order),
         "symbol": order["symbol"], "side": order["side"], "qty": order["qty"],
         "kind": "entry" if order["side"] == "buy" else "exit", "max_loss": (order.get("plan") or {}).get("max_loss"),
         "stop": order.get("stop_loss"), "plain": "made up", "status": status, "entry_ref": order.get("limit_price")}
    r.update(over)
    return r


def swing_market(today=TODAY):
    if today not in SWING_MKT:
        SWING_MKT[today] = market(today=today, symbols=("DEMO", "OGDC", "HBL", "LUCK"))
    return SWING_MKT[today]


def xd_market(today=TODAY):
    """The sample market with a made-up XD page of DEMO on Thu 2026-10-29 at 9:20 AM, before the open: a cash payout
    of 3.00, so PSX lowered the LDCP from the close 98.40 to 95.40, and the circuit breaker to 85.86 to 104.94."""
    m = copy.deepcopy(swing_market(today))
    m["limits"]["DEMO"] = {"lower": 85.86, "upper": 104.94, "ldcp": 95.4, "from": "company page", "label": "company page"}
    m["pages"]["DEMO"] = dict(m["pages"]["DEMO"], ex=["XD"], today=True, as_of="Thu 2026-10-29 9:20 AM")
    return m


def xd_data(folder):
    """A data folder: the sample closing files, and the made-up XD company page of DEMO (as xd_market, the way
    test_exit makes it) -> its path."""
    shutil.copytree(os.path.join(MARKET, "mkt_summary"), os.path.join(folder, "mkt_summary"))
    with open(os.path.join(MARKET, "company", "DEMO.html"), encoding="utf-8") as fh:
        page = fh.read()
    for old, new in (('quote__name">Demo Industries Limited<',
                      'quote__name">Demo Industries Limited<div class="tag tag--yellow">XD</div><'),
                     ("As of Thu, Oct 29, 2026 3:49 PM", "As of Thu, Oct 29, 2026 9:20 AM"),
                     ('data-low="88.56" data-high="108.24"', 'data-low="85.86" data-high="104.94"'),
                     ('LDCP</div><div class="stats_value">98.40', 'LDCP</div><div class="stats_value">95.40')):
        assert page.count(old) == 1, old
        page = page.replace(old, new)
    os.makedirs(os.path.join(folder, "company"))
    with open(os.path.join(folder, "company", "DEMO.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    return folder


def run_swing(orders, now=PRE, c=None, **kw):
    """check.check() of these orders with the swing config, at a Karachi time, on the sample market."""
    kw.setdefault("market", swing_market(now[:10]))
    return check.check(orders, c or swing(), now=at(now), **kw)


class Swing(unittest.TestCase):
    """Swing buys: the gates of SPEC v2 2.4 (psx-risk) and D29, D31, D33, D55, D57."""

    def test_the_contract_swing_buy_passes_in_the_pre_open(self):
        res = run_swing([swing_buy()])
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["reasons"]), ("PASS", "entry", []))
        self.assertEqual((r["entry_ref"], r["stop"], r["target"], r["max_loss"], r["fingerprint"]),
                         (99.38, 96.25, 105.64, 4997.81, BUY_FP))
        self.assertEqual((r["gap_loss"], r["gap_low"]), (14759.46, 89.44))
        self.assertIn("gap loss Rs 14,759.46 if it opens at the lower limit 89.44 (limit Rs 15,000)", r["info"])
        self.assertIn("open before this: 0 of 3 positions, Rs 0 of Rs 15,000 open risk (costs included)", r["info"])
        self.assertIn("after the buy fills, ticket.py protect makes a stop-limit ticket", warnings(res))
        self.assertEqual(res["cash_after"], 357034.73)          # the cash of portfolio-swing.json after the fill
        self.assertEqual((res["style"], res["notes"]), ("swing", [check.GAP_NOTE, check.HALT_NOTE]))
        self.assertEqual(check.GAP_NOTE, "A gap can open past a stop: a loss can be bigger than its max loss.")
        day = check.check([demo()], cfg(), now=at(NOW), market=MKT)
        self.assertNotIn("style", day)                          # day results keep the v1 keys
        self.assertNotIn("gap_loss", day["results"][0])

    def test_the_swing_window(self):
        def why(now, **over):
            o = swing_buy(**over)
            res = run_swing([o], now=now)
            return [x for x in res["results"][0]["reasons"] if x.startswith("window: ")]
        self.assertEqual(why(PRE), [])                                   # a limit buy waits for the opening match
        self.assertEqual(why("2026-10-29 09:20", type="market", limit_price=None),
                         ["window: PSX takes no market orders in the pre-open: use a limit order "
                          "(Thu 2026-10-29 09:20 Karachi time)"])
        self.assertEqual(why("2026-10-29 09:31"), ["window: the opening match runs until 9:32 AM PKT: no new orders "
                                                   "yet (Thu 2026-10-29 09:31 Karachi time)"])
        for now in ("2026-10-29 09:33", "2026-10-29 09:40", "2026-10-29 15:10", "2026-10-29 15:25",
                    "2026-10-30 14:20", "2026-10-30 09:05"):
            self.assertEqual(why(now), [], now)                          # no_entries_* are day rules
        self.assertEqual(verdicts(run_swing([swing_buy()], now="2026-10-29 15:10")), ["PASS"])
        self.assertIn("window: no new entries after 15:00", reasons(check.check([demo()], cfg(), now=at("2026-10-29 15:10"))))
        self.assertIn("closed all day (weekend)", why("2026-10-31 10:00")[0])
        self.assertIn("closed all day (PSX holiday: Iqbal Day)", why("2026-11-09 10:00")[0])
        self.assertIn("closed for the day", why("2026-10-29 16:00")[0])
        self.assertIn("Juma break", why("2026-10-30 12:30")[0])
        self.assertIn("until 2:32 PM PKT", why("2026-10-30 14:31")[0])
        res = run_swing([swing_buy()], now="2026-10-30 11:45")
        self.assertNotIn("Juma break starts", warnings(res))             # a swing buy is held past the break anyway

    def test_a_target_over_the_upper_limit_is_a_note_in_swing(self):
        o = swing_buy(take_profit=110.0, plan={"target": 110.0})
        r = run_swing([o])["results"][0]
        self.assertEqual((r["verdict"], r["reasons"]), ("PASS", []))
        self.assertIn("the target 110.00 is above today's upper limit 108.24 (company page): it cannot fill today, "
                      "only on a later day", r["info"])
        res = check.check([demo(take_profit=108.5)], cfg(), now=at(NOW), market=MKT)       # day: still a block
        self.assertEqual(res["results"][0]["reasons"], ["limits: the target 108.50 is above today's upper limit "
                                                        "108.24 (company page), so it cannot fill today"])
        o = swing_buy(limit_price=108.3, plan={"entry": 108.3})
        self.assertIn("limits: the limit price 108.30 is outside today's price limits 88.56 to 108.24",
                      reasons(run_swing([o])))                           # an entry outside them still blocks

    def test_a_stop_at_the_lower_limit_gets_the_swing_words(self):
        # exactly at the limit: a trade at 88.56 meets it on the buy day (psx-alerts rings it then), so it does not
        # wait for the next session (review F40)
        o = swing_buy(qty=100, stop_loss=88.56, take_profit=111.0, plan={"stop": 88.56, "target": 111.0})
        res = run_swing([o])
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("the stop 88.56 is at today's lower limit 88.56 (company page): a trade at the limit meets it "
                      "today, and at the limit there may be no buyers", warnings(res))
        self.assertNotIn("next session", warnings(res))
        # under the limit: it cannot be met today, so it counts from the next session (psx-budget's strict "<")
        o = swing_buy(qty=100, stop_loss=88.55, take_profit=111.0, plan={"stop": 88.55, "target": 111.0})
        res = run_swing([o])
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("the stop 88.55 is below today's lower limit 88.56 (company page): today the price cannot go "
                      "under the limit, so the stop counts from the next session", warnings(res))

    def test_an_ex_date_on_the_buy_day_blocks_a_plan_made_on_the_last_close(self):
        """The plan was made the evening before from the last close 98.40. On the buy day the company page shows XD:
        a made-up cash payout of 3.00, so PSX lowered the LDCP to 95.40 (circuit breaker 85.86 to 104.94). The plan's
        entry 99.38 and stop 96.25 are on the old prices: a limit buy fills near 95.40, under the stop (review F41)."""
        xd = xd_market()
        plan = contract("plan-swing.json")                               # last_close 98.4, limits computed from it
        block = ("ex-date: today's LDCP 95.40 (company page) is not the plan's last close 98.40. Today's company page "
                 "shows XD. An ex-date, a split or days with no trade moved it, so the plan's entry and stop do not "
                 "fit today's prices. Make a new plan with psx-exit (exit.py plan --symbol DEMO), then size it again "
                 "with psx-budget")
        over = "the stop 96.25 is at or over today's LDCP 95.40: a limit buy can fill lower, at or under the stop"
        res = run_swing([swing_buy()], market=xd, plan_doc=plan)
        self.assertEqual((verdicts(res), res["results"][0]["reasons"]), (["BLOCK"], [block]))
        self.assertIn(over, res["results"][0]["warnings"])
        # with no psx/plan.json of this order: the last close in the closing files, which psx-exit plans from
        other = [None, dict(plan, symbol="OGDC"), dict(plan, date="2026-10-28"), dict(plan, stop=96.0),
                 dict(plan, style="day")]
        for doc in other:
            self.assertEqual(run_swing([swing_buy()], market=xd, plan_doc=doc)["results"][0]["reasons"], [
                block.replace("the plan's last close 98.40", "the last close 98.40 (Wed 2026-10-28)")], doc)
        # the same from psx/prices.json (psx-alerts quote: limits from the company page); a page with no tag
        quote = copy.deepcopy(xd)
        quote["limits"]["DEMO"] = dict(xd["limits"]["DEMO"], **{"from": "prices file", "label": "psx/prices.json"})
        quote["pages"]["DEMO"] = dict(quote["pages"]["DEMO"], ex=[])
        self.assertEqual(run_swing([swing_buy()], market=quote, plan_doc=plan)["results"][0]["reasons"], [
            block.replace("(company page)", "(psx/prices.json)").replace(" Today's company page shows XD.", "")])
        # a plan made on the buy day from that page (psx-exit --entry): its LDCP is today's, so it passes
        new = dict(plan, entry=96.35, stop=93.22, target=102.61,
                   limits={"lower": 85.86, "upper": 104.94, "ldcp": 95.4, "from": "company page"})
        mine = swing_buy(limit_price=96.35, stop_loss=93.22, take_profit=102.61, qty=1000,
                         plan={"entry": 96.35, "stop": 93.22, "target": 102.61})
        res = run_swing([mine], market=xd, plan_doc=new)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertFalse([w for w in warnings(res).split(" | ") if "LDCP" in w])
        # the old entry by hand on that page: the plan fits today's LDCP, but its stop is over it, so it warns
        res = run_swing([swing_buy()], market=xd, plan_doc=dict(plan, limits=new["limits"]))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn(over, res["results"][0]["warnings"])
        # an LDCP one tick away is no ex-date; computed limits (no page of today) are not checked here
        tick = copy.deepcopy(xd)
        tick["limits"]["DEMO"] = dict(tick["limits"]["DEMO"], ldcp=98.39)
        self.assertEqual(verdicts(run_swing([swing_buy()], market=tick, plan_doc=plan)), ["PASS"])
        old = copy.deepcopy(xd)
        old["limits"]["DEMO"] = {"lower": 88.56, "upper": 108.24, "ldcp": 95.4, "from": "computed",
                                 "label": "computed from the 2026-10-28 close 98.40"}
        self.assertNotIn("ex-date:", reasons(run_swing([swing_buy()], market=old, plan_doc=plan)))
        # the made-up sample market: HBL went ex (XD) on Thu 10-29, and its LDCP is 275.00, not 279.00
        res = run_swing([swing_order("HBL", 100, 279.0, 270.0, 297.0)], now="2026-10-29 09:25")
        self.assertIn("ex-date: today's LDCP 275.00 (company page) is not the last close 279.00 (Wed 2026-10-28). "
                      "Today's company page shows XD.", reasons(res))
        # day style and a psx-invest plan: no such check
        self.assertNotIn("ex-date:", reasons(check.check([demo()], cfg(), now=at(NOW), market=xd)))
        inv = swing_buy(plan={"style": "invest"})
        self.assertNotIn("ex-date:", reasons(run_swing([inv], market=xd, plan_doc=plan)))
        self.assertNotIn("LDCP", warnings(run_swing([inv], market=xd, plan_doc=plan)))

    def test_the_style_gate_both_ways(self):
        day = "style: this is a swing plan, and your style is day (psx/config.json). Make a new plan with psx-exit"
        res = check.check([swing_buy()], cfg(), now=at(NOW), market=MKT)
        self.assertIn(day, reasons(res))
        self.assertEqual(verdicts(check.check([demo()], cfg(), now=at(NOW), market=MKT)), ["PASS"])
        res = run_swing([demo()], now=NOW)
        self.assertIn("style: this is a day plan, and your style is swing (psx/config.json). Make a swing plan with "
                      "psx-exit, then size it with psx-budget", reasons(res))
        bare = dict(swing_buy())
        bare.pop("plan")
        self.assertIn("style: this is a buy with no plan, and your style is swing", reasons(run_swing([bare])))
        odd = swing_buy(plan={"style": "scalp"})
        self.assertIn("style: the plan's style is 'scalp', and a plan is day, swing or invest", reasons(run_swing([odd])))
        inv = swing_buy(plan={"style": "invest"})
        for res in (run_swing([inv]), check.check([inv], cfg(), now=at(NOW), market=MKT)):
            self.assertNotIn("style:", reasons(res))                     # an invest plan passes the gate in both
        sell = dict(read(FLAT_PATH)["orders"][0], qty=1436)
        res = run_swing([sell], now="2026-10-30 10:00", portfolio=ledger(), market=swing_market("2026-10-30"))
        self.assertEqual((verdicts(res), warnings(res)), (["PASS"], ""))     # an exit has no style gate

    def test_a_bad_style_or_swing_value_is_one_plain_error(self):
        with self.assertRaises(ValueError):
            check.check([demo()], cfg(style="scalp"), now=at(NOW))
        with self.assertRaises(check.RiskError):
            run_swing([swing_buy()], c=swing(swing={"max_open_positions": "three"}))
        with self.assertRaises(check.RiskError):
            run_swing([swing_buy()], c=swing(swing={"max_gap_loss_pkr": -5}))
        with tempfile.TemporaryDirectory() as d:
            for bad, want in (({"style": "scalp"}, "check.py: \"style\" in psx/config.json is \"day\" or \"swing\", "
                                                   "not 'scalp'\n"),
                              ({"style": "swing", "swing": {"max_open_positions": 2.5}},
                               "check.py: \"max_open_positions\" in the \"swing\" block of psx/config.json must be a "
                               "whole number, 0 or more, or null (got 2.5)\n")):
                path = os.path.join(d, "config.json")
                with open(path, "w") as fh:
                    json.dump(cfg(**bad), fh)
                code, text, err = run(Cli().base(d, config=path) + ["--now", NOW, "--no-write"])
                self.assertEqual((code, text, err), (1, "", want))

    def test_three_held_positions_and_one_more_buy_are_blocked(self):
        res = run_swing([swing_buy()], portfolio=three_held())
        self.assertEqual(res["results"][0]["reasons"], ["open positions: 3 open (HBL, LUCK and OGDC held) plus this one "
                                                        "is 4, your limit is 3 (swing.max_open_positions)"])
        self.assertIn("open before this: 3 of 3 positions, Rs 2,569.90 of Rs 15,000 open risk (costs included)",
                      res["results"][0]["info"])
        self.assertEqual(verdicts(run_swing([swing_buy()], portfolio=three_held(),
                                            c=swing(swing={"max_open_positions": 4}))), ["PASS"])
        self.assertEqual(verdicts(run_swing([swing_buy()], portfolio=three_held(),
                                            c=swing(swing={"max_open_positions": 0}))), ["PASS"])     # 0: off (as v1)
        out = {"symbol": "OGDC", "side": "sell", "qty": 100, "type": "limit", "limit_price": 281.0,
               "time_in_force": "day", "board": "REG"}
        res = run_swing([out, swing_buy()], now="2026-10-29 09:40", portfolio=three_held())
        self.assertEqual(verdicts(res), ["PASS", "PASS"])           # a sell passed earlier in the batch frees one
        self.assertIn("open before this: 2 of 3 positions", " | ".join(res["results"][1]["info"]))
        two = [swing_order("LUCK", 300, 468.0, 453.0, 498.0), swing_buy()]
        res = run_swing(two, portfolio=dict(three_held(), positions=three_held()["positions"][:2]))
        self.assertEqual(verdicts(res), ["PASS", "BLOCK"])          # and a buy passed earlier counts
        self.assertEqual(res["results"][1]["reasons"], ["open positions: 3 open (HBL and OGDC held; LUCK passed earlier "
                                                        "in this batch) plus this one is 4, your limit is 3 "
                                                        "(swing.max_open_positions)"])

    def test_open_risk_over_the_limit_is_blocked(self):
        res = run_swing([swing_buy()], portfolio=three_held(), c=swing(swing={"max_open_risk_pkr": 6000,
                                                                              "max_open_positions": 5}))
        self.assertEqual(res["results"][0]["reasons"], [
            "open risk: Rs 2,569.90 at the stops of your open positions plus Rs 4,997.81 for this one = Rs 7,567.71, "
            "over your Rs 6,000 limit (swing.max_open_risk_pkr; costs included)"])
        five = {"max_open_positions": 5}
        self.assertEqual(verdicts(run_swing([swing_buy()], portfolio=three_held(), c=swing(swing=five))), ["PASS"])
        res = run_swing([swing_buy()], portfolio=three_held(), c=swing(swing=five, max_daily_loss_pkr=7000))
        self.assertEqual(res["results"][0]["reasons"], [                  # null: max_daily_loss_pkr is the limit
            "open risk: Rs 2,569.90 at the stops of your open positions plus Rs 4,997.81 for this one = Rs 7,567.71, "
            "over your Rs 7,000 limit (max_daily_loss_pkr, as swing.max_open_risk_pkr is null; costs included)"])
        self.assertIn("Rs 2,569.90 of Rs 7,000 open risk", " ".join(res["results"][0]["info"]))
        off = swing(swing={"max_open_risk_pkr": 0, "max_open_positions": 5}, max_daily_loss_pkr=7000)
        res = run_swing([swing_buy()], portfolio=three_held(), c=off)    # 0: off, as v1 reads 0 (not the gap limit)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("Rs 2,569.90 of no limit on open risk", " ".join(res["results"][0]["info"]))

    def test_a_position_with_no_plan_stop_counts_at_the_max_loss_per_trade(self):
        pf = three_held()
        pf["positions"][0]["plan"].pop("stop")
        res = run_swing([swing_buy()], portfolio=pf, c=swing(swing={"max_open_positions": 5}))
        self.assertIn("OGDC has no plan stop in psx/portfolio.json: its open risk counts as your Rs 5,000 max loss "
                      "per trade", res["warnings"])
        self.assertIn("open before this: 3 of 5 positions, Rs 6,674.62 of Rs 15,000 open risk (costs included)",
                      res["results"][0]["info"])                        # 5,000 + 895.28 + 779.34
        pf["positions"][1].pop("plan")
        res = run_swing([swing_buy()], portfolio=pf, c=swing(swing={"max_open_positions": 5}))
        self.assertIn("HBL has no plan stop in psx/portfolio.json", " | ".join(res["warnings"]))
        self.assertIn("open risk: Rs 10,779.34 at the stops of your open positions plus Rs 4,997.81 for this one",
                      reasons(res))
        pf = three_held()
        pf["positions"][2]["lots"][0].pop("cost")
        self.assertIn("LUCK has no cost in its lots in psx/portfolio.json: its open risk counts as your Rs 5,000",
                      " | ".join(run_swing([swing_buy()], portfolio=pf)["warnings"]))
        pf = three_held()
        pf["positions"][0]["plan"]["stop"] = 271.0                       # over the cost 270.00 a share: 0
        res = run_swing([swing_buy()], portfolio=pf, c=swing(swing={"max_open_positions": 5}))
        self.assertIn("Rs 1,674.62 of Rs 15,000 open risk", " | ".join(res["results"][0]["info"]))

    def test_a_stop_lowered_by_hand_warns_and_the_lower_stop_counts(self):
        pf = three_held()
        pf["positions"][1]["plan"]["stop"] = 250.0                       # HBL: 262.00 at the buy
        res = run_swing([swing_buy()], portfolio=pf, c=swing(swing={"max_open_positions": 5}))
        self.assertIn("HBL's stop in psx/portfolio.json (250.00) is under its stop at the buy (262.00): it was lowered "
                      "by hand.", res["warnings"])
        self.assertIn("Rs 3,767.76 of Rs 15,000 open risk", " | ".join(res["results"][0]["info"]))  # 895.28 + 2,093.14
        up = ledger(qty=143, stop=97.0)                                   # raised with ticket.py stop: no warning
        scaled = ledger(qty=143, stop=80.2, target=88.03, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB"}])
        low = ledger(qty=143, stop=80.0, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB"}])
        sell = {"symbol": "DEMO", "side": "sell", "qty": 143, "type": "market", "time_in_force": "day", "board": "REG"}
        for pf, want in ((up, None), (scaled, None), (low, "DEMO's stop in psx/portfolio.json (80.00) is under its stop "
                                                           "at the buy (80.20, after the recorded scale): it was lowered "
                                                           "by hand.")):
            words = run_swing([sell], now=FRI, portfolio=pf, ex_dates=XB_EX)["warnings"]   # the XB is in the data
            self.assertEqual([w for w in words if "lowered by hand" in w], [want] if want else [])
        self.assertIsNone(check.lowered_by_hand("DEMO", None))
        self.assertEqual(check.stop_at_buy_now({"stop_at_buy": 96.25, "scaled": [{"factor": 1}]}), 96.25)   # keep
        res = check.check([sell], cfg(), now=at(FRI), portfolio=low)
        self.assertNotIn("lowered by hand", " ".join(res["warnings"]))  # day style: no new warning

    def test_the_open_risk_after_a_scale_waits_for_the_bonus_shares(self):
        """XB 20% (factor 0.8333): ticket.py stop --scale puts the stop at 80.20 at once, but the cost a share and the
        shares move to the new price scale only when ticket.py fill --bonus records the bonus shares. Until then the
        stop before the scale counts (review F24: the mix counted Rs 28,004.33 and blocked every swing buy)."""
        scaled = ledger(stop=80.2, target=88.03, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB 20%"}])
        buy = swing_order("OGDC", 100, 281.0, 272.0, 299.0)
        res = run_swing([buy], now="2026-11-13 09:20", portfolio=scaled, ex_dates=XB_EX)   # the XB is in the data
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("open before this: 1 of 3 positions, Rs 4,997.81 of Rs 15,000 open risk (costs included)",
                      res["results"][0]["info"])                       # 1436 x (99.38 - 96.25) + costs, as at the buy
        self.assertIn("DEMO's plan was scaled (x0.8333 on Thu 2026-11-12), and no bonus or split shares are recorded "
                      "since the buy. So its open risk is counted on the old price scale, with the stop before the "
                      "scale (96.25). When the new shares show in your broker app, record them: ticket.py fill --symbol "
                      "DEMO --bonus N", res["warnings"])
        self.assertFalse([w for w in res["warnings"] if "lowered by hand" in w])
        bonus = copy.deepcopy(scaled)                                     # ticket.py fill --bonus 287 on Thu 11-19
        bonus["positions"][0]["lots"].append({"id": "DEMO-2", "date": "2026-11-19", "shares": 287, "cost": 0.0,
                                              "costs": 0.0, "bonus": True})
        bonus["positions"][0]["shares"] = 1723
        res = run_swing([buy], now="2026-11-20 09:20", portfolio=bonus, ex_dates=XB_EX)
        self.assertIn("open before this: 1 of 3 positions, Rs 5,028.16 of Rs 15,000 open risk (costs included)",
                      res["results"][0]["info"])                       # 142,709.68 / 1723 a share, stop 80.20
        self.assertFalse([w for w in res["warnings"] if "was scaled" in w or "lowered by hand" in w])
        keep = ledger(scaled=[{"date": "2026-11-12", "factor": 1, "why": "keep"}])     # --scale 1: nothing to wait for
        res = run_swing([buy], now="2026-11-13 09:20", portfolio=keep)
        self.assertIn("Rs 4,997.81 of Rs 15,000 open risk", " ".join(res["results"][0]["info"]))
        self.assertFalse([w for w in res["warnings"] if "was scaled" in w])
        self.assertEqual((check.unscaled_stop(80.2, [0.8333]), check.round_down_tick(96.25 * 0.8333)), (96.25, 80.2))
        self.assertEqual(check.unscaled_stop(48.12, [0.5]), 96.24)        # the lower of two ticks: the bigger risk

    def test_a_scale_counts_only_with_a_bonus_rights_or_split_ex_date(self):
        """ticket.py stop --scale F needs a bonus, rights or split ex-date of the share, and F is never under its
        factor. So a "scaled" entry added by hand must not hide a stop lowered by hand (review F15)."""
        scaled = ledger(stop=80.2, target=88.03, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB 20%"}])
        buy = swing_order("OGDC", 100, 281.0, 272.0, 299.0)
        nope = ("DEMO's stop in psx/portfolio.json (80.20) is under its stop at the buy (96.25): it was lowered by hand. "
                "The scale x0.8333 of Thu 2026-11-12 does not count: the closing files and psx/state/alerts.json show "
                "no bonus, rights or split ex-date of DEMO for it.")
        # no ex-date in the data: the scale does not count, and the lower stop counts as it is (1436 x 99.38 - 80.20)
        for ex in (None, {}, {"DEMO": []}, {"DEMO": [{"date": "2026-11-12", "kind": "XB", "factor": 1.0}]}):
            res = run_swing([buy], now="2026-11-13 09:20", portfolio=scaled, ex_dates=ex)
            self.assertIn(nope, res["warnings"], ex)
            self.assertFalse([w for w in res["warnings"] if "was scaled" in w], ex)
            self.assertEqual(res["results"][0]["reasons"], [
                "open risk: Rs 28,004.33 at the stops of your open positions plus Rs 999.05 for this one = Rs 29,003.38, "
                "over your Rs 15,000 limit (max_daily_loss_pkr, as swing.max_open_risk_pkr is null; costs included)"], ex)
        # an ex-date after the scale, or before the buy day, does not back it
        for when in ("2026-11-13", "2026-10-20"):
            ex = {"DEMO": [{"date": when, "kind": "XB", "factor": 0.8333}]}
            self.assertIn(nope, run_swing([buy], now="2026-11-13 09:20", portfolio=scaled, ex_dates=ex)["warnings"], when)
        # one ex-date backs one scale: a second scale of the same XB does not count
        two = ledger(stop=66.83, target=73.36, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB"},
                                                       {"date": "2026-11-13", "factor": 0.8333, "why": "again"}])
        res = run_swing([buy], now="2026-11-13 09:20", portfolio=two, ex_dates=XB_EX)
        self.assertIn("DEMO's stop in psx/portfolio.json (66.83) is under its stop at the buy (80.20, after the recorded "
                      "scale): it was lowered by hand. The scale x0.8333 of Fri 2026-11-13 does not count: the closing "
                      "files and psx/state/alerts.json show no bonus, rights or split ex-date of DEMO for it.",
                      res["warnings"])
        self.assertEqual(check.checked_scales(two["positions"][0]["plan"], XB_EX["DEMO"]),
                         ([("2026-11-12", 0.8333)], [("2026-11-13", 0.8333)], []))
        self.assertEqual((check.stop_at_buy_now(two["positions"][0]["plan"], XB_EX["DEMO"]),
                          check.stop_at_buy_now(two["positions"][0]["plan"])), (80.2, 96.25))
        # a scale under the ex-date's factor counts only as that factor, the most ticket.py takes
        hand = ledger(stop=48.12, target=52.82, scaled=[{"date": "2026-11-12", "factor": 0.5, "why": "by hand"}])
        res = run_swing([buy], now="2026-11-13 09:20", portfolio=hand, ex_dates=XB_EX)
        self.assertIn("DEMO's stop in psx/portfolio.json (48.12) is under its stop at the buy (80.20, after the recorded "
                      "scale): it was lowered by hand. The scale x0.5 of Thu 2026-11-12 is under its ex-date's factor "
                      "0.8333, so it counts as x0.8333.", res["warnings"])
        self.assertIn("open risk: Rs 60,184.80 at the stops of your open positions", reasons(res))  # stop 57.75, old scale
        # two ex-dates before one scale: both factors; a factor a hair under (4 decimals) is fine, as in ticket.py
        evs = [{"date": "2026-11-05", "kind": "XB", "factor": 0.9}, {"date": "2026-11-12", "kind": "XR", "factor": 0.9}]
        plan = {"date": "2026-10-29", "stop": 77.96, "stop_at_buy": 96.25,
                "scaled": [{"date": "2026-11-12", "factor": 0.8098}, {"date": "2026-11-12", "factor": 1}]}
        self.assertEqual(check.checked_scales(plan, evs), ([("2026-11-12", 0.8098)], [], []))
        self.assertIsNone(check.lowered_by_hand("DEMO", plan, evs))
        self.assertEqual(check.checked_scales(dict(plan, scaled=[{"date": "2026-11-12", "factor": 1.25}]), evs),
                         ([], [], []))                                    # over 1: no tool writes it, so not counted
        # day style: no swing warning at all, as in v1
        sell = {"symbol": "DEMO", "side": "sell", "qty": 1436, "type": "market", "time_in_force": "day", "board": "REG"}
        self.assertNotIn("lowered by hand", " ".join(check.check([sell], cfg(), now=at(FRI), portfolio=scaled)["warnings"]))

    def test_the_ex_dates_come_from_the_closing_files_and_psx_alerts(self):
        """ex_events() finds what ticket.py stop --scale finds: a new XB or XR flag (or a 10% LDCP change with no
        flag) in the closing files, and psx-alerts' record. An XD is a cash payout: never a scale (D27)."""
        since, mon = datetime.date(2026, 10, 29), datetime.date(2026, 11, 2)
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(os.path.join(MARKET, "mkt_summary"), os.path.join(d, "mkt_summary"))
            self.assertEqual(check.ex_events("DEMO", since, mon, d), [])           # the sample has none
            with open(os.path.join(d, "mkt_summary", "2026-10-30.lis"), "w") as fh:  # Fri: XB 20%, LDCP 84.67
                fh.write("30OCT2026|DEMO|0818|Demo IndustriesXB|84.70|85.50|84.00|85.00|1000000|84.67|\n")
            want = [{"date": "2026-10-30", "kind": "XB", "factor": 0.8334}]         # 84.67 / 101.60
            self.assertEqual(check.ex_events("DEMO", since, mon, d), want)
            self.assertEqual(check.ex_events("DEMO", datetime.date(2026, 10, 31), mon, d), [])   # before the buy day
            alerts = {"swing_ex": {"DEMO-2026-10-29": {
                "2026-11-02": {"kind": "XR", "factor": 0.95, "date": "2026-11-02", "seen": "company page"},
                "2026-10-31": {"kind": "XD", "factor": 0.97}, "2026-11-09": {"kind": "XB", "factor": 0.8}}}}
            got = check.ex_events("DEMO", since, mon, d, alerts)
            self.assertEqual(got, want + [{"date": "2026-11-02", "kind": "XR", "factor": 0.95}])   # no XD, nothing after
            pf = ledger(stop=80.21, target=88.04, scaled=[{"date": "2026-10-30", "factor": 0.8334, "why": "XB"}])
            pf["positions"].append(held("OGDC", 100, 270.0, 262.0))           # no scale: not read
            self.assertEqual(check.scale_events(pf, mon, d, alerts), {"DEMO": got})
            self.assertEqual(check.scale_events(ledger(), mon, d, alerts), {})
            self.assertEqual(check.scale_events(None, mon, d), {})

    def test_a_second_buy_of_a_held_stock_is_blocked(self):
        res = run_swing([swing_buy(qty=143)], portfolio=ledger(qty=143))
        self.assertIn("open: you already hold DEMO (since Thu 2026-10-29). Swing style keeps one plan a stock, so a "
                      "second buy of DEMO is blocked while you hold it", reasons(res))
        res = check.check([demo(qty=100)], cfg(), now=at(NOW), market=MKT, portfolio=read(PF_PATH))
        self.assertEqual(verdicts(res), ["PASS"])                       # day style: adding is fine, as in v1

    def test_the_gap_gate(self):
        small = cfg(style="swing", max_loss_per_trade_pkr=500)
        res = run_swing([swing_buy(qty=143)], c=small)
        self.assertEqual((verdicts(res), res["results"][0]["gap_loss"]), (["PASS"], 1469.78))
        self.assertIn("gap loss Rs 1,469.78 if it opens at the lower limit 89.44 (limit Rs 1,500)",
                      res["results"][0]["info"])
        self.assertIn("max loss Rs 497.69 (limit Rs 500)", " | ".join(res["results"][0]["info"]))
        tight = swing_buy(qty=183, stop_loss=97.0, take_profit=104.14, plan={"stop": 97.0, "target": 104.14})
        res = run_swing([tight], c=small)                                # the same max loss, more shares
        self.assertEqual(res["results"][0]["max_loss"], 499.9)
        self.assertEqual(res["results"][0]["reasons"], ["gap: Rs 1,880.90 if it opens at the lower limit 89.44, over "
                                                        "your Rs 1,500 gap limit (swing.max_gap_loss_pkr)"])
        hand = dict(small, swing=dict(small["swing"], max_gap_loss_pkr=1400))
        self.assertIn("gap: Rs 1,469.78 if it opens at the lower limit 89.44, over your Rs 1,400 gap limit",
                      reasons(run_swing([swing_buy(qty=143)], c=hand)))
        hand["swing"]["max_gap_loss_pkr"] = 2000
        self.assertEqual(verdicts(run_swing([tight], c=hand)), ["PASS"])
        # 0 is a Rs 0 limit, as in psx-budget: the gap loss must have a cap (D55), so every swing buy is blocked
        # (review F22: psx-risk read 0 as "no gap limit" while psx-budget refused every buy)
        hand["swing"]["max_gap_loss_pkr"] = 0
        res = run_swing([tight], c=hand)
        self.assertEqual(res["results"][0]["reasons"], ["gap: Rs 1,880.90 if it opens at the lower limit 89.44, over "
                                                        "your Rs 0 gap limit (swing.max_gap_loss_pkr)"])
        self.assertIn("gap loss Rs 1,880.90 if it opens at the lower limit 89.44 (limit Rs 0)", res["results"][0]["info"])
        # null with no max_loss_per_trade_pkr: no gap limit, in both tools
        res = run_swing([swing_buy()], c=cfg(style="swing", max_loss_per_trade_pkr=0))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("gap loss Rs 14,759.46 if it opens at the lower limit 89.44", res["results"][0]["info"])

    def test_psx_budget_and_psx_risk_read_the_gap_limit_the_same_way(self):
        """One config, two tools, one gap limit (review F22): the limit size.py sizes to is the one check.py shows
        and blocks at, and an order psx-budget sizes never meets psx-risk's gap gate."""
        size = load("psx-budget", "size")
        plan = contract("plan-swing.json")
        tight = swing_buy(qty=183, stop_loss=97.0, take_profit=104.14, plan={"stop": 97.0, "target": 104.14})
        limits = {}
        for per, cap in ((5000, None), (500, None), (500, 0), (500, 1400), (500, 2000), (0, None), (0, 0), (0, 900)):
            c = cfg(style="swing", max_loss_per_trade_pkr=per)
            c["swing"] = dict(c["swing"], max_gap_loss_pkr=cap)
            s = size.size_trade(plan, c, today=TODAY)
            limits[(per, cap)] = (c, s["gap_limit"])
            r = run_swing([tight], c=c)["results"][0]
            line = [x for x in r["info"] if x.startswith("gap loss ")]
            gap = [x for x in r["reasons"] if x.startswith("gap: ")]
            if s["gap_limit"] is None:
                self.assertEqual((line[0].endswith("89.44"), gap), (True, []), (per, cap))
            else:
                self.assertTrue(line[0].endswith("(limit %s)" % check.rs(s["gap_limit"])), (per, cap, line))
                self.assertEqual(bool(gap), r["gap_loss"] > s["gap_limit"], (per, cap))
            if s["qty"]:
                mine = run_swing([size.make_order(s)], c=c)["results"][0]
                self.assertEqual([x for x in mine["reasons"] if x.startswith("gap: ")], [], (per, cap))
            else:
                self.assertIn("gap limit (swing.max_gap_loss_pkr)", s["refusal"], (per, cap))
        for (per, cap), (c, want) in sorted(limits.items(), key=str):
            self.assertEqual(check.swing_gap_cap(check.swing_settings(c), per), want, (per, cap))

    def test_every_sell_pays_commission_in_swing(self):
        prices, times, details = check.load_prices(read(P1520))
        for style, want in (("day", "sell costs about Rs 22.98"), ("swing", "sell costs about Rs 623.66")):
            c = cfg(style=style, costs=dict(check.COSTS, day_trade_one_side=True))
            for lot_day in (TODAY, "2026-10-28"):
                pf = read(PF_PATH)
                pf["positions"][0]["lots"][0]["date"] = lot_day
                res = check.check(read(FLAT_PATH)["orders"], c, portfolio=pf, prices=prices, price_times=times,
                                  now=at("2026-10-29 15:20"), market=MKT)
                got = res["results"][0]["info"][0]
                self.assertIn(want if lot_day == TODAY else "sell costs about Rs 623.66", got, (style, lot_day))
        o = swing_buy()
        one = check.check([o], swing(costs=dict(check.COSTS, day_trade_one_side=True)), now=at(PRE), market=MKT)
        self.assertEqual(one["results"][0]["max_loss"], 4997.81)        # trade_costs: the stop-out pays both sides


class SwingLog(unittest.TestCase):
    """psx/state/broker.json in swing: buys with no fill yet (D57), an earlier buy with no fill (D33)."""
    WAIT = "Rs 142,965.27"                  # the DEMO buy with no fill: 1,436 x 99.38 + Rs 255.59 buy costs

    def test_two_buys_that_need_the_same_cash(self):
        pf = {"cash": 200000.0, "positions": []}
        self.assertEqual(verdicts(run_swing([swing_ogdc()], now="2026-10-29 09:25", portfolio=pf)), ["PASS"])
        res = run_swing([swing_ogdc()], now="2026-10-29 09:25", portfolio=pf, state=contract("broker-approved-old.json"))
        self.assertEqual(res["results"][0]["reasons"], [
            "cash: needs Rs 140,500 plus Rs 251.64 costs but there is only Rs 57,034.73 cash once today's buys with no "
            "fill yet are taken off (DEMO: %s, costs included); that would be margin (borrowed money), and this pack "
            "never uses margin" % self.WAIT])
        self.assertEqual(res["cash_after"], 57034.73)
        self.assertIn("no fill recorded yet for buy DEMO, which you said yes to today: %s (costs included) is taken off "
                      "the cash here, and it counts as an open position and as open risk." % self.WAIT,
                      " ".join(res["warnings"]))
        placed = contract("broker-placed-unfilled.json")                  # placed, still no fill: the same
        self.assertIn("only Rs 57,034.73 cash", reasons(run_swing([swing_ogdc()], now="2026-10-29 09:25",
                                                                  portfolio=pf, state=placed)))
        filled = {"orders": placed["orders"] + [{"at": "2026-10-29T09:31:00+05:00", "date": TODAY, "status": "filled",
                                                 "symbol": "DEMO", "side": "buy", "qty": 1436, "price": 99.38}]}
        self.assertEqual(verdicts(run_swing([swing_ogdc()], now="2026-10-29 09:35", portfolio=pf, state=filled)),
                         ["PASS"])                                        # a fill: its cash is in the ledger now
        res = run_swing([swing_buy()], now="2026-10-29 09:25", portfolio=pf, state=contract("broker-approved-old.json"))
        self.assertEqual(res["results"][0]["reasons"], ["duplicate: a buy DEMO was already placed today"])
        self.assertIn("open before this: 0 of 3 positions, Rs 0 of", " ".join(res["results"][0]["info"]))   # not itself

    def test_a_waiting_buy_counts_as_an_open_position_and_as_open_risk(self):
        pf = {"cash": 500000.0, "positions": []}
        st = contract("broker-approved-old.json")
        res = run_swing([swing_ogdc()], now="2026-10-29 09:25", portfolio=pf, state=st,
                        c=swing(swing={"max_open_positions": 1}))
        self.assertEqual(res["results"][0]["reasons"], ["open positions: 1 open (DEMO with no fill yet) plus this one "
                                                        "is 2, your limit is 1 (swing.max_open_positions)"])
        res = run_swing([swing_ogdc()], now="2026-10-29 09:25", portfolio=pf, state=st,
                        c=swing(swing={"max_open_risk_pkr": 9000}))
        self.assertEqual(res["results"][0]["reasons"], [
            "open risk: Rs 4,997.81 at the stops of your open positions plus Rs 4,995.22 for this one = Rs 9,993.03, "
            "over your Rs 9,000 limit (swing.max_open_risk_pkr; costs included)"])
        self.assertIn("open before this: 1 of 3 positions, Rs 4,997.81 of Rs 9,000 open risk (costs included)",
                      res["results"][0]["info"])

    def test_a_waiting_buy_with_no_price_in_the_log_blocks(self):
        st = contract("broker-approved-old.json")
        st["orders"][0].pop("entry_ref")
        res = run_swing([swing_ogdc()], now="2026-10-29 09:25", portfolio={"cash": 500000.0, "positions": []}, state=st)
        self.assertEqual(res["results"][0]["reasons"], ["cash: an earlier buy today (DEMO) has no price in the log, so "
                                                        "the cash cannot be checked. Record its fill, or record it "
                                                        "expired"])
        self.assertIn("no fill recorded yet for buy DEMO, which you said yes to today: the log has no price for it, so "
                      "its cash cannot be taken off here, and it counts as an open position", " ".join(res["warnings"]))

    def test_the_waiting_buy_warning_names_the_exact_line_to_clear_it(self):
        """ticket.py result --status expired alone is refused (it needs the order): name each one (review F61).
        Never --index 1: that is the newest ticket in psx/orders.json, which can be another order."""
        res = run_swing([swing_ogdc()], now="2026-10-29 09:25", portfolio={"cash": 500000.0, "positions": []},
                        state=contract("broker-approved-old.json"))
        words = " ".join(res["warnings"])
        self.assertIn("If it ends at the close with no fill, record it expired: ticket.py result --fingerprint %s "
                      "--status expired" % BUY_FP, words)
        self.assertNotIn("(ticket.py result --status expired)", words)
        two = {"orders": [yes(swing_buy(), at="09:16"), yes(swing_ogdc(qty=50), at="09:18")]}
        res = run_swing([swing_order("HBL", 10, 279.0, 270.0, 297.0)], now="2026-10-29 09:25",
                        portfolio={"cash": 500000.0, "positions": []}, state=two)
        self.assertIn("no fill recorded yet for buy DEMO, buy OGDC, which you said yes to today: Rs 157,040.43 (costs "
                      "included) is taken off the cash here, and they count as open positions and as open risk. When "
                      "they fill, record each one (psx-broker: ticket.py fill). If they end at the close with no fill, "
                      "record each one expired: ticket.py result --fingerprint %s --status expired; ticket.py result "
                      "--fingerprint %s --status expired" % (BUY_FP, check.fingerprint(swing_ogdc(qty=50))),
                      " ".join(res["warnings"]))                          # 142,965.27 + 14,075.16
        bare = {"orders": [dict(yes(swing_ogdc(qty=50), at="09:18"), fingerprint=None)]}
        res = run_swing([swing_buy()], now="2026-10-29 09:25", portfolio={"cash": 500000.0, "positions": []}, state=bare)
        self.assertIn("record it expired: ticket.py result --symbol OGDC --date 2026-10-29 --status expired",
                      " ".join(res["warnings"]))

    def test_a_buy_filled_in_part_keeps_the_rest_waiting(self):
        """result --filled-qty 10 of a 143-share buy: the other 133 may still fill today, so their cash and their
        risk still count (review F60: one fill used to end the whole order)."""
        c = swing(max_loss_per_trade_pkr=500, max_daily_loss_pkr=1000)     # open risk limit: Rs 1,000
        buy = swing_buy(qty=143, plan={"max_loss": 497.69})
        recs = [yes(buy, at="09:20"), yes(buy, at="09:22", status="placed")]
        ten = {"at": "2026-10-29T09:40:00+05:00", "date": TODAY, "status": "filled", "symbol": "DEMO", "side": "buy",
               "qty": 10, "price": 99.38}
        state = {"orders": recs + [ten]}
        pf = {"cash": 40000.0, "positions": [held("HBL", 50, 270.0, 262.0), held("DEMO", 10, 99.38, 96.25, day=TODAY)]}
        ogdc = swing_order("OGDC", 50, 281.0, 272.0, 299.0)                 # max loss Rs 499.52
        res = run_swing([ogdc], now="2026-10-29 09:45", c=c, portfolio=pf, state=state)
        # HBL Rs 447.64 + the 10 DEMO held Rs 34.80 + the other 133: 497.69 x 133 / 143 = Rs 462.89
        self.assertEqual(res["results"][0]["reasons"], [
            "open risk: Rs 945.33 at the stops of your open positions plus Rs 499.52 for this one = Rs 1,444.85, over "
            "your Rs 1,000 limit (max_daily_loss_pkr, as swing.max_open_risk_pkr is null; costs included)"])
        self.assertIn("buy DEMO is filled in part: 10 of 143 shares are recorded. The other 133 may still fill today: "
                      "Rs 13,241.21 (costs included) is taken off the cash here, and they count as open risk. Record "
                      "each fill with ticket.py fill. A PSX order ends at the close, so the rest stops counting after "
                      "today", res["warnings"])
        self.assertFalse([w for w in res["warnings"] if w.startswith("no fill recorded yet")])
        self.assertEqual(res["cash_after"], 26758.79)                     # 40,000 less 133 x 99.38 and their costs
        waiting, _, _ = check.open_orders(state, TODAY)
        self.assertEqual([(w["filled"], w["left"]) for w in waiting], [(10.0, 133.0)])
        rest = dict(ten, qty=133, at="2026-10-29T10:30:00+05:00")          # the rest filled: nothing waits
        self.assertEqual(check.open_orders({"orders": state["orders"] + [rest]}, TODAY)[0], [])
        self.assertEqual(check.open_orders(state, "2026-10-30")[2], [])     # it ended at the close: no old buy
        res = run_swing([ogdc], now="2026-10-29 09:45", c=swing(), portfolio=dict(pf, cash=30000.0), state=state)
        self.assertIn("cash: leaves Rs 2,683.63 cash after costs once today's buys with no fill yet are taken off "
                      "(DEMO: Rs 13,241.21, costs included)", reasons(res))  # 30,000 - 13,241.21 - 14,075.16

    def test_the_buffer_counts_a_waiting_buy_as_held_like_psx_budget(self):
        """Once today's waiting buys come off the cash, they count as held at qty x entry_ref (their costs are gone):
        the account total psx-budget uses (review F23). Cash 240,000 less DEMO's Rs 142,965.27 is Rs 97,034.73, plus
        Rs 142,709.68 held: Rs 239,744.41, so the 10% buffer is Rs 23,974.44, not Rs 24,000."""
        pf, st = {"cash": 240000.0, "positions": []}, contract("broker-approved-old.json")
        o = swing_order("OGDC", 259, 281.55, 264.55, 299.55)               # leaves Rs 23,982.68
        self.assertEqual(verdicts(run_swing([o], now="2026-10-29 09:25", portfolio=pf, state=st)), ["PASS"])
        o = swing_order("OGDC", 259, 281.6, 264.6, 299.6)
        self.assertEqual(run_swing([o], now="2026-10-29 09:25", portfolio=pf, state=st)["results"][0]["reasons"], [
            "cash: leaves Rs 23,969.70 cash after costs once today's buys with no fill yet are taken off (DEMO: "
            "Rs 142,965.27, costs included), under your 10% buffer of Rs 23,974.44"])

    def test_an_order_psx_budget_sizes_to_the_cash_passes_psx_risk(self):
        """size.py and check.py on one account, with a buy waiting (review F23): the order psx-budget sizes to the
        cash passes psx-risk, and one more share does not."""
        size = load("psx-budget", "size")
        st = {"orders": [yes(swing_ogdc(qty=200), at="09:16")]}           # OGDC 200 at 281.00, no fill yet
        for cash, qty in ((170068.5, 972), (175000.0, 1016)):
            s = size.size_trade(contract("plan-swing.json"), swing(), cash=cash, holdings={},
                                waiting=size.waiting_buys(st, TODAY), today=TODAY)
            self.assertEqual((s["qty"], s["bound_by"]), (qty, "cash"))
            o = size.make_order(s)
            pf = {"cash": cash, "positions": []}
            self.assertEqual(verdicts(run_swing([o], portfolio=pf, state=st)), ["PASS"], cash)
            more = run_swing([dict(o, qty=qty + 1)], portfolio=pf, state=st)
            self.assertIn("cash: leaves", reasons(more))
            self.assertIn("under your 10% buffer of", reasons(more))

    def test_an_invest_order_gets_the_cash_rule_in_a_day_config(self):
        inv = contract("orders-invest.json")["orders"]                    # TRIM 12 LUCK, then ADD 100 DEMO
        lt = cfg(require_stop=False, cash_buffer_pct=0, max_position_pct=20, max_order_pkr=100000, max_trades_per_day=3)
        pf = {"cash": 15000.0, "positions": [{"ticker": "LUCK", "shares": 200, "lots": [
            {"id": "LUCK-1", "date": None, "shares": 200, "cost": 90000.0}]}]}
        now = "2026-10-29 10:25"
        res = check.check(inv, lt, portfolio=pf, now=at(now), market=swing_market(), orders_date=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "entry"])
        self.assertIn("no stop: the max loss is not capped, so it is not counted in the daily loss", warnings(res, 1))
        wait = swing_ogdc(qty=50)
        st = {"orders": [yes(wait, at="10:21")]}                          # OGDC 50 at 281.00, no fill yet today
        res = check.check(inv, lt, portfolio=pf, state=st, now=at(now), market=swing_market(), orders_date=TODAY)
        self.assertEqual(verdicts(res), ["PASS", "BLOCK"])
        # 15,000 + the TRIM sell (5,616 less 10.06 costs) less the OGDC buy (14,050 plus 25.16 costs) = 6,530.78
        self.assertIn("cash: needs Rs 9,840 plus Rs 17.62 costs but there is only Rs 6,530.78 cash once today's buys "
                      "with no fill yet are taken off (OGDC: Rs 14,075.16, costs included)", reasons(res, 1))
        self.assertIn("is taken off the cash here for the psx-invest orders", " ".join(res["warnings"]))
        self.assertNotIn("style", res)                                   # a day folder: no swing gates
        res = check.check([inv[1]], dict(lt, require_stop=True), now=at(now), market=swing_market(), orders_date=TODAY)
        self.assertIn("stop: no stop", reasons(res))                     # require_stop true: blocked (3.7)

    def test_a_day_order_keeps_the_v1_warning(self):
        st = {"orders": [yes(swing_ogdc(qty=50), at="10:10")]}
        o = limit_buy("HBL", 100, 279.0, 270.0, 297.0)
        for state in (None, st):
            res = check.check([o], cfg(), state=state, now=at(NOW), market=swing_market())
            self.assertEqual((verdicts(res), res["cash_after"]), (["PASS"], 472050.03), state)   # 27,900 + 49.97
        self.assertIn("no fill recorded yet for buy OGDC, which you said yes to today: the cash here does not take it "
                      "off. When it fills, record it (psx-broker: ticket.py fill)", res["warnings"])

    def test_an_unrecorded_fill_of_an_earlier_day_blocks_in_swing_only(self):
        want = ("fill: the buy of DEMO placed on Thu 2026-10-29 has no fill recorded. If it filled, record the fill "
                "(ticket.py fill). If not, record it expired: ticket.py result --fingerprint %s --status expired" % BUY_FP)
        o = swing_ogdc()
        st = contract("broker-placed-unfilled.json")                      # placed on Thu 2026-10-29, no fill
        res = run_swing([o], now=FRI, state=st)
        self.assertEqual(res["results"][0]["reasons"], [want])
        self.assertEqual(verdicts(run_swing([o], now=FRI, state=contract("broker-expired.json"))), ["PASS"])
        for day in (TODAY, "2026-10-30"):                                 # a fill on or after that day clears it
            fill = {"at": day + "T11:00:00+05:00", "date": day, "status": "filled", "symbol": "DEMO", "side": "buy",
                    "qty": 1436, "price": 99.38}
            res = run_swing([o], now=FRI, state={"orders": st["orders"] + [fill]})
            self.assertEqual(verdicts(res), ["PASS"], day)
        early = dict(fill, date="2026-10-28", at="2026-10-28T11:00:00+05:00")
        self.assertIn(want, reasons(run_swing([o], now=FRI, state={"orders": [early] + st["orders"]})))
        day = check.check([o], cfg(), now=at(FRI), state=st, market=swing_market("2026-10-30"))
        self.assertNotIn("fill:", reasons(day))                           # day style: no block, and no new words
        self.assertEqual(day["warnings"], check.check([o], cfg(), now=at(FRI), market=swing_market("2026-10-30"))["warnings"])
        same_day = check.check([o], cfg(), now=at("2026-10-29 10:20"), state=st, market=swing_market())
        self.assertIn("no fill recorded yet for buy DEMO, which you said yes to today: the cash here does not take it "
                      "off", " ".join(same_day["warnings"]))              # the v1 warning, on that same day

    def test_an_approved_buy_of_an_earlier_day_blocks_until_it_is_closed(self):
        st = contract("broker-approved-old.json")                         # a yes on Thu 2026-10-29, never placed
        o = swing_ogdc()
        res = run_swing([o], now=FRI, state=st)
        self.assertEqual(res["results"][0]["reasons"], [
            "fill: the buy of DEMO you said yes to on Thu 2026-10-29 has no fill recorded. If it filled, record the fill "
            "(ticket.py fill). If not, record it expired: ticket.py result --fingerprint %s --status expired" % BUY_FP])
        base = {k: st["orders"][0][k] for k in ("index", "fingerprint", "symbol", "side", "plain")}
        for status in ("skipped", "cancelled", "expired"):
            for of in ({"of": TODAY}, {}):                               # with "of", and without (it closes the newest)
                close = dict(base, at="2026-10-30T09:00:00+05:00", date="2026-10-30", status=status, **of)
                res = run_swing([o], now=FRI, state={"orders": st["orders"] + [close]})
                self.assertEqual(verdicts(res), ["PASS"], (status, of))
        again = [dict(st["orders"][0], at="2026-10-30T09:20:00+05:00", date="2026-10-30")]    # the same order today
        orders, _ = check.order_book({"orders": st["orders"] + again})
        self.assertEqual([(x["day"], x["status"]) for x in orders], [(TODAY, "approved"), ("2026-10-30", "approved")])
        for status in ("declined", "failed"):                            # a "no" to today's ticket is not about Thu
            close = dict(base, at="2026-10-30T09:00:00+05:00", date="2026-10-30", status=status)
            self.assertIn("fill: the buy of DEMO you said yes to on Thu 2026-10-29",
                          reasons(run_swing([o], now=FRI, state={"orders": st["orders"] + [close]})), status)

    def test_cancelled_and_expired_free_the_trade_count_and_the_daily_loss(self):
        for c in (cfg(), swing()):
            for status in ("skipped", "cancelled", "expired"):
                recs = [yes(swing_ogdc(), at="09:20"), yes(swing_ogdc(), at="09:40", status=status, of=TODAY)]
                res = check.check([limit_buy("HBL", 10, 279.0, 270.0, 297.0)], c, state={"orders": recs},
                                  now=at("2026-10-29 10:20"))
                self.assertEqual(res["today"], {"orders_placed": 0, "entries_placed": 0, "loss_at_risk": 0.0},
                                 (c["style"], status))
        recs = contract("broker-expired.json")["orders"]                  # "of" another day: not one of today's
        self.assertEqual(check.placed_today({"orders": recs + [yes(swing_buy(), day="2026-10-30")]}, "2026-10-30")[1],
                         ["buy DEMO"])

    def test_stop_bonus_and_cash_records_are_no_order_no_trade_and_no_fill(self):
        other = [{"at": "2026-10-29T09:00:00+05:00", "date": TODAY, "status": "stop", "symbol": "DEMO", "from": 96.25,
                  "to": 97.0, "scale": None, "why": "move a stop up", "plain": "stop 96.25 to 97.00"},
                 {"at": "2026-10-29T09:01:00+05:00", "date": TODAY, "status": "bonus", "symbol": "DEMO", "qty": 28,
                  "plain": "28 bonus shares"},
                 {"at": "2026-10-29T09:02:00+05:00", "date": TODAY, "status": "cash", "from": 1000.0, "to": 46000.0,
                  "plain": "cash 1,000.00 to 46,000.00"}]
        self.assertEqual(check.order_book({"orders": other}), ([], []))
        self.assertEqual(check.placed_today({"orders": other}, TODAY), ([], []))
        at36 = "2026-10-30T09:36:00+05:00"
        for plain, rec_type, want in (("SELL 143 DEMO, stop-limit, trigger 96.25, limit 95.28", None, "stop-limit, 09:36"),
                                      ("SELL DEMO, REG board, quantity 143, STOP LIMIT trigger price 96.25", None,
                                       "stop-limit, 09:36"),
                                      ("SELL 143 DEMO, limit 97.00, good for today, REG board", None, "limit, 09:36"),
                                      ("SELL 143 DEMO, market, good for today", None, "market, 09:36"),
                                      ("made up", "stop_limit", "stop-limit, 09:36"), ("made up", None, "09:36")):
            self.assertEqual(check.order_words({"plain": plain, "type": rec_type, "at": at36}), want, plain)
        st = {"orders": contract("broker-approved-old.json")["orders"] + other}
        waiting, sells, old = check.open_orders(st, TODAY)
        self.assertEqual([(w["symbol"], w["status"], w["qty"]) for w in waiting], [("DEMO", "approved", 1436)])
        self.assertEqual((sells, old), ([], []))                          # a bonus record is not a fill
        res = run_swing([swing_ogdc()], now="2026-10-29 09:25", state=st, portfolio={"cash": 500000.0, "positions": []})
        self.assertEqual(res["today"], {"orders_placed": 1, "entries_placed": 1, "loss_at_risk": 4997.81})
        self.assertIn("open before this: 1 of 3 positions", " | ".join(res["results"][0]["info"]))


def protect(qty=143, **over):
    """The protect order of the contract (ticket.py protect): a stop-limit sell, trigger 96.25, limit 95.28."""
    o = dict(contract("orders-protect.json")["orders"][0], qty=qty)
    o.update(over)
    return o


def flatten(qty=143, **over):
    o = {"symbol": "DEMO", "side": "sell", "qty": qty, "type": "market", "time_in_force": "day", "board": "REG",
         "why": "close the DEMO swing position"}
    o.update(over)
    return o


class SwingSells(unittest.TestCase):
    """Sells in swing: the stop-limit (D32), no second sell of the same shares (D58), and no swing gate on an exit."""
    CANCEL = ("short: 143 DEMO shares are already in a sell placed today (stop-limit, 09:36). Cancel it in your broker "
              "app, then record it: ticket.py result --fingerprint %s --status cancelled" % PROTECT_FP)

    def sell(self, orders, state=None, pf=None, c=None, now=FRI, **kw):
        return run_swing(orders, now=now, portfolio=pf or ledger(qty=143), state=state, c=c, **kw)

    def test_a_stop_limit_sell_of_held_shares_passes_as_an_exit(self):
        res = self.sell([protect()])
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["reasons"]), ("PASS", "exit", []))
        self.assertEqual((r["fingerprint"], r["entry_ref"], r["entry_ref_from"]), (PROTECT_FP, 95.28, "stop limit"))
        self.assertIn("stop-limit: the trigger 96.25 is under the last close (101.60)", r["info"])
        self.assertIn("value Rs 13,625.04 at 95.28 (the stop-limit's limit price), sell costs about Rs 24.40", r["info"])
        res = self.sell([protect(qty=1436)], pf=ledger())                 # orders-protect.json as it is
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(check.fingerprint(contract("orders-protect.json")["orders"][0]), res["results"][0]["fingerprint"])
        self.assertEqual(verdicts(self.sell([protect()], now="2026-10-30 09:05")), ["PASS"])     # not a market order

    def test_a_stop_limit_buy_or_a_limit_over_the_trigger_is_blocked(self):
        only = "format: a stop-limit order is for selling shares you hold only"
        self.assertIn(only, reasons(self.sell([protect(side="buy")])))
        res = self.sell([protect()], pf={"cash": 100000.0, "positions": []})      # nothing held: no exit
        self.assertIn(only, reasons(res))
        self.assertIn("short: selling more DEMO than you hold", reasons(res))
        self.assertEqual(self.sell([protect(limit_price=97.0)])["results"][0]["reasons"],
                         ["format: the limit price 97.00 of a stop-limit sell must be at or under its trigger 96.25"])
        self.assertEqual(verdicts(self.sell([protect(limit_price=96.25)])), ["PASS"])      # at the trigger is fine
        self.assertIn("format: a stop-limit order needs a trigger_price", reasons(self.sell([protect(trigger_price=None)])))
        self.assertIn("format: a stop-limit order needs a limit_price", reasons(self.sell([protect(limit_price=None)])))
        self.assertIn("format: trigger_price 96.255 is not on the Rs 0.01 tick",
                      reasons(self.sell([protect(trigger_price=96.255)])))
        self.assertIn("format: type must be limit, market or stop_limit (a stop-limit sell), got 'stop'",
                      reasons(self.sell([protect(type="stop")])))
        day = check.check([protect()], cfg(), now=at(FRI), portfolio=ledger(qty=143))
        self.assertIn("format: type must be limit or market, got 'stop_limit'", reasons(day))   # day: as v1

    def test_a_trigger_at_or_over_the_price_now_is_blocked(self):
        def run_at(price, **kw):
            return self.sell([protect()], prices={"DEMO": price}, **kw)
        # a gap down with no ex-date: the stop rule is met, and flatten is the step, never --scale (review F16)
        met = ("The price now is 82.00, at or under your plan's stop 96.25: the stop rule is met. To sell: ticket.py "
               "flatten --symbol DEMO (--limit P in the pre-open). You decide.")
        self.assertEqual(run_at(82.0)["results"][0]["reasons"],
                         ["format: the trigger 96.25 is at or over the price now (82.00), so it would fire at once. " + met])
        self.assertEqual(verdicts(run_at(96.25)), ["BLOCK"])
        self.assertIn("the stop rule is met", reasons(run_at(96.25)))
        self.assertEqual(verdicts(run_at(96.26)), ["PASS"])
        self.assertIn("stop-limit: the trigger 96.25 is under the price now (96.26)", run_at(96.26)["results"][0]["info"])
        scale = ("A bonus, right or split lowers the price, and your plan is not scaled for it yet. You choose: scale it "
                 "(ticket.py stop --symbol DEMO --scale F; psx-alerts gives F) or keep it (--scale 1).")
        ex = copy.deepcopy(swing_market("2026-10-30"))                      # an ex-date: the company page LDCP of today
        ex["limits"]["DEMO"] = {"lower": 73.8, "upper": 90.2, "ldcp": 82.0, "from": "company page",
                                "label": "company page"}
        res = self.sell([protect()], market=ex)                            # 19% under the close before, no tag
        self.assertEqual(res["results"][0]["reasons"], [
            "format: the trigger 96.25 is at or over the last close (82.00), so it would fire at once. DEMO's LDCP today "
            "(82.00) is 19% under the close 101.60 of Thu 2026-10-29, with no tag: a split? " + scale])
        xb = copy.deepcopy(ex)                                              # an XB tag on today's company page
        xb["pages"]["DEMO"] = dict(xb["pages"]["DEMO"], ex=["XB"], today=True)
        self.assertIn("so it would fire at once. DEMO shows XB on today's company page. " + scale,
                      reasons(self.sell([protect()], market=xb)))
        old_xr = copy.deepcopy(swing_market("2026-10-30"))                  # an XR tag in the closing file before
        old_xr["closes"]["DEMO"] = dict(old_xr["closes"]["DEMO"], flags=["XR"])
        self.assertIn("DEMO shows XR in the closing file of Thu 2026-10-29. " + scale,
                      reasons(run_at(82.0, market=old_xr)))
        # scaled on or after the day the tag shows: no scale words. The old ticket gets the line for a new one,
        # and a price under the scaled stop meets the stop rule
        done = ledger(qty=143, stop=80.2, target=88.03, scaled=[{"date": "2026-10-30", "factor": 0.8333, "why": "XB"}])
        self.assertEqual(self.sell([protect()], market=xb, pf=done)["results"][0]["reasons"], [
            "format: the trigger 96.25 is at or over the last close (82.00), so it would fire at once. Your plan's stop "
            "is 80.20: ticket.py protect --symbol DEMO makes a new stop-limit from it."])
        new = protect(trigger_price=80.2, limit_price=79.39)
        self.assertEqual(verdicts(self.sell([new], market=xb, pf=done)), ["PASS"])
        words = reasons(self.sell([new], market=xb, pf=done, prices={"DEMO": 79.0}))
        self.assertIn("The price now is 79.00, at or under your plan's stop 80.20: the stop rule is met.", words)
        self.assertNotIn("--scale", words)
        # an XD tag: a cash payout. The stop stays (D27), so a fall by the payout can meet it: never --scale
        xd = copy.deepcopy(swing_market("2026-10-30"))
        xd["limits"]["DEMO"] = {"lower": 85.5, "upper": 104.5, "ldcp": 95.0, "from": "company page",
                                "label": "company page"}
        xd["pages"]["DEMO"] = dict(xd["pages"]["DEMO"], ex=["XD"], today=True)
        self.assertEqual(self.sell([protect()], market=xd)["results"][0]["reasons"], [
            "format: the trigger 96.25 is at or over the last close (95.00), so it would fire at once. DEMO shows XD on "
            "today's company page: a cash payout. The stop stays as it is. The last close is 95.00, at or under your "
            "plan's stop 96.25: the stop rule is met. To sell: ticket.py flatten --symbol DEMO (--limit P in the "
            "pre-open). You decide."])
        hand = protect(trigger_price=102.0, limit_price=101.0)              # a trigger over the price, the stop not met
        self.assertEqual(self.sell([hand], prices={"DEMO": 101.5})["results"][0]["reasons"], [
            "format: the trigger 102.00 is at or over the price now (101.50), so it would fire at once. Your plan's "
            "stop is 96.25: ticket.py protect --symbol DEMO makes a new stop-limit from it."])
        none = {"limits": {}, "unknown": {}, "closes": {}, "pages": {}, "universe": None, "universe_file": None,
                "warnings": []}
        res = self.sell([protect()], market=none)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no price for DEMO now and no last close, so the trigger 96.25 was not checked", warnings(res))

    def test_a_second_sell_of_the_same_shares_is_blocked_until_the_first_is_cancelled(self):
        res = self.sell([flatten()], state=contract("broker-protect-open.json"))
        self.assertEqual(res["results"][0]["reasons"], [self.CANCEL])
        self.assertEqual(res["results"][0]["kind"], "exit")
        # the "short:" block says it, with the cancel line: no "exit, not blocked" under a BLOCK (review F58)
        self.assertEqual(warnings(res), "")
        self.assertNotIn("not blocked", check.report(res))
        self.assertEqual(verdicts(self.sell([flatten()], state=contract("broker-protect-cancelled.json"))), ["PASS"])
        day = check.check([flatten()], cfg(), now=at(FRI), portfolio=ledger(qty=143),
                          state=contract("broker-protect-open.json"))
        self.assertEqual((verdicts(day), warnings(day)),
                         (["PASS"], "exit, not blocked: duplicate: a sell DEMO was already placed today"))   # v1
        st = contract("broker-protect-open.json")
        more = ledger(qty=200)                                              # 200 held, 143 in the open sell
        self.assertEqual(verdicts(self.sell([flatten(qty=57)], state=st, pf=more)), ["PASS"])
        self.assertIn("short: 143 DEMO shares are already in a sell placed today",
                      reasons(self.sell([flatten(qty=58)], state=st, pf=more)))
        self.assertEqual(verdicts(self.sell([protect()], state=st)), ["PASS"])    # the same order, checked again
        res = self.sell([flatten(limit_price=97.0, type="limit")], state=st)
        self.assertIn(self.CANCEL, reasons(res))                            # the block holds for a limit sell too

    def test_a_filled_sell_frees_its_shares_and_two_open_sells_are_named(self):
        st = contract("broker-protect-open.json")
        fill = {"at": "2026-10-30T10:30:00+05:00", "date": "2026-10-30", "status": "filled", "symbol": "DEMO",
                "side": "sell", "qty": 143, "price": 96.0}
        two = ledger(qty=286)
        res = self.sell([flatten()], now="2026-10-30 10:40", state={"orders": st["orders"] + [fill]}, pf=ledger(qty=143))
        self.assertEqual(verdicts(res), ["PASS"])                           # the protect sell filled: the ledger is 143
        lim = flatten(type="limit", limit_price=99.0)
        recs = st["orders"] + [yes(lim, day="2026-10-30", at="10:05", status="placed")]
        res = self.sell([flatten(qty=1, type="limit", limit_price=99.5)], now="2026-10-30 10:40", state={"orders": recs},
                        pf=two)
        self.assertEqual(res["results"][0]["reasons"], [
            "short: 286 DEMO shares are already in 2 sells placed today (stop-limit, 09:36; 10:05). Cancel them in your "
            "broker app, then record each one: ticket.py result --fingerprint %s --status cancelled; ticket.py result "
            "--fingerprint %s --status cancelled" % (PROTECT_FP, check.fingerprint(lim))])

    def test_a_sell_filled_in_part_still_holds_the_rest(self):
        """The stop-limit sold 100 of 143 at 10:00: the other 43 may still sell at the broker today, so a second
        sell of them is a blank sale (review F25: one fill used to end the whole order)."""
        st = contract("broker-protect-open.json")                           # 143 DEMO placed at 09:36
        part = {"at": "2026-10-30T10:00:00+05:00", "date": "2026-10-30", "status": "filled", "symbol": "DEMO",
                "side": "sell", "qty": 100, "price": 96.1}
        state, now, pf = {"orders": st["orders"] + [part]}, "2026-10-30 10:05", ledger(qty=43)
        res = self.sell([flatten(qty=43)], state=state, pf=pf, now=now)
        self.assertEqual(res["results"][0]["reasons"], [
            "short: 43 DEMO shares are already in a sell placed today (stop-limit, 09:36; 100 of 143 filled). Cancel it "
            "in your broker app, then record it: ticket.py result --fingerprint %s --status cancelled" % PROTECT_FP])
        self.assertEqual(verdicts(self.sell([protect(qty=43)], state=state, pf=pf, now=now)), ["BLOCK"])
        _, sells, _ = check.open_orders(state, "2026-10-30")
        self.assertEqual([(s["fingerprint"], s["filled"], s["left"]) for s in sells], [(PROTECT_FP, 100.0, 43.0)])
        rest = dict(part, qty=43, at="2026-10-30T10:02:00+05:00")          # the rest filled too
        cancelled = dict(st["orders"][1], at="2026-10-30T10:03:00+05:00", status="cancelled")
        for more in (rest, cancelled):
            self.assertEqual(verdicts(self.sell([flatten(qty=43)], state={"orders": state["orders"] + [more]}, pf=pf,
                                                now=now)), ["PASS"], more["status"])
        # the record ticket.py result writes after a part fill (SPEC v2 2.5: "of" is the order's day) closes the rest,
        # as cancelled or expired (review F26); one that closes an order of another day does not
        close = {k: st["orders"][1][k] for k in ("index", "fingerprint", "symbol", "side", "plain")}
        for status in ("cancelled", "expired"):
            close.update(at="2026-10-30T10:03:00+05:00", date="2026-10-30", status=status, of="2026-10-30")
            later = {"orders": state["orders"] + [close]}
            self.assertEqual(verdicts(self.sell([flatten(qty=43)], state=later, pf=pf, now=now)), ["PASS"], status)
            self.assertEqual(check.open_orders(later, "2026-10-30")[1], [], status)
        close["of"] = "2026-10-29"
        self.assertIn("short: 43 DEMO shares are already in a sell placed today (stop-limit, 09:36; 100 of 143 filled)",
                      reasons(self.sell([flatten(qty=43)], state={"orders": state["orders"] + [close]}, pf=pf, now=now)))
        early = dict(part, at="2026-10-30T09:30:00+05:00")                  # a fill before the protect is not its fill
        res = self.sell([flatten(qty=43)], state={"orders": st["orders"] + [early]}, pf=pf, now=now)
        self.assertIn("short: 43 DEMO shares are already in a sell placed today (stop-limit, 09:36). Cancel", reasons(res))
        _, sells, _ = check.open_orders({"orders": st["orders"] + [early]}, "2026-10-30")
        self.assertEqual([(s["filled"], s["left"]) for s in sells], [(0.0, 143.0)])

    def test_no_exit_is_blocked_by_a_swing_gate(self):
        pf = three_held()
        pf["positions"].append(ledger(qty=143)["positions"][0])
        c = swing(blocked=["DEMO"], max_order_pkr=100, max_daily_loss_pkr=50, max_trades_per_day=1,
                  swing={"max_open_positions": 1, "max_open_risk_pkr": 1, "max_gap_loss_pkr": 1})
        st = contract("broker-placed-unfilled.json")                        # an earlier buy with no fill
        for now in (FRI, "2026-10-30 16:00", "2026-10-31 10:00"):
            for o in (flatten(), protect(), flatten(type="limit", limit_price=99.0)):
                res = run_swing([o], now=now, c=c, portfolio=pf, state=st, orders_date="2026-10-29")
                r = res["results"][0]
                self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), (now, o["type"], r["reasons"]))
                for word in ("style:", "open:", "fill:", "open positions:", "open risk:", "gap:", "window:"):
                    self.assertNotIn(word, warnings(res), (now, word))


def hand_ledger(**over):
    """A made-up ledger written by hand, the v1 way, like a real trading folder: UBL held with no swing plan, and
    a "note" (a key no tool writes)."""
    pf = {"cash": 40000.0, "positions": [{"ticker": "UBL", "shares": 20, "lots": [
        {"id": "UBL-1", "date": "2026-10-20", "shares": 20, "cost": 7700.0, "costs": 15.0}]}],
          "note": "Made up. Written by hand, as in v1."}
    pf.update(over)
    return pf


class PaperAndRealShares(unittest.TestCase):
    """Paper on in a folder of real shares (D39, review F04 and F06). psx-broker refuses a paper fill into such a
    psx/portfolio.json, so psx-risk must not call its cash a "paper account"."""
    MAY = ("paper is on, but psx/portfolio.json may hold real shares: %s. psx-broker records no paper fill in it "
           "until you say which it is. Real shares: put \"real_holdings\": true at the top, and try paper in a folder "
           "of its own. A paper file: put \"real_holdings\": false at the top (README: \"Paper and real shares\")")
    HOLDS = ("paper is on, but psx/portfolio.json holds real shares (\"real_holdings\": true). psx-broker records no "
             "paper fill in it. Keep paper off here, and try paper in a folder of its own (README: \"Paper and real "
             "shares\")")

    def last_line(self, res):
        return [x for x in check.report(res).splitlines() if x.startswith("%d passed" % res["pass"])][0]

    def test_the_report_says_when_the_ledger_holds_real_shares(self):
        buy = swing_buy(qty=100)
        res = run_swing([buy], portfolio=hand_ledger())
        self.assertIn(self.MAY % 'it has "note", which the tools never write', res["warnings"])
        self.assertEqual(res["real_ledger"], "may hold")
        self.assertTrue(self.last_line(res).endswith("Cash after the passed orders: Rs 30,044.20 (paper is on, but "
                                                     "psx/portfolio.json may hold real shares)."), self.last_line(res))
        res = run_swing([buy], portfolio=hand_ledger(real_holdings=True))
        self.assertIn(self.HOLDS, res["warnings"])
        self.assertTrue(self.last_line(res).endswith("(paper is on, but psx/portfolio.json holds real shares)."))
        nonote = hand_ledger()
        nonote.pop("note")
        self.assertIn(self.MAY % "UBL in it has no swing plan, so no swing fill made it",
                      run_swing([buy], portfolio=nonote)["warnings"])
        yes_off = {"orders": [dict(yes(swing_ogdc(qty=50), day="2026-10-28"), paper=False)]}
        self.assertIn(self.MAY % "the broker log has a yes made with paper off (OGDC buy on 2026-10-28)",
                      run_swing([buy], portfolio=ledger(qty=143), state=yes_off)["warnings"])
        # a paper file says so, a swing ledger the tools wrote has no sign, and an empty one has nothing to protect
        for pf in (hand_ledger(real_holdings=False), ledger(qty=143), {"cash": 0, "positions": []}, None):
            res = run_swing([buy], portfolio=pf)
            self.assertFalse([w for w in res["warnings"] if w.startswith("paper is on")], pf)
            self.assertNotIn("real_ledger", res)
            self.assertIn("(paper account).", self.last_line(res))
        # paper off: the LIVE line, as before; day style: no new line at all (day stays as in v1)
        res = run_swing([buy], portfolio=hand_ledger(), c=swing(paper=False))
        self.assertFalse([w for w in res["warnings"] if w.startswith("paper is on")])
        self.assertIn("(LIVE account).", self.last_line(res))
        day = check.check([demo(qty=10)], cfg(), now=at(NOW), market=MKT, portfolio=hand_ledger())
        self.assertFalse([w for w in day["warnings"] if w.startswith("paper is on")])
        self.assertIn("(paper account).", self.last_line(day))
        self.assertGreater(check_words(self, check.report(run_swing([buy], portfolio=hand_ledger())) + "\n" +
                                       check.report(run_swing([buy], portfolio=hand_ledger(real_holdings=True)))), 10)

    def test_the_same_signs_as_psx_broker(self):
        """psx-risk's real_ledger() and psx-broker's maybe_real() see the same signs, so the warning comes exactly when
        a paper fill would be refused."""
        try:
            ticket = load("psx-broker", "ticket")
        except Exception:                       # a half-written ticket.py must not stop psx-risk's own tests
            ticket = None
        if ticket is None or not hasattr(ticket, "maybe_real"):
            self.skipTest("psx-broker/ticket.py has no maybe_real()")
        yes_off = {"orders": [dict(yes(swing_ogdc(qty=50)), paper=False)]}
        nonote = hand_ledger()
        nonote.pop("note")
        cases = [(hand_ledger(), None), (nonote, None), (ledger(qty=143), None), (ledger(qty=143), yes_off),
                 (hand_ledger(real_holdings=True), None), (hand_ledger(real_holdings=False), None),
                 ({"cash": 0, "positions": []}, yes_off), ({"cash": 100.0, "positions": []}, yes_off)]
        for style in ("swing", "day"):
            for pf, st in cases:
                mine = check.real_ledger(pf, st, style == "swing")[0]
                theirs = "holds" if ticket.is_real(pf) else ("may hold" if ticket.maybe_real(pf, {"style": style}, st)
                                                             else "")
                self.assertEqual(mine, theirs, (style, pf, st))


class SwingCli(unittest.TestCase):
    """The command line and the skill page in swing style."""

    def files(self, d, orders, pf=None, state=None):
        """Write the orders (and a portfolio and a broker log) to d -> the CLI arguments, swing config."""
        paths = {}
        for name, doc in (("orders", {"created_at": "2026-10-30T04:50:00Z", "orders": orders}), ("pf", pf),
                          ("state", state)):
            if doc is not None:
                paths[name] = os.path.join(d, name + ".json")
                with open(paths[name], "w") as fh:
                    json.dump(doc, fh)
        return Cli().base(d, orders=paths["orders"], config=SWING_CFG, **dict((k, v) for k, v in paths.items()
                                                                               if k != "orders"))

    def doc_block(self, heading):
        with open(os.path.join(ROOT, "psx-risk", "SKILL.md"), encoding="utf-8") as fh:
            doc = fh.read()
        return doc.split(heading, 1)[1].split("```\n", 2)[1]

    def test_the_swing_example_in_the_skill_page_is_the_real_output(self):
        orders = os.path.join(V2, "orders-swing.json")
        with tempfile.TemporaryDirectory() as d:
            code, text, err = run(Cli().base(d, orders=orders, config=SWING_CFG) + ["--now", PRE, "--no-write"])
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.doc_block("### Swing example"), text)
        self.assertEqual(text.count(check.GAP_NOTE), 1)

    def test_the_open_sell_block_in_the_skill_page_is_the_real_output(self):
        with tempfile.TemporaryDirectory() as d:
            args = self.files(d, [flatten()], pf=ledger(qty=143), state=contract("broker-protect-open.json"))
            code, text, _ = run(args + ["--now", FRI, "--no-write"])
            self.assertEqual(code, 1)
            for line in self.doc_block("### Sells in swing").splitlines():
                self.assertIn(line + "\n", text)
            args = self.files(d, [flatten()], pf=ledger(qty=143), state=contract("broker-protect-cancelled.json"))
            self.assertEqual(run(args + ["--now", FRI, "--no-write"])[0], 0)       # recorded cancelled: it passes

    def test_what_it_never_does_is_short_bullets(self):
        """Very easy English: one idea a sentence, as bullets (review F62: it was one 72-word sentence)."""
        with open(os.path.join(ROOT, "psx-risk", "SKILL.md"), encoding="utf-8") as fh:
            part = fh.read().split("## What it never does\n", 1)[1].split("\n## ", 1)[0]
        lines = [x for x in part.splitlines() if x.strip()]
        self.assertGreaterEqual(len(lines), 5)
        bullets = [x for x in lines if x.lstrip().startswith("- ")]
        self.assertEqual(len(bullets) + len([x for x in lines if x.startswith("    ")]), len(lines), part)
        text = " ".join(" ".join(x.strip().lstrip("- ") for x in lines).split())
        longest = max(len(s.split()) for s in text.replace(":", ".").split(".") if s.strip())
        self.assertLessEqual(longest, 20, part)

    def test_the_cli_records_the_swing_verdicts(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            code, text, err = run(Cli().base(d, orders=os.path.join(V2, "orders-swing.json"), config=SWING_CFG) +
                                  ["--now", PRE, "--out", out])
            self.assertEqual((code, err), (0, ""))
            rec = read(out)
            self.assertEqual((rec["style"], rec["results"][0]["gap_loss"], rec["results"][0]["fingerprint"]),
                             ("swing", 14759.46, BUY_FP))
            self.assertEqual(rec["notes"][0], check.GAP_NOTE)
            code, text, _ = run(Cli().base(d, orders=os.path.join(V2, "orders-swing.json"), config=SWING_CFG) +
                                ["--now", "2026-10-29 09:31", "--no-write", "--json"])
            res = json.loads(text)
            self.assertEqual((code, res["results"][0]["verdict"]), (1, "BLOCK"))
            self.assertIn("the opening match runs until 9:32 AM PKT", reasons(res))

    def test_the_gap_note_shows_once_a_report(self):
        res = run_swing([swing_buy(), swing_ogdc()], portfolio={"cash": 800000.0, "positions": []})
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(check.report(res).count(check.GAP_NOTE), 1)
        self.assertEqual(run_swing([flatten()], now=FRI, portfolio=ledger(qty=143))["notes"][0], check.GAP_NOTE)
        day = check.check([demo()], cfg(), now=at(NOW), market=MKT)
        self.assertNotIn(check.GAP_NOTE, check.report(day))

    def test_the_cli_reads_the_plan_and_todays_company_page(self):
        """Review F41 from the command line: the plan made the evening before (psx/plan.json, last close 98.40), the
        order psx-budget made from it, and a made-up XD company page of the buy day saved at 9:20 AM (LDCP 95.40)."""
        orders = os.path.join(V2, "orders-swing.json")
        with tempfile.TemporaryDirectory() as d:
            data = xd_data(os.path.join(d, "data"))
            base = Cli().base(d, orders=orders, config=SWING_CFG, data=data) + ["--now", PRE, "--no-write"]
            code, text, err = run(base + ["--plan", os.path.join(V2, "plan-swing.json")])
            flat = " ".join(text.split())
            self.assertEqual((code, err), (1, ""))
            self.assertIn("BLOCK #1 buy DEMO 1436 shares, entry", flat)
            self.assertIn("- ex-date: today's LDCP 95.40 (company page) is not the plan's last close 98.40. Today's "
                          "company page shows XD. An ex-date, a split or days with no trade moved it", flat)
            self.assertIn("~ the stop 96.25 is at or over today's LDCP 95.40: a limit buy can fill lower, at or under "
                          "the stop", flat)
            self.assertIn("today's price limits 85.86 to 104.94 (company page)", flat)
            check_words(self, text)
            # with no psx/plan.json the closing files give the last close; a broken one says so and does the same
            code, text, _ = run(base + ["--plan", os.path.join(d, "none")])
            self.assertEqual(code, 1)
            self.assertIn("is not the last close 98.40 (Wed 2026-10-28).", " ".join(text.split()))
            broken = os.path.join(d, "plan.json")
            with open(broken, "w") as fh:
                fh.write("{not json")
            code, text, err = run(base + ["--plan", broken])
            flat = " ".join(text.split())
            self.assertEqual((code, err), (1, ""))
            self.assertIn("WARNING: %s is not valid JSON: " % broken, flat)
            self.assertIn("So the LDCP the plan was made on is taken from the closing files", flat)
            self.assertIn("is not the last close 98.40 (Wed 2026-10-28).", flat)
            check_words(self, text)
            # the plan made on that page on the buy day (psx-exit --entry 96.35) fits today's LDCP: it passes
            plan = dict(contract("plan-swing.json"), entry=96.35, stop=93.22, target=102.61,
                        limits={"lower": 85.86, "upper": 104.94, "ldcp": 95.4, "from": "company page"})
            mine = swing_buy(limit_price=96.35, stop_loss=93.22, take_profit=102.61, qty=1000,
                             plan={"entry": 96.35, "stop": 93.22, "target": 102.61})
            paths = {}
            for name, doc in (("plan", plan), ("orders", {"created_at": "2026-10-29T04:20:00Z", "orders": [mine]})):
                paths[name] = os.path.join(d, name + "-new.json")
                with open(paths[name], "w") as fh:
                    json.dump(doc, fh)
            code, text, _ = run(Cli().base(d, orders=paths["orders"], config=SWING_CFG, data=data) +
                                ["--now", PRE, "--no-write", "--plan", paths["plan"]])
            self.assertEqual(code, 0, text)
            self.assertNotIn("LDCP", text)
            # day style never reads it: the same files, the v1 report
            code, day_text, _ = run(Cli().base(d, data=data) + ["--now", NOW, "--no-write", "--plan", broken])
            self.assertNotIn("not valid JSON", day_text)
            self.assertNotIn("ex-date", day_text)

    def test_the_cli_reads_psx_alerts_record_of_ex_dates(self):
        """Review F15 from the command line: a scaled plan counts when psx-alerts recorded the XB (company page) or
        the closing files show it, and warns when neither does. A broken record never stops a check."""
        pf = ledger(qty=143, stop=80.2, target=88.03, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB"}])
        record = {"swing_ex": {"DEMO-2026-10-29": {"2026-11-12": {"kind": "XB", "factor": 0.8333, "date": "2026-11-12",
                                                                  "seen": "company page"}}}}
        lowered = "DEMO's stop in psx/portfolio.json (80.20) is under its stop at the buy (96.25): it was lowered by hand."
        with tempfile.TemporaryDirectory() as d:
            args = self.files(d, [flatten()], pf=pf) + ["--now", "2026-11-13 10:00", "--no-write"]
            alerts = os.path.join(d, "alerts.json")
            with open(alerts, "w") as fh:
                json.dump(record, fh)
            code, text, err = run(args + ["--alerts", alerts])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("PASS  #1 sell DEMO 143 shares, exit", text)
            self.assertNotIn("lowered by hand", text)
            self.assertIn("DEMO's plan was scaled (x0.8333 on Thu 2026-11-12)", " ".join(text.split()))
            code, text, _ = run(args + ["--alerts", os.path.join(d, "none")])
            self.assertEqual(code, 0)                                     # an exit: a warning, never a block
            self.assertIn(lowered + " The scale x0.8333 of Thu 2026-11-12 does not count", " ".join(text.split()))
            with open(alerts, "w") as fh:
                fh.write("[")
            code, text, err = run(args + ["--alerts", alerts])
            flat = " ".join(text.split())
            self.assertEqual((code, err), (0, ""))
            self.assertIn("WARNING: %s is not valid JSON: " % alerts, flat)
            self.assertIn("So psx-alerts' record of ex-dates was not read", flat)
            self.assertIn(lowered, flat)
            check_words(self, text)

    def test_every_result_line_names_the_order(self):
        """ticket.py result --status X alone is refused: it needs --index N, --fingerprint FP, or --symbol X --date D
        (review F87). So no line of psx-risk says to type it that way."""
        for name in ("check.py", "SKILL.md"):
            with open(os.path.join(ROOT, "psx-risk", name), encoding="utf-8") as fh:
                text = " ".join(fh.read().split())
            found = re.findall(r"ticket\.py result (\S+)", text)
            self.assertGreater(len(found), 3, name)
            for flag in found:
                self.assertIn(flag, ("--fingerprint", "--symbol", "--index"), name)

    def test_paper_first_in_a_folder_of_its_own(self):
        """A paper fill must never go into a ledger of real shares (review F06). The swing part says to try paper in a
        folder of its own, and points at the README part that says how."""
        with open(os.path.join(ROOT, "psx-risk", "SKILL.md"), encoding="utf-8") as fh:
            part = fh.read().split("## Swing style\n", 1)[1].split("\n### ", 1)[0]
        self.assertIn("Try it on paper first, in a folder of its own. Never turn paper on in a folder that holds real "
                      "shares or real cash. See \"Paper and real shares\" in the README.", " ".join(part.split()))
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            self.assertIn("\n### Paper and real shares\n", fh.read())

    def test_every_new_line_passes_the_word_test(self):
        """tests/_words.py on the swing reports: no "Buy "/"Sell " start, no banned word, no em dash."""
        pf3 = three_held()
        pf3["positions"][0]["plan"].pop("stop")
        pf3["positions"][1]["plan"]["stop"] = 250.0
        st = {"orders": contract("broker-approved-old.json")["orders"] + contract("broker-protect-open.json")["orders"]}
        noref = contract("broker-approved-old.json")
        noref["orders"][0].pop("entry_ref")
        runs = [run_swing([swing_buy()]), run_swing([swing_buy()], portfolio=pf3),
                run_swing([swing_buy(take_profit=110.0)], portfolio=pf3, c=swing(swing={"max_open_risk_pkr": 100})),
                run_swing([swing_ogdc(), swing_buy()], now="2026-10-29 09:31", portfolio={"cash": 150000.0,
                                                                                            "positions": []}, state=st),
                run_swing([swing_ogdc()], now="2026-10-29 09:25", state=noref),
                run_swing([swing_ogdc(), demo()], now=FRI, state=contract("broker-placed-unfilled.json")),
                run_swing([swing_buy(qty=183, stop_loss=97.0)], c=swing(max_loss_per_trade_pkr=500)),
                run_swing([swing_buy(qty=143)], portfolio=ledger(qty=143)),
                run_swing([flatten(), protect()], now=FRI, portfolio=ledger(qty=143), state=st),
                run_swing([protect(side="buy"), protect(limit_price=97.0)], now=FRI, portfolio=ledger(qty=143),
                          prices={"DEMO": 82.0}),
                check.check([swing_buy()], cfg(), now=at(NOW), market=MKT),
                check.check(contract("orders-invest.json")["orders"], cfg(require_stop=False), now=at("2026-10-29 10:25"),
                            state={"orders": [yes(swing_ogdc(qty=50), at="10:21")]}, market=swing_market())]
        # the rest of the new lines: no max loss or cost in the log, no limit per trade, a buy with no plan, the
        # buffer, the lower limit, a buy with no stop, and the stop-limit format lines
        nolog = {"orders": [yes(swing_ogdc(qty=50), at="09:15", max_loss=None)]}
        nocost = three_held()
        nocost["positions"][2]["lots"][0].pop("cost")
        bare = dict(swing_buy())
        bare.pop("plan")
        nostop = swing_buy(stop_loss=None, take_profit=None, plan={"stop": None, "target": None})
        none = {"limits": {}, "unknown": {}, "closes": {}, "pages": {}, "universe": None, "universe_file": None,
                "warnings": []}
        runs += [run_swing([swing_buy()], state=nolog, portfolio=nocost, c=swing(max_loss_per_trade_pkr=0)),
                 run_swing([bare, swing_buy(plan={"style": "scalp"})]),
                 run_swing([swing_ogdc()], now="2026-10-29 09:25", portfolio={"cash": 300000.0, "positions": []},
                           state=contract("broker-approved-old.json"), c=swing(cash_buffer_pct=50)),
                 run_swing([swing_buy(qty=100, stop_loss=88.56, take_profit=111.0)]),
                 run_swing([nostop], c=swing(require_stop=False)),
                 run_swing([protect(trigger_price=None), protect(type="stop")], now=FRI, portfolio=ledger(qty=143)),
                 run_swing([protect()], now=FRI, portfolio=ledger(qty=143), market=none)]
        # round 1 of the review: the stop under the limit, the stop-limit trigger words, part fills, a scaled plan
        # with no bonus shares yet, two waiting buys, and the gap limit of Rs 0
        split = copy.deepcopy(swing_market("2026-10-30"))
        split["limits"]["DEMO"] = {"lower": 73.8, "upper": 90.2, "ldcp": 82.0, "from": "company page",
                                   "label": "company page"}
        xb = copy.deepcopy(split)
        xb["pages"]["DEMO"] = dict(xb["pages"]["DEMO"], ex=["XB"], today=True)
        xd = copy.deepcopy(split)
        xd["limits"]["DEMO"] = dict(xd["limits"]["DEMO"], ldcp=95.0)
        xd["pages"]["DEMO"] = dict(xd["pages"]["DEMO"], ex=["XD"], today=True)
        sold = {"orders": contract("broker-protect-open.json")["orders"] + [
            {"at": "2026-10-30T10:00:00+05:00", "date": "2026-10-30", "status": "filled", "symbol": "DEMO",
             "side": "sell", "qty": 100, "price": 96.1}]}
        bought = {"orders": [yes(swing_buy(qty=143), at="09:20"), {
            "at": "2026-10-29T09:40:00+05:00", "date": TODAY, "status": "filled", "symbol": "DEMO", "side": "buy",
            "qty": 10, "price": 99.38}]}
        scaled = ledger(stop=80.2, target=88.03, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": None}])
        zero = swing(max_loss_per_trade_pkr=500, swing={"max_gap_loss_pkr": 0})
        runs += [run_swing([swing_buy(qty=100, stop_loss=88.55, take_profit=111.0)]),
                 run_swing([protect()], now=FRI, portfolio=ledger(qty=143), prices={"DEMO": 82.0}),
                 run_swing([protect(), protect(qty=1)], now=FRI, portfolio=ledger(qty=143), market=split),
                 run_swing([protect()], now=FRI, portfolio=ledger(qty=143), market=xb),
                 run_swing([protect()], now=FRI, portfolio=ledger(qty=143), market=xd),
                 run_swing([protect(trigger_price=102.0, limit_price=101.0)], now=FRI, portfolio=ledger(qty=143),
                           prices={"DEMO": 101.5}),
                 run_swing([flatten(qty=43)], now="2026-10-30 10:05", portfolio=ledger(qty=43), state=sold),
                 run_swing([swing_ogdc(qty=50)], now="2026-10-29 09:45", state=bought,
                           portfolio={"cash": 300000.0, "positions": [held("DEMO", 10, 99.38, 96.25, day=TODAY)]}),
                 run_swing([swing_ogdc(qty=50)], now="2026-11-13 09:20", portfolio=scaled, ex_dates=XB_EX),
                 run_swing([swing_order("HBL", 10, 279.0, 270.0, 297.0)], now="2026-10-29 09:25",
                           state={"orders": [yes(swing_buy(), at="09:16"), yes(swing_ogdc(qty=50), at="09:18")]}),
                 run_swing([swing_buy(qty=143)], c=zero)]
        # round 2 of the review: an ex-date on the buy day (F41), a scale with no ex-date or under its factor (F15),
        # and paper on with a ledger of real shares (F04)
        hand = ledger(stop=48.12, target=52.82, scaled=[{"date": "2026-11-12", "factor": 0.5, "why": None}])
        runs += [run_swing([swing_buy()], market=xd_market(), plan_doc=contract("plan-swing.json")),
                 run_swing([swing_buy()], market=xd_market()),
                 run_swing([swing_ogdc(qty=50)], now="2026-11-13 09:20", portfolio=scaled),
                 run_swing([swing_ogdc(qty=50)], now="2026-11-13 09:20", portfolio=hand, ex_dates=XB_EX),
                 run_swing([swing_buy(qty=100)], portfolio=hand_ledger()),
                 run_swing([swing_buy(qty=100)], portfolio=dict((k, v) for k, v in hand_ledger().items() if k != "note")),
                 run_swing([swing_buy(qty=100)], portfolio=hand_ledger(real_holdings=True))]
        checked = 0
        for res in runs:
            text = check.report(res)
            checked += check_words(self, text)
            for r in res["results"]:
                checked += check_words(self, "\n".join(r["reasons"] + r["warnings"] + r["info"]))
        joined = "\n".join(check.report(res) for res in runs)
        for line in ("has no max loss in the log", "no cost in its lots", "there is no max_loss_per_trade_pkr",
                     "a buy with no plan", "the plan's style is 'scalp'", "under your 50% buffer",
                     "the stop counts from the next session", "no stop: in the open risk",
                     "needs a trigger_price", "or stop_limit (a stop-limit sell)", "no price for DEMO now",
                     "a trade at the limit meets it today", "the stop rule is met. To sell: ticket.py flatten",
                     "with no tag: a split?", "DEMO shows XB on today's company page.", "You choose: scale it",
                     "a cash payout. The stop stays as it is.", "makes a new stop-limit from it",
                     "100 of 143 filled", "buy DEMO is filled in part", "the rest stops counting after today",
                     "and no bonus or split shares are recorded since the buy", "record each one expired: ticket.py",
                     "over your Rs 0 gap limit", "record it expired: ticket.py result --fingerprint",
                     "is not the plan's last close 98.40. Today's company page shows XD.",
                     "is not the last close 98.40 (Wed 2026-10-28)", "a limit buy can fill lower, at or under the stop",
                     "The scale x0.8333 of Thu 2026-11-12 does not count", "so it counts as x0.8333",
                     "may hold real shares: UBL in it has no swing plan", "may hold real shares: it has \"note\"",
                     "holds real shares (\"real_holdings\": true). psx-broker records no paper fill in it",
                     "(paper is on, but psx/portfolio.json may hold real shares).",
                     "(paper is on, but psx/portfolio.json holds real shares)."):
            self.assertIn(line, " ".join(joined.split()), line)        # each new line was reached and checked
        for err in ('"style" in psx/config.json is "day" or "swing", not \'scalp\'',
                    '"max_open_positions" in the "swing" block of psx/config.json must be a whole number, 0 or more, '
                    'or null (got 2.5)'):
            checked += check_words(self, "check.py: " + err)
        self.assertGreater(checked, 250)


if __name__ == "__main__":
    unittest.main()
