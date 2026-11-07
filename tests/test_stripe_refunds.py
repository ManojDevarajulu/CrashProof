"""Unit tests for the Stripe adapter against a fake transport.

FakeStripe is this project's model of Stripe's documented behaviour. It is not a
recording; R4 replaces it with recorded real responses.
"""

import io
import json
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from crashproof.adapters import stripe_refunds
from crashproof.adapters.stripe_refunds import (
    HttpRequest,
    HttpResponse,
    LiveKeyRejected,
    StripeApiError,
    StripeRefunds,
    urllib_transport,
)
from crashproof.clock import Clock, Scheduler
from crashproof.ids import IdGen
from crashproof.runtime.executor import Executor, idempotency_key
from crashproof.runtime.ledger import Ledger
from crashproof.runtime.reconcile import make_reconciler
from crashproof.sim.trace import TraceRecorder
from crashproof.world.carriers import CarrierA, IdempotencyConflict
from crashproof.world.events import EventBus
from crashproof.world.faults import Timeout
from crashproof.world.refunds import InvalidRefundAmount, RefundProvider

KEY = "sk_test_abc123"
PI = "pi_test_1"
WF = "wf-1"


def refund_object(
    refund_id: str = "re_1",
    *,
    amount: int = 500,
    reference: str | None = WF,
    order_id: str | None = "o1",
    key: str | None = "idem-1",
    created: int = 1000,
) -> dict[str, Any]:
    metadata: dict[str, str] = {}
    if reference is not None:
        metadata["reference"] = reference
    if order_id is not None:
        metadata["order_id"] = order_id
    if key is not None:
        metadata["idempotency_key"] = key
    return {
        "id": refund_id,
        "object": "refund",
        "amount": amount,
        "created": created,
        "payment_intent": PI,
        "metadata": metadata,
        "status": "succeeded",
    }


def ok(body: dict[str, Any]) -> HttpResponse:
    return HttpResponse(200, json.dumps(body).encode())


def error(status: int, **fields: str) -> HttpResponse:
    return HttpResponse(status, json.dumps({"error": fields}).encode())


class Script:
    """Replays queued responses and records every request."""

    def __init__(self, *responses: HttpResponse | Exception) -> None:
        self.queue = list(responses)
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def adapter(script: Callable[[HttpRequest], HttpResponse]) -> StripeRefunds:
    return StripeRefunds(KEY, PI, transport=script)


def form(request: HttpRequest) -> dict[str, list[str]]:
    assert request.body is not None
    return parse_qs(request.body.decode())


# --- key safety ---------------------------------------------------------


@pytest.mark.parametrize("bad", ["sk_live_abc", "rk_test_abc", "pk_test_abc", ""])
def test_only_test_secret_keys_are_accepted(bad: str) -> None:
    with pytest.raises(LiveKeyRejected) as info:
        StripeRefunds(bad, PI, transport=Script())
    assert bad == "" or bad not in str(info.value)


def test_the_key_never_appears_in_repr() -> None:
    assert KEY not in repr(adapter(Script()))


# --- issue_refund request -------------------------------------------------


def test_issue_refund_builds_the_documented_request() -> None:
    script = Script(ok(refund_object()))
    adapter(script).issue_refund("o1", 500, WF, idempotency_key="idem-1")
    (request,) = script.requests
    assert request.method == "POST"
    assert request.url == "https://api.stripe.com/v1/refunds"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    assert request.headers["Idempotency-Key"] == "idem-1"
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert form(request) == {
        "payment_intent": [PI],
        "amount": ["500"],
        "metadata[reference]": [WF],
        "metadata[order_id]": ["o1"],
        "metadata[idempotency_key]": ["idem-1"],
    }


def test_an_unkeyed_call_sends_no_idempotency_header() -> None:
    script = Script(ok(refund_object(key=None)))
    adapter(script).issue_refund("o1", 500, WF)
    (request,) = script.requests
    assert "Idempotency-Key" not in request.headers
    assert "metadata[idempotency_key]" not in form(request)


