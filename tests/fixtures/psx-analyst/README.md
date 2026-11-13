# psx-analyst test pages (made up)

All data in this folder is made up. It is not real PSX data.

- `company/ZLAG.html`: the page lags its newest results notice. A board meeting is in progress.
- `company/ZONE.html`: one-off items: profit above sales, and profit growth far above sales growth.
- `company/ZRGT.html`: a right, so the page has no financials.
- ZLAG, ZONE and ZRGT are not PSX symbols. The companies are made up.
- The pages use the layout of a saved PSX company page.

Rebuild them (the same files every time):

```
cd tests/fixtures/psx-analyst
python3 make.py
```

The tests also use the shared sample market in `tests/fixtures/psx-market/`.
Notice files (`docs/<id>.txt` or `.pdf`) for these pages are made by the tests in temp folders.
