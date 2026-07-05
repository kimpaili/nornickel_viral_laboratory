from collections.abc import Callable
from decimal import Decimal
import math

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

    cell_coeffs = {
        cell.id: _curve_coefficients(rule, module_code, cell, coeff_min, coeff, coeff_max)
        for cell in target_cells
    }

    effect_tons_min = sum(
        (cell.tons * cell_coeffs[cell.id]["min"] for cell in target_cells),
        Decimal("0"),
    )
    effect_tons_max = sum(
        (cell.tons * cell_coeffs[cell.id]["max"] for cell in target_cells),
        Decimal("0"),
    )
    effect_usd_min = sum(
        (
            cell.tons * cell_coeffs[cell.id]["min"] * cell.metal_price_usd_t
            for cell in target_cells
        ),
        Decimal("0"),
    )
    effect_usd_max = sum(
        (
            cell.tons * cell_coeffs[cell.id]["max"] * cell.metal_price_usd_t
            for cell in target_cells
        ),
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
            "curve_factor": str(cell_coeffs[cell.id]["factor"]),
            "curve_coeff_min": str(cell_coeffs[cell.id]["min"]),
            "curve_coeff": str(cell_coeffs[cell.id]["mean"]),
            "curve_coeff_max": str(cell_coeffs[cell.id]["max"]),
            "curve_explanation": cell_coeffs[cell.id]["explanation"],
            "effect_tons_min": str(cell.tons * cell_coeffs[cell.id]["min"]),
            "effect_tons_max": str(cell.tons * cell_coeffs[cell.id]["max"]),
            "effect_usd_min": str(
                cell.tons * cell_coeffs[cell.id]["min"] * cell.metal_price_usd_t
            ),
            "effect_usd_max": str(
                cell.tons * cell_coeffs[cell.id]["max"] * cell.metal_price_usd_t
            ),
            "money_formula": (
                f"{cell.tons} т × {cell_coeffs[cell.id]['max']} × "
                f"{cell.metal_price_usd_t} $/т"
            ),
        }
        for cell in target_cells
    ]
    aggregate_coeff = _average([item["mean"] for item in cell_coeffs.values()], coeff)
    aggregate_coeff_min = _average([item["min"] for item in cell_coeffs.values()], coeff_min)
    aggregate_coeff_max = _average([item["max"] for item in cell_coeffs.values()], coeff_max)
    coeff_explanation = _aggregate_coeff_explanation(
        module_code,
        rule.target_cause,
        rule.target_size_class_code,
        target_cell_details,
    )

    required_kinds = rule.required_kinds
    feasible = required_kinds.issubset(module_input.equipment_kinds)
    provenance = {
        "module": module_code,
        "rule_id": rule.id,
        "rule_code": rule.code,
        "target_cause": rule.target_cause,
        "target_size_class": rule.target_size_class_code,
        "coeff": str(aggregate_coeff),
        "coeff_min": str(aggregate_coeff_min),
        "coeff_max": str(aggregate_coeff_max),
        "base_coeff": str(coeff),
        "base_coeff_min": str(coeff_min),
        "base_coeff_max": str(coeff_max),
        "coeff_model": "lognormal_size_curve_x_liberation",
        "coeff_explanation": coeff_explanation,
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


def _curve_coefficients(
    rule,
    module_code: str,
    cell: LossCellData,
    coeff_min: Decimal,
    coeff: Decimal,
    coeff_max: Decimal,
) -> dict:
    """Дискретная проекция кривой извлечения B(d) * L(lambda) на класс крупности.

    Это не first-principles предсказание; это технологически осмысленная форма,
    калиброванная экспертным диапазоном правила. Поэтому результат всегда зажат
    внутри coeff_min/coeff_max, а форма кривой только распределяет коэффициент
    по классам крупности и раскрытию.
    """
    d = _representative_microns(cell)
    size_factor = _lognormal_size_factor(d, module_code)
    liberation = _liberation_factor(cell.loss_cause, cell.mineral_form_code)
    module_factor = _module_factor(module_code, cell.loss_cause)
    curve_factor = Decimal(str(size_factor * liberation * module_factor))
    curve_factor = max(Decimal("0.15"), min(Decimal("1.20"), curve_factor))

    mean = coeff_min + (coeff_max - coeff_min) * curve_factor
    # Экспертный coeff остаётся якорем калибровки: не даём кривой улететь слишком
    # далеко от текущего правила при синтетических данных.
    mean = (mean + coeff) / Decimal("2")
    mean = max(coeff_min, min(coeff_max, mean))
    span = max(coeff_max - coeff_min, Decimal("0"))
    low = max(coeff_min, mean - span * Decimal("0.25"))
    high = min(coeff_max, mean + span * Decimal("0.25"))

    return {
        "min": low.quantize(Decimal("0.0001")),
        "mean": mean.quantize(Decimal("0.0001")),
        "max": high.quantize(Decimal("0.0001")),
        "factor": curve_factor.quantize(Decimal("0.0001")),
        "explanation": (
            f"класс {cell.size_class_code} (~{d:.0f} мкм), причина {cell.loss_cause}: "
            f"лог-нормальная кривая крупности × раскрытие даёт фактор "
            f"{curve_factor.quantize(Decimal('0.0001'))}"
        ),
    }


def _representative_microns(cell: LossCellData) -> float:
    if cell.microns_lo is not None and cell.microns_hi is not None:
        return float((cell.microns_lo + cell.microns_hi) / Decimal("2"))
    if cell.microns_lo is not None:
        return float(cell.microns_lo * Decimal("1.35"))
    if cell.microns_hi is not None:
        return float(cell.microns_hi * Decimal("0.65"))
    return 65.0


def _lognormal_size_factor(d: float, module_code: str) -> float:
    peaks = {
        "regrind": 95.0,
        "classification": 55.0,
        "fine_flotation": 25.0,
    }
    peak = peaks.get(module_code, 65.0)
    sigma = 0.85
    value = math.exp(-((math.log(max(d, 1.0)) - math.log(peak)) ** 2) / (2 * sigma**2))
    return max(0.0, min(1.0, value))


def _liberation_factor(loss_cause: str, mineral_form_code: str) -> float:
    if loss_cause == "free":
        return 0.95
    if loss_cause == "locked":
        return 0.70
    if mineral_form_code == "silicate_valleriite":
        return 0.20
    return 0.55


def _module_factor(module_code: str, loss_cause: str) -> float:
    if module_code == "regrind" and loss_cause == "locked":
        return 1.10
    if module_code == "classification" and loss_cause == "free":
        return 1.00
    if module_code == "fine_flotation" and loss_cause == "dispersed":
        return 0.95
    return 0.75


def _average(values: list[Decimal], fallback: Decimal) -> Decimal:
    if not values:
        return fallback
    return (sum(values, Decimal("0")) / Decimal(len(values))).quantize(Decimal("0.0001"))


def _aggregate_coeff_explanation(
    module_code: str,
    target_cause: str,
    target_size_class: str | None,
    cells: list[dict],
) -> str:
    if not cells:
        return "правило не попало ни в одну ячейку матрицы; эффект равен нулю"
    size = target_size_class or "все подходящие классы"
    return (
        f"{module_code}: причина {target_cause}, класс {size}; коэффициент получен "
        "усреднением лог-нормальной кривой извлечения по крупности и фактора раскрытия "
        f"по {len(cells)} целевым ячейкам."
    )
