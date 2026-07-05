"""Лаборатория гипотез — фронтенд (§16 ТЗ).

Принцип: сложность — в структуре, простота — в каждом экране.
Слева — «позвоночник» из стадий (всегда виден = масштаб системы).
Сверху — тонкая панель: фабрика, пример, свои данные.
Один экран = одно действие. Глубина — по клику, а не текстом.
Единый визуальный язык трёх цветов: бирюза (свободный/флотация),
янтарь (заперт/измельчение), серый (рассеян/тонкие).
"""

import os
from decimal import Decimal
from pathlib import Path

import pandas as pd
import requests
import streamlit as st


API_BASE = os.getenv("API_BASE", "http://localhost:8000")
ASSETS = Path(__file__).parent / "assets"
LOGO = str(ASSETS / "logo.png")
ICON = str(ASSETS / "icon.png")

BRAND_GREEN = "#00FFBF"
BRAND_VIOLET = "#7B3FF2"

st.set_page_config(
    page_title="Лаборатория гипотез",
    layout="wide",
    page_icon=ICON if os.path.exists(ICON) else "⚗️",
    initial_sidebar_state="expanded",
)
# Маленький логотип в углу, когда сайдбар свёрнут (Streamlit показывает его только
# в свёрнутом состоянии).
if os.path.exists(LOGO):
    st.logo(LOGO, size="large")


# ============================================================================
# Визуальный язык: три цвета причин потерь. Используется ВЕЗДЕ — матрица, чипы,
# модули, карта покрытия. Понял язык один раз — читаешь всё без подписей.
# ============================================================================
CAUSE = {
    "free": {"ru": "Свободный", "lever": "флотация", "hex": "#00E6AC"},
    "locked": {"ru": "Заперт", "lever": "измельчение", "hex": "#E6A23C"},
    "dispersed": {"ru": "Рассеян", "lever": "тонкие классы", "hex": "#8A9199"},
}
MODULE_RU = {"regrind": "Доизмельчение", "classification": "Классификация",
             "fine_flotation": "Флотация тонких"}
MODULE_CAUSE = {"regrind": "locked", "classification": "free", "fine_flotation": "dispersed"}
MINERAL_FORM_RU = {
    "free_pnt": "Свободный Pn/Cp", "locked_pnt_cp": "Запертые сростки Pn/Cp",
    "pyrrhotite_assoc": "Срастание с пирротином", "silicate_valleriite": "Силикаты/валлериит",
}
EQUIP_RU = {"mill": "мельница", "hydrocyclone": "гидроциклон", "classifier": "классификатор",
            "flotation": "флотомашина", "screen": "грохот", "magnetic": "магнитный сепаратор"}
ORIGIN_RU = {"generated": "система", "expert": "эксперт"}
STATUS_RU = {"new": "новая", "evaluated": "оценена", "in_roadmap": "в карте",
             "confirmed": "подтверждена", "rejected": "тупик"}
OUTCOME_RU = {"success": "успех", "partial": "частично", "failure": "провал"}
SIZE_ORDER = ["+125", "-125+71", "-71+45", "-45+20", "-20+10", "-10"]

