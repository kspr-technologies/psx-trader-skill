---
name: psx-invest
description: >-
  Long-term Signals for the Pakistan Stock Exchange (PSX). Watches the shares you
  hold for the long term and gives each one a signal: ADD, HOLD, TRIM or REVIEW.
  Each signal comes from plain rules you can read and change: the month-end close
  against its 10-month line, the 12-month change with payouts counted, how big a
  share or a sector is in your money, a big fall in profit, and gaps in the data.
  A bonus, a right or a split gets a REVIEW. Splits new money into whole shares,
  costs included, inside your caps. It never uses what you paid. Writes
  psx/signals.json and a journal. Nothing is placed: orders go through psx-risk,
  psx-broker and your yes. Use when the user says "should I buy more", "when
  should I sell my long-term shares", "check my portfolio", "where should new
  money go", "I have Rs 20,000 to invest", or "run my long-term signals".
argument-hint: "[signals | explain SYM | money AMOUNT | orders]"
---

# psx-invest

The Long-term Signals tool looks at the shares you keep for years.
It gives each one a signal: ADD, HOLD, TRIM or REVIEW.

- A signal is a rule that is met. It is not a forecast.
- The price rules look once a month, at the last close of the month.
- It never uses what you paid, and it never prints a profit.
- It never places an order. An order goes through psx-risk, psx-broker and your yes.
- It writes only in a folder with `psx/invest.json`. Every command that writes stops in a folder
  without it, even with `--invest`. So it never writes in the trading folder by mistake.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/invest.py" signals                 # one signal per holding; writes nothing
