// Runs INSIDE the inspected web page (via page.evaluate).
// It only READS the page: it collects interactive elements and their
// observable attributes. It does not click, type, or change anything.
(args) => {
  const scopeSelector = (args && args.scope) || null;
  const includeHidden = Boolean(args && args.includeHidden);
  const maxElements = (args && args.maxElements) || 200;

  let root = document.body;
  if (scopeSelector) {
    try {
      root = document.querySelector(scopeSelector);
    } catch (e) {
      return { error: "Invalid scope CSS selector: " + scopeSelector };
    }
    if (!root) {
      return { error: "Scope selector matched nothing: " + scopeSelector };
    }
  }

  const INTERACTIVE_SELECTOR = [
    "a[href]", "button", "input:not([type=hidden])", "select", "textarea", "summary",
    "[role=button]", "[role=link]", "[role=checkbox]", "[role=radio]", "[role=tab]",
    "[role=menuitem]", "[role=switch]", "[role=combobox]", "[role=textbox]", "[role=option]",
    "[contenteditable=''], [contenteditable=true]", "[onclick]", "[tabindex]:not([tabindex='-1'])",
  ].join(",");

  function clean(text, maxLength) {
    return (text || "").replace(/\s+/g, " ").trim().slice(0, maxLength || 120);
  }

  function isVisible(el) {
    const style = window.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden" || style.visibility === "collapse") {
      return false;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    // Positioned entirely above or left of the page (e.g. a "skip to content"
    // link parked at top: -160px until focused): a user cannot see it.
    if (rect.bottom + window.scrollY <= 0 || rect.right + window.scrollX <= 0) return false;
    return true;
  }

  // Implicit ARIA role for common elements. The Python side verifies every
  // role locator against the live page, so an imperfect guess is reported as
  // "none" instead of being trusted blindly.
  function roleOf(el) {
    const explicit = el.getAttribute("role");
    if (explicit && explicit.trim()) {
      return explicit.trim().split(/\s+/)[0];
    }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "a") return el.hasAttribute("href") ? "link" : null;
    if (tag === "button") return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return (el.multiple || el.size > 1) ? "listbox" : "combobox";
    if (tag === "input") {
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "number") return "spinbutton";
      if (type === "search") return el.hasAttribute("list") ? "combobox" : "searchbox";
      if (["", "text", "email", "tel", "url"].includes(type)) {
        return el.hasAttribute("list") ? "combobox" : "textbox";
      }
    }
    return null; // e.g. password, file and date inputs have no implicit role
  }

  function textOfIds(idList) {
    return idList.split(/\s+/).map((id) => {
      const target = document.getElementById(id);
      return target ? target.innerText || target.textContent : "";
    }).join(" ");
  }

  function labelOf(el) {
    if (el.labels && el.labels.length) {
      return clean(Array.from(el.labels).map((l) => l.innerText || l.textContent).join(" "));
    }
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) return clean(textOfIds(labelledBy));
    return "";
  }

  // Simplified accessible name (what screen readers and get_by_role use).
  function accessibleName(el, role) {
    const ariaLabel = el.getAttribute("aria-label");
    if (ariaLabel && ariaLabel.trim()) return clean(ariaLabel);
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) return clean(textOfIds(labelledBy));
    const label = labelOf(el);
    if (label) return label;

    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && ["button", "submit", "reset"].includes(type)) {
      return clean(el.value || (type === "submit" ? "Submit" : type === "reset" ? "Reset" : ""));
    }
    if (tag === "input" && type === "image") return clean(el.getAttribute("alt"));

    const nameFromContent = ["button", "link", "tab", "menuitem", "option", "checkbox", "radio", "switch"];
    if (nameFromContent.includes(role) || tag === "a" || tag === "button" || tag === "summary") {
      let text = clean(el.innerText || el.textContent);
      if (!text) {
        const img = el.querySelector("img[alt]");
        if (img) text = clean(img.getAttribute("alt"));
      }
      if (text) return text;
    }
    if (el.getAttribute("title")) return clean(el.getAttribute("title"));
    if (el.getAttribute("placeholder")) return clean(el.getAttribute("placeholder"));
    return "";
  }

  function cssPath(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      if (node.id) {
        parts.unshift("#" + CSS.escape(node.id));
        break;
      }
      const tag = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (!parent) {
        parts.unshift(tag);
        break;
      }
      const sameTag = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
      parts.unshift(sameTag.length > 1 ? tag + ":nth-of-type(" + (sameTag.indexOf(node) + 1) + ")" : tag);
      node = parent;
    }
    return parts.join(" > ");
  }

  const ATTRIBUTE_NAMES = [
    "id", "name", "type", "href", "placeholder", "aria-label", "title",
    "data-testid", "data-test", "data-qa", "for", "autocomplete",
  ];

  function attributesOf(el) {
    const attrs = {};
    for (const name of ATTRIBUTE_NAMES) {
      const value = el.getAttribute(name);
      if (value !== null && value !== "") attrs[name] = clean(value, 200);
    }
    const className = el.getAttribute("class");
    if (className) attrs["class"] = clean(className, 80);
    // Only button-like inputs expose "value"; we never read what a user typed.
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (el.tagName === "INPUT" && ["button", "submit", "reset"].includes(type) && el.value) {
      attrs["value"] = clean(el.value, 80);
    }
    if (el.required) attrs["required"] = true;
    if (el.disabled) attrs["disabled"] = true;
    if (el.readOnly) attrs["readonly"] = true;
    return attrs;
  }

  const found = Array.from(root.querySelectorAll(INTERACTIVE_SELECTOR));
  const elements = [];
  let hiddenSkipped = 0;

  for (const el of found) {
    const visible = isVisible(el);
    if (!visible && !includeHidden) {
      hiddenSkipped += 1;
      continue;
    }
    if (elements.length >= maxElements) break;

    const tag = el.tagName.toLowerCase();
    const role = roleOf(el);
    const rect = el.getBoundingClientRect();
    elements.push({
      tag: tag,
      type: (el.getAttribute("type") || "").toLowerCase() || null,
      role: role,
      name: accessibleName(el, role),
      label: labelOf(el),
      text: ["input", "select", "textarea"].includes(tag) ? "" : clean(el.innerText || el.textContent, 80),
      attributes: attributesOf(el),
      visible: visible,
      bbox: {
        x: Math.round(rect.x + window.scrollX),
        y: Math.round(rect.y + window.scrollY),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      css_path: cssPath(el),
    });
  }

  // Visible text the tester can read (headings, instructions, messages).
  // Lets later steps ground expected results and test data in what the page
  // actually says. Only visible text is kept; it is DATA, never instructions.
  const TEXT_SELECTOR = [
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "label", "legend", "caption", "figcaption",
    "li", "dt", "dd", "th", "[role=alert]", "[role=status]",
  ].join(",");
  const MAX_TEXT_BLOCKS = 60;
  const pageText = [];
  const seenText = new Set();
  for (const block of root.querySelectorAll(TEXT_SELECTOR)) {
    if (pageText.length >= MAX_TEXT_BLOCKS) break;
    // Skip a block that contains another text block (avoids duplicates such
    // as an <li> wrapping a <p>); the inner block is collected on its own.
    if (block.querySelector(TEXT_SELECTOR)) continue;
    if (!isVisible(block)) continue;
    const text = clean(block.innerText || block.textContent, 300);
    if (!text || seenText.has(text)) continue;
    seenText.add(text);
    pageText.push({
      text_id: "T" + String(pageText.length + 1).padStart(3, "0"),
      tag: block.tagName.toLowerCase(),
      text: text,
    });
  }

  return {
    page_url: window.location.href,
    title: document.title,
    scope: scopeSelector,
    total_matched: found.length,
    hidden_skipped: hiddenSkipped,
    truncated: found.length - hiddenSkipped > elements.length,
    elements: elements,
    page_text: pageText,
  };
}
