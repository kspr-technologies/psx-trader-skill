import datetime
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

facts = load("psx-analyst", "facts")
MARKET = os.path.join(FIX, "psx-market")      # the shared sample market (made up)
MINE = os.path.join(FIX, "psx-analyst")       # this skill's own made-up pages
NO_CONFIG = os.path.join(MINE, "no-such-config.json")
NOW = "2026-10-29 16:00"                      # Thu 2026-10-29, after the close (EXAMPLE.md)
TODAY = datetime.date(2026, 10, 29)
NETWORK = []                                  # every URL a test tried to fetch
ZLAG_NOTICE = ("Subject: BOARD MEETING\n\nA meeting of the Board of Directors of Zed Lag Industries Limited will be\n"
               "held on Wednesday, October 28, 2026 at 2:30 p.m. at Karachi to consider the accounts for the\n"
               "quarter ended September 30, 2026.\n\nThe closed period will be from October 21, 2026 to "
               "October 28, 2026.\n")
OGDC_NOTICE = ("Subject: BOARD MEETING AND CLOSED PERIOD\n\nA meeting of the Board of Directors of the Company will "
               "be held on\nOctober 30, 2026 at 1100 hours at Islamabad to consider the accounts for the period "
               "ended September 30, 2026.\n\nThe closed period will be from October 23, 2026 to October 30, 2026 "
               "(both days inclusive).\n")
OTHER_ROW = {"date": "2026-10-28", "title": "Board Meeting Other Than Financial Results", "doc_id": "299999",
             "pdf": "https://dps.psx.com.pk/download/document/299999.pdf", "image": None, "attachment": None}


def ask(link, path):
    """What the tool prints with the site off: the user saves the file, Claude never fetches it."""
    return ("  Ask the user to open %s in their browser and save it as %s, or paste a screenshot. "
            "Do not fetch it yourself." % (link, path))


def no_network(url, timeout=30):
    NETWORK.append(url)
    raise AssertionError("a test tried the network: %s" % url)


facts.http_get = no_network                   # tests never touch the network
facts.SLEEP = lambda seconds: None
REAL_PDF_TEXT = facts.pdf_text


def page(sym, root=MARKET):
    with open(os.path.join(root, "company", sym + ".html"), encoding="utf-8") as fh:
        return facts.parse_company(fh.read())


def analyse(sym, root=MARKET, today=TODAY):
    return facts.analyse(page(sym, root), today)


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = facts.main(args)
    return code, out.getvalue(), err.getvalue()


def offline(args, data=MARKET, now=NOW):
    return run(list(args) + ["--offline", "--data", data, "--now", now, "--config", NO_CONFIG])


def row(result, label):
    return next(r for r in result["quarters"] if r["label"] == label)


def checks(result, code):
    return [c["text"] for c in result["checks"] if c["code"] == code]


class TempData(unittest.TestCase):
    """Helpers for tests that need a temp folder (they never write to the fixtures)."""

    def temp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        return tmp

    def copy(self, src):
        data = os.path.join(self.temp(), "data")
        shutil.copytree(src, data)
        os.makedirs(os.path.join(data, "docs"), exist_ok=True)
        return data

    def swap_pdf_text(self, fn):
        facts.pdf_text = fn
        self.addCleanup(setattr, facts, "pdf_text", REAL_PDF_TEXT)


