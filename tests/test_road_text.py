"""road_text Tool 測試。本機模型用假的 requests 取代。"""
import json

import pytest
import requests

from api import Settings, load_settings
from commute_agent.tools import local_llm, road_text


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


def _settings() -> Settings:
    base = load_settings(load_env_file=False)
    return Settings(**{**base.__dict__, "ollama_url": "http://ollama.test", "local_llm_model": "gemma3:4b"})


@pytest.fixture
def ollama(monkeypatch):
    state = {"answer": {}}
    monkeypatch.setattr(local_llm.requests, "get",
                        lambda *a, **k: FakeResponse({"models": [{"name": "gemma3:4b"}]}))
    monkeypatch.setattr(local_llm.requests, "post",
                        lambda *a, **k: FakeResponse({"message": {"content": json.dumps(state["answer"], ensure_ascii=False)}}))
    return state


def test_noise_prefix_and_section_are_separated(ollama):
    ollama["answer"] = {"district": None, "roads": ["長和路", "北外環路"],
                        "section": "長和路一段222巷100弄口至北外環路口", "is_intersection": False,
                        "cleaned": "北外環 長和路一段222巷100弄口至北外環路口"}
    out = road_text.normalize_road_text("null北外環 長和路一段222巷100弄口至北外環路口", _settings())
    assert out["status"] == "ok" and out["roads"] == ["長和路", "北外環路"]
    assert not out["cleaned"].startswith("null")


def test_intersection_flag_and_district(ollama):
    ollama["answer"] = {"district": "北區", "roads": ["公園路", "公園南路"], "section": None,
                        "is_intersection": True, "cleaned": "北區公園路與公園南路口"}
    out = road_text.normalize_road_text("臺南市北區公園路和公園南路的交叉路口", _settings())
    assert out["district"] == "北區" and out["is_intersection"] is True


def test_bad_model_output_degrades_to_original_text(ollama):
    ollama["answer"] = {"roads": "not a list", "cleaned": ""}
    out = road_text.normalize_road_text("永康區中山路", _settings())
    assert out["status"] == "ok" and out["roads"] == [] and out["cleaned"] == "永康區中山路"


def test_unavailable_keeps_original_text(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(local_llm.requests, "get", boom)
    out = road_text.normalize_road_text("永康區中山路", _settings())
    assert out["status"] == "unavailable" and out["cleaned"] == "永康區中山路"


def test_empty_is_error(ollama):
    assert road_text.normalize_road_text(" ", _settings())["status"] == "error"
