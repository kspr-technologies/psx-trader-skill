---
name: psx-entry
description: >-
  Entry Timer for the Pakistan Stock Exchange (PSX). Watches a setup bar by bar
  and tells you the moment to get in. Reads 5-minute (or 1-minute) bars from a
  file you save from your broker, or from a tape of PSX company pages. Builds
  the opening range (the first 15 minutes) and calls the entry on the first
  closed bar that CLOSES above the range high, or above a level you give it
  (like a 20-day high from the scanner), with an optional volume check. Knows
  PSX hours, the Friday Juma break, Ramadan and holidays. Says waiting and how
  far, triggered (time and price), expired, or not open yet, and writes
  psx/entry.json for /psx-exit. Long side only. It never places an order. Use
  when the user says "tell me when to get in", "watch OGDC", "has it broken
  out", "did it break the opening range", "entry signal", "time my entry",
  "check my watchlist", or after /psx-scanner and /psx-news, before /psx-exit.
  Day trades only: in swing style, skip it (psx-exit plans the buy from the
  daily scan).
argument-hint: "[SYMBOL] [--setup orb|level] [--interval 5m|1m]"
---

# psx-entry

The Entry Timer turns a setup into one moment: the bar that confirms it.
It reads bars and waits for each one to finish.

- A price that pokes above the high in the middle of a bar is not an entry.
- A bar that CLOSES above it is.
- Then it says the time and the price, writes them down for the Exit Planner, and stops.

