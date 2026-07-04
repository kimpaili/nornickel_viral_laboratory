import os
from decimal import Decimal

import pandas as pd
import requests
import streamlit as st


API_BASE = os.getenv("API_BASE", "http://localhost:8000")

st.set_page_config(page_title="Фабрика гипотез", layout="wide", page_icon="⚗️")


# ----------------------------------------------------------------------------
# Помощники: запрос к API, форматирование, перевод значений и таблиц
# ----------------------------------------------------------------------------
def api(method: str, path: str, **kwargs):
    try:
        response = requests.request(method, f"{API_BASE}{path}", timeout=120, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        st.error(f"API недоступен ({API_BASE}{path}): {exc}")
        st.stop()


def fmt_tons(value):
    if value in (None, ""):
        return "—"
    return f"{Decimal(str(value)):,.1f} т".replace(",", " ")


# --- Словари перевода значений таблиц ---------------------------------------
LOSS_CAUSE_RU = {
    "free": "🟢 свободный (не пойман флотацией)",
    "locked": "🔒 запертый (сросток с породой)",
    "dispersed": "⚪ рассеянный (в решётке минерала)",
}
MODULE_RU = {
    "regrind": "Доизмельчение",
    "classification": "Классификация",
    "fine_flotation": "Флотация тонких",
}
MINERAL_FORM_RU = {
    "free_pnt": "Свободный Pn/Cp",
    "locked_pnt_cp": "Запертые сростки Pn/Cp",
    "pyrrhotite_assoc": "Срастание с пирротином",
    "silicate_valleriite": "Силикаты/валлериит",
}
EQUIP_RU = {
    "mill": "мельница",
    "hydrocyclone": "гидроциклон",
    "classifier": "классификатор",
    "flotation": "флотомашина",
    "screen": "грохот",
    "magnetic": "магнитный сепаратор",
}
STATUS_RU = {
    "new": "🆕 новая",
    "evaluated": "✅ оценена",
    "in_roadmap": "🗺️ в дорожной карте",
    "confirmed": "✔️ подтверждена",
    "rejected": "🚫 отклонена (тупик)",
}
ORIGIN_RU = {"generated": "🤖 система", "expert": "👤 эксперт"}
OUTCOME_RU = {"success": "успех", "partial": "частично", "failure": "провал"}
YES_NO = {True: "✅ да", False: "— нет", None: "—"}


def _tr_equip(value):
    if not value:
        return "—"
    return ", ".join(EQUIP_RU.get(v.strip(), v.strip()) for v in str(value).split(","))


def show_table(rows, columns: dict, translate: dict | None = None, round_cols: dict | None = None):
    """Оставляет и переименовывает только нужные колонки, переводит значения, округляет."""
    if not rows:
        st.caption("Пока пусто.")
        return
    df = pd.DataFrame(rows)
    for col, mapping in (translate or {}).items():
        if col in df.columns:
            if callable(mapping):
                df[col] = df[col].map(mapping)
            else:
                df[col] = df[col].map(lambda v: mapping.get(v, v))
    for col, digits in (round_cols or {}).items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(digits)
    keep = [c for c in columns if c in df.columns]
    st.dataframe(df[keep].rename(columns=columns), use_container_width=True, hide_index=True)


def hint(title: str, body: str):
    with st.expander(f"❓ {title}", expanded=False):
        st.markdown(body)


# ----------------------------------------------------------------------------
# Шапка
# ----------------------------------------------------------------------------
st.title("⚗️ Фабрика гипотез")
st.caption("Система поддержки решений для обогащения руды: где теряется металл, "
           "как его вернуть и как учиться на результатах опытов.")
st.info("**Принцип:** LLM *предлагает и объясняет*, а *считает и ранжирует* — "
        "детерминированный движок на реальных числах фабрики. Ни одна цифра эффекта не выдумана.")

plants = api("GET", "/plants")
if not plants:
    st.warning("В базе нет фабрик. Запусти seed: `docker compose --profile tools run --rm seed`")
    st.stop()

plant_by_label = {f"{p['code']} — {p['title']}": p for p in plants}
selected_label = st.sidebar.selectbox("🏭 Фабрика", list(plant_by_label))
plant = plant_by_label[selected_label]
plant_id = plant["id"]

with st.sidebar.container(border=True):
    st.metric("Питание (руда), тыс. т", plant.get("feed_smt") or "—")
    st.metric("Хвосты, тыс. т", plant.get("tailings_smt") or "—")
st.sidebar.caption(f"API: {API_BASE}")

with st.sidebar.expander("📖 Порядок работы", expanded=True):
    st.markdown(
        "1. **Диагноз** — где теряется металл.\n"
        "2. **Гипотезы** — идеи по возврату.\n"
        "3. **Рейтинг** — оценка и ранжирование.\n"
        "4. **Карточка** — обоснование топ-гипотезы.\n"
        "5. **Лаборатория** — опыт → обучение.\n"
        "6. **Литература** — обоснования из книг (RAG)."
    )
with st.sidebar.expander("📚 Словарь терминов"):
    st.markdown(
        "- **Класс крупности** — размер частиц, напр. `−45+20` мкм.\n"
        "- **Свободный** — зерно раскрыто, флотация упустила → настройка флотации.\n"
        "- **Запертый** — заперт в породе → доизмельчение.\n"
        "- **Рассеянный** — в решётке пустого минерала → флотацией не берётся.\n"
        "- **coeff** — доля возврата металла (0–1), калибруется опытом.\n"
        "- **Реализуемо** — хватает ли оборудования.\n"
        "- **Тупик** — что уже пробовали и не сработало."
    )


TARGET_HYP = "Доизмельчение крупных запертых сростков никеля"

t1, t2, t3, t4, t5, t6 = st.tabs(
    ["1 · Диагноз", "2 · Гипотезы", "3 · Рейтинг", "4 · Карточка", "5 · Лаборатория", "6 · Литература"]
)

# ----------------------------------------------------------------------------
# 1. Диагноз
# ----------------------------------------------------------------------------
with t1:
    st.subheader("🔬 Диагноз потерь")
    hint("Что это за экран",
         "**Вход:** матрица потерь фабрики (таблица хвостов).\n\n"
         "**Выход:** сколько металла теряется в каждой ячейке «класс × форма» и сколько из этого извлекаемо.\n\n"
         "**Как считается:** чистая арифметика по данным, без LLM. Чем краснее ячейка — тем тяжелее потеря.")

    data = api("GET", f"/plants/{plant_id}/diagnosis")
    with st.container(border=True):
        a, b = st.columns(2)
        a.metric("♻️ Извлекаемые потери", fmt_tons(data["recoverable_tons"]),
                 help="Металл, который можно вернуть флотацией/доизмельчением.")
        b.metric("⛔ Неизвлекаемые потери", fmt_tons(data["unrecoverable_tons"]),
                 help="Рассеянный в решётке пустых минералов — для флотации тупик.")

    matrix = pd.DataFrame(data["matrix"])
    if not matrix.empty:
        matrix["tons"] = pd.to_numeric(matrix["tons"], errors="coerce")
        matrix["mineral_form_code"] = matrix["mineral_form_code"].map(lambda v: MINERAL_FORM_RU.get(v, v))
        pivot = matrix.pivot_table(index="size_class_code", columns=["metal_code", "mineral_form_code"],
                                   values="tons", aggfunc="sum", fill_value=0)
        st.markdown("**Матрица потерь** — строки: класс крупности, столбцы: металл × форма, значения: тонны:")
        st.dataframe(pivot.style.background_gradient(axis=None, cmap="YlOrRd").format("{:.1f}"),
                     use_container_width=True)

    st.markdown("**Детально по ячейкам:**")
    show_table(data["cells"],
               {"metal_code": "Металл", "size_class_code": "Класс крупности", "mineral_form_code": "Мин. форма",
                "loss_cause": "Причина потери", "recoverable": "Извлекаемо", "tons": "Потери, т"},
               translate={"mineral_form_code": MINERAL_FORM_RU, "loss_cause": LOSS_CAUSE_RU, "recoverable": YES_NO},
               round_cols={"tons": 1})

# ----------------------------------------------------------------------------
# 2. Гипотезы
# ----------------------------------------------------------------------------
with t2:
    st.subheader("💡 Гипотезы улучшения")
    hint("Что это за экран",
         "**Вход:** тяжёлые ячейки матрицы + каталог правил движка.\n\n"
         "**Выход:** гипотезы «что сделать». Источники: 🤖 система (по тяжёлым ячейкам, без LLM) и 👤 эксперт.\n\n"
         "**Важно:** система сверяется с базой тупиков — что уже провалилось, заново не предлагает.")

    c1, c2 = st.columns([1, 1.4])
    with c1:
        with st.container(border=True):
            st.markdown("**🤖 Генерация системой**")
            st.caption("Подберёт рычаги под самые тяжёлые ячейки этой фабрики.")
            limit = st.slider("Сколько сгенерировать", 1, 20, 5)
            if st.button("⚙️ Сгенерировать", use_container_width=True):
                gen = api("POST", f"/plants/{plant_id}/generate", params={"limit": limit})
                st.success(f"Создано новых: {gen['created']}. Отброшено тупиков: {gen['skipped_dead_ends']}.")
    with c2:
        with st.container(border=True):
            st.markdown("**👤 Добавить гипотезу эксперта**")
            st.caption("Идея штурма — система её не заменяет, а оценивает наравне.")
            with st.form("expert_hypothesis"):
                title = st.text_input("Формулировка", "Доизмельчение крупных запертых сростков никеля")
                module_code = st.selectbox("Рычаг", ["regrind", "classification", "fine_flotation"],
                                           format_func=lambda v: MODULE_RU[v])
                submitted = st.form_submit_button("➕ Добавить")
            if submitted:
                created = api("POST", "/hypotheses/ingest",
                              json={"plant_id": plant_id, "title": title,
                                    "module_code": module_code, "origin": "expert"})
                st.success(f"Добавлена гипотеза №{created['id']}.")

    st.markdown("**Активные гипотезы:**")
    show_table(api("GET", f"/plants/{plant_id}/hypotheses"),
               {"id": "№", "title": "Гипотеза", "module_code": "Модуль", "origin": "Источник",
                "status": "Статус", "latest_effect_tons_max": "Эффект, т", "latest_feasible": "Реализуемо"},
               translate={"module_code": MODULE_RU, "origin": ORIGIN_RU, "status": STATUS_RU,
                          "latest_feasible": YES_NO},
               round_cols={"latest_effect_tons_max": 1})

    rejected = [h for h in api("GET", f"/plants/{plant_id}/hypotheses", params={"include_rejected": True})
                if h["status"] == "rejected"]
    if rejected:
        st.markdown("**🚫 Отклонённые тупики** (провалились в опыте):")
        show_table(rejected, {"id": "№", "title": "Гипотеза", "module_code": "Модуль"},
                   translate={"module_code": MODULE_RU})

    st.divider()
    st.markdown("**📖 Литературная генерация** (третий источник — LLM + корпус)")
    st.caption("Система ищет в учебниках рычаги для тяжёлых непокрытых ячеек и предлагает гипотезы "
               "со ссылками. LLM предлагает только идею; числа посчитает движок при оценке.")
    if st.button("📖 Предложить гипотезы из литературы", use_container_width=True):
        with st.spinner("Ищу в корпусе и формулирую предложения через Ollama..."):
            res = api("POST", f"/plants/{plant_id}/literature-hypotheses")
        st.session_state[f"lit_{plant_id}"] = res["proposals"]
    for i, p in enumerate(st.session_state.get(f"lit_{plant_id}", [])):
        with st.container(border=True):
            st.markdown(f"**{p['suggested_title']}**  ·  рычаг: {MODULE_RU.get(p['module_code'], p['module_code'])}")
            st.write(p["rationale"])
            for c in p["citations"]:
                where = c["source_file"] + (f", стр. {c['page']}" if c.get("page") else "")
                st.caption(f"[{c['n']}] {where}")
            if st.button("✅ Принять в работу", key=f"accept_{plant_id}_{i}"):
                api("POST", "/hypotheses/ingest",
                    json={"plant_id": plant_id, "title": p["suggested_title"],
                          "module_code": p["module_code"], "origin": "generated"})
                st.success("Принято. Оцени фабрику на экране «Рейтинг».")

# ----------------------------------------------------------------------------
# 3. Рейтинг
# ----------------------------------------------------------------------------
with t3:
    st.subheader("📊 Рейтинг гипотез")
    hint("Что это за экран",
         "**Вход:** активные гипотезы + числа фабрики (тонны, цены металлов, оборудование).\n\n"
         "**Выход:** ранжированный список с эффектом (т и $), реализуемостью и картой покрытия.\n\n"
         "**Как считается:** тонны целевой ячейки × коэффициент правила → деньги по цене металла, "
         "плюс проверка оборудования. Детерминированно.")

    if st.button("▶️ Оценить фабрику и пересобрать рейтинг", use_container_width=True):
        api("POST", f"/plants/{plant_id}/evaluate")
        st.success("Готово — рейтинг пересобран.")

    ranking = api("GET", f"/plants/{plant_id}/ranking")
    with st.container(border=True):
        st.metric("🎯 Покрыто извлекаемых потерь",
                  f"{Decimal(str(ranking['coverage_summary']['coverage_share'])):.0%}",
                  help="Доля извлекаемого металла, закрытая хотя бы одной реализуемой гипотезой.")

    st.markdown("**Рейтинг** (сверху — самые ценные и реализуемые):")
    if ranking["items"]:
        show_table(ranking["items"],
                   {"rank": "Ранг", "title": "Гипотеза", "module_code": "Модуль", "effect_tons_max": "Эффект, т",
                    "effect_usd_max": "Эффект, $", "feasible": "Реализуемо", "competes_with": "Конкур. с №"},
                   translate={"module_code": MODULE_RU, "feasible": YES_NO},
                   round_cols={"effect_tons_max": 1, "effect_usd_max": 0})
        csv = pd.DataFrame(ranking["items"]).to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ Скачать рейтинг (CSV)", csv,
                           file_name=f"рейтинг_{plant['code']}.csv", mime="text/csv")
    else:
        st.info("Пока нет оценок — нажми «Оценить фабрику» выше.")

    st.markdown("**Карта покрытия** (по ячейкам — сколько тонн закрыто гипотезами):")
    show_table(ranking["coverage_cells"],
               {"metal_code": "Металл", "size_class_code": "Класс", "mineral_form_code": "Форма",
                "tons": "Потери, т", "covered_effect_tons_max": "Закрыто, т", "coverage_share": "Доля",
                "contested": "Спорная"},
               translate={"mineral_form_code": MINERAL_FORM_RU, "contested": YES_NO},
               round_cols={"tons": 1, "covered_effect_tons_max": 1, "coverage_share": 2})

    if any(c.get("contested") for c in ranking["coverage_cells"]):
        st.warning("⚠️ **Конфликт за ячейку:** несколько гипотез бьют в одну ячейку, их эффекты "
                   "не складываются сверх её тоннажа (масс-баланс). Одобрить обе ≠ сложить эффекты.")

    st.divider()
    st.markdown("### 🅰️ Демонстрация 1 — релевантность")
    st.caption("Одна гипотеза на разных фабриках → разный эффект (разная структура потерь). "
               "Переключай фабрику слева.")
    rows = []
    for other in plants:
        for item in api("GET", f"/plants/{other['id']}/ranking")["items"]:
            if item["title"] == TARGET_HYP:
                rows.append({"Фабрика": other["code"],
                             "Эффект, т": round(float(item["effect_tons_max"] or 0), 1),
                             "Эффект, $": round(float(item["effect_usd_max"] or 0)),
                             "Реализуемо": YES_NO.get(item["feasible"]), "Ранг": item["rank"]})
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("Сначала оцени обе фабрики (кнопка выше на каждой).")

