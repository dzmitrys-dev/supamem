"""Hash manifest for the supamem indexer (D-21 schema, dirty-check).

Schema: ``{"<abs_doc_path>": {"prod": "<sha>", "tuned": "<sha>"}}``.
Legacy flat format ``{"<doc>": "<sha>"}`` is read as prod-side hash with
empty tuned, so an old manifest reindexes everything once on first tuned run.
Mirrors ``softchat/scripts/embed-dev-memories.py:load_hash_manifest`` exactly.

Plan 06-03 extends this additively (R-04): a top-level ``__transcripts__`` key
maps ``session_uuid -> {message_uuid: {content_hash, indexed_at}}`` so
transcript chunks dedupe per-message (D-25, D-27) without a SQLite migration.
File-keyed entries roundtrip byte-stable when no transcripts are present.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

EntryDict = dict[str, str]
TRANSCRIPTS_KEY = "__transcripts__"
# Plan 07-02 D-10: classifier hash is stored under a top-level reserved key,
# emitted only when not None so Phase-6-era manifests round-trip byte-stable.
CLASSIFIER_HASH_KEY = "__classifier_hash__"
# Phase 9 D-NULL-03 — gate for one-shot eager validity migration sweep.
# Set to ``supamem.__version__`` on first successful sweep; absent on
# Phase-8-era manifests so the gate trips exactly once post-upgrade.
# Mirrors :data:`CLASSIFIER_HASH_KEY` precedent (byte-stable rollback when
# the field is None — the key is omitted from the JSON dump entirely).
VALIDITY_MIGRATION_KEY = "__validity_migration__"
# Phase 11 D-PFX-06 — gate for one-shot eager path-prefixes migration sweep.
# Set to ``supamem.__version__`` on first successful sweep; absent on
# pre-Phase-11 manifests so the gate trips exactly once post-upgrade.
# Mirrors :data:`VALIDITY_MIGRATION_KEY` precedent (byte-stable rollback when
# the field is None — the key is omitted from the JSON dump entirely).
PATH_PREFIXES_MIGRATION_KEY = "__path_prefixes_migration__"


@dataclass
class Manifest:
    entries: dict[str, EntryDict] = field(default_factory=dict)
    transcripts: dict[str, dict[str, dict]] = field(default_factory=dict)
    # transcripts[session_uuid][message_uuid] = {"content_hash": "...", "indexed_at": "..."}
    classifier_hash: Optional[str] = None
    # Plan 07-02 D-10: sha256 digest of [classifier.rooms]; drift triggers a
    # set_payload sweep on next ``run_index``. Missing key on Phase-6 manifests
    # loads as None which trips the gate exactly once on first post-upgrade run.
    validity_migration: Optional[str] = None
    # Phase 9 D-NULL-03: stamped to ``supamem.__version__`` on first successful
    # eager-validity-migration sweep. Missing key on Phase-8-era manifests
    # loads as None which trips the gate exactly once on first post-upgrade
    # run (mirrors classifier_hash gate). Byte-stable rollback: the key is
    # NOT emitted to JSON when the field is None.
    path_prefixes_migration: Optional[str] = None
    # Phase 11 D-PFX-06: stamped to ``supamem.__version__`` on first successful
    # eager-path-prefixes-migration sweep. Missing key on pre-Phase-11 manifests
    # loads as None which trips the gate exactly once on first post-upgrade
    # run (mirrors validity_migration gate). Byte-stable rollback: the key is
    # NOT emitted to JSON when the field is None.

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
        # R-04: pop the namespaced transcripts key first.
        raw_transcripts = raw.get(TRANSCRIPTS_KEY, {})
        # D-10: classifier hash is a top-level reserved key; missing → None.
        raw_classifier_hash = raw.get(CLASSIFIER_HASH_KEY)
        classifier_hash = (
            str(raw_classifier_hash) if isinstance(raw_classifier_hash, str) else None
        )
        # Phase 9 D-NULL-03: validity-migration gate — top-level reserved key,
        # missing → None (gate trips once on first post-upgrade ``run_index``).
        raw_validity_migration = raw.get(VALIDITY_MIGRATION_KEY)
        validity_migration = (
            str(raw_validity_migration)
            if isinstance(raw_validity_migration, str)
            else None
        )
        # Phase 11 D-PFX-06: path-prefixes migration gate — top-level reserved
        # key, missing → None (gate trips once on first post-upgrade run_index).
        raw_path_prefixes_migration = raw.get(PATH_PREFIXES_MIGRATION_KEY)
        path_prefixes_migration = (
            str(raw_path_prefixes_migration)
            if isinstance(raw_path_prefixes_migration, str)
            else None
        )
        transcripts: dict[str, dict[str, dict]] = {}
        if isinstance(raw_transcripts, dict):
            for session_uuid, bucket in raw_transcripts.items():
                if not isinstance(bucket, dict):
                    continue
                inner: dict[str, dict] = {}
                for msg_uuid, rec in bucket.items():
                    if isinstance(rec, dict):
                        inner[msg_uuid] = {
                            "content_hash": str(rec.get("content_hash") or ""),
                            "indexed_at": str(rec.get("indexed_at") or ""),
                        }
                transcripts[session_uuid] = inner

        out: dict[str, EntryDict] = {}
        for k, v in raw.items():
            # Filter the transcripts namespace AND any future ``__x__`` keys
            # so they cannot accidentally pollute file-keyed entries.
            if k.startswith("__") and k.endswith("__"):
                continue
            if isinstance(v, str):
                out[k] = {"prod": v, "tuned": ""}
            elif isinstance(v, dict):
                out[k] = {
                    "prod": str(v.get("prod") or ""),
                    "tuned": str(v.get("tuned") or ""),
                }
        return cls(
            entries=out,
            transcripts=transcripts,
            classifier_hash=classifier_hash,
            validity_migration=validity_migration,
            path_prefixes_migration=path_prefixes_migration,
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict = dict(self.entries)
        # Lock: only emit the key when transcripts exist, so legacy manifest
        # bytes stay identical when no transcripts have been indexed.
        if self.transcripts:
            payload[TRANSCRIPTS_KEY] = self.transcripts
        # D-10: emit classifier hash only when set so legacy manifests (no
        # classifier) keep byte-identical JSON output.
        if self.classifier_hash is not None:
            payload[CLASSIFIER_HASH_KEY] = self.classifier_hash
        # Phase 9 D-NULL-03: emit validity_migration only when set so Phase-8
        # manifests round-trip byte-stable (mirrors classifier_hash convention).
        if self.validity_migration is not None:
            payload[VALIDITY_MIGRATION_KEY] = self.validity_migration
        # Phase 11 D-PFX-06: emit path_prefixes_migration only when set so
        # pre-Phase-11 manifests round-trip byte-stable (mirrors
        # validity_migration / classifier_hash convention).
        if self.path_prefixes_migration is not None:
            payload[PATH_PREFIXES_MIGRATION_KEY] = self.path_prefixes_migration
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def needs_index(self, doc_path: str, current_hash: str, target: str) -> bool:
        """True if the per-target hash differs (or doc unknown)."""
        if doc_path not in self.entries:
            return True
        return self.entries[doc_path].get(target, "") != current_hash

    def update(self, doc_path: str, target: str, sha: str) -> None:
        entry = self.entries.setdefault(doc_path, {"prod": "", "tuned": ""})
        entry[target] = sha

    # ───── Transcript per-message dedupe (Plan 06-03, R-04, D-25, D-27) ────

    def transcript_needs_index(
        self, session_uuid: str, message_uuid: str, content_hash: str
    ) -> bool:
        """True if (session, message) is unseen or its content_hash changed."""
        bucket = self.transcripts.get(session_uuid, {})
        rec = bucket.get(message_uuid)
        return rec is None or rec.get("content_hash") != content_hash

    def transcript_update(
        self, session_uuid: str, message_uuid: str, content_hash: str
    ) -> None:
        """Record (session, message, hash) with an ISO-8601 UTC timestamp."""
        self.transcripts.setdefault(session_uuid, {})[message_uuid] = {
            "content_hash": content_hash,
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }
