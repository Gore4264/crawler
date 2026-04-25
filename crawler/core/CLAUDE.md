# Core — Domain Contracts

Слой контрактов системы `crawler`. Здесь живут только модели данных (Pydantic v2), Protocol-интерфейсы (PEP 544), доменные события и формат `content_hash`. Никакой бизнес-логики, никаких внешних сетевых вызовов, никакой работы с Postgres. Все остальные слои импортируют отсюда — отсюда никто не импортирует.

**Этот документ — техническое задание для агента-исполнителя E1.** После него три файла (`core/models.py`, `core/contracts.py`, `core/events.py`) пишутся «по сигнатурам» без архитектурных вопросов.

## Дисциплина импортов

`core/` импортирует только: `stdlib`, `pydantic`, `typing_extensions`. Никаких ссылок на `storage/`, `processing/`, `plugins/*`, `api/`, `bus/`, `orchestration/`. Если в core понадобилось что-то из соседнего слоя — это сигнал, что архитектура нарушена; решается переносом нужного типа в core или (чаще) переосмыслением идеи.

## Mapping разделов на файлы

| Раздел документа | Файл (создаётся в E1) |
|---|---|
| A. Модели | `core/models.py` |
| B. Контракты (Protocol) | `core/contracts.py` |
| C. Доменные события | `core/events.py` |
| D. Формат `content_hash` | реализация — `processing/stages/normalize.py`; контракт здесь |
| E. Версионирование | политика, без файла |
| F. ADR-trail | политика, без файла |

## ADR-trail

Этот документ — материализация четырёх ADR из `repo-crawler/ADR/`. Все архитектурные точки невозврата уже зафиксированы там, здесь — только их следствия в виде сигнатур и правил.

- **ADR-0001** (`ADR/0001-embedding-dimension-and-provider.md`) — `IEmbedder.dimensions = 1024`, `model_id = "voyage-3.5"`. См. раздел **B.5**.
- **ADR-0002** (`ADR/0002-third-source-bluesky-telegram-deferred.md`) — Phase 0 поддерживает streaming-источники. См. `SourceMode` (раздел **A.1**) и `IStreamingSource` (раздел **B.4**).
- **ADR-0003** (`ADR/0003-single-postgres-instance.md`) — `IQueue` и `IEventBus` — два разных Protocol; обе реализации в Phase 0 живут на одном Postgres (pgmq + LISTEN/NOTIFY). См. разделы **B.7** и **B.8**.
- **ADR-0004** (`ADR/0004-content-hash-text-only.md`) — `content_hash = sha256(normalized_text)`, источник в хеш не входит. Точные правила нормализации — раздел **D**.

## Инварианты

1. **Контракты неприкосновенны после первой недели разработки.** Любое breaking-изменение (см. раздел E) требует ADR-сессии с владельцем и migration plan для всех зависимых модулей. Архитектор-агент не имеет полномочий менять breaking-контракты в одиночку.
2. **`IEmbedder.dimensions` совпадает с `vector(N)` в SQL-схеме.** Этот инвариант проверяется в миграциях `storage/` (assert при старте). По ADR-0001 значение = 1024.
3. **Все `datetime` в моделях — tz-aware, UTC.** Любое поле `datetime` в `core/` валидируется на наличие `tzinfo` и приводится к UTC. Naive datetime — ошибка валидации.
4. **`Decimal` для денег.** Все стоимостные поля (`cost_usd`, `monthly_usd`, `cost_per_1m_tokens`) — `decimal.Decimal`, не `float`. Это исключает накопление ошибок при суммировании в `usage_log`.
5. **Pipeline-каскад нерушим (см. project CLAUDE.md).** `processing/pipeline.py` обязан проверять, что `LLMClassifyStage` не запускается до `[Normalize, Dedup, KeywordFilter, SemanticFilter]`. Сама проверка — в processing-слое; здесь зафиксировано как продуктовый инвариант, чтобы агент-исполнитель `LLMClassifyStage` знал.

---

## A. Pydantic v2 модели

Все модели — `pydantic.BaseModel` с `model_config = ConfigDict(frozen=True, extra="forbid")` если не указано иное. Frozen — потому что доменные value-objects не должны мутировать после создания; extra=forbid — чтобы случайные поля в YAML/JSON не проходили валидацию молча.

### A.1. Type aliases

```python
from typing import Literal

SourceMode = Literal["search", "stream"]
Intent = Literal[
    "complaint", "question", "recommendation",
    "advertisement", "news", "discussion", "other",
]
Sentiment = Literal["positive", "neutral", "negative"]
NotificationChannel = Literal["telegram", "webhook", "email"]
CostModel = Literal["free", "per_request", "per_result", "subscription"]
ScanStatus = Literal["ok", "partial", "failed"]
NotificationStatus = Literal["ok", "failed", "skipped"]
FeedbackKind = Literal["relevant", "noise", "block_author"]
UsageKind = Literal["source", "embedding", "llm", "other"]
BudgetScope = Literal["monthly", "daily", "per_source"]
```

Расширение Literal-варианта — additive (см. раздел E). Удаление варианта — breaking.

