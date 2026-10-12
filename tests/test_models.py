import pytest
from pydantic import ValidationError

from crashproof.models import Line, Order, PolicyDecision, ReturnRequest, TraceEvent


def make_order(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "order_id": "order-000001",
        "customer_id": "cust-000001",
        "lines": [
            {"sku": "sku-a", "qty": 2, "unit_price": 1500},
            {"sku": "sku-b", "qty": 1, "unit_price": 700},
        ],
        "paid_amount": 3700,
        "delivered_at": 86400,
        "carrier": "A",
        "fraud_risk": False,
    }
    base.update(overrides)
    return base


def test_valid_order_round_trip() -> None:
    order = Order.model_validate(make_order())
    assert order.paid_amount == 3700
    assert order.lines[0] == Line(sku="sku-a", qty=2, unit_price=1500)
    assert Order.model_validate(order.model_dump()) == order


def test_order_allows_undelivered() -> None:
    order = Order.model_validate(make_order(delivered_at=None))
    assert order.delivered_at is None


def test_paid_amount_mismatch_rejected() -> None:
    with pytest.raises(ValidationError):
        Order.model_validate(make_order(paid_amount=3699))


@pytest.mark.parametrize("bad", [3700.0, True])
def test_float_or_bool_money_rejected(bad: object) -> None:
    with pytest.raises(ValidationError):
        Order.model_validate(make_order(paid_amount=bad))


def test_float_unit_price_rejected() -> None:
    lines = [{"sku": "sku-a", "qty": 1, "unit_price": 10.0}]
    with pytest.raises(ValidationError):
        Order.model_validate(make_order(lines=lines, paid_amount=10))


@pytest.mark.parametrize("qty", [0, -1])
def test_non_positive_qty_rejected(qty: int) -> None:
    with pytest.raises(ValidationError):
        Line(sku="sku-a", qty=qty, unit_price=100)


def test_negative_unit_price_rejected() -> None:
    with pytest.raises(ValidationError):
        Line(sku="sku-a", qty=1, unit_price=-1)


def test_zero_unit_price_accepted() -> None:
    assert Line(sku="sku-a", qty=1, unit_price=0).unit_price == 0


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        Order.model_validate(make_order(surprise=1))


def test_bad_carrier_rejected() -> None:
    with pytest.raises(ValidationError):
        Order.model_validate(make_order(carrier="D"))


def test_bad_actor_rejected() -> None:
    with pytest.raises(ValidationError):
        TraceEvent(
            seq=0, vt=0, workflow_id="wf-000001", actor="robot", event="x", data={}
        )


def test_models_are_immutable() -> None:
    order = Order.model_validate(make_order())
    with pytest.raises(ValidationError):
        order.paid_amount = 1  # type: ignore[misc]


def test_return_request_and_policy_decision_construct() -> None:
    req = ReturnRequest(
        workflow_id="wf-000001",
        order_id="order-000001",
        line_skus=["sku-a"],
        reason="damaged",
        requested_at=100,
    )
    assert req.line_skus == ["sku-a"]
    decision = PolicyDecision(
        id="pd-000001",
        workflow_id="wf-000001",
        rule_id="P1",
        allowed=True,
        reason="ok",
        vt=5,
    )
    assert decision.allowed is True


def test_trace_event_rejects_negative_seq_and_vt() -> None:
    for kwargs in ({"seq": -1, "vt": 0}, {"seq": 0, "vt": -1}):
        with pytest.raises(ValidationError):
            TraceEvent(
                workflow_id="wf-000001",
                actor="agent",
                event="x",
                data={},
                **kwargs,  # type: ignore[arg-type]
            )
