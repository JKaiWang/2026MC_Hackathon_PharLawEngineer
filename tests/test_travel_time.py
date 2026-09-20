"""travel_time 接口測試：兩個來源都以假資料取代，不打網路也不花錢。"""
import pytest

from commute_agent.tools import google_routes, travel_time

LIBRARY = {"status": "ok", "name": "G001 圖書館(舊)",
           "lat": 22.995525782871347, "lon": 120.21955682352767}
CSIE = {"status": "ok", "name": "B501 資訊工程系館",
        "lat": 22.997228266312952, "lon": 120.22083681028772}
MISSING = {"status": "not_found", "name": "", "lat": None, "lon": None}

CAMPUS = {"圖書館": LIBRARY, "B501 資訊工程系館": CSIE}


@pytest.fixture
def places(monkeypatch):
    monkeypatch.setattr(travel_time, "resolve_place",
                        lambda q: CAMPUS.get(q, MISSING))


def fake_google(result):
    return lambda origin, destination, mode: result


def test_duration_string_is_parsed_into_minutes():
    parsed = google_routes.parse_route(
        {"routes": [{"duration": "930s", "distanceMeters": 1200}]})
    assert parsed["minutes"] == 16  # 930 秒 = 15.5 分，四捨五入
    assert parsed["distance_m"] == 1200


