# ROADMAP: разработка системы Crawler через Claude Code

> **2026-04-27 — scope переопределён.** Phase 0 — это MVP-инструмент ручного
> режима (pull), а не production-ready система с push/scheduler/soak-test.
> Цель — проверить целесообразность инструмента до инвестиций в постоянное
> мониторинг-решение. Если MVP покажет ценность — Phase 1 строит fixed-monitoring;
> если нет — мы не потратили бюджет на etap-ы оркестрации, нотификаций и
> hardening зря. См. `crawler/todo-002.md` секция «Цель Phase 0».

Продуктовый план реализации Phase 0. Опирается на `CONCEPT.md` (раздел 9 KPI
обновлён под MVP) и `ARCHITECTURE.md` (разделы 4, 7, 8.1-8.3 помечены Phase 1+).

Главный принцип плана: **`core/contracts.py + core/models.py` — единственная
блокирующая точка во всей разработке**. После её прохождения граф работ
расходится в широкие параллельные ветки. Ничего из уже сделанного (Ветки 1+2
E1) не throwaway — все слои совместимы с pull-режимом без переделок.

Этап ≠ неделя. Этап — это смысловая единица.

---

## 0. Граф этапов и зависимостей

```
[E0] Архитектурный фундамент  ✅ done
        │
        ▼
[E1] End-to-end slice (Reddit → Pipeline → Signals в БД, без LLM)
   ├── Ветка 1: Storage backbone                          ✅ done
   ├── Ветка 2: Source + Pipeline backbone                ✅ done
   ├── Ветка 3: CLI + projects-CRUD + migration 002       ⏳ next
   └── Ветка 4: Интеграция (Reddit + Postgres + smoke)    ⏳
        │
        ▼
[E2] Embedding + Semantic Filter (Voyage 3.5 + pgvector)
        │
        ▼
[E3] LLM-классификация (Claude Haiku Batch + tool_use)
        │
        ▼
[E4] MCP-сервер (только MCP, без REST/WS)
        │
        ▼
Phase 0 готов к проверке: 3 numeric KPI + субъективная оценка
        │
        ▼
Если MVP полезен → Phase 1: scheduler, notifications, источники, ...
Если нет → закрываем проект, бюджет не сожжён
```

**Из старого плана удалены**: E2c (YAML-конфиг + второй проект — поглощается
Веткой 3 E1), E3-старый (расширение источников), E4-старый (orchestration +
scheduler + budget guard + bus), E5-старый (notifications full + inline-feedback),
E7-старый (hardening + 7-day soak). Всё это — Phase 1+.

**E6-старый сужен** до E4-нового: только MCP, без REST/WebSocket.

---

## 1. Этапы

### E0. Архитектурный фундамент ✅ DONE

Закрыт 2026-04-25. `core/CLAUDE.md` (1063 стр., 11 моделей + 9 Protocol +
12 событий + алгоритм content_hash). 4 ADR. Контракты v1.

---

### E1. End-to-end slice (Reddit → Pipeline → Signals в БД)

**Цель.** Один Reddit-пост проходит весь путь: Reddit API → Normalize →
Dedup → KeywordFilter → Decide (синтетический) → запись в `signals` → виден
через `crawler signals`. Без embedding/LLM. Это slice доказывает, что
контракты в `core/` правильные и pull-режим жизнеспособен.

#### Ветка 1: Storage backbone ✅ DONE

Закрыта 2026-04-25 (`d6728fa` совокупно с другими, см. историю). 4 таблицы
(`mentions`, `signals`, `scan_log`, `usage_log`), миграция 001, Repository
поверх asyncpg.

#### Ветка 2: Source + Pipeline backbone ✅ DONE

Закрыта 2026-04-27 (коммиты `b0959eb` + `d6728fa`). `BaseSource[ConfigT]` +
`BaseStreamingSource` иерархия (страховка под Phase 1+ источники), Reddit-source
через PRAW, Pipeline + PipelineContext, четыре стадии E1 (Normalize по
алгоритму core D + Dedup sha256-only + KeywordFilter + синтетический Decide),
FakeRepository. 62 unit-теста.

