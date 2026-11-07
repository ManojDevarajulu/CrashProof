import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from crashproof.clock import Clock
from crashproof.config import load_scenario_config
from crashproof.runtime.ledger import Ledger, open_ledger
from crashproof.sim.runner import run_seed

KEY = "k1"
CONFIG = load_scenario_config()


def write_executing(ledger: Ledger) -> None:
    ledger.insert_intended("wf-1", "issue_refund", KEY, {"order_id": "o1"})
    ledger.mark_executing(KEY)


def statuses(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as reader:
        rows = reader.execute("SELECT status FROM effects").fetchall()
    return [row[0] for row in rows]


def test_a_write_is_on_disk_before_the_ledger_is_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    ledger = open_ledger(path, Clock())
    write_executing(ledger)
    assert statuses(path) == ["EXECUTING"]
    ledger.close()


def test_a_plain_connection_would_lose_the_write_on_a_crash(tmp_path: Path) -> None:
    """Why open_ledger turns autocommit on: sqlite's default holds writes back."""
    path = tmp_path / "ledger.db"
    conn = sqlite3.connect(path)
    write_executing(Ledger(conn, Clock()))
    assert statuses(path) == []
    conn.close()


def test_reopening_keeps_rows_and_recover_marks_executing_unknown(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.db"
    first = open_ledger(path, Clock())
    write_executing(first)
    first.close()
    second = open_ledger(path, Clock())
    row = second.get_effect(KEY)
    assert row is not None
    assert row.status == "EXECUTING"
    assert second.recover() == [KEY]
    after = second.get_effect(KEY)
    assert after is not None
    assert after.status == "UNKNOWN"
    second.close()


def test_a_file_ledger_run_has_the_same_trace_hash_as_an_in_memory_run(
    tmp_path: Path,
) -> None:
    for seed in range(1, 21):
        in_memory = run_seed(seed, "hardened", CONFIG)
        on_disk = run_seed(
            seed, "hardened", CONFIG, ledger_path=tmp_path / f"seed-{seed}.db"
        )
        assert on_disk.trace_hash == in_memory.trace_hash
    assert (tmp_path / "seed-1.db").exists()


def test_run_seed_refuses_a_ledger_file_that_already_exists(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    run_seed(1, "hardened", CONFIG, ledger_path=path)
    with pytest.raises(FileExistsError):
        run_seed(1, "hardened", CONFIG, ledger_path=path)
