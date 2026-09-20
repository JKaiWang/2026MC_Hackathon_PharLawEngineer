"""road_events Tool 測試。

fixture 模式讀的 commute_agent/fixtures/ncku_traffic/Tainan.json 是 2026-09-19
對真實 TDX 端點錄製的回應（見資料夾裡的 README），距離斷言照那份快照的
實際數字寫，不是編的。live 模式的測試改用手寫的假 payload，只驗證本檔的
解析與流量控制邏輯，不代表 TDX 未來的回應內容一定長這樣。
"""
import pytest
import requests

from commute_agent.tools import road_events

TAIPEI_TOKEN_BODY = {"access_token": "fake-token-1", "expires_in": 3600, "token_type": "bearer"}

# 成大成功校區中心，錄製當下（2026-09-19 約 19:59）最近的真實事件約 1112m
# （機車事故，怡東路），500m 內沒有事件——這是真實快照，不是抓錯半徑。
NCKU_LAT, NCKU_LON = 22.9997, 120.2220


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
    monkeypatch.setenv("TDX_CLIENT_ID", "test-id")
    monkeypatch.setenv("TDX_CLIENT_SECRET", "test-secret")


@pytest.fixture
def fixture_mode(monkeypatch):
    monkeypatch.setenv("PROVIDER_MODE", "fixture")


@pytest.fixture(autouse=True)
def clean_token_cache():
    road_events.reset_token_cache()
    yield
    road_events.reset_token_cache()


# ---------- access token ----------

def test_get_access_token_returns_token_from_response(monkeypatch):
    monkeypatch.setattr(road_events.requests, "post", lambda *a, **k: FakeResponse(TAIPEI_TOKEN_BODY))
    token = road_events.get_access_token("id", "secret", timeout=5)
    assert token == "fake-token-1"


def test_get_access_token_is_cached_within_expiry(monkeypatch):
    calls = []
    monkeypatch.setattr(road_events.requests, "post",
                        lambda *a, **k: calls.append(1) or FakeResponse(TAIPEI_TOKEN_BODY))
    road_events.get_access_token("id", "secret", timeout=5, now=1_000_000.0)
    road_events.get_access_token("id", "secret", timeout=5, now=1_000_100.0)
    assert len(calls) == 1


def test_get_access_token_refetches_after_expiry(monkeypatch):
    calls = []
    monkeypatch.setattr(road_events.requests, "post",
                        lambda *a, **k: calls.append(1) or FakeResponse(TAIPEI_TOKEN_BODY))
    road_events.get_access_token("id", "secret", timeout=5, now=1_000_000.0)
    # expires_in=3600，快取提前 60 秒視為過期；3600 秒後一定已過期
    road_events.get_access_token("id", "secret", timeout=5, now=1_000_000.0 + 3600)
    assert len(calls) == 2


def test_get_access_token_http_error_raises_token_error(monkeypatch):
    monkeypatch.setattr(road_events.requests, "post", lambda *a, **k: FakeResponse(status_code=401))
    with pytest.raises(road_events.TokenError):
        road_events.get_access_token("id", "bad-secret", timeout=5)


def test_get_access_token_missing_field_raises(monkeypatch):
    monkeypatch.setattr(road_events.requests, "post", lambda *a, **k: FakeResponse({"token_type": "bearer"}))
    with pytest.raises(road_events.TokenError):
        road_events.get_access_token("id", "secret", timeout=5)


