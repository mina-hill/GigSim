from pathlib import Path

import pytest
import yaml

from agent import llm
from agent.config import REPO_ROOT, load_config
from agent.llm_cache import SQLiteLLMCache


def test_env_interpolation_and_profile(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILE", "test")
    monkeypatch.setenv("GIGSIM_BASE_URL", "http://sim:9999")
    cfg = load_config()
    assert cfg.models.profile == "test"
    assert cfg.marketplace["gigsim"]["base_url"] == "http://sim:9999"
    assert cfg.model_for_node("writer").provider == "fake"


def test_every_node_resolves_in_every_profile():
    cfg = load_config()
    for name, profile in cfg.models.profiles.items():
        for node, tier in cfg.models.nodes.items():
            assert tier in profile.tiers, f"profile {name} has no tier {tier} for node {node}"


def test_auto_send_cannot_be_enabled(tmp_path):
    raw = yaml.safe_load((REPO_ROOT / "agent" / "config.yaml").read_text())
    raw["autonomy"]["auto_send"] = True
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="auto_send"):
        load_config(path)


def test_fake_model_is_deterministic_and_queueable(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILE", "test")
    cfg = load_config()
    model = llm.get_chat_model("writer", cfg=cfg, use_cache=False)
    a = model.invoke("hello").content
    b = model.invoke("hello").content
    assert a == b and a.startswith("[fake-strong:")
    llm.queue_fake_responses("fake-strong", ["queued answer"])
    assert model.invoke("anything").content == "queued answer"
    llm.clear_fake_responses()


def test_sqlite_cache_round_trip(tmp_path: Path):
    from langchain_core.outputs import Generation

    cache = SQLiteLLMCache(tmp_path / "c.sqlite")
    assert cache.lookup("p", "m") is None
    cache.update("p", "m", [Generation(text="hi")])
    hit = cache.lookup("p", "m")
    assert hit is not None and hit[0].text == "hi"
    assert cache.lookup("p", "other-model") is None
    cache.clear()
    assert cache.size() == 0


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("MODEL_PROFILE", "open")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = load_config(env_file="/nonexistent/.env")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        llm.get_chat_model("writer", cfg=cfg)
