import datetime
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import urllib.error
import http.client
import socket
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

from _load import load, ROOT, FIX

alerts = load("psx-alerts", "alerts")

MARKET = os.path.join(FIX, "psx-market")        # the made-up sample market (read only)
QDIR = os.path.join(FIX, "psx-alerts")          # this skill's own fixtures
PLAN = os.path.join(QDIR, "plan-demo.json")     # the EXAMPLE.md section 6 plan
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; psx-trader-skill/1.0)"

# PSX weekday closures, SPEC section 3 (2026 as they happened; 2027 expected, moon dates provisional)
PSX_2026 = ["2026-02-05", "2026-03-20", "2026-03-23", "2026-05-01", "2026-05-26", "2026-05-27", "2026-05-28",
            "2026-06-25", "2026-06-26", "2026-08-14", "2026-08-26", "2026-11-09", "2026-12-25"]
PSX_2027 = ["2027-02-05", "2027-03-05", "2027-03-10", "2027-03-11", "2027-03-12", "2027-03-23", "2027-05-17",
            "2027-05-18", "2027-05-19", "2027-05-28", "2027-06-15", "2027-06-16", "2027-08-16", "2027-11-09"]
MOON_2027 = ["2027-03-05", "2027-03-10", "2027-03-11", "2027-03-12", "2027-05-17", "2027-05-18", "2027-05-19",
             "2027-06-15", "2027-06-16", "2027-08-16"]


def pk(text):
    """--now text (Karachi time unless it says Z or an offset) -> naive UTC."""
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def load_plan():
    with open(PLAN) as fh:
        return json.load(fh)


def plan_rules(cfg=None):
    return alerts.rules_from_plan(load_plan(), cfg)


def status(text, cfg=None):
    return alerts.market_status(pk(text), cfg)


def quote(sym, price, when, prev=None, lower=None, upper=None, limits="company page"):
    """A quote like get_quotes gives, with its data time at Karachi time `when`."""
    t = alerts.utc_iso(pk(when))
    return {"symbol": sym, "price": price, "prev_close": prev, "time": t, "fetched": t, "source": "psx-company",
            "from": "tape", "lower": lower, "upper": upper, "limits": limits, "open": None, "high": None,
            "low": None}


def demo_at(when):
    """DEMO from the sample company tape, matched to the tape's asof (EXAMPLE.md)."""
    q, errors = alerts.get_quotes(["DEMO"], "psx-company", MARKET, {}, pk(when), "--offline")
    return q, errors


