"""screener.html and indices.html, with the tags, classes and nesting of the real pages
(trimmed to what a reader needs), filled with the made-up sample market."""

import datetime
import html
import os

from . import fin
from .universe import COMPANY_PAGES, NC_TAG, ORDINARY, XD_EVENTS, listed
from .util import comma, page_clock, r2, rng, short_money, write_text

DAY = datetime.date(2026, 10, 29)
NOTE = ("Data delayed 5 minutes unless otherwise indicated. <br>Data powered by  <span style=\"font-weight:bold; "
        "color:#4583b6;\">Capital Stake</span>. See the Capital Stake  <a class=\"open__disclaimer\">Terms of "
        "Use</a>.")
HEAD = ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>%s</title></head><body><div '
        'class="header"><div class="topbar"><div class="topbar__container"><div class="topbar__menus"><div '
        'class="topbar__menu"><div class="topbar__now"><div class="topbar__status" style="border:none;"><div '
        'class="topbar__status__label">%s</div></div></div></div></div></div></div></div>')
FOOT = ('<div class="footer"><div class="footer__bottom"><div class="footer__bottom__item footer__site"> '
        'https://www.psx.com.pk</div></div></div></body></html>')


def change_cell(v, text):
    """A change cell: up, down or no change, as the real page writes it."""
    kind, icon = ("pos", "icon-up-dir") if v > 0 else (("neg", "icon-down-dir") if v < 0 else ("noc", ""))
    return ('<td class="right change__text--%s" data-order="%s" data-filter="%s"><i class="%s"></i> %s</td>'
            % (kind, repr(v), repr(v), icon, text))


def num_cell(order, text):
    """A plain number cell with data-order and data-filter."""
    return '<td class="right" data-order="%s" data-filter="%s">%s</td>' % (order, order, text)


def last_two(by):
    """(today's row, the day before's row) of a symbol: today = its latest day."""
    ds = sorted(by)
    return by[ds[-1]], by[ds[-2]]


def screener_numbers(sym, kind, shares_mn, ff_pct, by):
    """price, change %, market cap, free float shares, 30-day volume average, PE, yield, 1y %."""
    g = rng("screener", sym)
    today, _ = last_two(by)
    price = today["close"]
    pct = round((price - today["ldcp"]) / today["ldcp"] * 100, 3)
    shares = int(shares_mn * 1000000)
    vols = [by[d]["volume"] for d in sorted(by)[-30:]]
    avg = round(sum(vols) / float(len(vols)), 2)
    if sym in COMPANY_PAGES:
        pe = price / fin.ttm_eps(sym)
    else:
        pe = g.uniform(4.0, 24.0) if kind == "ordinary" else 0.0
    dy = round(g.uniform(0.0, 11.0), 2) if kind in ("ordinary", "reit", "modaraba") else 0.0
    return {"price": price, "pct": pct, "cap": round(price * shares, 2), "ff": int(shares * ff_pct / 100),
            "avg": avg, "pe": pe, "dy": dy, "y1": round(g.uniform(-30.0, 60.0), 3)}


def screener_row(sym, sector, full, kind, shares_mn, ff_pct, by, indices):
    """One <tr> of the screener table."""
    n = screener_numbers(sym, kind, shares_mn, ff_pct, by)
    tags = ""
    if sym in XD_EVENTS and XD_EVENTS[sym][0] <= DAY.isoformat() <= XD_EVENTS[sym][2]:
        tags += '<div class="tag tag--skim tag--xd">XD</div>'
    if sym in NC_TAG:
        tags += '<div class="tag tag--skim tag--def">NC</div>'
    cells = [num_cell(repr(n["cap"]), short_money(n["cap"])), num_cell(repr(n["price"]), "%.2f" % n["price"]),
             change_cell(n["pct"], "%.2f%%" % n["pct"]), change_cell(n["y1"], "%.2f%%" % n["y1"]),
             num_cell(repr(n["pe"]), "%.2f" % n["pe"]), num_cell(repr(n["dy"]), "%.2f" % n["dy"]),
             num_cell(str(n["ff"]), short_money(n["ff"])), num_cell(repr(n["avg"]), comma(n["avg"], 0))]
    return ('<tr><td data-order="%s"><a class="tbl__symbol" href="/company/%s" data-title="%s"><strong>%s</strong>'
            '</a>%s</td><td>%s</td><td>%s</td>%s</tr>'
            % (sym, sym, html.escape(full), sym, tags, sector, indices, "".join(cells)))


