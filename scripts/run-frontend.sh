#!/usr/bin/env bash
# Run the Vite dev server on http://localhost:5173 and open it in the default browser.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/gui"

if ! bash "$REPO_ROOT/scripts/doctor.sh" frontend; then
  echo
  echo "Preflight failed. Run 'scripts/install.sh' to install frontend deps."
  exit 1
fi

# Open the browser once vite has had a moment to bind the port.
# Backgrounded so it doesn't block the dev server; xdg-open is the standard
# Linux desktop launcher, present on Ubuntu/Debian/Fedora.
(
  sleep 2
  if command -v xdg-open >/dev/null; then
    xdg-open http://localhost:5173 >/dev/null 2>&1 || true
  fi
) &

echo
echo ">>> starting vite on http://localhost:5173"
exec npm run dev -- --host 127.0.0.1 --port 5173 "$@"