It never buys anything. Day trading loses most people money, and a clean signal does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check DEMO                  # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check DEMO --write          # also save psx/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check DEMO --setup level --level 96.56
python3 "${CLAUDE_SKILL_DIR}/entry.py" check DEMO --bars psx/data/bars/DEMO-1m.csv --interval 1m --min-volume-x 0.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch DEMO --until 15:00    # polls PSX, one line per closed bar
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                   # every hit in psx/watchlist.json
```

Other flags:

- `--or-minutes N` (default 15) and `--json`.
- `--data DIR`: the data folder (default `psx/data`).
- `--bars FILE`: a bars CSV from your broker, or a tape (`.jsonl`). It is read first. Give the path to the file, like `psx/data/bars/DEMO-1m.csv`.
- `--now "2026-10-29 10:15"`: replay a day at that Karachi time. A tape's time is the page's "As of" time, so `--now` is a data time.
- `--offline`: never touch the network. `watch` with `--now` or `--offline` replays the saved data bar by bar on a simulated clock, without waiting.
- `--every 60` (watch): seconds between two polls of the PSX site. 30 or more.
- `--config` (default `psx/config.json`).

## Swing style

Swing style: skip this step. psx-exit plans the buy from the daily scan.

- Swing style is `"style": "swing"` in psx/config.json. A swing buy is a limit buy at the last close plus 1%, planned the evening before from the closing files.
- So it needs no bars and no opening range. Go from `/psx-scanner` (after the close) straight to `/psx-exit`: `exit.py plan --symbol SYMBOL --write`.
- The Entry Timer is for day trades.

## What it reads and writes

| file | what |
| --- | --- |
| `--bars FILE` | a bars CSV from your broker (1-minute or 5-minute rows), or a tape. Read first |
| `psx/data/bars/<SYM>.csv` | bars exported from your broker terminal: bar start times, Karachi time |
| `psx/data/tape/<date>/<SYM>.jsonl` | company page snapshots. `watch` records them, one a minute |
| `psx/data/tape/<date>/market.jsonl` | screener snapshots from `/psx-scanner`: prices only, no volume |
| `psx/watchlist.json` | the scanner's hits (`hits[].symbol`, `hits[].level`), for `watchlist` |
| `psx/config.json` | `no_entries_before` (09:45), `no_entries_after` (15:00), `psx_site`, `data_delay_minutes` (5), `holidays`, `open_days`, `ramadan`. Optional |
| PSX company page | only for `watch`, only with `"psx_site": true`: one plain GET a minute |
| `psx/entry.json` | with `check --write`, or when `watch` triggers: symbol, setup, status, time, bar, price, level, opening range, session, source |

- The first source that has bars for the day wins. The first line of the output names it.
- PSX has no free intraday bars. So bars come from your broker's file, or from a tape.
- Turn on `"psx_site": true` only if PSX has given you permission (marketdatarequest@psx.com.pk). The PSX site terms ban automated use without it.

## The rule

- **Opening range (orb).** The high and low of the first 15 minutes of the first session.
  - Monday to Thursday: the 9:30, 9:35 and 9:40 bars.
  - Friday and Ramadan: the 9:15, 9:20 and 9:25 bars.
  - The level to beat is the range high.
- **Level.** A price you give, like a 20-day high from `/psx-scanner`.
- **The trigger** is the first closed bar that CLOSES above the level.
  - orb: the first closed bar after the range.
  - level: the first closed bar that ends at or after `no_entries_before`. It can be a bar of the range.
  - A wick above is not a trigger.
  - A close exactly at the level is not above it.
  - A bar that closes when a session ends (12:00 on a Friday, or the close) is not a trigger. An order cannot fill then. The next chance is the next session, at a new price.
- **Part of the range missing.** A tape that starts late, or a missing bar, gives only part of the range. Then there is no trigger: it says no data.
  - One case works: a company tape that started inside the range. Its first page shows the day's high and low, so the range uses them.
  - `psx/entry.json` then says how many range bars it saw (`"bars": 1, "of": 3`). `/psx-exit` does not put an or-mid or or-low stop on a part range.
- **The time** is that bar's close time: the 10:10 bar closes at 10:15 AM. The price is its close.
- **Bar numbers** count the day's 5-minute slots, across both Friday sessions. Bar 8 is the 10:10 bar Monday to Thursday. Friday 14:30 is bar 33.
- **Only closed bars count.** A bar counts once both are true:
  - its end is at or before the clock, and
  - the data has moved past its end.
- **Free PSX data is about 5 minutes late.** The 10:15 close shows on the PSX page at about 10:20.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger bar's volume at 1.5 times the range bars' average.
  - The first bar holds the opening match, so even 0.5 is strict.
  - A screener tape has no volume. Use a bars file or the company tape.
- **Time limits.** No entries before 09:45 or after 15:00 (config). These are Monday to Thursday times. `config_time` moves them for other hours:
  - Friday: 09:30 and 16:00.
  - Ramadan, Monday to Thursday: 09:30 and 13:00.
  - The trigger bar must close by `no_entries_after`. If the clock is past it when the bar shows, the tool does not say "get in": psx-risk will block a new entry.
- **What it says.** Waiting (and how far), triggered, expired, or no data.
  - Before the open: not open yet. Before the range ends: range forming.
  - In the Friday Juma break (12:00 to 2:30 PM): the market is closed. A trigger in the second session is fine.
  - Weekends and PSX holidays: the market is closed.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after `/psx-scanner`. Check `/psx-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line: waiting and how far, triggered at what time and price, or expired.
3. To wait for it:
   - With `"psx_site": true`, Claude can run `entry.py watch SYMBOL --until 15:00` in the background. It polls the company page once a minute and saves each page to the tape. It prints one line per closed bar. It exits 0 on the trigger (it writes `psx/entry.json`) or 1 when the setup expires. It sleeps through the Juma break.
   - Or, with bars from your broker, save a fresh file and run `entry.py check SYMBOL --write` on a `/loop` every 5 minutes.
   - Keep the computer awake while it watches. A sleeping laptop pauses it. It catches up, late, when it wakes.
   - Run only one tool that polls the PSX site at a time (the scanner tape, an entry watch or an alerts loop).
   - Auto-run (`/psx-autorun`) sees a watch by itself. Then it reads no PSX page, so no pause is needed.
   - An entry watch saves no market prices, only the page of the share it watches. So with auto-run on, the trading folder's alerts get no new price. After 15 minutes auto-run sends a problem message: "the DEMO alerts had no new price for 15 minutes". While a watch runs, tell the user to check the stop in the broker app.
4. When it triggers, say the time and the price, and that nothing was placed. Say that free PSX data is about 5 minutes late. If the trigger is more than a bar old, say that too: a late entry is a different trade.
5. Hand off to `/psx-exit` to set the stop and the target BEFORE anything is bought.

