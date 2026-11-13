#!/usr/bin/env python3
"""Rebuild the made-up sample PSX market in psx-market/. Standard library only, Python 3.8.

    cd tests/fixtures && python3 make_fixtures.py

The output is the same on every run (seeded random numbers, fixed file times). The real
PSX formats are copied; the numbers are not (PSX bans sharing its data). The parts live
in makefix/: daily files, symbol names, pages, company pages, tapes, bars, docs, feeds.
"""

import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from makefix import bars, company2, daily, dailyio, docs, feeds, intraday, pages, readme, tape, util  # noqa: E402

OUT = os.path.join(HERE, "psx-market")
PARTS = ("mkt_summary", "mkt_summary_old", "symbol_name", "company", "tape", "bars", "docs", "news")


def main():
    """Write every file of the sample market."""
    for part in PARTS:
        shutil.rmtree(os.path.join(OUT, part), ignore_errors=True)
    days = util.sessions()
    market = daily.build(days[:-1])
    paths = intraday.build_day(market)
    files = dailyio.write_closing(OUT, market, days)
    files.append(dailyio.write_old(OUT))
    files.append(dailyio.write_symbols(OUT, days[-1]))
    files += pages.write_pages(OUT, market)
    files += company2.write_companies(OUT, market)
    files += tape.write_tapes(OUT, paths, market)
    files += bars.write_bars(OUT, market, days[-31:-1])
    files += list(docs.write_docs(OUT, market).values())
    files += feeds.write_feeds(OUT)
    files.append(readme.write(OUT))
    print("wrote %d files to %s" % (len(files), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
