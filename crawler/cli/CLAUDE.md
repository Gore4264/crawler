# CLI — Точка входа и команды пользователя

Слой CLI системы `crawler`. Это единственная точка, в которой пользователь-владелец
взаимодействует с системой в Phase 0. CLI — тонкая обёртка над сервисным слоем
`crawler/api_core/`, который разделяется с будущим MCP-сервером (E4).

**Этот документ — техническое задание для агента-исполнителя E1 / Ветка 3.**
После него файлы `cli/main.py`, `cli/_context.py`, `cli/formatters.py`,
`cli/commands/project.py`, `cli/commands/scan.py`, `cli/commands/signals.py`,
`cli/commands/usage.py`, а также `api_core/__init__.py`, `api_core/projects.py`,
`api_core/scanning.py`, `api_core/signals.py` пишутся без архитектурных вопросов.

**Scope: E1 / Ветка 3.** Всё что касается E2+ (embedding, LLM, scheduler,
notifications, MCP) — явно НЕ создаётся. Список в разделе **F**.

## Дисциплина импортов

`cli/` импортирует: `api_core/` (сервисный слой), `core/` (модели), `stdlib`,
`typer`, `rich`, `structlog`. Не импортирует напрямую: `storage/`, `processing/`,
`plugins/`. Вся бизнес-логика — в `api_core/`.

`api_core/` импортирует: `core/` (контракты, модели), `storage/` (через IRepository),
`processing/` (Pipeline, стадии), `plugins/sources/` (RedditSource, SOURCE_REGISTRY).
Не импортирует: `cli/`, `api/`, `bus/`, `orchestration/`.

## Mapping разделов на файлы

| Раздел документа | Файл (создаётся в E1 / Ветка 3) |
|---|---|
| A. Структура слоя | `cli/__init__.py`, `cli/commands/__init__.py` |
| B. Команды CLI | `cli/commands/project.py`, `cli/commands/scan.py`, `cli/commands/signals.py`, `cli/commands/usage.py` |
| B (entry-point) | `cli/main.py` |
| C. CLI-контекст и connection | `cli/_context.py` |
| D. Output-форматы | `cli/formatters.py` |
| E. Exit codes и error handling | политика в `cli/main.py` + `cli/_context.py` |
| G. Сервисный слой | `crawler/api_core/__init__.py`, `api_core/projects.py`, `api_core/scanning.py`, `api_core/signals.py` |

---

## ADR-trail

| Раздел | ADR | Содержание |
|---|---|---|
| G (api_core) | — | D13 в ROADMAP удалено: thin-wrappers выбран без ADR |
| A (Typer выбор) | — | решение в этом документе, раздел A.2 |
| B.1 (project create дефолты) | — | Project.notifications = [] — open question H.1 |
| B.5 (scan + pipeline) | ADR-0004 | content_hash глобален; dedup работает на уровне pipeline |
| B.4 (project delete каскад) | ADR-0004 | mentions остаются; удаляются только projects + signals |

---

## Инварианты

1. **CLI не содержит бизнес-логики.** Любая логика «что делать» — в `api_core/`.
   CLI отвечает за: парсинг аргументов, инициализацию pool, форматирование output,
   exit codes.
2. **Один asyncpg.Pool на lifetime команды.** Pool создаётся при первой команде,
   закрывается при выходе через lifespan callback.
3. **Structlog на stderr, пользовательский output на stdout.** Никогда наоборот.
4. **Все ошибки через `typer.Exit(code)` с человекочитаемым сообщением.** Никаких
   сырых трейсбеков пользователю.
5. **`--verbose` меняет уровень structlog с INFO на DEBUG.** Глобальный флаг.

---

## A. Структура слоя

### A.1. Файловая структура

```
crawler/
├── cli/
│   ├── __init__.py           # пустой (пакет)
│   ├── main.py               # Typer app верхнего уровня + entry-point
│   ├── _context.py           # AppContext: Database, Repository, env-vars
│   ├── formatters.py         # функции форматирования: table, json, jsonl
│   └── commands/
│       ├── __init__.py       # пустой (пакет)
│       ├── project.py        # команды: project create/list/show/delete
│       ├── scan.py           # команда: scan
│       ├── signals.py        # команды: signals, signal show
│       └── usage.py          # команда: usage
└── api_core/
    ├── __init__.py           # пустой (пакет)
    ├── projects.py           # create_project, list_projects, get_project, delete_project
    ├── scanning.py           # run_scan (source + pipeline + persist)
    └── signals.py            # search_signals, get_signal_with_mention, get_usage_summary
```

**`api_core/` — сервисный слой, shared между CLI и MCP.** Подробнее — раздел **G**.

### A.2. Выбор фреймворка: Typer

**Решение: Typer.**

Обоснование:
- Typer использует type-hints Python для объявления параметров. Для проекта с тяжёлыми
  Pydantic-типами (`Project`, `TopicQuery`, `BudgetConfig`) это синергия: меньше
  шаблонного кода, автоматический help из docstring.
- `--keywords` как `list[str]` через `typer.Option` — повторяющийся флаг нативен.
- Rich integration из коробки: `typer` использует Rich для help и progress bars.
- Async-поддержка через простой helper `run_async(coro)` — см. раздел C.3.

**Что теряем vs Click:**
- Click — более стабильная async-интеграция через `asyncclick`. Но `asyncclick` —
  отдельная зависимость; helper `anyio.from_thread.run_sync` / `asyncio.run` проще.
- Click имеет `click.testing.CliRunner` — Typer экспонирует тот же Runner через
  `typer.testing.CliRunner`. Тесты через CliRunner работают одинаково.

**Вывод:** Typer для соло-CLI с Pydantic-доменом — правильный выбор.

### A.3. Entry-point pyproject.toml

```toml
[project.scripts]
crawler = "crawler.cli.main:app"
```

После `pip install -e .` команда `crawler` доступна в shell.

---

## B. Команды CLI (полный список)

### Глобальные флаги (на `app` верхнего уровня)

```
crawler [--verbose] [--format=table|json|jsonl] <command>
```

- `--verbose` / `-v`: bool flag, default False. Включает DEBUG-уровень structlog.
- `--format`: глобальный формат output (table / json / jsonl). Default: table.
  Перекрывается per-command флагом если он есть.

Глобальные флаги прокидываются через `AppContext` (раздел C.1).

---

### B.1. `project create`

**Синтаксис:**

```
crawler project create --name=<slug> --keywords=<kw1> --keywords=<kw2> [options]
```

