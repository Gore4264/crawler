# Дизайн-система глубокого мониторинга сети

Архитектурный документ. Пригоден для использования как корневой `ARCHITECTURE.md` в Claude-Code-проекте: каждый раздел самодостаточен и его можно подать агенту изолированно.

---

## ⚙ Phase 0 scope (актуально 2026-04-27)

Документ описывает **полную целевую архитектуру** на горизонт Phase 1-3.
В Phase 0 (MVP-инструмент ручного режима) реализуется **подмножество**:

**В Phase 0:** Domain Core (раздел 1), Storage (2), Sources (3 — только
Reddit), Processing (5 — все 8 стадий), MCP-сервер (часть раздела 8.4) +
**новый CLI-слой** (не описан в этом документе, см. `crawler/cli/CLAUDE.md`
после Ветки 3 E1).

**Phase 1+ (НЕ в Phase 0):**
- Раздел 4 (Orchestration: scheduler / budget guard / dispatcher / retry policy).
- Раздел 5.4 параллелизм через scheduler (одиночные ручные scan-ы достаточны).
- Раздел 6 (Configuration: YAML-конфиг проектов — заменяется CRUD через CLI/MCP).
- Раздел 7 (Notifications: Telegram, webhook, email, inline-feedback, filter-движок).
- Раздел 8.1-8.3 (REST API, WebSocket, FastAPI) — заменяются MCP-only.
- Раздел 9.1 (Event Bus: Postgres LISTEN/NOTIFY) — Phase 0 без bus.
- 7-дневный soak-test (`KPI #8`) — нерелевантен для pull-инструмента.

**Контракты остаются совместимы:** `INotifier`, `IEventBus`, `IQueue` Protocol-ы
живут в `core/contracts.py` как stubs без имплементаций (оставлены под Phase 1+).

См. `ROADMAP.md` для пошагового плана Phase 0.

---

## ⚠ Критические приоритеты: что важнее остального

Три акцента, без которых остальной документ можно прочитать неправильно. Если документ читается по диагонали — этот раздел читается полностью.

**Контракты в `core/contracts.py` — это вся ценность системы.** Если в реализации придётся срезать углы под давлением сроков, режь где угодно, кроме контрактов. Там пять Protocol-ов (`ISource`, `IStage`, `IRepository`, `INotifier`, `IEventBus`), и если они правильные — остальное собирается за 12 недель в любом порядке. Если сломаны — система превращается в спагетти к пятой неделе, и переделывать придётся всё. Полное определение контрактов — раздел 1.2. Любое изменение этого файла после первой недели — серьёзное архитектурное событие, требующее migration plan для всех зависимых модулей.

**MCP-сервер (раздел 8.4) — это второй виток leverage, а не опциональная фича.** На первый взгляд это «ещё один способ доступа к API», и есть соблазн отложить его в Phase 2 как roadmap-пункт. Это ошибка. Когда система становится MCP-сервером для Claude Code, каждая сессия разработки получает доступ к её данным как к контексту. «Найди мне всех Unity-разработчиков в Дананге, упоминавших Claude в последний месяц» превращается из часового исследования в один tool call. Это качественное изменение производительности владельца, и ради него стоит инвестировать в MCP с самого начала.

**Pipeline-каскад (раздел 5.2) — главный экономический рычаг системы.** Четыре бесплатные стадии перед `LLMClassifyStage` (Normalize, Dedup, KeywordFilter, SemanticFilter) отсекают 95% шума. Если поленишься и пустишь LLM сразу после Normalize, бюджет на $50/мес превращается в $50/день. Дисциплина «всё, что можно отсеять без LLM — отсеивается без LLM» — это разница между жизнеспособной системой и хобби-проектом, который умрёт от cost-overrun. Каждое нарушение порядка стадий должно требовать письменного обоснования.

---

## 0. Принципы и инварианты

Семь принципов, которые держат систему в форме при росте. Если какое-то решение их нарушает — это сигнал переосмыслить, а не обходить.

**Контракты раньше реализаций.** Каждый слой определяется через `Protocol`-интерфейс в `core/contracts.py`. Реализации сменяемы, контракты — нет. Это даёт изоляцию для Claude Code: один агент работает с одной реализацией, не зная других.

**Plugin everywhere.** Источники, стадии pipeline, нотификаторы — всё плагины. Добавить TikTok = создать файл в `plugins/sources/tiktok.py`. Добавить уведомление в Discord = `plugins/notifications/discord.py`. Никаких изменений в ядре.

**Декларативная конфигурация проектов.** Проект — это YAML, а не код. Темы, ключи, источники, бюджеты, расписания, пороги релевантности — всё в одном файле. Это позволяет копировать проекты, версионировать их в git, генерировать через UI без правок кода.