class Demo(unittest.TestCase):
    """DEMO on Thu 2026-10-29: the numbers of EXAMPLE.md section 11."""

    def setUp(self):
        self.r = analyse("DEMO")

    def test_newest_quarter_against_the_same_quarter_a_year_back(self):
        q = self.r["quarters"][0]
        self.assertEqual((q["label"], q["end"], q["derived"], q["label_ok"]), ("Q1 2027", "2026-09-30", False, True))
        self.assertEqual((q["sales"], q["profit"], q["eps"]), (10210451, 838074, 3.35))
        n = self.r["newest"]
        self.assertEqual(n["back"], "Q1 2026")
        self.assertEqual((n["sales"], n["profit"], n["eps"]), (13.0, 22.04, 21.82))
        self.assertEqual((n["net_margin"], n["net_margin_back"]), (8.21, 7.6))

    def test_quarter_ends_follow_the_june_year_end(self):
        got = [(q["label"], q["end"]) for q in self.r["quarters"]]
        self.assertEqual(got, [("Q1 2027", "2026-09-30"), ("Q4 2026", "2026-06-30"), ("Q3 2026", "2026-03-31"),
                               ("Q2 2026", "2025-12-31"), ("Q1 2026", "2025-09-30")])
        self.assertEqual(self.r["fiscal_year_end"], "June")
        self.assertEqual(self.r["units"], "Rs thousands, except EPS in rupees")

    def test_q4_is_derived_from_the_year(self):
        q4 = row(self.r, "Q4 2026")
        self.assertTrue(q4["derived"])
        # FY2026 38,450,200 minus Q1 9,035,797, Q2 9,420,299 and Q3 9,612,550
        self.assertEqual((q4["sales"], q4["profit"], q4["eps"]), (10381554, 788997, 3.16))
        self.assertEqual(q4["net_margin"], 7.6)
        self.assertEqual(checks(self.r, "derived"), ["Q4 2026 is derived: the year 2026 minus Q1, Q2 and Q3 on "
                                                     "the page. Its EPS is approximate."])

    def test_quarter_before_and_the_last_four_quarters(self):
        q = self.r["qoq"]
        self.assertEqual((q["label"], q["before"], q["derived"]), ("Q1 2027", "Q4 2026", True))
        self.assertEqual((q["sales"], q["profit"]), (-1.65, 6.22))
        t = self.r["ttm"]
        self.assertEqual(t["quarters"], ["Q1 2027", "Q4 2026", "Q3 2026", "Q2 2026"])
        self.assertEqual((t["sales"], t["profit"], t["eps"], t["derived"]), (39624854, 3073568, 12.29, True))
        self.assertEqual(round(self.r["price"] / t["eps"], 2), self.r["pe"])   # 101.60 / 12.29 = the page's 8.27

    def test_last_full_year_and_the_page_ratios(self):
        y = self.r["year"]
        self.assertEqual((y["label"], y["end"]), ("2026", "2026-06-30"))
        self.assertEqual((y["sales"], y["profit"], y["eps"]), (38450200, 2922215, 11.69))
        self.assertEqual((y["net_margin"], y["gross_margin"]), (7.6, 24.85))
        self.assertEqual((y["sales_growth"], y["profit_growth"], y["eps_growth"]), (13.0, 22.83, 22.79))
        self.assertEqual((self.r["price"], self.r["pe"], self.r["shares"], self.r["free_float_pct"]),
                         (101.6, 8.27, 250000000, 30.0))

    def test_what_changed_in_words(self):
        lines = facts.changes(self.r)
        self.assertEqual(lines[0], "Q1 2027 (Jul to Sep 2026) against Q1 2026, a year back: sales +13.00%, "
                                   "profit +22.04%, EPS +21.82% (3.35 against 2.75).")
        self.assertIn("Net margin 8.21% (profit / sales). A year back: 7.60%.", lines)
        self.assertTrue(any(line.startswith("Against the quarter before, Q4 2026 (derived): sales -1.65%, "
                                            "profit +6.22%.") for line in lines))
        self.assertTrue(any(line.startswith("Last full year, 2026 (to 2026-06-30): sales 38,450,200, profit "
                                            "2,922,215 (Rs thousands), EPS 11.69.") for line in lines))
        self.assertIn("Last 4 quarters (one derived): sales 39,624,854, profit 3,073,568 (Rs thousands), "
                      "EPS about 12.29.", lines)
        self.assertIn("On the page: price 101.60, P/E (TTM) 8.27.", lines)

    def test_figures_are_unconsolidated(self):
        self.assertEqual(self.r["figures"], "unconsolidated")
        self.assertEqual(checks(self.r, "unconsolidated"),
                         ["Figures are unconsolidated: the company alone, not its group."])
        self.assertEqual(checks(self.r, "label") + checks(self.r, "one_off"), [])


class WrongLabel(unittest.TestCase):
    """FFC: a December year end, yet the page calls its Jul to Sep 2026 quarter "Q1 2027"."""

    def setUp(self):
        self.r = analyse("FFC")

    def test_the_label_is_flagged(self):
        q = self.r["quarters"][0]
        self.assertEqual((q["label"], q["label_ok"], q["fiscal"], q["end"]), ("Q1 2027", False, "Q3 2026", "2026-09-30"))
        note = checks(self.r, "label")[0]
        self.assertTrue(note.startswith("Q1 2027: the label looks wrong; check the results notice."), note)
        self.assertIn("With a December year end, Q1 2027 would end on 2027-03-31", note)
        self.assertIn("so it is most likely Q3 2026 (Jul to Sep 2026).", note)

    def test_growth_follows_the_page_layout(self):
        n = self.r["newest"]
        self.assertEqual(n["back"], "Q3 2025")      # the page puts the same quarter a year back last
        self.assertEqual((n["sales"], n["profit"], n["eps"]), (11.0, 20.65, 20.7))
        self.assertEqual(self.r["qoq"]["before"], "Q2 2026")
        self.assertIsNone(self.r["ttm"])             # Q4 2025 is not on the page

    def test_a_wrong_label_with_no_layout_to_go_by(self):
        p = page("FFC")
        qt = p["financials"]["quarterly"]
        qt["columns"] = qt["columns"][:3]
        qt["rows"] = dict((k, v[:3]) for k, v in qt["rows"].items())
        r = facts.analyse(p, TODAY)
        q = r["quarters"][0]
        self.assertEqual((q["label"], q["label_ok"], q["fiscal"], q["end"]), ("Q1 2027", False, None, None))
        self.assertIsNone(r["newest"]["back"])
        self.assertIsNone(r["qoq"])
        self.assertNotIn("most likely", checks(r, "label")[0])

    def test_labels_that_do_not_fit_each_other(self):
        p = page("SYS")
        p["financials"]["quarterly"]["columns"][-1] = "Q2 2025"
        r = facts.analyse(p, TODAY)
        self.assertTrue(checks(r, "label")[0].startswith("Q3 2026 and Q2 2025: the labels do not fit; check the "
                                                         "results notice."))
        self.assertIsNone(r["newest"]["back"])
        self.assertTrue(r["quarters"][0]["label_ok"])

    def test_no_fiscal_year_end_means_no_label_check(self):
        p = page("FFC")
        p["profile"]["fiscal_year_end"] = None
        r = facts.analyse(p, TODAY)
        self.assertEqual(checks(r, "label"), [])
        self.assertEqual(checks(r, "fiscal_year"),
                         ["The page gives no fiscal year end, so the quarter labels were not checked."])
        self.assertIsNone(r["quarters"][0]["end"])


