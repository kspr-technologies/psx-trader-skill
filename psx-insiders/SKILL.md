---
name: psx-insiders
description: >-
  The PSX Insider Tracker. Watches the share trades that directors, the CEO,
  executives, their spouses and big holders (10% or more) must report to the
  Pakistan Stock Exchange: reads the "Disclosure of Interest" notices on PSX
  company pages, plus section 110 notices (someone went over 10%) and
  buy-back reports. Alerts once per new notice, reads the table (who, role,
  buy or sell, shares, rate, date, holding after), says how many days the
  notice took, flags a trade dated inside the company's own closed period,
  and flags stocks you hold. Use when the user says "are directors buying
  SYS", "insider trades on PSX", "disclosure of interest", "who is selling
  OGDC", "alert me on new insider notices", or "run the insiders skill".
argument-hint: "[--symbols SYS,OGDC] [--read]"
---

# psx-insiders

On PSX, insiders must report their trades in their own company's shares.
That means a director, the CEO, an executive, their spouse, or a holder of
10% or more. The company sends each report to PSX. PSX posts it on the
company page, in the "Others" tab. This skill reads those notices for you.

It is fast. It is not early.

- The rule gives the insider 7 days to tell the company.
- The company then sends it to PSX. So the notice on PSX can come later.
- There is no inside information here: only public notices, read quickly.
- Say that every time.

Tool in this folder (standard library Python 3.8; `pdftotext` from poppler
is optional, to read PDFs):

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py" --help
```

## What it reads and writes

| file | how | what |
| --- | --- | --- |
| `psx/data/company/<SYM>.html` | reads (with the site on, the site first) | a saved company page: notices in "Others", board meetings in "Board Meetings" |
| `psx/data/docs/<id>.txt` | reads | a notice as `pdftotext -layout` text |
| `psx/data/docs/<id>.pdf` | reads; saves it when the site is on | a notice PDF (read with pdftotext) |
| `psx/data/docs/<id>.gif` | points to it | a notice that is only a picture |
| `psx/data/mkt_summary/YYYY-MM-DD.Z` | reads | the trade day's low and high, to check a rate |
| `psx/watchlist.json`, `psx/plan.json` | reads | which companies to check |
| `psx/portfolio.json` | reads | which companies to check, and "You hold DEMO" |
| `psx/config.json` | reads | `"psx_site": true` lets the tool use the PSX site |
| `psx/state/insiders.json` | writes | the doc ids already shown, and the last check (UTC) |

- Save pages with your normal browser: `https://dps.psx.com.pk/company/<SYM>`
  as `psx/data/company/<SYM>.html`.
- The PSX site is used only with `"psx_site": true`. Turn it on only if PSX
  has given you permission (marketdatarequest@psx.com.pk). Never with
  `--offline` or `--now` (a replay). Plain GETs, at least 1.5 seconds apart.
- With the site on, the tool asks the site first. If the site fails, it
  uses the saved page and says so.
- With the site off, it reads only the saved page. A saved page has only the
  notices posted before you saved it. Save a new copy to see new ones.
- A saved page made before today gets a note: "Saved pages from before
  today". The day comes from the clock at the top of the page.
- With the site off, never open a `dps.psx.com.pk` link yourself. Ask the
  user to open it in their browser and save it as `psx/data/docs/<id>.<ext>`,
  or paste a screenshot.

## The rules

- **Quote the notice.** Every row comes from the notice itself, with its doc
  id and link. The doc id is the PSX PostID. It grows over time.
- **The law.** PSX Rule Book 5.6.4 (the notice title still says 5.6.1(d))
  and the Securities Act 2015, sections 101 to 106. The insider tells the
  company within 7 days. The company sends it on to PSX. A substantial
  shareholder holds 10% or more.
- **Say the delay.** Each row shows the days from the trade to the posting.
  Over 7 days is flagged: "Late: over 7 days."
- **Closed period.** The company names a closed period in its board meeting
  notice. Directors, the CEO and executives may not deal in it. When a trade
  date is inside it, say so as a plain fact, with the rule. No accusation.
  No guess.
- **Not checked is not outside.** A board meeting notice with no saved text
  is not checked. The tool names it: "Not checked: board meeting doc ...".
- **Section 110.** A section 110 notice says someone went over 10% of a
  company's shares. It can sit on the buyer's page and be about another
  company's shares. Check whose shares it names. The tool says so too.
- **Dates.** A date in numbers is day first: 05/10/2026 is 5 October. A date
  that only works month first (10/29/2026) is not read. It is counted as a
  line that did not read.
- **Pictures.** Most notices are only a GIF image or a scan. Say so. Never
  guess the rows.

## Step 1: what has been posted

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py" index
python3 "${CLAUDE_SKILL_DIR}/insiders.py" index --symbols DEMO,SYS --since 2026-10-01
```

- Which companies: `--symbols`, else your watchlist hits, your plan and your
  holdings, else every saved company page.
- Each line: the date, the symbol, the doc id, the kind and the title. Then
  the links: PDF, attachment, GIF.
- Each tab of a company page shows only its 5 newest rows. A busy company
  can push a notice off its page in under 2 weeks. So check every day.

## Step 2: watch for new ones

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py" new --read
```

- It shows only notices not seen before (by doc id).
- The first run shows the last 30 days. Older ones are marked as seen.
- With `--read` it reads each one: the saved text, else the saved PDF (with
  pdftotext), else the PDF from the site (when it is on), else it says
  "image only" and gives the GIF link.
- An empty saved text is a scan. The tool says so.
- With the site off, the tool says: "Ask the user to open <link> in their
  browser and save it as ..., or paste a screenshot." Do just that. Never
  fetch it yourself.
