# ROADMAP: разработка системы Crawler через Claude Code

Продуктовый план реализации Phase 0 (см. `CONCEPT.md` раздел 10). Не подменяет
архитектурный документ — опирается на него. Источники зависимостей и порядка
внутри слоёв — `ARCHITECTURE.md` + ответ архитектор-агента, цитируемый по тексту.

Главный принцип плана: **`core/contracts.py + core/models.py` — единственная
блокирующая точка во всей разработке**. После её прохождения граф работ
расходится в широкие параллельные ветки. Roadmap построен так, чтобы максимум
работ можно было пускать параллельными Claude Code сессиями, а не последовательно
по неделям.

Этап ≠ неделя. Этап — это смысловая единица: «что появилось в системе после,
чего не было до». Этапы могут идти параллельно. Внутри этапа — несколько задач.

---

## 0. Граф этапов и зависимостей

```
                       [E0] Архитектурный фундамент
                                   │
                                   ▼
                       [E1] End-to-end slice (Reddit → Telegram, без LLM)
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     [E2a] Embedding +     [E2b] LLM-классификация   [E2c] YAML-конфиг
     Semantic Filter       Claude Haiku Batch         + второй проект
              └────────────────────┬────────────────────┘
                                   ▼
                       [E3] Расширение источников (Bluesky, RSSHub)
                                   │
                                   ▼
                       [E4] Orchestration + Scheduler + Budget Guard
                                   │
              ┌────────────────────┴────────────────────┐
              ▼                                         ▼
     [E5] Notifications full                  [E6] API + MCP-сервер
     (filter, inline-feedback,                (REST, WebSocket,
     webhook channel)                         MCP tools)
              └────────────────────┬────────────────────┘
                                   ▼
                       [E7] Hardening (observability,
                            retention, healthchecks,
                            7 дней без вмешательства)
                                   │
                                   ▼
                       Phase 0 готов к проверке KPI
```

**Точки слияния:**
- После E1 — три параллельные ветки 2a/2b/2c. Сливаются перед E3.
- После E4 — две параллельные ветки 5/6. Сливаются перед E7.
- E7 закрывает KPI #8 (7 дней без вмешательства) — это критерий выхода из Phase 0.

---

## 1. Этапы

Формат: цель → затрагиваемые слои → артефакт-вход (что архитектор готовит до старта)
→ задачи → критерий готовности → точки решения владельца → ориентировочная стоимость.

### E0. Архитектурный фундамент

**Цель.** Зафиксировать всё, что **нельзя менять** после первого коммита кода.
Это не написание кода — это утверждение контрактов и решений.

**Затрагиваемые слои.** `core/` (документация), `storage/` (только решение по
размерности embedding и схеме `project_id`), общесистемные решения.

**Артефакт-вход.** `CONCEPT.md`, `ARCHITECTURE.md` (уже есть), ответ
архитектор-агента из todo-001 (сохранён в этой сессии).

**Задачи.**

1. Сессия архитектора: написать **детальный `repo-crawler/core/CLAUDE.md`**.
   Он должен закрыть: финальный список полей в `SourceQuery`, `RawMention`,
   `NormalizedMention`, `Signal`, `Project`, `TopicQuery`, `BudgetConfig`,
   `NotificationConfig`; финальные сигнатуры всех Protocol-ов; полный список
   `DomainEvent`-ов; формат `content_hash`; правила версионирования контрактов.
   Архитектор делает это **в отдельной сессии** — здесь продукт не нужен.

2. Сессия архитектора (продолжение или отдельная): добавить в core контракт
   **`IEmbedder`** (которого нет в текущем ARCHITECTURE) — с полями `dimensions: int`,
   `model_id: str`. Это нужно для дешёвой смены провайдера без миграций. Также
   добавить **`IQueue`** Protocol отдельно от `IEventBus` — очередь и шина
   должны быть разными абстракциями (рекомендация архитектора, блок D).

3. **ADR-сессия с владельцем** (не Claude Code, человек): принять 5 решений
   точек невозврата (см. секцию 2 ниже). Зафиксировать в `repo-crawler/ADR/`
   или в `core/CLAUDE.md` секцией «Решения».

**Критерий готовности.** `core/CLAUDE.md` закоммичен. ADR подписан. Размерность
embedding-вектора зафиксирована в SQL-схеме. Любой следующий агент, открывающий
`core/CLAUDE.md`, видит полные сигнатуры и не задаёт вопросов про контракты.

**Точки решения владельца** (см. секцию 2 — расширенный список):
- D1: размерность embedding (1024 / 1536 / 768).
- D2: Bluesky vs Telegram public для проверки streaming-паттерна в Phase 0.
- D3: Telethon (account+SIM) vs Bot API для Telegram public.
- D4: один Postgres vs data+queue Postgres.
- D5: формат `content_hash` (включает ли `source` в хеш).

