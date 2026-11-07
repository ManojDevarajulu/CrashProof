import sqlite3

from crashproof.clock import Clock, Scheduler
from crashproof.ids import IdGen
from crashproof.runtime.executor import Executor, idempotency_key
from crashproof.runtime.ledger import Ledger
from crashproof.runtime.reconcile import make_reconciler
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import CarrierA
from crashproof.world.events import EventBus
from crashproof.world.faults import Timeout
from crashproof.world.refunds import RefundProvider, RefundRecord, RefundService

WF = "wf-1"


class DictProvider:
    """A refund provider that is not RefundService: it only meets the protocol."""

    def __init__(self, lose_first_response: bool = False) -> None:
        self.calls = 0
        self._lose_first_response = lose_first_response
        self._by_key: dict[str, RefundRecord] = {}

    def issue_refund(
        self,
        order_id: str,
        amount: int,
        reference: str,
        *,
        idempotency_key: str | None = None,
    ) -> RefundRecord:
        self.calls += 1
        assert idempotency_key is not None
        record = self._by_key.get(idempotency_key)
        if record is None:
            record = RefundRecord(
                refund_id=f"ext-{len(self._by_key) + 1}",
                order_id=order_id,
                amount=amount,
                reference=reference,
                idempotency_key=idempotency_key,
                created_vt=0,
            )
            self._by_key[idempotency_key] = record
        if self._lose_first_response and self.calls == 1:
            raise Timeout("response lost after the refund was applied")
        return record

    def get_by_key(self, key: str) -> RefundRecord | None:
        return self._by_key.get(key)

    def get_by_reference(self, reference: str) -> tuple[RefundRecord, ...]:
        return tuple(r for r in self._by_key.values() if r.reference == reference)


class Rig:
    def __init__(self, provider: RefundProvider) -> None:
        self.provider = provider
        self.clock = Clock()
        self.trace = TraceRecorder()
        ids = IdGen(0)
        bus = EventBus(Scheduler(self.clock), self.clock, ids, self.trace)
        carrier = CarrierA(self.clock, ids, self.trace, bus)
        self.ledger = Ledger(sqlite3.connect(":memory:"), self.clock)
        reconciler = make_reconciler(
            self.ledger, carrier, provider, self.trace, self.clock
        )
        self.executor = Executor(
            self.ledger,
            carrier,
            provider,
            None,
            self.trace,
            self.clock,
            5,
            reconciler,
        )


def test_simulated_service_satisfies_the_protocol() -> None:
    clock = Clock()
    trace = TraceRecorder()
    ids = IdGen(0)
    bus = EventBus(Scheduler(clock), clock, ids, trace)
    assert isinstance(RefundService(clock, ids, trace, bus), RefundProvider)


def test_external_provider_satisfies_the_protocol() -> None:
    assert isinstance(DictProvider(), RefundProvider)


def test_object_missing_a_method_does_not_satisfy_the_protocol() -> None:
    class NoLookup:
        def issue_refund(self) -> None:
            return None

    assert not isinstance(NoLookup(), RefundProvider)


def test_executor_runs_against_a_provider_that_is_not_refund_service() -> None:
    rig = Rig(DictProvider())
    first = rig.executor.execute(WF, "issue_refund", "o1", 100)
    again = rig.executor.execute(WF, "issue_refund", "o1", 100)
    assert first.kind == "confirmed"
    assert again.kind == "confirmed"
    assert first.data == again.data
    assert isinstance(rig.provider, DictProvider)
    assert rig.provider.calls == 1


def test_lost_response_is_reconciled_through_the_protocol_without_a_second_call() -> (
    None
):
    provider = DictProvider(lose_first_response=True)
    rig = Rig(provider)
    first = rig.executor.execute(WF, "issue_refund", "o1", 100)
    second = rig.executor.execute(WF, "issue_refund", "o1", 100)
    assert first.kind == "timed_out"
    assert second.kind == "confirmed"
    assert provider.calls == 1
    key = idempotency_key(WF, "issue_refund")
    assert provider.get_by_key(key) is not None
    assert len(provider.get_by_reference(WF)) == 1
