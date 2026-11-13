import datetime
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _load import load, ROOT, FIX
from _words import check_words

ticket = load("psx-broker", "ticket")
try:                                      # psx-risk is a separate skill: its tests below run once it is PSX
    check = load("psx-risk", "check")
except Exception:                         # a half-written check.py must not stop the broker's own tests
    check = None
PSX_RISK = check is not None and getattr(check, "ORDER_FIELDS", None) == ticket.ORDER_FIELDS

BFIX = os.path.join(FIX, "psx-broker")
CFG_PATH = os.path.join(BFIX, "config.json")            # SPEC section 6, the example config
ORDERS_PATH = os.path.join(BFIX, "orders-demo.json")    # EXAMPLE.md section 7, the 10:15 order
PLAN_PATH = os.path.join(BFIX, "plan-demo.json")        # EXAMPLE.md section 6
NOW = "2026-10-29 10:15"
TODAY = "2026-10-29"
LATE = "2026-10-29 15:20"
COSTS = ticket.cost_settings({})
BATCH = ["DEMO", "OGDC", "HBL", "FFC", "SYS"]
OGDC = {"symbol": "OGDC", "side": "sell", "qty": 300, "type": "market", "time_in_force": "day", "board": "REG"}
DEMO_LINE = "BUY 3,428 DEMO, limit 100.65, good for today, REG board"
DEMO_PLAIN = (DEMO_LINE + " / plan stop 99.55 / plan target 101.75 / max loss about Rs 4,999.94 if the stop fills"
              " at 99.55, costs included")
DEMO_APP = "BUY DEMO, REG board, quantity 3428, LIMIT price 100.65, valid for the day"
FLATTEN = {"symbol": "DEMO", "side": "sell", "qty": 3428, "type": "market", "time_in_force": "day", "board": "REG",
           "why": "flat by 15:20: close the DEMO day trade"}
BANNED = ("real time", "real-time", "live", "guarantee", "profit", "p&l", "return of", "$")


def demo(**over):
    with open(ORDERS_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=None, batch=BATCH, known=()):
        return ticket.parse_yes(reply, order or demo(), batch, known, COSTS)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes DEMO", "yes 3428", "yes 3,428 DEMO",
                  "yes Rs 4,999.94", "yes max loss 4999.94", "yes 100.65", "yes target 101.75",
                  "Yes, buy 3428 DEMO at 100.65"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "absolutely", "send it", "haan", "ji haan",
                  "theek hai", "yes to all", "yes, all of them", "yes to the rest", "yes both", "yes sab", "yes dono",
                  "yes OGDC", "yes 1000", "yes but make it 1000", "yes?", "no", "not yes", "yes, wait",
                  "yes if it is under 101", "yes lekin baad mein", "yes magar 3000", "yes, stop 99.55",
                  "yes 3428 at 101"):
            self.assertFalse(self.yes(r), r)

    def test_amount_must_match_this_order(self):
        self.assertTrue(self.yes("yes sell 300 OGDC", OGDC))
        self.assertFalse(self.yes("yes sell 400", OGDC))
        ok, why = ticket.parse_yes("yes 3429", demo(), BATCH, (), COSTS)
        self.assertFalse(ok)
        self.assertIn("the reply says 3429, but this order is " + DEMO_LINE, why)

    def test_psx_symbols_with_digits(self):
        # real PSX symbols: BAPLR1 and SPAC2 end in a digit, 786 is all digits; none of it is an amount
        for sym in ("BAPLR1", "SPAC2", "786"):
            o = demo(symbol=sym, qty=100)
            self.assertTrue(self.yes("yes %s" % sym, o, [sym]), sym)
            self.assertTrue(self.yes("yes 100 %s" % sym, o, [sym]), sym)
            self.assertFalse(self.yes("yes 101 %s" % sym, o, [sym]), sym)
        self.assertFalse(self.yes("yes TPLRF1", demo(), ["DEMO", "TPLRF1"]))
        self.assertTrue(self.yes("yes DEMO", demo(), ["DEMO", "TPLRF1"]))

    def test_a_yes_that_names_a_known_stock_is_refused(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "universe.txt")
            with open(path, "w") as fh:
                fh.write("DEMO|Demo Industries Limited|0818\nHBL|Habib Bank Limited|0807\n786|786 Invest Ltd|0813\n")
            known = ticket.universe_symbols(path)
            self.assertEqual(known, ["DEMO", "HBL", "786"])
            self.assertEqual(ticket.universe_symbols(os.path.join(d, "none.txt")), [])
        finally:
            shutil.rmtree(d)
        self.assertFalse(self.yes("yes HBL", demo(), ["DEMO"], known))     # one order in the batch, HBL named
        self.assertTrue(self.yes("yes DEMO", demo(), ["DEMO"], known))
        self.assertTrue(self.yes("yes", demo(), ["DEMO"], known))

    def test_rupee_amounts(self):
        for r in ("yes Rs4,999.94", "yes PKR 4999.94", "yes Rs. 4,999.94"):
            self.assertTrue(self.yes(r), r)
        for r in ("yes Rs 5,000", "yes PKR5000", "yes Rs 4,999"):
            self.assertFalse(self.yes(r), r)

    def test_a_yes_for_another_side_type_or_size_is_not_a_yes(self):
        # the ticket is BUY 3,428 DEMO, limit 100.65, for today: a yes is for that order only
        for r in ("yes, sell 3428 DEMO", "yes sell it", "yes, sell", "Yes. Also sell my HBL", "yes becho",
                  "yes exit", "yes go ahead and short it", "yes go short", "yes, use margin", "yes futures",
                  "yes options too", "yes udhaar pe", "yes at market", "yes, buy 3428 at market price",
                  "yes mkt", "yes double it", "yes x2", "yes 2x", "yes, twice", "yes half", "yes more",
                  "yes less", "yes increase it", "yes reduce it", "yes dugna", "yes zyada", "yes, place it tomorrow",
                  "yes next week", "yes Monday", "yes kal", "yes baad mein", "yes when it opens",
                  "yes as long as it holds", "yes mat karo"):
            self.assertFalse(self.yes(r), r)
        self.assertEqual(ticket.parse_yes("yes sell it", demo(), BATCH, (), COSTS)[1],
                         "the reply says sell, but this order is a buy")
        self.assertEqual(ticket.parse_yes("yes at market", demo(), BATCH, (), COSTS)[1],
                         "the reply says market, but this is a limit order")
        self.assertIn("a changed order needs a new psx-risk check", ticket.parse_yes("yes x2", demo(), BATCH, (),
                                                                                    COSTS)[1])
        self.assertIn("no short selling, margin", ticket.parse_yes("yes, use margin", demo(), BATCH, (), COSTS)[1])
        for r in ("yes", "yes go ahead", "yes please place it", "Yes, buy it", "YES BUY IT NOW", "yes haan ji",
                  "yes khareedo", "yes at the limit 100.65"):
            self.assertTrue(self.yes(r), r)
        sell = FLATTEN                                            # a market sell: the side and type turn round
        for r in ("yes buy", "yes khareedo", "yes at limit", "yes short it", "yes sell 6856"):
            self.assertFalse(self.yes(r, sell), r)
        for r in ("yes sell 3428 DEMO", "yes exit", "YES EXIT", "yes close it", "yes at market", "yes becho"):
            self.assertTrue(self.yes(r, sell), r)

    def test_selling_words_any_price_and_later_times_are_not_a_yes_for_a_limit_buy(self):
        # the verifier's leftovers: square off, liquidate, dump, offload mean selling; "any price" is a market
        # order in words; "two times", "after lunch", "after Juma" and "on credit" change the order
        for r in ("yes square off", "yes liquidate", "yes dump it", "yes offload it", "yes unload",
                  "yes at any price", "yes at best price", "yes whatever price", "yes at the current rate",
                  "yes, two times", "yes after lunch", "yes in the afternoon", "yes after juma", "yes on credit"):
            self.assertFalse(self.yes(r), r)
        self.assertIn("this is a limit order: it fills only at its limit or better",
                      ticket.parse_yes("yes at any price", demo(), BATCH, (), COSTS)[1])
        for r in ("yes square off", "yes dump it", "yes at any price"):     # for the flatten sell these are fine
            self.assertTrue(self.yes(r, FLATTEN), r)

    def test_a_stock_in_capitals_is_refused_with_no_universe_file(self):
        # with no psx/universe.txt and one order, a word in capitals other than DEMO may be another stock
        for r in ("yes buy OGDC", "yes HBL", "yes for OGDC too", "YES HBL", "yes DEMO and LUCK"):
            self.assertFalse(self.yes(r, demo(), ["DEMO"]), r)
        self.assertEqual(ticket.parse_yes("yes HBL", demo(), ["DEMO"], (), COSTS)[1],
                         "the reply names HBL, but this order is for DEMO")
        for r in ("yes DEMO", "YES", "YES, BUY 3428 DEMO AT 100.65", "yes PKR4,999.94", "Yes I confirm, REG board"):
            self.assertTrue(self.yes(r, demo(), ["DEMO"]), r)
        self.assertTrue(self.yes("yes BAPLR1", demo(symbol="BAPLR1"), ["BAPLR1"]))


