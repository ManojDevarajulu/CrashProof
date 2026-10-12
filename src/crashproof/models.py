from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

type Json = None | bool | int | str | list[Json] | dict[str, Json]

Actor = Literal["agent", "runtime", "policy", "world", "fault", "human"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Line(_Model):
    sku: str
    qty: int = Field(gt=0)
    unit_price: int = Field(ge=0)


class Order(_Model):
    order_id: str
    customer_id: str
    lines: list[Line]
    paid_amount: int
    delivered_at: int | None
    carrier: Literal["A", "B", "C"]
    fraud_risk: bool

    @model_validator(mode="after")
    def _paid_amount_matches_lines(self) -> "Order":
        expected = sum(line.qty * line.unit_price for line in self.lines)
        if self.paid_amount != expected:
            raise ValueError(
                f"paid_amount {self.paid_amount} != sum of lines {expected}"
            )
        return self


class ReturnRequest(_Model):
    workflow_id: str
    order_id: str
    line_skus: list[str]
    reason: str
    requested_at: int


class PolicyDecision(_Model):
    id: str
    workflow_id: str
    rule_id: str
    allowed: bool
    reason: str
    vt: int


class TraceEvent(_Model):
    seq: int = Field(ge=0)
    vt: int = Field(ge=0)
    workflow_id: str
    actor: Actor
    event: str
    data: dict[str, Json]