# ----------------------------------------------------------------------------
# 4. Карточка
# ----------------------------------------------------------------------------
with t4:
    st.subheader("🗂️ Карточка гипотезы")
    hint("Что это за экран",
         "**Вход:** одна гипотеза + её оценка движком.\n\n"
         "**Выход:** человекочитаемое обоснование для НИОКР-комитета.\n\n"
         "**Как:** числа из движка, локальный Ollama только оборачивает их в текст.")

    hyps = api("GET", f"/plants/{plant_id}/hypotheses")
    if not hyps:
        st.info("Сначала сгенерируй или добавь гипотезы на экране «Гипотезы».")
    else:
        labels = {f"№{h['id']} — {h['title']}": h["id"] for h in hyps}
        hypothesis_id = labels[st.selectbox("Выбери гипотезу", list(labels))]
        if st.button("📝 Собрать карточку", use_container_width=True):
            card = api("GET", f"/hypotheses/{hypothesis_id}/card")
            st.caption("✅ Текст сгенерирован LLM (Ollama)" if card["llm_used"]
                       else "⚠️ Ollama недоступен — текст-заглушка на числах движка")
            with st.container(border=True):
                st.markdown(card["text"])

# ----------------------------------------------------------------------------
# 5. Лаборатория
# ----------------------------------------------------------------------------
with t5:
    st.subheader("🧪 Лаборатория — петля обучения")
    hint("Что это за экран",
         "**Вход:** гипотеза → дорожная карта опытов → результат этапа.\n\n"
         "**Выход:** система учится — коэффициент правила калибруется, провал заносится в тупики.\n\n"
         "**Демонстрация 2:** загрузи итог «провал» — coeff правила изменится, появится тупик, "
         "а на экране «Гипотезы» эта гипотеза уйдёт из выдачи.")

    hyps = api("GET", f"/plants/{plant_id}/hypotheses")
    if not hyps:
        st.info("Нет активных гипотез. Сгенерируй их на экране «Гипотезы».")
    else:
        labels = {f"№{h['id']} — {h['title']}": h["id"] for h in hyps}
        hypothesis_id = labels[st.selectbox("Гипотеза для дорожной карты", list(labels), key="lab_hyp")]

        if st.button("🗺️ Построить дорожную карту", use_container_width=True):
            st.session_state["roadmap_steps"] = api("POST", f"/hypotheses/{hypothesis_id}/roadmap")
        steps = st.session_state.get("roadmap_steps") or api("POST", f"/hypotheses/{hypothesis_id}/roadmap")

        st.markdown("**Этапы проверки** (первым — самый дешёвый killer-эксперимент):")
        show_table(steps,
                   {"step_order": "№", "title": "Этап", "cost": "Стоимость, $", "duration_days": "Дней",
                    "success_criterion": "Критерий успеха", "is_killer": "Killer", "status": "Статус"},
                   translate={"is_killer": YES_NO}, round_cols={"cost": 0})

        with st.container(border=True):
            st.markdown("**📥 Загрузить результат опыта:**")
            step_labels = {f"№{s['step_order']} — {s['title']}": s["id"] for s in steps}
            with st.form("artifact_form"):
                step_id = step_labels[st.selectbox("Этап", list(step_labels))]
                outcome = st.selectbox("Итог опыта", ["failure", "partial", "success"],
                                       format_func=lambda v: OUTCOME_RU[v])
                measured_value = st.number_input("Измеренный коэффициент возврата (0–1)",
                                                 min_value=0.0, max_value=1.0, value=0.0, step=0.01,
                                                 help="Для провала обычно 0. Движок сравнит с предсказанием.")
                note = st.text_area("Комментарий эксперта", "Провал: рычаг не дал эффекта на этой руде")
                submitted = st.form_submit_button("Загрузить результат")
            if submitted:
                res = api("POST", f"/roadmap/{step_id}/artifact",
                          json={"outcome": outcome, "measured_value": measured_value, "note": note})
                change = f"{res['coeff_before']} → {res['coeff_after']}" if res.get("coeff_before") else "без изменений"
                dead = f" ❗ Занесён тупик №{res['dead_end_id']}" if res.get("dead_end_id") else ""
                st.success(f"Загружено. Коэффициент правила: {change}.{dead}")
                st.info("Загляни на экран «Гипотезы»: провальная гипотеза ушла в тупики.")

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Правила движка** (coeff меняется от опытов):")
            show_table(api("GET", "/rules"),
                       {"module_code": "Модуль", "target_cause": "Причина", "target_size_class_code": "Класс",
                        "coeff": "coeff", "coeff_min": "min", "coeff_max": "max", "requires_kind": "Оборуд."},
                       translate={"module_code": MODULE_RU, "target_cause": LOSS_CAUSE_RU, "requires_kind": _tr_equip},
                       round_cols={"coeff": 3, "coeff_min": 3, "coeff_max": 3})
        with col2:
            st.markdown("**База тупиков:**")
            show_table(api("GET", "/dead-ends"),
                       {"module_code": "Модуль", "target_cause": "Причина", "size_class_code": "Класс",
                        "reason": "Почему тупик"},
                       translate={"module_code": MODULE_RU, "target_cause": LOSS_CAUSE_RU})

        with st.expander("✏️ Редактор правил — вторая точка экспертного контроля (§4.4)"):
            st.caption("Эксперт не согласен с коэффициентом? Поправь — и после пересчёта на «Рейтинге» "
                       "числа изменятся. Никакого чёрного ящика.")
            rules = api("GET", "/rules")
            rlabels = {
                f"{MODULE_RU.get(r['module_code'], r['module_code'])} · "
                f"{LOSS_CAUSE_RU.get(r['target_cause'], r['target_cause'])} · "
                f"класс {r.get('target_size_class_code') or 'любой'} · coeff={r['coeff']}": r
                for r in rules
            }
            sel = st.selectbox("Правило", list(rlabels), key="rule_edit_sel")
            r = rlabels[sel]
            new_coeff = st.number_input("Новый coeff (доля возврата 0–1)", 0.0, 1.0,
                                        float(r["coeff"]), 0.01, key="rule_edit_coeff")
            if st.button("💾 Сохранить коэффициент", key="rule_edit_save"):
                api("PATCH", f"/rules/{r['id']}", json={"coeff": new_coeff})
                st.success(f"coeff правила обновлён на {new_coeff}. Пересобери рейтинг, чтобы увидеть новые числа.")

