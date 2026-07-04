from collections.abc import Callable
from decimal import Decimal

from .contract import LossCellData, ModuleInput, ModuleVerdict


CellFilter = Callable[[LossCellData], bool]


def evaluate_rule_effect(
    module_input: ModuleInput,
    module_code: str,
    extra_filter: CellFilter | None = None,
) -> ModuleVerdict:
    rule = module_input.rule
    target_cells = [
        cell
        for cell in module_input.loss_cells
        if cell.recoverable
        and cell.loss_cause == rule.target_cause
        and (rule.target_size_class_id is None or cell.size_class_id == rule.target_size_class_id)
        and (extra_filter is None or extra_filter(cell))
    ]

    coeff = rule.coeff
    coeff_min = rule.coeff_min if rule.coeff_min is not None else coeff
    coeff_max = rule.coeff_max if rule.coeff_max is not None else coeff

    effect_tons_min = sum((cell.tons * coeff_min for cell in target_cells), Decimal("0"))
    effect_tons_max = sum((cell.tons * coeff_max for cell in target_cells), Decimal("0"))
    effect_usd_min = sum(
        (cell.tons * coeff_min * cell.metal_price_usd_t for cell in target_cells),
        Decimal("0"),
    )
    effect_usd_max = sum(
        (cell.tons * coeff_max * cell.metal_price_usd_t for cell in target_cells),
        Decimal("0"),
    )
    target_cell_details = [
        {
            "id": cell.id,
            "metal_code": cell.metal_code,
            "size_class_code": cell.size_class_code,
            "mineral_form_code": cell.mineral_form_code,
            "loss_cause": cell.loss_cause,
            "tons": str(cell.tons),
            "effect_tons_min": str(cell.tons * coeff_min),
            "effect_tons_max": str(cell.tons * coeff_max),
            "effect_usd_min": str(cell.tons * coeff_min * cell.metal_price_usd_t),
            "effect_usd_max": str(cell.tons * coeff_max * cell.metal_price_usd_t),
        }
        for cell in target_cells
    ]

    required_kinds = rule.required_kinds
    feasible = required_kinds.issubset(module_input.equipment_kinds)
    provenance = {
        "module": module_code,
        "rule_id": rule.id,
        "rule_code": rule.code,
        "target_cause": rule.target_cause,
        "target_size_class": rule.target_size_class_code,
        "coeff": str(coeff),
        "coeff_min": str(coeff_min),
        "coeff_max": str(coeff_max),
        "target_cell_ids": [cell.id for cell in target_cells],
        "target_cells": target_cell_details,
        "effect_tons_min": str(effect_tons_min),
        "effect_tons_max": str(effect_tons_max),
        "effect_usd_min": str(effect_usd_min),
        "effect_usd_max": str(effect_usd_max),
        "required_equipment": sorted(required_kinds),
        "plant_equipment": sorted(module_input.equipment_kinds),
        "source": rule.source,
    }

    return ModuleVerdict(
        module_code=module_code,
        rule_code=rule.code,
        target_cells=[cell.id for cell in target_cells],
        effect_tons=(effect_tons_min, effect_tons_max),
        effect_usd=(effect_usd_min, effect_usd_max),
        feasible=feasible,
        side_effect=rule.side_effect,
        provenance=provenance,
    )
