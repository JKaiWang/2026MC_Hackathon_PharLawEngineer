"""lookup_building Tool 測試：讓 Agent 能查全校任何大樓，不侷限於已知教室代碼。
fixture 為真實錄製的 nckumap buildinfo 回應。"""
import json
from pathlib import Path

import pytest
import requests

from commute_agent.tools import ncku_building

FIXTURES = Path(__file__).resolve().parent.parent / "commute_agent" / "fixtures" / "ncku_gis" / "buildinfo"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload=None, status_code=200, bad_json=False):
        self._payload, self.status_code, self._bad = payload, status_code, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("PROVIDER_MODE", "live")


@pytest.fixture
def fixture_mode(monkeypatch):
    monkeypatch.setenv("PROVIDER_MODE", "fixture")


# ---------- 純解析 ----------

def test_parse_maps_fields():
    cands = ncku_building.parse_building_response(load("%E8%B3%87%E8%A8%8A%E5%B7%A5%E7%A8%8B%E7%B3%BB%E9%A4%A8.json"), "資訊工程系館")
    assert cands == [{
        "building_id": "B029",
        "building_name": "B501 資訊工程系館",
        "keyword": "資訊系館",
        "desc": "",
        "exact_match": True,
    }]


def test_parse_matches_via_keyword_too():
    # 查詢字用「資訊系館」(keyword 欄位)，而非完整正式名稱，也應算完全相符
    cands = ncku_building.parse_building_response(load("%E8%B3%87%E8%A8%8A%E5%B7%A5%E7%A8%8B%E7%B3%BB%E9%A4%A8.json"), "資訊系館")
    assert cands[0]["exact_match"] is True


def test_parse_rejects_unexpected_schema():
    with pytest.raises(ncku_building.SchemaError):
        ncku_building.parse_building_response({"rows": []}, "x")


def test_parse_does_not_invent_campus_name():
    # buildinfo 只回傳 campusId 這種內部代碼，且與停車系統的 A-G 校區代碼
    # 並非同一套編碼（未經證實），因此本工具刻意不猜測校區中文名稱。
    cands = ncku_building.parse_building_response(load("%E8%B3%87%E8%A8%8A%E5%B7%A5%E7%A8%8B%E7%B3%BB%E9%A4%A8.json"), "資訊工程系館")
    assert "campus" not in cands[0]
    assert "campus_id" not in cands[0]


# ---------- live 模式 ----------

def test_live_calls_real_endpoint_with_expected_params(live, monkeypatch):
    captured = {}

    def fake_get(url, params, timeout, headers):
        captured.update(url=url, params=params, timeout=timeout)
        return FakeResponse(load("%E8%B3%87%E8%A8%8A%E5%B7%A5%E7%A8%8B%E7%B3%BB%E9%A4%A8.json"))

    monkeypatch.setattr(ncku_building.requests, "get", fake_get)
    result = ncku_building.lookup_building("資訊工程系館")

    assert captured["url"] == "https://db.nckumap.ncku.edu.tw/nckugis/public/buildinfo.htm"
    assert captured["params"]["q"] == "資訊工程系館"
    assert captured["params"]["exactlyMatch"] == "false"
    assert result["status"] == "ok"
    assert result["mode"] == "live"
    assert result["exact_match_count"] == 1


def test_live_empty_result_is_not_found(live, monkeypatch):
    monkeypatch.setattr(ncku_building.requests, "get",
                        lambda *a, **k: FakeResponse({"data": [], "totalCount": 0}))
    result = ncku_building.lookup_building("不存在的大樓")
    assert result["status"] == "not_found"


def test_live_timeout_returns_error_not_exception(live, monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(ncku_building.requests, "get", boom)
    result = ncku_building.lookup_building("資訊工程系館")
    assert result["status"] == "error"
    assert "逾時" in result["error_message"]


def test_live_http_500_returns_error(live, monkeypatch):
    monkeypatch.setattr(ncku_building.requests, "get", lambda *a, **k: FakeResponse(status_code=500))
    assert ncku_building.lookup_building("資訊工程系館")["status"] == "error"


# ---------- fixture 模式 ----------

def test_fixture_mode_reads_recorded_file_without_network(fixture_mode, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("fixture 模式不應打網路")
    monkeypatch.setattr(ncku_building.requests, "get", no_network)
    result = ncku_building.lookup_building("資訊工程系館")
    assert result["status"] == "ok"
    assert result["mode"] == "fixture"


def test_fixture_mode_missing_recording_is_error(fixture_mode):
    result = ncku_building.lookup_building("不存在的查詢字串")
    assert result["status"] == "error"
    assert "fixture" in result["error_message"]


# ---------- 輸入驗證 ----------

@pytest.mark.parametrize("bad", ["", "   ", "x" * 51])
def test_invalid_query_returns_error(fixture_mode, bad):
    assert ncku_building.lookup_building(bad)["status"] == "error"
