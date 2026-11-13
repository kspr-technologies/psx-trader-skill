"""news/*.xml: made-up RSS 2.0 items in each real feed's layout and date style. Headlines and text
are made up; they only test matching (ticker, name, the AHL source trap) and dates."""

import os

from .util import write_text

# feed: [(pubDate, title, slug id, text)], newest first
ITEMS = {
    "brecorder-markets.xml": [
        ("Thu, 29 Oct 2026 16:42:10 +0500", "KSE-100 gains 1,265 points as banks and cement lead", 40447120,
         "The KSE-100 index closed higher on Thursday. Among the most active shares, DEMO rose 3.25 percent to "
         "Rs101.60 after its quarterly results, while OGDC and HUBC also gained."),
        ("Thu, 29 Oct 2026 15:58:31 +0500", "OGDC moves board meeting to a later time", 40447085,
         "OGDC said its board meeting on the quarterly accounts will now start at a later time on the same day."),
        ("Thu, 29 Oct 2026 13:20:44 +0500", "Banks may lead earnings growth, says brokerage", 40446990,
         "\"Bank earnings are likely to grow in the coming quarter,\" AHL said in a note to clients on Thursday."),
        ("Thu, 29 Oct 2026 11:05:12 +0500", "Rupee steady against dollar in interbank market", 40446911,
         "The rupee was little changed against the US dollar in the interbank market on Thursday."),
        ("Thu, 29 Oct 2026 09:12:40 +0500", "Hubco to hold board meeting on quarterly results", 40446850,
         "The Hub Power Company Limited (Hubco) said its board will meet next week to review the quarter."),
        ("Wed, 28 Oct 2026 17:35:02 +0500", "Lucky Cement says despatches rose in October", 40446610,
         "Lucky Cement said local despatches rose in the first three weeks of October."),
        ("Wed, 28 Oct 2026 12:10:55 +0500", "SBP keeps policy rate unchanged", 40446533,
         "The State Bank of Pakistan (SBP) kept its policy rate unchanged at its meeting on Wednesday."),
    ],
    "dawn-business.xml": [
        ("Thu, 29 Oct 2026 19:05:18 +0500", "KSE-100 ends higher as results season picks up", 2040811,
         "Shares closed higher on Thursday as investors bought ahead of more quarterly results."),
        ("Thu, 29 Oct 2026 10:41:07 +0500", "IMF mission to review progress next month", 2040790,
         "An IMF mission will visit Pakistan next month to review the economic programme."),
        ("Wed, 28 Oct 2026 20:14:33 +0500", "Demo Industries wins Rs1.2bn packaging order", 2040702,
         "Demo Industries said it has signed a two-year contract to supply packaging goods worth about Rs1.2 billion."),
        ("Wed, 28 Oct 2026 16:02:49 +0500", "Meezan Bank opens new branches in Sindh", 2040675,
         "Meezan Bank said it has opened new branches in several towns of Sindh."),
        ("Wed, 28 Oct 2026 08:30:12 +0500", "Petrol price unchanged for October 29", 2040610,
         "The government kept the petrol price unchanged for Thursday under the daily pricing system."),
        ("Wed, 28 Oct 2026 07:42:51 +0500", "Business community split over SBP policy rate decision", 2040598,
         "Business groups gave mixed views on the SBP's latest policy rate decision."),
    ],
    "tribune-business.xml": [
        ("Thu, 29 Oct 26 17:20:11 +0500", "Stocks extend gains as KSE-100 crosses 157,000", 2637702,
         "The benchmark KSE-100 index crossed the 157,000 mark on Thursday."),
        ("Thu, 29 Oct 26 12:48:05 +0500", "OGDC says new well starts production", 2637655,
         "OGDC said a new well in Sindh has started producing gas."),
        ("Thu, 29 Oct 26 08:45:53 +0500", "What the SBP policy rate decision means for borrowers", 2637611,
         "Borrowers will see no change in their loan rates after the State Bank kept the policy rate steady."),
        ("Wed, 28 Oct 26 21:18:44 +0500", "Lucky Cement to expand plant", 2637540,
         "Lucky Cement said it plans to add a new production line at one of its plants."),
        ("Wed, 28 Oct 26 18:02:19 +0500", "IMF says reforms on track", 2637501,
         "The IMF said Pakistan's reforms are on track in a statement on Wednesday."),
        ("Wed, 28 Oct 26 11:30:40 +0500", "Gold price falls Rs1,500 per tola", 2637455,
         "Gold fell Rs1,500 per tola in the local market on Wednesday."),
    ],
    "propakistani-business.xml": [
        ("Thu, 29 Oct 2026 13:47:34 +0000", "PSX Wrap: KSE-100 Gains 0.8% in Results Week", 1109170,
         "The KSE-100 index gained 0.8 percent on Thursday as banks and energy shares rose."),
        ("Thu, 29 Oct 2026 11:15:09 +0000", "Hubco Board to Meet on Quarterly Accounts", 1109151,
         "Hubco will hold a board meeting next week to review its quarterly accounts."),
        ("Thu, 29 Oct 2026 08:20:41 +0000", "SBP Reserves Rise by $120 Million", 1109112,
         "Reserves held by the State Bank of Pakistan rose by $120 million in the week, the SBP said."),
        ("Wed, 28 Oct 2026 15:16:09 +0000", "Meezan Bank Launches New Digital Account", 1109042,
         "Meezan Bank has launched a new digital account that can be opened from a phone."),
        ("Wed, 28 Oct 2026 10:05:27 +0000", "Cement Sales Up 8% in October So Far", 1109001,
         "Cement despatches rose 8 percent in the first three weeks of October, industry data showed."),
        ("Wed, 28 Oct 2026 06:40:55 +0000", "Petrol Price to Stay the Same for October 29", 1108980,
         "The petrol price will stay the same for October 29, a notification said."),
    ],
}
CHANNEL = {
    "brecorder-markets.xml": ("Business Recorder - Markets", "https://www.brecorder.com/", "Business Recorder",
                              "Fri, 30 Oct 2026 00:19:42 +0500"),
    "dawn-business.xml": ("Dawn - Business", "https://www.dawn.com/", "Dawn", "Fri, 30 Oct 2026 00:06:27 +0500"),
}