#### Ветка 3: CLI + projects-CRUD + migration 002 ⏳ NEXT

**Замещает** старую Ветку 3 (Notifications + CLI) и поглощает старую E2c
(YAML-конфиг).

**Затрагиваемые слои.** Новая миграция `storage/migrations/002_projects.sql`
с таблицей `projects`. Новый `crawler/cli/` с командами через Click или Typer.
`crawler/storage/repositories.py` расширяется методами project-CRUD.

**Артефакт-вход.** Архитектор пишет `cli/CLAUDE.md` (полная спецификация
команд + аргументов + поведения) **в начале ветки**.

**Задачи.**

1. Архитекторская сессия → `cli/CLAUDE.md`. Покрывает:
   - Все CLI-команды (`project create/list/show/delete`, `scan`, `signals`,
     `signal show`, `usage`).
   - Формат аргументов (multi-keyword, project_id-генерация, дата-фильтры).
   - Обработка ошибок (нет проекта, нет ментионов, источник упал).
   - Output-форматы (table, json, jsonl) — рекомендация.
   - Connection к Postgres (DSN из env, как в integration-тестах).
   - Расширение `IRepository`: `create_project`, `list_projects`,
     `get_project`, `delete_project`. Миграция 002.

2. Исполнительская сессия:
   - `storage/migrations/002_projects.sql` — таблица `projects` с YAML-source
     столбцом (для будущего импорта/экспорта, не используется в Phase 0)
     или без него.
   - Расширение `repositories.py`: 4 новых метода project-CRUD.
   - `crawler/cli/main.py` — Click/Typer entry-point. Команды:
     - `crawler project create --name=X --keywords="a,b" --excluded="c"`
     - `crawler project list`, `crawler project show <id>`, `crawler project delete <id>`
     - `crawler scan --project=X` — вызывает Source + Pipeline на реальном Repository
     - `crawler signals --project=X [--since=...] [--limit=...]`
     - `crawler signal show <signal_id>`
     - `crawler usage --project=X` (cost-tracking из usage_log)
   - Unit-тесты CLI (через `click.testing.CliRunner`) на FakeRepository.

3. Интеграционные тесты CLI с реальным Postgres (продолжение
   `tests/integration/`).

**Критерий готовности.**
- `crawler project create + list + delete` работают на реальном Postgres.
- `crawler scan --project=X` идёт в Reddit (нужны creds), кладёт ментионы +
  signals в БД.
- `crawler signals --project=X` показывает ленту.
- Повторный scan не дублирует ни ментионы, ни signals (idempotency).

**Точки решения владельца.** Нет — D6/D7 удалены, владелец сам создаёт проект.

**Ориентировочная стоимость.** 2 сессии (архитектор + исполнитель), $4-7,
~6 часов wall-clock владельца на ревью + интеграцию.

#### Ветка 4: Интеграционная сессия ⏳

**Цель.** Соединить Ветки 1-3 на реальном бэкенде. Прогнать smoke-тест
end-to-end на проекте, который владелец создаст сам.

**Затрагиваемые слои.** Без новых файлов; верификация уже написанного.

**Артефакт-вход.** Закоммиченные Ветки 1-3.

**Задачи.**

1. Поднять Docker postgres (`docker compose up -d`), применить миграции
   001 + 002.
2. Получить Reddit API creds (владелец, через https://www.reddit.com/prefs/apps,
   script-type). Положить в `.env`.
3. `crawler project create --name=mvp-test --keywords="..."` — реальный проект.
4. `crawler scan --project=mvp-test` — реальный scan. Проверить что:
   - PRAW-вызовы отрабатывают, rate-limit соблюдается.
   - Ментионы пишутся в БД с правильными `content_hash`.
   - Pipeline проходит без ошибок (стадии E1 + синтетический Decide).
   - Signals попадают в БД.
   - usage_log заполняется.
