"""local_llm 與 building_match Tool 測試。Ollama 一律用假的 requests 取代，不打本機服務。

大樓清單用 data/ncku_buildings.json 的真實內容（從成大 GIS 撈的）。
"""
import json

import pytest
import requests

from api import Settings, load_settings
from commute_agent.tools import building_match, local_llm

ROWS = building_match.load_buildings()


class FakeResponse:
    def __init__(self, payload=None, status_code=200, bad_json=False):
        self._payload, self.status_code, self._bad = payload, status_code, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


def _settings(model="gemma3:4b") -> Settings:
    base = load_settings(load_env_file=False)
    return Settings(**{**base.__dict__, "ollama_url": "http://ollama.test", "local_llm_model": model})


def _tags(*names):
    return FakeResponse({"models": [{"name": n} for n in names]})


def _chat(answer: dict):
    return FakeResponse({"message": {"content": json.dumps(answer, ensure_ascii=False)}})


# ---------- 清單本身 ----------

def test_building_list_is_present_and_has_expected_buildings():
    names = {r["name"] for r in ROWS}
    assert len(ROWS) > 100
    assert "B501 資訊工程系館" in names and "A006 唯農大樓" in names
    assert all(r.get("id") and r.get("name") for r in ROWS)


def test_document_hides_gis_ids_but_keeps_aliases():
    doc = building_match.build_document(ROWS)
    assert "B501 資訊工程系館" in doc
    assert "｜資訊系館" in doc          # 別名要給模型
    assert "\nB029" not in doc          # GIS 內部 id 不給，兩組代碼會搞混它


# ---------- match_row（純函式） ----------

@pytest.mark.parametrize("answer,expected", [
    ("B501 資訊工程系館", "B029"),
    ("B501 資訊工程系館｜資訊系館", "B029"),   # 多帶別名
    ("雲平大樓東棟", "A029"),                  # 掉了門牌代碼
    ("D501 奇美大樓｜電機工程系｜奇美樓", "D202"),
])
def test_match_row_tolerates_model_formatting(answer, expected):
    assert building_match.match_row(ROWS, answer)["id"] == expected


@pytest.mark.parametrize("answer", ["", None, "B104 修齊大樓", "霍格華茲城堡"])
def test_match_row_rejects_invented_names(answer):
    # 「修齊大樓」在清單裡是 A104；模型回 B104 這種對不上的代碼，
    # 子字串比對仍會對到 A104——這是刻意的：名字對就好，代碼由清單決定
    row = building_match.match_row(ROWS, answer)
    if answer == "B104 修齊大樓":
        assert row["id"] == "A022"
    else:
        assert row is None


# ---------- local_llm ----------

def test_is_available_false_when_ollama_down(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(local_llm.requests, "get", boom)
    assert local_llm.is_available(_settings()) is False


def test_is_available_false_when_model_not_pulled(monkeypatch):
    monkeypatch.setattr(local_llm.requests, "get", lambda *a, **k: _tags("llama3:8b"))
    assert local_llm.is_available(_settings()) is False


def test_is_available_accepts_full_tag_variants(monkeypatch):
    monkeypatch.setattr(local_llm.requests, "get", lambda *a, **k: _tags("gemma3:4b-it-q4_K_M"))
    assert local_llm.is_available(_settings()) is True


def test_chat_json_returns_dict_and_requests_json_format(monkeypatch):
    seen = {}

    def fake_post(url, json=None, **k):
        seen["url"], seen["body"] = url, json
        return _chat({"building_name": "A006 唯農大樓", "confidence": 0.9, "reason": "x"})
    monkeypatch.setattr(local_llm.requests, "post", fake_post)
    out = local_llm.chat_json("sys", "user", _settings())
    assert out["building_name"] == "A006 唯農大樓"
    assert seen["url"] == "http://ollama.test/api/chat"
    assert seen["body"]["format"] == "json" and seen["body"]["options"]["temperature"] == 0


def test_chat_json_errors_are_wrapped(monkeypatch):
    monkeypatch.setattr(local_llm.requests, "post", lambda *a, **k: FakeResponse(status_code=500))
    with pytest.raises(local_llm.LocalLLMError):
        local_llm.chat_json("s", "u", _settings())
    monkeypatch.setattr(local_llm.requests, "post",
                        lambda *a, **k: FakeResponse({"message": {"content": "not json"}}))
    with pytest.raises(local_llm.LocalLLMError):
        local_llm.chat_json("s", "u", _settings())


# ---------- guess_building ----------

@pytest.fixture
def ollama(monkeypatch):
    state = {"answer": {"building_name": None, "confidence": 0.1, "reason": "校外"}}
    monkeypatch.setattr(local_llm.requests, "get", lambda *a, **k: _tags("gemma3:4b"))
    monkeypatch.setattr(local_llm.requests, "post", lambda *a, **k: _chat(state["answer"]))
    return state


def test_guess_maps_model_answer_to_gis_id(ollama):
    ollama["answer"] = {"building_name": "B501 資訊工程系館｜資訊系館", "confidence": 0.95,
                        "reason": "文字含資訊系館"}
    out = building_match.guess_building("資訊系館4264", _settings())
    assert out["status"] == "ok"
    assert out["build_id"] == "B029" and out["name"] == "B501 資訊工程系館"
    assert out["confidence"] == 0.95 and out["source"] == "gemma_local"


def test_guess_null_means_not_found(ollama):
    out = building_match.guess_building("臺南市東區怡東路21號", _settings())
    assert out["status"] == "not_found" and out["build_id"] == ""


def test_guess_invented_name_is_not_found_but_kept_for_inspection(ollama):
    ollama["answer"] = {"building_name": "霍格華茲城堡", "confidence": 0.99, "reason": ""}
    out = building_match.guess_building("魔法學校", _settings())
    assert out["status"] == "not_found"
    assert out["model_answer"] == "霍格華茲城堡"


def test_guess_unavailable_when_ollama_down(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(local_llm.requests, "get", boom)
    out = building_match.guess_building("唯農大樓", _settings())
    assert out["status"] == "unavailable" and "Ollama" in out["error_message"]


def test_guess_empty_text_is_error(ollama):
    assert building_match.guess_building("   ", _settings())["status"] == "error"