### A.2. SourceQuery

Единая модель запроса к источнику. Решение «mode-в-одной-модели vs StreamingSourceQuery»: один класс с `mode`. Обоснование: `dispatcher` единообразно вызывает `async for mention in source.search(q)`; разделение классов дублировало бы 80% полей.

```python
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field, model_validator

class SourceQuery(BaseModel):
    mode: SourceMode = "search"
    keywords: list[str] = Field(default_factory=list)              # OR-семантика внутри списка
    semantic_query: str | None = None
    excluded_keywords: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)             # ISO 639-1; пусто = все
    geo: str | None = None                                          # ISO 3166-1 alpha-2 или city name
    since: datetime | None = None
    until: datetime | None = None
    since_cursor: str | None = None                                 # source-specific непрозрачный resume-токен для streaming
    limit: int = 100                                                # для mode=search; в mode=stream игнорируется
    max_cost_usd: Decimal = Decimal("1.00")

    @model_validator(mode="after")
    def _validate_window(self) -> "SourceQuery":
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be greater than since")
        if self.mode == "stream" and self.until is not None:
            raise ValueError("stream mode is open-ended; until is not allowed")
        return self
```

Все datetime — tz-aware UTC (валидируется отдельным `field_validator`, опускается для краткости).

### A.3. RawMention

То, что источник возвращает «как есть» до нормализации.

```python
from pydantic import HttpUrl

class RawMention(BaseModel):
    source_id: str                                                  # имя плагина-источника, e.g. "reddit"
    external_id: str                                                # ID на платформе (post.id и т.п.)
    author: str | None = None                                       # display name
    author_id: str | None = None                                    # platform-specific ID
    text: str                                                       # min_length=1 после strip
    text_html: str | None = None                                    # исходный HTML (опц.)
    url: HttpUrl
    lang_hint: str | None = None                                    # язык по версии источника; не доверяем, но пробрасываем
    engagement: dict[str, int] = Field(default_factory=dict)        # likes/reposts/replies/views
    raw: dict = Field(default_factory=dict)                         # полный JSON источника для отладки
    published_at: datetime                                          # tz-aware UTC
    discovered_at: datetime                                         # tz-aware UTC
    fetched_at: datetime                                             # tz-aware UTC; когда мы получили объект
```

`source_id` совпадает с `ISource.id` плагина, который произвёл ментион — это инвариант, обеспечиваемый при `yield` в `search()`.

### A.4. NormalizedMention

Результат стадии Normalize. Наследует RawMention, добавляет вычисленные поля.

```python
from uuid import UUID, uuid4

class NormalizedMention(RawMention):
    id: UUID = Field(default_factory=uuid4)
    text_clean: str
    lang: str                                                        # ISO 639-1, обязательное (langdetect)
    content_hash: str                                                # 64-char hex (sha256), см. раздел D
    is_html_stripped: bool                                           # True если text_html был не пуст и стрипался
    normalize_version: int = 1                                       # см. раздел D, инкрементируется при смене алгоритма
    tracking_params_removed: list[str] = Field(default_factory=list) # для аудита; см. D.2
    minhash_signature: list[int] | None = None                       # зарезервировано для near-dedup; в Phase 0 не заполняется
    embedding: list[float] | None = None                             # заполняется на EmbeddingStage; len == 1024 (ADR-0001)

    @model_validator(mode="after")
    def _validate_hash_and_embedding(self) -> "NormalizedMention":
        if len(self.content_hash) != 64:
            raise ValueError("content_hash must be 64-char hex")
        int(self.content_hash, 16)  # raises if not hex
        if self.embedding is not None and len(self.embedding) != 1024:
            raise ValueError("embedding must be 1024-dim (ADR-0001)")
        return self
```

`text_clean` инвариант: применён алгоритм нормализации раздела **D** до шага 5 включительно (без хеширования). По нему считается `content_hash`. Это даёт детерминизм: одинаковый `text_clean` → одинаковый `content_hash`.

### A.5. PipelineTraceEntry

Запись о прохождении стадии. Используется внутри `Signal.pipeline_trace` для отладки и метрик.

```python
class PipelineTraceEntry(BaseModel):
    stage_name: str
    started_at: datetime                                             # tz-aware UTC
    duration_ms: int
    items_in: int
    items_out: int
    cost_usd: Decimal = Decimal("0")
    meta: dict = Field(default_factory=dict)                         # stage-specific (model_id, batch_size и т.п.)
```

### A.6. Signal

Финальный обогащённый сигнал — то, что попадает в нотификации и витрины.

**Решение:** `mention_id: UUID` как FK, а не `mention: NormalizedMention` вложенно. Это **отступление от ARCHITECTURE 1.1** — там показана inline-композиция. Аргументы за FK: (1) Signal живёт в отдельной таблице, ON CONFLICT и индексы работают по ID; (2) DomainEvent с Signal не дублирует mention-payload; (3) при retention `text`/`text_clean` могут быть удалены из mentions, а Signal остаётся со ссылкой и метаданными. Notifier получает Signal и Mention отдельными аргументами в `INotifier.send` — это явный контракт. Если продукт-агент решит, что shipping events требуют self-contained payload — пересматривается с migration plan.

