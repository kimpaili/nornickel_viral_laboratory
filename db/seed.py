from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import models  # noqa: E402
from backend.db import SessionLocal, init_db  # noqa: E402


def seed() -> None:
    init_db()
    with SessionLocal() as session:
        mineral_forms = _seed_mineral_forms(session)
        size_classes = _seed_size_classes(session)
        metals = _seed_metals(session)
        plants = _seed_plants(session)
        modules = _seed_modules(session)

        _seed_equipment(session, plants)
        _seed_rules(session, modules, size_classes)
        _seed_loss_cells(session, plants, metals, size_classes, mineral_forms)
        _seed_hypotheses(session, plants, modules)
        session.commit()


def _seed_mineral_forms(session):
    rows = [
        ("free_pnt", "Свободный пентландит/халькопирит", "free", True),
        ("locked_pnt_cp", "Запертые сростки пентландит/халькопирит", "locked", True),
        ("pyrrhotite_assoc", "Металл в срастании с пирротином", "dispersed", True),
        ("silicate_valleriite", "Силикаты / валлериит", "dispersed", False),
    ]
    return {
        code: _upsert(
            session,
            models.MineralForm,
            code,
            title=title,
            loss_cause=cause,
            recoverable=recoverable,
        )
        for code, title, cause, recoverable in rows
    }


def _seed_size_classes(session):
    rows = [
        ("+125", Decimal("125"), None, 1),
        ("-125+71", Decimal("71"), Decimal("125"), 2),
        ("-71+45", Decimal("45"), Decimal("71"), 3),
        ("-45+20", Decimal("20"), Decimal("45"), 4),
        ("-20+10", Decimal("10"), Decimal("20"), 5),
        ("-10", None, Decimal("10"), 6),
    ]
    return {
        code: _upsert(
            session,
            models.SizeClass,
            code,
            microns_lo=lo,
            microns_hi=hi,
            sort_order=sort_order,
        )
        for code, lo, hi, sort_order in rows
    }


def _seed_metals(session):
    rows = [
        ("Ni", "Никель", Decimal("18000")),
        ("Cu", "Медь", Decimal("9000")),
    ]
    return {
        code: _upsert(session, models.Metal, code, title=title, price_usd_t=price)
        for code, title, price in rows
    }


def _seed_plants(session):
    rows = [
        ("NOF", "Норильская обогатительная (демо)", Decimal("1200000"), Decimal("360000")),
        ("KGMK", "Кольская ГМК (демо)", Decimal("940000"), Decimal("280000")),
    ]
    return {
        code: _upsert(
            session,
            models.Plant,
            code,
            title=title,
            feed_smt=feed,
            tailings_smt=tailings,
        )
        for code, title, feed, tailings in rows
    }


def _seed_modules(session):
    rows = [
        ("regrind", "Доизмельчение", "Доп. раскрытие запертых сростков"),
        ("classification", "Классификация", "Перенаправление неверно расклассифицированных классов"),
        ("fine_flotation", "Флотация тонких классов", "Возврат тонких/рассеянных потерь"),
    ]
    return {
        code: _upsert(session, models.Module, code, title=title, description=description)
        for code, title, description in rows
    }


def _seed_equipment(session, plants):
    rows = [
        ("NOF", "mill", "MShTs 4.5x6.0", 2),
        ("NOF", "hydrocyclone", "GC-660", 8),
        ("NOF", "flotation", "RIF-25", 12),
        ("KGMK", "mill", "MShR 3.6x5.0", 2),
        ("KGMK", "hydrocyclone", "GC-500", 6),
        ("KGMK", "classifier", "Spiral classifier", 3),
        ("KGMK", "flotation", "TankCell demo", 10),
        ("KGMK", "screen", "Fine screen", 2),
    ]
    for plant_code, kind, model, qty in rows:
        existing = (
            session.query(models.Equipment)
            .filter_by(plant_id=plants[plant_code].id, kind=kind, model=model)
            .one_or_none()
        )
        if existing:
            existing.qty = qty
        else:
            session.add(
                models.Equipment(
                    plant_id=plants[plant_code].id,
                    kind=kind,
                    model=model,
                    qty=qty,
                )
            )


