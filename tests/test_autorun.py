"""Tests for psx-autorun: autorun.py, run.sh, install.sh, the app script and the launchd templates.

Offline: no PSX site, no launchd, no notifications, no caffeinate (the runner's Mac tools are swapped
for fakes that fail the test if they are called). Every file a test writes goes into a temp folder:
PSX_AUTORUN_HOME and PSX_AUTORUN_LOGS point there. The pack's tools are made-up scripts in a temp
folder. A few tests ask the pack's own clock (read only: it prints JSON)."""

import contextlib
import datetime
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from _load import FIX, ROOT, load
from _words import check_words

A = load("psx-autorun", "autorun")
HERE = os.path.join(ROOT, "psx-autorun")
CONTRACT = os.path.join(FIX, "psx-v2", "contract")
HAVE_FCNTL = importlib.util.find_spec("fcntl") is not None
needs_lock = unittest.skipUnless(HAVE_FCNTL, "no fcntl on this system: the lock needs it")
NORMAL = [["09:30", "15:30"]]
FRIDAY = [["09:15", "12:00"], ["14:30", "16:30"]]
RAMADAN = [["09:15", "13:30"]]
RAMADAN_FRIDAY = [["09:15", "12:30"]]
HOLIDAYS = {"2026-11-09": "PSX holiday: Iqbal Day"}
EXPECTED = {"2027-03-05": "PSX holiday: Juma-tul-Wida (expected, check PSX notices)"}
RAMADAN_DAYS = {"2027-02-10", "2027-03-04"}
RAMADAN_FRIDAYS = {"2027-02-12"}
EXTRA_CLOSED = {}          # a test may close more days for a while
MAC_HOOKS = ("run_osascript", "start_caffeinate", "caffeinate_while", "kickstart", "open_file")
FOOTER = ("A setup, an alert or a signal is a rule that matched. It is not advice. Nothing was bought or sold. "
          "Auto-run never places an order.")
STOP_RING = ("DEMO traded at or under your stop 96.25 on Fri 2026-11-20: the low in that day's closing file was "
             "95.90 (official close 96.40). Your plan's stop rule is met. You could sell: psx-broker flatten "
             "--symbol DEMO, then psx-risk and your yes. You decide.")
STALE = ("DEMO: no price newer than 15 minutes (newest 10:05 AM PKT). The swing stop was not checked. Check the "
         "price in your broker app.")
_SANDBOX = {}


def forbidden(name):
    def call(*a, **k):
        raise AssertionError("a test called %s: the tests use fakes only" % name)
    return call


def setUpModule():
    """Auto-run's own files (state, logs) go into a temp folder, never into ~/Library. No Mac tool runs."""
    _SANDBOX["dir"] = tempfile.mkdtemp(prefix="psx-autorun-test-")
    _SANDBOX["env"] = dict(os.environ)
    _SANDBOX["hooks"] = dict((k, getattr(A, k)) for k in MAC_HOOKS + ("other_pollers", "SLEEP", "ask_clock"))
    os.environ["PSX_AUTORUN_HOME"] = os.path.join(_SANDBOX["dir"], "home")
    os.environ["PSX_AUTORUN_LOGS"] = os.path.join(_SANDBOX["dir"], "logs")
    os.environ["PSX_NOTIFY"] = "applet"
    for k in MAC_HOOKS:
        setattr(A, k, forbidden(k))
    A.other_pollers = lambda: []        # a real tape may be running on this Mac
    A.SLEEP = lambda s: None


def tearDownModule():
    for k, v in _SANDBOX["hooks"].items():
        setattr(A, k, v)
    os.environ.clear()
    os.environ.update(_SANDBOX["env"])
    shutil.rmtree(_SANDBOX["dir"], ignore_errors=True)


def fake_clock(cfg, d):
    """The pack clock's answer for day d, made up from the tables above (no subprocess)."""
    key = d.isoformat()
    base = {"segments": [], "session": None, "reason": "", "calendar_known": d.year < 2028, "calendar_note": ""}
    if d.weekday() >= 5:
        return dict(base, reason="weekend")
    if key in HOLIDAYS or key in EXTRA_CLOSED:
        return dict(base, reason=HOLIDAYS.get(key) or EXTRA_CLOSED[key])
    if key in EXPECTED:
        return dict(base, reason=EXPECTED[key], calendar_note="%s is an expected PSX holiday (Juma-tul-Wida): "
                                                              "moon dates can move, so check PSX notices." % key)
    if key in RAMADAN_FRIDAYS:
        return dict(base, segments=RAMADAN_FRIDAY, session="ramadan-friday")
    if key in RAMADAN_DAYS:
        return dict(base, segments=RAMADAN, session="ramadan")
    if d.weekday() == 4:
        return dict(base, segments=FRIDAY, session="friday")
    return dict(base, segments=NORMAL, session="normal")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def contract(name):
    with open(os.path.join(CONTRACT, name), encoding="utf-8") as fh:
        return json.load(fh)