**Ориентировочная стоимость.** 1 сессия архитектора (~$2–4 в Sonnet/Opus
токенах) + 1–2 часа владельца на ADR. Без E0 нельзя начинать E1, поэтому
здесь **не экономим**.

---

### E1. End-to-end slice (Reddit → Telegram, без LLM)

**Цель.** Один реальный Reddit-пост проходит весь путь: Reddit API → Normalize
→ Dedup (через `existing_hashes` в реальной Postgres) → KeywordFilter → Decide
(`relevance=1.0`, `intent='other'`, синтетический Signal) → запись в `signals`
→ отправка в Telegram-чат. Без embedding, без LLM, без YAML, без bus, без
scheduler, без MCP, без feedback-кнопок. Это «вертикальный жгут», который
доказывает, что контракты в `core/` правильные.

**Затрагиваемые слои.** `core/` (готов после E0), `storage/` (минимум: schema
+ репозиторий), `plugins/sources/reddit.py`, `processing/` (3 стадии + каркас),
`plugins/notifications/telegram.py`, `cli.py` (заглушка вместо `orchestration/`),
`config/bootstrap.py` (hardcoded Project вместо YAML).

**Артефакт-вход.** Закоммиченный `core/CLAUDE.md` после E0.

**Задачи (3 параллельные ветки).**

- **Ветка 1: Storage backbone.** `storage/CLAUDE.md` (от архитектора, в начале
  ветки), `schema.sql` с таблицами `mentions` (UNIQUE по `content_hash`),
  `signals`, `scan_log`, `usage_log` — все с `project_id` FK и `created_at`
  индексом (под будущий retention). Репозиторий с методами для slice:
  `bulk_upsert_with_dedup`, `existing_hashes`, `last_scanned_at`, `append_usage`,
  `budget_used`. **Не делать в этой ветке:** `embeddings`, `pg_search`/BM25,
  `notification_log`, `events`-таблицу. Они появятся в E2/E4/E5.

- **Ветка 2: Source + Pipeline backbone.** `plugins/sources/CLAUDE.md`,
  `processing/CLAUDE.md` (от архитектора). `plugins/sources/_base.py` +
  `reddit.py` (PRAW). `processing/pipeline.py` (каркас + `PipelineContext`).
  `processing/stages/normalize.py` (NFKC + langdetect + html-cleanup),
  `processing/stages/dedup.py` (sha256, **без MinHash** в slice),
  `processing/stages/keyword_filter.py`. Минималистичный `DecideStage` ставит
  `relevance=1.0`, `intent='other'`, `is_spam=False`. До слияния с веткой 1 —
  работает на in-memory fake-repository.

- **Ветка 3: Notifications + CLI.** `plugins/notifications/CLAUDE.md` (от
  архитектора). `plugins/notifications/_base.py` + `telegram.py` через aiogram
  (только `bot.send_message`, **без** inline-кнопок и **без** filter-движка).
  `cli.py` с командой `scan-once --project=foo`. `config/bootstrap.py` —
  hardcoded `default_project()` с одним keyword и одним subreddit. До слияния
  с ветками 1 и 2 — на фикстуре (тестовый JSON вместо Reddit, mock-pipeline).

- **Слияние.** Сессия интеграции: подключить реальные репозиторий + pipeline +
  Reddit + Telegram, пройти end-to-end на тестовом keyword («Anthropic» в
  r/ClaudeAI). Написать integration-тест с реальной Postgres в docker.

**Критерий готовности.** Вызов `python -m crawler.cli scan-once --project=demo`
поднимает Postgres в docker, идёт в Reddit, кладёт ментионы с дедупликацией в
БД, посылает первое сообщение в реальный Telegram-чат владельца. Повторный
запуск через минуту не дублирует ни записи в БД, ни сообщения.

**Точки решения владельца.**
- D6: Telegram-чат для разработки — личный или dev-канал. Тривиально, но нужно
  до первого коммита `telegram.py`.
- D7: тестовый keyword для slice (рекомендация: «Anthropic» — много контента,
  не персональный).

**Ориентировочная стоимость.** 4 сессии Claude Code (3 ветки + интеграция).
Каждая ветка — 1 сессия размером ~150–250к токенов (~$1–3 в Sonnet). Итого
~$5–12. Wall-clock: при 1 параллельной сессии — 4 запуска подряд; при 3
параллельных — ~2 захода (3 ветки → интеграция).

---

### E2a. Embedding + Semantic Filter

**Цель.** Pipeline получает четвёртую и пятую стадии. Semantic search через
pgvector работает. Hybrid search (BM25 + cosine + RRF) реализован.

