from pathlib import Path

import pytest
from pydantic import ValidationError

from crashproof.config import (
    BehaviorWeights,
    CarrierWeights,
    ScenarioConfig,
    VariantWeights,
    load_scenario_config,
)
from crashproof.ids import IdGen
from crashproof.sim.scenario import make_scenario

CFG = load_scenario_config()
VALID_YAML = (Path(__file__).parent.parent / "src/crashproof/config").joinpath(
    "scenario_config.yaml"
)


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


CUT1 = quiet(CFG)


def build(seed: int, config: ScenarioConfig = CFG):  # type: ignore[no-untyped-def]
    return make_scenario(seed, config, IdGen(seed))


def test_same_seed_gives_identical_scenario() -> None:
    assert build(7) == build(7)


def test_workflow_id_is_minted_first() -> None:
    scenario = build(3)
    assert scenario.request.workflow_id == "wf-000001"
    assert scenario.order.order_id == "ord-000001"
    assert scenario.order.customer_id == "cus-000001"
    assert scenario.request.order_id == scenario.order.order_id


def test_ids_continue_from_the_shared_generator() -> None:
    ids = IdGen(1)
    make_scenario(1, CFG, ids)
    assert ids.new("wf") == "wf-000002"


def test_order_and_request_shape() -> None:
    a_only = CUT1.model_copy(
        update={
            "carriers": CFG.carriers.model_copy(
                update={"weights": CarrierWeights(A=1, B=0, C=0)}
            )
        }
    )
    for seed in range(1, 201):
        s = build(seed, a_only)
        lines = s.order.lines
        assert 1 <= len(lines) <= 3
        assert [line.sku for line in lines] == [
            f"sku-{i}" for i in range(1, len(lines) + 1)
        ]
        assert all(1 <= line.qty <= 3 for line in lines)
        assert all(500 <= line.unit_price <= 5000 for line in lines)
        assert s.order.paid_amount == sum(x.qty * x.unit_price for x in lines)
        assert s.order.delivered_at == 0
        assert s.order.carrier == "A"
        assert s.order.fraud_risk is False
        assert s.request.line_skus == [line.sku for line in lines]
        assert s.request.reason == "changed_mind"
        assert s.request.requested_at == 0
        assert 3600 <= s.scan_delay <= 7200


def test_seeds_one_to_two_hundred_cover_all_behaviors() -> None:
    seen = {build(seed, CUT1).behavior for seed in range(1, 201)}
    assert seen == {"normal", "duplicate_request", "never_ships_back"}


def test_zero_weight_behavior_is_never_drawn() -> None:
    weights = BehaviorWeights(normal=0, duplicate_request=1, never_ships_back=0)
    config = CUT1.model_copy(update={"behavior_weights": weights})
    assert {build(seed, config).behavior for seed in range(1, 101)} == {
        "duplicate_request"
    }


def test_scan_delay_is_drawn_for_every_behavior() -> None:
    first = BehaviorWeights(normal=10, duplicate_request=0, never_ships_back=0)
    last = BehaviorWeights(normal=0, duplicate_request=0, never_ships_back=10)
    config_a = CUT1.model_copy(update={"behavior_weights": first})
    config_b = CUT1.model_copy(update={"behavior_weights": last})
    for seed in range(1, 21):
        a, b = build(seed, config_a), build(seed, config_b)
        assert (a.behavior, b.behavior) == ("normal", "never_ships_back")
        assert a.scan_delay == b.scan_delay


def _config_text(**replacements: str) -> str:
    text = VALID_YAML.read_text(encoding="utf-8")
    for old, new in replacements.items():
        assert old in text
        text = text.replace(old, new)
    return text


def _load(tmp_path: Path, text: str) -> ScenarioConfig:
    path = tmp_path / "cfg.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return load_scenario_config(path)


def test_scan_delay_max_below_min_rejected(tmp_path: Path) -> None:
    text = _config_text(scan_delay_max="scan_delay_max_x").replace(
        "scan_delay_max_x: 7200", "scan_delay_max: 3599"
    )
    with pytest.raises(ValidationError):
        _load(tmp_path, text)


def test_all_zero_weights_rejected(tmp_path: Path) -> None:
    text = (
        VALID_YAML.read_text(encoding="utf-8")
        .replace("normal: 6", "normal: 0")
        .replace("duplicate_request: 2\n  never", "duplicate_request: 0\n  never")
        .replace("never_ships_back: 2", "never_ships_back: 0")
    )
    with pytest.raises(ValidationError):
        _load(tmp_path, text)


@pytest.mark.parametrize("section", ["timing", "run", "behavior_weights"])
def test_missing_section_rejected(tmp_path: Path, section: str) -> None:
    kept: list[str] = []
    skipping = False
    for line in VALID_YAML.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{section}:"):
            skipping = True
            continue
        if skipping and line.startswith(" "):
            continue
        skipping = False
        kept.append(line)
    with pytest.raises(ValidationError):
        _load(tmp_path, "\n".join(kept) + "\n")
