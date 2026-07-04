from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import BinaryIO

from sqlalchemy.orm import Session

from . import models


@dataclass(frozen=True)
class ParsedLossCell:
    metal_code: str
    size_class_code: str
    mineral_form_code: str
    tons: Decimal
    source: dict[str, str | int]


ALIASES = {
    "metal_code": {"metal", "metal_code", "металл", "код металла"},
    "size_class_code": {"size", "size_class", "size_class_code", "класс", "класс крупности"},
    "mineral_form_code": {"form", "mineral_form", "mineral_form_code", "форма", "минеральная форма"},
    "tons": {"tons", "loss_tons", "потери", "потери т", "тонн", "т"},
}


def parse_loss_cells_from_xlsx(file: BinaryIO, filename: str) -> tuple[list[ParsedLossCell], list[str]]:
    import pandas as pd

    warnings: list[str] = []
    parsed: list[ParsedLossCell] = []
    workbook = pd.read_excel(file, sheet_name=None)

    for sheet_name, frame in workbook.items():
        mapping = _resolve_columns(frame.columns)
        missing = [key for key in ALIASES if key not in mapping]
        if missing:
            warnings.append(
                f"{filename}:{sheet_name}: skipped, missing columns {', '.join(missing)}"
            )
            continue

        for row_index, row in frame.iterrows():
            try:
                tons = Decimal(str(row[mapping["tons"]]))
            except Exception:
                warnings.append(f"{filename}:{sheet_name}:{row_index + 2}: bad tons value")
                continue

            if tons <= 0:
                continue

            parsed.append(
                ParsedLossCell(
                    metal_code=str(row[mapping["metal_code"]]).strip(),
                    size_class_code=str(row[mapping["size_class_code"]]).strip(),
                    mineral_form_code=str(row[mapping["mineral_form_code"]]).strip(),
                    tons=tons,
                    source={"file": filename, "sheet": sheet_name, "row": int(row_index) + 2},
                )
            )

    return parsed, warnings


def upsert_loss_cells(
    session: Session,
    *,
    plant_id: int,
    rows: list[ParsedLossCell],
) -> int:
    count = 0
    for row in rows:
        metal = _get_by_code(session, models.Metal, row.metal_code)
        size_class = _get_by_code(session, models.SizeClass, row.size_class_code)
        mineral_form = _get_by_code(session, models.MineralForm, row.mineral_form_code)

        existing = (
            session.query(models.LossCell)
            .filter_by(
                plant_id=plant_id,
                metal_id=metal.id,
                size_class_id=size_class.id,
                mineral_form_id=mineral_form.id,
            )
            .one_or_none()
        )
        if existing:
            existing.tons = row.tons
        else:
            session.add(
                models.LossCell(
                    plant_id=plant_id,
                    metal_id=metal.id,
                    size_class_id=size_class.id,
                    mineral_form_id=mineral_form.id,
                    tons=row.tons,
                )
            )
        count += 1

    return count


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    normalized = {_normalize(column): column for column in columns}
    mapping: dict[str, str] = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            if _normalize(alias) in normalized:
                mapping[field] = normalized[_normalize(alias)]
                break
    return mapping


def _normalize(value: object) -> str:
    return str(value).strip().lower().replace(",", "").replace(".", "")


def _get_by_code(session: Session, model: type, code: str):
    item = session.query(model).filter_by(code=code).one_or_none()
    if not item:
        raise ValueError(f"Unknown {model.__name__} code: {code}")
    return item