**Event-driven слабая связанность.** Слои общаются через Event Bus, а не вызывают друг друга напрямую (за исключением чтения через интерфейсы хранилища). Notifications не знает про Processing — он только подписан на `SignalReady`.

**Cost-aware с первого дня.** Каждый Source умеет оценить стоимость запроса до его выполнения. Orchestrator не запускает скан, если бюджет проекта исчерпан. Все фактические расходы пишутся в `usage_log` с разбивкой по проекту/источнику/часу.

**Idempotency.** Повторный запуск ничего не ломает: дедупликация работает на уровне Storage (UNIQUE-индекс по хешу), а ScanLog хранит «когда последний раз сканировали этот source для этого project с этими params». При перезапуске воркера ничего не дублируется.

**Postgres-first.** Один Postgres держит данные, эмбеддинги (pgvector), полнотекст (tsvector/BM25), очередь задач (pgmq или pg-boss), шину событий (LISTEN/NOTIFY) и distributed locks (advisory locks). Никаких Kafka, Redis, Elasticsearch, Pinecone — пока проект соло. Это сознательное решение в пользу простоты эксплуатации.

---

## 1. Domain Core (поперечный)

Это сердце. Здесь живут только типы и контракты — никакой логики. Любой агент Claude Code, начинающий работу со слоем, читает этот раздел первым.

### 1.1 Value objects (Pydantic v2 модели)

```python
# core/models.py
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field
from uuid import UUID, uuid4

class SourceQuery(BaseModel):
    """Унифицированный запрос к любому источнику."""
    keywords: list[str] = []                    # точные ключи (OR-семантика внутри списка)
    semantic_query: str | None = None           # текст для embedding-based поиска
    excluded_keywords: list[str] = []
    languages: list[str] = []                   # ISO 639-1, пусто = все
    geo: str | None = None                      # ISO 3166-1 alpha-2, либо city name
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 100
    max_cost_usd: float = 1.0                   # верхняя граница, source может вернуть пусто

class RawMention(BaseModel):
    """То, что источник возвращает «как есть». Минимально структурировано."""
    source: str                                 # "reddit", "bluesky"
    source_id: str                              # уникальный ID внутри платформы
    url: str
    author: str | None = None
    author_id: str | None = None
    text: str
    lang: str | None = None
    posted_at: datetime
    fetched_at: datetime
    raw: dict = Field(default_factory=dict)     # исходный JSON для будущих стадий

class NormalizedMention(RawMention):
    """После Normalize: text_clean, content_hash, и опц. эмбеддинг (на стадии Embed)."""
    id: UUID = Field(default_factory=uuid4)
    text_clean: str
    content_hash: str                           # sha256(text_clean.lower().strip())
    minhash_signature: list[int] | None = None  # для near-dedup
    embedding: list[float] | None = None        # появляется на EmbedStage

class Signal(BaseModel):
    """Финальный обогащённый ментион — то, что попадает в нотификации и витрины."""
    mention: NormalizedMention
    relevance_score: float                      # 0..1, главный критерий
    is_spam: bool
    intent: Literal["complaint","question","recommendation",
                    "advertisement","news","discussion","other"]
    sentiment: Literal["positive","neutral","negative"]
    entities: list[str] = []
    topics: list[str] = []
    matched_query: str                          # имя темы из проекта, к которой пристегнули
    pipeline_trace: list[str] = []              # имена пройденных стадий, для отладки
    cost_usd: float = 0.0                       # сколько стоила обработка этого ментиона

class Project(BaseModel):
    """Корень агрегации. Всё, что описывает один кейс мониторинга."""
    id: str                                     # slug
    name: str
    queries: list["TopicQuery"]
    sources: list[str]                          # имена включённых источников
    notifications: list["NotificationConfig"]
    budget: "BudgetConfig"
    schedule_default: str                       # cron-выражение по умолчанию
    settings: dict = Field(default_factory=dict)
```

`TopicQuery`, `BudgetConfig`, `NotificationConfig` — производные структуры, они в полном файле `models.py`, но логика та же: всё иммутабельно после загрузки, всё валидируется Pydantic.

### 1.2 Контракты (Protocol)