- Exit code 1 means nothing new.
- Run it once a day after the close. With the site on, it asks the site each
  time, so `/loop 2h` while the market is open is plenty.

## Step 3: read one notice

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py" read 290210
python3 "${CLAUDE_SKILL_DIR}/insiders.py" parse notice.txt --filed 2026-10-29 --symbol DEMO --json
```

- `read` finds the doc id on the company pages and reads it like `new --read`.
- `parse` reads `pdftotext -layout` text. It knows both PSX layouts: the
  Form-29 table, and a company's own table (like SYS).
- `--filed` is the day PSX posted it. Without it, the letter's own date is used.
- `--symbol` adds the closed period check, "You hold", and the day's range.
- Check the row count against the notice when it runs over pages.

## Example

The sample market (`tests/fixtures/psx-market`) copied to `psx/data`. It is all made up: every person,
trade, notice and price below is invented for the tests, even where a real company's symbol is used.
Thursday 2026-10-29, after the close, the first run:

```bash
python3 "${CLAUDE_SKILL_DIR}/insiders.py" new --read --symbols DEMO,SYS --offline --now "2026-10-29 16:00"
```

```
First run: notices posted since 2026-09-29 (the last 30 days). Older ones are marked as seen.

NEW insider notice: DEMO (Demo Industries Limited), doc 290210, posted 2026-10-29.
  PDF https://dps.psx.com.pk/download/document/290210.pdf
  GIF https://dps.psx.com.pk/download/image/290210-1.gif
Read from psx/data/docs/290210.txt: 2 trades.
  - Mr. Salman Akhtar (Director) bought 25,000 shares at 97.11 on 2026-10-28.
    Worth Rs 2,427,750.00. Ready market. Holds 1,265,000 shares after (0.51%).
    Posted 1 day after the trade.
    This trade date is inside the closed period DEMO announced (October 27 to November 3, 2026).
    PSX Rule Book 5.6.4 says directors, the CEO and executives may not deal in a closed period.
    Check the notice.
  - Mrs. Hina Salman (Spouse of Director) bought 10,000 shares at 97.26 on 2026-10-28.
    Worth Rs 972,600.00. Ready market. Holds 410,000 shares after (0.16%).
    Posted 1 day after the trade.
    This trade date is inside the closed period DEMO announced (October 27 to November 3, 2026).
    PSX Rule Book 5.6.4 says directors, the CEO and executives may not deal in a closed period.
    Check the notice.

NEW insider notice: SYS (Systems Limited), doc 290070, posted 2026-10-27.
  PDF https://dps.psx.com.pk/download/document/290070.pdf
  GIF https://dps.psx.com.pk/download/image/290070-1.gif
Read from psx/data/docs/290070.txt: 3 trades.
  - Mr. Danish Iqbal (Senior Management) sold 150,000 shares at 120.60 on 2026-10-20.
    Worth Rs 18,090,000.00. Ready market.
    Posted 7 days after the trade.
  - Mr. Danish Iqbal (Senior Management) sold 85,500 shares at 122.68 on 2026-10-21.
    Worth Rs 10,489,140.00. Ready market.
    Posted 6 days after the trade.
  - Ms. Mehwish Tariq (Executive) bought 5,000 shares at 124.16 on 2026-10-22.
    Worth Rs 620,800.00. Ready market.
    Posted 5 days after the trade.
  Closed period: not checked (no SYS board meeting notice text in docs/).

NEW insider notice: DEMO (Demo Industries Limited), doc 289612, posted 2026-10-23.
  GIF https://dps.psx.com.pk/download/image/289612-1.gif
Image only, no text to read. Ask the user to open https://dps.psx.com.pk/download/image/289612-1.gif in their browser and save it as psx/data/docs/289612.gif, or paste a screenshot. Do not fetch it yourself. Do not guess its rows.

Fast, not early: the insider has 7 days to tell the company. The notice on PSX can come later.
These are public PSX notices. There is no inside information here.
A notice is a reason to look at a chart, never a setup.
```

- The closed period comes from DEMO's board meeting notice (doc 289306,
  October 27 to November 3, 2026).
- Both DEMO rates sit inside DEMO's range on 2026-10-28 (95.76 to 98.46). A
  rate outside the day's range gets a note: maybe an off-market deal, or a
  misread.

If the user wants to act on it, it goes the long way. A notice is days old,
so it is a reason to look at a chart, not a setup. `/psx-scanner` and
`/psx-entry` decide if there is a trade today. Then `/psx-budget` sizes it,
`/psx-risk` checks it, and `/psx-broker` gives the line to type into the broker
app only after their yes.

## Pictures and scans: the manual route

Most insider notices on PSX are only a GIF image, or a scanned PDF with no
text. The tool cannot read those. Claude can. With the site off, Claude
never opens the PSX link itself. The route:

1. The user opens the GIF link (or the PDF) in their browser.
2. They save it as `psx/data/docs/<id>.gif` (or `.pdf`), or paste a
   screenshot into the chat.
3. Claude reads the table and writes it up in the same shape as the tool's
   rows: who, role, buy or sell, shares, rate, date, holding after.
4. Claude adds the doc id and link, the days from the trade to the posting,
   the closed period check, and the "fast, not early" line.
5. A row Claude cannot read stays unread. Say so. Never guess it.

## What it never does

- Never says or implies that anyone has inside information or broke a rule.
  A closed period flag is a date, the rule, and "Check the notice."
- Never says that copying an insider beats the market. No return claims.
- Never turns a notice into a setup or an order.
- Never uses the PSX site unless `"psx_site": true`, and never with
  `--offline` or `--now`.
- Never opens a `dps.psx.com.pk` link itself while the site is off.
- Never guesses the rows of a picture or a scan.

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
