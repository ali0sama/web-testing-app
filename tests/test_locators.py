from runner.locators import build_candidates, choose_recommended, status_from_count

USERNAME_INPUT = {
    "tag": "input",
    "type": "text",
    "role": "textbox",
    "name": "Username",
    "label": "Username",
    "text": "",
    "attributes": {"id": "username", "name": "username"},
    "css_path": "#username",
}


def test_candidates_follow_priority_order():
    kinds = [c["kind"] for c in build_candidates(USERNAME_INPUT)]
    assert kinds == ["role", "label", "id"]  # css path "#username" removed as duplicate


def test_role_candidate_code():
    role = build_candidates(USERNAME_INPUT)[0]
    assert role["code"] == 'page.get_by_role("textbox", name="Username", exact=True)'


def test_test_id_comes_first():
    element = dict(USERNAME_INPUT, attributes={"data-testid": "user", "id": "username"})
    assert build_candidates(element)[0]["code"] == 'page.get_by_test_id("user")'


def test_unusual_id_uses_attribute_selector():
    element = dict(USERNAME_INPUT, attributes={"id": "1:weird"}, css_path="")
    id_candidate = [c for c in build_candidates(element) if c["kind"] == "id"][0]
    assert id_candidate["value"] == '[id="1:weird"]'


def test_quotes_are_escaped():
    element = {"tag": "button", "role": "button", "name": 'Say "hi"', "attributes": {}}
    code = build_candidates(element)[0]["code"]
    assert code == 'page.get_by_role("button", name="Say \\"hi\\"", exact=True)'


def test_status_from_count():
    assert status_from_count(0) == "none"
    assert status_from_count(1) == "unique"
    assert status_from_count(3) == "ambiguous"


def test_recommended_is_first_unique():
    candidates = [
        {"code": "a", "status": "none"},
        {"code": "b", "status": "ambiguous"},
        {"code": "c", "status": "unique"},
        {"code": "d", "status": "unique"},
    ]
    assert choose_recommended(candidates)["code"] == "c"
    assert choose_recommended([{"code": "a", "status": "ambiguous"}]) is None
