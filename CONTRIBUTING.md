# Contributing

Thank you for helping. This pack is for people who invest or trade on PSX.
Please keep it safe, plain and honest.

## Run the tests

You need Python 3.8 or newer. There is nothing to pip install.

```bash
cd tests
python3 -m unittest discover
```

- The tests run offline, on made-up data only. They take a few minutes.
- Run them all before you open a pull request. They must all pass.
- GitHub runs them too, on each push and pull request: on Linux and macOS,
  with Python 3.8, 3.10 and 3.12.
- `tests/test_chain.py` runs whole trades through the real command lines.
- It also checks each command in README.md against its tool's `--help`, and
  some of the README's own words. So a README change can fail a test. The
  failure names the line.

## The rules

### 1. Nothing places an order

- No code may place, send or change an order at a broker.
- Every order is a ticket. psx-risk must pass that exact order. Then the user
  says yes in the chat, and types the order into their own broker app.
- Paper trading stays the default: `"paper": true` in
  `psx-budget/config.example.json`.
- Auto-run must never run psx-broker, psx-budget or psx-risk.

### 2. Never ship real PSX data

- PSX's site terms ban automated tools without PSX's written permission. They
  also ban sharing PSX data.
- So `"psx_site"` stays `false` by default.
- Test data is made up, in PSX's real file formats. Never add a real closing
  file, a saved PSX page, a real notice or real prices.
- To change the sample market, change its generator in
  `tests/fixtures/makefix/`. Then rebuild it:
  `cd tests/fixtures && python3 make_fixtures.py`. It gives the same files
  every time.
- The swing test files: `cd tests/fixtures/psx-v2 && python3 make.py`.
- Never add personal data: no account numbers, emails, phone numbers, real
  trades or holdings. Not in code, tests, docs or issues.

### 3. Very easy English

- Docs and tool output use short words and short sentences. One idea a
  sentence.
- Use bullets and headings, not big blocks of text.
- No drama words. No em dash.
- A signal, a setup or an alert is a rule that is met. Never write it as
  advice or as a forecast.
- A line that offers an action ends with "You decide."
- `tests/_words.py` checks these words on tool output. Use it in a new test:
  `check_words(self, out)`.

### 4. Plain Python 3.8

- Standard library only. No packages.
- No Python 3.9 or newer features. `tests/test_repo.py` checks this.
- The shared helpers live in `tests/psx_core.py`. Each tool copies the ones it
  uses, word for word. Change the helper there first, then every copy.
  `tests/test_repo.py` checks that the copies match.

## Pull requests

- One change a pull request. Say what it changes and why.
- Add or change a test for each change in what a tool does.
- A PSX rule changed (hours, holidays, costs, limits)? Name the PSX notice.
- Run the whole test suite first.