**Аргументы и флаги:**

| Флаг | Тип | Default | Описание |
|---|---|---|---|
| `--name` / `-n` | `str \| None` | автогенерируется | slug `[a-z0-9_-]+`; если опущен — генерируется из timestamp (см. ниже) |
| `--keywords` / `-k` | `list[str]` | обязательное | повторяющийся флаг: `--keywords=anthropic --keywords="claude ai"`. OR-семантика внутри списка |
| `--excluded` / `-e` | `list[str]` | `[]` | повторяющийся флаг, те же правила |
| `--threshold` / `-t` | `float` | `0.7` | float [0.0, 1.0]; пороговое значение relevance_score для Decide |
| `--format` | `str` | глобальный | override: `table` \| `json` |

**Правила `--name`:**
- Валидируется regex `^[a-z0-9_-]+$` (из `Project.id` в `core/models.py` A.10).
- Если опущен — автогенерируется: `f"project-{int(datetime.now().timestamp())}"`,
  например `project-1714200000`. Это уникально в рамках одного запуска и читаемо
  (не UUID). Если коллизия (проект с таким id уже есть) — выводить ошибку с предложением
  использовать `--name=<другой-slug>`.

**Формат `--keywords`:**
- Повторяющийся флаг (не CSV). Обоснование: CSV ломается на фразах с запятыми
  (`"machine learning, AI"` → два ключевых слова плюс пробел). Повторяющийся флаг
  нативен в Typer и Click:
  ```
  crawler project create --keywords=anthropic --keywords="claude ai" --keywords=llm
  ```
- Каждое значение флага — одно keyword (может быть фраза с пробелами).
- JSON-массив не используется (сложнее вводить в shell без кавычек-дублирования).

**Обязательные поля Project и их дефолты в E1:**

При создании проекта через CLI в Phase 0 устанавливаются следующие дефолты:

| Поле Project | Дефолт | Обоснование |
|---|---|---|
| `sources` | `["reddit"]` | единственный источник Phase 0 |
| `notifications` | `[]` | push в Phase 0 отсутствует |
| `budget` | `BudgetConfig(monthly_usd=Decimal("10"))` | разумный default для MVP |
| `schedule_default` | `"manual"` | Phase 0 ручной режим |
| `pipeline` | `["normalize", "dedup", "keyword_filter", "decide"]` | E1-стадии |
| `threshold` | `0.7` (или из `--threshold`) | |
| `settings` | `{}` | |

**Важно: `Project.notifications` validation.** `core/models.py` A.10 объявляет
`notifications: list[NotificationConfig]` без `min_length=1`. Поле принимает пустой
список без ошибки валидации. Если это не так (Pydantic поставит ограничение в будущем) —
см. **Open Question H.1** ниже.

**Важно: `Project.pipeline` эволюция.** В E1 значение `["normalize", "dedup",
"keyword_filter", "decide"]`. При добавлении E2/E3 стадий это поле в существующих
проектах **не обновляется автоматически**. Решение: при `run_scan` `api_core/scanning.py`
самостоятельно конструирует Pipeline из стандартного набора стадий, **игнорируя
`project.pipeline`** в E1/E2/E3. Поле `pipeline` — для сериализации в JSONB и будущей
конфигурируемости (Phase 1+). В Phase 0 реальные стадии определяются в `scanning.py`
хардкодом. **Фиксируется как open question H.2** — продукт-агент решает когда
`project.pipeline` начинает реально управлять составом стадий.

**Output:**

- `--format=table` (default): строка `Created project: <id>` + вывод полного Project
  в таблицу (те же поля что `project show`).
- `--format=json`: JSON-объект `Project.model_dump(mode="json")`.

**Error-cases:**

| Условие | Exit code | Сообщение |
|---|---|---|
| `--name` не соответствует slug-regex | 1 | `Error: invalid project name '<name>': must match [a-z0-9_-]+` |
| Проект с таким id уже существует | 1 | `Error: project '<id>' already exists` |
| `--keywords` не передан (пустой список) | 1 | `Error: at least one --keywords required` |
| `--threshold` вне [0.0, 1.0] | 1 | `Error: threshold must be between 0.0 and 1.0` |
| Postgres недоступен | 2 | `Error: database connection failed: <dsn без пароля>` |

---

### B.2. `project list`

**Синтаксис:**

```
crawler project list [--active-only] [--format=table|json]
```

**Флаги:**

| Флаг | Тип | Default | Описание |
|---|---|---|---|
| `--active-only` | bool flag | True | показывать только `is_active=True` проекты |
| `--format` | str | глобальный | `table` \| `json` |

**Output (table):**

```
id            name          created_at           last_scan_at         signals
─────────────────────────────────────────────────────────────────────────────
mvp-test      mvp-test      2026-04-27 10:00     2026-04-27 11:30     42
ai-monitor    ai-monitor    2026-04-27 09:00     —                    0
```

Столбцы: `id | name | created_at | last_scan_at | signals_count`.

- `last_scan_at`: берётся из `scan_log` через `repository.last_scanned_at(project_id, ...)`.
  Для простоты — берём по первому `source_id` проекта и первой query.
  Если ни одного scan не было — выводится `—`.
- `signals_count`: `COUNT(*) FROM signals WHERE project_id = $1`.
  Это отдельный метод в `api_core/signals.py` (`count_signals(repo, project_id)`),
  который делает агрегацию через `repository.search_signals(project_id, limit=0)` ...
  нет, это неэффективно. **Решение: добавить метод `count_signals_by_project` в
  `api_core/signals.py` с прямым SQL через `repository`.** Сигнатура в IRepository
  не нужна — count делается в `api_core` поверх `search_signals` с большим `limit`
  (см. Open Question H.3).

**Когда проектов 0:**

```
No projects found. Create one with: crawler project create --name=<slug> --keywords=<kw>
```

Exit code 0 (пустой список — не ошибка).

**Output (json):** JSON-массив объектов `Project.model_dump(mode="json")`.

---

### B.3. `project show <id>`

**Синтаксис:**

```
crawler project show <project_id>
```

**Аргументы:** `project_id: str` — позиционный аргумент.

**Output (table):** полный Project + статистика:

