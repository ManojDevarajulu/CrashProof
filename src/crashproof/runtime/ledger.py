import json
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from crashproof.clock import Clock
from crashproof.models import Json

type EffectType = Literal["create_label", "issue_refund"]
type EffectStatus = Literal["INTENDED", "EXECUTING", "CONFIRMED", "FAILED", "UNKNOWN"]

_LEGAL: dict[EffectStatus, frozenset[EffectStatus]] = {
    "INTENDED": frozenset({"EXECUTING"}),
    "EXECUTING": frozenset({"CONFIRMED", "FAILED", "UNKNOWN"}),
    "UNKNOWN": frozenset({"CONFIRMED", "FAILED"}),
    "FAILED": frozenset({"EXECUTING"}),
    "CONFIRMED": frozenset(),
}

_DENIABLE: frozenset[EffectStatus] = frozenset({"INTENDED", "FAILED"})

_EFFECT_COLUMNS = (
    "id, workflow_id, effect_type, idempotency_key, status, request, response,"
    " policy_decision_id, attempts, created_vt, updated_vt"
)
_WORKFLOW_COLUMNS = "workflow_id, state, outcome, scan_received, updated_vt"


class LedgerError(Exception):
    """Raised on a duplicate key, a missing row or an illegal status change."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class EffectRow(_Frozen):
    id: int
    workflow_id: str
    effect_type: EffectType
    idempotency_key: str
    status: EffectStatus
    request: dict[str, Json]
    response: dict[str, Json] | None
    policy_decision_id: int | None
    attempts: int
    created_vt: int
    updated_vt: int


class PolicyDecisionRow(_Frozen):
    id: int
    workflow_id: str
    effect_key: str
    rule_id: str
    allowed: bool
    reason: str
    vt: int


class WorkflowRow(_Frozen):
    workflow_id: str
    state: str
    outcome: str | None
    scan_received: bool
    updated_vt: int


def _dumps(value: dict[str, Json]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Ledger:
    """Effects ledger and workflow table over a harness-owned sqlite connection.

    Holds no state of its own, so a Ledger rebuilt on the same connection after a
    simulated crash behaves identically.
    """

    def __init__(self, conn: sqlite3.Connection, clock: Clock) -> None:
        self._conn = conn
        self._clock = clock
        conn.execute(
            "CREATE TABLE IF NOT EXISTS effects ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL,"
            " effect_type TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,"
            " status TEXT NOT NULL, request TEXT NOT NULL, response TEXT,"
            " policy_decision_id INTEGER, attempts INTEGER NOT NULL,"
            " created_vt INTEGER NOT NULL, updated_vt INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workflows ("
            "workflow_id TEXT PRIMARY KEY, state TEXT NOT NULL, outcome TEXT,"
            " scan_received INTEGER NOT NULL, updated_vt INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS policy_decisions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL,"
            " effect_key TEXT NOT NULL, rule_id TEXT NOT NULL,"
            " allowed INTEGER NOT NULL, reason TEXT NOT NULL, vt INTEGER NOT NULL)"
        )

    def insert_intended(
        self,
        workflow_id: str,
        effect_type: EffectType,
        key: str,
        request: dict[str, Json],
    ) -> EffectRow:
        now = self._clock.now
        try:
            self._conn.execute(
                "INSERT INTO effects (workflow_id, effect_type, idempotency_key,"
                " status, request, response, policy_decision_id, attempts,"
                " created_vt, updated_vt)"
                " VALUES (?, ?, ?, 'INTENDED', ?, NULL, NULL, 0, ?, ?)",
                (workflow_id, effect_type, key, _dumps(request), now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise LedgerError(f"duplicate idempotency key {key!r}") from exc
        return self._require(key)

    def get_effect(self, key: str) -> EffectRow | None:
        cursor = self._conn.execute(
            f"SELECT {_EFFECT_COLUMNS} FROM effects WHERE idempotency_key = ?", (key,)
        )
        found = cursor.fetchone()
        return None if found is None else _effect_row(found)

    def mark_executing(self, key: str, decision_id: int | None = None) -> EffectRow:
        return self._move(key, "EXECUTING", None, decision_id)

    def mark_unknown(self, key: str) -> EffectRow:
        return self._move(key, "UNKNOWN", None)

    def mark_confirmed(self, key: str, response: dict[str, Json]) -> EffectRow:
        return self._move(key, "CONFIRMED", response)

    def mark_failed(self, key: str, response: dict[str, Json]) -> EffectRow:
        return self._move(key, "FAILED", response)

    def mark_denied(
        self, key: str, decision_id: int, response: dict[str, Json]
    ) -> EffectRow:
        row = self._require(key)
        if row.status not in _DENIABLE:
            raise LedgerError(f"illegal effect transition {row.status} -> FAILED")
        return self._move(key, "FAILED", response, decision_id, check=False)

    def insert_policy_decision(
        self,
        workflow_id: str,
        effect_key: str,
        rule_id: str,
        allowed: bool,
        reason: str,
    ) -> PolicyDecisionRow:
        cursor = self._conn.execute(
            "INSERT INTO policy_decisions (workflow_id, effect_key, rule_id,"
            " allowed, reason, vt) VALUES (?, ?, ?, ?, ?, ?)",
            (workflow_id, effect_key, rule_id, int(allowed), reason, self._clock.now),
        )
        if cursor.lastrowid is None:
            raise LedgerError("policy decision insert returned no row id")
        return PolicyDecisionRow(
            id=cursor.lastrowid,
            workflow_id=workflow_id,
            effect_key=effect_key,
            rule_id=rule_id,
            allowed=allowed,
            reason=reason,
            vt=self._clock.now,
        )

    def decisions_for(self, workflow_id: str) -> list[PolicyDecisionRow]:
        cursor = self._conn.execute(
            "SELECT id, workflow_id, effect_key, rule_id, allowed, reason, vt"
            " FROM policy_decisions WHERE workflow_id = ? ORDER BY id",
            (workflow_id,),
        )
        return [
            PolicyDecisionRow(
                id=found[0],
                workflow_id=found[1],
                effect_key=found[2],
                rule_id=found[3],
                allowed=bool(found[4]),
                reason=found[5],
                vt=found[6],
            )
            for found in cursor.fetchall()
        ]

    def recover(self) -> list[str]:
        cursor = self._conn.execute(
            "SELECT idempotency_key FROM effects WHERE status = 'EXECUTING' ORDER BY id"
        )
        keys: list[str] = [found[0] for found in cursor.fetchall()]
        for key in keys:
            self.mark_unknown(key)
        return keys

    def effects_for(self, workflow_id: str) -> list[EffectRow]:
        cursor = self._conn.execute(
            f"SELECT {_EFFECT_COLUMNS} FROM effects WHERE workflow_id = ? ORDER BY id",
            (workflow_id,),
        )
        return [_effect_row(found) for found in cursor.fetchall()]

    def all_effects(self) -> list[EffectRow]:
        cursor = self._conn.execute(
            f"SELECT {_EFFECT_COLUMNS} FROM effects ORDER BY id"
        )
        return [_effect_row(found) for found in cursor.fetchall()]

    def get_workflow(self, workflow_id: str) -> WorkflowRow | None:
        cursor = self._conn.execute(
            f"SELECT {_WORKFLOW_COLUMNS} FROM workflows WHERE workflow_id = ?",
            (workflow_id,),
        )
        found = cursor.fetchone()
        if found is None:
            return None
        return WorkflowRow(
            workflow_id=found[0],
            state=found[1],
            outcome=found[2],
            scan_received=bool(found[3]),
            updated_vt=found[4],
        )

    def upsert_workflow(
        self, workflow_id: str, state: str, outcome: str | None
    ) -> None:
        self._conn.execute(
            "INSERT INTO workflows (workflow_id, state, outcome, scan_received,"
            " updated_vt) VALUES (?, ?, ?, 0, ?)"
            " ON CONFLICT(workflow_id) DO UPDATE SET state = excluded.state,"
            " outcome = excluded.outcome, updated_vt = excluded.updated_vt",
            (workflow_id, state, outcome, self._clock.now),
        )

    def set_scan_received(self, workflow_id: str) -> None:
        self._update_workflow(workflow_id, "scan_received = 1", ())

    def set_outcome(self, workflow_id: str, outcome: str) -> None:
        self._update_workflow(workflow_id, "outcome = ?", (outcome,))

    def _update_workflow(
        self, workflow_id: str, assignment: str, params: tuple[str, ...]
    ) -> None:
        cursor = self._conn.execute(
            f"UPDATE workflows SET {assignment}, updated_vt = ? WHERE workflow_id = ?",
            (*params, self._clock.now, workflow_id),
        )
        if cursor.rowcount == 0:
            raise LedgerError(f"no workflow row for {workflow_id!r}")

    def _require(self, key: str) -> EffectRow:
        row = self.get_effect(key)
        if row is None:
            raise LedgerError(f"no effect for key {key!r}")
        return row

    def _move(
        self,
        key: str,
        target: EffectStatus,
        response: dict[str, Json] | None,
        decision_id: int | None = None,
        *,
        check: bool = True,
    ) -> EffectRow:
        row = self._require(key)
        if check and target not in _LEGAL[row.status]:
            raise LedgerError(f"illegal effect transition {row.status} -> {target}")
        stored = row.response if response is None else response
        self._conn.execute(
            "UPDATE effects SET status = ?, response = ?, attempts = ?,"
            " policy_decision_id = ?, updated_vt = ? WHERE idempotency_key = ?",
            (
                target,
                None if stored is None else _dumps(stored),
                row.attempts + (1 if target == "EXECUTING" else 0),
                row.policy_decision_id if decision_id is None else decision_id,
                self._clock.now,
                key,
            ),
        )
        return self._require(key)


def _effect_row(found: tuple[Any, ...]) -> EffectRow:
    request, response = found[5], found[6]
    return EffectRow(
        id=found[0],
        workflow_id=found[1],
        effect_type=found[2],
        idempotency_key=found[3],
        status=found[4],
        request=json.loads(request),
        response=None if response is None else json.loads(response),
        policy_decision_id=found[7],
        attempts=found[8],
        created_vt=found[9],
        updated_vt=found[10],
    )
