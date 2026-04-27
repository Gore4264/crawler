# Sources — Plugin-источники данных

Слой плагинов-источников системы `crawler`. Каждый источник реализует контракт `ISource`
(или `IStreamingSource`) из `core/contracts.py` и упаковывается в отдельный файл.
В scope **E1 / Ветка 2**: только `_base.py` (каркас `BaseSource`) и `reddit.py` (PRAW-обёртка).
Все остальные источники (`bluesky.py`, `rsshub.py`, `x_via_apify.py` и т.д.) — начиная с E3.

**Этот документ — техническое задание для агента-исполнителя E1 / Ветка 2.** После него
файлы `plugins/sources/_base.py` и `plugins/sources/reddit.py` пишутся без архитектурных
вопросов.

## Дисциплина импортов

`plugins/sources/` импортирует: `core/` (контракты и модели), `stdlib`, `praw`, `httpx`,
`aiolimiter`, `tenacity` (или кастомный retry). Не импортирует: `storage/`, `processing/`,
`api/`, `bus/`, `orchestration/`. Источник не знает о pipeline — он только производит
`RawMention`.

## Mapping разделов на файлы

| Раздел документа | Файл (создаётся в E1 / Ветка 2) |
|---|---|
| A. Структура папки, регистрация | `plugins/sources/_registry.py` (E1 — минимальный) |
| B. BaseSource каркас | `plugins/sources/_base.py` |
| C. RedditSource | `plugins/sources/reddit.py` |
| D. «Не делать» | политика, без файла |
| E. ADR-trail + связь с core | политика, без файла |
| F. Открытые вопросы | политика, без файла |

---

## ADR-trail

Этот документ материализует ADR, затрагивающие слой Sources:

- **ADR-0002** (`ADR/0002-third-source-bluesky-telegram-deferred.md`) — Phase 0 поддерживает
  streaming-источники (Bluesky firehose, E3). `BaseSource` обязан быть совместим с
  `IStreamingSource(ISource)` иерархией. `supports_streaming` — флаг в `SourceCapabilities`.
  Reddit (`supports_streaming=False`) — REST-pull, E1. Bluesky — E3.
- **ADR-0004** (`ADR/0004-content-hash-text-only.md`) — источник не входит в `content_hash`.
  Поле `source_id` в `RawMention` — только для атрибуции и индексации; дедуп по тексту.

---

## Инварианты

1. **`source_id` в `RawMention` совпадает с `ISource.id` плагина.** Это инвариант,
   обеспечиваемый в методе `search()` при `yield`. `BaseSource` форсирует это автоматически
   через атрибут `id` и инъекцию в `RawMention`.
2. **Источник stateless кроме client-сессий.** `since_cursor` приходит снаружи через
   `SourceQuery.since_cursor`. Состояние сессии HTTP/PRAW держится в атрибутах
   инстанса (`_client`, `_praw`), но не персистируется. Перезапуск = новая сессия.
3. **`estimate_cost()` — синхронный, без сетевых вызовов.** Только вычисление по метаданным
   запроса. Никаких I/O внутри.
4. **Source не пишет в `usage_log`.** Это ответственность dispatcher-а (E4). В E1 — CLI-обвязки.
5. **Source не держит состояния `since_cursor` внутри себя.** Cursor — внешнее состояние,
   хранится в `scan_log` и передаётся через `SourceQuery.since_cursor` при следующем вызове.

---

## A. Структура папки и регистрация плагинов

### A.1. Структура `plugins/sources/` для E1

```
plugins/sources/
├── CLAUDE.md              # этот файл
├── __init__.py            # экспортирует SOURCE_REGISTRY
├── _base.py               # BaseSource[ConfigT] + BaseStreamingSource stub
├── _registry.py           # SOURCE_REGISTRY dict + функция регистрации
└── reddit.py              # RedditSource(BaseSource[RedditConfig])
```

**Что НЕ создаётся в E1:**
`bluesky.py`, `rsshub.py`, `telegram_public.py`, `x_via_apify.py` — начиная с E3.
`_health_manager.py` — E4 (circuit breaker).

