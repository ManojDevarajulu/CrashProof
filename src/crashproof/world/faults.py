from typing import Literal

from crashproof.clock import Clock
from crashproof.config import FaultProbabilities
from crashproof.models import Json
from crashproof.rng import Rng
from crashproof.sim.trace import TraceRecorder

type ProviderOp = Literal["create_label", "issue_refund"]
type CrashPoint = Literal["label_applied", "refund_applied"]


class Timeout(Exception):
    """A provider call timed out.

    F1 and F2 raise this same type with the same message, so the caller cannot
    tell whether the effect was applied. It is not the builtin TimeoutError, so
    code that handles the builtin does not catch it by accident.
    """


class SimulatedCrash(BaseException):
    """The runtime process died at a named crash point (F5).

    Subclasses BaseException, not Exception, on purpose: a broad
    `except Exception` in runtime code must never swallow a crash. Only the
    runner catches it, to model a restart.
    """

    def __init__(self, point: CrashPoint) -> None:
        super().__init__(point)
        self.point = point


class FaultInjector:
    """Seeded fault decisions for F1, F2, F3 and F5.

    Owned by the harness like Rng and Clock, and it survives simulated
    crashes: its attempt and occurrence counters must not reset on restart, or
    attempt 1 would be drawn again and crash again.

    Each decision is one draw from a fresh fork keyed by
    (fault, reference, kind, number), so a verdict depends only on that key,
    never on call order, other workflows or other fault types. The same
    "refund attempt 2 of wf-X" gets the same verdict in every runtime.

    F1 is decided in begin_call and wins over F2. F2 is decided in end_call,
    so the effective F2 rate is (1 - p1) * p2. F5 is only reached after a call
    returned normally. Reads are never faulted. Faults that do not fire add
    nothing to the trace.
    """

    def __init__(
        self,
        rng: Rng,
        probs: FaultProbabilities,
        clock: Clock,
        trace: TraceRecorder,
    ) -> None:
        self._base = rng.fork("faults")
        self._probs = probs
        self._clock = clock
        self._trace = trace
        self._calls: dict[tuple[str, str], int] = {}
        self._deliveries: dict[tuple[str, str], int] = {}
        self._crashes: dict[tuple[str, str], int] = {}

    def begin_call(self, op: ProviderOp, reference: str) -> int:
        attempt = self._next(self._calls, (reference, op))
        if self._fires("F1", self._probs.F1, reference, op, attempt):
            self._record("F1", reference, {"op": op, "attempt": attempt})
            raise Timeout(f"{op} timed out")
        return attempt

    def end_call(self, op: ProviderOp, reference: str, attempt: int) -> None:
        if self._fires("F2", self._probs.F2, reference, op, attempt):
            self._record("F2", reference, {"op": op, "attempt": attempt})
            raise Timeout(f"{op} timed out")

    def duplicate_delivery(
        self, workflow_id: str, type: str, event_id: str, deliver_vt: int
    ) -> bool:
        occurrence = self._next(self._deliveries, (workflow_id, type))
        if not self._fires("F3", self._probs.F3, workflow_id, type, occurrence):
            return False
        self._record(
            "F3",
            workflow_id,
            {
                "event_id": event_id,
                "type": type,
                "deliver_vt": deliver_vt,
                "occurrence": occurrence,
            },
        )
        return True

    def crash_point(self, name: CrashPoint, reference: str) -> None:
        occurrence = self._next(self._crashes, (reference, name))
        if self._fires("F5", self._probs.F5, reference, name, occurrence):
            self._record("F5", reference, {"point": name, "occurrence": occurrence})
            raise SimulatedCrash(name)

    def _fires(self, fault: str, p: float, ref: str, kind: str, n: int) -> bool:
        return self._base.fork(fault).fork(f"{ref}|{kind}|{n}").chance(p)

    def _record(self, fault: str, reference: str, data: dict[str, Json]) -> None:
        self._trace.record(
            self._clock.now,
            reference,
            "fault",
            f"fault.{fault}",
            {"ref": reference, **data},
        )

    @staticmethod
    def _next(counters: dict[tuple[str, str], int], key: tuple[str, str]) -> int:
        counters[key] = counters.get(key, 0) + 1
        return counters[key]
