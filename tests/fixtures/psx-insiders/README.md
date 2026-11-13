# psx-insiders test files (made up)

Everything here is made up. It is not real PSX data.

- ACME ("Acme Textiles Limited") is a made-up company. ACME is not a PSX symbol.
- Every person, number and doc id is made up.
- Only the layouts copy real PSX notices, so the parser meets the real shapes.

| file | layout it copies |
| --- | --- |
| `own-one-row.txt` | a company's own table, one row, the date and "Buy" one space apart, an address footer |
| `own-five-sells.txt` | a company's own table, five sells by one person, posted late |
| `form29-styles.txt` | a Form-29 table with other date styles, names and roles over two lines, and one month-first date |
| `data/company/ACME.html` | a saved company page: an attachment-only notice, a section 110 notice, buy-back titles |
| `data/docs/288900.txt` | a board meeting notice with a closed period over two lines |
| `data/docs/290290.txt` | a section 110 notice (someone went over 10%) |

The shared sample market (DEMO, SYS and others) is in `../psx-market/`.
