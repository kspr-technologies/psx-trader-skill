#!/bin/sh
# PSX auto-run: the launcher. The PSX Autorun app calls this with one job name:
#   tick, tape, selftest or open-latest.
# It starts autorun.py with the Python that install.sh found (in python.path).
# install.sh copies this file to ~/Library/Application Support/psx-autorun/run.sh.
#
# Exit 3: no Python (the app says "The tick job did not run: ...").
# Exit 4: no Python again, and that was said less than an hour ago (state/launcher-told).
#         The app shows no message for exit 4, so a broken setup does not ring every 5 minutes.

HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(cat "$HERE/python.path" 2>/dev/null)"
STAMP="$HERE/state/launcher-told"
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  if [ -f "$STAMP" ] && [ -n "$(find "$STAMP" -mmin -60 2>/dev/null)" ]; then
    exit 4
  fi
  mkdir -p "$HERE/state" 2>/dev/null
  : > "$STAMP" 2>/dev/null
  echo "no Python at ${PY:-(not set)}. Run install.sh again." >&2
  exit 3
fi
rm -f "$STAMP" 2>/dev/null
PSX_NOTIFY=applet
export PSX_NOTIFY
exec "$PY" "$HERE/autorun.py" "$@"