def test_the_response_becomes_a_refund_record() -> None:
    record = adapter(Script(ok(refund_object(created=1234)))).issue_refund(
        "o1", 500, WF, idempotency_key="idem-1"
    )
    assert record.refund_id == "re_1"
    assert record.order_id == "o1"
    assert record.amount == 500
    assert record.reference == WF
    assert record.idempotency_key == "idem-1"
    assert record.created_vt == 1234


@pytest.mark.parametrize("amount", [0, -5, 1.5, True, "10"])
def test_a_bad_amount_is_rejected_before_any_request(amount: Any) -> None:
    script = Script()
    with pytest.raises(InvalidRefundAmount):
        adapter(script).issue_refund("o1", amount, WF)
    assert script.requests == []


def test_a_response_without_our_metadata_is_an_api_error() -> None:
    script = Script(ok(refund_object(reference=None)))
    with pytest.raises(StripeApiError):
        adapter(script).issue_refund("o1", 500, WF)


# --- error mapping ----------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_server_side_and_rate_limit_responses_mean_unknown_outcome(
    status: int,
) -> None:
    with pytest.raises(Timeout):
        adapter(Script(error(status, type="api_error"))).issue_refund("o1", 500, WF)


def test_a_transport_timeout_passes_through() -> None:
    with pytest.raises(Timeout):
        adapter(Script(Timeout("lost"))).issue_refund("o1", 500, WF)


def test_idempotency_error_maps_to_idempotency_conflict() -> None:
    response = error(400, type="idempotency_error", message="different parameters")
    with pytest.raises(IdempotencyConflict):
        adapter(Script(response)).issue_refund("o1", 500, WF, idempotency_key="k")


def test_an_amount_error_maps_to_invalid_refund_amount() -> None:
    response = error(400, type="invalid_request_error", param="amount")
    with pytest.raises(InvalidRefundAmount):
        adapter(Script(response)).issue_refund("o1", 500, WF)


@pytest.mark.parametrize(
    "response",
    [
        error(401, type="invalid_request_error", message="Invalid API Key"),
        error(404, type="invalid_request_error", code="resource_missing"),
        HttpResponse(400, b"not json"),
        HttpResponse(200, b"[]"),
    ],
)
def test_anything_else_is_a_loud_api_error(response: HttpResponse) -> None:
    with pytest.raises(StripeApiError) as info:
        adapter(Script(response)).issue_refund("o1", 500, WF)
    assert KEY not in str(info.value)


# --- lookups ------------------------------------------------------------------


def test_get_by_key_lists_the_payment_intent_and_filters_on_metadata() -> None:
    page = {
        "data": [
            refund_object("re_2", key="idem-2"),
            refund_object("re_1", key="idem-1"),
        ],
        "has_more": False,
    }
    script = Script(ok(page))
    found = adapter(script).get_by_key("idem-1")
    assert found is not None
    assert found.refund_id == "re_1"
    (request,) = script.requests
    assert request.method == "GET"
    assert request.body is None
    parsed = urlparse(request.url)
    assert parsed.path == "/v1/refunds"
    assert parse_qs(parsed.query) == {"payment_intent": [PI], "limit": ["100"]}


def test_get_by_key_returns_none_when_nothing_matches() -> None:
    page = {"data": [refund_object(key="other")], "has_more": False}
    assert adapter(Script(ok(page))).get_by_key("idem-1") is None


def test_get_by_reference_ignores_foreign_refunds_and_orders_by_creation() -> None:
    page = {
        "data": [
            refund_object("re_3", created=30),
            refund_object("re_x", reference="other-wf", created=25),
            refund_object("re_foreign", reference=None, created=20),
            refund_object("re_1", created=10),
        ],
        "has_more": False,
    }
    found = adapter(Script(ok(page))).get_by_reference(WF)
    assert [r.refund_id for r in found] == ["re_1", "re_3"]


def test_lookups_follow_pagination() -> None:
    first = {"data": [refund_object("re_2", key="a")], "has_more": True}
    second = {"data": [refund_object("re_1", key="idem-1")], "has_more": False}
    script = Script(ok(first), ok(second))
    found = adapter(script).get_by_key("idem-1")
    assert found is not None
    query = parse_qs(urlparse(script.requests[1].url).query)
    assert query["starting_after"] == ["re_2"]


