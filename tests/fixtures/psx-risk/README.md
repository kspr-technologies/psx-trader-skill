# The psx-risk test files (made up)

All data here is made up. It is not real PSX data.
DEMO ("Demo Industries Limited") is a made-up company. DEMO is not a PSX symbol.
The other symbols are used only as names. Their numbers are made up too.

Tests read these files. They never change them.
They are small JSON files written by hand, so there is no generator.

| file | what it is |
| --- | --- |
| `config.json` | the example config of psx-budget (day style) |
| `config-swing.json` | the same, with `"style": "swing"` (the swing example in the skill page) |
| `orders-demo.json` | the DEMO day buy of Thu 2026-10-29 (psx-budget) |
| `orders-flatten.json` | the 3:20 PM sell that closes it (psx-broker flatten) |
| `portfolio-filled.json` | the account after the paper fill of that buy |
| `broker-placed.json` | psx-broker's log of that buy: the yes, placed, filled |
| `prices-1052.json`, `prices-1520.json` | two quotes of DEMO (psx-alerts) |
| `universe.txt` | the list of ordinary shares (psx-scanner) |

The swing tests also read `../psx-v2/contract/` (the shared v2 file shapes).
