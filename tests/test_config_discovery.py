"""Tests for supamem.config discovery ladder (plan 80.6-03)."""
from __future__ import annotations

from pathlib import Path

import pytest

from supamem.config import ConfigChain, ResolvedConfig, load_config


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _clear_supamem_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear env vars that influence load_config so tests are hermetic."""
    for k in ("SUPAMEM_CONFIG", "QDRANT_URL", "QDRANT_API_KEY", "COLLECTION_NAME", "EMBEDDING_MODEL"):
        monkeypatch.delenv(k, raising=False)


def test_defaults_when_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 1: no config files anywhere → all defaults, chain says 'default'."""
    _clear_supamem_env(monkeypatch)
    cfg, chain = load_config(tmp_path)
    assert isinstance(cfg, ResolvedConfig)
    assert isinstance(chain, ConfigChain)
    assert cfg.collection == "dev_memory_tuned_hybrid"
    assert chain.collection == "default"


def test_supamem_toml_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 2: .supamem/config.toml resolves with chain='supamem_toml'."""
    _clear_supamem_env(monkeypatch)
    _write(tmp_path / ".supamem/config.toml", '[supamem]\ncollection = "x"\n')
    cfg, chain = load_config(tmp_path)
    assert cfg.collection == "x"
    assert chain.collection == "supamem_toml"


def test_pyproject_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 3: pyproject.toml [tool.supamem] resolves with chain='pyproject'."""
    _clear_supamem_env(monkeypatch)
    _write(tmp_path / "pyproject.toml", '[tool.supamem]\ncollection = "y"\n')
    cfg, chain = load_config(tmp_path)
    assert cfg.collection == "y"
    assert chain.collection == "pyproject"


def test_supamem_toml_beats_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 4: when both exist, .supamem/config.toml wins over pyproject."""
    _clear_supamem_env(monkeypatch)
    _write(tmp_path / "pyproject.toml", '[tool.supamem]\ncollection = "from-pyproject"\n')
    _write(tmp_path / ".supamem/config.toml", '[supamem]\ncollection = "from-supamem"\n')
    cfg, chain = load_config(tmp_path)
    assert cfg.collection == "from-supamem"
    assert chain.collection == "supamem_toml"


def test_env_var_legacy_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 5: legacy single-key env vars (QDRANT_URL etc.) win over file rungs."""
    _clear_supamem_env(monkeypatch)
    _write(tmp_path / ".supamem/config.toml", '[supamem]\nqdrant_url = "http://from-toml"\n')
    monkeypatch.setenv("QDRANT_URL", "http://from-env")
    cfg, chain = load_config(tmp_path)
    assert cfg.qdrant_url == "http://from-env"
    assert chain.qdrant_url == "env"


def test_supamem_config_env_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 6: SUPAMEM_CONFIG=<path> wins over .supamem/config.toml file rung."""
    _clear_supamem_env(monkeypatch)
    _write(tmp_path / ".supamem/config.toml", '[supamem]\ncollection = "from-default-file"\n')
    explicit = tmp_path / "custom.toml"
    _write(explicit, '[supamem]\ncollection = "from-explicit"\n')
    monkeypatch.setenv("SUPAMEM_CONFIG", str(explicit))
    cfg, chain = load_config(tmp_path)
    assert cfg.collection == "from-explicit"
    assert chain.collection == "env"


def test_autodetect_from_claude_insights(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 7: presence of .claude/insights/* seeds sources via auto_detect."""
    _clear_supamem_env(monkeypatch)
    _write(tmp_path / ".claude/insights/foo.md", "# foo\n")
    cfg, chain = load_config(tmp_path)
    assert cfg.sources == [".claude/insights/", ".claude/rules/"]
    assert chain.sources == "auto_detect"


def test_partial_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test 8: partial config in .supamem/config.toml; other fields fall to defaults."""
    _clear_supamem_env(monkeypatch)
    _write(tmp_path / ".supamem/config.toml", '[supamem]\ncollection = "x"\n')
    cfg, chain = load_config(tmp_path)
    assert cfg.collection == "x"
    assert chain.collection == "supamem_toml"
    assert cfg.embedder == "minilm"
    assert chain.embedder == "default"


def test_missing_supamem_config_env_path_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 9: SUPAMEM_CONFIG=/nonexistent → FileNotFoundError mentioning path."""
    _clear_supamem_env(monkeypatch)
    bogus = tmp_path / "does-not-exist.toml"
    monkeypatch.setenv("SUPAMEM_CONFIG", str(bogus))
    with pytest.raises(FileNotFoundError) as exc_info:
        load_config(tmp_path)
    assert str(bogus) in str(exc_info.value)


def test_malformed_toml_raises_with_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test 10: invalid TOML in .supamem/config.toml → RuntimeError with path."""
    _clear_supamem_env(monkeypatch)
    bad = tmp_path / ".supamem/config.toml"
    _write(bad, "this is not = valid TOML [unclosed\n")
    with pytest.raises(RuntimeError) as exc_info:
        load_config(tmp_path)
    msg = str(exc_info.value)
    assert msg.startswith("supamem: failed to parse")
    assert str(bad) in msg
