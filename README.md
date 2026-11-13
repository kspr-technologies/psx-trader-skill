# The PSX Day Trader skill

13 Claude skills for the Pakistan Stock Exchange: scan PSX charts for setups, track the news, plan day or swing trades, replay a trade 10,000 times before you risk a rupee, get rule-based signals for the shares you hold, and let your Mac run the checks for you.

- It is for people who invest or trade on PSX and use Claude Code.
- Free and MIT. No signup, no API key. Plain Python: nothing to pip install.
- It is the PSX version of Jake Schincariol's Day Trader skill ([Credits](#credits)).

**The safety promise**

- Nothing in this pack can place an order. It writes a ticket, and you place
  it yourself in your broker's app.
- No trade goes through until you say yes.
- Paper trading is the default.
- It is not financial advice. You can lose money. Read the
  [fine print](#fine-print).

## The thirteen

| command | job | what it does |
| --- | --- | --- |
| `/psx-scanner` | Chart Scanner | Scans every PSX share for setups: opening-range breakouts, flags, oversold bounces. |
| `/psx-news` | News Monitor | Reads PSX company notices and Pakistani business news on every stock you watch. |
| `/psx-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. For day trades. |
| `/psx-exit` | Exit Planner | Sets your stop and your target before you enter, inside the day's price limits. A swing plan adds its exit-by day and its trend rule. |
| `/psx-timemachine` | Time Machine | Replays a trade 10,000 times before you risk a rupee, and checks how the same rule did on past days. |
| `/psx-risk` | Risk Manager | Checks every trade against your limits and PSX's rules, and stops anything over them. |
| `/psx-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade, costs included. |
| `/psx-broker` | Order Ticket | Turns an approved order into a ticket you place in your broker's app. Paper by default, never without your yes. |
| `/psx-alerts` | Price Alerts | Rings when a price, a stop, a price limit or the opening bell hits. |
| `/psx-analyst` | Earnings Analyst | Reads the results on a PSX company page and sums up what changed. |
| `/psx-insiders` | Insider Tracker | Reads the share-trade notices that directors and big holders must file with PSX. |
| `/psx-invest` | Long-term Signals | Gives each share you hold for the long term a signal: ADD, HOLD, TRIM or REVIEW. Rules, not predictions. |
| `/psx-autorun` | Auto-run | Lets your Mac run the scans, alerts and signals on a timer, with no Claude window. It never places an order. |

## Data: read this first

PSX's website does not allow automated tools unless PSX gives you written
permission. PSX also does not allow sharing its market data without a licence.
So the pack works like this:

- **By default the tools read only files on your computer.** Put them in
  `psx/data/` in your project:
  - `mkt_summary/YYYY-MM-DD.Z`: the PSX daily closing files, one per session.
    Save them from the Downloads page on dps.psx.com.pk with your browser. The
    daily scan needs 30 or more (60 is best). The Time Machine's daily mode uses
    the last 60. The long-term signals use up to 500 (two years).
  - `company/SYMBOL.html`: a saved company page, for notices, results and the
    day's price limits.
  - `screener.html`: a saved screener page, for all prices at once.
  - `bars/SYMBOL.csv`: 5-minute or 1-minute bars you export from your broker's
    terminal (columns: datetime, open, high, low, close, volume, in Karachi time).
- **The PSX site is off until you turn it on.** If PSX has given you
  permission, set `"psx_site": true` in `psx/config.json`. Then the tools
  fetch fresh pages first, with plain, polite requests, at least 1.5 seconds
  apart. Run only one tool that polls the site at a time (the tape, an entry
  watch, an alerts loop or auto-run). Auto-run sees a tape or an entry watch
  by itself: see "One tool on the site at a time" in the fine print. A replay
  with `--now` never uses the site.
- **How to ask PSX.** Write to PSX's Market Data Team at
  marketdatarequest@psx.com.pk. It handles data licences and permission to
  use PSX data. Say which pages the tools read, how often, and that it is for
  your own use. Turn the site on only after PSX says yes in writing.
- **News feeds are always on.** Business Recorder, Dawn, Express Tribune and
  ProPakistani publish RSS feeds for readers. The tools read them for your
  own use.
- **PSX's free data is about 5 minutes late.** There is no free intraday
  history. The tools record their own tape of prices while the site is on,
  or read bars from your broker. Your broker's screen is the live one.

## Install

You need Claude Code and Python 3.8 or newer (check: `python3 --version`).
Nothing else: there is nothing to pip install. `pdftotext` (from poppler) is
optional: it lets the tools read PSX notice PDFs.

### Way 1: as a Claude Code plugin

In Claude Code, type:

```text
/plugin marketplace add kspr-technologies/psx-trader-skill
/plugin install psx-trader-skill@psx-trader-skill
```

- The second line opens the plugin's page. Pick where to install it, for
  example "Install for you".
- The skills then show up as `/psx-trader-skill:psx-scanner` and so on.
- To update, in a shell: `claude plugin update psx-trader-skill@psx-trader-skill`.
  Auto-update is off for this marketplace until you turn it on in `/plugin`.
- To remove: `claude plugin uninstall psx-trader-skill@psx-trader-skill`.

### Way 2: by hand (macOS and Linux)

Get the code, then link each `psx-*` folder into your skills folder:

```bash
git clone https://github.com/kspr-technologies/psx-trader-skill.git ~/psx-trader-skill
mkdir -p ~/.claude/skills
ln -s ~/psx-trader-skill/psx-* ~/.claude/skills/
```

- The skills then show up as `/psx-scanner` and so on.
- To update: `git -C ~/psx-trader-skill pull`. The links stay.
- To copy instead of link: `cp -R ~/psx-trader-skill/psx-* ~/.claude/skills/`.
  Then copy again after each update.
- For one project only, put them in that project's `.claude/skills/` instead.
- To remove the links: `rm ~/.claude/skills/psx-*`.

Then open Claude Code and type `/psx`: the 13 skills show up.

Auto-run on a Mac needs one more step that you do yourself. Use Way 2 for it:
a plugin's folder changes with each update. See "Auto-run on your Mac" below.

No Claude Code at all? Paste any single `SKILL.md` at the top of a chat and it
runs as a mode. You lose the Python tools, but the method still works.

## Quick start: 5 minutes on made-up data

Run one day trade by hand, on DEMO, a made-up stock. It needs the `git clone`
from Way 2. It writes only in a new folder, `~/psx-demo`, and only on paper:
nothing goes to a broker.

Make the demo folder with the made-up market and the example settings. Then
run the day, tool by tool. `--now` sets the clock to the made-up day, Thursday
October 29 2026. Each tool reads what the one before it wrote.

```bash
mkdir -p ~/psx-demo/psx
cd ~/psx-demo
cp -R ~/psx-trader-skill/tests/fixtures/psx-market psx/data
cp ~/psx-trader-skill/psx-budget/config.example.json psx/config.json
PACK=~/psx-trader-skill
python3 $PACK/psx-scanner/scan.py universe --now "2026-10-29 09:00"
python3 $PACK/psx-scanner/scan.py daily --now "2026-10-29 09:00"
python3 $PACK/psx-entry/entry.py check DEMO --now "2026-10-29 10:15" --write
python3 $PACK/psx-exit/exit.py plan --stop or-mid --target 1R --now "2026-10-29 10:16" --write
python3 $PACK/psx-timemachine/timemachine.py run --now "2026-10-29 10:16"
python3 $PACK/psx-budget/size.py --now "2026-10-29 10:16" --write
python3 $PACK/psx-risk/check.py --now "2026-10-29 10:16"
python3 $PACK/psx-broker/ticket.py show --now "2026-10-29 10:16"
python3 $PACK/psx-broker/ticket.py approve --index 1 --reply yes --now "2026-10-29 10:16"
```

You should see DEMO among 7 setups, then TRIGGERED at 100.65, a plan, the
replays, 3,428 shares and PASS. Only after your yes does the last tool print
the line to type into a broker app. This is paper, so you place nothing.

"An example, step by step" below explains each number. To try it with Claude,
start Claude Code in `~/psx-demo` and ask: "Explain the plan in
psx/plan.json." Done? Delete the folder: `rm -rf ~/psx-demo`.

## How to use it

Everything lands in a `psx/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (a day trade)
scanner -> news -> exit -> timemachine -> budget -> risk -> broker            (a swing trade: no entry step)
alerts: stop, target, flat by the close (day); stop, target, trend, time (swing)   (while it is open)
invest -> risk -> broker                                                       (long-term shares, in their own folder)
news / analyst / insiders                                                      (on a schedule)
autorun: the checks above on a timer                                           (on your Mac)
```

All times are Karachi time (PKT). The regular market runs:

- Monday to Thursday: 9:30 AM to 3:30 PM (trading starts at 9:32, after the
  opening match).
- Friday: 9:15 AM to 12:00 PM, then 2:30 PM to 4:30 PM (the Juma break).
- Ramadan: 9:15 AM to 1:30 PM, and 9:15 AM to 12:30 PM on Friday.
- The pre-open is the 15 minutes before the open. PSX takes limit orders
  then. They wait for the opening match.

### An example, step by step

The example below is a **sample day made for the tests**. The stock DEMO
("Demo Industries Limited") is made up, and so are all its prices. The sample
copies PSX's real file formats, so the tests can repeat every number here.
Real PSX data stays on your computer. The quick start above runs this day.

Thursday October 29 2026. DEMO closed at 98.40 the day before.

1. **`/psx-scanner`** before the open: of 31 sample shares, 28 pass the price
   and volume floors. 7 match a setup. DEMO is a 20-day breakout: it closed at
   98.40, over the 96.56 top close of the 20 sessions before.
2. **`/psx-news`**: DEMO's notices on PSX (results on October 27, a
   "Material Information" notice on October 28) and a Dawn story, "Demo
   Industries wins Rs1.2bn packaging order".
