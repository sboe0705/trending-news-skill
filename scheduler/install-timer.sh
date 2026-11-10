#!/usr/bin/env bash
# Installs (or with --uninstall removes) a systemd *user* timer that runs the
# trending-news briefing every 4 hours on this machine.
set -euo pipefail

UNIT=trending-news
UNIT_DIR="$HOME/.config/systemd/user"
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run-trending-news.sh"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now "$UNIT.timer" 2>/dev/null || true
  rm -f "$UNIT_DIR/$UNIT.service" "$UNIT_DIR/$UNIT.timer"
  systemctl --user daemon-reload
  echo "Removed $UNIT timer."
  exit 0
fi

chmod +x "$SCRIPT"
mkdir -p "$UNIT_DIR"

cat > "$UNIT_DIR/$UNIT.service" <<EOF
[Unit]
Description=Google Trends news briefing (Claude Code skill)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=$SCRIPT
TimeoutStartSec=15min
EOF

cat > "$UNIT_DIR/$UNIT.timer" <<EOF
[Unit]
Description=Run the Google Trends news briefing every 4 hours

[Timer]
OnCalendar=*-*-* 00/4:00:00
Persistent=true
RandomizedDelaySec=2min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT.timer"
systemctl --user list-timers "$UNIT.timer" --no-pager
echo
echo "Installed. Briefings are saved to \${TRENDING_NEWS_DIR:-~/trending-news}/ (latest.md = newest)."
echo "Run once now:   systemctl --user start $UNIT.service"
echo "Logs:           journalctl --user -u $UNIT.service"
echo "Uninstall:      $0 --uninstall"
