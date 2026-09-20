"""course_mail Tool 與 mail_update Skill 測試。本機模型一律用假的 requests 取代。"""
import json

import pytest
import requests

from api import Settings, load_settings
from commute_agent.skills import mail_update
from commute_agent.tools import course_mail, local_llm


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
    state = {"answer": {"kind": "info", "course": None, "summary": "", "confidence": 0.5}}
    monkeypatch.setattr(local_llm.requests, "get",
                        lambda *a, **k: FakeResponse({"models": [{"name": "gemma3:4b"}]}))
    monkeypatch.setattr(local_llm.requests, "post",
                        lambda *a, **k: FakeResponse({"message": {"content": json.dumps(state["answer"], ensure_ascii=False)}}))
    return state


# ---------- classify_course_mail ----------

def test_room_change_mail_is_structured(ollama):
    ollama["answer"] = {"kind": "room_change", "course": "數位IC設計", "new_location": "資訊系館4263",
                        "new_time": None, "effective_date": "下週一", "importance": "high",
                        "summary": "下週一改到 4263 上課", "confidence": 0.9}
    out = course_mail.classify_course_mail("教室異動", "各位同學，下週一數位IC設計改到資訊系館4263。", _settings())
    assert out["status"] == "ok" and out["kind"] == "room_change"
    assert out["new_location"] == "資訊系館4263" and out["importance"] == "high"
    assert out["source"] == "gemma_local"


def test_unknown_kind_is_coerced_and_importance_derived(ollama):
    ollama["answer"] = {"kind": "party", "course": "x", "importance": "極高", "confidence": "0.7"}
    out = course_mail.classify_course_mail("s", "b", _settings())
    assert out["kind"] == "info" and out["importance"] == "normal" and out["confidence"] == 0.7


def test_exam_defaults_to_critical_when_model_omits_importance(ollama):
    ollama["answer"] = {"kind": "exam", "course": "資料庫"}
    assert course_mail.classify_course_mail("期中考", "下週考試", _settings())["importance"] == "critical"


def test_string_null_becomes_none(ollama):
    ollama["answer"] = {"kind": "info", "course": "null", "new_location": "", "summary": "x"}
    out = course_mail.classify_course_mail("s", "b", _settings())
    assert out["course"] is None and out["new_location"] is None


def test_mail_never_goes_to_cloud_when_local_is_down(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(local_llm.requests, "get", boom)
    out = course_mail.classify_course_mail("s", "b", _settings())
    assert out["status"] == "unavailable" and "不會送雲端" in out["error_message"]


def test_empty_mail_is_error(ollama):
    assert course_mail.classify_course_mail("", "  ", _settings())["status"] == "error"


# ---------- match_course（純函式） ----------

COURSES = [{"name": "數位IC設計"}, {"name": "日文（一）"}, {"name": "日文（二）"}]


def test_match_course_exact_and_partial():
    assert mail_update.match_course(COURSES, "數位IC設計")["name"] == "數位IC設計"
    assert mail_update.match_course(COURSES, "數位 IC 設計課")["name"] == "數位IC設計"
    assert mail_update.match_course(COURSES, "日文") is None      # 兩門都對得上，不猜
    assert mail_update.match_course(COURSES, None) is None


# ---------- apply_course_mail ----------

@pytest.fixture
def world(monkeypatch, ollama):
    monkeypatch.setattr(mail_update, "classify_course_mail",
                        lambda s, b: {**ollama["answer"], "status": "ok", "source": "gemma_local",
                                      "model": "gemma3:4b"} if "status" not in ollama["answer"] else ollama["answer"])
    monkeypatch.setattr(mail_update, "load_courses", lambda p: [
        {"name": "數位IC設計", "day": "Monday", "start_time": "09:00", "end_time": "12:00", "location": "資訊系館4264"}])
    monkeypatch.setattr(mail_update, "locate_course_place",
                        lambda text, room="": {"status": "ok", "name": "B501 資訊工程系館", "build_id": "B029",
                                               "lat": 22.99, "lon": 120.22, "is_verified": True})
    return ollama


def test_room_change_produces_patch_with_verified_building(world):
    world["answer"] = {"kind": "room_change", "course": "數位IC設計", "new_location": "資訊系館4263",
                       "new_time": None, "effective_date": None, "importance": "high",
                       "summary": "改到 4263", "confidence": 0.9}
    out = mail_update.apply_course_mail("教室異動", "…")
    assert out["status"] == "ok" and out["course"]["name"] == "數位IC設計"
    assert out["proposed_patch"] == {"location": "資訊系館4263", "building_name": "B501 資訊工程系館",
                                     "extra_buffer_minutes": 5}
    assert out["needs_confirmation"] is True
    assert "GIS 驗證" in out["note"]


def test_cancelled_mail_skips_commute(world):
    world["answer"] = {"kind": "cancelled", "course": "數位IC設計", "new_location": None, "new_time": None,
                       "effective_date": None, "importance": "normal", "summary": "下週停課", "confidence": 0.9}
    out = mail_update.apply_course_mail("停課", "…")
    assert out["proposed_patch"] == {"skip_commute": True}
    assert "不用出門" in out["note"]


def test_exam_mail_adds_buffer_even_without_location(world):
    world["answer"] = {"kind": "exam", "course": "數位IC設計", "new_location": None, "new_time": None,
                       "effective_date": None, "importance": "critical", "summary": "期中考", "confidence": 0.9}
    out = mail_update.apply_course_mail("期中考", "…")
    assert out["proposed_patch"] == {"extra_buffer_minutes": 10}


def test_unmatched_course_is_flagged_not_guessed(world):
    world["answer"] = {"kind": "room_change", "course": "量子計算", "new_location": "唯農大樓7208",
                       "new_time": None, "effective_date": None, "importance": "high",
                       "summary": "改教室", "confidence": 0.8}
    out = mail_update.apply_course_mail("教室異動", "…")
    assert out["course"] is None and "對不到" in out["note"]


def test_irrelevant_mail_changes_nothing(world):
    world["answer"] = {"kind": "irrelevant", "course": None, "new_location": None, "new_time": None,
                       "effective_date": None, "importance": "normal", "summary": "社團宣傳", "confidence": 0.9}
    out = mail_update.apply_course_mail("社團招募", "…")
    assert out["proposed_patch"] == {} and out["course"] is None


def test_local_model_down_surfaces_unavailable(world):
    world["answer"] = {"status": "unavailable", "kind": None, "error_message": "本機 Ollama 未啟動"}
    out = mail_update.apply_course_mail("s", "b")
    assert out["status"] == "unavailable" and "Ollama" in out["note"]