**Затрагиваемые слои.** `storage/embedding_index.py` (новый), `processing/stages/embedding.py`,
`processing/stages/semantic_filter.py`. Расширение `repositories.py`
(`search_hybrid`).

**Артефакт-вход.** Решение D1 (размерность embedding из E0). `processing/CLAUDE.md`
(уже есть после E1) — но архитектор добавляет в него секцию про embedding и
semantic_filter ДО старта этого этапа (отдельная мини-сессия).

**Задачи.**

- Расширить `core/contracts.py` контрактом `IEmbedder` (если ещё не добавлен в E0).
- `storage/embedding_index.py` — pgvector колонка, HNSW-индекс, методы
  `upsert`, `search_semantic`, `search_hybrid`. Хранить в **отдельной таблице**
  от `mentions` (рекомендация архитектора, блок D — для дешёвого переезда на
  Qdrant в будущем).
- `processing/stages/embedding.py` — батчинг 100 ментионов через Voyage
  (или OpenAI/BGE по решению владельца). `cost_usd` в `Signal` обновляется.
- `processing/stages/semantic_filter.py` — cosine с эмбеддингами тем проекта,
  threshold 0.55. `Project` расширяется полем `topic_embeddings`
  (предрасчитанные эмбеддинги тем). Эмбеддинги тем считаются один раз при
  загрузке проекта, не на каждый scan.
- BM25/`pg_search` — добавляется здесь же (нужен для `search_hybrid`). GIN
  индекс на `mentions.text_clean`.
- Cost-tracking стадии: каждый embedding-вызов пишется в `usage_log`.

**Критерий готовности.** Reddit-пост проходит pipeline до Decide и в `signals`
у него есть осмысленный `relevance_score` (приближение к 1.0 для близкой темы,
к 0.0 для далёкой). Hybrid search через `repository.search_hybrid` возвращает
результаты в порядке RRF.

**Точки решения владельца.** Уже принято в E0 (D1).

**Ориентировочная стоимость.** 1–2 сессии (~$3–6).

---

### E2b. LLM-классификация (Claude Haiku Batch)

**Цель.** Pipeline получает шестую стадию — `LLMClassifyStage`. Заполняются
`intent`, `sentiment`, `entities`, `topics`, `relevance_score` корректируется
LLM-оценкой. `RankStage` и финальный `DecideStage` дополняются.

**Затрагиваемые слои.** `processing/stages/llm_classify.py`,
`processing/stages/rank.py`, `processing/stages/decide.py` (доработка).

**Артефакт-вход.** Архитектор готовит секцию в `processing/CLAUDE.md` про
LLM-стадию — формат tool_use, batch-API, retry-policy на rate limits.

**Задачи.**

- `LLMClassifyStage` через Claude Haiku Batch + tool_use. Один `classify_post_v2`
  tool с фиксированной схемой output. Batch — обязательно (ARCHITECTURE 5.4).
  Никогда не запускать LLM до того, как все четыре дешёвые стадии отсекли шум —
  это должно быть закреплено assert'ом в `pipeline.py` (если в `pipeline_trace`
  нет всех четырёх стадий до LLM — pipeline кидает ошибку).
- `RankStage` — RRF поверх BM25 + dense + LLM relevance.
- `DecideStage` — финал: `signal_ready = score ≥ project.threshold AND NOT spam`.
- Cost-tracking: каждый batch пишется в `usage_log` с детализацией по проекту
  и по сообщению.

**Критерий готовности.** Один batch из 100 Reddit-постов проходит
LLM-классификацию, у каждого Signal заполнены `intent`, `sentiment`, `entities`.
Стоимость батча видна в `usage_log` и сравнима с ARCHITECTURE 5.2 ($0.50/1k).

**Точки решения владельца.**
- D8: модель для классификации — `claude-haiku-4-5` (рекомендация
  ARCHITECTURE) или `claude-sonnet-4-6` (если качество критичнее цены).
  Решение влияет на cost-per-signal KPI.

**Ориентировочная стоимость.** 1 сессия (~$3–5).

---

### E2c. YAML-конфиг + второй проект

**Цель.** Проекты создаются YAML-файлами без правки кода. Появляется второй
проект. Validate-команда проверяет конфигурацию до запуска.

**Затрагиваемые слои.** `config/store.py`, `config/projects/*.yaml`,
`config/topics/*.yaml`, новая CLI-команда `deploy`.

**Артефакт-вход.** `config/CLAUDE.md` (от архитектора, перед стартом этапа).

**Задачи.**

- `ConfigurationStore` с методами `load_from_yaml`, `save`, `list_projects`,
  `validate` (имена источников/стадий должны существовать в plugin-регистре,
  cron-выражения валидны, бюджет ненулевой, threshold ∈ [0,1]).
