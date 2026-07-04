from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import get_settings
from .db import Base


class MineralForm(Base):
    __tablename__ = "mineral_form"
    __table_args__ = (
        CheckConstraint("loss_cause IN ('free','locked','dispersed')"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    loss_cause: Mapped[str] = mapped_column(Text, nullable=False)
    recoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    loss_cells: Mapped[list["LossCell"]] = relationship(back_populates="mineral_form")


class SizeClass(Base):
    __tablename__ = "size_class"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    microns_lo: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    microns_hi: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)

    loss_cells: Mapped[list["LossCell"]] = relationship(back_populates="size_class")


class Metal(Base):
    __tablename__ = "metal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    price_usd_t: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)

    loss_cells: Mapped[list["LossCell"]] = relationship(back_populates="metal")


class Plant(Base):
    __tablename__ = "plant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    feed_smt: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    tailings_smt: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    equipment: Mapped[list["Equipment"]] = relationship(
        back_populates="plant",
        cascade="all, delete-orphan",
    )
    loss_cells: Mapped[list["LossCell"]] = relationship(
        back_populates="plant",
        cascade="all, delete-orphan",
    )
    hypotheses: Mapped[list["Hypothesis"]] = relationship(
        back_populates="plant",
        cascade="all, delete-orphan",
    )


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty: Mapped[int | None] = mapped_column(Integer, nullable=True)

    plant: Mapped[Plant] = relationship(back_populates="equipment")


