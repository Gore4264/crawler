# Processing — 8-стадийный Pipeline

Слой обработки ментионов системы `crawler`. Реализует `IStage` из `core/contracts.py`
в конкретных стадиях и собирает их в `Pipeline`. В scope **E1 / Ветка 2**: каркас
`Pipeline` + `PipelineContext` + три стадии (`NormalizeStage`, `DedupStage`,
`KeywordFilterStage`) + синтетический `DecideStage`.

**Этот документ — техническое задание для агента-исполнителя E1 / Ветка 2.**
После него файлы `processing/pipeline.py`, `processing/context.py`,
`processing/_fakes.py`, `processing/stages/normalize.py`,
`processing/stages/dedup.py`, `processing/stages/keyword_filter.py`,
`processing/stages/decide.py` пишутся без архитектурных вопросов.

## Дисциплина импортов

`processing/` импортирует: `core/` (контракты, модели, события), `stdlib`,
`langdetect`, `selectolax`, `hashlib`, `re`. В E1 также: ничего из `storage/`
напрямую (только через `IRepository` Protocol из core). Не импортирует: `plugins/`,
`api/`, `bus/`, `orchestration/`.

## Mapping разделов на файлы

| Раздел документа | Файл (создаётся в E1 / Ветка 2) |
|---|---|
| A. Pipeline + PipelineContext | `processing/pipeline.py` + `processing/context.py` |
| B.1. NormalizeStage | `processing/stages/normalize.py` |
| B.2. DedupStage | `processing/stages/dedup.py` |
| B.3. KeywordFilterStage | `processing/stages/keyword_filter.py` |
| B.4. DecideStage | `processing/stages/decide.py` |
| C. FakeRepository | `processing/_fakes.py` |
| D. Параллелизм | политика, без файла |
| E. «Не делать» | политика, без файла |
| F. Открытые вопросы | политика, без файла |

---

## ADR-trail

- **ADR-0004** (`ADR/0004-content-hash-text-only.md`) — `content_hash = sha256(normalized_text)`.
  Источник (`source_id`) не входит в хеш. Следствие для `DedupStage`: проверка по
  `content_hash` глобальна, не `(source_id, content_hash)`. Cross-source дедуп —
  побочный эффект.
- **ADR-0001** (через `core/CLAUDE.md`) — embedding-размерность 1024, провайдер Voyage 3.5.
  `NormalizedMention.embedding` в Phase 0 `None` (заполняется в E2a). Поле есть в модели,
  `processing/stages/embedding.py` — E2a.
- **Инвариант из core/CLAUDE.md раздел 5** — `LLMClassifyStage` не запускается до
  `[Normalize, Dedup, KeywordFilter, SemanticFilter]`. В E1 только первые три стадии +
  синтетический Decide. В будущем `Pipeline.run()` обязан assert-ить порядок стадий
  до старта E2b.

---

## Инварианты

1. **`IStage.process()` — async, возвращает `list[NormalizedMention]` той же длины или
   меньшей.** «Меньшей» — при фильтрации (Dedup, KeywordFilter). «Той же» — при обогащении
   (Normalize, Embedding). Никогда не больше.
2. **`PipelineContext` живёт в `processing/`, не в `core/`.** Это решение, зафиксированное
   в `core/CLAUDE.md` B.11 open question #3. Обоснование — в разделе A.2 этого документа.
3. **Pipeline не пишет `NormalizedMention` в БД сам.** Запись — ответственность
   интеграционной сессии (CLI-обвязка / dispatcher). Pipeline только обрабатывает батч
   и возвращает `list[Signal]`.
4. **`structlog` используется с E1.** Каждая стадия пишет лог-запись с ключами
   `stage_name`, `items_in`, `items_out`, `duration_ms`. Метрики Prometheus — E7, но
   структурированные логи — сейчас, «потом не приделать» (ARCHITECTURE 9.2).
5. **Все datetime внутри pipeline — tz-aware UTC.** Наследуется из инварианта `core/`.

---

## A. Pipeline как chain of stages

### A.1. Класс `Pipeline`