- Сериализация `Project` обратно в `projects.yaml_source: text` колонку БД
  (источник истины — таблица `projects`, YAML-файлы — git-friendly бэкап).
- Topics как переиспользуемые блоки (`config/topics/`).
- CLI: `python -m crawler.cli deploy config/projects/{project}.yaml`.
- Второй проект — реальный Phase 0 проект (например, `ar-mat-monitor.yaml`
  из ARCHITECTURE 6.1, в облегчённой версии: только Reddit, две темы).

**Критерий готовности.** Два проекта запускаются параллельно через два
вызова `deploy`, и сигналы от них не пересекаются ни в БД, ни в Telegram
(благодаря `project_id` FK).

**Точки решения владельца.**
- D9: какой второй проект — `ar-mat-monitor` (бренд+конкуренты) или
  `danang-devs` (discovery людей по гео)? Влияет на то, какие источники
  нужны раньше в E3.

**Ориентировочная стоимость.** 1–2 сессии (~$3–5).

---

### E3. Расширение источников

**Цель.** Подключить ещё 2 источника — RSSHub и Bluesky (или Telegram public,
по решению D2/D3 из E0). Это **архитектурная валидация** plugin-абстракции:
второй и третий источники проверяют, что `BaseSource` действительно
переиспользуем для разных паттернов интеграции (REST-pull, прокси-агрегатор,
firehose/streaming).

**Затрагиваемые слои.** `plugins/sources/rsshub.py`, `plugins/sources/bluesky.py`
(или `telegram_public.py`). Возможные правки в `BaseSource` если паттерн не
влезает.

**Артефакт-вход.** Уже работающий `plugins/sources/_base.py` после E1.

**Задачи.**

- **RSSHub** — простейший. Self-hosted instance в docker-compose. Адаптер
  ходит за RSS-фидами, конвертит в `RawMention`. Покрывает HN, Substack,
  Twitter (без аккаунта), GitHub releases. Один источник — десятки
  платформ.
- **Bluesky через AT Protocol** — firehose через Jetstream. Long-running
  consumer. Если в `BaseSource` обнаруживается несовместимость со streaming
  (например, `search()` ожидает `AsyncIterator` без long-lived connection) —
  это блокер: переписать `BaseSource` сейчас, потом дороже.
- **ИЛИ** Telegram public через Telethon (если владелец выбрал D3=Telethon
  и D2=Telegram-public-приоритет): отдельный SIM, session-файл, MTProto.
  Архитектурно сложнее Bluesky, но критично для сценария 3.4 (CONCEPT 3.4 —
  Discovery людей в Дананге).

**Критерий готовности.** Те же два проекта из E2c теперь видят сигналы из
2–3 источников, не только Reddit. Capabilities-driven диспетчеризация
(ARCHITECTURE 3.3) работает: проект с `geo: VN` корректно использует
источник со `supports_geo=False` через post-filter.

**Точки решения владельца.** Уже частично в E0 (D2/D3). Здесь —
D10: подключать ли Visualping в E3 или отложить в E4+. Если сценарий 3.6
(регуляторный мониторинг) важен — Visualping нужен в Phase 0 (см. ответ
архитектора, блок E).

**Ориентировочная стоимость.** 2–3 сессии (~$5–9), включая работу с
Bluesky-firehose, который сложнее обычного pull.

---

### E4. Orchestration + Scheduler + Budget Guard + Bus

**Цель.** Система работает **без ручного запуска CLI**. Cron-расписания
проектов исполняются. Бюджет соблюдается. События идут через bus.

**Затрагиваемые слои.** `orchestration/scheduler.py`, `orchestration/budget_guard.py`,
`orchestration/dispatcher.py`, `bus/postgres_bus.py`, триггеры в `storage/migrations/`.

**Артефакт-вход.** `orchestration/CLAUDE.md` и `bus/CLAUDE.md` от архитектора.

**Задачи.**

- `bus/postgres_bus.py` — INSERT в `events` + LISTEN/NOTIFY trigger. Контракт
  `IEventBus` без утечки postgres-специфики наружу.
- `orchestration/dispatcher.py` — слушает `ScanRequested`, находит `ISource`,
  вызывает `estimate_cost`, при OK — запускает `source.search`, по завершению
  эмиттит `ScanFinished`.
- `orchestration/budget_guard.py` — перед каждым `ScanRequested` проверяет
  `usage_log` против `budget`. При 80% — `BudgetWarning`. При 95% —
  `BudgetExhausted` и пропуск.
- `orchestration/scheduler.py` — APScheduler крутит cron-выражения проектов.
  При `deploy` нового проекта — расписания регистрируются динамически.
- `retry_policy.py` — exponential backoff, circuit breaker по
  `source.health_check`.
