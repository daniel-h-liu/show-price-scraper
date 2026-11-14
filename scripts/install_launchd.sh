#!/bin/bash
# Install (or reinstall) one launchd agent for this user:
#   local.show-price-scraper.daily  -- 08:00 every day: refresh catalog, check tracked shows, email digest
#                                       (a missed 08:00 because the Mac was asleep runs once on wake)
#   The job runs under caffeinate (-i always, -s on AC power) so the Mac can't go back to sleep mid-run;
#   it's released when the job exits (-w) or after 2 hours at most (-t), so a hung run can't keep the Mac awake.
#   Before this, a sleeping Mac only gave the job a few seconds every ~16 min and the Met checks failed.
#   Optional, to also wake the Mac just before 8 (one-time, needs sudo):  sudo pmset repeat wakeorpoweron MTWRFSU 07:58:00
# Nothing else runs in the background; open the tracker page when needed with scripts/page.sh.
# Uninstall: scripts/install_launchd.sh --uninstall
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
DOMAIN="gui/$(id -u)"
PY="$ROOT/.venv/bin/python"
DAILY="local.show-price-scraper.daily"
WEB="local.show-price-scraper.web"  # earlier always-on page; only removed now

unload() { launchctl bootout "$DOMAIN/$1" 2>/dev/null || true; }

if [[ "${1:-}" == "--uninstall" ]]; then
  for l in "$DAILY" "$WEB" local.show-price-scraper; do unload "$l"; rm -f "$AGENTS/$l.plist"; done
  echo "uninstalled"
  exit 0
fi

mkdir -p "$ROOT/logs" "$AGENTS"
for l in local.show-price-scraper "$WEB"; do unload "$l"; rm -f "$AGENTS/$l.plist"; done  # earlier versions

cat > "$AGENTS/$DAILY.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$DAILY</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>-c</string><string>/usr/bin/caffeinate -i -s -t 7200 -w \$\$ &amp; exec '$PY' -m scraper.run</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/logs/run.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/run.log</string>
</dict>
</plist>
EOF

unload "$DAILY"; launchctl bootstrap "$DOMAIN" "$AGENTS/$DAILY.plist"
echo "installed: daily check + digest at 08:00 (log: logs/run.log)"
echo "tracker page (on demand): scripts/page.sh"
echo "run the daily job now:  launchctl kickstart $DOMAIN/$DAILY"