```python
class Signal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    mention_id: UUID
    project_id: str
    matched_query: str                                               # имя темы из Project.queries (TopicQuery.name)
    relevance_score: float = Field(ge=0.0, le=1.0)
    is_spam: bool
    intent: Intent
    sentiment: Sentiment
    entities: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    pipeline_trace: list[PipelineTraceEntry]                         # обязательное; min_length=1
    cost_usd: Decimal = Decimal("0")
    created_at: datetime                                             # tz-aware UTC
```

### A.7. TopicQuery

Описание одной темы интереса внутри проекта.

```python
import re

class TopicQuery(BaseModel):
    name: str                                                        # slug, уникальный в Project; ^[a-z0-9_]+$
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    semantic: str | None = None                                      # текст для embedding; используется для topic_embedding
    languages: list[str] = Field(default_factory=list)
    geo: str | None = None
    sources: list[str] = Field(default_factory=list)                 # подмножество Project.sources; пусто = все
    schedule: str | None = None                                      # cron; null = берётся Project.schedule_default
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)    # null = Project.threshold
    topic_embedding: list[float] | None = None                       # 1024-dim; считается при deploy/load_from_yaml

    @model_validator(mode="after")
    def _validate(self) -> "TopicQuery":
        if not re.fullmatch(r"[a-z0-9_]+", self.name):
            raise ValueError("TopicQuery.name must be a slug ([a-z0-9_]+)")
        if self.topic_embedding is not None and len(self.topic_embedding) != 1024:
            raise ValueError("topic_embedding must be 1024-dim (ADR-0001)")
        return self
```

`topic_embedding` считается один раз при загрузке проекта (не на каждый scan) — это явная оптимизация cost, упомянутая в ROADMAP E2a.

### A.8. BudgetConfig

```python
class BudgetConfig(BaseModel):
    monthly_usd: Decimal
    daily_usd: Decimal | None = None                                 # опц. soft-limit
    per_source_usd: dict[str, Decimal] = Field(default_factory=dict) # ключ = source_id
    warning_threshold: float = 0.8                                   # доля от monthly/daily/per_source
    cutoff_threshold: float = 0.95

    @model_validator(mode="after")
    def _validate_thresholds(self) -> "BudgetConfig":
        if not (0.0 < self.warning_threshold < self.cutoff_threshold <= 1.0):
            raise ValueError("must satisfy 0 < warning < cutoff <= 1")
        if self.monthly_usd <= 0:
            raise ValueError("monthly_usd must be positive")
        return self
```

### A.9. NotificationConfig

```python
class NotificationConfig(BaseModel):
    channel: NotificationChannel
    target: str                                                      # chat_id / URL / email; формат — проверка в config/
    filter_expr: str | None = None                                   # mini-DSL; парсер в notifications/, не в core
    dedup_window_seconds: int | None = None                          # null = бессрочная дедупликация через notification_log
```

`filter_expr` — строка вида `relevance_score >= 0.75 AND intent != 'advertisement'`. Точная грамматика и парсер — в `notifications/CLAUDE.md` (отдельная сессия архитектора). В core — только тип. См. «Открытые вопросы продукт-агенту».

### A.10. Project

```python
class Project(BaseModel):
    id: str                                                          # slug, primary key; ^[a-z0-9_-]+$
    name: str
    queries: list[TopicQuery]
    sources: list[str]                                               # имена источников (плагинов); объединение с TopicQuery.sources
    notifications: list[NotificationConfig]
    budget: BudgetConfig
    pipeline: list[str | dict]                                       # формат как в ARCHITECTURE 5.3; типизированная валидация — в config/
    schedule_default: str                                            # cron-выражение
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)            # default для тем без override
    settings: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "Project":
        if not re.fullmatch(r"[a-z0-9_-]+", self.id):
            raise ValueError("Project.id must be a slug")
        names = [q.name for q in self.queries]
        if len(names) != len(set(names)):
            raise ValueError("TopicQuery.name must be unique within Project")
        return self
```

`pipeline: list[str | dict]` — это сырая форма из YAML (e.g. `["normalize", "dedup", {"embedding": {"model": "voyage-3.5"}}]`). Полную типизацию делает `config/`-слой; в core достаточно списка, чтобы `Project` сериализовывался обратно в YAML без потерь.

---

## B. Protocol-контракты

Все Protocol — `runtime_checkable=False` (только статическая проверка). Базовые имплементации (`BaseSource`, `BaseNotifier` и т.п.) живут в соответствующих plugin-слоях, не в `core/`.

### B.1. SourceCapabilities

Pydantic-модель (data, не Protocol).

```python
from typing import ClassVar

class SourceCapabilities(BaseModel):
    supports_keywords: bool = True
    supports_semantic: bool = False
    supports_geo: bool = False
    supports_language_filter: bool = False
    supports_search: bool = True
    supports_streaming: bool = False                                 # ADR-0002: если True — реализация наследует IStreamingSource
    supports_historical: bool = True
    cost_model: CostModel = "free"
    typical_latency_ms: int = 1000
```