def screener_html(market):
    """The whole screener page as saved after the close."""
    idx = dict((r[0], r[8]) for r in ORDINARY)
    rows = []
    for sym, sector, short, full, price, usual, shares, ff, kind in sorted(listed()):
        rows.append(screener_row(sym, sector, full, kind, shares, ff, market[sym], idx.get(sym, "ALLSHR")))
    heads = [("symbol", "SYMBOL", ""), ("sector", "SECTOR", ""), ("listed", "LISTED IN", ""),
             ("marketCap", "MARKET CAP.", "right"), ("close", "PRICE", "right"),
             ("percentChange", "CHANGE (%)", "right"), ("changeYear", "1-YEAR CH. (%) *", "right"),
             ("peRatio", "PE RATIO (TTM)", "right"), ("dividendYield", "DIVIDEND YIELD (%)", "right"),
             ("freeFloat", "FREE FLOAT", "right"), ("volume30Avg", "30D VOLUME AVG.", "right")]
    th = "".join('<th%s data-name="%s">%s</th>' % (' class="right"' if c else "", k, t) for k, t, c in heads)
    clock = page_clock(datetime.datetime(2026, 10, 29, 15, 52), weekday=False)
    body = ('<div class="pageHeader__wrapper"><div class="pageHeader"><div class="pageHeader__title">Stock Screener'
            '</div></div></div><div class="section"><div class="tbl__wrapper"><table class="tbl" id="screenerTable" '
            'data-page-length="25" style="min-width: 900px;"><thead class="tbl__head"><tr>%s</tr></thead><tbody '
            'class="tbl__body">%s</tbody></table></div><div class="screenerNote"><div><div class="poweredBy" '
            'style="margin:1em;"><div class="text">%s</div></div></div><div><div style="margin:1em;">* Returns not '
            'adjusted for payouts</div></div></div></div>' % (th, "".join(rows), NOTE))
    return HEAD % ("Stock Screener | Pakistan Stock Exchange (PSX)", clock) + body + FOOT


# ---------------------------------------------------------------- indices

INDICES = [   # code, previous close, close (made up); high and low are made around them
    ("KSE100", 155837.14, 157102.66), ("ALLSHR", 95104.38, 95811.27),
    ("KSE30", 47512.36, 47897.21), ("KMI30", 226488.90, 228375.64),
]


def indices_html():
    """The indices page: one row per index and the "As of" line."""
    rows = []
    for code, prev, cur in INDICES:
        hi, lo = r2(max(prev, cur) * 1.0021), r2(min(prev, cur) * 0.9987)
        ch = r2(cur - prev)
        pct = (cur - prev) / prev * 100
        cells = "".join('<td class="right" data-order="%s">%s</td>' % (repr(v), comma(v)) for v in (hi, lo, cur))
        kind, icon = ("pos", "icon-up-dir") if ch >= 0 else ("neg", "icon-down-dir")
        cells += ('<td class="right change__text--%s" data-order="%s"><i class="%s"></i> %.2f</td>'
                  '<td class="right change__text--%s" data-order="%s"><i class="%s"></i> %.2f%%</td>'
                  % (kind, repr(ch), icon, ch, kind, repr(pct), icon, pct))
        rows.append('<tr><td><a class="link" href="javascript:;" data-code="%s"><b>%s</b></a></td>%s</tr>'
                    % (code, code, cells))
    head = ('<tr><th>Index</th><th class="right">High</th><th class="right">Low</th><th class="right">Current</th>'
            '<th class="right">Change</th><th class="right">% Change</th></tr>')
    body = ('<div class="pageHeader__wrapper"><div class="pageHeader"><div class="pageHeader__title">Indices</div>'
            '</div></div><div class="section section--padded" id="indicesTable"><div style="text-align: right; '
            'padding-bottom: 0.4em;">As of  %s</div><table class="tbl"><thead class="tbl__head">%s</thead><tbody '
            'class="tbl__body">%s</tbody></table></div>'
            % (page_clock(datetime.datetime(2026, 10, 29, 15, 50), weekday=False), head, "".join(rows)))
    clock = page_clock(datetime.datetime(2026, 10, 29, 15, 52), weekday=False)
    return HEAD % ("Indices | Pakistan Stock Exchange (PSX)", clock) + body + FOOT


def write_pages(out, market):
    """screener.html and indices.html."""
    a, b = os.path.join(out, "screener.html"), os.path.join(out, "indices.html")
    write_text(a, screener_html(market))
    write_text(b, indices_html())
    return [a, b]
