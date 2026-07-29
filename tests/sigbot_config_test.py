import sys

import pytest

from sigbot import config as config_mod

_BASE = """
signal:
  api_url: http://localhost:8080/
  bot_number: "+15555550100"
bot_name: Testy
db_path: t.db
"""


def _load(tmp_path, extra=""):
    p = tmp_path / "sigbot.yaml"
    p.write_text(_BASE + extra)
    return config_mod.load(p)


def test_defaults_and_url_normalization(tmp_path):
    cfg = _load(tmp_path)
    assert cfg.signal.api_url == "http://localhost:8080"  # trailing slash stripped
    assert cfg.api.port == 8100 and cfg.anthropic_api_key_env == "ANTHROPIC_API_KEY"


def test_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    assert _load(tmp_path).anthropic_api_key == "sk-ant-from-env"


def test_key_missing_everywhere(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="anthropic_api_key_file"):
        _ = _load(tmp_path).anthropic_api_key


def test_key_inline(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    cfg = _load(tmp_path, 'anthropic_api_key: "sk-ant-inline"\n')
    assert cfg.anthropic_api_key == "sk-ant-inline"  # inline beats env


def test_key_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    keyfile = tmp_path / "anthropic.key"
    keyfile.write_text("sk-ant-from-file\n")
    cfg = _load(tmp_path, f"anthropic_api_key_file: {keyfile}\n")
    assert cfg.anthropic_api_key == "sk-ant-from-file"  # whitespace stripped


def test_key_file_beats_env_and_loses_to_inline(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    keyfile = tmp_path / "anthropic.key"
    keyfile.write_text("sk-ant-from-file")
    cfg = _load(tmp_path, f"anthropic_api_key_file: {keyfile}\n")
    assert cfg.anthropic_api_key == "sk-ant-from-file"
    cfg = _load(tmp_path,
                f'anthropic_api_key: "sk-ant-inline"\nanthropic_api_key_file: {keyfile}\n')
    assert cfg.anthropic_api_key == "sk-ant-inline"


def test_key_file_missing_or_empty(tmp_path):
    cfg = _load(tmp_path, "anthropic_api_key_file: nope.key\n")
    with pytest.raises(RuntimeError, match="could not read"):
        _ = cfg.anthropic_api_key
    (tmp_path / "empty.key").write_text("  \n")
    cfg = _load(tmp_path, f"anthropic_api_key_file: {tmp_path / 'empty.key'}\n")
    with pytest.raises(RuntimeError, match="is empty"):
        _ = cfg.anthropic_api_key


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
