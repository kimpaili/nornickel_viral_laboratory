from decimal import Decimal

from sqlalchemy.orm import Session

from backend import models
from backend import schemas


def rebuild_ranking(session: Session, plant_id: int) -> list[models.Evaluation]:
    evaluations = _latest_evaluations(session, plant_id)
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
) -> tuple[schemas.CoverageSummary, list[schemas.CoverageCell], dict[int, list[int]]]:
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