class Calendar(unittest.TestCase):
    def test_holidays_match_the_spec_list(self):
        self.assertEqual(sorted(alerts.PSX_HOLIDAYS), PSX_2026 + PSX_2027)
        self.assertEqual(sorted(alerts.PSX_PROVISIONAL), MOON_2027)
        for d in alerts.PSX_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        self.assertEqual(alerts.trading_day(datetime.date(2026, 11, 9)), (False, "PSX holiday: Iqbal Day"))
        self.assertTrue(alerts.trading_day(datetime.date(2026, 1, 1))[0])        # a bank-only holiday

    def test_karachi_is_utc_plus_5_all_year(self):
        for utc in (DT(2026, 1, 15, 4, 30), DT(2026, 3, 8, 7, 0), DT(2026, 7, 1, 4, 30), DT(2026, 11, 1, 6, 0)):
            self.assertEqual(alerts.to_pkt(utc) - utc, datetime.timedelta(hours=5))
            self.assertEqual(alerts.pkt_to_utc(alerts.to_pkt(utc)), utc)

    def test_parse_now(self):
        self.assertEqual(pk("2026-10-29 10:15"), DT(2026, 10, 29, 5, 15))
        self.assertEqual(pk("2026-10-29T10:53"), DT(2026, 10, 29, 5, 53))
        self.assertEqual(pk("2026-10-29 10:53 PKT"), DT(2026, 10, 29, 5, 53))
        self.assertEqual(pk("2026-10-29T05:57:07Z"), DT(2026, 10, 29, 5, 57, 7))
        self.assertEqual(pk("2026-10-29T10:57:07+05:00"), DT(2026, 10, 29, 5, 57, 7))
        with self.assertRaises(alerts.AlertError):
            alerts._now("tomorrow")
        code, out, err = run(["clock", "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertIn("alerts.py: --now wants", err)
        self.assertNotIn("Traceback", err)

    def test_market_status_on_the_example_day(self):
        cases = [("09:10", "closed", "before the open, pre-open from 9:15 AM PKT"),
                 ("09:20", "pre-open", "limit orders only, trading from 9:30 AM PKT"),
                 ("09:31", "open", "regular session"), ("10:53", "open", "regular session"),
                 ("15:30", "closed", "after the close"),
                 ("15:36", "post-close", "post-close session, closing price only")]
        for hhmm, state, reason in cases:
            st = status("2026-10-29 " + hhmm)
            self.assertEqual((st["state"], st["reason"]), (state, reason), hhmm)
            self.assertEqual(st["open"], state == "open", hhmm)
        st = status("2026-10-29 10:53")
        self.assertEqual((st["now_karachi"], st["weekday"], st["session"], st["segment"]),
                         ("2026-10-29 10:53", "Thursday", "normal", 0))
        self.assertEqual((st["segments"], st["opens"], st["closes"]), ([["09:30", "15:30"]], "09:30", "15:30"))
        self.assertEqual(status("2026-10-29 09:10")["next_open"], "2026-10-29 09:30")
        self.assertEqual(status("2026-10-29 15:36")["next_open"], "2026-10-30 09:15")

    def test_friday_ramadan_holidays_and_unknown_years(self):
        fri = status("2026-10-30 10:00")
        self.assertEqual((fri["reason"], fri["segment"], fri["closes"]), ("Friday morning session", 0, "16:30"))
        brk = status("2026-10-30 12:30")
        self.assertEqual((brk["state"], brk["open"], brk["next_open"]), ("break", False, "2026-10-30 14:30"))
        self.assertEqual(brk["reason"], "Juma break, trading again at 2:30 PM PKT")
        aft = status("2026-10-30 14:31")
        self.assertEqual((aft["reason"], aft["segment"]), ("Friday afternoon session", 1))
        self.assertFalse(status("2026-10-30 16:30")["open"])
        ram = status("2026-02-23 13:29")
        self.assertEqual((ram["reason"], ram["closes"]), ("Ramadan session", "13:30"))
        self.assertFalse(status("2026-02-23 13:30")["open"])
        self.assertEqual(status("2026-02-20 10:00")["segments"], [["09:15", "12:30"]])      # a Ramadan Friday
        iqbal = status("2026-11-09 10:00")
        self.assertEqual((iqbal["state"], iqbal["reason"], iqbal["next_open"]),
                         ("closed", "PSX holiday: Iqbal Day", "2026-11-10 09:30"))
        self.assertEqual(status("2026-10-31 10:00")["reason"], "weekend")
        far = status("2028-03-01 10:00")
        self.assertFalse(far["calendar_known"])
        self.assertIn("holiday list for 2028 is not in this tool", far["calendar_note"])
        eid = status("2027-03-10 10:00")
        self.assertIn("expected", eid["reason"])
        self.assertIn("moon dates can move", eid["calendar_note"])

    def test_the_clock_line(self):
        self.assertEqual(alerts.status_line(status("2026-10-29 10:53")),
                         "PSX OPEN (2026-10-29 10:53 PKT, regular session).")
        self.assertEqual(alerts.status_line(status("2026-10-29 09:20")),
                         "PSX PRE-OPEN (2026-10-29 09:20 PKT, limit orders only, trading from 9:30 AM PKT).")
        self.assertEqual(alerts.status_line(status("2026-10-30 12:30")),
                         "PSX BREAK (2026-10-30 12:30 PKT, Juma break, trading again at 2:30 PM PKT).")
        self.assertEqual(alerts.status_line(status("2026-11-09 10:00")),
                         "PSX closed (2026-11-09 10:00 PKT, PSX holiday: Iqbal Day).")

    def test_config_holidays_open_days_and_ramadan(self):
        shut = {"holidays": {"2026-11-02": "Test closure"}}
        self.assertEqual(status("2026-11-02 10:00", shut)["reason"], "PSX holiday: Test closure")
        self.assertTrue(status("2026-11-09 10:00", {"open_days": ["2026-11-09"]})["open"])
        own = {"ramadan": {"start": "2027-02-10", "end": "2027-03-10"}}
        self.assertEqual(status("2027-02-10 10:00", own)["session"], "ramadan")
        self.assertEqual(status("2027-02-09 10:00", own)["session"], "normal")
        with self.assertRaises(alerts.AlertError):
            status("2026-11-02 10:00", {"holidays": {"2nd Nov": "x"}})


class Quotes(unittest.TestCase):
    def test_company_tape_at_10_52(self):
        q, errors = demo_at("2026-10-29 10:52")
        self.assertEqual(errors, [])
        d = q["DEMO"]
        self.assertEqual((d["price"], d["prev_close"], d["lower"], d["upper"]), (101.3, 98.4, 88.56, 108.24))
        self.assertEqual((d["time"], d["fetched"]), ("2026-10-29T05:52:00Z", "2026-10-29T05:57:07Z"))
        self.assertEqual((d["source"], d["from"], d["limits"]), ("psx-company", "tape", "company page"))

    def test_the_tape_is_matched_to_its_asof(self):
        self.assertEqual(demo_at("2026-10-29 10:53")[0]["DEMO"]["price"], 101.23)
        self.assertEqual(demo_at("2026-10-29 10:47")[0]["DEMO"]["price"], 100.9)      # the real clock at 10:53
        self.assertEqual(demo_at("2026-10-29 11:41")[0]["DEMO"]["price"], 101.8)
        self.assertEqual(demo_at("2026-10-29 15:20")[0]["DEMO"]["price"], 101.58)

    def test_the_saved_page_wins_after_the_close(self):
        d = demo_at("2026-10-29 16:00")[0]["DEMO"]
        self.assertEqual((d["price"], d["from"], d["time"]), (101.6, "saved page", "2026-10-29T10:49:00Z"))
        self.assertEqual(d["fetched"], "2026-10-29T10:52:00Z")            # the page's own clock, 3:52 PM
        q, errors = alerts.get_quotes(["HBL"], "psx-company", MARKET, {}, pk("2026-10-29 16:00"), "--offline")
        self.assertEqual((q["HBL"]["price"], q["HBL"]["flags"]), (273.36, ["XD"]))

    def test_data_from_after_now_is_never_used(self):
        q, errors = demo_at("2026-10-29 09:20")           # the tape starts at 09:33, the page is as of 15:49
        self.assertEqual(q, {})
        self.assertIn("later than the time asked for", errors[0])

    def test_screener_tape_and_the_last_close(self):
        q, errors = alerts.get_quotes(["DEMO", "OGDC"], "psx-screener", MARKET, {}, pk("2026-10-29 10:16"),
                                      "--offline")
        self.assertEqual(errors, [])
        d = q["DEMO"]
        self.assertEqual((d["price"], d["prev_close"], d["prev_close_from"]), (100.9, 98.4, "ldcp"))
        self.assertEqual((d["lower"], d["upper"], d["limits"]), (88.56, 108.24, "computed"))
        self.assertEqual((d["time"], d["fetched"], d["from"]), ("2026-10-29T05:16:00Z", "2026-10-29T05:21:00Z", "tape"))

    def test_a_saved_screener_after_the_close_is_as_of_the_close(self):
        q, _ = alerts.get_quotes(["DEMO"], "psx-screener", MARKET, {}, pk("2026-10-29 16:00"), "--offline")
        d = q["DEMO"]
        self.assertEqual((d["price"], d["from"]), (101.6, "saved page"))
        self.assertEqual(d["time"], "2026-10-29T10:30:00Z")              # 3:52 PM - 5 minutes is after 3:30 PM
        self.assertEqual(d["fetched"], "2026-10-29T10:52:00Z")
        night = alerts._session_end_before(DT(2026, 10, 30, 1, 13))
        self.assertEqual(night, DT(2026, 10, 29, 15, 30))                # a page saved at night is not fresh
        self.assertEqual(alerts._session_end_before(DT(2026, 10, 30, 12, 40)), DT(2026, 10, 30, 12, 0))
        self.assertEqual(alerts._session_end_before(DT(2026, 10, 30, 10, 5)), DT(2026, 10, 30, 10, 5))

    def test_screener_last_close_without_the_days_file(self):
        d = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(d, "mkt_summary"))
            shutil.copy(os.path.join(MARKET, "mkt_summary", "2026-10-28.Z"), os.path.join(d, "mkt_summary"))
            shutil.copytree(os.path.join(MARKET, "tape"), os.path.join(d, "tape"))
            q, errors = alerts.get_quotes(["DEMO"], "psx-screener", d, {}, pk("2026-10-29 10:16"), "--offline")
            self.assertEqual((q["DEMO"]["prev_close"], q["DEMO"]["prev_close_from"]), (98.4, "close"))
            self.assertEqual(errors, [])
        finally:
            shutil.rmtree(d)

    def test_an_unknown_symbol_and_the_site_off_line(self):
        q, errors = alerts.get_quotes(["ZZZZ"], "psx-company", MARKET, {}, pk("2026-10-29 16:00"), "--offline")
        path = os.path.join(MARKET, "company", "ZZZZ.html")
        self.assertEqual(errors, ["ZZZZ: " + alerts.SITE_OFF % ("https://dps.psx.com.pk/company/ZZZZ as " + path)])
        q, errors = alerts.get_quotes(["ZZZZ"], "psx-screener", MARKET, {}, pk("2026-10-29 16:00"), "--offline")
        self.assertIn("not on the screener", errors[0])

    def test_the_indices_page(self):
        ix, errors = alerts.get_indices(["KSE30", "KSE100", "NOPE"], MARKET, {}, pk("2026-10-29 16:00"), "--offline")
        self.assertEqual(round(ix["KSE30"]["change_pct"], 2), 0.81)
        self.assertEqual((ix["KSE30"]["current"], ix["KSE30"]["time"]), (47897.21, "2026-10-29T10:50:00Z"))
        self.assertEqual(errors, ["NOPE is not on the indices page"])
        ix, errors = alerts.get_indices(["KSE30"], MARKET, {}, pk("2026-10-29 10:53"), "--offline")
        self.assertEqual(ix, {})
        self.assertIn("later than the time asked for", errors[0])


class FakeSite(object):
    """Stands in for http_get: answers each URL with a saved sample page, or an HTTP error."""

    def __init__(self, pages=None, codes=None):
        self.pages, self.codes, self.calls = dict(pages or {}), dict(codes or {}), []

    def get(self, url, timeout=30):
        self.calls.append(url)
        if url in self.codes or url not in self.pages:
            raise urllib.error.HTTPError(url, self.codes.get(url, 404), "x", {}, io.BytesIO(b""))
        with open(self.pages[url], "rb") as fh:
            return fh.read()


class Network(unittest.TestCase):
    """The PSX site, faked: no test ever touches the network."""

    def setUp(self):
        self.saved = (alerts.http_get, alerts.SLEEP)
        self.d = tempfile.mkdtemp()
        self.on = {"psx_site": True}

    def tearDown(self):
        alerts.http_get, alerts.SLEEP = self.saved
        shutil.rmtree(self.d)

    def site(self, pages=None, codes=None):
        f = FakeSite(pages, codes)
        alerts.http_get = f.get
        return f

    def test_the_site_is_never_used_when_it_is_off(self):
        f = self.site()
        for cfg in ({}, {"psx_site": "yes"}, {"psx_site": 1}):          # only the JSON true turns it on
            q, errors = alerts.get_quotes(["DEMO"], "psx-company", self.d, cfg, pk("2026-10-29 10:52"), False)
            self.assertEqual(q, {})
            self.assertIn("The PSX site is off for this tool", errors[0])
            alerts.get_quotes(["DEMO"], "psx-screener", self.d, cfg, pk("2026-10-29 10:52"), False)
            alerts.get_indices(["KSE30"], self.d, cfg, pk("2026-10-29 10:52"), False)
        self.assertEqual(f.calls, [])

    def test_offline_and_now_keep_the_site_shut(self):
        f = self.site()
        cfg = os.path.join(self.d, "config.json")
        with open(cfg, "w") as fh:
            json.dump(self.on, fh)
        code, out, _ = run(["quote", "DEMO", "--data", self.d, "--config", cfg, "--offline"])
        self.assertEqual(code, 1)
        self.assertIn("--offline reads only the data folder", out)
        code, out, _ = run(["quote", "DEMO", "--data", self.d, "--config", cfg, "--now", "2026-10-29 10:52"])
        self.assertIn("--now reads only the data folder", out)
        data = os.path.join(self.d, "data")                      # a replay with --now and the site on: never fetched
        shutil.copytree(MARKET, data)
        files = ["--rules", os.path.join(self.d, "alerts.json"), "--state", os.path.join(self.d, "state.json")]
        run(["add", "--symbol", "DEMO", "--above", "200", "--config", cfg] + files)
        run(["add", "--index", "KSE30", "--pct", "4.5", "--config", cfg] + files)
        for source in ("psx-company", "psx-screener"):
            code, out, _ = run(["check", "--now", "2026-10-29 16:00", "--data", data, "--config", cfg, "--source",
                                source, "--prices-out", os.path.join(self.d, "prices.json")] + files)
            self.assertEqual(code, 1, out)
        self.assertEqual(f.calls, [])

    def test_a_site_get_sends_only_the_contract_headers_and_waits(self):
        seen, sleeps = [], []

        class Answer(io.BytesIO):
            headers = {}

        def urlopen(req, timeout=None):
            seen.append(req)
            return Answer(b"<html></html>")

        alerts.SLEEP = sleeps.append
        alerts.http_get.last = None
        with mock.patch("urllib.request.urlopen", urlopen):
            alerts.http_get("https://dps.psx.com.pk/company/DEMO")
            alerts.http_get("https://dps.psx.com.pk/screener")
        self.assertEqual(sorted(seen[0].header_items()), [("Accept-encoding", "gzip"), ("User-agent", UA)])
        self.assertEqual((seen[0].get_method(), seen[0].data), ("GET", None))
        self.assertEqual((alerts.UA, alerts.PAUSE), (UA, 1.5))
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(1.0 < sleeps[0] <= 1.5, sleeps)

    def test_a_site_page_goes_on_the_tape(self):
        url = "https://dps.psx.com.pk/company/DEMO"
        f = self.site({url: os.path.join(MARKET, "company", "DEMO.html")})
        q, errors = alerts.get_quotes(["DEMO"], "psx-company", self.d, self.on, pk("2026-10-29 16:00"), False)
        self.assertEqual((f.calls, errors), ([url], []))
        d = q["DEMO"]
        self.assertEqual((d["price"], d["from"], d["fetched"], d["time"]),
                         (101.6, "site", "2026-10-29T11:00:00Z", "2026-10-29T10:49:00Z"))
        tape = alerts.read_tape(alerts.tape_path(self.d, datetime.date(2026, 10, 29), "DEMO"))
        self.assertEqual([(s["asof"], s["price"], s["fetched"]) for s in tape],
                         [("2026-10-29 15:49", 101.6, "2026-10-29T16:00:00+05:00")])

    def site_page(self, asof, price):
        """The sample DEMO page as the site would serve it at another As-of time and price."""
        with open(os.path.join(MARKET, "company", "DEMO.html"), encoding="utf-8") as fh:
            page = fh.read()
        for a, b in (("As of Thu, Oct 29, 2026 3:49 PM", "As of Thu, Oct 29, 2026 " + asof),
                     ('quote__close">Rs.101.60', 'quote__close">Rs.%.2f' % price)):
            self.assertIn(a, page)
            page = page.replace(a, b)
        path = os.path.join(self.d, "site-DEMO.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        return path

    def test_the_site_comes_first_even_with_fresh_data_in_the_folder(self):
        # one rule for the pack (page_html): with the site on, the site first, so a tape line
        # or a page saved earlier never hides newer data
        shutil.copytree(os.path.join(MARKET, "tape"), os.path.join(self.d, "tape"))
        shutil.copytree(os.path.join(MARKET, "company"), os.path.join(self.d, "company"))
        url = "https://dps.psx.com.pk/company/DEMO"
        f = self.site({url: self.site_page("10:50 AM", 101.10)})
        q, errors = alerts.get_quotes(["DEMO"], "psx-company", self.d, self.on, pk("2026-10-29 10:52"), False)
        self.assertEqual((f.calls, errors), ([url], []))                 # the 10:52 tape line is fresh: asked anyway
        self.assertEqual((q["DEMO"]["price"], q["DEMO"]["from"]), (101.3, "tape"))   # the newest as of now wins
        f = self.site({url: self.site_page("10:52 AM", 101.35)})
        q, errors = alerts.get_quotes(["DEMO"], "psx-company", self.d, self.on, pk("2026-10-29 10:52"), False)
        self.assertEqual((f.calls, errors), ([url], []))
        self.assertEqual((q["DEMO"]["price"], q["DEMO"]["from"]), (101.35, "site"))  # the site wins a tie
        f = self.site({url: os.path.join(MARKET, "company", "DEMO.html")})
        q, _ = alerts.get_quotes(["DEMO"], "psx-company", self.d, self.on, pk("2026-10-29 16:00"), False)
        self.assertEqual((f.calls, q["DEMO"]["from"]), ([url], "site"))  # not the saved page of the same time

    def test_a_site_failure_falls_back_to_the_data_folder(self):
        shutil.copytree(os.path.join(MARKET, "company"), os.path.join(self.d, "company"))
        url = "https://dps.psx.com.pk/company/DEMO"
        f = self.site(codes={url: 503})
        q, errors = alerts.get_quotes(["DEMO"], "psx-company", self.d, self.on, pk("2026-10-29 16:00"), False)
        self.assertEqual((f.calls, q["DEMO"]["price"], q["DEMO"]["from"]), ([url], 101.6, "saved page"))
        self.assertEqual(errors, ["DEMO: could not read the PSX site for %s, so only the data folder was read" % url])
        screener = "https://dps.psx.com.pk/screener"
        self.site(codes={screener: 503})
        q, errors = alerts.get_quotes(["DEMO"], "psx-screener", self.d, self.on, pk("2026-10-29 16:00"), False)
        self.assertEqual((q, errors), ({}, ["PSX answered HTTP 503 for " + screener,
                                            "DEMO: no screener price (the PSX site could not be read)"]))
        indices = "https://dps.psx.com.pk/indices"
        shutil.copy(os.path.join(MARKET, "indices.html"), self.d)
        self.site(codes={indices: 503})
        ix, errors = alerts.get_indices(["KSE30"], self.d, self.on, pk("2026-10-29 16:00"), False)
        self.assertEqual((ix["KSE30"]["from"], errors),
                         ("saved page", ["could not read the PSX site for %s, so only the data folder was read"
                                         % indices]))

    def test_a_404_is_one_plain_error(self):
        url = "https://dps.psx.com.pk/company/ZZZZ"
        self.site(codes={url: 404})
        q, errors = alerts.get_quotes(["ZZZZ"], "psx-company", self.d, self.on, pk("2026-10-29 10:52"), False)
        self.assertEqual((q, errors), ({}, ["ZZZZ: PSX answered HTTP 404 for " + url]))

    def test_the_screener_from_the_site_and_its_last_close(self):
        url = "https://dps.psx.com.pk/screener"
        daily = "https://dps.psx.com.pk/download/mkt_summary/2026-10-28.Z"
        f = self.site({url: os.path.join(MARKET, "screener.html"),
                       daily: os.path.join(MARKET, "mkt_summary", "2026-10-28.Z")})
        q, errors = alerts.get_quotes(["DEMO", "OGDC"], "psx-screener", self.d, self.on, pk("2026-10-29 11:00"), False)
        self.assertEqual((f.calls, errors), ([url, daily], []))
        d = q["DEMO"]
        self.assertEqual((d["price"], d["prev_close"], d["from"]), (101.6, 98.4, "site"))
        self.assertEqual((d["time"], d["fetched"]), ("2026-10-29T05:55:00Z", "2026-10-29T06:00:00Z"))   # minus 5 min
        self.assertTrue(os.path.exists(os.path.join(self.d, "mkt_summary", "2026-10-28.Z")))   # never fetched twice
        tape = alerts.read_tape(alerts.tape_path(self.d, datetime.date(2026, 10, 29), "market"))
        self.assertEqual((len(tape), tape[0]["asof"], tape[0]["prices"]["DEMO"]), (1, "2026-10-29 10:55:00", 101.6))

    def test_the_indices_page_from_the_site(self):
        url = "https://dps.psx.com.pk/indices"
        f = self.site({url: os.path.join(MARKET, "indices.html")})
        ix, errors = alerts.get_indices(["KSE30"], self.d, self.on, pk("2026-10-29 16:00"), False)
        self.assertEqual((f.calls, errors, ix["KSE30"]["from"]), ([url], [], "site"))


class RuleCase(unittest.TestCase):
    def setUp(self):
        self.q = {"DEMO": quote("DEMO", 101.3, "2026-10-29 10:52", prev=98.4, lower=88.56, upper=108.24)}

    def at(self, text, rules, state=None, quotes=None, indices=None, cfg=None):
        now = pk(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now, cfg),
                               state or {}, now, indices, cfg)


class Rules(RuleCase):
    def test_a_target_rings_once(self):
        rules = [{"id": "demo-101", "symbol": "DEMO", "type": "above", "price": 101}]
        rings, st = self.at("2026-10-29 10:52", rules)
        self.assertEqual([r["message"] for r in rings],
                         ["DEMO is 101.30 at 10:52 AM PKT: at or above your alert level 101.00."])
        self.assertEqual(self.at("2026-10-29 10:53", rules, st)[0], [])

    def test_stop_and_rearm(self):
        rules = [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 101.5, "rearm": True}]
        rings, st = self.at("2026-10-29 10:52", rules)
        self.assertIn("at or below your stop 101.50", rings[0]["message"])
        up = {"DEMO": quote("DEMO", 101.8, "2026-10-29 11:41", prev=98.4)}
        rings, st = self.at("2026-10-29 11:41", rules, st, up)
        self.assertEqual(rings, [])
        self.assertNotIn("demo-stop", st["rung"])
        rings, st = self.at("2026-10-29 11:45", rules, st)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_the_last_close(self):
        big = [{"id": "demo-3", "symbol": "DEMO", "type": "pct_move", "pct": 3}]
        self.assertEqual(self.at("2026-10-29 10:52", big)[0], [])
        small = [{"id": "demo-2", "symbol": "DEMO", "type": "pct_move", "pct": 2}]
        rings, st = self.at("2026-10-29 10:52", small)
        self.assertEqual(rings[0]["message"],
                         "DEMO moved +2.95% to 101.30 at 10:52 AM PKT (from 98.40, the last close).")
        self.assertEqual(self.at("2026-10-29 11:00", small, st)[0], [])
        down = [{"id": "demo-d", "symbol": "DEMO", "type": "pct_move", "pct": 2, "direction": "down"}]
        self.assertEqual(self.at("2026-10-29 10:52", down)[0], [])
        ref = [{"id": "demo-r", "symbol": "DEMO", "type": "pct_move", "pct": 0.5, "ref": 100.65}]
        self.assertIn("(from 100.65, your ref)", self.at("2026-10-29 10:52", ref)[0][0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        self.assertEqual(self.at("2026-10-29 09:20", rules)[0], [])               # the pre-open
        rings, st = self.at("2026-10-29 09:30", rules)
        self.assertEqual(rings[0]["message"], "Opening bell: PSX is open (2026-10-29 09:30 PKT, regular session).")
        self.assertEqual(self.at("2026-10-29 11:00", rules, st)[0], [])
        self.assertEqual(len(self.at("2026-10-30 09:15", rules, st)[0]), 1)       # Friday opens at 09:15
        self.assertEqual(self.at("2026-10-31 10:00", rules)[0], [])               # Saturday

    def test_the_friday_restart_bell(self):
        rules = [{"id": "bell-2", "type": "opening_bell", "session": 2}]
        for when in ("2026-10-30 10:00", "2026-10-30 12:30", "2026-10-30 14:20", "2026-10-29 14:30"):
            self.assertEqual(self.at(when, rules)[0], [], when)
        rings, st = self.at("2026-10-30 14:30", rules)
        self.assertEqual(rings[0]["message"], "Opening bell: PSX is open again after the Juma break "
                                              "(2026-10-30 14:30 PKT, Friday afternoon session).")
        self.assertEqual(self.at("2026-10-30 15:00", rules, st)[0], [])

    def test_the_plan_gives_a_stop_a_target_and_flat_by_close(self):
        rules = plan_rules()
        self.assertEqual(rules, [
            {"id": "demo-plan-stop", "symbol": "DEMO", "type": "stop", "price": 99.55,
             "note": "plan stop: opening-range midpoint", "date": "2026-10-29"},
            {"id": "demo-plan-target", "symbol": "DEMO", "type": "target", "price": 101.75,
             "note": "plan target: 1R (one times the risk)", "date": "2026-10-29"},
            {"id": "demo-flat-by", "symbol": "DEMO", "type": "time", "at": "15:20",
             "label": "flat by close", "before_close": True, "date": "2026-10-29"}])
        self.assertEqual(self.at("2026-10-29 10:53", rules, quotes=demo_at("2026-10-29 10:53")[0])[0], [])
        state, first = {}, {}
        t = DT(2026, 10, 29, 10, 15)
        while t <= DT(2026, 10, 29, 15, 25):                  # the whole day after the entry, minute by minute
            text = t.strftime("%Y-%m-%d %H:%M")
            rings, state = self.at(text, rules, state, demo_at(text)[0])
            for r in rings:
                first.setdefault(r["id"], text)
            t += datetime.timedelta(minutes=1)
        self.assertEqual(first, {"demo-plan-target": "2026-10-29 11:41", "demo-flat-by": "2026-10-29 15:20"})

    def test_flat_by_close_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-29 15:19", rules)[0], [])
        rings, st = self.at("2026-10-29 15:20", rules)
        self.assertEqual(rings[0]["message"],
                         "Flat by close: it is 15:20 PKT (set for 15:20), the market closes at 15:30. If DEMO is "
                         "still open, close it: psx-broker flatten --symbol DEMO builds the sell ticket, then "
                         "psx-risk and your yes.")
        self.assertEqual(self.at("2026-10-29 15:25", rules, st)[0], [])
        rings, _ = self.at("2026-10-29 16:10", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-10-30 16:25", rules, st)[0], [])        # plan rules: their day only

    def test_a_time_alert_moves_with_the_close(self):
        rule = [{"id": "flat", "type": "time", "at": "15:20", "label": "flat by close", "before_close": True}]
        self.assertEqual(self.at("2026-10-30 16:19", rule)[0], [])
        rings, st = self.at("2026-10-30 16:20", rule)
        self.assertIn("it is 16:20 PKT (set for 15:20, moved to 16:20 for today's hours), the market closes at 16:30",
                      rings[0]["message"])
        self.assertEqual(st["rung"]["flat"], "2026-10-30T11:20:00Z")
        self.assertEqual(self.at("2026-02-23 13:19", rule)[0], [])                # a Ramadan Monday
        self.assertEqual(len(self.at("2026-02-23 13:20", rule)[0]), 1)
        self.assertEqual(len(self.at("2026-10-29 15:20", rule)[0]), 1)            # a normal Thursday
        plain = [{"id": "t", "type": "time", "at": "15:20", "label": "check"}]
        self.assertEqual(len(self.at("2026-10-30 15:20", plain)[0]), 1)           # no before_close: no move
        self.assertEqual(self.at("2026-10-31 16:25", rule)[0], [])                # Saturday: no session
        self.assertEqual(self.at("2026-11-09 16:25", rule)[0], [])                # Iqbal Day
        self.assertEqual(alerts.ring_time(rule[0], datetime.date(2026, 10, 30)), datetime.time(16, 20))

    def test_a_dated_rule_is_never_moved_twice(self):
        friday = dict(load_plan(), date="2026-10-30", flat_by="16:20", session="friday")
        rules = alerts.rules_from_plan(friday)
        self.assertEqual(rules[2]["at"], "16:20")
        self.assertEqual(self.at("2026-10-30 16:19", rules[2:])[0], [])
        rings, _ = self.at("2026-10-30 16:20", rules[2:])
        self.assertIn("it is 16:20 PKT (set for 16:20), the market closes at 16:30", rings[0]["message"])
        no_time = dict(friday)
        del no_time["flat_by"]
        self.assertEqual(alerts.rules_from_plan(no_time)[2]["at"], "16:20")      # config_time(15:20) on Friday
        self.assertEqual(alerts.rules_from_plan(no_time, {"flat_by": "15:00"})[2]["at"], "16:00")

    def test_time_alerts_around_the_juma_break(self):
        rule = [{"id": "t", "type": "time", "at": "11:50", "label": "check the trade"}]
        rings, _ = self.at("2026-10-30 11:50", rule)
        self.assertIn("the Juma break starts at 12:00 (trading again at 14:30).", rings[0]["message"])
        late = [{"id": "t", "type": "time", "at": "13:00", "label": "check"}]
        self.assertIn("PSX is in the Juma break until 14:30.", self.at("2026-10-30 13:00", late)[0][0]["message"])
        early = [{"id": "t", "type": "time", "at": "09:00", "label": "check"}]
        self.assertIn("PSX opens at 09:30.", self.at("2026-10-29 09:00", early)[0][0]["message"])


class MoreRules(RuleCase):
    def test_the_price_limit_rule(self):
        top = {"DEMO": quote("DEMO", 108.24, "2026-10-29 14:05", prev=98.4, lower=88.56, upper=108.24)}
        rules = [{"id": "demo-limit", "symbol": "DEMO", "type": "limit", "direction": "any"}]
        rings, st = self.at("2026-10-29 14:05", rules, quotes=top)
        self.assertEqual(rings[0]["message"], "DEMO is 108.24 at 2:05 PM PKT: at its upper price limit 108.24. It "
                                              "cannot trade above that today, so a buy may not fill.")
        self.assertEqual(self.at("2026-10-29 14:10", rules, st, top)[0], [])        # once a day
        down = [{"id": "demo-low", "symbol": "DEMO", "type": "limit", "direction": "down"}]
        self.assertEqual(self.at("2026-10-29 14:05", down, quotes=top)[0], [])
        low = {"DEMO": quote("DEMO", 88.56, "2026-10-29 11:00", prev=98.4, lower=88.56, upper=108.24,
                             limits="computed")}
        msg = self.at("2026-10-29 11:00", down, quotes=low)[0][0]["message"]
        self.assertIn("at its lower price limit 88.56. It cannot trade below that today, so a sell may not fill.", msg)
        self.assertIn("worked out from the last close", msg)
        self.assertEqual(self.at("2026-10-29 10:52", rules)[0], [])                # 101.30 is inside the limits
        self.assertEqual(alerts.price_limits(98.4), (88.56, 108.24))

    def test_the_index_move_rule_warns_before_a_halt(self):
        t = alerts.utc_iso(pk("2026-10-29 11:05"))
        drop = {"KSE30": {"index": "KSE30", "current": 45317.29, "change": -2195.07, "change_pct": -4.62,
                          "time": t},
                "KSE100": {"index": "KSE100", "current": 150000.0, "change": -7000.0, "change_pct": -4.46, "time": t}}
        rules = [{"id": "kse30-move-4.5", "type": "index_move", "index": "KSE30", "pct": 4.5}]
        rings, st = self.at("2026-10-29 11:06", rules, indices=drop)
        self.assertEqual(rings[0]["message"],
                         "KSE30 moved -4.62% to 45,317.29 at 11:05 AM PKT (its last close was 47,512.36). If it stays "
                         "5% away from its last close for 5 minutes, PSX halts all trading for 60 minutes and cancels "
                         "all open orders.")
        self.assertEqual(self.at("2026-10-29 11:10", rules, st, indices=drop)[0], [])
        self.assertEqual(self.at("2026-10-29 11:06", [dict(rules[0], pct=4.7)], indices=drop)[0], [])
        self.assertEqual(self.at("2026-10-29 11:06", [dict(rules[0], direction="up")], indices=drop)[0], [])
        k100 = [{"id": "k100", "type": "index_move", "index": "KSE100", "pct": 4}]
        self.assertNotIn("halts", self.at("2026-10-29 11:06", k100, indices=drop)[0][0]["message"])
        self.assertEqual(self.at("2026-10-30 10:00", rules, indices=drop)[0], [])    # yesterday's reading

    def test_an_old_price_never_rings(self):
        old = {"DEMO": quote("DEMO", 98.4, "2026-10-28 15:49", prev=96.56)}
        rules = [{"id": "s", "symbol": "DEMO", "type": "stop", "price": 99.55}]
        self.assertEqual(self.at("2026-10-29 10:00", rules, quotes=old)[0], [])
        self.assertEqual(len(self.at("2026-10-28 16:00", rules, quotes=old)[0]), 1)

    def test_a_price_after_the_close_says_was(self):
        rules = [{"id": "t", "symbol": "DEMO", "type": "target", "price": 101.5}]
        last = {"DEMO": quote("DEMO", 101.6, "2026-10-29 15:49", prev=98.4)}
        self.assertTrue(self.at("2026-10-29 16:00", rules, quotes=last)[0][0]["message"].startswith(
            "DEMO was 101.60 at 3:49 PM PKT: at or above your target 101.50."))

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "psx-alerts", "alerts.example.json")) as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("demo-plan") or r["id"] == "demo-flat-by"],
                         plan_rules())
        self.assertEqual(sorted(set(r["type"] for r in rules)),
                         ["index_move", "limit", "opening_bell", "pct_move", "stop", "target", "time"])

    def test_bad_rules(self):
        bad = [{"id": "x", "type": "moon"}, {"id": "x", "type": "above", "symbol": "DEMO"},
               {"id": "x", "type": "above", "symbol": "DEMO", "price": True},
               {"id": "x", "type": "time", "at": "4pm"},
               {"id": "x", "type": "limit", "symbol": "DEMO", "direction": "sideways"},
               {"id": "x", "type": "opening_bell", "session": 3}, {"id": "x", "type": "index_move", "index": "KSE30"},
               {"id": "x", "type": "pct_move", "symbol": "DEMO", "pct": 3, "direction": "both"},
               {"id": "x", "type": "stop", "symbol": "DEMO", "price": 99.55, "date": "2026-13-01"}, "not a rule"]
        for rule in bad:
            with self.assertRaises(alerts.AlertError, msg=str(rule)):
                self.at("2026-10-29 10:52", [rule])
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "DEMO", "stop": 99.55})
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan(dict(load_plan(), flat_by="3pm"))


class CliCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")
        self.none = os.path.join(self.d, "none.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now, data=MARKET, *more):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", "--data", data, "--config", self.none, "--now", now] + list(more))

    def add(self, *args):
        return run(["add", "--rules", self.rules, "--state", self.state, "--config", self.none] + list(args))


