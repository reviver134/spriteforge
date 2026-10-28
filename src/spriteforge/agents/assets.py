"""Immutable image versions and a stable-id registry; only explicit targets may be revised."""

import hashlib
import io
from uuid import uuid4

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from spriteforge.core.state import Artifact, Store
from spriteforge.providers.base import ImageProvider, ImageRequest

from .cancellation import check_active


class GenerateAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(pattern=r"^[a-z0-9_-]+$")
    instruction: str


class AssetResult(BaseModel):
    asset_id: str
    artifact_id: str
    version: int


class Assets:
    def __init__(self, store: Store, run_id: str, provider: ImageProvider):
        self.store, self.run_id, self.provider = store, run_id, provider
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS asset_registry (
                run_id TEXT REFERENCES runs(id), asset_id TEXT, artifact_id TEXT,
                version INTEGER, locked INTEGER, PRIMARY KEY(run_id,asset_id))""")

    def records(self) -> dict[str, AssetResult]:
        with self.store.connect() as db:
            return {
                r[0]: AssetResult(asset_id=r[0], artifact_id=r[1], version=r[2])
                for r in db.execute(
                    "SELECT asset_id,artifact_id,version FROM asset_registry WHERE run_id=?",
                    (self.run_id,),
                )
            }

    def generate(
        self, request: GenerateAsset, *, allowed: set[str], revisions: dict[str, int] | None = None
    ) -> AssetResult:
        if request.asset_id not in allowed:
            raise PermissionError("Asset is outside the approved targets")
        existing = self.records().get(request.asset_id)
        target_version = (revisions or {}).get(request.asset_id, 1)
        if existing and existing.version >= target_version:
            return existing
        if existing and request.asset_id not in (revisions or {}):
            raise PermissionError("Locked asset requires a confirmed change request")
        references = []
        if existing:
            references = [
                a.data for a in self.store.artifacts(self.run_id) if a.id == existing.artifact_id
            ]
        image_request = ImageRequest(
            prompt=request.instruction, size=(48, 48), references=references
        )
        image = (
            self.provider.edit(image_request) if existing else self.provider.generate(image_request)
        )
        check_active()
        artifact = Artifact(
            id=uuid4().hex,
            run_id=self.run_id,
            name=f"assets/{request.asset_id}/v{target_version}.png",
            data=image.data,
        )
        record = AssetResult(
            asset_id=request.asset_id, artifact_id=artifact.id, version=target_version
        )
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?)",
                (artifact.id, self.run_id, artifact.name, artifact.mime, artifact.data),
            )
            db.execute(
                "INSERT OR REPLACE INTO asset_registry VALUES(?,?,?,?,1)",
                (self.run_id, record.asset_id, record.artifact_id, record.version),
            )
            self.store._event(db, self.run_id, "Pixel Artist", "asset.locked", record.model_dump())
        return record

    def contact_sheet(self) -> str | None:
        records = sorted(self.records().values(), key=lambda r: r.asset_id)
        if not records:
            return None
        digest = hashlib.sha256("".join(r.artifact_id for r in records).encode()).hexdigest()[:16]
        name = f"contact-sheet/{digest}.png"
        artifacts = self.store.artifacts(self.run_id)
        previous = next((a for a in artifacts if a.name == name), None)
        if previous:
            return previous.id
        images = {a.id: a for a in artifacts}
        sheet = Image.new("RGBA", (64 * len(records), 64), (24, 24, 32, 255))
        for i, record in enumerate(records):
            image = Image.open(io.BytesIO(images[record.artifact_id].data)).convert("RGBA")
            sheet.paste(image, (i * 64 + 8, 8), image)
        buffer = io.BytesIO()
        sheet.save(buffer, format="PNG")
        artifact = Artifact(id=uuid4().hex, run_id=self.run_id, name=name, data=buffer.getvalue())
        self.store.save_artifact(artifact, "Art Director", "contact_sheet.created")
        return artifact.id
