"""classroom_guide 測試：GIS 與平面圖對照表都用假資料。"""
import pytest

from commute_agent.skills import classroom_guide as cg
from commute_agent.skills.classroom_guide import locate_classroom

CANDIDATE = {"room_code": "4264", "room_name": "", "floor": "2F",
             "building_id": "B029", "building_name": "B501 資訊工程系館",
             "building_name_en": "", "space_id": "", "exact_match": True}

PLAN = {"status": "ok", "floor": "2F", "floor_source": "given",
        "floor_rule": "42 開頭的大樓代號只有一碼，樓層看第 2 碼",
        "floor_by_room_code": "2F",
        "plan": {"file": "42.png", "url": "/picture/42.png",
                 "caption": "資訊系館 2F", "available": True},
        "plan_floor": "2F", "plan_building": "B501 資訊工程系館",
        "campus_map": {"file": "NCKU.png", "url": "/picture/NCKU.png",
                       "caption": "校區全圖", "available": True}}


LIVE_IMAGE = {"status": "ok", "url": "https://gis/wms?layer=gis_room:B029_2F",
              "layer": "gis_room:B029_2F", "floors": ["1F", "2F"],
              "width": 1200, "height": 700,
              "bbox": {"minx": 0.0, "miny": 0.0, "maxx": 100.0, "maxy": 50.0}}

LIVE_ROOMS = {"status": "ok", "rooms": [
    {"room_code": "4264", "room_name": "", "type": "普通教室", "capacity": "60",
     "using_unit": "資訊工程學系", "area": 66.6,
     "bounds": {"minx": 10.0, "miny": 10.0, "maxx": 20.0, "maxy": 20.0}}]}


@pytest.fixture
def world(monkeypatch):
    state = {"room": {"status": "ok", "candidates": [dict(CANDIDATE)],
                      "exact_match_count": 1, "source": "http://gis"},
             "plan": dict(PLAN),
             "image": dict(LIVE_IMAGE),
             "rooms": dict(LIVE_ROOMS)}

    state["place"] = {"status": "not_found", "source": None, "name": "",
                      "build_id": "", "floor": "", "lat": None, "lon": None}

    monkeypatch.setattr(cg, "lookup_room", lambda q: state["room"])
    monkeypatch.setattr(cg, "locate_course_place",
                        lambda text, room="": state["place"])
    monkeypatch.setattr(cg, "floor_plan_url",
                        lambda bid, floor, *a, **k: state["image"])
    monkeypatch.setattr(cg, "get_rooms_on_floor",
                        lambda bid, floor: state["rooms"])
    monkeypatch.setattr(cg, "get_floor_plan",
                        lambda code, name="", floor="": state["plan"])
    monkeypatch.setattr(cg, "get_building_centroid",
                        lambda bid: {"status": "ok", "name": "B501 資訊工程系館",
                                     "lat": 22.9972, "lon": 120.2208})
    monkeypatch.setattr(cg, "build_route_link",
                        lambda target, origin=None, travel_mode="walking": "http://map")
    return state


def test_conclusion_says_which_floor(world):
    found = locate_classroom("4264")
    assert found["status"] == "ok"
    assert "2 樓" in found["conclusion"]
    assert found["floor_source"] == "gis"


def test_coordinates_come_from_the_building_id(world):
    # 用名稱搜尋會對到別棟，一定要走 build_id
    assert locate_classroom("4264")["location"]["lat"] == 22.9972


def test_the_live_geoserver_layer_is_preferred(world):
    plan = locate_classroom("4264")["floor_plan"]
    assert plan["source"] == "geoserver"
    assert plan["url"] == LIVE_IMAGE["url"]
    assert plan["floors"] == ["1F", "2F"]


def test_the_target_room_is_marked_on_the_plan(world):
    # 房間在 bbox 的 x 10–20%／y 60–80%（y 要翻轉，地理往北是圖片往上）
    mark = locate_classroom("4264")["floor_plan"]["highlight"]
    assert (mark["left"], mark["width"]) == (10.0, 10.0)
    assert (mark["top"], mark["height"]) == (60.0, 20.0)


def test_campus_map_marks_the_gis_room_and_exposes_its_label(monkeypatch):
    monkeypatch.setattr(cg, "is_on_main_campus", lambda build_id: True)
    monkeypatch.setattr(cg, "campus_map_url", lambda: {
        "status": "ok", "url": "https://gis/campus", "bbox": {
            "minx": 0.0, "miny": 0.0, "maxx": 100.0, "maxy": 100.0,
        },
    })
    monkeypatch.setattr(cg, "get_building_footprint", lambda build_id: {
        "minx": 10.0, "miny": 10.0, "maxx": 30.0, "maxy": 30.0,
    })
    monkeypatch.setattr(cg, "get_rooms_on_floor", lambda build_id, floor: {
        "status": "ok", "rooms": [{
            "room_code": "4264", "bounds": {
                "minx": 20.0, "miny": 20.0, "maxx": 24.0, "maxy": 24.0,
            },
        }],
    })

    campus = cg._campus_plan("B029", "2F", "4264")

    assert campus["highlight_label"] == "4264"
    assert campus["highlight"] == {
        "left": 20.0, "top": 76.0, "width": 4.0, "height": 4.0,
    }


