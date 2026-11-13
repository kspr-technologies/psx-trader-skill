"""Announcement rows of the five company pages (made up), with document ids that rise over time."""

import datetime

INSIDER = ("Disclosure of Interest by a Director CEO, or Executive of a listed company and their Spouses and "
           "the Substantial Shareholders u/c 5.6.1.(d) of PSX Regulations")
TABS = ("Financial Results", "Board Meetings", "Others")
ORDER = ("DEMO", "OGDC", "HBL", "FFC", "SYS")

# symbol: {tab: [(date, title, files)]}, newest first. files: "pdf" = GIF and PDF, "gif" = GIF only.
ROWS = {
    "DEMO": {
        "Financial Results": [
            ("2026-10-27", "FINANCIAL RESULTS FOR THE QUARTER ENDED SEPTEMBER 30, 2026", "pdf"),
            ("2026-09-28", "Transmission of Annual Report for the Year Ended June 30, 2026", "pdf"),
            ("2026-09-03", "FINANCIAL RESULTS FOR THE YEAR ENDED JUNE 30, 2026", "pdf"),
            ("2026-04-28", "FINANCIAL RESULTS FOR THE PERIOD ENDED MARCH 31, 2026", "pdf"),
            ("2026-02-25", "FINANCIAL RESULTS FOR THE HALF YEAR ENDED DECEMBER 31, 2025", "pdf")],
        "Board Meetings": [
            ("2026-10-27", "Board Meeting In Progress", "pdf"),
            ("2026-10-20", "NOTICE OF BOARD MEETING", "pdf"),
            ("2026-10-13", "Board Meeting", "pdf"),
            ("2026-09-22", "Notice of Annual General Meeting", "pdf"),
            ("2026-08-20", "BOARD MEETING AND CLOSED PERIOD", "pdf")],
        "Others": [
            ("2026-10-29", INSIDER, "pdf"),
            ("2026-10-28", "Material Information", "pdf"),
            ("2026-10-23", INSIDER, "gif"),
            ("2026-09-30", "NOTICE TO SHAREHOLDERS- UNCLAIMED SHARES AND DIVIDENDS", "pdf"),
            ("2026-08-25", "Change of Company Secretary", "pdf")],
    },
    "OGDC": {
        "Financial Results": [
            ("2026-09-24", "Transmission of Annual Report for the Year Ended June 30, 2026", "pdf"),
            ("2026-09-03", "FINANCIAL RESULTS FOR THE YEAR ENDED JUNE 30, 2026", "pdf"),
            ("2026-04-29", "Transmission of Quarterly Report for the Period Ended March 31, 2026", "pdf"),
            ("2026-04-28", "FINANCIAL RESULTS FOR THE PERIOD ENDED MARCH 31 2026", "pdf"),
            ("2026-02-26", "Transmission of Half Yearly Report for the period ended December 31, 2025", "pdf")],
        "Board Meetings": [
            ("2026-10-29", "Board Meeting Rescheduled (Change in Time)", "pdf"),
            ("2026-10-22", "NOTICE OF BOARD MEETING", "pdf"),
            ("2026-10-15", "RESOLUTIONS PASSED BY THE SHAREHOLDERS AT THE ANNUAL GENERAL MEETING", "pdf"),
            ("2026-09-23", "Notice of Annual General Meeting", "pdf"),
            ("2026-08-25", "BOARD MEETING AND CLOSED PERIOD", "pdf")],
        "Others": [
            ("2026-10-28", "CREDIT OF FINAL CASH DIVIDEND", "pdf"),
            ("2026-10-21", "Commencement of Production from a New Well", "pdf"),
            ("2026-09-08", "Material Information", "pdf"),
            ("2026-08-17", "NOTICE TO SHAREHOLDERS- UNCLAIMED SHARES AND DIVIDENDS", "pdf"),
            ("2026-07-09", "Appointment of Chief Financial Officer", "pdf")],
    },
    "HBL": {
        "Financial Results": [
            ("2026-10-22", "FINANCIAL RESULTS FOR THE NINE MONTHS ENDED SEPTEMBER 30, 2026", "pdf"),
            ("2026-08-27", "Transmission of Half Yearly Report for the period ended June 30, 2026", "pdf"),
            ("2026-07-29", "FINANCIAL RESULTS FOR THE HALF YEAR ENDED JUNE 30, 2026", "pdf"),
            ("2026-04-29", "FINANCIAL RESULTS FOR THE QUARTER ENDED MARCH 31, 2026", "pdf"),
            ("2026-03-30", "Transmission of Annual Report for the Year Ended December 31, 2025", "pdf")],
        "Board Meetings": [
            ("2026-10-22", "Board Meeting In Progress", "pdf"),
            ("2026-10-14", "Board Meeting", "pdf"),
            ("2026-07-29", "Board Meeting In Progress", "pdf"),
            ("2026-07-20", "Board Meeting", "pdf"),
            ("2026-04-20", "Board Meeting", "pdf")],
        "Others": [
            ("2026-10-27", "BOOK CLOSURE FOR INTERIM CASH DIVIDEND", "pdf"),
            ("2026-10-08", "Material Information", "pdf"),
            ("2026-09-15", INSIDER, "gif"),
            ("2026-08-12", "Credit Rating", "pdf"),
            ("2026-07-02", "Change in Directorship", "pdf")],
    },
    "FFC": {
        "Financial Results": [
            ("2026-10-28", "FINANCIAL RESULTS FOR THE THIRD QUARTER ENDED SEPTEMBER 30, 2026", "pdf"),
            ("2026-08-27", "Transmission of Half Yearly Report for the period ended June 30, 2026", "pdf"),
            ("2026-07-30", "FINANCIAL RESULTS FOR THE HALF YEAR ENDED JUNE 30, 2026", "pdf"),
            ("2026-04-27", "FINANCIAL RESULTS FOR THE QUARTER ENDED MARCH 31, 2026", "pdf"),
            ("2026-03-25", "Transmission of Annual Report for the Year Ended December 31, 2025", "pdf")],
        "Board Meetings": [
            ("2026-10-28", "Board Meeting In Progress", "pdf"),
            ("2026-10-19", "Board Meeting for Announcement of Financial Results for the Third Quarter ended "
                           "September 30, 2026", "pdf"),
            ("2026-07-30", "Board Meeting In Progress", "pdf"),
            ("2026-07-20", "Board Meeting", "pdf"),
            ("2026-04-17", "Board Meeting", "pdf")],
        "Others": [
            ("2026-10-29", INSIDER, "gif"),
            ("2026-10-12", "Material Information", "pdf"),
            ("2026-09-03", "CREDIT OF INTERIM CASH DIVIDEND", "pdf"),
            ("2026-08-13", "Material Information", "pdf"),
            ("2026-07-07", "Change of Company Secretary", "pdf")],
    },
    "SYS": {
        "Financial Results": [
            ("2026-10-26", "Financial Results for the Nine Months Ended September 30, 2026", "pdf"),
            ("2026-08-24", "Financial Results for the Half Year Ended June 30, 2026", "pdf"),
            ("2026-04-28", "Financial Results for the Quarter Ended March 31, 2026", "pdf"),
            ("2026-03-31", "Transmission of Annual Report for the Year Ended December 31, 2025", "pdf"),
            ("2026-02-24", "Financial Results for the Year Ended December 31, 2025", "pdf")],
        "Board Meetings": [
            ("2026-10-26", "Board Meeting In Progress", "pdf"),
            ("2026-10-16", "Board Meeting", "pdf"),
            ("2026-08-24", "Board Meeting In Progress", "pdf"),
            ("2026-08-12", "Board Meeting", "pdf"),
            ("2026-04-17", "Board Meeting", "pdf")],
        "Others": [
            ("2026-10-27", INSIDER, "pdf"),
            ("2026-10-09", "Notice of Corporate Briefing Session", "gif"),
            ("2026-09-22", "Material Information", "pdf"),
            ("2026-08-31", "Shariah Disclosure for the Half Year Ended June 30, 2026", "pdf"),
            ("2026-07-09", "Notice of Corporate Briefing Session for year ended December 31, 2025", "gif")],
    },
}


def doc_id(sym, tab, i):
    """The document id of row i of a tab: 260000 + day of the year x 100 + a small number,
    so ids rise with the date and never repeat."""
    d = datetime.date.fromisoformat(ROWS[sym][tab][i][0])
    salt = ORDER.index(sym) * 15 + TABS.index(tab) * 5 + i
    return 260000 + d.timetuple().tm_yday * 100 + salt


def find(sym, tab, title_start):
    """(date, title, files, doc id) of the first row of a tab whose title starts with title_start."""
    for i, (d, title, files) in enumerate(ROWS[sym][tab]):
        if title.startswith(title_start):
            return d, title, files, doc_id(sym, tab, i)
    raise KeyError("%s %s %s" % (sym, tab, title_start))
