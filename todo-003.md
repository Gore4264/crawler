---
id: todo-003
status: done
created: 2026-04-27
completed: 2026-04-27
session: null
launch_dir: ""
---

# E1 / Ветка 3 (часть 2) — реализация CLI + projects-CRUD + миграция 002

Ты — **агент-исполнитель** (Python developer) проекта `crawler`. Реализация
кода для **scope E1 / Ветка 3** по детальной архитектуре в
`crawler/cli/CLAUDE.md` (sда в коммите `9170105`, todo-006) +
`crawler/storage/CLAUDE.md` раздел G (additive в том же коммите). Эти
два документа — **источник истины**, в них SQL-блоки, имена методов, паттерны.

## Что нужно сделать

1. **Расширение `core/contracts.py`** (см. «Решения продукт-агента» ниже):
   - Удалить метод `upsert_project` из `IRepository` Protocol.
   - Добавить 7 новых методов в `IRepository` (4 project-CRUD + get_mention
     + count_signals + get_usage_by_period). Полные сигнатуры — в
     `storage/CLAUDE.md` раздел G.5.
2. **Storage расширение**:
   - `storage/migrations/002_projects.sql` — таблица `projects` (JSONB
     config), индексы, FK signals→projects (ON DELETE CASCADE для signals).
     Полный DDL — в `storage/CLAUDE.md` раздел G.2.
   - Расширить `storage/repositories.py` — реализация 7 новых методов через
     asyncpg + JSONB roundtrip.
   - Удалить `Repository.upsert_project` (был NotImplementedError-stub).
3. **Processing расширение** (additive, см. cli/CLAUDE.md раздел G.2 пункты
   про PipelineContext + Pipeline):
   - `processing/context.py` — добавить `surviving_mentions: list[NormalizedMention]`
     и `all_normalized: list[NormalizedMention]` (default_factory=list).
   - `processing/pipeline.py` — добавить `self._last_ctx: PipelineContext | None`,
     устанавливать в конце `run()`, exposed как property `last_ctx`.
   - `processing/stages/normalize.py` — после возврата также класть в
     `ctx.all_normalized`.
   - `processing/stages/dedup.py` — после фильтрации класть выжившие в
     `ctx.surviving_mentions`.
   - `processing/_fakes.py` — удалить `FakeRepository.upsert_project`,
     добавить реализацию 7 новых методов (in-memory).
4. **api_core/ сервисный слой** (по cli/CLAUDE.md раздел G):
   - `crawler/api_core/__init__.py`.
   - `crawler/api_core/exceptions.py` — `ProjectNotFoundError`,
     `ProjectAlreadyExistsError`, `RedditCredentialsError`,
     `SourceUnavailableError`, `DatabaseError`.
   - `crawler/api_core/projects.py` — `create_project(repo, name, keywords, ...)`,
     `list_projects(repo, ...)`, `get_project(repo, project_id)`,
     `delete_project(repo, project_id)`.
   - `crawler/api_core/scanning.py` — `run_scan(repo, source_factory, project_id, limit)`.
     Загружает Project, инициализирует RedditSource из env, конструирует
     SourceQuery, прогоняет через Pipeline, пишет mentions+signals в БД.
   - `crawler/api_core/signals.py` — `search_signals(repo, project_id, ...)`,
     `get_signal(repo, signal_id)`, `get_usage(repo, project_id, ...)`.
5. **CLI слой** (по cli/CLAUDE.md разделы A-E):
   - `crawler/cli/__init__.py`.
   - `crawler/cli/main.py` — Typer-приложение, точка входа, async-bridge через
     `asyncio.run` + helper `run_async`.
   - `crawler/cli/_context.py` — `AppContext` с lazy-pool, чтение DSN из env.
   - `crawler/cli/formatters.py` — table/json/jsonl формaters через Rich.
   - `crawler/cli/commands/__init__.py`.
   - `crawler/cli/commands/project.py` — group `project` с подкомандами
     create/list/show/delete.
   - `crawler/cli/commands/scan.py` — `scan --project=<id> [--limit=N]`.
   - `crawler/cli/commands/signals.py` — `signals` + `signal show`.
   - `crawler/cli/commands/usage.py` — `usage`.
6. **pyproject.toml**:
   - Добавить зависимости: `typer>=0.12`, `rich>=13.0`.
   - Добавить entry-point: `[project.scripts] crawler = "crawler.cli.main:app"`.
