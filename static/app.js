// Front-end logic. All text that comes from inspected pages or the model is
// inserted with textContent (never innerHTML), so page content stays data.

const state = {
  inspection: null,
  selected: new Set(),
  preview: null,
};

const $ = (id) => document.getElementById(id);

// Small helper to create an element with text and optional class.
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

async function api(path, options) {
  const opts = options || {};
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch(path, opts);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }
  return data;
}

function setStatus(id, message, isError) {
  const node = $(id);
  node.textContent = message || "";
  node.className = "status" + (isError ? " error" : "");
}

// ---------------- config + history ----------------

async function loadConfig() {
  const config = await api("/api/config");
  $("url").value = config.target_url;
  $("allowed-hosts").textContent = "Allowed hosts: " + config.allowed_hosts.join(", ") +
    "  (change TARGET_URL / EXTRA_ALLOWED_HOSTS in .env)";
  $("model-info").textContent = `Model: ${config.provider} / ${config.model}  |  session ${config.session_id}`;
}

async function loadHistory() {
  const items = await api("/api/inspections");
  const select = $("history");
  select.length = 1;
  for (const item of items) {
    const option = el("option", `#${item.id}  ${item.title || ""}  ${item.final_url}  ` +
      `${item.scope ? "[scope " + item.scope + "] " : ""}(${item.element_count} elements, ${item.created_at})`);
    option.value = item.id;
    select.appendChild(option);
  }
}

// ---------------- inspection ----------------

