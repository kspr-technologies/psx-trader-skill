"""Made-up results of the five companies with a company page. Rs thousands, except EPS.

Every number is made up, but they fit together: a year is the sum of its four quarters,
EPS = profit after tax x 1000 / shares, the P/E uses the last four quarters.
"""

from decimal import Decimal, ROUND_HALF_UP

from .universe import row_of

# symbol: (fiscal year end, last full year, quarters out in the year after, bank?, sales of the
#          last full year (000), net margin %, yearly growth %, gross margin %, wrong label of the newest)
PLAN = {
    "DEMO": ("June", 2026, 1, False, 38450200, 7.6, 13.0, 24.5, None),
    "OGDC": ("June", 2026, 0, False, 431250600, 47.5, 6.0, 55.2, None),
    "HBL": ("December", 2025, 3, True, 598400300, 10.4, 9.0, None, None),
    "FFC": ("December", 2025, 3, False, 402316700, 16.8, 11.0, 31.4, "Q1 2027"),
    "SYS": ("December", 2025, 3, False, 41870900, 17.9, 14.0, 27.3, None),
}
SEASON = (0.235, 0.245, 0.250, 0.270)      # share of the year in Q1 to Q4


def eps(pat, shares):
    """EPS in rupees from profit after tax in Rs thousands, half up to 0.01."""
    v = Decimal(pat) * 1000 / Decimal(shares)
    return float(v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def shares_of(sym):
    """Made-up share count of a symbol (universe: millions)."""
    return int(row_of(sym)[6] * 1000000)


def years(sym):
    """{fiscal year: (sales, total income or None, profit after tax)}, five full years and the
    quarters of the year in progress. Rs thousands."""
    fye, last, _, bank, sales, margin, growth, _, _ = PLAN[sym]
    out = {}
    for back in range(5, -2, -1):
        y = last - back
        s = int(round(sales / (1 + growth / 100.0) ** back))
        if back == -1:
            s = int(round(sales * (1 + growth / 100.0)))
        m = margin * (1 + 0.04 * ((y * 7) % 5 - 2))
        income = int(round(s * 0.52)) if bank else None
        pat = int(round((income if bank else s) * m / 100.0 * (1 if not bank else 1.9)))
        out[y] = (s, income, pat)
    return out


def quarter(sym, fy, q):
    """(sales, total income, profit) of quarter q (1 to 4) of fiscal year fy, standing alone."""
    s, inc, pat = years(sym)[fy]
    w = SEASON[q - 1]
    if q < 4:
        return (int(round(s * w)), int(round(inc * w)) if inc else None, int(round(pat * w)))
    a = [quarter(sym, fy, k) for k in (1, 2, 3)]
    return (s - sum(x[0] for x in a), (inc - sum(x[1] for x in a)) if inc else None, pat - sum(x[2] for x in a))


def shown_quarters(sym):
    """[(label, fy, q)] the page shows: the three newest (no Q4 alone) and the same quarter a
    year back. FFC's newest has the wrong label the real page shows ("Q1 2027")."""
    fye, last, out_after, _, _, _, _, _, wrong = PLAN[sym]
    done = [(last + 1, q) for q in range(out_after, 0, -1)] + [(last, 3), (last, 2), (last, 1)]
    three = done[:3]
    back = (three[0][0] - 1, three[0][1])
    out = [("Q%d %d" % (q, fy), fy, q) for fy, q in three + [back]]
    if wrong:
        out[0] = (wrong,) + out[0][1:]
    return out


def ttm_eps(sym):
    """EPS of the last four quarters (Q4 counted as the year minus its first three)."""
    fye, last, out_after, _, _, _, _, _, _ = PLAN[sym]
    qs = [(last + 1, q) for q in range(out_after, 0, -1)] + [(last, 4), (last, 3), (last, 2), (last, 1)]
    return eps(sum(quarter(sym, fy, q)[2] for fy, q in qs[:4]), shares_of(sym))


def tables(sym, price):
    """(annual, quarterly, ratios) as {"columns": [...], "rows": [(label, [numbers])]}."""
    fye, last, _, bank, _, _, _, gross, _ = PLAN[sym]
    yrs = years(sym)
    sh = shares_of(sym)
    cols = [last - k for k in range(4)]

    def rows_for(vals):
        first = [("Mark-up Earned", [v[0] for v in vals]), ("Total Income", [v[1] for v in vals])] if bank \
            else [("Sales", [v[0] for v in vals])]
        return first + [("Profit after Taxation", [v[2] for v in vals]), ("EPS", [eps(v[2], sh) for v in vals])]

    annual = {"columns": [str(y) for y in cols], "rows": rows_for([yrs[y] for y in cols])}
    shown = shown_quarters(sym)
    quarterly = {"columns": [s[0] for s in shown], "rows": rows_for([quarter(sym, fy, q) for _, fy, q in shown])}
    e = dict((y, eps(yrs[y][2], sh)) for y in yrs)
    ratios = []
    if gross is not None:
        ratios.append(("Gross Profit Margin (%)", [round(gross + 0.7 * ((y * 3) % 4 - 1.5), 2) for y in cols]))
    ratios.append(("Net Profit Margin (%)", [round(yrs[y][2] * 100.0 / yrs[y][0], 2) for y in cols]))
    growth = [round((e[y] / e[y - 1] - 1) * 100, 2) for y in cols]
    ratios.append(("EPS Growth (%)", growth))
    ratios.append(("PEG", [round((price / e[y]) / g, 2) if g else 0.0 for y, g in zip(cols, growth)]))
    return annual, quarterly, {"columns": [str(y) for y in cols], "rows": ratios}
