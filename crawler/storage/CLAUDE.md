# Storage — Postgres-репозитории и схема

Слой персистентного хранилища системы `crawler`. Реализует `IRepository` и связанные абстракции из `core/contracts.py` поверх Postgres 16 + pgvector + pgmq. Никто из других слоёв не пишет SQL напрямую — только через интерфейсы этого слоя.

**Этот документ — техническое задание для агента-исполнителя E1 / Ветка 1.** После него четыре файла (`storage/schema.sql`, `storage/migrations/001_initial.sql`, `storage/database.py`, `storage/repositories.py`) пишутся «по сигнатурам и SQL-блокам» без архитектурных вопросов.

**Scope этой сессии — только E1.** Всё, что относится к E2a (embeddings, BM25), E4 (events, pgmq), E5 (notifications/feedback), E2c (projects table) — явно НЕ создаётся, см. раздел **D**.

## Дисциплина импортов

`storage/` импортирует: `core/`, `stdlib`, `asyncpg`, `pydantic`. Не импортирует: `processing/`, `plugins/*`, `api/`, `bus/`, `orchestration/`. Если репозитория «знает» о бизнес-логике — это сигнал, что метод нужно вынести в processing-слой.

## Mapping разделов на файлы

| Раздел документа | Файл (создаётся в E1 / Ветка 1) |
|---|---|
| A. SQL-схема | `storage/schema.sql` (декларативный snapshot для документации) и `storage/migrations/001_initial.sql` (применяемая миграция) |
| B. Стратегия миграций | `storage/migrate.py` (runner) + `storage/migrations/*.sql` |
| C. Connection pool, Database | `storage/database.py` |
| C. Репозиторий | `storage/repositories.py` |
| E. SQL-стратегии методов | реализация — в `repositories.py`; контракт-блоки здесь |

## ADR-trail

| Раздел документа | ADR | Содержание |
|---|---|---|
| A.1 (mentions схема), A.4 (`content_hash` UNIQUE) | ADR-0004 | content_hash без source — глобальный UNIQUE-ключ дедупа |
| C.1 (Database, единый pool) | ADR-0003 | один Postgres-инстанс; pgvector+pgmq+LISTEN/NOTIFY на одном connection pool |
| D (про future embeddings) | ADR-0001 | dim=1024; вектор в **отдельной таблице** `mentions_embeddings`, появится в E2a |

## Инварианты

1. **`asyncpg` напрямую, без SQLAlchemy ORM.** ARCHITECTURE 2.1 + ADR-0003 — мы не тащим SQLAlchemy в проект ради миграций. Это упрощает trace stack и снижает overhead per query.
2. **Все таблицы создаются в одной миграции `001_initial.sql`.** В Phase 0 не разносим по миграциям ради чистоты — одна транзакция, одна точка применения. Эволюция с E2a.
3. **`content_hash CHAR(64) NOT NULL UNIQUE`** — единственный глобальный дедуп-ключ (ADR-0004). UNIQUE-индекс создаётся автоматически.
4. **Все таблицы имеют `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`** — это invariant под будущий retention в E7. Поле обязательно даже если кажется избыточным для конкретной таблицы.
5. **Все temporal-поля — `TIMESTAMPTZ`** (не `TIMESTAMP`). asyncpg отдаёт их как tz-aware datetime в UTC, что соответствует инварианту core (раздел A) о tz-aware datetime во всех моделях.
6. **Денежные значения — `NUMERIC(12,6)`** для `cost_usd`, `monthly_usd` и др. Decimal в Python ↔ NUMERIC в Postgres — нативный mapping без потери точности. Точность 6 знаков после точки: $0.000001 — достаточно для embedding-токенов ($0.06/1M tokens = $0.00006/1k tokens).
7. **`init_extensions()` фейлится при старте, если pgvector или pgmq недоступны.** Это раннее обнаружение — миграции не запускаются на «голом» Postgres без расширений. (pgmq в Phase 0 не используется по-настоящему — он закладывается в E4. Но загрузка extension стоит копейки, а отсутствие — индикатор сломанной среды.)

---

## A. SQL-схема для scope E1

Четыре таблицы. Все `IF NOT EXISTS` опускаем — миграции версионированы, повторного применения не должно быть.

### A.1. `mentions`

Глобальный кеш контента, дедуплицирующийся через `content_hash`. Хранит сразу поля `RawMention` и поля, добавленные на стадии Normalize (`NormalizedMention`). Embedding-колонка **не создаётся** — придёт в E2a в **отдельной таблице** `mentions_embeddings(mention_id UUID FK, vector vector(1024))` (ADR-0001 + ADR-0003).

```sql
CREATE TABLE mentions (
    id                       UUID         PRIMARY KEY,
    content_hash             CHAR(64)     NOT NULL UNIQUE,
    -- Origin
    source_id                TEXT         NOT NULL,
    external_id              TEXT         NOT NULL,
    -- RawMention payload
    author                   TEXT,
    author_id                TEXT,
    text                     TEXT         NOT NULL,
    text_html                TEXT,
    url                      TEXT         NOT NULL,
    lang_hint                TEXT,
    engagement               JSONB        NOT NULL DEFAULT '{}'::jsonb,
    raw                      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    published_at             TIMESTAMPTZ  NOT NULL,
    discovered_at            TIMESTAMPTZ  NOT NULL,
    fetched_at               TIMESTAMPTZ  NOT NULL,
    -- NormalizedMention payload
    text_clean               TEXT         NOT NULL,
    lang                     TEXT         NOT NULL,
    is_html_stripped         BOOLEAN      NOT NULL,
    normalize_version        INTEGER      NOT NULL DEFAULT 1,
    tracking_params_removed  TEXT[]       NOT NULL DEFAULT '{}',
    -- Meta
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mentions_source_discovered
    ON mentions(source_id, discovered_at DESC);
```

**Решения:**

