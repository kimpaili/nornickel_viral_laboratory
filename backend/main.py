from __future__ import annotations

import csv
import hashlib
import io
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from . import docingest, export_pdf, ingest, llm, models, schemas
from .config import get_settings
from .db import get_session, init_db
from .engine import base, generate, learn, portfolio, rank
from .rag import literature, loader, retriever, yandex_client
from .rag.indexer import index_corpus


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Лаборатория гипотез MVP",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Ни один неожиданный сбой не должен показывать жюри голую 500-ку.

    Известные ошибки (HTTPException, валидация) обрабатываются FastAPI как обычно;
    сюда попадает только по-настоящему неожиданное — и превращается в понятный
    JSON с человекочитаемым сообщением по-русски."""
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Внутренняя ошибка при обработке запроса. "
            "Проверьте формат входных данных и попробуйте ещё раз.",
            "error_type": type(exc).__name__,
        },
    )


@app.get("/health", response_model=schemas.HealthResponse)
def health() -> schemas.HealthResponse:
    settings = get_settings()
    return schemas.HealthResponse(
        status="ok",
        llm_provider="yandex",
        llm_model=settings.yandex_chat_model,
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
    except Exception as exc:  # noqa: BLE001 - кривой XLSX даёт понятное 400, не 500
        raise HTTPException(
            status_code=400,
            detail=f"Не удалось разобрать файл «{upload.filename}»: {exc}",
        ) from exc

    if count == 0:
        raise HTTPException(
            status_code=400,
            detail="В файле не распознано ни одной строки потерь. Нужны колонки: "
            "металл, класс крупности, минеральная форма, тонны.",
        )

    session.commit()
    return schemas.IngestResponse(
        plant_id=plant.id,
        inserted_or_updated=count,
        skipped_rows=len(warnings),
        warnings=warnings,
    )


@app.post("/plants/{plant_id}/ingest-bundle", response_model=schemas.BundleIngestResponse)
def ingest_bundle(
    plant_id: int,
    task_prompt: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    prompts: list[str] = Form(default=[]),
    session: Session = Depends(get_session),
) -> schemas.BundleIngestResponse:
    """V3: единый вход «общий промт + пары файл/промт».

    Промпт маршрутизирует файл: матрица потерь, гипотезы, корпус, оборудование,
    картинка-с-инструкцией. Численный эффект не отдаётся LLM — он по-прежнему
    считается движком после парсинга структурированных данных.
    """
    _plant_or_404(session, plant_id)
    results: list[schemas.BundleFileResult] = []
    prompt_list = list(prompts or [])
    for index, upload in enumerate(files or []):
        prompt = prompt_list[index] if index < len(prompt_list) else ""
        results.append(_process_bundle_file(session, plant_id, upload, prompt))

    session.commit()
    return schemas.BundleIngestResponse(
        plant_id=plant_id,
        task_prompt=task_prompt,
        understood_summary=_build_data_summary(session, plant_id, task_prompt, results),
        constraints=_extract_constraints(task_prompt),
        results=results,
    )


@app.get("/plants/{plant_id}/data-summary", response_model=schemas.DataSummaryResponse)
def data_summary(
    plant_id: int,
    task_prompt: str | None = Query(None),
    session: Session = Depends(get_session),
) -> schemas.DataSummaryResponse:
    _plant_or_404(session, plant_id)
    return schemas.DataSummaryResponse(
        plant_id=plant_id,
        task_prompt=task_prompt,
        summary=_build_data_summary(session, plant_id, task_prompt, []),
        constraints=_extract_constraints(task_prompt),
    )


@app.post(
    "/plants/{plant_id}/ingest-hypotheses",
    response_model=schemas.HypothesisDocxResponse,
)
def ingest_hypotheses_docx(
    plant_id: int,
    upload: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> schemas.HypothesisDocxResponse:
    """P0: приём экспертных гипотез из DOCX «мозгового штурма» жюри.

    Каждая строка списка → отдельная экспертная гипотеза. Модуль-рычаг угадывается
    по ключевым словам; если не угадан — гипотеза оценивается всеми модулями."""
    _plant_or_404(session, plant_id)
    if not (upload.filename or "").lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Ожидается файл .docx с гипотезами")
    try:
        parsed, warnings = docingest.parse_hypotheses_from_docx(upload.file, upload.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created: list[schemas.IngestedHypothesis] = []
    skipped = 0
    for item in parsed:
        module = _module_by_code(session, item.module_code) if item.module_code else None
        existing = (
            session.query(models.Hypothesis)
            .filter_by(plant_id=plant_id, title=item.title)
            .one_or_none()
        )
        if existing:
            skipped += 1
            continue
        hypothesis = models.Hypothesis(
            plant_id=plant_id,
            module_id=module.id if module else None,
            title=item.title,
            origin="expert",
        )
        session.add(hypothesis)
        session.flush()
        created.append(
            schemas.IngestedHypothesis(
                id=hypothesis.id, title=hypothesis.title, module_code=item.module_code
            )
        )
    session.commit()
    return schemas.HypothesisDocxResponse(
        plant_id=plant_id,
        created=len(created),
        skipped_existing=skipped,
        hypotheses=created,
        warnings=warnings,
    )


@app.post("/corpus/upload", response_model=schemas.CorpusUploadResponse)
def upload_corpus_file(
    upload: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> schemas.CorpusUploadResponse:
    """P0: приём PDF/DOCX в литературный корпус.

    Файл сохраняется в папку корпуса и, если настроен Yandex, сразу индексируется
    для RAG. Без ключей Yandex файл всё равно сохранён — индексация позже."""
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in loader.SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"В корпус принимаются {', '.join(sorted(loader.SUPPORTED_SUFFIXES))}; "
            f"файл «{filename}» не подходит.",
        )

    settings = get_settings()
    upload_dir = Path(settings.corpus_path) / "uploads"
    try:
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / filename
        target.write_bytes(upload.file.read())
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Не удалось сохранить файл: {exc}") from exc

    health = yandex_client.health()
    if not health.get("reachable"):
        return schemas.CorpusUploadResponse(
            saved_file=filename,
            kind=suffix.lstrip("."),
            files_indexed=0,
            chunks_added=0,
            indexed=False,
            note="Файл сохранён, но Yandex не настроен — индексация выполнится позже "
            "по кнопке «Проиндексировать корпус».",
        )
    try:
        stats = index_corpus(session, str(upload_dir), reindex=False)
    except Exception as exc:  # noqa: BLE001 - сбой индексации не должен ронять загрузку
        return schemas.CorpusUploadResponse(
            saved_file=filename,
            kind=suffix.lstrip("."),
            files_indexed=0,
            chunks_added=0,
            indexed=False,
            note=f"Файл сохранён, но индексация не удалась: {exc}",
        )
    return schemas.CorpusUploadResponse(
        saved_file=filename,
        kind=suffix.lstrip("."),
        files_indexed=stats.files_indexed,
        chunks_added=stats.chunks_added,
        indexed=True,
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
    coverage_summary, coverage_cells, conflicts, conflict_tons = rank.build_coverage(
        session, plant_id
    )
    dead_end_reasons = _dead_end_reasons(session)
    return schemas.RankingResponse(
        plant_id=plant_id,
        items=[
            _ranking_item(evaluation, conflicts, conflict_tons, dead_end_reasons)
            for evaluation in evaluations
        ],
        coverage_summary=coverage_summary,
        coverage_cells=coverage_cells,
    )


@app.get("/plants/{plant_id}/coverage", response_model=list[schemas.CoverageCell])
def plant_coverage(
    plant_id: int,
    session: Session = Depends(get_session),
) -> list[schemas.CoverageCell]:
    _plant_or_404(session, plant_id)
    _, coverage_cells, _, _ = rank.build_coverage(session, plant_id)
    return coverage_cells


@app.get("/plants/{plant_id}/portfolio-plan", response_model=schemas.PortfolioPlanResponse)
def portfolio_plan(
    plant_id: int,
    budget: Decimal = Query(Decimal("250000"), ge=0),
    limit: int = Query(3, ge=1, le=10),
    session: Session = Depends(get_session),
) -> schemas.PortfolioPlanResponse:
    _plant_or_404(session, plant_id)
    return schemas.PortfolioPlanResponse(
        **portfolio.greedy_plan(session, plant_id, budget=budget, limit=limit)
    )


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
        return [_roadmap_step_schema(step) for step in existing]

    steps = _roadmap_steps_for(hypothesis)
    hypothesis.status = "in_roadmap"
    session.add_all(steps)
    session.commit()
    return [_roadmap_step_schema(step) for step in steps]


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
    health = yandex_client.health()
    if not health.get("reachable"):
        raise HTTPException(status_code=503, detail=f"Yandex не настроен: {health.get('error')}")
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


def _csv_stream(header: list[str], rows: list[list], filename: str) -> StreamingResponse:
    buffer = io.StringIO()
    # utf-8-sig, чтобы Excel на Windows корректно показал кириллицу.
    buffer.write("﻿")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if value is None else value for value in row])
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/portfolio.csv")
def export_portfolio_csv(
    plant_id: int = Query(...),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """P2: портфель фабрики — гипотезы + их последняя оценка — в CSV из БД."""
    plant = _plant_or_404(session, plant_id)
    evaluations = rank.list_ranking(session, plant_id)
    header = [
        "hypothesis_id", "title", "origin", "module", "target_cause",
        "effect_tons_min", "effect_tons_max", "effect_usd_min", "effect_usd_max",
        "feasible", "relevance_score", "rank", "dead_end_flag",
    ]
    rows = []
    for evaluation in evaluations:
        hypothesis = evaluation.hypothesis
        provenance = evaluation.provenance or {}
        rows.append([
            hypothesis.id,
            hypothesis.title,
            hypothesis.origin,
            hypothesis.module.code if hypothesis.module else "",
            provenance.get("target_cause", ""),
            evaluation.effect_tons_min,
            evaluation.effect_tons_max,
            evaluation.effect_usd_min,
            evaluation.effect_usd_max,
            evaluation.feasible,
            evaluation.relevance_score,
            evaluation.rank,
            evaluation.dead_end_flag,
        ])
    return _csv_stream(header, rows, f"portfolio_{plant.code}.csv")


@app.get("/export/hypothesis/{hypothesis_id}.csv")
def export_hypothesis_csv(
    hypothesis_id: int,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """P2: одна гипотеза — разбивка по модулям и этапы дорожной карты — в CSV."""
    hypothesis = _hypothesis_or_404(session, hypothesis_id)
    evaluation = _latest_evaluation(session, hypothesis_id)
    provenance = evaluation.provenance if evaluation else {}
    header = ["section", "module_or_step", "detail", "value_min", "value_max", "extra"]
    rows: list[list] = []
    for report in (provenance or {}).get("module_reports", []):
        rows.append([
            "module",
            report.get("module_code"),
            f"rule={report.get('rule_code')} coeff={report.get('coeff_min')}-{report.get('coeff_max')} "
            f"{'[выбран]' if report.get('selected') else ''}",
            report.get("effect_tons_min"),
            report.get("effect_tons_max"),
            f"usd {report.get('effect_usd_min')}-{report.get('effect_usd_max')}; "
            f"feasible={report.get('feasible')}",
        ])
    roadmap = (
        session.query(models.RoadmapStep)
        .filter_by(hypothesis_id=hypothesis_id)
        .order_by(models.RoadmapStep.step_order)
        .all()
    )
    for step in roadmap:
        rows.append([
            "roadmap_step",
            step.step_order,
            step.title,
            step.cost,
            step.duration_days,
            f"killer={step.is_killer}; status={step.status}",
        ])
    return _csv_stream(header, rows, f"hypothesis_{hypothesis_id}.csv")


@app.get("/export/matrix.csv")
def export_matrix_csv(
    plant_id: int = Query(...),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """P2: матрица потерь фабрики (класс × форма × металл × тонны) в CSV."""
    plant = _plant_or_404(session, plant_id)
    cells = session.query(models.LossCell).filter_by(plant_id=plant_id).all()
    header = [
        "metal", "size_class", "mineral_form", "loss_cause", "recoverable", "tons",
    ]
    rows = [
        [
            cell.metal.code,
            cell.size_class.code,
            cell.mineral_form.code,
            cell.mineral_form.loss_cause,
            cell.mineral_form.recoverable,
            cell.tons,
        ]
        for cell in cells
    ]
    return _csv_stream(header, rows, f"matrix_{plant.code}.csv")


def _pdf_response(content: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/export/portfolio.pdf")
def export_portfolio_pdf(
    plant_id: int = Query(...),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """PDF-дашборд портфеля «как на экране» (ТЗ §11/§17, V3 §4)."""
    plant = _plant_or_404(session, plant_id)
    evaluations = rank.list_ranking(session, plant_id)
    content = export_pdf.portfolio_pdf(plant.code, plant.title, evaluations)
    return _pdf_response(content, f"portfolio_{plant.code}.pdf")


@app.get("/export/matrix.pdf")
def export_matrix_pdf(
    plant_id: int = Query(...),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """PDF матрицы потерь фабрики (класс × форма × металл × тонны)."""
    plant = _plant_or_404(session, plant_id)
    cells = session.query(models.LossCell).filter_by(plant_id=plant_id).all()
    content = export_pdf.matrix_pdf(plant.code, plant.title, cells)
    return _pdf_response(content, f"matrix_{plant.code}.pdf")


@app.get("/export/hypothesis/{hypothesis_id}.pdf")
def export_hypothesis_pdf(
    hypothesis_id: int,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """PDF карточки гипотезы: текст + разбивка по модулям + дорожная карта."""
    hypothesis = _hypothesis_or_404(session, hypothesis_id)
    evaluation = _latest_evaluation(session, hypothesis_id)
    provenance = (evaluation.provenance if evaluation else {}) or {}
    roadmap = (
        session.query(models.RoadmapStep)
        .filter_by(hypothesis_id=hypothesis_id)
        .order_by(models.RoadmapStep.step_order)
        .all()
    )
    card_text, _ = llm.build_hypothesis_card(
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
                {"order": step.step_order, "title": step.title, "cost": step.cost,
                 "duration_days": step.duration_days, "is_killer": step.is_killer,
                 "status": step.status}
                for step in roadmap
            ],
        }
    )
    content = export_pdf.hypothesis_pdf(
        hypothesis.title,
        card_text,
        provenance.get("module_reports", []),
        roadmap,
    )
    return _pdf_response(content, f"hypothesis_{hypothesis_id}.pdf")


@app.post("/corpus/index", response_model=schemas.CorpusIndexResponse)
def corpus_index(
    payload: schemas.CorpusIndexRequest | None = None,
    session: Session = Depends(get_session),
) -> schemas.CorpusIndexResponse:
    payload = payload or schemas.CorpusIndexRequest()
    health = yandex_client.health()
    if not health.get("reachable"):
        raise HTTPException(status_code=503, detail=f"Yandex не настроен: {health.get('error')}")
    try:
        stats = index_corpus(session, payload.path, reindex=payload.reindex)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except yandex_client.YandexError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return schemas.CorpusIndexResponse(**stats.as_dict())


@app.get("/corpus/stats", response_model=schemas.CorpusStatsResponse)
def corpus_stats(session: Session = Depends(get_session)) -> schemas.CorpusStatsResponse:
    documents = session.query(models.CorpusDocument).order_by(models.CorpusDocument.id).all()
    chunk_count = session.query(models.CorpusChunk).count()
    return schemas.CorpusStatsResponse(
        documents=len(documents),
        chunks=chunk_count,
        llm=yandex_client.health(),
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
    except yandex_client.YandexError as exc:
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
    except yandex_client.YandexError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return schemas.CorpusAskResponse(
        query=payload.query,
        answer=result["answer"],
        used_llm=result["used_llm"],
        citations=[schemas.CorpusHit(**item) for item in result["citations"]],
    )


def _process_bundle_file(
    session: Session,
    plant_id: int,
    upload: UploadFile,
    prompt: str,
) -> schemas.BundleFileResult:
    filename = Path(upload.filename or "upload.bin").name
    suffix = Path(filename).suffix.lower()
    content = upload.file.read()
    kind = _route_kind(filename, prompt)

    try:
        if kind == "loss_matrix":
            rows, warnings = ingest.parse_loss_cells_from_xlsx(io.BytesIO(content), filename)
            count = ingest.upsert_loss_cells(session, plant_id=plant_id, rows=rows)
            return schemas.BundleFileResult(
                filename=filename,
                prompt=prompt,
                kind=kind,
                action="upsert_loss_cells",
                status="ok" if count else "warning",
                detail=f"Распознано и обновлено ячеек потерь: {count}",
                inserted_or_updated=count,
                warnings=warnings,
            )

        if kind == "hypotheses_docx":
            parsed, warnings = docingest.parse_hypotheses_from_docx(io.BytesIO(content), filename)
            created = _create_hypotheses_from_parsed(session, plant_id, parsed)
            return schemas.BundleFileResult(
                filename=filename,
                prompt=prompt,
                kind=kind,
                action="create_expert_hypotheses",
                status="ok",
                detail=f"Создано экспертных гипотез: {created}",
                created_hypotheses=created,
                warnings=warnings,
            )

        if kind == "corpus":
            chunks = _save_and_index_corpus_bytes(session, filename, suffix, content)
            return schemas.BundleFileResult(
                filename=filename,
                prompt=prompt,
                kind=kind,
                action="save_to_corpus",
                status="ok",
                detail="Файл сохранён в корпус; индексация выполнена, если настроен Yandex.",
                chunks_added=chunks,
            )

        if kind == "image":
            chunks = _save_image_as_prompted_source(session, filename, content, prompt)
            return schemas.BundleFileResult(
                filename=filename,
                prompt=prompt,
                kind=kind,
                action="save_image_source",
                status="ok",
                detail=(
                    "Картинка принята и сохранена. Vision/OCR-слой в этом MVP заменён "
                    "управляемым текстовым описанием из промпта; описание добавлено в корпус, "
                    "если доступен Yandex embedding."
                ),
                chunks_added=chunks,
            )

        return schemas.BundleFileResult(
            filename=filename,
            prompt=prompt,
            kind=kind,
            action="skip",
            status="warning",
            detail=f"Тип файла {suffix or 'без расширения'} не поддержан в V3-входе.",
        )
    except ValueError as exc:
        return schemas.BundleFileResult(
            filename=filename,
            prompt=prompt,
            kind=kind,
            action="parse",
            status="error",
            detail=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - один кривой файл не роняет весь пакет
        return schemas.BundleFileResult(
            filename=filename,
            prompt=prompt,
            kind=kind,
            action="process",
            status="error",
            detail=f"Не удалось обработать файл: {exc}",
        )


def _route_kind(filename: str, prompt: str) -> str:
    suffix = Path(filename).suffix.lower()
    text = f"{filename} {prompt}".lower()
    if suffix == ".xlsx" or any(word in text for word in ("матрица", "потер", "хвост")):
        return "loss_matrix"
    if suffix == ".docx" and any(word in text for word in ("гипотез", "мозгов", "идеи")):
        return "hypotheses_docx"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in loader.SUPPORTED_SUFFIXES:
        return "corpus"
    return "unsupported"


def _create_hypotheses_from_parsed(session: Session, plant_id: int, parsed: list) -> int:
    created = 0
    for item in parsed:
        module = _module_by_code(session, item.module_code) if item.module_code else None
        existing = (
            session.query(models.Hypothesis)
            .filter_by(plant_id=plant_id, title=item.title)
            .one_or_none()
        )
        if existing:
            continue
        session.add(
            models.Hypothesis(
                plant_id=plant_id,
                module_id=module.id if module else None,
                title=item.title,
                origin="expert",
            )
        )
        created += 1
    session.flush()
    return created


def _save_and_index_corpus_bytes(
    session: Session,
    filename: str,
    suffix: str,
    content: bytes,
) -> int:
    if suffix not in loader.SUPPORTED_SUFFIXES:
        return 0
    upload_dir = Path(get_settings().corpus_path) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / filename
    target.write_bytes(content)
    if not yandex_client.health().get("reachable"):
        return 0
    stats = index_corpus(session, str(upload_dir), reindex=False)
    return stats.chunks_added


def _save_image_as_prompted_source(
    session: Session,
    filename: str,
    content: bytes,
    prompt: str,
) -> int:
    upload_dir = Path(get_settings().corpus_path) / "uploads" / "images"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / filename
    target.write_bytes(content)
    description = (
        f"Изображение: {filename}\n"
        f"Инструкция пользователя: {prompt or 'без отдельной инструкции'}\n"
        "MVP V3: изображение принято как источник данных. "
        "Без vision-модуля численное извлечение из картинки не выполняется; "
        "промпт фиксирует, как эксперт просит интерпретировать схему/оборудование."
    )
    return _upsert_text_corpus_document(
        session,
        source_file=f"uploads/images/{filename}.prompt.txt",
        kind="image_prompt",
        file_hash=hashlib.sha256(content + prompt.encode("utf-8")).hexdigest(),
        content=description,
    )


def _upsert_text_corpus_document(
    session: Session,
    *,
    source_file: str,
    kind: str,
    file_hash: str,
    content: str,
) -> int:
    document = session.query(models.CorpusDocument).filter_by(source_file=source_file).one_or_none()
    if document and document.file_hash == file_hash:
        return 0
    if document:
        session.query(models.CorpusChunk).filter_by(document_id=document.id).delete()
        document.file_hash = file_hash
        document.kind = kind
        document.n_chunks = 0
    else:
        document = models.CorpusDocument(
            source_file=source_file,
            kind=kind,
            file_hash=file_hash,
            n_chunks=0,
        )
        session.add(document)
        session.flush()

    if not yandex_client.health().get("reachable"):
        return 0
    vector = yandex_client.embed_one(content, kind="doc")
    session.add(
        models.CorpusChunk(
            document_id=document.id,
            source_file=source_file,
            page=None,
            chunk_index=0,
            content=content,
            plant_hint=None,
            embedding=vector,
        )
    )
    document.n_chunks = 1
    return 1


def _build_data_summary(
    session: Session,
    plant_id: int,
    task_prompt: str | None,
    results: list[schemas.BundleFileResult],
) -> str:
    plant = _plant_or_404(session, plant_id)
    cells = session.query(models.LossCell).filter_by(plant_id=plant_id).all()
    recoverable = sum((cell.tons for cell in cells if cell.mineral_form.recoverable), Decimal("0"))
    unrecoverable = sum((cell.tons for cell in cells if not cell.mineral_form.recoverable), Decimal("0"))
    top_cells = sorted(cells, key=lambda cell: cell.tons, reverse=True)[:3]
    lines = [
        f"Фабрика: {plant.code} — {plant.title}.",
        f"Извлекаемые потери: {recoverable} т; неизвлекаемые/гидромет-кандидаты: {unrecoverable} т.",
    ]
    if task_prompt:
        lines.append(f"Общий промпт учтён как контекст и ограничения: {task_prompt[:500]}")
    if top_cells:
        lines.append("Самые тяжёлые ячейки:")
        for cell in top_cells:
            route = (
                "кандидат в другой передел (гидрометаллургия/автоклав)"
                if not cell.mineral_form.recoverable
                else cell.mineral_form.loss_cause
            )
            lines.append(
                f"- {cell.metal.code}, класс {cell.size_class.code}, "
                f"{cell.mineral_form.title}: {cell.tons} т; маршрут: {route}."
            )
    if results:
        ok = sum(1 for item in results if item.status == "ok")
        lines.append(f"Загружено файлов: {len(results)}, успешно обработано: {ok}.")
    return "\n".join(lines)


def _extract_constraints(task_prompt: str | None) -> list[str]:
    if not task_prompt:
        return []
    constraints: list[str] = []
    for raw in task_prompt.replace(";", "\n").split("\n"):
        line = raw.strip()
        low = line.lower()
        if not line:
            continue
        if any(word in low for word in ("бюджет", "лимит", "нельзя", "запрет", "только", "срок", "реагент")):
            constraints.append(line)
    return constraints[:12]


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
        hydromet_candidate=not cell.mineral_form.recoverable,
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
    conflict_tons: dict[int, Decimal] | None = None,
    dead_end_reasons: dict[tuple, str] | None = None,
) -> schemas.RankingItem:
    provenance = evaluation.provenance or {}
    breakdown = provenance.get("score_breakdown") or {}
    hypothesis = evaluation.hypothesis
    dead_end_reason = None
    if evaluation.dead_end_flag and dead_end_reasons is not None:
        key = (
            evaluation.rule.module_id if evaluation.rule else None,
            evaluation.rule.target_cause if evaluation.rule else None,
            evaluation.rule.target_size_class_id if evaluation.rule else None,
        )
        dead_end_reason = dead_end_reasons.get(key)
    return schemas.RankingItem(
        hypothesis_id=hypothesis.id,
        title=hypothesis.title,
        module_code=hypothesis.module.code if hypothesis.module else None,
        origin=hypothesis.origin,
        effect_tons_min=evaluation.effect_tons_min,
        effect_tons_max=evaluation.effect_tons_max,
        effect_usd_min=evaluation.effect_usd_min,
        effect_usd_max=evaluation.effect_usd_max,
        feasible=evaluation.feasible,
        relevance_score=evaluation.relevance_score,
        rank=evaluation.rank,
        target_cells=provenance.get("target_cell_ids", []),
        dead_end_flag=evaluation.dead_end_flag,
        dead_end_reason=dead_end_reason,
        competes_with=(conflicts or {}).get(hypothesis.id, []),
        competes_tons=(conflict_tons or {}).get(hypothesis.id),
        expected_effect_usd=_decimal_or_none(provenance.get("expected_effect_usd")),
        success_probability=_decimal_or_none(provenance.get("success_probability")),
        risk_score=_decimal_or_none(provenance.get("risk_score")),
        coverage_contribution=_decimal_or_none(provenance.get("coverage_contribution")),
        conflict_penalty=_decimal_or_none(provenance.get("conflict_penalty")),
        score_breakdown=breakdown,
        module_reports=[
            schemas.ModuleReport(**report)
            for report in provenance.get("module_reports", [])
        ],
    )


def _dead_end_reasons(session: Session) -> dict[tuple, str]:
    return {
        (item.module_id, item.target_cause, item.size_class_id): item.reason
        for item in session.query(models.DeadEnd).all()
    }


def _roadmap_steps_for(hypothesis: models.Hypothesis) -> list[models.RoadmapStep]:
    module_code = hypothesis.module.code if hypothesis.module else "generic"
    base_key = f"{hypothesis.plant_id}:{module_code}"
    return [
        models.RoadmapStep(
            hypothesis_id=hypothesis.id,
            step_order=1,
            title="Отбор проб по доминирующей ячейке потерь",
            shared_key=f"{base_key}:sampling",
            cost=Decimal("20000"),
            duration_days=2,
            success_criterion="Представительная проба отобрана, промаркирована и связана с ячейкой матрицы",
            is_killer=True,
        ),
        models.RoadmapStep(
            hypothesis_id=hypothesis.id,
            step_order=2,
            title="Ситовой анализ и минералогия по классам крупности",
            shared_key=f"{base_key}:mineralogy",
            cost=Decimal("60000"),
            duration_days=5,
            success_criterion="Лабораторный отчёт подтвердил форму потерь и степень раскрытия",
            is_killer=True,
        ),
        models.RoadmapStep(
            hypothesis_id=hypothesis.id,
            step_order=3,
            title="Лабораторная проверка выбранного технологического модуля",
            shared_key=f"{base_key}:bench-test",
            cost=Decimal("120000"),
            duration_days=7,
            success_criterion="Измеренный коэффициент возврата попал в интервал движка или объяснил отклонение",
            is_killer=False,
        ),
    ]


def _roadmap_step_schema(step: models.RoadmapStep) -> schemas.RoadmapStepRead:
    return schemas.RoadmapStepRead(
        id=step.id,
        hypothesis_id=step.hypothesis_id,
        step_order=step.step_order,
        title=step.title,
        shared_key=step.shared_key,
        cost=step.cost,
        duration_days=step.duration_days,
        success_criterion=step.success_criterion,
        subtasks=_roadmap_subtasks(step),
        cost_source="шаблон дорожной карты MVP: roadmap_step.cost",
        duration_source="шаблон дорожной карты MVP: roadmap_step.duration_days",
        is_killer=step.is_killer,
        status=step.status,
    )


def _roadmap_subtasks(step: models.RoadmapStep) -> list[str]:
    if step.step_order == 1:
        return [
            "выбрать целевую ячейку потерь из матрицы",
            "согласовать точку и время отбора с технологом",
            "отобрать и промаркировать пробу",
            "зафиксировать массу, класс крупности и связанный поток",
        ]
    if step.step_order == 2:
        return [
            "провести ситовой анализ по классам",
            "подготовить минералогический препарат",
            "оценить долю свободных, запертых и рассеянных форм",
            "сверить механизм потерь с правилом движка",
        ]
    return [
        "поставить лабораторный опыт по выбранному модулю",
        "измерить фактический коэффициент возврата",
        "сравнить результат с интервалом coeff_min/coeff_max",
        "загрузить артефакт опыта для калибровки правила",
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


def _decimal_or_none(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None


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
