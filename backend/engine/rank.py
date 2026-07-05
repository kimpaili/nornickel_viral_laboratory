from decimal import Decimal

from sqlalchemy.orm import Session

from backend import models
from backend import schemas
from backend.config import get_settings


def rebuild_ranking(session: Session, plant_id: int) -> list[models.Evaluation]:
    evaluations = _latest_evaluations(session, plant_id)
    _, _, conflicts, conflict_tons = build_coverage(session, plant_id)
    for evaluation in evaluations:
        score, breakdown = _extended_score(
            evaluation,
            conflicts=conflicts,
            conflict_tons=conflict_tons,
        )
        evaluation.relevance_score = score
        provenance = dict(evaluation.provenance or {})
        provenance["score_breakdown"] = {key: str(value) for key, value in breakdown.items()}
        provenance["expected_effect_usd"] = str(breakdown["expected_effect_usd"])
        provenance["success_probability"] = str(breakdown["success_probability"])
        provenance["risk_score"] = str(breakdown["risk_score"])
        provenance["coverage_contribution"] = str(breakdown["coverage_contribution"])
        provenance["conflict_penalty"] = str(breakdown["conflict_penalty"])
        provenance["score_formula"] = (
            "w_usd*norm(E[U]) + w_tons*norm(T) + w_prob*P(U>threshold) "
            "+ w_cov*DeltaCoverage + feasible - risk - dead_end - conflict"
        )
        for report in provenance.get("module_reports", []):
            if report.get("selected"):
                report["expected_effect_usd"] = str(breakdown["expected_effect_usd"])
                report["success_probability"] = str(breakdown["success_probability"])
                report["risk_penalty"] = str(breakdown["risk_score"])
                report["coverage_contribution"] = str(breakdown["coverage_contribution"])
                report["conflict_penalty"] = str(breakdown["conflict_penalty"])
                report["score_breakdown"] = {key: str(value) for key, value in breakdown.items()}
                report["relevance_contribution"] = str(score)
        evaluation.provenance = provenance

    evaluations.sort(
        key=lambda item: (
            bool(item.feasible),
            item.relevance_score or Decimal("0"),
            item.effect_usd_max or Decimal("0"),
        ),
        reverse=True,
    )
    for index, evaluation in enumerate(evaluations, start=1):
        evaluation.rank = index
    session.flush()
    return evaluations


def list_ranking(session: Session, plant_id: int) -> list[models.Evaluation]:
    evaluations = _latest_evaluations(session, plant_id)
    return sorted(evaluations, key=lambda item: item.rank or 10**9)


def build_coverage(
    session: Session,
    plant_id: int,
) -> tuple[
    schemas.CoverageSummary,
    list[schemas.CoverageCell],
    dict[int, list[int]],
    dict[int, Decimal],
]:
    cells = session.query(models.LossCell).filter_by(plant_id=plant_id).all()
    evaluations = [
        item
        for item in _latest_evaluations(session, plant_id)
        if item.feasible and not item.dead_end_flag
    ]

    by_cell: dict[int, dict] = {
        cell.id: {
            "claimed": Decimal("0"),
            "hypotheses": set(),
        }
        for cell in cells
    }
    for evaluation in evaluations:
        provenance = evaluation.provenance or {}
        for target in provenance.get("target_cells", []):
            cell_id = int(target["id"])
            if cell_id not in by_cell:
                continue
            by_cell[cell_id]["claimed"] += Decimal(str(target.get("effect_tons_max", "0")))
            by_cell[cell_id]["hypotheses"].add(evaluation.hypothesis_id)

    coverage_cells: list[schemas.CoverageCell] = []
    conflicts: dict[int, set[int]] = {}
    conflict_tons: dict[int, Decimal] = {}
    total_recoverable = Decimal("0")
    total_covered = Decimal("0")
    for cell in cells:
        if cell.mineral_form.recoverable:
            total_recoverable += cell.tons

        claimed = by_cell[cell.id]["claimed"]
        hypotheses = by_cell[cell.id]["hypotheses"]
        # Масс-баланс: реально «покрыть» ячейку можно максимум на её тоннаж —
        # эффекты гипотез не складываются сверх извлекаемой фракции.
        covered = min(claimed, cell.tons)
        total_covered += covered
        # Ячейка «оспариваемая», если в неё бьют минимум две гипотезы и их суммарный
        # заявленный эффект превышает тоннаж ячейки — значит эффекты не сложатся.
        contested = len(hypotheses) > 1 and claimed > cell.tons
        if contested:
            for hypothesis_id in hypotheses:
                conflicts.setdefault(hypothesis_id, set()).update(hypotheses - {hypothesis_id})
                conflict_tons[hypothesis_id] = conflict_tons.get(hypothesis_id, Decimal("0")) + cell.tons

        share = Decimal("0") if cell.tons == 0 else covered / cell.tons
        coverage_cells.append(
            schemas.CoverageCell(
                cell_id=cell.id,
                metal_code=cell.metal.code,
                size_class_code=cell.size_class.code,
                mineral_form_code=cell.mineral_form.code,
                loss_cause=cell.mineral_form.loss_cause,
                tons=cell.tons,
                claimed_effect_tons_max=claimed,
                covered_effect_tons_max=covered,
                coverage_share=share,
                contested=contested,
                delta_coverage_tons=covered,
                covered_by_hypotheses=sorted(hypotheses),
            )
        )

    total_share = Decimal("0") if total_recoverable == 0 else total_covered / total_recoverable
    return (
        schemas.CoverageSummary(
            total_recoverable_tons=total_recoverable,
            covered_effect_tons_max=total_covered,
            coverage_share=total_share,
        ),
        coverage_cells,
        {hypothesis_id: sorted(peers) for hypothesis_id, peers in conflicts.items()},
        conflict_tons,
    )


