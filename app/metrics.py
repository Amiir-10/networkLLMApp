import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "metrics.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    tool_call TEXT,
    tool_args TEXT,
    prose_response TEXT,
    validation_passed INTEGER,
    validation_error TEXT,
    execution_result TEXT,
    execution_success INTEGER,
    prompt_sent_at REAL NOT NULL,
    llm_response_at REAL,
    tool_executed_at REAL,
    prompt_eval_count INTEGER,
    eval_count INTEGER,
    ground_truth_tool TEXT,
    ground_truth_args TEXT,
    salvaged INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def _ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(_SCHEMA)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(interactions)")}
        if "salvaged" not in cols:
            conn.execute("ALTER TABLE interactions ADD COLUMN salvaged INTEGER")


@contextmanager
def _conn():
    _ensure_db()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


class MetricsCollector:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        _ensure_db()

    def start_interaction(self, model_id: str, prompt: str) -> dict:
        return {
            "id": str(uuid.uuid4())[:12],
            "session_id": self.session_id,
            "model_id": model_id,
            "prompt": prompt,
            "prompt_sent_at": time.time(),
        }

    def record(self, interaction: dict) -> None:
        cols = [
            "id", "session_id", "model_id", "prompt", "tool_call", "tool_args",
            "prose_response", "validation_passed", "validation_error",
            "execution_result", "execution_success", "prompt_sent_at",
            "llm_response_at", "tool_executed_at", "prompt_eval_count",
            "eval_count", "ground_truth_tool", "ground_truth_args",
            "salvaged",
        ]
        values = [interaction.get(c) for c in cols]
        placeholders = ",".join("?" * len(cols))
        col_names = ",".join(cols)
        with _conn() as conn:
            conn.execute(f"INSERT INTO interactions ({col_names}) VALUES ({placeholders})", values)

    def get_session_metrics(self, session_id: str | None = None) -> list[dict]:
        sid = session_id or self.session_id
        with _conn() as conn:
            rows = conn.execute(
                "SELECT * FROM interactions WHERE session_id = ? ORDER BY prompt_sent_at",
                (sid,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_model_summary(self) -> list[dict]:
        with _conn() as conn:
            rows = conn.execute("""
                SELECT
                    model_id,
                    COUNT(*) as total,
                    SUM(CASE WHEN execution_success = 1 THEN 1 ELSE 0 END) as successes,
                    AVG(llm_response_at - prompt_sent_at) as avg_llm_latency_s,
                    AVG(tool_executed_at - llm_response_at) as avg_exec_latency_s,
                    AVG(eval_count) as avg_tokens
                FROM interactions
                WHERE llm_response_at IS NOT NULL
                GROUP BY model_id
            """).fetchall()
            return [dict(r) for r in rows]
