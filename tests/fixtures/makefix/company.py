"""company/<SYM>.html: saved company pages, as of Thu, Oct 29, 2026 3:49 PM (made-up numbers)."""

import datetime
import html
import os

from . import fin, notices, pages
from .common import price_limits
from .universe import XD_EVENTS, row_of
from .util import comma, page_clock, page_day, r2, write_text

DAY = datetime.date(2026, 10, 29)
ASOF = page_clock(datetime.datetime(2026, 10, 29, 15, 49))
SECTOR_NAMES = {"0818": "MISCELLANEOUS", "0820": "OIL &amp; GAS EXPLORATION COMPANIES",
                "0807": "COMMERCIAL BANKS", "0809": "FERTILIZER", "0828": "TECHNOLOGY &amp; COMMUNICATION"}
PROFILE = {   # made-up text and made-up people (no real people)
    "DEMO": ("Demo Industries Limited is a made-up company used to test the PSX tools. It makes packaging "
             "and household goods.", "Plot 12, Sector 7, Korangi Industrial Area, Karachi",
             "www.demo-industries.example", [("Farhan Qadir", "CEO"), ("Nadia Rehman", "Chairperson"),
                                             ("Imran Siddiq", "Company Secretary")]),
    "OGDC": ("The company explores for and produces oil and gas in Pakistan.", "Jinnah Avenue, Blue Area, "
             "Islamabad", "www.ogdcl.com", [("Kamran Aslam", "CEO"), ("Saima Javed", "Chairperson"),
                                           ("Tariq Mehmood", "Company Secretary")]),
    "HBL": ("A commercial bank with branches across Pakistan and abroad.", "I.I. Chundrigar Road, Karachi",
            "www.hbl.com", [("Usman Ghani", "President &amp; CEO"), ("Rabia Sultan", "Chairperson"),
                            ("Adeel Farooq", "Company Secretary")]),
    "FFC": ("The company makes and sells urea and other fertilizers.", "The Mall, Rawalpindi", "www.ffc.com.pk",
            [("Zahid Hussain", "CEO"), ("Ayesha Karim", "Chairperson"), ("Bilal Anwar", "Company Secretary")]),
    "SYS": ("A software and IT services company.", "Sehjpal, Lahore Cantt, Lahore", "www.systemsltd.com",
            [("Hamza Rafiq", "CEO"), ("Sana Malik", "Chairperson"), ("Owais Qureshi", "Company Secretary")]),
}
FYE = {"DEMO": "June", "OGDC": "June", "HBL": "December", "FFC": "December", "SYS": "December"}


def stat(label, value, cls=""):
    """One stats item."""
    return ('<div class="stats_item"><div class="stats_label">%s</div><div class="stats_value%s">%s</div></div>'
            % (label, (" " + cls) if cls else "", value))


def range_stat(label, lo, hi, cur):
    """A low-high stats item with its numRange bar (data-low, data-high)."""
    return stat(label, '%s %s %s<div class="numRange" data-low="%s" data-high="%s" data-current="%s"></div>'
                % (comma(lo), chr(0x2014), comma(hi), repr(lo), repr(hi), repr(cur)))


def change_stat(label, v):
    """A change stats item: up or down arrow and the percent."""
    cls, icon = ("change__text--pos", "icon-up-dir") if v >= 0 else ("change__text--neg", "icon-down-dir")
    return stat(label, '<i class="%s"></i>  %.2f%%' % (icon, v), cls)


