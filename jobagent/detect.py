"""DOM → FormField extraction.

Two implementations:

- `detect_from_html(html)` — pure-Python (BeautifulSoup-free; we use
  Python's html.parser for portability). The fixture-replay tests run
  against this. It's also what the LinkedIn-modal scraper falls back
  to for steps that don't need JS interaction.
- `detect_from_page(page)` — Playwright sync API; calls into the live
  page via `page.locator(...)`. Same FormField shape; only the
  source differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from .schema import FieldKind, FormField


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    text: str = ""
    options: list[str] | None = None
    label: str | None = None


class _FormParser(HTMLParser):
    """A small HTML parser that emits FormField rows.

    Strategy:
      - Track open <label for=ID>...</label> blocks; their text becomes
        the label of the input with that id.
      - For inputs without an explicit `for`, fall back to the
        nearest preceding text node (e.g., "First name" above a bare
        <input>).
      - <option> children of <select> are accumulated into the
        select's options list.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[_Element] = []
        self._stack: list[_Element] = []
        # label-for-id → text
        self._labels: dict[str, str] = {}
        self._open_label_for: str | None = None
        self._open_label_text: list[str] = []
        # most recent text run (for unlabeled inputs)
        self._recent_text: str = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag == "label":
            self._open_label_for = a.get("for")
            self._open_label_text = []
        elif tag in ("input", "textarea", "select"):
            el = _Element(tag=tag, attrs=a)
            if tag == "select":
                el.options = []
                self._stack.append(el)
            self.elements.append(el)
        elif tag == "option" and self._stack and self._stack[-1].tag == "select":
            sel = self._stack[-1]
            label = a.get("label") or ""
            if label:
                assert sel.options is not None
                sel.options.append(label)
            else:
                # text comes via handle_data
                self._stack[-1].attrs["__pending_option"] = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "label":
            text = " ".join(self._open_label_text).strip()
            if self._open_label_for:
                self._labels[self._open_label_for] = text
            else:
                # Implicit-association labels: <label>name<input ...></label>
                if self.elements and self.elements[-1].tag in ("input", "textarea", "select"):
                    self.elements[-1].label = text
            self._open_label_for = None
            self._open_label_text = []
        elif tag == "select" and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._open_label_for is not None or self._open_label_text:
            self._open_label_text.append(data)
        if self._stack and self._stack[-1].tag == "select":
            sel = self._stack[-1]
            if "__pending_option" in sel.attrs:
                txt = data.strip()
                if txt:
                    assert sel.options is not None
                    sel.options.append(txt)
                sel.attrs.pop("__pending_option", None)
        if data.strip():
            self._recent_text = data.strip()

    def fields(self) -> list[FormField]:
        out: list[FormField] = []
        for el in self.elements:
            label = el.label or self._labels.get(el.attrs.get("id", ""), "") or self._recent_text
            kind = _normalize_kind(el)
            options = el.options or []
            field_id = el.attrs.get("id") or el.attrs.get("name") or f"anon-{len(out)}"
            required = "required" in el.attrs or el.attrs.get("aria-required") == "true"
            placeholder = el.attrs.get("placeholder") or None
            max_length = _parse_int(el.attrs.get("maxlength"))
            try:
                ff = FormField(
                    field_id=field_id,
                    label=label or el.attrs.get("name") or field_id,
                    kind=kind,
                    required=required,
                    options=options,
                    placeholder=placeholder,
                    max_length=max_length,
                )
            except ValueError:
                # Skip fields with empty labels — nothing useful to
                # classify there. The detector logs these in the run
                # so the operator can spot drift.
                continue
            out.append(ff)
        return out


def _normalize_kind(el: _Element) -> FieldKind:
    if el.tag == "textarea":
        return "textarea"
    if el.tag == "select":
        return "select"
    t = el.attrs.get("type", "text").lower()
    if t in ("text", "email", "tel", "url", "number", "date", "file", "checkbox", "radio"):
        return t  # type: ignore[return-value]
    return "text"


def _parse_int(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def detect_from_html(html: str) -> list[FormField]:
    """Parse a static HTML snippet into FormFields. Used by tests."""
    p = _FormParser()
    p.feed(html)
    return p.fields()


def detect_from_page(page: Any) -> list[FormField]:
    """Snapshot the live page's form via Playwright and run detection.

    We grab the page's HTML and reuse `detect_from_html` rather than
    poking the DOM with locator queries. The HTML snapshot is also
    saved to the run's audit trail.
    """
    html = page.content()
    return detect_from_html(html)
