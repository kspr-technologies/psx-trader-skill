"""Write the PSX daily files: mkt_summary/*.Z, the old gzip file and symbol_name/*.zip."""

import datetime
import os

from .universe import FUTURES, ORDINARY, OTHERS, XD_EVENTS
from .util import fmt_old, fmt_open, fmt_px, gzip_one, psx_date, unix, write_bytes, zip_one

ISO = datetime.date.fromisoformat


# ---------------------------------------------------------------- closing file

def xd_on(sym, d):
    """True when sym (or a future's underlying) shows XD on date d."""
    base = sym.split("-")[0]
    if base not in XD_EVENTS:
        return False
    ex, _, until = XD_EVENTS[base]
    return ISO(ex) <= d <= ISO(until)


def sort_key(sym):
    """PSX sorts both files by the symbol with the hyphen left out (ASL, ASLCPS, ASL-DEC)."""
    return sym.replace("-", "")


def closing_text(d, rows):
    """The closing11.lis text of day d: 13 fields, CRLF, PSX number styles."""
    lines = []
    for sym in sorted(rows, key=sort_key):
        r = rows[sym]
        short = r["short"] + ("XD" if xd_on(sym, d) else "")
        f = [psx_date(d), sym, r["sector"], short, fmt_open(r["open"]), fmt_px(r["high"]), fmt_px(r["low"]),
             fmt_px(r["close"]), str(int(r["volume"])), fmt_px(r["ldcp"]), "", "", ""]
        lines.append("|".join(f) + "\r\n")
    return "".join(lines)


def write_closing(out, market, days):
    """mkt_summary/YYYY-MM-DD.Z for each day: a zip with closing11.lis, as PSX serves it."""
    paths = []
    for d in days:
        rows = dict((s, by[d]) for s, by in market.items() if d in by)
        text = closing_text(d, rows).encode("latin-1")
        when = (d.year, d.month, d.day, 15, 58, 8)
        path = os.path.join(out, "mkt_summary", d.isoformat() + ".Z")
        write_bytes(path, zip_one("closing11.lis", text, when))
        paths.append(path)
    return paths


# ---------------------------------------------------------------- the old gzip file (before 2019)

OLD_ROWS = [   # 2 Jan 2018, made-up numbers, grouped by sector as the old files are
    ("FEM", "0819", "Equity Modaraba", 3.1, 3.2, 3.05, 3.15, 12500, 3.08),
    ("HBL", "0807", "Habib Bank", 186.0, 188.5, 184.1, 187.62, 2154000, 185.4),
    ("MCB", "0807", "MCB Bank Ltd", 210.5, 212.0, 209.0, 211.3, 806500, 210.5),
    ("FFC", "0809", "Fauji Fert", 77.0, 78.4, 76.6, 78.15, 1903500, 76.9),
    ("LUCK", "0804", "Lucky Cement", 552.0, 559.9, 548.0, 556.71, 433200, 551.25),
    ("HUBC", "0824", "Hub Power Co.", 97.5, 98.9, 97.0, 98.42, 3216000, 97.36),
    ("OGDC", "0820", "Oil & Gas Dev", 160.0, 162.25, 159.4, 161.88, 4870500, 159.73),
    ("SYS", "0828", "Systems Limited", 66.0, 67.0, 65.5, 66.4, 23500, 65.9),
    ("OGDC-CJAN", "40", "OGDC-CJAN", 0, 163.1, 0, 163.1, 0, 161.0),
]


def write_old(out):
    """mkt_summary_old/2018-01-02.Z in the old gzip style (whole numbers have no point)."""
    d = datetime.date(2018, 1, 2)
    lines = []
    for sym, sector, short, op, hi, lo, cl, vol, ldcp in OLD_ROWS:
        f = [psx_date(d), sym, sector, short, fmt_old(op), fmt_old(hi), fmt_old(lo), fmt_old(cl), str(vol),
             fmt_old(ldcp), "", "", ""]
        lines.append("|".join(f) + "\r\n")
    mtime = unix(datetime.datetime(2018, 1, 2, 11, 46, 23))
    path = os.path.join(out, "mkt_summary_old", "2018-01-02.Z")
    write_bytes(path, gzip_one("closing11.lis", "".join(lines).encode("latin-1"), mtime))
    return path


# ---------------------------------------------------------------- symbol name file

def symbol_entries(d):
    """(symbol, short name, full name) of every symbol in the symbol name file. Like the
    real file it has the stock futures but no debt and no index futures."""
    full = dict((r[0], (r[2], r[3])) for r in ORDINARY)
    full.update((r[0], (r[2], r[3])) for r in OTHERS)
    out = []
    for sym, (short, name) in full.items():
        out.append((sym, short + ("XD" if xd_on(sym, d) else ""), name))
    for sym, sector, under, _, _ in FUTURES:
        if sector == "40":
            short, name = full[under]
            out.append((sym, short + ("XD" if xd_on(sym, d) else ""), name))
    return sorted(out, key=lambda e: sort_key(e[0]))


def symbol_bytes(entries):
    """symbolname.lis: UTF-16BE with a BOM before every piece (symbol, pipe, short name,
    pipe, full name with its pipe, line end) and a last line that is one lone pipe."""
    bom = b"\xfe\xff"
    parts = []
    for sym, short, name in entries:
        for piece in (sym, "|", short, "|", name + "|", "\r\n"):
            parts.append(bom + piece.encode("utf-16-be"))
    parts.append(bom + "|".encode("utf-16-be"))
    return b"".join(parts)


def write_symbols(out, d):
    """symbol_name/YYYY-MM-DD.zip with symbolname.lis."""
    data = symbol_bytes(symbol_entries(d))
    path = os.path.join(out, "symbol_name", d.isoformat() + ".zip")
    write_bytes(path, zip_one("symbolname.lis", data, (d.year, d.month, d.day, 15, 57, 36)))
    return path