Замечание: `supports_realtime_stream` из ARCHITECTURE 1.2 переименован в `supports_streaming` для консистентности с `SourceMode`.

### B.2. CostEstimate

```python
class CostEstimate(BaseModel):
    expected_results: int
    expected_cost_usd: Decimal
    confidence: Literal["exact", "estimate", "unknown"]
```

### B.3. ISource

Базовый контракт — REST-pull (Reddit, RSSHub, Brave, Google Alerts, Visualping и т.п.).

```python
from typing import Protocol, AsyncIterator

class ISource(Protocol):
    id: str
    capabilities: SourceCapabilities

    async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]:
        ...

    async def health_check(self) -> bool:
        ...

    def estimate_cost(self, q: SourceQuery) -> CostEstimate:
        ...
```

`search()` — для REST-pull ведёт себя как «выполни запрос, отдай батч, заверши». Для streaming-source (см. B.4) — «прочитай всё что накопилось в буфере с `since_cursor` и заверши» (не блокирует бесконечно — итерация заканчивается, когда буфер пуст).

### B.4. IStreamingSource

Расширение для long-lived streaming. По ADR-0002 — Phase 0 имеет минимум один такой источник (Bluesky firehose).

```python
class IStreamingSource(ISource, Protocol):
    async def start(self) -> None:
        """Установить соединение, начать буферизацию входящих сообщений."""
        ...

    async def stop(self) -> None:
        """Закрыть соединение, дренировать буфер."""
        ...

    async def __aenter__(self) -> "IStreamingSource":
        ...

    async def __aexit__(self, exc_type, exc, tb) -> None:
        ...
```

**Решение «один ISource с mode vs IStreamingSource(ISource)»: иерархия с подчинением.** Обоснование:
- Dispatcher в hot-path вызывает `source.search(q)` одинаково для обоих типов — единый код пути pull.
- Lifecycle (`start`/`stop`/context manager) нужен только streaming-источникам — они держат WebSocket. У REST-источников `start/stop` — пустой код, что было бы технологическим долгом, если бы все наследовали один Protocol.
- Различение в коде — через `capabilities.supports_streaming` (data-флаг, дешевле, чем `isinstance` Protocol-проверка). Оркестратор: «если supports_streaming — запустить как long-running task через `async with source: ...`; иначе — обычный pull».

### B.5. IEmbedder

НОВЫЙ контракт (todo-003). По ADR-0001.

```python
class IEmbedder(Protocol):
    model_id: str                                                    # e.g. "voyage-3.5"
    dimensions: int                                                  # должно совпадать с vector(N) в SQL; для voyage-3.5 = 1024
    cost_per_1m_tokens: Decimal
    max_batch_size: int                                              # rate-limit/контракт провайдера

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Батчевый embedding. Длина результата == len(texts), каждый вектор длиной dimensions."""
        ...

    async def embed_one(self, text: str) -> list[float]:
        """Single. Может быть тонкой обёрткой над embed([text])."""
        ...

    def estimate_cost(self, texts: list[str]) -> Decimal:
        """Оценка стоимости батча. Реализация выбирает: точный подсчёт токенов (tokenizer) или приближение (chars/4)."""
        ...
```

В Phase 0 — единственная реализация `VoyageEmbedder` в `processing/stages/embedding.py` или отдельно. Контракт провайдер-агностичный: смена на OpenAI/BGE — без миграции схемы (см. ADR-0001).

### B.6. IRepository

Единый репозиторий-контракт. Реализация в `storage/repositories.py` может разнести по модулям (`MentionsRepo`, `SignalsRepo` и т.п.), но в `core/` фиксируется одна точка входа — упрощает DI.