def test_a_page_that_claims_more_but_is_empty_is_an_error() -> None:
    page = {"data": [], "has_more": True}
    with pytest.raises(StripeApiError):
        adapter(Script(ok(page))).get_by_key("k")


def test_a_failed_lookup_is_never_reported_as_not_found() -> None:
    with pytest.raises(Timeout):
        adapter(Script(error(503, type="api_error"))).get_by_key("k")


# --- default transport (urllib patched, no network) ------------------------


class FakeHttpResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def sample_request() -> HttpRequest:
    return HttpRequest("POST", "https://api.stripe.com/v1/refunds", {"A": "b"}, b"x=1")


def test_urllib_transport_returns_status_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[urllib.request.Request] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> FakeHttpResponse:
        seen.append(req)
        return FakeHttpResponse(200, b"{}")

    monkeypatch.setattr(stripe_refunds.urllib.request, "urlopen", fake_urlopen)
    assert urllib_transport(sample_request()) == HttpResponse(200, b"{}")
    assert seen[0].get_method() == "POST"
    assert seen[0].full_url == "https://api.stripe.com/v1/refunds"
    assert seen[0].data == b"x=1"


def test_urllib_transport_turns_an_http_error_into_a_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> None:
        raise urllib.error.HTTPError(
            req.full_url, 402, "x", None, io.BytesIO(b'{"error":{}}')
        )

    monkeypatch.setattr(stripe_refunds.urllib.request, "urlopen", fake_urlopen)
    assert urllib_transport(sample_request()) == HttpResponse(402, b'{"error":{}}')


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("down"), TimeoutError(), ConnectionResetError()],
)
def test_urllib_transport_maps_network_failures_to_timeout(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> None:
        raise failure

    monkeypatch.setattr(stripe_refunds.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(Timeout):
        urllib_transport(sample_request())


# --- protocol and the runtime ---------------------------------------------------


def test_the_adapter_satisfies_the_protocol() -> None:
    assert isinstance(adapter(Script()), RefundProvider)


class FakeStripe:
    """Our model of documented Stripe behaviour: keyed replay and a refund list."""

    def __init__(self, lose_first_response: bool = False) -> None:
        self.refunds: list[dict[str, Any]] = []
        self._by_key: dict[str, dict[str, Any]] = {}
        self._lose = lose_first_response
        self.posts = 0

    def __call__(self, req: HttpRequest) -> HttpResponse:
        if req.method == "GET":
            return ok({"data": list(reversed(self.refunds)), "has_more": False})
        self.posts += 1
        fields = form(req)
        key = req.headers.get("Idempotency-Key")
        existing = self._by_key.get(key) if key else None
        if existing is None:
            existing = refund_object(
                f"re_{len(self.refunds) + 1}",
                amount=int(fields["amount"][0]),
                reference=fields["metadata[reference]"][0],
                order_id=fields["metadata[order_id]"][0],
                key=key,
                created=len(self.refunds) + 1,
            )
            self.refunds.append(existing)
            if key:
                self._by_key[key] = existing
        if self._lose and self.posts == 1:
            raise Timeout("response lost after the refund was applied")
        return ok(existing)


def test_a_lost_response_is_reconciled_without_a_second_refund() -> None:
    stripe = FakeStripe(lose_first_response=True)
    provider = adapter(stripe)
    clock = Clock()
    trace = TraceRecorder()
    ids = IdGen(0)
    bus = EventBus(Scheduler(clock), clock, ids, trace)
    carrier = CarrierA(clock, ids, trace, bus)
    ledger = Ledger(sqlite3.connect(":memory:"), clock)
    reconciler = make_reconciler(ledger, carrier, provider, trace, clock)
    executor = Executor(ledger, carrier, provider, None, trace, clock, 5, reconciler)
    first = executor.execute(WF, "issue_refund", "o1", 500)
    second = executor.execute(WF, "issue_refund", "o1", 500)
    assert first.kind == "timed_out"
    assert second.kind == "confirmed"
    assert len(stripe.refunds) == 1
    assert stripe.posts == 1
    key = idempotency_key(WF, "issue_refund")
    assert stripe.refunds[0]["metadata"]["idempotency_key"] == key
