-- ============================================================
--  ФАБРИКА ГИПОТЕЗ — схема БД для MVP  (PostgreSQL)
--  Минимальная, но целостная: покрывает весь скелет —
--  фабрика → диагноз (матрица потерь) → гипотезы → оценка
--  модулями → дорожная карта → артефакты экспериментов →
--  калибровка правил / база тупиков.
-- ============================================================

-- Справочники ---------------------------------------------------

-- Минеральные формы нахождения металла (раскрытый/закрытый Pnt/Cp,
-- пирротин, силикаты/валлериит и т.д.) + флаг извлекаемости.
CREATE TABLE mineral_form (
    id           SERIAL PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,          -- 'raskr_pnt', 'zakr_pnt', 'pirrotin', ...
    title        TEXT NOT NULL,
    loss_cause   TEXT NOT NULL                  -- причина потери / класс лечения
                 CHECK (loss_cause IN ('free','locked','dispersed')),
    recoverable  BOOLEAN NOT NULL DEFAULT TRUE  -- достижимо ли флотацией в принципе
);

-- Классы крупности (+125, -125+71, ..., -10).
CREATE TABLE size_class (
    id        SERIAL PRIMARY KEY,
    code      TEXT NOT NULL UNIQUE,             -- '-71+45'
    microns_lo NUMERIC,                         -- нижняя граница, мкм
    microns_hi NUMERIC,                         -- верхняя граница, мкм
    sort_order INT NOT NULL
);

-- Металлы (Ni=28, Cu=29) + цена для перевода тонн в деньги.
CREATE TABLE metal (
    id           SERIAL PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,          -- 'Ni','Cu'
    title        TEXT NOT NULL,
    price_usd_t  NUMERIC                        -- $/т для оценки эффекта
);

-- Фабрика и её диагноз -----------------------------------------

CREATE TABLE plant (
    id              SERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,       -- 'KGMK','NOF_vkr','TOF'
    title           TEXT NOT NULL,
    feed_smt        NUMERIC,                    -- поступило в переработку, СМТ
    tailings_smt    NUMERIC,                    -- отвальные хвосты, СМТ
    created_at      TIMESTAMP DEFAULT now()
);