3. **`/psx-entry`** watched DEMO bar by bar. Its opening range (9:30 to 9:45)
   is 98.90 to 100.20. At 10:12 it said waiting: the 10:05 bar closed at 100.05,
   0.15 under the top. At 10:15 it said triggered: the 10:10 bar closed at
   100.65, 0.45% over the range.
4. **`/psx-exit`** set the plan before the buy: stop at the range middle,
   99.55, so 1.10 a share at risk; target one times that risk, 101.75; flat by
   3:20 PM either way. Today's price limits are 88.56 to 108.24 (10% from the
   last close), so both levels can fill. It also warns that the costs are about
   33% of the price risk.
5. **`/psx-timemachine`** replayed the rest of the day 10,000 times, from
   10:15 AM to the plan's flat-by time, 3:20 PM (seed 19, so anyone can repeat
   it). The data folder held 24 past Monday to Thursday sessions of DEMO's own
   5-minute bars, so each bar was drawn from those.
   - Target first 31.3 %, stop first 23.6 %, still open at 3:20 PM 45.1 %.
   - Costs are about 0.33R a trade. So a 1R win nets +0.67R and a stop costs
     -1.33R. The average R is -0.226, costs included.
   - Its verdict: "Leans toward the stop". After costs, a 1R target has to come
     first in 66 % of the replays that hit one side. It did in 57 %.
   - In daily mode (daily bars only) the same plan is "Strongly toward the stop".

   This is made-up data, so the odds say nothing about a real stock. The lesson
   is real, though: on PSX, costs eat a big part of a tight stop.

