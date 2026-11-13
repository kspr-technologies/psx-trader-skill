"""The shared word test of the PSX pack (v2): new tool text must never read like advice or an order.

Standard library only, Python 3.8. Use it in a test on the text a tool prints:

    from _words import check_words
    check_words(self, out)          # self is the unittest.TestCase

The rules, line by line:
- No line starts with "Buy " or "Sell ". This is case-sensitive: a ticket's
  "SELL DEMO, REG board, ..." line is the order the user approved, not advice.
  A list mark in front ("- ", "* ", "1. ") does not hide it.
- No "real time", "real-time", "guarantee" or "you would have made" (in any case).
- No em dash.
- An action line ends with "You decide." or "You decide. Nothing is placed.".
  An action line has "could buy" or "could sell" (in any case), "You could" or "The rule says:".

Kept v1 lines are not new text. The ones that hold an old word are in V1_LINES and are
not checked. A test may pass more old lines with skip=.
"""

import re

BANNED = ("real time", "real-time", "guarantee", "you would have made")
ENDINGS = ("You decide.", "You decide. Nothing is placed.")
EM_DASH = chr(0x2014)
LIST_MARK = re.compile(r"^(?:[-*]\s+|\d+[.)]\s+)")
# v1 lines that stay word for word in v2 output (psx-exit's NOTE is in every plan it prints)
V1_LINES = ("A stop is a plan, not a guarantee: a gap or a fast market can fill past it.",)


def is_action(line):
    """True when the line offers an action: "could buy", "could sell", "You could" or "The rule says:"."""
    low = line.lower()
    return "could buy" in low or "could sell" in low or "You could" in line or "The rule says:" in line


def problems(text, skip=()):
    """Each line of text that breaks a rule, as "what: the line". [] when every line passes."""
    out = []
    keep = set(V1_LINES) | set(x.strip() for x in skip)
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line in keep:
            continue
        if LIST_MARK.sub("", line).startswith(("Buy ", "Sell ")):
            out.append('starts with "Buy " or "Sell ": ' + line)
        low = line.lower()
        for word in BANNED:
            if word in low:
                out.append('has "%s": %s' % (word, line))
        if EM_DASH in line:
            out.append("has an em dash: " + line)
        if is_action(line) and not line.endswith(ENDINGS):
            out.append('an action line that does not end with "You decide.": ' + line)
    return out


def check_words(testcase, text, skip=()):
    """Fail the test (testcase is a unittest.TestCase) when text breaks a word rule.
    Returns how many lines were checked, so a test can see it checked something."""
    found = problems(text, skip)
    if found:
        testcase.fail("the word rules (tests/_words.py) are broken:\n  " + "\n  ".join(found))
    return len([x for x in str(text or "").splitlines() if x.strip()])