def test_encoded_polyline_is_decoded_for_route_event_matching():
    parsed = google_routes.parse_route({
        "routes": [{
            "duration": "60s",
            "distanceMeters": 1000,
            "polyline": {"encodedPolyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
        }]
    })

    assert parsed["route_points"] == [
        (38.5, -120.2),
        (40.7, -120.95),
        (43.252, -126.453),
    ]


def test_sub_minute_route_still_reports_one_minute():
    assert google_routes.parse_route({"routes": [{"duration": "20s"}]})["minutes"] == 1


def test_empty_routes_is_an_error():
    with pytest.raises(google_routes.RoutesError):
        google_routes.parse_route({"routes": []})


def test_malformed_duration_is_an_error():
    with pytest.raises(google_routes.RoutesError):
        google_routes.parse_route({"routes": [{"duration": "十分鐘"}]})


def test_estimate_provider_marks_result_as_estimate(places):
    r = travel_time.get_travel_time("圖書館", "B501 資訊工程系館", provider="estimate")
    assert r["status"] == "ok"
    assert r["is_estimate"] is True
    assert r["provider"] == "estimate"


def test_google_provider_result_is_not_an_estimate(places, monkeypatch):
    monkeypatch.setattr(travel_time, "compute_route", fake_google(
        {"status": "ok", "minutes": 7, "distance_m": 540}))
    r = travel_time.get_travel_time("圖書館", "B501 資訊工程系館", provider="google")
    assert r["is_estimate"] is False
    assert r["minutes"] == 7
    assert r["provider"] == "google_routes"


def test_google_failure_falls_back_to_estimate_with_reason(places, monkeypatch):
    monkeypatch.setattr(travel_time, "compute_route", fake_google(
        {"status": "error", "error_message": "未設定 GOOGLE_MAPS_API_KEY"}))
    r = travel_time.get_travel_time("圖書館", "B501 資訊工程系館", provider="google")
    # 金鑰沒設不該讓整個功能掛掉，但要說清楚為什麼變成估算值
    assert r["provider"] == "estimate"
    assert r["is_estimate"] is True
    assert "GOOGLE_MAPS_API_KEY" in r["fallback_reason"]
    assert r["minutes"] >= 1


def test_google_can_route_off_campus_origin_that_estimate_cannot(places, monkeypatch):
    # 估算做不到校外起點，這正是接 Google 的主要理由
    monkeypatch.setattr(travel_time, "compute_route", fake_google(
        {"status": "ok", "minutes": 14, "distance_m": 3200}))
    home = "台南市東區某路一段1號"
    assert travel_time.get_travel_time(
        home, "B501 資訊工程系館", provider="estimate")["status"] == "unavailable"
    assert travel_time.get_travel_time(
        home, "B501 資訊工程系館", provider="google")["minutes"] == 14


def test_off_campus_origin_stays_unavailable_when_google_also_fails(places, monkeypatch):
    monkeypatch.setattr(travel_time, "compute_route", fake_google(
        {"status": "error", "error_message": "額度用盡"}))
    r = travel_time.get_travel_time("台南火車站", "B501 資訊工程系館", provider="google")
    assert r["status"] == "unavailable"
    assert r["minutes"] is None


def test_transit_has_no_estimate_but_google_would_give_one(places, monkeypatch):
    assert travel_time.get_travel_time(
        "圖書館", "B501 資訊工程系館", "transit", provider="estimate")["minutes"] is None
    monkeypatch.setattr(travel_time, "compute_route", fake_google(
        {"status": "ok", "minutes": 22, "distance_m": 4100}))
    assert travel_time.get_travel_time(
        "圖書館", "B501 資訊工程系館", "transit", provider="google")["minutes"] == 22


def test_unknown_provider_falls_back_to_estimate(places):
    r = travel_time.get_travel_time("圖書館", "B501 資訊工程系館", provider="魔法")
    assert r["provider"] == "estimate"


def settings_with(key: str):
    """假的設定物件，讓測試不受開發者 .env 內容影響，也不會真的花錢。"""
    return lambda: type("S", (), {"google_maps_api_key": key,
                                  "http_timeout_seconds": 8.0})()


def test_missing_key_is_reported_without_calling_the_api(monkeypatch):
    # 沒金鑰時不該真的送出請求
    monkeypatch.setattr(google_routes, "load_settings", settings_with(""))
    monkeypatch.setattr(google_routes.requests, "post", lambda *a, **k:
                        pytest.fail("不該在沒有金鑰時呼叫 API"))
    r = google_routes.compute_route("A", "B", "walking")
    assert r["status"] == "error"
    assert "GOOGLE_MAPS_API_KEY" in r["error_message"]


def test_unsupported_mode_is_rejected_before_calling_the_api(monkeypatch):
    monkeypatch.setattr(google_routes, "load_settings", settings_with("fake-key"))
    monkeypatch.setattr(google_routes.requests, "post", lambda *a, **k:
                        pytest.fail("不該對不支援的模式呼叫 API"))
    assert google_routes.compute_route("A", "B", "teleport")["status"] == "error"


def test_coordinates_become_latlng_waypoint():
    # 座標比地名可靠：「B406 三系館」曾被 Google 解析成隔壁的材料系館
    wp = google_routes.waypoint((22.9972, 120.2208))
    assert wp["location"]["latLng"] == {"latitude": 22.9972, "longitude": 120.2208}


def test_plain_string_becomes_address_waypoint():
    assert google_routes.waypoint("台南市東區某路一段1號") == {
        "address": "台南市東區某路一段1號"}


def test_campus_place_is_sent_as_coordinates(places):
    # 校內地點先用免費的 GIS 查座標，不讓 Google 去猜名字
    assert travel_time.to_waypoint("圖書館") == (LIBRARY["lat"], LIBRARY["lon"])


def test_off_campus_address_is_sent_as_text(places):
    assert travel_time.to_waypoint("台南火車站") == "台南火車站"


def test_existing_coordinates_pass_through(places):
    assert travel_time.to_waypoint((22.5, 120.1)) == (22.5, 120.1)


def test_auto_uses_free_source_when_it_can_answer(places, monkeypatch):
    # 校內兩點走路，免費的就答得出來，不該花錢
    monkeypatch.setattr(travel_time, "compute_route", lambda *a, **k:
                        pytest.fail("免費來源答得出來時不該呼叫 Google"))
    r = travel_time.get_travel_time("圖書館", "B501 資訊工程系館", provider="auto")
    assert r["provider"] == "estimate"


def test_auto_falls_forward_to_google_for_off_campus_origin(places, monkeypatch):
    # 這正是使用者反映的情境：有金鑰卻回「無法估算」是錯的
    monkeypatch.setattr(travel_time, "compute_route", fake_google(
        {"status": "ok", "minutes": 11, "distance_m": 2085}))
    r = travel_time.get_travel_time("台南市東區某路一段1號", "B501 資訊工程系館",
                                    provider="auto")
    assert r["provider"] == "google_routes"
    assert r["minutes"] == 11


def test_auto_falls_forward_to_google_for_transit(places, monkeypatch):
    monkeypatch.setattr(travel_time, "compute_route", fake_google(
        {"status": "ok", "minutes": 20, "distance_m": 2600}))
    r = travel_time.get_travel_time("圖書館", "B501 資訊工程系館", "transit",
                                    provider="auto")
    assert r["minutes"] == 20


def test_auto_reports_both_failures_when_neither_source_works(places, monkeypatch):
    monkeypatch.setattr(travel_time, "compute_route", fake_google(
        {"status": "error", "error_message": "額度用盡"}))
    r = travel_time.get_travel_time("台南火車站", "B501 資訊工程系館", provider="auto")
    assert r["status"] == "unavailable"
    assert r["minutes"] is None
    assert "額度用盡" in r["fallback_reason"]
