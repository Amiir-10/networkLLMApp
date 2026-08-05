#!/usr/bin/env bash
# install-sync-timer.sh — systemd USER timer on this PC that runs
# scripts/sync-results.sh every 10 minutes (supervisor's results-safety net).
#
# Usage:
#   scripts/install-sync-timer.sh              # install + start
#   scripts/install-sync-timer.sh --uninstall  # stop + remove
#
# Check:   systemctl --user list-timers | grep vast-sync
# Logs:    journalctl --user -u vast-sync.service -n 20
# Cron fallback (non-systemd): */10 * * * * /path/to/repo/scripts/sync-results.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now vast-sync.timer 2>/dev/null || true
  rm -f "$UNIT_DIR/vast-sync.service" "$UNIT_DIR/vast-sync.timer"
  systemctl --user daemon-reload
  echo "vast-sync timer removed."
  exit 0
fi

[[ -f "$REPO_ROOT/scripts/vast-instance.env" ]] \
  || echo "NOTE: scripts/vast-instance.env not filled in yet — the timer will fail until it is."

mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/vast-sync.service" <<EOF
[Unit]
Description=Pull experiment results from the vast.ai instance

[Service]
Type=oneshot
ExecStart=$REPO_ROOT/scripts/sync-results.sh
EOF

cat > "$UNIT_DIR/vast-sync.timer" <<EOF
[Unit]
Description=Run vast-sync every 10 minutes while experiments run remotely

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now vast-sync.timer
echo "vast-sync.timer installed and started (every 10 min)."
systemctl --user list-timers --no-pager | grep -E 'NEXT|vast-sync' || true
echo "Uninstall with: scripts/install-sync-timer.sh --uninstall"
