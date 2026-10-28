"""Immutable checkpoints, content-addressed raw cache, and locked asset records."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from spriteforge.core.security import redact
from spriteforge.core.state import Artifact

from .asset_models import AssetRecord


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ProductionStore:
    def __init__(self, store, run_id):
        self.store, self.run_id = store, run_id
        self.root = store.path.parent / "runs" / run_id
        self.artifacts = {a.name: a for a in store.artifacts(run_id)}
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS production_assets (
                run_id TEXT REFERENCES runs(id), asset_id TEXT, body TEXT,
                PRIMARY KEY(run_id,asset_id))""")

    def save(self, name, value, role="Pixel Artist", mime="application/json"):
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Artifact paths must stay within the run")
        raw = (
            value
            if isinstance(value, bytes)
            else json.dumps(redact(value), sort_keys=True).encode()
        )
        old = self.artifacts.get(name)
        if old and old.data != raw:
            raise ValueError(f"Locked artifact differs: {name}")
        if not old:
            old = Artifact(id=uuid4().hex, run_id=self.run_id, name=name, mime=mime, data=raw)
            self.store.save_artifact(old, role, "artifact.created")
            self.artifacts[name] = old
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != raw:
            raise ValueError(f"Locked artifact differs on disk: {name}")
        if not target.exists():
            target.write_bytes(raw)
        return old.id

    def read(self, name):
        return json.loads(self.artifacts[name].data)

    def data(self, name):
        return self.artifacts[name].data

    def records(self):
        with self.store.connect() as db:
            return {
                r.id: r
                for (body,) in db.execute(
                    "SELECT body FROM production_assets WHERE run_id=?", (self.run_id,)
                )
                if (r := AssetRecord.model_validate_json(body))
            }

    def lock(self, record, plan):
        if plan.versions.get(record.id) != record.version:
            raise PermissionError("Asset version is outside the confirmed production plan")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT body FROM production_assets WHERE run_id=? AND asset_id=?",
                (self.run_id, record.id),
            ).fetchone()
            if row:
                previous = AssetRecord.model_validate_json(row[0])
                if previous.version >= record.version:
                    if previous != record:
                        raise PermissionError("Accepted asset is locked")
                    return
                if record.id not in plan.instructions:
                    raise PermissionError("Locked asset needs a confirmed change request")
            db.execute(
                "INSERT OR REPLACE INTO production_assets VALUES(?,?,?)",
                (self.run_id, record.id, record.model_dump_json()),
            )
            self.store._event(db, self.run_id, "Pixel Artist", "asset.locked", record.model_dump())

    def restore_files(self):
        for name, artifact in list(self.artifacts.items()):
            if name.startswith("production/"):
                self.save(name, artifact.data, mime=artifact.mime)