6. **`/psx-budget`** with a Rs 500,000 account and a Rs 5,000 max loss per
   trade, costs included: 3,428 shares (Rs 345,028.20). If the stop fills at
   99.55, the loss is Rs 4,999.94: Rs 3,770.80 from the price and Rs 1,229.14 in
   broker commission, sales tax and fees. Without the costs it would have been
   4,545 shares. It writes one order: buy 3,428 DEMO, limit 100.65, for today.
7. **`/psx-risk`** checks it against your limits and PSX's rules: the max loss,
   the daily loss limit, trades per day, reward to risk, the entry window, the
   price limits, position size and cash. PASS or BLOCK, with the reason. One
   more share (3,429) and the max loss is Rs 5,001.40: BLOCK.
8. **`/psx-broker`** shows you the order in plain words. You type yes for
   that exact order. Only then does it print the line to type into your
   broker's app (or, on paper, it fills at 100.65 in the paper ledger).
9. **`/psx-alerts`** rings on the stop, the target and at 3:20 PM to close
   whatever is still open. With the site off, save a fresh company page before
   each check, or watch the stop in your broker app.

## Two styles: day and swing

Each trading folder has one style. You set it in `psx/config.json`, and
nowhere else (there is no flag):

- `"style": "day"` is the default. A day trade is in and out on the same day.
  You are flat by 3:20 PM.
- `"style": "swing"` holds a trade for days or weeks: up to 20 sessions.
- Day style works as before. If you never set a style, nothing changes.
- One small change in both styles: with paper off, the first fill puts
  `"real_holdings": true` at the top of `psx/portfolio.json`. It prints one
  line about it. Then a paper fill can never change that file.
- Change the style only when no order is waiting, and you hold no shares
  without a swing plan. A day trade has no plan. A share you bought before the
  switch has none either. See "If you already hold shares" below.

Paper first, in a folder of its own. Swing trading loses money for many people
too. See "Paper and real shares" below.

### If you already hold shares

The swing tools watch a share only when `psx/portfolio.json` holds its swing
plan. A swing fill writes that plan. A share with no plan is not a swing
position: a day trade, or a share you bought before the switch.

A share with no plan:

- gets no swing rules: no trend rule, no time stop, no stop-limit reminder.
- is watched only by its rules in `psx/alerts.json`. They use only today's
  prices, never a closing file. So they miss a dip under the stop between two
  checks. They also miss a day when no check ran (the Mac slept).
- gets no stop-limit ticket and no new stop: `ticket.py protect` and
  `ticket.py stop` refuse it.
- psx-risk counts its risk as your max loss per trade.

So switch only after such a share is sold. If you switch while you hold it,
watch its stop in your broker app too. And give its rules
`"rearm": "daily"` in `psx/alerts.json`: then a rule rings once each day that
it is hit.

### Paper and real shares

Does a folder hold real shares or real cash? Then do not turn paper on there.

- A paper fill can go into that folder's `psx/portfolio.json`, next to your
  real shares. Then its cash and shares no longer match your broker app.
- On paper, every ticket says "do not place it in your app". A real share
  needs a real sell, for example when its stop is hit.
- So try paper in a new folder. Copy `psx/config.json` and `psx/data/` into
  its `psx/`, and set `"paper": true` there (and `"style": "swing"` for swing
  trades). Do not copy `psx/portfolio.json`: the paper cash then starts from
  its `"budget"`.
- Auto-run does not watch the new folder. It watches only the two folders you
  gave it at install.
- To lock a folder of real shares, put `"real_holdings": true` at the top of
  its `psx/portfolio.json`. Then a paper fill into it is refused, and nothing
  is written. Its paper tickets still say "do not place it", so keep paper off
  there.
- With paper off, a fill puts that flag in by itself. But a ledger from before
  v2, or one made by hand, has no `"real_holdings"` flag, even when its shares
  are real.
- So `ticket.py` also refuses a paper fill into a file with no flag that shows
  a sign of real shares. Then nothing is written. The signs:
  - a top key the tools never write, like a `"note"`;
  - a yes in `psx/state/broker.json` made with paper off;
  - in swing style, a held share with no swing plan.
- Then mark which it is. Real shares: put `"real_holdings": true` at the top.
  A paper file: put `"real_holdings": false` at the top. Then paper fills go
  in.
- A file with no flag and no sign takes a paper fill, as before. So lock a
  folder of real shares yourself.

### The four exits of a swing trade

