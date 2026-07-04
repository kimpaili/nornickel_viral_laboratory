from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LossCellData:
    id: int
    plant_id: int
    metal_id: int
    metal_code: str
    metal_price_usd_t: Decimal
    size_class_id: int
    size_class_code: str
    microns_lo: Decimal | None
    microns_hi: Decimal | None
    mineral_form_id: int
    mineral_form_code: str
    loss_cause: str
    recoverable: bool
    tons: Decimal


@dataclass(frozen=True)
class RuleData:
    id: int
    code: str
    module_code: str
    target_cause: str
    target_size_class_id: int | None
    target_size_class_code: str | None
    coeff: Decimal
    coeff_min: Decimal | None
    coeff_max: Decimal | None
    side_effect: str | None
    requires_kind: str | None
    source: str | None

    @property
    def required_kinds(self) -> set[str]:
        if not self.requires_kind:
            return set()
        return {item.strip() for item in self.requires_kind.split(",") if item.strip()}


@dataclass(frozen=True)
class ModuleInput:
    loss_cells: list[LossCellData]
    rule: RuleData
    equipment_kinds: set[str]
    metal_prices: dict[str, Decimal]


@dataclass(frozen=True)
class ModuleVerdict:
    module_code: str
    rule_code: str
    target_cells: list[int]
    effect_tons: tuple[Decimal, Decimal]
    effect_usd: tuple[Decimal, Decimal]
    feasible: bool
    side_effect: str | None
    provenance: dict
