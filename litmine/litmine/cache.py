"""Content-addressed stage cache.

Each stage stores JSON blobs under cache/<stage>/<key>.json where key is a
sha256 over the stage name, prompt/version identifiers, model identity and the
source-document hash. Changing a prompt version or the model changes the key,
so only the affected stage reruns. Export never touches the cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cache_key(**parts: Any) -> str:
    return sha256_text(stable_json(parts))


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path(self, stage: str, key: str) -> Path:
        return self.root / stage / f"{key}.json"

    def get(self, stage: str, key: str) -> Any | None:
        p = self.path(stage, key)
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, stage: str, key: str, value: Any) -> Any:
        p = self.path(stage, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        # atomic write so a crash mid-write never leaves a torn cache entry
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=1, sort_keys=True)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return value

    def has(self, stage: str, key: str) -> bool:
        return self.path(stage, key).exists()

    def list(self, stage: str) -> list[Path]:
        d = self.root / stage
        return sorted(d.glob("*.json")) if d.exists() else []
