"""Small tools of the sample-market generator: dates, numbers, files. Standard library only."""

import datetime
import gzip
import io
import json
import os
import random
import struct
import zipfile

from .common import price_limits, slots, trading_day

FIRST = datetime.date(2026, 7, 22)   # first session in the sample market
DAY = datetime.date(2026, 10, 29)    # the example day (Thursday)
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEK = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---------------------------------------------------------------- dates

def sessions(first=FIRST, last=DAY):
    """Trading days from first to last (both in), by the PSX calendar."""
    out, d = [], first
    while d <= last:
        if trading_day(d)[0]:
            out.append(d)
        d += datetime.timedelta(days=1)
    return out


def psx_date(d):
    """date -> "29OCT2026" (the closing file's date field)."""
    return "%02d%s%d" % (d.day, MONTHS[d.month - 1], d.year)


def page_day(d):
    """date -> "Oct 29, 2026" (company page announcement dates: no leading zero)."""
    return "%s %d, %d" % (MON[d.month - 1], d.day, d.year)


def page_clock(dt, weekday=True):
    """datetime -> "Thu, Oct 29, 2026 3:49 PM" (or without the weekday)."""
    h = dt.hour % 12 or 12
    s = "%s %d, %d %d:%02d %s" % (MON[dt.month - 1], dt.day, dt.year, h, dt.minute, "AM" if dt.hour < 12 else "PM")
    return ("%s, %s" % (WEEK[dt.weekday()], s)) if weekday else s


def at(d, hhmm):
    """date and "HH:MM" -> naive datetime."""
    h, m = hhmm.split(":")
    return datetime.datetime(d.year, d.month, d.day, int(h), int(m))


def minutes_between(a, b):
    """Every minute from datetime a up to b (b not included)."""
    out, t = [], a
    while t < b:
        out.append(t)
        t += datetime.timedelta(minutes=1)
    return out


def day_slots(d):
    """The 5-minute slot starts of day d (the canonical grid)."""
    return slots(d)


# ---------------------------------------------------------------- numbers

def r2(x):
    """Round to 0.01 the way prices are kept here (half up, no float noise)."""
    return float("%.2f" % (float(x) + (1e-9 if x >= 0 else -1e-9)))


def fmt_open(x):
    """OPEN in a closing file: always a decimal point (870.0, 65.1, 23.53)."""
    return repr(r2(x))


def fmt_px(x):
    """HIGH, LOW, CLOSE, LDCP in a closing file: no ".0" on a whole number (870, 945.8)."""
    v = r2(x)
    return str(int(v)) if v == int(v) else repr(v)


def fmt_old(x):
    """Numbers in a 2018 gzip closing file: whole numbers have no decimal point."""
    return fmt_px(x)


def comma(x, dec=2):
    """1234.5 -> "1,234.50"; dec=0 -> "1,235"."""
    return format(round(float(x), dec), ",.%df" % dec)


def short_money(x):
    """Screener short numbers: 399.5M, 79.7B, 1.3T, 499.1K, 0.0."""
    v = float(x)
    for unit, size in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= size:
            return "%.1f%s" % (v / size, unit)
    return "%.1f" % v


def within_limits(ldcp, *prices):
    """True when every price is inside price_limits(ldcp) (0 means no trade)."""
    lo, hi = price_limits(ldcp)
    return all(p == 0 or lo <= p <= hi for p in prices)


def clamp_limits(ldcp, p):
    """A price pulled inside the day's limits."""
    lo, hi = price_limits(ldcp)
    return min(max(r2(p), lo), hi)


def rng(*key):
    """A seeded random generator from any key (same key, same numbers)."""
    seed = 0
    for ch in "|".join(str(k) for k in key):
        seed = (seed * 131 + ord(ch)) % 2147483647
    return random.Random(seed)


def split_int(total, weights):
    """Split a whole number by weights; the parts add up to the total exactly."""
    s = float(sum(weights))
    parts = [int(total * w / s) for w in weights]
    parts[-1] += total - sum(parts)
    return parts


# ---------------------------------------------------------------- files

def write_bytes(path, data):
    """Write bytes, making the folders."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def write_text(path, text):
    """Write UTF-8 text with the line ends as given."""
    write_bytes(path, text.encode("utf-8"))


def zip_one(member, data, when):
    """A zip with one deflated member, as PSX serves it (MS-DOS made, no extras)."""
    buf = io.BytesIO()
    info = zipfile.ZipInfo(member, date_time=when)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 0
    info.external_attr = 0
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(info, data)
    return buf.getvalue()


def gzip_one(member, data, mtime):
    """A gzip file with the member's name in it (the pre-2019 PSX style, OS byte 11)."""
    buf = io.BytesIO()
    with gzip.GzipFile(filename=member, mode="wb", fileobj=buf, mtime=mtime, compresslevel=6) as g:
        g.write(data)
    raw = bytearray(buf.getvalue())
    raw[9] = 11
    return bytes(raw)


def jsonl(snaps):
    """Tape lines: one JSON object a line, keys sorted (as append_tape writes them)."""
    return "".join(json.dumps(s, sort_keys=True) + "\n" for s in snaps)


def unix(dt_utc):
    """naive UTC datetime -> Unix seconds."""
    return int((dt_utc - datetime.datetime(1970, 1, 1)).total_seconds())


def le32(n):
    """4 bytes little endian (for checks of gzip headers)."""
    return struct.pack("<I", n)