- **`project_id` в `mentions` НЕ создаётся.** Обоснование: `content_hash UNIQUE` глобален (ADR-0004) — один и тот же текст, найденный двумя проектами, даёт одну запись. Поле `project_id` было бы многозначным или арбитрарно «первый-пришёл-выиграл», а это путает downstream. Связь `mention ↔ project` идёт через `signals` (proj-specific). Если в Phase 1+ понадобится аналитика «какие mentions пришли по каким project-scan'ам» — добавляется many-to-many таблица `mention_project` через additive миграцию. См. **F.1**.
- **`engagement` и `raw` — `JSONB`.** Обоснование: оба поля — heterogeneous dict-payload, специфичный к источнику; JSONB даёт нативную индексируемость (через GIN при необходимости в Phase 1+) и компактнее JSON.
- **`tracking_params_removed` — `TEXT[]`** (Postgres array of text). Обоснование: однородный список строк, без вложенности; array нативный, индексируем через GIN при необходимости. JSONB здесь overkill.
- **`url` — `TEXT`, не `VARCHAR(2048)`.** Postgres TEXT не имеет накладных расходов vs varchar — TEXT/VARCHAR в Postgres эквивалентны. Делаем единообразно.

**Индексы для E1:**
- `UNIQUE(content_hash)` — автоматический, главный для дедупа.
- `(source_id, discovered_at DESC)` — для административных запросов «всё что Reddit принёс за последний час» (полезно при отладке slice).

**Индексы для будущих этапов (НЕ создаются сейчас, заметка):**
- E2a: `text_clean tsvector` GIN-индекс для BM25 (через `pg_search` extension или нативный tsvector).
- E7: `created_at` btree для retention-jobs (быстрый delete by date range). Создаётся когда retention включается.

### A.2. `signals`

Финал pipeline. Поля `Signal` из `core/CLAUDE.md` A.6. FK на `mentions(id)` — **`ON DELETE RESTRICT`**.

```sql
CREATE TABLE signals (
    id              UUID         PRIMARY KEY,
    mention_id      UUID         NOT NULL REFERENCES mentions(id) ON DELETE RESTRICT,
    project_id      TEXT         NOT NULL,
    matched_query   TEXT         NOT NULL,
    relevance_score REAL         NOT NULL CHECK (relevance_score >= 0.0 AND relevance_score <= 1.0),
    is_spam         BOOLEAN      NOT NULL,
    intent          TEXT         NOT NULL CHECK (intent IN (
                        'complaint','question','recommendation',
                        'advertisement','news','discussion','other')),
    sentiment       TEXT         NOT NULL CHECK (sentiment IN ('positive','neutral','negative')),
    entities        JSONB        NOT NULL DEFAULT '[]'::jsonb,
    topics          JSONB        NOT NULL DEFAULT '[]'::jsonb,
    pipeline_trace  JSONB        NOT NULL,
    cost_usd        NUMERIC(12,6) NOT NULL DEFAULT 0,
    signal_created_at TIMESTAMPTZ NOT NULL,                 -- из Signal.created_at
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()      -- момент INSERT
);

CREATE INDEX idx_signals_project_signal_created
    ON signals(project_id, signal_created_at DESC);
CREATE INDEX idx_signals_project_intent
    ON signals(project_id, intent);
CREATE INDEX idx_signals_mention
    ON signals(mention_id);
```

**Решения:**

- **`ON DELETE RESTRICT` на `mention_id`.** Архитектор-агент в `core/CLAUDE.md` A.6 зафиксировал: при retention `text`/`text_clean` могут быть очищены, но запись `mentions` остаётся (до 180 дней). RESTRICT гарантирует, что нельзя удалить mention пока на него ссылаются signals — это форсирует cleanup в правильном порядке (сначала retention signals, потом mentions). CASCADE — слишком агрессивно (signal случайно потеряется при retention mention). SET NULL — ломает контракт `INotifier.send(signal, mention, ...)` (нужен mention для рендеринга).

- **`intent`/`sentiment` как TEXT с CHECK constraint, не PostgreSQL ENUM.** Обоснование: добавление варианта в Literal-тип — additive в `core/CLAUDE.md` E.2. Postgres ENUM требует `ALTER TYPE ... ADD VALUE` (нельзя в транзакции до Postgres 12) и не поддерживает удаление варианта. CHECK constraint можно `DROP CONSTRAINT` + `ADD CONSTRAINT` атомарно в одной транзакции. Это лучше для версионирования контрактов.

- **Два timestamp-поля: `signal_created_at` и `created_at`.** `Signal.created_at` (из core) — момент логического создания сигнала pipeline-ом. Наш собственный `created_at DEFAULT NOW()` — момент физического INSERT. Они могут расходиться при backfill / re-processing. Поле из core хранится под именем `signal_created_at` чтобы не конфликтовать с инвариантом «`created_at` есть на всех таблицах».

- **`entities`/`topics` — `JSONB` (массив строк).** Альтернатива: `TEXT[]`. Выбираем JSONB ради единообразия с `pipeline_trace` (тоже JSONB) и потому что в будущем (E2b/Phase 1+) entities могут стать структурированными `[{type:"person", value:"Egor"}, ...]` — миграция остаётся в рамках JSONB.

**Индексы для E1:**
- `(project_id, signal_created_at DESC)` — основной для read-feed (`search_signals`).
- `(project_id, intent)` — фильтр по intent.
- `(mention_id)` — для JOIN при notifier.send (получение mention по signal).

**Заметка для E2a:** дополнительный индекс на `relevance_score` или composite `(project_id, relevance_score DESC)` может быть полезен. Решение откладывается на момент, когда измерим узкое место.

### A.3. `scan_log`

По `IRepository.record_scan` и `last_scanned_at` из core B.6.

```sql
CREATE TABLE scan_log (
    scan_id      UUID         PRIMARY KEY,
    project_id   TEXT         NOT NULL,
    source_id    TEXT         NOT NULL,
    query_name   TEXT         NOT NULL,
    started_at   TIMESTAMPTZ  NOT NULL,
    finished_at  TIMESTAMPTZ  NOT NULL,
    count        INTEGER      NOT NULL,
    cost_usd     NUMERIC(12,6) NOT NULL,
    status       TEXT         NOT NULL CHECK (status IN ('ok','partial','failed')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_scan_log_last_scanned
    ON scan_log(project_id, source_id, query_name, finished_at DESC)
    WHERE status IN ('ok','partial');
```

**Решения:**