class Cli(CliCase):
    def test_the_example_day_from_the_plan(self):
        code, text, _ = self.add("--from-plan", "--plan", PLAN)
        self.assertEqual(code, 0)
        for rid in ("demo-plan-stop", "demo-plan-target", "demo-flat-by"):
            self.assertIn("Added %s:" % rid, text)
        self.assertIn("The PSX site is off, so no new price comes in by itself: save a fresh company page before "
                      "each check, or watch your stop in your broker app.", text)
        code, text, _ = self.check("2026-10-29 10:53")
        self.assertEqual(code, 1)
        self.assertEqual(text.splitlines()[:2],
                         ["PSX OPEN (2026-10-29 10:53 PKT, regular session).",
                          "Nothing hit. 3 rule(s) watched today, DEMO 101.23 (as of 10:53 AM PKT)."])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"DEMO": 101.23})
        self.assertEqual((doc["detail"]["DEMO"]["time"], doc["detail"]["DEMO"]["fetched"]),
                         ("2026-10-29T05:53:00Z", "2026-10-29T05:58:07Z"))
        code, text, _ = self.check("2026-10-29 11:41")
        self.assertEqual(code, 0)
        self.assertIn("ALERT DEMO is 101.80 at 11:41 AM PKT: at or above your target 101.75 (plan target: 1R "
                      "(one times the risk)).", text)
        self.assertIn("not an order", text)
        code, text, _ = self.check("2026-10-29 15:20")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by close: it is 15:20 PKT (set for 15:20), the market closes at 15:30. If DEMO is "
                      "still open, close it: psx-broker flatten --symbol DEMO builds the sell ticket, then psx-risk "
                      "and your yes.", text)
        self.assertEqual(self.check("2026-10-29 15:25")[0], 1)
        code, text, _ = self.check("2026-10-30 10:00")
        self.assertIn("Nothing hit. 0 rule(s) watched today.", text)

    def test_a_new_plan_replaces_and_rearms_the_plan_rules(self):
        self.add("--from-plan", "--plan", PLAN)
        self.add("--opening-bell")
        self.assertEqual(self.check("2026-10-29 15:20")[0], 0)
        self.add("--from-plan", "--plan", PLAN)
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["demo-flat-by", "demo-plan-stop", "demo-plan-target", "opening-bell"])
        with open(self.state) as fh:
            rung = json.load(fh)["rung"]
        self.assertNotIn("demo-flat-by", rung)
        self.assertIn("opening-bell", rung)

    def test_add_rules_and_duplicates(self):
        for args in (["--opening-bell"], ["--symbol", "DEMO", "--above", "101"], ["--symbol", "OGDC", "--stop", "275"]):
            self.assertEqual(self.add(*args)[0], 0)
        code, _, err = self.add("--symbol", "DEMO", "--above", "101")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        code, text, _ = self.check("2026-10-29 16:00")
        self.assertEqual(code, 0)                                         # DEMO rang; the bell waits for the open
        self.assertIn("ALERT DEMO was 101.60 at 3:49 PM PKT: at or above your alert level 101.00.", text)
        with open(self.prices) as fh:
            self.assertEqual(json.load(fh)["prices"], {"DEMO": 101.6, "OGDC": 279.54})
        self.assertEqual(self.check("2026-10-30 09:16")[0], 0)               # Friday's bell
        self.assertEqual(self.check("2026-10-30 09:45")[0], 1)
        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_target_limit_index_and_the_friday_bell(self):
        self.assertEqual(self.add("--at", "15:20", "--label", "flat by close", "--before-close",
                                  "--symbol", "demo")[0], 0)
        self.assertEqual(self.add("--symbol", "DEMO", "--target", "101.75", "--date", "2026-10-29")[0], 0)
        self.assertEqual(self.add("--symbol", "DEMO", "--limit")[0], 0)
        self.assertEqual(self.add("--index", "kse30", "--pct", "4.5")[0], 0)
        self.assertEqual(self.add("--opening-bell", "--session", "2")[0], 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules, [
            {"id": "time-1520", "type": "time", "at": "15:20", "label": "flat by close", "symbol": "DEMO",
             "before_close": True},
            {"id": "demo-target-101.75", "symbol": "DEMO", "type": "target", "price": 101.75, "date": "2026-10-29"},
            {"id": "demo-limit-any", "symbol": "DEMO", "type": "limit", "direction": "any"},
            {"id": "kse30-move-4.5", "type": "index_move", "index": "KSE30", "pct": 4.5},
            {"id": "opening-bell-2", "type": "opening_bell", "session": 2}])
        self.assertEqual(self.add("--at", "4pm")[0], 1)
        self.assertEqual(self.add("--index", "KSE30")[0], 1)
        code, _, err = self.add("--from-plan", "--plan", os.path.join(self.d, "nope.json"))
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_a_dated_before_close_time_is_moved_once_when_added(self):
        code, text, _ = self.add("--at", "15:20", "--before-close", "--date", "2026-10-30", "--label", "flat by close")
        self.assertEqual(code, 0)
        self.assertIn("15:20 on a Monday to Thursday is 16:20 on Fri 2026-10-30.", text)
        with open(self.rules) as fh:
            self.assertEqual(json.load(fh)["rules"][0]["at"], "16:20")
        self.assertEqual(self.check("2026-10-30 16:19")[0], 1)
        self.assertEqual(self.check("2026-10-30 16:20")[0], 0)

    def test_a_plan_for_a_closed_day_and_a_bad_date_say_so(self):
        holiday = os.path.join(self.d, "plan.json")
        with open(holiday, "w") as fh:
            json.dump(dict(load_plan(), date="2026-11-09"), fh)
        code, text, _ = self.add("--from-plan", "--plan", holiday)
        self.assertEqual(code, 0)
        self.assertIn("2026-11-09 is not a PSX trading day (PSX holiday: Iqbal Day), so these alerts will not ring.",
                      text)
        code, _, err = self.add("--at", "15:20", "--before-close", "--date", "29-10-2026")
        self.assertEqual(code, 1)
        self.assertIn("--date wants a Karachi date like 2026-10-29", err)
        self.assertEqual(self.add("--symbol", "DEMO", "--stop", "99.55", "--date", "2026-02-30")[0], 1)

    def test_an_index_warning_from_a_saved_indices_page(self):
        data = os.path.join(self.d, "data")
        os.makedirs(data)
        with open(os.path.join(MARKET, "indices.html"), encoding="utf-8") as fh:
            page = fh.read()
        for a, b in (('data-order="47897.21">47,897.21', 'data-order="45317.29">45,317.29'),
                     ('data-order="384.85"><i class="icon-up-dir"></i> 384.85',
                      'data-order="-2195.07"><i class="icon-down-dir"></i> -2195.07'),
                     ('data-order="0.8099997558530003"><i class="icon-up-dir"></i> 0.81%',
                      'data-order="-4.62"><i class="icon-down-dir"></i> -4.62%'),
                     ("As of  Oct 29, 2026 3:50 PM", "As of  Oct 29, 2026 11:05 AM"),
                     ('topbar__status__label">Oct 29, 2026 3:52 PM', 'topbar__status__label">Oct 29, 2026 11:07 AM')):
            self.assertIn(a, page)
            page = page.replace(a, b)
        with open(os.path.join(data, "indices.html"), "w", encoding="utf-8") as fh:
            fh.write(page)
        self.add("--index", "KSE30", "--pct", "4.5")
        code, text, _ = self.check("2026-10-29 11:04", data)                    # the page is from 11:05
        self.assertEqual(code, 2)                                                # so the rule could not be checked
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)
        self.assertIn("error: the saved indices page is as of 11:05 AM PKT, later than the time asked for", text)
        code, text, _ = self.check("2026-10-29 11:06", data)
        self.assertEqual(code, 0)
        self.assertIn("ALERT KSE30 moved -4.62% to 45,317.29 at 11:05 AM PKT", text)
        self.assertIn("PSX halts all trading for 60 minutes", text)

    def test_a_bad_rule_is_skipped_and_the_others_still_ring(self):
        self.add("--from-plan", "--plan", PLAN)
        with open(self.rules) as fh:
            doc = json.load(fh)
        doc["rules"] += [{"id": "my-hand-rule", "symbol": "DEMO", "type": "above", "price": "101.50"},
                         {"id": "trail", "symbol": "DEMO", "type": "trailing", "price": 1}, "not a rule"]
        with open(self.rules, "w") as fh:
            json.dump(doc, fh)
        bad = ['error: rule my-hand-rule: needs "price" as a number above 0, like 101.5 (no quotes)',
               "error: rule trail: unknown type 'trailing'", "error: rule #6: a rule must be a JSON object"]
        code, text, err = self.check("2026-10-29 10:53")
        self.assertEqual((code, err), (2, ""))                            # nothing rang, but a rule is bad
        self.assertIn("Nothing hit. 3 rule(s) watched today, DEMO 101.23 (as of 10:53 AM PKT).", text)
        code, text, err = self.check("2026-10-29 11:41")
        self.assertEqual((code, err), (2, ""))                            # the target rang and a rule is bad
        self.assertIn("ALERT DEMO is 101.80 at 11:41 AM PKT: at or above your target 101.75", text)
        for line in bad:
            self.assertIn(line, text)
        code, text, _ = self.check("2026-10-29 11:45", MARKET, "--json")
        self.assertEqual((code, json.loads(text)["rings"], len(json.loads(text)["errors"])), (2, [], 3))
        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])   # list and add still work
        self.assertEqual(code, 0)
        self.assertIn('#6                       "not a rule"', text)
        self.assertIn("demo-plan-target", text)
        self.assertEqual(self.add("--from-plan", "--plan", PLAN)[0], 0)
        with open(self.rules) as fh:
            self.assertEqual(len(json.load(fh)["rules"]), 6)
        with open(self.rules, "w") as fh:
            fh.write('{"rules": [')
        code, text, err = self.check("2026-10-29 11:41")
        self.assertEqual((code, text), (2, ""))
        self.assertIn("is not valid JSON", err)

    def test_an_old_price_says_what_to_do(self):
        data = os.path.join(self.d, "data")
        os.makedirs(os.path.join(data, "tape", "2026-10-29"))
        with open(os.path.join(MARKET, "tape", "2026-10-29", "DEMO.jsonl"), encoding="utf-8") as fh:
            lines = [x for x in fh if x.strip() and json.loads(x)["asof"] <= "2026-10-29 10:53"]
        with open(os.path.join(data, "tape", "2026-10-29", "DEMO.jsonl"), "w", encoding="utf-8") as fh:
            fh.writelines(lines)                                          # the tape stops at 10:53
        self.add("--from-plan", "--plan", PLAN)
        code, text, _ = self.check("2026-10-29 13:00", data)
        self.assertEqual(code, 1)
        self.assertIn("note: DEMO: the data is 127 minutes old (as of 10:53 AM PKT). Save a new page, or check the "
                      "price in your broker app.", text)

    def test_the_skill_page_matches_the_tool(self):
        with open(os.path.join(ROOT, "psx-alerts", "SKILL.md"), encoding="utf-8") as fh:
            page = fh.read()
        self.add("--from-plan", "--plan", PLAN)
        outs = [self.check(t)[1].strip() for t in ("2026-10-29 10:53", "2026-10-29 11:41", "2026-10-29 15:20")]
        self.assertEqual(re.findall(r"```\n(PSX OPEN.*?)\n```", page, re.S), outs)   # the Example blocks
        head = " ".join(page.split("\n---\n", 1)[0].split())
        self.assertNotIn("DEMO", head)                                    # real tickers in the trigger phrases
        self.assertIn('"tell me if HUBC hits 170"', head)
        flat = " ".join(page.split())
        for line in ("With the site off, save a fresh company page before each check, or watch your stop in your "
                     "broker app.", "Run only one tool that polls the PSX site at a time.",
                     "Exit codes: 0 = something rang. 1 = nothing rang. 2 = a rule or a file could not be read.",
                     "the PSX site first, at every check"):
            self.assertIn(line, flat)


class CliQuotes(CliCase):
    def quote(self, *args):
        return run(["quote", "--offline", "--data", MARKET, "--config", self.none] + list(args))

    def test_quote_write_matches_the_example(self):
        code, text, _ = self.quote("DEMO", "--now", "2026-10-29 10:52", "--write", self.prices)
        self.assertEqual(code, 0)
        self.assertIn("DEMO   101.30 (+2.95% vs last close 98.40)  limits 88.56 to 108.24  as of 10:52 AM PKT", text)
        self.assertIn("about 5 minutes late", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc, {"as_of": "2026-10-29T05:57:07Z", "source": "psx-company",
                               "note": "PSX company page, about 5 minutes late", "prices": {"DEMO": 101.3},
                               "detail": {"DEMO": {"prev_close": 98.4, "time": "2026-10-29T05:52:00Z",
                                                   "source": "psx-company", "fetched": "2026-10-29T05:57:07Z",
                                                   "lower": 88.56, "upper": 108.24,
                                                   "limits_from": "company page"}}})

    def test_quote_write_merges(self):
        self.quote("DEMO", "--now", "2026-10-29 10:52", "--write", self.prices)
        self.quote("OGDC", "--now", "2026-10-29 16:00", "--write", self.prices, "--source", "psx-screener")
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"DEMO": 101.3, "OGDC": 279.54})
        self.assertEqual((doc["source"], doc["detail"]["OGDC"]["source"]), ("psx-screener", "psx-screener"))
        self.assertEqual(doc["detail"]["DEMO"]["fetched"], "2026-10-29T05:57:07Z")     # kept from the first run
        self.assertEqual((doc["detail"]["DEMO"]["limits_from"], doc["detail"]["OGDC"]["limits_from"]),
                         ("company page", "computed"))                         # the screener has no limits

    def test_a_price_written_by_hand_keeps_its_own_time(self):
        with open(self.prices, "w") as fh:
            json.dump({"as_of": "2026-10-29T10:00:00+05:00", "source": "manual", "prices": {"DEMO": 101.0}}, fh)
        self.quote("OGDC", "--now", "2026-10-29 16:00", "--write", self.prices)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"DEMO": 101.0, "OGDC": 279.54})
        self.assertEqual(doc["as_of"], doc["detail"]["OGDC"]["fetched"])
        self.assertEqual((doc["detail"]["DEMO"]["time"], doc["detail"]["DEMO"]["source"]),
                         ("2026-10-29T05:00:00Z", "manual"))           # 10:00 Karachi time, not the new as_of
        with open(self.prices, "w") as fh:
            json.dump({"prices": {"DEMO": 101.0}}, fh)                 # no time at all: it is dropped
        self.quote("OGDC", "--now", "2026-10-29 16:00", "--write", self.prices)
        with open(self.prices) as fh:
            self.assertEqual(json.load(fh)["prices"], {"OGDC": 279.54})

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "OGDC", "shares": 1}]}, fh)
        code, text, _ = self.quote("--holdings", "--portfolio", pf, "--plan", PLAN, "--now", "2026-10-29 16:00",
                                   "--json")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["DEMO", "OGDC"])

    def test_no_source_says_how_to_write_prices_json_by_hand(self):
        code, text, _ = run(["quote", "DEMO", "--data", self.d, "--config", self.none, "--now", "2026-10-29 10:52"])
        self.assertEqual(code, 1)
        self.assertIn("The PSX site is off for this tool: save https://dps.psx.com.pk/company/DEMO as", text)
        self.assertIn('write psx/prices.json by hand, like {"as_of": "2026-10-29T10:52:00+05:00", "source": "manual"',
                      text)
        code, _, err = run(["quote", "--data", self.d])
        self.assertIn("which symbols?", err)

    def test_clock_exit_codes_and_holiday_names(self):
        code, text, _ = run(["clock", "--now", "2026-10-29 10:15", "--config", self.none])
        self.assertEqual(code, 0)
        self.assertIn("PSX OPEN (2026-10-29 10:15 PKT, regular session).", text)
        self.assertIn("Hours on Thursday 2026-10-29: 09:30 to 15:30.", text)
        code, text, _ = run(["clock", "--now", "2026-11-09 10:00", "--config", self.none])
        self.assertEqual(code, 1)
        self.assertIn("PSX holiday: Iqbal Day", text)
        self.assertIn("Next open: 2026-11-10 09:30 PKT (the pre-open starts at 9:15 AM PKT).", text)
        self.assertIn("Quaid-e-Azam Day", run(["clock", "--now", "2026-12-25T10:00", "--config", self.none])[1])
        code, text, _ = run(["clock", "--now", "2026-10-30 12:30", "--config", self.none])
        self.assertEqual(code, 1)
        self.assertIn("09:15 to 12:00, then 14:30 to 16:30", text)
        code, text, _ = run(["clock", "--now", "2026-10-29 10:15", "--json", "--config", self.none])
        self.assertEqual(json.loads(text)["now_karachi"], "2026-10-29 10:15")

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", "--data", MARKET])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)
        self.assertEqual(run(["list", "--rules", self.rules])[0], 1)

    def test_check_json(self):
        self.add("--from-plan", "--plan", PLAN)
        code, text, _ = self.check("2026-10-29 11:41", MARKET, "--json")
        doc = json.loads(text)
        self.assertEqual((code, [r["id"] for r in doc["rings"]], doc["quotes"]),
                         (0, ["demo-plan-target"], {"DEMO": 101.8}))
        self.assertEqual((doc["market"]["state"], doc["market"]["now_karachi"]), ("open", "2026-10-29 11:41"))

    def test_the_output_stays_plain(self):
        self.add("--from-plan", "--plan", PLAN)
        self.add("--symbol", "DEMO", "--pct", "2")
        texts = [self.check(t)[1] for t in ("2026-10-29 10:53", "2026-10-29 11:41", "2026-10-29 15:20")]
        texts += [self.quote("DEMO", "OGDC", "ZZZZ", "--now", "2026-10-29 16:00")[1],
                  run(["clock", "--now", "2026-10-30 12:30", "--config", self.none])[1]]
        for text in texts:
            for bad in ("$", "real time", "real-time", " live", "guarantee", "profit", "P&L", "New York", chr(0x2014)):
                self.assertNotIn(bad, text)

    def test_the_sample_market_is_never_written(self):
        def files():
            out = {}
            for base, _, names in os.walk(MARKET):
                for n in names:
                    p = os.path.join(base, n)
                    out[p] = (os.path.getsize(p), os.path.getmtime(p))
            return out
        before = files()
        self.add("--from-plan", "--plan", PLAN)
        for t in ("2026-10-29 10:53", "2026-10-29 16:00"):
            self.check(t)
            self.check(t, MARKET, "--source", "psx-screener")
        self.quote("DEMO", "--now", "2026-10-29 10:52", "--write", self.prices)
        self.assertEqual(files(), before)


# ---------------------------------------------------------------- swing (SPEC v2 2.4 psx-alerts, Task 2)

V2 = os.path.join(FIX, "psx-v2")                 # the shared v2 test files (made up, read only)
PATHS = os.path.join(V2, "paths")


def contract(name):
    with open(os.path.join(V2, "contract", name), encoding="utf-8") as fh:
        return json.load(fh)


def swing_book(**plan):
    """portfolio-swing.json (DEMO, 1,436 shares, bought Thu 2026-10-29), with plan keys changed."""
    pf = contract("portfolio-swing.json")
    pf["positions"][0]["plan"].update(plan)
    return pf


def stop_ring(day, low, close):
    return ("DEMO traded at or under your stop 96.25 on %s: the low in that day's closing file was %.2f "
            "(official close %.2f)." % (day, low, close))


ACT = "You could sell: psx-broker flatten --symbol DEMO, then psx-risk and your yes. You decide."
LIVE = ("If your stop-limit is in, it may have filled today. Check your broker app. Record a fill with ticket.py "
        "fill, or record the order cancelled.")


