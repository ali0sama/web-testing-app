import pytest

from app import assistant
from app.config import Settings
from app.runner_client import safe_environment

INSPECTION = {
    "id": 1,
    "title": "Login Page",
    "final_url": "https://example.test/login",
    "elements": [
        {"element_id": "E001", "tag": "input", "role": "textbox", "name": "Username",
         "visible": True, "recommended_locator": 'page.get_by_label("Username", exact=True)'},
        {"element_id": "E002", "tag": "button", "role": "button", "name": "Login",
         "visible": True, "recommended_locator": None},
    ],
}


def test_validation_flags_invented_element():
    data = {"summary": "s", "next_actions": [
        {"element_id": "E001", "action": "fill", "reason": "r"},
        {"element_id": "E999", "action": "click", "reason": "r"},
    ]}
    result = assistant.validate_response(data, INSPECTION, ["E001"])
    assert result["next_actions"][0]["grounded"] is True
    assert result["next_actions"][0]["observed_locator"] == 'page.get_by_label("Username", exact=True)'
    assert result["next_actions"][1]["grounded"] is False
    assert any("E999" in issue for issue in result["validation_issues"])


def test_validation_flags_missing_unique_locator():
    data = {"next_actions": [{"element_id": "E002", "action": "click"}]}
    result = assistant.validate_response(data, INSPECTION, ["E002"])
    assert any("no unique locator" in issue for issue in result["validation_issues"])


def test_parse_model_json_with_extra_text():
    assert assistant.parse_model_json('Sure! {"summary": "x"} done') == {"summary": "x"}


def test_preview_requires_goal():
    with pytest.raises(assistant.AssistError):
        assistant.build_preview(INSPECTION, [], "   ")


def test_preview_rejects_unknown_ids():
    with pytest.raises(assistant.AssistError):
        assistant.build_preview(INSPECTION, ["E404"], "log in")


def test_preview_hash_changes_with_goal():
    first = assistant.build_preview(INSPECTION, ["E001"], "goal one")
    second = assistant.build_preview(INSPECTION, ["E001"], "goal two")
    assert first["preview_hash"] != second["preview_hash"]
    assert "<page_data>" in first["messages"][1]["content"]


def test_url_allowlist(monkeypatch):
    monkeypatch.setenv("TARGET_URL", "https://the-internet.herokuapp.com/login")
    monkeypatch.setenv("EXTRA_ALLOWED_HOSTS", "example.com")
    settings = Settings()
    assert settings.is_url_allowed("https://the-internet.herokuapp.com/checkboxes")
    assert settings.is_url_allowed("http://example.com")
    assert not settings.is_url_allowed("https://google.com")
    assert not settings.is_url_allowed("file:///C:/Windows/win.ini")


def test_runner_environment_has_no_secrets(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret")
    assert "GROQ_API_KEY" not in safe_environment()