Each exit is a plain rule. You can read it and change it.

- **The stop.** It comes from the daily closing files: 2 times the daily
  ATR(14) under the entry (`"atr:2"`). ATR(14) is how far the share moves in a
  normal day. It is the average of the last 14 sessions, each from its low to
  its high. A jump from the last close counts too. The tools never move the
  stop by themselves. `ticket.py stop --symbol DEMO --set 97.00` moves it up,
  at your word.
- **The target.** 2 times the risk (`"2R"`).
- **The time stop.** Out on the exit-by day: 20 PSX sessions after the buy
  day, by your flat-by time. It moves with that day's hours: 3:20 PM is
  4:20 PM on a Friday.
- **The trend break.** A close under the lowest close of the 10 sessions
  before it. The rule then says: sell in the next session. Payouts are
  counted, so a payout is never a trend break.

### The buy

- It is a limit buy at the last close plus 1% (`entry_buffer_pct`).
- You can put it in during the pre-open. It waits for the opening match.
- PSX orders last one day. A buy that does not fill ends at the close. Do not
  chase it. Record it: `ticket.py result --index 1 --status expired`.
- `--index 1` is the first order in `psx/orders.json` now. On a later day, or
  once a new order is there, name the buy by its fingerprint:
  `ticket.py result --fingerprint FP --status expired`. `approve` printed FP.
- psx-entry is not used. The daily scan already found the setup.

### Gap risk: a share held overnight

The next session can open far under your stop, even at its lower price limit
(10% down). Then a sell fills at the open, not at the stop.

- psx-exit shows this loss a share. psx-budget and psx-risk show it in rupees.
- `swing.max_gap_loss_pkr` caps it. The default is 3 times your max loss per
  trade (Rs 15,000 with Rs 5,000). psx-budget sizes to it, and psx-risk
  blocks a buy over it.
- More days down can lose more.

### The stop-limit ticket (live only)

PSX holds no stop order for you. Many broker apps have a stop-limit
(stop-loss) order.

- `ticket.py protect --symbol DEMO` makes a stop-limit sell ticket of the
  shares you hold. The trigger is your plan stop. The limit is 1% under it.
- It goes through psx-risk and your yes, like every order. The ticket asks
  for "yes 96.25" (your trigger).
- Treat it as a day order. Put it in again each day after the opening match.
  psx-alerts reminds you at the first check.
- No order guards the opening price. A gap at the open can go past it.
- A stop-limit sells only at its limit or better. On a fast fall it may not
  fill. Then sell by hand (`ticket.py flatten --symbol DEMO`).
- On paper, no stop order rests anywhere. psx-alerts watches the stop.

### Limits for the whole account

psx-risk adds these checks for swing buys. They never block a sale.

- At most 3 open swing positions (`max_open_positions`).
- The max loss of all open positions together is capped
  (`max_open_risk_pkr`; empty means your `max_daily_loss_pkr`).
- One position a stock.
- A buy with a yes but no fill recorded, from an earlier day, blocks new swing
  buys. Record the fill (`ticket.py fill`), or record it expired.
- A buy that waits at your broker uses its cash. It counts as open too.

One check can block a sale: PSX bans selling shares you do not hold. So while
a stop-limit sell of your shares rests at your broker, a second sell of the
same shares is blocked. Cancel the first one in your app, then record it:
`ticket.py result --fingerprint FP --status cancelled`.

In swing style the commission counts on both sides: a sale on a later day
always pays it.

### Payouts, bonus shares, rights and splits

- A cash payout (XD): your stop and target stay. A fall by the payout alone
  can meet the stop.
- A bonus, a right or a split (XB, XR): psx-alerts asks you once a day until
  you choose. Scale the plan (`ticket.py stop --symbol DEMO --scale 0.8333`)
  or keep it (`--scale 1`). Record the bonus shares when they come:
  `ticket.py fill --bonus 28 --symbol DEMO`.

### The swing settings

The example config ships with the swing block. A block in your own config
needs only the keys you change.

```json
  "style": "swing",
  "swing": {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20, "trend_exit_sessions": 10,
            "entry_buffer_pct": 1.0, "max_open_positions": 3, "max_open_risk_pkr": null,
            "max_gap_loss_pkr": null, "stop_limit_gap_pct": 1.0},
```

- `stop` also takes `low:N`, `level:K`, `price:X` and `pct:X` (see psx-exit).
- `no_entries_before` and `no_entries_after` are day rules. Swing does not use them.
- `flat_by` counts on the exit-by day only.

### How a swing trade runs

| when (Karachi time) | you run | what it does |
| --- | --- | --- |
| after the close | `scan.py daily` | the setups for the next session (the closing file is out 30 to 40 minutes after the close) |
| the same evening | `exit.py plan --symbol DEMO --write` | the swing plan for the next session |
| the same evening | `timemachine.py run` | 10,000 replays of the next 20 sessions, gaps included |
| the same evening | `size.py` | a preview of the size. It writes nothing |
| the buy day, in the pre-open | `size.py --write`, `check.py`, `ticket.py show`, `ticket.py approve` | the limit buy ticket, psx-risk, your yes |
| after the fill | `ticket.py result --index 1 --status placed` (live: add `--filled-price P`), or `ticket.py fill` | the ledger (`psx/portfolio.json`) gets the position and its plan |
| no fill | `ticket.py result --fingerprint FP --status expired` (`approve` printed FP) | the order is closed. New swing buys can go in again |
| live: each day after the opening match | `ticket.py protect --symbol DEMO` | the stop-limit ticket |
| while you hold it | `alerts.py check` | the stop, the target, the trend and the time rules |
| a rule rang | `ticket.py flatten --symbol DEMO` | the sell ticket (`--limit P` in the pre-open), then psx-risk and your yes |