def esc(s):
    """XML text escape."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slug(title):
    """a-title-like-this."""
    return "-".join("".join(ch for ch in w.lower() if ch.isalnum()) for w in title.split()).strip("-")


def recorder_like(name):
    """Business Recorder and Dawn: the same layout (+0500 dates, escaped HTML in description)."""
    title, link, desc, built = CHANNEL[name]
    base = "https://www.brecorder.com/news/" if "brecorder" in name else "https://www.dawn.com/news/"
    cat = "Markets" if "brecorder" in name else "Business"
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<rss xmlns:media="http://search.yahoo.com/mrss/" '
           'xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">\n  <channel>\n'
           '    <title>%s</title>\n    <link>%s</link>\n    <description>%s</description>\n'
           '    <language>en-Us</language>\n    <copyright>Copyright 2026</copyright>\n    <pubDate>%s</pubDate>\n'
           '    <lastBuildDate>%s</lastBuildDate>\n    <ttl>60</ttl>\n    \n' % (title, link, desc, built, built)]
    for when, head, n, text in ITEMS[name]:
        url = "%s%d/%s" % (base, n, slug(head))
        out.append('<item xmlns:default="http://purl.org/rss/1.0/modules/content/">\n      <title>%s</title>\n'
                   '      <link>%s</link>\n      <description>&lt;p&gt;%s&lt;/p&gt;</description>\n'
                   '      <content:encoded xmlns="http://purl.org/rss/1.0/modules/content/"><![CDATA[<p>%s</p>\n'
                   ']]></content:encoded>\n      <category>%s</category>\n      <guid>%s%d</guid>\n'
                   '      <pubDate>%s</pubDate>\n      <author>none@none.com (Recorder Report)</author>\n    </item>\n'
                   % (esc(head), url, esc(esc(text)), esc(text), cat, base, n, when))
    return "".join(out) + "  </channel>\n</rss>\n"


def tribune():
    """Express Tribune: tabs, CDATA, a 2-digit year in dates."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>\n<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" '
           'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">'
           '<channel>\n                        <title>Latest Business News and Business News Headlines | Business'
           '</title>\n                        <link>https://tribune.com.pk</link>\n                        '
           '<lastBuildDate>Fri, 30 Oct 26 00:54:22 +0500</lastBuildDate>\n                        '
           '<language>en-US</language>\n']
    for when, head, n, text in ITEMS["tribune-business.xml"]:
        url = "https://tribune.com.pk/story/%d/%s" % (n, slug(head))
        out.append('<item>\n\t\t\t<title>%s</title>\n\t\t\t<link>%s</link>\n\t\t\t<comments>%s#comments</comments>\n'
                   '\t\t\t<pubDate>%s</pubDate>\n\t\t\t<dc:creator>\n\t\t\t\t<![CDATA[Our Correspondent]]>\n'
                   '\t\t\t</dc:creator>\n\t\t\t<category><![CDATA[Business]]></category>\n\t\t\t<guid '
                   'isPermaLink="false">https://tribune.com.pk/?p=%d</guid>\n\t\t\t<description>\n\t\t\t\t'
                   '<![CDATA[%s]]>\n\t\t\t</description>\n\t\t\t<content:encoded>\n\t\t\t\t<![CDATA[%s]]>\n'
                   '\t\t\t</content:encoded>\n\t\t\t</item>' % (esc(head), url, url, when, n, text, text))
    return "".join(out) + "\t</channel>\n                </rss>\n"