python3 "${CLAUDE_SKILL_DIR}/invest.py" signals --write         # also psx/signals.json, the state, the journal; notify lines
python3 "${CLAUDE_SKILL_DIR}/invest.py" explain OGDC            # every fact and rule for one share
python3 "${CLAUDE_SKILL_DIR}/invest.py" money 46000             # how new money could be split; writes nothing
python3 "${CLAUDE_SKILL_DIR}/invest.py" orders --money 46000 --write   # today's orders for psx-risk
python3 "${CLAUDE_SKILL_DIR}/invest.py" cash --add 46000        # record new long-term money (also --set N)
python3 "${CLAUDE_SKILL_DIR}/invest.py" ack SYS --keep          # your choice on a REVIEW or TRIM (or --trimmed, --checked)
python3 "${CLAUDE_SKILL_DIR}/invest.py" backfill --dry-run      # what it would fetch; asks nothing
python3 "${CLAUDE_SKILL_DIR}/invest.py" backfill                # fetch it (site on only), 1.5 s apart
python3 "${CLAUDE_SKILL_DIR}/invest.py" backfill --forget-no-session   # clear only the days with no session
python3 "${CLAUDE_SKILL_DIR}/invest.py" rules                   # the rules and numbers in use
python3 "${CLAUDE_SKILL_DIR}/invest.py" journal                 # what the rules said, month by month
```

- Flags: `--data DIR` (default psx/data), `--config`, `--invest`, `--portfolio`, `--state`,
  `--metrics`, `--prices`, `--offline`, `--now "2026-10-30 17:00"` (Karachi time) and `--json`.
- Exit codes: 0 ran; 1 nothing to show (no holdings, or no closing files); 2 an error.
- `signals` without `--write` writes nothing at all. It is safe to run on a real folder.
- `--invest PATH` uses another folder's rules for a look only (signals without `--write`, explain,
  money, orders without `--write`, rules, journal). The report says where the rules came from.

The PSX site:

- It is used only with `"psx_site": true` in psx/config.json. Never with `--offline` or `--now`.
- It asks for closing files only, with plain GETs at least 1.5 s apart.
- `signals` fetches only the days after the newest file. Only `backfill` fetches old days.
- With the site off it fetches nothing. It says which files to save instead.

## What it reads and writes

| file | read or write | what |
| --- | --- | --- |
| `psx/data/mkt_summary/*.Z` | read (the site may add files) | closing files: close, LDCP, ex flags (XD, XB, XR), sector |
| `psx/portfolio.json` | read; `cash` writes it | the shares you hold and the cash. Never what you paid |
| `psx/invest.json` | read | the rule numbers (a copy of invest.example.json) |
| `psx/config.json` | read | `psx_site`, costs, `blocked`, the order limits, `paper`, the calendar fixes |
| `psx/metrics.json` | read | psx-analyst's results, for rule 7 |
| `psx/prices.json` | read | psx-alerts' quotes: a price for an order in the session |
| `psx/signals.json` | `signals --write` | each holding's facts and signal, the money, the notes |
| `psx/state/invest.json` | `signals --write`, `orders --write`, `ack`, `backfill` | the last signals, each share's break state, the watch start, open TRIMs, your acks, days with no session |
| `psx/invest/journal.jsonl` | each signal change, `orders --write` (also a run with no order), `cash`, `ack` | what the rules said. It only grows. Prices only, never a profit |
| `psx/orders.json` | `orders --write` | the orders for psx-risk. Never with paper and real holdings |

It reads no company pages and no insider notices. Payouts are shown as facts, not used by a rule.

## The rules

| # | rule | default | what it does | checked |
| --- | --- | --- | --- | --- |
| 1 | trend vote | the month-end close against the mean of the last 10 month-end closes. Up at +2% or more, down at -5% or less | one vote | each month-end |
| 2 | 12-month vote | this month-end close against the one 12 month-ends back, payouts counted. The same bands | one vote | each month-end |
| 3 | break | both votes down | the share goes out. TRIM half once if the break began after the watch began, else REVIEW | month-end |
| 4 | back in | no vote down and at least one up | the share is in again; ADD is allowed again | month-end |
| 5 | share size | cap 20% of the long-term money; trim line 25% | over the cap: no new money. Over the trim line at a month-end: TRIM back to the cap | size daily, TRIM at month-end |
| 6 | sector size | cap 30%; review line 35% | over the cap: no new money in that sector. Over the line: REVIEW | daily |
| 7 | profit | the newest quarter's profit after tax against the same quarter a year back: -25% or worse, or a loss | REVIEW | when psx/metrics.json has the row |
| 8 | data | a missing file next to an LDCP jump of 2% or more; no close for 20 sessions; a bonus, right or split after `"as_of"` | REVIEW (data) | daily |
| 9 | never add | the long-term config's `"blocked"` list | no new money (sells stay possible) | always |
| 10 | rights | an XR of a holding, or a right letter of it | REVIEW (rights) | daily |

Words:

- "Month-end close": the close of the last PSX session of a month that has a closing file.
  A month in progress counts only when the file of its last session is in.
- "Adjusted": older closes are scaled for payouts and splits. So a payout is never a fall.
- "The watch began": the newest month-end at the first `signals --write` in this folder.

**Rule 1, the trend vote.**
- The month-end close against its 10-month line (the mean of the last 10 month-end closes).
- Research: Faber (2007). A 10-month line on month-end prices gave returns near buy and hold, with smaller falls.
- How often: it is checked once a month. Month-end checks cut false signals.

**Rule 2, the 12-month vote.**
- This month-end close against the one 12 month-ends back. Payouts are counted.
- Research: Moskowitz, Ooi and Pedersen (2012); Lim, Wang and Yao (2018) for single stocks.
- How often: once a month.

**Rule 3, the break.**
- Both votes down: the share is "out". No new money while it is out.
- The break began after the watch began: TRIM half (`break_trim_pct`), once a break.
- The break began at or before the watch began: REVIEW. You choose: keep, or trim half.
- How often: in a random-walk test (no edge at all), about 0.4 to 0.5 breaks a share a year.
  About 1 break in 4 was undone within 3 months.

**Rule 4, back in.**
- No vote down, and at least one vote up: the share is "in" again. New money is allowed again.
- The state comes from the month-end prices. Each `signals --write` (and `ack`, `orders --write`) saves
  it in psx/state/invest.json, and the next run walks on from there, one month-end at a time.
- So when the newest 500 files move on, a share that stays out keeps its "since" date. Your ack still
  holds, and a TRIM comes once a break.

**Rule 5, the share size.**
- The cap is 20% of your long-term money (holdings at their last close, plus cash).
- Over the cap: no new money. Over the trim line (25%) at a month-end: TRIM back to 20%, in whole shares.
- Research: Bessembinder (2018). Most single stocks did worse than T-bills. One share can fail.
  Jaconetti, Kinniry and Zilbering (2010): a band cuts trading at about the same risk.
- How often: rare. A share must beat the rest by a lot.

**Rule 6, the sector size.**
- The cap is 30% of your long-term money. The review line is 35%.
- `"sector_groups"` joins PSX sector codes, like `{"AUTOS": ["0801", "0802"]}`.
- How often: rare.

**Rule 7, profit.**
- From psx/metrics.json (psx-analyst): profit after tax, never EPS. A split or a bonus changes EPS.
- -25% or worse on the same quarter a year back, or a loss: REVIEW. The rule only asks for a look.
- It waits when psx-analyst says the quarter label looks wrong.
- With no row, rule 7 skips the share. A candidate needs a row too: the note names every share with
  no row, candidates included. Auto-run's weekly results step fetches only the shares you hold.
- Research: Bernard and Thomas (1989) found prices drift after results. The drift faded in big US
  stocks (Martineau 2022) and was not significant on the KSE (Awais 2018).
- How often: a few times a year for 8 shares.

**Rule 8, data.**
- A closing file is missing next to an LDCP jump of 2% or more: a payout or split could hide there.
- No close for 20 sessions: is it suspended?
- A bonus (XB), a right issue (XR) or a split after `"as_of"` in psx/portfolio.json (with no
  `"as_of"`: after the watch began). Your share count may be old. No new money until you check.
- How often: rare after the backfill.

**Rule 9, never add.**
- The `"blocked"` list in the long-term psx/config.json. No new money. A sell is still possible.

**Rule 10, rights.**
- An XR of a holding, or a right letter of it (the symbol plus R, R1 or R2).
- It counts an XR or a new right letter after `"as_of"` (or the watch start), and a right letter that trades now.
- Pay for the right shares, or sell the right letter, by the date in the company's notice.
  If you do neither, the right is lost. The tool never says which to choose.

New money goes to ADD shares only, up to each share's fair share: equal (100% shared by your
holdings and candidates), or your own `"targets"`. Research: DeMiguel, Garlappi and Uppal (2009).
The equal split was not beaten out of sample.

No rule uses what you paid. Research: Odean (1998). People keep losers too long and sell winners too soon.

## How the signal is chosen

The first case that fits is the signal. Every reason shows under it (`explain`).

1. REVIEW (data): rule 8.
2. REVIEW (rights): rule 10. It comes before a TRIM, because a right has a deadline.
3. TRIM: over the trim line at a month-end check; or a break after the watch began.
4. REVIEW: a break from before the watch began; profit down 25% or more, or a loss; the sector over 35%.
5. ADD: all of these: in; no vote down and at least one up; after a minimum order the share stays at or
   under its fair share and the cap; the sector stays under its cap; not on the never-add list; no REVIEW.
6. HOLD: anything else. The reason says why ("over the 20% cap", "out since ...").

The break state of each share is "in", "out" or "not known". It is "not known" with too few
month-ends (10 for the trend, 13 for the 12-month change): then no ADD and no break TRIM.
A share that is already out at the first month-end with both votes known, after the watch began
(for example a new listing), is a REVIEW: the files do not show when its break began.

How long a signal stays:

- TRIM: until your shares in psx/portfolio.json drop by the trim amount, the share is back in, or you ack.
  After part of it is sold, the TRIM asks only for the rest, in every report and in psx/orders.json.
- REVIEW (break before the watch): until back in, or ack.
- REVIEW (profit): until psx/metrics.json shows a newer quarter, or ack.
- REVIEW (sector, data gap): while the cause is there, or ack. A data ack lasts until the next new gap.
- REVIEW (share count, rights): until `ack SYM --checked` ("I checked my shares").
- While a share is out after its TRIM, it is HOLD: no new money while out.

Your answer, `ack`: `invest.py ack SYS --keep --note "I keep it for the long term"`, or `--trimmed`,
or `--checked` for a share count or a right. It goes into the state and the journal. Then the report
says, for example: `HOLD. You chose keep on Wed 2026-11-04: "I keep it for the long term".`
A new event of another kind opens a new REVIEW.

## Step 1: set up once

1. Copy `${CLAUDE_SKILL_DIR}/invest.example.json` to `psx/invest.json` in the long-term folder.
2. Ask the user: "Is psx/portfolio.json what you really own?" If yes, put `"real_holdings": true`
   at the top of psx/portfolio.json (with the user's OK). Then a paper fill can never change it.
3. Put `"as_of": "YYYY-MM-DD"` there too: the day the share counts were checked against the broker app.
   A bonus, right or split after that day gets a REVIEW. `ack --checked` does not change `"as_of"`:
   the user sets it after checking the whole file.
4. The long-term psx/config.json needs 5 values for long-term orders (the user's OK first):

| key | for long-term | why |
| --- | --- | --- |
| `require_stop` | false | a long-term buy has no stop |
| `cash_buffer_pct` | 0 | psx-invest keeps `keep_cash_pkr` instead |
| `max_position_pct` | 20 | the same cap as rule 5 |
| `max_order_pkr` | the user picks (say 100000) | one ADD buy can be bigger than a day trade |
| `max_trades_per_day` | 3 | new money can be split into 3 buys |

One broker account holds both folders' cash. Never count the same money twice.

## Step 2: the backfill, once

The rules need about 2 years of closing files: 10 month-ends for the trend, 13 for the 12-month change.

1. `invest.py backfill --dry-run`: the count, the dates and the time. It asks nothing.
   For 500 sessions: about 450 requests, 12 to 15 minutes, about 10 MB.
2. With the user's yes, and only with `"psx_site": true`: `invest.py backfill`.
   A progress line every 20 files: "120 of 439 saved, about 9 minutes left."
3. A day that PSX answers with a 404 is an old holiday. It is recorded, and never asked again.
   Any other error stops the run. The next run goes on from there.
4. A web page in place of a closing file (a busy, blocked or error page) stops the run too.
   So do more than 6 weekdays in a row with a 404: no PSX holiday is that long. Those days are not
   recorded. Each holiday is saved as soon as a file comes after it, so a stopped run keeps them.
5. A wrong list of days with no session (after a site problem): pause auto-run first
   (`autorun.py pause all --minutes 10`), then `invest.py backfill --forget-no-session`.
   It clears only that list, and asks PSX for nothing. Then `autorun.py resume`.

## Step 3: the signals

1. `invest.py signals`: read every signal with its reason. Say the rule numbers.
2. `invest.py explain SYM` for each TRIM and REVIEW. Say what the rule says, then "you decide".
3. Then `invest.py signals --write`. It starts the watch, and prints one `notify:` line for each
   signal that changed. psx-autorun turns those lines into Mac messages.
4. When the user decides, record it with `ack`. In a folder that auto-run watches, first run
   `autorun.py pause all --minutes 10`, then `autorun.py resume` when done (two writers, one state file).

## Step 4: new money

1. `invest.py money 46000`: how the rules would split it. It writes nothing. Money that is not recorded yet
   counts as a what-if. Whole shares, costs included. One share at a time goes to the ADD share with the
   most room left under its fair share.
2. What is left stays as cash. With no ADD share: "No share meets the add rules today. The money waits as cash."
3. Before any order, record the new money: `invest.py cash --add 46000`. Only `"cash"` in psx/portfolio.json
   changes. It says: "This money must not be in your trading budget too." `orders --money N` refuses money
   that is not recorded.
4. Cash from a TRIM waits for the next run.

## Step 5: orders

In the session (09:45 to 15:00 for a buy; the yes within 5 minutes of the PASS):

1. Fresh prices for every holding: `alerts.py quote --holdings --write` (psx-alerts). It reads every
   share in psx/portfolio.json. Name each candidate too: `alerts.py quote --holdings DEMO --write`.
2. `invest.py orders --money 46000 --write`: the TRIM sells first, then the ADD buys, into psx/orders.json.
   It also saves each TRIM it shows in psx/state/invest.json, once. So after a sale, even a part
   of one, the next run asks only for the rest.
3. `/psx-risk`: PASS or BLOCK for each order. A TRIM sell is an exit.
4. `/psx-broker`: `ticket.py show`, the user's explicit yes, `ticket.py approve`: the line to type.
5. The user places it in their own broker app, then records it with `ticket.py result`.

- Paper mode and `"real_holdings": true` (the long-term folder of real shares): `orders` never writes
  psx/orders.json, so there is no ticket and no `ticket.py result`. The journal keeps what the rules
  would do. The user places the order in their own app. Then record each fill:
  `ticket.py fill --symbol DEMO --side buy --qty 151 --price 303.63 --real` (psx-broker; the numbers
  the app shows). For new money, run `invest.py cash --add N` first: `fill --real` does not check the
  cash, so the cash can go below zero. Until the fill is recorded, the rules see the old shares and cash.
- Going live is the user's own decision, in their own config.
- After a buy of a share not held before, the tool says: add it to the trading folder's blocked list.
- A candidate you do not hold shows "not held, a candidate" in its row. Its HOLD means "no rule asks
  for new money here", not "keep it".

## Example

The made-up market of the tests (`tests/fixtures/psx-invest`): 12 made-up Z shares, Rs 50,000 cash.
After a first `signals --write` on Fri 2026-10-02 (the watch began at Wed 2026-09-30):

```text
$ python3 invest.py signals --now "2026-10-30 17:00"
Long-term signals, Fri 2026-10-30 5:00 PM PKT. Closes to Fri 2026-10-30 (500 closing files from 2024-11-14).
Month-end check: Fri 2026-10-30. Next one: Mon 2026-11-30.
Your long-term money: Rs 944,559.90 in 12 shares and Rs 50,000.00 cash.

ADD     ZUP    4.9%  no vote down, trend up (+6.8%), 12-month up (+19.5%), room to its 8.3% fair share (rules 1, 2, 5)
TRIM    ZCAP  27.2%  over the 25% trim line (rule 5)
TRIM    ZBRK   8.8%  break: both votes turned down at the end of October (rule 3)
REVIEW  ZOLD   8.0%  both votes down since Fri 2026-07-31, before the watch began (rule 3)
HOLD    ZSPL   8.0%  trend no vote, 12-month no vote: the add rules need one vote up (rules 1, 2)
REVIEW  ZBON   4.8%  data: a bonus (XB) on Mon 2026-10-12: check your shares (rule 8)
REVIEW  ZGAP   7.0%  data: no closing file for Mon 2026-09-14 (rule 8)
HOLD    ZNEW   4.1%  trend not known, 12-month not known: the add rules need both votes (rules 1, 2)
REVIEW  ZRES   8.1%  profit -40.1% on the same quarter a year back (rule 7)
REVIEW  ZSTL   3.0%  data: no close since Fri 2026-10-02 (rule 8)
HOLD    ZTHN   7.1%  trend no vote, 12-month no vote: the add rules need one vote up (rules 1, 2)
REVIEW  ZRIT   4.0%  data: a right issue (XR) on Thu 2026-10-15: check your shares (rule 8); rights: ZRITR trades now (rule 10)

Note: no closing file for 1 PSX session by the calendar (Mon 2026-09-14). Prices are not scaled across a missing day. Run backfill or save the file.
Note: no row in psx/metrics.json for ZOLD, ZBON, ZGAP, ZNEW, ZSTL, ZTHN, ZRIT, so rule 7 skips them. Run psx-analyst: facts.py ZOLD ZBON ZGAP ZNEW ZSTL ZTHN ZRIT --metrics psx/metrics.json.

What the rules would do (prices: the last close):
Could sell 778 ZCAP at 91.55: about Rs 71,225.90, costs about Rs 127.57. You decide. Nothing is placed.
Could sell 650 ZBRK at 67.44: about Rs 43,836.00, costs about Rs 78.51. You decide. Nothing is placed.
A sale with a gain owes capital gains tax. NCCPL takes it. Not tax advice.
New money: the rules would split Rs 50,000.00 like this: 142 ZUP. You decide. Nothing is placed.
Rs 15,568.76 stays as cash. No ADD share has room for it.
(invest.py money 50000 shows how.)
Signals are rules, not predictions. No tool knows where a price will go. Not financial advice.
```

How to read it:

- ZCAP is 27.2% of the money, over the 25% trim line. The rule says: sell 778 shares, back to 20%. You decide.
- ZBRK's two votes turned down at the end of October, after the watch began. The rule says: sell half. You decide.
- ZOLD's break began in July, before the watch began. So it is a REVIEW: the user chooses.
- ZSPL had a 2-for-1 split with no flag. Its older prices are halved, so it shows no false break.
- New money goes to ZUP, up to its fair share (100% / 12 = 8.3%). The rest stays as cash.

## What the rules cannot do

- Whipsaws. In a choppy market a rule sells, then the price comes back (about 1 break in 4 in the test).
- Little PSX evidence. The PSX studies are few, small and mixed. Most research is on US stocks or indexes.
- Costs. Each buy and each sell costs about 0.18% at the default rates. A whipsaw costs more than that.
- Taxes. A sale with a gain owes capital gains tax (15% for a filer); NCCPL takes it. Not tax advice.
- Data gaps. A missing closing file can hide a payout or a split. That is why rule 8 asks for a look.
- The defaults are choices, not facts. Try them on paper first.

## Rules for Claude

- Quote the tool's numbers. Never work out a signal or a size by hand.
- Say "the rule says ...", then "you decide". Never say "buy X" or "sell X" as advice.
- Never change a rule number in psx/invest.json without the user.
- Never fetch from the PSX site with `"psx_site": false`, and never use a link the user did not save.
- Before `invest.py ack` in a folder auto-run watches: `autorun.py pause all --minutes 10`, then `resume`.
- Before the user sells for a TRIM, run `invest.py orders --write` (or `signals --write`) once, so the
  TRIM is saved. Then a sale counts against it, and the rule never asks for half of what is left.
- Before the first `--write`, tell the user what may show (for example: a REVIEW for a share that broke
  before the watch began).
- One broker account holds both folders' cash. Ask before `cash --add` if the money is new.

## What it never does

- It never places an order, and never predicts a price.
- It never uses, or shows, what you paid. It never prints a profit.
- It never adds to a share on the never-add list, and never sizes on margin.
- It never picks a new share by itself: only your holdings and your `"candidates"`.
- It never fetches old history outside `backfill`, and never fetches with the site off.
- It never writes psx/orders.json in paper mode with real holdings.

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
