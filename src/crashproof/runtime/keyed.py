from crashproof.runtime.executor import idempotency_key
from crashproof.runtime.naive import NaiveRuntime
from crashproof.world.carriers import LabelResult
from crashproof.world.refunds import RefundRecord


class KeyedRuntime(NaiveRuntime):
    """The naive runtime plus the deterministic idempotency key, and nothing else.

    The key goes to every provider that supports keys: the label key only when
    the carrier supports keys, the refund key always. No ledger, no
    reconciliation, no policy, no state machine; retries still come only from
    the agent, and a restart still loses all memory.
    """

    def _call_create_label(self, order_id: str) -> LabelResult:
        key = idempotency_key(self._reference, "create_label")
        return self._carrier.create_label(
            order_id,
            self._reference,
            idempotency_key=key if self._carrier.supports_keys else None,
        )

    def _call_issue_refund(self, order_id: str, amount: int) -> RefundRecord:
        key = idempotency_key(self._reference, "issue_refund")
        return self._refunds.issue_refund(
            order_id, amount, self._reference, idempotency_key=key
        )