```python
from typing import Protocol

class IRepository(Protocol):
    # --- Mentions ---
    async def bulk_upsert_mentions_with_dedup(
        self, mentions: list[NormalizedMention]
    ) -> tuple[int, int]:
        """INSERT ... ON CONFLICT (content_hash) DO NOTHING. Returns (inserted, skipped)."""
        ...

    async def existing_hashes(self, hashes: list[str]) -> set[str]:
        """Subset of hashes already present. Used by DedupStage in bulk."""
        ...

    # --- Signals ---
    async def insert_signals(self, signals: list[Signal]) -> int: ...
    async def get_signal(self, signal_id: UUID) -> Signal | None: ...

    async def search_signals(
        self,
        project_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
        intent: Intent | None = None,
        min_score: float | None = None,
        limit: int = 100,
    ) -> list[Signal]:
        """Read-only feed for API."""
        ...

    async def search_hybrid(
        self, project_id: str, text: str, query_vector: list[float], k: int = 50
    ) -> list[Signal]:
        """BM25 + cosine + RRF in one query (see ADR-0003)."""
        ...

    # --- Scan log ---
    async def last_scanned_at(
        self, project_id: str, source_id: str, query_name: str
    ) -> datetime | None: ...

    async def record_scan(
        self,
        scan_id: UUID,
        project_id: str,
        source_id: str,
        query_name: str,
        started_at: datetime,
        finished_at: datetime,
        count: int,
        cost_usd: Decimal,
        status: ScanStatus,
    ) -> None: ...

    # --- Usage / budget ---
    async def append_usage(
        self,
        project_id: str,
        source_id: str,
        cost_usd: Decimal,
        occurred_at: datetime,
        kind: UsageKind,
    ) -> None: ...

    async def budget_used(
        self, project_id: str, since: datetime, until: datetime | None = None
    ) -> Decimal: ...

    async def budget_used_by_source(
        self, project_id: str, source_id: str, since: datetime
    ) -> Decimal: ...

    # --- Notifications ---
    async def notification_already_sent(
        self,
        project_id: str,
        signal_id: UUID,
        channel: NotificationChannel,
        target: str,
    ) -> bool: ...

    async def record_notification(
        self,
        project_id: str,
        signal_id: UUID,
        channel: NotificationChannel,
        target: str,
        sent_at: datetime,
        status: NotificationStatus,
    ) -> None: ...

    # --- Projects ---
    async def upsert_project(self, project: Project, yaml_source: str) -> None: ...
    async def get_project(self, id: str) -> Project | None: ...
    async def list_projects(self) -> list[Project]: ...

    # --- Feedback ---
    async def record_feedback(
        self,
        signal_id: UUID,
        kind: FeedbackKind,
        created_at: datetime,
        target: dict | None = None,
    ) -> None:
        """target reserved for D12; in Phase 0 always None."""
        ...
```

### B.7. IQueue

НОВЫЙ контракт (todo-003). Семантика «обработай работу, ровно один потребитель». По ADR-0003 реализация в Phase 0 — `pgmq`; контракт `IQueue` Postgres не упоминает.

```python
class IQueue(Protocol):
    async def enqueue(
        self, queue: str, payload: dict, *, delay_seconds: int = 0
    ) -> UUID:
        """Returns message_id."""
        ...

    async def dequeue(
        self, queue: str, *, visibility_timeout: int = 30
    ) -> tuple[UUID, dict] | None:
        """Returns (message_id, payload) or None if empty. Sets visibility_timeout — message reappears if not ack'd in time."""
        ...

    async def ack(self, queue: str, message_id: UUID) -> None: ...

    async def nack(
        self, queue: str, message_id: UUID, *, retry_after_seconds: int = 60
    ) -> None: ...

    async def peek_size(self, queue: str) -> int: ...
```

`payload: dict` — упрощение в Phase 0 (без generic). При необходимости типизированных очередей в Phase 1+ — миграция на `IQueue[T]` с дефолт-имплементацией `T = dict`.

### B.8. IEventBus

Pub/sub, многие подписчики. По ADR-0003 реализация в Phase 0 — Postgres LISTEN/NOTIFY + триггер на таблице `events`. Контракт `IEventBus` Postgres не упоминает.

```python
from typing import Awaitable, Callable

class Subscription(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: str

    # Methods on Subscription cannot live on a frozen Pydantic model;
    # реализация в `bus/` оборачивает Subscription в dataclass с методом
    # async def unsubscribe(self) -> None.
    # В core фиксируется только структура data — handle на отписку.

class IEventBus(Protocol):
    async def publish(self, event: "DomainEvent") -> None: ...

    async def subscribe(
        self,
        event_type: type["DomainEvent"],
        handler: Callable[["DomainEvent"], Awaitable[None]],
    ) -> Subscription: ...

    async def unsubscribe(self, subscription: Subscription) -> None: ...
```

Forward-ref на `DomainEvent` — он живёт в разделе **C** (`core/events.py`).

### B.9. INotifier

```python
class NotificationResult(BaseModel):
    status: NotificationStatus
    external_id: str | None = None                                   # ID сообщения в канале (Telegram message_id, HTTP response и т.п.)
    error: str | None = None
    cost_usd: Decimal = Decimal("0")

class INotifier(Protocol):
    channel: NotificationChannel

    async def send(
        self,
        signal: Signal,
        mention: NormalizedMention,                                  # явная передача — Signal содержит только mention_id (см. A.6)
        config: NotificationConfig,
    ) -> NotificationResult: ...

    async def health_check(self) -> bool: ...
```

### B.10. IClassifier

```python
class ClassificationResult(BaseModel):
    intent: Intent
    sentiment: Sentiment
    entities: list[str]
    topics: list[str]
    is_spam: bool
    relevance_score: float = Field(ge=0.0, le=1.0)
    cost_usd: Decimal
    model_id: str
    latency_ms: int

class IClassifier(Protocol):
    model_id: str                                                    # "claude-haiku-4-5" / "claude-sonnet-4-6"
    cost_per_1m_input_tokens: Decimal
    cost_per_1m_output_tokens: Decimal

    async def classify(
        self, mentions: list[NormalizedMention], project: Project
    ) -> list[ClassificationResult]:
        """Batched classification. len(result) == len(mentions), order preserved."""
        ...

    def estimate_cost(self, mentions: list[NormalizedMention]) -> Decimal: ...
```

