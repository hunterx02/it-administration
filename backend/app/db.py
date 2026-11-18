"""Database engine/session. SQLite now, PostgreSQL later by changing ITADM_DATABASE_URL only."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return engine


class Database:
    def __init__(self, url: str):
        self.engine = make_engine(url)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def create_all(self) -> None:
        from . import models, modules  # noqa: F401  (register tables)
        Base.metadata.create_all(self.engine)
        self.add_missing_columns()

    def add_missing_columns(self) -> list[str]:
        """Additive migration: new nullable/defaulted columns are added to existing tables so old
        databases keep their data. Columns are never dropped or retyped automatically."""
        from sqlalchemy import inspect, text
        added = []
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                have = {c["name"] for c in insp.get_columns(table.name)}
                for col in table.columns:
                    if col.name in have:
                        continue
                    ddl_type = col.type.compile(dialect=self.engine.dialect)
                    default = ""
                    if col.default is not None and getattr(col.default, "is_scalar", False):
                        v = col.default.arg
                        default = f" DEFAULT {int(v) if isinstance(v, bool) else repr(v) if isinstance(v, str) else v}"
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}{default}'))
                    added.append(f"{table.name}.{col.name}")
        return added

    def session(self) -> Iterator[Session]:
        s = self.SessionLocal()
        try:
            yield s
        finally:
            s.close()