5. `crawler signals --project=mvp-test` — лента видна.
6. `crawler scan` повторный → дубликатов в БД нет.
7. **Перепрогон** интеграционных тестов `tests/integration/test_storage.py`
   (после ruff-косметики из Ветки 2).

**Критерий готовности.** Все шаги 1-7 зелёные. Владелец читает ленту
сигналов и говорит «осмысленно» / «спам» — это feedback на качество
keyword-фильтрации (ещё без LLM).

**Стоимость.** 1 сессия, $2-4. Владельцу ~30 минут на Reddit-creds + чтение
ленты.

---

### E2. Embedding + Semantic Filter

**Цель.** Pipeline получает четвёртую и пятую стадии. Voyage 3.5 эмбеддинги +
pgvector cosine-search. Hybrid search (BM25 + cosine + RRF) для поиска
по ленте.

**Затрагиваемые слои.** Новая миграция `003_embeddings.sql` (таблица
`mention_embeddings` с `vector(1024)` + HNSW-индекс). Новый
`processing/stages/embedding.py` + `semantic_filter.py`. Расширение
Repository (`search_hybrid`, `upsert_embedding`).

**Артефакт-вход.** Архитектор расширяет `processing/CLAUDE.md` секцией про
embedding/semantic_filter. Контракт `IEmbedder` уже в core (B.5).

**Задачи.**

- `IEmbedder`-имплементация для Voyage 3.5 через `voyageai` SDK.
- `EmbeddingStage` — батчинг 100 ментионов. Запись в `mention_embeddings`.
  `cost_usd` идёт в `usage_log`.
- `SemanticFilterStage` — cosine с эмбеддингами тем проекта. Threshold 0.55.
  `Project.queries[].topic_embedding` (заполняется при `project create`,
  один раз).
- BM25/`pg_search` GIN-индекс на `mentions.text_clean`.
- `Repository.search_hybrid` — RRF поверх BM25 + cosine.
- CLI: `crawler signals` получает флаг `--query="text..."` для семантического
  поиска по ленте.

**Критерий готовности.** Reddit-пост проходит pipeline до Decide с осмысленным
`relevance_score` (близкий к 1.0 для близкой темы). `crawler signals
--project=X --query="..."` возвращает результаты в порядке RRF.

**Стоимость.** 2 сессии (архитектура + реализация), $4-7.

---

### E3. LLM-классификация

**Цель.** Шестая стадия — `LLMClassifyStage`. `intent`, `sentiment`, `entities`,
`relevance_score` от Claude Haiku Batch.

**Затрагиваемые слои.** Новый `processing/stages/llm_classify.py` (+ `rank.py`,
обновлённый `decide.py`).

**Артефакт-вход.** Архитектор расширяет `processing/CLAUDE.md` секцией про
LLM-стадию (формат tool_use, batch-API, retry-policy).

**Задачи.**

- `IClassifier`-имплементация для Claude Haiku через `anthropic` SDK + Batch API.
- Tool `classify_post_v2` с фиксированной схемой output (intent/sentiment/
  entities/relevance/spam).
- `LLMClassifyStage`: assert что Normalize+Dedup+KeywordFilter+Embedding+
  SemanticFilter уже отработали (защита pipeline-каскада).
- `RankStage` — RRF поверх BM25 + dense + LLM relevance.
- `DecideStage` — финал: `signal_ready = score ≥ project.threshold AND NOT spam`.
- Cost-tracking каждого batch.

**Критерий готовности.** Один batch из 100 Reddit-постов проходит LLM,
у каждого Signal заполнены поля. Стоимость batch видна в `usage_log`.

**Точки решения.** **D8** — Haiku 4.5 (рекомендация ARCHITECTURE) или Sonnet
4.6. Решит владелец до старта E3.

**Стоимость.** 1 сессия, $3-5.

