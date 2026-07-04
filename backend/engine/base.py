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
        provenance=best.provenance,
        dead_end_flag=dead_end_flag,
    )
    hypothesis.status = "evaluated"
    session.add(evaluation)
    session.flush()
    return evaluation


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