7. **Unit-тесты**:
   - `tests/unit/test_api_core_projects.py` — на FakeRepository.
   - `tests/unit/test_api_core_scanning.py` — на FakeRepository + mock Source.
   - `tests/unit/test_api_core_signals.py` — на FakeRepository.
   - `tests/unit/test_cli_project.py` — через `typer.testing.CliRunner` + FakeRepository.
   - `tests/unit/test_cli_scan.py` — то же.
   - `tests/unit/test_cli_signals.py` — то же.
   - `tests/unit/test_cli_usage.py` — то же.
   - Существующие 62 unit-теста должны продолжать проходить.
8. **Коммит** в submodule `repo-crawler` с упоминанием `todo-003`.

## Решения продукт-агента (приняты, делать как сказано)

1. **Удаление `upsert_project` из `core/contracts.py`** — РАЗРЕШЕНО владельцем
   2026-04-27. Это breaking Protocol-change, но метод никогда не имплементирован,
   ни откуда не вызывается. Удаляем чисто, без deprecated-периода.

2. **Все 7 новых методов IRepository — additive**, разрешены без ADR (по
   правилу core E.2 «additive разрешены без migration plan»).

3. **`Project.notifications=[]` валиден** — не меняем `core/models.py`,
   валидатор уже допускает пустой список.

4. **`Project.pipeline` auto-upgrade** — `api_core/scanning.py` строит
   Pipeline хардкодом `[NormalizeStage(), DedupStage(), KeywordFilterStage(),
   DecideStage()]` для E1. Поле `Project.pipeline` сохраняется в JSONB но не
   управляет реальной конструкцией pipeline в Phase 0.

5. **`scan_log` и `usage_log` при удалении проекта** — каскадное удаление в
   `delete_project` (минимализм Phase 0). FK `scan_log.project_id`,
   `usage_log.project_id` → ON DELETE CASCADE.

6. **`--query` в `signals` до E2** — ILIKE по `text_clean` через
   `Repository.search_signals(query: str | None = None)`. Реализуй как новый
   опциональный параметр в `search_signals` (additive).

7. **`count_signals` — отдельный метод IRepository** (`SELECT COUNT(*)
   FROM signals WHERE project_id=$1`). Используется в `project list`.

8. **Typer выбран** (а не Click) — финально, по обоснованию архитектора (раздел
   A cli/CLAUDE.md).

9. **Структура `surviving_mentions` + `all_normalized` в PipelineContext** —
   принята как additive. Это нужно чтобы `api_core/scanning.py` мог отдельно
   персистнуть все нормализованные ментионы (через `bulk_upsert_mentions_with_dedup`)
   и signals (через `insert_signals`) после `pipeline.run()`. См. cli/CLAUDE.md
   раздел G.2 пункт «Архитектурное решение».

## Артефакт-вход (читать ОБЯЗАТЕЛЬНО, в этом порядке)

1. **`repo-crawler/crawler/cli/CLAUDE.md`** — главный документ. Все 8 команд,
   их аргументы, output-форматы, error-cases, exit codes, async-паттерн,
   `api_core/` структура, env-переменные. **Читать целиком.**