```python
# processing/pipeline.py
from __future__ import annotations
import asyncio
import datetime
import structlog
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4
from core.contracts import IRepository, IStage
from core.models import NormalizedMention, Signal, Project, PipelineTraceEntry

if TYPE_CHECKING:
    from processing.context import PipelineContext

logger = structlog.get_logger(__name__)

class Pipeline:
    """
    Цепочка IStage-стадий. Принимает батч ментионов, прогоняет через стадии,
    возвращает список Signal.
    """
    def __init__(
        self,
        stages: list[IStage],
        repository: IRepository,
    ) -> None:
        self._stages = stages
        self._repository = repository

    async def run(
        self,
        mentions: list[NormalizedMention],
        project: Project,
        scan_id: UUID | None = None,
    ) -> list[Signal]:
        """
        Прогнать батч через все стадии.
        Returns: список Signal (финальный результат).
        """
        from processing.context import PipelineContext

        scan_id = scan_id or uuid4()
        ctx = PipelineContext(
            project=project,
            scan_id=scan_id,
            repository=self._repository,
        )

        current: list[NormalizedMention] = list(mentions)

        for stage in self._stages:
            items_in = len(current)
            started_at = datetime.datetime.now(datetime.timezone.utc)

            current = await stage.process(current, ctx)

            duration_ms = int(
                (datetime.datetime.now(datetime.timezone.utc) - started_at)
                .total_seconds() * 1000
            )
            entry = PipelineTraceEntry(
                stage_name=stage.name,
                started_at=started_at,
                duration_ms=duration_ms,
                items_in=items_in,
                items_out=len(current),
                cost_usd=Decimal("0"),   # стадии E1 бесплатны; LLM-стадии обновят это поле
            )
            ctx.trace.append(entry)

            logger.info(
                "pipeline_stage_complete",
                stage=stage.name,
                items_in=items_in,
                items_out=len(current),
                duration_ms=duration_ms,
                scan_id=str(scan_id),
                project_id=project.id,
            )

            if not current:
                logger.info(
                    "pipeline_early_exit",
                    stage=stage.name,
                    reason="all_mentions_filtered",
                    scan_id=str(scan_id),
                )
                break

        # Конвертация оставшихся NormalizedMention → Signal
        # В E1: последняя стадия (DecideStage) сама создаёт Signal-ы и возвращает
        # их в специальном контейнере. Но IStage.process возвращает NormalizedMention.
        # Решение: DecideStage возвращает пустой список NormalizedMention + складывает
        # Signal-ы в ctx.pending_signals. Pipeline берёт их оттуда.
        return ctx.pending_signals
```

**Решение: `IRepository` в конструкторе `Pipeline(stages, repository)`.**

Обоснование выбора:
- `Pipeline(stages, repository)` — DI через конструктор. Ясно, тестируемо, стандартно.
- `run(mentions, project, repository)` — repository в каждом вызове избыточен,
  repository один на lifetime Pipeline.
- DI-контейнер (overkill для E1) — усложняет для минимальной выгоды.

Для slice (до интеграции с реальным storage): `Pipeline(stages, FakeRepository())`.

### A.2. `PipelineContext` — где живёт

**Решение: `processing/context.py`, не `core/`.**

