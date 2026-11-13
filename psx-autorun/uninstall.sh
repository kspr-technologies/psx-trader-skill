#!/bin/bash
# PSX auto-run: turn it off and remove it. You run this yourself, in Terminal:
#
#     bash "<pack>/psx-autorun/uninstall.sh"          stops the jobs, removes the schedules and the app
#     bash "<pack>/psx-autorun/uninstall.sh" --all    also deletes auto-run's settings, state and logs
#
# Your reports and your psx/ folders are never deleted. A tape you started by hand is left alone.

set -u

SUPPORT="$HOME/Library/Application Support/psx-autorun"
LOGS="$HOME/Library/Logs/psx-autorun"
AGENTS="$HOME/Library/LaunchAgents"
GUI="gui/$(id -u)"
LABELS="local.psx-autorun.tick local.psx-autorun.tape"

say() { printf '%s\n' "$*"; }

ALL="no"
case "${1:-}" in
  "") ;;
  --all) ALL="yes" ;;
  *) say "Use: bash uninstall.sh [--all]"; exit 1 ;;
esac

for L in $LABELS; do
  if launchctl bootout "$GUI/$L" 2>/dev/null; then
    say "Stopped:  $L"
  else
    say "Not on:   $L"
  fi
  rm -f "$AGENTS/$L.plist"
done

rm -rf "$SUPPORT/PSX Autorun.app" "$SUPPORT/app.stamp"
tccutil reset All local.psx-autorun >/dev/null 2>&1 || true
say "Removed:  the app and its macOS permissions"

if [ "$ALL" = "yes" ]; then
  rm -rf "$SUPPORT" "$LOGS"
  say "Removed:  $SUPPORT and $LOGS"
else
  say "Kept:     $SUPPORT (settings and state) and $LOGS. Use --all to delete them."
fi

say ""
say "Auto-run is off. Your reports and your psx/ folders were not touched."
say "If you set a wake time with pmset, remove it with: sudo pmset repeat cancel"