Every order still needs a psx-risk PASS of that exact order, at most 5 minutes
old, and your yes for that one order.

### A swing example, step by step

The same made-up DEMO, in a folder with `"style": "swing"`. On paper, with the
site off. `tests/test_chain.py` runs every step below and checks each number.
The sample market ends on Thursday October 29. More made-up closing files go
on to Friday November 27. In them, DEMO rises.

Wednesday October 28 2026, after the close. DEMO closed at 98.40.

1. **`/psx-scanner`** after the close: DEMO is a 20-day breakout. It closed at
   98.40, over the 96.56 top close of the 20 sessions before.
2. **`/psx-exit`** plans the buy for the next session, Thursday October 29:
   - a limit buy at 99.38: the last close plus 1%;
   - the stop at 96.25: 2 times the daily ATR (1.565, DEMO's normal day's move)
     under the entry, so 3.13 a share at risk;
   - the target at 105.64: two times that risk;
   - exit by Friday November 27 at 4:20 PM: 20 sessions on. Iqbal Day
     (Monday November 9) is not a session;
   - the trend rule: out after a close under the lowest close of the 10
     sessions before it (92.36 now);
   - the gap risk: a next-day open at the lower limit 89.44 loses about 10.28 a
     share, costs included. That is 2.95 times the loss at the stop.
3. **`/psx-timemachine`** replays the next 20 sessions 10,000 times (seed 19).
   Each session is one of DEMO's own last 60 daily bars, drawn whole, so gaps
   between sessions are in it.
   - Target 34.5 %, stop 34.0 % (2.8 % through a gap at the open), trend rule
     19.8 %, time stop 11.7 %.
   - The average R is +0.584, costs included. Its verdict: "Leans toward the
     target".
   - This is made-up data, so the odds say nothing about a real stock.
4. **`/psx-budget`** that evening gives a preview only. On the buy day, in the
   pre-open, it writes the order: 1,436 shares (Rs 142,709.68). If the stop
   fills at 96.25, the loss is Rs 4,997.81, costs included. If the next day
   opens at the lower limit, the loss is about Rs 14,759.46: under the
   Rs 15,000 gap limit.
5. **`/psx-risk`**: PASS in the pre-open. A limit buy waits for the opening
   match.
6. **`/psx-broker`**: you type yes. On paper it records the fill at 99.38. The
   ledger, `psx/portfolio.json`, now holds DEMO with its plan: the stop, the
   target, the exit-by day and the trend rule.
7. On paper, `ticket.py protect --symbol DEMO` writes no order: no stop rests
   anywhere, so psx-alerts watches it. Live, it makes the stop-limit ticket:
   trigger 96.25, limit 95.28.
8. **`/psx-alerts`** checks every closing file once. After the close on
   Friday October 30: nothing hit. Say no check ran on Monday or Tuesday (the
   Mac slept). The check after the close on Wednesday November 4 reads those
   two files and that day's, once each. It rings the target: the high in that
   day's closing file was 105.90.
9. The next morning, in the pre-open: `ticket.py flatten --symbol DEMO --limit 105.64`.
   psx-risk passes it: a sale of shares you hold is an exit. Then your yes.
   When DEMO leaves the ledger, its alerts stop by themselves.

## Long-term signals (psx-invest)

For the shares you keep for years. They live in their own folder, with its
own `psx/`, apart from the trading folder.

- Copy `psx-invest/invest.example.json` to `psx/invest.json` in that folder.
  Without it the tool stops. `--invest` can name a rules file in another
  folder, but then every command that writes still stops. So it never writes
  in the trading folder by mistake.
- Each share you hold gets one signal: ADD, HOLD, TRIM or REVIEW.
- A signal says which rules are met. It is not a forecast. You decide.
- It never uses what you paid, and it never prints a profit.

The rules. You can read and change every number in `psx/invest.json`:

| rule | what it looks at | what it does |
| --- | --- | --- |
| trend | the month-end close against its 10-month line | a vote: up at +2% or more, down at -5% or less |
| 12 months | the month-end close against the one 12 month-ends back, payouts counted | a vote, with the same bands |
| break | both votes down | TRIM half, once. A break that began before the watch began is a REVIEW |
| size | each share's part of your long-term money | over 20%: no new money. Over 25%: TRIM back to 20% |
| sector | each sector's part | over 30%: no new money. Over 35%: REVIEW |
| profit | the newest quarter against the same quarter a year back | down 25% or more, or a loss: REVIEW |
| data | a missing closing file, no trade for 20 sessions, a bonus, a right or a split | REVIEW: check your shares |

- The price rules look once a month, at the last close of the month. That cuts
  false signals.
- New money goes only to ADD shares: whole shares, costs included, inside the
  caps. It never picks a new share by itself.
- `"real_holdings": true` at the top of `psx/portfolio.json` marks real
  shares. On paper, no tool writes a paper fill into that file.