```
Project: mvp-test
─────────────────────────────
id:             mvp-test
name:           mvp-test
created_at:     2026-04-27 10:00 UTC
is_active:      True
keywords:       anthropic, claude ai, llm
excluded:       (none)
threshold:      0.7
sources:        reddit
budget:         $10.00/month
schedule:       manual
pipeline:       normalize, dedup, keyword_filter, decide

Statistics:
─────────────────────────────
signals total:  42
last_scan_at:   2026-04-27 11:30 UTC  (source: reddit, query: mvp-test)
budget_used:    $0.05 (this month)
```

Статистика собирается через `api_core/signals.py` функции.

**Error-cases:**

| Условие | Exit code | Сообщение |
|---|---|---|
| Проект не найден | 1 | `Error: project '<id>' not found` |

---

### B.4. `project delete <id>`

**Синтаксис:**

```
crawler project delete <project_id> [--force]
```

**Аргументы:**

| Аргумент/Флаг | Тип | Default | Описание |
|---|---|---|---|
| `project_id` | str | обязательный | позиционный |
| `--force` / `-f` | bool flag | False | пропустить confirmation |

**Поведение confirmation (без `--force`):**

```
This will delete project 'mvp-test' and all its signals (42 signals).
Mentions will be preserved (shared global cache).
Are you sure? [y/N]:
```

Если пользователь вводит что-то кроме `y`/`Y` — отмена:

```
Cancelled.
```

Exit code 0 при отмене (не ошибка).

**Что удаляется — архитектурное решение:**

- `DELETE FROM projects WHERE id = $1` — строка проекта.
- `DELETE FROM signals WHERE project_id = $1` — все сигналы проекта (CASCADE или явно).
- `DELETE FROM scan_log WHERE project_id = $1` — лог сканов (опционально, см. ниже).
- `DELETE FROM usage_log WHERE project_id = $1` — лог использования (опционально).
- `mentions` — **НЕ удаляются.** Обоснование: `content_hash` глобален (ADR-0004);
  одна и та же запись в `mentions` может быть источником signals для других проектов.
  Удаление mentions нарушило бы `REFERENCES mentions(id) ON DELETE RESTRICT` на таблице
  `signals`. Правильный порядок: сначала signals, потом — теоретически — mentions;
  но без project_id в mentions нельзя понять «какие mentions принадлежат только этому
  проекту» без JOIN через signals. Решение: оставлять mentions.

**Решение по `scan_log` и `usage_log`:**
- Удалять — для чистоты. `scan_log` и `usage_log` имеют `project_id TEXT NOT NULL`
  без FK (FK появится после E2c патча схемы), поэтому DELETE прямо по `project_id`.
  Если продукт решит сохранять историю audit-trail — можно мягкое удаление через
  `is_active=False` на проекте и сохранение остальных записей. **Фиксируется как
  Open Question H.4.**

**Реализация в `api_core/projects.py`:**

```python
async def delete_project(
    repo: IRepository,
    project_id: str,
    *,
    cascade: bool = True,
) -> None:
    """
    1. Проверить что проект существует (get_project).
    2. Если cascade: DELETE signals WHERE project_id.
    3. DELETE scan_log WHERE project_id.
    4. DELETE usage_log WHERE project_id.
    5. DELETE projects WHERE id.
    """
```

**Error-cases:**

| Условие | Exit code | Сообщение |
|---|---|---|
| Проект не найден | 1 | `Error: project '<id>' not found` |
| Postgres недоступен | 2 | `Error: database connection failed` |

---

### B.5. `scan --project=<id>`

**Синтаксис:**

```
crawler scan --project=<project_id> [--limit=N] [--format=table|json]
```

**Флаги:**

| Флаг | Тип | Default | Описание |
|---|---|---|---|
| `--project` / `-p` | str | обязательный | id проекта из БД |
| `--limit` / `-l` | int | 100 | макс. число ментионов у источника |
| `--format` | str | table | output итоговой статистики |

**Порядок выполнения (в `api_core/scanning.py`):**

```
1. repo.get_project(project_id) → Project (или ошибка ProjectNotFound)
2. Validate env creds: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
3. Инициализировать RedditSource из SOURCE_REGISTRY с конфигом из env
4. Для каждого TopicQuery в project.queries:
   a. Построить SourceQuery(keywords=query.keywords, excluded_keywords=query.excluded_keywords, limit=limit)
   b. scan_id = uuid4()
   c. started_at = datetime.now(UTC)
   d. raw_mentions = [m async for m in source.search(source_query)]
   e. Прогнать через Pipeline.run(raw_mentions, project, scan_id)
   f. Полученные Signal-ы → repo.insert_signals(signals)
   g. surviving_mentions = [mentions[i] for i where signal[i] прошёл]
      (нет, правильнее: после pipeline.run → bulk_upsert mentions + insert signals)
   h. repo.bulk_upsert_mentions_with_dedup(normalized_mentions_after_normalize_stage)
      НО: normalize stage уже внутри pipeline. Нужна отдельная обёртка. (см. ниже)
   i. finished_at = datetime.now(UTC)
   j. repo.record_scan(scan_id, project_id, "reddit", query.name, started_at, finished_at, count, cost_usd, status)
   k. repo.append_usage(project_id, "reddit", cost_usd=Decimal("0"), occurred_at=started_at, kind="source")
5. Вернуть ScanResult (статистика)
```

**Про сохранение ментионов и pipeline:**

Текущий Pipeline (из `processing/CLAUDE.md` A.1) не сохраняет ментионы — только
возвращает Signals. Сохранение ментионов — ответственность CLI-обвязки.

Проблема: после `pipeline.run(raw_mentions, project, scan_id)` мы имеем Signals,
но не NormalizedMentions (они сформированы внутри pipeline.run и не возвращаются).

**Решение:** `api_core/scanning.py::run_scan` использует следующую схему:

1. Сначала прогнать через `NormalizeStage` отдельно → получить `list[NormalizedMention]`.
2. Сохранить в БД через `repo.bulk_upsert_mentions_with_dedup(normalized)`.
3. Потом прогнать оставшиеся (после dedup) через `Pipeline(stages=[DedupStage, KeywordFilter, Decide])`.
4. Signals сохранить через `repo.insert_signals(signals)`.

Или, альтернативно — pipeline возвращает не только Signals но и surviving NormalizedMentions
(через расширение `PipelineContext.surviving_mentions` — additive, не breaking).

**Архитектурное решение:** второй вариант (расширение `PipelineContext`) чище — нет
дублирования логики Normalize вне pipeline. Executor добавляет в `PipelineContext`:

```python
@dataclasses.dataclass
class PipelineContext:
    ...
    surviving_mentions: list[NormalizedMention] = dataclasses.field(default_factory=list)
    all_normalized: list[NormalizedMention] = dataclasses.field(default_factory=list)
```