class TicketShape(unittest.TestCase):
    def test_fingerprint_matches_the_example(self):
        self.assertEqual(ticket.ORDER_FIELDS, ("symbol", "side", "qty", "type", "limit_price", "time_in_force",
                                               "board", "stop_loss", "take_profit", "trigger_price"))
        self.assertEqual(ticket.fingerprint(demo()), "e9d01b16c66c4a20")          # EXAMPLE.md section 7
        self.assertEqual(ticket.fingerprint(FLATTEN), "2f046115ed2a3498")         # EXAMPLE.md section 8
        bare = {k: v for k, v in demo().items() if k in ticket.ORDER_FIELDS}
        self.assertEqual(ticket.fingerprint(bare), "e9d01b16c66c4a20")           # why and plan do not count
        self.assertNotEqual(ticket.fingerprint(demo(board="FUT")), "e9d01b16c66c4a20")

    def test_plain_words(self):
        self.assertEqual(ticket.order_line(demo()), DEMO_LINE)
        self.assertEqual(ticket.plain(demo(), COSTS), DEMO_PLAIN)
        self.assertEqual(ticket.order_line(FLATTEN), "SELL 3,428 DEMO, market, good for today, REG board")
        self.assertEqual(ticket.plain(FLATTEN, COSTS), "SELL 3,428 DEMO, market, good for today, REG board")
        self.assertEqual(ticket.order_line(dict(OGDC, qty=1, type="limit", limit_price=283.5, time_in_force="ioc")),
                         "SELL 1 OGDC, limit 283.50, immediate or cancel, REG board")

    def test_max_loss_includes_costs(self):
        self.assertEqual(ticket.max_loss(demo(), COSTS), (4999.94, 99.55))
        self.assertEqual(ticket.max_loss(demo(qty=3429), COSTS), (5001.40, 99.55))    # one share more: over
        self.assertEqual(ticket.max_loss(demo(qty=3427), COSTS), (4998.48, 99.55))
        market = demo(type="market")
        del market["limit_price"]
        self.assertEqual(ticket.max_loss(market, COSTS), (4999.94, 99.55))              # the plan's own number
        del market["plan"]
        self.assertEqual(ticket.max_loss(market, COSTS), (None, 99.55))
        self.assertEqual(ticket.max_loss(FLATTEN, COSTS), (None, None))
        free = dict(COSTS, commission_pct=0, commission_min_per_share=0, other_charges_pct=0)
        self.assertEqual(ticket.max_loss(demo(), free), (3770.80, 99.55))                # no costs: price risk only

    def test_max_loss_comes_from_psx_risk(self):
        # SPEC section 5: the broker shows and logs the number psx-risk checked, not the plan's
        market = demo(type="market")
        del market["limit_price"]
        loss = ticket.max_loss_at_stop(101.30, 99.55, 3428, COSTS)          # psx-risk used the latest price
        rv = {"verdict": "PASS", "max_loss": loss, "stop": 99.55, "entry_ref": 101.3}
        self.assertEqual(ticket.max_loss(market, COSTS, rv), (loss, 99.55))
        self.assertEqual(ticket.max_loss(market, COSTS), (4999.94, 99.55))  # no verdict: the plan's number
        self.assertEqual(ticket.plain_parts(market, COSTS, rv)[-1],
                         "max loss about %s if it fills at 101.30 and the stop fills at 99.55, costs included"
                         % ticket.money(loss))
        self.assertEqual(ticket.plain(demo(), COSTS, dict(rv, max_loss=4999.94, entry_ref=100.65)), DEMO_PLAIN)
        self.assertEqual(ticket.max_loss(FLATTEN, COSTS, {"max_loss": None, "stop": None}), (None, None))
        yes = ticket.parse_yes
        self.assertTrue(yes("yes max loss %s" % ticket.money(loss), market, ["DEMO"], (), COSTS, rv)[0])
        self.assertTrue(yes("yes 101.30", market, ["DEMO"], (), COSTS, rv)[0])
        self.assertFalse(yes("yes Rs 4,999.94", market, ["DEMO"], (), COSTS, rv)[0])   # not the checked number

    def test_the_line_for_your_broker_app(self):
        self.assertEqual(ticket.app_line(demo()), DEMO_APP)
        self.assertEqual(ticket.app_line(FLATTEN), "SELL DEMO, REG board, quantity 3428, MARKET order, valid for the day")

    def test_ticket_json_has_no_connector_call(self):
        t = ticket.ticket_json(demo(), 1, {}, None, {"orders": []}, TODAY)
        self.assertFalse(t["ready"])
        self.assertEqual((t["fingerprint"], t["max_loss"], t["stop"], t["target"]), ("e9d01b16c66c4a20", 4999.94,
                                                                                    99.55, 101.75))
        self.assertEqual((t["app_line"], t["broker"], t["paper"]), (None, "manual", True))   # only after the yes
        self.assertEqual(sorted(t["order"]), sorted(f for f in ticket.ORDER_FIELDS if f != "trigger_price"))
        for k in ("tool", "tool_input", "bracket"):
            self.assertNotIn(k, t)

    def test_no_connector_and_no_network(self):
        folder = os.path.join(ROOT, "psx-broker")
        self.assertEqual(sorted(f for f in os.listdir(folder) if not f.startswith((".", "__"))),
                         ["SKILL.md", "ticket.py"])
        with open(os.path.join(folder, "ticket.py"), encoding="utf-8") as fh:
            src = fh.read()
        for word in ("place_stock_order", "mcp", "Allow once", "urllib", "socket", "http"):
            self.assertNotIn(word, src, word)
        with open(os.path.join(folder, "SKILL.md"), encoding="utf-8") as fh:
            doc = " ".join(fh.read().split())
        for must in ("Needs approval", "9.7(e)", "T+1", "halt", "KTrade", "two passwords and a PIN",
                     "never go in the chat", "cancels all open orders"):
            self.assertTrue(must in doc, "SKILL.md should say: " + must)


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.plan = os.path.join(self.d, "plan.json")
        shutil.copy(PLAN_PATH, self.plan)
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def risk_step(self, now=NOW, verdict="PASS", reasons=(), day=None, **extra):
        """What psx-risk's check.py writes to psx/state/risk.json (the contract): one result per order,
        and the time of the check (its --now) as "checked_at" in UTC and "now_karachi"."""
        with open(self.orders) as fh:
            doc = json.load(fh)
        results = []
        for i, o in enumerate(doc["orders"], 1):
            results.append(dict({"index": i, "symbol": o["symbol"], "side": o["side"],
                                 "kind": "entry" if o["side"].lower() == "buy" else "exit",
                                 "fingerprint": ticket.fingerprint(o), "verdict": verdict, "reasons": list(reasons),
                                 "warnings": [], "info": []}, **extra))
        self.write_risk({"date": day or now[:10], "now_karachi": now, "paper": True, "results": results,
                         "checked_at": ticket.utc_iso(ticket.parse_now(now)),
                         "pass": sum(r["verdict"] == "PASS" for r in results),
                         "block": sum(r["verdict"] != "PASS" for r in results)})

    def write_risk(self, doc):
        os.makedirs(os.path.dirname(self.risk), exist_ok=True)
        with open(self.risk, "w") as fh:
            json.dump(doc, fh)

    def set_orders(self, *orders):
        with open(self.orders, "w") as fh:
            json.dump({"orders": list(orders)}, fh)

    def set_cfg(self, **kw):
        with open(self.cfg) as fh:
            c = json.load(fh)
        c.update(kw)
        with open(self.cfg, "w") as fh:
            json.dump(c, fh)

    def state_doc(self):
        with open(self.state) as fh:
            return json.load(fh)

    def pf_doc(self):
        with open(self.pf) as fh:
            return json.load(fh)

    def buy_on_paper(self):
        self.risk_step()
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes"))[0], 0)
        code, text, err = run(self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf,
                                        now="2026-10-29 10:16"))
        self.assertEqual(code, 0, err)
        return text

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY: psx-risk has not checked these orders", text)
        self.assertNotIn("Reply \"yes\"", text)
        self.assertNotIn("In your broker app", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_full_paper_flow(self):
        self.risk_step()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, PAPER trade: practice, nothing goes to a broker", text)
        self.assertIn("  " + DEMO_LINE + "\n", text)
        self.assertIn("  plan stop 99.55: after the buy fills, put it in your app as a stop-loss order", text)
        self.assertIn("  plan target 101.75: PSX apps have no bracket order, so psx-alerts watches", text)
        self.assertIn("max loss about Rs 4,999.94 if the stop fills at 99.55, costs included", text)
        self.assertIn("  why: opening-range breakout on DEMO", text)
        self.assertIn("READY: psx-risk PASS, waiting for your yes", text)
        self.assertNotIn("In your broker app", text)                  # the app line comes after the yes
        self.assertIn("Paper: do not place it in your app", text)
        self.assertNotIn("{", text)                                   # no connector JSON
        self.assertNotIn('"plan"', text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertNotIn("In your broker app", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("Recorded your yes for order 1: " + DEMO_PLAIN, text)
        self.assertIn("\n  In your broker app: " + DEMO_APP + "\n", text)
        self.assertIn("so do not place it in your app", text)
        self.assertIn("ticket.py result --index 1 --status placed", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "paper-1",
                                      "--portfolio", self.pf, now="2026-10-29 10:16"))
        self.assertEqual(code, 0)
        self.assertIn("Paper fill: BUY 3,428 DEMO at 100.65, costs about Rs 617.95. Cash now Rs 154,353.85", text)
        self.assertIn("psx-alerts watches the plan", text)
        self.assertIn("Be flat by 15:20 Karachi time: ticket.py flatten --symbol DEMO", text)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed", "filled"])
        appr, plc, fill = st["orders"]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"]), ("yes", "entry", 4999.94, TODAY))
        self.assertEqual((appr["at"], appr["qty"], appr["broker"], appr["paper"]),
                         ("2026-10-29T10:15:00+05:00", 3428, "manual", True))
        self.assertEqual((plc["broker_order_id"], plc["max_loss"], plc["kind"], plc["at"]),
                         ("paper-1", 4999.94, "entry", "2026-10-29T10:16:00+05:00"))
        self.assertEqual((fill["qty"], fill["price"], fill["costs"], fill["side"]), (3428, 100.65, 617.95, "buy"))
        pf = self.pf_doc()
        self.assertEqual(pf["cash"], 154353.85)                     # EXAMPLE.md: 500,000 - 345,028.20 - 617.95
        self.assertEqual(pf["positions"][0]["shares"], 3428)
        self.assertEqual(pf["positions"][0]["lots"], [{"id": "DEMO-1", "date": TODAY, "shares": 3428,
                                                       "cost": 345028.2, "costs": 617.95}])

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        self.assertNotIn("In your broker app", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("1 placed today", text)
        self.assertIn("max loss Rs 4,999.94 (costs included)", text)
        self.assertIn("id paper-1", text)
        self.assertIn("costs Rs 617.95", text)

    def test_flatten_by_flat_by(self):
        self.buy_on_paper()
        code, text, _ = run(["flatten", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", self.plan, "--config", self.cfg, "--now", LATE])
        self.assertEqual(code, 0)
        self.assertIn("Flatten DEMO: SELL 3,428 DEMO, market, good for today, REG board (flat by 15:20 Karachi time)",
                      text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("The qty comes from %s" % self.pf, text)
        with open(self.orders) as fh:
            doc = json.load(fh)
        self.assertEqual((doc["created_by"], doc["created_at"], doc["paper"]), ("psx-broker", "2026-10-29T10:20:00Z",
                                                                                True))
        self.assertEqual(doc["orders"], [FLATTEN])                  # the SPEC section 7 flatten order
        self.assertEqual(ticket.fingerprint(doc["orders"][0]), "2f046115ed2a3498")
        self.risk_step(now=LATE)
        code, text, _ = run(self.args("show", now=LATE))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)                                 # an exit: 15:20 is after the entry window
        self.assertNotIn("In your broker app", text)
        self.assertIn("--filled-price P records the paper fill", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes sell 3428 DEMO", now=LATE))
        self.assertEqual(code, 0)
        self.assertIn("In your broker app: SELL DEMO, REG board, quantity 3428, MARKET order", text)
        before = self.state_doc()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf,
                                     now="2026-10-29 15:21"))
        self.assertEqual(code, 1)                                    # a market order has no price of its own
        self.assertIn("--filled-price", err)
        self.assertEqual(self.state_doc(), before)                   # nothing was written
        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-price", "101.58",
                                      "--portfolio", self.pf, now="2026-10-29 15:21"))
        self.assertEqual(code, 0)
        self.assertIn("Paper fill: SELL 3,428 DEMO at 101.58, costs about Rs 623.66", text)
        pf = self.pf_doc()
        self.assertEqual((pf["cash"], pf["positions"]), (501946.43, []))   # 154,353.85 + 348,216.24 - 623.66
        self.assertEqual([r["status"] for r in self.state_doc()["orders"]],
                         ["approved", "placed", "filled", "approved", "placed", "filled"])
        code, _, err = run(["flatten", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", LATE])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_the_skill_example_is_the_real_output(self):
        with open(os.path.join(ROOT, "psx-broker", "SKILL.md"), encoding="utf-8") as fh:
            part = fh.read().split("## Example", 1)[1].split("\n## ", 1)[0]
        blocks = re.findall(r"```\n(.*?)\n```", part, re.S)
        self.risk_step()
        outs = [run(self.args("show"))[1], run(self.args("approve", "--index", "1", "--reply", "yes"))[1],
                run(self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf,
                              now="2026-10-29 10:16"))[1],
                run(["flatten", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf,
                     "--plan", self.plan, "--config", self.cfg, "--now", LATE])[1]]
        outs = [o.replace(self.pf, "psx/portfolio.json").replace(self.orders, "psx/orders.json").strip()
                for o in outs]
        self.assertEqual(blocks, outs)

    def flat_by(self, now, plan=None, **cfg):
        if cfg:
            self.set_cfg(**cfg)
        code, text, err = run(["flatten", "--symbol", "DEMO", "--qty", "3428", "--orders", self.orders,
                               "--portfolio", self.none, "--plan", plan or self.none, "--config", self.cfg,
                               "--now", now])
        self.assertEqual(code, 0, err)
        return re.search(r"flat by (\d\d:\d\d) Karachi time", text).group(1)

    def test_flatten_uses_config_time_for_the_day(self):
        self.assertEqual(self.flat_by("2026-10-29 15:00"), "15:20")                 # Thu: a normal day
        self.assertEqual(self.flat_by("2026-10-30 15:00"), "16:20")                 # Fri: 10 minutes before 16:30
        self.assertEqual(self.flat_by("2026-02-23 12:00"), "13:20")                 # a Ramadan Monday
        self.assertEqual(self.flat_by("2026-10-29 15:00", self.plan), "15:20")      # today's plan
        self.assertEqual(self.flat_by("2026-10-30 15:00", self.plan), "16:20")      # yesterday's plan does not count
        self.assertEqual(self.flat_by("2026-10-30 15:00", flat_by="15:55"), "16:30")    # kept inside the session
        self.assertEqual(self.flat_by("2026-10-29 15:00", flat_by=None), "15:20")       # no key: 15:20

    def test_flatten_with_the_broker_qty(self):
        code, text, _ = run(["flatten", "--symbol", "demo", "--qty", "3,428", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", LATE])
        self.assertEqual(code, 0)
        self.assertNotIn("The qty comes from", text)
        with open(self.orders) as fh:
            self.assertEqual(json.load(fh)["orders"][0]["qty"], 3428)
        for bad in ("0", "2.5", "-5", "lots"):
            code, _, err = run(["flatten", "--symbol", "DEMO", "--qty", bad, "--orders", self.orders])
            self.assertEqual(code, 1, bad)
            self.assertIn("whole number of shares", err)

    def test_market_notes_on_the_psx_clock(self):
        self.risk_step(now="2026-10-29 09:20")                       # a buy is an entry: it waits for the open
        code, text, _ = run(self.args("show", now="2026-10-29 09:20"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY: no new entries now. PSX: pre-open: limit orders only", text)
        self.set_orders(dict(FLATTEN, type="limit", limit_price=101.5))   # a limit sell (an exit) may wait there
        self.risk_step(now="2026-10-29 09:20")
        code, text, _ = run(self.args("show", now="2026-10-29 09:20"))
        self.assertEqual(code, 0, text)
        self.assertIn("note: PSX is in the pre-open. A limit order waits for the opening match", text)
        self.set_orders(demo())
        self.risk_step()
        code, text, _ = run(self.args("show", now=NOW))
        self.assertEqual(code, 0)
        self.assertNotIn("note:", text)                              # 10:15: open, nothing to say
        code, text, _ = run(["flatten", "--symbol", "DEMO", "--qty", "3428", "--orders", self.orders,
                             "--plan", self.none, "--config", self.cfg, "--now", "2026-10-29 15:40"])
        self.assertEqual(code, 0)
        self.assertIn("Note: PSX now: post-close session", text)
        code, text, _ = run(["flatten", "--symbol", "DEMO", "--qty", "3428", "--orders", self.orders,
                             "--plan", self.none, "--config", self.cfg, "--now", "2026-10-31 11:00"])
        self.assertIn("closed all day (weekend)", text)
        code, text, _ = run(["flatten", "--symbol", "DEMO", "--qty", "3428", "--orders", self.orders,
                             "--plan", self.none, "--config", self.cfg, "--now", "2026-10-29 09:20"])
        self.assertIn("Note: PSX is in the pre-open, which takes limit orders only", text)
        code, text, _ = run(["flatten", "--symbol", "DEMO", "--qty", "3428", "--orders", self.orders,
                             "--plan", self.none, "--config", self.cfg, "--now", "2026-10-30 13:00"])
        self.assertIn("Note: PSX now: Juma break", text)

    def test_an_edited_order_is_not_ready(self):
        self.risk_step()
        with open(self.orders) as fh:
            doc = json.load(fh)
        doc["orders"][0]["qty"] = 3429
        with open(self.orders, "w") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.risk_step(now="2026-10-28 10:15")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("the risk check is from 2026-10-28, not today", text)

    def test_a_pass_counts_for_five_minutes(self):
        self.risk_step(now="2026-10-29 10:16")
        code, text, _ = run(self.args("show", now="2026-10-29 10:21"))
        self.assertEqual(code, 0, text)                              # 5 minutes: still READY
        code, text, _ = run(self.args("show", now="2026-10-29 15:27"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY: the psx-risk check is from 10:16 AM, more than 5 minutes ago (it is 3:27 PM "
                      "now); run check.py again", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-29 10:22"))
        self.assertEqual(code, 1)
        self.assertIn("order 1 is not ready: NOT READY: the psx-risk check is from 10:16 AM", err)
        self.assertFalse(os.path.exists(self.state))                 # no yes was recorded
        with open(self.risk) as fh:
            doc = json.load(fh)
        for checked_at, karachi, why in (("2026-10-29T05:10:00Z", "2026-10-29 10:16", "from 10:10 AM"),
                                         (None, "2026-10-29 10:10", "from 10:10 AM"),
                                         ("2026-10-29T05:16:00Z", None, ""),
                                         (None, None, "has no time in it"),
                                         ("2026-10-29T05:30:00Z", "2026-10-29 10:30", "after now")):
            self.write_risk(dict(doc, checked_at=checked_at, now_karachi=karachi))
            code, text, _ = run(self.args("show", now="2026-10-29 10:16"))
            self.assertEqual(code, 1 if why else 0, text)            # the earlier of the two times counts
            self.assertIn(why, text)

    def test_an_entry_waits_for_the_window_and_the_open(self):
        for now, why in (("2026-10-29 15:10", "NOT READY: no new entries after 15:00 Karachi time (it is 15:10)"),
                         ("2026-10-29 09:40", "NOT READY: no new entries before 09:45 Karachi time (it is 09:40)"),
                         ("2026-10-30 13:00", "NOT READY: no new entries now. PSX: Juma break"),
                         ("2026-10-29 15:40", "NOT READY: no new entries now. PSX: post-close session"),
                         ("2026-10-31 11:00", "NOT READY: no new entries now. PSX: closed all day (weekend)"),
                         ("2026-10-29 15:00", "READY: psx-risk PASS")):
            self.risk_step(now=now)                                  # a fresh PASS, as if psx-risk missed it
            code, text, _ = run(self.args("show", now=now))
            self.assertIn(why, text, now)
            code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes", now=now))
            self.assertEqual(code, 0 if why.startswith("READY") else 1, now)
            if code:
                self.assertIn("not ready", err)
        os.remove(self.state)
        self.set_cfg(no_entries_before=None, no_entries_after=None)   # no window: PSX must still be open
        self.risk_step(now="2026-10-29 15:10")
        self.assertEqual(run(self.args("show", now="2026-10-29 15:10"))[0], 0)
        self.risk_step(now="2026-10-29 15:32")
        self.assertIn("PSX: closed", run(self.args("show", now="2026-10-29 15:32"))[1])
        self.set_orders(FLATTEN)                                     # an exit is never held by the window
        self.risk_step(now="2026-10-29 15:25")
        self.set_cfg(no_entries_after="15:00")
        self.assertEqual(run(self.args("show", now="2026-10-29 15:25"))[0], 0)

    def test_the_ticket_and_the_log_use_psx_risks_max_loss(self):
        market = demo(type="market")
        del market["limit_price"]
        self.set_orders(market)
        loss = ticket.max_loss_at_stop(101.30, 99.55, 3428, COSTS)   # psx-risk measured it from 101.30
        self.risk_step(max_loss=loss, stop=99.55, entry_ref=101.3, entry_ref_from="price")
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 0, text)
        self.assertIn("max loss about %s if it fills at 101.30 and the stop fills at 99.55" % ticket.money(loss), text)
        self.assertNotIn("4,999.94 if", text)                        # the plan's number is not the checked one
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes Rs 4,999.94"))[0], 1)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes"))[0], 0)
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--filled-price", "101.30",
                                     "--portfolio", self.pf))
        self.assertEqual(code, 0, err)
        appr, plc, _ = self.state_doc()["orders"]
        self.assertEqual((appr["max_loss"], appr["entry_ref"], plc["max_loss"], plc["entry_ref"]),
                         (loss, 101.3, loss, 101.3))

    def test_the_side_is_not_case_sensitive(self):
        self.set_orders(demo(side="BUY"))
        self.risk_step()
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 0, text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes"))[0], 0)
        code, text, err = run(self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf))
        self.assertEqual(code, 0, err)
        self.assertIn("Paper fill: BUY 3,428 DEMO at 100.65", text)
        pf = self.pf_doc()
        self.assertEqual((pf["cash"], pf["positions"][0]["shares"]), (154353.85, 3428))   # a buy, not a sell
        self.assertEqual([r["side"] for r in self.state_doc()["orders"]], ["buy", "buy", "buy"])
        with open(self.risk) as fh:
            doc = json.load(fh)                                      # psx-risk may fingerprint "buy": same order
        doc["results"][0]["fingerprint"] = ticket.fingerprint(demo())
        self.write_risk(doc)
        self.assertIn("DONE: placed", run(self.args("show"))[1])
        code, text, err = run(["fill", "--symbol", "DEMO", "--side", "SELL", "--qty", "3428", "--price", "101.58",
                               "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.pf_doc()["positions"], [])
        self.assertEqual(self.state_doc()["orders"][-1]["side"], "sell")

    def test_blocked_orders_are_never_ready(self):
        self.risk_step(now="2026-10-29 09:40", verdict="BLOCK",
                       reasons=["window: no new entries before 09:45 Karachi time (it is 09:40)"])
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-29 09:40"))
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED by psx-risk: window: no new entries before 09:45 Karachi time", text)
        self.assertNotIn("In your broker app", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-29 09:40"))
        self.assertEqual(code, 1)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.risk_step()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        self.assertIn("Nothing was placed", text)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)

    def test_real_money_tickets_are_placed_by_you(self):
        self.set_cfg(paper=False)
        self.risk_step()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, REAL MONEY: you place it yourself in your broker app", text)
        self.assertNotIn(DEMO_APP, text)                              # no line for the app before the yes
        self.assertNotIn("before you place it", text)
        self.assertIn("Real money: the line to type into your broker app comes after your yes.", text)
        self.assertNotIn("Paper:", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        for must in ("Now place it yourself in your broker app:", "  " + DEMO_APP, "passwords and PIN",
                     "never in this chat", "--status skipped", "about 5 minutes late: check the price in your app"):
            self.assertIn(must, text)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("APPROVED by you at 2026-10-29T10:15:00+05:00", text)
        self.assertNotIn(DEMO_APP, text)
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "1000",
                                     "--portfolio", self.pf))
        self.assertEqual(code, 1)
        self.assertIn("--filled-qty needs --filled-price too", err)
        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "KT-1",
                                      "--portfolio", self.pf))
        self.assertEqual(code, 0)
        self.assertIn("When it fills, record it: ticket.py fill --symbol DEMO --side buy", text)
        self.assertIn("put the stop 99.55 in your app as a stop-loss order", text)
        self.assertFalse(os.path.exists(self.pf))                 # nothing filled yet
        code, text, _ = run(["fill", "--symbol", "DEMO", "--side", "buy", "--qty", "1000", "--price", "100.60",
                             "--now", "2026-10-29 10:17", "--portfolio", self.pf, "--config", self.cfg,
                             "--state", self.state])
        self.assertEqual(code, 0)
        cost = ticket.leg_cost(100.60, 1000, COSTS)
        self.assertIn("Recorded buy 1,000 DEMO at 100.60 on 2026-10-29", text)
        self.assertEqual(self.pf_doc()["cash"], round(500000 - 100600 - cost, 2))
        self.assertEqual([r["status"] for r in self.state_doc()["orders"]], ["approved", "placed", "filled"])

    def test_a_paper_market_order_needs_a_price(self):
        with open(self.orders) as fh:
            doc = json.load(fh)
        del doc["orders"][0]["limit_price"]
        doc["orders"][0]["type"] = "market"
        with open(self.orders, "w") as fh:
            json.dump(doc, fh)
        self.risk_step()
        run(self.args("approve", "--index", "1", "--reply", "yes 3428 DEMO"))
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf))
        self.assertEqual(code, 1)
        self.assertIn("a paper market order has no price to fill at", err)
        self.assertFalse(os.path.exists(self.pf))
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "3500",
                                     "--filled-price", "100.70", "--portfolio", self.pf))
        self.assertEqual(code, 1)
        self.assertIn("more than the order's 3428 shares", err)
        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "2000",
                                      "--filled-price", "100.70", "--portfolio", self.pf))
        self.assertEqual(code, 0, text)
        self.assertEqual(self.pf_doc()["positions"][0]["shares"], 2000)
        self.assertEqual(self.pf_doc()["cash"], round(500000 - 201400 - ticket.leg_cost(100.70, 2000, COSTS), 2))

    def test_skipped_and_failed_results(self):
        self.risk_step()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        code, _, err = run(self.args("result", "--index", "1", "--status", "failed", "--filled-price", "100.65"))
        self.assertEqual(code, 1)
        self.assertIn("go with --status placed only", err)
        code, text, _ = run(self.args("result", "--index", "1", "--status", "failed", "--note", "no cash in the app",
                                      "--portfolio", self.pf))
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.pf))
        last = self.state_doc()["orders"][-1]
        self.assertEqual((last["status"], last["note"]), ("failed", "no cash in the app"))
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: failed at 2026-10-29T10:15:00+05:00. To try it again: check.py, then a new yes", text)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            ticket.main(self.args("result", "--index", "1", "--status", "denied"))     # no Deny card at PSX
        self.assertEqual(cm.exception.code, 2)
        # a new check after the failure makes it READY again; it still needs a new yes
        self.risk_step(now="2026-10-29 10:20")
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-29 10:20"))
        self.assertEqual(code, 0, text)
        self.assertIn("READY: psx-risk PASS, waiting for your yes", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-29 10:20"))[0], 0)
        self.assertEqual(run(self.args("result", "--index", "1", "--status", "skipped", now="2026-10-29 10:21"))[0], 0)
        self.assertIn("DONE: skipped", run(self.args("show", now="2026-10-29 10:21"))[1])
        self.risk_step(now="2026-10-29 10:22")
        self.assertEqual(run(self.args("show", now="2026-10-29 10:22"))[0], 0)
        run(self.args("decline", "--index", "1", now="2026-10-29 10:22"))              # a no is final today
        self.risk_step(now="2026-10-29 10:25")
        code, text, _ = run(self.args("show", now="2026-10-29 10:25"))
        self.assertEqual(code, 1)
        self.assertIn("DONE: declined", text)
        self.assertNotIn("To try it again", text)

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "OGDC", "shares": 3000, "lots": [
            {"id": "OGDC-1", "date": "2026-10-01", "shares": 2000, "cost": 560000.0, "costs": 1000.0},
            {"id": "OGDC-2", "date": "2026-10-20", "shares": 1000, "cost": 290000.0, "costs": 500.0}]}]}
        with open(self.pf, "w") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "OGDC", "--side", "sell", "--qty", "2500", "--price", "283.29",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        pf = self.pf_doc()
        fee = ticket.leg_cost(283.29, 2500, COSTS)
        self.assertEqual(pf["cash"], round(708225.0 - fee, 2))
        self.assertIn("costs about %s" % ticket.money(fee), text)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["OGDC-2"])
        self.assertEqual((lots[0]["shares"], lots[0]["cost"], lots[0]["costs"]), (500, 145000.0, 250.0))
        self.assertEqual(pf["positions"][0]["shares"], 500)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        for qty in ("2.5", "0"):
            code, _, err = run(["fill", "--symbol", "OGDC", "--side", "sell", "--qty", qty, "--price", "90",
                                "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
            self.assertEqual(code, 1, qty)
            self.assertIn("whole number of shares", err)
        code, _, err = run(["fill", "--symbol", "OGDC", "--side", "sell", "--qty", "501", "--price", "90",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertIn("PSX bans selling shares you do not hold", err)
        self.assertNotIn("Traceback", err)

    def test_day_trade_one_side_drops_the_sell_commission(self):
        costs = dict(COSTS, day_trade_one_side=True)
        self.set_cfg(costs=costs)
        fill = ["fill", "--symbol", "DEMO", "--qty", "3428", "--portfolio", self.pf, "--config", self.cfg,
                "--state", self.state]
        run(fill + ["--side", "buy", "--price", "100.65", "--date", "2026-10-28"])
        run(fill + ["--side", "buy", "--price", "100.65", "--date", TODAY])
        cash = self.pf_doc()["cash"]
        run(fill + ["--side", "sell", "--price", "101.58", "--date", TODAY])   # takes the 10-28 lot: full costs
        self.assertEqual(self.pf_doc()["cash"], round(cash + 348216.24 - 623.66, 2))
        cash = self.pf_doc()["cash"]
        run(fill + ["--side", "sell", "--price", "101.58", "--date", TODAY])   # today's lot: no commission
        other = ticket.leg_cost(101.58, 3428, costs, commission=False)
        self.assertEqual(other, 22.98)                                         # 0.0066% of 348,216.24
        self.assertEqual(self.pf_doc()["cash"], round(cash + 348216.24 - other, 2))

    def test_times_are_karachi_time(self):
        self.risk_step()
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-29T05:15:00Z"))
        self.assertEqual(code, 0)
        self.assertEqual(self.state_doc()["orders"][0]["at"], "2026-10-29T10:15:00+05:00")
        code, text, _ = run(["log", "--state", self.state, "--now", "2026-10-28T19:30:00Z"])   # 00:30 PKT on 10-29
        self.assertEqual(code, 0)
        self.assertIn("approved", text)
        self.assertIn("0 placed today", text)
        code, text, _ = run(["log", "--state", self.state, "--now", "2026-10-28T18:30:00Z"])   # 23:30 PKT on 10-28
        self.assertEqual(code, 1)
        self.assertIn("No decisions recorded today", text)
        code, text, _ = run(["log", "--state", self.state, "--today", "2026-10-28", "--all"])
        self.assertEqual(code, 0)

    def test_bad_inputs_print_one_plain_line(self):
        for argv in (self.args("show", now="half past ten"), self.args("show") + ["--today", "29-10-2026"],
                     self.args("show") + ["--today", "2026-02-30"],
                     ["fill", "--symbol", "DEMO", "--side", "buy", "--qty", "10", "--price", "-1",
                      "--portfolio", self.pf, "--config", self.cfg, "--state", self.state],
                     ["fill", "--symbol", "DEMO", "--side", "buy", "--qty", "10", "--price", "100",
                      "--date", "yesterday", "--portfolio", self.pf, "--config", self.cfg, "--state", self.state]):
            code, out, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertTrue(err.startswith("ticket.py: "), err)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertNotIn("Traceback", err)
        self.set_cfg(flat_by="3:20 pm")
        code, _, err = run(["flatten", "--symbol", "DEMO", "--qty", "3428", "--orders", self.orders,
                            "--plan", self.none, "--config", self.cfg, "--now", LATE])
        self.assertEqual(code, 1)
        self.assertIn('"flat_by" in psx/config.json wants a Karachi time like 10:15', err)
        with open(self.orders, "w") as fh:
            fh.write("{not json")
        code, _, err = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("is not valid JSON", err)
        self.assertEqual(run([])[0], 1)

    def test_bad_file_shapes_print_one_plain_line(self):
        def put(path, doc):
            with open(path, "w") as fh:
                json.dump(doc, fh)
        self.risk_step()
        fills = [["fill", "--symbol", "DEMO", "--side", side, "--qty", "100", "--price", "100.65", "--portfolio",
                  self.pf, "--config", self.cfg, "--state", self.state] for side in ("buy", "sell")]
        flatten = ["flatten", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf, "--plan",
                   self.none, "--config", self.cfg, "--now", LATE]
        for bad in ([{"ticker": "DEMO", "shares": 100}], {"positions": None}, {"positions": {"DEMO": 100}},
                    {"positions": ["DEMO"]}):
            put(self.pf, bad)
            for argv in fills + [flatten]:
                code, _, err = run(argv)
                self.assertEqual((code, len(err.strip().splitlines())), (1, 1), err)
                self.assertTrue(err.startswith("ticket.py: %s (the portfolio)" % self.pf), err)
        os.remove(self.pf)
        for path, bad, want in ((self.cfg, [1], "(the config) must be a JSON object"),
                                (self.risk, [1], "(the risk check) must be a JSON object"),
                                (self.risk, {"date": TODAY, "results": None}, '"results" must be a list'),
                                (self.state, {"orders": None}, '"orders" must be a list')):
            keep = None
            if os.path.exists(path):
                with open(path) as fh:
                    keep = fh.read()
            put(path, bad)
            for argv in (self.args("show"), self.args("approve", "--index", "1", "--reply", "yes")):
                code, _, err = run(argv)
                self.assertEqual((code, len(err.strip().splitlines())), (1, 1), err)
                self.assertIn(want, err)
                self.assertNotIn("Traceback", err)
            if keep is None:
                os.remove(path)
            else:
                with open(path, "w") as fh:
                    fh.write(keep)

    def test_output_is_plain_and_has_no_money_claims(self):
        self.risk_step()
        texts = [run(self.args("show"))[1], run(self.args("show", "--json"))[1],
                 run(self.args("approve", "--index", "1", "--reply", "yes"))[1],
                 run(self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf))[1],
                 run(["log", "--state", self.state, "--now", NOW])[1],
                 run(["flatten", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf,
                      "--plan", self.plan, "--config", self.cfg, "--now", LATE])[1]]
        self.set_cfg(paper=False)
        self.risk_step(now=LATE)
        texts += [run(self.args("show", now=LATE))[1],
                  run(self.args("approve", "--index", "1", "--reply", "yes", now=LATE))[1]]
        for text in texts:
            low = text.lower().replace('"take_profit"', "")     # the order field's name is not a money claim
            for word in BANNED:
                self.assertNotIn(word, low, word)
            self.assertNotIn(chr(0x2014), text)                 # no em dash
            self.assertIsNone(re.search(r"\bET\b|New York|Alpaca", text))


@unittest.skipUnless(PSX_RISK, "psx-risk/check.py is not the PSX version yet")
class WithPsxRisk(unittest.TestCase):
    """The real psx-risk check, run on the EXAMPLE.md order (these run once psx-risk is converted)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--universe", self.none,
                               "--offline", "--data", os.path.join(FIX, "psx-market"), "--now", now,
                               "--out", self.risk])

    def test_the_fingerprint_is_shared(self):
        for o in (demo(), FLATTEN):
            self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))

    def test_psx_risk_passes_the_example_and_counts_the_fill(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0, text)
        self.assertIn("READY: psx-risk PASS", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes"))[0], 0)
        self.assertEqual(run(self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf,
                                       now="2026-10-29 10:16"))[0], 0)
        self.assertEqual(self.run_risk(now="2026-10-29 10:30"), 1)     # the same buy again: already placed today
        code, _, _ = run(["flatten", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf,
                          "--config", self.cfg, "--plan", self.none, "--now", LATE])
        self.assertEqual(code, 0)
        self.assertEqual(self.run_risk(now=LATE), 0)                    # an exit of shares you hold passes
        with open(self.risk) as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=LATE))
        self.assertEqual(code, 0, text)


# ---------------------------------------------------------------- swing style (SPEC v2 2.4 psx-broker, Task 4)

V2 = os.path.join(FIX, "psx-v2", "contract")      # the shared made-up file shapes (read only)
V2_PATHS = os.path.join(FIX, "psx-v2", "paths")    # made-up DEMO closing files after Thu 2026-10-29 (read only)
PRE_OPEN = "2026-10-29 09:20"                      # the pre-open of the buy day, Thu 2026-10-29
FP_BUY = "0d80ffbb0d898a06"                        # orders-swing.json's buy, as in the contract broker files
FP_143 = "4f1f7cf1107d6b31"                        # the protect sell of 143 DEMO in the contract broker files
SELL_143 = "SELL 143 DEMO, stop-limit, trigger 96.25, limit 95.28, good for today, REG board"
APP_143 = "SELL DEMO, REG board, quantity 143, STOP LIMIT trigger price 96.25, limit price 95.28, valid for the day"
LEDGER_KEYS = ["style", "date", "stop", "stop_at_buy", "target", "exit_by", "exit_by_time", "trend_sessions",
               "setup", "max_loss", "scaled", "from"]
CLOSE_KEYS = ["at", "date", "index", "fingerprint", "symbol", "side", "plain", "status", "of"]
SWING_COSTS = ticket.trade_costs({"style": "swing"})
REAL_WORDS = ('%s holds real shares ("real_holdings": true). A paper fill would change it, so nothing was written. '
              'Record a real fill with paper off, or with ticket.py fill --real, or use a paper folder.')   # SPEC v2 5.2
PAPER_LINE = "Paper: no stop order rests anywhere. psx-alerts rings at the stop; then ticket.py flatten."


def contract(name):
    with open(os.path.join(V2, name), encoding="utf-8") as fh:
        return json.load(fh)


def holding(qty=143):
    """psx/portfolio.json after a paper buy of qty DEMO at 99.38 on Thu 2026-10-29, with its swing plan:
    portfolio-swing.json for qty shares (143 is the Rs 500 a trade size, 1,436 the Rs 5,000 one)."""
    pf = contract("portfolio-swing.json")
    pos = pf["positions"][0]
    cost, fee = round(qty * 99.38, 2), ticket.leg_cost(99.38, qty, SWING_COSTS)
    pos["shares"] = qty
    pos["lots"][0].update(shares=qty, cost=cost, costs=fee)
    pos["plan"]["max_loss"] = ticket.max_loss_at_stop(99.38, 96.25, qty, SWING_COSTS)
    pf["cash"] = round(500000 - cost - fee, 2)
    return pf


class SwingBase(unittest.TestCase):
    """A temp folder: the example config with "style": "swing" (paper), and orders-swing.json as psx/orders.json
    (1,436 DEMO, limit 99.38, stop 96.25, target 105.64, made at 9:20 AM on Thu 2026-10-29)."""
    STYLE = "swing"

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="psx-broker-swing-")
        self.cfg = os.path.join(self.d, "config.json")
        with open(CFG_PATH) as fh:
            c = json.load(fh)
        c["style"] = self.STYLE
        self.put(self.cfg, c)
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(os.path.join(V2, "orders-swing.json"), self.orders)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.plan = os.path.join(self.d, "plan.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def put(self, path, doc):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)

    def doc(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def raw(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def set_cfg(self, **kw):
        c = self.doc(self.cfg)
        c.update(kw)
        self.put(self.cfg, c)

    def set_orders(self, *orders):
        self.put(self.orders, {"created_at": "2026-10-29T04:20:00Z", "orders": list(orders)})

    def copy_state(self, name):
        os.makedirs(os.path.dirname(self.state), exist_ok=True)
        shutil.copy(os.path.join(V2, name), self.state)

    def market(self, path):
        """A data folder: the sample market's closing files (to Thu 2026-10-29) plus one made-up path's
        (fixtures/psx-v2/paths: up, gap, trend, flat, bonus). -> its path."""
        data = os.path.join(self.d, "data-" + path)
        shutil.copytree(os.path.join(FIX, "psx-market", "mkt_summary"), os.path.join(data, "mkt_summary"))
        extra = os.path.join(V2_PATHS, path, "mkt_summary")
        for name in sorted(os.listdir(extra)):
            shutil.copy(os.path.join(extra, name), os.path.join(data, "mkt_summary", name))
        return data

    def clock(self, now):
        """Pretend the computer's own clock says `now` (Karachi time), for a real-money yes (F64)."""
        return mock.patch.object(ticket, "utc_now", lambda: ticket.parse_now(now))

    def tk(self, *argv, **kw):
        """Run ticket.py. Every line it prints passes the shared word test (tests/_words.py, D60) and the v1
        money words; code= checks the exit code."""
        code, out, err = run(list(argv))
        text = out + err
        check_words(self, text)
        low = text.replace(self.d, "<tmp>").replace(os.path.realpath(self.d), "<tmp>").lower()
        for word in BANNED:
            self.assertNotIn(word, low.replace('"take_profit"', ""), word)
        self.assertNotIn("Traceback", text)
        if "code" in kw:
            self.assertEqual(code, kw["code"], text)
        return code, out, err

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", PRE_OPEN)]

    def fill_args(self, *extra, **kw):
        return ["fill", "--symbol", "DEMO"] + list(extra) + [
            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state, "--orders", self.orders,
            "--plan", self.plan, "--now", kw.get("now", "2026-10-29 10:00")]

    def risk_step(self, now=PRE_OPEN, verdict="PASS"):
        """A made-up psx-risk check of every order in psx/orders.json (the risk.json contract). Like check.py,
        a limit order's entry_ref is its limit price."""
        doc = self.doc(self.orders)
        results = [{"index": i, "symbol": o["symbol"], "side": o["side"],
                    "kind": "entry" if o["side"].lower() == "buy" else "exit", "fingerprint": ticket.fingerprint(o),
                    "entry_ref": o.get("limit_price") if o.get("type") == "limit" else None,
                    "verdict": verdict, "reasons": [], "warnings": [], "info": []}
                   for i, o in enumerate(doc["orders"], 1)]
        self.put(self.risk, {"date": now[:10], "now_karachi": now, "results": results,
                             "checked_at": ticket.utc_iso(ticket.parse_now(now))})

    def buy(self, now=PRE_OPEN, at="2026-10-29 09:21", extra=()):
        """The swing buy on paper: a PASS, the yes, then result --status placed (the paper fill). -> its output."""
        self.risk_step(now)
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now=now), code=0)
        return self.tk(*self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf, *extra,
                                  now=at), code=0)[1]