- With paper on and real holdings, `invest.py orders --write` writes no
  `psx/orders.json`. It writes its journal: what the rules would do. It also
  saves each TRIM it shows in `psx/state/invest.json`. Then a sale you make
  later counts toward that TRIM, and the next run asks only for the rest.
- In that case, record each fill you make in your own app with `--real`:
  `ticket.py fill --symbol ZCAP --side sell --qty Q --price P --real`. Q and P
  are the shares and the price your app shows. `invest.py orders` prints this
  line for you.
- One broker account holds the cash of both folders. Never count the same
  money twice.
- Put your long-term shares in the `"blocked"` list of the trading folder's
  `psx/config.json`. Then the trading tools leave them alone: the scan leaves
  them out, and a buy of one is refused.
- The long-term folder's `psx/config.json` needs 5 values of its own:
  `"require_stop": false`, `"cash_buffer_pct": 0`, `"max_position_pct": 20`,
  `"max_order_pkr"` (your choice) and `"max_trades_per_day": 3`.
- The trend rule needs 10 month-ends, and the 12-month rule 13. With the site
  on, `invest.py backfill` fetches about 500 old closing files, once (about 12
  to 15 minutes). Try `invest.py backfill --dry-run` first: it asks nothing.

The steps:

1. `invest.py signals`: one signal per share. It writes nothing.
2. `invest.py explain ZUP`: every fact and rule for one share.
3. `invest.py money 46000`: how new money could be split. It writes nothing.
4. In the session: `invest.py orders --money 46000 --write`, then psx-risk
   (`check.py`), then `ticket.py show` and your yes for each order.

An example on the made-up test market (`tests/fixtures/psx-invest/`), on
Friday October 30 2026:

- ZUP is ADD: no vote is down, both are up, and it has room to its fair share.
- ZCAP is TRIM: it is 27.2% of the money, over the 25% trim line.
- ZOLD is REVIEW: both votes have been down since Friday July 31.
- On Monday November 2, in the session, `invest.py orders --write` writes the
  TRIM sell first (778 ZCAP at 91.55), then the ADD buy (142 ZUP at 242.04).
  Neither has a stop.
- psx-risk passes both with `"require_stop": false`. With `true` it blocks the
  buy, and it still passes the sale: a sale of shares you hold is an exit.

## Auto-run on your Mac (psx-autorun)

"Will it auto run, or do I have to ask for the scan again?" After a one-time
install, your Mac runs the checks on a timer. You do not need to open Claude.

- macOS starts a small app, "PSX Autorun", every 5 minutes. It runs a plain
  Python runner. No AI model runs on the timer.
- The runner runs a fixed list of the pack's own tools. It writes a short
  report and sends Mac messages.
- Claude is still needed for plans, for explaining signals and for every
  order. Each order needs your yes.

When it runs, Monday to Thursday (Karachi time). Every time comes from the
pack's PSX clock, so Fridays, Ramadan and holidays get their own times.

| job | when | what it runs |
| --- | --- | --- |
| morning | 8:15 AM | the scan, the alerts (for a swing share: every closing file not checked yet), news, insider notices, the long-term signals |
| price checks | every 5 minutes from 9:40 AM to 3:30 PM, and at 3:40 PM | the alerts: the trading folder first, then the long-term folder |
| after the close | 4:10 PM, and again until the closing file is out | the scan for the next session, the alerts on the close, the signals, the news, each buy with no fill recorded |
| week report | Saturday 10:00 AM | the share list, your long-term results, next week's PSX days, the cash in both folders |

On Fridays the price checks start at 9:25 AM, and again at 2:40 PM after the
Juma break. `autorun.py plan --date 2026-11-13` shows the times of that Friday.

What it never does:

- It never places an order. It has no broker link.
- It never runs psx-broker, psx-budget or psx-risk. It never runs
  `invest.py orders`, `cash`, `ack` or `backfill`, or `alerts.py add` or
  `reset`. The code refuses them, whatever the settings file says.
- It reads the PSX site only with `"psx_site": true`, one tool at a time.

You install it yourself, in Terminal. Claude shows you the line, and never
installs it for you:

```bash
bash "<pack>/psx-autorun/install.sh" --trading "<folder that holds psx/>" --longterm "<folder that holds psx/>"
```

- `<pack>` is the folder you cloned in Way 2. In the quotes, write
  `$HOME/psx-trader-skill`, not `~/psx-trader-skill`: `~` does not work inside
  quotes. The same goes for the two folders.
- macOS asks two things. Click Allow for both: your Documents folder, and
  notifications.
- A message shows: "PSX auto-run works".
- No admin password. Turn it off with `bash "<pack>/psx-autorun/uninstall.sh"`.
- Keep the Mac on, with the lid open and on power, in market hours. A shut lid
  sleeps. When the Mac is off, nothing runs.
- A Mac that slept catches up when it wakes. For a swing share with its plan
  in `psx/portfolio.json`, no close is lost: psx-alerts reads every closing
  file it has not checked yet. A rule you added by hand uses only today's
  prices, so it misses a day when no check ran.
- No morning message by 9:15 AM on a market day (9:00 AM on Fridays and in
  Ramadan)? Look at Login Items in System Settings, and run `autorun.py status`.
- The reports are in `<trading folder>/autorun/`. Open `LATEST.md` first.
  Alerts and problems come first in the messages.

