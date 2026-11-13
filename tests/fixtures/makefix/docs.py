"""docs/<id>.txt: made-up notice letters, laid out like `pdftotext -layout` output (LF, a form
feed after the page). Every person named here is made up."""

import datetime
import os

from . import fin, notices
from .util import MON, comma, r2, write_text

ADDRESS = ("   The General Manager\n   Pakistan Stock Exchange Limited\n   Stock Exchange Building\n"
           "   Stock Exchange Road\n")


def mid(row):
    """A trade price inside a day's range."""
    return r2((row["low"] + row["high"]) / 2.0)


def form29(market):
    """DEMO's Form-29 insider notice: two buys on 2026-10-28, inside the closed period."""
    row = market["DEMO"][datetime.date(2026, 10, 28)]
    a, b = mid(row), r2(min(row["high"], mid(row) + 0.15))
    lines = [
        "", "                                              FORM-29", "", ADDRESS.rstrip("\n"),
        "   Karachi" + " " * 70 + "Dated: October 29, 2026", "",
        "   Subject: Disclosure of Interest by Relevant Persons Holding Company's Shares under PSX Regulation 5.6.4.",
        "", "   Dear Sir,", "",
        "   In compliance with Regulation 5.6.4 of the PSX Regulations, we inform you that the following",
        "   transactions in the shares of Demo Industries Limited (the \"Company\") have been reported to us:", "",
        "   Sr.  Name of Relevant Person(s)     Details of Transaction(s)                                    Cumulative Shareholding",
        "   No.  with Description               Form of      Market   Date         Nature   No. of      Rate      Number of     Percentage",
        "                                       Share                                       Share(s)              shares",
        "                                       Certificate",
        "   1    Mr. Salman Akhtar              CDC          Ready    2026-10-28   Buy      %-11s %-9.2f %-13s 0.51%%"
        % ("25,000", a, "1,265,000"),
        "        (Director)",
        "   2    Mrs. Hina Salman               CDC          Ready    2026-10-28   Buy      %-11s %-9.2f %-13s 0.16%%"
        % ("10,000", b, "410,000"),
        "        (Spouse of Director)", "",
        "   The above transactions will be presented in the next meeting of the Board of Directors.", "",
        "   Yours faithfully,", "   For Demo Industries Limited", "", "", "   ______________________",
        "   Imran Siddiq", "   Company Secretary"]
    return "\n".join(lines) + "\n\f"


def sys_notice(market):
    """SYS's own insider table: Name of Person | Date of Trade | Nature of Trade | No. of shares | Rate | Market."""
    trades = [("Mr. Danish Iqbal", "(Senior Management)", datetime.date(2026, 10, 20), "Sell", 150000),
              ("Mr. Danish Iqbal", "(Senior Management)", datetime.date(2026, 10, 21), "Sell", 85500),
              ("Ms. Mehwish Tariq", "(Executive)", datetime.date(2026, 10, 22), "Buy", 5000)]
    body = []
    for name, role, d, nature, qty in trades:
        rate = mid(market["SYS"][d])
        body.append("    %-62s %-22s %-21s %-16s %-9s Ready" % (name, "%02d-%s-%d" % (d.day, MON[d.month - 1], d.year), nature, comma(qty, 0),
                                                              ("%.2f" % rate).rstrip("0").rstrip(".")))
        body.append("    " + role)
    lines = (["", ADDRESS.rstrip("\n"), "   Karachi, Pakistan" + " " * 90 + "Dated: October 27, 2026", "",
              "   Subject: Disclosure of Interest by a Director, CEO, substantial shareholder or executive of a listed",
              "   company u/s 5.6.4 of the PSX Regulations", "", "", "   Dear Sir,", "",
              "   We hereby inform you that the following transaction(s) have been executed by executives in shares of",
              "   Systems Limited (the \"Company\"), details of which are hereunder:", "",
              "    Name of Person                                                 Date of Trade Nature of"
              "                      No. of           Rate      Market",
              "                                                                                 Trade"
              "                          shares"] + body +
             ["", "", "   We confirm that the said transaction(s) will be presented in the subsequent board meeting of the",
              "   Company, as required under Section 5.6.4 of the PSX Regulations.", "   Yours Sincerely,", "", "",
              "   ________________________", "   Owais Qureshi", "   Company Secretary", "   Systems Limited"])
    return "\n".join(lines) + "\n\f"


