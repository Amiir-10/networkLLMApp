#!/usr/bin/env bash
# Run the FastAPI backend (uvicorn) on http://127.0.0.1:8000.
# Runs a fast preflight first; bails with a clear message if a required dep is missing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! bash scripts/doctor.sh backend; then
  echo
  echo "Preflight failed. Fix the [FAIL] items above (most are resolved by 'scripts/install.sh' or starting Ollama)."
  exit 1
fi

echo
echo ">>> starting uvicorn on http://127.0.0.1:8000"
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 "$@"
