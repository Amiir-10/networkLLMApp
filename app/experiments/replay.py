"""Recording layer for experiment runs — the prompt-replay integration point.

This module vendors an API-compatible subset of MukundaKatta/prompt-replay
(the supervisor-suggested repo: JSONL append-only store + Recorder), because
installing straight from an unreviewed git source is blocked in the dev
environment. The public surface (JsonlStore / InMemoryStore / Recorder with
`capture` + manual `record`) matches the upstream README, so installing the
real package later makes it a drop-in: the import below prefers the installed
`prompt_replay` package and only falls back to the local classes.

Upstream: https://github.com/MukundaKatta/prompt-replay (zero-dependency).
The Replayer/diff half of the upstream API (cross-model replay) is phase 2
and should come from the real package rather than being re-implemented here.
"""

from __future__ import annotations

import functools
import inspect
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class JsonlStore:
    """Append-only JSONL sink; one recorded call per line."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, entry: dict) -> None:
        line = json.dumps(entry, default=str)
        with self._lock, self.path.open("a") as f:
            f.write(line + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open() as f:
            return [json.loads(line) for line in f if line.strip()]


class InMemoryStore:
    def __init__(self):
        self.entries: list[dict] = []

    def write(self, entry: dict) -> None:
        self.entries.append(entry)

    def read_all(self) -> list[dict]:
        return list(self.entries)


class Recorder:
    def __init__(self, store, *, capture_errors: bool = True, metadata: dict | None = None):
        self.store = store
        self.capture_errors = capture_errors
        self.metadata = metadata or {}

    def record(self, request: dict, response: dict | None, error: str | None = None) -> None:
        self.store.write({
            "ts": datetime.now(timezone.utc).isoformat(),
            "metadata": self.metadata,
            "request": request,
            "response": response,
            "error": error,
        })

    def capture(self, fn):
        """Decorator: record every call's bound arguments and return value."""
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            request = {k: v for k, v in bound.arguments.items()}
            try:
                response = fn(*args, **kwargs)
            except Exception as e:
                if self.capture_errors:
                    self.record(request=request, response=None, error=str(e))
                raise
            self.record(request=request, response=response)
            return response

        return wrapper


try:  # prefer the real package when Amir installs it
    from prompt_replay import Recorder, JsonlStore, InMemoryStore  # type: ignore # noqa: F811,F401
except ImportError:
    pass