### A.2. Механизм регистрации: inline import в `__init__.py`

**Решение: inline imports в `plugins/sources/__init__.py` с явным `SOURCE_REGISTRY`.**

Обоснование выбора среди трёх вариантов из ARCHITECTURE 3.2:

| Вариант | Когда уместен |
|---|---|
| `entry_points` в `pyproject.toml` | Если Sources — отдельные pip-пакеты; overkill для соло-проекта |
| Dynamic loading из папки (glob `*.py`) | Удобно при ≥5 источниках; скрытые зависимости, сложнее debug |
| **Inline imports (выбрано)** | Явно, читаемо, достаточно для ≤5 источников в Phase 0 |

В Phase 0 у нас 3 источника (Reddit, RSSHub, Bluesky). Inline-imports делают зависимости видимыми
статически (pyright, grep). Переход к dynamic loading — additive рефакторинг, если источников
станет ≥10 (Phase 1+).

```python
# plugins/sources/__init__.py
from plugins.sources._registry import SOURCE_REGISTRY
from plugins.sources.reddit import RedditSource

SOURCE_REGISTRY["reddit"] = RedditSource
```

### A.3. Глобальный реестр

```python
# plugins/sources/_registry.py
from typing import type
from core.contracts import ISource

SOURCE_REGISTRY: dict[str, type[ISource]] = {}
```

`SOURCE_REGISTRY` — словарь `{name: class}`. Ключ = `ISource.id` = `RawMention.source_id`.
Наполнение — при импорте `plugins.sources` (через `__init__.py`). Dispatcher (E4) читает
реестр для поиска нужного плагина по `source_id` из `Project.sources`. В E1 CLI вызывает
source напрямую, минуя реестр.

### A.4. Конвенция имён

- Класс: `RedditSource(BaseSource[RedditConfig])`
- Атрибут класса: `id: ClassVar[str] = "reddit"`
- `RawMention.source_id` при yield: `self.id` (берётся из класса)
- Config-класс: `RedditConfig(BaseModel)` — живёт в том же файле `reddit.py`
- `SourceCapabilities` — атрибут класса `capabilities: ClassVar[SourceCapabilities]`

---

## B. `BaseSource` — каркас для всех источников

### B.1. Generic-параметризация

```python
# plugins/sources/_base.py
from typing import Generic, TypeVar, ClassVar, AsyncIterator
from core.contracts import ISource, IStreamingSource, SourceCapabilities, CostEstimate, SourceQuery
from core.models import RawMention
import asyncio
import httpx

ConfigT = TypeVar("ConfigT")

class BaseSource(Generic[ConfigT]):
    """
    Базовый класс для всех REST-pull источников.
    Наследник обязан определить:
      - id: ClassVar[str]          — уникальное имя плагина
      - capabilities: ClassVar[SourceCapabilities]
      - __init__(self, config: ConfigT) -> None
      - search(self, q: SourceQuery) -> AsyncIterator[RawMention]  — async generator
      - health_check(self) -> bool
      - estimate_cost(self, q: SourceQuery) -> CostEstimate
    BaseSource предоставляет:
      - _limiter: AsyncLimiter     — rate limiter (инициализируется в __init__)
      - _client: httpx.AsyncClient — shared HTTP-сессия (инициализируется в __init__)
      - _retry: _RetryPolicy       — экспоненциальный backoff
    """
    id: ClassVar[str]
    capabilities: ClassVar[SourceCapabilities]
```

**Протокол соответствия `ISource`:** `BaseSource` не наследует `ISource` Protocol
формально (Protocol — для статической типизации, не для наследования). Но `BaseSource`
обеспечивает все требуемые атрибуты и методы, поэтому `isinstance` через Protocol
(при `runtime_checkable=True`) и статические проверки pyright проходят без явного
наследования. Конкретные субклассы (`RedditSource`) явно типизируются как `ISource`
через аннотации в dispatcher-е.

### B.2. Lifecycle

