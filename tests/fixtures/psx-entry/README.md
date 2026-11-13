# psx-entry test files (made up)

All data here is made up. It is not real PSX data.

| file | what |
| --- | --- |
| `DEMO-1m-wick.csv` | 1-minute DEMO bars of Wed 2026-10-28, 09:30 to 10:06, like a broker export |

What `DEMO-1m-wick.csv` holds:

- Opening range (09:30 to 09:44): high 100.50, low 99.00, 15 of 15 bars.
- 09:49 is missing: no row for that minute.
- 09:52 has a wick above the range high (high 100.70) but closes at 100.30.
- 09:57 closes AT the range high (100.50). That is not above it.
- 09:58 is the first close above (100.62, volume 15,000, about 1.5 times the range average).
- 10:01 closes at 100.80 on 31,000 shares (about 3 times the range average).

The other tests use the shared sample market in `tests/fixtures/psx-market/`.