class Bank(unittest.TestCase):
    """HBL: a bank shows Mark-up Earned and Total Income, and has no gross margin."""

    def setUp(self):
        self.r = analyse("HBL")

    def test_markup_earned_is_the_top_line(self):
        self.assertTrue(self.r["bank"])
        self.assertEqual(self.r["top_line"], "mark-up earned")
        q = self.r["quarters"][0]
        self.assertEqual((q["label"], q["sales"], q["income"], q["profit"], q["eps"]),
                         ("Q3 2026", 163064082, 84793322, 16755160, 11.42))
        self.assertEqual(q["net_margin"], 10.28)
        self.assertEqual(self.r["year"]["net_margin"], 9.45)     # the page's own Net Profit Margin for 2025
        n = self.r["newest"]
        self.assertEqual((n["back"], n["sales"], n["income"], n["profit"]), ("Q3 2025", 9.0, 9.0, 18.48))

    def test_no_gross_margin_blank_not_zero(self):
        self.assertIsNone(self.r["year"]["gross_margin"])
        self.assertIn("A bank has no gross margin: it is blank, not zero.", checks(self.r, "bank")[0])
        code, out, err = offline(["HBL"])
        self.assertEqual(code, 0, err)
        self.assertIn("quarter  ends        mark-up earned  total income      profit    EPS  net margin", out)
        self.assertNotIn("gross margin", out.replace("A bank has no gross margin", ""))

    def test_one_offs_for_a_bank_use_total_income(self):
        p = page("HBL")
        rows = p["financials"]["annual"]["rows"]
        rows["Mark-up Earned"][0] = rows["Mark-up Earned"][1] * 0.7   # rates fell: mark-up earned -30%
        self.assertEqual(checks(facts.analyse(p, TODAY), "one_off"), [])  # profit -3.58%, total income +9.00%
        rows["Profit after Taxation"][0] = rows["Profit after Taxation"][1] * 1.4
        texts = checks(facts.analyse(p, TODAY), "one_off")
        self.assertEqual(texts, ["In the last year, profit growth (+40.00%) is far above total income growth "
                                 "(+9.00%): check for one-off items."])


class OneOffs(unittest.TestCase):
    """ZONE (made up): a big one-off gain in 2025."""

    def setUp(self):
        self.r = analyse("ZONE", MINE)

    def test_profit_above_sales_is_flagged(self):
        self.assertIn("Profit is above sales in 2025: check for one-off items (other income, a gain or a tax item).",
                      checks(self.r, "one_off"))

    def test_profit_growth_far_above_sales_growth(self):
        texts = checks(self.r, "one_off")
        self.assertIn("In the newest quarter, profit growth (+50.00%) is far above sales growth (+5.00%): check "
                      "for one-off items.", texts)
        self.assertIn("In the last year, profit growth (+1300.00%) is far above sales growth (+5.26%): check for "
                      "one-off items.", texts)
        self.assertEqual(checks(analyse("DEMO"), "one_off"), [])   # +22.04% against +13.00% is not far above

    def test_no_gross_margin_on_the_page(self):
        self.assertIsNone(self.r["year"]["gross_margin"])
        self.assertEqual(checks(self.r, "no_gross_margin"),
                         ["The page has no gross margin for 2025: it is blank, not zero."])


