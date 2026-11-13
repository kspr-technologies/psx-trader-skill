import ast
import json
import os
import re
import unittest
from urllib.parse import urlparse

from _load import ROOT

try:
    import yaml                  # optional: only to check front_value against a real YAML reader
except ImportError:
    yaml = None

# the PSX pack: thirteen skills, one script each
SKILLS = {"psx-scanner": "scan.py", "psx-news": "news.py", "psx-entry": "entry.py", "psx-exit": "exit.py",
          "psx-timemachine": "timemachine.py", "psx-risk": "check.py", "psx-budget": "size.py",
          "psx-broker": "ticket.py", "psx-alerts": "alerts.py", "psx-analyst": "facts.py",
          "psx-insiders": "insiders.py", "psx-invest": "invest.py", "psx-autorun": "autorun.py"}
ABOUT = ("13 Claude skills for the Pakistan Stock Exchange: scan PSX charts for setups, track the news, plan day or "
         "swing trades, replay a trade 10,000 times before you risk a rupee, get rule-based signals for the shares "
         "you hold, and let your Mac run the checks for you.")
# skills on the way to an order carry the same safety block, word for word
ORDER_PATH = ["psx-scanner", "psx-entry", "psx-exit", "psx-timemachine", "psx-budget", "psx-insiders",
              "psx-risk", "psx-broker", "psx-alerts", "psx-invest"]
# the "swing" block of the example config (SPEC v2 section 2.1), key for key and in this order
SWING_KEYS = ["stop", "target", "max_hold_sessions", "trend_exit_sessions", "entry_buffer_pct", "max_open_positions",
              "max_open_risk_pkr", "max_gap_loss_pkr", "stop_limit_gap_pct"]
# a US part left behind (the README may still credit the US pack it came from)
US_WORDS = ["Alpaca", "Yahoo", "Stooq", "Nasdaq", "EDGAR", "NYSE", "FINRA", "New York", "Pelosi", "Congress",
            "TSLA"]
# "trader-" or "trader/" from the US pack, but not the pack's own name in the shared User-Agent
US_PATTERNS = [r"\bSEC\b", r"\bET\b", r"\$\s?\d", r"(?<!psx-)trader[-/]"]
CORE = os.path.join(ROOT, "tests", "psx_core.py")   # the canonical helpers every script copies
GENERATOR = os.path.join(ROOT, "tests", "fixtures", "makefix", "common.py")
PLUGIN_JSON = os.path.join(ROOT, ".claude-plugin", "plugin.json")
MARKET_JSON = os.path.join(ROOT, ".claude-plugin", "marketplace.json")
# Agent Skills limits (docs: platform.claude.com agent-skills overview, and agentskills.io/specification)
MAX_DESCRIPTION = 1024
MAX_NAME = 64
SKILL_NAME = r"^[a-z0-9]+(-[a-z0-9]+)*$"     # a-z, 0-9 and single hyphens, no hyphen at either end
# your own data: git must never pick these up (psx/ is your trading folder, autorun/ holds auto-run's reports)
IGNORED = ["__pycache__/", "*.pyc", ".DS_Store", "psx/", "autorun/", "*.log"]


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def frontmatter(path):
    text = read(path)
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def front_value(fm, key):
    """One top-level key of a front matter, read the way YAML reads it, with the standard library only: a block
    scalar (| or >), a quoted string or a plain one. Line breaks fold, so a description is measured as Claude
    gets it."""
    lines = fm.split("\n")
    for i, line in enumerate(lines):
        if line.startswith(key + ":"):
            break
    else:
        return None
    head = line[len(key) + 1:].strip()
    body = []
    for line in lines[i + 1:]:
        if line[:1] not in ("", " ", "\t"):
            break                                     # the next key
        body.append(line)
    if head[:1] in ("|", ">"):
        return block_scalar(head, body)
    words = ([head] if head else []) + [x.strip() for x in body]
    while words and not words[0]:
        words.pop(0)
    while words and not words[-1]:
        words.pop()
    text = ""
    for w in words:                                   # a line break folds to a space, an empty line is a "\n"
        if not w:
            text += "\n"
        elif text and not text.endswith("\n"):
            text += " " + w
        else:
            text += w
    if len(text) > 1 and text[0] == text[-1] == '"':
        try:
            return json.loads(text, strict=False)
        except ValueError:
            return text[1:-1]
    if len(text) > 1 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def block_scalar(head, body):
    """A YAML block scalar, as PyYAML's scanner reads it. "|" keeps each line break. ">" folds the break between
    two lines that do not start with a space into one space. "-" drops the last break, "+" keeps the empty lines
    at the end too."""
    folded = head[0] == ">"
    flags = head[1:3]
    chomp = "-" if "-" in flags else "+" if "+" in flags else ""
    digits = [c for c in flags if c.isdigit()]
    filled = [x for x in body if x.strip()]
    indent = int(digits[0]) if digits else (len(filled[0]) - len(filled[0].lstrip(" ")) if filled else 0)
    chunks, breaks, line_break, i = [], [], "", 0
    while i < len(body) and not body[i].strip():
        breaks.append("\n")
        i += 1
    while i < len(body):
        chunks.extend(breaks)
        text = body[i][indent:]
        plain = text[:1] not in (" ", "\t")
        chunks.append(text)
        line_break, breaks, i = "\n", [], i + 1
        while i < len(body) and not body[i].strip():
            breaks.append("\n")
            i += 1
        if i < len(body):
            if folded and plain and body[i][indent:][:1] not in (" ", "\t"):
                if not breaks:
                    chunks.append(" ")
            else:
                chunks.append(line_break)
    if chomp != "-":
        chunks.append(line_break)
    if chomp == "+":
        chunks.extend(breaks)
    return "".join(chunks)


