from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from . import ingest, llm, models, schemas
from .config import get_settings
from .db import get_session, init_db
from .engine import base, generate, learn, rank
from .rag import literature, ollama_client, retriever
from .rag.indexer import index_corpus


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Factory of Hypotheses MVP",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=schemas.HealthResponse)
def health() -> schemas.HealthResponse:
    settings = get_settings()
    return schemas.HealthResponse(
        status="ok",
        llm_provider="ollama",
        llm_model=settings.ollama_chat_model,
    )


@app.get("/plants", response_model=list[schemas.PlantRead])
def list_plants(session: Session = Depends(get_session)) -> list[schemas.PlantRead]:
    return session.query(models.Plant).order_by(models.Plant.id).all()


@app.post("/plants/ingest", response_model=schemas.IngestResponse)
def ingest_plant_xlsx(
    upload: UploadFile = File(...),
    plant_id: int | None = Query(None),
    plant_code: str | None = Query(None),
    plant_title: str | None = Query(None),
    session: Session = Depends(get_session),
) -> schemas.IngestResponse:
    plant = _resolve_plant(session, plant_id, plant_code, plant_title)
    try:
        rows, warnings = ingest.parse_loss_cells_from_xlsx(upload.file, upload.filename)
        count = ingest.upsert_loss_cells(session, plant_id=plant.id, rows=rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.commit()
    return schemas.IngestResponse(
        plant_id=plant.id,
        inserted_or_updated=count,
        skipped_rows=len(warnings),
        warnings=warnings,
    )


@app.get("/plants/{plant_id}/diagnosis", response_model=schemas.DiagnosisResponse)
def plant_diagnosis(
    plant_id: int,
    session: Session = Depends(get_session),
) -> schemas.DiagnosisResponse:
    plant = _plant_or_404(session, plant_id)
    cells = session.query(models.LossCell).filter_by(plant_id=plant_id).all()
    recoverable = sum(
        (cell.tons for cell in cells if cell.mineral_form.recoverable),
        Decimal("0"),
    )
    unrecoverable = sum(
        (cell.tons for cell in cells if not cell.mineral_form.recoverable),
        Decimal("0"),
    )
    return schemas.DiagnosisResponse(
        plant=plant,
        recoverable_tons=recoverable,
        unrecoverable_tons=unrecoverable,
        cells=[_loss_cell_schema(cell) for cell in cells],
        matrix=_diagnosis_matrix(cells),
    )


@app.post("/plants/{plant_id}/generate", response_model=schemas.GenerateResponse)
def generate_for_plant(
    plant_id: int,
    limit: int = Query(5, ge=1, le=20),
    session: Session = Depends(get_session),
) -> schemas.GenerateResponse:
    _plant_or_404(session, plant_id)
    hypotheses = generate.generate_hypotheses(session, plant_id, limit=limit)
    skipped_dead_ends = generate.count_dead_end_candidates(session, plant_id)
    session.commit()
    return schemas.GenerateResponse(
        created=len(hypotheses),
        hypotheses=hypotheses,
        skipped_dead_ends=skipped_dead_ends,
    )


@app.get("/plants/{plant_id}/hypotheses", response_model=list[schemas.HypothesisListItem])
def list_hypotheses(
    plant_id: int,
    include_rejected: bool = Query(False),
    session: Session = Depends(get_session),
) -> list[schemas.HypothesisListItem]:
    _plant_or_404(session, plant_id)
    query = session.query(models.Hypothesis).filter_by(plant_id=plant_id)
    if not include_rejected:
        query = query.filter(models.Hypothesis.status != "rejected")
    hypotheses = query.order_by(models.Hypothesis.id).all()
    return [_hypothesis_item(session, hypothesis) for hypothesis in hypotheses]


@app.post("/hypotheses/ingest", response_model=schemas.HypothesisRead)
def ingest_hypothesis(
    payload: schemas.HypothesisCreate,
    session: Session = Depends(get_session),
) -> schemas.HypothesisRead:
    _plant_or_404(session, payload.plant_id)
    if payload.origin not in {"expert", "generated"}:
        raise HTTPException(status_code=400, detail="origin must be expert or generated")

    module = _module_by_code(session, payload.module_code) if payload.module_code else None
    hypothesis = models.Hypothesis(
        plant_id=payload.plant_id,
        module_id=module.id if module else None,
        title=payload.title,
        origin=payload.origin,
    )
    session.add(hypothesis)
    session.commit()
    session.refresh(hypothesis)
    return hypothesis


@app.post("/plants/{plant_id}/evaluate", response_model=schemas.EvaluateResponse)
def evaluate_plant(
    plant_id: int,
    session: Session = Depends(get_session),
) -> schemas.EvaluateResponse:
    _plant_or_404(session, plant_id)
    try:
        evaluations = base.evaluate_plant(session, plant_id)
        evaluations = rank.rebuild_ranking(session, plant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.commit()
    return schemas.EvaluateResponse(evaluated=len(evaluations), evaluations=evaluations)


@app.get("/plants/{plant_id}/ranking", response_model=schemas.RankingResponse)
def plant_ranking(
    plant_id: int,
    session: Session = Depends(get_session),
) -> schemas.RankingResponse:
    _plant_or_404(session, plant_id)
    evaluations = rank.list_ranking(session, plant_id)
    coverage_summary, coverage_cells, conflicts = rank.build_coverage(session, plant_id)
    return schemas.RankingResponse(
        plant_id=plant_id,
        items=[_ranking_item(evaluation, conflicts) for evaluation in evaluations],
        coverage_summary=coverage_summary,
        coverage_cells=coverage_cells,
    )


@app.get("/plants/{plant_id}/coverage", response_model=list[schemas.CoverageCell])
def plant_coverage(
    plant_id: int,
    session: Session = Depends(get_session),
) -> list[schemas.CoverageCell]:
    _plant_or_404(session, plant_id)
    _, coverage_cells, _ = rank.build_coverage(session, plant_id)
    return coverage_cells


@app.post("/hypotheses/{hypothesis_id}/roadmap", response_model=list[schemas.RoadmapStepRead])
def build_roadmap(
    hypothesis_id: int,
    session: Session = Depends(get_session),
) -> list[schemas.RoadmapStepRead]:
    hypothesis = _hypothesis_or_404(session, hypothesis_id)
    existing = (
        session.query(models.RoadmapStep)
        .filter_by(hypothesis_id=hypothesis_id)
        .order_by(models.RoadmapStep.step_order)
        .all()
    )
    if existing:
        return existing

    steps = _roadmap_steps_for(hypothesis)
    hypothesis.status = "in_roadmap"
    session.add_all(steps)
    session.commit()
    return steps


@app.post("/roadmap/{step_id}/artifact", response_model=schemas.ArtifactResponse)
def upload_artifact(
    step_id: int,
    payload: schemas.ArtifactCreate,
    session: Session = Depends(get_session),
) -> schemas.ArtifactResponse:
    if payload.outcome not in {"success", "failure", "partial"}:
        raise HTTPException(status_code=400, detail="outcome must be success, failure or partial")

    step = session.get(models.RoadmapStep, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="Roadmap step not found")

    artifact = models.ExperimentArtifact(
        roadmap_step_id=step.id,
        hypothesis_id=step.hypothesis_id,
        outcome=payload.outcome,
        measured_value=payload.measured_value,
        predicted_min=payload.predicted_min,
        predicted_max=payload.predicted_max,
        note=payload.note,
    )
    step.status = "done"
    session.add(artifact)
    session.flush()
    calibrations, dead_end = learn.apply_artifact(session, artifact)
    session.commit()
    first_calibration = calibrations[0] if calibrations else None
    return schemas.ArtifactResponse(
        artifact_id=artifact.id,
        calibration_ids=[item.id for item in calibrations],
        rule_id=first_calibration.rule_id if first_calibration else None,
        coeff_before=first_calibration.coeff_before if first_calibration else None,
        coeff_after=first_calibration.coeff_after if first_calibration else None,
        dead_end_id=dead_end.id if dead_end else None,
    )


@app.get("/hypotheses/{hypothesis_id}/card", response_model=schemas.CardResponse)
def hypothesis_card(
    hypothesis_id: int,
    session: Session = Depends(get_session),
) -> schemas.CardResponse:
    hypothesis = _hypothesis_or_404(session, hypothesis_id)
    evaluation = _latest_evaluation(session, hypothesis_id)
    roadmap = (
        session.query(models.RoadmapStep)
        .filter_by(hypothesis_id=hypothesis_id)
        .order_by(models.RoadmapStep.step_order)
        .all()
    )
    text, llm_used = llm.build_hypothesis_card(
        {
            "hypothesis": {
                "id": hypothesis.id,
                "title": hypothesis.title,
                "origin": hypothesis.origin,
                "status": hypothesis.status,
                "module": hypothesis.module.code if hypothesis.module else None,
            },
            "evaluation": _evaluation_context(evaluation),
            "roadmap": [
                {
                    "order": step.step_order,
                    "title": step.title,
                    "cost": step.cost,
                    "duration_days": step.duration_days,
                    "is_killer": step.is_killer,
                    "status": step.status,
                }
                for step in roadmap
            ],
        }
    )
    return schemas.CardResponse(hypothesis_id=hypothesis_id, llm_used=llm_used, text=text)


@app.post("/plants/{plant_id}/literature-hypotheses", response_model=schemas.LiteratureResponse)
def literature_hypotheses(
    plant_id: int,
    max_cells: int = Query(3, ge=1, le=8),
    session: Session = Depends(get_session),
) -> schemas.LiteratureResponse:
    _plant_or_404(session, plant_id)
    if session.query(models.CorpusChunk).count() == 0:
        raise HTTPException(status_code=400, detail="Корпус не проиндексирован — сначала проиндексируй литературу")
    health = ollama_client.health()
    if not health.get("reachable"):
        raise HTTPException(status_code=503, detail=f"Ollama недоступен: {health.get('error')}")
    proposals = literature.propose(session, plant_id, max_cells=max_cells)
    return schemas.LiteratureResponse(
        plant_id=plant_id,
        proposals=[schemas.LiteratureProposal(**item) for item in proposals],
    )


@app.get("/rules", response_model=list[schemas.RuleRead])
def list_rules(session: Session = Depends(get_session)) -> list[schemas.RuleRead]:
    rules = session.query(models.Rule).join(models.Module).order_by(models.Rule.id).all()
    return [_rule_schema(rule) for rule in rules]


@app.patch("/rules/{rule_id}", response_model=schemas.RuleRead)
def update_rule(
    rule_id: int,
    payload: schemas.RuleUpdate,
    session: Session = Depends(get_session),
) -> schemas.RuleRead:
    rule = session.get(models.Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Правило не найдено")
    if payload.coeff is not None:
        rule.coeff = payload.coeff
    if payload.coeff_min is not None:
        rule.coeff_min = payload.coeff_min
    if payload.coeff_max is not None:
        rule.coeff_max = payload.coeff_max
    if rule.coeff_min is not None and rule.coeff_max is not None and rule.coeff_min > rule.coeff_max:
        raise HTTPException(status_code=400, detail="coeff_min не может быть больше coeff_max")
    session.commit()
    session.refresh(rule)
    return _rule_schema(rule)


@app.get("/dead-ends", response_model=list[schemas.DeadEndRead])
def list_dead_ends(session: Session = Depends(get_session)) -> list[schemas.DeadEndRead]:
    dead_ends = session.query(models.DeadEnd).order_by(models.DeadEnd.id).all()
    return [_dead_end_schema(item) for item in dead_ends]


@app.post("/corpus/index", response_model=schemas.CorpusIndexResponse)
def corpus_index(
    payload: schemas.CorpusIndexRequest | None = None,
    session: Session = Depends(get_session),
) -> schemas.CorpusIndexResponse:
    payload = payload or schemas.CorpusIndexRequest()
    health = ollama_client.health()
    if not health.get("reachable"):
        raise HTTPException(status_code=503, detail=f"Ollama недоступен: {health.get('error')}")
    if not health.get("embed_model_present"):
        raise HTTPException(
            status_code=503,
            detail=f"Модель эмбеддингов {get_settings().ollama_embed_model} не найдена в Ollama",
        )
    try:
        stats = index_corpus(session, payload.path, reindex=payload.reindex)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.CorpusIndexResponse(**stats.as_dict())


@app.get("/corpus/stats", response_model=schemas.CorpusStatsResponse)
def corpus_stats(session: Session = Depends(get_session)) -> schemas.CorpusStatsResponse:
    documents = session.query(models.CorpusDocument).order_by(models.CorpusDocument.id).all()
    chunk_count = session.query(models.CorpusChunk).count()
    return schemas.CorpusStatsResponse(
        documents=len(documents),
        chunks=chunk_count,
        ollama=ollama_client.health(),
        files=[
            schemas.CorpusDocumentRead(
                source_file=document.source_file,
                kind=document.kind,
                plant_hint=document.plant_hint,
                n_chunks=document.n_chunks,
            )
            for document in documents
        ],
    )


@app.get("/corpus/search", response_model=schemas.CorpusSearchResponse)
def corpus_search(
    q: str = Query(..., min_length=3),
    k: int | None = Query(None, ge=1, le=20),
    plant_hint: str | None = Query(None),
    session: Session = Depends(get_session),
) -> schemas.CorpusSearchResponse:
    try:
        hits = retriever.search(session, q, k=k, plant_hint=plant_hint)
    except ollama_client.OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return schemas.CorpusSearchResponse(
        query=q,
        hits=[schemas.CorpusHit(**hit.as_dict()) for hit in hits],
    )


@app.post("/corpus/ask", response_model=schemas.CorpusAskResponse)
def corpus_ask(
    payload: schemas.CorpusAskRequest,
    session: Session = Depends(get_session),
) -> schemas.CorpusAskResponse:
    try:
        result = retriever.answer(
            session, payload.query, k=payload.k, plant_hint=payload.plant_hint
        )
    except ollama_client.OllamaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return schemas.CorpusAskResponse(
        query=payload.query,
        answer=result["answer"],
        used_llm=result["used_llm"],
        citations=[schemas.CorpusHit(**item) for item in result["citations"]],
    )


def _resolve_plant(
    session: Session,
    plant_id: int | None,
    plant_code: str | None,
    plant_title: str | None,
) -> models.Plant:
    if plant_id is not None:
        return _plant_or_404(session, plant_id)
    if not plant_code:
        raise HTTPException(status_code=400, detail="plant_id or plant_code is required")

    plant = session.query(models.Plant).filter_by(code=plant_code).one_or_none()
    if plant:
        return plant

    plant = models.Plant(code=plant_code, title=plant_title or plant_code)
    session.add(plant)
    session.flush()
    return plant


def _plant_or_404(session: Session, plant_id: int) -> models.Plant:
    plant = session.get(models.Plant, plant_id)
    if not plant:
        raise HTTPException(status_code=404, detail="Plant not found")
    return plant


def _hypothesis_or_404(session: Session, hypothesis_id: int) -> models.Hypothesis:
    hypothesis = session.get(models.Hypothesis, hypothesis_id)
    if not hypothesis:
        raise HTTPException(status_code=404, detail="Hypothesis not found")
    return hypothesis


def _module_by_code(session: Session, code: str) -> models.Module:
    module = session.query(models.Module).filter_by(code=code).one_or_none()
    if not module:
        raise HTTPException(status_code=400, detail=f"Unknown module_code: {code}")
    return module


def _loss_cell_schema(cell: models.LossCell) -> schemas.LossCellRead:
    return schemas.LossCellRead(
        id=cell.id,
        metal_code=cell.metal.code,
        size_class_code=cell.size_class.code,
        mineral_form_code=cell.mineral_form.code,
        loss_cause=cell.mineral_form.loss_cause,
        recoverable=cell.mineral_form.recoverable,
        tons=cell.tons,
    )


def _diagnosis_matrix(cells: list[models.LossCell]) -> list[dict[str, Decimal | str | bool]]:
    matrix: dict[tuple[str, str, str], dict[str, Decimal | str | bool]] = {}
    for cell in cells:
        key = (cell.size_class.code, cell.mineral_form.code, cell.metal.code)
        item = matrix.setdefault(
            key,
            {
                "size_class_code": cell.size_class.code,
                "mineral_form_code": cell.mineral_form.code,
                "metal_code": cell.metal.code,
                "loss_cause": cell.mineral_form.loss_cause,
                "recoverable": cell.mineral_form.recoverable,
                "tons": Decimal("0"),
            },
        )
        item["tons"] = item["tons"] + cell.tons
    return list(matrix.values())


def _hypothesis_item(
    session: Session,
    hypothesis: models.Hypothesis,
) -> schemas.HypothesisListItem:
    evaluation = _latest_evaluation(session, hypothesis.id)
    provenance = evaluation.provenance if evaluation and evaluation.provenance else {}
    return schemas.HypothesisListItem(
        id=hypothesis.id,
        plant_id=hypothesis.plant_id,
        module_code=hypothesis.module.code if hypothesis.module else None,
        title=hypothesis.title,
        origin=hypothesis.origin,
        status=hypothesis.status,
        latest_rank=evaluation.rank if evaluation else None,
        latest_effect_tons_max=evaluation.effect_tons_max if evaluation else None,
        latest_effect_usd_max=evaluation.effect_usd_max if evaluation else None,
        latest_feasible=evaluation.feasible if evaluation else None,
        target_cells=provenance.get("target_cell_ids", []),
        dead_end_flag=evaluation.dead_end_flag if evaluation else False,
    )


def _ranking_item(
    evaluation: models.Evaluation,
    conflicts: dict[int, list[int]] | None = None,
) -> schemas.RankingItem:
    provenance = evaluation.provenance or {}
    hypothesis = evaluation.hypothesis
    return schemas.RankingItem(
        hypothesis_id=hypothesis.id,
        title=hypothesis.title,
        module_code=hypothesis.module.code if hypothesis.module else None,
        effect_tons_max=evaluation.effect_tons_max,
        effect_usd_max=evaluation.effect_usd_max,
        feasible=evaluation.feasible,
        relevance_score=evaluation.relevance_score,
        rank=evaluation.rank,
        target_cells=provenance.get("target_cell_ids", []),
        dead_end_flag=evaluation.dead_end_flag,
        competes_with=(conflicts or {}).get(hypothesis.id, []),
    )


def _roadmap_steps_for(hypothesis: models.Hypothesis) -> list[models.RoadmapStep]:
    module_code = hypothesis.module.code if hypothesis.module else "generic"
    base_key = f"{hypothesis.plant_id}:{module_code}"
    return [
        models.RoadmapStep(
            hypothesis_id=hypothesis.id,
            step_order=1,
            title="Targeted sampling for the dominant loss cell",
            shared_key=f"{base_key}:sampling",
            cost=Decimal("20000"),
            duration_days=2,
            success_criterion="Representative sample collected and logged",
            is_killer=True,
        ),
        models.RoadmapStep(
            hypothesis_id=hypothesis.id,
            step_order=2,
            title="Mineralogy and size-by-form confirmation",
            shared_key=f"{base_key}:mineralogy",
            cost=Decimal("60000"),
            duration_days=5,
            success_criterion="Target loss mechanism confirmed by lab report",
            is_killer=True,
        ),
        models.RoadmapStep(
            hypothesis_id=hypothesis.id,
            step_order=3,
            title="Bench-scale module test",
            shared_key=f"{base_key}:bench-test",
            cost=Decimal("120000"),
            duration_days=7,
            success_criterion="Measured recovery lands inside engine interval",
            is_killer=False,
        ),
    ]


def _rule_schema(rule: models.Rule) -> schemas.RuleRead:
    return schemas.RuleRead(
        id=rule.id,
        module_code=rule.module.code,
        code=rule.code,
        target_cause=rule.target_cause,
        target_size_class_code=rule.target_size_class.code if rule.target_size_class else None,
        coeff=rule.coeff,
        coeff_min=rule.coeff_min,
        coeff_max=rule.coeff_max,
        requires_kind=rule.requires_kind,
        source=rule.source,
    )


def _dead_end_schema(item: models.DeadEnd) -> schemas.DeadEndRead:
    return schemas.DeadEndRead(
        id=item.id,
        module_code=item.module.code if item.module else None,
        target_cause=item.target_cause,
        size_class_code=item.size_class.code if item.size_class else None,
        reason=item.reason,
    )


def _latest_evaluation(
    session: Session,
    hypothesis_id: int,
) -> models.Evaluation | None:
    return (
        session.query(models.Evaluation)
        .filter_by(hypothesis_id=hypothesis_id)
        .order_by(models.Evaluation.id.desc())
        .first()
    )


def _evaluation_context(evaluation: models.Evaluation | None) -> dict:
    if not evaluation:
        return {}
    return {
        "rule_id": evaluation.rule_id,
        "effect_tons_min": evaluation.effect_tons_min,
        "effect_tons_max": evaluation.effect_tons_max,
        "effect_usd_min": evaluation.effect_usd_min,
        "effect_usd_max": evaluation.effect_usd_max,
        "feasible": evaluation.feasible,
        "relevance_score": evaluation.relevance_score,
        "rank": evaluation.rank,
        "dead_end_flag": evaluation.dead_end_flag,
        "provenance": evaluation.provenance,
    }
