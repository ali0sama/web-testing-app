# Web Testing Assistant (Human-in-the-Loop)

This assistant helps a human tester inspect a web page and plan tests with an LLM. Every model output is grounded in what the browser actually observed.

The current version is **Phase 1**: guided inspection plus one approved model call.

- Architecture, data flow and approval gates: [docs/design.md](docs/design.md)
- Sample inspection output: [samples/sample_inspection.json](samples/sample_inspection.json)

## Features (Phase 1)

- **Inspection.** Opens a URL in a **visible** Chromium window, running in a separate restricted runner process.
- **Element details.** Extracts the interactive elements with their role, accessible name, label and attributes.
- **Locators.** Builds candidate Playwright locators and checks each one on the live page: **unique**, **ambiguous** or **none**.
- **Tester controls.** The tester can:
  - narrow the scope with a CSS selector,
  - filter and select elements,
  - highlight an element in the real browser,
  - reload stored inspections.
- **Model call with approval.** The tester writes a testing goal, previews the **exact** prompt, and approves it. Only then is the model called.
- **Grounding check.** Answers are checked against the observed elements. Invented elements are flagged.
- **Usage log.** Every model call is logged with model, prompt/completion tokens (from the provider), response time, and the session average.

## Setup (Windows PowerShell)

```powershell
cd "D:\Uni Semester 7\Gen AI\Project\web-testing-assistant"
python -m venv .venv                      # skip if .venv already exists
# If activation is blocked, run this once:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .env.example .env               # then edit .env
```

Next, edit `.env`:

- **`GROQ_API_KEY`**: paste your free key from https://console.groq.com → API Keys.
- **`TARGET_URL`**: the website to test. Its host is allowed automatically. Add any other hosts to `EXTRA_ALLOWED_HOSTS`, separated by commas.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app
```

Open http://127.0.0.1:8000, then:

1. **Inspect.** The URL is pre-filled from `TARGET_URL`. Optionally enter a scope such as `form#login`, then click **Open & Inspect**. A Chromium window opens and the findings appear in the table.
2. **Select.** Tick the elements you care about. **Highlight** shows an element in the browser window.
3. **Approve and send.** Write a testing goal, click **Preview what will be sent**, read the prompt, then click **Approve & send to model**.
4. **Review.** Check the summary, the grounded next actions, the assumptions and the clarifying questions. Each call's tokens and timing appear in **Model usage**.

**Switching to another website:** change `TARGET_URL` in `.env` and restart `uvicorn`. No code changes are needed.

## Tests

```powershell
python -m pytest
```

## Model configuration

| Setting | Default |
|---|---|
| `LLM_PROVIDER` | `groq` (free tier). `ollama` is also supported for local models. |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `OLLAMA_MODEL` | `qwen2.5:7b` (requires `ollama pull qwen2.5:7b`) |

No paid API is used. Token counts come from the provider's `usage` field. Anything estimated is labeled `estimate`.

## Project layout

```
app/        FastAPI app: routes, config, storage, runner client, assistant, LLM client
runner/     isolated browser runner (separate process) + in-page extractor + locator builder
static/     web UI (HTML/CSS/JS)
docs/       design document
samples/    sample inspection output and model-usage evidence
tests/      unit tests
data/       runtime data: SQLite DB, screenshots, logs (not committed)
```

## Generated data

- `data/app.db`: inspections, elements, model calls.
- `data/screenshots/`: one full-page screenshot per inspection.
- `data/model_usage_log.jsonl`: one line per model call (copy it to `samples/` as evidence).
- `data/runner_stderr.log`: diagnostic output from the runner.

## Limitations

See section 9 of [docs/design.md](docs/design.md).
