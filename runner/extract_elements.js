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
    return rect.width > 0 && rect.height > 0;
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

  return {
    page_url: window.location.href,
    title: document.title,
    scope: scopeSelector,
    total_matched: found.length,
    hidden_skipped: hiddenSkipped,
    truncated: found.length - hiddenSkipped > elements.length,
    elements: elements,
  };
}
