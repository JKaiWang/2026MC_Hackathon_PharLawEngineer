"""departure_plan Skill 測試：課表、路程與現在時間全部用假資料，不打網路。"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from commute_agent.skills import departure_plan
from commute_agent.skills.departure_plan import (
    ARRIVAL_BUFFER_MINUTES,
    HIGH_STAKES_EXTRA_MINUTES,
    buffer_minutes_for,
)

TZ = ZoneInfo("Asia/Taipei")
CLASS_START = "2026-09-21T09:00:00+08:00"


def a_class(name="數位IC設計"):
    return {"name": name, "day_zh": "星期一", "start_time": "09:00",
            "end_time": "12:00", "location": "資訊系館4264", "room_query": "4264",
            "starts_at": CLASS_START}


@pytest.fixture
def world(monkeypatch):
    """把課表、教室查詢、路程與時鐘全部換掉，讓測試只驗出發時刻的算法。"""
    state = {"now": "2026-09-21T08:00:00+08:00", "travel": 19, "course": a_class()}

    monkeypatch.setattr(departure_plan, "get_next_class",
                        lambda path="": {"status": "ok", "next_class": state["course"]})
    monkeypatch.setattr(departure_plan, "_resolve_building",
                        lambda entry: "B501 資訊工程系館")
    monkeypatch.setattr(departure_plan, "estimate_trip",
                        lambda o, d, m: {"minutes": state["travel"], "is_estimate": True,
                                         "note": ""})
    monkeypatch.setattr(departure_plan, "plan_bike_journey_auto",
                        lambda o, d: {"status": "ok", "total_minutes": state["travel"],
                                      "note": "", "ride": {"is_estimate": True},
                                      "from_station": {}, "to_station": {},
                                      "walk_to_station": {}, "walk_to_destination": {},
                                      "map_link": ""})
    monkeypatch.setattr(departure_plan, "load_settings",
                        lambda: type("S", (), {"default_origin": "成大圖書館",
                                               "timezone": "Asia/Taipei"})())
    state["weather"] = {"status": "ok", "weather": "晴", "rain_probability": 10,
                        "will_rain": False, "apparent_temperature": "30"}
    monkeypatch.setattr(departure_plan, "get_weather",
                        lambda **kwargs: state["weather"])

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(state["now"])

    monkeypatch.setattr(departure_plan, "datetime", FrozenClock)
    return state


def test_departure_is_class_start_minus_travel_and_buffer(world):
    r = departure_plan.plan_departure()
    # 09:00 − 19 分路程 − 8 分緩衝 = 08:33
    assert r["leave_at"].endswith("08:33:00+08:00")
    assert r["travel_minutes"] == 19
    assert r["buffer_minutes"] == ARRIVAL_BUFFER_MINUTES


def test_plenty_of_time_when_departure_is_far_off(world):
    assert departure_plan.plan_departure()["verdict"] == "plenty"


def test_leave_now_when_departure_is_within_five_minutes(world):
    world["now"] = "2026-09-21T08:30:00+08:00"   # 出發時刻 08:33，剩 3 分
    r = departure_plan.plan_departure()
    assert r["verdict"] == "leave_now"
    assert r["minutes_until_departure"] == 3


def test_too_late_when_departure_time_has_passed(world):
    world["now"] = "2026-09-21T08:50:00+08:00"
    r = departure_plan.plan_departure()
    assert r["verdict"] == "too_late"
    assert r["minutes_until_departure"] < 0


def test_exactly_at_departure_time_counts_as_leave_now(world):
    world["now"] = "2026-09-21T08:33:00+08:00"
    assert departure_plan.plan_departure()["verdict"] == "leave_now"


def test_longer_journey_means_earlier_departure(world):
    world["travel"] = 40
    assert departure_plan.plan_departure()["leave_at"].endswith("08:12:00+08:00")


def test_high_stakes_course_gets_a_bigger_buffer():
    # 遲到考試與遲到普通課的代價不同，緩衝也該不同
    assert buffer_minutes_for("數位IC設計") == ARRIVAL_BUFFER_MINUTES
    assert buffer_minutes_for("計算機結構期中考") == (
        ARRIVAL_BUFFER_MINUTES + HIGH_STAKES_EXTRA_MINUTES)
    assert buffer_minutes_for("分組報告") > ARRIVAL_BUFFER_MINUTES


def test_exam_shifts_departure_earlier(world):
    world["course"] = a_class("演算法期末考")
    # 多 10 分緩衝，出發時刻從 08:33 提前到 08:23
    assert departure_plan.plan_departure()["leave_at"].endswith("08:23:00+08:00")


def test_unknown_travel_time_gives_no_verdict(world, monkeypatch):
    monkeypatch.setattr(departure_plan, "estimate_trip",
                        lambda o, d, m: {"minutes": None, "is_estimate": True, "note": ""})
    r = departure_plan.plan_departure()
    assert r["verdict"] is None
    assert r["leave_at"] is None


def test_a_location_without_a_room_code_still_resolves_a_building(monkeypatch):
    # 「成大綜合體育館-體育館羽球場」這種沒有教室代碼的地點，以前會整串原文
    # 往下傳給 plan_parking／geocode_place，兩邊都認不得，driving 模式因此
    # 整堂課都算不出來（曾經還會讓 travel_for_mode 直接崩潰）。不經過 world
    # fixture，因為它會把 _resolve_building 本身換掉，測不到這裡要測的邏輯。
    monkeypatch.setattr(departure_plan, "lookup_room",
                        lambda q: {"status": "error", "candidates": []})
    monkeypatch.setattr(departure_plan, "locate_course_place",
                        lambda text, room="": {"status": "ok", "name": "B202 綜合二館"})
    entry = {"location": "成大綜合體育館-體育館羽球場", "room_query": ""}
    assert departure_plan._resolve_building(entry) == "B202 綜合二館"


def test_a_room_code_is_tried_before_the_raw_text_resolver(monkeypatch):
    # 有教室代碼就該以代碼為準：「資訊系館」當關鍵字在 GIS 會比到 B502，
    # 但 4264 其實在 B501，locate_course_place 不該有機會覆蓋掉代碼查到的答案。
    monkeypatch.setattr(departure_plan, "lookup_room",
                        lambda q: {"status": "ok", "candidates": [
                            {"exact_match": True, "building_name": "B501 資訊工程系館"}]})
    monkeypatch.setattr(departure_plan, "locate_course_place",
                        lambda text, room="": (_ for _ in ()).throw(
                            AssertionError("有代碼時不該呼叫 locate_course_place")))
    entry = {"location": "資訊系館4264", "room_query": "4264"}
    assert departure_plan._resolve_building(entry) == "B501 資訊工程系館"


def test_no_upcoming_class_is_reported(world, monkeypatch):
    monkeypatch.setattr(departure_plan, "get_next_class",
                        lambda path="": {"status": "ok", "next_class": None})
    assert departure_plan.plan_departure()["status"] == "no_class"


def test_schedule_error_is_surfaced(world, monkeypatch):
    monkeypatch.setattr(departure_plan, "get_next_class",
                        lambda path="": {"status": "error", "error_message": "找不到課表檔案"})
    r = departure_plan.plan_departure()
    assert r["status"] == "error"
    assert "課表" in r["error_message"]


def test_driving_uses_the_two_leg_journey_including_parking(world, monkeypatch):
    monkeypatch.setattr(departure_plan, "plan_ride_and_walk",
                        lambda o, d, v: {"status": "ok", "total_minutes": 8, "note": "",
                                         "ride": {"is_estimate": False},
                                         "lot": {"name": "三系館地下機車停車場"}})
    r = departure_plan.plan_departure(travel_mode="driving")
    assert r["travel_minutes"] == 8
    assert r["parking"]["name"] == "三系館地下機車停車場"
    assert r["is_estimate"] is False


def test_driving_reports_failure_instead_of_crashing(world, monkeypatch):
    # 找不到停車場時 plan_ride_and_walk 回 status="error" 且 ride 為 None
    # （不是空 dict）；journey.get("ride", {}) 的預設值在鍵存在時不會生效，
    # 直接對 None 呼叫 .get() 曾經讓整支 API 回 500，改用課表裡沒有教室代碼、
    # GIS 認不得原文的地點（例如某體育館的球場）最容易踩到這條路。
    monkeypatch.setattr(departure_plan, "plan_ride_and_walk",
                        lambda o, d, v: {"status": "error", "ride": None, "walk": None,
                                         "lot": None, "total_minutes": None,
                                         "error_message": "找不到剩餘車位足夠的機車停車場"})
    r = departure_plan.plan_departure(travel_mode="driving")
    assert r["status"] == "ok"
    assert r["travel_minutes"] is None
    assert r["verdict"] is None
    assert "找不到剩餘車位" in r["note"]


def test_rain_warning_when_mode_is_exposed(world):
    world["weather"] = {"status": "ok", "weather": "短暫陣雨", "rain_probability": 70,
                        "will_rain": True, "apparent_temperature": "27"}
    r = departure_plan.plan_departure(travel_mode="bicycling")
    assert r["weather_advice"] is not None
    assert "70" in r["weather_advice"]


def test_no_rain_warning_for_transit_even_when_raining(world):
    # 搭公車淋不到雨，不需要提醒改交通方式
    world["weather"] = {"status": "ok", "weather": "雷雨", "rain_probability": 90,
                        "will_rain": True, "apparent_temperature": "26"}
    assert departure_plan.plan_departure(travel_mode="transit")["weather_advice"] is None


def test_no_warning_below_rain_threshold(world):
    world["weather"] = {"status": "ok", "weather": "多雲", "rain_probability": 20,
                        "will_rain": False, "apparent_temperature": "29"}
    assert departure_plan.plan_departure()["weather_advice"] is None


def test_weather_failure_does_not_break_the_plan(world):
    # 天氣查不到只是少一個提醒，出發時刻照樣要算得出來
    world["weather"] = {"status": "error", "error_message": "未設定 CWA_API_KEY"}
    r = departure_plan.plan_departure()
    assert r["verdict"] == "plenty"
    assert r["weather"] is None
    assert r["weather_advice"] is None
