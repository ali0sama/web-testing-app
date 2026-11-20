"""Renders every docs/diagrams/*.mmd (Mermaid) file to docs/images/*.png.

Images display everywhere (VS Code preview, GitHub, PDF, Word), unlike raw
Mermaid code blocks. Re-run after editing a .mmd file:

    python docs/render_diagrams.py

Needs internet access once to load mermaid.js from the jsDelivr CDN.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

DOCS_DIR = Path(__file__).resolve().parent
SOURCE_DIR = DOCS_DIR / "diagrams"
OUTPUT_DIR = DOCS_DIR / "images"
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

PAGE_HTML = f"""<!doctype html>
<html><head><meta charset="utf-8">
<script src="{MERMAID_URL}"></script>
<style>
  body {{ margin: 0; background: #ffffff; font-family: "Segoe UI", Arial, sans-serif; }}
  #out {{ display: inline-block; padding: 20px; background: #ffffff; }}
</style></head>
<body><div id="out"></div></body></html>"""

RENDER_JS = """async (code) => {
  const { svg } = await mermaid.render("diagram" + Date.now(), code);
  const out = document.getElementById("out");
  out.innerHTML = svg;
  const element = out.querySelector("svg");
  const box = element.viewBox.baseVal;
  element.style.maxWidth = "none";
  element.setAttribute("width", box.width);
  element.setAttribute("height", box.height);
}"""


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 2400, "height": 1600}, device_scale_factor=2)
        page.set_content(PAGE_HTML, wait_until="networkidle")
        page.evaluate("mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'strict' })")
        for source in sorted(SOURCE_DIR.glob("*.mmd")):
            page.evaluate(RENDER_JS, source.read_text(encoding="utf-8"))
            target = OUTPUT_DIR / (source.stem + ".png")
            page.locator("#out").screenshot(path=str(target))
            print(f"Rendered {source.name} -> {target.relative_to(DOCS_DIR.parent)}")
        browser.close()


if __name__ == "__main__":
    main()