- **`status` как TEXT с CHECK.** То же обоснование что в signals (A.2): additive Literal-эволюция.
- **Partial index `WHERE status IN ('ok','partial')`.** Метод `last_scanned_at` ищет именно успешные/частичные сканы — failed не должен влиять на «когда последний раз отсканировано». Partial index уменьшает размер индекса и ускоряет hot-query.
- **`scan_id` как PRIMARY KEY (без отдельного `id`).** scan_id — UUID, генерируется dispatcher'ом до старта скана и используется в событиях `ScanRequested`/`ScanStarted`/`ScanFinished` как идентификатор. Логично использовать его как PK напрямую.

### A.4. `usage_log`

По `IRepository.append_usage`, `budget_used`, `budget_used_by_source` из core B.6. Партиционирование/aggregation в Phase 0 — НЕ делаем (todo-004 D), обычная таблица.

```sql
CREATE TABLE usage_log (
    id           UUID         PRIMARY KEY,
    project_id   TEXT         NOT NULL,
    source_id    TEXT         NOT NULL,
    cost_usd     NUMERIC(12,6) NOT NULL,
    occurred_at  TIMESTAMPTZ  NOT NULL,
    kind         TEXT         NOT NULL CHECK (kind IN ('source','embedding','llm','other')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_usage_log_project_occurred
    ON usage_log(project_id, occurred_at DESC);

CREATE INDEX idx_usage_log_project_source_occurred
    ON usage_log(project_id, source_id, occurred_at DESC);
```

**Решения:**

- **`source_id` обязателен даже для `kind='llm'` или `kind='embedding'`.** В этих случаях source_id указывает источник ментионов, по которым шла стадия (например, embedding-стоимость для batch ментионов из Reddit). Это даёт `budget_used_by_source` корректную атрибуцию полной стоимости. Если стадия охватывает mixed-source батч — записывается одна `usage_log`-строка per source с пропорциональной стоимостью (или одна сводная с `source_id='mixed'` — это решение **F.2** для open question).
- **Без партиций.** В Phase 0 даже при 100 батчах × 10 источников × 60 дней = 60k строк, что Postgres-у на завтрак. Партиционирование по месяцу — заметка для Phase 1+, если KPI-агрегаты начнут тормозить.

### A.5. Маппинг core-моделей → колонок (для executor-а)

Однозначный mapping для каждой core-модели, попадающей в E1-таблицы. Pydantic-поле → SQL-колонка.

**`RawMention` + `NormalizedMention` → `mentions`:**

| Pydantic-поле | SQL-колонка | Замечание |
|---|---|---|
| `id` (UUID) | `id` | PK |
| `source_id` | `source_id` | |
| `external_id` | `external_id` | |
| `author` | `author` | nullable |
| `author_id` | `author_id` | nullable |
| `text` | `text` | |
| `text_html` | `text_html` | nullable |
| `url` (HttpUrl) | `url` | str(url) при INSERT |
| `lang_hint` | `lang_hint` | nullable |
| `engagement` (dict) | `engagement` | JSONB |
| `raw` (dict) | `raw` | JSONB |
| `published_at` | `published_at` | tz-aware UTC |
| `discovered_at` | `discovered_at` | tz-aware UTC |
| `fetched_at` | `fetched_at` | tz-aware UTC |
| `text_clean` | `text_clean` | |
| `lang` | `lang` | |
| `content_hash` | `content_hash` | UNIQUE |
| `is_html_stripped` | `is_html_stripped` | |
| `normalize_version` | `normalize_version` | |
| `tracking_params_removed` (list[str]) | `tracking_params_removed` | TEXT[] |

**Поля core, НЕ маппящиеся в `mentions` в Phase 0 (откладываются на E2a):**
- `embedding: list[float] \| None` → отдельная таблица `mentions_embeddings(mention_id, vector vector(1024))` в E2a.
- `minhash_signature: list[int] \| None` → колонка добавится в Phase 1+ при реализации near-dedup. В Phase 0 поле в `NormalizedMention` остаётся, но не персистится. Executor должен это явно обработать — модель сериализуется, поле игнорируется при INSERT.

**`Signal` → `signals`:**

| Pydantic-поле | SQL-колонка | Замечание |
|---|---|---|
| `id` | `id` | PK |
| `mention_id` | `mention_id` | FK |
| `project_id` | `project_id` | |
| `matched_query` | `matched_query` | |
| `relevance_score` | `relevance_score` | REAL |
| `is_spam` | `is_spam` | |
| `intent` | `intent` | TEXT с CHECK |
| `sentiment` | `sentiment` | TEXT с CHECK |
| `entities` (list[str]) | `entities` | JSONB |
| `topics` (list[str]) | `topics` | JSONB |
| `pipeline_trace` (list[PipelineTraceEntry]) | `pipeline_trace` | JSONB; Pydantic.model_dump() → list of dicts |
| `cost_usd` | `cost_usd` | NUMERIC(12,6) |
| `created_at` | `signal_created_at` | переименовано (см. A.2) |

**`scan_log` поля** — приходят прямо из аргументов `record_scan`, не из core-модели.

**`usage_log` поля** — приходят прямо из аргументов `append_usage`.

---

## B. Стратегия миграций

**Решение: plain SQL files + минимальный Python-runner.** Не Alembic.

Обоснование:
- Alembic тащит SQLAlchemy в проект ради single-purpose. Мы используем asyncpg напрямую (см. C). Не оправдано.
- yoyo-migrations / dbmate — внешние зависимости, дополнительный CLI. Для Phase 0 это лишний moving part.
- Plain SQL — версионирование в git, читаемость, ноль абстракций между «что я хотел» и «что Postgres получил». Идеально для соло-проекта.

### B.1. Структура

```
storage/
├── migrate.py                           # Python-runner
├── schema.sql                           # snapshot (для документации; не исполняется runner-ом)
├── repositories.py
├── database.py
└── migrations/
    ├── 001_initial.sql                  # все 4 таблицы + индексы из раздела A
    └── README.md                        # формат именования, правила
```

### B.2. Формат имён файлов

`NNN_short_description.sql`, где `NNN` — трёхзначный номер с ведущими нулями (001, 002, 003). Описание — snake_case, до ~5 слов. Версия = NNN; одна миграция = один файл.

Примеры:
- `001_initial.sql` — первая, создаёт все 4 таблицы.
- `002_add_embeddings.sql` — E2a.
- `003_add_events_table.sql` — E4.

### B.3. Таблица отслеживания

