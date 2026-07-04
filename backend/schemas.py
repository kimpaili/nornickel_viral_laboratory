from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: str
    llm_provider: str
    llm_model: str


class PlantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    title: str
    feed_smt: Decimal | None = None
    tailings_smt: Decimal | None = None


class LossCellRead(BaseModel):
    id: int
    metal_code: str
    size_class_code: str
    mineral_form_code: str
    loss_cause: str
    recoverable: bool
    tons: Decimal


class DiagnosisResponse(BaseModel):
    plant: PlantRead
    recoverable_tons: Decimal
    unrecoverable_tons: Decimal
    cells: list[LossCellRead]
    matrix: list[dict[str, Decimal | str | bool]]


class HypothesisCreate(BaseModel):
    plant_id: int
    title: str = Field(min_length=3)
    module_code: str | None = None
    origin: str = "expert"


class HypothesisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plant_id: int
    module_id: int | None = None
    title: str
    origin: str
    status: str


class HypothesisListItem(BaseModel):
    id: int
    plant_id: int
    module_code: str | None = None
    title: str
    origin: str
    status: str
    latest_rank: int | None = None
    latest_effect_tons_max: Decimal | None = None
    latest_effect_usd_max: Decimal | None = None
    latest_feasible: bool | None = None
    target_cells: list[int] = Field(default_factory=list)
    dead_end_flag: bool = False


class GenerateResponse(BaseModel):
    created: int
    hypotheses: list[HypothesisRead]
    skipped_dead_ends: int = 0


class EvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hypothesis_id: int
    rule_id: int | None = None
    effect_tons_min: Decimal | None = None
    effect_tons_max: Decimal | None = None
    effect_usd_min: Decimal | None = None
    effect_usd_max: Decimal | None = None
    feasible: bool | None = None
    relevance_score: Decimal | None = None
    rank: int | None = None
    dead_end_flag: bool = False


class EvaluateResponse(BaseModel):
    evaluated: int
    evaluations: list[EvaluationRead]


class RankingItem(BaseModel):
    hypothesis_id: int
    title: str
    module_code: str | None = None
    effect_tons_max: Decimal | None = None
    effect_usd_max: Decimal | None = None
    feasible: bool | None = None
    relevance_score: Decimal | None = None
    rank: int | None = None
    target_cells: list[int] = Field(default_factory=list)
    dead_end_flag: bool = False
    competes_with: list[int] = Field(default_factory=list)


class CoverageCell(BaseModel):
    cell_id: int
    metal_code: str
    size_class_code: str
    mineral_form_code: str
    loss_cause: str
    tons: Decimal
    claimed_effect_tons_max: Decimal = Decimal("0")
    covered_effect_tons_max: Decimal
    coverage_share: Decimal
    contested: bool = False
    covered_by_hypotheses: list[int] = Field(default_factory=list)


class CoverageSummary(BaseModel):
    total_recoverable_tons: Decimal
    covered_effect_tons_max: Decimal
    coverage_share: Decimal


class RankingResponse(BaseModel):
    plant_id: int
    items: list[RankingItem]
    coverage_summary: CoverageSummary
    coverage_cells: list[CoverageCell]


class RoadmapStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hypothesis_id: int
    step_order: int
    title: str
    shared_key: str | None = None
    cost: Decimal | None = None
    duration_days: int | None = None
    success_criterion: str | None = None
    is_killer: bool
    status: str


class ArtifactCreate(BaseModel):
    outcome: str
    measured_value: Decimal | None = None
    predicted_min: Decimal | None = None
    predicted_max: Decimal | None = None
    note: str | None = None


class ArtifactResponse(BaseModel):
    artifact_id: int
    calibration_ids: list[int]
    rule_id: int | None = None
    coeff_before: Decimal | None = None
    coeff_after: Decimal | None = None
    dead_end_id: int | None = None


class IngestResponse(BaseModel):
    plant_id: int
    inserted_or_updated: int
    skipped_rows: int
    warnings: list[str] = []


class CardResponse(BaseModel):
    hypothesis_id: int
    llm_used: bool
    text: str


class RuleRead(BaseModel):
    id: int
    module_code: str
    code: str
    target_cause: str
    target_size_class_code: str | None = None
    coeff: Decimal
    coeff_min: Decimal | None = None
    coeff_max: Decimal | None = None
    requires_kind: str | None = None
    source: str | None = None


class DeadEndRead(BaseModel):
    id: int
    module_code: str | None = None
    target_cause: str | None = None
    size_class_code: str | None = None
    reason: str


class CorpusIndexRequest(BaseModel):
    path: str | None = None
    reindex: bool = False


class CorpusIndexResponse(BaseModel):
    files_seen: int
    files_indexed: int
    files_skipped: int
    chunks_added: int
    errors: list[str] = Field(default_factory=list)


class CorpusDocumentRead(BaseModel):
    source_file: str
    kind: str
    plant_hint: str | None = None
    n_chunks: int


class CorpusStatsResponse(BaseModel):
    documents: int
    chunks: int
    ollama: dict
    files: list[CorpusDocumentRead] = Field(default_factory=list)


class CorpusHit(BaseModel):
    n: int | None = None
    chunk_id: int
    source_file: str
    page: int | None = None
    plant_hint: str | None = None
    snippet: str
    distance: float


class CorpusSearchResponse(BaseModel):
    query: str
    hits: list[CorpusHit] = Field(default_factory=list)


class CorpusAskRequest(BaseModel):
    query: str = Field(min_length=3)
    k: int | None = None
    plant_hint: str | None = None


class CorpusAskResponse(BaseModel):
    query: str
    answer: str
    used_llm: bool
    citations: list[CorpusHit] = Field(default_factory=list)


class LiteratureProposal(BaseModel):
    cell_id: int
    metal_code: str
    size_class_code: str
    mineral_form_code: str
    module_code: str | None = None
    suggested_title: str
    rationale: str
    citations: list[CorpusHit] = Field(default_factory=list)


class LiteratureResponse(BaseModel):
    plant_id: int
    proposals: list[LiteratureProposal] = Field(default_factory=list)


class RuleUpdate(BaseModel):
    coeff: Decimal | None = None
    coeff_min: Decimal | None = None
    coeff_max: Decimal | None = None
