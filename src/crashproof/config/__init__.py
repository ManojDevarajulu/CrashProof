from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_DEFAULT_PATH = Path(__file__).parent / "scenario_config.yaml"
_DEFAULT_POLICY_PATH = Path(__file__).parent / "policy_rules.yaml"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class FaultProbabilities(_Model):
    F1: float = Field(ge=0, le=1)
    F2: float = Field(ge=0, le=1)
    F3: float = Field(ge=0, le=1)
    F5: float = Field(ge=0, le=1)


class Timing(_Model):
    duplicate_request_delay: int = Field(ge=0)
    scan_delay_min: int = Field(ge=0)
    scan_delay_max: int
    scan_timeout: int = Field(ge=1)

    @model_validator(mode="after")
    def _max_not_below_min(self) -> "Timing":
        if self.scan_delay_max < self.scan_delay_min:
            raise ValueError("scan_delay_max must be >= scan_delay_min")
        return self


class RunLimits(_Model):
    max_restarts: int = Field(ge=0)
    max_events: int = Field(ge=1)


class BehaviorWeights(_Model):
    normal: int = Field(ge=0)
    duplicate_request: int = Field(ge=0)
    never_ships_back: int = Field(ge=0)

    @model_validator(mode="after")
    def _some_weight_positive(self) -> "BehaviorWeights":
        if self.normal + self.duplicate_request + self.never_ships_back <= 0:
            raise ValueError("at least one behavior weight must be above 0")
        return self


class CarrierWeights(_Model):
    A: int = Field(ge=0)
    B: int = Field(ge=0)
    C: int = Field(ge=0)

    @model_validator(mode="after")
    def _some_weight_positive(self) -> "CarrierWeights":
        if self.A + self.B + self.C <= 0:
            raise ValueError("at least one carrier weight must be above 0")
        return self


class CarrierSettings(_Model):
    weights: CarrierWeights
    b_label_delay: int = Field(ge=1)
    c_scan_extra_delay: int = Field(ge=0)


class VariantWeights(_Model):
    none: int = Field(ge=0)
    outside_window: int = Field(ge=0)
    high_value_over_cap: int = Field(ge=0)
    fraud_flagged: int = Field(ge=0)
    partial_return: int = Field(ge=0)

    @model_validator(mode="after")
    def _some_weight_positive(self) -> "VariantWeights":
        total = (
            self.none
            + self.outside_window
            + self.high_value_over_cap
            + self.fraud_flagged
            + self.partial_return
        )
        if total <= 0:
            raise ValueError("at least one variant weight must be above 0")
        return self


class PriceRange(_Model):
    min: int = Field(ge=1)
    max: int

    @model_validator(mode="after")
    def _max_not_below_min(self) -> "PriceRange":
        if self.max < self.min:
            raise ValueError("max must be >= min")
        return self


class Cut2Settings(_Model):
    variant_weights: VariantWeights
    early_refund_percent: int = Field(ge=0, le=100)
    high_value_unit_price: PriceRange


class ScenarioConfig(_Model):
    faults: FaultProbabilities
    max_attempts: int = Field(ge=1)
    timing: Timing
    run: RunLimits
    behavior_weights: BehaviorWeights
    carriers: CarrierSettings
    cut2: Cut2Settings


def load_scenario_config(path: Path | None = None) -> ScenarioConfig:
    source = _DEFAULT_PATH if path is None else path
    with source.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return ScenarioConfig.model_validate(raw)


type RuleId = Literal["P1", "P2", "P3", "P4", "P5", "P6"]


class ApproverSettings(_Model):
    delay_min: int = Field(ge=0)
    delay_max: int
    approve_weight: int = Field(ge=0)
    deny_weight: int = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> "ApproverSettings":
        if self.delay_max < self.delay_min:
            raise ValueError("delay_max must be >= delay_min")
        if self.approve_weight + self.deny_weight <= 0:
            raise ValueError("at least one approver weight must be above 0")
        return self


class PolicyConfig(_Model):
    refund_cap: int = Field(ge=1)
    window_days: int = Field(ge=1)
    label: list[RuleId]
    refund: list[RuleId]
    approver: ApproverSettings


def load_policy_config(path: Path | None = None) -> PolicyConfig:
    source = _DEFAULT_POLICY_PATH if path is None else path
    with source.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return PolicyConfig.model_validate(raw)
