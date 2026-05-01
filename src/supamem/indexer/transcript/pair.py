"""Turn-pair extraction from a Claude Code session event stream (Phase 06 D-03/D-05).

Strict turn-pair drawer (D-03) = 1 user event + the next contiguous assistant
event(s) until the next user event. One Q+A pair = one chunk downstream.

Filters (Pitfall §5 + D-19 boundary):
- ``isSidechain == True`` events skipped (sub-agent calls; v1 deferred —
  including them would corrupt ``turn_index`` arithmetic).
- ``summary`` (compaction checkpoint, not Q+A), ``file-history-snapshot``
  (anthropics/claude-code#36583 uuid collision), ``queue-operation``,
  ``system`` events all skipped.

D-05: An interrupted user with no assistant reply still emits a pair —
user prompts may carry decision context worth retrieving.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Event types that are NOT Q+A turns. Filtered before pair grouping.
_SKIP_TYPES: frozenset[str] = frozenset(
    {"summary", "file-history-snapshot", "queue-operation", "system"}
)


@dataclass
class TurnPair:
    """One Q+A drawer per D-03 — feeds the chunker (Plan 06-02).

    Carries the full original event dicts (not just uuids) because the
    chunker needs ``message.content`` to render the drawer text.
    """

    turn_index: int
    user_uuid: str
    assistant_uuids: list[str] = field(default_factory=list)
    user_event: dict = field(default_factory=dict)
    assistant_events: list[dict] = field(default_factory=list)


def extract_pairs(events: list[dict]) -> list[TurnPair]:
    """Group an event stream into :class:`TurnPair` instances per D-03.

    Skips ``isSidechain=True`` (Pitfall §5) and non-Q+A types
    (``summary``, ``file-history-snapshot``, ``queue-operation``, ``system``).
    Emits an interrupted-user pair with empty ``assistant_uuids`` per D-05.
    Assistant events appearing before any user event are silently dropped
    (rare; system bootstrap).
    """
    pairs: list[TurnPair] = []
    current: TurnPair | None = None
    turn_idx = 0
    for evt in events:
        if evt.get("isSidechain"):
            continue
        t = evt.get("type")
        if t in _SKIP_TYPES:
            continue
        if t == "user":
            if current is not None:
                pairs.append(current)
            current = TurnPair(
                turn_index=turn_idx,
                user_uuid=evt["uuid"],
                user_event=evt,
            )
            turn_idx += 1
        elif t == "assistant":
            if current is None:
                # assistant with no preceding user (rare; system bootstrap) — skip
                continue
            current.assistant_uuids.append(evt["uuid"])
            current.assistant_events.append(evt)
    if current is not None:
        pairs.append(current)  # D-05: emit even if assistant_uuids is empty
    return pairs