class Numbers(unittest.TestCase):
    def test_growth_and_margins_round_half_up(self):
        self.assertEqual(facts.pct(838074, 686721), 22.04)
        self.assertEqual(facts.pct(10210451, 9035797), 13.0)
        self.assertEqual(facts.pct(-50, -100), 50.0)               # a smaller loss is growth
        self.assertIsNone(facts.pct(1, 0))
        self.assertIsNone(facts.pct(None, 1))
        self.assertEqual(facts.margin(838074, 10210451), 8.21)
        self.assertIsNone(facts.margin(5, 0))
        self.assertEqual(facts.minus(11.69, [2.75, 2.86, 2.92]), 3.16)   # Decimal: no float dust
        self.assertIsNone(facts.minus(11.69, [2.75, None, 2.92]))

    def test_fiscal_quarters(self):
        d = datetime.date
        self.assertEqual(facts.quarter_end(1, 2027, 6), d(2026, 9, 30))    # June year end: Q1 2027 is Jul to Sep 2026
        self.assertEqual(facts.quarter_end(3, 2026, 12), d(2026, 9, 30))   # December year end
        self.assertEqual(facts.quarter_end(4, 2026, 6), d(2026, 6, 30))
        self.assertEqual(facts.quarter_end(2, 2026, 6), d(2025, 12, 31))
        self.assertEqual(facts.quarter_of(d(2026, 9, 30), 6), (1, 2027))
        self.assertEqual(facts.quarter_of(d(2026, 9, 30), 12), (3, 2026))
        self.assertIsNone(facts.quarter_of(d(2026, 8, 31), 6))
        self.assertEqual(facts.step((1, 2027), -1), (4, 2026))
        self.assertEqual(facts.step((3, 2025), 4), (3, 2026))
        self.assertEqual((facts.fiscal_month("30th June"), facts.fiscal_month("December")), (6, 12))
        self.assertIsNone(facts.fiscal_month(None))
        self.assertEqual(facts.span_text(d(2026, 9, 30)), "Jul to Sep 2026")
        self.assertEqual(facts.span_text(d(2027, 1, 31)), "Nov 2026 to Jan 2027")
        self.assertEqual(facts.parse_label("Q1 2027"), (1, 2027))
        self.assertIsNone(facts.parse_label("2026"))


