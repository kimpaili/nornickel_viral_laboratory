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
    hydromet_candidate: bool = False


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


class ModuleReport(BaseModel):
    module_code: str
    module_title: str | None = None
    selected: bool = False
    rule_id: int | None = None
    rule_code: str | None = None
    target_cause: str | None = None
    target_size_class: str | None = None
    coeff: str | None = None
    coeff_min: str | None = None
    coeff_max: str | None = None
    feasible: bool | None = None
    required_equipment: list[str] = Field(default_factory=list)
    plant_equipment: list[str] = Field(default_factory=list)
    side_effect: str | None = None
    effect_tons_min: str | None = None
    effect_tons_max: str | None = None
    effect_usd_min: str | None = None
    effect_usd_max: str | None = None
    relevance_contribution: str | None = None
    expected_effect_usd: str | None = None
    success_probability: str | None = None
    risk_penalty: str | None = None
    coverage_contribution: str | None = None
    conflict_penalty: str | None = None
    score_breakdown: dict = Field(default_factory=dict)
    coeff_explanation: str | None = None
    selection_reason: str | None = None
    money_formula: str | None = None
    target_cells: list[dict] = Field(default_factory=list)
    source: str | None = None


class RankingItem(BaseModel):
    hypothesis_id: int
    title: str
    module_code: str | None = None
    origin: str | None = None
    effect_tons_min: Decimal | None = None
    effect_tons_max: Decimal | None = None
    effect_usd_min: Decimal | None = None
    effect_usd_max: Decimal | None = None
    feasible: bool | None = None
    relevance_score: Decimal | None = None
    rank: int | None = None
    target_cells: list[int] = Field(default_factory=list)
    dead_end_flag: bool = False
    dead_end_reason: str | None = None
    competes_with: list[int] = Field(default_factory=list)
    competes_tons: Decimal | None = None
    expected_effect_usd: Decimal | None = None
    success_probability: Decimal | None = None
    risk_score: Decimal | None = None
    coverage_contribution: Decimal | None = None
    conflict_penalty: Decimal | None = None
    score_breakdown: dict = Field(default_factory=dict)
    module_reports: list[ModuleReport] = Field(default_factory=list)


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
    delta_coverage_tons: Decimal = Decimal("0")
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
    subtasks: list[str] = Field(default_factory=list)
    cost_source: str | None = None
    duration_source: str | None = None
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


class BundleFileResult(BaseModel):
    filename: str
    prompt: str | None = None
    kind: str
    action: str
    status: str
    detail: str | None = None
    inserted_or_updated: int = 0
    created_hypotheses: int = 0
    chunks_added: int = 0
    warnings: list[str] = Field(default_factory=list)


class BundleIngestResponse(BaseModel):
    plant_id: int
    task_prompt: str | None = None
    understood_summary: str
    constraints: list[str] = Field(default_factory=list)
    results: list[BundleFileResult] = Field(default_factory=list)


class IngestedHypothesis(BaseModel):
    id: int
    title: str
    module_code: str | None = None


class HypothesisDocxResponse(BaseModel):
    plant_id: int
    created: int
    skipped_existing: int
    hypotheses: list[IngestedHypothesis] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CorpusUploadResponse(BaseModel):
    saved_file: str
    kind: str
    files_indexed: int
    chunks_added: int
    indexed: bool
    note: str | None = None


class CardResponse(BaseModel):
    hypothesis_id: int
    llm_used: bool
    text: str


class DataSummaryResponse(BaseModel):
    plant_id: int
    task_prompt: str | None = None
    summary: str
    constraints: list[str] = Field(default_factory=list)


class PortfolioPlanItem(BaseModel):
    hypothesis_id: int
    title: str
    marginal_effect_usd: Decimal
    marginal_effect_tons: Decimal
    cost: Decimal
    ratio: Decimal
    shared_steps: list[str] = Field(default_factory=list)


class PortfolioPlanResponse(BaseModel):
    plant_id: int
    budget: Decimal
    selected: list[PortfolioPlanItem] = Field(default_factory=list)
    total_effect_usd: Decimal
    total_effect_tons: Decimal
    total_cost: Decimal


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
    llm: dict
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