В первой строке миграции 001 (или в bootstrap-блоке runner-а) создаётся системная таблица:

```sql
CREATE TABLE schema_migrations (
    version     INTEGER     PRIMARY KEY,
    filename    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum    TEXT        NOT NULL                 -- sha256 файла
);
```

`checksum` нужен чтобы детектить случайные правки уже-applied миграций — если файл `001_initial.sql` изменился после применения, runner кричит ошибкой при следующем запуске. Это защита от silent drift.

### B.4. Runner

`storage/migrate.py` — простой Python-скрипт:

```python
async def run_migrations(database: Database, migrations_dir: Path) -> None:
    """
    1. Открыть транзакцию.
    2. CREATE TABLE schema_migrations IF NOT EXISTS (только если 001 ещё не применена).
    3. Прочитать все *.sql файлы в migrations_dir, отсортировать по NNN.
    4. Прочитать applied versions из schema_migrations.
    5. Для каждой un-applied миграции:
         a. Прочитать файл, вычислить sha256.
         b. Открыть transaction.
         c. Выполнить SQL целиком через conn.execute(file_content).
         d. INSERT INTO schema_migrations (version, filename, checksum).
         e. Commit.
    6. Для каждой applied миграции — verify checksum совпадает с файлом.
       Mismatch = abort с человекочитаемой ошибкой.
    """
```

CLI: `python -m crawler.storage.migrate` (модуль с `if __name__ == "__main__"`).

### B.5. Идемпотентность и поведение при ошибке

- **Идемпотентность.** Повторный запуск — no-op (все версии уже в `schema_migrations`). Это нужно при auto-restart контейнера в E7.
- **Ошибка посередине.** Транзакция откатывается; запись в `schema_migrations` не появляется; runner падает с exit 1. Следующий запуск повторно попытается применить миграцию (которой нет в schema_migrations).
- **Multi-statement SQL в одной транзакции.** asyncpg `conn.execute(multi_statement_text)` поддерживает это. Если миграция требует команд вне транзакции (CREATE INDEX CONCURRENTLY, например — пригодится в Phase 1+ для production indexes без блокировки таблицы) — добавляется маркер в имени файла или комментарий в первой строке `-- NO_TRANSACTION`. В Phase 0 такого нет.
- **DOWN migrations НЕ поддерживаются** в Phase 0. Соло-проект, дешевле сделать compensating forward-миграцию чем поддерживать reversibility.

### B.6. `schema.sql` snapshot

Декларативный файл в корне `storage/` — текущее состояние всех таблиц как один документ. **НЕ исполняется runner-ом**, не используется для применения. Цели:
- Быстрый ответ на «как выглядит моя БД сейчас?» без чтения всех миграций.
- Diff-friendly для PR-ревью при breaking-изменениях.
- Источник для генерации ER-диаграмм в будущем.

Поддержание актуальности — manual (executor пишет в той же PR что и миграция). Если рассинхронизация обнаруживается — это сигнал, что cleanup в processes.

---

## C. Connection pool, Database и Repository

### C.1. Класс `Database`

Обёртка над `asyncpg.Pool` + lifecycle и type-codecs.

```python
# storage/database.py
import asyncpg
from contextlib import asynccontextmanager
from typing import AsyncIterator
import json

class Database:
    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 2,
        max_size: int = 10,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Создать pool, init type codecs, проверить расширения."""
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            init=self._init_connection,
        )
        await self._init_extensions()

    async def disconnect(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    async def _init_connection(conn: asyncpg.Connection) -> None:
        """Per-connection init: JSONB codec, остальные codecs (UUID/Decimal/TIMESTAMPTZ) — нативны в asyncpg."""
        await conn.set_type_codec(
            "jsonb",
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )

    async def _init_extensions(self) -> None:
        """CREATE EXTENSION IF NOT EXISTS. Фейлится если расширение недоступно."""
        async with self.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgvector")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgmq")
        # Нет: pg_search/BM25 (E2a). Без него старт НЕ фейлится.

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        assert self._pool is not None, "Database not connected"
        async with self._pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        assert self._pool is not None, "Database not connected"
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield conn
```

**Решения:**

- **DSN из env.** Конкретное имя env-var фиксируется в `config/CLAUDE.md` (отдельная сессия). В `Database.__init__` принимается DSN-строка явно — это упрощает тестирование с testcontainers.
- **Pool size: min=2, max=10 default.** Для соло-проекта Phase 0 — достаточно с запасом. Параметризуется через ctor для тестов и продакшена.
- **`init=_init_connection` per-connection.** JSONB codec ставится на каждый коннект в pool — это требование asyncpg (codec локальный к connection).
- **`init_extensions` на старте.** pgvector нужен для E2a, pgmq — для E4. Загружаем сейчас, чтобы Phase 0 база сразу была готова к расширению. Это лёгкие операции (CREATE EXTENSION IF NOT EXISTS — no-op если уже создано).
- **Нет `init` асинхронного контекст-менеджера на самом классе.** Вместо `async with Database(...)` явные `connect()`/`disconnect()` — это упрощает использование в FastAPI lifespan и в тестах. Если понадобится — добавляется `__aenter__`/`__aexit__` без breaking.

### C.2. Репозиторий-паттерн

**Решение: единый класс `Repository(IRepository)` в одном файле `storage/repositories.py`.**

Обоснование:
- `core/CLAUDE.md` B.6 фиксирует **один Protocol** `IRepository` для всех слоёв-клиентов. Реализация в одном классе — прямое отображение.
- В Phase 0 у `IRepository` ~17 методов. Один файл ~600–800 строк — читаемо, не громоздко.
- DI проще: один параметр `repo: IRepository` во всех слоях вместо 4–5 разных репозиториев.
- Если файл вырастет в Phase 1+ до 1500+ строк — рефакторинг на mixins (`MentionsMixin`, `SignalsMixin`, `ScanLogMixin`, `UsageLogMixin`, `NotificationsMixin`, ...), композирующий один `Repository` через множественное наследование. Это additive, не breaking — клиенты всё равно видят `IRepository`.

```python
# storage/repositories.py
from core.contracts import IRepository
from core.models import NormalizedMention, Signal, Project, ...
from core.events import ...
from storage.database import Database

class Repository(IRepository):
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- Mentions ---
    async def bulk_upsert_mentions_with_dedup(
        self, mentions: list[NormalizedMention]
    ) -> tuple[int, int]:
        ...

    async def existing_hashes(self, hashes: list[str]) -> set[str]:
        ...

    # ... все методы IRepository
```

