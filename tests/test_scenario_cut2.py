from pathlib import Path

import pytest
from pydantic import ValidationError

from crashproof.config import (
    Cut2Settings,
    PriceRange,
    ScenarioConfig,
    VariantWeights,
    load_policy_config,
    load_scenario_config,
)
from crashproof.ids import IdGen
from crashproof.rng import Rng
from crashproof.sim.scenario import ORACLE_WINDOW_DAYS, Scenario, make_scenario

CFG = load_scenario_config()
POLICY = load_policy_config()
DAY = 86400
YAML = Path(__file__).parent.parent / "src/crashproof/config/scenario_config.yaml"


def build(seed: int, config: ScenarioConfig = CFG) -> Scenario:
    return make_scenario(seed, config, IdGen(seed))


def only(variant: str, early: int = 0) -> ScenarioConfig:
    names = (
        "none",
        "outside_window",
        "high_value_over_cap",
        "fraud_flagged",
        "partial_return",
    )
    weights = VariantWeights(**{n: int(n == variant) for n in names})
    cut2 = CFG.cut2.model_copy(
        update={"variant_weights": weights, "early_refund_percent": early}
    )
    return CFG.model_copy(update={"cut2": cut2})


def test_outside_window_fields_and_oracle() -> None:
    for seed in range(1, 31):
        s = build(seed, only("outside_window"))
        assert s.behavior == s.variant == "outside_window"
        assert s.order.delivered_at is not None
        assert s.order.delivered_at < -(ORACLE_WINDOW_DAYS * DAY)
        assert s.order.delivered_at >= -(2 * ORACLE_WINDOW_DAYS * DAY + 0)
        assert s.request.requested_at == 0
        assert not s.oracle_eligible


def test_fraud_flagged_fields_and_oracle() -> None:
    for seed in range(1, 31):
        s = build(seed, only("fraud_flagged"))
        assert s.order.fraud_risk is True
        assert s.order.delivered_at == 0
        assert not s.oracle_eligible


def test_high_value_over_cap_is_a_full_return_above_the_cap() -> None:
    for seed in range(1, 31):
        s = build(seed, only("high_value_over_cap"))
        price = CFG.cut2.high_value_unit_price
        assert price.min <= s.order.lines[0].unit_price <= price.max
        assert s.request.line_skus == [x.sku for x in s.order.lines]
        assert s.order.paid_amount > POLICY.refund_cap
        assert s.oracle_eligible


def test_high_value_minimum_exceeds_the_refund_cap() -> None:
    assert CFG.cut2.high_value_unit_price.min > POLICY.refund_cap


def test_partial_return_returns_a_strict_prefix() -> None:
    for seed in range(1, 61):
        s = build(seed, only("partial_return"))
        skus = [x.sku for x in s.order.lines]
        assert len(skus) >= 2
        assert 1 <= len(s.request.line_skus) < len(skus)
        assert s.request.line_skus == skus[: len(s.request.line_skus)]
        assert s.oracle_eligible


def test_none_variant_is_eligible_with_delivered_at_zero() -> None:
    for seed in range(1, 31):
        s = build(seed, only("none"))
        assert (s.order.delivered_at, s.order.fraud_risk) == (0, False)
        assert s.oracle_eligible and not s.early_refund


def test_early_refund_percent_extremes() -> None:
    assert all(build(s, only("none", 100)).early_refund for s in range(1, 21))
    assert not any(build(s, only("none", 0)).early_refund for s in range(1, 21))


def test_default_config_draws_every_variant() -> None:
    assert {build(s).variant for s in range(1, 201)} == {
        "none",
        "outside_window",
        "high_value_over_cap",
        "fraud_flagged",
        "partial_return",
    }


def test_zero_weight_variant_is_never_drawn() -> None:
    assert {build(s, only("fraud_flagged")).variant for s in range(1, 101)} == {
        "fraud_flagged"
    }


def test_variant_none_keeps_the_original_draws() -> None:
    """Reference reimplementation of the pre-Cut-2 draw order."""
    for seed in range(1, 51):
        s = build(seed, only("none"))
        rng = Rng(seed).fork("scenario")
        lines = []
        for n in range(1, rng.randint(1, 3) + 1):
            lines.append((f"sku-{n}", rng.randint(1, 3), rng.randint(500, 5000)))
        w = CFG.behavior_weights
        pick = rng.randint(1, w.normal + w.duplicate_request + w.never_ships_back)
        behavior = (
            "normal"
            if pick <= w.normal
            else "duplicate_request"
            if pick <= w.normal + w.duplicate_request
            else "never_ships_back"
        )
        scan_delay = rng.randint(CFG.timing.scan_delay_min, CFG.timing.scan_delay_max)
        c = CFG.carriers.weights
        pick = rng.randint(1, c.A + c.B + c.C)
        carrier = "A" if pick <= c.A else "B" if pick <= c.A + c.B else "C"
        assert [(x.sku, x.qty, x.unit_price) for x in s.order.lines] == lines
        assert (s.behavior, s.scan_delay, s.carrier) == (behavior, scan_delay, carrier)


def test_scenario_is_deterministic() -> None:
    assert build(9) == build(9)


def text(**replacements: str) -> str:
    out = YAML.read_text(encoding="utf-8")
    for old, new in replacements.items():
        assert old in out
        out = out.replace(old, new)
    return out


def load(tmp_path: Path, content: str) -> ScenarioConfig:
    path = tmp_path / "c.yaml"
    path.write_text(content, encoding="utf-8", newline="\n")
    return load_scenario_config(path)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("scan_timeout: 172800", "scan_timeout: 0"),
        ("early_refund_percent: 10", "early_refund_percent: 101"),
        ("early_refund_percent: 10", "early_refund_percent: -1"),
        ("min: 20001", "min: 0"),
        ("max: 40000", "max: 20000"),
        ("none: 6", "none: -1"),
    ],
)
def test_cut2_config_validators(tmp_path: Path, old: str, new: str) -> None:
    with pytest.raises(ValidationError):
        load(tmp_path, YAML.read_text(encoding="utf-8").replace(old, new))


def test_all_zero_variant_weights_rejected(tmp_path: Path) -> None:
    content = (
        YAML.read_text(encoding="utf-8")
        .replace("none: 6", "none: 0")
        .replace("outside_window: 1", "outside_window: 0")
        .replace("high_value_over_cap: 1", "high_value_over_cap: 0")
        .replace("fraud_flagged: 1", "fraud_flagged: 0")
        .replace("partial_return: 1", "partial_return: 0")
    )
    with pytest.raises(ValidationError):
        load(tmp_path, content)


def test_missing_cut2_section_rejected(tmp_path: Path) -> None:
    head = YAML.read_text(encoding="utf-8").split("cut2:")[0]
    with pytest.raises(ValidationError):
        load(tmp_path, head)


def test_cut2_frozen_values() -> None:
    assert CFG.timing.scan_timeout == 172800
    assert CFG.cut2 == Cut2Settings(
        variant_weights=VariantWeights(
            none=6,
            outside_window=1,
            high_value_over_cap=1,
            fraud_flagged=1,
            partial_return=1,
        ),
        early_refund_percent=10,
        high_value_unit_price=PriceRange(min=20001, max=40000),
    )
