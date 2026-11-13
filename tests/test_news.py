import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("psx-news", "news")
MARKET = os.path.join(FIX, "psx-market")        # the made-up sample market (EXAMPLE.md)
FEED_DIR = os.path.join(MARKET, "news")
UNIVERSE = os.path.join(FIX, "psx-news", "universe.txt")
NOW = "2026-10-29 17:00"                         # Karachi time: Thursday, after the close
SOURCES = dict((n, s) for n, s, _ in news.FEEDS)
EM_DASH = chr(0x2014)


def raw(path):
    with open(path, "rb") as fh:
        return fh.read()


def feed(name):
    return news.parse_feed(raw(os.path.join(FEED_DIR, name + ".xml")), {"name": name, "source": SOURCES[name]})


def all_feeds():
    return [it for n in ("brecorder-markets", "dawn-business", "tribune-business", "propakistani-business")
            for it in feed(n)]


def page(sym, folder=MARKET):
    with open(os.path.join(folder, "company", sym + ".html"), encoding="utf-8") as fh:
        return news.parse_company(fh.read())


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


def headline(title, summary="", when=None):
    return {"id": "rss:" + title, "source": "Test", "kind": "headline", "feed": "test", "ticker": None,
            "title": title, "summary": summary, "link": "", "time": when,
            "date": news.to_pkt(when).date().isoformat() if when else None}


def by_title(items):
    return dict((i["title"], i) for i in items)


class Feeds(unittest.TestCase):
    def test_business_recorder_rss(self):
        items = feed("brecorder-markets")
        self.assertEqual(len(items), 7)
        first = items[0]
        self.assertEqual(first["title"], "KSE-100 gains 1,265 points as banks and cement lead")
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 29, 11, 42, 10))   # 16:42:10 +0500
        self.assertEqual(first["date"], "2026-10-29")
        self.assertEqual(first["id"], "rss:https://www.brecorder.com/news/40447120")
        self.assertTrue(first["link"].startswith("https://www.brecorder.com/news/40447120/"))
        self.assertEqual(first["source"], "Business Recorder")
        self.assertIn("DEMO rose 3.25 percent", first["summary"])
        self.assertNotIn("<p>", first["summary"])

    def test_tribune_two_digit_year_and_propakistani_utc(self):
        trib = feed("tribune-business")[0]
        self.assertEqual(trib["title"], "Stocks extend gains as KSE-100 crosses 157,000")
        self.assertEqual(trib["time"], datetime.datetime(2026, 10, 29, 12, 20, 11))  # "Thu, 29 Oct 26 17:20:11 +0500"
        self.assertEqual(trib["id"], "rss:https://tribune.com.pk/?p=2637702")
        self.assertEqual(trib["summary"], "The benchmark KSE-100 index crossed the 157,000 mark on Thursday.")
        pro = feed("propakistani-business")[0]
        self.assertEqual(pro["time"], datetime.datetime(2026, 10, 29, 13, 47, 34))   # +0000 is UTC
        self.assertEqual(news.to_pkt(pro["time"]).strftime("%H:%M"), "18:47")       # Karachi time
        dawn = by_title(feed("dawn-business"))["Demo Industries wins Rs1.2bn packaging order"]
        self.assertEqual(news.to_pkt(dawn["time"]), datetime.datetime(2026, 10, 28, 20, 14, 33))

    def test_times(self):
        self.assertEqual(news.parse_time("Thu, 29 Oct 26 17:20:11 +0500"), datetime.datetime(2026, 10, 29, 12, 20, 11))
        self.assertEqual(news.parse_time("Thu, 29 Oct 2026 04:22:26 GMT").hour, 4)
        self.assertEqual(news.parse_time("2026-10-29T16:42:10+05:00"), datetime.datetime(2026, 10, 29, 11, 42, 10))
        self.assertEqual(news.parse_time("2026-10-29T11:42:10Z"), datetime.datetime(2026, 10, 29, 11, 42, 10))
        self.assertIsNone(news.parse_time("soon"))
        self.assertIsNone(news.parse_time(""))

    def test_the_sample_feeds_hold_25_items(self):
        items = all_feeds()
        self.assertEqual(len(items), 25)                 # EXAMPLE.md section 10: 4 feeds, 25 items
        self.assertTrue(all(i["time"] is not None for i in items))
        self.assertTrue(all(i["id"].startswith("rss:https://") for i in items))

    def test_atom_and_odd_feeds(self):
        atom = (b'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
                b'<title>Some Desk</title><entry><title>Hubco signs a deal</title><id>tag:x,2026:1</id>'
                b'<link rel="alternate" href="https://example.com/a"/><updated>2026-10-29T09:00:00+05:00</updated>'
                b'<summary>Hubco said it signed a contract.</summary></entry></feed>')
        items = news.parse_feed(atom, {"name": "x", "source": None})
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it["id"], it["link"], it["source"]), ("rss:tag:x,2026:1", "https://example.com/a", "Some Desk"))
        self.assertEqual(it["time"], datetime.datetime(2026, 10, 29, 4, 0))
        spaced = b"\xef\xbb\xbf  \n<rss><channel><item><title>A</title><link>https://e.com/1</link></item></channel></rss>"
        self.assertEqual(news.parse_feed(spaced, {"name": "y", "source": "Y"})[0]["id"], "rss:https://e.com/1")
        with self.assertRaises(news.NewsError):
            news.parse_feed(b"<html><body>not a feed", {"name": "z", "source": "Z"})

    def test_feed_list(self):
        self.assertEqual([f["name"] for f in news.feed_list()], [n for n, _, _ in news.FEEDS])
        self.assertEqual(len(news.FEEDS), 5)
        saved = news.feed_list(None, MARKET, offline=True)   # --offline: every saved feed in <data>/news/
        self.assertEqual([f["name"] for f in saved],
                         ["brecorder-markets", "dawn-business", "propakistani-business", "tribune-business"])
        mine = news.feed_list("dawn-business, https://www.arabnews.pk/rss.xml")
        self.assertEqual([(f["name"], f["source"]) for f in mine],
                         [("dawn-business", "Dawn"), ("arabnews-pk-rss-xml", "arabnews.pk")])
        cfg = news.feed_list([{"url": "https://example.com/feed", "name": "ex", "source": "Example"}])
        self.assertEqual(cfg, [{"name": "ex", "source": "Example", "url": "https://example.com/feed"}])
        self.assertEqual(news.feed_list([]), [])
        with self.assertRaises(news.NewsError):
            news.feed_list("google-news")


