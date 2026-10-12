import pytest
from pydantic import ValidationError

from crashproof.models import Json, Line, Order
from crashproof.runtime.tools import (
    CheckPolicyArgs,
    CloseCaseArgs,
    CreateReturnLabelArgs,
    EscalateToHumanArgs,
    GetOrderArgs,
    IssueRefundArgs,
    ToolCall,
    ToolResult,
    validate_args,
)

VALID: list[tuple[str, dict[str, Json], type]] = [
    ("get_order", {"order_id": "o1"}, GetOrderArgs),
    ("check_policy", {"order_id": "o1"}, CheckPolicyArgs),
    ("create_return_label", {"order_id": "o1"}, CreateReturnLabelArgs),
    ("issue_refund", {"order_id": "o1", "amount": 500}, IssueRefundArgs),
    ("escalate_to_human", {"reason": "over cap"}, EscalateToHumanArgs),
    ("close_case", {}, CloseCaseArgs),
]


@pytest.mark.parametrize(("tool", "args", "model"), VALID)
def test_valid_args_accepted(tool: str, args: dict[str, Json], model: type) -> None:
    parsed = validate_args(ToolCall.model_validate({"tool": tool, "args": args}))
    assert isinstance(parsed, model)
    assert parsed.model_dump() == args


@pytest.mark.parametrize(("tool", "args", "model"), VALID)
def test_extra_field_rejected(tool: str, args: dict[str, Json], model: type) -> None:
    call = ToolCall.model_validate({"tool": tool, "args": {**args, "reference": "wf"}})
    with pytest.raises(ValidationError):
        validate_args(call)


@pytest.mark.parametrize(("tool", "args", "model"), VALID[:-1])
def test_missing_field_rejected(tool: str, args: dict[str, Json], model: type) -> None:
    key = sorted(args)[0]
    short = {k: v for k, v in args.items() if k != key}
    with pytest.raises(ValidationError):
        validate_args(ToolCall.model_validate({"tool": tool, "args": short}))


def test_wrong_types_rejected() -> None:
    for tool, args in [
        ("get_order", {"order_id": 5}),
        ("check_policy", {"order_id": None}),
        ("create_return_label", {"order_id": ["o1"]}),
        ("issue_refund", {"order_id": "o1", "amount": "500"}),
        ("escalate_to_human", {"reason": 1}),
    ]:
        with pytest.raises(ValidationError):
            validate_args(ToolCall.model_validate({"tool": tool, "args": args}))


@pytest.mark.parametrize("amount", [True, False, 0, -1])
def test_refund_amount_rejected_via_tool_call(amount: Json) -> None:
    call = ToolCall(tool="issue_refund", args={"order_id": "o1", "amount": amount})
    with pytest.raises(ValidationError):
        validate_args(call)


def test_refund_float_amount_rejected() -> None:
    with pytest.raises(ValidationError):
        IssueRefundArgs.model_validate({"order_id": "o1", "amount": 5.0})


def test_unknown_tool_name_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolCall.model_validate({"tool": "delete_everything", "args": {}})
    bypass = ToolCall.model_construct(tool="delete_everything", args={})
    with pytest.raises(ValueError, match="unknown tool"):
        validate_args(bypass)


def test_models_are_frozen() -> None:
    call = ToolCall(tool="close_case", args={})
    result = ToolResult(ok=True, data={})
    with pytest.raises(ValidationError):
        call.tool = "get_order"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        result.ok = False  # type: ignore[misc]
    with pytest.raises(ValidationError):
        IssueRefundArgs(order_id="o1", amount=1).amount = 2  # type: ignore[misc]


def test_tool_result_defaults_and_extra_forbidden() -> None:
    assert ToolResult(ok=False, data={}, error="boom").error == "boom"
    assert ToolResult(ok=True, data={}).error is None
    with pytest.raises(ValidationError):
        ToolResult.model_validate({"ok": True, "data": {}, "extra": 1})


def test_get_order_payload_round_trips() -> None:
    order = Order(
        order_id="o1",
        customer_id="c1",
        lines=[Line(sku="a", qty=2, unit_price=100)],
        paid_amount=200,
        delivered_at=None,
        carrier="A",
        fraud_risk=False,
    )
    result = ToolResult(ok=True, data={"order": order.model_dump(mode="json")})
    assert Order.model_validate(result.data["order"]) == order
