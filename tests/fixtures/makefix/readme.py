"""The README.md of the sample market (easy English)."""

import os

from .util import write_text

TEXT = """# The sample PSX market (made up)

All data in this folder is made up. It is not real PSX data.

## Why it is made up

- PSX does not allow sharing its market data without a licence.
- PSX also does not allow tools to copy its website.
- So the tests use made-up data in the real PSX file formats.

## What is real and what is not

- Real: the file formats, the PSX calendar, symbols and company names.
- Made up: every price, volume, share count, result, notice and news item.
- Made up: every person named in a notice or on a company page.
- DEMO ("Demo Industries Limited") is a made-up company. DEMO is not a PSX symbol.

## How to rebuild it

```
cd tests/fixtures
python3 make_fixtures.py
```

The script uses the Python standard library only.
It gives the same files every time.

## What is here

| path | what |
| --- | --- |
| `mkt_summary/YYYY-MM-DD.Z` | daily closing files, 70 sessions, 2026-07-22 to 2026-10-29 |
| `mkt_summary_old/2018-01-02.Z` | one old closing file (gzip, before 2019) |
| `symbol_name/2026-10-29.zip` | the symbol name file (UTF-16) |
| `screener.html`, `indices.html` | saved PSX pages, after the close of 2026-10-29 |
| `company/<SYM>.html` | saved company pages: DEMO, OGDC, HBL, FFC, SYS |
| `tape/2026-10-29/` | snapshots: `market.jsonl` (screener) and `DEMO.jsonl` (company page) |
| `bars/DEMO.csv`, `bars/DEMO-1m.csv` | DEMO bars, like a broker export |
| `docs/<id>.txt` | notice letters, as `pdftotext -layout` text |
| `news/*.xml` | RSS feeds in the layout of four Pakistani news sites |

## The example day

- Thursday 2026-10-29. DEMO: last close (LDCP) 98.40, limits 88.56 to 108.24.
- DEMO's day: open 99.20, high 102.35, low 98.90, close 101.60, volume 4,236,500.
- Opening range (09:30 to 09:40 bars): 98.90 to 100.20.
- The 10:10 bar closes at 100.65: the first close above the range (10:15 AM, bar 8).
"""


def write(out):
    """Write README.md. -> its path."""
    p = os.path.join(out, "README.md")
    write_text(p, TEXT)
    return p
