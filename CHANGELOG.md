# Changelog

What changed in each version. The newest version is at the top.

## 2.0.0

Three new parts, a blocked list and more safety guards. Day style works as
before. If you never set a style, nothing changes.

### Swing style

- A swing trade is held for days or weeks: up to 20 sessions.
- Turn it on with `"style": "swing"` in `psx/config.json`. Day style is still
  the default.
- Each swing trade has four exits: the stop, the target, the time stop and the
  trend break. Each one is a plain rule.
- psx-exit plans the buy from the daily scan, so psx-entry is not used.
- psx-timemachine replays the next 20 sessions, gaps included.
- psx-budget and psx-risk show the gap risk of a share held overnight, and
  cap it.
- psx-risk adds limits for the whole account:
  - at most 3 open swing positions;
  - a cap on the open risk;
  - one position a stock.
- psx-broker makes a stop-limit sell ticket for your broker app. It can also
  move a stop up, record bonus shares and record a buy that did not fill.
- psx-alerts checks the stop, the target, the trend and the time rules on
  every closing file, once each. It catches up after the Mac slept.

### psx-invest: long-term signals

- A new skill for the shares you keep for years, in a folder of their own.
- Each share gets one signal: ADD, HOLD, TRIM or REVIEW.
- A signal says which rules are met. It is not a forecast. You decide.
- It never uses what you paid, and it never prints a profit.
- It shows how new money could be split. Its orders go through psx-risk and
  your yes, like every order.

### psx-autorun: auto-run on a Mac

- A new skill. After a one-time install, your Mac runs the checks on a timer,
  with no Claude window open.
- It runs:
  - a morning check;
  - price checks every 5 minutes while PSX is open;
  - a check after the close;
  - a week report on Saturday.
- It sends Mac messages and writes a short report.
- Every time comes from the pack's PSX clock, so Fridays, Ramadan and holidays
  count.

### The blocked list

- `"blocked"` in `psx/config.json` lists the shares you never want to trade in
  that folder. For example, the shares you keep for the long term.
- The scan leaves them out, and says so in one line.
- psx-broker refuses to record a buy of one, unless you add `--blocked-ok`.
  psx-budget and psx-risk already refused such a buy.
- In the long-term folder, the list means: no new money for that share. A sell
  stays possible.

### Safety guards

- Nothing places an order. That has not changed.
- Auto-run never runs psx-broker, psx-budget or psx-risk. It never runs
  `invest.py orders`, `cash`, `ack` or `backfill`, or `alerts.py add` or
  `reset`. The code refuses them, whatever the settings file says.
- Real shares are guarded. With paper off, the first fill puts
  `"real_holdings": true` at the top of `psx/portfolio.json`. Then a paper
  fill can never change that file.
- A paper fill is also refused when a file with no such mark shows a sign of
  real shares. Then nothing is written.
- With paper off, a yes must use the computer's own clock. `--now` and
  `--today` are for replays, tests and paper.
- psx-invest writes nothing in a folder with no `psx/invest.json`. So it never
  writes in the trading folder by mistake.
- A shared word test checks the new tool output. It allows no advice words.
  A line that offers an action must end with "You decide."

## 1.0.0

The PSX version of the Day Trader skill pack by Jake Schincariol.

- 11 skills for day trading on the Pakistan Stock Exchange: psx-scanner,
  psx-news, psx-entry, psx-exit, psx-timemachine, psx-risk, psx-budget,
  psx-broker, psx-alerts, psx-analyst and psx-insiders.
- PSX hours: Monday to Thursday, the Friday Juma break, Ramadan hours and PSX
  holidays. All times are Karachi time.
- PSX rules: the daily price limits, market halts, no short selling, and
  settlement on T+1.
- Money in rupees. The broker's costs count in every loss: commission, sales
  tax and fees.
- PSX data from files you save yourself: the daily closing files, company
  pages and the screener. The PSX site is off by default.
- News from Pakistani business news feeds and from PSX company notices.
- psx-insiders reads the share-trade notices that directors and big holders
  file with PSX.
- Paper trading by default. Nothing places an order. You place it yourself in
  your broker's app, after your yes.
- Made-up test data only.
