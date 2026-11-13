# Fixtures for psx-exit (made up)

All data here is made up. It is not real PSX data.

| file | what |
| --- | --- |
| `entry.json` | the entry from EXAMPLE.md section 5: DEMO triggered at 10:15 AM, bar 8, 100.65 |
| `DEMO-friday.csv` | DEMO bars on Fri 2026-10-30, like a broker export: 09:15 to 11:55, then 14:30 to 14:45 |

- The Friday file has no bars in the Juma break (12:00 to 14:30).
- Its 14:30 bar is bar 33. It opens 0.50 above the 11:55 close (a gap after the break).
- The other tests use the shared sample market in `../psx-market/`.