**Решение: `BaseSource` → `BaseStreamingSource(BaseSource)` — два класса, разделение иерархии.**

Обоснование: это прямое отражение уже зафиксированного в `core/contracts.py` разделения
`ISource` / `IStreamingSource(ISource)` (ADR-0002). `BaseSource` = реализация `ISource`;
`BaseStreamingSource` = реализация `IStreamingSource`. Один класс с conditional-методами
(`if self.capabilities.supports_streaming`) — плохая практика (нарушает Liskov, усложняет
тесты, пустые методы как технологический долг).

**REST-pull (E1 — `BaseSource`):**

```python
class BaseSource(Generic[ConfigT]):
    def __init__(self, config: ConfigT) -> None:
        self._config = config
        # HTTP-клиент для httpx-based источников (RSSHub, Bluesky HTTP API и др.)
        # Reddit использует PRAW, но _client доступен для общих нужд
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
        from aiolimiter import AsyncLimiter
        # rate_limit_per_minute берётся из capabilities подкласса
        rpm = getattr(self.capabilities, "_rate_limit_per_minute", 60)
        self._limiter = AsyncLimiter(max_rate=rpm, time_period=60)

    async def close(self) -> None:
        """Закрыть HTTP-клиент. Вызывается при shutdown."""
        await self._client.aclose()

    # Абстрактные методы — наследник ОБЯЗАН переопределить
    async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        raise NotImplementedError

    def estimate_cost(self, q: SourceQuery) -> CostEstimate:
        raise NotImplementedError
```

**Streaming (E3 — `BaseStreamingSource`):**

```python
class BaseStreamingSource(BaseSource[ConfigT]):
    """
    Базовый класс для long-lived streaming источников (Bluesky firehose и др.).
    Наследник обязан реализовать _connect() и _disconnect().
    BaseStreamingSource предоставляет lifecycle: start/stop + async context manager.
    """
    def __init__(self, config: ConfigT) -> None:
        super().__init__(config)
        self._buffer: asyncio.Queue[RawMention] = asyncio.Queue(maxsize=10_000)
        self._running: bool = False

    async def start(self) -> None:
        """Установить long-lived соединение, начать буферизацию входящих сообщений."""
        self._running = True
        await self._connect()

    async def stop(self) -> None:
        """Закрыть соединение, дренировать буфер."""
        self._running = False
        await self._disconnect()

    async def __aenter__(self) -> "BaseStreamingSource":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]:
        """
        Для streaming-источника: отдать всё накопленное в буфере с since_cursor.
        Итерация завершается когда буфер пуст — не блокирует бесконечно.
        """
        while not self._buffer.empty():
            yield await self._buffer.get()

    # Абстрактные lifecycle-методы
    async def _connect(self) -> None:
        raise NotImplementedError

    async def _disconnect(self) -> None:
        raise NotImplementedError
```

**Совместимость с E3 (критический инвариант):** Добавление `BlueskySource(BaseStreamingSource)`
в E3 не требует правок в `BaseSource`. Иерархия:
```
BaseSource[ConfigT]
    └── BaseStreamingSource[ConfigT]
            └── BlueskySource(BaseStreamingSource[BlueskyConfig])   ← E3
    └── RedditSource(BaseSource[RedditConfig])                       ← E1
    └── RSSHubSource(BaseSource[RSSHubConfig])                       ← E3
```
Оркестратор (E4) проверяет `capabilities.supports_streaming` для выбора пути:
- `False` → прямой `source.search(q)` pull.
- `True` → `async with source: ...` lifecycle + тот же `source.search(q)` для drain.

### B.3. HTTP-сессия

**Решение: shared `httpx.AsyncClient` в `BaseSource.__init__`.**

- Один клиент per source-инстанс (не глобальный). Это изолирует тайм-ауты и сессии между источниками.
- PRAW (`reddit.py`) — синхронный, не использует `_client` напрямую. Reddit-специфичный
  HTTP идёт через PRAW. Для других httpx-based источников `_client` переиспользуется.
