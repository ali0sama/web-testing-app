"""Tests for: stored prompt/response evidence, wrong-page highlight guard,
and scope errors reported as user errors (HTTP 400)."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import main, storage
from app.runner_client import RunnerError
from app.schemas import HighlightRequest, InspectRequest

# worker.py imports its sibling module as "locators", so put runner/ on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
import worker  # noqa: E402


@pytest.fixture
def temp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(storage, "MODEL_LOG_PATH", tmp_path / "log.jsonl")
    storage.init_db()
    return tmp_path


# ---------------- model call evidence ----------------

def test_model_call_stores_prompt_response_and_result(temp_storage):
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "goal + page_data"}]
    record = {"session_id": "s1", "inspection_id": 7, "purpose": "summarize_and_suggest",
              "provider": "groq", "model": "m", "prompt_tokens": 10, "completion_tokens": 5,
              "total_tokens": 15, "token_source": "provider", "latency_ms": 123, "ok": True}
    call_id = storage.save_model_call(record, messages=messages, response_text='{"summary": "x"}')
    storage.save_model_call_result(call_id, {"summary": "x", "validation_issues": []})

    call = storage.get_model_call(call_id)
    assert call["messages"] == messages
    assert call["response_text"] == '{"summary": "x"}'
    assert call["result"]["summary"] == "x"
    assert "messages_json" not in call and "result_json" not in call

    # The usage table stays small: no prompt/response columns in the list.
    listed = storage.list_model_calls()[0]
    assert "response_text" not in listed and "messages_json" not in listed

    # The JSONL evidence log carries the prompt and response too.
    log_line = json.loads((temp_storage / "log.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert log_line["messages"] == messages
    assert log_line["response_text"] == '{"summary": "x"}'


def test_init_db_adds_new_columns_to_old_database(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    with sqlite3.connect(db_path) as connection:  # schema from before this change
        connection.execute(
            """CREATE TABLE model_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
               inspection_id INTEGER, purpose TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
               prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER, token_source TEXT,
               latency_ms INTEGER, ok INTEGER NOT NULL, error TEXT, created_at TEXT NOT NULL)""")
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", db_path)
    storage.init_db()
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(model_calls)")}
    assert {"messages_json", "response_text", "result_json"} <= columns


# ---------------- wrong-page highlight guard ----------------

@pytest.mark.parametrize("a, b, expected", [
    ("https://x.test/login", "https://x.test/login", True),
    ("https://x.test/login#content", "https://x.test/login", True),
    ("https://x.test/login/", "https://x.test/login", True),
    ("https://x.test/secure", "https://x.test/login", False),
    ("about:blank", "https://x.test/login", False),
])
def test_same_page(a, b, expected):
    assert worker.same_page(a, b) is expected


class FakePage:
    url = "https://x.test/secure"


def test_runner_refuses_highlight_on_other_page():
    session = worker.BrowserSession()
    session.get_page = lambda: FakePage()
    with pytest.raises(worker.WrongPageError):
        worker.handle_highlight(session, {"locator": {"kind": "id", "value": "#username"},
                                          "expected_url": "https://x.test/login"})


INSPECTION = {
    "id": 3, "final_url": "https://x.test/login",
    "elements": [{"element_id": "E001", "locators": [
        {"kind": "id", "value": "#username", "code": 'page.locator("#username")', "status": "unique"}]}],
}


class FakeRunner:
    def __init__(self, error: RunnerError = None):
        self.error = error
        self.calls = []

    def call(self, command, args=None, timeout=None):
        self.calls.append((command, args))
        if self.error and command in ("inspect", "highlight"):
            raise self.error
        return {"final_url": "https://x.test/login", "title": "t", "load_ms": 1}


def test_highlight_sends_expected_url_and_maps_wrong_page_to_409(monkeypatch):
    fake = FakeRunner(RunnerError("WrongPageError: The browser shows A, but ...", "WrongPageError"))
    monkeypatch.setattr(main, "runner", fake)
    monkeypatch.setattr(main.storage, "get_inspection", lambda _id: INSPECTION)
    with pytest.raises(HTTPException) as info:
        main.highlight(HighlightRequest(inspection_id=3, element_id="E001"))
    assert info.value.status_code == 409
    assert fake.calls[0][1]["expected_url"] == "https://x.test/login"


# ---------------- test data must be observed or given by the tester ----------------

from app import assistant  # noqa: E402
from app.llm.prompts import build_assist_messages  # noqa: E402

GROUNDING_INSPECTION = {
    "id": 9, "title": "Login", "final_url": "https://x.test/login",
    "page_text": [{"text_id": "T001", "tag": "h4", "text": "Enter tomsmith for the username."}],
    "elements": [{"element_id": "E001", "recommended_locator": 'page.locator("#u")'}],
}


@pytest.mark.parametrize("hint, goal, expected", [
    ("", "", ""),
    ("<valid username>", "", "placeholder"),
    ("tomsmith", "", "page_text"),
    ("alice", "log in as alice", "goal"),
    ("SuperSecretPassword!", "log in", "unverified"),
])
def test_test_data_source(hint, goal, expected):
    assert assistant.test_data_source(hint, goal, GROUNDING_INSPECTION) == expected


def test_unobserved_test_data_is_flagged():
    data = {"next_actions": [{"element_id": "E001", "action": "fill", "test_data_hint": "SuperSecretPassword!"}]}
    result = assistant.validate_response(data, GROUNDING_INSPECTION, ["E001"], goal="log in")
    assert result["next_actions"][0]["test_data_source"] == "unverified"
    assert any("SuperSecretPassword!" in issue for issue in result["validation_issues"])


def test_prompt_includes_page_text_as_data():
    user = build_assist_messages(GROUNDING_INSPECTION, GROUNDING_INSPECTION["elements"], "log in")[1]["content"]
    assert "<page_text>\nT001: Enter tomsmith for the username.\n</page_text>" in user


def test_inspection_page_text_round_trip(temp_storage):
    result = {"elements": [], "page_text": GROUNDING_INSPECTION["page_text"]}
    inspection_id = storage.save_inspection("s", "https://x.test/login", False, {}, result)
    inspection = storage.get_inspection(inspection_id)
    assert inspection["page_text"] == GROUNDING_INSPECTION["page_text"]
    assert "page_text_json" not in inspection


# ---------------- scope errors are user errors ----------------

def test_invalid_scope_returns_400(monkeypatch):
    fake = FakeRunner(RunnerError("ScopeError: Scope selector matched nothing: #nope", "ScopeError"))
    monkeypatch.setattr(main, "runner", fake)
    monkeypatch.setattr(main.settings, "allowed_hosts", ["x.test"])
    with pytest.raises(HTTPException) as info:
        main.inspect_page(InspectRequest(url="https://x.test/login", scope="#nope"))
    assert info.value.status_code == 400
    assert info.value.detail == "Scope selector matched nothing: #nope"


def test_other_runner_errors_stay_502(monkeypatch):
    fake = FakeRunner(RunnerError("TimeoutError: page.goto timed out", "TimeoutError"))
    monkeypatch.setattr(main, "runner", fake)
    monkeypatch.setattr(main.settings, "allowed_hosts", ["x.test"])
    with pytest.raises(HTTPException) as info:
        main.inspect_page(InspectRequest(url="https://x.test/login"))
    assert info.value.status_code == 502