def _latest_evaluations(session: Session, plant_id: int) -> list[models.Evaluation]:
    hypotheses = (
        session.query(models.Hypothesis)
        .filter(models.Hypothesis.plant_id == plant_id, models.Hypothesis.status != "rejected")
        .all()
    )
    result: list[models.Evaluation] = []
    for hypothesis in hypotheses:
        if not hypothesis.evaluations:
            continue
        result.append(max(hypothesis.evaluations, key=lambda item: item.id))
    return result


def _extended_score(
    evaluation: models.Evaluation,
    *,
    conflicts: dict[int, list[int]],
    conflict_tons: dict[int, Decimal],
) -> tuple[Decimal, dict[str, Decimal]]:
    settings = get_settings()
    effect_usd_min = evaluation.effect_usd_min or Decimal("0")
    effect_usd_max = evaluation.effect_usd_max or Decimal("0")
    effect_tons_max = evaluation.effect_tons_max or Decimal("0")
    expected_usd = (effect_usd_min + effect_usd_max) / Decimal("2")
    spread = max(effect_usd_max - effect_usd_min, Decimal("0"))
    risk = Decimal("0") if effect_usd_max <= 0 else min(spread / effect_usd_max, Decimal("1"))
    success_probability = max(
        Decimal("0.05"),
        min(Decimal("0.95"), Decimal("1") - risk / Decimal("2")),
    )
    coverage_contribution = _coverage_contribution(evaluation)
    conflict_penalty = Decimal("0")
    if conflicts.get(evaluation.hypothesis_id):
        conflict_penalty = min(
            conflict_tons.get(evaluation.hypothesis_id, Decimal("0")) / Decimal("100"),
            Decimal("1"),
        )

    usd_component = settings.score_weight_usd * min(expected_usd / Decimal("1000000"), Decimal("1"))
    tons_component = settings.score_weight_tons * min(effect_tons_max / Decimal("100"), Decimal("1"))
    probability_component = settings.score_weight_probability * success_probability
    coverage_component = settings.score_weight_coverage * min(
        coverage_contribution / Decimal("100"),
        Decimal("1"),
    )
    feasible_component = settings.score_weight_feasible if evaluation.feasible else Decimal("0")
    risk_component = settings.score_weight_risk * risk
    dead_end_component = settings.score_weight_dead_end if evaluation.dead_end_flag else Decimal("0")
    conflict_component = settings.score_weight_conflict * conflict_penalty

    score = (
        usd_component
        + tons_component
        + probability_component
        + coverage_component
        + feasible_component
        - risk_component
        - dead_end_component
        - conflict_component
    )
    score = max(score, Decimal("0")).quantize(Decimal("0.0001"))
    return score, {
        "expected_effect_usd": expected_usd.quantize(Decimal("0.01")),
        "success_probability": success_probability.quantize(Decimal("0.0001")),
        "risk_score": risk.quantize(Decimal("0.0001")),
        "coverage_contribution": coverage_contribution.quantize(Decimal("0.0001")),
        "conflict_penalty": conflict_penalty.quantize(Decimal("0.0001")),
        "usd_component": usd_component.quantize(Decimal("0.0001")),
        "tons_component": tons_component.quantize(Decimal("0.0001")),
        "probability_component": probability_component.quantize(Decimal("0.0001")),
        "coverage_component": coverage_component.quantize(Decimal("0.0001")),
        "feasible_component": feasible_component.quantize(Decimal("0.0001")),
        "risk_component": risk_component.quantize(Decimal("0.0001")),
        "dead_end_component": dead_end_component.quantize(Decimal("0.0001")),
        "conflict_component": conflict_component.quantize(Decimal("0.0001")),
    }


def _coverage_contribution(evaluation: models.Evaluation) -> Decimal:
    provenance = evaluation.provenance or {}
    total = Decimal("0")
    for target in provenance.get("target_cells", []):
        tons = Decimal(str(target.get("tons", "0")))
        effect = Decimal(str(target.get("effect_tons_max", "0")))
        total += min(tons, effect)
    return total
