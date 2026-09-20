"""Tool 層：讀本機課表，算出「現在這堂」與「下一堂」課。

本檔只讀檔案、不打網路，也不呼叫其他 Tool：查大樓、組導航連結分別是
ncku_room 與 walking_link 的事，由上層決定要不要串起來。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from api import load_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 課表的 day 欄位是英文，datetime.weekday() 是 0=Monday
WEEKDAY_OF = {
    "Monday": 0, "Tuesday": 1, "Wednesday": 2,
    "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6,
}
ZH_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

# 成大教室代碼是 4 到 5 位數字（例如 4264、65304、27103）
ROOM_CODE_RE = re.compile(r"\d{4,5}")


class SchemaError(ValueError):
    """課表 JSON 缺少必要欄位或格式不符。"""


def extract_room_query(location: str) -> str:
    """從課表的地點字串抓出可以餵給 lookup_room 的查詢字。

    課表寫法混雜，例如 "資訊系館4264"（含教室代碼）、"雲平大樓27103（視聽教室）"
    （代碼後面還有註記）、"社科院大樓階梯教室－心理"（完全沒有代碼）。
    有數字代碼時優先用代碼查，最準；沒有就回空字串，交由上層改用原字串處理。
    """
    match = ROOM_CODE_RE.search(location or "")
    return match.group() if match else ""


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, _, minute = (value or "").partition(":")
    return int(hour), int(minute)


def _course_datetimes(course: dict, now: datetime) -> tuple[datetime, datetime]:
    """算出 course 在「以 now 當週為基準」的那一次上課起訖時間。"""
    weekday = WEEKDAY_OF[course["day"]]
    start_h, start_m = _parse_hhmm(course["start_time"])
    end_h, end_m = _parse_hhmm(course["end_time"])
    day = now.date() + timedelta(days=weekday - now.weekday())
    start = datetime.combine(day, time(start_h, start_m), now.tzinfo)
    end = datetime.combine(day, time(end_h, end_m), now.tzinfo)
    return start, end


def _as_entry(course: dict, start: datetime, end: datetime, now: datetime) -> dict:
    return {
        "temporary_change": course.get("temporary_change"),
        "name": course["name"],
        "day_zh": course.get("day_zh") or ZH_WEEKDAYS[WEEKDAY_OF[course["day"]]],
        "start_time": course["start_time"],
        "end_time": course["end_time"],
        "periods": course.get("periods", []),
        "location": course["location"],
        "room_query": extract_room_query(course["location"]),
        "starts_at": start.isoformat(timespec="seconds"),
        "ends_at": end.isoformat(timespec="seconds"),
        "minutes_until_start": round((start - now).total_seconds() / 60),
        "minutes_until_end": round((end - now).total_seconds() / 60),
    }


def find_classes(courses: list[dict], now: datetime) -> tuple[dict | None, dict | None]:
    """回傳 (正在上的課, 下一堂課)。純函式，時間由呼叫端給，方便測試。

    下一堂課會往後找滿七天，所以星期五晚上問會正確跨到下星期一。
    """
    current = None
    upcoming: list[tuple[datetime, dict]] = []

    for course in courses:
        if course.get("day") not in WEEKDAY_OF:
            raise SchemaError(f"課程 {course.get('name')!r} 的 day 欄位無法辨識：{course.get('day')!r}")
        # 本週與下週各算一次，才能跨過週末找到下星期一的課
        for week in (0, 1):
            start, end = _course_datetimes(course, now)
            start += timedelta(weeks=week)
            end += timedelta(weeks=week)
            from commute_agent.tools.schedule_changes import overlay
            effective = overlay(course, start.date())
            start = start.replace(hour=_parse_hhmm(effective['start_time'])[0], minute=_parse_hhmm(effective['start_time'])[1])
            end = end.replace(hour=_parse_hhmm(effective['end_time'])[0], minute=_parse_hhmm(effective['end_time'])[1])
            if start <= now < end and current is None:
                current = _as_entry(effective, start, end, now)
            if start > now:
                upcoming.append((start, _as_entry(effective, start, end, now)))
                break

    upcoming.sort(key=lambda pair: pair[0])
    return current, (upcoming[0][1] if upcoming else None)


def find_recent_class(courses: list[dict], now: datetime, within_minutes: int) -> dict | None:
    """回傳 within_minutes 分鐘內剛下課的那一堂，沒有就回 None。純函式。

    用來推測使用者「人大概還在上一堂的教室附近」：find_classes 只看得到
    正在上的課，下課那一刻起 current 就變成 None，這時要靠這支才知道
    剛才在哪。正在上課的不算（下課時間還沒到）。
    """
    latest: tuple[datetime, dict] | None = None

    for course in courses:
        if course.get("day") not in WEEKDAY_OF:
            raise SchemaError(f"課程 {course.get('name')!r} 的 day 欄位無法辨識：{course.get('day')!r}")
        # 週一凌晨剛過就要看到上週日的課，所以也往前算一週
        for week in (-1, 0):
            start, end = _course_datetimes(course, now)
            start += timedelta(weeks=week)
            end += timedelta(weeks=week)
            ended_minutes_ago = (now - end).total_seconds() / 60
            if 0 <= ended_minutes_ago <= within_minutes:
                if latest is None or end > latest[0]:
                    latest = (end, _as_entry(course, start, end, now))

    return latest[1] if latest else None


def load_courses(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    courses = payload.get("courses")
    if not isinstance(courses, list):
        raise SchemaError("課表 JSON 缺少 courses 陣列")
    for course in courses:
        missing = [k for k in ("name", "day", "start_time", "end_time", "location") if not course.get(k)]
        if missing:
            raise SchemaError(f"課程 {course.get('name', '?')!r} 缺少欄位：{missing}")
    from commute_agent.tools.schedule_changes import attach
    return attach(courses, path)


def get_next_class(schedule_path: str = "", now: datetime | None = None) -> dict:
    """查使用者的課表，回傳現在正在上的課與下一堂課。

    適用時機：使用者問「我下一堂課在哪」、「等一下要去哪上課」、
    「現在該出發了嗎」這類跟自己課表有關的問題時使用。

    Args:
        schedule_path: 要讀哪一份課表。留空表示用設定裡的預設課表。
            網頁會傳入使用者上傳辨識出來的那一份，否則出發時間會依範例
            課表計算，跟畫面上顯示的課不是同一堂。
        now: 用哪個時間當「現在」，留空是真實時間。網頁的模擬時間靠它，
            否則畫面顯示的是模擬時間的下一堂，這裡卻用真實時間去找。

    Returns:
        dict，包含：
        - status: "ok"（有找到下一堂課）、"not_found"（課表是空的）或 "error"
        - current_class: 現在正在上的課；沒有在上課時為 None
        - next_class: 下一堂課；兩者都含 name、day_zh、start_time、location、
          room_query（可直接餵給 lookup_room 的教室代碼，抓不到時為空字串）、
          minutes_until_start（距離上課還有幾分鐘）
        - now: 查詢當下的時間
        - error_message: 僅在 status 為 "error" 時出現
    """
    settings = load_settings()
    tz = settings.timezone
    now = now or datetime.now(ZoneInfo(tz))
    path = Path(schedule_path or settings.class_schedule_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    result = {
        "status": "ok",
        "current_class": None,
        "next_class": None,
        "now": now.isoformat(timespec="seconds"),
        "source": str(path),
    }

    if not path.is_file():
        return {**result, "status": "error", "error_message": f"找不到課表檔案：{path}"}

    try:
        courses = load_courses(path)
    except (SchemaError, ValueError) as exc:
        return {**result, "status": "error", "error_message": f"課表格式異常：{exc}"}

    current, upcoming = find_classes(courses, now)
    result["current_class"] = current
    result["next_class"] = upcoming
    if upcoming is None:
        result["status"] = "not_found"
    return result