async function inspect() {
  const button = $("inspect-btn");
  button.disabled = true;
  setStatus("inspect-status", "Opening the page in the browser and inspecting...");
  try {
    const inspection = await api("/api/inspect", {
      method: "POST",
      body: { url: $("url").value, scope: $("scope").value, include_hidden: $("include-hidden").checked },
    });
    showInspection(inspection);
    setStatus("inspect-status",
      `Done. Browser time: page load ${inspection.load_ms} ms, inspection + locator checks ${inspection.inspect_ms} ms.` +
      (inspection.truncated ? " Element limit reached; narrow the scope." : ""));
    loadHistory();
  } catch (error) {
    setStatus("inspect-status", error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadStoredInspection(id) {
  if (!id) return;
  try {
    showInspection(await api(`/api/inspections/${id}`));
    setStatus("inspect-status", `Loaded stored inspection #${id} (not re-scanned). Highlighting works only if that page is open in the browser.`);
  } catch (error) {
    setStatus("inspect-status", error.message, true);
  }
}

function showInspection(inspection) {
  state.inspection = inspection;
  state.selected = new Set();
  hidePreview();
  $("findings-card").hidden = false;
  $("goal-card").hidden = false;
  $("inspection-meta").textContent =
    `Inspection #${inspection.id} | ${inspection.title} | ${inspection.final_url} | ` +
    `scope: ${inspection.scope || "whole page"} | ${inspection.element_count} elements ` +
    `(${inspection.hidden_skipped || 0} hidden skipped) | ${inspection.created_at} | DOM hash ${(inspection.dom_hash || "").slice(0, 12)}`;
  $("browser-timing").textContent =
    `Browser/runner time (separate from model time): page load ${inspection.load_ms} ms, ` +
    `inspection + locator checks ${inspection.inspect_ms} ms`;
  $("export-link").href = `/api/inspections/${inspection.id}/export`;
  $("screenshot-link").href = `/api/inspections/${inspection.id}/screenshot`;
  renderElements();
}

function elementMatchesFilter(element, filter) {
  if (!filter) return true;
  const haystack = JSON.stringify([element.element_id, element.tag, element.role, element.name,
    element.label, element.text, element.attributes]).toLowerCase();
  return haystack.includes(filter.toLowerCase());
}

function shownElements() {
  const filter = $("filter").value.trim();
  return state.inspection.elements.filter((e) => elementMatchesFilter(e, filter));
}

function renderElements() {
  const body = $("elements-body");
  body.textContent = "";
  for (const element of shownElements()) {
    const row = el("tr");
    if (state.selected.has(element.element_id)) row.className = "selected";

    const checkCell = el("td");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selected.has(element.element_id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selected.add(element.element_id);
      else state.selected.delete(element.element_id);
      row.className = checkbox.checked ? "selected" : "";
      updateSelectionCount();
      hidePreview();
    });
    checkCell.appendChild(checkbox);
    row.appendChild(checkCell);

    row.appendChild(el("td", element.element_id));

    const kind = el("td");
    kind.appendChild(el("div", `<${element.tag}${element.type ? " type=" + element.type : ""}>`));
    kind.appendChild(el("div", "role: " + (element.role || "-"), "muted small"));
    if (!element.visible) kind.appendChild(el("div", "hidden", "no small"));
    row.appendChild(kind);

    const nameCell = el("td");
    nameCell.appendChild(el("div", element.name || "(no accessible name)"));
    if (element.label) nameCell.appendChild(el("div", "label: " + element.label, "muted small"));
    row.appendChild(nameCell);

    const attrs = Object.entries(element.attributes || {})
      .map(([key, value]) => `${key}=${value}`).join("\n");
    const attrCell = el("td");
    attrCell.appendChild(el("code", attrs));
    attrCell.style.whiteSpace = "pre-wrap";
    row.appendChild(attrCell);

    const locCell = el("td");
    (element.locators || []).forEach((locator) => {
      const line = el("span", null, "loc");
      line.appendChild(el("span", `${locator.status} (${locator.count ?? "?"})`, "badge " + locator.status));
      line.appendChild(el("code", locator.code));
      if (locator.code === element.recommended_locator) line.appendChild(el("span", "  ★ recommended", "star"));
      locCell.appendChild(line);
    });
    if (!element.recommended_locator) locCell.appendChild(el("div", "No unique locator found", "no small"));
    row.appendChild(locCell);

    const actionCell = el("td");
    const highlightButton = el("button", "Highlight");
    highlightButton.disabled = !element.recommended_locator;
    highlightButton.addEventListener("click", () => highlight(element.element_id));
    actionCell.appendChild(highlightButton);
    row.appendChild(actionCell);

    body.appendChild(row);
  }
  updateSelectionCount();
}

function updateSelectionCount() {
  $("selection-count").textContent = `${state.selected.size} selected`;
}

async function highlight(elementId) {
  try {
    await api("/api/highlight", { method: "POST", body: { inspection_id: state.inspection.id, element_id: elementId } });
    setStatus("inspect-status", `${elementId} highlighted in the browser window.`);
  } catch (error) {
    setStatus("inspect-status", error.message, true);
  }
}

// ---------------- approval gate + model call ----------------

function assistBody() {
  return {
    inspection_id: state.inspection.id,
    element_ids: Array.from(state.selected),
    goal: $("goal").value,
  };
}

function hidePreview() {
  state.preview = null;
  $("preview-box").hidden = true;
}

async function preview() {
  setStatus("assist-status", "");
  try {
    const result = await api("/api/assist/preview", { method: "POST", body: assistBody() });
    state.preview = result;
    $("preview-meta").textContent =
      `Will send ${result.element_ids.length} element(s) to ${result.provider} / ${result.model}. ` +
      `Estimated prompt size: ~${result.estimated_prompt_tokens} tokens (estimate, chars/4). Review, then approve.`;
    $("preview-text").textContent = result.messages
      .map((m) => `===== ${m.role.toUpperCase()} =====\n${m.content}`).join("\n\n");
    $("preview-box").hidden = false;
  } catch (error) {
    setStatus("assist-status", error.message, true);
  }
}

async function approveAndSend() {
  if (!state.preview) return;
  const button = $("approve-btn");
  button.disabled = true;
  setStatus("assist-status", "Waiting for the model...");
  try {
    const response = await api("/api/assist", {
      method: "POST",
      body: { ...assistBody(), approved_preview_hash: state.preview.preview_hash },
    });
    hidePreview();
    setStatus("assist-status", "");
    showResult(response);
  } catch (error) {
    setStatus("assist-status", error.message, true);
  } finally {
    button.disabled = false;
    loadCalls();
  }
}

function fillList(id, items) {
  const list = $(id);
  list.textContent = "";
  if (!items.length) list.appendChild(el("li", "(none)", "muted"));
  for (const item of items) list.appendChild(el("li", item));
}

function showResult(response) {
  const { call, result, raw_text: rawText } = response;
  $("result-card").hidden = false;
  $("result-meta").textContent =
    `Call #${call.id} | ${call.provider} / ${call.model} | model response time ${call.latency_ms} ms | ` +
    `tokens: ${call.prompt_tokens} prompt + ${call.completion_tokens} completion = ${call.total_tokens} (${call.token_source})`;
  $("result-summary").textContent = result.summary || "(no summary)";

  const body = $("actions-body");
  body.textContent = "";
  for (const action of result.next_actions) {
    const row = el("tr");
    row.appendChild(el("td", action.element_id));
    row.appendChild(el("td", action.grounded ? "yes, observed" : "NO, not observed", action.grounded ? "yes" : "no"));
    row.appendChild(el("td", action.action));
    const locCell = el("td");
    locCell.appendChild(el("code", action.observed_locator || "-"));
    row.appendChild(locCell);
    row.appendChild(el("td", action.reason));
    row.appendChild(el("td", action.test_data_hint));
    body.appendChild(row);
  }
  fillList("assumptions", result.assumptions);
  fillList("questions", result.clarifying_questions);

  const validation = $("validation");
  validation.hidden = result.validation_issues.length === 0;
  validation.textContent = "Validation: " + result.validation_issues.join(" ");
  $("raw-output").textContent = rawText;
}

// ---------------- usage table ----------------

async function loadCalls() {
  const data = await api("/api/model-calls");
  const s = data.session;
  $("session-stats").textContent =
    `This session (${data.session_id}): ${s.successful_calls} successful call(s), ` +
    `average model response time ${s.avg_latency_ms === null ? "-" : s.avg_latency_ms + " ms"}, ` +
    `${s.total_tokens} tokens total. Browser/inspection time is shown separately in section 1.`;
  const body = $("calls-body");
  body.textContent = "";
  for (const call of data.calls) {
    const row = el("tr");
    [call.id, call.created_at, `${call.provider} / ${call.model}`, call.prompt_tokens ?? "-",
      call.completion_tokens ?? "-", call.total_tokens ?? "-", call.token_source || "-",
      call.latency_ms != null ? call.latency_ms + " ms" : "-",
    ].forEach((value) => row.appendChild(el("td", value)));
    row.appendChild(el("td", call.ok ? "ok" : "failed: " + call.error, call.ok ? "yes" : "no"));
    body.appendChild(row);
  }
}

// ---------------- wiring ----------------

$("inspect-btn").addEventListener("click", inspect);
$("history").addEventListener("change", (e) => loadStoredInspection(e.target.value));
$("filter").addEventListener("input", renderElements);
$("select-all").addEventListener("click", () => {
  shownElements().forEach((e) => state.selected.add(e.element_id));
  hidePreview();
  renderElements();
});
$("select-none").addEventListener("click", () => {
  state.selected.clear();
  hidePreview();
  renderElements();
});
$("goal").addEventListener("input", hidePreview);
$("preview-btn").addEventListener("click", preview);
$("approve-btn").addEventListener("click", approveAndSend);
$("cancel-btn").addEventListener("click", hidePreview);

loadConfig().catch((e) => setStatus("inspect-status", e.message, true));
loadHistory().catch(() => {});
loadCalls().catch(() => {});