def propakistani():
    """ProPakistani: WordPress layout, +0000 dates."""
    out = ['<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"\n\txmlns:content="http://purl.org/rss/1.0/'
           'modules/content/"\n\txmlns:dc="http://purl.org/dc/elements/1.1/"\n\t>\n\n<channel>\n\t<title>Business - '
           'Latest News &amp; Updates | ProPakistani</title>\n\t<link>https://propakistani.pk/category/business/</link>'
           '\n\t<description>Technology and Business News</description>\n\t<lastBuildDate>Thu, 29 Oct 2026 18:50:55 '
           '+0000</lastBuildDate>\n\t<language>en-US</language>\n\t\n']
    for when, head, n, text in ITEMS["propakistani-business.xml"]:
        url = "https://propakistani.pk/2026/10/%s/%s/" % (when[5:7], slug(head))
        out.append('<item>\n\t\t<title>%s</title>\n\t\t<link>%s</link>\n\t\t\t\t\t<comments>%s#respond</comments>\n'
                   '\t\t\n\t\t<dc:creator><![CDATA[Business Desk]]></dc:creator>\n\t\t<pubDate>%s</pubDate>\n'
                   '\t\t\t\t<category><![CDATA[Business]]></category>\n\t\t<guid isPermaLink="false">'
                   'https://propakistani.pk/?p=%d</guid>\n\n\t\t\t\t\t<description><![CDATA[<p>%s</p>]]></description>\n'
                   '\t\t\t\t\t\t\t\t\t\t<content:encoded><![CDATA[<p>%s</p>]]></content:encoded>\n\t</item>\n'
                   % (esc(head), url, url, when, n, text, text))
    return "".join(out) + "\t</channel>\n</rss>\n"


def write_feeds(out):
    """The four feed files."""
    paths = []
    for name in ITEMS:
        if name in CHANNEL:
            text = recorder_like(name)
        elif name.startswith("tribune"):
            text = tribune()
        else:
            text = propakistani()
        p = os.path.join(out, "news", name)
        write_text(p, text)
        paths.append(p)
    return paths