class Notices(unittest.TestCase):
    def test_company_page_notices(self):
        items = news.notices_from_page(page("DEMO"), "DEMO")
        self.assertEqual(len(items), 15)                  # 3 tabs x 5 rows
        self.assertEqual([i["tab"] for i in items[::5]], ["Financial Results", "Board Meetings", "Others"])
        mat = [i for i in items if i["id"] == "psx:290111"][0]
        self.assertEqual((mat["title"], mat["date"], mat["time"]), ("Material Information", "2026-10-28", None))
        self.assertEqual(mat["link"], "https://dps.psx.com.pk/download/document/290111.pdf")
        self.assertEqual((mat["kind"], mat["source"], mat["ticker"]), ("notice", "PSX notice", "DEMO"))
        gif = [i for i in items if i["id"] == "psx:289612"][0]   # an image only: no PDF
        self.assertEqual(gif["link"], "https://dps.psx.com.pk/download/image/289612-1.gif")

    def test_notice_tags_and_marks(self):
        items = news.notices_from_page(page("DEMO"), "DEMO") + news.notices_from_page(page("HBL"), "HBL")
        t = by_title(news.tag(items, {"DEMO": None, "HBL": None}))
        mat = t["Material Information"]
        self.assertEqual((mat["tags"], mat["important"], mat["why"]), (["material"], True, ["material information"]))
        res = t["FINANCIAL RESULTS FOR THE QUARTER ENDED SEPTEMBER 30, 2026"]
        self.assertEqual((res["tags"], res["important"]), (["results"], True))
        self.assertEqual((t["Board Meeting In Progress"]["tags"], t["Board Meeting In Progress"]["important"]),
                         (["board"], False))
        ins = [i for i in items if i["id"] == "psx:290210"][0]
        self.assertEqual((ins["tags"], ins["important"]), (["insider"], False))
        self.assertEqual(t["Transmission of Annual Report for the Year Ended June 30, 2026"]["tags"], ["results"])
        self.assertFalse(t["Transmission of Annual Report for the Year Ended June 30, 2026"]["important"])
        self.assertEqual(t["Change of Company Secretary"]["tags"], ["people"])
        self.assertEqual(t["NOTICE OF BOARD MEETING"]["tags"], ["board"])
        self.assertEqual(t["BOOK CLOSURE FOR INTERIM CASH DIVIDEND"]["tags"], ["dividend"])
        self.assertEqual(t["Credit Rating"]["tags"], ["analyst"])
        self.assertEqual(t["Change in Directorship"]["tags"], ["people"])

    def test_notices_never_merge_on_the_title(self):
        a = news.notices_from_page(page("DEMO"), "DEMO")
        b = news.notices_from_page(page("OGDC"), "OGDC")
        items = news.dedupe(news.tag(a + b, {"DEMO": None, "OGDC": None}))
        self.assertEqual(len(items), 30)       # both pages have a "Material Information"
        twice = news.dedupe(news.tag(news.notices_from_page(page("DEMO"), "DEMO") +
                                     news.notices_from_page(page("DEMO"), "OGDC"), {"DEMO": None, "OGDC": None}))
        self.assertEqual(len(twice), 15)       # the same PSX doc id is one notice
        self.assertEqual(twice[0]["touches"], ["DEMO", "OGDC"])


