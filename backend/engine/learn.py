from decimal import Decimal

from sqlalchemy.orm import Session

from backend import models


def apply_artifact(
    session: Session,
    artifact: models.ExperimentArtifact,
) -> tuple[list[models.RuleCalibration], models.DeadEnd | None]:
    evaluation = _latest_evaluation(session, artifact.hypothesis_id)
    if not evaluation or not evaluation.rule:
        return [], None

    rule = evaluation.rule
    before = rule.coeff
    prior_count = (
        session.query(models.RuleCalibration).filter_by(rule_id=rule.id).count()
    )
    after = _next_coeff(rule, artifact, before, prior_count)
    calibration = models.RuleCalibration(
        rule_id=rule.id,
        artifact_id=artifact.id,
        coeff_before=before,
        coeff_after=after,
    )
    rule.coeff = after
    session.add(calibration)

    dead_end = None
    if artifact.outcome == "failure":
        dead_end = _get_or_create_dead_end(session, rule, artifact)
        _reject_matching_hypotheses(session, artifact.hypothesis.plant_id, rule)

    session.flush()
    return [calibration], dead_end


def _get_or_create_dead_end(
    session: Session,
    rule: models.Rule,
    artifact: models.ExperimentArtifact,
) -> models.DeadEnd:
    existing = (
        session.query(models.DeadEnd)
        .filter_by(
            module_id=rule.module_id,
            target_cause=rule.target_cause,
            size_class_id=rule.target_size_class_id,
        )
        .one_or_none()
    )
    if existing:
        return existing

    dead_end = models.DeadEnd(
        module_id=rule.module_id,
        target_cause=rule.target_cause,
        size_class_id=rule.target_size_class_id,
        reason=artifact.note or "Experiment artifact marked this hypothesis as failure",
        source_artifact_id=artifact.id,
    )
    session.add(dead_end)
    session.flush()
    return dead_end


def _reject_matching_hypotheses(
    session: Session,
    plant_id: int,
    rule: models.Rule,
) -> None:
    evaluations = (
        session.query(models.Evaluation)
        .join(models.Hypothesis)
        .filter(
            models.Hypothesis.plant_id == plant_id,
            models.Evaluation.rule_id == rule.id,
        )
        .all()
    )
    for evaluation in evaluations:
        evaluation.hypothesis.status = "rejected"
        evaluation.dead_end_flag = True


def _latest_evaluation(session: Session, hypothesis_id: int) -> models.Evaluation | None:
    return (
        session.query(models.Evaluation)
        .filter_by(hypothesis_id=hypothesis_id)
        .order_by(models.Evaluation.id.desc())
        .first()
    )


def _next_coeff(
    rule: models.Rule,
    artifact: models.ExperimentArtifact,
    before: Decimal,
    prior_count: int,
) -> Decimal:
    """Взвешенная калибровка коэффициента правила.

    Новый замер входит как статистическое обновление:
        k_next = k + eta * (observed - k), eta = 1 / sqrt(N + 1).

    Чем больше пилотов уже накоплено по правилу, тем меньше шаг единичного замера.
    Провал — исключение из этой осторожности: он не сглаживается, а сразу тянет
    коэффициент к нижней границе диапазона и создаёт переносимый тупик.
    """
    low = rule.coeff_min if rule.coeff_min is not None else Decimal("0")
    high = rule.coeff_max if rule.coeff_max is not None else Decimal("1")

    if artifact.outcome == "failure":
        candidate = low
    else:
        observed = _observed_coeff(artifact, before)
        eta = Decimal("1") / Decimal(prior_count + 1).sqrt()
        candidate = before + eta * (observed - before)

    return max(low, min(high, candidate))


def _observed_coeff(
    artifact: models.ExperimentArtifact,
    fallback: Decimal,
) -> Decimal:
    measured = artifact.measured_value
    if measured is None:
        return fallback
    if Decimal("0") <= measured <= Decimal("1"):
        return measured
    if artifact.predicted_max and artifact.predicted_max > 0:
        return fallback * measured / artifact.predicted_max
    return fallback