`NormalizeStage.process()` помимо возврата также кладёт в `ctx.all_normalized`.
`DedupStage.process()` кладёт выжившие в `ctx.surviving_mentions` после фильтрации.
`api_core/scanning.py` после `pipeline.run()`:

```python
result = await pipeline.run(mentions, project, scan_id)
signals = result  # list[Signal]
all_normalized = pipeline.last_ctx.all_normalized  # все после Normalize
surviving = pipeline.last_ctx.surviving_mentions    # новые (не-деdup)
await repo.bulk_upsert_mentions_with_dedup(all_normalized)
# (ON CONFLICT DO NOTHING = dedup)
await repo.insert_signals(signals)
```

**Замечание:** `pipeline.last_ctx` нужен чтобы достать context после run.
Executor добавляет `self._last_ctx: PipelineContext | None = None` в Pipeline и
устанавливает его в конце `run()`. Это minor additive изменение `processing/pipeline.py`.

**Также**, `Pipeline.run()` принимает `list[RawMention]` (источник возвращает RawMention),
но `IStage.process` работает с `list[NormalizedMention]`. В `processing/CLAUDE.md` B.1
это обсуждается: Pipeline принимает `list[RawMention]`, приводит через cast, первой стадией
обязательно NormalizeStage. Executor это уже реализовал. Сигнатура `pipeline.run`
должна принимать `list[RawMention]` — проверить фактическую сигнатуру в
`processing/pipeline.py` при реализации.

**Env переменные для Reddit:**

| Переменная | Описание |
|---|---|
| `REDDIT_CLIENT_ID` | OAuth2 client_id из Reddit app |
| `REDDIT_CLIENT_SECRET` | OAuth2 client_secret |
| `REDDIT_USER_AGENT` | user-agent строка для PRAW (напр. `crawler:v0.1 by /u/username`) |

Если хотя бы одна не установлена — немедленная ошибка до запуска scan:

```
Error: REDDIT_CLIENT_ID not set. Reddit credentials required.
Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT in environment or .env
```

**Output по ходу выполнения:**

Typer + Rich progress bar. Отображается:
```
Scanning project 'mvp-test' via reddit...
Fetching mentions... ━━━━━━━━━━━━━━━━━━━━ 100%  done
Running pipeline...
```

Если `--verbose`: дополнительно structlog-логи на stderr (stage-by-stage).

**Output итоговой статистики (table):**

```
Scan complete
─────────────────────
Project:        mvp-test
Source:         reddit
Query:          mvp-test
Mentions total: 100
New (inserted): 87
Duplicates:     13
Signals:        34
Cost:           $0.00
Duration:       3.2s
```

**Output итоговой статистики (json):**

```json
{
  "project_id": "mvp-test",
  "source_id": "reddit",
  "query_name": "mvp-test",
  "mentions_fetched": 100,
  "mentions_inserted": 87,
  "duplicates": 13,
  "signals_created": 34,
  "cost_usd": "0.000000",
  "duration_seconds": 3.2
}
```

**Error-cases:**

| Условие | Exit code | Сообщение |
|---|---|---|
| Проект не найден | 1 | `Error: project '<id>' not found` |
| Reddit creds не установлены | 1 | `Error: REDDIT_CLIENT_ID not set...` |
| Reddit API недоступен / rate-limit | 2 | `Error: Reddit API error: <message>` |
| Postgres недоступен | 2 | `Error: database connection failed` |
| Ctrl+C | 130 | `\nInterrupted by user` |
| Любое неожиданное исключение | 2 | `Error: unexpected error: <type>: <message>` (+ structlog DEBUG traceback) |

**`ScanResult` — data-класс для `api_core/scanning.py`:**

```python
@dataclasses.dataclass
class ScanResult:
    project_id: str
    source_id: str
    query_name: str
    mentions_fetched: int
    mentions_inserted: int
    duplicates: int
    signals_created: int
    cost_usd: Decimal
    duration_seconds: float
    status: ScanStatus  # 'ok' | 'partial' | 'failed'
```

---

### B.6. `signals --project=<id>`

**Синтаксис:**

```
crawler signals --project=<project_id> [--since=<datetime>] [--limit=20]
                [--query=<text>] [--format=table|json|jsonl]
```

**Флаги:**

| Флаг | Тип | Default | Описание |
|---|---|---|---|
| `--project` / `-p` | str | обязательный | id проекта |
| `--since` / `-s` | `datetime \| None` | None | ISO datetime или `24h`, `7d` shortcuts |
| `--limit` / `-l` | int | 50 | число сигналов |
| `--query` / `-q` | `str \| None` | None | текстовый поиск; до E2 — ILIKE по text_clean |
| `--format` | str | table | `table \| json \| jsonl` |

**Формат `--since`:**

Принимает:
- ISO 8601 datetime: `"2026-04-27T10:00:00"` (assume UTC если нет tzinfo) или `"2026-04-27"` (начало дня).
- Relative shortcuts: `"24h"`, `"7d"`, `"30d"` → `datetime.now(UTC) - timedelta(...)`.
- Если `--since` не передан — показывать последние `--limit` сигналов без ограничения по времени
  (т.е. `since=None`, сортировка по `signal_created_at DESC`, `LIMIT limit`).

**`--query` до E2:**

- Если `--query` передан без E2 (нет embedding-стадии) — выполняется ILIKE-фильтрация
  по `mentions.text_clean` через JOIN с signals. Это реализуется в `api_core/signals.py`
  как отдельный путь:
  - E1: `search_signals_with_text_filter(repo, project_id, text_query, ...)` — SQL с
    ILIKE (`WHERE m.text_clean ILIKE '%query%'`) через JOIN `signals JOIN mentions ON signals.mention_id = mentions.id`.
  - E2+: заменяется на `repository.search_hybrid(project_id, text, query_vector, k)`.
  Зафиксировано в Open Question H.5 — продукт решает когда переключать.

**Output (table):**

```
Signals for 'mvp-test'  (last 50)
─────────────────────────────────────────────────────────────────────────────────────────
created_at            score  intent        text[:80]                            url
─────────────────────────────────────────────────────────────────────────────────────────
2026-04-27 11:30 UTC  1.00   other         anthropic released claude 4... →    https://reddit.com/...
2026-04-27 11:28 UTC  1.00   other         new claude model benchmark resul... →   https://reddit.com/...
```