class SwingTickets(SwingBase):
    """READY in swing (swing_window), the swing ticket words, and the ledger plan of a fill (items 1 to 3, 6, 7)."""

    def test_a_pre_open_limit_buy_is_ready_in_swing(self):
        self.risk_step()
        out = self.tk(*self.args("show"), code=0)[1]
        self.assertIn("  BUY 1,436 DEMO, limit 99.38, good for today, REG board\n", out)
        self.assertIn("  plan stop 96.25: after the buy fills, ticket.py protect makes the stop-limit ticket (psx-risk"
                      " and your yes first)\n", out)
        self.assertNotIn("as a stop-loss order", out)
        self.assertIn("max loss about Rs 4,997.81 if the stop fills at 96.25, costs included", out)
        self.assertIn("  READY: psx-risk PASS, waiting for your yes\n", out)
        self.assertIn("note: PSX is in the pre-open. A limit order waits for the opening match", out)
        self.assertNotIn("In your broker app", out)                    # the line comes after the yes
        out = self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=0)[1]
        self.assertIn("  In your broker app: BUY DEMO, REG board, quantity 1436, LIMIT price 99.38, valid for the day\n",
                      out)
        self.assertIn("PSX orders last one day: a limit buy that does not fill today ends at the close. Do not chase it."
                      " If it does not fill, record it: ticket.py result --index 1 --status expired (on a later day: "
                      "--fingerprint %s)." % FP_BUY, out)

    def test_a_market_buy_in_the_pre_open_is_not_ready(self):
        o = contract("orders-swing.json")["orders"][0]
        o["type"] = "market"
        del o["limit_price"]
        self.set_orders(o)
        self.risk_step()
        out = self.tk(*self.args("show"), code=1)[1]
        self.assertIn("NOT READY: PSX takes no market orders in the pre-open: use a limit order", out)
        err = self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=1)[2]
        self.assertIn("not ready", err)
        self.assertFalse(os.path.exists(self.state))

    def test_the_swing_window_has_no_times_and_the_day_window_stays(self):
        for now, want in (("2026-10-29 09:31", "NOT READY: the opening match runs until 9:32 AM PKT: no new orders yet"),
                          ("2026-10-29 09:33", "READY: psx-risk PASS"),
                          ("2026-10-29 15:10", "READY: psx-risk PASS"),           # after the day window: fine in swing
                          ("2026-10-30 14:20", "READY: psx-risk PASS"),           # Friday: the pre-open after the break
                          ("2026-10-30 13:00", "NOT READY: no new entries now: Juma break"),
                          ("2026-10-29 15:40", "NOT READY: no new entries now: post-close session"),
                          ("2026-11-09 10:00", "NOT READY: no new entries now: closed all day (PSX holiday: Iqbal Day)")):
            self.risk_step(now)
            self.assertIn(want, self.tk(*self.args("show", now=now))[1], now)
        self.set_cfg(style="day")                                         # a day folder keeps the v1 window
        for now, want in (("2026-10-29 09:20", "NOT READY: no new entries now. PSX: pre-open"),
                          ("2026-10-29 15:10", "NOT READY: no new entries after 15:00 Karachi time (it is 15:10)")):
            self.risk_step(now)
            self.assertIn(want, self.tk(*self.args("show", now=now))[1], now)

    def test_a_paper_fill_writes_the_swing_plan_into_the_ledger(self):
        out = self.buy()
        want = contract("portfolio-swing.json")                           # SPEC v2 2.5, key for key
        want["positions"][0]["plan"]["from"] = self.orders
        pf = self.doc(self.pf)
        self.assertEqual(pf, want)
        self.assertEqual(list(pf["positions"][0]["plan"]), LEDGER_KEYS)
        self.assertNotIn("real_holdings", pf)                             # paper never sets the flag
        self.assertIn("Paper fill: BUY 1,436 DEMO at 99.38, costs about Rs 255.59. Cash now Rs 357,034.73", out)
        self.assertIn("\nNext: ticket.py protect --symbol DEMO for the stop-limit order. Hold up to Fri 2026-11-27. "
                      "The alerts come from %s now.\n" % self.pf, out)
        for gone in ("Be flat by", "alerts.py add --from-plan", "stop-loss order"):
            self.assertNotIn(gone, out)
        st = self.doc(self.state)["orders"]
        self.assertEqual([r["status"] for r in st], ["approved", "placed", "filled"])
        self.assertEqual((st[0]["max_loss"], st[1]["max_loss"]), (4997.81, 4997.81))

    def test_a_live_placed_buy_waits_for_its_fill(self):
        self.set_cfg(paper=False)
        self.risk_step()
        with self.clock(PRE_OPEN):                                        # a real-money yes is on the real clock
            self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=0)
        out = self.tk(*self.args("result", "--index", "1", "--status", "placed", "--broker-id", "FS-1001",
                                 "--portfolio", self.pf, now="2026-10-29 09:22"), code=0)[1]
        self.assertIn("When it fills, record it: ticket.py fill --symbol DEMO --side buy --qty Q --price P", out)
        self.assertIn("After the fill: ticket.py protect --symbol DEMO for the stop-limit order. Hold up to Fri "
                      "2026-11-27. The alerts come from %s then." % self.pf, out)
        self.assertFalse(os.path.exists(self.pf))
        self.assertEqual(self.doc(self.state), contract("broker-placed-unfilled.json"))   # the contract records
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "1436", "--price", "99.38",
                                      now="2026-10-29 09:40"), code=0)[1]       # the fill, from today's order
        self.assertIn("Swing plan from %s: stop 96.25, target 105.64, exit by Fri 2026-11-27 at 4:20 PM PKT."
                      % self.orders, out)
        pf = self.doc(self.pf)
        self.assertEqual((list(pf)[0], pf["positions"][0]["plan"]["stop_at_buy"]), ("real_holdings", 96.25))

    def test_a_day_buy_gets_no_plan_key(self):
        self.set_cfg(style="day")
        shutil.copy(ORDERS_PATH, self.orders)
        self.risk_step(NOW)
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now=NOW), code=0)
        out = self.tk(*self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf, now=NOW),
                      code=0)[1]
        self.assertIn("Be flat by 15:20 Karachi time: ticket.py flatten --symbol DEMO.", out)
        self.assertNotIn("protect", out)
        self.assertEqual(self.doc(self.pf), {"cash": 154353.85, "positions": [{"ticker": "DEMO", "shares": 3428, "lots": [
            {"id": "DEMO-1", "date": TODAY, "shares": 3428, "cost": 345028.2, "costs": 617.95}]}]})   # v1, exactly

    def test_swing_pays_commission_on_both_sides(self):
        costs = dict(COSTS, day_trade_one_side=True)                     # D30: ignored in swing
        self.set_cfg(costs=costs)
        self.tk(*self.fill_args("--side", "buy", "--qty", "1436", "--price", "99.38"), code=0)
        cash = self.doc(self.pf)["cash"]
        self.assertEqual(cash, 357034.73)
        self.tk(*self.fill_args("--side", "sell", "--qty", "1436", "--price", "101.58"), code=0)   # the same day
        fee = ticket.leg_cost(101.58, 1436, costs, True)
        self.assertGreater(fee, ticket.leg_cost(101.58, 1436, costs, False))
        self.assertEqual(self.doc(self.pf), {"cash": round(cash + 145868.88 - fee, 2), "positions": []})   # plan gone

    def test_a_sell_takes_a_lot_with_no_date_first(self):
        self.set_cfg(style="day")
        self.put(self.pf, {"cash": 0, "positions": [{"ticker": "DEMO", "shares": 650, "lots": [
            {"id": "DEMO-2", "date": "2026-10-20", "shares": 100, "cost": 9500.0, "costs": 20.0},
            {"id": "DEMO-1", "date": None, "shares": 500, "cost": 46250.0},
            {"id": "DEMO-3", "shares": 50, "cost": 4600.0}]}]})
        self.tk(*self.fill_args("--side", "sell", "--qty", "550", "--price", "98.40", "--date", TODAY), code=0)
        self.assertEqual(self.doc(self.pf)["positions"][0]["lots"],
                         [{"id": "DEMO-2", "date": "2026-10-20", "shares": 100, "cost": 9500.0, "costs": 20.0}])

    def test_the_stop_limit_words(self):
        o = dict(contract("orders-protect.json")["orders"][0], qty=143)
        self.assertEqual(ticket.TYPE_WORDS["stop_limit"], "stop-limit")
        self.assertEqual((ticket.order_line(o), ticket.plain(o, SWING_COSTS), ticket.app_line(o)),
                         (SELL_143, SELL_143, APP_143))
        self.assertIn(96.25, ticket.amount_numbers(o, SWING_COSTS))       # so "yes 96.25" counts
        self.assertEqual(ticket.fingerprint(o), FP_143)                   # the trigger is in the fingerprint
        self.assertNotEqual(ticket.fingerprint(o), ticket.fingerprint(dict(o, trigger_price=None)))
        self.assertEqual(ticket.fingerprint(demo()), "e9d01b16c66c4a20")  # the DEMO day order does not change


