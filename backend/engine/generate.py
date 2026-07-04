from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend import models


def generate_hypotheses(session: Session, plant_id: int, limit: int = 5) -> list[models.Hypothesis]:
    cells = (
        session.query(models.LossCell)
        .filter_by(plant_id=plant_id)
        .order_by(models.LossCell.tons.desc())
        .all()
    )
    created: list[models.Hypothesis] = []

    for cell in cells:
        if len(created) >= limit:
            break
        if not cell.mineral_form.recoverable:
            continue

        rule = _best_rule_for_cell(session, cell)
        if not rule or _is_dead_end(session, rule):
            continue

        title = (
            f"{rule.module.title}: потери {cell.metal.code} "
            f"в классе {cell.size_class.code}, форма «{cell.mineral_form.title}»"
        )
        existing = (
            session.query(models.Hypothesis)
            .filter_by(plant_id=plant_id, title=title)
            .one_or_none()
        )
        if existing:
            continue

        hypothesis = models.Hypothesis(
            plant_id=plant_id,
            module_id=rule.module_id,
            title=title,
            origin="generated",
        )
        session.add(hypothesis)
        created.append(hypothesis)

    session.flush()
    return created


def count_dead_end_candidates(session: Session, plant_id: int) -> int:
    count = 0
    cells = session.query(models.LossCell).filter_by(plant_id=plant_id).all()
    for cell in cells:
        if not cell.mineral_form.recoverable:
            continue
        rule = _best_rule_for_cell(session, cell)
        if rule and _is_dead_end(session, rule):
            count += 1
    return count


def _best_rule_for_cell(session: Session, cell: models.LossCell) -> models.Rule | None:
    return (
        session.query(models.Rule)
        .join(models.Module)
        .filter(models.Rule.target_cause == cell.mineral_form.loss_cause)
        .filter(
            or_(
                models.Rule.target_size_class_id == cell.size_class_id,
                models.Rule.target_size_class_id.is_(None),
            )
        )
        .order_by(models.Rule.target_size_class_id.is_(None), models.Rule.coeff.desc())
        .first()
    )


def _is_dead_end(session: Session, rule: models.Rule) -> bool:
    return (
        session.query(models.DeadEnd)
        .filter_by(
            module_id=rule.module_id,
            target_cause=rule.target_cause,
            size_class_id=rule.target_size_class_id,
        )
        .first()
        is not None
    )