def test_get_access_token_network_error_raises(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("dns fail")
    monkeypatch.setattr(road_events.requests, "post", boom)
    with pytest.raises(road_events.TokenError):
        road_events.get_access_token("id", "secret", timeout=5)


# ---------- parse_road_events ----------
# TDX 真實回應（2026-09-19 實測）：事件陣列在 LiveEvents；座標在 Positions，
# 是 "POINT (經度 緯度)" 的 WKT 字串；地點文字在 Location.Other；
# Description／EventTitle 都是現成中文文字。以下先測這個確認過的形狀，
# 再測幾個沒實測過、留著防呆用的候選欄位。

def test_parse_handles_wkt_positions_and_location_other():
    rows = [{"EventID": "E1", "Description": "事故", "EventTitle": "緊急救護",
             "EventType": 1, "Positions": "POINT (120.22 23.0)",
             "Location": {"Other": "小東路口"}}]
    events = road_events.parse_road_events(rows)
    assert events == [{
        "event_id": "E1", "description": "事故", "category": "緊急救護",
        "type_code": 1, "type_is_code": False, "road_name": "小東路口",
        "reported_at": None, "lat": 23.0, "lon": 120.22, "raw": rows[0],
    }]


def test_parse_unwraps_live_events_key():
    payload = {"LiveEvents": [{"EventID": "E2", "Positions": "POINT (120.22 23.0)"}],
              "UpdateTime": "2026-09-19T00:00:00+08:00"}
    events = road_events.parse_road_events(payload)
    assert len(events) == 1 and events[0]["event_id"] == "E2"


def test_parse_handles_nested_position_fallback():
    # 沒實測過會回這個形狀，留著當防呆
    rows = [{"EventID": "E3", "Position": {"PositionLon": 120.22, "PositionLat": 23.0}}]
    events = road_events.parse_road_events(rows)
    assert events[0]["lat"] == 23.0 and events[0]["lon"] == 120.22


def test_parse_handles_flat_lon_lat_fallback():
    rows = [{"EventID": "E4", "Latitude": 23.0, "Longitude": 120.22}]
    events = road_events.parse_road_events(rows)
    assert events[0]["lat"] == 23.0 and events[0]["lon"] == 120.22


def test_parse_unwraps_dict_with_data_key_fallback():
    payload = {"Data": [{"EventID": "E5", "Positions": "POINT (120.22 23.0)"}]}
    events = road_events.parse_road_events(payload)
    assert len(events) == 1 and events[0]["event_id"] == "E5"


def test_parse_type_is_code_true_when_no_description_or_category():
    rows = [{"EventID": "E6", "EventSubType": 9, "Positions": "POINT (120.22 23.0)"}]
    events = road_events.parse_road_events(rows)
    assert events[0]["type_code"] == 9 and events[0]["type_is_code"] is True


def test_parse_type_is_code_false_when_category_present_even_without_description():
    rows = [{"EventID": "E7", "EventSubType": 9, "EventTitle": "壅塞",
             "Positions": "POINT (120.22 23.0)"}]
    events = road_events.parse_road_events(rows)
    assert events[0]["type_is_code"] is False and events[0]["category"] == "壅塞"


def test_parse_empty_list_returns_empty():
    assert road_events.parse_road_events([]) == []


def test_filter_events_near_route_includes_mid_route_event():
    events = [{"event_id": "mid", "lat": 23.0005, "lon": 120.005,
               "description": "施工"}]
    route = [(23.0, 120.0), (23.0, 120.01)]

    found = road_events.filter_events_near_route(events, route, radius_m=100)

    assert [event["event_id"] for event in found] == ["mid"]
    assert found[0]["distance_m"] < 100


def test_parse_missing_coordinates_raises_schema_error_listing_keys():
    rows = [{"EventID": "E8", "Foo": "bar"}]
    with pytest.raises(road_events.SchemaError) as exc:
        road_events.parse_road_events(rows)
    assert "EventID" in str(exc.value) and "Foo" in str(exc.value)


def test_parse_garbage_positions_string_is_not_mistaken_for_coordinates():
    rows = [{"EventID": "E9", "Positions": "not a wkt point"}]
    with pytest.raises(road_events.SchemaError):
        road_events.parse_road_events(rows)


def test_parse_non_list_non_dict_raises():
    with pytest.raises(road_events.SchemaError):
        road_events.parse_road_events("not a list")


def test_parse_dict_without_recognizable_array_raises():
    with pytest.raises(road_events.SchemaError):
        road_events.parse_road_events({"Unexpected": "shape"})


# ---------- get_road_events：fixture 模式（真實錄製快照，見 fixtures/ncku_traffic/README.md） ----------

def test_fixture_mode_500m_matches_the_recorded_empty_snapshot(fixture_mode):
    # 錄製當下成大周邊 500m 內真的沒有事件——這是誠實的空結果，不是抓錯半徑
    result = road_events.get_road_events("Tainan", NCKU_LAT, NCKU_LON, radius_m=500)
    assert result["status"] == "ok"
    assert result["events"] == []
    assert result["count"] == 0


def test_fixture_mode_2000m_includes_nearest_real_event_sorted_by_distance(fixture_mode):
    result = road_events.get_road_events("Tainan", NCKU_LAT, NCKU_LON, radius_m=2000)
    assert result["status"] == "ok"
    assert result["count"] >= 2
    distances = [e["distance_m"] for e in result["events"]]
    assert distances == sorted(distances)
    nearest = result["events"][0]
    assert 1000 < nearest["distance_m"] < 1200
    assert nearest["description"]  # 真實回應每筆都有現成中文說明
    assert nearest["event_id"]


def test_fixture_mode_missing_city_file_is_error(fixture_mode):
    result = road_events.get_road_events("Taipei", NCKU_LAT, NCKU_LON, radius_m=500)
    assert result["status"] == "error"
    assert "Taipei" in result["error_message"]


# ---------- get_road_events：live 模式 ----------

def test_live_mode_sends_bearer_token_and_format_json(live, monkeypatch):
    seen = {}

    def fake_post(url, data=None, **kwargs):
        assert url == road_events.TOKEN_URL
        return FakeResponse(TAIPEI_TOKEN_BODY)

    def fake_get(url, params=None, headers=None, **kwargs):
        seen["url"], seen["params"], seen["headers"] = url, params, headers
        return FakeResponse({"LiveEvents": [{"EventID": "L1", "Description": "測試",
                                            "Positions": f"POINT ({NCKU_LON} {NCKU_LAT})"}]})

    monkeypatch.setattr(road_events.requests, "post", fake_post)
    monkeypatch.setattr(road_events.requests, "get", fake_get)

    result = road_events.get_road_events("Tainan", NCKU_LAT, NCKU_LON, radius_m=500)
    assert result["status"] == "ok" and result["count"] == 1
    assert seen["url"] == "https://tdx.transportdata.tw/api/basic/v1/Traffic/RoadEvent/LiveEvent/City/Tainan"
    assert seen["params"] == {"$format": "JSON"}
    assert seen["headers"]["Authorization"] == "Bearer fake-token-1"


def test_live_mode_token_failure_surfaces_as_error(live, monkeypatch):
    monkeypatch.setattr(road_events.requests, "post", lambda *a, **k: FakeResponse(status_code=403))
    result = road_events.get_road_events("Tainan", NCKU_LAT, NCKU_LON)
    assert result["status"] == "error" and "error_message" in result


def test_live_mode_401_from_data_endpoint_is_error(live, monkeypatch):
    monkeypatch.setattr(road_events.requests, "post", lambda *a, **k: FakeResponse(TAIPEI_TOKEN_BODY))
    monkeypatch.setattr(road_events.requests, "get", lambda *a, **k: FakeResponse(status_code=401))
    result = road_events.get_road_events("Tainan", NCKU_LAT, NCKU_LON)
    assert result["status"] == "error" and "金鑰" in result["error_message"]


def test_live_mode_timeout_is_error(live, monkeypatch):
    monkeypatch.setattr(road_events.requests, "post", lambda *a, **k: FakeResponse(TAIPEI_TOKEN_BODY))

    def timeout(*a, **k):
        raise requests.Timeout("slow")
    monkeypatch.setattr(road_events.requests, "get", timeout)
    result = road_events.get_road_events("Tainan", NCKU_LAT, NCKU_LON)
    assert result["status"] == "error" and "逾時" in result["error_message"]


def test_live_mode_bad_json_is_error(live, monkeypatch):
    monkeypatch.setattr(road_events.requests, "post", lambda *a, **k: FakeResponse(TAIPEI_TOKEN_BODY))
    monkeypatch.setattr(road_events.requests, "get", lambda *a, **k: FakeResponse(bad_json=True))
    result = road_events.get_road_events("Tainan", NCKU_LAT, NCKU_LON)
    assert result["status"] == "error"


def test_live_mode_malformed_event_becomes_error_result(live, monkeypatch):
    monkeypatch.setattr(road_events.requests, "post", lambda *a, **k: FakeResponse(TAIPEI_TOKEN_BODY))
    monkeypatch.setattr(road_events.requests, "get", lambda *a, **k: FakeResponse([{"Nothing": "useful"}]))
    result = road_events.get_road_events("Tainan", NCKU_LAT, NCKU_LON)
    assert result["status"] == "error" and "回應格式異常" in result["error_message"]
