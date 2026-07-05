from __future__ import annotations

from decimal import Decimal
from typing import Any

from openai import OpenAI

from .config import get_settings
from .rag import yandex_client


SYSTEM_CARD_PROMPT = (
    "Ты оформляешь карточку гипотезы для НИОКР-комитета обогатительной фабрики. "
    "Пиши по-русски, структурно и кратко, деловым языком. "
    "Используй ТОЛЬКО переданные числа — не выдумывай новых цифр. "
    "Разделы карточки: \n"
    "1. Проблема — в какую ячейку потерь бьёт гипотеза.\n"
    "2. Механизм — как вмешательство возвращает металл.\n"
    "3. Ожидаемый эффект — диапазон в тоннах и деньгах (это потолок, не гарантия).\n"
    "4. Реализуемость — хватает ли оборудования.\n"
    "5. Обоснование — какое правило и какие ячейки использованы.\n"
    "6. Первый эксперимент — самый дешёвый способ проверить."
)


# ---------------------------------------------------------------------------
# Карточка гипотезы — генерация через YandexGPT (Yandex Cloud Foundation Models).
# Числа берутся из движка; LLM только оборачивает их в текст.
# ---------------------------------------------------------------------------
def build_hypothesis_card(context: dict[str, Any]) -> tuple[str, bool]:
    facts = _facts_block(context)
    try:
        text = yandex_client.chat(
            [
                {"role": "system", "content": SYSTEM_CARD_PROMPT},
                {"role": "user", "content": facts},
            ],
            temperature=0.2,
        )
        if not text.strip():
            raise yandex_client.YandexError("пустой ответ модели")
        return text.strip(), True
    except yandex_client.YandexError:
        return _fallback_card(context), False


def _facts_block(context: dict[str, Any]) -> str:
    data = _extract(context)
    lines = [
        f"Гипотеза: {data['title']}",
        f"Модуль (рычаг): {data['module']}",
        f"Целевая причина потерь: {data['cause_ru']}",
        "",
        "ЧИСЛА ДВИЖКА (использовать только их):",
        f"- Эффект, тонны металла: от {data['tons_min']} до {data['tons_max']}",
        f"- Эффект, деньги USD: от {data['usd_min']} до {data['usd_max']}",
        f"- Реализуемо на текущем оборудовании: {'да' if data['feasible'] else 'нет'}",
        f"- Правило: {data['rule_code']} (коэффициент {data['coeff_min']}–{data['coeff_max']})",
        f"- Требуемое оборудование: {data['required_equipment'] or '—'}",
        f"- Оборудование фабрики: {data['plant_equipment'] or '—'}",
        f"- Источник правила: {data['source'] or '—'}",
        "",
        "Целевые ячейки матрицы потерь:",
    ]
    for cell in data["cells"]:
        lines.append(
            f"  • {cell.get('metal_code')} в классе {cell.get('size_class_code')}, "
            f"форма «{_form_ru(cell.get('mineral_form_code'))}»: {cell.get('tons')} т потерь"
        )
    if data["killer_step"]:
        lines += ["", f"Первый (дешёвый) этап проверки: {data['killer_step']}"]
    return "\n".join(lines)


def _fallback_card(context: dict[str, Any]) -> str:
    d = _extract(context)
    cells = "\n".join(
        f"- {c.get('metal_code')} · класс {c.get('size_class_code')} · "
        f"форма «{_form_ru(c.get('mineral_form_code'))}» · {c.get('tons')} т"
        for c in d["cells"]
    ) or "- нет данных"
    return (
        f"### {d['title']}\n\n"
        f"**Проблема.** Гипотеза бьёт в потери типа «{d['cause_ru']}» "
        f"(модуль {d['module']}).\n\n"
        f"**Ожидаемый эффект (потолок, не гарантия):** "
        f"{d['tons_min']}–{d['tons_max']} т металла, "
        f"{d['usd_min']}–{d['usd_max']} USD.\n\n"
        f"**Реализуемость:** {'да' if d['feasible'] else 'нет'} "
        f"(нужно: {d['required_equipment'] or '—'}; есть: {d['plant_equipment'] or '—'}).\n\n"
        f"**Обоснование:** правило `{d['rule_code']}`, "
        f"коэффициент {d['coeff_min']}–{d['coeff_max']}. Целевые ячейки:\n{cells}\n\n"
        f"**Первый эксперимент:** {d['killer_step'] or 'самый дешёвый этап дорожной карты'} — "
        f"сверить факт с диапазоном движка.\n\n"
        f"_(Текст собран без LLM: Yandex недоступен. Все числа — из движка.)_"
    )


