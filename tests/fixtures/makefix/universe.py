"""The sample universe: real PSX symbols and names (public listing facts), made-up numbers.

DEMO ("Demo Industries Limited") is a made-up company; DEMO is not a PSX symbol.
Every price, volume, share count and financial figure in the sample market is made up.
"""

# ---------------------------------------------------------------- ordinary shares
# sym, sector, short name, full name, price near 10-28 (made up), usual volume (made up),
# shares (millions, made up), free float %, index list, daily role, opening-range role

ORDINARY = [
    ("DEMO", "0818", "Demo Industries", "Demo Industries Limited", 98.40, 2600000, 250, 30,
     "ALLSHR,KMIALLSHR", "breakout_20d", "orb@8"),
    ("OGDC", "0820", "Oil & Gas Dev", "Oil & Gas Development Company Limited", 281.0, 3200000, 4300, 15,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30,OGTI", "", "orb@4"),
    ("PPL", "0820", "Pak Petroleum", "Pakistan Petroleum Limited", 188.0, 4100000, 2720, 25,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30,OGTI", "bull_flag", ""),
    ("MARI", "0820", "Mari Energies Ltd.", "Mari Energies Limited", 642.0, 900000, 1200, 20,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30,OGTI", "", ""),
    ("POL", "0820", "Pak Oilfields", "Pakistan Oilfields Limited", 612.0, 850000, 280, 30,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30,OGTI", "", ""),
    ("HUBC", "0824", "Hub Power Co.", "The Hub Power Company Limited", 168.0, 3800000, 1300, 60,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "", "orb@3"),
    ("LUCK", "0804", "Lucky Cement", "Lucky Cement Limited", 468.0, 1500000, 1460, 40,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "breakout_20d", ""),
    ("DGKC", "0804", "D.G.K.Cement", "D.G. Khan Cement Company Limited", 176.0, 2400000, 440, 50,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "", ""),
    ("MLCF", "0804", "Maple Leaf", "Maple Leaf Cement Factory Limited", 94.5, 5200000, 1050, 45,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "", ""),
    ("FCCL", "0804", "Fauji Cement", "Fauji Cement Company Limited", 51.2, 6100000, 2450, 35,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "", ""),
    ("PIOC", "0804", "Pioneer Cement", "Pioneer Cement Limited", 228.0, 1100000, 227, 45,
     "ALLSHR,KMIALLSHR,KSE100", "", "missing"),
    ("MCB", "0807", "MCB Bank Ltd", "MCB Bank Limited", 331.0, 1300000, 1185, 40,
     "ALLSHR,KSE100,KSE30,BKTI", "", ""),
    ("HBL", "0807", "Habib Bank", "Habib Bank Limited", 279.0, 2700000, 1467, 40,
     "ALLSHR,KSE100,KSE30,BKTI", "", ""),
    ("UBL", "0807", "United Bank", "United Bank Limited", 392.0, 1900000, 2505, 30,
     "ALLSHR,KSE100,KSE30,BKTI", "reclaim_ma20", ""),
    ("MEBL", "0807", "Meezan Bank Ltd", "Meezan Bank Limited", 438.0, 1700000, 1790, 45,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30,BKTI", "", "orb@6"),
    ("NBP", "0807", "National Bank", "National Bank of Pakistan", 161.0, 4600000, 2127, 25,
     "ALLSHR,KSE100,KSE30,BKTI", "", ""),
    ("BAFL", "0807", "Bank Al-Falah", "Bank Alfalah Limited", 96.3, 3900000, 1577, 45,
     "ALLSHR,KSE100,KSE30,BKTI", "", ""),
    ("BOP", "0807", "B.O.Punjab", "The Bank of Punjab", 13.1, 16000000, 3305, 40,
     "ALLSHR,KSE100,BKTI", "", ""),
    ("ENGROH", "0813", "Engro Holdings", "Engro Holdings Limited", 262.0, 2100000, 1222, 50,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "", "orb@7"),
    ("FFC", "0809", "Fauji Fert", "Fauji Fertilizer Company Limited", 521.0, 1800000, 1423, 55,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "", "orb@5"),
    ("EFERT", "0809", "Engro Fertert", "Engro Fertilizers Limited", 209.0, 1600000, 1335, 40,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "", ""),
    ("SYS", "0828", "Systems Limited", "Systems Limited", 121.0, 3300000, 1470, 45,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "", ""),
    ("TRG", "0828", "TRG Pak Ltd", "TRG Pakistan Limited", 74.8, 6800000, 545, 60,
     "ALLSHR,KSE100,KSE30", "oversold", ""),
    ("AIRLINK", "0828", "Air Link Commun", "Air Link Communication Limited", 158.0, 5300000, 395, 35,
     "ALLSHR,KMIALLSHR,KSE100", "", ""),
    ("PSO", "0821", "P.S.O.", "Pakistan State Oil Company Limited", 418.0, 2500000, 470, 45,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "oversold_bounce", ""),
    ("ATRL", "0825", "Attock Refinery", "Attock Refinery Limited", 1185.0, 650000, 107, 35,
     "ALLSHR,KMIALLSHR,KSE100", "", ""),
    ("CNERGY", "0825", "Cnergyico PK", "Cnergyico PK  Limited", 8.25, 52000000, 5490, 25,
     "ALLSHR,KMIALLSHR,KSE100", "", ""),
    ("KEL", "0824", "K-Electric Ltd.", "K-Electric Limited", 6.12, 41000000, 27600, 25,
     "ALLSHR,KMIALLSHR,KSE100,KSE30", "breakout_20d", "orb@5"),
    ("SEARL", "0823", "The Searle Company", "The Searle Company Limited", 104.5, 2900000, 510, 50,
     "ALLSHR,KMI30,KMIALLSHR,KSE100,KSE30", "oversold", ""),
    ("NML", "0829", "Nishat Mills Ltd", "Nishat Mills Limited", 149.0, 420000, 351, 45,
     "ALLSHR,KSE100", "oversold", ""),
    ("ILP", "0829", "Interloop Ltd.", "Interloop Limited", 72.4, 1400000, 1400, 25,
     "ALLSHR,KMIALLSHR,KSE100", "", "absent"),
]

# ---------------------------------------------------------------- other kinds (edge cases)
# sym, sector, short name, full name, price, usual volume, shares (millions), free float %, kind

OTHERS = [
    ("BAPLR1", "0805", "Bawany Air(R)", "Bawany Air Products LimitedRight", 5.95, 650000, 30, 50, "right"),
    ("EPCLPS", "0805", "Engro Poly (PR)", "Engro Poly(Pref)", 14.2, 3000, 300, 40, "preference"),
    ("GEMMEL", "0824", "Mughal Energy Ltd", "Mughal Energy Limited(GEM)", 36.5, 4000, 120, 20, "gem_equity"),
    ("ANLNV", "0829", "Azgard(Non-Voti)(PR)", "Azgard Nine Non-Voting Ordinary Shares", 7.45, 900, 14, 30,
     "non_voting"),
    ("MZNPETF", "0837", "Meezan Pakistan", "Meezan Pakistan ETF", 17.05, 600000, 90, 100, "etf"),
    ("FEM", "0819", "Equity Modaraba", "First Equity Modaraba", 11.6, 90000, 52, 50, "modaraba"),
    ("DCR", "0836", "Dolmen City", "Dolmen City REIT", 39.1, 210000, 2224, 25, "reit"),
]
DEBT = ("P01GIS210127", "36", "1 Year GIS", 97.3)

# ---------------------------------------------------------------- futures (from 2026-08-31)
# sym, sector, underlying (None for an index), premium % near the start, trades on: "all", "some", "none"

FUTURES = [
    ("DEMO-NOV", "40", "DEMO", 1.4, "some"),
    ("KSE30-NOV", "41", None, 1.2, "none"),
    ("OGDC-NOV", "40", "OGDC", 1.3, "some"),
    ("OGDC-NOVB", "40", "OGDC", 1.1, "all"),
]
KSE30_LEVEL = 47512.36   # made-up KSE-30 level near 10-28

# ---------------------------------------------------------------- special days

XD_EVENTS = {             # symbol: (ex-date, dividend Rs, last day the short name shows XD)
    "HUBC": ("2026-10-08", 5.00, "2026-10-14"),
    "HBL": ("2026-10-29", 4.00, "2026-10-29"),
}
ABSENT = {"ILP": ("2026-10-15", "2026-10-29")}   # no row on these days: it did not trade
NO_REG_TRADE = ("NML", "2026-10-13")             # OPEN/HIGH/LOW 0 with VOLUME > 0, CLOSE = LDCP
THIN_CLOSE = ("GEMMEL", "2026-10-29")            # CLOSE outside [LOW, HIGH], equal to LDCP
NC_TAG = ("ANLNV",)                              # screener shows an NC tag
COMPANY_PAGES = ("DEMO", "OGDC", "HBL", "FFC", "SYS")


def row_of(sym):
    """The ORDINARY row of a symbol, or None."""
    for r in ORDINARY:
        if r[0] == sym:
            return r
    return None


def listed():
    """(sym, sector, short, full, price, volume, shares_mn, ff_pct, kind) of every listed stock
    the screener shows (no futures, no debt)."""
    out = [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], "ordinary") for r in ORDINARY]
    return out + [tuple(r) for r in OTHERS]