class Notices(TempData):
    def zlag_with(self, name, body):
        data = self.copy(MINE)
        with open(os.path.join(data, "docs", name), "wb") as fh:
            fh.write(body)
        return data

    def test_latest_results_notice(self):
        code, out, err = offline(["DEMO", "--json"])
        self.assertEqual(code, 0, err)
        res = json.loads(out)[0]["notices"]["results"]
        self.assertEqual((res["date"], res["doc_id"], res["period_end"]), ("2026-10-27", "290000", "2026-09-30"))
        self.assertEqual(res["title"], "FINANCIAL RESULTS FOR THE QUARTER ENDED SEPTEMBER 30, 2026")
        self.assertEqual(res["link"], "https://dps.psx.com.pk/download/document/290000.pdf")
        # the results letter (docs/290000.txt) shows the page's sales, profit and EPS
        self.assertEqual((res["check"], res["text_from"]), ("same", "docs/290000.txt"))

    def test_which_quarter_a_results_notice_is_for(self):
        code, out, err = offline(["FFC", "OGDC", "DEMO", "--json"])
        self.assertEqual(code, 0, err)
        got = [(d["symbol"], d["notices"]["results"]["period"], d["notices"]["results"]["page_label"])
               for d in json.loads(out)]
        self.assertEqual(got, [("FFC", "Q3 2026", "Q1 2027"), ("OGDC", "the year 2026", None),
                               ("DEMO", "Q1 2027", None)])
        code, out, err = offline(["FFC"])
        self.assertIn("  It is for Q3 2026: the quarter the page calls Q1 2027.", out)

    def test_results_titles_seen_on_psx(self):
        end = facts.period_end
        self.assertEqual(end("FINANCIAL RESULTS FOR THE QUARTER ENDED SEPTEMBER 30, 2026"), "2026-09-30")
        self.assertEqual(end("Financial Results for the Year Ended 30-06-2026"), "2026-06-30")
        self.assertEqual(end("Financial Results for the Quarter Ended 30 Sep 2026"), "2026-09-30")
        self.assertEqual(end("Financial Results for the Quarter Ended 2026-09-30"), "2026-09-30")
        self.assertEqual(end("Financial Results for the Quarter Ended - March 31, 2026"), "2026-03-31")
        self.assertEqual(end("Financial Results for the Quarter Ended on September 30, 2026"), "2026-09-30")
        self.assertEqual(end("FINANCIAL RESULTS FOR THE PERIOD ENDED MARCH 31 2026"), "2026-03-31")
        self.assertEqual(end("Financial Results for the Quarter ended 30 September 2026"), "2026-09-30")
        self.assertEqual(end("Transmission of Quarterly Report for the Period Ended 31/03/2026"), "2026-03-31")
        self.assertEqual(end("Financial Results for the Quarter Ended September 30,2026"), "2026-09-30")   # no space
        self.assertEqual(end("Financial Results for the Quarter Ended 30 September,2026"), "2026-09-30")
        self.assertIsNone(end("Financial Results for the Quarter Ended September 302026"))
        self.assertIsNone(end("Credit of Second Interim Cash Dividend for the year ending Dec 2026"))

    def test_next_board_meeting_from_the_notice_text(self):
        code, out, err = offline(["DEMO", "--json"])
        n = json.loads(out)[0]["notices"]
        b = n["board"]
        self.assertEqual((b["doc_id"], b["date"], b["title"]), ("289306", "2026-10-20", "NOTICE OF BOARD MEETING"))
        self.assertEqual((b["meeting"], b["time"], b["ahead"], b["text_from"]),
                         ("2026-11-03", "11:00", True, "docs/289306.txt"))
        self.assertEqual(b["closed"], ["2026-10-27", "2026-11-03"])
        self.assertEqual((b["period_end"], b["consolidated"]), ("2026-09-30", True))
        self.assertEqual(b["quote"], "will be held on November 3, 2026 at 11:00 a.m.")
        self.assertEqual(n["met"]["doc_id"], "290005")    # "Board Meeting In Progress" is not a meeting to come
        code, out, err = offline(["DEMO"])
        self.assertIn("Next board meeting: Tue 2026-11-03 at 11:00 AM PKT (in 5 days).", out)
        self.assertIn("  Closed period: 2026-10-27 to 2026-11-03 (today is inside it).", out)

    def test_board_texts_seen_on_psx(self):
        f = facts.board_facts
        sys_ = f("Notice is hereby given that the 238th Board Meeting of 'Systems Limited' is scheduled to take\n"
                 "place at its Lahore Office, E-1, Sehjpal Near DHA Phase-VIII (Ex-Air Avenue), Lahore Cantt, "
                 "Pakistan\nin person or through video-link on October 23, 2026 at 05:00 p.m. to consider Third "
                 "Quater\nAccounts for the ended 30 September 2026 for declaration of any entitlement.\n\nIn "
                 "addition, the Company has declared the closed period from October 16, 2026 to\nOctober 23, 2026 "
                 "(both days inclusive).")
        self.assertEqual((sys_["meeting"], sys_["time"], sys_["period_end"]), ("2026-10-23", "17:00", "2026-09-30"))
        self.assertEqual(sys_["closed"], ["2026-10-16", "2026-10-23"])
        self.assertEqual(sys_["quote"], "is scheduled ... on October 23, 2026 at 05:00 p.m.")
        self.assertFalse(sys_["consolidated"])
        ogdc = f("the meeting of the Board of Directors will be held on October 30, 2026 at 1100 hours at Islamabad")
        self.assertEqual((ogdc["meeting"], ogdc["time"]), ("2026-10-30", "11:00"))
        later = f("The meeting will now be held on Friday, 30th October, 2026 at 3:00 p.m. instead.")
        self.assertEqual((later["meeting"], later["time"]), ("2026-10-30", "15:00"))
        none = f("The Board of Directors approved the un-consolidated accounts.")
        self.assertEqual((none["meeting"], none["closed"], none["consolidated"]), (None, None, False))

    def test_no_text_says_save_the_notice(self):
        code, out, err = offline(["OGDC"])
        self.assertEqual(code, 0, err)
        self.assertIn("Newest board notice: Thu 2026-10-29, Board Meeting Rescheduled (Change in Time).", out)
        # the site is off: Claude never opens a PSX link, the user saves the file or pastes a screenshot
        self.assertIn(ask("https://dps.psx.com.pk/download/document/290220.pdf",
                          os.path.join(MARKET, "docs", "290220.pdf")), out)
        self.assertNotIn("Open the notice", out)

    def test_a_scanned_pdf_is_named_a_scan(self):
        data = self.zlag_with("389950.pdf", b"%PDF-1.4 made up")
        self.swap_pdf_text(lambda path: "\f\f\f")          # what pdftotext gives for a scan
        code, out, err = offline(["ZLAG"], data)
        self.assertEqual(code, 0, err)
        self.assertIn("  docs/389950.pdf has no text: it is most likely a scan (many PSX notices are).", out)
        # the board met on 10-28 (Board Meeting In Progress): the notice is not needed, so no one is asked
        self.assertIn("  It is most likely for the meeting of Wed 2026-10-28, which is over.\n"
                      "  https://dps.psx.com.pk/download/document/389950.pdf\n", out)

    def test_how_to_get_a_notice_with_the_site_off_and_on(self):
        data = self.zlag_with("389950.pdf", b"%PDF-1.4 made up")
        self.swap_pdf_text(lambda path: "\f\f\f")
        p = page("ZLAG", data)
        p["announcements"]["Board Meetings"] = [r for r in p["announcements"]["Board Meetings"]
                                                if "Progress" not in r["title"]]
        link = "https://dps.psx.com.pk/download/document/389950.pdf"
        off = facts.notice_lines(facts.read_notices(p, data, TODAY, facts.analyse(p, TODAY)), TODAY)
        self.assertIn("  docs/389950.pdf has no text: it is most likely a scan (many PSX notices are).", off)
        self.assertIn(ask(link, os.path.join(data, "docs", "389950.pdf")), off)
        on = facts.notice_lines(facts.read_notices(p, data, TODAY, facts.analyse(p, TODAY), site=True), TODAY)
        self.assertIn("  Open the notice: " + link, on)      # with the site on, Claude may open it
        self.assertNotIn("Ask the user", "\n".join(on))

    def ogdc_with(self, docs):
        data = os.path.join(self.temp(), "data")
        os.makedirs(os.path.join(data, "company"))
        os.makedirs(os.path.join(data, "docs"))
        shutil.copy(os.path.join(MARKET, "company", "OGDC.html"), os.path.join(data, "company"))
        for name, body in docs.items():
            with open(os.path.join(data, "docs", name), "w", encoding="utf-8") as fh:
                fh.write(body)
        return data

    def test_an_older_notice_gives_the_date_and_a_newer_one_can_change_it(self):
        # OGDC: the newest notice (Rescheduled, Change in Time) has no text; the one before it gives the date
        data = self.ogdc_with({"289521.txt": OGDC_NOTICE})
        code, out, err = offline(["OGDC"], data)
        self.assertEqual(code, 0, err)
        self.assertIn("Next board meeting: Fri 2026-10-30 at 11:00 AM PKT (in 1 day).\n"
                      "  From the notice of Thu 2026-10-22 (NOTICE OF BOARD MEETING), text in docs/289521.txt.\n", out)
        self.assertIn("  Closed period: 2026-10-23 to 2026-10-30 (today is inside it).", out)
        self.assertIn("  A newer notice can change the date or time: Thu 2026-10-29, Board Meeting Rescheduled "
                      "(Change in Time).\n" + ask("https://dps.psx.com.pk/download/document/290220.pdf",
                                                  os.path.join(data, "docs", "290220.pdf")), out)
        code, out, err = offline(["OGDC", "--json"], data)
        b = json.loads(out)[0]["notices"]["board"]
        self.assertEqual((b["doc_id"], b["meeting"], b["ahead"], [x["doc_id"] for x in b["newer"]]),
                         ("289521", "2026-10-30", True, ["290220"]))

    def test_an_older_notice_for_a_meeting_that_is_over_does_not_count(self):
        data = self.ogdc_with({"283724.txt": "The meeting of the Board will be held on September 2, 2026 at 11:00 a.m."})
        code, out, err = offline(["OGDC"], data)
        self.assertEqual(code, 0, err)
        self.assertIn("Newest board notice: Thu 2026-10-29, Board Meeting Rescheduled (Change in Time).", out)
        self.assertNotIn("2026-09-02", out)

    def test_a_board_meeting_not_for_results(self):
        # HBL posts "Board Meeting Other Than Financial Results": no results warning, and it hides nothing
        data = self.temp()
        os.makedirs(os.path.join(data, "docs"))
        with open(os.path.join(data, "docs", "299999.txt"), "w", encoding="utf-8") as fh:
            fh.write("A meeting of the Board of Directors of the Bank will be held on October 30, 2026 at 11:00 a.m. "
                     "at Karachi to consider matters other than financial results.\n")
        p, day = page("HBL"), datetime.date(2026, 10, 30)
        p["announcements"]["Board Meetings"].insert(0, dict(OTHER_ROW))
        n = facts.read_notices(p, data, day, facts.analyse(p, day))
        self.assertEqual((n["board"]["doc_id"], n["other"]["doc_id"], n["other"]["meeting"]),
                         ("288736", "299999", "2026-10-30"))
        text = "\n".join(facts.notice_lines(n, day))
        self.assertIn("Newest board notice for results: Wed 2026-10-14, Board Meeting.", text)
        self.assertIn("Board meeting not for results: Fri 2026-10-30 at 11:00 AM PKT (today), from the notice of "
                      "Wed 2026-10-28 (Board Meeting Other Than Financial Results).", text)
        self.assertNotIn("Results can come out", text)
        self.assertIsNone(facts.metrics_row("HBL", facts.analyse(p, day), n)["next_board_meeting"])
        self.assertTrue(facts.NOT_RESULTS.search("Board Meeting for Agenda Other than Financial Results"))
        p["announcements"]["Board Meetings"][0]["date"] = "2026-10-01"     # older than the results notice
        self.assertIsNone(facts.read_notices(p, data, day, facts.analyse(p, day))["other"])

    def test_pdftotext_text_gives_the_meeting(self):
        data = self.zlag_with("389950.pdf", b"%PDF-1.4 made up")
        seen = []
        self.swap_pdf_text(lambda path: seen.append(os.path.basename(path)) or ZLAG_NOTICE)
        code, out, err = offline(["ZLAG"], data)
        self.assertEqual(code, 0, err)
        self.assertEqual(seen, ["389950.pdf"])
        self.assertIn("Newest board notice: the meeting was on Wed 2026-10-28 at 2:30 PM PKT (1 day ago).", out)
        self.assertIn("text in docs/389950.pdf.", out)
        self.assertIn("  Closed period: 2026-10-21 to 2026-10-28.", out)

    def test_no_pdftotext_or_an_image_only(self):
        data = self.zlag_with("389950.pdf", b"%PDF-1.4 made up")
        self.swap_pdf_text(lambda path: None)              # pdftotext is not installed
        code, out, err = offline(["ZLAG"], data)
        self.assertIn("  docs/389950.pdf was not read: pdftotext (poppler) is not installed, or it failed.", out)
        os.remove(os.path.join(data, "docs", "389950.pdf"))
        with open(os.path.join(data, "docs", "389950.gif"), "wb") as fh:
            fh.write(b"GIF89a made up")
        code, out, err = offline(["ZLAG"], data)
        self.assertIn("  docs/389950.gif is an image only: there is no text to read.", out)

    def test_the_board_is_meeting_today(self):
        data = self.zlag_with("389950.txt", ZLAG_NOTICE.encode("utf-8"))
        code, out, err = offline(["ZLAG"], data, now="2026-10-28 12:00")
        self.assertEqual(code, 0, err)
        self.assertIn("Results notice: Wed 2026-10-28 (today), Financial Results for the Quarter Ended 30 "
                      "September 2026", out)
        self.assertIn("The board is meeting today (Board Meeting In Progress): results can come out during the "
                      "session.", out)
        self.assertIn("Next board meeting: Wed 2026-10-28 at 2:30 PM PKT (today). Results can come out during "
                      "the session.", out)
        self.assertIn("(today is inside it)", out)


