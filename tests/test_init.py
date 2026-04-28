"""Tests for ``supamem.init.run_init`` (Plan 80.6-08)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from supamem.init import _slugify, probe_qdrant, run_init


def test_probe_qdrant_returns_false_on_connection_refused() -> None:
    """A closed port → False, never raises."""
    assert probe_qdrant("http://127.0.0.1:1", timeout=0.5) is False


def test_run_init_writes_config_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mock Qdrant client; assert .supamem/config.toml has [supamem] collection key."""
    import supamem.init as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)

    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
    monkeypatch.setattr(mod, "_get_client", lambda url, api_key="": fake_client)

    rc = run_init(tmp_path, yes=True)
    assert rc == 0
    cfg_path = tmp_path / ".supamem" / "config.toml"
    assert cfg_path.exists()
    body = cfg_path.read_text(encoding="utf-8")
    assert "[supamem]" in body
    assert "collection" in body
    assert "supamem-" in body


def test_run_init_skips_create_when_collection_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse to create_collection if name already exists (T-80.6-08-04)."""
    import supamem.init as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)

    existing = MagicMock()
    existing.name = f"supamem-{tmp_path.name.lower().replace('_', '-')}"
    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[existing])
    monkeypatch.setattr(mod, "_get_client", lambda url, api_key="": fake_client)

    rc = run_init(tmp_path, yes=True)
    assert rc == 4
    fake_client.create_collection.assert_not_called()


def test_run_init_uses_slug_from_cwd_basename(tmp_path: Path) -> None:
    """A cwd named 'My-Proj_X' → slug 'my-proj-x' → collection 'supamem-my-proj-x'."""
    target = tmp_path / "My-Proj_X"
    target.mkdir()
    assert _slugify(target.name) == "my-proj-x"


def test_run_init_refuses_to_overwrite_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-80.6-08-02: refuse to clobber .supamem/config.toml without --force."""
    import supamem.init as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: True)
    fake_client = MagicMock()
    fake_client.get_collections.return_value = MagicMock(collections=[])
    monkeypatch.setattr(mod, "_get_client", lambda url, api_key="": fake_client)

    cfg_dir = tmp_path / ".supamem"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text("[supamem]\ncollection = 'pre-existing'\n", encoding="utf-8")

    rc = run_init(tmp_path, yes=True)
    assert rc == 3  # refuse-to-overwrite exit code
    fake_client.create_collection.assert_not_called()


def test_run_init_aborts_when_qdrant_down_without_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Qdrant is unreachable AND yes=False, abort cleanly without prompting."""
    import supamem.init as mod

    monkeypatch.setattr(mod, "probe_qdrant", lambda url, timeout=2.0: False)
    rc = run_init(tmp_path, yes=False)
    assert rc == 2