Столбцы: `created_at | relevance_score | intent | text_clean[:80] | url`.

**Output (jsonl):**

По одному Signal JSON per строка — удобно для `jq`:

```json
{"id":"...","project_id":"mvp-test","relevance_score":1.0,"intent":"other","mention_id":"...","created_at":"2026-04-27T11:30:00Z"}
{"id":"...","project_id":"mvp-test","relevance_score":1.0,...}
```

Для jsonl формата каждый Signal включает базовые поля (без pipeline_trace — он большой);
для полного вывода с трассой — использовать `signal show <id>`.

**Когда сигналов 0:**

```
No signals found for project 'mvp-test'. Run: crawler scan --project=mvp-test
```

Exit code 0.

---

### B.7. `signal show <signal_id>`

**Синтаксис:**

```
crawler signal show <signal_id>
```

**Аргументы:** `signal_id: str` — UUID сигнала (позиционный).

**Реализация в `api_core/signals.py`:**

```python
async def get_signal_with_mention(
    repo: IRepository,
    signal_id: UUID,
) -> tuple[Signal, NormalizedMention] | None:
    """
    1. repo.get_signal(signal_id) → Signal | None
    2. Получить NormalizedMention по signal.mention_id:
       новый метод IRepository.get_mention(mention_id) → NormalizedMention | None
       (additive расширение IRepository — см. раздел G.3)
    3. Вернуть (signal, mention) или None
    """
```

**Output:**

```
Signal <uuid>
──────────────────────────────────────────────
project:        mvp-test
matched_query:  mvp-test
relevance:      1.00
is_spam:        False
intent:         other
sentiment:      neutral
entities:       []
cost:           $0.000000
created_at:     2026-04-27 11:30:00 UTC

Mention:
──────────────────────────────────────────────
source:         reddit
url:            https://reddit.com/r/ClaudeAI/...
published_at:   2026-04-27 11:00:00 UTC
lang:           en
text:
  Anthropic released Claude 4 today with new reasoning capabilities...
  (full text, no truncation)

Pipeline trace:
──────────────────────────────────────────────
normalize     0ms   100→100  cost=$0.00
dedup         5ms   100→87   cost=$0.00
keyword_filter 2ms  87→34    cost=$0.00
decide        1ms   34→34    cost=$0.00
```

**Error-cases:**

| Условие | Exit code | Сообщение |
|---|---|---|
| Signal не найден | 1 | `Error: signal '<id>' not found` |
| Mention не найден (orphan signal) | 2 | `Error: mention for signal '<id>' not found (data integrity issue)` |

---

### B.8. `usage --project=<id>`

**Синтаксис:**

```
crawler usage --project=<project_id> [--since=<date>]
```

**Флаги:**

| Флаг | Тип | Default | Описание |
|---|---|---|---|
| `--project` / `-p` | str | обязательный | id проекта |
| `--since` / `-s` | `date \| None` | начало текущего месяца | дата начала периода |

**Реализация в `api_core/signals.py::get_usage_summary`:**

```python
@dataclasses.dataclass
class UsageSummary:
    project_id: str
    period_start: datetime
    total_usd: Decimal
    by_kind: dict[str, Decimal]    # kind → total_usd
    by_source: dict[str, Decimal]  # source_id → total_usd
    signals_count: int             # signals.count for period (approximate via scan_log)
    cost_per_signal: Decimal | None  # total / signals_count, None если 0 signals
```

SQL для `api_core`:

```sql
SELECT kind, source_id, SUM(cost_usd) AS total
FROM usage_log
WHERE project_id = $1 AND occurred_at >= $2
GROUP BY kind, source_id;
```

**Output:**

```
Usage for 'mvp-test'  (since 2026-04-01)
─────────────────────────────────────────
Kind        Cost
─────────────────────────────────────────
source      $0.000000
embedding   $0.000000
llm         $0.000000
other       $0.000000
─────────────────────────────────────────
TOTAL       $0.000000

By source:
  reddit    $0.000000

Signals this period:  34
Cost per signal:      $0.000000  (KPI #3 target: < $0.50)
```

**Когда нет данных за период:**

```
No usage data for project 'mvp-test' since 2026-04-01
```

Exit code 0.

---

## C. CLI-контекст и connection

### C.1. Класс `AppContext`

```python
# cli/_context.py
from __future__ import annotations
import os
import asyncio
import dataclasses
from typing import Any
import typer
import structlog
from storage.database import Database
from storage.repositories import Repository
from core.contracts import IRepository

logger = structlog.get_logger(__name__)

@dataclasses.dataclass
class AppContext:
    """
    Singleton-контекст одного CLI-вызова.
    Создаётся в callback глобального app, передаётся всем командам через ctx.obj.
    """
    database_dsn: str
    verbose: bool = False
    _database: Database | None = dataclasses.field(default=None, repr=False)
    _repository: Repository | None = dataclasses.field(default=None, repr=False)

    @classmethod
    def from_env(cls, *, verbose: bool = False) -> "AppContext":
        """Читает CRAWLER_DATABASE_DSN из env. Fail-fast если не установлен."""
        dsn = os.getenv("CRAWLER_DATABASE_DSN")
        if not dsn:
            typer.echo(
                "Error: CRAWLER_DATABASE_DSN not set.\n"
                "Set it in environment or .env file.\n"
                "Example: postgresql://crawler:password@localhost:5432/crawler",
                err=True,
            )
            raise typer.Exit(code=2)
        return cls(database_dsn=dsn, verbose=verbose)

    async def connect(self) -> None:
        """Lazy connection: вызывается при первой команде."""
        self._database = Database(dsn=self.database_dsn)
        await self._database.connect()
        self._repository = Repository(db=self._database)

    async def disconnect(self) -> None:
        if self._database is not None:
            await self._database.disconnect()

    @property
    def repository(self) -> IRepository:
        if self._repository is None:
            raise RuntimeError("AppContext.connect() not called")
        return self._repository
```

### C.2. DSN и env-переменные

| Переменная | Описание |
|---|---|
| `CRAWLER_DATABASE_DSN` | PostgreSQL DSN: `postgresql://user:pass@host:5432/db` |
| `REDDIT_CLIENT_ID` | OAuth2 client_id |
| `REDDIT_CLIENT_SECRET` | OAuth2 client_secret |
| `REDDIT_USER_AGENT` | user-agent строка для PRAW |

**`.env` файл:** поддерживается через `python-dotenv` (`load_dotenv()` вызывается в
начале `cli/main.py`). Файл `.env` в корне repo (уже есть из E1 / Ветка 1).

