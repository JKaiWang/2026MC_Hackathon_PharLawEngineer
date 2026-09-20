from web import server


def _room_result(building_name="A006 唯農大樓", floor="2F"):
    return {
        "status": "ok",
        "candidates": [{
            "building_name": building_name,
            "floor": floor,
            "exact_match": True,
        }],
    }


def test_room_only_location_is_not_marked_as_corrected(monkeypatch):
    monkeypatch.setattr(server, "lookup_room", lambda query: _room_result())

    result = server._resolve_building({
        "location": "共同教室-A1302",
        "room_query": "A1302",
    })

    assert result["building_name"] == "A006 唯農大樓"
    assert result["floor"] == "2F"
    assert result["corrected"] is False


def test_explicit_wrong_building_is_marked_as_corrected(monkeypatch):
    monkeypatch.setattr(server, "lookup_room", lambda query: _room_result())

    result = server._resolve_building({
        "location": "B501 資訊工程系館 A1302",
        "room_query": "A1302",
    })

    assert result["corrected"] is True

"""web.server 端點測試。Google、Gemini、成大 GIS 一律換成假的。"""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from commute_agent import scenario
from web import server
"""web.server 測試：端點的參數與回應格式。Google、Gemini、成大 GIS 一律換成假的。"""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from commute_agent import scenario
from web import server

COURSES = [
    {"name": "數位IC設計", "day": "Monday", "day_zh": "星期一", "start_time": "09:00",
     "end_time": "12:00", "location": "資訊系館4264"},
    {"name": "日文（一）", "day": "Monday", "day_zh": "星期一", "start_time": "13:10",
     "end_time": "15:00", "location": "唯農大樓7208"},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps({"timezone": "Asia/Taipei", "courses": COURSES}),
                        encoding="utf-8")
    monkeypatch.setenv("USER_SCHEDULE_PATH", str(schedule))
    monkeypatch.setattr(server, "load_settings", lambda: type("S", (), {
        "timezone": "Asia/Taipei", "default_origin": "",
        "class_schedule_path": "data/class_schedule.json"})())
    return TestClient(server.app)


@pytest.fixture
def no_schedule(client, tmp_path, monkeypatch):
    monkeypatch.setenv("USER_SCHEDULE_PATH", str(tmp_path / "missing.json"))
    return client


# ---- 日曆匯出 ----

