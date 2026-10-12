import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "docs" / "design.html"
MD = ROOT / "docs" / "DESIGN.md"
SUMMARY = ROOT / "results" / "summary.json"

FORBIDDEN = (
    "http://",
    "https://",
    "<script src",
    "<link",
    "eval(",
    "new Function",
    ".innerHTML",
    "insertAdjacentHTML",
    "document.write",
    "@import",
    "XMLHttpRequest",
    "fetch(",
    "[REPLACE",
)


def test_html_file_shape() -> None:
    raw = HTML.read_bytes()
    text = raw.decode("utf-8")
    assert b"\r" not in raw
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert len(raw) < 40_000


@pytest.mark.parametrize("token", FORBIDDEN)
def test_html_has_no_forbidden_token(token: str) -> None:
    assert token not in HTML.read_text(encoding="utf-8")


def test_html_has_sections_and_key_words() -> None:
    text = HTML.read_text(encoding="utf-8")
    for n in range(1, 10):
        assert f'<span class="num">{n:02d}</span>' in text
    assert "simulation" in text
    assert "does not show" in text


def test_html_bar_widths_match_summary() -> None:
    if not SUMMARY.exists():
        pytest.skip("results/summary.json is missing; run make eval SEEDS=1000")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    if summary["seeds"] != 1000:
        pytest.skip("results/summary.json was not produced with 1000 seeds")
    rows = re.findall(
        r"<span>(\w+)</span>.*?data-w=\"([0-9.]+)\"", HTML.read_text("utf-8")
    )
    assert sorted(name for name, _ in rows) == sorted(summary["runtimes"])
    for name, width in rows:
        violating = summary["runtimes"][name]["total_violating_seeds"]
        assert width == f"{violating / summary['seeds'] * 100:.1f}".rstrip("0").rstrip(
            "."
        ) or float(width) == round(violating / summary["seeds"] * 100, 1)


def test_markdown_covers_topics() -> None:
    text = MD.read_text(encoding="utf-8")
    assert "design.html" in text
    lowered = text.lower()
    for topic in (
        "question",
        "run",
        "fair",
        "runtimes",
        "hardened",
        "measured",
        "checkers",
        "not show",
        "next",
    ):
        assert topic in lowered
