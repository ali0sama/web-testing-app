"""FastAPI web app: serves the UI and the JSON API.

Run with:  uvicorn app.main:app
"""
import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import assistant, storage
from app.config import SCREENSHOT_DIR, STATIC_DIR, settings
from app.llm.client import LLMError
from app.runner_client import RunnerError, runner
from app.schemas import AssistApproveRequest, AssistRequest, HighlightRequest, InspectRequest

# A new session id each time the server starts (used for session averages).
SESSION_ID = uuid.uuid4().hex[:12]


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    yield
    runner.stop()  # close the browser runner when the server stops


app = FastAPI(title="Web Testing Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def load_inspection(inspection_id: int) -> dict:
    inspection = storage.get_inspection(inspection_id)
    if inspection is None:
        raise HTTPException(404, f"Inspection {inspection_id} not found")
    return inspection


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config():
    return {
        "session_id": SESSION_ID,
        "target_url": settings.target_url,
        "allowed_hosts": settings.allowed_hosts,
        "provider": settings.llm_provider,
        "model": settings.model_name,
    }


# ---------------- inspection ----------------

@app.post("/api/inspect")
def inspect_page(request: InspectRequest):
    url = request.url.strip()
    if not settings.is_url_allowed(url):
        raise HTTPException(400, f"This host is not allowed. Allowed hosts: {', '.join(settings.allowed_hosts)}. "
                                 "Change TARGET_URL or EXTRA_ALLOWED_HOSTS in .env.")
    scope = (request.scope or "").strip() or None
    screenshot_name = f"inspection_{uuid.uuid4().hex[:10]}.png"
    try:
        opened = runner.call("open", {"url": url})
        result = runner.call("inspect", {
            "scope": scope,
            "include_hidden": request.include_hidden,
            "screenshot_name": screenshot_name,
        })
    except RunnerError as exc:
        raise HTTPException(502, f"Browser runner error: {exc}")
    inspection_id = storage.save_inspection(SESSION_ID, url, request.include_hidden, opened, result)
    inspection = storage.get_inspection(inspection_id)
    inspection["truncated"] = result.get("truncated", False)
    return inspection


@app.get("/api/inspections")
def list_inspections():
    return storage.list_inspections()


@app.get("/api/inspections/{inspection_id}")
def get_inspection(inspection_id: int):
    return load_inspection(inspection_id)


@app.get("/api/inspections/{inspection_id}/export")
def export_inspection(inspection_id: int):
    inspection = load_inspection(inspection_id)
    body = json.dumps(inspection, indent=2, ensure_ascii=False)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="inspection_{inspection_id}.json"'},
    )


@app.get("/api/inspections/{inspection_id}/screenshot")
def get_screenshot(inspection_id: int):
    inspection = load_inspection(inspection_id)
    if not inspection.get("screenshot"):
        raise HTTPException(404, "No screenshot for this inspection")
    path = SCREENSHOT_DIR / inspection["screenshot"]
    if not path.is_file():
        raise HTTPException(404, "Screenshot file is missing")
    return FileResponse(path, media_type="image/png")


@app.post("/api/highlight")
def highlight(request: HighlightRequest):
    inspection = load_inspection(request.inspection_id)
    element = next((e for e in inspection["elements"] if e["element_id"] == request.element_id), None)
    if element is None:
        raise HTTPException(404, f"Element {request.element_id} not found")
    locators = element.get("locators") or []
    if request.locator_index is not None:
        if not 0 <= request.locator_index < len(locators):
            raise HTTPException(400, "Invalid locator index")
        locator = locators[request.locator_index]
    else:
        locator = next((c for c in locators if c.get("status") == "unique"), None)
        if locator is None:
            raise HTTPException(409, "This element has no unique locator to highlight")
    try:
        return runner.call("highlight", {"locator": locator}, timeout=20)
    except RunnerError as exc:
        raise HTTPException(502, f"Could not highlight (is the page still open?): {exc}")


# ---------------- model assistance (approval gate) ----------------

@app.post("/api/assist/preview")
def assist_preview(request: AssistRequest):
    inspection = load_inspection(request.inspection_id)
    try:
        return assistant.build_preview(inspection, request.element_ids, request.goal)
    except assistant.AssistError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/assist")
def assist(request: AssistApproveRequest):
    inspection = load_inspection(request.inspection_id)
    try:
        return assistant.run_assist(SESSION_ID, inspection, request.element_ids,
                                    request.goal, request.approved_preview_hash)
    except assistant.AssistError as exc:
        raise HTTPException(400, str(exc))
    except LLMError as exc:
        raise HTTPException(502, str(exc))


@app.get("/api/model-calls")
def model_calls():
    return {
        "session_id": SESSION_ID,
        "session": storage.session_stats(SESSION_ID),
        "calls": storage.list_model_calls(),
    }