class SwingFill(SwingBase):
    """fill attaches a swing plan to a new position: today's order, else psx/plan.json, else the flags (item 2)."""
    BUY = ("--side", "buy", "--qty", "1436", "--price", "99.38")
    FLAGS = ("--stop", "96.25", "--target", "105.64", "--exit-by", "2026-11-27")

    def test_fill_takes_the_plan_of_todays_swing_order(self):
        out = self.tk(*self.fill_args(*self.BUY), code=0)[1]
        want = contract("portfolio-swing.json")["positions"][0]["plan"]
        want["from"] = self.orders
        self.assertEqual(self.doc(self.pf)["positions"][0]["plan"], want)
        self.assertIn("Swing plan from %s: stop 96.25, target 105.64, exit by Fri 2026-11-27 at 4:20 PM PKT.\n"
                      % self.orders, out)
        self.assertIn("Next: ticket.py protect --symbol DEMO for the stop-limit order. Hold up to Fri 2026-11-27.", out)

    def test_fill_takes_the_plan_from_plan_json(self):
        os.remove(self.orders)
        shutil.copy(os.path.join(V2, "plan-swing.json"), self.plan)
        out = self.tk(*self.fill_args(*self.BUY), code=0)[1]
        plan = self.doc(self.pf)["positions"][0]["plan"]
        self.assertEqual(list(plan), LEDGER_KEYS)
        self.assertEqual(plan, dict(contract("portfolio-swing.json")["positions"][0]["plan"], **{"from": self.plan}))
        self.assertIn("Swing plan from %s:" % self.plan, out)

    def test_fill_takes_the_plan_from_the_flags(self):
        os.remove(self.orders)
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "143", "--price", "99.38", *self.FLAGS), code=0)[1]
        self.assertEqual(self.doc(self.pf)["positions"][0]["plan"], {
            "style": "swing", "date": "2026-10-29", "stop": 96.25, "stop_at_buy": 96.25, "target": 105.64,
            "exit_by": "2026-11-27", "exit_by_time": "16:20", "trend_sessions": 10, "setup": None, "max_loss": 497.69,
            "scaled": [], "from": "by hand"})
        self.assertIn("Swing plan from your --stop, --target and --exit-by: stop 96.25, target 105.64, exit by Fri "
                      "2026-11-27 at 4:20 PM PKT.", out)
        os.remove(self.pf)                                               # a stop rounds down, a target half up
        self.tk(*self.fill_args("--side", "buy", "--qty", "143", "--price", "99.38", "--stop", "96.259", "--target",
                                "105.645", "--exit-by", "2026-11-30"), code=0)
        plan = self.doc(self.pf)["positions"][0]["plan"]
        self.assertEqual((plan["stop"], plan["target"], plan["exit_by_time"]), (96.25, 105.65, "15:20"))

    def test_no_swing_plan_is_refused_and_nothing_is_written(self):
        os.remove(self.orders)
        line = ("ticket.py: No swing plan for DEMO on 2026-10-29: give --stop, --target and --exit-by, so its alerts "
                "and risk count.\n")
        self.assertEqual(self.tk(*self.fill_args(*self.BUY), code=1)[2], line)
        for plan in (dict(contract("plan-swing.json"), symbol="OGDC"), self.doc(PLAN_PATH)):   # another stock; a day plan
            self.put(self.plan, plan)
            self.assertEqual(self.tk(*self.fill_args(*self.BUY), code=1)[2], line)
        with open(self.plan, "w") as fh:
            fh.write("{not json")                                        # a broken plan file is no plan, never a crash
        self.assertEqual(self.tk(*self.fill_args(*self.BUY), code=1)[2], line)
        self.assertFalse(os.path.exists(self.pf))
        self.assertFalse(os.path.exists(self.state))

    def test_an_old_swing_plan_asks_for_the_flags(self):
        shutil.copy(os.path.join(V2, "plan-swing.json"), self.plan)       # both are for Thu 2026-10-29
        err = self.tk(*self.fill_args(*(self.BUY + ("--date", "2026-10-30")), now="2026-10-30 10:00"), code=1)[2]
        self.assertIn("No swing plan for DEMO on 2026-10-30: give --stop, --target and --exit-by, so its alerts and "
                      "risk count. The newest swing plan of DEMO (%s) is for 2026-10-29." % self.orders, err)
        self.assertFalse(os.path.exists(self.pf))
        self.tk(*self.fill_args(*(self.BUY + ("--date", "2026-10-30") + self.FLAGS), now="2026-10-30 10:00"), code=0)
        plan = self.doc(self.pf)["positions"][0]["plan"]
        self.assertEqual((plan["date"], plan["from"]), ("2026-10-30", "by hand"))

    def test_the_flags_need_a_swing_folder_and_a_new_position(self):
        for extra, why in ((self.FLAGS[:2], "--stop, --target and --exit-by go together"),
                           (("--stop", "105.64", "--target", "96.25", "--exit-by", "2026-11-27"), "must be under --target"),
                           (("--stop", "96.25", "--target", "105.64", "--exit-by", "2026-10-28"), "before the fill date"),
                           (("--stop", "0", "--target", "105.64", "--exit-by", "2026-11-27"), "want prices above 0"),
                           (("--stop", "96.25", "--target", "105.64", "--exit-by", "27-11-2026"), "--exit-by wants a date"),
                           (("--stop", "96.25", "--target", "105.64", "--exit-by", "2026-11-09"),
                            "--exit-by 2026-11-09 is not a PSX trading day (PSX holiday: Iqbal Day)")):
            self.assertIn(why, self.tk(*self.fill_args(*(self.BUY + extra)), code=1)[2], extra)
        err = self.tk(*self.fill_args(*(("--side", "sell", "--qty", "10", "--price", "99") + self.FLAGS)), code=1)[2]
        self.assertIn("go with a buy that opens a position", err)
        self.assertFalse(os.path.exists(self.pf))
        self.tk(*self.fill_args(*self.BUY), code=0)
        err = self.tk(*self.fill_args(*(self.BUY + self.FLAGS)), code=1)[2]
        self.assertIn("DEMO is already in %s: --stop, --target and --exit-by set the plan of a new position only" % self.pf,
                      err)
        self.set_cfg(style="day")
        self.assertIn('they need "style": "swing" in psx/config.json', self.tk(*self.fill_args(*(self.BUY + self.FLAGS)),
                                                                               code=1)[2])

    def test_a_share_held_with_no_swing_plan_is_named_plainly(self):
        # F02: a share bought before the switch has no plan. fill, stop and protect say so, and never name a step
        # that refuses it too (the old fill words said "ticket.py stop --symbol UBL --set X")
        self.set_cfg(paper=False)
        self.put(self.pf, {"real_holdings": True, "cash": 40000.0, "positions": [HAND]})
        before = self.raw(self.pf)
        tail = ["--portfolio", self.pf, "--config", self.cfg, "--state", self.state, "--now", "2026-11-11 10:00"]
        err = self.tk(*(["fill", "--symbol", "UBL", "--side", "buy", "--qty", "1", "--price", "390", "--stop", "375",
                         "--target", "410", "--exit-by", "2026-11-27", "--orders", self.orders, "--plan", self.plan]
                        + tail), code=1)[2]
        self.assertEqual(err, "ticket.py: UBL is already in %s, with no swing plan. --stop, --target and --exit-by give "
                              "a plan to a new position only. ticket.py stop cannot give it one either: it changes only a "
                              "swing plan. Only your own rules in psx/alerts.json watch UBL. Nothing was written.\n"
                              % self.pf)
        err = self.tk(*(["stop", "--symbol", "UBL", "--set", "380"] + tail), code=1)[2]
        self.assertEqual(err, "ticket.py: no swing plan for UBL in %s: ticket.py stop changes the stop of a held swing "
                              "position. Only your own rules in psx/alerts.json watch UBL.\n" % self.pf)
        err = self.tk(*(["protect", "--symbol", "UBL", "--orders", self.orders] + tail), code=1)[2]
        self.assertEqual(err, "ticket.py: no swing plan stop for UBL in %s, so there is no stop to protect. A swing plan "
                              "is written only when a swing buy fills (ticket.py result, or ticket.py fill --stop --target "
                              "--exit-by). Only your own rules in psx/alerts.json watch UBL.\n" % self.pf)
        self.assertEqual(self.raw(self.pf), before)
        self.assertFalse(os.path.exists(self.state))
        err = self.tk(*(["stop", "--symbol", "HBL", "--set", "380"] + tail), code=1)[2]     # not held: the old words
        self.assertEqual(err, "ticket.py: no swing plan for HBL in %s: ticket.py stop changes the stop of a held swing "
                              "position\n" % self.pf)

    def test_a_later_fill_of_the_same_buy_keeps_its_plan(self):
        self.risk_step()
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=0)
        self.tk(*self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "1000", "--filled-price",
                           "99.30", "--portfolio", self.pf, now="2026-10-29 09:40"), code=0)
        plan = self.doc(self.pf)["positions"][0]["plan"]
        self.assertEqual((plan["stop"], plan["max_loss"]), (96.25, ticket.max_loss_at_stop(99.30, 96.25, 1000,
                                                                                            SWING_COSTS)))
        os.remove(self.orders)                                           # no plan is needed: the position has one
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "436", "--price", "99.38"), code=0)[1]
        self.assertNotIn("Swing plan from", out)
        pos = self.doc(self.pf)["positions"][0]
        self.assertEqual((pos["shares"], pos["plan"]), (1436, plan))


class SwingResult(SwingBase):
    """result --status expired or cancelled; an order of any day named by --fingerprint or --symbol --date (D33)."""

    def by_record(self, *argv, **kw):
        extra = {"code": kw["code"]} if "code" in kw else {}
        return self.tk("result", *(argv + ("--state", self.state, "--now", kw.get("now", "2026-10-30 16:15"))), **extra)

    def test_expired_by_fingerprint_closes_a_placed_buy_of_an_earlier_day(self):
        self.copy_state("broker-placed-unfilled.json")
        os.remove(self.orders)                                           # no psx/orders.json is needed
        out = self.by_record("--fingerprint", FP_BUY, "--status", "expired", code=0)[1]
        self.assertEqual(self.doc(self.state), contract("broker-expired.json"))      # the contract record, key for key
        self.assertEqual(list(self.doc(self.state)["orders"][-1]), CLOSE_KEYS)
        self.assertEqual(out, "Recorded the DEMO buy of Thu 2026-10-29 as expired: the order ended at the close without "
                              "a fill.\n  BUY 1,436 DEMO, limit 99.38, good for today, REG board (fingerprint %s)\n"
                              "Nothing else was written. psx-risk counts this order as closed now.\n" % FP_BUY)
        self.assertFalse(os.path.exists(self.pf) or os.path.exists(self.orders))
        err = self.by_record("--fingerprint", FP_BUY, "--status", "expired", code=1)[2]          # closed already
        self.assertIn('the newest record of this order is "expired" on 2026-10-30, not approved or placed', err)

    def test_expired_by_fingerprint_closes_a_buy_that_was_approved_and_never_placed(self):
        self.copy_state("broker-approved-old.json")
        self.by_record("--fingerprint", FP_BUY.upper(), "--status", "expired", "--note", "not placed", code=0)
        last = self.doc(self.state)["orders"][-1]
        self.assertEqual(list(last), CLOSE_KEYS + ["note"])
        self.assertEqual((last["status"], last["of"], last["date"], last["index"]), ("expired", "2026-10-29",
                                                                                      "2026-10-30", 1))

    def test_symbol_and_date_find_the_order_and_two_orders_are_refused(self):
        self.copy_state("broker-placed-unfilled.json")
        self.by_record("--symbol", "demo", "--date", "2026-10-29", "--status", "cancelled", code=0)
        self.assertEqual(self.doc(self.state)["orders"][-1]["status"], "cancelled")
        self.copy_state("broker-approved-old.json")                      # two open orders of DEMO on 10-29
        st = self.doc(self.state)
        st["orders"].append(dict(st["orders"][0], fingerprint="1111111111111111", qty=1000,
                                 at="2026-10-29T09:25:00+05:00"))
        self.put(self.state, st)
        before = self.raw(self.state)
        err = self.by_record("--symbol", "DEMO", "--date", "2026-10-29", "--status", "expired", code=1)[2]
        self.assertIn("2 orders of DEMO on 2026-10-29 match: name one with --fingerprint (%s, 1111111111111111)" % FP_BUY,
                      err)
        self.assertEqual(self.raw(self.state), before)
        self.by_record("--fingerprint", "1111111111111111", "--status", "skipped", code=0)
        self.by_record("--symbol", "DEMO", "--date", "2026-10-29", "--status", "expired", code=0)   # one is left open
        self.assertEqual([r["status"] for r in self.doc(self.state)["orders"]], ["approved", "approved", "skipped",
                                                                                 "expired"])
        for argv, why in ((("--symbol", "HBL", "--date", "2026-10-29"), "no order of HBL with a yes on 2026-10-29"),
                          (("--symbol", "DEMO"), "--symbol and --date go together"),
                          (("--symbol", "DEMO", "--date", "29-10-2026"), "--date wants a date like 2026-10-29"),
                          (("--fingerprint", FP_BUY, "--symbol", "DEMO", "--date", "2026-10-29"), "name the order one way"),
                          (("--fingerprint", "ffffffffffffffff"), "no order with the fingerprint ffffffffffffffff")):
            self.assertIn(why, self.by_record(*(argv + ("--status", "expired")), code=1)[2], argv)

    def test_only_expired_skipped_or_cancelled_can_be_recorded_that_way(self):
        self.copy_state("broker-placed-unfilled.json")
        before = self.raw(self.state)
        for status in ("placed", "failed"):
            err = self.by_record("--fingerprint", FP_BUY, "--status", status, code=1)[2]
            self.assertIn("only --status expired, skipped or cancelled can be recorded. A fill is recorded with "
                          "ticket.py fill", err)
        err = self.by_record("--fingerprint", FP_BUY, "--status", "expired", "--filled-price", "99", code=1)[2]
        self.assertIn("--filled-qty and --filled-price go with --status placed only", err)
        self.assertEqual(self.raw(self.state), before)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            ticket.main(["result", "--status", "expired", "--state", self.state])     # which order?
        self.assertEqual(cm.exception.code, 2)

    def test_a_declined_or_filled_order_is_not_open(self):
        st = contract("broker-placed-unfilled.json")
        st["orders"].append(dict(st["orders"][0], status="declined", fingerprint="2222222222222222"))
        st["orders"].append({"at": "2026-10-29T10:00:00+05:00", "date": "2026-10-29", "status": "filled",
                             "symbol": "DEMO", "side": "buy", "qty": 1436, "price": 99.38, "costs": 255.59,
                             "plain": "BUY 1,436 DEMO at 99.38"})
        self.put(self.state, st)
        before = self.raw(self.state)
        err = self.by_record("--fingerprint", FP_BUY, "--status", "expired", code=1)[2]
        self.assertIn("buy DEMO has a fill recorded on or after 2026-10-29, so this order is not open", err)
        err = self.by_record("--fingerprint", "2222222222222222", "--status", "expired", code=1)[2]
        self.assertIn('the newest record of this order is "declined"', err)
        self.assertEqual(self.raw(self.state), before)

    def test_expired_with_index_after_a_paper_yes(self):
        self.risk_step()
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=0)
        out = self.tk(*self.args("result", "--index", "1", "--status", "expired", "--portfolio", self.pf,
                                 now="2026-10-29 15:35"), code=0)[1]
        self.assertEqual(out, "Recorded order 1 as expired: the order ended at the close without a fill.\n")
        self.assertFalse(os.path.exists(self.pf))                         # nothing filled
        st = self.doc(self.state)["orders"]
        self.assertEqual(st[-1], {"at": "2026-10-29T15:35:00+05:00", "date": TODAY, "index": 1, "fingerprint": FP_BUY,
                                  "symbol": "DEMO", "side": "buy", "plain": st[0]["plain"], "status": "expired",
                                  "of": TODAY})
        self.assertIn("DONE: expired at 2026-10-29T15:35:00+05:00. To try it again: check.py, then a new yes",
                      self.tk(*self.args("show", now="2026-10-29 15:35"), code=1)[1])

    def test_expired_with_index_needs_a_yes_of_today(self):
        self.risk_step()
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=0)
        err = self.tk(*self.args("result", "--index", "1", "--status", "expired", now="2026-10-30 10:00"), code=1)[2]
        self.assertIn("order 1 has no yes recorded today, so there is nothing to record as expired. For an order of an "
                      "earlier day: ticket.py result --fingerprint FP --status expired", err)
        self.by_record("--fingerprint", FP_BUY, "--status", "expired", code=0, now="2026-10-30 10:00")
        err = self.tk(*self.args("result", "--index", "1", "--status", "placed", now="2026-10-30 10:00"), code=1)[2]
        self.assertIn("order 1 has no yes recorded today; a result can only follow an approval", err)    # v1 words