---

### E4. MCP-сервер (без REST/WebSocket)

**Цель.** Claude Code получает MCP-инструменты для управления проектами,
запуска scan, поиска по ленте. Это завершает Phase 0 — после E4 владелец
может работать с инструментом полностью через AI-куратора.

**Затрагиваемые слои.** Новый `crawler/mcp/server.py` (через MCP SDK).

**Артефакт-вход.** Архитектор пишет `mcp/CLAUDE.md` (список tools, схемы
аргументов, error-handling).

**Задачи.**

- MCP-сервер с tools:
  - `mcp_create_project(name, keywords, excluded_keywords)`
  - `mcp_list_projects()`
  - `mcp_run_scan(project_id, limit?)`
  - `mcp_search_signals(project_id, query?, since?, limit?)`
  - `mcp_get_project_state(project_id)` (включает usage)
- Auth — простой shared secret из env (для соло достаточно).
- Stdio-транспорт по умолчанию (Claude Code).
- Tools — thin-wrappers над теми же функциями, что использует CLI
  (общий backend в `crawler/api_core/`).

**Критерий готовности.** В реальном Claude Code клиенте: подключаемся к
MCP-серверу, вызываем `mcp_create_project`, `mcp_run_scan`, читаем результаты
через `mcp_search_signals`. Минимум один полный цикл сделан Claude Code-ом
без вмешательства человека (кроме первого запуска).

**Точки решения.** Удалена D13 (thin-wrappers vs независимые) — выбран
thin-wrappers без обсуждения.

**Стоимость.** 1-2 сессии, $4-7. **Включает тестирование в реальном Claude
Code клиенте.**

---

## 2. Точки решения владельца (сводный список)

Из 15 точек решения старого плана **остаётся одна**:

| ID | Решение | До какого этапа |
|----|---------|-----------------|
| D8 | Модель для LLM-классификации (`claude-haiku-4-5` vs `claude-sonnet-4-6`) | E3 |

**Удалены** (всё либо привязано к Phase 1+, либо отменено):
- D1-D5 (закрыты ADR в E0).
- D6 (Telegram-чат) — нет Telegram.
- D7 (тестовый keyword) — владелец сам создаёт проект.
- D9 (второй проект) — владелец сам создаёт.
- D10 (Visualping) — Phase 1+.
- D11 (APScheduler vs Prefect) — Phase 1+.
- D12 (inline-feedback семантика) — Phase 1+.
- D13 (MCP-tools архитектура) — выбран thin-wrappers без обсуждения.
- D14 (retention эмбеддингов) — Phase 1+ (нет retention в Phase 0).
- D15 (hard-limits на API-провайдере) — рекомендация владельцу настроить
  Anthropic + Voyage billing alerts вручную, не код.

---

## 3. Параллелизация и распределение работы по сессиям

После E0 + E1 (Ветки 1+2 done) — последовательное выполнение по умолчанию:

```
✅ E0 → ✅ E1.1 → ✅ E1.2 → ⏳ E1.3 → ⏳ E1.4 → E2 → E3 → E4
```

Можно параллелить **E2 и E3** после E1.4 (они независимы по файлам), но
для соло-разработчика последовательно проще ревью.

**Минимальный wall-clock путь** (1 параллельная сессия за раз):
~6-9 сессий Claude Code до закрытия Phase 0 (вместо старых 18-25).

---

## 4. Ориентировочная стоимость Phase 0

| Этап | Сессии | Цена ($, sonnet) | Время владельца |
|------|--------|------------------|-----------------|
| E0 | done | done | done |
| E1.1, E1.2 | done | done | done |
| E1.3 (CLI) | 2 | 4-7 | 4-6ч (ревью + Reddit creds) |
| E1.4 (интеграция) | 1 | 2-4 | 1-2ч (smoke-тест) |
| E2 | 2 | 4-7 | 1-2ч |
| E3 | 1 | 3-5 | 1-2ч (D8 решение) |
| E4 | 1-2 | 4-7 | 2-3ч (тест в Claude Code) |
| **Итого новый** | **7-8** | **17-30** | **9-15ч** |