### B.11. IStage

Стадия Pipeline. Регистрируется в `processing/`, реализации — в `processing/stages/`.

```python
class IStage(Protocol):
    name: str

    async def process(
        self,
        mentions: list[NormalizedMention],
        ctx: "PipelineContext",                                      # forward-ref; PipelineContext живёт в processing/, не в core/
    ) -> list[NormalizedMention]:
        """Return subset (or transformed copies) of input. Pipeline assembles trace from items_in/items_out."""
        ...
```

`PipelineContext` сознательно не в `core/` — он содержит DI-handles на `IRepository`/`IEmbedder`/`IClassifier` и runtime-state batch-а, что уводит его в processing-слой. Если в Phase 1 появится потребность в альтернативных Pipeline-реализациях, потребляющих контракт через `core/` — `PipelineContext` мигрирует сюда (см. «Открытые вопросы продукт-агенту»).

---

## C. DomainEvent — полный список

`core/events.py`. База + 12 событий из todo-003.

### C.1. Базовый класс

```python
from typing import ClassVar
from datetime import UTC

class DomainEvent(BaseModel):
    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project_id: str | None = None                                    # большинство — про проект; некоторые системные (SourceHealthChanged) — None
    event_type: ClassVar[str]                                        # фиксированная строка-имя в каждом подклассе

    model_config = ConfigDict(frozen=True, extra="forbid")
```

`event_type` — `ClassVar`, чтобы не сериализоваться в payload (значение метаданных подкласса). `IEventBus.publish` сам кладёт его в JSON-обёртку.

### C.2. Полный список событий

```python
class ScanRequested(DomainEvent):
    event_type: ClassVar[str] = "scan.requested"
    scan_id: UUID
    query_name: str
    source_id: str
    requested_query: SourceQuery

class ScanStarted(DomainEvent):
    event_type: ClassVar[str] = "scan.started"
    scan_id: UUID
    source_id: str
    query_name: str
    started_at: datetime

class MentionsFetched(DomainEvent):
    """Эмиттится при каждом батче от source — дробит длинные сканы."""
    event_type: ClassVar[str] = "mentions.fetched"
    scan_id: UUID
    batch_id: UUID
    count: int
    finished_at: datetime

class ScanFinished(DomainEvent):
    event_type: ClassVar[str] = "scan.finished"
    scan_id: UUID
    source_id: str
    query_name: str
    total_count: int
    cost_usd: Decimal
    status: ScanStatus

class ScanFailed(DomainEvent):
    event_type: ClassVar[str] = "scan.failed"
    scan_id: UUID
    source_id: str
    query_name: str
    error: str
    error_class: str                                                 # exception class name; для группировки в Prometheus

class MentionNormalized(DomainEvent):
    event_type: ClassVar[str] = "mention.normalized"
    mention_id: UUID
    content_hash: str

class MentionDeduped(DomainEvent):
    """Для observability — почему ментион выкинут на DedupStage."""
    event_type: ClassVar[str] = "mention.deduped"
    content_hash: str
    source_id: str
    reason: Literal["exact_hash", "minhash"]

class SignalReady(DomainEvent):
    event_type: ClassVar[str] = "signal.ready"
    signal_id: UUID
    mention_id: UUID
    matched_query: str
    relevance_score: float
    intent: Intent

class BudgetWarning(DomainEvent):
    event_type: ClassVar[str] = "budget.warning"
    current_usd: Decimal
    threshold_usd: Decimal
    fraction: float                                                  # current / limit, e.g. 0.82
    scope: BudgetScope
    source_id: str | None = None                                     # заполняется при scope=per_source

class BudgetExhausted(DomainEvent):
    event_type: ClassVar[str] = "budget.exhausted"
    current_usd: Decimal
    limit_usd: Decimal
    scope: BudgetScope
    source_id: str | None = None

class SourceHealthChanged(DomainEvent):
    """Глобальное событие — без project_id."""
    event_type: ClassVar[str] = "source.health_changed"
    source_id: str
    healthy: bool
    error: str | None = None

class FeedbackReceived(DomainEvent):
    event_type: ClassVar[str] = "feedback.received"
    signal_id: UUID
    kind: FeedbackKind
    target: dict | None = None                                       # зарезервировано под D12; в Phase 0 — None
    received_at: datetime
```

### C.3. Правило payload-полноты

Поля события — это **именно те поля, которые subscriber-у нужны без обращения в БД** для типичной реакции на событие. Для всего остального subscriber делает `repository.get_signal(signal_id)`. Это исключает раздувание payload и удерживает события небольшими (важно для LISTEN/NOTIFY — там есть лимит на размер payload, ~8KB).

---

## D. Формат `content_hash` (детерминированная нормализация)

Реализация — `processing/stages/normalize.py` (не в `core/`). Этот раздел — спецификация алгоритма, фиксирующая ADR-0004 на уровне правил.

### D.1. Алгоритм по шагам

Дано: `RawMention` с полями `text` и опционально `text_html`. Возвращается `text_clean` и `content_hash`.