def test_no_mark_when_the_room_is_not_on_that_layer(world):
    state_rooms = {"status": "ok", "rooms": [{"room_code": "9999", "bounds": None}]}
    world["rooms"] = state_rooms
    assert locate_classroom("4264")["floor_plan"]["highlight"] is None


def test_screenshot_is_the_fallback_when_there_is_no_layer(world):
    # GeoServer 沒有這一層時才用 picture/ 的人工截圖
    world["image"] = {"status": "not_found", "floors": []}
    plan = locate_classroom("4264")["floor_plan"]
    assert plan["source"] == "picture"
    assert plan["url"] == "/picture/42.png"


def test_gis_wins_but_the_disagreement_is_reported(world):
    # 代碼推算 3F、GIS 說 2F：以 GIS 為準，但不能把差異吞掉
    world["plan"] = {**PLAN, "floor_by_room_code": "3F"}
    found = locate_classroom("4264")
    assert found["floor"] == "2F"
    assert "以 GIS 為準" in found["floor_conflict"]


def test_no_conflict_when_the_two_agree(world):
    assert locate_classroom("4264")["floor_conflict"] is None


def test_floor_falls_back_to_the_room_code_rule(world):
    # GIS 沒有樓層時用代碼推算，而且要講明這是推算
    world["room"] = {**world["room"],
                     "candidates": [{**CANDIDATE, "floor": ""}]}
    found = locate_classroom("4264")
    assert found["floor"] == "2F"
    assert found["floor_source"] == "room_code"
    assert "推算" in found["conclusion"]


def test_the_floor_plan_answers_when_the_gis_cannot(world):
    # 「格致廳小講堂」在 GIS 查不到，但平面圖對照表認得它
    world["room"] = {"status": "not_found", "candidates": [],
                     "exact_match_count": 0, "source": "http://gis"}
    world["plan"] = {**PLAN, "floor_by_room_code": None, "plan_floor": "B1",
                     "plan_building": "資訊大樓"}
    found = locate_classroom("格致廳小講堂")
    assert found["building_name"] == "資訊大樓"
    assert found["floor_source"] == "floor_plan"
    assert "地下 1 樓" in found["conclusion"]


def test_a_room_code_beats_the_text_resolver(world):
    # 有代碼就不該再去讀文字，代碼是確定的、名稱不是
    world["place"] = {"status": "ok", "source": "gemini_building",
                      "name": "別棟大樓", "build_id": "X999", "floor": "",
                      "lat": 1.0, "lon": 2.0}
    found = locate_classroom("4264")
    assert found["building_id"] == "B029"
    assert found["building_source"] == "gis_room"


def test_the_text_resolver_answers_when_there_is_no_room_code(world):
    # 「社科院大樓 階梯教室－心理」沒有代碼，roominfo 永遠查不到，
    # 以前畫面上只剩「查不到大樓」，現在要給出實際位置
    world["room"] = {"status": "not_found", "candidates": [],
                     "exact_match_count": 0, "source": "http://gis"}
    world["plan"] = {**PLAN, "status": "not_found", "plan": None,
                     "floor_by_room_code": None, "error_message": "沒有平面圖"}
    world["place"] = {"status": "ok", "source": "gemini_building",
                      "name": "E901 社會科學院大樓", "build_id": "E047",
                      "floor": "", "lat": 23.0019, "lon": 120.2166}
    found = locate_classroom("社科院大樓 階梯教室－心理")
    assert found["building_name"] == "E901 社會科學院大樓"
    assert found["building_source"] == "gemini_building"
    assert found["location"]["lat"] == 23.0019


def test_a_plan_from_another_building_is_not_shown(world):
    # 用文字換出來的大樓配別處來的樓層，可能拼出不含那間教室的圖
    world["room"] = {"status": "not_found", "candidates": [],
                     "exact_match_count": 0, "source": "http://gis"}
    world["place"] = {"status": "ok", "source": "gis_prefix", "name": "B003 資訊大樓",
                      "build_id": "B006", "floor": "", "lat": 22.99, "lon": 120.21}
    assert locate_classroom("資訊大樓格致廳小講堂")["floor_plan"]["source"] == "picture"


def test_lookup_error_is_propagated(world):
    world["room"] = {"status": "error", "candidates": [],
                     "error_message": "成大地理資訊系統查詢逾時"}
    found = locate_classroom("4264")
    assert found["status"] == "error"
    assert "逾時" in found["error_message"]


def test_empty_query_is_rejected(world):
    assert locate_classroom("  ")["status"] == "error"