- Тайм-ауты: `connect=5s`, `read=30s`. Reddit API бывает медленным.
- Закрытие через `close()` / `__aexit__` в dispatcher-е после завершения скана (E4). В E1 —
  вызывается вручную в CLI.

### B.4. Rate Limiting

**Решение: `aiolimiter.AsyncLimiter` как атрибут `_limiter` на инстансе.**

```python
# В _base.py: rate_limit настраивается через SourceCapabilities
# Архитектурное соглашение: подкласс добавляет _rate_limit_per_minute в capabilities
# или переопределяет __init__ для кастомной инициализации limiter

# Паттерн использования в search():
async with self._limiter:
    # выполнить API-вызов
    result = await asyncio.to_thread(praw_call)
```

Стратегия:
- `AsyncLimiter(max_rate=rpm, time_period=60)` — token bucket.
- Reddit: 60 req/min (OAuth). `max_rate=60`.
- Bluesky firehose (E3): без лимита с клиентской стороны, но с back-pressure через
  `asyncio.Queue(maxsize=10_000)` в `BaseStreamingSource`.

`aiolimiter` — явная зависимость в `pyproject.toml` (`aiolimiter>=1.1`).

### B.5. Retry Policy

**Решение: встроенный exponential backoff через `tenacity` внутри одного `search()`-вызова.**

Стратегия:
- Retry-able: `5xx` HTTP-ошибки, `429 Too Many Requests`, `ConnectionError`, `TimeoutError`.
- Fail-fast (не retry): `4xx` кроме 429 (auth/not found — retry бессмыслен), `praw.exceptions.PRAWException` с кодами авторизации.
- Exponential backoff: `wait=exponential(multiplier=1, min=1, max=60)`, `stop=stop_after_attempt(3)`.

```python
# В _base.py — декоратор _with_retry для оборачивания network-вызовов
# Конкретная реализация в _base.py как helper-функция или через tenacity.retry

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

def _is_retryable(exc: BaseException) -> bool:
    """True если исключение → retry, False если → fail-fast."""
    import httpx
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    return False
```

**НЕ реализовывать circuit-breaker** — это E4 (`orchestration/retry_policy.py`).
В E1: только 3 попытки с backoff внутри одного `search()`-вызова.

### B.6. Cost Tracking

**Паттерн: Source предоставляет данные, dispatcher записывает.**

```python
# BaseSource — заглушка
def estimate_cost(self, q: SourceQuery) -> CostEstimate:
    raise NotImplementedError(
        f"{self.__class__.__name__} must implement estimate_cost()"
    )
```

Поток cost-data:
1. `dispatcher.estimate_cost(source, q)` → `CostEstimate` (до вызова).
2. `source.search(q)` возвращает `RawMention`-ы. Стоимость определяется конфигурацией источника
   (бесплатный Reddit: `cost_usd=0.0`; Apify-источники: `cost_usd = pages × rate`).
3. После исчерпания итератора dispatcher вызывает `repository.append_usage(...)`.
4. Источник **не пишет в БД** — это нарушало бы границу ответственности.

В E1 Reddit бесплатный: `estimate_cost()` возвращает `CostEstimate(expected_results=q.limit,
expected_cost_usd=Decimal("0"), confidence="exact")`.

### B.7. Error Handling и DomainEvent mapping

**Решение: Source `raise`-ит исключения, dispatcher их ловит и эмиттит DomainEvent.**

Обоснование выбора (альтернатива — buffer-паттерн):
- `raise` — идиоматичен в Python. Caller всегда знает, что произошло.
- Buffer-паттерн (Source ловит сам, пишет в внутренний buffer, dispatcher читает после) —
  усложняет интерфейс (`source.errors` property? `drain_errors()`?) и нарушает async-итератор
  семантику.
- **Компромисс для частичного сбоя:** Source может `yield` ментионы, которые успел получить
  до ошибки, а потом `raise`. Dispatcher ловит исключение в `async for` обёртке:

```python
# Паттерн в dispatcher-е (E4) / CLI (E1):
fetched_mentions = []
try:
    async for mention in source.search(q):
        fetched_mentions.append(mention)
except SomeNetworkError as exc:
    # частичный сбой: fetched_mentions содержит то что успели
    await bus.publish(ScanFailed(
        scan_id=scan_id, source_id=source.id,
        error=str(exc), error_class=type(exc).__name__,
        ...
    ))
    status = "partial" if fetched_mentions else "failed"
```

Таким образом: 50 ментионов отдали → API упал → pipeline видит 50 ментионов, статус скана `"partial"`.

Source-ы объявляют собственные исключения:

```python
# В _base.py
class SourceError(Exception):
    """Базовое исключение источника данных."""

class SourceAuthError(SourceError):
    """Ошибка аутентификации — fail-fast, не retry."""

class SourceRateLimitError(SourceError):
    """Rate limit исчерпан — retry с backoff."""

class SourceFetchError(SourceError):
    """Ошибка сетевого запроса — retry."""
```

### B.8. Capabilities-driven диспетчеризация

**Паттерн: Source — единственное место, где описываются capabilities.**

```python
# В reddit.py
class RedditSource(BaseSource[RedditConfig]):
    id: ClassVar[str] = "reddit"
    capabilities: ClassVar[SourceCapabilities] = SourceCapabilities(
        supports_keywords=True,
        supports_semantic=False,
        supports_geo=False,
        supports_language_filter=False,
        supports_search=True,
        supports_streaming=False,   # ADR-0002: Reddit — REST-pull
        supports_historical=True,
        cost_model="free",
        typical_latency_ms=2000,
    )
```

Dispatcher (E4) только читает `source.capabilities` — никакой логики «если source_id == reddit».
В E1 CLI-обвязка также должна читать capabilities, а не hardcode паттерн.

---

## C. `RedditSource` — конкретная реализация

### C.1. Конфигурация

```python
# В reddit.py
from pydantic import BaseModel, Field, SecretStr

class RedditConfig(BaseModel):
    """Конфиг для Reddit-источника. Загружается из YAML/env в E2c."""
    client_id: str
    client_secret: SecretStr
    user_agent: str = "crawler/0.1 by crawler_bot"
    subreddits: list[str] = Field(default_factory=lambda: ["ClaudeAI"])
    # Параметры поиска по умолчанию
    default_sort: str = "new"       # new | hot | top | relevance
    default_limit: int = 100        # per subreddit per search call
```

Поля `client_id` / `client_secret` — OAuth Client Credentials (не Personal Use Script).
`user_agent` — обязателен по политике Reddit API; должен содержать версию и идентификатор.
`subreddits` — список subreddit-ов для поиска. По решению D7 тестовый = `["ClaudeAI"]`.

### C.2. Инициализация PRAW

```python
class RedditSource(BaseSource[RedditConfig]):
    def __init__(self, config: RedditConfig) -> None:
        super().__init__(config)
        import praw
        self._praw = praw.Reddit(
            client_id=config.client_id,
            client_secret=config.client_secret.get_secret_value(),
            user_agent=config.user_agent,
        )
```

**Async vs Sync PRAW:** используем `praw` (синхронный) + `asyncio.to_thread()`.

Обоснование: `asyncpraw` существует, но имеет особенности — его API не идентичен `praw`
(например, `async for` вместо `for` в некоторых итераторах, вариативные async/sync методы).
`asyncio.to_thread()` — стандартный Python 3.9+ механизм для выноса блокирующего I/O
в threadpool без зависимости от дополнительной библиотеки. Это меньше риска несовместимости.

Открытый вопрос F.1: если `asyncpraw` окажется предпочтительнее по другим причинам —
решение продукт-агента до старта executor-сессии.

### C.3. Метод `search()` — маппинг `SourceQuery` → PRAW

