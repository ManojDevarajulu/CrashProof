from typing import Literal

from pydantic import BaseModel, ConfigDict

from crashproof.config import ScenarioConfig
from crashproof.ids import IdGen
from crashproof.models import Line, Order, ReturnRequest
from crashproof.rng import Rng

type Variant = Literal[
    "none", "outside_window", "high_value_over_cap", "fraud_flagged", "partial_return"
]

type Behavior = Literal[
    "normal",
    "duplicate_request",
    "never_ships_back",
    "outside_window",
    "high_value_over_cap",
    "fraud_flagged",
    "partial_return",
]

ORACLE_WINDOW_DAYS = 30

type CarrierName = Literal["A", "B", "C"]

_BEHAVIORS: tuple[Behavior, ...] = ("normal", "duplicate_request", "never_ships_back")
_VARIANTS: tuple[Variant, ...] = (
    "none",
    "outside_window",
    "high_value_over_cap",
    "fraud_flagged",
    "partial_return",
)
_CARRIERS: tuple[CarrierName, ...] = ("A", "B", "C")


class Scenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    seed: int
    order: Order
    request: ReturnRequest
    behavior: Behavior
    scan_delay: int
    carrier: CarrierName
    variant: Variant
    early_refund: bool
    oracle_eligible: bool


def make_scenario(seed: int, config: ScenarioConfig, ids: IdGen) -> Scenario:
    """Build the one scenario for a seed.

    The order of id minting and rng draws is fixed, so the number of draws never
    depends on the behavior that is picked. The carrier, then the Cut 2 variant and
    early-refund draws, come last so adding them never changes an earlier draw.
    """
    rng = Rng(seed).fork("scenario")
    workflow_id = ids.new("wf")
    order_id = ids.new("ord")
    customer_id = ids.new("cus")
    lines = [_draw_line(rng, n) for n in range(1, rng.randint(1, 3) + 1)]
    request = ReturnRequest(
        workflow_id=workflow_id,
        order_id=order_id,
        line_skus=[line.sku for line in lines],
        reason="changed_mind",
        requested_at=0,
    )
    behavior = _draw_behavior(rng, config)
    timing = config.timing
    scan_delay = rng.randint(timing.scan_delay_min, timing.scan_delay_max)
    carrier = _draw_carrier(rng, config)
    variant = _draw_variant(rng, config)
    early_refund = rng.randint(1, 100) <= config.cut2.early_refund_percent
    delivered_at = 0
    fraud_risk = False
    if variant == "outside_window":
        overdue = rng.randint(1, ORACLE_WINDOW_DAYS * 86400)
        delivered_at = -(ORACLE_WINDOW_DAYS * 86400 + overdue)
    elif variant == "fraud_flagged":
        fraud_risk = True
    elif variant == "high_value_over_cap":
        price = config.cut2.high_value_unit_price
        first = lines[0].model_copy(
            update={"unit_price": rng.randint(price.min, price.max)}
        )
        lines = [first, *lines[1:]]
    elif variant == "partial_return":
        if len(lines) == 1:
            lines.append(_draw_line(rng, 2))
        kept = rng.randint(1, len(lines) - 1)
        request = request.model_copy(
            update={"line_skus": [line.sku for line in lines[:kept]]}
        )
    order = Order(
        order_id=order_id,
        customer_id=customer_id,
        lines=lines,
        paid_amount=sum(line.qty * line.unit_price for line in lines),
        delivered_at=delivered_at,
        carrier=carrier,
        fraud_risk=fraud_risk,
    )
    return Scenario(
        seed=seed,
        order=order,
        request=request,
        behavior=behavior if variant == "none" else variant,
        scan_delay=scan_delay,
        carrier=carrier,
        variant=variant,
        early_refund=early_refund,
        oracle_eligible=oracle_eligible(order, request),
    )


def oracle_eligible(order: Order, request: ReturnRequest) -> bool:
    """Ground truth from SPEC section 6. Used by invariants, never by runtimes."""
    if order.delivered_at is None or order.fraud_risk:
        return False
    return request.requested_at - order.delivered_at <= ORACLE_WINDOW_DAYS * 86400


def _draw_line(rng: Rng, number: int) -> Line:
    qty = rng.randint(1, 3)
    unit_price = rng.randint(500, 5000)
    return Line(sku=f"sku-{number}", qty=qty, unit_price=unit_price)


def _draw_behavior(rng: Rng, config: ScenarioConfig) -> Behavior:
    weights = config.behavior_weights
    by_name = {
        "normal": weights.normal,
        "duplicate_request": weights.duplicate_request,
        "never_ships_back": weights.never_ships_back,
    }
    pick = rng.randint(1, sum(by_name.values()))
    cumulative = 0
    for name in _BEHAVIORS:
        cumulative += by_name[name]
        if pick <= cumulative:
            return name
    raise AssertionError("unreachable: pick is within the total weight")


def _draw_variant(rng: Rng, config: ScenarioConfig) -> Variant:
    weights = config.cut2.variant_weights
    by_name: dict[Variant, int] = {
        "none": weights.none,
        "outside_window": weights.outside_window,
        "high_value_over_cap": weights.high_value_over_cap,
        "fraud_flagged": weights.fraud_flagged,
        "partial_return": weights.partial_return,
    }
    pick = rng.randint(1, sum(by_name.values()))
    cumulative = 0
    for name in _VARIANTS:
        cumulative += by_name[name]
        if pick <= cumulative:
            return name
    raise AssertionError("unreachable: pick is within the total weight")


def _draw_carrier(rng: Rng, config: ScenarioConfig) -> CarrierName:
    weights = config.carriers.weights
    by_name = {"A": weights.A, "B": weights.B, "C": weights.C}
    pick = rng.randint(1, sum(by_name.values()))
    cumulative = 0
    for name in _CARRIERS:
        cumulative += by_name[name]
        if pick <= cumulative:
            return name
    raise AssertionError("unreachable: pick is within the total weight")