def safety_block(text):
    m = re.search(r"^## Safety\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else None


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml", ".html", ".jsonl")) or f == "LICENSE":
                yield os.path.join(base, f)


def top_level(path):
    """{name: source text} of a file's top-level functions and simple assignments."""
    src = read(path)
    out = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef):
            out[node.name] = ast.get_source_segment(src, node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = ast.get_source_segment(src, node)
    return out


class Repo(unittest.TestCase):
    def test_thirteen_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))
        for skill, script in SKILLS.items():
            self.assertTrue(os.path.isfile(os.path.join(ROOT, skill, script)), skill)

    def test_root_layout(self):
        # the pack, and the usual repo docs if they are there; nothing else (no trading folder, no notes)
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith(".git") and d != ".DS_Store")
        need = [".claude-plugin", "LICENSE", "README.md", "tests"] + list(SKILLS)
        may = ["CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md"]
        for name in need:
            self.assertIn(name, root)
        self.assertEqual([d for d in root if d not in need + may], [])

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_names_and_descriptions_fit_the_agent_skills_limits(self):
        # Agent Skills: a name of 1 to 64 characters (a-z, 0-9, single hyphens; no "anthropic" or "claude") that
        # is its folder's name, and a description of 1 to 1,024 characters with no XML tags. The length counts
        # after YAML folds the lines, as Claude gets it.
        for s in SKILLS:
            fm, _ = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            name, about = front_value(fm, "name"), front_value(fm, "description")
            self.assertEqual(name, s)
            self.assertLessEqual(len(name), MAX_NAME, s)
            self.assertRegex(name, SKILL_NAME)
            for word in ("anthropic", "claude"):
                self.assertNotIn(word, name, s)
            self.assertTrue(about and about.strip(), "%s has no description" % s)
            self.assertLessEqual(len(about), MAX_DESCRIPTION, "%s: the description is %d characters after folding, "
                                 "over %d" % (s, len(about), MAX_DESCRIPTION))
            self.assertNotRegex(about, r"<[A-Za-z/!?][^<>]*>", s)

    def test_front_value_folds_like_yaml(self):
        fm = ("a: >-\n  one\n  two\n\n  three\n    four\n"
              "b: plain\n  more\n\n  after\n"
              "c: |\n  x\n  y\n"
              "d: 'it''s'\n"
              'e: "q \\"w\\""\n'
              "f: >\n  p\n  q\n\n\n"
              "g: >2-\n   lead\n  next\n"
              "h:\n  only body\n  two")
        want = {"a": "one two\nthree\n  four", "b": "plain more\nafter", "c": "x\ny\n", "d": "it's", "e": 'q "w"',
                "f": "p q\n", "g": " lead\nnext", "h": "only body two"}
        for key, value in want.items():
            self.assertEqual(front_value(fm, key), value, key)
        self.assertIsNone(front_value(fm, "nope"))

    @unittest.skipUnless(yaml, "PyYAML is not installed (front_value is still checked by its own examples)")
    def test_front_value_reads_every_skill_like_pyyaml(self):
        for s in SKILLS:
            fm, _ = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            want = yaml.safe_load(fm + "\n")
            for key in want:
                self.assertEqual(front_value(fm, key), want[key], "%s: %s" % (s, key))

    def test_order_path_skills_carry_one_safety_block(self):
        blocks = {}
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            block = safety_block(text)
            self.assertIsNotNone(block, s)
            low = " ".join(block.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("yes", low, s)
            self.assertIn("nothing in this pack places an order", low, s)
            blocks[s] = block
        self.assertEqual(len(set(blocks.values())), 1, "the Safety blocks differ: %s" % sorted(blocks))

    def test_plugin_manifest_points_at_the_root_skills(self):
        plugin = json.loads(read(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(plugin["name"], "psx-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        market = json.loads(read(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
        self.assertEqual(market["name"], "psx-trader-skill")
        self.assertEqual(market["plugins"][0]["name"], "psx-trader-skill")
        self.assertEqual(market["plugins"][0]["source"], "./")

    def test_plugin_and_marketplace_agree(self):
        # Claude Code docs (plugins/marketplace-reference, plugins/host-marketplace): the install id is
        # <entry name>@<marketplace name>, and the entry name must be the plugin.json name. The version is set in
        # plugin.json and not again in the entry (plugin.json would win). The marketplace's own version is the
        # same release.
        plugin = json.loads(read(PLUGIN_JSON))
        market = json.loads(read(MARKET_JSON))
        self.assertEqual(len(market["plugins"]), 1)
        entry = market["plugins"][0]
        self.assertEqual(entry["name"], plugin["name"])
        self.assertEqual(market["name"], plugin["name"])
        self.assertRegex(plugin["name"], SKILL_NAME)
        self.assertRegex(plugin["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(market.get("version", market.get("metadata", {}).get("version")), plugin["version"])
        self.assertNotIn("version", entry)
        # one author: the same person owns the marketplace
        self.assertEqual(plugin["author"], market["owner"])
        self.assertTrue(plugin["author"]["name"].strip())
        for who in (plugin["author"], market["owner"]):
            self.assertNotRegex(json.dumps(who).lower(), r"claude|anthropic")
        # the MIT license, and the original author's notice stays in it (the license asks for that)
        self.assertEqual(plugin["license"], "MIT")
        license_text = read(os.path.join(ROOT, "LICENSE"))
        self.assertIn("MIT License", license_text)
        self.assertIn("Permission is hereby granted, free of charge", license_text)
        self.assertIn("Jake Schincariol", license_text)
        # homepage must parse as a URL, or the plugin does not load
        for url in (plugin["homepage"], plugin["repository"], plugin["author"].get("url", "https://x.y")):
            parts = urlparse(url)
            self.assertEqual((parts.scheme, bool(parts.netloc)), ("https", True), url)

    def test_gitignore_keeps_your_data_out(self):
        rules = read(os.path.join(ROOT, ".gitignore")).split("\n")
        for rule in IGNORED:
            self.assertIn(rule, rules)
        # nothing the pack needs may sit in a folder git leaves out
        for base, dirs, _ in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for d in dirs:
                self.assertNotIn(d + "/", IGNORED, os.path.relpath(os.path.join(base, d), ROOT))

    def test_readme_names_every_skill_and_the_fine_print(self):
        readme = " ".join(read(os.path.join(ROOT, "README.md")).split())
        self.assertTrue(readme.startswith("# The PSX Day Trader skill"))
        for line in (ABOUT, "## Fine print", "psx_site"):
            self.assertIn(line, readme)
        self.assertIn("not financial advice", readme.lower())
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            rel = os.path.relpath(path, ROOT)
            text = read(path) if not rel.startswith(os.path.join("tests", "fixtures", "psx-market")) else ""
            self.assertNotIn(chr(0x2014), text, rel)
            self.assertIsNone(re.search(r"[\w.+-]+@gmail\.com", text), rel)

    def test_no_us_leftovers_in_the_skills(self):
        for s in SKILLS:
            folder = os.path.join(ROOT, s)
            for f in sorted(os.listdir(folder)):
                path = os.path.join(folder, f)
                if not os.path.isfile(path) or not f.endswith((".md", ".py", ".json")):
                    continue
                text = read(path)
                for word in US_WORDS:
                    self.assertNotIn(word, text, "%s/%s: %s" % (s, f, word))
                for pat in US_PATTERNS:
                    self.assertIsNone(re.search(pat, text), "%s/%s: %s" % (s, f, pat))

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools (the README promises 3.8)
        paths = [os.path.join(ROOT, s, p) for s in SKILLS for p in os.listdir(os.path.join(ROOT, s))
                 if p.endswith(".py")]
        for path in paths:
            src = read(path)
            rel = os.path.relpath(path, ROOT)
            ast.parse(src, feature_version=(3, 8))
            for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix("):
                self.assertNotIn(bad, src, rel)
            for bad in ("list[", "dict[", "tuple["):
                self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), rel)
            self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", rel)

    def test_shared_helpers_are_exact_copies(self):
        # a script copies a canonical helper verbatim or not at all (SPEC: never edit a copy)
        canon = top_level(CORE)
        copies = [os.path.join(ROOT, s, p) for s, p in SKILLS.items()] + [GENERATOR]
        checked = 0
        for path in copies:
            mine = top_level(path)
            for name, src in mine.items():
                if name in canon:
                    checked += 1
                    self.assertEqual(src, canon[name], "%s: %s is not a verbatim copy of tests/psx_core.py"
                                     % (os.path.relpath(path, ROOT), name))
        self.assertGreater(checked, 50)

    def test_order_fields_are_the_same_for_risk_and_broker(self):
        risk = top_level(os.path.join(ROOT, "psx-risk", "check.py"))
        broker = top_level(os.path.join(ROOT, "psx-broker", "ticket.py"))
        self.assertEqual(risk["ORDER_FIELDS"], broker["ORDER_FIELDS"])
        self.assertIn('"stop_loss"', risk["ORDER_FIELDS"])

    def test_day_trading_limits_ship_in_the_example_config(self):
        cfg = json.loads(read(os.path.join(ROOT, "psx-budget", "config.example.json")))
        for k in ("max_loss_per_trade_pkr", "max_daily_loss_pkr", "max_trades_per_day", "min_reward_risk",
                  "require_stop", "no_entries_before", "no_entries_after", "flat_by", "costs"):
            self.assertIn(k, cfg, k)
        self.assertIs(cfg["require_stop"], True)
        for k in ("allow_short", "allow_margin", "allow_options", "psx_site"):
            self.assertIs(cfg[k], False, k)
        for k in ("commission_pct", "commission_min_per_share", "sales_tax_pct", "other_charges_pct",
                  "day_trade_one_side"):
            self.assertIn(k, cfg["costs"], k)
        for k in ("max_loss_per_trade_usd", "pdt_guard", "sec_user_agent", "fractional"):
            self.assertNotIn(k, cfg, k)
        # v2: the day style is the default, and the swing block ships with its defaults (SPEC v2 section 2.1)
        self.assertEqual(cfg["style"], "day")
        self.assertEqual(list(cfg)[list(cfg).index("flat_by") + 1:list(cfg).index("flat_by") + 3], ["style", "swing"])
        self.assertEqual(list(cfg["swing"]), SWING_KEYS)
        self.assertEqual(cfg["swing"], {"stop": "atr:2", "target": "2R", "max_hold_sessions": 20,
                                        "trend_exit_sessions": 10, "entry_buffer_pct": 1.0, "max_open_positions": 3,
                                        "max_open_risk_pkr": None, "max_gap_loss_pkr": None,
                                        "stop_limit_gap_pct": 1.0})

    def test_paper_trading_is_the_default(self):
        cfg = json.loads(read(os.path.join(ROOT, "psx-budget", "config.example.json")))
        self.assertIs(cfg["paper"], True)
        self.assertEqual(cfg["broker"], "manual")

    def test_the_sample_market_says_it_is_made_up(self):
        note = read(os.path.join(ROOT, "tests", "fixtures", "psx-market", "README.md")).lower()
        self.assertIn("made up", note)


if __name__ == "__main__":
    unittest.main()
