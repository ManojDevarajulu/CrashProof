import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

BANNED_CALLS = {
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "datetime.date.today",
    "time.time",
    "time.time_ns",
    "time.sleep",
    "time.monotonic",
    "time.monotonic_ns",
    "time.perf_counter",
    "time.perf_counter_ns",
    "uuid.uuid1",
    "uuid.uuid4",
    "os.urandom",
    "hash",
    "__import__",
    "eval",
    "exec",
}
BANNED_MODULES = {
    "secrets",
    "threading",
    "asyncio",
    "multiprocessing",
    "concurrent",
    "socket",
    "ssl",
    "http",
    "urllib",
    "requests",
    "httpx",
    "google.genai",
    "importlib",
}
NETWORK_MODULES = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "requests",
    "httpx",
    "google.genai",
}
# Exact (relative path, rule) exemptions required by AGENTS.md Rule 4.
RANDOM_CLASS_EXEMPT = "crashproof/rng.py"
NETWORK_EXEMPT = "crashproof/agent/llm.py"


def _matches_module(name: str, module: str) -> bool:
    return name == module or name.startswith(module + ".")


def _banned_module(name: str, rel_path: str) -> str | None:
    for module in sorted(BANNED_MODULES):
        if _matches_module(name, module):
            if rel_path == NETWORK_EXEMPT and module in NETWORK_MODULES:
                return None
            return module
    return None


def _collect_imports(
    tree: ast.AST, rel_path: str, aliases: dict[str, str], findings: list[str]
) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    aliases[top] = top
                if _banned_module(alias.name, rel_path):
                    findings.append(
                        f"{rel_path}:{node.lineno}: banned import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                aliases[alias.asname or alias.name] = full
            if _banned_module(node.module, rel_path):
                findings.append(
                    f"{rel_path}:{node.lineno}: banned import from {node.module}"
                )


def _qualified(expr: ast.expr, aliases: dict[str, str]) -> str | None:
    if isinstance(expr, ast.Name):
        return aliases.get(expr.id, expr.id)
    if isinstance(expr, ast.Attribute):
        base = _qualified(expr.value, aliases)
        return None if base is None else f"{base}.{expr.attr}"
    return None


def _is_banned_call(name: str, rel_path: str) -> bool:
    if name in BANNED_CALLS:
        return True
    if name == "random.Random":
        return rel_path != RANDOM_CLASS_EXEMPT
    return name.startswith("random.")


def scan_source(source: str, rel_path: str) -> list[str]:
    tree = ast.parse(source)
    aliases: dict[str, str] = {}
    findings: list[str] = []
    _collect_imports(tree, rel_path, aliases, findings)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _qualified(node.func, aliases)
        if name is not None and _is_banned_call(name, rel_path):
            findings.append(f"{rel_path}:{node.lineno}: banned call {name}")
    return findings


def scan_tree(root: Path) -> tuple[int, list[str]]:
    files = sorted(root.rglob("*.py"))
    findings: list[str] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        findings.extend(scan_source(path.read_text(encoding="utf-8"), rel))
    return len(files), findings


def test_src_is_clean() -> None:
    visited, findings = scan_tree(SRC)
    assert visited >= 1
    assert findings == []


def test_scan_visits_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    visited, findings = scan_tree(tmp_path)
    assert visited == 1
    assert findings == []


def test_detects_time_sleep() -> None:
    assert scan_source("import time\ntime.sleep(1)\n", "crashproof/x.py")


def test_detects_aliased_from_import() -> None:
    assert scan_source("from time import sleep as s\ns(1)\n", "crashproof/x.py")


def test_detects_aliased_module_datetime_now() -> None:
    assert scan_source("import datetime as dt\ndt.datetime.now()\n", "crashproof/x.py")


def test_detects_from_datetime_import() -> None:
    assert scan_source(
        "from datetime import datetime\ndatetime.now()\n", "crashproof/x.py"
    )


def test_detects_module_random() -> None:
    assert scan_source("import random\nrandom.random()\n", "crashproof/x.py")


def test_detects_random_class_outside_rng() -> None:
    assert scan_source("import random\nrandom.Random(1)\n", "crashproof/x.py")


def test_detects_threading_import() -> None:
    assert scan_source("import threading\n", "crashproof/x.py")


def test_detects_uuid_and_urandom() -> None:
    assert scan_source("import uuid\nuuid.uuid4()\n", "crashproof/x.py")
    assert scan_source("import os\nos.urandom(4)\n", "crashproof/x.py")


def test_detects_hash_eval_exec_dunder_import_importlib() -> None:
    for src in (
        "hash('a')\n",
        "eval('1')\n",
        "exec('x=1')\n",
        "__import__('os')\n",
        "import importlib\n",
        "from importlib import import_module\n",
    ):
        assert scan_source(src, "crashproof/x.py"), src


def test_rng_exemption_allows_random_class_only() -> None:
    assert scan_source("import random\nrandom.Random(1)\n", "crashproof/rng.py") == []
    assert scan_source("import random\nrandom.random()\n", "crashproof/rng.py")


def test_llm_network_exemption_is_scoped_to_that_path() -> None:
    assert scan_source("import urllib.request\n", "crashproof/agent/llm.py") == []
    assert (
        scan_source("from google.genai import Client\n", "crashproof/agent/llm.py")
        == []
    )
    assert scan_source("import urllib.request\n", "crashproof/other.py")
    assert scan_source("from google.genai import Client\n", "crashproof/other.py")


def test_llm_exemption_does_not_cover_other_rules() -> None:
    assert scan_source("import time\ntime.time()\n", "crashproof/agent/llm.py")
    assert scan_source("import threading\n", "crashproof/agent/llm.py")