```python
# core/contracts.py
from typing import Protocol, AsyncIterator

class SourceCapabilities(BaseModel):
    supports_keywords: bool = True
    supports_semantic: bool = False
    supports_geo: bool = False
    supports_language_filter: bool = False
    supports_realtime_stream: bool = False
    supports_historical: bool = True
    cost_model: Literal["free","per_request","per_result","subscription"] = "free"
    typical_latency_ms: int = 1000

class CostEstimate(BaseModel):
    expected_results: int
    expected_cost_usd: float
    confidence: Literal["exact","estimate","unknown"]

class ISource(Protocol):
    name: str
    capabilities: SourceCapabilities
    async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]: ...
    async def health_check(self) -> bool: ...
    def estimate_cost(self, q: SourceQuery) -> CostEstimate: ...

class IStage(Protocol):
    name: str
    async def process(
        self, mentions: list[NormalizedMention], ctx: "PipelineContext"
    ) -> list[NormalizedMention]: ...

class IRepository(Protocol):
    async def bulk_insert(self, mentions: list[NormalizedMention]) -> int: ...
    async def existing_hashes(self, hashes: list[str]) -> set[str]: ...
    async def last_scanned_at(self, project_id: str, source: str, query_name: str) -> datetime | None: ...
    async def search_hybrid(self, project_id: str, query: str, k: int = 50) -> list[Signal]: ...
    async def append_usage(self, project_id: str, source: str, cost_usd: float) -> None: ...
    async def budget_used(self, project_id: str, since: datetime) -> float: ...

class INotifier(Protocol):
    name: str
    async def send(self, signal: Signal, config: NotificationConfig) -> None: ...

class IEventBus(Protocol):
    async def publish(self, event: "DomainEvent") -> None: ...
    async def subscribe(self, event_type: str, handler) -> None: ...
```

### 1.3 События

Каноничный список событий в `core/events.py`. Все наследуют `DomainEvent` с полями `event_id`, `occurred_at`, `project_id`. Полная номенклатура: `ScanRequested`, `ScanStarted`, `MentionsFetched`, `ScanFinished`, `ScanFailed`, `MentionProcessed`, `SignalReady`, `BudgetWarning`, `BudgetExhausted`, `SourceUnhealthy`. Никаких событий с CRUD-операциями над сущностями — только бизнес-факты.

---

## 2. Слой 1: Storage

Самый нижний слой. Все остальные читают и пишут только через интерфейсы из `IRepository` и связанных абстракций. Никто не лезет в SQL напрямую вне этого слоя.

### 2.1 Схема (Postgres 16 + pgvector + pgmq)

Семь основных таблиц:

| Таблица | Назначение |
|---|---|
| `projects` | YAML-проект, загруженный в БД. Один источник истины. |
| `mentions` | Каждый уникальный ментион. `content_hash` UNIQUE. |
| `signals` | Обогащённый результат после pipeline. 1:1 с mentions, разделены ради эволюции схемы Signal. |
| `embeddings` | `vector(1024)` для Voyage 3.5 / 1536 для OpenAI-3-small. HNSW-индекс. |
| `scan_log` | Кто сканировал что и когда. Используется в `last_scanned_at`. |
| `usage_log` | Финансовая телеметрия. Час+проект+источник+стоимость. |
| `notification_log` | Какие сигналы куда улетели. Для дедупликации алертов. |

Полнотекст — `tsvector` колонка на `mentions.text_clean` с GIN-индексом + `pg_search` (BM25, более точный, чем стандартный ts_rank). Гибридный поиск — два пути в одном запросе с RRF, объединение через CTE.

Очередь задач — `pgmq` (Postgres Message Queue). Шина событий — `LISTEN/NOTIFY` с triggers. Для соло-проекта этого хватает с запасом до миллионов сообщений.

### 2.2 Массовые контракты (то, что пользователь правильно подметил)

Помимо элементарных CRUD, репозиторий обязан давать «массовые» операции, иначе оркестратор замучается с N+1:

```python
# storage/repositories.py
class MentionsRepository:
    async def existing_hashes(self, hashes: list[str]) -> set[str]:
        """Возвращает подмножество хешей, уже существующих в БД."""
        ...

    async def last_scanned_at(
        self, project_id: str, source: str, query_name: str
    ) -> datetime | None:
        """До какой даты уже отсканировано — чтобы не повторять прошлое."""
        ...

    async def bulk_upsert_with_dedup(
        self, mentions: list[NormalizedMention]
    ) -> tuple[int, int]:
        """INSERT ... ON CONFLICT (content_hash) DO NOTHING.
        Возвращает (inserted, skipped). Делает дедуп на стороне БД."""
        ...
```

Эти три метода — это и есть «контракты массовой обработки» из твоего наброска. Они освобождают оркестратор и pipeline от знания, какие хеши уже видены.

### 2.3 Где живут эмбеддинги

Технически в той же `mentions` таблице — `embedding vector(1024)` колонка. Но в репозитории они отделены:

```python
class EmbeddingIndex:
    async def upsert(self, mention_id: UUID, vector: list[float]) -> None: ...
    async def search_semantic(
        self, query_vector: list[float], project_id: str, k: int = 50
    ) -> list[tuple[UUID, float]]: ...
    async def search_hybrid(
        self, project_id: str, text: str, query_vector: list[float], k: int = 50
    ) -> list[tuple[UUID, float]]:
        """BM25 + cosine + RRF в одном запросе через CTE."""
        ...
```

