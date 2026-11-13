"""The whole DEMO trade, tool by tool, through the real command lines (the README example).

Each step runs a skill's script the way a user would, in a temp project folder whose psx/data
is a copy of the made-up sample market. Each tool reads what the one before it wrote, so a
change to a shared file (entry.json, plan.json, orders.json, ...) that breaks a reader fails here.
Offline, as every test is.

v2 (SPEC v2, Task 10) adds five chains, on made-up data only:
- Swing: the DEMO swing trade from the scan to the sell, on the sample market plus the made-up
  path "up" (fixtures/psx-v2/paths/up: its high meets the target on Wed 2026-11-04). And a live
  stop-limit that blocks a second sell of the same shares until it is recorded cancelled.
- LongTerm: psx-invest's orders through psx-risk and psx-broker, on the made-up market of
  fixtures/psx-invest (its own make.py writes it into a temp folder once).
- Autorun: every step that auto-run runs is a real script, and its flags are in that tool's
  --help. The signal lines it counts, and a notify line only on a change.
- Shapes: the files these chains write have the keys and types of fixtures/psx-v2/contract/.
And the doc checks: every command in README.md matches its tool's --help (Readme), and the
README's own words (ReadmeWords): paper and real shares (the flag a live fill sets, the signs of
real shares in a file with no flag), a share held before the switch to swing, each ticket.py result
naming its order, the long-term folder's guard and its TRIMs, the auto-run pause, plain words and
short sentences. Autorun checks auto-run's times and its entry-watch words against the runner (and
psx-entry/SKILL.md). Where those words say what a tool does, the test runs the tool on made-up data,
so the words stay true.
Every line the v2 chains print passes the shared word test (tests/_words.py).
"""

import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from _load import FIX, ROOT, load
from _words import problems

MARKET = os.path.join(FIX, "psx-market")


def run(cwd, script, *args):
    p = subprocess.run([sys.executable, os.path.join(ROOT, script)] + list(args), cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                       timeout=300)
    return p.returncode, p.stdout


def read_json(cwd, name):
    with open(os.path.join(cwd, "psx", name), encoding="utf-8") as fh:
        return json.load(fh)