def _seed_rules(session, modules, size_classes):
    rows = [
        ("regrind_locked_plus125", "regrind", "locked", "+125", "0.20", "0.12", "0.28", "slimes growth risk", "mill"),
        ("regrind_locked_71_45", "regrind", "locked", "-71+45", "0.11", "0.07", "0.16", "energy growth", "mill"),
        ("regrind_locked_any", "regrind", "locked", None, "0.08", "0.04", "0.12", "overgrinding risk", "mill"),
        ("classification_free_any", "classification", "free", None, "0.06", "0.03", "0.10", "circulating load shift", "hydrocyclone"),
        ("classification_free_minus10", "classification", "free", "-10", "0.04", "0.02", "0.08", "slime bypass risk", "hydrocyclone"),
        ("fine_flotation_disp_45_20", "fine_flotation", "dispersed", "-45+20", "0.08", "0.04", "0.12", "reagent consumption growth", "flotation"),
        ("fine_flotation_disp_20_10", "fine_flotation", "dispersed", "-20+10", "0.06", "0.03", "0.10", "froth stability risk", "flotation"),
    ]
    for code, module_code, cause, size_code, coeff, coeff_min, coeff_max, side_effect, requires in rows:
        _upsert(
            session,
            models.Rule,
            code,
            module_id=modules[module_code].id,
            target_cause=cause,
            target_size_class_id=size_classes[size_code].id if size_code else None,
            coeff=Decimal(coeff),
            coeff_min=Decimal(coeff_min),
            coeff_max=Decimal(coeff_max),
            side_effect=side_effect,
            requires_kind=requires,
            source="MVP seed: expert prior, calibrate after lab artifact",
        )


def _seed_loss_cells(session, plants, metals, size_classes, mineral_forms):
    rows = [
        ("NOF", "Ni", "+125", "locked_pnt_cp", "120"),
        ("NOF", "Ni", "-71+45", "locked_pnt_cp", "95"),
        ("NOF", "Ni", "-45+20", "pyrrhotite_assoc", "80"),
        ("NOF", "Ni", "-20+10", "pyrrhotite_assoc", "65"),
        ("NOF", "Ni", "-10", "silicate_valleriite", "50"),
        ("NOF", "Cu", "-10", "free_pnt", "25"),
        ("KGMK", "Ni", "+125", "locked_pnt_cp", "60"),
        ("KGMK", "Ni", "-71+45", "locked_pnt_cp", "140"),
        ("KGMK", "Ni", "-45+20", "pyrrhotite_assoc", "70"),
        ("KGMK", "Ni", "-20+10", "pyrrhotite_assoc", "40"),
        ("KGMK", "Cu", "-125+71", "free_pnt", "90"),
        ("KGMK", "Cu", "-10", "free_pnt", "30"),
    ]
    for plant_code, metal_code, size_code, form_code, tons in rows:
        _upsert_loss_cell(
            session,
            plant_id=plants[plant_code].id,
            metal_id=metals[metal_code].id,
            size_class_id=size_classes[size_code].id,
            mineral_form_id=mineral_forms[form_code].id,
            tons=Decimal(tons),
        )


def _seed_hypotheses(session, plants, modules):
    rows = [
        ("NOF", "regrind", "Доизмельчение крупных запертых сростков никеля"),
        ("NOF", "fine_flotation", "Возврат тонкого рассеянного никеля флотацией"),
        ("KGMK", "regrind", "Доизмельчение крупных запертых сростков никеля"),
        ("KGMK", "classification", "Снижение потерь меди настройкой классификации"),
    ]
    for plant_code, module_code, title in rows:
        existing = (
            session.query(models.Hypothesis)
            .filter_by(plant_id=plants[plant_code].id, title=title)
            .one_or_none()
        )
        if not existing:
            session.add(
                models.Hypothesis(
                    plant_id=plants[plant_code].id,
                    module_id=modules[module_code].id,
                    title=title,
                    origin="expert",
                )
            )


def _upsert(session, model, code: str, **values):
    item = session.query(model).filter_by(code=code).one_or_none()
    if item is None:
        item = model(code=code, **values)
        session.add(item)
    else:
        for key, value in values.items():
            setattr(item, key, value)
    session.flush()
    return item


def _upsert_loss_cell(session, **values):
    existing = (
        session.query(models.LossCell)
        .filter_by(
            plant_id=values["plant_id"],
            metal_id=values["metal_id"],
            size_class_id=values["size_class_id"],
            mineral_form_id=values["mineral_form_id"],
        )
        .one_or_none()
    )
    if existing:
        existing.tons = values["tons"]
    else:
        session.add(models.LossCell(**values))


if __name__ == "__main__":
    seed()
