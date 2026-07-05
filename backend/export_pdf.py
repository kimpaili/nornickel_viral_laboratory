"""PDF-экспорт дашбордов (ТЗ V2 §11, §17 DoD; ТЗ V3 §4).

Тестировщик жмёт «Скачать PDF» на портфеле / карточке / диагнозе и получает файл
«как на экране», с теми же числами из БД, что и CSV. Реализация серверная (фронт —
Streamlit, а не React, поэтому html2canvas+jsPDF из ТЗ здесь неприменим), но результат
для жюри тот же: кнопка → PDF.

Кириллица рендерится через DejaVuSans, который уже поставляется вместе с matplotlib
(зависимость проекта) — отдельный шрифт бандлить не нужно, работает и в Docker.
"""

from __future__ import annotations

import io
from decimal import Decimal
from functools import lru_cache

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_FONT = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"


@lru_cache(maxsize=1)
def _ensure_fonts() -> None:
    """Регистрируем DejaVuSans из matplotlib — кроссплатформенно и без докачки шрифтов."""
    import matplotlib.font_manager as fm

    regular = fm.findfont("DejaVu Sans")
    try:
        bold = fm.findfont("DejaVu Sans:bold")
    except Exception:  # noqa: BLE001 - если жирного нет, используем обычный
        bold = regular
    pdfmetrics.registerFont(TTFont(_FONT, regular))
    pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold))


def _styles() -> dict[str, ParagraphStyle]:
    _ensure_fonts()
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "FHTitle", parent=base["Title"], fontName=_FONT_BOLD, fontSize=18, spaceAfter=6,
        textColor=colors.HexColor("#7B3FF2"),
    )
    sub = ParagraphStyle(
        "FHSub", parent=base["Normal"], fontName=_FONT, fontSize=9,
        textColor=colors.HexColor("#666666"), spaceAfter=10,
    )
    normal = ParagraphStyle("FHNormal", parent=base["Normal"], fontName=_FONT, fontSize=9)
    return {"title": title, "sub": sub, "normal": normal}


def _table(header: list[str], rows: list[list], col_widths: list[float] | None = None) -> Table:
    styles = _styles()
    data = [[Paragraph(str(h), styles["normal"]) for h in header]]
    for row in rows:
        data.append([Paragraph("" if v is None else str(v), styles["normal"]) for v in row])
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7B3FF2")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
                ("FONTNAME", (0, 1), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F0FB")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _build(title: str, subtitle: str, flowables: list, landscape_mode: bool = False) -> bytes:
    styles = _styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4) if landscape_mode else A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm,
        title=title,
    )
    story = [Paragraph(title, styles["title"]), Paragraph(subtitle, styles["sub"])]
    story.extend(flowables)
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def _fmt(value, digits: int = 1) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{Decimal(str(value)):,.{digits}f}".replace(",", " ")
    except Exception:  # noqa: BLE001
        return str(value)


def portfolio_pdf(plant_code: str, plant_title: str, evaluations: list) -> bytes:
    header = ["#", "Гипотеза", "Модуль", "Источник", "Эффект, т", "Эффект, $", "Реализ.", "Score", "Тупик"]
    rows = []
    for ev in evaluations:
        hypothesis = ev.hypothesis
        rows.append([
            ev.rank,
            hypothesis.title,
            hypothesis.module.code if hypothesis.module else "—",
            hypothesis.origin,
            _fmt(ev.effect_tons_max),
            _fmt(ev.effect_usd_max, 0),
            "да" if ev.feasible else "нет",
            _fmt(ev.relevance_score, 1),
            "да" if ev.dead_end_flag else "",
        ])
    widths = [10 * mm, 78 * mm, 24 * mm, 20 * mm, 22 * mm, 26 * mm, 16 * mm, 16 * mm, 14 * mm]
    flowables = [_table(header, rows, widths)] if rows else [
        Paragraph("Портфель пуст — оцените фабрику.", _styles()["normal"])
    ]
    return _build(
        "Портфель гипотез",
        f"Фабрика {plant_code} — {plant_title}. Все числа — из детерминированного движка.",
        flowables,
        landscape_mode=True,
    )


def matrix_pdf(plant_code: str, plant_title: str, cells: list) -> bytes:
    header = ["Металл", "Класс", "Форма", "Причина", "Извлекаемо", "Потери, т"]
    rows = [
        [
            cell.metal.code,
            cell.size_class.code,
            cell.mineral_form.code,
            cell.mineral_form.loss_cause,
            "да" if cell.mineral_form.recoverable else "нет (гидромет)",
            _fmt(cell.tons),
        ]
        for cell in cells
    ]
    flowables = [_table(header, rows)] if rows else [
        Paragraph("Матрица потерь пуста.", _styles()["normal"])
    ]
    return _build(
        "Матрица потерь",
        f"Фабрика {plant_code} — {plant_title}. Класс крупности × минеральная форма × металл.",
        flowables,
    )


def hypothesis_pdf(hypothesis_title: str, card_text: str, module_reports: list, roadmap: list) -> bytes:
    styles = _styles()
    flowables: list = []

    if card_text:
        for block in card_text.split("\n\n"):
            clean = block.replace("#", "").replace("**", "").strip()
            if clean:
                flowables.append(Paragraph(clean.replace("\n", "<br/>"), styles["normal"]))
                flowables.append(Spacer(1, 4))

    if module_reports:
        flowables.append(Spacer(1, 8))
        flowables.append(Paragraph("Разбивка по модулям", styles["title"]))
        header = ["Модуль", "Правило", "Коэффициент", "Эффект, т", "Эффект, $", "Выбран"]
        rows = [
            [
                rp.get("module_code"),
                rp.get("rule_code"),
                f"{rp.get('coeff_min')}–{rp.get('coeff_max')}",
                _fmt(rp.get("effect_tons_max")),
                _fmt(rp.get("effect_usd_max"), 0),
                "да" if rp.get("selected") else "",
            ]
            for rp in module_reports
        ]
        flowables.append(_table(header, rows))

    if roadmap:
        flowables.append(Spacer(1, 8))
        flowables.append(Paragraph("Дорожная карта эксперимента", styles["title"]))
        header = ["Этап", "Задача", "Стоимость, $", "Дней", "Killer"]
        rows = [
            [
                step.step_order,
                step.title,
                _fmt(step.cost, 0),
                step.duration_days,
                "да" if step.is_killer else "",
            ]
            for step in roadmap
        ]
        flowables.append(_table(header, rows))

    if not flowables:
        flowables = [Paragraph("По гипотезе пока нет оценки и дорожной карты.", styles["normal"])]

    return _build("Карточка гипотезы", hypothesis_title, flowables)