## Example: DEMO on Thu 2026-10-29

DEMO is a made-up stock in the sample market (`tests/fixtures/psx-market`). Its opening range is 98.90 to 100.20.

At 10:12 the 10:10 bar is still open, so it waits:

```
$ entry.py check DEMO --data tests/fixtures/psx-market --bars tests/fixtures/psx-market/tape/2026-10-29/DEMO.jsonl --now "2026-10-29 10:12"
DEMO  Thu 2026-10-29 10:12 PKT  opening-range breakout (15-minute range) on 5-minute bars from the company tape (tape/2026-10-29/DEMO.jsonl), data to 10:12
Opening range 9:30-9:45: high 100.20, low 98.90 (3 of 3 bars).
WAITING  last close 100.05 (10:05 bar), 0.15 under the range high 100.20 (-0.15%).
The 10:10 bar is still forming (100.40 so far, closes 10:15 AM); it does not count until it closes.
```

At 10:15 the bar has closed:

```
$ entry.py check DEMO --data tests/fixtures/psx-market --bars tests/fixtures/psx-market/tape/2026-10-29/DEMO.jsonl --now "2026-10-29 10:15"
DEMO  Thu 2026-10-29 10:15 PKT  opening-range breakout (15-minute range) on 5-minute bars from the company tape (tape/2026-10-29/DEMO.jsonl), data to 10:15
Opening range 9:30-9:45: high 100.20, low 98.90 (3 of 3 bars).
TRIGGERED 10:15 AM at 100.65: the 10:10 bar (bar 8) closed above the range high 100.20 (+0.45%).
This is the moment to get in, if you take the trade. Nothing was placed.
Free PSX data is about 5 minutes late: check the price in your broker app before you buy.
Next: set the stop and target BEFORE you buy: /psx-exit (exit.py plan --from psx/entry.json).
```

- Without `--bars`, the sample market's first source is `bars/DEMO.csv`. It gives the same range and the same trigger. At 10:12 it shows no "so far" line: a 5-minute file has no price inside a bar.
- `psx/entry.json` holds `"status": "triggered"`, `"time": "10:15 AM"`, `"bar_start": "10:10"`, `"bar_index": 8`, `"price": 100.65`, `"level": 100.2`, `"opening_range": {"high": 100.2, "low": 98.9, "minutes": 15, "bars": 3, "of": 3}`, `"session": "normal"`.
- With `--min-volume-x 1` the 10:10 bar is too thin (0.30x the range's volume). No later bar is heavy enough, so the setup expires at 15:00: "no close above the range high 100.20 with enough volume (1.00x) by 15:00".

## Rules

- Bar by bar, on bars from your broker or a PSX tape. Free PSX data is about 5 minutes late. Say so when a minute matters.
- A signal is information, not an order, and not a promise. Nothing here promises winning trades or money.
- Longs only. PSX bans blank sales (selling shares you do not hold). If the user wants a short, say that.
- One trigger per setup per day: the first close above. Later closes do not reset it.
- Never call an entry early because a bar looks like it will close above.
- A close at today's upper price limit may not fill: PSX trades up to the limit, never above. The tool says so.
- Late data cannot show a market halt. In a halt PSX cancels all open orders.

## What it never does

- It never places an order and never sizes a position.
- It never sets the stop (that is `/psx-exit`) and never moves the level after the fact.
- It never counts a bar that has not closed.
- It never polls the PSX site unless `"psx_site": true` is in your config.
- It never runs `/psx-broker` from a background watch: nobody is there to say yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order. psx-broker shows the order and waits. It prints the line to
  type into the broker app only after psx-risk passed that exact order today and the user typed an
  explicit yes for that one order in the chat. The user then places it in their own broker's app.
  If the user ever adds a broker tool that can place orders, set it to "Needs approval"
  (Customize > Connectors > tool permissions), never "Always allow".
- Long only. No short selling (PSX bans blank sales), no margin, no futures and no options.
- Broker logins, passwords and PINs never go in the chat.
