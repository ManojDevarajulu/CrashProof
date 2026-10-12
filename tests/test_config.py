from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from crashproof.config import (
    BehaviorWeights,
    CarrierSettings,
    CarrierWeights,
    Cut2Settings,
    FaultProbabilities,
    PriceRange,
    RunLimits,
    ScenarioConfig,
    Timing,
    VariantWeights,
    load_scenario_config,
)

SECTIONS = (
    "timing: {duplicate_request_delay: 60, scan_delay_min: 3600,"
    " scan_delay_max: 7200, scan_timeout: 172800}\n"
    "run: {max_restarts: 5, max_events: 10000}\n"
    "behavior_weights: {normal: 6, duplicate_request: 2, never_ships_back: 2}\n"
    "carriers: {weights: {A: 4, B: 4, C: 2}, b_label_delay: 120,"
    " c_scan_extra_delay: 1800}\n"
    "cut2: {variant_weights: {none: 6, outside_window: 1, high_value_over_cap: 1,"
    " fraud_flagged: 1, partial_return: 1}, early_refund_percent: 10,"
    " high_value_unit_price: {min: 20001, max: 40000}}\n"
)
VALID = "faults: {F1: 0.1, F2: 0.2, F3: 0.3, F5: 0.4}\nmax_attempts: 3\n" + SECTIONS


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_default_file_matches_frozen_spec_values() -> None:
    cfg = load_scenario_config()
    assert cfg == ScenarioConfig(
        faults=FaultProbabilities(F1=0.08, F2=0.08, F3=0.15, F5=0.05),
        max_attempts=5,
        timing=Timing(
            duplicate_request_delay=60,
            scan_delay_min=3600,
            scan_delay_max=7200,
            scan_timeout=172800,
        ),
        run=RunLimits(max_restarts=5, max_events=10000),
        behavior_weights=BehaviorWeights(
            normal=6, duplicate_request=2, never_ships_back=2
        ),
        carriers=CarrierSettings(
            weights=CarrierWeights(A=4, B=4, C=2),
            b_label_delay=120,
            c_scan_extra_delay=1800,
        ),
        cut2=Cut2Settings(
            variant_weights=VariantWeights(
                none=6,
                outside_window=1,
                high_value_over_cap=1,
                fraud_flagged=1,
                partial_return=1,
            ),
            early_refund_percent=10,
            high_value_unit_price=PriceRange(min=20001, max=40000),
        ),
    )


def test_custom_path_loads(tmp_path: Path) -> None:
    cfg = load_scenario_config(write(tmp_path, VALID))
    assert cfg.faults.F2 == 0.2
    assert cfg.max_attempts == 3


def test_probabilities_zero_and_one_accepted(tmp_path: Path) -> None:
    text = "faults: {F1: 0, F2: 1, F3: 0.0, F5: 1.0}\nmax_attempts: 1\n" + SECTIONS
    cfg = load_scenario_config(write(tmp_path, text))
    assert cfg.faults.F1 == 0
    assert cfg.faults.F2 == 1


@pytest.mark.parametrize("p", ["-0.01", "1.01"])
def test_probability_out_of_range_rejected(tmp_path: Path, p: str) -> None:
    text = f"faults: {{F1: {p}, F2: 0, F3: 0, F5: 0}}\nmax_attempts: 5\n" + SECTIONS
    with pytest.raises(ValidationError):
        load_scenario_config(write(tmp_path, text))


def test_max_attempts_zero_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_scenario_config(
            write(tmp_path, VALID.replace("max_attempts: 3", "max_attempts: 0"))
        )


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_scenario_config(write(tmp_path, VALID + "extra: 1\n"))


def test_unknown_fault_key_rejected(tmp_path: Path) -> None:
    text = "faults: {F1: 0, F2: 0, F3: 0, F5: 0, F4: 0}\nmax_attempts: 5\n" + SECTIONS
    with pytest.raises(ValidationError):
        load_scenario_config(write(tmp_path, text))


def test_missing_fault_key_rejected(tmp_path: Path) -> None:
    text = "faults: {F1: 0, F2: 0, F3: 0}\nmax_attempts: 5\n" + SECTIONS
    with pytest.raises(ValidationError):
        load_scenario_config(write(tmp_path, text))


def test_missing_max_attempts_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_scenario_config(
            write(tmp_path, "faults: {F1: 0, F2: 0, F3: 0, F5: 0}\n" + SECTIONS)
        )


def test_bool_where_number_expected_rejected(tmp_path: Path) -> None:
    text = "faults: {F1: true, F2: 0, F3: 0, F5: 0}\nmax_attempts: 5\n" + SECTIONS
    with pytest.raises(ValidationError):
        load_scenario_config(write(tmp_path, text))
    with pytest.raises(ValidationError):
        load_scenario_config(
            write(tmp_path, VALID.replace("max_attempts: 3", "max_attempts: true"))
        )


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_scenario_config(write(tmp_path, "- 1\n- 2\n"))


def test_malformed_yaml_propagates(tmp_path: Path) -> None:
    with pytest.raises(yaml.YAMLError):
        load_scenario_config(write(tmp_path, "faults: [unclosed\n"))


def test_missing_file_propagates(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_scenario_config(tmp_path / "nope.yaml")


def test_config_is_frozen() -> None:
    cfg = load_scenario_config()
    with pytest.raises(ValidationError):
        cfg.max_attempts = 9  # type: ignore[misc]