class SwingRules(unittest.TestCase):
    def test_a_swing_plan_gives_only_the_stop_and_target_of_its_buy_day(self):
        rules = alerts.rules_from_plan(contract("plan-swing.json"))
        self.assertEqual(rules, [
            {"id": "demo-plan-stop", "symbol": "DEMO", "type": "stop", "price": 96.25,
             "note": "plan stop: 2 x ATR(14) under the entry", "date": "2026-10-29"},
            {"id": "demo-plan-target", "symbol": "DEMO", "type": "target", "price": 105.64,
             "note": "plan target: 2R (two times the risk)", "date": "2026-10-29"}])
        self.assertEqual(len(plan_rules()), 3)                              # a day plan keeps its flat-by rule
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan(dict(contract("plan-swing.json"), date=None))

    def test_rules_from_positions_gives_the_four_rules_and_the_halt_rule(self):
        rules = alerts.rules_from_positions(contract("portfolio-swing.json"), {}, datetime.date(2026, 11, 5))
        by = dict((r["id"], r) for r in rules)
        self.assertEqual(sorted(by), ["demo-swing-stop-2026-10-29", "demo-swing-target-2026-10-29",
                                      "demo-swing-time-2026-10-29", "demo-swing-trend-2026-10-29", "swing-halt"])
        self.assertEqual(by["demo-swing-stop-2026-10-29"],
                         {"id": "demo-swing-stop-2026-10-29", "symbol": "DEMO", "type": "stop", "price": 96.25,
                          "rearm": "daily", "swing": True, "note": "the swing plan in psx/portfolio.json"})
        self.assertEqual((by["demo-swing-target-2026-10-29"]["price"], by["demo-swing-target-2026-10-29"]["rearm"]),
                         (105.64, "daily"))
        self.assertEqual(by["demo-swing-trend-2026-10-29"]["sessions"], 10)
        t = by["demo-swing-time-2026-10-29"]
        self.assertEqual((t["type"], t["date"], t["at"], t["label"]), ("time", "2026-11-27", "16:20", "time stop"))
        self.assertEqual(by["swing-halt"], {"id": "swing-halt", "type": "index_move", "index": "KSE30", "pct": 5,
                                            "swing": True})
        for r in rules:
            self.assertIs(r["swing"], True)
            alerts.validate(r)
        day = {"cash": 1000, "positions": [{"ticker": "DEMO", "shares": 3428, "lots": []}]}
        self.assertEqual(alerts.rules_from_positions(day, {}), [])          # a day position: none at all
        sold = swing_book()
        sold["positions"][0]["shares"] = 0
        self.assertEqual(alerts.rules_from_positions(sold, {}), [])

    def test_the_stop_limit_reminder_rule_is_for_live_mode_only(self):
        live = {"paper": False}
        thu = alerts.rules_from_positions(contract("portfolio-swing.json"), live, datetime.date(2026, 11, 5))
        fri = alerts.rules_from_positions(contract("portfolio-swing.json"), live, datetime.date(2026, 11, 6))
        ram = alerts.rules_from_positions(contract("portfolio-swing.json"), live, datetime.date(2027, 2, 15))
        for rules, at in ((thu, "09:32"), (fri, "09:17"), (ram, "09:17")):
            r = [x for x in rules if x["id"] == "demo-swing-protect-2026-10-29"][0]
            self.assertEqual((r["type"], r["at"], r.get("date")), ("time", at, None))
        sat = alerts.rules_from_positions(contract("portfolio-swing.json"), live, datetime.date(2026, 11, 7))
        for cfg in ({}, {"paper": True}, {"paper": "no"}):
            ids = [x["id"] for x in alerts.rules_from_positions(contract("portfolio-swing.json"), cfg,
                                                                datetime.date(2026, 11, 5))]
            self.assertNotIn("demo-swing-protect-2026-10-29", ids)
        self.assertNotIn("demo-swing-protect-2026-10-29", [x["id"] for x in sat])

    def test_validate_takes_the_new_fields(self):
        ok = [{"id": "a", "symbol": "DEMO", "type": "stop", "price": 96.25, "rearm": "daily", "swing": True},
              {"id": "b", "symbol": "DEMO", "type": "trend", "sessions": 10, "swing": True},
              {"id": "c", "symbol": "DEMO", "type": "target", "price": 1, "rearm": False, "swing": False}]
        for r in ok:
            alerts.validate(r)
        bad = [{"id": "x", "symbol": "DEMO", "type": "stop", "price": 96.25, "rearm": "weekly"},
               {"id": "x", "symbol": "DEMO", "type": "stop", "price": 96.25, "swing": "yes"},
               {"id": "x", "symbol": "DEMO", "type": "trend"}, {"id": "x", "type": "trend", "sessions": 10},
               {"id": "x", "symbol": "DEMO", "type": "trend", "sessions": 1},
               {"id": "x", "symbol": "DEMO", "type": "trend", "sessions": 61},
               {"id": "x", "symbol": "DEMO", "type": "trend", "sessions": True},
               {"id": "x", "symbol": "DEMO", "type": "trend", "sessions": 10.0}]
        for r in bad:
            with self.assertRaises(alerts.AlertError, msg=str(r)):
                alerts.validate(r)
        self.assertIn("trend", alerts.PRICE_TYPES)
        self.assertIn("trend", alerts.NO_QUOTE)

    def test_a_daily_rearm_rings_once_a_day_while_hit(self):
        rule = [{"id": "s", "symbol": "DEMO", "type": "stop", "price": 99, "rearm": "daily"}]
        q = {"DEMO": quote("DEMO", 98.5, "2026-11-05 10:00")}
        now = pk("2026-11-05 10:00")
        rings, st = alerts.evaluate(rule, q, alerts.market_status(now), {}, now)
        self.assertEqual(len(rings), 1)
        later = pk("2026-11-05 11:00")
        self.assertEqual(alerts.evaluate(rule, {"DEMO": quote("DEMO", 98.4, "2026-11-05 11:00")},
                                         alerts.market_status(later), st, later)[0], [])
        nxt = pk("2026-11-06 10:00")
        rings, _ = alerts.evaluate(rule, {"DEMO": quote("DEMO", 98.4, "2026-11-06 10:00")},
                                   alerts.market_status(nxt), st, nxt)
        self.assertEqual(len(rings), 1)


