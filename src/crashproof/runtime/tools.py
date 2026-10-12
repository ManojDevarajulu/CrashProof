from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from crashproof.models import Json

ToolName = Literal[
    "get_order",
    "check_policy",
    "create_return_label",
    "issue_refund",
    "escalate_to_human",
    "close_case",
]


class ToolModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class GetOrderArgs(ToolModel):
    order_id: str


class CheckPolicyArgs(ToolModel):
    order_id: str


class CreateReturnLabelArgs(ToolModel):
    order_id: str


class IssueRefundArgs(ToolModel):
    order_id: str
    amount: int = Field(gt=0)


class EscalateToHumanArgs(ToolModel):
    reason: str


class CloseCaseArgs(ToolModel):
    pass


ToolArgs = (
    GetOrderArgs
    | CheckPolicyArgs
    | CreateReturnLabelArgs
    | IssueRefundArgs
    | EscalateToHumanArgs
    | CloseCaseArgs
)

_ARGS_BY_TOOL: dict[str, type[ToolArgs]] = {
    "get_order": GetOrderArgs,
    "check_policy": CheckPolicyArgs,
    "create_return_label": CreateReturnLabelArgs,
    "issue_refund": IssueRefundArgs,
    "escalate_to_human": EscalateToHumanArgs,
    "close_case": CloseCaseArgs,
}


class ToolCall(ToolModel):
    tool: ToolName
    args: dict[str, Json]


class ToolResult(ToolModel):
    ok: bool
    data: dict[str, Json]
    error: str | None = None


def validate_args(call: ToolCall) -> ToolArgs:
    model = _ARGS_BY_TOOL.get(call.tool)
    if model is None:
        raise ValueError(f"unknown tool: {call.tool!r}")
    return model.model_validate(call.args)
