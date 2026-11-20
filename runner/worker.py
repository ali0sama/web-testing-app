"""Isolated browser runner.

This script runs as a SEPARATE process from the web app. The app talks to it
with one JSON object per line over stdin/stdout. It accepts only a fixed set
of commands (see HANDLERS) - it never runs arbitrary code or shell commands.

Restrictions:
- Started with an environment that has no API keys (see app/runner_client.py).
- Working directory is the data/ folder; screenshots may only be written to
  the configured screenshot folder.
- Page navigations to hosts outside the allowlist are blocked.
"""
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from locators import build_candidates, choose_recommended, status_from_count

SCRIPT_DIR = Path(__file__).resolve().parent
EXTRACT_JS = (SCRIPT_DIR / "extract_elements.js").read_text(encoding="utf-8")
MAX_ELEMENTS = 200
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+\.png$")
HIGHLIGHT_JS = """el => {
  const oldOutline = el.style.outline;
  const oldOffset = el.style.outlineOffset;
  el.style.outline = '3px solid #e11d48';
  el.style.outlineOffset = '2px';
  setTimeout(() => { el.style.outline = oldOutline; el.style.outlineOffset = oldOffset; }, 3000);
}"""


class BrowserSession:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.allowed_hosts = set()
        self.screenshot_dir = None

    def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme in ("about", "data", "blob"):
            return True
        if parsed.scheme not in ("http", "https"):
            return False
        return (parsed.hostname or "").lower() in self.allowed_hosts

    def guard_request(self, route, request):
        # Block page navigations (typed URL, links, redirects) to other hosts.
        # Images/scripts/styles from CDNs are still allowed so pages render.
        if request.is_navigation_request() and not self.is_allowed(request.url):
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def get_page(self):
        if self.playwright is None:
            self.playwright = sync_playwright().start()
        if self.browser is None or not self.browser.is_connected():
            self.browser = self.playwright.chromium.launch(headless=False)
            self.context = None
        if self.context is None or self.page is None or self.page.is_closed():
            if self.context is not None:
                try:
                    self.context.close()
                except Exception:
                    pass
            self.context = self.browser.new_context(viewport={"width": 1280, "height": 800})
            self.context.route("**/*", self.guard_request)
            self.page = self.context.new_page()
        return self.page

    def close(self):
        for thing in (self.context, self.browser):
            try:
                if thing is not None:
                    thing.close()
            except Exception:
                pass
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass


def to_locator(page, candidate: dict):
    """Turn a stored candidate back into a real Playwright locator."""
    kind = candidate["kind"]
    value = candidate["value"]
    if kind == "test_id":
        return page.get_by_test_id(value)
    if kind == "role":
        return page.get_by_role(candidate["role"], name=value, exact=True)
    if kind == "label":
        return page.get_by_label(value, exact=True)
    if kind == "placeholder":
        return page.get_by_placeholder(value, exact=True)
    if kind == "text":
        return page.get_by_text(value, exact=True)
    if kind in ("id", "css"):
        return page.locator(value)
    raise ValueError(f"Unknown locator kind: {kind}")


# ---------------- command handlers ----------------

def handle_configure(session: BrowserSession, args: dict):
    session.allowed_hosts = {h.lower() for h in args.get("allowed_hosts", [])}
    session.screenshot_dir = Path(args["screenshot_dir"]).resolve()
    session.screenshot_dir.mkdir(parents=True, exist_ok=True)
    return {"allowed_hosts": sorted(session.allowed_hosts)}


def handle_open(session: BrowserSession, args: dict):
    url = args["url"]
    if not session.is_allowed(url):
        raise PermissionError(f"Host not allowed by runner: {url}")
    page = session.get_page()
    started = time.perf_counter()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # some pages never become fully idle; DOM is already loaded
    page.bring_to_front()
    return {
        "final_url": page.url,
        "title": page.title(),
        "load_ms": round((time.perf_counter() - started) * 1000),
    }


def handle_inspect(session: BrowserSession, args: dict):
    page = session.get_page()
    if page.url in ("", "about:blank"):
        raise RuntimeError("No page is open. Open a URL first.")

    started = time.perf_counter()
    result = page.evaluate(EXTRACT_JS, {
        "scope": args.get("scope") or None,
        "includeHidden": bool(args.get("include_hidden")),
        "maxElements": MAX_ELEMENTS,
    })
    if result.get("error"):
        raise ValueError(result["error"])

    # Validate every candidate locator against the live page (uniqueness).
    for number, element in enumerate(result["elements"], start=1):
        element["element_id"] = f"E{number:03d}"
        candidates = build_candidates(element)
        for candidate in candidates:
            try:
                count = to_locator(page, candidate).count()
                candidate["count"] = count
                candidate["status"] = status_from_count(count)
            except Exception as exc:
                candidate["count"] = None
                candidate["status"] = "error"
                candidate["error"] = str(exc).splitlines()[0][:200]
        element["locators"] = candidates
        recommended = choose_recommended(candidates)
        element["recommended_locator"] = recommended["code"] if recommended else None

    dom_hash = hashlib.sha256(page.content().encode("utf-8")).hexdigest()

    screenshot_name = args.get("screenshot_name")
    if screenshot_name:
        if not SAFE_FILENAME.match(screenshot_name) or session.screenshot_dir is None:
            raise PermissionError("Screenshot name is not allowed")
        page.screenshot(path=str(session.screenshot_dir / screenshot_name), full_page=True)

    result["dom_hash"] = dom_hash
    result["screenshot"] = screenshot_name
    result["inspect_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def handle_highlight(session: BrowserSession, args: dict):
    page = session.get_page()
    locator = to_locator(page, args["locator"])
    count = locator.count()
    if count == 0:
        raise LookupError("Locator matches no element on the current page")
    target = locator.first
    target.scroll_into_view_if_needed(timeout=5000)
    target.evaluate(HIGHLIGHT_JS)
    page.bring_to_front()
    return {"count": count}


def handle_ping(session: BrowserSession, args: dict):
    return {"pong": True}


HANDLERS = {
    "configure": handle_configure,
    "open": handle_open,
    "inspect": handle_inspect,
    "highlight": handle_highlight,
    "ping": handle_ping,
}


def main():
    session = BrowserSession()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        message = {}
        try:
            message = json.loads(line)
            command = message.get("cmd")
            if command == "shutdown":
                reply = {"ok": True, "data": {}}
            else:
                handler = HANDLERS.get(command)
                if handler is None:
                    raise ValueError(f"Unknown command: {command}")
                reply = {"ok": True, "data": handler(session, message.get("args") or {})}
        except Exception as exc:
            reply = {"ok": False, "error": f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"}
        reply["id"] = message.get("id") if isinstance(message, dict) else None
        # ensure_ascii keeps stdout plain ASCII, avoiding Windows encoding issues.
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()
        if isinstance(message, dict) and message.get("cmd") == "shutdown":
            break
    session.close()


if __name__ == "__main__":
    main()
