from pathlib import Path

VIEWER = Path(__file__).resolve().parent.parent / "viewer" / "index.html"

FORBIDDEN = [
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
    "import(",
    "XMLHttpRequest",
    "fetch(",
]
REQUIRED = ["agent", "runtime", "policy", "world", "fault", "human", "FAULT", "SHA-256"]


def _raw() -> bytes:
    return VIEWER.read_bytes()


def test_viewer_exists_and_is_utf8() -> None:
    assert VIEWER.is_file()
    _raw().decode("utf-8")


def test_viewer_uses_lf_and_ends_with_one_newline() -> None:
    raw = _raw()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")


def test_viewer_has_no_forbidden_constructs() -> None:
    text = _raw().decode("utf-8")
    found = [item for item in FORBIDDEN if item in text]
    assert found == []


def test_viewer_contains_required_text() -> None:
    text = _raw().decode("utf-8")
    missing = [item for item in REQUIRED if item not in text]
    assert missing == []


def test_viewer_is_small() -> None:
    assert len(_raw()) < 60_000