class Tagging(unittest.TestCase):
    def setUp(self):
        names = news.read_universe(UNIVERSE)
        self.assertEqual(names["DEMO"], "Demo Industries Limited")
        self.watch = dict((s, names[s]) for s in ("DEMO", "OGDC", "HUBC", "LUCK", "MEBL"))

    def test_touches_by_symbol_and_name(self):
        t = by_title(news.tag(all_feeds(), self.watch))
        self.assertEqual(t["KSE-100 gains 1,265 points as banks and cement lead"]["touches"], ["DEMO", "HUBC", "OGDC"])
        self.assertEqual(t["Demo Industries wins Rs1.2bn packaging order"]["touches"], ["DEMO"])   # the name
        self.assertEqual(t["Hubco to hold board meeting on quarterly results"]["touches"], ["HUBC"])
        self.assertEqual(t["Hubco Board to Meet on Quarterly Accounts"]["touches"], ["HUBC"])       # an alias
        self.assertEqual(t["Lucky Cement says despatches rose in October"]["touches"], ["LUCK"])
        self.assertEqual(t["Meezan Bank opens new branches in Sindh"]["touches"], ["MEBL"])
        self.assertEqual(t["OGDC says new well starts production"]["touches"], ["OGDC"])
        self.assertEqual(t["SBP keeps policy rate unchanged"]["touches"], [])
        self.assertEqual(t["Cement Sales Up 8% in October So Far"]["touches"], [])
        # EXAMPLE.md section 10, with the PSX tags of this tool
        self.assertEqual(t["KSE-100 gains 1,265 points as banks and cement lead"]["tags"], ["results", "macro"])
        self.assertEqual(t["Demo Industries wins Rs1.2bn packaging order"]["tags"], ["deal"])   # "contract"

    def test_a_name_counts_in_the_title_and_lead_a_symbol_anywhere(self):
        late = "Shares were mixed. " * 30            # pushes the rest past the first lines
        item = headline("Market wrap", summary=news.lead(late + "Lucky Cement rose."))
        item["_text"] = "Market wrap " + late + "Lucky Cement rose, and OGDC fell."
        t = news.tag([item], {"LUCK": "Lucky Cement Limited", "OGDC": None})[0]
        self.assertEqual(t["touches"], ["OGDC"])      # the symbol deep in the story counts; the name does not
        item["summary"] = "Lucky Cement rose."
        self.assertEqual(news.tag([item], {"LUCK": "Lucky Cement Limited"})[0]["touches"], ["LUCK"])

    def test_the_ahl_trap(self):
        watch = dict(self.watch, AHL="Arif Habib Limited")
        t = by_title(news.tag(all_feeds(), watch))
        trap = t["Banks may lead earnings growth, says brokerage"]
        self.assertIn('AHL said in a note', trap["summary"])
        self.assertEqual(trap["touches"], [])           # AHL is a broker quoted as the source
        self.assertEqual(trap["tags"], ["results", "analyst"])

    def test_names_and_capitals(self):
        self.assertEqual(news.name_forms("Demo Industries Limited"), ["Demo Industries"])
        self.assertEqual(news.name_forms("Systems Limited"), ["Systems Limited"])
        self.assertEqual(news.name_forms("The Hub Power Company Limited"), ["Hub Power Company"])
        self.assertEqual(news.name_forms("Cnergyico PK  Limited"), ["Cnergyico PK"])
        self.assertEqual(news.name_forms("National Bank of Pakistan"), ["National Bank of Pakistan"])
        self.assertEqual(news.name_forms("Interloop Limited"), ["Interloop Limited"])
        self.assertEqual(news.name_forms("Engro"), [])
        pats = news.mention_patterns({"DEMO": "Demo Industries Limited", "SYS": "Systems Limited",
                                      "OGDC": "Oil & Gas Development Company Limited", "786": None,
                                      "PPL": "Pakistan Petroleum Limited", "FFC": None, "NBP": None})
        hits = lambda text: sorted(t for t, (sym, named) in pats.items()   # noqa: E731
                                   if (sym and sym.search(text)) or (named and news.name_found(named, text)))
        self.assertEqual(hits("the Pakistan Petroleum Dealers Association said"), [])   # a longer name
        self.assertEqual(hits("Hatton National Bank and Saudi National Bank fell"), [])
        self.assertEqual(hits("In Karachi, National Bank and The National Bank rose"), ["NBP"])
        self.assertEqual(hits("Pakistan Petroleum Limited posted"), ["PPL"])
        self.assertEqual(hits("Pakistan Petroleum earns Rs99bn"), ["PPL"])
        self.assertEqual(hits("Fauji Fertiliser and National Bank led the gains"), ["FFC", "NBP"])
        self.assertEqual(hits(news.clean("Fauji Ferti\xadliser rose")), ["FFC"])          # a soft hyphen
        self.assertEqual(hits("Demo day for start-ups in Lahore"), [])
        self.assertEqual(hits("DEMO rose 3 percent; DEMO's volume doubled"), ["DEMO"])
        self.assertEqual(hits("DEMO INDUSTRIES POSTS PROFIT"), ["DEMO"])
        self.assertEqual(hits("a demo industries fair"), [])
        self.assertEqual(hits("KDEMO and DEMOX are other words"), [])
        self.assertEqual(hits("payment systems rose"), [])
        self.assertEqual(hits("Systems Limited said"), ["SYS"])
        self.assertEqual(hits("SYS gained"), ["SYS"])
        self.assertEqual(hits("Oil and Gas Development Company said"), ["OGDC"])
        self.assertEqual(hits("Gold rose by 786 rupees"), [])

    def test_dedupe_merges_the_same_story(self):
        a = feed("brecorder-markets")
        b = news.parse_feed(raw(os.path.join(FEED_DIR, "brecorder-markets.xml")),
                            {"name": "brecorder-business-finance", "source": "Business Recorder"})
        items = news.dedupe(news.tag(a + b, self.watch))
        self.assertEqual(len(items), 7)            # Business Recorder posts a story in both feeds
        same = [headline("Rupee gains", when=datetime.datetime(2026, 10, 28, 19, 30)),   # 00:30 PKT on 10-29
                headline("Rupee  gains!", when=datetime.datetime(2026, 10, 29, 3, 0))]   # 08:00 PKT on 10-29
        same[1]["id"] = "rss:other"
        self.assertEqual(len(news.dedupe(news.tag(same, {}))), 1)   # the same Karachi day
        same[1]["time"] = datetime.datetime(2026, 10, 29, 19, 30)
        same[1]["date"] = "2026-10-30"
        self.assertEqual(len(news.dedupe(news.tag(same, {}))), 2)

    def test_psx_tags_and_marks(self):
        items = news.tag([
            headline("Trading halted at PSX after SECP order; rupee falls before SBP policy rate decision"),
            headline("ABC announces 25% right shares and 10% bonus"),
            headline("XYZ and ABC agree on merger"),
            headline("KSE-100 halts four-day losing streak"),
            headline("Profit-taking drags KSE-100 lower"),
            headline("LUCK posts Rs5bn profit in first quarter"),
            headline("PSX moves XYZ to the defaulters segment over non-compliance"),
            headline("IMF approves the next tranche; inflation eases to 5pc"),
        ], {})
        t = by_title(items)
        halt = t["Trading halted at PSX after SECP order; rupee falls before SBP policy rate decision"]
        self.assertEqual(halt["tags"], ["regulatory", "halt", "macro"])
        self.assertEqual(halt["why"], ["market halt"])
        rights = t["ABC announces 25% right shares and 10% bonus"]
        self.assertEqual((rights["tags"], rights["why"]), (["rights", "bonus"], ["rights issue"]))
        merger = t["XYZ and ABC agree on merger"]
        self.assertEqual((merger["tags"], merger["why"]), (["deal"], ["merger or takeover"]))
        self.assertEqual((t["KSE-100 halts four-day losing streak"]["tags"],
                          t["KSE-100 halts four-day losing streak"]["important"]), (["macro"], False))
        self.assertEqual(t["Profit-taking drags KSE-100 lower"]["tags"], ["macro"])
        self.assertEqual(t["LUCK posts Rs5bn profit in first quarter"]["tags"], ["results"])
        nc = t["PSX moves XYZ to the defaulters segment over non-compliance"]
        self.assertEqual((nc["tags"], nc["why"]), (["risk"], ["suspension or non-compliance"]))
        self.assertEqual(t["IMF approves the next tranche; inflation eases to 5pc"]["tags"], ["macro"])
        self.assertEqual([label for label, _ in news.KEYWORDS],
                         ["results", "dividend", "board", "material", "insider", "rights", "bonus", "deal",
                          "capital", "people", "legal", "regulatory", "risk", "halt", "macro", "analyst"])

    def test_other_than_results_and_board_meetings_get_no_mark(self):
        # real PSX notice titles (made-up doc ids): "other than" is not results and not a rights issue
        def notices(tab, *titles):
            rows = [{"date": "2026-10-29", "title": t, "doc_id": str(900 + i)} for i, t in enumerate(titles)]
            return news.notices_from_page({"announcements": {tab: rows}}, "DEMO")
        items = news.tag(notices("Board Meetings", "Board meeting other than Financial result",
                                 "28th Board Meeting (Other than Financial Results)",
                                 "236th Emergent Board Meeting Other Than Financial Results",
                                 "BOARD MEETING FOR THE ANNOUNCEMENT OF THE FINANCIAL RESULTS FOR THE NINE MONTHS")
                         + notices("Others", "Application for further Issue share by way of Other than Right Offer",
                                   "Board Meeting Other Than Financial Results")
                         + notices("Financial Results", "Financial Result for the Quarter ended September 30, 2026"),
                         {"DEMO": None})
        got = [(i["tags"], i["important"]) for i in items]
        self.assertEqual(got, [(["board"], False), (["board"], False), (["board"], False),
                               (["results", "board"], False),     # a meeting still to come: tagged, no "!"
                               ([], False), (["board"], False),
                               (["results"], True)])
        right = news.tag([headline("ABC rights issue approved; XYZ to issue shares other than rights")], {})[0]
        self.assertEqual((right["tags"], right["why"]), (["rights"], ["rights issue"]))

    def test_short_names_count_like_a_symbol(self):
        watch = {"OGDC": "Oil & Gas Development Company Limited", "PTC": None, "SNGP": None, "SSGC": None,
                 "MARI": "Mari Energies Limited"}
        late = "Shares were mixed. " * 30
        items = news.tag([headline("OGDCL makes oil, gas discovery in Sindh"),
                          headline("OGDCL, MARI-backed well starts flowing"),
                          headline("Market wrap", summary="Shares were mixed."),
                          headline("PTCL and SNGPL rose; SSGCL's profit fell")], watch)
        items[2]["_text"] = "Market wrap " + late + "OGDCL fell."   # deep in the story: still counts
        items = news.tag(items, watch)
        self.assertEqual([i["touches"] for i in items], [["OGDC"], ["MARI", "OGDC"], ["OGDC"], ["PTC", "SNGP", "SSGC"]])
        self.assertEqual(news.tag([headline("OGDCLX and XOGDCL are other words")], watch)[0]["touches"], [])

    def test_psx_the_exchange_is_not_the_psx_stock(self):
        watch = {"PSX": "Pakistan Stock Exchange Limited", "LUCK": "Lucky Cement Limited", "FFC": None}
        items = news.tag([headline("PSX extends rally to fourth session"),
                          headline("Lucky Cement approves expansion",
                                   summary="It said so in a notice to the Pakistan Stock Exchange (PSX)."),
                          headline("PSX Limited posts higher profit")], watch)
        self.assertEqual([i["touches"] for i in items], [[], ["LUCK"], ["PSX"]])
        ffc = news.notices_from_page({"announcements": {"Others": [{
            "date": "2026-10-29", "title": "Disclosure of Interest u/c 5.6.1.(d) of PSX Regulations", "doc_id": "7"}]}},
            "FFC")
        self.assertEqual(news.tag(ffc, watch)[0]["touches"], ["FFC"])
        own = news.notices_from_page({"announcements": {"Financial Results": [{
            "date": "2026-10-29", "title": "Financial Results for the Quarter", "doc_id": "8"}]}}, "PSX")
        self.assertEqual(news.tag(own, watch)[0]["touches"], ["PSX"])   # its own notices still count

    def test_brokers_as_the_source_and_longer_names(self):
        watch = {"AHL": "Arif Habib Limited", "HBL": "Habib Bank Limited", "UBL": "United Bank Limited"}
        t = lambda title, summary="": news.tag([headline(title, summary)], watch)[0]["touches"]   # noqa: E731
        self.assertEqual(t("Index gains", "Arif Habib Limited said the index gained 1,200 points."), [])
        self.assertEqual(t("Index gains", "Arif Habib Ltd. said the index gained."), [])
        self.assertEqual(t("Index gains", "Arif Habib says banks will lead."), [])
        self.assertEqual(t("Index gains", "Banks will lead, according to Arif Habib."), [])
        self.assertEqual(t("Arif Habib posts profit for the quarter"), ["AHL"])   # the broker as the subject
        self.assertEqual(t("Habib Bank AG Zurich opens new branch in Lahore"), [])
        self.assertEqual(t("HABIB BANK AG ZURICH OPENS A BRANCH"), [])
        self.assertEqual(t("Habib Bank posts profit"), ["HBL"])
        self.assertEqual(t("United Bank PLC results"), [])