- Pipeline переходит с прямого вызова в CLI на подписку: `MentionsFetched`
  → батч в pipeline → `SignalReady`. Notifier подписывается на `SignalReady`.

**Критерий готовности.** `python -m crawler.orchestration.run` запускает
один процесс, который ничего не требует от человека: проекты крутятся по
своим расписаниям, бюджеты соблюдаются, сигналы летят в Telegram. Если
выключить процесс на час и включить — `last_scanned_at` обеспечивает
отсутствие пропусков и дубликатов.

**Точки решения владельца.**
- D11: APScheduler vs Prefect. Архитектор: «дёшево свапается, можно отложить».
  Рекомендация: APScheduler для Phase 0, Prefect — если в Phase 1 появится
  multi-host оркестрация.

**Ориентировочная стоимость.** 2–3 сессии (~$5–9). Этап содержит самую
тонкую интеграцию (bus + scheduler + budget) — закладывайте запас по
интеграционному тестированию.

---

### E5. Notifications full

**Цель.** Telegram-канал перестаёт быть «один сигнал → одно сообщение». Появляется
filter-движок, дедупликация алертов, inline-feedback кнопки, второй channel
(webhook). KPI #2 (FP rate < 10%) становится измеряемым.

**Затрагиваемые слои.** `plugins/notifications/telegram.py` (расширение),
`plugins/notifications/webhook.py` (новый), `storage/notification_log` таблица,
`api/routes/feedback.py` (callback-роут — но api/ ещё не построен; либо
временно отдельный bot-poller, либо ждём E6 с минимальным API).

**Артефакт-вход.** `plugins/notifications/CLAUDE.md` (расширение от
архитектора).

**Задачи.**

- Filter-движок в `NotificationConfig.filter`. Минимальный mini-DSL:
  `relevance_score >= 0.75 AND intent != 'advertisement'`. Парсер на простом
  AST или CEL-Python. Без выражений notifier превратится в спам — это уже
  не удобство, а необходимость.
- `notification_log` таблица + UNIQUE(project_id, signal_id, channel, target).
- Inline-кнопки `✅ Релевантно` / `❌ Шум` / `🚫 Заблокировать автора`.
  Callback → `feedback_log` таблица.
- Pre-MVP feedback handler: либо bot-poller внутри `notifications/telegram.py`,
  либо ждём E6 с минимальным API. Решение продукта — ждать E6 (избежать
  дублирования логики), но если KPI #2 нужно измерить раньше — встраиваем
  bot-poller. Рекомендация: сделать bot-poller в этом этапе, потому что
  feedback-data копится с момента первого алерта.
- `webhook.py` notifier — POST на любой URL. Нужен для архитектурной
  валидации plugin-абстракции (см. ответ архитектора, блок F).

**Критерий готовности.** Сигнал, попавший в Telegram, имеет три кнопки.
Нажатие на «❌ Шум» сохраняется в `feedback_log`. Один и тот же сигнал
не приходит дважды в один и тот же чат (благодаря `notification_log`).
Webhook notifier работает на тестовом endpoint.

**Точки решения владельца.**
- D12: семантика inline-feedback. Архитектор вынёс в открытый вопрос (блок
  H, пункт 5): «❌ Шум» — это про автора, ключевую фразу, тему, или
  комбинацию? Без решения D12 `feedback_log` будет писать только `mention_id`,
  и аналитика feedback-loop будет ограниченной. Решение влияет на схему
  `feedback_log`, поэтому **должно быть до старта E5**.

**Ориентировочная стоимость.** 2 сессии (~$4–7).

---

### E6. API + MCP-сервер

**Цель.** Все операции системы доступны через REST/WebSocket. Claude Code
получает MCP-инструменты для поиска по своей же БД мониторинга.

**Затрагиваемые слои.** `api/main.py`, `api/auth.py`, `api/routes/*`,
`api/ws.py`, `api/mcp_server.py`.

**Артефакт-вход.** `api/CLAUDE.md` (от архитектора, перед стартом).

**Задачи.**

- `api/main.py` + `api/auth.py` (API-key или JWT, для соло — простой shared
  secret в `.env`).
- `routes/projects.py` (CRUD), `routes/signals.py` (read-only лента с
  фильтрами), `routes/sources.py` (GET capabilities), `routes/usage.py`
  (бюджет/расходы), `routes/feedback.py` (FP/FN отметки — мигрируем сюда из
  bot-poller, если он был в E5).
- `routes/ws.py` — WebSocket /ws/signals?project_id=...&token=... — клиент
  получает JSON-сообщения каждый раз при `SignalReady` через bus.