Это даёт право в будущем вынести эмбеддинги в отдельный Qdrant без переписывания pipeline.

---

## 3. Слой 2: Sources

Адаптеры к внешним платформам. Каждый — отдельный плагин, реализующий `ISource`.

### 3.1 Структура папки

```
plugins/sources/
├── _base.py              # переиспользуемые helpers (rate limiting, retry, http session)
├── reddit.py             # PRAW wrapper
├── bluesky.py            # atproto firehose + search
├── telegram_public.py    # Telethon на read-only аккаунте
├── x_via_apify.py        # обёртка над apify-client → apidojo/tweet-scraper-v2
├── rsshub.py             # ходит в self-hosted RSSHub за фидами
├── google_alerts.py      # парсит email-форвард в IMAP-папке
├── brave_search.py       # https://api.search.brave.com
├── exa_semantic.py       # семантический discovery через exa.ai
└── visualping_changes.py # webhook от Visualping → нормализованный RawMention
```

### 3.2 Регистрация плагинов

Простейший способ — entry_points в `pyproject.toml`. Чуть гибче — динамическая загрузка из папки. Я бы делал гибридно: ядро регистрирует всё из `plugins/sources/*.py`, исключая файлы с префиксом `_`. Класс должен наследовать `BaseSource` и иметь атрибут `name`.

```python
# plugins/sources/reddit.py
from plugins.sources._base import BaseSource, BaseSourceConfig
from core.contracts import SourceCapabilities
from core.models import SourceQuery, RawMention, CostEstimate

class RedditConfig(BaseSourceConfig):
    client_id: str
    client_secret: str
    user_agent: str
    subreddits: list[str] = ["all"]

class RedditSource(BaseSource[RedditConfig]):
    name = "reddit"
    capabilities = SourceCapabilities(
        supports_keywords=True,
        supports_semantic=False,
        supports_geo=False,
        supports_realtime_stream=True,
        cost_model="free",
    )

    async def search(self, q: SourceQuery):
        # PRAW запрос
        async for sub in self._reddit.subreddit("+".join(self.config.subreddits)).search(...):
            yield self._to_raw(sub)

    def estimate_cost(self, q: SourceQuery) -> CostEstimate:
        return CostEstimate(expected_results=q.limit, expected_cost_usd=0.0, confidence="exact")
```

### 3.3 Capabilities-driven диспетчеризация

Когда orchestrator хочет «найти Unity-разработчиков в Дананге», он смотрит на `capabilities.supports_geo` всех источников и фильтрует. Если источник не умеет geo, но проект требует — оркестратор автоматически добавит post-filter на стороне processing вместо отказа.

Это и есть «гибкость» из твоих требований: добавляешь источник с `supports_realtime_stream=True` — система сразу знает, что его можно использовать в long-running consumer вместо периодического pull.

---

## 4. Слой 3: Orchestration

То, чего не было в твоём наброске, но без чего система не живёт. Решает три вопроса: **когда сканировать**, **что сканировать**, **прерывать ли в случае проблем с бюджетом**.

### 4.1 Компоненты

```
orchestration/
├── scheduler.py        # APScheduler или Prefect — крутит cron-выражения проектов
├── budget_guard.py     # перед каждым ScanRequested проверяет usage_log vs budget
├── dispatcher.py       # принимает ScanRequested → находит ISource → запускает
├── retry_policy.py     # exponential backoff, circuit breaker по source.health_check
└── plan.py             # для каждого Project.queries[].sources вычисляет следующий запуск
```

### 4.2 Жизненный цикл скана

1. `Scheduler` тикает по cron-выражению темы проекта.
2. `BudgetGuard.check(project_id)` — если потрачено ≥ 95% бюджета, эмитим `BudgetExhausted` и пропускаем.
3. Создаётся `ScanRequested(project, query, source)` событие → в шину.
4. `Dispatcher` слушает шину, находит зарегистрированный `ISource`, вызывает `estimate_cost`. Если оценка превышает остаток бюджета — отказ + `BudgetWarning`.
5. Если ОК — вызывается `source.search(q)`, поток `RawMention` идёт в очередь pipeline.
6. После завершения — `ScanFinished(scan_id, count, actual_cost)`, запись в `scan_log` и `usage_log`.

### 4.3 Бюджет как first-class

В YAML проекта:
```yaml
budget:
  monthly_usd: 50
  daily_usd: 5            # опционально, soft-limit
  per_source_caps:
    x_via_apify: 30       # на этом источнике не разгуляешься
    brave_search: 10
  warning_threshold: 0.8  # эмиттить BudgetWarning при 80%
```