1. **Извлечение текста.**
   - Если `text_html` непустой:
     - Парсинг через `selectolax.parser.HTMLParser` (быстрее BeautifulSoup, не требует lxml).
     - Удалить полностью теги `<script>`, `<style>`, `<noscript>` (и их содержимое).
     - Извлечь только текст из остального DOM (`tree.body.text(separator=" ")` или эквивалент).
     - Установить `is_html_stripped = True`.
   - Иначе:
     - Использовать `text` как есть.
     - `is_html_stripped = False`.

2. **Удаление трекинговых параметров из inline-ссылок.**
   - Поиск URL по regex `r"https?://\S+"`.
   - Для каждого:
     - `urllib.parse.urlsplit` → `SplitResult`.
     - Из `query` через `parse_qsl(keep_blank_values=True)` удалить ключи из списка ниже (case-insensitive).
     - Перерендерить через `urlunsplit` + `urlencode`.
     - Записать список фактически удалённых ключей в `tracking_params_removed` (для аудита; пригодится при отладке «почему дедуп не схватил»).

   **Список удаляемых параметров** (фиксированный, регистронезависимый):
   ```
   utm_source, utm_medium, utm_campaign, utm_term, utm_content,
   fbclid, gclid, mc_eid, mc_cid, igshid,
   _hsenc, _hsmi, ref, ref_src, ref_url,
   vero_id, yclid, msclkid, twclid
   ```

3. **Unicode normalization.** `text = unicodedata.normalize("NFKC", text)`.

4. **Lowercase.** `text = text.lower()`.

5. **Collapse whitespace.** `text = re.sub(r"\s+", " ", text).strip()`.

   Шаги 1–5 дают `text_clean`.

6. **Hash.** `content_hash = hashlib.sha256(text_clean.encode("utf-8")).hexdigest()`. 64-char hex.

### D.2. Что НЕ входит в нормализацию (явно)

- Перевод между языками.
- Удаление эмодзи.
- Удаление цифр или пунктуации.
- Lemmatization / stemming.
- Расширение сокращений или замена символов (e.g. `&` → `and`).

Эти операции — для retrieval/embedding-стадий, не для дедупа. Дедуп должен быть **жёстким** — два текста, отличающиеся одной запятой, считаются разными. Это сознательный выбор в пользу precision; для near-dedup в Phase 1+ зарезервирован `minhash_signature` в `NormalizedMention`.

### D.3. Versioning

`NormalizedMention.normalize_version: int = 1`. При любом изменении алгоритма (включая изменение списка трекинговых параметров) — инкрементировать. Поле сохраняется в БД. Пересчёт старых записей не автоматический; batch-job — отдельное продуктовое решение в Phase 1+.

### D.4. Тест-кейсы (для разработчика E1, ветка 2)

Эти кейсы должны покрываться unit-тестами в `processing/stages/normalize.py` или `tests/unit/test_normalize.py`:

1. **Cross-source identity:** тот же текст в `RawMention` от Reddit и от Bluesky → одинаковый `content_hash`.
2. **UTM-стрип:** `https://example.com/?utm_source=newsletter&id=42` и `https://example.com/?id=42` в идентичном тексте → одинаковый `content_hash`. `tracking_params_removed = ["utm_source"]` в первом случае, `[]` во втором.
3. **HTML-эквивалент:** `<p>Hello <b>world</b></p>` и plain `Hello world` → одинаковый `content_hash`. В первом случае `is_html_stripped=True`.
4. **Whitespace-эквивалент:** `Hello    world\n\n` и `hello world` → одинаковый `content_hash` (lowercase + collapse).
5. **NFKC:** `Café` (precomposed) и `Cafe` + combining accent → одинаковый `content_hash`.

---

## E. Версионирование контрактов

Цель раздела — задать декларативную планку «что breaking, что additive», чтобы изменения после первой недели разработки были осознанным архитектурным событием, а не эволюционной правкой.

### E.1. Breaking-изменения

**Любое из перечисленного** — breaking. Требует ADR-сессии с владельцем и migration plan.

- Удаление поля из Pydantic-модели.
- Изменение типа поля (включая сужение типа, например `str → Literal[...]`).
- Изменение семантики при сохранении типа (например, изменение единиц измерения с секунд на миллисекунды).
- Удаление Protocol-метода или изменение его сигнатуры (включая порядок аргументов, типы, добавление обязательного аргумента).
- Удаление DomainEvent-класса.
- Изменение `event_type` существующего события.
- Удаление варианта из Literal-типа (e.g. удалить `"complaint"` из `Intent`).
- Изменение размерности embedding-вектора (это breaking сразу для `IEmbedder.dimensions`, `NormalizedMention.embedding`, `TopicQuery.topic_embedding`, и SQL-схемы — каскадное; см. ADR-0001).

### E.2. Additive-изменения

Можно вносить без ADR, но с обновлением `core/CLAUDE.md` в той же PR.