- `api/mcp_server.py` — MCP-tools `mcp_search_signals`, `mcp_get_project_state`,
  `mcp_create_project`, `mcp_run_scan`. Архитектор в ответе на todo-001 (блок F)
  отмечает: MCP полезен только когда в БД накопились сигналы — после E1
  это уже выполнено. Реализация в E6 — оптимальный момент.

**Критерий готовности.** `curl GET /signals?project_id=ar-mat-monitor` возвращает
последние сигналы. Веб-клиент (заглушка на curl или httpie) видит ленту в
реальном времени через WebSocket. Claude Code в новой сессии через MCP
успешно вызывает `mcp_search_signals("ar-mat-monitor", "AR yoga mat", 10)` и
получает 10 результатов.

**Точки решения владельца.**
- D13: MCP-tools — thin-wrappers над REST или независимые. Открытый вопрос
  архитектора (блок H, пункт 6). Влияет на структуру `api/`. Решение нужно
  до старта E6.

**Ориентировочная стоимость.** 2–3 сессии (~$5–9). MCP-сервер — отдельная
сессия, потому что требует тестирования в реальном Claude Code клиенте.

---

### E7. Hardening (observability + retention + healthchecks)

**Цель.** Закрыть KPI #8 — система выживает 7 дней без вмешательства. Это
финальная проверка перед выходом из Phase 0.

**Затрагиваемые слои.** Все, плюс новый `observability/` или дополнения в
существующие модули. `storage/retention.py` (новый job).

**Артефакт-вход.** Всё уже есть. Архитектор готовит секцию «Observability»
в нескольких CLAUDE.md (точечная сессия — добавить инструкции для логов
во все слои, не одну отдельную папку).

**Задачи.**

- **Structlog уже должен быть с E1** (рекомендация архитектора в блоке F:
  «потом не приделать»). В E7 — только верификация: каждый stage пишет
  правильные ключи (`project_id`, `scan_id`, `mention_id`, `stage_name`).
- **Prometheus метрики**: `mentions_processed_total{project,stage}`,
  `pipeline_duration_seconds{stage}`, `budget_used_usd{project,source}`,
  `source_health{source}`. Endpoint `/metrics` в `api/`.
- **OpenTelemetry трейсы**: scan → batch → stage → notification как connected
  spans. Полезно для отладки «почему сигнал не дошёл».
- **Retention job** — cron-задача в scheduler: после 90 дней — удаляет `text`,
  `text_clean` из mentions (метаданные оставляет). После 180 — удаляет ментион
  целиком (cascade на signals). Эмбеддинги — отдельный вопрос (D14).
- **Healthchecks** на уровне docker-compose: postgres healthcheck, source
  health check каждые 5 минут.
- **Auto-restart** в docker-compose с `restart: unless-stopped`.
- **Cost-prediction агрегатор**: команда `python -m crawler.cli predict --project=X`
  показывает предсказанную месячную стоимость на основе `estimate_cost` всех
  источников + средняя стоимость pipeline.
- **Запустить систему на 7 дней** на VPS, мониторить через Prometheus.

**Критерий готовности.** Через 7 дней работы без человеческих вмешательств:
ноль необработанных событий в очереди, бюджет соблюдён по всем проектам,
ноль повторных алертов в Telegram, ноль unhandled exceptions в логах. KPI #8
закрыт. **Phase 0 готов.**

**Точки решения владельца.**
- D14: что делать с эмбеддингами на retention. Открытый вопрос архитектора
  (блок H, пункт 4). Удалять вместе с текстом или оставлять для семантической
  истории? Решение нужно до запуска retention job.
- D15: hard-limits на API-провайдере (Anthropic billing alerts, Voyage spend
  cap). Открытый вопрос архитектора (блок H, пункт 7). Это не код, а админ-
  настройки в провайдер-консолях, но без них `BudgetGuard` — единственный
  слой защиты от cost-overrun.

**Ориентировочная стоимость.** 2–3 сессии (~$5–9) на код + 7 дней
астрономического времени на soak-test.

---

## 2. Точки решения владельца (сводный список)