class SwingProtect(SwingBase):
    """protect: one stop-limit sell from the ledger plan stop, through psx-risk and a yes (item 5, D32)."""

    def setUp(self):
        super().setUp()
        self.set_cfg(paper=False)

    def protect(self, *extra, **kw):
        more = {"code": kw["code"]} if "code" in kw else {}
        return self.tk("protect", "--symbol", "DEMO", *(extra + ("--orders", self.orders, "--portfolio", self.pf,
                                                                 "--config", self.cfg, "--state", self.state, "--now",
                                                                 kw.get("now", "2026-10-30 09:35"))), **more)

    def test_protect_writes_the_stop_limit_order_of_the_contract(self):
        self.put(self.pf, contract("portfolio-swing.json"))
        out = self.protect(now="2026-10-29 09:36", code=0)[1]
        self.assertEqual(self.doc(self.orders), contract("orders-protect.json"))    # 95.28 from 96.25, key for key
        self.assertEqual(list(self.doc(self.orders)["orders"][0]), ["symbol", "side", "qty", "type", "trigger_price",
                                                                    "limit_price", "time_in_force", "board", "why"])
        self.assertIn("Protect DEMO: SELL 1,436 DEMO, stop-limit, trigger 96.25, limit 95.28, good for today, REG "
                      "board.\n", out)
        self.assertIn("The limit is 1% under it (swing.stop_limit_gap_pct in psx/config.json).", out)
        self.assertIn("Wrote it to %s (it replaced 1 earlier proposal).\n" % self.orders, out)
        self.assertIn('  2. ticket.py show, then the user\'s yes for this one order: "yes 96.25".', out)
        self.assertNotIn(APP_143.replace("143", "1436"), out)            # the line to type comes after the yes

    def test_the_ticket_shows_the_notes_and_asks_for_yes_with_the_trigger(self):
        self.put(self.pf, holding(143))
        self.protect(code=0)
        self.risk_step(now="2026-10-30 09:35")
        out = self.tk(*self.args("show", now="2026-10-30 09:35"), code=0)[1]
        for line in ("ORDER 1 of 1, REAL MONEY: you place it yourself in your broker app", "  " + SELL_143,
                     "  note: PSX orders last one day. If your broker app ends this order at the close, put it in again "
                     "each day after the opening match, while you hold DEMO (ticket.py protect --symbol DEMO). "
                     "psx-alerts reminds you at the first check. Check its validity in your app.",
                     "  note: No order guards the opening price. A gap at the open can go past your stop.",
                     "  note: A stop-limit sells only at 95.28 or better. If the price opens or falls under 95.28, it "
                     "may not fill: then sell by hand (ticket.py flatten).",
                     "  note: A market halt cancels every open order at PSX, and may cancel this one too.",
                     "  note: While this order rests, a second sell of the same shares is refused by psx-risk. Cancel "
                     "it in your app first, and record it: ticket.py result --fingerprint %s --status cancelled." % FP_143):
            self.assertIn(line + "\n", out)
        self.assertTrue(out.split("\n\nOne order at a time")[0].endswith(
            '  Reply "yes 96.25" to approve this one order, or "no" to skip it.'))
        self.assertNotIn(APP_143, out)
        for reply in ("yes, put the stop", "yes stop 96.25", "yes 96.20", "yes at market", "yes at any price", "yes buy"):
            out = self.tk(*self.args("approve", "--index", "1", "--reply", reply, now="2026-10-30 09:35"), code=1)[1]
            self.assertIn("Not recorded", out, reply)
            self.assertNotIn(APP_143, out)
        self.assertFalse(os.path.exists(self.state))
        out = self.tk(*self.args("approve", "--index", "1", "--reply", "yes 96.25", now="2026-10-30 09:35"), code=0)[1]
        self.assertIn("Now place it yourself in your broker app:\n  " + APP_143 + "\n", out)
        self.assertIn("If you cancel it later in your app, record it: ticket.py result --fingerprint %s --date "
                      "2026-10-30 --status cancelled." % FP_143, out)          # the day: one fingerprint, many days
        order = self.doc(self.orders)["orders"][0]
        for reply in ("yes 143", "yes 95.28", "yes sell", "Yes."):
            self.assertTrue(ticket.parse_yes(reply, order, ["DEMO"], (), SWING_COSTS)[0], reply)

    def test_a_placed_protect_fills_nothing_and_cancelled_frees_it(self):
        self.put(self.pf, holding(143))
        before = self.raw(self.pf)
        self.protect(code=0)
        self.risk_step(now="2026-10-30 09:35")
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes 96.25", now="2026-10-30 09:35"), code=0)
        err = self.tk(*self.args("result", "--index", "1", "--status", "placed", "--filled-price", "96.20",
                                 "--portfolio", self.pf, now="2026-10-30 09:36"), code=1)[2]
        self.assertIn("a stop-limit order fills at your broker later: when it fills, record it with ticket.py fill", err)
        out = self.tk(*self.args("result", "--index", "1", "--status", "placed", "--broker-id", "FS-2001",
                                 "--portfolio", self.pf, now="2026-10-30 09:36"), code=0)[1]
        self.assertIn("When it fills, record it: ticket.py fill --symbol DEMO --side sell --qty Q --price P", out)
        self.assertIn("record it: ticket.py result --fingerprint %s --date 2026-10-30 --status cancelled." % FP_143, out)
        self.assertEqual(self.raw(self.pf), before)                       # a protect order fills nothing
        self.assertEqual(self.doc(self.state), contract("broker-protect-open.json"))
        out = self.tk(*self.args("result", "--index", "1", "--status", "cancelled", now="2026-10-30 11:05"), code=0)[1]
        self.assertEqual(out, "Recorded order 1 as cancelled: you cancelled it in your broker app.\n")
        self.assertEqual(self.doc(self.state), contract("broker-protect-cancelled.json"))
        self.assertEqual(self.raw(self.pf), before)
        self.risk_step(now="2026-10-30 11:10")                           # a newer check: READY again, a new yes
        self.assertIn("READY: psx-risk PASS", self.tk(*self.args("show", now="2026-10-30 11:10"), code=0)[1])

    @unittest.skipUnless(PSX_RISK, "psx-risk/check.py is not the PSX version yet")
    def test_cancelled_frees_the_open_sell_count(self):
        self.copy_state("broker-protect-open.json")
        self.set_orders(dict(contract("orders-protect.json")["orders"][0], qty=143))
        self.assertEqual(len(check.placed_today(self.doc(self.state), "2026-10-30")[0]), 1)    # it rests at the broker
        self.tk(*self.args("result", "--index", "1", "--status", "cancelled", now="2026-10-30 11:05"), code=0)
        self.assertEqual(self.doc(self.state), contract("broker-protect-cancelled.json"))
        self.assertEqual(check.placed_today(self.doc(self.state), "2026-10-30"), ([], []))

    def test_paper_protect_writes_no_order_and_a_paper_stop_limit_is_never_ready(self):
        self.set_cfg(paper=True)
        self.put(self.pf, holding(143))
        before = self.raw(self.orders)
        code, out, err = self.protect(code=0)
        self.assertEqual((out, err), (PAPER_LINE + "\n", ""))            # only the paper line
        self.assertEqual(self.raw(self.orders), before)
        self.set_orders(dict(contract("orders-protect.json")["orders"][0], qty=143))
        self.risk_step(now="2026-10-30 09:35")
        out = self.tk(*self.args("show", now="2026-10-30 09:35"), code=1)[1]
        self.assertIn("NOT READY: " + PAPER_LINE, out)

    def test_protect_has_no_stop_flag_and_needs_a_plan_stop(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            ticket.main(["protect", "--symbol", "DEMO", "--stop", "97.00"])     # ticket.py stop moves a stop
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("nothing to protect: no DEMO shares in %s" % self.pf, self.protect(code=1)[2])
        bare = holding(143)
        del bare["positions"][0]["plan"]
        self.put(self.pf, bare)
        self.assertIn("no swing plan stop for DEMO", self.protect(code=1)[2])
        self.put(self.pf, holding(143))
        self.assertIn("--qty 144 is more than the 143 DEMO shares", self.protect("--qty", "144", code=1)[2])
        self.put(self.pf, holding(1436))
        self.protect("--qty", "143", code=0)
        o = self.doc(self.orders)["orders"][0]
        self.assertEqual((o["qty"], ticket.fingerprint(o), ticket.app_line(o)), (143, FP_143, APP_143))
        self.set_cfg(swing={"stop_limit_gap_pct": 2.0})
        self.protect(code=0)
        self.assertEqual(self.doc(self.orders)["orders"][0]["limit_price"], 94.32)    # 96.25 x 0.98, rounded down
        self.set_cfg(swing={"stop_limit_gap_pct": "1%"})
        self.assertIn('"stop_limit_gap_pct" in the "swing" block', self.protect(code=1)[2])

    def test_market_note_for_a_stop_limit_in_the_pre_open(self):
        o = dict(contract("orders-protect.json")["orders"][0], qty=143)
        thu = datetime.datetime(2026, 10, 29, 9, 20)
        self.assertEqual(ticket.market_note(o, thu, {}), "PSX is in the pre-open. Put this stop-limit in after the "
                         "opening match: in the pre-open orders cannot be changed, and a stop works only on trades.")
        self.assertEqual(ticket.market_note(dict(o, type="limit"), thu, {}), "PSX is in the pre-open. A limit order "
                         "waits for the opening match, and you cannot change or cancel it until then.")   # as in v1
        self.assertEqual(ticket.market_note(FLATTEN, thu, {}), "PSX is in the pre-open, which takes limit orders only. "
                         "A market order must wait for the open.")
        self.assertEqual(ticket.market_note(o, datetime.datetime(2026, 10, 29, 10, 0), {}), "")
        self.put(self.pf, holding(143))
        out = self.protect(now="2026-10-29 09:20", code=0)[1]
        self.assertIn("Note: PSX is in the pre-open. Put this stop-limit in after the opening match", out)


class SwingFlatten(SwingBase):
    """flatten in swing: no flat-by words, --limit P, and the cancel line for an open protect sell (item 8)."""

    def flatten(self, *extra, **kw):
        more = {"code": kw["code"]} if "code" in kw else {}
        return self.tk("flatten", "--symbol", "DEMO", *(extra + (
            "--orders", self.orders, "--portfolio", self.pf, "--plan", self.plan, "--config", self.cfg,
            "--state", self.state, "--now", kw.get("now", "2026-10-30 11:00"))), **more)

    def test_flatten_limit_makes_a_limit_sell_with_no_flat_by_words(self):
        self.put(self.pf, holding(143))
        out = self.flatten("--limit", "97.00", now="2026-10-30 09:05", code=0)[1]
        self.assertEqual(self.doc(self.orders)["orders"], [{
            "symbol": "DEMO", "side": "sell", "qty": 143, "type": "limit", "limit_price": 97.0, "time_in_force": "day",
            "board": "REG", "why": "close the DEMO swing position"}])
        self.assertIn("Flatten DEMO: SELL 143 DEMO, limit 97.00, good for today, REG board.\n", out)
        for gone in ("flat by", "day trade", "Karachi time)"):
            self.assertNotIn(gone, out)
        self.assertIn("Note: PSX is in the pre-open. A limit order waits for the opening match", out)   # Fri 9:05 AM
        self.assertIn("  3. Paper: ticket.py result --index 1 --status placed records a fill at the limit 97.00.", out)
        out = self.flatten(now="2026-10-30 09:05", code=0)[1]            # a market sell in the pre-open
        self.assertEqual(self.doc(self.orders)["orders"][0]["type"], "market")
        self.assertIn("For a sell in the pre-open, make a limit sell: ticket.py flatten --symbol DEMO --limit P.", out)
        for bad in ("0", "97.005", "-1", "ninety"):
            self.assertIn("--limit wants a price above 0 on the Rs 0.01 tick, like 97.00",
                          self.flatten("--limit", bad, code=1)[2], bad)

    def test_flatten_prints_the_cancel_line_while_a_protect_sell_is_open(self):
        self.set_cfg(paper=False)
        self.put(self.pf, holding(143))
        self.copy_state("broker-protect-open.json")
        out = self.flatten(code=0)[1]
        self.assertIn("Next:\n  1. If you put a stop-loss order in your app, cancel it first, so it cannot sell these "
                      "shares again.\n     Then record it: ticket.py result --fingerprint %s --status cancelled\n"
                      "  2. psx-risk: check.py." % FP_143, out)
        self.copy_state("broker-protect-cancelled.json")
        out = self.flatten(code=0)[1]
        self.assertNotIn("--fingerprint", out)
        self.assertIn("  3. If you put a stop-loss order in your app, cancel it first", out)      # the v1 step
        self.copy_state("broker-protect-open.json")
        self.assertNotIn("--fingerprint", self.flatten(now="2026-11-02 11:00", code=0)[1])       # Friday's order
        with open(self.state, "w") as fh:
            fh.write("{not json")
        self.assertNotIn("--fingerprint", self.flatten(code=0)[1])       # a bad log never stops an exit ticket


class SwingStop(SwingBase):
    """stop: the one command that changes a held position's stop, up only or scaled (item 12, D26, D56)."""

    def stop(self, *extra, **kw):
        """ticket.py stop for DEMO. data=: the data folder of the closing files (none by default);
        psx-alerts' record is self.alerts (no file by default)."""
        more = {"code": kw["code"]} if "code" in kw else {}
        return self.tk("stop", "--symbol", "DEMO", *(extra + ("--portfolio", self.pf, "--config", self.cfg, "--state",
                                                              self.state, "--data", kw.get("data", self.none),
                                                              "--alerts", self.alerts,
                                                              "--now", kw.get("now", "2026-11-12 09:40"))),
                       **more)

    def setUp(self):
        super().setUp()
        self.none = os.path.join(self.d, "no-data")
        self.alerts = os.path.join(self.d, "state", "alerts.json")

    def test_stop_set_moves_the_stop_up_only(self):
        self.set_cfg(paper=False)
        self.put(self.pf, holding(143))
        before = self.raw(self.pf)
        self.assertEqual(self.stop("--set", "95.00", code=1)[2], "ticket.py: move a stop up only: 95.00 is under the "
                         "stop 96.25. To match a bonus or split, use --scale.\n")      # SPEC v2 5.2, word for word
        self.assertIn("move a stop up only: 96.25 is the stop now", self.stop("--set", "96.25", code=1)[2])
        self.assertIn("--set wants a price above 0, like 97.00", self.stop("--set", "-1", code=1)[2])
        self.assertEqual(self.raw(self.pf), before)
        self.assertFalse(os.path.exists(self.state))
        out = self.stop("--set", "97.009", code=0)[1]                    # rounded down to the tick
        self.assertEqual(out, "DEMO stop 96.25 to 97.00 (the target stays 105.64). Your stop-limit in the broker app "
                              "is not changed. Make a new one: ticket.py protect --symbol DEMO.\n")
        plan = self.doc(self.pf)["positions"][0]["plan"]
        self.assertEqual((plan["stop"], plan["stop_at_buy"], plan["target"], plan["scaled"]), (97.0, 96.25, 105.64, []))
        self.assertEqual(self.doc(self.state)["orders"], [{       # psx-alerts reads this record (F37): keep its shape
            "at": "2026-11-12T09:40:00+05:00", "date": "2026-11-12", "status": "stop", "symbol": "DEMO", "from": 96.25,
            "to": 97.0, "scale": None, "why": None, "plain": "DEMO stop 96.25 to 97.00"}])
        self.tk("protect", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf, "--config", self.cfg,
                "--now", "2026-11-12 09:41", code=0)
        o = self.doc(self.orders)["orders"][0]
        self.assertEqual((o["trigger_price"], o["limit_price"]), (97.0, 96.03))    # protect takes the new stop

    def test_stop_scale_for_a_bonus_and_keep(self):
        self.set_cfg(paper=False)
        self.put(self.pf, holding(143))
        bonus = self.market("bonus")                                     # XB on Thu 11-12: LDCP 89.70, factor 0.8333
        out = self.stop("--scale", "0.8333", "--why", "XB 20%", data=bonus, code=0)[1]
        self.assertEqual(out, "DEMO stop 96.25 to 80.20, target 105.64 to 88.03 (scale 0.8333: XB 20% on Thu "
                              "2026-11-12). Your stop-limit in the broker app is not changed. Make a new one: "
                              "ticket.py protect --symbol DEMO.\n")             # SPEC v2 5.2, word for word
        plan = self.doc(self.pf)["positions"][0]["plan"]
        self.assertEqual((plan["stop"], plan["target"], plan["stop_at_buy"]), (80.2, 88.03, 96.25))
        self.assertEqual(plan["scaled"], [{"date": "2026-11-12", "factor": 0.8333, "why": "XB 20%"}])   # SPEC v2 2.5
        rec = self.doc(self.state)["orders"][-1]
        self.assertEqual(list(rec), ["at", "date", "status", "symbol", "from", "to", "scale", "why", "plain"])
        self.assertEqual((rec["from"], rec["to"], rec["scale"], rec["why"]), (96.25, 80.2, 0.8333, "XB 20%"))
        out = self.stop("--scale", "1", now="2026-11-13 09:40", code=0)[1]
        self.assertEqual(out, "DEMO stop 80.20 and target 88.03 stay as they are (scale 1: keep on Fri 2026-11-13). "
                              "Your stop-limit in the broker app is not changed.\n")
        plan = self.doc(self.pf)["positions"][0]["plan"]
        self.assertEqual((plan["stop"], plan["target"]), (80.2, 88.03))
        self.assertEqual(plan["scaled"][-1], {"date": "2026-11-13", "factor": 1.0, "why": "keep"})
        before = (self.raw(self.pf), self.raw(self.state))
        for bad in ("1.2", "0", "-0.5", "half"):
            self.assertIn("--scale wants a factor over 0 and at most 1, like 0.8333", self.stop("--scale", bad,
                                                                                                code=1)[2], bad)
        err = self.stop("--scale", "0.8333", data=bonus, now="2026-11-13 09:45", code=1)[2]    # one ex-date, one scale
        self.assertEqual(err, "ticket.py: the XB ex-date of DEMO on Thu 2026-11-12 has a scale already. A second scale "
                              "would lower the stop again, so nothing was changed. To move the stop up: --set.\n")
        self.assertEqual((self.raw(self.pf), self.raw(self.state)), before)

    def test_scale_needs_a_bonus_rights_or_split_ex_date(self):
        # F15: a plain gap down is not an ex-date. --set refuses a lower stop; --scale must refuse it too
        self.set_cfg(paper=False)
        self.put(self.pf, holding(1436))                                 # max loss Rs 4,997.81 at the stop 96.25
        gap = self.market("gap")                                         # Mon 11-02 opens at 95.40: no flag, no split
        before = self.raw(self.pf)
        for now, why in (("2026-11-03 10:00", None), ("2026-10-30 17:20", "give it room")):
            extra = ("--why", why) if why else ()
            err = self.stop(*(("--scale", "0.95") + extra), data=gap, now=now, code=1)[2]
            self.assertIn("--scale 0.95 lowers the stop, and that needs a bonus, rights or split ex-date of DEMO. None "
                          "is in the closing files in %s or in %s from Thu 2026-10-29 (the buy day) to " % (gap, self.alerts),
                          err)
            self.assertIn("so nothing was changed. A fall in the price is not an ex-date: the stop moves up only "
                          "(--set).", err)
        self.assertIn("move a stop up only", self.stop("--set", "91.43", data=gap, now="2026-11-03 10:00", code=1)[2])
        self.assertEqual(self.raw(self.pf), before)
        self.assertFalse(os.path.exists(self.state))
        self.stop("--scale", "1", data=gap, now="2026-11-03 10:00", code=0)   # keep: always allowed
        self.assertEqual(self.doc(self.pf)["positions"][0]["plan"]["stop"], 96.25)

    def test_scale_is_never_under_the_ex_date_factor(self):
        self.put(self.pf, holding(143))
        bonus = self.market("bonus")
        before = self.raw(self.pf)
        err = self.stop("--scale", "0.5", data=bonus, now="2026-11-12 17:00", code=1)[2]
        self.assertEqual(err, "ticket.py: --scale 0.5 is under 0.8333, the factor of the XB ex-date of DEMO (Thu "
                              "2026-11-12). It would lower the stop more than the ex-date did, so nothing was changed. "
                              "Use --scale 0.8333, or --scale 1 to keep the stop.\n")
        self.assertEqual(self.raw(self.pf), before)
        err = self.stop("--scale", "0.8333", data=bonus, now="2026-11-11 17:00", code=1)[2]   # the day before the XB
        self.assertIn("None is in the closing files", err)
        self.stop("--scale", "0.9", data=bonus, now="2026-11-12 17:00", code=0)   # less than the factor: allowed
        self.assertEqual(self.doc(self.pf)["positions"][0]["plan"]["stop"], 86.62)

    def test_scale_from_psx_alerts_record_of_the_company_page(self):
        # the morning of the ex-date: psx-alerts saw XB on the company page; the closing file is not out yet
        self.put(self.pf, holding(143))
        ev = {"flags": ["XB"], "from": 107.64, "to": 89.7, "factor": 0.8333, "kind": "XB", "date": "2026-11-12",
              "seen": "company page"}
        self.put(self.alerts, {"swing_ex": {"DEMO-2026-10-29": {"2026-11-12": dict(ev, kind="XD", flags=["XD"])}}})
        self.assertIn("None is in the closing files", self.stop("--scale", "0.8333", code=1)[2])   # XD: cash, no scale
        self.put(self.alerts, {"swing_ex": {"DEMO-2026-11-02": {"2026-11-12": ev}}})              # another buy day
        self.assertIn("None is in the closing files", self.stop("--scale", "0.8333", code=1)[2])
        self.put(self.alerts, {"swing_ex": {"DEMO-2026-10-29": {"2026-11-12": ev}}})
        self.stop("--scale", "0.8333", "--why", "XB 20%", now="2026-11-12 09:40", code=0)
        plan = self.doc(self.pf)["positions"][0]["plan"]
        self.assertEqual((plan["stop"], plan["target"]), (80.2, 88.03))

    def test_stop_on_paper_and_with_no_plan(self):
        self.put(self.pf, holding(143))                                  # the example config: paper
        self.assertEqual(self.stop("--set", "97.00", code=0)[1], "DEMO stop 96.25 to 97.00 (the target stays 105.64). "
                         "Paper: no stop order rests anywhere, so psx-alerts watches the new stop.\n")
        bare = holding(143)
        del bare["positions"][0]["plan"]
        self.put(self.pf, bare)
        self.assertIn("no swing plan for DEMO in %s" % self.pf, self.stop("--set", "98.00", code=1)[2])
        for argv in (["stop", "--symbol", "DEMO", "--set", "97", "--scale", "1"], ["stop", "--symbol", "DEMO"]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                ticket.main(argv)
            self.assertEqual(cm.exception.code, 2)


class SwingBonusAndCash(SwingBase):
    """fill --bonus (item 13, D56) and cash --set (item 14, D57)."""

    def test_fill_bonus_adds_a_lot_at_cost_0(self):
        self.put(self.pf, holding(143))
        cash = self.doc(self.pf)["cash"]
        out = self.tk(*self.fill_args("--bonus", "28", now="2026-11-16 10:00"), code=0)[1]
        self.assertEqual(out, "Recorded 28 bonus shares of DEMO at cost 0: 171 shares now, the same total cost. Record "
                              "the bonus shares your broker app shows. The company may keep back 10% of them until a "
                              "tax is paid (section 236Z).\n")                 # SPEC v2 5.2, word for word
        pf = self.doc(self.pf)
        pos = pf["positions"][0]
        self.assertEqual((pf["cash"], pos["shares"], pos["plan"]), (cash, 171, holding(143)["positions"][0]["plan"]))
        self.assertEqual(pos["lots"][-1], {"id": "DEMO-2", "date": "2026-11-16", "shares": 28, "cost": 0.0, "costs": 0.0,
                                           "bonus": True})
        self.assertEqual(sum(l["cost"] for l in pos["lots"]), 14211.34)  # the same total cost, now over 171 shares
        self.assertEqual(self.doc(self.state)["orders"], [{
            "at": "2026-11-16T10:00:00+05:00", "date": "2026-11-16", "status": "bonus", "symbol": "DEMO", "qty": 28,
            "plain": "28 bonus shares of DEMO at cost 0"}])             # no fingerprint, not "filled"
        self.assertNotIn("real_holdings", pf)
        if PSX_RISK:
            self.assertEqual(check.placed_today(self.doc(self.state), "2026-11-16"), ([], []))   # not a trade

    def test_bonus_keeps_the_guard_and_needs_shares(self):
        self.put(self.pf, contract("portfolio-real.json"))              # paper folder, real shares
        before = self.raw(self.pf)
        self.assertIn(REAL_WORDS % self.pf, self.tk(*self.fill_args("--bonus", "100"), code=1)[2])
        self.assertEqual(self.raw(self.pf), before)
        self.assertFalse(os.path.exists(self.state))
        self.tk(*self.fill_args("--bonus", "100", "--real"), code=0)
        self.assertEqual(self.doc(self.pf)["positions"][0]["shares"], 600)
        for extra, why in ((("--bonus", "10", "--side", "buy"), "--bonus records bonus shares only"),
                           (("--bonus", "2.5"), "whole number of shares"), (("--bonus", "0"), "whole number of shares")):
            self.assertIn(why, self.tk(*self.fill_args(*extra), code=1)[2], extra)
        os.remove(self.pf)
        self.assertIn("no DEMO shares in %s: bonus shares come only on shares you hold" % self.pf,
                      self.tk(*self.fill_args("--bonus", "28"), code=1)[2])

    def test_cash_set_changes_only_the_cash(self):
        self.put(self.pf, holding(143))
        before = self.doc(self.pf)
        out = self.tk("cash", "--set", "50,000", "--portfolio", self.pf, "--state", self.state, "--now",
                      "2026-11-14 10:00", code=0)[1]
        self.assertEqual(out, "Cash in %s set from Rs 485,763.21 to Rs 50,000.00. Nothing else changed.\n" % self.pf)
        self.assertEqual(self.doc(self.pf), dict(before, cash=50000.0))
        self.assertEqual(self.doc(self.state)["orders"], [{
            "at": "2026-11-14T10:00:00+05:00", "date": "2026-11-14", "status": "cash", "from": 485763.21, "to": 50000.0,
            "plain": "cash Rs 485,763.21 to Rs 50,000.00"}])
        for bad in ("-5", "lots", ""):
            self.assertIn("--set wants rupees, 0 or more, like 46000", self.tk("cash", "--set", bad, "--portfolio",
                                                                              self.pf, "--state", self.state,
                                                                              code=1)[2], bad)
        os.remove(self.pf)
        out = self.tk("cash", "--set", "50000", "--portfolio", self.pf, "--state", self.state, code=0)[1]
        self.assertIn("(a new file, with no positions)", out)
        self.assertEqual(self.doc(self.pf), {"cash": 50000.0, "positions": []})


class RealHoldings(SwingBase):
    """The real holdings guard (D39, both styles): a paper fill never writes into a file of real shares."""
    STYLE = "day"

    def test_a_paper_result_into_real_holdings_writes_nothing(self):
        shutil.copy(ORDERS_PATH, self.orders)
        shutil.copy(os.path.join(V2, "portfolio-real.json"), self.pf)
        self.risk_step(NOW)
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now=NOW), code=0)
        before = (self.raw(self.pf), self.raw(self.state))
        err = self.tk(*self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf, now=NOW),
                      code=1)[2]
        self.assertEqual(err, "ticket.py: " + REAL_WORDS % self.pf + "\n")
        self.assertEqual((self.raw(self.pf), self.raw(self.state)), before)       # both byte for byte
        self.tk(*self.args("result", "--index", "1", "--status", "skipped", "--portfolio", self.pf, now=NOW), code=0)
        self.assertEqual(self.raw(self.pf), before[0])                   # no fill, no change

    def test_fill_into_real_holdings_needs_real(self):
        shutil.copy(os.path.join(V2, "portfolio-real.json"), self.pf)
        before = self.raw(self.pf)
        sell = ("--side", "sell", "--qty", "100", "--price", "98.40", "--date", TODAY)
        self.assertIn(REAL_WORDS % self.pf, self.tk(*self.fill_args(*sell), code=1)[2])
        self.assertEqual(self.raw(self.pf), before)
        self.assertFalse(os.path.exists(self.state))
        out = self.tk(*self.fill_args(*(sell + ("--real",))), code=0)[1]
        pf = self.doc(self.pf)
        self.assertEqual(list(pf)[:2], ["real_holdings", "as_of"])      # the flag stays on top; as_of is kept
        self.assertEqual((pf["real_holdings"], pf["as_of"], pf["positions"][0]["shares"]), (True, "2026-10-28", 400))
        self.assertNotIn("now says", out)                                # it was set already

    def test_paper_never_sets_the_flag_and_a_real_fill_does(self):
        buy = ("--side", "buy", "--qty", "100", "--price", "100.65", "--date", TODAY)
        self.tk(*self.fill_args(*buy), code=0)
        self.assertNotIn("real_holdings", self.doc(self.pf))             # a paper fill into a paper file
        out = self.tk(*self.fill_args(*(buy + ("--real",))), code=0)[1]
        self.assertEqual(list(self.doc(self.pf))[0], "real_holdings")
        self.assertIn('%s now says "real_holdings": true: it holds real shares, so a paper fill can never change it.'
                      % self.pf, out)
        os.remove(self.pf)
        self.set_cfg(paper=False)
        self.assertIn("now says", self.tk(*self.fill_args(*buy), code=0)[1])   # a fill with paper off sets it
        self.assertIs(self.doc(self.pf)["real_holdings"], True)

    def test_a_live_result_sets_the_flag(self):
        self.set_cfg(paper=False)
        shutil.copy(ORDERS_PATH, self.orders)
        self.risk_step(NOW)
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now=NOW), code=0)
        out = self.tk(*self.args("result", "--index", "1", "--status", "placed", "--filled-price", "100.60",
                                 "--portfolio", self.pf, now=NOW), code=0)[1]
        self.assertIn("Fill: BUY 3,428 DEMO at 100.60", out)
        pf = self.doc(self.pf)
        self.assertEqual(list(pf), ["real_holdings", "cash", "positions"])
        self.assertNotIn("plan", pf["positions"][0])                     # a day buy

    def test_an_invest_buy_shows_no_stop_lines_and_no_flat_by(self):
        self.set_cfg(require_stop=False)
        shutil.copy(os.path.join(V2, "orders-invest.json"), self.orders)
        now = "2026-10-29 10:20"
        self.risk_step(now)
        out = self.tk(*self.args("show", "--index", "2", now=now), code=0)[1]
        self.assertIn("  BUY 100 DEMO, limit 98.40, good for today, REG board\n", out)
        for gone in ("plan stop", "plan target", "max loss", "Be flat by", "protect"):
            self.assertNotIn(gone, out)
        out = self.tk(*self.args("approve", "--index", "2", "--reply", "yes", now=now), code=0)[1]
        for gone in ("max loss", "expired"):
            self.assertNotIn(gone, out)
        out = self.tk(*self.args("result", "--index", "2", "--status", "placed", "--portfolio", self.pf, now=now),
                      code=0)[1]
        self.assertIn("Paper fill: BUY 100 DEMO at 98.40", out)
        for gone in ("Be flat by", "psx-alerts watches", "stop-loss", "protect"):
            self.assertNotIn(gone, out)
        self.assertNotIn("plan", self.doc(self.pf)["positions"][0])