**Бюджет на токены оставшейся работы**: ~$20-30 (было $50-80 в старом плане).
**Время владельца**: ~10-15ч (было 20-32ч).
**Operating cost** ($50/мес из ARCHITECTURE) — нерелевантен в pull-режиме,
тратятся только при ручных запусках scan. По прикидке E2+E3 — на одной
теме с 100 ментионов в день: ~$0.50/scan (Voyage + Haiku Batch). Месяц
ручных запусков (10/день) ≈ $150/мес — но это владелец контролирует
руками, не cron.

---

## 5. Критерии выхода из Phase 0

1. ✅ `crawler project create + scan + signals + signal show + usage` работают.
2. ✅ MCP-сервер запускается, Claude Code-клиент успешно проходит цикл
   create → run_scan → search_signals.
3. ✅ KPI #1 «time-to-first-signal <1 час»: новый проект → первый Signal в
   ленте за <1 час wall-clock.
4. ✅ KPI #3 «cost per actionable signal <$0.50»: один scan на 100 ментионов
   стоит < $0.50 суммарно (Voyage + Haiku Batch).
5. ✅ KPI #4 «hours-saved <2/week» (ориентир): субъективно, владелец оценивает.
6. ✅ Несколько проектов параллельно — данные не пересекаются.
7. **Главный gate**: владелец читает ленту реальных Signal-ов на реальной
   теме и субъективно говорит «полезно — продолжаем в Phase 1» или «не
   полезно — закрываем». Это **единственное условие** выхода из Phase 0
   (старые KPI #2/#5/#6/#7/#8 удалены).

---

## 6. Открытые вопросы продукт-агенту (за пределами D)

- Формат CLI multi-keyword (CSV vs повтор флага vs JSON-массив) — решит
  архитектор Ветки 3 в `cli/CLAUDE.md`.
- MCP authentication для соло-режима — решит архитектор E4 в `mcp/CLAUDE.md`.
- YAML-конфиг проектов как импорт/экспорт — оставлять в Phase 0 или
  удалить — рекомендация продукт-агента: **удалить** (CRUD через CLI
  достаточно, YAML — Phase 1+ когда появятся 5+ проектов и понадобится
  git-friendly бэкап).

---

## 7. Что НЕ делает этот roadmap

- **Не утверждает технологии** в спорных точках — это работа архитектора
  внутри каждого этапа.
- **Не назначает даты.**
- **Не описывает реализацию** — это работа архитектора в детальной
  документации каждого слоя (`core/CLAUDE.md`, `storage/CLAUDE.md` уже
  готовы; `cli/CLAUDE.md`, `mcp/CLAUDE.md` появятся).
- **Не покрывает Phase 1+.** Скоуп Phase 1 собирается отдельной
  продукт-сессией ПОСЛЕ закрытия Phase 0 и положительного gate-решения.

---

## Заключение

Roadmap построен вокруг одного факта: **Phase 0 — это валидация инструмента,
не строительство production-системы**. Если ручной режим окажется бесполезным
— стоп после E4. Если полезен — Phase 1 строит сверху scheduler, notifications,
расширение источников, hardening, retention. Тогда же возвращаются KPI #2,
#5, #6, #7, #8.

Главная защита от провала — **дисциплина gate в конце Phase 0**: не
автоматически переходим в Phase 1, а **сначала спрашиваем владельца**, есть
ли смысл. Если да — собираем roadmap Phase 1 заново, опираясь на наблюдения
из MVP-периода. Если нет — закрываем проект честно.

Roadmap утверждает **владелец**. Реализация каждого этапа — отдельные сессии
архитектора и исполнителя в режиме автономии (см. `crawler/todo-002.md`
секция «Главное: владелец занят» — обновлено 2026-04-27).