def quote(sym, full, sector, by):
    """The quote section: name, tags, price, change, As of, REG board stats."""
    today = by[DAY]
    ldcp, price = today["ldcp"], today["close"]
    ch = r2(price - ldcp)
    pct = ch / ldcp * 100
    lower, upper = price_limits(ldcp)
    lows = [r["low"] for r in by.values() if r["low"]] + [today["low"]]
    highs = [r["high"] for r in by.values() if r["high"]]
    tag = '<div class="tag tag--skim tag--xd">XD</div>' if sym in XD_EVENTS and XD_EVENTS[sym][0] == "2026-10-29" else ""
    kind, icon = ("pos", "icon-up-dir") if ch >= 0 else ("neg", "icon-down-dir")
    n = pages.screener_numbers(sym, "ordinary", row_of(sym)[6], row_of(sym)[7], by)
    reg = ('<div class="tabs__panel" data-name="REG"><div class="stats stats--noborder">%s%s%s%s</div>'
           '<div class="stats company__quote__rangeStats">%s%s%s</div><div class="stats">%s%s%s%s</div>'
           '<div class="stats">%s%s%s%s</div><div class="stats">%s%s<div class="stats_item"></div>'
           '<div class="stats_item"></div></div></div>'
           % (stat("Open", comma(today["open"])), stat("High", comma(today["high"])), stat("Low", comma(today["low"])),
              stat("Volume", comma(today["volume"], 0)), range_stat("CIRCUIT BREAKER", lower, upper, price),
              range_stat("DAY RANGE", today["low"], today["high"], price),
              range_stat("52-WEEK RANGE ^", r2(min(lows) * 0.84), r2(max(highs) * 1.03), price),
              stat("Ask Price", "0.00"), stat("Ask Volume", "0"), stat("Bid Price", "0.00"), stat("Bid Volume", "0"),
              stat("LDCP", comma(ldcp)), stat("VAR", "15.00"), stat("HAIRCUT", "17.50"),
              stat("P/E Ratio (TTM) **", "%.2f" % n["pe"]), change_stat("1-Year Change * ^", n["y1"]),
              change_stat("YTD Change * ^", n["y1"] * 0.6)))
    tabs = "".join('<div class="tabs__list__item" data-name="%s">%s</div>' % (k, v)
                   for k, v in (("REG", "REG"), ("FUT", "DFC "), ("CSF", "CSF"), ("ODL", "ODL")))
    return ('<div class="section section--padded company" id="quote"><div class="company__quote"><div '
            'class="quote__details"><div class="quote__name">%s%s</div><div class="quote__sector"><span>%s</span>'
            '</div><div class="quote__price"><div class="quote__close">Rs.%.2f</div><div class="quote__change '
            'change__text--%s"><div class="change__direction"><i class="%s"></i> </div><div class="change__value">'
            '%.2f</div><div class="change__percent">  (%.2f%%)</div></div></div><div class="quote__chart"><div '
            'class="chart" id="companyDailyChart"><div class="chart__tools"><div class="quote__date">^ As of %s'
            '</div></div><div class="chart__body"></div></div></div></div><div class="quote__stats" id="statsTab">'
            '<div class="tabs"><div class="tabs__list">%s</div><div class="tabs__panels">%s<div class="tabs__panel" '
            'data-name="FUT"></div></div></div></div></div></div>'
            % (html.escape(full), tag, SECTOR_NAMES[sector], price, kind, icon, ch, pct, ASOF, tabs, reg))


def profile_equity(sym, price):
    """The profile and equity sections."""
    desc, addr, web, people = PROFILE[sym]
    shares = fin.shares_of(sym)
    ff_pct = row_of(sym)[7]
    rows = "".join("<tr><td><strong>%s</strong></td><td>%s</td></tr>" % p for p in people)
    prof = ('<div class="section section--padded company" id="profile"><div class="company__profile"><h1 '
            'class="section__title">Company Profile</h1><div class="profile__items"><div class="profile__item '
            'profile__item--decription"><div class="item__head">BUSINESS DESCRIPTION</div><p>%s</p></div><div '
            'class="profile__item profile__item--people"><div class="item__head">KEY PEOPLE</div><table class="tbl">'
            '<tbody class="tbl__body">%s</tbody></table></div></div><div class="profile__items"><div '
            'class="profile__item"><div class="item__head">ADDRESS</div><p>%s</p><div class="item__head">WEBSITE'
            '</div><p> <a href="http://%s" target="_blank">%s</a></p></div><div class="profile__item"><div '
            'class="item__head">REGISTRAR</div><p>A made-up share registrar, Karachi</p></div><div '
            'class="profile__item"><div class="item__head">AUDITOR</div><p>M/s Sample &amp; Co., Chartered '
            'Accountants</p><div class="item__head">Fiscal Year End</div><p>%s</p></div></div></div></div>'
            % (desc, rows, addr, web, web, FYE[sym]))
    eq = ('<div class="section section--padded companyEquity" id="equity"><h1 class="section__title">Equity Profile'
          '</h1><div class="stats stats--wrappable">%s%s%s%s%s</div></div>'
          % (stat("Market Cap (000'<span style=\"text-transform: lowercase;\">s</span>)", comma(price * shares / 1000.0)),
             stat("Shares", comma(shares, 0)), stat("Face Value", "10.00"),
             stat("Free Float", comma(int(shares * ff_pct / 100), 0)), stat("Free Float", "%.2f%%" % ff_pct)))
    return prof + eq