class LossCell(Base):
    __tablename__ = "loss_cell"
    __table_args__ = (
        UniqueConstraint("plant_id", "metal_id", "size_class_id", "mineral_form_id"),
        Index("idx_loss_cell_plant", "plant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    metal_id: Mapped[int] = mapped_column(ForeignKey("metal.id"))
    size_class_id: Mapped[int] = mapped_column(ForeignKey("size_class.id"))
    mineral_form_id: Mapped[int] = mapped_column(ForeignKey("mineral_form.id"))
    tons: Mapped[Decimal] = mapped_column(Numeric, nullable=False)

    plant: Mapped[Plant] = relationship(back_populates="loss_cells")
    metal: Mapped[Metal] = relationship(back_populates="loss_cells")
    size_class: Mapped[SizeClass] = relationship(back_populates="loss_cells")
    mineral_form: Mapped[MineralForm] = relationship(back_populates="loss_cells")


class Module(Base):
    __tablename__ = "module"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    rules: Mapped[list["Rule"]] = relationship(back_populates="module")
    hypotheses: Mapped[list["Hypothesis"]] = relationship(back_populates="module")


class Rule(Base):
    __tablename__ = "rule"
    __table_args__ = (
        CheckConstraint("target_cause IN ('free','locked','dispersed')"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(ForeignKey("module.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    target_cause: Mapped[str] = mapped_column(Text, nullable=False)
    target_size_class_id: Mapped[int | None] = mapped_column(
        ForeignKey("size_class.id"),
        nullable=True,
    )
    coeff: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    coeff_min: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    coeff_max: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    side_effect: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)

    module: Mapped[Module] = relationship(back_populates="rules")
    target_size_class: Mapped[SizeClass | None] = relationship()
    evaluations: Mapped[list["Evaluation"]] = relationship(back_populates="rule")


class Hypothesis(Base):
    __tablename__ = "hypothesis"
    __table_args__ = (
        CheckConstraint("origin IN ('expert','generated')"),
        CheckConstraint("status IN ('new','evaluated','in_roadmap','confirmed','rejected')"),
        Index("idx_hypothesis_plant", "plant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plant_id: Mapped[int] = mapped_column(ForeignKey("plant.id", ondelete="CASCADE"))
    module_id: Mapped[int | None] = mapped_column(ForeignKey("module.id"), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    plant: Mapped[Plant] = relationship(back_populates="hypotheses")
    module: Mapped[Module | None] = relationship(back_populates="hypotheses")
    evaluations: Mapped[list["Evaluation"]] = relationship(
        back_populates="hypothesis",
        cascade="all, delete-orphan",
    )
    roadmap_steps: Mapped[list["RoadmapStep"]] = relationship(
        back_populates="hypothesis",
        cascade="all, delete-orphan",
    )


class Evaluation(Base):
    __tablename__ = "evaluation"
    __table_args__ = (Index("idx_evaluation_hyp", "hypothesis_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hypothesis_id: Mapped[int] = mapped_column(ForeignKey("hypothesis.id", ondelete="CASCADE"))
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("rule.id"), nullable=True)
    target_metal_id: Mapped[int | None] = mapped_column(ForeignKey("metal.id"), nullable=True)
    effect_tons_min: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    effect_tons_max: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    effect_usd_min: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    effect_usd_max: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    feasible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relevance_score: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dead_end_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    hypothesis: Mapped[Hypothesis] = relationship(back_populates="evaluations")
    rule: Mapped[Rule | None] = relationship(back_populates="evaluations")
    target_metal: Mapped[Metal | None] = relationship()


class RoadmapStep(Base):
    __tablename__ = "roadmap_step"
    __table_args__ = (
        CheckConstraint("status IN ('planned','done','skipped')"),
        Index("idx_roadmap_hyp", "hypothesis_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hypothesis_id: Mapped[int] = mapped_column(ForeignKey("hypothesis.id", ondelete="CASCADE"))
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    shared_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success_criterion: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_killer: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="planned")

    hypothesis: Mapped[Hypothesis] = relationship(back_populates="roadmap_steps")
    artifacts: Mapped[list["ExperimentArtifact"]] = relationship(back_populates="roadmap_step")


class ExperimentArtifact(Base):
    __tablename__ = "experiment_artifact"
    __table_args__ = (
        CheckConstraint("outcome IN ('success','failure','partial')"),
        Index("idx_artifact_step", "roadmap_step_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    roadmap_step_id: Mapped[int] = mapped_column(ForeignKey("roadmap_step.id", ondelete="CASCADE"))
    hypothesis_id: Mapped[int] = mapped_column(ForeignKey("hypothesis.id", ondelete="CASCADE"))
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    measured_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    predicted_min: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    predicted_max: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    roadmap_step: Mapped[RoadmapStep] = relationship(back_populates="artifacts")
    hypothesis: Mapped[Hypothesis] = relationship()
    calibrations: Mapped[list["RuleCalibration"]] = relationship(back_populates="artifact")


class RuleCalibration(Base):
    __tablename__ = "rule_calibration"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("rule.id", ondelete="CASCADE"))
    artifact_id: Mapped[int] = mapped_column(ForeignKey("experiment_artifact.id", ondelete="CASCADE"))
    coeff_before: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    coeff_after: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    rule: Mapped[Rule] = relationship()
    artifact: Mapped[ExperimentArtifact] = relationship(back_populates="calibrations")


class CorpusDocument(Base):
    """Файл корпуса (PDF/DOCX). Хранит хэш для идемпотентной переиндексации."""

    __tablename__ = "corpus_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_file: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(Text, nullable=False)
    plant_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    n_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    chunks: Mapped[list["CorpusChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class CorpusChunk(Base):
    """Фрагмент корпуса с эмбеддингом (pgvector). Источник обоснований и
    литературной генерации — §4.2/§4.3 концепта. LLM здесь не считает числа."""

    __tablename__ = "corpus_chunk"
    __table_args__ = (Index("idx_corpus_chunk_doc", "document_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("corpus_document.id", ondelete="CASCADE")
    )
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    plant_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(get_settings().embed_dim))

    document: Mapped[CorpusDocument] = relationship(back_populates="chunks")


class DeadEnd(Base):
    __tablename__ = "dead_end"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int | None] = mapped_column(ForeignKey("module.id"), nullable=True)
    target_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_class_id: Mapped[int | None] = mapped_column(ForeignKey("size_class.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("experiment_artifact.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    module: Mapped[Module | None] = relationship()
    size_class: Mapped[SizeClass | None] = relationship()
    source_artifact: Mapped[ExperimentArtifact | None] = relationship()
