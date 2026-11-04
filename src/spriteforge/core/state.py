"""Atomic event journal and materialized state; artifacts live in SQLite too."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .security import redact


class Run(BaseModel):
    id: str
    status: Literal[
        "pending", "running", "paused", "completed", "failed", "aborted", "budget_exhausted"
    ] = "pending"
    cursor: int = 0
    config: dict = Field(default_factory=dict)


class Stage(BaseModel):
    run_id: str
    name: str
    status: str
    cursor: int


class Event(BaseModel):
    id: int
    run_id: str
    timestamp: str
    agent: str
    type: str
    payload: dict


class Artifact(BaseModel):
    id: str
    run_id: str
    name: str
    mime: str = "image/png"
    data: bytes


class Store:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, status TEXT, cursor INTEGER, config TEXT);
                CREATE TABLE IF NOT EXISTS stages (
                    run_id TEXT REFERENCES runs(id), name TEXT, status TEXT, cursor INTEGER,
                    PRIMARY KEY(run_id, name));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT REFERENCES runs(id),
                    timestamp TEXT, agent TEXT, type TEXT, payload TEXT);
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(id), name TEXT,
                    mime TEXT, data BLOB, UNIQUE(run_id, name));
                CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT, 'Events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT, 'Events are append-only'); END;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _event(self, db, run_id, agent, kind, payload):
        db.execute(
            "INSERT INTO events(run_id,timestamp,agent,type,payload) VALUES(?,?,?,?,?)",
            (
                run_id,
                datetime.now(UTC).isoformat(),
                agent,
                kind,
                json.dumps(redact(payload)),
            ),
        )

    def create_run(self, config: dict) -> Run:
        run = Run(id=uuid4().hex, config=redact(config))
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?)", (run.id, run.status, 0, json.dumps(run.config))
            )
            self._event(db, run.id, "Director", "run.created", run.model_dump())
        return run

    def append_event(self, run_id: str, agent: str, kind: str, payload: dict) -> None:
        with self.connect() as db:
            self._event(db, run_id, agent, kind, payload)

    def get_run(self, run_id: str) -> Run:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Run(**{**dict(row), "config": json.loads(row["config"])})

    def runs(self) -> list[Run]:
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM runs ORDER BY rowid DESC")]
        return [self.get_run(r) for r in ids]

    def events(self, run_id: str, after: int = 0) -> list[Event]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, after)
            ).fetchall()
        return [Event(**{**dict(r), "payload": json.loads(r["payload"])}) for r in rows]

    def artifacts(self, run_id: str) -> list[Artifact]:
        with self.connect() as db:
            return [
                Artifact(**dict(r))
                for r in db.execute(
                    "SELECT * FROM artifacts WHERE run_id=? ORDER BY name", (run_id,)
                )
            ]

    def fail(self, run_id: str, message: str) -> None:
        with self.connect() as db:
            changed = db.execute("UPDATE runs SET status='failed' WHERE id=?", (run_id,))
            if not changed.rowcount:
                raise KeyError(run_id)
            db.execute("UPDATE stages SET status='failed' WHERE run_id=?", (run_id,))
            self._event(db, run_id, "Director", "run.failed", {"message": message})

    def pause_for_budget(self, run_id: str, message: str) -> None:
        """A step used up its allowance: the run waits for the human (Continue), it does not
        end (cost policy: caps stop loops, not the user)."""
        with self.connect() as db:
            db.execute("UPDATE runs SET status='budget_exhausted' WHERE id=?", (run_id,))
            self._event(db, run_id, "Director", "run.paused", {"message": message})

    def continue_run(self, run_id: str) -> None:
        """Continue a run paused by its allowance (or one that ended so before the cost
        policy): a fresh allowance for the work it does next."""
        with self.connect() as db:
            changed = db.execute(
                "UPDATE runs SET status='running' WHERE id=? AND status='budget_exhausted'",
                (run_id,),
            )
            if not changed.rowcount:
                raise ValueError("Only a paused run can be continued")
            self._event(db, run_id, "Human", "allowance.granted", {})

    def retry_failed(self, run_id: str) -> None:
        """Explicit retry keeps artifacts, gate answers and cost reservations intact."""
        with self.connect() as db:
            changed = db.execute(
                "UPDATE runs SET status='running' WHERE id=? AND status='failed'", (run_id,)
            )
            if not changed.rowcount:
                raise ValueError("Only a failed run can be retried")
            self._event(db, run_id, "Human", "run.retry_requested", {})

    def save_artifact(self, artifact: Artifact, agent: str, kind: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?)",
                (artifact.id, artifact.run_id, artifact.name, artifact.mime, artifact.data),
            )
            self._event(db, artifact.run_id, agent, kind, artifact.model_dump(exclude={"data"}))

    def advance(
        self,
        run_id: str,
        expected: int,
        message: str,
        artifact: Artifact | None = None,
        final: bool = False,
    ) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT cursor,status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row[0] != expected or row[1] == "completed":
                return False
            status = "completed" if final else "running"
            db.execute(
                "UPDATE runs SET cursor=?,status=? WHERE id=?", (expected + 1, status, run_id)
            )
            stage = Stage(run_id=run_id, name="demo", status=status, cursor=expected + 1)
            db.execute(
                "INSERT OR REPLACE INTO stages VALUES(?,?,?,?)",
                (run_id, stage.name, stage.status, stage.cursor),
            )
            payload = {
                "cursor": expected + 1,
                "message": message,
                "status": status,
                "stage": stage.model_dump(),
            }
            if artifact:
                if artifact.run_id != run_id:
                    raise ValueError("Artifact belongs to a different run")
                db.execute(
                    "INSERT INTO artifacts VALUES(?,?,?,?,?)",
                    (artifact.id, run_id, artifact.name, artifact.mime, artifact.data),
                )
                payload["artifact"] = artifact.model_dump(exclude={"data"})
            self._event(
                db, run_id, "Director", "run.completed" if final else "stage.progress", payload
            )
            return True
