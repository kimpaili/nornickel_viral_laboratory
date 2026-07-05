from decimal import Decimal

from sqlalchemy.orm import Session

from backend import models
from backend.engine import mod_classification, mod_fine_flotation, mod_regrind
from backend.engine.contract import LossCellData, ModuleInput, RuleData, ModuleVerdict


RUNNERS = {
    "regrind": mod_regrind.evaluate,
    "classification": mod_classification.evaluate,
    "fine_flotation": mod_fine_flotation.evaluate,
}


def _rule_priority(pair: tuple[models.Rule, ModuleVerdict]) -> tuple:
    """Порядок выбора правила под гипотезу — реализация принципа концепта
    «приоритет у более специфичного правила, а не у того, что даёт больший эффект».

    Сравниваем по убыванию значимости:
      1. feasible      — правило, требующее отсутствующего оборудования, не выигрывает;
      2. has_effect    — правило, не попавшее ни в одну ячейку матрицы, не выигрывает
                         только за счёт своей специфичности (иначе точечное правило с
                         нулём тонн обошло бы общее правило с реальным эффектом);
      3. specificity   — привязанное к классу крупности правило важнее правила «на любой
                         класс»: движок выбирает точную оценку, а не красивую цифру;
      4. effect_usd/tons — эффект лишь разрешает ничьи внутри одной специфичности.
    """
    rule, verdict = pair
    specificity = 1 if rule.target_size_class_id is not None else 0
    has_effect = verdict.effect_tons[1] > Decimal("0")
    return (
        bool(verdict.feasible),
        has_effect,
        specificity,
        verdict.effect_usd[1],
        verdict.effect_tons[1],
    )


def evaluate_plant(session: Session, plant_id: int) -> list[models.Evaluation]:
    hypotheses = (
        session.query(models.Hypothesis)
        .filter(models.Hypothesis.plant_id == plant_id, models.Hypothesis.status != "rejected")
        .all()
    )
    return [evaluate_hypothesis(session, hypothesis) for hypothesis in hypotheses]


def evaluate_hypothesis(
    session: Session,
    hypothesis: models.Hypothesis,
) -> models.Evaluation:
    rules = _rules_for_hypothesis(session, hypothesis)
    pairs = [
        (rule, _run_rule(session, hypothesis.plant_id, rule))
        for rule in rules
        if rule.module.code in RUNNERS
    ]
    if not pairs:
        raise ValueError(f"No runnable rules for hypothesis {hypothesis.id}")

    best_rule, best = max(pairs, key=_rule_priority)
    target_metal_id = _first_target_metal_id(session, hypothesis.plant_id, best.target_cells)
    dead_end_flag = _matches_dead_end(session, best_rule)

    module_reports = _module_reports(session, pairs, best_rule.id)
    provenance = dict(best.provenance)
    provenance["module_reports"] = module_reports

    session.query(models.Evaluation).filter_by(hypothesis_id=hypothesis.id).delete()
    evaluation = models.Evaluation(
        hypothesis_id=hypothesis.id,
        rule_id=best_rule.id,
        target_metal_id=target_metal_id,
        effect_tons_min=best.effect_tons[0],
        effect_tons_max=best.effect_tons[1],
        effect_usd_min=best.effect_usd[0],
        effect_usd_max=best.effect_usd[1],
        feasible=best.feasible,
        relevance_score=_relevance_score(best),
        provenance=provenance,
        dead_end_flag=dead_end_flag,
    )
    hypothesis.status = "evaluated"
    session.add(evaluation)
    session.flush()
    return evaluation