`budget_guard.py` агрегирует `usage_log` за месяц/день и сверяет. Это критично, потому что Apify, Voyage, Claude API, Brave Search — все они платные, и одно сломанное расписание может за ночь сжечь $200.

---

## 5. Слой 4: Processing Pipeline

Сердце обогащения. То, что превращает сырой ментион в Signal.

### 5.1 Pipeline как chain of stages

```python
# processing/pipeline.py
class Pipeline:
    def __init__(self, stages: list[IStage]):
        self.stages = stages

    async def run(self, mentions: list[RawMention], project: Project) -> list[Signal]:
        ctx = PipelineContext(project=project)
        items = [self._normalize(m) for m in mentions]
        for stage in self.stages:
            items = await stage.process(items, ctx)
            ctx.trace(stage.name, len(items))
        return [self._to_signal(m, ctx) for m in items]
```

### 5.2 Каноничный набор стадий

| Стадия | Назначение | Падает в стоимости |
|---|---|---|
| `NormalizeStage` | unicode NFKC, эмодзи-стрипинг, html-cleanup, lang detect | бесплатно |
| `DedupStage` | sha256 hash + MinHashLSH near-dedup. Дёргает `existing_hashes` массово | бесплатно |
| `KeywordFilterStage` | regex по `project.keywords` и `excluded_keywords` | бесплатно |
| `EmbeddingStage` | Voyage 3.5 / OpenAI-3-small. Батчинг 100 ментионов за вызов | $0.06–0.20/1M токенов |
| `SemanticFilterStage` | cosine с эмбеддингами тем проекта, threshold 0.55 | бесплатно (после Embed) |
| `LLMClassifyStage` | Claude Haiku Batch + tool_use. Заполняет intent/sentiment/entities/score | $0.50/1k ментионов |
| `RankStage` | RRF на BM25 + dense + LLM relevance | бесплатно |
| `DecideStage` | финальное решение: `signal_ready = score ≥ project.threshold AND NOT spam` | бесплатно |

### 5.3 Конфигурация pipeline на проект

В YAML проекта можно переопределить порядок и параметры стадий:

```yaml
pipeline:
  - normalize
  - dedup
  - keyword_filter
  - embedding:
      model: voyage-3.5
      dim: 1024
  - semantic_filter:
      threshold: 0.50           # для этого проекта мягче
  - llm_classify:
      model: claude-haiku-4-5
      batch: true
      tool: classify_post_v2    # альтернативный промпт
  - decide:
      threshold: 0.65
```

Это позволяет одному проекту экономить на LLM (выкинуть `llm_classify`), а другому — добавить кастомную стадию `geo_filter` после semantic.

### 5.4 Параллелизм и батчи

Pipeline работает на батчах в 100–1000 ментионов. EmbeddingStage и LLMClassifyStage параллелят запросы внутри батча через `asyncio.gather` с семафором (для контроля rate limit). DedupStage и KeywordFilterStage — синхронные, но быстрые. Узким местом всегда будет LLM, поэтому система должна ампутировать как можно больше **до** LLM-стадии (что и делают четыре стадии перед ней).

---

## 6. Слой 5: Configuration

Твой «слой настройки». Здесь живут проекты как декларативные сущности.

### 6.1 Project как YAML

Полный пример:

```yaml
# config/projects/ar-mat-monitor.yaml
id: ar-mat-monitor
name: "AR-коврик: бренд + конкуренты + клиенты"

queries:
  - name: brand_mentions
    keywords: ["MyMatBrand", "myMat AR"]
    sources: [reddit, bluesky, telegram_public, rsshub]
    schedule: "*/15 * * * *"

  - name: competitors
    keywords: ["Lululemon Studio Mirror", "Tonal Mat"]
    semantic: "AR-enabled smart fitness mats with computer vision"
    sources: [all]
    schedule: "0 */2 * * *"

  - name: customer_pains
    semantic: "people complaining about traditional yoga mats or wanting smart fitness gear at home"
    excluded_keywords: ["coupon", "promo", "discount"]
    sources: [reddit, bluesky]
    schedule: "0 9 * * *"

  - name: danang_devs
    keywords: ["Da Nang", "Đà Nẵng", "Danang"]
    semantic: "AI engineers, Unity developers, Claude Code users in Vietnam"
    sources: [bluesky, telegram_public, rsshub]
    schedule: "0 12 * * *"

sources:
  - reddit
  - bluesky
  - telegram_public
  - rsshub

pipeline:
  - normalize
  - dedup
  - keyword_filter
  - embedding: { model: voyage-3.5 }
  - semantic_filter: { threshold: 0.55 }
  - llm_classify: { model: claude-haiku-4-5, batch: true }
  - rank
  - decide: { threshold: 0.7 }

budget:
  monthly_usd: 50
  per_source_caps:
    x_via_apify: 30
  warning_threshold: 0.8

notifications:
  - channel: telegram
    target: "-1001234567890"
    filter: "relevance_score >= 0.75 AND intent != 'advertisement'"
  - channel: webhook
    target: "https://my-unity-app.local/signals"
    filter: "matched_query == 'danang_devs'"
```

