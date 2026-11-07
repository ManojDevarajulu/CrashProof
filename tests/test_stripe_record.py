"""Tests for the Stripe contract recorder, run against a fake Stripe account.

Nothing here touches the network. The real recording is made once by the human
with `make record-stripe`.
"""

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from crashproof.adapters.stripe_refunds import (
    HttpRequest,
    HttpResponse,
    LiveKeyRejected,
    RecordingError,
    RecordingTransport,
    StripeApiError,
    record_contract,
)
from crashproof.cli import _record_stripe
from crashproof.world.faults import Timeout

KEY = "sk_test_abc123secretvalue"


def ok(body: dict[str, Any]) -> HttpResponse:
    return HttpResponse(200, json.dumps(body).encode())


def error(status: int, **fields: str) -> HttpResponse:
    return HttpResponse(status, json.dumps({"error": fields}).encode())


def fields_of(request: HttpRequest) -> dict[str, list[str]]:
    assert request.body is not None
    return parse_qs(request.body.decode())


class FakeAccount:
    """A Stripe account with one succeeded PaymentIntent. Our model, not a recording."""

    def __init__(self, *, succeed: bool = True, leak_key: bool = False) -> None:
        self.refunds: list[dict[str, Any]] = []
        self.by_key: dict[str, dict[str, Any]] = {}
        self.charge = 0
        self.succeed = succeed
        self.leak_key = leak_key
        self.seen_auth: list[str] = []

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.seen_auth.append(request.headers.get("Authorization", ""))
        path = urlparse(request.url).path
        if path == "/v1/payment_intents":
            return self._payment_intent(request)
        if request.method == "GET":
            return self._list(request)
        return self._refund(request)

    def _payment_intent(self, request: HttpRequest) -> HttpResponse:
        self.charge = int(fields_of(request)["amount"][0])
        body: dict[str, Any] = {
            "id": "pi_fake",
            "status": "succeeded" if self.succeed else "requires_action",
            "amount": self.charge,
            "client_secret": "pi_fake_secret_hush",
        }
        if self.leak_key:
            body["note"] = KEY
        return ok(body)

    def _list(self, request: HttpRequest) -> HttpResponse:
        query = parse_qs(urlparse(request.url).query)
        if query["payment_intent"][0] != "pi_fake":
            return error(404, type="invalid_request_error", code="resource_missing")
        return ok({"data": list(reversed(self.refunds)), "has_more": False})

    def _refund(self, request: HttpRequest) -> HttpResponse:
        fields = fields_of(request)
        if fields["payment_intent"][0] != "pi_fake":
            return error(404, type="invalid_request_error", code="resource_missing")
        amount = int(fields["amount"][0])
        key = request.headers.get("Idempotency-Key")
        if key in self.by_key:
            stored = self.by_key[key]
            if stored["amount"] != amount:
                return error(400, type="idempotency_error")
            return ok(stored)
        if amount > self.charge - sum(r["amount"] for r in self.refunds):
            return error(400, type="invalid_request_error", param="amount")
        refund: dict[str, Any] = {
            "id": f"re_{len(self.refunds) + 1}",
            "amount": amount,
            "created": len(self.refunds) + 1,
            "metadata": {
                "reference": fields["metadata[reference]"][0],
                "order_id": fields["metadata[order_id]"][0],
                **(
                    {"idempotency_key": fields["metadata[idempotency_key]"][0]}
                    if key
                    else {}
                ),
            },
        }
        self.refunds.append(refund)
        if key:
            self.by_key[key] = refund
        return ok(refund)


