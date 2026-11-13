# The psx-invest test market (made up)

All data here is made up. It is not real PSX data.
The Z symbols are not PSX symbols. They were checked once against a real PSX symbol list (not shipped).

## The files

| file | what |
| --- | --- |
| `make.py` | writes the made-up market: `python3 make.py OUT` makes `OUT/mkt_summary/YYYY-MM-DD.Z` |
| `config.json` | a long-term `psx/config.json`: the pack's example with the 5 long-term values (section 3.8) |
| `invest.json` | `psx/invest.json`: a copy of `psx-invest/invest.example.json` |
| `portfolio.json` | `psx/portfolio.json`: 12 made-up holdings, Rs 50,000 cash, `"as_of": "2026-10-01"` |
| `metrics.json` | `psx/metrics.json` in psx-analyst's shape, for 5 of the shares |

## How the market is made

- `make.py` uses the Python standard library and the helpers in `../makefix`.
- It gives the same files every time.
- 500 closing files: the PSX sessions from Thu 2024-11-14 to Fri 2026-10-30 by the calendar.
  Every weekday before 2026 counts as a session.
- One session has no file: Mon 2026-09-14.
- The tests write it once into a temp folder. So the 500 files do not ship.

## What each share shows (on Fri 2026-10-30)

| symbol | story | signal |
| --- | --- | --- |
| ZUP | a steady rise; a cash payout of Rs 14.50 on Mon 2026-08-10 (XD) | ADD: it gets the new money |
| ZCAP | a big rise to 27% of the money | TRIM back to 20% (778 shares) |
| ZBRK | up to June, then down: both votes down at Oct 30, one at Sep 30 | TRIM half once the watch began before Oct 30, else REVIEW |
| ZOLD | down since July: both votes down from Fri 2026-07-31 | REVIEW; `ack ZOLD --keep` gives HOLD |
| ZSPL | a 2-for-1 split with no flag on Thu 2026-07-02, after 3 sessions with no trade | HOLD: no false break, the split is noted |
| ZBON | a 25% bonus (XB, factor 0.8) on Mon 2026-10-12, after the `"as_of"` date | REVIEW (data); `ack ZBON --checked` gives ADD back |
| ZGAP | a +6% LDCP jump next to the missing file of Mon 2026-09-14 | REVIEW (data) |
| ZNEW | listed on Mon 2026-05-04: 6 month-ends | HOLD: the trend is not known |
| ZRES | profit -40.1% in `metrics.json` | REVIEW (profit) |
| ZSTL | no trade from Mon 2026-10-05: 20 sessions | REVIEW (data) |
| ZTHN | a +0.95% LDCP move with no flag on Mon 2026-08-24, after 2 days with no trade | HOLD: not scaled, not a split |
| ZRIT | ex-right (XR) on Thu 2026-10-15; the right letter ZRITR trades from Fri 2026-10-23 | REVIEW (rights and data) |

Also in the files: ZRITR (the right letter) and ZUP-NOV (a stock future, sector 40). psx-invest skips the future.
In `metrics.json`: ZRES at -40.1%, ZSPL with a quarter label that looks wrong, ZUP with EPS down 60% but profit
up (EPS is never used), and ZBRK with a board meeting on Thu 2026-11-12.