# ----------------------------------------------------------------------------
# 6. Литература (RAG)
# ----------------------------------------------------------------------------
with t6:
    st.subheader("📚 Литература — поиск обоснований (RAG)")
    hint("Что это за экран",
         "**Вход:** твой вопрос на русском + корпус учебников (в векторной базе).\n\n"
         "**Выход:** ответ **со ссылками на источник** (файл + страница).\n\n"
         "**Как:** вопрос → эмбеддинг локальной моделью Ollama → поиск ближайших фрагментов → "
         "ответ строго по ним. Всё локально, числа не выдумываются.")

    stats = api("GET", "/corpus/stats")
    ollama = stats.get("ollama", {})
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("Документов", stats["documents"])
        c2.metric("Фрагментов", stats["chunks"])
        c3.metric("Ollama", "✅ доступен" if ollama.get("reachable") else "❌ недоступен")
    if ollama.get("reachable"):
        st.caption(f"Эмбеддинги: `{ollama.get('embed_model')}` · Ответ: `{ollama.get('chat_model')}`")
    else:
        st.warning("Ollama недоступен — поиск и ответы работать не будут.")

    if st.button("🔄 Проиндексировать / обновить корпус", use_container_width=True):
        with st.spinner("Индексация через Ollama — может занять пару минут..."):
            r = api("POST", "/corpus/index", json={})
        st.success(f"Проиндексировано: {r['files_indexed']}, без изменений: {r['files_skipped']}, "
                   f"фрагментов добавлено: {r['chunks_added']}.")

    with st.expander("Что в базе"):
        show_table(stats["files"],
                   {"source_file": "Файл", "kind": "Тип", "plant_hint": "Фабрика", "n_chunks": "Фрагментов"})

    st.divider()
    st.markdown("**Задай вопрос корпусу:**")
    query = st.text_input("Вопрос", "Как крупность измельчения влияет на извлечение при флотации?")
    mode = st.radio("Что вернуть", ["Ответ с обоснованием и ссылками", "Только найденные фрагменты"],
                    horizontal=True)
    if st.button("🔎 Спросить корпус", use_container_width=True):
        with st.spinner("Ищу в корпусе и формулирую ответ..."):
            if mode.startswith("Ответ"):
                d = api("POST", "/corpus/ask", json={"query": query})
                with st.container(border=True):
                    st.markdown("#### Ответ")
                    st.markdown(d["answer"])
                st.markdown("#### Источники")
                for cite in d["citations"]:
                    where = cite["source_file"] + (f", стр. {cite['page']}" if cite.get("page") else "")
                    with st.expander(f"[{cite['n']}] {where} · близость={cite['distance']}"):
                        st.write(cite["snippet"])
            else:
                d = api("GET", "/corpus/search", params={"q": query})
                st.caption("Чем меньше «близость» — тем релевантнее фрагмент.")
                show_table(d["hits"],
                           {"source_file": "Файл", "page": "Стр.", "distance": "Близость", "snippet": "Фрагмент"},
                           round_cols={"distance": 3})