- Добавление опционального поля с дефолтом в Pydantic-модель.
- Добавление нового Protocol-метода **с дефолтной реализацией в base-классе** (`BaseSource`, `BaseNotifier` в plugin-папках). Если дефолт-имплементация невозможна — это breaking.
- Добавление нового DomainEvent.
- Добавление нового NotificationChannel-варианта (e.g. `"slack"`).
- Добавление нового варианта в Literal-тип (e.g. новый `Intent`).
- Добавление нового Protocol (e.g. `ICache` в Phase 1).

### E.3. Имена версий

- **Pydantic-модели и Protocol-ы:** при breaking — суффикс в имени класса: `RawMentionV2`, `ISourceV2`. Старая версия не удаляется одномоментно — живёт минимум до закрытия очередного этапа Phase 0/1, помечается комментарием `# DEPRECATED since 2026-XX-XX, use RawMentionV2`.
- **DomainEvent:** при breaking — новое имя класса (`SignalReadyV2`); поле `event_type` подбирается так, чтобы старые подписчики продолжали получать старые события в течение dual-write периода миграции.
- **`normalize_version`** — отдельный счётчик в `NormalizedMention`, инкрементируется только при изменении алгоритма раздела D (это узкий, вертикальный re-version, не общий контрактный).

### E.4. Текущая версия контрактов

**Версия: 1.** При первом breaking-изменении начинается работа над версией 2 — параллельная экспозиция `*V2` классов, dual-write/dual-read период, миграция всех зависимых модулей, удаление V1.

### E.5. Полномочия

- Архитектор-агент имеет право вносить **additive-изменения** в core напрямую (с PR-ревью владельцем).
- **Breaking-изменения** инициируются только продукт-агентом через ADR-сессию с владельцем. Архитектор может предложить breaking — но не выполнить.

---

## F. ADR-trail (повторно, по разделам)

| Раздел документа | ADR | Содержание ADR |
|---|---|---|
| A.4 (`embedding`), A.7 (`topic_embedding`), B.5 (`IEmbedder`) | ADR-0001 | dim=1024, model="voyage-3.5" |
| A.1 (`SourceMode`), A.2 (`SourceQuery.mode/since_cursor`), B.1 (`supports_streaming`), B.4 (`IStreamingSource`) | ADR-0002 | Bluesky streaming в Phase 0; Telegram MTProto отложен |
| B.7 (`IQueue`), B.8 (`IEventBus`) | ADR-0003 | один Postgres; Queue и EventBus — разные Protocol |
| A.4 (`content_hash`), D (полный алгоритм нормализации) | ADR-0004 | content_hash = sha256(normalized_text) |

Любое архитектурное решение, изменяющее эти разделы — требует обновления соответствующего ADR (или создания нового).

---

## Открытые вопросы продукт-агенту

Эти решения приняты архитектором в рамках сессии — продукт-агент может пересмотреть, если это не противоречит закрытым ADR.

1. **Signal: `mention_id` (FK) vs nested `mention`.** Архитектор выбрал FK — отступление от ARCHITECTURE 1.1, где показана inline-композиция. Аргументы за FK перечислены в A.6. Если продукт-агент решит, что shipping events требуют self-contained payload (например, для аудита signals после retention text/text_clean) — пересматривается с migration plan для `INotifier.send`, `Signal`, `SignalReady`.

2. **D12 — `feedback_log.target` структура.** В core зафиксирован `target: dict | None` placeholder в `IRepository.record_feedback` и `FeedbackReceived`. В Phase 0 используется как `None`. Решение нужно **до старта E5** (там реализуется `feedback_log`). Варианты для D12:
   - `target: Literal["mention", "author", "keyword", "topic"] | None`.
   - Структурированный объект `{"kind": "...", "value": "..."}`.
   Решение влияет на схему `feedback_log` (добавление колонок) и payload `FeedbackReceived`. Сейчас — additive-эволюция: dict-поле не меняет схему.

3. **`PipelineContext` локализация.** Сейчас декларирован forward-ref в `IStage.process` и физически живёт в `processing/`. Если продукт-агент захочет сделать его частью публичного контракта (для альтернативных pipeline-имплементаций, например streaming-pipeline) — переносится в core с миграцией всех `IStage`-имплементаций. Не блокер для E1.

4. **Mini-DSL `NotificationConfig.filter_expr`.** Грамматика и парсер — НЕ в core. Должны быть зафиксированы в `notifications/CLAUDE.md` (отдельная сессия архитектора, перед E5). В core — только тип `str | None`. Это ограничение области текущей сессии.

5. **`Project.pipeline: list[str | dict]` raw-формат.** Полная типизация конфига pipeline-стадий — задача `config/CLAUDE.md` (отдельная сессия архитектора, перед E2c). В core фиксируется минимум, чтобы Project сериализовался обратно в YAML.

Если в ходе разработки агент-исполнитель E1 обнаружит, что какое-то из этих решений нужно расширить/уточнить — он добавляет вопрос сюда **через PR**, а не правит код в обход. Это сохраняет инвариант «контракты неприкосновенны».

---

## OPS

- **Type**: folder
- **Parent**: `../../CLAUDE.md`
- **Root**: `../../../../../`
- **Мета-файлы**: [.timeline](.timeline), [.links](.links)

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
