# The psx-v2 test files (made up)

All data in this folder is made up. It is not real PSX data.
DEMO ("Demo Industries Limited") is a made-up company. DEMO is not a PSX symbol.

## How to rebuild it

```
cd tests/fixtures/psx-v2
python3 make.py
```

- It uses the Python standard library and `../makefix` only.
- It gives the same files every time.
- Tests read these files. They never change them.

## paths/: closing files for a swing trade

The swing example: buy DEMO on Thu 2026-10-29 at 99.38.
The stop is 96.25 and the target is 105.64. The trend rule looks at 10 sessions.
The exit-by day is Fri 2026-11-27, at 4:20 PM.

- Each path has `mkt_summary/YYYY-MM-DD.Z` files with DEMO rows only.
- They are the 20 PSX sessions from Fri 2026-10-30 to Fri 2026-11-27.
- Mon 2026-11-09 is Iqbal Day, so there is no file for it.
- Each LDCP is the close before. The bonus day is the one exception.
- Every price is inside that day's price limits.
- To use a path: copy `../psx-market` to a temp folder. Then add the path's files.

| path | what it shows |
| --- | --- |
| `up` | It rises. Wed 2026-11-04 has the high 105.90: the first time it reaches the target. |
| `gap` | Fri 10-30 closes at 97.10. Mon 11-02 opens at 95.40, under the stop: a gap. Its low is 94.80 and its close 95.60. |
| `trend` | It gets to 104.00 on 11-12. Every close from 11-11 to 11-24 is 101.20 or more. Wed 11-25 closes at 100.50, under 101.20: the trend rule. The high never reaches 105.64. |
| `flat` | Small steps up. No stop, no target and no trend break. The time stop ends it on Fri 11-27. |
| `bonus` | The same as `up` to Wed 11-11. From Thu 11-12 every price is the `up` price / 1.2. The LDCP of 11-12 is 89.70 (107.64 / 1.2). The short name shows XB from 11-12 to 11-18: a made-up 20% bonus, factor 0.8333. |

## contract/: the shared file shapes

| file | what it is |
| --- | --- |
| `plan-swing.json` | psx/plan.json of the swing example (psx-exit) |
| `orders-swing.json` | psx/orders.json with the swing buy: 1,436 DEMO at 99.38 (psx-budget) |
| `portfolio-swing.json` | psx/portfolio.json after a paper fill at 99.38, with the swing plan block |
| `orders-protect.json` | psx/orders.json with the stop-limit sell: trigger 96.25, limit 95.28 (psx-broker protect, live) |
| `portfolio-real.json` | a long-term psx/portfolio.json with `"real_holdings": true` and `"as_of"` |
| `orders-invest.json` | psx/orders.json from psx-invest: a TRIM sell of LUCK and an ADD buy of DEMO, with no stops |
| `broker-placed-unfilled.json` | psx/state/broker.json: a live buy placed on Thu 2026-10-29, with no fill |
| `broker-expired.json` | the same, then "expired" on Fri 2026-10-30 |
| `broker-approved-old.json` | a buy approved on Thu 2026-10-29 and never placed |
| `broker-protect-open.json` | a live protect sell of 143 DEMO, placed on Fri 2026-10-30 at 9:36 AM, with no fill |
| `broker-protect-cancelled.json` | the same, then "cancelled" |
| `clock-keys.json` | the keys of `alerts.py clock --json`, and what it says on four days |

- The fingerprints in the broker files are the real ones of the orders above.
- The "plain" words of the protect sell are made up. psx-broker's own words may differ.
- orders-invest.json was made on Thu 2026-10-29 at 10:20 AM, so it fits the sample market day.