### C.3. Pydantic ↔ asyncpg.Record mapping

**Решение: `Model.model_validate(dict(record))` для чтения; `model_dump()` или explicit fields для записи.**

```python
# Чтение
async def get_signal(self, signal_id: UUID) -> Signal | None:
    async with self._db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, mention_id, project_id, matched_query, "
            "relevance_score, is_spam, intent, sentiment, "
            "entities, topics, pipeline_trace, cost_usd, "
            "signal_created_at AS created_at "
            "FROM signals WHERE id = $1",
            signal_id,
        )
        if row is None:
            return None
        return Signal.model_validate(dict(row))

# Запись (bulk)
async def insert_signals(self, signals: list[Signal]) -> int:
    if not signals:
        return 0
    async with self._db.transaction() as conn:
        await conn.executemany(
            "INSERT INTO signals "
            "(id, mention_id, project_id, matched_query, relevance_score, "
            "is_spam, intent, sentiment, entities, topics, pipeline_trace, "
            "cost_usd, signal_created_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12,$13)",
            [
                (
                    s.id, s.mention_id, s.project_id, s.matched_query,
                    s.relevance_score, s.is_spam, s.intent, s.sentiment,
                    json.dumps(s.entities), json.dumps(s.topics),
                    json.dumps([t.model_dump(mode="json") for t in s.pipeline_trace]),
                    s.cost_usd, s.created_at,
                )
                for s in signals
            ],
        )
        return len(signals)
```

**Замечания:**

- **JSONB через `json.dumps`** при записи. asyncpg type codec для jsonb (см. C.1) принимает str/json-сериализуемый dict. `Decimal`, `datetime`, `UUID` сериализуются нативно asyncpg-ом, JSONB — через codec.
- **`model_dump(mode="json")`** для `PipelineTraceEntry` — обеспечивает сериализацию `Decimal`/`datetime`/`UUID` в JSON-совместимые типы.
- **`AS created_at`** в SELECT для `signals` — приводит SQL-имя `signal_created_at` к Python-полю `Signal.created_at` (см. A.2 о двух timestamp-полях). Это намеренное aliasing на read.
- **`HttpUrl`** при чтении url из mentions: `Pydantic` валидатор примет str и сконструирует HttpUrl автоматически.

### C.4. Транзакционность по методам E1-scope

Для каждого метода — нужна ли явная транзакция:

| Метод | Транзакция | Причина |
|---|---|---|
| `bulk_upsert_mentions_with_dedup` | implicit (один statement) | INSERT ... ON CONFLICT atomic |
| `existing_hashes` | нет | read-only single SELECT |
| `insert_signals` | explicit (executemany) | гарантия all-or-nothing для батча |
| `last_scanned_at` | нет | read-only |
| `record_scan` | implicit | один INSERT |
| `append_usage` | implicit | один INSERT |
| `budget_used` / `budget_used_by_source` | нет | read-only aggregate |
| `get_signal` | нет | read-only |
| `search_signals` (E1: заглушка) | нет | read-only |
| `search_hybrid` (E1: NotImplementedError) | — | реализуется в E2a |
| `notification_already_sent` / `record_notification` (E1: заглушка) | — | реализуется в E5 |
| `record_feedback` (E1: заглушка) | — | реализуется в E5 |
| `upsert_project` / `get_project` / `list_projects` (E1: заглушка) | — | реализуется в E2c |

Многошаговые транзакции в Phase 0 не нужны — каждый метод укладывается в один statement. В E2a при INSERT mention + INSERT в `mentions_embeddings` потребуется явная `transaction()` — здесь `Database.transaction()` контекст-менеджер пригодится.

### C.5. Заглушки методов IRepository не в E1-scope

Контракт `IRepository` определяет ~17 методов. В E1 / Ветка 1 реализуются полностью только E1-scope (см. таблицу C.4). Для остальных:

**Решение: каждый «не-E1» метод имеет тело `raise NotImplementedError("requires storage support from EX")`** с указанием будущего этапа.

Обоснование: Protocol требует наличия метода (mypy/pyright). Реализация-заглушка позволяет проходить статическую проверку и явно сигнализировать вызывающему коду «этот функционал ещё не готов». Альтернатива — TODO-implementation возвращающая `None`/`[]`/`False` — опасна: orchestration не упадёт, а молча будет вести себя неверно.

Список заглушек на E1:
- `search_hybrid` → реализация E2a.
- `search_signals` → можно сделать минимум-имплементацию в E1 (read-only, простая фильтрация по project_id и created_at) — это полезно для отладки slice. Решение: **минимальная имплементация в E1** (без BM25/semantic).
- `notification_already_sent`, `record_notification` → E5.
- `record_feedback` → E5.
- `upsert_project`, `get_project`, `list_projects` → E2c. **Заглушка в E1**, потому что в slice проект приходит из `config/bootstrap.py` (hardcoded `default_project()`, см. ROADMAP E1 Ветка 3) — БД его не хранит.
- `budget_used_by_source` → нужен в E4, можно реализовать сейчас (тривиально).

**Решение по `record_scan` и `last_scanned_at` в E1:** реализуются полностью. В slice без orchestration их вызовы делает `cli.py scan-once` — пишет start/finish времена и count, чтобы повторный запуск не сканировал заново. Это даёт минимальную idempotency на CLI-уровне.

---

## D. Что НЕ делать в этой ветке (явный список)

Из ROADMAP E1 Ветка 1, повтор для исполнителя:

| НЕ создавать | Где появится |
|---|---|
| Таблица `mentions_embeddings` (или колонка `vector(1024)` в `mentions`) | E2a |
| GIN-индекс на `mentions.text_clean` для BM25 / `pg_search` | E2a |
| Таблица `notification_log` | E5 |
| Таблица `feedback_log` | E5 |
| Таблица `events` для bus + триггер LISTEN/NOTIFY | E4 |
| pgmq queue tables (`pgmq.q_*`, `pgmq.a_*`) | E4 (создаются автоматически через `pgmq.create()` функции при первом enqueue) |
| Таблица `projects` (с YAML source-of-truth) | E2c |
| FK на `projects(id)` для `mentions.project_id` (поля нет, см. A.1), `signals.project_id`, `scan_log.project_id`, `usage_log.project_id` | E2c — ALTER TABLE ADD CONSTRAINT |
| Партиции для `usage_log` / `mentions` | Phase 1+ если KPI-агрегаты замедлятся |
| Row-Level Security policies | Phase 1+ если появится multi-tenant |
| Backup/restore автоматизация | E7 |