def board_notice():
    """DEMO's notice of a board meeting on November 3, 2026, with a closed period."""
    lines = ["", "   DEMO INDUSTRIES LIMITED", "   Plot 12, Sector 7, Korangi Industrial Area, Karachi", "",
             "   October 20, 2026", "", ADDRESS.rstrip("\n"), "   Karachi", "",
             "   Subject: NOTICE OF BOARD MEETING", "", "   Dear Sir,", "",
             "   We are pleased to inform you that a meeting of the Board of Directors of Demo Industries Limited",
             "   will be held on November 3, 2026 at 11:00 a.m. at the registered office of the Company in Karachi",
             "   to consider the condensed interim consolidated financial statements of the Company for the period",
             "   ended September 30, 2026.", "",
             "   The closed period, as required by the PSX Regulations, will be from October 27, 2026 to",
             "   November 3, 2026 (both days inclusive). During this period no director, CEO or executive shall",
             "   deal, directly or indirectly, in the shares of the Company.", "",
             "   You may inform your members accordingly.", "", "   Yours faithfully,", "", "",
             "   Imran Siddiq", "   Company Secretary"]
    return "\n".join(lines) + "\n\f"


def results_letter():
    """DEMO's cover letter for the results of the quarter ended September 30, 2026."""
    sh = fin.shares_of("DEMO")
    now, before = fin.quarter("DEMO", 2027, 1), fin.quarter("DEMO", 2026, 1)
    rows = [("Sales", now[0], before[0]), ("Profit after taxation", now[2], before[2])]
    table = ["   %-40s %18s %18s" % ("", "Sep 30, 2026", "Sep 30, 2025"),
             "   %-40s %18s %18s" % ("", "(Rupees in '000)", "(Rupees in '000)")]
    table += ["   %-40s %18s %18s" % (k, comma(a, 0), comma(b, 0)) for k, a, b in rows]
    table.append("   %-40s %18s %18s" % ("Earnings per share (Rupees)", "%.2f" % fin.eps(now[2], sh),
                                         "%.2f" % fin.eps(before[2], sh)))
    lines = ["", "   DEMO INDUSTRIES LIMITED", "", "   October 27, 2026", "", ADDRESS.rstrip("\n"), "   Karachi", "",
             "   Subject: FINANCIAL RESULTS FOR THE QUARTER ENDED SEPTEMBER 30, 2026", "", "   Dear Sir,", "",
             "   The Board of Directors of Demo Industries Limited, in its meeting held on October 27, 2026,",
             "   approved the un-audited unconsolidated financial statements of the Company for the quarter ended",
             "   September 30, 2026. The results are as follows:", ""] + table + [
             "", "   No dividend has been declared for the quarter.", "", "   Yours faithfully,", "", "",
             "   Imran Siddiq", "   Company Secretary"]
    return "\n".join(lines) + "\n\f"


def material_info():
    """DEMO's material information of October 28, 2026 (a made-up order)."""
    lines = ["", "   DEMO INDUSTRIES LIMITED", "", "   October 28, 2026", "", ADDRESS.rstrip("\n"), "   Karachi", "",
             "   Subject: Material Information", "", "   Dear Sir,", "",
             "   In accordance with Section 96 of the Securities Act, 2015 and Clause 5.6.1(a) of the PSX",
             "   Regulations, we inform you that the Company has signed a contract to supply packaging goods",
             "   worth about Rs 1.2 billion over the next two years.", "",
             "   Yours faithfully,", "", "", "   Imran Siddiq", "   Company Secretary"]
    return "\n".join(lines) + "\n\f"


def write_docs(out, market):
    """docs/<id>.txt for the five notices. -> {name: path}."""
    plan = [("form29", ("DEMO", "Others", notices.INSIDER), form29(market)),
            ("sys_insider", ("SYS", "Others", notices.INSIDER), sys_notice(market)),
            ("board_notice", ("DEMO", "Board Meetings", "NOTICE OF BOARD MEETING"), board_notice()),
            ("results", ("DEMO", "Financial Results", "FINANCIAL RESULTS FOR THE QUARTER"), results_letter()),
            ("material", ("DEMO", "Others", "Material Information"), material_info())]
    paths = {}
    for name, key, text in plan:
        doc = notices.find(*key)[3]
        p = os.path.join(out, "docs", "%d.txt" % doc)
        write_text(p, text)
        paths[name] = p
    return paths