def _module_reports(
    session: Session,
    pairs: list[tuple[models.Rule, ModuleVerdict]],
    selected_rule_id: int,
) -> list[dict]:
    """Мини-отчёт по каждому модулю — «главная фича» этапа (прозрачность движка).

    По каждому модулю берём лучшее из его правил (тот же критерий специфичности,
    что и в общем выборе) и раскрываем его логику: на какие ячейки бил, какое
    правило и коэффициент применил, какие числа фабрики взял и что на выходе.
    Приоритет гипотезы виден как сумма вкладов модулей (`relevance_contribution`).
    Один модуль помечается `selected` — именно он даёт итоговый эффект гипотезы.
    """
    best_per_module: dict[str, tuple[models.Rule, ModuleVerdict]] = {}
    for rule, verdict in pairs:
        code = rule.module.code
        current = best_per_module.get(code)
        if current is None or _rule_priority((rule, verdict)) > _rule_priority(current):
            best_per_module[code] = (rule, verdict)

    reports: list[dict] = []
    for code, (rule, verdict) in best_per_module.items():
        prov = verdict.provenance
        score_breakdown = _score_breakdown(verdict)
        reports.append(
            {
                "module_code": code,
                "module_title": rule.module.title,
                "selected": rule.id == selected_rule_id,
                "rule_id": rule.id,
                "rule_code": rule.code,
                "target_cause": rule.target_cause,
                "target_size_class": prov.get("target_size_class"),
                "coeff": prov.get("coeff"),
                "coeff_min": prov.get("coeff_min"),
                "coeff_max": prov.get("coeff_max"),
                "coeff_explanation": prov.get("coeff_explanation"),
                "feasible": verdict.feasible,
                "required_equipment": prov.get("required_equipment", []),
                "plant_equipment": prov.get("plant_equipment", []),
                "side_effect": verdict.side_effect,
                "effect_tons_min": str(verdict.effect_tons[0]),
                "effect_tons_max": str(verdict.effect_tons[1]),
                "effect_usd_min": str(verdict.effect_usd[0]),
                "effect_usd_max": str(verdict.effect_usd[1]),
                "relevance_contribution": str(_relevance_score(verdict)),
                "expected_effect_usd": str(score_breakdown["expected_effect_usd"]),
                "success_probability": str(score_breakdown["success_probability"]),
                "risk_penalty": str(score_breakdown["risk_penalty"]),
                "score_breakdown": {key: str(value) for key, value in score_breakdown.items()},
                "selection_reason": _selection_reason(rule, verdict, rule.id == selected_rule_id),
                "money_formula": _money_formula(prov.get("target_cells", [])),
                "target_cells": prov.get("target_cells", []),
                "source": prov.get("source"),
            }
        )
    # Выбранный модуль — первым, остальные по убыванию вклада.
    reports.sort(
        key=lambda item: (item["selected"], Decimal(item["relevance_contribution"])),
        reverse=True,
    )
    return reports


def _rules_for_hypothesis(
    session: Session,
    hypothesis: models.Hypothesis,
) -> list[models.Rule]:
    query = session.query(models.Rule).join(models.Module)
    if hypothesis.module_id:
        query = query.filter(models.Rule.module_id == hypothesis.module_id)

    target_size = _target_size_from_generated_title(session, hypothesis)
    if target_size:
        specific_rules = (
            query.filter(models.Rule.target_size_class_id == target_size.id)
            .order_by(models.Rule.id)
            .all()
        )
        if specific_rules:
            return specific_rules
        query = query.filter(models.Rule.target_size_class_id.is_(None))

    return query.order_by(models.Rule.id).all()


def _target_size_from_generated_title(
    session: Session,
    hypothesis: models.Hypothesis,
) -> models.SizeClass | None:
    marker = " в классе "
    if hypothesis.origin != "generated" or marker not in hypothesis.title:
        return None
    size_code = hypothesis.title.split(marker, 1)[1].split(",", 1)[0].strip()
    return session.query(models.SizeClass).filter_by(code=size_code).one_or_none()


def _run_rule(session: Session, plant_id: int, rule: models.Rule) -> ModuleVerdict:
    module_input = ModuleInput(
        loss_cells=_loss_cells(session, plant_id),
        rule=_rule_data(rule),
        equipment_kinds=_equipment_kinds(session, plant_id),
        metal_prices=_metal_prices(session),
    )
    return RUNNERS[rule.module.code](module_input)


def _loss_cells(session: Session, plant_id: int) -> list[LossCellData]:
    cells = session.query(models.LossCell).filter_by(plant_id=plant_id).all()
    return [
        LossCellData(
            id=cell.id,
            plant_id=cell.plant_id,
            metal_id=cell.metal_id,
            metal_code=cell.metal.code,
            metal_price_usd_t=cell.metal.price_usd_t or Decimal("0"),
            size_class_id=cell.size_class_id,
            size_class_code=cell.size_class.code,
            microns_lo=cell.size_class.microns_lo,
            microns_hi=cell.size_class.microns_hi,
            mineral_form_id=cell.mineral_form_id,
            mineral_form_code=cell.mineral_form.code,
            loss_cause=cell.mineral_form.loss_cause,
            recoverable=cell.mineral_form.recoverable,
            tons=cell.tons,
        )
        for cell in cells
    ]


