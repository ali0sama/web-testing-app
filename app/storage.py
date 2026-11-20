"""SQLite storage for inspections, observed elements and model-call records.

Inspection results are kept so later steps (test design, generation, and the
Phase 2 rescan/compare) can refer to what was actually observed.
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.config import DATA_DIR, DB_PATH, MODEL_LOG_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    requested_url TEXT NOT NULL,
    final_url TEXT,
    title TEXT,
    scope TEXT,
    include_hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    dom_hash TEXT,
    screenshot TEXT,
    element_count INTEGER NOT NULL,
    total_matched INTEGER,
    hidden_skipped INTEGER,
    load_ms INTEGER,
    inspect_ms INTEGER,
    page_text_json TEXT
);
CREATE TABLE IF NOT EXISTS elements (
    inspection_id INTEGER NOT NULL REFERENCES inspections(id),
    element_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (inspection_id, element_id)
);
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    inspection_id INTEGER,
    purpose TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    token_source TEXT,
    latency_ms INTEGER,
    ok INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    messages_json TEXT,
    response_text TEXT,
    result_json TEXT
);
"""

# Columns added after the first release; added to older databases on startup.
ADDED_COLUMNS = {
    "inspections": ("page_text_json",),
    "model_calls": ("messages_json", "response_text", "result_json"),
}

# Small columns for the usage table (the full prompt/response is fetched per call).
MODEL_CALL_SUMMARY_COLUMNS = (
    "id, session_id, inspection_id, purpose, provider, model, prompt_tokens, "
    "completion_tokens, total_tokens, token_source, latency_ms, ok, error, created_at"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(SCHEMA)
        for table, columns in ADDED_COLUMNS.items():
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")


# ---------------- inspections ----------------

def save_inspection(session_id: str, requested_url: str, include_hidden: bool,
                    opened: dict, result: dict) -> int:
    with connect() as connection:
        cursor = connection.execute(
            """INSERT INTO inspections (session_id, requested_url, final_url, title, scope,
                   include_hidden, created_at, dom_hash, screenshot, element_count,
                   total_matched, hidden_skipped, load_ms, inspect_ms, page_text_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, requested_url, result.get("page_url") or opened.get("final_url"),
                result.get("title") or opened.get("title"), result.get("scope"),
                1 if include_hidden else 0, now_iso(), result.get("dom_hash"),
                result.get("screenshot"), len(result["elements"]), result.get("total_matched"),
                result.get("hidden_skipped"), opened.get("load_ms"), result.get("inspect_ms"),
                json.dumps(result.get("page_text") or [], ensure_ascii=False),
            ),
        )
        inspection_id = cursor.lastrowid
        for element in result["elements"]:
            connection.execute(
                "INSERT INTO elements (inspection_id, element_id, data_json) VALUES (?, ?, ?)",
                (inspection_id, element["element_id"], json.dumps(element)),
            )
    return inspection_id


def get_inspection(inspection_id: int):
    with connect() as connection:
        row = connection.execute("SELECT * FROM inspections WHERE id = ?", (inspection_id,)).fetchone()
        if row is None:
            return None
        element_rows = connection.execute(
            "SELECT data_json FROM elements WHERE inspection_id = ? ORDER BY element_id",
            (inspection_id,),
        ).fetchall()
    inspection = dict(row)
    inspection["include_hidden"] = bool(inspection["include_hidden"])
    page_text_json = inspection.pop("page_text_json", None)
    inspection["page_text"] = json.loads(page_text_json) if page_text_json else []
    inspection["elements"] = [json.loads(r["data_json"]) for r in element_rows]
    return inspection


def list_inspections(limit: int = 30) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT id, final_url, title, scope, created_at, element_count
               FROM inspections ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------- model calls ----------------

def save_model_call(record: dict, messages: list[dict] = None, response_text: str = None) -> int:
    """Store one model call: metrics plus the exact prompt that was sent and
    the raw response, so every call can be reviewed later as evidence."""
    record = dict(record)
    record.setdefault("created_at", now_iso())
    with connect() as connection:
        cursor = connection.execute(
            """INSERT INTO model_calls (session_id, inspection_id, purpose, provider, model,
                   prompt_tokens, completion_tokens, total_tokens, token_source,
                   latency_ms, ok, error, created_at, messages_json, response_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["session_id"], record.get("inspection_id"), record["purpose"],
                record["provider"], record["model"], record.get("prompt_tokens"),
                record.get("completion_tokens"), record.get("total_tokens"),
                record.get("token_source"), record.get("latency_ms"),
                1 if record.get("ok") else 0, record.get("error"), record["created_at"],
                json.dumps(messages, ensure_ascii=False) if messages is not None else None,
                response_text,
            ),
        )
        record["id"] = cursor.lastrowid
    # Also append to a JSON-lines log file (submitted as usage evidence).
    log_entry = {**record, "messages": messages, "response_text": response_text}
    with open(MODEL_LOG_PATH, "a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    return record["id"]


def save_model_call_result(call_id: int, validated: dict):
    """Attach the validated (grounding-checked) result to a stored call."""
    with connect() as connection:
        connection.execute(
            "UPDATE model_calls SET result_json = ? WHERE id = ?",
            (json.dumps(validated, ensure_ascii=False), call_id),
        )


def list_model_calls(limit: int = 100) -> list[dict]:
    with connect() as connection:
        rows = connection.execute(
            f"SELECT {MODEL_CALL_SUMMARY_COLUMNS} FROM model_calls ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    calls = [dict(r) for r in rows]
    for call in calls:
        call["ok"] = bool(call["ok"])
    return calls


def get_model_call(call_id: int):
    """One call with its full prompt, raw response and validated result."""
    with connect() as connection:
        row = connection.execute("SELECT * FROM model_calls WHERE id = ?", (call_id,)).fetchone()
    if row is None:
        return None
    call = dict(row)
    call["ok"] = bool(call["ok"])
    messages_json = call.pop("messages_json", None)
    result_json = call.pop("result_json", None)
    call["messages"] = json.loads(messages_json) if messages_json else None
    call["result"] = json.loads(result_json) if result_json else None
    return call


def session_stats(session_id: str) -> dict:
    """Average response time counts successful calls only."""
    with connect() as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS calls, AVG(latency_ms) AS avg_latency_ms,
                      SUM(total_tokens) AS total_tokens
               FROM model_calls WHERE session_id = ? AND ok = 1""",
            (session_id,),
        ).fetchone()
    return {
        "successful_calls": row["calls"],
        "avg_latency_ms": round(row["avg_latency_ms"]) if row["avg_latency_ms"] is not None else None,
        "total_tokens": row["total_tokens"] or 0,
    }