| ID | Решение | До какого этапа |
|----|---------|-----------------|
| D1 | Размерность embedding-вектора (1024 / 1536 / 768) и провайдер (Voyage / OpenAI / BGE self-hosted). Точка невозврата на уровне SQL. | E0 (до первой миграции) |
| D2 | Bluesky vs Telegram public для проверки streaming-паттерна в Phase 0 (если выбираем 3 источника, какой третий) | E0 (влияет на core-контракты для streaming) |
| D3 | Telethon (account+SIM) vs Bot API для Telegram public (если D2 = Telegram) | E0 |
| D4 | Один Postgres vs data+queue Postgres (физическое разделение) | E0 (определяет docker-compose) |
| D5 | Формат `content_hash` — включает ли `source` в хеш | E0 (точка невозврата) |
| D6 | Telegram-чат для разработки (личный или dev-канал) | E1 |
| D7 | Тестовый keyword для slice (рекомендация: «Anthropic») | E1 |
| D8 | Модель для LLM-классификации (`claude-haiku-4-5` vs `claude-sonnet-4-6`) | E2b |
| D9 | Какой второй проект (ar-mat-monitor vs danang-devs vs habit-timer) | E2c |
| D10 | Включать ли Visualping в Phase 0 (для сценария 3.6 регуляторный мониторинг) | E3 |
| D11 | APScheduler vs Prefect | E4 (рекомендация: APScheduler) |
| D12 | Семантика inline-feedback (`mention_id` only vs `feedback_target` enum) | E5 (открытый вопрос архитектора, блок H пункт 5) |
| D13 | MCP-tools — thin-wrappers над REST или независимые | E6 (открытый вопрос архитектора, блок H пункт 6) |
| D14 | Что делать с эмбеддингами на retention (удалять / оставлять) | E7 (открытый вопрос архитектора, блок H пункт 4) |
| D15 | Hard-limits на API-провайдере (Anthropic billing alerts, Voyage spend cap) | E7 (открытый вопрос архитектора, блок H пункт 7) |

**D1–D5 — точки невозврата.** Их нужно зафиксировать в ADR до E1, иначе цена
смены потом — миграция всего.

**D6–D15 — точки настройки.** Влияют на конкретные этапы, но не блокируют
архитектуру. Решаются непосредственно перед соответствующим этапом.

---

## 3. Параллелизация и распределение работы по сессиям

**Что не параллелится никогда:**
- E0 → всё остальное. Контракты — единственная блокирующая точка.
- E7 идёт после всего остального (это integration-soak).

**Что параллелится после E0:**
- E1 разбит на 3 параллельные ветки (Storage / Source+Pipeline / Notifications+CLI)
  + сессия интеграции.

**Что параллелится после E1:**
- E2a, E2b, E2c — три параллельные ветки. Не пересекаются по файлам.
- E3 не параллелится с E2 — расширение источников требует, чтобы LLM-стадия
  и YAML-конфиг работали (иначе не на чем тестировать новые источники).

**Что параллелится после E4:**
- E5 и E6 — параллельные ветки. Notifications дополняет уже работающую
  цепочку, API строится поверх готового storage+bus.

**Минимальный wall-clock путь** (1 параллельная сессия за раз):
E0 → E1 (4 sub-сессии) → E2a → E2b → E2c → E3 → E4 → E5 → E6 → E7
≈ 14–18 сессий Claude Code + 7 дней soak-test.

**Максимальный параллельный путь** (3 одновременные сессии):
E0 → [E1.1 ‖ E1.2 ‖ E1.3] → E1.merge → [E2a ‖ E2b ‖ E2c] → E3 → E4 → [E5 ‖ E6] → E7
≈ 9–11 «волн» сессий + soak-test. Wall-clock примерно вдвое короче, но
требует, чтобы владелец удерживал контекст 3 параллельных сессий
одновременно — это нагрузка на ревью.

**Рекомендация продукта.** Идти **гибридно**: E0 и E1 — параллельно
(там низкий риск пересечений), E2 — параллельно (три ветки), E3–E7 —
последовательно (интеграционная сложность растёт, лучше держать одну
сессию в фокусе).

---

## 4. Ориентировочная стоимость Phase 0

| Этап | Сессии Claude Code | Цена ($, Sonnet 4) | Время владельца (часы) |
|------|---------------------|---------------------|------------------------|
| E0 | 1–2 (архитектор) + ADR | 4–8 | 2–4 (ADR-решения) |
| E1 | 4 (3 ветки + интеграция) | 5–12 | 4–6 (ревью кода) |
| E2a | 1–2 | 3–6 | 1–2 |
| E2b | 1 | 3–5 | 1–2 |
| E2c | 1–2 | 3–5 | 1–2 |
| E3 | 2–3 | 5–9 | 2–3 |
| E4 | 2–3 | 5–9 | 3–4 |
| E5 | 2 | 4–7 | 2–3 |
| E6 | 2–3 | 5–9 | 2–4 |
| E7 | 2–3 + 7 дней soak | 5–9 | 2 + monitoring |
| **Итого** | **18–25** | **42–79** | **20–32** |

Бюджет на токены Phase 0: **~$50–80**. Время владельца на ревью и решения:
**~25–30 часов**, распределённых на 2–4 месяца (см. CONCEPT 10 — Phase 0
рассчитан на «месяц по выходным», но реалистично 2–3 месяца с учётом
параллелизации и soak-test).

Это в дополнение к месячному операционному бюджету ($50/мес из ARCHITECTURE
4.3 — на API-вызовы Anthropic/Voyage/Apify в работающей системе).

