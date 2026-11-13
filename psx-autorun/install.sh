#!/bin/bash
# PSX auto-run: set it up on this Mac. You run this yourself, once, in Terminal:
#
#   bash "<pack>/psx-autorun/install.sh" --trading "<folder that holds psx/>" --longterm "<folder that holds psx/>"
#
#   --trading DIR    the folder that holds your trading psx/ folder (needed)
#   --longterm DIR   the folder that holds your long-term psx/ folder (you may leave it out)
#   --reports DIR    where the reports go (default: <trading folder>/autorun)
#
# The pack is the folder above this file. No admin password. It only touches your own user:
#   ~/Library/Application Support/psx-autorun/        the runner, its settings, its state, the app
#   ~/Library/LaunchAgents/local.psx-autorun.*.plist   the two schedules
#   ~/Library/Logs/psx-autorun/                        the logs
#   the reports folder
# It never places an order. It does not change the files in your psx/ folders.
# Run it again any time (after a Python update, for example). It keeps your settings.

set -eu

say()   { printf '%s\n' "$*"; }
stop()  { printf '\nStopped: %s\n' "$*" >&2; exit 1; }
usage() { say 'Use: bash install.sh --trading "<folder that holds psx/>" [--longterm "<folder>"] [--reports "<folder>"]'; }

# 0. The folders you give. Nothing is changed before they are checked.
TRADING=""
LONGTERM=""
REPORTS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --trading)  [ $# -ge 2 ] || { usage; stop "--trading needs a folder."; }; TRADING="$2"; shift 2 ;;
    --longterm) [ $# -ge 2 ] || { usage; stop "--longterm needs a folder."; }; LONGTERM="$2"; shift 2 ;;
    --reports)  [ $# -ge 2 ] || { usage; stop "--reports needs a folder."; }; REPORTS="$2"; shift 2 ;;
    -h|--help)  usage; exit 0 ;;
    *)          usage; stop "unknown option: $1" ;;
  esac
