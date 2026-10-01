"""Key resolution: config wins, else generate once and reuse; opt-out honoured."""
import os
import pytest
from llm_router.config import Settings, load_config
from llm_router.keys import ensure_master_key, key_path, new_key


def test_generated_then_reused(tmp_path, monkeypatch):
    monkeypatch.delenv("LLMROUTER_MASTER_KEY", raising=False)
    monkeypatch.delenv("LLMROUTER_NO_KEY", raising=False)
    monkeypatch.setenv("LLMROUTER_KEY_FILE", str(tmp_path / "router.key"))
    cfg = tmp_path / "config.yaml"
    cfg.write_text("listen: 127.0.0.1:9\n", encoding="utf-8")
    st = load_config(cfg)
    assert st.key_source == "generated" and st.master_keys[0].startswith("sk-router-")
    assert key_path(cfg).read_text(encoding="utf-8").strip() == st.master_keys[0]
    assert load_config(cfg).key_source == "reused"          # same key after restart
    assert load_config(cfg).master_keys == st.master_keys


def test_config_and_env_and_nokey(tmp_path, monkeypatch):
    monkeypatch.setenv("LLMROUTER_KEY_FILE", str(tmp_path / "router.key"))
    s = Settings(master_keys=["sk-from-config"])
    assert ensure_master_key(s, tmp_path / "config.yaml") == "config"
    assert s.master_keys == ["sk-from-config"]
    monkeypatch.setenv("LLMROUTER_MASTER_KEY", "sk-env")
    s2 = Settings()
    assert ensure_master_key(s2, tmp_path / "config.yaml") == "env"
    assert s2.master_keys == ["sk-env"]
    monkeypatch.setenv("LLMROUTER_NO_KEY", "1")
    s3 = Settings()
    assert ensure_master_key(s3, tmp_path / "config.yaml") == "disabled"
    assert s3.master_keys == []
    assert not (tmp_path / "router.key").exists()


def test_rotate_mints_a_new_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LLMROUTER_MASTER_KEY", raising=False)
    monkeypatch.delenv("LLMROUTER_NO_KEY", raising=False)
    kp = tmp_path / "router.key"
    monkeypatch.setenv("LLMROUTER_KEY_FILE", str(kp))
    st = Settings()
    first = ensure_master_key(st, tmp_path / "config.yaml")
    assert first == "generated"
    st2 = Settings()
    assert ensure_master_key(st2, tmp_path / "config.yaml", rotate=True) == "generated"
    assert st2.master_keys[0] != st.master_keys[0]
    assert kp.read_text(encoding="utf-8").strip() == st2.master_keys[0]


def test_new_key_shape():
    k = new_key()
    assert k.startswith("sk-router-") and len(k) > 40 and k != new_key()
