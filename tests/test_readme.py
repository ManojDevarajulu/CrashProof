import re
from pathlib import Path

import pytest

from crashproof.cli import main
from crashproof.sim.report import splice_results

_ROOT = Path(__file__).resolve().parent.parent
_START = "<!-- RESULTS:START -->"
_END = "<!-- RESULTS:END -->"
_HEADINGS = (
    "## What this shows",
    "## Quick start",
    "## How it works",
    "## Limitations",
)


def readme_text() -> str:
    return (_ROOT / "README.md").read_text(encoding="utf-8")


def test_splice_replaces_only_the_marked_block() -> None:
    text = f"top\n{_START}\nold\nlines\n{_END}\nbottom\n"
    result = splice_results(text, "## New\n\nrow\n")
    assert result == f"top\n{_START}\n## New\n\nrow\n{_END}\nbottom\n"


def test_splice_is_idempotent() -> None:
    text = f"a\n{_START}\nx\n{_END}\nb\n"
    once = splice_results(text, "report\n")
    assert splice_results(once, "report\n") == once


def test_splice_strips_exactly_one_trailing_newline() -> None:
    text = f"{_START}\n{_END}\n"
    assert splice_results(text, "r\n\n") == f"{_START}\nr\n\n{_END}\n"


def test_splice_handles_report_without_trailing_newline() -> None:
    text = f"{_START}\n{_END}"
    assert splice_results(text, "r") == f"{_START}\nr\n{_END}"


@pytest.mark.parametrize(
    "text",
    [
        f"a\n{_END}\n",
        f"a\n{_START}\n",
        f"{_END}\nmid\n{_START}\n",
        f"{_START}\n{_START}\n{_END}\n",
        f"{_START}\n{_END}\n{_END}\n",
        "no markers\n",
    ],
)
def test_splice_rejects_bad_markers(text: str) -> None:
    with pytest.raises(ValueError):
        splice_results(text, "r\n")


def write_inputs(readme: str = f"# T\n{_START}\nold\n{_END}\nend\n") -> None:
    Path("README.md").write_bytes(readme.encode("utf-8"))
    Path("results").mkdir(exist_ok=True)
    Path("results/summary.md").write_bytes(b"## R\n\nrow\n")
    Path("results/summary.json").write_bytes(b'{"keep": 1}\n')


def test_cli_readme_missing_readme_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write_inputs()
    Path("README.md").unlink()
    assert main(["readme"]) == 2


def test_cli_readme_missing_report_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write_inputs()
    before = Path("README.md").read_bytes()
    Path("results/summary.md").unlink()
    assert main(["readme"]) == 2
    assert Path("README.md").read_bytes() == before


def test_cli_readme_bad_markers_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write_inputs("no markers\n")
    assert main(["readme"]) == 2
    assert Path("README.md").read_bytes() == b"no markers\n"


def test_cli_readme_is_deterministic_lf_and_leaves_results_alone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    write_inputs(f"# T\r\n{_START}\r\nold\r\n{_END}\r\nend\r\n\r\n\r\n")
    summary_json = Path("results/summary.json").read_bytes()
    summary_md = Path("results/summary.md").read_bytes()
    assert main(["readme"]) == 0
    assert "README.md updated" in capsys.readouterr().out
    first = Path("README.md").read_bytes()
    assert main(["readme"]) == 0
    assert Path("README.md").read_bytes() == first
    assert b"\r" not in first
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")
    assert b"## R\n\nrow\n" in first
    assert Path("results/summary.json").read_bytes() == summary_json
    assert Path("results/summary.md").read_bytes() == summary_md


def test_readme_has_exactly_one_marker_pair_in_order() -> None:
    text = readme_text()
    assert text.count(_START) == 1 and text.count(_END) == 1
    assert text.index(_START) < text.index(_END)


def test_readme_headings_are_in_required_order() -> None:
    text = readme_text()
    assert text.startswith("# Crashproof\n")
    positions = [text.index(f"\n{heading}\n") for heading in _HEADINGS]
    assert positions == sorted(positions)
    assert text.index(_END) < positions[0]
    assert text.rstrip("\n").splitlines()[-1].endswith("docs/ARCHITECTURE.md.")


def test_readme_mentions_simulation_and_limitations() -> None:
    text = readme_text()
    assert "simulation" in text.lower()
    assert "Limitations" in text


def test_readme_has_no_urls() -> None:
    text = readme_text()
    assert "http://" not in text and "https://" not in text


def test_readme_make_commands_exist_in_makefile() -> None:
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    targets = set(re.findall(r"^([a-z][a-z-]*):", makefile, flags=re.MULTILINE))
    mentioned = set(re.findall(r"\bmake ([a-z][a-z-]*)", readme_text()))
    assert {"setup", "test", "eval", "report", "replay", "readme"} <= mentioned
    assert mentioned <= targets


def test_readme_is_lf_with_one_trailing_newline() -> None:
    data = (_ROOT / "README.md").read_bytes()
    assert b"\r" not in data
    assert data.endswith(b"\n") and not data.endswith(b"\n\n")


def test_readme_block_matches_current_summary() -> None:
    report = _ROOT / "results" / "summary_readme.md"
    if not report.exists():
        report = _ROOT / "results" / "summary.md"
    if not report.exists():
        pytest.skip("results/summary_readme.md not generated; run make report")
    expected = splice_results(readme_text(), report.read_text(encoding="utf-8"))
    assert readme_text() == expected, "README.md is stale; run make readme"
