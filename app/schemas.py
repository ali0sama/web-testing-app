"""Shapes of the JSON request bodies the web UI sends to the API."""
from pydantic import BaseModel, Field


class InspectRequest(BaseModel):
    url: str
    scope: str | None = None          # optional CSS selector to limit inspection
    include_hidden: bool = False


class HighlightRequest(BaseModel):
    inspection_id: int
    element_id: str
    locator_index: int | None = None  # which candidate to use; default = recommended


class AssistRequest(BaseModel):
    inspection_id: int
    element_ids: list[str] = Field(default_factory=list)
    goal: str


class AssistApproveRequest(AssistRequest):
    approved_preview_hash: str        # proves the tester approved this exact prompt