def test_calendar_export_is_a_downloadable_ics_file(client):
    r = client.get("/api/calendar.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.count("BEGIN:VEVENT") == len(COURSES)


def test_calendar_export_without_a_schedule_is_not_found(no_schedule):
    assert no_schedule.get("/api/calendar.ics").status_code == 404


def test_calendar_weeks_are_clamped_to_a_sane_range(client):
    assert "COUNT=30" in client.get("/api/calendar.ics", params={"weeks": 999}).text
    assert "RRULE" not in client.get("/api/calendar.ics", params={"weeks": 0}).text


# ---- 遲到通知信 ----

def test_late_notice_returns_a_draft_with_a_mailto_link(client):
    r = client.get("/api/late_notice", params={
        "course": "數位IC設計", "starts_at": "2026-09-21T09:00:00+08:00", "late": 8})
    body = r.json()
    assert r.status_code == 200
    assert "數位IC設計" in body["subject"] and "8 分鐘" in body["subject"]
    assert body["mailto"].startswith("mailto:")


def test_leave_notice_is_available_through_the_same_endpoint(client):
    r = client.get("/api/late_notice", params={
        "course": "數位IC設計", "starts_at": "2026-09-21T09:00:00+08:00", "kind": "leave"})
    body = r.json()
    assert r.status_code == 200
    assert "請假" in body["subject"] and "無法出席" in body["body"]
    assert body["needs_reason"] is True            # 原因由使用者自己填，不替他編


def test_notice_kind_must_be_late_or_leave(client):
    r = client.get("/api/late_notice", params={
        "course": "課", "starts_at": "2026-09-21T09:00:00+08:00", "kind": "cancel"})
    assert r.status_code == 400


# ---- 出席檢查：正在上課時，比較你的位置與教室 ----

@pytest.fixture
def in_class(client, monkeypatch):
    """讓 /api/state 不打任何網路：只留下「出席檢查拿到什麼參數」這件事可觀察。"""
    seen = {}
    monkeypatch.setattr(server, "load_settings", lambda: type("S", (), {
        "timezone": "Asia/Taipei", "default_origin": "我家的預設地址",
        "class_schedule_path": "data/class_schedule.json"})())
    monkeypatch.setattr(server, "_with_route", lambda entry, origin, mode: entry)
    monkeypatch.setattr(server, "find_transition", lambda *a, **k: None)
    monkeypatch.setattr(server, "plan_departure", lambda *a, **k: None)
    monkeypatch.setattr(server, "check_attendance",
                        lambda cur, now, **kw: seen.update(cur=cur["name"], **kw) or {"status": "ok"})
    seen["client"] = client
    return seen


def during_class(seen, **params):
    # 週一 10:00 模擬時間：課表裡 09:00–12:00 的數位IC設計正在上
    r = seen["client"].get("/api/state", params={"now": "2026-09-21T10:00", **params})
    assert r.status_code == 200
    return r.json()


def test_state_includes_the_attendance_check_while_a_class_is_in_session(in_class):
    body = during_class(in_class, origin="成大圖書館")
    assert body["attendance"] == {"status": "ok"}
    assert in_class["cur"] == "數位IC設計"


def test_attendance_uses_only_the_typed_place_never_the_default_origin(in_class):
    # 沒輸入位置時不能拿預設地址（住家）當「現在的位置」，否則會被誤判成缺席
    during_class(in_class)
    assert in_class["place_text"] == ""
    during_class(in_class, origin="成大圖書館")
    assert in_class["place_text"] == "成大圖書館"


def test_attendance_receives_the_browser_location_and_accuracy(in_class):
    during_class(in_class, lat=22.9968, lon=120.2168, accuracy=35)
    assert (in_class["lat"], in_class["lon"], in_class["accuracy_m"]) == (22.9968, 120.2168, 35)


def test_no_attendance_check_outside_class_time(in_class):
    body = in_class["client"].get("/api/state", params={"now": "2026-09-21T08:00"}).json()
    assert body["attendance"] is None


def test_late_notice_rejects_an_unparseable_time(client):
    r = client.get("/api/late_notice", params={"course": "課", "starts_at": "明天早上"})
    assert r.status_code == 400


# ---- 拍照找教室 ----

def test_scan_room_hands_the_photo_and_target_to_the_skill(client, monkeypatch):
    seen = {}

    def fake(raw, mime, target):
        seen.update(raw=raw, mime=mime, target=target)
        return {"status": "ok", "here": {"room_code": "4264"}}

    monkeypatch.setattr(server, "scan_room_sign", fake)
    r = client.post("/api/scan_room", params={"target": "4364"},
                    files={"file": ("sign.png", b"fake-bytes", "image/png")})
    assert r.status_code == 200 and r.json()["here"]["room_code"] == "4264"
    assert seen == {"raw": b"fake-bytes", "mime": "image/png", "target": "4364"}


def test_scan_room_error_is_a_400(client, monkeypatch):
    monkeypatch.setattr(server, "scan_room_sign",
                        lambda *a: {"status": "error", "error_message": "不支援的圖片格式"})
    r = client.post("/api/scan_room", files={"file": ("x.pdf", b"x", "application/pdf")})
    assert r.status_code == 400


# ---- AI 建議：偏好、模擬時間與情境 ----

def test_recommend_needs_an_origin(client):
    assert client.get("/api/recommend").status_code == 400


def test_recommend_passes_preference_simulated_time_and_scenarios(client, monkeypatch):
    seen = {}

    def fake(origin, vehicle, path, preference="", now=None):
        seen.update(origin=origin, preference=preference, now=now,
                    active=scenario.active())
        return {"status": "ok"}

    monkeypatch.setattr(server, "recommend_plan", fake)
    r = client.get("/api/recommend", params={
        "origin": "成大圖書館", "preference": "我怕熱", "now": "2026-09-21T08:00",
        "scenario": "heavy_rain,bogus"})
    assert r.status_code == 200
    assert seen["preference"] == "我怕熱"
    assert isinstance(seen["now"], datetime) and seen["now"].hour == 8
    assert seen["active"] == {"heavy_rain"}       # 認不得的情境被丟掉


def test_recommend_uses_real_time_when_the_clock_is_not_simulated(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "recommend_plan", lambda o, v, p, preference="", now=None:
                        seen.update(now=now) or {"status": "ok"})
    client.get("/api/recommend", params={"origin": "成大圖書館"})
    assert seen["now"] is None


def test_recommend_rejects_an_unparseable_time(client):
    r = client.get("/api/recommend", params={"origin": "成大圖書館", "now": "週一早上"})
    assert r.status_code == 400


def test_a_scenario_does_not_outlive_its_request(client, monkeypatch):
    monkeypatch.setattr(server, "recommend_plan", lambda *a, **k: {"status": "ok"})
    client.get("/api/recommend", params={"origin": "成大圖書館", "scenario": "heavy_rain"})
    assert scenario.active() == frozenset()


# ---- 重新規畫 ----

def test_replan_passes_the_previous_round_and_accepted_risks(client, monkeypatch):
    seen = {}

    def fake(origin, vehicle, path, previous_mode=None, now=None, simulated=False,
             known_soft=()):
        seen.update(origin=origin, previous_mode=previous_mode, simulated=simulated,
                    known_soft=list(known_soft), active=scenario.active())
        return {"status": "ok", "action": "keep"}

    monkeypatch.setattr(server, "replan", fake)
    r = client.get("/api/replan", params={
        "mode": "bicycling", "origin": "成大圖書館", "now": "2026-09-21T08:00",
        "scenario": "bike_zero", "known_soft": "rain,parking_low"})
    assert r.status_code == 200
    assert seen == {"origin": "成大圖書館", "previous_mode": "bicycling", "simulated": True,
                    "known_soft": ["rain", "parking_low"], "active": {"bike_zero"}}


def test_replan_without_a_previous_mode_is_a_first_recommendation(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "replan", lambda *a, **k: seen.update(k) or {"status": "ok"})
    client.get("/api/replan", params={"origin": "成大圖書館"})
    assert seen["previous_mode"] is None and seen["simulated"] is False


def test_replan_rejects_an_unknown_mode(client):
    r = client.get("/api/replan", params={"mode": "teleport", "origin": "成大圖書館"})
    assert r.status_code == 400


def test_replan_needs_an_origin(client):
    assert client.get("/api/replan", params={"mode": "walking"}).status_code == 400


def test_replan_needs_a_schedule(no_schedule):
    r = no_schedule.get("/api/replan", params={"mode": "walking", "origin": "成大圖書館"})
    assert r.status_code == 400


def test_replan_failure_is_reported_with_a_400(client, monkeypatch):
    monkeypatch.setattr(server, "replan", lambda *a, **k: {"status": "no_class"})
    r = client.get("/api/replan", params={"mode": "walking", "origin": "成大圖書館"})
    assert r.status_code == 400


# ---- 讀錯門牌時：使用者自己輸入或點選確認 ----

def test_here_endpoint_passes_the_code_and_target(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "confirm_room", lambda code, target: seen.update(
        code=code, target=target) or {"status": "ok", "here": {"room_code": code}})
    r = client.get("/api/here", params={"code": "A1302", "target": "A1306"})
    assert r.status_code == 200 and r.json()["here"]["room_code"] == "A1302"
    assert seen == {"code": "A1302", "target": "A1306"}


def test_here_endpoint_reports_an_unusable_code_as_400(client, monkeypatch):
    monkeypatch.setattr(server, "confirm_room",
                        lambda code, target: {"status": "error", "error_message": "請輸入教室代碼"})
    assert client.get("/api/here", params={"code": " "}).status_code == 400


def test_here_endpoint_keeps_not_recognized_as_a_normal_answer(client, monkeypatch):
    # 查不到但有相近的建議，是正常的回答（畫面要顯示建議），不是錯誤
    monkeypatch.setattr(server, "confirm_room", lambda code, target: {
        "status": "not_recognized", "suggestions": [{"room_code": "A1302"}]})
    r = client.get("/api/here", params={"code": "A1301"})
    assert r.status_code == 200 and r.json()["suggestions"][0]["room_code"] == "A1302"