def steps_by_name(recording: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {step["name"]: step for step in recording["steps"]}


def test_the_recording_covers_each_scenario_with_the_outcome_the_adapter_reports() -> (
    None
):
    recording = record_contract(KEY, FakeAccount())
    outcomes = {name: s["outcome"] for name, s in steps_by_name(recording).items()}
    assert outcomes == {
        "create_payment_intent": "ok",
        "refund": "ok",
        "refund_replay": "ok",
        "refund_key_reused_with_other_amount": "IdempotencyConflict",
        "refund_unkeyed_first": "ok",
        "refund_unkeyed_second": "ok",
        "lookup_by_key": "ok",
        "lookup_missing_key": "ok",
        "lookup_by_reference": "ok",
        "refund_more_than_charged": "InvalidRefundAmount",
        "refund_unknown_payment_intent": "StripeApiError",
    }


def test_each_step_keeps_its_requests_and_responses_in_order() -> None:
    recording = record_contract(KEY, FakeAccount())
    steps = steps_by_name(recording)
    first = steps["refund"]["exchanges"][0]
    assert first["request"]["method"] == "POST"
    assert first["request"]["url"].endswith("/v1/refunds")
    assert first["response"]["status"] == 200
    replay = steps["refund_replay"]["exchanges"][0]
    assert replay["request"] == first["request"]
    assert (
        json.loads(replay["response"]["body"])["id"]
        == (json.loads(first["response"]["body"])["id"])
    )
    assert steps["lookup_by_key"]["exchanges"][0]["request"]["method"] == "GET"


def test_the_recorded_inputs_let_a_replay_rebuild_the_same_requests() -> None:
    recording = record_contract(KEY, FakeAccount())
    inputs = recording["inputs"]
    assert inputs["payment_intent"] == "pi_fake"
    assert inputs["reference"] == "wf-pi_fake"
    steps = steps_by_name(recording)
    sent = steps["refund"]["exchanges"][0]["request"]
    assert inputs["key"] in sent["body"]
    assert sent["headers"]["Idempotency-Key"] == inputs["key"]


def test_the_api_key_and_client_secret_never_reach_the_recording() -> None:
    account = FakeAccount()
    recording = record_contract(KEY, account)
    text = json.dumps(recording)
    assert KEY not in text
    assert "Authorization" not in text
    assert "_secret_" not in text
    assert "hush" not in text
    assert account.seen_auth
    assert all(value == f"Bearer {KEY}" for value in account.seen_auth)


def test_a_recording_that_would_contain_the_key_is_refused() -> None:
    with pytest.raises(RecordingError):
        record_contract(KEY, FakeAccount(leak_key=True))


def test_a_live_key_is_refused_before_any_request() -> None:
    account = FakeAccount()
    with pytest.raises(LiveKeyRejected):
        record_contract("sk_live_abc", account)
    assert account.seen_auth == []


def test_a_payment_intent_that_did_not_succeed_stops_the_recording() -> None:
    with pytest.raises(StripeApiError):
        record_contract(KEY, FakeAccount(succeed=False))


def test_recording_transport_records_a_timeout_and_reraises() -> None:
    def lost(request: HttpRequest) -> HttpResponse:
        raise Timeout("lost")

    recorder = RecordingTransport(lost)
    with pytest.raises(Timeout):
        recorder(HttpRequest("GET", "https://x/y", {"Authorization": "Bearer z"}, None))
    (exchange,) = recorder.take()
    assert exchange["response"] is None
    assert exchange["error"] == "Timeout"
    assert "Authorization" not in json.dumps(exchange)
    assert recorder.take() == []


# --- the CLI command ---------------------------------------------------------


def test_cli_writes_the_recording(tmp_path: Path) -> None:
    out = tmp_path / "contracts" / "stripe_refunds.json"
    status = _record_stripe(out, {"STRIPE_API_KEY": KEY}, FakeAccount())
    assert status == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["inputs"]["payment_intent"] == "pi_fake"
    assert KEY not in out.read_text(encoding="utf-8")
    assert b"\r" not in out.read_bytes()


def test_cli_needs_a_key(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    assert _record_stripe(out, {}, FakeAccount()) == 2
    assert not out.exists()


def test_cli_rejects_a_live_key_without_echoing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "r.json"
    live = "sk_live_notarealkey"
    assert _record_stripe(out, {"STRIPE_API_KEY": live}, FakeAccount()) == 2
    assert live not in capsys.readouterr().out
    assert not out.exists()


def test_cli_never_overwrites_an_existing_recording(tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    out.write_text("keep me\n", encoding="utf-8")
    account = FakeAccount()
    assert _record_stripe(out, {"STRIPE_API_KEY": KEY}, account) == 2
    assert out.read_text(encoding="utf-8") == "keep me\n"
    assert account.seen_auth == []