class Base(unittest.TestCase):
    """Runs the tool in a temp folder. The network is swapped out: a fetch fails the test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.none = os.path.join(self.tmp.name, "none.json")
        self.calls, self.serve = [], {}
        self._get, self._sleep, self._clock = news.http_get, news.SLEEP, news.utc_now
        news.http_get = self.no_network
        news.SLEEP = lambda s: None
        self.base = ["--offline", "--data", MARKET, "--universe", UNIVERSE, "--state", self.state, "--now", NOW,
                     "--config", self.none, "--portfolio", self.none, "--watchlist", self.none, "--plan", self.none]

    def tearDown(self):
        news.http_get, news.SLEEP, news.utc_now = self._get, self._sleep, self._clock
        self.tmp.cleanup()

    def live(self):
        """A live run (no --offline, no --now) with the real clock set to NOW: --now is a replay and never fetches."""
        news.utc_now = lambda: news.parse_now(NOW)
        return dict(offline=None, now=None)

    def no_network(self, url, timeout=30):
        raise AssertionError("the network was used: %s" % url)

    def fake_get(self, url, timeout=30):
        self.calls.append(url)
        if url in self.serve:
            return self.serve[url]
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    def args(self, *extra, **swap):
        out = list(self.base)
        for k, v in swap.items():
            flag = "--" + k.replace("_", "-")
            if v is None:
                i = out.index(flag)
                del out[i:i + (1 if flag == "--offline" else 2)]
            else:
                out[out.index(flag) + 1] = v
        return list(extra) + out

    def data_copy(self, *companies, **kw):
        """A temp data folder with the sample feeds and daily files and only these company pages."""
        d = os.path.join(self.tmp.name, kw.get("name", "data"))
        shutil.copytree(os.path.join(MARKET, "news"), os.path.join(d, "news"))
        shutil.copytree(os.path.join(MARKET, "mkt_summary"), os.path.join(d, "mkt_summary"))
        os.makedirs(os.path.join(d, "company"))
        for s in companies:
            shutil.copy(os.path.join(MARKET, "company", s + ".html"), os.path.join(d, "company", s + ".html"))
        return d

    def write(self, name, obj):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(obj if isinstance(obj, str) else json.dumps(obj))
        return path


class Check(Base):
    def test_the_example_demo(self):
        # EXAMPLE.md section 10: DEMO on Thu 2026-10-29, checked at 5:00 PM after the close
        code, out, err = run(self.args("--tickers", "DEMO"))
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], "DEMO      101.60 +3.25% vs the last close 98.40 at 3:49 PM PKT  MOVING")
        self.assertIn("PSX notices (PSX gives a date, not a time):", lines)
        notices = [ln for ln in lines if ln[2:4] == "20" and "PKT" not in ln]
        self.assertEqual(len(notices), 4)
        self.assertTrue(notices[0].startswith("  2026-10-29  DEMO    Disclosure of Interest by a Director CEO"))
        self.assertEqual(notices[1:], ["! 2026-10-28  DEMO    Material Information",
                                       "! 2026-10-27  DEMO    FINANCIAL RESULTS FOR THE QUARTER ENDED SEPTEMBER 30, 2026",
                                       "  2026-10-27  DEMO    Board Meeting In Progress"])
        self.assertIn("      tags: material  https://dps.psx.com.pk/download/document/290111.pdf", lines)
        heads = [ln for ln in lines if ln[2:4] == "20" and " PKT  " in ln]
        self.assertEqual(heads, [
            "  2026-10-29 16:42 PKT  DEMO        Business Recorder  KSE-100 gains 1,265 points as banks and cement lead",
            "  2026-10-28 20:14 PKT  DEMO        Dawn               Demo Industries wins Rs1.2bn packaging order"])
        self.assertIn("      tags: results, macro  https://www.brecorder.com/news/40447120/"
                      "kse100-gains-1265-points-as-banks-and-cement-lead", lines)
        self.assertNotIn("Banks may lead earnings growth", out)      # the AHL trap matches no stock
        self.assertNotIn("note:", out)
        # the site is off: Claude never fetches a dps.psx.com.pk link, the user opens it (D17)
        ask = ("To read a notice: ask the user to open its link in their browser and save it as %s (an image as "
               "<id>.gif), or paste a screenshot. Do not fetch it yourself." % os.path.join(MARKET, "docs", "<id>.pdf"))
        self.assertEqual(lines[lines.index("Headlines:") - 2], ask)   # under the notices
        self.assertEqual(lines[-1], "Checked 2026-10-29 17:00 PKT. Page prices are about 5 minutes late. Headlines "
                                    "are leads, not facts: read the story or the notice before acting. Not financial advice.")
        self.assertNotIn("open the story or the notice", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn(EM_DASH, out)
        code, out, err = run(self.args("--tickers", "DEMO", "--json", "--all", "--dry-run"))
        self.assertEqual(json.loads(out)["notes"], [ask])
        code, out, err = run(self.args("--tickers", "DEMO", "--no-notices", "--dry-run"))
        self.assertNotIn("To read a notice", out)                    # no notices shown: no line

    def test_the_skill_md_example_is_the_real_output(self):
        code, out, err = run(self.args("--tickers", "DEMO"))
        with open(os.path.join(os.path.dirname(FIX), "..", "psx-news", "SKILL.md"), encoding="utf-8") as fh:
            doc = fh.read()
        out = out.replace(MARKET + os.sep, "psx/data/")             # the example uses the default --data
        self.assertIn("```\n" + out + "```\n", doc)

    def test_new_items_once_newest_first(self):
        code, out, err = run(self.args("--tickers", "DEMO", "OGDC", "HUBC", "LUCK", "MEBL", "--json"))
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["checked"], "2026-10-29T12:00:00Z")   # --now is Karachi time
        heads = [i for i in data["items"] if i["kind"] == "headline"]
        notes = [i for i in data["items"] if i["kind"] == "notice"]
        self.assertEqual([i["time"] for i in heads], sorted((i["time"] for i in heads), reverse=True))
        self.assertEqual([i["date"] for i in notes], sorted((i["date"] for i in notes), reverse=True))
        self.assertEqual((len(heads), len(notes)), (10, 6))
        self.assertTrue(all(i["new"] for i in data["items"]))
        self.assertTrue(all("_text" not in i for i in data["items"]))
        code, out, _ = run(self.args("--tickers", "DEMO", "OGDC", "HUBC", "LUCK", "MEBL"))
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)
        code, out, _ = run(self.args("--tickers", "DEMO", "--all", "--json"))
        self.assertEqual(code, 1)                                   # shown again, but not new
        self.assertEqual(len(json.loads(out)["items"]), 6)

    def test_tickers_from_watchlist_and_plan(self):
        wl = self.write("watchlist.json", {"created_by": "psx-scanner", "hits": [
            {"symbol": "DEMO", "name": "Demo Industries Limited", "sector": "0818", "setups": ["breakout_20d"]}]})
        plan = self.write("plan.json", {"created_by": "psx-exit", "symbol": "OGDC"})
        self.assertEqual(sorted(news.watch_from_files(self.none, wl, plan)), ["DEMO", "OGDC"])
        code, out, err = run(self.args("--json", "--dry-run", watchlist=wl, plan=plan))
        self.assertEqual(code, 0, err)
        tickers = set(t for i in json.loads(out)["items"] for t in i["touches"])
        self.assertEqual(tickers, {"DEMO", "OGDC"})
        self.assertEqual([m["ticker"] for m in json.loads(out)["moves"]], ["DEMO", "OGDC"])
        self.assertFalse(os.path.exists(self.state))

    def test_no_tickers_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)

    def test_market_news_without_tickers(self):
        code, out, err = run(self.args("--market", "--hours", "8", now="2026-10-29 14:00"))
        self.assertEqual(code, 0, err)
        self.assertIn("market      Business Recorder  Rupee steady against dollar in interbank market", out)
        self.assertIn("IMF mission to review progress next month", out)
        self.assertIn("SBP Reserves Rise by", out)
        self.assertNotIn("Banks may lead earnings growth", out)    # 13:20, tagged results and analyst, not macro
        self.assertNotIn("Hubco", out)                              # names a stock you do not watch
        self.assertNotIn("PSX notices", out)
        self.assertNotIn("vs the last close", out)

    def test_market_news_is_pakistani_news(self):
        d = self.data_copy()
        stories = [("Indian rupee ends flat after hitting two-week low", "The Indian rupee ended flat on Thursday."),
                   ("Gold slips as markets await US inflation data", "Gold prices slipped on Thursday."),
                   ("Wall Street slides on rate fears", "US stocks fell as interest rates rose."),
                   ("Rupee extends gain against US dollar", "The Pakistani rupee gained in the interbank market."),
                   ("Diesel price nears Rs400 as govt adds over a rupee", "Fuel prices rose for the next 15 days.")]
        rss = "".join("<item><title>%s</title><link>https://example.com/%d</link><description>%s</description>"
                      "<pubDate>Thu, 29 Oct 2026 08:0%d:00 +0000</pubDate></item>" % (t, i, s, i)
                      for i, (t, s) in enumerate(stories))
        with open(os.path.join(d, "news", "wire.xml"), "w", encoding="utf-8") as fh:
            fh.write("<rss><channel><title>Wire</title>%s</channel></rss>" % rss)
        code, out, err = run(self.args("--market", "--hours", "8", "--dry-run", data=d, now="2026-10-29 14:00"))
        self.assertEqual(code, 0, err)
        for title, _ in stories[:3]:
            self.assertNotIn(title, out)                 # foreign wire stories: not market news here
        self.assertIn("Rupee extends gain against US dollar", out)
        self.assertIn("Diesel price nears Rs400", out)
        self.assertIn("Rupee steady against dollar in interbank market", out)   # "the rupee" is Pakistan's
        self.assertTrue(news.local_news(headline("KSE-100 slips")))
        self.assertFalse(news.local_news(headline("China's yuan hits 4-year high", "after the Fed decision")))

    def test_no_trade_since_an_older_day(self):
        d = self.data_copy("DEMO")
        path = os.path.join(d, "company", "DEMO.html")
        with open(path, encoding="utf-8") as fh:
            text = fh.read().replace("As of Thu, Oct 29, 2026 3:49 PM", "As of Wed, Oct 28, 2026 3:49 PM")
        with open(path, "w", encoding="utf-8") as fh:   # an old page: its LDCP is still the one of that day
            fh.write(text)
        code, out, err = run(self.args("--tickers", "DEMO", "--no-feeds", "--dry-run", data=d))
        self.assertIn("DEMO      101.60 +3.25% vs the last close 98.40 at 3:49 PM PKT on Wed 2026-10-28  MOVING", out)
        self.assertIn("Page prices were about 5 minutes late when each page was saved.", out)
        with open(path, "w", encoding="utf-8") as fh:   # no trade today: the LDCP moved on to the newer close
            fh.write(text.replace('LDCP</div><div class="stats_value">98.40<',
                                  'LDCP</div><div class="stats_value">101.60<'))
        code, out, err = run(self.args("--tickers", "DEMO", "--no-feeds", "--dry-run", data=d))
        self.assertIn("DEMO      101.60 +0.00% vs the last close 101.60, no trade since 3:49 PM PKT on Wed 2026-10-28\n",
                      out)
        self.assertIn("Page prices were about 5 minutes late when each page was saved.", out)
        self.assertNotIn("Page prices are about", out)
        code, out, err = run(self.args("--tickers", "DEMO", "--no-feeds", "--json", "--dry-run", data=d))
        self.assertEqual(json.loads(out)["moves"][0]["no_trade"], True)
        code, out, err = run(self.args("--tickers", "DEMO", "--no-feeds", "--json", "--dry-run"))
        self.assertEqual(json.loads(out)["moves"][0]["no_trade"], False)   # traded today

    def test_moves(self):
        now = news.parse_now(NOW)
        m = news.page_move("DEMO", page("DEMO"), now)
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (101.6, 98.4, 3.25))
        self.assertEqual((m["lower"], m["upper"], m["limits_from"], m["at_limit"]), (88.56, 108.24, "company page", None))
        self.assertEqual(news.to_pkt(m["time"]), datetime.datetime(2026, 10, 29, 15, 49))
        quiet = dict(m, pct=1.2)
        self.assertNotIn("MOVING", news.render_moves([quiet], now=now)[0])
        self.assertIn("MOVING", news.render_moves([quiet], move_pct=1.0, now=now)[0])
        self.assertIsNone(news.page_move("DEMO", page("DEMO"), news.parse_now("2026-10-29 15:00")))  # after --now
        self.assertIn("at 3:49 PM PKT on Thu 2026-10-29", news.render_moves([m], now=now + datetime.timedelta(days=1))[0])
        # no company page: the newest daily closing file; ILP did not trade on 10-29
        code, out, err = run(self.args("--tickers", "KEL", "ILP", "--dry-run"))
        self.assertIn("KEL         6.08 -0.65% vs the last close 6.12, official close of Thu 2026-10-29", out)
        self.assertNotIn("ILP  ", out)
        self.assertIn("no company page for ILP, KEL.", out)
        # before the close, today's file does not count yet
        code, out, err = run(self.args("--tickers", "KEL", "ILP", "--dry-run", now="2026-10-29 10:00"))
        self.assertIn("KEL         6.12 +3.90% vs the last close 5.89, official close of Wed 2026-10-28  MOVING", out)
        self.assertIn("ILP        72.40 +1.36% vs the last close 71.43, official close of Wed 2026-10-28", out)
        code, out, err = run(self.args("--tickers", "DEMO", "--no-moves", "--dry-run"))
        self.assertNotIn("vs the last close", out)
        self.assertIn("Material Information", out)

    def test_price_limits_and_page_flags(self):
        d = self.data_copy("DEMO", "HBL")
        path = os.path.join(d, "company", "DEMO.html")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        text = text.replace("Rs.101.60", "Rs.108.24").replace(">3.20<", ">9.84<").replace("(3.25%)", "(10.00%)")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(os.path.join(d, "mkt_summary", "2026-10-29.lis"), "w") as fh:   # a bare closing11.lis
            fh.write("29OCT2026|ZZZ|0818|Zed Limited|9.50|9.80|9.00|9.00|1000|10.00|||\n"
                     "29OCT2026|KEL|0824|K-Electric|6.12|6.17|5.12|5.12|1000|6.12|||\n")
        os.remove(os.path.join(d, "mkt_summary", "2026-10-29.Z"))
        code, out, err = run(self.args("--tickers", "DEMO", "HBL", "KEL", "ZZZ", "--dry-run", data=d))
        self.assertIn("DEMO      108.24 +10.00% vs the last close 98.40 at 3:49 PM PKT  MOVING  AT UPPER LIMIT 108.24", out)
        self.assertIn("HBL       273.36 -0.60% vs the last close 275.00 at 3:49 PM PKT  XD (ex-dividend)", out)
        self.assertIn("KEL         5.12 -16.34% vs the last close 6.12, official close of Thu 2026-10-29  MOVING  "
                      "AT LOWER LIMIT 5.12", out)          # 10% of 6.12 is under Rs 1: the limit is 6.12 - 1.00
        self.assertIn("ZZZ         9.00 -10.00% vs the last close 10.00, official close of Thu 2026-10-29  MOVING  "
                      "AT LOWER LIMIT 9.00", out)
        code, out, err = run(self.args("--tickers", "DEMO", "--json", "--dry-run", data=d))
        m = json.loads(out)["moves"][0]
        self.assertEqual((m["at_limit"], m["upper"], m["moving"]), ("upper", 108.24, True))

    def test_site_off_says_what_to_save(self):
        code, out, err = run(self.args("--tickers", "DEMO", "PSO", "TRG", "--dry-run"))
        notes = [ln for ln in out.splitlines() if ln.startswith("note:")]
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("note: no company page for PSO, TRG. The PSX site is off for this tool: "
                                            "save https://dps.psx.com.pk/company/<SYM> as "))
        self.assertIn(os.path.join(MARKET, "company", "<SYM>.html"), notes[0])
        self.assertIn('"psx_site": true', notes[0])
        code, out, err = run(self.args("--tickers", "PSO", "--dry-run"))
        self.assertIn("note: no company page for PSO. The PSX site is off for this tool: save "
                      "https://dps.psx.com.pk/company/PSO as %s" % os.path.join(MARKET, "company", "PSO.html"), out)

    def test_site_on_reads_the_psx_site(self):
        news.http_get = self.fake_get
        cfg = self.write("config.json", {"psx_site": True})
        for name, _, url in news.FEEDS:
            if name != "brecorder-business-finance":
                self.serve[url] = raw(os.path.join(FEED_DIR, name + ".xml"))
        self.serve["https://dps.psx.com.pk/company/DEMO"] = raw(os.path.join(MARKET, "company", "DEMO.html"))
        d = self.data_copy()                      # no saved company page: it must come from the site
        code, out, err = run(self.args("--tickers", "DEMO", "--dry-run", config=cfg, data=d, **self.live()))
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.calls), sorted(["https://dps.psx.com.pk/company/DEMO"] +
                                                    [u for _, _, u in news.FEEDS]))
        self.assertIn("! 2026-10-28  DEMO    Material Information", out)
        self.assertIn("DEMO      101.60 +3.25% vs the last close 98.40 at 3:49 PM PKT  MOVING", out)
        self.assertIn("note: feed brecorder-business-finance: HTTP 404 for https://www.brecorder.com/feeds/"
                      "business-finance", out)
        self.assertNotIn("To read a notice", out)                  # the site is on: the links may be opened
        self.assertFalse(os.path.exists(os.path.join(d, "company", "DEMO.html")))   # PSX bans caching its pages
        # a saved page never hides the site (D3): the site is asked first
        self.calls[:] = []
        d2 = self.data_copy("OGDC", name="data2")
        shutil.copy(os.path.join(MARKET, "company", "OGDC.html"), os.path.join(d2, "company", "DEMO.html"))
        code, out, err = run(self.args("--tickers", "DEMO", "--dry-run", "--no-feeds", config=cfg, data=d2,
                                       **self.live()))
        self.assertEqual(self.calls, ["https://dps.psx.com.pk/company/DEMO"])
        self.assertIn("tags: material  https://dps.psx.com.pk/download/document/290111.pdf", out)   # DEMO's own
        self.assertNotIn("note:", out)                   # the wrong saved page was never read
        # the site fails: the saved page is used, and the tool says so
        self.calls[:] = []
        del self.serve["https://dps.psx.com.pk/company/DEMO"]
        d3 = self.data_copy("DEMO", name="data3")
        code, out, err = run(self.args("--tickers", "DEMO", "--dry-run", "--no-feeds", config=cfg, data=d3,
                                       **self.live()))
        self.assertEqual(self.calls, ["https://dps.psx.com.pk/company/DEMO"])
        self.assertIn("note: DEMO: the PSX site did not give the page, so the saved page was used. Its notices "
                      "can be old.", out)
        self.assertIn("Material Information", out)

    def test_a_replay_with_now_never_fetches(self):
        news.http_get = self.fake_get                    # the site is on, but --now is a replay (D3)
        cfg = self.write("config.json", {"psx_site": True})
        code, out, err = run(self.args("--tickers", "DEMO", "OGDC", "KEL", "--dry-run", offline=None, config=cfg))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls, [])                 # no company page, no daily file, no feed
        self.assertIn("! 2026-10-28  DEMO    Material Information", out)          # the saved page
        self.assertIn("Demo Industries wins Rs1.2bn packaging order", out)        # the saved feed
        self.assertIn("KEL         6.08 -0.65% vs the last close 6.12, official close of Thu 2026-10-29", out)
        d = self.data_copy("DEMO")
        os.remove(os.path.join(d, "news", "dawn-business.xml"))
        code, out, err = run(self.args("--tickers", "DEMO", "--feeds", "dawn-business", "--dry-run", offline=None,
                                       config=cfg, data=d))
        self.assertEqual(self.calls, [])
        self.assertIn("no saved feed at %s (--offline and --now read only the data folder)"
                      % os.path.join(d, "news", "dawn-business.xml"), out)

    def test_site_off_fetches_only_the_feeds(self):
        news.http_get = self.fake_get
        for name, _, url in news.FEEDS:
            self.serve[url] = b"<rss><channel></channel></rss>"
        code, out, err = run(self.args("--tickers", "DEMO", "PSO", "--dry-run", **self.live()))
        self.assertEqual(sorted(self.calls), sorted(u for _, _, u in news.FEEDS))   # never dps.psx.com.pk
        self.assertIn("Material Information", out)            # the saved page
        self.assertIn("note: no company page for PSO. The PSX site is off", out)

    def test_tickers_with_commas_and_junk_symbols(self):
        code, out, err = run(self.args("--tickers", "DEMO,OGDC", "HUBC,", "--json", "--dry-run", "--no-feeds"))
        self.assertEqual(code, 0, err)
        self.assertEqual([m["ticker"] for m in json.loads(out)["moves"]], ["DEMO", "HUBC", "OGDC"])
        news.http_get = self.fake_get
        cfg = self.write("config.json", {"psx_site": True})
        for bad in (["DEMO,OGDC,ZZ/Z"], ["DEMO", "OG DC"], ["../x"]):
            code, out, err = run(self.args("--tickers", *bad, config=cfg, **self.live()))
            self.assertEqual(code, 2, bad)
            self.assertIn("news: not a PSX symbol:", err)
        self.assertEqual(self.calls, [])                 # refused before any fetch
        wl = self.write("watchlist.json", {"hits": [{"symbol": "DEMO"}, {"symbol": "DEMO OGDC"}]})
        code, out, err = run(self.args("--no-feeds", "--dry-run", watchlist=wl))
        self.assertIn('note: skipped "DEMO OGDC" in your files: not a PSX symbol', out)
        self.assertIn("Material Information", out)

    def test_the_window_by_date_and_a_pinned_now(self):
        code, out, err = run(self.args("--tickers", "DEMO", "--hours", "6", "--json", "--dry-run"))
        got = [(i["kind"], i["date"]) for i in json.loads(out)["items"]]
        self.assertEqual(got, [("notice", "2026-10-29"), ("headline", "2026-10-29")])
        code, out, err = run(self.args("--tickers", "DEMO", "--dry-run", now="2026-10-28 18:00"))
        self.assertIn("! 2026-10-28  DEMO    Material Information", out)
        self.assertNotIn("Disclosure of Interest", out.split("2026-10-28  DEMO")[0])   # posted 10-29: not yet
        self.assertNotIn("2026-10-29", out.replace("Checked 2026-10-28", ""))
        self.assertNotIn("Demo Industries wins", out)               # 20:14, after the pinned clock
        # the saved page is as of 10-29 3:49 PM, after the clock: the 10-28 closing file gives the move
        self.assertIn("DEMO       98.40 +1.91% vs the last close 96.56, official close of Wed 2026-10-28", out)

    def test_only_shown_items_are_remembered(self):
        code, out, err = run(self.args("--tickers", "DEMO", "--hours", "6"))
        self.assertEqual(code, 0, err)
        with open(self.state) as fh:
            state = json.load(fh)
        self.assertEqual(sorted(state["seen"]), ["psx:290210", "rss:https://www.brecorder.com/news/40447120"])
        self.assertEqual(state["last_check"], "2026-10-29T12:00:00Z")
        code, out, err = run(self.args("--tickers", "DEMO", "--json"))
        self.assertEqual(code, 0, err)                              # the wider window still has new items
        self.assertEqual([i["id"] for i in json.loads(out)["items"] if i["new"]],
                         ["psx:290111", "rss:https://www.dawn.com/news/2040702", "psx:290000", "psx:290005"])

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00Z", "psx:1": "2026-10-20T00:00:00Z", "bad": "soon"}}
        now = news.parse_now(NOW)
        news.remember([], state, now)
        self.assertEqual(sorted(state["seen"]), ["psx:1"])
        self.assertEqual(state["last_check"], "2026-10-29T12:00:00Z")
        bad = self.write("state.json", "{not json")
        code, out, err = run(self.args("--tickers", "DEMO", state=bad))
        self.assertEqual(code, 0, err)
        self.assertIn("is not valid JSON", out)            # noted, and a new state is started
        with open(bad) as fh:
            self.assertIn("psx:290111", json.load(fh)["seen"])

    def test_karachi_time(self):
        self.assertEqual(news.to_pkt(datetime.datetime(2026, 10, 28, 19, 30)), datetime.datetime(2026, 10, 29, 0, 30))
        self.assertEqual(news.parse_now("2026-10-29 17:00"), datetime.datetime(2026, 10, 29, 12, 0))
        self.assertEqual(news.parse_now("2026-10-29T12:00:00Z"), datetime.datetime(2026, 10, 29, 12, 0))
        self.assertEqual(news.KARACHI_HOURS, 5)
        code, out, err = run(self.args("--tickers", "DEMO", "--dry-run", now="2026-10-29T12:00:00Z"))
        self.assertIn("Checked 2026-10-29 17:00 PKT.", out)

    def test_bad_input_is_a_plain_error(self):
        cfg = self.write("config.json", "{bad")
        code, out, err = run(self.args("--tickers", "DEMO", config=cfg))
        self.assertEqual(code, 2)
        self.assertIn("is not valid JSON", err)
        code, out, err = run(self.args("--tickers", "DEMO", now="tomorrow"))
        self.assertEqual(code, 2)
        self.assertIn("--now wants a time like", err)
        cfg = self.write("config2.json", {"news_feeds": 5})
        code, out, err = run(self.args("--tickers", "DEMO", config=cfg))
        self.assertEqual(code, 2)
        self.assertIn('"news_feeds" in psx/config.json wants a list', err)
        cfg = self.write("config3.json", {"news_feeds": "dawn-business"})   # one text works like --feeds
        code, out, err = run(self.args("--tickers", "DEMO", "--json", "--dry-run", config=cfg))
        self.assertEqual(set(i["source"] for i in json.loads(out)["items"]), {"PSX notice", "Dawn"})

    def test_feed_choices(self):
        code, out, err = run(self.args("--tickers", "DEMO", "--feeds", "dawn-business", "--json", "--dry-run"))
        self.assertEqual(set(i["source"] for i in json.loads(out)["items"]), {"PSX notice", "Dawn"})
        cfg = self.write("config.json", {"news_feeds": ["brecorder-markets"]})
        code, out, err = run(self.args("--tickers", "DEMO", "--json", "--dry-run", config=cfg))
        self.assertEqual(set(i["source"] for i in json.loads(out)["items"]), {"PSX notice", "Business Recorder"})
        code, out, err = run(self.args("--tickers", "DEMO", "--no-feeds", "--json", "--dry-run"))
        self.assertEqual(set(i["kind"] for i in json.loads(out)["items"]), {"notice"})
        code, out, err = run(self.args("--tickers", "DEMO", "--no-notices", "--json", "--dry-run"))
        self.assertEqual(set(i["kind"] for i in json.loads(out)["items"]), {"headline"})
        self.assertEqual(len(json.loads(out)["moves"]), 1)
        code, out, err = run(self.args("--tickers", "DEMO", "--feeds", "brecorder-business-finance", "--dry-run"))
        self.assertIn("note: feed brecorder-business-finance: no saved feed at", out)

    def test_a_saved_page_of_another_stock_is_skipped(self):
        d = self.data_copy()
        shutil.copy(os.path.join(MARKET, "company", "OGDC.html"), os.path.join(d, "company", "DEMO.html"))
        code, out, err = run(self.args("--tickers", "DEMO", "--no-feeds", "--dry-run", data=d))
        self.assertIn("note: DEMO: the saved page is the page of OGDC, so it was skipped", out)
        self.assertNotIn("CREDIT OF FINAL CASH DIVIDEND", out)
        self.assertIn("DEMO      101.60 +3.25% vs the last close 98.40, official close of Thu 2026-10-29  MOVING", out)


if __name__ == "__main__":
    unittest.main()
