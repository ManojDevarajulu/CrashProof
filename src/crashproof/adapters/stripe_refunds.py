"""Stripe test-mode refunds behind the RefundProvider protocol.

Network I/O is allowed here (AGENTS.md Rule 4 exemption) and runs only under
`make demo-real`, with a `sk_test_` key. Tests inject a fake transport.

How the protocol maps onto Stripe:
- The idempotency key goes in the Idempotency-Key header and, because Stripe
  forgets keys after 24 hours, also in the refund metadata.
- reference (the workflow id), order_id and the key are stored in metadata.
- Lookups list the PaymentIntent's refunds and filter on that metadata.
- A response that does not settle whether the refund was applied (network
  failure, 429, 5xx) raises faults.Timeout, so the executor marks the effect
  UNKNOWN and reconciles instead of retrying blindly.
- created_vt carries Stripe's `created` timestamp (epoch seconds), not virtual time.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from crashproof.world.carriers import IdempotencyConflict
from crashproof.world.faults import Timeout
from crashproof.world.refunds import InvalidRefundAmount, RefundRecord

API_BASE = "https://api.stripe.com/v1"
KEY_PREFIX = "sk_test_"
_PAGE_SIZE = "100"


class LiveKeyRejected(ValueError):
    """The API key is not a test-mode secret key."""


class StripeApiError(Exception):
    """Stripe answered, but not in a way that maps onto the protocol."""


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


type Transport = Callable[[HttpRequest], HttpResponse]


def urllib_transport(request: HttpRequest, timeout: float = 30.0) -> HttpResponse:
    """Send a request with urllib. An HTTP error status is a response, not a failure.

    Any other failure to complete the exchange leaves the outcome unknown.
    """
    prepared = urllib.request.Request(
        request.url,
        data=request.body,
        headers=request.headers,
        method=request.method,
    )
    try:
        with urllib.request.urlopen(prepared, timeout=timeout) as reply:
            return HttpResponse(reply.status, reply.read())
    except urllib.error.HTTPError as exc:
        with exc:
            return HttpResponse(exc.code, exc.read())
    except OSError as exc:
        raise Timeout(
            f"no complete response from Stripe: {type(exc).__name__}"
        ) from exc


class StripeRefunds:
    """RefundProvider for one PaymentIntent."""

    def __init__(
        self,
        api_key: str,
        payment_intent: str,
        transport: Transport = urllib_transport,
    ) -> None:
        if not api_key.startswith(KEY_PREFIX):
            raise LiveKeyRejected(f"only {KEY_PREFIX}... test keys are accepted")
        self._api_key = api_key
        self._payment_intent = payment_intent
        self._transport = transport

    def __repr__(self) -> str:
        return f"StripeRefunds(payment_intent={self._payment_intent!r})"

    def issue_refund(
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
        fields = [
            ("payment_intent", self._payment_intent),
            ("amount", str(amount)),
            ("metadata[reference]", reference),
            ("metadata[order_id]", order_id),
        ]
        headers = self._headers()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        if idempotency_key is not None:
            fields.append(("metadata[idempotency_key]", idempotency_key))
            headers["Idempotency-Key"] = idempotency_key
        body = urllib.parse.urlencode(fields).encode()
        reply = self._send(HttpRequest("POST", f"{API_BASE}/refunds", headers, body))
        record = _ours(_checked(reply))
        if record is None:
            raise StripeApiError("refund response is missing the reference metadata")
        return record

    def get_by_key(self, key: str) -> RefundRecord | None:
        """The refund made under a key, or None. A failed lookup raises, never None."""
        for record in self._list():
            if record.idempotency_key == key:
                return record
        return None

    def get_by_reference(self, reference: str) -> tuple[RefundRecord, ...]:
        return tuple(r for r in self._list() if r.reference == reference)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _send(self, request: HttpRequest) -> HttpResponse:
        return self._transport(request)

    def _list(self) -> list[RefundRecord]:
        records: list[RefundRecord] = []
        after: str | None = None
        while True:
            query = [("payment_intent", self._payment_intent), ("limit", _PAGE_SIZE)]
            if after is not None:
                query.append(("starting_after", after))
            url = f"{API_BASE}/refunds?{urllib.parse.urlencode(query)}"
            page = _checked(self._send(HttpRequest("GET", url, self._headers(), None)))
            data = page.get("data")
            if not isinstance(data, list):
                raise StripeApiError("refund list response has no data array")
            for item in data:
                if isinstance(item, dict):
                    record = _ours(item)
                    if record is not None:
                        records.append(record)
            if page.get("has_more") is not True:
                return sorted(records, key=lambda r: (r.created_vt, r.refund_id))
            last = data[-1] if data else None
            after = last.get("id") if isinstance(last, dict) else None
            if not isinstance(after, str):
                raise StripeApiError("refund list claims more pages but gave no cursor")


def _checked(reply: HttpResponse) -> dict[str, object]:
    """The JSON object of a successful reply; map every other outcome loudly."""
    if reply.status == 429 or reply.status >= 500:
        raise Timeout(f"Stripe answered {reply.status}; the outcome is unknown")
    body = _json_object(reply.body)
    if 200 <= reply.status < 300:
        return body
    raw = body.get("error")
    detail = raw if isinstance(raw, dict) else {}
    if detail.get("type") == "idempotency_error":
        raise IdempotencyConflict(
            "Stripe: idempotency key reused with other parameters"
        )
    if detail.get("param") == "amount":
        raise InvalidRefundAmount("Stripe rejected the refund amount")
    raise StripeApiError(
        f"Stripe rejected the request: {reply.status} "
        f"type={detail.get('type')} code={detail.get('code')}"
    )


def _json_object(raw: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise StripeApiError("Stripe reply is not JSON") from exc
    if not isinstance(parsed, dict):
        raise StripeApiError("Stripe reply is not a JSON object")
    return parsed


def _ours(refund: dict[str, object]) -> RefundRecord | None:
    """A RefundRecord for a refund this project made; None for any other refund."""
    metadata = refund.get("metadata")
    if not isinstance(metadata, dict):
        return None
    reference = metadata.get("reference")
    order_id = metadata.get("order_id")
    if not isinstance(reference, str) or not isinstance(order_id, str):
        return None
    key = metadata.get("idempotency_key")
    refund_id = refund.get("id")
    amount = refund.get("amount")
    created = refund.get("created")
    if not isinstance(refund_id, str) or type(amount) is not int:
        raise StripeApiError("refund object has no usable id or amount")
    if type(created) is not int:
        raise StripeApiError("refund object has no usable created time")
    return RefundRecord(
        refund_id=refund_id,
        order_id=order_id,
        amount=amount,
        reference=reference,
        idempotency_key=key if isinstance(key, str) else None,
        created_vt=created,
    )