### C.3. Async-интеграция

Typer's `typer.run` — синхронный. CLI-команды асинхронные (используют `await`).

**Решение: helper `run_async(coro)` в `cli/main.py`.**

```python
import asyncio
from typing import Coroutine, TypeVar

T = TypeVar("T")

def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Запустить async coroutine из sync контекста (CLI entry-point)."""
    return asyncio.run(coro)
```

Каждая CLI-команда в `commands/*.py` — `async def`, вызывается через `run_async(...)`.

**Паттерн команды:**

```python
# cli/commands/project.py

import typer
from cli._context import AppContext
from cli.main import run_async
import api_core.projects as projects_api

app = typer.Typer()

@app.command("create")
def project_create(
    ctx: typer.Context,
    name: str | None = typer.Option(None, "--name", "-n", help="Project slug"),
    keywords: list[str] = typer.Option(..., "--keywords", "-k", help="Keywords (repeatable)"),
    excluded: list[str] = typer.Option([], "--excluded", "-e", help="Excluded keywords"),
    threshold: float = typer.Option(0.7, "--threshold", "-t", min=0.0, max=1.0),
) -> None:
    app_ctx: AppContext = ctx.obj
    run_async(_project_create_async(app_ctx, name, keywords, excluded, threshold))

async def _project_create_async(
    app_ctx: AppContext,
    name: str | None,
    keywords: list[str],
    excluded: list[str],
    threshold: float,
) -> None:
    await app_ctx.connect()
    try:
        result = await projects_api.create_project(
            repo=app_ctx.repository,
            name=name,
            keywords=keywords,
            excluded=excluded,
            threshold=threshold,
        )
        # форматирование output через formatters.py
        ...
    finally:
        await app_ctx.disconnect()
```

**Pool lifecycle:** `connect()` + `try/finally disconnect()` внутри каждой команды.
Это проще чем lifespan callback и достаточно для CLI (одна команда = одно подключение).

### C.4. Конфигурирование structlog

В `cli/main.py::_configure_logging(verbose: bool)`:

```python
import structlog, logging

def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,   # ВАЖНО: логи на stderr
        level=level,
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
```

В production (Phase 1+) добавится JSON-рендерер для машинного парсинга. Сейчас — ConsoleRenderer для читаемости.

---

## D. Output-форматы

### D.1. Три формата

| Формат | Описание | Когда использовать |
|---|---|---|
| `table` | Rich `Table` | default; для интерактивного использования |
| `json` | единый JSON-массив или объект | скриптинг, пайпинг в jq |
| `jsonl` | построчный JSON | пайпинг, потоковый разбор |

### D.2. `formatters.py`

```python
# cli/formatters.py
from rich.console import Console
from rich.table import Table
import json
import sys
from typing import Any

console = Console()   # stdout
err_console = Console(stderr=True)  # stderr

def print_table(headers: list[str], rows: list[list[str]], title: str = "") -> None:
    """Вывести Rich Table на stdout."""
    table = Table(title=title, show_header=True)
    for h in headers:
        table.add_column(h)
    for row in rows:
        table.add_row(*row)
    console.print(table)

def print_json(data: Any) -> None:
    """Вывести JSON на stdout."""
    console.print_json(json.dumps(data, default=str))

def print_jsonl(items: list[Any]) -> None:
    """Вывести jsonl на stdout — по строке на объект."""
    for item in items:
        sys.stdout.write(json.dumps(item, default=str) + "\n")
    sys.stdout.flush()

def print_error(message: str) -> None:
    """Вывести ошибку на stderr через Rich."""
    err_console.print(f"[red]Error:[/red] {message}")

def print_success(message: str) -> None:
    """Вывести успех на stdout."""
    console.print(f"[green]{message}[/green]")
```

### D.3. Правило: stdout только результаты, stderr только логи

- `formatters.print_table / print_json / print_jsonl` → stdout.
- `formatters.print_error` → stderr.
- `structlog` → stderr (настроен в C.4).
- **Никогда** не писать в stdout из structlog.

---

## E. Error handling и exit codes

### E.1. Exit codes

| Code | Значение | Когда |
|---|---|---|
| 0 | success | команда выполнена; пустой результат тоже 0 |
| 1 | user error | проект не найден, неверный аргумент, missing env var для бизнес-команды |
| 2 | system error | Postgres down, Reddit API down, unexpected exception |
| 130 | Ctrl+C | KeyboardInterrupt |

### E.2. Обработка исключений

**Стратегия:** в каждой `_async`-функции команды оборачивать в `try/except`:

```python
async def _project_create_async(...):
    await app_ctx.connect()
    try:
        ...
    except ProjectNotFoundError as e:
        print_error(str(e))
        raise typer.Exit(code=1)
    except asyncpg.PostgresConnectionError as e:
        print_error(f"database connection failed: {e}")
        raise typer.Exit(code=2)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted by user")
        raise typer.Exit(code=130)
    except Exception as e:
        logger.exception("unexpected_error", error=str(e))
        print_error(f"unexpected error: {type(e).__name__}: {e}")
        raise typer.Exit(code=2)
    finally:
        await app_ctx.disconnect()
```

**Доменные исключения в `api_core/`:**

```python
# api_core/exceptions.py
class CrawlerError(Exception):
    """Базовый класс."""

class ProjectNotFoundError(CrawlerError):
    def __init__(self, project_id: str):
        super().__init__(f"project '{project_id}' not found")

class ProjectAlreadyExistsError(CrawlerError):
    def __init__(self, project_id: str):
        super().__init__(f"project '{project_id}' already exists")

class RedditCredentialsMissingError(CrawlerError):
    def __init__(self, var_name: str):
        super().__init__(
            f"{var_name} not set. "
            "Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT."
        )
```

Эти исключения бросаются в `api_core/`, отлавливаются в `cli/commands/*.py`.

### E.3. Глобальный `--verbose`

При `--verbose=True` структлог переходит в DEBUG. В exceptions — structlog.exception
включает traceback в DEBUG-логе (на stderr). Пользователь видит:
- Без `--verbose`: `Error: unexpected error: TypeError: ...`
- С `--verbose`: полный traceback в stderr + та же строка ошибки.

---

## F. Что НЕ делать в E1 / Ветка 3