st.markdown(
    f"""
    <style>
      section[data-testid="stSidebar"] {{ border-right: 1px solid {BRAND_VIOLET}33; }}
      div[data-testid="stMetricValue"] {{ color: {BRAND_GREEN}; }}
      .stTabs [aria-selected="true"] {{ color: {BRAND_GREEN} !important; }}
      .chip {{ display:inline-block; padding:2px 10px; border-radius:12px;
               font-size:0.78rem; font-weight:600; color:#0b0f17; }}
      .fh-title {{ font-size:2.0rem; font-weight:800;
        background:linear-gradient(90deg,{BRAND_GREEN},{BRAND_VIOLET});
        -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text; }}
      .matrix {{ border-collapse:collapse; width:100%; font-size:0.8rem; }}
      .matrix td, .matrix th {{ padding:6px 8px; text-align:center; border:1px solid #ffffff12; }}
      .matrix th {{ color:#9aa4ad; font-weight:600; }}
      .step {{ border-radius:10px; padding:10px 14px; margin:4px 0;
               background:#151b28; border-left:4px solid #ffffff22; }}
      /* Убрать кнопку «На весь экран» у картинок/таблиц */
      button[title="View fullscreen"],
      [data-testid="StyledFullScreenButton"],
      [data-testid="stElementToolbar"] {{ display: none !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================================
# HTTP
# ============================================================================
def api(method: str, path: str, **kwargs):
    try:
        r = requests.request(method, f"{API_BASE}{path}", timeout=120, **kwargs)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as exc:
        detail = None
        try:
            detail = exc.response.json().get("detail")
        except Exception:  # noqa: BLE001
            detail = exc.response.text if exc.response is not None else None
        st.error(f"⚠️ {detail or exc}")
        return None
    except requests.RequestException as exc:
        st.error(f"API недоступен ({API_BASE}{path}): {exc}")
        st.stop()


def api_bytes(path: str) -> bytes:
    try:
        r = requests.get(f"{API_BASE}{path}", timeout=120)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return b""


# ============================================================================
# Форматирование и единый визуальный язык
# ============================================================================
def fmt_t(v):
    return "—" if v in (None, "") else f"{Decimal(str(v)):,.1f} т".replace(",", " ")


def fmt_usd(v):
    return "—" if v in (None, "") else f"${Decimal(str(v)):,.0f}".replace(",", " ")


def chip(cause: str) -> str:
    c = CAUSE.get(cause, {"ru": cause, "hex": "#8A9199"})
    return f'<span class="chip" style="background:{c["hex"]}">{c["ru"]}</span>'


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def size_key(code: str) -> int:
    return SIZE_ORDER.index(code) if code in SIZE_ORDER else 99


def help_box(body: str):
    with st.expander("❓ Как читать этот экран"):
        st.markdown(body)


def show_table(rows, columns, translate=None, round_cols=None):
    if not rows:
        st.caption("Пока пусто.")
        return
    df = pd.DataFrame(rows)
    for col, mp in (translate or {}).items():
        if col in df.columns:
            df[col] = df[col].map(mp) if callable(mp) else df[col].map(lambda v: mp.get(v, v))
    for col, d in (round_cols or {}).items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(d)
    keep = [c for c in columns if c in df.columns]
    st.dataframe(df[keep].rename(columns=columns), use_container_width=True, hide_index=True)


def render_matrix(cells: list[dict]) -> str:
    """Матрица потерь как тепловая таблица в цветах причин (визуальный якорь §16)."""
    sizes = sorted({c["size_class_code"] for c in cells}, key=size_key)
    cols = sorted({(c["metal_code"], c["mineral_form_code"], c["loss_cause"]) for c in cells},
                  key=lambda x: (x[0], list(CAUSE).index(x[2]) if x[2] in CAUSE else 9))
    grid = {(c["size_class_code"], c["metal_code"], c["mineral_form_code"]): c for c in cells}
    maxt = max((float(c["tons"]) for c in cells), default=1) or 1

    head = "".join(
        f'<th>{m}<br><span style="color:{CAUSE[ca]["hex"]}">▉</span> '
        f'{MINERAL_FORM_RU.get(f, f)}</th>'
        for m, f, ca in cols
    )
    body = ""
    for s in sizes:
        row = f"<th>{s}</th>"
        for m, f, ca in cols:
            cell = grid.get((s, m, f))
            if cell:
                t = float(cell["tons"])
                bg = _rgba(CAUSE[ca]["hex"], 0.12 + 0.85 * t / maxt)
                row += f'<td style="background:{bg};color:#0b0f17;font-weight:600">{t:.0f}</td>'
            else:
                row += '<td style="color:#3a424c">·</td>'
        body += f"<tr>{row}</tr>"
    return f'<table class="matrix"><tr><th>класс \\ форма</th>{head}</tr>{body}</table>'


def render_clickable_matrix(cells: list[dict]) -> int | None:
    sizes = sorted({c["size_class_code"] for c in cells}, key=size_key)
    cols = sorted(
        {(c["metal_code"], c["mineral_form_code"], c["loss_cause"]) for c in cells},
        key=lambda x: (x[0], list(CAUSE).index(x[2]) if x[2] in CAUSE else 9),
    )
    grid = {(c["size_class_code"], c["metal_code"], c["mineral_form_code"]): c for c in cells}
    st.caption("Нажми на число в ячейке — ниже появятся гипотезы, которые закрывают эту потерю.")
    header = st.columns([1.1] + [1 for _ in cols])
    header[0].markdown("**класс**")
    for col, (metal, form, cause) in zip(header[1:], cols):
        col.markdown(
            f'<span style="color:{CAUSE[cause]["hex"]}">■</span> **{metal}**<br>'
            f'<span style="font-size:0.75rem">{MINERAL_FORM_RU.get(form, form)}</span>',
            unsafe_allow_html=True,
        )
    selected = st.session_state.get("selected_loss_cell")
    for size in sizes:
        row = st.columns([1.1] + [1 for _ in cols])
        row[0].markdown(f"**{size}**")
        for col, (metal, form, cause) in zip(row[1:], cols):
            cell = grid.get((size, metal, form))
            if not cell:
                col.caption("·")
                continue
            label = f"{float(cell['tons']):.0f}"
            if cell.get("hydromet_candidate"):
                label += "\nгидромет"
            if col.button(
                label,
                key=f"loss_cell_{cell['id']}",
                use_container_width=True,
                type="primary" if selected == cell["id"] else "secondary",
            ):
                st.session_state["selected_loss_cell"] = cell["id"]
                selected = cell["id"]
    return selected


def render_module_reports(reports: list[dict]):
    """Мини-отчёт по каждому модулю — прозрачность движка (§5 ТЗ)."""
    if not reports:
        st.caption("Нет разбивки — оцени фабрику на «Оценке».")
        return
    for rp in reports:
        cause = rp.get("target_cause")
        sel = "🎯 выбран движком" if rp.get("selected") else "рассмотрен"
        feas = "✅ реализуемо" if rp.get("feasible") else "⛔ нет оборудования"
        st.markdown(
            f'{chip(cause)} **{MODULE_RU.get(rp["module_code"], rp["module_code"])}** · '
            f"{sel} · {feas}", unsafe_allow_html=True)
        st.caption(
            f'Правило `{rp.get("rule_code")}` · коэффициент '
            f'{rp.get("coeff_min")}–{rp.get("coeff_max")} · класс {rp.get("target_size_class") or "любой"}')
        if rp.get("coeff_explanation"):
            st.info(f"Кривая извлечения: {rp['coeff_explanation']}")
        if rp.get("selection_reason"):
            st.caption(f"Вывод движка: {rp['selection_reason']}")
        a, b, c, d = st.columns(4)
        a.metric("Эффект, т", f"{float(rp.get('effect_tons_max') or 0):.1f}")
        b.metric("Эффект, $", fmt_usd(rp.get("effect_usd_max")))
        c.metric("P(успех)", f"{float(rp.get('success_probability') or 0):.0%}")
        d.metric("Вклад в Score", f"{float(rp.get('relevance_contribution') or 0):.1f}")
        if rp.get("money_formula"):
            st.caption(f"Деньги: {rp['money_formula']}")
        tc = rp.get("target_cells") or []
        if tc:
            show_table(tc, {"metal_code": "Металл", "size_class_code": "Класс",
                            "mineral_form_code": "Форма", "tons": "Потери, т",
                            "curve_coeff_max": "k кривой", "effect_tons_max": "Возврат, т",
                            "effect_usd_max": "$"},
                       translate={"mineral_form_code": MINERAL_FORM_RU},
                       round_cols={"tons": 1, "curve_coeff_max": 4,
                                   "effect_tons_max": 2, "effect_usd_max": 0})
        breakdown = rp.get("score_breakdown") or {}
        if breakdown:
            with st.expander("Как вклад вошёл в итоговый приоритет"):
                rows = [
                    {"Компонент": "Деньги", "Значение": breakdown.get("usd_component")},
                    {"Компонент": "Тонны", "Значение": breakdown.get("tons_component")},
                    {"Компонент": "Вероятность", "Значение": breakdown.get("probability_component")},
                    {"Компонент": "Покрытие", "Значение": breakdown.get("coverage_component")},
                    {"Компонент": "Реализуемость", "Значение": breakdown.get("feasible_component")},
                    {"Компонент": "Штраф риска", "Значение": breakdown.get("risk_component")},
                    {"Компонент": "Штраф конфликта", "Значение": breakdown.get("conflict_component")},
                    {"Компонент": "Штраф тупика", "Значение": breakdown.get("dead_end_component")},
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.divider()


# ============================================================================
# Данные
# ============================================================================
plants = api("GET", "/plants")
if not plants:
    st.warning("В базе нет фабрик. Запусти seed: `docker compose --profile tools run --rm seed`")
    st.stop()


# ============================================================================
# ПОЗВОНОЧНИК (сайдбар) — 6 стадий всегда видны
# ============================================================================
if os.path.exists(LOGO):
    st.sidebar.image(LOGO, use_container_width=True)

STAGES = [
    ("🔬 Диагноз", "diag"),
    ("💡 Гипотезы", "hyp"),
    ("📊 Оценка", "rank"),
    ("🗂️ Карточка", "card"),
    ("🗺️ Дорожная карта", "road"),
    ("🧪 Лаборатория", "lab"),
    ("📚 Литература", "lit"),
]
st.sidebar.markdown("### Стадии")
labels = [s[0] for s in STAGES]
choice = st.sidebar.radio("Стадия", labels, label_visibility="collapsed")
stage = dict((l, k) for l, k in STAGES)[choice]

st.sidebar.markdown("### Язык цвета")
st.sidebar.markdown(
    "".join(
        f'<div style="margin:3px 0">{chip(k)} '
        f'<span style="color:#9aa4ad;font-size:0.8rem">{v["lever"]}</span></div>'
        for k, v in CAUSE.items()
    ),
    unsafe_allow_html=True,
)
# ============================================================================
# ТОНКАЯ ВЕРХНЯЯ ПАНЕЛЬ — фабрика, пример, свои данные
# ============================================================================
st.markdown('<div class="fh-title">Лаборатория гипотез</div>', unsafe_allow_html=True)
st.caption("Где теряется металл • как его вернуть • как учиться на опытах. "
           "Все числа — из детерминированного движка, не из LLM.")

bar = st.container(border=True)
with bar:
    c1, c2, c3, c4 = st.columns([2.2, 1.4, 1.2, 1.2])
    with c1:
        plabels = {f"{p['code']} — {p['title']}": p for p in plants}
        plant = plabels[st.selectbox("🏭 Фабрика", list(plabels), label_visibility="collapsed")]
        plant_id = plant["id"]
    with c2:
        st.caption(f"Питание {plant.get('feed_smt') or '—'} • хвосты {plant.get('tailings_smt') or '—'} тыс.т")
    with c3:
        if st.button("▶️ Загрузить пример", use_container_width=True,
                     help="Оценивает обе предзагруженные фабрики — демо готово к клику."):
            for p in plants:
                api("POST", f"/plants/{p['id']}/evaluate")
            st.toast("Обе фабрики оценены — открой «Оценку».", icon="✅")
    with c4:
        with st.popover("📤 Свои данные", use_container_width=True):
            st.markdown("**Единый вход V3: файлы + промты**")
            task_prompt = st.text_area(
                "Общий промт",
                "Цель: найти реализуемые гипотезы снижения потерь. Учесть ограничения по оборудованию и бюджету.",
                height=90,
            )
            uploads = st.file_uploader(
                "Файлы",
                type=["xlsx", "docx", "pdf", "png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
                help="XLSX парсится кодом; PDF/DOCX идут в корпус; картинки сохраняются с промптом-инструкцией.",
            )
            file_prompts = []
            for idx, upload in enumerate(uploads or []):
                file_prompts.append(
                    st.text_input(
                        f"Промт к файлу: {upload.name}",
                        "основная матрица потерь" if upload.name.lower().endswith("xlsx")
                        else "в корпус, источник обоснований",
                        key=f"bundle_prompt_{idx}_{upload.name}",
                    )
                )
            if uploads and st.button("Загрузить пакет", key="b_bundle", use_container_width=True):
                multipart = [
                    ("files", (upload.name, upload.getvalue(), upload.type or "application/octet-stream"))
                    for upload in uploads
                ]
                data = [("task_prompt", task_prompt)] + [("prompts", prompt) for prompt in file_prompts]
                r = api("POST", f"/plants/{plant_id}/ingest-bundle", data=data, files=multipart)
                if r:
                    st.success("Пакет обработан.")
                    st.text(r["understood_summary"])
                    for item in r["results"]:
                        status = "✅" if item["status"] == "ok" else "⚠️"
                        st.caption(f"{status} {item['filename']}: {item['detail']}")

st.write("")


# ============================================================================
# СТАДИИ
# ============================================================================
def stage_diagnosis():
    st.subheader("🔬 Диагноз потерь")
    help_box("Матрица хвостов фабрики: сколько металла теряется в каждой ячейке "
             "**класс крупности × минеральная форма** и сколько из этого извлекаемо. "
             "Цвет = причина потери. Чем насыщеннее — тем тяжелее ячейка. Клик по ячейке ниже → "
             "какие гипотезы её закрывают.")
    data = api("GET", f"/plants/{plant_id}/diagnosis")
    if not data:
        st.stop()

    a, b = st.columns(2)
    a.metric("♻️ Извлекаемые потери", fmt_t(data["recoverable_tons"]))
    b.metric("⛔ Неизвлекаемые потери", fmt_t(data["unrecoverable_tons"]))

    st.markdown("**Матрица потерь**")
    st.markdown(render_matrix(data["cells"]), unsafe_allow_html=True)
    cid = render_clickable_matrix(data["cells"])
    dl1, dl2 = st.columns(2)
    dl1.download_button("⬇️ Матрица (CSV)", api_bytes(f"/export/matrix.csv?plant_id={plant_id}"),
                        file_name=f"matrix_{plant['code']}.csv", mime="text/csv",
                        use_container_width=True)
    dl2.download_button("📄 Матрица (PDF)", api_bytes(f"/export/matrix.pdf?plant_id={plant_id}"),
                        file_name=f"matrix_{plant['code']}.pdf", mime="application/pdf",
                        use_container_width=True)

    hydromet = [c for c in data["cells"] if c.get("hydromet_candidate")]
    if hydromet:
        with st.expander("🧪 Кандидаты в другой передел: гидрометаллургия / автоклав"):
            show_table(
                hydromet,
                {"metal_code": "Металл", "size_class_code": "Класс",
                 "mineral_form_code": "Форма", "tons": "Потери, т"},
                translate={"mineral_form_code": MINERAL_FORM_RU},
                round_cols={"tons": 1},
            )

    st.markdown("**🖱️ Выбранная ячейка → какие гипотезы её закрывают**")
    rk = api("GET", f"/plants/{plant_id}/ranking") or {"items": [], "coverage_cells": []}
    by_id = {it["hypothesis_id"]: it for it in rk["items"]}
    cov = {c["cell_id"]: c for c in rk["coverage_cells"]}
    if cid:
        cc = cov.get(cid)
        if cc and cc.get("covered_by_hypotheses"):
            for hid in cc["covered_by_hypotheses"]:
                it = by_id.get(hid, {})
                tag = "🔴 спорная (конфликт)" if cc.get("contested") else "🟢 закрыта"
                st.markdown(f"- **{it.get('title', f'Гипотеза №{hid}')}** — "
                            f"{fmt_t(it.get('effect_tons_max'))} · {tag}")
        else:
            st.info("Ячейку пока не закрывает ни одна реализуемая гипотеза. "
                    "Сгенерируй гипотезы (стадия «Гипотезы») и оцени фабрику.")
    else:
        st.info("Выбери ячейку кликом по матрице выше.")


def stage_hypotheses():
    st.subheader("💡 Гипотезы улучшения")
    help_box("Две колоды идей: **🤖 система** подбирает рычаги под самые тяжёлые ячейки "
             "(и сверяется с базой тупиков), **👤 эксперт** добавляет свои. Оба источника "
             "оцениваются одним движком наравне. DOCX «мозгового штурма» грузится сверху («Свои данные»).")

    mode = st.radio("Режим", ["📋 Мои гипотезы", "✨ Предложить новые"], horizontal=True,
                    label_visibility="collapsed")

    if mode.startswith("✨"):
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            st.markdown("**🤖 Система — по тяжёлым ячейкам**")
            n = st.slider("Сколько", 1, 20, 5, label_visibility="collapsed")
            if st.button("⚙️ Сгенерировать", use_container_width=True):
                g = api("POST", f"/plants/{plant_id}/generate", params={"limit": n})
                if g:
                    st.success(f"Создано: {g['created']} · отброшено тупиков: {g['skipped_dead_ends']}.")
        with c2.container(border=True):
            st.markdown("**👤 Эксперт — своя формулировка**")
            with st.form("expert"):
                t = st.text_input("Формулировка", "Доизмельчение крупных запертых сростков никеля")
                m = st.selectbox("Рычаг", list(MODULE_RU), format_func=lambda v: MODULE_RU[v])
                if st.form_submit_button("➕ Добавить", use_container_width=True):
                    r = api("POST", "/hypotheses/ingest",
                            json={"plant_id": plant_id, "title": t, "module_code": m, "origin": "expert"})
                    if r:
                        st.success(f"Добавлена гипотеза №{r['id']}.")
        st.divider()
        st.markdown("**📖 Из литературы (LLM + корпус)** — идея со ссылками; числа посчитает движок.")
        if st.button("📖 Предложить из литературы", use_container_width=True):
            with st.spinner("Ищу в корпусе через Yandex…"):
                res = api("POST", f"/plants/{plant_id}/literature-hypotheses")
            if res:
                st.session_state[f"lit_{plant_id}"] = res["proposals"]
        for i, p in enumerate(st.session_state.get(f"lit_{plant_id}", [])):
            with st.container(border=True):
                st.markdown(f"**{p['suggested_title']}** · {MODULE_RU.get(p['module_code'], p['module_code'])}")
                st.write(p["rationale"])
                for c in p["citations"]:
                    st.caption(f"[{c['n']}] {c['source_file']}"
                               + (f", стр. {c['page']}" if c.get("page") else ""))
                if st.button("✅ Принять", key=f"acc_{i}"):
                    api("POST", "/hypotheses/ingest",
                        json={"plant_id": plant_id, "title": p["suggested_title"],
                              "module_code": p["module_code"], "origin": "generated"})
                    st.success("Принято — оцени на «Оценке».")
        return

    # Мои гипотезы — карточки-строки со свёрнутой сутью
    hyps = api("GET", f"/plants/{plant_id}/hypotheses") or []
    if not hyps:
        st.info("Пока нет гипотез. Переключись на «✨ Предложить новые».")
        return
    for h in hyps:
        mc = h.get("module_code")
        cause = MODULE_CAUSE.get(mc, "dispersed")
        with st.container(border=True):
            left, right = st.columns([5, 2])
            left.markdown(
                f'{chip(cause)} **{h["title"]}**  \n'
                f'<span style="color:#9aa4ad;font-size:0.82rem">'
                f'{ORIGIN_RU.get(h["origin"], h["origin"])} · {MODULE_RU.get(mc, "—")} · '
                f'{STATUS_RU.get(h["status"], h["status"])}</span>',
                unsafe_allow_html=True)
            right.metric("Эффект", fmt_t(h.get("latest_effect_tons_max")))
            if h.get("dead_end_flag"):
                st.error("🚫 Тупик: уже проваливалось в опыте.")

    rej = [h for h in api("GET", f"/plants/{plant_id}/hypotheses",
                          params={"include_rejected": True}) or [] if h["status"] == "rejected"]
    if rej:
        with st.expander(f"🚫 Отклонённые тупики ({len(rej)})"):
            show_table(rej, {"title": "Гипотеза", "module_code": "Модуль"},
                       translate={"module_code": MODULE_RU})


def stage_ranking():
    st.subheader("📊 Оценка и рейтинг")
    help_box("**Модуль** — технологический рычаг: доизмельчение, классификация или флотация тонких. "
             "Движок смотрит, какие ячейки матрицы модуль может закрыть, выбирает самое специфичное "
             "правило, берёт коэффициент из кривой извлечения и переводит тонны в деньги. "
             "Итоговый приоритет складывается из денег, тонн, вероятности успеха, покрытия новых "
             "ячеек, реализуемости и штрафов за риск/тупик/конфликт. "
             "Плашка **тупик** означает, что похожая проверка уже провалилась; **конфликт** — две "
             "гипотезы конкурируют за одну ячейку, их эффекты нельзя сложить сверх тоннажа.")
    if st.button("▶️ Оценить фабрику и пересобрать рейтинг", use_container_width=True):
        if api("POST", f"/plants/{plant_id}/evaluate") is not None:
            st.toast("Рейтинг пересобран.", icon="✅")

    rk = api("GET", f"/plants/{plant_id}/ranking")
    if not rk:
        st.stop()
    share = Decimal(str(rk["coverage_summary"]["coverage_share"]))
    st.metric("🎯 Покрыто извлекаемых потерь", f"{share:.0%}")

    if not rk["items"]:
        st.info("Пока нет оценок — нажми «Оценить фабрику».")
        return
    for it in rk["items"]:
        cause = MODULE_CAUSE.get(it.get("module_code"), "dispersed")
        with st.container(border=True):
            head, val = st.columns([5, 2])
            feas = "✅" if it.get("feasible") else "⛔"
            head.markdown(
                f'{chip(cause)} **#{it["rank"]} · {it["title"]}**  \n'
                f'<span style="color:#9aa4ad;font-size:0.82rem">'
                f'{ORIGIN_RU.get(it.get("origin"), "")} · {MODULE_RU.get(it.get("module_code"), "—")}</span>',
                unsafe_allow_html=True)
            val.metric(f"{feas} Эффект", fmt_t(it.get("effect_tons_max")), fmt_usd(it.get("effect_usd_max")))
            m1, m2, m3 = st.columns(3)
            m1.metric("Ожидаемый $", fmt_usd(it.get("expected_effect_usd")))
            m2.metric("P(успех)", f"{float(it.get('success_probability') or 0):.0%}")
            m3.metric("ΔCoverage", fmt_t(it.get("coverage_contribution")))
            if it.get("dead_end_flag"):
                st.error(f"🚫 **Тупик:** {it.get('dead_end_reason') or 'уже проваливалось'}")
            if it.get("competes_with"):
                peers = ", ".join(f"№{p}" for p in it["competes_with"])
                tons = fmt_t(it.get("competes_tons")) if it.get("competes_tons") else "общие тонны"
                st.warning(f"⚔️ **Конфликт за ячейку:** конкурирует с {peers} за {tons} — "
                           f"эффекты не суммируются (масс-баланс).")
            with st.expander("🔍 Мини-отчёт по модулям"):
                render_module_reports(it.get("module_reports", []))

    ex1, ex2 = st.columns(2)
    ex1.download_button("⬇️ Портфель (CSV из БД)",
                        api_bytes(f"/export/portfolio.csv?plant_id={plant_id}"),
                        file_name=f"portfolio_{plant['code']}.csv", mime="text/csv",
                        use_container_width=True)
    ex2.download_button("📄 Портфель (PDF-дашборд)",
                        api_bytes(f"/export/portfolio.pdf?plant_id={plant_id}"),
                        file_name=f"portfolio_{plant['code']}.pdf", mime="application/pdf",
                        use_container_width=True)

    with st.expander("🅰️ Демо-релевантность: одна гипотеза → разный эффект на фабриках"):
        target = "Доизмельчение крупных запертых сростков никеля"
        rows = []
        for other in plants:
            for it in (api("GET", f"/plants/{other['id']}/ranking") or {}).get("items", []):
                if it["title"] == target:
                    rows.append({"Фабрика": other["code"],
                                 "Эффект, т": round(float(it["effect_tons_max"] or 0), 1),
                                 "Ранг": it["rank"], "Реализуемо": "✅" if it["feasible"] else "—"})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True) if rows \
            else st.caption("Сначала оцени обе фабрики (кнопка «Загрузить пример» сверху).")

    with st.expander("🧮 Базовая оптимизация портфеля экспериментов"):
        budget = st.number_input("Бюджет проверки, $", 0, 1_000_000, 250_000, 10_000)
        plan = api("GET", f"/plants/{plant_id}/portfolio-plan", params={"budget": budget, "limit": 3})
        if plan and plan["selected"]:
            st.metric("Суммарный предельный эффект", fmt_usd(plan["total_effect_usd"]),
                      f"стоимость {fmt_usd(plan['total_cost'])}")
            st.dataframe(pd.DataFrame(plan["selected"]), use_container_width=True, hide_index=True)
        else:
            st.caption("Нет реализуемого портфеля в заданном бюджете.")


def _pick_hypothesis(key: str):
    hyps = api("GET", f"/plants/{plant_id}/hypotheses") or []
    if not hyps:
        st.info("Сначала добавь гипотезы на стадии «Гипотезы».")
        return None, None
    labels = {f"№{h['id']} — {h['title']}": h["id"] for h in hyps}
    return labels[st.selectbox("Гипотеза", list(labels), key=key)], hyps


def stage_card():
    st.subheader("🗂️ Карточка гипотезы")
    help_box("Одна гипотеза целиком для НИОКР-комитета: эффект в тоннах и деньгах, "
             "обоснование по модулям, первый (дешёвый) эксперимент. Числа — из движка, "
             "текст карточки собирается по этим числам и не придумывает новых значений.")
    hid, _ = _pick_hypothesis("card_hyp")
    if not hid:
        return
    c1, c2 = st.columns([1, 1])
    if c1.button("📝 Собрать карточку", use_container_width=True):
        card = api("GET", f"/hypotheses/{hid}/card")
        if card:
            st.caption("Карточка собрана по числам движка.")
            with st.container(border=True):
                st.markdown(card["text"])
    with c2:
        st.download_button("⬇️ Карточка (CSV)", api_bytes(f"/export/hypothesis/{hid}.csv"),
                           file_name=f"hypothesis_{hid}.csv", mime="text/csv",
                           use_container_width=True)
        st.download_button("📄 Карточка (PDF)", api_bytes(f"/export/hypothesis/{hid}.pdf"),
                           file_name=f"hypothesis_{hid}.pdf", mime="application/pdf",
                           use_container_width=True)


def _roadmap_dot(steps: list[dict]) -> str:
    """Детерминированный граф этапов из данных (killer-узлы и сращённые — подсвечены)."""
    lines = [
        'digraph roadmap {',
        '  rankdir=TB; bgcolor="transparent";',
        '  node [style="filled,rounded", shape=box, fontname="Arial", '
        'fontcolor="#0b0f17", fontsize=11, margin="0.18,0.12"];',
        '  edge [color="#8A9199", arrowsize=0.7];',
    ]
    # сращённые узлы = один shared_key встречается более одного раза
    shared_seen: dict[str, int] = {}
    for s in steps:
        shared_seen[s.get("shared_key") or ""] = shared_seen.get(s.get("shared_key") or "", 0) + 1
    for s in steps:
        killer = s.get("is_killer")
        merged = shared_seen.get(s.get("shared_key") or "", 0) > 1
        fill = BRAND_GREEN if killer else ("#7B3FF2" if merged else "#c9d1d9")
        fontcolor = "#0b0f17" if killer else ("#ffffff" if merged else "#0b0f17")
        badge = " ★killer" if killer else (" ⛓ сращён" if merged else "")
        label = (f'{s["step_order"]}. {s["title"]}\\n'
                 f'{fmt_usd(s.get("cost"))} · {s.get("duration_days")} дн{badge}')
        lines.append(f'  n{s["step_order"]} [label="{label}", fillcolor="{fill}", fontcolor="{fontcolor}"];')
    ordered = sorted(steps, key=lambda x: x["step_order"])
    for a, b in zip(ordered, ordered[1:]):
        lines.append(f'  n{a["step_order"]} -> n{b["step_order"]};')
    lines.append("}")
    return "\n".join(lines)


def stage_roadmap():
    st.subheader("🗺️ Дорожная карта эксперимента")
    help_box("План проверки гипотезы графом: этапы от дешёвого **killer-эксперимента** "
             "(зелёный — может быстро и дёшево «убить» гипотезу) к дорогим. "
             "Фиолетовым подсвечены **сращённые узлы** — этапы, общие для нескольких гипотез "
             "(их можно ставить один раз на весь портфель). Каждый этап разворачивается "
             "в подзадания; стоимость и сроки берутся из шаблона этапа, а не «с потолка».")
    hid, _ = _pick_hypothesis("road_hyp")
    if not hid:
        return
    steps = api("POST", f"/hypotheses/{hid}/roadmap") or []
    if not steps:
        st.info("Нет этапов дорожной карты.")
        return
    total = sum(float(s.get("cost") or 0) for s in steps)
    days = sum(int(s.get("duration_days") or 0) for s in steps)
    a, b = st.columns(2)
    a.metric("Суммарная стоимость", fmt_usd(total))
    b.metric("Суммарно дней", days)

    st.graphviz_chart(_roadmap_dot(steps), use_container_width=True)

    st.markdown("**Этапы и подзадания**")
    for s in steps:
        killer = "🎯 killer" if s.get("is_killer") else ""
        done = "✅ выполнен" if s.get("status") == "done" else "⏳ запланирован"
        with st.expander(f"Этап {s['step_order']}. {s['title']}  ·  "
                         f"{fmt_usd(s.get('cost'))} · {s.get('duration_days')} дн  {killer}"):
            st.caption(f"Статус: {done}. Критерий успеха: {s.get('success_criterion') or '—'}")
            subs = s.get("subtasks") or []
            if subs:
                st.markdown("**Подзадания:**")
                for t in subs:
                    st.markdown(f"- {t}")
            st.caption(f"💰 Стоимость — источник: {s.get('cost_source') or '—'}")
            st.caption(f"📅 Сроки — источник: {s.get('duration_source') or '—'}")


def stage_lab():
    st.subheader("🧪 Лаборатория — петля обучения")
    help_box(
        "**Артефакт эксперимента** — это результат реального опыта (измеренный коэффициент "
        "возврата, итог: успех/частично/провал, комментарий), загруженный обратно в систему. "
        "Он связывает виртуальную лабораторию с реальной: каждый опыт **дообучает движок**.\n\n"
        "- Успешный/частичный опыт **калибрует коэффициент правила** взвешенно: "
        "`k← k + η·(факт−k)`, шаг `η=1/√(N+1)` — чем больше опытов накоплено, тем меньше "
        "единичный замер двигает правило (устойчивость к выбросам).\n"
        "- **Провал** тянет коэффициент к нижней границе и заносит **тупик** "
        "(модуль+причина+класс), который переносится и на другие фабрики и убирает "
        "провальные гипотезы из выдачи.\n\n"
        "Поэтому система **дорожает с каждым экспериментом**: накопленные калибровки и тупики — "
        "это её растущий актив. Ниже — редактор правил для ручной экспертной настройки: "
        "правишь коэффициент → рейтинг пересчитывается сразу.")
    hid, _ = _pick_hypothesis("lab_hyp")
    if not hid:
        return
    steps = api("POST", f"/hypotheses/{hid}/roadmap") or []
    with st.container(border=True):
        st.markdown("**📥 Результат опыта**")
        with st.form("artifact"):
            sl = {f"Этап {s['step_order']} — {s['title']}": s["id"] for s in steps}
            sid = sl[st.selectbox("Этап", list(sl))] if sl else None
            outcome = st.selectbox("Итог", ["failure", "partial", "success"],
                                   format_func=lambda v: OUTCOME_RU[v])
            mv = st.number_input("Измеренный коэффициент возврата (0–1)", 0.0, 1.0, 0.0, 0.01)
            note = st.text_area("Комментарий", "Провал: рычаг не дал эффекта на этой руде")
            if st.form_submit_button("Загрузить результат", use_container_width=True) and sid:
                r = api("POST", f"/roadmap/{sid}/artifact",
                        json={"outcome": outcome, "measured_value": mv, "note": note})
                if r:
                    change = f"{r['coeff_before']} → {r['coeff_after']}" if r.get("coeff_before") else "без изменений"
                    dead = f" · ❗ тупик №{r['dead_end_id']}" if r.get("dead_end_id") else ""
                    st.success(f"Коэффициент правила: {change}.{dead}")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Правила движка**")
        show_table(api("GET", "/rules"),
                   {"module_code": "Модуль", "target_cause": "Причина", "target_size_class_code": "Класс",
                    "coeff": "coeff", "requires_kind": "Оборуд."},
                   translate={"module_code": MODULE_RU,
                              "requires_kind": lambda v: ", ".join(EQUIP_RU.get(x.strip(), x.strip())
                                                                   for x in str(v or "").split(",") if x.strip()) or "—"},
                   round_cols={"coeff": 3})
    with c2:
        st.markdown("**База тупиков**")
        show_table(api("GET", "/dead-ends"),
                   {"module_code": "Модуль", "target_cause": "Причина", "size_class_code": "Класс",
                    "reason": "Почему тупик"}, translate={"module_code": MODULE_RU})

    with st.expander("✏️ Редактор правил — пересчёт на глазах"):
        rules = api("GET", "/rules") or []
        rl = {f"{MODULE_RU.get(r['module_code'], r['module_code'])} · {r['target_cause']} · "
              f"класс {r.get('target_size_class_code') or 'любой'} · coeff={r['coeff']}": r for r in rules}
        if rl:
            r = rl[st.selectbox("Правило", list(rl))]
            nc = st.number_input("Новый coeff (0–1)", 0.0, 1.0, float(r["coeff"]), 0.01)
            if st.button("💾 Сохранить и пересчитать"):
                if api("PATCH", f"/rules/{r['id']}", json={"coeff": nc}) is not None:
                    api("POST", f"/plants/{plant_id}/evaluate")
                    st.success(f"coeff → {nc}, рейтинг пересчитан. Открой «Оценку».")


def stage_literature():
    st.subheader("📚 Литература — поиск обоснований (RAG)")
    help_box("Вопрос на русском → эмбеддинг Yandex → поиск ближайших "
             "фрагментов корпуса → ответ строго по ним, со ссылкой на файл и страницу. "
             "Числа не выдумываются.")
    stats = api("GET", "/corpus/stats")
    if not stats:
        st.stop()
    ol = stats.get("llm", {})
    a, b, c = st.columns(3)
    a.metric("Документов", stats["documents"])
    b.metric("Фрагментов", stats["chunks"])
    c.metric("Yandex", "✅" if ol.get("reachable") else "❌")
    if not ol.get("reachable"):
        st.warning("Yandex не настроен — поиск и ответы работать не будут.")
    if st.button("🔄 Проиндексировать / обновить корпус", use_container_width=True):
        with st.spinner("Индексация через Yandex…"):
            r = api("POST", "/corpus/index", json={})
        if r:
            st.success(f"Проиндексировано: {r['files_indexed']} · фрагментов: {r['chunks_added']}.")

    q = st.text_input("Вопрос", "Как крупность измельчения влияет на извлечение при флотации?")
    mode = st.radio("Что вернуть", ["Ответ со ссылками", "Только фрагменты"], horizontal=True)
    if st.button("🔎 Спросить корпус", use_container_width=True):
        with st.spinner("Ищу…"):
            if mode.startswith("Ответ"):
                d = api("POST", "/corpus/ask", json={"query": q})
                if d:
                    with st.container(border=True):
                        st.markdown(d["answer"])
                    for cite in d["citations"]:
                        where = cite["source_file"] + (f", стр. {cite['page']}" if cite.get("page") else "")
                        with st.expander(f"[{cite['n']}] {where} · близость={cite['distance']}"):
                            st.write(cite["snippet"])
            else:
                d = api("GET", "/corpus/search", params={"q": q})
                if d:
                    show_table(d["hits"], {"source_file": "Файл", "page": "Стр.",
                                           "distance": "Близость", "snippet": "Фрагмент"},
                               round_cols={"distance": 3})


ROUTES = {
    "diag": stage_diagnosis, "hyp": stage_hypotheses, "rank": stage_ranking,
    "card": stage_card, "road": stage_roadmap, "lab": stage_lab, "lit": stage_literature,
}
ROUTES[stage]()