Commands (run them from `~/Library/Application Support/psx-autorun/`):

```bash
python3 autorun.py status                   # when each job last ran
python3 autorun.py plan                     # today's times
python3 autorun.py tick --dry-run           # what is due now, without running it
python3 autorun.py run after_close          # run a job now (morning, after_close, weekly)
python3 autorun.py pause checks             # no price checks today (before your own alerts loop)
python3 autorun.py pause all --minutes 10   # stop everything for 10 minutes, then go on by itself
python3 autorun.py resume
```

## The tools

All standard-library Python. With the site off (the default) they read only
`psx/data/`. With the site on, they ask the site first. Run them from your
project folder, the one that holds `psx/`. Put the pack's folder in front of
each path, as in the quick start. Leave out the `# notes`.

```bash
python3 psx-scanner/scan.py universe                                # ordinary PSX shares -> psx/universe.txt
python3 psx-scanner/scan.py daily                                   # before the open: daily setups
python3 psx-scanner/scan.py tape                                    # record the market tape (needs the site)
python3 psx-scanner/scan.py orb --at 10:15                          # opening-range breakouts so far
python3 psx-news/news.py --tickers DEMO                             # PSX notices, news, today's move
python3 psx-entry/entry.py check DEMO --setup orb                   # waiting / triggered / expired
python3 psx-entry/entry.py watch DEMO --every 60                    # bar by bar until it triggers
python3 psx-exit/exit.py plan --stop or-mid --target 1R --write     # -> psx/plan.json
python3 psx-timemachine/timemachine.py run --write                  # 10,000 replays to your flat-by + the history
python3 psx-budget/size.py --write                                  # -> psx/orders.json
python3 psx-risk/check.py                                           # PASS / BLOCK per order
python3 psx-broker/ticket.py show                                   # the tickets, READY or not
python3 psx-alerts/alerts.py add --from-plan                        # stop, target, flat by close
python3 psx-alerts/alerts.py check                                  # rings what hit, once
python3 psx-analyst/facts.py DEMO                                   # quarters, growth, EPS
python3 psx-insiders/insiders.py new --read                         # new insider notices
```

In swing style (`"style": "swing"` in `psx/config.json`):

```bash
python3 psx-scanner/scan.py daily                                   # after the close: setups for the next session
python3 psx-exit/exit.py plan --symbol DEMO --write                 # the swing plan for the next session
python3 psx-timemachine/timemachine.py run                          # 10,000 replays of the next 20 sessions
python3 psx-budget/size.py                                          # the evening before: a preview, writes nothing
python3 psx-broker/ticket.py result --index 1 --status expired      # a buy that did not fill today
python3 psx-broker/ticket.py result --fingerprint FP --status expired   # the same, on a later day
python3 psx-broker/ticket.py protect --symbol DEMO                  # live: the stop-limit sell ticket
python3 psx-broker/ticket.py stop --symbol DEMO --set 97.00         # move the stop up (or --scale F for a bonus)
python3 psx-broker/ticket.py fill --bonus 28 --symbol DEMO          # bonus shares, at cost 0
python3 psx-broker/ticket.py flatten --symbol DEMO --limit 105.64   # a limit sell (no market order in the pre-open)
python3 psx-broker/ticket.py cash --set 46000                       # correct the cash in psx/portfolio.json
python3 psx-alerts/alerts.py list                                   # your rules, and the swing rules of the ledger
```

In a long-term folder (with `psx/invest.json`):

```bash
python3 psx-invest/invest.py signals                                # one signal per share; writes nothing
python3 psx-invest/invest.py signals --write                        # also psx/signals.json, the state, the journal
python3 psx-invest/invest.py explain ZUP                            # every fact and rule for one share
python3 psx-invest/invest.py money 46000                            # how new money could be split; writes nothing
python3 psx-invest/invest.py orders --money 46000 --write           # today's orders for psx-risk (in the session)
python3 psx-invest/invest.py cash --add 46000                       # record new long-term money
python3 psx-invest/invest.py ack ZOLD --keep                        # your choice on a REVIEW or TRIM
python3 psx-invest/invest.py backfill --dry-run                     # what the one-time backfill would fetch
python3 psx-invest/invest.py backfill --forget-no-session           # clear only the days with no session (pause auto-run first)
python3 psx-invest/invest.py rules                                  # the rules and numbers in use
```

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. Swing trading loses money for many
people too. You can lose money. Decide for yourself, and talk to a licensed
adviser if you need advice.

**No trade goes through until you say yes.** Nothing in this pack places an
order. `/psx-broker` shows the order first. It prints the line to type into
your broker app only after `/psx-risk` passes that exact order and you type
yes for it. Then you place it yourself. We found no
Pakistani broker that gives retail clients an order API (12 brokers checked in
October 2026). If a broker tool ever appears, set it to "Needs approval" in
Claude, never "Always allow". Your broker passwords and PIN never go in the chat.
Auto-run never places an order, and never runs psx-broker, psx-budget or
psx-risk.

**Paper first.** `"paper": true` is the default in `config.example.json`. Try
swing trades on paper first, in a folder of their own. Never turn paper on in
a folder that holds real shares or real cash: see "Paper and real shares".
When you go live, start a fresh `psx/portfolio.json`: paper and real positions
must not mix. A ledger from before v2, or one made by hand, has no
`"real_holdings"` flag, even when its shares are real. So when you go from real
money to paper in a folder of real shares, put `"real_holdings": true` in its
`psx/portfolio.json` first. Then practise in a separate paper folder, with its
own fresh ledger.