MAYBE_WORDS = ('%s has no "real_holdings" key, but it may hold real shares: %s. A paper fill would change it, so '
               'nothing was written. Ask the user which it is. Real shares: put "real_holdings": true at the top, and '
               'practise in a separate paper folder. A paper file: put "real_holdings": false at the top.')
HAND = {"ticker": "UBL", "shares": 20, "lots": [{"id": "UBL-1", "date": "2026-10-20", "shares": 20, "cost": 7700.0,
                                                 "costs": 15.0}]}        # made up: shares bought by hand, no plan


class UnmarkedRealLedger(SwingBase):
    """F04: a ledger from before v2, or one made by hand, has no "real_holdings" key even when its shares are real.
    With paper on, a fill into a file with no flag that shows a sign of real shares writes nothing (D39)."""
    STYLE = "day"

    def paper_result(self, now=NOW, *extra):
        """The DEMO day order on paper: a PASS, the yes, then result --status placed. -> (code, out, err)."""
        shutil.copy(ORDERS_PATH, self.orders)
        self.risk_step(now)
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now=now), code=0)
        self.before = (self.raw(self.pf) if os.path.exists(self.pf) else None, self.raw(self.state))
        return self.tk(*self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf, *extra,
                                  now=now))

    def test_a_ledger_with_a_note_by_hand_gets_no_paper_fill(self):
        self.put(self.pf, {"cash": 40000.0, "positions": [HAND], "note": "Made up: a trading account, by hand."})
        code, out, err = self.paper_result()
        self.assertEqual((code, err), (1, "ticket.py: " + MAYBE_WORDS % (self.pf, 'it has "note", which the tools '
                                                                         'never write') + "\n"))
        self.assertEqual((self.raw(self.pf), self.raw(self.state)), self.before)      # both byte for byte
        for extra in (("--side", "sell", "--qty", "20", "--price", "390"), ("--bonus", "2")):
            argv = ["fill", "--symbol", "UBL"] + list(extra) + ["--portfolio", self.pf, "--config", self.cfg,
                                                                 "--state", self.state, "--now", "2026-10-29 11:00"]
            self.assertIn('has no "real_holdings" key, but it may hold real shares', self.tk(*argv, code=1)[2], extra)
        self.assertEqual((self.raw(self.pf), self.raw(self.state)), self.before)
        self.tk(*self.fill_args("--side", "buy", "--qty", "10", "--price", "100.65", "--real"), code=0)   # a real fill
        self.assertIs(self.doc(self.pf)["real_holdings"], True)

    def test_real_holdings_false_says_it_is_a_paper_file(self):
        self.put(self.pf, {"real_holdings": False, "cash": 500000, "positions": [HAND], "note": "Made up: paper."})
        code, out, err = self.paper_result()
        self.assertEqual(code, 0, err)
        self.assertIn("Paper fill: BUY 3,428 DEMO at 100.65", out)
        pf = self.doc(self.pf)
        self.assertIs(pf["real_holdings"], False)                        # paper never sets the flag
        self.assertEqual(sorted(p["ticker"] for p in pf["positions"]), ["DEMO", "UBL"])

    def test_a_yes_made_with_paper_off_marks_the_folder(self):
        self.put(self.pf, {"cash": 40000.0, "positions": [HAND]})         # only the keys the tools write
        self.copy_state("broker-placed-unfilled.json")                   # a yes of 10-29 made with paper off
        before = (self.raw(self.pf), self.raw(self.state))
        err = self.tk(*self.fill_args("--side", "buy", "--qty", "10", "--price", "100.65"), code=1)[2]
        self.assertEqual(err, "ticket.py: " + MAYBE_WORDS % (self.pf, "%s has a yes made with paper off (DEMO buy on "
                                                             "2026-10-29)" % self.state) + "\n")
        self.assertEqual((self.raw(self.pf), self.raw(self.state)), before)

    def test_a_ledger_the_paper_tools_made_still_takes_paper_fills(self):
        code, out, err = self.paper_result()                             # a new ledger, from the budget
        self.assertEqual(code, 0, err)
        self.tk(*self.fill_args("--side", "sell", "--qty", "3428", "--price", "101.58"), code=0)
        self.tk(*self.fill_args("--side", "buy", "--qty", "100", "--price", "101.75"), code=0)
        self.assertNotIn("real_holdings", self.doc(self.pf))
        os.remove(self.pf)
        self.put(self.pf, {"cash": 0, "positions": [], "note": "Made up: empty."})   # nothing in it to protect
        self.tk(*self.fill_args("--side", "buy", "--qty", "1", "--price", "100"), code=0)


class UnmarkedRealSwing(SwingBase):
    """F04 in swing style: a held share with no swing plan was not made by a swing fill, so it may be real."""

    def test_a_share_with_no_swing_plan_gets_no_paper_fill(self):
        self.put(self.pf, {"cash": 40000.0, "positions": [HAND]})
        self.risk_step()
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=0)
        before = (self.raw(self.pf), self.raw(self.state))
        err = self.tk(*self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf,
                                 now="2026-10-29 09:40"), code=1)[2]
        self.assertEqual(err, "ticket.py: " + MAYBE_WORDS % (self.pf, "UBL in it has no swing plan, so no swing fill "
                                                             "made it") + "\n")
        self.assertEqual((self.raw(self.pf), self.raw(self.state)), before)
        # the worst case: a paper flatten of these UBL shares (they may be real) would take them out of the ledger
        self.tk("flatten", "--symbol", "UBL", "--limit", "390.00", "--orders", self.orders, "--portfolio", self.pf,
                "--plan", self.plan, "--config", self.cfg, "--state", self.state, "--now", "2026-10-29 10:30", code=0)
        self.risk_step(now="2026-10-29 10:30")
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-29 10:30"), code=0)
        self.assertIn("UBL in it has no swing plan", self.tk(*self.args(
            "result", "--index", "1", "--status", "placed", "--portfolio", self.pf, now="2026-10-29 10:31"), code=1)[2])
        self.assertEqual([p["ticker"] for p in self.doc(self.pf)["positions"]], ["UBL"])


class ProtectDays(SwingBase):
    """F17: a protect put in again on Mon 11-02 has the same fingerprint as Friday's. result closes the order of the
    day the user names, never today's by mistake. F26: after a part fill, the rest can be recorded cancelled."""

    def setUp(self):
        super().setUp()
        self.set_cfg(paper=False)
        self.put(self.pf, holding(143))
        self.set_orders(dict(contract("orders-protect.json")["orders"][0], qty=143))

    def two_days(self, fri_closed=False):
        """broker-protect-open.json (Fri 10-30, placed 9:36 AM), [Friday's close at 2 PM], then Monday's protect."""
        st = contract("broker-protect-open.json")
        fri = st["orders"][:2]
        if fri_closed:
            st["orders"].append(dict(fri[1], at="2026-10-30T14:00:00+05:00", status="cancelled", of="2026-10-30"))
        st["orders"] += [dict(fri[0], at="2026-11-02T09:40:00+05:00", date="2026-11-02"),
                         dict(fri[1], at="2026-11-02T09:41:00+05:00", date="2026-11-02", broker_order_id="FS-2002")]
        self.put(self.state, st)

    def result(self, *argv, **kw):
        return self.tk("result", *(argv + ("--state", self.state, "--now", kw.get("now", "2026-11-02 10:45"))),
                       **({"code": kw["code"]} if "code" in kw else {}))

    def flatten_out(self, now="2026-11-02 10:50"):
        return self.tk("flatten", "--symbol", "DEMO", "--orders", os.path.join(self.d, "flat.json"), "--portfolio",
                       self.pf, "--plan", self.plan, "--config", self.cfg, "--state", self.state, "--now", now,
                       code=0)[1]

    def test_the_older_day_is_closed_by_its_date_and_today_still_rests(self):
        self.two_days()
        out = self.result("--fingerprint", FP_143, "--date", "2026-10-30", "--status", "cancelled", code=0)[1]
        self.assertIn("Recorded the DEMO sell of Fri 2026-10-30 as cancelled: you cancelled it in your broker app.", out)
        self.assertNotIn("also has an open order", out)
        last = self.doc(self.state)["orders"][-1]
        self.assertEqual((last["date"], last["of"], list(last)), ("2026-11-02", "2026-10-30", CLOSE_KEYS))
        self.assertIn("Then record it: ticket.py result --fingerprint %s --status cancelled" % FP_143, self.flatten_out())
        self.risk_step(now="2026-11-02 10:46")                           # today's protect is still placed: not READY
        self.assertIn("DONE: placed at 2026-11-02T09:41:00+05:00", self.tk(*self.args("show", now="2026-11-02 10:46"),
                                                                            code=1)[1])
        if PSX_RISK:
            self.assertEqual(len(check.placed_today(self.doc(self.state), "2026-11-02")[0]), 1)   # it still counts
        err = self.result("--fingerprint", FP_143, "--date", "2026-10-28", "--status", "cancelled", code=1)[2]
        self.assertIn("no order with the fingerprint %s on 2026-10-28" % FP_143, err)

    def test_a_bare_fingerprint_closes_the_newest_open_order_and_names_the_older_one(self):
        self.two_days()
        out = self.result("--fingerprint", FP_143, "--status", "cancelled", code=0)[1]
        self.assertIn("Recorded the DEMO sell of Mon 2026-11-02 as cancelled", out)
        self.assertIn("This fingerprint also has an open order of Fri 2026-10-30 in %s. To record that one, add --date "
                      "2026-10-30." % self.state, out)
        self.assertEqual(self.doc(self.state)["orders"][-1]["of"], "2026-11-02")
        self.assertNotIn("--fingerprint", self.flatten_out())            # today's sell is closed now

    def test_symbol_and_date_never_close_another_days_order(self):
        self.two_days(fri_closed=True)
        before = self.raw(self.state)
        err = self.result("--symbol", "DEMO", "--date", "2026-10-30", "--status", "cancelled", code=1)[2]
        self.assertIn('the newest record of this order is "cancelled" on 2026-10-30, not approved or placed', err)
        self.assertEqual(self.raw(self.state), before)
        out = self.result("--symbol", "DEMO", "--date", "2026-11-02", "--status", "cancelled", code=0)[1]
        self.assertIn("Recorded the DEMO sell of Mon 2026-11-02 as cancelled", out)

    def test_a_filled_buy_of_the_same_day_does_not_hide_the_sell(self):
        st = contract("broker-placed-unfilled.json")                     # the buy, placed Thu 10-29
        st["orders"].append({"at": "2026-10-29T09:40:00+05:00", "date": "2026-10-29", "status": "filled", "symbol": "DEMO",
                             "side": "buy", "qty": 1436, "price": 99.38, "costs": 255.59, "plain": "BUY 1,436 DEMO at 99.38"})
        prot = contract("broker-protect-open.json")["orders"]
        st["orders"] += [dict(r, at=r["at"].replace("2026-10-30", "2026-10-29"), date="2026-10-29") for r in prot]
        self.put(self.state, st)
        out = self.result("--symbol", "DEMO", "--date", "2026-10-29", "--status", "cancelled", now="2026-10-29 11:00",
                          code=0)[1]
        self.assertIn("Recorded the DEMO sell of Thu 2026-10-29 as cancelled", out)
        self.assertEqual(self.doc(self.state)["orders"][-1]["fingerprint"], FP_143)

    def part_fill(self, qty=100):
        self.copy_state("broker-protect-open.json")                      # 143 DEMO, stop-limit, placed Fri 9:36 AM
        st = self.doc(self.state)
        st["orders"].append({"at": "2026-10-30T14:00:00+05:00", "date": "2026-10-30", "status": "filled", "symbol": "DEMO",
                             "side": "sell", "qty": qty, "price": 96.2, "costs": 5.0, "plain": "SELL %d DEMO at 96.20" % qty})
        self.put(self.state, st)
        self.put(self.pf, holding(143 - qty))

    def test_a_part_filled_stop_limit_can_be_recorded_cancelled(self):
        self.part_fill()
        if PSX_RISK:                       # F25: psx-risk keeps the other 43 open until the cancel record
            self.assertEqual([(s["filled"], s["left"]) for s in check.open_orders(self.doc(self.state), "2026-10-30")[1]],
                             [(100.0, 43.0)])
        out = self.flatten_out(now="2026-10-30 14:05")
        self.assertIn("  1. If you put a stop-loss order in your app, cancel it first, so it cannot sell these shares "
                      "again.\n     Part of it filled (100 of 143 shares): cancel the rest in your app.\n     Then record "
                      "it: ticket.py result --fingerprint %s --status cancelled\n" % FP_143, out)
        before = self.raw(self.state)
        err = self.result("--fingerprint", FP_143, "--status", "skipped", now="2026-10-30 14:06", code=1)[2]
        self.assertIn("100 of the 143 shares of this order filled on or after 2026-10-30, so it was placed, not skipped. "
                      "Record the rest as cancelled or expired", err)
        self.assertEqual(self.raw(self.state), before)
        out = self.result("--fingerprint", FP_143, "--status", "cancelled", now="2026-10-30 14:06", code=0)[1]
        self.assertIn("Recorded the DEMO sell of Fri 2026-10-30 as cancelled: you cancelled the rest in your broker app.\n"
                      "  SELL 143 DEMO, stop-limit, trigger 96.25, limit 95.28, good for today, REG board (fingerprint "
                      "%s)\n100 of its 143 shares had filled: that fill stays in the log. The other 43 are closed now.\n"
                      % FP_143, out)
        self.assertNotIn("--fingerprint", self.flatten_out(now="2026-10-30 14:07"))
        if PSX_RISK:                       # the record closes the rest for psx-risk too: a flatten of the 43 is free
            self.assertEqual(check.open_orders(self.doc(self.state), "2026-10-30")[1], [])

    def test_index_cancel_after_a_part_fill_and_a_full_fill_is_not_open(self):
        self.part_fill()
        out = self.tk(*self.args("result", "--index", "1", "--status", "cancelled", now="2026-10-30 14:06"), code=0)[1]
        self.assertEqual(out, "Recorded order 1 as cancelled: you cancelled the rest in your broker app.\n100 of its 143 "
                              "shares had filled: that fill stays in the log. The other 43 are closed now.\n")
        self.part_fill(qty=143)                                          # all of it filled: nothing rests
        err = self.tk(*self.args("result", "--index", "1", "--status", "cancelled", now="2026-10-30 14:06"), code=1)[2]
        self.assertIn("sell DEMO has a fill recorded today, so order 1 is not open: nothing to record", err)


BLOCKED_WORDS = ("FFC is on your blocked list in psx/config.json (long-term shares). Is this the right folder? A "
                 "long-term fill goes in the long-term folder. Nothing was written. If FFC is a trade of this folder, "
                 "add --blocked-ok.")