def _rule_data(rule: models.Rule) -> RuleData:
    return RuleData(
        id=rule.id,
        code=rule.code,
        module_code=rule.module.code,
        target_cause=rule.target_cause,
        target_size_class_id=rule.target_size_class_id,
        target_size_class_code=rule.target_size_class.code if rule.target_size_class else None,
        coeff=rule.coeff,
        coeff_min=rule.coeff_min,
        coeff_max=rule.coeff_max,
        side_effect=rule.side_effect,
        requires_kind=rule.requires_kind,
        source=rule.source,
    )


def _equipment_kinds(session: Session, plant_id: int) -> set[str]:
    equipment = session.query(models.Equipment).filter_by(plant_id=plant_id).all()
    return {item.kind for item in equipment}


def _metal_prices(session: Session) -> dict[str, Decimal]:
    metals = session.query(models.Metal).all()
    return {metal.code: metal.price_usd_t or Decimal("0") for metal in metals}


def _first_target_metal_id(
    session: Session,
    plant_id: int,
    target_cell_ids: list[int],
) -> int | None:
    if not target_cell_ids:
        return None
    cell = (
        session.query(models.LossCell)
        .filter(models.LossCell.plant_id == plant_id, models.LossCell.id == target_cell_ids[0])
        .one_or_none()
    )
    return cell.metal_id if cell else None


def _matches_dead_end(session: Session, rule: models.Rule) -> bool:
    return (
        session.query(models.DeadEnd)
        .filter(
            models.DeadEnd.module_id == rule.module_id,
            models.DeadEnd.target_cause == rule.target_cause,
            models.DeadEnd.size_class_id == rule.target_size_class_id,
        )
        .first()
        is not None
    )


def _relevance_score(verdict: ModuleVerdict) -> Decimal:
    money_score = min(verdict.effect_usd[1] / Decimal("100000"), Decimal("70"))
    tons_score = min(verdict.effect_tons[1], Decimal("20"))
    feasibility_bonus = Decimal("10") if verdict.feasible else Decimal("0")
    return money_score + tons_score + feasibility_bonus


def _score_breakdown(verdict: ModuleVerdict) -> dict[str, Decimal]:
    expected_usd = (verdict.effect_usd[0] + verdict.effect_usd[1]) / Decimal("2")
    spread = max(verdict.effect_usd[1] - verdict.effect_usd[0], Decimal("0"))
    denom = max(verdict.effect_usd[1], Decimal("1"))
    risk = min(spread / denom, Decimal("1"))
    probability = max(Decimal("0.05"), min(Decimal("0.95"), Decimal("1") - risk / Decimal("2")))
    return {
        "expected_effect_usd": expected_usd.quantize(Decimal("0.01")),
        "success_probability": probability.quantize(Decimal("0.0001")),
        "risk_penalty": risk.quantize(Decimal("0.0001")),
        "usd_component": min(verdict.effect_usd[1] / Decimal("100000"), Decimal("70")),
        "tons_component": min(verdict.effect_tons[1], Decimal("20")),
        "feasible_component": Decimal("10") if verdict.feasible else Decimal("0"),
    }


def _selection_reason(rule: models.Rule, verdict: ModuleVerdict, selected: bool) -> str:
    specificity = "специфичное правило по классу" if rule.target_size_class_id else "общее правило"
    hit = "попало в целевые ячейки" if verdict.effect_tons[1] > 0 else "не попало в ячейки матрицы"
    feasible = "оборудование есть" if verdict.feasible else "нет нужного оборудования"
    if selected:
        return f"модуль выбран: {specificity}, {hit}, {feasible}, лучший вклад среди правил модуля"
    return f"модуль рассмотрен, но не выбран итоговым: {specificity}, {hit}, {feasible}"


def _money_formula(target_cells: list[dict]) -> str:
    if not target_cells:
        return "нет целевых ячеек, денежный эффект равен 0"
    first = target_cells[0]
    return (
        "по каждой ячейке: потери, т × коэффициент кривой × цена металла; "
        f"пример: {first.get('money_formula', 'тонны × coeff × цена')}"
    )
