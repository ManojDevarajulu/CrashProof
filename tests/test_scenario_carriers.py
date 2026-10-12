from pathlib import Path

import pytest
from pydantic import ValidationError

from crashproof.config import (
    CarrierSettings,
    CarrierWeights,
    ScenarioConfig,
    VariantWeights,
    load_scenario_config,
)
from crashproof.ids import IdGen
from crashproof.sim.scenario import Scenario, make_scenario

CFG = load_scenario_config()
YAML = (Path(__file__).parent.parent / "src/crashproof/config").joinpath(
    "scenario_config.yaml"
)


def weighted(a: int, b: int, c: int) -> ScenarioConfig:
    carriers = CFG.carriers.model_copy(
        update={"weights": CarrierWeights(A=a, B=b, C=c)}
    )
    return CFG.model_copy(update={"carriers": carriers})


def quiet(config: ScenarioConfig) -> ScenarioConfig:
    cut2 = config.cut2.model_copy(
        update={
            "variant_weights": VariantWeights(
                none=1,
                outside_window=0,
                high_value_over_cap=0,
                fraud_flagged=0,
                partial_return=0,
            ),
            "early_refund_percent": 0,
        }
    )
    return config.model_copy(update={"cut2": cut2})


def build(seed: int, config: ScenarioConfig = CFG) -> Scenario:
    return make_scenario(seed, config, IdGen(seed))


def test_carrier_is_drawn_last() -> None:
    a_only = quiet(weighted(1, 0, 0))
    for seed in range(1, 51):
        default, only_a = build(seed, quiet(CFG)), build(seed, a_only)
        assert default.request == only_a.request
        assert default.behavior == only_a.behavior
        assert default.scan_delay == only_a.scan_delay
        assert default.order.model_copy(update={"carrier": "A"}) == only_a.order


def test_default_weights_draw_all_three_carriers() -> None:
    drawn = {build(seed).carrier for seed in range(1, 201)}
    assert drawn == {"A", "B", "C"}


def test_order_carrier_matches_scenario_carrier() -> None:
    for seed in range(1, 51):
        s = build(seed)
        assert s.order.carrier == s.carrier


@pytest.mark.parametrize(
    ("weights", "expected"),
    [
        ((1, 0, 0), {"A"}),
        ((0, 1, 0), {"B"}),
        ((0, 0, 1), {"C"}),
        ((1, 0, 1), {"A", "C"}),
    ],
)
def test_a_zero_weight_is_never_drawn(
    weights: tuple[int, int, int], expected: set[str]
) -> None:
    drawn = {build(seed, weighted(*weights)).carrier for seed in range(1, 201)}
    assert drawn == expected


def test_all_zero_weights_rejected() -> None:
    with pytest.raises(ValidationError):
        CarrierWeights(A=0, B=0, C=0)


def test_negative_weight_rejected() -> None:
    with pytest.raises(ValidationError):
        CarrierWeights(A=-1, B=1, C=1)


def test_b_label_delay_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        CarrierSettings(
            weights=CFG.carriers.weights, b_label_delay=0, c_scan_extra_delay=0
        )


def test_negative_c_scan_extra_delay_rejected() -> None:
    with pytest.raises(ValidationError):
        CarrierSettings(
            weights=CFG.carriers.weights, b_label_delay=1, c_scan_extra_delay=-1
        )


def test_missing_carriers_section_rejected(tmp_path: Path) -> None:
    text = YAML.read_text(encoding="utf-8")
    without = text[: text.index("carriers:")]
    path = tmp_path / "cfg.yaml"
    path.write_text(without, encoding="utf-8", newline="\n")
    with pytest.raises(ValidationError):
        load_scenario_config(path)