-- Установленное оборудование (паспорт парка) — для фильтра
-- реализуемости и адресации настроек. В MVP минимально.
CREATE TABLE equipment (
    id          SERIAL PRIMARY KEY,
    plant_id    INT NOT NULL REFERENCES plant(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                  -- 'mill','hydrocyclone','classifier','flotation','screen','magnetic'
    model       TEXT,                           -- 'МШЦ 4,5x6,0','ГЦ-660'
    qty         INT
);

-- ЯДРО ДИАГНОЗА: матрица потерь.
-- Одна строка = сколько тонн металла M потеряно в классе S в форме F.
CREATE TABLE loss_cell (
    id             SERIAL PRIMARY KEY,
    plant_id       INT NOT NULL REFERENCES plant(id)        ON DELETE CASCADE,
    metal_id       INT NOT NULL REFERENCES metal(id),
    size_class_id  INT NOT NULL REFERENCES size_class(id),
    mineral_form_id INT NOT NULL REFERENCES mineral_form(id),
    tons           NUMERIC NOT NULL,            -- потери металла, т
    UNIQUE (plant_id, metal_id, size_class_id, mineral_form_id)
);

-- Правила и модули (движок оценки) ------------------------------

-- Модуль = класс вмешательства с единым контрактом.
CREATE TABLE module (
    id          SERIAL PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,           -- 'regrind','classification','fine_flotation'
    title       TEXT NOT NULL,
    description TEXT
);

-- Правило внутри модуля: на какую (причину × класс) оно действует
-- и с каким коэффициентом раскрытия/извлечения. Коэффициент
-- калибруется артефактами (см. ниже) — потому и хранится в БД,
-- а не зашит в код.
CREATE TABLE rule (
    id             SERIAL PRIMARY KEY,
    module_id      INT NOT NULL REFERENCES module(id) ON DELETE CASCADE,
    code           TEXT NOT NULL UNIQUE,
    target_cause   TEXT NOT NULL                -- в какую причину бьёт
                   CHECK (target_cause IN ('free','locked','dispersed')),
    target_size_class_id INT REFERENCES size_class(id),  -- NULL = любой класс
    coeff          NUMERIC NOT NULL,            -- доля целевого металла, которую вернёт (0..1)
    coeff_min      NUMERIC,                     -- нижняя граница диапазона
    coeff_max      NUMERIC,                     -- верхняя граница диапазона
    side_effect    TEXT,                        -- напр. 'прирост шламов'
    requires_kind  TEXT,                        -- какое оборудование нужно (kind); NULL = не требует
    source         TEXT                         -- ссылка на источник правила (литература/опыт)
);

-- Гипотезы -----------------------------------------------------

CREATE TABLE hypothesis (
    id           SERIAL PRIMARY KEY,
    plant_id     INT NOT NULL REFERENCES plant(id) ON DELETE CASCADE,
    module_id    INT REFERENCES module(id),     -- к какому рычагу относится
    title        TEXT NOT NULL,
    origin       TEXT NOT NULL                  -- откуда взялась
                 CHECK (origin IN ('expert','generated')),
    status       TEXT NOT NULL DEFAULT 'new'    -- жизненный цикл
                 CHECK (status IN ('new','evaluated','in_roadmap','confirmed','rejected')),
    created_at   TIMESTAMP DEFAULT now()
);

-- Результат оценки гипотезы движком (снимок на момент прогона).
CREATE TABLE evaluation (
    id                SERIAL PRIMARY KEY,
    hypothesis_id     INT NOT NULL REFERENCES hypothesis(id) ON DELETE CASCADE,
    rule_id           INT REFERENCES rule(id),
    target_metal_id   INT REFERENCES metal(id),
    effect_tons_min   NUMERIC,                  -- потолок эффекта, т (диапазон)
    effect_tons_max   NUMERIC,
    effect_usd_min    NUMERIC,
    effect_usd_max    NUMERIC,
    feasible          BOOLEAN,                  -- прошла ли фильтр оборудования
    relevance_score   NUMERIC,                  -- итоговый балл релевантности фабрике
    rank              INT,                      -- место в рейтинге по фабрике
    provenance        JSONB,                    -- какие loss_cell/rule/числа использованы
    dead_end_flag     BOOLEAN DEFAULT FALSE,    -- совпала с известным тупиком
    created_at        TIMESTAMP DEFAULT now()
);

-- Дорожная карта проверки --------------------------------------

-- Этапы проверки гипотезы (отбор проб, минералогия, лаб. флотация…).
-- shared_key позволяет сращивать одинаковые этапы разных гипотез.
CREATE TABLE roadmap_step (
    id             SERIAL PRIMARY KEY,
    hypothesis_id  INT NOT NULL REFERENCES hypothesis(id) ON DELETE CASCADE,
    step_order     INT NOT NULL,
    title          TEXT NOT NULL,              -- 'Отбор проб', 'Ситовой анализ'...
    shared_key     TEXT,                       -- одинаковый у сращиваемых этапов
    cost           NUMERIC,
    duration_days  INT,
    success_criterion TEXT,
    is_killer      BOOLEAN DEFAULT FALSE,      -- дешёвый этап, способный убить гипотезу
    status         TEXT NOT NULL DEFAULT 'planned'
                   CHECK (status IN ('planned','done','skipped'))
);

-- Артефакты экспериментов (петля обучения) ---------------------

-- Результат выполненного этапа, загруженный из реальной лаборатории.
-- Это то, что замыкает виртуальную лабораторию с реальной.
CREATE TABLE experiment_artifact (
    id               SERIAL PRIMARY KEY,
    roadmap_step_id  INT NOT NULL REFERENCES roadmap_step(id) ON DELETE CASCADE,
    hypothesis_id    INT NOT NULL REFERENCES hypothesis(id)   ON DELETE CASCADE,
    outcome          TEXT NOT NULL             -- итог этапа
                     CHECK (outcome IN ('success','failure','partial')),
    measured_value   NUMERIC,                  -- фактический замер (напр. реальный КПД раскрытия)
    predicted_min    NUMERIC,                  -- что предсказывал движок (для сравнения)
    predicted_max    NUMERIC,
    note             TEXT,                     -- интерпретация эксперта
    created_at       TIMESTAMP DEFAULT now()
);

-- Лог калибровки правил: как артефакт изменил коэффициент.
-- Даёт прозрачность «дообучения» — виден каждый сдвиг.
CREATE TABLE rule_calibration (
    id            SERIAL PRIMARY KEY,
    rule_id       INT NOT NULL REFERENCES rule(id) ON DELETE CASCADE,
    artifact_id   INT NOT NULL REFERENCES experiment_artifact(id) ON DELETE CASCADE,
    coeff_before  NUMERIC NOT NULL,
    coeff_after   NUMERIC NOT NULL,
    created_at    TIMESTAMP DEFAULT now()
);

-- База тупиков: обобщённая запись «что не сработало и почему».
-- Новая гипотеза сверяется с ней до попадания в рейтинг.
CREATE TABLE dead_end (
    id             SERIAL PRIMARY KEY,
    module_id      INT REFERENCES module(id),
    target_cause   TEXT,
    size_class_id  INT REFERENCES size_class(id),
    reason         TEXT NOT NULL,              -- почему тупик
    source_artifact_id INT REFERENCES experiment_artifact(id),
    created_at     TIMESTAMP DEFAULT now()
);

-- Индексы под частые выборки -----------------------------------
CREATE INDEX idx_loss_cell_plant     ON loss_cell(plant_id);
CREATE INDEX idx_hypothesis_plant    ON hypothesis(plant_id);
CREATE INDEX idx_evaluation_hyp      ON evaluation(hypothesis_id);
CREATE INDEX idx_roadmap_hyp         ON roadmap_step(hypothesis_id);
CREATE INDEX idx_artifact_step       ON experiment_artifact(roadmap_step_id);