```python
async def search(self, q: SourceQuery) -> AsyncIterator[RawMention]:
    """
    Для каждого subreddit в self._config.subreddits:
      - вызвать subreddit.search(query, sort=sort, limit=limit, params={after: cursor})
      - маппить каждый praw.models.Submission → RawMention
      - yield RawMention
    """
    from datetime import timezone
    import datetime

    sort = q.keywords[0] if not q.keywords else "new"  # sort strategy
    keyword = " ".join(q.keywords) if q.keywords else ""
    limit = q.limit  # из SourceQuery.limit (default=100)

    # since_cursor — PRAW pagination через after=t3_xxx
    after = q.since_cursor  # None или "t3_xxx"

    for subreddit_name in self._config.subreddits:
        async with self._limiter:
            subreddit = await asyncio.to_thread(
                lambda: self._praw.subreddit(subreddit_name)
            )
            submissions = await asyncio.to_thread(
                lambda: list(
                    subreddit.search(
                        keyword,
                        sort="new",
                        limit=limit,
                        params={"after": after} if after else {},
                    )
                )
            )

        for submission in submissions:
            yield self._map_submission(submission)
```

**Маппинг `SourceQuery` → PRAW-параметры:**

| `SourceQuery` поле | PRAW-параметр | Примечание |
|---|---|---|
| `keywords[0..n]` | `query = " ".join(keywords)` | OR-семантика через пробел — Reddit-поиск OR по умолчанию |
| `limit` | `limit=q.limit` | max 100 per call по Reddit API |
| `since_cursor` | `params={"after": q.since_cursor}` | пагинация через fullname |
| `mode` | игнорируется | Reddit — всегда search/REST |
| `since` / `until` | не поддерживаются нативно | post-фильтрация по `published_at` — вне Source |
| `languages` | не поддерживается | post-фильтрация — вне Source |
| `geo` | не поддерживается | `SourceCapabilities.supports_geo=False` |

### C.4. Маппинг `praw.models.Submission` → `RawMention`

```python
from datetime import datetime, timezone
from pydantic import HttpUrl

def _map_submission(self, submission) -> RawMention:
    """Маппинг PRAW Submission → RawMention."""
    from core.models import RawMention
    import datetime

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    return RawMention(
        source_id=self.id,                                          # "reddit"
        external_id=submission.fullname,                            # "t3_abc123" — fullname
        author=str(submission.author) if submission.author else None,
        author_id=str(submission.author) if submission.author else None,
        text=submission.selftext or submission.title,               # text поста; если нет — title
        text_html=submission.selftext_html or None,                 # HTML-версия (если есть)
        url=HttpUrl(f"https://www.reddit.com{submission.permalink}"),
        lang_hint=None,                                             # Reddit не даёт язык
        engagement={
            "score": submission.score,
            "num_comments": submission.num_comments,
            "upvote_ratio": int(submission.upvote_ratio * 100),     # хранить как int (%)
        },
        raw={
            "id": submission.id,
            "fullname": submission.fullname,
            "subreddit": submission.subreddit.display_name,
            "is_self": submission.is_self,
            "over_18": submission.over_18,
            "flair": submission.link_flair_text,
        },
        published_at=datetime.datetime.fromtimestamp(
            submission.created_utc, tz=datetime.timezone.utc
        ),
        discovered_at=now_utc,
        fetched_at=now_utc,
    )
```

**Ключевые решения маппинга:**

- **`external_id = submission.fullname` (`t3_xxx`)** — не `submission.id` (`xxx`).
  Обоснование: `fullname` — полный глобальный идентификатор Reddit. При использовании
  `after=t3_xxx` для пагинации нужен именно fullname. Если использовать короткий `id`
  (`xxx`), нужно добавлять prefix `t3_` при каждом пагинационном вызове.
  Fullname — самодостаточный, однозначный, используется в PRAW API напрямую.

- **`text = submission.selftext or submission.title`** — для link-постов (`selftext == ""`)
  текста нет, берём title. Это минимально-валидный text для pipeline.
  Более богатый текст (body link-поста) — только через scraping, вне scope E1.

