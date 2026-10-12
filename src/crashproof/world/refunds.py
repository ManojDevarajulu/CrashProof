from pydantic import BaseModel, ConfigDict

from crashproof.clock import Clock
from crashproof.ids import IdGen
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import IdempotencyConflict
from crashproof.world.events import EventBus
from crashproof.world.faults import FaultInjector


class InvalidRefundAmount(ValueError):
    """Raised when a refund amount is not a positive int."""


class RefundRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    refund_id: str
    order_id: str
    amount: int
    reference: str
    idempotency_key: str | None
    created_vt: int


class RefundService:
    """Simulated refund service.

    With a key it applies at most once per key and replays the original record
    on a repeat; the same key with a different request raises
    IdempotencyConflict. Without a key every call applies a new refund. It does
    not check the order or the amount against paid_amount, on purpose.
    """

    def __init__(
        self,
        clock: Clock,
        ids: IdGen,
        trace: TraceRecorder,
        bus: EventBus,
        faults: FaultInjector | None = None,
    ) -> None:
        self._clock = clock
        self._ids = ids
        self._trace = trace
        self._bus = bus
        self._faults = faults
        self._refunds: list[RefundRecord] = []
        self._by_key: dict[str, tuple[tuple[str, int, str], RefundRecord]] = {}

    @property
    def refunds(self) -> tuple[RefundRecord, ...]:
        return tuple(self._refunds)

    def issue_refund(
        self,
        order_id: str,
        amount: int,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> RefundRecord:
        if self._faults is None:
            return self._issue_refund(
                order_id, amount, reference, idempotency_key=idempotency_key
            )
        attempt = self._faults.begin_call("issue_refund", reference)
        record = self._issue_refund(
            order_id, amount, reference, idempotency_key=idempotency_key
        )
        self._faults.end_call("issue_refund", reference, attempt)
        return record

    def _issue_refund(
        self,
        order_id: str,
        amount: int,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> RefundRecord:
        if type(amount) is not int or amount <= 0:
            raise InvalidRefundAmount(
                f"refund amount must be a positive int: {amount!r}"
            )
        request = (order_id, amount, reference)
        if idempotency_key is not None and idempotency_key in self._by_key:
            return self._replay(idempotency_key, request)
        record = RefundRecord(
            refund_id=self._ids.new("rfd"),
            order_id=order_id,
            amount=amount,
            reference=reference,
            idempotency_key=idempotency_key,
            created_vt=self._clock.now,
        )
        self._refunds.append(record)
        if idempotency_key is not None:
            self._by_key[idempotency_key] = (request, record)
        self._trace.record(
            self._clock.now,
            reference,
            "world",
            "refund.issued",
            {
                "refund_id": record.refund_id,
                "order_id": order_id,
                "amount": amount,
                "reference": reference,
                "key": idempotency_key,
            },
        )
        self._bus.publish(
            "refund.confirmed",
            reference,
            {"refund_id": record.refund_id, "order_id": order_id, "amount": amount},
            delay=0,
        )
        return record

    def _replay(self, key: str, request: tuple[str, int, str]) -> RefundRecord:
        stored_request, record = self._by_key[key]
        reference = request[2]
        if stored_request != request:
            self._trace.record(
                self._clock.now,
                reference,
                "world",
                "refund.key_conflict",
                {"key": key, "refund_id": record.refund_id},
            )
            raise IdempotencyConflict(f"key {key!r} reused with a different request")
        self._trace.record(
            self._clock.now,
            reference,
            "world",
            "refund.replayed",
            {"key": key, "refund_id": record.refund_id},
        )
        return record

    def get_by_key(self, key: str) -> RefundRecord | None:
        """The refund applied under a key, or None. Not found is a valid answer."""
        entry = self._by_key.get(key)
        return None if entry is None else entry[1]

    def get_by_reference(self, reference: str) -> tuple[RefundRecord, ...]:
        """Refunds for a reference, in creation order. Empty means none exist."""
        return tuple(r for r in self._refunds if r.reference == reference)

    def total_refunded(self, order_id: str) -> int:
        return sum(r.amount for r in self._refunds if r.order_id == order_id)