**Архитектурные заметки про future схему (для следующих сессий архитектора):**

- **`mentions_embeddings`** (E2a, ADR-0001 + ADR-0003) — **отдельная таблица**, не колонка в `mentions`. Структура:
  ```sql
  CREATE TABLE mentions_embeddings (
      mention_id UUID PRIMARY KEY REFERENCES mentions(id) ON DELETE CASCADE,
      embedding  vector(1024) NOT NULL,
      model_id   TEXT NOT NULL,             -- 'voyage-3.5'
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  CREATE INDEX ON mentions_embeddings USING hnsw (embedding vector_cosine_ops);
  ```
  CASCADE здесь — потому что embedding бесполезен без mention.

- **`events`** (E4) — таблица для bus с триггером `pg_notify`:
  ```sql
  CREATE TABLE events (
      id          UUID PRIMARY KEY,
      event_type  TEXT NOT NULL,
      project_id  TEXT,
      payload     JSONB NOT NULL,
      occurred_at TIMESTAMPTZ NOT NULL,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
  );
  -- + trigger: AFTER INSERT → pg_notify('domain_events', NEW.id::text)
  ```

- **`notification_log`** (E5):
  ```sql
  CREATE TABLE notification_log (
      id          UUID PRIMARY KEY,
      project_id  TEXT NOT NULL,
      signal_id   UUID NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
      channel     TEXT NOT NULL,
      target      TEXT NOT NULL,
      status      TEXT NOT NULL,
      sent_at     TIMESTAMPTZ NOT NULL,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (project_id, signal_id, channel, target)
  );
  ```

Эти заметки — для информации продукт-агента и следующих сессий архитектора. Executor E1 их **не реализует**.

---

## E. SQL-стратегии для методов IRepository (E1-scope)

Каждый блок — короткая стратегия + SQL для executor-а. Полные сигнатуры — в `core/CLAUDE.md` B.6.

### E.1. `bulk_upsert_mentions_with_dedup(mentions) -> (inserted, skipped)`

```sql
INSERT INTO mentions (
    id, content_hash, source_id, external_id,
    author, author_id, text, text_html, url, lang_hint,
    engagement, raw, published_at, discovered_at, fetched_at,
    text_clean, lang, is_html_stripped, normalize_version, tracking_params_removed
)
VALUES (...) -- batch
ON CONFLICT (content_hash) DO NOTHING
RETURNING id;
```

`inserted = len(returning_rows)`; `skipped = len(input) - inserted`. Атомарность — гарантируется одним statement в Postgres. Использовать `executemany` или `copy_records_to_table` — на усмотрение executor (для батча 100–1000 ментионов разница невелика; рекомендуем `executemany` для простоты).

**Замечание про executemany + RETURNING:** asyncpg `executemany` в текущей версии **не возвращает RETURNING** (это ограничение драйвера). Альтернатива:
1. **`UNNEST` подход:** один statement с массивами для каждой колонки:
   ```sql
   INSERT INTO mentions (...)
   SELECT * FROM UNNEST($1::uuid[], $2::char(64)[], ...)
   ON CONFLICT (content_hash) DO NOTHING
   RETURNING id;
   ```
   Это работает с `conn.fetch(...)` и возвращает RETURNING. **Рекомендуется этот подход.**
2. Либо предварительно: `existing_hashes` → отфильтровать → `executemany` без RETURNING → `inserted = len(filtered)`. Имеет race condition между двумя запросами; не рекомендуется.

### E.2. `existing_hashes(hashes) -> set[str]`

```sql
SELECT content_hash FROM mentions WHERE content_hash = ANY($1::char(64)[]);
```

Возврат `set(row['content_hash'] for row in rows)`. Используется в DedupStage для pre-filter перед `bulk_upsert_*` (хотя ON CONFLICT уже даёт дедуп — `existing_hashes` нужен раньше, чтобы не запускать дальнейшие стадии pipeline на дубликатах).

### E.3. `insert_signals(signals) -> int`

```sql
INSERT INTO signals (...)
VALUES (...);
```

Bulk через `executemany`. Возврат `len(signals)`. Если нужен RETURNING (не нужен в Phase 0) — UNNEST-подход как в E.1.

### E.4. `last_scanned_at(project_id, source_id, query_name) -> datetime | None`

```sql
SELECT MAX(finished_at)
FROM scan_log
WHERE project_id = $1
  AND source_id = $2
  AND query_name = $3
  AND status IN ('ok','partial');
```

Использует partial index `idx_scan_log_last_scanned` (см. A.3). Возврат `row[0]` (может быть NULL).

### E.5. `record_scan(scan_id, ..., status)`

```sql
INSERT INTO scan_log
    (scan_id, project_id, source_id, query_name,
     started_at, finished_at, count, cost_usd, status)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9);
```

Один INSERT, atomic.

### E.6. `append_usage(project_id, source_id, cost_usd, occurred_at, kind)`

```sql
INSERT INTO usage_log (id, project_id, source_id, cost_usd, occurred_at, kind)
VALUES (gen_random_uuid(), $1, $2, $3, $4, $5);
```

Либо `id` генерируется в Python и передаётся $1. На усмотрение executor — оба варианта корректны. Рекомендация: **в Python**, чтобы id был доступен немедленно для логирования.

### E.7. `budget_used(project_id, since, until=None) -> Decimal`

```sql
SELECT COALESCE(SUM(cost_usd), 0)
FROM usage_log
WHERE project_id = $1
  AND occurred_at >= $2
  AND ($3::timestamptz IS NULL OR occurred_at <= $3);
```

`COALESCE(SUM, 0)` — когда нет строк, SUM возвращает NULL; нам нужен Decimal(0). Используется индекс `idx_usage_log_project_occurred`.

### E.8. `budget_used_by_source(project_id, source_id, since) -> Decimal`