- **`author` и `author_id`** — оба = `str(submission.author)`.
  Reddit не разделяет display name и user ID на уровне `praw.models.Submission`.
  `submission.author` — объект `praw.models.Redditor`; `str(author)` = username.
  `author_id` = тот же username (не числовой ID). Если нужен числовой Reddit user ID —
  требует отдельного API-вызова (`submission.author.id`), дорого для батча. Отложено Phase 1+.

- **`engagement`** — score, num_comments, upvote_ratio (как int %). Не добавляем `title`
  (это не engagement-метрика). `upvote_ratio` — float в PRAW; конвертируем в int % для
  однородного `dict[str, int]` контракта из `RawMention`.

- **`url`** — полный URL: `https://www.reddit.com` + `submission.permalink`.
  `submission.permalink` = `/r/ClaudeAI/comments/xxx/title/` без хоста.
  `HttpUrl` валидирует формат.

### C.5. Пагинация: `since_cursor`

Reddit пагинация — через параметр `after` (fullname последнего просмотренного поста).

```
since_cursor формат: "t3_abc123"  (fullname Reddit-поста)
```

Поток cursor-а:
1. Первый вызов: `q.since_cursor = None` → `params = {}` → PRAW вернёт самые свежие посты.
2. После scan: внешний код (dispatcher/CLI) сохраняет `since_cursor = last_submission.fullname`.
3. Следующий вызов: `q.since_cursor = "t3_abc123"` → `params = {"after": "t3_abc123"}`.

**Source не обновляет cursor сам** — он stateless. Сохранение cursor — ответственность
вызывающего кода через `repository.record_scan` (там хранятся мета-данные скана,
но не cursor явно). В E1 CLI-обвязка может сохранять cursor в `scan_log` как дополнительные
метаданные или в `config/bootstrap.py`.

Открытый вопрос F.2: явное поле `last_cursor` в `scan_log` vs хранение в `raw` JSONB —
решение до E1 интеграции.

### C.6. `health_check()`

```python
async def health_check(self) -> bool:
    """
    GET /api/v1/me — Reddit API endpoint, возвращает info об авторизованном пользователе.
    Если OAuth credentials валидны → 200 → True.
    Любое исключение / не-200 → False.
    """
    try:
        me = await asyncio.to_thread(lambda: self._praw.user.me())
        return me is not None
    except Exception:
        return False
```

### C.7. `estimate_cost()`

```python
def estimate_cost(self, q: SourceQuery) -> CostEstimate:
    return CostEstimate(
        expected_results=q.limit,
        expected_cost_usd=Decimal("0"),
        confidence="exact",    # Reddit API бесплатный
    )
```

Reddit OAuth — бесплатный в Phase 0. Potential rate-limit превышение не стоит денег.

### C.8. Что НЕ делать в `RedditSource`

- **НЕ** подписываться на comment-stream (`subreddit.stream.comments()`) — это streaming,
  не REST-pull. Паттерн для E3 / streaming-источника.
- **НЕ** делать multireddit search через несколько API-вызовов параллельно — это задача
  dispatcher-а (E4). Source итерирует subreddits последовательно.
- **НЕ** фильтровать по `is_self`, `is_video`, `over_18` — это работа `KeywordFilterStage`
  или будущей стадии media-фильтра.
- **НЕ** сохранять `since_cursor` внутри инстанса — Source stateless.
- **НЕ** писать в `usage_log` — это задача dispatcher-а/CLI-обвязки.

---

## D. Что НЕ делать в E1 / Ветка 2 (Source)

| Не создавать | Когда появится |
|---|---|
| `bluesky.py` | E3 |
| `rsshub.py` | E3 |
| `telegram_public.py` | Phase 1+ (ADR-0002) |
| `x_via_apify.py` | E3+ |
| `BaseStreamingSource` имплементацию (только stub) | E3 (при добавлении `bluesky.py`) |
| circuit-breaker в `BaseSource` | E4 (`orchestration/retry_policy.py`) |
| parallel-fetch нескольких источников | E4 (`dispatcher`) |
| запись в `usage_log` из Source | E4 dispatcher / E1 CLI-обвязка |
| proxy/VPN-роутинг | Phase 1+ |
| Comments-stream из Reddit | E3+ (streaming) |

