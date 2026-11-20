"""Builds candidate Playwright locators from one extracted element.

This module is pure Python (no browser), so it can be unit tested.
The worker later counts how many elements each candidate matches on the
live page, which tells us whether the locator is unique.

Candidates are listed in Playwright's recommended priority order:
test id -> role + name -> label -> placeholder -> id -> text -> CSS path.
"""
import json
import re

SIMPLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
TEXT_TAGS_SKIPPED = {"input", "select", "textarea"}
MAX_TEXT_LENGTH = 60


def py_str(value: str) -> str:
    """Quote a value as a Python/JS string literal, e.g. Login -> "Login"."""
    return json.dumps(value, ensure_ascii=False)


def css_attr(attr: str, value: str) -> str:
    """Build a CSS attribute selector such as [data-test="login"]."""
    return "[" + attr + "=" + py_str(value) + "]"


def build_candidates(element: dict) -> list[dict]:
    attrs = element.get("attributes") or {}
    candidates = []

    test_id = attrs.get("data-testid")
    if test_id:
        candidates.append({
            "kind": "test_id",
            "value": test_id,
            "code": f"page.get_by_test_id({py_str(test_id)})",
        })

    for test_attr in ("data-test", "data-qa"):
        if attrs.get(test_attr):
            selector = css_attr(test_attr, attrs[test_attr])
            candidates.append({
                "kind": "css",
                "value": selector,
                "code": f"page.locator({py_str(selector)})",
            })

    role = element.get("role")
    name = element.get("name")
    if role and name:
        candidates.append({
            "kind": "role",
            "role": role,
            "value": name,
            "code": f"page.get_by_role({py_str(role)}, name={py_str(name)}, exact=True)",
        })

    label = element.get("label")
    if label:
        candidates.append({
            "kind": "label",
            "value": label,
            "code": f"page.get_by_label({py_str(label)}, exact=True)",
        })

    placeholder = attrs.get("placeholder")
    if placeholder:
        candidates.append({
            "kind": "placeholder",
            "value": placeholder,
            "code": f"page.get_by_placeholder({py_str(placeholder)}, exact=True)",
        })

    element_id = attrs.get("id")
    if element_id:
        if SIMPLE_ID.match(element_id):
            selector = "#" + element_id
        else:
            selector = css_attr("id", element_id)
        candidates.append({
            "kind": "id",
            "value": selector,
            "code": f"page.locator({py_str(selector)})",
        })

    text = element.get("text")
    if text and element.get("tag") not in TEXT_TAGS_SKIPPED and len(text) <= MAX_TEXT_LENGTH:
        candidates.append({
            "kind": "text",
            "value": text,
            "code": f"page.get_by_text({py_str(text)}, exact=True)",
        })

    css_path = element.get("css_path")
    if css_path:
        candidates.append({
            "kind": "css",
            "value": css_path,
            "code": f"page.locator({py_str(css_path)})",
        })

    # Remove duplicates (e.g. css_path "#username" equals the id candidate).
    unique = []
    seen_values = set()
    for candidate in candidates:
        key = candidate["code"]
        if key in seen_values:
            continue
        seen_values.add(key)
        unique.append(candidate)
    return unique


def status_from_count(count: int) -> str:
    if count == 1:
        return "unique"
    if count == 0:
        return "none"
    return "ambiguous"


def choose_recommended(candidates: list[dict]):
    """Return the highest-priority candidate that matched exactly one element."""
    for candidate in candidates:
        if candidate.get("status") == "unique":
            return candidate
    return None