```sql
SELECT COALESCE(SUM(cost_usd), 0)
FROM usage_log
WHERE project_id = $1
  AND source_id = $2
  AND occurred_at >= $3;
```

Используется индекс `idx_usage_log_project_source_occurred`.

### E.9. `get_signal(signal_id) -> Signal | None` (минимум для E1)

```sql
SELECT id, mention_id, project_id, matched_query,
       relevance_score, is_spam, intent, sentiment,
       entities, topics, pipeline_trace, cost_usd,
       signal_created_at AS created_at
FROM signals
WHERE id = $1;
```

`Signal.model_validate(dict(row))`.

### E.10. `search_signals(project_id, since, until, intent, min_score, limit) -> list[Signal]` (минимум для E1)

В E1 — простая фильтрация без BM25/semantic, для отладки и future API.

```sql
SELECT id, mention_id, project_id, matched_query,
       relevance_score, is_spam, intent, sentiment,
       entities, topics, pipeline_trace, cost_usd,
       signal_created_at AS created_at
FROM signals
WHERE project_id = $1
  AND ($2::timestamptz IS NULL OR signal_created_at >= $2)
  AND ($3::timestamptz IS NULL OR signal_created_at <= $3)
  AND ($4::text IS NULL OR intent = $4)
  AND ($5::real IS NULL OR relevance_score >= $5)
ORDER BY signal_created_at DESC
LIMIT $6;
```

Использует индекс `idx_signals_project_signal_created`. Полнотекст и hybrid — в E2a.

---

## F. Открытые вопросы продукт-агенту

Эти решения архитектор принял в рамках сессии — продукт-агент может пересмотреть, если это не противоречит закрытым ADR.

### F.1. `project_id` в `mentions` — НЕТ.

Архитектор удалил поле из `mentions`. Обоснование в **A.1**: `content_hash` глобальный (ADR-0004), `mention ↔ project` связь — через `signals`. Альтернатива — many-to-many таблица `mention_project(mention_id, project_id, first_observed_at)` для аналитики «какие mentions trigger'или signals у проекта X». Это additive миграция, безопасная для Phase 1+ (когда появится конкретная аналитическая потребность).

**Если продукт-агент решит, что project_id нужен в `mentions` уже сейчас** (например, для retention per-project) — пересматривается до старта executor-сессии.

### F.2. `usage_log.source_id` для mixed-source батчей.

Архитектор заложил: `source_id` обязателен в каждой записи. Для случаев, когда стадия (embedding/llm) обрабатывает mixed-source батч ментионов — два варианта учёта стоимости:

1. **Per-source allocation:** одна `usage_log`-запись per source с пропорциональной стоимостью (по числу ментионов от source в батче). Точнее, но усложняет логику в processing-стадиях.
2. **Sweepable bucket:** одна запись с `source_id='mixed'` + JSONB-поле с разбивкой (требует ALTER TABLE сейчас или Phase 1+).

**Рекомендация архитектора:** вариант 1 (per-source) — он keeps schema простой, а логика «раздели стоимость батча по источникам» уже в processing-слое (он знает size батча и source каждого mention). Решение нужно до старта E2a; в E1 этот вопрос не возникает (только source-стадия, тривиально 1:1).

### F.3. `signals.cost_usd` vs `usage_log` дубль.

`Signal.cost_usd` фиксирует **полную стоимость pipeline для данного ментиона**. `usage_log` фиксирует **стоимость per source/embedding/llm-вызов**. Это две проекции одних и тех же расходов — `usage_log` агрегируем по проекту/времени, `signals.cost_usd` — атрибутим конкретный сигнал к стоимости. Это сознательная избыточность, она нужна для KPI «cost per actionable signal» (должен быть < $0.50). Без дубля считать cost-per-signal сложно.

**Если продукт-агент решит, что дубль не нужен** (cost-per-signal вычисляется через JOIN signals × usage_log) — поле `signals.cost_usd` удаляется из core/CLAUDE.md A.6 и схемы. Это breaking для core (см. core E.1), требует ADR-сессии.

### F.4. `core/CLAUDE.md` без `OPS:SEED:BEGIN/END` HTML-маркеров.

В прошлой сессии (todo-003) Seed Protocol в `core/CLAUDE.md` был вставлен без маркеров `<!-- OPS:SEED:BEGIN -->` и `<!-- OPS:SEED:END -->`. Это ломает `_tools/seed_replace.py` для этого файла. Архитектор обнаружил при работе над текущим todo (todo-004 явно потребовал маркеры).

**Действие:** требуется отдельная правка `core/CLAUDE.md` — добавить маркеры вокруг секции Seed Protocol. Архитектор-агент НЕ делает это в текущей сессии (вне scope todo-004). Продукт-агент инициирует micro-task для этого, или совмещает с другой сессией обновления core.

В **этом** документе (`storage/CLAUDE.md`) маркеры `<!-- OPS:SEED:BEGIN -->`/`<!-- OPS:SEED:END -->` будут вставлены — см. секцию Seed Protocol ниже.

### F.5. Decimal precision NUMERIC(12,6).

12 цифр всего, 6 после точки → max value $999,999.999999. Для Phase 0 бюджет $50/мес × 12 месяцев × 10 проектов = $6000 max-ish. С запасом 100×. Если в Phase 1+ появятся ситуации с биллион-токен-батчами и стоимостями выше $1000 за один вызов — пересматривается на NUMERIC(14,6). Дешёвая ALTER TABLE, не блокер.

---

## OPS

- **Type**: folder
- **Parent**: `../../CLAUDE.md`
- **Root**: `../../../../../`
- **Мета-файлы**: [.timeline](.timeline), [.links](.links)

<!-- OPS:SEED:BEGIN -->

### Seed Protocol

**OPS (Operational Project System)** — файловая система управления проектами через AI-агентов.
Все данные — Markdown-файлы с метаданными в Git-репозитории. Управление — AI-агентами и человеком.

Каждый `CLAUDE.md` — узел в цепочке контекста. Поле **Parent** указывает на родительский `CLAUDE.md`.
Поле **Root** указывает на корень OPS, где находятся `SYSTEM.md`, `_templates/` и `_tools/`.
CLAUDE.md без Parent — это корень OPS.

