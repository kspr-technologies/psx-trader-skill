#!/usr/bin/env python3
"""Make the psx-analyst test pages: made up, in the layout of a saved PSX company page.

    cd tests/fixtures/psx-analyst && python3 make.py

Every company, number, notice and person here is made up. ZLAG, ZONE and ZRGT are not
PSX symbols. The script gives the same files every time.
- ZLAG: the page lags its newest results notice; a board meeting is in progress.
- ZONE: one-off items (profit above sales, profit growth far above sales growth).
- ZRGT: a right, so the page has no financials.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
CLOCK = "Oct 29, 2026 3:52 PM"


def stat(label, value):
    return ('<div class="stats_item"><div class="stats_label">%s</div><div class="stats_value">%s</div></div>'
            % (label, value))


def section(name, body, cls="company"):
    return '<div class="section section--padded %s" id="%s">%s</div>' % (cls, name, body)


def quote(name, sector, price, change, ldcp, pe):
    stats = stat("LDCP", "%.2f" % ldcp) + stat("P/E Ratio (TTM) **", "%.2f" % pe if pe else "N/A")
    return section("quote", (
        '<div class="company__quote"><div class="quote__details"><div class="quote__name">%s</div>'
        '<div class="quote__sector"><span>%s</span></div><div class="quote__price"><div class="quote__close">'
        'Rs.%.2f</div><div class="quote__change change__text--pos"><div class="change__direction"><i class="'
        'icon-up-dir"></i> </div><div class="change__value">%.2f</div><div class="change__percent">  (%.2f%%)'
        '</div></div></div><div class="quote__chart"><div class="chart"><div class="chart__tools"><div class="'
        'quote__date">^ As of Thu, Oct 29, 2026 3:49 PM</div></div></div></div></div><div class="quote__stats">'
        '<div class="tabs"><div class="tabs__panels"><div class="tabs__panel" data-name="REG"><div class="stats">'
        '%s</div></div></div></div></div></div>') % (name, sector, price, change, 100.0 * change / ldcp, stats))


def profile(fye):
    return section("profile", '<div class="company__profile"><div class="profile__item"><div class="item__head">'
                              'Fiscal Year End</div><p>%s</p></div></div>' % fye)


def equity(shares):
    return section("equity", stat("Shares", "{:,}".format(shares)), "companyEquity")


def notices(tabs):
    """tabs: {tab: [(date text, title, doc id)]}; every notice has a GIF and a PDF."""
    out = []
    for tab in ("Financial Results", "Board Meetings", "Others"):
        rows = "".join(
            '<tr><td>%s</td><td>%s </td><td> <i class="icon-file-image"></i><a href="javascript:" data-images="'
            '%s-1.gif">View</a>&nbsp;&nbsp;&nbsp;<i class="icon-file-pdf"></i><a href="/download/document/%s.pdf" '
            'target="_blank">PDF</a></td></tr>' % (day, title, doc, doc) for day, title, doc in tabs.get(tab, []))
        out.append('<div class="tabs__panel" data-name="%s"><table class="tbl"><tbody class="tbl__body">%s'
                   '</tbody></table></div>' % (tab, rows))
    return section("announcements", '<div id="announcementsTab"><div class="tabs"><div class="tabs__panels">%s'
                                    '</div></div></div>' % "".join(out))


def table(heads, rows, decimals=()):
    th = "".join('<th class="right">%s</th>' % h for h in heads)
    body = "".join("<tr><td>%s</td>%s</tr>" % (label, "".join(
        '<td class="right"><span class="">%s</span></td>' % (("%.2f" % v) if label in decimals else "{:,}".format(v))
        for v in vals)) for label, vals in rows)
    return ('<table class="tbl tbl--col-striped"> <thead class="tbl__head"><tr><th></th>%s</tr></thead>'
            '<tbody class="tbl__body">%s</tbody></table>' % (th, body))


def financials(annual, quarterly):
    eps = ("EPS",)
    panels = "".join('<div class="tabs__panel" data-name="%s">%s</div>' % (name, table(t[0], t[1], eps))
                     for name, t in (("Annual", annual), ("Quarterly", quarterly)))
    return section("financials", '<p>All numbers in thousands (000\'s) except EPS</p><div class="tabs__panels">'
                                 '%s</div>' % panels)


def ratios(heads, rows):
    return section("ratios", table(heads, rows, [r[0] for r in rows]))


def page(sym, parts):
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>%s (made up)</title></head><body>'
            '<div class="topbar__status__label">%s</div><div class="pageHeader__title">%s</div>%s</body></html>\n'
            % (sym, CLOCK, sym, "".join(parts)))


ZLAG = page("ZLAG", [
    quote("Zed Lag Industries Limited", "CEMENT", 50.00, 0.50, 49.50, 12.50), profile("June"), equity(100000000),
    notices({"Financial Results": [
        ("Oct 28, 2026", "Financial Results for the Quarter Ended 30 September 2026", "390010"),
        ("Oct 20, 2026", "Transmission of Annual Report for the Year Ended June 30, 2026", "389900"),
        ("Sep 15, 2026", "FINANCIAL RESULTS FOR THE YEAR ENDED JUNE 30, 2026", "388800")],
        "Board Meetings": [
        ("Oct 28, 2026", "Board Meeting In Progress", "390005"),
        ("Oct 20, 2026", "Board Meeting", "389950"),
        ("Oct 8, 2026", "Notice of Annual General Meeting", "389000")]}),
    financials((["2026", "2025", "2024", "2023"], [
        ("Sales", [4000000, 3600000, 3300000, 3000000]), ("Profit after Taxation", [400000, 324000, 297000, 270000]),
        ("EPS", [4.00, 3.24, 2.97, 2.70])]),
        (["Q3 2026", "Q2 2026", "Q1 2026", "Q3 2025"], [
            ("Sales", [1050000, 1000000, 950000, 1000000]), ("Profit after Taxation", [110000, 100000, 90000, 100000]),
            ("EPS", [1.10, 1.00, 0.90, 1.00])])),
    ratios(["2026", "2025", "2024", "2023"], [("Gross Profit Margin (%)", [30.00, 29.50, 29.00, 28.50]),
                                              ("Net Profit Margin (%)", [10.00, 9.00, 9.00, 9.00])])])

ZONE = page("ZONE", [
    quote("Zed One-Off Limited", "CHEMICAL", 30.00, 0.30, 29.70, 0.70), profile("December"), equity(50000000),
    notices({}),
    financials((["2025", "2024"], [
        ("Sales", [2000000, 1900000]), ("Profit after Taxation", [2100000, 150000]), ("EPS", [42.00, 3.00])]),
        (["Q3 2026", "Q2 2026", "Q1 2026", "Q3 2025"], [
            ("Sales", [525000, 510000, 505000, 500000]), ("Profit after Taxation", [60000, 45000, 44000, 40000]),
            ("EPS", [1.20, 0.90, 0.88, 0.80])]))])

ZRGT = page("ZRGT", [quote("Zed Right Limited (Right)", "CHEMICAL", 2.00, 0.10, 1.90, None), equity(1000000)])


if __name__ == "__main__":
    folder = os.path.join(HERE, "company")
    os.makedirs(folder, exist_ok=True)
    for sym, text in (("ZLAG", ZLAG), ("ZONE", ZONE), ("ZRGT", ZRGT)):
        with open(os.path.join(folder, sym + ".html"), "w", encoding="utf-8") as fh:
            fh.write(text)
    print("wrote %s: ZLAG.html, ZONE.html, ZRGT.html" % folder)