### 6.2 ConfigurationStore

```python
# config/store.py
class ConfigurationStore:
    async def load_from_yaml(self, path: Path) -> Project: ...
    async def save(self, project: Project) -> None: ...
    async def list_projects(self) -> list[Project]: ...
    async def validate(self, project: Project) -> list[ValidationError]:
        """Проверяет: источники существуют, эмбеддинги совместимы по dim,
        cron-выражения валидны, бюджет не нулевой, итд."""
```

Источник истины — таблица `projects` в БД (поле `yaml_source: text` + распарсенные поля). YAML-файлы в `config/projects/` — это git-friendly бэкап, который можно sync'ать через CLI-команду.

### 6.3 Темы (Topics) как переиспользуемые блоки

Часто бывает, что разные проекты хотят одну и ту же тему. Имеет смысл вынести темы в отдельные YAML-файлы и реферить:

```yaml
# config/topics/ar_smart_fitness.yaml
name: ar_smart_fitness
semantic: "AR-enabled smart fitness mats with computer vision"
keywords: ["AR mat", "smart yoga mat", "computer vision fitness"]
languages: [en, vi, ru]
```

В проекте: `queries: [{ uses_topic: ar_smart_fitness, sources: [...], schedule: ... }]`.

---

## 7. Слой 6: Notifications

Тоже отсутствовал в твоём наброске явно. Подписан на `SignalReady`, рассылает по каналам. Каналы — плагины, как и источники.

### 7.1 Контракт

```python
class INotifier(Protocol):
    name: str
    async def send(self, signal: Signal, config: NotificationConfig) -> None: ...
```

`NotificationConfig` содержит `channel`, `target` (chat_id, email, URL), `filter` (CEL/SQL-like выражение для post-filter). Filter — критически важная штука, потому что без него Telegram-чат превратится в спам-помойку.

### 7.2 Компоненты

```
plugins/notifications/
├── _base.py
├── telegram.py     # aiogram, поддерживает inline-кнопки FP/FN feedback
├── email.py        # SMTP
├── webhook.py      # POST на любой URL — для Unity-морды и других интеграций
├── slack.py
└── discord.py
```

### 7.3 Дедупликация алертов

Один и тот же ментион в разных источниках = один сигнал, но риск дубликатов остаётся. `notification_log` (project_id, signal_id, channel, target, sent_at) с unique-индексом гарантирует, что один и тот же сигнал не уйдёт в один и тот же чат дважды. Если меняется только `relevance_score` (например, при переклассификации), новой нотификации не будет — это сознательное решение.

### 7.4 Inline-обратная связь

В Telegram-уведомление встраиваются кнопки `✅ Релевантно` / `❌ Шум` / `🚫 Заблокировать автора`. Колбэки летят в API Layer, оттуда в `feedback_log`. Эти данные используются для:
- расчёта точности классификации по проектам;
- автоматического обновления `excluded_keywords` (если автор много раз помечен как шум);
- в перспективе — fine-tuning классификатора или рефакторинга промпта.

---

## 8. Слой 7: API & Presentation

Последний слой. То, через что любая морда (web, Unity, CLI, Telegram-bot, MCP) общается с системой.

### 8.1 Принципы

API-first. Любая операция системы доступна через REST или WebSocket. Это значит:
- веб-морда (Next.js) — обычный клиент API;
- Unity-морда — обычный клиент API;
- CLI-инструменты — обычный клиент API;
- MCP-сервер для Claude — обёртка над API.

Никаких операций «только из админки»: всё, что делает админка, можно сделать `curl`-ом.

### 8.2 Структура

```
api/
├── main.py                  # FastAPI app
├── auth.py                  # API-key или JWT
├── routes/
│   ├── projects.py          # CRUD проектов
│   ├── sources.py           # GET список источников и их capabilities
│   ├── mentions.py          # поиск, фильтр, экспорт
│   ├── signals.py           # ленты сигналов с фильтрами
│   ├── usage.py             # бюджет/расходы по проектам
│   ├── feedback.py          # FP/FN отметки
│   └── ws.py                # WebSocket для real-time
└── mcp_server.py            # отдельный entry_point — MCP для Claude Code
```

### 8.3 WebSocket-канал для Unity-морды

```
GET /ws/signals?project_id=ar-mat-monitor&token=...
```

