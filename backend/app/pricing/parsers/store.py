"""Versioned tariff store (JSON on disk). A set version = hash of its files' checksums.

Layout:  <store>/index.json
         <store>/<set_id>/<version-hash>.json        records + exceptions + file checksums
         <store>/<set_id>/originals/<sha>-<name>     the original source bytes

Re-ingesting identical bytes is a no-op. A changed file creates a new version and makes it current;
older
versions are never rewritten, so a quote committed against version X can always reload X's evidence.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

from .base import LaneRate


class TariffStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def _index(self) -> dict:
        return json.loads(self.index_path.read_text()) if self.index_path.exists() else {}

    def _write_index(self, idx: dict) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, indent=1, sort_keys=True))
        tmp.replace(self.index_path)

    def _vpath(self, set_id: str, version: str) -> Path:
        return self.root / set_id / f"{version.split('@')[-1]}.json"

    def commit(self, result, paths: list[Path]) -> bool:
        """Store a SetResult. Returns True if this is a new version."""
        idx = self._index()
        entry = idx.setdefault(result.set_id, {"current": None, "versions": []})
        vp = self._vpath(result.set_id, result.version)
        new = not vp.exists()
        if new:
            vp.parent.mkdir(parents=True, exist_ok=True)
            orig = vp.parent / "originals"
            orig.mkdir(exist_ok=True)
            for p in paths:
                f = next(x for x in result.files if x["name"] == p.name)
                dst = orig / f"{f['checksum'][:16]}-{p.name}"
                if not dst.exists():
                    shutil.copy2(p, dst)
            data = result.to_dict()
            data["ingested_at"] = dt.datetime.now(dt.UTC).isoformat()
            vp.write_text(json.dumps(data, indent=1))
            entry["versions"].append(result.version)
        entry["current"] = result.version
        self._write_index(idx)
        return new

    def versions(self, set_id: str) -> list[str]:
        return self._index().get(set_id, {}).get("versions", [])

    def load_version(self, set_id: str, version: str) -> dict:
        return json.loads(self._vpath(set_id, version).read_text())

    def current_records(self) -> list[LaneRate]:
        out = []
        for set_id, e in sorted(self._index().items()):
            if e.get("current"):
                out += [
                    LaneRate.from_dict(r)
                    for r in self.load_version(set_id, e["current"])["records"]
                ]
        return out

    def record(self, set_id: str, version: str, origin: str, dest: str) -> LaneRate | None:
        """Reload the exact lane a past decision used (original evidence, even after an update)."""
        for r in self.load_version(set_id, version)["records"]:
            if r["origin"] == origin and r["destination"] == dest:
                return LaneRate.from_dict(r)
        return None
