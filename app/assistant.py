"""Assistant logic: build the prompt from stored inspection data, call the
model only after the tester approved the exact prompt, and check that the
answer refers only to elements that were really observed."""
import hashlib
import json

from app import storage
from app.config import settings
from app.llm.client import LLMError, chat, estimate_tokens
from app.llm.prompts import build_assist_messages

MAX_ELEMENTS_IN_PROMPT = 80


class AssistError(Exception):
    pass


def select_elements(inspection: dict, element_ids: list[str]) -> list[dict]:
    """Selected elements, in page order. No selection = all observed elements."""
    if not element_ids:
        selected = list(inspection["elements"])
    else:
        wanted = set(element_ids)
        known = {e["element_id"] for e in inspection["elements"]}
        unknown = wanted - known
        if unknown:
            raise AssistError(f"Unknown element ids for this inspection: {sorted(unknown)}")
        selected = [e for e in inspection["elements"] if e["element_id"] in wanted]
    if len(selected) > MAX_ELEMENTS_IN_PROMPT:
        raise AssistError(
            f"{len(selected)} elements selected; please select at most {MAX_ELEMENTS_IN_PROMPT} "
            "or narrow the inspection scope."
        )
    return selected


def messages_hash(messages: list[dict]) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True).encode("utf-8")).hexdigest()


def build_preview(inspection: dict, element_ids: list[str], goal: str) -> dict:
    if not goal.strip():
        raise AssistError("Please describe a testing goal first.")
    selected = select_elements(inspection, element_ids)
    messages = build_assist_messages(inspection, selected, goal)
    return {
        "provider": settings.llm_provider,
        "model": settings.model_name,
        "messages": messages,
        "preview_hash": messages_hash(messages),
        "estimated_prompt_tokens": estimate_tokens("".join(m["content"] for m in messages)),
        "element_ids": [e["element_id"] for e in selected],
    }


def parse_model_json(text: str) -> dict:
    """Parse the model's JSON. Falls back to the outermost {...} block."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def as_text_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def test_data_source(hint: str, goal: str, inspection: dict) -> str:
    """Where a suggested test value comes from:
    'goal' / 'page_text' = found there (grounded), 'placeholder' = <...> the
    tester must fill in, 'unverified' = not observed anywhere (model knowledge
    or invention), '' = no test data."""
    hint = hint.strip()
    if not hint:
        return ""
    if hint.startswith("<") and hint.endswith(">"):
        return "placeholder"
    needle = hint.lower()
    if needle in goal.lower():
        return "goal"
    if any(needle in block.get("text", "").lower() for block in inspection.get("page_text") or []):
        return "page_text"
    return "unverified"


def validate_response(data: dict, inspection: dict, selected_ids: list[str], goal: str = "") -> dict:
    """Mark every suggested action as grounded (element was observed) or not,
    attach the OBSERVED locator instead of trusting any model-made one, and
    check that suggested test data was actually observed or given by the tester."""
    elements_by_id = {e["element_id"]: e for e in inspection["elements"]}
    selected = set(selected_ids)
    issues = []
    actions = []
    for item in data.get("next_actions") or []:
        if not isinstance(item, dict):
            continue
        element_id = str(item.get("element_id", "")).strip()
        element = elements_by_id.get(element_id)
        hint = str(item.get("test_data_hint", "") or "")
        action = {
            "element_id": element_id,
            "action": str(item.get("action", "")),
            "reason": str(item.get("reason", "")),
            "test_data_hint": hint,
            "test_data_source": test_data_source(hint, goal, inspection),
            "grounded": element is not None,
            "in_selection": element_id in selected,
            "observed_locator": element.get("recommended_locator") if element else None,
        }
        if element is None:
            issues.append(f"Model referenced '{element_id}', which was NOT observed in inspection #{inspection['id']}.")
        elif element_id not in selected:
            issues.append(f"'{element_id}' was observed but is outside your selection.")
        elif not action["observed_locator"]:
            issues.append(f"'{element_id}' has no unique locator; it needs a better locator before testing.")
        if action["test_data_source"] == "unverified":
            issues.append(f"Test data '{hint}' for '{element_id}' is not in your goal or the observed page text; "
                          "confirm it before using it.")
        actions.append(action)
    return {
        "summary": str(data.get("summary", "")),
        "next_actions": actions,
        "assumptions": as_text_list(data.get("assumptions")),
        "clarifying_questions": as_text_list(data.get("clarifying_questions")),
        "validation_issues": issues,
    }


def run_assist(session_id: str, inspection: dict, element_ids: list[str], goal: str,
               approved_preview_hash: str) -> dict:
    preview = build_preview(inspection, element_ids, goal)
    if approved_preview_hash != preview["preview_hash"]:
        raise AssistError("The prompt changed after you approved it. Preview and approve it again.")

    base_record = {
        "session_id": session_id,
        "inspection_id": inspection["id"],
        "purpose": "summarize_and_suggest",
        "provider": settings.llm_provider,
        "model": settings.model_name,
    }
    try:
        result = chat(preview["messages"])
    except LLMError as exc:
        storage.save_model_call({**base_record, "ok": False, "error": str(exc)}, messages=preview["messages"])
        raise

    record = {
        **base_record,
        "model": result.model,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "token_source": result.token_source,
        "latency_ms": result.latency_ms,
        "ok": True,
    }
    record["id"] = storage.save_model_call(record, messages=preview["messages"], response_text=result.text)

    try:
        validated = validate_response(parse_model_json(result.text), inspection, preview["element_ids"], goal)
    except (json.JSONDecodeError, AttributeError):
        validated = {
            "summary": "", "next_actions": [], "assumptions": [], "clarifying_questions": [],
            "validation_issues": ["The model did not return valid JSON. Raw text is shown below."],
        }
    storage.save_model_call_result(record["id"], validated)
    return {"call": record, "result": validated, "raw_text": result.text}