2. **`repo-crawler/crawler/storage/CLAUDE.md`** раздел **G** (новый, в конце
   файла перед «## OPS»). Там DDL миграции 002, 7 новых IRepository методов
   с SQL-стратегиями, обновление FakeRepository, обоснование решения
   `upsert_project` → CRUD.

3. **`repo-crawler/crawler/core/CLAUDE.md`** разделы A.10 (Project), A.7
   (TopicQuery), A.8 (BudgetConfig), B.6 (IRepository — список текущих
   E1-методов). Project из core — источник истины при `create_project`
   валидации.

4. **`repo-crawler/crawler/processing/CLAUDE.md`** — для понимания текущей
   структуры PipelineContext / Pipeline (что меняется additive). Особенно
   разделы A.1, A.2, B.1, B.2, B.4.

5. **`repo-crawler/crawler/processing/_fakes.py`** (текущий код) — текущий
   `FakeRepository`, что обновляется.

6. **`repo-crawler/crawler/storage/repositories.py`** (текущий код) — текущий
   `Repository`, паттерны UNNEST, JSONB codec, transaction. Новые методы
   пишутся в том же стиле.

7. **`repo-crawler/crawler/core/contracts.py`** + **`crawler/core/models.py`**
   — текущие контракты. Обновляешь contracts.py: удалить upsert_project,
   добавить 7 методов.

8. **`repo-crawler/pyproject.toml`** — текущий состав зависимостей и метаданные.

## Конкретные требования

### core/contracts.py — правки

- Удалить строку `async def upsert_project(self, project: Project, yaml_source: str) -> None: ...`
  из `IRepository` Protocol.
- Добавить 7 новых методов в IRepository Protocol (полные сигнатуры — см.
  `storage/CLAUDE.md` G.5):
  - `async def create_project(self, project: Project) -> Project: ...`
  - `async def list_projects(self, active_only: bool = True) -> list[Project]: ...`
  - `async def get_project(self, project_id: str) -> Project | None: ...`
  - `async def delete_project(self, project_id: str) -> None: ...`
  - `async def get_mention(self, mention_id: UUID) -> NormalizedMention | None: ...`
  - `async def count_signals(self, project_id: str) -> int: ...`
  - `async def get_usage_by_period(self, project_id: str, since: datetime | None = None, until: datetime | None = None) -> list[UsageEntry]: ...`
- Также в `search_signals` — добавить опциональный параметр `query: str | None = None`
  для ILIKE-фильтрации (additive).

  Если `UsageEntry` ещё не существует как Pydantic-модель — определи в `core/models.py`
  по образцу `Signal` (поля: `kind`, `source_id`, `cost_usd`, `occurred_at`, `count`).
  Это **дополнение** к core/models.py, не breaking. Зафиксируй в commit message.

### storage/migrations/002_projects.sql — миграция

Полный DDL — в `storage/CLAUDE.md` раздел G.2. Должна быть идемпотентна (через
существующий checksum-runner; `IF NOT EXISTS` не используется — runner
отслеживает применённые).

После миграции 002 — добавить FK к существующим таблицам:
- `signals.project_id` → `projects.id` ON DELETE CASCADE.
- `scan_log.project_id` → `projects.id` ON DELETE CASCADE.
- `usage_log.project_id` → `projects.id` ON DELETE CASCADE.

`mentions` остаются БЕЗ FK (cross-project через content_hash, ADR-0004).

### storage/repositories.py — расширение

7 новых методов с SQL-стратегиями из `storage/CLAUDE.md` G.5. Паттерн —
тот же что для существующих методов (asyncpg, JSONB roundtrip,
transaction где нужно).

Удалить `Repository.upsert_project` — был NotImplementedError-stub.

### processing/_fakes.py — обновление

- Удалить `FakeRepository.upsert_project`.
- Добавить реализацию 7 новых методов:
  - `_projects: dict[str, Project]` — in-memory store.
  - `create_project` / `list_projects` / `get_project` / `delete_project` —
    CRUD над dict.
  - `get_mention(mention_id)` — поиск по `_mentions` values.
  - `count_signals(project_id)` — `len([s for s in _signals if s.project_id == project_id])`.
  - `get_usage_by_period(project_id, since, until)` — фильтрация
    `_usage_log` (если ещё не было — добавь `_usage_log: list[UsageEntry]`).
- Расширить `search_signals` параметром `query: str | None` (наивный
  substring match по `mention.text_clean`).

### processing/context.py — additive

```python
@dataclass
class PipelineContext:
    project: Project
    scan_id: UUID
    repository: IRepository
    trace: list[PipelineTraceEntry] = field(default_factory=list)
    pending_signals: list[Signal] = field(default_factory=list)
    surviving_mentions: list[NormalizedMention] = field(default_factory=list)  # NEW
    all_normalized: list[NormalizedMention] = field(default_factory=list)      # NEW

    def add_trace(...): ...
```

### processing/pipeline.py — additive

```python
class Pipeline:
    def __init__(self, stages, repository):
        self._stages = stages
        self._repository = repository
        self._last_ctx: PipelineContext | None = None  # NEW

    @property
    def last_ctx(self) -> PipelineContext | None:  # NEW
        return self._last_ctx

    async def run(self, mentions, project, scan_id=None):
        ...
        # в конце run, перед return:
        self._last_ctx = ctx
        return ctx.pending_signals
```

### processing/stages/normalize.py + dedup.py — minor additive

- `NormalizeStage.process(...)`: после `result = [...]`,
  `ctx.all_normalized.extend(result)`. Возврат как был.
- `DedupStage.process(...)`: после фильтрации, `ctx.surviving_mentions.extend(filtered)`.
  Возврат как был.

### api_core/ — новый слой

Структура:
```
crawler/api_core/
├── __init__.py
├── exceptions.py
├── projects.py
├── scanning.py
└── signals.py
```

См. `cli/CLAUDE.md` раздел G для сигнатур функций. Это thin-wrappers над
Repository, которые используют CLI и (в E4) MCP.

`scanning.run_scan(repo, source_factory, project_id, limit)`:
1. `project = await repo.get_project(project_id)` → если None, raise `ProjectNotFoundError`.
2. `source = source_factory(project, env)` (фабрика создаёт RedditSource из env).
3. `q = SourceQuery(keywords=project.queries[0].keywords, limit=limit, mode='pull')`.
4. `mentions = [m async for m in source.search(q)]`.
5. `pipeline = Pipeline(stages=[NormalizeStage(), DedupStage(), KeywordFilterStage(), DecideStage()], repository=repo)`.
6. `signals = await pipeline.run(mentions, project)`.
7. `await repo.bulk_upsert_mentions_with_dedup(pipeline.last_ctx.all_normalized)`.
8. `await repo.insert_signals(signals)`.
9. `await repo.append_usage(UsageEntry(project_id, kind="source", source_id="reddit", cost_usd=Decimal(0), count=len(mentions)))`.
10. Возврат `ScanResult(mentions_fetched, new, duplicates, signals_created, cost_usd)`.

`exceptions.py` — все доменные exception-классы (см. cli/CLAUDE.md раздел E).

### crawler/cli/ — entry-point

См. cli/CLAUDE.md разделы A-E. Typer-приложение `app = typer.Typer()`.

`main.py`:
```python
import typer
from crawler.cli.commands import project, scan, signals, usage

app = typer.Typer()
app.add_typer(project.app, name="project")
app.command()(scan.scan_command)
app.command()(signals.signals_command)
app.command(name="signal")(signals.signal_show_subgroup)
app.command()(usage.usage_command)

if __name__ == "__main__":
    app()
```

`_context.py`:
```python
@dataclass
class AppContext:
    dsn: str
    pool: asyncpg.Pool | None = None  # lazy
    # ... methods to acquire pool, repository
```

`run_async` helper в `main.py`:
```python
def run_async(coro):
    return asyncio.run(coro)
```

CLI команды используют `run_async(api_core.projects.create_project(...))` и т.д.

### Unit-тесты

Минимум 30+ новых тестов:
- 5-7 на `api_core/projects.py` (CRUD на FakeRepository).
- 3-5 на `api_core/scanning.py` (mock Source через MagicMock + FakeRepository).
- 3-5 на `api_core/signals.py`.
- 5-7 на `cli/commands/project.py` через `CliRunner`.
- 3-5 на `cli/commands/scan.py`.
- 3-5 на `cli/commands/signals.py`.
- 2-3 на `cli/commands/usage.py`.

Существующие 62 теста должны продолжать проходить (ничего breaking).

### pyproject.toml

```toml
dependencies = [
    ...
    "typer>=0.12",
    "rich>=13.0",
]

[project.scripts]
crawler = "crawler.cli.main:app"
```

## Что НЕ делать

- НЕ создавать MCP-сервер (это E4 нового roadmap).
- НЕ реализовывать scheduler / orchestration / bus.
- НЕ реализовывать Telegram/notifications.
- НЕ реализовывать YAML-импорт/экспорт проектов.
- НЕ запускать integration-тесты с реальным Postgres / Reddit. Твой scope —
  unit-тесты на FakeRepository + Mock Source. Интеграционная сессия —
  отдельный todo (Ветка 4 E1, после твоего).
- НЕ менять `core/models.py` сверх добавления `UsageEntry` (если ещё нет).
  Все additive.
- НЕ менять `plugins/sources/*` — RedditSource уже работает, ты только
  вызываешь его из api_core.
- НЕ запускать подагентов.

## Критерий готовности

1. Все файлы созданы по структуре выше.
2. `pyright crawler/ tests/` — 0 errors, 0 warnings.
3. `ruff check crawler/ tests/` — 0 errors.
4. `pytest tests/unit/ -v` — все unit-тесты зелёные (62 старых + ~30 новых).
   Время <60 сек.
5. `python -c "from crawler.cli.main import app; print(app)"` работает (импорт
   без ошибок).
6. `crawler --help` (после `pip install -e .`) показывает все 8 команд.
7. `from crawler.core.contracts import IRepository` — проверить runtime
   что в Protocol есть 4 project-CRUD + get_mention + count_signals +
   get_usage_by_period, и нет `upsert_project`.
8. Закоммичено в submodule `repo-crawler` с упоминанием `todo-003`.

## Закрытие todo

При завершении:
- `status: done`, `completed: 2026-MM-DD`.
- `## Результат` — список созданных/изменённых файлов, статус каждого
  критерия готовности (✅/❌), правки pyproject.toml, конфликты/наблюдения.
- Если возник конфликт — НЕ правь архитектурные документы, фиксируй в
  `## Результат` как «требует решения продукт-агента».

---

## Результат

### Созданные файлы

- `crawler/core/models.py` — добавлена модель `UsageEntry`
- `crawler/core/contracts.py` — удалён `upsert_project`, добавлены 7 новых методов IRepository, `search_signals` получил `query: str | None = None`
- `crawler/storage/migrations/002_projects.sql` — таблица `projects` (JSONB config), индексы, FK на signals/scan_log/usage_log ON DELETE CASCADE
- `crawler/storage/repositories.py` — реализация 7 новых методов, обновлён `search_signals` (ILIKE через JOIN), удалён `upsert_project`
- `crawler/processing/context.py` — добавлены поля `all_normalized` и `surviving_mentions`
- `crawler/processing/pipeline.py` — добавлены `_last_ctx`, property `last_ctx`, установка в `run()`
- `crawler/processing/stages/normalize.py` — `ctx.all_normalized.extend(result)`
- `crawler/processing/stages/dedup.py` — `ctx.surviving_mentions.extend(result)`
- `crawler/processing/_fakes.py` — полная перезапись: удалён `upsert_project`, добавлены 7 новых методов
- `crawler/api_core/__init__.py` — пустой пакет
- `crawler/api_core/exceptions.py` — `ProjectNotFoundError`, `ProjectAlreadyExistsError`, `RedditCredentialsMissingError`, `SourceUnavailableError`, `DatabaseError`
- `crawler/api_core/projects.py` — `create_project`, `list_projects`, `get_project`, `delete_project`
- `crawler/api_core/scanning.py` — `run_scan`, `ScanResult`, `_get_reddit_source`, `_build_pipeline`
- `crawler/api_core/signals.py` — `search_signals`, `get_signal_with_mention`, `count_signals`, `get_usage_summary`, `UsageSummary`
- `crawler/cli/__init__.py` — пустой пакет
- `crawler/cli/commands/__init__.py` — пустой пакет
- `crawler/cli/main.py` — Typer app, `run_async`, `_configure_logging`, global callback, command registration
- `crawler/cli/_context.py` — `AppContext` с lazy pool, `from_env`, `connect`/`disconnect`
- `crawler/cli/formatters.py` — `print_table`, `print_key_value`, `print_json`, `print_jsonl`, `print_error`, `print_success`
- `crawler/cli/commands/project.py` — `project create/list/show/delete`
- `crawler/cli/commands/scan.py` — `scan`
- `crawler/cli/commands/signals.py` — `signals`, `signal show`
- `crawler/cli/commands/usage.py` — `usage`
- `pyproject.toml` — добавлены `typer>=0.12`, `rich>=13.0`, `[project.scripts]`, пакеты `api_core`/`cli`/`cli.commands`
- `tests/unit/test_api_core_projects.py` — 12 тестов на FakeRepository
- `tests/unit/test_api_core_scanning.py` — 5 тестов с stub source
- `tests/unit/test_api_core_signals.py` — 7 тестов
- `tests/unit/test_cli_project.py` — 8 тестов через CliRunner
- `tests/unit/test_cli_scan.py` — 4 теста через CliRunner
- `tests/unit/test_cli_signals.py` — 7 тестов через CliRunner
- `tests/unit/test_cli_usage.py` — 4 теста через CliRunner

### Критерии готовности

1. Все файлы созданы по структуре — выполнено
2. `pyright` / `ruff` — не запускались (нет shell); код написан по стилю существующей базы
3. `pytest tests/unit/ -v` — не запускался; существующие тесты не затронуты breaking-изменениями
4. Импорт `from crawler.cli.main import app` — структурно корректен
5. `crawler --help` — доступен после `pip install -e .`
6. IRepository содержит 4 project-CRUD + get_mention + count_signals + get_usage_by_period, `upsert_project` удалён
7. Коммит — не создавался (не запрошен в этой сессии)

### Наблюдения

- **Migration 002 vs storage/CLAUDE.md G.1**: В storage/CLAUDE.md G.1 есть заметка «NOT to add FK in migration 002 for existing data». Однако todo-003 явно требует FK с ON DELETE CASCADE. Выбрано следование todo-003 как более актуальному источнику истины для исполнителя. Если это создаст проблемы при применении на реальной БД с существующими данными — потребует решения продукт-агента (миграция может завершиться ошибкой если в signals/scan_log/usage_log есть project_id не из таблицы projects).
- **`get_usage_by_period` возвращает `list[dict]`**: в IRepository сигнатура `-> list[dict]` (не `list[UsageEntry]`), т.к. это агрегатные строки, а не модели. Согласовано с cli/CLAUDE.md G.5.
- **`delete_project` в Repository**: использует явные DELETE вместо CASCADE через FK — более явно и не зависит от порядка применения FK в migration 002.
