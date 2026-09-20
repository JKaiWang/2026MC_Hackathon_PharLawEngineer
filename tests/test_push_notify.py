"""push_notify Tool 與 departure_notify Skill 測試。不打 ntfy、不算真的課表。"""
import pytest
import requests

from api import Settings, load_settings
from commute_agent.skills import departure_notify
from commute_agent.tools import push_notify


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


def _settings(mode="live", topic="t0pic") -> Settings:
    base = load_settings(load_env_file=False)
    return Settings(**{**base.__dict__, "provider_mode": mode, "ntfy_topic": topic,
                       "ntfy_server": "https://ntfy.test"})


# ---------- send_push ----------

def test_fixture_mode_is_dry_run_and_never_posts(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not post in fixture mode")
    monkeypatch.setattr(push_notify.requests, "post", boom)
    out = push_notify.send_push("該出發了", "現在走", settings=_settings(mode="fixture"))
    assert out["status"] == "dry_run" and out["title"] == "該出發了"


def test_live_posts_json_to_server_root_with_topic(monkeypatch):
    seen = {}

    def fake_post(url, json=None, **k):
        seen["url"], seen["body"] = url, json
        return FakeResponse(200)
    monkeypatch.setattr(push_notify.requests, "post", fake_post)
    out = push_notify.send_push("該出發了", "現在走", priority="high", tags=["alarm_clock"],
                                click_url="https://maps.example", settings=_settings())
    assert out["status"] == "ok"
    assert seen["url"] == "https://ntfy.test"
    assert seen["body"]["topic"] == "t0pic" and seen["body"]["title"] == "該出發了"
    assert seen["body"]["priority"] == 4 and seen["body"]["tags"] == ["alarm_clock"]
    assert seen["body"]["click"] == "https://maps.example"


def test_missing_topic_is_error_not_exception(monkeypatch):
    out = push_notify.send_push("t", "m", settings=_settings(topic=""))
    assert out["status"] == "error" and "NTFY_TOPIC" in out["error_message"]


def test_http_failures_become_error(monkeypatch):
    monkeypatch.setattr(push_notify.requests, "post", lambda *a, **k: FakeResponse(500))
    assert push_notify.send_push("t", "m", settings=_settings())["status"] == "error"

    def timeout(*a, **k):
        raise requests.Timeout()
    monkeypatch.setattr(push_notify.requests, "post", timeout)
    assert "逾時" in push_notify.send_push("t", "m", settings=_settings())["error_message"]


def test_bad_priority_raises_and_empty_text_is_error():
    with pytest.raises(ValueError):
        push_notify.send_push("t", "m", priority="loud", settings=_settings(mode="fixture"))
    assert push_notify.send_push("", "m", settings=_settings(mode="fixture"))["status"] == "error"


# ---------- departure_notify ----------

def _plan(verdict, remaining, **extra):
    return {"status": "ok", "verdict": verdict, "leave_at": "2026-09-22T08:35:00+08:00",
            "minutes_until_departure": remaining, "travel_minutes": 12, "buffer_minutes": 8,
            "is_estimate": True, "weather_advice": None,
            "course": {"name": "數位IC設計", "day_zh": "星期一", "start_time": "09:00",
                       "location": "資訊系館4264", "building": "B501 資訊工程系館"}, **extra}


@pytest.fixture
def world(monkeypatch):
    state = {"plan": _plan("leave_now", 0), "sent": []}
    monkeypatch.setattr(departure_notify, "plan_departure", lambda **k: state["plan"])

    def fake_send(title, message, priority="default", tags=None, click_url=None):
        state["sent"].append({"title": title, "message": message, "priority": priority,
                              "tags": tags, "click_url": click_url})
        return {"status": "ok", "title": title, "message": message, "priority": priority}
    monkeypatch.setattr(departure_notify, "send_push", fake_send)
    return state


def test_leave_now_is_pushed_with_route_link(world):
    out = departure_notify.notify_departure(origin="圖書館", travel_mode="walking")
    assert out["status"] == "sent" and out["priority"] == "high"
    sent = world["sent"][0]
    assert "數位IC設計" in sent["message"] and "08:35" in sent["message"] and "步行約 12 分" in sent["message"]
    assert sent["click_url"] and "travelmode=walking" in sent["click_url"]
    assert out["dedupe_key"] == "數位IC設計|09:00|leave_now"


def test_plenty_is_skipped_unless_always(world):
    world["plan"] = _plan("plenty", 40)
    assert departure_notify.notify_departure()["status"] == "skipped"
    assert world["sent"] == []
    assert departure_notify.notify_departure(always=True)["status"] == "sent"


def test_too_late_is_urgent_and_says_so(world):
    world["plan"] = _plan("too_late", -7)
    out = departure_notify.notify_departure()
    assert out["priority"] == "urgent" and "已過 7 分鐘" in out["message"]


def test_weather_advice_is_appended(world):
    world["plan"] = _plan("leave_now", 2, weather_advice="出發時降雨機率 70%，可考慮改搭公車。")
    assert "降雨機率" in departure_notify.notify_departure()["message"]


def test_no_class_passes_through(world):
    world["plan"] = {"status": "no_class", "verdict": None}
    out = departure_notify.notify_departure()
    assert out["status"] == "no_class" and world["sent"] == []
