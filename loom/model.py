from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class SourceDocument:
    source_id: str
    path: str
    name: str
    suffix: str
    size_bytes: int
    sha256: str
    ingested_at: str
    text: str
    metadata: dict[str, Any]

    @classmethod
    def from_path(cls, path: Path, text: str, metadata: dict[str, Any] | None = None) -> "SourceDocument":
        data = path.read_bytes()
        digest = sha256_bytes(data)
        return cls(
            source_id=digest[:16],
            path=str(path),
            name=path.name,
            suffix=path.suffix.lower(),
            size_bytes=len(data),
            sha256=digest,
            ingested_at=now_iso(),
            text=text,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExtractionResult:
    source_id: str
    source_name: str
    schema_name: str
    model: str
    created_at: str
    preserved_text: str
    structured_data: dict[str, Any]
    review: dict[str, Any]
    deliverables: list[dict[str, Any]]
    confidence_notes: list[str]
    triggers_run: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