class SwingCase(unittest.TestCase):
    """A temp project: psx/data is the sample market plus one made-up v2 path, and the ledger holds the
    swing example (DEMO bought on Thu 2026-10-29, stop 96.25, target 105.64, exit by Fri 2026-11-27)."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.data = os.path.join(self.d, "data")
        names = ("alerts.json", "state.json", "prices.json", "portfolio.json", "plan.json", "config.json")
        self.rules, self.state, self.prices, self.pf, self.plan, self.cfg = [os.path.join(self.d, n) for n in names]
        self.texts = []

    def tearDown(self):
        shutil.rmtree(self.d)

    def market(self, path=None, upto="2026-11-27", skip=()):
        shutil.copytree(MARKET, self.data)
        if path:
            folder = os.path.join(PATHS, path, "mkt_summary")
            for f in sorted(os.listdir(folder)):
                if f[:10] <= upto and f[:10] not in skip:
                    shutil.copy(os.path.join(folder, f), os.path.join(self.data, "mkt_summary"))

    def write(self, path, doc):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)

    def book(self, **plan):
        self.write(self.pf, swing_book(**plan))

    def read_state(self):
        with open(self.state, encoding="utf-8") as fh:
            return json.load(fh)

    def check(self, now, *more):
        args = ["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices, "--data",
                self.data, "--config", self.cfg, "--portfolio", self.pf, "--plan", self.plan]
        if now:
            args += ["--offline", "--now", now]
        code, out, err = run(args + list(more))
        self.texts.append(out + err)
        return code, out, err

    def alerts_of(self, out):
        return [x[6:] for x in out.splitlines() if x.startswith("ALERT ")]

    def tape(self, when, price, sym="DEMO"):
        """A company page snapshot of sym on the tape, as the tools record it (made up)."""
        path = alerts.tape_path(self.data, datetime.date.fromisoformat(when[:10]), sym)
        alerts.append_tape(path, {"fetched": when.replace(" ", "T") + ":30+05:00", "asof": when,
                                  "source": "company", "symbol": sym, "price": price})

    def indices(self, when, drop=False):
        """The sample indices page as if saved at Karachi time `when` (made up), so the halt rule can read
        it in a replay of the session. drop: KSE30 is 5.12% under its last close."""
        with open(os.path.join(MARKET, "indices.html"), encoding="utf-8") as fh:
            page = fh.read()
        t = DT.strptime(when, "%Y-%m-%d %H:%M")
        stamp = "%s %d, %d %d:%02d %s" % (t.strftime("%b"), t.day, t.year, t.hour % 12 or 12, t.minute,
                                          "AM" if t.hour < 12 else "PM")
        swaps = [("As of  Oct 29, 2026 3:50 PM", "As of  " + stamp),
                 ('topbar__status__label">Oct 29, 2026 3:52 PM', 'topbar__status__label">' + stamp)]
        if drop:
            swaps += [('data-order="47897.21">47,897.21', 'data-order="45079.73">45,079.73'),
                      ('data-order="384.85"><i class="icon-up-dir"></i> 384.85',
                       'data-order="-2432.63"><i class="icon-down-dir"></i> -2432.63'),
                      ('data-order="0.8099997558530003"><i class="icon-up-dir"></i> 0.81%',
                       'data-order="-5.12"><i class="icon-down-dir"></i> -5.12%')]
        for a, b in swaps:
            self.assertIn(a, page)
            page = page.replace(a, b)
        with open(os.path.join(self.data, "indices.html"), "w", encoding="utf-8") as fh:
            fh.write(page)

    def words(self):
        """Every line the swing checks of this test printed passes the shared word test (D60)."""
        from _words import check_words
        self.assertGreater(check_words(self, "\n".join(self.texts)), 0)


class SwingPaths(SwingCase):
    def test_gap_the_stop_rings_from_the_file_then_daily_and_not_after_the_sell(self):
        self.market("gap")
        self.book()
        code, out, _ = self.check("2026-11-02 15:00")                 # in the session: its file is not out
        self.assertEqual((code, self.alerts_of(out)), (2, []))          # (2: no fresh price, see SwingPrices)
        self.assertIn("note: DEMO: checked the closing files of Thu 2026-10-29 and Fri 2026-10-30.", out)
        code, out, _ = self.check("2026-11-02 16:10")
        self.assertEqual(code, 0)
        self.assertEqual(self.alerts_of(out), [
            stop_ring("Mon 2026-11-02", 94.80, 95.60) + " DEMO opened at 95.40 on Mon 2026-11-02, under your stop "
            "96.25 (a gap): a sell fills near the open, not at the stop. Your plan's stop rule is met. " + ACT])
        self.assertIn("note: DEMO: checked the closing file of Mon 2026-11-02.", out)
        self.assertEqual(self.check("2026-11-02 16:20")[0], 1)          # once
        code, out, _ = self.check("2026-11-03 16:10")                  # the daily re-arm while held
        self.assertEqual(self.alerts_of(out), [
            "DEMO: the stop rule was met on Mon 2026-11-02. You still hold 1,436 shares in psx/portfolio.json. "
            + ACT])
        self.assertEqual(self.check("2026-11-03 17:00")[0], 1)
        self.write(self.pf, {"cash": 500000, "positions": []})          # sold
        code, out, err = self.check("2026-11-04 16:10")
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertIn("no rules", err)                                  # no rules and no swing share: as in v1
        self.write(self.rules, {"rules": [{"id": "bell", "type": "opening_bell"}]})
        self.assertEqual(self.alerts_of(self.check("2026-11-04 16:20")[1]), [])
        state = self.read_state()
        for name in ("swing_met", "closes_checked"):
            self.assertNotIn(name, state)                               # the sold position's entries are gone
        self.words()

    def test_up_the_target_rings_on_11_04_from_the_high(self):
        self.market("up")
        self.book()
        code, out, _ = self.check("2026-11-03 16:10")
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        code, out, _ = self.check("2026-11-04 16:10")
        self.assertEqual(self.alerts_of(out), [
            "DEMO traded at or over your target 105.64 on Wed 2026-11-04: the high in that day's closing file was "
            "105.90. Your plan's target rule is met. You could sell: psx-broker flatten --symbol DEMO --limit P, "
            "then psx-risk and your yes. You decide."])
        self.assertEqual(self.read_state()["swing_met"], {"DEMO-2026-10-29": {"target": "2026-11-04"}})
        self.words()

    def test_trend_rings_once_after_the_close_of_11_25_and_not_before(self):
        self.market("trend")
        self.book(target=112.0)                                         # exit.py --target price:112
        for t in ("2026-11-02 16:10", "2026-11-12 16:10", "2026-11-24 16:10", "2026-11-25 15:00"):
            self.assertEqual(self.alerts_of(self.check(t)[1]), [], t)
        code, out, _ = self.check("2026-11-25 16:10")
        self.assertEqual(self.alerts_of(out), [
            "DEMO closed at 100.50 on Wed 2026-11-25 (the official close), under 101.20, the lowest close of the 10 "
            "sessions before. Your plan's trend rule is met. It says: sell in the next session. You could sell: "
            "psx-broker flatten --symbol DEMO (--limit P in the pre-open), then psx-risk and your yes. You decide."])
        self.assertEqual(self.check("2026-11-25 16:30")[0], 1)
        out = self.check("2026-11-26 16:10")[1]
        self.assertEqual(self.alerts_of(out), [
            "DEMO: the trend rule was met on Wed 2026-11-25. It says: sell in the next session. You still hold "
            "1,436 shares in psx/portfolio.json. " + ACT])
        self.words()

    def test_flat_the_time_stop_at_16_20_on_the_exit_by_day_only(self):
        self.market("flat")
        self.book()
        for t in ("2026-11-26 15:20", "2026-11-26 16:25", "2026-11-27 16:19"):
            self.assertEqual(self.alerts_of(self.check(t)[1]), [], t)
        out = self.check("2026-11-27 16:20")[1]
        self.assertEqual(self.alerts_of(out), [
            "DEMO: today is the exit-by day of your swing plan (the time stop, 20 sessions). The plan says: out by "
            "4:20 PM PKT. " + ACT])
        self.assertEqual(self.alerts_of(self.check("2026-11-27 16:25")[1]), [])
        self.words()

    def test_past_the_exit_by_day_is_an_alert_once_a_day(self):
        self.market("flat")
        self.book()
        self.check("2026-11-27 17:00")
        past = ("DEMO is past its exit-by day (Fri 2026-11-27) and still in psx/portfolio.json. The time stop is met. "
                + ACT)
        self.assertEqual(self.alerts_of(self.check("2026-11-28 10:00")[1]), [past])     # a Saturday check
        self.assertEqual(self.alerts_of(self.check("2026-11-28 11:00")[1]), [])
        self.assertEqual(self.alerts_of(self.check("2026-11-30 08:15")[1]), [past])
        self.words()


class SwingWalk(SwingCase):
    def test_a_mac_that_slept_still_reads_each_closing_file_once(self):
        self.market("trend")
        self.book(target=112.0)
        self.check("2026-11-24 16:10")                                  # then no check on 11-25 or 11-26
        code, out, _ = self.check("2026-11-27 08:15")                  # the morning job of Friday
        self.assertEqual(code, 0)
        self.assertEqual(self.alerts_of(out), [
            "DEMO closed at 100.50 on Wed 2026-11-25 (the official close), under 101.20, the lowest close of the 10 "
            "sessions before. Your plan's trend rule was met on Wed 2026-11-25. It says: sell in the next session. "
            "You still hold 1,436 shares in psx/portfolio.json. You could sell: psx-broker flatten --symbol DEMO "
            "(--limit P in the pre-open), then psx-risk and your yes. You decide."])
        self.assertIn("note: DEMO: checked the closing files of Wed 2026-11-25 and Thu 2026-11-26.", out)
        self.assertEqual(self.read_state()["closes_checked"], {"DEMO-2026-10-29": "2026-11-26"})
        code, out, _ = self.check("2026-11-27 08:20")                  # a second check reads none
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertNotIn("checked the closing file", out)
        self.words()

    def test_a_missing_file_is_skipped_with_a_note_and_checked_when_it_comes(self):
        self.market("gap", skip=("2026-11-02",))
        self.book()
        code, out, _ = self.check("2026-11-03 16:10")
        self.assertIn("note: no closing file for Mon 2026-11-02 yet: its stop, target and trend checks wait.", out)
        self.assertIn("checked the closing files of Thu 2026-10-29, Fri 2026-10-30 and Tue 2026-11-03.", out)
        self.assertEqual(self.alerts_of(out), [
            stop_ring("Tue 2026-11-03", 95.32, 96.00) + " DEMO opened at 95.72 on Tue 2026-11-03, under your stop "
            "96.25 (a gap): a sell fills near the open, not at the stop. Your plan's stop rule is met. " + ACT])
        self.assertEqual(self.read_state()["closes_missing"], {"DEMO-2026-10-29": ["2026-11-02"]})
        self.assertIn("no closing file for Mon 2026-11-02", self.check("2026-11-03 16:20")[1])
        shutil.copy(os.path.join(PATHS, "gap", "mkt_summary", "2026-11-02.Z"), os.path.join(self.data, "mkt_summary"))
        code, out, _ = self.check("2026-11-03 16:30")
        self.assertIn("note: DEMO: checked the closing file of Mon 2026-11-02.", out)
        self.assertNotIn("no closing file", out)
        self.assertNotIn("closes_missing", self.read_state())
        self.assertEqual(self.read_state()["closes_checked"], {"DEMO-2026-10-29": "2026-11-03"})
        self.assertEqual(self.read_state()["swing_met"], {"DEMO-2026-10-29": {"stop": "2026-11-02"}})  # its first day
        self.assertNotIn("checked the closing file", self.check("2026-11-03 16:40")[1])
        self.words()

    def test_a_rule_without_swing_never_uses_the_closing_file(self):
        self.market("gap")
        self.write(self.rules, {"rules": [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 96.25}]})
        code, out, _ = self.check("2026-11-02 16:10")                  # the 11-02 low is 94.80
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertIn("Nothing hit. 1 rule(s) watched today, DEMO 101.60 (as of Thu 2026-10-29 3:49 PM PKT).", out)
        self.assertNotIn("closing file", out)
        self.assertNotIn("closes_checked", self.read_state())

    def test_no_double_ring_on_the_buy_day(self):
        self.market()
        self.write(self.plan, contract("plan-swing.json"))
        self.assertEqual(run(["add", "--from-plan", "--plan", self.plan, "--rules", self.rules, "--state", self.state,
                              "--config", self.cfg])[0], 0)
        self.tape("2026-10-29 11:00", 96.0)
        self.indices("2026-10-29 10:59")
        self.book()                                                     # the fill is in the ledger
        code, out, _ = self.check("2026-10-29 11:00")
        self.assertEqual(code, 0)
        self.assertEqual(self.alerts_of(out), [
            "DEMO is 96.00 at 11:00 AM PKT: at or below your stop 96.25 (the swing plan in psx/portfolio.json). "
            "Your plan's stop rule is met. " + ACT])
        self.assertNotIn("demo-plan-stop", self.read_state()["rung"])
        self.assertIn("demo-swing-stop-2026-10-29", self.read_state()["rung"])
        self.write(self.pf, {"cash": 500000, "positions": []})          # no fill recorded: the plan rules ring
        self.write(self.state, {})
        out = self.check("2026-10-29 11:00")[1]
        self.assertEqual(self.alerts_of(out), [
            "DEMO is 96.00 at 11:00 AM PKT: at or below your stop 96.25 (plan stop: 2 x ATR(14) under the entry)."])
        self.words()

    def test_a_quote_ring_on_a_day_is_not_rung_again_from_its_file(self):
        self.market("gap")
        self.book()
        self.check("2026-10-30 17:00")
        self.tape("2026-11-02 10:00", 95.50)
        code, out, _ = self.check("2026-11-02 10:00")
        self.assertEqual(self.alerts_of(out), [
            "DEMO is 95.50 at 10:00 AM PKT: at or below your stop 96.25 (the swing plan in psx/portfolio.json). "
            "Your plan's stop rule is met. " + ACT])
        self.assertEqual(self.read_state()["swing_met"], {"DEMO-2026-10-29": {"stop": "2026-11-02"}})
        code, out, _ = self.check("2026-11-02 16:10")                  # the 11-02 file: low 94.80
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertIn("checked the closing file of Mon 2026-11-02", out)
        self.words()

    def test_check_with_no_rules_file_checks_the_ledger_and_with_neither_exits_1(self):
        self.market("up")
        self.book()
        code, out, _ = self.check("2026-11-04 16:10")
        self.assertEqual(code, 0)
        self.assertIn("ALERT DEMO traded at or over your target 105.64 on Wed 2026-11-04", out)
        self.assertIn("An alert is information, not an order.", out)
        os.remove(self.pf)
        code, out, err = self.check("2026-11-04 16:20")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("alerts.py: no rules in %s: add some with alerts.py add --from-plan" % self.rules, err)
        self.write(self.pf, {"cash": 1000, "positions": [{"ticker": "DEMO", "shares": 10, "lots": []}]})
        self.assertEqual(self.check("2026-11-04 16:20")[0], 1)          # a day position: still "no rules"
        self.words()


class SwingPrices(SwingCase):
    OLD = ("error: DEMO: no price newer than 15 minutes (newest 10:00 AM PKT). The swing stop was not checked. "
           "Check the price in your broker app.")

    def test_an_old_price_in_the_session_is_an_error(self):
        self.market("flat", upto="2026-11-04")
        self.book()
        self.tape("2026-11-05 10:00", 102.0)
        code, out, _ = self.check("2026-11-05 10:15")                  # 15 minutes old: still fresh
        self.assertEqual(code, 1)
        self.assertNotIn("no price newer", out)
        code, out, _ = self.check("2026-11-05 10:16")                  # 16 minutes old
        self.assertEqual(code, 2)
        self.assertIn(self.OLD, out)
        self.assertNotIn("note: DEMO: the data is", out)                # the error says it, not the v1 note
        self.assertEqual(self.check("2026-11-05 16:00")[0], 1)          # after the close: not an error
        self.words()

    def test_no_old_price_error_in_the_juma_break_or_with_no_swing_share(self):
        self.market("flat", upto="2026-11-05")
        self.book()
        self.tape("2026-11-06 11:50", 102.3)
        self.assertNotIn("no price newer", self.check("2026-11-06 12:30")[1])     # the Friday Juma break
        self.assertIn("no price newer", self.check("2026-11-06 14:40")[1])        # the afternoon session
        none = alerts.old_prices([{"symbol": "DEMO"}], {}, status("2026-11-05 10:00"), pk("2026-11-05 10:00"))
        self.assertEqual(none[0], ["DEMO: no price newer than 15 minutes (none found). The swing stop was not "
                                   "checked. Check the price in your broker app."])
        self.words()

    def test_a_day_rule_with_an_old_quote_keeps_the_v1_note_and_exit_code(self):
        shutil.copytree(MARKET, self.data, ignore=shutil.ignore_patterns("DEMO.jsonl"))
        with open(os.path.join(MARKET, "tape", "2026-10-29", "DEMO.jsonl"), encoding="utf-8") as fh:
            lines = [x for x in fh if x.strip() and json.loads(x)["asof"] <= "2026-10-29 10:53"]
        with open(os.path.join(self.data, "tape", "2026-10-29", "DEMO.jsonl"), "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        self.write(self.pf, {"cash": 1000, "positions": [{"ticker": "DEMO", "shares": 3428, "lots": []}]})
        self.write(self.rules, {"rules": plan_rules()})
        code, out, _ = self.check("2026-10-29 13:00")
        self.assertEqual(code, 1)
        self.assertIn("note: DEMO: the data is 127 minutes old (as of 10:53 AM PKT). Save a new page, or check the "
                      "price in your broker app.", out)
        self.assertNotIn("error", out)


class SwingPlanAlert(SwingCase):
    WORDS = ("DEMO: a swing plan for Thu 2026-10-29 and no DEMO position in psx/portfolio.json. If you bought it, "
             "record the fill (ticket.py fill): until then its stop and target are not watched.")

    def test_a_plan_with_no_position_rings_after_the_buy_day_close_and_the_next_session_only(self):
        self.market()
        self.write(self.plan, contract("plan-swing.json"))
        self.write(self.pf, {"cash": 500000, "positions": []})
        code, _, err = self.check("2026-10-29 15:00")                  # before the close: the order may fill
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)
        code, out, _ = self.check("2026-10-29 16:10")
        self.assertEqual((code, self.alerts_of(out)), (0, [self.WORDS]))
        self.assertEqual(self.check("2026-10-29 16:20")[0], 1)          # once a day
        code, out, _ = self.check("2026-10-30 10:00")                  # the next session
        self.assertEqual((code, self.alerts_of(out)), (0, [self.WORDS]))
        self.assertEqual(self.check("2026-11-02 10:00")[0], 1)          # not the third
        self.words()

    def test_no_plan_alert_while_held_or_after_the_sale_or_for_a_day_plan(self):
        self.market()
        self.write(self.plan, contract("plan-swing.json"))
        self.book()
        self.assertNotIn(self.WORDS, self.check("2026-10-29 16:10")[1])
        self.write(self.pf, {"cash": 500000, "positions": []})          # sold on the buy day
        self.write(self.rules, {"rules": [{"id": "bell", "type": "opening_bell"}]})
        self.assertNotIn(self.WORDS, self.check("2026-10-29 16:20")[1])
        self.assertNotIn(self.WORDS, self.check("2026-10-30 10:00")[1])
        os.remove(self.state)
        self.write(self.plan, load_plan())                              # a day plan: never
        self.assertNotIn("a swing plan", self.check("2026-10-29 16:10")[1])


XB_WORDS = ("DEMO went ex %s (XB): the reference price fell from 107.64 to 89.70 (factor 0.8333). Your stop %s and "
            "target 120.00 are on the old price scale, so the stop rule can ring on this alone. You choose: scale them "
            "(ticket.py stop --symbol DEMO --scale 0.8333) or keep them (--scale 1). Record the bonus shares when "
            "they come (ticket.py fill --bonus N).")


class SwingExDates(SwingCase):
    def test_an_xd_row_gives_the_cash_words_once_and_no_factor_alert(self):
        self.market("up", upto="2026-11-04")
        with open(os.path.join(self.data, "mkt_summary", "2026-11-05.lis"), "w", encoding="latin-1") as fh:
            fh.write("05NOV2026|DEMO|0818|Demo IndustriesXD|102.51|103.35|102.11|103.00|2000000|102.30|||\r\n")
        self.book(target=120.0)                                         # made up: a Rs 3.00 payout on Thu 11-05
        code, out, _ = self.check("2026-11-05 16:10")
        self.assertEqual(self.alerts_of(out), [
            "DEMO went ex today (XD). The reference price is about Rs 3.00 lower: the payout, paid to you in cash. "
            "Your stop stays 96.25, so a fall by the payout alone can meet it."])
        self.assertNotIn("You choose", out)
        self.assertEqual(self.alerts_of(self.check("2026-11-06 08:15")[1]), [])      # once
        self.words()

    def test_bonus_the_xb_alert_rings_once_a_day_until_the_plan_is_scaled(self):
        self.market("bonus")
        self.book(stop=85.0, stop_at_buy=85.0, target=120.0)            # a stop the bonus alone does not meet
        self.assertEqual(self.alerts_of(self.check("2026-11-11 16:10")[1]), [])
        self.assertEqual(self.alerts_of(self.check("2026-11-12 16:10")[1]), [XB_WORDS % ("today", "85.00")])
        self.assertEqual(self.alerts_of(self.check("2026-11-12 16:20")[1]), [])
        self.assertEqual(self.alerts_of(self.check("2026-11-13 16:40")[1]), [XB_WORDS % ("on Thu 2026-11-12", "85.00")])
        self.book(stop=85.0, stop_at_buy=85.0, target=120.0,
                  scaled=[{"date": "2026-11-11", "factor": 0.9, "why": "an older one"}])
        self.assertEqual(len(self.alerts_of(self.check("2026-11-16 16:10")[1])), 1)  # dated before the ex-date
        self.book(stop=70.83, stop_at_buy=85.0, target=100.0,
                  scaled=[{"date": "2026-11-16", "factor": 0.8333, "why": "XB 20% on 2026-11-12"}])
        self.assertEqual(self.alerts_of(self.check("2026-11-17 16:10")[1]), [])
        self.book(stop=85.0, stop_at_buy=85.0, target=120.0, scaled=[{"date": "2026-11-17", "factor": 1, "why": "keep"}])
        self.assertEqual(self.alerts_of(self.check("2026-11-18 16:10")[1]), [])     # keep is a choice too
        self.words()

    def test_bonus_a_stop_ring_on_the_bonus_alone_then_the_scale_forgets_it(self):
        self.market("bonus")
        self.book(target=120.0)
        self.check("2026-11-11 16:10")
        out = self.check("2026-11-12 16:10")[1]
        self.assertEqual(self.alerts_of(out), [
            stop_ring("Thu 2026-11-12", 89.51, 90.17) + " " + XB_WORDS % ("today", "96.25") +
            " Your plan's stop rule is met. " + ACT])
        self.assertNotIn("(a gap)", out)                                # the new price scale is not a gap
        self.book(stop=80.2, target=100.0, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB 20%"}])
        code, out, _ = self.check("2026-11-12 16:30")
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertIn("checked the closing file of Thu 2026-11-12", out)            # read again, scaled
        self.assertNotIn("swing_met", self.read_state())
        self.assertEqual(self.alerts_of(self.check("2026-11-13 16:40")[1]), [])
        self.assertNotIn("lowered by hand", self.check("2026-11-13 16:50")[1])       # 80.20 is the scaled stop
        self.words()

    def page(self, flag, ldcp, asof):
        """The sample DEMO company page, saved again with an ex flag after the name, a new LDCP and As-of (made up)."""
        with open(os.path.join(MARKET, "company", "DEMO.html"), encoding="utf-8") as fh:
            page = fh.read()
        for a, b in (('<div class="quote__name">Demo Industries Limited</div>',
                      '<div class="quote__name">Demo Industries Limited<div class="tag tag--skim tag--%s">%s</div>'
                      '</div>' % (flag.lower(), flag)),
                     ('LDCP</div><div class="stats_value">98.40', 'LDCP</div><div class="stats_value">%.2f' % ldcp),
                     ("As of Thu, Oct 29, 2026 3:49 PM", "As of " + asof)):
            self.assertIn(a, page)
            page = page.replace(a, b)
        path = os.path.join(self.d, "site-DEMO.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        return path

    def test_a_saved_company_page_with_the_flag_gives_the_alert_in_the_morning(self):
        self.market("bonus", upto="2026-11-11")
        shutil.copy(self.page("XB", 89.70, "Wed, Nov 11, 2026 3:49 PM"), os.path.join(self.data, "company", "DEMO.html"))
        self.book(stop=85.0, stop_at_buy=85.0, target=120.0)
        code, out, _ = self.check("2026-11-12 08:15")
        self.assertEqual((code, self.alerts_of(out)), (0, [XB_WORDS % ("today", "85.00")]))
        ev = self.read_state()["swing_ex"]["DEMO-2026-10-29"]["2026-11-12"]
        self.assertEqual((ev["kind"], ev["factor"], ev["seen"]), ("XB", 0.8333, "company page"))
        self.assertEqual(self.alerts_of(self.check("2026-11-12 08:30")[1]), [])
        shutil.copy(self.page("XB", 89.70, "Wed, Nov 4, 2026 3:49 PM"), os.path.join(self.data, "company", "DEMO.html"))
        os.remove(self.state)
        self.assertEqual(self.alerts_of(self.check("2026-11-12 08:40")[1]), [])     # an old page is not used
        self.words()

    def test_at_most_four_company_page_reads_a_share_a_day(self):
        self.market("bonus", upto="2026-11-11")
        self.book(stop=85.0, stop_at_buy=85.0, target=120.0)
        self.write(self.cfg, {"psx_site": True})
        saved = (alerts.http_get, alerts.SLEEP)
        self.addCleanup(setattr, alerts, "http_get", saved[0])
        self.addCleanup(setattr, alerts, "SLEEP", saved[1])
        old = self.page("XD", 107.64, "Wed, Nov 11, 2026 3:49 PM")      # no new LDCP yet: read again later
        f = FakeSite({"https://dps.psx.com.pk/company/DEMO": old,
                      "https://dps.psx.com.pk/screener": os.path.join(MARKET, "screener.html"),
                      "https://dps.psx.com.pk/indices": os.path.join(MARKET, "indices.html")})
        alerts.http_get, alerts.SLEEP = f.get, (lambda s: None)
        for t in ("2026-11-12 08:15", "2026-11-12 09:40", "2026-11-12 10:00", "2026-11-12 10:30", "2026-11-12 11:00",
                  "2026-11-12 11:30"):
            with mock.patch.object(alerts, "utc_now", return_value=pk(t)):
                self.check(None, "--source", "psx-screener")
        self.assertEqual(f.calls.count("https://dps.psx.com.pk/company/DEMO"), 4)
        self.assertEqual(self.read_state()["ex_look"]["DEMO"]["reads"], 4)
        self.assertEqual(sorted(set(f.calls)), ["https://dps.psx.com.pk/company/DEMO",
                                                "https://dps.psx.com.pk/indices", "https://dps.psx.com.pk/screener"])
        self.words()


class SwingLive(SwingCase):
    # no prices in the reminder (F31): they come from ticket.py protect, after psx-risk and the yes
    PROTECT = ("DEMO: psx/state/broker.json has no stop-limit sell of today. A stop-limit ticket is good for one day. "
               "If your broker app has none now, make a new ticket: ticket.py protect --symbol DEMO. psx-risk checks "
               "it, then your yes.")

    def remind(self, when):
        self.tape(when, 102.2)
        return self.alerts_of(self.check(when)[1])

    def test_the_stop_limit_reminder_once_a_day_after_the_opening_match_in_live_mode(self):
        self.market("flat", upto="2026-11-05")
        self.book()
        self.write(self.cfg, {"paper": False})
        for t in ("2026-11-05 09:20", "2026-11-05 09:31"):             # the pre-open, the opening match
            self.assertEqual(self.remind(t), [], t)
        self.assertEqual(self.remind("2026-11-05 09:37"), [self.PROTECT])          # Thursday
        self.assertEqual(self.remind("2026-11-05 10:00"), [])
        self.assertEqual(self.remind("2026-11-06 09:16"), [])                      # Friday: the match to 9:17
        self.assertEqual(self.remind("2026-11-06 09:22"), [self.PROTECT])
        self.write(self.cfg, {"paper": True})
        self.assertEqual(self.remind("2026-11-10 09:37"), [])                      # paper: never
        self.words()

    def test_no_reminder_after_the_close_even_if_no_check_ran_in_the_session(self):
        self.market("flat", upto="2026-11-05")
        self.book()
        self.write(self.cfg, {"paper": False})
        self.assertEqual(self.alerts_of(self.check("2026-11-05 16:10")[1]), [])

    def test_a_live_stop_ring_says_the_stop_limit_may_have_filled(self):
        self.market("gap")
        self.book()
        self.write(self.cfg, {"paper": False})
        out = self.check("2026-11-02 16:10")[1]
        self.assertEqual(self.alerts_of(out), [
            stop_ring("Mon 2026-11-02", 94.80, 95.60) + " DEMO opened at 95.40 on Mon 2026-11-02, under your stop "
            "96.25 (a gap): a sell fills near the open, not at the stop. Your plan's stop rule is met. " + LIVE + " "
            + ACT])
        self.tape("2026-11-03 10:00", 95.9)
        self.indices("2026-11-03 10:00")
        self.write(self.state, {})
        out = self.check("2026-11-03 10:00")[1]                        # from a quote too
        self.assertIn("DEMO is 95.90 at 10:00 AM PKT: at or below your stop 96.25 (the swing plan in "
                      "psx/portfolio.json). Your plan's stop rule is met. " + LIVE + " " + ACT, out)
        self.write(self.cfg, {"paper": True})
        self.write(self.state, {})
        self.assertNotIn("may have filled", self.check("2026-11-02 16:10")[1])
        self.words()

    def test_the_halt_rule_while_a_swing_share_is_held(self):
        self.market("flat", upto="2026-11-04")
        self.book()
        self.tape("2026-11-05 11:00", 102.2)
        self.indices("2026-11-05 11:00", drop=True)
        out = self.check("2026-11-05 11:00")[1]
        self.assertEqual(self.alerts_of(out), [         # F28 (round 2): record the cancelled stop-limit first
            "KSE30 moved -5.12% to 45,079.73 at 11:00 AM PKT (its last close was 47,512.36). If it stays 5% away from "
            "its last close for 5 minutes, PSX halts all trading for 60 minutes and cancels all open orders. PSX may "
            "halt. A halt cancels all open orders at PSX, and may cancel a stop-limit your broker holds. After the "
            "restart, check it in your broker app. If PSX cancelled your stop-limit, record it first: ticket.py "
            "result --symbol DEMO --date 2026-11-05 --status cancelled. Then ticket.py protect --symbol DEMO makes a "
            "new one."])
        self.assertEqual(self.alerts_of(self.check("2026-11-05 11:05")[1]), [])     # once a day
        self.write(self.pf, {"cash": 500000, "positions": []})                       # nothing held: no halt rule
        self.write(self.rules, {"rules": [{"id": "bell", "type": "opening_bell"}]})
        os.remove(self.state)
        out = self.check("2026-11-05 11:10")[1]
        self.assertNotIn("KSE30", out)
        self.words()

    def test_a_hand_lowered_stop_gives_a_note(self):
        self.market("flat", upto="2026-11-04")
        self.book(stop=95.0)
        out = self.check("2026-11-04 16:10")[1]
        self.assertIn("note: DEMO's stop in psx/portfolio.json (95.00) is under its stop at the buy (96.25): it was "
                      "lowered by hand.", out)
        self.book(stop=97.0)
        self.assertNotIn("lowered by hand", self.check("2026-11-04 16:20")[1])       # moved up: fine
        # F15 (round 2): a scale record with no bonus, rights or split ex-date (none on this path) was written by
        # hand: it counts for nothing (FixScaleNote has the scales that count)
        self.book(stop=80.0, scaled=[{"date": "2026-11-04", "factor": 0.8333, "why": "XB 20%"}])
        self.assertIn("note: DEMO's stop in psx/portfolio.json (80.00) is under its stop at the buy (96.25): it was "
                      "lowered by hand.", self.check("2026-11-04 16:30")[1])
        self.words()


class SwingCli(SwingCase):
    def test_add_from_a_swing_plan_says_the_ledger_takes_over(self):
        self.write(self.plan, contract("plan-swing.json"))
        code, out, _ = run(["add", "--from-plan", "--plan", self.plan, "--rules", self.rules, "--state", self.state,
                            "--config", self.cfg])
        self.texts.append(out)
        self.assertEqual(code, 0)
        self.assertIn("From the fill on, the swing alerts come from psx/portfolio.json and stay on until DEMO is sold. "
                      "Auto-run (psx-autorun) checks every 5 minutes in the session, once after the close, and each "
                      "morning. Without it, run alerts.py check yourself.", out)
        self.assertNotIn("Run alerts.py check every few minutes", out)
        with open(self.rules, encoding="utf-8") as fh:
            self.assertEqual([r["id"] for r in json.load(fh)["rules"]], ["demo-plan-stop", "demo-plan-target"])
        self.words()

    def test_list_shows_the_swing_rules_from_the_ledger(self):
        self.book()
        code, out, _ = run(["list", "--rules", self.rules, "--state", self.state, "--portfolio", self.pf,
                            "--config", self.cfg])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 5)
        for line in lines:
            self.assertTrue(line.endswith("  (from %s)" % self.pf), line)
        self.assertTrue(lines[0].startswith("demo-swing-stop-2026-10-29 {"))
        self.write(self.pf, {"cash": 1, "positions": []})
        self.assertEqual(run(["list", "--rules", self.rules, "--portfolio", self.pf])[:2], (1, "No rules yet.\n"))

    def test_todays_file_is_fetched_from_30_minutes_after_the_close_and_15_minutes_apart(self):
        self.market("gap", upto="2026-10-29")                           # 10-30 and 11-02 are not saved
        self.book()
        self.write(self.cfg, {"psx_site": True})
        saved = (alerts.http_get, alerts.SLEEP)
        self.addCleanup(setattr, alerts, "http_get", saved[0])
        self.addCleanup(setattr, alerts, "SLEEP", saved[1])
        f = FakeSite()
        alerts.http_get, alerts.SLEEP = f.get, (lambda s: None)
        url = "https://dps.psx.com.pk/download/mkt_summary/2026-11-02.Z"

        def at(t):
            with mock.patch.object(alerts, "utc_now", return_value=pk(t)):
                return self.check(None)

        at("2026-11-02 15:59")
        self.assertNotIn(url, f.calls)
        at("2026-11-02 16:00")
        self.assertEqual(f.calls.count(url), 1)                         # a 404: not out yet
        at("2026-11-02 16:10")
        self.assertEqual(f.calls.count(url), 1)                         # less than 15 minutes later
        f.pages[url] = os.path.join(PATHS, "gap", "mkt_summary", "2026-11-02.Z")
        code, out, _ = at("2026-11-02 16:15")
        self.assertEqual(f.calls.count(url), 2)
        self.assertTrue(os.path.exists(os.path.join(self.data, "mkt_summary", "2026-11-02.Z")))   # saved
        self.assertIn(stop_ring("Mon 2026-11-02", 94.80, 95.60), out)
        self.assertIn("note: no closing file for Fri 2026-10-30 yet", out)          # older files: scan.py daily
        self.assertNotIn("https://dps.psx.com.pk/download/mkt_summary/2026-10-30.Z", f.calls)
        self.assertEqual(sorted(set(u for u in f.calls if "mkt_summary" in u)), [url])
        self.words()

    def test_check_json_lists_the_swing_positions(self):
        self.market("up")
        self.book()
        code, out, _ = self.check("2026-11-04 16:10", "--json")
        doc = json.loads(out)
        self.assertEqual((code, [r["id"] for r in doc["rings"]]), (0, ["demo-swing-target-2026-10-29"]))
        self.assertEqual(doc["swing"], [{"symbol": "DEMO", "date": "2026-10-29", "stop": 96.25, "target": 105.64,
                                         "exit_by": "2026-11-27", "closes_checked": "2026-11-04",
                                         "met": {"target": "2026-11-04"}}])
        self.assertIn("DEMO: checked the closing files of Thu 2026-10-29 to Wed 2026-11-04 (5 files).", doc["notes"])
        self.write(self.pf, {"cash": 1, "positions": [{"ticker": "DEMO", "shares": 1, "lots": []}]})
        self.write(self.rules, {"rules": [{"id": "bell", "type": "opening_bell"}]})
        doc = json.loads(self.check("2026-11-04 16:20", "--json")[1])
        self.assertEqual(sorted(doc), ["errors", "indices", "market", "quotes", "rings"])  # day: the v1 keys


class SwingMore(SwingCase):
    def test_the_swing_examples_in_the_skill_page_match_the_tool(self):
        with open(os.path.join(ROOT, "psx-alerts", "SKILL.md"), encoding="utf-8") as fh:
            page = fh.read()
        blocks = re.findall(r"```\n(PSX closed \(.*?)\n```", page, re.S)
        self.market("gap")
        self.book()
        gap = self.check("2026-11-02 16:10")[1].strip()
        shutil.rmtree(self.d)
        self.setUp()
        self.market("trend")
        self.book(target=112.0)
        self.check("2026-11-24 16:10")
        slept = self.check("2026-11-27 08:15")[1].strip()
        self.assertEqual(blocks, [gap, slept])
        self.assertEqual(len(re.findall(r"```\n(PSX OPEN.*?)\n```", page, re.S)), 3)   # the day blocks stay
        flat = " ".join(page.split())
        for line in ("Auto-run (psx-autorun) is the usual way.", "it needs an open Claude session",
                     "every closing file is checked once, even after the Mac slept",
                     "a met rule rings once a day until the share is sold", 'not "nothing hit"',
                     "autorun.py pause all --minutes 10"):
            self.assertIn(line, flat)
        self.words()

    def test_a_ring_from_the_buy_day_file_may_have_come_before_the_buy(self):
        self.market()
        self.book(stop=99.0)                                            # the 10-29 low is 98.90
        out = self.check("2026-10-29 16:10")[1]
        self.assertEqual(self.alerts_of(out), [
            "DEMO traded at or under your stop 99.00 on Thu 2026-10-29: the low in that day's closing file was 98.90 "
            "(official close 101.60). It may have come before your buy. Your plan's stop rule is met. " + ACT])
        self.words()

    def test_an_exit_by_day_closed_by_a_new_holiday_alerts_on_the_next_session(self):
        self.market("flat", upto="2026-11-26")
        self.book()
        self.write(self.cfg, {"holidays": {"2026-11-27": "a closure PSX announced late"}})
        self.assertEqual(self.alerts_of(self.check("2026-11-27 16:20")[1]), [])      # closed: no time rule rings
        self.assertEqual(self.alerts_of(self.check("2026-11-30 09:00")[1]), [
            "DEMO is past its exit-by day (Fri 2026-11-27) and still in psx/portfolio.json. The time stop is met. "
            + ACT])
        self.words()

    def test_the_rights_and_split_words(self):
        p = alerts.swing_positions(contract("portfolio-swing.json"))[0][0]
        today = datetime.date(2026, 11, 12)
        xr = alerts._ex_event(["XR"], 95.0, 100.0, True)
        xr["date"] = "2026-11-12"
        text = alerts._ex_words(p, xr, today)
        self.assertTrue(text.startswith("DEMO went ex today (XR): the reference price fell from 100.00 to 95.00 "
                                        "(factor 0.9500)."), text)
        self.assertTrue(text.endswith("A right letter may trade too. Pay for the right shares, or sell the right "
                                      "letter, by the date in the company's notice."), text)
        self.assertNotIn("bonus shares", text)
        split = alerts._ex_event([], 50.0, 100.0, True)
        split["date"] = "2026-11-12"
        self.assertEqual(split["kind"], "split")
        self.assertIn("(no flag: a split?): the reference price fell from 100.00 to 50.00 (factor 0.5000)",
                      alerts._ex_words(p, split, today))
        self.assertIsNone(alerts._ex_event([], 50.0, 100.0, False))     # never across a missing file
        self.assertIsNone(alerts._ex_event([], 91.0, 100.0, True))      # 9%: not a split
        self.assertIsNone(alerts._ex_event(["XD"], 100.0, 100.0, True))  # no lower LDCP yet
        from _words import check_words
        check_words(self, "\n".join([text, alerts._ex_words(p, split, today)]))

    def test_the_page_look_reads_three_shares_at_most(self):
        syms = ("DEMO", "OGDC", "HBL", "FFC")
        f = FakeSite(dict(("https://dps.psx.com.pk/company/%s" % s, os.path.join(MARKET, "company", s + ".html"))
                          for s in syms))
        saved = (alerts.http_get, alerts.SLEEP)
        self.addCleanup(setattr, alerts, "http_get", saved[0])
        self.addCleanup(setattr, alerts, "SLEEP", saved[1])
        alerts.http_get, alerts.SLEEP = f.get, (lambda s: None)
        positions = [{"symbol": s, "key": "%s-2026-10-27" % s} for s in syms]
        state, errors, notes = {}, [], []
        alerts.ex_look(positions, {}, MARKET, {"psx_site": True}, pk("2026-10-29 08:15"), state, False, errors,
                       notes)
        self.assertEqual(f.calls, ["https://dps.psx.com.pk/company/%s" % s for s in syms[:3]])


# ---------------------------------------------------------------- fix round 1 (the v2 review findings)

class Raising(FakeSite):
    """A FakeSite that raises an error for some URLs: a timeout, a reset, a bad answer (made up)."""

    def __init__(self, pages=None, codes=None, raises=None):
        FakeSite.__init__(self, pages, codes)
        self.raises = dict(raises or {})

    def get(self, url, timeout=30):
        if url in self.raises:
            self.calls.append(url)
            raise self.raises[url]
        return FakeSite.get(self, url, timeout)


class FixCase(SwingCase):
    def site(self, f, **cfg):
        """The PSX site on, faked by f (no network)."""
        self.write(self.cfg, dict({"psx_site": True}, **cfg))
        saved = (alerts.http_get, alerts.SLEEP)
        self.addCleanup(setattr, alerts, "http_get", saved[0])
        self.addCleanup(setattr, alerts, "SLEEP", saved[1])
        alerts.http_get, alerts.SLEEP = f.get, (lambda s: None)
        return f

    def at(self, t, *more):
        """A check on the real clock (no --now, no --offline), the clock set to Karachi time t."""
        with mock.patch.object(alerts, "utc_now", return_value=pk(t)):
            return self.check(None, *more)

    def page_file(self, name, text):
        path = os.path.join(self.d, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def broker(self, *recs):
        """psx/state/broker.json next to the state file (made up)."""
        self.write(os.path.join(self.d, "broker.json"), {"orders": list(recs)})


class FixHeldNoPlan(FixCase):
    NOTE = ("DEMO: you hold it with no swing plan in psx/portfolio.json, so the swing checks do not watch it. %s")

    def test_a_share_held_with_no_swing_plan_gets_a_note_in_a_swing_folder(self):
        # F02: the swing checks skip a position with no plan block; in a swing folder the check says so
        self.market("gap")
        self.write(self.cfg, {"style": "swing"})
        self.write(self.pf, {"cash": 1000, "positions": [{"ticker": "DEMO", "shares": 24, "lots": []}]})
        self.write(self.rules, {"rules": [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 90.0},
                                          {"id": "demo-old", "symbol": "DEMO", "type": "stop", "price": 90.0,
                                           "date": "2026-10-01"}]})
        note = "note: " + self.NOTE % "Only your own rules in psx/alerts.json watch it: 1 today."
        code, out, _ = self.check("2026-11-02 16:10")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 1 rule(s) watched today", out)
        self.assertIn(note, out)
        self.assertNotIn("closing file", out)                           # still no walk: the note says so
        self.assertEqual(json.loads(self.check("2026-11-02 16:10", "--json")[1])["notes"], [note[6:]])
        code, out, _ = run(["list", "--rules", self.rules, "--state", self.state, "--portfolio", self.pf,
                            "--config", self.cfg])
        self.texts.append(out)
        self.assertEqual((code, out.splitlines()[-1]), (0, note))
        os.remove(self.rules)                                           # no rules at all
        code, out, err = self.check("2026-11-02 16:10")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("alerts.py: no rules", err)
        self.assertIn("note: " + self.NOTE % "No rule in psx/alerts.json watches it today.", err)
        self.texts.append(err)
        self.write(self.cfg, {})                                        # a day folder: as in v1, no note
        self.write(self.rules, {"rules": [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 90.0}]})
        code, out, _ = self.check("2026-11-02 16:10")
        self.assertNotIn("swing plan", out)
        self.assertNotIn("notes", json.loads(self.check("2026-11-02 16:10", "--json")[1]))
        self.assertNotIn("swing plan", run(["list", "--rules", self.rules, "--portfolio", self.pf, "--config",
                                            self.cfg])[1])
        self.words()


class FixMetNotNothingHit(FixCase):
    def test_a_met_swing_rule_is_never_nothing_hit(self):
        # F13: the stop rang from a quote at 10:00; the 16:10 check reads that day's file and rings nothing new
        self.market("gap")
        self.book()
        self.check("2026-10-30 17:00")
        self.tape("2026-11-02 09:55", 95.29)
        self.indices("2026-11-02 09:55")
        self.assertEqual(self.check("2026-11-02 10:00")[0], 0)
        self.tape("2026-11-02 11:55", 95.10)
        self.indices("2026-11-02 11:55")
        code, out, _ = self.check("2026-11-02 12:00")                  # in the session, a fresh price
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertNotIn("Nothing hit", out)
        self.assertIn("No new alert. ", out)
        line = ("DEMO: the stop rule was met on Mon 2026-11-02. It rang today at 10:00 AM PKT. You still hold 1,436 "
                "shares in psx/portfolio.json.")
        self.assertIn(line, out)
        code, out, _ = self.check("2026-11-02 16:10")
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertNotIn("Nothing hit", out)
        self.assertIn("No new alert. 3 rule(s) watched today, DEMO 95.10 (as of 11:55 AM PKT).\n" + line, out)
        self.assertIn("note: DEMO: checked the closing file of Mon 2026-11-02.", out)
        self.write(self.pf, {"cash": 500000, "positions": []})          # sold: nothing is met any more
        self.write(self.rules, {"rules": [{"id": "bell", "type": "opening_bell"}]})
        self.assertIn("Nothing hit. 1 rule(s) watched today.", self.check("2026-11-02 16:20")[1])
        self.words()

    def test_a_met_time_stop_is_never_nothing_hit_either(self):
        # F13: the time stop rang at 16:20 on the exit-by day; later checks name it, also on the days after
        self.market("flat")
        self.book()
        self.check("2026-11-26 16:10")
        rang = self.alerts_of(self.check("2026-11-27 16:20")[1])        # in the session (exit 2: no fresh price)
        self.assertEqual(len(rang), 1)
        self.assertTrue(rang[0].startswith("DEMO: today is the exit-by day of your swing plan"), rang)
        code, out, _ = self.check("2026-11-27 16:40")
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertNotIn("Nothing hit", out)
        self.assertIn("DEMO: the time stop is met: today is the exit-by day. It rang today at 4:20 PM PKT. You still "
                      "hold 1,436 shares in psx/portfolio.json.", out)
        self.assertEqual(self.check("2026-11-30 08:15")[0], 0)          # the "past its exit-by day" ring
        out = self.check("2026-11-30 08:30")[1]
        self.assertNotIn("Nothing hit", out)
        self.assertIn("DEMO: the time stop is met: the exit-by day was Fri 2026-11-27. It rang today at 8:15 AM PKT. "
                      "You still hold 1,436 shares in psx/portfolio.json.", out)
        self.words()


class FixTodaysFile(FixCase):
    def test_todays_missing_file_after_the_close_gets_a_note(self):
        # F32: after the close, today's closing file is not saved yet: say so, and read it when it comes
        self.market("trend", upto="2026-11-24")
        self.book(target=112.0)
        self.check("2026-11-24 16:10")
        self.tape("2026-11-25 15:25", 100.5)
        self.write(self.cfg, {"psx_site": False})
        wait = "note: no closing file for Wed 2026-11-25 yet: its stop, target and trend checks wait."
        off = ("note: The PSX site is off for this tool, so this closing file does not come by itself. Save "
               "https://dps.psx.com.pk/download/mkt_summary/2026-11-25.Z as %s with your browser (PSX puts it out "
               "about 30 to 40 minutes after the close)." % os.path.join(self.data, "mkt_summary", "2026-11-25.Z"))
        code, out, _ = self.check("2026-11-25 15:00")                  # in the session: not out yet, no note
        self.assertNotIn("2026-11-25 yet", out)
        for t in ("2026-11-25 16:10", "2026-11-25 19:30"):
            code, out, _ = self.check(t)
            self.assertEqual(code, 1, t)
            self.assertIn(wait, out)
            self.assertIn(off, out)
            self.assertNotIn("closes_missing", self.read_state())       # the next walk reads it anyway
        shutil.copy(os.path.join(PATHS, "trend", "mkt_summary", "2026-11-25.Z"), os.path.join(self.data, "mkt_summary"))
        code, out, _ = self.check("2026-11-25 19:45")
        self.assertEqual(code, 0)
        self.assertIn("ALERT DEMO closed at 100.50 on Wed 2026-11-25 (the official close)", out)
        self.assertNotIn("yet:", out)
        self.words()

    def test_a_late_check_reads_a_file_that_came_after_the_evening(self):
        # F32: the evening note leaves the day out of closes_missing; the next morning's walk reads its file
        self.market("trend", upto="2026-11-24")
        self.book(target=112.0)
        self.check("2026-11-24 16:10")
        self.assertIn("no closing file for Wed 2026-11-25 yet", self.check("2026-11-25 16:10")[1])
        shutil.copy(os.path.join(PATHS, "trend", "mkt_summary", "2026-11-25.Z"), os.path.join(self.data, "mkt_summary"))
        out = self.check("2026-11-26 08:15")[1]
        self.assertIn("Your plan's trend rule was met on Wed 2026-11-25.", out)
        self.assertIn("note: DEMO: checked the closing file of Wed 2026-11-25.", out)
        self.words()


class FixTrendWindow(FixCase):
    def test_a_trend_rule_of_40_sessions_is_checked(self):
        # F38: the walk loads enough closing files for the trend rule's sessions, not 45 calendar days
        self.market("trend", upto="2026-11-13", skip=("2026-11-12", "2026-11-13"))
        folder = os.path.join(self.data, "mkt_summary")
        with open(os.path.join(folder, "2026-11-12.lis"), "w", encoding="latin-1") as fh:   # made-up falls
            fh.write("12NOV2026|DEMO|0818|Demo Industries|101.06|101.30|91.50|92.00|2000000|101.20|||\r\n")
        with open(os.path.join(folder, "2026-11-13.lis"), "w", encoding="latin-1") as fh:
            fh.write("13NOV2026|DEMO|0818|Demo Industries|91.80|92.10|88.80|89.00|2000000|92.00|||\r\n")
        self.book(stop=80.0, stop_at_buy=80.0, target=112.0, trend_sessions=40)
        self.assertEqual(self.alerts_of(self.check("2026-11-12 16:10")[1]), [])    # 92.00: over the 40-session low
        out = self.check("2026-11-13 16:40")[1]
        self.assertEqual(len(self.alerts_of(out)), 1, out)
        self.assertTrue(self.alerts_of(out)[0].startswith(
            "DEMO closed at 89.00 on Fri 2026-11-13 (the official close), under 90.43, the lowest close of the 40 "
            "sessions before. Your plan's trend rule is met."), out)
        self.assertNotIn("was not checked", out)
        self.words()

    def test_too_few_closes_for_the_trend_rule_give_a_note(self):
        # F38: a trend rule with fewer closes in the data folder than its sessions says it was not checked
        self.market()
        folder = os.path.join(self.data, "mkt_summary")
        for f in os.listdir(folder):
            if f < "2026-09-01":
                os.remove(os.path.join(folder, f))
        self.book(trend_sessions=60)
        out = self.check("2026-10-29 16:10")[1]
        n = len([f for f in os.listdir(folder) if f < "2026-10-29"])
        self.assertIn("note: DEMO: the trend rule (60 sessions) was not checked on Thu 2026-10-29: the data folder "
                      "has only %d closes of DEMO before that day." % n, out)
        self.book(trend_sessions=10)
        os.remove(self.state)
        self.assertNotIn("was not checked", self.check("2026-10-29 16:10")[1])
        self.words()


class FixSite(FixCase):
    SCREENER = "https://dps.psx.com.pk/screener"
    DAILY = "https://dps.psx.com.pk/download/mkt_summary/2026-10-28.Z"

    def test_a_closing_file_the_site_does_not_give_is_one_error_and_the_stop_still_rings(self):
        # F14: the screener is fine, the closing file of the session before is missing and its download fails
        shutil.copytree(MARKET, self.data, ignore=shutil.ignore_patterns("tape", "2026-10-28.Z", "2026-10-29.Z"))
        self.write(self.rules, {"rules": [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 102.0},
                                          {"id": "t", "type": "time", "at": "10:00", "label": "made-up time alert"}]})
        errs = ((socket.timeout("timed out"), "timed out"), (urllib.error.URLError("timed out"), "timed out"),
                (urllib.error.HTTPError(self.DAILY, 500, "Internal Server Error", {}, io.BytesIO(b"")),
                 "PSX answered HTTP 500"),
                (ConnectionResetError(54, "Connection reset by peer"), "[Errno 54] Connection reset by peer"),
                (http.client.BadStatusLine("garbage"), "garbage"))
        for e, why in errs:
            if os.path.exists(self.state):
                os.remove(self.state)
            f = self.site(Raising({self.SCREENER: os.path.join(MARKET, "screener.html")}, raises={self.DAILY: e}))
            code, out, err = self.at("2026-10-29 11:00", "--source", "psx-screener")
            self.assertEqual(code, 2, out + err)
            self.assertEqual(err, "")                                   # no traceback
            self.assertIn("ALERT DEMO is 101.60 at 10:55 AM PKT: at or below your stop 102.00.", out)
            self.assertIn("ALERT Made-up time alert: it is 11:00 PKT (set for 10:00)", out)
            lines = [x for x in out.splitlines() if x.startswith("error: ")]
            self.assertEqual(lines, ["error: could not read the closing file of the session before Thu 2026-10-29 "
                                     "from the PSX site (%s), so the screener prices have no last close and no price "
                                     "limits" % why], out)
            self.assertEqual(f.calls.count(self.DAILY), 1)              # asked once per check
            self.assertTrue(os.path.exists(self.state) and os.path.exists(self.prices))
        self.words()

    def test_a_bad_answer_from_the_site_is_an_error_line_not_a_crash(self):
        # F14: an answer the copied page_html does not catch (http.client.BadStatusLine) never stops the check
        self.market()
        self.write(self.rules, {"rules": [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 90.0},
                                          {"id": "t", "type": "time", "at": "10:00", "label": "made-up time alert"}]})
        self.site(Raising(raises={self.SCREENER: http.client.BadStatusLine("garbage")}))
        code, out, err = self.at("2026-10-29 11:00", "--source", "psx-screener")
        self.assertEqual((code, err), (2, ""))
        self.assertIn("ALERT Made-up time alert: it is 11:00 PKT (set for 10:00)", out)
        self.assertIn("error: could not read the prices: garbage", out)
        self.words()

    def test_an_old_screener_page_from_the_site_is_old_data(self):
        # F34: a site page whose own clock is old is dated by that clock, so D54 sees the old price
        shutil.copytree(MARKET, self.data, ignore=shutil.ignore_patterns("tape"))
        with open(os.path.join(MARKET, "screener.html"), encoding="utf-8") as fh:
            page = fh.read()
        clock = 'topbar__status__label">Oct 29, 2026 3:52 PM'
        self.assertIn(clock, page)
        old = self.page_file("old-screener.html", page.replace(clock, 'topbar__status__label">Oct 29, 2026 10:05 AM'))
        self.site(Raising({self.SCREENER: old, "https://dps.psx.com.pk/indices": os.path.join(MARKET, "indices.html")}))
        q, errors = alerts.get_quotes(["DEMO"], "psx-screener", self.data, {"psx_site": True}, pk("2026-10-29 10:30"),
                                      False)
        self.assertEqual((q["DEMO"]["time"], q["DEMO"]["fetched"], errors),
                         ("2026-10-29T05:00:00Z", "2026-10-29T05:30:00Z", []))      # 10:05 less 5 minutes
        tape = alerts.read_tape(alerts.tape_path(self.data, datetime.date(2026, 10, 29), "market"))
        self.assertEqual((tape[-1]["asof"], tape[-1]["fetched"]), ("2026-10-29 10:00:00", "2026-10-29T10:30:00+05:00"))
        self.book()
        code, out, _ = self.at("2026-10-29 10:30", "--source", "psx-screener")
        self.assertEqual(code, 2)
        self.assertIn("error: DEMO: no price newer than 15 minutes (newest 10:00 AM PKT). The swing stop was not "
                      "checked. Check the price in your broker app.", out)
        fresh = self.page_file("fresh-screener.html", page.replace(clock, 'topbar__status__label">Oct 29, 2026 10:30 AM'))
        self.site(Raising({self.SCREENER: fresh}))
        q, _ = alerts.get_quotes(["DEMO"], "psx-screener", self.data, {"psx_site": True}, pk("2026-10-29 10:30"), False)
        self.assertEqual(q["DEMO"]["time"], "2026-10-29T05:25:00Z")     # a fresh page: the fetch time less 5 minutes
        self.words()

    def test_a_screener_error_page_is_one_error_and_not_a_tape_line(self):
        # F77: a 200 answer with no share table never goes on the tape and never blames the symbols
        shutil.copytree(MARKET, self.data, ignore=shutil.ignore_patterns("tape"))
        tape = alerts.tape_path(self.data, datetime.date(2026, 10, 29), "market")
        no_table = ("the PSX screener page had no share table at https://dps.psx.com.pk/screener (an error page or a "
                    "new layout?)")
        for text in ("", "<html><body>Service Unavailable</body></html>"):
            self.site(Raising({self.SCREENER: self.page_file("bad.html", text)}))
            q, errors = alerts.get_quotes(["DEMO"], "psx-screener", self.data, {"psx_site": True},
                                          pk("2026-10-29 10:30"), False)
            self.assertEqual((q, errors), ({}, [no_table, "DEMO: no screener price (the PSX site could not be read)"]))
            self.assertFalse(os.path.exists(tape))
        alerts.append_tape(tape, {"fetched": "2026-10-29T10:25:00+05:00", "asof": "2026-10-29 10:20:00",
                                  "source": "screener", "prices": {"DEMO": 101.1}})   # a good page of 10:25
        q, errors = alerts.get_quotes(["DEMO"], "psx-screener", self.data, {"psx_site": True}, pk("2026-10-29 10:30"),
                                      False)
        self.assertEqual((q["DEMO"]["price"], q["DEMO"]["from"], errors), (101.1, "tape", [no_table]))
        self.assertEqual(len(alerts.read_tape(tape)), 1)
        self.write(self.rules, {"rules": [{"id": "demo-stop", "symbol": "DEMO", "type": "stop", "price": 90.0}]})
        code, out, _ = self.at("2026-10-29 10:30", "--source", "psx-screener")
        self.assertEqual(code, 2)                                       # a problem, not "ok", for auto-run
        self.assertIn("error: " + no_table, out)
        self.words()

    def test_an_error_page_for_the_ex_date_look_gives_a_note_once_a_day(self):
        # F78: a company page with no As-of time (an error page, an empty answer, a new layout) gets a note
        self.market("bonus", upto="2026-11-11")
        os.remove(os.path.join(self.data, "company", "DEMO.html"))
        self.book(stop=85.0, stop_at_buy=85.0, target=120.0)
        with open(os.path.join(MARKET, "company", "DEMO.html"), encoding="utf-8") as fh:
            layout = fh.read().replace("quote__date", "quote__when")
        note = ("note: DEMO: the company page could not be read for the ex-date look (the page from the PSX site has "
                "no As-of time: an error page or a new layout?). The closing file shows an ex-date after the close.")
        for text in ("", "<html><body>Error</body></html>", layout):
            if os.path.exists(self.state):
                os.remove(self.state)
            f = self.site(Raising({"https://dps.psx.com.pk/company/DEMO": self.page_file("demo.html", text),
                                   self.SCREENER: os.path.join(MARKET, "screener.html")}))
            self.assertIn(note, self.at("2026-11-12 08:15", "--source", "psx-screener")[1])
            self.assertNotIn("ex-date look", self.at("2026-11-12 08:30", "--source", "psx-screener")[1])  # once a day
            self.assertEqual(f.calls.count("https://dps.psx.com.pk/company/DEMO"), 2)
        shutil.copy(os.path.join(MARKET, "company", "DEMO.html"), os.path.join(self.data, "company"))   # Oct 29
        os.remove(self.state)
        self.site(Raising({self.SCREENER: os.path.join(MARKET, "screener.html")}))     # the company page: 404
        self.assertIn("note: DEMO: the company page could not be read for the ex-date look (the PSX site did not give "
                      "the page, and the saved page is as of Thu 2026-10-29 3:49 PM PKT). The closing file shows an "
                      "ex-date after the close.", self.at("2026-11-12 08:15", "--source", "psx-screener")[1])
        self.words()


class FixReminder(FixCase):
    PROTECT = SwingLive.PROTECT

    def remind(self, when, price=102.2):
        self.tape(when, price)
        return self.alerts_of(self.check(when)[1])

    def protect_log(self, day, *more):
        """psx-broker's log of a protect stop-limit sell of DEMO on `day`: the yes at 9:35, placed at 9:36, then
        each (HH:MM, status) of `more` (made up)."""
        base = {"fingerprint": "4f1f7cf1107d6b31", "symbol": "DEMO", "side": "sell", "qty": 1436, "kind": "exit",
                "plain": "SELL 1,436 DEMO, stop-limit, trigger 96.25, limit 95.28, good for today, REG board"}
        recs = [dict(base, at=day + "T09:35:00+05:00", date=day, status="approved", reply="yes 96.25"),
                dict(base, at=day + "T09:36:00+05:00", date=day, status="placed")]
        recs += [dict(base, at="%sT%s:00+05:00" % (day, t), date=day, status=s, of=day) for t, s in more]
        self.broker(*recs)

    def test_the_reminder_has_no_prices(self):
        # F31: the order's prices come from ticket.py protect after psx-risk, never from an alert
        positions = alerts.swing_positions(contract("portfolio-swing.json"))[0]
        ring = {"id": "demo-swing-protect-2026-10-29", "symbol": "DEMO", "message": "Stop-limit reminder: ..."}
        for cfg in ({"paper": False}, {"paper": False, "swing": {"stop_limit_gap_pct": 2.5}}):
            text = alerts.swing_words(ring, positions, {}, pk("2026-11-05 09:37"), cfg)
            self.assertEqual(text, self.PROTECT)
            for word in ("trigger", "96.25", "95.28", "limit 9"):
                self.assertNotIn(word, text)

    def test_no_reminder_when_todays_stop_limit_is_in_the_broker_log(self):
        # F73: today's stop-limit sell of the share is approved or placed: no "make a new ticket"
        self.market("flat", upto="2026-11-05")
        self.book()
        self.write(self.cfg, {"paper": False})
        self.protect_log("2026-11-05")
        self.assertEqual(self.remind("2026-11-05 09:37"), [])
        self.protect_log("2026-11-05", ("11:05", "cancelled"))         # cancelled: the reminder rings
        self.assertEqual(self.remind("2026-11-05 11:10"), [self.PROTECT])
        self.protect_log("2026-11-05")                                  # yesterday's stop-limit: not today's
        self.assertEqual(self.remind("2026-11-06 09:22"), [self.PROTECT])
        broker = os.path.join(self.d, "broker.json")
        with open(broker, encoding="utf-8") as fh:
            recs = json.load(fh)["orders"]
        os.remove(broker)
        other = os.path.join(self.d, "other.json")                     # --broker FILE
        self.write(other, {"orders": [dict(r, at=r["at"].replace("11-05", "11-10"), date="2026-11-10") for r in recs]})
        self.tape("2026-11-10 09:37", 102.2)
        self.assertEqual(self.alerts_of(self.check("2026-11-10 09:37", "--broker", other)[1]), [])
        self.words()

    def test_no_reminder_while_the_price_is_at_or_under_the_stop(self):
        # F73: a stop-limit at the stop would fire at once: the stop alert says it, the reminder waits
        self.market("flat", upto="2026-11-04")
        self.book()
        self.write(self.cfg, {"paper": False})
        self.tape("2026-11-05 09:36", 96.0)
        out = self.alerts_of(self.check("2026-11-05 09:37")[1])
        self.assertEqual(len(out), 1, out)
        self.assertTrue(out[0].startswith("DEMO is 96.00 at 9:36 AM PKT: at or below your stop 96.25"), out)
        # F16 (round 2): the price is back over the stop, but the stop rule was met today: the plan says
        # sell (flatten), so no stop-limit reminder (FixMetStopReminder has the other cases)
        self.assertEqual(self.remind("2026-11-05 10:00"), [])
        self.words()

    def test_the_reminder_waits_for_the_ex_date_answer(self):
        # F31: with a bonus not answered yet, the stop is on the old price scale: first scale or keep it
        self.market("bonus", upto="2026-11-12")
        self.book(stop=85.0, stop_at_buy=85.0, target=120.0)
        self.write(self.cfg, {"paper": False})
        self.check("2026-11-12 16:10")
        wait = ("DEMO: psx/state/broker.json has no stop-limit sell of today. Answer the ex-date alert first (scale or "
                "keep the stop). Then, if your broker app has none now, make a new ticket: ticket.py protect --symbol "
                "DEMO. psx-risk checks it, then your yes.")
        self.assertIn(wait, self.remind("2026-11-13 09:22", 90.3))
        self.book(stop=70.83, stop_at_buy=85.0, target=100.0,
                  scaled=[{"date": "2026-11-13", "factor": 0.8333, "why": "XB 20%"}])
        self.assertEqual(self.remind("2026-11-16 09:37", 90.6), [self.PROTECT])
        self.words()


class FixScales(FixCase):
    def test_a_scale_typed_before_the_ex_date_is_seen_still_counts(self):
        # F36, path 1: the scale comes before the walk sees the XB; it must still forget the old-scale stop
        self.market("bonus", upto="2026-11-11")
        self.book(target=120.0)
        self.check("2026-11-11 16:10")
        self.tape("2026-11-12 10:00", 89.9)
        self.indices("2026-11-12 10:00")
        out = self.check("2026-11-12 10:00")[1]
        self.assertIn("ALERT DEMO is 89.90 at 10:00 AM PKT: at or below your stop 96.25", out)   # the bonus alone
        self.book(stop=80.2, target=100.0, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB 20%"}])
        self.tape("2026-11-12 11:00", 90.1)
        self.indices("2026-11-12 11:00")
        self.check("2026-11-12 11:00")
        self.assertNotIn("swing_scales", self.read_state())            # no ex-date yet: the record waits
        shutil.copy(os.path.join(PATHS, "bonus", "mkt_summary", "2026-11-12.Z"), os.path.join(self.data, "mkt_summary"))
        code, out, _ = self.check("2026-11-12 16:10")
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertIn("Nothing hit.", out)
        self.assertNotIn("swing_met", self.read_state())
        self.assertEqual(self.read_state()["swing_scales"], {"DEMO-2026-10-29": ["2026-11-12 x0.8333"]})
        for f in ("2026-11-13.Z", "2026-11-16.Z"):
            shutil.copy(os.path.join(PATHS, "bonus", "mkt_summary", f), os.path.join(self.data, "mkt_summary"))
        for t in ("2026-11-13 08:15", "2026-11-13 16:40", "2026-11-16 08:15"):
            self.assertEqual(self.alerts_of(self.check(t)[1]), [], t)
        self.words()

    def test_a_scale_typed_before_the_ex_date_rings_a_real_hit_once(self):
        # F36: the walk meets the scaled stop, then sees the ex-date of the waiting scale and reads the file again
        self.market("bonus", upto="2026-11-11")
        self.book(target=120.0)
        self.check("2026-11-11 16:10")
        self.book(stop=90.0, target=100.0, scaled=[{"date": "2026-11-12", "factor": 0.8333, "why": "XB 20%"}])
        shutil.copy(os.path.join(PATHS, "bonus", "mkt_summary", "2026-11-12.Z"), os.path.join(self.data, "mkt_summary"))
        out = self.check("2026-11-12 16:10")[1]                        # the 11-12 low 89.51 is under 90.00
        rang = self.alerts_of(out)
        self.assertEqual(len(rang), 1, out)
        self.assertTrue(rang[0].startswith("DEMO traded at or under your stop 90.00 on Thu 2026-11-12"), rang)
        self.assertEqual(self.read_state()["swing_met"], {"DEMO-2026-10-29": {"stop": "2026-11-12"}})
        self.words()

    def test_a_scale_answers_the_bonus_not_a_later_payout(self):
        # F36, path 2: an XB, then an XD, then the scale: the scale answers the XB
        self.market("bonus", upto="2026-11-17", skip=("2026-11-16",))
        with open(os.path.join(self.data, "mkt_summary", "2026-11-16.lis"), "w", encoding="latin-1") as fh:
            fh.write("16NOV2026|DEMO|0818|Demo IndustriesXBXD|89.54|90.13|89.21|89.83|2000000|89.42|||\r\n")
        self.book(target=120.0)
        self.check("2026-11-11 16:10")
        self.assertIn("ALERT DEMO traded at or under your stop 96.25 on Thu 2026-11-12", self.check("2026-11-12 16:10")[1])
        out = self.check("2026-11-16 16:10")[1]
        self.assertIn("ALERT DEMO went ex today (XD).", out)
        self.assertIn("ALERT DEMO: the stop rule was met on Thu 2026-11-12.", out)
        self.book(stop=80.2, target=100.0, scaled=[{"date": "2026-11-17", "factor": 0.8333, "why": "XB 20%"}])
        code, out, _ = self.check("2026-11-17 16:10")
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertNotIn("swing_met", self.read_state())
        self.assertEqual(self.alerts_of(self.check("2026-11-18 08:15")[1]), [])
        self.words()

    def test_two_scales_round_down_one_at_a_time(self):
        # F68: ticket.py rounds the stop down after each scale; the hand-lowered note must do the same.
        # F15 (round 2): a scale counts only for an ex-date the check has seen (state["swing_ex"])
        def seen(*evs):
            return {"swing_ex": {"DEMO-2026-10-29": dict((d, {"kind": "XB", "flags": ["XB"], "factor": f, "date": d})
                                                         for d, f in evs)}}
        p = alerts.swing_positions(swing_book(stop=53.46, scaled=[
            {"date": "2026-11-04", "factor": 0.6667, "why": "XB 50%"},
            {"date": "2026-11-04", "factor": 1, "why": "keep"},
            {"date": "2026-11-05", "factor": 0.8333, "why": "XB 20%"}]))[0][0]
        both = seen(("2026-11-04", 0.6667), ("2026-11-05", 0.8333))
        self.assertIsNone(alerts.lowered_note(p, both))                 # 96.25 -> 64.16 -> 53.46
        p["stop"] = 53.45
        self.assertEqual(alerts.lowered_note(p, both), "DEMO's stop in psx/portfolio.json (53.45) is under its stop "
                                                       "at the buy (53.46 after the scales): it was lowered by hand.")
        p = alerts.swing_positions(swing_book(stop=72.90, scaled=[
            {"date": "2026-11-04", "factor": 0.8333, "why": "XB 20%"},
            {"date": "2026-11-05", "factor": 0.9091, "why": "XB 10%"}]))[0][0]
        self.assertIsNone(alerts.lowered_note(p, seen(("2026-11-04", 0.8333), ("2026-11-05", 0.9091))))   # 80.20, 72.90


class FixStopRaise(FixCase):
    RAISE = {"at": "2026-11-02T14:00:00+05:00", "date": "2026-11-02", "status": "stop", "symbol": "DEMO",
             "from": 96.25, "to": 100.5, "scale": None, "why": None, "plain": "DEMO stop 96.25 to 100.50"}

    def test_a_raised_stop_is_not_used_on_the_day_it_was_raised(self):
        # F37: Mon 11-02's low 100.30 came while the stop was 96.25; the raise to 100.50 came at 14:00
        self.market("trend")
        self.book(target=112.0)
        self.check("2026-10-30 17:00")
        self.book(target=112.0, stop=100.5)                             # ticket.py stop --set 100.50 at 14:00
        self.broker(self.RAISE)
        code, out, _ = self.check("2026-11-02 16:10")
        self.assertEqual((code, self.alerts_of(out)), (1, []))
        self.assertIn("note: DEMO: checked the closing file of Mon 2026-11-02.", out)
        out = self.check("2026-11-03 16:10")[1]                        # the low 100.36: the new stop was in force
        self.assertEqual(self.alerts_of(out), [
            "DEMO traded at or under your stop 100.50 on Tue 2026-11-03: the low in that day's closing file was "
            "100.36 (official close 100.90). Your plan's stop rule is met. " + ACT])
        self.assertEqual(self.read_state()["swing_met"], {"DEMO-2026-10-29": {"stop": "2026-11-03"}})
        self.words()

    def test_a_late_walk_judges_each_day_on_the_stop_of_that_day(self):
        # F37: no check from the buy day to Tue 11-03 (a Mac that slept), the raise on Mon 11-02 at 14:00
        self.market("trend")
        self.book(target=112.0, stop=100.5)
        self.broker(self.RAISE)
        out = self.check("2026-11-03 16:10")[1]
        rang = self.alerts_of(out)
        self.assertEqual(len(rang), 1, out)                             # 10-29, 10-30 and 11-02 on the stop 96.25
        self.assertTrue(rang[0].startswith("DEMO traded at or under your stop 100.50 on Tue 2026-11-03: the low"), out)
        self.assertIn("checked the closing files of Thu 2026-10-29 to Tue 2026-11-03 (4 files).", out)
        self.words()

    def test_a_buy_day_low_may_have_come_before_the_buy_and_a_later_day_rings_again(self):
        # F37: the buy day's file is in doubt; its daily line says so, and a later day's low rings again
        self.market("gap", upto="2026-10-29")
        self.book(stop=99.0)                                            # the 10-29 low is 98.90
        out = self.check("2026-10-29 16:10")[1]
        self.assertIn("It may have come before your buy.", out)
        self.assertEqual(self.read_state()["swing_maybe"], {"DEMO-2026-10-29": ["stop"]})
        self.assertEqual(self.alerts_of(self.check("2026-10-30 08:15")[1]), [
            "DEMO: the stop rule was met on Thu 2026-10-29. That was the buy day: it may have come before your buy. "
            "You still hold 1,436 shares in psx/portfolio.json. " + ACT])
        shutil.copy(os.path.join(PATHS, "gap", "mkt_summary", "2026-10-30.Z"), os.path.join(self.data, "mkt_summary"))
        out = self.check("2026-10-30 16:40")[1]                        # Fri 10-30: the low 96.90
        self.assertEqual(self.alerts_of(out), [
            "DEMO traded at or under your stop 99.00 on Fri 2026-10-30: the low in that day's closing file was 96.90 "
            "(official close 97.10). Your plan's stop rule is met. " + ACT])
        self.assertEqual(self.read_state()["swing_met"], {"DEMO-2026-10-29": {"stop": "2026-10-30"}})
        self.assertNotIn("swing_maybe", self.read_state())
        self.words()

    def test_a_buy_day_file_that_comes_late_keeps_the_later_day(self):
        # F37: the buy day's file comes after Fri 10-30 met the stop: the met day stays Fri 10-30
        self.market("gap", upto="2026-10-30")
        os.remove(os.path.join(self.data, "mkt_summary", "2026-10-29.Z"))
        self.book(stop=99.0)
        out = self.check("2026-10-30 16:40")[1]
        self.assertIn("note: no closing file for Thu 2026-10-29 yet", out)
        self.assertIn("DEMO traded at or under your stop 99.00 on Fri 2026-10-30", out)
        shutil.copy(os.path.join(MARKET, "mkt_summary", "2026-10-29.Z"), os.path.join(self.data, "mkt_summary"))
        self.check("2026-10-30 17:00")
        self.assertEqual(self.read_state()["swing_met"], {"DEMO-2026-10-29": {"stop": "2026-10-30"}})
        self.assertNotIn("swing_maybe", self.read_state())
        self.words()


class FixWords(FixCase):
    def test_a_factor_over_1_names_no_scale_the_broker_refuses(self):
        # F69: ticket.py stop --scale takes 1 or less, and no command lowers the share count
        from _words import check_words
        p = alerts.swing_positions(contract("portfolio-swing.json"))[0][0]
        ev = alerts._ex_event([], 125.0, 100.0, True)
        ev["date"] = "2026-11-12"
        text = alerts._ex_words(p, ev, datetime.date(2026, 11, 12))
        self.assertEqual(text, "DEMO went ex today (no flag: a split?): the reference price rose from 100.00 to 125.00 "
                               "(factor 1.2500). Your stop 96.25 and target 105.64 are on the old price scale, so the "
                               "target rule can ring on this alone, and the stop is far under the price. ticket.py stop "
                               "--scale takes a factor of 1 or less, so it cannot scale them up. You can raise the stop "
                               "(ticket.py stop --symbol DEMO --set X; the stop x 1.2500 is about 120.31). Then record "
                               "your choice: ticket.py stop --symbol DEMO --scale 1 (this alert then stops). Check your "
                               "share count in your broker app: it may have gone down. No command lowers it in "
                               "psx/portfolio.json.")
        self.assertNotIn("--scale 1.2500", text)
        self.assertNotIn("--bonus", text)
        p["target"] = None
        self.assertIn("Your stop 96.25 is on the old price scale, so the stop is far under the price.",
                      alerts._ex_words(p, ev, datetime.date(2026, 11, 12)))
        check_words(self, text)

    def test_an_ex_date_on_the_buy_day_is_not_the_buyers(self):
        # F74: a buy on the ex-date gets no payout and no bonus shares
        from _words import check_words
        p = alerts.swing_positions(contract("portfolio-swing.json"))[0][0]         # bought Thu 2026-10-29
        day = datetime.date(2026, 10, 29)
        xd = alerts._ex_event(["XD"], 97.0, 100.0, True)
        xd["date"] = "2026-10-29"
        self.assertEqual(alerts._ex_words(p, xd, day), (
            "DEMO went ex today (XD), the day you bought it. The reference price is about Rs 3.00 lower. A buy on the "
            "ex-date gets no payout, so this payout is not yours. Your stop stays 96.25."))
        xb = alerts._ex_event(["XB"], 83.33, 100.0, True)
        xb["date"] = "2026-10-29"
        text = alerts._ex_words(p, xb, day)
        self.assertEqual(text, (
            "DEMO went ex today (XB): the reference price fell from 100.00 to 83.33 (factor 0.8333). You bought on the "
            "ex-date, so no bonus share comes to you for it. If your plan was made before the ex-date, your stop 96.25 "
            "and target 105.64 are on the old price scale. Check them against your buy price, then choose: scale them "
            "(ticket.py stop --symbol DEMO --scale 0.8333) or keep them (--scale 1)."))
        xr = alerts._ex_event(["XR"], 95.0, 100.0, True)
        xr["date"] = "2026-10-29"
        self.assertIn("so no right comes to you for it.", alerts._ex_words(p, xr, day))
        self.assertNotIn("right letter", alerts._ex_words(p, xr, day))
        later = dict(xd, date="2026-10-30")                             # the day after the buy: the cash words
        self.assertIn("paid to you in cash", alerts._ex_words(p, later, datetime.date(2026, 10, 30)))
        check_words(self, "\n".join([text, alerts._ex_words(p, xd, day)]))

    def test_an_xd_on_the_buy_day_from_the_closing_file(self):
        # F74: the walk gives the buy-day words for an XD row on the buy day
        self.market("up", upto="2026-11-04")
        with open(os.path.join(self.data, "mkt_summary", "2026-11-05.lis"), "w", encoding="latin-1") as fh:
            fh.write("05NOV2026|DEMO|0818|Demo IndustriesXD|102.51|103.35|102.11|103.00|2000000|102.30|||\r\n")
        self.book(date="2026-11-05", target=120.0)                     # bought on the ex-date
        self.assertEqual(self.alerts_of(self.check("2026-11-05 16:10")[1]), [
            "DEMO went ex today (XD), the day you bought it. The reference price is about Rs 3.00 lower. A buy on the "
            "ex-date gets no payout, so this payout is not yours. Your stop stays 96.25."])
        self.words()

    def test_an_open_at_the_stop_is_no_gap(self):
        # F76: an open exactly at the stop sells at about the stop: no gap words
        self.market("gap")
        self.book(stop=95.40, stop_at_buy=95.40)                       # Mon 11-02 opens at 95.40, low 94.80
        out = self.check("2026-11-02 16:10")[1]
        self.assertEqual(self.alerts_of(out), [
            "DEMO traded at or under your stop 95.40 on Mon 2026-11-02: the low in that day's closing file was 94.80 "
            "(official close 95.60). Your plan's stop rule is met. " + ACT])
        self.book(stop=95.41, stop_at_buy=95.41)                       # one tick over the open: a gap
        os.remove(self.state)
        self.assertIn("DEMO opened at 95.40 on Mon 2026-11-02, under your stop 95.41 (a gap)",
                      self.check("2026-11-02 16:10")[1])
        self.words()


class FixPrices(FixCase):
    def test_an_older_quote_never_takes_the_place_of_a_newer_price_on_the_real_clock(self):
        # F71: a check or quote on the real clock keeps the newer price; a --now replay writes as in v1
        path = self.prices
        now = pk("2026-10-29 11:00")
        alerts.write_prices(path, {"DEMO": quote("DEMO", 101.3, "2026-10-29 10:52")}, now, "psx-company", keep_newer=True)
        doc = alerts.write_prices(path, {"DEMO": quote("DEMO", 100.9, "2026-10-29 10:30")}, now, "psx-company",
                                  keep_newer=True)
        self.assertEqual((doc["prices"]["DEMO"], doc["detail"]["DEMO"]["time"], doc["as_of"]),
                         (101.3, "2026-10-29T05:52:00Z", "2026-10-29T05:52:00Z"))
        doc = alerts.write_prices(path, {"DEMO": quote("DEMO", 100.9, "2026-10-29 10:30")}, now, "psx-company")
        self.assertEqual(doc["prices"]["DEMO"], 100.9)                  # keep_newer off: as in v1
        self.write(path, {"as_of": "2026-10-29T11:20:00+05:00", "source": "manual", "prices": {"DEMO": 102.0}})
        doc = alerts.write_prices(path, {"DEMO": quote("DEMO", 100.9, "2026-10-29 10:58")}, pk("2026-10-29 11:25"),
                                  "psx-screener", keep_newer=True)
        d = doc["detail"]["DEMO"]
        self.assertEqual((doc["prices"]["DEMO"], d["time"], d["source"]), (102.0, "2026-10-29T06:20:00Z", "manual"))
        self.write(path, {"as_of": "2026-10-29T11:40:00+05:00", "source": "manual", "prices": {"DEMO": 102.0}})
        doc = alerts.write_prices(path, {"DEMO": quote("DEMO", 100.9, "2026-10-29 10:58")}, pk("2026-10-29 11:25"),
                                  "psx-screener", keep_newer=True)
        self.assertEqual(doc["prices"]["DEMO"], 100.9)                  # a hand time later than now is not kept

    def test_the_quote_command_on_the_real_clock_keeps_a_newer_hand_price(self):
        self.write(self.prices, {"as_of": "2026-10-29T15:55:00+05:00", "source": "manual", "prices": {"DEMO": 101.9}})
        with mock.patch.object(alerts, "utc_now", return_value=pk("2026-10-29 16:00")):
            code, out, _ = run(["quote", "DEMO", "--offline", "--data", MARKET, "--write", self.prices])
        self.assertEqual(code, 0, out)
        with open(self.prices, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["prices"]["DEMO"], 101.9)    # the page of 15:49 is older
        code, out, _ = run(["quote", "DEMO", "--offline", "--now", "2026-10-29 16:00", "--data", MARKET, "--write",
                            self.prices])
        with open(self.prices, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["prices"]["DEMO"], 101.6)    # a replay writes what it read (v1)


class FixMetStopReminder(FixCase):
    """F16 (round 2): while the plan's stop rule is met, the plan says sell (psx-broker flatten). So the
    stop-limit reminder stays quiet: psx-risk would block that stop-limit too (it can fire at once)."""
    PROTECT = SwingLive.PROTECT

    def test_no_reminder_later_that_day_or_the_next_day_once_the_stop_rule_is_met(self):
        self.market("flat", upto="2026-11-04")
        self.book()
        self.write(self.cfg, {"paper": False})
        self.tape("2026-11-05 09:36", 96.0)
        rang = self.alerts_of(self.check("2026-11-05 09:37")[1])
        self.assertEqual(len(rang), 1, rang)                            # the stop alert only
        self.assertIn("Your plan's stop rule is met.", rang[0])
        self.tape("2026-11-05 10:00", 102.2)                            # back over the stop: still met
        out = self.check("2026-11-05 10:00")[1]
        self.assertEqual(self.alerts_of(out), [])
        self.assertIn("No new alert.", out)
        self.assertNotIn("demo-swing-protect-2026-10-29", self.read_state()["rung"])
        self.tape("2026-11-06 09:22", 102.4)                            # Friday: the match ends at 9:17
        rang = self.alerts_of(self.check("2026-11-06 09:22")[1])
        self.assertEqual(len(rang), 1, rang)                            # the met stop once a day, no reminder
        self.assertTrue(rang[0].startswith("DEMO: the stop rule was met on Thu 2026-11-05."), rang)
        self.words()

    def test_no_reminder_when_the_walk_of_the_same_check_meets_the_stop(self):
        # no check on Mon 11-02 (the Mac slept): the first check of Tue 11-03 reads the 11-02 file (low 94.80)
        # while the price is back over the stop. The stop rings; the reminder does not, now or later
        self.market("gap")
        self.book()
        self.write(self.cfg, {"paper": False})
        self.check("2026-10-30 17:00")
        self.tape("2026-11-03 09:40", 97.0)
        rang = self.alerts_of(self.check("2026-11-03 09:40")[1])
        self.assertEqual(len(rang), 1, rang)
        self.assertTrue(rang[0].startswith("DEMO traded at or under your stop 96.25 on Mon 2026-11-02"), rang)
        self.assertNotIn("demo-swing-protect-2026-10-29", self.read_state()["rung"])
        self.tape("2026-11-03 10:00", 97.2)
        self.assertEqual(self.alerts_of(self.check("2026-11-03 10:00")[1]), [])
        self.words()

    def test_a_buy_day_low_that_may_have_come_before_the_buy_keeps_the_reminder(self):
        self.market()
        self.book(stop=99.0)                                            # the 10-29 low is 98.90
        self.write(self.cfg, {"paper": False})
        self.check("2026-10-29 16:10")
        self.assertEqual(self.read_state()["swing_maybe"], {"DEMO-2026-10-29": ["stop"]})
        self.tape("2026-10-30 09:22", 100.5)
        self.assertIn(self.PROTECT, self.alerts_of(self.check("2026-10-30 09:22")[1]))
        self.words()


class FixHaltWords(FixCase):
    def test_the_halt_alert_names_the_command_that_records_the_cancelled_stop_limit(self):
        # F28 (round 2): after a halt, record today's stop-limit as cancelled first; then the reminder rings
        # again and ticket.py protect can make a new one
        self.market("flat", upto="2026-11-04")
        self.book()
        self.write(self.cfg, {"paper": False})
        FixReminder.protect_log(self, "2026-11-05")                     # today's stop-limit: approved, placed
        self.tape("2026-11-05 11:00", 102.2)
        self.indices("2026-11-05 11:00", drop=True)
        rang = self.alerts_of(self.check("2026-11-05 11:00")[1])
        self.assertEqual(len(rang), 1, rang)                            # the halt only: today's stop-limit is in
        m = re.search(r" If PSX cancelled your stop-limit, record it first: ticket\.py (result --symbol DEMO --date "
                      r"2026-11-05 --status cancelled)\. Then ticket\.py protect --symbol DEMO makes a new one\.$",
                      rang[0])
        self.assertIsNotNone(m, rang[0])
        ticket = load("psx-broker", "ticket")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ticket.main(m.group(1).split() + ["--state", os.path.join(self.d, "broker.json"), "--orders",
                                                     os.path.join(self.d, "orders.json"), "--config", self.cfg,
                                                     "--now", "2026-11-05 12:30"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("as cancelled", out.getvalue())
        self.tape("2026-11-05 12:35", 102.3)
        self.assertEqual(self.alerts_of(self.check("2026-11-05 12:35")[1]), [SwingLive.PROTECT])
        self.words()

    def test_the_halt_words_name_each_share_held(self):
        pf = swing_book()
        zed = json.loads(json.dumps(pf["positions"][0]))
        zed["ticker"] = "ZED"
        pf["positions"].append(zed)
        ring = {"id": "swing-halt", "symbol": "KSE30", "message": "KSE30 moved -5.12% to 45,079.73 at 11:00 AM PKT."}
        text = alerts.swing_words(ring, alerts.swing_positions(pf)[0], {}, pk("2026-11-05 11:00"), {})
        self.assertTrue(text.endswith(" record it first: ticket.py result --symbol DEMO --date 2026-11-05 --status "
                                      "cancelled. Then ticket.py protect --symbol DEMO makes a new one. Do the same "
                                      "for ZED."), text)
        self.texts.append(text)
        self.words()


class FixScaleNote(FixCase):
    """F15 (round 2): the "lowered by hand" note counts a ticket.py stop --scale record only for a bonus,
    rights or split ex-date the check has seen, and never under that ex-date's factor."""
    NOTE = "DEMO's stop in psx/portfolio.json (%.2f) is under its stop at the buy (%s): it was lowered by hand."

    def test_a_scale_record_counts_only_for_an_ex_date_and_its_factor(self):
        def seen(kind, factor):
            return {"swing_ex": {"DEMO-2026-10-29": {"2026-11-12": {"kind": kind, "flags": [kind] if kind[0] == "X"
                                                                    else [], "factor": factor, "date": "2026-11-12"}}}}

        def note(stop, factor, state, day="2026-11-12"):
            p = alerts.swing_positions(swing_book(stop=stop, scaled=[{"date": day, "factor": factor, "why": "x"}]))[0][0]
            return alerts.lowered_note(p, state)
        xb = seen("XB", 0.8333)
        self.assertIsNone(note(80.20, 0.8333, xb))                      # 96.25 x 0.8333 = 80.20 (ticket.py)
        self.assertIsNone(note(80.17, 0.833, xb))                       # a bit under the factor: ticket.py takes it
        self.assertEqual(note(80.00, 0.8333, xb), self.NOTE % (80.00, "80.20 after the scale"))
        self.assertEqual(note(80.20, 0.8333, {}), self.NOTE % (80.20, "96.25"))              # no ex-date: by hand
        self.assertEqual(note(80.20, 0.8333, None), self.NOTE % (80.20, "96.25"))
        self.assertEqual(note(80.20, 0.8333, xb, "2026-11-11"), self.NOTE % (80.20, "96.25"))   # dated before it
        self.assertEqual(note(67.37, 0.70, xb), self.NOTE % (67.37, "80.20 after the scale"))   # under its factor
        self.assertEqual(note(93.32, 0.9696, seen("XD", 0.9696)), self.NOTE % (93.32, "96.25"))   # a payout
        self.assertEqual(note(80.20, 0.8333, seen("split", 2.0)), self.NOTE % (80.20, "96.25"))   # a price that rose

    def test_the_note_in_a_check_after_a_bonus(self):
        # the XB of Thu 11-12 (factor 0.8333) is in the closing file of the "bonus" path
        self.market("bonus", upto="2026-11-12")
        scaled = [{"date": "2026-11-12", "factor": 0.8333, "why": "XB 20%"}]
        self.book(stop=80.2, target=120.0, scaled=scaled)
        self.assertNotIn("lowered by hand", self.check("2026-11-12 16:10")[1])
        self.book(stop=80.0, target=120.0, scaled=scaled)
        self.assertIn("note: " + self.NOTE % (80.00, "80.20 after the scale"), self.check("2026-11-12 16:20")[1])
        self.book(stop=80.2, target=120.0, scaled=[dict(scaled[0], date="2026-11-11")])  # dated before the XB
        self.assertIn("note: " + self.NOTE % (80.20, "96.25"), self.check("2026-11-12 16:30")[1])
        self.words()


