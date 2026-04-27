"""AppContext — per-command database connection context.

Created in the global app callback, passed to all commands via ctx.obj.
Follows a simple connect()/disconnect() lifecycle — no global state.
"""
from __future__ import annotations

import dataclasses
import os

import structlog
import typer

from crawler.core.contracts import IRepository

logger = structlog.get_logger(__name__)


@dataclasses.dataclass
class AppContext:
    """
    Singleton-like context for one CLI invocation.

    Holds the DSN and lazily creates the database pool on first connect().
    Passed to all commands via ctx.obj.
    """

    database_dsn: str
    verbose: bool = False
    _database: object | None = dataclasses.field(default=None, repr=False)
    _repository: object | None = dataclasses.field(default=None, repr=False)

    @classmethod
    def from_env(cls, *, verbose: bool = False) -> AppContext:
        """
        Read CRAWLER_DATABASE_DSN from environment. Exits with code 2 if not set.
        """
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
        """Create database pool and repository. Call before using repository."""
        from crawler.storage.database import Database
        from crawler.storage.repositories import Repository

        db = Database(dsn=self.database_dsn)
        await db.connect()
        self._database = db
        self._repository = Repository(db=db)
        logger.debug("database_connected", dsn_host=self.database_dsn.split("@")[-1])

    async def disconnect(self) -> None:
        """Close the database pool."""
        if self._database is not None:
            from crawler.storage.database import Database

            if isinstance(self._database, Database):
                await self._database.disconnect()
            self._database = None
            self._repository = None
            logger.debug("database_disconnected")

    @property
    def repository(self) -> IRepository:
        if self._repository is None:
            raise RuntimeError(
                "AppContext.connect() was not called before accessing repository"
            )
        return self._repository  # type: ignore[return-value]