| Не создавать / не реализовывать | Когда появится |
|---|---|
| MCP-сервер (`crawler/mcp/`) | E4 |
| `bus/` LISTEN-NOTIFY / pgmq | Phase 1+ |
| Scheduler / `crawler scheduler start` | Phase 1+ |
| Webhook / email / Telegram notifications | Phase 1+ |
| Filter-движок для алертов (mini-DSL `filter_expr`) | Phase 1+ |
| YAML-импорт/экспорт проектов | Phase 1+ |
| `signal mark relevant/spam` (feedback-loop) | Phase 1+ |
| REST API / FastAPI / WebSocket | Phase 1+ |
| `crawler project update` команда | Phase 1+ |
| Семантический поиск в `signals --query` (Voyage embedding) | E2 |
| `scan` в фоне (async task / daemon mode) | Phase 1+ |
| Bluesky / Telegram / другие источники | Phase 1+ |
| BM25-поиск (GIN-индекс на text_clean) | E2 |

**Про `pipeline` в `project create`:** поле сохраняется в JSONB, но не управляет
реальными стадиями в E1 (стадии хардкодированы в `api_core/scanning.py`).

---

## G. Сервисный слой `api_core/` — общий backend для CLI и MCP

### G.1. Назначение и принцип

`api_core/` — тонкий сервисный слой с чистыми async-функциями. Принимает `IRepository`
и другие Protocol-зависимости как аргументы (DI через параметры функций, не через
глобалы). CLI-команды и MCP-tools (E4) — thin-wrappers над этими функциями.

**Это решает дублирование** при добавлении MCP: вся логика уже в `api_core/`,
MCP просто добавляет инструменты с другой сериализацией.

### G.2. `api_core/projects.py`

```python
# api_core/projects.py
from __future__ import annotations
import datetime
from decimal import Decimal
from uuid import uuid4
import re
from core.contracts import IRepository
from core.models import Project, TopicQuery, BudgetConfig
from api_core.exceptions import ProjectNotFoundError, ProjectAlreadyExistsError

async def create_project(
    repo: IRepository,
    *,
    name: str | None,
    keywords: list[str],
    excluded: list[str] = (),
    threshold: float = 0.7,
) -> Project:
    """
    1. Сгенерировать id если name=None.
    2. Валидировать slug-regex (raise ValueError если не соответствует).
    3. Проверить что проект с таким id не существует (repo.get_project).
    4. Построить Project с дефолтами Phase 0.
    5. repo.create_project(project) → Project с заполненным created_at.
    6. Вернуть Project.
    """

async def list_projects(
    repo: IRepository,
    *,
    active_only: bool = True,
) -> list[Project]:
    """Обёртка над repo.list_projects(active_only)."""

async def get_project(
    repo: IRepository,
    project_id: str,
) -> Project:
    """
    repo.get_project(project_id) → Project | None.
    Если None → raise ProjectNotFoundError(project_id).
    """

async def delete_project(
    repo: IRepository,
    project_id: str,
    *,
    cascade: bool = True,
) -> None:
    """
    1. Проверить наличие (get_project).
    2. Если cascade: удалить signals, scan_log, usage_log.
    3. Удалить projects-строку.
    Всё в одной транзакции через repo.delete_project(project_id, cascade=cascade).
    """
```

### G.3. `api_core/scanning.py`

```python
# api_core/scanning.py
from __future__ import annotations
import dataclasses
import datetime
import os
from decimal import Decimal
from uuid import uuid4
from core.contracts import IRepository
from core.models import Project, RawMention, SourceQuery
from api_core.exceptions import (
    ProjectNotFoundError, RedditCredentialsMissingError,
)

@dataclasses.dataclass
class ScanResult:
    project_id: str
    source_id: str
    query_name: str
    mentions_fetched: int
    mentions_inserted: int
    duplicates: int
    signals_created: int
    cost_usd: Decimal
    duration_seconds: float
    status: str  # ScanStatus

async def run_scan(
    repo: IRepository,
    project_id: str,
    *,
    limit: int = 100,
    progress_callback: "Callable[[str], None] | None" = None,
) -> list[ScanResult]:
    """
    Для каждой query в project.queries:
      1. Валидировать Reddit creds из env.
      2. Инициализировать RedditSource.
      3. Выполнить source.search(SourceQuery).
      4. pipeline.run(raw_mentions, project, scan_id).
      5. repo.bulk_upsert_mentions_with_dedup(ctx.all_normalized).
      6. repo.insert_signals(signals).
      7. repo.record_scan(...).
      8. repo.append_usage(...).
    Returns: list[ScanResult] — по одному на каждый (query, source).
    """

def _get_reddit_source() -> "RedditSource":
    """
    Читать REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT из env.
    Raise RedditCredentialsMissingError если какая-то отсутствует.
    Инициализировать RedditSource с RedditConfig(
        client_id=..., client_secret=..., user_agent=...,
        subreddits=None,   # query keywords ищем глобально
        default_limit=100,
        score_threshold=0,
    ).
    """

def _build_pipeline(repo: IRepository) -> "Pipeline":
    """
    Строить Pipeline с E1-стадиями (хардкод):
      [NormalizeStage(), DedupStage(), KeywordFilterStage(), DecideStage()]
    Этот список управляется здесь в Phase 0 — не через project.pipeline.
    """
```

**Additive изменение `processing/pipeline.py`:**

Executor добавляет в `Pipeline`:
- Поле `self._last_ctx: PipelineContext | None = None`.
- В конце `run()`: `self._last_ctx = ctx`.
- Property `last_ctx: PipelineContext`.

Также в `PipelineContext`:
- Поле `all_normalized: list[NormalizedMention]` (заполняется в NormalizeStage).
- Поле `surviving_mentions: list[NormalizedMention]` (заполняется в DedupStage).

Изменения additive — не breaking для существующих тестов (новые поля с default_factory=list).

### G.4. `api_core/signals.py`