Сервер шлёт в канал JSON-сообщения формата `{"type": "signal", "signal": {...}}` каждый раз, когда `SignalReady` появляется в шине и проходит post-filter подписки. Unity-приложение подписывается на этот канал и визуализирует сигналы как ему угодно — карточки, поток, карта мира, что угодно. Поскольку ты Unity-разработчик, эта морда — реальный продакшен-сценарий: можно сделать «дашборд мониторинга» с гейм-эстетикой.

### 8.4 MCP-сервер для самого Claude Code

Это **самый интересный** компонент для твоего сценария разработки. Через MCP Claude Code получает доступ к собственному датасету мониторинга:

- `mcp_search_signals(project, query, k)` — гибридный поиск по уже обработанным сигналам;
- `mcp_get_project_state(project)` — текущий статус, бюджет, последние ошибки;
- `mcp_create_project(yaml)` — создать новый проект из YAML;
- `mcp_run_scan(project, source, query_name)` — форсированный запуск скана.

Когда ты говоришь Claude Code «найди мне последние упоминания Claude Code в Дананге», он не идёт в веб — он идёт в твой MCP-сервер и достаёт уже отсканированные сигналы. Это превращает систему из «дашборда» в «персонального research-агента».

---

## 9. Поперечные: Event Bus и Logging

### 9.1 Event Bus

Реализация — Postgres LISTEN/NOTIFY. Триггер на `events`-таблице эмиттит NOTIFY с payload. Подписчики делают LISTEN. Один процесс — один subscriber. Для соло-проекта это идеально: атомарность, durability, простота отладки (события видны как обычные строки в БД).

```python
# bus/postgres_bus.py
class PostgresEventBus(IEventBus):
    async def publish(self, event: DomainEvent) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO events (id, type, payload, project_id) VALUES ($1,$2,$3,$4)",
                event.event_id, event.type, event.model_dump_json(), event.project_id
            )
            # триггер сам сделает NOTIFY

    async def subscribe(self, event_type: str, handler):
        async with self.pool.acquire() as conn:
            await conn.add_listener("domain_events", self._make_callback(event_type, handler))
```

Если когда-то понадобится горизонтальное масштабирование с несколькими воркерами — переезжаешь на Redis Streams или NATS, не меняя контракт `IEventBus`.

### 9.2 Logging и наблюдаемость

Структурный логгинг через `structlog`. Каждое событие, каждый scan, каждая стадия pipeline — JSON-строка с `project_id`, `scan_id`, `mention_id`, `stage_name`. OpenTelemetry-трейсы между API → orchestrator → pipeline → notifications. Метрики в Prometheus формате: `mentions_processed_total{project,stage}`, `pipeline_duration_seconds{stage}`, `budget_used_usd{project,source}`.

---

## 10. Файловая структура проекта

```
monitoring/
├── CLAUDE.md                   # верхнеуровневая навигация для агентов
├── ARCHITECTURE.md             # этот документ
├── pyproject.toml
├── docker-compose.yml          # postgres + (опц.) prometheus + grafana
├── core/
│   ├── CLAUDE.md               # «здесь только контракты, никакой логики»
│   ├── models.py
│   ├── contracts.py
│   └── events.py
├── storage/
│   ├── CLAUDE.md
│   ├── schema.sql
│   ├── migrations/
│   ├── repositories.py
│   └── embedding_index.py
├── plugins/
│   ├── sources/
│   │   ├── CLAUDE.md           # «как написать новый источник»
│   │   ├── _base.py
│   │   ├── reddit.py
│   │   ├── bluesky.py
│   │   └── ...
│   └── notifications/
│       ├── CLAUDE.md
│       ├── _base.py
│       ├── telegram.py
│       └── ...
├── orchestration/
│   ├── CLAUDE.md
│   ├── scheduler.py
│   ├── budget_guard.py
│   └── dispatcher.py
├── processing/
│   ├── CLAUDE.md
│   ├── pipeline.py
│   └── stages/
│       ├── normalize.py
│       ├── dedup.py
│       ├── keyword_filter.py
│       ├── embedding.py
│       ├── semantic_filter.py
│       ├── llm_classify.py
│       ├── rank.py
│       └── decide.py
├── config/
│   ├── CLAUDE.md
│   ├── store.py
│   ├── projects/
│   │   └── ar-mat-monitor.yaml
│   └── topics/
│       └── ar_smart_fitness.yaml
├── api/
│   ├── CLAUDE.md
│   ├── main.py
│   ├── auth.py
│   ├── routes/
│   ├── ws.py
│   └── mcp_server.py
├── bus/
│   ├── CLAUDE.md
│   └── postgres_bus.py
└── tests/
    ├── unit/
    ├── integration/            # с реальной Postgres в docker
    └── fixtures/
```

