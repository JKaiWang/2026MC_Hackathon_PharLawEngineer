"""road_watch Skill 測試：起點/座標與路況查詢都用假資料取代，不打網路。"""
import pytest

from commute_agent.skills import road_watch

CSIE = {"status": "ok", "name": "B501 資訊工程系館", "lat": 22.9972, "lon": 120.2208}
LIBRARY = {"status": "ok", "name": "G001 圖書館(舊)", "lat": 22.9955, "lon": 120.2196}
MISSING = {"status": "not_found", "name": "", "lat": None, "lon": None}

ACCIDENT = {"event_id": "E1", "description": "事故", "type_code": 1, "type_is_code": False,
            "road_name": "小東路", "reported_at": None, "lat": 23.0, "lon": 120.22, "distance_m": 120}


@pytest.fixture
def places(monkeypatch):
    table = {}
    monkeypatch.setattr(road_watch, "resolve_place", lambda q: table.get(q, MISSING))
    return table


@pytest.fixture
def events(monkeypatch):
    """key 是 (lat, lon) 四捨五入到小數點四位，回傳 get_road_events 的結果。"""
    table = {}

    def fake_get_road_events(city, lat, lon, radius_m):
        return table.get((city, round(lat, 4), round(lon, 4)),
                         {"status": "ok", "events": [], "count": 0, "mode": "fixture",
                          "source": "", "fetched_at": ""})

    monkeypatch.setattr(road_watch, "get_road_events", fake_get_road_events)
    return table


def test_both_sides_resolved_and_clean(places, events):
    places.update({"圖書館": LIBRARY, "B501 資訊工程系館": CSIE})
    result = road_watch.check_route_events("圖書館", "B501 資訊工程系館")
    assert result["status"] == "ok"
    assert result["has_events"] is False
    assert result["origin"]["resolved"] is True and result["origin"]["name"] == "G001 圖書館(舊)"
    assert result["destination"]["resolved"] is True


def test_event_near_destination_sets_has_events(places, events):
    places.update({"圖書館": LIBRARY, "B501 資訊工程系館": CSIE})
    events[("Tainan", round(CSIE["lat"], 4), round(CSIE["lon"], 4))] = {
        "status": "ok", "events": [ACCIDENT], "count": 1, "mode": "fixture", "source": "", "fetched_at": "",
    }
    result = road_watch.check_route_events("圖書館", "B501 資訊工程系館")
    assert result["has_events"] is True
    assert result["destination"]["count"] == 1
    assert result["destination"]["events"][0]["road_name"] == "小東路"
    assert result["origin"]["count"] == 0


def test_off_campus_origin_is_marked_unresolved_but_destination_still_checked(places, events):
    # 校外地址（例如住家）查不到座標，起點這端如實標記，不假裝查過
    places.update({"B501 資訊工程系館": CSIE})
    result = road_watch.check_route_events("台南火車站", "B501 資訊工程系館")
    assert result["status"] == "ok"
    assert result["origin"]["resolved"] is False
    assert "查不到座標" in result["origin"]["note"] or "校外" in result["origin"]["note"]
    assert result["destination"]["resolved"] is True


def test_both_sides_unresolved_is_error(places, events):
    result = road_watch.check_route_events("台南火車站", "某個查不到的地方")
    assert result["status"] == "error"
    assert result["has_events"] is False


def test_radius_is_passed_through(places, monkeypatch):
    places.update({"B501 資訊工程系館": CSIE, "圖書館": LIBRARY})
    captured = []

    def spy(city, lat, lon, radius_m):
        captured.append(radius_m)
        return {"status": "ok", "events": [], "count": 0, "mode": "fixture", "source": "", "fetched_at": ""}

    monkeypatch.setattr(road_watch, "get_road_events", spy)
    road_watch.check_route_events("圖書館", "B501 資訊工程系館", radius_m=800)
    assert captured == [800, 800]


def test_supplied_destination_place_avoids_second_fuzzy_lookup(places, monkeypatch):
    places["origin"] = LIBRARY
    captured = []

    def spy(city, lat, lon, radius_m):
        captured.append((lat, lon, radius_m))
        return {"status": "ok", "events": [], "count": 0, "mode": "fixture",
                "source": "", "fetched_at": ""}

    monkeypatch.setattr(road_watch, "get_road_events", spy)
    result = road_watch.check_route_events(
        "origin", "A105",
        destination_place=CSIE,
    )

    assert result["destination"]["resolved"] is True
    assert captured[-1] == (CSIE["lat"], CSIE["lon"], 500.0)


def test_route_events_cover_the_path_between_endpoints(places, monkeypatch):
    places.update({"圖書館": LIBRARY, "B501 資訊工程系館": CSIE})
    route_event = {**ACCIDENT, "event_id": "MID", "distance_m": 42}
    monkeypatch.setattr(
        road_watch,
        "compute_route",
        lambda origin, destination, travel_mode, with_path=False: {
            "status": "ok", "route_points": [(22.9955, 120.2196),
                                                 (22.9972, 120.2208)]
        },
    )
    monkeypatch.setattr(
        road_watch,
        "get_road_events_along_route",
        lambda city, route_points, radius_m: {
            "status": "ok", "events": [route_event], "count": 1,
            "mode": "fixture", "source": "", "fetched_at": "",
        },
    )

    result = road_watch.check_route_events("圖書館", "B501 資訊工程系館")

    assert result["status"] == "ok"
    assert result["route"]["count"] == 1
    assert result["route"]["events"][0]["event_id"] == "MID"


def test_route_event_query_requests_the_navigation_geometry(places, monkeypatch):
    places.update({"library": LIBRARY, "csie": CSIE})
    captured = {}

    def fake_compute_route(origin, destination, travel_mode, with_path=False):
        captured["with_path"] = with_path
        return {"status": "ok", "route_points": [
            (22.9955, 120.2196), (22.9972, 120.2208),
        ]}

    monkeypatch.setattr(road_watch, "compute_route", fake_compute_route)
    monkeypatch.setattr(road_watch, "get_road_events_along_route",
                        lambda city, points, radius_m: {
                            "status": "ok", "events": [], "count": 0,
                        })

    road_watch.check_route_events("library", "csie")

    assert captured["with_path"] is True


def test_route_result_marks_a_closed_road_event(places, monkeypatch):
    places.update({"library": LIBRARY, "csie": CSIE})
    monkeypatch.setattr(road_watch, "compute_route",
                        lambda *args, **kwargs: {
                            "status": "ok", "route_points": [
                                (22.9955, 120.2196), (22.9972, 120.2208),
                            ]})
    monkeypatch.setattr(road_watch, "get_road_events_along_route",
                        lambda city, points, radius_m: {
                            "status": "ok", "events": [{
                                **ACCIDENT, "event_id": "CLOSED",
                                "description": "道路封閉施工",
                            }], "count": 1,
                        })

    result = road_watch.check_route_events("library", "csie")

    assert result["route"]["has_closed_route"] is True
    assert result["route"]["closure_count"] == 1