---

## 5. Критерии выхода из Phase 0

Из CONCEPT раздел 10: «все 8 метрик успеха достигнуты в течение трёх месяцев
подряд». Сводный чек-лист:

- [ ] Time-to-first-signal на новом проекте < 1 час (проверяется в E2c)
- [ ] False positive rate в Telegram < 10% (измеряется через feedback_log
      из E5, проверяется в E7)
- [ ] Стоимость за actionable signal < $0.50 (выводится из usage_log,
      проверяется в E7)
- [ ] Покрытие важных каналов > 80% (проверяется субъективно владельцем)
- [ ] Часов в неделю на ручной обзор < 2 (отслеживается владельцем)
- [ ] Время до знания о значимом действии конкурента < 24 часов
      (проверяется через 1–2 месяца после E7)
- [ ] Качественные customer-pain сигналы ≥ 3 в неделю
      (проверяется через 1–2 месяца после E7)
- [ ] Время выживания системы без вмешательства ≥ 7 дней (E7)

Phase 0 готов к закрытию, когда все 8 пунктов отмечены. Это не задача
roadmap — это задача для будущей продукт-сессии после E7.

---

## 6. Открытые вопросы к владельцу (за пределами D1–D15)

Архитектор выделил эти вопросы в блоке H своего ответа. Они **не относятся к
конкретному этапу**, а определяют общесистемные политики. Желательно решить
их до E1, но без них можно стартовать (важно зафиксировать как «решение
отложено»).

1. **KPI #8 «7 дней без вмешательства» — что считается «активной работой»?**
   Без падений? Без передёргивания cron'а? Без переключения VPN? Влияет на
   retry-policy и auto-restart в E4/E7.

2. **Множественные роли владельца внутри одного человека** (CONCEPT 2.2:
   архитектор / потребитель / тренер / AI-куратор) — нужны ли разные
   API-ключи или интерфейсы? Архитектурно простой ответ: один auth, разные
   режимы UI. Но если будет два разных Telegram-чата (sigals + admin) —
   это два разных `target` в notifications.

3. **Что считается «MVP-сценарием для проверки концепции»?** Архитектура
   позволяет 7 канонических сценариев из CONCEPT 3. Какой из них выбираем
   первым после E2c? Рекомендация продукта: **3.1 (мониторинг бренда)** —
   простейший в настройке (точные ключи), быстрый feedback для тренировки
   FP-фильтра.

---

## 7. Что НЕ делает этот roadmap

- **Не утверждает технологии** в спорных точках — это решение владельца
  (см. D1–D15).
- **Не назначает даты** — этап ≠ неделя. Сроки зависят от темпа сессий.
- **Не описывает реализацию** — это работа архитектора в детальной
  документации каждого слоя (`core/CLAUDE.md`, `storage/CLAUDE.md` и т.д.)
  и исполнителей в каждой ветке.
- **Не покрывает Phase 1+.** CONCEPT раздел 10 даёт ориентиры, но roadmap
  Phase 1 собирается продукт-агентом отдельной сессией после закрытия Phase 0.
- **Не задаёт KPI самому себе.** Roadmap — инструмент, а не метрика. Его
  качество проверяется через успешное прохождение этапов, а не через
  собственную «полноту».

---

## Заключение

Roadmap построен вокруг одного архитектурного факта: **`core/contracts.py`
— единственная блокирующая точка, после которой граф работ распадается на
максимально широкие параллельные ветки**. Это даёт два рычага:

1. **Скорость.** При параллельных сессиях Phase 0 проходится в 2 раза быстрее,
   чем при последовательной разработке по неделям из ARCHITECTURE раздел 11.
   Архитектурный roadmap-каркас (12 недель) — это последовательный путь.
   Продуктовый roadmap (этот документ) — параллельный.

2. **Изоляция риска.** Каждая ветка — отдельная сессия Claude Code с
   собственным CLAUDE.md и явной границей ответственности. Регрессии
   локализованы. Если ветка не получилась — переписываем её одну, не весь
   slice.

Главная защита от провала — **дисциплина E0**. Если контракты не
зафиксированы или ADR не подписан до E1 — все остальные этапы становятся
зыбкими, и через 4–6 недель неизбежно потребуется migration plan.
Если E0 пройден чисто — остальное собирается за известную сумму усилий и
известную сумму денег.

Roadmap утверждает **владелец**. Решения D1–D15 — на нём. Реализацию каждого
этапа ведут отдельные агенты-исполнители по детальным CLAUDE.md от
архитектора. Продукт-агент возвращается в работу при изменениях scope, при
закрытии этапа (валидация критерия готовности), и при подготовке roadmap
Phase 1.