---

## E. Связь с `core/` и ADR

### E.1. Методы ISource — что делает Reddit, что BaseSource

| Метод `ISource` | `BaseSource` | `RedditSource` |
|---|---|---|
| `search(q)` | `raise NotImplementedError` | Реализован: PRAW.subreddit.search → yield RawMention |
| `health_check()` | `raise NotImplementedError` | GET `/api/v1/me` через PRAW |
| `estimate_cost(q)` | `raise NotImplementedError` | `CostEstimate(0, "exact")` |

Атрибуты:

| Атрибут `ISource` | `BaseSource` | `RedditSource` |
|---|---|---|
| `id: str` | ClassVar — обязан определить подкласс | `"reddit"` |
| `capabilities: SourceCapabilities` | ClassVar — обязан определить подкласс | Определён |

### E.2. DomainEvent-ы, связанные с Sources

Source сам события **не эмиттит** — это задача dispatcher-а (E4). Но Source влияет на:
- `MentionsFetched` — эмиттится dispatcher-ом после каждого батча от source.
- `ScanFinished(status='partial')` — если `search()` вырвался с исключением после partial yield.
- `ScanFailed` — если `search()` вырвался с исключением до первого yield.
- `SourceHealthChanged` — после `health_check()`.

В E1 CLI-обвязка сама решает, что делать при исключении из source.

### E.3. `RawMention.source_id` инвариант

`BaseSource` форсирует инвариант через генерацию `RawMention`:
```python
# В _map_submission (RedditSource) — явный self.id:
return RawMention(
    source_id=self.id,   # "reddit" — значение из ClassVar
    ...
)
```
Если наследник использует другое значение в `source_id` — это бесшумная ошибка, которую
выловит интеграционный тест E1. Рекомендация: в `BaseSource` добавить helper
`_make_raw_mention(**kwargs) -> RawMention` который форсирует `source_id=self.id`.

---

## F. Открытые вопросы продукт-агенту

### F.1. `praw` vs `asyncpraw`

Архитектор выбрал `praw` + `asyncio.to_thread()`. Аргументы за `asyncpraw`:
- нативный async/await API без threadpool overhead;
- лучше интегрируется с `async for`.

Аргументы за `praw` (выбранное):
- `asyncpraw` иногда отстаёт по features от `praw` (исторически);
- `asyncio.to_thread()` — стандартный Python 3.9+ механизм, нет дополнительной зависимости;
- в Reddit-кейсе latency PRAW-вызовов (сотни мс) скрывает overhead threadpool.

**Если продукт-агент предпочитает `asyncpraw`** — решение до старта executor-сессии.
Изменение не влияет на core-контракты, только на `reddit.py` и `pyproject.toml`.

### F.2. Хранение `since_cursor` между сканами

`scan_log` не имеет explicit поля `last_cursor`. Варианты:
1. **Добавить `last_cursor TEXT` в `scan_log`** — самодостаточный, явный.
2. **Хранить в `scan_log.raw JSONB`** — если добавить raw-поле.
3. **Отдельная таблица `source_cursors(project_id, source_id, query_name, cursor)`** — чище,
   но дополнительная таблица.

В E1 CLI-обвязка может хранить cursor in-memory или передавать явно. Для полноценного
планировщика (E4) нужно решение. **Рекомендация архитектора:** вариант 3 (отдельная таблица)
— чистое разделение ответственности. Решение нужно до интеграционной сессии E1.

### F.3. Куда живёт `_RetryPolicy` helper

`tenacity` decorator — в `_base.py` как модульная функция `_with_retry()` или
как class-метод `BaseSource._retry_on_network_error(func)`? Решение влияет на то,
как тестировать retry-поведение в isolation. Рекомендация: функция в `_base.py`,
используемая как `@_with_retry` декоратор. Если окажется неудобно — refactor без
влияния на контракты.

---

## OPS

- **Type**: folder
- **Parent**: `../CLAUDE.md`
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