def T(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")


def D(text):
    return datetime.date.fromisoformat(text)


def message_lines(out):
    """The notify lines the app gets -> [(title, [text lines], sound)]."""
    found = []
    for line in out.splitlines():
        if line.startswith("notify\t"):
            _, title, text, sound = line.split("\t")
            found.append((title, text.split(A.BREAK), sound))
    return found


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        for name in ("trading", "longterm"):
            os.makedirs(os.path.join(self.tmp, name, "psx"))
        self.cfg = A.deep_merge(A.DEFAULTS, {"folders": {"trading": os.path.join(self.tmp, "trading"),
                                                         "longterm": os.path.join(self.tmp, "longterm")}})
        self.state = {"installed": "2026-10-01 08:00"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def psx(self, folder, *parts):
        return os.path.join(self.cfg["folders"][folder], "psx", *parts)

    def due_at(self, text, state=None):
        now = T(text)
        state = self.state if state is None else state
        info = A.day_info(self.cfg, state, now.date(), fake_clock)
        prev, prev_info = A.last_trading_day(self.cfg, state, now.date(), fake_clock)
        return [(j, d.isoformat(), n) for j, d, n in A.due(self.cfg, state, now, info, prev, prev_info)]

    def finish(self, job, day, at="2026-01-01 00:00"):
        A.mark(self.state, job, D(day), at)

    def plan(self, segs, day):
        return A.day_plan(self.cfg, {"segments": segs}, D(day))


class TestPlan(Base):
    def test_normal_day_times(self):
        p = self.plan(NORMAL, "2026-11-02")
        self.assertEqual(p["morning"], T("2026-11-02 08:15"))
        self.assertEqual(p["checks"], [(T("2026-11-02 09:37"), T("2026-11-02 15:30"))])
        self.assertEqual(p["finals"], [T("2026-11-02 15:40")])
        self.assertEqual(p["after_close"], T("2026-11-02 16:10"))
        self.assertEqual(p["give_up"], T("2026-11-02 19:30"))

    def test_friday_two_parts(self):
        p = self.plan(FRIDAY, "2026-11-06")
        self.assertEqual(p["morning"], T("2026-11-06 08:00"))
        self.assertEqual(p["checks"], [(T("2026-11-06 09:22"), T("2026-11-06 12:00")),
                                       (T("2026-11-06 14:37"), T("2026-11-06 16:30"))])
        self.assertEqual(p["finals"], [T("2026-11-06 12:10"), T("2026-11-06 16:40")])
        self.assertEqual((p["after_close"], p["give_up"]), (T("2026-11-06 17:10"), T("2026-11-06 20:30")))

    def test_ramadan_monday_to_thursday(self):
        p = self.plan(RAMADAN, "2027-02-10")
        self.assertEqual((p["morning"], p["checks"], p["finals"], p["after_close"]),
                         (T("2027-02-10 08:00"), [(T("2027-02-10 09:22"), T("2027-02-10 13:30"))],
                          [T("2027-02-10 13:40")], T("2027-02-10 14:10")))

    def test_ramadan_friday(self):
        p = self.plan(RAMADAN_FRIDAY, "2027-02-12")
        self.assertEqual((p["morning"], p["checks"], p["finals"], p["after_close"]),
                         (T("2027-02-12 08:00"), [(T("2027-02-12 09:22"), T("2027-02-12 12:30"))],
                          [T("2027-02-12 12:40")], T("2027-02-12 13:10")))

    def test_closed_day_and_its_words(self):
        self.assertIsNone(self.plan([], "2026-11-09"))
        lines = A.plan_lines(self.cfg, None, D("2026-11-09"), {"reason": "PSX holiday: Iqbal Day"})
        self.assertEqual(lines, ["PSX is closed on Monday 9 November 2026 (PSX holiday: Iqbal Day). No market jobs."])

    def test_plan_lines_friday(self):
        text = "\n".join(A.plan_lines(self.cfg, self.plan(FRIDAY, "2026-11-06"), D("2026-11-06")))
        self.assertIn("8:00 AM  morning check", text)
        # the checks may start at 9:22, but launchd ticks at minute 0, 5, 10 ...: the first check is at 9:25
        self.assertIn("9:25 AM to 12:00 PM  price checks every 5 minutes", text)
        self.assertIn("2:40 PM to 4:30 PM  price checks every 5 minutes", text)
        self.assertIn("12:10 PM  last check of this part of the day", text)
        self.assertIn("4:40 PM  last check of the day", text)
        self.assertIn("5:10 PM  after-close check", text)

    def test_an_expected_holiday_gets_the_look_in_the_plan(self):
        info = fake_clock(self.cfg, D("2027-03-05"))
        self.assertEqual(A.expected_holiday(info), "Juma-tul-Wida")
        lines = A.plan_lines(self.cfg, None, D("2027-03-05"), info)
        self.assertIn("10:00 AM  one look at the PSX site", lines[1])
        self.assertEqual(A.expected_holiday(fake_clock(self.cfg, D("2026-11-09"))), "")


class TestDue(Base):
    def setUp(self):
        Base.setUp(self)
        EXTRA_CLOSED.clear()

    def test_monday_morning(self):
        self.finish("after_close", "2026-10-30")
        self.finish("weekly", "2026-10-31")
        self.assertEqual(self.due_at("2026-11-02 08:14"), [])
        self.assertEqual(self.due_at("2026-11-02 08:15"), [("morning", "2026-11-02", "")])

    def test_checks_every_five_minutes(self):
        for job, day in (("after_close", "2026-10-30"), ("weekly", "2026-10-31"), ("morning", "2026-11-02")):
            self.finish(job, day)
        self.assertEqual(self.due_at("2026-11-02 09:36"), [])
        self.assertEqual(self.due_at("2026-11-02 09:37"), [("check", "2026-11-02", "")])
        self.state["last_check"] = "2026-11-02 09:37"
        self.assertEqual(self.due_at("2026-11-02 09:40"), [])
        self.assertEqual(self.due_at("2026-11-02 09:42"), [("check", "2026-11-02", "")])

    def test_final_check_and_after_close_retries(self):
        for job, day in (("after_close", "2026-10-30"), ("weekly", "2026-10-31"), ("morning", "2026-11-02")):
            self.finish(job, day)
        self.state["last_check"] = "2026-11-02 15:29"
        self.assertEqual(self.due_at("2026-11-02 15:35"), [])
        self.assertEqual(self.due_at("2026-11-02 15:40"), [("check", "2026-11-02", "final0")])
        self.finish("final0", "2026-11-02")
        self.assertEqual(self.due_at("2026-11-02 16:09"), [])
        self.assertEqual(self.due_at("2026-11-02 16:10"), [("after_close", "2026-11-02", "")])
        self.state["waiting"] = {"2026-11-02": "2026-11-02 16:25"}
        self.assertEqual(self.due_at("2026-11-02 16:20"), [])
        self.assertEqual(self.due_at("2026-11-02 16:25"), [("after_close", "2026-11-02", "")])

    def test_friday_and_the_juma_break(self):
        self.finish("after_close", "2026-11-05")
        self.finish("weekly", "2026-10-31")
        self.assertEqual(self.due_at("2026-11-06 08:00"), [("morning", "2026-11-06", "")])
        self.finish("morning", "2026-11-06")
        self.assertEqual(self.due_at("2026-11-06 09:22"), [("check", "2026-11-06", "")])
        self.state["last_check"] = "2026-11-06 11:58"
        self.assertEqual(self.due_at("2026-11-06 12:10"), [("check", "2026-11-06", "final0")])
        self.finish("final0", "2026-11-06")
        self.assertEqual(self.due_at("2026-11-06 13:00"), [])        # the Juma break: nothing
        self.assertEqual(self.due_at("2026-11-06 14:37"), [("check", "2026-11-06", "")])
        self.assertEqual(self.due_at("2026-11-06 17:10")[-1], ("after_close", "2026-11-06", ""))

    def test_holiday_and_weekend(self):
        self.finish("after_close", "2026-11-06")
        self.finish("weekly", "2026-11-07")
        self.assertEqual(self.due_at("2026-11-08 10:00"), [])                       # Sunday
        self.assertEqual(self.due_at("2026-11-09 08:15"), [])                       # Iqbal Day
        self.assertEqual(self.due_at("2026-11-09 16:10"), [])

    def test_missed_after_close_runs_next_morning_first(self):
        self.finish("weekly", "2026-11-07")
        # Friday's after-close never ran (the Mac was off). Monday was a holiday.
        self.assertEqual(self.due_at("2026-11-10 08:15"),
                         [("after_close", "2026-11-06", "late"), ("morning", "2026-11-10", "")])

    def test_catch_up_waits_for_the_close(self):
        self.finish("weekly", "2026-10-31")
        self.finish("morning", "2026-11-03")
        self.state["last_check"] = "2026-11-03 11:00"
        got = self.due_at("2026-11-03 11:02")          # Monday's after-close was missed; now it is Tuesday 11:02
        self.assertNotIn(("after_close", "2026-11-02", "late"), got)
        self.assertIn(("after_close", "2026-11-02", "late"), self.due_at("2026-11-03 15:50"))

    def test_catch_up_goes_at_most_four_days_back(self):
        for day in ("2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"):
            EXTRA_CLOSED[day] = "PSX holiday: a made-up closure"
        self.finish("weekly", "2026-11-14")
        got = self.due_at("2026-11-20 08:00")          # the last session was Fri 11-13: 7 days back
        self.assertEqual(got, [("morning", "2026-11-20", "")])
        EXTRA_CLOSED.clear()

    def test_late_morning_and_no_morning_after_close(self):
        self.finish("after_close", "2026-10-30")
        self.finish("weekly", "2026-10-31")
        self.assertEqual(self.due_at("2026-11-02 11:00")[0], ("morning", "2026-11-02", "late"))
        self.assertEqual(self.due_at("2026-11-02 17:00"), [("after_close", "2026-11-02", "")])

    def test_weekly(self):
        self.finish("after_close", "2026-11-06")
        self.finish("weekly", "2026-10-31")
        self.assertEqual(self.due_at("2026-11-07 09:59"), [])
        self.assertEqual(self.due_at("2026-11-07 10:00"), [("weekly", "2026-11-07", "")])
        self.assertEqual(self.due_at("2026-11-08 18:00"), [("weekly", "2026-11-07", "late")])
        self.finish("morning", "2026-11-10")       # missed all weekend: not in the session, but after the close
        self.state["last_check"] = "2026-11-10 09:58"
        self.assertNotIn("weekly", [j for j, _, _ in self.due_at("2026-11-10 10:00")])
        self.finish("after_close", "2026-11-10")
        self.assertIn(("weekly", "2026-11-07", "late"), self.due_at("2026-11-10 16:30"))

    def test_nothing_from_before_the_install(self):
        self.state = {"installed": "2026-11-04 10:00"}
        got = self.due_at("2026-11-04 16:10")       # no catch-up for Tuesday, no weekly for Oct 31
        self.assertEqual(got, [("check", "2026-11-04", "final0"), ("after_close", "2026-11-04", "")])

    def test_pause_all_and_pause_checks(self):
        self.finish("after_close", "2026-10-30")
        self.finish("weekly", "2026-10-31")
        self.state["pause"] = {"all": True}
        self.assertEqual(self.due_at("2026-11-02 08:15"), [])
        self.state["pause"] = {"checks_off": "2026-11-02"}
        self.finish("morning", "2026-11-02")
        self.assertEqual(self.due_at("2026-11-02 10:00"), [])
        self.assertEqual(self.due_at("2026-11-03 10:00")[-1], ("check", "2026-11-03", ""))

    def test_pause_all_for_ten_minutes_ends_by_itself(self):
        self.finish("after_close", "2026-10-30")
        self.finish("weekly", "2026-10-31")
        self.state["pause"] = {"all": True, "until": "2026-11-02 08:25"}
        self.assertEqual(self.due_at("2026-11-02 08:24"), [])
        self.assertFalse(A.end_pause(self.state, T("2026-11-02 08:24")))
        self.assertEqual(self.due_at("2026-11-02 08:25"), [("morning", "2026-11-02", "")])
        self.assertTrue(A.end_pause(self.state, T("2026-11-02 08:25")))
        self.assertNotIn("pause", self.state)

    def test_ramadan_day(self):
        self.finish("after_close", "2027-02-09")
        self.finish("weekly", "2027-02-06")
        self.assertEqual(self.due_at("2027-02-10 08:00"), [("morning", "2027-02-10", "")])
        self.finish("morning", "2027-02-10")
        self.assertEqual(self.due_at("2027-02-10 14:10")[-1], ("after_close", "2027-02-10", ""))

    def test_the_plan_says_when_the_first_check_really_runs(self):
        # launchd ticks at minute 0, 5, 10 ...: walk those ticks through due() and compare with the plan's words
        for day, segs, before in (("2026-11-06", FRIDAY, "2026-11-05"), ("2026-11-05", NORMAL, "2026-11-04"),
                                  ("2027-02-10", RAMADAN, "2027-02-09")):
            self.state = {"installed": "2026-10-01 08:00"}
            self.finish("after_close", before)
            self.finish("morning", day)
            self.finish("weekly", A.weekly_slot(self.cfg, T(day + " 08:00")).date().isoformat())
            starts, last = [], None
            t = T(day + " 08:00")
            while t < T(day + " 17:00"):
                if ("check", day, "") in self.due_at(t.strftime("%Y-%m-%d %H:%M")):
                    if last is None or t - last > datetime.timedelta(minutes=5):
                        starts.append(A.clock_label(t))
                    last = t
                    self.state["last_check"] = t.strftime("%Y-%m-%d %H:%M")
                t += datetime.timedelta(minutes=5)
            lines = A.plan_lines(self.cfg, self.plan(segs, day), D(day))
            said = [x.split(" to ")[0] for x in lines if "price checks every" in x]
            self.assertEqual(said, starts, day)

    def test_an_expected_holiday_gets_one_look_and_no_checks(self):
        self.finish("after_close", "2027-03-04")
        self.finish("weekly", "2027-02-27")
        self.assertEqual(self.due_at("2027-03-05 09:59"), [])          # no morning job and no checks
        self.assertEqual(self.due_at("2027-03-05 10:00"), [("probe", "2027-03-05", "")])
        self.state["pause"] = {"checks_off": "2027-03-05"}
        self.assertEqual(self.due_at("2027-03-05 10:05"), [])
        self.state.pop("pause")
        self.finish("probe", "2027-03-05")
        self.assertEqual(self.due_at("2027-03-05 10:05"), [])
        self.assertEqual(self.due_at("2027-03-05 11:00"), [])


class TestWeeklySlot(Base):
    def test_slot(self):
        self.assertEqual(A.weekly_slot(self.cfg, T("2026-11-07 10:00")), T("2026-11-07 10:00"))
        self.assertEqual(A.weekly_slot(self.cfg, T("2026-11-07 09:59")), T("2026-10-31 10:00"))
        self.assertEqual(A.weekly_slot(self.cfg, T("2026-11-10 08:00")), T("2026-11-07 10:00"))


class TestClockCache(Base):
    def counting_clock(self):
        calls = []

        def clock(cfg, d):
            calls.append(d.isoformat())
            return fake_clock(cfg, d)
        return clock, calls

    def test_the_cache_is_by_date(self):
        clock, calls = self.counting_clock()
        A.day_info(self.cfg, self.state, D("2026-11-06"), clock)
        info = A.day_info(self.cfg, self.state, D("2026-11-06"), clock)
        self.assertEqual(calls, ["2026-11-06"])
        self.assertEqual(info["segments"], FRIDAY)

    def test_a_changed_config_with_the_same_time_asks_again(self):
        clock, calls = self.counting_clock()
        path = self.psx("trading", "config.json")
        with open(path, "w") as fh:
            fh.write('{"holidays": {"2026-11-06": "a"}}')
        os.utime(path, (1700000000, 1700000000))
        A.day_info(self.cfg, self.state, D("2026-11-06"), clock)
        with open(path, "w") as fh:          # new bytes, the same length and the same file time
            fh.write('{"holidays": {"2026-11-06": "b"}}')
        os.utime(path, (1700000000, 1700000000))
        A.day_info(self.cfg, self.state, D("2026-11-06"), clock)
        self.assertEqual(calls, ["2026-11-06", "2026-11-06"])
        A.day_info(self.cfg, self.state, D("2026-11-06"), clock)      # nothing new: the cache answers
        self.assertEqual(len(calls), 2)

    def test_the_cache_keeps_40_days(self):
        clock, calls = self.counting_clock()
        for i in range(45):
            A.day_info(self.cfg, self.state, D("2026-11-01") + datetime.timedelta(days=i), clock)
        self.assertEqual(len(self.state["clock"]), 40)
        self.assertNotIn("2026-11-01", self.state["clock"])


class TestClassify(unittest.TestCase):
    S = A.step("x", "trading", "psx-scanner/scan.py", kind="scan")

    def test_ok_and_nothing(self):
        self.assertEqual(A.classify(self.S, 0, "hits", ""), ("ok", ""))
        self.assertEqual(A.classify(self.S, 1, "Daily scan: no setups", ""), ("ok", ""))

    def test_tool_error_on_exit_1(self):
        st, why = A.classify(self.S, 1, "", "scan.py: no closing files in psx/data/mkt_summary")
        self.assertEqual(st, "failed")
        self.assertIn("no closing files", why)

    def test_quiet_words_are_nothing_to_check(self):
        s = A.news_step("n", "trading", [], "News")
        self.assertEqual(A.classify(s, 2, "", "news: no tickers. Pass --tickers")[0], "skipped")
        s = A.alerts_step("a", "trading", "Alerts")
        self.assertEqual(A.classify(s, 1, "", "alerts.py: no rules in psx/alerts.json")[0], "skipped")
        s = A.insiders_step("i", "trading", "Insiders")
        self.assertEqual(A.classify(s, 1, "No symbols to check: pass --symbols DEMO", "")[0], "skipped")

    def test_alerts_exit_2(self):
        s = A.alerts_step("a", "longterm", "Alerts")
        st, why = A.classify(s, 2, "PSX OPEN\nerror: SYS: not on the screener with a price\n", "")
        self.assertEqual((st, why), ("failed", "SYS: not on the screener with a price"))

    def test_crash_block_timeout(self):
        tb = "Traceback (most recent call last):\n  File x\nKeyError: 'hits'\n"
        self.assertEqual(A.classify(self.S, 1, "", tb), ("failed", "the tool crashed: KeyError: 'hits'"))
        self.assertIn("macOS blocked", A.classify(self.S, 1, "", "[Errno 1] Operation not permitted: 'x'")[1])
        self.assertEqual(A.classify(self.S, None, "", "", timed_out=True)[0], "failed")


class TestSummaries(Base):
    NEWS = textwrap.dedent("""\
        DEMO     100.65 +2.29% vs the last close 98.40 at 10:15 AM PKT
        PSX notices (PSX gives a date, not a time):
        ! 2026-10-28  DEMO    Material Information
              tags: material  https://dps.psx.com.pk/download/document/1.pdf
        Headlines:
          2026-10-29 09:12 PKT  DEMO        Dawn               Demo Industries wins a made-up order
              https://example.org/news/1
        Checked 2026-10-29 10:15 PKT. Headlines are leads, not facts.
        """)

    def res(self, out="", rc=0):
        return {"status": "ok", "why": "", "out": out, "err": "", "rc": rc}

    def test_news_count(self):
        s = A.news_step("n", "trading", [], "News")
        self.assertEqual(A.summarize(s, self.res(self.NEWS), self.cfg["folders"]["trading"]), "News: 2 items to read.")
        self.assertEqual(A.summarize(s, self.res(self.NEWS, 1), self.cfg["folders"]["trading"]), "News: nothing new.")

    def test_scan_reads_the_watchlist(self):
        write_json(self.psx("trading", "watchlist.json"),
                   {"hits": [{"symbol": s} for s in ("DEMO", "AAA", "BBB", "CCC", "DDD", "EEE")]})
        s = A.step("scan", "trading", "psx-scanner/scan.py", kind="scan", title="Daily scan")
        self.assertEqual(A.summarize(s, self.res(), self.cfg["folders"]["trading"]),
                         "Daily scan: 6 setups: DEMO, AAA, BBB, CCC and 2 more.")

    def test_scan_splits_off_the_blocked_list(self):
        write_json(self.psx("trading", "watchlist.json"), {"hits": [{"symbol": s} for s in ("FFC", "DEMO", "LUCK")]})
        write_json(self.psx("trading", "config.json"), {"blocked": ["FFC", "LUCK"]})
        s = A.step("scan", "trading", "psx-scanner/scan.py", kind="scan", title="Daily scan")
        r = self.res()
        self.assertEqual(A.summarize(s, r, self.cfg["folders"]["trading"]),
                         "Daily scan: 1 setup: DEMO. Also matched, but on your blocked list (long-term shares): "
                         "FFC, LUCK.")
        self.assertEqual(r["count"], 1)
        # the v2 scanner leaves them out itself and lists them in "blocked_left_out"
        write_json(self.psx("trading", "watchlist.json"), {"hits": [{"symbol": "DEMO"}], "blocked_left_out": ["OGDC"]})
        self.assertEqual(A.summarize(s, self.res(), self.cfg["folders"]["trading"]),
                         "Daily scan: 1 setup: DEMO. Also matched, but on your blocked list (long-term shares): OGDC.")

    def test_halt_tag(self):
        text = "Headlines:\n  2026-03-09 09:30 PKT  market      Mettis   Trading halted at PSX\n" \
               "      tags: halt, macro  https://example.org/1\n"
        self.assertTrue(A.HALT_RE.search(text))
        self.assertFalse(A.HALT_RE.search(self.NEWS))

    def test_invest_signals(self):
        out = textwrap.dedent("""\
            Long-term signals, Fri 2026-10-30 5:10 PM PKT. Closes to Fri 2026-10-30.
            ADD     ZUP    5.0%  no vote down, 12-month up (+14.2%)
            HOLD    ZINS  15.9%  trend up, 12-month up
            TRIM    ZCAP  27.1%  over the 25% trim line
            TRIM    ZBRK  12.0%  break: both votes turned down
            REVIEW  ZOLD   8.0%  both votes down
            notify: ZBRK: a sell rule is met (TRIM). Open the report. You decide.
            """)
        s = A.signals_step("Long-term signals")
        r = self.res(out)
        self.assertEqual(A.summarize(s, r, ""), "Long-term signals: TRIM: ZCAP, ZBRK. REVIEW: ZOLD. ADD: ZUP. "
                                                "HOLD: 1. Rules, not predictions.")
        self.assertEqual(r["count"], 4)
        self.assertIn("Long-term signals to read: 4.", A.notify_text("after_close", [(s, r)], []))

    def test_alerts_count_and_a_summary_line(self):
        s = A.alerts_step("a", "longterm", "Alerts")
        self.assertEqual(A.summarize(s, self.res("PSX OPEN\nALERT SYS 105.00 is at or below 105.05\n"), ""),
                         "Alerts: 1 rang.")
        self.assertEqual(A.summarize(s, self.res("PSX OPEN\nNothing hit. 1 rule(s) watched today.\n", 1), ""),
                         "Alerts: no new alert rang (1 rule watched).")

    def held_by_hand(self):
        """A made-up day folder: 20 UBL in the ledger with no plan, and rules written by hand in psx/alerts.json."""
        write_json(self.psx("trading", "portfolio.json"), {"cash": 100, "positions": [{"ticker": "UBL", "shares": 20}]})
        write_json(self.psx("trading", "alerts.json"), {"rules": [
            {"id": "ubl-stop-375.00", "type": "stop", "symbol": "UBL", "price": 375.0},
            {"id": "ubl-target-410.00", "type": "target", "symbol": "UBL", "price": 410.0},
            {"id": "time-1500", "type": "time", "at": "15:00", "date": "2026-11-27"}]})
        write_json(self.psx("trading", "state", "alerts.json"), {"rung": {"ubl-stop-375.00": "2026-11-12T05:05:00Z"}})
        return A.alerts_step("a", "trading", "Alerts on the close (trading folder)"), self.cfg["folders"]["trading"]

    def test_a_stop_that_rang_and_is_still_held_never_reads_nothing_hit(self):
        s, folder = self.held_by_hand()  # the stop rang at 10:05 AM; UBL closed under it, and the shares are held
        out = "PSX closed.\nNothing hit. 2 rule(s) watched today, UBL 372.00 (as of 3:30 PM PKT).\n"
        line = A.summarize(s, self.res(out, 1), folder, D("2026-11-12"))
        self.assertEqual(line, "Alerts on the close (trading folder): no new alert rang (2 rules watched). Rang, and "
                               "you still hold the share: UBL stop 375.00 (rang Thu 2026-11-12; UBL 372.00 as of "
                               "3:30 PM PKT). Such a rule rings once, then stays quiet until you reset it.")
        self.assertNotIn("nothing hit", line.lower())
        check_words(self, line)
        # the next morning (or a late job after a sleep): the data is from the day before, so no rule used it
        out = ("PSX closed.\nNothing hit. 2 rule(s) watched today, UBL 372.00 (as of Thu 2026-11-12 3:30 PM PKT).\n"
               "note: UBL: the newest data is from Thu 2026-11-12 3:30 PM PKT, not today, so no alert used it.\n")
        line = A.summarize(s, self.res(out, 1), folder, D("2026-11-13"))
        self.assertTrue(line.endswith("UBL has no price from today, so its rules were not checked."), line)
        # sold: nothing is still held, so nothing is named
        write_json(self.psx("trading", "portfolio.json"), {"cash": 100, "positions": []})
        self.assertEqual(A.summarize(s, self.res(out, 1), folder, D("2026-11-13")),
                         "Alerts on the close (trading folder): no new alert rang (2 rules watched). UBL has no price "
                         "from today, so its rules were not checked.")

    def test_a_met_swing_rule_of_a_held_share_is_named(self):
        write_json(self.psx("trading", "portfolio.json"), contract("portfolio-swing.json"))
        write_json(self.psx("trading", "state", "alerts.json"),
                   {"swing_met": {"DEMO-2026-10-29": {"stop": "2026-11-02"}}, "rung": {}})
        s = A.alerts_step("a", "trading", "Alerts on the close (trading folder)")
        out = ("Nothing hit. 3 rule(s) watched today, DEMO 96.00 (as of 3:30 PM PKT).\n"
               "note: DEMO: the data is 40 minutes old (as of 2:50 PM PKT). Save a new page, or check the price in "
               "your broker app.\n")
        line = A.summarize(s, self.res(out, 1), self.cfg["folders"]["trading"], D("2026-11-03"))
        self.assertEqual(line, "Alerts on the close (trading folder): no new alert rang (3 rules watched). Met, and you "
                               "still hold the share: DEMO stop rule (met Mon 2026-11-02). Old prices: DEMO 40 minutes "
                               "old.")
        check_words(self, line)

    MET = ("DEMO: the stop rule was met on Mon 2026-11-02. It rang today at 10:00 AM PKT. You still hold 1,436 "
           "shares in psx/portfolio.json.")

    def test_a_no_new_alert_line_keeps_the_count_and_names_each_met_rule(self):
        # F13: psx-alerts says "No new alert." (never "Nothing hit.") while a swing rule of a held share is met,
        # then one line for each met rule. The runner keeps its rule count and its prices, and names each rule.
        write_json(self.psx("trading", "portfolio.json"), contract("portfolio-swing.json"))
        write_json(self.psx("trading", "alerts.json"), {"rules": [
            {"id": "demo-hand-stop", "type": "stop", "symbol": "DEMO", "price": 97.0}]})
        write_json(self.psx("trading", "state", "alerts.json"),
                   {"swing_met": {"DEMO-2026-10-29": {"stop": "2026-11-02"}},
                    "rung": {"demo-hand-stop": "2026-11-02T05:00:00Z"}})
        s = A.alerts_step("a", "trading", "Alerts on the close (trading folder)")
        folder = self.cfg["folders"]["trading"]
        out = ("PSX closed (2026-11-02 16:10 PKT, after the close).\n"
               "No new alert. 3 rule(s) watched today, DEMO 95.60 (as of 3:30 PM PKT).\n" + self.MET + "\n"
               "note: DEMO: checked the closing file of Mon 2026-11-02.\n")
        r = self.res(out, 1)
        line = A.summarize(s, r, folder, D("2026-11-02"))
        self.assertEqual(line, "Alerts on the close (trading folder): no new alert rang (3 rules watched). A swing "
                               "rule is still met. " + self.MET + " Rang, and you still hold the share: DEMO stop "
                               "97.00 (rang Mon 2026-11-02; DEMO 95.60 as of 3:30 PM PKT). Such a rule rings once, "
                               "then stays quiet until you reset it.")
        self.assertNotIn("nothing hit", line.lower())
        self.assertNotIn("Met, and you still hold", line)          # psx-alerts' own line, not a second one
        self.assertEqual((r["count"], r["met"]), (0, 1))
        text = A.notify_text("after_close", [(s, r)], [])
        self.assertEqual(text, "Swing rules still met: 1. News: nothing new. Problems: 0. Click to open the report.")
        check_words(self, line + "\n" + text)
        # two met rules (the time stop too), each in psx-alerts' own words
        time_stop = ("DEMO: the time stop is met: the exit-by day was Fri 2026-11-27. It rang today at 8:15 AM PKT. "
                     "You still hold 1,436 shares in psx/portfolio.json.")
        r = self.res(out.replace(self.MET, self.MET + "\n" + time_stop), 1)
        self.assertIn("(3 rules watched). 2 swing rules are still met. %s %s" % (self.MET, time_stop),
                      A.summarize(s, r, folder, D("2026-11-30")))
        self.assertEqual(r["met"], 2)
        # a ring: it is its own message, so no "still met" count
        r = self.res("PSX OPEN\nALERT " + STOP_RING + "\n")
        A.summarize(s, r, folder, D("2026-11-20"))
        self.assertEqual((r["count"], r["met"]), (1, 0))
        self.assertNotIn("still met", A.notify_text("after_close", [(s, r)], []))
        # "Nothing hit." has no rule lines after it: the met rule comes from psx-alerts' state file
        r = self.res(out.replace("No new alert.", "Nothing hit.").replace(self.MET + "\n", ""), 1)
        self.assertIn("(3 rules watched). Met, and you still hold the share: DEMO stop rule (met Mon 2026-11-02).",
                      A.summarize(s, r, folder, D("2026-11-02")))
        self.assertEqual(r["met"], 1)

    def test_a_swing_share_with_no_price_from_today_was_checked_on_the_closing_files(self):
        # psx-alerts reads every closing file for a swing share (D53), so "its rules were not checked" is only
        # for a share with no swing plan: its rules use today's prices only
        pf = contract("portfolio-swing.json")
        pf["positions"].append({"ticker": "UBL", "shares": 20})
        write_json(self.psx("trading", "portfolio.json"), pf)
        s = A.alerts_step("a", "trading", "Alerts (trading folder)")
        out = ("PSX closed.\nNothing hit. 3 rule(s) watched today, DEMO 99.00 (as of Thu 2026-11-12 3:30 PM PKT).\n"
               "note: DEMO: the newest data is from Thu 2026-11-12 3:30 PM PKT, not today, so no alert used it.\n"
               "note: UBL: the newest data is from Thu 2026-11-12 3:30 PM PKT, not today, so no alert used it.\n")
        line = A.summarize(s, self.res(out, 1), self.cfg["folders"]["trading"], D("2026-11-13"))
        self.assertEqual(line, "Alerts (trading folder): no new alert rang (3 rules watched). DEMO has no price "
                               "from today, so its swing rules were checked on the closing files only. UBL has no "
                               "price from today, so its rules were not checked.")
        check_words(self, line)

    def test_a_candidate_hold_is_counted_apart(self):
        # a HOLD row of a candidate you do not hold is not "keep your shares" (psx-invest marks it)
        out = textwrap.dedent("""\
            ADD     ZUP    5.0%  no vote down, 12-month up (+14.2%)
            HOLD    ZINS  15.9%  trend up, 12-month up
            HOLD    ZNEW   0.0%  not held, a candidate; trend not known, 12-month not known
            HOLD    ZOLD   0.0%  not held, a candidate; out since Fri 2026-07-31: no new money while out (rule 3)
            """)
        s = A.signals_step("Long-term signals")
        r = self.res(out)
        self.assertEqual(A.summarize(s, r, ""), "Long-term signals: ADD: ZUP. HOLD: 1 of your shares and 2 candidates. "
                                                "Rules, not predictions.")
        self.assertEqual(r["count"], 1)
        r = self.res(out.replace("HOLD    ZOLD", "ADD     ZOLD"))
        self.assertIn("ADD: ZUP, ZOLD. HOLD: 1 of your shares and 1 candidate.", A.summarize(s, r, ""))

    def test_news_says_how_many_feeds_did_not_answer(self):
        s = A.news_step("n", "trading", [], "News")
        out = self.NEWS + "note: feed dawn-business: could not reach https://www.dawn.com/feeds/business: refused\n"
        self.assertEqual(A.summarize(s, self.res(out), self.cfg["folders"]["trading"]),
                         "News: 2 items to read. 1 of 5 news feeds did not answer.")
        write_json(self.psx("trading", "config.json"), {"news_feeds": ["dawn-business", "tribune-business"]})
        self.assertEqual(A.feed_count(out, self.cfg["folders"]["trading"]), (1, 2))
        news = load("psx-news", "news")             # read only: the count of psx-news' own feeds
        self.assertEqual(A.NEWS_FEEDS_DEFAULT, len(news.FEEDS))

    def test_the_week_results_line_names_every_share(self):
        s = [x for x in A.DEFAULT_JOBS["weekly"] if x["id"] == "results"][0]
        syms = ["ZUP", "ZCAP", "ZBRK", "ZOLD", "ZSPL", "ZBON", "ZGAP", "ZNEW"]     # made up, as in psx-invest's tests
        r = dict(self.res("Demo Industries Limited (DEMO), MISCELLANEOUS. Fiscal year ends in June.\n"),
                 argv=["python3", "facts.py"] + syms + ["--metrics", "psx/metrics.json"])
        self.assertEqual(A.summarize(s, r, ""), "Results of your long-term shares: Results read for 8 shares (ZUP, "
                                                "ZCAP, ZBRK, ZOLD and 4 more). Saved in psx/metrics.json. The full "
                                                "output file has each one.")
        r["argv"] = ["python3", "facts.py", "SYS", "--metrics", "psx/metrics.json"]
        self.assertIn("Results read for 1 share (SYS).", A.summarize(s, r, ""))
        other = A.step("x", "longterm", "psx-other/other.py", title="Other")
        self.assertEqual(A.summarize(other, self.res("ZUP: HOLD\nsummary: 1 rule met\n"), ""), "Other: 1 rule met")


class TestRealAlertsWords(Base):
    """The real alerts.py, run by the runner's own run_step (offline, the site off, made-up data in a temp
    folder): the "In short" line never reads "no new alert" alone while a swing rule of a share you still
    hold is met (F13). It reads psx-alerts' words, so a change there shows here."""

    def folder(self, path):
        f = self.cfg["folders"]["trading"]
        data = os.path.join(f, "psx", "data")
        shutil.copytree(os.path.join(FIX, "psx-market"), data)
        src = os.path.join(FIX, "psx-v2", "paths", path, "mkt_summary")
        for name in sorted(os.listdir(src)):
            shutil.copy(os.path.join(src, name), os.path.join(data, "mkt_summary"))
        write_json(self.psx("trading", "config.json"), {"style": "swing", "psx_site": False})
        write_json(self.psx("trading", "portfolio.json"), contract("portfolio-swing.json"))
        return f

    def check(self, f, now):
        """The trading folder's alerts step, on a fixed day: -> (result, its "In short" line)."""
        s = A.alerts_step("a", "trading", "Alerts on the close (trading folder)")
        cfg = dict(self.cfg, python=sys.executable, pack=ROOT)
        res = A.run_step(cfg, dict(s, args=s["args"] + ["--offline", "--now", now]), False)
        self.assertEqual(res["argv"][1:4], [os.path.join(ROOT, "psx-alerts", "alerts.py"), "check", "--source"])
        return res, A.summarize(s, res, f, D(now[:10]))

    def met_line(self, res, line, watched, word):
        """One rule line from psx-alerts after its summary line; the runner shows it whole."""
        own = A.still_met(res["out"])
        self.assertEqual(len(own), 1, res["out"])
        self.assertTrue(own[0].startswith("DEMO: the %s" % word) and "You still hold 1,436 shares" in own[0], own)
        self.assertIn("no new alert rang (%d rules watched). A swing rule is still met. %s" % (watched, own[0]), line)
        self.assertNotIn("nothing hit", line.lower())
        self.assertEqual((res["status"], res["count"], res["met"]), ("ok", 0, 1))
        check_words(self, res["out"] + "\n" + line)

    def test_a_met_stop_after_its_ring_is_named(self):
        f = self.folder("gap")
        res, line = self.check(f, "2026-11-02 16:10")         # the stop rings from that day's closing file
        self.assertEqual((res["status"], len(A.ring_lines(res["out"]))), ("ok", 1), res["out"])
        res, line = self.check(f, "2026-11-02 16:20")         # a later run that day: nothing new rings
        self.met_line(res, line, 3, "stop rule was met on Mon 2026-11-02")
        self.assertIn("DEMO has no price from today, so its swing rules were checked on the closing files only.", line)

    def test_a_met_time_stop_after_its_ring_is_named(self):
        f = self.folder("flat")
        self.check(f, "2026-11-26 16:10")
        res, _ = self.check(f, "2026-11-27 16:20")             # the exit-by day: the time stop rings
        self.assertEqual(len(A.ring_lines(res["out"])), 1, res["out"])
        res, line = self.check(f, "2026-11-27 16:40")         # after the close: nothing new rings
        self.met_line(res, line, 4, "time stop is met")


class TestPositions(Base):
    def test_a_swing_position_shows_its_plan_and_its_gap_loss(self):
        write_json(self.psx("trading", "portfolio.json"), contract("portfolio-swing.json"))
        write_json(self.psx("trading", "config.json"), {"style": "swing"})
        lines, swing = A.position_lines(self.cfg["folders"]["trading"])
        self.assertEqual(swing, 1)
        self.assertEqual(lines, ["DEMO: you hold 1,436 shares (swing). Plan stop 96.25, target 105.64, exit by "
                                 "Fri 2026-11-27. Gap loss: about Rs 14,759.46, costs included, if it opens at 89.44 "
                                 "(the lower limit from the buy price 99.38)."])

    def test_the_gap_loss_of_143_shares(self):
        pf = contract("portfolio-swing.json")
        pf["positions"][0].update(shares=143, lots=[{"id": "DEMO-1", "date": "2026-10-29", "shares": 143,
                                                      "cost": 14211.34, "costs": 25.45}])
        write_json(self.psx("trading", "portfolio.json"), pf)
        lines, _ = A.position_lines(self.cfg["folders"]["trading"])
        self.assertIn("Gap loss: about Rs 1,469.78, costs included", lines[0])

    def test_a_day_position_and_an_empty_one(self):
        write_json(self.psx("trading", "portfolio.json"),
                   {"positions": [{"ticker": "OLD", "shares": 0}, {"ticker": "SYS", "shares": 50}]})
        self.assertEqual(A.position_lines(self.cfg["folders"]["trading"]), (["You hold 50 SYS."], 0))
        self.assertEqual(A.position_lines(self.cfg["folders"]["longterm"]), ([], 0))


class TestOpenBuys(Base):
    LINE = ("DEMO: a buy placed on Thu 2026-10-29 has no fill recorded. Record the fill (ticket.py fill), or "
            "record it expired (ticket.py result --fingerprint 0d80ffbb0d898a06 --status expired). New swing buys "
            "wait until then.")

    def broker(self, folder, name):
        write_json(self.psx(folder, "state", "broker.json"), contract(name))

    def test_a_placed_buy_with_no_fill_is_listed(self):
        write_json(self.psx("trading", "config.json"), {"style": "swing"})
        self.broker("trading", "broker-placed-unfilled.json")
        self.assertEqual(A.open_buy_lines(self.cfg), [(self.LINE, "DEMO", "2026-10-29")])
        self.assertEqual(A.buys_message(A.open_buy_lines(self.cfg)), self.LINE)

    def test_an_expired_buy_is_not_listed(self):
        self.broker("trading", "broker-expired.json")
        self.assertEqual(A.open_buy_lines(self.cfg), [])

    def test_an_old_approved_buy_and_then_its_fill(self):
        write_json(self.psx("trading", "config.json"), {"style": "swing"})
        self.broker("trading", "broker-approved-old.json")
        self.assertIn("DEMO: a buy approved on Thu 2026-10-29 has no fill recorded.", A.open_buy_lines(self.cfg)[0][0])
        doc = contract("broker-approved-old.json")
        doc["orders"].append({"at": "2026-10-29T10:01:00+05:00", "date": "2026-10-29", "status": "filled",
                              "symbol": "DEMO", "side": "buy", "qty": 1436, "price": 99.38})
        write_json(self.psx("trading", "state", "broker.json"), doc)
        self.assertEqual(A.open_buy_lines(self.cfg), [])

    def test_the_day_style_and_the_long_term_words(self):
        self.broker("trading", "broker-placed-unfilled.json")
        self.broker("longterm", "broker-placed-unfilled.json")
        got = [line for line, _, _ in A.open_buy_lines(self.cfg)]
        self.assertFalse(got[0].endswith("New swing buys wait until then."))       # day style: psx-risk only warns
        self.assertTrue(got[1].startswith("DEMO (long-term folder): a buy placed on Thu 2026-10-29"))
        self.assertIn("2 buys have no fill recorded: DEMO (Thu 2026-10-29), DEMO (long-term folder) "
                      "(Thu 2026-10-29).", A.buys_message(A.open_buy_lines(self.cfg)))
        self.assertEqual(A.open_buys(""), [])

    PART = ("DEMO: a buy placed on Tue 2026-11-10 is filled in part: 100 of 253 shares are recorded. If more of it "
            "filled, record them (ticket.py fill). If not, the other 153 ended at the close with no fill. To record "
            "that: ticket.py result --fingerprint 5e5e5e5e5e5e5e5e --status expired.")

    def part(self, folder="trading", filled=100):
        """A live limit buy of 253 DEMO placed on Tue 2026-11-10, with `filled` shares recorded (ticket.py result
        --status placed --filled-qty, made up)."""
        rec = {"date": "2026-11-10", "index": 1, "fingerprint": "5e5e5e5e5e5e5e5e", "symbol": "DEMO", "side": "buy",
               "qty": 253, "kind": "entry", "entry_ref": 33.05, "plain": "BUY 253 DEMO, limit 33.05, good for today"}
        doc = {"orders": [dict(rec, at="2026-11-10T09:30:00+05:00", status="approved"),
                          dict(rec, at="2026-11-10T09:40:00+05:00", status="placed"),
                          {"at": "2026-11-10T09:40:00+05:00", "date": "2026-11-10", "status": "filled",
                           "symbol": "DEMO", "side": "buy", "qty": filled, "price": 33.05}]}
        write_json(self.psx(folder, "state", "broker.json"), doc)
        return doc

    def test_a_part_fill_is_told_after_the_close_of_its_day(self):
        # F60: 100 of a 253-share buy are recorded. The other 153 may have filled too, or they ended at the close
        write_json(self.psx("trading", "config.json"), {"style": "swing"})
        doc = self.part()
        self.assertEqual(A.part_buy_lines(self.cfg, D("2026-11-10")), [self.PART])
        self.assertEqual(A.open_buy_lines(self.cfg), [])           # it has a fill: not "no fill recorded"
        self.assertEqual(A.part_buy_lines(self.cfg, D("2026-11-11")), [])   # a PSX order ends at the close
        check_words(self, self.PART)
        doc["orders"].append({"at": "2026-11-10T11:00:00+05:00", "date": "2026-11-10", "status": "filled",
                              "symbol": "DEMO", "side": "buy", "qty": 153, "price": 33.05})
        write_json(self.psx("trading", "state", "broker.json"), doc)
        self.assertEqual(A.part_buy_lines(self.cfg, D("2026-11-10")), [])   # the rest filled and is recorded

    def test_a_part_fill_closed_as_expired_or_with_no_count_is_not_told(self):
        doc = self.part()
        doc["orders"].append({"at": "2026-11-10T16:20:00+05:00", "date": "2026-11-10", "index": 1,
                              "fingerprint": "5e5e5e5e5e5e5e5e", "symbol": "DEMO", "side": "buy", "status": "expired"})
        write_json(self.psx("trading", "state", "broker.json"), doc)      # ticket.py result --fingerprint FP
        self.assertEqual(A.part_buy_lines(self.cfg, D("2026-11-10")), [])
        doc = self.part()
        del doc["orders"][2]["qty"]                                        # a fill with no share count: any fill
        write_json(self.psx("trading", "state", "broker.json"), doc)      # closes the order (the old rule)
        self.assertEqual((A.part_buy_lines(self.cfg, D("2026-11-10")), A.open_buy_lines(self.cfg)), ([], []))
        self.part("longterm", filled=10)
        self.assertEqual(A.part_buy_lines(self.cfg, D("2026-11-10")),
                         [self.PART.replace("DEMO: a buy", "DEMO (long-term folder): a buy").replace(
                             "100 of 253", "10 of 253").replace("other 153", "other 243")])


class TestCash(Base):
    def test_the_week_cash_line_has_both_ledgers_and_the_sum(self):
        write_json(self.psx("trading", "portfolio.json"), {"cash": 50000, "positions": []})
        write_json(self.psx("longterm", "portfolio.json"), {"cash": 0, "positions": []})
        self.assertEqual(A.cash_line(self.cfg),
                         "Cash: trading Rs 50,000.00, long-term Rs 0.00, together Rs 50,000.00. Does this match your "
                         "broker app? If not: ticket.py cash --set N (trading) or invest.py cash --set N (long-term).")

    def test_one_ledger_or_no_cash_recorded(self):
        self.cfg["folders"]["longterm"] = ""
        write_json(self.psx("trading", "portfolio.json"), {"cash": 50000})
        self.assertEqual(A.cash_line(self.cfg), "Cash: trading Rs 50,000.00. Does this match your broker app? If not: "
                                                "ticket.py cash --set N (trading).")
        os.remove(self.psx("trading", "portfolio.json"))
        self.assertIn("trading not recorded (no cash in psx/portfolio.json)", A.cash_line(self.cfg))


class TestCalendarNote(Base):
    def test_a_missing_and_an_empty_calendar_are_the_same(self):
        write_json(self.psx("trading", "config.json"), {"psx_site": True})
        write_json(self.psx("longterm", "config.json"), {"holidays": {}, "open_days": []})
        self.assertEqual(A.calendar_mismatch(self.cfg), "")
        write_json(self.psx("longterm", "config.json"), {"holidays": {"2026-11-03": "a made-up closure"}})
        self.assertIn("Make them the same.", A.calendar_mismatch(self.cfg))

    def test_the_week_report_says_to_add_a_new_years_holidays(self):
        saved, A.ask_clock = A.ask_clock, fake_clock      # 2028 is not in the made-up calendar either
        try:
            text = "\n".join(A.week_lines(self.cfg, self.state, T("2027-12-29 10:00")))
        finally:
            A.ask_clock = saved
        self.assertIn('The PSX holiday list for 2028 is not in the tools. Add closures under "holidays" in '
                      'psx/config.json when PSX announces them.', text)
        self.assertIn("Cash: ", text)


class TestNotes(unittest.TestCase):
    def emit(self, n):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            n.emit()
        return buf.getvalue()

    def test_applet_lines_end_with_the_footer_on_its_own_line(self):
        n = A.Notes(A.DEFAULTS, mode="applet")
        n.add("alerts", "PSX alert", "SYS\t105.00\nlow", sound=True, rank=0)
        self.assertEqual(self.emit(n), "notify\tPSX alert\tSYS 105.00 low\\n%s\tGlass\n" % FOOTER)
        self.assertEqual(message_lines("notify\tPSX alert\tSYS 105.00 low\\n%s\tGlass" % FOOTER),
                         [("PSX alert", ["SYS 105.00 low", FOOTER], "Glass")])

    def test_many_become_four(self):
        n = A.Notes(A.DEFAULTS, mode="applet")
        for i in range(6):
            n.add("alerts", "PSX alert", "alert %d" % i)
        lines = self.emit(n).splitlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("\t3 more messages: open the report.\\n", lines[-1])

    def test_alerts_and_problems_come_before_the_counts(self):
        n = A.Notes(A.DEFAULTS, mode="applet")
        n.add("morning", "PSX: Morning check done", "Setups: 2.", rank=2)
        n.add("after_close", "PSX: After-close check done", "Setups: 1.", rank=2)
        n.add("weekly", "PSX: Week report ready", "Problems: 0.", rank=2)
        n.add("alerts", "PSX signal", "ZCAP: a sell rule is met (TRIM). Open the report. You decide.")
        n.add("problems", "PSX auto-run: problem", "the DEMO stop was not checked", sound=True, rank=0)
        n.add("alerts", "PSX alert", "DEMO hit its stop", sound=True, rank=0)
        shown = [t for t, _, _ in n.ordered()]
        self.assertEqual(shown, ["PSX auto-run: problem", "PSX alert", "PSX signal", "PSX auto-run"])
        self.assertEqual(n.ordered()[-1][1], "3 more messages: open the report.")

    def test_switches(self):
        n = A.Notes(A.deep_merge(A.DEFAULTS, {"notify": {"morning": False}}), mode="applet")
        n.add("morning", "PSX", "done")
        self.assertEqual(n.items, [])

    def test_a_long_alert_is_cut_before_its_action(self):
        long = ("DEMO traded at or under your stop 96.25 on Fri 2026-11-20: the low in that day's closing file "
                "was 95.90 (official close 96.40). " * 4) + "You could sell: psx-broker flatten. You decide."
        cut = A.clip(long)
        self.assertLessEqual(len(cut), A.MESSAGE_MAX)
        self.assertTrue(cut.endswith("Open the report for the rest."))
        self.assertNotIn("You could", cut)
        check_words(self, cut)
        self.assertEqual(A.clip(STOP_RING), STOP_RING)           # a ring of normal length stays whole

    def test_print_mode_writes_the_footer_once(self):
        n = A.Notes(A.DEFAULTS, mode="print")
        n.add("alerts", "PSX alert", "one")
        n.add("alerts", "PSX alert", "two")
        self.assertEqual(self.emit(n), "Notification: PSX alert: one\nNotification: PSX alert: two\n%s\n" % FOOTER)
        self.assertEqual(self.emit(n), "")


class TestSafety(unittest.TestCase):
    def test_order_tools_are_refused(self):
        tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        try:
            path = os.path.join(tmp, "autorun.json")
            write_json(path, {"jobs": {"morning": [A.step("buy", "trading", "psx-broker/ticket.py", ["show"])]}})
            with self.assertRaises(A.AutorunError):
                A.load_config(path)
        finally:
            shutil.rmtree(tmp)
        for bad in ("psx-budget/size.py", "./psx-risk/check.py", "psx-broker/ticket.py"):
            with self.assertRaises(A.AutorunError):
                A.check_safe({"script": bad})
        # the folder ban on its own: no file name here is one of ticket.py, size.py or check.py
        for bad in ("psx-broker/other.py", "psx-risk/x.py", "psx-budget/y.py", "PSX-Broker/SKILL.md",
                    "psx-invest/../psx-risk/limits.py"):
            with self.assertRaises(A.AutorunError, msg=bad):
                A.check_safe({"script": bad})
            self.assertNotIn(bad.rsplit("/", 1)[-1], A.NEVER_SCRIPTS)
        for cmd in ("orders", "cash", "ack", "backfill"):        # psx-invest: only "signals" is run
            with self.assertRaises(A.AutorunError):
                A.check_safe({"script": "psx-invest/invest.py", "args": [cmd, "--write"]})
        for cmd in ("add", "reset"):
            with self.assertRaises(A.AutorunError):
                A.check_safe({"script": "psx-alerts/alerts.py", "args": [cmd, "--symbol", "SYS"]})

    def test_path_tricks_are_refused(self):
        for bad, args in (("psx-invest/../psx-broker/ticket.py", []), ("/somewhere/psx-alerts/alerts.py", ["check"]),
                          ("../psx-scanner/scan.py", ["daily"]), ("PSX-BROKER/ticket.py", ["show"]),
                          ("tools/ticket.py", []), ("tools/size.py", []), ("psx-alerts//alerts.py", ["RESET"]),
                          ("psx-invest/./invest.py", ["--offline", "orders"]), ("", []), (".", [])):
            with self.assertRaises(A.AutorunError, msg=bad):
                A.check_safe({"script": bad, "args": args})

    def test_safe_steps_pass(self):
        self.assertEqual(A.check_safe({"script": "psx-invest/invest.py", "args": ["signals", "--write", "--offline"]}),
                         "psx-invest/invest.py")
        self.assertEqual(A.check_safe({"script": "./psx-alerts/alerts.py", "args": ["check"]}), "psx-alerts/alerts.py")
        A.check_safe({"script": "psx-alerts/alerts.py", "args": ["quote", "DEMO", "--json"]})

    def test_the_guard_runs_again_before_each_run(self):
        cfg = A.deep_merge(A.DEFAULTS, {"pack": ROOT, "folders": {"trading": ROOT}})
        s = A.step("x", "trading", "psx-scanner/scan.py", ["daily"])
        s["script"] = "psx-risk/check.py"                 # changed after the settings were loaded
        with self.assertRaises(A.AutorunError):
            A.run_step(cfg, s, False)

    def test_a_link_to_an_order_tool_is_refused(self):
        tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        try:
            os.makedirs(os.path.join(tmp, "psx-broker"))
            os.makedirs(os.path.join(tmp, "psx-scanner"))
            open(os.path.join(tmp, "psx-broker", "ticket.py"), "w").close()
            os.symlink(os.path.join(tmp, "psx-broker", "ticket.py"), os.path.join(tmp, "psx-scanner", "scan.py"))
            cfg = A.deep_merge(A.DEFAULTS, {"pack": tmp, "folders": {"trading": tmp}})
            with self.assertRaises(A.AutorunError):
                A.run_step(cfg, A.step("scan", "trading", "psx-scanner/scan.py", ["daily"]), False)
        finally:
            shutil.rmtree(tmp)

    def test_no_default_step_can_make_an_order(self):
        for job, steps in A.DEFAULT_JOBS.items():
            for s in steps:
                rel = A.check_safe(s)
                self.assertNotIn(rel.split("/")[0], A.NEVER_RUN, job)
                self.assertNotIn("orders", s["args"], job)
                if "--write" in s["args"]:
                    self.assertEqual(s["args"][:1], ["signals"], job)
                self.assertNotIn("orders.json", " ".join(s["args"]), job)


SPEC_STEPS = {     # SPEC v2 section 4.3, in order: (folder, script, args)
    "morning": [("trading", "psx-scanner/scan.py", ["daily", "--quiet"]),
                ("trading", "psx-alerts/alerts.py", ["check", "--source", "{price_source}"]),
                ("trading", "psx-news/news.py", ["--market"]),
                ("trading", "psx-insiders/insiders.py", ["new", "--read"]),
                ("longterm", "psx-invest/invest.py", ["signals", "--write", "--offline"]),
                ("longterm", "psx-news/news.py", []),
                ("longterm", "psx-insiders/insiders.py", ["new", "--read"])],
    "check": [("trading", "psx-alerts/alerts.py", ["check", "--source", "{price_source}"]),
              ("longterm", "psx-alerts/alerts.py", ["check", "--source", "{price_source}"])],
    "after_close": [("trading", "psx-scanner/scan.py", ["daily", "--quiet"]),
                    ("trading", "psx-alerts/alerts.py", ["check", "--source", "{price_source}"]),
                    ("longterm", "psx-alerts/alerts.py", ["check", "--source", "{price_source}"]),
                    ("longterm", "psx-invest/invest.py", ["signals", "--write", "--offline"]),
                    ("longterm", "psx-news/news.py", []),
                    ("trading", "psx-news/news.py", ["--market"])],
    "weekly": [("trading", "psx-scanner/scan.py", ["universe"]),
               ("longterm", "psx-analyst/facts.py", ["@holdings", "--metrics", "psx/metrics.json"])],
}
SPEC_EXAMPLE = {"python": "", "pack": "", "folders": {"trading": "", "longterm": ""}, "reports": "",
                "calendar_folder": "trading", "morning_lead_min": 75, "check_every_min": 5, "check_start_after_min": 7,
                "final_check_after_min": 10, "after_close_delay_min": 40, "after_close_retry_min": 15,
                "after_close_give_up_min": 240, "weekly": {"weekday": 5, "time": "10:00"},
                "price_source": "psx-screener", "tape": False, "keep_awake": True,
                "notify": {"morning": True, "alerts": True, "after_close": True, "weekly": True, "problems": True,
                           "sound": "Glass"}}


class TestSteps(unittest.TestCase):
    def test_the_step_lists_match_section_4_3(self):
        got = dict((job, [(s["folder"], s["script"], s["args"]) for s in steps]) for job, steps in A.DEFAULT_JOBS.items())
        self.assertEqual(got, SPEC_STEPS)
        sites = dict((s["id"], s["site"]) for s in A.DEFAULT_JOBS["check"])
        self.assertEqual(sites, {"alerts-trading": "yes", "alerts-longterm": "tape"})
        signals = A.DEFAULT_JOBS["morning"][4]
        self.assertEqual((signals["optional"], signals["needs_file"], signals["site"]),
                         (True, os.path.join("psx", "invest.json"), "no"))

    def test_the_example_settings_match_the_spec(self):
        with open(os.path.join(HERE, "autorun.example.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc, SPEC_EXAMPLE)
        self.assertEqual(list(doc), list(SPEC_EXAMPLE))
        for k, v in doc.items():
            if k in A.DEFAULTS and k not in ("python", "pack", "folders", "reports"):
                self.assertEqual(A.DEFAULTS[k], v, k)

    def test_loading_fills_in_the_python_and_the_reports_folder(self):
        tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        try:
            path = os.path.join(tmp, "autorun.json")
            write_json(path, dict(SPEC_EXAMPLE, folders={"trading": os.path.join(tmp, "t"), "longterm": ""}))
            cfg = A.load_config(path)
            self.assertEqual(cfg["python"], sys.executable)
            self.assertEqual(cfg["reports"], os.path.join(tmp, "t", "autorun"))
            self.assertEqual(cfg["folders"]["longterm"], "")
        finally:
            shutil.rmtree(tmp)

    def test_the_settings_file_cannot_pick_the_program(self):
        # "python" in autorun.json once named the program that ran every step: /usr/bin/find could then run
        # any tool on the timer. Now every step runs with auto-run's own Python.
        tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        try:
            path = os.path.join(tmp, "autorun.json")
            os.makedirs(os.path.join(tmp, "t", "psx", "data", "tape"))
            write_json(path, {"python": "/usr/bin/find", "folders": {"trading": os.path.join(tmp, "t")},
                              "pack": ROOT})
            cfg = A.load_config(path)
            self.assertEqual(cfg["python"], sys.executable)
            s = A.step("x", "trading", "psx-scanner/scan.py", ["-maxdepth", "0", "-exec", "echo", ";"])

            def no_run(argv, **kw):          # nothing runs in this test: the command line is what counts
                raise OSError("not run in a test")
            saved, A.subprocess.run = A.subprocess.run, no_run
            try:
                res = A.run_step(cfg, s, True)
            finally:
                A.subprocess.run = saved
            self.assertEqual(res["argv"][0], sys.executable)
            self.assertNotIn("/usr/bin/find", res["argv"])
        finally:
            shutil.rmtree(tmp)

    def test_paths_in_the_settings_must_be_full_paths(self):
        tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        try:
            path = os.path.join(tmp, "autorun.json")
            for bad in ({"reports": "myreports"}, {"folders": {"trading": "trade"}}, {"pack": "pack"}):
                write_json(path, dict(bad, folders=bad.get("folders") or {"trading": os.path.join(tmp, "t")}))
                with self.assertRaises(A.AutorunError, msg=str(bad)) as e:
                    A.load_config(path)
                self.assertIn("wants a full path (one that starts with /)", str(e.exception))
                check_words(self, str(e.exception))
            write_json(path, {"reports": "~/psx-reports", "folders": {"trading": os.path.join(tmp, "t")}})
            self.assertEqual(A.load_config(path)["reports"], os.path.join(os.path.expanduser("~"), "psx-reports"))
        finally:
            shutil.rmtree(tmp)

    def test_bad_settings_are_plain_errors(self):
        tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        try:
            path = os.path.join(tmp, "autorun.json")
            for bad in ({"check_every_min": 0}, {"weekly": {"weekday": 9}}, {"price_source": "yahoo-ish"},
                        {"folders": {"other": "/x"}}, {"calendar_folder": "mine"}, {"morning_lead_min": True},
                        {"weekly": {"time": "25:00"}}):
                write_json(path, bad)
                with self.assertRaises(A.AutorunError, msg=str(bad)):
                    A.load_config(path)
            with open(path, "w") as fh:
                fh.write("{not json")
            with self.assertRaises(A.AutorunError):
                A.load_config(path)
            with self.assertRaises(A.AutorunError):
                A.load_config(os.path.join(tmp, "missing.json"))
        finally:
            shutil.rmtree(tmp)


class TestRealClock(unittest.TestCase):
    """Asks the pack's own clock (psx-alerts/alerts.py clock --json, read only) with a made-up config."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        write_json(os.path.join(self.tmp, "psx", "config.json"), {"psx_site": False})
        self.cfg = A.deep_merge(A.DEFAULTS, {"python": sys.executable, "pack": ROOT,
                                             "folders": {"trading": self.tmp, "longterm": ""}})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_friday_holiday_and_ramadan(self):
        self.assertEqual(A.ask_clock(self.cfg, D("2026-11-06"))["segments"], FRIDAY)
        st = A.ask_clock(self.cfg, D("2026-11-09"))
        self.assertEqual(st["segments"], [])
        self.assertIn("Iqbal Day", st["reason"])
        self.assertEqual(A.ask_clock(self.cfg, D("2027-02-12"))["segments"], RAMADAN_FRIDAY)

    def test_the_plan_of_four_days(self):
        path = os.path.join(self.tmp, "autorun.json")
        write_json(path, {"python": sys.executable, "pack": ROOT, "folders": {"trading": self.tmp}})
        env = os.environ.get("PSX_NOTIFY")
        os.environ["PSX_NOTIFY"] = "print"
        try:
            got = {}
            for day in ("2026-11-05", "2026-11-06", "2026-11-07", "2026-11-09"):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(A.main(["--config", path, "plan", "--now", day + " 07:00"]), 0)
                got[day] = buf.getvalue()
        finally:
            os.environ["PSX_NOTIFY"] = env
        self.assertIn("- 8:15 AM  morning check\n- 9:40 AM to 3:30 PM  price checks every 5 minutes\n"
                      "- 3:40 PM  last check of the day\n- 4:10 PM  after-close check", got["2026-11-05"])
        self.assertIn("- 8:00 AM  morning check\n- 9:25 AM to 12:00 PM  price checks every 5 minutes\n"
                      "- 12:10 PM  last check of this part of the day\n- 2:40 PM to 4:30 PM  price checks every 5 "
                      "minutes\n- 4:40 PM  last check of the day\n- 5:10 PM  after-close check", got["2026-11-06"])
        self.assertIn("PSX is closed on Saturday 7 November 2026 (weekend). No market jobs.\n- 10:00 AM  week report\n",
                      got["2026-11-07"])
        self.assertNotIn("week report", got["2026-11-05"] + got["2026-11-06"] + got["2026-11-09"])
        self.assertIn("PSX is closed on Monday 9 November 2026 (PSX holiday: Iqbal Day).", got["2026-11-09"])

    def test_an_expected_holiday_is_seen_the_evening_before(self):
        info = A.day_info(self.cfg, {}, D("2027-03-05"))
        self.assertEqual(A.expected_holiday(info), "Juma-tul-Wida")
        self.assertEqual(A.holiday_ahead(self.cfg, {}, D("2027-03-04")), (D("2027-03-05"), "Juma-tul-Wida"))
        self.assertEqual(A.holiday_ahead(self.cfg, {}, D("2026-11-06")), (None, ""))     # Iqbal Day is fixed


def pack_files():
    for base, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in sorted(files):
            yield os.path.join(base, f)


class TestFiles(unittest.TestCase):
    def test_every_file_has_no_em_dash_no_user_path_and_no_email(self):
        seen = set()
        for path in pack_files():
            rel = os.path.relpath(path, HERE)
            seen.add(os.path.splitext(path)[1])
            text = read(path)
            self.assertNotIn(chr(0x2014), text, rel)
            for user_path in ("/" + "Users/", "Documents/" + "trader-skill"):     # in pieces: this file names none
                self.assertNotIn(user_path, text, rel)
            for mail in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text):     # only PSX's own data team
                self.assertTrue(mail.endswith("@psx.com.pk"), "%s: %s" % (rel, mail))
        self.assertTrue({".py", ".sh", ".plist", ".applescript", ".json", ".md"} <= seen, seen)

    def test_the_files_of_section_4_1_are_there(self):
        for rel in ("SKILL.md", "autorun.py", "run.sh", "PSXAutorun.applescript", "install.sh", "uninstall.sh",
                    "autorun.example.json", "launchd/local.psx-autorun.tick.plist",
                    "launchd/local.psx-autorun.tape.plist"):
            self.assertTrue(os.path.isfile(os.path.join(HERE, rel)), rel)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("sh"), "no bash here")
    def test_the_shell_scripts_parse(self):
        for shell, name in (("bash", "install.sh"), ("bash", "uninstall.sh"), ("sh", "run.sh")):
            p = subprocess.run([shutil.which(shell), "-n", os.path.join(HERE, name)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=30)          # -n: read and check only, run nothing
            self.assertEqual(p.returncode, 0, "%s: %s" % (name, p.stderr.decode()))

    def test_the_plists_name_no_documents_path(self):
        import plistlib
        tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        try:
            for label in ("local.psx-autorun.tick", "local.psx-autorun.tape"):
                text = read(os.path.join(HERE, "launchd", label + ".plist"))
                self.assertNotIn("Documents", text)
                filled = text.replace("__HOME__", tmp).replace("__APP__", os.path.join(tmp, "PSX Autorun.app"))
                doc = plistlib.loads(filled.encode("utf-8"))
                self.assertEqual(doc["Label"], label)
                self.assertTrue(doc["ProgramArguments"][0].endswith("PSX Autorun.app/Contents/MacOS/applet"))
                self.assertTrue(doc["StandardOutPath"].startswith(os.path.join(tmp, "Library", "Logs")))
                out = os.path.join(tmp, label + ".plist")
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(filled)
                if shutil.which("plutil"):
                    p = subprocess.run([shutil.which("plutil"), "-lint", out], stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, timeout=30)
                    self.assertEqual(p.returncode, 0, p.stdout.decode() + p.stderr.decode())
                if label.endswith("tick"):
                    self.assertIs(doc["RunAtLoad"], True)
                    self.assertEqual([x["Minute"] for x in doc["StartCalendarInterval"]], list(range(0, 60, 5)))
                    self.assertEqual(doc["EnvironmentVariables"], {"PSX_JOB": "tick"})
                else:
                    self.assertNotIn("StartCalendarInterval", doc)
        finally:
            shutil.rmtree(tmp)

    def test_install_checks_its_folders_before_it_changes_anything(self):
        # Never run install.sh in a test: read it. --trading is checked before the first change.
        text = read(os.path.join(HERE, "install.sh"))
        need = text.index('[ -n "$TRADING" ] || { usage; stop "--trading is needed')
        for action in ("mkdir -p", "cp \"$HERE", "printf '%s\\n' \"$PY\" >", "osacompile", "launchctl", "open -n",
                       "rm -f"):
            self.assertLess(need, text.index(action), action)
        self.assertIn("Nothing was changed.", text[need:need + 200])
        self.assertIn('"$(cd "$HERE/.." && pwd)"', text)               # the pack: the folder above install.sh
        self.assertNotIn("PSX_PACK", text)
        self.assertIn('[ -f "$TRADING/psx/config.json" ]', text)

    def test_install_tests_the_real_path_and_turns_it_off_when_it_fails(self):
        text = read(os.path.join(HERE, "install.sh"))
        self.assertLess(text.index("probe.request"), text.index('launchctl bootstrap'))
        self.assertLess(text.index("launchctl bootstrap"), text.index("launchctl kickstart"))
        self.assertIn("-lt 60", text)
        self.assertIn("Did you see the message \"PSX auto-run works\"? (y/n)", text)
        self.assertIn("The scheduled run could not write to your reports folder in Documents. Open System Settings, "
                      "Privacy & Security, Files and Folders, and turn on Documents Folder for PSX Autorun. Then run "
                      "install.sh again. Nothing was left turned on.", text)
        self.assertIn("fix the PROBLEM line above, then run install.sh again. Nothing was turned on.", text)
        self.assertIn("codesign --force --deep --sign -", text)
        self.assertIn("plutil -replace LSUIElement -bool true", text)

    def test_uninstall_keeps_the_reports_and_the_psx_folders(self):
        text = read(os.path.join(HERE, "uninstall.sh"))
        self.assertIn("tccutil reset All local.psx-autorun", text)
        self.assertIn('launchctl bootout "$GUI/$L"', text)
        for line in text.splitlines():
            if "rm -" in line:
                self.assertTrue("$SUPPORT" in line or "$AGENTS" in line or "$LOGS" in line, line)

    def test_the_app_shows_no_message_for_exit_4(self):
        text = read(os.path.join(HERE, "PSXAutorun.applescript"))
        self.assertLess(text.index("if errNum is 4 then return"), text.index("display notification"))
        self.assertIn('property launcherPath : "__LAUNCHER__"', text)
        self.assertIn('set AppleScript\'s text item delimiters to "\\\\n"', text)
        self.assertIn('property footerText : "%s"' % FOOTER, text)

    def test_the_runner_calls_the_mac_tools_only_from_its_hooks(self):
        import ast
        src = read(os.path.join(HERE, "autorun.py"))
        allowed = set(MAC_HOOKS) | {"other_pollers"}
        for node in ast.parse(src).body:
            if isinstance(node, ast.FunctionDef) and node.name not in allowed:
                body = ast.get_source_segment(src, node)
                for tool in ("osascript", "caffeinate\"", "launchctl\"", "/usr/bin/open", "pgrep"):
                    self.assertNotIn(tool, body, node.name)
        for k in MAC_HOOKS:
            self.assertIsNot(getattr(A, k), _SANDBOX["hooks"][k], k)       # a fake while the tests run

    def test_the_safety_section_is_word_for_word(self):
        text = read(os.path.join(HERE, "SKILL.md"))
        block = text.split("\n## Safety\n", 1)[1]
        words = " ".join(x[2:] if x.startswith("- ") else x for x in block.strip().splitlines())
        self.assertEqual(words, "Education and tooling, not financial advice. Nothing in this pack places an order. "
                                "Auto-run has no broker link. It never runs psx-broker, psx-budget or psx-risk. PSX "
                                "prices here are about 5 minutes late. A stop is a plan, not a promise. Auto-run "
                                "cannot see a market halt in late data.")

    def test_the_skill_page_says_what_task_9_asks(self):
        text = " ".join(read(os.path.join(HERE, "SKILL.md")).split())
        for words in ('bash "<pack>/psx-autorun/install.sh" --trading "<folder that holds psx/>" --longterm '
                      '"<folder that holds psx/>"', "Click **Allow** for both", 'Did you see the message "PSX '
                      'auto-run works"?', "**Alerts** style", "allow it in each Focus", "pause all --minutes 10",
                      "No message by 9:15 AM on a market day** (9:00 AM on Fridays and in Ramadan)",
                      "lid open and on power", "read `LATEST.md`", "Never install or uninstall auto-run",
                      "Never add a step with an order tool", "pause checks", "This ran late",
                      "auto-run cannot see prices in the session", "## Wake the Mac (optional)", "## Turn it off",
                      "## Problems and fixes", "## Rules for Claude", "## Settings", "## Commands"):
            self.assertIn(words, text)
        self.assertNotIn("not built yet", text)

    def test_the_skill_page_says_what_a_late_check_covers(self):
        text = " ".join(read(os.path.join(HERE, "SKILL.md")).split())
        self.assertNotIn("No close is lost: psx-alerts reads every closing file", text)       # it had no limit
        for words in ("For a swing position with its plan in psx/portfolio.json, no close is lost",
                      "Rules you added by hand in psx/alerts.json (in either folder), and every rule in a day-style "
                      "folder, use today's prices only, never a closing file. So a close the Mac slept through is "
                      "not checked for them.",
                      # F07: a hand rule misses a dip between two checks too, and a share held from before the
                      # switch to swing has only hand rules (the README's "If you already hold shares")
                      "Such rules also see only the price at each check. So a dip under the stop between two checks "
                      "is missed too.",
                      "A share you hold with no swing plan (a day trade, or a share you bought before the switch to "
                      "swing) has only such rules. See \"If you already hold shares\" in the README.",
                      "check those shares in your broker app"):
            self.assertIn(words, text)
        self.assertIn("\n### If you already hold shares\n", read(os.path.join(ROOT, "README.md")))

    def test_the_skill_page_never_asks_for_a_pause_with_no_resume(self):
        text = " ".join(read(os.path.join(HERE, "SKILL.md")).split())
        self.assertNotIn("Before a long price watch by hand (a tape or an entry watch in the chat): `autorun.py pause "
                         "checks`", text)
        # F10: the same rule as the README's "One tool on the site at a time"
        for words in ("A tape or an entry watch in the chat: auto-run sees it by itself. Then auto-run reads no PSX "
                      "page, and its checks read only the prices saved on your computer. Do not pause auto-run for it.",
                      "An entry watch saves no market prices",
                      "Before your own alerts loop (a `/loop` or a scheduled task in Claude): run `autorun.py pause "
                      "checks`. The pause lasts the rest of the day.",
                      "Until `autorun.py resume`, the price checks of both folders are off, so no stop is checked "
                      "while PSX is open. The after-close job still runs. So run `autorun.py resume` as soon as the "
                      "loop ends, and tell the user: until then auto-run checks no stop."):
            self.assertIn(words, text)
        readme = " ".join(read(os.path.join(ROOT, "README.md")).split())
        for words in ("auto-run sees it by itself. Then auto-run reads no PSX page, and its checks read only the "
                      "prices saved on your computer. Do not pause auto-run for it.",
                      "a `/loop` or a scheduled task in Claude", "The pause lasts the rest of the day.",
                      "the price checks of both folders are off, so no stop is checked while PSX is open. The "
                      "after-close job still runs."):
            self.assertIn(words, readme)
        self.assertEqual(A.POLLERS, ("psx-scanner/scan.py tape", "psx-entry/entry.py watch"))   # what it finds

    def test_the_skill_page_names_each_command_that_needs_a_pause_first(self):
        # psx-invest's backfill --forget-no-session rewrites psx/state/invest.json too (SPEC 3.11: pause first)
        text = " ".join(read(os.path.join(HERE, "SKILL.md")).split())
        self.assertIn("So before `invest.py ack`, `invest.py backfill --forget-no-session`, `alerts.py add` or "
                      "`alerts.py reset` in a folder auto-run watches, run `autorun.py pause all --minutes 10`", text)

    def test_the_skill_page_says_what_the_after_close_report_names(self):
        text = " ".join(read(os.path.join(HERE, "SKILL.md")).split())
        for words in ("It also lists each buy with no fill recorded, and each buy of that day filled in part.",
                      "**A buy is filled in part** (after the close, on that day only)",
                      "`ticket.py result --fingerprint FP --status expired`. psx-risk counts the rest only on that "
                      "day.",
                      "After it rang, the report still names it, never just \"no new alert\". The message counts it: "
                      "\"Swing rules still met: 1.\""):
            self.assertIn(words, text)
        self.assertIn("Swing rules still met: %d.", read(os.path.join(HERE, "autorun.py")))

    @unittest.skipUnless(shutil.which("bash"), "no bash here")
    def test_install_makes_the_reports_folder_a_full_path(self):
        # Never run install.sh in a test. Only its few "reports path" lines run here: plain case statements on a
        # string, in a temp folder.
        text = read(os.path.join(HERE, "install.sh"))
        start, end = text.index("# --- reports path ---\n"), text.index("# --- end of reports path ---")
        self.assertLess(text.index('[ -n "$TRADING" ] || { usage; stop'), start)
        for action in ("mkdir -p", "cp \"$HERE", "printf '%s\\n' \"$PY\" >", "json.dump(cfg"):
            self.assertLess(end, text.index(action), action)          # before anything is written
        self.assertIn('given["reports"], had["reports"] = asked, cfg.get("reports")', text)
        tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        try:
            here = os.path.realpath(tmp)
            for given, want in (("myreports", os.path.join(here, "myreports")), ("~/psx-reports", "/home-x/psx-reports"),
                                ("~", "/home-x"), ("/abs/reports", "/abs/reports"), ("", "")):
                p = subprocess.run([shutil.which("bash"), "-c", 'set -eu; REPORTS="$1"\n%sprintf "%%s" "$REPORTS"'
                                    % text[start:end], "_", given], cwd=here, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env={"PATH": "/usr/bin:/bin", "HOME": "/home-x"}, timeout=30)
                self.assertEqual((p.returncode, p.stdout.decode()), (0, want), given)
        finally:
            shutil.rmtree(tmp)


class TestRunSh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        shutil.copy(os.path.join(HERE, "run.sh"), os.path.join(self.tmp, "run.sh"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sh(self, *args):
        p = subprocess.run([shutil.which("sh"), os.path.join(self.tmp, "run.sh")] + list(args),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                           env={"PATH": "/usr/bin:/bin", "HOME": self.tmp})
        return p.returncode, p.stdout.decode(), p.stderr.decode()

    @unittest.skipUnless(shutil.which("sh") and shutil.which("find"), "no sh here")
    def test_no_python_gives_exit_3_once_then_4_within_the_hour(self):
        with open(os.path.join(self.tmp, "python.path"), "w") as fh:
            fh.write(os.path.join(self.tmp, "no-such-python") + "\n")
        rc, out, err = self.run_sh("tick")
        self.assertEqual(rc, 3)
        self.assertIn("no Python at", err)
        stamp = os.path.join(self.tmp, "state", "launcher-told")
        self.assertTrue(os.path.isfile(stamp))
        self.assertEqual(self.run_sh("tick")[0], 4)            # the app shows nothing for 4
        old = os.path.getmtime(stamp) - 2 * 3600
        os.utime(stamp, (old, old))
        self.assertEqual(self.run_sh("tick")[0], 3)            # an hour later it is told again

    @unittest.skipUnless(shutil.which("sh"), "no sh here")
    def test_it_starts_the_runner_in_applet_mode(self):
        with open(os.path.join(self.tmp, "python.path"), "w") as fh:
            fh.write(sys.executable + "\n")
        with open(os.path.join(self.tmp, "autorun.py"), "w") as fh:
            fh.write("import os, sys\nprint(' '.join(sys.argv[1:]), os.environ.get('PSX_NOTIFY'))\n")
        os.makedirs(os.path.join(self.tmp, "state"))
        open(os.path.join(self.tmp, "state", "launcher-told"), "w").close()
        rc, out, err = self.run_sh("tick", "--dry-run")
        self.assertEqual((rc, out.strip()), (0, "tick --dry-run applet"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "state", "launcher-told")))


FAKE_HEAD = r'''
import datetime, json, os, sys
a = sys.argv[1:]
pack = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(pack, "calls.txt"), "a") as fh:
    fh.write("%s %s %s\n" % (os.path.basename(os.getcwd()), TOOL, " ".join(a)))
try:
    with open(os.path.join("psx", "fake.json")) as fh:
        f = json.load(fh).get(TOOL) or {}
except (OSError, ValueError):
    f = {}
'''
FAKE_BODY = {
    "alerts": r'''
if a[0] == "clock":
    d = datetime.date.fromisoformat(a[a.index("--now") + 1][:10])
    segs = [] if d.weekday() >= 5 else ([["09:15", "12:00"], ["14:30", "16:30"]] if d.weekday() == 4
                                        else [["09:30", "15:30"]])
    print(json.dumps({"segments": segs, "session": "normal" if segs else None,
                      "reason": "" if segs else "weekend", "calendar_known": True, "calendar_note": ""}))
    sys.exit(1)
if a[0] == "quote":
    t = f.get("quote_time")
    print(json.dumps({"quotes": {a[1]: {"price": 100.0, "time": t}} if t else {}, "errors": []}))
    sys.exit(0 if t else 1)
if not os.path.exists(os.path.join("psx", "alerts.json")):
    print("alerts.py: no rules in psx/alerts.json: add some with alerts.py add --from-plan", file=sys.stderr)
    sys.exit(1)
if f.get("tape") and "--offline" not in a:
    path = os.path.join("psx", "data", "tape", f["tape"], "market.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).replace(microsecond=0)
    with open(path, "a") as fh:
        fh.write(json.dumps({"fetched": f.get("tape_fetched") or now.isoformat(), "source": "screener",
                             "prices": {"DEMO": 100.0}}) + "\n")
print("PSX OPEN (made-up clock).")
for r in f.get("rings") or []:
    print("ALERT " + r)
for e in f.get("errors") or []:
    print("error: " + e)
if not f.get("rings"):
    print(f.get("nothing") or "Nothing hit. 1 rule(s) watched today.")
for n in f.get("notes") or []:
    print("note: " + n)
sys.exit(f.get("exit", 0 if f.get("rings") else 1))
''',
    "scan": r'''
hits = f.get("hits", ["DEMO", "AAA"])
with open(os.path.join("psx", "watchlist.json"), "w") as fh:
    json.dump({"hits": [{"symbol": s} for s in hits]}, fh)
print("Daily scan: %d setups." % len(hits))
sys.exit(0)
''',
    "news": r'''
if f.get("feeds_down") is None or f["feeds_down"] < 5:
    print("Headlines:")
    print("  2026-11-02 07:40 PKT  DEMO        Dawn               A made-up story")
if f.get("halt"):
    print("      tags: halt, macro  https://example.org/1")
for name in ["brecorder-markets", "brecorder-business-finance", "dawn-business", "tribune-business",
             "propakistani-business"][:f.get("feeds_down") or 0]:
    print("note: feed %s: could not reach https://example.org/%s: [Errno 61] Connection refused" % (name, name))
sys.exit(0 if f.get("feeds_down") is None or f["feeds_down"] < 5 else 1)
''',
    "insiders": r'''
print("No new notices.")
sys.exit(1)
''',
    "invest": r'''
for line in f.get("lines") or ["HOLD    DEMO   10.0%  no rule asks for a change"]:
    print(line)
sys.exit(0)
''',
    "facts": r'''
print("DEMO: results read (made up).")
for line in f.get("lines") or []:
    print(line)
sys.exit(0)
''',
}
FAKE_PATHS = {"alerts": "psx-alerts/alerts.py", "scan": "psx-scanner/scan.py", "news": "psx-news/news.py",
              "insiders": "psx-insiders/insiders.py", "invest": "psx-invest/invest.py", "facts": "psx-analyst/facts.py"}


@needs_lock
class Tick(unittest.TestCase):
    """A made-up pack (fake tools that log each call to calls.txt) and two made-up folders in a temp folder.
    The clock is the made-up one (no subprocess), unless a test asks for the fake pack's own."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="psx-autorun-")
        self.env = dict(os.environ)
        self.saved = (A.other_pollers, A.ask_clock)
        A.ask_clock = fake_clock
        os.environ["PSX_AUTORUN_HOME"] = os.path.join(self.tmp, "home")
        os.environ["PSX_AUTORUN_LOGS"] = os.path.join(self.tmp, "logs")
        os.environ["PSX_NOTIFY"] = "applet"
        self.pack = os.path.join(self.tmp, "pack")
        for tool, rel in FAKE_PATHS.items():
            path = os.path.join(self.pack, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write("TOOL = %r\n" % tool + FAKE_HEAD + FAKE_BODY[tool])
        self.folders = {}
        for name in ("trading", "longterm"):
            f = os.path.join(self.tmp, name)
            os.makedirs(os.path.join(f, "psx", "data", "mkt_summary"))
            write_json(os.path.join(f, "psx", "config.json"), {"psx_site": True, "paper": True, "holidays": {}})
            self.folders[name] = f
        os.makedirs(os.path.join(self.folders["trading"], "psx", "data", "tape"))
        os.symlink(os.path.join(self.folders["trading"], "psx", "data", "tape"),
                   os.path.join(self.folders["longterm"], "psx", "data", "tape"))
        write_json(self.p("longterm", "alerts.json"), {"rules": [{"id": "x"}]})
        write_json(self.p("longterm", "portfolio.json"), {"cash": 0, "positions": [{"ticker": "SYS", "shares": 1}]})
        self.fake("longterm", "alerts", rings=["SYS 105.00 is at or below your below 105.05."])
        self.cfg_path = os.path.join(self.tmp, "autorun.json")
        self.settings()

    def tearDown(self):
        A.other_pollers, A.ask_clock = self.saved
        os.environ.clear()
        os.environ.update(self.env)
        EXTRA_CLOSED.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, **extra):
        doc = {"python": sys.executable, "pack": self.pack, "folders": dict(self.folders),
               "reports": os.path.join(self.tmp, "reports"), "keep_awake": False}
        doc.update(extra)
        write_json(self.cfg_path, doc)

    def p(self, folder, *parts):
        return os.path.join(self.folders[folder], "psx", *parts)

    def r(self, *parts):
        return os.path.join(self.tmp, "reports", *parts)

    def fake(self, folder, tool, **how):
        path = self.p(folder, "fake.json")
        doc = json.loads(read(path)) if os.path.exists(path) else {}
        doc[tool] = how
        write_json(path, doc)

    def closing_file(self, day):
        open(self.p("trading", "data", "mkt_summary", day + ".Z"), "w").close()

    def calls(self):
        path = os.path.join(self.pack, "calls.txt")
        return read(path).splitlines() if os.path.exists(path) else []

    def clear_calls(self):
        path = os.path.join(self.pack, "calls.txt")
        if os.path.exists(path):
            os.remove(path)

    def state(self):
        return json.loads(read(os.path.join(self.tmp, "home", "state", "state.json")))

    def run_cmd(self, *args):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = A.main(["--config", self.cfg_path] + list(args))
        self.err = err.getvalue()
        return rc, buf.getvalue()

    def tick(self, now):
        rc, out = self.run_cmd("tick", "--now", now)
        self.assertEqual(rc, 0)
        return out

    def titles(self, out):
        return [t for t, _, _ in message_lines(out)]


class TestWholeTick(Tick):
    def test_a_day(self):
        A.ask_clock = self.saved[1]                  # this test asks the fake pack's own clock (a subprocess)
        out = self.tick("2026-11-02 08:15")
        self.assertIn("notify\tPSX: Morning check done\tSetups: 2. News: 2 new. Problems: 0.", out)
        report = read(self.r("reports", "2026-11-02", "morning.md"))
        self.assertIn("Daily scan (trading folder): 2 setups: DEMO, AAA.", report)
        self.assertIn("Nothing was bought or sold.", report)
        self.assertIn("Paper trading is on in the trading folder.", report)
        self.assertTrue(report.rstrip().endswith(FOOTER))
        out = self.tick("2026-11-02 10:00")      # the trading folder has no rules: the long-term step reads the site
        self.assertIn("notify\tPSX alert\tSYS 105.00 is at or below your below 105.05.\\n%s\tGlass" % FOOTER, out)
        self.assertIn("longterm alerts check --source psx-screener", self.calls())
        self.assertIn("SYS 105.00", read(self.r("reports", "2026-11-02", "checks.md")))
        self.tick("2026-11-02 15:40")
        self.assertEqual(self.tick("2026-11-02 16:10"), "")     # no closing file yet: it waits, no report
        self.assertFalse(os.path.exists(self.r("reports", "2026-11-02", "after-close.md")))
        self.assertEqual(self.state()["waiting"]["2026-11-02"], "2026-11-02 16:25")
        self.closing_file("2026-11-02")
        out = self.tick("2026-11-02 16:25")
        self.assertIn("After-close check done", out)
        self.assertIn("After-close check: today 4:25 PM (ok)", read(self.r("STATUS.md")))
        self.assertTrue(read(self.r("LATEST.md")).startswith("# After-close check: Monday 2 November 2026"))

    def test_the_morning_job_runs_the_trading_alerts_check_after_the_scan(self):
        self.tick("2026-11-02 08:15")
        calls = self.calls()
        self.assertEqual(calls[:4], ["trading scan daily --quiet", "trading alerts check --source psx-screener",
                                     "trading news --market", "trading insiders new --read"])
        self.assertEqual(calls[4:], ["longterm news ", "longterm insiders new --read"])   # no psx/invest.json yet

    def test_the_check_round_takes_the_new_tape_line(self):
        write_json(self.p("trading", "alerts.json"), {"rules": [{"id": "y"}]})
        self.fake("trading", "alerts", tape="2026-11-02")
        self.tick("2026-11-02 08:15")
        self.clear_calls()
        self.tick("2026-11-02 10:00")
        self.assertEqual(self.calls(), ["trading alerts check --source psx-screener",
                                        "longterm alerts check --source psx-screener --offline"])

    def test_no_page_read_means_the_long_term_reads_the_site(self):
        self.tick("2026-11-02 08:15")                 # the trading folder has no rules, so it read no page
        self.clear_calls()
        self.tick("2026-11-02 10:00")
        self.assertEqual(self.calls(), ["trading alerts check --source psx-screener",
                                        "longterm alerts check --source psx-screener"])

    def test_a_failed_trading_step_means_the_long_term_reads_the_site(self):
        write_json(self.p("trading", "alerts.json"), {"rules": [{"id": "y"}]})
        self.fake("trading", "alerts", exit=2, errors=["DEMO: no screener price (the PSX site could not be read)"])
        self.tick("2026-11-02 08:15")
        self.clear_calls()
        self.tick("2026-11-02 10:00")
        self.assertEqual(self.calls()[-1], "longterm alerts check --source psx-screener")

    def test_an_old_tape_line_or_a_tape_with_no_link_does_not_count(self):
        write_json(self.p("trading", "alerts.json"), {"rules": [{"id": "y"}]})
        self.fake("trading", "alerts", tape="2026-11-02", tape_fetched="2020-01-01T10:00:00+05:00")
        self.tick("2026-11-02 08:15")
        self.clear_calls()
        self.tick("2026-11-02 10:00")
        self.assertEqual(self.calls()[-1], "longterm alerts check --source psx-screener")
        os.remove(self.p("longterm", "data", "tape"))         # no link: the long-term check reads the site itself
        os.makedirs(self.p("longterm", "data", "tape"))
        self.fake("trading", "alerts", tape="2026-11-02")
        self.clear_calls()
        self.tick("2026-11-02 10:05")
        self.assertEqual(self.calls()[-1], "longterm alerts check --source psx-screener")

    def test_three_old_price_rounds_give_one_problem_then_one_an_hour(self):
        write_json(self.p("trading", "alerts.json"), {"rules": [{"id": "y"}]})
        self.fake("trading", "alerts", exit=2, errors=[STALE])
        os.remove(self.p("longterm", "alerts.json"))
        self.tick("2026-11-02 08:15")
        told = []
        for i in range(15):                                    # 10:00 to 11:10, every 5 minutes
            at = (T("2026-11-02 10:00") + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")
            told += [(at, m) for m in message_lines(self.tick(at)) if m[0] == "PSX auto-run: problem"]
        self.assertEqual([at for at, _ in told], ["2026-11-02 10:10", "2026-11-02 11:10"])
        self.assertEqual(told[0][1][1], ["the DEMO stop was not checked for 15 minutes: no price newer than 15 "
                                         "minutes. Check the price in your broker app.", FOOTER])
        self.assertIn("for 75 minutes", told[1][1][1][0])
        self.fake("trading", "alerts")                           # prices are back
        self.assertIn("PSX auto-run: working again", self.titles(self.tick("2026-11-02 11:15")))

    def test_a_missing_long_term_folder_skips_its_steps(self):
        folders = dict(self.folders, longterm="")
        self.settings(folders=folders)
        out = self.tick("2026-11-02 08:15")
        self.assertTrue(all(c.startswith("trading ") for c in self.calls()))
        self.assertNotIn("PSX auto-run: problem", self.titles(out))
        self.assertNotIn("long-term", read(self.r("reports", "2026-11-02", "morning.md")))
        self.clear_calls()
        self.tick("2026-11-02 10:00")
        self.assertEqual(self.calls(), ["trading alerts check --source psx-screener"])

    def test_a_long_term_folder_that_is_gone_is_told_and_the_trading_jobs_still_run(self):
        os.remove(self.p("longterm", "config.json"))
        out = self.tick("2026-11-02 08:15")
        self.assertIn("PSX auto-run: problem", self.titles(out))
        self.assertIn("no psx/config.json in the long-term folder", out)
        self.assertIn("PSX: Morning check done", self.titles(out))
        self.assertTrue(all(c.startswith("trading ") for c in self.calls()))

    def test_the_after_close_report_lists_a_buy_with_no_fill(self):
        write_json(self.p("trading", "config.json"), {"psx_site": True, "paper": False, "style": "swing"})
        write_json(self.p("trading", "state", "broker.json"), contract("broker-placed-unfilled.json"))
        self.closing_file("2026-11-02")
        out = self.tick("2026-11-02 16:10")
        line = ("DEMO: a buy placed on Thu 2026-10-29 has no fill recorded. Record the fill (ticket.py fill), or record "
                "it expired (ticket.py result --fingerprint 0d80ffbb0d898a06 --status expired). New swing buys wait "
                "until then.")
        self.assertIn("- " + line, read(self.r("reports", "2026-11-02", "after-close.md")))
        self.assertIn(("PSX: a buy has no fill recorded", [line, FOOTER], ""), message_lines(out))
        self.assertIn("Paper trading is OFF in the trading folder.", read(self.r("LATEST.md")))
        write_json(self.p("trading", "state", "broker.json"), contract("broker-expired.json"))
        self.closing_file("2026-11-03")
        out = self.tick("2026-11-03 16:10")
        self.assertNotIn("PSX: a buy has no fill recorded", self.titles(out))
        self.assertNotIn("has no fill recorded", read(self.r("reports", "2026-11-03", "after-close.md")))

    def test_a_missing_tool_is_told_and_a_missing_optional_one_is_not(self):
        os.remove(os.path.join(self.pack, "psx-insiders", "insiders.py"))
        os.remove(os.path.join(self.pack, "psx-invest", "invest.py"))
        write_json(self.p("longterm", "invest.json"), {})
        out = self.tick("2026-11-02 08:15")
        report = read(self.r("reports", "2026-11-02", "morning.md"))
        self.assertIn("Insider notices (trading folder): PROBLEM: no tool at", report)
        self.assertNotIn("Long-term signals", report)              # optional, and not in the pack: no noise
        self.assertIn("PSX auto-run: problem", self.titles(out))

    def test_a_blocked_reports_folder_falls_back_and_is_told(self):
        os.makedirs(self.r())
        os.chmod(self.r(), 0o555)                                  # like macOS blocking Documents for the app
        try:
            out = self.tick("2026-11-02 08:15")
        finally:
            os.chmod(self.r(), 0o755)
        fallback = os.path.join(self.tmp, "home", "reports-fallback")
        self.assertTrue(os.path.isfile(os.path.join(fallback, "reports", "2026-11-02", "morning.md")))
        self.assertTrue(os.path.isfile(os.path.join(fallback, "LATEST.md")))
        self.assertIn("reports-fallback", out)
        self.assertIn("PSX auto-run: problem", self.titles(out))

    def test_the_week_report_has_the_cash_line(self):
        write_json(self.p("trading", "portfolio.json"), {"cash": 50000, "positions": []})
        self.tick("2026-11-07 10:00")
        report = read(self.r("reports", "weekly", "week-of-2026-11-07.md"))
        self.assertIn("- Cash: trading Rs 50,000.00, long-term Rs 0.00, together Rs 50,000.00. Does this match your "
                      "broker app? If not: ticket.py cash --set N (trading) or invest.py cash --set N (long-term).",
                      report)
        self.assertIn("Weekdays with PSX closed in the next 7 days: Mon 2026-11-09 (PSX holiday: Iqbal Day).", report)
        self.assertIn("longterm facts SYS --metrics psx/metrics.json", self.calls())
        self.assertIn("- Alerts that auto-run sent this week: 0. An alert from a check you ran yourself, or in the chat, "
                      "is not counted here.", report)
        self.assertIn("- Results of your long-term shares: Results read for 1 share (SYS). Saved in psx/metrics.json.",
                      report)

    def test_a_line_for_claude_is_said_to_you_in_the_report(self):
        link, where = "https://dps.psx.com.pk/download/document/286309.pdf", "psx/data/docs/286309.pdf"
        self.fake("longterm", "facts", lines=["Ask the user to open %s in their browser and save it as %s, or paste a "
                                              "screenshot. Do not fetch it yourself." % (link, where)])
        self.tick("2026-11-07 10:00")
        report = read(self.r("reports", "weekly", "week-of-2026-11-07.md"))
        self.assertIn("To read it, open %s in your browser and save it as %s, or paste a screenshot in the chat. Claude "
                      "does not fetch it." % (link, where), report)
        self.assertNotIn("Ask the user", report)
        self.assertIn("Ask the user to open %s" % link, read(self.r("reports", "weekly", "week-of-2026-11-07-full.txt")))


class TestHolidaysAndPauses(Tick):
    AHEAD = ("Tomorrow, Fri 2027-03-05, is an expected PSX holiday (Juma-tul-Wida). Moon dates can move. If PSX says "
             "it is open, add the day under \"open_days\" in the trading folder's psx/config.json. Auto-run then runs "
             "as normal.")
    OPEN = ("PSX looks open today, Fri 2027-03-05, but the calendar has it as a holiday. Add the day under "
            "\"open_days\" in psx/config.json. Until then auto-run checks nothing today.")

    def test_an_expected_holiday_is_told_the_evening_before(self):
        self.closing_file("2027-03-04")
        out = self.tick("2027-03-04 14:10")                    # a Ramadan Thursday: the after-close time
        self.assertIn(("PSX: tomorrow may be a holiday", [self.AHEAD, FOOTER], ""), message_lines(out))
        self.assertIn("- " + self.AHEAD, read(self.r("reports", "2027-03-04", "after-close.md")))

    def held(self):
        write_json(self.p("trading", "portfolio.json"), {"cash": 0, "positions": [{"ticker": "DEMO", "shares": 100}]})

    def test_the_holiday_look_sees_today_and_runs_no_checks(self):
        self.held()
        self.fake("trading", "alerts", quote_time="2027-03-05T05:05:00Z")       # 10:05 AM Karachi time, today
        self.assertEqual(self.tick("2027-03-05 09:55"), "")
        out = self.tick("2027-03-05 10:00")
        self.assertEqual(message_lines(out), [("PSX looks open today", [self.OPEN, FOOTER], "Glass")])
        self.assertEqual(self.calls(), ["trading alerts quote DEMO --source psx-company --json"])
        self.assertIn(self.OPEN, read(self.r("reports", "2027-03-05", "checks.md")))
        self.assertEqual(self.tick("2027-03-05 10:05"), "")    # one look a day, and no checks
        self.assertEqual(len(self.calls()), 1)

    def test_the_holiday_look_with_yesterdays_quote_says_nothing(self):
        self.held()
        self.fake("trading", "alerts", quote_time="2027-03-04T08:30:00Z")
        self.assertEqual(self.tick("2027-03-05 10:00"), "")
        self.assertEqual(len(self.calls()), 1)

    def test_no_look_with_the_site_off_or_nothing_held(self):
        self.fake("trading", "alerts", quote_time="2027-03-05T05:05:00Z")
        write_json(self.p("longterm", "portfolio.json"), {"positions": []})
        self.assertEqual(self.tick("2027-03-05 10:00"), "")      # nothing held in either folder
        self.assertEqual(self.calls(), [])
        shutil.rmtree(os.path.join(self.tmp, "home"))
        self.held()
        write_json(self.p("trading", "config.json"), {"psx_site": False, "paper": True})
        self.assertEqual(self.tick("2027-03-05 10:00"), "")      # the site is off
        self.assertEqual(self.calls(), [])

    def test_the_probe_request_writes_the_probe_file_and_nothing_else(self):
        os.makedirs(os.path.join(self.tmp, "home", "state"))
        with open(os.path.join(self.tmp, "home", "state", "probe.request"), "w") as fh:
            fh.write("probe-123\n")
        self.assertEqual(self.tick("2026-11-02 08:15"), "")
        self.assertIn("probe-123", read(self.r("autorun-probe.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "home", "state", "probe.request")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "home", "state", "state.json")))
        self.assertEqual(self.calls(), [])
        self.assertFalse(os.path.exists(self.r("reports")))
        self.assertIn("PSX: Morning check done", self.titles(self.tick("2026-11-02 08:15")))   # the next tick works

    def test_pause_all_for_ten_minutes_ends_by_itself(self):
        rc, out = self.run_cmd("pause", "all", "--minutes", "10", "--now", "2026-11-02 08:10")
        self.assertIn("Auto-run is paused for 10 minutes, until 8:20 AM.", out)
        self.assertEqual(self.tick("2026-11-02 08:15"), "")
        self.assertIn("PSX: Morning check done", self.titles(self.tick("2026-11-02 08:20")))
        self.assertNotIn("pause", self.state())
        os.environ["PSX_NOTIFY"] = "print"          # by hand: a plain error and exit 1
        rc, out = self.run_cmd("pause", "checks", "--minutes", "5")
        self.assertEqual(rc, 1)
        self.assertIn("--minutes goes with pause all", out)

    def test_resume_undoes_pause_all_first_then_pause_checks(self):
        self.run_cmd("pause", "checks", "--now", "2026-11-02 08:00")
        self.run_cmd("pause", "all", "--minutes", "10", "--now", "2026-11-02 08:00")
        rc, out = self.run_cmd("resume", "--now", "2026-11-02 08:01")
        self.assertIn("Price checks stay off today", out)
        self.assertEqual(self.state()["pause"], {"checks_off": "2026-11-02"})
        rc, out = self.run_cmd("resume", "--now", "2026-11-02 08:02")
        self.assertIn("Price checks are back on today.", out)
        self.assertNotIn("pause", self.state())
        self.assertIn("Nothing was paused.", self.run_cmd("resume")[1])

    def test_selftest_status_and_pause_checks(self):
        rc, out = self.run_cmd("selftest")
        self.assertEqual(rc, 0)
        self.assertIn("notify\tPSX auto-run works", out)
        self.assertTrue(read(os.path.join(self.tmp, "home", "selftest.txt")).startswith("OK\n"))
        self.run_cmd("pause", "checks", "--now", "2026-11-02 09:00")
        self.tick("2026-11-02 08:15")
        self.assertNotIn("PSX alert", self.tick("2026-11-02 10:00"))
        self.run_cmd("resume", "--now", "2026-11-02 10:01")
        self.assertIn("PSX alert", self.tick("2026-11-02 10:05"))
        os.environ["PSX_NOTIFY"] = "print"
        rc, out = self.run_cmd("status", "--now", "2026-11-02 10:06")
        self.assertIn("- Morning check: today 8:15 AM (ok)", out)
        self.assertIn("price checks every 5 minutes", out)
        rc, out = self.run_cmd("tick", "--dry-run", "--now", "2026-11-02 16:10")
        self.assertIn("due: after_close for 2026-11-02", out)

    def test_the_command_words_pass_the_word_test(self):
        os.environ["PSX_NOTIFY"] = "print"
        outs = [self.run_cmd(*args)[1] for args in (
            ("selftest",), ("plan", "--now", "2026-11-06 07:00"), ("plan", "--date", "2027-03-05"),
            ("pause", "checks", "--now", "2026-11-02 09:00"), ("pause", "all", "--minutes", "10"), ("resume",),
            ("resume",), ("resume",), ("tick", "--dry-run", "--now", "2026-11-02 08:15"),
            ("status", "--now", "2026-11-02 08:20"), ("run", "morning", "--now", "2026-11-02 08:30"))]
        words = [str(e) for e in self.errors()]
        for text in outs + words:
            check_words(self, text)
        self.assertGreater(sum(len(x.splitlines()) for x in outs), 30)

    def errors(self):
        found = []
        for bad in (lambda: A.check_safe({"script": "psx-broker/ticket.py"}),
                    lambda: A.check_safe({"script": "psx-invest/invest.py", "args": ["orders"]}),
                    lambda: A.check_safe({"script": "../x.py"}), lambda: A.now_karachi("soon"),
                    lambda: A.load_config(os.path.join(self.tmp, "missing.json")), lambda: A.by_date("today", None)):
            try:
                bad()
            except A.AutorunError as e:
                found.append(e)
        self.assertEqual(len(found), 6)
        return found

    def test_a_missing_pack_is_told(self):
        doc = json.loads(read(self.cfg_path))
        doc["pack"] = os.path.join(self.tmp, "missing-pack")
        write_json(self.cfg_path, doc)
        self.assertIn("notify\tPSX auto-run: problem\tno PSX tools at", self.tick("2026-11-02 08:15"))

    def test_another_poller_means_offline(self):
        A.other_pollers = lambda: [4242]     # a tape or an entry watch is using the PSX site
        self.tick("2026-11-02 08:15")
        self.tick("2026-11-02 10:00")
        self.assertIn("longterm alerts check --source psx-screener --offline", self.calls())
        self.assertIn("trading news --market --no-notices --no-moves", self.calls())
        self.assertIn("Another PSX tool was using the PSX site", read(self.r("reports", "2026-11-02", "morning.md")))

    def test_the_calendar_mismatch_is_noted(self):
        write_json(self.p("longterm", "config.json"),
                   {"psx_site": True, "paper": True, "holidays": {"2026-11-03": "a made-up closure"}})
        self.tick("2026-11-02 08:15")
        self.assertIn("list different holidays or Ramadan dates", read(self.r("reports", "2026-11-02", "morning.md")))


class TestReportsAndWords(Tick):
    def busy_day(self):
        """A live swing day: a stop ring, an old price, a halt story, a buy with no fill, a signal change."""
        write_json(self.p("trading", "config.json"), {"psx_site": True, "paper": False, "style": "swing"})
        write_json(self.p("trading", "portfolio.json"), contract("portfolio-swing.json"))
        write_json(self.p("trading", "state", "broker.json"), contract("broker-placed-unfilled.json"))
        write_json(self.p("trading", "alerts.json"), {"rules": [{"id": "y"}]})
        write_json(self.p("longterm", "invest.json"), {})
        self.fake("trading", "alerts", rings=[STOP_RING], tape="2026-11-02")
        self.fake("trading", "news", halt=True)
        self.fake("longterm", "invest", lines=["TRIM    ZCAP  27.1%  over the 25% trim line (rule 5)",
                                               "HOLD    ZUP   10.0%  no rule asks for a change",
                                               "notify: ZCAP: a sell rule is met (TRIM). Open the report. You decide."])
        saved = A.Notes.ordered                 # show every message here (the cut to 4 has its own test)
        A.Notes.ordered = lambda n: [(t, x, s) for _, t, x, s in sorted(n.items, key=lambda i: i[0])]
        try:
            outs = [self.tick("2026-11-02 08:15"), self.tick("2026-11-02 10:00")]
            self.fake("trading", "alerts", exit=2, errors=[STALE])
            outs += [self.tick(at) for at in ("2026-11-02 10:05", "2026-11-02 10:10", "2026-11-02 10:15")]
            self.fake("trading", "alerts", rings=[STOP_RING])
            self.closing_file("2026-11-02")
            outs.append(self.tick("2026-11-02 16:10"))
            write_json(self.p("trading", "portfolio.json"), {"cash": 50000, "positions": []})
            outs.append(self.tick("2026-11-07 10:00"))
        finally:
            A.Notes.ordered = saved
        return outs

    def test_every_report_line_and_message_passes_the_word_test(self):
        outs = self.busy_day()
        titles = [t for out in outs for t in self.titles(out)]
        for t in ("PSX alert", "PSX: a market halt is in the news", "PSX auto-run: problem", "PSX signal",
                  "PSX: a buy has no fill recorded", "PSX: Morning check done", "PSX: After-close check done",
                  "PSX: Week report ready"):
            self.assertIn(t, titles)
        checked = 0
        for out in outs:
            for title, lines, sound in message_lines(out):
                self.assertEqual(lines[-1], FOOTER, title)
                checked += check_words(self, "\n".join([title] + lines))
        for base, dirs, files in os.walk(self.r()):
            for f in files:
                checked += check_words(self, read(os.path.join(base, f)))
        self.assertGreater(checked, 100)
        morning = read(self.r("reports", "2026-11-02", "morning.md"))
        for words in ("Put in your stop-limit after the opening match. psx-alerts reminds you at the first check. No "
                      "order guards the opening price: a gap at the open can go past your stop.",
                      "Paper trading is OFF in the trading folder.",
                      "A market halt is in the news. A halt cancels all open orders at PSX. Check your orders in your "
                      "broker app.", "DEMO: you hold 1,436 shares (swing). Plan stop 96.25",
                      "Long-term signals: TRIM: ZCAP. HOLD: 1. Rules, not predictions."):
            self.assertIn(words, morning)
        self.assertNotIn("FS EDGE", morning)

    def test_the_paper_words_and_a_late_report(self):
        write_json(self.p("trading", "config.json"), {"psx_site": True, "paper": True, "style": "swing"})
        write_json(self.p("trading", "portfolio.json"), contract("portfolio-swing.json"))
        self.tick("2026-11-02 11:00")
        morning = read(self.r("reports", "2026-11-02", "morning.md"))
        self.assertIn("Paper: no stop-limit rests at a broker. Auto-run checks the stop every 5 minutes in the "
                      "session.", morning)
        self.assertIn("This ran late, at 11:00 AM. The Mac was asleep or off at the planned time.", morning)

    def test_every_file_stays_in_the_temp_folders(self):
        before = set(os.listdir(self.tmp))
        self.busy_day()
        self.assertTrue(A.home_dir().startswith(self.tmp) and A.log_dir().startswith(self.tmp))
        self.assertEqual(set(os.listdir(self.tmp)) - before, {"home", "logs", "reports"})
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "logs", "autorun.log")))
        for folder in self.folders.values():           # no order file in a psx/ folder
            self.assertFalse(os.path.exists(os.path.join(folder, "psx", "orders.json")))

    def test_a_dry_run_runs_nothing_and_writes_nothing(self):
        rc, out = self.run_cmd("tick", "--dry-run", "--now", "2026-11-02 08:15")
        self.assertEqual(out.strip(), "due: morning for 2026-11-02")
        self.assertEqual(self.calls(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "home", "state", "state.json")))

    def test_a_broken_state_file_is_moved_aside(self):
        os.makedirs(os.path.join(self.tmp, "home", "state"))
        with open(os.path.join(self.tmp, "home", "state", "state.json"), "w") as fh:
            fh.write("{broken")
        self.assertIn("PSX: Morning check done", self.titles(self.tick("2026-11-02 08:15")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "home", "state", "state.json.bad")))

    def test_a_busy_lock_ends_the_tick(self):
        lock = A.Lock("run.lock")
        self.assertTrue(lock.acquire())
        try:
            self.assertEqual(self.tick("2026-11-02 08:15"), "")
            self.assertEqual(self.calls(), [])
        finally:
            lock.release()

    def test_run_by_hand_works_while_paused_and_says_so(self):
        self.run_cmd("pause", "all", "--now", "2026-11-02 08:00")
        rc, out = self.run_cmd("run", "morning", "--now", "2026-11-02 08:05")
        self.assertIn("Done. The report is in", out)
        self.assertIn("This run was started by hand, at 8:05 AM.", read(self.r("reports", "2026-11-02", "morning.md")))
        self.assertEqual(self.tick("2026-11-02 08:15"), "")          # still paused for the timer

    def test_keep_awake_only_in_its_window(self):
        seen = []
        A.start_caffeinate = lambda seconds: seen.append(seconds)
        try:
            cfg = A.deep_merge(A.DEFAULTS, {"keep_awake": True})
            plan = A.day_plan(cfg, {"segments": NORMAL}, D("2026-11-02"))
            for at in ("2026-11-02 08:09", "2026-11-02 08:10", "2026-11-02 16:29", "2026-11-02 16:30"):
                A.keep_awake(cfg, plan, T(at))
            self.assertEqual(seen, [400, 400])
            A.keep_awake(A.deep_merge(cfg, {"keep_awake": False}), plan, T("2026-11-02 10:00"))
            A.keep_awake(cfg, None, T("2026-11-08 10:00"))
            self.assertEqual(seen, [400, 400])
        finally:
            A.start_caffeinate = forbidden("start_caffeinate")

    def test_the_tape_is_for_the_day_style_only(self):
        cfg = A.load_config(self.cfg_path)
        cfg["tape"] = True
        plan = A.day_plan(cfg, {"segments": NORMAL}, D("2026-11-02"))
        self.assertTrue(A.tape_wanted(cfg, {}, plan, T("2026-11-02 09:20")))
        self.assertFalse(A.tape_wanted(cfg, {}, plan, T("2026-11-02 15:41")))
        self.assertFalse(A.tape_wanted(cfg, {"pause": {"checks_off": "2026-11-02"}}, plan, T("2026-11-02 09:20")))
        write_json(self.p("trading", "config.json"), {"style": "swing"})
        self.assertFalse(A.tape_wanted(cfg, {}, plan, T("2026-11-02 09:20")))
        cfg["tape"] = False
        write_json(self.p("trading", "config.json"), {})
        self.assertFalse(A.tape_wanted(cfg, {}, plan, T("2026-11-02 09:20")))


def done_text(out, title):
    """The text of one counts message ("PSX: Morning check done", ...) in a tick's output."""
    found = [lines[0] for t, lines, _ in message_lines(out) if t == title]
    return found[0] if found else ""


class TestBrokenUserFiles(Tick):
    """A file you edit by hand (a ledger, a config, broker.json) that is not valid JSON keeps its name.
    Auto-run tells it, counts it, and keeps it open until the file reads again."""

    BAD = '{\n  cash: 40000.0,\n  "positions": []\n}\n'          # a typo: a key with no quotes

    def break_file(self, folder, *parts):
        path = self.p(folder, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(self.BAD)
        return path

    def kept(self, path):
        self.assertTrue(os.path.isfile(path), path)
        self.assertEqual(read(path), self.BAD)
        self.assertFalse(os.path.exists(path + ".bad"), path)

    def test_a_broken_ledger_keeps_its_name_and_stays_a_problem_until_it_is_fixed(self):
        path = self.break_file("trading", "portfolio.json")
        words = ("psx/portfolio.json in the trading folder is not valid JSON (line 2, column 3). Auto-run did not "
                 "change it. Fix the file.")
        out = self.tick("2026-11-02 08:15")
        self.kept(path)
        self.assertIn(("PSX auto-run: problem", [words, FOOTER], "Glass"), message_lines(out))
        self.assertIn("Problems: 1.", done_text(out, "PSX: Morning check done"))
        report = read(self.r("reports", "2026-11-02", "morning.md"))
        self.assertIn("- PROBLEM: " + words, report)
        self.assertIn("- Problems: 1 (each one is in a line above).", report)
        for at in ("2026-11-02 10:00", "2026-11-02 10:05"):
            self.tick(at)
        self.closing_file("2026-11-02")
        self.tick("2026-11-02 16:10")
        self.kept(path)
        self.assertIn("- PROBLEM: " + words, read(self.r("reports", "2026-11-02", "after-close.md")))
        self.assertIn("- Problems now: " + words, read(self.r("STATUS.md")))
        write_json(path, {"cash": 40000.0, "positions": []})         # fixed by hand
        out = self.tick("2026-11-03 08:15")
        self.assertIn(("PSX auto-run: working again", ["psx/portfolio.json in the trading folder is fixed.", FOOTER],
                       ""), message_lines(out))
        self.assertIn("- Problems now: none", read(self.r("STATUS.md")))
        self.assertIn("Problems: 0.", done_text(out, "PSX: Morning check done"))

    def test_a_broken_long_term_ledger_in_the_week_report(self):
        path = self.break_file("longterm", "portfolio.json")
        out = self.tick("2026-11-07 10:00")
        self.kept(path)
        report = read(self.r("reports", "weekly", "week-of-2026-11-07.md"))
        why = "is not valid JSON (line 2, column 3)"
        self.assertIn("- PROBLEM: psx/portfolio.json in the long-term folder %s." % why, report)
        self.assertIn("- Results of your long-term shares: nothing to check (psx/portfolio.json %s, so no share was "
                      "read)." % why, report)
        self.assertIn("long-term not read (psx/portfolio.json %s)" % why, report)
        self.assertIn("Problems: 1.", done_text(out, "PSX: Week report ready"))
        self.assertNotIn("longterm facts", " ".join(self.calls()))

    def test_a_broken_long_term_config_and_broker_file_keep_their_names(self):
        config = self.break_file("longterm", "config.json")
        broker = self.break_file("trading", "state", "broker.json")
        self.fake("longterm", "alerts")          # no ring here: only 4 messages show in one go
        self.closing_file("2026-11-02")
        out = self.tick("2026-11-02 16:10")
        self.kept(config)
        self.kept(broker)
        report = read(self.r("reports", "2026-11-02", "after-close.md"))
        self.assertIn("- PROBLEM: psx/config.json in the long-term folder is not valid JSON", report)
        self.assertIn("- PROBLEM: psx/state/broker.json in the trading folder is not valid JSON", report)
        self.assertNotIn("The PSX site is off in the long-term folder", report)     # broken, not off
        self.assertNotIn("no psx/config.json in the long-term folder", out)
        self.assertIn("Problems: 2.", done_text(out, "PSX: After-close check done"))
        for line in report.splitlines():
            check_words(self, line)

    def test_auto_run_still_moves_aside_only_its_own_broken_files(self):
        path = self.break_file("trading", "portfolio.json")
        os.makedirs(os.path.join(self.tmp, "home", "state"))
        with open(os.path.join(self.tmp, "home", "state", "state.json"), "w") as fh:
            fh.write("{broken")
        self.tick("2026-11-02 08:15")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "home", "state", "state.json.bad")))
        self.kept(path)


class TestNewsFeeds(Tick):
    def test_no_feed_answered_is_a_problem_not_nothing_new(self):
        self.fake("trading", "news", feeds_down=5)
        out = self.tick("2026-11-02 08:15")
        report = read(self.r("reports", "2026-11-02", "morning.md"))
        self.assertIn("- News and market news (trading folder): PROBLEM: no news feed answered (5 of 5). Check the "
                      "Mac's internet.", report)
        self.assertIn("PSX auto-run: problem", self.titles(out))
        self.assertIn("Problems: 1.", done_text(out, "PSX: Morning check done"))
        self.fake("trading", "news", feeds_down=2)                 # some feeds answer again
        self.closing_file("2026-11-02")
        out = self.tick("2026-11-02 16:10")
        report = read(self.r("reports", "2026-11-02", "after-close.md"))
        self.assertIn("- News and market news (trading folder): 1 item to read. 2 of 5 news feeds did not answer.",
                      report)
        self.assertIn("Problems: 0.", done_text(out, "PSX: After-close check done"))


class TestAfterClose(Tick):
    NOT_FOUND = ("The closing file of Mon 2026-11-02 was not found. Was PSX closed that day? If so, add the day under "
                 "\"holidays\" in psx/config.json.")

    def test_an_early_run_by_hand_does_not_cancel_the_after_close_job(self):
        self.tick("2026-11-02 08:15")
        rc, out = self.run_cmd("run", "after_close", "--now", "2026-11-02 15:45")     # asked for right after the close
        self.assertEqual(rc, 0)
        self.assertIn("- The closing file of Mon 2026-11-02 is not out yet. This run used the closing files that are "
                      "there. The after-close check still runs by itself when the file is out.",
                      read(self.r("reports", "2026-11-02", "after-close.md")))
        rc, out = self.run_cmd("tick", "--dry-run", "--now", "2026-11-02 16:10")
        self.assertIn("due: after_close for 2026-11-02", out)
        self.tick("2026-11-02 16:10")                               # no closing file yet: it waits
        self.assertEqual(self.state()["waiting"]["2026-11-02"], "2026-11-02 16:25")
        self.run_cmd("run", "after_close", "--now", "2026-11-02 16:20")   # by hand again, in the retry window
        self.assertEqual(self.state()["waiting"]["2026-11-02"], "2026-11-02 16:25")
        self.assertFalse(self.state()["done"]["2026-11-02"].get("after_close"))
        self.closing_file("2026-11-02")
        self.assertIn("PSX: After-close check done", self.titles(self.tick("2026-11-02 16:25")))
        self.assertTrue(self.state()["done"]["2026-11-02"].get("after_close"))

    def test_a_closing_file_that_never_comes_is_counted_and_ends_when_it_comes(self):
        self.tick("2026-11-02 16:10")
        out = self.tick("2026-11-02 19:40")                          # past the 4 hours
        self.assertIn(("PSX auto-run: problem", [self.NOT_FOUND, FOOTER], "Glass"), message_lines(out))
        self.assertIn("Problems: 1.", done_text(out, "PSX: After-close check done"))
        report = read(self.r("reports", "2026-11-02", "after-close.md"))
        self.assertIn("- " + self.NOT_FOUND, report)
        self.assertIn("- Problems: 1 (each one is in a line above).", report)
        status = read(self.r("STATUS.md"))
        self.assertIn("- After-close check: today 7:40 PM (problems)", status)
        self.assertIn("- Problems now: " + self.NOT_FOUND, status)
        self.closing_file("2026-11-02")                               # it comes after all
        out = self.tick("2026-11-03 08:15")
        self.assertIn(("PSX auto-run: working again", ["The closing file of Mon 2026-11-02 is there now.", FOOTER], ""),
                      message_lines(out))
        self.assertIn("- Problems now: none", read(self.r("STATUS.md")))

    def test_the_after_close_job_names_a_met_swing_rule_and_a_part_fill(self):
        # F13 and F60 in one after-close job: the stop rang at 10:00 AM from a quote, so the job's check rings
        # nothing new; and 100 of today's 253-share buy are recorded
        write_json(self.p("trading", "config.json"),
                   {"psx_site": True, "paper": False, "style": "swing", "holidays": {}})
        write_json(self.p("trading", "portfolio.json"), contract("portfolio-swing.json"))
        write_json(self.p("trading", "alerts.json"), {"rules": []})
        met = ("DEMO: the stop rule was met on Mon 2026-11-02. It rang today at 10:00 AM PKT. You still hold 1,436 "
               "shares in psx/portfolio.json.")
        self.fake("trading", "alerts", nothing="No new alert. 3 rule(s) watched today, DEMO 95.60 (as of 3:30 PM PKT)."
                                               "\n" + met, notes=["DEMO: checked the closing file of Mon 2026-11-02."])
        rec = {"date": "2026-11-02", "index": 1, "fingerprint": "5e5e5e5e5e5e5e5e", "symbol": "EPCL", "side": "buy",
               "qty": 253, "kind": "entry", "entry_ref": 33.05}
        write_json(self.p("trading", "state", "broker.json"), {"orders": [
            dict(rec, at="2026-11-02T09:30:00+05:00", status="approved"),
            dict(rec, at="2026-11-02T09:40:00+05:00", status="placed"),
            {"at": "2026-11-02T09:40:00+05:00", "date": "2026-11-02", "status": "filled", "symbol": "EPCL",
             "side": "buy", "qty": 100, "price": 33.05}]})
        self.closing_file("2026-11-02")
        out = self.tick("2026-11-02 16:10")
        report = read(self.r("reports", "2026-11-02", "after-close.md"))
        self.assertIn("- Alerts on the close (trading folder): no new alert rang (3 rules watched). A swing rule is "
                      "still met. " + met, report)
        self.assertIn("- EPCL: a buy placed on Mon 2026-11-02 is filled in part: 100 of 253 shares are recorded.",
                      report)
        self.assertNotIn("PSX: a buy has no fill recorded", self.titles(out))       # it has a fill
        self.assertEqual(done_text(out, "PSX: After-close check done"),
                         "Swing rules still met: 1. Setups: 2. News: 2 new. Problems: 0. Click to open the report.")
        self.closing_file("2026-11-03")
        self.fake("trading", "alerts", nothing="Nothing hit. 3 rule(s) watched today.")
        write_json(self.p("trading", "state", "alerts.json"), {})
        self.tick("2026-11-03 16:10")                                 # the next day: the rest ended at the close
        self.assertNotIn("filled in part", read(self.r("reports", "2026-11-03", "after-close.md")))

    def test_a_closing_file_problem_ends_when_the_day_is_a_holiday_in_the_config(self):
        self.tick("2026-11-02 16:10")
        self.tick("2026-11-02 19:40")
        write_json(self.p("trading", "config.json"),
                   {"psx_site": True, "paper": True, "holidays": {"2026-11-02": "a made-up closure"}})
        EXTRA_CLOSED["2026-11-02"] = "PSX holiday: a made-up closure"
        out = self.tick("2026-11-03 08:15")
        self.assertIn(("PSX auto-run: working again", ["Mon 2026-11-02 is a PSX holiday in psx/config.json now.",
                                                      FOOTER], ""), message_lines(out))


class TestProblemsEnd(Tick):
    def test_a_blocked_reports_folder_is_counted_then_working_again(self):
        os.makedirs(self.r())
        os.chmod(self.r(), 0o555)
        try:
            out = self.tick("2026-11-02 08:15")
        finally:
            os.chmod(self.r(), 0o755)
        self.assertIn("Problems: 1.", done_text(out, "PSX: Morning check done"))
        fallback = read(os.path.join(self.tmp, "home", "reports-fallback", "reports", "2026-11-02", "morning.md"))
        self.assertIn("- " + A.FALLBACK_WORDS, fallback)
        self.assertIn("- Problems: 1 (each one is in a line above).", fallback)
        out = self.tick("2026-11-02 10:00")                          # the folder takes files again
        self.assertIn(("PSX auto-run: working again", ["The reports go to your reports folder again.", FOOTER], ""),
                      message_lines(out))
        self.assertIn("- Problems now: none", read(self.r("STATUS.md")))

    def test_a_job_stopped_by_the_clock_ends_with_working_again(self):
        self.closing_file("2026-11-02")
        fail = [True]

        def clock(cfg, d):
            if fail[0] and d > D("2026-11-02"):                    # the look at the days ahead fails once
                raise A.AutorunError("the PSX clock did not answer: timed out")
            return fake_clock(cfg, d)
        A.ask_clock = clock
        out = self.tick("2026-11-02 16:10")
        self.assertIn(("PSX auto-run: problem", ["After-close check: the PSX clock did not answer: timed out", FOOTER],
                       "Glass"), message_lines(out))
        fail[0] = False
        out = self.tick("2026-11-02 16:15")
        self.assertIn(("PSX auto-run: working again", ["After-close check works again.", FOOTER], ""), message_lines(out))
        self.assertNotIn("job:after_close", self.state().get("problems") or {})

    def test_a_tape_that_did_not_start_ends_with_working_again(self):
        self.settings(tape=True)
        tries = []

        def kick(label):
            tries.append(label)
            if len(tries) == 1:
                raise OSError("launchctl did not run")
        saved, A.kickstart = A.kickstart, kick
        try:
            first = self.tick("2026-11-02 09:20")
            second = self.tick("2026-11-02 09:25")
        finally:
            A.kickstart = saved
        self.assertEqual(tries, [A.LABEL_TAPE, A.LABEL_TAPE])
        self.assertIn(("PSX auto-run: problem", ["The tape recorder did not start: launchctl did not run", FOOTER],
                       "Glass"), message_lines(first))
        self.assertIn(("PSX auto-run: working again", ["The tape recorder starts again.", FOOTER], ""),
                      message_lines(second))


class TestCheckRounds(Tick):
    OLD = "UBL: the data is 55 minutes old (as of 9:35 AM PKT). Save a new page, or check the price in your broker app."

    def every_5(self, start, n):
        return [(T(start) + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M") for i in range(n)]

    def test_an_entry_watch_leaves_old_prices_and_that_is_told(self):
        write_json(self.p("trading", "alerts.json"), {"rules": [{"id": "ubl-stop-375.00"}]})
        self.fake("trading", "alerts", nothing="Nothing hit. 2 rule(s) watched today, UBL 380.00 (as of 9:35 AM PKT).",
                  notes=[self.OLD])
        A.other_pollers = lambda: [4242]          # an entry watch runs: it saves its own share's page, no market tape
        self.tick("2026-11-02 08:15")
        told = []
        for at in self.every_5("2026-11-02 10:30", 4):
            told += [(at, m[1]) for m in message_lines(self.tick(at)) if m[0] == "PSX auto-run: problem"]
        self.assertEqual(told, [("2026-11-02 10:40", ["the UBL alerts had no new price for 15 minutes: the newest one "
                                                       "is 55 minutes old. Check the price in your broker app.",
                                                       FOOTER])])
        self.assertIn("trading alerts check --source psx-screener --offline", self.calls())
        self.assertIn("- Price check: today 10:45 AM (problems). Another PSX tool was running (a tape or an entry "
                      "watch), so it read saved prices only.", read(self.r("STATUS.md")))
        checks = read(self.r("reports", "2026-11-02", "checks.md"))
        self.assertIn("- PROBLEM: Alerts (trading folder): no new price for UBL (the newest one is 55 minutes old)",
                      checks)
        self.assertIn("- " + A.BUSY_WORDS, checks)
        self.fake("trading", "alerts")                              # new prices again
        self.assertIn("PSX auto-run: working again", self.titles(self.tick("2026-11-02 10:50")))

    def test_the_last_check_after_the_close_never_says_working_again(self):
        write_json(self.p("trading", "alerts.json"), {"rules": [{"id": "y"}]})
        self.fake("trading", "alerts", exit=2, errors=[STALE])
        os.remove(self.p("longterm", "alerts.json"))
        self.tick("2026-11-02 08:15")
        told = [m for at in self.every_5("2026-11-02 15:10", 3) for m in self.titles(self.tick(at))]
        self.assertIn("PSX auto-run: problem", told)
        self.fake("trading", "alerts", notes=[self.OLD])             # after the close: no D54 check, an old note
        out = self.tick("2026-11-02 15:40")                         # the last check of the day
        self.assertNotIn("PSX auto-run: working again", self.titles(out))
        self.assertNotIn("PSX auto-run: problem", self.titles(out))
        self.assertIn("the DEMO stop was not checked for 15 minutes", read(self.r("STATUS.md")))
        self.fake("trading", "alerts")
        out = self.tick("2026-11-03 09:40")                         # the next session, a new price
        self.assertIn("PSX auto-run: working again", self.titles(out))

    def test_a_ring_in_a_check_round_reaches_latest_and_status(self):
        self.tick("2026-11-02 08:15")
        opened = []
        A.open_file = lambda path: opened.append(path)
        try:
            out = self.tick("2026-11-02 10:00")                     # the long-term SYS rule rings
            self.run_cmd("open-latest")
        finally:
            A.open_file = forbidden("open_file")
        self.assertIn("PSX alert", self.titles(out))
        latest = read(self.r("LATEST.md"))
        self.assertTrue(latest.startswith("# Price checks: Monday 2 November 2026\n\nThe newest report: today's price "
                                          "checks that rang or had a problem, oldest first."), latest[:200])
        self.assertIn("- SYS 105.00 is at or below your below 105.05.", latest)
        self.assertTrue(latest.rstrip().endswith(FOOTER))
        self.assertEqual(opened, [self.r("LATEST.md")])
        self.assertIn("- Alerts that rang today: 1 (each one is in reports/2026-11-02/checks.md)",
                      read(self.r("STATUS.md")))
        self.tick("2026-11-02 15:40")                               # the last check rings it again (made-up tool)
        self.closing_file("2026-11-02")
        self.tick("2026-11-02 16:10")
        report = read(self.r("reports", "2026-11-02", "after-close.md"))
        self.assertIn("- Alerts that rang today in the price checks: 2 (each one is in reports/2026-11-02/checks.md).",
                      report)
        self.assertTrue(read(self.r("LATEST.md")).startswith("# After-close check: Monday 2 November 2026"))
        for line in latest.splitlines() + report.splitlines():
            check_words(self, line)

    def test_a_pause_of_the_checks_is_said_in_the_reports(self):
        self.run_cmd("pause", "checks", "--now", "2026-11-02 08:00")
        self.tick("2026-11-02 08:15")
        self.assertIn("- Price checks are off today (autorun.py pause checks): auto-run checks no alert and no stop in "
                      "the session.", read(self.r("reports", "2026-11-02", "morning.md")))
        self.closing_file("2026-11-02")
        self.tick("2026-11-02 16:10")
        self.assertIn("- Price checks were off today (autorun.py pause checks): auto-run checked no alert and no stop",
                      read(self.r("reports", "2026-11-02", "after-close.md")))
        os.environ["PSX_NOTIFY"] = "print"
        rc, out = self.run_cmd("pause", "checks", "--now", "2026-11-03 08:00")
        self.assertIn("auto-run checks no alert and no stop", out)


class TestPlanAndStatus(Tick):
    def test_the_status_says_how_the_checks_read_prices(self):
        cfg = A.load_config(self.cfg_path)
        self.assertEqual(A.tape_words(cfg), "off. The price checks read the PSX site every 5 minutes: the screener "
                                            "page, and the indices page while a swing share is held")
        cfg["check_every_min"] = 10
        self.assertIn("every 10 minutes", A.tape_words(cfg))
        write_json(self.p("trading", "config.json"), {"psx_site": False, "paper": True})
        self.assertEqual(A.tape_words(cfg), "off. The PSX site is off in the trading folder, so the price checks read "
                                            "only saved files")
        self.tick("2026-11-02 08:15")
        self.assertIn("- Tape recorder: off. The PSX site is off in the trading folder, so the price checks read only "
                      "saved files", read(self.r("STATUS.md")))
        cfg["tape"] = True
        self.assertEqual(A.tape_words(cfg), "on (day style): the price checks read the tape")

    def test_the_week_report_line_is_in_the_plan_of_its_day(self):
        cfg = A.load_config(self.cfg_path)
        self.assertEqual(A.plan_lines(cfg, None, D("2026-11-07"), {"reason": "weekend"}),
                         ["PSX is closed on Saturday 7 November 2026 (weekend). No market jobs.", "10:00 AM  week report"])
        cfg["weekly"] = {"weekday": 0, "time": "10:00"}             # a Monday: it waits for the close
        plan = A.day_plan(cfg, {"segments": NORMAL}, D("2026-11-02"))
        self.assertEqual(A.plan_lines(cfg, plan, D("2026-11-02"))[-1],
                         "10:00 AM  week report (it waits until 15 minutes after the close)")
        os.environ["PSX_NOTIFY"] = "print"
        rc, out = self.run_cmd("status", "--now", "2026-11-07 09:00")
        self.assertIn("  10:00 AM  week report", out)


if __name__ == "__main__":
    unittest.main()