Обоснование (финальный ответ на open question #3 из `core/CLAUDE.md`):

`PipelineContext` содержит DI-handles на `IRepository` (и в будущих этапах —
`IEmbedder`, `IClassifier`). Это runtime-объект batch-а, он живёт одну обработку.
Если вынести в `core/`, то `core/` начнёт тянуть типы из `processing/`, что нарушает
правило «`core/` — единственный модуль без внешних зависимостей внутри проекта».

Формула: `IStage` в `core/` ссылается на `PipelineContext` через `TYPE_CHECKING`
(forward-ref), никакого runtime-import из `core/` в `processing/` не возникает.
Это зафиксировано в `core/CLAUDE.md` B.11 — там уже стоит `"PipelineContext"` как
string-аннотация.

**Если в Phase 1+ появится потребность в альтернативном pipeline-е** (например,
streaming-pipeline для Bluesky без batch-семантики), который тоже реализует контракт —
`PipelineContext` мигрирует в `core/`. Это additive миграция (forward-ref становится
реальным import), не breaking для стадий.

```python
# processing/context.py
from __future__ import annotations
import dataclasses
from typing import TYPE_CHECKING
from uuid import UUID
from core.contracts import IRepository
from core.models import Project, PipelineTraceEntry, Signal

@dataclasses.dataclass
class PipelineContext:
    """
    Runtime-контекст одного pipeline-прогона. Передаётся всем стадиям.
    Стадии могут читать project/repository; записывать в trace и pending_signals.
    """
    project: Project
    scan_id: UUID
    repository: IRepository
    trace: list[PipelineTraceEntry] = dataclasses.field(default_factory=list)
    pending_signals: list[Signal] = dataclasses.field(default_factory=list)
```

`dataclass` (не Pydantic) — потому что `PipelineContext` mutable (trace и pending_signals
пополняются по ходу pipeline). Pydantic frozen=True для mutable-объекта — неудобно.

**Поля `PipelineContext`:**
- `project: Project` — объект проекта с keywords, threshold, sources.
- `scan_id: UUID` — ID текущего скана (используется в трассе и в DomainEvent-ах).
- `repository: IRepository` — репозиторий; используется `DedupStage` для `existing_hashes`.
- `trace: list[PipelineTraceEntry]` — накапливается в `Pipeline.run()` после каждой стадии.
- `pending_signals: list[Signal]` — `DecideStage` кладёт сюда готовые Signal-ы.
  Pipeline собирает и возвращает.

### A.3. Как `Pipeline.run()` трассирует стадии

После каждой стадии `Pipeline.run()` добавляет `PipelineTraceEntry` в `ctx.trace`.
Поля трассы:
- `stage_name = stage.name`
- `started_at` — datetime.now(UTC) до вызова stage.process
- `duration_ms` — `(now - started_at).total_seconds() * 1000` (int)
- `items_in` — `len(current)` до стадии
- `items_out` — `len(current)` после стадии
- `cost_usd` — `Decimal("0")` для E1-стадий; LLM/embedding стадии E2a/E2b обновят.

Трасса передаётся в каждый `Signal` через `DecideStage` (который создаёт Signal
с заполненным `pipeline_trace = ctx.trace`).

### A.4. Маппинг `NormalizedMention` → `Signal` (через DecideStage)

В E1 `DecideStage` синтетически создаёт Signal для каждого выжившего ментиона:

```
signal.id             = uuid4()
signal.mention_id     = mention.id        ← id из NormalizedMention (UUID)
signal.project_id     = ctx.project.id
signal.matched_query  = ctx.project.queries[0].name  ← первая тема (в E1 одна)
signal.relevance_score = 1.0
signal.is_spam        = False
signal.intent         = "other"
signal.sentiment      = "neutral"         ← синтетически (нет LLM в E1)
signal.entities       = []
signal.topics         = []
signal.pipeline_trace = list(ctx.trace)   ← копия трассы до финала
signal.cost_usd       = Decimal("0")
signal.created_at     = datetime.now(UTC)
```

**Про `signal.mention_id`:** это UUID из `NormalizedMention.id`. В slice (до интеграции
с реальным storage) — UUID генерируется в памяти, не совпадает с `id` записи в БД.
При интеграции CLI вызывает `repository.bulk_upsert_mentions_with_dedup(mentions)` — та
возвращает только что вставленные записи, но их UUID = `mention.id` (PK генерируется
в Python при создании NormalizedMention). Таким образом `signal.mention_id` всегда
корректен — это тот же UUID, что и PK в таблице mentions.

---

## B. Стадии E1

### B.1. `NormalizeStage`

**Файл:** `processing/stages/normalize.py`

Bridge между `RawMention` и `NormalizedMention`. Реализует ровно алгоритм D из `core/CLAUDE.md`
(6 шагов по шагам). Не фильтрует — только обогащает. Output всегда той же длины что input.

#### Сигнатура

```python
class NormalizeStage:
    name: str = "normalize"

    async def process(
        self,
        mentions: list[NormalizedMention],     # принимает NormalizedMention для совместимости с IStage
        ctx: "PipelineContext",
    ) -> list[NormalizedMention]:
        ...
```

**Замечание по типам:** `IStage.process` принимает `list[NormalizedMention]`. Но `NormalizeStage`
принимает на вход фактически `RawMention`-объекты (первая в цепочке). Решение: Pipeline собирает
стадии с типом `list[NormalizedMention]` — первый шаг конвертирует `RawMention` → `NormalizedMention`
внутри `NormalizeStage`. Caller (Pipeline) передаёт список через `cast` или просто как
`list[RawMention]` без явного cast — Python duck-typing позволяет, pyright нужно аннотировать.

Альтернатива: первый вход в Pipeline — `list[RawMention]`, а не `list[NormalizedMention]`.
Но тогда `IStage.process` нужно два разных generic типа — усложнение без пользы в E1.
**Решение: Pipeline принимает `list[RawMention]` явно, приводит к `list[NormalizedMention]`
через `NormalizeStage` — первой стадией обязательно.**

#### Алгоритм (реализует `core/CLAUDE.md` D.1 ровно)

Helpers, выделенные в отдельные функции внутри `normalize.py`:

```python
def _extract_text(mention: RawMention) -> tuple[str, bool]:
    """
    Шаг 1: извлечение текста из HTML или plain text.
    Returns: (text, is_html_stripped)
    """
    if mention.text_html:
        from selectolax.parser import HTMLParser
        tree = HTMLParser(mention.text_html)
        # удалить script/style/noscript полностью
        for tag in tree.css("script, style, noscript"):
            tag.decompose()
        text = tree.body.text(separator=" ") if tree.body else ""
        return text, True
    return mention.text, False


def _strip_tracking_params(text: str) -> tuple[str, list[str]]:
    """
    Шаг 2: удаление трекинговых параметров из inline-ссылок.
    Returns: (cleaned_text, removed_params_list)
    """
    import re
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    TRACKING_PARAMS = frozenset({
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "mc_eid", "mc_cid", "igshid",
        "_hsenc", "_hsmi", "ref", "ref_src", "ref_url",
        "vero_id", "yclid", "msclkid", "twclid",
    })

    removed_all: list[str] = []

    def replace_url(match: re.Match) -> str:
        url = match.group(0)
        parts = urlsplit(url)
        params = parse_qsl(parts.query, keep_blank_values=True)
        kept, removed = [], []
        for k, v in params:
            if k.lower() in TRACKING_PARAMS:
                removed.append(k.lower())
            else:
                kept.append((k, v))
        removed_all.extend(removed)
        new_query = urlencode(kept)
        return urlunsplit(parts._replace(query=new_query))

    cleaned = re.sub(r"https?://\S+", replace_url, text)
    return cleaned, removed_all


def _compute_content_hash(text_clean: str) -> str:
    """Шаг 6: SHA-256 hex."""
    import hashlib
    return hashlib.sha256(text_clean.encode("utf-8")).hexdigest()
```

**Полный алгоритм (шаги 1–6 из `core/CLAUDE.md` D.1):**

```
1. _extract_text(mention) → (raw_text, is_html_stripped)
2. _strip_tracking_params(raw_text) → (text_no_tracking, tracking_params_removed)
3. unicodedata.normalize("NFKC", text_no_tracking) → text_nfkc
4. text_nfkc.lower() → text_lower
5. re.sub(r"\s+", " ", text_lower).strip() → text_clean
   (шаги 1–5 дают text_clean)
6. _compute_content_hash(text_clean) → content_hash
```

**Определение `lang`:**

```python
def _detect_lang(text_clean: str) -> str:
    """
    Определить язык через langdetect.
    При ошибке (мало текста, mixed-script, исключение) → "und" (ISO 639-3 undefined).
    """
    if len(text_clean.strip()) < 20:
        return "und"
    try:
        from langdetect import detect
        return detect(text_clean)
    except Exception:
        return "und"
```

`"und"` — ISO 639-3 код для "undefined/undetermined". Не пустая строка и не None —
поле `lang: str` обязательное в `NormalizedMention`.

#### Конструкция `NormalizedMention`

```python
async def process(self, mentions, ctx):
    result = []
    for mention in mentions:
        raw_text, is_html_stripped = _extract_text(mention)
        text_stripped, tracking_params_removed = _strip_tracking_params(raw_text)
        import unicodedata, re
        text_nfkc = unicodedata.normalize("NFKC", text_stripped)
        text_lower = text_nfkc.lower()
        text_clean = re.sub(r"\s+", " ", text_lower).strip()
        content_hash = _compute_content_hash(text_clean)
        lang = _detect_lang(text_clean)

        normalized = NormalizedMention(
            **mention.model_dump(),          # копируем все RawMention-поля
            text_clean=text_clean,
            lang=lang,
            content_hash=content_hash,
            is_html_stripped=is_html_stripped,
            normalize_version=1,
            tracking_params_removed=tracking_params_removed,
            # minhash_signature=None — Phase 1+
            # embedding=None — E2a
        )
        result.append(normalized)
    return result
```

**Перформанс:** sync-обработка внутри (нет await), `async def process` только для
совместимости с `IStage`. Для батча 100 ментионов — обычный Python loop без
параллелизации (regex + selectolax быстрые).

#### Тест-кейсы (должны быть в `tests/unit/test_normalize.py`)

Из `core/CLAUDE.md` D.4:
1. Cross-source identity: тот же текст от Reddit и Bluesky → одинаковый `content_hash`.
2. UTM-стрип: URL с `utm_source` и без → одинаковый `content_hash`.
3. HTML-эквивалент: `<p>Hello <b>world</b></p>` vs `Hello world` → одинаковый hash, `is_html_stripped=True`.
4. Whitespace: `Hello    world\n\n` vs `hello world` → одинаковый hash.
5. NFKC: precomposed и decomposed → одинаковый hash.

---

### B.2. `DedupStage`

**Файл:** `processing/stages/dedup.py`

SHA-256 дедупликация — без MinHash (ADR-0004 + ROADMAP E1 явно: «без MinHash в slice»).

#### Сигнатура

```python
class DedupStage:
    name: str = "dedup"

    async def process(
        self,
        mentions: list[NormalizedMention],
        ctx: "PipelineContext",
    ) -> list[NormalizedMention]:
        ...
```

#### Алгоритм

```
1. Собрать set всех content_hash из входящего батча.
2. Внутрибатчевый дедуп (inter-mention dedup):
   Если в батче два ментиона с одинаковым content_hash — оставить первый.
   Обоснование: порядок итерации детерминирован (list), first-wins согласован
   с поведением bulk_upsert_mentions_with_dedup (ON CONFLICT DO NOTHING — тоже first-wins
   по порядку INSERT). Cross-source коллизия в одном батче — edge case, но явный.
3. Вызвать ctx.repository.existing_hashes(hashes) → set[str] уже присутствующих в БД.
4. Вычесть: оставить только те mention-ы, чей content_hash НЕ в DB-set.
5. Вернуть отфильтрованный список.
```

```python
async def process(self, mentions, ctx):
    # Шаг 1: собрать хеши батча
    batch_hashes = [m.content_hash for m in mentions]

    # Шаг 2: внутрибатчевый дедуп — first-wins
    seen: set[str] = set()
    deduped_batch: list[NormalizedMention] = []
    for mention in mentions:
        if mention.content_hash not in seen:
            seen.add(mention.content_hash)
            deduped_batch.append(mention)

    # Шаг 3: запрос в БД
    existing = await ctx.repository.existing_hashes(list(seen))

    # Шаг 4: фильтрация
    result = [m for m in deduped_batch if m.content_hash not in existing]
    return result
```

**DI через `ctx.repository`** — `DedupStage` получает `IRepository` через `PipelineContext`,
не через direct inject в конструктор. Обоснование:
- `PipelineContext` уже инъектирован в Pipeline и передаётся всем стадиям.
- Инъекция repository в каждую стадию отдельно — дублирование.
- Стадии без DB-зависимости (Normalize, KeywordFilter) просто игнорируют `ctx.repository`.

**НЕ делать `bulk_upsert` в DedupStage** — только фильтрация. Запись в БД:
- Интеграция (CLI E1): после `pipeline.run()` → `repository.bulk_upsert_mentions_with_dedup(surviving_mentions)`.
- Dispatcher (E4): то же, после получения результата.
- DedupStage знает только «уже есть / нет».

**Без MinHash** — оставляем поле `minhash_signature: list[int] | None = None` в
`NormalizedMention` нетронутым (Phase 1+). `DedupStage` не заполняет его.

---

### B.3. `KeywordFilterStage`

**Файл:** `processing/stages/keyword_filter.py`

Regex-фильтрация по `project.queries[*].keywords` и `project.queries[*].excluded_keywords`.

#### Сигнатура

```python
class KeywordFilterStage:
    name: str = "keyword_filter"

    def __init__(self) -> None:
        # Compiled patterns кешируются per-project_id per-query
        # Компиляция — lazy при первом вызове с данным project
        self._compiled: dict[str, tuple[list[re.Pattern], list[re.Pattern]]] = {}

    async def process(
        self,
        mentions: list[NormalizedMention],
        ctx: "PipelineContext",
    ) -> list[NormalizedMention]:
        ...
```

#### Алгоритм

```
1. Скомпилировать regex-паттерны для project (lazy, кеш по project_id).
2. Для каждого ментиона:
   a. Применить include-паттерны к mention.text_clean.
   b. Если хотя бы один include совпал — проверить exclude-паттерны.
   c. Если ни один exclude не совпал — ментион проходит.
3. Вернуть отфильтрованный список.
```

#### Компиляция паттернов

```python
import re

def _compile_keyword(kw: str) -> re.Pattern:
    """
    Компиляция одного keyword в regex-паттерн.
    Стратегия:
    - Длинные слова (>3 символов) и одиночные слова → word-boundary: r"\bword\b"
    - Короткие слова (≤3 символов) → substring: r"kw"
    - Multi-word фразы → дословный substring (lowercase)
    Все паттерны — IGNORECASE (хотя text_clean уже lowercase — на случай будущих изменений).
    """
    escaped = re.escape(kw.lower().strip())
    words = kw.split()
    if len(words) > 1:
        # Multi-word: дословный substring, не word-boundary
        # "machine learning" → r"machine learning" (после escape)
        return re.compile(escaped, re.IGNORECASE)
    elif len(kw.strip()) > 3:
        # Одиночное длинное слово: word-boundary
        # "Anthropic" → r"\banthropoc\b"
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    else:
        # Короткое слово (≤3 символов): substring
        # "AI" → r"ai" (без word boundary — "ai" в "main" тоже совпадёт)
        return re.compile(escaped, re.IGNORECASE)
```

**Обоснование word-boundary vs substring:**

- `"Anthropic"` с `\b...\b` — не совпадёт с `"Anthropics"` (компания Anthropic Inc).
  Но совпадёт с `"anthropic"` в середине предложения.
- `"AI"` без `\b` — substring. `"AI"` в `"main"` даст false positive, но `"AI"` в
  `"train AI models"` корректно совпадёт. Для 2-символьных аббревиатур word-boundary
  иногда ломает (`"AI" → r"\bai\b"` не совпадает с `"AI-driven"` из-за дефиса).
- `"machine learning"` — multi-word, дословный substring. Совпадает с
  `"deep machine learning techniques"`.

#### Поведение при пустом `project.keywords`

**Решение: пустые keywords → пропускать всё (no-op фильтр).**

Обоснование: если проект не задал keywords, значит все ментионы релевантны (нет фильтра).
Это логически согласованно с семантикой «отсутствие критерия = нет ограничения».
Альтернатива «фильтровать всё» — тихий silent failure («я создал проект, keywords пусты,
никаких сигналов»), который сложно обнаружить.

**Это открытый вопрос F.1** — если продукт-агент решит иначе (пустые = фильтровать всё,
потому что это safer default), поведение меняется без изменения контрактов.

#### Агрегация по нескольким queries

В `Project.queries: list[TopicQuery]` — несколько тем. `KeywordFilterStage` в E1
рассматривает каждый ментион как проходящий если он соответствует хотя бы одной теме
из `project.queries`. Это OR-семантика между темами.

```python
async def process(self, mentions, ctx):
    patterns = self._get_patterns(ctx.project)
    result = []
    for mention in mentions:
        for include_pats, exclude_pats in patterns:
            text = mention.text_clean
            include_match = (
                not include_pats  # пустые keywords → no-op
                or any(p.search(text) for p in include_pats)
            )
            exclude_match = any(p.search(text) for p in exclude_pats)
            if include_match and not exclude_match:
                result.append(mention)
                break  # достаточно одной темы
    return result
```

---

### B.4. `DecideStage` (синтетический для E1)

**Файл:** `processing/stages/decide.py`

Заглушка. Все выжившие ментионы → Signal с синтетическими полями.

#### Сигнатура

```python
from decimal import Decimal
import datetime
from uuid import uuid4
from core.models import NormalizedMention, Signal

class DecideStage:
    name: str = "decide"

    async def process(
        self,
        mentions: list[NormalizedMention],
        ctx: "PipelineContext",
    ) -> list[NormalizedMention]:
        """
        E1 synthetic decide:
        - Для каждого ментиона создаём Signal(relevance=1.0, intent='other', is_spam=False).
        - Signal кладём в ctx.pending_signals.
        - Возвращаем пустой список NormalizedMention (all consumed).
        """
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        query_name = ctx.project.queries[0].name if ctx.project.queries else "default"

        for mention in mentions:
            signal = Signal(
                id=uuid4(),
                mention_id=mention.id,
                project_id=ctx.project.id,
                matched_query=query_name,
                relevance_score=1.0,
                is_spam=False,
                intent="other",
                sentiment="neutral",
                entities=[],
                topics=[],
                pipeline_trace=list(ctx.trace),
                cost_usd=Decimal("0"),
                created_at=now_utc,
            )
            ctx.pending_signals.append(signal)

        return []  # all mentions consumed, signals in ctx.pending_signals
```

В E2b `DecideStage` получит реальную логику: `LLMClassifyStage` + `RankStage` + порог.
Сейчас — честная заглушка, ясно помеченная.

---

## C. `PipelineContext` + `IRepository` injection и FakeRepository

### C.1. Fake Repository для slice-тестов

**Файл: `processing/_fakes.py`**

**Решение: `processing/_fakes.py`**, не `tests/fakes/` и не `tests/conftest.py`.

Обоснование:
- Fake нужен не только в тестах — он нужен при отладке CLI до полной интеграции.
  `processing/_fakes.py` доступен из любого кода в проекте.
- `tests/conftest.py` — pytest-специфичный, неудобен для import вне тестов.
- `tests/fakes/` — отдельная директория, хорошо для изоляции, но слишком далеко
  от processing-слоя. Если fake меняется при изменении IRepository — лучше держать рядом.

При желании в E4/E5 fake можно переместить в `tests/fakes/` — это рефакторинг без
изменения контрактов.

```python
# processing/_fakes.py
from __future__ import annotations
import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID
from core.contracts import IRepository
from core.models import (
    NormalizedMention, Signal, Project, Intent,
    NotificationChannel, NotificationStatus, FeedbackKind,
    ScanStatus, UsageKind,
)

class FakeRepository:
    """
    In-memory реализация IRepository для slice-тестов и отладки CLI до E1-интеграции.
    Реализует только E1-scope методы. Остальные → NotImplementedError.
    """
    def __init__(self) -> None:
        self._hashes: set[str] = set()
        self._mentions: list[NormalizedMention] = []
        self._signals: list[Signal] = []

    # --- Mentions ---
    async def bulk_upsert_mentions_with_dedup(
        self, mentions: list[NormalizedMention]
    ) -> tuple[int, int]:
        inserted, skipped = 0, 0
        for m in mentions:
            if m.content_hash not in self._hashes:
                self._hashes.add(m.content_hash)
                self._mentions.append(m)
                inserted += 1
            else:
                skipped += 1
        return inserted, skipped

    async def existing_hashes(self, hashes: list[str]) -> set[str]:
        return self._hashes & set(hashes)

    # --- Signals ---
    async def insert_signals(self, signals: list[Signal]) -> int:
        self._signals.extend(signals)
        return len(signals)

    async def get_signal(self, signal_id: UUID) -> Signal | None:
        return next((s for s in self._signals if s.id == signal_id), None)

    async def search_signals(self, project_id, since=None, until=None,
                              intent=None, min_score=None, limit=100):
        return [s for s in self._signals if s.project_id == project_id][:limit]

    # --- Scan log ---
    async def last_scanned_at(self, project_id, source_id, query_name):
        return None  # всегда "не сканировали"

    async def record_scan(self, scan_id, project_id, source_id, query_name,
                           started_at, finished_at, count, cost_usd, status):
        pass  # no-op в fake

    # --- Usage / budget ---
    async def append_usage(self, project_id, source_id, cost_usd, occurred_at, kind):
        pass  # no-op

    async def budget_used(self, project_id, since, until=None):
        return Decimal("0")

    async def budget_used_by_source(self, project_id, source_id, since):
        return Decimal("0")

    # --- Всё остальное → NotImplementedError ---
    async def search_hybrid(self, *args, **kwargs):
        raise NotImplementedError("requires E2a")

    async def notification_already_sent(self, *args, **kwargs):
        raise NotImplementedError("requires E5")

    async def record_notification(self, *args, **kwargs):
        raise NotImplementedError("requires E5")

    async def record_feedback(self, *args, **kwargs):
        raise NotImplementedError("requires E5")

    async def upsert_project(self, *args, **kwargs):
        raise NotImplementedError("requires E2c")

    async def get_project(self, *args, **kwargs):
        raise NotImplementedError("requires E2c")

    async def list_projects(self, *args, **kwargs):
        raise NotImplementedError("requires E2c")
```

### C.2. Инициализация Pipeline для E1 slice

```python
# В cli.py или tests/integration/test_e1_pipeline.py

from processing.pipeline import Pipeline
from processing._fakes import FakeRepository
from processing.stages.normalize import NormalizeStage
from processing.stages.dedup import DedupStage
from processing.stages.keyword_filter import KeywordFilterStage
from processing.stages.decide import DecideStage

pipeline = Pipeline(
    stages=[
        NormalizeStage(),
        DedupStage(),
        KeywordFilterStage(),
        DecideStage(),
    ],
    repository=FakeRepository(),   # до интеграции с реальным storage
)

signals = await pipeline.run(mentions=raw_mentions, project=hardcoded_project)
```

После интеграции (E1 слияние веток): `FakeRepository()` заменяется на
`Repository(db=database)` из `storage/repositories.py`.

---

## D. Параллелизм и батчи в E1

### D.1. Минимальный подход (E1)

- Pipeline принимает `list[NormalizedMention]` целиком — батч одного scan-а.
- Размер батча определяется Source (`RedditConfig.default_limit = 100`).
- Все три E1-стадии — sync (быстрые CPU операции: regex, hash, DB query).
- `asyncio.gather` не нужен — стадии последовательны.

### D.2. Future-proof design

Каркас Pipeline уже поддерживает async-стадии без переписывания. `await stage.process(...)`
работает как для `async def` (E1 стадии), так и для стадий, которые внутри делают
`asyncio.gather` (E2a EmbeddingStage — батч-запрос к Voyage API).

Пример: `EmbeddingStage` (E2a) — батч embeddings через Voyage:

```python
# E2a — не писать сейчас, только схема
class EmbeddingStage:
    name = "embedding"

    async def process(self, mentions, ctx):
        texts = [m.text_clean for m in mentions]
        embeddings = await ctx.embedder.embed(texts)   # один batched API-вызов
        # обновить mentions с embeddings
        ...
```

`Pipeline.run()` вызывает `await stage.process(...)` одинаково — никаких изменений в pipeline.

### D.3. Размер батча и поведение при большом вводе

В E1 Reddit возвращает до 100 постов (`q.limit=100`). Это штатный батч.
При увеличении в будущем — pipeline обрабатывает как есть (нет лимита на входе).
Чанкование батча для LLM/embedding — внутри соответствующей стадии, не в Pipeline.

---

## E. Что НЕ делать в E1 / Ветка 2 (Processing)

| Не создавать / не реализовывать | Когда появится |
|---|---|
| `EmbeddingStage` | E2a |
| `SemanticFilterStage` | E2a |
| `LLMClassifyStage` | E2b |
| `RankStage` | E2b |
| MinHash в `DedupStage` | Phase 1+ |
| BM25/full-text-search | E2a |
| YAML-загрузку `Project` | E2c (сейчас hardcoded в `config/bootstrap.py`) |
| Подписку на bus-events | E4 |
| Типизированный `Project.pipeline` config | E2c |
| Prometheus-метрики | E7 (но structlog — сейчас) |
| Circuit-breaker в pipeline | E4 |
| `Pipeline.run()` assert на порядок LLM-стадий | реализовать до старта E2b |

**Про structlog:** E7 добавляет Prometheus метрики; но structlog включается с E1 на
каждой стадии (рекомендация ARCHITECTURE 9.2 — «потом не приделать»). Минимальный logging
уже в примерах кода выше (`logger.info("pipeline_stage_complete", ...)`). В E7 добавится
только Prometheus `Counter` / `Histogram` поверх тех же ключей.

---

## F. Открытые вопросы продукт-агенту

### F.1. `KeywordFilterStage` при пустом `project.keywords`

**Архитектор рекомендует:** пустые keywords → no-op (пропускать всё).

Обоснование: отсутствие ограничения = нет фильтра. Согласуется с семантикой `SourceQuery.keywords`
(пустой список = нет keyword-ограничений в запросе).

Альтернатива «фильтровать всё» (safer default для UX):
- Если владелец забыл добавить keywords → ноль сигналов → очевидная проблема.
- Но тогда нужен явный индикатор «no-filter mode» (флаг в `TopicQuery`).

**Решение нужно до executor-сессии.** Влияет только на `KeywordFilterStage`, не на контракты.

### F.2. Агрегация по нескольким темам и `matched_query` в Signal

В E1 `DecideStage` создаёт Signal с `matched_query = project.queries[0].name` —
берёт первую тему. Если ментион соответствует нескольким темам — он попадает
только в один Signal (с первой совпавшей темой). Это упрощение.

**Более правильно:** один ментион → несколько Signal-ов (по одному per matched query).
Это требует: или несколько вызовов `KeywordFilterStage` per query, или изменения
в `DecideStage` с информацией о всех совпавших темах.

Решение нужно до E2c (там появляется второй проект с несколькими темами). В E1 —
один проект, одна тема, упрощение корректно.

### F.3. `PipelineContext.pending_signals` vs возврат из `DecideStage`

Текущий дизайн: `DecideStage.process()` возвращает `[]` (пустой список NormalizedMention)
и складывает Signal-ы в `ctx.pending_signals`. Это нарушает «чистый» интерфейс `IStage.process`.

Альтернатива: `Pipeline.run()` получает Signal-ы через специальный API после всех стадий
(не через `ctx`). Но тогда нужен отдельный метод на Pipeline или специальный тип результата.

Если продукт-агент предпочитает более чистый дизайн — решение до executor-сессии.
Текущий подход достаточен для E1; рефакторинг в E2b когда `DecideStage` станет полным.

---

## OPS

- **Type**: folder
- **Parent**: `../CLAUDE.md`
- **Root**: `../../../../`
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