**PSX's data terms.** The PSX site is off by default (`"psx_site": false`).
Turn it on only if PSX allows you. Your saved files are for your own use. The
test data in this pack is made up for the same reason.

**One tool on the site at a time.** Never let two tools poll the PSX site at
once.

- A tape or an entry watch: auto-run sees it by itself. Then auto-run reads no
  PSX page, and its checks read only the prices saved on your computer. Do not
  pause auto-run for it.
  - A tape saves new prices of the whole market, so the checks go on.
  - An entry watch saves no market prices, only the page of the share it
    watches. So the trading folder's alerts get no new price. After 15 minutes
    auto-run sends a problem message: "the DEMO alerts had no new price for 15
    minutes". While a watch runs, check your stop in your broker app.
- Your own alerts loop (a `/loop` or a scheduled task in Claude): first run
  `autorun.py pause checks`. The pause lasts the rest of the day.
- Until you run `autorun.py resume`, the price checks of both folders are off,
  so no stop is checked while PSX is open. The after-close job still runs. So
  run `autorun.py resume` as soon as your loop ends.

**Costs are part of the loss.** A same-day round trip at a normal broker costs
about 0.36% of the trade. That is:

- 0.15% commission on each side, or Rs 0.03 a share if that is more;
- 15% sales tax on the commission;
- small exchange and regulator fees.

Set your broker's real rates in the `costs` block of `psx/config.json`. In
swing style the commission counts on both sides.

**Held overnight.** A swing trade can open past its stop, even at the lower
price limit. Then the loss is bigger than the max loss at the stop. The tools
show this gap loss before you buy, and cap it (`swing.max_gap_loss_pkr`).

**Price limits and halts.** A share cannot trade more than 10% (or Rs 1)
from its last close in one day. At a limit there may be no buyers, so a stop
may not fill. If the KSE-30 index stays 5% up or down from its last close for
5 minutes, PSX stops all trading for 60 minutes and cancels every open order.
This rule does not apply in the last 75 minutes of the day. Check your orders
in your broker app after a halt. A halt can cancel your stop-limit too: put it
in again after the restart.

**The Time Machine is a simulation, not a prediction.** In daily mode it is
built from the stock's last 60 daily bars; with 20 or more sessions of your own
5-minute bars it uses those. The future can do things those days never did:
results, a halt, a headline. Treat its odds as a reality check, never a promise.

**A stop is a plan, not a guarantee.** PSX has no exchange stop orders. Some
broker apps have a stop-loss feature; otherwise you sell by hand when the
alert rings.

**Signals are rules, not predictions.** No tool knows where a price will go.
A setup, an alert or a signal is a rule that matched. You decide.

**Short selling.** Selling shares you do not hold is banned on PSX's regular
market. This pack plans long trades only. Settlement is T+1.

**Insider notices are public, not inside information.** Directors, executives
and big holders must tell their company within 7 days of a trade (PSX Rule
Book 5.6.4). The company then sends it to PSX, so the notice can come later.
The tracker reads those public notices. It is fast, not early.

**Taxes.** Gains on PSX shares are taxed (15% for filers in tax year 2027,
taken by NCCPL). The costs above do not include this tax. Nothing here is tax
advice.

## Files

```
psx-scanner/     SKILL.md, scan.py
psx-news/        SKILL.md, news.py
psx-entry/       SKILL.md, entry.py
psx-exit/        SKILL.md, exit.py
psx-timemachine/ SKILL.md, timemachine.py
psx-risk/        SKILL.md, check.py
psx-budget/      SKILL.md, size.py, config.example.json, targets.example.json
psx-broker/      SKILL.md, ticket.py
psx-alerts/      SKILL.md, alerts.py, alerts.example.json
psx-analyst/     SKILL.md, facts.py, metrics.example.json
psx-insiders/    SKILL.md, insiders.py
psx-invest/      SKILL.md, invest.py, invest.example.json
psx-autorun/     SKILL.md, autorun.py, run.sh, install.sh, uninstall.sh, PSXAutorun.applescript,
                 autorun.example.json, launchd/ (the two schedule templates)
tests/           unittest suite, psx_core.py (the shared helpers), made-up test data
```

## Credits

- The original Day Trader skill is by [Jake Schincariol](https://opusjake.ai)
  ([day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill), MIT).
  The jobs, the safety rules and the code style are his.
- This PSX version is by Waleed Amjad. The market rules, the data and the
  money are PSX.
- Maintainer: Waleed Amjad ([developerwaleed](https://github.com/developerwaleed) on GitHub).

## Help and changes

- Issues and pull requests:
  [github.com/kspr-technologies/psx-trader-skill](https://github.com/kspr-technologies/psx-trader-skill).
- Found a bug, or a PSX rule that changed? Open an issue. Never post real PSX
  data, your trades or your account details there.
- Before a pull request, read [CONTRIBUTING.md](CONTRIBUTING.md). It says how
  to run the tests.
- What changed in each version: [CHANGELOG.md](CHANGELOG.md).

## License

MIT. See [LICENSE](LICENSE). It holds both notices: the original one by Jake
Schincariol, and the one for this PSX version.
