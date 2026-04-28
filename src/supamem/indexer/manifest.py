"""Hash manifest for the supamem indexer (D-21 schema, dirty-check).

Schema: ``{"<abs_doc_path>": {"prod": "<sha>", "tuned": "<sha>"}}``.
Legacy flat format ``{"<doc>": "<sha>"}`` is read as prod-side hash with
empty tuned, so an old manifest reindexes everything once on first tuned run.
Mirrors ``softchat/scripts/embed-dev-memories.py:load_hash_manifest`` exactly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

EntryDict = dict[str, str]


@dataclass
class Manifest:
    entries: dict[str, EntryDict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Manifest:
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        out: dict[str, EntryDict] = {}
        for k, v in raw.items():
            if isinstance(v, str):
                out[k] = {"prod": v, "tuned": ""}
            elif isinstance(v, dict):
                out[k] = {
                    "prod": str(v.get("prod") or ""),
                    "tuned": str(v.get("tuned") or ""),
                }
        return cls(entries=out)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.entries, indent=2, sort_keys=True), encoding="utf-8"
        )

    def needs_index(self, doc_path: str, current_hash: str, target: str) -> bool:
        """True if the per-target hash differs (or doc unknown)."""
        if doc_path not in self.entries:
            return True
        return self.entries[doc_path].get(target, "") != current_hash

    def update(self, doc_path: str, target: str, sha: str) -> None:
        entry = self.entries.setdefault(doc_path, {"prod": "", "tuned": ""})
        entry[target] = sha