_CAUSE_RU = {
    "free": "свободный металл (не пойман флотацией)",
    "locked": "запертый в сростках (нужно доизмельчение)",
    "dispersed": "рассеянный в решётке минерала",
}
_FORM_RU = {
    "free_pnt": "свободный пентландит/халькопирит",
    "locked_pnt_cp": "запертые сростки пентландит/халькопирит",
    "pyrrhotite_assoc": "срастание с пирротином",
    "silicate_valleriite": "силикаты/валлериит",
}


def _form_ru(code: str | None) -> str:
    return _FORM_RU.get(code, code or "—")


def _extract(context: dict[str, Any]) -> dict[str, Any]:
    hypothesis = context.get("hypothesis") or {}
    evaluation = context.get("evaluation") or {}
    provenance = evaluation.get("provenance") or {}
    roadmap = context.get("roadmap") or []
    killer = next((s for s in roadmap if s.get("is_killer")), roadmap[0] if roadmap else None)
    return {
        "title": hypothesis.get("title", "Гипотеза"),
        "module": provenance.get("module") or hypothesis.get("module") or "—",
        "cause_ru": _CAUSE_RU.get(provenance.get("target_cause"), provenance.get("target_cause") or "—"),
        "tons_min": _num(evaluation.get("effect_tons_min")),
        "tons_max": _num(evaluation.get("effect_tons_max")),
        "usd_min": _num(evaluation.get("effect_usd_min")),
        "usd_max": _num(evaluation.get("effect_usd_max")),
        "feasible": bool(evaluation.get("feasible")),
        "rule_code": provenance.get("rule_code", "—"),
        "coeff_min": provenance.get("coeff_min", "—"),
        "coeff_max": provenance.get("coeff_max", "—"),
        "required_equipment": ", ".join(provenance.get("required_equipment") or []),
        "plant_equipment": ", ".join(provenance.get("plant_equipment") or []),
        "source": provenance.get("source"),
        "cells": provenance.get("target_cells") or [],
        "killer_step": (killer or {}).get("title") if killer else None,
    }


def _num(value: Any) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{Decimal(str(value)):.1f}"
    except Exception:  # noqa: BLE001
        return str(value)


# ---------------------------------------------------------------------------
# OpenRouter оставлен только для ассиста разбора входных таблиц (ingest).
# ---------------------------------------------------------------------------
def _client() -> OpenAI:
    settings = get_settings()
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    default_headers = {"X-OpenRouter-Title": settings.openrouter_app_title}
    if settings.openrouter_app_url:
        default_headers["HTTP-Referer"] = settings.openrouter_app_url

    return OpenAI(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_headers=default_headers,
    )


def complete_text(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    max_tokens: int = 900,
) -> str:
    settings = get_settings()
    response = _client().chat.completions.create(
        model=settings.openrouter_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def explain_table_structure(sample: dict[str, Any]) -> str:
    prompt = (
        "Определи, какие колонки таблицы соответствуют plant_code, metal_code, "
        "size_class_code, mineral_form_code и tons. Верни короткий JSON mapping."
    )
    return complete_text(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": str(sample)},
        ],
        temperature=0,
        max_tokens=500,
    )
