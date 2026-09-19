"""estimate_travel_time Tool 測試。

本工具依賴 Google Maps Distance Matrix API 才能算出有依據的真實時間，
沒有 GOOGLE_MAPS_API_KEY 時一律誠實回報「無法使用」，絕不用假設的速度
去推算一個看起來像真的、實際上編造的時間數字。
"""
import pytest
import requests

from commute_agent.tools import travel_time


class FakeResponse:
    def __init__(self, payload=None, status_code=200, bad_json=False):
        self._payload, self.status_code, self._bad = payload, status_code, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._payload


DISTANCE_MATRIX_OK = {
    "status": "OK",
    "origin_addresses": ["敬業一舍, 台南市"],
    "destination_addresses": ["國立成功大學 B502 資訊工程系大樓, 台南市"],
    "rows": [{"elements": [{
        "status": "OK",
        "distance": {"text": "1.2 公里", "value": 1200},
        "duration": {"text": "16 分鐘", "value": 960},
    }]}],
}

DISTANCE_MATRIX_ELEMENT_NOT_FOUND = {
    "status": "OK",
    "rows": [{"elements": [{"status": "NOT_FOUND"}]}],
}

DISTANCE_MATRIX_REQUEST_DENIED = {"status": "REQUEST_DENIED", "error_message": "The provided API key is invalid."}


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key-not-real")


# ---------- 沒有金鑰：一律誠實回報 unavailable，絕不編數字 ----------

def test_no_api_key_returns_unavailable_not_a_fake_number(no_key):
    result = travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "機車")
    assert result["status"] == "unavailable"
    assert "GOOGLE_MAPS_API_KEY" in result["reason"]
    assert "duration_text" not in result


def test_no_api_key_never_calls_network(no_key, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("沒有金鑰時不應打網路")
    monkeypatch.setattr(travel_time.requests, "get", no_network)
    travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "機車")


# ---------- 有金鑰：呼叫真實 API ----------

def test_calls_distance_matrix_with_expected_params(with_key, monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured.update(url=url, params=params)
        return FakeResponse(DISTANCE_MATRIX_OK)

    monkeypatch.setattr(travel_time.requests, "get", fake_get)
    result = travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "汽車")

    assert captured["url"] == "https://maps.googleapis.com/maps/api/distancematrix/json"
    assert captured["params"]["origins"] == "敬業一舍"
    assert captured["params"]["destinations"] == "資訊工程系館"
    assert captured["params"]["mode"] == "driving"
    assert captured["params"]["key"] == "test-key-not-real"
    assert result["status"] == "ok"
    assert result["duration_text"] == "16 分鐘"
    assert result["duration_seconds"] == 960
    assert result["distance_text"] == "1.2 公里"


def test_walking_mode_maps_to_walking(with_key, monkeypatch):
    captured = {}
    monkeypatch.setattr(travel_time.requests, "get",
                        lambda url, params, timeout: (captured.update(params=params), FakeResponse(DISTANCE_MATRIX_OK))[1])
    travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "步行")
    assert captured["params"]["mode"] == "walking"


def test_moped_uses_driving_mode_with_caveat(with_key, monkeypatch):
    # Google Distance Matrix 沒有機車專用模式，只能用 driving 近似，
    # 必須明確告知使用者這是近似值，不能當成機車專屬的準確時間。
    monkeypatch.setattr(travel_time.requests, "get", lambda *a, **k: FakeResponse(DISTANCE_MATRIX_OK))
    result = travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "機車")
    assert result["google_travel_mode"] == "driving"
    assert "近似" in result["caveat"]


def test_car_mode_has_no_caveat(with_key, monkeypatch):
    monkeypatch.setattr(travel_time.requests, "get", lambda *a, **k: FakeResponse(DISTANCE_MATRIX_OK))
    result = travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "汽車")
    assert result.get("caveat") is None


def test_element_not_found_returns_not_found(with_key, monkeypatch):
    monkeypatch.setattr(travel_time.requests, "get",
                        lambda *a, **k: FakeResponse(DISTANCE_MATRIX_ELEMENT_NOT_FOUND))
    result = travel_time.estimate_travel_time("不存在的地方", "資訊工程系館", "機車")
    assert result["status"] == "not_found"


def test_top_level_status_not_ok_returns_error(with_key, monkeypatch):
    monkeypatch.setattr(travel_time.requests, "get",
                        lambda *a, **k: FakeResponse(DISTANCE_MATRIX_REQUEST_DENIED))
    result = travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "機車")
    assert result["status"] == "error"
    assert "REQUEST_DENIED" in result["error_message"]


def test_timeout_returns_error_not_exception(with_key, monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(travel_time.requests, "get", boom)
    result = travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "機車")
    assert result["status"] == "error"
    assert "逾時" in result["error_message"]


def test_http_500_returns_error(with_key, monkeypatch):
    monkeypatch.setattr(travel_time.requests, "get", lambda *a, **k: FakeResponse(status_code=500))
    assert travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "機車")["status"] == "error"


def test_malformed_json_returns_error(with_key, monkeypatch):
    monkeypatch.setattr(travel_time.requests, "get", lambda *a, **k: FakeResponse(bad_json=True))
    assert travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "機車")["status"] == "error"


def test_unknown_vehicle_type_raises(with_key):
    with pytest.raises(ValueError):
        travel_time.estimate_travel_time("敬業一舍", "資訊工程系館", "飛天車")


def test_empty_origin_raises(with_key):
    with pytest.raises(ValueError):
        travel_time.estimate_travel_time("", "資訊工程系館", "機車")


def test_empty_destination_raises(with_key):
    with pytest.raises(ValueError):
        travel_time.estimate_travel_time("敬業一舍", "", "機車")