Каждая папка имеет свой `CLAUDE.md` — короткий контекст-документ для агентов, который объясняет: что это за слой, какие контракты он реализует, чего нельзя делать (например, в `processing/` — «не делай прямые SQL-запросы, используй IRepository»).

---

## 11. Порядок реализации (для Claude Code)

Дисциплина «снизу вверх», месяц по выходным:

| Неделя | Что строить | Критерий готовности |
|---|---|---|
| 1 | `core/` (DTO, контракты), `storage/schema.sql`, миграции, `repositories.py` | unit-тесты на bulk_insert, existing_hashes, last_scanned_at проходят |
| 2 | `bus/postgres_bus.py`, первый `plugins/sources/reddit.py`, минимальный `dispatcher.py` | можно вручную запустить скан Reddit и увидеть RawMention в БД |
| 3 | Pipeline-каркас, стадии Normalize/Dedup/KeywordFilter | сырой ментион проходит pipeline и становится Signal без LLM |
| 4 | EmbeddingStage (Voyage), pgvector-индекс, SemanticFilterStage | гибридный поиск работает, метрики recall измеримы |
| 5 | LLMClassifyStage с Claude Haiku Batch, RankStage, DecideStage | первый проект полностью обработан end-to-end |
| 6 | `config/store.py`, YAML-loader, валидация, второй проект из YAML | можно создать новый проект без правки кода |
| 7 | `orchestration/scheduler.py` + `budget_guard.py` | проекты сами запускаются по cron, бюджет соблюдается |
| 8 | Notifications: telegram + webhook | сигналы летят в чат с inline-feedback |
| 9 | `api/` основные роуты + WebSocket | веб-клиент видит ленту в реальном времени |
| 10 | MCP-сервер | Claude Code сам ищет в твоей БД через инструменты |
| 11 | Добавить 5–7 источников (Bluesky, Telegram, RSSHub, Brave, Exa) | покрытие по проектам ≥ 80% |
| 12 | Observability, healthchecks, retention jobs | система работает 7 дней без вмешательства |

На каждой неделе — один Claude Code агент с одним CLAUDE.md и чёткими граничными условиями. Контракты не меняются — это исключает каскадные правки.

---

## 12. Что не вошло, но стоит знать

**Multi-tenancy** — изолированно для каждого `project_id` в БД, на уровне строк (Row-Level Security в Postgres). Защита от случайной утечки данных между проектами, если в будущем захочешь подключать заказчиков.

**Versioning контрактов и схем** — все DTO версионируются (`Mention.v1`, `Signal.v2`). Pipeline хранит версию в `pipeline_trace`, что позволяет переобработать старые ментионы новыми стадиями без потери истории.

**Backfill** — отдельный CLI-скрипт `python -m monitoring backfill --project=X --source=Y --since=2026-01-01`. Оrchestrator знает, как делать historical-скан, если у источника `supports_historical=True`.

**Cost-prediction** — `estimate_cost` каждого источника плюс модель `predicted_processing_cost(n_mentions, pipeline)` дают прогноз стоимости проекта на месяц **до** его запуска. Это спасает от сюрпризов.

**Замена компонентов** — поскольку всё через Protocol, легко вырезать pgvector и поставить Qdrant (`storage/qdrant_index.py implements EmbeddingIndex`). Или заменить Postgres LISTEN/NOTIFY на NATS. Или Voyage на BGE-M3 self-hosted. Контракты не двинутся.

---

## Заключение

Главный архитектурный сдвиг по сравнению с твоим наброском: **добавлены два слоя (Orchestration и Notifications) и два поперечных компонента (Domain Core и Event Bus)**. Без них пять твоих слоёв не образуют связного целого: некому решать, когда сканировать, и негде определять «общий язык».

Главный технологический выбор: **один Postgres правит всем** — данные, эмбеддинги, BM25, очереди, шина событий, locks. Это сознательная ставка на простоту эксплуатации соло-проекта. Перейти на распределённый стек можно за неделю в любой момент, не переписывая контракты.

Главный приём для разработки через Claude Code: **каждая папка = отдельный bounded context с собственным CLAUDE.md**. Один агент работает с одним слоем, контракты в `core/` неизменны, событийная модель изолирует слои друг от друга. Это даёт практически линейную скорость разработки и почти нулевой риск каскадных регрессий.

И последнее — про твою специфику. Поскольку ты Unity-разработчик с глубокой ECS/DOTS-интуицией, эта архитектура должна тебе зайти: контракты как `IComponent`, события как `IEvent`, plug-and-play модули как системы в World. Только вместо Burst-jobs у нас asyncio. А вместо Entity — `Project`, бегущий через слои.
