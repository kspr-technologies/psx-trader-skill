"""The rest of a company page: announcements, financials, ratios, and the page itself."""

import datetime
import os

from . import fin, notices
from .company import DAY, profile_equity, quote
from .pages import FOOT, HEAD
from .universe import COMPANY_PAGES, row_of
from .util import comma, page_clock, page_day, write_text

TH = '<tr><th style="width:140px;">Date</th><th>Title</th><th style="width:140px;">Document</th></tr>'


def doc_cell(doc, files):
    """The Document cell: a GIF viewer link, and a PDF link when there is one."""
    cell = ' <i class="icon-file-image"></i><a href="javascript:" data-images="%d-1.gif">View</a>&nbsp;&nbsp;&nbsp;' % doc
    if files == "pdf":
        cell += '<i class="icon-file-pdf"></i><a href="/download/document/%d.pdf" target="_blank">PDF</a>' % doc
    return cell


def announcements(sym):
    """3 tabs x 5 rows, newest first, as the real page nests them."""
    panels = []
    for tab in notices.TABS:
        rows = []
        for i, (d, title, files) in enumerate(notices.ROWS[sym][tab]):
            day = page_day(datetime.date.fromisoformat(d))
            rows.append("<tr><td>%s</td><td>%s </td><td>%s</td></tr>"
                        % (day, title, doc_cell(notices.doc_id(sym, tab, i), files)))
        table = ('<table class="tbl"><thead class="tbl__head">%s</thead><tbody class="tbl__body">%s</tbody></table>'
                 % (TH, "".join(rows)))
        if tab == "Financial Results":     # the real page has no wrapper around this one
            panels.append('<div class="tabs__panel" data-name="%s">%s</div>' % (tab, table))
        else:
            panels.append('<div class="tabs__panel" data-name="%s"><div class="tbl__wrapper">%s</div></div>'
                          % (tab, table))
    tabs = "".join('<div class="tabs__list__item" data-name="%s">%s</div>' % (t, t) for t in notices.TABS)
    return ('<div class="section section--padded company" id="announcements"><div class="company__payouts"><h1 '
            'class="section__title">Announcements</h1><div id="announcementsTab"><div class="tabs"><div '
            'class="tabs__list">%s</div><div class="tabs__panels">%s</div></div></div></div></div>'
            % (tabs, "".join(panels)))


def cell(v, eps=False):
    """A number cell: thousands with commas, EPS and ratios with 2 decimals, minus in brackets."""
    text = ("%.2f" % abs(v)) if eps else comma(abs(v), 0)
    if v < 0:
        return '<td class="right"><span class="change__text--neg">(%s)</span></td>' % text
    return '<td class="right"><span class="">%s</span></td>' % text


def table(t, decimals_for=("EPS",), all_decimals=False):
    """A financials or ratios table body."""
    head = "<tr><th></th>%s</tr>" % "".join('<th class="right">%s</th>' % c for c in t["columns"])
    rows = "".join("<tr><td>%s</td>%s</tr>" % (label, "".join(cell(v, all_decimals or label in decimals_for)
                                                               for v in vals)) for label, vals in t["rows"])
    return head, rows


def financials(sym, price):
    """The financials (annual, quarterly) and ratios sections."""
    annual, quarterly, ratios = fin.tables(sym, price)
    panels = []
    for name, t in (("Annual", annual), ("Quarterly", quarterly)):
        head, rows = table(t)
        panels.append('<div class="tabs__panel" data-name="%s"><div class="tbl__wrapper"><table class="tbl '
                      'tbl--col-striped"> <thead class="tbl__head">%s<tbody class="tbl__body">%s</tbody></thead>'
                      '</table></div></div>' % (name, head, rows))
    fin_html = ('<div class="section section--padded company" id="financials"><div class="company__financials"><h1 '
                'class="section__title">Financials</h1><div id="financialTab"> <div class="tabs tabs--capsule '
                'tabs--align-right"><p style="color:#666; float:left;">All numbers in thousands (000\'s) except EPS'
                '</p><div class="tabs__list"><div class="tabs__list__item" data-name="Annual">Annual</div><div '
                'class="tabs__list__item" data-name="Quarterly">Quarterly</div></div><div class="tabs__panels">%s'
                '</div></div></div></div></div>' % "".join(panels))
    head, rows = table(ratios, all_decimals=True)
    rat = ('<div class="section section--padded company" id="ratios"><div class="company__ratios"><h1 '
           'class="section__title">Ratios</h1><div class="tbl__wrapper"><table class="tbl tbl--col-striped"> '
           '<thead class="tbl__head">%s</thead><tbody class="tbl__body">%s</tbody></table></div></div></div>'
           % (head, rows))
    return fin_html + rat


def company_html(sym, market):
    """One whole saved company page."""
    r = row_of(sym)
    by = market[sym]
    price = by[DAY]["close"]
    menu = "".join('<div><a href="#%s">%s</a></div>' % (k, k.upper())
                   for k in ("quote", "profile", "equity", "announcements", "financials", "ratios"))
    top = ('<div class="pageHeader__wrapper"><div class="pageHeader"><div class="pageHeader__title">%s</div><div '
           'class="pageHeader__menu">%s</div></div></div>' % (sym, menu))
    tail = ('<div class="section section--padded company" id="payouts"><div class="company__payouts"><h1 '
            'class="section__title">Payouts</h1></div></div><div class="section section--padded company" '
            'id="reports"><h1 class="section__title">Financial Reports</h1><div class="tbl__wrapper"></div></div>')
    clock = page_clock(datetime.datetime(2026, 10, 29, 15, 52), weekday=False)
    body = (top + quote(sym, r[3], r[1], by) + profile_equity(sym, price) + announcements(sym)
            + financials(sym, price) + tail)
    return HEAD % ("%s - %s | Pakistan Stock Exchange (PSX)" % (sym, r[3].replace("&", "&amp;")), clock) + body + FOOT


def write_companies(out, market):
    """company/<SYM>.html for the five pages."""
    paths = []
    for sym in COMPANY_PAGES:
        p = os.path.join(out, "company", sym + ".html")
        write_text(p, company_html(sym, market))
        paths.append(p)
    return paths