class BlockedShares(SwingBase):
    """F27: the "blocked" list holds the long-term shares. fill refuses a buy of one (it may belong in the long-term
    folder); a sell is never refused; flatten and protect say so before the yes."""
    STYLE = "day"

    def setUp(self):
        super().setUp()
        self.set_cfg(blocked=["FFC", "OGDC"], paper=False)

    def ffc(self, *extra, **kw):
        return self.tk("fill", "--symbol", "FFC", *(extra + ("--portfolio", self.pf, "--config", self.cfg, "--state",
                                                             self.state, "--now", "2026-11-12 11:00")), **kw)

    def test_a_buy_of_a_blocked_share_needs_blocked_ok(self):
        buy = ("--side", "buy", "--qty", "10", "--price", "300")
        self.assertEqual(self.ffc(*buy, code=1)[2], "ticket.py: " + BLOCKED_WORDS + "\n")
        self.set_cfg(style="swing")                                      # the same words, not "No swing plan"
        self.assertEqual(self.ffc(*buy, code=1)[2], "ticket.py: " + BLOCKED_WORDS + "\n")
        self.assertFalse(os.path.exists(self.pf) or os.path.exists(self.state))
        self.set_cfg(style="day")
        self.assertIn("Recorded buy 10 FFC at 300.00", self.ffc(*(buy + ("--blocked-ok",)), code=0)[1])
        self.assertIn("Recorded sell 4 FFC", self.ffc("--side", "sell", "--qty", "4", "--price", "301", code=0)[1])

    def test_flatten_and_protect_of_a_blocked_share_say_check_the_folder(self):
        note = ("FFC is on your blocked list in psx/config.json: these may be long-term shares. Check that this is the "
                "right folder before you say yes.")
        self.set_cfg(paper=True)               # paper: the day flatten's v1 step 4 ("Sell in your broker app") is not new
        self.put(self.pf, {"real_holdings": True, "cash": 1000, "positions": [{"ticker": "FFC", "shares": 6}]})
        out = self.tk("flatten", "--symbol", "FFC", "--orders", self.orders, "--portfolio", self.pf, "--plan", self.plan,
                      "--config", self.cfg, "--state", self.state, "--now", "2026-11-12 11:05", code=0)[1]
        self.assertIn(note + "\n", out)
        self.assertEqual(self.doc(self.orders)["orders"][0]["why"], "flat by 15:20: close FFC, a share on your blocked list")
        self.assertNotIn("day trade", out)
        self.set_cfg(blocked=[])                                         # not blocked: the v1 words
        self.tk("flatten", "--symbol", "FFC", "--orders", self.orders, "--portfolio", self.pf, "--plan", self.plan,
                "--config", self.cfg, "--now", "2026-11-12 11:05", code=0)
        self.assertEqual(self.doc(self.orders)["orders"][0]["why"], "flat by 15:20: close the FFC day trade")
        self.set_cfg(style="swing", blocked=["DEMO"], paper=False)       # a live protect writes the ticket
        self.put(self.pf, holding(143))
        out = self.tk("protect", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf, "--config",
                      self.cfg, "--state", self.state, "--now", "2026-10-30 09:35", code=0)[1]
        self.assertIn(note.replace("FFC", "DEMO") + "\n", out)


class AfterAHalt(SwingBase):
    """F28: a halt cancels every open order. The new protect has the same fingerprint as the placed one, so show and
    protect name the way out: record the old one cancelled first."""

    def test_show_and_protect_name_the_way_out(self):
        self.set_cfg(paper=False)
        self.put(self.pf, holding(143))
        prot = ("protect", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf, "--config", self.cfg,
                "--state", self.state)
        self.tk(*(prot + ("--now", "2026-10-30 09:35")), code=0)
        self.risk_step(now="2026-10-30 09:35")
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes 96.25", now="2026-10-30 09:35"), code=0)
        self.tk(*self.args("result", "--index", "1", "--status", "placed", now="2026-10-30 09:36"), code=0)
        out = self.tk(*(prot + ("--now", "2026-10-30 11:40")), code=0)[1]          # after the halt
        self.assertIn("Next:\n  1. A stop-limit sell of these shares is open today in %s. If it still rests in your app, "
                      "you need\n     no new one. If PSX or you cancelled it (a market halt cancels every open order), "
                      "record that first:\n     ticket.py result --fingerprint %s --status cancelled\n  2. psx-risk: "
                      "check.py." % (self.state, FP_143), out)
        self.risk_step(now="2026-10-30 11:40")
        label = ("DONE: placed at 2026-10-30T09:36:00+05:00. If PSX or you cancelled it (a market halt cancels every "
                 "open order), record it: ticket.py result --fingerprint %s --status cancelled. Then check.py and a new "
                 "yes" % FP_143)
        self.assertIn("  " + label + "\n", self.tk(*self.args("show", now="2026-10-30 11:41"), code=1)[1])
        err = self.tk(*self.args("approve", "--index", "1", "--reply", "yes 96.25", now="2026-10-30 11:41"), code=1)[2]
        self.assertEqual(err, "ticket.py: order 1 is not ready: %s\n" % label)
        self.tk("result", "--fingerprint", FP_143, "--status", "cancelled", "--note", "PSX halt", "--state", self.state,
                "--now", "2026-10-30 11:42", code=0)
        self.risk_step(now="2026-10-30 11:43")
        self.assertIn("READY: psx-risk PASS", self.tk(*self.args("show", now="2026-10-30 11:43"), code=0)[1])


class RealMoneyClock(SwingBase):
    """F64: --now pins the clock. With paper off in swing style, approve of a buy refuses a clock that is not the
    computer's own, and a sell gets a note. Day style stays as in v1: no note (R1-1, the day golden diff). Paper gets
    nothing."""
    REFUSED = ("ticket.py: --now says Thu 2026-10-29 9:20 AM, but the clock of this computer says Thu 2026-10-29 7:41 PM "
               "(Karachi time). A real-money yes for a buy counts only on the real clock, so nothing was recorded. Run "
               "approve again without --now and --today.\n")

    def test_a_real_money_yes_for_a_swing_buy_needs_the_real_clock(self):
        self.set_cfg(paper=False)
        self.risk_step()
        with self.clock("2026-10-29 19:41"):                             # PSX closed hours ago
            self.assertEqual(self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=1)[2], self.REFUSED)
        self.assertFalse(os.path.exists(self.state))
        with self.clock("2026-10-29 09:21"):                             # one minute off: fine
            out = self.tk(*self.args("approve", "--index", "1", "--reply", "yes"), code=0)[1]
        self.assertIn("Now place it yourself in your broker app:", out)

    def test_today_must_be_the_real_karachi_day(self):
        self.set_cfg(paper=False)
        self.risk_step()
        doc = self.doc(self.risk)
        doc["date"] = "2026-10-30"                                       # a check made for the wrong day
        self.put(self.risk, doc)
        with self.clock(PRE_OPEN):
            err = self.tk(*self.args("approve", "--index", "1", "--reply", "yes") + ["--today", "2026-10-30"], code=1)[2]
        self.assertEqual(err, "ticket.py: --today says 2026-10-30, but today in Karachi is 2026-10-29. A real-money yes "
                              "for a buy counts only on the real clock, so nothing was recorded. Run approve again "
                              "without --now and --today.\n")

    def test_a_swing_exit_gets_a_note_and_day_style_and_paper_get_nothing(self):
        self.set_cfg(paper=False)
        self.put(self.pf, holding(143))
        self.tk("protect", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf, "--config", self.cfg,
                "--state", self.state, "--now", "2026-10-30 09:35", code=0)   # a swing sell: the stop-limit
        self.risk_step(now="2026-10-30 09:35")
        with self.clock("2026-10-30 19:41"):
            code, out, err = self.tk(*self.args("approve", "--index", "1", "--reply", "yes 96.25",
                                                now="2026-10-30 09:35"), code=0)
        self.assertIn("Now place it yourself in your broker app:\n  " + APP_143 + "\n", out)   # the sell is recorded
        self.assertEqual(err, "Note: --now says Fri 2026-10-30 9:35 AM, but the clock of this computer says Fri "
                              "2026-10-30 7:41 PM (Karachi time). A real-money yes should be on the real clock: check in "
                              "your app that PSX is open and that the order still fits now.\n")
        os.remove(self.state)
        self.set_cfg(style="day")                                        # day style: v1 exactly, no note (R1-1)
        shutil.copy(ORDERS_PATH, self.orders)
        self.risk_step(NOW)
        with self.clock("2026-11-11 21:38"):                             # as in the live day golden run
            code, out, err = self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now=NOW), code=0)
        self.assertIn("  " + DEMO_APP + "\n", out)                       # v1: the day yes is recorded
        self.assertEqual(err, "")
        self.assertEqual(self.doc(self.state)["orders"][-1]["status"], "approved")
        os.remove(self.state)
        self.set_cfg(paper=True)
        with self.clock("2026-10-29 19:41"):
            self.assertEqual(self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now=NOW), code=0)[2], "")


REST_LINE = ("The other %s shares may still fill today: record them with ticket.py fill --symbol DEMO --side buy --qty Q "
             "--price P. A PSX order ends at the close.")


class PartBuyFill(SwingBase):
    """F60: a part fill of a buy leaves the rest open until the close (D57). After result --filled-qty Q, or a fill of
    fewer shares, swing and psx-invest buys say how many may still fill. expired or cancelled closes the rest. Day
    style says nothing new (as in v1)."""

    def waiting(self, day=TODAY):
        """psx-risk's view of the buys with no fill yet on `day`: [(shares filled, shares left)]."""
        return [(w["filled"], w["left"]) for w in check.open_orders(self.doc(self.state), day)[0]]

    def test_a_part_filled_swing_buy_says_the_rest_may_still_fill(self):
        out = self.buy(at="2026-10-29 09:40", extra=("--filled-qty", "1000", "--filled-price", "99.30"))
        self.assertIn("Paper fill: BUY 1,000 DEMO at 99.30", out)
        self.assertIn("\n" + REST_LINE % "436" + "\nNext: ticket.py protect --symbol DEMO", out)
        if PSX_RISK:
            self.assertEqual(self.waiting(), [(1000.0, 436.0)])        # psx-risk counts the same rest
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "400", "--price", "99.38"), code=0)[1]
        self.assertTrue(out.endswith("\n" + REST_LINE % "36" + "\n"), out)
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "36", "--price", "99.38", now="2026-10-29 10:05"),
                      code=0)[1]
        self.assertNotIn("may still fill", out)                          # all of it filled
        if PSX_RISK:
            self.assertEqual(self.waiting(), [])
        self.assertEqual(self.doc(self.pf)["positions"][0]["shares"], 1436)

    def test_expired_closes_the_rest_of_a_part_filled_buy(self):
        self.buy(at="2026-10-29 09:40", extra=("--filled-qty", "1000", "--filled-price", "99.30"))
        out = self.tk(*self.args("result", "--index", "1", "--status", "expired", now="2026-10-29 15:35"), code=0)[1]
        self.assertEqual(out, "Recorded order 1 as expired: the rest of the order ended at the close with no fill.\n"
                              "1,000 of its 1,436 shares had filled: that fill stays in the log. The other 436 are closed "
                              "now.\n")
        if PSX_RISK:
            self.assertEqual(self.waiting(), [])
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "10", "--price", "99.38", now="2026-10-29 15:36"),
                      code=0)[1]
        self.assertNotIn("may still fill", out)                          # the order is closed: no rest to name

    def test_a_part_fill_recorded_on_a_later_day(self):
        self.buy(at="2026-10-29 09:40", extra=("--filled-qty", "1000", "--filled-price", "99.30"))
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "10", "--price", "99.38", "--date", TODAY,
                                      now="2026-10-30 10:00"), code=0)[1]
        self.assertNotIn("may still fill", out)                          # yesterday's order ended at the close
        out = self.tk("result", "--fingerprint", FP_BUY, "--status", "expired", "--state", self.state, "--now",
                      "2026-10-30 10:01", code=0)[1]
        self.assertEqual(out, "Recorded the DEMO buy of Thu 2026-10-29 as expired: the rest of the order ended at the "
                              "close with no fill.\n  BUY 1,436 DEMO, limit 99.38, good for today, REG board (fingerprint "
                              "%s)\n1,010 of its 1,436 shares had filled: that fill stays in the log. The other 426 are "
                              "closed now.\nNothing else was written. psx-risk counts this order as closed now.\n" % FP_BUY)

    def test_day_style_says_nothing_new_after_a_part_fill(self):
        self.set_cfg(style="day")
        shutil.copy(ORDERS_PATH, self.orders)
        self.risk_step(NOW)
        self.tk(*self.args("approve", "--index", "1", "--reply", "yes", now=NOW), code=0)
        out = self.tk(*self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "1000", "--portfolio",
                                 self.pf, now=NOW), code=0)[1]
        self.assertEqual(out.splitlines()[1:], [
            "Paper fill: BUY 1,000 DEMO at 100.65, costs about Rs 180.26. Cash now Rs 399,169.74 in %s." % self.pf,
            "psx-alerts watches the plan (alerts.py add --from-plan): stop 99.55, target 101.75.",
            "Be flat by 15:20 Karachi time: ticket.py flatten --symbol DEMO."])          # v1, line for line
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "400", "--price", "100.65", now="2026-10-29 10:20"),
                      code=0)[1]
        self.assertEqual(len(out.splitlines()), 1, out)                  # v1: only the "Recorded" line

    def test_a_part_filled_invest_buy_says_it_too(self):
        self.set_cfg(style="day", require_stop=False)                    # the long-term folder: psx-invest orders
        shutil.copy(os.path.join(V2, "orders-invest.json"), self.orders)
        now = "2026-10-29 10:20"
        self.risk_step(now)
        self.tk(*self.args("approve", "--index", "2", "--reply", "yes", now=now), code=0)
        out = self.tk(*self.args("result", "--index", "2", "--status", "placed", "--filled-qty", "60", "--portfolio",
                                 self.pf, now=now), code=0)[1]
        self.assertIn("Paper fill: BUY 60 DEMO at 98.40", out)
        self.assertIn("\n" + REST_LINE % "40" + "\n", out)
        out = self.tk(*self.fill_args("--side", "buy", "--qty", "30", "--price", "98.40", now="2026-10-29 10:30"),
                      code=0)[1]
        self.assertTrue(out.endswith("\n" + REST_LINE % "10" + "\n"), out)


class NoOrdersInARealPaperFolder(SwingBase):
    """F45: in a paper folder of real shares (the long-term folder) psx-invest writes no psx/orders.json. result then
    names ticket.py fill --real, not psx-budget. Anywhere else the v1 words stay."""
    STYLE = "day"

    def test_result_names_fill_real(self):
        os.remove(self.orders)
        self.put(self.pf, contract("portfolio-real.json"))
        argv = self.args("result", "--index", "1", "--status", "placed", "--portfolio", self.pf, now=NOW)
        err = self.tk(*argv, code=1)[2]
        self.assertEqual(err, 'ticket.py: no orders file at %s, so there is no order to record. %s holds real shares '
                              '("real_holdings": true) and paper is on: then psx-invest writes no orders file. To record '
                              'a real fill from your broker app: ticket.py fill --symbol X --side buy --qty Q --price P '
                              '--real (or --side sell)\n' % (self.orders, self.pf))
        v1 = "ticket.py: no orders file (run psx-budget, or ticket.py flatten, first) at %s\n" % self.orders
        self.set_cfg(paper=False)                                        # paper off: the v1 words
        self.assertEqual(self.tk(*argv, code=1)[2], v1)
        self.set_cfg(paper=True)
        self.put(self.pf, {"cash": 500000, "positions": []})             # a paper ledger: the v1 words
        self.assertEqual(self.tk(*argv, code=1)[2], v1)
        os.remove(self.pf)                                               # no ledger yet: the v1 words
        self.assertEqual(self.tk(*argv, code=1)[2], v1)
        self.assertFalse(os.path.exists(self.state))


class SwingDocs(SwingBase):
    """The "Swing style" part of psx-broker/SKILL.md: its example blocks are the real output."""

    def page(self):
        with open(os.path.join(ROOT, "psx-broker", "SKILL.md"), encoding="utf-8") as fh:
            return fh.read()

    def test_the_swing_section_is_the_real_output(self):
        part = self.page().split("\n## Swing style\n", 1)[1].split("\n## ", 1)[0]
        blocks = re.findall(r"```\n(.*?)\n```", part, re.S)
        self.set_cfg(paper=False)
        self.put(self.pf, dict([("real_holdings", True)] + list(holding(143).items())))   # after a real buy fill
        outs = [self.tk("protect", "--symbol", "DEMO", "--orders", self.orders, "--portfolio", self.pf, "--config",
                        self.cfg, "--now", "2026-10-30 09:35", code=0)[1]]
        self.risk_step(now="2026-10-30 09:35")
        outs.append(self.tk(*self.args("show", now="2026-10-30 09:35"), code=0)[1])
        outs.append(self.tk(*self.args("approve", "--index", "1", "--reply", "yes 96.25", now="2026-10-30 09:35"),
                            code=0)[1])
        outs.append(self.tk("stop", "--symbol", "DEMO", "--scale", "0.8333", "--why", "XB 20%", "--portfolio", self.pf,
                            "--config", self.cfg, "--state", self.state, "--data", self.market("bonus"),
                            "--now", "2026-11-12 09:40", code=0)[1])           # the XB ex-date is in the closing file
        outs.append(self.tk(*self.fill_args("--bonus", "28", now="2026-11-16 10:00"), code=0)[1])
        outs = [o.replace(self.pf, "psx/portfolio.json").replace(self.orders, "psx/orders.json").strip() for o in outs]
        self.assertEqual(blocks, outs)

    def test_the_skill_page_says_the_swing_rules(self):
        doc = " ".join(self.page().split())
        for must in ('"style": "swing"', "Try swing trades on paper first", "Swing trading loses money for many people "
                     "too", "stop_at_buy", "--status expired", "--status cancelled", "--fingerprint FP",
                     "--symbol DEMO --date 2026-10-29", "Trigger Price", "Treat it as a day order",
                     "No order guards the opening price", "it may not fill", "A market halt cancels every open order",
                     "--limit 97.00", "stop --symbol DEMO --set 97.00", "--scale 1", "--bonus 28", "FBR rule 13N",
                     "section 236Z", "fill --real", "real_holdings", "Paper mode never sets it", "cash --set 46000",
                     "Never count the same money twice", "T+1", "the price PSX really traded", "Record it expired",
                     "trigger_price",
                     # F06: paper in a folder of its own; F07, F02: a share held before the switch; F60; F45; R1-1
                     "Try swing trades on paper first, in a folder of their own (never turn paper on in a folder that "
                     "holds real shares)", "Does a trading folder hold real shares or real cash? Then do not turn paper "
                     "on there", "Still keep paper off in a trading folder like that", "### A share you held before "
                     "the switch", "No command gives it a plan", "Only your own rules in `psx/alerts.json` watch it",
                     "may still fill today", "nothing for `result`", "In day style it says nothing, as before"):
            self.assertIn(must, doc, "SKILL.md should say: " + must)
        self.assertEqual(doc.count("## Safety"), 1)


if __name__ == "__main__":
    unittest.main()