**Если у тебя нет контекста кроме этого файла** — иди по Parent вверх до корня,
там полные правила (`SYSTEM.md`) и шаблоны (`_templates/`).
Если Parent недоступен (файл не существует) — ты вне дерева OPS.
Работай локально по правилам seed. Не создавай OPS-блоки сущностей без доступа к корню.
Сообщи пользователю о сломанной цепочке.

**Приоритет**: инструкции Seed Protocol — базовые правила. Действуют всегда,
если пользователь явно не указал их игнорировать.

#### При старте сессии

1. Прочитай этот CLAUDE.md — пойми контекст директории и OPS-блок (если есть).
2. Найди `todo-*.md` со `status: open` в текущей и родительских директориях — напомни пользователю.
3. Если задача связана с проектом — следуй по Parent до CLAUDE.md проекта (тип `project`).

#### Структура CLAUDE.md

Зоны файла и правила их модификации:

- **Выше `## OPS`** — авторский контент. Не модифицируй без явного запроса пользователя.
- **Внутри `## OPS`, выше `OPS:SEED:BEGIN`** — системные данные (поля + OPS-блок). Обновляй по правилам.
- **Между `OPS:SEED:BEGIN` и `OPS:SEED:END`** — неприкосновенный протокол. Не трогай никогда.

Шаблон нового CLAUDE.md:

    # {Имя директории}

    {Описание: 1-3 предложения, что здесь находится.}

    ## OPS

    - **Type**: {root | area | project | repo | folder}
    - **Parent**: `{относительный путь к CLAUDE.md родителя}`
    - **Root**: `{относительный путь к корню OPS}`
    - **Мета-файлы**: [.timeline](.timeline), [.links](.links)

    ### OPS:{Тип блока}        ← только если area, project или repo
    {поля по спецификации из ops-blocks.md}

    {Seed Protocol целиком — от OPS:SEED:BEGIN до OPS:SEED:END}

#### OPS-блоки сущностей

OPS-блоки — подсекции `### OPS:{Тип}` внутри `## OPS`. Определяют роль директории в системе.
Один CLAUDE.md содержит максимум один OPS-блок сущности.

| Блок | Когда использовать |
|------|-------------------|
| `### OPS:Area` | Корень направления деятельности (`areas/{name}/`) |
| `### OPS:Project` | Корень проекта (содержит цель, статус, KPI) |
| `### OPS:Repo` | Корень репозитория или git submodule |

Полная спецификация полей, обязательные/опциональные поля и примеры:
`{Root}/_templates/ops-blocks.md` (подставь Root из поля выше).

Если файл недоступен — заполни минимум: **id**, **status**, **description** (для Area)
или **goal** (для Project).

#### Мета-файлы

В каждой индексированной директории — три файла:

| Файл | Назначение |
|------|-----------|
| `CLAUDE.md` | Контекст + `## OPS` с Seed Protocol |
| `.timeline` | Временные координаты файлов (YAML) |
| `.links` | Связи с файлами за пределами этой директории (YAML) |

Формат `.timeline` (все поля опциональны):

    filename.ext:
      start: YYYY-MM-DD
      due: YYYY-MM-DD
      after: other.ext     # зависимость (строка или список)
      estimate: 2w         # число + d/w/m

Формат `.links`:

    filename.ext:
      - ../relative/path/to/target.md

#### Создание мета-файлов в новой директории

**Когда**: директория содержит ≥3 значимых файла, или агент создаёт ≥3 таких файла в ходе работы.

«Значимый файл» — это: исходный код, `.md` файлы с контентом, конфигурации проекта.
Не считаются: автогенерированные файлы, бинарные ресурсы, файлы зависимостей, `*.meta`.

Шаги:

1. Создай `CLAUDE.md` по шаблону выше. Скопируй seed целиком (от `OPS:SEED:BEGIN` до `OPS:SEED:END`).
2. Заполни **Type**, **Parent** (путь к ближайшему родительскому CLAUDE.md), **Root** (путь к корню OPS).
3. Создай `.timeline` и `.links` с комментарием-шаблоном (можно пустые).
4. Если директория — area/project/repo — добавь OPS-блок (запроси `_templates/ops-blocks.md`).

**Если CLAUDE.md уже существует, но без секции `## OPS`** — добавь её в конец файла.
Авторский контент выше — не трогай.

**Не создавай мета-файлы в**: Library, Temp, .git, node_modules, obj, Logs, Builds, bin, target

#### Правила обновления

- При изменении файлов — обнови `.timeline` если есть временные данные.
- При создании связей за пределы директории — добавь в `.links`.
- Не удаляй строки из `.timeline`/`.links` — помечай устаревшие комментарием.
- Авторский контент (выше `## OPS`) — не модифицируй без запроса пользователя.
- Если Parent ведёт на несуществующий файл — сообщи пользователю, не пытайся исправить автоматически.

#### Чего НЕ хранить в CLAUDE.md

CLAUDE.md — контекст, не журнал. Не добавляй:

- Логи действий, секции «Лог», «Состояние», «История изменений»
- Карты файлов и индексы (это задача визуализатора и `_tools/`)
- Временные заметки (используй todo-файлы)
- Дублирование информации из родительских CLAUDE.md

#### Todo-файлы

Имя файла: `todo-NNN.md`, где NNN — трёхзначный номер с нулями (001, 002, 003...).
Номер = следующий после максимального существующего в этой директории.

Формат:

    ---
    id: todo-NNN
    status: open            # open | done
    created: YYYY-MM-DD
    completed: null         # YYYY-MM-DD при status: done
    session: null           # заполняется OPS-инструментами; агент ставит null
    launch_dir: ""          # заполняется OPS-инструментами; агент ставит ""
    ---

    Текст задачи. Может быть многострочным.
    Каждая строка отображается в интерфейсе OPS Explorer.

Поля `session` и `launch_dir` заполняются автоматически визуализатором OPS Explorer
при привязке Claude-сессии к todo. Агент при создании todo всегда ставит `null` и `""`.

Правила:

- При старте сессии — найди `todo-*.md` со `status: open` в текущей и родительских директориях,
  напомни пользователю о незакрытых.
- При завершении todo — установи `status: done`, `completed: YYYY-MM-DD`.
- Не создавай todo без запроса пользователя или явной необходимости в ходе работы.

<!-- OPS:SEED:END -->
