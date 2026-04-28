from __future__ import annotations

from pathlib import Path

from jobagent.detect import detect_from_html

FIXTURES = Path(__file__).parent / "fixtures"


def test_detect_easy_apply_step1():
    html = (FIXTURES / "easy_apply_step1.html").read_text()
    fields = detect_from_html(html)
    by_id = {f.field_id: f for f in fields}

    assert "firstName" in by_id
    assert by_id["firstName"].label == "First name"
    assert by_id["firstName"].kind == "text"
    assert by_id["firstName"].required is True

    assert by_id["email"].kind == "email"
    assert by_id["phone"].kind == "tel"

    assert by_id["auth"].kind == "select"
    assert "Yes" in by_id["auth"].options
    assert "No" in by_id["auth"].options

    assert by_id["why"].kind == "textarea"
    assert by_id["why"].max_length == 500
    assert by_id["why"].required is True

    assert by_id["resume"].kind == "file"


def test_detect_handles_implicit_label_association():
    html = """
    <form>
      <label>City<input id="city" type="text"></label>
    </form>
    """
    fields = detect_from_html(html)
    assert fields[0].label == "City"


def test_detect_skips_empty_label_fields_without_crashing():
    html = '<form><input id="x" type="text"></form>'
    # No surrounding text → label falls back to the field_id itself.
    fields = detect_from_html(html)
    # We don't assert exact behavior — only that it didn't raise.
    assert len(fields) <= 1