class FixSkillPage(unittest.TestCase):
    def test_the_skill_page_says_what_the_round_2_fixes_do(self):
        with open(os.path.join(ROOT, "psx-alerts", "SKILL.md"), encoding="utf-8") as fh:
            page = fh.read()
        flat = " ".join(page.split())
        # F105: the reminder comes at auto-run's first check: the first 5-minute tick from 7 minutes after the open
        autorun = load("psx-autorun", "autorun")
        when = []
        for d in (datetime.date(2026, 11, 5), datetime.date(2026, 11, 6), datetime.date(2027, 2, 15)):
            start = datetime.datetime.combine(d, alerts.session(d)[0][0])
            first = autorun.first_tick(start + datetime.timedelta(minutes=autorun.DEFAULTS["check_start_after_min"]))
            self.assertGreaterEqual(first, alerts._first_match(d))     # after the opening match: it rings there
            when.append(alerts.clock_label(first))
        self.assertEqual(when[1], when[2])                              # a Friday and a Ramadan day: the same
        row = "the first check after the opening match (about %s with auto-run; %s on Fridays and in Ramadan)" % (
            when[0], when[1])
        self.assertTrue(row in flat, row)
        for words in ("It does not ring while the stop rule is met.",                                 # F16
                      "A low only in the buy day's file may have come before your buy. It does not stop the reminder.",
                      "If PSX cancelled it, record that first: `ticket.py result --symbol DEMO --date <today> --status "
                      "cancelled`. Then `ticket.py protect --symbol DEMO` makes a new one.",            # F28
                      "A scale written by hand with no such ex-date counts for nothing.",               # F15
                      "Its rules in `psx/alerts.json` use only today's prices, never a closing file.",  # F07
                      'Give its stop `"rearm": "daily"`', "loses no close when the Mac slept", "No new alert."):
            self.assertTrue(words in flat, words)
        self.assertFalse("So a Mac that slept loses no close." in flat)  # only a share with its swing plan
        from _words import problems
        self.assertEqual(problems(page), [])


if __name__ == "__main__":
    unittest.main()