class Chain(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="psx-chain-")
        os.makedirs(os.path.join(cls.tmp, "psx"))
        shutil.copytree(MARKET, os.path.join(cls.tmp, "psx", "data"))
        shutil.copy(os.path.join(ROOT, "psx-budget", "config.example.json"),
                    os.path.join(cls.tmp, "psx", "config.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def step(self, script, *args, **kw):
        code, out = run(self.tmp, script, *args)
        want = kw.get("code", 0)
        self.assertEqual(code, want, "%s %s exited %s:\n%s" % (script, " ".join(args), code, out))
        for text in kw.get("has", ()):
            self.assertIn(text, out, "%s %s:\n%s" % (script, " ".join(args), out))
        return out

    def test_the_demo_trade_end_to_end(self):
        t = self.tmp
        # 1. the scanner, before the open
        self.step("psx-scanner/scan.py", "universe", "--offline", "--now", "2026-10-29 09:00",
                  has=["Kept 31 ordinary shares."])
        with open(os.path.join(t, "psx", "universe.txt"), encoding="utf-8") as fh:
            universe = fh.read().splitlines()
        self.assertEqual(len(universe), 31)
        self.assertIn("DEMO|Demo Industries Limited|0818", universe)
        self.step("psx-scanner/scan.py", "daily", "--offline", "--now", "2026-10-29 09:00",
                  has=["28 passed the floors", "20-day closing-high breakout (level 96.56)"])
        hits = dict((h["symbol"], h) for h in read_json(t, "watchlist.json")["hits"])
        self.assertEqual(sorted(hits), ["DEMO", "LUCK", "PPL", "PSO", "SEARL", "TRG", "UBL"])
        self.assertEqual(hits["DEMO"]["level"], 96.56)
        self.assertEqual(hits["DEMO"]["name"], "Demo Industries Limited")
        # the opening-range scan at 10:16 (the market tape samples every 2 minutes, so DEMO's 10:10 bar
        # is seen to end only with the 10:16 snapshot); looked at, not written, so the daily watchlist stays
        self.step("psx-scanner/scan.py", "orb", "--offline", "--now", "2026-10-29 10:16", "--no-write",
                  has=["10:15 AM  DEMO     100.65  range 98.90 to 100.20   +0.45% over"])

        # 2. the news on the watchlist (names come from universe.txt)
        self.step("psx-news/news.py", "--offline", "--tickers", "DEMO", "--now", "2026-10-29 17:00",
                  "--dry-run", has=["Demo Industries wins Rs1.2bn packaging order", "Material Information"])

        # 3. the entry timer writes entry.json; 4. the exit planner reads it and writes plan.json
        self.step("psx-entry/entry.py", "check", "DEMO", "--offline", "--now", "2026-10-29 10:15", "--write",
                  has=["TRIGGERED 10:15 AM at 100.65"])
        entry = read_json(t, "entry.json")
        self.assertEqual((entry["status"], entry["bar_index"], entry["price"]), ("triggered", 8, 100.65))
        self.step("psx-exit/exit.py", "plan", "--stop", "or-mid", "--target", "1R", "--write", "--offline",
                  "--now", "2026-10-29 10:16", has=["Wrote psx/plan.json."])
        plan = read_json(t, "plan.json")
        self.assertEqual((plan["entry"], plan["stop"], plan["target"]), (100.65, 99.55, 101.75))
        self.assertEqual((plan["limits"]["lower"], plan["limits"]["upper"]), (88.56, 108.24))
        self.assertEqual(plan["flat_by"], "15:20")

        # 5. the time machine replays the plan to its flat-by time, costs counted in R and the verdict
        self.step("psx-timemachine/timemachine.py", "run", "--offline", "--now", "2026-10-29 10:16",
                  has=["Mode: bars.", "target first   31.3 %", "seed 19", "the plan's flat-by time",
                       "Costs: about 0.33R a trade", "Leans toward the stop"])

        # 6. the budget sizes it and writes orders.json; 7. the risk check passes it
        self.step("psx-budget/size.py", "--now", "2026-10-29 10:15", "--write",
                  has=["3,428 shares of DEMO = Rs 345,028.20 at 100.65", "Rs 4,999.94, costs included"])
        order = read_json(t, "orders.json")["orders"][0]
        self.assertEqual((order["qty"], order["type"], order["limit_price"]), (3428, "limit", 100.65))
        self.assertEqual((order["stop_loss"], order["take_profit"]), (99.55, 101.75))
        self.step("psx-risk/check.py", "--offline", "--now", "2026-10-29 10:16",
                  has=["PASS  #1 buy DEMO 3428 shares, entry", "Cash after the passed orders: Rs 154,353.85"])

        # 8. the ticket, the yes, the paper fill; the line to type into the app comes only after the yes
        out = self.step("psx-broker/ticket.py", "show", "--now", "2026-10-29 10:16",
                        has=["READY: psx-risk PASS, waiting for your yes"])
        self.assertNotIn("In your broker app", out)
        self.step("psx-broker/ticket.py", "approve", "--index", "1", "--reply", "maybe",
                  "--now", "2026-10-29 10:16", code=1)
        self.step("psx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes, sell it",
                  "--now", "2026-10-29 10:16", code=1)
        self.step("psx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes", "--now", "2026-10-29 10:16",
                  has=["In your broker app: BUY DEMO, REG board, quantity 3428, LIMIT price 100.65"])
        self.step("psx-broker/ticket.py", "result", "--index", "1", "--status", "placed",
                  "--now", "2026-10-29 10:17", has=["Cash now Rs 154,353.85"])
        book = read_json(t, "portfolio.json")
        held = dict((p.get("ticker") or p.get("symbol"), p) for p in book["positions"])
        self.assertEqual(held["DEMO"]["shares"], 3428)

        # 9. the alerts from the plan: nothing at 10:53, the target at 11:41, flat by 15:20
        self.step("psx-alerts/alerts.py", "add", "--from-plan")
        self.step("psx-alerts/alerts.py", "check", "--offline", "--now", "2026-10-29 10:53",
                  code=1, has=["Nothing hit. 3 rule(s) watched today, DEMO 101.23"])
        self.step("psx-alerts/alerts.py", "check", "--offline", "--now", "2026-10-29 11:41",
                  has=["ALERT DEMO is 101.80 at 11:41 AM PKT: at or above your target 101.75"])
        self.step("psx-alerts/alerts.py", "check", "--offline", "--now", "2026-10-29 15:20",
                  has=["psx-broker flatten --symbol DEMO"])

        # 10. out by the close: the flatten sell is an exit, so it passes after 15:00
        self.step("psx-broker/ticket.py", "flatten", "--symbol", "DEMO", "--now", "2026-10-29 15:20",
                  has=["SELL 3,428 DEMO, market"])
        self.assertEqual(read_json(t, "orders.json")["orders"][0]["side"], "sell")
        self.step("psx-risk/check.py", "--offline", "--now", "2026-10-29 15:20",
                  has=["PASS  #1 sell DEMO 3428 shares, exit"])

        # 11. after the close: the results and the insider notices
        self.step("psx-analyst/facts.py", "DEMO", "--offline", "--now", "2026-10-29 16:00",
                  has=["Next board meeting: Tue 2026-11-03 at 11:00 AM PKT"])
        self.step("psx-insiders/insiders.py", "new", "--read", "--symbols", "DEMO,SYS", "--offline",
                  "--now", "2026-10-29 16:00",
                  has=["Mr. Salman Akhtar (Director) bought 25,000 shares at 97.11 on 2026-10-28.",
                       "inside the closed period DEMO announced"])


# ---------------------------------------------------------------- v2: the shared parts of the new chains

V2 = os.path.join(FIX, "psx-v2")
CONTRACT = os.path.join(V2, "contract")
INVEST = os.path.join(FIX, "psx-invest")
README = os.path.join(ROOT, "README.md")
EVENING = "2026-10-28 17:00"      # Wed, after the close: the scan, the plan and the replays for the next session
PREOPEN = "2026-10-29 09:20"      # Thu, the buy day, in the pre-open: the order, psx-risk, the ticket, the yes
FILLED = "2026-10-29 09:40"       # the paper fill is recorded
BUY_FP = "0d80ffbb0d898a06"       # the fingerprint of the DEMO swing buy (as in fixtures/psx-v2/contract)
LT_EVENING = "2026-10-30 17:00"   # Fri, the evening of the newest long-term closing file (a month-end)
LT_SESSION = "2026-11-02 10:30"   # Mon, in the session: psx-invest's orders are made then (SPEC v2 3.7)
SWING_PLAN = {"style": "swing", "date": "2026-10-29", "stop": 96.25, "stop_at_buy": 96.25, "target": 105.64,
              "exit_by": "2026-11-27", "exit_by_time": "16:20", "trend_sessions": 10, "setup": "breakout_20d",
              "max_loss": 4997.81, "scaled": [], "from": "psx/orders.json"}
_TEMPS = []                       # every temp folder this module makes (tearDownModule removes them)
_CACHE = {}                       # folders made once for the module: the DEMO buy, the long-term market


def tearDownModule():
    for path in _TEMPS:
        shutil.rmtree(path, ignore_errors=True)
    del _TEMPS[:]
    _CACHE.clear()
    _HELP.clear()


def new_temp(prefix):
    path = tempfile.mkdtemp(prefix=prefix)
    _TEMPS.append(path)
    return path


def read_doc(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_doc(path, doc):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def edit_config(root, **changes):
    """Change keys in <root>/psx/config.json (a temp copy only)."""
    path = os.path.join(root, "psx", "config.json")
    doc = read_doc(path)
    doc.update(changes)
    write_doc(path, doc)


def must(cwd, script, *args):
    """Run one tool for a folder the tests share; it must exit 0."""
    code, out = run(cwd, script, *args)
    if code != 0:
        raise AssertionError("%s %s exited %s:\n%s" % (script, " ".join(args), code, out))
    return out


def copy_project(src):
    """A copy of a project folder, in a new temp folder."""
    dst = os.path.join(new_temp("psx-chain-copy-"), "project")
    shutil.copytree(src, dst, symlinks=True)
    return dst


def swing_project(path="up"):
    """A new project folder for a swing trade: psx/data is the sample market (closing files to
    Thu 2026-10-29) plus the made-up path's closing files (Fri 2026-10-30 to Fri 2026-11-27), and
    psx/config.json is the example config with "style": "swing" (paper, the site off)."""
    root = new_temp("psx-chain-swing-")
    os.makedirs(os.path.join(root, "psx"))
    data = os.path.join(root, "psx", "data")
    shutil.copytree(MARKET, data)
    extra = os.path.join(V2, "paths", path, "mkt_summary")
    for name in sorted(os.listdir(extra)):
        shutil.copy(os.path.join(extra, name), os.path.join(data, "mkt_summary", name))
    cfg = read_doc(os.path.join(ROOT, "psx-budget", "config.example.json"))
    cfg["style"] = "swing"
    write_doc(os.path.join(root, "psx", "config.json"), cfg)
    return root


def demo_bought():
    """The DEMO swing buy, made once for the module, as two folders:
    "approved": after the yes, before any result; "bought": after the paper fill at 99.38."""
    if "bought" not in _CACHE:
        root = swing_project()
        must(root, "psx-scanner/scan.py", "daily", "--offline", "--now", EVENING)
        must(root, "psx-exit/exit.py", "plan", "--symbol", "DEMO", "--write", "--offline", "--now", EVENING)
        must(root, "psx-budget/size.py", "--now", PREOPEN, "--write")
        must(root, "psx-risk/check.py", "--offline", "--now", PREOPEN)
        must(root, "psx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes", "--now", PREOPEN)
        _CACHE["approved"] = copy_project(root)
        must(root, "psx-broker/ticket.py", "result", "--index", "1", "--status", "placed", "--now", FILLED)
        _CACHE["bought"] = root
    return _CACHE


def lt_market():
    """The made-up market of fixtures/psx-invest: 500 closing files to Fri 2026-10-30, written once
    for the module by its own make.py (so the 500 files never ship)."""
    if "lt-market" not in _CACHE:
        base = new_temp("psx-chain-lt-market-")
        out = os.path.join(base, "data")
        must(base, os.path.join("tests", "fixtures", "psx-invest", "make.py"), out)
        _CACHE["lt-market"] = out
    return _CACHE["lt-market"]


def lt_project(**top):
    """A long-term project folder: the fixture's config.json (the long-term values: no stop needed),
    invest.json, portfolio.json and metrics.json in psx/, and a copy of the made-up market in
    psx/data. top: keys put first in psx/portfolio.json (like "real_holdings": true)."""
    root = new_temp("psx-chain-lt-")
    os.makedirs(os.path.join(root, "psx"))
    for name in ("config.json", "invest.json", "portfolio.json", "metrics.json"):
        shutil.copy(os.path.join(INVEST, name), os.path.join(root, "psx", name))
    shutil.copytree(lt_market(), os.path.join(root, "psx", "data"))
    if top:
        path = os.path.join(root, "psx", "portfolio.json")
        doc = read_doc(path)
        write_doc(path, dict(list(top.items()) + [(k, v) for k, v in doc.items() if k not in top]))
    return root


def fingerprint_in(text):
    """The fingerprint a tool tells you to use: "--fingerprint FP"."""
    m = re.search(r"--fingerprint ([0-9a-f]{16})", text)
    return m.group(1) if m else None


class Steps(unittest.TestCase):
    """step(): one tool in a project folder: its exit code, the lines it must (and must not) print,
    and the shared word rules on every line it prints."""

    def step(self, cwd, script, *args, **kw):
        code, out = run(cwd, script, *args)
        cmd = "%s %s" % (script, " ".join(args))
        self.assertEqual(code, kw.get("code", 0), "%s exited %s:\n%s" % (cmd, code, out))
        for text in kw.get("has", ()):
            self.assertIn(text, out, "%s:\n%s" % (cmd, out))
        for text in kw.get("not_has", ()):
            self.assertNotIn(text, out, "%s:\n%s" % (cmd, out))
        bad = problems(out)
        self.assertEqual(bad, [], "%s: the word rules (tests/_words.py) are broken:\n%s" % (cmd, "\n".join(bad)))
        return out



class Swing(Steps):
    """The DEMO swing trade (SPEC v2 section 2.2, and the README's swing example): buy on Thu
    2026-10-29, hold, sell after the target rings. Paper, the site off, every step offline."""

    def test_the_demo_swing_trade_end_to_end(self):
        t = swing_project()
        # 1. Wed 2026-10-28, after the close: the scan for the next session
        self.step(t, "psx-scanner/scan.py", "daily", "--offline", "--now", EVENING,
                  has=["Closes through Wed 2026-10-28", "20-day closing-high breakout (level 96.56)"])
        hit = dict((h["symbol"], h) for h in read_json(t, "watchlist.json")["hits"])["DEMO"]
        self.assertEqual((hit["setups"], hit["level"], hit["close_date"]), (["breakout_20d"], 96.56, "2026-10-28"))

        # 2. the swing plan for the next session, from the closing files (no bars, no psx-entry)
        self.step(t, "psx-exit/exit.py", "plan", "--symbol", "DEMO", "--write", "--offline", "--now", EVENING,
                  has=["Swing plan: DEMO long, buy on Thu 2026-10-29, 20-day closing-high breakout (level 96.56)",
                       "Entry         99.38  limit buy: the last close 98.40 (Wed 2026-10-28) + 1%",
                       "Stop          96.25  2 x ATR(14) under the entry: daily ATR 1.5650 over the 14 sessions",
                       "Target        105.64  2R (two times the risk)",
                       "Exit by       Fri 2026-11-27 at 16:20 PKT: 20 sessions after the buy day (the time stop)",
                       "the lowest close of the 10 sessions before it (now 92.36)",
                       "Gap risk      a next-day open at the lower limit 89.44 loses about 10.28 a share, costs "
                       "included: 2.95 times the loss at the stop",
                       "Held overnight: a gap can open past the stop. Then a sell fills at the open, not at the stop.",
                       "Wrote psx/plan.json.", "Next: /psx-timemachine replays the next 20 sessions"])
        plan = read_json(t, "plan.json")
        self.assertEqual((plan["style"], plan["date"], plan["entry"], plan["stop"], plan["target"]),
                         ("swing", "2026-10-29", 99.38, 96.25, 105.64))
        self.assertEqual((plan["exit_by"], plan["exit_by_time"], plan["trend_level"], plan["flat_by"]),
                         ("2026-11-27", "16:20", 92.36, None))
        self.assertEqual((plan["setup"], plan["level"]), ("breakout_20d", 96.56))      # the scan's hit
        self.assertEqual((plan["limits"]["lower"], plan["limits"]["upper"]), (88.56, 108.24))
        self.assertEqual((plan["gap_low"], plan["gap_loss_per_share"], plan["gap_r"]), (89.44, 10.2782, 2.95))

        # 3. the time machine picks swing mode by itself: the next 20 sessions, gaps included
        self.step(t, "psx-timemachine/timemachine.py", "run", "--offline", "--now", EVENING,
                  has=["Mode: swing.",
                       "10,000 replays of the next 20 sessions, Thu 2026-10-29 to the exit-by day Fri 2026-11-27, "
                       "seed 19.", "A gap through the stop sells at the open, not at the stop.",
                       "target         34.5 %", "stop           34.0 %", "(a gap: 2.8 %)", "trend          19.8 %",
                       "time           11.7 %", "Average R: +0.584", "Leans toward the target"])

        # 4. the budget the same evening: a preview of the next session; nothing is written
        self.step(t, "psx-budget/size.py", "--now", EVENING,
                  has=["Size: 1,436 shares of DEMO = Rs 142,709.68 at 99.38.",
                       "Preview for Thu 2026-10-29: size it again on that day, before you buy."])
        self.assertFalse(os.path.exists(os.path.join(t, "psx", "orders.json")))

        # 5. Thu 2026-10-29, the pre-open: the order (a limit buy waits for the opening match), psx-risk
        self.step(t, "psx-budget/size.py", "--now", PREOPEN, "--write",
                  has=["Size: 1,436 shares of DEMO = Rs 142,709.68 at 99.38.",
                       "Max loss at the stop: Rs 4,997.81, costs included.",
                       "Loss if it opens at the lower limit the next day: about Rs 14,759.46, costs included (your gap "
                       "limit Rs 15,000). More days down can lose more.",
                       "PSX orders last one day: a limit buy that does not fill today ends at the close. Do not chase it.",
                       "Hold up to Fri 2026-11-27 (20 sessions). Out earlier at the stop, the target or a trend break."],
                  not_has=["Be flat by"])
        order = read_json(t, "orders.json")["orders"][0]
        self.assertEqual((order["side"], order["qty"], order["type"], order["limit_price"]), ("buy", 1436, "limit", 99.38))
        self.assertEqual((order["stop_loss"], order["take_profit"]), (96.25, 105.64))
        self.assertEqual([order["plan"][k] for k in ("style", "exit_by", "exit_by_time", "flat_by", "gap_loss")],
                         ["swing", "2026-11-27", "16:20", None, 14759.46])
        self.step(t, "psx-risk/check.py", "--offline", "--now", PREOPEN,
                  has=["PASS  #1 buy DEMO 1436 shares, entry",
                       "gap loss Rs 14,759.46 if it opens at the lower limit 89.44 (limit Rs 15,000)",
                       "open before this: 0 of 3 positions", "Cash after the passed orders: Rs 357,034.73",
                       "A gap can open past a stop: a loss can be bigger than its max loss."])

        # 6. the ticket, the yes, the paper fill: the ledger gets the position and its swing plan
        self.step(t, "psx-broker/ticket.py", "show", "--now", PREOPEN,
                  has=["READY: psx-risk PASS, waiting for your yes",
                       "plan stop 96.25: after the buy fills, ticket.py protect makes the stop-limit ticket"],
                  not_has=["In your broker app"])
        self.step(t, "psx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes", "--now", PREOPEN,
                  has=["In your broker app: BUY DEMO, REG board, quantity 1436, LIMIT price 99.38, valid for the day"])
        self.step(t, "psx-broker/ticket.py", "result", "--index", "1", "--status", "placed", "--now", FILLED,
                  has=["Paper fill: BUY 1,436 DEMO at 99.38", "Cash now Rs 357,034.73",
                       "Next: ticket.py protect --symbol DEMO for the stop-limit order. Hold up to Fri 2026-11-27. The "
                       "alerts come from psx/portfolio.json now."], not_has=["Be flat by"])
        book = read_json(t, "portfolio.json")
        self.assertEqual(book["cash"], 357034.73)
        pos = book["positions"][0]
        self.assertEqual((pos["ticker"], pos["shares"], pos["plan"]), ("DEMO", 1436, SWING_PLAN))

        # 7. protect on paper: no stop order rests anywhere, so no ticket is written
        orders = read_bytes(os.path.join(t, "psx", "orders.json"))
        self.step(t, "psx-broker/ticket.py", "protect", "--symbol", "DEMO", "--now", "2026-10-29 09:45",
                  has=["Paper: no stop order rests anywhere. psx-alerts rings at the stop; then ticket.py flatten."])
        self.assertEqual(read_bytes(os.path.join(t, "psx", "orders.json")), orders)

        # 8. the alerts: on the buy day from the plan (no flat-by rule), then from the ledger while it is held
        self.step(t, "psx-alerts/alerts.py", "add", "--from-plan",
                  has=["Added demo-plan-stop", "Added demo-plan-target",
                       "From the fill on, the swing alerts come from psx/portfolio.json and stay on until DEMO is sold."])
        self.assertEqual([(r["id"], r["type"], r["date"]) for r in read_json(t, "alerts.json")["rules"]],
                         [("demo-plan-stop", "stop", "2026-10-29"), ("demo-plan-target", "target", "2026-10-29")])
        self.step(t, "psx-alerts/alerts.py", "list",
                  has=["demo-swing-stop-2026-10-29", "demo-swing-target-2026-10-29", "demo-swing-trend-2026-10-29",
                       "demo-swing-time-2026-10-29", "(from psx/portfolio.json)"])
        # Fri 10-30 after the close: the closing files of the buy day and of that day; nothing hit
        self.step(t, "psx-alerts/alerts.py", "check", "--offline", "--now", "2026-10-30 17:10", code=1,
                  has=["Nothing hit. 3 rule(s) watched today",
                       "note: DEMO: checked the closing files of Thu 2026-10-29 and Fri 2026-10-30."])
        # no check on Mon 11-02 or Tue 11-03 (a Mac that slept): the check after the close of Wed 11-04
        # reads those two files and that day's, once each, and rings the target from that day's high
        self.step(t, "psx-alerts/alerts.py", "check", "--offline", "--now", "2026-11-04 16:10",
                  has=["ALERT DEMO traded at or over your target 105.64 on Wed 2026-11-04: the high in that day's "
                       "closing file was 105.90. Your plan's target rule is met. You could sell: psx-broker flatten "
                       "--symbol DEMO --limit P, then psx-risk and your yes. You decide.",
                       "note: DEMO: checked the closing files of Mon 2026-11-02, Tue 2026-11-03 and Wed 2026-11-04."])
        state = read_json(t, os.path.join("state", "alerts.json"))
        self.assertEqual((state["closes_checked"], state["swing_met"]),
                         ({"DEMO-2026-10-29": "2026-11-04"}, {"DEMO-2026-10-29": {"target": "2026-11-04"}}))
        out = self.step(t, "psx-alerts/alerts.py", "check", "--offline", "--now", "2026-11-04 16:25", code=1,
                        not_has=["checked the closing files", "ALERT "])                  # read once, rung once
        # the summary line: "Nothing hit." or, while a swing rule stays met, "No new alert." (psx-alerts' words)
        self.assertRegex(out, r"(?m)^(Nothing hit|No new alert)\. 3 rule\(s\) watched today")

        # 9. Thu 11-05, the pre-open: a limit sell (PSX takes no market order then); psx-risk: an exit
        self.step(t, "psx-broker/ticket.py", "flatten", "--symbol", "DEMO", "--limit", "105.64", "--now",
                  "2026-11-05 09:20", has=["Flatten DEMO: SELL 1,436 DEMO, limit 105.64, good for today, REG board."],
                  not_has=["Be flat by"])
        sell = read_json(t, "orders.json")["orders"][0]
        self.assertEqual((sell["side"], sell["qty"], sell["type"], sell["limit_price"], sell["why"]),
                         ("sell", 1436, "limit", 105.64, "close the DEMO swing position"))
        self.step(t, "psx-risk/check.py", "--offline", "--now", "2026-11-05 09:20",
                  has=["PASS  #1 sell DEMO 1436 shares, exit"])

        # 10. the yes and the paper sale: DEMO leaves the ledger, so its swing alerts stop by themselves
        self.step(t, "psx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes", "--now", "2026-11-05 09:21",
                  has=["In your broker app: SELL DEMO, REG board, quantity 1436, LIMIT price 105.64, valid for the day"])
        self.step(t, "psx-broker/ticket.py", "result", "--index", "1", "--status", "placed", "--now",
                  "2026-11-05 09:40", has=["Paper fill: SELL 1,436 DEMO at 105.64"])
        self.assertEqual(read_json(t, "portfolio.json")["positions"], [])
        self.step(t, "psx-alerts/alerts.py", "check", "--offline", "--now", "2026-11-05 16:10", code=1,
                  has=["Nothing hit. 0 rule(s) watched today."])
        self.step(t, "psx-alerts/alerts.py", "list", not_has=["demo-swing-"])

    def test_a_live_stop_limit_blocks_a_second_sell(self):
        # the same DEMO position, in a temp copy with "paper": false (made-up data: nothing goes anywhere)
        t = copy_project(demo_bought()["bought"])
        edit_config(t, paper=False)
        book = read_bytes(os.path.join(t, "psx", "portfolio.json"))
        # Fri 10-30, after the opening match (9:17 AM on a Friday): the stop-limit ticket of the held shares
        self.step(t, "psx-broker/ticket.py", "protect", "--symbol", "DEMO", "--now", "2026-10-30 09:40",
                  has=["Protect DEMO: SELL 1,436 DEMO, stop-limit, trigger 96.25, limit 95.28, good for today, REG "
                       "board.", "the user's yes for this one order: \"yes 96.25\""])
        doc = read_json(t, "orders.json")
        self.assertIs(doc["paper"], False)
        self.assertEqual([(o["side"], o["qty"], o["type"], o["trigger_price"], o["limit_price"]) for o in doc["orders"]],
                         [("sell", 1436, "stop_limit", 96.25, 95.28)])
        self.step(t, "psx-risk/check.py", "--offline", "--now", "2026-10-30 09:40",
                  has=["PASS  #1 sell DEMO 1436 shares, exit", "stop-limit: the trigger 96.25 is under the last close"])
        self.step(t, "psx-broker/ticket.py", "show", "--now", "2026-10-30 09:41",
                  has=["READY: psx-risk PASS, waiting for your yes",
                       "No order guards the opening price. A gap at the open can go past your stop.",
                       "Reply \"yes 96.25\" to approve this one order, or \"no\" to skip it."],
                  not_has=["In your broker app", "STOP LIMIT trigger price"])
        self.step(t, "psx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes, put the stop", "--now",
                  "2026-10-30 09:41", code=1)                     # "stop" is a doubt word: not a yes
        out = self.step(t, "psx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes 96.25", "--now",
                        "2026-10-30 09:41", has=["SELL DEMO, REG board, quantity 1436, STOP LIMIT trigger price 96.25, "
                                                 "limit price 95.28, valid for the day"])
        fp = fingerprint_in(out)
        self.assertIsNotNone(fp, out)
        # placed in the app: it rests there; nothing is filled, so the ledger does not change
        self.step(t, "psx-broker/ticket.py", "result", "--index", "1", "--status", "placed", "--broker-id", "FS-2001",
                  "--now", "2026-10-30 09:42", has=["Recorded order 1 as placed."], not_has=["Paper fill"])
        self.assertEqual(read_bytes(os.path.join(t, "psx", "portfolio.json")), book)
        # a sell by hand while the stop-limit rests: psx-broker says to cancel it first, psx-risk blocks it
        self.step(t, "psx-broker/ticket.py", "flatten", "--symbol", "DEMO", "--now", "2026-10-30 10:30",
                  has=["Flatten DEMO: SELL 1,436 DEMO, market, good for today, REG board.",
                       "ticket.py result --fingerprint %s --status cancelled" % fp])
        self.step(t, "psx-risk/check.py", "--offline", "--now", "2026-10-30 10:31", code=1,
                  has=["BLOCK #1 sell DEMO 1436 shares, exit",
                       "short: 1436 DEMO shares are already in a sell placed today (stop-limit, 09:42)",
                       "--fingerprint %s" % fp])
        # cancelled in the app, then recorded: the same sell passes, as an exit
        self.step(t, "psx-broker/ticket.py", "result", "--fingerprint", fp, "--status", "cancelled", "--now",
                  "2026-10-30 10:33", has=["Recorded the DEMO sell of Fri 2026-10-30 as cancelled"])
        self.step(t, "psx-risk/check.py", "--offline", "--now", "2026-10-30 10:34",
                  has=["PASS  #1 sell DEMO 1436 shares, exit"], not_has=["BLOCK"])
        self.assertEqual([r["status"] for r in read_json(t, os.path.join("state", "broker.json"))["orders"]
                          if r.get("fingerprint") == fp], ["approved", "placed", "cancelled"])



_HELP = {}


def help_text(script, *sub):
    """(exit code, text) of a pack script's --help (or one subcommand's), run once each, in a temp
    folder. Auto-run's own folders point into it too, though --help reads and writes nothing."""
    key = (script,) + sub
    if key not in _HELP:
        if "help-dir" not in _CACHE:
            _CACHE["help-dir"] = new_temp("psx-chain-help-")
        base = _CACHE["help-dir"]
        env = dict(os.environ, PSX_AUTORUN_HOME=os.path.join(base, "home"), PSX_AUTORUN_LOGS=os.path.join(base, "logs"))
        p = subprocess.run([sys.executable, os.path.join(ROOT, script)] + list(sub) + ["--help"], cwd=base, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        _HELP[key] = (p.returncode, p.stdout)
    return _HELP[key]


def positional_part(text):
    """The "positional arguments:" part of a --help text ("" when there is none)."""
    if "positional arguments:" not in text:
        return ""
    part = text.split("positional arguments:", 1)[1]
    return re.split(r"\n(?:options|optional arguments):", part, maxsplit=1)[0]


def subcommands(text):
    """The subcommand names a --help text lists (indented 4 under "positional arguments:"), or []."""
    return re.findall(r"(?m)^    ([a-z][a-z0-9_-]*)(?=\s{2,}|$)", positional_part(text))


def command_problems(script, words):
    """What in one command (a pack script and the words after it) its --help does not have: the
    subcommand, a flag, or a value of a flag with fixed choices. [] when all is there."""
    code, text = help_text(script)
    if code != 0:
        return ["%s --help exited %s" % (script, code)]
    rest = list(words)
    subs = subcommands(text)
    if subs:
        if not rest or rest[0] not in subs:
            return ["%s: %r is not one of its commands (%s)" % (script, rest[0] if rest else "", ", ".join(subs))]
        code, text = help_text(script, rest[0])
        if code != 0:
            return ["%s %s --help exited %s" % (script, rest[0], code)]
        rest = rest[1:]
    found = []
    for i, word in enumerate(rest):
        if not word.startswith("--"):
            continue
        flag = word.split("=", 1)[0]
        if not re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(flag), text):
            found.append("%s: its --help has no %s" % (script, flag))
            continue
        m = re.search(r"%s\s+\{([^}]+)\}" % re.escape(flag), text)
        if m and i + 1 < len(rest) and not rest[i + 1].startswith("-") and rest[i + 1] not in m.group(1).split(","):
            found.append("%s %s %s: the choices are %s" % (script, flag, rest[i + 1], m.group(1)))
    for group in re.findall(r"(?m)^  \{([^}]+)\}", positional_part(text)):
        if not set(group.split(",")) & set(rest):
            found.append("%s: it needs one of %s" % (script, group))
    return found


def shell_problems(script, words):
    """The flags of a shell script (install.sh, uninstall.sh) that its option list does not have.
    The script is only read, never run."""
    with open(os.path.join(ROOT, script), encoding="utf-8") as fh:
        text = fh.read()
    return ["%s: it takes no %s" % (script, w) for w in words if w.startswith("--") and
            not re.search(r"(?m)^\s*(?:[\w-]+\|)*%s(?:\|[\w-]+)*\)" % re.escape(w), text)]


class LongTerm(Steps):
    """psx-invest's orders take the normal path (SPEC v2 3.7, D40): psx/orders.json, psx-risk,
    psx-broker, your yes. Made up: the market of fixtures/psx-invest, the fixture's long-term config."""

    def test_the_long_term_orders_pass_risk_and_print_a_ticket(self):
        t = lt_project()
        # Mon 2026-11-02 in the session: the TRIM sell first, then the ADD buy, both with no stop
        self.step(t, "psx-invest/invest.py", "orders", "--write", "--now", LT_SESSION,
                  has=["Could sell 778 ZCAP at 91.55", "Could buy 142 ZUP at 242.04",
                       "Wrote 2 orders to psx/orders.json: the sells first, then the buys."])
        doc = read_json(t, "orders.json")
        self.assertEqual([(o["side"], o["symbol"], o["qty"], o["stop_loss"], o["plan"]["style"], o["plan"]["signal"])
                          for o in doc["orders"]],
                         [("sell", "ZCAP", 778, None, "invest", "TRIM"), ("buy", "ZUP", 142, None, "invest", "ADD")])
        # "require_stop": true (the trading default): the buy with no stop is blocked; the TRIM sell is an exit
        strict = os.path.join(t, "psx", "config-stop.json")
        cfg = read_doc(os.path.join(t, "psx", "config.json"))
        cfg["require_stop"] = True
        write_doc(strict, cfg)
        self.step(t, "psx-risk/check.py", "--offline", "--now", LT_SESSION, "--config", strict, "--no-write", code=1,
                  has=["PASS  #1 sell ZCAP 778 shares, exit", "BLOCK #2 buy ZUP 142 shares, entry", "stop: no stop."])
        # the long-term value "require_stop": false: both pass; a buy with no stop gets a warning
        self.step(t, "psx-risk/check.py", "--offline", "--now", LT_SESSION,
                  has=["PASS  #1 sell ZCAP 778 shares, exit", "PASS  #2 buy ZUP 142 shares, entry",
                       "no stop: the max loss is not capped, so it is not counted in the daily loss"])
        out = self.step(t, "psx-broker/ticket.py", "show", "--now", "2026-11-02 10:31",
                        has=["SELL 778 ZCAP, limit 91.55, good for today, REG board",
                             "BUY 142 ZUP, limit 242.04, good for today, REG board"],
                        not_has=["plan stop", "max loss about", "Be flat by"])
        self.assertEqual(out.count("READY: psx-risk PASS, waiting for your yes"), 2)
        out = self.step(t, "psx-broker/ticket.py", "approve", "--index", "2", "--reply", "yes", "--now",
                        "2026-11-02 10:31",
                        has=["In your broker app: BUY ZUP, REG board, quantity 142, LIMIT price 242.04, valid for the day"])
        self.assertNotIn("stop", out.lower())                          # a long-term buy has no stop lines
        # paper, and "real_holdings": true in psx/portfolio.json: no psx/orders.json, only the journal
        r = lt_project(real_holdings=True)
        self.step(r, "psx-invest/invest.py", "orders", "--write", "--now", LT_SESSION,
                  has=["Paper mode and real holdings: nothing was written to psx/orders.json."])
        self.assertFalse(os.path.exists(os.path.join(r, "psx", "orders.json")))
        with open(os.path.join(r, "psx", "invest", "journal.jsonl"), encoding="utf-8") as fh:
            last = json.loads(fh.read().splitlines()[-1])
        self.assertEqual((last["kind"], last["written"]), ("orders", False))



def forbidden(name):
    def call(*a, **k):
        raise AssertionError("a test called autorun.%s: these tests start no Mac tool" % name)
    return call


class Autorun(unittest.TestCase):
    """psx-autorun runs the pack's own tools (SPEC v2 4.3). Nothing here starts auto-run, loads a
    schedule or shows a message: its Mac tools are fakes that fail the test, and its own files would
    go into a temp folder. The steps' tools run with --help, and invest.py signals in a temp folder."""

    HOOKS = ("run_osascript", "start_caffeinate", "caffeinate_while", "kickstart", "open_file", "other_pollers",
             "SLEEP", "log")

    @classmethod
    def setUpClass(cls):
        cls.auto = load("psx-autorun", "autorun")
        cls.saved = dict((k, getattr(cls.auto, k)) for k in cls.HOOKS)
        for k in cls.HOOKS[:5]:
            setattr(cls.auto, k, forbidden(k))
        cls.auto.other_pollers = lambda: []        # a tape may really be running on this Mac: never look
        cls.auto.SLEEP = lambda s: None
        cls.auto.log = lambda msg: None             # auto-run's log file stays where it is
        cls.env = dict(os.environ)
        cls.tmp = new_temp("psx-chain-autorun-")
        os.environ["PSX_AUTORUN_HOME"] = os.path.join(cls.tmp, "home")
        os.environ["PSX_AUTORUN_LOGS"] = os.path.join(cls.tmp, "logs")

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved.items():
            setattr(cls.auto, k, v)
        os.environ.clear()
        os.environ.update(cls.env)

    def test_autorun_steps_match_the_tools(self):
        auto = self.auto
        found, checked = [], 0
        for job in sorted(auto.DEFAULT_JOBS):
            for s in auto.DEFAULT_JOBS[job]:
                where = "%s: %s" % (job, s["id"])
                auto.check_safe(s)                  # no default step is an order tool, or an order command
                self.assertTrue(os.path.isfile(os.path.join(ROOT, s["script"])), "%s: no %s" % (where, s["script"]))
                words = [w.replace("{price_source}", auto.DEFAULTS["price_source"]) for w in s["args"]
                         if w != "@holdings"]
                extra = list(s["busy_args"]) + (["--offline"] if s["site"] == "tape" else [])
                found += ["%s: %s" % (where, p) for p in command_problems(s["script"], words + extra)]
                checked += 1
        # the one look on an expected moon holiday (SPEC v2 4.2): its step is only read here, never run
        trading = os.path.join(self.tmp, "trading")
        os.makedirs(os.path.join(trading, "psx"))
        write_doc(os.path.join(trading, "psx", "config.json"), {"psx_site": True})
        write_doc(os.path.join(trading, "psx", "portfolio.json"), {"positions": [{"ticker": "DEMO", "shares": 10}]})
        looks, real_run_step = [], auto.run_step
        auto.run_step = lambda cfg, s, busy, offline_ok=False: looks.append(s) or {"out": "", "status": "ok"}
        try:
            auto.run_probe({"folders": {"trading": trading, "longterm": ""}}, {}, auto.Notes(mode="print"),
                           auto.now_karachi("2027-03-05 10:00").date(), auto.now_karachi("2027-03-05 10:00"))
        finally:
            auto.run_step = real_run_step
        self.assertEqual([s["script"] for s in looks], ["psx-alerts/alerts.py"])
        found += ["probe: %s" % p for p in command_problems(looks[0]["script"], looks[0]["args"] + looks[0]["busy_args"])]
        self.assertEqual(found, [])
        self.assertGreater(checked, 15)
        self.signal_lines()

    def test_the_readme_pause_words_match_the_runner(self):
        """README "One tool on the site at a time": auto-run sees a tape or an entry watch by itself, and
        then each step runs with its busy flags (the alerts with --offline), so it reads no PSX page.
        "pause checks" stops the checks while PSX is open, for the rest of that day only. The check
        after the close still runs."""
        auto = self.auto
        self.assertEqual(sorted(auto.POLLERS), ["psx-entry/entry.py watch", "psx-scanner/scan.py tape"])
        for job in sorted(auto.DEFAULT_JOBS):
            for s in auto.DEFAULT_JOBS[job]:
                if s["site"] != "no":
                    self.assertTrue(s["busy_args"], "%s: %s reads the site with another poller on" % (job, s["id"]))
                if s["script"] == "psx-alerts/alerts.py":
                    self.assertEqual(s["busy_args"], ["--offline"], "%s: %s" % (job, s["id"]))
        cfg = auto.deep_merge(auto.DEFAULTS, {"folders": {"trading": self.tmp, "longterm": self.tmp}})
        day = {"segments": [["09:30", "15:30"]], "session": "regular", "reason": "", "calendar_known": True,
               "calendar_note": ""}

        def due(at, paused):
            state = {"installed": "2026-10-01 08:00"}
            if paused:
                state["pause"] = {"checks_off": "2026-11-11"}       # autorun.py pause checks, on Wed 11-11
            for d in (datetime.date(2026, 11, 11), datetime.date(2026, 11, 12)):
                auto.mark(state, "morning", d, "%s 08:15" % d.isoformat())
            return [(job, note) for job, _, note in auto.due(cfg, state, auto.now_karachi(at), day)
                    if job != "weekly"]

        self.assertEqual(due("2026-11-11 11:00", False), [("check", "")])
        self.assertEqual(due("2026-11-11 11:00", True), [])                      # no check while PSX is open
        self.assertEqual(due("2026-11-11 15:45", True), [])                      # nor the last one at 3:40 PM
        self.assertEqual(due("2026-11-11 16:15", False), [("check", "final0"), ("after_close", "")])
        self.assertEqual(due("2026-11-11 16:15", True), [("after_close", "")])  # the check after the close runs
        self.assertEqual(due("2026-11-12 11:00", True), [("check", "")])        # the next day: on by itself

    def test_the_readme_check_times_match_the_plan(self):
        """review F105: README "Auto-run on your Mac": each job's time, from the runner's own day plan and the
        pack's PSX clock (the lines autorun.py plan prints). launchd ticks at minute 0, 5, 10 ..., so a check
        window that opens at 9:37 AM has its first check at 9:40 AM."""
        auto = self.auto
        text = readme_text()
        section = text[text.index("## Auto-run on your Mac (psx-autorun)"):text.index("## The tools")]
        rows = dict((job, when.strip()) for job, when in
                    re.findall(r"(?m)^\| (morning|price checks|after the close) \| ([^|]+)\|", section))
        self.assertEqual(sorted(rows), ["after the close", "morning", "price checks"])
        trading = new_temp("psx-chain-plan-")
        os.makedirs(os.path.join(trading, "psx"))
        shutil.copy(os.path.join(ROOT, "psx-budget", "config.example.json"), os.path.join(trading, "psx", "config.json"))
        cfg = auto.deep_merge(auto.DEFAULTS, {"python": sys.executable, "pack": ROOT,
                                             "folders": {"trading": trading, "longterm": ""}})

        def plan(day):
            d = datetime.date.fromisoformat(day)
            info = auto.day_info(cfg, {}, d)              # psx-alerts/alerts.py clock, as autorun.py plan asks it
            lines = auto.plan_lines(cfg, auto.day_plan(cfg, info, d), d, info)
            return [re.split(r"\s{2,}", x, maxsplit=1) for x in lines]

        for day in ("2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"):        # Monday to Thursday
            lines = plan(day)
            self.assertEqual([what for _, what in lines], ["morning check", "price checks every 5 minutes",
                                                           "last check of the day",
                                                           "after-close check (it waits for the closing file)"], day)
            (morning, _), (window, _), (last, _), (after, _) = lines
            self.assertEqual(window, "9:40 AM to 3:30 PM", day)
            self.assertEqual(rows["morning"], morning, day)
            self.assertEqual(rows["price checks"], "every 5 minutes from %s, and at %s" % (window, last), day)
            self.assertTrue(rows["after the close"].startswith(after + ", "), (day, rows["after the close"]))
        # Friday 2026-11-13, the day the README names: two parts, around the Juma break
        self.assertEqual(datetime.date(2026, 11, 13).weekday(), 4)
        self.assertIn("`autorun.py plan --date 2026-11-13` shows the times of that Friday.", flat(text))
        starts = [when.split(" to ")[0] for when, what in plan("2026-11-13") if what.startswith("price checks")]
        self.assertEqual(starts, ["9:25 AM", "2:40 PM"])
        self.assertIn("On Fridays the price checks start at %s, and again at %s after the Juma break." % tuple(starts),
                      flat(text))
        self.assertNotIn("9:37 AM", text)

    def test_the_readme_entry_watch_words_match_the_runner(self):
        """review F53: README "One tool on the site at a time" and psx-entry/SKILL.md. An entry watch saves only
        the page of the share it watches. Auto-run's own alerts step (the screener, saved prices only) then
        sees no new price, and after 3 rounds of 5 minutes the runner sends the words the docs quote."""
        auto = self.auto
        part = fine_print(readme_text(), "One tool on the site at a time.")
        for words in ("A tape saves new prices of the whole market, so the checks go on.",
                      "An entry watch saves no market prices, only the page of the share it watches. So the trading "
                      "folder's alerts get no new price. After 15 minutes auto-run sends a problem message: \"the DEMO "
                      "alerts had no new price for 15 minutes\". While a watch runs, check your stop in your broker "
                      "app."):
            self.assertIn(words, part)
        with open(os.path.join(ROOT, "psx-entry", "SKILL.md"), encoding="utf-8") as fh:
            skill = flat(fh.read())
        for words in ("Auto-run (`/psx-autorun`) sees a watch by itself. Then it reads no PSX page, so no pause is "
                      "needed.",
                      "An entry watch saves no market prices, only the page of the share it watches. So with auto-run "
                      "on, the trading folder's alerts get no new price. After 15 minutes auto-run sends a problem "
                      "message: \"the DEMO alerts had no new price for 15 minutes\". While a watch runs, tell the user "
                      "to check the stop in the broker app."):
            self.assertIn(words, skill)
        # psx-entry (no change in v2): the watch's one tape write is the watched share's own file, never the
        # market tape, and it never writes psx/prices.json
        with open(os.path.join(ROOT, "psx-entry", "entry.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(re.findall(r"(?m)^\s+append_tape\((.+)\)$", src),
                         ["tape_path(a.data, day, sym), poll_company(sym, cfg, now)"])
        self.assertNotIn("prices.json", src)
        entry = load("psx-entry", "entry")
        self.assertEqual(entry.tape_path("psx/data", datetime.date(2026, 10, 29), "DEMO"),
                         os.path.join("psx/data", "tape", "2026-10-29", "DEMO.jsonl"))
        # what a watch leaves (made up): the sample market's tape ends at 10:00, DEMO's own page goes on
        t = new_temp("psx-chain-watch-")
        os.makedirs(os.path.join(t, "psx"))
        shutil.copytree(MARKET, os.path.join(t, "psx", "data"))
        shutil.copy(os.path.join(ROOT, "psx-budget", "config.example.json"), os.path.join(t, "psx", "config.json"))
        tape = os.path.join(t, "psx", "data", "tape", "2026-10-29", "market.jsonl")
        with open(tape, encoding="utf-8") as fh:
            keep = [x for x in fh if json.loads(x)["asof"] <= "2026-10-29 10:00:00"]
        with open(tape, "w", encoding="utf-8") as fh:
            fh.write("".join(keep))
        write_doc(os.path.join(t, "psx", "alerts.json"),
                  {"rules": [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 96.25}]})
        # auto-run's own check step for the trading folder, with its flags while another PSX tool runs
        s = [x for x in auto.DEFAULT_JOBS["check"] if x["folder"] == "trading" and x["kind"] == "alerts"][0]
        self.assertEqual(auto.DEFAULTS["price_source"], "psx-screener")
        args = [w.replace("{price_source}", auto.DEFAULTS["price_source"]) for w in s["args"]] + s["busy_args"]
        self.assertEqual(args, ["check", "--source", "psx-screener", "--offline"])
        code, out = run(t, s["script"], *(args + ["--now", "2026-10-29 10:30", "--no-write"]))
        self.assertIn(code, s["ok"], out)
        self.assertIn("note: DEMO: the data is 30 minutes old (as of 10:00 AM PKT).", out)
        self.assertEqual(problems(out), [])
        # the same folder through DEMO's own page (the watch's page): a new price, so the source is the point
        code, out2 = run(t, s["script"], "check", "--source", "psx-company", "--offline", "--now", "2026-10-29 10:30",
                         "--no-write")
        self.assertIn("DEMO 100.85 (as of 10:30 AM PKT)", out2)
        self.assertNotIn("minutes old", out2)
        # the runner: no message after 1 or 2 rounds, one after the 3rd (15 minutes), in the docs' words
        why, words = auto.old_price(out, auto.DEFAULTS["check_every_min"])
        self.assertEqual(why, "no new price for DEMO (the newest one is 30 minutes old)")
        self.assertEqual(auto.DEFAULTS["problem_after_fails"] * auto.DEFAULTS["check_every_min"], 15)
        state, notes, told = {}, auto.Notes(mode="print"), []
        for at in ("2026-10-29 10:30", "2026-10-29 10:35", "2026-10-29 10:40"):
            auto.problem(state, notes, auto.DEFAULTS, "check:%s" % s["id"], words, auto.now_karachi(at),
                         after=auto.DEFAULTS["problem_after_fails"])
            told.append([(title, text) for title, text, _ in notes.ordered()])
            notes.items = []
        self.assertEqual(told, [[], [], [("PSX auto-run: problem", "the DEMO alerts had no new price for 15 minutes: the "
                                          "newest one is 30 minutes old. Check the price in your broker app.")]])

    def signal_lines(self):
        """invest.py signals --write --offline, run by the runner's own run_step in a temp long-term
        folder: the rows it counts, its "In short" line, and a notify line only when a signal changed."""
        auto = self.auto
        t = lt_project()
        s = [x for x in auto.DEFAULT_JOBS["after_close"] if x["id"] == "signals"][0]
        self.assertEqual(s["args"], ["signals", "--write", "--offline"])
        step = dict(s, args=s["args"] + ["--now", LT_EVENING])     # the runner's own step, on a fixed day
        cfg = {"python": sys.executable, "pack": ROOT, "folders": {"trading": "", "longterm": t},
               "price_source": "psx-screener", "step_timeout_s": 600}
        held = sorted(p["ticker"] for p in read_json(t, "portfolio.json")["positions"])

        def tick():
            res = auto.run_step(cfg, step, False)
            self.assertEqual((res["status"], res["rc"]), ("ok", 0), res)
            self.assertEqual(problems(res["out"]), [])
            self.assertEqual(sorted(sym for _, sym in auto.SIGNAL_RE.findall(res["out"])), held)   # one row a share
            return res, [x[8:].strip() for x in res["out"].splitlines() if x.lower().startswith("notify: ")]

        res, told = tick()            # the first run: every signal is new; a HOLD needs no message
        line = auto.summarize(step, res, t)
        self.assertEqual(line, "Long-term signals on the close: TRIM: ZCAP. REVIEW: ZBRK, ZOLD, ZBON, ZGAP, ZRES, "
                               "ZSTL, ZRIT. ADD: ZUP. HOLD: 3. Rules, not predictions.")
        self.assertEqual(res["count"], 9)
        self.assertEqual(sorted(x.split(":")[0] for x in told),
                         sorted(sym for sig, sym in auto.SIGNAL_RE.findall(res["out"]) if sig != "HOLD"))
        self.assertIn("ZCAP: a sell rule is met (TRIM). Open the report. You decide.", told)
        self.assertEqual(tick()[1], [])                   # the same files again: no change, no message
        # your own choice, in the chat (auto-run never runs ack: the guard refuses it)
        with self.assertRaises(auto.AutorunError):
            auto.check_safe(auto.step("ack", "longterm", "psx-invest/invest.py", ["ack", "ZOLD", "--keep"]))
        must(t, "psx-invest/invest.py", "ack", "ZOLD", "--keep", "--now", LT_EVENING)
        self.assertEqual(tick()[1], ["ZOLD: no rule asks for a change now (HOLD)."])



def kind(x):
    """The JSON type of a value: null, bool, number, text, list or object."""
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "bool"
    if isinstance(x, (int, float)):
        return "number"
    if isinstance(x, str):
        return "text"
    return "list" if isinstance(x, list) else "object" if isinstance(x, dict) else type(x).__name__


def contract(name):
    return read_doc(os.path.join(CONTRACT, name))


class Shapes(unittest.TestCase):
    """The files the chains write have the keys and types of fixtures/psx-v2/contract/ (D51).
    The values may differ: the risk fixtures' protect sell has 143 shares, this chain's has 1,436.
    Not compared: portfolio-real.json (you write it from your broker statement; no tool does) and
    clock-keys.json (tests/test_contract.py checks it against the real clock)."""

    # broker.json keys a real record may have or not: the app's order number (only with --broker-id),
    # and psx-risk's entry kept with the yes of a sell (a buy must have it: psx-risk counts its cash)
    MAYBE = {"buy": ("broker_order_id",), "sell": ("broker_order_id", "entry_ref")}

    def same_shape(self, real, want, where, maybe=()):
        """real has want's keys and only those (a key in maybe may be there or not), each with the same
        JSON type. Objects inside are compared the same way, and each item of a list with want's first."""
        self.assertEqual(kind(real), kind(want), "%s: %s, not %s" % (where, kind(real), kind(want)))
        if isinstance(want, dict):
            missing = sorted(set(want) - set(real) - set(maybe))
            extra = sorted(set(real) - set(want) - set(maybe))
            self.assertEqual((missing, extra), ([], []), "%s: (keys missing, keys not in the contract)" % where)
            for k in want:
                if k in real:
                    self.same_shape(real[k], want[k], "%s.%s" % (where, k), maybe)
        elif isinstance(want, list) and want:
            for i, item in enumerate(real):
                self.same_shape(item, want[0], "%s[%d]" % (where, i), maybe)

    def test_the_shapes_match_the_contract_files(self):
        made = demo_bought()
        t = made["bought"]
        plan = read_json(t, "plan.json")
        # here the buy day's limits come from the company page, and "base" says which close a computed
        # limit is from, so it is there only when "from" is "computed" (as in v1)
        self.same_shape(plan, contract("plan-swing.json"), "plan.json (psx-exit)",
                        () if plan["limits"]["from"] == "computed" else ("base",))
        self.same_shape(read_json(t, "orders.json"), contract("orders-swing.json"), "orders.json (psx-budget)")
        self.same_shape(read_json(t, "portfolio.json"), contract("portfolio-swing.json"), "portfolio.json (psx-broker)")
        # a live stop-limit, placed in the app, then cancelled there (a temp copy, made-up data)
        live = copy_project(t)
        edit_config(live, paper=False)
        must(live, "psx-broker/ticket.py", "protect", "--symbol", "DEMO", "--now", "2026-10-30 09:40")
        self.same_shape(read_json(live, "orders.json"), contract("orders-protect.json"), "orders.json (protect)")
        must(live, "psx-risk/check.py", "--offline", "--now", "2026-10-30 09:40")
        fp = fingerprint_in(must(live, "psx-broker/ticket.py", "approve", "--index", "1", "--reply", "yes 96.25",
                                 "--now", "2026-10-30 09:41"))
        must(live, "psx-broker/ticket.py", "result", "--index", "1", "--status", "placed", "--broker-id", "FS-2001",
             "--now", "2026-10-30 09:42")
        must(live, "psx-broker/ticket.py", "result", "--fingerprint", fp, "--status", "cancelled", "--now",
             "2026-10-30 11:05")
        # a buy with a yes that never filled, recorded expired the next day by its fingerprint
        old = copy_project(made["approved"])
        must(old, "psx-broker/ticket.py", "result", "--fingerprint", BUY_FP, "--status", "expired", "--now",
             "2026-10-30 16:15")
        want = {}
        for name in ("broker-placed-unfilled.json", "broker-expired.json", "broker-protect-open.json",
                     "broker-protect-cancelled.json"):
            for r in contract(name)["orders"]:
                want[(r["status"], r["side"])] = r
        got = read_json(live, os.path.join("state", "broker.json"))["orders"]
        got += read_json(old, os.path.join("state", "broker.json"))["orders"][-1:]
        seen = set()
        for r in got:
            key = (r["status"], r["side"])
            if key == ("filled", "buy"):
                continue                            # the v1 fill record: no contract file has one
            self.assertIn(key, want, "broker.json: a record the contract does not have: %s" % (key,))
            self.same_shape(r, want[key], "broker.json %s %s" % key, self.MAYBE[r["side"]])
            seen.add(key)
        self.assertEqual(sorted(seen), sorted(want))      # every record shape of the contract was written
        # psx-invest's orders (the long-term chain)
        lt = lt_project()
        must(lt, "psx-invest/invest.py", "orders", "--write", "--now", LT_SESSION)
        self.same_shape(read_json(lt, "orders.json"), contract("orders-invest.json"), "orders.json (psx-invest)")


def readme_commands(text):
    """The commands in README.md: each line of a ```bash block that runs python3 or bash, and each
    `code` span that starts with python3, bash or a tool's script name and has words after it."""
    names = {"autorun.py": "psx-autorun/autorun.py", "install.sh": "psx-autorun/install.sh",
             "uninstall.sh": "psx-autorun/uninstall.sh"}
    for folder in sorted(os.listdir(ROOT)):
        if folder.startswith("psx-") and os.path.isdir(os.path.join(ROOT, folder)):
            for f in sorted(os.listdir(os.path.join(ROOT, folder))):
                if f.endswith(".py"):
                    names.setdefault(f, folder + "/" + f)
    lines = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        lines += [re.sub(r"\s+#\s.*$", "", x).strip() for x in block.splitlines()]
    lines += re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.S))
    found = []
    for line in lines:
        head = line.split()[:2]
        if not head or (head[0] not in ("python3", "bash") and (head[0] not in names or len(head) < 2)):
            continue
        try:
            words = shlex.split(line)
        except ValueError:
            found.append((line, None, None, None))  # an odd quote: the test names it
            continue
        if words[0] in ("python3", "bash"):
            words = words[1:]
        if not words or words[0].startswith("-"):
            continue                                # python3 -m unittest ...
        found.append((line, words[0], names.get(os.path.basename(words[0])), words[1:]))
    return found


class Readme(unittest.TestCase):
    def test_the_readme_commands_match_the_tools_help(self):
        with open(README, encoding="utf-8") as fh:
            found = readme_commands(fh.read())
        bad, tools = [], set()
        for line, said, script, words in found:
            if said is None:
                bad.append("%s: it cannot be read as a shell command" % line)
                continue
            if script is None:
                if not any(said == os.path.relpath(os.path.join(b, f), ROOT) or said == f
                           for b, _, files in os.walk(os.path.join(ROOT, "tests")) for f in files):
                    bad.append("%s: no such script in the pack" % line)
                continue
            if "/" in said and not said.endswith(script):
                bad.append("%s: %s lives in %s" % (line, os.path.basename(script), script))
            tools.add(script)
            got = shell_problems(script, words) if script.endswith(".sh") else command_problems(script, words)
            bad += ["%s: %s" % (line, p) for p in got]
        self.assertEqual(bad, [])
        self.assertGreater(len(found), 60)
        # the script of each of the 13 skills, and auto-run's install and uninstall, show up at least once
        want = set(["psx-autorun/install.sh", "psx-autorun/uninstall.sh"])
        for folder in os.listdir(ROOT):
            if os.path.isfile(os.path.join(ROOT, folder, "SKILL.md")):
                want |= set(folder + "/" + f for f in os.listdir(os.path.join(ROOT, folder)) if f.endswith(".py"))
        self.assertEqual(sorted(want - tools), [])


# ---------------------------------------------------------------- v2: the README's own words

# The README's v1 sentences over MAX_WORDS, kept word for word, and the ABOUT line (tests/test_repo.py
# pins it): a phrase of each. Every other sentence has at most MAX_WORDS words (a `code` span is one).
V1_LONG = ("13 Claude skills for the Pakistan Stock Exchange", "DEMO's notices on PSX (results on October 27",
           "set the plan before the buy: stop at the range middle", "replayed the rest of the day 10,000 times",
           "If the stop fills at 99.55", "checks it against your limits and PSX's rules",
           "If the KSE-30 index stays 5% up or down", "In daily mode it is built from the stock's last 60 daily bars")
MAX_WORDS = 25
V1_WORDS = "A stop is a plan, not a guarantee."   # the one v1 README line that the shared word test flags


def readme_text():
    with open(README, encoding="utf-8") as fh:
        return fh.read()


def flat(text):
    """text on one line, one space between words."""
    return " ".join(text.split())


def readme_part(text, heading):
    """The README text under heading (with its # marks, like "### Paper and real shares"), up to the
    next heading of the same or a higher level, on one line. "" when there is no such heading."""
    m = re.search(r"(?m)^%s$" % re.escape(heading), text)
    if not m:
        return ""
    rest = text[m.end():]
    end = re.search(r"(?m)^#{1,%d} " % len(heading.split(" ", 1)[0]), rest)
    return flat(rest[:end.start()] if end else rest)


def fine_print(text, title):
    """The README item that starts with **title**, up to the next bold item or heading, on one line."""
    start = text.find("**%s**" % title)
    if start < 0:
        return ""
    end = re.search(r"\n\n(?:\*\*|#)", text[start + 1:])
    return flat(text[start:start + 1 + end.start()] if end else text[start:])


def prose_sentences(text):
    """(line, sentence) of each sentence in the README's prose. Code blocks, tables and headings are
    left out, a list item starts a new sentence, a `code` span counts as one word, and the ** marks
    are dropped."""
    out, buf, at, code = [], [], [0], False

    def flush():
        para = re.sub(r"`[^`]*`", "CODE", " ".join(buf)).replace("**", "")
        out.extend((at[0], s) for s in re.split(r"(?<=[.!?])[\"')]*\s+", para) if s.strip())
        del buf[:]

    for n, line in enumerate(text.splitlines(), 1):
        fence = line.startswith("```")
        code = code != fence
        if fence or code or not line.strip() or line.lstrip().startswith(("|", "#")):
            flush()
            continue
        item = re.match(r"\s*(?:[-*]|\d+\.)\s+", line)
        if item:
            flush()
            line = line[item.end():]
        if not buf:
            at[0] = n
        buf.append(line.strip())
    flush()
    return out


class ReadmeWords(Steps):
    """The README's own words, from the v2 review. Where they say what a tool does, the test runs that
    tool on made-up data (paper, the site off), so the words stay true."""

    def test_paper_stays_out_of_a_folder_of_real_shares(self):
        text = readme_text()
        part = readme_part(text, "### Paper and real shares")
        for words in ("Does a folder hold real shares or real cash? Then do not turn paper on there.",
                      "A paper fill can go into that folder's `psx/portfolio.json`, next to your real shares.",
                      'On paper, every ticket says "do not place it in your app".',
                      "Copy `psx/config.json` and `psx/data/` into its `psx/`",
                      'Do not copy `psx/portfolio.json`: the paper cash then starts from its `"budget"`.',
                      "Auto-run does not watch the new folder",
                      'put `"real_holdings": true` at the top of its `psx/portfolio.json`. Then a paper fill into '
                      "it is refused, and nothing is written."):
            self.assertIn(words, part)
        self.assertIn('Never turn paper on in a folder that holds real shares or real cash: see "Paper and real '
                      'shares".', fine_print(text, "Paper first."))
        self.assertIn('Paper first, in a folder of its own. Swing trading loses money for many people too. See '
                      '"Paper and real shares" below.', readme_part(text, "## Two styles: day and swing"))
        # the tools: a new paper folder (the example config, no psx/portfolio.json) starts from "budget";
        # a file with "real_holdings": true refuses a paper fill and stays the same, byte for byte
        t = new_temp("psx-chain-paper-")
        os.makedirs(os.path.join(t, "psx"))
        shutil.copy(os.path.join(ROOT, "psx-budget", "config.example.json"), os.path.join(t, "psx", "config.json"))
        budget = read_json(t, "config.json")["budget"]
        fill = ("fill", "--symbol", "DEMO", "--side", "buy", "--qty", "10", "--price", "100", "--date", "2026-10-29",
                "--now", "2026-10-29 10:00")
        self.step(t, "psx-broker/ticket.py", *fill, has=["Recorded buy 10 DEMO at 100.00 on 2026-10-29"])
        pf = read_json(t, "portfolio.json")
        self.assertEqual(pf["cash"], round(budget - 1000.0 - pf["positions"][0]["lots"][0]["costs"], 2))
        self.assertNotIn("real_holdings", pf)
        ledger = os.path.join(t, "psx", "portfolio.json")
        write_doc(ledger, dict([("real_holdings", True)] + list(pf.items())))
        book = read_bytes(ledger)
        self.step(t, "psx-broker/ticket.py", *fill, code=1,
                  has=['holds real shares ("real_holdings": true). A paper fill would change it, so nothing was '
                       'written.'])
        self.assertEqual(read_bytes(ledger), book)

    def test_a_share_held_before_the_switch_is_not_a_swing_position(self):
        text = readme_text()
        self.assertIn("Change the style only when no order is waiting, and you hold no shares without a swing "
                      "plan.", readme_part(text, "## Two styles: day and swing"))
        part = readme_part(text, "### If you already hold shares")
        for words in ("The swing tools watch a share only when `psx/portfolio.json` holds its swing plan.",
                      "They use only today's prices, never a closing file.",
                      "`ticket.py protect` and `ticket.py stop` refuse it",
                      "psx-risk counts its risk as your max loss per trade",
                      "So switch only after such a share is sold.",
                      "watch its stop in your broker app too", '`"rearm": "daily"`'):
            self.assertIn(words, part)
        whole = flat(text)
        self.assertEqual(whole.lower().count("no close is lost"), 1)
        self.assertIn("For a swing share with its plan in `psx/portfolio.json`, no close is lost", whole)
        self.assertIn("A rule you added by hand uses only today's prices, so it misses a day when no check ran.",
                      whole)
        # the tools: DEMO held with no plan (a day buy, or a share bought before the switch), a hand stop
        # at 96.25, on the path "gap": the low in the closing file of Mon 11-02 is 94.80
        t = swing_project("gap")
        ledger = os.path.join(t, "psx", "portfolio.json")
        with_plan = contract("portfolio-swing.json")
        no_plan = json.loads(json.dumps(with_plan))
        for p in no_plan["positions"]:
            del p["plan"]
        write_doc(ledger, no_plan)
        write_doc(os.path.join(t, "psx", "alerts.json"),
                  {"rules": [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 96.25, "rearm": "daily"}]})
        book = read_bytes(ledger)
        self.step(t, "psx-alerts/alerts.py", "check", "--offline", "--now", "2026-11-03 08:15", "--no-write", code=1,
                  has=["Nothing hit. 1 rule(s) watched today"], not_has=["ALERT", "closing file"])
        self.step(t, "psx-broker/ticket.py", "protect", "--symbol", "DEMO", "--now", "2026-11-03 09:40", code=1,
                  has=["no swing plan stop for DEMO"])
        self.step(t, "psx-broker/ticket.py", "stop", "--symbol", "DEMO", "--set", "97", "--now", "2026-11-03 09:40",
                  code=1, has=["no swing plan for DEMO"])
        self.assertEqual(read_bytes(ledger), book)
        # the same share with its swing plan in the ledger: the closing file of Mon 11-02 rings the stop
        write_doc(ledger, with_plan)
        self.step(t, "psx-alerts/alerts.py", "check", "--offline", "--now", "2026-11-03 08:15", "--no-write",
                  has=["ALERT DEMO traded at or under your stop 96.25 on Mon 2026-11-02: the low in that day's "
                       "closing file was 94.80"])

    def test_the_auto_run_pause_says_how_to_end_it(self):
        # what auto-run really does is checked in Autorun.test_the_readme_pause_words_match_the_runner
        text = readme_text()
        part = fine_print(text, "One tool on the site at a time.")
        for words in ("A tape or an entry watch: auto-run sees it by itself. Then auto-run reads no PSX page, and "
                      "its checks read only the prices saved on your computer. Do not pause auto-run for it.",
                      "Your own alerts loop (a `/loop` or a scheduled task in Claude): first run `autorun.py pause "
                      "checks`. The pause lasts the rest of the day.",
                      "Until you run `autorun.py resume`, the price checks of both folders are off, so no stop is "
                      "checked while PSX is open. The after-close job still runs.",
                      "So run `autorun.py resume` as soon as your loop ends."):
            self.assertIn(words, part)
        self.assertNotIn("long watch by hand", text)
        self.assertIn('Auto-run sees a tape or an entry watch by itself: see "One tool on the site at a time" in the '
                      'fine print.', flat(text))

    def test_plain_words_and_short_sentences(self):
        text = readme_text()
        # ATR in plain words, in the list item where the README first uses it
        first = text.index("ATR")
        self.assertIn("ATR(14) is how far the share moves in a normal day.",
                      flat(text[first:text.index("\n- ", first)]))
        # one idea a sentence: at most MAX_WORDS words, but for the v1 sentences kept word for word
        long = ["line %d (%d words): %s" % (n, len(s.split()), s) for n, s in prose_sentences(text)
                if len(s.split()) > MAX_WORDS and not any(k in s for k in V1_LONG)]
        self.assertEqual(long, [])
        # the shared word rules (tests/_words.py) hold on every README line, but for one v1 line
        self.assertEqual([p for p in problems(text) if V1_WORDS not in p], [])

    def new_folder(self, prefix, ledger=None, log=None, **config):
        """A temp project: the example config (paper, the site off) with config's changes, and
        psx/portfolio.json (ledger) and psx/state/broker.json (log) when given. Made-up data only."""
        t = new_temp(prefix)
        os.makedirs(os.path.join(t, "psx", "state"))
        shutil.copy(os.path.join(ROOT, "psx-budget", "config.example.json"), os.path.join(t, "psx", "config.json"))
        edit_config(t, **config)
        self.assertIs(read_json(t, "config.json")["psx_site"], False)
        if ledger is not None:
            write_doc(os.path.join(t, "psx", "portfolio.json"), ledger)
        if log is not None:
            write_doc(os.path.join(t, "psx", "state", "broker.json"), log)
        return t

    def test_a_live_fill_marks_the_ledger_real(self):
        # review F114 (D39): in both styles, the first fill with paper off puts the flag at the top and says so
        part = readme_part(readme_text(), "## Two styles: day and swing")
        self.assertIn("Day style works as before. If you never set a style, nothing changes.", part)
        self.assertIn('One small change in both styles: with paper off, the first fill puts `"real_holdings": true` '
                      "at the top of `psx/portfolio.json`. It prints one line about it. Then a paper fill can never "
                      "change that file.", part)
        self.assertNotIn("works exactly as before", flat(readme_text()))
        # the tools: a day folder (no style set) with a ledger made the v1 way (no flag), the same fill on paper
        # and with paper off. Only the flag and its one line differ.
        held = {"cash": 50000.0, "positions": [{"ticker": "DEMO", "shares": 100, "avg_cost": 98.0}]}
        fill = ("fill", "--symbol", "DEMO", "--side", "buy", "--qty", "10", "--price", "100", "--date", "2026-10-29")
        line = ('psx/portfolio.json now says "real_holdings": true: it holds real shares, so a paper fill can never '
                "change it.")
        got = {}
        for paper in (True, False):
            t = self.new_folder("psx-chain-live-", ledger=held, paper=paper)
            out = self.step(t, "psx-broker/ticket.py", *(fill + ("--now", "2026-10-29 10:00")),
                            has=["Recorded buy 10 DEMO at 100.00 on 2026-10-29 in psx/portfolio.json"])
            got[paper] = (t, out.splitlines(), read_json(t, "portfolio.json"))
        (_, paper_out, paper_doc), (live, live_out, live_doc) = got[True], got[False]
        self.assertEqual(live_out, paper_out + [line])                       # one more line, and only that one
        self.assertEqual(list(live_doc)[0], "real_holdings")                 # at the top
        self.assertIs(live_doc["real_holdings"], True)
        self.assertEqual(dict((k, v) for k, v in live_doc.items() if k != "real_holdings"), paper_doc)
        self.assertNotIn("real_holdings", paper_doc)                         # paper never sets it
        # the next fill with paper off: the flag is there already, so no line about it
        self.step(live, "psx-broker/ticket.py", *(fill + ("--now", "2026-10-29 10:05")), not_has=["real_holdings"])

    def test_a_file_with_no_flag_and_a_sign_of_real_shares_takes_no_paper_fill(self):
        # review F04 (F06): the real-money-to-paper rule, and the signs ticket.py looks for in a file with no flag
        text = readme_text()
        part = readme_part(text, "### Paper and real shares")
        for words in ("With paper off, a fill puts that flag in by itself. But a ledger from before v2, or one made "
                      'by hand, has no `"real_holdings"` flag, even when its shares are real.',
                      "So `ticket.py` also refuses a paper fill into a file with no flag that shows a sign of real "
                      "shares. Then nothing is written. The signs:",
                      'a top key the tools never write, like a `"note"`;',
                      "a yes in `psx/state/broker.json` made with paper off;",
                      "in swing style, a held share with no swing plan.",
                      'Then mark which it is. Real shares: put `"real_holdings": true` at the top. A paper file: put '
                      '`"real_holdings": false` at the top. Then paper fills go in.',
                      "A file with no flag and no sign takes a paper fill, as before. So lock a folder of real shares "
                      "yourself."):
            self.assertIn(words, part)
        self.assertIn('A ledger from before v2, or one made by hand, has no `"real_holdings"` flag, even when its '
                      "shares are real. So when you go from real money to paper in a folder of real shares, put "
                      '`"real_holdings": true` in its `psx/portfolio.json` first. Then practise in a separate paper '
                      "folder, with its own fresh ledger.", fine_print(text, "Paper first."))
        # the tools, on paper: each sign refuses the fill and leaves the file as it was, byte for byte. The
        # ledger holds another made-up share, OLDCO (a swing fill of a share held with no plan stops anyway).
        held = {"ticker": "OLDCO", "shares": 100, "avg_cost": 98.0}
        day = ("fill", "--symbol", "DEMO", "--side", "buy", "--qty", "10", "--price", "100", "--date", "2026-10-29",
               "--now", "2026-10-29 10:00")
        swing = day + ("--stop", "96.25", "--target", "105.64", "--exit-by", "2026-11-27")
        live_yes = {"orders": [{"at": "2026-10-28T10:00:00+05:00", "date": "2026-10-28", "fingerprint": "0123456789abcdef",
                                "symbol": "DEMO", "side": "buy", "status": "approved", "paper": False}]}
        why = 'psx/portfolio.json has no "real_holdings" key, but it may hold real shares: '
        for ledger, log, style, args, sign in (
                ({"note": "from my statement", "cash": 1000.0, "positions": [held]}, None, "day", day,
                 'it has "note", which the tools never write'),
                ({"cash": 1000.0, "positions": [held]}, live_yes, "day", day,
                 "psx/state/broker.json has a yes made with paper off (DEMO buy on 2026-10-28)"),
                ({"cash": 1000.0, "positions": [held]}, None, "swing", swing,
                 "OLDCO in it has no swing plan, so no swing fill made it")):
            t = self.new_folder("psx-chain-sign-", ledger=ledger, log=log, style=style)
            book = read_bytes(os.path.join(t, "psx", "portfolio.json"))
            self.step(t, "psx-broker/ticket.py", *args, code=1,
                      has=[why + sign, "A paper fill would change it, so nothing was written.",
                           'A paper file: put "real_holdings": false at the top.'])
            self.assertEqual(read_bytes(os.path.join(t, "psx", "portfolio.json")), book)
        # "real_holdings": false says it is a paper file: the same swing fill goes in, and the flag stays false
        t = self.new_folder("psx-chain-sign-", ledger={"real_holdings": False, "note": "paper", "cash": 1000.0,
                                                        "positions": [held]}, style="swing")
        self.step(t, "psx-broker/ticket.py", *swing, has=["Recorded buy 10 DEMO at 100.00 on 2026-10-29"])
        self.assertIs(read_json(t, "portfolio.json")["real_holdings"], False)

    def test_every_result_command_names_its_order(self):
        # review F61, F87: "ticket.py result --status expired" alone stops (exit 2), so each result names its order
        text = readme_text()
        found = [(line, words) for line, _, script, words in readme_commands(text)
                 if script == "psx-broker/ticket.py" and words and words[0] == "result"]
        self.assertGreater(len(found), 4)
        self.assertEqual([line for line, w in found if not ("--index" in w or "--fingerprint" in w
                                                           or ("--symbol" in w and "--date" in w))], [])
        row = re.search(r"(?m)^\| no fill \| `([^`]+)` \(`approve` printed FP\) \|", text)
        self.assertIsNotNone(row, "README: the no-fill row of the swing table")
        words = shlex.split(row.group(1))
        self.assertEqual(words[:2], ["ticket.py", "result"])
        self.assertIn("`--index 1` is the first order in `psx/orders.json` now. On a later day, or once a new order is "
                      "there, name the buy by its fingerprint: `ticket.py result --fingerprint FP --status expired`. "
                      "`approve` printed FP.", readme_part(text, "### The buy"))
        # the tools: the DEMO swing buy with a yes and no fill (made up, paper). The row's command, with the
        # fingerprint that approve printed, closes it at the close of the buy day and on the next day.
        approved = demo_bought()["approved"]
        fp = [r for r in read_json(approved, os.path.join("state", "broker.json"))["orders"]
              if r["status"] == "approved"][-1]["fingerprint"]
        self.assertEqual(fp, BUY_FP)
        args = [fp if w == "FP" else w for w in words[1:]]
        done = "Recorded the DEMO buy of Thu 2026-10-29 as expired: the order ended at the close without a fill."
        for at in ("2026-10-29 15:45", "2026-10-30 16:15"):
            self.step(copy_project(approved), "psx-broker/ticket.py", *(args + ["--now", at]), has=[done])
        # --index 1 does not reach the buy on a later day, nor once another order is in psx/orders.json
        self.step(copy_project(approved), "psx-broker/ticket.py", "result", "--index", "1", "--status", "expired",
                  "--now", "2026-10-30 16:15", code=1, has=["ticket.py result --fingerprint FP --status expired"])
        other = copy_project(approved)
        doc = read_json(other, "orders.json")
        doc["orders"][0]["qty"] = 100                              # a new order: another fingerprint
        write_doc(os.path.join(other, "psx", "orders.json"), doc)
        self.step(other, "psx-broker/ticket.py", "result", "--index", "1", "--status", "expired", "--now",
                  "2026-10-29 15:45", code=1, has=["order 1 has no yes recorded today"])
        self.step(other, "psx-broker/ticket.py", *(args + ["--now", "2026-10-29 15:45"]), has=[done])
        # the row as it was: it names no order, so it stops
        self.step(copy_project(approved), "psx-broker/ticket.py", "result", "--status", "expired", "--now",
                  "2026-10-29 15:45", code=2, has=["the following arguments are required: --index"])

    def test_the_long_term_words_match_invest(self):
        # review F43, F48: the folder guard (--invest too), and paper with real holdings: the journal, the TRIMs
        # it saves, and the --real fill line
        text = readme_text()
        part = readme_part(text, "## Long-term signals (psx-invest)")
        for words in ("Without it the tool stops. `--invest` can name a rules file in another folder, but then every "
                      "command that writes still stops. So it never writes in the trading folder by mistake.",
                      "It writes its journal: what the rules would do. It also saves each TRIM it shows in "
                      "`psx/state/invest.json`. Then a sale you make later counts toward that TRIM, and the next run "
                      "asks only for the rest.",
                      "In that case, record each fill you make in your own app with `--real`: `ticket.py fill --symbol "
                      "ZCAP --side sell --qty Q --price P --real`. Q and P are the shares and the price your app "
                      "shows. `invest.py orders` prints this line for you."):
            self.assertIn(words, part)
        self.assertNotIn("It writes only its journal", flat(text))
        self.assertIn("python3 psx-invest/invest.py backfill --forget-no-session", text)
        # the tools: a folder with no psx/invest.json (like the trading folder) and the fixture's rules elsewhere
        t = lt_project()
        os.remove(os.path.join(t, "psx", "invest.json"))
        rules = os.path.join(INVEST, "invest.json")
        names = sorted(os.listdir(os.path.join(t, "psx")))
        book = read_bytes(os.path.join(t, "psx", "portfolio.json"))
        self.step(t, "psx-invest/invest.py", "signals", "--now", LT_EVENING, code=2,
                  has=["No psx/invest.json: copy psx-invest/invest.example.json to psx/invest.json first"])
        for cmd in (("signals", "--write"), ("orders", "--write"), ("cash", "--add", "100"), ("ack", "ZOLD", "--keep"),
                    ("backfill", "--forget-no-session")):
            self.step(t, "psx-invest/invest.py", *(cmd + ("--invest", rules, "--now", LT_SESSION)), code=2,
                      has=["This folder has no psx/invest.json, so psx-invest writes nothing here."])
        self.step(t, "psx-invest/invest.py", "signals", "--invest", rules, "--now", LT_EVENING,
                  has=["Note: the rules come from %s, not from psx/invest.json in this folder." % rules])
        self.assertEqual(sorted(os.listdir(os.path.join(t, "psx"))), names)     # no state, journal or orders
        self.assertEqual(read_bytes(os.path.join(t, "psx", "portfolio.json")), book)
        # paper and "real_holdings": true: no psx/orders.json, the TRIM saved once, the --real line
        r = lt_project(real_holdings=True)
        self.step(r, "psx-invest/invest.py", "orders", "--write", "--now", LT_SESSION,
                  has=["Could sell 778 ZCAP at 91.55",
                       "Note: 1 new TRIM is saved in psx/state/invest.json, once. After a sale, the next run asks only "
                       "for the rest.",
                       "Paper mode and real holdings: nothing was written to psx/orders.json.",
                       "record each fill: ticket.py fill --symbol ZCAP --side sell --qty Q --price P --real"])
        self.assertFalse(os.path.exists(os.path.join(r, "psx", "orders.json")))
        trims = read_json(r, os.path.join("state", "invest.json"))["trims"]
        self.assertEqual([(x["amount"], x["closed"]) for x in trims["ZCAP"]], [(778, None)])
        # a sale of 400 in the app, recorded with --real: the next run asks only for the other 378
        self.step(r, "psx-broker/ticket.py", "fill", "--symbol", "ZCAP", "--side", "sell", "--qty", "400", "--price",
                  "91.55", "--date", "2026-11-02", "--real", "--now", "2026-11-02 11:00",
                  has=["Recorded sell 400 ZCAP at 91.55 on 2026-11-02"])
        self.step(r, "psx-invest/invest.py", "orders", "--now", "2026-11-02 11:05", has=["Could sell 378 ZCAP at 91.55"],
                  not_has=["Could sell 778 ZCAP"])
        # the README's backfill line: it clears only the list of days with no session, and asks PSX for nothing
        self.step(r, "psx-invest/invest.py", "backfill", "--forget-no-session", "--now", "2026-11-02 11:10",
                  has=["Nothing else changed."])


if __name__ == "__main__":
    unittest.main()