class Lagging(TempData):
    def test_says_when_the_page_lags_the_results_notice(self):
        code, out, err = offline(["ZLAG"], MINE)
        self.assertEqual(code, 0, err)
        self.assertIn("NOTE: The newest results notice (2026-10-28) is for the period ended 2026-09-30, but the "
                      "page's newest figures end 2026-06-30. The page has not caught up: read the notice.", out)
        self.assertIn("read the notice. " + ask("https://dps.psx.com.pk/download/document/390010.pdf",
                                                os.path.join(MINE, "docs", "390010.pdf")).strip(), out)
        data = self.copy(MINE)
        with open(os.path.join(data, "docs", "390010.txt"), "w", encoding="utf-8") as fh:
            fh.write("Financial results for the quarter ended September 30, 2026.\n")
        code, out, err = offline(["ZLAG"], data)
        self.assertIn("The page has not caught up: read the notice (docs/390010.txt).\n", out)

    def test_no_note_when_the_page_has_the_newest_results(self):
        for sym in ("DEMO", "FFC", "HBL", "OGDC", "SYS"):
            code, out, err = offline([sym])
            self.assertEqual(code, 0, err)
            self.assertNotIn("NOTE:", out, sym)


class Cli(TempData):
    def test_metrics_file(self):
        path = os.path.join(self.temp(), "psx", "metrics.json")
        code, out, err = offline(["DEMO", "FFC", "HBL", "--metrics", path])
        self.assertEqual(code, 0, err)
        self.assertIn("wrote %s (3 companies)" % path, out)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual((data["as_of"], data["units"]), ("2026-10-29", "Rs thousands, except EPS in rupees"))
        rows = dict((r["symbol"], r) for r in data["companies"])
        self.assertEqual(sorted(rows), ["DEMO", "FFC", "HBL"])
        d = rows["DEMO"]
        self.assertEqual((d["quarter"], d["quarter_end"], d["sales_000"], d["profit_000"], d["eps"]),
                         ("Q1 2027", "2026-09-30", 10210451, 838074, 3.35))
        self.assertEqual((d["sales_growth_yoy"], d["profit_growth_yoy"], d["eps_growth_yoy"], d["net_margin"]),
                         (13.0, 22.04, 21.82, 8.21))
        self.assertEqual((d["year"], d["year_eps"], d["gross_margin"], d["eps_ttm"], d["pe"]),
                         ("2026", 11.69, 24.85, 12.29, 8.27))
        self.assertEqual((d["results_notice"], d["next_board_meeting"], d["figures"]),
                         ("2026-10-27", "2026-11-03", "unconsolidated"))
        self.assertFalse(rows["FFC"]["label_ok"])
        self.assertEqual((rows["HBL"]["sales_label"], rows["HBL"]["gross_margin"]), ("mark-up earned", None))
        code, out, err = offline(["OGDC", "--metrics", path])        # merges by symbol
        self.assertEqual(code, 0, err)
        self.assertIn("(4 companies)", out)
        self.assertNotIn(chr(0x2014), out)

    def test_a_missing_page_with_the_site_off(self):
        code, out, err = run(["ZZZZ", "--data", MARKET, "--config", NO_CONFIG, "--now", NOW])
        self.assertEqual(code, 2)
        self.assertIn("facts: ZZZZ: The PSX site is off for this tool: save https://dps.psx.com.pk/company/ZZZZ as ",
                      err)
        self.assertIn('set "psx_site": true in psx/config.json if PSX has given you permission.', err)
        code, out, err = offline(["DEMO", "ZZZZ"])
        self.assertEqual(code, 1)                                     # one worked, one did not
        self.assertEqual(NETWORK, [])

    def test_the_site_only_when_the_config_says_so(self):
        tmp = self.temp()
        cfg, data = os.path.join(tmp, "config.json"), os.path.join(tmp, "data")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"psx_site": True}, fh)
        with open(os.path.join(MARKET, "company", "DEMO.html"), "rb") as fh:
            body = fh.read()
        urls = []
        facts.http_get = lambda url, timeout=30: urls.append(url) or body
        self.addCleanup(setattr, facts, "http_get", no_network)
        code, out, err = run(["DEMO", "--data", data, "--config", cfg])        # no --now: today, from the site
        self.assertEqual(code, 0, err)
        self.assertEqual(urls, ["https://dps.psx.com.pk/company/DEMO"])
        self.assertIn("Source: the PSX company page, from the site (about 5 minutes late).", out)
        self.assertFalse(os.path.exists(data))                        # nothing is saved
        code, out, err = run(["DEMO", "--data", data, "--config", cfg, "--offline"])
        self.assertEqual((code, len(urls)), (2, 1))                   # --offline never fetches
        self.assertIn("(--offline reads only the data folder)", err)
        code, out, err = run(["DEMO", "--data", data, "--config", cfg, "--now", NOW])
        self.assertEqual((code, len(urls)), (2, 1))                   # nor does a --now replay: the site has only today
        self.assertIn("(a --now replay reads only the data folder)", err)
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write('{"psx_site": true,')                           # bad JSON: the site stays off
        code, out, err = run(["DEMO", "--data", data, "--config", cfg])
        self.assertEqual((code, len(urls)), (2, 1))
        self.assertIn("is not valid JSON, so the PSX site stays off", err)

    def test_with_the_site_on_it_asks_the_site_first(self):
        cfg = os.path.join(self.temp(), "config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"psx_site": True}, fh)
        urls = []

        def down(url, timeout=30):
            urls.append(url)
            raise facts.urllib.error.URLError("no route to host")
        facts.http_get = down
        self.addCleanup(setattr, facts, "http_get", no_network)
        code, out, err = run(["DEMO", "--data", MARKET, "--config", cfg])
        self.assertEqual(code, 0, err)
        self.assertEqual(urls, ["https://dps.psx.com.pk/company/DEMO"])   # a saved page does not hide the site
        self.assertIn("(page time Thu 2026-10-29 3:52 PM PKT). The PSX site did not answer.", out)
        code, out, err = run(["DEMO", "--data", MARKET, "--config", cfg, "--now", NOW])
        self.assertEqual((code, len(urls)), (0, 1))                       # a --now replay never asks the site
        self.assertNotIn("did not answer", out)

    def test_json_output(self):
        code, out, err = offline(["DEMO", "--json"])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(len(data), 1)
        d = data[0]
        self.assertEqual((d["symbol"], d["source"], d["page_made"]), ("DEMO", "saved", "2026-10-29T10:52:00Z"))
        self.assertEqual([q["label"] for q in d["result"]["quarters"]],
                         ["Q1 2027", "Q4 2026", "Q3 2026", "Q2 2026", "Q1 2026"])
        self.assertEqual(d["result"]["as_of"], "2026-10-29T10:49:00Z")   # 3:49 PM PKT, stored in UTC
        self.assertEqual(d["notices"]["board"]["meeting"], "2026-11-03")
        self.assertEqual(d["changes"], facts.changes(d["result"]))

    def test_a_page_with_no_financials(self):
        code, out, err = offline(["ZRGT"], MINE)
        self.assertEqual(code, 2)
        self.assertIn("facts: ZRGT: the page has no financials", err)

    def test_help_and_a_bad_now(self):
        code, out, err = run([])
        self.assertEqual(code, 2)
        self.assertIn("usage", out)
        code, out, err = offline(["DEMO"], now="yesterday")
        self.assertEqual(code, 2)
        self.assertIn("--now wants a time", err)

    def test_plain_words_in_the_output(self):
        code, out, err = offline(["DEMO", "FFC", "HBL", "OGDC", "SYS"])
        self.assertEqual(code, 0, err)
        self.assertIsNone(re.search(r"(?i)\b(real[ -]time|live|guarantee\w*|buy|sell|cheap|expensive)\b", out))
        self.assertNotIn("$", out)
        self.assertNotIn(chr(0x2014), out)


class Example(unittest.TestCase):
    def test_the_skill_md_example_is_the_real_output(self):
        with open(os.path.join(ROOT, "psx-analyst", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"\nIt prints:\n\n```text\n(.*?)```\n", text, re.S)
        self.assertIsNotNone(m, "SKILL.md has no example output block")
        code, out, err = offline(["DEMO"])
        self.assertEqual(code, 0, err)
        self.assertEqual(m.group(1), out.rstrip("\n") + "\n")


if __name__ == "__main__":
    unittest.main()