done
[ -n "$TRADING" ] || { usage; stop "--trading is needed: the folder that holds your trading psx/ folder. Nothing was changed."; }
# The reports folder as a full path. launchd starts auto-run in its own folder, so a path like "myreports",
# or a "~/..." in quotes (the shell does not expand it there), would point somewhere else.
# tests/test_autorun.py runs the lines between the two marks in a temp folder.
# --- reports path ---
case "$REPORTS" in "~") REPORTS="$HOME" ;; "~/"*) REPORTS="$HOME/${REPORTS#\~/}" ;; esac
case "$REPORTS" in ""|/*) ;; *) REPORTS="$PWD/$REPORTS" ;; esac
# --- end of reports path ---

HERE="$(cd "$(dirname "$0")" && pwd)"
PACK="$(cd "$HERE/.." && pwd)"
SUPPORT="$HOME/Library/Application Support/psx-autorun"
LOGS="$HOME/Library/Logs/psx-autorun"
AGENTS="$HOME/Library/LaunchAgents"
APP="$SUPPORT/PSX Autorun.app"
GUI="gui/$(id -u)"
LABELS="local.psx-autorun.tick local.psx-autorun.tape"

[ "$(uname -s)" = "Darwin" ] || stop "this is for a Mac."
for f in autorun.py run.sh PSXAutorun.applescript autorun.example.json \
         launchd/local.psx-autorun.tick.plist launchd/local.psx-autorun.tape.plist; do
  [ -f "$HERE/$f" ] || stop "a file is missing next to install.sh: $f"
done
[ -f "$PACK/psx-alerts/alerts.py" ] || stop "no PSX tools at $PACK (install.sh must stay in psx-autorun/ of the pack)."
TRADING="$(cd "$TRADING" 2>/dev/null && pwd)" || stop "no folder at the --trading path."
[ -f "$TRADING/psx/config.json" ] || stop "no psx/config.json in $TRADING."
if [ -n "$LONGTERM" ]; then
  LONGTERM="$(cd "$LONGTERM" 2>/dev/null && pwd)" || stop "no folder at the --longterm path."
  [ -f "$LONGTERM/psx/config.json" ] || stop "no psx/config.json in $LONGTERM."
fi
ASKED_REPORTS="$REPORTS"
[ -n "$REPORTS" ] || REPORTS="$TRADING/autorun"

# 1. Python: the real program, not a pyenv shim (launchd starts jobs with no shell setup).
PY="${PSX_PYTHON:-}"
if [ -z "$PY" ] && command -v pyenv >/dev/null 2>&1; then
  PY="$(pyenv which python3 2>/dev/null || true)"
fi
[ -n "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || stop "no python3 found. Install Python 3.8 or newer."
PY="$("$PY" -c 'import os, sys; print(os.path.realpath(sys.executable))')" || stop "python3 did not start."
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' || stop "Python 3.8 or newer is needed ($PY)."
say "Python:    $PY"
say "Pack:      $PACK"
say "Trading:   $TRADING/psx"
if [ -n "$LONGTERM" ]; then say "Long-term: $LONGTERM/psx"; else say "Long-term: none (its steps are skipped)"; fi

# 2. The runner, its launcher and its settings. An old settings file is kept: only the Python
#    path is updated.
mkdir -p "$SUPPORT/state" "$LOGS" "$AGENTS"
cp "$HERE/autorun.py" "$SUPPORT/autorun.py"
cp "$HERE/run.sh" "$SUPPORT/run.sh"
printf '%s\n' "$PY" > "$SUPPORT/python.path"
"$PY" - "$SUPPORT/autorun.json" "$HERE/autorun.example.json" "$PY" "$PACK" "$TRADING" "$LONGTERM" "$REPORTS" \
  "$ASKED_REPORTS" <<'EOF'
import json, os, sys
out, example, py, pack, trading, longterm, reports, asked = sys.argv[1:]
if os.path.exists(out):
    with open(out) as fh:
        cfg = json.load(fh)
    cfg["python"] = py
    print("Settings:  kept " + out + " (only the Python path is new)")
    given = {"pack": pack, "trading": trading, "longterm": longterm}
    had = {"pack": cfg.get("pack"), "trading": (cfg.get("folders") or {}).get("trading"),
           "longterm": (cfg.get("folders") or {}).get("longterm")}
    if asked:                       # --reports was given: say so when the kept settings name another folder
        given["reports"], had["reports"] = asked, cfg.get("reports")
    for k in sorted(given):
        if (had[k] or "") != (given[k] or ""):
            print("Note:      the kept settings say %s is %s, not %s. To change it, edit autorun.json, or delete "
                  "it and run install.sh again." % (k, had[k] or "(not set)", given[k] or "(not set)"))
else:
    with open(example) as fh:
        cfg = json.load(fh)
    cfg.update({"python": py, "pack": pack, "reports": reports, "folders": {"trading": trading, "longterm": longterm}})
    print("Settings:  wrote " + out)
with open(out + ".tmp", "w") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
os.replace(out + ".tmp", out)
EOF
REPORTS="$("$PY" -c 'import json, os, sys
cfg = json.load(open(sys.argv[1]))
print(cfg.get("reports") or os.path.join((cfg.get("folders") or {}).get("trading") or "", "autorun"))' "$SUPPORT/autorun.json")"
mkdir -p "$REPORTS" || stop "cannot make the reports folder $REPORTS."
say "Reports:   $REPORTS"

# 3. The small app. It holds the Documents permission and shows the messages. It is built again
#    only when its script changes, so macOS keeps your "Allow". (A new Python only changes python.path.)
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
"$PY" - "$HERE/PSXAutorun.applescript" "$TMP/main.applescript" "$SUPPORT/run.sh" <<'EOF'
import sys
src, out, launcher = sys.argv[1:]
if '"' in launcher or "\\" in launcher:
    sys.exit("a path has a quote or a backslash in it: " + launcher)
with open(src) as fh:
    text = fh.read().replace("__LAUNCHER__", launcher)
with open(out, "w") as fh:
    fh.write(text)
EOF
STAMP="$(shasum -a 256 "$TMP/main.applescript" | cut -d' ' -f1)"
if [ -d "$APP" ] && [ "$(cat "$SUPPORT/app.stamp" 2>/dev/null || true)" = "$STAMP" ]; then
  say "App:       kept $APP"
else
  rm -rf "$APP"
  osacompile -o "$APP" "$TMP/main.applescript" || stop "osacompile could not build the app."
  PL="$APP/Contents/Info.plist"
  plutil -replace CFBundleIdentifier -string local.psx-autorun "$PL"
  plutil -replace CFBundleName -string "PSX Autorun" "$PL"
  plutil -replace CFBundleShortVersionString -string 2.0 "$PL"
  plutil -replace LSUIElement -bool true "$PL"
  plutil -replace NSDocumentsFolderUsageDescription -string \
    "PSX Autorun runs your PSX tools. They read and write your psx folders in Documents." "$PL"
  codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || stop "codesign could not sign the app."
  printf '%s\n' "$STAMP" > "$SUPPORT/app.stamp"
  say "App:       built $APP"
fi

# 4. The two schedules, from the templates in launchd/ (only paths outside Documents).
for L in $LABELS; do
  "$PY" - "$HERE/launchd/$L.plist" "$AGENTS/$L.plist" "$HOME" "$APP" <<'EOF'
import plistlib, sys
from xml.sax.saxutils import escape
src, out, home, app = sys.argv[1:]
with open(src) as fh:
    text = fh.read().replace("__HOME__", escape(home)).replace("__APP__", escape(app))
if "/Documents/" in text:
    sys.exit("a schedule would name a path in Documents, and macOS would block it: " + out)
plistlib.loads(text.encode("utf-8"))            # stop here if it is not a valid plist
with open(out, "w") as fh:
    fh.write(text)
EOF
  plutil -lint "$AGENTS/$L.plist" >/dev/null || stop "$AGENTS/$L.plist is not valid."
done
say "Schedules: $AGENTS/local.psx-autorun.tick.plist and .tape.plist"

# 5. The macOS permission step: a test run through the app, while you are here.
say ""
say "A test run starts now. macOS asks you two things. Click Allow for both:"
say "  1. \"PSX Autorun\" would like to access files in your Documents folder."
say "  2. Notifications from PSX Autorun."
rm -f "$SUPPORT/selftest.txt"
open -n -W --env PSX_JOB=selftest "$APP" || true
[ -f "$SUPPORT/selftest.txt" ] || stop "the test did not run. Look in $LOGS/autorun.log. Nothing was turned on."
say ""
cat "$SUPPORT/selftest.txt"
head -1 "$SUPPORT/selftest.txt" | grep -q '^OK' \
  || stop "fix the PROBLEM line above, then run install.sh again. Nothing was turned on."
SEEN="y"
printf '\nDid you see the message "PSX auto-run works"? (y/n) '
read -r ANSWER || ANSWER=""
case "$ANSWER" in
  y|Y|yes|Yes|YES) ;;
  *) SEEN="n"
     say "Then macOS hides the messages. Fix it like this:"
     say "  1. Open System Settings, Notifications, PSX Autorun. Turn on Allow notifications."
     say "  2. Set the style to Alerts (Alerts stay on the screen until you close them)."
     say "  3. Open System Settings, Focus. In each Focus, allow PSX Autorun."
     say "The install goes on: your files are what matter here." ;;
esac

# 6. Turn the schedules on (an old copy is stopped first). The probe request is written first, so
#    the first scheduled run (at load) answers it.
for L in $LABELS; do
  launchctl bootout "$GUI/$L" 2>/dev/null || true
done
sleep 2
TOKEN="probe-$(date +%Y%m%d%H%M%S)-$$"
PROBE="$REPORTS/autorun-probe.txt"
printf '%s\n' "$TOKEN" > "$SUPPORT/state/probe.request"
for L in $LABELS; do
  launchctl enable "$GUI/$L" 2>/dev/null || true
  launchctl bootstrap "$GUI" "$AGENTS/$L.plist" \
    || stop "launchctl could not load $L. Run uninstall.sh, wait a few seconds, then run install.sh again."
done

# 7. Test the real path. launchd starts Contents/MacOS/applet itself, not through "open", and macOS
#    may judge that path on its own. The scheduled run must write a file in the reports folder.
launchctl kickstart "$GUI/local.psx-autorun.tick" 2>/dev/null || true
say ""
say "Testing the scheduled run (up to 60 seconds) ..."
n=0
while [ $n -lt 60 ]; do
  if grep -qF "$TOKEN" "$PROBE" 2>/dev/null; then
    break
  fi
  sleep 1
  n=$((n + 1))
done
if ! grep -qF "$TOKEN" "$PROBE" 2>/dev/null; then
  for L in $LABELS; do
    launchctl bootout "$GUI/$L" 2>/dev/null || true
  done
  rm -f "$SUPPORT/state/probe.request"
  stop "The scheduled run could not write to your reports folder in Documents. Open System Settings, Privacy & Security, Files and Folders, and turn on Documents Folder for PSX Autorun. Then run install.sh again. Nothing was left turned on."
fi
say "The scheduled run works: it wrote $PROBE"

# 8. Today's plan, and what to know.
say ""
say "Done. Auto-run is on. Today's plan (Karachi time):"
"$PY" "$SUPPORT/autorun.py" plan || true
say ""
say "- Reports: $REPORTS (open LATEST.md first)"
say "- Logs:    $LOGS"
say "- macOS may show \"Background Items Added\". That is auto-run. Leave it on in"
say "  System Settings, General, Login Items & Extensions."
say "- Keep the Mac on, with the lid open and on power, in market hours."
say "- To turn it off: bash \"$HERE/uninstall.sh\""
if [ "$SEEN" = "n" ]; then
  say ""
  say "Messages: you did not see the test message. Until you fix it (step 5 above), auto-run still works,"
  say "but its messages may not show. The reports are always written."
fi
