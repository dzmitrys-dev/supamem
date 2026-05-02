"""Retrieval result types — Pydantic models exposed to callers."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class RetrievedChunk(BaseModel):
    """A single hit returned by a retrieval backend."""

    model_config = ConfigDict(frozen=True)

    id: str
    text: str
    score: float
    source_path: Optional[str] = None
    file_path: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    payload: Optional[dict[str, Any]] = None
    rerank_score: Optional[float] = None
