from decimal import Decimal

from sqlalchemy.orm import Session

from backend import models
from backend.engine import rank


DEFAULT_BUDGET = Decimal("250000")


def greedy_plan(
    session: Session,
    plant_id: int,
    *,
    budget: Decimal = DEFAULT_BUDGET,
    limit: int = 3,
) -> dict:
    evaluations = [
        item
        for item in rank.list_ranking(session, plant_id)
        if item.feasible and not item.dead_end_flag
    ]
    selected: list[dict] = []
    used_cells: dict[int, Decimal] = {}
    used_steps: set[str] = set()
    total_cost = Decimal("0")
    total_effect_usd = Decimal("0")
    total_effect_tons = Decimal("0")

    while len(selected) < limit:
        best = None
        for evaluation in evaluations:
            if any(item["hypothesis_id"] == evaluation.hypothesis_id for item in selected):
                continue
            marginal_tons, marginal_usd = _marginal_effect(evaluation, used_cells)
            if marginal_usd <= 0:
                continue
            step_keys = _roadmap_keys(session, evaluation.hypothesis_id)
            cost = _portfolio_cost(session, evaluation.hypothesis_id, used_steps)
            if total_cost + cost > budget:
                continue
            ratio = marginal_usd / max(cost, Decimal("1"))
            candidate = {
                "evaluation": evaluation,
                "marginal_effect_tons": marginal_tons,
                "marginal_effect_usd": marginal_usd,
                "cost": cost,
                "ratio": ratio,
                "shared_steps": sorted(step_keys & used_steps),
            }
            if best is None or ratio > best["ratio"]:
                best = candidate
        if best is None:
            break

        evaluation = best["evaluation"]
        _apply_cells(evaluation, used_cells)
        used_steps.update(_roadmap_keys(session, evaluation.hypothesis_id))
        total_cost += best["cost"]
        total_effect_usd += best["marginal_effect_usd"]
        total_effect_tons += best["marginal_effect_tons"]
        selected.append(
            {
                "hypothesis_id": evaluation.hypothesis_id,
                "title": evaluation.hypothesis.title,
                "marginal_effect_usd": best["marginal_effect_usd"].quantize(Decimal("0.01")),
                "marginal_effect_tons": best["marginal_effect_tons"].quantize(Decimal("0.0001")),
                "cost": best["cost"],
                "ratio": best["ratio"].quantize(Decimal("0.0001")),
                "shared_steps": best["shared_steps"],
            }
        )

    return {
        "plant_id": plant_id,
        "budget": budget,
        "selected": selected,
        "total_effect_usd": total_effect_usd.quantize(Decimal("0.01")),
        "total_effect_tons": total_effect_tons.quantize(Decimal("0.0001")),
        "total_cost": total_cost,
    }


def _marginal_effect(
    evaluation: models.Evaluation,
    used_cells: dict[int, Decimal],
) -> tuple[Decimal, Decimal]:
    tons = Decimal("0")
    usd = Decimal("0")
    for target in (evaluation.provenance or {}).get("target_cells", []):
        cell_tons = Decimal(str(target.get("tons", "0")))
        already = used_cells.get(int(target["id"]), Decimal("0"))
        room = max(cell_tons - already, Decimal("0"))
        effect_tons = min(Decimal(str(target.get("effect_tons_max", "0"))), room)
        if effect_tons <= 0:
            continue
        max_effect = Decimal(str(target.get("effect_tons_max", "0")))
        max_usd = Decimal(str(target.get("effect_usd_max", "0")))
        usd += Decimal("0") if max_effect <= 0 else max_usd * effect_tons / max_effect
        tons += effect_tons
    return tons, usd


def _apply_cells(evaluation: models.Evaluation, used_cells: dict[int, Decimal]) -> None:
    for target in (evaluation.provenance or {}).get("target_cells", []):
        cell_id = int(target["id"])
        cell_tons = Decimal(str(target.get("tons", "0")))
        effect = Decimal(str(target.get("effect_tons_max", "0")))
        used_cells[cell_id] = min(cell_tons, used_cells.get(cell_id, Decimal("0")) + effect)


def _portfolio_cost(session: Session, hypothesis_id: int, used_steps: set[str]) -> Decimal:
    steps = _roadmap_steps(session, hypothesis_id)
    total = Decimal("0")
    for step in steps:
        if step.shared_key and step.shared_key in used_steps:
            continue
        total += step.cost or Decimal("0")
    return total


def _roadmap_keys(session: Session, hypothesis_id: int) -> set[str]:
    return {step.shared_key for step in _roadmap_steps(session, hypothesis_id) if step.shared_key}


def _roadmap_steps(session: Session, hypothesis_id: int) -> list[models.RoadmapStep]:
    return (
        session.query(models.RoadmapStep)
        .filter_by(hypothesis_id=hypothesis_id)
        .order_by(models.RoadmapStep.step_order)
        .all()
    )
