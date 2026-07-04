"""Литературная генерация гипотез (§4.3 концепта).

Для тяжёлых плохо покрытых ячеек матрицы потерь ищем в корпусе релевантные
фрагменты и просим LLM предложить конкретную гипотезу СО ССЫЛКАМИ на источник.
LLM предлагает только ИДЕЮ (в какую ячейку и каким рычагом бить) — числа эффекта
по-прежнему считает детерминированный движок, когда гипотезу принимают и оценивают.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .. import models
from ..engine import rank
from . import ollama_client, retriever

_CAUSE_MODULE = {"free": "classification", "locked": "regrind", "dispersed": "fine_flotation"}
_CAUSE_RU = {
    "free": "свободный, но не пойман флотацией",
    "locked": "заперт в сростках с породой",
    "dispersed": "рассеян в решётке минерала",
}
_FORM_RU = {
    "free_pnt": "свободный пентландит/халькопирит",
    "locked_pnt_cp": "запертые сростки пентландит/халькопирит",
    "pyrrhotite_assoc": "срастание с пирротином",
    "silicate_valleriite": "силикаты/валлериит",
}

_SYSTEM = (
    "Ты — инженер-технолог обогатительной фабрики. По приведённым фрагментам литературы "
    "предложи ОДНУ конкретную гипотезу, как снизить потери металла в указанной ячейке. "
    "Пиши по-русски, 2–3 предложения, опирайся ТОЛЬКО на фрагменты и ссылайся на источники [n]. "
    "НЕ приводи числовых оценок эффекта — их посчитает отдельный движок."
)


def propose(session: Session, plant_id: int, max_cells: int = 3, k: int = 4) -> list[dict]:
    _, coverage, _ = rank.build_coverage(session, plant_id)
    cov_by_id = {c.cell_id: float(c.coverage_share) for c in coverage}

    cells = session.query(models.LossCell).filter_by(plant_id=plant_id).all()
    recoverable = [c for c in cells if c.mineral_form.recoverable]
    # Кандидаты — тяжёлые и плохо покрытые ячейки.
    recoverable.sort(key=lambda c: (1.0 - cov_by_id.get(c.id, 0.0)) * float(c.tons), reverse=True)

    proposals: list[dict] = []
    for cell in recoverable[:max_cells]:
        form_ru = _FORM_RU.get(cell.mineral_form.code, cell.mineral_form.title)
        cause = cell.mineral_form.loss_cause
        query = (
            f"Снижение потерь {cell.metal.code} в классе крупности {cell.size_class.code}, "
            f"форма {form_ru}, причина: {_CAUSE_RU.get(cause, cause)}"
        )
        hits = retriever.search(session, query, k=k)
        if not hits:
            continue

        context = "\n\n".join(
            f"[{i}] {h.source_file}{f', стр. {h.page}' if h.page else ''}\n{h.content}"
            for i, h in enumerate(hits, 1)
        )
        try:
            rationale = ollama_client.chat(
                [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content":
                        f"Ячейка: {cell.metal.code}, класс {cell.size_class.code}, форма {form_ru}, "
                        f"причина потерь — {_CAUSE_RU.get(cause, cause)}.\n\n"
                        f"Фрагменты литературы:\n{context}\n\nПредложи гипотезу."},
                ]
            )
        except ollama_client.OllamaError as exc:
            rationale = f"(Ollama недоступен: {exc})"

        proposals.append({
            "cell_id": cell.id,
            "metal_code": cell.metal.code,
            "size_class_code": cell.size_class.code,
            "mineral_form_code": cell.mineral_form.code,
            "module_code": _CAUSE_MODULE.get(cause),
            # Заголовок в формате генерации — чтобы при принятии движок нашёл правило по классу.
            "suggested_title": (
                f"Литература: потери {cell.metal.code} в классе {cell.size_class.code}, "
                f"форма «{form_ru}»"
            ),
            "rationale": rationale.strip(),
            "citations": [{"n": i, **h.as_dict()} for i, h in enumerate(hits, 1)],
        })
    return proposals