```python
# api_core/signals.py
import dataclasses
import datetime
from decimal import Decimal
from uuid import UUID
from core.contracts import IRepository
from core.models import Signal, NormalizedMention, Intent

async def search_signals(
    repo: IRepository,
    project_id: str,
    *,
    since: datetime.datetime | None = None,
    until: datetime.datetime | None = None,
    intent: Intent | None = None,
    min_score: float | None = None,
    limit: int = 50,
    text_query: str | None = None,  # до E2: ILIKE через JOIN
) -> list[Signal]:
    """
    Если text_query задан — выполнить JOIN signals + mentions с ILIKE фильтром.
    Иначе — repo.search_signals(...).
    """

async def get_signal_with_mention(
    repo: IRepository,
    signal_id: UUID,
) -> tuple[Signal, NormalizedMention] | None:
    """
    1. repo.get_signal(signal_id).
    2. repo.get_mention(signal.mention_id).   ← additive IRepository метод
    3. Вернуть (signal, mention).
    """

@dataclasses.dataclass
class UsageSummary:
    project_id: str
    period_start: datetime.datetime
    total_usd: Decimal
    by_kind: dict[str, Decimal]
    by_source: dict[str, Decimal]
    signals_count: int
    cost_per_signal: Decimal | None

async def get_usage_summary(
    repo: IRepository,
    project_id: str,
    *,
    since: datetime.datetime,
) -> UsageSummary:
    """
    Суммирование usage_log за период.
    SELECT kind, source_id, SUM(cost_usd) FROM usage_log WHERE project_id=... AND occurred_at>=...
    GROUP BY kind, source_id.
    Плюс count signals за период (через search_signals с большим limit или отдельный COUNT).
    """
```

### G.5. Additive расширения IRepository для cli/api_core

Следующие методы добавляются в `IRepository` (в `core/contracts.py`) и реализуются
в `storage/repositories.py`. Все — additive (core E.2).

| Метод | Сигнатура | Назначение |
|---|---|---|
| `create_project` | `(project: Project) -> Project` | INSERT в `projects`; возвращает с `created_at` |
| `list_projects` | `(active_only: bool = True) -> list[Project]` | SELECT из `projects` |
| `get_project` | `(project_id: str) -> Project \| None` | SELECT по id |
| `delete_project` | `(project_id: str, cascade: bool = True) -> None` | DELETE (см. B.4 каскад) |
| `get_mention` | `(mention_id: UUID) -> NormalizedMention \| None` | SELECT по id из `mentions` |
| `count_signals` | `(project_id: str, since: datetime \| None = None) -> int` | COUNT(*) FROM signals |
| `get_usage_by_period` | `(project_id: str, since: datetime) -> list[dict]` | GROUP BY kind, source_id в usage_log |

**Про `get_mention`:** нужен для `signal show`. Это чтение из `mentions` по UUID.
Маппинг row → `NormalizedMention` через `NormalizedMention.model_validate(dict(row))`.
`embedding` и `minhash_signature` в Phase 0 отсутствуют в таблице — будут `None` в модели.

**Про `count_signals`:** используется в `project list` и `project show`. Альтернатива —
делать `len(await repo.search_signals(project_id, limit=10_000))` — плохо масштабируется.
Отдельный метод с `SELECT COUNT(*)` — правильно.

**Про `get_usage_by_period`:** возвращает `list[dict]` (сырые агрегированные строки),
`api_core/signals.py` превращает в `UsageSummary`. Метод в IRepository возвращает
dict намеренно — типизация агрегатов сложнее чем того стоит для одного use-case.

Эти методы — additive расширение IRepository. Полные сигнатуры + SQL-стратегии —
в разделе `storage/CLAUDE.md G`.

---

## H. Открытые вопросы продукт-агенту

### H.1. `Project.notifications` — пустой список

В `core/models.py` A.10 поле `notifications: list[NotificationConfig]` не имеет
`min_length` ограничения. Поэтому `notifications=[]` проходит Pydantic-валидацию.

**Архитектор проверил:** в текущем `core/models.py` A.10 нет `min_length=1` на `notifications`.
Следовательно, `Project(notifications=[])` — валидно. CLI создаёт проект с пустым списком.

**Если в будущем добавится `min_length=1`** (breaking-изменение core) — потребуется:
- Placeholder `NotificationConfig(channel="db", target="local")` как фиктивный канал, или
- Разрешение от продукт-агента добавить `notifications: list[NotificationConfig] = Field(default_factory=list)`.

Рекомендация архитектора: не добавлять `min_length=1` — Phase 0 без push, пустой список
семантически корректен.

### H.2. `Project.pipeline` — когда начинает управлять реальными стадиями

В Phase 0 поле `pipeline` сохраняется в JSONB, но `api_core/scanning.py` строит Pipeline
хардкодом. При добавлении E2 (embedding) и E3 (LLM) стадии добавляются в хардкод в `scanning.py`.

**Два варианта перехода:**

1. **Миграция через `project update --add-stage=embedding`** (CLI-команда, Phase 1+).
   Существующие проекты получают новые стадии явно.
2. **Auto-upgrade при scan:** `scanning.py` проверяет наличие активной embedding-конфигурации
   и добавляет стадию автоматически (Phase 0 поведение при E2).

**Рекомендация архитектора:** вариант 2 для Phase 0 (меньше ручной работы). Вариант 1
для Phase 1+ когда появится multi-source и сложные pipeline-конфиги.

Решение нужно продукт-агенту до старта E2.

### H.3. `count_signals` через `search_signals(limit=big)` vs отдельный метод IRepository

Для `project list` нужен `signals_count` per project. Варианты:
- `len(await repo.search_signals(p_id, limit=10_000))` — работает, но тащит данные.
- `repo.count_signals(p_id)` — новый метод IRepository с `SELECT COUNT(*)`.

**Рекомендация архитектора:** `count_signals` как отдельный метод IRepository (additive).
Включён в список раздела G.5.

### H.4. `scan_log` и `usage_log` при удалении проекта

При `project delete` — удалять ли `scan_log` и `usage_log`?

Аргументы за:
- Чистота данных.
- Без FK эти таблицы не знают что проект удалён.

Аргументы против:
- Audit trail утрачивается.
- Стоимость не суммируется правильно если нужна retrospective.

**Рекомендация архитектора:** удалять в Phase 0 (минимализм). В Phase 1+ при появлении
billing-аналитики — мягкое удаление через `is_active=False` или архивирование.

### H.5. `--query` в `signals` до E2: ILIKE или отключить

До E2 (embedding) `--query` может работать только как ILIKE по `text_clean`.
Варианты:
1. **ILIKE активен в E1.** Реализован в `api_core/signals.py` через JOIN.
   Плюс: команда работает сразу. Минус: ILIKE медленный без GIN-индекса.
2. **`--query` в E1 показывает warning:** `"--query requires E2 (semantic search). Use --grep for ILIKE filtering (Phase 1+)"`.

**Рекомендация архитектора:** вариант 1 (ILIKE работает). В Phase 0 объём данных малый
(сотни сигналов), производительность ILIKE без индекса приемлема. При E2 — заменяем
на hybrid search без изменения CLI-интерфейса.

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
